"""plans/104 A-9 목(mock) 리그 — 신규 DB 편입 전 과정을 한 흐름으로 고정한다.

사용자 확정(2026-09-17 · §11.1 A-9): **목으로만 검증한다** — MCP 9099·mlx 8080·실 Redis·임시 앱
서버를 쓰지 않는다. 실 mlx 실측은 사용자가 직접 한다.

한 흐름(`_run_flow`)이 「목록 → 등록 → 설명 초안·적용 → 분석·승인 → 변경 점검 → 설정 조각」을
이어 돌리고, 테스트마다 그 흐름의 한 단면을 단언한다(실패 지점을 좁히려고 단계별로 나눴다).
점검(④)을 승인(⑤) 뒤에 두는 이유는 **구조 영향**을 판정할 대상이 있어야 하기 때문이다 —
승인본이 참조하는 컬럼의 타입이 바뀌면 재분석 필요가 떠야 한다(U2).

고정하는 것:
  ① 목록 — MCP에만 있는 소스가 `mcp_only`·미등록으로 보이고, 활성인데 구조 없는 DB에 경고
  ② 등록 — 스키마 캐시·스냅샷 저장 · `get_table_schema` 호출 수 = 테이블 수 · 지문 SQL 미호출
  ③ 설명 초안 — 목 LLM 초안 → 일부 테이블 제외 적용 → 적용 건수·제외 반영
  ④ 점검 — 타입 변경·컬럼 추가 감지 · 구조 영향 · 신규 코드값
  ⑤ 분석·승인 — 검증 4종 통과 초안 → tmp 프로필에 `source: manual` · 버전 v0/v1 · 되돌리기 원문 복원
  ⑥ 질의 — 등록·승인 뒤 첫 질의가 캐시 히트 · 질의 중 LLM 설명 생성·구조 분석 0 ·
     적용본이 `_structure_meta`로 붙음
  ⑦ 준비도 — 레지스트리 반영 전 미충족 항목 · 반영 뒤 필수 7/7 · 권장 2/2
  ⑧ 쓰기 0 — 저장소 `config/`·`.cache/structure/`·`.env`·`mcp_server/`가 테스트 전후로 같다

이름은 전부 가상(`sample_src`·`entity_t`·`attr_t` …)이다. 목 MCP·목 LLM·인메모리 Redis 페이크·
`tmp_path` 프로필 디렉터리만 쓴다.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from src.routing.registry import parse_registry
from src.schema_cache.db_registration_service import DBRegistrationService
from src.schema_cache.db_structure_service import DBStructureService
from src.schema_cache.fingerprint import FINGERPRINT_SQL
from src.schema_cache.redis_cache import RedisSchemaCache
from src.state import AgentState, create_initial_state
from tests.test_schema_cache.test_plan104_service_fixtures import (
    REMOTE_ENV,
    Env,
    FakeTable,
    MockLLM,
    make_env,
    make_registry,
)

# `src.nodes` 패키지가 노드 함수를 같은 이름으로 재노출하므로 모듈은 importlib로 잡는다.
sa_mod = importlib.import_module("src.nodes.schema_analyzer")

REPO = Path(__file__).resolve().parents[2]

NEW = "sample_src"      # MCP에만 있는 신규 소스 — 이 흐름의 주인공
BARE = "bare_src"       # 활성인데 구조 정보가 없는 소스(목록 경고 대상)
LEGACY = "legacy_src"   # 수동 프로필을 사람이 관리해 온 소스(v0 보관·되돌리기 대상)
EXCLUDED = "metric_t"   # 설명 적용에서 관리자가 빼는 테이블

EAV_META = {
    "patterns": [{
        "type": "eav",
        "entity_table": "entity_t",
        "config_table": "attr_t",
        "attribute_column": "attr_name",
        "value_column": "attr_value",
        "join_condition": "entity_t.id = attr_t.entity_id",
        "known_attributes": ["cpu_core"],
    }],
    "query_guide": "속성은 attr_t를 피벗해 읽는다",
}

# 수동 프로필 원문 — 되돌리기가 주석까지 그대로 살려야 한다
LEGACY_PROFILE = (
    "# 사람이 관리하는 수동 프로필 — 이 주석은 되돌리기로 복원돼야 한다\n"
    "source: manual\n"
    "patterns: []\n"
    "query_guide: ''\n"
    "allowed_tables:\n"
    "- entity_t\n"
)


def _tables() -> dict[str, FakeTable]:
    """가상 EAV 스키마 — 엔티티 1 · 속성 1 · 지표 1(FK로 한 묶음)."""
    return {
        "entity_t": FakeTable([
            ("id", "integer", False, True),
            ("entity_name", "character varying", True, False),
            ("entity_type", "character varying", True, False),
        ]),
        "attr_t": FakeTable(
            [
                ("id", "integer", False, True),
                ("entity_id", "integer", False, False),
                ("attr_name", "character varying", True, False),
                ("attr_value", "character varying", True, False),
            ],
            fks=[("entity_id", "entity_t", "id")],
        ),
        EXCLUDED: FakeTable(
            [
                ("id", "integer", False, True),
                ("entity_id", "integer", False, False),
                ("metric_value", "numeric", True, False),
            ],
            fks=[("entity_id", "entity_t", "id")],
        ),
    }


def _sql_rows(source: str, sql: str) -> list[dict[str, Any]]:
    """목 MCP의 `execute_sql` 응답 — 코드값·샘플·서버 변수."""
    if sql.startswith("SELECT DISTINCT attr_name"):
        return [{"attr_name": "cpu_core"}, {"attr_name": "mem_total"}]
    if sql.startswith("SELECT DISTINCT entity_type"):
        return [{"entity_type": "server"}, {"entity_type": "storage"}]
    if "LIMIT 5" in sql:
        return [{"attr_name": "cpu_core", "attr_value": "8"}]
    return [{"server_version": "16.2", "standard_conforming_strings": "on"}]


def _mutate_schema(env: Env, source: str) -> None:
    """점검용 스키마 변경 — 승인본이 참조하는 컬럼의 타입 변경 + 컬럼 1개 추가(테이블 수 불변)."""
    tables = env.session.tables[source]
    tables["attr_t"].columns = [
        (c, "text" if c == "attr_name" else t, n, pk) for c, t, n, pk in tables["attr_t"].columns
    ]
    tables["entity_t"].columns.append(("zone_code", "character varying", True, False))


# ──────────────────────────────────────────────
# 한 흐름
# ──────────────────────────────────────────────


@dataclass
class Flow:
    """한 흐름의 산출물 — 단계마다 단언할 값과 그 시점의 목 MCP 호출 기록."""

    env: Env
    structure: DBStructureService
    registration: DBRegistrationService
    listed: dict[str, Any]
    registered: dict[str, Any]
    applied: dict[str, Any]
    analyzed: dict[str, Any]
    approved: dict[str, Any]
    checked: dict[str, Any]
    snippet: dict[str, Any]
    schema_calls_after_register: int
    sql_after_register: list[str]


async def _run_flow(env: Env) -> Flow:
    """목록 → 등록 → 설명 적용 → 분석·승인 → 점검 → 조각을 한 번에 돌린다."""
    structure = DBStructureService(env.config, env.mgr, **env.service_kwargs())
    registration = DBRegistrationService(env.config, env.mgr, **env.service_kwargs())

    listed = await structure.list_sources()
    registered = await registration.run_register(
        NEW, steps=["probe", "schema", "descriptions", "db_description"],
        tables=None, by="admin", ctx=env.ctx,
    )
    schema_calls_after_register = len(env.session.tool_calls("get_table_schema"))
    sql_after_register = env.session.executed_sql()
    applied = await registration.apply_description_draft(
        NEW, registered["steps"]["descriptions"]["detail"]["draft_id"],
        exclude_tables=[EXCLUDED], by="admin",
    )
    analyzed = await structure.run_analyze(
        NEW, scope="all", tables=None, code_columns=None, by="admin", ctx=env.ctx,
    )
    approved = await structure.approve_draft(
        NEW, analyzed["draft_id"], by="admin", reason="첫 승인"
    )
    _mutate_schema(env, NEW)
    checked = await structure.run_check(
        NEW, by="admin", code_columns=["entity_t.entity_type"], ctx=env.ctx,
    )
    snippet = await registration.config_snippets(NEW)
    return Flow(
        env=env, structure=structure, registration=registration, listed=listed,
        registered=registered, applied=applied, analyzed=analyzed, approved=approved,
        checked=checked, snippet=snippet,
        schema_calls_after_register=schema_calls_after_register,
        sql_after_register=sql_after_register,
    )


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    """목 MCP 소스 3종 · 목 LLM · 페이크 Redis · tmp 프로필 디렉터리."""
    e = make_env(tmp_path, monkeypatch, active=(BARE,))
    for name in (NEW, BARE, LEGACY):
        e.session.add_source(name, "postgresql", _tables())
    e.session.sql_rows = _sql_rows
    e.registry = make_registry(
        {"db_id": BARE, "engine": "postgresql", "description": "가상 공용 데이터"},
        {"db_id": LEGACY, "engine": "postgresql", "description": "가상 이관 데이터"},
    )
    e.llm = MockLLM(
        structure=lambda prompt: json.dumps(EAV_META, ensure_ascii=False),
        samples=lambda prompt: json.dumps(
            [{"purpose": "속성 샘플", "sql": "SELECT attr_name, attr_value FROM attr_t LIMIT 5"}]
        ),
        db_description="가상 자산의 속성·지표를 담는 DB",
    )
    return e


# ──────────────────────────────────────────────
# ⑧ 쓰기 0 — 모든 테스트에 건다
# ──────────────────────────────────────────────


GUARDED_FILES = (
    Path("config") / "db_registry.yaml",
    Path(".env"),
    Path(".encenv"),
    Path("mcp_server") / "config.toml",
    Path("mcp_server") / ".env",
)


def _tree_digest(root: Path) -> dict[str, str]:
    """디렉터리 아래 모든 파일의 (상대경로 → sha256). 없으면 빈 매핑."""
    if not root.exists():
        return {}
    return {
        str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _repo_snapshot() -> dict[str, Any]:
    """저장소에서 앱이 절대 쓰지 않아야 하는 자리(R11 · G-2)의 지문."""
    return {
        "config": _tree_digest(REPO / "config"),
        "structure_cache": _tree_digest(REPO / ".cache" / "structure"),
        "guarded": {
            str(rel): (
                hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
                if (REPO / rel).is_file() else None
            )
            for rel in GUARDED_FILES
        },
    }


@pytest.fixture(autouse=True)
def repo_untouched():
    """이 모듈의 어떤 테스트도 저장소 `config/`·`.cache/structure/`를 건드리지 않는다.

    병행 세션이 같은 시각에 `config/`를 편집하면 이 단언이 대신 실패한다 — 그때는 실패 메시지의
    파일 경로로 구별한다.
    """
    before = _repo_snapshot()
    yield
    assert _repo_snapshot() == before


# ──────────────────────────────────────────────
# ① 목록(A-2 · U1)
# ──────────────────────────────────────────────


async def test_step1_list_shows_mcp_only_source_and_active_without_structure(env):
    """흐름 시작 시점: MCP에만 있는 소스는 `mcp_only`·미등록, 활성인데 구조 없는 DB는 경고."""
    flow = await _run_flow(env)

    assert flow.listed["mcp_available"] is True and flow.listed["env"] == REMOTE_ENV
    rows = {row["source"]: row for row in flow.listed["sources"]}
    assert rows[NEW]["mismatch"] == "mcp_only"
    assert rows[NEW]["registered"] is False and rows[NEW]["active"] is False
    assert rows[NEW]["engine"] == "postgresql" and rows[NEW]["structure"]["status"] == "none"
    assert rows[BARE]["active"] is True and rows[BARE]["mismatch"] is None
    assert rows[BARE]["structure"]["warning"] == "active_without_structure"
    assert rows[NEW]["readiness"]["ready"] is False

    # 흐름을 마친 뒤 같은 목록이 승인본·마지막 점검을 보인다(U1 종단)
    after = {row["source"]: row for row in (await flow.structure.list_sources())["sources"]}
    assert after[NEW]["structure"]["status"] == "approved"
    assert after[NEW]["structure"]["latest_ver"] == 1 and after[NEW]["structure"]["warning"] is None
    assert after[NEW]["last_check"]["reanalysis_required"] is True
    assert after[BARE]["structure"]["status"] == "none"        # 다른 DB는 그대로다


# ──────────────────────────────────────────────
# ② 등록(O-2 · B-3)
# ──────────────────────────────────────────────


async def test_step2_register_stores_cache_and_snapshot_with_one_collection(env):
    """스키마 캐시·스냅샷이 수집 1회를 공유하고, 지문 SQL은 부르지 않는다."""
    flow = await _run_flow(env)

    step = flow.registered["steps"]["schema"]
    assert step["status"] == "ok" and step["count"] == 3
    assert flow.schema_calls_after_register == 3              # 테이블 수와 같다(중복 수집 없음)
    assert FINGERPRINT_SQL not in flow.sql_after_register
    cached = await env.mgr.get_schema(NEW)
    assert sorted(cached["tables"]) == ["attr_t", "entity_t", EXCLUDED]
    assert {"from": f"{EXCLUDED}.entity_id", "to": "entity_t.id"} in cached["relationships"]
    snapshot = await env.store.load_snapshot(NEW)
    assert snapshot["snapshot"]["table_count"] == 3 and snapshot["env"] == REMOTE_ENV
    registration = await env.store.load_registration(NEW)
    assert registration["probe"]["status"] == "ok"
    assert registration["probe"]["detail"]["in_mcp"] is True
    assert registration["schema"]["env"] == REMOTE_ENV


# ──────────────────────────────────────────────
# ③ 설명 초안·적용(O-4 · B-4)
# ──────────────────────────────────────────────


async def test_step3_description_draft_applies_without_excluded_table(env):
    """목 LLM 초안을 만들고, 관리자가 뺀 테이블은 캐시에 들어가지 않는다."""
    flow = await _run_flow(env)

    draft_step = flow.registered["steps"]["descriptions"]
    assert draft_step["status"] == "draft" and draft_step["count"] == 3
    assert draft_step["detail"]["llm_calls"] == 3 and draft_step["detail"]["failed_tables"] == []
    assert flow.applied["applied_tables"] == ["entity_t", "attr_t"]
    assert flow.applied["excluded_tables"] == [EXCLUDED]
    assert flow.applied["coverage"] == {"scope": 3, "applied": 2, "excluded": 1}

    descriptions = await env.mgr.get_descriptions(NEW)
    assert descriptions["entity_t.entity_name"] == "entity_name 설명"
    assert not any(key.startswith(f"{EXCLUDED}.") for key in descriptions)
    synonyms = await env.mgr.get_synonyms(NEW)
    assert synonyms["attr_t.attr_name"] == ["attr_name_별칭"]
    assert flow.applied["global_synced_columns"] > 0          # 적용 경로가 전역 동기화를 부른다


# ──────────────────────────────────────────────
# ④ 변경 점검(A-3 · A-4)
# ──────────────────────────────────────────────


async def test_step4_check_detects_type_change_impact_and_new_code_values(env):
    """타입 변경·컬럼 추가를 잡고, 승인본이 참조하는 컬럼이면 재분석 필요·신규 코드값을 보인다."""
    flow = await _run_flow(env)
    checked = flow.checked

    assert checked["diff"]["baseline"] is False
    assert checked["diff"]["type_changed"] == [
        {"table": "attr_t", "column": "attr_name", "old": "character varying", "new": "text"}
    ]
    assert [c["column"] for c in checked["diff"]["columns_added"]] == ["zone_code"]
    assert checked["diff"]["tables_added"] == [] and checked["diff"]["tables_removed"] == []

    assert checked["reanalysis_required"] is True
    hits = {(h["table"], h["column"], h["path"]) for h in checked["impact"]["hits"]}
    assert ("attr_t", "attr_name", "patterns[0].attribute_column") in hits

    values = {item["key"]: item for item in checked["code_values"]}
    assert values["attr_t.attr_name"]["origin"] == "eav_attribute"
    assert values["attr_t.attr_name"]["known"] == ["cpu_core"]
    assert values["attr_t.attr_name"]["new_values"] == ["mem_total"]   # 승인본이 모르는 값
    assert values["entity_t.entity_type"]["origin"] == "admin"
    assert values["entity_t.entity_type"]["baseline"] is True          # 첫 수집은 세지 않는다
    assert checked["new_code_value_count"] == 1
    assert (await env.store.load_check_result(NEW))["snapshot_hash"] == checked["snapshot_hash"]


# ──────────────────────────────────────────────
# ⑤ 분석·승인(A-5 · A-6)
# ──────────────────────────────────────────────


async def test_step5_analyze_and_approve_write_manual_profile_to_tmp(env):
    """검증 4종을 통과한 초안이 tmp 프로필에 `source: manual`로 기록되고 v1이 남는다."""
    flow = await _run_flow(env)

    assert flow.analyzed["analysis_status"] == "ok" and flow.analyzed["errors"] == []
    assert flow.analyzed["llm_calls"] == {"structure": 1, "samples": 1}
    draft = await env.store.get_draft(NEW, flow.analyzed["draft_id"])
    validation = draft["validation"]
    assert [c["code"] for c in validation["checks"]] == [
        "refs_exist", "join_types", "sample_sql_safe", "sample_exec",
    ]
    assert all(c["passed"] for c in validation["checks"]) and validation["passed"] is True
    assert draft["status"] == "approved"

    path = env.profiles_dir / f"{NEW}.yaml"
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert profile["source"] == "manual" and "environment" not in profile
    assert profile["query_guide"] == EAV_META["query_guide"]
    assert profile["patterns"][0]["config_table"] == "attr_t"
    assert flow.approved["version"]["ver"] == 1
    assert [v["kind"] for v in await env.store.list_versions(NEW)] == ["approved"]
    assert await env.mgr.has_structure_authority(NEW) is True
    assert not (REPO / "config" / "db_profiles" / f"{NEW}.yaml").exists()


async def test_step5_rollback_restores_original_manual_profile_bytes(env):
    """수동 프로필 DB는 승인 직전 원문이 v0로 보관되고, 되돌리기가 주석까지 복원한다."""
    path = env.profiles_dir / f"{LEGACY}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(LEGACY_PROFILE.encode("utf-8"))
    service = DBStructureService(env.config, env.mgr, **env.service_kwargs())

    analyzed = await service.run_analyze(
        LEGACY, scope="all", tables=None, code_columns=None, by="admin", ctx=env.ctx
    )
    approved = await service.approve_draft(LEGACY, analyzed["draft_id"], by="admin", reason="승인")

    versions = await env.store.list_versions(LEGACY)
    assert [(v["ver"], v["kind"]) for v in versions] == [(0, "baseline"), (1, "approved")]
    assert versions[0]["content"] == LEGACY_PROFILE
    assert approved["version"]["ver"] == 1
    merged = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert merged["allowed_tables"] == ["entity_t"]          # 수동 전용 필드 보존
    assert merged["patterns"][0]["entity_table"] == "entity_t"

    rolled = await service.rollback(LEGACY, 0, by="admin", reason="원복")

    assert path.read_bytes() == LEGACY_PROFILE.encode("utf-8")
    assert rolled["version"]["kind"] == "rollback" and rolled["version"]["ver"] == 2


# ──────────────────────────────────────────────
# ⑥ 질의(성공 기준 8)
# ──────────────────────────────────────────────


def _query_client() -> AsyncMock:
    """질의 경로 DB 클라이언트 — 캐시 히트면 수집·지문 조회를 부르지 않아야 한다."""
    client = AsyncMock()
    client.get_sample_data = AsyncMock(return_value=[])
    return client


class _SelectLLM:
    """관련 테이블 선택만 답하는 목 LLM — 프롬프트를 기록해 호출 수를 센다."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ainvoke(self, messages: list[Any], *args: Any, **kwargs: Any) -> MagicMock:
        self.prompts.append("\n".join(str(getattr(m, "content", m)) for m in messages))
        return MagicMock(content="entity_t, attr_t")


