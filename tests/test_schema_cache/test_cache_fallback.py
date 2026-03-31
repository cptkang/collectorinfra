"""Plan 29 Phase 4: 캐시 정책 수정 검증 테스트.

검증 대상:
1. PersistentSchemaCache 확장 메서드
   - save_descriptions / load_descriptions
   - save_synonyms / load_synonyms
   - delete_field (멱등성 포함)
   - 캐시 파일 없을 때 빈 딕셔너리 반환
   - disabled 상태에서의 동작

2. SchemaCacheManager 파일 폴백 (Redis 장애 시나리오)
   - get_descriptions: Redis 미연결 시 파일 캐시에서 로드
   - get_synonyms: Redis 미연결 시 파일 캐시에서 로드
   - save_descriptions: Redis + 파일 양쪽 저장
   - save_synonyms: Redis + 파일 양쪽 저장
   - delete_db_description: Redis + 파일 양쪽 삭제

3. 이중 저장 후 Redis 장애 복구 시나리오
   - descriptions: Redis + 파일 저장 → Redis 장애 → 파일 로드 성공
   - synonyms: Redis + 파일 저장 → Redis 장애 → 파일 로드 성공
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schema_cache.persistent_cache import (
    CACHE_FORMAT_VERSION,
    PersistentSchemaCache,
)
from src.schema_cache.cache_manager import SchemaCacheManager, reset_cache_manager


# ============================================================
# 공통 fixture
# ============================================================


@pytest.fixture(autouse=True)
def reset_singleton():
    """각 테스트 전후로 싱글톤을 리셋한다."""
    reset_cache_manager()
    yield
    reset_cache_manager()


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
    """테스트용 스키마 딕셔너리."""
    return {
        "tables": {
            "servers": {
                "columns": [
                    {"name": "id", "type": "integer"},
                    {"name": "hostname", "type": "varchar"},
                ],
                "row_count_estimate": 50,
                "sample_data": [],
            },
        },
        "relationships": [],
    }


@pytest.fixture
def cache_with_schema(cache: PersistentSchemaCache, sample_schema: dict) -> PersistentSchemaCache:
    """스키마가 저장된 PersistentSchemaCache를 반환한다."""
    cache.save("test_db", sample_schema, fingerprint="fp_test")
    return cache


@pytest.fixture
def sample_descriptions() -> dict[str, str]:
    """테스트용 컬럼 설명 매핑."""
    return {
        "servers.id": "서버 고유 식별자",
        "servers.hostname": "서버 호스트명",
    }


@pytest.fixture
def sample_synonyms() -> dict[str, list[str]]:
    """테스트용 유사 단어 매핑."""
    return {
        "servers.hostname": ["서버명", "호스트명", "서버 이름"],
        "servers.id": ["서버 ID", "서버 번호"],
    }


@pytest.fixture
def file_config(tmp_path: Path):
    """파일 캐시 전용 AppConfig."""
    config = MagicMock()
    config.schema_cache.backend = "file"
    config.schema_cache.cache_dir = str(tmp_path / "mgr_cache")
    config.schema_cache.enabled = True
    config.schema_cache.auto_generate_descriptions = False
    config.redis.host = "localhost"
    config.redis.port = 6379
    config.redis.db = 0
    config.redis.password = ""
    config.redis.ssl = False
    config.redis.socket_timeout = 5
    return config


@pytest.fixture
def redis_config(tmp_path: Path):
    """Redis 백엔드 AppConfig."""
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = str(tmp_path / "mgr_redis_cache")
    config.schema_cache.enabled = True
    config.schema_cache.auto_generate_descriptions = False
    config.redis.host = "localhost"
    config.redis.port = 6379
    config.redis.db = 0
    config.redis.password = ""
    config.redis.ssl = False
    config.redis.socket_timeout = 5
    return config


# ============================================================
# 1. PersistentSchemaCache 확장 메서드 테스트
# ============================================================


class TestPersistentCacheSaveLoadDescriptions:
    """save_descriptions / load_descriptions 테스트."""

    def test_save_and_load_descriptions_roundtrip(
        self,
        cache_with_schema: PersistentSchemaCache,
        sample_descriptions: dict[str, str],
    ) -> None:
        """descriptions 저장 후 동일 값이 로드되어야 한다."""
        result = cache_with_schema.save_descriptions("test_db", sample_descriptions)
        assert result is True

        loaded = cache_with_schema.load_descriptions("test_db")
        assert loaded == sample_descriptions

    def test_load_descriptions_no_cache_file(self, cache: PersistentSchemaCache) -> None:
        """캐시 파일이 없으면 빈 딕셔너리를 반환한다."""
        loaded = cache.load_descriptions("nonexistent_db")
        assert loaded == {}

    def test_load_descriptions_no_descriptions_field(
        self,
        cache_with_schema: PersistentSchemaCache,
    ) -> None:
        """캐시 파일에 _descriptions 필드가 없으면 빈 딕셔너리를 반환한다."""
        loaded = cache_with_schema.load_descriptions("test_db")
        assert loaded == {}

    def test_save_descriptions_overwrites_existing(
        self,
        cache_with_schema: PersistentSchemaCache,
    ) -> None:
        """save_descriptions는 기존 descriptions를 덮어쓴다."""
        cache_with_schema.save_descriptions("test_db", {"servers.id": "초기 설명"})
        cache_with_schema.save_descriptions("test_db", {"servers.hostname": "새 설명"})

        loaded = cache_with_schema.load_descriptions("test_db")
        assert loaded == {"servers.hostname": "새 설명"}
        assert "servers.id" not in loaded

    def test_save_descriptions_returns_false_without_schema(
        self,
        cache: PersistentSchemaCache,
        sample_descriptions: dict[str, str],
    ) -> None:
        """스키마 캐시 파일이 없으면 save_descriptions는 False를 반환한다."""
        result = cache.save_descriptions("no_schema_db", sample_descriptions)
        assert result is False


class TestPersistentCacheSaveLoadSynonyms:
    """save_synonyms / load_synonyms 테스트."""

    def test_save_and_load_synonyms_roundtrip(
        self,
        cache_with_schema: PersistentSchemaCache,
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """synonyms 저장 후 동일 값이 로드되어야 한다."""
        result = cache_with_schema.save_synonyms("test_db", sample_synonyms)
        assert result is True

        loaded = cache_with_schema.load_synonyms("test_db")
        assert loaded == sample_synonyms

    def test_load_synonyms_no_cache_file(self, cache: PersistentSchemaCache) -> None:
        """캐시 파일이 없으면 빈 딕셔너리를 반환한다."""
        loaded = cache.load_synonyms("nonexistent_db")
        assert loaded == {}

    def test_load_synonyms_no_synonyms_field(
        self,
        cache_with_schema: PersistentSchemaCache,
    ) -> None:
        """캐시 파일에 _synonyms 필드가 없으면 빈 딕셔너리를 반환한다."""
        loaded = cache_with_schema.load_synonyms("test_db")
        assert loaded == {}

    def test_save_synonyms_returns_false_without_schema(
        self,
        cache: PersistentSchemaCache,
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """스키마 캐시 파일이 없으면 save_synonyms는 False를 반환한다."""
        result = cache.save_synonyms("no_schema_db", sample_synonyms)
        assert result is False

    def test_synonyms_values_are_lists(
        self,
        cache_with_schema: PersistentSchemaCache,
    ) -> None:
        """유사 단어 값이 리스트로 저장·로드된다."""
        synonyms = {"servers.hostname": ["서버명", "호스트명"]}
        cache_with_schema.save_synonyms("test_db", synonyms)

        loaded = cache_with_schema.load_synonyms("test_db")
        assert isinstance(loaded["servers.hostname"], list)
        assert "서버명" in loaded["servers.hostname"]
        assert "호스트명" in loaded["servers.hostname"]


class TestPersistentCacheDeleteField:
    """delete_field 테스트."""

    def test_delete_existing_field(
        self,
        cache_with_schema: PersistentSchemaCache,
        sample_descriptions: dict[str, str],
    ) -> None:
        """존재하는 필드를 삭제한다."""
        cache_with_schema.save_descriptions("test_db", sample_descriptions)

        result = cache_with_schema.delete_field("test_db", "_descriptions")
        assert result is True

        loaded = cache_with_schema.load_descriptions("test_db")
        assert loaded == {}

    def test_delete_nonexistent_field_is_idempotent(
        self,
        cache_with_schema: PersistentSchemaCache,
    ) -> None:
        """존재하지 않는 필드 삭제는 True를 반환한다 (멱등성)."""
        result = cache_with_schema.delete_field("test_db", "_nonexistent_field")
        assert result is True

    def test_delete_field_twice_is_idempotent(
        self,
        cache_with_schema: PersistentSchemaCache,
        sample_descriptions: dict[str, str],
    ) -> None:
        """같은 필드를 두 번 삭제해도 True를 반환한다 (멱등성)."""
        cache_with_schema.save_descriptions("test_db", sample_descriptions)

        first = cache_with_schema.delete_field("test_db", "_descriptions")
        second = cache_with_schema.delete_field("test_db", "_descriptions")

        assert first is True
        assert second is True

    def test_delete_field_no_cache_file(self, cache: PersistentSchemaCache) -> None:
        """캐시 파일이 없으면 False를 반환한다."""
        result = cache.delete_field("nonexistent_db", "_descriptions")
        assert result is False

    def test_delete_field_preserves_other_fields(
        self,
        cache_with_schema: PersistentSchemaCache,
        sample_descriptions: dict[str, str],
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """특정 필드 삭제 시 다른 필드는 유지된다."""
        cache_with_schema.save_descriptions("test_db", sample_descriptions)
        cache_with_schema.save_synonyms("test_db", sample_synonyms)

        cache_with_schema.delete_field("test_db", "_descriptions")

        # _descriptions는 삭제됨
        assert cache_with_schema.load_descriptions("test_db") == {}
        # _synonyms는 유지됨
        assert cache_with_schema.load_synonyms("test_db") == sample_synonyms


class TestPersistentCacheDisabledState:
    """disabled 상태에서의 descriptions/synonyms 동작 테스트."""

    def test_save_descriptions_disabled_returns_false(self, tmp_path: Path) -> None:
        """disabled 상태에서 save_descriptions는 False를 반환한다."""
        cache = PersistentSchemaCache(
            cache_dir=str(tmp_path / "cache"), enabled=False
        )
        result = cache.save_descriptions("any_db", {"col": "desc"})
        assert result is False

    def test_load_descriptions_disabled_returns_empty(self, tmp_path: Path) -> None:
        """disabled 상태에서 load_descriptions는 빈 딕셔너리를 반환한다."""
        cache = PersistentSchemaCache(
            cache_dir=str(tmp_path / "cache"), enabled=False
        )
        loaded = cache.load_descriptions("any_db")
        assert loaded == {}

    def test_save_synonyms_disabled_returns_false(self, tmp_path: Path) -> None:
        """disabled 상태에서 save_synonyms는 False를 반환한다."""
        cache = PersistentSchemaCache(
            cache_dir=str(tmp_path / "cache"), enabled=False
        )
        result = cache.save_synonyms("any_db", {"col": ["syn"]})
        assert result is False

    def test_load_synonyms_disabled_returns_empty(self, tmp_path: Path) -> None:
        """disabled 상태에서 load_synonyms는 빈 딕셔너리를 반환한다."""
        cache = PersistentSchemaCache(
            cache_dir=str(tmp_path / "cache"), enabled=False
        )
        loaded = cache.load_synonyms("any_db")
        assert loaded == {}

    def test_delete_field_disabled_returns_false(self, tmp_path: Path) -> None:
        """disabled 상태에서 delete_field는 False를 반환한다."""
        cache = PersistentSchemaCache(
            cache_dir=str(tmp_path / "cache"), enabled=False
        )
        result = cache.delete_field("any_db", "_descriptions")
        assert result is False


# ============================================================
# 2. SchemaCacheManager 파일 폴백 테스트 (Redis 장애 시나리오)
# ============================================================


class TestCacheManagerGetDescriptionsFileFallback:
    """get_descriptions: Redis 미연결 시 파일 캐시에서 로드."""

    @pytest.mark.asyncio
    async def test_get_descriptions_redis_failure_falls_back_to_file(
        self,
        redis_config,
        sample_schema: dict,
        sample_descriptions: dict[str, str],
    ) -> None:
        """Redis 장애 시 파일 캐시에서 descriptions를 로드한다."""
        mgr = SchemaCacheManager(redis_config)

        # 파일 캐시에 직접 저장
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")
        mgr._file_cache.save_descriptions("test_db", sample_descriptions)

        # Redis 연결 실패 시뮬레이션
        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            result = await mgr.get_descriptions("test_db")

        assert result == sample_descriptions

    @pytest.mark.asyncio
    async def test_get_descriptions_file_mode_loads_from_file(
        self,
        file_config,
        sample_schema: dict,
        sample_descriptions: dict[str, str],
    ) -> None:
        """file 모드에서 파일 캐시에서 descriptions를 로드한다."""
        mgr = SchemaCacheManager(file_config)

        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")
        mgr._file_cache.save_descriptions("test_db", sample_descriptions)

        result = await mgr.get_descriptions("test_db")
        assert result == sample_descriptions

    @pytest.mark.asyncio
    async def test_get_descriptions_redis_returns_empty_falls_back_to_file(
        self,
        redis_config,
        sample_schema: dict,
        sample_descriptions: dict[str, str],
    ) -> None:
        """Redis에 descriptions가 없으면 파일 캐시로 폴백한다."""
        mgr = SchemaCacheManager(redis_config)

        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")
        mgr._file_cache.save_descriptions("test_db", sample_descriptions)

        mock_redis = AsyncMock()
        mock_redis.health_check = AsyncMock(return_value=True)
        mock_redis.load_descriptions = AsyncMock(return_value={})  # Redis에 없음
        mgr._redis_cache = mock_redis
        mgr._redis_available = True

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            result = await mgr.get_descriptions("test_db")

        # 파일 캐시에서 로드됨
        assert result == sample_descriptions


class TestCacheManagerGetSynonymsFileFallback:
    """get_synonyms: Redis 미연결 시 파일 캐시에서 로드."""

    @pytest.mark.asyncio
    async def test_get_synonyms_redis_failure_falls_back_to_file(
        self,
        redis_config,
        sample_schema: dict,
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """Redis 장애 시 파일 캐시에서 synonyms를 로드한다."""
        mgr = SchemaCacheManager(redis_config)

        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")
        mgr._file_cache.save_synonyms("test_db", sample_synonyms)

        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            result = await mgr.get_synonyms("test_db")

        assert result == sample_synonyms

    @pytest.mark.asyncio
    async def test_get_synonyms_file_mode_loads_from_file(
        self,
        file_config,
        sample_schema: dict,
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """file 모드에서 파일 캐시에서 synonyms를 로드한다."""
        mgr = SchemaCacheManager(file_config)

        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")
        mgr._file_cache.save_synonyms("test_db", sample_synonyms)

        result = await mgr.get_synonyms("test_db")
        assert result == sample_synonyms

    @pytest.mark.asyncio
    async def test_get_synonyms_redis_empty_falls_back_to_file(
        self,
        redis_config,
        sample_schema: dict,
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """Redis에 synonyms가 없으면 파일 캐시로 폴백한다."""
        mgr = SchemaCacheManager(redis_config)

        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")
        mgr._file_cache.save_synonyms("test_db", sample_synonyms)

        mock_redis = AsyncMock()
        mock_redis.health_check = AsyncMock(return_value=True)
        mock_redis.load_synonyms = AsyncMock(return_value={})  # Redis에 없음
        mgr._redis_cache = mock_redis
        mgr._redis_available = True

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            result = await mgr.get_synonyms("test_db")

        assert result == sample_synonyms


class TestCacheManagerSaveDescriptionsDual:
    """save_descriptions: Redis + 파일 양쪽 저장 확인."""

    @pytest.mark.asyncio
    async def test_save_descriptions_saves_to_both_redis_and_file(
        self,
        redis_config,
        sample_schema: dict,
        sample_descriptions: dict[str, str],
    ) -> None:
        """save_descriptions는 Redis와 파일 캐시 양쪽에 저장한다."""
        mgr = SchemaCacheManager(redis_config)

        # 파일 캐시에 스키마 먼저 저장 (descriptions 저장 전제 조건)
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")

        mock_redis = AsyncMock()
        mock_redis.health_check = AsyncMock(return_value=True)
        mock_redis.save_descriptions = AsyncMock(return_value=True)
        mgr._redis_cache = mock_redis
        mgr._redis_available = True

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            result = await mgr.save_descriptions("test_db", sample_descriptions)

        assert result is True
        # Redis에 저장 호출 확인
        mock_redis.save_descriptions.assert_called_once_with("test_db", sample_descriptions)
        # 파일 캐시에도 저장 확인
        file_loaded = mgr._file_cache.load_descriptions("test_db")
        assert file_loaded == sample_descriptions

    @pytest.mark.asyncio
    async def test_save_descriptions_file_only_when_redis_fails(
        self,
        redis_config,
        sample_schema: dict,
        sample_descriptions: dict[str, str],
    ) -> None:
        """Redis 장애 시에도 파일 캐시 저장은 성공한다."""
        mgr = SchemaCacheManager(redis_config)
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")

        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            result = await mgr.save_descriptions("test_db", sample_descriptions)

        # 파일 캐시 저장으로 True 반환
        assert result is True
        file_loaded = mgr._file_cache.load_descriptions("test_db")
        assert file_loaded == sample_descriptions


class TestCacheManagerSaveSynonymsDual:
    """save_synonyms: Redis + 파일 양쪽 저장 확인."""

    @pytest.mark.asyncio
    async def test_save_synonyms_saves_to_both_redis_and_file(
        self,
        redis_config,
        sample_schema: dict,
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """save_synonyms는 Redis와 파일 캐시 양쪽에 저장한다."""
        mgr = SchemaCacheManager(redis_config)
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")

        mock_redis = AsyncMock()
        mock_redis.health_check = AsyncMock(return_value=True)
        mock_redis.save_synonyms = AsyncMock(return_value=True)
        mgr._redis_cache = mock_redis
        mgr._redis_available = True

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            result = await mgr.save_synonyms("test_db", sample_synonyms)

        assert result is True
        # Redis 저장 호출 확인
        mock_redis.save_synonyms.assert_called_once_with(
            "test_db", sample_synonyms, source="llm"
        )
        # 파일 캐시 저장 확인
        file_loaded = mgr._file_cache.load_synonyms("test_db")
        assert file_loaded == sample_synonyms

    @pytest.mark.asyncio
    async def test_save_synonyms_file_only_when_redis_fails(
        self,
        redis_config,
        sample_schema: dict,
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """Redis 장애 시에도 파일 캐시 synonyms 저장은 성공한다."""
        mgr = SchemaCacheManager(redis_config)
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")

        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            result = await mgr.save_synonyms("test_db", sample_synonyms)

        assert result is True
        file_loaded = mgr._file_cache.load_synonyms("test_db")
        assert file_loaded == sample_synonyms


class TestCacheManagerDeleteDbDescriptionDual:
    """delete_db_description: Redis + 파일 양쪽 삭제 확인."""

    @pytest.mark.asyncio
    async def test_delete_db_description_deletes_from_both(
        self,
        redis_config,
        sample_schema: dict,
    ) -> None:
        """delete_db_description은 Redis와 파일 캐시 양쪽에서 삭제한다."""
        mgr = SchemaCacheManager(redis_config)

        # 파일 캐시에 DB 설명 저장
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")
        mgr._file_cache.update_field("test_db", "_db_description", "인프라 DB")

        mock_redis = AsyncMock()
        mock_redis.health_check = AsyncMock(return_value=True)
        mock_redis.delete_db_description = AsyncMock(return_value=True)
        mgr._redis_cache = mock_redis
        mgr._redis_available = True

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            result = await mgr.delete_db_description("test_db")

        assert result is True
        # Redis 삭제 호출 확인
        mock_redis.delete_db_description.assert_called_once_with("test_db")
        # 파일 캐시에서 삭제 확인
        file_data = mgr._file_cache.load("test_db")
        assert file_data is not None
        assert "_db_description" not in file_data

    @pytest.mark.asyncio
    async def test_delete_db_description_file_only_when_redis_fails(
        self,
        redis_config,
        sample_schema: dict,
    ) -> None:
        """Redis 장애 시에도 파일 캐시 삭제는 진행된다."""
        mgr = SchemaCacheManager(redis_config)
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")
        mgr._file_cache.update_field("test_db", "_db_description", "인프라 DB")

        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            result = await mgr.delete_db_description("test_db")

        assert result is True
        file_data = mgr._file_cache.load("test_db")
        assert "_db_description" not in file_data


# ============================================================
# 3. 이중 저장 후 Redis 장애 복구 시나리오
# ============================================================


class TestRedisFailureRecoveryDescriptions:
    """descriptions: Redis + 파일 저장 → Redis 장애 → 파일에서 복구."""

    @pytest.mark.asyncio
    async def test_descriptions_survive_redis_failure(
        self,
        redis_config,
        sample_schema: dict,
        sample_descriptions: dict[str, str],
    ) -> None:
        """Redis 장애 발생 시 파일 캐시에서 descriptions를 정상 로드한다."""
        mgr = SchemaCacheManager(redis_config)
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")

        mock_redis = AsyncMock()
        mock_redis.health_check = AsyncMock(return_value=True)
        mock_redis.save_descriptions = AsyncMock(return_value=True)
        mock_redis.load_descriptions = AsyncMock(return_value=sample_descriptions)
        mgr._redis_cache = mock_redis
        mgr._redis_available = True

        # Step 1: Redis + 파일 양쪽에 저장
        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            saved = await mgr.save_descriptions("test_db", sample_descriptions)
        assert saved is True

        # Step 2: Redis 장애 발생
        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            # Step 3: 파일 캐시에서 정상 로드
            result = await mgr.get_descriptions("test_db")

        assert result == sample_descriptions

    @pytest.mark.asyncio
    async def test_descriptions_file_cache_is_written_even_with_redis_success(
        self,
        redis_config,
        sample_schema: dict,
        sample_descriptions: dict[str, str],
    ) -> None:
        """Redis 저장 성공 시에도 파일 캐시에 이중 저장된다."""
        mgr = SchemaCacheManager(redis_config)
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")

        mock_redis = AsyncMock()
        mock_redis.health_check = AsyncMock(return_value=True)
        mock_redis.save_descriptions = AsyncMock(return_value=True)
        mgr._redis_cache = mock_redis
        mgr._redis_available = True

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            await mgr.save_descriptions("test_db", sample_descriptions)

        # Redis 없이 파일 캐시 직접 조회
        file_loaded = mgr._file_cache.load_descriptions("test_db")
        assert file_loaded == sample_descriptions


class TestRedisFailureRecoverySynonyms:
    """synonyms: Redis + 파일 저장 → Redis 장애 → 파일에서 복구."""

    @pytest.mark.asyncio
    async def test_synonyms_survive_redis_failure(
        self,
        redis_config,
        sample_schema: dict,
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """Redis 장애 발생 시 파일 캐시에서 synonyms를 정상 로드한다."""
        mgr = SchemaCacheManager(redis_config)
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")

        mock_redis = AsyncMock()
        mock_redis.health_check = AsyncMock(return_value=True)
        mock_redis.save_synonyms = AsyncMock(return_value=True)
        mock_redis.load_synonyms = AsyncMock(return_value=sample_synonyms)
        mgr._redis_cache = mock_redis
        mgr._redis_available = True

        # Step 1: Redis + 파일 양쪽에 저장
        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            saved = await mgr.save_synonyms("test_db", sample_synonyms)
        assert saved is True

        # Step 2: Redis 장애 발생
        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            # Step 3: 파일 캐시에서 정상 로드
            result = await mgr.get_synonyms("test_db")

        assert result == sample_synonyms

    @pytest.mark.asyncio
    async def test_synonyms_file_cache_is_written_even_with_redis_success(
        self,
        redis_config,
        sample_schema: dict,
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """Redis 저장 성공 시에도 파일 캐시에 이중 저장된다."""
        mgr = SchemaCacheManager(redis_config)
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")

        mock_redis = AsyncMock()
        mock_redis.health_check = AsyncMock(return_value=True)
        mock_redis.save_synonyms = AsyncMock(return_value=True)
        mgr._redis_cache = mock_redis
        mgr._redis_available = True

        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            await mgr.save_synonyms("test_db", sample_synonyms)

        file_loaded = mgr._file_cache.load_synonyms("test_db")
        assert file_loaded == sample_synonyms

    @pytest.mark.asyncio
    async def test_synonyms_complete_redis_to_file_recovery_scenario(
        self,
        redis_config,
        sample_schema: dict,
        sample_synonyms: dict[str, list[str]],
    ) -> None:
        """완전한 복구 시나리오: 저장 → Redis 완전 장애 → 파일 복구 → Redis 복구."""
        mgr = SchemaCacheManager(redis_config)
        mgr._file_cache.save("test_db", sample_schema, fingerprint="fp")

        mock_redis = AsyncMock()
        mock_redis.health_check = AsyncMock(return_value=True)
        mock_redis.save_synonyms = AsyncMock(return_value=True)
        mock_redis.load_synonyms = AsyncMock(return_value=sample_synonyms)
        mgr._redis_cache = mock_redis

        # 1. 정상 저장 (Redis + 파일 이중 저장)
        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            await mgr.save_synonyms("test_db", sample_synonyms)

        # 2. Redis 장애 → 파일에서 로드
        with patch.object(mgr, "ensure_redis_connected", return_value=False):
            result_from_file = await mgr.get_synonyms("test_db")
        assert result_from_file == sample_synonyms

        # 3. Redis 복구 → Redis에서 로드
        with patch.object(mgr, "ensure_redis_connected", return_value=True):
            result_from_redis = await mgr.get_synonyms("test_db")
        assert result_from_redis == sample_synonyms
