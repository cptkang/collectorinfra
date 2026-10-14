"""사다리 하위 단 노드 미등록 — 빌드 타임 배타성 (plans/163 W1 D1).

`docs/21_orchestration_ladder.md` §2 · CLAUDE.md 「배타성은 빌드 타임이다 — 상위 단이 성립하면
하위 단은 노드조차 등록되지 않는다」를 고정한다. 종전에는 2·3단 플래그를 함께 켜면 2단 빌드에
3단 노드 6종(+장애 진단 on 시 `fault_diagnosis`)이 엣지 없이 등록되고, 1·2단 빌드에 단일 DB
꼬리(schema_analyzer … output_generator·error_response, 승인 on이면 approval_gate)가 START에서
닿지 않는 채로 등록됐다. 2단 서브에이전트는 노드 **함수**를 직접 호출하고(`subagents.py`),
1단은 도구 루프라 그래프 노드 이름을 쓰는 경로(Command goto·interrupt 재개·get_graph 소비)가 없다.

검증:
- 사다리 4단 × 옵트인 플래그(기본 · 단독 5종 · 전부) 조합에서 **등록 노드 = START 도달 노드**
  (죽은 노드 0)
- 1·2단 확정 빌드의 노드 집합이 그 단 고유 노드 + 공통 전단뿐 · `interrupt_before` 비어 있음
- 3·4단 확정 빌드는 단일 DB 꼬리를 그대로 등록·배선한다(종전 배선 불변은
  `test_plan166_w0_graph_topology.py` 스냅샷이 3·4단 39조합으로 고정)

실 LLM 0 · 네트워크 0 — 백엔드 판정·deepagents 조립을 대역으로 바꾼다(노드 등록만 본다).
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import src.graph as graph_module
from src.config import AppConfig, LLMConfig, OrchestratorConfig

#: 사다리 단 → (deepagents, intent_orchestration, semantic_routing). 상위 단 플래그도 켜 둔다
#: (로컬 `.env`처럼 1·2·3단 플래그가 함께 on인 구성을 재현한다).
_TIERS: dict[str, tuple[bool, bool, bool]] = {
    "deep_agent": (True, True, True),
    "intent_orchestration": (False, True, True),
    "semantic_router": (False, False, True),
    "legacy": (False, False, False),
}

_OPTIONS: tuple[str, ...] = ("approval", "fault_dx", "plan_loop", "seq", "probe")

#: 기본 · 단독 5종 · 전부
_COMBOS: tuple[frozenset[str], ...] = (
    frozenset(),
    *(frozenset({o}) for o in _OPTIONS),
    frozenset(_OPTIONS),
)

_COMMON = {"context_resolver", "input_parser", "field_mapper"}

_SINGLE_DB_TAIL = {
    "schema_analyzer", "query_generator", "query_validator", "query_executor",
    "result_organizer", "output_generator", "error_response",
}

_TIER3_ONLY = {
    "semantic_router", "multi_db_executor", "result_merger", "cache_management",
    "synonym_registrar", "general_inference", "fault_diagnosis",
}


def _config(tier: str, on: frozenset[str]) -> AppConfig:
    """사다리·옵트인 플래그를 전부 명시한 테스트 config(`.env` 누수 차단)."""
    deep, intent, semantic = _TIERS[tier]
    cfg = AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        orchestrator=OrchestratorConfig(
            provider="vllm", base_url="http://127.0.0.1:9/v1", model="Qwen3.5-9B",
        ),
    )
    cfg.worker_provider_override = None
    cfg.enable_deepagents_package = deep
    cfg.enable_intent_orchestration = intent
    cfg.enable_semantic_routing = semantic
    cfg.enable_sql_approval = "approval" in on
    cfg.noise_gate.fault_diagnosis_enabled = "fault_dx" in on
    cfg.tier3_plan_loop_enabled = "plan_loop" in on
    cfg.composite.sequential_fallback_tiers_enabled = "seq" in on
    cfg.cross_system_probe_enabled = "probe" in on
    cfg.observability.trace_enabled = False
    return cfg


def _build(monkeypatch: pytest.MonkeyPatch, tier: str, on: frozenset[str]) -> tuple[Any, str]:
    """그래프를 빌드해 (컴파일 결과, 확정 사다리 단)을 돌려준다."""
    deep = _TIERS[tier][0]
    monkeypatch.setattr(
        graph_module, "select_orchestration_backend",
        lambda c: "deep_agent" if deep else "semantic_router",
    )
    monkeypatch.setattr(graph_module, "_deep_agent_buildable", lambda c, llm: True)
    ladder: list[str] = []
    monkeypatch.setattr(
        graph_module, "record_ladder_resolution", lambda t, r, **_: ladder.append(t.value),
    )
    compiled = graph_module.build_graph(_config(tier, on), checkpointer=InMemorySaver())
    assert len(ladder) == 1, ladder
    return compiled, ladder[0]


def _registered(compiled: Any) -> set[str]:
    return {n for n in compiled.get_graph().nodes if not n.startswith("__")}


def _reachable(compiled: Any) -> set[str]:
    """START에서 (조건부 포함) 엣지로 닿는 노드."""
    edges: dict[str, set[str]] = {}
    for e in compiled.get_graph().edges:
        edges.setdefault(e.source, set()).add(e.target)
    seen: set[str] = set()
    stack = ["__start__"]
    while stack:
        node = stack.pop()
        if node not in seen:
            seen.add(node)
            stack.extend(edges.get(node, ()))
    return {n for n in seen if not n.startswith("__")}


_CASES = [
    pytest.param(tier, on, id=f"{tier}|{'+'.join(sorted(on)) or 'base'}")
    for tier in _TIERS
    for on in _COMBOS
]


@pytest.mark.parametrize(("tier", "on"), _CASES)
def test_registered_nodes_are_all_reachable(
    monkeypatch: pytest.MonkeyPatch, tier: str, on: frozenset[str],
) -> None:
    """어느 단·옵션 조합이든 등록된 노드는 전부 START에서 닿는다(죽은 노드 0)."""
    compiled, resolved = _build(monkeypatch, tier, on)
    assert resolved == tier
    registered = _registered(compiled)
    assert registered == _reachable(compiled), sorted(registered - _reachable(compiled))


@pytest.mark.parametrize("on", _COMBOS, ids=lambda o: "+".join(sorted(o)) or "base")
def test_tier1_registers_only_deep_agent_path(
    monkeypatch: pytest.MonkeyPatch, on: frozenset[str],
) -> None:
    """1단 확정 빌드는 공통 전단 + deep_agent뿐 — 2·3단·단일 DB 꼬리·승인 게이트 미등록."""
    compiled, _ = _build(monkeypatch, "deep_agent", on)
    assert _registered(compiled) == _COMMON | {"deep_agent"}
    assert not compiled.interrupt_before_nodes


@pytest.mark.parametrize("on", _COMBOS, ids=lambda o: "+".join(sorted(o)) or "base")
def test_tier2_registers_only_intent_path(
    monkeypatch: pytest.MonkeyPatch, on: frozenset[str],
) -> None:
    """2단 확정 빌드는 공통 전단 + 2단 4노드뿐 — 3단 노드·단일 DB 꼬리·승인 게이트 미등록."""
    compiled, _ = _build(monkeypatch, "intent_orchestration", on)
    assert _registered(compiled) == _COMMON | {
        "intent_planner", "agent_orchestrator", "replanner", "result_aggregator",
    }
    assert not compiled.interrupt_before_nodes


@pytest.mark.parametrize("tier", ["semantic_router", "legacy"])
def test_tier3_and_tier4_keep_single_db_tail(monkeypatch: pytest.MonkeyPatch, tier: str) -> None:
    """하위 단이 확정되는 구성에서는 단일 DB 꼬리·승인 게이트를 종전대로 등록·배선한다
    (D-161 — 분기를 지운 게 아니라 확정 단에서만 등록)."""
    compiled, _ = _build(monkeypatch, tier, frozenset({"approval"}))
    registered = _registered(compiled)
    assert _SINGLE_DB_TAIL | {"approval_gate"} <= registered
    assert list(compiled.interrupt_before_nodes) == ["approval_gate"]
    if tier == "semantic_router":
        assert _TIER3_ONLY - {"fault_diagnosis"} <= registered
    else:
        assert not (_TIER3_ONLY & registered)
