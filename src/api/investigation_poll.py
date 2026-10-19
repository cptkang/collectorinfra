"""조사 지연 poll 공용 모듈 (plans/155 G-10 · plans/187 W10 — `alarm.py`에서 추출).

알람 조사 조회(`/alarm/investigations/*`)와 추가 조사(`kind="diagnosis"`) 조회가
**같은 잠금·간격·종결 규칙**을 쓰도록 한 벌만 둔다. 동작은 추출 전 `alarm.py`의
`_refresh_investigation`·`_poll_investigation`·`_sweep_investigation_polls`와 같다 —
`alarm.py`는 옛 이름으로 재노출한다.

추가 조사 레코드의 선택 필드(`owner_sub`·`parent_investigation_id`·`focus`·`origin`·
`subject`)는 종결 행에도 옮겨 적는다. 알람 레코드에는 그 키가 없으므로 종결 행이 추출 전과
비트 동일하다.

(plans/187 W5a · D-341 ⑦) 플레이북 잡의 poll 응답에 있는 진척 키(`progress`·`partial_briefing`)는
**저장하지 않고** 돌려주는 레코드 사본에만 얹는다. 미종결이면 두 키를, 종결이면 `progress`만 싣는다.
poll 간격 안의 재요청은 직전 poll 값(`POLL_LIVE`)을 재사용하고, 종결 시 버리며 간격이 지나면 sweep
한다. 키가 없는 응답(플레이북 off)이면 돌려주는 레코드가 종전과 같다.

계층: interface(`src/api/`). noise_gate 저장소·클라이언트는 함수 안에서 import 한다(종전과 같다 —
테스트가 `sre_agent_client.build_sre_agent_client`를 바꿔 끼운다).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from noise_gate.infrastructure.investigation_store import InvestigationStore

logger = logging.getLogger(__name__)

# 아직 종결되지 않은 조사를 sre_agent에 다시 물을 때 종결로 보는 상태(submit/poll 계약 · D-123).
TERMINAL_POLL_STATUSES: frozenset[str] = frozenset(
    {"done", "failed", "timeout", "stub", "rejected", "not_found"}
)

#: 같은 조사의 지연 poll 최소 간격(초) — 카드 재조회·여러 사용자가 조사 서비스를 두드리지 않게.
POLL_MIN_INTERVAL = 10.0
#: 조사별 마지막 poll 시각(monotonic)과 동시성 1 잠금 — 키는 (저장소 경로, 조사 id).
#: 간격이 지난 키는 다음 호출에서 sweep 한다(프로세스 in-memory dict는 키 만료 sweep 원칙).
POLL_AT: dict[tuple[str, str], float] = {}
POLL_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
#: (plans/187 W5a) 미종결 조사의 직전 poll 진척 키 — 키는 `POLL_AT`과 같고 같은 sweep으로 만료한다.
POLL_LIVE: dict[tuple[str, str], dict[str, Any]] = {}
#: poll 응답에서 응답에만 싣는 진척 키(저장 안 함).
LIVE_KEYS: tuple[str, ...] = ("progress", "partial_briefing")


def _live_fields(res: dict[str, Any]) -> dict[str, Any]:
    """poll 응답의 진척 키 중 dict 값만 고른다(없으면 빈 dict)."""
    return {k: res[k] for k in LIVE_KEYS if isinstance(res.get(k), dict)}


def sweep_polls(now: float) -> None:
    """간격이 지난 poll 시각·잠금을 지운다(진행 중인 잠금은 남긴다)."""
    expired = [key for key, at in POLL_AT.items() if now - at >= POLL_MIN_INTERVAL]
    for key in expired:
        lock = POLL_LOCKS.get(key)
        if lock is not None and lock.locked():
            continue
        POLL_AT.pop(key, None)
        POLL_LOCKS.pop(key, None)
    for key in [k for k in POLL_LIVE if k not in POLL_AT]:
        POLL_LIVE.pop(key, None)


async def refresh_investigation(
    store: InvestigationStore, rec: dict[str, Any], ng: Any
) -> tuple[dict[str, Any], str | None]:
    """미종결 조사를 sre_agent에 poll해 종결이면 저장하고 최신 레코드를 돌려준다.

    조사 id별로 동시에 1건만 poll하고(`asyncio.Lock`), 마지막 poll 뒤
    `POLL_MIN_INTERVAL`초 안의 재요청은 poll 없이 저장소 상태로 응답한다(아직 미종결이면 직전
    poll의 진척 키를 얹는다 — plans/187 W5a).

    Returns:
        (레코드, 재조회 실패 사유 또는 None)
    """
    investigation_id = str(rec.get("investigation_id") or "")
    if not investigation_id:
        return rec, None
    key = (str(store.path), investigation_id)
    sweep_polls(time.monotonic())
    lock = POLL_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        last = POLL_AT.get(key)
        if last is not None and time.monotonic() - last < POLL_MIN_INTERVAL:
            current = store.get_by_investigation(investigation_id) or rec
            live = POLL_LIVE.get(key)
            if live and current.get("status") in _active_statuses():
                return {**current, **live}, None
            return current, None
        POLL_AT[key] = time.monotonic()
        return await poll_investigation(store, rec, investigation_id, ng)


async def poll_investigation(
    store: InvestigationStore, rec: dict[str, Any], investigation_id: str, ng: Any
) -> tuple[dict[str, Any], str | None]:
    """조사 서비스에 1회 poll한다(`refresh_investigation`의 잠금 안에서만 부른다).

    요청마다 새 클라이언트를 쓴다(워커 공유 클라이언트와 세션이 섞이지 않게 — 후속 폴링과 같은
    이유). 호출 전체를 `investigation_mcp_call_timeout_seconds`로 묶는다. 실패하면 저장소 상태를
    그대로 두고 사유를 돌려준다(침묵 금지). 미종결(running)은 저장하지 않는다(재조회마다 행이
    쌓이지 않게). 종결이어도 저장소에 이미 종결 행이 있으면(후속 폴링 등이 먼저 씀) 다시 쓰지
    않는다.
    실패·거부·타임아웃·스텁 응답의 `reason`·`error`는 레코드 사유로 남긴다.
    """
    from noise_gate.infrastructure.investigation_store import (
        ACTIVE_STATUSES,
        LINK_FIELDS,
        STATUS_RUNNING,
        terminal_poll_reason,
    )
    from noise_gate.infrastructure.sre_agent_client import build_sre_agent_client

    client = build_sre_agent_client(ng)
    if client is None:
        return rec, "조사 서비스 URL 미설정 — 진행 상태를 다시 조회할 수 없습니다"
    timeout = float(getattr(ng, "investigation_mcp_call_timeout_seconds", 10.0))

    async def _poll() -> dict[str, Any]:
        await client.connect()
        try:
            return await client.poll(investigation_id)
        finally:
            await client.disconnect()

    try:
        res = await asyncio.wait_for(_poll(), timeout=timeout)
    except TimeoutError:
        return rec, f"조사 서비스 재조회 타임아웃({timeout:.0f}s 초과)"
    except Exception as exc:  # noqa: BLE001 — 재조회 실패는 저장소 상태로 응답한다
        logger.warning("조사 재조회 실패: inv=%s", investigation_id, exc_info=True)
        return rec, f"조사 서비스 재조회 실패: {type(exc).__name__}"
    status = str(res.get("status") or "")
    live = _live_fields(res)
    key = (str(store.path), investigation_id)
    if status not in TERMINAL_POLL_STATUSES:
        if live:
            POLL_LIVE[key] = live
        else:
            POLL_LIVE.pop(key, None)
        base = {**rec, "status": STATUS_RUNNING} if status else rec
        return ({**base, **live} if live else base), None
    POLL_LIVE.pop(key, None)
    # 종결 뒤에는 진척(`progress`)만 응답에 얹는다 — 부분 브리핑은 종결 브리핑으로 대체된다.
    final_live = {"progress": live["progress"]} if "progress" in live else {}
    current = store.get_by_investigation(investigation_id)
    if current is not None and current.get("status") not in ACTIVE_STATUSES:
        return ({**current, **final_live} if final_live else current), None
    # 추가 조사 레코드의 연결 필드만 옮긴다(알람 레코드는 값이 없어 키를 쓰지 않는다).
    links = {k: rec[k] for k in LINK_FIELDS if rec.get(k)}
    store.record(
        investigation_id=investigation_id,
        alarm_ids=list(rec.get("alarm_ids") or []),
        kind=str(rec.get("kind") or "alarm"),
        episode_id=rec.get("episode_id"),
        status=status,
        source="api_poll",
        verdict=res.get("verdict"),
        briefing=res.get("briefing") if isinstance(res.get("briefing"), dict) else None,
        reason=terminal_poll_reason(res),
        **links,
    )
    stored = store.get_by_investigation(investigation_id) or rec
    return ({**stored, **final_live} if final_live else stored), None


def _active_statuses() -> frozenset[str]:
    """미종결 상태 집합(저장소 정본 — 함수 안 import는 모듈 머리 규칙과 같다)."""
    from noise_gate.infrastructure.investigation_store import ACTIVE_STATUSES

    return ACTIVE_STATUSES


__all__ = [
    "LIVE_KEYS",
    "POLL_AT",
    "POLL_LIVE",
    "POLL_LOCKS",
    "POLL_MIN_INTERVAL",
    "TERMINAL_POLL_STATUSES",
    "poll_investigation",
    "refresh_investigation",
    "sweep_polls",
]
