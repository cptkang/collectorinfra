"""스키마 캐시 통합 테스트.

Redis가 없는 환경에서도 파일 캐시 폴백이 정상 동작하는지,
query_generator 프롬프트에 설명/유사 단어가 포함되는지 등을 검증한다.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestQueryGeneratorPromptIntegration:
    """query_generator 프롬프트에 설명/유사 단어가 포함되는지 테스트."""

    def test_format_schema_with_descriptions(self):
        """컬럼 설명이 프롬프트에 포함된다."""
        from src.nodes.query_generator import _format_schema_for_prompt

        schema_info = {
            "tables": {
                "servers": {
                    "columns": [
                        {"name": "hostname", "type": "varchar(255)"},
                        {"name": "ip_address", "type": "inet"},
                    ],
                    "sample_data": [],
                },
            },
            "relationships": [],
        }
        descriptions = {
            "servers.hostname": "서버의 호스트명 (FQDN 또는 별칭)",
            "servers.ip_address": "서버의 IP 주소 (IPv4)",
        }
        synonyms = {
            "servers.hostname": ["서버명", "서버이름", "호스트명"],
            "servers.ip_address": ["IP", "아이피", "서버IP"],
        }

        result = _format_schema_for_prompt(
            schema_info,
            column_descriptions=descriptions,
            column_synonyms=synonyms,
        )

        # 설명이 포함됨
        assert "서버의 호스트명 (FQDN 또는 별칭)" in result
        assert "서버의 IP 주소 (IPv4)" in result
        # 유사 단어가 포함됨
        assert "서버명" in result
        assert "아이피" in result

    def test_format_schema_without_descriptions(self):
        """설명이 없으면 기존 동작과 동일하다."""
        from src.nodes.query_generator import _format_schema_for_prompt

        schema_info = {
            "tables": {
                "servers": {
                    "columns": [
                        {"name": "hostname", "type": "varchar(255)"},
                    ],
                    "sample_data": [],
                },
            },
            "relationships": [],
        }

        result = _format_schema_for_prompt(schema_info)

        assert "hostname" in result
        assert "varchar(255)" in result
        # 설명이 없으므로 "--"가 없어야 함
        assert "-- " not in result

    def test_format_schema_partial_descriptions(self):
        """일부 컬럼에만 설명이 있는 경우."""
        from src.nodes.query_generator import _format_schema_for_prompt

        schema_info = {
            "tables": {
                "servers": {
                    "columns": [
                        {"name": "hostname", "type": "varchar(255)"},
                        {"name": "id", "type": "integer"},
                    ],
                    "sample_data": [],
                },
            },
            "relationships": [],
        }
        descriptions = {
            "servers.hostname": "서버 호스트명",
        }

        result = _format_schema_for_prompt(
            schema_info, column_descriptions=descriptions
        )

        assert "서버 호스트명" in result
        # id에는 설명이 없음
        lines = result.split("\n")
        id_line = [l for l in lines if "id:" in l and "integer" in l][0]
        assert "-- " not in id_line


class TestFileCacheFallbackIntegration:
    """Redis 없이 파일 캐시만으로 동작하는지 테스트."""

    async def test_file_backend_compatibility(self, tmp_path):
        """SCHEMA_CACHE_BACKEND=file 시 기존 동작과 100% 동일."""
        config = MagicMock()
        config.schema_cache.backend = "file"
        config.schema_cache.cache_dir = str(tmp_path)
        config.schema_cache.enabled = True
        config.schema_cache.auto_generate_descriptions = False
        config.redis.host = "localhost"
        config.redis.port = 6379
        config.redis.db = 0
        config.redis.password = ""
        config.redis.ssl = False
        config.redis.socket_timeout = 5

        from src.schema_cache.cache_manager import SchemaCacheManager

        mgr = SchemaCacheManager(config)

        # Redis 캐시가 생성되지 않아야 함
        assert mgr._redis_cache is None

        # 스키마 저장/로드가 파일만으로 동작해야 함
        schema = {
            "tables": {
                "test_table": {
                    "columns": [{"name": "col1", "type": "int"}],
                },
            },
            "relationships": [],
        }
        saved = await mgr.save_schema("test_db", schema, "fp_test")
        assert saved is True

        loaded = await mgr.get_schema("test_db")
        assert loaded is not None
        assert "test_table" in loaded["tables"]

        # descriptions/synonyms는 빈 딕셔너리 반환
        descs = await mgr.get_descriptions("test_db")
        assert descs == {}
        syns = await mgr.get_synonyms("test_db")
        assert syns == {}


class TestStateFieldsIntegration:
    """AgentState에 새 필드가 추가되었는지 테스트."""

    def test_state_has_column_descriptions(self):
        """AgentState에 column_descriptions 필드가 있다."""
        from src.state import create_initial_state

        state = create_initial_state("테스트 질의")
        assert "column_descriptions" in state
        assert state["column_descriptions"] == {}

    def test_state_has_column_synonyms(self):
        """AgentState에 column_synonyms 필드가 있다."""
        from src.state import create_initial_state

        state = create_initial_state("테스트 질의")
        assert "column_synonyms" in state
        assert state["column_synonyms"] == {}

    def test_state_has_routing_intent(self):
        """AgentState에 routing_intent 필드가 있다."""
        from src.state import create_initial_state

        state = create_initial_state("테스트 질의")
        assert "routing_intent" in state
        assert state["routing_intent"] is None


class TestConfigIntegration:
    """설정 통합 테스트."""

    def test_redis_config_exists(self, monkeypatch, tmp_path):
        """AppConfig에 redis 필드가 있다 (기본값 검증)."""
        # .env 파일의 REDIS_PORT 등이 기본값을 덮어쓰지 않도록
        # 작업 디렉터리를 .env가 없는 임시 경로로 변경하고 환경변수를 제거한다.
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_HOST", raising=False)
        from src.config import AppConfig, RedisConfig
        # RedisConfig()를 명시적으로 생성해야 클래스 정의 시점에 캐시된
        # 기본값(.env 반영)이 아닌 현재 환경 기준의 기본값을 얻는다.
        config = AppConfig(redis=RedisConfig())
        assert hasattr(config, "redis")
        assert config.redis.host == "localhost"
        assert config.redis.port == 6379

    def test_schema_cache_config_has_backend(self):
        """SchemaCacheConfig에 backend 필드가 있다."""
        from src.config import SchemaCacheConfig
        config = SchemaCacheConfig()
        assert config.backend == "redis"
        assert config.auto_generate_descriptions is True

    def test_schema_cache_backend_file_option(self):
        """SchemaCacheConfig backend을 file로 설정할 수 있다."""
        from src.config import SchemaCacheConfig
        config = SchemaCacheConfig(backend="file")
        assert config.backend == "file"
