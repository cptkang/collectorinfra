"""plans/163 W1 S3 — 사용자 경로 오류 응답: 예외 원문 비노출 · 요청 번호 · 원문은 서버 로그.

- 감사 미들웨어가 응답 헤더 `X-Request-ID`에 요청 번호를 싣는다(StreamingResponse 포함).
- 질의 네 진입점(`/query`·`/query/file`·두 스트림)의 그래프 예외 · APM 작업 API 게이트웨이
  오류(502) · 대화 조회 실패(500) → 응답에는 고정 문구 + 「(요청 {번호})」, 원문은 로그.
  APM 결과 받기 불가(409 사유)는 `test_plan134_w0b_verify.py`가 단언한다.
- SSE error 이벤트의 실패 경위(`last_error`·`steps[].detail`)에도 원문을 싣지 않는다(S3+).
- 2단 기준 경로 답 본문(`result_aggregator._finalize_task`)도 같다(S3+).

거부 경로를 단언한다(secure-coding 절차 3). 실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from src.api.dependencies import require_user

#: 예외 원문 대역 — DSN·SQL·내부 주소가 섞인 문자열(응답에 나오면 안 된다).
SECRET = "postgresql://svc:pw-9x@10.1.2.3/pdb · SELECT secret_col FROM t_hidden"
ROUTES = ("text", "file")


# ── 앱 조립 ──────────────────────────────────────────────────────────────


class _Graph:
    """라우트가 쓰는 표면만 가진 그래프 대역 — `raise_exc`면 실행 중 예외를 낸다."""

    def __init__(self, events: list[dict] | None = None, *, raise_exc: bool = False,
                 state_exc: bool = False) -> None:
        self.events = events or []
        self.raise_exc = raise_exc
        self.state_exc = state_exc

    def get_state(self, config: dict) -> Any:
        if self.state_exc:
            raise RuntimeError(SECRET)
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        if self.raise_exc:
            raise RuntimeError(SECRET)
        return {"final_response": "응답", "query_results": [],
                "messages": [HumanMessage(content="q")]}

    async def astream_events(self, input_state: dict, config: dict,
                             version: str = "v2") -> AsyncGenerator[dict, None]:
        for ev in self.events:
            yield ev
        if self.raise_exc:
            raise RuntimeError(SECRET)
        yield {"event": "on_chain_end", "name": "LangGraph", "data": {"output": {
            "final_response": "응답", "query_results": [],
            "messages": [HumanMessage(content="q")]}}}


@pytest.fixture(scope="module")
def app_config():
    from src.config import AppConfig, ServerConfig

    cfg = AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=60),
    )
    cfg.server.sse_heartbeat_interval_sec = 0
    cfg.result_viz.chart_enabled = False
    cfg.result_viz.explain_enabled = False
    cfg.schema_cache.enabled = False
    cfg.multi_db.active_db_ids_csv = "polestar,itam"
    cfg.composite.scope_select_enabled = False
    return cfg


@pytest.fixture(autouse=True)
def _quiet_middleware(monkeypatch):
    """감사 미들웨어의 트레이스 수집을 끈다(파일 덤프 0) — 요청 번호 부여만 본다."""
    from src.api.middleware import audit_middleware
    from src.config import ObservabilityConfig

    monkeypatch.setattr(audit_middleware, "_observability_config",
                        lambda: ObservabilityConfig(trace_enabled=False))


def _client(graph: _Graph, app_config) -> TestClient:
    from src.api.middleware.audit_middleware import AuditMiddleware
    from src.api.routes import conversation as conversation_routes
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = app_config
    app.state.graph = graph
    app.add_middleware(AuditMiddleware)
    app.include_router(query_routes.router, prefix="/api/v1")
    app.include_router(conversation_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def _file() -> dict:
    return {"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")}


def _post(client: TestClient, route: str, *, stream: bool):
    path = "/api/v1/query" + ("/file" if route == "file" else "") + ("/stream" if stream else "")
    if route == "text":
        return client.post(path, json={"query": "서버 목록"})
    return client.post(path, data={"query": "서버 목록"}, files=_file())


def _events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


# ── 요청 번호 헤더 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("stream", [False, True])
def test_response_carries_request_id_header(app_config, stream) -> None:
    client = _client(_Graph(), app_config)
    first = _post(client, "text", stream=stream)
    second = _post(client, "text", stream=stream)
    assert first.status_code == 200
    rid = first.headers.get("X-Request-ID")
    assert rid and len(rid) == 8
    assert second.headers.get("X-Request-ID") not in (None, rid), "요청마다 새 번호"


# ── 질의 네 진입점 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_nonstream_500_hides_exception_text(app_config, route, caplog) -> None:
    with caplog.at_level(logging.ERROR):
        r = _post(_client(_Graph(raise_exc=True), app_config), route, stream=False)
    assert r.status_code == 500
    rid = r.headers["X-Request-ID"]
    assert SECRET not in r.text
    assert r.json()["detail"] == f"처리 중 오류가 발생했습니다 (요청 {rid})"
    assert SECRET in caplog.text and rid in caplog.text, "원문은 요청 번호와 함께 서버 로그로"


@pytest.mark.parametrize("route", ROUTES)
def test_stream_error_hides_exception_text(app_config, route, caplog) -> None:
    graph = _Graph([{"event": "on_chain_start", "name": "query_generator", "data": {}}],
                   raise_exc=True)
    with caplog.at_level(logging.ERROR):
        r = _post(_client(graph, app_config), route, stream=True)
    rid = r.headers["X-Request-ID"]
    err = _events(r.text)[-1]
    assert err["type"] == "error" and err["code"] == "exception"
    assert SECRET not in r.text
    assert err["message"] == f"처리 중 오류가 발생했습니다 (요청 {rid})"
    assert err["stage"]["name"] == "query_generator", "비밀 아닌 경위(단계 이름)는 유지"
    assert SECRET in caplog.text and rid in caplog.text


@pytest.mark.parametrize("route", ROUTES)
def test_stream_failure_trace_hides_step_error_text(app_config, route, caplog) -> None:
    """노드 출력 `error_message`(DB 오류 원문 등)는 error 이벤트의 경위에 원문으로 싣지 않는다."""
    graph = _Graph([
        {"event": "on_chain_start", "name": "query_validator", "data": {}},
        {"event": "on_chain_end", "name": "query_validator",
         "data": {"output": {"error_message": SECRET}}},
        {"event": "on_chain_start", "name": "query_generator", "data": {}},
    ], raise_exc=True)
    with caplog.at_level(logging.WARNING):
        r = _post(_client(graph, app_config), route, stream=True)
    err = _events(r.text)[-1]
    assert err["type"] == "error"
    assert SECRET not in json.dumps(err, ensure_ascii=False)
    failed = [s for s in err["steps"] if s["status"] == "failed"]
    assert [s["name"] for s in failed] == ["query_validator"], "실패 표시는 유지"
    assert failed[0]["detail"] and err["last_error"]
    assert "query_validator" in err["last_error"], "비밀 아닌 단계 이름은 남긴다"
    assert SECRET in caplog.text, "원문은 서버 로그로"


def test_stream_trace_unit_keeps_status_drops_text(caplog) -> None:
    from src.api.stream_failure import StreamTrace

    t = StreamTrace()
    t.node_started("query_validator", 0)
    with caplog.at_level(logging.WARNING):
        t.node_ended("query_validator", {"error_message": SECRET}, 5)
        t.observe_progress({"kind": "step", "name": "pipeline.execute", "phase": "end",
                            "timestamp_ms": 6, "detail": SECRET})
        t.observe_progress({"kind": "task", "phase": "end", "timestamp_ms": 7,
                            "task": {"task_id": "t1", "status": "skipped",
                                     "reason": "선행 결과 없음"}})
    f = t.failure_fields(code="timeout", elapsed_ms=10, limit_sec=60)
    assert SECRET not in json.dumps(f, ensure_ascii=False)
    assert [(s["name"], s["status"]) for s in f["steps"]] == [
        ("query_validator", "failed"), ("pipeline.execute", "failed"), ("t1", "skipped")]
    assert f["steps"][2]["detail"] == "선행 결과 없음", "건너뜀 사유(실패 원문 아님)는 유지"
    assert "pipeline.execute" in f["last_error"]
    assert caplog.text.count(SECRET) == 2


# ── 대화 조회 ────────────────────────────────────────────────────────────


def test_conversation_500_hides_exception_text(app_config, caplog) -> None:
    with caplog.at_level(logging.ERROR):
        r = _client(_Graph(state_exc=True), app_config).get("/api/v1/conversation/th-1")
    assert r.status_code == 500
    rid = r.headers["X-Request-ID"]
    assert SECRET not in r.text
    assert r.json()["detail"] == f"대화 기록을 불러오지 못했습니다 (요청 {rid})"
    assert SECRET in caplog.text


# ── APM 작업 API ─────────────────────────────────────────────────────────


def test_apm_gateway_error_502_hides_exception_text(caplog) -> None:
    from src.api.routes.apm_jobs import _http_error
    from src.orchestration.apm_jobs import JobUnavailableError

    req = SimpleNamespace(state=SimpleNamespace(request_id="ab12cd34"))
    with caplog.at_level(logging.ERROR):
        exc = _http_error(JobUnavailableError(SECRET), req)
    assert exc.status_code == 502
    assert SECRET not in exc.detail
    assert exc.detail == "작업 정보를 가져오지 못했습니다 (요청 ab12cd34)"
    assert SECRET in caplog.text and "ab12cd34" in caplog.text


@pytest.mark.parametrize("cls_name", ["JobNotFoundError", "JobForbiddenError"])
def test_apm_domain_errors_keep_safe_message(cls_name) -> None:
    """도메인 예외는 안전 문구만 담는다 — 메시지를 그대로 낸다(대상 아님 · W0 확인)."""
    from src.api.routes.apm_jobs import _http_error
    from src.orchestration import apm_jobs

    exc = _http_error(getattr(apm_jobs, cls_name)("작업을 찾을 수 없습니다."), None)
    assert exc.detail == "작업을 찾을 수 없습니다."


# ── 2단 답 본문 (result_aggregator) ──────────────────────────────────────


@pytest.fixture
def bound_rid():
    structlog.contextvars.bind_contextvars(request_id="feed0123")
    yield "feed0123"
    structlog.contextvars.unbind_contextvars("request_id")


@pytest.mark.asyncio
async def test_aggregator_text_task_error_hides_raw(bound_rid, caplog) -> None:
    from src.orchestration.result_aggregator import _finalize_task
    from src.state import create_initial_state

    task = {"task_id": "t1", "order": 1, "agent": "general_inference"}
    with caplog.at_level(logging.WARNING):
        out = await _finalize_task(task, {"error": SECRET}, create_initial_state("q"),
                                   llm=AsyncMock(), app_config=SimpleNamespace())
    assert SECRET not in out["text"]
    assert out["text"] == f"작업 처리 중 오류가 발생했습니다 (요청 {bound_rid})"
    assert out["error"], "실패 여부(task 요약)는 유지"
    assert SECRET in caplog.text and bound_rid in caplog.text


@pytest.mark.asyncio
async def test_aggregator_output_generator_exception_hides_raw(bound_rid, caplog) -> None:
    from src.orchestration.result_aggregator import _finalize_task
    from src.state import create_initial_state

    agg_mod = sys.modules["src.orchestration.result_aggregator"]

    async def boom(*a, **k):
        raise RuntimeError(SECRET)

    task = {"task_id": "t1", "order": 1, "agent": "data_query", "sub_query": "q"}
    res = {"organized_data": {"rows": [{"hostname": "a"}], "summary": ""}}
    with patch.object(agg_mod, "output_generator", new=boom), caplog.at_level(logging.ERROR):
        out = await _finalize_task(task, res, create_initial_state("q"),
                                   llm=AsyncMock(), app_config=SimpleNamespace())
    assert SECRET not in out["text"] and SECRET not in str(out["error"])
    assert out["text"] == f"결과 생성 중 오류가 발생했습니다 (요청 {bound_rid})"
    assert out["error"], "실패 여부는 유지"
    assert SECRET in caplog.text and bound_rid in caplog.text


# ── S3+ 교정: 고정 도메인 사유는 살리고 예외 원문만 가린다 ──────────────────


@pytest.mark.parametrize(("raw", "shown"), [
    ("모든 DB 쿼리가 실패했습니다.", "모든 DB 쿼리가 실패했습니다."),
    ("data_insufficient", "data_insufficient"),
    ("쿼리 타임아웃 초과. 쿼리를 최적화해주세요.", "쿼리 타임아웃 초과. 쿼리를 최적화해주세요."),
    (f"SQL 검증 실패: 존재하지 않는 테이블 {SECRET}", "SQL 검증 실패"),
    (f"SQL 실행 에러: {SECRET}", "SQL 실행 에러"),
    (f"DB 연결 에러: {SECRET}. DB 연결 상태를 확인해주세요.", "DB 연결 에러"),
    (f"DB 스키마 조회 실패: {SECRET}", "DB 스키마 조회 실패"),
    (SECRET, None),
    (f"SQL 실행 에러가 아닌 {SECRET}", None),  # 머리말은 「머리말:」 꼴만 인정
])
def test_safe_error_text_is_allowlist(raw, shown) -> None:
    from src.utils.user_error import GENERIC_TASK_ERROR, safe_error_text, user_error_text

    assert safe_error_text(raw) == shown
    assert user_error_text(raw) == (shown or GENERIC_TASK_ERROR)


def test_user_error_text_keeps_handler_authored_message() -> None:
    """처리기가 지은 사용자 문구(거부 `degraded_reason` · 미실행 `skipped`)는 그대로 보인다."""
    from src.utils.user_error import GENERIC_TASK_ERROR, user_error_text

    msg = "제니퍼가 연결되어 있지 않아 조회하지 않았습니다."
    refusal = {"error": msg, "degraded_reason": "source_unavailable"}
    assert user_error_text(msg, result=refusal) == msg
    skip = "1단계 조건에 해당하는 서버가 0대라 실행하지 않았습니다."
    assert user_error_text(skip, result={"error": skip, "skipped": True}) == skip
    # 표지 없는 결과(예외 정규화 `{"error": str(exc)}`)는 가린다
    assert user_error_text(SECRET, result={"error": SECRET}) == GENERIC_TASK_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize(("raw", "text"), [
    ("모든 DB 쿼리가 실패했습니다.",
     "작업 처리 중 오류가 발생했습니다: 모든 DB 쿼리가 실패했습니다."),
    (f"SQL 검증 실패: {SECRET}", "작업 처리 중 오류가 발생했습니다: SQL 검증 실패"),
])
async def test_aggregator_text_task_keeps_fixed_domain_reason(bound_rid, raw, text) -> None:
    """verifier #7 — 고정 도메인 사유는 답 본문에 남고, 보간된 원문은 머리말만 남는다."""
    from src.orchestration.result_aggregator import _finalize_task
    from src.state import create_initial_state

    task = {"task_id": "t1", "order": 1, "agent": "general_inference"}
    out = await _finalize_task(task, {"error": raw}, create_initial_state("q"),
                               llm=AsyncMock(), app_config=SimpleNamespace())
    assert out["text"] == text
    assert SECRET not in out["text"] and SECRET not in str(out["error"])


