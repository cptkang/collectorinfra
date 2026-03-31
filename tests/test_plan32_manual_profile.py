"""Plan 32: EAV 수동 프로필 설정 통합 검증 테스트.

검증 대상:
- Stream A: _load_manual_profile() 수동 프로필 로드 + 저장 보호
- Stream B: _format_structure_guide() / _generate_sql() value_joins 프롬프트 연동
- Stream C: sync_known_attributes_to_eav_synonyms() Redis 동기화
- 스트림 간 연동: known_attributes_detail 포맷 정합성, value_joins 프롬프트 삽입
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.query_generator import _format_structure_guide
from src.nodes.schema_analyzer import (
    _load_manual_profile,
    _read_existing_profile_source,
    _save_structure_profile,
)


# ===========================================================================
# 1. Stream A: _load_manual_profile 수동 프로필 로드
# ===========================================================================


class TestLoadManualProfile:
    """_load_manual_profile 수동 프로필 로드 테스트."""

    def test_load_polestar_pg_yaml(self):
        """실제 polestar_pg.yaml 파일이 올바르게 로드되는지 확인."""
        profile = _load_manual_profile("polestar_pg")
        if profile is None:
            pytest.skip("polestar_pg.yaml이 config/db_profiles/ 에 없습니다.")

        # source가 manual이므로 로드되어야 함
        assert profile is not None
        assert "patterns" in profile
        assert "query_guide" in profile

        # EAV 패턴 존재
        eav_patterns = [p for p in profile["patterns"] if p["type"] == "eav"]
        assert len(eav_patterns) >= 1

        eav = eav_patterns[0]
        assert eav["entity_table"] == "cmm_resource"
        assert eav["config_table"] == "core_config_prop"
        assert eav["attribute_column"] == "name"
        assert eav["value_column"] == "stringvalue_short"

    def test_known_attributes_detail_format(self):
        """known_attributes_detail이 올바른 포맷으로 변환되는지 확인."""
        profile = _load_manual_profile("polestar_pg")
        if profile is None:
            pytest.skip("polestar_pg.yaml이 config/db_profiles/ 에 없습니다.")

        eav_patterns = [p for p in profile["patterns"] if p["type"] == "eav"]
        eav = eav_patterns[0]

        # known_attributes는 문자열 리스트로 변환
        assert isinstance(eav["known_attributes"], list)
        assert all(isinstance(a, str) for a in eav["known_attributes"])
        assert "OSType" in eav["known_attributes"]

        # known_attributes_detail은 원본 객체 리스트 보존
        assert "known_attributes_detail" in eav
        detail = eav["known_attributes_detail"]
        assert isinstance(detail, list)
        assert all(isinstance(d, dict) for d in detail)

        # 각 객체에 name, synonyms 키 존재
        for d in detail:
            assert "name" in d
            assert "synonyms" in d
            assert isinstance(d["synonyms"], list)

    def test_value_joins_loaded(self):
        """value_joins가 올바르게 로드되는지 확인."""
        profile = _load_manual_profile("polestar_pg")
        if profile is None:
            pytest.skip("polestar_pg.yaml이 config/db_profiles/ 에 없습니다.")

        eav_patterns = [p for p in profile["patterns"] if p["type"] == "eav"]
        eav = eav_patterns[0]

        assert "value_joins" in eav
        vjs = eav["value_joins"]
        assert len(vjs) >= 2

        # 각 value_join에 필수 키 확인
        for vj in vjs:
            assert "eav_attribute" in vj
            assert "eav_value_column" in vj
            assert "entity_column" in vj

        # Hostname 조인 존재 확인
        hostnames = [vj for vj in vjs if vj["eav_attribute"] == "Hostname"]
        assert len(hostnames) == 1
        assert hostnames[0]["entity_column"] == "hostname"

    def test_returns_none_for_auto_source(self, tmp_path):
        """source가 auto인 프로필은 None을 반환."""
        yaml_content = "source: auto\npatterns:\n  - type: eav\n"
        yaml_path = tmp_path / "test_auto.yaml"
        yaml_path.write_text(yaml_content)

        _real_join = os.path.join
        with patch(
            "src.nodes.schema_analyzer.os.path.join",
            side_effect=lambda *args: _real_join(str(tmp_path), args[-1]),
        ):
            result = _load_manual_profile("test_auto")

        assert result is None

    def test_returns_none_when_file_not_exists(self):
        """프로필 파일이 없으면 None을 반환."""
        result = _load_manual_profile("nonexistent_db_12345")
        assert result is None


# ===========================================================================
# 2. Stream A: _save_structure_profile 저장 보호
# ===========================================================================


class TestSaveStructureProfileProtection:
    """_save_structure_profile의 manual 프로필 보호 테스트."""

    @pytest.mark.asyncio
    async def test_manual_profile_not_overwritten(self, tmp_path):
        """source: manual인 기존 파일은 덮어쓰지 않음."""
        import yaml

        profiles_dir = tmp_path / "db_profiles"
        profiles_dir.mkdir()
        yaml_path = profiles_dir / "test_db.yaml"
        yaml_path.write_text(
            yaml.dump({"source": "manual", "patterns": [{"type": "eav"}]})
        )

        structure_meta = {
            "patterns": [{"type": "eav", "entity_table": "NEW_TABLE"}],
            "query_guide": "new guide",
        }
        cache_mgr = AsyncMock()
        cache_mgr.save_schema = AsyncMock()

        _real_join = os.path.join

        with patch(
            "src.nodes.schema_analyzer.os.path.join",
            side_effect=lambda *args: _real_join(str(tmp_path), *args[1:]),
        ):
            with patch("src.nodes.schema_analyzer.os.makedirs"):
                await _save_structure_profile("test_db", structure_meta, cache_mgr)

        # YAML 파일이 변경되지 않았는지 확인
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        assert data["source"] == "manual"
        # NEW_TABLE이 아닌 원래 내용 유지
        assert data["patterns"][0].get("entity_table") is None

    @pytest.mark.asyncio
    async def test_auto_profile_overwritten(self, tmp_path):
        """source: auto인 기존 파일은 덮어쓸 수 있음."""
        import yaml

        # _save_structure_profile은 os.path.join("config", "db_profiles")를 사용
        # 이를 tmp_path로 리다이렉트
        profiles_dir = str(tmp_path)
        yaml_path = tmp_path / "test_db.yaml"
        yaml_path.write_text(
            yaml.dump({"source": "auto", "patterns": [{"type": "eav"}]})
        )

        structure_meta = {
            "patterns": [{"type": "eav", "entity_table": "NEW_TABLE"}],
            "query_guide": "new guide",
        }
        cache_mgr = AsyncMock()
        cache_mgr.save_schema = AsyncMock()

        _real_join = os.path.join

        def _redirect_join(*args):
            # "config" + "db_profiles" -> tmp_path, db_id.yaml -> tmp_path/db_id.yaml
            result = _real_join(*args)
            if "config" in args and "db_profiles" in args:
                return profiles_dir
            if result.endswith(".yaml") or result.endswith(".json"):
                filename = os.path.basename(result)
                return _real_join(profiles_dir, filename)
            return result

        with patch(
            "src.nodes.schema_analyzer.os.path.join",
            side_effect=_redirect_join,
        ):
            with patch("src.nodes.schema_analyzer.os.makedirs"):
                await _save_structure_profile("test_db", structure_meta, cache_mgr)

        # YAML 파일이 갱신되었는지 확인
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        assert data["source"] == "auto"
        assert data["patterns"][0]["entity_table"] == "NEW_TABLE"


# ===========================================================================
# 3. Stream B: value_joins 프롬프트 연동
# ===========================================================================


class TestValueJoinsPromptIntegration:
    """value_joins가 query_generator 프롬프트에 올바르게 삽입되는지 검증."""

    def _make_structure_meta_with_value_joins(self) -> dict:
        """value_joins가 포함된 structure_meta fixture."""
        return {
            "patterns": [
                {
                    "type": "eav",
                    "entity_table": "cmm_resource",
                    "config_table": "core_config_prop",
                    "attribute_column": "name",
                    "value_column": "stringvalue_short",
                    "value_joins": [
                        {
                            "eav_attribute": "Hostname",
                            "eav_value_column": "stringvalue_short",
                            "entity_column": "hostname",
                        },
                        {
                            "eav_attribute": "IPaddress",
                            "eav_value_column": "stringvalue_short",
                            "entity_column": "ipaddress",
                        },
                    ],
                }
            ],
            "query_guide": "Polestar DB 쿼리 가이드",
        }

    def test_format_structure_guide_includes_value_joins(self):
        """_format_structure_guide()가 value_joins 정보를 포함."""
        structure_meta = self._make_structure_meta_with_value_joins()
        guide = _format_structure_guide(structure_meta)

        assert "값 기반 조인" in guide
        assert "Hostname" in guide
        assert "IPaddress" in guide
        assert "cmm_resource" in guide
        assert "core_config_prop" in guide

    def test_format_structure_guide_without_value_joins(self):
        """value_joins가 없을 때는 해당 섹션이 포함되지 않음."""
        structure_meta = {
            "patterns": [
                {
                    "type": "eav",
                    "entity_table": "T1",
                    "config_table": "T2",
                    "attribute_column": "NAME",
                    "value_column": "VALUE",
                }
            ],
            "query_guide": "기본 가이드",
        }
        guide = _format_structure_guide(structure_meta)

        assert "값 기반 조인" not in guide
        assert "기본 가이드" in guide

    def test_value_joins_format_matches_yaml_keys(self):
        """value_joins의 키가 YAML 파일과 정확히 일치하는지 확인."""
        profile = _load_manual_profile("polestar_pg")
        if profile is None:
            pytest.skip("polestar_pg.yaml이 없습니다.")

        eav = [p for p in profile["patterns"] if p["type"] == "eav"][0]
        structure_meta = {
            "patterns": profile["patterns"],
            "query_guide": profile.get("query_guide", ""),
        }

        # _format_structure_guide가 실제 YAML 데이터로 정상 작동하는지 확인
        guide = _format_structure_guide(structure_meta)
        assert "Hostname" in guide
        assert "IPaddress" in guide


# ===========================================================================
# 4. Stream C: sync_known_attributes_to_eav_synonyms 포맷 정합성
# ===========================================================================


class TestSyncKnownAttributesFormat:
    """Stream A의 known_attributes_detail과 Stream C의 입력 포맷 정합성 검증."""

    @pytest.mark.asyncio
    async def test_known_attributes_detail_compatible_with_sync(self):
        """_load_manual_profile의 known_attributes_detail이
        sync_known_attributes_to_eav_synonyms의 입력으로 직접 사용 가능."""
        from src.schema_cache.redis_cache import RedisSchemaCache

        profile = _load_manual_profile("polestar_pg")
        if profile is None:
            pytest.skip("polestar_pg.yaml이 없습니다.")

        eav = [p for p in profile["patterns"] if p["type"] == "eav"][0]
        detail = eav["known_attributes_detail"]

        # Mock Redis 클라이언트
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.hgetall = AsyncMock(return_value={})  # 기존 synonyms 없음
        mock_redis.hset = AsyncMock()

        config = MagicMock()
        config.host = "localhost"
        config.port = 6379
        config.db = 0
        config.password = ""
        config.ssl = False
        config.socket_timeout = 5

        cache = RedisSchemaCache(config)
        cache._redis = mock_redis
        cache._connected = True

        # sync 호출 - 포맷 에러 없이 성공해야 함
        count = await cache.sync_known_attributes_to_eav_synonyms(detail)

        # 10개 known_attributes 모두 synonyms가 있으므로 10개 동기화
        assert count == len(detail)

        # hset 호출 확인 (저장이 실행됨)
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args
        key = call_args[0][0]
        assert "eav_names" in key

    def test_detail_has_required_keys(self):
        """known_attributes_detail의 각 항목에 필수 키가 있는지 확인."""
        profile = _load_manual_profile("polestar_pg")
        if profile is None:
            pytest.skip("polestar_pg.yaml이 없습니다.")

        eav = [p for p in profile["patterns"] if p["type"] == "eav"][0]
        detail = eav["known_attributes_detail"]

        for attr in detail:
            assert "name" in attr, f"'name' 키 누락: {attr}"
            assert isinstance(attr["name"], str)
            assert "synonyms" in attr, f"'synonyms' 키 누락: {attr}"
            assert isinstance(attr["synonyms"], list)
            assert len(attr["synonyms"]) > 0, f"synonyms 비어있음: {attr['name']}"


# ===========================================================================
# 5. 스트림 간 연동: schema_analyzer에서 sync 호출 확인
# ===========================================================================


class TestSchemaAnalyzerSyncIntegration:
    """schema_analyzer가 수동 프로필 로드 시 sync_known_attributes_to_eav_synonyms를 호출하는지 확인."""

    def test_manual_profile_has_detail_for_sync(self):
        """수동 프로필 로드 후 known_attributes_detail이 존재하여 sync에 전달 가능."""
        profile = _load_manual_profile("polestar_pg")
        if profile is None:
            pytest.skip("polestar_pg.yaml이 없습니다.")

        # source 제거 후 structure_meta 형태
        structure_meta = {k: v for k, v in profile.items() if k != "source"}

        # patterns에서 eav 패턴의 known_attributes_detail 확인
        eav_patterns = [
            p for p in structure_meta.get("patterns", [])
            if p.get("type") == "eav"
        ]
        assert len(eav_patterns) >= 1

        detail = eav_patterns[0].get("known_attributes_detail")
        assert detail is not None
        assert len(detail) > 0

    @pytest.mark.asyncio
    async def test_multi_db_executor_value_joins_in_structure_guide(self):
        """multi_db_executor._generate_sql()에서도 value_joins가 structure_guide에 포함."""
        from src.nodes.multi_db_executor import _generate_sql

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=MagicMock(
                content="```sql\nSELECT 1 LIMIT 1;\n```"
            )
        )

        schema_info = {
            "tables": {
                "cmm_resource": {
                    "columns": [{"name": "hostname", "type": "varchar"}]
                },
                "core_config_prop": {
                    "columns": [
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
                        "value_joins": [
                            {
                                "eav_attribute": "Hostname",
                                "eav_value_column": "stringvalue_short",
                                "entity_column": "hostname",
                            },
                        ],
                    }
                ],
                "query_guide": "",
            },
        }

        await _generate_sql(
            llm=mock_llm,
            parsed_requirements={"original_query": "서버 조회"},
            schema_info=schema_info,
            sub_query_context="서버 조회",
            default_limit=1000,
            db_engine="postgresql",
        )

        # LLM에 전달된 시스템 프롬프트에 value_joins 정보가 포함되어야 함
        call_args = mock_llm.ainvoke.call_args[0][0]
        system_msg = call_args[0].content

        assert "Hostname" in system_msg
        assert "값 기반 조인" in system_msg or "value-based join" in system_msg
