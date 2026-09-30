"""plans/87 J8 · D-287 ④ · F-7 — APM 알람(dbId `jennifer_<소스 id>`)의 존 판정·전달·ack.

F-7(현행 결함): `dbId=jennifer`는 레지스트리 DB가 아니라 존이 None → 존 일부만 구독하는 사용자에게
APM 알람이 가지 않고 ack·피드백도 「존 매핑 없는 db_id는 거부」로 막혔다. J8은 레지스트리
`solutions[apm].sources`로 존을 풀어 이를 해소한다 — 라우트 코드는 그대로이고 `db_id_to_zone`만
바뀐다.

검증은 스트림·쓰기 경로가 **실제로 호출하는 함수**(`event_visible_to`·`_zone_permits`)와 ack 라우트
(`_assert_zone_access`)로 한다(로직 미러 아님). 존 없는 소스(`jennifer` 단일 설정 · 표에 없는
소스)는
종전대로 전 존 구독자·관리자만 본다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.api.routes import alarm as alarm_routes
from src.api.routes.alarm import _zone_permits, event_visible_to
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON, db_id_to_zone

BANK = "jennifer_bank"
COMMON = "jennifer_common"
LEGACY = "jennifer_legacy"
DEFAULT = "jennifer"          # 게이트웨이 단일 설정(소스 default) — 존 없음
UNKNOWN = "jennifer_zzz"      # 레지스트리에 없는 소스 — 존 없음 + 경고 1회

OPERATOR_BANKJON = {"sub": "op-b", "role": "user", "alarm_zones": [ZONE_BANKJON]}
OPERATOR_GONGJON = {"sub": "op-g", "role": "user", "alarm_zones": [ZONE_GONGJON]}
DUAL = {"sub": "op-d", "role": "user", "alarm_zones": [ZONE_GONGJON, ZONE_BANKJON]}
ADMIN = {"sub": "adm", "role": "admin", "alarm_zones": None}


# ── 존 판정 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "db_id,zone",
    [(BANK, ZONE_BANKJON), (COMMON, ZONE_GONGJON), (LEGACY, ZONE_BANKJON),
     (DEFAULT, None), (UNKNOWN, None)],
)
def test_db_id_to_zone_resolves_apm_sources(db_id: str, zone: str | None) -> None:
    assert db_id_to_zone(db_id) == zone


def test_db_zone_mapping_unchanged() -> None:
    assert db_id_to_zone("polestar_b0") == ZONE_BANKJON
    assert db_id_to_zone("polestar_cm_gp") == ZONE_GONGJON
    assert db_id_to_zone("cloud_portal") is None
    assert db_id_to_zone(None) is None


# ── SSE 전달(event_visible_to) ─────────────────────────────────────────────


def _visible(
    db_id: str, zones: set[str], *, deliver_all: bool = False, is_admin: bool = False
) -> bool:
    return event_visible_to({"db_id": db_id, "tier": "page"}, allowed_zones=zones,
                            deliver_all=deliver_all, is_admin=is_admin)


def test_zone_subscriber_receives_own_zone_apm_alarms() -> None:
    assert _visible(BANK, {ZONE_BANKJON}) is True
    assert _visible(LEGACY, {ZONE_BANKJON}) is True        # 레거시 = 은행존(G-14 ①)
    assert _visible(COMMON, {ZONE_GONGJON}) is True


def test_zone_subscriber_does_not_receive_other_zone_apm_alarms() -> None:
    assert _visible(COMMON, {ZONE_BANKJON}) is False
    assert _visible(BANK, {ZONE_GONGJON}) is False
    assert _visible(LEGACY, {ZONE_GONGJON}) is False


@pytest.mark.parametrize("db_id", [DEFAULT, UNKNOWN])
def test_zoneless_source_only_for_all_zone_subscribers_and_admin(db_id: str) -> None:
    assert _visible(db_id, {ZONE_BANKJON}) is False
    assert _visible(db_id, {ZONE_GONGJON}) is False
    assert _visible(db_id, {ZONE_BANKJON, ZONE_GONGJON}, deliver_all=True) is True
    assert _visible(db_id, {ZONE_BANKJON, ZONE_GONGJON}, deliver_all=True, is_admin=True) is True


def test_suppress_rule_still_applies_to_apm_alarms() -> None:
    event = {"db_id": BANK, "tier": "suppress"}
    assert event_visible_to(
        event, allowed_zones={ZONE_BANKJON}, deliver_all=False, is_admin=False
    ) is False


# ── 쓰기·조회 경로(_zone_permits) ──────────────────────────────────────────


def test_zone_permits_apm_sources() -> None:
    assert _zone_permits({ZONE_BANKJON}, BANK) is True
    assert _zone_permits({ZONE_BANKJON}, LEGACY) is True
    assert _zone_permits({ZONE_BANKJON}, COMMON) is False
    assert _zone_permits({ZONE_GONGJON}, COMMON) is True
    assert _zone_permits({ZONE_GONGJON}, DEFAULT) is False
    assert _zone_permits(None, DEFAULT) is True            # 전 존(관리자)


# ── ack 라우트(_assert_zone_access) ────────────────────────────────────────


class _FakeIncidentStore:
    """IncidentStore 대역 — get_db_id/ack만 쓴다(test_alarm_feedback_rbac 동형)."""

    def __init__(self, db_id: str | None) -> None:
        self._db_id = db_id
        self.ack_calls: list[int] = []

    async def get_db_id(self, incident_id: int) -> str | None:
        return self._db_id

    async def ack(self, *, incident_id: int, acked_at, acked_by: str) -> bool:  # noqa: ANN001
        self.ack_calls.append(incident_id)
        return True


def _ack(user: dict, db_id: str) -> tuple[int, list[int]]:
    store = _FakeIncidentStore(db_id)
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(auth=SimpleNamespace(enabled=True))
    app.state.incident_store = store
    app.dependency_overrides[require_user] = lambda: user
    resp = TestClient(app).post("/api/v1/alarm/incidents/11/ack")
    return resp.status_code, store.ack_calls


def test_bankjon_operator_can_ack_bank_and_legacy_apm_incidents() -> None:
    assert _ack(OPERATOR_BANKJON, BANK) == (200, [11])
    assert _ack(OPERATOR_BANKJON, LEGACY) == (200, [11])


def test_other_zone_operator_cannot_ack_apm_incident() -> None:
    assert _ack(OPERATOR_GONGJON, BANK) == (403, [])
    assert _ack(OPERATOR_BANKJON, COMMON) == (403, [])


def test_zoneless_source_ack_only_for_all_zones_or_admin() -> None:
    assert _ack(OPERATOR_GONGJON, DEFAULT) == (403, [])
    assert _ack(DUAL, DEFAULT) == (200, [11])
    assert _ack(ADMIN, DEFAULT) == (200, [11])