def test_aggregator_partial_failure_list_hides_raw() -> None:
    """「일부 작업이 실패했습니다」 목록과 합성 입력이 쓰는 task `error`도 원문이 아니다."""
    from src.orchestration.result_aggregator import _merge_finalized
    from src.utils.user_error import user_error_text

    finalized = [
        {"order": 1, "text": "답 1", "error": None},
        {"order": 2, "text": "", "error": user_error_text(SECRET)},
        {"order": 3, "text": "", "error": user_error_text("모든 DB 쿼리가 실패했습니다.")},
    ]
    body = _merge_finalized(finalized)["final_response"]
    assert "일부 작업이 실패했습니다" in body
    assert SECRET not in body
    assert "- 작업 2: 작업 처리 중 오류가 발생했습니다" in body
    assert "- 작업 3: 모든 DB 쿼리가 실패했습니다." in body


@pytest.mark.parametrize(("error", "kept"), [
    (f"SQL 실행 에러: {SECRET}", "(마지막 사유: SQL 실행 에러)"),
    ("모든 DB 쿼리가 실패했습니다.", "(마지막 사유: 모든 DB 쿼리가 실패했습니다.)"),
    (SECRET, None),
])
def test_failure_disclosure_detail_keeps_only_fixed_reason(error, kept) -> None:
    from src.orchestration.result_aggregator import _failure_disclosure

    d = _failure_disclosure({"task_id": "t1", "sub_query": "서버 목록"}, {"error": error})
    assert d is not None and SECRET not in d["text"]
    if kept:
        assert kept in d["text"]
    else:
        assert "마지막 사유" not in d["text"]


