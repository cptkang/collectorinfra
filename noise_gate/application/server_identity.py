"""서버 식별 정보 부착 — hostname → 폴스타 등록 서버명·IP 역조회 (D-167).

배경: 폴스타 알람 소켓 연계 템플릿은 `${platformName}`·`${ipAddress}`를 지원하지 않아
(EL1008E, 2026-08-26 실측) 운영 템플릿이 `"serverName":"${hostname}"`로 바뀌었다. 그 결과
공동존(gp/yd, name ≠ hostname)에서 (1) 웹 UI가 hostname만 보여 서버 식별이 어렵고 (2) 이력·
노이즈 컨텍스트의 `SVR.NAME = server_name` 매칭이 0건으로 떨어질 위험이 생겼다.

이 모듈은 이벤트 구성 직후(dedup·지문·그래프 이전) `cmm_resource`를 hostname으로 역조회해
`AlarmEvent.server_identity`를 채우고, **결정적 승격 규칙**으로 `server_name`·`ip_address`를
보정한다 — 이후 모든 소비처(이력 매칭·지문·캐시 키·통보 본문·SSE)가 등록명을 쓰게 된다.

승격 규칙(결정적·보수적):
    - `server_name`은 템플릿이 hostname을 준 경우(빈 값 또는 hostname과 동일)에만 등록명으로
      바꾼다. 템플릿이 별도 서버명을 준 경우는 존중한다.
    - `ip_address`는 비어 있을 때만 채운다.
    - 같은 hostname의 `server.Server` 행이 2건 이상(ambiguous)이면 오식별 방지를 위해 둘 다
      승격하지 않는다(식별 정보에는 ambiguous=True로 표시).
    - 조회 실패·타임아웃·미등록 db_id는 warning 후 존/사이트 라벨만 담은 식별 정보를 붙인다
      (source="event") — 파이프라인을 절대 막지 않는다.

워커(`alarm_worker._process`)와 API(`routes/alarm.py`) 양쪽이 이 함수를 호출한다(경로 대칭).
Redis 캐시(옵션)는 `(db_id, hostname)` 키로 조회 결과를 TTL 보관해 알람당 DB 왕복을 줄인다.

계층: application — domain(ServerIdentity) + 주입된 infrastructure(resolver·redis) 소비.
존/사이트 라벨은 `src.routing.registry`(config/db_registry.yaml 정본)에서 파생한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from noise_gate.domain.alarm import AlarmEvent, ServerIdentity

logger = logging.getLogger(__name__)

_CACHE_KEY = "alarm:identity:{db_id}:{hostname}"

SOURCE_DB = "polestar_db"
SOURCE_CACHE = "cache"
SOURCE_EVENT = "event"


def zone_labels_for(db_id: str) -> tuple[str, str, str]:
    """db_id → (존 코드, 존 라벨, 사이트 라벨)을 레지스트리(config/db_registry.yaml)에서 파생한다.

    사이트 라벨은 그 DB를 **배타적으로** 지목하는 위치 표면어 중 하나다(김포/여의도/은행존).
    여러 후보가 있으면 "…존"으로 끝나는 표면어를 우선하고, 없으면 선언 순서 첫 항목을 쓴다
    (결정적). 미등록 db_id·레지스트리 로드 실패는 빈 문자열 3개(표시 생략).
    """
    try:
        from src.routing.registry import get_registry

        registry = get_registry()
        entry = registry.get(db_id)
        zone = (entry.zone if entry and entry.zone else "") or ""
        zone_label = next((z.label for z in registry.zones if z.code == zone), "") if zone else ""
        hints = tuple(registry.location_db_hints().get(db_id, ()))
        site = next((t for t in hints if t.endswith("존")), hints[0] if hints else "")
        return zone, zone_label, site
    except Exception:  # noqa: BLE001 — 라벨 파생 실패가 알람 처리를 막지 않는다
        logger.warning("존/사이트 라벨 파생 실패(표시 생략): db_id=%s", db_id)
        return "", "", ""


def _promote(event: AlarmEvent, row: dict[str, Any]) -> None:
    """역조회 행으로 event의 server_name·ip_address를 보수적으로 승격한다."""
    name = str(row.get("name") or "").strip()
    ip = str(row.get("ip_address") or "").strip()
    if name and (not event.server_name or event.server_name == event.hostname):
        event.server_name = name
    if ip and not event.ip_address:
        event.ip_address = ip


async def _cache_get(redis, key: str) -> Optional[dict[str, Any]]:  # noqa: ANN001
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
    except Exception:  # noqa: BLE001 — 캐시 실패는 DB 조회로 진행
        return None
    if not raw:
        return None
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — 손상 캐시는 무시
        return None


async def _cache_set(redis, key: str, row: dict[str, Any], ttl: int) -> None:  # noqa: ANN001
    if redis is None or ttl <= 0:
        return
    try:
        await redis.set(key, json.dumps(row, ensure_ascii=False), ex=int(ttl))
    except Exception:  # noqa: BLE001 — 캐시 적재 실패는 무시
        logger.debug("서버 식별 캐시 적재 실패(무시): key=%s", key)


async def attach_server_identity(
    event: AlarmEvent,
    resolver,  # noqa: ANN001 — PolestarHostnameResolver | None (덕 타이핑: lookup_identity)
    *,
    redis=None,  # noqa: ANN001 — redis.asyncio 클라이언트 | None
    timeout: float = 3.0,
    cache_ttl: int = 3600,
) -> Optional[ServerIdentity]:
    """event에 서버 식별 정보를 붙이고 server_name·ip_address를 승격한다 (graceful).

    Args:
        event: 알람 이벤트(제자리 갱신)
        resolver: `lookup_identity(db_id, hostname)`를 제공하는 리졸버. None이면 DB 조회 없이
            존/사이트 라벨만 붙인다(source="event").
        redis: 캐시용 redis.asyncio 클라이언트(없으면 캐시 미사용)
        timeout: DB 역조회 전체 타임아웃(초)
        cache_ttl: 캐시 TTL(초, 0 이하면 미캐시)

    Returns:
        부착된 ServerIdentity. hostname이 비어 있으면 None(이벤트 무변경).
    """
    hostname = (event.hostname or "").strip()
    if not hostname:
        return None

    zone, zone_label, site_label = zone_labels_for(event.db_id)
    identity = ServerIdentity(
        name="",
        hostname=hostname,
        ip_address=event.ip_address or "",
        zone=zone,
        zone_label=zone_label,
        site_label=site_label,
        source=SOURCE_EVENT,
    )

    row: Optional[dict[str, Any]] = None
    if resolver is not None:
        key = _CACHE_KEY.format(db_id=event.db_id, hostname=hostname)
        row = await _cache_get(redis, key)
        if row is not None:
            identity.source = SOURCE_CACHE
        else:
            try:
                row = await asyncio.wait_for(
                    resolver.lookup_identity(event.db_id, hostname), timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "서버 식별 역조회 타임아웃(%.1fs) — 이벤트 값 유지: db_id=%s hostname=%s",
                    timeout, event.db_id, hostname,
                )
                row = None
            except Exception:  # noqa: BLE001 — 조회 실패가 알람 처리를 막지 않는다
                logger.warning(
                    "서버 식별 역조회 실패 — 이벤트 값 유지: db_id=%s hostname=%s",
                    event.db_id, hostname, exc_info=True,
                )
                row = None
            if row is not None:
                identity.source = SOURCE_DB
                await _cache_set(redis, key, row, cache_ttl)

    if row is not None:
        identity.name = str(row.get("name") or "").strip()
        identity.ip_address = str(row.get("ip_address") or "").strip() or identity.ip_address
        identity.os_type = str(row.get("os_type") or "").strip()
        identity.ambiguous = bool(row.get("ambiguous"))
        if identity.ambiguous:
            logger.warning(
                "서버 식별 모호(동일 hostname server.Server 2건 이상) — 승격 생략: db_id=%s hostname=%s",
                event.db_id, hostname,
            )
        else:
            _promote(event, row)

    event.server_identity = identity
    return identity
