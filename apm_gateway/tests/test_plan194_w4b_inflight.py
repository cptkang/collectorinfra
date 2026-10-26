"""소스별 제니퍼 동시 호출 상한 (plans/194 W4b · D-347).

상한 초과 시 떠 있는 요청 수 ≤ 상한 · 소스 간 독립 · 자리 대기 상한 초과 오류 · 자리 대기의
우선순위 보존 · 설정(전역·소스별 덮어쓰기) · httpx 연결 풀 크기. 실 네트워크 없음(MockTransport).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.adapters.throttle import PrioritySlots
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_POLLER,
    CallScope,
    use_scope,
)
from apm_gateway.domain.errors import SOURCE_UNAVAILABLE, ApmError

from apm_gateway.config import JenniferApiConfig, load_config


class _Gate:
    """요청을 `open` 전까지 붙잡아 두고 동시에 떠 있는 수의 최댓값을 잰다."""

    def __init__(self) -> None:
        self.opened = asyncio.Event()
        self.active = 0
        self.peak = 0
        self.seen: list[str] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request.url.params.get("domain_id", "-"))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await self.opened.wait()
        finally:
            self.active -= 1
        return httpx.Response(200, json={"result": []})


def _client(gate: _Gate, *, limit: int, timeout: float = 5.0, url: str = "http://apm.test"):
    cfg = JenniferApiConfig(
        url=url,
        token="t",
        rate_limit_per_sec=0,  # 속도 제어를 빼고 자리만 본다
        timeout_seconds=timeout,
        max_inflight_per_source=limit,
    )
    return JenniferClient(cfg, transport=httpx.MockTransport(gate.handler))


async def _settle(rounds: int = 20) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


async def test_in_flight_never_exceeds_limit_and_all_complete():
    gate = _Gate()
    client = _client(gate, limit=3)
    tasks = [asyncio.create_task(client.get_json("/api/domain")) for _ in range(10)]
    await _settle()
    assert gate.active == 3 and client._slots.waiting == 7
    gate.opened.set()
    await asyncio.gather(*tasks)
    await client.aclose()
    assert gate.peak == 3 and len(gate.seen) == 10
    assert client._slots.in_flight == 0 and client._slots.waiting == 0


async def test_sources_are_independent():
    """한 소스가 상한까지 막혀 있어도 다른 소스 호출은 바로 나간다."""
    busy, free = _Gate(), _Gate()
    free.opened.set()
    a = _client(busy, limit=2, url="http://bank.test")
    b = _client(free, limit=2, url="http://common.test")
    blocked = [asyncio.create_task(a.get_json("/api/domain")) for _ in range(4)]
    await _settle()
    assert busy.active == 2 and a._slots.waiting == 2
    assert await asyncio.wait_for(b.get_json("/api/domain"), 1.0) == {"result": []}
    busy.opened.set()
    await asyncio.gather(*blocked)
    await a.aclose()
    await b.aclose()


async def test_slot_wait_over_timeout_is_clear_source_unavailable_error():
    gate = _Gate()
    client = _client(gate, limit=1, timeout=0.05)
    holder = asyncio.create_task(client.get_json("/api/domain"))
    await _settle()
    with pytest.raises(ApmError) as info:
        await client.get_json("/api/instance", {"domain_id": 7})
    assert info.value.code == SOURCE_UNAVAILABLE
    assert "제니퍼 동시 호출 상한 대기 초과" in str(info.value)
    assert "1건" in str(info.value) and "/api/instance" in str(info.value)
    assert gate.seen == ["-"]  # 넘친 호출은 네트워크에 나가지 않았다
    gate.opened.set()
    await holder
    # 자리가 새지 않았다 — 다음 호출은 바로 나간다
    await client.get_json("/api/domain")
    await client.aclose()
    assert client._slots.in_flight == 0 and client._slots.waiting == 0


async def test_slot_wait_keeps_priority_order():
    """자리를 기다리는 동안 먼저 온 background가 나중 온 poller·interactive를 막지 않는다."""
    gate = _Gate()
    client = _client(gate, limit=1)
    holder = asyncio.create_task(client.get_json("/api/domain"))
    await _settle()

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
        await _settle(3)
    gate.opened.set()
    await asyncio.gather(holder, *tasks)
    await client.aclose()
    assert gate.seen == ["-", "4", "3", "1", "2"] and gate.peak == 1


async def test_cancel_after_handoff_passes_slot_on():
    """자리를 넘겨받은 직후 취소돼도 자리가 새지 않고 다음 대기자에게 간다."""
    slots = PrioritySlots(1)
    await slots.acquire()
    first = asyncio.create_task(slots.acquire(PRIORITY_INTERACTIVE))
    second = asyncio.create_task(slots.acquire(PRIORITY_BACKGROUND))
    await _settle(3)
    slots.release()  # first에게 넘긴다
    first.cancel()  # 깨어나기 전에 취소 — first가 받은 자리를 second에게 넘겨야 한다
    await asyncio.gather(first, return_exceptions=True)
    await asyncio.wait_for(second, 1.0)
    assert slots.in_flight == 1 and slots.waiting == 0
    slots.release()
    assert slots.in_flight == 0


def test_slots_reject_non_positive_limit():
    with pytest.raises(ValueError):
        PrioritySlots(0)


# ── 설정 · 연결 풀 ─────────────────────────────────────────


def test_config_default_global_and_per_source_override():
    assert load_config({}, dotenv=None).jennifer.max_inflight_per_source == 8
    single = load_config(
        {"JENNIFER_API_URL": "http://one.test", "JENNIFER_MAX_INFLIGHT_PER_SOURCE": "3"},
        dotenv=None,
    )
    assert [s.max_inflight_per_source for s in single.sources] == [3]
    multi = load_config(
        {
            "JENNIFER_SOURCES": '["bank", "legacy"]',
            "JENNIFER_BANK_API_URL": "http://bank.test",
            "JENNIFER_BANK_API_TOKEN": "tok-bank",
            "JENNIFER_BANK_MAX_INFLIGHT_PER_SOURCE": "2",
            "JENNIFER_LEGACY_API_URL": "http://legacy.test",
            "JENNIFER_LEGACY_API_TOKEN": "tok-legacy",
            "JENNIFER_MAX_INFLIGHT_PER_SOURCE": "6",
        },
        dotenv=None,
    )
    assert {s.source_id: s.max_inflight_per_source for s in multi.sources} == {
        "bank": 2,
        "legacy": 6,
    }


@pytest.mark.parametrize(
    "env",
    [
        {"JENNIFER_MAX_INFLIGHT_PER_SOURCE": "0"},
        {
            "JENNIFER_SOURCES": '["bank"]',
            "JENNIFER_BANK_API_URL": "http://bank.test",
            "JENNIFER_BANK_API_TOKEN": "tok-bank",
            "JENNIFER_BANK_MAX_INFLIGHT_PER_SOURCE": "-1",
        },
    ],
)
def test_config_rejects_non_positive_limit(env):
    with pytest.raises(ValueError, match="MAX_INFLIGHT_PER_SOURCE"):
        load_config(env, dotenv=None)


@pytest.mark.parametrize("value", ["0.5", "2.5", "abc"])
def test_config_rejects_non_integer_limit_with_key_name(value):
    with pytest.raises(ValueError, match="JENNIFER_MAX_INFLIGHT_PER_SOURCE는 정수"):
        load_config({"JENNIFER_MAX_INFLIGHT_PER_SOURCE": value}, dotenv=None)
    per_source = {
        "JENNIFER_SOURCES": '["bank"]',
        "JENNIFER_BANK_API_URL": "http://bank.test",
        "JENNIFER_BANK_API_TOKEN": "tok-bank",
        "JENNIFER_BANK_MAX_INFLIGHT_PER_SOURCE": value,
    }
    with pytest.raises(ValueError, match="JENNIFER_BANK_MAX_INFLIGHT_PER_SOURCE는 정수"):
        load_config(per_source, dotenv=None)


async def test_http_pool_matches_in_flight_limit():
    client = JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t", max_inflight_per_source=5)
    )
    pool = client._http()._transport._pool  # type: ignore[attr-defined]
    assert pool._max_connections == 5 and pool._max_keepalive_connections == 5
    await client.aclose()
