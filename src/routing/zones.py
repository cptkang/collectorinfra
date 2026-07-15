"""알림 지역 스코프(존) ↔ db_id 매핑 단일 출처 (Plan 59 Part C / §17).

지역 스코프 RBAC에서 사용자의 알림 수신 범위를 '존' 단위로 표현하고, 알람 이벤트의
db_id를 존으로 역해소해 구독자별로 필터링한다. 존↔db_id 매핑은 이 모듈에만 두어
하드코딩 분산을 막는다(CLAUDE.md 존 라우팅 교훈). 신규 존/DB 편입 시 여기만 갱신한다.
"""

from __future__ import annotations

# 존 코드 상수(단일 출처)
ZONE_GONGJON = "gongjon"   # 공동존(K리전): 김포 + 여의도
ZONE_BANKJON = "bankjon"   # 은행존(K리전 은행/레거시)

# 존 → 해당 존에 속한 db_id 목록
_ZONE_TO_DB_IDS: dict[str, list[str]] = {
    ZONE_GONGJON: ["polestar_cm_gp", "polestar_cm_yd"],
    ZONE_BANKJON: ["polestar_b0"],
}

# db_id → 존 (역방향 인덱스)
_DB_ID_TO_ZONE: dict[str, str] = {
    db_id: zone for zone, db_ids in _ZONE_TO_DB_IDS.items() for db_id in db_ids
}


def all_zones() -> list[str]:
    """정의된 모든 존 코드를 반환한다."""
    return list(_ZONE_TO_DB_IDS.keys())


def zone_to_db_ids(zone: str) -> list[str]:
    """존 코드에 속한 db_id 목록을 반환한다(미정의 존이면 빈 목록)."""
    return list(_ZONE_TO_DB_IDS.get(zone, []))


def db_id_to_zone(db_id: str | None) -> str | None:
    """db_id가 속한 존 코드를 반환한다(매핑 없으면 None)."""
    if not db_id:
        return None
    return _DB_ID_TO_ZONE.get(db_id)


def normalize_zones(zones: list[str] | None) -> list[str]:
    """입력 존 목록에서 정의된 존만 추려 중복 제거해 반환한다."""
    if not zones:
        return []
    seen: list[str] = []
    for z in zones:
        if z in _ZONE_TO_DB_IDS and z not in seen:
            seen.append(z)
    return seen
