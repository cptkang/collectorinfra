"""억제기 메타모니터링 + aggregate 운영지표 확장 단위 테스트 (Plan 52 §9 · Phase E3).

- 억제율 > threshold → high_suppress_ratio
- 창 내 total < min_events → no_events
- 정상 → 빈 리스트
- aggregate 신규 키(page_count/ticket_count/.../actionable_ratio/last_event_*) 정확성
- 기존 키(total/by_tier/suppress_ratio) 하위호환 유지
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.alarm.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)
from src.alarm.infrastructure.decision_store import DecisionStore


def make_decision(tier: str) -> NotificationDecision:
    return NotificationDecision(
        tier=tier, reason="meta 테스트", priority=300,
        signals={"severity": 2}, fingerprint="fp",
    )


def _store_with(tmp_path, tiers: list[str]) -> DecisionStore:
    store = DecisionStore(str(tmp_path / "decisions.jsonl"))
    for t in tiers:
        store.record(make_decision(t))
    return store


class TestAggregateExtendedKeys:
    def test_counts_and_ratios(self, tmp_path):
        store = _store_with(
            tmp_path,
            [TIER_PAGE, TIER_TICKET, TIER_DASHBOARD, TIER_SUPPRESS, TIER_SUPPRESS],
        )
        agg = store.aggregate()
        assert agg["total"] == 5
        assert agg["page_count"] == 1
        assert agg["ticket_count"] == 1
        assert agg["dashboard_count"] == 1
        assert agg["suppress_count"] == 2
        # actionable = page + ticket = 2
        assert agg["actionable"] == 2
        assert agg["actionable_ratio"] == 2 / 5
        assert agg["suppress_ratio"] == 2 / 5

    def test_backward_compatible_keys_present(self, tmp_path):
        # 기존 키(total/by_tier/suppress_ratio)는 동일 의미로 유지된다
        store = _store_with(tmp_path, [TIER_PAGE, TIER_SUPPRESS])
        agg = store.aggregate()
        assert agg["total"] == 2
        assert agg["by_tier"][TIER_PAGE] == 1
        assert agg["by_tier"][TIER_SUPPRESS] == 1
        assert agg["suppress_ratio"] == 0.5

    def test_last_event_age_recent(self, tmp_path):
        store = DecisionStore(str(tmp_path / "decisions.jsonl"))
        now = datetime.now(timezone.utc)
        store.record(make_decision(TIER_PAGE), ts=now - timedelta(seconds=10))
        store.record(make_decision(TIER_PAGE), ts=now)
        agg = store.aggregate()
        assert agg["last_event_ts"] == now.isoformat()
        # 가장 최근(=now) 기준 경과 — 작은 양수
        assert 0.0 <= agg["last_event_age_seconds"] < 60.0

    def test_empty_has_all_keys(self, tmp_path):
        store = DecisionStore(str(tmp_path / "missing.jsonl"))
        agg = store.aggregate()
        for key in (
            "page_count", "ticket_count", "dashboard_count", "suppress_count",
            "actionable", "actionable_ratio", "last_event_ts", "last_event_age_seconds",
        ):
            assert key in agg
        assert agg["last_event_ts"] is None
        assert agg["last_event_age_seconds"] is None


class TestMetaAlerts:
    def test_high_suppress_ratio_triggers(self, tmp_path):
        # 3/4 = 0.75 억제율 > threshold 0.5 → high_suppress_ratio
        store = _store_with(
            tmp_path, [TIER_SUPPRESS, TIER_SUPPRESS, TIER_SUPPRESS, TIER_PAGE]
        )
        alerts = store.meta_alerts(
            window_seconds=3600, suppress_ratio_threshold=0.5, min_events=1
        )
        types = [a["type"] for a in alerts]
        assert "high_suppress_ratio" in types
        high = next(a for a in alerts if a["type"] == "high_suppress_ratio")
        assert high["value"] == 0.75
        assert high["threshold"] == 0.5
        assert high["window_seconds"] == 3600

    def test_no_events_triggers(self, tmp_path):
        # 빈 저장소 + min_events=1 → no_events
        store = DecisionStore(str(tmp_path / "decisions.jsonl"))
        alerts = store.meta_alerts(
            window_seconds=3600, suppress_ratio_threshold=0.9, min_events=1
        )
        types = [a["type"] for a in alerts]
        assert types == ["no_events"]
        assert alerts[0]["total"] == 0
        assert alerts[0]["min_events"] == 1

    def test_no_events_when_below_min(self, tmp_path):
        # total 2 < min_events 5 → no_events (억제율은 정상이어도 무수신 경보)
        store = _store_with(tmp_path, [TIER_PAGE, TIER_PAGE])
        alerts = store.meta_alerts(
            window_seconds=3600, suppress_ratio_threshold=0.9, min_events=5
        )
        assert any(a["type"] == "no_events" for a in alerts)

    def test_normal_returns_empty(self, tmp_path):
        # 억제율 0.25 < 0.9, total 4 >= min 1 → 정상(빈 리스트)
        store = _store_with(
            tmp_path, [TIER_PAGE, TIER_TICKET, TIER_DASHBOARD, TIER_SUPPRESS]
        )
        alerts = store.meta_alerts(
            window_seconds=3600, suppress_ratio_threshold=0.9, min_events=1
        )
        assert alerts == []

    def test_high_suppress_not_fired_on_empty(self, tmp_path):
        # 이벤트 없으면 high_suppress_ratio는 트리거되지 않는다(no_events만)
        store = DecisionStore(str(tmp_path / "decisions.jsonl"))
        alerts = store.meta_alerts(
            window_seconds=3600, suppress_ratio_threshold=0.0, min_events=1
        )
        assert all(a["type"] != "high_suppress_ratio" for a in alerts)
