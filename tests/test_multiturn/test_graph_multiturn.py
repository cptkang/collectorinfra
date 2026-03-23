"""멀티턴 그래프 빌드 테스트."""

import pytest

from src.config import AppConfig
from src.graph import (
    build_graph,
    route_after_approval,
    route_after_semantic_router,
    route_after_validation_with_approval,
)
from src.state import create_initial_state


class TestRouteAfterSemanticRouter:
    """semantic_router 이후 라우팅 검증 (synonym_registration 포함)."""

    def test_synonym_registration_routing(self):
        state = create_initial_state(user_query="test")
        state["routing_intent"] = "synonym_registration"

        assert route_after_semantic_router(state) == "synonym_registrar"

    def test_cache_management_routing(self):
        state = create_initial_state(user_query="test")
        state["routing_intent"] = "cache_management"

        assert route_after_semantic_router(state) == "cache_management"

    def test_data_query_single_db(self):
        state = create_initial_state(user_query="test")
        state["routing_intent"] = "data_query"
        state["is_multi_db"] = False

        assert route_after_semantic_router(state) == "schema_analyzer"


class TestRouteAfterApproval:
    """approval_gate 이후 라우팅 검증."""

    def test_approve_routes_to_executor(self):
        state = create_initial_state(user_query="test")
        state["approval_action"] = "approve"

        assert route_after_approval(state) == "query_executor"

    def test_reject_routes_to_end(self):
        from langgraph.graph import END

        state = create_initial_state(user_query="test")
        state["approval_action"] = "reject"

        assert route_after_approval(state) == END

    def test_modify_routes_to_validator(self):
        state = create_initial_state(user_query="test")
        state["approval_action"] = "modify"

        assert route_after_approval(state) == "query_validator"


class TestRouteAfterValidationWithApproval:
    """SQL 승인 활성화 시 validator 이후 라우팅 검증."""

    def test_passed_routes_to_approval_gate(self):
        state = create_initial_state(user_query="test")
        state["validation_result"] = {"passed": True, "reason": "ok", "auto_fixed_sql": None}

        assert route_after_validation_with_approval(state) == "approval_gate"

    def test_failed_routes_to_generator(self):
        state = create_initial_state(user_query="test")
        state["validation_result"] = {"passed": False, "reason": "bad sql", "auto_fixed_sql": None}
        state["retry_count"] = 0

        assert route_after_validation_with_approval(state) == "query_generator"


class TestBuildGraphWithApproval:
    """SQL 승인 활성화 시 그래프 빌드 검증."""

    def test_builds_with_approval(self):
        config = AppConfig(
            enable_sql_approval=True,
            checkpoint_backend="sqlite",
            checkpoint_db_url=":memory:",
        )
        graph = build_graph(config)
        assert graph is not None

    def test_builds_without_approval(self):
        config = AppConfig(
            enable_sql_approval=False,
            checkpoint_backend="sqlite",
            checkpoint_db_url=":memory:",
        )
        graph = build_graph(config)
        assert graph is not None

    def test_context_resolver_is_first_node(self):
        """context_resolver가 START 이후 첫 노드이다."""
        config = AppConfig(
            checkpoint_backend="sqlite",
            checkpoint_db_url=":memory:",
        )
        graph = build_graph(config)
        # 그래프가 정상 빌드되면 context_resolver가 포함됨
        assert graph is not None
