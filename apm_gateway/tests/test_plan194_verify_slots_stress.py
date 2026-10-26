"""plans/194 W4b 독립 검증 — `PrioritySlots` 취소·대기 초과 경합 스트레스(verifier 작성).

무작위 우선순위·대기 상한(`wait_for`)·밖 취소·보유 시간이 섞여도
- 동시 보유 수 ≤ 상한
- 끝나면 `in_flight == 0`·`waiting == 0`(자리 누수·이중 반환 0)
실 네트워크 0.
"""

from __future__ import annotations

import asyncio
import random

import pytest
from apm_gateway.adapters.throttle import PrioritySlots
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_POLLER,
)

_PRIOS = (PRIORITY_POLLER, PRIORITY_INTERACTIVE, PRIORITY_BACKGROUND)


@pytest.mark.parametrize("seed", range(6))
async def test_random_cancel_and_timeouts_keep_slots_balanced(seed: int) -> None:
    rng = random.Random(seed)
    limit = 3
    slots = PrioritySlots(limit, aging_seconds=0.02)
    held = {"now": 0, "peak": 0}

    async def user() -> None:
        await asyncio.sleep(rng.uniform(0, 0.02))
        try:
            await asyncio.wait_for(slots.acquire(rng.choice(_PRIOS)), rng.uniform(0.001, 0.05))
        except TimeoutError:
            return
        held["now"] += 1
        held["peak"] = max(held["peak"], held["now"])
        try:
            await asyncio.sleep(rng.uniform(0, 0.01))
        finally:
            held["now"] -= 1
            slots.release()

    tasks = [asyncio.create_task(user()) for _ in range(300)]
    for _ in range(40):
        await asyncio.sleep(rng.uniform(0, 0.003))
        victim = rng.choice(tasks)
        victim.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert held["peak"] <= limit
    assert held["now"] == 0
    assert (slots.in_flight, slots.waiting) == (0, 0), (slots.in_flight, slots.waiting)
