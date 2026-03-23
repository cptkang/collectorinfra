"""SchemaCacheManager 신규 3가지 기능 테스트.

기능 1: get_global_synonyms_full, update_global_description, get_global_description, list_global_column_names
기능 2: generate_global_synonyms
기능 3: find_similar_global_columns, reuse_synonyms
"""

from __future__ import annotations

import json
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
    config.multi_db.get_active_db_ids.return_value = ["polestar"]
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


# === 기능 1: Description 관련 메서드 ===


class TestGlobalSynonymsFull:
    """get_global_synonyms_full 래퍼 테스트."""

    @pytest.mark.asyncio
    async def test_delegates_to_redis(self, manager, mock_redis_cache):
        """Redis 캐시에 위임."""
        mock_redis_cache.load_global_synonyms_full = AsyncMock(return_value={
            "hostname": {"words": ["서버명"], "description": "서버의 호스트명"},
        })

        result = await manager.get_global_synonyms_full()

        assert result["hostname"]["description"] == "서버의 호스트명"
        mock_redis_cache.load_global_synonyms_full.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_empty_for_file_backend(self, app_config):
        """파일 백엔드에서는 빈 dict."""
        app_config.schema_cache.backend = "file"
        mgr = SchemaCacheManager(app_config)
        result = await mgr.get_global_synonyms_full()
        assert result == {}


class TestUpdateGlobalDescription:
    """update_global_description 래퍼 테스트."""

    @pytest.mark.asyncio
    async def test_delegates_to_redis(self, manager, mock_redis_cache):
        """Redis 캐시에 위임."""
        mock_redis_cache.update_global_description = AsyncMock(return_value=True)

        result = await manager.update_global_description("hostname", "설명")

        assert result is True
        mock_redis_cache.update_global_description.assert_called_once_with(
            "hostname", "설명"
        )

    @pytest.mark.asyncio
    async def test_returns_false_for_file_backend(self, app_config):
        """파일 백엔드에서는 False."""
        app_config.schema_cache.backend = "file"
        mgr = SchemaCacheManager(app_config)
        result = await mgr.update_global_description("hostname", "설명")
        assert result is False


class TestGetGlobalDescription:
    """get_global_description 래퍼 테스트."""

    @pytest.mark.asyncio
    async def test_delegates_to_redis(self, manager, mock_redis_cache):
        """Redis 캐시에 위임."""
        mock_redis_cache.get_global_description = AsyncMock(
            return_value="서버의 호스트명"
        )

        result = await manager.get_global_description("hostname")

        assert result == "서버의 호스트명"

    @pytest.mark.asyncio
    async def test_returns_none_for_file_backend(self, app_config):
        """파일 백엔드에서는 None."""
        app_config.schema_cache.backend = "file"
        mgr = SchemaCacheManager(app_config)
        result = await mgr.get_global_description("hostname")
        assert result is None


class TestListGlobalColumnNames:
    """list_global_column_names 래퍼 테스트."""

    @pytest.mark.asyncio
    async def test_delegates_to_redis(self, manager, mock_redis_cache):
        """Redis 캐시에 위임."""
        mock_redis_cache.list_global_column_names = AsyncMock(
            return_value=["hostname", "ip_address"]
        )

        result = await manager.list_global_column_names()

        assert result == ["hostname", "ip_address"]


# === 기능 2: generate_global_synonyms ===


class TestGenerateGlobalSynonyms:
    """generate_global_synonyms 테스트."""

    @pytest.mark.asyncio
    async def test_generates_and_merges(self, manager, mock_redis_cache):
        """LLM 생성 후 기존과 merge."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(
            content=json.dumps({
                "words": ["서버명", "호스트명", "서버 이름"],
                "description": "서버의 호스트명",
            })
        ))

        # 기존 글로벌 사전
        mock_redis_cache.load_global_synonyms_full = AsyncMock(return_value={
            "hostname": {"words": ["서버명"], "description": "이전 설명"},
        })
        mock_redis_cache.save_global_synonyms = AsyncMock(return_value=True)

        result = await manager.generate_global_synonyms("hostname", llm)

        assert "서버명" in result["words"]
        assert "호스트명" in result["words"]
        assert "서버 이름" in result["words"]
        # description은 새로 생성된 것 우선
        assert result["description"] == "서버의 호스트명"
        mock_redis_cache.save_global_synonyms.assert_called_once()

    @pytest.mark.asyncio
    async def test_generates_with_seed_words(self, manager, mock_redis_cache):
        """seed_words가 결과에 포함됨."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(
            content=json.dumps({
                "words": ["서버명"],
                "description": "서버 이름",
            })
        ))
        mock_redis_cache.load_global_synonyms_full = AsyncMock(return_value={})
        mock_redis_cache.save_global_synonyms = AsyncMock(return_value=True)

        result = await manager.generate_global_synonyms(
            "server_name", llm, seed_words=["호스트", "서버"]
        )

        # seed_words가 결과에 포함
        assert "호스트" in result["words"]
        assert "서버" in result["words"]
        assert "서버명" in result["words"]

    @pytest.mark.asyncio
    async def test_handles_llm_failure_with_seed_words(self, manager, mock_redis_cache):
        """LLM 실패 시 seed_words만이라도 저장."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=Exception("LLM error"))
        mock_redis_cache.save_global_synonyms = AsyncMock(return_value=True)

        result = await manager.generate_global_synonyms(
            "hostname", llm, seed_words=["서버명"]
        )

        assert result["words"] == ["서버명"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_total_failure(self, manager, mock_redis_cache):
        """LLM 실패 + seed_words 없으면 빈 결과."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=Exception("LLM error"))

        result = await manager.generate_global_synonyms("hostname", llm)

        assert result["words"] == []


