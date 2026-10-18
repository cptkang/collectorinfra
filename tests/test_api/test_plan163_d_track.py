"""plans/163 W1 D 트랙 — W0이 고정한 현행 동작 중 결함 판정분 교정(D5 · D6).

- D5: 그래프가 등록하는 `entity_locator`·`sequential_runner`도 SSE 진행 이벤트(node_start/
  node_complete)를 낸다 — 화이트리스트 `_STREAM_KNOWN_NODES`·화면 라벨 `nodeLabels`에 함께 둔다.
- D6: 경로 간 `done`·응답 형태 대칭(D-066 정신 — 단일/멀티·텍스트/파일 대칭):
  ① ainvoke 폴백 `done`에도 `awaiting_approval` ② 파일 스트림 `done`도 `clarification`·
  `source_switch` 키를 항상 싣는다(값 없으면 null) ③ 처리 상한 부분 결과 `done`이 정상 `done`의
  키를 모두 갖는다
  ④ 파일 사전 게이트(존 역질문·선택 거부) 응답의 `thread_id`를 텍스트 경로처럼 채운다.

D2(파일 경로가 그래프 출력 `zone_clarification`을 보지 않음)는 의도 판정(D-143 후속2 — 후단 게이트
채널은 텍스트 라우트만)이라 여기서 다루지 않는다.

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from src.api.dependencies import require_user

ROUTES = ("text", "file")
ROWS = [{"hostname": "kpo-web-01", "cpu": 91.5}]
ZONE_CLARIFICATION = {
    "kind": "zone_select",
    "question": "어느 존을 볼까요?",
    "options": [{"id": "bankjon", "label": "은행존"}],
    "original_query": "서버 목록",
}
APP_JS = Path(__file__).resolve().parents[2] / "src" / "static" / "js" / "app.js"


# ── 그래프 대역 ──────────────────────────────────────────────────────────


def _start(name: str) -> dict:
    return {"event": "on_chain_start", "name": name, "data": {}, "parent_ids": ["r"]}


def _end(name: str, output: dict | None = None) -> dict:
    return {"event": "on_chain_end", "name": name, "data": {"output": output or {}},
            "parent_ids": ["r"]}


def _final_output(response: str = "최종 응답", **extra: Any) -> dict:
    return {"final_response": response, "query_results": list(ROWS),
            "generated_sql": "SELECT 1", "messages": [HumanMessage(content="서버 목록")], **extra}


def _final(response: str = "최종 응답") -> dict:
    return {"event": "on_chain_end", "name": "LangGraph",
            "data": {"output": _final_output(response)}}


class _InvokeOnlyGraph:
    """`astream_events`가 없는 대역 — 스트림 라우트는 ainvoke 폴백을 탄다."""

    def __init__(self, events: list[dict] | None = None, *,
                 delay_before: dict[int, float] | None = None, state: dict | None = None,
                 result: dict | None = None) -> None:
        self.events = events or []
        self.delay_before = delay_before or {}
        self.state = state
        self.result = result if result is not None else _final_output()

    def get_state(self, config: dict) -> Any:
        return SimpleNamespace(values=self.state) if self.state is not None else None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        return dict(self.result)


class _Graph(_InvokeOnlyGraph):
    async def astream_events(self, input_state: dict, config: dict,
                             version: str = "v2") -> AsyncGenerator[dict, None]:
        for i, ev in enumerate(self.events):
            if i in self.delay_before:
                await asyncio.sleep(self.delay_before[i])
            yield ev


# ── 앱 조립 ──────────────────────────────────────────────────────────────


@pytest.fixture
def app_config():
    from src.config import AppConfig, ServerConfig

    cfg = AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=60),
    )
    s = cfg.server
    s.sse_progress_events = True
    s.sse_heartbeat_interval_sec = 0
    s.answer_reserve_sec = 15
    s.stream_idle_timeout_sec = 30
    s.stream_delivery_grace_sec = 60
    cfg.result_viz.chart_enabled = False
    cfg.result_viz.explain_enabled = False
    cfg.schema_cache.enabled = False
    cfg.multi_db.active_db_ids_csv = "polestar,itam"
    cfg.composite.scope_select_enabled = False
    cfg.enable_sql_approval = False
    return cfg


@pytest.fixture(autouse=True)
def _ladder_unresolved(monkeypatch):
    from src.observability import ladder

    monkeypatch.setattr(ladder, "_resolution", None)


def _client(graph: _InvokeOnlyGraph, app_config) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = app_config
    app.state.graph = graph
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def _file() -> dict:
    return {"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")}


def _stream(client: TestClient, route: str, thread_id: str | None = None) -> list[dict]:
    if route == "text":
        body: dict[str, Any] = {"query": "서버 목록"}
        if thread_id:
            body["thread_id"] = thread_id
        r = client.post("/api/v1/query/stream", json=body)
    else:
        data = {"query": "서버 목록", **({"thread_id": thread_id} if thread_id else {})}
        r = client.post("/api/v1/query/file/stream", data=data, files=_file())
    assert r.status_code == 200, r.text
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def _post(client: TestClient, route: str, thread_id: str | None = None) -> dict:
    if route == "text":
        body: dict[str, Any] = {"query": "서버 목록"}
        if thread_id:
            body["thread_id"] = thread_id
        r = client.post("/api/v1/query", json=body)
    else:
        data = {"query": "서버 목록", **({"thread_id": thread_id} if thread_id else {})}
        r = client.post("/api/v1/query/file", data=data, files=_file())
    assert r.status_code == 200, r.text
    return r.json()


def _normal_graph() -> _Graph:
    return _Graph([
        _start("input_parser"), _end("input_parser"),
        _start("query_executor"), _end("query_executor", {"query_results": list(ROWS)}),
        _start("output_generator"), _final("답"),
    ])


def _done_keys(events: list[dict]) -> set[str]:
    assert events[-1]["type"] == "done", events[-1]
    return set(events[-1])


# ── D5 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("node", ["entity_locator", "sequential_runner"])
def test_d5_registered_nodes_are_progress_whitelisted(node) -> None:
    from src.api.routes.query import _STREAM_KNOWN_NODES

    assert node in _STREAM_KNOWN_NODES
    labels = APP_JS.read_text(encoding="utf-8")
    labels = labels[labels.index("var nodeLabels = {"):]
    labels = labels[:labels.index("};")]
    assert re.search(rf"\b{node}:\s", labels), f"nodeLabels 누락: {node}"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("node", ["entity_locator", "sequential_runner"])
def test_d5_node_progress_events_are_emitted(app_config, route, node) -> None:
    graph = _Graph([_start(node), _end(node, {}), _start("output_generator"), _final("답")])
    events = _stream(_client(graph, app_config), route)
    assert node in [e.get("node") for e in events if e["type"] == "node_start"]
    assert node in [e.get("node") for e in events if e["type"] == "node_complete"]


# ── D6 ───────────────────────────────────────────────────────────────────


def test_d6_done_key_set_is_same_across_routes_and_fallback(app_config) -> None:
    """텍스트·파일 × astream·ainvoke 폴백 네 경로의 정상 `done` 키 집합이 같다(① · ②)."""
    got = {
        (route, kind): _done_keys(_stream(_client(graph, app_config), route))
        for route in ROUTES
        for kind, graph in (("astream", _normal_graph()), ("fallback", _InvokeOnlyGraph()))
    }
    reference = got[("text", "astream")]
    assert {"awaiting_approval", "clarification", "source_switch"} <= reference
    for key, keys in got.items():
        assert keys == reference, (key, sorted(reference ^ keys))


@pytest.mark.parametrize("route", ROUTES)
def test_d6_fallback_done_reports_awaiting_approval(app_config, route) -> None:
    done = _stream(_client(_InvokeOnlyGraph(), app_config), route)[-1]
    assert done["awaiting_approval"] is False


@pytest.mark.parametrize("route", ROUTES)
def test_d6_partial_timeout_done_has_normal_done_keys(app_config, route) -> None:
    """처리 상한 부분 결과 `done`은 정상 `done`의 키를 모두 갖는다(③ — 값 없으면 null)."""
    normal = _done_keys(_stream(_client(_normal_graph(), app_config), route))
    app_config.server.query_timeout = 0.4
    app_config.server.file_query_timeout = 0.4
    graph = _Graph([
        _start("query_executor"), _end("query_executor", {"query_results": list(ROWS)}),
        _start("output_generator"), _final(),
    ], delay_before={3: 5.0})
    events = _stream(_client(graph, app_config), route)
    done = events[-1]
    assert done["type"] == "done" and done["status"] == "partial"
    assert normal <= set(done), sorted(normal - set(done))
    assert done["awaiting_approval"] is False and done["has_file"] is False
    assert done["clarification"] is None and done["db_scope"] is None


def _pre_gate(monkeypatch) -> None:
    from src.api.routes import query as query_routes

    monkeypatch.setattr(query_routes, "_file_zone_clarification_or_none",
                        lambda *a, **k: dict(ZONE_CLARIFICATION))
    monkeypatch.setattr(query_routes, "_zone_clarification_or_none",
                        lambda *a, **k: dict(ZONE_CLARIFICATION))


@pytest.mark.parametrize("route", ROUTES)
def test_d6_pre_gate_thread_id_is_filled_like_text(app_config, monkeypatch, route) -> None:
    """사전 게이트 응답도 thread_id를 채운다 — 요청에 없으면 query_id, 있으면 그 값(④)."""
    _pre_gate(monkeypatch)
    client = _client(_Graph([_final()]), app_config)

    body = _post(client, route)
    assert body["status"] == "clarification"
    assert body["thread_id"] == body["query_id"]
    assert _post(client, route, thread_id="th-given")["thread_id"] == "th-given"

    done = _stream(client, route)[-1]
    assert done["type"] == "done" and done["clarification"]
    assert done["thread_id"] == done["query_id"]
    assert _stream(client, route, thread_id="th-given")[-1]["thread_id"] == "th-given"


@pytest.mark.parametrize("route", ROUTES)
def test_d6_selection_denied_thread_id_is_filled_like_text(app_config, monkeypatch, route) -> None:
    from src.api.routes import query as query_routes

    monkeypatch.setattr(query_routes, "apply_selection_authorization",
                        lambda ids, user: (ids, True))
    client = _client(_Graph([_final()]), app_config)
    body = _post(client, route)
    assert body["thread_id"] == body["query_id"]
    done = _stream(client, route)[-1]
    assert done["thread_id"] == done["query_id"]


@pytest.mark.parametrize("stream", [False, True])
def test_d6_file_audit_session_matches_response_thread(app_config, monkeypatch, stream) -> None:
    """verifier #10 — 파일 경로 감사도 텍스트처럼 `thread_id or query_id`를 쓴다."""
    from src.api.routes import query as query_routes

    seen: list[Any] = []

    async def fake_audit(request, current_user, **kw) -> None:
        seen.append(kw["thread_id"])

    monkeypatch.setattr(query_routes, "_audit_user_request", fake_audit)
    _pre_gate(monkeypatch)
    client = _client(_Graph([_final()]), app_config)
    for given in (None, "th-given"):
        seen.clear()
        if stream:
            done = _stream(client, "file", thread_id=given)[-1]
        else:
            done = _post(client, "file", thread_id=given)
        assert seen == [done["thread_id"]]
        assert done["thread_id"] == (given or done["query_id"])


def test_d6_file_main_path_uses_request_query_id(app_config, monkeypatch) -> None:
    """그래프를 탄 파일 질의도 감사 세션 = 응답 thread_id다(값을 두 번 만들지 않는다)."""
    from src.api.routes import query as query_routes

    seen: list[Any] = []

    async def fake_audit(request, current_user, **kw) -> None:
        seen.append(kw["thread_id"])

    monkeypatch.setattr(query_routes, "_audit_user_request", fake_audit)
    client = _client(_normal_graph(), app_config)
    body = _post(client, "file")
    assert seen == [body["thread_id"]] and body["thread_id"] == body["query_id"]
    seen.clear()
    done = _stream(client, "file")[-1]
    assert seen == [done["thread_id"]] and done["thread_id"] == done["query_id"]
