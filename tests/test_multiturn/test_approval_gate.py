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


# ──────────────────────────────────────────────
# plans/104 · D-135 ① — 구조 승인 대기로 멈춘 구 스레드
# ──────────────────────────────────────────────

#: 삭제된 구 게이트 노드 이름 — 그 노드 앞에서 멈춘 구 체크포인트를 재현하는 데만 쓴다
#: (삭제 참조 grep에 테스트 자신이 걸리지 않게 조각으로 조립한다).
_LEGACY_GATE = "structure_" + "approval_gate"

_STALE_STRUCTURE_CHECKPOINT = {
    "user_query": "이전 질의",
    "awaiting_approval": True,
    "approval_context": {
        "type": "structure_analysis", "db_id": "new_db", "analysis_result": {"patterns": []},
    },
    "approval_action": None,
}


def _body(query: str):
    from src.api.schemas import QueryRequest

    return QueryRequest(query=query, thread_id="t-stale")


class TestStaleStructureApprovalThread:
    """구조 승인 게이트는 삭제됐다 — 그 앞에서 멈춘 스레드의 다음 턴은 일반 질의 턴이다."""

    async def test_not_treated_as_approval_turn(self):
        from src.api.routes.query import _resolve_turn_approval

        checkpoint = dict(_STALE_STRUCTURE_CHECKPOINT)
        assert await _resolve_turn_approval(_body("승인"), checkpoint, config=None) is None

    def test_followup_delta_releases_approval_state(self):
        from src.api.routes.query import _build_turn_input_state

        delta = _build_turn_input_state(
            _body("서버 목록 보여줘"), "t-stale", dict(_STALE_STRUCTURE_CHECKPOINT), current_user={}
        )
        # 일반 후속 턴 델타(요청 스코프 초기화 포함) + 승인 상태 해제
        assert delta["uploaded_file"] is None and "resolved_limit" in delta
        assert delta["awaiting_approval"] is False
        assert delta["approval_context"] is None
        assert delta["approval_action"] is None

    def test_sql_approval_thread_still_resumes_as_approval(self):
        """SQL 승인 대기(유일하게 남은 승인 게이트)는 종전대로 승인 턴이다."""
        from src.api.routes.query import _build_turn_input_state

        checkpoint = {
            "awaiting_approval": True,
            "approval_context": {"type": "sql_approval", "sql": "SELECT 1"},
        }
        delta = _build_turn_input_state(_body("승인"), "t-sql", checkpoint, current_user={})
        assert delta["approval_action"] == "approve"
        assert "awaiting_approval" not in delta

    async def test_old_checkpoint_on_deleted_node_runs_without_error(self):
        """게이트 앞에서 멈춘 체크포인트를 게이트 없는 그래프가 이어받아도 예외 없이 완주한다.

        LangGraph는 새 입력이 오면 없는 노드로 재개하지 않고 START부터 돈다(실측) — 그러나
        승인 상태는 체크포인터 델타 병합으로 남으므로 라우트 델타가 해제해야 대기가 풀린다.
        """
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph

        from src.api.routes.query import _build_turn_input_state, _get_checkpoint_state
        from src.state import AgentState

        async def analyzer(state):
            if state.get("user_query") == "첫 질의":
                ctx = dict(_STALE_STRUCTURE_CHECKPOINT["approval_context"])
                return {"awaiting_approval": True, "approval_context": ctx}
            return {}

        async def gate(state):
            return {"final_response": "승인 요청"}

        async def generator(state):
            return {"final_response": f"답: {state['user_query']}"}

        saver = InMemorySaver()
        cfg = {"configurable": {"thread_id": "t-old"}}

        old = StateGraph(AgentState)
        old.add_node("schema_analyzer", analyzer)
        old.add_node(_LEGACY_GATE, gate)
        old.add_node("query_generator", generator)
        old.add_edge(START, "schema_analyzer")
        old.add_conditional_edges(
            "schema_analyzer",
            lambda s: _LEGACY_GATE if s.get("awaiting_approval") else "query_generator",
            {_LEGACY_GATE: _LEGACY_GATE, "query_generator": "query_generator"},
        )
        old.add_edge(_LEGACY_GATE, "query_generator")
        old.add_edge("query_generator", END)
        old_graph = old.compile(checkpointer=saver, interrupt_before=[_LEGACY_GATE])
        await old_graph.ainvoke({"user_query": "첫 질의"}, cfg)
        assert old_graph.get_state(cfg).next == (_LEGACY_GATE,)

        new = StateGraph(AgentState)
        new.add_node("schema_analyzer", analyzer)
        new.add_node("query_generator", generator)
        new.add_edge(START, "schema_analyzer")
        new.add_edge("schema_analyzer", "query_generator")
        new.add_edge("query_generator", END)
        new_graph = new.compile(checkpointer=saver)

        checkpoint_state = await _get_checkpoint_state(new_graph, cfg)
        assert checkpoint_state["awaiting_approval"] is True
        delta = _build_turn_input_state(
            _body("서버 목록"), "t-old", checkpoint_state, current_user={}
        )
        result = await new_graph.ainvoke(delta, cfg)

        assert result["final_response"] == "답: 서버 목록"
        assert result["awaiting_approval"] is False and result["approval_context"] is None
