"""input_parser 노드 테스트.

자연어 파싱, JSON 추출, 기본값 설정, 에러 처리를 검증한다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.nodes.input_parser import (
    _parse_uploaded_file,
    input_parser,
)
from src.utils.json_extract import extract_json_from_response
from src.state import create_initial_state


class TestExtractJsonFromResponse:
    """LLM 응답에서 JSON 추출 검증."""

    def test_json_code_block(self):
        """```json ... ``` 패턴에서 JSON을 추출한다."""
        content = '```json\n{"query_targets": ["서버"]}\n```'
        result = extract_json_from_response(content)
        assert result["query_targets"] == ["서버"]

    def test_plain_json(self):
        """순수 JSON 응답을 파싱한다."""
        content = '{"query_targets": ["CPU"], "output_format": "text"}'
        result = extract_json_from_response(content)
        assert result["query_targets"] == ["CPU"]

    def test_json_with_surrounding_text(self):
        """텍스트로 둘러싸인 JSON을 추출한다."""
        content = 'Here is the result:\n{"query_targets": ["메모리"]}\nEnd.'
        result = extract_json_from_response(content)
        assert result["query_targets"] == ["메모리"]

    def test_invalid_json_returns_none(self):
        """유효하지 않은 JSON은 None을 반환한다."""
        result = extract_json_from_response("This is not JSON at all")
        assert result is None

    def test_code_block_without_json_tag(self):
        """```로만 감싼 JSON을 처리한다."""
        content = '```\n{"query_targets": ["디스크"]}\n```'
        result = extract_json_from_response(content)
        assert result.get("query_targets") == ["디스크"]


class TestInputParserNode:
    """input_parser 노드 전체 동작 검증."""

    @pytest.mark.asyncio
    async def test_basic_natural_language_parsing(self):
        """기본 자연어 질의 파싱이 동작한다."""
        state = create_initial_state(user_query="CPU 사용률이 80% 이상인 서버")

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"query_targets": ["서버", "CPU"], "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 80}], "output_format": "text"}'
        )

        result = await input_parser(state, llm=mock_llm, app_config=MagicMock())

        assert "parsed_requirements" in result
        parsed = result["parsed_requirements"]
        assert "서버" in parsed["query_targets"]
        assert "CPU" in parsed["query_targets"]
        assert result["current_node"] == "input_parser"
        assert result["error_message"] is None

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self):
        """LLM 호출 실패 시 최소 파싱 결과로 진행한다."""
        state = create_initial_state(user_query="서버 목록 조회")

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = Exception("LLM 에러")

        result = await input_parser(state, llm=mock_llm, app_config=MagicMock())

        # 에러 시에도 그래프가 중단되지 않도록 최소 결과를 반환
        assert "parsed_requirements" in result
        parsed = result["parsed_requirements"]
        assert parsed["original_query"] == "서버 목록 조회"
        assert parsed["output_format"] == "text"

    @pytest.mark.asyncio
    async def test_default_values_set(self):
        """파싱 결과에 기본값이 설정된다."""
        state = create_initial_state(user_query="서버 정보 알려줘")

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"query_targets": ["서버"]}'
        )

        result = await input_parser(state, llm=mock_llm, app_config=MagicMock())

        parsed = result["parsed_requirements"]
        assert parsed.get("output_format") == "text"
        assert parsed.get("filter_conditions") == []
        assert parsed.get("time_range") is None
        assert parsed.get("aggregation") is None
        assert parsed.get("limit") is None

    @pytest.mark.asyncio
    async def test_template_structure_none_without_file(self):
        """파일 업로드 없이 template_structure는 None이다."""
        state = create_initial_state(user_query="test")

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"query_targets": ["서버"]}'
        )

        result = await input_parser(state, llm=mock_llm, app_config=MagicMock())
        assert result["template_structure"] is None


class TestParseUploadedFile:
    """양식 파일 파싱 검증 (Phase 2 스텁)."""

    def test_unsupported_file_type(self):
        """지원하지 않는 파일 형식은 None을 반환한다."""
        result = _parse_uploaded_file(b"fake", "pdf")
        assert result is None

    def test_xlsx_without_parser(self):
        """Excel 파서 미구현 시 None을 반환한다."""
        result = _parse_uploaded_file(b"fake", "xlsx")
        # Phase 2에서 구현 예정이므로 None (ImportError 처리)
        assert result is None

    def test_docx_without_parser(self):
        """Word 파서 미구현 시 None을 반환한다."""
        result = _parse_uploaded_file(b"fake", "docx")
        assert result is None
