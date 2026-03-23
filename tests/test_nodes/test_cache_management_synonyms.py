"""cache_management 노드 유사단어 CRUD 테스트.

list-synonyms, add-synonym, remove-synonym, update-synonym 액션을 검증한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.cache_management import (
    _handle_add_synonym,
    _handle_invalidate,
    _handle_list_synonyms,
    _handle_remove_synonym,
    _handle_update_synonym,
)


@pytest.fixture
def app_config():
    """테스트용 AppConfig."""
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = ["polestar", "cloud_portal"]
    return config


@pytest.fixture
def cache_mgr():
    """Mock SchemaCacheManager."""
    mgr = AsyncMock()
    mgr.backend = "redis"
    mgr.ensure_redis_connected = AsyncMock(return_value=True)
    return mgr


class TestHandleListSynonyms:
    """list-synonyms 핸들러 테스트."""

    @pytest.mark.asyncio
    async def test_list_global_synonyms(self, cache_mgr, app_config):
        """글로벌 유사단어 전체 조회."""
        cache_mgr.get_global_synonyms_full = AsyncMock(
            return_value={
                "hostname": {"words": ["서버명", "호스트명"]},
                "ip_address": {"words": ["IP"]},
            }
        )

        result = await _handle_list_synonyms(cache_mgr, app_config, None, None)

        assert "글로벌 유사 단어 사전" in result
        assert "hostname" in result
        assert "서버명" in result

    @pytest.mark.asyncio
    async def test_list_global_synonyms_empty(self, cache_mgr, app_config):
        """글로벌 사전 비어있을 때."""
        cache_mgr.get_global_synonyms_full = AsyncMock(return_value={})

        result = await _handle_list_synonyms(cache_mgr, app_config, None, None)

        assert "비어 있습니다" in result

    @pytest.mark.asyncio
    async def test_list_db_synonyms(self, cache_mgr, app_config):
        """특정 DB의 유사단어 조회."""
        cache_mgr.get_synonyms = AsyncMock(
            return_value={"servers.hostname": ["서버명"]}
        )

        result = await _handle_list_synonyms(
            cache_mgr, app_config, "polestar", None
        )

        assert "polestar" in result
        assert "servers.hostname" in result

    @pytest.mark.asyncio
    async def test_list_column_synonyms(self, cache_mgr, app_config):
        """특정 컬럼의 유사단어 조회 (글로벌 + DB별)."""
        cache_mgr.get_global_synonyms_full = AsyncMock(
            return_value={
                "hostname": {
                    "words": ["서버명", "호스트"],
                    "description": "서버의 호스트명",
                }
            }
        )
        cache_mgr.get_synonyms = AsyncMock(
            return_value={"servers.hostname": ["서버명"]}
        )

        result = await _handle_list_synonyms(
            cache_mgr, app_config, None, "hostname"
        )

        assert "글로벌" in result
        assert "서버명" in result

    @pytest.mark.asyncio
    async def test_list_column_synonyms_not_found(self, cache_mgr, app_config):
        """존재하지 않는 컬럼의 유사단어 조회."""
        cache_mgr.get_global_synonyms_full = AsyncMock(return_value={})
        cache_mgr.get_synonyms = AsyncMock(return_value={})

        result = await _handle_list_synonyms(
            cache_mgr, app_config, None, "nonexistent"
        )

        assert "없습니다" in result


class TestHandleAddSynonym:
    """add-synonym 핸들러 테스트."""

    @pytest.mark.asyncio
    async def test_add_synonym_to_global_and_dbs(self, cache_mgr, app_config):
        """글로벌 + 해당 DB에 유사단어 추가."""
        cache_mgr.add_global_synonym = AsyncMock(return_value=True)
        cache_mgr.get_schema = AsyncMock(return_value={
            "tables": {
                "servers": {
                    "columns": [{"name": "hostname"}]
                }
            }
        })
        cache_mgr.add_synonyms = AsyncMock(return_value=True)

        result = await _handle_add_synonym(
            cache_mgr, app_config, None, "hostname", ["서버호스트"]
        )

        assert "추가했습니다" in result
        assert "글로벌 사전에 등록" in result
        cache_mgr.add_global_synonym.assert_called_once_with("hostname", ["서버호스트"])

    @pytest.mark.asyncio
    async def test_add_synonym_no_column(self, cache_mgr, app_config):
        """컬럼 미지정 시 에러 메시지."""
        result = await _handle_add_synonym(
            cache_mgr, app_config, None, None, ["서버호스트"]
        )
        assert "지정해야" in result

    @pytest.mark.asyncio
    async def test_add_synonym_no_words(self, cache_mgr, app_config):
        """단어 미지정 시 에러 메시지."""
        result = await _handle_add_synonym(
            cache_mgr, app_config, None, "hostname", None
        )
        assert "지정해야" in result

    @pytest.mark.asyncio
    async def test_add_synonym_specific_db(self, cache_mgr, app_config):
        """특정 DB 지정 시 해당 DB만 동기화."""
        cache_mgr.add_global_synonym = AsyncMock(return_value=True)
        cache_mgr.get_schema = AsyncMock(return_value={
            "tables": {"servers": {"columns": [{"name": "hostname"}]}}
        })
        cache_mgr.add_synonyms = AsyncMock(return_value=True)

        result = await _handle_add_synonym(
            cache_mgr, app_config, "polestar", "hostname", ["서버호스트"]
        )

        # polestar에만 add_synonyms 호출됨
        assert cache_mgr.add_synonyms.call_count == 1
        call_args = cache_mgr.add_synonyms.call_args[0]
        assert call_args[0] == "polestar"


class TestHandleRemoveSynonym:
    """remove-synonym 핸들러 테스트."""

    @pytest.mark.asyncio
    async def test_remove_synonym_from_global_and_dbs(self, cache_mgr, app_config):
        """글로벌 + DB에서 유사단어 삭제."""
        cache_mgr.remove_global_synonym = AsyncMock(return_value=True)
        cache_mgr.get_synonyms = AsyncMock(
            return_value={"servers.hostname": ["서버명", "호스트명"]}
        )
        cache_mgr.remove_synonyms = AsyncMock(return_value=True)

        result = await _handle_remove_synonym(
            cache_mgr, app_config, None, "hostname", ["호스트명"]
        )

        assert "삭제했습니다" in result
        cache_mgr.remove_global_synonym.assert_called_once()


class TestHandleUpdateSynonym:
    """update-synonym 핸들러 테스트."""

    @pytest.mark.asyncio
    async def test_update_synonym_replaces_all(self, cache_mgr, app_config):
        """유사단어 교체 (기존 전체 삭제 후 새로 설정)."""
        cache_mgr._redis_cache = MagicMock()
        cache_mgr._redis_cache.GLOBAL_SYNONYMS_KEY = "synonyms:global"
        cache_mgr._redis_cache._redis = AsyncMock()
        cache_mgr.get_global_description = AsyncMock(return_value=None)
        cache_mgr.get_synonyms = AsyncMock(
            return_value={"servers.hostname": ["서버명", "호스트명"]}
        )
        cache_mgr.save_synonyms = AsyncMock(return_value=True)

        result = await _handle_update_synonym(
            cache_mgr, app_config, None, "hostname", ["새이름1", "새이름2"]
        )

        assert "교체했습니다" in result

    @pytest.mark.asyncio
    async def test_update_synonym_no_column(self, cache_mgr, app_config):
        """컬럼 미지정 시 에러."""
        result = await _handle_update_synonym(
            cache_mgr, app_config, None, None, ["새이름"]
        )
        assert "지정해야" in result


class TestHandleInvalidatePreservesSynonyms:
    """invalidate 핸들러 응답에 synonyms 보존 안내 포함 검증."""

    @pytest.mark.asyncio
    async def test_invalidate_response_mentions_synonyms_preserved(self, cache_mgr):
        """invalidate 응답에 유사단어 보존 언급."""
        cache_mgr.invalidate = AsyncMock(return_value=True)

        result = await _handle_invalidate(cache_mgr, "polestar")

        assert "보존" in result

    @pytest.mark.asyncio
    async def test_invalidate_all_response_mentions_synonyms_preserved(self, cache_mgr):
        """invalidate_all 응답에 유사단어 보존 언급."""
        cache_mgr.invalidate_all = AsyncMock(return_value=5)

        result = await _handle_invalidate(cache_mgr, None)

        assert "보존" in result
