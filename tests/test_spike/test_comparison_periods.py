"""기간 대비(비교) 해석 (Plan 82 W9-T6 · SPEC-spike-condition).

`resolve_stat_month_range`(단일 기간)는 **건드리지 않는다** — 형제 함수를 신설하고
기존 경로 동작이 불변임을 함께 단언한다.

DB·LLM 0(D-127).
"""

from __future__ import annotations

from datetime import date

import pytest

from src.utils.query_gen_common import (
    BlockedComparison,
    ComparisonPeriods,
    resolve_comparison_periods,
    resolve_stat_month_range,
)

REF = date(2026, 8, 28)


class TestMonthComparison:
    @pytest.mark.parametrize("query", [
        "전월 대비 급증한 파일시스템",
        "1달 전 대비 사용률이 오른 서버",
        "한 달 전 대비 상승분",
        "지난달 대비 사용률 변화",
    ])
    def test_month_expressions_yield_previous_pair(self, query):
        result = resolve_comparison_periods(query, REF)

        assert result == ComparisonPeriods(base_month="202606", cur_month="202607")

    def test_current_month_is_excluded(self):
        """진행 중인 달의 월 통계는 미완결이라 비교 대상이 아니다(D-076 후속4와 같은 원칙)."""
        result = resolve_comparison_periods("전월 대비 급증", REF)

        assert result.cur_month != "202608"

    def test_year_boundary(self):
        result = resolve_comparison_periods("전월 대비 급증", date(2026, 1, 15))

        assert result == ComparisonPeriods(base_month="202511", cur_month="202512")


class TestWeekIsBlocked:
    @pytest.mark.parametrize("query", [
        "1주일 전 대비 급증한 파일시스템",
        "지난주 대비 사용률이 오른 서버",
        "전주 대비 상승",
    ])
    def test_week_expressions_are_blocked_with_reason(self, query):
        result = resolve_comparison_periods(query, REF)

        assert isinstance(result, BlockedComparison)
        assert "보존기간" in result.reason
        assert "월 단위" in result.suggestion

    def test_week_takes_precedence_over_month(self):
        """주 단위와 월 단위가 함께 있으면 **차단**이 이긴다 — 조용히 월로 바꿔 답하지 않는다."""
        result = resolve_comparison_periods("지난주 대비 그리고 전월 대비 급증", REF)

        assert isinstance(result, BlockedComparison)


class TestNoComparison:
    @pytest.mark.parametrize("query", ["지난달 사용률 보여줘", "CPU 80% 이상 서버", "", None])
    def test_absent_expression_returns_none(self, query):
        assert resolve_comparison_periods(query, REF) is None


class TestExistingRangeUnchanged:
    @pytest.mark.parametrize("query,expected", [
        ("지난달 사용률", ("202607", "202607")),
        ("지난 3개월 평균", ("202605", "202607")),
        ("이번달 사용률", ("202608", "202608")),
        ("2026년 6월 사용률", ("202606", "202606")),
    ])
    def test_single_period_resolution_is_untouched(self, query, expected):
        assert resolve_stat_month_range(query, REF) == expected

    def test_comparison_expression_still_reads_as_single_month(self):
        """"전월 대비"도 종전대로 **단일 월**로 읽힌다 — 이 함수는 바뀌지 않았다.

        ★ 그래서 비교 모드에서 `build_stat_month_block`을 함께 주입하면 안 된다:
        단일 월 등호 필터가 두 기간 비교와 정면 충돌한다(§6.11 · T9가 미주입을 단언).
        """
        assert resolve_stat_month_range("전월 대비 급증", REF) == ("202607", "202607")
