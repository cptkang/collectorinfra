"""plans/132 W6 — 데이터 소스별 유사어 계약(G-13~G-17 확정 범위 · LLM 0회).

G-13(사용자 확정): 폴스타·ITAM은 **DB 공용 사전**(종전 전역 사전)을 같이 쓰고, 제니퍼·문서 등 비DB
소스의 유사어는 각자 분리한다 — 비DB 유사어의 정본은 소유 패키지 설정 파일이다(N-12d · G-17).

1. G-15 등록 가드 — 채팅 유사어 쓰기가 비DB 소스 맥락(이번 턴 지목 · 직전 턴 소스)이면 DB 사전에
   넣지 않고 안내만 한다. DB 맥락은 현행(DB를 못 정해도 묻지 않고 DB 공용 사전)
2. 비DB 처리기(`apm_query`·`doc_query`)는 DB 유사어 사전을 읽지 않는다(소스 밖 누수 금지)
3. G-16 — 값 유사어 치환(`input_parser._apply_column_value_synonyms`)은 컬럼명이 맞는 필터 조건에만
   걸리고, `apm_query`는 필터 조건에서 서버 식별자만 읽는다 → 치환 위치를 옮기지 않아도 비DB 조회에
   새지 않는다(근거 고정)
4. Y-8 — 양식 「유사어 등록」은 사용자가 확정한 단어라 `operator` 태그로 넣는다
"""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.cache_management import _SYNONYM_WRITE_ACTIONS, cache_management
from src.orchestration import apm_query as aq
from src.routing.registry import get_registry
from src.routing.source_hints import non_db_synonym_context

REPO = Path(__file__).resolve().parents[2]
#: `src.nodes`가 노드 함수를 재수출해 모듈 속성으로 접근한다.
input_parser = importlib.import_module("src.nodes.input_parser")


def _config() -> MagicMock:
    config = MagicMock()
    config.auth.enabled = False
    config.multi_db.get_active_db_ids.return_value = ["polestar", "itam"]
    return config


def _llm_for(action: str, **extra: object) -> MagicMock:
    llm = MagicMock()
    payload = {"action": action, "db_id": None, **extra}
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps(payload)))
    return llm


def _mgr() -> MagicMock:
    mgr = MagicMock()
    for name in ("add_global_synonym", "add_synonyms", "remove_global_synonym",
                 "remove_synonyms", "save_global_synonyms", "save_synonyms"):
        setattr(mgr, name, AsyncMock(return_value=True))
    mgr.get_global_synonyms = AsyncMock(return_value={})
    mgr.get_global_description = AsyncMock(return_value="")
    mgr.get_synonyms = AsyncMock(return_value={})
    mgr.get_schema = AsyncMock(return_value=None)
    return mgr


def _state(query: str, hints: list[str], previous: list[str] | None = None) -> dict:
    return {"user_query": query, "parsed_requirements": {"target_db_hints": hints},
            "conversation_context": {"previous_sources": previous or []}, "user_role": "user"}


# ── 1. G-15 등록 가드 ─────────────────────────────────────────────────────────

def test_context_judgement() -> None:
    label = get_registry().system_label("apm")
    assert non_db_synonym_context(["제니퍼"], []) == "제니퍼", "이번 턴 지목은 사용자 표현"
    assert non_db_synonym_context(["제니퍼", "폴스타"], []) is None, "DB 지목이 섞이면 DB 맥락"
    assert non_db_synonym_context([], ["apm"]) == label, "지목 없으면 직전 턴 소스(표시명)"
    assert non_db_synonym_context(["ITAM"], ["apm"]) is None, "이번 턴 지목이 직전 턴보다 우선"
    assert non_db_synonym_context([], []) is None, "DB 맥락 기본(현행)"
    assert non_db_synonym_context(["web01"], []) is None, "소스 아닌 힌트는 지목이 아니다"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hints,previous", [(["제니퍼"], []), ([], ["apm"]), (["전산관리매뉴얼"], [])])
async def test_non_db_context_does_not_write_db_dictionary(hints, previous) -> None:
    mgr = _mgr()
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        out = await cache_management(
            _state("인스턴스는 서버와 같은 말로 유사어 추가해줘", hints, previous),
            llm=_llm_for("add-synonym", target_column="hostname", words=["인스턴스"]),
            app_config=_config())
    assert "채팅으로 등록하지 않습니다" in out["final_response"]
    assert "DB 유사어 사전에도 넣지 않았습니다" in out["final_response"]
    mgr.add_global_synonym.assert_not_called()
    mgr.add_synonyms.assert_not_called()


@pytest.mark.asyncio
async def test_synonym_set_in_non_db_context_is_guided() -> None:
    mgr = _mgr()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=AssertionError("동의어 집합은 결정적 파싱 — LLM 미호출"))
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        out = await cache_management(
            _state("인스턴스, 서버, 노드는 동의어로 등록해줘", [], ["apm"]), llm=llm,
            app_config=_config())
    assert "채팅으로 등록하지 않습니다" in out["final_response"]
    mgr.add_global_synonym.assert_not_called()


@pytest.mark.asyncio
async def test_db_context_registers_to_shared_dictionary_as_before() -> None:
    """DB 맥락(지목 없음 · 직전 비DB 소스 없음)은 현행 — DB 공용 사전에 넣는다(G-13 · G-15)."""
    mgr = _mgr()
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        out = await cache_management(
            _state("호스트는 hostname의 유사어로 추가해줘", [], []),
            llm=_llm_for("add-synonym", target_column="hostname", words=["호스트"]),
            app_config=_config())
    assert "채팅으로 등록하지 않습니다" not in out["final_response"]
    mgr.add_global_synonym.assert_awaited()


