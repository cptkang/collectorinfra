"""사용자별 요청 조사 상한 (D-335 ⑥ · plans/187 W10·W11·W12 — `alarm.py`에서 추출).

추가 조사(`diagnosis`) · 알람 다시 분석(`manual`) · 채팅 증상 질의 조사(W12)를 **한 카운터**로 센다:
진행 중 1건(미종결은 재조회로 먼저 갱신) · 시간당 `investigation_additional_user_hourly_limit`건
(`submitted_at` 기준).

라우트(interface)와 채팅 노드(application)가 함께 쓰도록 infrastructure에 둔다. 미종결 레코드의
재조회 함수는 호출부가 주입한다(콜백 주입 — 라우트는 지연 poll 공용 모듈
`src.api.investigation_poll.refresh_investigation`을, 노드는 저장하지 않는 poll을 넘긴다).
동작은 추출 전 `alarm.py`의 `_user_request_limit`·`_submitted_epoch`와 같다.

(plans/187 교정 · M1) 「상한 검사 → 접수 레코드 기록」은 `user_request_slot`의 사용자별 잠금 안에서
한 임계구역으로 묶는다 — 같은 사용자의 동시 요청이 둘 다 검사를 통과하지 못하게 한다. 요청자
`sub`가 비면 상한을 셀 수 없으므로 접수를 거부한다(fail-closed · `REASON_OWNER_UNKNOWN`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from noise_gate.infrastructure.investigation_store import InvestigationStore

#: 시간당 상한의 창(초).
HOUR_SECONDS = 3600.0

#: 요청자 `sub`가 비었을 때의 거부 코드(fail-closed — 사용자별 상한을 셀 수 없다).
REASON_OWNER_UNKNOWN = "owner_unknown"
#: 잠금 dict 키 만료(초) — 마지막 사용 뒤 이만큼 지나고 잠겨 있지 않으면 sweep 한다.
USER_LOCK_TTL_SECONDS = 600.0

#: 사용자별 접수 잠금 — `sub → (잠금, 마지막 사용 epoch)`. 프로세스 수명 dict라 키 만료 sweep.
_USER_LOCKS: dict[str, tuple[asyncio.Lock, float]] = {}

#: 미종결 레코드 재조회 — `(store, rec, ng) → (최신 레코드, 실패 사유|None)`.
RefreshFn = Callable[[Any, dict[str, Any], Any], Awaitable[tuple[dict[str, Any], str | None]]]


def submitted_epoch(rec: dict[str, Any]) -> float | None:
    """레코드 `submitted_at`(ISO 8601) → epoch 초. 못 읽으면 None."""
    try:
        return datetime.fromisoformat(str(rec.get("submitted_at") or "")).timestamp()
    except ValueError:
        return None


async def user_request_limit(
    store: InvestigationStore, ng: Any, sub: str, *, refresh: RefreshFn
) -> tuple[str, str] | None:
    """사용자별 요청 조사 상한 — 넘으면 `(code, reason)`, 아니면 None.

    Args:
        store: 조사 저장소.
        ng: NoiseGateConfig(덕 타이핑) — 시간당 상한을 읽는다.
        sub: 요청자 `sub`(= 저장 레코드 `owner_sub`).
        refresh: 미종결 레코드 재조회 함수(호출부 주입).
    """
    from noise_gate.infrastructure.investigation_store import ACTIVE_STATUSES

    if not sub:
        return (
            REASON_OWNER_UNKNOWN,
            "요청자 식별 정보(sub)가 없어 사용자 요청 조사를 접수하지 않습니다 — "
            "다시 로그인한 뒤 요청해 주세요.",
        )
    mine = store.list_requests(owner_sub=sub)
    for rec in mine:
        if rec.get("status") in ACTIVE_STATUSES:
            fresh, _ = await refresh(store, rec, ng)
            if fresh.get("status") in ACTIVE_STATUSES:
                return (
                    "inflight_limit",
                    f"진행 중인 사용자 요청 조사가 있습니다({fresh.get('investigation_id')}) — "
                    "끝난 뒤 다시 요청해 주세요.",
                )
    hourly_limit = int(getattr(ng, "investigation_additional_user_hourly_limit", 3))
    now = time.time()
    recent = [
        rec for rec in mine
        if (at := submitted_epoch(rec)) is not None and now - at < HOUR_SECONDS
    ]
    if len(recent) >= hourly_limit:
        return (
            "hourly_limit",
            f"시간당 사용자 요청 조사 상한({hourly_limit}건)에 도달했습니다 — "
            "잠시 후 다시 요청해 주세요.",
        )
    return None


def _sweep_user_locks(now: float) -> None:
    """만료된(마지막 사용 뒤 TTL 경과 · 잠겨 있지 않은) 사용자 잠금을 지운다."""
    for key, (lock, last) in list(_USER_LOCKS.items()):
        if not lock.locked() and now - last > USER_LOCK_TTL_SECONDS:
            _USER_LOCKS.pop(key, None)


def _user_lock(sub: str) -> asyncio.Lock:
    """사용자 `sub`의 접수 잠금(없으면 만든다 · 호출마다 sweep · 마지막 사용 시각 갱신)."""
    now = time.monotonic()
    _sweep_user_locks(now)
    entry = _USER_LOCKS.get(sub)
    lock = entry[0] if entry is not None else asyncio.Lock()
    _USER_LOCKS[sub] = (lock, now)
    return lock


@asynccontextmanager
async def user_request_slot(
    store: InvestigationStore, ng: Any, sub: str, *, refresh: RefreshFn
) -> AsyncIterator[tuple[str, str] | None]:
    """사용자별 임계구역 — 잠금을 잡고 상한을 판정해 `(code, reason)` 또는 None을 내준다.

    호출부는 블록 **안에서** 거부(값이 있을 때)하거나 접수 → 조사 저장소 기록까지 끝낸다. 블록을
    벗어나면(예외 포함) 잠금이 풀린다. `sub`가 비면 잠그지 않고 `REASON_OWNER_UNKNOWN`을 내준다.

    Args:
        store: 조사 저장소.
        ng: NoiseGateConfig(덕 타이핑).
        sub: 요청자 `sub`.
        refresh: 미종결 레코드 재조회 함수(호출부 주입).
    """
    if not sub:
        yield await user_request_limit(store, ng, sub, refresh=refresh)
        return
    lock = _user_lock(sub)
    async with lock:
        yield await user_request_limit(store, ng, sub, refresh=refresh)
    _USER_LOCKS[sub] = (lock, time.monotonic())


__all__ = [
    "HOUR_SECONDS",
    "REASON_OWNER_UNKNOWN",
    "RefreshFn",
    "USER_LOCK_TTL_SECONDS",
    "submitted_epoch",
    "user_request_limit",
    "user_request_slot",
]
