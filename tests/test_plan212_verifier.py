"""plans/212 독립 검증(verifier) — 구현자 테스트가 덮지 않는 지점.

  ① 4단(legacy) 그래프도 역할 미설정이면 모든 노드가 워커와 같은 객체이고, answer 역할은
     단일 DB 꼬리 `output_generator`에만 간다(§4.3 · §9-1).
  ② 리로드에서 역할 해석이 실패하면 500 · 이전 그래프·설정을 그대로 둔다 — 워커로 대체한 새
     그래프가 들어가지 않는다(§4.1 ③).
  ③ 실 소켓 가짜 서버 2대 — 워커와 역할이 각자 지정한 엔드포인트·모델로만 요청한다(§9-2).
  ④ 과금 판정 소비처 전수: `scripts/`의 모든 `external_planes(` 호출이 `roles=`를 넘긴다
     (주입 평면이 리터럴 mlx인 1곳 예외) · 판정 집합 정의는 preflight 1곳(§4.4 · D-127).
  ⑤ (기존 결함 기록) `WORKER_PROVIDER_OVERRIDE`가 워커·미설정 역할 평면을 gemini로 바꿔도
     과금 판정이 보지 않는다 — strict xfail.

실 LLM 0 · 외부 네트워크 0(127.0.0.1 임시 포트만). 테스트 config는 역할 15필드와 provider별
필드를 명시해 `.env` 누수를 막는다.
"""

from __future__ import annotations

import ast
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

import src.llm as llm_module
from src.api.routes.admin import reload_settings
from src.config import AppConfig, LLMConfig, OrchestratorConfig
from tests.test_api.test_settings_llm_roles import (
    _ADMIN,
    _FakeLoadConfig,
    _isolated,
    _request,
)
from tests.test_graph_llm_roles import _ROLE_URL, _FakeLLM, _injected_llms
from tests.test_graph_llm_roles import _config as _tier_config

_ROOT = Path(__file__).resolve().parent.parent
_EMPTY_ROLES: dict[str, Any] = {
    f"{role}_{item}": (0 if item == "timeout" else "")
    for role in llm_module.LLM_ROLES
    for item in ("provider", "base_url", "model", "api_key", "timeout")
}


@pytest.fixture
def created(monkeypatch: pytest.MonkeyPatch) -> list[_FakeLLM]:
    made: list[_FakeLLM] = []

    def _fake(config: AppConfig, provider: str, purpose: str) -> _FakeLLM:
        fields = llm_module._ROLE_WORKER_FIELDS[provider]
        base_url = getattr(config.llm, fields["base_url"]) if fields["base_url"] else ""
        model = getattr(config.llm, fields["model"]) if fields["model"] else ""
        obj = _FakeLLM(provider, base_url, model, purpose)
        made.append(obj)
        return obj

    monkeypatch.setattr(llm_module, "_create_for_provider", _fake)
    return made


def _legacy_config(**roles: Any) -> AppConfig:
    cfg = _tier_config("semantic_router", plan_loop=False, **roles)
    cfg.enable_deepagents_package = False
    cfg.enable_intent_orchestration = False
    cfg.enable_semantic_routing = False
    return cfg


def _build_legacy(monkeypatch: pytest.MonkeyPatch, cfg: AppConfig) -> Any:
    from langgraph.checkpoint.memory import InMemorySaver

    import src.graph as graph_module

    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")
    ladder: list[str] = []
    monkeypatch.setattr(
        graph_module, "record_ladder_resolution", lambda t, r, **_: ladder.append(t.value),
    )
    compiled = graph_module.build_graph(cfg, checkpointer=InMemorySaver())
    assert ladder == ["legacy"]
    return compiled


# ── ① 4단 legacy ─────────────────────────────────────────────────────


def test_legacy_tier_no_roles_every_node_gets_worker(
    monkeypatch: pytest.MonkeyPatch, created: list[_FakeLLM],
) -> None:
    compiled = _build_legacy(monkeypatch, _legacy_config())
    assert len(created) == 1
    injected = _injected_llms(compiled)
    assert "output_generator" in injected
    assert all(llm is created[0] for llm in injected.values())


def test_legacy_tier_answer_role_reaches_only_output_generator(
    monkeypatch: pytest.MonkeyPatch, created: list[_FakeLLM],
) -> None:
    cfg = _legacy_config(answer_base_url=_ROLE_URL, answer_model="answer-model")
    compiled = _build_legacy(monkeypatch, cfg)
    worker, answer = created
    assert (answer.base_url, answer.model, answer.purpose) == (_ROLE_URL, "answer-model", "answer")
    injected = _injected_llms(compiled)
    assert {n for n, llm in injected.items() if llm is answer} == {"output_generator"}
    assert all(injected[n] is worker for n in set(injected) - {"output_generator"})


# ── ② 리로드 실패 = 이전 그래프 유지 ─────────────────────────────────


