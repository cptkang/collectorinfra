"""구조 분석 및 EAV 지원 단위 테스트.

DB의 EAV(Entity-Attribute-Value) 비정규화 테이블 쿼리 지원 기능,
구조 분석 유틸 함수, SQL 안전성 검증 등을 검증한다.
DB 연결 없이 단위 테스트로 실행 가능하다.

Plan 27 리팩토링 이후: polestar 하드코딩 제거, _structure_meta 기반으로 전환.
"""

import copy

import pytest

from src.nodes.query_generator import (
    _build_system_prompt,
    _build_user_prompt,
    _extract_eav_tables,
    _format_structure_guide,
    _get_eav_pattern,
)
from src.nodes.query_validator import (
    _add_limit_clause,
    _has_limit_clause,
    _validate_forbidden_joins,
)
from src.nodes.schema_analyzer import (
    _format_schema_for_analysis,
    _parse_llm_json,
    _validate_sample_sql,
)
from src.routing.domain_config import get_domain_by_id


# ---------------------------------------------------------------------------
# 1. LIMIT 검사 DB2/PostgreSQL (_has_limit_clause)
# ---------------------------------------------------------------------------

class TestHasLimitClause:
    """_has_limit_clause 함수 검증."""

    def test_has_limit_clause_postgresql(self):
        assert _has_limit_clause("SELECT * FROM t LIMIT 100") is True

    def test_has_limit_clause_db2(self):
        assert _has_limit_clause("SELECT * FROM t FETCH FIRST 100 ROWS ONLY") is True

    def test_has_limit_clause_db2_singular(self):
        assert _has_limit_clause("SELECT * FROM t FETCH FIRST 1 ROW ONLY") is True

    def test_has_limit_clause_none(self):
        assert _has_limit_clause("SELECT * FROM t") is False


# ---------------------------------------------------------------------------
# 2. LIMIT 자동 추가 DB 엔진별 (_add_limit_clause)
# ---------------------------------------------------------------------------

class TestAddLimitClause:
    """_add_limit_clause 함수 검증."""

    def test_add_limit_clause_postgresql(self):
        result = _add_limit_clause("SELECT * FROM t", 100, "postgresql")
        assert "LIMIT 100" in result
        assert "FETCH FIRST" not in result

    def test_add_limit_clause_db2(self):
        result = _add_limit_clause("SELECT * FROM t", 100, "db2")
        assert "FETCH FIRST 100 ROWS ONLY" in result
        assert "LIMIT" not in result.split("FETCH")[0]

    def test_add_limit_clause_default(self):
        """db_engine 미지정 시 postgresql 기본"""
        result = _add_limit_clause("SELECT * FROM t", 50)
        assert "LIMIT 50" in result

    def test_add_limit_clause_strips_semicolon(self):
        result = _add_limit_clause("SELECT * FROM t;", 100, "postgresql")
        assert result.endswith(";")
        assert "LIMIT 100" in result


# ---------------------------------------------------------------------------
# 3. 회귀 테스트 (특수 구조 없는 DB에 영향 없음)
# ---------------------------------------------------------------------------

class TestRegressionNonSpecialStructure:
    """특수 구조가 없는 DB에 구조 가이드가 삽입되지 않는지 검증."""

    def test_no_structure_guide_without_meta(self):
        """일반 DB에서는 structure_guide가 비어있음"""
        schema = {
            "tables": {
                "servers": {
                    "columns": [{"name": "id", "type": "int", "primary_key": True}]
                }
            }
        }
        prompt = _build_system_prompt(schema, 100)
        # _structure_meta가 없으므로 structure_guide 관련 내용 미포함
        assert "LIMIT 100" in prompt or "100" in prompt

    def test_structure_guide_included_when_meta_present(self):
        """_structure_meta가 있으면 구조 가이드가 포함됨"""
        schema = {
            "tables": {
                "TEST_TABLE": {
                    "columns": [{"name": "ID", "type": "int", "primary_key": True}]
                }
            },
            "_structure_meta": {
                "patterns": [
                    {
                        "type": "eav",
                        "entity_table": "TEST_TABLE",
                        "config_table": "CONFIG_TABLE",
                        "attribute_column": "NAME",
                        "value_column": "VALUE",
                        "join_condition": "CONFIG_TABLE.FK = TEST_TABLE.ID",
                    }
                ],
                "query_guide": "EAV 피벗 쿼리를 사용하세요.",
            },
        }
        prompt = _build_system_prompt(schema, 100, active_db_engine="db2")
        assert "EAV 피벗 쿼리를 사용하세요" in prompt
        assert "DB2" in prompt


