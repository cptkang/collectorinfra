"""plans/119 T-1ⓐ·ⓒ — LLM 호출 상한 = min(설정 상한, 요청 마감까지 남은 시간) · 취소 실측.

- `astream_text`: 마감이 없으면 종전 호출과 인자까지 같다. 마감이 묶이면 조회 단계는 조회 마감,
  최종 응답 스트림(`USER_RESPONSE_TAG`)은 처리 마감 + 전달 연장(스트림 라우트가 묶는 값)에서
  끊는다. `max_tokens`는 받는 방법이 실측된 provider(ChatOpenAI 계열 = MLX 워커)에만 싣는다.
- `KBGenAIChat`(FabriX 운영): `_agenerate`·`_astream`의 총상한이 `call_timeout(total_timeout)`이다.
- 취소 실측(T-1ⓒ): 127.0.0.1 루프백 HTTP 서버로 FabriX 스트림을 흉내 내고, 상한이 발동하면
  **서버 쪽에서 연결 종료가 관측되는지**(httpx 스트림이 실제로 닫히는지) 확인한다.

외부 네트워크·LLM·과금 0 — 서버는 루프백에만 바인딩한다(tests/conftest.py 네트워크 가드 허용 범위).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk

import src.clients.fabrix_kbgenai as fabrix_mod
from src.clients.fabrix_kbgenai import KBGenAIChat
from src.llm import USER_RESPONSE_TAG, astream_text, output_token_limit_kwargs
from src.utils.deadline import bind_request_deadline, unbind_request_deadline


class _SlowLLM:
    """astream 인자를 기록하고 지연 뒤 한 청크를 내는 모의 LLM."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    def astream(self, messages, config=None, **kwargs):  # noqa: ANN001
        self.calls.append({"config": config, "kwargs": kwargs})

        async def _gen():
            await asyncio.sleep(self.delay)
            yield AIMessageChunk(content="ok")

        return _gen()


def _kbgenai(url: str = "http://127.0.0.1:9/", total_timeout: int = 300) -> KBGenAIChat:
    return KBGenAIChat(
        endpoint_url=url, x_openapi_token="t", x_generative_ai_client="c",
        asset_id="m", total_timeout=total_timeout, timeout=300,
    )


def _bind(seconds_from_now: float, reserve: float = 0.0, grace: float = 0.0):
    return bind_request_deadline(time.monotonic() + seconds_from_now, reserve, grace)


# ── 출력 토큰 상한 전달 ──────────────────────────────────────────────────────


class TestOutputTokenLimit:
    def test_supported_only_for_chat_openai_family(self):
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

        from src.clients.fabrix_client import FabriXAPIClient
        from src.clients.ollama_client import LLMAPIClient

        mlx = pytest.importorskip("src.clients.mlx_client")
        worker = mlx.MLXChatOpenAI(base_url="http://127.0.0.1:9/v1", api_key="EMPTY", model="x")
        assert output_token_limit_kwargs(worker, 123) == {"max_tokens": 123}
        assert output_token_limit_kwargs(_kbgenai(), 123) is None
        assert output_token_limit_kwargs(
            FabriXAPIClient(base_url="http://127.0.0.1:9", chat_model="m", api_key="k"), 1
        ) is None
        assert output_token_limit_kwargs(LLMAPIClient(), 1) is None
        assert output_token_limit_kwargs(GenericFakeChatModel(messages=iter([])), 1) is None

    def test_mlx_request_body_carries_max_completion_tokens(self):
        mlx = pytest.importorskip("src.clients.mlx_client")
        worker = mlx.MLXChatOpenAI(
            base_url="http://127.0.0.1:9/v1", api_key="EMPTY", model="x",
            max_completion_tokens=4096,
        )
        payload = worker._get_request_payload([HumanMessage("x")], stream=True, max_tokens=77)
        assert payload["max_completion_tokens"] == 77  # 생성자 기본값(4096)을 덮어쓴다

    @pytest.mark.asyncio
    async def test_astream_text_binds_max_tokens_for_mlx(self):
        mlx = pytest.importorskip("src.clients.mlx_client")
        seen: dict[str, Any] = {}

        async def _fake_astream(self, messages, stop=None, run_manager=None, **kwargs):
            seen.update(kwargs)
            yield ChatGenerationChunk(message=AIMessageChunk(content="요약"))

        worker = mlx.MLXChatOpenAI(base_url="http://127.0.0.1:9/v1", api_key="EMPTY", model="x")
        with patch.object(mlx.MLXChatOpenAI, "_astream", _fake_astream):
            assert await astream_text(worker, [HumanMessage("x")], max_tokens=55) == "요약"
        assert seen.get("max_tokens") == 55


