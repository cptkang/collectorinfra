"""테스트 B — 계약: 프록시 + 가짜 KBGenAI를 실프로세스로 띄우고 실 SDK로 부른다 (plans/148).

- 가짜 KBGenAI(`scripts/fake_kbgenai.py`)와 프록시(`python -m fabrix_proxy`)를 하위 프로세스로
  빈 포트에 띄운다. 설정은 환경변수로만 주입한다(`FABRIX_*`는 부모 환경에서 걷어 낸다).
- 응답은 `testdata/scenarios/contract_script.json` 대본이 정한다 — 실 LLM 호출 0건.
- openai SDK(비스트림·스트림) · `ChatOpenAI.bind_tools`(ainvoke·astream) · 오류 봉투 → SDK 예외
  매핑 · 업스트림 페이로드(규약 블록·few-shot·이력 직렬화)를 확인한다.
- 종료 시 이 모듈이 띄운 PID만 종료한다.
"""

from __future__ import annotations

import importlib.util
import json
import os
import select
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

openai = pytest.importorskip("openai", reason="openai 미설치 — 계약 테스트는 실 SDK가 필요하다")

PROXY_ROOT = Path(__file__).resolve().parents[1]  # fabrix_proxy/
FAKE_SCRIPT = PROXY_ROOT / "scripts" / "fake_kbgenai.py"
SCENARIO = PROXY_ROOT / "testdata" / "scenarios" / "contract_script.json"

TOKEN = "contract-proxy-token"
ALIAS = "fabrix-tools"
UPSTREAM_API_KEY = "fake-upstream-api-key"
UPSTREAM_CLIENT_KEY = "fake-upstream-client-key"
UPSTREAM_MODEL = "fake-asset-001"
TOTAL_TIMEOUT_S = 2  # 대본 CASE_SLOW 지연(4초)보다 짧게 — 504 경로

HAS_LANGCHAIN_OPENAI = importlib.util.find_spec("langchain_openai") is not None
needs_langchain = pytest.mark.skipif(
    not HAS_LANGCHAIN_OPENAI, reason="langchain-openai 미설치 — ChatOpenAI 계약 생략"
)

