"""공통 호스트 키 `AlarmEvent.host_key` · `host_key_strength` (plans/144 §5.5 · W2).

검증 항목:
    A. 정규화 — 공백·대소문자·FQDN 첫 라벨·끝 점, IP 모양은 자르지 않음
    B. 같은 서버(인프라 알람 · APM 알람, 대소문자·FQDN 차이) → 같은 키
    C. 강도 — 인프라 실값/APM override·host_name → strong · APM 정규식 → weak ·
       빈 키/ambiguous(정합 사유·서버 식별 역조회) → none
    D. 같은 hostname·다른 db_id → 같은 키(존 분리는 호출자 책임 — W3)
    E. 생성자·직렬화 불변 — dataclass 필드·asdict 키에 host_key가 없다
"""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime
from typing import Any

import pytest

from noise_gate.domain.alarm import (
    HOST_KEY_NONE,
    HOST_KEY_STRONG,
    HOST_KEY_WEAK,
    AlarmEvent,
    ServerIdentity,
    normalize_host_key,
)

REF = datetime(2026, 10, 7, 10, 0, 0)

# 게이트웨이 `resolver.reverse`(apm_gateway/application/resolver.py)의 실측 사유 어휘
_GATEWAY_REASONS_STRONG = ("override", "host_name")


def _infra_event(hostname: str, db_id: str = "pg_a", **kw: Any) -> AlarmEvent:
    return AlarmEvent(
        db_id=db_id,
        server_name=kw.pop("server_name", hostname),
        hostname=hostname,
        ip_address="",
        resource_ancestry="",
        alarm_id="A-1",
        severity=2,
        alarm_status="",
        resource_type=kw.pop("resource_type", "host"),
        resource_name="",
        alarm_name="CPU",
        alarm_time=REF,
        conditions="",
        condition_log="",
        **kw,
    )


def _apm_event(
    hostname: str,
    match_reason: str,
    *,
    db_id: str = "jennifer_main",
    resource_type: str = "apm.Instance",
    server_name: str = "",
) -> AlarmEvent:
    raw = {
        "dbId": db_id,
        "source": "jennifer",
        "hostname": hostname,
        "apm": {"match_reason": match_reason, "match_confidence": "high"},
    }
    return AlarmEvent(
        db_id=db_id,
        server_name=server_name or hostname or "was-inst-1",
        hostname=hostname,
        ip_address="",
        resource_ancestry="",
        alarm_id="jennifer:abc",
        severity=2,
        alarm_status="",
        resource_type=resource_type,
        resource_name="was-inst-1",
        alarm_name="JVM_HEAP_MEM_HIGH",
        alarm_time=REF,
        conditions="",
        condition_log="",
        raw_payload=raw,
    )


# ─── A. 정규화 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("web01", "web01"),
        ("  WEB01  ", "web01"),
        ("Web01.Example.Corp", "web01"),
        ("web01.example.corp.", "web01"),
        ("", ""),
        ("   ", ""),
        ("203.0.113.7", "203.0.113.7"),
        ("203.0.113.8", "203.0.113.8"),
        ("2001:db8::1", "2001:db8::1"),
    ],
)
def test_normalize_host_key(value: str, expected: str) -> None:
    assert normalize_host_key(value) == expected


def test_ip_not_truncated_distinct_ips_distinct_keys() -> None:
    a = _infra_event("203.0.113.7")
    b = _infra_event("203.0.113.8")
    assert a.host_key == "203.0.113.7"
    assert a.host_key != b.host_key


# ─── B. 같은 서버 → 같은 키 ─────────────────────────────────────────────────

def test_infra_and_apm_same_server_same_key() -> None:
    infra = _infra_event("WEB01")
    apm = _apm_event("web01.example.corp", "host_name")
    assert infra.host_key == apm.host_key == "web01"


def test_host_key_uses_hostname_not_server_name() -> None:
    """키 원천은 hostname이다 — 등록명(server_name)이 달라도 키는 hostname에서 나온다."""
    infra = _infra_event("web01", server_name="REG-NAME-01")
    assert infra.host_key == "web01"


def test_apm_unresolved_hostname_empty_key() -> None:
    """게이트웨이 정합 실패(hostname="") → 키는 "" — 인스턴스명(serverName)으로 대체하지 않는다."""
    apm = _apm_event("", "unresolved", server_name="was-inst-1")
    assert apm.host_key == ""
    assert apm.host_key_strength == HOST_KEY_NONE


# ─── C. 강도 ────────────────────────────────────────────────────────────────

def test_infra_real_hostname_strong() -> None:
    assert _infra_event("web01").host_key_strength == HOST_KEY_STRONG


def test_infra_empty_hostname_none() -> None:
    assert _infra_event("  ").host_key_strength == HOST_KEY_NONE


