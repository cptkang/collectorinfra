"""우선순위 속도 제어 — 소스 클라이언트 1개의 호출 시작 간격을 지키며 대기열을 우선순위로 판다
(plans/134 W0-B N-14 · SPEC-apm-question-coverage §3.4).

- 속도(초당 호출 수)는 그대로다 — 호출 시작 간격 `1/rate`. 같은 토큰을 폴러·동기 호출·백그라운드
  작업이 나눈다.
- 대기 중 요청은 `poller` > `interactive` > `background` 순서로 나간다. 같은 순위는 먼저 온 순서다.
- 기아 방지(에이징): 대기 시간이 `aging_seconds`를 넘을 때마다 한 단계씩 올린다(최고 = `poller`
  순위). 백그라운드 요청도 결국 나간다.
- 대기자가 없고 간격이 지났으면 바로 나간다(대기열 태스크를 만들지 않는다).
- 동시 실행 상한(`PrioritySlots` · plans/194 W4b · D-347): 속도 제어는 시작 간격만 지켜 응답이
  느리면 떠 있는 요청이 `rate × 지연`만큼 쌓인다. 자리 대기도 같은 우선순위·에이징으로 판다.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from apm_gateway.domain.call_context import PRIORITY_INTERACTIVE, PRIORITY_RANK


@dataclass
class _Waiter:
    rank: int
    seq: int
    since: float
    future: asyncio.Future[None] = field(repr=False)


def _effective_rank(waiter: _Waiter, now: float, aging: float) -> int:
    """에이징을 반영한 순위 — `aging`초 기다릴 때마다 한 단계 올린다(최고 0)."""
    if aging <= 0:
        return waiter.rank
    return max(0, waiter.rank - int((now - waiter.since) // aging))


class PriorityThrottle:
    def __init__(
        self,
        rate_per_sec: float,
        aging_seconds: float = 10.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = 1.0 / float(rate_per_sec) if rate_per_sec and rate_per_sec > 0 else 0.0
        self._aging = float(aging_seconds)
        self._clock = clock
        self._next_start = 0.0
        self._waiters: list[_Waiter] = []
        self._seq = itertools.count()
        self._dispatcher: asyncio.Task[None] | None = None

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    def _effective(self, waiter: _Waiter, now: float) -> int:
        return _effective_rank(waiter, now, self._aging)

    async def acquire(self, priority: str = PRIORITY_INTERACTIVE) -> None:
        """호출 1회의 시작 차례를 기다린다(속도 제한이 없으면 바로 돌아온다)."""
        if self._interval <= 0:
            return
        now = self._clock()
        if not self._waiters and now >= self._next_start:
            self._next_start = now + self._interval
            return
        waiter = _Waiter(
            PRIORITY_RANK.get(priority, PRIORITY_RANK[PRIORITY_INTERACTIVE]),
            next(self._seq),
            now,
            asyncio.get_running_loop().create_future(),
        )
        self._waiters.append(waiter)
        if self._dispatcher is None or self._dispatcher.done():
            self._dispatcher = asyncio.create_task(self._dispatch())
        try:
            await waiter.future
        except asyncio.CancelledError:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            raise

    async def _dispatch(self) -> None:
        while self._waiters:
            wait = self._next_start - self._clock()
            if wait > 0:
                await asyncio.sleep(wait)
            if not self._waiters:
                break
            now = self._clock()
            best = min(self._waiters, key=lambda w: (self._effective(w, now), w.seq))
            self._waiters.remove(best)
            self._next_start = max(now, self._next_start) + self._interval
            if not best.future.done():
                best.future.set_result(None)


class PrioritySlots:
    """동시 실행 자리 `limit`개 — 자리가 비면 대기자 중 우선순위(에이징 반영)가 가장 높은 것에
    넘긴다.

    `asyncio.Semaphore`는 먼저 온 순서라 background 요청이 쌓이면 poller·interactive가 그 뒤에
    선다 — 그래서 속도 제어와 같은 순위 규칙으로 판다(plans/194 W4b · D-347).
    """

    def __init__(
        self,
        limit: int,
        aging_seconds: float = 10.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0:
            raise ValueError("동시 실행 상한은 0보다 커야 한다")
        self.limit = int(limit)
        self._aging = float(aging_seconds)
        self._clock = clock
        self._held = 0
        self._waiters: list[_Waiter] = []
        self._seq = itertools.count()

    @property
    def in_flight(self) -> int:
        return self._held

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    async def acquire(self, priority: str = PRIORITY_INTERACTIVE) -> None:
        """자리 하나를 얻는다(빈 자리가 있고 대기자가 없으면 바로 돌아온다). 받은 자리는 반드시
        `release`로 돌려준다."""
        if self._held < self.limit and not self._waiters:
            self._held += 1
            return
        waiter = _Waiter(
            PRIORITY_RANK.get(priority, PRIORITY_RANK[PRIORITY_INTERACTIVE]),
            next(self._seq),
            self._clock(),
            asyncio.get_running_loop().create_future(),
        )
        self._waiters.append(waiter)
        try:
            await waiter.future
        except asyncio.CancelledError:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            elif waiter.future.done() and not waiter.future.cancelled():
                self.release()  # 자리를 넘겨받은 직후 취소(대기 상한 초과 등) — 다음 대기자에게
            raise

    def release(self) -> None:
        """자리를 돌려준다 — 대기자가 있으면 자리 수를 줄이지 않고 바로 넘긴다."""
        now = self._clock()
        while self._waiters:
            best = min(self._waiters, key=lambda w: (_effective_rank(w, now, self._aging), w.seq))
            self._waiters.remove(best)
            if not best.future.done():
                best.future.set_result(None)
                return
        self._held -= 1
