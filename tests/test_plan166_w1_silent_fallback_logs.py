"""plans/166 W1 X5(src) — 침묵 폴백에 경고 로그 한 줄. 반환값·흐름은 그대로다."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from src.schema_cache.redis_cache import RedisSchemaCache


def _cache_with_failing_redis() -> RedisSchemaCache:
    cache = RedisSchemaCache(MagicMock())
    cache._connected = True
    cache._redis = MagicMock()
    for name in ("hget", "hkeys", "ping"):
        setattr(cache._redis, name, AsyncMock(side_effect=ConnectionError("down")))
    return cache


async def test_redis_lookup_failure_logs_and_returns_default(caplog) -> None:
    cache = _cache_with_failing_redis()
    with caplog.at_level(logging.WARNING, logger="src.schema_cache.redis_cache"):
        assert await cache.get_db_description("db1") is None
        assert await cache.list_global_column_names() == []
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 2
    assert all(r.exc_info for r in warned)


async def test_health_check_failure_logs_and_marks_disconnected(caplog) -> None:
    cache = _cache_with_failing_redis()
    with caplog.at_level(logging.WARNING, logger="src.schema_cache.redis_cache"):
        assert await cache.health_check() is False
    assert cache._connected is False
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and not warned[0].exc_info


def test_fabrix_input_schema_failure_logs_and_returns_empty_schema(caplog) -> None:
    from src.clients.fabrix_client import FabriXAPIClient

    class _BrokenSchema:
        @staticmethod
        def model_json_schema() -> dict:
            raise RuntimeError("boom")

    class _Broken:
        input_schema = _BrokenSchema

    with caplog.at_level(logging.WARNING, logger="src.clients.fabrix_client"):
        assert FabriXAPIClient._extract_parameters(_Broken()) == {
            "type": "object", "properties": {},
        }
    assert any(r.exc_info for r in caplog.records)


def test_fabrix_input_schema_success_is_unchanged() -> None:
    from src.clients.fabrix_client import FabriXAPIClient

    class _Schema:
        @staticmethod
        def model_json_schema() -> dict:
            return {"type": "object", "properties": {"q": {"type": "string"}}}

    class _Tool:
        input_schema = _Schema

    assert FabriXAPIClient._extract_parameters(_Tool())["properties"] == {"q": {"type": "string"}}
