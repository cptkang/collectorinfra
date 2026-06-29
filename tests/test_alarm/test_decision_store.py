"""DecisionStore JSONL 감사 적재·집계 단위 테스트 (Plan 52 §8.3).

JSONL append, DASHBOARD/SUPPRESS 기록(억제≠삭제), aggregate 억제율,
enabled=False no-op, 기록 실패 graceful degradation을 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.alarm.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
)
from src.alarm.infrastructure.decision_store import DecisionStore


def make_decision(tier: str = TIER_PAGE, **kwargs) -> NotificationDecision:
    """테스트용 NotificationDecision 생성."""
    base = dict(
        tier=tier,
        reason="테스트 결정",
        priority=300,
        signals={"severity": 2, "self_heal": False},
        fingerprint="fp-abc",
    )
    base.update(kwargs)
    return NotificationDecision(**base)


def _read_lines(path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestRecord:
    def test_append_jsonl(self, tmp_path):
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        store.record(make_decision(), alarm_id="A-1")
        store.record(make_decision(tier=TIER_DASHBOARD), alarm_id="A-2")

        rows = _read_lines(store_path)
        assert len(rows) == 2
        first = rows[0]
        assert first["alarm_id"] == "A-1"
        assert first["tier"] == TIER_PAGE
        assert first["reason"] == "테스트 결정"
        assert first["priority"] == 300
        assert first["fingerprint"] == "fp-abc"
        assert first["signals"]["severity"] == 2
        # ts는 ISO8601 파싱 가능해야 함
        assert datetime.fromisoformat(first["ts"])

    def test_records_dashboard_and_suppress(self, tmp_path):
        # 억제 ≠ 삭제: DASHBOARD/SUPPRESS도 반드시 기록
        store_path = tmp_path / "audit" / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        store.record(make_decision(tier=TIER_DASHBOARD))
        store.record(make_decision(tier=TIER_SUPPRESS))

        rows = _read_lines(store_path)
        tiers = [r["tier"] for r in rows]
        assert TIER_DASHBOARD in tiers
        assert TIER_SUPPRESS in tiers

    def test_creates_parent_directories(self, tmp_path):
        store_path = tmp_path / "deep" / "nested" / "dir" / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        store.record(make_decision())
        assert store_path.exists()

    def test_custom_ts_recorded(self, tmp_path):
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        ts = datetime(2026, 6, 29, 10, 0, 0, tzinfo=timezone.utc)
        store.record(make_decision(), ts=ts)
        rows = _read_lines(store_path)
        assert rows[0]["ts"] == ts.isoformat()


class TestEnabledFlag:
    def test_disabled_is_noop(self, tmp_path):
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path), enabled=False)
        store.record(make_decision())
        assert not store_path.exists()
        assert store.aggregate() == {"total": 0, "by_tier": {}, "suppress_ratio": 0.0}


class TestGracefulFailure:
    def test_record_failure_does_not_raise(self, tmp_path):
        # 경로가 디렉토리면 파일 open 실패 → 예외 전파 없이 graceful
        bad_path = tmp_path / "imadir"
        bad_path.mkdir()
        store = DecisionStore(str(bad_path))
        # 예외가 나지 않아야 한다(발송 차단 금지)
        store.record(make_decision())


class TestAggregate:
    def test_empty_when_no_file(self, tmp_path):
        store = DecisionStore(str(tmp_path / "missing.jsonl"))
        agg = store.aggregate()
        assert agg == {"total": 0, "by_tier": {}, "suppress_ratio": 0.0}

    def test_suppress_ratio(self, tmp_path):
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        store.record(make_decision(tier=TIER_PAGE))
        store.record(make_decision(tier=TIER_SUPPRESS))
        store.record(make_decision(tier=TIER_SUPPRESS))
        store.record(make_decision(tier=TIER_DASHBOARD))

        agg = store.aggregate()
        assert agg["total"] == 4
        assert agg["by_tier"][TIER_SUPPRESS] == 2
        assert agg["by_tier"][TIER_PAGE] == 1
        assert agg["by_tier"][TIER_DASHBOARD] == 1
        assert agg["suppress_ratio"] == 0.5

    def test_window_filters_old_records(self, tmp_path):
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        now = datetime.now(timezone.utc)
        store.record(make_decision(tier=TIER_SUPPRESS), ts=now - timedelta(hours=2))
        store.record(make_decision(tier=TIER_PAGE), ts=now)

        # 최근 1시간 창 → 오래된 SUPPRESS 제외, PAGE 1건만
        agg = store.aggregate(window_seconds=3600)
        assert agg["total"] == 1
        assert agg["by_tier"].get(TIER_PAGE) == 1
        assert agg["suppress_ratio"] == 0.0

    def test_corrupted_line_skipped(self, tmp_path):
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        store.record(make_decision(tier=TIER_PAGE))
        # 손상된 줄 추가
        with store_path.open("a", encoding="utf-8") as fh:
            fh.write("not-json\n")
        store.record(make_decision(tier=TIER_SUPPRESS))

        agg = store.aggregate()
        assert agg["total"] == 2
        assert agg["suppress_ratio"] == 0.5