def test_failure_disclosure_kind_uses_raw_reason() -> None:
    """kind 판정(읽기 전용 차단)은 원문으로 한다 — 사유를 가려도 차단 문구는 그대로다."""
    from src.domain import disclosure as disc
    from src.orchestration.result_aggregator import _failure_disclosure

    res = {"error": "SQL 검증 실패: 금지된 키워드가 포함되어 있습니다"}
    d = _failure_disclosure({"task_id": "t1"}, res)
    assert d is not None and d["kind"] == disc.SQL_BLOCKED


# ── S3+ 교정: 캐시 관리 답 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_management_exception_hides_raw(bound_rid, caplog) -> None:
    from src.nodes import cache_management as cm
    from src.state import create_initial_state

    async def boom(*a, **k):
        raise RuntimeError(SECRET)

    state = create_initial_state("캐시 갱신해 줘")
    with patch.object(cm, "_parse_cache_intent", new=boom), \
            patch.object(cm, "get_cache_manager", return_value=object()), \
            caplog.at_level(logging.ERROR):
        out = await cm.cache_management(state, llm=AsyncMock(), app_config=SimpleNamespace())
    assert SECRET not in out["final_response"]
    assert out["final_response"] == f"캐시 관리 중 오류가 발생했습니다 (요청 {bound_rid})"
    assert SECRET in caplog.text and bound_rid in caplog.text


