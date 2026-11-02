"""plans/207 트랙 C M-4 — 실시간 알림 버스 구독자 큐 상한(`NOISE_ALARM_BUS_QUEUE_MAX`).

- 0(기본): 무상한 큐 · 모든 이벤트 보존(현행과 같다).
- 양수: 넘치면 그 구독자 큐의 가장 오래된 이벤트를 버리고 최신을 넣는다 · publish가 막히지 않는다 ·
  구독자별 버림 수를 센다 · 경고는 첫 버림 1회 + 요약 주기마다 1줄.
"""

from __future__ import annotations

import asyncio
import logging

from noise_gate.infrastructure import notification_bus as bus_mod
from noise_gate.infrastructure.notification_bus import AlarmNotificationBus


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_zero_is_unbounded_and_keeps_everything():
    async def _main():
        bus = AlarmNotificationBus()
        q = bus.subscribe()
        assert q.maxsize == 0
        for i in range(500):
            await bus.publish({"i": i})
        return bus, q

    bus, q = asyncio.run(_main())
    assert [e["i"] for e in _drain(q)] == list(range(500))
    assert bus.dropped_count(q) == 0


def test_bounded_drops_oldest_keeps_latest_and_counts():
    async def _main():
        bus = AlarmNotificationBus(queue_max=3)
        slow = bus.subscribe()
        fast = bus.subscribe()
        got: list[int] = []
        for i in range(6):
            # publish는 느린 구독자 때문에 막히지 않는다(상한 초과는 즉시 버림)
            await asyncio.wait_for(bus.publish({"i": i}), timeout=0.5)
            got.append(fast.get_nowait()["i"])  # 빠른 구독자는 바로 소비
        return bus, slow, fast, got

    bus, slow, fast, got = asyncio.run(_main())
    assert [e["i"] for e in _drain(slow)] == [3, 4, 5]
    assert bus.dropped_count(slow) == 3
    assert got == list(range(6)) and bus.dropped_count(fast) == 0


def test_drop_warnings_are_rate_limited(caplog, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(bus_mod.time, "monotonic", lambda: clock["t"])

    async def _publish(bus, n):
        for i in range(n):
            await bus.publish({"i": i})

    bus = AlarmNotificationBus(queue_max=2)
    q = bus.subscribe()
    with caplog.at_level(logging.WARNING, logger=bus_mod.__name__):
        asyncio.run(_publish(bus, 50))  # 버림 48건 — 경고는 첫 버림 1줄뿐
        first = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(first) == 1 and "버리기 시작" in first[0].getMessage()

        clock["t"] += bus_mod._DROP_SUMMARY_SECONDS
        asyncio.run(_publish(bus, 10))  # 주기가 지나면 요약 1줄
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 2 and "요약" in warns[1].getMessage()
    assert bus.dropped_count(q) == 58


def test_unsubscribe_clears_drop_count():
    async def _main():
        bus = AlarmNotificationBus(queue_max=1)
        q = bus.subscribe()
        await bus.publish({"i": 0})
        await bus.publish({"i": 1})
        assert bus.dropped_count(q) == 1
        bus.unsubscribe(q)
        await bus.publish({"i": 2})  # 구독 해제 뒤에는 넣지 않는다
        return bus, q

    bus, q = asyncio.run(_main())
    assert bus.dropped_count(q) == 0
    assert [e["i"] for e in _drain(q)] == [1]
