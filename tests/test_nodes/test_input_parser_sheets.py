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


class TestTargetSheetsValidatedAgainstTemplate:
    """양식에 없는 시트명은 지목으로 인정하지 않는다 (plans/108 CU-A1).

    run `20260918-182507` 실측: `target_sheets`가 비-None인 4턴 중 **3턴이 양식 시트와
    무관한 이름**이었고(`['은행존']`·`['리소스 현황']` — 실제 시트는 `서버정보`·`리소스상태`),
    그중 2턴이 **헤더만 있는 빈 엑셀**로 산출됐다(H-03 759행·H-04 2338행 조회 후 0행 기입).
    하류 3곳(`field_mapper`·`result_organizer`·`excel_writer`)이 모두 **정확 일치**로
    거르기 때문에, 이름이 하나도 안 맞으면 대상 시트가 0개가 되어 조용히 아무것도 채우지
    않는다. 하나도 못 맞히면 지목이 없었던 것으로 되돌린다.
    """

    def test_unmatched_llm_sheet_is_dropped(self):
        """LLM이 존 이름을 시트명으로 내놓으면 지목을 버린다(전체 시트 대상)."""
        parsed = {"target_sheets": ["은행존"]}
        assert _extract_target_sheets(parsed, "은행존 서버 목록", ["서버정보"]) is None

    def test_matched_sheet_is_kept(self):
        parsed = {"target_sheets": ["성능요약"]}
        assert _extract_target_sheets(parsed, "질의", ["성능요약", "표지"]) == ["성능요약"]

    def test_partially_matched_keeps_only_real_sheets(self):
        parsed = {"target_sheets": ["성능요약", "은행존"]}
        assert _extract_target_sheets(parsed, "질의", ["성능요약", "표지"]) == ["성능요약"]

    def test_regex_fallback_is_validated_too(self):
        """정규식 폴백 산출물도 같은 규칙을 받는다."""
        assert _extract_target_sheets({}, "'서버현황' 시트만 채워줘", ["리소스상태"]) is None
        assert _extract_target_sheets({}, "'서버현황' 시트만 채워줘", ["서버현황"]) == [
            "서버현황"
        ]

    def test_no_available_sheets_keeps_prior_behavior(self):
        """양식 정보가 없으면(업로드 없음) 종전대로 그대로 통과시킨다."""
        parsed = {"target_sheets": ["요약"]}
        assert _extract_target_sheets(parsed, "질의") == ["요약"]
        assert _extract_target_sheets(parsed, "질의", []) == ["요약"]
