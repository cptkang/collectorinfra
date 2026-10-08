"""테스트 C — 소비자 종단: 본체 실제 조립 함수가 fabrix_proxy를 오케스트레이터로 쓴다 (plans/148).

프록시(`python -m fabrix_proxy`)와 가짜 KBGenAI(`fabrix_proxy/scripts/fake_kbgenai.py`)를 하위
프로세스로 띄운다. 이 파일은 `fabrix_proxy`를 import하지 않는다(양방향 import 0 경계 유지 —
HTTP 계약만 쓴다). 응답은 `fabrix_proxy/testdata/scenarios/contract_script.json` 대본이 정한다
— 실 LLM·Gemini 호출 0건(오케스트레이터 provider는 `vllm` + 프록시 base_url 고정).

① deepagents `create_deep_agent`(모델 = ChatOpenAI→프록시) 미니 에이전트 완주
② `create_orchestrator_llm` + `orchestrator_available` + `bind_tools` 1회
③ 노이즈 게이트 트랙 B — `_select_backend` == "track_b" + 신호 도구 루프 1회
④ litellm `openai/<별칭>` 1회 왕복(미설치면 skip)

langchain-openai는 기본 async httpx 클라이언트를 프로세스 단위로 캐시한다 — 루프가 바뀌면
닫힌 루프에 묶인 연결을 재사용하므로 비동기 테스트는 모듈 루프 하나를 공유한다.
"""

from __future__ import annotations

import importlib.util
import json
import os
import select
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
PROXY_ROOT = REPO / "fabrix_proxy"
FAKE_SCRIPT = PROXY_ROOT / "scripts" / "fake_kbgenai.py"
SCENARIO = PROXY_ROOT / "testdata" / "scenarios" / "contract_script.json"

TOKEN = "poc-consumer-token"
ALIAS = "fabrix-tools"
FINAL_ANSWER = "Final answer: Paris is 21 degrees and clear."

module_loop = pytest.mark.asyncio(loop_scope="module")


def _missing(*modules: str) -> list[str]:
    return [m for m in modules if importlib.util.find_spec(m) is None]


