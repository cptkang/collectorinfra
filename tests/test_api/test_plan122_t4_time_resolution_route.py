"""plans/122 T-4 — 응답 계약에 `time_resolution` · 기간 되묻기 종료 응답 (D-309).

고정하는 계약:
① 네 진입점(비스트림·스트림 × 텍스트·파일)과 스트림 폴백(`ainvoke`)이 state `time_resolution`을
   **그대로**(`QueryTime.to_state()` 모양) 싣는다 — 하네스가 `done.time_resolution`으로 기준
   `[start, end)`를 읽는다. 스트림 astream은 종료 노드 델타가 아니라 누적 상태에서 싣는다
   (해석은 `input_parser` 델타에만 있다).
② 값이 없으면(플래그 off) 스트림 `done`에 키를 싣지 않는다 — 바이트 불변. 비스트림은 null.
③ 요청 스코프 — 직전 턴 값이 이번 턴 응답으로 새지 않는다(상태 생성 함수가 None으로 초기화).
④ `input_parser → END` 기간 되묻기 턴은 존 역질문과 같은 채널로 낸다 — status="clarification" ·
   `clarification`(kind `time_period` · 선택지 없음). 하네스 상태 유도(`_derive_status`)도 역질문.
⑤ (verify-122t D5) 후속 턴 되묻기 응답·결과 저장에 직전 턴 SQL·행 수·파일이 실리지 않는다 —
   후속 턴 입력은 승계용으로 그 키를 비우지 않아 체크포인트 값이 남는다. 모의 그래프(체크포인트
   병합 흉내)와 **실제 그래프**(`build_graph` + `MemorySaver`) 두 겹으로 고정한다.

실 LLM 0 · 네트워크 0. 모의 그래프가 `astream_events(v2)` 모양의 루트 직속 이벤트를 낸다
(`test_plan120_stream_scope.py`와 같은 방식).
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import AsyncGenerator
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.domain.query_time import resolve_query_time
from src.domain.time_spec import KST
from src.state import create_initial_state

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)
_ROOT = ["run-root"]
TR = resolve_query_time("최근 30일 CPU 상위 10대", NOW).to_state()
TR_CLARIFY = resolve_query_time("2월 30일 CPU 사용률", NOW).to_state()
CLARIFY_TEXT = "[조회 기간] 2월 30일은 없는 날짜입니다. 조회할 기간을 다시 알려 주세요."


class _Graph:
    """astream_events·ainvoke·get_state만 흉내 낸다 — 두 경로가 같은 노드 델타를 쓴다."""

    def __init__(self, nodes: list[tuple[str, dict[str, Any]]], *, checkpoint: dict | None = None):
        self.nodes = nodes
        self.checkpoint = checkpoint

    def get_state(self, config: dict) -> Any:
        return SimpleNamespace(values=self.checkpoint) if self.checkpoint else None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        state: dict[str, Any] = {**(self.checkpoint or {}), **input_state}
        for _name, output in self.nodes:
            state.update(output)
        return state

    async def astream_events(
        self, input_state: dict, config: dict, version: str = "v2"
    ) -> AsyncGenerator[dict, None]:
        for name, output in self.nodes:
            yield {"event": "on_chain_start", "name": name, "data": {}, "parent_ids": _ROOT}
            yield {
                "event": "on_chain_end", "name": name,
                "data": {"output": output}, "parent_ids": _ROOT,
            }


class _InvokeOnlyGraph(_Graph):
    """`astream_events`가 없는 그래프 — 스트림 라우트가 폴백(`ainvoke`)으로 간다."""

    def __getattribute__(self, name: str) -> Any:
        if name == "astream_events":
            raise AttributeError(name)
        return super().__getattribute__(name)


def _answer_nodes(time_resolution: dict | None) -> list[tuple[str, dict[str, Any]]]:
    parser_out: dict[str, Any] = {"parsed_requirements": {"query_targets": ["CPU"]}}
    if time_resolution is not None:
        parser_out["time_resolution"] = time_resolution
    return [
        ("context_resolver", {"current_node": "context_resolver"}),
        ("input_parser", parser_out),
        ("field_mapper", {"current_node": "field_mapper"}),
        ("output_generator", {"final_response": "응답 본문"}),
    ]


def _clarify_nodes() -> list[tuple[str, dict[str, Any]]]:
    """impl-t3 배선 — 해석 불가면 `input_parser`가 되묻기 문구를 싣고 그래프가 끝난다."""
    return [
        ("context_resolver", {"current_node": "context_resolver"}),
        ("input_parser", {
            "time_resolution": TR_CLARIFY, "final_response": CLARIFY_TEXT,
            "current_node": "input_parser",
        }),
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


def _file() -> dict[str, Any]:
    return {"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")}


def _call(client: TestClient, route: str, *, thread_id: str | None = None) -> dict[str, Any]:
    """route별 응답 본문 — 스트림은 `done` 이벤트."""
    body: dict[str, Any] = {"query": "테스트 질의"}
    if thread_id:
        body["thread_id"] = thread_id
    if route == "text":
        r = client.post("/api/v1/query", json=body)
    elif route == "text_stream":
        r = client.post("/api/v1/query/stream", json=body)
    elif route == "file":
        r = client.post("/api/v1/query/file", data=body, files=_file())
    else:
        r = client.post("/api/v1/query/file/stream", data=body, files=_file())
    assert r.status_code == 200, r.text
    if not route.endswith("_stream"):
        return r.json()
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1, [e["type"] for e in events]
    return done[0]


ROUTES = ("text", "text_stream", "file", "file_stream")
STREAMS = ("text_stream", "file_stream")


# ① 네 진입점 · 스트림 폴백

@pytest.mark.parametrize("route", ROUTES)
def test_four_entry_points_carry_state_value(route, app_config):
    out = _call(_client(_Graph(_answer_nodes(TR)), app_config), route)
    assert out["time_resolution"] == TR
    assert out["response"] == "응답 본문"


@pytest.mark.parametrize("route", STREAMS)
def test_stream_fallback_ainvoke_carries_state_value(route, app_config):
    graph = _InvokeOnlyGraph(_answer_nodes(TR))
    out = _call(_client(graph, app_config), route)
    assert out["time_resolution"] == TR


def test_stored_result_carries_same_value(app_config):
    from src.api.routes import query as query_routes

    done = _call(_client(_Graph(_answer_nodes(TR)), app_config), "text_stream")
    assert query_routes._results_store[done["query_id"]]["time_resolution"] == TR


# ② 플래그 off

@pytest.mark.parametrize("route", ROUTES)
def test_absent_value_omits_key_or_null(route, app_config):
    out = _call(_client(_Graph(_answer_nodes(None)), app_config), route)
    if route in STREAMS:
        assert "time_resolution" not in out
    else:
        assert out["time_resolution"] is None


# ③ 요청 스코프 — 직전 턴 값이 새지 않는다

@pytest.mark.parametrize("route", ("text", "text_stream"))
def test_previous_turn_value_does_not_leak(route, app_config):
    checkpoint = dict(create_initial_state(user_query="직전 질의", thread_id="th-t4"))
    checkpoint["time_resolution"] = TR
    graph = _Graph(_answer_nodes(None), checkpoint=checkpoint)
    out = _call(_client(graph, app_config), route, thread_id="th-t4")
    assert out.get("time_resolution") is None


@pytest.mark.parametrize("route", ("text", "text_stream"))
def test_this_turn_value_replaces_previous(route, app_config):
    checkpoint = dict(create_initial_state(user_query="직전 질의", thread_id="th-t4b"))
    checkpoint["time_resolution"] = TR_CLARIFY
    graph = _Graph(_answer_nodes(TR), checkpoint=checkpoint)
    out = _call(_client(graph, app_config), route, thread_id="th-t4b")
    assert out["time_resolution"] == TR
    assert not out.get("clarification")  # 직전 턴 되묻기가 이번 턴 상태를 바꾸지 않는다


# ④ 기간 되묻기 종료 턴

@pytest.mark.parametrize("route", ROUTES)
def test_clarify_end_is_clarification_response(route, app_config):
    from scripts.scenario.client import _derive_status

    out = _call(_client(_Graph(_clarify_nodes()), app_config), route)
    assert out["response"] == CLARIFY_TEXT
    clar = out["clarification"]
    assert clar["kind"] == "time_period"
    assert clar["question"] == CLARIFY_TEXT
    assert clar["options"] == [] and clar["reason"] == "invalid_date"
    assert out["time_resolution"] == TR_CLARIFY
    assert out.get("row_count") in (0, None) and not out.get("executed_sql")
    if route in STREAMS:
        assert _derive_status(out) == "clarification"  # 하네스가 역질문으로 읽는다
    else:
        assert out["status"] == "clarification"


@pytest.mark.parametrize("route", STREAMS)
def test_clarify_end_fallback_ainvoke(route, app_config):
    out = _call(_client(_InvokeOnlyGraph(_clarify_nodes()), app_config), route)
    assert out["clarification"]["kind"] == "time_period"


@pytest.mark.parametrize("route", ("file", "file_stream"))
def test_file_answer_without_clarify_keeps_completed_shape(route, app_config):
    """파일 경로 종전 모양 — 되묻기가 아니면 `clarification` 키가 없다(스트림 바이트 불변)."""
    out = _call(_client(_Graph(_answer_nodes(TR)), app_config), route)
    if route == "file":
        assert out["status"] == "completed" and out["clarification"] is None
    else:
        assert "clarification" not in out


def test_response_model_declares_time_resolution():
    from src.api.schemas import QueryResponse

    assert "time_resolution" in QueryResponse.model_fields
    dumped = QueryResponse(query_id="q", status="completed", response="r", time_resolution=TR)
    assert dumped.model_dump()["time_resolution"] == TR


# ⑤ 후속 턴 되묻기 — 직전 턴 산출물 미탑재(verify-122t D5)

STALE_SQL = "SELECT stale FROM polestar.cmm_resource"


def _stale_checkpoint(thread_id: str) -> dict[str, Any]:
    """직전 턴(조회·파일 산출 완료) 체크포인트 — 후속 턴 입력이 비우지 않는 키를 채운다."""
    checkpoint = dict(create_initial_state(user_query="지난달 CPU 상위 5대", thread_id=thread_id))
    checkpoint.update({
        "generated_sql": STALE_SQL, "query_results": [{"h": "a"}, {"h": "b"}],
        "output_file": b"old", "output_file_name": "old.xlsx", "mapping_report_md": "# old",
        "time_resolution": TR,
    })
    return checkpoint


def _events(client: TestClient, route: str, query: str, thread_id: str) -> list[dict[str, Any]]:
    body: dict[str, Any] = {"query": query, "thread_id": thread_id}
    if route == "text_stream":
        r = client.post("/api/v1/query/stream", json=body)
    else:
        r = client.post("/api/v1/query/file/stream", data=body, files=_file())
    assert r.status_code == 200, r.text
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def _assert_no_stale(out: dict[str, Any]) -> None:
    from src.api.routes import query as query_routes

    assert out["clarification"]["kind"] == "time_period"
    assert not out.get("executed_sql")
    assert out.get("row_count") in (0, None)
    assert not out.get("has_file") and out.get("file_name") is None
    assert not out.get("has_mapping_report")
    stored = query_routes._results_store[out["query_id"]]
    assert stored["query_results"] == [] and stored["output_file"] is None
    assert stored["mapping_report_md"] is None


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("graph_cls", [_Graph, _InvokeOnlyGraph], ids=["astream", "ainvoke"])
def test_followup_clarify_turn_drops_prior_turn_outputs(route, graph_cls, app_config):
    if graph_cls is _InvokeOnlyGraph and route not in STREAMS:
        pytest.skip("비스트림은 원래 ainvoke다 — astream 행이 같은 경로를 덮는다")
    thread = f"stale-{route}-{graph_cls.__name__}"
    graph = graph_cls(_clarify_nodes(), checkpoint=_stale_checkpoint(thread))
    out = _call(_client(graph, app_config), route, thread_id=thread)
    _assert_no_stale(out)


@pytest.mark.parametrize("route", STREAMS)
def test_followup_clarify_fallback_meta_event_has_no_prior_sql(route, app_config):
    thread = f"stale-meta-{route}"
    graph = _InvokeOnlyGraph(_clarify_nodes(), checkpoint=_stale_checkpoint(thread))
    events = _events(_client(graph, app_config), route, "13월 CPU 사용률", thread)
    meta = [e for e in events if e["type"] == "meta"]
    assert meta and not meta[0]["executed_sql"] and meta[0]["row_count"] == 0


@pytest.mark.parametrize("route", ("text", "text_stream"))
def test_followup_answer_turn_keeps_outputs_unchanged(route, app_config):
    """되묻기가 아닌 후속 턴은 종전 그대로 — 덮어쓰기가 정상 응답으로 번지지 않는다."""
    thread = f"keep-{route}"
    graph = _Graph(_answer_nodes(TR), checkpoint=_stale_checkpoint(thread))
    out = _call(_client(graph, app_config), route, thread_id=thread)
    assert not out.get("clarification")
    if route == "text":  # 비스트림은 ainvoke 전체 상태 — 종전 동작(승계 값) 그대로
        assert out["executed_sql"] == STALE_SQL and out["row_count"] == 2


# ⑤' 실제 그래프 2턴 — `build_graph` + `MemorySaver` + 실제 `input_parser`·되묻기 간선

class _ParserLLM:
    """입력 파싱 응답만 내는 가짜 LLM(되묻기 턴은 다른 LLM 호출이 없다)."""

    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, messages: Any, *a: Any, **k: Any) -> Any:
        from langchain_core.messages import AIMessage

        self.calls += 1
        return AIMessage(content=json.dumps({
            "query_targets": ["CPU"], "filter_conditions": [], "time_range": None,
            "output_format": "text", "aggregation": None, "limit": None,
        }))

    def with_config(self, *a: Any, **k: Any) -> _ParserLLM:
        return self

    def bind(self, *a: Any, **k: Any) -> _ParserLLM:
        return self


class _NoAstream:
    """실제 그래프의 `astream_events`만 가린다 — 스트림 라우트가 `ainvoke` 폴백으로 간다."""

    def __init__(self, compiled: Any) -> None:
        self._compiled = compiled

    def __getattr__(self, name: str) -> Any:
        if name == "astream_events":
            raise AttributeError(name)
        return getattr(self._compiled, name)


@pytest.fixture
def real_graph(monkeypatch):
    import importlib

    from langgraph.checkpoint.memory import MemorySaver

    import src.graph as graph_module
    from src.config import AppConfig

    monkeypatch.setattr(graph_module, "create_llm", lambda *a, **k: _ParserLLM())
    monkeypatch.setattr(importlib.import_module("src.nodes.input_parser"), "_now", lambda: NOW)
    cfg = AppConfig()
    cfg.enable_deepagents_package = False
    cfg.enable_intent_orchestration = False
    cfg.enable_semantic_routing = True
    cfg.tier3_plan_loop_enabled = False
    cfg.enable_sql_approval = False
    cfg.structured_output_backend = "none"
    cfg.query.time_resolution_enabled = True
    cfg.observability.trace_enabled = False
    cfg.server.sse_progress_events = True
    cfg.server.sse_heartbeat_interval_sec = 5
    return graph_module.build_graph(cfg, checkpointer=MemorySaver()), cfg


@pytest.mark.parametrize("route", ["text", "text_stream", "text_stream_fallback"])
def test_real_graph_followup_clarify_turn_has_no_stale_outputs(route, real_graph):
    """1턴(조회 완료 체크포인트) → 2턴 「13월 CPU 사용률」(같은 thread) — 실제 체크포인터 병합."""
    from src.api.routes import query as query_routes

    compiled, cfg = real_graph
    thread = f"real-stale-{route}"
    conf = {"configurable": {"thread_id": thread}}
    # 1턴 산출물 — 조회를 끝낸 턴의 체크포인트(출력 노드가 쓴 것처럼 기록한다)
    compiled.update_state(conf, _stale_checkpoint(thread), as_node="output_generator")
    assert compiled.get_state(conf).values["generated_sql"] == STALE_SQL

    graph = _NoAstream(compiled) if route == "text_stream_fallback" else compiled
    app = FastAPI()
    app.state.config = cfg
    app.state.graph = graph
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    client = TestClient(app)
    body = {"query": "13월 CPU 사용률", "thread_id": thread}
    if route == "text":
        r = client.post("/api/v1/query", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["status"] == "clarification"
    else:
        r = client.post("/api/v1/query/stream", json=body)
        assert r.status_code == 200, r.text
        events = [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ")]
        out = next(e for e in events if e["type"] == "done")
        meta = [e for e in events if e["type"] == "meta"]
        assert meta and not meta[0]["executed_sql"] and meta[0]["row_count"] == 0
    assert out["time_resolution"]["clarify"] == "invalid_date"
    _assert_no_stale(out)
    # 그래프는 조회하지 않았다 — 체크포인트의 승계 값은 그대로 남는다(지시어 승계 계약)
    assert compiled.get_state(conf).values["generated_sql"] == STALE_SQL

