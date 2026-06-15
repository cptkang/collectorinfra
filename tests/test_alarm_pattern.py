"""알람 이력 통계 계산(Plan 47) 단위 테스트.

compute_history_stats의 1차 분류 규칙 4종(첫 발생/주기적/급증/산발적),
주기 라벨 3종(일/주/월), truncated 플래그, 현재 이벤트 제외,
event.alarm_time 기준 시간 윈도우를 검증한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.alarm.domain.alarm import AlarmEvent, AlarmHistoryEntry
from src.alarm.domain.alarm_pattern import (
    compute_history_stats,
    history_entries_from_dicts,
    history_entries_to_dicts,
)

REF = datetime(2026, 6, 11, 14, 35, 0)


def make_event(**kwargs) -> AlarmEvent:
    """테스트용 AlarmEvent를 생성한다."""
    defaults = dict(
        db_id="polestar_cm_gp",
        server_name="cop0-aisapd02",
        hostname="saisvd01",
        ip_address="10.1.2.3",
        resource_ancestry="/Servers/svr/Cpus",
        alarm_id="CUR-001",
        severity=2,
        alarm_status="NOT_ACK",
        resource_type="server.Cpus",
        resource_name="svr-001-CPU",
        alarm_name="CPU 사용률 임계 초과",
        alarm_time=REF,
        conditions="",
        condition_log="",
        is_clear=False,
    )
    defaults.update(kwargs)
    return AlarmEvent(**defaults)


def make_entry(
    alarm_time: datetime,
    alarm_id: str = "",
    severity: int = 2,
    resource_name: str = "svr-001-CPU",
) -> AlarmHistoryEntry:
    """테스트용 이력 행을 생성한다."""
    return AlarmHistoryEntry(
        alarm_id=alarm_id or f"H-{alarm_time:%Y%m%d%H%M%S}",
        severity=severity,
        alarm_status="NOT_ACK",
        resource_name=resource_name,
        alarm_time=alarm_time,
    )


class TestPreClassification:
    """1차 분류 규칙 4종."""

    def test_first_occurrence_when_no_history(self):
        stats = compute_history_stats(make_event(), [], 5, 90)
        assert stats.pre_classification == "첫 발생"
        assert stats.total_count == 0
        assert stats.first_seen is None
        assert stats.last_seen is None

    def test_current_event_excluded_from_history(self):
        # 폴스타가 TCP 발송 전 DB에 기록한 현재 알람 자신만 조회된 경우 → 첫 발생
        entries = [make_entry(REF - timedelta(minutes=1), alarm_id="CUR-001")]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.total_count == 0
        assert stats.pre_classification == "첫 발생"

    def test_clear_entries_excluded(self):
        # severity=0(해소) 행은 발생 통계에서 제외
        entries = [make_entry(REF - timedelta(days=d), severity=0) for d in range(1, 5)]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.total_count == 0
        assert stats.pre_classification == "첫 발생"

    def test_periodic_daily(self):
        entries = [make_entry(REF - timedelta(hours=24 * i)) for i in range(1, 11)]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.pre_classification == "주기적"
        assert stats.period_label == "일 주기"
        assert stats.interval_cv is not None
        assert stats.interval_cv < 0.5
        assert stats.median_interval_minutes == 24 * 60

    def test_burst(self):
        # 24시간 내 6건 (임계 5건 이상) + 30일 일평균(0.2)의 3배 이상
        entries = [make_entry(REF - timedelta(hours=i)) for i in range(1, 7)]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.count_24h == 6
        assert stats.pre_classification == "급증"

    def test_burst_takes_precedence_over_periodic(self):
        # 간격이 일정해도 급증 조건이 먼저 매칭되어야 함
        entries = [make_entry(REF - timedelta(hours=2 * i)) for i in range(1, 8)]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.pre_classification == "급증"

    def test_daily_periodic_not_burst(self):
        # 일 주기 알람은 count_24h가 적어 급증으로 오판되지 않아야 함
        entries = [make_entry(REF - timedelta(hours=24 * i)) for i in range(1, 31)]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.count_24h == 0
        assert stats.pre_classification == "주기적"

    def test_sporadic(self):
        entries = [
            make_entry(REF - timedelta(hours=1)),
            make_entry(REF - timedelta(days=2)),
            make_entry(REF - timedelta(days=20)),
        ]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.pre_classification == "산발적"
        assert stats.period_label == ""


class TestPeriodLabel:
    """주기 라벨 산정 (일/주/월/기타)."""

    def test_weekly_label(self):
        entries = [make_entry(REF - timedelta(days=7 * i)) for i in range(1, 13)]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.pre_classification == "주기적"
        assert stats.period_label == "주 주기"

    def test_monthly_label(self):
        entries = [make_entry(REF - timedelta(days=30 * i)) for i in range(1, 4)]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.pre_classification == "주기적"
        assert stats.period_label == "월 주기"

    def test_other_period_label(self):
        # 12시간 간격 — 일/주/월 어느 경계에도 해당하지 않음
        entries = [make_entry(REF - timedelta(hours=12 * i)) for i in range(1, 7)]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.pre_classification == "주기적"
        assert stats.period_label == "기타 주기"


class TestWindows:
    """다중 윈도우 — event.alarm_time 기준 (처리 시점 now 기준 아님)."""

    def test_windows_relative_to_alarm_time(self):
        event = make_event(alarm_time=datetime(2026, 1, 10, 0, 0, 0))
        ref = event.alarm_time
        entries = [
            make_entry(ref - timedelta(hours=12)),   # 24h 내
            make_entry(ref - timedelta(days=3)),     # 7일 내
            make_entry(ref - timedelta(days=15)),    # 30일 내
            make_entry(ref - timedelta(days=60)),    # 전체 기간
        ]
        stats = compute_history_stats(event, entries, 5, 90)
        assert stats.count_24h == 1
        assert stats.count_7d == 2
        assert stats.count_30d == 3
        assert stats.total_count == 4

    def test_entries_after_alarm_time_excluded(self):
        # 수신 지연·재처리로 기준 시각 이후 행이 조회되어도 통계에서 제외
        entries = [
            make_entry(REF + timedelta(hours=1)),
            make_entry(REF - timedelta(hours=1)),
        ]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.total_count == 1

    def test_hour_histogram_limited_to_30d(self):
        entries = [
            make_entry((REF - timedelta(days=1)).replace(hour=2)),
            make_entry((REF - timedelta(days=40)).replace(hour=5)),
        ]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert 2 in stats.hour_histogram
        assert 5 not in stats.hour_histogram

    def test_same_resource_count(self):
        entries = [
            make_entry(REF - timedelta(days=1), resource_name="svr-001-CPU"),
            make_entry(REF - timedelta(days=2), resource_name="svr-002-CPU"),
        ]
        stats = compute_history_stats(make_event(), entries, 5, 90)
        assert stats.same_resource_count == 1
        assert stats.total_count == 2


class TestFlags:
    """truncated 플래그·source 전달."""

    def test_truncated_flag_passthrough(self):
        entries = [make_entry(REF - timedelta(hours=24 * i)) for i in range(1, 4)]
        stats = compute_history_stats(make_event(), entries, 5, 90, truncated=True)
        assert stats.truncated is True

    def test_source_passthrough(self):
        stats = compute_history_stats(make_event(), [], 5, 90, source="cache")
        assert stats.source == "cache"

    def test_default_source(self):
        stats = compute_history_stats(make_event(), [], 5, 90)
        assert stats.source == "polestar_db"


class TestSerialization:
    """캐시 직렬화 헬퍼 왕복 변환."""

    def test_round_trip(self):
        entries = [
            make_entry(REF - timedelta(hours=3), alarm_id="H-1", severity=3),
            make_entry(REF - timedelta(days=2), alarm_id="H-2", severity=0),
        ]
        restored = history_entries_from_dicts(history_entries_to_dicts(entries))
        assert restored == entries
