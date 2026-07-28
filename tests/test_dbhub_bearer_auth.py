"""DBHubClient 전송 인증 Bearer 헤더 검증 (Plan 04 §6-4, D-015).

설정 토큰이 있으면 SSE 연결에 `Authorization: Bearer <token>` 헤더를 첨부하고,
없으면 무헤더(서버 무인증 통과 전제 — 기존 동작 비트동일)임을 확인한다.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import DBHubConfig
from src.dbhub.client import DBHubClient


def _make_client(bearer_token: str = "") -> DBHubClient:
    config = DBHubConfig(
        server_url="http://localhost:9099/sse",
        source_name="infra_db",
        mcp_call_timeout=10,
        bearer_token=bearer_token,
    )
    return DBHubClient(config)


# ── _auth_headers 단위 ──────────────────────────────────────────


def test_auth_headers_none_when_no_token():
    """토큰 미설정 시 헤더는 None(무헤더 — 회귀 0)."""
    assert _make_client(bearer_token="")._auth_headers() is None


def test_auth_headers_bearer_when_token_set():
    """토큰 설정 시 Bearer 헤더를 구성한다."""
    client = _make_client(bearer_token="sekret")
    assert client._auth_headers() == {"Authorization": "Bearer sekret"}


def test_dbhub_config_bearer_token_default_empty():
    """DBHubConfig.bearer_token 기본값은 빈 문자열이다(무인증 전제)."""
    assert DBHubConfig(source_name="x").bearer_token == ""


# ── connect()가 sse_client에 headers를 전달하는지 ───────────────


class _FakeSSEContext:
    """sse_client가 반환하는 async 컨텍스트 매니저 대역."""

    async def __aenter__(self):
        return (MagicMock(name="read_stream"), MagicMock(name="write_stream"))

    async def __aexit__(self, *exc):
        return False


class _FakeSessionContext:
    """ClientSession() async 컨텍스트 매니저 대역 — initialize를 가진 세션 반환."""

    async def __aenter__(self):
        session = MagicMock(name="session")
        session.initialize = AsyncMock()
        return session

    async def __aexit__(self, *exc):
        return False


def _run_connect_capture(bearer_token: str) -> dict:
    """connect()를 실행하고 sse_client 호출 kwargs를 캡처해 반환한다."""
    captured: dict = {}

    def fake_sse_client(url, headers=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeSSEContext()

    client = _make_client(bearer_token=bearer_token)
    with patch("mcp.client.sse.sse_client", side_effect=fake_sse_client), patch(
        "mcp.ClientSession", return_value=_FakeSessionContext()
    ):
        asyncio.run(client.connect())
    return captured


def test_connect_attaches_bearer_header_when_token_set():
    """토큰이 있으면 connect가 sse_client에 Bearer 헤더를 전달한다."""
    captured = _run_connect_capture(bearer_token="tok-123")
    assert captured["headers"] == {"Authorization": "Bearer tok-123"}
    assert captured["url"] == "http://localhost:9099/sse"


def test_connect_no_header_when_token_absent():
    """토큰이 없으면 headers=None으로 전달(기존 동작 비트동일 — 회귀 0)."""
    captured = _run_connect_capture(bearer_token="")
    assert captured["headers"] is None
