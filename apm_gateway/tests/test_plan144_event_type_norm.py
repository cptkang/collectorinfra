"""제니퍼 알람 페이로드 정규화 유형 `apm.event_type_norm` (plans/144 §4.3 · D-274).

- 어댑터가 중립 이벤트 레코드에 정규화 유형을 채운다(대문자 · 앞뒤 공백 제거 · `ERROR_`·`WARNING_`
  접두 1회 제거 — `normalize_event_type`과 같은 규칙).
- 도메인 `build_alarm_payload`는 레코드 값을 옮기기만 한다. `alarmName`·원문 `event_type`·멱등 키·
  `alarmId`는 바뀌지 않는다(기존 핑거프린트·관제 이력·커서 보존).
"""

from __future__ import annotations

import pytest
from apm_gateway.adapters.jennifer.fields import normalize_event_type, parse_event
from apm_gateway.domain.events import build_alarm_payload


def _raw(**over):
    base = {
        "time": 1_700_000_000_000,
        "eventLevel": "CRITICAL",
        "errorType": "ERROR_SERVICE_QUEUING",
        "metricsName": "",
        "instanceId": 1001,
        "instanceName": "was01_a",
        "domainId": 7,
        "domainName": "dom",
    }
    base.update(over)
    return base


def _payload(event):
    return build_alarm_payload(
        event,
        source="jennifer",
        source_label="JENNIFER",
        source_id="default",
        hostname="h",
        ip_address="",
        match_confidence="high",
        match_reason="",
        severity=3,
        was_signals=[],
        tz="Asia/Seoul",
    )


@pytest.mark.parametrize(
    "error_type, metrics_name, norm",
    [
        ("ERROR_SERVICE_QUEUING", "", "SERVICE_QUEUING"),  # ERROR_ 접두
        ("WARNING_JVM_HEAP_MEM_HIGH", "", "JVM_HEAP_MEM_HIGH"),  # WARNING_ 접두
        ("SERVICE_QUEUING", "", "SERVICE_QUEUING"),  # 접두 없음
        ("error_outofmemory", "", "OUTOFMEMORY"),  # 소문자 입력
        ("  ERROR_OUTOFMEMORY ", "", "OUTOFMEMORY"),  # 앞뒤 공백
        ("ERROR_ERROR_X", "", "ERROR_X"),  # 접두는 1회만 뗀다
        ("", "gc_time_usage", "GC_TIME_USAGE"),  # 지표 기반 이벤트
        ("", "", ""),  # 빈 값
    ],
)
def test_parse_event_fills_norm(error_type, metrics_name, norm):
    ev = parse_event(_raw(errorType=error_type, metricsName=metrics_name))
    assert ev["event_type_norm"] == norm
    assert ev["event_type_norm"] == normalize_event_type(ev["event_type"])
    assert ev["event_type"] == (error_type or metrics_name)  # 원문 보존


def test_payload_carries_norm_and_keeps_raw_fields():
    ev = parse_event(_raw(errorType="error_service_queuing"))
    p = _payload(ev)
    assert p["apm"]["event_type_norm"] == "SERVICE_QUEUING"
    assert p["apm"]["event_type"] == "error_service_queuing"
    assert p["alarmName"] == "error_service_queuing"
    # 정규화 칸은 멱등 키·alarmId에 들어가지 않는다 — 칸이 없던 레코드와 같은 값.
    legacy = _payload({k: v for k, v in ev.items() if k != "event_type_norm"})
    assert p["alarmId"] == legacy["alarmId"]
    assert p["apm"]["idempotency_key"] == legacy["apm"]["idempotency_key"]


def test_payload_without_norm_falls_back_to_raw():
    ev = {k: v for k, v in parse_event(_raw()).items() if k != "event_type_norm"}
    assert _payload(ev)["apm"]["event_type_norm"] == "ERROR_SERVICE_QUEUING"
    empty = {k: v for k, v in parse_event(_raw(errorType="")).items() if k != "event_type_norm"}
    assert _payload(empty)["apm"]["event_type_norm"] == ""
