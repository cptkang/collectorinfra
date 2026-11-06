"""업무 서비스 구성원 종류·정규 키 — plans/215 §3.1·§3.5 (W1 도메인 · W2 저장소·사본 공용 계약).

구성원 정규 키(`biz_service_member.member_key`)와 Redis 색인 필드는 이 모듈의 함수로만 만든다.
구분자는 APM·SMS 연계 키와 같은 `|`다(`apm_host_map.FIELD_SEP` · D-362). 키 조각에 구분자가
들어 있거나 비어 있으면 키를 만들지 않는다(None).

- `sms_server`   : `{db_id}|{resource_id}`
- `apm_instance` : `{source_id}|{domain_id}|{scope}|{match_key}` — scope는 `instance`·`workload`,
  match_key는 `apm_host_map.match_key_for(scope, instance_name)`의 값(210 매핑 키와 같다)
- `apm_domain`   : `{source_id}|{domain_id}`
- `apm_business` : `{source_id}|{domain_id}|{business_id}` (관리자 수동 추가 전용)
"""

from __future__ import annotations

from noise_gate.domain.apm_host_map import FIELD_SEP, SCOPE_INSTANCE, SCOPE_WORKLOAD

KIND_SMS_SERVER = "sms_server"
KIND_APM_INSTANCE = "apm_instance"
KIND_APM_DOMAIN = "apm_domain"
KIND_APM_BUSINESS = "apm_business"
KINDS = (KIND_SMS_SERVER, KIND_APM_INSTANCE, KIND_APM_DOMAIN, KIND_APM_BUSINESS)
APM_KINDS = (KIND_APM_INSTANCE, KIND_APM_DOMAIN, KIND_APM_BUSINESS)
INSTANCE_SCOPES = (SCOPE_INSTANCE, SCOPE_WORKLOAD)

_PARTS = {
    KIND_SMS_SERVER: 2,
    KIND_APM_INSTANCE: 4,
    KIND_APM_DOMAIN: 2,
    KIND_APM_BUSINESS: 3,
}


def _join(*parts: object) -> str | None:
    texts = [str(p if p is not None else "").strip() for p in parts]
    if any(not t or FIELD_SEP in t for t in texts):
        return None
    return FIELD_SEP.join(texts)


def sms_member_key(db_id: object, resource_id: object) -> str | None:
    """SMS 서버 구성원 키 `{db_id}|{resource_id}`."""
    return _join(db_id, resource_id)


def apm_instance_member_key(
    source_id: object, domain_id: object, scope: str, match_key: object
) -> str | None:
    """APM 인스턴스(또는 워크로드) 구성원 키 — scope가 instance·workload가 아니면 None."""
    if scope not in INSTANCE_SCOPES:
        return None
    return _join(source_id, domain_id, scope, match_key)


def apm_domain_member_key(source_id: object, domain_id: object) -> str | None:
    """APM 도메인 구성원 키 `{source_id}|{domain_id}`."""
    return _join(source_id, domain_id)


def apm_business_member_key(
    source_id: object, domain_id: object, business_id: object
) -> str | None:
    """제니퍼 업무 구성원 키 `{source_id}|{domain_id}|{business_id}`."""
    return _join(source_id, domain_id, business_id)


def parse_member_key(kind: str, member_key: str) -> tuple[str, ...] | None:
    """정규 키를 조각으로 되돌린다 — 종류별 조각 수가 맞지 않거나 빈 조각이 있으면 None."""
    n = _PARTS.get(kind)
    if n is None or not isinstance(member_key, str):
        return None
    parts = tuple(member_key.split(FIELD_SEP))
    if len(parts) != n or any(not p for p in parts):
        return None
    if kind == KIND_APM_INSTANCE and parts[2] not in INSTANCE_SCOPES:
        return None
    return parts
