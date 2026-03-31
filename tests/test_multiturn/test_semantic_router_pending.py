"""semantic_router의 pending 우선 라우팅 테스트."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.routing.semantic_router import semantic_router
from src.state import create_initial_state
from src.config import AppConfig, MultiDBConfig


def _make_config_with_db() -> AppConfig:
    """활성 DB가 있는 config를 생성한다."""
    config = AppConfig(
        enable_semantic_routing=True,
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )
    config.multi_db = MultiDBConfig(
        active_db_ids_csv="polestar",
    )
    return config


class TestPendingSynonymReuseRouting:
    """pending_synonym_reuse가 있으면 cache_management로 라우팅."""

    async def test_routes_to_cache_management(self):
        state = create_initial_state(user_query="재활용")
        state["pending_synonym_reuse"] = {
            "target_column": "server_name",
            "suggestions": [{"column": "hostname"}],
        }

        mock_llm = AsyncMock()
        config = _make_config_with_db()

        result = await semantic_router(state, llm=mock_llm, app_config=config)

        assert result["routing_intent"] == "cache_management"
        assert result["is_multi_db"] is False


class TestPendingSynonymRegistrationsRouting:
    """pending_synonym_registrations가 있으면 synonym_registrar로 라우팅."""

    async def test_routes_to_synonym_registrar(self):
        state = create_initial_state(user_query="전체 등록")
        state["parsed_requirements"] = {
            "synonym_registration": {"mode": "all", "indices": []},
        }
        state["pending_synonym_registrations"] = [
            {"index": 1, "field": "CPU 사용률", "column": "cpu_metrics.usage_pct", "db_id": "polestar"},
        ]

        mock_llm = AsyncMock()
        config = _make_config_with_db()

        result = await semantic_router(state, llm=mock_llm, app_config=config)

        assert result["routing_intent"] == "synonym_registration"

    async def test_empty_registrations_does_not_route(self):
        """빈 리스트일 때는 라우팅하지 않는다."""
        state = create_initial_state(user_query="서버 목록")
        state["pending_synonym_registrations"] = []

        mock_llm = AsyncMock()
        config = _make_config_with_db()

        # LLM이 호출되어야 하므로 mock 설정
        mock_response = MagicMock()
        mock_response.content = '{"intent": "data_query", "databases": [{"db_id": "polestar", "relevance_score": 0.9, "sub_query_context": "서버 목록", "user_specified": false, "reason": "test"}]}'
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await semantic_router(state, llm=mock_llm, app_config=config)

        # synonym_registration이 아닌 다른 라우팅이어야 함
        assert result["routing_intent"] != "synonym_registration"
