"""result_organizer 노드 테스트.

데이터 정리, 마스킹, 충분성 판단, 숫자 포맷팅을 검증한다.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.nodes.result_organizer import (
    _check_data_sufficiency,
    _format_numbers,
    _generate_summary,
    _get_unit_for_column,
    result_organizer,
)
from src.state import create_initial_state


class TestCheckDataSufficiency:
    """데이터 충분성 판단 검증."""

    def test_empty_results_are_sufficient(self):
        """빈 결과(0건)는 충분하다고 판단한다 ('해당 데이터 없음' 응답)."""
        assert _check_data_sufficiency([], {}, None) is True

    def test_results_without_template_are_sufficient(self):
        """양식 없이 결과가 있으면 충분하다."""
        results = [{"hostname": "web-01"}]
        assert _check_data_sufficiency(results, {}, None) is True

    def test_template_with_enough_columns(self):
        """양식 헤더 대비 충분한 컬럼이 있으면 충분하다."""
        results = [{"hostname": "web-01", "ip": "10.0.0.1", "os": "Ubuntu"}]
        template = {"sheets": [{"headers": ["서버명", "IP"], "header_row": 1, "data_start_row": 2}]}
        assert _check_data_sufficiency(results, {}, template) is True

    def test_template_with_insufficient_columns(self):
        """양식 헤더 대비 컬럼이 50% 미만이면 부족하다."""
        results = [{"hostname": "web-01"}]
        template = {"sheets": [{"headers": ["서버명", "IP", "OS", "CPU", "메모리"], "header_row": 1, "data_start_row": 2}]}
        assert _check_data_sufficiency(results, {}, template) is False


class TestGetUnitForColumn:
    """컬럼명 기반 단위 추론 검증."""

    def test_pct_column(self):
        rules = {"pct": "%", "_gb": " GB"}
        assert _get_unit_for_column("usage_pct", rules) == "%"

    def test_gb_column(self):
        rules = {"pct": "%", "_gb": " GB"}
        assert _get_unit_for_column("total_gb", rules) == " GB"

    def test_unknown_column(self):
        rules = {"pct": "%"}
        assert _get_unit_for_column("hostname", rules) == ""


class TestFormatNumbers:
    """숫자 포맷팅 검증."""

    def test_percentage_format(self):
        results = [{"usage_pct": 85.333}]
        formatted = _format_numbers(results, {})
        assert formatted[0]["usage_pct"] == "85.3%"

    def test_gb_format(self):
        results = [{"total_gb": 128.0}]
        formatted = _format_numbers(results, {})
        assert "GB" in str(formatted[0]["total_gb"])

    def test_non_numeric_unchanged(self):
        results = [{"hostname": "web-01"}]
        formatted = _format_numbers(results, {})
        assert formatted[0]["hostname"] == "web-01"

    def test_no_unit_number_unchanged(self):
        """단위 추론이 불가한 숫자는 원본을 유지한다."""
        results = [{"id": 42}]
        formatted = _format_numbers(results, {})
        assert formatted[0]["id"] == 42


class TestGenerateSummary:
    """요약 생성 검증."""

    def test_empty_results(self):
        summary = _generate_summary([], {})
        assert "데이터가 없습니다" in summary

    def test_with_results(self):
        results = [{"a": 1}, {"a": 2}, {"a": 3}]
        parsed = {"query_targets": ["서버", "CPU"]}
        summary = _generate_summary(results, parsed)
        assert "3건" in summary
        assert "서버" in summary

    def test_no_targets(self):
        results = [{"a": 1}]
        parsed = {"query_targets": []}
        summary = _generate_summary(results, parsed)
        assert "1건" in summary


class TestResultOrganizerNode:
    """result_organizer 노드 전체 동작 검증."""

    @pytest.mark.asyncio
    async def test_basic_organization(self, sample_query_results):
        """기본 데이터 정리가 올바르게 수행된다."""
        state = create_initial_state(user_query="CPU 사용률 조회")
        state["query_results"] = sample_query_results
        state["parsed_requirements"] = {
            "query_targets": ["서버", "CPU"],
            "output_format": "text",
        }

        with patch("src.nodes.result_organizer.load_config") as mock_config:
            mock_config.return_value.security.sensitive_columns = ["password"]
            mock_config.return_value.security.mask_pattern = "***MASKED***"
            result = await result_organizer(state)

        assert result["organized_data"]["is_sufficient"] is True
        assert len(result["organized_data"]["rows"]) == 3
        assert result["current_node"] == "result_organizer"

    @pytest.mark.asyncio
    async def test_empty_results(self):
        """빈 결과도 정상 처리된다."""
        state = create_initial_state(user_query="test")
        state["query_results"] = []
        state["parsed_requirements"] = {"query_targets": ["서버"], "output_format": "text"}

        with patch("src.nodes.result_organizer.load_config") as mock_config:
            mock_config.return_value.security.sensitive_columns = []
            mock_config.return_value.security.mask_pattern = "***"
            result = await result_organizer(state)

        assert result["organized_data"]["is_sufficient"] is True
        assert "데이터가 없습니다" in result["organized_data"]["summary"]

    @pytest.mark.asyncio
    async def test_sensitive_data_masked(self):
        """민감 데이터가 마스킹된다."""
        state = create_initial_state(user_query="test")
        state["query_results"] = [
            {"hostname": "web-01", "password": "secret123"},
        ]
        state["parsed_requirements"] = {"query_targets": ["서버"], "output_format": "text"}

        with patch("src.nodes.result_organizer.load_config") as mock_config:
            mock_config.return_value.security.sensitive_columns = ["password"]
            mock_config.return_value.security.mask_pattern = "***MASKED***"
            result = await result_organizer(state)

        rows = result["organized_data"]["rows"]
        assert rows[0]["password"] == "***MASKED***"
        assert rows[0]["hostname"] == "web-01"
