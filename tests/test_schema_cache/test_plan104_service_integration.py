"""plans/104 A-6 통합 — 서비스 × 실제 `StructureStore`(v2) × 실제 `profile_merge`.

tmp 프로필 디렉터리 + 인메모리 Redis 페이크로 「분석 → 승인 → 질의 경로 적용본 → 되돌리기」를 끝까지
돌린다. 목은 MCP 세션과 LLM뿐이다.

- 승인: 현행 수동 프로필(주석 포함)이 v0 baseline으로 보관되고 v1 approved가 파일에 쓰인다 ·
  수동 전용 필드 보존 · Redis 적용본 캐시 갱신 · 목록의 구조 상태 `approved`.
- Redis를 비워도 매니저의 적용본 조회가 버전에서 복원한다(파일 쓰기 없음).
- 되돌리기(v0): 현행 파일이 원문 바이트(주석 포함) 그대로 돌아오고 v2 rollback이 남는다.
"""

from __future__ import annotations

import json

import pytest
import yaml

from src.schema_cache.db_structure_service import DBStructureService
from tests.test_schema_cache.test_plan104_service_fixtures import (
    REMOTE_ENV,
    FakeTable,
    MockLLM,
    make_env,
    make_registry,
)

SRC = "app_gamma"

ORIGINAL = (
    "# 사람이 관리하는 수동 프로필 — 이 주석은 되돌리기로 복원돼야 한다\n"
    "source: manual\n"
    "patterns: []\n"
    "query_guide: ''\n"
    "# 허용 테이블은 사람이 고른다\n"
    "allowed_tables:\n"
    "- t_node\n"
    "query_examples:\n"
    "- question: 노드 목록\n"
    "  sql: SELECT id FROM t_node LIMIT 10\n"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(SRC, "postgresql", {
        "t_node": FakeTable([
            ("id", "integer", False, True),
            ("parent_id", "integer", True, False),
            ("node_type", "character varying", True, False),
        ]),
    })
    e.session.sql_rows = lambda source, sql: [{"id": 1}]
    e.registry = make_registry({"db_id": SRC, "engine": "postgresql", "description": "가상"})
    e.llm = MockLLM(
        structure=lambda prompt: json.dumps({
            "patterns": [{
                "type": "hierarchy", "table": "t_node", "id_column": "id",
                "parent_column": "parent_id", "type_column": "node_type",
            }],
            "query_guide": "계층은 parent_id로 따라간다",
        }, ensure_ascii=False),
        samples=lambda prompt: json.dumps(
            [{"purpose": "루트", "sql": "SELECT id FROM t_node LIMIT 5"}]
        ),
    )
    path = e.profiles_dir / f"{SRC}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ORIGINAL.encode("utf-8"))
    return e


async def test_analyze_approve_restore_and_rollback_with_real_store(env):
    service = DBStructureService(env.config, env.mgr, **env.service_kwargs())
    profile_path = env.profiles_dir / f"{SRC}.yaml"

    analyzed = await service.run_analyze(
        SRC, scope="all", tables=None, code_columns=None, by="admin", ctx=env.ctx
    )
    assert analyzed["validation_passed"] is True
    draft = await env.store.get_draft(SRC, analyzed["draft_id"])
    assert draft["comment_lines_in_current"] == 2

    approved = await service.approve_draft(
        SRC, analyzed["draft_id"], by="admin", reason="검토 완료"
    )

    # 파일: 헤더 1줄 + 병합 결과 · 수동 필드 보존 · LLM 필드 반영
    written = profile_path.read_text(encoding="utf-8")
    assert written.startswith("# plans/104 관리자 승인 적용 v1 ")
    profile = yaml.safe_load(written)
    assert profile["source"] == "manual" and "environment" not in profile
    assert profile["allowed_tables"] == ["t_node"]
    assert profile["query_examples"][0]["question"] == "노드 목록"
    assert profile["query_guide"] == "계층은 parent_id로 따라간다"
    assert profile["patterns"][0]["type_column"] == "node_type"
    assert "samples" not in profile

    # 버전: v0 baseline(원문 보관) → v1 approved
    versions = await env.store.list_versions(SRC)
    assert [(v["ver"], v["kind"]) for v in versions] == [(0, "baseline"), (1, "approved")]
    assert versions[0]["content"] == ORIGINAL
    assert versions[1]["env"] == REMOTE_ENV and versions[1]["draft_id"] == analyzed["draft_id"]
    assert approved["version"]["comment_lines_dropped"] == 2
    assert "content" not in approved["version"]

    # 질의 경로 적용본: Redis 캐시 → 비운 뒤에도 버전에서 복원
    meta = await env.mgr.get_applied_structure_meta(SRC)
    assert meta["patterns"][0]["table"] == "t_node"
    await env.fake_redis.flushdb()
    restored = await env.mgr.get_applied_structure_meta(SRC)
    assert restored is not None and restored["query_guide"] == "계층은 parent_id로 따라간다"
    assert profile_path.read_text(encoding="utf-8") == written  # 복원은 파일을 쓰지 않는다

    listed = await service.list_sources()
    row = next(r for r in listed["sources"] if r["source"] == SRC)
    assert row["structure"]["status"] == "approved" and row["structure"]["latest_ver"] == 1
    detail = await service.get_detail(SRC)
    assert [v["kind"] for v in detail["versions"]] == ["baseline", "approved"]
    assert all("content" not in v and "profile" not in v for v in detail["versions"])

    # 되돌리기 v0: 원문 바이트(주석 포함) 그대로
    rolled = await service.rollback(SRC, 0, by="admin", reason="원복")
    assert profile_path.read_bytes() == ORIGINAL.encode("utf-8")
    assert rolled["rolled_back_to"] == 0 and rolled["version"]["kind"] == "rollback"
    assert rolled["version"]["ver"] == 2
    state = env.store.profile_state(SRC)
    assert state["drift"] is False and state["latest_kind"] == "rollback"


async def test_approving_on_db_without_profile_creates_manual_profile(env):
    """구조 정보가 없던 DB도 승인하면 같은 경로에 `source: manual` 프로필이 생긴다(v1부터)."""
    (env.profiles_dir / f"{SRC}.yaml").unlink()
    service = DBStructureService(env.config, env.mgr, **env.service_kwargs())
    analyzed = await service.run_analyze(
        SRC, scope="all", tables=None, code_columns=None, by="admin", ctx=env.ctx
    )

    approved = await service.approve_draft(SRC, analyzed["draft_id"], by="admin", reason="첫 승인")

    assert approved["version"]["ver"] == 1 and approved["version"]["kind"] == "approved"
    assert [v["kind"] for v in await env.store.list_versions(SRC)] == ["approved"]
    profile = yaml.safe_load((env.profiles_dir / f"{SRC}.yaml").read_text(encoding="utf-8"))
    assert profile["source"] == "manual"
    assert await env.mgr.has_structure_authority(SRC) is True
