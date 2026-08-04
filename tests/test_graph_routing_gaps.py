"""그래프 라우팅 함수의 분기 고정 테스트 (Plan 69 P1-2 / §1.8 공백 보강).

리팩토링(P3~P5)의 "동작 불변" 판정 기준 — 각 라우팅 함수가 어떤 상태에서 어느 노드
이름을 돌려주는지를 분기별로 못 박는다. 특히 승인 게이트는 미지 값·None에서 END로
닫히는 fail-closed(D-130)가 유지돼야 하므로 langgraph의 END 상수와 직접 비교한다.

현행 동작 고정이 목적이다 — 버그 수정이 아니라 지금 그대로를 기록한다.
"""

from __future__ import annotations

import pytest
from langgraph.graph import END

from src.graph import (
    _INTENT_ROUTE_MAP,
    route_after_approval,
    route_after_orchestrator,
    route_after_replanner,
    route_after_schema_analyzer,
    route_after_semantic_router,
    route_after_structure_approval,
)
from src.state import create_initial_state


def _state(**overrides) -> dict:
    state = create_initial_state(user_query="test")
    state.update(overrides)
    return state


class TestRouteAfterApproval:
    """SQL 승인 게이트 — 명시 승인 외에는 실행하지 않는다(fail-closed, D-130)."""

    def test_approve_goes_to_executor(self):
        assert route_after_approval(_state(approval_action="approve")) == "query_executor"

    def test_modify_goes_back_to_validator(self):
        assert route_after_approval(_state(approval_action="modify")) == "query_validator"

    @pytest.mark.parametrize(
        "action", ["reject", None, "", "APPROVE", "approve ", "unknown_action"]
    )
    def test_everything_else_ends(self, action):
        """reject·None·대소문자/공백 변형·미지 값은 전부 END(승인 문자열 정확 일치만 통과)."""
        assert route_after_approval(_state(approval_action=action)) == END

    def test_missing_key_ends(self):
        """approval_action 키 자체가 없어도 END."""
        state = create_initial_state(user_query="test")
        state.pop("approval_action", None)
        assert route_after_approval(state) == END


class TestRouteAfterSchemaAnalyzer:
    """구조 분석 HITL 회부 — 승인 대기 + 구조 분석 컨텍스트일 때만 승인 게이트로 간다."""

    def test_structure_analysis_approval_goes_to_gate(self):
        state = _state(
            awaiting_approval=True,
            approval_context={"type": "structure_analysis"},
        )
        assert route_after_schema_analyzer(state) == "structure_approval_gate"

    def test_other_approval_context_continues_to_generator(self):
        """승인 대기라도 컨텍스트 유형이 다르면 그대로 SQL 생성으로 진행한다."""
        state = _state(
            awaiting_approval=True,
            approval_context={"type": "text2sql_low_confidence"},
        )
        assert route_after_schema_analyzer(state) == "query_generator"

    def test_empty_context_continues_to_generator(self):
        """컨텍스트가 빈 dict면 SQL 생성으로 진행한다.

        (approval_context가 None인 조합은 현행 코드가 AttributeError를 내므로 여기서
        단언하지 않는다 — 안전망이 결함을 정답으로 굳히지 않게 별도 보고 대상.)
        """
        state = _state(awaiting_approval=True, approval_context={})
        assert route_after_schema_analyzer(state) == "query_generator"

    def test_no_approval_goes_to_generator(self):
        assert route_after_schema_analyzer(_state()) == "query_generator"


class TestRouteAfterStructureApproval:
    """구조 분석 승인 결과 — approve만 재분석으로 되돌아가고 나머지는 계속 진행한다."""

    def test_approve_reenters_schema_analyzer(self):
        assert route_after_structure_approval(_state(approval_action="approve")) == "schema_analyzer"

    @pytest.mark.parametrize("action", ["reject", None, "modify", "unknown"])
    def test_non_approve_continues_to_generator(self, action):
        """구조 메타 없이 진행 — 승인 게이트와 달리 종료가 아니다(현행 동작)."""
        assert route_after_structure_approval(_state(approval_action=action)) == "query_generator"


class TestRouteAfterSemanticRouter:
    """의도 매핑 → 멀티 DB → 단일 DB 순서의 우선순위를 고정한다."""

    @pytest.mark.parametrize("intent,node", sorted(_INTENT_ROUTE_MAP.items()))
    def test_mapped_intents(self, intent, node):
        assert route_after_semantic_router(_state(routing_intent=intent)) == node

    def test_intent_wins_over_multi_db(self):
        """의도 매핑이 is_multi_db보다 우선한다(현행 분기 순서)."""
        state = _state(routing_intent="cache_management", is_multi_db=True)
        assert route_after_semantic_router(state) == "cache_management"

    def test_multi_db_without_mapped_intent(self):
        state = _state(routing_intent="data_query", is_multi_db=True)
        assert route_after_semantic_router(state) == "multi_db_executor"

    def test_default_single_db(self):
        assert route_after_semantic_router(_state(routing_intent="data_query")) == "schema_analyzer"


class TestRouteAfterOrchestratorAndReplanner:
    """오케스트레이션 루프 — 종료 판정은 replanner 한 곳에 모여 있다(Plan 49 §3.3)."""

    def test_orchestrator_always_goes_to_replanner(self):
        assert route_after_orchestrator(_state()) == "replanner"
        assert route_after_orchestrator(_state(needs_replan=False)) == "replanner"

    def test_replanner_reenters_orchestrator_when_replan_needed(self):
        assert route_after_replanner(_state(needs_replan=True)) == "agent_orchestrator"

    @pytest.mark.parametrize("needs_replan", [False, None])
    def test_replanner_aggregates_when_done(self, needs_replan):
        assert route_after_replanner(_state(needs_replan=needs_replan)) == "result_aggregator"
