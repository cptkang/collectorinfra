"""plans/104 A-1 S5 · B-1 · invalidate_all 자산 보존 — 사람 자산을 재생성이 덮지 않는다.

- S5 ① DB 설명 출처(`schema:db_description_origin`): LLM 생성이 수동 설명을 덮지 않는다
  (API·채팅·CLI 공통 판정).
- S5 ② LLM 유사어 저장은 같은 컬럼의 비-llm 출처 단어(operator 등)를 보존하고 llm 단어만 교체한다.
- B-1 `get_all_status()`의 설명·유사어 건수 = Redis 실제 건수.
- `invalidate_all()`은 관리자 자산 키와 DB 설명 출처를 보존한다.

LLM·DB 0 · Redis는 인메모리 페이크(`tests/mocks/async_redis.py`).
"""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schema_cache.cache_manager import SchemaCacheManager
from src.schema_cache.redis_cache import RedisSchemaCache
from tests.mocks.async_redis import attach_fake_redis

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = {
    "tables": {
        "t1": {"columns": [{"name": "colA", "type": "varchar"}, {"name": "colB", "type": "int"}]},
    },
    "relationships": [],
}


def _config(tmp_path: Path, backend: str = "redis") -> MagicMock:
    config = MagicMock()
    config.schema_cache.backend = backend
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = True
    return config


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """페이크 Redis가 붙은 매니저 — 작업 디렉터리·파일 캐시는 tmp."""
    monkeypatch.chdir(tmp_path)
    mgr = SchemaCacheManager(_config(tmp_path))
    fake = attach_fake_redis(mgr._redis_cache)
    return mgr, fake


@pytest.fixture
def redis_cache():
    cache = RedisSchemaCache(MagicMock())
    fake = attach_fake_redis(cache)
    return cache, fake


# === S5 ① DB 설명 출처 ===


class TestDbDescriptionOrigin:
    async def test_llm_does_not_overwrite_manual(self, manager, caplog):
        mgr, fake = manager
        assert await mgr.save_db_description("db1", "사람이 쓴 설명", origin="manual") is True

        with caplog.at_level(logging.INFO, logger="src.schema_cache.cache_manager"):
            saved = await mgr.save_db_description("db1", "LLM 재생성 설명", origin="llm")

        assert saved is False
        assert await mgr.get_db_description("db1") == "사람이 쓴 설명"
        assert await mgr.get_db_description_origin("db1") == "manual"
        assert any("수동 설정 설명 보존" in r.getMessage() for r in caplog.records)

    async def test_default_origin_is_manual(self, manager):
        mgr, fake = manager
        assert await mgr.save_db_description("db1", "설명") is True
        assert await fake.hget(RedisSchemaCache.DB_DESCRIPTION_ORIGIN_KEY, "db1") == "manual"

    async def test_legacy_value_without_origin_is_regenerated(self, manager):
        """출처 기록이 없는 레거시 설명은 manual이 아니므로 LLM이 갱신한다."""
        mgr, fake = manager
        await fake.hset(RedisSchemaCache.DB_DESCRIPTIONS_KEY, "db1", "레거시 설명")

        assert await mgr.save_db_description("db1", "LLM 설명", origin="llm") is True
        assert await mgr.get_db_description("db1") == "LLM 설명"
        assert await mgr.get_db_description_origin("db1") == "llm"

    async def test_llm_overwrites_llm_and_manual_overwrites_llm(self, manager):
        mgr, _ = manager
        assert await mgr.save_db_description("db1", "LLM 1", origin="llm") is True
        assert await mgr.save_db_description("db1", "LLM 2", origin="llm") is True
        assert await mgr.save_db_description("db1", "사람 설명", origin="manual") is True
        assert await mgr.get_db_description("db1") == "사람 설명"
        assert await mgr.get_db_description_origin("db1") == "manual"

    async def test_delete_clears_origin(self, manager):
        mgr, fake = manager
        await mgr.save_db_description("db1", "사람 설명", origin="manual")
        assert await mgr.delete_db_description("db1") is True
        assert await fake.hget(RedisSchemaCache.DB_DESCRIPTION_ORIGIN_KEY, "db1") is None
        assert await mgr.save_db_description("db1", "LLM 설명", origin="llm") is True

    async def test_file_backend_keeps_manual(self, tmp_path, monkeypatch):
        """파일 백엔드도 같은 규칙 — 출처는 캐시 파일 필드에 남는다."""
        monkeypatch.chdir(tmp_path)
        mgr = SchemaCacheManager(_config(tmp_path, backend="file"))
        assert mgr._file_cache.save("db1", _SCHEMA, "fp") is True

        assert await mgr.save_db_description("db1", "사람 설명", origin="manual") is True
        assert await mgr.save_db_description("db1", "LLM 설명", origin="llm") is False
        assert await mgr.get_db_description("db1") == "사람 설명"
        assert await mgr.get_db_description_origin("db1") == "manual"


