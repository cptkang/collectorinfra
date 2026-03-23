"""input_parser target_sheets extraction tests."""

from __future__ import annotations

from src.nodes.input_parser import _extract_target_sheets


class TestExtractTargetSheets:
    """Test the _extract_target_sheets function."""

    def test_llm_parsed_sheets_takes_priority(self):
        """LLM-parsed target_sheets is preferred over regex."""
        parsed = {"target_sheets": ["CPU Data"]}
        result = _extract_target_sheets(parsed, "some query")
        assert result == ["CPU Data"]

    def test_llm_parsed_null_falls_back_to_regex(self):
        """When LLM returns null, regex extraction is attempted."""
        parsed = {"target_sheets": None}
        result = _extract_target_sheets(parsed, "'Server Info' sheet")
        # Korean keyword required for regex; English won't match
        assert result is None

    def test_single_quoted_sheet_name_korean(self):
        """Korean: extract sheet name from single-quoted pattern."""
        parsed = {}
        result = _extract_target_sheets(parsed, "'CPU Data' sheet")
        assert result is None  # no Korean keyword

    def test_korean_sheet_keyword(self):
        """Korean regex: '시트명' 시트."""
        parsed = {}
        result = _extract_target_sheets(parsed, "'서버현황' 시트만 채워줘")
        assert result == ["서버현황"]

    def test_double_quoted_sheet_name(self):
        """Korean regex: "시트명" 시트."""
        parsed = {}
        # Using straight double quotes inside the query
        result = _extract_target_sheets(
            parsed,
            '"CPU 메트릭" 시트에 데이터 넣어줘',
        )
        assert result == ["CPU 메트릭"]

    def test_no_sheet_indication_returns_none(self):
        """No sheet indication returns None (all sheets)."""
        parsed = {}
        result = _extract_target_sheets(parsed, "전체 서버 CPU 현황 조회해줘")
        assert result is None

    def test_llm_empty_list_falls_back(self):
        """LLM returns empty list, falls back to regex."""
        parsed = {"target_sheets": []}
        result = _extract_target_sheets(parsed, "'메모리' 시트만 업데이트해줘")
        assert result == ["메모리"]

    def test_multiple_sheets_in_query(self):
        """Multiple sheet names in one query."""
        parsed = {}
        result = _extract_target_sheets(
            parsed,
            "'서버현황' 시트랑 'CPU 메트릭' 시트에 데이터 채워줘",
        )
        assert result is not None
        assert "서버현황" in result
        assert "CPU 메트릭" in result
