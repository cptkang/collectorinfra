"""plans/104 A-8 — 질의 경로는 구조를 읽기만 한다 (D-227 · R1·R3 · G-1 (a)).

고정하는 계약:
  ① 구조 정보 우선순위 — 수동 프로필 > 관리자 승인 적용본 > 없음
  ② 구조 정보가 없어도 멈추지 않고(HITL 0) `dependency_notes`에 사유를 남긴다
  ③ 질의 중 LLM 구조 분석·샘플 SQL 생성 호출 0
  ④ 사유가 최종 응답 본문에 보인다 — 3단 그래프 경로(단일 DB)는 본문 1회, 2단(서브에이전트 →
     결과 집계) 경로도 **같은 문구가 1회만** 나온다
  ⑤ 멀티 DB 경로도 같은 우선순위·같은 노트(단일/멀티 대칭)
  ⑥ 삭제된 구조 승인 게이트·설정의 참조 0
"""

from __future__ import annotations

import importlib
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.graph as graph_module
from src.nodes.output_generator import append_structure_missing_note
from src.nodes.schema_analyzer import schema_analyzer
from src.prompts.structure_analyzer import (
    SAMPLE_SQL_GENERATION_PROMPT,
    STRUCTURE_ANALYSIS_PROMPT,
)
from src.state import create_initial_state
from src.utils.prior_dependency import (
    ADMIN_ASSET_NOTE_KINDS,
    NOTE_STRUCTURE_MISSING,
    structure_missing_note,
)

# `src.nodes` 패키지가 노드 함수를 같은 이름으로 재노출하므로 모듈은 importlib로 잡는다.
mdb = importlib.import_module("src.nodes.multi_db_executor")
sa_mod = importlib.import_module("src.nodes.schema_analyzer")
_ROOT = Path(__file__).resolve().parents[2]
DB_ID = "new_db"
APPLIED_META = {"patterns": [], "query_guide": "승인본 가이드"}
MANUAL_PROFILE = {"source": "manual", "patterns": [], "query_guide": "수동 프로필 가이드"}
PARSED = {"query_targets": [], "original_query": "항목 목록", "output_format": "text"}
PASSED = {"validation_result": {"passed": True, "reason": "", "auto_fixed_sql": None}}
GENERATED = {"generated_sql": "SELECT 1", "retry_count": 0}
EXECUTED = {"query_results": [{"id": 1}], "error_message": None}
ORGANIZED = {"organized_data": {"is_sufficient": True, "rows": [{"id": 1}], "summary": "1건"}}
BODY = "조회 결과 1건입니다."
#: 삭제된 게이트·설정 이름 — 이 파일 자신이 참조 grep에 걸리지 않게 조각으로 조립한다.
_DELETED_GATE = "structure_" + "approval_gate"
_DELETED_FIELD = "enable_" + "structure_approval"


class _CacheMgr:
    """schema_analyzer가 쓰는 캐시 매니저 표면만 흉내 낸다(Redis 미연결)."""

    redis_available = False

    def __init__(self, applied: dict | None = None) -> None:
        self.applied = applied
        self.applied_calls: list[str] = []

    async def get_schema_or_fetch(self, client, db_id):
        schema = {
            "tables": {
                "items": {"columns": [{"name": "id", "type": "int"}]},
                "owners": {"columns": [{"name": "name", "type": "varchar"}]},
            },
            "relationships": [],
        }
        # 컬럼 설명은 등록된 상태로 둔다 — 이 파일은 구조 정보 노트만 본다(설명 노트는
        # test_plan104_lazy_descriptions.py 소관)
        return schema, True, "메모리", {"items.id": "항목 식별자"}, {}

    async def get_applied_structure_meta(self, db_id):
        self.applied_calls.append(db_id)
        return self.applied

    async def save_structure_meta(self, db_id, meta):
        return True

    async def sync_known_attributes_to_eav_synonyms(self, detail):
        return 0

    async def get_synonyms(self, db_id):
        return {}


