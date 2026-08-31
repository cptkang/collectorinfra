"""DBHubClient 종료 경로 정리 보장 검증 (D-193).

`disconnect()`가 session·sse 컨텍스트를 **각각 독립적으로** 닫는지 확인한다.
한 try에 묶으면 session `__aexit__` 실패(취소 중 언와인딩·anyio cancel scope
위반 등)에 막혀 `sse_client`가 열린 채 남고, 좀비 sse_reader가 이후 서버
메시지마다 `anyio.BrokenResourceError`를 뿜는다(2026-08-31 실측 재현).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.config import DBHubConfig
from src.dbhub.client import DBHubClient


def _make_client() -> DBHubClient:
    return DBHubClient(
        DBHubConfig(
            server_url="http://localhost:9099/sse",
            source_name="infra_db",
            mcp_call_timeout=10,
        )
    )


def _attach_contexts(client: DBHubClient, *, session_exc: BaseException | None = None):
    """session·sse 컨텍스트를 주입한다. session_exc가 있으면 session 종료가 실패한다."""
    session_ctx = AsyncMock()
    sse_ctx = AsyncMock()
    if session_exc is not None:
        session_ctx.__aexit__.side_effect = session_exc
    client._session_context = session_ctx
    client._sse_context = sse_ctx
    client._mcp_session = object()
    client._connected = True
    return session_ctx, sse_ctx


def test_disconnect_closes_both_contexts():
    """정상 경로에서 session·sse 둘 다 닫는다."""
    client = _make_client()
    session_ctx, sse_ctx = _attach_contexts(client)

    asyncio.run(client.disconnect())

    session_ctx.__aexit__.assert_awaited_once()
    sse_ctx.__aexit__.assert_awaited_once()
    assert client._connected is False
    assert client._sse_context is None


def test_disconnect_closes_sse_even_if_session_close_fails():
    """session 종료가 실패해도 sse는 반드시 닫는다 — 좀비 sse_reader 차단(D-193).

    회귀 대상: 두 __aexit__를 한 try에 묶으면 session 실패 시 sse가 호출조차
    되지 않아 SSE 연결·리더 태스크가 프로세스에 남는다.
    """
    client = _make_client()
    session_ctx, sse_ctx = _attach_contexts(
        client,
        session_exc=RuntimeError(
            "Attempted to exit cancel scope in a different task than it was entered in"
        ),
    )

    asyncio.run(client.disconnect())

    session_ctx.__aexit__.assert_awaited_once()
    sse_ctx.__aexit__.assert_awaited_once()  # ← 수정 전에는 호출되지 않았다
    assert client._sse_context is None
    assert client._connected is False


def test_disconnect_reraises_cancellation_after_cleaning_up():
    """session 종료가 취소돼도 sse는 닫고, 취소는 삼키지 않고 재전파한다."""
    client = _make_client()
    session_ctx, sse_ctx = _attach_contexts(client, session_exc=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client.disconnect())

    sse_ctx.__aexit__.assert_awaited_once()
    assert client._sse_context is None
    assert client._connected is False


def test_connect_cleans_up_half_open_context_on_cancellation():
    """핸드셰이크 취소 시 반쯤 열린 컨텍스트를 connect한 태스크에서 정리한다(D-193).

    정리를 상위 finally에 맡기면 다른 태스크에서 `__aexit__`가 불려 anyio
    cancel scope 규약이 깨지고 sse_client가 좀비로 남는다.
    """
    client = _make_client()
    sse_ctx = AsyncMock()
    sse_ctx.__aenter__.return_value = (object(), object())

    async def _run():
        def _sse_client(**_kwargs):
            return sse_ctx

        class _Session:
            def __init__(self, *_a):
                pass

            async def __aenter__(self):
                raise asyncio.CancelledError()  # 핸드셰이크 중 취소

            async def __aexit__(self, *_a):
                return False

        import mcp
        import mcp.client.sse

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(mcp, "ClientSession", _Session, raising=False)
            mp.setattr(mcp.client.sse, "sse_client", _sse_client, raising=False)
            with pytest.raises(asyncio.CancelledError):
                await client.connect()

    asyncio.run(_run())

    sse_ctx.__aexit__.assert_awaited_once()  # 반쯤 열린 sse가 정리됐다
    assert client._sse_context is None
    assert client._connected is False
