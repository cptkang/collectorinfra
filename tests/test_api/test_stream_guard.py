"""SSE 가드의 타임아웃 검출·내부 취소 분리(D-198) 테스트.

종전 wait_for 구조는 타임아웃 발화 후 내부 태스크의 취소 **완료**까지 기다렸다 —
LLM 호출이 취소에 반응하지 않으면(FabriX 미귀환) TimeoutError가 영영 raise되지
않아 가드가 무력화됐다(2026-09-07 task_dump 실측: event_generator가 wait_for에
8분+ 고정). 새 헬퍼는 취소 완료를 기다리지 않고 타임아웃을 즉시 반환해야 한다.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.api.routes.query import _next_event_or_timeout


async def test_normal_event_passthrough():
    async def gen():
        yield {"event": "on_chain_start"}

    it = gen().__aiter__()
    event, timed_out = await _next_event_or_timeout(it, 5)
    assert timed_out is False
    assert event == {"event": "on_chain_start"}


async def test_stop_iteration_propagates():
    """이터레이터 소진은 종전과 동일하게 StopAsyncIteration으로 전파(호출부 break)."""

    async def gen():
        if False:  # pragma: no cover - 빈 async generator 구성용
            yield

    it = gen().__aiter__()
    with pytest.raises(StopAsyncIteration):
        await _next_event_or_timeout(it, 5)


async def test_timeout_detected_without_waiting_for_cancellation():
    """내부가 취소에 늦게 반응해도(FabriX 미귀환 모사) 타임아웃은 즉시 검출된다."""

    async def gen():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # 취소 완료가 0.8초 지연되는 상황 — 종전 wait_for라면 이 지연까지
            # (미귀환이면 무한) 기다린 뒤에야 TimeoutError를 냈다.
            await asyncio.sleep(0.8)
            raise
        yield {}  # pragma: no cover

    it = gen().__aiter__()
    t0 = time.monotonic()
    event, timed_out = await _next_event_or_timeout(it, 0.1)
    elapsed = time.monotonic() - t0

    assert timed_out is True
    assert event is None
    # 취소 지연(0.8s)을 기다리지 않고 타임아웃(0.1s) 직후 반환해야 한다
    assert elapsed < 0.5, f"취소 완료를 기다렸다 (elapsed={elapsed:.2f}s)"

    # 유령 태스크가 자체 정리될 시간을 준다 (이벤트 루프 종료 경고 억제)
    await asyncio.sleep(1.0)
