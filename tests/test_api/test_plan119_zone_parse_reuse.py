"""plans/119 Q-2 — 존 역질문 답변 턴의 파싱 재사용 (D-267 ③).

직전 턴이 후단 게이트로 존을 되물었고, 이번 턴이 같은 원 질의 + 존 선택뿐이면 라우트가 직전 턴
파싱본을 요청 스코프 키(`reuse_parsed_requirements`)로 싣고 `input_parser`는 LLM을 부르지 않는다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.routes.query import _build_turn_input_state, _zone_answer_parse_reuse
from src.api.schemas import QueryRequest
from src.nodes.input_parser import input_parser
from src.state import create_followup_input, create_initial_state

_QUERY = "서버 CPU 사용률 상위 10건"
_PARSED = {
    "original_query": _QUERY,
    "query_targets": ["서버", "CPU 사용률"],
    "filter_conditions": [],
    "output_format": "text",
    "limit": 10,
    "target_db_hints": [],
}
_USER = {"sub": "u1", "role": "user", "allowed_db_ids": None}


def _checkpoint(**over) -> dict:
    state = {
        "zone_clarification": {"kind": "zone_select", "original_query": _QUERY, "question": "?"},
        "parsed_requirements": dict(_PARSED),
        "messages": [],
    }
    state.update(over)
    return state


def _body(query: str = _QUERY, selected=("db_a",)) -> QueryRequest:
    return QueryRequest(query=query, selected_db_ids=list(selected) if selected else None)


class TestRouteReuseCondition:
    def test_answer_turn_reuses_prior_parse(self):
        reused = _zone_answer_parse_reuse(_body(), _checkpoint())
        assert reused == _PARSED
        assert reused is not _checkpoint()["parsed_requirements"]  # 사본

    @pytest.mark.parametrize(
        "body,cp",
        [
            (_body(selected=()), _checkpoint()),                                  # 존 선택 없음
            (_body(query="다른 질의"), _checkpoint()),                              # 원 질의 불일치
            (_body(), _checkpoint(zone_clarification=None)),                       # 역질문 없던 턴
            (_body(), _checkpoint(parsed_requirements={})),                        # 파싱본 없음
            (_body(), _checkpoint(zone_clarification={"original_query": ""})),     # 원 질의 없음
        ],
    )
    def test_no_reuse_outside_condition(self, body, cp):
        assert _zone_answer_parse_reuse(body, cp) is None

    def test_turn_input_carries_reuse_only_on_answer_turn(self):
        delta = _build_turn_input_state(_body(), "t1", _checkpoint(), _USER)
        assert delta["reuse_parsed_requirements"] == _PARSED
        plain = _build_turn_input_state(_body(selected=()), "t1", _checkpoint(), _USER)
        assert plain["reuse_parsed_requirements"] is None

    def test_request_scope_reset_in_state_factories(self):
        # 체크포인터는 델타만 병합한다 — 두 생성기 모두 None으로 명시 초기화해야 한다.
        assert create_followup_input("q")["reuse_parsed_requirements"] is None
        assert create_initial_state(user_query="q")["reuse_parsed_requirements"] is None


class TestInputParserSkip:
    @pytest.mark.asyncio
    async def test_reuse_skips_llm(self):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=AssertionError("LLM 호출 금지"))
        state = create_initial_state(user_query=_QUERY)
        state["reuse_parsed_requirements"] = dict(_PARSED)
        out = await input_parser(state, llm=llm, app_config=MagicMock())
        assert out["parsed_requirements"]["query_targets"] == _PARSED["query_targets"]
        assert out["parsed_requirements"]["original_query"] == _QUERY
        assert out["template_structure"] is None
        llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_reuse_calls_llm(self):
        llm = MagicMock()
        resp = MagicMock()
        resp.content = '{"query_targets": ["서버"], "filter_conditions": []}'
        llm.ainvoke = AsyncMock(return_value=resp)
        state = create_initial_state(user_query=_QUERY)
        cfg = MagicMock()
        cfg.structured_output_backend = "none"
        await input_parser(state, llm=llm, app_config=cfg)
        assert llm.ainvoke.await_count >= 1
