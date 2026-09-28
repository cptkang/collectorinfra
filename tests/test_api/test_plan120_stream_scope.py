"""스트림 `done.db_scope` 전체 상태 기준 · 노드 완료 이벤트 (plans/120 S-1 · V-6).

run 20260923-140539(`ladder-1`)에서 3단 arm 111/111행의 `db_ids`가 비었다. 스트림은
`final_response`를 낸 노드(3단 `output_generator`)의 **델타**로 `build_db_scope`를 불렀고,
그 델타에는 대상 DB 키가 없다.
비스트림은 `ainvoke` 전체 상태를 쓰므로 값이 있었다 — D-205 "4경로 대칭"의 실제 결함이다.
같은 run에서 3단 `multi_db_executor`·`result_merger`는 `node_start`만 있고 `node_complete`가 0건이라
하네스의 노드 시간 합이 wall의 1/4까지 떨어졌다(B-03: wall 127.8초 · 노드 합 32.7초).

실 LLM 0 · 네트워크 0. 모의 그래프가 LangGraph `astream_events(v2)` 모양의 루트 직속 이벤트를 낸다.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import time
from collections.abc import AsyncGenerator, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.state import create_initial_state

_ROOT = ["run-root"]   # 루트 직속 노드 이벤트의 parent_ids(길이 1)


def _node(name: str, output: dict[str, Any], *, delay: float = 0.0) -> list[tuple[dict, float]]:
    """노드 시작·완료 이벤트 한 쌍. ``delay``는 완료 이벤트 전 대기(노드 소요)다."""
    return [
        ({"event": "on_chain_start", "name": name, "data": {}, "parent_ids": _ROOT}, 0.0),
        (
            {
                "event": "on_chain_end", "name": name,
                "data": {"output": output}, "parent_ids": _ROOT,
            },
            delay,
        ),
    ]


class _Graph:
    """astream_events·ainvoke·get_state만 흉내 낸다 — 두 경로가 같은 노드 델타를 쓴다."""

    def __init__(
        self, nodes: list[tuple[str, dict[str, Any], float]], *, checkpoint: dict | None = None
    ):
        self.nodes = nodes
        self.checkpoint = checkpoint

    def get_state(self, config: dict) -> Any:
        return SimpleNamespace(values=self.checkpoint) if self.checkpoint else None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        # LangGraph 병합 의미(값 채널 덮어쓰기) — 체크포인트 + 입력 + 노드 델타 순서
        state: dict[str, Any] = {**(self.checkpoint or {}), **input_state}
        for _name, output, _delay in self.nodes:
            state.update(output)
        return state

    async def astream_events(
        self, input_state: dict, config: dict, version: str = "v2"
    ) -> AsyncGenerator[dict, None]:
        for name, output, delay in self.nodes:
            for event, wait in _node(name, output, delay=delay):
                if wait:
                    await asyncio.sleep(wait)
                yield event


# 3단(semantic_router) 멀티 DB 경로 — 대상 DB는 라우터 델타에만 있다
_GP, _YD, _B0 = "polestar_cm_gp", "polestar_cm_yd", "polestar_b0"


def _tier3_nodes(
    *, multi_delay: float = 0.0, merge_delay: float = 0.0
) -> list[tuple[str, dict, float]]:
    rows = [{"server_name": "s1", "_source_db": _GP}, {"server_name": "s2", "_source_db": _YD}]
    return [
        ("context_resolver", {"conversation_context": None}, 0.0),
        ("input_parser", {"parsed_requirements": {"query_targets": ["서버"]}}, 0.0),
        ("field_mapper", {"current_node": "field_mapper"}, 0.0),
        ("semantic_router", {
            "routing_intent": "data_query",
            "target_databases": [{"db_id": _GP}, {"db_id": _YD}],
            "active_db_id": _GP,
            "is_multi_db": True,
            "db_scope_source": "classified",
        }, 0.0),
        (
            "multi_db_executor",
            {"db_results": {_GP: rows[:1], _YD: rows[1:]}, "query_results": rows},
            multi_delay,
        ),
        ("result_merger", {"query_results": rows, "db_result_summary": {}}, merge_delay),
        ("result_organizer", {"organized_data": {"summary": "2건", "rows": rows}}, 0.0),
        ("output_generator", {"final_response": "3단 응답"}, 0.0),
    ]


def _tier2_nodes(*, promote: bool) -> list[tuple[str, dict, float]]:
    final: dict[str, Any] = {"final_response": "2단 응답", "current_node": "result_aggregator"}
    if promote:
        final.update({
            "active_db_id": _GP, "target_databases": [{"db_id": _GP}], "db_scope_source": "hint",
        })
    return [
        ("context_resolver", {"current_node": "context_resolver"}, 0.0),
        ("input_parser", {"parsed_requirements": {"query_targets": ["서버"]}}, 0.0),
        ("field_mapper", {"current_node": "field_mapper"}, 0.0),
        ("intent_planner", {"task_plan": [{"task_id": "t1", "agent": "data_query"}]}, 0.0),
        ("agent_orchestrator", {"task_results": {"t1": {"target_db_ids": [_GP]}}}, 0.0),
        ("replanner", {"needs_replan": False}, 0.0),
        ("result_aggregator", final, 0.0),
    ]


@pytest.fixture(scope="module")
def app_config():
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, ServerConfig

    return AppConfig(
        db_backend="direct",
        db_connection_string="",
        log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=120),
    )


@pytest.fixture(autouse=True)
def _flags(app_config):
    app_config.server.sse_progress_events = True
    app_config.server.sse_heartbeat_interval_sec = 5
    yield


def _client(graph: _Graph, app_config) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = app_config
    app.state.graph = graph
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def _sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _stream(
    client: TestClient, route: str, *, thread_id: str | None = None
) -> tuple[list[dict], str]:
    if route == "text":
        body: dict[str, Any] = {"query": "테스트 질의"}
        if thread_id:
            body["thread_id"] = thread_id
        r = client.post("/api/v1/query/stream", json=body)
    else:
        data = {"query": "테스트 질의"}
        if thread_id:
            data["thread_id"] = thread_id
        r = client.post(
            "/api/v1/query/file/stream",
            data=data,
            files={"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")},
        )
    assert r.status_code == 200, r.text
    return _sse(r.text), r.text


def _nonstream_scope(client: TestClient, *, thread_id: str | None = None) -> dict:
    body: dict[str, Any] = {"query": "테스트 질의"}
    if thread_id:
        body["thread_id"] = thread_id
    r = client.post("/api/v1/query", json=body)
    assert r.status_code == 200, r.text
    return r.json()["db_scope"]


def _done(events: list[dict]) -> dict:
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1, [e["type"] for e in events]
    return done[0]


# ---------------------------------------------------------------------------
# S-1 — 스트림 db_scope = 누적 상태 = 비스트림
# ---------------------------------------------------------------------------


def test_delta_only_scope_was_empty_for_tier3():
    """결함 재현(수정 전 입력): 3단 종료 노드 델타만으로는 db_ids가 빈다."""
    from src.routing.db_scope import build_db_scope

    final_delta = _tier3_nodes()[-1][1]
    assert build_db_scope(final_delta)["db_ids"] == []


@pytest.mark.parametrize("route", ("text", "file"))
def test_tier3_stream_scope_matches_executed_dbs(route, app_config):
    from src.api.routes import query as query_routes

    client = _client(_Graph(_tier3_nodes()), app_config)
    events, _ = _stream(client, route)
    done = _done(events)
    assert done["db_scope"]["db_ids"] == [_GP, _YD]
    assert done["db_scope"]["source"] == "classified"
    # 저장 결과(`/query/{id}/result`)도 같은 값이다
    assert query_routes._results_store[done["query_id"]]["db_scope"] == done["db_scope"]


def test_tier3_stream_equals_nonstream(app_config):
    client = _client(_Graph(_tier3_nodes()), app_config)
    events, _ = _stream(client, "text")
    assert _done(events)["db_scope"] == _nonstream_scope(client)


@pytest.mark.parametrize("promote", (True, False))
def test_tier2_stream_equals_nonstream(promote, app_config):
    """2단 — 승격하는 턴은 종전에도 같았고, 승격하지 않는 턴은 두 경로 모두 빈다(대칭)."""
    client = _client(_Graph(_tier2_nodes(promote=promote)), app_config)
    events, _ = _stream(client, "text")
    scope = _done(events)["db_scope"]
    assert scope == _nonstream_scope(client)
    assert scope["db_ids"] == ([_GP] if promote else [])


def test_followup_turn_carries_checkpoint_like_nonstream(app_config):
    """후속 턴 — 이번 턴 노드가 DB 키를 안 내면 체크포인트 값이 남는다(ainvoke 전체 상태와 같다)."""
    checkpoint = create_initial_state(user_query="직전 질의", thread_id="th-1")
    checkpoint["target_databases"] = [{"db_id": _B0}]
    checkpoint["active_db_id"] = _B0
    client = _client(_Graph(_tier2_nodes(promote=False), checkpoint=dict(checkpoint)), app_config)
    events, _ = _stream(client, "text", thread_id="th-1")
    scope = _done(events)["db_scope"]
    assert scope["db_ids"] == [_B0]
    assert scope == _nonstream_scope(client, thread_id="th-1")


def test_followup_turn_router_overrides_checkpoint(app_config):
    checkpoint = create_initial_state(user_query="직전 질의", thread_id="th-2")
    checkpoint["target_databases"] = [{"db_id": _B0}]
    checkpoint["active_db_id"] = _B0
    client = _client(_Graph(_tier3_nodes(), checkpoint=dict(checkpoint)), app_config)
    events, _ = _stream(client, "text", thread_id="th-2")
    assert _done(events)["db_scope"]["db_ids"] == [_GP, _YD]


# ---------------------------------------------------------------------------
# V-6 — 진행 데이터가 없는 노드도 node_complete → 하네스 노드 합 ≈ wall
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._lines = text.splitlines()

    def iter_lines(self) -> Iterator[str]:
        yield from self._lines


@pytest.mark.parametrize("route", ("text", "file"))
def test_multi_db_nodes_emit_node_complete(route, app_config):
    client = _client(_Graph(_tier3_nodes()), app_config)
    events, _ = _stream(client, route)
    completes = [e for e in events if e["type"] == "node_complete"]
    names = [e["node"] for e in completes]
    for node in ("multi_db_executor", "result_merger"):
        assert node in names, names
    # 진행 데이터가 없는 노드는 빈 data 로 — 화면은 본문을 채우지 않는다(app.js handleNodeComplete)
    assert next(e for e in completes if e["node"] == "multi_db_executor")["data"] == {}


def test_runner_node_sum_close_to_wall_for_tier3_multi_db(app_config):
    """3단 멀티 DB 모의 턴 — 하네스 집계(`_consume_sse`)의 노드 합이 처리 시간에 붙는다."""
    from scripts.scenario.assertions import Observation
    from scripts.scenario.client import ClientConfig, ScenarioClient

    client = _client(_Graph(_tier3_nodes(multi_delay=0.3, merge_delay=0.2)), app_config)
    _, raw = _stream(client, "text")
    obs = Observation()
    runner = ScenarioClient(ClientConfig(port=1))
    try:
        runner._consume_sse(_FakeResponse(raw), obs, started=time.perf_counter())
    finally:
        runner.close()
    assert obs.node_elapsed_ms.get("multi_db_executor", 0) >= 250
    assert obs.node_elapsed_ms.get("result_merger", 0) >= 150
    total = sum(obs.node_elapsed_ms.values())
    assert obs.processing_time_ms and total >= 0.9 * obs.processing_time_ms, (
        total, obs.processing_time_ms, obs.node_elapsed_ms,
    )