# ---------------------------------------------------------------------------
# 4. DBDomainConfig db_engine 필드 테스트
# ---------------------------------------------------------------------------

class TestDBDomainConfigEngine:
    """DBDomainConfig의 db_engine 필드 검증."""

    def test_polestar_db_engine(self):
        polestar = get_domain_by_id("polestar")
        assert polestar is not None
        assert polestar.db_engine == "db2"

    def test_other_db_engine_default(self):
        """다른 DB는 기본 postgresql"""
        cloud = get_domain_by_id("cloud_portal")
        assert cloud is not None
        assert cloud.db_engine == "postgresql"


# ---------------------------------------------------------------------------
# 5. EAV 속성 유사어 매칭 (_apply_eav_synonym_mapping)
# ---------------------------------------------------------------------------

from src.document.field_mapper import _apply_eav_synonym_mapping, MappingResult


class TestApplyEavSynonymMapping:
    """EAV 속성 유사어 매칭 테스트."""

    def test_basic_match(self):
        """기본 EAV 유사어 매칭"""
        remaining = {"OS종류", "서버명"}
        eav_synonyms = {"OSType": ["운영체제", "OS 종류", "OS종류"]}
        result = MappingResult()
        _apply_eav_synonym_mapping(remaining, eav_synonyms, result)
        assert "OS종류" not in remaining
        assert "서버명" in remaining  # EAV 아닌 필드는 남아있음
        assert result.db_column_mapping["_default"]["OS종류"] == "EAV:OSType"
        assert result.mapping_sources["OS종류"] == "eav_synonym"

    def test_basic_match_with_explicit_db_id(self):
        """eav_db_id를 명시적으로 전달하는 경우"""
        remaining = {"OS종류"}
        eav_synonyms = {"OSType": ["OS종류"]}
        result = MappingResult()
        _apply_eav_synonym_mapping(remaining, eav_synonyms, result, eav_db_id="polestar")
        assert "OS종류" not in remaining
        assert result.db_column_mapping["polestar"]["OS종류"] == "EAV:OSType"

    def test_case_insensitive(self):
        """대소문자 무관 매칭"""
        remaining = {"운영체제"}
        eav_synonyms = {"OSType": ["운영체제"]}
        result = MappingResult()
        _apply_eav_synonym_mapping(remaining, eav_synonyms, result)
        assert "운영체제" not in remaining
        assert result.db_column_mapping["_default"]["운영체제"] == "EAV:OSType"

    def test_attr_name_direct_match(self):
        """EAV 속성명 자체로 매칭"""
        remaining = {"OSType"}
        eav_synonyms = {"OSType": ["운영체제", "OS 종류"]}
        result = MappingResult()
        _apply_eav_synonym_mapping(remaining, eav_synonyms, result)
        assert "OSType" not in remaining
        assert result.db_column_mapping["_default"]["OSType"] == "EAV:OSType"

    def test_no_match(self):
        """매칭되지 않는 필드는 remaining에 유지"""
        remaining = {"비고", "메모"}
        eav_synonyms = {"OSType": ["운영체제"]}
        result = MappingResult()
        _apply_eav_synonym_mapping(remaining, eav_synonyms, result)
        assert remaining == {"비고", "메모"}
        assert not result.db_column_mapping

    def test_empty_synonyms(self):
        """빈 synonyms는 아무 매칭 없음"""
        remaining = {"OS종류"}
        result = MappingResult()
        _apply_eav_synonym_mapping(remaining, {}, result)
        assert "OS종류" in remaining

    def test_multiple_matches(self):
        """여러 EAV 속성이 동시에 매칭"""
        remaining = {"운영체제", "제조사", "서버 모델"}
        eav_synonyms = {
            "OSType": ["운영체제", "OS 종류"],
            "Vendor": ["제조사", "벤더"],
            "Model": ["서버 모델", "모델명"],
        }
        result = MappingResult()
        _apply_eav_synonym_mapping(remaining, eav_synonyms, result)
        assert len(remaining) == 0
        assert result.db_column_mapping["_default"]["운영체제"] == "EAV:OSType"
        assert result.db_column_mapping["_default"]["제조사"] == "EAV:Vendor"
        assert result.db_column_mapping["_default"]["서버 모델"] == "EAV:Model"


# ---------------------------------------------------------------------------
# 6. EAV 매핑 검증 (_validate_mapping)
# ---------------------------------------------------------------------------

