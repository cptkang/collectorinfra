"""그래프 라우팅 함수 테스트."""

import pytest

from src.graph import route_after_semantic_router
from src.state import create_initial_state


class TestRouteAfterSemanticRouter:
    """route_after_semantic_router 함수 테스트."""

    def test_single_db_routes_to_schema_analyzer(self):
        """단일 DB 질의는 schema_analyzer로 라우팅된다."""
        state = create_initial_state(user_query="테스트")
        state["is_multi_db"] = False
        result = route_after_semantic_router(state)
        assert result == "schema_analyzer"

    def test_multi_db_routes_to_multi_db_executor(self):
        """멀티 DB 질의는 multi_db_executor로 라우팅된다."""
        state = create_initial_state(user_query="테스트")
        state["is_multi_db"] = True
        result = route_after_semantic_router(state)
        assert result == "multi_db_executor"

    def test_default_routes_to_schema_analyzer(self):
        """is_multi_db가 설정되지 않으면 schema_analyzer로 라우팅된다."""
        state = create_initial_state(user_query="테스트")
        # is_multi_db 기본값은 False
        result = route_after_semantic_router(state)
        assert result == "schema_analyzer"
