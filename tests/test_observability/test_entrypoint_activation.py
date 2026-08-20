"""진입점에서 트레이스가 **실제로** 발동하는지 검증한다 (D-141).

`test_wiring_parity.py`는 소스 텍스트 검사라 "코드가 거기 있다"만 말한다. 여기서는
실제 요청을 흘려보내 파일이 생기는지 확인한다 — 배선이 있어도 미들웨어 순서·예외 처리
때문에 발동하지 않을 수 있기 때문이다.

HTTP 진입점 4곳(`ainvoke` 2 + `astream_events` 2)은 모두 `AuditMiddleware`를 지나므로
미들웨어 수준에서 검증하면 네 경로가 함께 덮인다. CLI는 미들웨어 밖이라 별도로 본다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.api.middleware.audit_middleware import AuditMiddleware
from src.observability import trace_collector as tc
from src.observability.levels import TraceLevel


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    tc.reset_all()
    monkeypatch.chdir(tmp_path)   # logs/는 cwd 기준으로 생성된다
    yield
    tc.reset_all()


def _trace_files(root: Path) -> list[Path]:
    d = root / "logs" / "trace" / datetime.now().strftime("%Y-%m-%d")
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def _app(handler) -> Starlette:
    app = Starlette(routes=[Route("/q", handler, methods=["POST"])])
    app.add_middleware(AuditMiddleware)
    return app


class TestHttpEntrypoint:
    """네 HTTP 진입점이 공유하는 미들웨어 경로."""

    def test_failed_request_writes_trace(self, tmp_path):
        """노드가 실패 신호를 남긴 요청은 파일로 덤프된다."""
        async def handler(request):
            rid = request.state.request_id
            tc.observe_state(rid, {"routing_intent": "data_query",
                                   "query_results": [], "retry_count": 0})
            tc.record_step(rid, tc.TraceStep(
                step=1, node="query_executor", level=TraceLevel.INFO,
                event="node.exit", elapsed_ms=1.0))
            return PlainTextResponse("ok")

        with TestClient(_app(handler)) as client:
            client.post("/q")

        files = _trace_files(tmp_path)
        assert len(files) == 1, "실패 요청인데 트레이스가 남지 않았다"
        header = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
        assert header["severity"] == "warn"
        assert header["triggers"] == ["zero_rows"]

    def test_healthy_request_writes_nothing(self, tmp_path):
        async def handler(request):
            rid = request.state.request_id
            tc.observe_state(rid, {"routing_intent": "data_query",
                                   "query_results": [{"a": 1}], "retry_count": 0})
            return PlainTextResponse("ok")

        with TestClient(_app(handler)) as client:
            client.post("/q")

        assert _trace_files(tmp_path) == []

    def test_exception_in_handler_still_dumps(self, tmp_path):
        """예외로 빠져나온 요청도 덤프된다 — 그때가 가장 진단이 필요하다."""
        async def handler(request):
            rid = request.state.request_id
            tc.record_step(rid, tc.TraceStep(
                step=1, node="query_generator", level=TraceLevel.ERROR,
                event="node.exception", elapsed_ms=1.0, reason="RuntimeError"))
            tc.observe_state(rid, {"error_message": "boom"})
            raise RuntimeError("boom")

        with TestClient(_app(handler), raise_server_exceptions=False) as client:
            client.post("/q")

        files = _trace_files(tmp_path)
        assert len(files) == 1
        header = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
        assert header["severity"] == "error"

    def test_request_buffer_released_after_response(self, tmp_path):
        """요청이 끝나면 버퍼가 해제된다 (미들웨어가 수명을 닫는다)."""
        async def handler(request):
            return PlainTextResponse("ok")

        with TestClient(_app(handler)) as client:
            client.post("/q")

        assert tc.active_request_count() == 0

    def test_each_request_gets_its_own_file(self, tmp_path):
        """요청마다 별도 파일 — request_id로 구분된다."""
        async def handler(request):
            rid = request.state.request_id
            tc.observe_state(rid, {"routing_intent": "data_query",
                                   "query_results": [], "retry_count": 0})
            return PlainTextResponse("ok")

        with TestClient(_app(handler)) as client:
            client.post("/q")
            client.post("/q")

        assert len(_trace_files(tmp_path)) == 2

    def test_disabled_flag_suppresses_dump(self, tmp_path, monkeypatch):
        """OBS_TRACE_ENABLED=false면 요청이 실패해도 파일이 없다."""
        from src.config import ObservabilityConfig

        monkeypatch.setattr(
            "src.api.middleware.audit_middleware._observability_config",
            lambda: ObservabilityConfig(trace_enabled=False),
        )

        async def handler(request):
            rid = request.state.request_id
            tc.observe_state(rid, {"routing_intent": "data_query",
                                   "query_results": [], "retry_count": 0})
            return PlainTextResponse("ok")

        with TestClient(_app(handler)) as client:
            client.post("/q")

        assert _trace_files(tmp_path) == []


class TestCliEntrypoint:
    """CLI는 미들웨어를 지나지 않으므로 별도 배선이 필요하다."""

    def test_cli_wires_request_id_into_initial_state(self):
        """CLI가 request_id를 state에 넣어야 노드가 추적 대상이 된다."""
        import inspect

        from src import main

        source = inspect.getsource(main)
        assert "request_id=request_id" in source, (
            "CLI가 request_id를 initial_state에 넣지 않으면 traced가 수집을 건너뛴다"
        )

    async def test_cli_flow_produces_trace(self, tmp_path):
        """CLI와 동일한 순서(start → 노드 실행 → flush)로 파일이 생긴다."""
        from src.observability.trace_writer import flush_if_failed

        tc.start_request("cli-abc", thread_id="cli-session", user_query="질의")

        async def node(state):
            return {"routing_intent": "data_query", "query_results": [], "retry_count": 0}

        await tc.traced(node, name="query_executor")({"request_id": "cli-abc"})
        flush_if_failed("cli-abc", project_root=tmp_path)

        files = _trace_files(tmp_path)
        assert len(files) == 1 and files[0].name == "cli-abc.jsonl"
