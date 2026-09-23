"""캐시 미스 경로의 LLM 생성·전역 사전 쓰기 0 (plans/104 B-6 · B-8 ② · D-228 후속).

D-228은 캐시 미스 시 자동 생성된 LLM 유사어의 전역 전파를 구조 정본(수동 프로필·승인본) DB로
한정하는 가드였다. plans/104 B-6이 질의 경로의 지연 LLM 설명·유사어 생성 자체를 없애면서 그 가드
분기도 같은 결정 안에서 사라졌다(B-8 ②). 이 파일이 고정하는 것:

  ① 캐시 미스에서 LLM 생성 0 — 프로필 유무(없음·`source: auto`·`source: manual`)와 무관
  ② 캐시 미스에서 전역 사전(`synonyms:global`) 쓰기 0 · DB별 설명·유사어 쓰기도 0(백업이 없으면)
  ③ `get_schema_or_fetch`에 가드 코드 참조 0
  ④ 구조 정본 판정(`has_structure_authority`)은 양식 매핑 전역 등록 판정(G-11)이 계속 쓴다
  ⑤ 사용자 명시 경로(`sync_global_synonyms` 직접 호출 · `add_global_synonym`)는 불변

LLM·DB 0 · Redis는 목 또는 인메모리 페이크.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from src.schema_cache import cache_manager as cache_manager_module
from src.schema_cache.cache_manager import SchemaCacheManager
from tests.mocks.async_redis import attach_fake_redis

_GEN_SYNONYMS = {"t1.colA": ["별칭A"], "t1.colB": ["별칭B"]}


@pytest.fixture
def manager():
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = ".cache/schema"
    config.schema_cache.enabled = True
    mgr = SchemaCacheManager(config)
    mgr._redis_cache = AsyncMock()
    mgr._redis_available = True
    return mgr


def _client():
    col = SimpleNamespace(name="colA", data_type="varchar", nullable=True,
                          is_primary_key=False, is_foreign_key=False, references=None)
    table = SimpleNamespace(columns=[col], row_count_estimate=1)
    client = MagicMock()
    client.execute_sql = AsyncMock(return_value=SimpleNamespace(rows=[]))
    client.get_full_schema = AsyncMock(
        return_value=SimpleNamespace(tables={"t1": table}, relationships=[])
    )
    return client


def _write_profile(root: Path, db_id: str, source: str) -> None:
    profiles = root / "config" / "db_profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / f"{db_id}.yaml").write_text(f"source: {source}\ntables: {{}}\n", encoding="utf-8")


def _fake_manager(tmp_path: Path):
    """인메모리 Redis 페이크를 붙인 매니저(파일 캐시·설명 백업은 tmp 아래)."""
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = True
    mgr = SchemaCacheManager(config)
    fake = attach_fake_redis(mgr._redis_cache)
    return mgr, fake


class TestCacheMissGeneratesNothing:
    """① · ② — 캐시 미스는 스키마 저장까지만 한다."""

    @pytest.mark.parametrize("profile_source", [None, "auto", "manual"])
    @pytest.mark.asyncio
    async def test_no_llm_no_global_or_db_synonym_writes(
        self, tmp_path, monkeypatch, profile_source
    ):
        monkeypatch.chdir(tmp_path)
        if profile_source:
            _write_profile(tmp_path, "db1", profile_source)
        mgr, fake = _fake_manager(tmp_path)

        with patch("src.schema_cache.description_generator.DescriptionGenerator") as gen_cls, \
             patch("src.llm.create_llm") as create_llm, \
             patch.object(mgr, "sync_global_synonyms", AsyncMock()) as sync, \
             patch.object(mgr, "has_structure_authority", AsyncMock()) as authority:
            schema, cache_hit, source, descriptions, _ = await mgr.get_schema_or_fetch(
                _client(), "db1"
            )

        assert (cache_hit, source) == (False, "DB 직접 조회")
        assert "t1" in schema["tables"]                     # 스키마 저장까지는 종전과 같다
        gen_cls.assert_not_called()                         # DescriptionGenerator 미생성
        create_llm.assert_not_called()                      # LLM 획득 0
        sync.assert_not_awaited()                           # 전역 동기화 0
        authority.assert_not_awaited()                      # 가드 판정 자체가 없다
        assert descriptions == {}
        assert await fake.exists("synonyms:global") == 0
        assert await fake.hlen("schema:db1:descriptions") == 0
        assert await fake.hlen("schema:db1:synonyms") == 0

    @pytest.mark.asyncio
    async def test_missing_descriptions_logged_without_generation(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.chdir(tmp_path)
        mgr, _ = _fake_manager(tmp_path)
        with caplog.at_level(logging.WARNING, logger="src.schema_cache.cache_manager"):
            await mgr.get_schema_or_fetch(_client(), "bare_db")
        assert any(
            "컬럼 설명 미등록" in r.getMessage() and "db_id=bare_db" in r.getMessage()
            for r in caplog.records
        )


def test_guard_code_references_zero():
    """③ `get_schema_or_fetch`에 D-228 가드·지연 생성 참조가 남지 않는다."""
    source = inspect.getsource(SchemaCacheManager.get_schema_or_fetch)
    for name in (
        "has_structure_authority", "_has_manual_profile", "sync_global_synonyms",
        "DescriptionGenerator", "generate_for_db", "create_llm", "load_config",
    ):
        assert name not in source, name
    # 종전 차단 로그 문구 — 이 파일 자신이 grep에 걸리지 않게 조각으로 조립한다.
    blocked_log = "LLM 유사어 전역 " + "전파 차단"
    assert blocked_log not in Path(cache_manager_module.__file__).read_text(encoding="utf-8")


def _stub_store(*, versions: list | None = None, error: Exception | None = None):
    """승인본 조회 대역 — 판정 함수가 어느 조회 API를 부르든 같은 답을 준다."""
    store = MagicMock()
    if error is not None:
        store.list_versions = AsyncMock(side_effect=error)
        store.has_versions = MagicMock(side_effect=error)
        store.has_approved_version = MagicMock(side_effect=error)
    else:
        store.list_versions = AsyncMock(return_value=list(versions or []))
        store.has_versions = MagicMock(return_value=bool(versions))
        store.has_approved_version = MagicMock(return_value=any(
            v.get("kind") in ("approved", "rollback") for v in versions or []
        ))
    return store


class TestStructureAuthority:
    """④ 구조 정본 판정 — 수동 프로필 또는 승인본(양식 매핑 전역 등록 판정이 소비)."""

    @pytest.mark.asyncio
    async def test_manual_profile_is_authority(self, manager, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_profile(tmp_path, "manual_db", "manual")
        assert await manager.has_structure_authority("manual_db") is True

    @pytest.mark.asyncio
    async def test_approved_version_is_authority(self, manager, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        store = _stub_store(versions=[{"ver": 1, "kind": "approved"}])
        with patch.object(SchemaCacheManager, "structure_store", new_callable=PropertyMock,
                          return_value=store):
            assert await manager.has_structure_authority("approved_db") is True

    @pytest.mark.asyncio
    async def test_baseline_only_versions_are_not_authority(self, manager, tmp_path, monkeypatch):
        """적용 직전 보관(baseline·external_change)만 있으면 승인 버전이 아니다."""
        monkeypatch.chdir(tmp_path)
        store = _stub_store(versions=[{"ver": 0, "kind": "baseline"},
                                      {"ver": 1, "kind": "external_change"}])
        with patch.object(SchemaCacheManager, "structure_store", new_callable=PropertyMock,
                          return_value=store):
            assert await manager.has_structure_authority("baseline_db") is False

    @pytest.mark.parametrize("profile_source", [None, "auto"])
    @pytest.mark.asyncio
    async def test_no_manual_no_approved_is_not_authority(
        self, manager, tmp_path, monkeypatch, profile_source
    ):
        """LLM 자동 구조 분석이 쓰던 `source: auto` 프로필은 수동 프로필이 아니다."""
        monkeypatch.chdir(tmp_path)
        if profile_source:
            _write_profile(tmp_path, "bare_db", profile_source)
        with patch.object(SchemaCacheManager, "structure_store", new_callable=PropertyMock,
                          return_value=_stub_store()):
            assert await manager.has_structure_authority("bare_db") is False

    @pytest.mark.asyncio
    async def test_authority_lookup_failure_is_not_authority(
        self, manager, tmp_path, monkeypatch, caplog
    ):
        """승인본 조회가 실패하면 WARNING 후 정본 없음으로 본다(전역 쓰기 쪽으로 새지 않는다)."""
        monkeypatch.chdir(tmp_path)
        store = _stub_store(error=RuntimeError("store down"))
        with patch.object(SchemaCacheManager, "structure_store", new_callable=PropertyMock,
                          return_value=store), \
             caplog.at_level(logging.WARNING, logger="src.schema_cache.cache_manager"):
            assert await manager.has_structure_authority("some_db") is False
        assert any("구조 승인본 확인 실패" in r.getMessage() for r in caplog.records)


class TestExplicitPathsUnchanged:
    """⑤ 사용자 명시 경로는 판정 없이 종전대로 동작한다."""

    @pytest.mark.asyncio
    async def test_direct_sync_still_merges_without_profile(self, manager, tmp_path, monkeypatch):
        """관리 명령 경로가 직접 부르는 `sync_global_synonyms`는 가드하지 않는다."""
        monkeypatch.chdir(tmp_path)
        manager._redis_cache.load_synonyms = AsyncMock(return_value=_GEN_SYNONYMS)
        manager._redis_cache.load_global_synonyms = AsyncMock(return_value={})
        manager._redis_cache.add_global_synonym = AsyncMock(return_value=True)
        with patch.object(manager, "get_synonyms", AsyncMock(return_value=_GEN_SYNONYMS)), \
             patch.object(manager, "get_global_synonyms", AsyncMock(return_value={})):
            merged = await manager.sync_global_synonyms("unprofiled_db")
        assert merged == 2
        assert manager._redis_cache.add_global_synonym.await_count == 2

    @pytest.mark.asyncio
    async def test_add_global_synonym_unchanged(self, manager):
        """사용자 등록(`synonym_registrar`)이 쓰는 `add_global_synonym`은 그대로 위임한다."""
        manager._redis_cache.add_global_synonym = AsyncMock(return_value=True)
        assert await manager.add_global_synonym("colA", ["별칭"]) is True
        manager._redis_cache.add_global_synonym.assert_awaited_once_with("colA", ["별칭"])


def test_repository_manual_profiles_are_detected():
    """현행 수동 프로필 DB는 정본으로 판정된다 — 프로필 없는 등록 DB는 아니다."""
    from src.domain.profile_merge import LOCAL_SANDBOX_ENVIRONMENT
    from src.schema_cache.cache_manager import _has_manual_profile
    from src.schema_cache.catalog_builder import load_structure_profile

    for db_id in ("polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "polestar"):
        assert _has_manual_profile(db_id), db_id
    for db_id in ("itam", "itsm", "cloud_portal", "test_db"):
        # 로컬 관리자 승인본(plans/104 · 추적 금지)은 이 머신에서만 수동 프로필이다
        profile = load_structure_profile(db_id)
        if isinstance(profile, dict) and profile.get("environment") == LOCAL_SANDBOX_ENVIRONMENT:
            continue
        assert not _has_manual_profile(db_id), db_id
