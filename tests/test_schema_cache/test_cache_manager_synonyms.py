"""SchemaCacheManager 유사단어 확장 기능 테스트.

글로벌 유사단어, load_synonyms_with_global_fallback,
sync_global_synonyms, add/remove_synonyms 래퍼를 검증한다.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schema_cache.cache_manager import SchemaCacheManager


@pytest.fixture
def app_config():
    """테스트용 AppConfig (Redis 백엔드)."""
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = ".cache/schema"
    config.schema_cache.enabled = True
    config.redis.host = "localhost"
    config.redis.port = 6379
    config.redis.db = 0
    config.redis.password = ""
    config.redis.ssl = False
    config.redis.socket_timeout = 5
    config.multi_db.get_active_db_ids.return_value = ["polestar", "cloud_portal"]
    return config


@pytest.fixture
def mock_redis_cache():
    """Mock RedisSchemaCache."""
    cache = AsyncMock()
    cache.health_check = AsyncMock(return_value=True)
    return cache


@pytest.fixture
def manager(app_config, mock_redis_cache):
    """Redis 연결된 SchemaCacheManager."""
    mgr = SchemaCacheManager(app_config)
    mgr._redis_cache = mock_redis_cache
    mgr._redis_available = True
    return mgr


class TestAddRemoveSynonyms:
    """add_synonyms / remove_synonyms 래퍼 테스트."""

    @pytest.mark.asyncio
    async def test_add_synonyms_delegates_to_redis(self, manager, mock_redis_cache):
        """add_synonyms는 Redis 캐시에 위임."""
        mock_redis_cache.add_synonyms = AsyncMock(return_value=True)

        result = await manager.add_synonyms(
            "polestar", "servers.hostname", ["서버호스트"], source="operator"
        )

        assert result is True
        mock_redis_cache.add_synonyms.assert_called_once_with(
            "polestar", "servers.hostname", ["서버호스트"], source="operator"
        )

    @pytest.mark.asyncio
    async def test_remove_synonyms_delegates_to_redis(self, manager, mock_redis_cache):
        """remove_synonyms는 Redis 캐시에 위임."""
        mock_redis_cache.remove_synonyms = AsyncMock(return_value=True)

        result = await manager.remove_synonyms(
            "polestar", "servers.hostname", ["호스트명"]
        )

        assert result is True
        mock_redis_cache.remove_synonyms.assert_called_once_with(
            "polestar", "servers.hostname", ["호스트명"]
        )


class TestGlobalSynonymsMethods:
    """글로벌 유사단어 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_get_global_synonyms(self, manager, mock_redis_cache):
        """글로벌 유사단어 조회."""
        mock_redis_cache.load_global_synonyms = AsyncMock(
            return_value={"hostname": ["서버명"], "ip_address": ["IP"]}
        )

        result = await manager.get_global_synonyms()

        assert result == {"hostname": ["서버명"], "ip_address": ["IP"]}

    @pytest.mark.asyncio
    async def test_add_global_synonym(self, manager, mock_redis_cache):
        """글로벌 유사단어 추가."""
        mock_redis_cache.add_global_synonym = AsyncMock(return_value=True)

        result = await manager.add_global_synonym("hostname", ["서버호스트"])

        assert result is True
        mock_redis_cache.add_global_synonym.assert_called_once_with(
            "hostname", ["서버호스트"]
        )

    @pytest.mark.asyncio
    async def test_remove_global_synonym(self, manager, mock_redis_cache):
        """글로벌 유사단어 삭제."""
        mock_redis_cache.remove_global_synonym = AsyncMock(return_value=True)

        result = await manager.remove_global_synonym("hostname", ["호스트"])

        assert result is True


