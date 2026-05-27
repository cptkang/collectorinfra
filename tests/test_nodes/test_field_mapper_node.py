"""field_mapper 노드 단위 테스트."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.document.field_mapper import (
    MappingResult,
    _resolve_fallback_db_id,
    _synonym_match,
    _apply_eav_synonym_mapping,
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

        result, _llm_details = await perform_3step_mapping(
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

        result, _llm_details = await perform_3step_mapping(
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
                    "confidence": "high",
                }
            })
        )

        result, _llm_details = await perform_3step_mapping(
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
        result, _llm_details = await perform_3step_mapping(
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

        result, _llm_details = await perform_3step_mapping(
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

        result, _llm_details = await perform_3step_mapping(
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
                    "confidence": "high",
                }
            })
        )

        # synonyms 비어있음 (Redis 미존재 시뮬레이션)
        result, _llm_details = await perform_3step_mapping(
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
            mock_load.return_value = ({}, {}, [], {}, {}, None)
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


# === _resolve_fallback_db_id 및 "unknown" 제거 검증 ===


class TestResolveFallbackDbId:
    """EAV 폴백 DB ID 결정 로직을 검증한다."""

    def test_priority_first(self):
        """priority_db_ids가 있으면 첫 번째를 반환한다."""
        assert _resolve_fallback_db_id(
            ["polestar", "cloud_portal"], ["itsm"], {}
        ) == "polestar"

    def test_active_fallback(self):
        """priority가 비어있으면 active_db_ids[0]을 반환한다."""
        assert _resolve_fallback_db_id(
            [], ["polestar", "cloud_portal"], {}
        ) == "polestar"

    def test_synonyms_key_fallback(self):
        """priority와 active 모두 없으면 synonyms 키를 사용한다."""
        assert _resolve_fallback_db_id(
            [], None, {"cloud_portal": {"t.col": ["word"]}}
        ) == "cloud_portal"

    def test_default_fallback(self):
        """모두 없으면 '_default'를 반환한다."""
        assert _resolve_fallback_db_id([], None, {}) == "_default"

    def test_empty_active_uses_synonyms(self):
        """active_db_ids가 빈 리스트이면 synonyms 키를 사용한다."""
        assert _resolve_fallback_db_id(
            [], [], {"itsm": {}}
        ) == "itsm"


class TestNoUnknownDbId:
    """mapped_db_ids에 'unknown'이 포함되지 않음을 검증한다."""

    @pytest.mark.asyncio
    async def test_eav_mapping_uses_active_db_id(self):
        """priority_db_ids가 빈 리스트일 때 EAV 매핑은 active_db_ids[0]을 사용한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="{}")

        result, _ = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["OS유형"],
            field_mapping_hints=[],
            all_db_synonyms={},
            all_db_descriptions={},
            priority_db_ids=[],
            eav_name_synonyms={"OSType": ["OS유형", "운영체제"]},
            active_db_ids=["polestar"],
        )

        # "unknown"이 아닌 "polestar"가 DB ID로 사용됨
        assert "unknown" not in result.db_column_mapping
        if result.db_column_mapping:
            assert "polestar" in result.db_column_mapping

    @pytest.mark.asyncio
    async def test_mapped_db_ids_never_contains_unknown(self):
        """mapped_db_ids에 'unknown'이 절대 포함되지 않는다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="{}")

        result, _ = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["OS유형"],
            field_mapping_hints=[],
            all_db_synonyms={"polestar": {"config.os_type": ["OS유형"]}},
            all_db_descriptions={},
            priority_db_ids=[],
            eav_name_synonyms=None,
            active_db_ids=["polestar"],
        )

        assert "unknown" not in result.mapped_db_ids

    @pytest.mark.asyncio
    async def test_eav_no_active_db_uses_default(self):
        """active_db_ids도 없으면 '_default'를 사용한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="{}")

        result, _ = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["OS유형"],
            field_mapping_hints=[],
            all_db_synonyms={},
            all_db_descriptions={},
            priority_db_ids=[],
            eav_name_synonyms={"OSType": ["OS유형"]},
            active_db_ids=[],
        )

        assert "unknown" not in result.db_column_mapping
        if result.db_column_mapping:
            assert "_default" in result.db_column_mapping


class TestResolvePriorityDbIds:
    """_resolve_priority_db_ids 함수 테스트."""

    def test_empty_hints_returns_empty(self):
        from src.nodes.field_mapper import _resolve_priority_db_ids
        assert _resolve_priority_db_ids([], ["polestar", "cloud_portal"]) == []
        assert _resolve_priority_db_ids(None, ["polestar", "cloud_portal"]) == []

    def test_raw_db_id_match(self):
        from src.nodes.field_mapper import _resolve_priority_db_ids
        # 대소문자 구분 없이 매칭 확인
        assert _resolve_priority_db_ids(["Polestar"], ["polestar", "cloud_portal"]) == ["polestar"]
        assert _resolve_priority_db_ids(["polestar"], ["polestar", "cloud_portal"]) == ["polestar"]

    def test_alias_exact_match(self):
        from src.nodes.field_mapper import _resolve_priority_db_ids
        # "폴스타" -> "polestar"
        assert _resolve_priority_db_ids(["폴스타"], ["polestar", "cloud_portal"]) == ["polestar"]
        # "클라우드 포탈" -> "cloud_portal"
        assert _resolve_priority_db_ids(["클라우드 포탈"], ["polestar", "cloud_portal"]) == ["cloud_portal"]

    def test_alias_substring_match(self):
        from src.nodes.field_mapper import _resolve_priority_db_ids
        # "여의도 폴스타" -> "polestar_cm_yd" ("공동존 여의도 폴스타" 의 부분 문자열)
        active_dbs = ["polestar", "polestar_cm_yd", "cloud_portal"]
        assert _resolve_priority_db_ids(["여의도 폴스타"], active_dbs) == ["polestar_cm_yd"]

    def test_multiple_hints_match(self):
        from src.nodes.field_mapper import _resolve_priority_db_ids
        active_dbs = ["polestar", "polestar_cm_yd", "cloud_portal", "itsm"]
        assert _resolve_priority_db_ids(["여의도 폴스타", "itsm"], active_dbs) == ["polestar_cm_yd", "itsm"]


