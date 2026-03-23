"""AgentState 시멘틱 라우팅 확장 필드 테스트 (v2)."""

import pytest

from src.state import AgentState, create_initial_state


class TestAgentStateSemanticRouting:
    """AgentState 시멘틱 라우팅 필드 테스트."""

    def test_initial_state_has_routing_fields(self):
        """초기 State에 시멘틱 라우팅 필드가 포함되어 있다."""
        state = create_initial_state(user_query="테스트 질의")
        assert "target_databases" in state
        assert "active_db_id" in state
        assert "db_results" in state
        assert "db_schemas" in state
        assert "db_errors" in state
        assert "is_multi_db" in state
        assert "user_specified_db" in state

    def test_initial_state_routing_defaults(self):
        """초기 State의 라우팅 필드가 올바른 기본값을 갖는다."""
        state = create_initial_state(user_query="테스트 질의")
        assert state["target_databases"] == []
        assert state["active_db_id"] is None
        assert state["db_results"] == {}
        assert state["db_schemas"] == {}
        assert state["db_errors"] == {}
        assert state["is_multi_db"] is False
        assert state["user_specified_db"] is None

    def test_initial_state_preserves_existing_fields(self):
        """시멘틱 라우팅 필드 추가가 기존 필드에 영향을 주지 않는다."""
        state = create_initial_state(
            user_query="테스트 질의",
            uploaded_file=b"test",
            file_type="xlsx",
        )
        assert state["user_query"] == "테스트 질의"
        assert state["uploaded_file"] == b"test"
        assert state["file_type"] == "xlsx"
        assert state["retry_count"] == 0
        assert state["generated_sql"] == ""
        assert state["final_response"] == ""