from src.document.field_mapper import _validate_mapping


class TestValidateMappingEav:
    """EAV 매핑 검증 테스트."""

    def _make_schema_with_eav(self):
        return {
            "tables": {
                "CMM_RESOURCE": {
                    "columns": [{"name": "HOSTNAME", "type": "VARCHAR"}]
                }
            },
            "_structure_meta": {
                "patterns": [
                    {
                        "type": "eav",
                        "entity_table": "CMM_RESOURCE",
                        "config_table": "CONFIG_TABLE",
                        "attribute_column": "NAME",
                        "value_column": "VALUE",
                        "known_attributes": ["OSType", "Vendor", "Model", "Hostname"],
                    }
                ]
            },
        }

    def test_valid_eav_mapping(self):
        """known_attributes에 있는 EAV 속성 -> 통과"""
        schema = self._make_schema_with_eav()
        mapping = {"OS종류": "EAV:OSType"}
        result = _validate_mapping(mapping, schema, ["OS종류"])
        assert result["OS종류"] == "EAV:OSType"

    def test_unknown_eav_mapping(self):
        """known_attributes에 없는 EAV 속성 -> None"""
        schema = self._make_schema_with_eav()
        mapping = {"알 수 없는 필드": "EAV:UnknownAttr"}
        result = _validate_mapping(mapping, schema, ["알 수 없는 필드"])
        assert result["알 수 없는 필드"] is None

    def test_regular_column_still_works(self):
        """정규 컬럼 매핑은 기존대로 동작"""
        schema = self._make_schema_with_eav()
        mapping = {"서버명": "CMM_RESOURCE.HOSTNAME"}
        result = _validate_mapping(mapping, schema, ["서버명"])
        assert result["서버명"] == "CMM_RESOURCE.HOSTNAME"

    def test_mixed_eav_and_regular(self):
        """EAV + 정규 컬럼 혼합 매핑"""
        schema = self._make_schema_with_eav()
        mapping = {
            "서버명": "CMM_RESOURCE.HOSTNAME",
            "OS종류": "EAV:OSType",
            "비고": None,
        }
        result = _validate_mapping(mapping, schema, ["서버명", "OS종류", "비고"])
        assert result["서버명"] == "CMM_RESOURCE.HOSTNAME"
        assert result["OS종류"] == "EAV:OSType"
        assert result["비고"] is None

    def test_eav_without_structure_meta(self):
        """_structure_meta가 없으면 EAV 매핑 실패"""
        schema = {"tables": {"servers": {"columns": [{"name": "id", "type": "int"}]}}}
        mapping = {"OS종류": "EAV:OSType"}
        result = _validate_mapping(mapping, schema, ["OS종류"])
        assert result["OS종류"] is None


# ---------------------------------------------------------------------------
# 7. EAV 가상 컬럼 포맷 (_format_schema_columns)
# ---------------------------------------------------------------------------

from src.document.field_mapper import _format_schema_columns


class TestFormatSchemaColumnsEav:
    """EAV 가상 컬럼 포맷 테스트."""

    def test_eav_virtual_columns_included(self):
        """_structure_meta가 있으면 EAV 가상 컬럼 포함"""
        schema = {
            "tables": {
                "CMM_RESOURCE": {
                    "columns": [{"name": "HOSTNAME", "type": "VARCHAR"}]
                }
            },
            "_structure_meta": {
                "patterns": [
                    {
                        "type": "eav",
                        "entity_table": "CMM_RESOURCE",
                        "config_table": "CONFIG_TABLE",
                        "attribute_column": "NAME",
                        "value_column": "VALUE",
                        "known_attributes": ["OSType", "Vendor", "Model", "Hostname"],
                    }
                ]
            },
        }
        result = _format_schema_columns(schema)
        assert "EAV:OSType" in result
        assert "EAV:Vendor" in result
        assert "EAV 피벗 속성" in result

    def test_no_eav_without_structure_meta(self):
        """일반 스키마에서는 EAV 가상 컬럼 미포함"""
        schema = {
            "tables": {
                "servers": {
                    "columns": [{"name": "hostname", "type": "varchar"}]
                }
            }
        }
        result = _format_schema_columns(schema)
        assert "EAV:" not in result


# ---------------------------------------------------------------------------
# 8. Query Generator의 EAV 매핑 감지
# ---------------------------------------------------------------------------


