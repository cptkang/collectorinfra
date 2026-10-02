"""plans/104 B-6 — 질의 경로는 컬럼 설명을 읽기만 한다 (G-9 (a) · §3.8.5 · D-227).

고정하는 계약:
  ① 캐시 미스 질의의 LLM 호출 0 — 관련 테이블 선택 외 LLM 호출 0 · `DescriptionGenerator` 미생성
  ② 설명이 비었으면 사유 노출 — 단일(schema_analyzer)·멀티(multi_db_executor) 대칭 · DB당 1건 ·
     설명이 비어 있는 동안 질의마다(캐시 히트 포함) 싣고 설명이 있으면 키를 싣지 않는다 · 응답 본문 `[안내]` 1회
  ③ Redis를 비운 뒤 설명 백업에서 복원 — LLM 0 · 출처 태그 보존 · 스키마에 없는 컬럼 제외 ·
     백업 뒤 등록된 유사어 항목 불변
  ④ 설정 필드 부재 — 설정·카탈로그·도움말·`.env.example`

LLM·DB 0 · Redis는 인메모리 페이크 또는 매니저 대역.
"""

from __future__ import annotations

import importlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import SchemaCacheConfig
from src.dbhub.models import ColumnInfo, SchemaInfo, TableInfo
from src.nodes.output_generator import append_structure_missing_note
from src.schema_cache.cache_manager import SchemaCacheManager
from src.schema_cache.redis_cache import RedisSchemaCache
from src.state import create_initial_state
from src.utils.prior_dependency import (
    NOTE_DESCRIPTIONS_MISSING,
    NOTE_STRUCTURE_MISSING,
    descriptions_missing_note,
    structure_missing_note,
)
from tests.mocks.async_redis import attach_fake_redis

# `src.nodes` 패키지가 노드 함수를 같은 이름으로 재노출하므로 모듈은 importlib로 잡는다.
sa_mod = importlib.import_module("src.nodes.schema_analyzer")
mdb = importlib.import_module("src.nodes.multi_db_executor")
_ROOT = Path(__file__).resolve().parents[2]
DB_ID = "new_db"
APPLIED_META = {"patterns": [], "query_guide": "승인본 가이드"}
PARSED = {"query_targets": [], "original_query": "항목 목록", "output_format": "text"}
DESCRIPTIONS = {"items.id": "항목 식별자"}
_SCHEMA_DICT = {
    "tables": {
        "items": {"columns": [{"name": "id", "type": "int"}]},
        "owners": {"columns": [{"name": "name", "type": "varchar"}]},
    },
    "relationships": [],
}


class _CacheMgr:
    """노드가 쓰는 캐시 매니저 표면 대역 — 캐시 히트 여부·설명·적용본을 고른다(Redis 미연결)."""

    redis_available = False

    def __init__(self, *, cache_hit: bool, descriptions: dict, applied: dict | None = None) -> None:
        self.cache_hit = cache_hit
        self.descriptions = descriptions
        self.applied = applied

    async def get_schema_or_fetch(self, client, db_id):
        source = "메모리" if self.cache_hit else "DB 직접 조회"
        schema = json.loads(json.dumps(_SCHEMA_DICT))
        return schema, self.cache_hit, source, dict(self.descriptions), {}

    async def get_applied_structure_meta(self, db_id):
        return self.applied

    async def get_synonyms(self, db_id):
        return {}


