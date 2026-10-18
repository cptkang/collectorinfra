"""plans/181 W1 독립 검증 — `create_initial_state` 인자 칸(`_FROM_ARGS`) 덮어쓰기 보호.

`_FROM_ARGS`는 `None`과 같은 값이라, 표에 자리만 잡고 `state.update(...)`에서 인자를 빠뜨려도
키 집합 단언(`test_plan181_output_state`)은 통과한다(변이 V17 생존). 인자를 전부 채워 부른 결과에서
인자에서 오는 칸 21개가 입력값(또는 파생값)인지 고정한다.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from src.state import _INITIAL_STATE_DEFAULTS, create_initial_state

_ARGS = {
    "uploaded_file": b"PK",
    "file_type": "xlsx",
    "thread_id": "th-1",
    "csv_sheet_data": {"S": {}},
    "user_id": "u1",
    "user_department": "dept",
    "user_role": "operator",
    "allowed_db_ids": ["polestar"],
    "allowed_sources": ["polestar"],
    "request_id": "req-1",
    "client_ip": "10.0.0.1",
    "selected_db_ids": ["polestar_cm_gp"],
    "resolved_limit": 5000,
    "allow_zone_clarification": True,
    "raw_user_query": "원문",
    "selected_sources": ["apm"],
    "selected_apm_source_ids": ["a"],
}

#: 인자에서 오는 상태 키 → 기대값(파생 3종 포함)
_EXPECTED = {
    **{k: v for k, v in _ARGS.items() if k != "allow_zone_clarification"},
    "user_query": "질의",
    "zone_clarification_allowed": True,
    "display_query": "질의",
    "apm_source_basis": "selected",
}


def test_arg_slots_all_overwritten() -> None:
    """인자를 전부 채우면 인자 칸 21개가 모두 입력값·파생값이다(update 누락 시 None 잔류 → 실패)."""
    state = create_initial_state("질의", **_ARGS)
    assert len(_EXPECTED) + 1 == 21  # + messages
    for key, expected in _EXPECTED.items():
        assert state[key] == expected, key  # type: ignore[literal-required]
    messages = state["messages"]
    assert len(messages) == 1 and isinstance(messages[0], HumanMessage)
    assert messages[0].content == "질의"


def test_arg_slots_are_table_none_placeholders() -> None:
    """인자 칸은 표에서 자리표시(None)다 — 표가 인자 값을 고정 기본값으로 덮지 않는다."""
    table = dict(_INITIAL_STATE_DEFAULTS)
    for key in (*_EXPECTED, "messages"):
        assert key in table and table[key] is None, key
