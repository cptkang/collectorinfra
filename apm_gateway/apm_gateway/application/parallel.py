"""유계 병렬 호출 — 도메인마다 1회 묻는 가벼운 조회를 겹쳐 부른다 (plans/197 W1 · D-296 ⑤).

- 결과는 **입력 순서대로** 돌려준다. `ApmError`는 값으로 담는다 — 호출자가 결과를 순서대로 훑으며
  종전 `try/except ApmError` 분기(실패 목록·`[한계]`)를 그대로 재현한다.
- 그 밖 예외는 전파하고 남은 호출은 취소한다. 호출자 취소(`CancelledError`)도 남은 호출을 취소하고
  전파한다(승격 전 취소 계약 — SPEC-apm-question-coverage §3.2).
- 자식 태스크는 호출 맥락(`current_scope` contextvar)을 물려받는다 — 작업의 슬롯 대기·호출 수·
  우선순위가 그대로 적용된다. 겹쳐 부르는 동안 맥락에 동시 폭을 신고한다(작업의 남은 예상 시간).
- `limit = 1`(또는 항목 1개)이면 태스크 없이 종전처럼 입력 순서대로 하나씩 부른다.
- 폭은 소스 클라이언트의 동시 호출 상한(`JENNIFER_MAX_INFLIGHT_PER_SOURCE` · D-347)과 별개다 — 클라이언트가 소스별로
  다시 묶고, 속도 상한(호출 시작 간격)은 그대로라 초당 호출 수는 늘지 않는다.
- X-View 1분 조각처럼 무거운 루프에는 쓰지 않는다(뷰 서버 부하 — D-344).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar

from apm_gateway.domain.call_context import current_scope
from apm_gateway.domain.errors import ApmError

T = TypeVar("T")
R = TypeVar("R")


async def gather_bounded(
    items: Iterable[T], fn: Callable[[T], Awaitable[R]], limit: int
) -> list[R | ApmError]:
    """`fn(item)`을 최대 `limit`개까지 겹쳐 부르고 입력 순서대로 결과(또는 `ApmError`)를
    돌려준다."""
    seq = list(items)
    width = min(max(1, int(limit)), len(seq))
    out: list[R | ApmError] = []
    if width <= 1:
        for item in seq:
            try:
                out.append(await fn(item))
            except ApmError as e:
                out.append(e)
        return out

    gate = asyncio.Semaphore(width)

    async def one(item: T) -> R | ApmError:
        async with gate:
            try:
                return await fn(item)
            except ApmError as e:
                return e

    with current_scope().parallel(width):
        tasks = [asyncio.create_task(one(item)) for item in seq]
        try:
            return list(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