class _RecordingLLM:
    """호출 프롬프트를 기록하는 목 LLM — 테이블 선택 응답만 돌려준다."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ainvoke(self, messages, *args, **kwargs):
        self.prompts.append("\n".join(str(getattr(m, "content", m)) for m in messages))
        return MagicMock(content="items, owners")


def _schema_client() -> AsyncMock:
    """캐시 미스 수집에 필요한 표면만 가진 DB 클라이언트(지문 조회 0행)."""
    client = AsyncMock()
    client.execute_sql = AsyncMock(return_value=SimpleNamespace(rows=[]))
    client.get_sample_data = AsyncMock(return_value=[])
    client.get_full_schema = AsyncMock(return_value=SchemaInfo(
        tables={
            "items": TableInfo(name="items", columns=[ColumnInfo(name="id", data_type="int")]),
            "owners": TableInfo(
                name="owners", columns=[ColumnInfo(name="name", data_type="varchar")]
            ),
        },
        relationships=[],
    ))
    return client


def _fake_manager(tmp_path: Path) -> tuple[SchemaCacheManager, object]:
    """인메모리 Redis 페이크를 붙인 실제 매니저(파일 캐시·설명 백업은 tmp 아래)."""
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = True
    mgr = SchemaCacheManager(config)
    return mgr, attach_fake_redis(mgr._redis_cache)


@asynccontextmanager
async def _node_io(cache_mgr, client: AsyncMock | None = None):
    client = client or _schema_client()

    @asynccontextmanager
    async def _db_ctx(*_a, **_kw):
        yield client

    with patch("src.nodes.schema_analyzer.get_db_client", side_effect=_db_ctx), \
         patch("src.nodes.schema_analyzer.get_cache_manager", return_value=cache_mgr), \
         patch("src.nodes.schema_analyzer._load_manual_profile", return_value=None):
        yield client


@asynccontextmanager
async def _no_description_llm():
    """설명 생성기·LLM 획득 스파이 — 호출되면 단언이 잡는다."""
    with patch("src.schema_cache.description_generator.DescriptionGenerator") as gen_cls, \
         patch("src.llm.create_llm") as create_llm, \
         patch.object(RedisSchemaCache, "_governance_enabled", return_value=False):
        yield gen_cls, create_llm


def _state(query_targets: list[str] | None = None, **extra) -> dict:
    state = create_initial_state(user_query="항목 목록")
    state["active_db_id"] = DB_ID
    state["parsed_requirements"] = {
        "query_targets": query_targets or [],
        "original_query": "항목 목록",
        "output_format": "text",
    }
    state.update(extra)
    return state


# ──────────────────────────────────────────────
# ① 캐시 미스 질의의 LLM 호출 0
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_miss_query_makes_no_description_llm_calls(mock_config, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 저장소의 전역 유사어 파일·프로필을 읽지 않게
    mgr, fake = _fake_manager(tmp_path)
    llm = _RecordingLLM()
    async with _no_description_llm() as (gen_cls, create_llm), _node_io(mgr):
        out = await sa_mod.schema_analyzer(
            _state(query_targets=["항목"]), llm=llm, app_config=mock_config
        )

    assert out["error_message"] is None
    assert out["schema_cache_source"] == "DB 직접 조회"          # 캐시 미스 경로를 탔다
    assert len(llm.prompts) == 1                                   # 관련 테이블 선택 1회뿐
    gen_cls.assert_not_called()                                    # DescriptionGenerator 미생성
    create_llm.assert_not_called()
    assert await fake.hlen(f"schema:{DB_ID}:descriptions") == 0
    assert [n["kind"] for n in out["dependency_notes"]] == [
        NOTE_STRUCTURE_MISSING, NOTE_DESCRIPTIONS_MISSING,
    ]


# ──────────────────────────────────────────────
# ② 사유 노출 — 단일 DB
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_db_notes_missing_descriptions_once(mock_config):
    prior = {"kind": "decompose", "task_id": None, "detail": "기존 노트"}
    existing = descriptions_missing_note(DB_ID)
    cache_mgr = _CacheMgr(cache_hit=False, descriptions={}, applied=APPLIED_META)
    async with _node_io(cache_mgr):
        out = await sa_mod.schema_analyzer(
            _state(dependency_notes=[prior]), llm=AsyncMock(), app_config=mock_config
        )
        again = await sa_mod.schema_analyzer(
            _state(dependency_notes=[prior, existing]), llm=AsyncMock(), app_config=mock_config
        )

    assert out["dependency_notes"] == [prior, descriptions_missing_note(DB_ID)]
    assert again["dependency_notes"] == [prior, existing]           # 같은 DB 노트는 1건
    detail = out["dependency_notes"][1]["detail"]
    assert DB_ID in detail and "컬럼 설명 미등록" in detail and "관리자" in detail


@pytest.mark.parametrize(
    ("cache_hit", "descriptions"),
    [(False, DESCRIPTIONS), (True, DESCRIPTIONS)],
)
@pytest.mark.asyncio
async def test_single_db_no_note_with_descriptions(
    mock_config, cache_hit, descriptions
):
    cache_mgr = _CacheMgr(cache_hit=cache_hit, descriptions=descriptions, applied=APPLIED_META)
    async with _node_io(cache_mgr):
        out = await sa_mod.schema_analyzer(_state(), llm=AsyncMock(), app_config=mock_config)
    assert "dependency_notes" not in out                             # 반환 shape 현행 유지
    assert out["column_descriptions"] == descriptions


def test_response_body_renders_both_kinds_once():
    s_note, d_note = structure_missing_note(DB_ID), descriptions_missing_note(DB_ID)
    other = {"kind": "scope_db", "detail": "x"}
    state = {"dependency_notes": [s_note, d_note, dict(d_note), other]}
    assert append_structure_missing_note("본문", state) == (
        f"본문\n\n[안내] {s_note['detail']}\n[안내] {d_note['detail']}"
    )


@pytest.mark.asyncio
async def test_tier2_subagent_pipeline_carries_same_note(mock_config):
    """2단 서브에이전트도 같은 `schema_analyzer`를 불러 같은 노트를 task 결과로 올린다."""
    from src.orchestration import subagents

    task = {"task_id": "t1", "agent": "data_query", "sub_query": "항목 목록", "db_ids": [DB_ID],
            "depends_on": [], "input_from": [], "order": 1, "status": "pending"}
    isolated = {"user_query": "항목 목록", "parsed_requirements": PARSED}
    ok = {"validation_result": {"passed": True, "reason": "", "auto_fixed_sql": None}}
    executed = {"query_results": [{"id": 1}], "error_message": None}
    with patch.object(subagents, "query_generator",
                      AsyncMock(return_value={"generated_sql": "SELECT 1", "retry_count": 0})), \
         patch.object(subagents, "query_validator", AsyncMock(return_value=ok)), \
         patch.object(subagents, "query_executor", AsyncMock(return_value=executed)), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value={
             "organized_data": {"is_sufficient": True, "rows": [{"id": 1}], "summary": "1건"},
         })):
        async with _node_io(_CacheMgr(cache_hit=False, descriptions={}, applied=APPLIED_META)):
            res = await subagents.run_data_query_pipeline(
                task, isolated, llm=AsyncMock(), app_config=mock_config
            )
    assert res["dependency_notes"] == [descriptions_missing_note(DB_ID)]


# ──────────────────────────────────────────────
# ② 사유 노출 — 멀티 DB(대칭)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_db_analyze_schema_notes_once_per_db(mock_config, monkeypatch):
    monkeypatch.setattr(sa_mod, "_load_manual_profile", lambda db_id: None)
    notes: list[dict] = []
    for cache_hit, descriptions, expected in (
        (False, {}, [descriptions_missing_note(DB_ID)]),
        (False, {}, [descriptions_missing_note(DB_ID)]),       # 같은 DB 두 번째 — 1건 유지
        (True, {}, [descriptions_missing_note(DB_ID)]),        # 캐시 히트여도 비었으면 유지(1건)
    ):
        cache_mgr = _CacheMgr(cache_hit=cache_hit, descriptions=descriptions, applied=APPLIED_META)
        monkeypatch.setattr(
            "src.schema_cache.cache_manager.get_cache_manager", lambda cfg, _m=cache_mgr: _m
        )
        await mdb._analyze_schema(
            _schema_client(), PARSED, db_id=DB_ID, app_config=mock_config,
            dependency_notes=notes,
        )
        assert notes == expected

    other: list[dict] = []
    cache_mgr = _CacheMgr(cache_hit=False, descriptions=DESCRIPTIONS, applied=APPLIED_META)
    monkeypatch.setattr("src.schema_cache.cache_manager.get_cache_manager", lambda cfg: cache_mgr)
    await mdb._analyze_schema(
        _schema_client(), PARSED, db_id=DB_ID, app_config=mock_config, dependency_notes=other
    )
    assert other == []                                           # 설명이 있으면 싣지 않는다
    # 채널 없이 부르는 종전 호출부도 그대로 동작한다
    await mdb._analyze_schema(_schema_client(), PARSED, db_id=DB_ID, app_config=mock_config)


@pytest.mark.asyncio
async def test_multi_db_node_result_carries_notes_per_db(monkeypatch):
    """멀티 DB 노드 본체 — 대상 DB마다 구조·설명 노트가 1건씩 결과 `dependency_notes`에 실린다."""
    monkeypatch.setattr(sa_mod, "_load_manual_profile", lambda db_id: None)
    cache_mgr = _CacheMgr(cache_hit=False, descriptions={}, applied=None)
    monkeypatch.setattr("src.schema_cache.cache_manager.get_cache_manager", lambda cfg: cache_mgr)

    class _Registry:
        def __init__(self, cfg):
            pass

        def is_registered(self, db_id):
            return True

        @asynccontextmanager
        async def get_client(self, db_id):
            client = _schema_client()
            rows = SimpleNamespace(rows=[{"id": 1}], row_count=1)
            client.execute_sql = AsyncMock(return_value=rows)
            yield client

    async def _generate(*_a, **_kw):
        return "SELECT id FROM items"

    monkeypatch.setattr(mdb, "DBRegistry", _Registry)
    monkeypatch.setattr(mdb, "_generate_sql", _generate)
    monkeypatch.setattr(mdb, "_validate_sql_simple", lambda sql, schema_info, **_kw: None)
    monkeypatch.setattr(mdb, "get_domain_by_id", lambda db_id: None)
    monkeypatch.setattr(mdb, "log_query_execution", AsyncMock())

    state = create_initial_state(user_query="항목 목록")
    state["target_databases"] = [{"db_id": "db_a"}, {"db_id": "db_b"}]
    config = SimpleNamespace(
        query=SimpleNamespace(default_limit=100),
        text2sql=SimpleNamespace(multi_full_validation=False),
    )
    out = await mdb.multi_db_executor(state, llm=AsyncMock(), app_config=config)

    expected = {
        (kind, db_id)
        for kind in (NOTE_STRUCTURE_MISSING, NOTE_DESCRIPTIONS_MISSING)
        for db_id in ("db_a", "db_b")
    }
    got = [(n["kind"], n["db_id"]) for n in out["dependency_notes"]]
    assert sorted(got) == sorted(expected)                        # DB당 종류별 1건
    body = append_structure_missing_note("본문", out)
    for db_id in ("db_a", "db_b"):
        assert body.count(descriptions_missing_note(db_id)["detail"]) == 1


# ──────────────────────────────────────────────
# ③ Redis를 비운 뒤 설명 백업에서 복원
# ──────────────────────────────────────────────

_BACKUP_SYNONYMS = {
    "items.id": {"words": ["항목번호", "운영자별칭"],
                 "sources": {"항목번호": "llm", "운영자별칭": "operator"}},
    "owners.name": {"words": ["백업별칭"], "sources": {"백업별칭": "llm"}},
    "gone.col": {"words": ["사라진"], "sources": {"사라진": "llm"}},
}


@pytest.mark.asyncio
async def test_descriptions_restored_from_backup_after_redis_flush(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mgr, fake = _fake_manager(tmp_path)
    mgr.structure_store.backup_descriptions(
        DB_ID, {"items.id": "항목 식별자", "gone.col": "스키마에 없는 컬럼"}, _BACKUP_SYNONYMS
    )
    await fake.flushdb()                                             # Redis 유실
    async with _no_description_llm() as (gen_cls, create_llm):
        # 유실 뒤 운영자가 먼저 등록한 항목 — 백업이 덮지 않아야 한다
        await mgr.add_synonyms(DB_ID, "owners.name", ["새별칭"], source="operator")
        _, cache_hit, _, descriptions, synonyms = await mgr.get_schema_or_fetch(
            _schema_client(), DB_ID
        )

    assert cache_hit is False
    gen_cls.assert_not_called()
    create_llm.assert_not_called()
    assert descriptions == {"items.id": "항목 식별자"}                # 스키마에 없는 컬럼 제외
    assert await fake.hgetall(f"schema:{DB_ID}:descriptions") == {"items.id": "항목 식별자"}

    items_entry = json.loads(await fake.hget(f"schema:{DB_ID}:synonyms", "items.id"))
    # 출처 태그 보존
    assert items_entry["sources"] == {"항목번호": "llm", "운영자별칭": "operator"}
    owners_entry = json.loads(await fake.hget(f"schema:{DB_ID}:synonyms", "owners.name"))
    assert owners_entry["words"] == ["새별칭"]                        # 기존 항목 불변
    assert await fake.hget(f"schema:{DB_ID}:synonyms", "gone.col") is None
    assert set(synonyms["items.id"]) == {"항목번호", "운영자별칭"}
    assert await fake.exists("synonyms:global") == 0                  # 전역 사전 쓰기 0
    file_synonyms = mgr._file_cache.load_synonyms(DB_ID)              # 파일 캐시는 단어 목록 형식
    assert all(isinstance(words, list) for words in file_synonyms.values())


@pytest.mark.asyncio
async def test_restored_descriptions_suppress_note(mock_config, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mgr, fake = _fake_manager(tmp_path)
    mgr.structure_store.backup_descriptions(DB_ID, dict(DESCRIPTIONS), {})
    await fake.flushdb()
    async with _no_description_llm(), _node_io(mgr):
        out = await sa_mod.schema_analyzer(_state(), llm=AsyncMock(), app_config=mock_config)
    assert out["column_descriptions"] == DESCRIPTIONS
    assert [n["kind"] for n in out["dependency_notes"]] == [NOTE_STRUCTURE_MISSING]


# ──────────────────────────────────────────────
# ④ 설정 필드 부재
# ──────────────────────────────────────────────

def test_lazy_generation_setting_removed_everywhere():
    from src.api.settings_catalog import IMMEDIATE_KEYS, field_index

    # 이 파일 자신이 참조 grep에 걸리지 않게 조각으로 조립한다.
    field = "auto_generate_" + "descriptions"
    env_key = "SCHEMA_CACHE_" + field.upper()
    assert field not in SchemaCacheConfig.model_fields
    assert env_key not in field_index()
    assert env_key not in IMMEDIATE_KEYS
    help_files = sorted((_ROOT / "config" / "settings_help").glob("*.yaml"))
    for path in [_ROOT / ".env.example", *help_files]:
        assert env_key not in path.read_text(encoding="utf-8"), path


@pytest.mark.asyncio
async def test_single_db_notes_missing_descriptions_on_cache_hit_too(mock_config):
    """캐시가 데워진 뒤에도 설명이 비어 있으면 질의마다 사유를 남긴다(침묵 강등 금지)."""
    cache_mgr = _CacheMgr(cache_hit=True, descriptions={}, applied=APPLIED_META)
    async with _node_io(cache_mgr):
        out = await sa_mod.schema_analyzer(_state(), llm=AsyncMock(), app_config=mock_config)
    assert out["dependency_notes"] == [descriptions_missing_note(DB_ID)]
