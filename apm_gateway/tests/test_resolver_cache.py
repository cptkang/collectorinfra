"""인스턴스 목록 캐시 — 단일 적재 · 만료 뒤 기존 명단 + 백그라운드 갱신 · 선적재.

운영 실측(2026-09-30): 제니퍼 도메인 약 350개 · 호출 상한 5회/초라 적재 1회 ≈ 70초. 빈 캐시에서
요청이 겹치면 각자 처음부터 적재해 호출 상한을 나눠 쓰며 140~160초가 걸렸고, 본체는 10초에 포기했다.
외부 접속 0 — 제니퍼 API 대역만 쓴다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import pytest

from apm_gateway.application import resolver as resolver_mod
from apm_gateway.application.resolver import InstanceResolver
from apm_gateway.domain.errors import SOURCE_UNAVAILABLE, ApmError

DOMAINS = (1000, 2000)


class FakeApi:
    """제니퍼 API 대역 — 호출 수를 세고, `gate`가 있으면 도메인 조회를 그 신호까지 붙잡는다."""

    def __init__(self) -> None:
        self.domain_calls = 0
        self.instance_calls = 0
        self.fail = False
        self.gate: asyncio.Event | None = None

    async def domains(self):
        self.domain_calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise ApmError(SOURCE_UNAVAILABLE, "뷰 서버 응답 없음")
        return [{"domain_id": d, "domain_name": f"d{d}"} for d in DOMAINS]

    async def instances(self, domain_id, domain_name=""):
        self.instance_calls += 1
        await asyncio.sleep(0)
        return [{"domain_id": domain_id, "instance_id": domain_id + 1,
                 "instance_name": f"was{domain_id}", "host_name": f"h{domain_id}"}]


@pytest.fixture
def clock():
    now = [1000.0]
    return now


def _resolver(api: FakeApi, now: list[float]) -> InstanceResolver:
    return InstanceResolver(api, {}, cache_seconds=600, clock=lambda: now[0])


async def test_concurrent_cold_callers_share_one_load(clock):
    api = FakeApi()
    api.gate = asyncio.Event()
    res = _resolver(api, clock)
    waiters = [asyncio.create_task(res.inventory()) for _ in range(5)]
    await asyncio.sleep(0)
    api.gate.set()
    results = await asyncio.gather(*waiters)
    assert all(r is results[0] for r in results)
    assert api.domain_calls == 1, "진행 중인 적재를 함께 기다려야 한다"
    assert api.instance_calls == len(DOMAINS)


async def test_expired_cache_answers_immediately_and_refreshes_once(clock):
    api = FakeApi()
    res = _resolver(api, clock)
    first = await res.inventory()
    clock[0] += 601
    api.gate = asyncio.Event()  # 갱신은 끝나지 않은 채로 둔다

    assert await res.inventory() is first, "만료 뒤에도 기존 명단으로 즉시 답한다"
    refresh = res._loading
    assert await res.inventory() is first
    assert res._loading is refresh, "진행 중인 갱신이 있으면 새로 걸지 않는다"
    await asyncio.sleep(0)  # 예약된 갱신이 시작된다
    assert api.domain_calls == 2, "백그라운드 갱신은 한 번만 건다"

    api.gate.set()
    refreshed = await res._loading
    assert refreshed is not first
    assert await res.inventory() is refreshed
    assert api.domain_calls == 2


async def test_failed_refresh_keeps_list_and_backs_off(clock, caplog):
    api = FakeApi()
    res = _resolver(api, clock)
    first = await res.inventory()
    clock[0] += 601
    api.fail = True

    with caplog.at_level(logging.WARNING, logger=resolver_mod.__name__):
        assert await res.inventory() is first
        with pytest.raises(ApmError):
            await res._loading
        await asyncio.sleep(0)  # 완료 콜백
    assert "갱신 실패" in caplog.text

    assert await res.inventory() is first, "갱신 실패 뒤에도 기존 명단을 유지한다"
    assert api.domain_calls == 2, "재시도 간격 안에서는 다시 적재하지 않는다"

    clock[0] += resolver_mod.REFRESH_RETRY_SECONDS + 1
    api.fail = False
    await res.inventory()
    await res._loading
    assert api.domain_calls == 3


async def test_cancelled_waiter_does_not_abort_the_load(clock):
    api = FakeApi()
    api.gate = asyncio.Event()
    res = _resolver(api, clock)
    waiter = asyncio.create_task(res.inventory())
    await asyncio.sleep(0)
    waiter.cancel()  # 본체가 호출 상한에 걸려 연결을 끊은 경우
    with contextlib.suppress(asyncio.CancelledError):
        await waiter

    api.gate.set()
    loaded = await res._loading
    assert await res.inventory() is loaded, "적재 결과가 캐시에 남아야 한다"
    assert api.domain_calls == 1


async def test_cold_failure_still_raises_and_next_call_retries(clock):
    api = FakeApi()
    api.fail = True
    res = _resolver(api, clock)
    with pytest.raises(ApmError):
        await res.inventory()
    with pytest.raises(ApmError):
        await res.inventory()
    assert api.domain_calls == 2, "명단이 없으면 요청마다 적재를 시도한다(종전 동작)"


async def test_staleness_note_only_after_expiry(clock):
    api = FakeApi()
    res = _resolver(api, clock)
    inv = await res.inventory()
    assert res.notes(inv) == []
    clock[0] += 3600
    notes = res.notes(inv)
    assert len(notes) == 1 and "60분 전 기준" in notes[0]


async def test_warm_up_fills_cache_and_swallows_failure(clock, caplog):
    api = FakeApi()
    res = _resolver(api, clock)
    await res.warm_up()
    assert api.domain_calls == 1
    await res.inventory()
    assert api.domain_calls == 1, "선적재한 명단을 쓴다"

    broken = FakeApi()
    broken.fail = True
    with caplog.at_level(logging.WARNING, logger=resolver_mod.__name__):
        await _resolver(broken, clock).warm_up()  # 예외를 올리지 않는다
    assert "선적재 실패" in caplog.text


def test_entrypoint_schedules_warm_up_in_background():
    """선적재는 기동을 붙잡지 않는다 — 서버 시작 전에 await 하지 않고 작업으로 띄운다."""
    source = (Path(__file__).resolve().parent.parent / "apm_gateway" / "__main__.py").read_text(
        encoding="utf-8")
    assert "asyncio.create_task(resolver.warm_up())" in source
    assert "await resolver.warm_up()" not in source
