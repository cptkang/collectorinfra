"""plans/122 T-2 · T-3(순수 부분) — 한국어 규칙 인식기 · LLM 슬롯 검증 단위 테스트.

해석 결과(구간·입도)의 골드 채점은 `test_time_gold.py`가 한다. 여기서는 스팬·겹침 규칙,
오탐 차단, 슬롯 폐기 사유(환각 기간 차단)를 본다. LLM 호출 0.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from src.domain.time_expr import interpret, recognize, slot_to_spec
from src.domain.time_spec import KST, PartialDate, TimeSpec, resolve

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)


def kst(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=KST)


def periods(text: str) -> list[TimeSpec]:
    return [s for s in recognize(text) if s.relation != "none"]


class TestRecognizeSpans:
    def test_range_beats_single_month(self) -> None:
        """「2026년 1월부터 6월까지」는 범위 하나 — 첫 월 단일로 읽지 않는다(D-185)."""
        specs = recognize("2026년 1월부터 6월까지 메모리 사용률 최대값")
        assert len(specs) == 1
        spec = specs[0]
        assert spec.relation == "between" and spec.span == "2026년 1월부터 6월까지"
        assert spec.abs_start == PartialDate(year=2026, month=1)
        assert spec.abs_end == PartialDate(month=6)

    def test_grain_inside_claimed_span_is_not_recounted(self) -> None:
        """「지난 3개월간」의 '월간'은 입도로 다시 잡히지 않고, 뒤의 「월간」만 입도다."""
        specs = recognize("지난 3개월간 전체 서버별 월간 CPU 통계")
        assert [(s.span, s.relation, s.display_grain) for s in specs] == [
            ("지난 3개월간", "last", "none"),
            ("월간", "none", "month"),
        ]

    def test_spans_are_ordered_and_disjoint(self) -> None:
        text = "전체 서버의 시간 단위 CPU 사용률을 최근 30일치 전부 보여줘"
        specs = recognize(text)
        positions = [s.span_pos for s in specs]
        assert positions == sorted(positions)  # type: ignore[type-var]
        for spec in specs:
            assert spec.span_pos is not None
            assert text[spec.span_pos[0]:spec.span_pos[1]] == spec.span
        for a, b in zip(positions, positions[1:]):
            assert a is not None and b is not None and a[1] <= b[0]

    def test_invalid_month_makes_no_spec(self) -> None:
        assert recognize("지난 13월 CPU 사용률") == []

    def test_relative_year_is_offset_not_computed(self) -> None:
        """「작년 6월」 — 인식기는 연도를 계산하지 않는다(year_offset)."""
        (spec,) = periods("작년 6월 CPU")
        assert spec.abs_start == PartialDate(year_offset=-1, month=6)

    def test_week_is_lexical(self) -> None:
        """「지난주」 = 달력 주 · 「지난 2주」 = 완결 14일(어휘로 가른다)."""
        (calendar_week,) = periods("지난주 알람")
        assert (calendar_week.relation, calendar_week.n, calendar_week.unit) == ("last", 1, "week")
        (days,) = periods("지난 2주 알람")
        assert (days.relation, days.n, days.unit) == ("last", 14, "day")

    def test_korean_numerals(self) -> None:
        (spec,) = periods("최근 세 달 동안 알람")
        assert (spec.n, spec.unit, spec.span) == (3, "month", "최근 세 달 동안")
        assert periods("세 서버 목록") == []

    def test_since_and_until_suffix(self) -> None:
        (since,) = periods("9월부터 CPU")
        assert since.relation == "since" and since.span == "9월부터"
        (until,) = periods("6월까지 CPU")
        assert until.relation == "until" and until.abs_end == PartialDate(month=6)

    def test_unbounded_marker(self) -> None:
        specs = recognize("메모리 이용률이 90%를 초과한 적이 있는 서버를 조회해줘")
        assert [(s.span, s.unbounded) for s in specs] == [("한 적이 있", True)]

    @pytest.mark.parametrize(
        "text, unit",
        [("지지난달 CPU", "month"), ("전전월 CPU", "month"), ("지지난주 알람", "week"),
         ("지지난 분기 CPU", "quarter"), ("재작년 CPU", "year")],
    )
    def test_two_ago_is_not_read_as_last(self, text: str, unit: str) -> None:
        """「지지난달」 안의 「지난달」·「전전월」 안의 「전월」로 오해석하지 않는다(현행 오독)."""
        (spec,) = periods(text)
        assert (spec.relation, spec.n, spec.unit) == ("ago", 2, unit)

    def test_quarter_and_half_are_calendar_units(self) -> None:
        (q,) = periods("2026년 2분기 CPU")
        assert (q.relation, q.unit, q.abs_start) == ("absolute", "quarter",
                                                     PartialDate(year=2026, month=4))
        (h,) = periods("올해 하반기 CPU")
        assert (h.unit, h.abs_start) == ("half", PartialDate(year_offset=0, month=7))


class TestFalsePositives:
    """기존 정규식의 오탐 차단(D-185)과 이번에 추가한 어휘 경계 — 기간을 만들지 않는다."""

    @pytest.mark.parametrize(
        "text",
        [
            "상위 3-5개 서버",
            "1-6 서버",
            "B-01~B-06 각 5회 반복(캐시 warm)",
            "논리코어가 8개 이상인 서버",
            "3개월 전 대비",  # '개월'의 '월'은 달력 월이 아니다(여기서는 흔적만 남는다)
            "호스트명이 완전일치하는 서버",
            "해당월 CPU 값",
            "그제서야 확인한 서버",
            "용도와 목적이 있는 서버",
            "지난 해당 서버의 OS",
            "경고 이상 알람 이력을 최근 발생 순으로 100건 조회해줘",
            "현재 활성 상태인 심각 알람 목록 보여줘",
            "지금 CPU 사용률 높은 서버 알려줘",
            "요번에 교체한 서버 목록",
        ],
    )
    def test_no_period(self, text: str) -> None:
        assert periods(text) == []


class TestInterpret:
    def test_finest_grain_wins(self) -> None:
        res = interpret("월별로 보되 시간별 상세도 최근 7일", NOW)
        assert res is not None and res.grain == "hour"

    def test_trace_without_rule_is_unresolved(self) -> None:
        assert interpret("3일 전 알람", NOW) is None
        assert interpret("지난 13월 CPU", NOW) is None
        assert interpret("다음 달 예상 사용률", NOW) is None

    def test_quantity_comparison_is_not_a_trace(self) -> None:
        res = interpret("경과년수가 5년 이상인 노후 서버", NOW)
        assert res is not None and res.source == "default"

    def test_grain_only_uses_default_period(self) -> None:
        res = interpret("일별 CPU 사용률", NOW)
        assert res is not None
        assert (res.start, res.end, res.grain, res.source) == (
            kst(2026, 8, 1), kst(2026, 9, 1), "day", "default")

    def test_period_wins_over_unbounded(self) -> None:
        res = interpret("2026년 6월에 메모리 사용률이 90%를 넘은 적이 있는 서버", NOW)
        assert res is not None and not res.unbounded
        assert (res.start, res.end) == (kst(2026, 6, 1), kst(2026, 7, 1))


def _slot(**kwargs: Any) -> dict[str, Any]:
    return kwargs


class TestSlotToSpec:
    def test_valid_slot_resolves(self) -> None:
        """규칙이 못 잡는 「3일 전」을 슬롯이 채운다 — 계산은 해석기가 한다."""
        spec, reason = slot_to_spec(
            _slot(relation="ago", n=3, unit="day", span="3일 전"), "3일 전 알람 보여줘")
        assert reason is None and spec is not None
        assert spec.source == "llm" and spec.span_pos == (0, 4)
        res = resolve(spec, NOW)
        assert (res.start, res.end, res.grain) == (kst(2026, 9, 26), kst(2026, 9, 27), "day")

    def test_hallucinated_span_is_rejected(self) -> None:
        """원문에 없는 기간(R3-05 로그: 「13월」 → 13개월)은 폐기한다."""
        spec, reason = slot_to_spec(
            _slot(relation="last", n=13, unit="month", span="최근 13개월"), "지난 13월 CPU 사용률")
        assert spec is None and reason == "span_not_in_text"

    def test_whitespace_and_case_insensitive_span(self) -> None:
        spec, reason = slot_to_spec(
            _slot(relation="last", n=30, unit="day", span="최근 30일"), "최근30일 CPU")
        assert reason is None and spec is not None and spec.span == "최근30일"
        spec, reason = slot_to_spec(
            _slot(relation="last", n=1, unit="month", span="Last Month"), "last month cpu")
        assert reason is None and spec is not None

    @pytest.mark.parametrize(
        "slot, reason",
        [
            ("not a dict", "slot_not_object"),
            (_slot(n=3, unit="day", span="3일 전"), "relation_missing"),
            (_slot(relation="yesterday", span="어제"), "invalid_relation"),
            (_slot(relation="none", span="CPU"), "no_time_expression"),
            (_slot(relation="ago", n=3, unit="day"), "span_missing"),
            (_slot(relation="ago", n=3, unit="day", span="  "), "span_missing"),
            (_slot(relation="ago", n="3", unit="day", span="3일 전"), "invalid_n"),
            (_slot(relation="ago", n=True, unit="day", span="3일 전"), "invalid_n"),
            (_slot(relation="ago", n=0, unit="day", span="3일 전"), "n_out_of_range"),
            (_slot(relation="ago", n=7, unit="day", span="3일 전"), "n_mismatch_span"),
            (_slot(relation="ago", n=3, unit="fortnight", span="3일 전"), "invalid_unit"),
            (_slot(relation="ago", n=3, span="3일 전"), "unit_missing"),
            (_slot(relation="ago", n=3, unit="day", completeness="partial", span="3일 전"),
             "invalid_completeness"),
            (_slot(relation="ago", n=3, unit="day", anchor="data_latest", span="3일 전"),
             "anchor_data_latest_unsupported"),
            (_slot(relation="ago", n=3, unit="day", anchor="yesterday", span="3일 전"),
             "invalid_anchor"),
            (_slot(relation="ago", n=3, unit="day", display_grain="week", span="3일 전"),
             "invalid_display_grain"),
        ],
    )
    def test_rejections_on_relative(self, slot: Any, reason: str) -> None:
        spec, got = slot_to_spec(slot, "3일 전 알람 보여줘")
        assert spec is None and got == reason

    @pytest.mark.parametrize(
        "slot, reason",
        [
            (_slot(relation="absolute", start={"month": 13}, span="13월"), "month_out_of_range"),
            (_slot(relation="absolute", start={"month": 2, "day": 30}, span="2월 30일"),
             "day_out_of_range"),
            (_slot(relation="absolute", start={"day": 3}, span="3일"), "partial_date_gap"),
            (_slot(relation="absolute", start="2026-06", span="6월"), "invalid_partial_date"),
            (_slot(relation="absolute", start={"month": "6"}, span="6월"), "invalid_partial_date"),
            (_slot(relation="absolute", span="6월"), "start_missing"),
            (_slot(relation="between", start={"month": 1}, span="1월부터"), "end_missing"),
            (_slot(relation="absolute", start={"month": 5}, span="6월"), "date_mismatch_span"),
        ],
    )
    def test_rejections_on_absolute(self, slot: Any, reason: str) -> None:
        text = "13월 2월 30일 3일 6월 1월부터 CPU"
        spec, got = slot_to_spec(slot, text)
        assert spec is None and got == reason

    def test_year_must_come_from_span(self) -> None:
        """「작년 6월」에 LLM이 계산한 연도(2025)는 받지 않는다 — year_offset이면 받는다."""
        text = "작년 6월 CPU 사용률"
        spec, reason = slot_to_spec(
            _slot(relation="absolute", start={"year": 2025, "month": 6}, span="작년 6월"), text)
        assert spec is None and reason == "year_not_in_span"
        slot = _slot(relation="absolute", start={"year_offset": -1, "month": 6}, span="작년 6월")
        spec, reason = slot_to_spec(slot, text)
        assert reason is None and spec is not None
        res = resolve(spec, NOW)
        assert (res.start, res.end) == (kst(2025, 6, 1), kst(2025, 7, 1))

    def test_explicit_year_in_span_is_accepted(self) -> None:
        spec, reason = slot_to_spec(
            _slot(relation="absolute", start={"year": 2026, "month": 6}, span="2026년 6월"),
            "2026년 6월 CPU")
        assert reason is None and spec is not None

    def test_grain_only_slot(self) -> None:
        spec, reason = slot_to_spec(
            _slot(relation="none", display_grain="hour", span="시간별"), "시간별 CPU 사용률")
        assert reason is None and spec is not None
        assert (spec.relation, spec.display_grain) == ("none", "hour")

    def test_this_ignores_n(self) -> None:
        spec, reason = slot_to_spec(
            _slot(relation="this", n=1, unit="month", span="요번 달"), "요번 달 CPU")
        assert reason is None and spec is not None and spec.n is None
        res = resolve(spec, NOW)
        assert (res.start, res.end, res.grain) == (kst(2026, 9, 1), kst(2026, 9, 29), "day")

    def test_derived_n_from_span_units(self) -> None:
        """「지난 2년」을 LLM이 24개월로 옮겨도 스팬의 수(2)에서 유도되면 받는다."""
        spec, reason = slot_to_spec(
            _slot(relation="last", n=24, unit="month", span="지난 2년"), "지난 2년 CPU")
        assert reason is None and spec is not None


class TestInterpretDetail:
    """되묻기 사유 코드(D-291) — 호출부가 되묻기 문구를 고를 수 있게 사유를 가른다."""

    NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)

    @pytest.mark.parametrize("text, code", [
        ("13월 CPU 사용률", "invalid_date"),
        ("2월 30일 알람", "invalid_date"),
        ("2027년 3월 CPU", "future_explicit"),
        ("내년 3월 CPU", "future_relative"),
        ("3일 전 알람", "unresolved"),
    ])
    def test_clarify_codes(self, text: str, code: str) -> None:
        from src.domain.time_expr import interpret_detail

        detail = interpret_detail(text, self.NOW)
        assert detail.resolution is None and detail.clarify == code

    def test_invalid_date_span_is_claimed(self) -> None:
        """「2월 30일」 안의 「2월」을 후순위 규칙이 떼어 가지 않는다(종전 2월 한 달 오해석)."""
        from src.domain.time_expr import recognize_detail

        specs, invalid = recognize_detail("2월 30일 알람")
        assert specs == [] and [code for _, _, code in invalid] == ["day_out_of_range"]
        assert recognize("2월 30일 알람") == []

    def test_resolved_has_no_clarify(self) -> None:
        from src.domain.time_expr import interpret_detail

        detail = interpret_detail("지난달 CPU", self.NOW)
        assert detail.clarify is None and detail.resolution is not None
        assert detail.resolution == interpret("지난달 CPU", self.NOW)