@pytest.mark.parametrize("reason", _GATEWAY_REASONS_STRONG)
def test_apm_override_or_host_name_strong(reason: str) -> None:
    assert _apm_event("web01", reason).host_key_strength == HOST_KEY_STRONG


def test_apm_regex_weak() -> None:
    assert _apm_event("web01", "regex").host_key_strength == HOST_KEY_WEAK


def test_apm_missing_reason_weak() -> None:
    """사유가 없거나 미지 값이면 강하게 보지 않는다(보수적)."""
    assert _apm_event("web01", "").host_key_strength == HOST_KEY_WEAK


def test_apm_reason_case_sensitive() -> None:
    """게이트웨이 어휘는 소문자 스네이크(`host_name`) — 계획서 표기 `hostName`은 strong이 아니다."""
    assert _apm_event("web01", "hostName").host_key_strength == HOST_KEY_WEAK


def test_apm_ambiguous_reason_none() -> None:
    assert _apm_event("web01", "ambiguous").host_key_strength == HOST_KEY_NONE


def test_ambiguous_server_identity_none() -> None:
    """서버 식별 역조회가 모호(같은 hostname 2건 이상)하면 키 신뢰도는 none."""
    infra = _infra_event("web01")
    infra.server_identity = ServerIdentity(hostname="web01", ambiguous=True)
    assert infra.host_key == "web01"
    assert infra.host_key_strength == HOST_KEY_NONE


@pytest.mark.parametrize(
    ("db_id", "resource_type", "source"),
    [
        ("jennifer", "host", None),            # 단일 소스 dbId
        ("jennifer_dr", "host", None),         # 다중 소스 dbId
        ("pg_a", "APM.Instance", None),        # resourceType만(대소문자 무시)
        ("pg_a", "host", "jennifer"),          # 원문 source만
    ],
)
def test_apm_detection_criteria(db_id: str, resource_type: str, source: str | None) -> None:
    """APM 판정은 server_identity.is_apm_source 기준 + process_rank.is_apm_event 기준과 같다."""
    raw: dict[str, Any] = {"apm": {"match_reason": "regex"}}
    if source:
        raw["source"] = source
    ev = _infra_event("web01", db_id=db_id, resource_type=resource_type, raw_payload=raw)
    assert ev.host_key_strength == HOST_KEY_WEAK


def test_infra_db_id_with_jennifer_infix_is_not_apm() -> None:
    ev = _infra_event("web01", db_id="pg_jennifer", raw_payload={"apm": {"match_reason": "regex"}})
    assert ev.host_key_strength == HOST_KEY_STRONG


def test_apm_detection_matches_existing_predicates() -> None:
    """기존 application·domain 판정과 어긋나지 않는다(같은 이벤트에 대해 같은 답)."""
    from noise_gate.application.server_identity import is_apm_source
    from noise_gate.domain.process_rank import is_apm_event

    apm = _apm_event("web01", "regex")
    infra = _infra_event("web01")
    assert is_apm_source(apm) and is_apm_event(apm)
    assert apm.host_key_strength == HOST_KEY_WEAK
    assert not is_apm_source(infra) and not is_apm_event(infra)
    assert infra.host_key_strength == HOST_KEY_STRONG


# ─── D. 다른 존 같은 hostname ───────────────────────────────────────────────

def test_same_hostname_different_db_same_key_zone_split_is_caller_duty() -> None:
    """host_key는 존을 담지 않는다 — 같은 hostname·다른 db_id면 같은 키(존 결합은 W3 호출자)."""
    a = _infra_event("web01", db_id="pg_a")
    b = _infra_event("web01", db_id="pg_b")
    assert a.host_key == b.host_key == "web01"


# ─── E. 생성자·직렬화 불변 ─────────────────────────────────────────────────

def test_dataclass_fields_and_asdict_unchanged() -> None:
    names = [f.name for f in fields(AlarmEvent)]
    assert "host_key" not in names and "host_key_strength" not in names
    assert names == [
        "db_id", "server_name", "hostname", "ip_address", "resource_ancestry",
        "alarm_id", "severity", "alarm_status", "resource_type", "resource_name",
        "alarm_name", "alarm_time", "conditions", "condition_log",
        "is_clear", "raw_payload", "server_identity", "received_at",
    ]
    d = asdict(_apm_event("web01", "override"))
    assert "host_key" not in d and "host_key_strength" not in d


def test_apm_server_name_untouched() -> None:
    """키 계산은 serverName·hostname을 바꾸지 않는다(관제·핑거프린트 이력 보존)."""
    apm = _apm_event("Web01.Example.Corp", "host_name", server_name="Web01.Example.Corp")
    _ = apm.host_key, apm.host_key_strength
    assert apm.server_name == "Web01.Example.Corp"
    assert apm.hostname == "Web01.Example.Corp"
