"""plans/104 — 레거시 Redis `structure_meta`는 적용본이 아니다 (2026-09-17 사용자 확정).

확정 내용: 승인 버전(approved·rollback)만 적용본으로 인정하고, 승인 버전 없는 Redis
`structure_meta`(예전 질의 경로 LLM 분석본)와 수동 프로필의 Redis 사본은
`get_applied_structure_meta`·`has_structure_authority`가 적용본으로 보지 않는다(질의 경로·전역 등록
판정 대칭). 관리자 「DB 구조」 탭에는 "레거시 분석본 — 승인 필요" 후보로 보이고, 승인하면 일반
초안과 같은 규칙으로 버전화된다. 레거시 키는 지우지 않는다.

이 파일이 고정하는 것:
  ① 매니저·저장소 — 레거시만 있으면 적용본 None·정본 아님 · 수동 프로필 사본도(파일 삭제 뒤)
     아님 · 승인 버전이 있으면 그 버전을 돌려주고 Redis 캐시를 갱신 · baseline·external_change만이면
     승인 아님
  ② 질의 경로(schema_analyzer) — 레거시만 있는 DB는 `structure_missing` 노트(대칭)
  ③ 서비스 — 목록 `legacy_candidate`·경고 우선순위 · 상세 요약 · 레거시 초안 잡 → 검증 → 승인 →
     버전화 · 레거시 키 삭제 0 · 후보가 아니면 거절

작업 디렉터리·프로필 디렉터리·버전 루트는 전부 tmp — 실제 `config/db_profiles/`에 쓰지 않는다.
LLM은 목(샘플 SQL 응답만) · MCP는 가짜 세션 · Redis는 인메모리 페이크. 이름은 가상.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from src.nodes.schema_analyzer import schema_analyzer
from src.schema_cache.cache_manager import SchemaCacheManager
from src.schema_cache.db_structure_service import DBStructureService, DraftNotApprovable
from src.schema_cache.structure_store import StructureStore
from src.state import create_initial_state
from src.utils.prior_dependency import NOTE_STRUCTURE_MISSING, structure_missing_note
from tests.mocks.async_redis import attach_fake_redis
from tests.test_schema_cache.test_plan104_service_fixtures import (
    Env,
    FakeTable,
    MockLLM,
    make_env,
    make_registry,
)

DB = "db1"
LEGACY_META = {
    "patterns": [{
        "type": "hierarchy", "table": "t_node", "id_column": "id",
        "parent_column": "parent_id", "type_column": "node_type",
    }],
    "query_guide": "예전 질의 경로가 분석한 안내",
    "samples": {"루트": [{"id": 1}]},
}
APPROVED_PROFILE = {"source": "manual", "patterns": [], "query_guide": "승인 안내"}


def _meta_key(db_id: str) -> str:
    return f"schema:{db_id}:structure_meta"


# ──────────────────────────────────────────────
# ① 매니저·저장소
# ──────────────────────────────────────────────


@pytest.fixture
def mgr_env(tmp_path, monkeypatch):
    """(매니저, 저장소, 페이크 Redis, 프로필 디렉터리, 버전 루트) — 작업 디렉터리는 tmp."""
    monkeypatch.chdir(tmp_path)
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = True
    mgr = SchemaCacheManager(config)
    fake = attach_fake_redis(mgr._redis_cache)
    profiles_dir = tmp_path / "config" / "db_profiles"
    backup_root = tmp_path / ".cache" / "structure"
    store = StructureStore(mgr._redis_cache, backup_root, profiles_dir=profiles_dir)
    mgr._structure_store = store
    return mgr, store, fake, profiles_dir, backup_root


def _write_version(backup_root: Path, ver: int, kind: str, profile: dict) -> None:
    """버전 파일을 직접 쓴다(적용 API가 만들지 않는 조합 — baseline·external_change만 남은 상태)."""
    content = yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)
    entry = {"ver": ver, "kind": kind, "content": content, "profile": profile}
    directory = backup_root / DB / "versions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"v{ver}.yaml").write_text(
        yaml.safe_dump(entry, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


class TestAppliedIsApprovedVersionOnly:
    async def test_legacy_redis_meta_alone_is_not_applied(self, mgr_env):
        mgr, store, fake, _, _ = mgr_env
        await fake.set(_meta_key(DB), json.dumps(LEGACY_META, ensure_ascii=False))

        assert await mgr.get_applied_structure_meta(DB) is None
        assert await mgr.has_structure_authority(DB) is False
        assert await mgr.get_structure_meta_or_profile(DB) is None  # 파일도 없음
        assert store.has_approved_version(DB) is False
        # 판정은 읽기만 한다 — 레거시 키는 그대로 남는다
        assert json.loads(await fake.get(_meta_key(DB))) == LEGACY_META

    async def test_manual_profile_redis_copy_is_not_applied_once_file_is_gone(self, mgr_env):
        mgr, _, fake, profiles_dir, _ = mgr_env
        profiles_dir.mkdir(parents=True)
        path = profiles_dir / f"{DB}.yaml"
        path.write_text("source: manual\npatterns: []\nquery_guide: 수동\n", encoding="utf-8")
        # 질의 경로가 수동 프로필을 읽을 때 남기는 Redis 사본과 같은 모양
        assert await mgr.save_structure_meta(DB, {"patterns": [], "query_guide": "수동"})
        assert await mgr.has_structure_authority(DB) is True  # 수동 프로필 파일이 정본

        path.unlink()

        assert await fake.exists(_meta_key(DB)) == 1
        assert await mgr.get_applied_structure_meta(DB) is None
        assert await mgr.has_structure_authority(DB) is False

    async def test_approved_version_is_returned_and_refreshes_redis_cache(self, mgr_env):
        mgr, store, fake, profiles_dir, _ = mgr_env
        await fake.set(_meta_key(DB), json.dumps(LEGACY_META, ensure_ascii=False))
        await store.apply_profile(
            DB, dict(APPROVED_PROFILE), by="admin", reason="승인", env="http://mcp.test",
            draft_id="d1", field_diff=[],
        )
        (profiles_dir / f"{DB}.yaml").unlink()  # 배포로 현행 파일이 사라져도 버전이 적용본
        await fake.set(_meta_key(DB), json.dumps(LEGACY_META, ensure_ascii=False))

        expected = {"patterns": [], "query_guide": "승인 안내"}
        assert await mgr.get_applied_structure_meta(DB) == expected
        assert json.loads(await fake.get(_meta_key(DB))) == expected  # 캐시를 버전으로 갱신
        assert await mgr.has_structure_authority(DB) is True
        assert await mgr.get_structure_meta_or_profile(DB) == expected
        assert not (profiles_dir / f"{DB}.yaml").exists()  # 복원은 파일을 쓰지 않는다

    async def test_baseline_and_external_change_only_are_not_approved(self, mgr_env):
        mgr, store, fake, _, backup_root = mgr_env
        _write_version(backup_root, 0, "baseline", {"source": "manual", "query_guide": "원본"})
        _write_version(
            backup_root, 1, "external_change", {"source": "manual", "query_guide": "편집"}
        )

        assert store.has_versions(DB) is True
        assert store.latest_applied_version(DB) is None
        assert store.has_approved_version(DB) is False
        assert await store.restore_applied(DB) is None
        assert await mgr.get_applied_structure_meta(DB) is None
        assert await mgr.has_structure_authority(DB) is False
        assert await fake.exists(_meta_key(DB)) == 0

    async def test_latest_applied_skips_trailing_archive_versions(self, mgr_env):
        """최신 번호가 보관(external_change)이어도 적용본은 그 앞의 가장 큰 승인 버전이다."""
        _, store, _, _, backup_root = mgr_env
        _write_version(backup_root, 0, "baseline", {"source": "manual", "query_guide": "원본"})
        _write_version(backup_root, 1, "approved", {"source": "manual", "query_guide": "v1"})
        _write_version(backup_root, 2, "rollback", {"source": "manual", "query_guide": "v2"})
        _write_version(
            backup_root, 3, "external_change", {"source": "manual", "query_guide": "편집"}
        )

        latest = store.latest_applied_version(DB)
        assert latest is not None and (latest["ver"], latest["kind"]) == (2, "rollback")
        assert await store.restore_applied(DB) == {"query_guide": "v2"}

    async def test_structure_meta_or_profile_keeps_manual_file_fallback(self, mgr_env):
        """승인 버전이 없으면 레거시 Redis 값 대신 프로필 파일 폴백(현행 동작)을 쓴다."""
        mgr, _, fake, profiles_dir, _ = mgr_env
        await fake.set(_meta_key(DB), json.dumps(LEGACY_META, ensure_ascii=False))
        profiles_dir.mkdir(parents=True)
        (profiles_dir / f"{DB}.yaml").write_text(
            "source: manual\npatterns: []\nquery_guide: 파일\n", encoding="utf-8"
        )
        assert await mgr.get_structure_meta_or_profile(DB) == {
            "patterns": [], "query_guide": "파일",
        }


# ──────────────────────────────────────────────
# ② 질의 경로 대칭
# ──────────────────────────────────────────────


@asynccontextmanager
async def _query_path(mgr: SchemaCacheManager):
    schema = {
        "tables": {"t_node": {"columns": [{"name": "id", "type": "int"}]}},
        "relationships": [],
    }
    mgr.get_schema_or_fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=(schema, True, "메모리", {"t_node.id": "노드 식별자"}, {})
    )
    client = AsyncMock()
    client.get_sample_data = AsyncMock(return_value=[])

    @asynccontextmanager
    async def _db_ctx(*_a, **_kw):
        yield client

    with patch("src.nodes.schema_analyzer.get_db_client", side_effect=_db_ctx), \
         patch("src.nodes.schema_analyzer.get_cache_manager", return_value=mgr):
        yield


def _query_state() -> dict:
    state = create_initial_state(user_query="노드 목록")
    state["active_db_id"] = DB
    state["parsed_requirements"] = {
        "query_targets": [], "original_query": "노드 목록", "output_format": "text",
    }
    return state


class TestQueryPathSymmetry:
    async def test_legacy_only_db_gets_structure_missing_note(self, mgr_env, mock_config):
        mgr, _, fake, _, _ = mgr_env
        await fake.set(_meta_key(DB), json.dumps(LEGACY_META, ensure_ascii=False))

        async with _query_path(mgr):
            out = await schema_analyzer(_query_state(), llm=AsyncMock(), app_config=mock_config)

        assert "_structure_meta" not in out["schema_info"]
        kinds = [n["kind"] for n in out.get("dependency_notes") or []]
        assert kinds.count(NOTE_STRUCTURE_MISSING) == 1
        assert structure_missing_note(DB) in out["dependency_notes"]
        assert json.loads(await fake.get(_meta_key(DB))) == LEGACY_META  # 삭제·덮어쓰기 0

    async def test_manual_profile_copy_left_in_redis_is_not_used_after_file_removal(
        self, mgr_env, mock_config
    ):
        mgr, _, fake, profiles_dir, _ = mgr_env
        profiles_dir.mkdir(parents=True)
        path = profiles_dir / f"{DB}.yaml"
        path.write_text("source: manual\npatterns: []\nquery_guide: 수동 안내\n", encoding="utf-8")

        async with _query_path(mgr):
            first = await schema_analyzer(_query_state(), llm=AsyncMock(), app_config=mock_config)
            assert first["schema_info"]["_structure_meta"]["query_guide"] == "수동 안내"
            assert await fake.exists(_meta_key(DB)) == 1  # 질의 경로의 Redis 사본

            path.unlink()
            second = await schema_analyzer(_query_state(), llm=AsyncMock(), app_config=mock_config)

        assert "_structure_meta" not in second["schema_info"]
        assert structure_missing_note(DB) in second["dependency_notes"]


# ──────────────────────────────────────────────
# ③ 서비스 — 목록·상세·레거시 초안 잡·승인
# ──────────────────────────────────────────────

SRC = "app_legacy"


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    e = make_env(tmp_path, monkeypatch, active=(SRC,))
    e.session.add_source(SRC, "postgresql", {
        "t_node": FakeTable([
            ("id", "integer", False, True),
            ("parent_id", "integer", True, False),
            ("node_type", "character varying", True, False),
        ]),
    })
    e.session.sql_rows = lambda source, sql: [{"id": 1}]
    e.registry = make_registry({"db_id": SRC, "engine": "postgresql", "description": "가상"})
    e.llm = MockLLM(samples=lambda prompt: json.dumps(
        [{"purpose": "루트 노드", "sql": "SELECT id FROM t_node LIMIT 5"}], ensure_ascii=False
    ))
    return e


def _service(env: Env) -> DBStructureService:
    return DBStructureService(env.config, env.mgr, **env.service_kwargs())


async def _set_legacy(env: Env, meta: dict = LEGACY_META) -> None:
    await env.fake_redis.set(_meta_key(SRC), json.dumps(meta, ensure_ascii=False))


async def _row(service: DBStructureService) -> dict:
    listed = await service.list_sources()
    return next(r for r in listed["sources"] if r["source"] == SRC)


class TestLegacyCandidateDisplay:
    async def test_list_and_detail_show_legacy_candidate(self, env):
        await _set_legacy(env)
        service = _service(env)

        row = await _row(service)
        assert row["structure"]["status"] == "none"
        assert row["structure"]["legacy_candidate"] is True
        # 활성인데 구조 없음보다 앞선다
        assert row["structure"]["warning"] == "legacy_unapproved"

        detail = await service.get_detail(SRC)
        assert detail["structure"]["legacy_candidate"] is True
        assert detail["legacy_meta"] == {
            "pattern_count": 1,
            "query_guide_length": len(LEGACY_META["query_guide"]),
            "has_samples": True,
        }

    async def test_without_legacy_value_warning_is_unchanged(self, env):
        row = await _row(_service(env))
        assert row["structure"]["legacy_candidate"] is False
        assert row["structure"]["warning"] == "active_without_structure"
        assert (await _service(env).get_detail(SRC))["legacy_meta"] is None

    async def test_manual_profile_redis_copy_is_not_a_candidate(self, env):
        env.write_profile(SRC, {"source": "manual", "patterns": [], "query_guide": "수동"})
        await _set_legacy(env, {"patterns": [], "query_guide": "수동"})
        row = await _row(_service(env))
        assert row["structure"]["status"] == "manual"
        assert row["structure"]["legacy_candidate"] is False
        assert row["structure"]["warning"] is None

    async def test_empty_legacy_value_is_not_a_candidate(self, env):
        await env.fake_redis.set(_meta_key(SRC), "{}")
        assert (await _row(_service(env)))["structure"]["legacy_candidate"] is False


class TestLegacyDraftJob:
    async def test_draft_validate_approve_versions_and_keeps_legacy_key(self, env):
        await _set_legacy(env)
        deleted: list[str] = []
        real_delete = env.fake_redis.delete

        async def spy_delete(*names: str) -> int:
            deleted.extend(names)
            return await real_delete(*names)

        env.fake_redis.delete = spy_delete  # type: ignore[method-assign]
        service = _service(env)

        result = await service.run_legacy_draft(SRC, by="admin", ctx=env.ctx)

        assert result["scope"] == "legacy" and result["analysis_status"] == "ok"
        assert result["validation_passed"] is True
        assert result["llm_calls"] == {"structure": 0, "samples": 1}
        assert env.llm.kinds("structure") == []            # LLM 구조 분석 0
        assert len(env.llm.kinds("samples")) == 1          # 샘플은 새로 만든다
        draft = await env.store.get_draft(SRC, result["draft_id"])
        assert draft["scope"] == "legacy" and draft["status"] == "pending"
        assert draft["meta"] == {
            "patterns": LEGACY_META["patterns"], "query_guide": LEGACY_META["query_guide"],
        }
        assert [c["code"] for c in draft["validation"]["checks"]] == [
            "refs_exist", "join_types", "sample_sql_safe", "sample_exec",
        ]
        assert draft["sample_attempts"][0]["status"] == "ok"
        assert draft["merged_profile"]["source"] == "manual"
        assert "samples" not in draft["merged_profile"]
        assert any(d["path"].startswith("patterns") for d in draft["field_diff"])
        assert await env.store.list_versions(SRC) == []
        assert not (env.profiles_dir / f"{SRC}.yaml").exists()
        # 초안 단계에서는 레거시 값이 그대로다(적용본 아님)
        assert json.loads(await env.fake_redis.get(_meta_key(SRC))) == LEGACY_META
        assert await env.mgr.get_applied_structure_meta(SRC) is None

        approved = await service.approve_draft(SRC, result["draft_id"], by="admin", reason="검토")

        assert approved["version"]["ver"] == 1 and approved["version"]["kind"] == "approved"
        profile = yaml.safe_load((env.profiles_dir / f"{SRC}.yaml").read_text(encoding="utf-8"))
        assert profile["source"] == "manual"
        assert profile["patterns"] == LEGACY_META["patterns"]
        assert profile["query_guide"] == LEGACY_META["query_guide"]
        assert "samples" not in profile
        assert env.store.has_approved_version(SRC) is True
        applied = await env.mgr.get_applied_structure_meta(SRC)
        assert applied is not None and applied["patterns"] == LEGACY_META["patterns"]
        assert await env.mgr.has_structure_authority(SRC) is True
        row = await _row(service)
        assert row["structure"]["status"] == "approved"
        assert row["structure"]["legacy_candidate"] is False
        assert row["structure"]["warning"] is None
        # 레거시 키는 지우지 않는다 — 승인 적용이 적용본 캐시로 덮을 뿐
        assert _meta_key(SRC) not in deleted
        assert await env.fake_redis.exists(_meta_key(SRC)) == 1

    async def test_invalid_legacy_pattern_fails_validation_and_cannot_be_approved(self, env):
        await _set_legacy(env, {
            "patterns": [{"type": "hierarchy", "table": "t_missing", "id_column": "id",
                          "parent_column": "parent_id"}],
            "query_guide": "없는 테이블을 가리키는 예전 분석",
        })
        service = _service(env)
        result = await service.run_legacy_draft(SRC, by="admin", ctx=env.ctx)

        assert result["validation_passed"] is False
        with pytest.raises(DraftNotApprovable) as exc:
            await service.approve_draft(SRC, result["draft_id"], by="admin", reason="확인")
        assert exc.value.code == "validation_failed"
        assert not (env.profiles_dir / f"{SRC}.yaml").exists()
        assert env.store.has_approved_version(SRC) is False

    async def test_no_patterns_legacy_skips_llm(self, env):
        await _set_legacy(env, {"patterns": [], "query_guide": "패턴 없는 예전 분석"})
        result = await _service(env).run_legacy_draft(SRC, by="admin", ctx=env.ctx)
        assert result["analysis_status"] == "no_patterns" and result["validation_passed"] is True
        assert env.llm.calls == []

    @pytest.mark.parametrize("case", ["no_value", "manual_profile", "approved_version"])
    async def test_not_a_candidate_is_rejected_without_side_effects(self, env, case):
        if case == "manual_profile":
            env.write_profile(SRC, {"source": "manual", "patterns": [], "query_guide": "수동"})
            await _set_legacy(env)
        elif case == "approved_version":
            await env.store.apply_profile(
                SRC, dict(APPROVED_PROFILE), by="admin", reason="승인", env="http://x",
                draft_id=None, field_diff=[],
            )
            await _set_legacy(env)
        service = _service(env)

        with pytest.raises(DraftNotApprovable) as exc:
            await service.run_legacy_draft(SRC, by="admin", ctx=env.ctx)

        assert exc.value.code == "legacy_not_found"
        assert await env.store.list_drafts(SRC) == []
        assert env.llm.calls == []
        assert env.session.calls == []
