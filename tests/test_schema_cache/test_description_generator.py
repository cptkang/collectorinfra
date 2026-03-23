"""DescriptionGenerator 단위 테스트.

Mock LLM을 사용하여 설명/유사 단어 생성 로직을 테스트한다.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schema_cache.description_generator import DescriptionGenerator
from src.utils.json_extract import extract_json_from_response


@pytest.fixture
def mock_llm():
    """Mock LLM 인스턴스."""
    llm = AsyncMock()
    return llm


@pytest.fixture
def generator(mock_llm):
    """DescriptionGenerator 인스턴스."""
    return DescriptionGenerator(mock_llm)


@pytest.fixture
def sample_columns():
    """테스트용 컬럼 목록."""
    return [
        {"name": "id", "type": "integer", "primary_key": True},
        {"name": "hostname", "type": "varchar(255)", "nullable": False},
        {"name": "ip_address", "type": "inet"},
        {"name": "os_type", "type": "varchar(50)"},
    ]


@pytest.fixture
def sample_data():
    """테스트용 샘플 데이터."""
    return [
        {"id": 1, "hostname": "web-srv-01", "ip_address": "10.0.1.5", "os_type": "Linux"},
        {"id": 2, "hostname": "db-srv-01", "ip_address": "10.0.1.10", "os_type": "Linux"},
    ]


class TestDescriptionGenerator:
    """DescriptionGenerator 기본 동작 테스트."""

    async def test_generate_for_table_success(
        self, generator, mock_llm, sample_columns, sample_data
    ):
        """테이블별 설명 생성이 성공한다."""
        llm_response = MagicMock()
        llm_response.content = json.dumps({
            "servers.id": {
                "description": "서버 고유 식별자",
                "synonyms": ["서버ID", "ID"],
            },
            "servers.hostname": {
                "description": "서버의 호스트명",
                "synonyms": ["서버명", "호스트명", "서버이름"],
            },
            "servers.ip_address": {
                "description": "서버의 IP 주소",
                "synonyms": ["IP", "아이피", "IP주소"],
            },
            "servers.os_type": {
                "description": "운영체제 종류",
                "synonyms": ["운영체제", "OS", "OS종류"],
            },
        })
        mock_llm.ainvoke = AsyncMock(return_value=llm_response)

        result = await generator.generate_for_table(
            "servers", sample_columns, sample_data
        )

        assert len(result) == 4
        assert "servers.hostname" in result
        assert result["servers.hostname"]["description"] == "서버의 호스트명"
        assert "서버명" in result["servers.hostname"]["synonyms"]

    async def test_generate_for_table_with_code_block(
        self, generator, mock_llm, sample_columns
    ):
        """코드 블록 형태의 LLM 응답도 처리한다."""
        response_json = {
            "servers.hostname": {
                "description": "서버의 호스트명",
                "synonyms": ["서버명"],
            },
        }
        llm_response = MagicMock()
        llm_response.content = f"```json\n{json.dumps(response_json)}\n```"
        mock_llm.ainvoke = AsyncMock(return_value=llm_response)

        result = await generator.generate_for_table("servers", sample_columns)
        assert "servers.hostname" in result

    async def test_generate_for_table_llm_failure(
        self, generator, mock_llm, sample_columns
    ):
        """LLM 호출 실패 시 빈 딕셔너리를 반환한다."""
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM 오류"))

        result = await generator.generate_for_table("servers", sample_columns)
        assert result == {}

    async def test_generate_for_table_parse_failure(
        self, generator, mock_llm, sample_columns
    ):
        """LLM 응답 파싱 실패 시 빈 딕셔너리를 반환한다."""
        llm_response = MagicMock()
        llm_response.content = "이것은 JSON이 아닙니다"
        mock_llm.ainvoke = AsyncMock(return_value=llm_response)

        result = await generator.generate_for_table("servers", sample_columns)
        assert result == {}

    async def test_generate_for_db(self, generator, mock_llm):
        """전체 DB 설명 생성이 성공한다."""
        schema_dict = {
            "tables": {
                "servers": {
                    "columns": [
                        {"name": "hostname", "type": "varchar"},
                    ],
                    "sample_data": [],
                },
                "cpu_metrics": {
                    "columns": [
                        {"name": "usage_pct", "type": "float"},
                    ],
                    "sample_data": [],
                },
            },
        }

        # 각 테이블마다 다른 응답
        responses = [
            MagicMock(content=json.dumps({
                "servers.hostname": {
                    "description": "서버의 호스트명",
                    "synonyms": ["서버명"],
                },
            })),
            MagicMock(content=json.dumps({
                "cpu_metrics.usage_pct": {
                    "description": "CPU 사용률",
                    "synonyms": ["CPU사용률"],
                },
            })),
        ]
        mock_llm.ainvoke = AsyncMock(side_effect=responses)

        descriptions, synonyms = await generator.generate_for_db(schema_dict)

        assert "servers.hostname" in descriptions
        assert "cpu_metrics.usage_pct" in descriptions
        assert "servers.hostname" in synonyms
        assert "cpu_metrics.usage_pct" in synonyms

    async def test_generate_incremental(self, generator, mock_llm):
        """incremental 생성: 기존 설명이 있는 컬럼은 건너뛴다."""
        schema_dict = {
            "tables": {
                "servers": {
                    "columns": [
                        {"name": "hostname", "type": "varchar"},
                        {"name": "new_column", "type": "varchar"},
                    ],
                    "sample_data": [],
                },
            },
        }
        existing = {"servers.hostname": "기존 설명"}

        llm_response = MagicMock()
        llm_response.content = json.dumps({
            "servers.new_column": {
                "description": "새 컬럼 설명",
                "synonyms": ["새컬럼"],
            },
        })
        mock_llm.ainvoke = AsyncMock(return_value=llm_response)

        new_descs, new_syns = await generator.generate_incremental(
            schema_dict, existing
        )

        assert "servers.new_column" in new_descs
        assert "servers.hostname" not in new_descs  # 기존 설명이 있으므로 건너뜀


class TestDescriptionGeneratorDBDescription:
    """DB 설명 생성 테스트."""

    async def test_generate_db_description_success(self, generator, mock_llm):
        """DB 설명 생성이 성공한다."""
        llm_response = MagicMock()
        llm_response.content = "서버 사양, CPU/메모리/디스크 사용량을 관리하는 인프라 모니터링 DB"
        mock_llm.ainvoke = AsyncMock(return_value=llm_response)

        schema_dict = {
            "tables": {
                "servers": {
                    "columns": [
                        {"name": "hostname", "type": "varchar"},
                        {"name": "ip_address", "type": "varchar"},
                    ],
                    "sample_data": [
                        {"hostname": "web-srv-01", "ip_address": "10.0.1.5"},
                    ],
                },
                "cpu_metrics": {
                    "columns": [
                        {"name": "server_id", "type": "integer"},
                        {"name": "usage_pct", "type": "float"},
                    ],
                    "sample_data": [],
                },
            },
        }

        result = await generator.generate_db_description("polestar", schema_dict)
        assert result is not None
        assert "인프라" in result or "모니터링" in result or "서버" in result

    async def test_generate_db_description_with_quotes(self, generator, mock_llm):
        """따옴표로 감싸진 응답도 처리한다."""
        llm_response = MagicMock()
        llm_response.content = '"인프라 모니터링 DB"'
        mock_llm.ainvoke = AsyncMock(return_value=llm_response)

        schema_dict = {
            "tables": {
                "servers": {
                    "columns": [{"name": "hostname", "type": "varchar"}],
                    "sample_data": [],
                },
            },
        }

        result = await generator.generate_db_description("polestar", schema_dict)
        assert result == "인프라 모니터링 DB"

    async def test_generate_db_description_empty_tables(self, generator, mock_llm):
        """테이블이 없으면 None을 반환한다."""
        result = await generator.generate_db_description("empty_db", {"tables": {}})
        assert result is None

    async def test_generate_db_description_llm_failure(self, generator, mock_llm):
        """LLM 실패 시 None을 반환한다."""
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM 오류"))

        schema_dict = {
            "tables": {
                "servers": {
                    "columns": [{"name": "id", "type": "integer"}],
                    "sample_data": [],
                },
            },
        }

        result = await generator.generate_db_description("polestar", schema_dict)
        assert result is None

    async def test_generate_db_description_many_columns_truncated(
        self, generator, mock_llm
    ):
        """10개 이상 컬럼은 잘려서 표시된다."""
        llm_response = MagicMock()
        llm_response.content = "많은 컬럼을 가진 DB"
        mock_llm.ainvoke = AsyncMock(return_value=llm_response)

        columns = [{"name": f"col_{i}", "type": "varchar"} for i in range(15)]
        schema_dict = {
            "tables": {
                "big_table": {
                    "columns": columns,
                    "sample_data": [],
                },
            },
        }

        result = await generator.generate_db_description("big_db", schema_dict)
        assert result is not None
        # LLM이 호출되었는지 확인
        mock_llm.ainvoke.assert_awaited_once()


class TestExtractJson:
    """JSON 추출 유틸리티 테스트."""

    def test_extract_from_code_block(self):
        """코드 블록에서 JSON을 추출한다."""
        content = '```json\n{"key": "value"}\n```'
        result = extract_json_from_response(content)
        assert result == {"key": "value"}

    def test_extract_from_plain_json(self):
        """일반 JSON을 추출한다."""
        content = '{"key": "value"}'
        result = extract_json_from_response(content)
        assert result == {"key": "value"}

    def test_extract_from_text_with_json(self):
        """텍스트 속의 JSON을 추출한다."""
        content = '설명입니다. {"key": "value"} 끝.'
        result = extract_json_from_response(content)
        assert result == {"key": "value"}

    def test_returns_none_for_invalid(self):
        """유효하지 않은 입력에 None을 반환한다."""
        result = extract_json_from_response("이것은 JSON이 아닙니다")
        assert result is None
