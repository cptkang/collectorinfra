"""요청 마감 전파 — 처리 마감 · 조회 마감 · 호출 상한 (plans/119 T-1·T-2 · D-267 ⑥).

요청 하나를 세 시계로 잰다(D-267 ⑥⑦).

- **처리 마감**(`request_deadline`): 첫 답변(표 또는 첫 토큰)이 나가야 하는 시각이다. 라우트가
  `time.monotonic()` 기준으로 매 턴 입력 상태에 싣는다(D-266 ④의 값을 그대로 재사용한다 — 새로
  만들지 않는다).
- **조회 마감** = 처리 마감 − 서술 예약(`API_ANSWER_RESERVE_SEC`). 조회 단계(스키마 분석·SQL
  생성/재생성·재계획)는 이 시각을 넘겨 새 LLM 작업을 시작하지 않는다.
- **전달 상한**: 첫 답변 뒤의 토큰 간 idle·전달 연장 상한이다. 판정은 SSE 라우트(`query.py`)가
  한다. 이 모듈은 최종 사용자 응답 스트림(`delivery_phase`)의 호출 상한을 처리 마감 + 전달 연장
  (스트림 라우트가 묶은 값 · 비스트림은 0)까지 넓혀, 첫 답변 뒤를 라우트 판정에 맡긴다(G-7).

LLM 클라이언트는 그래프 상태를 받지 않는다. 그래서 라우트가 같은 마감을 ContextVar로 묶는다
(`bind_request_deadline`). asyncio 태스크는 생성 시점의 컨텍스트를 복사하므로 그래프 노드와
서브에이전트 태스크까지 전파된다. **묶이지 않은 컨텍스트(CLI·단위 테스트·옛 체크포인트)는 모든
함수가 종전 동작을 돌려준다** — 상한은 설정값 그대로, 판정은 "시간 충분"이다.

`utils` 계층이라 설정을 읽지 않는다(`scripts/arch_check.py` — utils는 외부 패키지만). 서술 예약
초 수는 호출부가 설정에서 읽어 넘긴다.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

#: 마감을 이미 넘긴 뒤에 시작된 호출에 주는 최소 상한(초). 0·음수 timeout 은 라이브러리마다
#: 뜻이 달라(무한 대기로 읽는 곳이 있다) 양수 하한을 둔다.
MIN_CALL_TIMEOUT_SEC = 1.0

# (처리 마감 monotonic 초, 서술 예약 초, 전달 연장 초). None 이면 마감 없음(종전 동작).
_BOUND: ContextVar[tuple[float, float, float] | None] = ContextVar(
    "collectorinfra_request_deadline", default=None
)
# 서술(요약) 단계 표지 — 참이면 호출 상한에서 서술 예약을 빼지 않는다(예약분이 곧 이 단계의 몫이다).
_ANSWER_PHASE: ContextVar[bool] = ContextVar("collectorinfra_answer_phase", default=False)
# 전달 단계 표지 — 최종 사용자 응답 스트림. 첫 답변 뒤는 라우트의 idle·전체 상한이 끊으므로 호출
# 상한은 처리 마감 + 전달 연장(G-7 무한 대기 방지 상한)까지다.
_DELIVERY_PHASE: ContextVar[bool] = ContextVar("collectorinfra_delivery_phase", default=False)


def bind_request_deadline(
    deadline: float | None, reserve_sec: float = 0.0, grace_sec: float = 0.0
) -> Token[tuple[float, float, float] | None]:
    """이 컨텍스트(와 이후 생성되는 태스크)에 요청 마감을 묶는다. 반환 토큰으로 `unbind` 한다.

    `grace_sec`(전달 연장)은 스트림 라우트만 넘긴다 — 비스트림은 처리 상한이 곧 전체 상한이다.
    """
    value = None if deadline is None else (
        float(deadline), max(0.0, float(reserve_sec or 0)), max(0.0, float(grace_sec or 0)),
    )
    return _BOUND.set(value)


def unbind_request_deadline(token: Token[tuple[float, float, float] | None]) -> None:
    """`bind_request_deadline` 을 되돌린다."""
    _BOUND.reset(token)


def bound_deadline() -> tuple[float, float] | None:
    """묶인 (처리 마감, 서술 예약) — 없으면 None."""
    bound = _BOUND.get()
    return None if bound is None else (bound[0], bound[1])


@contextmanager
def answer_phase() -> Iterator[None]:
    """서술 단계 구간 — 이 안의 LLM 호출 상한은 조회 마감이 아니라 처리 마감까지다."""
    token = _ANSWER_PHASE.set(True)
    try:
        yield
    finally:
        _ANSWER_PHASE.reset(token)


def in_answer_phase() -> bool:
    return _ANSWER_PHASE.get()


@contextmanager
def delivery_phase() -> Iterator[None]:
    """최종 사용자 응답 스트림 구간 — 호출 상한은 처리 마감 + 전달 연장까지다(G-7)."""
    token = _DELIVERY_PHASE.set(True)
    try:
        yield
    finally:
        _DELIVERY_PHASE.reset(token)


def in_delivery_phase() -> bool:
    return _DELIVERY_PHASE.get()


def _now(now: float | None) -> float:
    return time.monotonic() if now is None else now


def remaining_sec(deadline: float | None, *, now: float | None = None) -> float | None:
    """마감까지 남은 초(음수 가능). 마감이 없으면 None."""
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
        return None
    return float(deadline) - _now(now)


def retrieval_deadline(deadline: float | None, reserve_sec: float) -> float | None:
    """조회 마감 = 처리 마감 − 서술 예약. 마감이 없으면 None."""
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
        return None
    return float(deadline) - max(0.0, float(reserve_sec or 0))


def retrieval_remaining(
    state: Mapping[str, Any], reserve_sec: float, *, now: float | None = None
) -> float | None:
    """상태의 `request_deadline` 기준 조회 마감까지 남은 초. 마감이 없으면 None(종전 동작)."""
    return remaining_sec(retrieval_deadline(state.get("request_deadline"), reserve_sec), now=now)


def has_time_for(
    state: Mapping[str, Any],
    reserve_sec: float,
    need_sec: float | None,
    *,
    now: float | None = None,
) -> bool:
    """조회 마감까지 `need_sec` 이상 남았는가 — 새 조회 작업을 시작해도 되는가.

    마감이 없거나(CLI·옛 체크포인트) 필요 시간을 모르면(첫 시도 — 직전 소요 없음) True다.
    필요 시간은 방금 잰 직전 소요를 넘긴다(추정 상수 금지 — D-266 ④와 같은 원칙).
    """
    left = retrieval_remaining(state, reserve_sec, now=now)
    if left is None:
        return True
    if need_sec is None:
        return left > 0
    return left >= float(need_sec)


def call_timeout(configured: float, *, now: float | None = None) -> float:
    """LLM·DB 호출 1건 상한 = min(설정 상한, 남은 시간) (plans/119 T-1 · 문헌 L-1·L-2).

    남은 시간은 조회 단계면 조회 마감까지, 서술 단계(`answer_phase`)면 처리 마감까지, 전달 단계
    (`delivery_phase` — 최종 사용자 응답 스트림)면 처리 마감 + 전달 연장까지다. 마감이
    묶여 있지 않으면 설정 상한 그대로다. 마감을 넘겼으면 `MIN_CALL_TIMEOUT_SEC` 이다.
    """
    bound = _BOUND.get()
    if bound is None:
        return float(configured)
    deadline, reserve, grace = bound
    if _DELIVERY_PHASE.get():
        deadline += grace
    elif not _ANSWER_PHASE.get():
        deadline -= reserve
    left = deadline - _now(now)
    return max(MIN_CALL_TIMEOUT_SEC, min(float(configured), left))