# ── S3+ 교정: APM 작업 상태 조회 ─────────────────────────────────────────


def _apm_service():
    from src.orchestration.apm_jobs import ApmJobService

    svc = object.__new__(ApmJobService)
    svc._masker = None
    return svc


@pytest.mark.parametrize(("code", "shown"), [
    ("cancelled", "취소 요청으로 끝났습니다."),
    ("interrupted", "게이트웨이가 다시 시작돼 작업이 중단됐습니다 — 다시 요청해야 합니다."),
    ("api_error", "작업이 실패했습니다 — 사유는 서버 로그에 남겼습니다(작업 aaaaaaaa)."),
])
def test_apm_view_replaces_gateway_reason(code, shown) -> None:
    job_id = "a" * 32
    view = _apm_service()._view(
        {"job_id": job_id, "state": "failed"},
        {"job": {"job_id": job_id, "state": "failed", "error": {"code": code, "reason": SECRET}}},
    )
    assert view["error"] == {"code": code, "reason": shown}
    # 장부 캐시만으로 그리는 목록(`list_jobs`)도 같다
    listed = _apm_service()._view(
        {"job_id": job_id, "state": "failed", "error": {"code": code, "reason": SECRET}}, None)
    assert listed["error"]["reason"] == shown


@pytest.mark.asyncio
async def test_apm_refresh_logs_gateway_reason_once(caplog) -> None:
    """원문은 장부 값이 바뀔 때 한 번만 로그로 — 카드 폴링마다 쌓이지 않는다."""
    svc = _apm_service()
    svc._store = SimpleNamespace(update=AsyncMock())
    job_id = "b" * 32
    error = {"code": "api_error", "reason": SECRET}
    envelope = {"job": {"job_id": job_id, "state": "failed", "error": error}}
    with caplog.at_level(logging.WARNING):
        await svc._refresh({"job_id": job_id, "error": None}, envelope)
        await svc._refresh({"job_id": job_id, "error": dict(error)}, envelope)
    assert caplog.text.count(SECRET) == 1


# ── S3+ 교정 2차: 답 본문·단계 배지에 남던 원문 경로 ─────────────────────────


@pytest.mark.asyncio
async def test_apm_gateway_down_answer_hides_exception(monkeypatch, bound_rid, caplog) -> None:
    """제니퍼 게이트웨이 연결 실패 답은 예외 원문 대신 요청 번호를 싣고 원문은 로그에 둔다."""
    from contextlib import asynccontextmanager

    from src.orchestration import apm_query as aq
    from tests.test_orchestration.test_plan134_w5_apm import _ctx, _isolated, _run, _tx

    @asynccontextmanager
    async def down(url, headers):
        raise aq.SourceMcpError(SECRET)
        yield  # pragma: no cover

    monkeypatch.setattr(aq, "_SESSION_FACTORY", down)
    with caplog.at_level(logging.WARNING):
        res = await _run(["apm.profile"], _isolated(ctx=_ctx([_tx(1)])))
    assert res["degraded_reason"] == "source_unavailable"
    assert SECRET not in res["final_response"]
    assert f"연결하지 못해 조회하지 않았습니다(요청 {bound_rid})" in res["final_response"]
    assert SECRET in caplog.text and bound_rid in caplog.text


@pytest.mark.parametrize(("last_reason", "tail"), [
    (f"SQL 실행 에러: {SECRET}", " 마지막 실패 사유: SQL 실행 에러"),
    (f"SQL 검증 실패: {SECRET}", " 마지막 실패 사유: SQL 검증 실패"),
    (SECRET, ""),
    (None, ""),
])
def test_deadline_stop_message_keeps_only_safe_reason(last_reason, tail) -> None:
    from src.nodes.query_validator import deadline_stop_message

    message = deadline_stop_message(
        "SQL 재생성", remaining_sec=10.0, need_sec=20.0, last_reason=last_reason,
    )
    assert SECRET not in message
    assert message.endswith(f"직전 SQL 생성 20초).{tail}")


