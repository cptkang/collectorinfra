"""PersistentSchemaCache 테스트."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.schema_cache.persistent_cache import (
    CACHE_FORMAT_VERSION,
    PersistentSchemaCache,
)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """임시 캐시 디렉토리를 반환한다."""
    d = tmp_path / "schema_cache"
    d.mkdir()
    return d


@pytest.fixture
def cache(cache_dir: Path) -> PersistentSchemaCache:
    """테스트용 PersistentSchemaCache를 반환한다."""
    return PersistentSchemaCache(cache_dir=str(cache_dir), enabled=True)


@pytest.fixture
def sample_schema() -> dict:
    """테스트용 스키마 딕셔너리를 반환한다."""
    return {
        "tables": {
            "servers": {
                "columns": [
                    {"name": "id", "type": "integer"},
                    {"name": "hostname", "type": "varchar"},
                ],
                "row_count_estimate": 50,
                "sample_data": [{"id": 1, "hostname": "web-01"}],
            },
        },
        "relationships": [],
    }


class TestSaveAndLoad:
    """저장 및 로드 테스트."""

    def test_save_and_load_roundtrip(
        self, cache: PersistentSchemaCache, sample_schema: dict,
    ) -> None:
        """저장 후 로드하면 동일한 스키마를 반환한다."""
        assert cache.save("test_db", sample_schema)
        loaded = cache.load("test_db")
        assert loaded is not None
        assert loaded["schema"] == sample_schema
        assert loaded["_db_id"] == "test_db"
        assert loaded["_cache_version"] == CACHE_FORMAT_VERSION

    def test_get_schema_returns_only_schema(
        self, cache: PersistentSchemaCache, sample_schema: dict,
    ) -> None:
        """get_schema는 스키마 딕셔너리만 반환한다."""
        cache.save("test_db", sample_schema)
        schema = cache.get_schema("test_db")
        assert schema == sample_schema

    def test_load_nonexistent_returns_none(
        self, cache: PersistentSchemaCache,
    ) -> None:
        """존재하지 않는 DB에 대해 None을 반환한다."""
        assert cache.load("nonexistent") is None

    def test_save_with_explicit_fingerprint(
        self, cache: PersistentSchemaCache, sample_schema: dict,
    ) -> None:
        """명시적 fingerprint를 저장한다."""
        cache.save("test_db", sample_schema, fingerprint="abc123")
        loaded = cache.load("test_db")
        assert loaded["_fingerprint"] == "abc123"

    def test_save_creates_file(
        self, cache: PersistentSchemaCache, cache_dir: Path, sample_schema: dict,
    ) -> None:
        """저장 시 JSON 파일이 생성된다."""
        cache.save("my_db", sample_schema)
        assert (cache_dir / "my_db_schema.json").exists()

    def test_multi_db_independent_files(
        self, cache: PersistentSchemaCache, cache_dir: Path, sample_schema: dict,
    ) -> None:
        """DB별로 독립 파일을 생성한다."""
        cache.save("db1", sample_schema)
        cache.save("db2", sample_schema)
        assert (cache_dir / "db1_schema.json").exists()
        assert (cache_dir / "db2_schema.json").exists()


class TestFingerprintComparison:
    """fingerprint 비교 테스트."""

    def test_is_changed_no_cache(
        self, cache: PersistentSchemaCache,
    ) -> None:
        """캐시가 없으면 변경으로 판단한다."""
        assert cache.is_changed("test_db", "some_hash") is True

    def test_is_changed_same_fingerprint(
        self, cache: PersistentSchemaCache, sample_schema: dict,
    ) -> None:
        """fingerprint가 동일하면 변경 없음으로 판단한다."""
        cache.save("test_db", sample_schema, fingerprint="fp123")
        assert cache.is_changed("test_db", "fp123") is False

    def test_is_changed_different_fingerprint(
        self, cache: PersistentSchemaCache, sample_schema: dict,
    ) -> None:
        """fingerprint가 다르면 변경으로 판단한다."""
        cache.save("test_db", sample_schema, fingerprint="fp123")
        assert cache.is_changed("test_db", "fp456") is True

    def test_get_cached_fingerprint(
        self, cache: PersistentSchemaCache, sample_schema: dict,
    ) -> None:
        """캐시된 fingerprint를 반환한다."""
        cache.save("test_db", sample_schema, fingerprint="my_fp")
        assert cache.get_cached_fingerprint("test_db") == "my_fp"

    def test_get_cached_fingerprint_no_cache(
        self, cache: PersistentSchemaCache,
    ) -> None:
        """캐시가 없으면 None을 반환한다."""
        assert cache.get_cached_fingerprint("test_db") is None


class TestInvalidation:
    """캐시 무효화 테스트."""

    def test_invalidate_single(
        self, cache: PersistentSchemaCache, sample_schema: dict,
    ) -> None:
        """특정 DB 캐시만 무효화한다."""
        cache.save("db1", sample_schema)
        cache.save("db2", sample_schema)
        cache.invalidate("db1")
        assert cache.load("db1") is None
        assert cache.load("db2") is not None

    def test_invalidate_all(
        self, cache: PersistentSchemaCache, sample_schema: dict,
    ) -> None:
        """전체 캐시를 무효화한다."""
        cache.save("db1", sample_schema)
        cache.save("db2", sample_schema)
        count = cache.invalidate_all()
        assert count == 2
        assert cache.load("db1") is None
        assert cache.load("db2") is None


class TestCorruptedCache:
    """손상된 캐시 처리 테스트."""

    def test_corrupted_json(
        self, cache: PersistentSchemaCache, cache_dir: Path,
    ) -> None:
        """손상된 JSON 파일은 None을 반환하고 파일을 삭제한다."""
        corrupt_path = cache_dir / "bad_db_schema.json"
        corrupt_path.write_text("{ invalid json }", encoding="utf-8")
        assert cache.load("bad_db") is None
        assert not corrupt_path.exists()

    def test_wrong_version(
        self, cache: PersistentSchemaCache, cache_dir: Path,
    ) -> None:
        """캐시 포맷 버전이 다르면 None을 반환한다."""
        data = {
            "_cache_version": 999,
            "_fingerprint": "abc",
            "_db_id": "old_db",
            "schema": {"tables": {}},
        }
        path = cache_dir / "old_db_schema.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert cache.load("old_db") is None


class TestDisabledCache:
    """비활성화된 캐시 테스트."""

    def test_disabled_load_returns_none(self, tmp_path: Path) -> None:
        """비활성화 시 load는 항상 None을 반환한다."""
        cache = PersistentSchemaCache(
            cache_dir=str(tmp_path / "cache"),
            enabled=False,
        )
        assert cache.load("any_db") is None

    def test_disabled_save_returns_false(self, tmp_path: Path) -> None:
        """비활성화 시 save는 False를 반환한다."""
        cache = PersistentSchemaCache(
            cache_dir=str(tmp_path / "cache"),
            enabled=False,
        )
        assert cache.save("any_db", {"tables": {}}) is False

    def test_disabled_is_changed_returns_true(self, tmp_path: Path) -> None:
        """비활성화 시 is_changed는 항상 True를 반환한다."""
        cache = PersistentSchemaCache(
            cache_dir=str(tmp_path / "cache"),
            enabled=False,
        )
        assert cache.is_changed("any_db", "fp") is True


class TestListCachedDbs:
    """캐시 목록 조회 테스트."""

    def test_list_empty(self, cache: PersistentSchemaCache) -> None:
        """캐시가 없으면 빈 리스트를 반환한다."""
        assert cache.list_cached_dbs() == []

    def test_list_multiple_dbs(
        self, cache: PersistentSchemaCache, sample_schema: dict,
    ) -> None:
        """여러 DB 캐시 정보를 반환한다."""
        cache.save("alpha_db", sample_schema, fingerprint="fp1")
        cache.save("beta_db", sample_schema, fingerprint="fp2")
        result = cache.list_cached_dbs()
        assert len(result) == 2
        db_ids = {item["db_id"] for item in result}
        assert db_ids == {"alpha_db", "beta_db"}


class TestSafeFileNaming:
    """파일명 안전성 테스트."""

    def test_special_chars_in_db_id(
        self, cache: PersistentSchemaCache, sample_schema: dict,
    ) -> None:
        """특수 문자가 포함된 DB ID도 안전하게 처리한다."""
        cache.save("db/with:special@chars!", sample_schema)
        assert cache.get_schema("db/with:special@chars!") is not None
