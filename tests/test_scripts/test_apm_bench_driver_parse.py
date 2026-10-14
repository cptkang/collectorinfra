"""APM 벤치 드라이버 — 턴별 가짜 입력 파싱 답(`parse_for_turn` · plans/157 W1 · G-5).

소스 되묻기 답 턴은 라우트가 원 질문을 다시 파싱하므로 파싱 답도 원 질문 기준이어야 한다(결함 ③).
드라이버 기동 없이 순수 함수만 본다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.apm_bench import driver as dv
from scripts.apm_bench.fake_llm import parse_from_turn
from scripts.apm_bench.scenarios import (
    Expect,
    PlaceholderError,
    Scenario,
    Turn,
    fill_placeholders,
    load_scenarios,
)

PLACEHOLDERS_E1 = Path(__file__).resolve().parents[2] / "testdata/apm_bench/placeholders.e1.yaml"
VALUES = {"inst_c": "was01_b", "host_1": "was-host01", "host_2": "was-host02"}


def _expect(window: str, clarify: str | None = None) -> Expect:
    return Expect(agents=("apm_query",), views=("apm.app_health",), target="inst:c",
                  window=window, clarify=clarify)


def _scenario(*turns: Turn) -> Scenario:
    return Scenario("S", ("COV-X",), "dev", tuple(turns))


def _by_id(sid: str) -> Scenario:
    return next(s for s in load_scenarios() if s.id == sid)


def test_source_answer_turn_keeps_original_time_slot() -> None:
    """A-1: `APM-SRC-ambiguous` 답 턴(「공동존 운영」) — 원 질문 「…응답시간 30분」의 기간 슬롯."""
    scenario = _by_id("APM-SRC-ambiguous")
    assert scenario.turns[0].expect.clarify == "source"
    assert scenario.turns[1].reply_to == "source"
    parsed = dv.parse_for_turn(scenario, 1, VALUES)
    slot = parsed["time_expr"]
    assert slot is not None
    assert (slot["relation"], slot["n"], slot["unit"], slot["span"]) == ("last", 30, "minute",
                                                                         "30분")
    # 종전(답 문장 기준)은 기간 표현이 없어 슬롯이 비었다 — 결함 ③ 재현
    answer = scenario.turns[1]
    old = parse_from_turn(answer.expect, answer.prompt, fill_placeholders(answer.prompt, VALUES),
                          mapping=VALUES)
    assert old["time_expr"] is None


def test_answer_turn_hosts_from_original_template() -> None:
    """호스트 자리표 필터도 원 질문 템플릿 기준 — 답 문장의 자리표는 쓰지 않는다."""
    scenario = _scenario(
        Turn("{{host_1}} 응답시간 10분", _expect("range:10", clarify="source")),
        Turn("공동존 {{host_2}}", _expect("range:10"), reply_to="source"),
    )
    parsed = dv.parse_for_turn(scenario, 1, VALUES)
    assert parsed["filter_conditions"] == [{"field": "hostname", "op": "=",
                                            "value": "was-host01"}]
    assert parsed["time_expr"]["n"] == 10


def test_answer_turn_window_comes_from_answer_expect() -> None:
    """기대 창은 답 턴의 `expect.window` — 답 턴 창이 `range:`가 아니면 원 질문 기간이 있어도 null.
    """
    scenario = _scenario(
        Turn("{{inst_c}} 응답시간 30분", _expect("range:30", clarify="source")),
        Turn("공동존 운영", _expect("default"), reply_to="source"),
    )
    assert dv.parse_for_turn(scenario, 1, VALUES)["time_expr"] is None


def test_first_turn_with_source_clarify_uses_own_prompt() -> None:
    """직전 턴 없이 `clarify: source`인 첫 턴 — 자기 문장 기준(종전과 같다)."""
    scenario = _by_id("APM-SRC-ambiguous")
    turn = scenario.turns[0]
    expected = parse_from_turn(turn.expect, turn.prompt, fill_placeholders(turn.prompt, VALUES),
                               mapping=VALUES)
    assert dv.parse_for_turn(scenario, 0, VALUES) == expected
    assert expected["time_expr"]["n"] == 30


def test_turn_after_other_clarify_is_unchanged() -> None:
    """직전 턴 되묻기가 소스가 아니면(`required_arg`) 이번 턴 문장 기준(종전과 같다)."""
    scenario = _scenario(
        Turn("{{inst_c}} 응답시간 30분", _expect("range:30", clarify="required_arg")),
        Turn("5분", _expect("range:5"), reply_to="required_arg"),
    )
    assert dv.parse_for_turn(scenario, 1, VALUES)["time_expr"]["n"] == 5


def test_all_other_turns_bit_identical() -> None:
    """A-2: 소스 되묻기 답 턴이 아닌 전 시나리오 턴 — 종전 파싱 답과 비트 동일(자동 답 턴 포함)."""
    values = dv.load_placeholders(PLACEHOLDERS_E1).values
    checked = 0
    answer_turns = []
    for scenario in load_scenarios():
        for idx, turn in enumerate(scenario.turns):
            if turn.reply_to == "source":
                answer_turns.append((scenario.id, idx))
                continue
            try:
                prompt = fill_placeholders(turn.prompt, values)
            except PlaceholderError:
                continue
            expected = parse_from_turn(turn.expect, turn.prompt, prompt, mapping=values)
            assert dv.parse_for_turn(scenario, idx, values) == expected, (scenario.id, idx)
            checked += 1
    assert checked > 100
    # 답 턴 전수(2026-10-14) — 늘면 이 테스트로 함께 본다
    assert answer_turns == [("APM-SRC-ambiguous", 1), ("APM-SRC-none-all", 1)]


def test_missing_placeholder_in_original_question_raises() -> None:
    """원 질문 자리표가 없으면 `PlaceholderError` — 드라이버가 턴을 「자리표 미등록」으로 남긴다.
    """
    scenario = _scenario(
        Turn("{{inst_c}} 응답시간 30분", _expect("range:30", clarify="source")),
        Turn("공동존 운영", _expect("range:30"), reply_to="source"),
    )
    with pytest.raises(PlaceholderError):
        dv.parse_for_turn(scenario, 1, {})