# ── astream_text 마감 전파 ────────────────────────────────────────────────────


class TestAstreamTextDeadline:
    @pytest.mark.asyncio
    async def test_no_deadline_call_is_unchanged(self):
        llm = _SlowLLM()
        assert await astream_text(llm, [], tags=[USER_RESPONSE_TAG]) == "ok"
        assert llm.calls == [{"config": {"tags": [USER_RESPONSE_TAG]}, "kwargs": {}}]
        # 받는 방법이 없는 provider에 max_tokens를 줘도 인자는 그대로다
        await astream_text(llm, [], max_tokens=10)
        assert llm.calls[-1] == {"config": None, "kwargs": {}}

    @pytest.mark.asyncio
    async def test_retrieval_call_cut_at_retrieval_deadline(self):
        token = _bind(2.5, reserve=1.5)  # 조회 마감 = 1.0초 뒤
        try:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                await astream_text(_SlowLLM(delay=10.0), [])
            assert 0.8 <= time.monotonic() - started < 2.0
        finally:
            unbind_request_deadline(token)

    @pytest.mark.asyncio
    async def test_user_response_stream_runs_to_processing_deadline(self):
        token = _bind(2.0, reserve=1.5)  # 조회 마감은 0.5초 뒤지만 서술은 처리 마감(2.0초)까지
        try:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                await astream_text(_SlowLLM(delay=10.0), [], tags=[USER_RESPONSE_TAG])
            assert 1.8 <= time.monotonic() - started < 3.5
            # 마감 안에 끝나는 호출은 그대로 통과한다
            assert await astream_text(_SlowLLM(delay=0.0), [], tags=[USER_RESPONSE_TAG]) == "ok"
        finally:
            unbind_request_deadline(token)

    @pytest.mark.asyncio
    async def test_user_response_stream_extends_by_delivery_grace(self):
        # G-7: 첫 답변 뒤는 라우트의 idle·전체 상한이 끊는다 — 최종 응답 스트림의 호출 상한은
        # 처리 마감 + 전달 연장(스트림 라우트가 묶는 값)까지다. 조회 호출은 여전히 조회 마감이다.
        token = _bind(0.5, reserve=0.2, grace=1.5)  # 처리 마감 0.5초 · 전체 상한 2.0초 뒤
        try:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                await astream_text(_SlowLLM(delay=10.0), [], tags=[USER_RESPONSE_TAG])
            assert 1.8 <= time.monotonic() - started < 3.5
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                await astream_text(_SlowLLM(delay=10.0), [])
            # 조회 마감은 이미 지났다 — 전달 연장을 받지 못하고 최소 상한(MIN_CALL_TIMEOUT_SEC 1초)
            assert time.monotonic() - started < 1.5
        finally:
            unbind_request_deadline(token)


# ── KBGenAIChat 총상한 = call_timeout(total_timeout) ─────────────────────────


class TestFabrixCallTimeout:
    @pytest.mark.asyncio
    async def test_agenerate_uses_configured_limit_without_deadline(self, monkeypatch):
        seen: list[tuple[float, float]] = []
        real = fabrix_mod.call_timeout

        def _spy(configured, **kw):
            out = real(configured, **kw)
            seen.append((configured, out))
            return out

        async def _impl(self, messages):
            from langchain_core.messages import AIMessage
            from langchain_core.outputs import ChatGeneration, ChatResult

            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="x"))])

        monkeypatch.setattr(fabrix_mod, "call_timeout", _spy)
        monkeypatch.setattr(KBGenAIChat, "_agenerate_impl", _impl)
        await _kbgenai(total_timeout=300).ainvoke([HumanMessage("x")])
        assert seen == [(300, 300.0)]

    @pytest.mark.asyncio
    async def test_agenerate_cut_at_deadline(self, monkeypatch):
        async def _hang(self, messages):
            await asyncio.sleep(30)

        monkeypatch.setattr(KBGenAIChat, "_agenerate_impl", _hang)
        token = _bind(1.2)
        try:
            started = time.monotonic()
            with pytest.raises(asyncio.TimeoutError):
                await _kbgenai(total_timeout=300)._agenerate([HumanMessage("x")])
            assert time.monotonic() - started < 3.0  # 300초가 아니라 마감에서 끊긴다
        finally:
            unbind_request_deadline(token)


