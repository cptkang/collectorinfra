"""output_generator 노드 테스트.

자연어 응답 생성, 빈 결과 처리, 파일 출력 분기를 검증한다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.nodes.output_generator import (
    _build_response_prompt,
    _generate_empty_result_response,
    output_generator,
)
from src.state import create_initial_state


class TestGenerateEmptyResultResponse:
    """빈 결과 응답 생성 검증."""

    def test_basic_empty_response(self):
        """기본 빈 결과 응답을 생성한다."""
        parsed = {"query_targets": ["서버", "CPU"], "filter_conditions": []}
        response = _generate_empty_result_response(parsed)
        assert "서버" in response
        assert "CPU" in response
        assert "데이터가 없습니다" in response

    def test_empty_with_filters_suggests_alternatives(self):
        """필터가 있는 빈 결과는 대안을 제안한다."""
        parsed = {
            "query_targets": ["서버"],
            "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 99}],
        }
        response = _generate_empty_result_response(parsed)
        assert "완화" in response

    def test_empty_with_time_range_suggests_expansion(self):
        """시간 범위가 있는 빈 결과는 시간 범위 확대를 제안한다."""
        parsed = {
            "query_targets": ["서버"],
            "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 99}],
            "time_range": {"start": "2026-03-15", "end": "2026-03-15"},
        }
        response = _generate_empty_result_response(parsed)
        assert "시간 범위" in response


class TestBuildResponsePrompt:
    """응답 생성 프롬프트 구성 검증."""

    def test_includes_all_sections(self):
        """프롬프트에 모든 섹션이 포함된다."""
        prompt = _build_response_prompt(
            original_query="서버 목록",
            summary="3건 조회",
            rows=[{"hostname": "web-01"}],
            sql="SELECT * FROM servers LIMIT 10",
        )
        assert "서버 목록" in prompt
        assert "3건 조회" in prompt
        assert "SELECT" in prompt
        assert "web-01" in prompt

    def test_truncates_large_result(self):
        """결과가 20건을 초과하면 상위 20건만 표시한다."""
        rows = [{"id": i} for i in range(50)]
        prompt = _build_response_prompt(
            original_query="test",
            summary="50건",
            rows=rows,
            sql="SELECT * FROM t",
        )
        assert "상위 20건" in prompt


class TestOutputGeneratorNode:
    """output_generator 노드 전체 동작 검증."""

    @pytest.mark.asyncio
    async def test_text_output(self):
        """텍스트 응답을 생성한다."""
        state = create_initial_state(user_query="서버 목록")
        state["organized_data"] = {
            "summary": "3건 조회",
            "rows": [{"hostname": "web-01"}],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "text",
            "original_query": "서버 목록",
        }
        state["generated_sql"] = "SELECT * FROM servers LIMIT 10"

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="서버 목록 조회 결과입니다. 총 1건의 서버가 있습니다."
        )

        result = await output_generator(state, llm=mock_llm, app_config=MagicMock())

        assert result["final_response"] != ""
        assert result["output_file"] is None
        assert result["current_node"] == "output_generator"
        assert result["error_message"] is None

    @pytest.mark.asyncio
    async def test_empty_results_response(self):
        """빈 결과에 대한 응답을 생성한다."""
        state = create_initial_state(user_query="test")
        state["organized_data"] = {
            "summary": "",
            "rows": [],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "text",
            "filter_conditions": [],
        }
        state["generated_sql"] = "SELECT * FROM servers WHERE 1=0"

        result = await output_generator(state, app_config=MagicMock())

        assert "데이터가 없습니다" in result["final_response"]

    @pytest.mark.asyncio
    async def test_xlsx_output_fallback_without_template(self):
        """양식/매핑 없이 Excel 출력 시 텍스트 응답으로 폴백한다."""
        state = create_initial_state(user_query="test")
        state["organized_data"] = {
            "summary": "3건",
            "rows": [{"hostname": "web-01"}],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "xlsx",
            "original_query": "서버 목록 엑셀로",
        }
        state["generated_sql"] = "SELECT * FROM servers LIMIT 10"

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="텍스트 응답")

        result = await output_generator(state, llm=mock_llm, app_config=MagicMock())

        assert "텍스트 응답으로 대체" in result["final_response"]
        assert result["output_file"] is None

    @pytest.mark.asyncio
    async def test_unsupported_format(self):
        """지원하지 않는 출력 형식에 대한 안내를 반환한다."""
        state = create_initial_state(user_query="test")
        state["organized_data"] = {
            "summary": "",
            "rows": [],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "pdf",
        }
        state["generated_sql"] = ""

        result = await output_generator(state, app_config=MagicMock())

        assert "지원하지 않는" in result["final_response"]
