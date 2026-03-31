"""Plan 31: 필드 매핑 실패 원인 해결 검증 테스트.

검증 대상:
- 해결 1: 글로벌 유사어 통합 로드 (nodes/field_mapper.py)
- 해결 2+4: LLM 유사어 발견 단계 Step 2.8 (document/field_mapper.py, prompts/field_mapper.py)
- 해결 3: EAV 피벗 쿼리 분리 (nodes/multi_db_executor.py)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.document.field_mapper import (
    MappingResult,
    _apply_llm_synonym_discovery,
    _register_llm_synonym_discoveries_to_redis,
    perform_3step_mapping,
)
from src.prompts.field_mapper import (
    FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT,
    FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT,
)


# === Helper ===


def _make_cache_manager(redis_available: bool = True) -> MagicMock:
    """테스트용 cache_manager mock을 생성한다."""
    cm = MagicMock()
    cm.redis_available = redis_available
    cm.add_synonyms = AsyncMock(return_value=True)
    cm.add_global_synonym = AsyncMock(return_value=True)

    redis_cache = MagicMock()
    redis_cache.load_eav_name_synonyms = AsyncMock(return_value={})
    redis_cache.save_eav_name_synonyms = AsyncMock(return_value=None)
    cm._redis_cache = redis_cache

    return cm


# === 해결 1: 글로벌 유사어 통합 로드 검증 ===


class TestGlobalSynonymFallback:
    """해결 1: field_mapper가 load_synonyms_with_global_fallback()을 호출하는지 검증."""

    @pytest.mark.asyncio
    async def test_load_db_cache_data_calls_global_fallback(self) -> None:
        """_load_db_cache_data()가 get_synonyms() 대신
        load_synonyms_with_global_fallback()을 호출한다."""
        from src.nodes.field_mapper import _load_db_cache_data

        mock_config = MagicMock()
        cm = _make_cache_manager()
        cm.load_synonyms_with_global_fallback = AsyncMock(
            return_value={"CMM_RESOURCE.HOSTNAME": ["호스트명", "서버명"]}
        )
        cm.get_descriptions = AsyncMock(return_value={})

        with patch(
            "src.schema_cache.cache_manager.get_cache_manager",
            return_value=cm,
        ):
            result = await _load_db_cache_data(
                mock_config, ["polestar"], []
            )

        all_synonyms = result[0]
        assert "polestar" in all_synonyms
        cm.load_synonyms_with_global_fallback.assert_called_once_with("polestar")


# === 해결 2+4: LLM 유사어 발견 단계 (Step 2.8) 검증 ===


class TestLlmSynonymDiscoveryPrompts:
    """Step 2.8 프롬프트 상수 검증."""

    def test_system_prompt_exists(self) -> None:
        """FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT가 정의되어 있다."""
        assert FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT
        assert "미매핑" in FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT or \
               "매칭" in FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT

    def test_user_prompt_has_placeholders(self) -> None:
        """FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT에 필수 placeholder가 있다."""
        assert "{unmapped_fields}" in FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT
        assert "{db_columns_with_synonyms}" in FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT
        assert "{eav_attributes_with_synonyms}" in FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT

    def test_system_prompt_mentions_synonym_mapping(self) -> None:
        """시스템 프롬프트에 유의어 기반 매핑 지침이 포함되어 있다."""
        prompt = FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT
        # 유의어/Synonyms 기반 매핑 규칙 언급
        assert "유의어" in prompt or "Synonyms" in prompt
        assert "null" in prompt  # 매핑 불가 시 null 반환 규칙

    def test_system_prompt_mentions_eav(self) -> None:
        """시스템 프롬프트에 EAV 속성 매핑 지침이 있다."""
        prompt = FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT
        assert "EAV" in prompt

    def test_output_format_specifies_matched_key(self) -> None:
        """출력 형식에 matched_key가 포함되어 있다."""
        prompt = FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT
        assert "matched_key" in prompt


class TestApplyLlmSynonymDiscovery:
    """_apply_llm_synonym_discovery() 함수 검증."""

    @pytest.mark.asyncio
    async def test_sends_column_names_with_synonyms(self) -> None:
        """DB 컬럼명과 synonym words를 함께 전달한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({})
        )

        remaining = {"필드A", "필드B"}
        all_db_synonyms = {
            "polestar": {
                "CMM_RESOURCE.HOSTNAME": ["호스트명", "서버명"],
                "CMM_RESOURCE.IPADDRESS": ["IP주소", "아이피"],
            }
        }

        await _apply_llm_synonym_discovery(
            llm=mock_llm,
            remaining=remaining,
            all_db_synonyms=all_db_synonyms,
            eav_name_synonyms=None,
            priority_db_ids=["polestar"],
            result=MappingResult(),
            cache_manager=None,
        )

        # LLM에 전달된 메시지 검사
        call_args = mock_llm.ainvoke.call_args[0][0]
        user_msg = call_args[1].content
        # 컬럼명은 포함 (db_id:table.column 형식)
        assert "polestar:CMM_RESOURCE.HOSTNAME" in user_msg
        assert "polestar:CMM_RESOURCE.IPADDRESS" in user_msg
        # synonym words도 포함됨 (유의어 목록)
        assert "호스트명" in user_msg
        assert "서버명" in user_msg
        assert "아이피" in user_msg
        assert "IP주소" in user_msg

    @pytest.mark.asyncio
    async def test_sends_eav_names_with_synonyms(self) -> None:
        """EAV 속성명과 synonym words를 함께 전달한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({})
        )

        remaining = {"S/N(Serial Number)"}
        eav_name_synonyms = {
            "SerialNumber": ["시리얼 번호", "S/N"],
            "Vendor": ["제조사", "벤더"],
        }

        await _apply_llm_synonym_discovery(
            llm=mock_llm,
            remaining=remaining,
            all_db_synonyms={},
            eav_name_synonyms=eav_name_synonyms,
            priority_db_ids=[],
            result=MappingResult(),
            cache_manager=None,
        )

        call_args = mock_llm.ainvoke.call_args[0][0]
        user_msg = call_args[1].content
        # EAV 속성명은 포함 (EAV:속성명 형식)
        assert "EAV:SerialNumber" in user_msg
        assert "EAV:Vendor" in user_msg
        # EAV synonym words도 포함됨
        assert "시리얼 번호" in user_msg
        assert "벤더" in user_msg

    @pytest.mark.asyncio
    async def test_single_llm_call(self) -> None:
        """LLM 호출이 정확히 1회만 수행된다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({})
        )

        remaining = {"필드1", "필드2", "필드3"}

        await _apply_llm_synonym_discovery(
            llm=mock_llm,
            remaining=remaining,
            all_db_synonyms={"db1": {"t.c1": ["w1"], "t.c2": ["w2"]}},
            eav_name_synonyms={"EAV1": ["w3"]},
            priority_db_ids=[],
            result=MappingResult(),
            cache_manager=None,
        )

        assert mock_llm.ainvoke.call_count == 1

    @pytest.mark.asyncio
    async def test_column_mapping_applied(self) -> None:
        """LLM이 db_id:table.column 형식으로 매칭하면 매핑이 적용된다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({
                "자산명/호스트명": {
                    "matched_key": "polestar:CMM_RESOURCE.HOSTNAME",
                    "reason": "호스트명 부분 매칭",
                },
                "IP": {
                    "matched_key": "polestar:CMM_RESOURCE.IPADDRESS",
                    "reason": "IP는 IPADDRESS의 약어",
                },
            })
        )

        remaining = {"자산명/호스트명", "IP"}
        result = MappingResult()

        await _apply_llm_synonym_discovery(
            llm=mock_llm,
            remaining=remaining,
            all_db_synonyms={"polestar": {"CMM_RESOURCE.HOSTNAME": [], "CMM_RESOURCE.IPADDRESS": []}},
            eav_name_synonyms=None,
            priority_db_ids=["polestar"],
            result=result,
            cache_manager=None,
        )

        assert result.db_column_mapping["polestar"]["자산명/호스트명"] == "CMM_RESOURCE.HOSTNAME"
        assert result.db_column_mapping["polestar"]["IP"] == "CMM_RESOURCE.IPADDRESS"
        assert result.mapping_sources["자산명/호스트명"] == "llm_synonym"
        assert result.mapping_sources["IP"] == "llm_synonym"
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_eav_mapping_applied(self) -> None:
        """LLM이 EAV:속성명 형식으로 매칭하면 EAV 매핑이 적용된다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({
                "S/N(Serial Number)": {
                    "matched_key": "EAV:SerialNumber",
                    "reason": "Serial Number가 SerialNumber에 대응",
                },
            })
        )

        remaining = {"S/N(Serial Number)"}
        result = MappingResult()

        await _apply_llm_synonym_discovery(
            llm=mock_llm,
            remaining=remaining,
            all_db_synonyms={},
            eav_name_synonyms={"SerialNumber": []},
            priority_db_ids=["polestar"],
            result=result,
            cache_manager=None,
        )

        assert result.db_column_mapping["polestar"]["S/N(Serial Number)"] == "EAV:SerialNumber"
        assert result.mapping_sources["S/N(Serial Number)"] == "llm_synonym"
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_mapping_source_is_llm_synonym(self) -> None:
        """Step 2.8 매핑의 mapping_sources 값은 'llm_synonym'이다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({
                "설명": {
                    "matched_key": "polestar:CMM_RESOURCE.DESCRIPTION",
                    "reason": "설명 = DESCRIPTION",
                },
            })
        )

        remaining = {"설명"}
        result = MappingResult()

        await _apply_llm_synonym_discovery(
            llm=mock_llm,
            remaining=remaining,
            all_db_synonyms={"polestar": {"CMM_RESOURCE.DESCRIPTION": []}},
            eav_name_synonyms=None,
            priority_db_ids=["polestar"],
            result=result,
            cache_manager=None,
        )

        assert result.mapping_sources["설명"] == "llm_synonym"

    @pytest.mark.asyncio
    async def test_null_mapping_ignored(self) -> None:
        """LLM이 null을 반환한 필드는 매핑되지 않는다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({
                "비고": None,
            })
        )

        remaining = {"비고"}
        result = MappingResult()

        await _apply_llm_synonym_discovery(
            llm=mock_llm,
            remaining=remaining,
            all_db_synonyms={"db1": {"t.c": []}},
            eav_name_synonyms=None,
            priority_db_ids=[],
            result=result,
            cache_manager=None,
        )

        assert "비고" not in result.mapping_sources
        assert "비고" in remaining

    @pytest.mark.asyncio
    async def test_llm_failure_graceful(self) -> None:
        """LLM 호출 실패 시 에러 없이 정상 반환된다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = Exception("LLM timeout")

        remaining = {"자산명/호스트명"}
        result = MappingResult()

        # 에러 없이 실행됨
        await _apply_llm_synonym_discovery(
            llm=mock_llm,
            remaining=remaining,
            all_db_synonyms={"db1": {"t.c": []}},
            eav_name_synonyms=None,
            priority_db_ids=[],
            result=result,
            cache_manager=None,
        )

        # 매핑 없음, remaining 변동 없음
        assert len(result.mapping_sources) == 0
        assert "자산명/호스트명" in remaining

    @pytest.mark.asyncio
    async def test_empty_remaining_skips_llm(self) -> None:
        """remaining이 비어있으면 LLM 호출 없이 반환한다."""
        mock_llm = AsyncMock()
        remaining: set[str] = set()

        await _apply_llm_synonym_discovery(
            llm=mock_llm,
            remaining=remaining,
            all_db_synonyms={},
            eav_name_synonyms=None,
            priority_db_ids=[],
            result=MappingResult(),
            cache_manager=None,
        )

        mock_llm.ainvoke.assert_not_called()


class TestRegisterLlmSynonymDiscoveriesToRedis:
    """_register_llm_synonym_discoveries_to_redis() 함수 검증."""

    @pytest.mark.asyncio
    async def test_column_mapping_calls_add_global_synonym(self) -> None:
        """컬럼 매핑 시 cache_manager.add_global_synonym()이 호출된다."""
        cm = _make_cache_manager()
        mapped_fields = [
            ("자산명/호스트명", "polestar:CMM_RESOURCE.HOSTNAME", "column"),
        ]

        await _register_llm_synonym_discoveries_to_redis(
            cm, mapped_fields, eav_name_synonyms=None,
        )

        cm.add_global_synonym.assert_called_once_with("HOSTNAME", ["자산명/호스트명"])

    @pytest.mark.asyncio
    async def test_eav_mapping_calls_save_eav_name_synonyms(self) -> None:
        """EAV 매핑 시 redis_cache.save_eav_name_synonyms()가 호출된다."""
        cm = _make_cache_manager()
        mapped_fields = [
            ("S/N(Serial Number)", "EAV:SerialNumber", "eav"),
        ]

        await _register_llm_synonym_discoveries_to_redis(
            cm, mapped_fields, eav_name_synonyms={},
        )

        # EAV synonyms 저장 확인
        cm._redis_cache.save_eav_name_synonyms.assert_called_once()
        saved = cm._redis_cache.save_eav_name_synonyms.call_args[0][0]
        assert "SerialNumber" in saved
        assert "S/N(Serial Number)" in saved["SerialNumber"]

    @pytest.mark.asyncio
    async def test_no_cache_manager_no_error(self) -> None:
        """cache_manager=None일 때 에러 없이 반환된다."""
        mapped_fields = [("필드", "db:t.c", "column")]
        # 에러 없이 실행됨
        await _register_llm_synonym_discoveries_to_redis(
            None, mapped_fields, eav_name_synonyms=None,
        )

    @pytest.mark.asyncio
    async def test_redis_unavailable_no_error(self) -> None:
        """redis_available=False일 때 에러 없이 반환된다."""
        cm = _make_cache_manager(redis_available=False)
        mapped_fields = [("필드", "db:t.c", "column")]
        await _register_llm_synonym_discoveries_to_redis(
            cm, mapped_fields, eav_name_synonyms=None,
        )
        cm.add_global_synonym.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_mapped_fields_no_action(self) -> None:
        """mapped_fields가 비어있으면 아무 동작도 하지 않는다."""
        cm = _make_cache_manager()
        await _register_llm_synonym_discoveries_to_redis(
            cm, [], eav_name_synonyms=None,
        )
        cm.add_global_synonym.assert_not_called()


class TestStep28InPerform3StepMapping:
    """perform_3step_mapping() 내 Step 2.8 삽입 위치 검증."""

    @pytest.mark.asyncio
    async def test_step28_runs_after_synonym_before_llm(self) -> None:
        """Step 2.8은 Step 2.5 이후, Step 3 이전에 실행된다.

        synonym으로 매핑되지 않은 필드가 Step 2.8에서 매핑되면
        Step 3(LLM 통합 추론)에는 전달되지 않는다.
        """
        mock_llm = AsyncMock()
        # Step 2.8 응답: "자산명/호스트명"을 컬럼에 매핑
        # Step 3 응답: 다른 필드에 대해 응답 (호출되지 않아야 함)
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({
                "자산명/호스트명": {
                    "matched_key": "polestar:CMM_RESOURCE.HOSTNAME",
                    "reason": "호스트명 매칭",
                },
            })
        )

        result, details = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["서버명", "자산명/호스트명"],
            field_mapping_hints=[],
            all_db_synonyms={
                "polestar": {
                    "CMM_RESOURCE.HOSTNAME": ["서버명"],  # "서버명"은 Step 2에서 매핑
                }
            },
            all_db_descriptions={},
            priority_db_ids=["polestar"],
        )

        # "서버명"은 Step 2(synonym)에서 매핑
        assert result.mapping_sources["서버명"] == "synonym"
        # "자산명/호스트명"은 Step 2.8(llm_synonym)에서 매핑
        assert result.mapping_sources["자산명/호스트명"] == "llm_synonym"
        # LLM은 Step 2.8에서 1회만 호출 (Step 3은 미호출: remaining이 비어있으므로)
        assert mock_llm.ainvoke.call_count == 1

    @pytest.mark.asyncio
    async def test_llm_synonym_counted_in_summary(self) -> None:
        """perform_3step_mapping의 로그/결과에 llm_synonym 카운트가 반영된다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content=json.dumps({
                "IP": {
                    "matched_key": "polestar:CMM_RESOURCE.IPADDRESS",
                    "reason": "IP = IPADDRESS",
                },
            })
        )

        result, _ = await perform_3step_mapping(
            llm=mock_llm,
            field_names=["IP"],
            field_mapping_hints=[],
            all_db_synonyms={"polestar": {"CMM_RESOURCE.IPADDRESS": []}},
            all_db_descriptions={},
            priority_db_ids=["polestar"],
        )

        llm_synonym_count = sum(
            1 for s in result.mapping_sources.values() if s == "llm_synonym"
        )
        assert llm_synonym_count == 1


