"""query_generator 노드 테스트.

SQL 생성, 재시도 로직, 프롬프트 구성을 검증한다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.nodes.query_generator import (
    _build_system_prompt,
    _build_user_prompt,
    _extract_sql_from_response,
    _format_schema_for_prompt,
    query_generator,
)
from src.state import create_initial_state


class TestExtractSqlFromResponse:
    """LLM 응답에서 SQL 추출 검증."""

    def test_sql_code_block(self):
        """```sql ... ``` 패턴에서 SQL을 추출한다."""
        content = "```sql\nSELECT * FROM servers LIMIT 10;\n```"
        result = _extract_sql_from_response(content)
        assert result.startswith("SELECT")
        assert "LIMIT 10" in result

    def test_generic_code_block_with_select(self):
        """``` ... ``` 패턴(SELECT 시작)에서 SQL을 추출한다."""
        content = "```\nSELECT hostname FROM servers LIMIT 5;\n```"
        result = _extract_sql_from_response(content)
        assert "hostname" in result

    def test_plain_select(self):
        """코드 블록 없는 SELECT 문을 추출한다."""
        content = "Here is the query: SELECT id FROM servers LIMIT 10;"
        result = _extract_sql_from_response(content)
        assert "SELECT" in result

    def test_fallback_returns_full_content(self):
        """SQL을 추출할 수 없으면 전체 내용을 반환한다."""
        content = "I cannot generate SQL"
        result = _extract_sql_from_response(content)
        assert result == "I cannot generate SQL"


class TestQueryGeneratorNode:
    """query_generator 노드 전체 동작 검증."""

    @pytest.mark.asyncio
    async def test_basic_sql_generation(self, sample_state):
        """기본 SQL 생성이 동작한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT s.hostname, c.usage_pct FROM servers s JOIN cpu_metrics c ON s.id = c.server_id WHERE c.usage_pct >= 80 LIMIT 100;\n```"
        )
        mock_config = MagicMock()
        mock_config.query.default_limit = 1000

        result = await query_generator(sample_state, llm=mock_llm, app_config=mock_config)

        assert "generated_sql" in result
        assert "SELECT" in result["generated_sql"]
        assert result["error_message"] is None
        assert result["current_node"] == "query_generator"

    @pytest.mark.asyncio
    async def test_retry_increments_count(self, sample_state):
        """재시도 시 retry_count가 증가한다."""
        sample_state["error_message"] = "이전 SQL 검증 실패"
        sample_state["retry_count"] = 1
        sample_state["generated_sql"] = "SELECT bad_sql"

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT hostname FROM servers LIMIT 10;\n```"
        )
        mock_config = MagicMock()
        mock_config.query.default_limit = 1000

        result = await query_generator(sample_state, llm=mock_llm, app_config=mock_config)

        assert result["retry_count"] == 2  # 1 -> 2
        assert result["error_message"] is None  # 에러 초기화

    @pytest.mark.asyncio
    async def test_first_call_does_not_increment(self, sample_state):
        """첫 호출(에러 없음)에서는 retry_count를 증가시키지 않는다."""
        sample_state["error_message"] = None
        sample_state["retry_count"] = 0

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT * FROM servers LIMIT 10;\n```"
        )
        mock_config = MagicMock()
        mock_config.query.default_limit = 1000

        result = await query_generator(sample_state, llm=mock_llm, app_config=mock_config)

        assert result["retry_count"] == 0


class TestBuildUserPrompt:
    """사용자 프롬프트 구성 검증."""

    def test_includes_original_query(self):
        """원본 질의가 프롬프트에 포함된다."""
        prompt = _build_user_prompt(
            parsed_requirements={"original_query": "CPU 정보 알려줘"},
            template_structure=None,
            error_message=None,
            previous_sql=None,
        )
        assert "CPU 정보 알려줘" in prompt

    def test_includes_retry_context(self):
        """재시도 시 이전 에러와 SQL이 프롬프트에 포함된다."""
        prompt = _build_user_prompt(
            parsed_requirements={"original_query": "test"},
            template_structure=None,
            error_message="존재하지 않는 테이블 참조",
            previous_sql="SELECT * FROM bad_table",
        )
        assert "존재하지 않는 테이블" in prompt
        assert "bad_table" in prompt

    def test_includes_template_structure(self):
        """양식 구조가 프롬프트에 포함된다."""
        prompt = _build_user_prompt(
            parsed_requirements={"original_query": "test"},
            template_structure={"sheets": [{"headers": ["서버명", "IP"]}]},
            error_message=None,
            previous_sql=None,
        )
        assert "양식" in prompt
        assert "서버명" in prompt


class TestFormatSchemaForPrompt:
    """스키마 포맷팅 검증."""

    def test_empty_schema(self):
        """빈 스키마도 에러 없이 처리한다."""
        result = _format_schema_for_prompt({})
        assert isinstance(result, str)

    def test_schema_includes_table_info(self, sample_schema_info):
        """스키마 텍스트에 테이블 정보가 포함된다."""
        result = _format_schema_for_prompt(sample_schema_info)
        assert "servers" in result
        assert "hostname" in result
        assert "cpu_metrics" in result

    def test_schema_includes_fk_info(self, sample_schema_info):
        """FK 관계가 포함된다."""
        result = _format_schema_for_prompt(sample_schema_info)
        assert "cpu_metrics.server_id" in result
        assert "servers.id" in result
