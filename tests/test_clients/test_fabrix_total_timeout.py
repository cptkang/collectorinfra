"""FabriX 호출 총 소요 상한(D-195) 테스트 — 실 호출 0건·과금 0.

무한대기 근본 원인(2026-09-07 task_dump 실측): FabriX가 연결을 유지한 채
하트비트(STATUS/SYNC)만 보내면 httpx read 타임아웃이 라인마다 리셋되어 호출이
무기한이 된다(1시간+ 관측). 총상한은 그와 무관하게 벽시계 기준으로 끊어야 한다.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from langchain_core.messages import HumanMessage

import src.clients.fabrix_kbgenai as mod
from src.clients.fabrix_kbgenai import KBGenAIChat


def _make(total_timeout: int = 1) -> KBGenAIChat:
    return KBGenAIChat(
        endpoint_url="https://mock.fabrix.invalid/v1/chat",
        x_openapi_token="t",
        x_generative_ai_client="c",
        asset_id="m",
        total_timeout=total_timeout,
    )


class _HangingClient:
    """응답 없는 POST — 연결 유지 + 완전 침묵 모사."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        await asyncio.sleep(3600)


class _HeartbeatResponse:
    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        while True:
            yield "data: " + json.dumps({"content": "", "event_status": "STATUS"})
            await asyncio.sleep(0.01)


class _HeartbeatStreamCtx:
    async def __aenter__(self):
        return _HeartbeatResponse()

    async def __aexit__(self, *a):
        return False


class _HeartbeatClient:
    """하트비트만 무한 송신하는 SSE — read 타임아웃 리셋 시나리오 모사."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, *a, **kw):
        return _HeartbeatStreamCtx()


async def test_agenerate_total_timeout(monkeypatch):
    monkeypatch.setattr(mod.httpx, "AsyncClient", _HangingClient)
    llm = _make(total_timeout=1)
    start = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await llm._agenerate([HumanMessage(content="hi")])
    assert time.monotonic() - start < 5


async def test_astream_heartbeat_total_timeout(monkeypatch):
    """하트비트가 계속 와서 read 타임아웃이 리셋돼도 총상한에서 끊는다."""
    monkeypatch.setattr(mod.httpx, "AsyncClient", _HeartbeatClient)
    llm = _make(total_timeout=1)
    start = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        async for _ in llm._astream([HumanMessage(content="hi")]):
            pass
    assert time.monotonic() - start < 5


async def test_agenerate_normal_under_limit():
    """정상 응답은 총상한과 무관하게 그대로 통과한다(회귀 가드)."""
    from tests.mocks.fabrix_kbgenai_mock import make_llm, mock_kbgenai

    with mock_kbgenai():
        llm = make_llm()
        result = await llm._agenerate(
            [HumanMessage(content="안녕하세요, 오늘 날씨 어때요?")]
        )
    assert result.generations[0].message.content
