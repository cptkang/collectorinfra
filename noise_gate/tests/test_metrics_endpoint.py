"""GET /alarm/metrics 운영지표 엔드포인트 테스트 (Plan 52 §9 · Phase E3).

(1) aggregate/meta_alerts 함수 단위(인증 무관) +
(2) FastAPI TestClient로 /api/v1/alarm/metrics 호출 — 최소 앱에 alarm 라우터만 마운트하고
    auth.enabled=False(기존 require_user 패턴)로 인증을 우회한다.
MTTA/MTTR/사건전환율 null + unavailable_metrics 노출을 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.domain.notification_policy import (
    NotificationDecision,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)
from noise_gate.infrastructure.decision_store import DecisionStore
from src.api.routes import alarm as alarm_routes


def make_decision(tier: str) -> NotificationDecision:
    return NotificationDecision(
        tier=tier, reason="metrics 테스트", priority=300,
        signals={"severity": 2}, fingerprint="fp",
    )


def _seed_store(path, tiers: list[str]) -> None:
    store = DecisionStore(str(path))
    for t in tiers:
        store.record(make_decision(t))


def _make_config(decisions_path) -> SimpleNamespace:
    """metrics 엔드포인트가 읽는 최소 config (auth off + noise_gate E3 필드)."""
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=False),
        noise_gate=SimpleNamespace(
            decision_store_path=str(decisions_path),
            meta_alert_window_seconds=3600,
            meta_alert_suppress_ratio=0.9,
            meta_alert_min_events=1,
        ),
    )


def _make_client(config) -> TestClient:
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    return TestClient(app)


class TestMetricsEndpoint:
    def test_returns_tier_counts_and_ratios(self, tmp_path):
        decisions = tmp_path / "decisions.jsonl"
        _seed_store(decisions, [TIER_PAGE, TIER_TICKET, TIER_SUPPRESS, TIER_SUPPRESS])
        client = _make_client(_make_config(decisions))

        resp = client.get("/api/v1/alarm/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["window_seconds"] == 3600
        assert data["total"] == 4
        assert data["page_count"] == 1
        assert data["ticket_count"] == 1
        assert data["suppress_count"] == 2
        assert data["actionable_ratio"] == 2 / 4
        assert data["suppress_ratio"] == 2 / 4

    def test_mtta_mttr_null_with_unavailable_reason(self, tmp_path):
        decisions = tmp_path / "decisions.jsonl"
        _seed_store(decisions, [TIER_PAGE])
        client = _make_client(_make_config(decisions))

        data = client.get("/api/v1/alarm/metrics").json()
        # 환각 금지: 계측 불가 지표는 null + 사유 노출
        assert data["mtta_seconds"] is None
        assert data["mttr_seconds"] is None
        assert data["incident_conversion_rate"] is None
        assert "reason" in data["unavailable_metrics"]

    def test_high_suppress_ratio_meta_alert_exposed(self, tmp_path):
        decisions = tmp_path / "decisions.jsonl"
        # 3/3 = 1.0 > 0.9 → high_suppress_ratio
        _seed_store(decisions, [TIER_SUPPRESS, TIER_SUPPRESS, TIER_SUPPRESS])
        client = _make_client(_make_config(decisions))

        data = client.get("/api/v1/alarm/metrics").json()
        types = [a["type"] for a in data["meta_alerts"]]
        assert "high_suppress_ratio" in types

    def test_no_events_meta_alert_when_empty(self, tmp_path):
        decisions = tmp_path / "missing.jsonl"  # 파일 없음 → total 0
        client = _make_client(_make_config(decisions))

        data = client.get("/api/v1/alarm/metrics").json()
        assert data["total"] == 0
        assert data["last_event_age_seconds"] is None
        types = [a["type"] for a in data["meta_alerts"]]
        assert "no_events" in types


class TestUnitFallback:
    """인증 장벽이 있는 환경을 대비한 함수 단위 검증(aggregate/meta_alerts 직접 호출)."""

    def test_aggregate_and_meta_alerts(self, tmp_path):
        decisions = tmp_path / "decisions.jsonl"
        _seed_store(decisions, [TIER_PAGE, TIER_TICKET])
        store = DecisionStore(str(decisions))

        agg = store.aggregate(window_seconds=3600)
        assert agg["actionable"] == 2
        assert agg["actionable_ratio"] == 1.0

        # total 2 >= min 1, 억제율 0 < 0.9 → 정상(빈 리스트)
        assert store.meta_alerts(
            window_seconds=3600, suppress_ratio_threshold=0.9, min_events=1
        ) == []