@asynccontextmanager
async def _query_io(cache_mgr: Any, client: AsyncMock):
    """질의 경로 주변 I/O 대역 — DB 클라이언트·캐시 매니저·설명 생성기·LLM 획득."""

    @asynccontextmanager
    async def _db_ctx(*_a: Any, **_kw: Any):
        yield client

    with patch("src.nodes.schema_analyzer.get_db_client", side_effect=_db_ctx), \
         patch("src.nodes.schema_analyzer.get_cache_manager", return_value=cache_mgr), \
         patch("src.schema_cache.description_generator.DescriptionGenerator") as gen_cls, \
         patch("src.llm.create_llm") as create_llm, \
         patch.object(RedisSchemaCache, "_governance_enabled", return_value=False):
        yield gen_cls, create_llm


def _query_state() -> AgentState:
    state = create_initial_state(user_query="자산 속성 목록")
    state["active_db_id"] = NEW
    state["parsed_requirements"] = {
        "query_targets": ["자산 속성"], "original_query": "자산 속성 목록", "output_format": "text",
    }
    return state


async def test_step6_first_query_is_cache_hit_without_admin_llm_calls(env, mock_config):
    """등록·승인 뒤 첫 질의는 캐시 히트고, 질의 중 LLM 설명 생성·구조 분석은 0이다."""
    await _run_flow(env)
    client, llm = _query_client(), _SelectLLM()
    admin_llm_calls = len(env.llm.calls)

    async with _query_io(env.mgr, client) as (gen_cls, create_llm):
        out = await sa_mod.schema_analyzer(_query_state(), llm=llm, app_config=mock_config)

    assert out["error_message"] is None
    assert out["schema_cache_source"] == "Redis"              # 지연 수집 없이 캐시에서 읽었다
    client.get_full_schema.assert_not_awaited()
    client.execute_sql.assert_not_awaited()                   # 지문 SQL도 없다
    assert len(llm.prompts) == 1                              # 관련 테이블 선택 1회뿐
    assert "## DB 스키마" not in llm.prompts[0]                # 구조 분석 프롬프트가 아니다
    gen_cls.assert_not_called()
    create_llm.assert_not_called()
    assert len(env.llm.calls) == admin_llm_calls              # 관리자 목 LLM도 다시 불리지 않는다

    meta = out["schema_info"]["_structure_meta"]
    assert meta["query_guide"] == EAV_META["query_guide"]
    assert meta["patterns"][0]["config_table"] == "attr_t"
    assert out["column_descriptions"]["entity_t.id"] == "id 설명"
    assert "dependency_notes" not in out                      # 구조·설명이 다 있으면 사유가 없다


