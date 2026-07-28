"""자동 조사 트리거 페이로드 직렬화 (Plan 64 §0.2 CW-A · Plan 60 §14.2 · sre-agent/05 §4).

게이트가 이미 보유한 값(`AlarmEvent` + `NotificationDecision` + E1 재발/E2 클러스터/E4 root
메타)만 재사용해 `sre_agent` 조사 서비스의 트리거 페이로드(`contract_version: "1"`)로
직렬화한다. 신규 수집·변환 계층은 없다(sre-agent/05 §4 — 게이트 보유값 그대로 직렬화).

이 모듈은 domain 계층에 위치하므로 표준 라이브러리만 의존한다(src 내 다른 모듈 import 금지).
event/decision은 덕 타이핑으로 소비하며 타입에 결합하지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

# 트리거 페이로드 계약 버전 (sre-agent/05 §4). sre_agent JobStore.validate_payload가 이 값과
# 일치할 때만 수용한다(불일치·event 결측·필수 필드 결측 시 rejected).
CONTRACT_VERSION = "1"


def _fmt_alarm_time(value: object) -> str:
    """alarm_time을 폴스타 원 이벤트 형식(yyyyMMddHHmmss)으로 직렬화한다.

    datetime이면 strftime, 그 외(문자열 등)는 문자열화한다. None/빈 값은 빈 문자열.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d%H%M%S")
    return str(value)


def build_trigger_payload(
    event,  # noqa: ANN001 — AlarmEvent (덕 타이핑)
    decision,  # noqa: ANN001 — NotificationDecision (덕 타이핑)
    *,
    recurrence: Optional[dict] = None,
    correlation_meta: Optional[dict] = None,
    root_resource: Optional[str] = None,
) -> dict:
    """게이트 보유값으로 조사 트리거 페이로드(`contract_version: "1"`)를 조립한다.

    `event`는 폴스타 원 이벤트 스키마(Plan 01/05 §4)와 동일 키로 직렬화한다 — collectorinfra
    `AlarmEvent`가 보유한 값을 그대로 옮기므로 변환 계층이 불필요하다. 필수 event 필드
    (serverName/hostname/severity)는 결측 시 sre_agent가 거부하므로 이벤트가 값을 갖지 않으면
    빈 값으로 직렬화되어(그대로 통과) 조사 서비스가 rejected로 응답한다(침묵 금지·계약 준수).

    Args:
        event: 알람 이벤트(db_id/server_name/hostname/severity/alarm_* 등 속성).
        decision: 발송 판단(tier/reason/fingerprint/signals 속성).
        recurrence: E1 재발생 메타(직전 창 count 등, 없으면 None).
        correlation_meta: E2 크로스-호스트 클러스터 메타(대표 지문·멤버 순번 등, 없으면 None).
        root_resource: E4 다홉 연쇄의 root 리소스 식별자(없으면 None).

    Returns:
        `{contract_version, event, decision, meta}` 형태의 JSON 직렬화 가능 dict.
    """
    return {
        "contract_version": CONTRACT_VERSION,
        "event": {
            "dbId": str(getattr(event, "db_id", "") or ""),
            "serverName": str(getattr(event, "server_name", "") or ""),
            "hostname": str(getattr(event, "hostname", "") or ""),
            "alarmId": str(getattr(event, "alarm_id", "") or ""),
            "severity": int(getattr(event, "severity", 0) or 0),
            "alarmName": str(getattr(event, "alarm_name", "") or ""),
            "resourceType": str(getattr(event, "resource_type", "") or ""),
            "resourceName": str(getattr(event, "resource_name", "") or ""),
            "alarmTime": _fmt_alarm_time(getattr(event, "alarm_time", None)),
            "conditions": str(getattr(event, "conditions", "") or ""),
            "conditionLog": str(getattr(event, "condition_log", "") or ""),
        },
        "decision": {
            "tier": getattr(decision, "tier", ""),
            "reason": getattr(decision, "reason", ""),
            "fingerprint": getattr(decision, "fingerprint", ""),
            "signals": dict(getattr(decision, "signals", None) or {}),
        },
        "meta": {
            "recurrence": recurrence,
            "cluster": correlation_meta,
            "root_resource": root_resource,
            "source": "collectorinfra",
        },
    }


__all__ = ["CONTRACT_VERSION", "build_trigger_payload"]
