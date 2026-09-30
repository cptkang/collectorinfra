"""이벤트 정규화 — APM 이벤트 → `alarm:raw` 폴스타 템플릿 페이로드 (plans/87 §5.5 · SPEC-apm-gateway
§5).

순수 함수 · 벤더 무지(소스 이름·라벨은 인자로 받는다). 폴러가 이 모듈로 페이로드를 만들고,
`noise_gate` 워커는 폴스타 알람과 **같은 파서**(`AlarmEvent`)로 받는다 — 게이트웨이 이벤트임은
`resourceType="apm.Instance"`·`dbId`·`raw_payload.apm`으로 구분한다.

제니퍼 소스가 여럿이면(plans/87 J8 · D-287 ②) `dbId` = `<source>_<source_id>`(소비자가 레지스트리로
존을 푼다) · `apm.source_id` · `resourceAncestry`에 소스 id가 들어간다. 단일 설정 소스 `default`는
`dbId`·`resourceAncestry`를 v4와 같게 둔다. 멱등 키에는 항상 소스 id가 들어간다(S-6).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apm_gateway.domain.sources import DEFAULT_SOURCE_ID, alarm_db_id

RESOURCE_TYPE = "apm.Instance"

# 레벨 → 폴스타 severity(0 해소 · 1 주의 · 2 경고 · 3 심각). config/event_levels.yaml이 덮어쓴다.
DEFAULT_LEVEL_SEVERITY: dict[str, int] = {
    "fatal": 3,
    "critical": 3,
    "warning": 2,
    "normal": 1,
    "recovery": 0,
    "clear": 0,
}
DEFAULT_UNKNOWN_SEVERITY = 2  # 미지 레벨은 보수적으로 경고(R-3)

# 최소 레벨 필터용 순위(해소 레벨은 필터와 무관하게 통과시킨다 — 알람이 해소되지 않는 일을 막는다).
_LEVEL_RANK = {"fatal": 3, "critical": 3, "warning": 2, "normal": 1}
CLEAR_LEVELS = frozenset({"recovery", "clear"})


def severity_for_level(
    level: str, mapping: dict[str, int] | None = None, unknown: int | None = None
) -> int:
    table = mapping or DEFAULT_LEVEL_SEVERITY
    key = str(level or "").strip().lower()
    if key in table:
        return int(table[key])
    return DEFAULT_UNKNOWN_SEVERITY if unknown is None else int(unknown)


def level_rank(level: str) -> int:
    """최소 레벨 비교용 순위. 미지 레벨은 경고(2)로 본다."""
    return _LEVEL_RANK.get(str(level or "").strip().lower(), 2)


def passes_min_level(level: str, min_level: str) -> bool:
    key = str(level or "").strip().lower()
    if key in CLEAR_LEVELS:
        return True
    return level_rank(key) >= level_rank(min_level)


def idempotency_key(
    source_id: str,
    domain_id: object,
    instance_id: object,
    event_type: str,
    time_ms: object,
    txid: str,
) -> str:
    """합성 멱등 키(§0.9 판단 ③ — 응답에 eventId가 없다).

    소스가 다르면 값이 같아도 다른 이벤트다(plans/87 J8 S-6).
    """
    raw = "|".join(
        str(x if x is not None else "")
        for x in (source_id, domain_id, instance_id, event_type, time_ms, txid)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def format_alarm_time(time_ms: int | None, tz: str) -> str:
    """epoch ms → `yyyyMMddHHmmss`(지정 시간대 · naive 문자열 — 폴스타 템플릿 형식)."""
    if time_ms is None:
        return ""
    return datetime.fromtimestamp(time_ms / 1000, ZoneInfo(tz)).strftime("%Y%m%d%H%M%S")


def build_alarm_payload(
    event: dict[str, Any],
    *,
    source: str,
    source_label: str,
    source_id: str,
    hostname: str,
    ip_address: str,
    match_confidence: str,
    match_reason: str,
    severity: int,
    was_signals: list[dict[str, Any]],
    tz: str,
) -> dict[str, Any]:
    """중립 이벤트 레코드(마스킹 끝난 것)를 `alarm:raw` 페이로드로 만든다(SPEC §5)."""
    key = idempotency_key(
        source_id,
        event.get("domain_id"),
        event.get("instance_id"),
        event.get("event_type", ""),
        event.get("time_ms"),
        event.get("txid", ""),
    )
    instance_name = str(event.get("instance_name") or "")
    domain_name = str(event.get("domain_name") or "")
    event_type = str(event.get("event_type") or "")
    level = str(event.get("level") or "")
    value = event.get("value")
    message = str(event.get("message") or "")
    condition_log = message if value is None else f"{message} (value={value:g})"
    ancestry = [source_label, domain_name, instance_name]
    if source_id != DEFAULT_SOURCE_ID:
        ancestry.insert(1, source_id)
    return {
        "dbId": alarm_db_id(source, source_id),
        "source": source,
        "serverName": hostname or instance_name,
        "hostname": hostname,
        "ipAddress": ip_address,
        "resourceAncestry": " > ".join(ancestry),
        "alarmId": f"{source}:{key[:16]}",
        "severity": int(severity),
        "alarmStatus": "",
        "resourceType": RESOURCE_TYPE,
        "resourceName": instance_name,
        "alarmName": event_type,
        "alarmTime": format_alarm_time(event.get("time_ms"), tz),
        "conditions": f"{source_label} EVENT {level} — {event_type}",
        "conditionLog": condition_log,
        "apm": {
            "source": source,
            "source_id": source_id,
            "domain_id": event.get("domain_id"),
            "domain_name": domain_name,
            "instance_id": event.get("instance_id"),
            "instance_name": instance_name,
            "event_type": event_type,
            "event_kind": event.get("event_kind", ""),
            "level": level,
            "value": value,
            "txid": event.get("txid", ""),
            "time_ms": event.get("time_ms"),
            "application": event.get("application", ""),
            "match_confidence": match_confidence,
            "match_reason": match_reason,
            "was_signals": was_signals,
            "idempotency_key": key,
        },
    }
