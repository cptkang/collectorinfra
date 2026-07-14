"""값 검색 인덱스(E5-2) 검증: SQL 방언·검색·읽기전용 구축·캐시 저장/로드.

무회귀: 모든 기능은 flag(value_retrieval) OFF 시 호출부가 미진입하며, 여기서는
인프라 계층(방언 생성·순수 검색·읽기전용 구축·TTL 캐시)을 직접 검증한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schema_cache.redis_cache import RedisSchemaCache
from src.schema_cache.value_index import (
    build_distinct_values_sql,
    build_value_index,
    search_value_index,
)


class TestDistinctSql:
    def test_postgres_uses_limit(self):
        sql = build_distinct_values_sql("polestar.cmm_resource", "resource_type", "postgresql", 500)
        assert "SELECT DISTINCT resource_type" in sql
        assert "LIMIT 500" in sql
        assert "FETCH FIRST" not in sql

    def test_db2_uses_fetch_first(self):
        sql = build_distinct_values_sql("POLESTAR.CMM_RESOURCE", "RESOURCE_TYPE", "db2", 300)
        assert "FETCH FIRST 300 ROWS ONLY" in sql
        assert "LIMIT" not in sql

    def test_read_only_select_only(self):
        sql = build_distinct_values_sql("t", "c", "postgresql")
        assert sql.strip().upper().startswith("SELECT DISTINCT")


class TestSearch:
    def test_exact_substring_match(self):
        index = {"resource_type": ["server.Server", "server.Cpus", "server.Memory"]}
        result = search_value_index(index, ["server"])
        assert set(result["resource_type"]) == {"server.Server", "server.Cpus", "server.Memory"}

    def test_no_match_returns_empty(self):
        index = {"resource_type": ["server.Server"]}
        assert search_value_index(index, ["존재하지않음"]) == {}

    def test_empty_inputs(self):
        assert search_value_index({}, ["x"]) == {}
        assert search_value_index({"k": ["v"]}, []) == {}

    def test_short_keyword_guarded(self):
        # 1글자 키워드는 무시(가드)
        index = {"k": ["ab"]}
        assert search_value_index(index, ["a"]) == {}

    def test_fuzzy_off_does_not_approximate(self):
        index = {"eav_name": ["Hostname"]}
        # 'hostnam' 오탈자는 부분어로 'hostname'에 포함되므로 exact 경로도 잡는다;
        # 완전 다른 표기는 fuzzy OFF에서 매칭 안 됨
        assert search_value_index(index, ["호스트네임"], fuzzy=False) == {}

    def test_fuzzy_on_matches_spacing_variant(self):
        index = {"eav_name": ["메모리 사용률"]}
        result = search_value_index(index, ["메모리사용률"], fuzzy=True, min_score=0.85)
        assert result.get("eav_name") == ["메모리 사용률"]

    def test_max_per_key_caps(self):
        index = {"k": [f"server{i}" for i in range(50)]}
        result = search_value_index(index, ["server"], max_per_key=5)
        assert len(result["k"]) == 5


class TestBuildValueIndex:
    async def test_builds_from_dict_rows(self):
        client = MagicMock()
        res = MagicMock()
        res.rows = [{"resource_type": "server.Server"}, {"resource_type": "server.Cpus"}]
        client.execute_sql = AsyncMock(return_value=res)
        specs = [{"key": "resource_type", "table": "cmm_resource", "column": "resource_type", "engine": "postgresql"}]
        index = await build_value_index(client, specs)
        assert index["resource_type"] == ["server.Server", "server.Cpus"]

    async def test_builds_from_list_rows(self):
        client = MagicMock()
        res = MagicMock()
        res.rows = [["Hostname"], ["OSType"]]
        client.execute_sql = AsyncMock(return_value=res)
        specs = [{"key": "eav_name", "sql": "SELECT DISTINCT name FROM cmm_config LIMIT 1000"}]
        index = await build_value_index(client, specs)
        assert index["eav_name"] == ["Hostname", "OSType"]

    async def test_spec_failure_isolated(self):
        client = MagicMock()
        res_ok = MagicMock()
        res_ok.rows = [{"c": "ok"}]

        async def _exec(sql):
            if "boom" in sql:
                raise RuntimeError("boom table missing")
            return res_ok

        client.execute_sql = AsyncMock(side_effect=_exec)
        specs = [
            {"key": "good", "sql": "SELECT DISTINCT c FROM t LIMIT 1000"},
            {"key": "bad", "sql": "SELECT DISTINCT c FROM boom LIMIT 1000"},
        ]
        index = await build_value_index(client, specs)
        # 한 spec 실패해도 나머지는 부분 반환
        assert index.get("good") == ["ok"]
        assert "bad" not in index

    async def test_missing_key_skipped(self):
        client = MagicMock()
        client.execute_sql = AsyncMock()
        index = await build_value_index(client, [{"table": "t", "column": "c"}])
        assert index == {}
        client.execute_sql.assert_not_called()


class TestRedisValueIndexCache:
    @pytest.fixture
    def cache(self):
        config = MagicMock()
        config.host, config.port, config.db = "localhost", 6379, 0
        config.password, config.ssl, config.socket_timeout = "", False, 5
        return RedisSchemaCache(config)

    @pytest.fixture
    def fake_redis(self):
        store: dict[str, str] = {}
        r = MagicMock()

        async def _set(key, val, ex=None):
            store[key] = val

        async def _get(key):
            return store.get(key)

        r.set = AsyncMock(side_effect=_set)
        r.get = AsyncMock(side_effect=_get)
        r._store = store
        return r

    async def test_not_connected_save_false(self, cache):
        assert await cache.save_column_value_index("db1", {"k": ["v"]}) is False

    async def test_not_connected_load_none(self, cache):
        assert await cache.load_column_value_index("db1") is None

    async def test_roundtrip_with_ttl(self, cache, fake_redis):
        cache._redis = fake_redis
        cache._connected = True
        idx = {"resource_type": ["server.Server"], "eav_name": ["Hostname"]}
        assert await cache.save_column_value_index("db1", idx) is True
        # ex= TTL이 전달되었는지 확인
        _, kwargs = fake_redis.set.call_args
        assert kwargs.get("ex") == RedisSchemaCache.COLUMN_VALUE_INDEX_TTL
        loaded = await cache.load_column_value_index("db1")
        assert loaded == idx

    async def test_load_miss_returns_none(self, cache, fake_redis):
        cache._redis = fake_redis
        cache._connected = True
        assert await cache.load_column_value_index("absent") is None

    async def test_does_not_clobber_operator_map_key(self, cache, fake_redis):
        # 값 인덱스는 schema:{db}:column_value_index 키를 쓰며
        # 기존 synonyms:column_values(연산자 맵)와 분리됨을 확인
        cache._redis = fake_redis
        cache._connected = True
        await cache.save_column_value_index("db1", {"k": ["v"]})
        assert "schema:db1:column_value_index" in fake_redis._store
        assert RedisSchemaCache.COLUMN_VALUE_SYNONYMS_KEY not in fake_redis._store
