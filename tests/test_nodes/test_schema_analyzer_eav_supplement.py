"""EAV 동반 테이블 자동 보충 로직 검증 테스트.

_supplement_eav_tables()가 수동 프로필의 EAV 패턴을 기반으로
entity 테이블이 선택되었을 때 config 테이블을 자동 포함하는지 검증한다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

from src.nodes.schema_analyzer import _supplement_eav_tables, schema_analyzer
from src.dbhub.models import SchemaInfo, TableInfo, ColumnInfo



class TestSupplementEavTables:
    """_supplement_eav_tables() 함수 단위 테스트."""

    def test_entity_selected_adds_config(self, tmp_path, monkeypatch):
        """entity 테이블이 선택되면 config 테이블이 자동 추가된다."""
        # 수동 프로필 준비
        profiles_dir = tmp_path / "config" / "db_profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "test_db.yaml").write_text(
            "source: manual\n"
            "patterns:\n"
            "  - type: eav\n"
            "    entity_table: cmm_resource\n"
            "    config_table: core_config_prop\n"
            "    attribute_column: name\n"
            "    value_column: stringvalue_short\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        relevant = ["polestar.cmm_resource"]
        all_tables = ["polestar.cmm_resource", "polestar.core_config_prop"]

        result = _supplement_eav_tables(relevant, all_tables, "test_db")

        assert "polestar.cmm_resource" in result
        assert "polestar.core_config_prop" in result

    def test_config_already_selected_no_duplicate(self, tmp_path, monkeypatch):
        """config 테이블이 이미 선택되어 있으면 중복 추가하지 않는다."""
        profiles_dir = tmp_path / "config" / "db_profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "test_db.yaml").write_text(
            "source: manual\n"
            "patterns:\n"
            "  - type: eav\n"
            "    entity_table: cmm_resource\n"
            "    config_table: core_config_prop\n"
            "    attribute_column: name\n"
            "    value_column: stringvalue_short\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        relevant = ["polestar.cmm_resource", "polestar.core_config_prop"]
        all_tables = ["polestar.cmm_resource", "polestar.core_config_prop"]

        result = _supplement_eav_tables(relevant, all_tables, "test_db")

        assert result.count("polestar.core_config_prop") == 1

    def test_no_entity_selected_no_change(self, tmp_path, monkeypatch):
        """entity 테이블이 선택되지 않았으면 변경 없다."""
        profiles_dir = tmp_path / "config" / "db_profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "test_db.yaml").write_text(
            "source: manual\n"
            "patterns:\n"
            "  - type: eav\n"
            "    entity_table: cmm_resource\n"
            "    config_table: core_config_prop\n"
            "    attribute_column: name\n"
            "    value_column: stringvalue_short\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        relevant = ["polestar.other_table"]
        all_tables = ["polestar.cmm_resource", "polestar.core_config_prop", "polestar.other_table"]

        result = _supplement_eav_tables(relevant, all_tables, "test_db")

        assert result == ["polestar.other_table"]

    def test_no_manual_profile_no_change(self):
        """수동 프로필이 없으면 원본 그대로 반환한다."""
        relevant = ["polestar.cmm_resource"]
        all_tables = ["polestar.cmm_resource", "polestar.core_config_prop"]

        result = _supplement_eav_tables(relevant, all_tables, "nonexistent_db_id")

        assert result == ["polestar.cmm_resource"]

    def test_no_eav_pattern_no_change(self, tmp_path, monkeypatch):
        """EAV 패턴이 아닌 프로필이면 변경 없다."""
        profiles_dir = tmp_path / "config" / "db_profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "test_db.yaml").write_text(
            "source: manual\n"
            "patterns:\n"
            "  - type: hierarchy\n"
            "    table: cmm_resource\n"
            "    id_column: id\n"
            "    parent_column: parent_resource_id\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        relevant = ["polestar.cmm_resource"]
        all_tables = ["polestar.cmm_resource", "polestar.core_config_prop"]

        result = _supplement_eav_tables(relevant, all_tables, "test_db")

        assert result == ["polestar.cmm_resource"]


class TestSchemaAnalyzerSynonymsAllowedTables:
    """schema_analyzer에서 synonyms 테이블이 allowed_tables에 동적으로 추가되는지 검증한다."""

    @pytest.mark.asyncio
    async def test_synonyms_added_to_allowed_tables(self, sample_state, mock_config):
        # 1. State/DB 설정
        db_id = "polestar_test"
        sample_state["active_db_id"] = db_id
        # D-051(Plan 57 §4.★): 유사어 동적 보완은 이번 질의 용어와 매칭될 때만 적용된다.
        # cmm_resource_system의 유사어 "시스템명"이 질의에 등장해야 allowed_tables에 보완된다.
        sample_state["parsed_requirements"] = {
            "query_targets": ["서버", "CPU"],
            "original_query": "모든 서버의 시스템명 조회"
        }

        # 2. Mock DB Client 및 SchemaInfo 구성
        schema_info_obj = SchemaInfo()
        schema_info_obj.tables = {
            "polestar.cmm_resource": TableInfo(
                name="polestar.cmm_resource",
                columns=[ColumnInfo(name="id", data_type="integer")]
            ),
            "polestar.core_config_prop": TableInfo(
                name="polestar.core_config_prop",
                columns=[ColumnInfo(name="configuration_id", data_type="integer")]
            ),
            "polestar.cmm_resource_system": TableInfo(
                name="polestar.cmm_resource_system",
                columns=[ColumnInfo(name="systemname", data_type="varchar")]
            ),
        }
        
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        
        # 3. Cache Manager 모의 객체
        schema_dict = {
            "tables": {
                "polestar.cmm_resource": {"columns": [{"name": "id", "type": "integer"}]},
                "polestar.core_config_prop": {"columns": [{"name": "configuration_id", "type": "integer"}]},
                "polestar.cmm_resource_system": {"columns": [{"name": "systemname", "type": "varchar"}]},
            },
            "relationships": []
        }
        mock_cache_mgr = AsyncMock()
        # get_schema_or_fetch는 (schema_dict, cache_hit, cache_source, descriptions, synonyms) 5-tuple 반환
        mock_cache_mgr.get_schema_or_fetch.return_value = (
            schema_dict,
            True,
            "메모리",
            {},
            {},
        )

        mock_cache_mgr.get_synonyms.return_value = {
            "cmm_resource_system.systemname": ["시스템명"]
        }

        # 4. LLM 모의 객체
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="polestar.cmm_resource, polestar.cmm_resource_system")

        # 5. _load_manual_profile 패치
        mock_profile = {
            "source": "manual",
            "allowed_tables": ["cmm_resource"]
        }

        @asynccontextmanager
        async def mock_db_context(client):
            yield client

        with patch("src.nodes.schema_analyzer.get_db_client", return_value=mock_db_context(mock_client)), \
             patch("src.nodes.schema_analyzer.get_cache_manager", return_value=mock_cache_mgr), \
             patch("src.nodes.schema_analyzer._load_manual_profile", return_value=mock_profile):
            
            result = await schema_analyzer(sample_state, llm=mock_llm, app_config=mock_config)

        # 6. 검증
        assert "relevant_tables" in result
        assert "polestar.cmm_resource" in result["relevant_tables"]
        # cmm_resource_system의 유사어 "시스템명"이 질의에 등장하므로(게이트 통과)
        # allowed_tables에 보완되어 필터링되지 않아야 함 (D-051 게이트 동작)
        assert "polestar.cmm_resource_system" in result["relevant_tables"]

    @pytest.mark.asyncio
    async def test_unmatched_synonym_table_not_supplemented(
        self, sample_state, mock_config
    ):
        """유사어가 이번 질의에 등장하지 않으면 그 테이블은 보완되지 않는다 (D-051 폭증 회귀 차단).

        게이트 부재 시 등록된 유사어 전 테이블이 _allowed로 유입되어 relevant·프롬프트가
        폭증했다(실측: b0 _allowed 5→407). 질의 무관 유사어 테이블이 들어오지 않음을 확인한다.
        """
        db_id = "polestar_test"
        sample_state["active_db_id"] = db_id
        # 질의에 "시스템명"이 없음 → cmm_resource_system은 보완 대상이 아님
        sample_state["parsed_requirements"] = {
            "query_targets": ["서버", "CPU"],
            "original_query": "모든 서버의 CPU 조회",
        }

        schema_dict = {
            "tables": {
                "polestar.cmm_resource": {"columns": [{"name": "id", "type": "integer"}]},
                "polestar.cmm_resource_system": {
                    "columns": [{"name": "systemname", "type": "varchar"}]
                },
            },
            "relationships": [],
        }
        mock_cache_mgr = AsyncMock()
        mock_cache_mgr.get_schema_or_fetch.return_value = (
            schema_dict, True, "메모리", {}, {},
        )
        # cmm_resource_system에 유사어가 등록돼 있으나, 질의에 그 유사어("시스템명")가 없음
        mock_cache_mgr.get_synonyms.return_value = {
            "cmm_resource_system.systemname": ["시스템명"]
        }

        mock_client = AsyncMock()
        mock_llm = AsyncMock()
        # LLM이 환각으로 cmm_resource_system까지 선택해도, allowed_tables 필터에서 제거돼야 함
        mock_llm.ainvoke.return_value = MagicMock(
            content="polestar.cmm_resource, polestar.cmm_resource_system"
        )
        mock_profile = {"source": "manual", "allowed_tables": ["cmm_resource"]}

        @asynccontextmanager
        async def mock_db_context(client):
            yield client

        with patch("src.nodes.schema_analyzer.get_db_client", return_value=mock_db_context(mock_client)), \
             patch("src.nodes.schema_analyzer.get_cache_manager", return_value=mock_cache_mgr), \
             patch("src.nodes.schema_analyzer._load_manual_profile", return_value=mock_profile):
            result = await schema_analyzer(sample_state, llm=mock_llm, app_config=mock_config)

        assert "polestar.cmm_resource" in result["relevant_tables"]
        # 게이트 미통과 → cmm_resource_system은 relevant에서 제외되어야 한다
        assert "polestar.cmm_resource_system" not in result["relevant_tables"]