@pytest.mark.parametrize(("reason", "shown"), [
    (f"SQL 파싱 에러: {SECRET}", "SQL 파싱 에러"),
    (f"LLM 백엔드 예외 응답(비-SQL) | 응답 원문: '{SECRET}'", "LLM 백엔드 예외 응답(비-SQL)"),
    ("존재하지 않는 테이블 참조: t_x", "존재하지 않는 테이블 참조: t_x"),  # 코드가 지은 검증 사유
    ("검증 통과", "검증 통과"),
])
def test_query_validator_node_complete_reason(reason, shown) -> None:
    """3·4단 단계 배지의 검증 사유 — 파서 예외·백엔드 응답 원문은 떼고 보인다."""
    from src.api.routes.query import _extract_node_progress
    from src.config import SecurityConfig

    data = _extract_node_progress(
        "query_validator", {"validation_result": {"passed": False, "reason": reason}},
        security=SecurityConfig(),
    )
    assert data == {"passed": False, "reason": shown}


# ── S3+ 교정 3차: DB별 사유 — 예외 보간부만 떼고 산출 발췌(D-153 후속2)는 남긴다 ──────

_EXCERPT = " | LLM 산출 발췌: 'blocked by the filter'"
_DEADLINE_MSG = "답변할 시간을 남기려고 SQL 재생성을(를) 하지 않았습니다(조회 마감 경과)."


@pytest.mark.parametrize(("raw", "shown"), [
    (f"DB 'gp' 실행 에러: {SECRET}", "DB 'gp' 실행 에러"),
    (f"DB 'gp' 실행 에러: {SECRET} | 재생성 1회 후 재실패: {SECRET}",
     "DB 'gp' 실행 에러 | 재생성 1회 후 재실패"),
    (f"DB 'gp' 실행 에러: {SECRET} | 재생성 1회 시도했으나 검증 실패: SQL 파싱 에러: {SECRET}",
     "DB 'gp' 실행 에러 | 재생성 1회 시도했으나 검증 실패: SQL 파싱 에러"),
    (f"DB 'gp' 실행 에러: {SECRET} | {_DEADLINE_MSG}", f"DB 'gp' 실행 에러 | {_DEADLINE_MSG}"),
    # 비-SQL 산출 발췌·코드 원인 문구는 남는다(D-153 후속2)
    (f"SQL 검증 실패: FabriX PII 필터 차단 응답{_EXCERPT}",
     f"SQL 검증 실패: FabriX PII 필터 차단 응답{_EXCERPT}"),
    (f"SQL 검증 실패: LLM 백엔드 예외 응답(비-SQL) | 응답 원문: '{SECRET}'",
     "SQL 검증 실패: LLM 백엔드 예외 응답(비-SQL)"),
    ("답변할 시간을 남기려고 스키마 분석을(를) 하지 않았습니다(조회 마감 경과).",
     "답변할 시간을 남기려고 스키마 분석을(를) 하지 않았습니다(조회 마감 경과)."),
    ("DB 'x'이(가) 레지스트리에 등록되지 않았습니다.",
     "DB 'x'이(가) 레지스트리에 등록되지 않았습니다."),
    ("DB 'x'에서 테이블을 찾을 수 없습니다.", "DB 'x'에서 테이블을 찾을 수 없습니다."),
    (SECRET, "작업 처리 중 오류가 발생했습니다"),
    # 여러 작업의 같은 DB 사유(`_merged_zone_state`의 「 / 」)는 건마다 판별한다
    (f"DB 'b0' 실행 에러: {SECRET} / SQL 검증 실패: SELECT 문이 아닙니다{_EXCERPT}",
     f"DB 'b0' 실행 에러 / SQL 검증 실패: SELECT 문이 아닙니다{_EXCERPT}"),
])
def test_db_error_text_strips_only_exception(raw, shown) -> None:
    from src.utils.user_error import db_error_text

    assert db_error_text(raw) == shown
    assert db_error_text(shown) == shown, "판별은 두 번 걸어도 같다(집계기 병합 뒤 재판별)"


def test_db_error_text_selection_none_head_matches_source() -> None:
    """테이블 선별 0개 안내의 시작 문구가 정본 상수와 어긋나면 안내가 고정 문구로 바뀐다."""
    from src.nodes.table_selection import SELECTION_NONE_MESSAGE
    from src.utils.user_error import db_error_text

    guidance = f"{SELECTION_NONE_MESSAGE}(예: 서버 사양)"
    assert db_error_text(guidance) == guidance


def test_db_failure_summary_keeps_head_and_judges_each_line() -> None:
    """전 DB 실패 요약(`result_merger`)은 머리를 살리고 DB별 줄만 판별한다."""
    from src.utils.user_error import safe_error_text, user_error_text

    raw = (f"모든 DB 쿼리가 실패했습니다:\n[은행존] DB 'b0' 실행 에러: {SECRET}\n  DETAIL: x\n"
           f"[공동존] SQL 검증 실패: SELECT 문이 아닙니다{_EXCERPT}")
    expected = ("모든 DB 쿼리가 실패했습니다:\n[은행존] DB 'b0' 실행 에러\n"
                f"[공동존] SQL 검증 실패: SELECT 문이 아닙니다{_EXCERPT}")
    assert safe_error_text(raw) == expected
    assert user_error_text(raw) == expected


def test_zone_coverage_footnote_keeps_excerpt_hides_exception() -> None:
    from src.nodes.output_generator import _append_zone_coverage_notes

    out = _append_zone_coverage_notes("답", {"db_errors": {
        "polestar_b0": f"DB 'polestar_b0' 실행 에러: {SECRET}",
        "polestar_cm_gp": f"SQL 검증 실패: FabriX PII 필터 차단 응답{_EXCERPT}",
    }})
    assert SECRET not in out
    assert "- polestar_b0: DB 'polestar_b0' 실행 에러" in out
    assert f"- polestar_cm_gp: SQL 검증 실패: FabriX PII 필터 차단 응답{_EXCERPT}" in out


