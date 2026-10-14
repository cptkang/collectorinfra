"""plans/156 W2 — 시각 시작점·범위 해석(G-1·G-2 (a) · D-328 ⑤ 개정).

기준 시각 2026-10-13(화) 10:37 KST(§5 사례 표). 사건(E) = 분 정밀 · 통계(M) = 시 경계로 넓힌다
(분 시각이면 `minute_widened_to_hour`). 시점 단독은 그 시 한 칸. 미래 시각·역전·자정 걸침(날짜 하나)
= 되묻기. 「3시 방향」·「9시 이후 생성된」 같은 비기간 반례를 함께 고정한다.

2026-10-14 사용자 확정(W2 교정 라운드): 표지 없는 1~12시는 오전·오후를 되묻는다(`clock_meridiem`) —
그래서 해석 사례는 「오전 9시」·13~23시로 쓴다. 끝이 명시된 닫힌 범위의 통계가 비면 되묻는다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain.query_time import clarify_message, resolve_query_time
from src.domain.time_expr import recognize, slot_to_spec
from src.domain.time_spec import (
    KST,
    NOTE_CLOCK_PREVIOUS_HOUR,
    NOTE_EVENT_TO_NOW,
    NOTE_MINUTE_WIDENED,
    NOTE_PERIOD_IN_PROGRESS,
    NOTE_YEAR_INFERRED,
    PartialDate,
    TimeSpec,
    TimeSpecError,
    resolve,
)

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)


def _at(h: int, m: int = 0, *, day: int = 13, month: int = 10) -> datetime:
    return datetime(2026, month, day, h, m, tzinfo=KST)


def _both(text: str, now: datetime = NOW) -> tuple[tuple, tuple]:
    qt = resolve_query_time(text, now)
    assert qt.clarify is None, (text, qt.clarify)
    assert qt.metric is not None and qt.event is not None
    return (qt.metric.start, qt.metric.end), (qt.event.start, qt.event.end)


# ── §5 사례 표 · §1.1 ① 시각 행 ───────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "metric", "event"), [
    # plans/156 확정(2026-10-14) — 표지 없는 1~12시는 되묻으므로 사례에 표지를 붙였다
    ("오늘 오전 9시부터 알람", (_at(9), _at(10)), (_at(9), _at(10, 37))),
    ("어제 오전 10시 30분부터 알람",
     (_at(10, day=12), _at(10)), (_at(10, 30, day=12), _at(10, 37))),
    ("오늘 오전 9시부터 10시까지 알람", (_at(9), _at(10)), (_at(9), _at(10))),  # 끝은 표지를 잇는다
    ("어제 22시부터 오늘 새벽 2시까지 알람",
     (_at(22, day=12), _at(2)), (_at(22, day=12), _at(2))),
    ("2026년 9월 15일 오전 10시 30분부터 알람",
     (_at(10, month=9, day=15), _at(10)), (_at(10, 30, month=9, day=15), _at(10, 37))),
    ("어제 오전 10시 알람",
     (_at(10, day=12), _at(11, day=12)), (_at(10, day=12), _at(11, day=12))),
    ("오늘 오전 9시 알람", (_at(9), _at(10)), (_at(9), _at(10))),
    ("9월 15일 14시 알람",
     (_at(14, month=9, day=15), _at(15, month=9, day=15)),
     (_at(14, month=9, day=15), _at(15, month=9, day=15))),
    ("오늘 오전 9시부터 10시 30분까지 CPU", (_at(9), _at(10)), (_at(9), _at(10, 30))),
    ("오전 9~10시 알람", (_at(9), _at(10)), (_at(9), _at(10))),
    ("어제 오전 10시 반부터 알람",
     (_at(10, day=12), _at(10)), (_at(10, 30, day=12), _at(10, 37))),
    ("2026-09-15 오전 10시부터 알람", (_at(10, month=9, day=15), _at(10)),
     (_at(10, month=9, day=15), _at(10, 37))),
    ("어제 오후 2시 알람", (_at(14, day=12), _at(15, day=12)), (_at(14, day=12), _at(15, day=12))),
    ("오전 9시 이후 발생한 알람", (_at(9), _at(10)), (_at(9), _at(10, 37))),
    ("0시부터 알람", (_at(0), _at(10)), (_at(0), _at(10, 37))),  # 0시는 표지 없이 해석한다
])
def test_clock_cases(text: str, metric: tuple, event: tuple) -> None:
    assert _both(text) == (metric, event)


def test_minute_clock_widens_metric_with_notice() -> None:
    """분 시각 → 통계는 시 경계로 넓히고 고지 · 사건은 고지 없이 분 그대로 · 정시는 고지 없음."""
    qt = resolve_query_time("어제 오전 10시 30분부터 알람", NOW)
    assert qt.metric is not None and qt.event is not None
    assert NOTE_MINUTE_WIDENED in qt.metric.notes
    assert NOTE_MINUTE_WIDENED not in qt.event.notes
    qt = resolve_query_time("오늘 오전 9시부터 알람", NOW)
    assert qt.metric is not None and NOTE_MINUTE_WIDENED not in qt.metric.notes


def test_year_inferred_note_kept() -> None:
    qt = resolve_query_time("9월 15일 14시 알람", NOW)
    assert qt.metric is not None and NOTE_YEAR_INFERRED in qt.metric.notes


def test_clock_is_one_spec_with_date() -> None:
    """날짜 토큰과 시각은 한 명세다 — 「오늘」이 따로 하루로 잡히지 않는다."""
    (spec,) = [s for s in recognize("오늘 오전 9시부터 10시까지 알람") if s.relation != "none"]
    assert spec.relation == "between" and spec.span == "오늘 오전 9시부터 10시까지"
    assert spec.abs_start == PartialDate(day_offset=0, hour=9)
    assert spec.abs_end == PartialDate(day_offset=0, hour=10)
    (spec,) = recognize("어제 오전 10시 30분부터 알람")
    assert spec.relation == "since" and spec.abs_start == PartialDate(
        day_offset=-1, hour=10, minute=30)


# ── 되묻기 ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "code"), [
    ("오늘 14시부터 알람", "future_explicit"),       # 14시는 기준 시각보다 미래(G-1)
    ("오늘 14시부터 CPU", "future_explicit"),
    ("14시 30분 알람", "future_explicit"),           # 시점 단독 · 날짜 없음 = 오늘
    ("10월 13일 14시 알람", "future_explicit"),      # 연도 추론은 날짜까지만 — 작년 14시가 아니다
    ("오늘 오후 2시부터 4시까지 알람", "future_explicit"),
    ("오늘 14시부터 오전 9시까지 알람", "invalid_spec"),  # 날짜 하나에 시각 역전(G-2)
    ("오늘 22시부터 새벽 2시까지 알람", "invalid_spec"),  # 자정 걸침은 두 날짜가 모두 있을 때만
    ("어제 9시 전 알람", "unresolved"),             # 「전」 — 이전인지 N분 전인지 모호
    ("10시 30분 전 알람", "unresolved"),
    ("10시 30분 동안 CPU", "unresolved"),
    ("밤 12시 알람", "unresolved"),                  # 날짜가 모호한 표지
    ("오후 14시 알람", "unresolved"),
    ("15일 9시 알람", "unresolved"),                 # 못 읽은 날짜 조각 — 오늘로 읽지 않는다
    ("25시 알람", "invalid_clock"),                 # 없는 시각 — 날짜가 아니라 시각 문구(교정)
    ("10시 70분부터 알람", "invalid_clock"),
    ("내일 9시부터 알람", "future_relative"),
    # plans/156 확정(2026-10-14) — 표지 없는 1~12시는 오전·오후를 되묻는다
    ("1시 30분 알람", "clock_meridiem"),
    ("2시 반 회의", "clock_meridiem"),
    ("3시부터 5시까지 알람", "clock_meridiem"),
    ("12시 알람", "clock_meridiem"),
    ("오늘 9시부터 알람", "clock_meridiem"),
    ("9~10시 알람", "clock_meridiem"),
    # 끝에 자기 날짜가 있으면 표지를 잇지 않는다
    ("어제 22시부터 오늘 2시까지 알람", "clock_meridiem"),
    # plans/156 확정(2026-10-14) — 끝이 명시된 닫힌 범위의 통계가 비면 전용 코드로 되묻는다
    # ((c) 대체 없음 · 끝이 미래여도 같다)
    ("오전 10시부터 오전 10시 30분까지 알람", "clock_range_in_progress"),
    ("오늘 오전 10시부터 오후 2시까지 알람", "clock_range_in_progress"),
])
def test_clarify(text: str, code: str) -> None:
    qt = resolve_query_time(text, NOW)
    assert qt.clarify == code, (text, qt.clarify)


# plans/156 확정 (c) — 시작 시각의 시가 진행 중이라 통계 구간이 비면 요청을 되묻지 않는다.
# 사건은 [시작, 기준 시각) 그대로, 통계는 직전 정시 한 칸 [09:00, 10:00) + `clock_previous_hour`.
# 끝이 열린 「…부터」와 시점 단독만이다 — 닫힌 범위는 되묻는다(`test_clarify` · 2026-10-14 확정)
@pytest.mark.parametrize("text, event", [
    ("오전 10시 30분부터 알람", (_at(10, 30), _at(10, 37))),
    ("오늘 오전 10시부터 알람", (_at(10), _at(10, 37))),
    ("오전 10시 알람", (_at(10), _at(10, 37))),            # 시점 단독 — 진행 중인 시
])
def test_clock_in_current_hour_metric_falls_back_to_previous_hour(text: str, event: tuple) -> None:
    """「10시 30분부터」 등(10:37) — 사건은 기준 시각까지, 통계는 직전 정시 한 시간 + 고지."""
    qt = resolve_query_time(text, NOW)
    assert qt.clarify is None, (text, qt.clarify)
    assert qt.event is not None and qt.metric is not None
    assert (qt.event.start, qt.event.end) == event
    assert (qt.metric.start, qt.metric.end) == (_at(9), _at(10))
    assert NOTE_CLOCK_PREVIOUS_HOUR in qt.metric.notes
    assert NOTE_MINUTE_WIDENED not in qt.metric.notes  # 넓힌 게 아니라 대체했다
    assert NOTE_CLOCK_PREVIOUS_HOUR not in qt.event.notes
    # 대체 구간은 완결 구간이다 — 진행 중 고지와 모순되지 않게 뺀다(교정 Minor)
    assert qt.metric.completeness == "complete"
    assert NOTE_PERIOD_IN_PROGRESS not in qt.metric.notes
    assert NOTE_EVENT_TO_NOW in qt.event.notes  # 사건은 기준 시각까지(D-291)


def test_clock_previous_hour_not_used_when_hour_completed() -> None:
    """시작 시의 시가 이미 끝났으면(11:05의 「10시 30분부터」) 대체하지 않는다 — 넓힘 고지만."""
    qt = resolve_query_time("오전 10시 30분부터 알람", _at(11, 5))
    assert qt.metric is not None
    assert (qt.metric.start, qt.metric.end) == (_at(10), _at(11))
    assert NOTE_MINUTE_WIDENED in qt.metric.notes
    assert NOTE_CLOCK_PREVIOUS_HOUR not in qt.metric.notes


def test_clock_previous_hour_notice_phrase() -> None:
    """새 고지 코드는 disclosure 단일 출처에 문구가 있다(사본 대조는 test_plan123_judge)."""
    from src.domain import disclosure as disc

    assert NOTE_CLOCK_PREVIOUS_HOUR in disc.KIND_TABLE
    assert disc.KIND_TABLE[NOTE_CLOCK_PREVIOUS_HOUR].grade == "neutral"
    assert "직전 정시" in disc._PERIOD_NOTE_PHRASES[NOTE_CLOCK_PREVIOUS_HOUR]


# ── 시점 단독 = 그 시 한 칸 + 고지 ────────────────────────────────────────────────

def test_point_with_minute_is_hour_bucket() -> None:
    later = _at(15, 10)
    metric, event = _both("14시 30분 알람", later)
    assert metric == event == (_at(14), _at(15))
    qt = resolve_query_time("14시 30분 알람", later)
    assert qt.metric is not None and NOTE_MINUTE_WIDENED in qt.metric.notes


def test_incident_style_point_after_the_hour() -> None:
    """「14시 장애 원인」은 15시에 물으면 그 시 한 칸, 10:37에는 미래라 되묻는다."""
    assert _both("14시 장애 원인", _at(15)) == ((_at(14), _at(15)), (_at(14), _at(15)))
    assert resolve_query_time("14시 장애 원인", NOW).clarify == "future_explicit"


def test_meridiem_range_inherits_marker() -> None:
    metric, event = _both("오늘 오후 2시부터 4시까지 알람", _at(17))
    assert metric == event == (_at(14), _at(16))


# ── 비기간 반례(거짓 되묻기·오해석 방지) ────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "3시 방향 서버",
    "9시 이후 생성된 티켓",
    "9시 이전 생성된 티켓",
    "오전 9시 이후 등록된 서버",   # 날짜 없는 시각만 속성 필터다(날짜가 있으면 기간 — V-3)
    "배치 9시 실행 서버",          # 실행 계열 속성(교정 Minor)
    "13시에 실행된 배치",
    "10시스템 점검 결과",
    "5시리즈 모델 목록",
])
def test_not_a_period(text: str) -> None:
    qt = resolve_query_time(text, NOW)
    assert qt.clarify is None, (text, qt.clarify)
    assert qt.metric is not None and qt.metric.source == "default"
    assert qt.event is not None and qt.event.unbounded


@pytest.mark.parametrize(("text", "n", "unit"), [
    ("최근 3시간 알람", 3, "hour"),
    ("최근 1시간 30분 알람", 90, "minute"),
    ("1시간 반 동안 CPU", 90, "minute"),
    ("최근 24시간 CPU", 24, "hour"),
])
def test_hour_durations_untouched(text: str, n: int, unit: str) -> None:
    """「N시간」은 시각이 아니다 — 종전 기간 규칙 그대로."""
    (spec,) = [s for s in recognize(text) if s.relation != "none"]
    assert (spec.relation, spec.n, spec.unit) == ("last", n, unit)


# ── 해석기(time_spec) — 반열림 끝 · 단위 지정 absolute ─────────────────────────────

def test_until_clock_end_is_exclusive_point() -> None:
    res = resolve(TimeSpec("until", abs_end=PartialDate(month=10, day=13, hour=9)), NOW)
    assert (res.start, res.end) == (None, _at(9))


def test_absolute_with_day_unit_stays_day() -> None:
    """absolute + unit=day + 시는 종전대로 그 날 하루(시각 경로 대상 아님)."""
    spec = TimeSpec("absolute", unit="day", abs_start=PartialDate(month=9, day=15, hour=10))
    res = resolve(spec, NOW)
    assert (res.start, res.end) == (_at(0, month=9, day=15), _at(0, month=9, day=16))


@pytest.mark.parametrize(("fields", "code"), [
    ({"month": 9, "day": 1, "hour": 9, "minute": 60}, "minute_out_of_range"),
    ({"month": 9, "day": 1, "minute": 30}, "partial_date_gap"),
    ({"day_offset": -1}, "partial_date_gap"),
    ({"day_offset": -1, "month": 9, "hour": 9}, "partial_date_day_conflict"),
    ({"day_offset": 1, "hour": 9}, "day_out_of_range"),
])
def test_partial_date_clock_validation(fields: dict, code: str) -> None:
    with pytest.raises(TimeSpecError) as exc:
        PartialDate(**fields)
    assert exc.value.code == code


# ── LLM 슬롯 — minute 키(범위 · gap · 스팬 대조) ──────────────────────────────────

TEXT = "10월 12일 오전 10시 30분부터 알람"


def _slot(start: dict, span: str = "10월 12일 오전 10시 30분부터") -> dict:
    return {"relation": "since", "start": start, "span": span}


def test_slot_minute_accepted_and_resolved() -> None:
    spec, reason = slot_to_spec(_slot({"month": 10, "day": 12, "hour": 10, "minute": 30}), TEXT)
    assert reason is None and spec is not None
    assert spec.abs_start == PartialDate(month=10, day=12, hour=10, minute=30)
    ev = resolve(spec, NOW, subject="event")
    assert (ev.start, ev.end) == (_at(10, 30, day=12), _at(10, 37))


@pytest.mark.parametrize(("start", "code"), [
    ({"month": 10, "day": 12, "hour": 10, "minute": 61}, "minute_out_of_range"),
    ({"month": 10, "day": 12, "minute": 30}, "partial_date_gap"),
    ({"month": 10, "day": 12, "hour": 10, "minute": 45}, "date_mismatch_span"),
    ({"month": 10, "day": 12, "hour": 11, "minute": 30}, "date_mismatch_span"),
])
def test_slot_minute_rejected(start: dict, code: str) -> None:
    assert slot_to_spec(_slot(start), TEXT) == (None, code)


@pytest.mark.parametrize(("text", "span", "start"), [
    ("9월 1일 오전 10시 반부터 알람", "9월 1일 오전 10시 반부터",
     {"month": 9, "day": 1, "hour": 10, "minute": 30}),
    ("9월 1일 오후 2시부터 알람", "9월 1일 오후 2시부터", {"month": 9, "day": 1, "hour": 14}),
    ("9월 1일 오전 10시 정각부터 알람", "9월 1일 오전 10시 정각부터",
     {"month": 9, "day": 1, "hour": 10, "minute": 0}),
    ("9월 1일 오후 2시부터 4시까지 알람", "9월 1일 오후 2시부터 4시까지",
     {"month": 9, "day": 1, "hour": 16}),  # 끝은 시작의 표지를 잇는다
])
def test_slot_clock_span_forms(text: str, span: str, start: dict) -> None:
    """「반」=30분 · 「오후 2시」=14시 · 0분(정각)은 스팬 대조를 통과한다."""
    spec, reason = slot_to_spec(_slot(start, span), text)
    assert reason is None and spec is not None


# ── W2 교정 라운드(2026-10-14) ─────────────────────────────────────────────────────

@pytest.mark.parametrize(("span", "start", "code"), [
    # 표지 없는 1~12시 — 슬롯이 오전으로 읽어도 되묻는다(규칙과 같은 확정)
    ("10월 12일 10시 30분부터", {"month": 10, "day": 12, "hour": 10, "minute": 30},
     "clock_meridiem_missing"),
    # 표지 없는 2시를 14시로 계산한 슬롯 — 스팬의 시와 다르다
    ("10월 12일 2시부터", {"month": 10, "day": 12, "hour": 14}, "date_mismatch_span"),
    # 「오후 2시부터 4시까지」의 4를 오전 4시로 읽은 슬롯 — 표지를 이으면 16시다
    ("10월 12일 오후 2시부터 4시까지", {"month": 10, "day": 12, "hour": 4}, "date_mismatch_span"),
])
def test_slot_meridiem_rules(span: str, start: dict, code: str) -> None:
    assert slot_to_spec(_slot(start, span), span + " 알람") == (None, code)


def test_slot_meridiem_reason_is_asked_like_rule() -> None:
    """규칙이 못 잡은 시각(「아홉시」)도 슬롯 hour 1~12 + 표지 없음이면 오전·오후를 되묻는다."""
    slot = {"relation": "absolute", "start": {"month": 10, "day": 12, "hour": 9}, "span": "아홉시"}
    qt = resolve_query_time("아홉시 알람", NOW, slot=slot)
    assert qt.clarify == "clock_meridiem" and qt.slot_reason == "clock_meridiem_missing"


@pytest.mark.parametrize(("span", "hour", "clarify"), [
    ("아홉시", 21, "clock_meridiem"),   # 한글 수사 시는 1~12시 — 13~23이어도 표지가 없으면 되묻는다
    ("아홉시", 9, "clock_meridiem"),
    ("오후 아홉시", 21, None),          # 표지가 있으면 환산값만 받는다
])
def test_slot_word_clock_meridiem(span: str, hour: int, clarify: str | None) -> None:
    """숫자로 쓴 시가 없는 슬롯 스팬 — 표지 없으면 hour 값과 무관하게 되묻는다(W2 교정 3)."""
    slot = {"relation": "absolute", "start": {"month": 10, "day": 12, "hour": hour}, "span": span}
    qt = resolve_query_time(span + " 알람", NOW, slot=slot)
    assert qt.clarify == clarify
    if clarify is None:
        assert qt.slot_reason is None


@pytest.mark.parametrize(("span", "hour", "code"), [
    ("오후 아홉시", 9, "date_mismatch_span"),   # 표지 환산값(21)과 다르다
    ("오후아홉시", 21, None),
    ("밤 열한시", 23, None),
    ("영시", 0, None),
    ("자정", 0, None),
    ("정오", 12, None),
    ("오늘", 15, "date_mismatch_span"),          # 스팬에 시가 없다
])
def test_slot_word_clock_span_check(span: str, hour: int, code: str | None) -> None:
    slot = {"relation": "absolute", "start": {"month": 10, "day": 12, "hour": hour}, "span": span}
    assert slot_to_spec(slot, span + " 알람")[1] == code


@pytest.mark.parametrize(("code", "phrase"), [
    ("clock_meridiem", "오전인지 오후인지"),
    ("invalid_clock", "없는 시각"),
    ("clock_range_in_progress", "아직 끝나지 않은 시간대"),
])
def test_clock_clarify_messages(code: str, phrase: str) -> None:
    msg = clarify_message(code)
    assert msg.startswith("[조회 기간] ") and phrase in msg and "「오전 9시" in msg


def test_slot_closed_range_in_progress_is_asked_like_rule() -> None:
    """슬롯 경로도 닫힌 범위 통계가 비면 같은 코드로 되묻는다."""
    slot = {"relation": "between", "start": {"month": 10, "day": 13, "hour": 10},
            "end": {"month": 10, "day": 13, "hour": 10, "minute": 30}, "span": "오전 열시 반"}
    qt = resolve_query_time("오전 열시 반 알람", NOW, slot=slot)
    assert qt.clarify == "clock_range_in_progress" and qt.slot_reason == "clock_range_empty"


def test_dated_clock_with_attr_verb_is_a_period() -> None:
    """V-3 — 날짜가 있는 시각은 속성 동사가 붙어도 명시 기간이다(사건이 기간 없음이 되지 않는다)."""
    _, event = _both("어제 오전 9시 이후 등록된 알람")
    assert event == (_at(9, day=12), _at(10, 37))


@pytest.mark.parametrize("text", [
    "2026.10.12 오전 10시 알람", "10/12 오전 9시 알람", "20261012 오전 9시 알람",
    "12일자 오전 9시 알람", "10월 12일(월) 오전 9시 알람",
])
def test_unknown_date_fragment_before_clock_is_asked(text: str) -> None:
    """V-1 — 규칙이 모르는 숫자 날짜 뒤 시각을 오늘로 읽지 않는다."""
    assert resolve_query_time(text, NOW).clarify == "unresolved"


@pytest.mark.parametrize("text", [
    "지난주 오전 9시 알람", "최근 7일간 오전 9시 CPU", "작년 15시 알람", "어제 하루 오전 9시 알람",
    "오늘날 오전 9시 알람", "그제서야 오전 9시 알람",
    "오전 9시와 오전 10시 사이 알람",   # 「사이」는 범위로 풀지 않고 되묻는다
])
def test_dateless_clock_ambiguities_are_asked(text: str) -> None:
    """V-2 · 날짜 낱말 경계 — 날짜 없는 시각이 다른 기간·날짜 같은 낱말과 함께 오면 되묻는다."""
    assert resolve_query_time(text, NOW).clarify == "unresolved"


def test_two_dateless_clocks_compare_today() -> None:
    """날짜 없는 시각끼리는 오늘의 두 시각이다(V-2 대상 아님)."""
    metric, _ = _both("오전 9시와 오전 8시 알람 비교")
    assert metric == (_at(8), _at(10))


def test_event_since_clock_has_event_to_now() -> None:
    qt = resolve_query_time("어제 오전 9시부터 알람", NOW)
    assert qt.event is not None and NOTE_EVENT_TO_NOW in qt.event.notes
    qt = resolve_query_time("어제 오전 9시 알람", NOW)  # 끝난 시 — 기준 시각까지가 아니다
    assert qt.event is not None and NOTE_EVENT_TO_NOW not in qt.event.notes


def test_minute_head_event_is_not_empty() -> None:
    """10:30:20의 「오전 10시 30분부터」 — 사건은 분 경계로 깎으면 비므로 기준 시각까지 둔다."""
    now = NOW.replace(minute=30, second=20)
    qt = resolve_query_time("오전 10시 30분부터 알람", now)
    assert qt.clarify is None and qt.event is not None
    assert (qt.event.start, qt.event.end) == (_at(10, 30), now)
    assert "empty_range" not in qt.event.notes


@pytest.mark.parametrize("text", [
    "1" + " " * 9998 + "시", "오늘" + " " * 9990 + "9시", "오전" + " " * 9990 + "9시",
    "9시" + " " * 9990 + "생성된", "1시 30분 " * 1250, "10/12 " * 1600 + "9시",
    "9시부터" + " " * 9990 + "10시", "1" + " " * 9998 + "월",
])
def test_ten_thousand_chars_under_one_second(text: str) -> None:
    import time

    t = time.perf_counter()
    resolve_query_time(text, NOW)
    assert time.perf_counter() - t < 1.0
