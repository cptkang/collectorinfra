"""RedisSchemaCache 단위 테스트.

fakeredis를 사용하여 실제 Redis 없이 테스트한다.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schema_cache.redis_cache import CACHE_FORMAT_VERSION, RedisSchemaCache


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


class TestRedisSchemaCache:
    """RedisSchemaCache 기본 동작 테스트."""

    async def test_not_connected_returns_none(self, cache):
        """연결되지 않은 상태에서 load는 None을 반환한다."""
        result = await cache.load_schema("test_db")
        assert result is None

    async def test_not_connected_save_returns_false(self, cache, sample_schema_dict):
        """연결되지 않은 상태에서 save는 False를 반환한다."""
        result = await cache.save_schema("test_db", sample_schema_dict, "abc123")
        assert result is False

    async def test_not_connected_fingerprint_returns_none(self, cache):
        """연결되지 않은 상태에서 get_fingerprint는 None을 반환한다."""
        result = await cache.get_fingerprint("test_db")
        assert result is None

    async def test_not_connected_is_changed_returns_true(self, cache):
        """연결되지 않은 상태에서 is_changed는 True를 반환한다."""
        result = await cache.is_changed("test_db", "any_fp")
        assert result is True

    async def test_health_check_not_connected(self, cache):
        """연결되지 않은 상태에서 health_check는 False를 반환한다."""
        result = await cache.health_check()
        assert result is False

    async def test_not_connected_descriptions_returns_empty(self, cache):
        """연결되지 않은 상태에서 descriptions 로드는 빈 딕셔너리를 반환한다."""
        result = await cache.load_descriptions("test_db")
        assert result == {}

    async def test_not_connected_synonyms_returns_empty(self, cache):
        """연결되지 않은 상태에서 synonyms 로드는 빈 딕셔너리를 반환한다."""
        result = await cache.load_synonyms("test_db")
        assert result == {}

    async def test_not_connected_invalidate_returns_false(self, cache):
        """연결되지 않은 상태에서 invalidate는 False를 반환한다."""
        result = await cache.invalidate("test_db")
        assert result is False

    async def test_not_connected_invalidate_all_returns_zero(self, cache):
        """연결되지 않은 상태에서 invalidate_all는 0을 반환한다."""
        result = await cache.invalidate_all()
        assert result == 0

    async def test_not_connected_list_cached_dbs_returns_empty(self, cache):
        """연결되지 않은 상태에서 list_cached_dbs는 빈 목록을 반환한다."""
        result = await cache.list_cached_dbs()
        assert result == []


class TestRedisSchemaCacheWithMock:
    """Mock Redis 클라이언트를 사용한 테스트."""

    @pytest.fixture
    def mock_redis(self):
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
    def connected_cache(self, cache, mock_redis):
        """연결된 상태의 캐시 인스턴스."""
        cache._redis = mock_redis
        cache._connected = True
        return cache

    async def test_save_schema(self, connected_cache, mock_redis, sample_schema_dict):
        """스키마 저장이 올바르게 Redis에 기록된다."""
        pipe_mock = AsyncMock()
        pipe_mock.delete = MagicMock()
        pipe_mock.hset = MagicMock()
        pipe_mock.set = MagicMock()
        pipe_mock.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipe_mock)

        result = await connected_cache.save_schema(
            "test_db", sample_schema_dict, "fp123"
        )
        assert result is True
        pipe_mock.execute.assert_awaited_once()

    async def test_load_schema_hit(self, connected_cache, mock_redis):
        """캐시된 스키마를 정상적으로 로드한다."""
        mock_redis.hgetall = AsyncMock(side_effect=[
            # meta
            {
                "fingerprint": "fp123",
                "cached_at": "2026-03-17T10:00:00",
                "cache_version": str(CACHE_FORMAT_VERSION),
                "table_count": "1",
                "total_column_count": "2",
                "description_status": "complete",
            },
            # tables
            {
                "servers": json.dumps({
                    "columns": [{"name": "id", "type": "integer"}],
                }),
            },
        ])
        mock_redis.get = AsyncMock(return_value="[]")

        result = await connected_cache.load_schema("test_db")
        assert result is not None
        assert "tables" in result
        assert "servers" in result["tables"]

    async def test_load_schema_miss(self, connected_cache, mock_redis):
        """캐시 미스 시 None을 반환한다."""
        mock_redis.hgetall = AsyncMock(return_value={})
        result = await connected_cache.load_schema("nonexistent")
        assert result is None

    async def test_get_fingerprint(self, connected_cache, mock_redis):
        """fingerprint를 정상 조회한다."""
        mock_redis.hget = AsyncMock(return_value="abc123")
        result = await connected_cache.get_fingerprint("test_db")
        assert result == "abc123"

    async def test_is_changed_true(self, connected_cache, mock_redis):
        """fingerprint가 다르면 변경으로 판단한다."""
        mock_redis.hget = AsyncMock(return_value="old_fp")
        result = await connected_cache.is_changed("test_db", "new_fp")
        assert result is True

    async def test_is_changed_false(self, connected_cache, mock_redis):
        """fingerprint가 같으면 변경되지 않은 것으로 판단한다."""
        mock_redis.hget = AsyncMock(return_value="same_fp")
        result = await connected_cache.is_changed("test_db", "same_fp")
        assert result is False

    async def test_save_and_load_descriptions(self, connected_cache, mock_redis):
        """설명 저장 및 로드."""
        descriptions = {
            "servers.hostname": "서버의 호스트명",
            "servers.id": "서버 고유 ID",
        }
        result = await connected_cache.save_descriptions("test_db", descriptions)
        assert result is True
        mock_redis.hset.assert_awaited()

    async def test_save_and_load_synonyms(self, connected_cache, mock_redis):
        """유사 단어 저장 및 로드."""
        synonyms = {
            "servers.hostname": ["서버명", "호스트명", "서버이름"],
        }
        result = await connected_cache.save_synonyms("test_db", synonyms)
        assert result is True

    async def test_add_synonyms(self, connected_cache, mock_redis):
        """유사 단어 추가."""
        mock_redis.hget = AsyncMock(
            return_value=json.dumps(["서버명", "호스트명"])
        )
        result = await connected_cache.add_synonyms(
            "test_db", "servers.hostname", ["호스트", "server name"]
        )
        assert result is True

    async def test_remove_synonyms(self, connected_cache, mock_redis):
        """유사 단어 삭제."""
        mock_redis.hget = AsyncMock(
            return_value=json.dumps(["서버명", "호스트명", "호스트"])
        )
        result = await connected_cache.remove_synonyms(
            "test_db", "servers.hostname", ["호스트"]
        )
        assert result is True

    async def test_invalidate(self, connected_cache, mock_redis):
        """캐시 삭제."""
        result = await connected_cache.invalidate("test_db")
        assert result is True
        mock_redis.delete.assert_awaited_once()

    async def test_get_status(self, connected_cache, mock_redis):
        """캐시 상태 조회."""
        mock_redis.hgetall = AsyncMock(return_value={
            "fingerprint": "fp123",
            "cached_at": "2026-03-17",
            "table_count": "5",
            "total_column_count": "30",
            "description_status": "complete",
        })
        mock_redis.hlen = AsyncMock(side_effect=[20, 15])  # descriptions, synonyms

        status = await connected_cache.get_status("test_db")
        assert status["exists"] is True
        assert status["fingerprint"] == "fp123"
        assert status["table_count"] == 5
        assert status["description_count"] == 20
        assert status["synonym_count"] == 15


class TestRedisSchemaDBDescriptions:
    """DB 설명(db_descriptions) 기능 테스트."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis 클라이언트."""
        redis_mock = AsyncMock()
        redis_mock.ping = AsyncMock()
        redis_mock.hset = AsyncMock()
        redis_mock.hget = AsyncMock(return_value=None)
        redis_mock.hgetall = AsyncMock(return_value={})
        redis_mock.hdel = AsyncMock()
        return redis_mock

    @pytest.fixture
    def connected_cache(self, cache, mock_redis):
        """연결된 상태의 캐시 인스턴스."""
        cache._redis = mock_redis
        cache._connected = True
        return cache

    async def test_save_db_description(self, connected_cache, mock_redis):
        """DB 설명 저장이 성공한다."""
        result = await connected_cache.save_db_description(
            "polestar",
            "서버 사양, CPU/메모리/디스크 사용량을 관리하는 인프라 모니터링 DB",
        )
        assert result is True
        mock_redis.hset.assert_awaited_once_with(
            RedisSchemaCache.DB_DESCRIPTIONS_KEY,
            "polestar",
            "서버 사양, CPU/메모리/디스크 사용량을 관리하는 인프라 모니터링 DB",
        )

    async def test_load_db_descriptions(self, connected_cache, mock_redis):
        """전체 DB 설명 로드가 성공한다."""
        mock_redis.hgetall = AsyncMock(return_value={
            "polestar": "인프라 모니터링 DB",
            "cloud_portal": "클라우드 포탈 DB",
        })
        result = await connected_cache.load_db_descriptions()
        assert result == {
            "polestar": "인프라 모니터링 DB",
            "cloud_portal": "클라우드 포탈 DB",
        }
        mock_redis.hgetall.assert_awaited_once_with(
            RedisSchemaCache.DB_DESCRIPTIONS_KEY
        )

    async def test_get_db_description(self, connected_cache, mock_redis):
        """특정 DB 설명 조회가 성공한다."""
        mock_redis.hget = AsyncMock(return_value="인프라 모니터링 DB")
        result = await connected_cache.get_db_description("polestar")
        assert result == "인프라 모니터링 DB"
        mock_redis.hget.assert_awaited_once_with(
            RedisSchemaCache.DB_DESCRIPTIONS_KEY, "polestar"
        )

    async def test_get_db_description_not_found(self, connected_cache, mock_redis):
        """존재하지 않는 DB 설명은 None을 반환한다."""
        mock_redis.hget = AsyncMock(return_value=None)
        result = await connected_cache.get_db_description("nonexistent")
        assert result is None

    async def test_delete_db_description(self, connected_cache, mock_redis):
        """DB 설명 삭제가 성공한다."""
        result = await connected_cache.delete_db_description("polestar")
        assert result is True
        mock_redis.hdel.assert_awaited_once_with(
            RedisSchemaCache.DB_DESCRIPTIONS_KEY, "polestar"
        )

    async def test_not_connected_save_returns_false(self, cache):
        """연결되지 않은 상태에서 save_db_description은 False를 반환한다."""
        result = await cache.save_db_description("polestar", "test")
        assert result is False

    async def test_not_connected_load_returns_empty(self, cache):
        """연결되지 않은 상태에서 load_db_descriptions는 빈 딕셔너리를 반환한다."""
        result = await cache.load_db_descriptions()
        assert result == {}

    async def test_not_connected_get_returns_none(self, cache):
        """연결되지 않은 상태에서 get_db_description은 None을 반환한다."""
        result = await cache.get_db_description("polestar")
        assert result is None

    async def test_not_connected_delete_returns_false(self, cache):
        """연결되지 않은 상태에서 delete_db_description은 False를 반환한다."""
        result = await cache.delete_db_description("polestar")
        assert result is False
