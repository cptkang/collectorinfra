"""스코프 칩 "해제"(reset_db_scope) — 3지점 대칭 회귀 (plans/90 · D-205 · SPEC-thread-db-scope §Success 4).

해제는 세 곳이 함께 움직여야 한다:
  ⓐ create_followup_input — 승계 원천(active_db_id/target_databases/mapped_db_ids) 명시 초기화
  ⓑ context_resolver      — sticky 폴백(prior_ctx.previous_db_ids) 차단, previous_entities는 유지
  ⓒ pre-gate              — 해제 턴은 첫 턴 규칙(존 미지정 대량 조회면 역질문)
하나라도 빠지면 칩은 "미지정"인데 다음 질의는 조용히 직전 존으로 간다. LLM·DB 0.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.api.routes.query import _scope_select_or_none, _zone_clarification_or_none
from src.api.schemas import QueryRequest
from src.config import CompositeConfig, MultiDBConfig
from src.nodes.context_resolver import context_resolver
from src.state import create_followup_input

B0 = "polestar_b0"
ALL = [B0, "polestar_cm_gp", "polestar_cm_yd"]


def _config():
    return SimpleNamespace(
        multi_db=MultiDBConfig(active_db_ids_csv=",".join(ALL)),
        composite=CompositeConfig(scope_select_enabled=True),
    )


class TestFollowupInput:
    def test_default_does_not_touch_succession_carriers(self):
        """기본값(False)이면 승계 원천 키가 델타에 없다 — 체크포인트 값 보존(현행 동작 동일)."""
        delta = create_followup_input("그 서버 메모리")
        assert delta["db_scope_reset"] is False
        assert delta["db_scope_source"] is None
        for k in ("active_db_id", "target_databases", "mapped_db_ids"):
            assert k not in delta

    def test_reset_clears_all_three_carriers(self):
        delta = create_followup_input("모든 서버 CPU", reset_db_scope=True)
        assert delta["db_scope_reset"] is True
        assert delta["active_db_id"] is None
        assert delta["target_databases"] == []
        assert delta["mapped_db_ids"] is None  # G-4: 폼필 고정 DB도 비운다


class TestContextResolver:
    @staticmethod
    def _state(**over):
        base = {
            "messages": [HumanMessage("a"), AIMessage("b"), HumanMessage("c")],
            "generated_sql": "", "query_results": [], "relevant_tables": [],
            "active_db_id": None, "target_databases": [], "mapped_db_ids": None,
            "parsed_requirements": {},
            "conversation_context": {
                "previous_db_ids": [B0],
                "previous_entities": [{"field": "hostname", "value": "abd00"}],
                "previous_location": "은행존",
            },
        }
        base.update(over)
        return base

    @pytest.mark.asyncio
    async def test_sticky_survives_without_reset(self):
        out = await context_resolver(self._state())
        assert out["conversation_context"]["previous_db_ids"] == [B0]
        assert out["conversation_context"]["previous_location"] == "은행존"

    @pytest.mark.asyncio
    async def test_reset_breaks_sticky_but_keeps_entities(self):
        out = await context_resolver(self._state(db_scope_reset=True))
        ctx = out["conversation_context"]
        assert ctx["previous_db_ids"] == []
        assert ctx["previous_location"] == ""
        assert ctx["previous_entities"] == [{"field": "hostname", "value": "abd00"}]


class TestPreGate:
    MASS = "모든 서버들의 OS 종류를 확인해줘"

    def test_followup_without_reset_still_passes(self):
        """기존 계약 유지 — 스코프 없는 후속 턴도 비발동(SPEC Open Q1)."""
        body = QueryRequest(query=self.MASS)
        assert _zone_clarification_or_none(body, {"prev": True}, _config()) is None

    def test_reset_turn_asks_again(self):
        body = QueryRequest(query=self.MASS, reset_db_scope=True)
        clar = _zone_clarification_or_none(body, {"prev": True}, _config())
        assert clar is not None and clar["kind"] == "zone_select"

    def test_reset_turn_with_zone_term_does_not_ask(self):
        """위치어가 있으면 첫 턴과 마찬가지로 D-065가 결정적으로 좁힌다."""
        body = QueryRequest(query="은행존의 " + self.MASS, reset_db_scope=True)
        assert _zone_clarification_or_none(body, {"prev": True}, _config()) is None

    def test_scope_select_treats_reset_as_first_turn(self):
        body = SimpleNamespace(query="모든 서버의 OS 버전을 조회해줘", selected_db_ids=None, reset_db_scope=True)
        assert _scope_select_or_none(body, {"any": "state"}, _config(), None) is not None

    def test_scope_select_followup_without_reset_unchanged(self):
        body = SimpleNamespace(query="모든 서버의 OS 버전을 조회해줘", selected_db_ids=None)
        assert _scope_select_or_none(body, {"any": "state"}, _config(), None) is None