async def test_step6_applied_version_supplies_structure_without_manual_profile(env, mock_config):
    """수동 프로필을 못 읽어도 승인 적용본이 같은 구조 정보를 질의에 붙인다(②경로)."""
    await _run_flow(env)
    client = _query_client()

    async with _query_io(env.mgr, client):
        with patch("src.nodes.schema_analyzer._load_manual_profile", return_value=None):
            out = await sa_mod.schema_analyzer(
                _query_state(), llm=_SelectLLM(), app_config=mock_config
            )

    assert out["schema_info"]["_structure_meta"]["query_guide"] == EAV_META["query_guide"]
    assert "dependency_notes" not in out


# ──────────────────────────────────────────────
# ⑦ 준비도(B-1)
# ──────────────────────────────────────────────


async def test_step7_readiness_unmet_until_registry_entry_is_reflected(env):
    """흐름을 마쳐도 레지스트리 반영 전에는 C2·C3가 비고, 조각을 반영하면 필수 7/7이 된다."""
    flow = await _run_flow(env)

    before = await flow.registration.readiness(NEW)
    assert before.ready is False
    assert {i.code for i in before.unmet_required()} == {"C2", "C3"}

    # 사람이 조각을 `config/db_registry.yaml`에 반영하고 앱을 재기동한 상태를 흉내 낸다(R11)
    reflected = parse_registry(yaml.safe_load(flow.snippet["registry_yaml"]))
    entry = reflected.get(NEW)
    assert entry is not None and entry.description == "가상 자산의 속성·지표를 담는 DB"
    assert "# 사람 검토 필요" in flow.snippet["registry_yaml"]
    env.registry = reflected

    after = await flow.registration.readiness(NEW)
    assert after.ready is True, [(i.code, i.detail) for i in after.unmet_required()]
    assert (after.required_met, after.required_total) == (7, 7)
    assert (after.recommended_met, after.recommended_total) == (2, 2)
    items = {i.code: i for i in after.items}
    assert items["C6"].detail.startswith("승인본")
    assert items["C8"].ok is True and items["C9"].ok is True


