"""plans/156 W2 독립 검증 — 시각 시작점·범위(G-1·G-2 (a)) + 사용자 확정 (c) 통계 직전 정시 한 칸.

기준 시각 2026-10-13(화) 10:37 KST(§5 사례 표). 통과 테스트는 확정 동작을 고정한다.
검증에서 찾은 결함 V-1~V-4는 W2 교정 라운드에서 고쳐 xfail 표지를 지웠다.
2026-10-14 사용자 확정 — 표지 없는 1~12시는 오전·오후를 되묻는다(사례에 표지를 붙였다) · 끝이
명시된 닫힌 범위의 통계가 비면 되묻는다((c) 직전 정시 대체는 「…부터」·시점 단독만).
실 LLM·DB·네트워크 0.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from src.db_adapters.polestar.time_period import alarm_ts_bounds, stat_bounds
from src.db_adapters.polestar.validators import check_time_conditions
from src.domain.query_time import QueryTime, resolve_query_time
from src.domain.time_expr import slot_to_spec
from src.domain.time_spec import (
    KST,
    NOTE_CLOCK_PREVIOUS_HOUR,
    NOTE_MINUTE_WIDENED,
    NOTE_YEAR_INFERRED,
)
from src.orchestration import apm_query as aq

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)


def _at(h: int, m: int = 0, *, day: int = 13, month: int = 10, year: int = 2026) -> datetime:
    return datetime(year, month, day, h, m, tzinfo=KST)


def _qt(text: str, now: datetime = NOW, **kw: object) -> QueryTime:
    return resolve_query_time(text, now, **kw)  # type: ignore[arg-type]


def _pair(qt: QueryTime) -> tuple[tuple, tuple]:
    assert qt.clarify is None, qt.clarify
    assert qt.metric is not None and qt.event is not None
    return (qt.metric.start, qt.metric.end), (qt.event.start, qt.event.end)


# ── 1. 비기간 시각 언급 — 시각 기간을 새로 만들지 않는다 ─────────────────────────────

@pytest.mark.parametrize("text", [
    "3시 방향 서버", "10시 방향 서버", "A동 3시 방향 랙에 있는 서버", "1시간 뒤", "24시간",
    "12시간제", "Windows 10 시스템 알람", "서버 10시스템", "192.168.1.10 시 서버",
    "10:30부터 알람", "1시간 이상 지속된 알람",
])
def test_non_clock_mentions_do_not_become_clock_periods(text: str) -> None:
    qt = _qt(text)
    if qt.clarify is None:
        assert qt.metric is not None and qt.metric.source == "default", qt.metric


# ── 2. 오전·오후·새벽·밤 환산 · 0시·24시 · 분 범위 ──────────────────────────────────

@pytest.mark.parametrize(("text", "hour"), [
    ("오전 12시 알람", 0), ("새벽 1시 알람", 1), ("아침 7시 알람", 7), ("0시 알람", 0),
    ("어제 밤 10시 알람", 22), ("어제 오후 12시 알람", 12), ("엊그제 오후 3시 알람", 15),
    ("새벽 12시 30분 알람", 0),
])
def test_meridiem_conversion(text: str, hour: int) -> None:
    qt = _qt(text)
    assert qt.clarify is None and qt.event is not None
    assert qt.event.start is not None and qt.event.start.hour == hour


@pytest.mark.parametrize(("text", "code"), [
    ("밤 12시 알람", "unresolved"), ("어제 밤 12시 알람", "unresolved"),
    ("오후 14시 알람", "unresolved"), ("오전 13시 알람", "unresolved"),
    ("오전 0시 알람", "unresolved"), ("저녁 5시 알람", "unresolved"),
    ("오후 12시 알람", "future_explicit"), ("저녁 7시 알람", "future_explicit"),
    # 교정 — 없는 시각은 「달력에 없는 날짜」가 아니라 시각 문구(`invalid_clock`)로 되묻는다
    ("24시 알람", "invalid_clock"), ("오늘 24시 알람", "invalid_clock"),
    ("10시 60분 알람", "invalid_clock"), ("10시 75분부터", "invalid_clock"),
    ("2시 알람", "clock_meridiem"), ("12시 알람", "clock_meridiem"),  # 2026-10-14 확정
])
def test_meridiem_and_range_rejections(text: str, code: str) -> None:
    assert _qt(text).clarify == code


# ── 3. 반열림 · 롤오버 · 역전 · 연도 추정 · 시점 단독 ─────────────────────────────────

def test_midnight_crossing_with_both_dates_is_half_open() -> None:
    m, e = _pair(_qt("어제 22시부터 오늘 새벽 2시까지 알람"))
    assert m == e == (_at(22, day=12), _at(2))


@pytest.mark.parametrize("text", [
    "어제 22시부터 새벽 2시까지",        # 날짜 하나 · 자정 걸침 → 되묻기(G-2 (a))
    "오늘 14시부터 오전 9시까지",        # 역전
    "어제 오전 9시부터 3시까지",         # 끝은 오전을 잇는다 → 역전
    "어제 오전 10시 30분부터 10시 30분까지 알람",  # 빈 범위(시작 = 끝)
    "어제 오후 2시부터 오전 4시까지",
])
def test_reversed_or_single_date_crossing_is_asked(text: str) -> None:
    assert _qt(text).clarify == "invalid_spec"


def test_year_rollover_range_infers_each_side() -> None:
    qt = _qt("12월 31일 23시부터 1월 1일 새벽 1시까지")
    m, _ = _pair(qt)
    assert m == (_at(23, month=12, day=31, year=2025), _at(1, month=1, day=1))
    assert qt.metric is not None and NOTE_YEAR_INFERRED in qt.metric.notes


def test_year_inference_compares_dates_only() -> None:
    """오늘 날짜 + 미래 시각은 작년이 아니라 미래 되묻기 · 지난 날짜는 올해."""
    assert _qt("10월 13일 14시 알람").clarify == "future_explicit"
    m, _ = _pair(_qt("9월 15일 14시 알람"))
    assert m == (_at(14, month=9, day=15), _at(15, month=9, day=15))
    m, _ = _pair(_qt("2월 29일 오전 10시 알람"))
    assert m[0] == _at(10, month=2, day=29, year=2024)


def test_point_clock_is_that_hour_for_both_subjects() -> None:
    m, e = _pair(_qt("어제 오전 10시 알람"))
    assert m == e == (_at(10, day=12), _at(11, day=12))
    m, e = _pair(_qt("어제 14시 30분 장애 원인 분석해줘"))
    assert m == e == (_at(14, day=12), _at(15, day=12))


def test_until_clock_end_is_exclusive() -> None:
    m, e = _pair(_qt("어제 15시까지 알람"))
    assert m == e == (None, _at(15, day=12))
    m, e = _pair(_qt("오늘 오전 9시 30분까지 알람"))
    assert e == (None, _at(9, 30)) and m == (None, _at(10))


# ── 4. (c) 직전 정시 한 칸 — 주체별 일관성 · 계약 · 소비처 ──────────────────────────

@pytest.mark.parametrize(("text", "event"), [
    ("오전 10시 30분부터 알람", (_at(10, 30), _at(10, 37))),
    ("오전 10시 알람", (_at(10), _at(10, 37))),
])
def test_previous_hour_metric_and_minute_event(text: str, event: tuple) -> None:
    qt = _qt(text)
    m, e = _pair(qt)
    assert m == (_at(9), _at(10))
    assert e == event
    assert qt.metric is not None and qt.event is not None
    assert NOTE_CLOCK_PREVIOUS_HOUR in qt.metric.notes
    assert NOTE_CLOCK_PREVIOUS_HOUR not in qt.event.notes
    assert NOTE_MINUTE_WIDENED not in qt.metric.notes  # 대체 고지와 확장 고지는 배타


# 2026-10-14 사용자 확정 — 끝이 명시된 닫힌 범위는 (c) 대체 없이 되묻는다
@pytest.mark.parametrize("text", [
    "오늘 오전 10시부터 10시 30분까지 알람", "오늘 오전 10시 30분부터 10시 40분까지 알람",
])
def test_closed_range_with_empty_metric_is_asked(text: str) -> None:
    assert _qt(text).clarify == "clock_range_in_progress"


def test_previous_hour_state_roundtrip_keeps_contract() -> None:
    qt = _qt("오전 10시 30분부터 알람")
    back = QueryTime.from_state(qt.to_state())
    assert back is not None and back == qt
    with pytest.raises(ValueError):
        QueryTime(NOW, qt.metric, qt.event, clarify="unresolved")  # clarify_with_resolution


def test_previous_hour_consumers_get_minute_and_hour_literals() -> None:
    qt = _qt("오전 10시 30분부터 알람")
    assert qt.event is not None and qt.metric is not None
    assert alarm_ts_bounds(qt.event) == ("2026-10-13 10:30:00", "2026-10-13 10:37:00")
    sb = stat_bounds(qt.metric)
    assert sb is not None and (sb.table, sb.lo, sb.hi) == ("cmm_metric_stat_h", "2026101309",
                                                          "2026101310")
    sql = ("SELECT a.id FROM polestar.cmm_alarm a WHERE a.ctime >= TIMESTAMP '2026-10-13 10:30:00' "
           "AND a.ctime < TIMESTAMP '2026-10-13 10:37:00' LIMIT 100")
    assert check_time_conditions(sql, qt) == []
    bad = sql.replace("10:30:00", "10:00:00")
    assert check_time_conditions(bad, qt)


def test_apm_window_takes_minute_start() -> None:
    views = {v.id: v for v in aq.apm_views()}
    plan = aq.plan_window(views["apm.app_health"], None, NOW,
                          query_time=_qt("오전 10시 30분부터 web01 응답시간"))
    assert plan.mode == "window"
    assert plan.args == {"reference_time": None, "lookback_minutes": 7}
    plan = aq.plan_window(views["apm.app_health"], None, NOW,
                          query_time=_qt("어제 오전 10시 30분부터 10시 45분까지 web01 응답시간"))
    assert plan.args == {"reference_time": "2026-10-12T10:45:00+09:00", "lookback_minutes": 15}


# ── 5. 슬롯 minute 검증 · 규칙 되묻기를 슬롯이 덮지 않음 ──────────────────────────────

@pytest.mark.parametrize(("slot", "code"), [
    ({"relation": "absolute", "start": {"month": 10, "day": 12, "minute": 30},
      "span": "10월 12일 9시 전후"}, "partial_date_gap"),
    ({"relation": "absolute", "start": {"month": 10, "day": 12, "hour": 9, "minute": 60},
      "span": "10월 12일 9시 전후"}, "minute_out_of_range"),
    ({"relation": "absolute", "start": {"month": 10, "day": 12, "hour": 24},
      "span": "10월 12일 밤 12시"}, "hour_out_of_range"),
])
def test_slot_minute_validation(slot: dict, code: str) -> None:
    assert slot_to_spec(slot, "10월 12일 9시 전후 알람 10월 12일 밤 12시")[1] == code


@pytest.mark.parametrize("text", [
    "오늘 14시부터 오전 9시까지 알람", "어제 22시부터 새벽 2시까지 알람",
])
def test_rule_invalid_spec_not_overridden_by_slot(text: str) -> None:
    slot = {"relation": "last", "n": 1, "unit": "day", "span": "어제"}
    qt = _qt(text, slot=slot)
    assert qt.clarify == "invalid_spec" and qt.slot_status == "unused"


# ── 6. 성능 — API 상한(2,000자) 입력 ────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "1" + " " * 1998 + "시", "1시 " * 666, "1시 30분 " * 250, "9시부터 10시까지 " * 180,
    "오늘 " * 660, "9-" * 1000,
])
def test_long_input_is_bounded(text: str) -> None:
    t = time.perf_counter()
    _qt(text)
    assert time.perf_counter() - t < 2.0


# ── 결함 V-1~V-4 — W2 교정 라운드에서 고침(xfail 해제) ─────────────────────────────
# 표지 없는 1~12시는 확정으로 오전·오후 되묻기가 되므로, 결함 자체를 재도록 사례에 표지를 붙였다

# V-1: 규칙이 모르는 날짜 표기 뒤 시각이 오늘로 풀렸다
@pytest.mark.parametrize("text", [
    "2026.10.12 오전 10시 알람", "2026/10/12 오전 10시 알람", "10/12 오전 9시 알람",
    "10.12 오전 9시 알람", "20261012 오전 9시 알람", "10-12 오전 9시 알람", "12일자 오전 9시 알람",
])
def test_unrecognized_date_before_clock_is_not_today(text: str) -> None:
    qt = _qt(text)
    assert qt.clarify is not None or (
        qt.event is not None and qt.event.start is not None and qt.event.start.day == 12)


# V-2: 다른 기간과 함께 온 날짜 없는 시각이 오늘로 풀려 cover가 오늘까지 넓어졌다
@pytest.mark.parametrize("text", [
    "지난주 오전 9시 알람", "최근 7일간 오전 9시 CPU 사용률", "최근 7일 동안 오전 9시 CPU",
    "작년 오전 9시 알람", "어제(10/12) 오전 9시 알람", "10월 12일(월) 오전 9시 알람",
    "어제 하루 오전 9시 알람",
])
def test_dateless_clock_with_other_period_does_not_widen_to_today(text: str) -> None:
    qt = _qt(text)
    assert qt.clarify is not None or (
        qt.metric is not None and qt.metric.end is not None and qt.metric.end <= _at(0))


# V-3: 날짜 토큰 있는 시각 + 속성 동사가 명시 기간을 버렸다(사건 = 기간 조건 없음)
@pytest.mark.parametrize("text", [
    "어제 오전 9시 이후 등록된 알람", "어제 오전 9시부터 변경된 설정",
    "오늘 오전 9시 이후 추가된 서버",
    "어제 오전 9시부터 접수된 장애",
])
def test_attr_verb_does_not_drop_dated_clock(text: str) -> None:
    qt = _qt(text)
    assert qt.clarify is not None or (qt.event is not None and not qt.event.unbounded)


# V-4: 슬롯 minute·오후 hour 대조가 스팬의 모든 수(월·일)로 통과했다
@pytest.mark.parametrize(("text", "slot"), [
    ("10월 10일 10시 30분 전 알람", {"relation": "until", "end": {
        "month": 10, "day": 10, "hour": 10, "minute": 10}, "span": "10월 10일 10시 30분 전"}),
    ("10월 3일 오후 2시 전 알람", {"relation": "until", "end": {
        "month": 10, "day": 3, "hour": 15}, "span": "10월 3일 오후 2시 전"}),
    ("10월 12일 밤 12시 알람", {"relation": "absolute", "start": {
        "month": 10, "day": 12, "hour": 12}, "span": "10월 12일 밤 12시"}),
])
def test_slot_clock_values_must_come_from_clock(text: str, slot: dict) -> None:
    assert slot_to_spec(slot, text)[1] == "date_mismatch_span"
