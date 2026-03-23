"""field_mapper 노드 단위 테스트."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.document.field_mapper import (
    MappingResult,
    _synonym_match,
    extract_field_names,
    perform_3step_mapping,
)
from src.nodes.field_mapper import field_mapper, _build_pending_registrations
from src.state import create_initial_state


def _make_state(**overrides) -> dict:
    """테스트용 State를 생성한다."""
    state = create_initial_state("서버 정보 조회해줘")
    state.update(overrides)
    return state


# === extract_field_names ===


class TestExtractFieldNames:
    """extract_field_names 함수 테스트."""

    def test_xlsx_headers(self):
        template = {
            "file_type": "xlsx",
            "sheets": [{"headers": ["서버명", "IP주소"]}],
        }
        result = extract_field_names(template)
        assert result == ["서버명", "IP주소"]

    def test_docx_placeholders_and_tables(self):
        template = {
            "file_type": "docx",
            "placeholders": ["서버명"],
            "tables": [{"headers": ["IP", "CPU"]}],
        }
        result = extract_field_names(template)
        assert "서버명" in result
        assert "IP" in result
        assert "CPU" in result

    def test_doc_type(self):
        """doc 타입도 docx와 동일하게 처리."""
        template = {
            "file_type": "doc",
            "placeholders": ["서버명"],
            "tables": [],
        }
        result = extract_field_names(template)
        assert result == ["서버명"]


# === synonym_match ===


class TestSynonymMatch:
    """_synonym_match 함수 테스트."""

    def test_exact_match(self):
        synonyms = {"servers.hostname": ["서버명", "호스트명"]}
        assert _synonym_match("서버명", synonyms) == "servers.hostname"

    def test_case_insensitive(self):
        synonyms = {"servers.hostname": ["HOSTNAME", "서버명"]}
        assert _synonym_match("hostname", synonyms) == "servers.hostname"

    def test_column_name_match(self):
        synonyms = {"servers.hostname": ["호스트"]}
        assert _synonym_match("hostname", synonyms) == "servers.hostname"

    def test_no_match(self):
        synonyms = {"servers.hostname": ["서버명"]}
        assert _synonym_match("비고", synonyms) is None


# === perform_3step_mapping ===


class TestPerform3StepMapping:
    """3단계 매핑 통합 테스트."""

    @pytest.mark.asyncio
    async def test_hint_mapping_priority(self):
        """1단계 힌트가 최우선 적용된다."""
        mock_llm = AsyncMock()

        result = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["서버명", "IP주소"],
            field_mapping_hints=[
                {"field": "서버명", "column": "servers.hostname", "db_id": "polestar"},
            ],
            all_db_synonyms={"polestar": {"servers.ip_address": ["IP주소"]}},
            all_db_descriptions={},
            priority_db_ids=[],
        )

        assert result.column_mapping["서버명"] == "servers.hostname"
        assert result.mapping_sources["서버명"] == "hint"
        assert result.column_mapping["IP주소"] == "servers.ip_address"
        assert result.mapping_sources["IP주소"] == "synonym"
        # LLM은 호출되지 않아야 함
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_synonym_mapping(self):
        """2단계 synonyms 매핑이 동작한다."""
        mock_llm = AsyncMock()

        result = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["서버명", "IP주소"],
            field_mapping_hints=[],
            all_db_synonyms={
                "polestar": {
                    "servers.hostname": ["서버명", "호스트명"],
                    "servers.ip_address": ["IP주소", "아이피"],
                },
            },
            all_db_descriptions={},
            priority_db_ids=[],
        )

        assert result.column_mapping["서버명"] == "servers.hostname"
        assert result.column_mapping["IP주소"] == "servers.ip_address"
        assert result.mapping_sources["서버명"] == "synonym"
        assert result.mapping_sources["IP주소"] == "synonym"
        assert "polestar" in result.mapped_db_ids

    @pytest.mark.asyncio
    async def test_llm_fallback(self):
        """synonyms에 없는 필드는 LLM으로 폴백한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({
                "CPU 사용률": {
                    "db_id": "polestar",
                    "column": "cpu_metrics.usage_pct",
                }
            })
        )

        result = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["서버명", "CPU 사용률"],
            field_mapping_hints=[],
            all_db_synonyms={
                "polestar": {"servers.hostname": ["서버명"]},
            },
            all_db_descriptions={
                "polestar": {
                    "cpu_metrics.usage_pct": "CPU 사용률 (%)",
                },
            },
            priority_db_ids=[],
        )

        assert result.column_mapping["서버명"] == "servers.hostname"
        assert result.mapping_sources["서버명"] == "synonym"
        assert result.column_mapping["CPU 사용률"] == "cpu_metrics.usage_pct"
        assert result.mapping_sources["CPU 사용률"] == "llm_inferred"

    @pytest.mark.asyncio
    async def test_priority_db(self):
        """우선순위 DB의 synonyms가 먼저 검색된다."""
        mock_llm = AsyncMock()

        # 동일 synonym이 두 DB에 존재
        result = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["서버명"],
            field_mapping_hints=[],
            all_db_synonyms={
                "polestar": {"servers.hostname": ["서버명"]},
                "cloud_portal": {"cloud_servers.name": ["서버명"]},
            },
            all_db_descriptions={},
            priority_db_ids=["cloud_portal"],
        )

        # cloud_portal이 우선순위이므로 cloud_portal의 매핑이 선택됨
        assert result.column_mapping["서버명"] == "cloud_servers.name"
        assert "cloud_portal" in result.mapped_db_ids

    @pytest.mark.asyncio
    async def test_multi_db_mapping(self):
        """여러 DB에 걸친 매핑이 동작한다."""
        mock_llm = AsyncMock()

        result = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["서버명", "VM 이름"],
            field_mapping_hints=[],
            all_db_synonyms={
                "polestar": {"servers.hostname": ["서버명"]},
                "cloud_portal": {"vms.vm_name": ["VM 이름"]},
            },
            all_db_descriptions={},
            priority_db_ids=[],
        )

        assert "polestar" in result.mapped_db_ids
        assert "cloud_portal" in result.mapped_db_ids
        assert result.db_column_mapping["polestar"]["서버명"] == "servers.hostname"
        assert result.db_column_mapping["cloud_portal"]["VM 이름"] == "vms.vm_name"

    @pytest.mark.asyncio
    async def test_unmapped_fields_are_none(self):
        """매핑되지 않는 필드는 column_mapping에 None으로 포함된다."""
        mock_llm = AsyncMock()
        # LLM도 매핑 실패
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({"비고": None})
        )

        result = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["비고"],
            field_mapping_hints=[],
            all_db_synonyms={},
            all_db_descriptions={"polestar": {"servers.hostname": "호스트명"}},
            priority_db_ids=[],
        )

        assert result.column_mapping["비고"] is None

    @pytest.mark.asyncio
    async def test_no_redis_graceful_fallback(self):
        """Redis 없이도 LLM 폴백으로 동작한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({
                "서버명": {
                    "db_id": "polestar",
                    "column": "servers.hostname",
                }
            })
        )

        # synonyms 비어있음 (Redis 미존재 시뮬레이션)
        result = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["서버명"],
            field_mapping_hints=[],
            all_db_synonyms={},
            all_db_descriptions={
                "polestar": {"servers.hostname": "서버 호스트명"},
            },
            priority_db_ids=[],
        )

        assert result.column_mapping["서버명"] == "servers.hostname"
        assert result.mapping_sources["서버명"] == "llm_inferred"


# === field_mapper node ===


class TestFieldMapperNode:
    """field_mapper 노드 테스트."""

    @pytest.mark.asyncio
    async def test_skip_without_template(self):
        """template_structure가 없으면 스킵한다."""
        state = _make_state()
        result = await field_mapper(state, llm=AsyncMock(), app_config=MagicMock())
        assert result["current_node"] == "field_mapper"
        assert "column_mapping" not in result

    @pytest.mark.asyncio
    async def test_skip_with_empty_fields(self):
        """양식에 필드가 없으면 스킵한다."""
        state = _make_state(
            template_structure={"file_type": "xlsx", "sheets": []},
        )
        mock_config = MagicMock()
        mock_config.multi_db.get_active_db_ids.return_value = []

        result = await field_mapper(state, llm=AsyncMock(), app_config=mock_config)
        assert result["current_node"] == "field_mapper"

    @pytest.mark.asyncio
    async def test_produces_mapping(self):
        """양식이 있으면 매핑 결과를 반환한다."""
        state = _make_state(
            template_structure={
                "file_type": "xlsx",
                "sheets": [{"headers": ["서버명"]}],
            },
            parsed_requirements={
                "field_mapping_hints": [],
                "target_db_hints": [],
            },
        )

        mock_config = MagicMock()
        mock_config.multi_db.get_active_db_ids.return_value = []

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({"서버명": None})
        )

        with patch("src.nodes.field_mapper._load_db_cache_data") as mock_load:
            mock_load.return_value = ({}, {}, [])
            result = await field_mapper(state, llm=mock_llm, app_config=mock_config)

        assert result["current_node"] == "field_mapper"
        assert "column_mapping" in result


# === build_pending_registrations ===


class TestBuildPendingRegistrations:
    """pending_synonym_registrations 생성 테스트."""

    def test_builds_from_llm_inferred(self):
        mr = MappingResult()
        mr.mapping_sources = {
            "서버명": "synonym",
            "CPU": "llm_inferred",
            "메모리": "llm_inferred",
        }
        mr.db_column_mapping = {
            "polestar": {
                "서버명": "servers.hostname",
                "CPU": "cpu_metrics.usage_pct",
                "메모리": "memory_metrics.total_gb",
            }
        }

        pending = _build_pending_registrations(mr)

        assert len(pending) == 2
        assert pending[0]["field"] == "CPU"
        assert pending[0]["index"] == 1
        assert pending[1]["field"] == "메모리"
        assert pending[1]["index"] == 2

    def test_empty_when_no_inferred(self):
        mr = MappingResult()
        mr.mapping_sources = {"서버명": "synonym"}
        mr.db_column_mapping = {"polestar": {"서버명": "servers.hostname"}}

        pending = _build_pending_registrations(mr)
        assert len(pending) == 0
