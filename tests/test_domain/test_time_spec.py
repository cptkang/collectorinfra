"""plans/122 T-1 — 시간 도메인 모델 + 결정적 해석기 단위 테스트.

정책 표(§10.3.1) 전 행의 골드 채점은 `test_time_gold.py`가 한다. 여기서는 모델 불변식 ·
표 밖 조합 · 소비처용 원시 연산(`window_around` · `relative_window` · `cover`)을 본다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from src.domain.time_spec import (
    KST,
    NOTE_DEFAULT_PERIOD,
    NOTE_EMPTY_RANGE,
    NOTE_FUTURE_PERIOD,
    NOTE_MULTIPLE_PERIODS,
    PartialDate,
    TimeResolution,
    TimeSpec,
    TimeSpecError,
    cover,
    default_resolution,
    relative_window,
    resolve,
    window_around,
)

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)  # §10.3.1 기준 시각(화)


def kst(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST)


def _code(exc: pytest.ExceptionInfo[TimeSpecError]) -> str:
    return exc.value.code


class TestPartialDate:
    @pytest.mark.parametrize(
        "kwargs, code",
        [
            ({}, "partial_date_empty"),
            ({"month": 13}, "month_out_of_range"),
            ({"month": 0}, "month_out_of_range"),
            ({"month": 2, "day": 30}, "day_out_of_range"),
            ({"year": 2026, "month": 2, "day": 29}, "day_out_of_range"),
            ({"day": 3}, "partial_date_gap"),
            ({"month": 9, "hour": 3}, "partial_date_gap"),
            ({"month": 9, "day": 1, "hour": 24}, "hour_out_of_range"),
            ({"year": 2026, "year_offset": -1}, "partial_date_year_conflict"),
            ({"year": 1800}, "year_out_of_range"),
        ],
    )
    def test_invalid_values_raise_with_code(self, kwargs: dict[str, int], code: str) -> None:
        with pytest.raises(TimeSpecError) as exc:
            PartialDate(**kwargs)
        assert _code(exc) == code

    def test_leap_day_allowed_when_year_unknown(self) -> None:
        assert PartialDate(month=2, day=29).precision == "day"

    def test_precision(self) -> None:
        assert PartialDate(year=2026).precision == "year"
        assert PartialDate(month=6).precision == "month"
        assert PartialDate(month=6, day=1, hour=3).precision == "hour"


class TestTimeSpecValidation:
    @pytest.mark.parametrize(
        "kwargs, code",
        [
            ({"relation": "yesterday"}, "invalid_relation"),
            ({"relation": "last", "unit": "month", "anchor": "data_latest"},
             "anchor_data_latest_unsupported"),
            ({"relation": "last"}, "unit_missing"),
            ({"relation": "last", "unit": "month", "n": 0}, "n_out_of_range"),
            ({"relation": "last", "unit": "month", "n": 121}, "n_out_of_range"),
            ({"relation": "last", "unit": "month", "n": True}, "invalid_n"),
            ({"relation": "absolute"}, "start_missing"),
            ({"relation": "between", "abs_start": PartialDate(month=1)}, "end_missing"),
            ({"relation": "last", "unit": "day", "anchor": "explicit"},
             "anchor_explicit_without_date"),
            ({"relation": "last", "unit": "day", "unbounded": True}, "unbounded_with_period"),
            ({"relation": "absolute", "unit": "quarter", "abs_start": PartialDate(month=2)},
             "unit_misaligned"),
            ({"relation": "none", "display_grain": "week"}, "invalid_display_grain"),
        ],
    )
    def test_invalid_specs_raise_with_code(self, kwargs: dict[str, object], code: str) -> None:
        with pytest.raises(TimeSpecError) as exc:
            TimeSpec(**kwargs)  # type: ignore[arg-type]
        assert _code(exc) == code


class TestResolveTimezone:
    def test_naive_now_is_kst(self) -> None:
        res = resolve(TimeSpec("last", n=1, unit="month"), datetime(2026, 9, 29, 10, 0))
        assert res.start == kst(2026, 8, 1) and res.end == kst(2026, 9, 1)

    def test_aware_now_is_converted_to_kst(self) -> None:
        """UTC 2026-08-31 16:00 = KST 2026-09-01 01:00 — 「지난달」은 8월이다."""
        utc = datetime(2026, 8, 31, 16, 0, tzinfo=UTC)
        res = resolve(TimeSpec("last", n=1, unit="month"), utc)
        assert res.start == kst(2026, 8, 1) and res.end == kst(2026, 9, 1)
        assert res.anchor_at.utcoffset() == timedelta(hours=9)


class TestResolveRelations:
    def test_explicit_anchor_last_days(self) -> None:
        """「9월 15일 기준 최근 7일」 — 기준일 자체는 완결 전이라 제외(오늘 제외 규칙과 같다)."""
        spec = TimeSpec("last", n=7, unit="day", anchor="explicit",
                        abs_start=PartialDate(month=9, day=15))
        res = resolve(spec, NOW)
        assert (res.start, res.end) == (kst(2026, 9, 8), kst(2026, 9, 15))
        assert res.anchor == "explicit" and res.anchor_at == kst(2026, 9, 15)

    def test_ago_and_next(self) -> None:
        ago = resolve(TimeSpec("ago", n=3, unit="day"), NOW)
        assert (ago.start, ago.end, ago.grain) == (kst(2026, 9, 26), kst(2026, 9, 27), "day")
        nxt = resolve(TimeSpec("next", n=1, unit="month"), NOW)
        assert (nxt.start, nxt.end) == (kst(2026, 10, 1), kst(2026, 11, 1))
        assert NOTE_FUTURE_PERIOD in nxt.notes

    def test_last_to_date_includes_current_unit(self) -> None:
        """「최근 3개월(이번 달 포함)」 — 7월 1일 ~ 어제, 일 입도."""
        res = resolve(TimeSpec("last", n=3, unit="month", completeness="to_date"), NOW)
        assert (res.start, res.end, res.grain) == (kst(2026, 7, 1), kst(2026, 9, 29), "day")

    def test_rolling_month_clamps_day(self) -> None:
        """롤링 1개월을 3월 31일 10시에 — 2월 말일로 맞춘다."""
        res = resolve(TimeSpec("last", n=1, unit="month", completeness="rolling"),
                      kst(2026, 3, 31, 10, 30))
        assert (res.start, res.end, res.grain) == (
            kst(2026, 2, 28, 10), kst(2026, 3, 31, 10), "hour")

    def test_between_reversed_is_sorted(self) -> None:
        spec = TimeSpec("between", abs_start=PartialDate(2026, 6), abs_end=PartialDate(2026, 1))
        res = resolve(spec, NOW)
        assert (res.start, res.end) == (kst(2026, 1, 1), kst(2026, 7, 1))

    def test_between_with_start_year_only(self) -> None:
        """「2025년 11월부터 2월까지」 — 끝은 다음 해(D-185)."""
        spec = TimeSpec("between", abs_start=PartialDate(2025, 11), abs_end=PartialDate(month=2))
        res = resolve(spec, NOW)
        assert (res.start, res.end) == (kst(2025, 11, 1), kst(2026, 3, 1))

    def test_since_future_explicit_year_is_clarified(self) -> None:
        """연도를 명시한 미래 시작은 되묻기(2026-09-30 사용자 확정 · D-291).

        종전은 빈 구간 + 미래 고지였다.
        """
        with pytest.raises(TimeSpecError) as exc:
            resolve(TimeSpec("since", abs_start=PartialDate(2027, 1)), NOW)
        assert exc.value.code == "future_explicit_period"

    def test_yearless_leap_day_goes_back_to_leap_year(self) -> None:
        """연도 미상 「2월 29일」을 평년에 물으면 가장 최근 윤년(2024)이다."""
        res = resolve(TimeSpec("absolute", abs_start=PartialDate(month=2, day=29)),
                      kst(2026, 3, 1))
        assert (res.start, res.end) == (kst(2024, 2, 29), kst(2024, 3, 1))

    def test_none_relation_is_default(self) -> None:
        res = resolve(TimeSpec("none"), NOW)
        assert res == default_resolution(NOW)


class TestDefaultResolution:
    def test_year_wrap(self) -> None:
        res = default_resolution(kst(2026, 1, 15, 9))
        assert (res.start, res.end, res.grain) == (kst(2025, 12, 1), kst(2026, 1, 1), "month")
        assert res.source == "default" and res.notes == (NOTE_DEFAULT_PERIOD,)


class TestTimeResolution:
    def test_invariants(self) -> None:
        with pytest.raises(TimeSpecError):
            TimeResolution(start=datetime(2026, 1, 1), end=kst(2026, 2, 1), grain="month",
                           completeness="complete", source="rule", anchor_at=NOW)
        with pytest.raises(TimeSpecError):
            TimeResolution(start=kst(2026, 3, 1), end=kst(2026, 2, 1), grain="month",
                           completeness="complete", source="rule", anchor_at=NOW)
        with pytest.raises(TimeSpecError):
            TimeResolution(start=kst(2026, 1, 1), end=None, grain="month",
                           completeness="complete", source="rule", anchor_at=NOW,
                           unbounded=True)

    def test_month_range_projection(self) -> None:
        assert resolve(TimeSpec("last", n=3, unit="month"), NOW).month_range() == (
            "202606", "202608")
        assert resolve(TimeSpec("this", unit="month"), NOW).month_range() is None
        assert resolve(TimeSpec("this", unit="month"), kst(2026, 9, 1, 10)).month_range() is None
        assert resolve(TimeSpec("none", unbounded=True), NOW).month_range() is None
        # 월 경계에 맞는 일 입도 구간도 월로 투영된다(월 투영은 입도가 아니라 경계만 본다)
        assert resolve(TimeSpec("last", n=31, unit="day"), kst(2026, 9, 1, 10)).month_range() == (
            "202608", "202608")

    @pytest.mark.parametrize(
        "spec, now, want",
        [
            (TimeSpec("last", n=30, unit="day"), NOW, "2026-08-30 ~ 2026-09-28"),
            (TimeSpec("last", n=1, unit="month"), NOW, "2026-08-01 ~ 2026-08-31"),
            (TimeSpec("last", n=1, unit="hour"), NOW, "2026-09-29 09:00 ~ 2026-09-29 10:00"),
            (TimeSpec("this", unit="month"), kst(2026, 9, 1, 10), "2026-09-01 (완결된 구간 없음)"),
            (TimeSpec("none", unbounded=True), NOW, "전체 보관 기간(기간 조건 없음)"),
            (TimeSpec("until", abs_end=PartialDate(2026, 6)), NOW, "~ 2026-06-30"),
        ],
    )
    def test_label_is_human_readable(self, spec: TimeSpec, now: datetime, want: str) -> None:
        label = resolve(spec, now).label()
        assert label == want
        assert "202608" not in label and "202609" not in label  # 'YYYYMM' 비노출


class TestCover:
    def test_disjoint_months_hull(self) -> None:
        a = resolve(TimeSpec("absolute", abs_start=PartialDate(2026, 5)), NOW)
        b = resolve(TimeSpec("absolute", abs_start=PartialDate(2026, 7)), NOW)
        res = cover([a, b])
        assert (res.start, res.end, res.grain) == (kst(2026, 5, 1), kst(2026, 8, 1), "month")
        assert NOTE_MULTIPLE_PERIODS in res.notes

    def test_mixed_grain_takes_finer(self) -> None:
        last_month = resolve(TimeSpec("last", n=1, unit="month"), NOW)
        this_month = resolve(TimeSpec("this", unit="month"), NOW)
        res = cover([last_month, this_month])
        assert (res.start, res.end, res.grain, res.completeness) == (
            kst(2026, 8, 1), kst(2026, 9, 29), "day", "to_date")

    def test_single_passthrough_and_empty(self) -> None:
        one = resolve(TimeSpec("last", n=1, unit="month"), NOW)
        assert cover([one]) is one
        with pytest.raises(TimeSpecError):
            cover([])

    def test_unbounded_dominates(self) -> None:
        res = cover([resolve(TimeSpec("none", unbounded=True), NOW),
                     resolve(TimeSpec("last", n=1, unit="month"), NOW)])
        assert res.unbounded and res.start is None


class TestWindowAround:
    def test_hour_window_snaps_outward(self) -> None:
        """사건 14:37 전 2시간 · 후 30분 → [12:00, 16:00)(121 TP-3.1 사건 기준 창)."""
        res = window_around(kst(2026, 9, 28, 14, 37), timedelta(hours=2), timedelta(minutes=30),
                            "hour")
        assert (res.start, res.end, res.grain) == (kst(2026, 9, 28, 12), kst(2026, 9, 28, 16),
                                                   "hour")
        assert res.anchor == "explicit" and res.anchor_at == kst(2026, 9, 28, 14, 37)
        assert res.completeness == "rolling"

    def test_day_window_and_aligned_center(self) -> None:
        res = window_around(kst(2026, 9, 28), timedelta(days=1), timedelta(0), "day")
        assert (res.start, res.end) == (kst(2026, 9, 27), kst(2026, 9, 28))

    def test_invalid_arguments(self) -> None:
        with pytest.raises(TimeSpecError):
            window_around(NOW, timedelta(hours=-1), timedelta(0), "hour")
        with pytest.raises(TimeSpecError):
            window_around(NOW, timedelta(hours=1), timedelta(0), "week")


class TestRelativeWindow:
    """plans/122 H-2 `period_covers.relative` 자리표 — 반개구간 날짜."""

    @pytest.mark.parametrize(
        "kind, kwargs, anchor, want",
        [
            ("last_month", {}, NOW, (date(2026, 8, 1), date(2026, 9, 1))),
            ("this_month", {}, NOW, (date(2026, 9, 1), date(2026, 9, 29))),
            ("this_month", {}, kst(2026, 9, 1, 10), (date(2026, 9, 1), date(2026, 9, 1))),
            ("last_n_months", {"n": 3}, NOW, (date(2026, 6, 1), date(2026, 9, 1))),
            ("last_n_months", {"n": 6}, NOW, (date(2026, 3, 1), date(2026, 9, 1))),
            ("month_span", {"month_from": 11, "month_to": 2}, NOW,
             (date(2025, 11, 1), date(2026, 3, 1))),
            ("month_span", {"month_from": 11, "month_to": 2}, kst(2026, 1, 15, 9),
             (date(2024, 11, 1), date(2025, 3, 1))),
            ("last_month", {}, kst(2026, 1, 15, 9), (date(2025, 12, 1), date(2026, 1, 1))),
        ],
    )
    def test_kinds(
        self, kind: str, kwargs: dict[str, int], anchor: datetime, want: tuple[date, date]
    ) -> None:
        assert relative_window(kind, anchor, **kwargs) == want

    def test_missing_arguments_and_unknown_kind(self) -> None:
        with pytest.raises(TimeSpecError):
            relative_window("last_n_months", NOW)
        with pytest.raises(TimeSpecError):
            relative_window("month_span", NOW, month_from=1)
        with pytest.raises(TimeSpecError):
            relative_window("last_week", NOW)

    def test_same_policy_as_resolver(self) -> None:
        """하네스와 제품이 같은 해석을 쓴다(단일 출처)."""
        res = resolve(TimeSpec("last", n=1, unit="month"), NOW)
        assert res.start is not None and res.end is not None
        assert relative_window("last_month", NOW) == (res.start.date(), res.end.date())


def test_empty_range_note_on_first_of_month() -> None:
    res = resolve(TimeSpec("this", unit="month"), kst(2026, 9, 1, 10))
    assert res.is_empty and NOTE_EMPTY_RANGE in res.notes and res.grain == "day"


class TestEventSubjectAndClarify:
    """2026-09-30 사용자 확정(D-291) — 알람 경로 예외 · 연도 명시 미래 되묻기(정책 표 §10.3.1)."""

    def test_event_default_is_unbounded(self) -> None:
        res = default_resolution(NOW, subject="event")
        assert res.unbounded and res.source == "default"
        assert "event_no_default_period" in res.notes

    def test_metric_default_is_unchanged(self) -> None:
        res = default_resolution(NOW)
        assert (res.start, res.end, res.grain) == (kst(2026, 8, 1), kst(2026, 9, 1), "month")

    def test_event_this_month_runs_to_anchor(self) -> None:
        res = resolve(TimeSpec("this", unit="month"), kst(2026, 9, 29, 10, 37), subject="event")
        assert (res.start, res.end, res.completeness) == (kst(2026, 9, 1), kst(2026, 9, 29, 10, 37),
                                                          "to_date")
        assert "event_to_now" in res.notes

    def test_invalid_subject_is_rejected(self) -> None:
        with pytest.raises(TimeSpecError) as exc:
            resolve(TimeSpec("this", unit="month"), NOW, subject="stat")  # type: ignore[arg-type]
        assert exc.value.code == "invalid_subject"

    def test_future_explicit_absolute_and_between(self) -> None:
        for spec in (TimeSpec("absolute", abs_start=PartialDate(2027, 3)),
                     TimeSpec("between", abs_start=PartialDate(2027, 1),
                              abs_end=PartialDate(2027, 3))):
            with pytest.raises(TimeSpecError) as exc:
                resolve(spec, NOW)
            assert exc.value.code == "future_explicit_period"

    def test_yearless_month_is_never_future(self) -> None:
        """연도 없는 월은 「미래가 아닌 가장 최근」이라 되묻지 않는다(D-185 유지)."""
        res = resolve(TimeSpec("absolute", abs_start=PartialDate(month=12)), NOW)
        assert (res.start, res.end) == (kst(2025, 12, 1), kst(2026, 1, 1))
