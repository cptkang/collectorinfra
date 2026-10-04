"""우선순위 속도 제어 — poller > interactive > background · 에이징 · 취소 (plans/134 W0-B N-14 ·
SPEC-apm-question-coverage §3.4).

속도(초당 호출 수)는 그대로 두고 대기열 순서만 본다. 실 네트워크 없음(MockTransport).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.adapters.throttle import PriorityThrottle
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_POLLER,
    CallScope,
    use_scope,
)

from apm_gateway.config import JenniferApiConfig


async def _queue(throttle: PriorityThrottle, labels: list[str], order: list[str]) -> list:
    async def one(label: str) -> None:
        await throttle.acquire(label)
        order.append(label)

    tasks = []
    for label in labels:
        tasks.append(asyncio.create_task(one(label)))
        await asyncio.sleep(0)  # 대기열 진입 순서를 고정한다
    return tasks


@pytest.mark.asyncio
async def test_waiting_requests_go_poller_then_interactive_then_background():
    throttle = PriorityThrottle(rate_per_sec=50, aging_seconds=60)
    await throttle.acquire(PRIORITY_INTERACTIVE)  # 차례를 하나 써서 뒤 요청들이 기다리게 한다
    order: list[str] = []
    tasks = await _queue(
        throttle,
        [PRIORITY_BACKGROUND, PRIORITY_BACKGROUND, PRIORITY_INTERACTIVE, PRIORITY_POLLER],
        order,
    )
    await asyncio.gather(*tasks)
    assert order == [
        PRIORITY_POLLER,
        PRIORITY_INTERACTIVE,
        PRIORITY_BACKGROUND,
        PRIORITY_BACKGROUND,
    ]


@pytest.mark.asyncio
async def test_aging_raises_long_waiting_background_request():
    """에이징 — 오래 기다린 background가 나중에 온 interactive보다 먼저 나간다(기아 방지)."""
    for aging, expected in ((0.05, PRIORITY_BACKGROUND), (60.0, PRIORITY_INTERACTIVE)):
        now = [100.0]
        throttle = PriorityThrottle(rate_per_sec=10, aging_seconds=aging, clock=lambda: now[0])
        await throttle.acquire(PRIORITY_INTERACTIVE)  # 다음 차례 = 100.1
        order: list[str] = []
        tasks = await _queue(throttle, [PRIORITY_BACKGROUND], order)
        now[0] = 100.06
        tasks += await _queue(throttle, [PRIORITY_INTERACTIVE], order)
        now[0] = 100.1  # 배차 시점 — background는 0.1초(에이징 2주기) · interactive는 0.04초 대기
        await asyncio.gather(*tasks)
        assert order[0] == expected, aging


@pytest.mark.asyncio
async def test_cancelled_waiter_leaves_queue_and_others_proceed():
    throttle = PriorityThrottle(rate_per_sec=50)
    await throttle.acquire()
    order: list[str] = []
    tasks = await _queue(throttle, [PRIORITY_BACKGROUND, PRIORITY_INTERACTIVE], order)
    tasks[1].cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert order == [PRIORITY_BACKGROUND] and throttle.waiting == 0


@pytest.mark.asyncio
async def test_rate_zero_never_waits():
    throttle = PriorityThrottle(rate_per_sec=0)
    for _ in range(100):
        await throttle.acquire(PRIORITY_BACKGROUND)
    assert throttle.waiting == 0


@pytest.mark.asyncio
async def test_client_requests_follow_call_scope_priority():
    """소스 클라이언트 — 같은 토큰을 나누는 백그라운드 작업 대기 중에도 폴러 요청이 먼저 나간다."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("domain_id", "-"))
        return httpx.Response(200, json={"result": []})

    client = JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t", rate_limit_per_sec=40),
        transport=httpx.MockTransport(handler),
    )
    await client.get_json("/api/domain")  # 차례를 하나 쓴다

    async def call(priority: str, domain: int) -> None:
        with use_scope(CallScope(priority)):
            await client.get_json("/api/instance", {"domain_id": domain})

    tasks = []
    for priority, domain in (
        (PRIORITY_BACKGROUND, 1),
        (PRIORITY_BACKGROUND, 2),
        (PRIORITY_INTERACTIVE, 3),
        (PRIORITY_POLLER, 4),
    ):
        tasks.append(asyncio.create_task(call(priority, domain)))
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    await client.aclose()
    assert seen == ["-", "4", "3", "1", "2"]
    assert client.calls_total == 5
