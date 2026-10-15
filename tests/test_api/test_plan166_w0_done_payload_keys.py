"""plans/166 W0 특성화 스냅샷 — 단계 2(plans/163)만 갱신 가능,
단계 3 이후 갱신 금지(갱신이 필요하면 리팩터가 아니다).

질의 라우트 네 진입점(`src/api/routes/query.py`)의 응답 **키 집합**을 고정한다(스멜 A1·A2·A3·A6).
기대값은 「맞는 값」이 아니라 「지금 값」이다(D-337 ⑤) — 진입점 사이 비대칭도 그대로 적는다.

- 스트림(`/query/stream` · `/query/file/stream`): 마지막 이벤트(`done` 또는 `error`)의 키 집합 +
  이벤트 `type` 순서(처음 나온 순서 · 중복 제거).
- 비스트림(`/query` · `/query/file`): HTTP 상태 + 응답 JSON 키 집합 + 값이 null이 아닌 키 집합.
- 값은 싣지 않는다(시각·uuid·소요 시간이 비결정이다).

경로: 정상(astream) · 폴백(`ainvoke` — 그래프에 `astream_events`가 없을 때) ·
처리 상한 초과(행 없음 → 오류/504 · 행 있음 → 부분 결과) · 그래프 예외(스트림 error · 500) ·
역질문(기간 되묻기 · 존 후단 게이트 · 존 사전 게이트 — 사다리 미확정/2단).
그래프는 이벤트 목록을 내는 대역이다(`tests/test_api/test_plan119_stream_timeouts.py`와 같은 모양).

스냅샷 갱신::

    UPDATE_PLAN162_SNAPSHOT=1 python -m pytest \
        tests/test_api/test_plan166_w0_done_payload_keys.py

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from src.api.dependencies import require_user
from src.llm import USER_RESPONSE_TAG

SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1] / "snapshots" / "plan166_w0" / "done_payload_keys.json"
)
UPDATE_ENV = "UPDATE_PLAN162_SNAPSHOT"

ROUTES = ("text", "file")
ROWS = [{"hostname": "kpo-web-01", "cpu": 91.5}]
TIME_CLARIFY = {"clarify": "unresolved_period"}
ZONE_CLARIFICATION = {
    "kind": "zone_select",
    "question": "어느 존을 볼까요?",
    "options": [{"id": "bankjon", "label": "은행존"}],
    "original_query": "서버 목록",
}
TIER2_LADDER = {
    "tier": "intent_orchestration", "degraded_reason": "none", "resolved_by": "explicit_env",
}


# ── 이벤트 대역 ──────────────────────────────────────────────────────────


def _end(name: str, output: dict | None = None) -> dict:
    return {"event": "on_chain_end", "name": name, "data": {"output": output or {}},
            "parent_ids": ["r"]}


def _start(name: str) -> dict:
    return {"event": "on_chain_start", "name": name, "data": {}, "parent_ids": ["r"]}


def _token(text: str) -> dict:
    return {
        "event": "on_chat_model_stream", "name": "llm",
        "tags": [USER_RESPONSE_TAG], "metadata": {"langgraph_node": "output_generator"},
        "data": {"chunk": type("C", (), {"content": text})()},
        "parent_ids": ["r", "n", "m"],
    }


def _final_output(response: str = "최종 응답", **extra: Any) -> dict:
    return {
        "final_response": response,
        "query_results": list(ROWS),
        "generated_sql": "SELECT 1",
        "messages": [HumanMessage(content="서버 목록")],
        **extra,
    }


def _final(response: str = "최종 응답", **extra: Any) -> dict:
    """루트 그래프 종료 이벤트(`parent_ids` 없음 = 루트)."""
    return {"event": "on_chain_end", "name": "LangGraph",
            "data": {"output": _final_output(response, **extra)}}


class _InvokeOnlyGraph:
    """`astream_events`가 없는 그래프 대역 — 스트림 라우트는 `ainvoke` 폴백 경로를 탄다."""

    def __init__(self, events: list[dict] | None = None, *,
                 delay_before: dict[int, float] | None = None, state: dict | None = None,
                 result: dict | None = None, invoke_delay: float = 0.0,
                 raise_exc: bool = False) -> None:
        self.events = events or []
        self.delay_before = delay_before or {}
        self.state = state
        self.result = result if result is not None else _final_output()
        self.invoke_delay = invoke_delay
        self.raise_exc = raise_exc

    def get_state(self, config: dict) -> Any:
        return SimpleNamespace(values=self.state) if self.state is not None else None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        if self.raise_exc:
            raise RuntimeError("가짜 그래프 예외")
        if self.invoke_delay:
            await asyncio.sleep(self.invoke_delay)
        return dict(self.result)


class _Graph(_InvokeOnlyGraph):
    """라우트가 쓰는 표면(`get_state`·`ainvoke`·`astream_events`)만 가진 그래프 대역."""

    async def astream_events(self, input_state: dict, config: dict,
                             version: str = "v2") -> AsyncGenerator[dict, None]:
        for i, ev in enumerate(self.events):
            if i in self.delay_before:
                await asyncio.sleep(self.delay_before[i])
            yield ev
        if self.raise_exc:
            raise RuntimeError("가짜 그래프 예외")


# ── 앱 조립 ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def _base_config():
    from src.config import AppConfig, ServerConfig

    return AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=60),
    )


@pytest.fixture
def app_config(_base_config):
    """키 집합에 닿는 설정을 매 테스트 명시한다 — `.env` 누수 차단(공통 규약)."""
    cfg = _base_config
    s = cfg.server
    s.query_timeout = 60
    s.file_query_timeout = 60
    s.sse_progress_events = True
    s.sse_heartbeat_interval_sec = 0          # heartbeat 이벤트 수가 시각에 따라 달라진다
    s.answer_reserve_sec = 15
    s.stream_idle_timeout_sec = 30
    s.stream_delivery_grace_sec = 60
    cfg.result_viz.chart_enabled = False
    cfg.result_viz.explain_enabled = False
    cfg.schema_cache.enabled = False
    # 존 게이트·범위 사전 선택 판정 재료 — 스냅샷을 채록한 값(로컬 `.env` 2026-10-14)으로 고정한다.
    # 활성 DB가 다르면 파일 경로 존 게이트가 갈린다(verifier 실측: 은행·공동존 3종이면 11건 상이).
    cfg.multi_db.active_db_ids_csv = "polestar,itam"
    cfg.composite.scope_select_enabled = True
    cfg.enable_deepagents_package = True
    cfg.enable_intent_orchestration = True
    cfg.enable_semantic_routing = True
    cfg.enable_sql_approval = False
    return cfg


@pytest.fixture(autouse=True)
def _ladder_unresolved(monkeypatch):
    """사다리 확정 기록은 프로세스 전역이다.

    사전 게이트 `plan_summary` 유무가 앞 테스트에 달리지 않게 미확정으로 둔다.
    """
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


def _stream_record(client: TestClient, route: str) -> dict:
    if route == "text":
        r = client.post("/api/v1/query/stream", json={"query": "서버 목록"})
    else:
        r = client.post("/api/v1/query/file/stream", data={"query": "서버 목록"}, files=_file())
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    types: list[str] = []
    for e in events:
        if e["type"] not in types:
            types.append(e["type"])
    last = events[-1] if events else {}
    return {
        "status_code": r.status_code,
        "event_types": types,
        "terminal_type": last.get("type"),
        "terminal_keys": sorted(last),
    }


def _post_record(client: TestClient, route: str) -> dict:
    if route == "text":
        r = client.post("/api/v1/query", json={"query": "서버 목록"})
    else:
        r = client.post("/api/v1/query/file", data={"query": "서버 목록"}, files=_file())
    body = r.json()
    assert isinstance(body, dict), body
    # `response_model`이 전 필드를 직렬화하므로 키 집합만으로는 라우트가 채운 칸을 못 가린다 —
    # 값이 null이 아닌 키 집합을 함께 적는다(값 자체는 싣지 않는다).
    return {
        "status_code": r.status_code,
        "keys": sorted(body),
        "non_null_keys": sorted(k for k, v in body.items() if v is not None),
    }


# ── 경로 정의 ────────────────────────────────────────────────────────────


def _set_limit(app_config, limit: float) -> None:
    app_config.server.query_timeout = limit
    app_config.server.file_query_timeout = limit


def _pre_gate(monkeypatch, *, tier2: bool) -> None:
    """존 사전 게이트를 발동시킨다 — 판정 함수는 레지스트리·인가에 달려 대역으로 바꾼다."""
    from src.api.routes import query as query_routes
    from src.observability import ladder

    monkeypatch.setattr(query_routes, "_zone_clarification_or_none",
                        lambda *a, **k: dict(ZONE_CLARIFICATION))
    monkeypatch.setattr(query_routes, "_file_zone_clarification_or_none",
                        lambda *a, **k: dict(ZONE_CLARIFICATION))
    if tier2:
        monkeypatch.setattr(ladder, "_resolution", dict(TIER2_LADDER))


def _stream_graph(case: str, app_config, monkeypatch) -> _InvokeOnlyGraph:
    if case == "normal":
        return _Graph([
            _start("input_parser"), _end("input_parser"),
            _start("query_executor"), _end("query_executor", {"query_results": list(ROWS)}),
            _start("output_generator"), _token("답"), _final("답"),
        ])
    if case == "fallback_ainvoke":
        return _InvokeOnlyGraph()
    if case == "timeout_no_rows":
        _set_limit(app_config, 0.4)
        return _Graph([_end("input_parser"), _start("query_generator"), _final()],
                      delay_before={2: 5.0})
    if case == "timeout_partial":
        _set_limit(app_config, 0.4)
        return _Graph([
            _start("query_executor"), _end("query_executor", {"query_results": list(ROWS)}),
            _start("output_generator"), _final(),
        ], delay_before={3: 5.0})
    if case == "time_clarification":
        # input_parser가 해석 불가로 그래프를 끝낸다 — 노드 종료 출력에 되묻기 문구가 실린다
        return _Graph([_start("input_parser"), _end("input_parser", {
            "final_response": "언제 기준으로 볼까요?", "time_resolution": dict(TIME_CLARIFY),
        })])
    if case == "zone_clarification_post":
        return _Graph([_start("output_generator"),
                       _final("어느 존을 볼까요?", zone_clarification=dict(ZONE_CLARIFICATION))])
    if case in ("zone_clarification_pre", "zone_clarification_pre_tier2"):
        _pre_gate(monkeypatch, tier2=case.endswith("tier2"))
        return _Graph([_final()])
    if case == "graph_exception":
        return _Graph([_start("input_parser")], raise_exc=True)
    raise AssertionError(case)


def _post_graph(case: str, app_config, monkeypatch) -> _InvokeOnlyGraph:
    if case == "normal":
        return _Graph()
    if case == "timeout_no_rows":
        _set_limit(app_config, 0.3)
        return _Graph(invoke_delay=5.0)
    if case == "timeout_partial":
        _set_limit(app_config, 0.3)
        return _Graph(invoke_delay=5.0, state={"query_results": list(ROWS)})
    if case == "time_clarification":
        return _Graph(result=_final_output(
            "언제 기준으로 볼까요?", time_resolution=dict(TIME_CLARIFY)))
    if case == "zone_clarification_post":
        return _Graph(result=_final_output("어느 존을 볼까요?",
                                           zone_clarification=dict(ZONE_CLARIFICATION)))
    if case in ("zone_clarification_pre", "zone_clarification_pre_tier2"):
        _pre_gate(monkeypatch, tier2=case.endswith("tier2"))
        return _Graph()
    if case == "graph_exception":
        return _Graph(raise_exc=True)
    raise AssertionError(case)


STREAM_CASES = (
    "normal", "fallback_ainvoke", "timeout_no_rows", "timeout_partial",
    "time_clarification", "zone_clarification_post",
    "zone_clarification_pre", "zone_clarification_pre_tier2", "graph_exception",
)
POST_CASES = (
    "normal", "timeout_no_rows", "timeout_partial",
    "time_clarification", "zone_clarification_post",
    "zone_clarification_pre", "zone_clarification_pre_tier2", "graph_exception",
)


def _cases() -> list[tuple[str, str, str]]:
    """(스냅샷 키 접두, route, case) 목록."""
    out = [("stream", route, case) for route in ROUTES for case in STREAM_CASES]
    out += [("post", route, case) for route in ROUTES for case in POST_CASES]
    return out


def _record(kind: str, route: str, case: str, app_config, monkeypatch) -> dict:
    build: Callable[..., _InvokeOnlyGraph] = _stream_graph if kind == "stream" else _post_graph
    graph = build(case, app_config, monkeypatch)
    client = _client(graph, app_config)
    return _stream_record(client, route) if kind == "stream" else _post_record(client, route)


# ── 스냅샷 대조 ──────────────────────────────────────────────────────────


def _load_snapshot() -> dict:
    assert SNAPSHOT_PATH.exists(), (
        f"스냅샷이 없다: {SNAPSHOT_PATH} — {UPDATE_ENV}=1로 최초 채록하라")
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def test_snapshot_covers_exactly_the_declared_cases() -> None:
    """선언한 경로 집합과 스냅샷 키 집합이 같다 — 경로를 몰래 빼거나 더하지 않는다."""
    if os.environ.get(UPDATE_ENV) == "1":
        pytest.skip("스냅샷 갱신 중")
    expected = {f"{kind}.{route}.{case}" for kind, route, case in _cases()}
    assert set(_load_snapshot()) == expected


def test_update_snapshot(app_config, monkeypatch) -> None:
    """`UPDATE_PLAN162_SNAPSHOT=1`일 때만 전 경로를 다시 채록해 파일을 쓴다."""
    if os.environ.get(UPDATE_ENV) != "1":
        pytest.skip(f"{UPDATE_ENV}=1일 때만 채록한다")
    current: dict[str, dict] = {}
    for kind, route, case in _cases():
        with monkeypatch.context() as mp:
            from src.observability import ladder

            mp.setattr(ladder, "_resolution", None)
            saved = (app_config.server.query_timeout, app_config.server.file_query_timeout)
            current[f"{kind}.{route}.{case}"] = _record(kind, route, case, app_config, mp)
            app_config.server.query_timeout, app_config.server.file_query_timeout = saved
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    pytest.skip(f"스냅샷을 갱신했다: {SNAPSHOT_PATH}")


@pytest.mark.parametrize(("kind", "route", "case"), _cases(),
                         ids=[f"{k}.{r}.{c}" for k, r, c in _cases()])
def test_payload_keys_match_snapshot(
    kind: str, route: str, case: str, app_config, monkeypatch
) -> None:
    if os.environ.get(UPDATE_ENV) == "1":
        pytest.skip("스냅샷 갱신 중")
    key = f"{kind}.{route}.{case}"
    expected = _load_snapshot()[key]
    got = _record(kind, route, case, app_config, monkeypatch)
    assert got == expected, (
        f"{key} 키 집합이 바뀌었다 — 리팩터는 응답 키를 바꾸지 않는다(D-337 ⑤)"
    )
