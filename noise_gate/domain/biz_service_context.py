"""알람 조사 페이로드의 업무 서비스 맥락 `meta.biz_service` 조립 (plans/215 §4.8 D2 · D-368).

입력은 이미 귀속된 서비스 사본 1건(`bizsvc:svc:{id}` JSON — 표시 정보 + 활성 구성원)과 조사 대상
식별자다. 출력에는 식별자와 표시명 1개만 싣는다(업무명 원문 토큰·근거 문장은 싣지 않는다).

고르는 순서(상한 안 · 결정적): D1 이상 구성원 → 대상과 같은 접두 업무코드(SMS)·같은 APM 도메인 →
나머지 정규 식별자 오름차순. 대상 자신은 뺀다. 존 필터는 호출자가 정한 허용 존 집합으로 한다 —
자동 경로는 알람 존, 수동 경로는 요청자 허용 존(권한 밖 구성원은 건수도 싣지 않는다).

계층: domain — 표준 라이브러리 + `src.domain.host_names` + 같은 domain 모듈만 쓴다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.domain.host_names import canonical_host_key

from noise_gate.domain.apm_host_map import match_key_for
from noise_gate.domain.biz_service_keys import (
    INSTANCE_SCOPES,
    KIND_APM_DOMAIN,
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    apm_domain_member_key,
    apm_instance_member_key,
)

#: D1 판독 코드(R1~R6) — 메모의 `verdict`가 이 형식일 때만 싣는다.
_D1_VERDICT = re.compile(r"^R[1-6]$")
#: 대상 구성원의 확신도를 못 찾았을 때(귀속은 됐지만 대상 구성원 행이 없음) 쓰는 값.
_CONFIDENCE_FALLBACK = "inferred"
_CONFIRMED = "confirmed"


@dataclass(frozen=True)
class BizTarget:
    """조사 대상 식별자 — 대상 자신을 구성원 목록에서 빼고 「같은 코드·도메인」 순서를 정한다.

    Attributes:
        sms_keys: 대상 SMS 서버의 구성원 정규 키(`{db_id}|{resource_id}`) — 210 연계 대상 등
        hostname: 대상 호스트 이름(정규 호스트 키로 SMS 구성원과 대조)
        db_id: 대상 호스트 이름 대조를 그 db_id 구성원으로 좁힌다(빈 값이면 좁히지 않음 — APM 알람)
        apm: APM 알람의 `(source_id, domain_id, instance_name)`(아니면 None)
        link_hosts: 연계 대상 서버의 `(db_id, 호스트 이름)` — 자원 id 없이 연결된 대상도 같은
            db_id·같은 정규 호스트 키 구성원으로 뺀다(APM 알람의 확정 연결 등)
    """

    sms_keys: frozenset[str] = field(default_factory=frozenset)
    hostname: str = ""
    db_id: str = ""
    apm: tuple[str, str, str] | None = None
    link_hosts: frozenset[tuple[str, str]] = field(default_factory=frozenset)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _id_order(value: str) -> tuple[int, int, str]:
    """식별자 정렬 키 — 숫자만이면 수치 순, 아니면 문자열 순(숫자가 앞)."""
    return (0, int(value), "") if value.isdigit() else (1, 0, value)


def _apm_target_keys(apm: tuple[str, str, str] | None) -> tuple[set[str], str | None]:
    if apm is None:
        return set(), None
    source_id, domain_id, instance_name = apm
    keys = {
        k
        for k in (
            apm_instance_member_key(source_id, domain_id, s, match_key_for(s, instance_name))
            for s in INSTANCE_SCOPES
        )
        if k
    }
    return keys, apm_domain_member_key(source_id, domain_id)


def _d1_parts(d1: Mapping[str, Any] | None) -> tuple[str | None, set[tuple[str, str]]]:
    """D1 메모 → (판독 코드 | None, 이상 구성원 `(kind, member_key)` 집합). 형식이 틀리면 무시."""
    if not isinstance(d1, Mapping):
        return None, set()
    verdict = _text(d1.get("verdict"))
    if not _D1_VERDICT.match(verdict):
        return None, set()
    anomalous: set[tuple[str, str]] = set()
    for item in d1.get("anomalous_members") or ():
        if isinstance(item, Mapping):
            kind, key = _text(item.get("kind")), _text(item.get("member_key"))
            if kind and key:
                anomalous.add((kind, key))
    return verdict, anomalous


def _link_host_keys(target: BizTarget) -> set[tuple[str, str]]:
    keys = {(_text(d), canonical_host_key(_text(h))) for d, h in target.link_hosts}
    return {k for k in keys if all(k)}


def _is_target_sms(
    member: Mapping[str, Any], target: BizTarget, host: str, links: set[tuple[str, str]]
) -> bool:
    if _text(member.get("member_key")) in target.sms_keys:
        return True
    db_id = _text(member.get("db_id"))
    hosts = {canonical_host_key(_text(member.get(f))) for f in ("hostname", "server_name")}
    if any((db_id, h) in links for h in hosts):
        return True
    if not host or (target.db_id and db_id != target.db_id):
        return False
    return host in hosts


def build_biz_service_context(
    service: Mapping[str, Any],
    target: BizTarget,
    *,
    allowed_zones: Collection[str] | None,
    max_sms: int,
    max_apm: int,
    d1: Mapping[str, Any] | None = None,
    member_zone: Callable[[Mapping[str, Any]], str] | None = None,
) -> dict[str, Any] | None:
    """서비스 사본 + 대상 → `meta.biz_service` 값(서비스 id가 없으면 None).

    Args:
        service: 서비스 사본(`id`·`display_name`·`members[]`)
        target: 조사 대상 식별자
        allowed_zones: 허용 존 코드 집합(None = 거르지 않음). 구성원 존이 집합 안일 때만 싣는다
        max_sms: SMS 구성원 상한
        max_apm: APM 인스턴스 구성원 상한
        d1: D1 메모(`verdict`·`anomalous_members`) — 형식이 맞을 때만 순서·`d1_verdict`에 쓴다
        member_zone: 구성원 → 존 코드(기본은 사본의 `zone` 칸)

    Returns:
        `{service_id, display_name, confidence, sms_members, apm_members, truncated[, d1_verdict]}`
    """
    try:
        service_id = int(service.get("id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    zone_of = member_zone or (lambda m: _text(m.get("zone")))
    verdict, anomalous = _d1_parts(d1)
    host = canonical_host_key(target.hostname)
    links = _link_host_keys(target)
    apm_keys, apm_dom_key = _apm_target_keys(target.apm)
    members = [m for m in service.get("members") or () if isinstance(m, Mapping)]

    target_members: list[Mapping[str, Any]] = []
    sms: dict[tuple[str, str], Mapping[str, Any]] = {}
    apm: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for m in members:
        kind, key = _text(m.get("kind")), _text(m.get("member_key"))
        if kind == KIND_SMS_SERVER:
            if _is_target_sms(m, target, host, links):
                target_members.append(m)
                continue
            ref = (_text(m.get("db_id")), _text(m.get("resource_id")))
            if all(ref):
                sms.setdefault(ref, m)
        elif kind == KIND_APM_INSTANCE:
            if key in apm_keys:
                target_members.append(m)
                continue
            ref3 = (
                _text(m.get("source_id")), _text(m.get("domain_id")), _text(m.get("instance_name"))
            )
            if all(ref3):
                apm.setdefault(ref3, m)
        elif kind == KIND_APM_DOMAIN and apm_dom_key and key == apm_dom_key:
            target_members.append(m)

    def _zone_ok(m: Mapping[str, Any]) -> bool:
        return allowed_zones is None or zone_of(m) in allowed_zones

    prefixes = {
        _text(m.get("prefix_code"))
        for m in target_members
        if _text(m.get("kind")) == KIND_SMS_SERVER and _text(m.get("prefix_code"))
    }
    domains = {
        (_text(m.get("source_id")), _text(m.get("domain_id")))
        for m in target_members
        if _text(m.get("kind")) != KIND_SMS_SERVER
    }
    if target.apm is not None:
        domains.add((target.apm[0], target.apm[1]))

    def _flag(m: Mapping[str, Any]) -> int:
        return 0 if (_text(m.get("kind")), _text(m.get("member_key"))) in anomalous else 1

    sms_rows = sorted(
        ((ref, m) for ref, m in sms.items() if _zone_ok(m)),
        key=lambda x: (
            _flag(x[1]),
            0 if _text(x[1].get("prefix_code")) in prefixes else 1,
            x[0][0],
            _id_order(x[0][1]),
        ),
    )
    apm_rows = sorted(
        ((ref, m) for ref, m in apm.items() if _zone_ok(m)),
        key=lambda x: (_flag(x[1]), 0 if x[0][:2] in domains else 1, *x[0][:2], x[0][2]),
    )
    confidences = [_text(m.get("confidence")) for m in target_members if _text(m.get("confidence"))]
    out: dict[str, Any] = {
        "service_id": service_id,
        "display_name": _text(service.get("display_name")),
        "confidence": _CONFIRMED if _CONFIRMED in confidences
        else (confidences[0] if confidences else _CONFIDENCE_FALLBACK),
        "sms_members": [
            {
                "db_id": ref[0],
                "server_name": _text(m.get("server_name")),
                "hostname": _text(m.get("hostname")),
                "confidence": _text(m.get("confidence")) or _CONFIDENCE_FALLBACK,
            }
            for ref, m in sms_rows[: max(0, max_sms)]
        ],
        "apm_members": [
            {
                "source_id": ref[0],
                "domain_id": ref[1],
                "instance_name": ref[2],
                "confidence": _text(m.get("confidence")) or _CONFIDENCE_FALLBACK,
            }
            for ref, m in apm_rows[: max(0, max_apm)]
        ],
        "truncated": len(sms_rows) > max_sms or len(apm_rows) > max_apm,
    }
    if verdict:
        out["d1_verdict"] = verdict
    return out


__all__ = ["BizTarget", "build_biz_service_context"]