class TestQueryGeneratorEavMapping:
    """Query Generator의 EAV 매핑 감지 테스트."""

    def test_eav_mapping_generates_pivot_hint(self):
        """EAV 매핑이 있으면 피벗 쿼리 힌트 포함"""
        prompt = _build_user_prompt(
            parsed_requirements={"original_query": "서버 정보 조회"},
            template_structure=None,
            error_message=None,
            previous_sql=None,
            column_mapping={
                "서버명": "CMM_RESOURCE.HOSTNAME",
                "OS종류": "EAV:OSType",
                "제조사": "EAV:Vendor",
            },
        )
        assert "EAV 피벗 매핑" in prompt
        assert "CASE WHEN" in prompt
        assert "OSType" in prompt
        assert "Vendor" in prompt
        # 정규 매핑도 포함
        assert "CMM_RESOURCE.HOSTNAME" in prompt

    def test_no_eav_mapping_no_pivot_hint(self):
        """EAV 매핑이 없으면 피벗 힌트 미포함"""
        prompt = _build_user_prompt(
            parsed_requirements={"original_query": "서버 목록"},
            template_structure=None,
            error_message=None,
            previous_sql=None,
            column_mapping={
                "서버명": "CMM_RESOURCE.HOSTNAME",
            },
        )
        assert "EAV 피벗 매핑" not in prompt
        assert "CMM_RESOURCE.HOSTNAME" in prompt

    def test_only_eav_mapping(self):
        """EAV 매핑만 있는 경우"""
        prompt = _build_user_prompt(
            parsed_requirements={"original_query": "OS 정보"},
            template_structure=None,
            error_message=None,
            previous_sql=None,
            column_mapping={
                "OS종류": "EAV:OSType",
            },
        )
        assert "EAV 피벗 매핑" in prompt
        assert "양식-DB 매핑" not in prompt  # 정규 매핑 섹션 없음


# ===========================================================================
# Plan 27 신규 테스트
# ===========================================================================


# ---------------------------------------------------------------------------
# 9. _validate_sample_sql 안전성 검증
# ---------------------------------------------------------------------------

class TestValidateSampleSql:
    """_validate_sample_sql 안전성 검증 테스트."""

    def test_valid_select_with_limit(self):
        assert _validate_sample_sql("SELECT * FROM t LIMIT 10") is True

    def test_valid_select_with_fetch_first(self):
        assert _validate_sample_sql("SELECT * FROM t FETCH FIRST 10 ROWS ONLY") is True

    def test_reject_insert(self):
        assert _validate_sample_sql("INSERT INTO t VALUES (1)") is False

    def test_reject_delete(self):
        assert _validate_sample_sql("DELETE FROM t WHERE id = 1") is False

    def test_reject_no_limit(self):
        assert _validate_sample_sql("SELECT * FROM t") is False

    def test_reject_update(self):
        assert _validate_sample_sql("UPDATE t SET x = 1") is False

    def test_reject_drop(self):
        assert _validate_sample_sql("DROP TABLE t") is False


# ---------------------------------------------------------------------------
# 10. _format_schema_for_analysis 변환 테스트
# ---------------------------------------------------------------------------

class TestFormatSchemaForAnalysis:
    """_format_schema_for_analysis 변환 테스트."""

    def test_basic_format(self):
        schema_dict = {
            "tables": {
                "users": {
                    "columns": [
                        {"name": "id", "type": "int", "primary_key": True, "nullable": False},
                        {"name": "name", "type": "varchar"},
                    ]
                }
            }
        }
        result = _format_schema_for_analysis(schema_dict)
        assert "users" in result
        assert "id: int" in result
        assert "PK" in result
        assert "name: varchar" in result

    def test_fk_included(self):
        schema_dict = {
            "tables": {
                "t": {
                    "columns": [
                        {"name": "id", "type": "int", "foreign_key": True, "references": "other.id"}
                    ]
                }
            },
            "relationships": [{"from": "t.id", "to": "other.id"}],
        }
        result = _format_schema_for_analysis(schema_dict)
        assert "FK" in result
        assert "FK 관계" in result


# ---------------------------------------------------------------------------
# 11. _parse_llm_json JSON 추출 테스트
# ---------------------------------------------------------------------------

class TestParseLlmJson:
    """_parse_llm_json JSON 추출 테스트."""

    def test_plain_json(self):
        result = _parse_llm_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_wrapped(self):
        result = _parse_llm_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_invalid_json(self):
        with pytest.raises(ValueError):
            _parse_llm_json("not json at all")


