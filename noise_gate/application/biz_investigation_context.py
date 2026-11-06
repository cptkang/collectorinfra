"""알람 조사 페이로드에 업무 서비스 맥락 `meta.biz_service`를 단다 (plans/215 §4.8 D2 · D-368).

자동 트리거·사건 종합·수동 분석 세 경로가 `attach_biz_service`를 부른다(`_attach_polestar_link`
뒤 — 귀속 1단계가 그 연계 대상을 쓴다). `NOISE_BIZ_INVESTIGATION_CONTEXT_ENABLED`(+
`NOISE_BIZ_SERVICE_ENABLED`)가 꺼져 있거나 서비스 미소속·사본 없음·Redis 실패면 키를 만들지 않는다
(페이로드 비트 동일 · `polestar_link`는 건드리지 않는다).

워커는 Redis 읽기 사본만 쓴다(쓰기 0 · D-049). 존 필터: 자동 경로는 알람 존 안 구성원만, 수동
경로는 요청자 허용 존 안 구성원만(권한 밖 서버 이름이 조사 결과로 새지 않게 · §9). 자동 경로에서
알람 존을 못 풀면 구성원을 싣지 않는다(fail-closed — 서비스 식별·표시명만 남는다). D1 판독
(`d1_verdict`·이상 구성원 순서)은 메모의 알람 존 항목만 쓴다 — 알람 존 미상·허용 밖·항목 없음이면
싣지 않는다(fail-closed).

호스트 귀속은 SMS DB 알람이면 그 db_id 구성원으로만 한다(`sms_db_of` — 다른 DB의 같은 이름 서버로
귀속하지 않는다 · D-224 ④).

계층: application — domain 빌더 + infrastructure 사본(덕 타이핑으로 주입 · 없으면 redis로 만든다).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Collection, Mapping
from typing import Any

from noise_gate.domain.alarm import is_apm_db_id
from noise_gate.domain.biz_service_context import BizTarget, build_biz_service_context
from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER, sms_member_key
from noise_gate.domain.process_rank import is_apm_event

logger = logging.getLogger(__name__)

#: D1 결과 메모 키 접두(`bizdiag:d1:{service_id}` · 60초 — W9가 기록 · 여기서는 읽기만).
#: 값은 `{by_zone: {존: {verdict, anomalous_members}}, computed_at}` — 알람 존 항목만 쓴다.
D1_MEMO_PREFIX = "bizdiag:d1:"


def context_active(cfg: Any) -> bool:
    """D2 플래그 유효 여부 — 정확히 True일 때만(`MagicMock` 설정 오판 방지)."""
    ng = getattr(cfg, "noise_gate", None)
    return getattr(ng, "biz_investigation_context_active", False) is True


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _linked_items(hints: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """`meta.hints.polestar_link`(linked)의 대표 + `targets` 항목(연결 아님이면 빈 목록)."""
    link = hints.get("polestar_link")
    if not isinstance(link, Mapping) or link.get("status") != "linked":
        return []
    return [link, *(t for t in link.get("targets") or () if isinstance(t, Mapping))]


def _link_targets(hints: Mapping[str, Any]) -> list[tuple[str, str]]:
    """`meta.hints.polestar_link`(linked)의 `(db_id, resource_id)` 목록 — 대표 + `targets`."""
    out: list[tuple[str, str]] = []
    for t in _linked_items(hints):
        ref = (_text(t.get("db_id")), _text(t.get("resource_id")))
        if all(ref) and ref not in out:
            out.append(ref)
    return out


def _link_hosts(hints: Mapping[str, Any]) -> frozenset[tuple[str, str]]:
    """연계 대상의 `(db_id, 호스트 이름)` — 자원 id 없이 연결된 대상도 D2 대상 제외에 쓴다."""
    return frozenset(
        (_text(t.get("db_id")), _text(t.get(f)))
        for t in _linked_items(hints)
        for f in ("hostname", "server_name")
        if _text(t.get("db_id")) and _text(t.get(f))
    )


def sms_db_of(db_id: Any) -> str:
    """알람 db_id → 호스트 귀속을 좁힐 SMS DB id(레지스트리 등록 DB일 때만 · 아니면 빈 값).

    APM 소스 db_id(`is_apm_db_id`)·미등록 db_id는 빈 값 — 호스트 귀속이 전 DB 기준(1대일 때만)으로
    남는다. 레지스트리를 못 읽으면 APM이 아닌 db_id는 그대로 돌려준다(fail-closed — 다른 DB의
    같은 이름 서버로 귀속하지 않는다).
    """
    text = _text(db_id)
    if not text or is_apm_db_id(text):
        return ""
    from src.routing.registry import get_registry

    try:
        registered = get_registry().get(text) is not None
    except Exception:  # noqa: BLE001 — 레지스트리 실패는 DB 한정(오귀속 0)
        logger.debug("SMS DB 판정 실패 — DB 한정으로 처리: db_id=%s", text, exc_info=True)
        return text
    return text if registered else ""


def _apm_identity(event: Any, hints: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """APM 알람의 `(source_id, domain_id, instance_name)` — 210 연계 매핑과 같은 규칙."""
    from noise_gate.application.apm_host_map_lookup import apm_block
    from noise_gate.domain.host_link import apm_source_id

    apm = apm_block(event)
    source_id = apm_source_id(_text(getattr(event, "db_id", "")), hints.get("source_id"))
    domain_id = _text(
        apm.get("domain_id") if apm.get("domain_id") is not None else hints.get("domain_id")
    )
    instance = _text(apm.get("instance_name")) or _text(getattr(event, "resource_name", ""))
    if not (source_id and domain_id and instance):
        return None
    return source_id, domain_id, instance


def _db_zone(db_id: str) -> str:
    from src.routing.zones import db_id_to_zone

    try:
        return db_id_to_zone(db_id) or ""
    except Exception:  # noqa: BLE001 — 레지스트리 실패는 존 미상(허용 집합 밖 → 싣지 않음)
        logger.debug("존 해석 실패: db_id=%s", db_id, exc_info=True)
        return ""


def _apm_source_zone(source_id: str) -> str:
    """APM 소스 id → 존 코드(레지스트리 솔루션 소스 표 · 둘 이상 존에 걸리면 미상)."""
    from src.routing.registry import get_registry

    try:
        zones = {
            _text(src.zone)
            for spec in get_registry().solutions()
            if spec.family
            for src in spec.sources or ()
            if src.id == source_id
        }
    except Exception:  # noqa: BLE001 — 레지스트리 실패는 존 미상
        logger.debug("APM 소스 존 해석 실패: source=%s", source_id, exc_info=True)
        return ""
    return zones.pop() if len(zones) == 1 else ""


def member_zone(member: Mapping[str, Any]) -> str:
    """구성원 존 코드 — 사본 `zone` 칸, 비었으면 레지스트리(SMS db_id · APM 소스)."""
    zone = _text(member.get("zone"))
    if zone:
        return zone
    if _text(member.get("kind")) == KIND_SMS_SERVER:
        return _db_zone(_text(member.get("db_id")))
    return _apm_source_zone(_text(member.get("source_id")))


async def _d1_memo(redis: Any, service_id: int, zone: str) -> dict[str, Any] | None:
    """D1 메모의 그 존 판독(존 미상·항목 없음·손상·Redis 실패면 None — 고르는 순서 1단계를
    건너뛰고 `d1_verdict`를 싣지 않는다 · fail-closed)."""
    if redis is None or not zone:
        return None
    try:
        raw = await redis.get(f"{D1_MEMO_PREFIX}{int(service_id)}")
    except Exception:  # noqa: BLE001 — 메모 미상은 무시
        logger.debug("D1 메모 조회 실패: service=%s", service_id, exc_info=True)
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    by_zone = value.get("by_zone") if isinstance(value, dict) else None
    entry = by_zone.get(zone) if isinstance(by_zone, dict) else None
    return entry if isinstance(entry, dict) else None


async def attach_biz_service(
    cfg: Any,  # AppConfig (덕 타이핑)
    event: Any,  # AlarmEvent
    payload: dict[str, Any],
    *,
    cache: Any = None,  # RedisBizServiceCache | None
    redis: Any = None,  # redis.asyncio 클라이언트 | None — cache가 없을 때만 쓴다
    manual: bool = False,
    requester_zones: Collection[str] | None = None,
) -> None:
    """조사 페이로드에 `meta.biz_service`를 단다(값이 있을 때만 · 실패는 키 없음).

    Args:
        cache: 업무 서비스 사본(API 프로세스 `app.state.biz_service_cache`).
            없으면 `redis`로 만든다.
        manual: 수동 분석 경로면 True — 존 필터가 `requester_zones`(None = 전 존)가 된다.
            False(자동 경로)면 알람 존 안 구성원만 싣는다.
        requester_zones: 요청자 허용 존 코드 집합(수동 경로만 · None = 전 존).
    """
    if not context_active(cfg):
        return
    if cache is None and redis is not None:
        from noise_gate.infrastructure.biz_service_cache import RedisBizServiceCache

        cache = RedisBizServiceCache(redis)
    if cache is None:
        return
    alarm_id = getattr(event, "alarm_id", "")
    try:
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            return
        raw_hints = meta.get("hints")
        hints: Mapping[str, Any] = raw_hints if isinstance(raw_hints, Mapping) else {}
        targets = _link_targets(hints)
        apm = _apm_identity(event, hints) if is_apm_event(event) else None
        hostname = _text(getattr(event, "hostname", ""))
        db_id = _text(getattr(event, "db_id", ""))
        sms_db = "" if is_apm_event(event) else sms_db_of(db_id)
        attribution = await cache.attribute(
            polestar_link_targets=targets or None, apm=apm, hostname=hostname or None,
            db_id=sms_db or None,
        )
        if attribution is None:
            return
        service = await cache.get_service(attribution.service_id)
        if not service:
            logger.info(
                "업무 서비스 맥락 생략 — 사본 없음: alarm_id=%s service=%s",
                alarm_id, attribution.service_id,
            )
            return
        target = BizTarget(
            sms_keys=frozenset(k for k in (sms_member_key(d, r) for d, r in targets) if k),
            hostname=hostname,
            db_id="" if is_apm_event(event) else db_id,
            apm=apm,
            link_hosts=_link_hosts(hints),
        )
        allowed: Collection[str] | None = requester_zones
        alarm_zone = _db_zone(db_id)
        if not manual:
            allowed = frozenset({alarm_zone}) if alarm_zone else frozenset()  # 존 미상 = 0건
        d1_zone = alarm_zone if allowed is None or alarm_zone in allowed else ""
        ng = cfg.noise_gate
        value = build_biz_service_context(
            service,
            target,
            allowed_zones=allowed,
            max_sms=int(getattr(ng, "biz_investigation_max_sms", 10)),
            max_apm=int(getattr(ng, "biz_investigation_max_apm", 8)),
            d1=await _d1_memo(getattr(cache, "redis", redis), attribution.service_id, d1_zone),
            member_zone=member_zone,
        )
    except Exception:  # noqa: BLE001 — 맥락 부착 실패가 조사 제출을 막지 않는다(키 없음)
        logger.warning("업무 서비스 맥락 부착 실패(키 없음): alarm_id=%s", alarm_id, exc_info=True)
        return
    if value is None:
        return
    meta["biz_service"] = value
    logger.info(
        "업무 서비스 맥락: alarm_id=%s service=%s basis=%s sms=%d apm=%d truncated=%s d1=%s",
        alarm_id, value["service_id"], attribution.basis, len(value["sms_members"]),
        len(value["apm_members"]), value["truncated"], value.get("d1_verdict", "-"),
    )


__all__ = ["D1_MEMO_PREFIX", "attach_biz_service", "context_active", "member_zone", "sms_db_of"]