def test_task_card_db_errors_keep_excerpt_hide_exception() -> None:
    from src.api.routes.query import _summarize_tasks

    items = _summarize_tasks(
        [{"task_id": "t1", "order": 1, "agent": "data_query"}],
        {"t1": {"db_errors": {"b0": f"DB 'b0' 실행 에러: {SECRET}",
                              "gp": f"SQL 검증 실패: SELECT 문이 아닙니다{_EXCERPT}"}}},
    )
    assert items[0]["db_errors"] == {"b0": "DB 'b0' 실행 에러",
                                     "gp": f"SQL 검증 실패: SELECT 문이 아닙니다{_EXCERPT}"}


@pytest.mark.asyncio
async def test_apm_all_calls_failed_answer_hides_reason(monkeypatch, bound_rid, caplog) -> None:
    """제니퍼 호출이 모두 실패한 답은 대상·오류 코드와 요청 번호만 싣고 사유 원문은 로그에 둔다."""
    from tests.test_orchestration import test_plan134_w2_verify as w2

    gw = w2._Gateway({**w2._ALL_REPLIES,
                      "apm_app_health": w2._err("apm_app_health", "apm_api_error", SECRET)})
    monkeypatch.setattr(w2.aq, "_SESSION_FACTORY", gw.factory())
    with caplog.at_level(logging.WARNING):
        res = await w2._run(["apm.app_health"], "web01")
    assert res["degraded_reason"] == "apm_calls_failed"
    assert SECRET not in res["final_response"]
    shown = f"web01: apm_api_error (요청 {bound_rid} · 상세 사유는 서버 로그)"
    assert shown in res["final_response"]
    assert SECRET in caplog.text and bound_rid in caplog.text


# ── S3+ 교정 4차: 3·4단 그래프 오류 응답 노드 ─────────────────────────────


def _error_response(error_message: str) -> str:
    from src.graph import _error_response_node
    from src.state import create_initial_state

    state = create_initial_state("q")
    state["error_message"] = error_message
    return _error_response_node(state)["final_response"]


