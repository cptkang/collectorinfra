"""plans/119 · D-267 ⑦ — 벤치가 러너 행의 첫 답변 칸(`ttft_ms`·`timeline`)을 받아 요약한다.

- 운영 상한 초과 계수는 상한 의미가 「첫 답변까지」인 판이면 **첫 답변 시각**으로 센다
  (D-267 주의 ②).
- 상한 의미를 보고하지 않은 판(종전)은 전체 소요로 세고 그렇게 적는다.
- TTFT 는 arm 별 p50·p90 한 줄로 싣는다. 칸이 없는 옛 run 은 줄을 싣지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.bench import sweep


def _raw(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    raw = tmp_path / "raw.jsonl"
    raw.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return raw


def _first_answer_timeline(first_answer_ms: float | None) -> dict[str, Any]:
    return {"cap_semantic": "first_answer", "limit_sec": 60.0, "first_answer_ms": first_answer_ms,
            "end_ms": 100_000.0, "timeout_stage": None, "timeout_kind": None}


def test_첫_답변_상한_판은_첫_답변_시각으로_센다(tmp_path) -> None:
    rows = [
        # 20초에 답이 나가기 시작해 100초에 끝났다 — 첫 답변 상한에 걸리지 않는다.
        {"profile": "baseline", "scenario_id": "S0", "wall_ms": 100_000, "ttft_ms": 20_000,
         "timeline": _first_answer_timeline(19_500)},
        # 서버 첫 답변 70초 — 러너 칸보다 서버(상한을 거는 쪽) 시계가 우선이다.
        {"profile": "baseline", "scenario_id": "S1", "wall_ms": 90_000, "ttft_ms": 50_000,
         "timeline": _first_answer_timeline(70_000)},
        # 토큰 없이 끝난 턴(타임아웃 오류) — 응답 도착이 곧 첫 답변이다.
        {"profile": "baseline", "scenario_id": "S2", "wall_ms": 180_500, "ttft_ms": None,
         "timeline": _first_answer_timeline(None)},
    ]
    health = sweep.scan_health({"profiles": []}, _raw(tmp_path, rows))

    assert health.cap_semantic == "first_answer"
    assert health.over_user_timeout == {"baseline": 2}
    line = health.user_timeout_line()
    assert "운영 처리 상한(첫 답변까지) 60초 초과 턴" in line and "`baseline` 2턴" in line
    assert "측정 처리 상한 180초(첫 답변까지)" in line and "D-267 ⑦" in line


def test_상한_의미를_모르는_판은_전체_소요로_세고_그렇게_적는다(tmp_path) -> None:
    rows = [{"profile": "baseline", "scenario_id": "S0", "wall_ms": 100_000, "ttft_ms": 20_000}]
    health = sweep.scan_health({"profiles": []}, _raw(tmp_path, rows))
    assert health.cap_semantic == "total" and health.over_user_timeout == {"baseline": 1}
    assert "운영 상한(요청 전체) 60초 초과" in health.user_timeout_line()


def test_상한_의미는_러너_메타가_1순위다(tmp_path) -> None:
    rows = [{"profile": "baseline", "scenario_id": "S0", "wall_ms": 1_000,
             "timeline": {"parse_end_ms": 1.0}}]            # 키 없음 = 종전 의미
    raw = _raw(tmp_path, rows)
    assert sweep.run_cap_semantic({}, sweep.read_raw_rows(raw)) == "total"
    assert sweep.run_cap_semantic({"meta": {"cap_semantic": "first_answer"}},
                                  sweep.read_raw_rows(raw)) == "first_answer"
    assert sweep.run_cap_semantic({}, []) == "total"


def test_TTFT를_arm마다_요약한다(tmp_path) -> None:
    rows = ([{"profile": "baseline", "scenario_id": f"S{i}", "wall_ms": 1_000,
              "ttft_ms": 1_000.0 * i} for i in range(1, 11)]
            + [{"profile": "baseline", "scenario_id": "S99", "wall_ms": 1_000, "ttft_ms": None},
               {"profile": "baseline", "scenario_id": "S98", "wall_ms": 1_000, "ttft_ms": 99_000,
                "func_verdict": "invalid"},                   # 무효 턴은 표본이 아니다
               {"profile": "X-1", "scenario_id": "S0", "wall_ms": 1_000, "ttft_ms": 2_000},
               {"profile": "X-2", "scenario_id": "S0", "wall_ms": 1_000, "ttft_ms": None}])
    health = sweep.scan_health({"profiles": []}, _raw(tmp_path, rows))

    assert health.ttft_by_arm["baseline"] == (10, 5500.0, 9000.0)
    assert health.ttft_by_arm["X-1"] == (1, 2000.0, None)
    line = health.ttft_line()
    assert "`baseline` p50 5.5초 · p90 9.0초(n=10)" in line
    assert "`X-1` p50 2.0초 · p90 표본 부족(n=1)" in line and "`X-2` 표본 없음" in line


def test_TTFT_칸이_없는_옛_run은_줄을_싣지_않는다(tmp_path) -> None:
    rows = [{"profile": "baseline", "scenario_id": "S0", "wall_ms": 1_000}]
    health = sweep.scan_health({"profiles": []}, _raw(tmp_path, rows))
    assert health.ttft_by_arm == {} and health.ttft_line() is None
