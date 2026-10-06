"""plans/138 W6-a · D-305 ⑧(G-5) — 스키마 재저장이 파일 캐시의 부가 필드를 지우지 않는다.

내부망 run `20261006-152938`에서 서버 실행 상태는 컬럼 설명 698/698(Redis)인데 벤치 카탈로그(파일
캐시)는 0이었다. 원인 재현: `SchemaCacheManager.save_schema`가 Redis에는 스키마 키만 다시 쓰고(설명
해시는 그대로) 파일에는 `PersistentSchemaCache.save()`로 **파일 전체를 스키마만으로 다시 써서**
`_descriptions`·`_synonyms`·`_db_description`이 사라진다. 고친 뒤에는 기존 파일의 `schema` 밖 키를
보존한다(새로 쓰는 메타·스키마 키가 우선). Redis·DB 0 — 인메모리 페이크 Redis만 쓴다.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.schema_cache.cache_manager import SchemaCacheManager, reset_cache_manager
from src.schema_cache.persistent_cache import PersistentSchemaCache
from tests.mocks.async_redis import attach_fake_redis


def _schema(*columns: str) -> dict:
    return {
        "tables": {
            "t_asset": {
                "columns": [{"name": c, "type": "varchar"} for c in columns],
                "row_count_estimate": 3,
                "sample_data": [],
            }
        },
        "relationships": [],
    }


_DESCRIPTIONS = {"t_asset.host": "호스트 이름", "t_asset.owner": "담당 부서"}
_SYNONYMS = {"t_asset.host": ["서버명", "호스트명"]}


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_cache_manager()
    yield
    reset_cache_manager()


def _config(tmp_path: Path, backend: str) -> MagicMock:
    config = MagicMock()
    config.schema_cache.backend = backend
    config.schema_cache.cache_dir = str(tmp_path / "cache")
    config.schema_cache.enabled = True
    return config


def _file_body(directory: Path, db_id: str = "db1") -> dict:
    return json.loads((directory / f"{db_id}_schema.json").read_text(encoding="utf-8"))


class TestSaveKeepsExtras:
    def test_resave_keeps_descriptions_synonyms_and_db_description(self, tmp_path: Path) -> None:
        cache = PersistentSchemaCache(cache_dir=str(tmp_path))
        assert cache.save("db1", _schema("host", "owner"), fingerprint="fp1")
        assert cache.save_descriptions("db1", _DESCRIPTIONS)
        assert cache.save_synonyms("db1", _SYNONYMS)
        assert cache.update_field("db1", "_db_description", "자산 원장")
        assert cache.update_field("db1", "_db_description_origin", "llm")

        assert cache.save("db1", _schema("host", "owner", "added"), fingerprint="fp2")

        body = _file_body(tmp_path)
        assert body["_descriptions"] == _DESCRIPTIONS
        assert body["_synonyms"] == _SYNONYMS
        assert body["_db_description"] == "자산 원장"
        assert body["_db_description_origin"] == "llm"
        # 새로 쓰는 키는 새 값이 우선이다
        assert body["_fingerprint"] == "fp2"
        assert [c["name"] for c in body["schema"]["tables"]["t_asset"]["columns"]] == [
            "host",
            "owner",
            "added",
        ]
        # 같은 인스턴스(메모리 버퍼)와 새 인스턴스(파일) 모두 보존본을 본다
        assert cache.load_descriptions("db1") == _DESCRIPTIONS
        fresh = PersistentSchemaCache(cache_dir=str(tmp_path))
        assert fresh.load_descriptions("db1") == _DESCRIPTIONS
        assert fresh.load_synonyms("db1") == _SYNONYMS

    def test_first_save_has_no_extras(self, tmp_path: Path) -> None:
        cache = PersistentSchemaCache(cache_dir=str(tmp_path))
        assert cache.save("db1", _schema("host"), fingerprint="fp1")
        assert set(_file_body(tmp_path)) == {
            "_cache_version",
            "_fingerprint",
            "_db_id",
            "_cached_at",
            "_cached_at_iso",
            "schema",
        }

    def test_other_format_version_extras_are_not_carried(self, tmp_path: Path) -> None:
        """포맷 버전이 다른 옛 파일의 부가 필드는 옮기지 않는다(로드도 무시하는 파일이다)."""
        path = tmp_path / "db1_schema.json"
        path.write_text(
            json.dumps({"_cache_version": 0, "schema": {}, "_descriptions": {"a.b": "x"}}),
            encoding="utf-8",
        )
        cache = PersistentSchemaCache(cache_dir=str(tmp_path))
        assert cache.save("db1", _schema("host"), fingerprint="fp1")
        assert "_descriptions" not in _file_body(tmp_path)

    def test_deleted_field_stays_deleted_after_resave(self, tmp_path: Path) -> None:
        cache = PersistentSchemaCache(cache_dir=str(tmp_path))
        cache.save("db1", _schema("host"), fingerprint="fp1")
        cache.save_descriptions("db1", _DESCRIPTIONS)
        assert cache.delete_field("db1", "_descriptions")
        cache.save("db1", _schema("host"), fingerprint="fp2")
        assert "_descriptions" not in _file_body(tmp_path)


class TestManagerResaveSymmetry:
    async def test_redis_and_file_both_keep_descriptions_after_schema_resave(
        self, tmp_path: Path
    ) -> None:
        """run 20261006-152938 재현 — 재저장 뒤 Redis는 설명을 지키는데 파일만 잃던 비대칭."""
        mgr = SchemaCacheManager(_config(tmp_path, "redis"))
        attach_fake_redis(mgr._redis_cache)
        assert await mgr.save_schema("db1", _schema("host", "owner"), "fp1")
        assert await mgr.save_descriptions("db1", _DESCRIPTIONS)
        assert await mgr.save_synonyms("db1", _SYNONYMS)

        assert await mgr.save_schema("db1", _schema("host", "owner"), "fp2")

        assert await mgr._redis_cache.load_descriptions("db1") == _DESCRIPTIONS
        fresh = PersistentSchemaCache(cache_dir=str(tmp_path / "cache"))
        assert fresh.load_descriptions("db1") == _DESCRIPTIONS
        assert fresh.load_synonyms("db1") == _SYNONYMS

    async def test_file_backend_keeps_descriptions_after_schema_resave(
        self, tmp_path: Path
    ) -> None:
        mgr = SchemaCacheManager(_config(tmp_path, "file"))
        assert await mgr.save_schema("db1", _schema("host"), "fp1")
        assert await mgr.save_descriptions("db1", {"t_asset.host": "호스트 이름"})
        assert await mgr.save_schema("db1", _schema("host"), "fp2")
        assert await mgr.get_descriptions("db1") == {"t_asset.host": "호스트 이름"}
