"""SchemaCacheManager 단위 테스트.

Redis/파일 캐시 추상화와 fallback 로직을 테스트한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schema_cache.cache_manager import (
    CacheRefreshResult,
    CacheStatus,
    SchemaCacheManager,
    get_cache_manager,
    reset_cache_manager,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    """각 테스트 전후로 싱글톤을 리셋한다."""
    reset_cache_manager()
    yield
    reset_cache_manager()


@pytest.fixture
def mock_config():
    """테스트용 AppConfig."""
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = "/tmp/test_cache"
    config.schema_cache.enabled = True
    config.schema_cache.auto_generate_descriptions = True
    config.redis.host = "localhost"
    config.redis.port = 6379
    config.redis.db = 0
    config.redis.password = ""
    config.redis.ssl = False
    config.redis.socket_timeout = 5
    return config


@pytest.fixture
def file_config():
    """파일 캐시 전용 AppConfig."""
    config = MagicMock()
    config.schema_cache.backend = "file"
    config.schema_cache.cache_dir = "/tmp/test_cache"
    config.schema_cache.enabled = True
    config.schema_cache.auto_generate_descriptions = False
    config.redis.host = "localhost"
    config.redis.port = 6379
    config.redis.db = 0
    config.redis.password = ""
    config.redis.ssl = False
    config.redis.socket_timeout = 5
    return config


@pytest.fixture
def sample_schema():
    """테스트용 스키마 딕셔너리."""
    return {
        "tables": {
            "servers": {
                "columns": [
                    {"name": "id", "type": "integer", "primary_key": True},
                    {"name": "hostname", "type": "varchar(255)"},
                ],
                "sample_data": [],
            },
        },
        "relationships": [],
    }


class TestSchemaCacheManagerInit:
    """초기화 테스트."""

    def test_redis_backend_creates_redis_cache(self, mock_config):
        """redis 백엔드는 RedisSchemaCache를 생성한다."""
        mgr = SchemaCacheManager(mock_config)
        assert mgr._redis_cache is not None
        assert mgr.backend == "redis"

    def test_file_backend_no_redis_cache(self, file_config):
        """file 백엔드는 RedisSchemaCache를 생성하지 않는다."""
        mgr = SchemaCacheManager(file_config)
        assert mgr._redis_cache is None
        assert mgr.backend == "file"


class TestSchemaCacheManagerFileFallback:
    """파일 캐시 폴백 테스트."""

    @pytest.mark.asyncio
    async def test_file_mode_get_schema_from_file(self, file_config, sample_schema):
        """file 모드에서는 파일 캐시만 사용한다."""
        mgr = SchemaCacheManager(file_config)

        with patch.object(mgr._file_cache, "get_schema", return_value=sample_schema):
            result = await mgr.get_schema("test_db")
            assert result is not None
            assert "tables" in result

    @pytest.mark.asyncio
    async def test_file_mode_save_schema(self, file_config, sample_schema):
        """file 모드에서 저장은 파일 캐시만 사용한다."""
        mgr = SchemaCacheManager(file_config)

        with patch.object(mgr._file_cache, "save", return_value=True):
            result = await mgr.save_schema("test_db", sample_schema, "fp123")
            assert result is True


class TestSchemaCacheManagerRedisWithFallback:
    """Redis 장애 시 파일 폴백 테스트."""

    async def test_redis_failure_falls_back_to_file(self, mock_config, sample_schema):
        """Redis 연결 실패 시 파일 캐시로 폴백한다."""
        mgr = SchemaCacheManager(mock_config)
        mgr._redis_available = False

        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            with patch.object(
                mgr._file_cache, "get_schema", return_value=sample_schema
            ):
                result = await mgr.get_schema("test_db")
                assert result is not None

    async def test_redis_failure_fingerprint_falls_back(self, mock_config):
        """Redis 연결 실패 시 fingerprint도 파일 캐시에서 조회한다."""
        mgr = SchemaCacheManager(mock_config)

        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            with patch.object(
                mgr._file_cache, "get_cached_fingerprint", return_value="fp_from_file"
            ):
                result = await mgr.get_fingerprint("test_db")
                assert result == "fp_from_file"


class TestSchemaCacheManagerStatus:
    """캐시 상태 관련 테스트."""

    async def test_get_status_no_cache(self, file_config):
        """캐시가 없는 DB의 상태를 조회한다."""
        mgr = SchemaCacheManager(file_config)
        with patch.object(mgr._file_cache, "load", return_value=None):
            status = await mgr.get_status("nonexistent")
            assert status.backend == "none"

    async def test_get_all_status_empty(self, file_config):
        """캐시가 없으면 빈 목록을 반환한다."""
        mgr = SchemaCacheManager(file_config)
        with patch.object(mgr._file_cache, "list_cached_dbs", return_value=[]):
            statuses = await mgr.get_all_status()
            assert statuses == []


class TestSchemaCacheManagerInvalidate:
    """캐시 무효화 테스트."""

    async def test_invalidate_file_mode(self, file_config):
        """file 모드에서 캐시를 삭제한다."""
        mgr = SchemaCacheManager(file_config)
        with patch.object(mgr._file_cache, "invalidate", return_value=True):
            result = await mgr.invalidate("test_db")
            assert result is True

    async def test_invalidate_all_file_mode(self, file_config):
        """file 모드에서 전체 캐시를 삭제한다."""
        mgr = SchemaCacheManager(file_config)
        with patch.object(mgr._file_cache, "invalidate_all", return_value=3):
            result = await mgr.invalidate_all()
            assert result == 3


class TestSchemaCacheManagerDBDescriptions:
    """DB 설명 기능 테스트."""

    async def test_get_db_descriptions_redis(self, mock_config):
        """Redis에서 DB 설명을 로드한다."""
        mgr = SchemaCacheManager(mock_config)

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            mgr._redis_cache.load_db_descriptions = AsyncMock(
                return_value={"polestar": "인프라 DB", "cloud_portal": "클라우드 DB"}
            )
            result = await mgr.get_db_descriptions()
            assert result == {"polestar": "인프라 DB", "cloud_portal": "클라우드 DB"}

    async def test_get_db_descriptions_file_fallback(self, file_config):
        """파일 캐시 폴백으로 DB 설명을 로드한다."""
        mgr = SchemaCacheManager(file_config)

        with patch.object(
            mgr._file_cache,
            "list_cached_dbs",
            return_value=[{"db_id": "polestar"}],
        ):
            with patch.object(
                mgr._file_cache,
                "load",
                return_value={"_db_description": "인프라 DB"},
            ):
                result = await mgr.get_db_descriptions()
                assert result == {"polestar": "인프라 DB"}

    async def test_get_db_description_single(self, mock_config):
        """특정 DB 설명을 조회한다."""
        mgr = SchemaCacheManager(mock_config)

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            mgr._redis_cache.get_db_description = AsyncMock(
                return_value="인프라 모니터링 DB"
            )
            result = await mgr.get_db_description("polestar")
            assert result == "인프라 모니터링 DB"

    async def test_save_db_description(self, mock_config):
        """DB 설명을 저장한다."""
        mgr = SchemaCacheManager(mock_config)

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            mgr._redis_cache.save_db_description = AsyncMock(return_value=True)
            with patch.object(mgr._file_cache, "load", return_value=None):
                result = await mgr.save_db_description("polestar", "인프라 DB")
                assert result is True
                mgr._redis_cache.save_db_description.assert_awaited_once_with(
                    "polestar", "인프라 DB"
                )

    async def test_delete_db_description(self, mock_config):
        """DB 설명을 삭제한다."""
        mgr = SchemaCacheManager(mock_config)

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            mgr._redis_cache.delete_db_description = AsyncMock(return_value=True)
            result = await mgr.delete_db_description("polestar")
            assert result is True

    async def test_get_db_description_none_when_no_cache(self, file_config):
        """캐시가 없으면 None을 반환한다."""
        mgr = SchemaCacheManager(file_config)
        with patch.object(mgr._file_cache, "load", return_value=None):
            result = await mgr.get_db_description("nonexistent")
            assert result is None


class TestGetCacheManagerSingleton:
    """싱글톤 동작 테스트."""

    def test_returns_same_instance(self, file_config):
        """같은 인스턴스를 반환한다."""
        mgr1 = get_cache_manager(file_config)
        mgr2 = get_cache_manager(file_config)
        assert mgr1 is mgr2

    def test_reset_creates_new_instance(self, file_config):
        """리셋 후 새 인스턴스를 생성한다."""
        mgr1 = get_cache_manager(file_config)
        reset_cache_manager()
        mgr2 = get_cache_manager(file_config)
        assert mgr1 is not mgr2
