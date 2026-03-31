"""Fingerprint TTL 기반 Redis 캐시 최적화 단위 테스트.

Plan 26에서 추가된 기능을 검증한다:
- RedisSchemaCache.is_fingerprint_fresh()
- RedisSchemaCache.refresh_fingerprint_checked_at()
- RedisSchemaCache.save_schema()의 fingerprint_checked_at 파이프라인
- SchemaCacheManager.is_fingerprint_fresh()
- SchemaCacheManager.refresh_fingerprint_ttl()
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schema_cache.cache_manager import (
    SchemaCacheManager,
    get_cache_manager,
    reset_cache_manager,
)
from src.schema_cache.redis_cache import CACHE_FORMAT_VERSION, RedisSchemaCache


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def redis_config():
    """테스트용 RedisConfig."""
    config = MagicMock()
    config.host = "localhost"
    config.port = 6379
    config.db = 0
    config.password = ""
    config.ssl = False
    config.socket_timeout = 5
    return config


@pytest.fixture
def cache(redis_config):
    """연결되지 않은 RedisSchemaCache 인스턴스."""
    return RedisSchemaCache(redis_config)


@pytest.fixture
def mock_redis():
    """Mock Redis 클라이언트."""
    redis_mock = AsyncMock()
    redis_mock.ping = AsyncMock()
    redis_mock.hset = AsyncMock()
    redis_mock.hget = AsyncMock(return_value=None)
    redis_mock.hgetall = AsyncMock(return_value={})
    redis_mock.hlen = AsyncMock(return_value=0)
    redis_mock.hdel = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock()
    redis_mock.delete = AsyncMock()
    redis_mock.aclose = AsyncMock()
    return redis_mock


@pytest.fixture
def connected_cache(cache, mock_redis):
    """연결된 상태의 RedisSchemaCache 인스턴스."""
    cache._redis = mock_redis
    cache._connected = True
    return cache


@pytest.fixture
def sample_schema_dict():
    """테스트용 스키마 딕셔너리."""
    return {
        "tables": {
            "servers": {
                "columns": [
                    {
                        "name": "id",
                        "type": "integer",
                        "nullable": False,
                        "primary_key": True,
                        "foreign_key": False,
                        "references": None,
                    },
                    {
                        "name": "hostname",
                        "type": "varchar(255)",
                        "nullable": False,
                        "primary_key": False,
                        "foreign_key": False,
                        "references": None,
                    },
                ],
                "row_count_estimate": 100,
                "sample_data": [{"id": 1, "hostname": "web-srv-01"}],
            },
        },
        "relationships": [
            {"from": "cpu_metrics.server_id", "to": "servers.id"},
        ],
    }


@pytest.fixture(autouse=True)
def reset_singleton():
    """각 테스트 전후로 싱글톤을 리셋한다."""
    reset_cache_manager()
    yield
    reset_cache_manager()


@pytest.fixture
def mock_app_config_redis():
    """Redis 백엔드 테스트용 AppConfig."""
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = "/tmp/test_cache"
    config.schema_cache.enabled = True
    config.schema_cache.auto_generate_descriptions = True
    config.schema_cache.fingerprint_ttl_seconds = 1800
    config.redis.host = "localhost"
    config.redis.port = 6379
    config.redis.db = 0
    config.redis.password = ""
    config.redis.ssl = False
    config.redis.socket_timeout = 5
    return config


@pytest.fixture
def mock_app_config_file():
    """파일 백엔드 테스트용 AppConfig."""
    config = MagicMock()
    config.schema_cache.backend = "file"
    config.schema_cache.cache_dir = "/tmp/test_cache"
    config.schema_cache.enabled = True
    config.schema_cache.auto_generate_descriptions = False
    config.schema_cache.fingerprint_ttl_seconds = 1800
    config.redis.host = "localhost"
    config.redis.port = 6379
    config.redis.db = 0
    config.redis.password = ""
    config.redis.ssl = False
    config.redis.socket_timeout = 5
    return config


# ============================================================
# RedisSchemaCache 레벨 테스트
# ============================================================


class TestRedisSchemaCache_IsFingerprintFresh:
    """RedisSchemaCache.is_fingerprint_fresh() 메서드 테스트."""

    async def test_is_fingerprint_fresh_within_ttl(self, connected_cache, mock_redis):
        """타임스탬프가 TTL 내이면 True를 반환한다."""
        # 현재 시각에서 600초(10분) 전의 타임스탬프를 설정 (TTL 1800초 이내)
        import time

        checked_at = str(time.time() - 600)
        mock_redis.get = AsyncMock(return_value=checked_at)

        result = await connected_cache.is_fingerprint_fresh("test_db", ttl_seconds=1800)
        assert result is True

        # 올바른 키로 호출되었는지 확인
        mock_redis.get.assert_awaited_once_with("schema:test_db:fingerprint_checked_at")

    async def test_is_fingerprint_fresh_expired(self, connected_cache, mock_redis):
        """타임스탬프가 TTL을 초과하면 False를 반환한다."""
        import time

        # 현재 시각에서 2000초 전의 타임스탬프를 설정 (TTL 1800초 초과)
        checked_at = str(time.time() - 2000)
        mock_redis.get = AsyncMock(return_value=checked_at)

        result = await connected_cache.is_fingerprint_fresh("test_db", ttl_seconds=1800)
        assert result is False

    async def test_is_fingerprint_fresh_no_key(self, connected_cache, mock_redis):
        """키가 없으면(None) False를 반환한다."""
        mock_redis.get = AsyncMock(return_value=None)

        result = await connected_cache.is_fingerprint_fresh("test_db", ttl_seconds=1800)
        assert result is False

    async def test_is_fingerprint_fresh_not_connected(self, cache):
        """Redis 미연결 시 False를 반환한다."""
        result = await cache.is_fingerprint_fresh("test_db", ttl_seconds=1800)
        assert result is False

    async def test_is_fingerprint_fresh_exact_boundary(self, connected_cache, mock_redis):
        """TTL 경계값 테스트: time.time()을 mock하여 정확한 경계를 테스트한다."""
        with patch("src.schema_cache.redis_cache.time") as mock_time:
            mock_time.time.return_value = 10000.0

            # checked_at = 8201 -> elapsed = 1799 -> TTL(1800) 이내
            mock_redis.get = AsyncMock(return_value="8201.0")
            result = await connected_cache.is_fingerprint_fresh(
                "test_db", ttl_seconds=1800
            )
            assert result is True

            # checked_at = 8200 -> elapsed = 1800 -> TTL(1800)과 동일 (< 이므로 False)
            mock_redis.get = AsyncMock(return_value="8200.0")
            result = await connected_cache.is_fingerprint_fresh(
                "test_db", ttl_seconds=1800
            )
            assert result is False

    async def test_is_fingerprint_fresh_redis_error(self, connected_cache, mock_redis):
        """Redis 조회 중 예외 발생 시 False를 반환한다."""
        mock_redis.get = AsyncMock(side_effect=Exception("Redis connection lost"))

        result = await connected_cache.is_fingerprint_fresh("test_db", ttl_seconds=1800)
        assert result is False


class TestRedisSchemaCache_RefreshFingerprintCheckedAt:
    """RedisSchemaCache.refresh_fingerprint_checked_at() 메서드 테스트."""

    async def test_refresh_fingerprint_checked_at(self, connected_cache, mock_redis):
        """타임스탬프가 갱신되는지 확인한다."""
        with patch("src.schema_cache.redis_cache.time") as mock_time:
            mock_time.time.return_value = 12345.678

            await connected_cache.refresh_fingerprint_checked_at("test_db")

            mock_redis.set.assert_awaited_once_with(
                "schema:test_db:fingerprint_checked_at",
                "12345.678",
            )

    async def test_refresh_fingerprint_checked_at_not_connected(self, cache):
        """Redis 미연결 시 아무 작업도 하지 않는다 (예외 없음)."""
        # 예외 발생하지 않으면 통과
        await cache.refresh_fingerprint_checked_at("test_db")

    async def test_refresh_fingerprint_checked_at_redis_error(
        self, connected_cache, mock_redis
    ):
        """Redis 쓰기 중 예외 발생 시 조용히 경고를 남긴다."""
        mock_redis.set = AsyncMock(side_effect=Exception("Redis write error"))

        # 예외가 외부로 전파되지 않아야 한다
        await connected_cache.refresh_fingerprint_checked_at("test_db")


class TestRedisSchemaCache_SaveSchemaSetsFingerprintCheckedAt:
    """save_schema 호출 시 fingerprint_checked_at 키가 설정되는지 확인한다."""

    async def test_save_schema_sets_fingerprint_checked_at(
        self, connected_cache, mock_redis, sample_schema_dict
    ):
        """save_schema 호출 시 파이프라인에 fingerprint_checked_at가 포함된다."""
        pipe_mock = AsyncMock()
        pipe_mock.delete = MagicMock()
        pipe_mock.hset = MagicMock()
        pipe_mock.set = MagicMock()
        pipe_mock.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipe_mock)

        with patch("src.schema_cache.redis_cache.time") as mock_time:
            mock_time.time.return_value = 99999.0
            mock_time.strftime = MagicMock(return_value="2026-03-25T10:00:00")

            result = await connected_cache.save_schema(
                "test_db", sample_schema_dict, "fp123"
            )
            assert result is True

            # 파이프라인에서 fingerprint_checked_at 키가 설정되었는지 확인
            fp_ts_key = "schema:test_db:fingerprint_checked_at"
            set_calls = [
                call for call in pipe_mock.set.call_args_list
                if call.args[0] == fp_ts_key
            ]
            assert len(set_calls) == 1, (
                f"fingerprint_checked_at 키가 파이프라인에 설정되지 않았다. "
                f"실제 set 호출: {pipe_mock.set.call_args_list}"
            )
            assert set_calls[0].args[1] == "99999.0"

            # 파이프라인이 실행되었는지 확인
            pipe_mock.execute.assert_awaited_once()


# ============================================================
# SchemaCacheManager 레벨 테스트
# ============================================================


class TestCacheManager_IsFingerprintFresh:
    """SchemaCacheManager.is_fingerprint_fresh() 메서드 테스트."""

    async def test_cache_manager_is_fingerprint_fresh_redis(
        self, mock_app_config_redis
    ):
        """Redis 백엔드에서 is_fingerprint_fresh가 동작한다."""
        mgr = SchemaCacheManager(mock_app_config_redis)

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            mgr._redis_cache.is_fingerprint_fresh = AsyncMock(return_value=True)

            result = await mgr.is_fingerprint_fresh("test_db")
            assert result is True

            mgr._redis_cache.is_fingerprint_fresh.assert_awaited_once_with(
                "test_db", 1800
            )

    async def test_cache_manager_is_fingerprint_fresh_redis_returns_false(
        self, mock_app_config_redis
    ):
        """Redis 백엔드에서 TTL 만료 시 False를 반환한다."""
        mgr = SchemaCacheManager(mock_app_config_redis)

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            mgr._redis_cache.is_fingerprint_fresh = AsyncMock(return_value=False)

            result = await mgr.is_fingerprint_fresh("test_db")
            assert result is False

    async def test_cache_manager_is_fingerprint_fresh_file_backend(
        self, mock_app_config_file
    ):
        """파일 백엔드에서 항상 False를 반환한다."""
        mgr = SchemaCacheManager(mock_app_config_file)

        result = await mgr.is_fingerprint_fresh("test_db")
        assert result is False

    async def test_cache_manager_is_fingerprint_fresh_redis_disconnected(
        self, mock_app_config_redis
    ):
        """Redis 백엔드이나 연결 실패 시 False를 반환한다."""
        mgr = SchemaCacheManager(mock_app_config_redis)

        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            result = await mgr.is_fingerprint_fresh("test_db")
            assert result is False


class TestCacheManager_RefreshFingerprintTTL:
    """SchemaCacheManager.refresh_fingerprint_ttl() 메서드 테스트."""

    async def test_cache_manager_refresh_fingerprint_ttl(self, mock_app_config_redis):
        """refresh_fingerprint_ttl이 Redis로 위임된다."""
        mgr = SchemaCacheManager(mock_app_config_redis)

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            mgr._redis_cache.refresh_fingerprint_checked_at = AsyncMock()

            await mgr.refresh_fingerprint_ttl("test_db")

            mgr._redis_cache.refresh_fingerprint_checked_at.assert_awaited_once_with(
                "test_db"
            )

    async def test_cache_manager_refresh_fingerprint_ttl_file_backend(
        self, mock_app_config_file
    ):
        """파일 백엔드에서는 아무 작업도 하지 않는다."""
        mgr = SchemaCacheManager(mock_app_config_file)

        # 예외 발생하지 않으면 통과
        await mgr.refresh_fingerprint_ttl("test_db")

    async def test_cache_manager_refresh_fingerprint_ttl_redis_disconnected(
        self, mock_app_config_redis
    ):
        """Redis 연결 실패 시 아무 작업도 하지 않는다."""
        mgr = SchemaCacheManager(mock_app_config_redis)

        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            # 예외 발생하지 않으면 통과
            await mgr.refresh_fingerprint_ttl("test_db")


# ============================================================
# 설정 통합 테스트
# ============================================================


class TestFingerprintTTLConfig:
    """SchemaCacheConfig.fingerprint_ttl_seconds 설정 확인."""

    def test_default_value(self):
        """기본값이 1800초(30분)이다."""
        from src.config import SchemaCacheConfig

        config = SchemaCacheConfig()
        assert config.fingerprint_ttl_seconds == 1800

    def test_custom_value(self):
        """환경변수로 커스텀 값을 설정할 수 있다."""
        from src.config import SchemaCacheConfig

        with patch.dict(
            "os.environ", {"SCHEMA_CACHE_FINGERPRINT_TTL_SECONDS": "900"}
        ):
            config = SchemaCacheConfig()
            assert config.fingerprint_ttl_seconds == 900