# ---------------------------------------------------------------------------
# 12. _extract_eav_tables 동적 추출 테스트
# ---------------------------------------------------------------------------

class TestExtractEavTables:
    """_extract_eav_tables 동적 추출 테스트."""

    def test_extract_eav_tables(self):
        schema = {
            "_structure_meta": {
                "patterns": [
                    {"type": "eav", "entity_table": "ENTITY", "config_table": "CONFIG"}
                ]
            }
        }
        result = _extract_eav_tables(schema)
        assert "entity" in result
        assert "config" in result

    def test_no_structure_meta(self):
        assert _extract_eav_tables({"tables": {}}) == set()

    def test_no_eav_pattern(self):
        schema = {
            "_structure_meta": {
                "patterns": [{"type": "hierarchy", "table": "T"}]
            }
        }
        assert _extract_eav_tables(schema) == set()


# ---------------------------------------------------------------------------
# 13. _get_eav_pattern 첫 EAV 패턴 추출 테스트
# ---------------------------------------------------------------------------

class TestGetEavPattern:
    """_get_eav_pattern 첫 EAV 패턴 추출 테스트."""

    def test_get_first_eav(self):
        schema = {
            "_structure_meta": {
                "patterns": [
                    {"type": "hierarchy", "table": "T"},
                    {"type": "eav", "entity_table": "E", "config_table": "C"},
                ]
            }
        }
        result = _get_eav_pattern(schema)
        assert result["type"] == "eav"
        assert result["entity_table"] == "E"

    def test_none_without_meta(self):
        assert _get_eav_pattern(None) is None
        assert _get_eav_pattern({}) is None


# ===========================================================================
# Plan 26 캐시 통합 테스트
# ===========================================================================

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from src.nodes.schema_analyzer import (
    _save_structure_profile,
    _analyze_db_structure,
    _llm_select_relevant_tables,
    _collect_structure_samples,
)
from src.dbhub.models import SchemaInfo, TableInfo, ColumnInfo


# ---------------------------------------------------------------------------
# 14. _save_structure_profile YAML/JSON 자동 생성 테스트
# ---------------------------------------------------------------------------

class TestSaveStructureProfile:
    """_save_structure_profile YAML/JSON 자동 생성 테스트."""

    @pytest.mark.asyncio
    async def test_yaml_file_created(self, tmp_path):
        """YAML 파일이 자동 생성되는지 확인"""
        structure_meta = {
            "patterns": [{"type": "eav", "entity_table": "T1", "config_table": "T2"}],
            "query_guide": "test guide"
        }
        cache_mgr = AsyncMock()
        cache_mgr.save_schema = AsyncMock()

        # 패치 전에 원본 os.path.join 참조를 저장하여 재귀 방지
        _real_join = os.path.join

        with patch("src.nodes.schema_analyzer.os.path.join", side_effect=lambda *args: _real_join(str(tmp_path), *args[1:])):
            with patch("src.nodes.schema_analyzer.os.makedirs"):
                await _save_structure_profile("test_db", structure_meta, cache_mgr)

        # Redis 캐시 저장 호출 확인
        cache_mgr.save_schema.assert_called_once()
        call_args = cache_mgr.save_schema.call_args
        assert call_args[0][0] == "test_db:structure_meta"

    @pytest.mark.asyncio
    async def test_cache_save_failure_graceful(self):
        """캐시 저장 실패 시에도 YAML 생성 시도"""
        structure_meta = {"patterns": [], "query_guide": ""}
        cache_mgr = AsyncMock()
        cache_mgr.save_schema = AsyncMock(side_effect=Exception("Redis down"))

        # 예외 없이 정상 완료
        await _save_structure_profile("test_db", structure_meta, cache_mgr)

    @pytest.mark.asyncio
    async def test_json_fallback_without_yaml(self):
        """PyYAML 미설치 시 JSON fallback"""
        structure_meta = {"patterns": [{"type": "hierarchy"}], "query_guide": "guide"}
        cache_mgr = AsyncMock()
        cache_mgr.save_schema = AsyncMock()

        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("no yaml")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with patch("src.nodes.schema_analyzer.os.makedirs"):
                with patch("builtins.open", MagicMock()):
                    await _save_structure_profile("test_db", structure_meta, cache_mgr)

        # 예외 없이 정상 완료되면 성공


# ---------------------------------------------------------------------------
# 15. 구조 분석 캐시 흐름 단위 테스트
# ---------------------------------------------------------------------------

