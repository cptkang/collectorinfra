"""plans/122 T-4~T-6 — 폴스타 시간 리터럴 경계(`src/db_adapters/polestar/time_period.py`).

해석 결과(`TimeResolution`) → 통계 테이블·`stat_date` 반개구간 · 알람 `ctime` TIMESTAMP 경계.
프롬프트 블록·검증기·결정적 조립이 같은 값을 쓰는 단일 출처다. LLM 호출 0.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.db_adapters.polestar.time_period import (
    alarm_ts_bounds,
    sql_applies_period,
    stat_bounds,
)
from src.domain.query_time import resolve_query_time
from src.domain.time_spec import KST

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)


@pytest.mark.parametrize("text, table, lo, hi, last", [
    ("지난달", "cmm_metric_stat_m", "202608", "202609", "202608"),
    ("지난 3개월", "cmm_metric_stat_m", "202606", "202609", "202608"),
    ("이번 달", "cmm_metric_stat_d", "20260901", "20260929", "20260928"),
    ("최근 30일", "cmm_metric_stat_d", "20260830", "20260929", "20260928"),
    ("어제", "cmm_metric_stat_d", "20260928", "20260929", "20260928"),
    ("지난주", "cmm_metric_stat_d", "20260921", "20260928", "20260927"),
    ("최근 3시간", "cmm_metric_stat_h", "2026092907", "2026092910", "2026092909"),
    ("오늘", "cmm_metric_stat_h", "2026092900", "2026092910", "2026092909"),
    ("시간 단위 최근 30일", "cmm_metric_stat_h", "2026083000", "2026092900", "2026092823"),
    ("CPU 사용률", "cmm_metric_stat_m", "202608", "202609", "202608"),  # 기간 없음 = 지난달
])
def test_stat_bounds(text: str, table: str, lo: str, hi: str, last: str) -> None:
    qt = resolve_query_time(text, NOW)
    assert qt.metric is not None
    sb = stat_bounds(qt.metric)
    assert sb is not None
    assert (sb.table, sb.lo, sb.hi, sb.last) == (table, lo, hi, last)
    assert sb.where() == f"s.stat_date >= '{lo}' AND s.stat_date < '{hi}'"


def test_unbounded_has_no_bounds() -> None:
    qt = resolve_query_time("CPU 90% 넘은 적이 있는 서버", NOW)
    assert qt.metric is not None and qt.metric.unbounded
    assert stat_bounds(qt.metric) is None and alarm_ts_bounds(qt.metric) is None


def test_empty_range_on_first_day() -> None:
    qt = resolve_query_time("이번 달", datetime(2026, 10, 1, 9, tzinfo=KST))
    assert qt.metric is not None
    sb = stat_bounds(qt.metric)
    assert sb is not None and sb.is_empty and sb.last is None


def test_until_is_left_open() -> None:
    qt = resolve_query_time("2026년 9월 1일까지", NOW)
    assert qt.metric is not None
    sb = stat_bounds(qt.metric)
    assert sb is not None and sb.lo is None
    assert sb.where() == "s.stat_date < '20260902'"


@pytest.mark.parametrize("text, bounds", [
    ("어제 알람", ("2026-09-28 00:00:00", "2026-09-29 00:00:00")),
    ("지난주 알람", ("2026-09-21 00:00:00", "2026-09-28 00:00:00")),
    ("이번 달 알람", ("2026-09-01 00:00:00", "2026-09-29 10:00:00")),  # 사건: 기준 시각까지
    ("최근 30일 알람", ("2026-08-30 00:00:00", "2026-09-29 00:00:00")),
])
def test_alarm_bounds_use_event_subject(text: str, bounds: tuple[str, str]) -> None:
    qt = resolve_query_time(text, NOW)
    assert qt.event is not None
    assert alarm_ts_bounds(qt.event) == bounds


def test_alarm_without_period_has_no_condition() -> None:
    qt = resolve_query_time("최근 발생 순 알람 100건", NOW)
    assert qt.event is not None and qt.event.unbounded
    assert alarm_ts_bounds(qt.event) is None


class TestSqlAppliesPeriod:
    def test_month_equality(self) -> None:
        qt = resolve_query_time("지난달", NOW)
        assert qt.metric is not None
        assert sql_applies_period("WHERE s.stat_date = '202608'", qt.metric)

    def test_day_between_inclusive(self) -> None:
        qt = resolve_query_time("최근 30일", NOW)
        assert qt.metric is not None
        sql = "WHERE s.stat_date BETWEEN '20260830' AND '20260928'"
        assert sql_applies_period(sql, qt.metric)

    def test_function_form_is_not_applied(self) -> None:
        qt = resolve_query_time("최근 30일", NOW)
        assert qt.metric is not None
        sql = "WHERE s.stat_date >= TO_CHAR(CURRENT_DATE - INTERVAL '30 day', 'YYYYMMDD')"
        assert not sql_applies_period(sql, qt.metric)

    def test_alarm_ts(self) -> None:
        qt = resolve_query_time("어제 알람", NOW)
        assert qt.event is not None
        sql = ("WHERE a.ctime >= TIMESTAMP '2026-09-28 00:00:00' "
               "AND a.ctime < TIMESTAMP '2026-09-29 00:00:00'")
        assert sql_applies_period(sql, qt.event)

    def test_no_time_condition(self) -> None:
        qt = resolve_query_time("지난달", NOW)
        assert qt.metric is not None
        assert not sql_applies_period("SELECT name FROM cmm_resource", qt.metric)
        assert not sql_applies_period("", qt.metric)