# === 기능 3: find_similar_global_columns, reuse_synonyms ===


class TestFindSimilarGlobalColumns:
    """find_similar_global_columns 테스트."""

    @pytest.mark.asyncio
    async def test_finds_similar_column(self, manager, mock_redis_cache):
        """유사 컬럼 발견."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(
            content=json.dumps([
                {"column": "hostname", "reason": "둘 다 서버 식별자"}
            ])
        ))
        mock_redis_cache.load_global_synonyms_full = AsyncMock(return_value={
            "hostname": {
                "words": ["서버명", "호스트명"],
                "description": "서버의 호스트명",
            },
            "ip_address": {
                "words": ["IP"],
                "description": "IP 주소",
            },
        })

        result = await manager.find_similar_global_columns("server_name", llm)

        assert len(result) == 1
        assert result[0]["column"] == "hostname"
        assert result[0]["words"] == ["서버명", "호스트명"]
        assert result[0]["description"] == "서버의 호스트명"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_similar(self, manager, mock_redis_cache):
        """유사 컬럼 없음."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="[]"))
        mock_redis_cache.load_global_synonyms_full = AsyncMock(return_value={
            "hostname": {"words": ["서버명"], "description": ""},
        })

        result = await manager.find_similar_global_columns("cpu_usage", llm)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_global_empty(self, manager, mock_redis_cache):
        """글로벌 사전 비어있을 때."""
        llm = AsyncMock()
        mock_redis_cache.load_global_synonyms_full = AsyncMock(return_value={})

        result = await manager.find_similar_global_columns("server_name", llm)

        assert result == []
        llm.ainvoke.assert_not_called()  # LLM 호출하지 않음


class TestReuseSynonyms:
    """reuse_synonyms 테스트."""

    @pytest.mark.asyncio
    async def test_copy_mode(self, manager, mock_redis_cache):
        """copy 모드: 소스의 유사 단어를 그대로 복사."""
        mock_redis_cache.load_global_synonyms_full = AsyncMock(return_value={
            "hostname": {
                "words": ["서버명", "호스트명"],
                "description": "서버의 호스트명",
            }
        })
        mock_redis_cache.save_global_synonyms = AsyncMock(return_value=True)

        result = await manager.reuse_synonyms(
            "hostname", "server_name", mode="copy"
        )

        assert result["words"] == ["서버명", "호스트명"]
        assert result["description"] == "서버의 호스트명"
        mock_redis_cache.save_global_synonyms.assert_called_once()

        # 저장 호출 확인
        save_args = mock_redis_cache.save_global_synonyms.call_args
        saved = save_args[0][0]
        assert "server_name" in saved
        assert saved["server_name"]["words"] == ["서버명", "호스트명"]

    @pytest.mark.asyncio
    async def test_merge_mode(self, manager, mock_redis_cache):
        """merge 모드: 소스 + LLM 생성 병합."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(
            content=json.dumps({
                "words": ["서버이름", "서버명"],
                "description": "서버의 이름",
            })
        ))
        mock_redis_cache.load_global_synonyms_full = AsyncMock(return_value={
            "hostname": {
                "words": ["서버명", "호스트명"],
                "description": "서버의 호스트명",
            }
        })
        mock_redis_cache.save_global_synonyms = AsyncMock(return_value=True)

        result = await manager.reuse_synonyms(
            "hostname", "server_name", mode="merge", llm=llm
        )

        # 소스의 words + LLM 생성 words 병합 (중복 제거)
        assert "서버명" in result["words"]
        assert "호스트명" in result["words"]
        assert "서버이름" in result["words"]


class TestFileBackendGraceful:
    """파일 백엔드에서의 graceful 동작 테스트."""

    @pytest.mark.asyncio
    async def test_all_new_methods_return_defaults(self, app_config):
        """파일 백엔드에서 모든 신규 메서드가 기본값 반환."""
        app_config.schema_cache.backend = "file"
        mgr = SchemaCacheManager(app_config)

        assert await mgr.get_global_synonyms_full() == {}
        assert await mgr.update_global_description("h", "d") is False
        assert await mgr.get_global_description("h") is None
        assert await mgr.list_global_column_names() == []
