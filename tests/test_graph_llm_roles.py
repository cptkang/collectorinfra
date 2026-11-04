"""plans/212 W2: 용도별 LLM 평면 배선 — `build_graph`가 역할 인스턴스를 노드에 주입한다.

★ 이 파일이 지키는 계약
  ① 역할을 하나도 설정하지 않으면 1·2·3단 모든 노드의 주입 `llm`이 워커와 같은 객체(`is`)다.
  ② `LLM_ROUTER_*` → 3단 `semantic_router`만 · `LLM_PLANNER_*` → 2단 intent_planner·replanner /
     3단 계획 루프 plan·replan만 · `LLM_ANSWER_*` → 응답 합성 노드(2단 result_aggregator · 3단
     general_inference·finalize · output_generator)만 다른 객체를 받는다.
  ③ 역할 provider만 바꾸고 엔드포인트가 없으면 조립이 ValueError로 실패한다(워커 폴백 없음).
  ④ 기동 로그 1줄(`LLM 평면: …`)이 남고 키 문자열은 싣지 않는다.

실 LLM 0 · 네트워크 0 — `src.llm._create_for_provider`를 가짜 객체 팩토리로 바꾼다. 테스트 config는
LLM 역할 필드·사다리 플래그·활성 DB를 모두 명시한다(`.env` 없는 워크트리에서도 같은 결과).
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import src.graph as graph_module
import src.llm as llm_module
from src.config import AppConfig, LLMConfig, OrchestratorConfig
from src.config.data_access import MultiDBConfig

_ROLE_ITEMS = ("provider", "base_url", "model", "api_key", "timeout")
_EMPTY_ROLES: dict[str, Any] = {
    f"{role}_{item}": (0 if item == "timeout" else "")
    for role in llm_module.LLM_ROLES
    for item in _ROLE_ITEMS
}

_WORKER_URL = "http://127.0.0.1:9/v1"
_ROLE_URL = "http://127.0.0.1:19/v1"
_ROLE_KEY = "fake-role-key-91c2"
_WORKER_KEY = "fake-worker-key-3b7d"

#: 사다리 단 → (deepagents, intent_orchestration, semantic_routing)
_TIERS: dict[str, tuple[bool, bool, bool]] = {
    "deep_agent": (True, True, True),
    "intent_orchestration": (False, True, True),
    "semantic_router": (False, False, True),
}

_PLANNER_NODES = {
    "intent_orchestration": {"intent_planner", "replanner"},
    "semantic_router": {"plan", "replan"},
}
_ANSWER_NODES = {
    "intent_orchestration": {"result_aggregator"},
    "semantic_router": {"general_inference", "finalize", "output_generator"},
}
_ROUTER_NODES = {"intent_orchestration": set(), "semantic_router": {"semantic_router"}}


class _FakeLLM:
    """생성 인자만 기록하는 가짜 LLM(호출하지 않는다)."""

    def __init__(self, provider: str, base_url: str, model: str, purpose: str) -> None:
        self.provider = provider
        self.base_url = base_url
        self.model = model
        self.purpose = purpose


@pytest.fixture
def created(monkeypatch: pytest.MonkeyPatch) -> list[_FakeLLM]:
    """`_create_for_provider`를 가로채 만든 가짜 객체를 순서대로 남긴다."""
    made: list[_FakeLLM] = []

    def _fake(config: AppConfig, provider: str, purpose: str) -> _FakeLLM:
        llm_cfg = config.llm
        fields = llm_module._ROLE_WORKER_FIELDS[provider]
        base_url = getattr(llm_cfg, fields["base_url"]) if fields["base_url"] else ""
        model = getattr(llm_cfg, fields["model"]) if fields["model"] else ""
        obj = _FakeLLM(provider, base_url, model, purpose)
        made.append(obj)
        return obj

    monkeypatch.setattr(llm_module, "_create_for_provider", _fake)
    return made


def _config(tier: str, *, plan_loop: bool = True, **roles: Any) -> AppConfig:
    """역할 필드·사다리·옵트인 플래그·활성 DB를 전부 명시한 테스트 config."""
    deep, intent, semantic = _TIERS[tier]
    llm_values = dict(_EMPTY_ROLES)
    llm_values.update(roles)
    cfg = AppConfig(
        llm=LLMConfig(
            _env_file=None,
            provider="vllm",
            vllm_base_url=_WORKER_URL,
            vllm_model="worker-model",
            vllm_api_key=_WORKER_KEY,
            ollama_base_url="",
            **llm_values,
        ),
        orchestrator=OrchestratorConfig(
            _env_file=None, provider="vllm", base_url=_WORKER_URL, model="orch-model",
        ),
        multi_db=MultiDBConfig(_env_file=None, ACTIVE_DB_IDS="polestar"),
    )
    cfg.worker_provider_override = None
    cfg.enable_deepagents_package = deep
    cfg.enable_intent_orchestration = intent
    cfg.enable_semantic_routing = semantic
    cfg.enable_sql_approval = False
    cfg.noise_gate.fault_diagnosis_enabled = False
    cfg.noise_gate.sre_chat_investigation_enabled = False
    cfg.tier3_plan_loop_enabled = plan_loop
    cfg.composite.sequential_fallback_tiers_enabled = False
    cfg.cross_system_probe_enabled = False
    cfg.observability.trace_enabled = False
    return cfg


def _build(monkeypatch: pytest.MonkeyPatch, cfg: AppConfig, tier: str) -> Any:
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
    compiled = graph_module.build_graph(cfg, checkpointer=InMemorySaver())
    assert ladder == [tier]
    return compiled


def _partial_kwargs(target: Any) -> dict[str, Any]:
    """partial을 따라 내려가며 키워드 인자를 모은다(바깥 partial 우선 · inner partial 포함)."""
    found: dict[str, Any] = {}
    stack = [target]
    while stack:
        obj = inspect.unwrap(stack.pop())
        if isinstance(obj, functools.partial):
            for key, value in obj.keywords.items():
                found.setdefault(key, value)
                if isinstance(value, functools.partial):
                    stack.append(value)
            stack.append(obj.func)
    return found


def _injected_llms(compiled: Any) -> dict[str, Any]:
    """노드 이름 → 주입된 `llm`/`worker_llm`(LLM 미주입 노드는 제외)."""
    out: dict[str, Any] = {}
    for name, node in compiled.nodes.items():
        if name.startswith("__"):
            continue
        runnable = node.bound
        target = runnable.afunc if getattr(runnable, "afunc", None) is not None else runnable.func
        kwargs = _partial_kwargs(target)
        for key in ("llm", "worker_llm"):
            if key in kwargs:
                out[name] = kwargs[key]
                break
    return out


def _worker(created: list[_FakeLLM]) -> _FakeLLM:
    return created[0]


# ── ① 역할 미설정 = 워커 객체 그대로 ─────────────────────────────────────


@pytest.mark.parametrize(
    ("tier", "plan_loop"),
    [("deep_agent", False), ("intent_orchestration", False), ("semantic_router", True)],
)
def test_no_roles_every_node_gets_worker(
    monkeypatch: pytest.MonkeyPatch, created: list[_FakeLLM], tier: str, plan_loop: bool,
) -> None:
    compiled = _build(monkeypatch, _config(tier, plan_loop=plan_loop), tier)
    assert len(created) == 1, "역할 미설정이면 워커 1개만 만든다"
    worker = _worker(created)
    injected = _injected_llms(compiled)
    assert injected, "LLM 주입 노드를 찾지 못했다(partial 판독 실패)"
    expected = (
        _ROUTER_NODES.get(tier, set()) | _PLANNER_NODES.get(tier, set())
        | _ANSWER_NODES.get(tier, set())
    )
    assert expected <= set(injected)
    assert all(llm is worker for llm in injected.values()), {
        n for n, llm in injected.items() if llm is not worker
    }


# ── ② 역할별 주입 지점 ────────────────────────────────────────────────


_ROLE_CASES = [
    pytest.param("router", "semantic_router", _ROUTER_NODES["semantic_router"], id="router-t3"),
    pytest.param(
        "planner", "intent_orchestration", _PLANNER_NODES["intent_orchestration"], id="planner-t2",
    ),
    pytest.param("planner", "semantic_router", _PLANNER_NODES["semantic_router"], id="planner-t3"),
    pytest.param(
        "answer", "intent_orchestration", _ANSWER_NODES["intent_orchestration"], id="answer-t2",
    ),
    pytest.param("answer", "semantic_router", _ANSWER_NODES["semantic_router"], id="answer-t3"),
    pytest.param("router", "intent_orchestration", set(), id="router-t2-unused"),
]


@pytest.mark.parametrize(("role", "tier", "nodes"), _ROLE_CASES)
def test_role_reaches_only_its_nodes(
    monkeypatch: pytest.MonkeyPatch,
    created: list[_FakeLLM],
    role: str,
    tier: str,
    nodes: set[str],
) -> None:
    cfg = _config(
        tier, **{f"{role}_base_url": _ROLE_URL, f"{role}_model": f"{role}-model"},
    )
    compiled = _build(monkeypatch, cfg, tier)
    worker = _worker(created)
    role_objs = [c for c in created if c is not worker]
    assert len(role_objs) == 1
    role_llm = role_objs[0]
    assert (role_llm.provider, role_llm.base_url, role_llm.model) == (
        "vllm", _ROLE_URL, f"{role}-model",
    )
    assert role_llm.purpose == ("answer" if role == "answer" else "deterministic")

    injected = _injected_llms(compiled)
    got_role = {n for n, llm in injected.items() if llm is role_llm}
    assert got_role == nodes
    assert all(injected[n] is worker for n in set(injected) - nodes)


# ── ③ 필수값 누락 = 조립 실패 ─────────────────────────────────────────


@pytest.mark.parametrize("role", llm_module.LLM_ROLES)
def test_role_provider_without_endpoint_fails_build(
    monkeypatch: pytest.MonkeyPatch, created: list[_FakeLLM], role: str,
) -> None:
    cfg = _config("intent_orchestration", mlx_base_url="", **{f"{role}_provider": "mlx"})
    with pytest.raises(ValueError, match=f"LLM 역할 {role}"):
        _build(monkeypatch, cfg, "intent_orchestration")


# ── ④ 기동 로그 1줄 · 키 없음 ─────────────────────────────────────────


def test_startup_log_one_line_without_keys(
    monkeypatch: pytest.MonkeyPatch, created: list[_FakeLLM], caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = _config(
        "semantic_router",
        router_base_url=_ROLE_URL, router_model="router-model", router_api_key=_ROLE_KEY,
    )
    with caplog.at_level(logging.INFO, logger="src.graph"):
        _build(monkeypatch, cfg, "semantic_router")
    lines = [r.getMessage() for r in caplog.records if r.name == "src.graph"]
    planes = [m for m in lines if m.startswith("LLM 평면: ")]
    assert len(planes) == 1, planes
    line = planes[0]
    assert "router=vllm(router-model@127.0.0.1:19" in line
    assert "planner=워커 승계" in line and "answer=워커 승계" in line
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert _ROLE_KEY not in joined and _WORKER_KEY not in joined
