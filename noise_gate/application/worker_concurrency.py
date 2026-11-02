"""알람 워커 동시 처리의 순서 장치 (plans/207 트랙 B W2 · C-2·C-3 — N≥2 경로 전용).

`ALARM_WORKER_MAX_CONCURRENCY`가 2 이상일 때만 워커가 쓴다. 1(기본)이면 이 모듈을 거치지 않는다.

알람 1건의 구간(P1 파싱 · P2 서버명 역조회 · P3 게이트 사전 판정 · P4 그래프 · P5 후처리 ·
P6 ACK) 중 P1·P2는 메시지별로 동시에, **P3는 수신 순서대로 한 건씩**, P4~P6는 다시 동시에 돈다.

- 순번 — 메시지를 읽은 순서로 번호를 매기고, P3에는 그 번호 순서로만 들어간다(앞지르기 없음 · Q3).
  P3 안의 await(위상 그래프 로드·주석 분류기·해소 발행)까지 순번이 감싼다. 조기 반환·예외·취소에도
  `StageSlot.finish`가 순번을 넘겨 뒤 건이 막히지 않는다.
- 충돌 키 — P3에 들어가기 전, P3를 지났지만 P5가 끝나지 않은 알람과 키가 하나라도 겹치면 그 알람의
  P5가 끝날 때까지 기다린다(키 정의는 `conflict_keys`). 순번을 쥔 채 기다리므로 뒤 건도 기다린다.
- 전역 배리어 — 호출자가 준 판정(`barrier`)이 참이면 진행 중 전부의 P5를 기다린다(PAGE 레지스트리의
  주기 정리·키 상한처럼 호스트를 가로지르는 정리).
- 판정 시각 — 시계 함수를 받으면 판정할 때마다 다시 읽는다(대기 중 정리 기한을 넘겨도 놓치지
  않는다). 들어간 순간의 값이 `StageSlot.entered_at`이며, 워커는 이를 그 알람의 P3 시각으로 쓴다.

동시 처리 태스크 집합(`SerialStageGate.tasks`)은 한곳에 둔다 — 종료 드레인(W3)이 이 집합을 쓴다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Hashable, Sequence
from typing import Any

from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.cross_source import episode_key
from noise_gate.domain.page_review import fanout_key_of, host_key_of

logger = logging.getLogger(__name__)

#: 동시 처리 상한 범위(`1 ≤ N ≤ 16`) — 밖은 경계값으로 자른다(C-4).
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 16

#: 전역 배리어 판정 — (다음 알람의 now, 진행 중 알람들의 진입 시각 목록) → 진행 중 전부를 기다릴지.
BarrierCheck = Callable[[float, Sequence[float]], bool]
#: 판정 시각 — 고정값 또는 판정마다 부르는 시계 함수.
Clock = float | Callable[[], float]


def resolve_max_concurrency(raw: Any) -> int:
    """설정값 → 동시 처리 상한. 정수가 아니면 1, 범위 밖은 경계값으로 자르고 WARNING 1줄."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("알람 워커 동시 처리 상한 값 오류 → 1(직렬): raw=%r", raw)
        return MIN_CONCURRENCY
    clamped = min(max(value, MIN_CONCURRENCY), MAX_CONCURRENCY)
    if clamped != value:
        logger.warning(
            "알람 워커 동시 처리 상한 범위(%d~%d) 밖 → %d로 자름: 설정=%d",
            MIN_CONCURRENCY, MAX_CONCURRENCY, clamped, value,
        )
    return clamped


def conflict_keys(
    event: AlarmEvent, *, fanout: bool, episodes: bool
) -> frozenset[Hashable]:
    """P2(서버명 역조회) 뒤 이벤트의 충돌 키 집합(plans/207 §1.4 「충돌 키 집합」).

    - 호스트 키 `host_key_of(db_id, server_name or hostname)` — 항상
    - 캐시 키 `(db_id, server_name, alarm_name)` — 항상(server_name이 빈 알람의 노이즈 캐시 공유)
    - 동시발생 키 `fanout_key_of(db_id, alarm_name)` — `fanout`(PAGE 레지스트리 있음)일 때
    - 사건 키 `episode_key(zone, host_key)` — `episodes`(사건 추적기 있음)이고 호스트 키가 있을 때.
      존은 사건 추적기와 같은 규칙(서버 식별의 존 · 없으면 빈 값)이다.
    """
    keys: set[Hashable] = {
        ("host", *host_key_of(event.db_id, event.server_name or event.hostname)),
        ("cache", event.db_id or "", event.server_name or "", event.alarm_name or ""),
    }
    if fanout:
        keys.add(("fanout", *fanout_key_of(event.db_id, event.alarm_name)))
    if episodes and event.host_key:
        identity = event.server_identity
        zone = (identity.zone if identity is not None else "") or ""
        keys.add(("episode", episode_key(zone, event.host_key)))
    return frozenset(keys)


