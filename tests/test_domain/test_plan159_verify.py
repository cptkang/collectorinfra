"""plans/159 독립 검증(D-328) — 결함 재현(xfail strict) + 경계 고정.

기준 시각 2026-10-13(화) 10:37 KST. 실 LLM·DB·네트워크 0. 결함은 `xfail(strict=True)`로 남긴다 —
교정되면 XPASS가 실패로 떠서 마커를 지우게 한다(V-1~V-4는 W1 교정 라운드에서 해제).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.query_time import resolve_query_time
from src.domain.time_expr import interpret_detail, recognize
from src.domain.time_spec import KST, NOTE_MINUTE_WIDENED, TimeSpec, resolve

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)


def _at(h: int, m: int = 0, *, day: int = 13, month: int = 10, year: int = 2026) -> datetime:
    return datetime(year, month, day, h, m, tzinfo=KST)


# ── 회귀: 시간 기간 뒤에 붙은 분 간격 ────────────────────────────────────────────

# 교정됨 — W1 교정 라운드에서 xfail 해제(기대값 그대로):
# V-1: `_b_last_n`이 접미(동안·치) 뒤 m.end()에서 `_MINUTE_TAIL_RE`를 본다 — 「5분 간격」을
# 합성 꼬리로 오인해 「최근 3시간 동안」을 버리고 흔적 → 되묻기(기준 SHA는 최근 3시간)
@pytest.mark.parametrize("text", [
    "최근 3시간 동안 5분 간격 CPU 추이",
    "최근 3시간치 10분 간격 TPS",
])
def test_hour_period_with_suffix_then_minute_interval_keeps_hour(text: str) -> None:
    it = interpret_detail(text, NOW)
    assert it.clarify is None
    assert it.resolution is not None
    assert (it.resolution.start, it.resolution.end) == (_at(7), _at(10))


# 교정됨 — W1 교정 라운드에서 xfail 해제(기대값 그대로):
# V-2: `hour_minute` 합성이 바로 뒤 빈도 어휘(간격·마다)를 보지 않는다 — 「지난 3시간 10분
# 간격」이 190분 기간이 된다(D-328 ⑧ 「5분 간격·5분마다」는 기간 아님 · 기준 SHA는 3시간)
@pytest.mark.parametrize("text", [
    "지난 3시간 10분 간격 TPS 추세",
    "최근 3시간 5분마다 CPU",
    "최근 2시간 30분 간격으로 응답시간",
])
def test_composite_does_not_swallow_adjacent_minute_interval(text: str) -> None:
    periods = [(s.relation, s.n, s.unit) for s in recognize(text) if s.relation != "none"]
    assert periods and periods[0][2] == "hour", periods


# 교정됨 — W1 교정 라운드에서 xfail 해제(기대값 그대로):
# V-3: `last_n`(접두 「최근」)은 단위 뒤 빈도·수량 어휘를 보지 않는다 — 「최근 5분 간격」·
# 「최근 30분 이상 걸린」이 분 기간이 된다(D-328 ⑧ · 기준 SHA는 기간 없음 = 기본값)
@pytest.mark.parametrize("text", [
    "최근 5분 간격으로 CPU 추이",
    "최근 5분마다 수집된 CPU",
    "최근 30분 이상 걸린 거래",
    "최근 30분 넘게 걸린 배치",
])
def test_prefixed_minute_frequency_or_quantity_is_not_a_period(text: str) -> None:
    assert not [s for s in recognize(text) if s.unit == "minute"]


# ── 슬롯 경로: 시각 뒤 분(D-328 ⑤) ───────────────────────────────────────────────

# 교정됨 — W1 교정 라운드에서 xfail 해제(기대값 그대로):
# V-4: 시각 뒤 분은 규칙이 일부러 흔적으로 남기지만(⑤ 되묻기) 슬롯 경로(`slot_to_spec`)에 같은
# 결정적 가드가 없다 — LLM이 unit=minute 슬롯을 내면 「10시 30분부터」가 최근 30분으로 수락된다
@pytest.mark.parametrize("text, span", [
    ("10시 30분부터 알람", "30분"),
    ("14시 30분 알람", "30분"),
    ("10시 30분부터 알람", "10시 30분부터"),
])
def test_slot_minute_after_clock_is_still_asked(text: str, span: str) -> None:
    slot = {"relation": "last", "n": 30, "unit": "minute", "span": span}
    qt = resolve_query_time(text, NOW, slot=slot)
    assert qt.clarify is not None, (qt.slot_status, qt.event)


# ── 경계 계산(통과 고정) ────────────────────────────────────────────────────────

@pytest.mark.parametrize(("n", "metric_start"), [
    (1, _at(9)), (59, _at(9)), (60, _at(9)), (61, _at(8)),
    (119, _at(8)), (120, _at(8)), (121, _at(7)),
])
def test_ceil_hours_boundary(n: int, metric_start: datetime) -> None:
    res = resolve(TimeSpec(relation="last", n=n, unit="minute"), NOW, subject="metric")
    assert (res.start, res.end) == (metric_start, _at(10))
    assert NOTE_MINUTE_WIDENED in res.notes


@pytest.mark.parametrize("anchor", [
    _at(0, 30, month=11, day=1),
    _at(0, 30, year=2027, month=1, day=1),
])
def test_ninety_minutes_across_month_and_year_boundary(anchor: datetime) -> None:
    ev = resolve(TimeSpec(relation="last", n=90, unit="minute"), anchor, subject="event")
    me = resolve(TimeSpec(relation="last", n=90, unit="minute"), anchor, subject="metric")
    midnight = anchor.replace(minute=0)
    assert (ev.start, ev.end) == (anchor - timedelta(minutes=90), anchor)  # 전날 23:00 ~ 00:30
    assert (me.start, me.end) == (midnight - timedelta(hours=2), midnight)  # 전날 22:00 ~ 00:00
    assert NOTE_MINUTE_WIDENED not in ev.notes and NOTE_MINUTE_WIDENED in me.notes


def test_anchor_with_microseconds_and_utc_input() -> None:
    at =datetime(2026, 10, 13, 1, 37, 59, 999999, tzinfo=UTC)  # = 10:37:59.999999 KST
    ev = resolve(TimeSpec(relation="ago", n=5, unit="minute"), at, subject="event")
    assert (ev.start, ev.end) == (_at(10, 32), _at(10, 37))


@pytest.mark.parametrize("text", [
    "5분 전체 알람", "5분 전까지 알람", "5분 전보다 늘었나", "5분 전송 에러",
    "1시간 30분 이상 걸린 배치", "2시간 반영", "오후 2시 30분 알람", "10 시 30 분 알람",
    "30분할 CPU", "분당 처리량", "세 분", "3분기 CPU",
])
def test_non_minute_or_non_period_words_make_no_minute_period(text: str) -> None:
    assert not [s for s in recognize(text) if s.unit == "minute"]


@pytest.mark.parametrize("text", ["최근 10081분", "최근 200시간 30분", "12345분 전"])
def test_over_n_max_is_asked(text: str) -> None:
    it = interpret_detail(text, NOW)
    assert it.resolution is None and it.clarify is not None
