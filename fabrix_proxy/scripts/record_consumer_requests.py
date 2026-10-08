#!/usr/bin/env python3
"""OpenAI 호환 소비자 요청 녹화 하네스 (plans/148 1단계 W1 작업 1-1).

실제 소비자(openai SDK · LangChain `ChatOpenAI.bind_tools` · deepagents · 본체 트랙 B)가
OpenAI 호환 서버에 **실제로 보내는 요청 바디**를 가짜 서버로 녹화한다. 프록시가 받을 필드와
무시할 필드를 확정하고, W3의 규약 블록 비용 측정·내부망 env-check(녹화 필드 vs 설치 버전 필드
대조)에 재사용한다.

- 실 LLM 호출 0건 — 모든 소비자는 하네스가 띄운 127.0.0.1 가짜 서버만 가리킨다.
- 저장 JSON에는 헤더 **이름**만 남긴다(Authorization·api_key 값 금지).
- 경계: 이 모듈은 `src`·`noise_gate`를 import하지 않는다. 본체 도구 덤프·트랙 B 녹화
  (`--include-body-tools`, 개발 맥 전용)는 저장소 루트를 cwd로 한 하위 프로세스에서 돈다.

    python fabrix_proxy/scripts/record_consumer_requests.py [--out DIR] \
        [--include-body-tools] [--only 소비자,…]
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

# FastAPI는 핸들러 주석을 모듈 전역에서 해석한다(`from __future__ import annotations`) —
# 함수 안 import면 Request가 쿼리 파라미터로 오인되므로 모듈 최상위에서 가져온다.
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = SCRIPT_DIR.parent
REPO_ROOT = PACKAGE_DIR.parent
DEFAULT_OUT = PACKAGE_DIR / "testdata" / "consumer_requests"

FAKE_MODEL = "fabrix-tools"
QWEN_MODEL = "Qwen3.5-9B"
DUMMY_KEY = "dummy"
FINAL_TEXT = "scripted final answer"
ABSENT = "<absent>"
RESULT_MARKER = "__RECORD_RESULT__"

# 버전 기록 대상 배포 패키지
VERSION_DISTS = (
    "openai", "langchain-openai", "langchain-core", "langchain", "langgraph",
    "deepagents", "httpx", "litellm",
)

# OpenAI Chat Completions 표준 요청 파라미터 — 이 밖의 최상위 키는 확장(extra_body류)으로 본다
STANDARD_KEYS = frozenset({
    "messages", "model", "audio", "frequency_penalty", "function_call", "functions",
    "logit_bias", "logprobs", "max_completion_tokens", "max_tokens", "metadata", "modalities",
    "n", "parallel_tool_calls", "prediction", "presence_penalty", "prompt_cache_key",
    "reasoning_effort", "response_format", "safety_identifier", "seed", "service_tier",
    "stop", "store", "stream", "stream_options", "temperature", "tool_choice", "tools",
    "top_logprobs", "top_p", "user", "verbosity", "web_search_options",
})

# 업무 무관 합성 도구(OpenAI 도구 JSON) — 가짜 서버는 이 이름을 우선 호출한다
SYNTHETIC_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_weather",
            "description": "Look up the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Get the shipping status of an order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "include_items": {"type": "boolean"},
                },
                "required": ["order_id"],
            },
        },
    },
]
SYNTHETIC_TOOL_NAMES = tuple(t["function"]["name"] for t in SYNTHETIC_TOOLS)
USER_PROMPT = "What is the weather in Springfield right now?"


# ── 필드 요약 ─────────────────────────────────────────────────────────────


def _content_kind(content: Any) -> str:
    """메시지 content의 형태를 str/parts/null/기타로 분류한다."""
    if content is None:
        return "null"
    if isinstance(content, str):
        return "str"
    if isinstance(content, list):
        return "parts"
    return type(content).__name__


def field_signature(body: dict[str, Any]) -> dict[str, Any]:
    """Chat Completions 요청 바디 1건의 필드 서명을 만든다(W3 env-check 재사용).

    값 자체가 아니라 프록시 수신 규약에 영향을 주는 모양만 남긴다 — 최상위 키 집합,
    샘플링·스트림 파라미터 값, 확장 키, 메시지 역할·키·content 형태, tools 항목 type.

    Args:
        body: `/v1/chat/completions` 요청 JSON

    Returns:
        JSON 직렬화 가능한 서명 dict
    """
    messages = body.get("messages") or []
    roles: set[str] = set()
    keys_by_role: dict[str, set[str]] = {}
    content_kinds: dict[str, set[str]] = {}
    part_types: set[str] = set()
    tool_call_keys: set[str] = set()
    tool_call_function_keys: set[str] = set()
    tool_call_arg_kinds: set[str] = set()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role"))
        roles.add(role)
        keys_by_role.setdefault(role, set()).update(msg.keys())
        content = msg.get("content")
        content_kinds.setdefault(role, set()).add(_content_kind(content))
        if isinstance(content, list):
            part_types.update(str(p.get("type")) for p in content if isinstance(p, dict))
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tool_call_keys.update(tc.keys())
            fn = tc.get("function") or {}
            tool_call_function_keys.update(fn.keys())
            tool_call_arg_kinds.add(type(fn.get("arguments")).__name__)
    tools = body.get("tools") or []
    return {
        "top_level_keys": sorted(body.keys()),
        "nonstandard_keys": sorted(k for k in body if k not in STANDARD_KEYS),
        "model": body.get("model", ABSENT),
        "tool_choice": body.get("tool_choice", ABSENT),
        "parallel_tool_calls": body.get("parallel_tool_calls", ABSENT),
        "stream": body.get("stream", ABSENT),
        "stream_options": body.get("stream_options", ABSENT),
        "max_tokens": body.get("max_tokens", ABSENT),
        "max_completion_tokens": body.get("max_completion_tokens", ABSENT),
        "temperature": body.get("temperature", ABSENT),
        "top_p": body.get("top_p", ABSENT),
        "extra_body": body.get("extra_body", ABSENT),
        "chat_template_kwargs": body.get("chat_template_kwargs", ABSENT),
        "message_count": len(messages),
        "message_roles": sorted(roles),
        "message_keys_by_role": {r: sorted(k) for r, k in sorted(keys_by_role.items())},
        "content_kinds_by_role": {r: sorted(k) for r, k in sorted(content_kinds.items())},
        "content_part_types": sorted(part_types),
        "assistant_tool_call_keys": sorted(tool_call_keys),
        "assistant_tool_call_function_keys": sorted(tool_call_function_keys),
        "assistant_tool_call_argument_types": sorted(tool_call_arg_kinds),
        "tool_count": len(tools),
        "tool_types": sorted({str(t.get("type")) for t in tools if isinstance(t, dict)}),
        "tool_names": [
            str((t.get("function") or {}).get("name")) for t in tools if isinstance(t, dict)
        ],
    }


# ── 가짜 OpenAI 서버 ─────────────────────────────────────────────────────


class Recorder:
    """가짜 서버가 받은 요청을 현재 케이스 라벨과 함께 적재한다(스레드 안전)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        self._case = ""
        self._call_seq = 0

    def set_case(self, case: str) -> None:
        """이후 요청을 귀속할 케이스 라벨을 정한다."""
        with self._lock:
            self._case = case

    def add(self, method: str, path: str, header_names: list[str], body: Any) -> None:
        """요청 1건을 적재한다(헤더는 이름만)."""
        with self._lock:
            self._records.append({
                "seq": len(self._records) + 1,
                "case": self._case,
                "method": method,
                "path": path,
                "header_names": header_names,
                "body": body,
            })

    def next_call_id(self) -> str:
        """결정적 tool_call id를 발급한다(녹화본 재현성)."""
        with self._lock:
            self._call_seq += 1
            return f"call_{self._call_seq:04d}"

    def drain(self) -> list[dict[str, Any]]:
        """적재분을 꺼내고 비운다(소비자 1개 단위)."""
        with self._lock:
            out, self._records = self._records, []
            self._call_seq = 0
            return out


