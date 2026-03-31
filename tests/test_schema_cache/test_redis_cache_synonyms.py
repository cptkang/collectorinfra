"""RedisSchemaCache 유사단어 확장 기능 테스트.

2계층 유사단어 (DB별 + 글로벌), source 태깅,
invalidate 시 synonyms 보존 등을 검증한다.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

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


# === source 태깅 테스트 ===


class TestSynonymSourceTagging:
    """유사단어 source 태깅 테스트."""

    @pytest.mark.asyncio
    async def test_save_synonyms_with_list_converts_to_tagged(self, cache, mock_redis):
        """list[str] 형태로 저장 시 source 태깅으로 자동 변환."""
        synonyms = {"servers.hostname": ["서버명", "호스트명"]}
        await cache.save_synonyms("polestar", synonyms, source="llm")

        call_args = mock_redis.hset.call_args
        mapping = call_args.kwargs.get("mapping") or call_args[1].get("mapping")
        parsed = json.loads(mapping["servers.hostname"])

        assert "words" in parsed
        assert "sources" in parsed
        assert parsed["words"] == ["서버명", "호스트명"]
        assert parsed["sources"]["서버명"] == "llm"
        assert parsed["sources"]["호스트명"] == "llm"

    @pytest.mark.asyncio
    async def test_save_synonyms_with_tagged_dict_preserved(self, cache, mock_redis):
        """이미 태깅된 dict 형태는 그대로 보존."""
        synonyms = {
            "servers.hostname": {
                "words": ["서버명", "호스트명"],
                "sources": {"서버명": "llm", "호스트명": "operator"},
            }
        }
        await cache.save_synonyms("polestar", synonyms)

        call_args = mock_redis.hset.call_args
        mapping = call_args.kwargs.get("mapping") or call_args[1].get("mapping")
        parsed = json.loads(mapping["servers.hostname"])

        assert parsed["sources"]["호스트명"] == "operator"

    @pytest.mark.asyncio
    async def test_load_synonyms_returns_words_only(self, cache, mock_redis):
        """load_synonyms는 단어 목록만 반환."""
        tagged = json.dumps({
            "words": ["서버명", "호스트명"],
            "sources": {"서버명": "llm", "호스트명": "operator"},
        })
        mock_redis.hgetall = AsyncMock(
            return_value={"servers.hostname": tagged}
        )

        result = await cache.load_synonyms("polestar")

        assert result == {"servers.hostname": ["서버명", "호스트명"]}

    @pytest.mark.asyncio
    async def test_load_synonyms_legacy_list_format(self, cache, mock_redis):
        """레거시 list 형태도 정상 로드."""
        mock_redis.hgetall = AsyncMock(
            return_value={"servers.hostname": json.dumps(["서버명", "호스트명"])}
        )

        result = await cache.load_synonyms("polestar")
        assert result == {"servers.hostname": ["서버명", "호스트명"]}

    @pytest.mark.asyncio
    async def test_load_synonyms_with_sources(self, cache, mock_redis):
        """load_synonyms_with_sources는 source 포함 반환."""
        tagged = json.dumps({
            "words": ["서버명", "호스트명"],
            "sources": {"서버명": "llm", "호스트명": "operator"},
        })
        mock_redis.hgetall = AsyncMock(
            return_value={"servers.hostname": tagged}
        )

        result = await cache.load_synonyms_with_sources("polestar")

        assert result["servers.hostname"]["words"] == ["서버명", "호스트명"]
        assert result["servers.hostname"]["sources"]["호스트명"] == "operator"

    @pytest.mark.asyncio
    async def test_add_synonyms_preserves_existing_source(self, cache, mock_redis):
        """add_synonyms 시 기존 source는 보존."""
        existing = json.dumps({
            "words": ["서버명"],
            "sources": {"서버명": "operator"},
        })
        mock_redis.hget = AsyncMock(return_value=existing)

        await cache.add_synonyms("polestar", "servers.hostname", ["호스트명"], source="llm")

        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[1] if len(call_args[1]) > 0 else call_args.args[2])

        assert "서버명" in parsed["words"]
        assert "호스트명" in parsed["words"]
        assert parsed["sources"]["서버명"] == "operator"  # 기존 보존
        assert parsed["sources"]["호스트명"] == "llm"  # 새 source

    @pytest.mark.asyncio
    async def test_remove_synonyms_removes_from_sources(self, cache, mock_redis):
        """remove_synonyms 시 sources에서도 삭제."""
        existing = json.dumps({
            "words": ["서버명", "호스트명"],
            "sources": {"서버명": "llm", "호스트명": "operator"},
        })
        mock_redis.hget = AsyncMock(return_value=existing)

        await cache.remove_synonyms("polestar", "servers.hostname", ["호스트명"])

        call_args = mock_redis.hset.call_args
        key, col, data = call_args[0]
        parsed = json.loads(data)

        assert "호스트명" not in parsed["words"]
        assert "호스트명" not in parsed["sources"]
        assert "서버명" in parsed["words"]


# === invalidate synonyms 보존 테스트 ===


class TestInvalidateDeletesDBSynonyms:
    """invalidate 시 DB별 synonyms도 삭제 테스트 (Plan 30 정책 변경).

    글로벌 synonyms만 보존하고, DB별 synonyms는 함께 삭제한다.
    """

    @pytest.mark.asyncio
    async def test_invalidate_deletes_db_synonyms(self, cache, mock_redis):
        """invalidate는 DB별 synonyms 키도 삭제한다."""
        await cache.invalidate("polestar")

        call_args = mock_redis.delete.call_args
        deleted_keys = call_args[0]

        # DB별 synonyms도 삭제 대상에 포함
        assert "schema:polestar:synonyms" in deleted_keys
        # meta, tables, relationships, descriptions도 삭제
        assert "schema:polestar:meta" in deleted_keys
        assert "schema:polestar:tables" in deleted_keys
        assert "schema:polestar:relationships" in deleted_keys
        assert "schema:polestar:descriptions" in deleted_keys
        # fingerprint_checked_at, structure_meta도 삭제
        assert "schema:polestar:fingerprint_checked_at" in deleted_keys
        assert "schema:polestar:structure_meta" in deleted_keys

    @pytest.mark.asyncio
    async def test_invalidate_all_deletes_db_synonyms(self, cache, mock_redis):
        """invalidate_all은 DB별 synonyms를 삭제하고 글로벌 사전만 보존."""
        # scan_iter가 여러 키를 반환하는 시뮬레이션
        mock_redis.scan_iter = MagicMock()
        keys = [
            "schema:polestar:meta",
            "schema:polestar:tables",
            "schema:polestar:synonyms",  # DB별 synonyms는 삭제됨
            "schema:polestar:descriptions",
        ]
        mock_redis.scan_iter.return_value = AsyncIterator(keys)

        await cache.invalidate_all()

        # delete 호출된 키들 수집
        deleted_keys = [
            call.args[0]
            for call in mock_redis.delete.call_args_list
        ]

        # DB별 synonyms도 삭제됨
        assert "schema:polestar:synonyms" in deleted_keys
        assert "schema:polestar:meta" in deleted_keys

    @pytest.mark.asyncio
    async def test_delete_synonyms_explicit(self, cache, mock_redis):
        """delete_synonyms는 명시적으로 삭제 가능."""
        await cache.delete_synonyms("polestar")

        mock_redis.delete.assert_called_with("schema:polestar:synonyms")


# === 글로벌 유사단어 테스트 ===


class TestGlobalSynonyms:
    """글로벌 유사단어 사전 테스트."""

    @pytest.mark.asyncio
    async def test_save_global_synonyms(self, cache, mock_redis):
        """글로벌 유사단어 저장."""
        synonyms = {"hostname": ["서버명", "호스트명"], "ip_address": ["IP", "아이피"]}
        await cache.save_global_synonyms(synonyms)

        call_args = mock_redis.hset.call_args
        assert call_args[0][0] == "synonyms:global"

    @pytest.mark.asyncio
    async def test_load_global_synonyms(self, cache, mock_redis):
        """글로벌 유사단어 로드."""
        mock_redis.hgetall = AsyncMock(return_value={
            "hostname": json.dumps(["서버명", "호스트명"]),
            "ip_address": json.dumps(["IP", "아이피"]),
        })

        result = await cache.load_global_synonyms()

        assert result["hostname"] == ["서버명", "호스트명"]
        assert result["ip_address"] == ["IP", "아이피"]

    @pytest.mark.asyncio
    async def test_add_global_synonym_merges(self, cache, mock_redis):
        """글로벌 유사단어 추가 시 기존과 병합."""
        mock_redis.hget = AsyncMock(
            return_value=json.dumps(["서버명"])
        )

        await cache.add_global_synonym("hostname", ["호스트명", "서버이름"])

        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[0][2])
        # 현재는 dict 형태로 저장 (words 키)
        words = parsed["words"] if isinstance(parsed, dict) else parsed
        assert "서버명" in words  # 기존 보존
        assert "호스트명" in words
        assert "서버이름" in words

    @pytest.mark.asyncio
    async def test_add_global_synonym_no_duplicates(self, cache, mock_redis):
        """글로벌 유사단어 추가 시 중복 제거."""
        mock_redis.hget = AsyncMock(
            return_value=json.dumps(["서버명", "호스트명"])
        )

        await cache.add_global_synonym("hostname", ["서버명", "새단어"])

        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[0][2])
        words = parsed["words"] if isinstance(parsed, dict) else parsed
        assert words.count("서버명") == 1  # 중복 없음

    @pytest.mark.asyncio
    async def test_remove_global_synonym(self, cache, mock_redis):
        """글로벌 유사단어 삭제."""
        mock_redis.hget = AsyncMock(
            return_value=json.dumps(["서버명", "호스트명", "서버이름"])
        )

        await cache.remove_global_synonym("hostname", ["호스트명"])

        call_args = mock_redis.hset.call_args
        parsed = json.loads(call_args[0][2])
        words = parsed["words"] if isinstance(parsed, dict) else parsed
        assert "호스트명" not in words
        assert "서버명" in words

    @pytest.mark.asyncio
    async def test_remove_global_synonym_deletes_empty(self, cache, mock_redis):
        """모든 유사단어 삭제 시 키 자체를 삭제 (description 없는 경우)."""
        mock_redis.hget = AsyncMock(
            return_value=json.dumps(["서버명"])
        )

        await cache.remove_global_synonym("hostname", ["서버명"])

        mock_redis.hdel.assert_called_with("synonyms:global", "hostname")

    @pytest.mark.asyncio
    async def test_delete_global_synonyms(self, cache, mock_redis):
        """글로벌 유사단어 전체 명시 삭제."""
        await cache.delete_global_synonyms()

        mock_redis.delete.assert_called_with("synonyms:global")

    @pytest.mark.asyncio
    async def test_invalidate_all_preserves_global(self, cache, mock_redis):
        """invalidate_all은 글로벌 사전도 보존."""
        keys = [
            "schema:polestar:meta",
            "schema:polestar:tables",
        ]
        mock_redis.scan_iter = MagicMock()
        mock_redis.scan_iter.return_value = AsyncIterator(keys)

        await cache.invalidate_all()

        # synonyms:global은 scan_iter("schema:*")에 매칭되지 않으므로 자동 보존
        deleted_keys = [
            call.args[0]
            for call in mock_redis.delete.call_args_list
        ]
        assert "synonyms:global" not in deleted_keys


# === 연결 없을 때 graceful 처리 ===


class TestDisconnectedGraceful:
    """Redis 연결 없을 때 graceful 동작 테스트."""

    @pytest.mark.asyncio
    async def test_global_synonyms_return_empty_when_disconnected(self, redis_config):
        """연결 없을 때 글로벌 유사단어 조회는 빈 dict 반환."""
        cache = RedisSchemaCache(redis_config)
        assert await cache.load_global_synonyms() == {}
        assert await cache.add_global_synonym("test", ["a"]) is False
        assert await cache.save_global_synonyms({"test": ["a"]}) is False

    @pytest.mark.asyncio
    async def test_delete_synonyms_returns_false_when_disconnected(self, redis_config):
        """연결 없을 때 삭제는 False 반환."""
        cache = RedisSchemaCache(redis_config)
        assert await cache.delete_synonyms("polestar") is False
        assert await cache.delete_global_synonyms() is False


# === 헬퍼 ===


class AsyncIterator:
    """async for를 지원하는 테스트용 이터레이터."""

    def __init__(self, items):
        self._items = items
        self._index = 0

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item
