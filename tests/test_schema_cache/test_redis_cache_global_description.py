"""RedisSchemaCache 글로벌 유사단어 description 확장 테스트.

기능 1: 글로벌 유사단어에 컬럼 설명(description) 추가
- synonyms:global의 value를 {words: [...], description: "..."} 형태로 확장
- update_global_description(), get_global_description() 메서드
- 기존 list 형태와 하위 호환 유지
- list_global_column_names() 메서드
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schema_cache.redis_cache import RedisSchemaCache


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
def mock_redis():
    """Mock Redis 클라이언트."""
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def cache(redis_config, mock_redis):
    """연결된 RedisSchemaCache 인스턴스."""
    c = RedisSchemaCache(redis_config)
    c._redis = mock_redis
    c._connected = True
    return c


class TestSaveGlobalSynonymsWithDescription:
    """글로벌 유사단어 저장 시 description 지원 테스트."""

    @pytest.mark.asyncio
    async def test_save_dict_with_description(self, cache, mock_redis):
        """dict 형태(words+description) 저장."""
        synonyms = {
            "hostname": {
                "words": ["서버명", "호스트명"],
                "description": "서버의 호스트명",
            }
        }
        await cache.save_global_synonyms(synonyms)

        call_args = mock_redis.hset.call_args
        mapping = call_args.kwargs.get("mapping") or call_args[1].get("mapping")
        parsed = json.loads(mapping["hostname"])

        assert parsed["words"] == ["서버명", "호스트명"]
        assert parsed["description"] == "서버의 호스트명"

    @pytest.mark.asyncio
    async def test_save_list_converts_to_dict(self, cache, mock_redis):
        """list 형태 저장 시 dict으로 변환 (하위 호환)."""
        synonyms = {"hostname": ["서버명", "호스트명"]}
        await cache.save_global_synonyms(synonyms)

        call_args = mock_redis.hset.call_args
        mapping = call_args.kwargs.get("mapping") or call_args[1].get("mapping")
        parsed = json.loads(mapping["hostname"])

        assert parsed["words"] == ["서버명", "호스트명"]
        assert "description" not in parsed  # list에서 변환 시 description 없음


class TestLoadGlobalSynonymsBackwardCompatible:
    """글로벌 유사단어 로드 하위 호환 테스트."""

    @pytest.mark.asyncio
    async def test_load_returns_words_only_from_dict(self, cache, mock_redis):
        """dict 형태에서 words만 반환."""
        mock_redis.hgetall = AsyncMock(return_value={
            "hostname": json.dumps({
                "words": ["서버명", "호스트명"],
                "description": "서버의 호스트명",
            }),
        })

        result = await cache.load_global_synonyms()
        assert result == {"hostname": ["서버명", "호스트명"]}

    @pytest.mark.asyncio
    async def test_load_returns_words_from_legacy_list(self, cache, mock_redis):
        """레거시 list 형태에서도 정상 로드."""
        mock_redis.hgetall = AsyncMock(return_value={
            "hostname": json.dumps(["서버명", "호스트명"]),
        })

        result = await cache.load_global_synonyms()
        assert result == {"hostname": ["서버명", "호스트명"]}


class TestLoadGlobalSynonymsFull:
    """load_global_synonyms_full 테스트."""

    @pytest.mark.asyncio
    async def test_returns_full_entry_with_description(self, cache, mock_redis):
        """description 포함 전체 entry 반환."""
        mock_redis.hgetall = AsyncMock(return_value={
            "hostname": json.dumps({
                "words": ["서버명"],
                "description": "서버의 호스트명",
            }),
        })

        result = await cache.load_global_synonyms_full()
        assert result["hostname"]["words"] == ["서버명"]
        assert result["hostname"]["description"] == "서버의 호스트명"

    @pytest.mark.asyncio
    async def test_converts_legacy_list_to_dict(self, cache, mock_redis):
        """레거시 list를 dict으로 변환."""
        mock_redis.hgetall = AsyncMock(return_value={
            "hostname": json.dumps(["서버명"]),
        })

        result = await cache.load_global_synonyms_full()
        assert result["hostname"]["words"] == ["서버명"]
        assert "description" not in result["hostname"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_disconnected(self, redis_config):
        """연결 없을 때 빈 dict 반환."""
        cache = RedisSchemaCache(redis_config)
        result = await cache.load_global_synonyms_full()
        assert result == {}


class TestUpdateGlobalDescription:
    """update_global_description 테스트."""

    @pytest.mark.asyncio
    async def test_update_existing_entry(self, cache, mock_redis):
        """기존 entry의 description 업데이트."""
        mock_redis.hget = AsyncMock(return_value=json.dumps({
            "words": ["서버명", "호스트명"],
            "description": "이전 설명",
        }))

        result = await cache.update_global_description(
            "hostname", "서버의 호스트명 (FQDN)"
        )

        assert result is True
        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[0][2])
        assert parsed["words"] == ["서버명", "호스트명"]  # words 보존
        assert parsed["description"] == "서버의 호스트명 (FQDN)"

    @pytest.mark.asyncio
    async def test_update_legacy_list_entry(self, cache, mock_redis):
        """레거시 list entry에 description 추가."""
        mock_redis.hget = AsyncMock(
            return_value=json.dumps(["서버명", "호스트명"])
        )

        result = await cache.update_global_description("hostname", "새 설명")

        assert result is True
        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[0][2])
        assert parsed["words"] == ["서버명", "호스트명"]
        assert parsed["description"] == "새 설명"

    @pytest.mark.asyncio
    async def test_create_new_entry_with_description(self, cache, mock_redis):
        """없는 entry에 description 추가 (words 빈 배열)."""
        mock_redis.hget = AsyncMock(return_value=None)

        result = await cache.update_global_description(
            "new_column", "새 컬럼 설명"
        )

        assert result is True
        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[0][2])
        assert parsed["words"] == []
        assert parsed["description"] == "새 컬럼 설명"

    @pytest.mark.asyncio
    async def test_returns_false_when_disconnected(self, redis_config):
        """연결 없을 때 False 반환."""
        cache = RedisSchemaCache(redis_config)
        result = await cache.update_global_description("hostname", "test")
        assert result is False


class TestGetGlobalDescription:
    """get_global_description 테스트."""

    @pytest.mark.asyncio
    async def test_get_existing_description(self, cache, mock_redis):
        """기존 description 조회."""
        mock_redis.hget = AsyncMock(return_value=json.dumps({
            "words": ["서버명"],
            "description": "서버의 호스트명",
        }))

        result = await cache.get_global_description("hostname")
        assert result == "서버의 호스트명"

    @pytest.mark.asyncio
    async def test_get_none_when_no_description(self, cache, mock_redis):
        """description이 없는 entry."""
        mock_redis.hget = AsyncMock(return_value=json.dumps({
            "words": ["서버명"],
        }))

        result = await cache.get_global_description("hostname")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_none_for_legacy_list(self, cache, mock_redis):
        """레거시 list entry에서는 None."""
        mock_redis.hget = AsyncMock(return_value=json.dumps(["서버명"]))

        result = await cache.get_global_description("hostname")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_none_when_not_found(self, cache, mock_redis):
        """entry 자체가 없으면 None."""
        mock_redis.hget = AsyncMock(return_value=None)

        result = await cache.get_global_description("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_none_when_disconnected(self, redis_config):
        """연결 없을 때 None."""
        cache = RedisSchemaCache(redis_config)
        result = await cache.get_global_description("hostname")
        assert result is None


class TestListGlobalColumnNames:
    """list_global_column_names 테스트."""

    @pytest.mark.asyncio
    async def test_returns_sorted_column_names(self, cache, mock_redis):
        """정렬된 컬럼명 목록 반환."""
        mock_redis.hkeys = AsyncMock(
            return_value=["ip_address", "hostname", "os_type"]
        )

        result = await cache.list_global_column_names()
        assert result == ["hostname", "ip_address", "os_type"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_disconnected(self, redis_config):
        """연결 없을 때 빈 리스트."""
        cache = RedisSchemaCache(redis_config)
        result = await cache.list_global_column_names()
        assert result == []


class TestAddGlobalSynonymPreservesDescription:
    """add_global_synonym이 기존 description을 보존하는지 테스트."""

    @pytest.mark.asyncio
    async def test_preserves_description_on_add(self, cache, mock_redis):
        """단어 추가 시 description 보존."""
        mock_redis.hget = AsyncMock(return_value=json.dumps({
            "words": ["서버명"],
            "description": "서버의 호스트명",
        }))

        await cache.add_global_synonym("hostname", ["호스트"])

        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[0][2])
        assert "서버명" in parsed["words"]
        assert "호스트" in parsed["words"]
        assert parsed["description"] == "서버의 호스트명"

    @pytest.mark.asyncio
    async def test_no_description_for_new_entry(self, cache, mock_redis):
        """새 entry는 description 없음."""
        mock_redis.hget = AsyncMock(return_value=None)

        await cache.add_global_synonym("new_col", ["단어1"])

        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[0][2])
        assert parsed["words"] == ["단어1"]
        assert "description" not in parsed


class TestRemoveGlobalSynonymPreservesDescription:
    """remove_global_synonym이 description을 보존하는지 테스트."""

    @pytest.mark.asyncio
    async def test_preserves_description_when_words_remain(self, cache, mock_redis):
        """단어 삭제 후에도 description 보존."""
        mock_redis.hget = AsyncMock(return_value=json.dumps({
            "words": ["서버명", "호스트명"],
            "description": "서버의 호스트명",
        }))

        await cache.remove_global_synonym("hostname", ["호스트명"])

        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[0][2])
        assert parsed["words"] == ["서버명"]
        assert parsed["description"] == "서버의 호스트명"

    @pytest.mark.asyncio
    async def test_preserves_entry_when_only_description_remains(self, cache, mock_redis):
        """모든 단어 삭제해도 description이 있으면 entry 보존."""
        mock_redis.hget = AsyncMock(return_value=json.dumps({
            "words": ["서버명"],
            "description": "서버의 호스트명",
        }))

        await cache.remove_global_synonym("hostname", ["서버명"])

        # description이 있으므로 hdel이 아닌 hset 호출
        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[0][2])
        assert parsed["words"] == []
        assert parsed["description"] == "서버의 호스트명"
        mock_redis.hdel.assert_not_called()

    @pytest.mark.asyncio
    async def test_deletes_entry_when_empty(self, cache, mock_redis):
        """words도 description도 없으면 entry 삭제."""
        mock_redis.hget = AsyncMock(return_value=json.dumps({
            "words": ["서버명"],
        }))

        await cache.remove_global_synonym("hostname", ["서버명"])

        mock_redis.hdel.assert_called_with("synonyms:global", "hostname")