def _synth_value(schema: dict[str, Any]) -> Any:
    """JSON 스키마 1개에 맞는 최소 합성값을 만든다."""
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    if kind == "object":
        return _synth_args(schema)
    return "sample"


def _synth_args(parameters: dict[str, Any]) -> dict[str, Any]:
    """도구 parameters 스키마의 required 필드만 최소 합성값으로 채운다."""
    props = parameters.get("properties") or {}
    return {
        name: _synth_value(props.get(name) or {})
        for name in parameters.get("required") or []
    }


def _pick_tool(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """호출할 도구를 고른다 — 합성 도구 우선, 없으면 첫 번째."""
    for tool in tools:
        if (tool.get("function") or {}).get("name") in SYNTHETIC_TOOL_NAMES:
            return tool
    return tools[0]


def _scripted_reply(
    body: dict[str, Any], recorder: Recorder
) -> tuple[str | None, list[dict[str, Any]] | None]:
    """대본 응답 — 마지막이 tool이면 평문, 도구가 있으면 tool_calls 1건, 없으면 평문."""
    messages = body.get("messages") or []
    last_role = messages[-1].get("role") if messages else None
    tools = body.get("tools") or []
    if last_role == "tool" or not tools:
        return FINAL_TEXT, None
    fn = _pick_tool(tools).get("function") or {}
    args = _synth_args(fn.get("parameters") or {})
    return None, [{
        "id": recorder.next_call_id(),
        "type": "function",
        "function": {"name": fn.get("name", ""), "arguments": json.dumps(args)},
    }]


def _sse(payload: dict[str, Any]) -> str:
    """SSE data 줄 1개."""
    return f"data: {json.dumps(payload)}\n\n"


def _stream_chunks(
    body: dict[str, Any], content: str | None, tool_calls: list[dict[str, Any]] | None
) -> list[str]:
    """OpenAI SSE(`chat.completion.chunk`) 순서대로 청크 문자열을 만든다."""
    base = {
        "id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 1700000000,
        "model": body.get("model", FAKE_MODEL),
    }

    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        return _sse({**base, "choices": [
            {"index": 0, "delta": delta, "finish_reason": finish}
        ]})

    out = [chunk({"role": "assistant", "content": ""})]
    if tool_calls:
        for i, tc in enumerate(tool_calls):
            out.append(chunk({"tool_calls": [{
                "index": i, "id": tc["id"], "type": "function",
                "function": {"name": tc["function"]["name"], "arguments": ""},
            }]}))
            out.append(chunk({"tool_calls": [{
                "index": i, "function": {"arguments": tc["function"]["arguments"]},
            }]}))
        out.append(chunk({}, "tool_calls"))
    else:
        out.append(chunk({"content": content or ""}))
        out.append(chunk({}, "stop"))
    if (body.get("stream_options") or {}).get("include_usage"):
        out.append(_sse({**base, "choices": [], "usage": {
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
        }}))
    out.append("data: [DONE]\n\n")
    return out


def _build_app(recorder: Recorder) -> FastAPI:
    """녹화용 FastAPI 앱을 만든다."""
    app = FastAPI()

    def header_names(request: Request) -> list[str]:
        return sorted({k.lower() for k in request.headers.keys()})

    @app.get("/v1/models")
    async def models(request: Request) -> JSONResponse:
        recorder.add("GET", request.url.path, header_names(request), None)
        return JSONResponse({"object": "list", "data": [
            {"id": FAKE_MODEL, "object": "model", "created": 1700000000, "owned_by": "fake"},
        ]})

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        body = await request.json()
        recorder.add("POST", request.url.path, header_names(request), body)
        content, tool_calls = _scripted_reply(body, recorder)
        if body.get("stream"):
            chunks = _stream_chunks(body, content, tool_calls)

            async def gen() -> AsyncIterator[str]:
                for c in chunks:
                    yield c

            return StreamingResponse(gen(), media_type="text/event-stream")
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return JSONResponse({
            "id": "chatcmpl-fake", "object": "chat.completion", "created": 1700000000,
            "model": body.get("model", FAKE_MODEL),
            "choices": [{
                "index": 0, "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def other(path: str, request: Request) -> JSONResponse:
        raw = await request.body()
        try:
            body: Any = json.loads(raw) if raw else None
        except ValueError:
            body = {"_raw_len": len(raw)}
        recorder.add(request.method, request.url.path, header_names(request), body)
        return JSONResponse({"error": {"message": "not recorded route"}}, status_code=404)

    return app


class FakeServer:
    """uvicorn을 데몬 스레드로 127.0.0.1 빈 포트에 띄운다."""

    def __init__(self, recorder: Recorder) -> None:
        import uvicorn

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        config = uvicorn.Config(
            _build_app(recorder), host="127.0.0.1", port=self.port, log_level="warning",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def base_url(self) -> str:
        """OpenAI 호환 /v1 엔드포인트."""
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> FakeServer:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("가짜 서버 기동 시간 초과")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


# ── 소비자 ───────────────────────────────────────────────────────────────


def _chat_openai(base_url: str, model: str = FAKE_MODEL) -> Any:
    """가짜 서버를 가리키는 ChatOpenAI(재시도 0)."""
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    return ChatOpenAI(
        base_url=base_url, api_key=SecretStr(DUMMY_KEY), model=model, max_retries=0,
    )


def _lc_tools() -> list[Any]:
    """합성 도구의 LangChain 도구판."""
    from langchain_core.tools import tool

    @tool
    def lookup_weather(city: str, unit: str = "celsius") -> str:
        """Look up the current weather for a city."""
        return f"{city}: 21 degrees {unit}, clear"

    @tool
    def get_order_status(order_id: str, include_items: bool = False) -> str:
        """Get the shipping status of an order."""
        return f"order {order_id}: shipped"

    return [lookup_weather, get_order_status]


async def _openai_sdk(base_url: str, rec: Recorder, *, stream: bool) -> None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key=DUMMY_KEY, max_retries=0)
    messages: list[dict[str, Any]] = [{"role": "user", "content": USER_PROMPT}]
    rec.set_case("tool_call_turn")
    if stream:
        resp: Any = await client.chat.completions.create(
            model=FAKE_MODEL, messages=messages, tools=SYNTHETIC_TOOLS, stream=True,  # type: ignore[arg-type]
        )
        async for _ in resp:
            pass
    else:
        await client.chat.completions.create(
            model=FAKE_MODEL, messages=messages, tools=SYNTHETIC_TOOLS,  # type: ignore[arg-type]
        )
    await client.close()


async def _run_openai_sdk_nonstream(base_url: str, rec: Recorder) -> None:
    await _openai_sdk(base_url, rec, stream=False)


async def _run_openai_sdk_stream(base_url: str, rec: Recorder) -> None:
    await _openai_sdk(base_url, rec, stream=True)


async def _lc_tool_round(bound: Any, rec: Recorder, *, use_stream: bool) -> None:
    """bind_tools LLM으로 도구 호출 → ToolMessage → 최종 답 2턴을 돈다."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    messages: list[Any] = [HumanMessage(content=USER_PROMPT)]
    rec.set_case("turn1_tool_call")
    ai: Any
    if use_stream:
        ai = None
        async for chunk in bound.astream(messages):
            ai = chunk if ai is None else ai + chunk
    else:
        ai = await bound.ainvoke(messages)
    ai_msg = AIMessage(content=ai.content, tool_calls=ai.tool_calls)
    messages.append(ai_msg)
    for tc in ai_msg.tool_calls:
        messages.append(ToolMessage(content="21 degrees, clear", tool_call_id=tc["id"]))
    rec.set_case("turn2_after_tool")
    if use_stream:
        async for _ in bound.astream(messages):
            pass
    else:
        await bound.ainvoke(messages)


async def _run_chatopenai_ainvoke(base_url: str, rec: Recorder) -> None:
    bound = _chat_openai(base_url).bind_tools(_lc_tools())
    await _lc_tool_round(bound, rec, use_stream=False)


async def _run_chatopenai_astream(base_url: str, rec: Recorder) -> None:
    bound = _chat_openai(base_url).bind_tools(_lc_tools())
    await _lc_tool_round(bound, rec, use_stream=True)


async def _run_chatopenai_astream_events(base_url: str, rec: Recorder) -> None:
    """astream_events로 감쌌을 때 내부 ainvoke가 stream:true로 나가는지 확인한다."""
    from langchain_core.messages import HumanMessage
    from langchain_core.runnables import RunnableLambda

    bound = _chat_openai(base_url).bind_tools(_lc_tools())
    messages = [HumanMessage(content=USER_PROMPT)]

    async def call_ainvoke(msgs: list[Any]) -> Any:
        return await bound.ainvoke(msgs)

    rec.set_case("control_plain_ainvoke")
    await bound.ainvoke(messages)
    rec.set_case("ainvoke_wrapped_by_astream_events")
    async for _ in RunnableLambda(call_ainvoke).astream_events(messages, version="v2"):
        pass
    rec.set_case("direct_astream_events")
    async for _ in bound.astream_events(messages, version="v2"):
        pass


async def _run_deepagents(base_url: str, rec: Recorder) -> None:
    """deepagents 미니 에이전트 1회 완주(도구 호출 → 결과 → 최종 답)."""
    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=_chat_openai(base_url),
        tools=_lc_tools()[:1],
        system_prompt="You are a helpful assistant. Use tools when needed.",
    )
    rec.set_case("agent_ainvoke")
    await agent.ainvoke({"messages": [{"role": "user", "content": USER_PROMPT}]})


async def _run_litellm(base_url: str, rec: Recorder) -> None:
    # litellm은 import 시 비용표를 외부(GitHub)에서 받아 온다 — 로컬 사본만 쓰게 해 외부 호출을
    # 막는다(설정 판단이 아니라 외부 호출 차단 목적이다).
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    litellm = importlib.import_module("litellm")
    rec.set_case("acompletion_openai_provider")
    await litellm.acompletion(
        model=f"openai/{FAKE_MODEL}", api_base=base_url, api_key=DUMMY_KEY,
        messages=[{"role": "user", "content": USER_PROMPT}], tools=SYNTHETIC_TOOLS,
        num_retries=0,
    )


Runner = Callable[[str, Recorder], Awaitable[None]]


@dataclass(frozen=True)
class Consumer:
    """녹화 대상 소비자 1개."""

    name: str
    requires: tuple[str, ...]  # import 가능해야 하는 모듈
    description: str
    run: Runner


CONSUMERS: tuple[Consumer, ...] = (
    Consumer("openai_sdk_nonstream", ("openai",),
             "openai AsyncOpenAI chat.completions.create(tools=…) 비스트림",
             _run_openai_sdk_nonstream),
    Consumer("openai_sdk_stream", ("openai",),
             "openai AsyncOpenAI chat.completions.create(tools=…, stream=True)",
             _run_openai_sdk_stream),
    Consumer("chatopenai_bind_tools_ainvoke", ("langchain_openai",),
             "ChatOpenAI.bind_tools(…).ainvoke 2턴(도구 호출 → ToolMessage → 최종)",
             _run_chatopenai_ainvoke),
    Consumer("chatopenai_bind_tools_astream", ("langchain_openai",),
             "ChatOpenAI.bind_tools(…).astream 2턴", _run_chatopenai_astream),
    Consumer("chatopenai_astream_events", ("langchain_openai",),
             "대조 ainvoke · astream_events가 감싼 ainvoke · 직접 astream_events",
             _run_chatopenai_astream_events),
    Consumer("deepagents_mini_agent", ("deepagents", "langchain_openai"),
             "create_deep_agent(model=ChatOpenAI, tools=[합성 1개]).ainvoke 완주", _run_deepagents),
    Consumer("litellm_openai", ("litellm",),
             "litellm.acompletion(model='openai/…', api_base=가짜, tools=…)", _run_litellm),
)


# ── 본체 도구 덤프·트랙 B 녹화(하위 프로세스 — fabrix_proxy는 src·noise_gate import 금지) ──

_DEEP_AGENT_TOOLS_CODE = f"""
import json
from langchain_core.utils.function_calling import convert_to_openai_tool
from src.config import AppConfig, OrchestratorConfig
from src.orchestration.deepagents_tools import build_tools
orch = OrchestratorConfig(_env_file=None, provider="vllm", base_url="http://127.0.0.1:9/v1",
                          model="{FAKE_MODEL}", api_key="{DUMMY_KEY}", verify_ssl=True)
cfg = AppConfig(orchestrator=orch)
# 도구 조립만 한다(실행 없음) — worker_llm은 도구 실행 시에만 쓰이므로 None.
tools = build_tools(None, cfg, {{}}, collector=[])
print("{RESULT_MARKER}" + json.dumps([convert_to_openai_tool(t) for t in tools]))
"""

_NOISE_TOOLS_CODE = f"""
import json
from types import SimpleNamespace
from langchain_core.utils.function_calling import convert_to_openai_tool
from noise_gate.infrastructure.noise_signal_tools import build_noise_signal_tools
event = SimpleNamespace(condition_log="sample log line", alarm_name="sample alarm",
                        server_name="host-a", resource_type="log", severity=2)
# 운영 트랙 B와 같이 collect_dependency=True(agentic_enricher._collect_track_b).
tools = build_noise_signal_tools(None, event, collect_dependency=True, collector=[])
print("{RESULT_MARKER}" + json.dumps([convert_to_openai_tool(t) for t in tools]))
"""

_NOISE_TRACK_B_CODE = f"""
import asyncio, json, sys
from types import SimpleNamespace
from src.config import AppConfig, OrchestratorConfig
from noise_gate.infrastructure.noise_signal_tools import (
    build_noise_enricher_backend, build_noise_signal_tools, run_signal_react_loop)
from noise_gate.application.nodes.agentic_enricher import _build_user_prompt
from noise_gate.prompts.agentic_enricher import AGENTIC_ENRICHER_SYSTEM_PROMPT
base_url, model = sys.argv[1], sys.argv[2]
# provider는 vllm + 가짜 base_url 고정, 검증 대상 필드는 명시(.env 누수 방지).
orch = OrchestratorConfig(_env_file=None, provider="vllm", base_url=base_url, model=model,
                          api_key="{DUMMY_KEY}", timeout=30, verify_ssl=True,
                          enable_thinking=False)
cfg = AppConfig(orchestrator=orch)
assert cfg.orchestrator.provider == "vllm" and cfg.orchestrator.base_url == base_url
event = SimpleNamespace(condition_log="kernel: sample error line", alarm_name="sample alarm",
                        server_name="host-a", resource_type="log", severity=2)
tools = build_noise_signal_tools(None, event, collect_dependency=True, collector=[])
bound = build_noise_enricher_backend(cfg, tools)
calls = asyncio.run(run_signal_react_loop(
    bound, tools, system_prompt=AGENTIC_ENRICHER_SYSTEM_PROMPT,
    user_prompt=_build_user_prompt(event), max_tool_calls=2))
print("{RESULT_MARKER}" + json.dumps({{"tool_calls_executed": calls}}))
"""


def _run_body_code(code: str, *args: str) -> Any:
    """저장소 루트를 cwd로 본체 코드를 하위 프로세스에서 돌리고 결과 JSON을 받는다."""
    proc = subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300, check=False,
    )
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER):])
    tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
    raise RuntimeError(f"하위 프로세스 실패(rc={proc.returncode}):\n{tail}")


def _versions() -> dict[str, str]:
    """설치 버전 실측(미설치는 'not installed')."""
    out = {"python": sys.version.split()[0]}
    for dist in VERSION_DISTS:
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = "not installed"
    return out


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _signatures(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """chat/completions 요청별 서명(그 외 경로는 경로만)."""
    sigs = []
    for r in records:
        entry: dict[str, Any] = {"seq": r["seq"], "case": r["case"], "path": r["path"]}
        if isinstance(r["body"], dict) and r["path"].endswith("/chat/completions"):
            entry["signature"] = field_signature(r["body"])
        sigs.append(entry)
    return sigs


async def _record(
    rec: Recorder, name: str, description: str, run: Callable[[], Awaitable[None]],
    out: Path, summary: dict[str, Any], versions: dict[str, str],
) -> None:
    """소비자 1개를 돌려 녹화 파일과 요약 항목을 남긴다."""
    rec.drain()
    error = ""
    try:
        await run()
    except Exception as e:  # noqa: BLE001 — 소비자 실패는 기록하고 다음 소비자로 진행
        error = f"{type(e).__name__}: {e}"
    records = rec.drain()
    _write_json(out / f"{name}.json", {
        "consumer": name, "description": description,
        "fake_base_url": "http://127.0.0.1:<port>/v1",
        "versions": versions, "error": error or None, "requests": records,
    })
    summary[name] = {
        "status": "error" if error else "recorded", "error": error or None,
        "file": f"{name}.json", "request_count": len(records),
        "requests": _signatures(records),
    }
    print(f"[{'ERROR' if error else 'OK'}] {name}: 요청 {len(records)}건"
          + (f" — {error}" if error else ""))


async def _record_all(
    server: FakeServer, rec: Recorder, out: Path, only: set[str],
    include_body_tools: bool, summary: dict[str, Any], versions: dict[str, str],
) -> None:
    """모든 소비자를 **한 이벤트 루프**에서 녹화한다.

    langchain-openai는 기본 async httpx 클라이언트를 모듈 단위로 캐시한다 — 소비자마다
    `asyncio.run`으로 루프를 새로 만들면 닫힌 루프에 묶인 연결을 재사용해 Connection error가 난다.
    """
    for c in CONSUMERS:
        if only and c.name not in only:
            continue
        missing = [m for m in c.requires if importlib.util.find_spec(m) is None]
        if missing:
            reason = f"미설치: {', '.join(missing)}"
            summary[c.name] = {"status": "skipped", "reason": reason}
            print(f"[SKIP] {c.name}: {reason}")
            continue

        async def run_consumer(c: Consumer = c) -> None:
            await c.run(server.base_url, rec)

        await _record(rec, c.name, c.description, run_consumer, out, summary, versions)

    if not include_body_tools:
        return
    if not only or "noise_track_b_bind_tools" in only:
        async def track_b() -> None:
            # 모델명 2종 — qwen 계열이면 본체가 extra_body.chat_template_kwargs를 붙인다.
            for model in (FAKE_MODEL, QWEN_MODEL):
                rec.set_case(f"react_loop_model={model}")
                await asyncio.to_thread(
                    _run_body_code, _NOISE_TRACK_B_CODE, server.base_url, model
                )

        await _record(
            rec, "noise_track_b_bind_tools",
            "noise_gate 트랙 B — create_orchestrator_llm(vllm)·bind_tools·"
            "run_signal_react_loop(하위 프로세스)", track_b, out, summary, versions,
        )
    for name, source, code in (
        ("body_tools_deep_agent",
         "src/orchestration/deepagents_tools.py:build_tools (1단 deep agent)",
         _DEEP_AGENT_TOOLS_CODE),
        ("body_tools_noise_track_b",
         "noise_gate/infrastructure/noise_signal_tools.py:build_noise_signal_tools "
         "(collect_dependency=True)", _NOISE_TOOLS_CODE),
    ):
        try:
            tools = await asyncio.to_thread(_run_body_code, code)
        except Exception as e:  # noqa: BLE001 — 덤프 실패는 기록만
            summary[name] = {"status": "error", "error": str(e)}
            print(f"[ERROR] {name}: {e}")
            continue
        _write_json(out / f"{name}.json", {"source": source, "tools": tools})
        summary[name] = {
            "status": "recorded", "file": f"{name}.json", "tool_count": len(tools),
            "tool_names": [t["function"]["name"] for t in tools],
            "schema_json_chars": len(json.dumps(tools, ensure_ascii=False)),
        }
        print(f"[OK] {name}: 도구 {len(tools)}개")


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="녹화 출력 디렉터리")
    parser.add_argument("--include-body-tools", action="store_true",
                        help="본체 도구 스키마 덤프·트랙 B 녹화(개발 맥 전용 — 하위 프로세스)")
    parser.add_argument("--only", default="", help="쉼표 구분 소비자 이름(기본 전부)")
    args = parser.parse_args(argv)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    unknown = only - {c.name for c in CONSUMERS} - {"noise_track_b_bind_tools"}
    if unknown:
        parser.error(f"알 수 없는 소비자: {sorted(unknown)}")
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    versions = _versions()
    summary: dict[str, Any] = {}
    rec = Recorder()

    with FakeServer(rec) as server:
        asyncio.run(_record_all(
            server, rec, out, only, args.include_body_tools, summary, versions,
        ))

    _write_json(out / "fields_summary.json", {"versions": versions, "consumers": summary})
    print(f"요약: {out / 'fields_summary.json'}")
    return 1 if any(v.get("status") == "error" for v in summary.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