# === S5 ② 유사어 병합 ===


def _entry(fake_value: str) -> dict:
    return json.loads(fake_value)


class TestLlmSynonymMerge:
    async def test_operator_words_survive_llm_regeneration(self, redis_cache):
        cache, fake = redis_cache
        await cache.save_synonyms("db1", {"t1.colA": ["옛LLM"]}, source="llm")
        await cache.add_synonyms("db1", "t1.colA", ["운영자어"], source="operator")

        await cache.save_synonyms("db1", {"t1.colA": ["새LLM1", "새LLM2"]}, source="llm")

        entry = _entry(await fake.hget("schema:db1:synonyms", "t1.colA"))
        assert entry["words"] == ["운영자어", "새LLM1", "새LLM2"]
        assert entry["sources"] == {"운영자어": "operator", "새LLM1": "llm", "새LLM2": "llm"}

    async def test_duplicate_word_keeps_non_llm_tag(self, redis_cache):
        cache, fake = redis_cache
        await cache.add_synonyms("db1", "t1.colA", ["서버명"], source="operator")

        await cache.save_synonyms("db1", {"t1.colA": ["서버명", "호스트"]}, source="llm")

        entry = _entry(await fake.hget("schema:db1:synonyms", "t1.colA"))
        assert entry["words"] == ["서버명", "호스트"]
        assert entry["sources"] == {"서버명": "operator", "호스트": "llm"}

    async def test_no_existing_entry_is_byte_identical_to_legacy(self, redis_cache):
        cache, fake = redis_cache
        await cache.save_synonyms("db1", {"t1.colA": ["a", "b"]}, source="llm")
        raw = await fake.hget("schema:db1:synonyms", "t1.colA")
        assert raw == json.dumps(
            {"words": ["a", "b"], "sources": {"a": "llm", "b": "llm"}}, ensure_ascii=False
        )

    async def test_legacy_list_entry_is_replaced(self, redis_cache):
        """레거시 list 형태(출처 없음 = llm)는 종전대로 교체된다."""
        cache, fake = redis_cache
        await fake.hset("schema:db1:synonyms", "t1.colA", json.dumps(["옛단어"]))
        await cache.save_synonyms("db1", {"t1.colA": ["새단어"]}, source="llm")
        entry = _entry(await fake.hget("schema:db1:synonyms", "t1.colA"))
        assert entry == {"words": ["새단어"], "sources": {"새단어": "llm"}}

    async def test_non_llm_source_list_still_replaces(self, redis_cache):
        cache, fake = redis_cache
        await cache.add_synonyms("db1", "t1.colA", ["운영자어"], source="operator")
        await cache.save_synonyms("db1", {"t1.colA": ["시드어"]}, source="operator")
        entry = _entry(await fake.hget("schema:db1:synonyms", "t1.colA"))
        assert entry == {"words": ["시드어"], "sources": {"시드어": "operator"}}

    async def test_tagged_dict_value_is_stored_as_given(self, redis_cache):
        cache, fake = redis_cache
        await cache.add_synonyms("db1", "t1.colA", ["운영자어"], source="operator")
        tagged = {"words": ["x"], "sources": {"x": "llm"}}
        await cache.save_synonyms("db1", {"t1.colA": tagged}, source="llm")
        assert _entry(await fake.hget("schema:db1:synonyms", "t1.colA")) == tagged

    async def test_other_columns_untouched(self, redis_cache):
        cache, fake = redis_cache
        await cache.add_synonyms("db1", "t1.colB", ["운영자어"], source="operator")
        await cache.save_synonyms("db1", {"t1.colA": ["새LLM"]}, source="llm")
        entry_b = _entry(await fake.hget("schema:db1:synonyms", "t1.colB"))
        assert entry_b["sources"] == {"운영자어": "operator"}

    async def test_governance_on_keeps_preserved_word_meta(self, redis_cache):
        cache, fake = redis_cache
        old = {
            "words": ["운영자어", "옛LLM"],
            "sources": {"운영자어": "operator", "옛LLM": "llm"},
            "meta": {
                "운영자어": {"usage_count": 7, "last_used_ts": 1.0, "confidence": 0.9},
                "옛LLM": {"usage_count": 3, "last_used_ts": 1.0, "confidence": 0.1},
            },
        }
        await fake.hset("schema:db1:synonyms", "t1.colA", json.dumps(old, ensure_ascii=False))
        with patch.object(RedisSchemaCache, "_governance_enabled", return_value=True):
            await cache.save_synonyms("db1", {"t1.colA": ["새LLM"]}, source="llm")
        entry = _entry(await fake.hget("schema:db1:synonyms", "t1.colA"))
        assert entry["meta"]["운영자어"]["usage_count"] == 7
        assert entry["meta"]["새LLM"]["usage_count"] == 0
        assert "옛LLM" not in entry["meta"]

    async def test_governance_off_writes_no_meta(self, redis_cache):
        cache, fake = redis_cache
        old = {
            "words": ["운영자어"],
            "sources": {"운영자어": "operator"},
            "meta": {"운영자어": {"usage_count": 7, "last_used_ts": 1.0, "confidence": 0.9}},
        }
        await fake.hset("schema:db1:synonyms", "t1.colA", json.dumps(old, ensure_ascii=False))
        with patch.object(RedisSchemaCache, "_governance_enabled", return_value=False):
            await cache.save_synonyms("db1", {"t1.colA": ["새LLM"]}, source="llm")
        entry = _entry(await fake.hget("schema:db1:synonyms", "t1.colA"))
        assert "meta" not in entry

    async def test_manager_operator_registration_then_llm_regeneration(self, manager):
        """계획 §3.7 S5 검증 문장 — 운영자 유사어 등록 → LLM 재생성 → 운영자 단어 유지."""
        mgr, _ = manager
        assert await mgr.save_schema("db1", _SCHEMA) is True
        assert await mgr.add_synonyms("db1", "t1.colA", ["운영자어"], source="operator") is True

        assert await mgr.save_synonyms("db1", {"t1.colA": ["LLM어"], "t1.colB": ["B어"]}) is True

        synonyms = await mgr.get_synonyms("db1")
        assert synonyms["t1.colA"] == ["운영자어", "LLM어"]
        assert synonyms["t1.colB"] == ["B어"]


