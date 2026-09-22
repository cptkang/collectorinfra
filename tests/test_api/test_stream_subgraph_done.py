"""SSE 종료 판정 — 서브그래프 안의 `final_response`로 스트림이 닫히지 않는다 (plans/103 K-3 · P0-3).

`astream_events(v2)`의 `parent_ids` 깊이로 루트 직속 노드와 서브그래프 안 노드를 가른다
(루트 0 · 루트 직속 1 · 노드 안 그래프 ≥2). 실 LangGraph로 깊이를 고정한다 — 버전이 바뀌어
깊이 규칙이 달라지면 여기서 깨진다. 실 LLM 0.
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from src.api.routes.query import _is_subgraph_event


class _S(TypedDict, total=False):
    q: str
    final_response: str


def test_depth_rule():
    assert _is_subgraph_event({}) is False                      # 키 없음(테스트 대역) = 루트
    assert _is_subgraph_event({"parent_ids": []}) is False       # 루트 실행
    assert _is_subgraph_event({"parent_ids": ["r"]}) is False    # 루트 직속 노드
    assert _is_subgraph_event({"parent_ids": ["r", "n"]}) is True


def _graph():
    inner = StateGraph(_S)

    async def partial_answer(state):
        return {"final_response": "부분 답"}

    inner.add_node("partial_answer", partial_answer)
    inner.add_edge(START, "partial_answer")
    inner.add_edge("partial_answer", END)
    inner_c = inner.compile()

    async def task_run(state):
        await inner_c.ainvoke(state)
        return {"q": "done"}

    async def finalize(state):
        return {"final_response": "전체 답"}

    outer = StateGraph(_S)
    outer.add_node("task_run", task_run)
    outer.add_node("finalize", finalize)
    outer.add_edge(START, "task_run")
    outer.add_edge("task_run", "finalize")
    outer.add_edge("finalize", END)
    return outer.compile()


@pytest.mark.asyncio
async def test_first_root_level_final_response_is_the_real_answer():
    ends = []
    async for ev in _graph().astream_events({"q": "x"}, version="v2"):
        out = (ev.get("data") or {}).get("output")
        if ev["event"] == "on_chain_end" and isinstance(out, dict) and "final_response" in out:
            ends.append((ev["name"], out["final_response"], _is_subgraph_event(ev)))
    # 판정 없이 첫 이벤트를 잡으면 서브그래프의 부분 답으로 닫힌다(K-3 원형)
    assert ends[0] == ("partial_answer", "부분 답", True)
    assert next(e for e in ends if not e[2])[:2] == ("finalize", "전체 답")
