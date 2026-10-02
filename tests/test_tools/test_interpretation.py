"""기간·건수 해석 도구 검증 (Plan 67 Phase S1 §4.2)."""

from __future__ import annotations

from datetime import date

from src.tools.interpretation import resolve_limit, resolve_time_range


class TestResolveTimeRange:
    def test_absolute_month(self):
        assert resolve_time_range("2026년 6월 사용률", today=date(2026, 7, 29)) == {
            "resolved": True, "start": "202606", "end": "202606",
        }

    def test_relative_n_months_excludes_current(self):
        """진행 중인 달은 제외하고 직전 완결 월까지 잡는다(기존 결정적 규칙 유지)."""
        assert resolve_time_range("지난 3개월", today=date(2026, 7, 29)) == {
            "resolved": True, "start": "202604", "end": "202606",
        }

    def test_no_period_expression(self):
        assert resolve_time_range("서버 목록 조회", today=date(2026, 7, 29)) == {
            "resolved": False, "start": None, "end": None,
        }

    def test_empty_query(self):
        assert resolve_time_range("")["resolved"] is False


class TestResolveLimit:
    def test_explicit_count(self):
        assert resolve_limit("100건 조회", default_limit=1000) == 100

    def test_top_n(self):
        assert resolve_limit("상위 10대", default_limit=1000) == 10

    def test_all_query_raises_limit(self):
        assert resolve_limit("전체 서버", default_limit=1000) > 1000

    def test_default_when_no_expression(self):
        assert resolve_limit("서버 목록", default_limit=50) == 50
