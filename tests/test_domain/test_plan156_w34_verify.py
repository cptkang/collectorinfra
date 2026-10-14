"""plans/156 W3·W4 + W2 교정 라운드 독립 검증.

G-3(사건 시 롤링 끝) · G-5(빈도 어휘) · G-6(N시간 전).

기준 시각 2026-10-13(화) 10:37 KST(§5 사례 표). 통과 테스트는 확정 동작을 고정하고, 검증에서 찾은
결함은 `xfail(strict=True)`로 남긴다(고치면 XPASS로 실패해 표지를 지우게 한다).
실 LLM·DB·네트워크 0.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from src.db_adapters.polestar.time_period import alarm_ts_bounds, stat_bounds
from src.db_adapters.polestar.validators import check_time_conditions
from src.domain.query_time import QueryTime, resolve_query_time
from src.domain.time_expr import slot_to_spec
from src.domain.time_spec import KST, NOTE_MINUTE_WIDENED
from src.orchestration import apm_query as aq

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)


def _at(h: int, m: int = 0, *, day: int = 13) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=KST)


def _qt(text: str, now: datetime = NOW, **kw: object) -> QueryTime:
    return resolve_query_time(text, now, **kw)  # type: ignore[arg-type]


def _bounds(qt: QueryTime) -> tuple[tuple, tuple]:
    assert qt.clarify is None, qt.clarify
    assert qt.event is not None and qt.metric is not None
    return (qt.event.start, qt.event.end), (qt.metric.start, qt.metric.end)


def _views() -> dict[str, aq.ViewSpec]:
    out: dict[str, aq.ViewSpec] = {}
    for v in aq.apm_views():
        out.setdefault(v.window, v)
    return out


# ── G-3 사건 시 롤링 끝 = 기준 분 · 통계 정시 ─────────────────────────────────────

@pytest.mark.parametrize("text", [
    "최근 3시간 알람", "지난 3시간 알람", "최근 3시간 이내 알람", "최근 3 hrs 알람",
    "최근 3 hours 알람", "3시간 전 알람", "3 시간 전 알람", "3시간전 알람", "3시간 전쯤 알람",
])
def test_g3_g6_hour_rolling_event_minute_metric_hour(text: str) -> None:
    event, metric = _bounds(_qt(text))
    assert event == (_at(7, 37), _at(10, 37))
    assert metric == (_at(7), _at(10))


def test_g3_anchor_with_seconds_floors_to_minute() -> None:
    event, metric = _bounds(_qt("최근 3시간 알람", datetime(2026, 10, 13, 10, 37, 42, tzinfo=KST)))
    assert event == (_at(7, 37), _at(10, 37))
    assert metric == (_at(7), _at(10))


def test_g3_on_the_hour_event_equals_metric() -> None:
    event, metric = _bounds(_qt("최근 3시간 알람", _at(10)))
    assert event == metric == (_at(7), _at(10))


def test_g3_single_source_alarm_literals_and_validator() -> None:
    """알람 리터럴·검증기·통계 리터럴이 같은 해석에서 나온다(D-309 ③).

    종전 정시 경계는 반려된다.
    """
    qt = _qt("최근 3시간 알람")
    assert qt.event is not None and qt.metric is not None
    assert alarm_ts_bounds(qt.event) == ("2026-10-13 07:37:00", "2026-10-13 10:37:00")
    sb = stat_bounds(qt.metric)
    assert sb is not None and (sb.lo, sb.hi) == ("2026101307", "2026101310")
    new = ("SELECT * FROM polestar.cmm_alarm a WHERE a.ctime >= TIMESTAMP '2026-10-13 07:37:00' "
           "AND a.ctime < TIMESTAMP '2026-10-13 10:37:00'")
    old = new.replace("07:37:00", "07:00:00").replace("10:37:00", "10:00:00")
    assert check_time_conditions(new, qt) == []
    assert check_time_conditions(old, qt) != []


def test_g3_apm_windows_keep_stat_interpretation() -> None:
    """APM은 종전 그대로.

    hourly는 정시 끝, range는 기준 시각까지 민 같은 길이, 비교는 정시 구간.
    """
    views = _views()
    qt = _qt("최근 3시간 응답시간")
    hourly = aq.plan_window(views["hourly"], None, NOW, query_time=qt)
    assert hourly.args == {"reference_time": "2026-10-13T10:00:00+09:00", "lookback_minutes": 180}
    rng = aq.plan_window(views["range"], None, NOW, query_time=qt)
    assert rng.args == {"reference_time": None, "lookback_minutes": 180}
    cmp_ = aq.plan_compare_window(qt)
    assert cmp_.args == {
        "current_start": "2026-10-13T07:00:00+09:00", "current_end": "2026-10-13T10:00:00+09:00",
        "baseline_start": "2026-10-13T04:00:00+09:00", "baseline_end": "2026-10-13T07:00:00+09:00",
    }


def test_g6_hour_ago_apm_compare_matches_last_n_hours() -> None:
    a = aq.plan_compare_window(_qt("3시간 전 응답시간"))
    b = aq.plan_compare_window(_qt("최근 3시간 응답시간"))
    assert a.args == b.args


# ── G-6 N시간 전 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "event", "metric"), [
    ("1시간 전부터 알람", (_at(9, 37), _at(10, 37)), (_at(9), _at(10))),
    ("24시간 전 알람", (_at(10, 37, day=12), _at(10, 37)), (_at(10, day=12), _at(10))),
    ("10시간 전 알람", (_at(0, 37), _at(10, 37)), (_at(0), _at(10))),
    ("3시간 전에 발생한 알람", (_at(7, 37), _at(10, 37)), (_at(7), _at(10))),
])
def test_g6_hour_ago_cases(text: str, event: tuple, metric: tuple) -> None:
    assert _bounds(_qt(text)) == (event, metric)


@pytest.mark.parametrize("text", [
    "3일 전 알람", "0시간 전 알람", "10000시간 전 알람", "세 시간 전 알람", "3시간 전까지 알람",
    "3시간 전후 알람",
])
def test_g6_kept_clarify(text: str) -> None:
    assert _qt(text).clarify == "unresolved"


def test_g6_slot_hour_ago_matches_rule() -> None:
    """규칙이 못 잡는 「세 시간 전」을 슬롯 ago·hour로 받으면 규칙과 같은 구간이다."""
    slot = {"relation": "ago", "n": 3, "unit": "hour", "span": "세 시간 전"}
    expected = ((_at(7, 37), _at(10, 37)), (_at(7), _at(10)))
    assert _bounds(_qt("세 시간 전 알람", slot=slot)) == expected


# ── G-5 빈도·수량 어휘 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "최근 3시간 이상 걸린 배치", "3시간마다 알람", "3일 간격 점검", "최근 3일 간격 점검",
    "최근 2주 이상 알람", "3시간째 장애", "최근 3시간째 장애", "최근 3시간씩 배치",
    "최근 3시간 넘게 걸린", "최근 3주일 이상", "최근 3개월 이상", "1시간마다 수집",
    "최근 30 min_avg 알람",
])
def test_g5_frequency_is_not_period(text: str) -> None:
    qt = _qt(text)
    assert qt.clarify is None and qt.metric is not None and qt.metric.source == "default"
    assert qt.event is not None and qt.event.unbounded


@pytest.mark.parametrize(("text", "days"), [("최근 3주일 알람", 21), ("최근 3주일간 알람", 21),
                                            ("최근 2 days 알람", 2)])
def test_g5_week_day_words_stay_period(text: str, days: int) -> None:
    event, _ = _bounds(_qt(text))
    assert event[1] - event[0] == timedelta(days=days)


@pytest.mark.parametrize(("text", "span"), [
    ("3시간 이상 걸린 배치", "3시간"), ("최근 3일 간격 점검", "최근 3일"),
])
def test_g5_slot_guard_rejects_frequency(text: str, span: str) -> None:
    unit = "hour" if "시간" in span else "day"
    spec, reason = slot_to_spec({"relation": "last", "n": 3, "unit": unit, "span": span}, text)
    assert spec is None and reason == "freq_not_period"


# ── 표지 없는 1~12시 되묻기 — 과잉 되묻기 없음 · 표지 환산 ──────────────────────────

@pytest.mark.parametrize("text", [
    "최근 12시간 알람", "12시간 동안 알람", "최근 1시간 반 알람", "1시간 반 동안 알람",
    "최근 24시간 이내 알람", "10시간 전 알람",
])
def test_no_meridiem_clarify_for_durations(text: str) -> None:
    assert _qt(text).clarify is None


def test_three_oclock_direction_is_not_clock() -> None:
    assert _qt("3시 방향 서버").clarify is None


@pytest.mark.parametrize(("text", "start", "end"), [
    ("어제 오전 9시부터 11시까지 알람", _at(9, day=12), _at(11, day=12)),
    ("어제 오후 2시부터 4시까지 알람", _at(14, day=12), _at(16, day=12)),
    ("어제 오후 1시부터 3시까지 알람", _at(13, day=12), _at(15, day=12)),
    ("어제 오전 9~11시 알람", _at(9, day=12), _at(11, day=12)),
    ("어제 오후 2~4시 알람", _at(14, day=12), _at(16, day=12)),
    ("어제 오전 9시부터 오후 12시까지 알람", _at(9, day=12), _at(12, day=12)),
])
def test_range_end_inherits_start_meridiem(text: str, start: datetime, end: datetime) -> None:
    event, metric = _bounds(_qt(text))
    assert event == metric == (start, end)


def test_pm_minute_range_widens_metric() -> None:
    qt = _qt("어제 오후 2시 30분부터 3시까지 알람")
    assert _bounds(qt) == ((_at(14, 30, day=12), _at(15, day=12)),
                           (_at(14, day=12), _at(15, day=12)))
    assert qt.metric is not None and NOTE_MINUTE_WIDENED in qt.metric.notes


@pytest.mark.parametrize(("text", "hour", "day"), [
    ("아침 9시 알람", 9, 13), ("어제 저녁 7시 알람", 19, 12), ("새벽 1시 알람", 1, 13),
    ("어제 밤 11시 알람", 23, 12), ("어제 오후 12시 알람", 12, 12), ("어제 오전 12시 알람", 0, 12),
])
def test_meridiem_word_conversion(text: str, hour: int, day: int) -> None:
    event, _ = _bounds(_qt(text))
    assert event[0] == _at(hour, day=day)


@pytest.mark.parametrize(("text", "code"), [
    ("밤 12시 알람", "unresolved"), ("저녁 7시 알람", "future_explicit"),
    ("1시 30분 알람", "clock_meridiem"), ("어제 9~10시 알람", "clock_meridiem"),
    ("어제 22시부터 오늘 2시까지", "clock_meridiem"),
    ("오전 10시부터 10시 30분까지 알람", "clock_range_in_progress"),
])
def test_clock_clarify_codes(text: str, code: str) -> None:
    assert _qt(text).clarify == code


# ── 성능 — 1만 자 반복 패턴 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    " " * 10000, "시" * 10000, "분" * 10000, "9시" * 5000, "9 시 " * 2500, "9시 30분 " * 1200,
    "오전 9 " * 2000, "이십" * 5000, "9~" * 5000, "9" + " " * 9998 + "시",
    "9시" + " " * 9998 + "부터", "3 hours " * 1200, "9시 " + "가나다 " * 3000 + "생성된",
])
def test_long_inputs_stay_fast(text: str) -> None:
    t0 = time.perf_counter()
    _qt(text)
    slot_to_spec({"relation": "last", "n": 3, "unit": "hour", "span": text[:200]}, text)
    assert time.perf_counter() - t0 < 2.0


# ── 결함(검증 지적) — J-1~J-3 교정 · J-4 기각(D-309) · J-5만 xfail(strict) ─────────

@pytest.mark.parametrize("text", [
    "최근 3시간 이상징후 알람", "최근 3일 이상 징후", "최근 1시간 이상 탐지 결과",
    "최근 7일 이상 없는 서버", "최근 3시간 이상한 알람",
])
def test_j1_anomaly_word_keeps_period_or_asks(text: str) -> None:
    qt = _qt(text)
    assert qt.clarify is not None or qt.explicit


@pytest.mark.parametrize("text", [
    "15일 서버 오전 9시 알람", "15일 web01 서버 오전 9시 알람",
    "10/12 서버 web01 관련 알람 중 오전 9시 이후",
    "월요일에 발생한 서버 web01 알람 오전 9시 이후",
])
def test_j2_detached_date_fragment_asks(text: str) -> None:
    assert _qt(text).clarify is not None


@pytest.mark.parametrize("text", [
    "어제 오전 9시부터 정오까지 알람", "어제 오전 9시부터 자정까지 알람",
    "어제 오전 9시부터 오후까지 알람", "어제 오후 2시부터 3시간 알람",
    "어제 오후 2시부터 3시간 동안 알람",
])
def test_j3_since_does_not_swallow_end_expression(text: str) -> None:
    qt = _qt(text)
    assert qt.clarify is not None or (qt.event is not None and qt.event.end is not None
                                      and qt.event.end <= _at(0))


# J-4 기각(팀 리드 2026-10-14): G-6 「3일 전 되묻기 유지」는 규칙 경로의 현행 유지다.
# 슬롯 ago·day 채택은 D-309(규칙 미매칭 「3일 전」 = 슬롯 채택)가 정한 현행이라 그대로 고정한다.
def test_j4_day_ago_slot_accepted_per_d309() -> None:
    qt = _qt("3일 전 알람", slot={"relation": "ago", "n": 3, "unit": "day", "span": "3일 전"})
    assert qt.clarify is None and qt.slot_status == "accepted"
    assert qt.metric is not None and qt.metric.source == "llm"
    assert (qt.metric.start, qt.metric.end) == (_at(0, day=10), _at(0, day=11))


@pytest.mark.xfail(strict=True, reason="J-5: 「오전 9시부터 12시까지」 끝이 오전 12시(=0시)로"
                   " 승계돼"
                   " 역전 → invalid_spec 되묻기")
@pytest.mark.parametrize("text", [
    "어제 오전 9시부터 12시까지 알람", "어제 오전 11시부터 1시까지 알람",
])
def test_j5_morning_to_noon_range(text: str) -> None:
    assert _qt(text).clarify is None
