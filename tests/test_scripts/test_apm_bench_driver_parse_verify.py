"""plans/157 W1 독립 검증 — `parse_for_turn`의 답 턴 판정 경계(verifier).

최초 구현은 「직전 턴 `expect.clarify == "source"`」만 보고 원 질문을 골랐다. 시나리오 스키마에는
답 턴 표지 `reply_to`가 따로 있고(`check_structure`는 `reply_to` → 앞 턴 clarify만 검사 — 역방향은
검사하지 않는다), 라우트는 되묻기에 답이 아닌 새 질문이면 문장을 바꾸지 않는다. 교정 라운드에서
`reply_to` 사슬로 판정하도록 고쳤다 — 아래 두 형태(현재 시나리오 2026-10-14에는 없음)를 고정한다.
"""

from __future__ import annotations

from scripts.apm_bench import driver as dv
from scripts.apm_bench.fake_llm import parse_from_turn
from scripts.apm_bench.scenarios import (
    Expect,
    Scenario,
    Turn,
    check_structure,
    fill_placeholders,
    load_scenarios,
)

VALUES = {"inst_c": "was01_b"}


def _expect(window: str, clarify: str | None = None) -> Expect:
    return Expect(agents=("apm_query",), views=("apm.app_health",), target="inst:c",
                  window=window, clarify=clarify)


def _own(turn: Turn) -> dict:
    return parse_from_turn(turn.expect, turn.prompt, fill_placeholders(turn.prompt, VALUES),
                           mapping=VALUES)


def test_current_scenarios_answer_turns_all_carry_reply_to() -> None:
    """전제 실측: 소스 되묻기 바로 뒤 턴은 전부 `reply_to: source`다.

    아래 두 고정 테스트(verifier Low ⑦ 교정 — `reply_to` 사슬 판정)의 현 시나리오 실측 영향은
    0이다."""
    after_ask = [(s.id, i, s.turns[i].reply_to) for s in load_scenarios()
                 for i in range(1, len(s.turns)) if s.turns[i - 1].expect.clarify == "source"]
    assert after_ask and all(r == "source" for _, _, r in after_ask), after_ask


def test_non_answer_turn_after_source_ask_uses_own_prompt() -> None:
    scenario = Scenario("S", ("COV-X",), "dev", (
        Turn("{{inst_c}} 응답시간 30분", _expect("range:30", clarify="source")),
        Turn("{{inst_c}} 응답시간 5분", _expect("range:5")),
    ))
    assert not check_structure([scenario]), "구조 검사는 이 형태를 허용한다"
    assert dv.parse_for_turn(scenario, 1, VALUES) == _own(scenario.turns[1])


def test_chained_answer_turns_use_first_question() -> None:
    first = Turn("{{inst_c}} 응답시간 30분", _expect("range:30", clarify="source"))
    scenario = Scenario("S", ("COV-X",), "dev", (
        first,
        Turn("공동존", _expect("range:30", clarify="source"), reply_to="source"),
        Turn("공동존 운영", _expect("range:30"), reply_to="source"),
    ))
    assert not check_structure([scenario])
    got = dv.parse_for_turn(scenario, 2, VALUES)
    assert got["time_expr"] is not None and got["time_expr"]["n"] == 30
