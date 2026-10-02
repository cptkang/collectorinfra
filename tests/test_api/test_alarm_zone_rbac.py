"""Plan 59 Part C — 알림 지역 스코프 RBAC 회귀 테스트(§17).

검증 대상:
- routing.zones 존↔db_id 매핑 단일 출처.
- dependencies.alarm_zones_for_user 존 산출(admin=전존/운영자=해당존/일반=빈집합/개발=전존).
- 구독자별 이벤트 가시성(공동존↔은행존 격리, 중복 역할=전존, 일반=403).
- resolve_stream_user 쿠키 우선 인증 + 개발 모드 우회.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.api.dependencies import ANONYMOUS_USER, alarm_zones_for_user, resolve_stream_user
from src.routing.zones import (
    ZONE_BANKJON,
    ZONE_GONGJON,
    all_zones,
    db_id_to_zone,
    normalize_zones,
    zone_to_db_ids,
)


# --- 존 매핑 단일 출처 ---


def test_zone_to_db_ids():
    assert set(zone_to_db_ids(ZONE_GONGJON)) == {"polestar_cm_gp", "polestar_cm_yd"}
    assert zone_to_db_ids(ZONE_BANKJON) == ["polestar_b0"]
    assert zone_to_db_ids("unknown") == []


def test_db_id_to_zone():
    assert db_id_to_zone("polestar_cm_gp") == ZONE_GONGJON
    assert db_id_to_zone("polestar_cm_yd") == ZONE_GONGJON
    assert db_id_to_zone("polestar_b0") == ZONE_BANKJON
    assert db_id_to_zone("cloud_portal") is None
    assert db_id_to_zone(None) is None


def test_normalize_zones_filters_and_dedups():
    assert normalize_zones([ZONE_GONGJON, "bogus", ZONE_GONGJON, ZONE_BANKJON]) == [
        ZONE_GONGJON, ZONE_BANKJON,
    ]
    assert normalize_zones(None) == []


# --- alarm_zones_for_user ---


def test_zones_admin_gets_all():
    admin = {"role": "admin", "alarm_zones": None}
    assert alarm_zones_for_user(admin, auth_enabled=True) == set(all_zones())


def test_zones_dev_mode_gets_all():
    assert alarm_zones_for_user(None, auth_enabled=False) == set(all_zones())


def test_zones_operator_gets_only_assigned():
    op = {"role": "user", "alarm_zones": [ZONE_GONGJON]}
    assert alarm_zones_for_user(op, auth_enabled=True) == {ZONE_GONGJON}


def test_zones_general_user_empty():
    general = {"role": "user", "alarm_zones": []}
    assert alarm_zones_for_user(general, auth_enabled=True) == set()


def test_zones_dual_role_union():
    dual = {"role": "user", "alarm_zones": [ZONE_GONGJON, ZONE_BANKJON]}
    assert alarm_zones_for_user(dual, auth_enabled=True) == {ZONE_GONGJON, ZONE_BANKJON}


# --- 구독자별 이벤트 가시성(엔드포인트 필터 로직 미러) ---


def _would_deliver(user, auth_enabled, db_id):
    """alarm_notifications_stream의 인가+필터 판정을 재현한다."""
    zones = alarm_zones_for_user(user, auth_enabled)
    if not zones:
        return "403"  # 구독 거부
    if zones >= set(all_zones()):
        return True  # 전 존
    z = db_id_to_zone(db_id)
    return z is not None and z in zones


def test_gongjon_operator_isolation():
    op = {"role": "user", "alarm_zones": [ZONE_GONGJON]}
    assert _would_deliver(op, True, "polestar_cm_gp") is True
    assert _would_deliver(op, True, "polestar_cm_yd") is True
    assert _would_deliver(op, True, "polestar_b0") is False


def test_bankjon_operator_isolation():
    op = {"role": "user", "alarm_zones": [ZONE_BANKJON]}
    assert _would_deliver(op, True, "polestar_b0") is True
    assert _would_deliver(op, True, "polestar_cm_gp") is False


def test_general_user_gets_403():
    general = {"role": "user", "alarm_zones": []}
    assert _would_deliver(general, True, "polestar_b0") == "403"


def test_admin_receives_all_zones():
    admin = {"role": "admin", "alarm_zones": None}
    for db in ("polestar_cm_gp", "polestar_cm_yd", "polestar_b0"):
        assert _would_deliver(admin, True, db) is True


def test_dual_role_receives_all():
    dual = {"role": "user", "alarm_zones": [ZONE_GONGJON, ZONE_BANKJON]}
    for db in ("polestar_cm_gp", "polestar_cm_yd", "polestar_b0"):
        assert _would_deliver(dual, True, db) is True


# --- resolve_stream_user (쿠키 인증) ---


def _fake_request(auth_enabled=True, cookies=None, headers=None, user_repo=None):
    config = SimpleNamespace(
        auth=SimpleNamespace(enabled=auth_enabled, jwt_secret="auth-secret"),
        admin=SimpleNamespace(jwt_secret="admin-secret"),
    )
    state = SimpleNamespace(config=config, user_repo=user_repo)
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        cookies=cookies or {},
        headers=headers or {},
    )


async def test_resolve_stream_user_dev_bypass():
    req = _fake_request(auth_enabled=False)
    assert await resolve_stream_user(req) == ANONYMOUS_USER


async def test_resolve_stream_user_no_token_none():
    req = _fake_request(auth_enabled=True)
    assert await resolve_stream_user(req) is None


async def test_resolve_stream_user_cookie_token():
    import jwt
    from datetime import datetime, timedelta, timezone

    token = jwt.encode(
        {"sub": "u1", "type": "user", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "auth-secret",
        algorithm="HS256",
    )
    # user_repo 없이 payload 폴백 경로
    req = _fake_request(auth_enabled=True, cookies={"user_token": token}, user_repo=None)
    result = await resolve_stream_user(req)
    assert result is not None
    assert result["sub"] == "u1"
