"""semantic_router mapped_db_ids 기반 라우팅 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.routing.semantic_router import semantic_router
from src.state import create_initial_state


def _make_state(**overrides) -> dict:
    state = create_initial_state("서버 정보 조회")
    state.update(overrides)
    return state


def _make_config():
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = ["polestar", "cloud_portal"]
    config.enable_semantic_routing = True
    return config


class TestMappedDbIdsRouting:
    """field_mapper 매핑 결과 기반 라우팅 테스트."""

    @pytest.mark.asyncio
    async def test_single_db_from_mapping(self):
        """mapped_db_ids에 단일 DB가 있으면 해당 DB로 라우팅."""
        state = _make_state(mapped_db_ids=["polestar"])
        config = _make_config()
        mock_llm = AsyncMock()

        result = await semantic_router(state, llm=mock_llm, app_config=config)

        assert len(result["target_databases"]) == 1
        assert result["target_databases"][0]["db_id"] == "polestar"
        assert result["is_multi_db"] is False
        assert result["routing_intent"] == "data_query"
        # LLM 호출 스킵 확인
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_db_from_mapping(self):
        """mapped_db_ids에 여러 DB가 있으면 멀티 DB 라우팅."""
        state = _make_state(mapped_db_ids=["polestar", "cloud_portal"])
        config = _make_config()
        mock_llm = AsyncMock()

        result = await semantic_router(state, llm=mock_llm, app_config=config)

        assert len(result["target_databases"]) == 2
        assert result["is_multi_db"] is True
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mapped_db_ids_uses_llm(self):
        """mapped_db_ids가 없으면 기존 LLM 라우팅 사용."""
        state = _make_state(mapped_db_ids=None)
        config = _make_config()
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"intent": "data_query", "databases": [{"db_id": "polestar", "relevance_score": 0.9, "sub_query_context": "서버 정보", "user_specified": false, "reason": "서버 관련"}]}'
        )

        # SchemaCacheManager import를 모킹
        mock_cache_mgr = AsyncMock()
        mock_cache_mgr.get_db_descriptions = AsyncMock(return_value={})

        with patch("src.schema_cache.cache_manager.get_cache_manager", return_value=mock_cache_mgr):
            result = await semantic_router(state, llm=mock_llm, app_config=config)

        assert len(result["target_databases"]) >= 1
        # LLM이 호출됨
        mock_llm.ainvoke.assert_called()
