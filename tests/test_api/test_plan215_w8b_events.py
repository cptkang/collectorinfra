"""plans/215 W8b — `GET /biz-services/monitor/{id}/events` 라우트.

인가 · 존 · 억제 규칙 · 마스킹 · 변경 플래그를 본다.

W6 라우트 테스트와 같은 대역(사용자 JWT · 가짜 사본 · 가짜 결정 이력)을 쓴다. 변경 이력은
`ChangeFeed.fetch_recent_changes`를 계측해 플래그가 꺼져 있으면 호출 0임을 본다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.application.biz_monitor_alarms import BizAlarmAggregator
from noise_gate.infrastructure.change_feed import ChangeFeed
from src.api.routes import biz_monitor
from tests.mocks.async_redis import FakeAsyncRedis
from tests.test_api.test_plan215_w6_biz_monitor import (
    ADMIN_SECRET,
    AUTH_SECRET,
    BASE,
    _Cache,
    _Decisions,
    _hdr,
    _Incidents,
    _services,
    _Users,
)


def _now(minus: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(seconds=minus)).isoformat()


def _records() -> list[dict[str, Any]]:
    return [
        {"ts": _now(30), "alarm_id": "r1", "tier": "page", "signals": {"severity": 3},
         "db_id": "pg", "hostname": "loan-01", "fingerprint": "f1",
         "alarm_name": "AKIAIOSFODNN7EXAMPLE"},
        {"ts": _now(40), "alarm_id": "r2", "tier": "suppress", "signals": {"severity": 1},
         "db_id": "pg", "hostname": "loan-01", "fingerprint": "f2", "alarm_name": "억제된 알람"},
        {"ts": _now(50), "alarm_id": "r3", "tier": "ticket", "signals": {"severity": 2},
         "db_id": "pg", "hostname": "loan-02", "fingerprint": "f3", "alarm_name": "존B 알람"},
    ]


def _app(*, changes: bool = False) -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    app.include_router(biz_monitor.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=True, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
        security=SimpleNamespace(mask_pattern="***"),
        noise_gate=SimpleNamespace(
            biz_monitor_active=True, biz_monitor_metrics_active=False,
            biz_monitor_alarm_window_minutes=60, biz_monitor_changes_active=changes,
            biz_monitor_change_lookback_minutes=60, decision_store_path="",
            investigation_store_path="", investigation_store_max_lines=2000,
        ),
    )
    app.state.user_repo = _Users()
    cache = _Cache(_services(), FakeAsyncRedis())
    app.state.biz_service_cache = cache
    decisions = _Decisions(_records())
    app.state.biz_monitor_alarms = BizAlarmAggregator(
        cache=cache, incident_store=_Incidents(), decision_store=decisions, window_minutes=60,
        settings_loader=lambda: SimpleNamespace(config=SimpleNamespace(sms_thresholds={}),
                                                generic_terms=frozenset()),
    )
    app.state.test_decisions = decisions
    return TestClient(app), app


def _use_decisions(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """라우트가 만드는 결정 이력 저장소를 대역으로 바꾼다(경로 설정 없이)."""
    monkeypatch.setattr(biz_monitor, "_decision_store", lambda ng: app.state.test_decisions)


@pytest.fixture
def feed_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _fake(self: Any, client: Any, db_id: str, window_seconds: int, *,
                    reference_epoch: int, resource_ids: Any = None) -> list[Any]:
        calls.append(db_id)
        return []

    monkeypatch.setattr(ChangeFeed, "fetch_recent_changes", _fake)
    return calls


def test_unauthenticated_and_bad_params() -> None:
    client, _ = _app()
    assert client.get(f"{BASE}/1/events").status_code == 401
    assert client.get(f"{BASE}/1/events?window=2h", headers=_hdr("a")).status_code == 400
    assert client.get(f"{BASE}/1/events?sources=zzz", headers=_hdr("a")).status_code == 400
    assert client.get(f"{BASE}/2/events", headers=_hdr("u")).status_code == 404  # 권한 밖 서비스


def test_user_default_no_suppressed_zone_and_mask(
    monkeypatch: pytest.MonkeyPatch, feed_calls: list[str]
) -> None:
    client, app = _app()
    _use_decisions(app, monkeypatch)
    res = client.get(f"{BASE}/1/events?include_suppressed=true", headers=_hdr("u"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {
        "generated_at", "service_id", "window", "window_seconds", "sources_available", "rows",
        "total_rows", "truncated", "hidden_member_count", "hidden_row_count",
        "include_suppressed", "anomaly_start", "change_lookback_minutes", "pinned_note",
        "notices",
    }
    assert [r["alarm_id"] for r in body["rows"]] == ["r1"]  # 억제 없음 · 존B 행은 건수만
    assert body["include_suppressed"] is False
    assert body["hidden_row_count"] == 1 and body["hidden_member_count"] == 1
    assert body["rows"][0]["summary"] == "***"  # 사용자 경로 마스킹
    assert "AKIAIOSFODNN7EXAMPLE" not in res.text and "존B 알람" not in res.text
    assert body["sources_available"]["change"]["available"] is False
    assert feed_calls == []  # 변경 플래그 off → change_feed 호출 0


def test_admin_include_suppressed_and_unmasked(
    monkeypatch: pytest.MonkeyPatch, feed_calls: list[str]
) -> None:
    client, app = _app()
    _use_decisions(app, monkeypatch)
    plain = client.get(f"{BASE}/1/events", headers=_hdr("a")).json()
    assert sorted(r["alarm_id"] for r in plain["rows"]) == ["r1", "r3"]
    full = client.get(f"{BASE}/1/events?include_suppressed=true&window=6h",
                      headers=_hdr("a")).json()
    assert sorted(r["alarm_id"] for r in full["rows"]) == ["r1", "r2", "r3"]
    assert full["window"] == "6h" and full["include_suppressed"] is True
    assert "AKIAIOSFODNN7EXAMPLE" in {r["summary"] for r in full["rows"]}
    only = client.get(f"{BASE}/1/events?sources=state_change", headers=_hdr("a")).json()
    assert only["rows"] == []
    assert feed_calls == []


def test_changes_flag_on_injects_reader_and_db2_unsupported(
    monkeypatch: pytest.MonkeyPatch, feed_calls: list[str]
) -> None:
    from noise_gate.infrastructure import apm_host_map_sql_scanners as scanners

    async def _execute(db_id: str, sql: str) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(scanners, "registry_executor", lambda cfg: _execute)
    monkeypatch.setattr(scanners, "db_engine_of", lambda db: "db2" if db == "b0" else "postgresql")
    client, app = _app(changes=True)
    _use_decisions(app, monkeypatch)
    svc = app.state.biz_service_cache.services[1]
    svc["members"].append({"id": 13, "kind": "sms_server", "member_key": "b0|301",
                           "db_id": "b0", "resource_id": "301", "hostname": "loan-03",
                           "server_name": "loan-03", "zone": "gongjon"})
    body = client.get(f"{BASE}/1/events", headers=_hdr("a")).json()
    assert body["sources_available"]["change"]["available"] is True
    assert feed_calls == ["pg"]  # PG 존 1회 · DB2 존 0
    assert any(n.startswith("변경 이력 미지원 — b0") for n in body["notices"])