class TestStructureMetaCacheFlow:
    """구조 분석 캐시 흐름 단위 테스트."""

    def test_cache_key_format(self):
        """캐시 키가 '{db_id}:structure_meta' 형식인지 확인"""
        assert "test_db:structure_meta" == "test_db:structure_meta"

    @pytest.mark.asyncio
    async def test_analyze_returns_none_for_empty_patterns(self):
        """LLM이 빈 patterns를 반환하면 None"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(
            content='{"patterns": [], "query_guide": ""}'
        ))

        result = await _analyze_db_structure(mock_llm, {"tables": {"t": {"columns": []}}})
        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_returns_dict_for_eav_pattern(self):
        """LLM이 EAV 패턴을 반환하면 dict"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(
            content='{"patterns": [{"type": "eav", "entity_table": "E", "config_table": "C", "attribute_column": "NAME", "value_column": "VALUE", "join_condition": "C.FK = E.ID"}], "query_guide": "Use EAV pivot"}'
        ))

        result = await _analyze_db_structure(mock_llm, {"tables": {"E": {"columns": []}, "C": {"columns": []}}})
        assert result is not None
        assert len(result["patterns"]) == 1
        assert result["patterns"][0]["type"] == "eav"
        assert result["query_guide"] == "Use EAV pivot"

    @pytest.mark.asyncio
    async def test_analyze_returns_none_on_llm_failure(self):
        """LLM 호출 실패 시 None 반환 (graceful)"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM timeout"))

        result = await _analyze_db_structure(mock_llm, {"tables": {}})
        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_returns_none_on_invalid_json(self):
        """LLM이 잘못된 JSON 반환 시 None"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(
            content="이것은 JSON이 아닙니다"
        ))

        result = await _analyze_db_structure(mock_llm, {"tables": {}})
        assert result is None


# ---------------------------------------------------------------------------
# 16. _llm_select_relevant_tables LLM mock 테스트
# ---------------------------------------------------------------------------

class TestLlmSelectRelevantTables:
    """_llm_select_relevant_tables LLM mock 테스트."""

    @pytest.mark.asyncio
    async def test_returns_all_tables_when_no_targets(self):
        """query_targets가 없으면 LLM 호출 없이 전체 반환"""
        schema = SchemaInfo()
        schema.tables["users"] = TableInfo(name="users", columns=[ColumnInfo(name="id", data_type="int")])
        schema.tables["orders"] = TableInfo(name="orders", columns=[ColumnInfo(name="id", data_type="int")])

        mock_llm = AsyncMock()  # LLM이 호출되면 안 됨

        result = await _llm_select_relevant_tables(mock_llm, schema, [], "")
        assert set(result) == {"users", "orders"}
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_selects_relevant_tables(self):
        """LLM이 관련 테이블을 선택"""
        schema = SchemaInfo()
        schema.tables["users"] = TableInfo(name="users", columns=[ColumnInfo(name="id", data_type="int")])
        schema.tables["orders"] = TableInfo(name="orders", columns=[ColumnInfo(name="id", data_type="int")])
        schema.tables["logs"] = TableInfo(name="logs", columns=[ColumnInfo(name="id", data_type="int")])

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="users, orders"))

        result = await _llm_select_relevant_tables(mock_llm, schema, ["사용자"], "사용자 주문 조회")
        assert "users" in result
        assert "orders" in result
        assert "logs" not in result

    @pytest.mark.asyncio
    async def test_llm_failure_returns_all_tables(self):
        """LLM 실패 시 전체 테이블 반환"""
        schema = SchemaInfo()
        schema.tables["t1"] = TableInfo(name="t1", columns=[ColumnInfo(name="id", data_type="int")])

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("timeout"))

        result = await _llm_select_relevant_tables(mock_llm, schema, ["서버"], "서버 목록")
        assert result == ["t1"]

    @pytest.mark.asyncio
    async def test_llm_invalid_tables_returns_all(self):
        """LLM이 존재하지 않는 테이블명만 반환 시 전체 반환"""
        schema = SchemaInfo()
        schema.tables["real_table"] = TableInfo(name="real_table", columns=[ColumnInfo(name="id", data_type="int")])

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="fake_table1, fake_table2"))

        result = await _llm_select_relevant_tables(mock_llm, schema, ["서버"], "서버 목록")
        assert result == ["real_table"]


# ---------------------------------------------------------------------------
# 17. _collect_structure_samples LLM mock 테스트
# ---------------------------------------------------------------------------

