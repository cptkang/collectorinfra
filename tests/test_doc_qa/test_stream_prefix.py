"""`/문서` 접두의 스트림 진입 + 관리자 「문서 검색 시험」 탭 토큰 (plans/126 T-3·T-4).

2026-09-30 폐쇄망 실측 결함 2건을 고정한다.

- 채팅 화면은 텍스트 질의를 `/query/stream`으로 보내는데 접두 분기가 `/query`에만 있어,
  `/문서 백업 담당자의 역할은 무엇인가?`가 그래프로 새어 일반 안내로 분류됐다.
- 탭 스크립트가 `admin_token`만 읽어, 일반 로그인(`/login` → `user_token`)한 admin 계정이
  토큰 없이 요청해 401을 받았다(화면에는 「권한이 없습니다」로 보였다).

실 LLM 0 · 네트워크 0.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.api.schemas import QueryResponse

ROOT = Path(__file__).resolve().parent.parent.parent


class _UntouchableGraph:
    """접두 질의에서는 그래프를 한 번도 건드리면 안 된다."""

    def get_state(self, config):
        raise AssertionError("접두 질의가 체크포인트를 읽었다")

    async def ainvoke(self, *a, **k):
        raise AssertionError("접두 질의가 그래프로 새었다")

    def astream_events(self, *a, **k):
        raise AssertionError("접두 질의가 그래프로 새었다")


def _config(*, prefix: bool = True, heartbeat: int = 5):
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, RagConfig, ServerConfig

    return AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, sse_heartbeat_interval_sec=heartbeat),
        rag=RagConfig(enabled=True, chat_prefix_enabled=prefix),
    )


def _client(config, graph=None) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = config
    app.state.graph = graph or _UntouchableGraph()
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "admin"}
    return TestClient(app)


def _events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def doc_answer(monkeypatch):
    """문서 엔진 대역 — 받은 명령을 기록하고 고정 답을 돌려준다."""
    from src.api.routes import query as query_routes

    calls: list = []

    async def fake(config, command, *, query_id, thread_id, user=None):
        calls.append((command.query, tuple(command.collection_ids), user))
        return QueryResponse(
            query_id=query_id, status="completed", thread_id=thread_id,
            response="문서 근거 답",
        )

    monkeypatch.setattr(query_routes, "_answer_document_query", fake)
    return calls


QUERY = "/문서 백업 담당자의 역할은 무엇인가?"


def test_stream_route_answers_prefix_without_graph(doc_answer):
    r = _client(_config()).post("/api/v1/query/stream", json={"query": QUERY})
    assert r.status_code == 200, r.text
    done = [e for e in _events(r.text) if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["response"] == "문서 근거 답"
    assert doc_answer == [("백업 담당자의 역할은 무엇인가?", (), {"sub": "u1", "role": "admin"})]


def test_stream_and_non_stream_give_the_same_answer(doc_answer):
    client = _client(_config())
    streamed = [e for e in _events(client.post("/api/v1/query/stream", json={"query": QUERY}).text)
                if e["type"] == "done"][0]["response"]
    plain = client.post("/api/v1/query", json={"query": QUERY}).json()["response"]
    assert streamed == plain == "문서 근거 답"
    assert len(doc_answer) == 2


def test_scoped_prefix_reaches_engine_on_stream(doc_answer):
    r = _client(_config()).post("/api/v1/query/stream", json={"query": "/문서:hq_manual 계정 신청 절차"})
    assert r.status_code == 200
    assert doc_answer[0][:2] == ("계정 신청 절차", ("hq_manual",))


def test_heartbeat_while_engine_is_slow(monkeypatch):
    """검색+LLM이 화면의 무신호 경고(15초)를 넘길 수 있어 기다리는 동안 heartbeat를 낸다."""
    from src.api.routes import query as query_routes

    async def slow(config, command, *, query_id, thread_id, user=None):
        await asyncio.sleep(1.3)
        return QueryResponse(query_id=query_id, status="completed", thread_id=thread_id, response="늦은 답")

    monkeypatch.setattr(query_routes, "_answer_document_query", slow)
    events = _events(_client(_config(heartbeat=1)).post("/api/v1/query/stream", json={"query": QUERY}).text)
    kinds = [e["type"] for e in events]
    assert "heartbeat" in kinds
    assert kinds[-1] == "done" and events[-1]["response"] == "늦은 답"


def test_engine_failure_becomes_error_event(monkeypatch):
    """헤더가 이미 나간 뒤라 500이 아니라 `error` 이벤트로 사유(예외 클래스)를 알린다."""
    from src.api.routes import query as query_routes

    async def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(query_routes, "_answer_document_query", boom)
    events = _events(_client(_config()).post("/api/v1/query/stream", json={"query": QUERY}).text)
    assert [e["type"] for e in events] == ["error"]
    assert "RuntimeError" in events[0]["message"]


def test_prefix_off_leaves_stream_to_the_graph(monkeypatch):
    """접두가 꺼져 있으면 종전 그대로 그래프로 간다(기본 off = 현행 동작)."""
    from src.api.routes import query as query_routes

    doc_calls: list = []

    async def never(*a, **k):
        doc_calls.append(a)

    class _RecordingGraph:
        def __init__(self):
            self.touched: list[str] = []

        def get_state(self, config):
            return None

        async def ainvoke(self, *a, **k):
            self.touched.append("ainvoke")
            return {"final_response": "그래프 답"}

        def astream_events(self, *a, **k):
            self.touched.append("astream_events")
            raise RuntimeError("그래프 도달")

    monkeypatch.setattr(query_routes, "_answer_document_query", never)
    graph = _RecordingGraph()
    _client(_config(prefix=False), graph).post("/api/v1/query/stream", json={"query": QUERY})
    assert "astream_events" in graph.touched
    assert doc_calls == []


class TestAdminTabToken:
    """관리자 화면 스크립트는 로그인 경로와 무관하게 같은 토큰을 싣는다."""

    def test_rag_tab_reads_user_token_like_admin_js(self):
        rag = (ROOT / "src/static/js/admin-rag-docs.js").read_text(encoding="utf-8")
        admin = (ROOT / "src/static/js/admin.js").read_text(encoding="utf-8")
        order = 'var TOKEN_KEYS = ["admin_token", "user_token"];'
        assert order in admin
        assert order in rag

    def test_every_dashboard_script_that_reads_admin_token_also_reads_user_token(self):
        """대시보드에 실리는 스크립트 중 `admin_token`만 읽는 것이 없어야 한다(같은 결함의 재발 차단)."""
        html = (ROOT / "src/static/admin/dashboard.html").read_text(encoding="utf-8")
        scripts = re.findall(r'<script src="/static/js/([\w\-]+\.js)', html)
        assert "admin-rag-docs.js" in scripts
        offenders = []
        for name in scripts:
            text = (ROOT / "src/static/js" / name).read_text(encoding="utf-8")
            if '"admin_token"' in text and '"user_token"' not in text:
                offenders.append(name)
        assert not offenders, offenders

    def test_auth_failures_are_told_apart(self):
        """401(토큰 없음·만료)과 403(관리자 아님)을 한 문구로 뭉치지 않는다 — 이번 오진의 원인."""
        rag = (ROOT / "src/static/js/admin-rag-docs.js").read_text(encoding="utf-8")
        assert "status === 401" in rag and "status === 403" in rag
        assert "status === 401 || r.status === 403" not in rag
