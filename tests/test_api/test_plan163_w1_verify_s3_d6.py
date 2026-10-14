"""plans/163 W1 독립 검증(verifier) — S3 잔여 노출 경로 · D6 값 전달 · APM 사유 일관성.

처음에는 `xfail(strict=True)`로 남은 경로를 고정했고, 교정 라운드(S3+)에서 고친 뒤 일반 단언으로
바꿨다.
  ① progress step `detail` ② progress task `error` ③ node_complete agent_orchestrator
  `tasks[].error`·`db_errors` ④ node_complete query_executor `error`(3·4단)
  ⑤ 파일 경로 `source_switch` — 칩이 양식 파일 없이 텍스트로 재전송하므로 파일 경로는 키만 두고
    값은 항상 null이다(파일 경로는 소스 전환 미지원 · 팀 리드 판정)
  ⑥ APM 상태 조회(`_view`)도 409처럼 게이트웨이 사유를 숨긴다.

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from src.api.dependencies import require_user

SECRET = "postgresql://svc:pw@10.0.0.9:5432/polestar relation secret_tbl does not exist"
ROUTES = ("text", "file")
SWITCH = {
    "kind": "source_select",
    "question": "다른 데이터 소스로 다시 보려면 선택하세요.",
    "options": [{"id": "itam", "label": "자산관리"}],
    "original_query": "서버 목록",
    "correction": True,
    "areas": ["server"],
}


# ── 대역 ────────────────────────────────────────────────────────────────


def _start(name: str) -> dict:
    return {"event": "on_chain_start", "name": name, "data": {}, "parent_ids": ["r"]}


def _end(name: str, output: dict | None = None) -> dict:
    return {"event": "on_chain_end", "name": name, "data": {"output": output or {}},
            "parent_ids": ["r"]}


def _custom(name: str, data: dict) -> dict:
    return {"event": "on_custom_event", "name": name, "data": data, "parent_ids": ["r"]}


def _final_output(**extra: Any) -> dict:
    return {"final_response": "답", "query_results": [{"h": "a"}], "generated_sql": "SELECT 1",
            "messages": [HumanMessage(content="서버 목록")], **extra}


class _Graph:
    def __init__(self, events: list[dict], result: dict | None = None) -> None:
        self.events = events
        self.result = result if result is not None else _final_output()

    def get_state(self, config: dict) -> Any:
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        return dict(self.result)

    async def astream_events(self, input_state: dict, config: dict,
                             version: str = "v2") -> AsyncGenerator[dict, None]:
        for ev in self.events:
            yield ev


class _InvokeOnly:
    def __init__(self, result: dict) -> None:
        self.result = result

    def get_state(self, config: dict) -> Any:
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        return dict(self.result)


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


def _client(graph: Any, app_config) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = app_config
    app.state.graph = graph
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def _file() -> dict:
    return {"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")}


def _stream_raw(client: TestClient, route: str) -> str:
    if route == "text":
        r = client.post("/api/v1/query/stream", json={"query": "서버 목록"})
    else:
        r = client.post("/api/v1/query/file/stream", data={"query": "서버 목록"}, files=_file())
    assert r.status_code == 200, r.text
    return r.text


def _events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


# ── D6: 파일 경로 source_switch — 키는 있고 값은 항상 null ────────────────


def test_d6_file_post_source_switch_is_always_null(app_config) -> None:
    client = _client(_InvokeOnly(_final_output(source_switch=dict(SWITCH))), app_config)
    r = client.post("/api/v1/query/file", data={"query": "서버 목록"}, files=_file())
    assert r.status_code == 200, r.text
    body = r.json()
    assert "source_switch" in body and body["source_switch"] is None


@pytest.mark.parametrize("kind", ["astream", "fallback"])
def test_d6_file_stream_source_switch_is_always_null(app_config, kind) -> None:
    """그래프가 기억 칩을 내도 파일 경로 done은 null — 칩이 양식 없이 재질의하지 않게 한다."""
    out = _final_output(source_switch=dict(SWITCH))
    graph = (
        _Graph([_start("output_generator"),
                {"event": "on_chain_end", "name": "LangGraph", "data": {"output": out}}],
               result=out)
        if kind == "astream" else _InvokeOnly(out)
    )
    done = _events(_stream_raw(_client(graph, app_config), "file"))[-1]
    assert done["type"] == "done"
    assert "source_switch" in done and done["source_switch"] is None
    assert done["clarification"] is None


# ── S3 잔여: SSE 본문의 예외 원문 ───────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_s3_residual_progress_step_detail(app_config, route) -> None:
    graph = _Graph([
        _start("agent_orchestrator"),
        _custom("pipeline.execute", {"phase": "start"}),
        _custom("pipeline.execute", {"phase": "end", "detail": f"SQL 실행 에러: {SECRET}"}),
        _start("result_aggregator"),
        {"event": "on_chain_end", "name": "LangGraph", "data": {"output": _final_output()}},
    ])
    assert SECRET not in _stream_raw(_client(graph, app_config), route)


@pytest.mark.parametrize("route", ROUTES)
def test_s3_residual_progress_task_error(app_config, route) -> None:
    from src.orchestration.task_progress import build_task_payload

    task = {"task_id": "t1", "order": 1, "agent": "data_query", "status": "failed",
            "sub_query": "서버 목록"}
    payload = build_task_payload(task, "end", result={"error": SECRET})
    graph = _Graph([
        _start("agent_orchestrator"),
        _custom("task", {**build_task_payload(task, "start")}),
        _custom("task", payload),
        _start("result_aggregator"),
        {"event": "on_chain_end", "name": "LangGraph", "data": {"output": _final_output()}},
    ])
    assert SECRET not in _stream_raw(_client(graph, app_config), route)


@pytest.mark.parametrize("field", ["error", "db_errors"])
def test_s3_residual_orchestrator_node_complete(field) -> None:
    from src.api.routes.query import _extract_node_progress
    from src.config import SecurityConfig

    res = {"error": SECRET} if field == "error" else {"db_errors": {"polestar": SECRET}}
    data = _extract_node_progress(
        "agent_orchestrator",
        {"task_plan": [{"task_id": "t1", "order": 1, "agent": "data_query"}],
         "task_results": {"t1": res}},
        security=SecurityConfig(),
    )
    assert SECRET not in json.dumps(data, ensure_ascii=False)


def test_s3_residual_query_executor_node_complete() -> None:
    from src.api.routes.query import _extract_node_progress
    from src.config import SecurityConfig

    data = _extract_node_progress(
        "query_executor", {"query_results": [], "error_message": f"DB 연결 에러: {SECRET}"},
        security=SecurityConfig(),
    )
    assert SECRET not in json.dumps(data, ensure_ascii=False)


# ── S3 APM: 409는 숨기고 상태 조회는 낸다 ─────────────────────────────────


def test_s3_apm_view_hides_gateway_reason_like_409() -> None:
    from src.orchestration.apm_jobs import ApmJobService

    svc = object.__new__(ApmJobService)
    svc._masker = None  # rows 없음 — 마스킹 미사용
    job_id = "a" * 32
    view = svc._view(
        {"job_id": job_id, "state": "failed"},
        {"job": {"job_id": job_id, "state": "failed",
                 "error": {"code": "api_error", "reason": SECRET}}},
    )
    assert SECRET not in json.dumps(view, ensure_ascii=False, default=str)
