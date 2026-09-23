"""「실행된 SQL 보기」 — 스트림 `done.executed_sql`이 비지 않는다 (plans/116 §10.3).

스트림 라우트는 `final_response`를 낸 첫 루트 직속 노드 출력에서 닫힌다. 그 노드(2단
`result_aggregator` · 3단 `output_generator`)의 출력에는 SQL이 없으므로, 앞 노드 출력에서
누적한 값으로 채워야 한다. 2단은 top-level `generated_sql`이 빈 문자열이고 SQL은
`task_results`에만 있다(녹화 `scripts/manual/fixtures/queries/*.json` 실측) — 비스트림
(`ainvoke`) 경로도 같은 이유로 비었다.

실 LLM 0 · 네트워크 0 — 실 LangGraph를 컴파일해 라우트에 꽂는다.
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
from src.api.routes.query import _executed_sql


class _S(TypedDict, total=False):
    user_query: str
    final_response: str
    query_results: list
    generated_sql: str
    task_plan: list
    task_results: dict
    messages: list


class _Graph:
    def __init__(self, compiled: Any, *, stream: bool = True) -> None:
        self._compiled = compiled
        if stream:
            self.astream_events = lambda s, c, version="v2": compiled.astream_events(
                s, c, version=version
            )

    def get_state(self, config: dict) -> None:
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        return await self._compiled.ainvoke(input_state, config)


def _tier3_graph() -> Any:
    """3단 모양 — query_generator가 SQL을 내고 output_generator가 SQL 없이 최종 답을 낸다."""

    async def query_generator(state: _S) -> dict:
        return {"generated_sql": "SELECT hostname FROM t"}

    async def output_generator(state: _S) -> dict:
        return {
            "final_response": "3단 답",
            "messages": [HumanMessage(content=state.get("user_query", ""))],
        }

    g = StateGraph(_S)
    g.add_node("query_generator", query_generator)
    g.add_node("output_generator", output_generator)
    g.add_edge(START, "query_generator")
    g.add_edge("query_generator", "output_generator")
    g.add_edge("output_generator", END)
    return g.compile()


def _tier2_graph() -> Any:
    """2단 모양 — SQL은 task_results에만 있고 top-level generated_sql은 빈 문자열."""

    async def intent_planner(state: _S) -> dict:
        return {"task_plan": [
            {"task_id": "t2", "order": 2, "agent": "data_query"},
            {"task_id": "t1", "order": 1, "agent": "data_query"},
            {"task_id": "t3", "order": 3, "agent": "general"},
        ]}

    async def agent_orchestrator(state: _S) -> dict:
        return {"task_results": {
            "t2": {"generated_sql": "SELECT 2"},
            "t1": {"generated_sql": "SELECT 1"},
            "t3": {"final_response": "일반 답"},
        }}

    async def result_aggregator(state: _S) -> dict:
        return {
            "final_response": "2단 답",
            "generated_sql": "",
            "messages": [HumanMessage(content=state.get("user_query", ""))],
        }

    g = StateGraph(_S)
    for name, fn in (("intent_planner", intent_planner), ("agent_orchestrator", agent_orchestrator),
                     ("result_aggregator", result_aggregator)):
        g.add_node(name, fn)
    g.add_edge(START, "intent_planner")
    g.add_edge("intent_planner", "agent_orchestrator")
    g.add_edge("agent_orchestrator", "result_aggregator")
    g.add_edge("result_aggregator", END)
    return g.compile()


@pytest.fixture(scope="module")
def app_config():
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, ServerConfig

    return AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=120),
    )


def _client(graph: _Graph, app_config) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = app_config
    app.state.graph = graph
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def _stream(client: TestClient, route: str) -> dict:
    if route == "text":
        r = client.post("/api/v1/query/stream", json={"query": "서버 목록"})
    else:
        r = client.post(
            "/api/v1/query/file/stream",
            data={"query": "서버 목록"},
            files={"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")},
        )
    assert r.status_code == 200, r.text
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    done = [e for e in events if e["type"] == "done"]
    meta = [e for e in events if e["type"] == "meta"]
    assert len(done) == 1 and len(meta) == 1, events
    assert meta[0]["executed_sql"] == done[0]["executed_sql"]
    return done[0]


_TIER2_SQL = "-- [t1]\nSELECT 1\n\n-- [t2]\nSELECT 2"
ROUTES = ("text", "file")


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("stream", (True, False), ids=("astream_events", "ainvoke_fallback"))
def test_tier3_stream_done_carries_sql(route, stream, app_config):
    done = _stream(_client(_Graph(_tier3_graph(), stream=stream), app_config), route)
    assert done["executed_sql"] == "SELECT hostname FROM t"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("stream", (True, False), ids=("astream_events", "ainvoke_fallback"))
def test_tier2_stream_done_carries_task_sql_in_plan_order(route, stream, app_config):
    done = _stream(_client(_Graph(_tier2_graph(), stream=stream), app_config), route)
    assert done["executed_sql"] == _TIER2_SQL


def test_tier2_non_stream_query_carries_task_sql(app_config):
    client = _client(_Graph(_tier2_graph()), app_config)
    r = client.post("/api/v1/query", json={"query": "서버 목록"})
    assert r.status_code == 200, r.text
    assert r.json()["executed_sql"] == _TIER2_SQL


def test_executed_sql_sources():
    both = {"generated_sql": "SELECT 9", "task_results": {"t1": {"generated_sql": "X"}}}
    assert _executed_sql(both) == "SELECT 9"
    assert _executed_sql({"task_results": {"t1": {"generated_sql": "SELECT 1"}}}) == "SELECT 1"
    assert _executed_sql({"db_executed_sqls": {"a": "SELECT a", "b": ""}}) == "SELECT a"
    assert _executed_sql({"generated_sql": "", "task_results": {"t1": {}}}) is None
    assert _executed_sql({}) is None