def test_guarded_actions_cover_every_chat_write_path() -> None:
    """실측 전수 — 이 노드에서 유사어 사전에 쓰는 처리기는 모두 가드 대상이다."""
    src = (REPO / "src/nodes/cache_management.py").read_text(encoding="utf-8")
    writes = ("add_global_synonym", "add_synonyms(", "remove_global_synonym", "remove_synonyms(",
              "save_global_synonyms", "save_synonyms(", "generate_global_synonyms(")
    handlers = {
        "add-synonym": "_handle_add_synonym(", "add-synonym-set": "_handle_add_synonym_set(",
        "remove-synonym": "_handle_remove_synonym(", "update-synonym": "_handle_update_synonym(",
        "generate-global-synonyms": "_handle_generate_global_synonyms(",
        "generate-synonyms": "_handle_generate_synonyms(",
    }
    assert set(handlers) == set(_SYNONYM_WRITE_ACTIONS)
    assert any(w in src for w in writes)


# ── 2. 비DB 처리기는 DB 유사어 사전을 읽지 않는다 ─────────────────────────────

@pytest.mark.parametrize(
    "module", ["src/orchestration/apm_query.py", "src/orchestration/doc_query.py"])
def test_non_db_handlers_do_not_import_db_synonym_dictionaries(module: str) -> None:
    tree = ast.parse((REPO / module).read_text(encoding="utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
         for alias in node.names}
    assert not {m for m in imported if m.startswith("src.schema_cache")}
    text = (REPO / module).read_text(encoding="utf-8")
    assert "synonym" not in text.lower(), "소스 밖 유사어 사전 소비 0(N-12d)"


# ── 3. G-16 값 유사어 치환 위치 근거 ──────────────────────────────────────────

def _cache_with(values: dict) -> MagicMock:
    cache = MagicMock()
    cache.get_column_value_synonyms = AsyncMock(return_value=values)
    return cache


@pytest.mark.asyncio
async def test_value_synonyms_touch_only_matching_db_columns() -> None:
    parsed = {"filter_conditions": [
        {"field": "avail_status", "op": "=", "value": "비정상"},
        {"field": "hostname", "op": "=", "value": "비정상"},
    ]}
    values = {"AVAIL_STATUS": {"비정상": {"op": "!=", "value": 0}}}
    with patch.object(input_parser, "get_cache_manager", return_value=_cache_with(values)):
        out = await input_parser._apply_column_value_synonyms(parsed)
    assert out["filter_conditions"][0] == {"field": "avail_status", "op": "!=", "value": 0}
    assert out["filter_conditions"][1]["value"] == "비정상", "컬럼명이 다른 조건은 그대로"


@pytest.mark.asyncio
async def test_table_qualified_value_keys_do_not_match_bare_fields() -> None:
    """실측 기록(2026-10-02) — 시드의 `테이블.컬럼` 키(폴스타 `cmm_alarm.alarmseverity` · plans/133
    ITAM 시드도 같은 형태)는 입력 파서가 내는 맨 컬럼명 필드와 맞지 않는다(대조식이
    `키 == 필드 or 필드.endswith(키)`). 치환은 필드가 테이블 한정일 때만 걸린다 — 현행 동작 고정."""
    values = {"cmm_alarm.alarmseverity": {"critical": {"op": "=", "value": 3}}}
    for field, replaced in (("alarmseverity", False), ("cmm_alarm.alarmseverity", True)):
        parsed = {"filter_conditions": [{"field": field, "op": "=", "value": "critical"}]}
        with patch.object(input_parser, "get_cache_manager", return_value=_cache_with(values)):
            out = await input_parser._apply_column_value_synonyms(parsed)
        assert (out["filter_conditions"][0]["value"] == 3) is replaced, field


def test_apm_handler_reads_only_server_identifiers_from_filters() -> None:
    """`apm_query` 대상은 서버 식별자 조건뿐 — 값 유사어로 바뀐 상태 조건은 대상이 아니다."""
    isolated = {"parsed_requirements": {"filter_conditions": [
        {"field": "avail_status", "op": "!=", "value": 0},
        {"field": "hostname", "op": "=", "value": "web01"},
    ]}}
    targets = aq.resolve_apm_targets(isolated, 10)
    assert [t.hostname for t in targets] == ["web01"]
    assert aq.resolve_apm_targets({"parsed_requirements": {"filter_conditions": [
        {"field": "avail_status", "op": "!=", "value": 0}]}}, 10) == []


# ── 4. Y-8 양식 「유사어 등록」 = operator ─────────────────────────────────────

@pytest.mark.asyncio
async def test_form_synonym_registration_is_operator_tagged() -> None:
    from src.nodes.field_mapper import _handle_synonym_registration

    mgr = _mgr()
    state = {"pending_synonym_registrations": [
        {"index": 1, "db_id": "polestar", "column": "cmm_resource.hostname", "field": "서버명"}]}
    with patch("src.schema_cache.cache_manager.get_cache_manager", return_value=mgr):
        await _handle_synonym_registration(state, {"mode": "all"}, _config())
    mgr.add_synonyms.assert_awaited_once_with(
        "polestar", "cmm_resource.hostname", ["서버명"], source="operator")
    mgr.save_synonyms.assert_not_called()
