"""plans/187 W12 — 채팅 증상 질의 조사를 2단(기준 경로 `intent_orchestration`)에도 배선.

- 플래그 조합: `fault_diagnosis_enabled` · `sre_chat_investigation_enabled` 중 하나라도 off면
  2단 빌드가 종전과 같다(`fault_diagnosis` 노드 미등록 · field_mapper → intent_planner 직행).
- 둘 다 on: `fault_diagnosis` 노드·조건부 분기 등록(등록 노드 = START 도달 노드).
- 판정(`route_after_field_mapper_tier2`): 진단 표지 + 대상 1건 × 유형 1건(플레이북 경로)만
  조사로, 나머지(대상 0·2 · 유형 0·2 · 지시어 · 진단 표지 없는 조회)는 종전 2단 흐름.
- 그래프 종단: 확정 질의 → 플레이북 잡 접수 + 조사 카드 · 미확정 질의 → intent_planner 이후 흐름 ·
  두 경우 모두 LLM 호출 0회(전단 노드는 대역 — 라우팅 판정만 본다).

실 LLM 0 · 네트워크 0 · sre_agent import 0(MCP 계약 페이크 클라이언트).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import src.graph as graph_module
import src.nodes.fault_diagnosis as fd
from src.config import AppConfig, LLMConfig, OrchestratorConfig
from src.graph import route_after_field_mapper_tier2

_COMMON = {"context_resolver", "input_parser", "field_mapper"}
_TIER2 = {"intent_planner", "agent_orchestrator", "replanner", "result_aggregator"}


class CountingLLM:
    """호출 수를 세는 가짜 LLM — 어떤 호출 형태든 센 뒤 실패시킨다."""

    def __init__(self) -> None:
        self.calls = 0

    def _hit(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("LLM 호출 금지")

    invoke = ainvoke = with_structured_output = bind_tools = _hit


class FakeClient:
    """sre_agent MCP 계약(submit/diagnose/poll) 페이크."""

    def __init__(self) -> None:
        self.submits: list[dict] = []
        self.diagnoses: list[dict] = []

    async def unreachable_reason(self) -> None:
        return None

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def submit(self, payload: dict) -> dict:
        self.submits.append(payload)
        return {"status": "accepted", "investigation_id": f"inv-pb-{len(self.submits)}"}

    async def diagnose(self, question: str, **kw: Any) -> dict:
        self.diagnoses.append({"question": question, **kw})
        return {"status": "accepted", "investigation_id": f"inv-dx-{len(self.diagnoses)}"}

    async def poll(self, investigation_id: str) -> dict:
        return {"status": "running"}


def _config(tmp_path: Any, *, fault_dx: bool, chat: bool) -> AppConfig:
    """2단 확정 config — 검증 대상 필드를 전부 명시한다(`.env` 누수 차단)."""
    cfg = AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        orchestrator=OrchestratorConfig(
            provider="vllm", base_url="http://127.0.0.1:9/v1", model="Qwen3.5-9B",
        ),
    )
    cfg.worker_provider_override = None
    cfg.enable_deepagents_package = False
    cfg.enable_intent_orchestration = True
    cfg.enable_semantic_routing = True
    cfg.enable_sql_approval = False
    cfg.tier3_plan_loop_enabled = False
    cfg.composite.sequential_fallback_tiers_enabled = False
    cfg.composite.availability_precheck_enabled = False
    cfg.composite.max_targets = 10
    cfg.cross_system_probe_enabled = False
    cfg.observability.trace_enabled = False
    cfg.host_authz.mode = "admin_only"
    ng = cfg.noise_gate
    ng.fault_diagnosis_enabled = fault_dx
    ng.sre_chat_investigation_enabled = chat
    ng.investigation_service_url = "http://localhost:9098/sse"
    ng.investigation_mcp_call_timeout_seconds = 5.0
    ng.investigation_store_path = str(tmp_path / "inv.jsonl")
    ng.investigation_additional_user_hourly_limit = 3
    return cfg


def _build(monkeypatch: pytest.MonkeyPatch, cfg: AppConfig) -> tuple[Any, CountingLLM]:
    """2단 그래프를 빌드한다 — 백엔드 판정은 대역, LLM은 호출 수를 세는 가짜."""
    llm = CountingLLM()
    monkeypatch.setattr(graph_module, "create_llm", lambda *a, **k: llm)
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")
    ladder: list[str] = []
    monkeypatch.setattr(
        graph_module, "record_ladder_resolution", lambda t, r, **_: ladder.append(t.value),
    )
    compiled = graph_module.build_graph(cfg, checkpointer=InMemorySaver())
    assert ladder == ["intent_orchestration"]
    return compiled, llm


def _registered(compiled: Any) -> set[str]:
    return {n for n in compiled.get_graph().nodes if not n.startswith("__")}


def _targets_of(compiled: Any, source: str) -> set[str]:
    return {e.target for e in compiled.get_graph().edges if e.source == source}


def _reachable(compiled: Any) -> set[str]:
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


# ── 빌드 — 플래그 조합 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fault_dx", "chat"), [(False, False), (True, False), (False, True)],
    ids=["both_off", "fault_dx_only", "chat_only"],
)
def test_off_combos_keep_tier2_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, fault_dx: bool, chat: bool,
) -> None:
    """하나라도 off면 노드 미등록 · field_mapper → intent_planner 직행(종전과 같다)."""
    compiled, _ = _build(monkeypatch, _config(tmp_path, fault_dx=fault_dx, chat=chat))
    assert _registered(compiled) == _COMMON | _TIER2
    assert _targets_of(compiled, "field_mapper") == {"intent_planner"}


def test_both_on_registers_fault_diagnosis_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """둘 다 on이면 fault_diagnosis 노드와 분기만 더한다 — 죽은 노드 0 · 종단은 END."""
    compiled, _ = _build(monkeypatch, _config(tmp_path, fault_dx=True, chat=True))
    registered = _registered(compiled)
    assert registered == _COMMON | _TIER2 | {"fault_diagnosis"}
    assert registered == _reachable(compiled)
    assert _targets_of(compiled, "field_mapper") == {"fault_diagnosis", "intent_planner"}
    assert _targets_of(compiled, "fault_diagnosis") == {"__end__"}


# ── 판정 — 결정적 라우터 ──────────────────────────────────────────────


def _state(query: str, hosts: list[str] | None = None, **extra: Any) -> dict:
    conds = [{"field": "hostname", "value": h} for h in (hosts or [])]
    st: dict[str, Any] = {
        "user_query": query,
        "active_db_id": "polestar",
        "parsed_requirements": {"filter_conditions": conds},
        "conversation_context": None,
        "user_role": "admin",
        "user_id": "admin-1",
    }
    st.update(extra)
    return st


@pytest.mark.parametrize(
    ("query", "hosts", "expected"),
    [
        ("A1 응답 지연 원인 알려줘", ["A1"], "fault_diagnosis"),
        ("web01 CPU 사용률 왜 높아?", ["web01"], "fault_diagnosis"),
        ("web01 메모리 진단해줘", ["web01"], "fault_diagnosis"),
        # 진단 표지 없는 조회는 조사로 가로채지 않는다
        ("A1 응답시간 보여줘", ["A1"], "intent_planner"),
        ("web01 CPU 사용률 상위 10건", ["web01"], "intent_planner"),
        # 대상 0 · 2
        ("응답 지연 원인 알려줘", [], "intent_planner"),
        ("web01 web02 CPU 원인 분석", ["web01", "web02"], "intent_planner"),
        # 유형 0 · 2 · 미확정 kind(디스크 — 용량·I/O 둘)
        ("web01 장애 원인 알려줘", ["web01"], "intent_planner"),
        ("web01 CPU 메모리 원인 분석", ["web01"], "intent_planner"),
        ("web01 디스크 원인 분석", ["web01"], "intent_planner"),
        # 지시어는 식별자가 아니다
        ("그 서버 CPU 원인 분석", ["그 서버"], "intent_planner"),
    ],
)
def test_route_after_field_mapper_tier2(
    tmp_path: Any, query: str, hosts: list[str], expected: str,
) -> None:
    cfg = _config(tmp_path, fault_dx=True, chat=True)
    assert route_after_field_mapper_tier2(_state(query, hosts), app_config=cfg) == expected


def test_prior_targets_not_used_for_entry(tmp_path: Any) -> None:
    """직전 턴 승계 대상만 있으면 진입하지 않는다(이번 턴 명시 대상만 — G-32)."""
    cfg = _config(tmp_path, fault_dx=True, chat=True)
    st = _state(
        "CPU 원인 분석해줘",
        prior_targets=[{"hostname": "web01", "db_id": "polestar"}],
        conversation_context={"previous_entities": {"hostname": ["web01"]}},
    )
    assert route_after_field_mapper_tier2(st, app_config=cfg) == "intent_planner"


# ── 그래프 종단 — LLM 호출 0 ───────────────────────────────────────────


def _stub_front(monkeypatch: pytest.MonkeyPatch, hosts: list[str]) -> None:
    """전단 3노드를 대역으로 — field_mapper가 이번 턴 대상(filter_conditions)을 싣는다."""

    async def context_resolver(state: dict, **_: Any) -> dict:
        return {}

    async def input_parser(state: dict, **_: Any) -> dict:
        return {
            "parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "value": h} for h in hosts],
            },
        }

    async def field_mapper(state: dict, **_: Any) -> dict:
        return {}

    monkeypatch.setattr(graph_module, "context_resolver", context_resolver)
    monkeypatch.setattr(graph_module, "input_parser", input_parser)
    monkeypatch.setattr(graph_module, "field_mapper", field_mapper)


def _stub_tier2(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """2단 4노드 대역 — 지난 노드 이름을 기록한다."""
    visited: list[str] = []

    def _node(name: str, update: dict) -> Any:
        async def node(state: dict, **_: Any) -> dict:
            visited.append(name)
            return update
        return node

    monkeypatch.setattr(graph_module, "intent_planner", _node("intent_planner", {}))
    monkeypatch.setattr(graph_module, "agent_orchestrator", _node("agent_orchestrator", {}))
    monkeypatch.setattr(graph_module, "replanner", _node("replanner", {"needs_replan": False}))
    monkeypatch.setattr(
        graph_module, "result_aggregator",
        _node("result_aggregator", {"final_response": "2단 응답"}),
    )
    return visited


async def _run(compiled: Any, query: str) -> dict:
    return await compiled.ainvoke(
        {"user_query": query, "active_db_id": "polestar", "user_role": "admin",
         "user_id": "admin-1"},
        config={"configurable": {"thread_id": "t-ladder2"}},
    )


@pytest.mark.asyncio
async def test_confirmed_query_submits_playbook_without_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """확정 질의 → 플레이북 잡 접수 · 조사 카드 · 2단 노드 미경유 · LLM 0회."""
    _stub_front(monkeypatch, ["A1"])
    visited = _stub_tier2(monkeypatch)
    client = FakeClient()
    monkeypatch.setattr(fd, "_build_client", lambda gate_cfg: client)
    monkeypatch.setattr(fd, "log_investigation", AsyncMock())
    compiled, llm = _build(monkeypatch, _config(tmp_path, fault_dx=True, chat=True))

    out = await _run(compiled, "A1 응답 지연 원인 알려줘")

    assert llm.calls == 0
    assert visited == []
    assert out["routing_intent"] == "fault_diagnosis"
    assert out["final_response"].startswith("장애 조사를 접수했습니다")
    assert len(client.submits) == 1 and client.diagnoses == []
    assert client.submits[0]["meta"]["playbook_hint"] == "was.latency"
    ref = out["disclosures"][0]["ref"]
    assert ref["investigation_id"] == "inv-pb-1"
    assert ref["playbook"] == "was.latency"
    assert ref["route_label"] == "플레이북 was.latency — 대상·증상 일치"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "hosts"),
    [("A1 응답시간 보여줘", ["A1"]), ("응답 지연 원인 알려줘", [])],
    ids=["lookup", "no_target"],
)
async def test_unconfirmed_query_keeps_tier2_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, query: str, hosts: list[str],
) -> None:
    """미확정 질의 → intent_planner 이후 종전 흐름 · 조사 접수 0 · 판정에 LLM 0회."""
    _stub_front(monkeypatch, hosts)
    visited = _stub_tier2(monkeypatch)
    client = FakeClient()
    monkeypatch.setattr(fd, "_build_client", lambda gate_cfg: client)
    compiled, llm = _build(monkeypatch, _config(tmp_path, fault_dx=True, chat=True))

    out = await _run(compiled, query)

    assert llm.calls == 0
    assert visited == ["intent_planner", "agent_orchestrator", "replanner", "result_aggregator"]
    assert out["final_response"] == "2단 응답"
    assert client.submits == [] and client.diagnoses == []