@pytest.mark.parametrize("role", llm_module.LLM_ROLES)
async def test_reload_with_broken_role_keeps_previous_graph(
    monkeypatch: pytest.MonkeyPatch, created: list[_FakeLLM], role: str,
) -> None:
    monkeypatch.setattr("src.security.audit_logger.setup_logging", lambda level="INFO": None)
    old = _isolated(_tier_config("intent_orchestration", plan_loop=False))
    fresh = _isolated(_tier_config(
        "intent_orchestration", plan_loop=False, mlx_base_url="", **{f"{role}_provider": "mlx"},
    ))
    monkeypatch.setattr("src.config.load_config", _FakeLoadConfig(fresh))
    request = _request(old)
    old_graph = request.app.state.graph

    with pytest.raises(HTTPException) as exc_info:
        await reload_settings(request, _ADMIN)

    assert exc_info.value.status_code == 500
    assert "ValueError" in str(exc_info.value.detail)
    assert request.app.state.graph is old_graph
    assert request.app.state.config is old


# ── ③ 실 소켓 가짜 서버 — 엔드포인트·모델 분리 ───────────────────────


class _Recorder(BaseHTTPRequestHandler):
    seen: list[tuple[int, str, str]] = []

    def do_POST(self) -> None:  # noqa: N802 — http.server 규약
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.server.server_address[1], self.path, body["model"]))
        payload = json.dumps({
            "id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        return None


@pytest.fixture
def two_servers():
    pytest.importorskip("langchain_openai")
    _Recorder.seen = []
    servers = [ThreadingHTTPServer(("127.0.0.1", 0), _Recorder) for _ in range(2)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for t in threads:
        t.start()
    try:
        yield [s.server_address[1] for s in servers]
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()


def test_role_requests_reach_only_their_own_server(two_servers: list[int]) -> None:
    port_w, port_r = two_servers
    roles = dict(_EMPTY_ROLES)
    roles.update(
        router_base_url=f"http://127.0.0.1:{port_r}/v1", router_model="router-M",
        planner_provider="mlx", planner_base_url=f"http://127.0.0.1:{port_r}/v1",
        planner_model="planner-M",
    )
    cfg = AppConfig(
        llm=LLMConfig(
            _env_file=None, provider="vllm",
            vllm_base_url=f"http://127.0.0.1:{port_w}/v1", vllm_model="worker-M",
            vllm_api_key="", vllm_verify_ssl=True, mlx_base_url="", mlx_model="",
            **roles,
        ),
        orchestrator=OrchestratorConfig(_env_file=None, provider="vllm", base_url="", model="o"),
    )
    worker = llm_module.create_llm(cfg)
    router = llm_module.create_role_llm(cfg, "router", worker)
    planner = llm_module.create_role_llm(cfg, "planner", worker)
    answer = llm_module.create_role_llm(cfg, "answer", worker)
    assert answer is worker
    for llm in (worker, router, planner):
        assert llm.invoke("hi").content == "ok"
    assert _Recorder.seen == [
        (port_w, "/v1/chat/completions", "worker-M"),
        (port_r, "/v1/chat/completions", "router-M"),
        (port_r, "/v1/chat/completions", "planner-M"),
    ]


# ── ④ 과금 판정 소비처 전수 ───────────────────────────────────────────

#: `roles=` 없이 불러도 되는 호출 — (파일, 둘레 함수). 주입 평면이 리터럴 mlx이고 역할은 같은
#: 진입(`run`)이 `_body_roles()`로 따로 판정한다.
_ROLELESS_ALLOWED = {("scripts/apm_bench/driver.py", "mlx_blockers")}


def _external_planes_calls() -> list[tuple[str, str, ast.Call]]:
    found = []
    for path in sorted((_ROOT / "scripts").rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        if "/tests/" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "external_planes"):
                    found.append((rel, func.name, node))
    return found


def test_every_external_planes_call_passes_roles() -> None:
    calls = _external_planes_calls()
    assert len(calls) >= 10, calls  # 소비처가 사라지면 이 검사가 헛돈다
    missing = sorted(
        {(rel, fn) for rel, fn, call in calls
         if not any(k.arg == "roles" for k in call.keywords)} - _ROLELESS_ALLOWED
    )
    assert missing == [], f"roles= 없이 과금 판정하는 소비처(D-127 우회): {missing}"


def test_billing_sets_defined_only_in_preflight() -> None:
    owners = []
    for base in ("src", "scripts", "noise_gate"):
        for path in (_ROOT / base).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "WORKER_INTERNAL_PROVIDERS =" in text or "def external_planes" in text:
                owners.append(path.relative_to(_ROOT).as_posix())
    assert owners == ["scripts/scenario/preflight.py"]


# ── ⑤ 기존 결함 기록 — WORKER_PROVIDER_OVERRIDE ──────────────────────


def test_worker_provider_override_gemini_is_billed() -> None:
    from scripts.scenario.preflight import _plane_provider, external_planes, role_providers

    cfg = AppConfig(
        llm=LLMConfig(_env_file=None, provider="mlx", **_EMPTY_ROLES),
        orchestrator=OrchestratorConfig(_env_file=None, provider="mlx", base_url="", model="m"),
    )
    cfg.worker_provider_override = "gemini"
    assert external_planes(_plane_provider(cfg.llm), "mlx", roles=role_providers(cfg))
