"""SSE 종료 판정 — 두 스트림 라우트가 **루트 직속 종료**에서만 `done`을 낸다 (plans/103 K-3 · P0-3).

`tests/test_api/test_query_stream_progress.py`의 대역 그래프는 손으로 만든 이벤트 목록이라
`parent_ids`가 없다 — 깊이 규칙을 검증하지 못한다. 여기서는 **실 LangGraph를 컴파일해**
라우트에 꽂고, 실제 이벤트의 `parent_ids`로 판정이 되는지 본다.

두 모양을 고정한다:
- 1단(deep_agent)형 — 노드가 안에서 다른 그래프를 돌리고 **자기 출력에** `final_response`를 담는다.
  깊이 1이므로 종전과 같이 그 노드에서 `done`이 나가야 한다(플래그 없이 들어간 변경이라 회귀 금지).
- 3단 계획 루프형 — `task_run` 서브그래프 **안**의 노드가 `final_response`를 내고, 루트의
  `finalize`가 최종 답을 낸다. 서브그래프의 부분 답으로 스트림이 닫히면 안 된다.

실 LLM 0 · 네트워크 0.
"""

from __future__ import annotations

import io
import json
import os
from typing import Any, TypedDict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from src.api.dependencies import require_user


class _S(TypedDict, total=False):
    user_query: str
    final_response: str
    query_results: list
    generated_sql: str
    messages: list


class _CountingGraph:
    """라우트가 쓰는 표면만 노출하고 폴백 `ainvoke` 호출을 센다."""

    def __init__(self, compiled: Any) -> None:
        self._compiled = compiled
        self.ainvoke_calls = 0

    def get_state(self, config: dict) -> None:
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        self.ainvoke_calls += 1
        return await self._compiled.ainvoke(input_state, config)

    def astream_events(self, input_state: dict, config: dict, version: str = "v2"):
        return self._compiled.astream_events(input_state, config, version=version)


def _inner_graph(output: dict) -> Any:
    inner = StateGraph(_S)

    async def leaf(state: _S) -> dict:
        return dict(output)

    inner.add_node("leaf", leaf)
    inner.add_edge(START, "leaf")
    inner.add_edge("leaf", END)
    return inner.compile()


def _tier1_graph() -> Any:
    """노드 안에서 내부 그래프를 돌리고 노드 자신이 최종 답을 내는 모양(1단 deep_agent)."""
    tool_loop = _inner_graph({"query_results": [{"hostname": "a"}]})

    async def deep_agent(state: _S) -> dict:
        await tool_loop.ainvoke(state)
        return {
            "final_response": "1단 최종 답",
            "generated_sql": "SELECT 1",
            "query_results": [{"hostname": "a"}],
            "messages": [HumanMessage(content=state.get("user_query", ""))],
        }

    g = StateGraph(_S)
    g.add_node("deep_agent", deep_agent)
    g.add_edge(START, "deep_agent")
    g.add_edge("deep_agent", END)
    return g.compile()


def _plan_loop_graph() -> Any:
    """서브그래프 안 노드가 부분 답을 내고, 루트 `finalize`가 최종 답을 내는 모양(3단 계획 루프)."""
    task = _inner_graph({"final_response": "task 부분 답"})

    async def task_run(state: _S) -> dict:
        await task.ainvoke(state)
        return {"query_results": [{"hostname": "a"}]}

    async def finalize(state: _S) -> dict:
        return {
            "final_response": "합성된 최종 답",
            "generated_sql": "SELECT 1",
            "query_results": [{"hostname": "a"}],
            "messages": [HumanMessage(content=state.get("user_query", ""))],
        }

    g = StateGraph(_S)
    g.add_node("task_run", task_run)
    g.add_node("finalize", finalize)
    g.add_edge(START, "task_run")
    g.add_edge("task_run", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


@pytest.fixture(scope="module")
def app_config():
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, ServerConfig

    return AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=120),
    )


def _client(graph: _CountingGraph, app_config) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = app_config
    app.state.graph = graph
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def _post(client: TestClient, route: str) -> list[dict]:
    if route == "text":
        r = client.post("/api/v1/query/stream", json={"query": "서버 목록"})
    else:
        r = client.post(
            "/api/v1/query/file/stream",
            data={"query": "서버 목록"},
            files={"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")},
        )
    assert r.status_code == 200, r.text
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


ROUTES = ("text", "file")


@pytest.mark.parametrize("route", ROUTES)
def test_tier1_shape_still_closes_at_its_node(route, app_config):
    """플래그 없이 들어간 변경이라 1단 모양은 종전 그대로여야 한다 — 폴백 재실행 0."""
    graph = _CountingGraph(_tier1_graph())
    events = _post(_client(graph, app_config), route)
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["response"] == "1단 최종 답"
    assert graph.ainvoke_calls == 0


@pytest.mark.parametrize("route", ROUTES)
def test_subgraph_partial_answer_does_not_close_the_stream(route, app_config):
    """서브그래프 안 `final_response`는 종료 판정에서 빠진다 — 최종 답은 루트 `finalize`의 것."""
    graph = _CountingGraph(_plan_loop_graph())
    events = _post(_client(graph, app_config), route)
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["response"] == "합성된 최종 답"
    tokens = "".join(e.get("content", "") for e in events if e["type"] == "token")
    assert "task 부분 답" not in tokens
    assert graph.ainvoke_calls == 0