def test_graph_error_response_hides_raw_with_request_id(bound_rid, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        response = _error_response(SECRET)
    assert SECRET not in response
    assert f"에러 내용: 작업 처리 중 오류가 발생했습니다 (요청 {bound_rid})" in response
    assert SECRET in caplog.text and bound_rid in caplog.text


@pytest.mark.parametrize(("raw", "shown"), [
    (f"SQL 실행 에러: {SECRET}", "에러 내용: SQL 실행 에러\n"),
    (f"모든 DB 쿼리가 실패했습니다:\n[은행존] DB 'b0' 실행 에러: {SECRET}",
     "에러 내용: 모든 DB 쿼리가 실패했습니다:\n[은행존] DB 'b0' 실행 에러\n"),
])
def test_graph_error_response_keeps_safe_head(bound_rid, raw, shown) -> None:
    response = _error_response(raw)
    assert SECRET not in response and shown in response


# ── S3+ 후속 교정(security-auditor): 판별기를 거치지 않던 사용자 노출 경로 ──────────


@pytest.mark.asyncio
async def test_host_inspect_failure_hides_client_exception(monkeypatch, bound_rid, caplog) -> None:
    """조사 도구 호출 예외(`{tool} 호출 실패: {e}`)는 거부 문구(`degraded_reason`)에 싣지 않는다.

    `degraded_reason`이 붙은 결과는 판별기가 처리기 문구로 믿고 그대로 보이므로(`user_error_text`)
    처리기가 문구를 지을 때 원문을 빼야 한다.
    """
    from src.orchestration.host_inspect import DEGRADED_KEY
    from src.utils.user_error import user_error_text
    from tests.test_composite import test_plan121_host_inspect_result as hr

    def boom(tool, args):
        raise RuntimeError(SECRET)

    hr._real_client(monkeypatch, boom)
    with caplog.at_level(logging.WARNING):
        res = await hr.run_host_inspect({"sub_query": "svweb001 OS 정보"}, hr._HOST, llm=None,
                                        app_config=hr._cfg(investigation=True))
    assert res[DEGRADED_KEY] == "inspect_failed"
    for text in (res["error"], res["final_response"], user_error_text(res["error"], result=res)):
        assert SECRET not in text and f"요청 {bound_rid}" in text
    assert SECRET in caplog.text and bound_rid in caplog.text


@pytest.mark.asyncio
async def test_host_inspect_kind_failure_summary_hides_reason(monkeypatch, bound_rid,
                                                              caplog) -> None:
    from tests.test_composite import test_plan121_host_inspect_result as hr

    def payload(tool, args):
        return {"error": SECRET} if args.get("kind") == "memory" else hr._db_payload(tool, args)

    hr._real_client(monkeypatch, payload)
    with caplog.at_level(logging.WARNING):
        res = await hr.run_host_inspect(
            {"sub_query": "svweb001 사용률 추세"}, hr._trend_isolated(["CPU", "메모리"]),
            llm=None, app_config=hr._cfg(investigation=True))
    summary = res["organized_data"]["summary"]
    assert SECRET not in summary
    assert f"메모리 추세는 조회하지 못했습니다(요청 {bound_rid})." in summary
    assert SECRET in caplog.text


@pytest.mark.asyncio
async def test_merged_rows_output_failure_hides_exception(bound_rid, caplog) -> None:
    from src.orchestration.result_aggregator import _finalize_merged_rows
    from src.state import create_initial_state

    agg_mod = sys.modules["src.orchestration.result_aggregator"]

    async def boom(*a, **k):
        raise RuntimeError(SECRET)

    with patch.object(agg_mod, "output_generator", new=boom), caplog.at_level(logging.ERROR):
        out = await _finalize_merged_rows([{"hostname": "a"}], create_initial_state("q"),
                                          llm=AsyncMock(), app_config=SimpleNamespace())
    assert SECRET not in out["final_response"]
    assert out["final_response"] == (
        f"통합 결과 1건을 정리하는 중 오류가 발생했습니다 (요청 {bound_rid})")
    assert SECRET in caplog.text and bound_rid in caplog.text


def _raise_cache_manager(monkeypatch) -> None:
    import src.schema_cache.cache_manager as cm

    def boom(*a, **k):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(cm, "get_cache_manager", boom)


_PENDING = [{"index": 1, "db_id": "polestar", "column": "cmm_resource.hostname",
             "field": "서버명"}]


@pytest.mark.asyncio
async def test_field_mapper_synonym_registration_hides_exception(monkeypatch, bound_rid,
                                                                 caplog) -> None:
    from src.nodes.field_mapper import _handle_synonym_registration
    from src.state import create_initial_state

    _raise_cache_manager(monkeypatch)
    state = create_initial_state("전체 등록")
    state["pending_synonym_registrations"] = _PENDING
    with caplog.at_level(logging.ERROR):
        out = await _handle_synonym_registration(state, {"mode": "all"}, SimpleNamespace())
    assert out is not None
    assert out["final_response"] == f"유사어 등록 중 오류가 발생했습니다 (요청 {bound_rid})"
    assert SECRET in caplog.text and bound_rid in caplog.text


@pytest.mark.asyncio
async def test_synonym_registrar_hides_exception(monkeypatch, bound_rid, caplog) -> None:
    from src.nodes.synonym_registrar import synonym_registrar
    from src.state import create_initial_state

    _raise_cache_manager(monkeypatch)
    state = create_initial_state("전체 등록")
    state["pending_synonym_registrations"] = _PENDING
    with caplog.at_level(logging.ERROR):
        out = await synonym_registrar(state, app_config=SimpleNamespace())
    assert out["final_response"] == f"유사어 등록 중 오류가 발생했습니다 (요청 {bound_rid})"
    assert SECRET not in str(out.get("error_message"))
    assert SECRET in caplog.text and bound_rid in caplog.text


def test_form_fill_exception_reason_hides_exception(monkeypatch, bound_rid, caplog) -> None:
    import src.document.excel_writer as ew
    from src.nodes.output_generator import _generate_document_file

    def boom(**kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(ew, "fill_excel_template", boom)
    state = {"organized_data": {"rows": [], "column_mapping": {"서버명": "hostname"}},
             "template_structure": {"sheets": []}, "uploaded_file": b"x"}
    with caplog.at_level(logging.ERROR):
        out = _generate_document_file(state, "xlsx")
    assert out == {"reason": f"양식 채우기 중 오류가 발생했습니다 (요청 {bound_rid})"}
    assert SECRET in caplog.text and bound_rid in caplog.text


def _apm_failed_meta() -> dict:
    return {"failures": [
        {"view": "apm.app_health", "hostname": "web01", "reason": f"source_unavailable: {SECRET}"},
        {"view": "apm.app_health", "hostname": "web02",
         "reason": f"APM 게이트웨이 연결·호출 실패: {SECRET}"},
    ], "provenance": [], "hostnames": ["web01", "web02"], "inserted_steps": [], "notes": [],
        "limits": []}


def test_apm_accepted_answer_hides_failure_reason(bound_rid, caplog) -> None:
    from src.orchestration.apm_query import _accepted_answer

    with caplog.at_level(logging.WARNING):
        res = _accepted_answer("제니퍼", [], _apm_failed_meta(), [])
    assert SECRET not in res["final_response"]
    assert ("조회하지 못한 대상 2건: web01(source_unavailable), web02 "
            f"(요청 {bound_rid} · 상세 사유는 서버 로그)") in res["final_response"]
    assert SECRET in caplog.text and bound_rid in caplog.text


def test_apm_summary_hides_failure_reason(bound_rid) -> None:
    from src.orchestration.apm_query import _summary

    text = _summary("제니퍼", [], {}, _apm_failed_meta(), 0)
    assert SECRET not in text
    assert "조회하지 못한 대상 2건: web01(source_unavailable), web02 (요청" in text


@pytest.mark.parametrize("raw", [
    "DB 'a' 실행 에러: boom / 답변할 시간을 남기려고 x postgres://u:p@h",
    "답변할 시간을 남기려고 x postgres://u:p@h",
    "DB 'a' 실행 에러: boom | 답변할 시간을 남기려고 x postgres://u:p@h",
    "질문에 맞는 조회 대상 테이블을 찾지 못했습니다 — 무엇을 찾는지 알려 주세요 postgres://u:p@h",
    ("답변할 시간을 남기려고 SQL 재생성을(를) 하지 않았습니다(조회 마감 경과)."
     " 마지막 실패 사유: postgres://u:p@h"),
])
def test_db_error_text_fixed_phrases_must_match_whole(raw) -> None:
    """조회 마감·선별 0개 문구는 머리만 맞아서는 통과하지 않는다(꼬리 원문이 따라 나오지 않는다)."""
    from src.utils.user_error import db_error_text

    assert "postgres://u:p@h" not in db_error_text(raw)


@pytest.mark.parametrize("stage", ["스키마 분석", "SQL 생성", "SQL 재생성"])
@pytest.mark.parametrize(("remaining", "need", "last"), [
    (None, None, None), (10.0, 20.0, None), (10.0, 20.0, f"SQL 실행 에러: {SECRET}"),
    (-5.0, 3.0, "모든 DB 쿼리가 실패했습니다."),
])
def test_db_error_text_keeps_real_deadline_messages(stage, remaining, need, last) -> None:
    from src.nodes.query_validator import deadline_stop_message
    from src.utils.user_error import db_error_text

    message = deadline_stop_message(stage, remaining_sec=remaining, need_sec=need,
                                    last_reason=last)
    assert db_error_text(message) == message
    assert db_error_text(f"DB 'a' 실행 에러: {SECRET} | {message}") == (
        f"DB 'a' 실행 에러 | {message}")


# ── S3+ 후속 교정 2: 제니퍼 첫 홉·대상 해석 단계의 호출 오류 원문 ──────────────────


def _apm_gateway(monkeypatch, **replies):
    from tests.test_orchestration import test_plan134_w2_verify as w2

    gw = w2._Gateway({**w2._ALL_REPLIES, **replies})
    monkeypatch.setattr(w2.aq, "_SESSION_FACTORY", gw.factory())
    return w2


@pytest.mark.asyncio
async def test_apm_rank_failure_reason_hidden_in_first_hop_notice(monkeypatch, bound_rid,
                                                                   caplog) -> None:
    """부하 순위 실패 사유(「부하 순위를 받지 못해(…)」)는 코드와 요청 번호만 — 고지·요약 모두."""
    w2 = _apm_gateway(monkeypatch, apm_fleet=_apm_err("apm_fleet", "apm_api_error"))
    with caplog.at_level(logging.WARNING):
        res = await w2._run(["apm.app_health"])
    assert SECRET not in json.dumps(res, ensure_ascii=False, default=str)
    assert f"부하 순위를 받지 못해(apm_api_error · 요청 {bound_rid})" in (
        res["organized_data"]["summary"])
    assert SECRET in caplog.text and bound_rid in caplog.text


@pytest.mark.asyncio
async def test_apm_first_hop_total_failure_refusal_hides_reason(monkeypatch, bound_rid) -> None:
    """첫 홉(순위·목록)이 모두 실패해 조회하지 않은 답에도 사유 원문이 없다."""
    w2 = _apm_gateway(monkeypatch, apm_fleet=_apm_err("apm_fleet", "apm_api_error"),
                      apm_instance_map=_apm_err("apm_instance_map", "source_unavailable"))
    res = await w2._run(["apm.app_health"])
    assert SECRET not in res["final_response"]
    assert SECRET not in json.dumps(res.get("disclosures") or [], ensure_ascii=False)
    assert f"source_unavailable (요청 {bound_rid})" in res["final_response"]


@pytest.mark.asyncio
async def test_apm_target_search_failure_notice_hides_reason(monkeypatch, bound_rid) -> None:
    """대상 텍스트 해석 검색 실패 고지(「… 검색 실패 — …」)에 원문을 싣지 않는다."""
    w2 = _apm_gateway(monkeypatch,
                      apm_instance_map=_apm_err("apm_instance_map", "apm_api_error"))
    task = {"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health"],
            "sub_query": "질의", "targets": [{"text": "zzz", "kind": "instance"}]}
    res = await w2.aq.run_apm_query(task, w2._isolated(), llm=None, app_config=w2._cfg(),
                                    now=w2.NOW)
    texts = json.dumps([res.get("final_response"), res.get("disclosures")], ensure_ascii=False)
    assert SECRET not in texts
    assert f"검색 실패 — apm_api_error (요청 {bound_rid})" in texts


@pytest.mark.asyncio
async def test_apm_result_file_read_failure_hides_reason(bound_rid, caplog) -> None:
    from src.orchestration import apm_query as aq

    class _Down:
        async def call_tool(self, name, args):
            raise aq.SourceMcpError(SECRET)

    env = {"artifact": {"job_id": "j1", "chunks": [{"index": 0}]}}
    with caplog.at_level(logging.WARNING):
        rows, short = await aq._read_result_file(_Down(), env, SimpleNamespace(owner="o"),
                                                 [{"a": 1}], 10)
    assert rows == [{"a": 1}]
    assert short == f"결과 파일 청크 1/1 읽기 실패 — 호출 실패 (요청 {bound_rid})"
    assert SECRET in caplog.text and bound_rid in caplog.text


def _apm_err(tool: str, code: str) -> dict:
    return {"error": code, "reason": SECRET, "source_kind": "apm_api", "source": "jennifer",
            "tool": tool}


@pytest.mark.parametrize(("err", "shown"), [
    (f"apm_api_error: {SECRET}", "apm_api_error"),
    ("instance_unresolved: web01 불일치", "instance_unresolved: web01 불일치"),
    ("postgresql://u:p@h/db", None),  # 원문의 스킴은 오류 코드가 아니다
    (f"APM 게이트웨이 연결·호출 실패: {SECRET}", None),
    # 예외 종류 이름 머리는 종류 이름만, 코드가 원문 없이 지은 사유는 그대로
    (f"RuntimeError: {SECRET}", "RuntimeError"),
    ("APM 게이트웨이 도구 호출 시간 초과(apm_instance_map, 30초)",
     "APM 게이트웨이 도구 호출 시간 초과(apm_instance_map, 30초)"),
    ("APM 게이트웨이 연결·호출 실패: ConnectError", "APM 게이트웨이 연결·호출 실패: ConnectError"),
    (f"APM 게이트웨이 도구 오류(apm_instance_map): {SECRET}", None),
])
def test_apm_shown_call_error_reads_only_code_prefix(err, shown) -> None:
    from src.orchestration.apm_query import _shown_call_error

    assert _shown_call_error(err) == shown
