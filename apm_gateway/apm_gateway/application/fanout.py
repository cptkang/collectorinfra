"""도구 안 독립 API 호출 동시 실행 — 순서 보존 헬퍼 (plans/205 W2 · D-356 ①).

- `gather_ordered(items, call, limit=n)`은 항목마다 `call(item)`을 최대 `n`개 동시에 돌리고 결과를
  **입력 순서로** `(항목, 결과 또는 ApmError)` 목록으로 돌려준다. 호출부는 이 목록을 종전 루프와
  같은 순서로 처리한다(행·`[한계]`·실패 고지) — 봉투가 순차 실행과 같다.
- `limit <= 1`(또는 항목 1개 이하)이면 태스크 없이 차례로 await한다 — 현행 호출 순서와 같다
  (`APM_FANOUT_CONCURRENCY=1` 되돌리기 · 녹화 응답 순서에 기대는 테스트 보호). 이때 계약 위반
  (`CONTRACT_VIOLATION`)을 받으면 그 항목까지만 담고 멈춘다(`stop_on_contract_violation`) — 종전
  루프가 그 자리에서 던져 뒤 항목을 부르지 않던 것과 호출 수·봉투가 같다. 종전 루프가 계약 위반도
  잡고 계속 돌던 호출부는 `False`를 넘긴다.
- 동시 경로도 계약 위반을 받으면(`stop_on_contract_violation`) 그 항목보다 **입력 순서가 뒤인**
  항목은 자리를 얻은 뒤 호출하지 않고, 결과는 입력 순서상 첫 계약 위반 항목까지 잘라 준다 — 차례
  경로와 길이·형식이 같다. 그보다 앞 항목은 계속 부르고 모두 기다린다(호출부가 그 앞 결과를 처리한
  뒤 계약 위반 자리에서 던지므로). 이미 떠난 호출은 거두지 않는다(§5 허용 차이 — 호출 수만 많을
  수 있다).
- **중첩은 차례로**(plans/205 W3 · R-1): 동시 항목 안에서 다시 부른 `gather_ordered`는 `limit`와
  무관하게 차례 경로(위와 같은 코드 · `stop_on_contract_violation` 의미 그대로)로 돈다 — 배치 항목이
  동시면 항목 안 하위 도구의 호출은 종전처럼 차례로, 배치가 아니면 도구 안 호출이 동시다. 작업 1건의
  미결 호출을 `limit` 이하로 묶어 곱셈(항목 × 도구)을 없앤다: 자리 대기 ≈ 대기열 깊이 × 응답 지연 /
  자리이므로, 작업 4 × `limit` 4 = 16이면 자리 8 · 지연 3~5s에서 대기가 API timeout(30s) 안에 들지만
  항목 4 × 도구 4 = 작업당 16이면 64건이 줄 서 30s를 넘는다(W3 경계 측정). 표지는 모듈 ContextVar로
  항목 태스크 맥락 사본에만 서므로 바깥 맥락에 새지 않는다. 공유 명단 적재는 새 맥락에서 돌아
  (`resolver`) 표지를 물려받지 않는다.
- `ApmError`만 결과로 잡는다. 그 밖 예외는 나머지 호출을 취소한 뒤 다시 올린다.
- 호출 맥락: 동시 항목은 항목별 맥락(`_FanoutScope`)에서 돈다. 호출 훅(작업 슬롯 대기·호출 수·
  소요·대기 시간)·우선순위·호출자 키·호출 계획(`expect_calls`)은 바깥 맥락 객체에 그대로 닿고
  (승격으로 우선순위가 바뀌면 진행 중 항목도 다음 호출부터 따른다), 자격증명 검사 메모(`note`)·가린
  칸(`masked`)만 항목별로 모았다가 끝난 뒤 **입력 순서로** 바깥 맥락에 다시 신고한다 — 응답 도착
  순서가 봉투 `limits` 순서를 바꾸지 않게. 다시 신고한 뒤 들어오는 메모(공유 적재가 늦게 끝난
  경우)는 곧바로 바깥으로 보낸다.
- 상한은 새로 만들지 않는다 — 모든 호출은 클라이언트의 자리 → 속도 제어 → HTTP를 지난다(D-347 ③).
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar, cast

from apm_gateway.domain.call_context import CallScope, current_scope, use_scope
from apm_gateway.domain.errors import CONTRACT_VIOLATION, ApmError

I = TypeVar("I")  # noqa: E741
R = TypeVar("R")

# 동시 항목 태스크 안인가 — 그 안의 `gather_ordered`는 차례로 돈다(중첩 곱셈 방지 · W3 R-1)
_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar("apm_fanout_active", default=False)


class _FanoutScope(CallScope):
    """동시 항목 1개의 맥락 — 훅·우선순위·호출 계획은 바깥 그대로, 메모·가린 칸은 모아 둔다."""

    def __init__(self, outer: CallScope) -> None:  # 우선순위는 바깥 것을 읽는다
        self._outer = outer
        self._notes: list[str] = []
        self._masked: list[list[str]] = []
        self._replayed = False

    @property
    def priority(self) -> str:
        return self._outer.priority

    @priority.setter
    def priority(self, value: str) -> None:
        self._outer.priority = value

    @property
    def caller(self) -> str:
        return self._outer.caller

    async def before_call(self, source_id: str = "") -> None:
        await self._outer.before_call(source_id)

    def count_call(self, source_id: str = "", *, shared: bool = False) -> None:
        self._outer.count_call(source_id, shared=shared)

    def after_call(
        self, source_id: str, seconds: float, *, group: str = "", shared: bool = False
    ) -> None:
        self._outer.after_call(source_id, seconds, group=group, shared=shared)

    def after_wait(self, source_id: str, *, slot_ms: float, throttle_ms: float) -> None:
        self._outer.after_wait(source_id, slot_ms=slot_ms, throttle_ms=throttle_ms)

    def expect_calls(self, count: int) -> None:
        self._outer.expect_calls(count)

    def note(self, text: str) -> None:
        if self._replayed:
            self._outer.note(text)
        else:
            self._notes.append(text)

    def masked(self, fields: Iterable[str]) -> None:
        if self._replayed:
            self._outer.masked(fields)
        else:
            self._masked.append(list(fields))

    def replay(self) -> None:
        """모은 메모·가린 칸을 바깥 맥락에 신고한다(이후 신고는 곧바로 바깥으로)."""
        self._replayed = True
        for text in self._notes:
            self._outer.note(text)
        for fields in self._masked:
            self._outer.masked(fields)
        self._notes.clear()
        self._masked.clear()


async def gather_ordered(
    items: Iterable[I],
    call: Callable[[I], Awaitable[R]],
    *,
    limit: int,
    stop_on_contract_violation: bool = True,
) -> list[tuple[I, R | ApmError]]:
    """항목마다 `call`을 최대 `limit`개 동시에 돌려 입력 순서로 (항목, 결과|ApmError)를 준다.
    이미 동시 항목 안이면 차례로 돈다. 계약 위반을 받으면(`stop_on_contract_violation`) 입력
    순서상 첫 계약 위반 항목까지만 준다 — 동시 경로는 그 뒤 항목을 새로 띄우지 않는다."""
    units = list(items)
    if limit <= 1 or len(units) <= 1 or _ACTIVE.get():
        out: list[tuple[I, R | ApmError]] = []
        for unit in units:
            try:
                out.append((unit, await call(unit)))
            except ApmError as e:
                out.append((unit, e))
                if stop_on_contract_violation and e.code == CONTRACT_VIOLATION:
                    break
        return out

    outer = current_scope()
    gate = asyncio.Semaphore(limit)
    scopes = [_FanoutScope(outer) for _ in units]
    # 계약 위반을 받은 항목 중 입력 순서가 가장 앞인 자리 — 그보다 뒤 항목은 새로 띄우지 않는다
    cut = len(units)

    async def run(index: int) -> R | ApmError | None:
        nonlocal cut
        _ACTIVE.set(True)  # 이 태스크 맥락 사본에만 선다
        async with gate:
            if index > cut:  # 앞 항목이 계약 위반 — 이 결과는 잘려 쓰이지 않는다
                return None
            with use_scope(scopes[index]):
                try:
                    return await call(units[index])
                except ApmError as e:
                    if stop_on_contract_violation and e.code == CONTRACT_VIOLATION:
                        cut = min(cut, index)
                    return e

    tasks = [asyncio.create_task(run(i)) for i in range(len(units))]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        for scope in scopes:
            scope.replay()
    # 입력 순서상 첫 계약 위반 항목까지 — 그 앞 항목은 모두 불렀다(`cut`은 줄기만 한다)
    end = min(cut + 1, len(units))
    return list(zip(units[:end], cast("list[R | ApmError]", results[:end]), strict=True))
