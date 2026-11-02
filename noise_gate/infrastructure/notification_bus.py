"""알람 알림 버스.

분석된 알람 이벤트를 구독 중인 SSE 클라이언트(웹 UI)에 브로드캐스트한다.
FastAPI app.state.alarm_bus에 싱글턴으로 저장된다.

(plans/207 트랙 C M-4) `queue_max`가 양수면 구독자 큐에 상한을 두고, 넘치면 그 구독자 큐의
**가장 오래된** 이벤트를 버린 뒤 새 이벤트를 넣는다 — 느린 구독자가 `publish`(알람 처리 경로)를
붙잡거나 큐가 끝없이 자라지 않게 한다. 버림은 구독자별로 세고, 구독자별 첫 버림 1회와 전역
`_DROP_SUMMARY_SECONDS` 주기마다 (전 구독자 합산) 요약 1줄만 WARNING으로 남긴다.
0(기본)이면 종전과 같은 무상한 큐다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# 버림 요약 경고 최소 간격(초) — 버림이 이어져도 경고는 이 주기에 1줄이다.
_DROP_SUMMARY_SECONDS = 60.0


class AlarmNotificationBus:
    """구독자(asyncio.Queue)에게 알람 이벤트를 브로드캐스트하는 인메모리 버스."""

    def __init__(self, queue_max: int = 0) -> None:
        """구독자 큐 상한을 받는다(0 이하 = 무상한 · 현행)."""
        self._queues: list[asyncio.Queue[dict[str, Any]]] = []
        self._queue_max = max(0, int(queue_max))
        # 구독자별 누적 버림 수와 마지막 요약 이후 버림 수(상한이 있을 때만 쓴다)
        self._dropped: dict[int, int] = {}
        self._dropped_since_summary = 0
        self._last_summary_at = time.monotonic()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_max)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass
        dropped = self._dropped.pop(id(q), 0)
        if dropped:
            logger.info("알림 버스 구독 종료 — 이 구독자의 누적 버림 %d건", dropped)

    def dropped_count(self, q: asyncio.Queue[dict[str, Any]]) -> int:
        """구독자 큐 하나의 누적 버림 수(상한 초과로 버린 가장 오래된 이벤트 수)."""
        return self._dropped.get(id(q), 0)

    async def publish(self, event: dict[str, Any]) -> None:
        if not self._queue_max:
            for q in self._queues:
                await q.put(event)
            return
        for q in self._queues:
            if q.full():
                q.get_nowait()  # 가장 오래된 이벤트를 버린다 — 화면 재동기화가 메운다
                self._note_drop(q)
            q.put_nowait(event)

    def _note_drop(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        """버림 1건을 센다 — 구독자별 첫 버림 경고 1회 + 전역 요약 주기마다 요약 1줄.

        첫 버림 경고는 요약 카운터·시각을 건드리지 않는다(다른 구독자의 버림 수가 요약에서 빠지지
        않게) — 요약은 모든 구독자의 버림을 합쳐 전역 `_DROP_SUMMARY_SECONDS` 주기로만 낸다.
        """
        count = self._dropped.get(id(q), 0) + 1
        self._dropped[id(q)] = count
        self._dropped_since_summary += 1
        now = time.monotonic()
        if count == 1:
            logger.warning(
                "알림 버스 구독자 큐 상한(%d) 초과 — 가장 오래된 이벤트를 버리기 시작 "
                "(느린 구독자 · 구독자 %d명)",
                self._queue_max,
                len(self._queues),
            )
        if now - self._last_summary_at >= _DROP_SUMMARY_SECONDS:
            logger.warning(
                "알림 버스 버림 요약 — 지난 요약 이후 %d건 · 버린 구독자 %d명 (상한 %d)",
                self._dropped_since_summary,
                len(self._dropped),
                self._queue_max,
            )
            self._dropped_since_summary = 0
            self._last_summary_at = now
