"""응답 서술 비용·재계획 발동의 대리 지표 (plans/110 · run 20260922-093837 분석).

`llm_calls`·`tokens` 가 제품에서 오지 않는 동안(O-b) 「한 번 호출이 느린 것」과
「여러 번 부르는 것」을 가르는 대리 지표를 `bottleneck.md` 에 싣는다.
- 서술 노드(2단 `result_aggregator` · 3단 `output_generator`) 지연 대 응답 길이 상관
- 재계획이 실제로 후속 실행을 만든 턴(`agent_orchestrator` 2회 이상)과 그 턴의 타임아웃
"""

from __future__ import annotations

from pathlib import Path

from scripts.scenario.analyze import (
    NARRATION_MIN_ELAPSED_MS,
    bottleneck,
    narration_cost,
    replan_followup,
)


def _row(**kwargs) -> dict:
    base = {
        "scenario_id": "T-01", "turn": 1, "func_verdict": "pass", "error": None,
        "node_elapsed_ms": {}, "node_calls": {}, "response_text": "",
        "unevaluated_reason": None,
    }
    base.update(kwargs)
    return base


def _narr(node: str, length: int, ms_per_char: float = 20.0) -> dict:
    return _row(
        node_elapsed_ms={node: length * ms_per_char},
        response_text="가" * length,
    )


def test_서술_지연이_응답_길이에_비례하면_상관이_1에_가깝다() -> None:
    rows = [_narr("result_aggregator", n) for n in (200, 600, 1200, 2000, 3500, 800)]
    got = narration_cost(rows)
    assert got["measured"] is True
    assert got["samples"] == 6
    assert got["pearson_r"] > 0.99
    assert abs(got["ms_per_char_p50"] - 20.0) < 0.01


def test_3단_output_generator_도_서술_노드로_센다() -> None:
    rows = [_narr("output_generator", n) for n in (300, 700, 1500, 2500, 4000)]
    got = narration_cost(rows)
    assert got["measured"] is True
    assert got["samples"] == 5
    assert set(got["nodes"]) == {"output_generator"}


def test_LLM_미호출로_보이는_짧은_노드_시간과_응답_없는_턴은_뺀다() -> None:
    rows = [_narr("result_aggregator", n) for n in (300, 700, 1500, 2500, 4000)]
    rows.append(_row(node_elapsed_ms={"result_aggregator": NARRATION_MIN_ELAPSED_MS - 1},
                     response_text="존을 선택하세요"))
    rows.append(_row(node_elapsed_ms={"result_aggregator": 50000.0}, response_text=None,
                     unevaluated_reason="timeout"))
    assert narration_cost(rows)["samples"] == 5


def test_표본이_부족하면_상관을_만들지_않는다(tmp_path: Path) -> None:
    rows = [_narr("result_aggregator", n) for n in (300, 700)]
    got = narration_cost(rows)
    assert got["measured"] is False
    assert got["pearson_r"] is None
    body = bottleneck(tmp_path, rows)
    assert "## 응답 서술 비용 (대리 지표)" in body
    assert "판정 불가" in body.split("## 응답 서술 비용 (대리 지표)")[1]


def test_길이가_모두_같으면_상관은_판정_불가다() -> None:
    rows = [_narr("result_aggregator", 1000) for _ in range(6)]
    got = narration_cost(rows)
    assert got["measured"] is True
    assert got["pearson_r"] is None


def test_재계획_후속_실행_턴과_그_타임아웃을_센다() -> None:
    rows = [
        _row(node_calls={"replanner": 1, "agent_orchestrator": 1}),
        _row(node_calls={"replanner": 1, "agent_orchestrator": 1}),
        _row(node_calls={"replanner": 2, "agent_orchestrator": 2},
             unevaluated_reason="timeout", func_verdict="error"),
        _row(node_calls={"replanner": 2, "agent_orchestrator": 2}, func_verdict="pass"),
        _row(node_calls={"output_generator": 1}),     # 3단 — 재계획 노드 없음
    ]
    got = replan_followup(rows)
    assert got == {
        "replanner_turns": 4, "followup_turns": 2, "followup_timeout": 1,
        "followup_pass": 1,
    }


def test_bottleneck_에_두_절이_실리고_기존_절은_그대로다(tmp_path: Path) -> None:
    rows = [_narr("result_aggregator", n) for n in (300, 700, 1500, 2500, 4000)]
    for r in rows:
        r["node_calls"] = {"replanner": 1, "agent_orchestrator": 1}
    body = bottleneck(tmp_path, rows)
    assert "## 응답 서술 비용 (대리 지표)" in body
    assert "## 재계획 후속 실행" in body
    assert "## 호출 수와 지연의 분리" in body
    assert body.index("## 호출 수와 지연의 분리") < body.index("## 응답 서술 비용 (대리 지표)")
