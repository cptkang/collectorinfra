"""필드 매퍼 단위 테스트 (LLM mock)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.document.field_mapper import (
    extract_field_names as _extract_field_names,
    _format_schema_columns,
    _validate_mapping,
    map_fields,
)


def _make_schema_info() -> dict:
    """테스트용 스키마 정보를 생성한다."""
    return {
        "tables": {
            "servers": {
                "columns": [
                    {"name": "id", "type": "integer"},
                    {"name": "hostname", "type": "varchar(255)"},
                    {"name": "ip_address", "type": "varchar(50)"},
                    {"name": "status", "type": "varchar(20)"},
                ],
            },
            "cpu_metrics": {
                "columns": [
                    {"name": "server_id", "type": "integer"},
                    {"name": "usage_pct", "type": "float"},
                    {"name": "timestamp", "type": "timestamp"},
                ],
            },
        }
    }


class TestExtractFieldNames:
    """_extract_field_names 함수 테스트."""

    def test_excel_headers(self):
        """Excel 시트 헤더에서 필드명을 추출한다."""
        template = {
            "file_type": "xlsx",
            "sheets": [
                {"headers": ["서버명", "IP주소", "CPU 사용률"]},
            ],
        }
        result = _extract_field_names(template)
        assert result == ["서버명", "IP주소", "CPU 사용률"]

    def test_word_placeholders(self):
        """Word 플레이스홀더에서 필드명을 추출한다."""
        template = {
            "file_type": "docx",
            "placeholders": ["서버명", "날짜"],
            "tables": [
                {"headers": ["이름", "값"]},
            ],
        }
        result = _extract_field_names(template)
        assert "서버명" in result
        assert "날짜" in result
        assert "이름" in result
        assert "값" in result

    def test_deduplication(self):
        """중복 필드명이 제거된다."""
        template = {
            "file_type": "docx",
            "placeholders": ["서버명", "서버명"],
            "tables": [{"headers": ["서버명"]}],
        }
        result = _extract_field_names(template)
        assert result.count("서버명") == 1

    def test_empty_template(self):
        """빈 양식에서 빈 목록을 반환한다."""
        template = {"file_type": "xlsx", "sheets": []}
        result = _extract_field_names(template)
        assert result == []


class TestFormatSchemaColumns:
    """_format_schema_columns 함수 테스트."""

    def test_basic_format(self):
        """스키마 컬럼이 포맷된다."""
        schema = _make_schema_info()
        result = _format_schema_columns(schema)

        assert "servers.hostname" in result
        assert "servers.ip_address" in result
        assert "cpu_metrics.usage_pct" in result

    def test_empty_schema(self):
        """빈 스키마에서 빈 문자열을 반환한다."""
        result = _format_schema_columns({"tables": {}})
        assert result == ""


class TestValidateMapping:
    """_validate_mapping 함수 테스트."""

    def test_valid_mapping(self):
        """유효한 매핑은 보존된다."""
        mapping = {
            "서버명": "servers.hostname",
            "IP주소": "servers.ip_address",
        }
        schema = _make_schema_info()
        field_names = ["서버명", "IP주소"]

        result = _validate_mapping(mapping, schema, field_names)

        assert result["서버명"] == "servers.hostname"
        assert result["IP주소"] == "servers.ip_address"

    def test_invalid_column_removed(self):
        """존재하지 않는 컬럼 참조는 None으로 변경된다."""
        mapping = {
            "서버명": "servers.hostname",
            "가짜컬럼": "servers.nonexistent",
        }
        schema = _make_schema_info()
        field_names = ["서버명", "가짜컬럼"]

        result = _validate_mapping(mapping, schema, field_names)

        assert result["서버명"] == "servers.hostname"
        assert result["가짜컬럼"] is None

    def test_null_mapping_preserved(self):
        """None 매핑은 그대로 유지된다."""
        mapping = {"비고": None}
        schema = _make_schema_info()

        result = _validate_mapping(mapping, schema, ["비고"])
        assert result["비고"] is None


class TestMapFields:
    """map_fields 함수 테스트."""

    @pytest.mark.asyncio
    async def test_successful_mapping(self):
        """LLM이 올바른 매핑을 반환한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({
                "서버명": "servers.hostname",
                "CPU 사용률": "cpu_metrics.usage_pct",
                "비고": None,
            })
        )

        template = {
            "file_type": "xlsx",
            "sheets": [{"headers": ["서버명", "CPU 사용률", "비고"]}],
        }
        schema = _make_schema_info()

        result = await map_fields(mock_llm, template, schema)

        assert result["서버명"] == "servers.hostname"
        assert result["CPU 사용률"] == "cpu_metrics.usage_pct"
        assert result["비고"] is None

    @pytest.mark.asyncio
    async def test_llm_returns_json_in_codeblock(self):
        """LLM이 코드블록으로 감싼 JSON을 반환해도 파싱한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='```json\n{"서버명": "servers.hostname"}\n```'
        )

        template = {
            "file_type": "xlsx",
            "sheets": [{"headers": ["서버명"]}],
        }
        schema = _make_schema_info()

        result = await map_fields(mock_llm, template, schema)
        assert result["서버명"] == "servers.hostname"

    @pytest.mark.asyncio
    async def test_empty_fields(self):
        """양식에 필드가 없으면 빈 딕셔너리를 반환한다."""
        mock_llm = AsyncMock()
        template = {"file_type": "xlsx", "sheets": []}
        schema = _make_schema_info()

        result = await map_fields(mock_llm, template, schema)
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_schema(self):
        """스키마가 비어있으면 모든 필드가 None이다."""
        mock_llm = AsyncMock()
        template = {
            "file_type": "xlsx",
            "sheets": [{"headers": ["서버명"]}],
        }
        schema = {"tables": {}}

        result = await map_fields(mock_llm, template, schema)
        assert result["서버명"] is None
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none_mapping(self):
        """LLM 호출 실패 시 모든 필드가 None인 매핑을 반환한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = Exception("LLM error")

        template = {
            "file_type": "xlsx",
            "sheets": [{"headers": ["서버명"]}],
        }
        schema = _make_schema_info()

        result = await map_fields(mock_llm, template, schema)
        assert result["서버명"] is None

    @pytest.mark.asyncio
    async def test_invalid_json_retries(self):
        """LLM이 잘못된 JSON을 반환하면 재시도한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = [
            MagicMock(content="invalid json"),
            MagicMock(content='{"서버명": "servers.hostname"}'),
        ]

        template = {
            "file_type": "xlsx",
            "sheets": [{"headers": ["서버명"]}],
        }
        schema = _make_schema_info()

        result = await map_fields(mock_llm, template, schema)
        assert result["서버명"] == "servers.hostname"
        assert mock_llm.ainvoke.call_count >= 2
