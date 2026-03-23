"""멀티턴 관련 AgentState 확장 테스트."""

import pytest

from langchain_core.messages import AIMessage, HumanMessage

from src.state import AgentState, create_initial_state


class TestMultiturnStateFields:
    """멀티턴 관련 새 필드가 AgentState에 올바르게 추가되었는지 검증."""

    def test_create_initial_state_has_multiturn_fields(self):
        """초기 State에 멀티턴 필드가 포함된다."""
        state = create_initial_state(user_query="test")

        assert "messages" in state
        assert "thread_id" in state
        assert "conversation_context" in state
        assert "awaiting_approval" in state
        assert "approval_context" in state
        assert "approval_action" in state
        assert "approval_modified_sql" in state

    def test_initial_messages_has_human_message(self):
        """초기 State의 messages에 사용자 질의가 HumanMessage로 포함된다."""
        state = create_initial_state(user_query="서버 목록 조회")

        assert len(state["messages"]) == 1
        assert isinstance(state["messages"][0], HumanMessage)
        assert state["messages"][0].content == "서버 목록 조회"

    def test_thread_id_parameter(self):
        """thread_id가 올바르게 설정된다."""
        state = create_initial_state(user_query="test", thread_id="session-123")
        assert state["thread_id"] == "session-123"

    def test_thread_id_default_none(self):
        """thread_id 미제공 시 None이다."""
        state = create_initial_state(user_query="test")
        assert state["thread_id"] is None

    def test_initial_hitl_fields(self):
        """HITL 필드의 초기값이 올바르다."""
        state = create_initial_state(user_query="test")
        assert state["awaiting_approval"] is False
        assert state["approval_context"] is None
        assert state["approval_action"] is None
        assert state["approval_modified_sql"] is None

    def test_conversation_context_initial_none(self):
        """conversation_context의 초기값이 None이다."""
        state = create_initial_state(user_query="test")
        assert state["conversation_context"] is None
