"""plans/215 W6 — 업무 현황 보기 API(요약 · 상세 · heartbeat) · 기능 조회 4키 · 라우트 등록 조건.

인가는 실 `require_user`(사용자 JWT `type=user` · `user_repo` 대역)로 본다. 집계기는 가짜 저장소로
미리 만들어 `app.state.biz_monitor_alarms`에 둔다(라우트가 새로 만들지 않는다).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.application.biz_monitor_alarms import BizAlarmAggregator
from src.api.routes import biz_monitor
from tests.mocks.async_redis import FakeAsyncRedis

AUTH_SECRET = "auth-secret-215w6-0123456789abcdef0123"
ADMIN_SECRET = "admin-secret-215w6-0123456789abcdef012"
BASE = "/api/v1/biz-services/monitor"
ZA, ZB = "gongjon", "bankjon"


def _token(payload: dict[str, Any], secret: str) -> str:
    return jwt.encode(
        {**payload, "exp": datetime.now(UTC) + timedelta(hours=1)}, secret, algorithm="HS256"
    )


def _hdr(sub: str, kind: str = "user") -> dict[str, str]:
    return {"Authorization": "Bearer " + _token({"sub": sub, "type": kind}, AUTH_SECRET)}


class _Users:
    """`a`만 관리자 · `u`는 공동존 사용자 · `n`은 존 없음."""

    async def get_by_user_id(self, user_id: str):  # noqa: ANN201
        from src.domain.user import User, UserRole

        role = UserRole.ADMIN if user_id == "a" else UserRole.USER
        zones = {"u": [ZA], "n": []}.get(user_id)
        return User(user_id=user_id, username=user_id, hashed_password="x", role=role,
                    alarm_zones=zones)


class _Cache:
    """사본 대역 — 귀속은 호스트 이름 → 서비스 표."""

    def __init__(self, services: dict[int, dict[str, Any]], redis: FakeAsyncRedis) -> None:
        self.services = services
        self.redis = redis

    async def list_service_ids(self) -> list[int]:
        return sorted(self.services)

    async def get_service(self, sid: int) -> dict[str, Any] | None:
        return self.services.get(sid)

    async def attribute(self, *, polestar_link_targets: Any = None, apm: Any = None,
                        hostname: str | None = None, db_id: str | None = None) -> Any:
        for sid, svc in self.services.items():
            if any(m.get("hostname") == hostname for m in svc["members"]):
                return SimpleNamespace(service_id=sid, basis="host")
        return None


class _Decisions:
    enabled = True

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.calls = 0

    def iter_recent_decisions(self, window_seconds: int) -> Any:
        self.calls += 1
        return iter(self.records)


class _Incidents:
    def __init__(self) -> None:
        self.calls = 0

    async def list_open(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.calls += 1
        return []


def _member(mid: int, rid: str, host: str, zone: str) -> dict[str, Any]:
    return {"id": mid, "kind": "sms_server", "member_key": f"pg|{rid}", "db_id": "pg",
            "resource_id": rid, "hostname": host, "server_name": host, "zone": zone}


def _services() -> dict[int, dict[str, Any]]:
    return {
        1: {"id": 1, "service_key": "loan", "display_name": "여신", "tier": 1, "monitored": True,
            "verified": True, "description": "AKIAIOSFODNN7EXAMPLE", "owner_org": "여신팀",
            "origin": "auto",
            "members": [_member(11, "101", "loan-01", ZA), _member(12, "102", "loan-02", ZB)]},
        2: {"id": 2, "service_key": "card", "display_name": "카드비밀", "tier": 2,
            "monitored": False, "members": [_member(21, "201", "card-01", ZB)]},
    }


def _config(*, metrics: bool = False, auth: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=auth, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
        security=SimpleNamespace(mask_pattern="***"),
        noise_gate=SimpleNamespace(
            biz_monitor_active=True, biz_monitor_metrics_active=metrics,
            biz_monitor_alarm_window_minutes=60,
        ),
    )


def _app(*, metrics: bool = False, auth: bool = True,
         records: list[dict[str, Any]] | None = None) -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    app.include_router(biz_monitor.router, prefix="/api/v1")
    app.state.config = _config(metrics=metrics, auth=auth)
    app.state.user_repo = _Users()
    redis = FakeAsyncRedis()
    cache = _Cache(_services(), redis)
    app.state.biz_service_cache = cache
    app.state.decisions = _Decisions(records if records is not None else [
        {"ts": datetime.now(UTC).isoformat(), "alarm_id": "r1", "tier": "page",
         "signals": {"severity": 3}, "db_id": "pg", "hostname": "loan-01"},
        {"ts": datetime.now(UTC).isoformat(), "alarm_id": "r2", "tier": "suppress",
         "signals": {"severity": 1}, "db_id": "pg", "hostname": "loan-02"},
    ])
    app.state.incidents = _Incidents()
    app.state.biz_monitor_alarms = BizAlarmAggregator(
        cache=cache, incident_store=app.state.incidents, decision_store=app.state.decisions,
        window_minutes=60,
        settings_loader=lambda: SimpleNamespace(config=SimpleNamespace(sms_thresholds={}),
                                                generic_terms=frozenset()),
    )
    return TestClient(app), app


# ─── 인가 ──────────────────────────────────────────────────────────────


def test_unauthenticated_401() -> None:
    client, _ = _app()
    assert client.get(BASE).status_code == 401
    assert client.get(f"{BASE}/1").status_code == 401
    assert client.post(f"{BASE}/heartbeat").status_code == 401


def test_admin_token_type_rejected_on_user_path() -> None:
    """사용자 시크릿으로 서명했어도 `type`이 user가 아니면 거부(서명만으로 끝내지 않는다)."""
    client, _ = _app()
    assert client.get(BASE, headers=_hdr("u", kind="admin")).status_code == 401


def test_plain_user_sees_own_zone_only() -> None:
    client, _ = _app()
    res = client.get(BASE, headers=_hdr("u"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert [s["id"] for s in body["services"]] == [1]
    assert body["hidden_service_count"] == 1
    item = body["services"][0]
    assert (item["member_count"], item["hidden_member_count"]) == (1, 1)
    assert item["alarms"]["recent_total"] == 1
    text = json.dumps(body, ensure_ascii=False)
    assert "카드비밀" not in text and "loan-02" not in text
    assert "정상" not in text


def test_no_zone_user_counts_only() -> None:
    client, _ = _app()
    body = client.get(BASE, headers=_hdr("n")).json()
    assert body["services"] == [] and body["hidden_service_count"] == 2
    assert client.get(f"{BASE}/1", headers=_hdr("n")).status_code == 404


def test_admin_sees_all() -> None:
    client, _ = _app()
    body = client.get(BASE, headers=_hdr("a")).json()
    assert sorted(s["id"] for s in body["services"]) == [1, 2]
    assert body["hidden_service_count"] == 0


# ─── 상세 ──────────────────────────────────────────────────────────────


def test_detail_keys_mask_and_hidden_members() -> None:
    client, _ = _app()
    res = client.get(f"{BASE}/1", headers=_hdr("u"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"generated_at", "window_minutes", "metrics_status", "service",
                         "members", "hidden_member_count", "unattributed_service_alarms",
                         "notices"}
    assert [m["name"] for m in body["members"]] == ["loan-01"]
    assert body["hidden_member_count"] == 1
    head = body["service"]
    assert head["description"] == "***"   # 값 패턴 마스킹(사용자 경로)
    assert head["owner_org"] == "여신팀"
    assert {"service_key", "evidence", "sms_notes", "origin"} <= set(head)
    admin = client.get(f"{BASE}/1", headers=_hdr("a")).json()
    assert admin["service"]["description"] == "AKIAIOSFODNN7EXAMPLE"   # 운영자는 원문
    assert len(admin["members"]) == 2


def test_detail_out_of_scope_and_missing_404() -> None:
    client, _ = _app()
    assert client.get(f"{BASE}/2", headers=_hdr("u")).status_code == 404
    assert client.get(f"{BASE}/99", headers=_hdr("a")).status_code == 404
    assert client.get(f"{BASE}/abc", headers=_hdr("a")).status_code == 422


# ─── 지표 수집 상태 · M3 · heartbeat ─────────────────────────────────────


def test_metrics_off_no_redis_reads_and_off_status() -> None:
    client, app = _app(metrics=False)
    body = client.get(BASE, headers=_hdr("a")).json()
    assert body["metrics_status"]["state"] == "off"
    assert body["metrics_status"]["label"] == "꺼짐"
    item = next(s for s in body["services"] if s["id"] == 1)
    assert item["reasons"][0] == "지표 수집 꺼짐" and item["metrics"] is None
    assert app.state.biz_service_cache.redis._data == {}


def test_metrics_on_reads_monitor_key() -> None:
    client, app = _app(metrics=True)
    redis = app.state.biz_service_cache.redis

    import asyncio

    async def seed() -> None:
        await redis.set("bizmon:svc:1", json.dumps({
            "updated_at": "2026-11-05T10:00:00+00:00", "sms_stat_time": "2026-11-05T10:00:00",
            "sms_metrics": [{"member_key": "pg|101", "metric": "cpu", "value": 40},
                            {"member_key": "pg|102", "metric": "cpu", "value": 99}],
        }))
        await redis.set("bizmon:status", json.dumps({"state": "collecting",
                                                     "last_success_at": "2026-11-05T10:00:00"}))

    asyncio.run(seed())
    body = client.get(BASE, headers=_hdr("u")).json()
    assert body["metrics_status"]["state"] == "collecting"
    assert body["metrics_status"]["label"] == "수집 중"
    item = body["services"][0]
    assert item["metrics"]["sms_max"] == {"cpu": 40.0}   # 권한 밖 구성원 값은 빠진다
    detail = client.get(f"{BASE}/1", headers=_hdr("u")).json()
    assert detail["members"][0]["sms"] == {"cpu": 40.0}
    assert "99" not in json.dumps(detail["members"])


def test_heartbeat_sets_viewers_ex_120() -> None:
    client, app = _app(metrics=True)
    res = client.post(f"{BASE}/heartbeat", headers=_hdr("u"))
    assert res.status_code == 200, res.text
    assert res.json() == {"recorded": True, "ttl_seconds": 120, "reason": ""}
    redis = app.state.biz_service_cache.redis

    import asyncio

    ttl = asyncio.run(redis.ttl("bizmon:viewers"))
    assert 0 < ttl <= 120


def test_heartbeat_metrics_off_records_nothing() -> None:
    client, app = _app(metrics=False)
    res = client.post(f"{BASE}/heartbeat", headers=_hdr("u"))
    assert res.status_code == 200
    assert res.json()["recorded"] is False
    assert app.state.biz_service_cache.redis._data == {}


def test_heartbeat_without_redis_503() -> None:
    client, app = _app(metrics=True)
    app.state.biz_service_cache = None
    assert client.post(f"{BASE}/heartbeat", headers=_hdr("u")).status_code == 503


def test_memo_shared_between_boards() -> None:
    """보드 여러 개(사용자 여럿)의 요청도 10초 안이면 저장소 호출 1회."""
    client, app = _app()
    for sub in ("u", "a", "u", "n"):
        assert client.get(BASE, headers=_hdr(sub)).status_code == 200
    client.get(f"{BASE}/1", headers=_hdr("a"))
    assert app.state.decisions.calls == 1 and app.state.incidents.calls == 1


# ─── 플래그 off = 라우트 0건 · 기능 조회 4키 ───────────────────────────


@pytest.mark.parametrize("enabled", [False, True])
def test_create_app_registers_routes_only_when_active(enabled: bool) -> None:
    from src.api.server import create_app
    from src.config import AppConfig, AuthConfig, NoiseGateConfig

    app = create_app(
        AppConfig(
            db_backend="direct",
            db_connection_string="",
            log_level="WARNING",
            auth=AuthConfig(enabled=False),
            noise_gate=NoiseGateConfig(biz_service_enabled=enabled, biz_monitor_enabled=True),
        )
    )
    paths = {p for p in app.openapi()["paths"] if p.startswith(BASE)}
    if not enabled:
        assert paths == set()
        return
    # 뒤 Wave(W8 topology·trend · W8b events · W8c metrics · W9 진단)가
    # 라우트를 더하므로 W6 라우트 포함만 단언
    assert {BASE, f"{BASE}/{{service_id}}", f"{BASE}/heartbeat"} <= paths


def _caps(**ng: Any) -> dict[str, Any]:
    from src.api.dependencies import require_user
    from src.api.routes import alarm as alarm_routes
    from src.config.alarm import NoiseGateConfig

    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=False), noise_gate=NoiseGateConfig(_env_file=None, **ng)
    )
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app).get("/api/v1/alarm/capabilities").json()


_BIZ_KEYS = ("biz_service_enabled", "biz_monitor_enabled", "biz_diagnosis_enabled",
             "biz_history_enabled")


def test_capabilities_biz_keys_off_and_existing_keys_unchanged() -> None:
    off = _caps()
    assert {k: off[k] for k in _BIZ_KEYS} == dict.fromkeys(_BIZ_KEYS, False)
    on = _caps(biz_service_enabled=True, biz_monitor_enabled=True, biz_diagnosis_enabled=True,
               biz_monitor_history_enabled=True)
    assert {k: on[k] for k in _BIZ_KEYS} == dict.fromkeys(_BIZ_KEYS, True)
    # 종전 키는 값·집합 모두 그대로(가산만)
    assert {k: v for k, v in off.items() if k not in _BIZ_KEYS} == {
        k: v for k, v in on.items() if k not in _BIZ_KEYS
    }
    assert "card_flow_enabled" not in off


def test_capabilities_dependent_flags() -> None:
    """의존 플래그는 `*_active`로 — 업무 서비스가 꺼져 있으면 현황·진단·지표도 거짓."""
    body = _caps(biz_monitor_enabled=True, biz_diagnosis_enabled=True,
                 biz_monitor_history_enabled=True)
    assert {k: body[k] for k in _BIZ_KEYS} == dict.fromkeys(_BIZ_KEYS, False)
