"""plans/114 M-7 ② — 2단 프로파일이 섞인 run 은 알람 시나리오에 「해석 제외 권고」를 단다.

111 G-5 확정: *"2단을 재측정 arm 으로 쓰면 측정 왜곡(알람 질의 전건 오분류)을 리포트에 고지"*.
run `20260922-112010` 리포트에는 그 고지가 없었다(`report.py` grep 0건).

알람 시나리오 판별(결정적): 군 `D` 이거나 어느 턴 질의문에 `has_alarm_signal` 이 참.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.scenario import utf8_open
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn
from scripts.scenario.report import TIER2_ALARM_NOTE, build_summary, render_markdown


def _row(scenario_id: str, group: str, **over) -> dict:
    base = {"run_id": "r", "profile": "baseline", "env": "closed", "mode": "run", "repeat": 0,
            "group": group, "scenario_id": scenario_id, "turn": 1, "plans": [114],
            "kind": "normal", "pair_id": None, "func_verdict": "fail", "perf_verdict": "pass",
            "response_mode": "answer", "forbidden_mode": None,
            "failed_assertions": [{"key": "status", "expected": "completed", "actual": "error"}],
            "manual_notes": [], "wall_ms": 100.0, "processing_time_ms": 100.0,
            "node_elapsed_ms": {}, "node_path": [], "sse_events": [], "executed_sql": "SELECT 1",
            "row_count": 1, "artifacts": [], "error": None}
    base.update(over)
    return base


def _run(tmp_path: Path, tier: str) -> Path:
    run_dir = tmp_path / "20260922-000000"
    run_dir.mkdir(parents=True)
    rows = [_row("D-03", "D"), _row("A-04", "A"), _row("A-01", "A")]
    with utf8_open(run_dir / "raw.jsonl", "w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    payload = {"meta": {"run_id": run_dir.name, "env": "closed", "mode": "run", "repeat": 1},
               "profiles": [{"name": "baseline", "valid": True, "tier": tier, "reasons": []}],
               "skipped": []}
    with utf8_open(run_dir / "run.json", "w") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return run_dir


def _catalog() -> Catalog:
    def scenario(sid: str, group: str, query: str) -> Scenario:
        return Scenario(id=sid, group=group, plans=[114], title=query,
                        turns=[Turn(send={"query": query}, expect={})])

    return Catalog(
        groups={"A": Group(id="A", name="a", latency_target_ms=5000),
                "D": Group(id="D", name="d", latency_target_ms=10000)},
        scenarios=[scenario("D-03", "D", "2026년 7월에 발생한 심각 알람이 몇 건인지 알려줘"),
                   scenario("A-04", "A", "현재 활성 상태인 심각 알람 목록 보여줘"),
                   scenario("A-01", "A", "여의도 개발 서버들의 CPU 사용률을 보여줘")])


def test_2단이면_D군과_알람_질의_시나리오에_해석_제외를_단다(tmp_path) -> None:
    run_dir = _run(tmp_path, "intent_orchestration")
    catalog = _catalog()
    summary = build_summary(run_dir, catalog)

    caveat = summary["tier2_alarm_caveat"]
    assert caveat["scenario_ids"] == ["A-04", "D-03"], "A-01 은 알람 질의가 아니다"
    assert "has_alarm_signal" in caveat["criterion"]

    markdown = render_markdown(summary, run_dir, catalog)
    head = markdown.split("## 1.")[0]
    assert f"[{TIER2_ALARM_NOTE}]" in head and "`A-04` · `D-03`" in head
    assert f"※ {TIER2_ALARM_NOTE}: A-04 · D-03" in markdown
    section6 = markdown.split("## 6.")[1].split("## 7.")[0]
    blocks = {block.split(" ", 1)[0]: block for block in section6.split("### ")[1:]}
    assert TIER2_ALARM_NOTE in blocks["D-03"] and TIER2_ALARM_NOTE in blocks["A-04"]
    assert TIER2_ALARM_NOTE not in blocks["A-01"]


def test_카탈로그를_받으면_성능_목표_칸이_찬다(tmp_path) -> None:
    """M-7 ① — 벤치가 카탈로그 없이 부르면 3절 `목표(ms)` 가 전 칸 공란이었다."""
    summary = build_summary(_run(tmp_path, "semantic_router"), _catalog())
    assert summary["groups"]["A"]["target_ms"] == 5000
    assert summary["groups"]["D"]["target_ms"] == 10000


def test_카탈로그가_없으면_D군만_고르고_그_사실을_적는다(tmp_path) -> None:
    summary = build_summary(_run(tmp_path, "intent_orchestration"), None)
    caveat = summary["tier2_alarm_caveat"]
    assert caveat["scenario_ids"] == ["D-03"]
    assert "카탈로그 없이" in caveat["criterion"]


def test_기준_단_run_에는_고지가_없다(tmp_path) -> None:
    run_dir = _run(tmp_path, "semantic_router")
    summary = build_summary(run_dir, _catalog())
    assert summary["tier2_alarm_caveat"] is None
    assert TIER2_ALARM_NOTE not in render_markdown(summary, run_dir, _catalog())
