"""우선순위 속도 제어 — 소스 클라이언트 1개의 호출 시작 간격을 지키며 대기열을 우선순위로 판다
(plans/134 W0-B N-14 · SPEC-apm-question-coverage §3.4).

- 속도(초당 호출 수)는 그대로다 — 호출 시작 간격 `1/rate`. 같은 토큰을 폴러·동기 호출·백그라운드
  작업이 나눈다.
- 대기 중 요청은 `poller` > `interactive` > `background` 순서로 나간다. 같은 순위는 먼저 온 순서다.
- 기아 방지(에이징): 대기 시간이 `aging_seconds`를 넘을 때마다 한 단계씩 올린다(최고 = `poller`
  순위). 백그라운드 요청도 결국 나간다.
- 대기자가 없고 간격이 지났으면 바로 나간다(대기열 태스크를 만들지 않는다).
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
        if self._aging <= 0:
            return waiter.rank
        return max(0, waiter.rank - int((now - waiter.since) // self._aging))

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
