"""`entity_locator` 그래프 배선 (plans/102 §3.4·§3.5 ⑤ · X-6 · 성공 기준 6).

★ 이 파일이 지키는 계약
  ① `CROSS_SYSTEM_PROBE_ENABLED` off면 3단 그래프의 노드·엣지·분기 함수가 현행과 같다 —
     `entity_locator` 미등록 · `semantic_router`의 조건부 분기가 그대로.
  ② on이면 3단에만 등록 — `semantic_router → entity_locator → (기존 분기 대상 그대로)`.
     1단(deep_agent)·2단(intent)·4단(legacy)에는 등록하지 않는다.
  ③ 분기 함수는 사유 노출(HALT)일 때만 END, 아니면 기존 분기 함수에 그대로 위임한다.
  ④ 노드에 LLM이 주입되지 않는다.

DB·LLM 0(D-127) — 그래프는 빌드만 한다(실행하지 않는다).
"""

from __future__ import annotations

import functools
import inspect

import pytest

import src.graph as graph_module
from src.graph import build_graph, route_after_entity_locator, route_after_semantic_router

END = "__end__"


def _cfg(mock_config, *, probe: bool, semantic=True, intent=False, seq=False, fdx=False):
    cfg = mock_config
    cfg.enable_deepagents_package = False
    cfg.enable_intent_orchestration = intent
    cfg.enable_semantic_routing = semantic
    cfg.enable_sql_approval = False
    cfg.composite.sequential_fallback_tiers_enabled = seq
    cfg.noise_gate.fault_diagnosis_enabled = fdx
    cfg.cross_system_probe_enabled = probe
    return cfg


def _nodes(compiled) -> set[str]:
    return set(compiled.get_graph().nodes.keys())


def _edges(compiled) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in compiled.get_graph().edges}


def _branches(compiled) -> dict[str, dict]:
    return {src: dict(spec) for src, spec in compiled.builder.branches.items()}


@pytest.fixture
def semantic_backend(monkeypatch):
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")


def test_flag_defaults_off(mock_config):
    assert mock_config.cross_system_probe_enabled is False


@pytest.mark.parametrize("seq", [False, True])
@pytest.mark.parametrize("fdx", [False, True])
def test_tier3_off_is_unchanged_and_on_only_inserts_locator(
    mock_config, semantic_backend, seq, fdx
):
    off = build_graph(_cfg(mock_config, probe=False, seq=seq, fdx=fdx))
    on = build_graph(_cfg(mock_config, probe=True, seq=seq, fdx=fdx))

    # ① off — 노드 미등록 · semantic_router가 분기를 직접 가진다
    assert "entity_locator" not in _nodes(off)
    assert "semantic_router" in _branches(off) and "entity_locator" not in _branches(off)
    off_route = next(iter(_branches(off)["semantic_router"]))
    expected = "condition" if seq else "route_after_semantic_router"
    assert off_route == expected, "off면 분기 함수가 현행 그대로다"

    # ② on — 노드 하나만 더해지고, 기존 분기 대상은 entity_locator 뒤로 그대로 옮겨 간다
    assert _nodes(on) == _nodes(off) | {"entity_locator"}
    router_targets_off = {t for s, t in _edges(off) if s == "semantic_router"}
    router_targets_on = {t for s, t in _edges(on) if s == "semantic_router"}
    locator_targets = {t for s, t in _edges(on) if s == "entity_locator"}
    assert router_targets_on == {"entity_locator"}
    assert locator_targets == router_targets_off
    assert END in locator_targets, "사유 노출 행이 턴을 끝낼 수 있다"
    others_off = {e for e in _edges(off) if e[0] != "semantic_router"}
    others_on = {e for e in _edges(on) if e[0] not in ("semantic_router", "entity_locator")}
    assert others_on == others_off, "그 밖의 엣지는 바뀌지 않는다"


@pytest.mark.parametrize("tier", ["deep_agent", "intent", "legacy"])
def test_not_registered_outside_tier3(mock_config, monkeypatch, tier):
    if tier == "deep_agent":
        monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "deep_agent")
        monkeypatch.setattr(graph_module, "_deep_agent_buildable", lambda c, llm: True)
        cfg = _cfg(mock_config, probe=True)
    else:
        monkeypatch.setattr(
            graph_module, "select_orchestration_backend", lambda c: "semantic_router"
        )
        cfg = _cfg(mock_config, probe=True, intent=(tier == "intent"), semantic=(tier != "legacy"))
    compiled = build_graph(cfg)
    assert "entity_locator" not in _nodes(compiled)
    assert all("entity_locator" not in e for e in _edges(compiled))


def test_locator_node_has_no_llm(mock_config, semantic_backend):
    compiled = build_graph(_cfg(mock_config, probe=True))
    runnable = compiled.nodes["entity_locator"].bound
    target = runnable.afunc if getattr(runnable, "afunc", None) is not None else runnable.func
    target = inspect.unwrap(target)
    assert isinstance(target, functools.partial)
    assert "llm" not in target.keywords and set(target.keywords) == {"app_config"}


class TestRouteAfterEntityLocator:
    def test_halt_ends_turn(self):
        state = {"entity_probe": {"action": "halt"}, "is_multi_db": True}
        assert route_after_entity_locator(state, delegate=route_after_semantic_router) == END

    @pytest.mark.parametrize(
        "probe, multi, expected",
        [
            (None, False, "schema_analyzer"),
            ({"action": "query"}, True, "multi_db_executor"),
            ({"action": "keep"}, False, "schema_analyzer"),
        ],
    )
    def test_otherwise_delegates(self, probe, multi, expected):
        state = {"entity_probe": probe, "is_multi_db": multi, "routing_intent": "data_query"}
        assert route_after_entity_locator(state, delegate=route_after_semantic_router) == expected

    def test_delegate_is_called_verbatim(self):
        calls = []

        def delegate(state):
            calls.append(state)
            return "cache_management"

        state = {"routing_intent": "cache_management"}
        assert route_after_entity_locator(state, delegate=delegate) == "cache_management"
        assert calls == [state]
