"""부분 결과 턴(`status: partial`)의 판정 처리 — plans/114 P-2 (게이트 G-E · D-241 안의 적용).

제품이 시간 상한에서 **서술 없이 표만** 돌려주는 경로가 생겼다(`src/domain/partial_result.py`).
그 턴은 오류 문구가 없어 종전 타임아웃 검사에 걸리지 않는다. 그대로 두면 둘이 깨진다.

1. 카탈로그가 `status: completed` 를 기대하는 턴이 전부 **기능 불합격**이 된다.
2. 제품이 빨라진 것도 아닌데 **타임아웃률만 내려간다**(M-2 ② 관문 은폐).

그래서 `partial` 을 `timeout` 과 **같은 제거 사유**로 본다. 사유 어휘는 늘리지 않는다.
"""

from __future__ import annotations

from typing import Any

from scripts.bench import sweep as sweep_mod
from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import Verdict
from scripts.scenario.catalog import Scenario, Turn
from scripts.scenario.client import Observation
from scripts.scenario.report import unevaluated_reason, unevaluated_summary
from src.domain.partial_result import PARTIAL_STATUS


def _row(**kw: Any) -> dict[str, Any]:
    base = {
        "group": "A", "scenario_id": "A-01", "turn": 1, "repeat": 0,
        "func_verdict": "fail", "response_mode": "answer", "error": None,
        "forbidden_mode": None, "failed_assertions": [],
    }
    base.update(kw)
    return base


# ── 러너 행에 상태가 실린다 (a) ────────────────────────────────────────────


def test_러너_행이_응답_상태를_싣는다() -> None:
    """칸이 없으면 판정 쪽이 `partial` 을 알 길이 없다 — 러너 칸이 정본이다."""
    scenario = Scenario(id="A-01", group="A", plans=[114], title="t",
                        turns=[Turn(send={"query": "q"}, expect={})])
    obs = Observation()
    obs.status = PARTIAL_STATUS

    row = runner_mod._row({"run_id": "r", "env": "closed", "mode": "run"},
                          "baseline", scenario, 1, 0, obs, Verdict())

    assert row["status"] == PARTIAL_STATUS


# ── 리포트 규칙 (b) ────────────────────────────────────────────────────────


def test_partial은_타임아웃과_같은_사유로_분모에서_빠진다() -> None:
    assert unevaluated_reason(_row(status=PARTIAL_STATUS)) == "timeout"


def test_completed는_종전대로_평가_대상이다() -> None:
    assert unevaluated_reason(_row(status="completed", func_verdict="pass")) is None


def test_무효가_partial보다_먼저다() -> None:
    """한 턴이 두 사유로 세어지면 「제거 합 + 분모 == 전체」가 깨진다."""
    row = _row(status=PARTIAL_STATUS, func_verdict="error", error="http 401")

    assert unevaluated_reason(row) == "invalid"


def test_사유_어휘는_늘어나지_않는다() -> None:
    summary = unevaluated_summary([_row(status=PARTIAL_STATUS), _row(status="completed")])

    assert "partial" not in summary["by_reason"]
    assert summary["by_reason"]["timeout"] == 1


# ── 벤치 폴백 규칙 (c) ────────────────────────────────────────────────────


def test_벤치도_같은_규칙으로_읽는다() -> None:
    assert sweep_mod.unevaluated_reason_of(_row(status=PARTIAL_STATUS)) == "timeout"


def test_러너_칸이_있으면_그_값이_정본이다() -> None:
    """새 run 은 러너가 사유를 적어 온다 — 벤치가 다시 판정해 어긋나게 만들지 않는다."""
    row = _row(status=PARTIAL_STATUS, unevaluated_reason="invalid")

    assert sweep_mod.unevaluated_reason_of(row) == "invalid"


def test_타임아웃률에_합산돼_관문이_약해지지_않는다() -> None:
    """부분 결과로 바뀌었다고 타임아웃률이 내려가면 M-2 ② 관문이 은폐된다."""
    rows = [_row(status=PARTIAL_STATUS) for _ in range(3)]
    rows += [_row(status="completed", func_verdict="pass") for _ in range(7)]

    counts = sweep_mod.unevaluated_counts(rows)

    assert counts["timeout"] == 3