# === 해결 3: EAV 피벗 쿼리 분리 검증 ===


class TestMultiDbExecutorEavSeparation:
    """multi_db_executor._generate_sql()의 EAV 피벗 쿼리 분리 로직 검증."""

    def test_get_eav_pattern_extracts_pattern(self) -> None:
        """_get_eav_pattern()이 structure_meta에서 EAV 패턴을 추출한다."""
        from src.nodes.multi_db_executor import _get_eav_pattern

        schema_info = {
            "_structure_meta": {
                "patterns": [
                    {
                        "type": "eav",
                        "entity_table": "CMM_RESOURCE",
                        "config_table": "CORE_CONFIG_PROP",
                        "attribute_column": "NAME",
                        "value_column": "VALUE",
                        "join_condition": "r.RESOURCE_ID = p.RESOURCE_ID",
                    }
                ]
            }
        }

        pattern = _get_eav_pattern(schema_info)
        assert pattern is not None
        assert pattern["type"] == "eav"
        assert pattern["config_table"] == "CORE_CONFIG_PROP"

    def test_get_eav_pattern_returns_none_for_missing(self) -> None:
        """_get_eav_pattern()이 EAV 패턴이 없으면 None을 반환한다."""
        from src.nodes.multi_db_executor import _get_eav_pattern

        assert _get_eav_pattern(None) is None
        assert _get_eav_pattern({}) is None
        assert _get_eav_pattern({"_structure_meta": None}) is None
        assert _get_eav_pattern({"_structure_meta": {"patterns": []}}) is None

    def test_extract_eav_tables(self) -> None:
        """_extract_eav_tables()가 EAV 패턴의 관련 테이블명을 추출한다."""
        from src.nodes.multi_db_executor import _extract_eav_tables

        schema_info = {
            "_structure_meta": {
                "patterns": [
                    {
                        "type": "eav",
                        "entity_table": "CMM_RESOURCE",
                        "config_table": "CORE_CONFIG_PROP",
                    }
                ]
            }
        }

        tables = _extract_eav_tables(schema_info)
        assert "cmm_resource" in tables
        assert "core_config_prop" in tables

    def test_extract_eav_tables_returns_empty_for_no_eav(self) -> None:
        """_extract_eav_tables()가 EAV 패턴이 없으면 빈 set을 반환한다."""
        from src.nodes.multi_db_executor import _extract_eav_tables

        assert _extract_eav_tables(None) == set()
        assert _extract_eav_tables({}) == set()

    @pytest.mark.asyncio
    async def test_generate_sql_separates_eav_entries(self) -> None:
        """_generate_sql()이 EAV 매핑을 분리하여 CASE WHEN 가이드를 생성한다."""
        from src.nodes.multi_db_executor import _generate_sql

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT r.HOSTNAME FROM CMM_RESOURCE r LIMIT 100;\n```"
        )

        schema_info = {
            "tables": {
                "CMM_RESOURCE": {
                    "columns": [
                        {"name": "HOSTNAME", "type": "varchar(255)"},
                        {"name": "RESOURCE_ID", "type": "integer"},
                    ],
                },
                "CORE_CONFIG_PROP": {
                    "columns": [
                        {"name": "NAME", "type": "varchar(255)"},
                        {"name": "VALUE", "type": "varchar(1000)"},
                        {"name": "RESOURCE_ID", "type": "integer"},
                    ],
                },
            },
            "_structure_meta": {
                "patterns": [
                    {
                        "type": "eav",
                        "entity_table": "CMM_RESOURCE",
                        "config_table": "CORE_CONFIG_PROP",
                        "attribute_column": "NAME",
                        "value_column": "VALUE",
                        "join_condition": "r.RESOURCE_ID = p.RESOURCE_ID",
                    }
                ],
                "query_guide": "",
            },
        }

        column_mapping = {
            "서버명": "CMM_RESOURCE.HOSTNAME",
            "제조사": "EAV:Vendor",
            "모델명": "EAV:Model",
        }

        await _generate_sql(
            llm=mock_llm,
            parsed_requirements={},
            schema_info=schema_info,
            sub_query_context="서버 정보 조회",
            default_limit=1000,
            column_mapping=column_mapping,
            db_engine="postgresql",
        )

        # LLM에 전달된 메시지 검사
        call_args = mock_llm.ainvoke.call_args[0][0]
        user_msg = call_args[1].content

        # 정규 매핑 포함
        assert "CMM_RESOURCE.HOSTNAME" in user_msg
        # EAV 피벗 매핑 가이드 포함
        assert "CASE WHEN" in user_msg
        assert "Vendor" in user_msg
        assert "Model" in user_msg
        # EAV 설정 테이블 정보 포함
        assert "CORE_CONFIG_PROP" in user_msg

    @pytest.mark.asyncio
    async def test_generate_sql_consistency_with_query_generator(self) -> None:
        """multi_db_executor의 EAV 분리 로직이 query_generator와 동일한 패턴을 사용한다.

        두 모듈 모두:
        - regular_entries / eav_entries 분리
        - _get_eav_pattern() 사용
        - _extract_eav_tables() 사용
        - CASE WHEN 피벗 가이드 생성
        """
        import importlib
        import src.nodes.multi_db_executor as mde_module
        qg_module = importlib.import_module("src.nodes.query_generator")

        # 동일 함수 존재 여부 확인
        assert hasattr(mde_module, "_get_eav_pattern")
        assert hasattr(mde_module, "_extract_eav_tables")
        assert hasattr(qg_module, "_get_eav_pattern")
        assert hasattr(qg_module, "_extract_eav_tables")

    @pytest.mark.asyncio
    async def test_eav_table_consistency_filter(self) -> None:
        """EAV 쿼리 시 비-EAV 테이블 매핑이 제외된다."""
        from src.nodes.multi_db_executor import _generate_sql

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT 1 LIMIT 1;\n```"
        )

        schema_info = {
            "tables": {
                "CMM_RESOURCE": {
                    "columns": [{"name": "HOSTNAME", "type": "varchar"}],
                },
                "CORE_CONFIG_PROP": {
                    "columns": [{"name": "NAME", "type": "varchar"}, {"name": "VALUE", "type": "varchar"}],
                },
                "OTHER_TABLE": {
                    "columns": [{"name": "COL1", "type": "varchar"}],
                },
            },
            "_structure_meta": {
                "patterns": [
                    {
                        "type": "eav",
                        "entity_table": "CMM_RESOURCE",
                        "config_table": "CORE_CONFIG_PROP",
                        "attribute_column": "NAME",
                        "value_column": "VALUE",
                    }
                ],
                "query_guide": "",
            },
        }

        # OTHER_TABLE의 컬럼이 매핑에 포함되어도 EAV 쿼리에서 제외되어야 함
        column_mapping = {
            "서버명": "CMM_RESOURCE.HOSTNAME",
            "기타": "OTHER_TABLE.COL1",
            "제조사": "EAV:Vendor",
        }

        await _generate_sql(
            llm=mock_llm,
            parsed_requirements={},
            schema_info=schema_info,
            sub_query_context="서버 정보 조회",
            default_limit=1000,
            column_mapping=column_mapping,
            db_engine="postgresql",
        )

        call_args = mock_llm.ainvoke.call_args[0][0]
        user_msg = call_args[1].content

        # Plan 37 수정 3-2: 정규 컬럼 필터링 제거
        # EAV config 테이블과 entity 테이블이 다를 수 있으므로
        # LLM이 schema_info를 보고 적절한 JOIN을 결정하도록 함
        # 따라서 OTHER_TABLE.COL1도 프롬프트에 포함됨
        assert "OTHER_TABLE.COL1" in user_msg
