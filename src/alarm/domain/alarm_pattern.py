"""알람 이력 통계 계산 (Plan 47 — 순수 함수).

폴스타 DB에서 조회한 알람 이력(`AlarmHistoryEntry` 리스트)을 `AlarmHistoryStats`로
변환한다. DB/Redis/LLM 의존이 없는 결정적(deterministic) 로직이므로 domain 계층에
배치한다 (Known Mistakes 2026-03-23: 데이터 모델 변환 함수는 모델이 있는 계층에 배치).

1차 분류 규칙 (위에서부터 먼저 매칭되는 항목 적용 — Plan 47 §5.2):
    첫 발생  — lookback 내 발생 이력 0건 (현재 이벤트 제외)
    급증     — count_24h ≥ burst_threshold_24h 이고 count_24h가 최근 30일 일평균의 3배 이상
    주기적   — 발생 ≥ 3건 이고 간격 변동계수(CV) < 0.5 (period_label 부여)
    산발적   — 그 외
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Any, Optional

from src.alarm.domain.alarm import AlarmEvent, AlarmHistoryEntry, AlarmHistoryStats

# 주기 라벨 산정 경계 (간격 중앙값 기준 — Plan 47 §5.2)
_DAILY_HOURS = (20.0, 28.0)        # 20~28시간 → 일 주기
_WEEKLY_DAYS = (5.5, 8.5)          # 5.5~8.5일 → 주 주기
_MONTHLY_DAYS = (26.0, 35.0)       # 26~35일 → 월 주기

# 주기적 판정: 간격 변동계수(CV) 상한
_PERIODIC_CV_THRESHOLD = 0.5
# 주기적 판정: 최소 발생 건수
_PERIODIC_MIN_COUNT = 3
# 급증 판정: 24h 발생이 최근 30일 일평균의 몇 배 이상이어야 하는지
_BURST_DAILY_AVG_MULTIPLIER = 3.0


def _period_label(median_interval_minutes: float) -> str:
    """간격 중앙값(분)으로 주기 라벨을 산정한다."""
    hours = median_interval_minutes / 60.0
    days = hours / 24.0
    if _DAILY_HOURS[0] <= hours <= _DAILY_HOURS[1]:
        return "일 주기"
    if _WEEKLY_DAYS[0] <= days <= _WEEKLY_DAYS[1]:
        return "주 주기"
    if _MONTHLY_DAYS[0] <= days <= _MONTHLY_DAYS[1]:
        return "월 주기"
    return "기타 주기"


def compute_history_stats(
    event: AlarmEvent,
    entries: list[AlarmHistoryEntry],
    burst_threshold_24h: int,
    lookback_days: int,
    *,
    truncated: bool = False,
    source: str = "polestar_db",
) -> AlarmHistoryStats:
    """발생(severity>=1) 이벤트 기준 통계 계산 + 1차 분류.

    - entries에서 event.alarm_id와 동일한 행은 제외한다 (현재 알람 자신 —
      폴스타가 TCP 발송 전에 DB에 먼저 기록하는 경우 조회 결과에 포함될 수 있음).
    - 모든 시간 윈도우(24h/7일/30일/lookback)는 **event.alarm_time을 기준 시각**으로
      계산한다 — 서버 처리 시각(now) 기준이 아님. 수신 지연·재처리로 알람 발생 시점과
      처리 시점이 벌어져도 발생 시점 기준의 패턴 판단을 유지하기 위함.
      (이력 조회 SQL의 lookback_start도 동일하게 alarm_time - lookback_days)
    - 시간대 히스토그램은 최근 30일 한정, 간격 분석은 전체 기간 대상 (§3.2 다중 윈도우 원칙).

    Args:
        event: 현재 알람 이벤트 (기준 시각 = event.alarm_time)
        entries: 폴스타 DB에서 조회된 이력 (해소 severity=0 행 포함 가능)
        burst_threshold_24h: 급증 판정 24h 최소 건수
        lookback_days: 조회 기간 (일) — 통계 메타 정보용
        truncated: max_rows 도달로 이력 일부만 조회된 경우 True
        source: 이력 출처 ("polestar_db" | "cache" | "simulated")

    Returns:
        AlarmHistoryStats (사전 분류 포함)
    """
    ref = event.alarm_time

    # 발생 이벤트만 (해소 severity=0 제외, 현재 알람 자신 제외, 기준 시각 이후 행 제외)
    occurrences = sorted(
        (
            e for e in entries
            if e.alarm_id != event.alarm_id and e.severity >= 1 and e.alarm_time <= ref
        ),
        key=lambda e: e.alarm_time,
    )

    total_count = len(occurrences)
    count_24h = sum(1 for e in occurrences if e.alarm_time > ref - timedelta(hours=24))
    count_7d = sum(1 for e in occurrences if e.alarm_time > ref - timedelta(days=7))
    count_30d = sum(1 for e in occurrences if e.alarm_time > ref - timedelta(days=30))
    same_resource_count = sum(
        1 for e in occurrences if e.resource_name == event.resource_name
    )

    first_seen: Optional[datetime] = occurrences[0].alarm_time if occurrences else None
    last_seen: Optional[datetime] = occurrences[-1].alarm_time if occurrences else None

    # 시간대 분포 — 최근 30일 한정 (오래된 시간대 패턴 희석 방지)
    hour_histogram: dict[int, int] = {}
    for e in occurrences:
        if e.alarm_time > ref - timedelta(days=30):
            hour = e.alarm_time.hour
            hour_histogram[hour] = hour_histogram.get(hour, 0) + 1

    # 발생 간격 — 전체 기간 (주·월 주기 감지용)
    median_interval_minutes: Optional[float] = None
    interval_cv: Optional[float] = None
    if total_count >= 2:
        intervals = [
            (occurrences[i + 1].alarm_time - occurrences[i].alarm_time).total_seconds() / 60.0
            for i in range(total_count - 1)
        ]
        median_interval_minutes = float(statistics.median(intervals))
        mean = statistics.fmean(intervals)
        if mean > 0:
            interval_cv = float(statistics.pstdev(intervals) / mean)

    # 1차 분류 (위에서부터 먼저 매칭되는 항목 적용)
    period_label = ""
    if total_count == 0:
        pre_classification = "첫 발생"
    elif (
        count_24h >= burst_threshold_24h
        and count_30d > 0
        and count_24h >= _BURST_DAILY_AVG_MULTIPLIER * (count_30d / 30.0)
    ):
        # 최근 30일 일평균 기준 — 최근에 시작된 반복 알람을 과거 무발생 기간이 희석하지 않도록
        pre_classification = "급증"
    elif (
        total_count >= _PERIODIC_MIN_COUNT
        and interval_cv is not None
        and interval_cv < _PERIODIC_CV_THRESHOLD
    ):
        pre_classification = "주기적"
        if median_interval_minutes is not None:
            period_label = _period_label(median_interval_minutes)
    else:
        pre_classification = "산발적"

    return AlarmHistoryStats(
        total_count=total_count,
        count_24h=count_24h,
        count_7d=count_7d,
        count_30d=count_30d,
        same_resource_count=same_resource_count,
        first_seen=first_seen,
        last_seen=last_seen,
        hour_histogram=hour_histogram,
        median_interval_minutes=median_interval_minutes,
        interval_cv=interval_cv,
        period_label=period_label,
        truncated=truncated,
        pre_classification=pre_classification,
        source=source,
    )


# ─── 캐시 직렬화 헬퍼 (조회 행 원본을 Redis에 JSON으로 저장 — Plan 47 §5.3) ───

_CACHE_TIME_FMT = "%Y-%m-%dT%H:%M:%S"


def history_entries_to_dicts(entries: list[AlarmHistoryEntry]) -> list[dict[str, Any]]:
    """이력 행 목록을 JSON 직렬화 가능한 dict 목록으로 변환한다."""
    return [
        {
            "alarm_id": e.alarm_id,
            "severity": e.severity,
            "alarm_status": e.alarm_status,
            "resource_name": e.resource_name,
            "alarm_time": e.alarm_time.strftime(_CACHE_TIME_FMT),
        }
        for e in entries
    ]


def history_entries_from_dicts(items: list[dict[str, Any]]) -> list[AlarmHistoryEntry]:
    """dict 목록(캐시 역직렬화)을 이력 행 목록으로 변환한다."""
    return [
        AlarmHistoryEntry(
            alarm_id=str(item["alarm_id"]),
            severity=int(item["severity"]),
            alarm_status=str(item.get("alarm_status", "")),
            resource_name=str(item.get("resource_name", "")),
            alarm_time=datetime.strptime(item["alarm_time"], _CACHE_TIME_FMT),
        )
        for item in items
    ]