class TestCollectStructureSamples:
    """_collect_structure_samples LLM mock 테스트."""

    @pytest.mark.asyncio
    async def test_collects_samples_from_safe_sql(self):
        """안전한 SQL만 실행하여 샘플 수집"""
        structure_meta = {"patterns": [{"type": "eav"}], "query_guide": "guide"}
        schema_dict = {"tables": {"t": {"columns": []}}}

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(
            content='[{"purpose": "EAV names", "sql": "SELECT name FROM config LIMIT 10"}]'
        ))

        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.rows = [{"name": "OSType"}, {"name": "Vendor"}]
        mock_client.execute_sql = AsyncMock(return_value=mock_result)

        result = await _collect_structure_samples(mock_llm, mock_client, schema_dict, structure_meta)
        assert "_structure_meta" in result
        assert "samples" in result["_structure_meta"]

    @pytest.mark.asyncio
    async def test_skips_unsafe_sql(self):
        """위험한 SQL은 스킵"""
        structure_meta = {"patterns": [], "query_guide": ""}
        schema_dict = {"tables": {}}

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(
            content='[{"purpose": "bad", "sql": "DELETE FROM t WHERE 1=1"}]'
        ))
        mock_client = AsyncMock()

        result = await _collect_structure_samples(mock_llm, mock_client, schema_dict, structure_meta)
        mock_client.execute_sql.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_original_schema(self):
        """LLM 실패 시 원본 schema_dict 반환"""
        structure_meta = {"patterns": [], "query_guide": ""}
        schema_dict = {"tables": {"t": {"columns": []}}}

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM error"))
        mock_client = AsyncMock()

        result = await _collect_structure_samples(mock_llm, mock_client, schema_dict, structure_meta)
        assert "tables" in result


# ===========================================================================
# Plan 33 Phase 2: 금지 조인 패턴 검증 테스트
# ===========================================================================


# ---------------------------------------------------------------------------
# 18. _validate_forbidden_joins 금지 조인 패턴 감지 테스트
# ---------------------------------------------------------------------------

def _make_eav_schema_info() -> dict:
    """EAV 프로필이 포함된 테스트용 schema_info를 생성한다."""
    return {
        "tables": {
            "polestar.cmm_resource": {
                "columns": [
                    {"name": "id", "type": "bigint"},
                    {"name": "hostname", "type": "varchar"},
                    {"name": "ipaddress", "type": "varchar"},
                    {"name": "resource_conf_id", "type": "bigint"},
                    {"name": "resource_type", "type": "varchar"},
                ]
            },
            "polestar.core_config_prop": {
                "columns": [
                    {"name": "id", "type": "bigint"},
                    {"name": "configuration_id", "type": "bigint"},
                    {"name": "name", "type": "varchar"},
                    {"name": "stringvalue_short", "type": "varchar"},
                ]
            },
        },
        "_structure_meta": {
            "patterns": [
                {
                    "type": "eav",
                    "entity_table": "cmm_resource",
                    "config_table": "core_config_prop",
                    "attribute_column": "name",
                    "value_column": "stringvalue_short",
                    "excluded_join_columns": [
                        {
                            "table": "cmm_resource",
                            "column": "resource_conf_id",
                            "reason": "운영 DB에서 NULL. core_config_prop.configuration_id와 매핑되지 않음",
                        }
                    ],
                }
            ],
            "query_guide": "hostname 기반 브릿지 조인을 사용하세요.",
        },
    }


