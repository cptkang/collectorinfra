"""유계 병렬 호출 — 도메인마다 1회 묻는 가벼운 조회를 겹쳐 부른다 (plans/207 W1 · D-296 ⑤).

- 결과는 **입력 순서대로** 돌려준다. `ApmError`는 값으로 담는다 — 호출자가 결과를 순서대로 훑으며
  종전 `try/except ApmError` 분기(실패 목록·`[한계]`)를 그대로 재현한다.
- 그 밖 예외는 전파하고 남은 호출은 취소한다. 호출자 취소(`CancelledError`)도 남은 호출을 취소하고
  전파한다(승격 전 취소 계약 — SPEC-apm-question-coverage §3.2).
- 자식 태스크는 호출 맥락(`current_scope` contextvar)을 물려받는다 — 작업의 슬롯 대기·호출 수·
  우선순위가 그대로 적용된다. 겹쳐 부르는 동안 맥락에 동시 폭을 신고한다(작업의 남은 예상 시간).
- 자격증명 검사 메모·가린 칸(`note`·`masked` — 클라이언트가 HTTP 1회마다 신고)은 항목마다 따로
  모았다가 결과를 다 받은 뒤 **입력 순서로** 바깥 맥락에 다시 신고한다 — 응답 도착 순서와 무관하게
  봉투 `limits`·고지가 순차 실행과 같다(plans/205 W2 · §5 공통 순서 위험). 그 밖 훅·속성(우선순위·
  호출자·호출 수·소요·대기·호출 계획·동시 폭)은 바깥 맥락 그대로다. 메모를 모으지 않는 맥락(작업 밖
  기본 맥락 — 경고 로그 · 공유 적재 맥락 — 인스턴스 명단 적재 #1은 이 계획 밖)은 감싸지 않고 그대로
  물려준다.
- `limit = 1`(또는 항목 1개)이면 태스크 없이 종전처럼 입력 순서대로 하나씩 부른다.
- 중단 조건(`stop` — 계약 위반처럼 호출자가 순서상 첫 건에서 올리는 오류)이 맞으면 남은 항목을
  부르지 않는다: 직렬은 거기서 멈추고, 동시는 아직 자리를 받지 못한 항목을 부르지 않는다(이미 나간
  호출은 끝까지 기다린다). 부르지 않은 칸에는 그 오류를 채워 길이를 항목 수와 같게 둔다 — 호출자는
  입력 순서상 첫 위반에서 올리므로 그 칸은 처리되지 않는다(plans/205 W2 교정).
- 항목마다 게이트가 다른 1단 구조(`gather_gated`)는 소스별 폭 묶음(`sources.gather_per_source`)이
  쓴다 — 메모 재신고·중단 조건이 소스를 넘어 전역 입력 순서로 맞는다.
- 폭은 소스 클라이언트의 동시 호출 상한(`JENNIFER_MAX_INFLIGHT_PER_SOURCE` · D-347)과 별개다 — 클라이언트가 소스별로
  다시 묶고, 속도 상한(호출 시작 간격)은 그대로라 초당 호출 수는 늘지 않는다.
- X-View 1분 조각처럼 무거운 루프에는 쓰지 않는다(뷰 서버 부하 — D-344).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable, Iterator
from typing import TypeVar

from apm_gateway.domain.call_context import CallScope, SharedLoadScope, current_scope, set_scope
from apm_gateway.domain.errors import CONTRACT_VIOLATION, ApmError

T = TypeVar("T")
R = TypeVar("R")
# 중단 조건 — 맞으면 남은 항목을 부르지 않는다(모듈 설명)
Stop = Callable[[ApmError], bool]


def contract_violation(e: ApmError) -> bool:
    """계약 위반(게이트웨이 버그) — 호출자가 결과를 훑다 첫 건에서 올린다(`limit_notice`)."""
    return e.code == CONTRACT_VIOLATION


# 메모를 모으지 않는 맥락 — 감싸지 않는다(모듈 설명)
_PASS_THROUGH = (CallScope, SharedLoadScope)


class _OrderedNotes(CallScope):
    """병렬 항목 1개의 맥락 — 메모·가린 칸은 모았다가 `flush`에서 바깥 맥락에 신고하고, 그 밖
    훅·속성은 바깥 맥락에 바로 넘긴다(`batch._ItemScope`·`SharedLoadScope`와 같은 위임 꼴).
    `flush` 뒤에 온 신고(항목이 시작한 공유 적재가 늦게 끝난 경우)는 바로 넘긴다."""

    def __init__(self, outer: CallScope) -> None:  # 우선순위는 바깥 맥락 것을 읽는다(속성 대신)
        self._outer = outer
        # 받은 순서대로 — 문자열 = 메모 · 목록 = 가린 칸 이름(None = 이미 신고함)
        self._held: list[str | list[str]] | None = []

    @property
    def priority(self) -> str:
        return self._outer.priority

    @priority.setter
    def priority(self, value: str) -> None:
        self._outer.priority = value

    @property  # type: ignore[override]
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

    def waited(self, source_id: str, slot_ms: float, throttle_ms: float) -> None:
        self._outer.waited(source_id, slot_ms, throttle_ms)

    def expect_calls(self, count: int) -> None:
        self._outer.expect_calls(count)

    def note(self, text: str) -> None:
        if self._held is None:
            self._outer.note(text)
        else:
            self._held.append(text)

    def masked(self, fields: Iterable[str]) -> None:
        if self._held is None:
            self._outer.masked(fields)
        else:
            self._held.append(list(fields))

    @contextlib.contextmanager
    def parallel(self, width: int) -> Iterator[None]:
        with self._outer.parallel(width):
            yield

    def flush(self) -> None:
        """모은 메모·가린 칸을 받은 순서대로 바깥 맥락에 신고한다(이후 신고는 바로 넘긴다)."""
        held, self._held = self._held or [], None
        for value in held:
            if isinstance(value, str):
                self._outer.note(value)
            else:
                self._outer.masked(value)


async def gather_bounded(
    items: Iterable[T],
    fn: Callable[[T], Awaitable[R]],
    limit: int,
    *,
    stop: Stop | None = None,
) -> list[R | ApmError]:
    """`fn(item)`을 최대 `limit`개까지 겹쳐 부르고 입력 순서대로 결과(또는 `ApmError`)를
    돌려준다."""
    seq = list(items)
    width = min(max(1, int(limit)), len(seq))
    if width <= 1:
        out: list[R | ApmError] = []
        for item in seq:
            try:
                out.append(await fn(item))
            except ApmError as e:
                out.append(e)
                if stop is not None and stop(e):  # 종전 직렬 루프처럼 여기서 멈춘다
                    out += [e] * (len(seq) - len(out))
                    break
        return out
    gate = asyncio.Semaphore(width)
    return await gather_gated(seq, fn, [gate] * len(seq), width, stop=stop)


async def gather_gated(
    seq: list[T],
    fn: Callable[[T], Awaitable[R]],
    gates: list[asyncio.Semaphore],
    width: int,
    *,
    stop: Stop | None = None,
) -> list[R | ApmError]:
    """항목마다 제 게이트(`gates[i]`)를 지나 `fn(item)`을 겹쳐 부르고 입력 순서대로 결과를
    돌려준다 — 항목 수만큼 태스크를 띄우고, `width`는 맥락에 신고하는 동시 폭이다."""
    outer = current_scope()
    scopes = [None if type(outer) in _PASS_THROUGH else _OrderedNotes(outer) for _ in seq]
    halted: list[ApmError] = []  # 중단 조건에 맞은 첫 오류(전 항목 공유)

    async def one(item: T, gate: asyncio.Semaphore, scope: _OrderedNotes | None) -> R | ApmError:
        if scope is not None:  # 태스크 맥락은 사본이라 이 태스크에만 걸린다
            set_scope(scope)
        async with gate:
            if halted:  # 자리를 받기 전에 중단됐다 — 부르지 않는다
                return halted[0]
            try:
                return await fn(item)
            except ApmError as e:
                if stop is not None and not halted and stop(e):
                    halted.append(e)
                return e

    with outer.parallel(width):
        tasks = [
            asyncio.create_task(one(item, gate, sc))
            for item, gate, sc in zip(seq, gates, scopes, strict=True)
        ]
        try:
            return list(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:  # 예외·취소로 끝나도 받은 메모는 버리지 않는다(입력 순서 · 침묵 금지)
            for sc in scopes:
                if sc is not None:
                    sc.flush()
