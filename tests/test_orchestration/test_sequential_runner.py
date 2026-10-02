"""3·4단 순차 러너 — `sequential-fallback-runner` (D-203 · plans/88 §4.7 · W9).

핵심 계약: ①1·2단 빌드에는 노드가 없다 ②플래그 off면 그래프 배선 바이트 동일 ③SQL 승인
플래그가 on이면 진입하지 않는다(우회 금지 — 구조 승인은 plans/104에서 게이트째 삭제) ④2단 부품
(_llm_decompose·agent_orchestrator·result_aggregator)을 재사용한다.
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, patch

import pytest

import src.graph as graph_module
from src.graph import build_graph, route_after_field_mapper_legacy, route_after_semantic_router_sequential
from src.orchestration.sequential_runner import sequential_entry, sequential_runner
from src.state import create_initial_state

sr_mod = importlib.import_module("src.orchestration.sequential_runner")
SEQ_QUERY = "CPU 사용률이 높은 서버를 찾아 그 서버들의 최근 1개월 CPU 사용률을 보여줘"


def _cfg(mock_config, *, runner=True, sql_approval=False):
    cfg = mock_config
    cfg.enable_intent_orchestration = False
    cfg.enable_deepagents_package = False
    cfg.enable_sql_approval = sql_approval
    cfg.composite.sequential_fallback_tiers_enabled = runner
    return cfg


# ──────────────────────────────────────────────
# 진입 판정
# ──────────────────────────────────────────────

def test_entry_requires_all_conditions(mock_config):
    cfg = _cfg(mock_config)
    st = {"user_query": SEQ_QUERY, "routing_intent": "data_query"}
    assert sequential_entry(st, cfg)
    assert not sequential_entry({**st, "user_query": "전체 서버 OS"}, cfg)          # 표지 없음
    assert not sequential_entry({**st, "routing_intent": "cache_management"}, cfg)   # 비데이터 의도
    assert not sequential_entry({**st, "template_structure": {"x": 1}}, cfg)         # 폼필
    assert not sequential_entry(st, _cfg(mock_config, runner=False))                 # 플래그 off


def test_entry_refuses_when_sql_approval_on(mock_config):
    """★ 승인 게이트 우회 금지 — SQL 승인이 켜져 있으면 진입하지 않는다."""
    st = {"user_query": SEQ_QUERY, "routing_intent": None}
    assert sequential_entry(st, _cfg(mock_config))
    assert not sequential_entry(st, _cfg(mock_config, sql_approval=True))


def test_entry_no_longer_waits_for_structure_hitl(mock_config):
    """plans/104: 구조 승인 설정이 사라져 기본 설정에서 순차 러너가 진입한다(102 X-T8 해소)."""
    st = {"user_query": SEQ_QUERY, "routing_intent": "data_query"}
    assert sequential_entry(st, _cfg(mock_config))


def test_route_wrappers_delegate_when_not_entering(mock_config):
    cfg = _cfg(mock_config)
    assert route_after_semantic_router_sequential({"user_query": "x", "routing_intent": "cache_management"}, config=cfg) == "cache_management"
    assert route_after_semantic_router_sequential({"user_query": SEQ_QUERY, "routing_intent": None}, config=cfg) == "sequential_runner"
    assert route_after_field_mapper_legacy({"user_query": "x"}, config=cfg) == "schema_analyzer"
    assert route_after_field_mapper_legacy({"user_query": SEQ_QUERY}, config=cfg) == "sequential_runner"


# ──────────────────────────────────────────────
# 그래프 배선 — 3·4단에만, 플래그 off면 불변
# ──────────────────────────────────────────────

def _names(compiled):
    return set(compiled.get_graph().nodes.keys())


def _pairs(compiled):
    return {(e.source, e.target) for e in compiled.get_graph().edges}


@pytest.fixture
def semantic_backend(monkeypatch):
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")


def test_tier3_registers_runner_only_when_flag_on(mock_config, semantic_backend):
    cfg = _cfg(mock_config, runner=True)
    cfg.enable_semantic_routing = True
    on = build_graph(cfg)
    assert "sequential_runner" in _names(on)
    assert ("semantic_router", "sequential_runner") in _pairs(on) and ("sequential_runner", "__end__") in _pairs(on)

    cfg_off = _cfg(mock_config, runner=False)
    cfg_off.enable_semantic_routing = True
    off = build_graph(cfg_off)
    assert "sequential_runner" not in _names(off)
    assert _names(off) == _names(on) - {"sequential_runner"}


def test_tier4_legacy_conditional_edge(mock_config, semantic_backend):
    cfg = _cfg(mock_config, runner=True)
    cfg.enable_semantic_routing = False
    on = build_graph(cfg)
    pairs = _pairs(on)
    assert ("field_mapper", "sequential_runner") in pairs and ("field_mapper", "schema_analyzer") in pairs

    cfg_off = _cfg(mock_config, runner=False)
    cfg_off.enable_semantic_routing = False
    off = build_graph(cfg_off)
    assert "sequential_runner" not in _names(off) and ("field_mapper", "schema_analyzer") in _pairs(off)


def test_tier2_never_registers_runner(mock_config, semantic_backend):
    cfg = _cfg(mock_config, runner=True)
    cfg.enable_intent_orchestration = True
    cfg.enable_semantic_routing = True
    compiled = build_graph(cfg)
    assert "sequential_runner" not in _names(compiled) and "intent_planner" in _names(compiled)


# ──────────────────────────────────────────────
# 노드 — 2단 부품 재사용
# ──────────────────────────────────────────────

CHAIN = {"tasks": [
    {"task_id": "t1", "agent": "data_query", "sub_query": "높은 서버", "depends_on": [], "input_from": [], "order": 1, "status": "pending"},
    {"task_id": "t2", "agent": "data_query", "sub_query": "그 서버들 CPU", "depends_on": ["t1"], "input_from": ["t1"], "order": 2, "status": "pending"},
], "clarification_needed": None}


@pytest.mark.asyncio
async def test_runner_two_pass_reuses_orchestrator_and_aggregator(mock_config):
    cfg = _cfg(mock_config)
    captured = {}

    async def fake_orch(state, *, llm, app_config):
        captured["task_plan"] = state["task_plan"]
        return {"task_plan": state["task_plan"], "task_results": {"t1": {"query_results": [{"hostname": "a"}]},
                                                                  "t2": {"query_results": [{"hostname": "a", "cpu": 1}]}}}

    async def fake_agg(state, *, llm, app_config, synthesize):
        captured["synthesize"] = synthesize
        return {"final_response": "답", "query_results": state["task_results"]["t2"]["query_results"]}

    with patch.object(sr_mod, "_llm_decompose", AsyncMock(return_value=CHAIN)), \
         patch.object(sr_mod, "agent_orchestrator", AsyncMock(side_effect=fake_orch)), \
         patch.object(sr_mod, "result_aggregator", AsyncMock(side_effect=fake_agg)):
        out = await sequential_runner(create_initial_state(user_query=SEQ_QUERY), llm=AsyncMock(), app_config=cfg)
    assert [t["task_id"] for t in captured["task_plan"]] == ["t1", "t2"] and captured["synthesize"] is False
    assert out["final_response"] == "답" and out["is_composite"] is True and out["current_node"] == "sequential_runner"


@pytest.mark.asyncio
async def test_runner_single_plan_runs_once_with_note(mock_config):
    cfg = _cfg(mock_config)
    single = {"tasks": [{"task_id": "t1", "agent": "data_query", "sub_query": SEQ_QUERY,
                         "depends_on": [], "input_from": [], "order": 1, "status": "pending"}]}
    captured = {}

    async def fake_orch(state, *, llm, app_config):
        captured["notes"] = state.get("dependency_notes")
        return {"task_plan": state["task_plan"], "task_results": {"t1": {"query_results": []}}}

    with patch.object(sr_mod, "_llm_decompose", AsyncMock(return_value=single)), \
         patch.object(sr_mod, "agent_orchestrator", AsyncMock(side_effect=fake_orch)), \
         patch.object(sr_mod, "result_aggregator", AsyncMock(return_value={"final_response": "x"})):
        out = await sequential_runner(create_initial_state(user_query=SEQ_QUERY), llm=AsyncMock(), app_config=cfg)
    assert out["is_composite"] is False
    assert captured["notes"][0]["reason"] == "sequential_not_applied"
    assert out["dependency_notes"][0]["kind"] == "decompose"