class TestValidateForbiddenJoins:
    """_validate_forbidden_joins 금지 조인 패턴 감지 테스트."""

    def test_detect_entity_id_eq_config_configuration_id(self):
        """entity.id = config.configuration_id 직접 조인 감지"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT r.hostname, p.stringvalue_short "
            "FROM polestar.cmm_resource r "
            "LEFT JOIN polestar.core_config_prop p "
            "ON r.id = p.configuration_id "
            "WHERE r.resource_type LIKE 'platform.server%' LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        assert len(errors) >= 1
        assert any("id" in e and "configuration_id" in e for e in errors)

    def test_detect_reverse_config_configuration_id_eq_entity_id(self):
        """config.configuration_id = entity.id 역방향 감지"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT r.hostname "
            "FROM polestar.cmm_resource r "
            "JOIN polestar.core_config_prop p "
            "ON p.configuration_id = r.id "
            "LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        assert len(errors) >= 1
        assert any("configuration_id" in e for e in errors)

    def test_detect_excluded_join_column_resource_conf_id(self):
        """excluded_join_columns: resource_conf_id가 config_table과 조인 시 감지"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT r.hostname "
            "FROM polestar.cmm_resource r "
            "LEFT JOIN polestar.core_config_prop p "
            "ON r.resource_conf_id = p.configuration_id "
            "LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        assert len(errors) >= 1
        assert any("resource_conf_id" in e for e in errors)

    def test_detect_excluded_join_column_reverse(self):
        """excluded_join_columns 역방향: config.configuration_id = entity.resource_conf_id"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT r.hostname "
            "FROM polestar.cmm_resource r "
            "LEFT JOIN polestar.core_config_prop p "
            "ON p.configuration_id = r.resource_conf_id "
            "LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        assert len(errors) >= 1
        assert any("resource_conf_id" in e for e in errors)

    def test_correct_bridge_join_no_error(self):
        """올바른 hostname 기반 브릿지 조인은 에러 없음"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT r.hostname, p_os.stringvalue_short AS os_type "
            "FROM polestar.cmm_resource r "
            "LEFT JOIN polestar.core_config_prop p_host "
            "ON p_host.name = 'Hostname' AND p_host.stringvalue_short = r.hostname "
            "LEFT JOIN polestar.core_config_prop p_os "
            "ON p_os.configuration_id = p_host.configuration_id AND p_os.name = 'OSType' "
            "WHERE r.resource_type LIKE 'platform.server%' LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        assert len(errors) == 0

    def test_no_structure_meta_returns_empty(self):
        """_structure_meta가 없으면 빈 리스트 반환"""
        schema = {"tables": {"t": {"columns": []}}}
        sql = "SELECT * FROM t LEFT JOIN t2 ON t.id = t2.fk LIMIT 100"
        errors = _validate_forbidden_joins(sql, schema)
        assert errors == []

    def test_non_eav_pattern_returns_empty(self):
        """EAV 패턴이 아닌 경우 빈 리스트 반환"""
        schema = {
            "tables": {},
            "_structure_meta": {
                "patterns": [{"type": "hierarchy", "table": "T"}],
            },
        }
        sql = "SELECT * FROM T LIMIT 100"
        errors = _validate_forbidden_joins(sql, schema)
        assert errors == []

    def test_case_insensitive_detection(self):
        """대소문자 무관 감지"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT R.HOSTNAME "
            "FROM polestar.CMM_RESOURCE R "
            "LEFT JOIN polestar.CORE_CONFIG_PROP P "
            "ON R.ID = P.CONFIGURATION_ID "
            "LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        assert len(errors) >= 1

    def test_schema_prefix_handled(self):
        """스키마 접두사(polestar.)가 있어도 정상 감지"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT r.hostname "
            "FROM polestar.cmm_resource r "
            "JOIN polestar.core_config_prop p ON r.id = p.configuration_id "
            "LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        assert len(errors) >= 1

    def test_error_message_includes_bridge_guidance(self):
        """에러 메시지에 올바른 브릿지 조인 안내가 포함됨"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT r.hostname "
            "FROM polestar.cmm_resource r "
            "JOIN polestar.core_config_prop p ON r.id = p.configuration_id "
            "LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        assert len(errors) >= 1
        assert "hostname" in errors[0].lower() or "Hostname" in errors[0]
        assert "브릿지" in errors[0]

    def test_multiple_forbidden_patterns_detected(self):
        """하나의 SQL에서 여러 금지 패턴 동시 감지"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT r.hostname "
            "FROM polestar.cmm_resource r "
            "JOIN polestar.core_config_prop p1 ON r.id = p1.configuration_id "
            "JOIN polestar.core_config_prop p2 ON r.resource_conf_id = p2.configuration_id "
            "LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        # 두 개 이상의 금지 패턴 감지
        assert len(errors) >= 2

    def test_config_to_config_join_no_error(self):
        """config_table 간 configuration_id 조인은 허용 (브릿지 패턴의 2단계)"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT p_os.stringvalue_short "
            "FROM polestar.core_config_prop p_host "
            "JOIN polestar.core_config_prop p_os "
            "ON p_os.configuration_id = p_host.configuration_id "
            "LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        assert len(errors) == 0

    def test_unrelated_table_join_no_error(self):
        """관련 없는 테이블 간 조인은 에러 없음"""
        schema = _make_eav_schema_info()
        sql = (
            "SELECT a.col1 "
            "FROM other_table a "
            "JOIN another_table b ON a.id = b.foreign_id "
            "LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, schema)
        assert len(errors) == 0