class SerialStageGate:
    """P3 순번 · 충돌 키 대기 · 전역 배리어 · 진행 중 태스크 집합(소비 루프 1개당 1개)."""

    def __init__(self) -> None:
        #: 읽었으나 끝나지 않은 처리 태스크 — 백프레셔(≤ N)와 종료 정리의 단일 출처.
        self.tasks: set[asyncio.Task[None]] = set()
        self._next_ticket = 0
        self._turn = 0
        self._passed: set[int] = set()
        # P3를 지났고 P5가 끝나지 않은 알람 — 순번 → (충돌 키, P3 진입 시각).
        self._held: dict[int, tuple[frozenset[Hashable], float]] = {}
        self._wake = asyncio.Event()

    def issue(self) -> StageSlot:
        """수신 순서대로 순번을 매긴 자리 1개."""
        ticket = self._next_ticket
        self._next_ticket += 1
        return StageSlot(self, ticket)

    @property
    def held_count(self) -> int:
        """P3를 지났고 P5가 끝나지 않은 알람 수."""
        return len(self._held)

    async def _enter(
        self,
        ticket: int,
        keys: frozenset[Hashable],
        now: Clock,
        barrier: BarrierCheck | None,
    ) -> float:
        while True:
            if self._turn == ticket:
                at = now() if callable(now) else now  # 판정마다 현재 시각
                if self._clear(keys, at, barrier):
                    break
            wake = self._wake  # 판정과 대기 사이에 await가 없다 — 깨움을 놓치지 않는다
            await wake.wait()
        self._held[ticket] = (keys, at)
        return at

    def _clear(
        self, keys: frozenset[Hashable], now: float, barrier: BarrierCheck | None
    ) -> bool:
        if not self._held:
            return True
        if any(keys & held for held, _ in self._held.values()):
            return False
        return barrier is None or not barrier(now, [t for _, t in self._held.values()])

    def _pass(self, ticket: int) -> None:
        self._passed.add(ticket)
        advanced = False
        while self._turn in self._passed:
            self._passed.discard(self._turn)
            self._turn += 1
            advanced = True
        if advanced:
            self._notify()

    def _release(self, ticket: int) -> None:
        if self._held.pop(ticket, None) is not None:
            self._notify()

    def _notify(self) -> None:
        wake, self._wake = self._wake, asyncio.Event()
        wake.set()


class StageSlot:
    """알람 1건의 순번 자리 — `enter` → (P3) → `leave_serial` → (P4·P5) → `finish`."""

    __slots__ = ("_gate", "ticket", "entered_at", "_passed", "_finished")

    def __init__(self, gate: SerialStageGate, ticket: int) -> None:
        self._gate = gate
        self.ticket = ticket
        #: P3에 들어간 판정 시각(`enter` 전에는 None).
        self.entered_at: float | None = None
        self._passed = False
        self._finished = False

    async def enter(
        self, keys: frozenset[Hashable], now: Clock, barrier: BarrierCheck | None = None
    ) -> None:
        """순번이 오고, 충돌 키가 비고, 배리어가 거짓일 때까지 기다린 뒤 P3를 쥔다.

        `now`가 시계 함수면 판정할 때마다 다시 부르고, 통과한 판정의 시각을 `entered_at`에 둔다.
        """
        self.entered_at = await self._gate._enter(self.ticket, keys, now, barrier)

    def leave_serial(self) -> None:
        """P3를 놓는다(순번을 다음 건으로) — 충돌 키는 `finish`까지 쥔다. 두 번째 호출은 무시."""
        if not self._passed:
            self._passed = True
            self._gate._pass(self.ticket)

    def finish(self) -> None:
        """처리 끝(정상·조기 반환·예외·취소) — 순번을 넘기고 충돌 키를 놓는다(동기 · 멱등)."""
        if self._finished:
            return
        self._finished = True
        self.leave_serial()
        self._gate._release(self.ticket)