class _RecordingLLM:
    """호출 프롬프트를 기록하는 목 LLM — 테이블 선택 응답만 돌려준다."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ainvoke(self, messages, *args, **kwargs):
        self.prompts.append("\n".join(str(getattr(m, "content", m)) for m in messages))
        return MagicMock(content="items, owners")


def _client() -> AsyncMock:
    client = AsyncMock()
    client.get_sample_data = AsyncMock(return_value=[])
    return client


@asynccontextmanager
async def _patched_io(cache_mgr: _CacheMgr, manual: dict | None, client: AsyncMock | None = None):
    client = client or _client()

    @asynccontextmanager
    async def _db_ctx(*_a, **_kw):
        yield client

    with patch("src.nodes.schema_analyzer.get_db_client", side_effect=_db_ctx), \
         patch("src.nodes.schema_analyzer.get_cache_manager", return_value=cache_mgr), \
         patch("src.nodes.schema_analyzer._load_manual_profile", return_value=manual):
        yield client


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
# ① 우선순위 · ② 멈추지 않고 사유
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manual_profile_wins_over_applied(mock_config):
    cache_mgr = _CacheMgr(applied=APPLIED_META)
    async with _patched_io(cache_mgr, dict(MANUAL_PROFILE)):
        out = await schema_analyzer(_state(), llm=AsyncMock(), app_config=mock_config)
    assert out["schema_info"]["_structure_meta"]["query_guide"] == "수동 프로필 가이드"
    assert "source" not in out["schema_info"]["_structure_meta"]
    assert cache_mgr.applied_calls == []          # 수동 프로필이 있으면 적용본을 보지 않는다
    assert "dependency_notes" not in out


@pytest.mark.asyncio
async def test_applied_meta_used_without_manual_profile(mock_config):
    cache_mgr = _CacheMgr(applied=APPLIED_META)
    async with _patched_io(cache_mgr, None):
        out = await schema_analyzer(_state(), llm=AsyncMock(), app_config=mock_config)
    assert out["schema_info"]["_structure_meta"] == APPLIED_META
    assert cache_mgr.applied_calls == [DB_ID]
    assert "dependency_notes" not in out           # 반환 shape 현행 유지


@pytest.mark.asyncio
async def test_missing_structure_continues_and_notes_reason(mock_config):
    prior = {"kind": "decompose", "task_id": None, "detail": "기존 노트"}
    async with _patched_io(_CacheMgr(applied=None), None):
        out = await schema_analyzer(
            _state(dependency_notes=[prior]), llm=AsyncMock(), app_config=mock_config
        )
    assert out["error_message"] is None
    assert "awaiting_approval" not in out and "approval_context" not in out   # HITL 0
    assert "_structure_meta" not in out["schema_info"]
    assert out["relevant_tables"] == ["items", "owners"]
    notes = out["dependency_notes"]
    assert notes[0] == prior                                              # 기존 노트 보존
    assert notes[1] == structure_missing_note(DB_ID)
    assert notes[1]["kind"] == NOTE_STRUCTURE_MISSING
    assert DB_ID in notes[1]["detail"] and "관리자 「DB 구조」 탭" in notes[1]["detail"]


@pytest.mark.asyncio
async def test_missing_note_not_duplicated_for_same_db(mock_config):
    existing = structure_missing_note(DB_ID)
    async with _patched_io(_CacheMgr(applied=None), None):
        out = await schema_analyzer(
            _state(dependency_notes=[existing]), llm=AsyncMock(), app_config=mock_config
        )
    assert out["dependency_notes"] == [existing]


# ──────────────────────────────────────────────
# ③ 질의 중 LLM 구조 분석·샘플 SQL 생성 0
# ──────────────────────────────────────────────

@pytest.mark.parametrize("applied", [None, APPLIED_META])
@pytest.mark.asyncio
async def test_no_structure_analysis_llm_calls(mock_config, applied):
    llm = _RecordingLLM()
    client = _client()
    async with _patched_io(_CacheMgr(applied=applied), None, client):
        await schema_analyzer(_state(query_targets=["항목"]), llm=llm, app_config=mock_config)
    assert len(llm.prompts) == 1                                  # 관련 테이블 선택 1회뿐
    assert all(STRUCTURE_ANALYSIS_PROMPT[:80] not in p for p in llm.prompts)
    assert all(SAMPLE_SQL_GENERATION_PROMPT[:80] not in p for p in llm.prompts)
    client.execute_sql.assert_not_called()                        # 샘플 SQL 실행 0


# ──────────────────────────────────────────────
# ④ 응답 본문 노출 — 3단 그래프(단일 DB) · 2단 서브에이전트 경로
# ──────────────────────────────────────────────

def test_output_generator_kind_matches_schema_analyzer():
    """노트 종류는 utils 한 곳에 있고 출력 노드가 렌더하는 종류에 포함된다(사본 없음)."""
    assert NOTE_STRUCTURE_MISSING in ADMIN_ASSET_NOTE_KINDS


def test_append_note_renders_only_structure_missing_once():
    note = structure_missing_note(DB_ID)
    state = {"dependency_notes": [note, dict(note), {"kind": "scope_db", "detail": "다른 노트"}]}
    out = append_structure_missing_note("본문", state)
    assert out == f"본문\n\n[안내] {note['detail']}"
    assert append_structure_missing_note("본문", {"dependency_notes": None}) == "본문"


_TEXT_RESPONSE = "src.nodes.output_generator._generate_text_response"


def _stub(result: dict):
    async def _node(state, **_kw):
        return result
    return _node


@pytest.mark.asyncio
async def test_tier3_single_db_final_response_shows_reason(mock_config, monkeypatch):
    """3단(semantic_router) 컴파일 그래프를 끝까지 돌려 최종 응답 본문에 사유가 1회 보이는지 본다.

    schema_analyzer·output_generator는 실제 노드, 나머지는 결정적 대역(LLM·DB 0).
    """
    mock_config.enable_semantic_routing = True
    mock_config.enable_intent_orchestration = False
    mock_config.enable_deepagents_package = False
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")
    monkeypatch.setattr(graph_module, "create_llm", lambda *a, **kw: AsyncMock())
    monkeypatch.setattr(graph_module, "context_resolver", _stub({}))
    monkeypatch.setattr(graph_module, "input_parser", _stub({"parsed_requirements": PARSED}))
    monkeypatch.setattr(graph_module, "field_mapper", _stub({}))
    monkeypatch.setattr(graph_module, "semantic_router", _stub({
        "routing_intent": "data_query", "is_multi_db": False, "active_db_id": DB_ID,
    }))
    monkeypatch.setattr(graph_module, "query_generator", _stub(GENERATED))
    monkeypatch.setattr(graph_module, "query_validator", _stub(PASSED))
    monkeypatch.setattr(graph_module, "query_executor", _stub(EXECUTED))
    monkeypatch.setattr(graph_module, "result_organizer", _stub(ORGANIZED))
    compiled = graph_module.build_graph(mock_config)

    async with _patched_io(_CacheMgr(applied=None), None):
        with patch(_TEXT_RESPONSE, AsyncMock(return_value=BODY)):
            result = await compiled.ainvoke(
                create_initial_state(user_query="항목 목록", thread_id="t-104"),
                {"configurable": {"thread_id": "t-104"}},
            )

    detail = structure_missing_note(DB_ID)["detail"]
    assert result["final_response"].startswith(BODY)
    assert result["final_response"].count(detail) == 1
    assert not result.get("awaiting_approval")


@pytest.mark.asyncio
async def test_tier3_error_response_also_shows_reason(mock_config):
    """재시도 초과 에러 응답에도 같은 사유가 붙는다(구조 안내 없이 실패한 자리)."""
    state = create_initial_state(user_query="항목 목록")
    state.update(
        retry_count=3,
        error_message="SQL 검증 실패",
        dependency_notes=[structure_missing_note(DB_ID)],
    )
    out = graph_module._error_response_node(state)
    assert out["final_response"].count(structure_missing_note(DB_ID)["detail"]) == 1


@pytest.mark.asyncio
async def test_tier2_subagent_path_shows_reason_exactly_once(mock_config):
    """2단: run_data_query_pipeline(실제 schema_analyzer) → result_aggregator(실제 출력 노드)."""
    from src.orchestration import subagents
    from src.orchestration.result_aggregator import result_aggregator

    task = {"task_id": "t1", "agent": "data_query", "sub_query": "항목 목록", "db_ids": [DB_ID],
            "depends_on": [], "input_from": [], "order": 1, "status": "pending"}
    isolated = {"user_query": "항목 목록", "parsed_requirements": PARSED}
    with patch.object(subagents, "query_generator", AsyncMock(return_value=GENERATED)), \
         patch.object(subagents, "query_validator", AsyncMock(return_value=PASSED)), \
         patch.object(subagents, "query_executor", AsyncMock(return_value=EXECUTED)), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value=ORGANIZED)):
        async with _patched_io(_CacheMgr(applied=None), None):
            res = await subagents.run_data_query_pipeline(
                task, isolated, llm=AsyncMock(), app_config=mock_config
            )

    # task 결과로 승격된다
    assert [n["kind"] for n in res["dependency_notes"]] == [NOTE_STRUCTURE_MISSING]

    state = create_initial_state(user_query="항목 목록")
    state.update(task_plan=[task], task_results={"t1": res})
    with patch(_TEXT_RESPONSE, AsyncMock(return_value=BODY)):
        out = await result_aggregator(
            state, llm=AsyncMock(), app_config=mock_config, synthesize=False
        )

    detail = structure_missing_note(DB_ID)["detail"]
    assert out["final_response"].count(detail) == 1
    assert "## 순차 처리 경과" in out["final_response"]          # 집계기 경과 블록 한 곳에만
    assert "[안내] " + detail not in out["final_response"]       # 본문(output_generator) 중복 없음


# ──────────────────────────────────────────────
# ⑤ 멀티 DB 대칭
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_db_analyze_schema_uses_applied_meta(mock_config, monkeypatch):
    cache_mgr = _CacheMgr(applied=APPLIED_META)
    monkeypatch.setattr("src.schema_cache.cache_manager.get_cache_manager", lambda cfg: cache_mgr)
    monkeypatch.setattr(sa_mod, "_load_manual_profile", lambda db_id: None)
    schema = await mdb._analyze_schema(
        _client(), {"original_query": "항목"}, db_id=DB_ID, app_config=mock_config
    )
    assert schema["_structure_meta"] == APPLIED_META
    assert cache_mgr.applied_calls == [DB_ID]


def test_multi_db_note_is_same_and_once():
    run = SimpleNamespace(dependency_notes=[])
    mdb._note_structure_missing(run, DB_ID)
    mdb._note_structure_missing(run, DB_ID)
    assert run.dependency_notes == [structure_missing_note(DB_ID)]
    mdb._note_structure_missing(SimpleNamespace(), DB_ID)  # 채널 없는 대역은 무시(예외 없음)


# ──────────────────────────────────────────────
# ⑥ 삭제된 게이트·설정 참조 0
# ──────────────────────────────────────────────

def test_deleted_gate_references_zero():
    pattern = re.compile(re.escape(_DELETED_GATE[: -len("_gate")]) + "|" + _DELETED_FIELD.upper())
    hits = [
        f"{path.relative_to(_ROOT)}:{i}"
        for path in sorted((_ROOT / "src").rglob("*.py"))
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []
    assert not (_ROOT / "src" / "nodes" / f"{_DELETED_GATE}.py").exists()


def test_deleted_setting_removed(mock_config):
    from src.config import AppConfig

    assert _DELETED_FIELD not in AppConfig.model_fields
    assert mock_config.schema_cache.admin_llm_concurrency == 2
    assert mock_config.schema_cache.structure_group_max_tables == 40
