"""approval_gate 노드 테스트."""

import pytest

from src.nodes.approval_gate import approval_gate
from src.state import create_initial_state


class TestApprovalGateFirstEntry:
    """첫 진입 (approval_action=None) 시 동작 검증."""

    async def test_sets_awaiting_approval(self):
        """첫 진입 시 awaiting_approval=True로 설정한다."""
        state = create_initial_state(user_query="서버 목록")
        state["generated_sql"] = "SELECT * FROM servers"
        state["validation_result"] = {"passed": True, "reason": "ok", "auto_fixed_sql": None}

        result = await approval_gate(state)

        assert result["awaiting_approval"] is True
        assert result["approval_context"]["type"] == "sql_approval"
        assert "SELECT * FROM servers" in result["approval_context"]["sql"]
        assert "SQL을 실행하시겠습니까?" in result["final_response"]

    async def test_includes_sql_in_response(self):
        """응답에 SQL이 포함된다."""
        state = create_initial_state(user_query="test")
        state["generated_sql"] = "SELECT hostname FROM servers LIMIT 10"

        result = await approval_gate(state)
        assert "SELECT hostname FROM servers LIMIT 10" in result["final_response"]


class TestApprovalGateApprove:
    """승인 시 동작 검증."""

    async def test_approve_clears_approval_state(self):
        """승인 시 approval 상태가 해제된다."""
        state = create_initial_state(user_query="실행")
        state["approval_action"] = "approve"

        result = await approval_gate(state)

        assert result["awaiting_approval"] is False
        assert result["approval_context"] is None
        assert result["approval_action"] is None


class TestApprovalGateReject:
    """거부 시 동작 검증."""

    async def test_reject_generates_cancel_response(self):
        """거부 시 취소 응답이 생성된다."""
        state = create_initial_state(user_query="취소")
        state["approval_action"] = "reject"

        result = await approval_gate(state)

        assert result["awaiting_approval"] is False
        assert "취소" in result["final_response"]


class TestApprovalGateModify:
    """수정 시 동작 검증."""

    async def test_modify_updates_generated_sql(self):
        """수정 시 generated_sql이 변경된다."""
        state = create_initial_state(user_query="SELECT * FROM servers WHERE id > 5")
        state["approval_action"] = "modify"
        state["approval_modified_sql"] = "SELECT * FROM servers WHERE id > 5"

        result = await approval_gate(state)

        assert result["awaiting_approval"] is False
        assert result["generated_sql"] == "SELECT * FROM servers WHERE id > 5"