# ── 취소 실측 (T-1ⓒ) — 루프백 서버가 연결 종료를 관측하는가 ────────────────────


class _FakeFabrixServer:
    """KBGenAI 스트림 흉내 — `heartbeat`(STATUS 줄을 0.2초마다) 또는 `silent`(헤더 뒤 무응답)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.disconnected = asyncio.Event()
        self.disconnected_at: float | None = None
        self._server: asyncio.base_events.Server | None = None
        self.port = 0

    async def __aenter__(self) -> _FakeFabrixServer:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        m = re.search(rb"content-length:\s*(\d+)", head, re.IGNORECASE)
        if m:
            await reader.readexactly(int(m.group(1)))
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        await writer.drain()

        async def _pump() -> None:
            line = b'data: {"content": "", "event_status": "STATUS"}\n'
            try:
                while self.mode == "heartbeat":
                    writer.write(b"%x\r\n%s\r\n" % (len(line), line))
                    await writer.drain()
                    await asyncio.sleep(0.2)
            except (ConnectionError, OSError):
                pass

        pump = asyncio.create_task(_pump())
        try:
            await reader.read()  # 클라이언트가 연결을 닫으면 EOF(b"")로 돌아온다
        except (ConnectionError, OSError):
            pass
        self.disconnected_at = time.monotonic()
        self.disconnected.set()
        pump.cancel()
        writer.close()


class TestCancellationClosesStream:
    @pytest.mark.asyncio
    async def test_heartbeat_stream_cut_by_client_limit_and_connection_closed(self):
        """D-198 무한 하트비트 — 클라이언트 총상한(= 마감)이 끊고, 서버가 연결 종료를 본다."""
        async with _FakeFabrixServer("heartbeat") as srv:
            llm = _kbgenai(f"http://127.0.0.1:{srv.port}/")
            token = _bind(1.2)
            try:
                started = time.monotonic()
                with pytest.raises(asyncio.TimeoutError):
                    async for _ in llm.astream([HumanMessage("x")]):
                        pass
                cut_at = time.monotonic()
                assert cut_at - started < 3.0
            finally:
                unbind_request_deadline(token)
            await asyncio.wait_for(srv.disconnected.wait(), timeout=2.0)
            assert srv.disconnected_at is not None and srv.disconnected_at - cut_at < 1.0

    @pytest.mark.asyncio
    async def test_user_response_stream_client_limit_uses_processing_deadline(self):
        """최종 응답 스트림은 FabriX 클라이언트 자체 상한도 처리 마감을 본다 — 조회 마감(예약만큼
        이른 시각)에서 먼저 끊기지 않는다(호출부가 answer_phase로 감싸지 않은 경우 포함)."""
        async with _FakeFabrixServer("heartbeat") as srv:
            llm = _kbgenai(f"http://127.0.0.1:{srv.port}/")
            token = _bind(2.5, reserve=1.5)  # 조회 마감 1.0초 · 처리 마감 2.5초
            try:
                started = time.monotonic()
                with pytest.raises(TimeoutError):
                    await astream_text(llm, [HumanMessage("x")], tags=[USER_RESPONSE_TAG])
                assert 2.2 <= time.monotonic() - started < 4.0
            finally:
                unbind_request_deadline(token)

    @pytest.mark.asyncio
    async def test_silent_stream_cancelled_by_outer_timeout_closes_connection(self):
        """수신이 완전히 멈춘 스트림 — 줄 단위 검사는 못 돈다. 바깥 상한(astream_text)의
        취소가 httpx 스트림을 실제로 닫는지 본다."""
        async with _FakeFabrixServer("silent") as srv:
            llm = _kbgenai(f"http://127.0.0.1:{srv.port}/")
            token = _bind(1.0)
            try:
                started = time.monotonic()
                with pytest.raises(TimeoutError):
                    await astream_text(llm, [HumanMessage("x")], tags=[USER_RESPONSE_TAG])
                cut_at = time.monotonic()
                assert cut_at - started < 3.0
            finally:
                unbind_request_deadline(token)
            await asyncio.wait_for(srv.disconnected.wait(), timeout=2.0)
            assert srv.disconnected_at is not None and srv.disconnected_at - cut_at < 1.0
