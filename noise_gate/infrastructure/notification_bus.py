"""알람 알림 버스.

분석된 알람 이벤트를 구독 중인 SSE 클라이언트(웹 UI)에 브로드캐스트한다.
FastAPI app.state.alarm_bus에 싱글턴으로 저장된다.
"""

from __future__ import annotations

import asyncio


class AlarmNotificationBus:
    """구독자(asyncio.Queue)에게 알람 이벤트를 브로드캐스트하는 인메모리 버스."""

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    async def publish(self, event: dict) -> None:
        for q in self._queues:
            await q.put(event)