class TestLoadSynonymsWithGlobalFallback:
    """load_synonyms_with_global_fallback 테스트."""

    @pytest.mark.asyncio
    async def test_returns_db_synonyms_when_all_covered(self, manager, mock_redis_cache):
        """DB synonyms가 모든 컬럼을 커버하면 그대로 반환."""
        mock_redis_cache.load_synonyms = AsyncMock(
            return_value={"servers.hostname": ["서버명"]}
        )
        mock_redis_cache.load_global_synonyms = AsyncMock(return_value={})

        result = await manager.load_synonyms_with_global_fallback("polestar")

        assert result == {"servers.hostname": ["서버명"]}

    @pytest.mark.asyncio
    async def test_fallback_to_global_for_missing_columns(self, manager, mock_redis_cache):
        """DB synonyms에 없는 컬럼은 글로벌 사전에서 폴백."""
        # DB synonyms: hostname만 있음
        mock_redis_cache.load_synonyms = AsyncMock(
            return_value={"servers.hostname": ["서버명"]}
        )
        # 글로벌: ip_address 있음
        mock_redis_cache.load_global_synonyms = AsyncMock(
            return_value={"ip_address": ["IP", "아이피"]}
        )
        # 스키마: servers 테이블에 hostname, ip_address 컬럼
        mock_redis_cache.load_schema = AsyncMock(return_value={
            "tables": {
                "servers": {
                    "columns": [
                        {"name": "hostname"},
                        {"name": "ip_address"},
                    ]
                }
            },
            "relationships": [],
        })

        result = await manager.load_synonyms_with_global_fallback("polestar")

        assert result["servers.hostname"] == ["서버명"]  # DB 유지
        assert result["servers.ip_address"] == ["IP", "아이피"]  # 글로벌 폴백

    @pytest.mark.asyncio
    async def test_db_synonyms_take_precedence_over_global(self, manager, mock_redis_cache):
        """DB synonyms가 글로벌보다 우선."""
        mock_redis_cache.load_synonyms = AsyncMock(
            return_value={"servers.hostname": ["서버명"]}  # DB에 있음
        )
        mock_redis_cache.load_global_synonyms = AsyncMock(
            return_value={"hostname": ["호스트", "글로벌서버"]}  # 글로벌에도 있음
        )
        mock_redis_cache.load_schema = AsyncMock(return_value={
            "tables": {
                "servers": {"columns": [{"name": "hostname"}]}
            },
            "relationships": [],
        })

        result = await manager.load_synonyms_with_global_fallback("polestar")

        # DB synonyms 우선
        assert result["servers.hostname"] == ["서버명"]


class TestSyncGlobalSynonyms:
    """sync_global_synonyms 테스트."""

    @pytest.mark.asyncio
    async def test_syncs_db_synonyms_to_global(self, manager, mock_redis_cache):
        """DB synonyms를 글로벌 사전에 병합."""
        mock_redis_cache.load_synonyms = AsyncMock(
            return_value={
                "servers.hostname": ["서버명", "호스트명"],
                "servers.ip_address": ["IP"],
            }
        )
        mock_redis_cache.load_global_synonyms = AsyncMock(
            return_value={"hostname": ["서버명"]}  # 기존 글로벌
        )
        mock_redis_cache.add_global_synonym = AsyncMock(return_value=True)

        count = await manager.sync_global_synonyms("polestar")

        assert count >= 1
        # hostname에 "호스트명" 추가, ip_address에 "IP" 추가
        calls = mock_redis_cache.add_global_synonym.call_args_list
        col_names = [c[0][0] for c in calls]
        assert "hostname" in col_names or "ip_address" in col_names


class TestFileBackendFallback:
    """파일 백엔드 시 글로벌 유사단어 메서드 graceful 처리."""

    @pytest.mark.asyncio
    async def test_global_synonyms_return_empty_for_file_backend(self, app_config):
        """파일 백엔드에서 글로벌 유사단어 조회는 빈 dict."""
        app_config.schema_cache.backend = "file"
        mgr = SchemaCacheManager(app_config)

        result = await mgr.get_global_synonyms()
        assert result == {}

    @pytest.mark.asyncio
    async def test_add_synonyms_returns_false_for_file_backend(self, app_config):
        """파일 백엔드에서 add_synonyms는 False."""
        app_config.schema_cache.backend = "file"
        mgr = SchemaCacheManager(app_config)

        result = await mgr.add_synonyms("polestar", "servers.hostname", ["서버명"])
        assert result is False
