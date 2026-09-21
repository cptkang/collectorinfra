"""SSE 진행 신호 계약 — plans/89 T1~T3 · D-204 (`SPEC-sse-progress-contract.md`).

실 LLM 0 · 네트워크 0. `app.state.graph`에 Mock 그래프를 주입하고 SSE 바이트를 파싱한다
(`tests/e2e/conftest.py` MockGraph 형식). 두 스트림 라우트(`/query/stream`·`/query/file/stream`)를
같은 시나리오로 돌려 대칭을 고정한다(Known Mistakes 「단일/멀티 경로 대칭」).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user

# ---------------------------------------------------------------------------
# Mock 그래프 — 이벤트 목록 + 이벤트 간 지연 + 취소 관측
# ---------------------------------------------------------------------------


def _chain(name: str, output: dict | None = None) -> list[dict]:
    evs = [{"event": "on_chain_start", "name": name, "data": {}}]
    if output is not None:
        evs.append({"event": "on_chain_end", "name": name, "data": {"output": output}})
    return evs


def _final(response: str = "응답") -> dict:
    from langchain_core.messages import HumanMessage

    return {
        "event": "on_chain_end",
        "name": "LangGraph",
        "data": {"output": {
            "final_response": response,
            "generated_sql": "SELECT 1",
            "query_results": [{"hostname": "a"}],
            "messages": [HumanMessage(content="q")],
        }},
    }


class _Graph:
    def __init__(self, events: list[dict], *, delay_before: dict[int, float] | None = None):
        self.events = events
        self.delay_before = delay_before or {}
        self.cancelled = False
        self.started = False

    def get_state(self, config: dict) -> None:
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:  # 폴백 경로(미사용)
        return _final()["data"]["output"]

    async def astream_events(self, input_state: dict, config: dict, version: str = "v2") -> AsyncGenerator[dict, None]:
        self.started = True
        try:
            for i, ev in enumerate(self.events):
                if i in self.delay_before:
                    await asyncio.sleep(self.delay_before[i])
                yield ev
        except asyncio.CancelledError:
            self.cancelled = True
            raise


# ---------------------------------------------------------------------------
# 앱 조립
# ---------------------------------------------------------------------------


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


def _client(graph: _Graph, app_config) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = app_config
    app.state.graph = graph
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def _parse_sse(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def _post(client: TestClient, route: str) -> list[dict]:
    if route == "text":
        r = client.post("/api/v1/query/stream", json={"query": "테스트 질의"})
    else:
        r = client.post(
            "/api/v1/query/file/stream",
            data={"query": "테스트 질의"},
            files={"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")},
        )
    assert r.status_code == 200, r.text
    assert "text/event-stream" in r.headers.get("content-type", "")
    return _parse_sse(r.text)


ROUTES = ("text", "file")


@pytest.fixture(autouse=True)
def _reset_flags(app_config):
    app_config.server.sse_progress_events = True
    app_config.server.sse_heartbeat_interval_sec = 5
    app_config.server.query_timeout = 60
    app_config.server.file_query_timeout = 120
    yield


# ---------------------------------------------------------------------------
# T1 — 화이트리스트: deep_agent가 두 라우트 모두에서 나온다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_deep_agent_node_events_emitted(route, app_config):
    events = (
        _chain("context_resolver", {"turn": 1})
        + _chain("field_mapper")
        + _chain("deep_agent", {"final_response": "x", "current_node": "deep_agent"})
        + [_final()]
    )
    got = _post(_client(_Graph(events), app_config), route)
    starts = [e["node"] for e in got if e["type"] == "node_start"]
    assert "deep_agent" in starts, starts
    completes = [e for e in got if e["type"] == "node_complete" and e["node"] == "deep_agent"]
    assert completes and completes[0]["data"]["status"] == "에이전트 실행 완료"
    assert got[-1]["type"] == "done"


def test_known_nodes_constant_covers_ladder_nodes():
    from src.api.routes.query import _STREAM_KNOWN_NODES

    for n in ("deep_agent", "fault_diagnosis", "cache_management", "intent_planner", "schema_analyzer"):
        assert n in _STREAM_KNOWN_NODES


# ---------------------------------------------------------------------------
# T3 — 도구·커스텀 이벤트 → progress
# ---------------------------------------------------------------------------


_TOOL_EVENTS = (
    _chain("deep_agent")
    + [
        {"event": "on_tool_start", "name": "query_infra_db", "data": {"input": {"sub_query": "CPU 높은 서버"}}},
        {"event": "on_custom_event", "name": "task",
         "data": {"task_id": "t1", "order": 1, "phase": "start", "agent": "data_query", "sub_query": "CPU 높은 서버"}},
        {"event": "on_custom_event", "name": "task",
         "data": {"task_id": "t1", "order": 1, "phase": "end", "status": "completed", "row_count": 7}},
        {"event": "on_tool_end", "name": "query_infra_db", "data": {"output": "7 rows"}},
        {"event": "on_custom_event", "name": "agent.aggregate", "data": {"phase": "start", "label": "최종 합성"}},
    ]
    + [_final()]
)


@pytest.mark.parametrize("route", ROUTES)
def test_tool_and_custom_events_become_progress(route, app_config):
    got = _post(_client(_Graph(list(_TOOL_EVENTS)), app_config), route)
    prog = [e for e in got if e["type"] == "progress"]
    kinds = [(p["kind"], p["phase"], p["name"]) for p in prog]
    assert kinds == [
        ("tool", "start", "query_infra_db"),
        ("task", "start", "task"),
        ("task", "end", "task"),
        ("tool", "end", "query_infra_db"),
        ("step", "start", "agent.aggregate"),
    ], kinds
    # 도구 시작은 sub_query를 label로, task는 페이로드를 그대로(plans/88 verdict 필드명 동일)
    assert prog[0]["label"] == "CPU 높은 서버"
    assert prog[0]["node"] == "deep_agent"
    assert prog[2]["task"]["row_count"] == 7
    assert prog[4]["label"] == "최종 합성"
    # 진행 이벤트가 done 앞에 오고, 순서가 보존된다
    assert got[-1]["type"] == "done"


@pytest.mark.parametrize("route", ROUTES)
def test_flag_off_emits_no_progress_or_heartbeat(route, app_config):
    """sse_progress_events=false → progress·heartbeat 0건, 나머지 이벤트 열은 on과 동일."""
    on = _post(_client(_Graph(list(_TOOL_EVENTS)), app_config), route)
    app_config.server.sse_progress_events = False
    off = _post(_client(_Graph(list(_TOOL_EVENTS)), app_config), route)
    assert not [e for e in off if e["type"] in ("progress", "heartbeat")]
    strip = lambda evs: [  # noqa: E731
        {k: v for k, v in e.items() if k not in ("timestamp_ms", "processing_time_ms", "query_id", "thread_id")}
        for e in evs if e["type"] not in ("progress", "heartbeat")
    ]
    assert strip(on) == strip(off)


# ---------------------------------------------------------------------------
# T2 — heartbeat · 무이벤트 상한 · 생산자 취소
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_heartbeat_emitted_during_silence(route, app_config):
    app_config.server.sse_heartbeat_interval_sec = 0.1
    events = _chain("deep_agent") + [_final()]
    graph = _Graph(events, delay_before={1: 0.45})  # deep_agent 시작 뒤 0.45s 침묵
    got = _post(_client(graph, app_config), route)
    hbs = [e for e in got if e["type"] == "heartbeat"]
    assert len(hbs) >= 2, [e["type"] for e in got]
    # 마지막 활동 시점은 침묵 동안 고정(단조 증가하지 않는다), 경과는 증가
    assert hbs[-1]["elapsed_ms"] > hbs[0]["elapsed_ms"]
    assert abs(hbs[-1]["last_activity_ms"] - hbs[0]["last_activity_ms"]) < 60
    # 하트비트는 done 앞에 오고 이벤트 순서를 흐트러뜨리지 않는다
    assert got[-1]["type"] == "done"
    idx_hb = got.index(hbs[0])
    assert [e["type"] for e in got[:idx_hb]] == ["node_start"]


@pytest.mark.parametrize("route", ROUTES)
def test_idle_timeout_preserved_and_producer_cancelled(route, app_config):
    app_config.server.sse_heartbeat_interval_sec = 0.1
    app_config.server.query_timeout = 0.4
    app_config.server.file_query_timeout = 0.4
    events = _chain("deep_agent") + [_final()]
    graph = _Graph(events, delay_before={1: 5.0})
    got = _post(_client(graph, app_config), route)
    assert got[-1]["type"] == "error"
    assert "처리 시간이 초과되었습니다" in got[-1]["message"]
    assert not [e for e in got if e["type"] == "done"]
    # 무이벤트 상한 전까지는 heartbeat가 나갔다
    assert [e for e in got if e["type"] == "heartbeat"]
    # 생산자 태스크가 취소됐다(스트림 종료 후 좀비 태스크 없음)
    assert graph.started and graph.cancelled


@pytest.mark.parametrize("route", ROUTES)
def test_heartbeat_disabled_when_interval_zero(route, app_config):
    app_config.server.sse_heartbeat_interval_sec = 0
    events = _chain("deep_agent") + [_final()]
    got = _post(_client(_Graph(events, delay_before={1: 0.3}), app_config), route)
    assert not [e for e in got if e["type"] == "heartbeat"]
    assert got[-1]["type"] == "done"


# ---------------------------------------------------------------------------
# 단위 — 헬퍼 자체
# ---------------------------------------------------------------------------


def test_progress_payload_ignores_other_events():
    from src.api.routes.query import _progress_sse_payload

    assert _progress_sse_payload({"event": "on_chain_start", "name": "x"}, "deep_agent", 0.0) is None
    p = _progress_sse_payload({"event": "on_custom_event", "name": "step.x", "data": "not-a-dict"}, None, 0.0)
    assert p["kind"] == "step" and p["phase"] == "start" and p["node"] == ""


def test_graph_event_stream_reraises_producer_error():
    """생산자 예외는 소비자로 재전달된다 — 기존 AttributeError 폴백(ainvoke) 경로 유지."""
    from src.api.routes.query import _graph_event_stream

    class _Bad:
        async def astream_events(self, *a, **k):
            raise AttributeError("no astream")
            yield  # pragma: no cover

    async def run():
        gen = _graph_event_stream(_Bad(), {}, {}, idle_timeout=1, heartbeat_interval=0)
        with pytest.raises(AttributeError):
            async for _ in gen:
                pass

    asyncio.run(run())


# ---------------------------------------------------------------------------
# D-242 — 실패 경위: error 이벤트가 "어디서·어떻게" 끊겼는지를 싣는다
# (운영 실측 2026-09-21: 은행존 질의가 SQL 검증 실패 → 재생성 도중
#  60.6초에 끊겼는데
#  화면에는 "처리 시간이 초과되었습니다" 한 줄뿐이었다)
# ---------------------------------------------------------------------------


def _step(name: str, phase: str, **data: Any) -> dict:
    return {"event": "on_custom_event", "name": name, "data": {"phase": phase, **data}}


_RETRY_THEN_STALL = (
    _chain("deep_agent")
    + [
        {"event": "on_custom_event", "name": "task",
         "data": {"task_id": "t1", "order": 1, "sub_query": "은행존 메모리 평균 80% 초과",
                  "phase": "start", "status": "pending", "agent": "infra_db"}},
        _step("pipeline.schema", "start", label="스키마 분석"),
        _step("pipeline.schema", "end"),
        _step("pipeline.generate", "start", label="SQL 생성"),
        _step("pipeline.generate", "end"),
        _step("pipeline.validate", "start", label="SQL 검증"),
        _step("pipeline.validate", "end",
              detail="SQL 검증 실패: 삭제 리소스 제외 필터가 없습니다"),
        _step("pipeline.generate", "start", label="SQL 재생성 1회차"),
        _final(),   # 여기 앞에서 멈춘다(delay_before)
    ]
)


@pytest.mark.parametrize("route", ROUTES)
def test_timeout_error_carries_failure_trace(route, app_config):
    app_config.server.sse_heartbeat_interval_sec = 0.1
    app_config.server.query_timeout = 0.4
    app_config.server.file_query_timeout = 0.4
    events = list(_RETRY_THEN_STALL)
    graph = _Graph(events, delay_before={len(events) - 1: 5.0})
    got = _post(_client(graph, app_config), route)
    err = got[-1]
    assert err["type"] == "error"
    # 문구는 그대로(하네스가 문구로 분류) — 경위는 별도 필드
    assert err["message"].startswith("처리 시간이 초과되었습니다")
    assert err["code"] == "timeout" and err["limit_sec"] == 0.4
    assert err["elapsed_ms"] >= 400
    # 멈춘 곳: 가장 안쪽의 진행 중 단계
    assert err["stage"]["label"] == "SQL 재생성 1회차" and err["stage"]["status"] == "running"
    # 앞서 난 실패와 단계 목록
    assert "검증 실패" in err["last_error"]
    by_label = {(s["label"], s["status"]) for s in err["steps"]}
    assert ("스키마 분석", "done") in by_label and ("SQL 검증", "failed") in by_label
    assert [s["kind"] for s in err["steps"]][:2] == ["node", "task"]   # deep_agent → 작업
    assert err["steps_dropped"] == 0
    # 단계 실패 사유는 progress 이벤트에도 실린다
    val_end = [e for e in got
               if e.get("type") == "progress" and e.get("name") == "pipeline.validate"
               and e.get("phase") == "end"]
    assert val_end and "검증 실패" in val_end[0]["detail"]


@pytest.mark.parametrize("route", ROUTES)
def test_exception_error_carries_failure_trace(route, app_config):
    class _Boom(_Graph):
        async def astream_events(self, input_state, config, version="v2"):
            yield {"event": "on_chain_start", "name": "query_generator", "data": {}}
            raise RuntimeError("DB 연결 끊김")

    got = _post(_client(_Boom([]), app_config), route)
    err = got[-1]
    assert err["type"] == "error" and err["code"] == "exception"
    assert "DB 연결 끊김" in err["message"]
    assert err["stage"]["name"] == "query_generator"


def test_trace_node_retry_and_step_cap():
    """3단 경로: node_start SSE는 노드당 1회지만 경위는 재시도마다 남는다.

    노드 출력의 error_message는 그 단계의 실패로 본다.
    """
    from src.api.stream_failure import StreamTrace

    t = StreamTrace()
    t.node_started("query_generator", 0)
    t.node_ended("query_generator", {"generated_sql": "SELECT 1"}, 10)
    t.node_started("query_validator", 10)
    t.node_ended("query_validator", {"error_message": "SQL 검증 실패: X"}, 11)
    t.node_started("query_generator", 11)
    f = t.failure_fields(code="timeout", elapsed_ms=50, limit_sec=120)
    assert [(s["name"], s["status"]) for s in f["steps"]] == [
        ("query_generator", "done"), ("query_validator", "failed"), ("query_generator", "running")]
    assert f["stage"]["name"] == "query_generator" and f["stage"]["ms"] == 39
    assert f["last_error"] == "SQL 검증 실패: X"

    capped = StreamTrace()
    for i in range(40):
        capped.start("step", f"s{i}", i)
    f2 = capped.failure_fields(code="timeout", elapsed_ms=100, limit_sec=None)
    assert len(f2["steps"]) == 30 and f2["steps_dropped"] == 10 and f2["steps"][0]["name"] == "s10"
