"""T0 스파이크 — 중첩 그래프 이벤트 전파 실측 (plans/89 §3.2-②③ 전제 · D-204).

deep_agent 노드는 바깥 그래프의 노드 안에서 **내부 CompiledGraph를 `ainvoke`** 한다
(`src/orchestration/deep_agent.py:227`, 부모 콜백을 config에 명시하지 않는다). 계획서
§3.2-②③은 그 내부 도구 호출(`on_tool_start`)과 커스텀 이벤트(`on_custom_event`)가 바깥
`astream_events(v2)`에 나타난다는 전제 위에 서 있다. 이 테스트가 그 전제를 실 LangGraph로
고정한다 — 실패하면 계획서 T0 대체 경로(콜백 명시 전달)로 간다.

실 LLM 0 · 네트워크 0 (FakeMessagesListChatModel).
"""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

import pytest
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph


class _Inner(TypedDict, total=False):
    messages: list


class _Outer(TypedDict, total=False):
    user_query: str
    final_response: str


@tool
async def query_infra_db(sub_query: str) -> str:
    """인프라 DB 조회(더미)."""
    # 핸들러 내부 마일스톤 — plans/89 §3.2-③ / SPEC-composite-task-progress
    await adispatch_custom_event(
        "task", {"task_id": "tool_data_query_1", "order": 1, "phase": "start", "sub_query": sub_query}
    )
    await adispatch_custom_event(
        "task", {"task_id": "tool_data_query_1", "order": 1, "phase": "end", "status": "completed", "row_count": 3}
    )
    return "3 rows"


def _build_inner_graph():
    """tool-calling 루프를 흉내 내는 최소 내부 그래프: model → tools → model(END)."""
    llm = FakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "query_infra_db", "args": {"sub_query": "CPU 높은 서버"}, "id": "c1"}],
            ),
            AIMessage(content="완료"),
        ]
    )

    async def model(state: _Inner) -> _Inner:
        msg = await llm.ainvoke(state["messages"])
        return {"messages": state["messages"] + [msg]}

    async def tools(state: _Inner) -> _Inner:
        last = state["messages"][-1]
        out = []
        for call in getattr(last, "tool_calls", []) or []:
            result = await query_infra_db.ainvoke(call["args"])
            out.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
        return {"messages": state["messages"] + out}

    def route(state: _Inner) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    g = StateGraph(_Inner)
    g.add_node("model", model)
    g.add_node("tools", tools)
    g.set_entry_point("model")
    g.add_conditional_edges("model", route, {"tools": "tools", END: END})
    g.add_edge("tools", "model")
    return g.compile()


def _build_outer_graph():
    inner = _build_inner_graph()

    async def deep_agent(state: _Outer) -> _Outer:
        # deep_agent.py:227과 동일 — config에 부모 콜백을 넘기지 않는다(recursion_limit만).
        result = await inner.ainvoke(
            {"messages": [HumanMessage(content=state["user_query"])]},
            config={"recursion_limit": 10},
        )
        return {"final_response": result["messages"][-1].content}

    g = StateGraph(_Outer)
    g.add_node("deep_agent", deep_agent)
    g.set_entry_point("deep_agent")
    g.add_edge("deep_agent", END)
    return g.compile()


async def _collect(graph) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for ev in graph.astream_events({"user_query": "q"}, {"configurable": {"thread_id": "t"}}, version="v2"):
        events.append(ev)
    return events


@pytest.fixture(scope="module")
def outer_events() -> list[dict[str, Any]]:
    return asyncio.run(_collect(_build_outer_graph()))


def test_outer_graph_emits_deep_agent_chain_events(outer_events):
    names = [e.get("name") for e in outer_events if e.get("event") == "on_chain_start"]
    assert "deep_agent" in names


def test_nested_tool_start_propagates_to_outer_stream(outer_events):
    """★ 전제 ②: 내부 그래프의 도구 호출이 바깥 astream_events에 on_tool_start로 나타난다."""
    tool_starts = [e for e in outer_events if e.get("event") == "on_tool_start"]
    assert tool_starts, "내부 그래프 on_tool_start가 바깥 스트림에 전파되지 않았다 — T0 대체 경로 필요"
    assert tool_starts[0]["name"] == "query_infra_db"
    assert tool_starts[0]["data"]["input"] == {"sub_query": "CPU 높은 서버"}
    tool_ends = [e for e in outer_events if e.get("event") == "on_tool_end"]
    assert tool_ends and tool_ends[0]["name"] == "query_infra_db"


def test_nested_custom_event_propagates_to_outer_stream(outer_events):
    """★ 전제 ③: 도구 핸들러 안의 adispatch_custom_event가 on_custom_event로 나타난다."""
    customs = [e for e in outer_events if e.get("event") == "on_custom_event"]
    assert len(customs) == 2, f"custom event {len(customs)}건 — 기대 2건(start/end)"
    assert [c["name"] for c in customs] == ["task", "task"]
    assert customs[0]["data"]["phase"] == "start"
    assert customs[1]["data"]["row_count"] == 3


def test_nested_events_carry_outer_node_metadata(outer_events):
    """바깥 노드명이 metadata.langgraph_node로 붙는다 — SSE 변환 시 node 필드의 근거."""
    tool_starts = [e for e in outer_events if e.get("event") == "on_tool_start"]
    meta = tool_starts[0].get("metadata", {})
    assert meta.get("langgraph_node") in ("deep_agent", "tools"), meta
