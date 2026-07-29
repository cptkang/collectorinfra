"""알림 지역 스코프(존) ↔ db_id 매핑 (Plan 59 Part C / §17).

지역 스코프 RBAC에서 사용자의 알림 수신 범위를 '존' 단위로 표현하고, 알람 이벤트의
db_id를 존으로 역해소해 구독자별로 필터링한다. 존↔db_id 매핑의 **정본은
`config/db_registry.yaml`**(zones 선언 + DB별 `zone` 필드)이며, 이 모듈은 그 파생
조회 API만 제공한다. 신규 존/DB 편입 시 레지스트리만 갱신한다(Plan 67 R2).
"""

from __future__ import annotations

from src.routing.registry import get_registry

# 존 코드 상수(소비처 하드코딩 방지용 별칭 — 값의 정본은 레지스트리 zones 선언)
ZONE_GONGJON = "gongjon"   # 공동존(K리전): 김포 + 여의도
ZONE_BANKJON = "bankjon"   # 은행존(K리전 은행/레거시)


def all_zones() -> list[str]:
    """정의된 모든 존 코드를 반환한다."""
    return list(get_registry().zone_codes())


def zone_to_db_ids(zone: str) -> list[str]:
    """존 코드에 속한 db_id 목록을 반환한다(미정의 존이면 빈 목록)."""
    return list(get_registry().zone_to_db_ids().get(zone, ()))


def db_id_to_zone(db_id: str | None) -> str | None:
    """db_id가 속한 존 코드를 반환한다(매핑 없으면 None)."""
    if not db_id:
        return None
    entry = get_registry().get(db_id)
    return entry.zone if entry and entry.zone else None


def normalize_zones(zones: list[str] | None) -> list[str]:
    """입력 존 목록에서 정의된 존만 추려 중복 제거해 반환한다."""
    if not zones:
        return []
    defined = set(get_registry().zone_codes())
    seen: list[str] = []
    for z in zones:
        if z in defined and z not in seen:
            seen.append(z)
    return seen
