"""우선순위 DB 지정 시 타 DB 매핑 제한(개선안 1) 검증 유닛 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.document.field_mapper import (
    MappingResult,
    _apply_synonym_mapping,
    _apply_llm_synonym_discovery,
    _apply_llm_mapping_with_synonyms,
    perform_3step_mapping,
)


@pytest.mark.asyncio
async def test_apply_synonym_mapping_restricts_to_priority_dbs() -> None:
    """_apply_synonym_mapping은 priority_db_ids가 주어졌을 때 해당 DB에 대해서만 synonym 매칭을 수행한다."""
    # 1. synonyms 사전 정의
    all_db_synonyms = {
        "polestar_cm_yd": {
            "cmm_resource.hostname": ["호스트명", "호스트네임"],
        },
        "polestar_cm_gp": {
            "cmm_resource_system.systemname": ["서버 이름", "서버명"],
        }
    }

    # 2. Case A: priority_db_ids가 ['polestar_cm_yd']로 주어졌을 때
    remaining_a = {"호스트네임", "서버 이름"}
    result_a = MappingResult()
    priority_db_ids = ["polestar_cm_yd"]

    _apply_synonym_mapping(
        remaining=remaining_a,
        all_db_synonyms=all_db_synonyms,
        priority_db_ids=priority_db_ids,
        result=result_a,
    )

    # 호스트네임은 yd DB의 synonym이므로 매핑되어야 함
    assert "호스트네임" not in remaining_a
    assert result_a.db_column_mapping["polestar_cm_yd"]["호스트네임"] == "cmm_resource.hostname"

    # 서버 이름은 gp DB의 synonym이나, priority_db_ids 제한으로 인해 매핑되면 안 됨 (remaining에 남아있어야 함)
    assert "서버 이름" in remaining_a
    assert "polestar_cm_gp" not in result_a.db_column_mapping

    # 3. Case B: priority_db_ids가 없을 때 (전체 DB 매칭 허용)
    remaining_b = {"호스트네임", "서버 이름"}
    result_b = MappingResult()

    _apply_synonym_mapping(
        remaining=remaining_b,
        all_db_synonyms=all_db_synonyms,
        priority_db_ids=[],
        result=result_b,
    )

    # 두 필드 모두 매핑되어야 함
    assert "호스트네임" not in remaining_b
    assert "서버 이름" not in remaining_b
    assert result_b.db_column_mapping["polestar_cm_yd"]["호스트네임"] == "cmm_resource.hostname"
    assert result_b.db_column_mapping["polestar_cm_gp"]["서버 이름"] == "cmm_resource_system.systemname"


@pytest.mark.asyncio
async def test_apply_llm_synonym_discovery_restricts_to_priority_dbs() -> None:
    """_apply_llm_synonym_discovery는 priority_db_ids가 주어졌을 때 타 DB로의 매핑 제안을 배제한다."""
    # 1. Mock LLM 설정 (LLM이 yd와 gp 모두에 매핑 결과를 리턴했다고 가정)
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = '{"호스트네임": {"matched_key": "polestar_cm_yd:cmm_resource.hostname"}, "서버 이름": {"matched_key": "polestar_cm_gp:cmm_resource_system.systemname"}}'
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    all_db_synonyms = {
        "polestar_cm_yd": {"cmm_resource.hostname": []},
        "polestar_cm_gp": {"cmm_resource_system.systemname": []},
    }

    remaining = {"호스트네임", "서버 이름"}
    result = MappingResult()
    priority_db_ids = ["polestar_cm_yd"]

    with patch("src.document.field_mapper.FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT", "test"), \
         patch("src.document.field_mapper.FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT", "test {unmapped_fields} {db_columns_with_synonyms} {eav_attributes_with_synonyms}"):
        await _apply_llm_synonym_discovery(
            llm=mock_llm,
            remaining=remaining,
            all_db_synonyms=all_db_synonyms,
            eav_name_synonyms=None,
            priority_db_ids=priority_db_ids,
            result=result,
            cache_manager=None,
        )

    # 호스트네임(yd)은 매핑 완료되어 remaining에서 제외되어야 함
    assert "호스트네임" not in remaining
    assert result.db_column_mapping["polestar_cm_yd"]["호스트네임"] == "cmm_resource.hostname"

    # 서버 이름(gp)은 priority_db_ids(yd) 제한으로 인해 배제되고 remaining에 그대로 남아있어야 함
    assert "서버 이름" in remaining
    assert "polestar_cm_gp" not in result.db_column_mapping


@pytest.mark.asyncio
async def test_apply_llm_mapping_with_synonyms_restricts_to_priority_dbs() -> None:
    """_apply_llm_mapping_with_synonyms는 priority_db_ids가 주어졌을 때 타 DB로의 매핑 제안을 배제한다."""
    # 1. Mock LLM 설정 (Step 3에서 LLM이 gp와 yd 매핑 결과를 리턴했다고 가정)
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = (
        '{"호스트네임": {"db_id": "polestar_cm_yd", "column": "cmm_resource.hostname", "confidence": "high", "reason": "test"}, '
        '"서버 이름": {"db_id": "polestar_cm_gp", "column": "cmm_resource_system.systemname", "confidence": "high", "reason": "test"}}'
    )
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    all_db_descriptions = {
        "polestar_cm_yd": {"cmm_resource.hostname": "desc"},
        "polestar_cm_gp": {"cmm_resource_system.systemname": "desc"},
    }
    all_db_synonyms = {
        "polestar_cm_yd": {},
        "polestar_cm_gp": {},
    }

    remaining_fields = ["호스트네임", "서버 이름"]
    result = MappingResult()
    priority_db_ids = ["polestar_cm_yd"]

    with patch("src.document.field_mapper.FIELD_MAPPER_ENHANCED_SYSTEM_PROMPT", "test"), \
         patch("src.document.field_mapper.FIELD_MAPPER_ENHANCED_USER_PROMPT", "test {field_names} {db_schema_with_synonyms} {eav_context}"):
        await _apply_llm_mapping_with_synonyms(
            llm=mock_llm,
            remaining_fields=remaining_fields,
            all_db_synonyms=all_db_synonyms,
            all_db_descriptions=all_db_descriptions,
            priority_db_ids=priority_db_ids,
            result=result,
        )

    # 호스트네임(yd)은 매핑 등록되어야 함
    assert result.db_column_mapping["polestar_cm_yd"]["호스트네임"] == "cmm_resource.hostname"

    # 서버 이름(gp)은 priority_db_ids 제한으로 인해 배제되고 등록되지 않아야 함
    assert "polestar_cm_gp" not in result.db_column_mapping


@pytest.mark.asyncio
async def test_core_table_priority_mapping() -> None:
    """_apply_synonym_mapping은 핵심 엔터티 테이블의 컬럼을 다른 서브 테이블보다 우선 매칭한다.

    핵심 테이블 집합은 구조 선언(patterns[].entity_table)에서 도출해 주입한다
    (Plan 67 R2 — 공용 계층에 특정 DB 테이블명 하드코딩 금지, D-088).
    """
    # synonyms 사전에 스키마가 붙은 핵심 테이블 polestar.cmm_resource와 서브 테이블 cmm_ad_result가 둘 다 존재
    all_db_synonyms = {
        "polestar_cm_yd": {
            "cmm_ad_result.ip_address": ["IP주소", "아이피"],
            "polestar.cmm_resource.ipaddress": ["IP주소", "IP 주소"],
        }
    }

    remaining = {"IP주소"}
    result = MappingResult()
    priority_db_ids = ["polestar_cm_yd"]

    _apply_synonym_mapping(
        remaining=remaining,
        all_db_synonyms=all_db_synonyms,
        priority_db_ids=priority_db_ids,
        result=result,
        core_tables={"cmm_resource"},
    )

    # cmm_ad_result.ip_address가 알파벳 순서상 앞서고 synonyms에 먼저 기재되어 있지만,
    # cmm_resource가 핵심 엔터티 테이블이므로 polestar.cmm_resource.ipaddress로 매핑되어야 함
    assert "IP주소" not in remaining
    assert result.db_column_mapping["polestar_cm_yd"]["IP주소"] == "polestar.cmm_resource.ipaddress"


@pytest.mark.asyncio
async def test_core_tables_derived_from_structure_declaration() -> None:
    """구조 선언의 EAV entity_table이 핵심 테이블·피벗 게이트 판정의 근거가 된다."""
    from src.document.field_mapper import (
        _core_entity_tables,
        _schema_uses_eav_metric_pivot,
    )

    metas = {
        "polestar_cm_yd": {
            "patterns": [
                {"type": "eav", "entity_table": "cmm_resource", "config_table": "core_config_prop"},
                {"type": "hierarchy"},
            ]
        }
    }
    assert _core_entity_tables(metas) == {"cmm_resource"}
    assert _schema_uses_eav_metric_pivot(metas) is True

    # 평탄 스키마(EAV 선언 없음)는 핵심 테이블 구분도, 사용률 스킵 게이트도 발동하지 않는다.
    flat = {"generic_mon": {"patterns": [{"type": "hierarchy", "table": "servers"}]}}
    assert _core_entity_tables(flat) == set()
    assert _schema_uses_eav_metric_pivot(flat) is False
    assert _core_entity_tables(None) == set()
    assert _schema_uses_eav_metric_pivot(None) is False


@pytest.mark.asyncio
async def test_perform_3step_mapping_eav_priority() -> None:
    """perform_3step_mapping은 EAV 매핑이 일반 테이블 컬럼 매핑보다 높은 우선순위를 가지도록 보장한다."""
    # OS 종류 필드가 일반 테이블(sms_agent_install_file_info.ostype)의 유의어이기도 하고,
    # EAV 속성명(OSType)의 유의어이기도 할 때, EAV가 먼저 적용되어야 한다.
    field_names = ["OS 종류"]
    eav_name_synonyms = {
        "OSType": ["OS 종류", "운영체제"]
    }
    all_db_synonyms = {
        "polestar_cm_yd": {
            "sms_agent_install_file_info.ostype": ["OS 종류"],
        }
    }
    all_db_descriptions = {
        "polestar_cm_yd": {}
    }
    priority_db_ids = ["polestar_cm_yd"]

    # 3-step mapping을 실행 (LLM 호출 없이 2단계와 2.5단계(EAV)선에서 끝남)
    # mock LLM을 전달하지만 실제 LLM 단계까지 가지 않음
    mock_llm = MagicMock()

    result, _ = await perform_3step_mapping(
        llm=mock_llm,
        field_names=field_names,
        field_mapping_hints=[],
        all_db_synonyms=all_db_synonyms,
        all_db_descriptions=all_db_descriptions,
        priority_db_ids=priority_db_ids,
        eav_name_synonyms=eav_name_synonyms,
        active_db_ids=["polestar_cm_yd"],
    )

    # EAV 매핑이 먼저 호출되었으므로 EAV:OSType이 최종 매핑되어야 함
    assert result.column_mapping["OS 종류"] == "EAV:OSType"
    assert result.mapping_sources["OS 종류"] == "eav_synonym"