def _skip_if_missing(*modules: str) -> None:
    missing = _missing(*modules)
    if missing:
        pytest.skip(f"미설치: {', '.join(missing)}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _child_env(extra: dict[str, str]) -> dict[str, str]:
    """부모 환경에서 `FABRIX_*`를 걷어 내고(누수 방지) 지정 값만 싣는다."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("FABRIX_")}
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    env.update(extra)
    return env


def _stop(proc: subprocess.Popen[Any]) -> None:
    """자기 PID만 종료한다."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture(scope="module")
def proxy_v1(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """가짜 KBGenAI + 프록시를 띄우고 프록시 `/v1` URL을 준다(모듈 1회)."""
    missing = _missing("fastapi", "uvicorn", "jsonschema", "pydantic_settings")
    if missing:
        pytest.skip(f"프록시 의존 미설치({', '.join(missing)}) — 프록시 기동 불가")
    work = tmp_path_factory.mktemp("fabrix_proxy_poc")
    procs: list[subprocess.Popen[Any]] = []
    # 이 모듈 동안만 127.0.0.1을 프록시 우회 대상에 둔다(사내 HTTP_PROXY 환경 대비).
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("NO_PROXY", "127.0.0.1,localhost")
        mp.setenv("no_proxy", "127.0.0.1,localhost")
        try:
            fake = subprocess.Popen(
                [sys.executable, str(FAKE_SCRIPT), "--host", "127.0.0.1", "--port", "0",
                 "--script", str(SCENARIO)],
                cwd=PROXY_ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, env=_child_env({}),
            )
            procs.append(fake)
            assert fake.stdout is not None
            ready, _, _ = select.select([fake.stdout], [], [], 20)
            line = fake.stdout.readline().strip() if ready else ""
            if not line.startswith("FAKE_KBGENAI_READY port="):
                pytest.fail(f"가짜 KBGenAI 기동 실패: {line!r}")
            fake_port = int(line.split("=", 1)[1])

            port = _free_port()
            log = work / "proxy.log"
            env = _child_env({
                "FABRIX_PROXY_TOKEN": TOKEN,
                "FABRIX_PROXY_LOG_LEVEL": "WARNING",
                "FABRIX_PROXY_MODEL_ALIASES": json.dumps([ALIAS]),
                "FABRIX_BASE_URL": f"http://127.0.0.1:{fake_port}/kbgenai/chat",
                "FABRIX_API_KEY": "fake-upstream-api-key",
                "FABRIX_CLIENT_KEY": "fake-upstream-client-key",
                "FABRIX_MODEL": "fake-asset-001",
                "FABRIX_TOTAL_TIMEOUT": "30",
                "FABRIX_LLM_CONFIG": "{}",
                "FABRIX_PROXY_CONTENTS_MODE": "turns",
                "FABRIX_PROXY_PROTOCOL_LANG": "en",
                "FABRIX_PROXY_PROTOCOL_FILE": "",
                "FABRIX_PROXY_FEWSHOT": "static",
                "FABRIX_PROXY_FEWSHOT_PLACEMENT": "system",
                "FABRIX_PROXY_FEWSHOT_FILE": "",
                "FABRIX_PROXY_REPAIR_MAX": "1",
                "FABRIX_PROXY_PASSTHROUGH": "false",
                "FABRIX_PROXY_POC_MODE": "false",
            })
            with log.open("w", encoding="utf-8") as fh:
                proxy = subprocess.Popen(
                    [sys.executable, "-m", "fabrix_proxy", "--host", "127.0.0.1",
                     "--port", str(port)],
                    cwd=PROXY_ROOT, stdout=fh, stderr=subprocess.STDOUT, env=env,
                )
            procs.append(proxy)
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 30
            with httpx.Client(trust_env=False) as client:
                while True:
                    if proxy.poll() is not None:
                        tail = log.read_text(encoding="utf-8", errors="replace")[-2000:]
                        pytest.fail(f"프록시가 기동 중 종료(rc={proxy.returncode}):\n{tail}")
                    try:
                        if client.get(base + "/health", timeout=1).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        pytest.fail("프록시 /health 대기 시간 초과")
                    time.sleep(0.1)
            yield base + "/v1"
        finally:
            for proc in reversed(procs):
                _stop(proc)


def _app_config(v1: str) -> Any:
    """본체 AppConfig — 검증 대상 필드를 명시해 `.env` 누수를 막는다(provider는 vllm 고정)."""
    from src.config import AppConfig, LLMConfig, OrchestratorConfig

    orch = OrchestratorConfig(
        _env_file=None, provider="vllm", base_url=v1, model=ALIAS, api_key=TOKEN,
        timeout=30, health_timeout=3, verify_ssl=True, enable_thinking=False,
    )
    cfg = AppConfig(
        _env_file=None,
        llm=LLMConfig(_env_file=None, provider="ollama", gemini_api_key=""),
        orchestrator=orch,
        enable_semantic_routing=False,
    )
    assert cfg.orchestrator.provider == "vllm" and cfg.orchestrator.base_url == v1
    return cfg


def _lookup_weather_tool() -> Any:
    from langchain_core.tools import tool

    @tool
    def lookup_weather(city: str, unit: str = "celsius") -> str:
        """Look up the current weather for a city."""
        return f"{city}: 21 degrees {unit}, clear"

    return lookup_weather


# ① deepagents 미니 에이전트 ────────────────────────────────────────────────


@module_loop
async def test_deepagents_mini_agent_completes(proxy_v1: str) -> None:
    _skip_if_missing("deepagents", "langchain_openai")
    from deepagents import create_deep_agent
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    model = ChatOpenAI(
        base_url=proxy_v1, api_key=SecretStr(TOKEN), model=ALIAS, max_retries=0, timeout=30,
        http_async_client=httpx.AsyncClient(trust_env=False),
    )
    agent = create_deep_agent(
        model=model, tools=[_lookup_weather_tool()],
        system_prompt="You are a helpful assistant. Use tools when needed.",
    )
    result = await agent.ainvoke({"messages": [{"role": "user", "content": "weather in Paris?"}]})
    messages = result["messages"]
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    assert [m.name for m in tool_msgs] == ["lookup_weather"]
    assert "21 degrees" in str(tool_msgs[0].content)
    last = messages[-1]
    assert isinstance(last, AIMessage) and not last.tool_calls
    assert last.content == FINAL_ANSWER
    await model.http_async_client.aclose()


# ② 본체 오케스트레이터 조립 ────────────────────────────────────────────────


@module_loop
async def test_body_orchestrator_llm_via_proxy(proxy_v1: str) -> None:
    _skip_if_missing("langchain_openai")
    from langchain_core.messages import HumanMessage

    from src.llm import create_orchestrator_llm
    from src.orchestration.deep_agent import orchestrator_available

    cfg = _app_config(proxy_v1)
    # 무인증 `/v1/models` 가용성 판정
    assert orchestrator_available(cfg) is True
    llm = create_orchestrator_llm(cfg)
    bound = llm.bind_tools([_lookup_weather_tool()])
    ai = await bound.ainvoke([HumanMessage(content="weather in Paris?")])
    assert [tc["name"] for tc in ai.tool_calls] == ["lookup_weather"]
    assert ai.tool_calls[0]["args"] == {"city": "Paris"}


# ③ 노이즈 게이트 트랙 B ────────────────────────────────────────────────────


class _FakeNoiseRepo:
    """저장소 의존 대체 — 고정 컨텍스트를 돌려준다(DB 접근 없음)."""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def fetch(self, event: Any, collect_dependency: bool = False) -> dict[str, Any]:
        self.calls.append(collect_dependency)
        return {"importance": "low", "maintenance": False}


@module_loop
async def test_noise_track_b_loop_via_proxy(proxy_v1: str) -> None:
    _skip_if_missing("langchain_openai")
    from noise_gate.application.nodes.agentic_enricher import _build_user_prompt, _select_backend
    from noise_gate.infrastructure.noise_signal_tools import (
        build_noise_enricher_backend,
        build_noise_signal_tools,
        run_signal_react_loop,
    )
    from noise_gate.prompts.agentic_enricher import AGENTIC_ENRICHER_SYSTEM_PROMPT

    cfg = _app_config(proxy_v1)
    gate_cfg = SimpleNamespace(agentic_enricher_fallback="deterministic_only")
    assert _select_backend(gate_cfg, cfg) == "track_b"

    repo = _FakeNoiseRepo()
    event = SimpleNamespace(
        condition_log="sample message line", alarm_name="sample alarm",
        server_name="host-a", resource_type="log", severity=2,
    )
    collector: list[dict[str, Any]] = []
    tools = build_noise_signal_tools(repo, event, collect_dependency=True, collector=collector)
    bound = build_noise_enricher_backend(cfg, tools)
    calls = await run_signal_react_loop(
        bound, tools, system_prompt=AGENTIC_ENRICHER_SYSTEM_PROMPT,
        user_prompt=_build_user_prompt(event), max_tool_calls=2,
    )
    # 대본: 병렬 2건(scan_message_signature · collect_importance_maintenance)
    assert calls == 2
    assert [c["signal"] for c in collector] == ["message_signature", "importance_maintenance"]
    assert repo.calls == [False]


# ④ litellm ────────────────────────────────────────────────────────────────


@module_loop
async def test_litellm_openai_provider_round_trip(proxy_v1: str) -> None:
    if importlib.util.find_spec("litellm") is None:
        pytest.skip("litellm 미설치 — sre_agent/.venv 전용 의존이라 루트 venv에서는 생략한다")
    # litellm은 import 시 비용표를 외부(GitHub)에서 받아 온다 — 로컬 사본만 쓰게 해 외부 호출을
    # 막는다(설정 판단이 아니라 외부 호출 차단 목적이다).
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    resp = await litellm.acompletion(
        model=f"openai/{ALIAS}", api_base=proxy_v1, api_key=TOKEN, num_retries=0,
        messages=[{"role": "user", "content": "weather in Paris?"}],
        tools=[{
            "type": "function",
            "function": {
                "name": "lookup_weather",
                "description": "Look up the current weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }],
    )
    calls = resp.choices[0].message.tool_calls
    assert calls and calls[0].function.name == "lookup_weather"
    assert json.loads(calls[0].function.arguments) == {"city": "Paris"}
