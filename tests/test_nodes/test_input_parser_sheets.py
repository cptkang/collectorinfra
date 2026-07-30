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


class TestRegexFallbackFalsePositives:
    """정규식 폴백 오탐 차단 (Plan 67 R3-(ii) / A10).

    LLM `target_sheets`가 1순위이고, 정규식은 "시트" 키워드가 따옴표에 인접할 때만 인정한다.
    종전 두 번째 패턴은 `시트`가 선택이어서 따옴표+조사 표현을 시트명으로 오탐했다.
    """

    def test_quoted_region_with_particle_is_not_a_sheet(self):
        """"'서울'의 서버" 같은 따옴표 지역명은 시트명이 아니다."""
        assert _extract_target_sheets({}, "'서울'의 서버 목록 조회해줘") is None
        assert _extract_target_sheets({}, "'김포'에 있는 서버 알려줘") is None
        assert _extract_target_sheets({}, "'공동존'만 조회해줘") is None

    def test_sheet_keyword_inside_quotes_still_matched(self):
        """따옴표 안에 '시트'가 포함된 형태는 그대로 인정한다."""
        assert _extract_target_sheets({}, "'요약시트'만 채워줘") == ["요약시트"]

    def test_llm_result_takes_priority_over_regex(self):
        """LLM 산출물이 있으면 정규식 폴백을 타지 않는다."""
        parsed = {"target_sheets": ["요약"]}
        assert _extract_target_sheets(parsed, "'서버현황' 시트만 채워줘") == ["요약"]

    def test_llm_result_is_sanitized(self):
        """LLM 산출물의 공백·비문자열·중복은 정리한다."""
        parsed = {"target_sheets": [" 요약 ", 3, None, "요약", "CPU"]}
        assert _extract_target_sheets(parsed, "질의") == ["요약", "CPU"]

    def test_llm_result_all_invalid_falls_back_to_regex(self):
        """LLM 산출물이 전부 무효면 정규식 폴백으로 내려간다."""
        parsed = {"target_sheets": [None, "  "]}
        assert _extract_target_sheets(parsed, "'서버현황' 시트만 채워줘") == ["서버현황"]
