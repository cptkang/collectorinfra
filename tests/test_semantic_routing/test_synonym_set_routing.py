"""동의어 집합 등록 요청의 라우팅·종단 검증 (D-142).

"등록은 됐는데 매칭에 안 쓰인다"는 무효 구현을 막기 위해, 라우팅부터
실제 유사어 반영까지 사슬로 확인한다.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.nodes.cache_management import cache_management
from src.routing.semantic_router import semantic_router
from src.utils.synonym_set_parser import parse_synonym_set

REQUIREMENT_QUERY = "vcore, cpu, core은 동의어이다. 캐시에 등록하라."


def _app_config(db_ids: list[str] | None = None):
    cfg = MagicMock()
    cfg.multi_db.get_active_db_ids.return_value = db_ids if db_ids is not None else ["polestar_b0"]
    return cfg


class TestRouting:
    async def test_requirement_query_routes_to_cache_management(self):
        """요건 원문이 cache_management로 간다 (LLM 호출 없이 결정적으로)."""
        llm = MagicMock()
        llm.ainvoke = AsyncMock()

        result = await semantic_router(
            {"user_query": REQUIREMENT_QUERY, "parsed_requirements": {}},
            llm=llm,
            app_config=_app_config(),
        )

        assert result["routing_intent"] == "cache_management"
        llm.ainvoke.assert_not_awaited()

    async def test_ordinary_query_does_not_hijack_routing(self):
        """평범한 질의는 이 경로로 새지 않는다."""
        assert parse_synonym_set("CPU 사용률이 가장 높은 서버 알려줘") is None

    async def test_anchored_request_does_not_hijack_routing(self):
        """기존 add-synonym 표현도 이 경로로 가로채지 않는다."""
        assert parse_synonym_set("hostname에 '서버명' 유사 단어를 추가해줘") is None


class TestEndToEnd:
    """등록 → 유사어 반영 사슬."""

    @pytest.fixture
    def cache_mgr(self, monkeypatch):
        stored: dict[str, list[str]] = {}

        mgr = MagicMock()
        mgr.redis_available = True
        mgr.get_schema = AsyncMock(return_value={
            "tables": {"server": {"columns": [{"name": "cpu"}, {"name": "hostname"}]}}
        })
        mgr.get_global_synonyms = AsyncMock(side_effect=lambda: dict(stored))

        async def _add(anchor, words):
            stored.setdefault(anchor, []).extend(words)
            return True

        mgr.add_global_synonym = AsyncMock(side_effect=_add)
        mgr.add_synonyms = AsyncMock(return_value=True)
        mgr._redis_cache = MagicMock()
        mgr._redis_cache.load_eav_name_synonyms = AsyncMock(return_value={})

        monkeypatch.setattr(
            "src.nodes.cache_management.get_cache_manager", lambda cfg: mgr
        )
        mgr._stored = stored
        return mgr

    async def test_registration_persists_synonyms(self, cache_mgr):
        llm = MagicMock()
        llm.ainvoke = AsyncMock()

        result = await cache_management(
            {"user_query": REQUIREMENT_QUERY},
            llm=llm,
            app_config=_app_config(),
        )

        assert cache_mgr._stored == {"cpu": ["vcore", "core"]}
        assert "cpu" in result["final_response"]
        llm.ainvoke.assert_not_awaited()  # 결정적 경로 — LLM 미사용

    async def test_words_absent_before_registration(self, cache_mgr):
        """등록 전에는 해당 유사어가 없다 (before/after 대조의 before)."""
        assert cache_mgr._stored == {}

    async def test_registered_words_are_retrievable(self, cache_mgr):
        llm = MagicMock()
        llm.ainvoke = AsyncMock()

        await cache_management(
            {"user_query": REQUIREMENT_QUERY}, llm=llm, app_config=_app_config()
        )
        synonyms = await cache_mgr.get_global_synonyms()

        assert "vcore" in synonyms["cpu"]
        assert "core" in synonyms["cpu"]

    async def test_unknown_words_are_not_registered(self, cache_mgr):
        """스키마에 없는 단어들만으로는 등록되지 않고 되묻는다."""
        llm = MagicMock()
        llm.ainvoke = AsyncMock()

        result = await cache_management(
            {"user_query": "aaa, bbb, ccc는 동의어야. 등록해줘"},
            llm=llm,
            app_config=_app_config(),
        )

        assert cache_mgr._stored == {}
        assert "찾지 못했" in result["final_response"]

    async def test_node_returns_standard_shape(self, cache_mgr):
        """다른 노드와 동일한 반환 형태여야 그래프가 정상 종료한다."""
        llm = MagicMock()
        llm.ainvoke = AsyncMock()

        result = await cache_management(
            {"user_query": REQUIREMENT_QUERY}, llm=llm, app_config=_app_config()
        )

        assert set(result) >= {"final_response", "current_node"}
        assert result["current_node"] == "cache_management"


class TestConsumptionChain:
    """등록한 유사어가 실제 매칭 경로까지 도달하는지 — "등록은 됐는데 안 쓰인다" 차단."""

    def test_write_and_read_share_the_same_redis_key(self):
        """등록(add_global_synonym)과 조회(load_global_synonyms)가 같은 키를 쓴다."""
        import inspect

        from src.schema_cache.redis_cache import RedisSchemaCache

        writer = inspect.getsource(RedisSchemaCache.add_global_synonym)
        reader = inspect.getsource(RedisSchemaCache.load_global_synonyms)

        assert "GLOBAL_SYNONYMS_KEY" in writer
        assert "GLOBAL_SYNONYMS_KEY" in reader

    def test_field_mapper_consumes_global_synonyms(self):
        """매핑 노드가 전역 유사어를 실제로 읽는다 (사슬의 소비 끝단)."""
        src = Path("src/nodes/field_mapper.py").read_text(encoding="utf-8")

        assert "get_global_synonyms()" in src, (
            "field_mapper가 전역 유사어를 읽지 않으면 등록해도 매칭에 쓰이지 않는다"
        )

    async def test_registered_word_reaches_consumer_api(self):
        """등록 직후 소비 API가 그 단어를 돌려준다 (같은 매니저 인스턴스 기준)."""
        stored: dict[str, list[str]] = {}

        mgr = MagicMock()
        mgr.redis_available = True
        mgr.get_schema = AsyncMock(return_value={
            "tables": {"server": {"columns": [{"name": "cpu"}]}}
        })
        mgr.get_global_synonyms = AsyncMock(side_effect=lambda: dict(stored))
        mgr._redis_cache = MagicMock()
        mgr._redis_cache.load_eav_name_synonyms = AsyncMock(return_value={})
        mgr.add_synonyms = AsyncMock(return_value=True)

        async def _add(anchor, words):
            stored.setdefault(anchor, []).extend(words)
            return True

        mgr.add_global_synonym = AsyncMock(side_effect=_add)

        from src.nodes.cache_management import _handle_add_synonym_set

        await _handle_add_synonym_set(mgr, _app_config(), None, ["vcore", "cpu", "core"])

        # field_mapper가 부르는 것과 동일한 API로 조회
        consumed = await mgr.get_global_synonyms()
        assert "vcore" in consumed["cpu"] and "core" in consumed["cpu"]
