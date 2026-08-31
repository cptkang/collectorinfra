"""Plan 83 T10 — SSE 이벤트 가시성(존 × SUPPRESS 권한) 판정.

`event_visible_to`는 스트림 엔드포인트가 실제로 호출하는 순수 함수다(로직 미러가 아니라
구현 그 자체를 검증한다). 표시 레벨(page/ticket/dashboard)은 여기서 거르지 않는다 —
개인 선호는 브라우저에 있고 서버는 그 값을 모른다(G-1 localStorage 확정).
"""

from __future__ import annotations

from src.api.routes.alarm import event_visible_to
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON

GONGJON_EVENT = {"db_id": "polestar_cm_gp", "tier": "dashboard"}
BANKJON_EVENT = {"db_id": "polestar_b0", "tier": "dashboard"}
SUPPRESSED = {"db_id": "polestar_cm_gp", "tier": "suppress"}
NO_TIER = {"db_id": "polestar_cm_gp"}


def _visible(event, zones=frozenset({ZONE_GONGJON}), deliver_all=False, is_admin=False):
    return event_visible_to(
        event, allowed_zones=set(zones), deliver_all=deliver_all, is_admin=is_admin
    )


# --- 존 규약(회귀 0) ---

def test_zone_isolation_preserved():
    assert _visible(GONGJON_EVENT) is True
    assert _visible(BANKJON_EVENT) is False


def test_deliver_all_bypasses_zone():
    assert _visible(BANKJON_EVENT, deliver_all=True, is_admin=True) is True


def test_unmapped_db_id_not_delivered_to_scoped_user():
    assert _visible({"db_id": "cloud_portal", "tier": "page"}) is False


# --- SUPPRESS 권한(T10 신규) ---

def test_suppress_hidden_from_non_admin():
    """억제 알람은 비관리자 브라우저에 **도달조차 하지 않는다**."""
    assert _visible(SUPPRESSED) is False


def test_suppress_hidden_even_with_zone_access():
    """자기 존 알람이어도 SUPPRESS는 권한이 따로다."""
    assert _visible(SUPPRESSED, zones={ZONE_GONGJON}, is_admin=False) is False


def test_suppress_delivered_to_admin():
    assert _visible(SUPPRESSED, deliver_all=True, is_admin=True) is True


def test_suppress_zone_still_applies_to_admin_scope():
    """관리자 판정이 존 규약을 대체하지는 않는다(deliver_all이 아닌 경우)."""
    assert _visible(
        {"db_id": "polestar_b0", "tier": "suppress"}, zones={ZONE_GONGJON}, is_admin=True
    ) is False


# --- 티어 미상 / 일반 티어 ---

def test_missing_tier_passes():
    """analyze 테스트 경로 payload에는 tier가 없다 — 막으면 테스트 카드가 사라진다."""
    assert _visible(NO_TIER) is True


def test_normal_tiers_not_filtered_by_server():
    """page/ticket/dashboard는 서버가 거르지 않는다(개인 레벨은 클라이언트 소관)."""
    for tier in ("page", "ticket", "dashboard"):
        assert _visible({"db_id": "polestar_cm_gp", "tier": tier}) is True