class TestSpaceInsensitiveSynonymMatch:
    """공백 무시 동의어 매칭 단위 테스트."""

    def test_space_insensitive_match_synonyms(self):
        # field에 공백이 있고 synonym에 공백이 없는 경우
        synonyms = {"cmm_resource.logicalcore": ["CPU코어", "논리코어"]}
        assert _synonym_match("CPU 코어", synonyms) == "cmm_resource.logicalcore"

        # field에 공백이 없고 synonym에 공백이 있는 경우
        synonyms_with_space = {"cmm_resource.logicalcore": ["CPU 코어", "논리 코어"]}
        assert _synonym_match("CPU코어", synonyms_with_space) == "cmm_resource.logicalcore"

    def test_space_insensitive_match_column(self):
        synonyms = {"cmm_resource.logicalcore": ["논리코어"]}
        # 컬럼명 자체(logicalcore)와 공백/밑줄 무시 매칭 시도
        assert _synonym_match("logical core", synonyms) == "cmm_resource.logicalcore"


class TestSpaceInsensitiveEavSynonymMapping:
    """EAV 공백 무시 동의어 매칭 단위 테스트."""

    @pytest.mark.asyncio
    async def test_eav_space_insensitive(self):
        mr = MappingResult()
        remaining = {"메모리 용량"}
        
        # eav_name_synonyms 에는 "메모리용량"으로 등록되어 있음
        eav_name_synonyms = {"TotalSize": ["메모리용량", "메모리크기"]}
        
        _apply_eav_synonym_mapping(
            remaining=remaining,
            eav_name_synonyms=eav_name_synonyms,
            result=mr,
            eav_db_id="polestar_cm_yd"
        )
        
        assert "메모리 용량" not in remaining
        assert mr.db_column_mapping["polestar_cm_yd"]["메모리 용량"] == "EAV:TotalSize"
        assert mr.mapping_sources["메모리 용량"] == "eav_synonym"


class TestLocalYamlFallback:
    """로컬 YAML 폴백 로직 테스트."""

    def test_load_local_yaml_fallback(self):
        from src.nodes.field_mapper import _load_local_yaml_fallback
        
        all_syns, eav_syns, global_syns = _load_local_yaml_fallback(["polestar_cm_yd"])
        
        # global_synonyms 가 정상 로드되었는지 확인
        assert "HOSTNAME" in global_syns
        assert "호스트네임" in global_syns["HOSTNAME"]
        assert "NAME" in global_syns
        assert "서버 이름" in global_syns["NAME"]
        
        # DB 프로필 (polestar_cm_yd) known_attributes 가 로드되었는지 확인
        assert "OSType" in eav_syns
        assert "운영체제" in eav_syns["OSType"]
        
        # all_syns에 allowed_tables의 가상 컬럼 synonyms가 생성되었는지 확인
        assert "polestar_cm_yd" in all_syns
        assert "cmm_resource.hostname" in all_syns["polestar_cm_yd"]
        assert "호스트네임" in all_syns["polestar_cm_yd"]["cmm_resource.hostname"]


class TestServerNameVsHostname:
    """서버 이름과 호스트네임의 명확한 구분 매핑 테스트."""

    @pytest.mark.asyncio
    async def test_server_name_maps_to_name(self):
        mock_llm = AsyncMock()
        
        # 로컬 폴백을 통한 synonyms를 직접 주입받는 상황 시뮬레이션
        from src.nodes.field_mapper import _load_local_yaml_fallback
        all_syns, eav_syns, global_syns = _load_local_yaml_fallback(["polestar_cm_yd"])
        
        result, _ = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["서버 이름", "호스트네임"],
            field_mapping_hints=[],
            all_db_synonyms=all_syns,
            all_db_descriptions={},
            priority_db_ids=["polestar_cm_yd"],
            eav_name_synonyms=eav_syns,
            global_synonyms=global_syns
        )
        
        # "서버 이름" -> cmm_resource.name 으로 매핑되어야 함
        assert result.column_mapping["서버 이름"] == "cmm_resource.name"
        assert result.mapping_sources["서버 이름"] == "synonym"
        
        # "호스트네임" -> cmm_resource.hostname 또는 EAV:Hostname 으로 매핑되어야 함
        # priority_db_ids에 polestar_cm_yd가 있으므로 EAV:Hostname 혹은 cmm_resource.hostname 중 적절한 곳으로 매핑됨
        # global_syns["HOSTNAME"] 에 "호스트네임"이 있으므로 all_syns["polestar_cm_yd"]["cmm_resource.hostname"] 에 호스트네임이 등록되어 synonym 매핑됨
        assert result.column_mapping["호스트네임"] == "cmm_resource.hostname"
        assert result.mapping_sources["호스트네임"] == "synonym"


