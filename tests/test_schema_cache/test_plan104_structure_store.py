"""plans/104 저장 계층 — `StructureStore`(프로필 파일 정본 · 버전 이력)와 매니저의 적용본 조회.

Wave 2 계약 §3·§7.3: 첫 적용 시 현행 파일을 v0(baseline)으로 보관 · 같은 sha면 추가 보관 없음 ·
외부 편집은 external_change 보관 · 프로필 원자 기록 · v0 되돌리기 바이트 동일 복원 · drift 판정 ·
Redis를 비운 뒤 `restore_applied`는 Redis만 채우고 파일 불변 · 설명 백업 왕복 · db_id 경로 검증.

작업 디렉터리는 tmp로 옮기고 `profiles_dir`·`backup_root`도 tmp에 둔다 — 실제
`config/db_profiles/`에 쓰지 않는다. 픽스처 이름은 가상(`node_t` 등). LLM·DB 0.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from src.domain.profile_merge import merge_profile, profile_field_diff
from src.schema_cache import structure_store as store_module
from src.schema_cache.cache_manager import SchemaCacheManager
from src.schema_cache.redis_cache import RedisSchemaCache
from src.schema_cache.structure_store import (
    DEFAULT_PROFILES_DIR,
    StructureStore,
    StructureStoreUnavailable,
    count_comment_lines,
    validate_db_id,
)
from tests.mocks.async_redis import attach_fake_redis

# 사람이 쓴 프로필 원문 — 주석·빈 줄·줄 끝 공백·CRLF 줄을 섞어 바이트 복원을 확인한다
ORIGINAL_TEXT = (
    "# 가상 구조 프로필 — 사람이 직접 작성\n"
    "# 두 번째 머리 주석\n"
    "source: manual\n"
    "\n"
    "patterns:\n"
    "  # 패턴 주석\n"
    "  - type: hierarchy\n"
    "    table: node_t   \n"
    "    id_column: id\r\n"
    "    parent_column: parent_id\n"
    "query_guide: |\n"
    "  수동 안내\n"
    "allowed_tables: [node_t]\n"
)
ORIGINAL_COMMENT_LINES = 3
DRAFT_META = {
    "patterns": [{"type": "hierarchy", "table": "node_t", "name_column": "label"}],
    "query_guide": "LLM 안내",
    "samples": [{"sql": "SELECT 1", "rows": [{"v": "실데이터"}]}],
}
ENV = "http://mcp.test:9099"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _config(tmp_path: Path) -> MagicMock:
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = True
    return config


@pytest.fixture
def env(tmp_path, monkeypatch):
    """(매니저, 저장소, 페이크 Redis, 프로필 디렉터리, 백업 루트) — 작업 디렉터리는 tmp.

    프로필 디렉터리는 tmp의 `config/db_profiles`라 수동 프로필 판정(상대 경로)과 같은 파일을 본다.
    """
    monkeypatch.chdir(tmp_path)
    mgr = SchemaCacheManager(_config(tmp_path))
    fake = attach_fake_redis(mgr._redis_cache)
    backup_root = tmp_path / ".cache" / "structure"
    profiles_dir = tmp_path / "config" / "db_profiles"
    store = StructureStore(mgr._redis_cache, backup_root, profiles_dir=profiles_dir)
    mgr._structure_store = store
    return mgr, store, fake, profiles_dir, backup_root


def _write_original(profiles_dir: Path, text: str = ORIGINAL_TEXT) -> Path:
    profiles_dir.mkdir(parents=True, exist_ok=True)
    path = profiles_dir / "db1.yaml"
    path.write_bytes(text.encode("utf-8"))
    return path


def _merged(base: dict | None, draft: dict = DRAFT_META, *, local: bool = False) -> dict:
    return merge_profile(base, draft, local_sandbox=local)


async def _apply(store: StructureStore, profile: dict, **kw) -> dict:
    params = {
        "by": "admin",
        "reason": "검토 완료",
        "env": ENV,
        "draft_id": "d1",
        "field_diff": [],
    }
    params.update(kw)
    return await store.apply_profile("db1", profile, **params)


def _version_files(backup_root: Path) -> list[str]:
    directory = backup_root / "db1" / "versions"
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


class TestManagerWiring:
    def test_default_paths(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = SchemaCacheManager(_config(tmp_path))
        store = mgr.structure_store
        assert store.backup_root == tmp_path / ".cache" / "structure"
        assert store.profiles_dir == DEFAULT_PROFILES_DIR == Path("config/db_profiles")
        assert mgr.structure_store is store  # 매니저당 1개

    def test_store_follows_replaced_redis_cache(self, env):
        mgr, store, _, _, _ = env
        mgr._redis_cache = RedisSchemaCache(MagicMock())
        assert mgr.structure_store is not store
        assert mgr.structure_store.redis_cache is mgr._redis_cache


class TestApplyProfile:
    async def test_first_apply_archives_current_as_v0_baseline(self, env):
        _, store, fake, profiles_dir, backup_root = env
        path = _write_original(profiles_dir)
        await fake.set("schema:db1:column_value_index", "{}")
        base = yaml.safe_load(ORIGINAL_TEXT)
        merged = _merged(base)
        diff = profile_field_diff(base, merged)

        entry = await _apply(store, merged, field_diff=diff)

        versions = await store.list_versions("db1")
        assert [(v["ver"], v["kind"]) for v in versions] == [(0, "baseline"), (1, "approved")]
        v0 = versions[0]
        assert v0["content"] == ORIGINAL_TEXT
        assert v0["content_sha256"] == _sha(ORIGINAL_TEXT)
        assert v0["profile"] == base and v0["draft_id"] is None and v0["field_diff"] == []
        assert entry == versions[1]
        assert entry["draft_id"] == "d1" and entry["by"] == "admin" and entry["env"] == ENV
        assert entry["field_diff"] == diff and entry["rolled_back_from"] is None
        assert entry["comment_lines_dropped"] == ORIGINAL_COMMENT_LINES
        assert entry["created_at"] and entry["profile"] == merged
        assert set(entry) == {
            "ver", "kind", "content", "content_sha256", "profile", "created_at", "by", "reason",
            "env", "draft_id", "rolled_back_from", "field_diff", "comment_lines_dropped",
        }

        text = path.read_bytes().decode("utf-8")
        assert text == entry["content"] and entry["content_sha256"] == _sha(text)
        header = f"# plans/104 관리자 승인 적용 v1 · {entry['created_at']} · admin · env={ENV}\n"
        assert text.startswith(header)
        assert yaml.safe_load(text) == merged
        assert "samples" not in yaml.safe_load(text)

        applied = json.loads(await fake.get("schema:db1:structure_meta"))
        assert applied == {k: v for k, v in merged.items() if k != "source"}
        assert await fake.exists("schema:db1:column_value_index") == 0
        assert _version_files(backup_root) == ["v0.yaml", "v1.yaml"]

    async def test_first_apply_without_current_file_is_v1(self, env):
        _, store, _, profiles_dir, backup_root = env
        entry = await _apply(store, _merged(None, local=True))
        assert entry["ver"] == 1 and entry["kind"] == "approved"
        assert entry["comment_lines_dropped"] == 0
        assert _version_files(backup_root) == ["v1.yaml"]
        written = yaml.safe_load((profiles_dir / "db1.yaml").read_text(encoding="utf-8"))
        assert written["source"] == "manual" and written["environment"] == "local_sandbox"

    async def test_second_apply_with_same_sha_adds_no_archive(self, env):
        _, store, _, profiles_dir, backup_root = env
        _write_original(profiles_dir)
        first = await _apply(store, _merged(yaml.safe_load(ORIGINAL_TEXT)))
        second = await _apply(store, _merged(first["profile"], {"query_guide": "무시"}))
        assert second["ver"] == 2 and second["kind"] == "approved"
        assert second["comment_lines_dropped"] == 1  # 직전 원문(v1)의 헤더 주석 1줄
        assert _version_files(backup_root) == ["v0.yaml", "v1.yaml", "v2.yaml"]

    async def test_external_edit_archived_as_external_change(self, env):
        _, store, _, profiles_dir, _ = env
        _write_original(profiles_dir)
        await _apply(store, _merged(yaml.safe_load(ORIGINAL_TEXT)))
        edited = "# 배포가 덮어쓴 파일\nsource: manual\npatterns: []\n"
        _write_original(profiles_dir, edited)
        assert store.profile_state("db1")["drift"] is True

        entry = await _apply(store, _merged(yaml.safe_load(edited)))

        versions = await store.list_versions("db1")
        assert [(v["ver"], v["kind"]) for v in versions] == [
            (0, "baseline"), (1, "approved"), (2, "external_change"), (3, "approved"),
        ]
        assert versions[2]["content"] == edited
        assert entry["ver"] == 3
        assert store.profile_state("db1")["drift"] is False

    async def test_concurrent_applies_get_distinct_versions(self, env):
        _, store, _, profiles_dir, _ = env
        a = _merged(None, {"patterns": [], "query_guide": "A"})
        b = _merged(None, {"patterns": [], "query_guide": "B"})
        entries = await asyncio.gather(_apply(store, a), _apply(store, b))
        assert sorted(e["ver"] for e in entries) == [1, 2]
        latest = max(entries, key=lambda e: e["ver"])
        assert (profiles_dir / "db1.yaml").read_bytes().decode("utf-8") == latest["content"]
        assert [v["kind"] for v in await store.list_versions("db1")] == ["approved", "approved"]

    async def test_profile_write_failure_keeps_original_and_version(self, env, monkeypatch, caplog):
        _, store, _, profiles_dir, _ = env
        path = _write_original(profiles_dir)
        real_replace = os.replace

        def failing_replace(src, dst):
            if Path(dst) == path:
                raise OSError("디스크 가득 참")
            real_replace(src, dst)

        monkeypatch.setattr(store_module.os, "replace", failing_replace)
        with caplog.at_level(logging.ERROR, logger="src.schema_cache.structure_store"):
            with pytest.raises(OSError):
                await _apply(store, _merged(yaml.safe_load(ORIGINAL_TEXT)))

        assert path.read_bytes().decode("utf-8") == ORIGINAL_TEXT
        assert not list(profiles_dir.glob(".*.tmp"))
        assert any("현행 파일 쓰기 실패" in r.getMessage() for r in caplog.records)
        state = store.profile_state("db1")
        assert state["latest_ver"] == 1 and state["drift"] is True

    async def test_no_tmp_files_left(self, env):
        _, store, _, profiles_dir, backup_root = env
        _write_original(profiles_dir)
        await _apply(store, _merged(yaml.safe_load(ORIGINAL_TEXT)))
        assert not [p for p in profiles_dir.iterdir() if p.name.endswith(".tmp")]
        versions_dir = backup_root / "db1" / "versions"
        assert not [p for p in versions_dir.iterdir() if p.name.endswith(".tmp")]

    async def test_invalid_arguments(self, env):
        _, store, _, profiles_dir, _ = env
        with pytest.raises(ValueError):
            await _apply(store, {"source": "manual"}, kind="baseline")
        with pytest.raises(TypeError):
            await _apply(store, ["not", "dict"])  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            await _apply(store, {}, kind="rollback", content="- 목록은 매핑이 아니다\n")
        assert not (profiles_dir / "db1.yaml").exists()

    async def test_header_values_are_single_line(self, env):
        _, store, _, profiles_dir, _ = env
        merged = _merged(None)
        await _apply(store, merged, by="admin\nsource: auto", env="http://x\n  y")
        text = (profiles_dir / "db1.yaml").read_text(encoding="utf-8")
        assert text.splitlines()[0].startswith("# plans/104 관리자 승인 적용 v1")
        assert yaml.safe_load(text) == merged

    async def test_redis_unavailable_still_writes_file(self, tmp_path, caplog):
        profiles_dir = tmp_path / "profiles"
        offline = StructureStore(None, tmp_path / "structure", profiles_dir=profiles_dir)
        with caplog.at_level(logging.WARNING, logger="src.schema_cache.structure_store"):
            entry = await offline.apply_profile(
                "db1", _merged(None), by=None, reason="", env=ENV, draft_id=None, field_diff=[],
            )
        assert entry["ver"] == 1
        assert (profiles_dir / "db1.yaml").read_bytes().decode("utf-8") == entry["content"]
        assert any("Redis 미연결" in r.getMessage() for r in caplog.records)

    async def test_redis_command_error_warns_and_continues(self, env, caplog):
        _, store, fake, profiles_dir, _ = env
        fake.set = AsyncMock(side_effect=RuntimeError("boom"))
        with caplog.at_level(logging.WARNING, logger="src.schema_cache.structure_store"):
            entry = await _apply(store, _merged(None))
        assert (profiles_dir / "db1.yaml").read_bytes().decode("utf-8") == entry["content"]
        assert any("적용본 캐시 갱신 실패" in r.getMessage() for r in caplog.records)

    async def test_version_roundtrip_escapes_line_separator_characters(self, env):
        """NEL 같은 문자가 들어 있어도 버전 원문이 그대로 되읽힌다(이스케이프 덤프 폴백)."""
        _, store, _, profiles_dir, _ = env
        text = (
            "# 주석\nsource: manual\n"
            'query_guide: "a\\Nb"\n'  # YAML 이스케이프 \N = NEL
            "# 끝" + chr(0x85) + "\n"
        )
        _write_original(profiles_dir, text)
        await _apply(store, _merged(yaml.safe_load(text)))
        [v0] = [v for v in await store.list_versions("db1") if v["ver"] == 0]
        assert v0["content"] == text


class TestRollback:
    async def test_rollback_to_v0_restores_bytes_and_comments(self, env):
        mgr, store, fake, profiles_dir, _ = env
        path = _write_original(profiles_dir)
        original = yaml.safe_load(ORIGINAL_TEXT)
        approved = await _apply(store, _merged(original))

        entry = await store.rollback("db1", 0, by="admin", reason="되돌리기", env=ENV)

        assert path.read_bytes() == ORIGINAL_TEXT.encode("utf-8")
        assert count_comment_lines(path.read_text(encoding="utf-8")) == ORIGINAL_COMMENT_LINES
        assert entry["ver"] == 2 and entry["kind"] == "rollback" and entry["rolled_back_from"] == 0
        assert entry["content"] == ORIGINAL_TEXT and entry["draft_id"] is None
        assert entry["field_diff"] == profile_field_diff(approved["profile"], original)
        assert entry["comment_lines_dropped"] == 0
        assert json.loads(await fake.get("schema:db1:structure_meta")) == {
            k: v for k, v in original.items() if k != "source"
        }
        # 되돌린 직후 현행 = 최신 버전 → 추가 보관 없음 · drift 없음
        assert [v["ver"] for v in await store.list_versions("db1")] == [0, 1, 2]
        assert store.profile_state("db1")["drift"] is False
        assert mgr._redis_cache is store.redis_cache

    async def test_rollback_to_approved_version(self, env):
        _, store, _, profiles_dir, _ = env
        first = await _apply(store, _merged(None, {"patterns": [], "query_guide": "v1"}))
        await _apply(store, _merged(None, {"patterns": [], "query_guide": "v2"}))
        entry = await store.rollback("db1", 1, by="admin", reason="", env=ENV)
        assert (profiles_dir / "db1.yaml").read_bytes().decode("utf-8") == first["content"]
        assert entry["ver"] == 3 and entry["profile"] == first["profile"]

    async def test_unknown_version_raises(self, env):
        _, store, _, _, _ = env
        with pytest.raises(ValueError):
            await store.rollback("db1", 7, by=None, reason="", env=ENV)


class TestProfileState:
    async def test_state_transitions(self, env):
        _, store, _, profiles_dir, _ = env
        assert store.read_current_profile("db1") is None
        assert store.profile_state("db1") == {
            "exists": False, "source": None, "environment": None, "sha256": None,
            "latest_ver": None, "latest_kind": None, "drift": False,
        }

        path = _write_original(profiles_dir)
        current = store.read_current_profile("db1")
        assert current == {
            "exists": True, "content": ORIGINAL_TEXT, "sha256": _sha(ORIGINAL_TEXT),
            "profile": yaml.safe_load(ORIGINAL_TEXT), "source": "manual", "environment": None,
        }
        assert store.profile_state("db1")["drift"] is False  # 버전 없음 → 판정 안 함

        entry = await _apply(store, _merged(current["profile"], local=True))
        state = store.profile_state("db1")
        assert state["latest_ver"] == 1 and state["latest_kind"] == "approved"
        assert state["environment"] == "local_sandbox" and state["drift"] is False
        assert state["sha256"] == entry["content_sha256"]

        path.write_bytes(path.read_bytes() + "# 사람이 덧붙인 줄\n".encode())
        assert store.profile_state("db1")["drift"] is True

        path.unlink()  # 배포로 현행 파일이 사라짐
        state = store.profile_state("db1")
        assert state["exists"] is False and state["drift"] is True

    def test_unparseable_profile_reports_none(self, env, caplog):
        _, store, _, profiles_dir, _ = env
        _write_original(profiles_dir, "source: [깨진\n")
        with caplog.at_level(logging.WARNING, logger="src.schema_cache.structure_store"):
            current = store.read_current_profile("db1")
        assert current is not None and current["profile"] is None and current["source"] is None
        assert any("파싱 실패" in r.getMessage() for r in caplog.records)


class TestRestoreApplied:
    async def test_restore_fills_redis_only_and_keeps_file(self, env):
        mgr, store, fake, profiles_dir, backup_root = env
        path = _write_original(profiles_dir)
        entry = await _apply(store, _merged(yaml.safe_load(ORIGINAL_TEXT)))
        edited = "# 사람 편집\nsource: manual\n".encode()
        path.write_bytes(edited)
        stat_before = path.stat()
        files_before = _version_files(backup_root)
        await fake.flushdb()

        meta = await store.restore_applied("db1")

        expected = {k: v for k, v in entry["profile"].items() if k != "source"}
        assert meta == expected
        assert json.loads(await fake.get("schema:db1:structure_meta")) == expected
        assert path.read_bytes() == edited and path.stat().st_mtime_ns == stat_before.st_mtime_ns
        assert _version_files(backup_root) == files_before
        assert await fake.exists("schema:db1:structure_versions") == 0

    async def test_manager_restores_after_flush_even_without_profile_file(self, env):
        mgr, store, fake, profiles_dir, _ = env
        entry = await _apply(store, _merged(None))
        (profiles_dir / "db1.yaml").unlink()
        await fake.flushdb()

        expected = {k: v for k, v in entry["profile"].items() if k != "source"}
        assert await mgr.get_applied_structure_meta("db1") == expected
        assert not (profiles_dir / "db1.yaml").exists()
        assert await mgr.has_structure_authority("db1") is True  # 수동 프로필 없이 버전으로

    async def test_restore_without_redis_returns_meta(self, env):
        _, store, _, profiles_dir, backup_root = env
        entry = await _apply(store, _merged(None))
        offline = StructureStore(None, backup_root, profiles_dir=profiles_dir)
        assert await offline.restore_applied("db1") == {
            k: v for k, v in entry["profile"].items() if k != "source"
        }

    async def test_invalidate_keeps_versions_and_applied_is_restored(self, env):
        mgr, store, fake, _, _ = env
        entry = await _apply(store, _merged(None))
        await mgr.invalidate("db1")
        assert await fake.exists("schema:db1:structure_meta") == 0

        expected = {k: v for k, v in entry["profile"].items() if k != "source"}
        assert await mgr.get_applied_structure_meta("db1") == expected
        assert await fake.exists("schema:db1:structure_meta") == 1

    async def test_invalidate_all_keeps_admin_assets(self, env):
        mgr, store, _, _, _ = env
        entry = await _apply(store, _merged(None))
        await store.add_draft("db1", {"meta": DRAFT_META})
        await store.save_snapshot(
            "db1", {"snapshot": {}, "hash": "h", "taken_at": "t", "env": "e", "by": None}
        )
        await store.record_step("db1", "schema", status="done", count=1)
        await mgr.invalidate_all()

        assert await store.list_drafts("db1")
        assert await store.load_snapshot("db1")
        assert await store.load_registration("db1")
        assert await mgr.get_applied_structure_meta("db1") == {
            k: v for k, v in entry["profile"].items() if k != "source"
        }

    async def test_nothing_applied_returns_none(self, env):
        mgr, store, _, _, _ = env
        assert await store.restore_applied("db1") is None
        assert store.has_versions("db1") is False
        assert await mgr.get_applied_structure_meta("db1") is None
        assert await mgr.has_structure_authority("db1") is False

    async def test_approved_version_wins_over_redis_structure_meta(self, env):
        """적용본은 승인 버전이다 — Redis `structure_meta` 원시 값을 먼저 읽지 않고 버전으로 덮는다.

        (2026-09-17 사용자 확정: 승인 버전만 적용본 · 레거시 Redis 값은 관리자 후보)
        """
        mgr, store, fake, _, _ = env
        entry = await _apply(store, _merged(None))
        legacy = {"patterns": [], "query_guide": "R"}
        await fake.set("schema:db1:structure_meta", json.dumps(legacy))
        expected = {k: v for k, v in entry["profile"].items() if k != "source"}
        assert await mgr.get_applied_structure_meta("db1") == expected
        assert json.loads(await fake.get("schema:db1:structure_meta")) == expected

    async def test_lookup_failure_warns_and_returns_none(self, env, caplog):
        mgr, store, _, _, _ = env
        store.restore_applied = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        with caplog.at_level(logging.WARNING, logger="src.schema_cache.cache_manager"):
            assert await mgr.get_applied_structure_meta("db1") is None
        assert any("적용본 구조 정보 조회 실패" in r.getMessage() for r in caplog.records)

    async def test_corrupt_version_skipped_and_number_not_reused(self, env, caplog):
        _, store, _, _, backup_root = env
        await _apply(store, _merged(None, {"patterns": [], "query_guide": "v1"}))
        (backup_root / "db1" / "versions" / "v2.yaml").write_text(
            "- 목록이라 형식 오류\n", encoding="utf-8"
        )
        with caplog.at_level(logging.WARNING, logger="src.schema_cache.structure_store"):
            assert [v["ver"] for v in await store.list_versions("db1")] == [1]
            assert store.profile_state("db1")["latest_ver"] == 1
        assert any("형식 오류" in r.getMessage() for r in caplog.records)

        entry = await _apply(store, _merged(None, {"patterns": [], "query_guide": "v3"}))
        assert entry["ver"] == 3
        assert (backup_root / "db1" / "versions" / "v2.yaml").read_text(encoding="utf-8") == (
            "- 목록이라 형식 오류\n"
        )


class TestDescriptionsBackup:
    def test_roundtrip(self, env):
        _, store, _, _, backup_root = env
        descriptions = {"node_t.label": "노드 이름", "node_t.parent_id": "상위 노드"}
        synonyms = {
            "node_t.label": {
                "words": ["이름", "명칭"],
                "sources": {"이름": "manual", "명칭": "llm"},
            },
        }
        path = store.backup_descriptions("db1", descriptions, synonyms)

        assert path == backup_root / "db1" / "descriptions.yaml"
        assert not list(path.parent.glob(".*.tmp"))
        loaded = store.load_descriptions_backup("db1")
        assert loaded is not None
        assert loaded["descriptions"] == descriptions and loaded["synonyms"] == synonyms
        assert isinstance(loaded["saved_at"], str) and loaded["saved_at"]
        assert set(loaded) == {"descriptions", "synonyms", "saved_at"}

    def test_missing_and_corrupt(self, env, caplog):
        _, store, _, _, backup_root = env
        assert store.load_descriptions_backup("db1") is None
        (backup_root / "db1").mkdir(parents=True)
        (backup_root / "db1" / "descriptions.yaml").write_text("- 목록\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="src.schema_cache.structure_store"):
            assert store.load_descriptions_backup("db1") is None
        assert any("설명 백업 형식 오류" in r.getMessage() for r in caplog.records)


class TestWithoutRedis:
    async def test_file_only_store_reads_files_and_refuses_redis_writes(self, env):
        _, store, _, profiles_dir, backup_root = env
        await _apply(store, _merged(None))
        offline = StructureStore(None, backup_root, profiles_dir=profiles_dir)

        assert offline.available is False
        assert [v["ver"] for v in await offline.list_versions("db1")] == [1]
        assert offline.has_versions("db1") is True
        assert await offline.list_drafts("db1") == []
        assert await offline.load_snapshot("db1") is None
        assert await offline.load_registration("db1") == {}
        with pytest.raises(StructureStoreUnavailable):
            await offline.add_draft("db1", {})
        with pytest.raises(StructureStoreUnavailable):
            await offline.record_step("db1", "schema", status="done")

    async def test_disconnected_redis_cache_is_unavailable(self, tmp_path):
        cache = RedisSchemaCache(MagicMock())
        cache.connect = AsyncMock(side_effect=ConnectionError("refused"))
        store = StructureStore(cache, tmp_path / "structure", profiles_dir=tmp_path / "profiles")
        assert store.available is False
        with pytest.raises(StructureStoreUnavailable):
            await store.save_snapshot("db1", {})


class TestDbIdValidation:
    @pytest.mark.parametrize("bad", ["../etc", "a/b", "a:b", "", "db 1", "db.1"])
    async def test_invalid_db_id_rejected(self, env, bad):
        _, store, _, profiles_dir, backup_root = env
        with pytest.raises(ValueError):
            await store.apply_profile(
                bad, {"source": "manual"}, by=None, reason="", env="", draft_id=None, field_diff=[]
            )
        with pytest.raises(ValueError):
            await store.rollback(bad, 0, by=None, reason="", env="")
        with pytest.raises(ValueError):
            await store.list_versions(bad)
        with pytest.raises(ValueError):
            await store.restore_applied(bad)
        for sync_call in (
            store.has_versions, store.profile_state, store.read_current_profile,
            store.load_descriptions_backup,
        ):
            with pytest.raises(ValueError):
                sync_call(bad)
        with pytest.raises(ValueError):
            store.backup_descriptions(bad, {}, {})
        assert not profiles_dir.exists()
        assert not backup_root.exists()

    def test_valid_db_ids(self):
        for ok in ("db_a", "itam", "DB-2", "a1"):
            assert validate_db_id(ok) == ok


class TestDrafts:
    async def test_structure_draft_lifecycle(self, env):
        _, store, _, _, _ = env
        first = await store.add_draft(
            "db1", {"meta": DRAFT_META, "scope": "all", "status": "무시됨"}
        )
        second = await store.add_draft("db1", {"meta": {"patterns": []}, "scope": "tables"})

        assert len(first["draft_id"]) == 12 and first["status"] == "pending" and first["created_at"]
        assert first["meta"] == DRAFT_META
        assert [d["draft_id"] for d in await store.list_drafts("db1")] == [
            second["draft_id"], first["draft_id"],
        ]
        assert await store.get_draft("db1", first["draft_id"]) == first

        updated = await store.update_draft(
            "db1", first["draft_id"], status="rejected", reason="틀림"
        )
        assert updated is not None
        assert updated["status"] == "rejected" and updated["reason"] == "틀림"
        assert (await store.get_draft("db1", first["draft_id"]))["status"] == "rejected"
        assert await store.update_draft("db1", "없는초안", status="x") is None
        assert await store.get_draft("db1", "없는초안") is None

    async def test_description_draft_lifecycle(self, env):
        _, store, _, _, _ = env
        draft = await store.add_description_draft(
            "db1", {"descriptions": {"t1.c": "설명"}, "synonyms": {}, "failed_tables": []}
        )
        assert draft["status"] == "pending"
        assert await store.list_description_drafts("db1") == [draft]
        assert await store.get_description_draft("db1", draft["draft_id"]) == draft
        applied = await store.update_description_draft("db1", draft["draft_id"], status="applied")
        assert applied is not None and applied["status"] == "applied"
        # 구조 초안과 키가 분리돼 있다
        assert await store.list_drafts("db1") == []


class TestSnapshotAndRegistration:
    async def test_snapshot_and_check_result_roundtrip(self, env):
        _, store, fake, _, _ = env
        record = {"snapshot": {"tables": {}}, "hash": "h1", "taken_at": "2026-09-17T10:00:00+09:00",
                  "env": "http://mcp.test", "by": "admin"}
        await store.save_snapshot("db1", record)
        await store.save_check_result("db1", {"has_changes": False})
        assert await store.load_snapshot("db1") == record
        assert await store.load_check_result("db1") == {"has_changes": False}
        assert await fake.exists("schema:db1:schema_snapshot", "schema:db1:check_result") == 2

    async def test_record_step_merges_steps(self, env):
        _, store, _, _, _ = env
        schema = await store.record_step(
            "db1", "schema", status="done", count=12, by="admin", env="http://mcp.test"
        )
        await store.record_step(
            "db1", "descriptions", status="failed", provider="mlx", detail={"failed_tables": ["t1"]}
        )
        registration = await store.load_registration("db1")

        assert schema["at"] and schema["count"] == 12
        assert set(registration) == {"schema", "descriptions"}
        assert registration["schema"] == schema
        assert registration["descriptions"]["detail"] == {"failed_tables": ["t1"]}
        assert registration["descriptions"]["provider"] == "mlx"


class TestWriteBoundary:
    async def test_only_profile_file_is_written_under_config(self, env, tmp_path):
        """프로필 쓰기는 `{profiles_dir}/{db_id}.yaml` 하나뿐이다.

        초안·등록 상태·무효화·적용본 조회는 config에 쓰지 않는다.
        """
        mgr, store, _, _, _ = env
        await store.add_draft("db1", {"meta": DRAFT_META})
        await store.record_step("db1", "schema", status="done")
        await mgr.invalidate("db1")
        await mgr.get_applied_structure_meta("db1")
        assert not (tmp_path / "config").exists()

        await _apply(store, _merged(None))
        config_dir = tmp_path / "config"
        written = sorted(p.relative_to(tmp_path).as_posix() for p in config_dir.rglob("*"))
        assert written == ["config/db_profiles", "config/db_profiles/db1.yaml"]


SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
_FILE_WRITE_RE = re.compile(
    r"write_text\(|write_bytes\(|open\([^)]*['\"][wax]|os\.replace\(|os\.remove\(|"
    r"\.unlink\(|shutil\.(?:copy|move)"
)


def _src_files_matching(pattern: re.Pattern[str]) -> set[str]:
    return {
        path.relative_to(SRC_ROOT.parent).as_posix()
        for path in SRC_ROOT.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    }


class TestProfileWriteSingleEntry:
    """질의 경로 보호(계약 §3) — 프로필 쓰기는 저장소 한 곳이고 호출자는 관리자 서비스뿐이다.

    grep 단언이라 호출 모양(`apply_profile(`·`store.rollback(`)이 바뀌면 함께 고친다.
    """

    def test_apply_and_rollback_called_only_by_admin_service(self):
        store_file = "src/schema_cache/structure_store.py"
        callers = _src_files_matching(re.compile(r"(?<!def )apply_profile\(")) - {store_file}
        rollback_callers = _src_files_matching(re.compile(r"store\.rollback\(")) - {store_file}
        assert callers <= {"src/schema_cache/db_structure_service.py"}
        assert rollback_callers <= {"src/schema_cache/db_structure_service.py"}

    def test_only_store_writes_files_next_to_profile_paths(self):
        """`db_profiles`를 언급하면서 파일 쓰기·교체·삭제를 하는 모듈은 저장소뿐이다."""
        mentions = _src_files_matching(re.compile(r"db_profiles"))
        writers = {
            rel for rel in mentions
            if _FILE_WRITE_RE.search((SRC_ROOT.parent / rel).read_text(encoding="utf-8"))
        }
        assert writers == {"src/schema_cache/structure_store.py"}