LOOKUP_WEATHER = {
    "type": "function",
    "function": {
        "name": "lookup_weather",
        "description": "Look up the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["city"],
        },
    },
}
ORDER_STATUS = {
    "type": "function",
    "function": {
        "name": "get_order_status",
        "description": "Get the shipping status of an order.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
}
TOOLS = [LOOKUP_WEATHER, ORDER_STATUS]


@dataclass
class Stack:
    """띄운 프로세스 묶음의 접속 정보."""

    proxy_url: str  # http://127.0.0.1:<port>
    record_dir: Path

    @property
    def v1(self) -> str:
        return self.proxy_url + "/v1"


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _child_env(extra: dict[str, str]) -> dict[str, str]:
    """부모 환경에서 `FABRIX_*`를 걷어 내고(누수 방지) 지정 값만 싣는다."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("FABRIX_")}
    no_proxy = "127.0.0.1,localhost"
    env["NO_PROXY"] = env["no_proxy"] = no_proxy
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


def _read_ready(proc: subprocess.Popen[str], timeout: float = 20.0) -> int:
    """가짜 서버 stdout 첫 줄 `FAKE_KBGENAI_READY port=<N>`에서 포트를 읽는다."""
    assert proc.stdout is not None
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        raise RuntimeError("가짜 KBGenAI READY 줄 대기 시간 초과")
    line = proc.stdout.readline().strip()
    if not line.startswith("FAKE_KBGENAI_READY port="):
        raise RuntimeError(f"가짜 KBGenAI 기동 실패: {line!r}")
    return int(line.split("=", 1)[1])


def _wait_health(url: str, proc: subprocess.Popen[Any], log: Path, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(trust_env=False) as client:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                tail = log.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise RuntimeError(f"프록시가 기동 중 종료(rc={proc.returncode}):\n{tail}")
            try:
                if client.get(url + "/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise RuntimeError("프록시 /health 대기 시간 초과")


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    """가짜 KBGenAI + 프록시를 하위 프로세스로 띄운다(모듈 1회)."""
    for mod in ("fastapi", "uvicorn"):
        if importlib.util.find_spec(mod) is None:
            pytest.skip(f"{mod} 미설치 — 프록시·가짜 KBGenAI 기동 불가")
    work = tmp_path_factory.mktemp("contract")
    record_dir = work / "records"
    procs: list[subprocess.Popen[Any]] = []
    try:
        fake = subprocess.Popen(
            [sys.executable, str(FAKE_SCRIPT), "--host", "127.0.0.1", "--port", "0",
             "--script", str(SCENARIO), "--record-dir", str(record_dir)],
            cwd=PROXY_ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=_child_env({}),
        )
        procs.append(fake)
        fake_port = _read_ready(fake)

        proxy_port = _free_port()
        proxy_log = work / "proxy.log"
        env = _child_env({
            "FABRIX_PROXY_TOKEN": TOKEN,
            "FABRIX_PROXY_HOST": "127.0.0.1",
            "FABRIX_PROXY_PORT": str(proxy_port),
            "FABRIX_PROXY_LOG_LEVEL": "WARNING",
            "FABRIX_PROXY_MODEL_ALIASES": json.dumps([ALIAS]),
            "FABRIX_BASE_URL": f"http://127.0.0.1:{fake_port}/kbgenai/v1/chat",
            "FABRIX_API_KEY": UPSTREAM_API_KEY,
            "FABRIX_CLIENT_KEY": UPSTREAM_CLIENT_KEY,
            "FABRIX_MODEL": UPSTREAM_MODEL,
            "FABRIX_VERIFY_SSL": "false",
            "FABRIX_TIMEOUT": "30",
            "FABRIX_TOTAL_TIMEOUT": str(TOTAL_TIMEOUT_S),
            "FABRIX_LLM_CONFIG": "{}",
            "FABRIX_NATIVE_URL": "",
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
        with proxy_log.open("w", encoding="utf-8") as log_fh:
            proxy = subprocess.Popen(
                [sys.executable, "-m", "fabrix_proxy", "--host", "127.0.0.1",
                 "--port", str(proxy_port)],
                cwd=PROXY_ROOT, stdout=log_fh, stderr=subprocess.STDOUT, env=env,
            )
        procs.append(proxy)
        url = f"http://127.0.0.1:{proxy_port}"
        _wait_health(url, proxy, proxy_log)
        yield Stack(proxy_url=url, record_dir=record_dir)
    finally:
        for proc in reversed(procs):
            _stop(proc)


def _client(stack: Stack, token: str = TOKEN) -> Any:
    return openai.OpenAI(
        base_url=stack.v1, api_key=token, max_retries=0, timeout=30,
        http_client=httpx.Client(trust_env=False),
    )


def _chat_openai(stack: Stack) -> Any:
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    return ChatOpenAI(
        base_url=stack.v1, api_key=SecretStr(TOKEN), model=ALIAS, max_retries=0, timeout=30,
        http_async_client=httpx.AsyncClient(trust_env=False),
    )


def _lc_tools() -> list[Any]:
    from langchain_core.tools import tool

    @tool
    def lookup_weather(city: str, unit: str = "celsius") -> str:
        """Look up the current weather for a city."""
        return f"{city}: 21 degrees {unit}, clear"

    return [lookup_weather]


def _find_record(stack: Stack, marker: str) -> dict[str, Any]:
    """contents에 marker가 든 첫 업스트림 요청 기록을 찾는다."""
    for path in sorted(stack.record_dir.glob("req_*.json"), key=lambda p: int(p.stem[4:])):
        record = json.loads(path.read_text(encoding="utf-8"))
        if any(marker in str(c) for c in record["payload"].get("contents", [])):
            return record
    raise AssertionError(f"marker {marker!r}가 든 업스트림 요청 기록이 없다")


# ─────────────────────────────── 표면 ───────────────────────────────


def test_models_without_auth(stack: Stack) -> None:
    with httpx.Client(trust_env=False) as client:
        resp = client.get(stack.v1 + "/models")
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()["data"]] == [ALIAS]


# ─────────────────────────────── openai SDK ───────────────────────────────


def test_openai_nonstream_tool_calls(stack: Stack) -> None:
    client = _client(stack)
    raw = client.chat.completions.with_raw_response.create(
        model=ALIAS, messages=[{"role": "user", "content": "weather in Paris? nonstream"}],
        tools=TOOLS,
    )
    assert raw.headers["x-proxy-tool-emulation"] == "parsed"
    resp = raw.parse()
    choice = resp.choices[0]
    assert choice.finish_reason == "tool_calls"
    calls = choice.message.tool_calls
    assert calls is not None and len(calls) == 1
    assert calls[0].id.startswith("call_")
    assert calls[0].function.name == "lookup_weather"
    assert json.loads(calls[0].function.arguments) == {"city": "Paris"}


def test_openai_stream_tool_calls_assembled(stack: Stack) -> None:
    client = _client(stack)
    stream = client.chat.completions.create(
        model=ALIAS, messages=[{"role": "user", "content": "weather in Paris? stream"}],
        tools=TOOLS, stream=True,
    )
    calls: dict[int, dict[str, str]] = {}
    finish = None
    for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        for delta in choice.delta.tool_calls or []:
            slot = calls.setdefault(delta.index, {"id": "", "name": "", "arguments": ""})
            if delta.id:
                slot["id"] = delta.id
            if delta.function and delta.function.name:
                slot["name"] += delta.function.name
            if delta.function and delta.function.arguments:
                slot["arguments"] += delta.function.arguments
        finish = choice.finish_reason or finish
    assert finish == "tool_calls"
    assert len(calls) == 1
    assert calls[0]["id"].startswith("call_")
    assert calls[0]["name"] == "lookup_weather"
    assert json.loads(calls[0]["arguments"]) == {"city": "Paris"}


@pytest.mark.parametrize("stream", [False, True])
def test_openai_plain_text(stack: Stack, stream: bool) -> None:
    client = _client(stack)
    messages = [{"role": "user", "content": "say hello CASE_PLAIN"}]
    if stream:
        text = ""
        finish = None
        for chunk in client.chat.completions.create(
            model=ALIAS, messages=messages, tools=TOOLS, stream=True
        ):
            if chunk.choices:
                text += chunk.choices[0].delta.content or ""
                assert not chunk.choices[0].delta.tool_calls
                finish = chunk.choices[0].finish_reason or finish
    else:
        resp = client.chat.completions.create(model=ALIAS, messages=messages, tools=TOOLS)
        assert resp.choices[0].message.tool_calls is None
        text, finish = resp.choices[0].message.content or "", resp.choices[0].finish_reason
    assert text.startswith("Plain answer #")
    assert finish == "stop"


def test_openai_tool_round_trip_payload(stack: Stack) -> None:
    """도구 결과 회신 턴 — 최종 평문 + 업스트림 contents에 이력이 규약 표기로 직렬화된다."""
    client = _client(stack)
    marker = "round-trip-marker-7f3"
    resp = client.chat.completions.create(
        model=ALIAS,
        messages=[
            {"role": "user", "content": f"weather in Paris? {marker}"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_abc123", "type": "function",
                "function": {"name": "lookup_weather", "arguments": '{"city": "Paris"}'},
            }]},
            {"role": "tool", "tool_call_id": "call_abc123", "content": "21 degrees, clear"},
        ],
        tools=TOOLS,
    )
    assert resp.choices[0].finish_reason == "stop"
    assert resp.choices[0].message.content == "Final answer: Paris is 21 degrees and clear."

    record = _find_record(stack, marker)
    contents = record["payload"]["contents"]
    joined = "\n".join(contents)
    assert "<tool_call>" in joined and "lookup_weather" in joined
    assert '<tool_response name="lookup_weather" id="call_abc123">' in joined
    assert "21 degrees, clear" in contents[-1]


def test_upstream_payload_carries_protocol_and_fewshot(stack: Stack) -> None:
    """업스트림 페이로드 계약 — 규약 블록 · few-shot · KBGenAI 고정 키 · 헤더 이름."""
    client = _client(stack)
    marker = "payload-marker-91c"
    client.chat.completions.create(
        model=ALIAS, messages=[
            {"role": "system", "content": "You are a test assistant."},
            {"role": "user", "content": f"weather in Paris? {marker}"},
        ], tools=TOOLS,
    )
    record = _find_record(stack, marker)
    payload = record["payload"]
    assert record["path"] == "/kbgenai/v1/chat"
    assert payload["modelId"] == UPSTREAM_MODEL
    assert payload["isStream"] is False
    assert payload["isRagOn"] is False
    system = payload["systemPrompt"]
    assert system.startswith("You are a test assistant.")
    # 도구 규약 블록 — 출력 표기와 요청 도구 목록
    assert "<tool_call>" in system
    assert "lookup_weather" in system and "get_order_status" in system
    # 정적 few-shot(system 배치) — 가상 예시 도구
    assert "# Format examples" in system
    assert "example_lookup_item" in system
    assert all(isinstance(c, str) for c in payload["contents"])
    # 헤더는 이름만 기록된다(값 없음) — 자격증명은 프록시 프로세스에만 있다
    assert {"x-openapi-token", "x-generative-ai-client"} <= set(record["header_names"])
    raw = json.dumps(record, ensure_ascii=False)
    assert UPSTREAM_API_KEY not in raw and UPSTREAM_CLIENT_KEY not in raw and TOKEN not in raw


# ─────────────────────────────── 오류 매핑 ───────────────────────────────


@pytest.mark.parametrize(
    ("prompt", "token", "model", "exc_name", "status", "code"),
    [
        ("hello", "wrong-token", ALIAS, "AuthenticationError", 401, "invalid_api_key"),
        ("hello", TOKEN, "unknown-alias", "BadRequestError", 400, "invalid_request"),
        ("hello CASE_INVALID", TOKEN, ALIAS, "InternalServerError", 502, "tool_call_invalid"),
        ("hello CASE_UPSTREAM_FAIL", TOKEN, ALIAS, "InternalServerError", 502, "upstream_error"),
        ("hello CASE_HTTP500", TOKEN, ALIAS, "InternalServerError", 502, "upstream_error"),
        ("hello CASE_SLOW", TOKEN, ALIAS, "InternalServerError", 504, "upstream_timeout"),
        ("hello CASE_PII", TOKEN, ALIAS, "BadRequestError", 400, "content_filter"),
        ("hello CASE_PII_HTTP", TOKEN, ALIAS, "BadRequestError", 400, "content_filter"),
    ],
)
def test_error_envelope_maps_to_sdk_exceptions(
    stack: Stack, prompt: str, token: str, model: str, exc_name: str, status: int, code: str
) -> None:
    client = _client(stack, token)
    with pytest.raises(getattr(openai, exc_name)) as info:
        client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}], tools=TOOLS
        )
    assert info.value.status_code == status
    assert info.value.code == code


def test_stream_error_raises_before_stream(stack: Stack) -> None:
    client = _client(stack)
    with pytest.raises(openai.InternalServerError) as info:
        client.chat.completions.create(
            model=ALIAS, messages=[{"role": "user", "content": "hello CASE_INVALID"}],
            tools=TOOLS, stream=True,
        )
    assert info.value.status_code == 502
    assert info.value.code == "tool_call_invalid"


# ─────────────────────────────── ChatOpenAI ───────────────────────────────


@needs_langchain
async def test_chatopenai_bind_tools_ainvoke_round(stack: Stack) -> None:
    from langchain_core.messages import HumanMessage, ToolMessage

    llm = _chat_openai(stack)
    bound = llm.bind_tools(_lc_tools())
    messages: list[Any] = [HumanMessage(content="weather in Paris? lc-ainvoke")]
    ai = await bound.ainvoke(messages)
    assert [tc["name"] for tc in ai.tool_calls] == ["lookup_weather"]
    assert ai.tool_calls[0]["args"] == {"city": "Paris"}
    assert ai.tool_calls[0]["id"].startswith("call_")
    messages += [ai, ToolMessage(content="21 degrees, clear", tool_call_id=ai.tool_calls[0]["id"])]
    final = await bound.ainvoke(messages)
    assert final.tool_calls == []
    assert final.content == "Final answer: Paris is 21 degrees and clear."
    await llm.http_async_client.aclose()


@needs_langchain
async def test_chatopenai_bind_tools_astream_chunks(stack: Stack) -> None:
    from langchain_core.messages import HumanMessage

    llm = _chat_openai(stack)
    bound = llm.bind_tools(_lc_tools())
    merged: Any = None
    async for chunk in bound.astream([HumanMessage(content="weather in Paris? lc-astream")]):
        merged = chunk if merged is None else merged + chunk
    assert merged is not None
    assert [tc["name"] for tc in merged.tool_calls] == ["lookup_weather"]
    assert merged.tool_calls[0]["args"] == {"city": "Paris"}
    await llm.http_async_client.aclose()