# === invalidate_all 자산 보존 ===


class TestInvalidateAllPreservesAdminAssets:
    async def test_admin_keys_and_origin_survive(self, redis_cache):
        cache, fake = redis_cache
        preserved = [
            "schema:db1:structure_versions",
            "schema:db1:structure_drafts",
            "schema:db1:schema_snapshot",
            "schema:db1:check_result",
            "schema:db1:registration",
            "schema:db1:description_drafts",
        ]
        for key in preserved:
            await fake.set(key, "[]")
        await fake.hset(RedisSchemaCache.DB_DESCRIPTION_ORIGIN_KEY, "db1", "manual")
        await fake.hset(RedisSchemaCache.DB_DESCRIPTIONS_KEY, "db1", "설명")
        runtime = ["schema:db1:structure_meta", "schema:db1:relationships"]
        for key in runtime:
            await fake.set(key, "{}")
        await fake.hset("schema:db1:meta", mapping={"fingerprint": "fp"})

        deleted = await cache.invalidate_all()

        assert deleted == 3
        assert await fake.exists(*preserved) == len(preserved)
        assert await fake.hget(RedisSchemaCache.DB_DESCRIPTION_ORIGIN_KEY, "db1") == "manual"
        assert await fake.exists(*runtime, "schema:db1:meta") == 0

    async def test_invalidate_single_db_key_list_unchanged(self, redis_cache):
        """DB 단위 무효화 삭제 목록은 종전과 같다 — 관리자 자산 키는 목록에 없다."""
        cache, fake = redis_cache
        await fake.set("schema:db1:structure_versions", "[]")
        await fake.set("schema:db1:structure_meta", "{}")
        await cache.invalidate("db1")
        assert await fake.exists("schema:db1:structure_versions") == 1
        assert await fake.exists("schema:db1:structure_meta") == 0