# ──────────────────────────────────────────────
# ⑧ 쓰기 0 (명시 단언 — autouse 가드와 별개로 흐름이 실제로 쓴 자리를 함께 보인다)
# ──────────────────────────────────────────────


async def test_step8_flow_writes_only_under_tmp(env):
    """전 과정이 저장소에 쓰지 않는다 — 쓰기는 tmp 프로필·tmp 버전 백업에만 남는다."""
    before = _repo_snapshot()
    assert before["config"], "저장소 config/ 지문이 비었다 — 쓰기 0 단언이 무의미해진다"
    profiles_before = sorted(p.name for p in (REPO / "config" / "db_profiles").glob("*"))

    flow = await _run_flow(env)

    assert _repo_snapshot() == before
    assert sorted(p.name for p in (REPO / "config" / "db_profiles").glob("*")) == profiles_before
    for db_id in (NEW, BARE, LEGACY):
        assert not (REPO / "config" / "db_profiles" / f"{db_id}.yaml").exists()
        assert not (REPO / ".cache" / "structure" / db_id).exists()

    # 그런데도 흐름은 tmp 아래에 실제로 썼다 — "아무 일도 안 일어난" 통과가 아니다
    assert (env.profiles_dir / f"{NEW}.yaml").is_file()
    assert (env.store.backup_root / NEW).is_dir()
    assert flow.approved["version"]["ver"] == 1
    assert Path(flow.applied["backup_path"]).is_relative_to(env.tmp)