# === B-1 상태 건수 ===


class TestAllStatusCounts:
    async def test_counts_are_real_redis_hlen(self, manager):
        mgr, _ = manager
        assert await mgr.save_schema("db1", _SCHEMA) is True
        await mgr.save_descriptions("db1", {"t1.colA": "설명A", "t1.colB": "설명B"})
        await mgr.save_synonyms("db1", {"t1.colA": ["별칭"]})

        statuses = await mgr.get_all_status()

        status = next(s for s in statuses if s.db_id == "db1")
        assert status.backend == "redis"
        assert status.table_count == 1
        assert status.description_count == 2
        assert status.synonym_count == 1

    async def test_empty_counts_are_zero(self, manager):
        mgr, _ = manager
        assert await mgr.save_schema("db1", _SCHEMA) is True
        [status] = [s for s in await mgr.get_all_status() if s.db_id == "db1"]
        assert (status.description_count, status.synonym_count) == (0, 0)


# === CLI 출처 ===


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "schema_cache_cli_under_test", _ROOT / "scripts" / "schema_cache_cli.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _cli_args(**overrides):
    base = {"generate": False, "set": None, "delete": False, "db_id": "db1"}
    base.update(overrides)
    return SimpleNamespace(**base)


class TestCliOrigin:
    async def _run(self, mgr, args, description="LLM 설명"):
        cli = _load_cli()
        generator = MagicMock()
        generator.generate_db_description = AsyncMock(return_value=description)
        mgr.disconnect = AsyncMock()
        with patch("src.config.load_config", return_value=MagicMock()), \
             patch("src.schema_cache.cache_manager.get_cache_manager", return_value=mgr), \
             patch("src.llm.create_llm", return_value=MagicMock()), \
             patch("src.schema_cache.description_generator.DescriptionGenerator",
                   return_value=generator):
            await cli.cmd_db_description(args)
        return generator

    async def test_set_records_manual(self, manager):
        mgr, fake = manager
        await self._run(mgr, _cli_args(set="사람 설명"))
        assert await fake.hget(RedisSchemaCache.DB_DESCRIPTION_ORIGIN_KEY, "db1") == "manual"

    async def test_generate_records_llm(self, manager):
        mgr, fake = manager
        await mgr.save_schema("db1", _SCHEMA)
        await self._run(mgr, _cli_args(generate=True))
        assert await mgr.get_db_description("db1") == "LLM 설명"
        assert await fake.hget(RedisSchemaCache.DB_DESCRIPTION_ORIGIN_KEY, "db1") == "llm"

    async def test_generate_skips_manual_without_llm_call(self, manager, capsys):
        mgr, _ = manager
        await mgr.save_schema("db1", _SCHEMA)
        await mgr.save_db_description("db1", "사람 설명", origin="manual")

        generator = await self._run(mgr, _cli_args(generate=True))

        generator.generate_db_description.assert_not_awaited()
        assert await mgr.get_db_description("db1") == "사람 설명"
        assert "건너뜀" in capsys.readouterr().out
