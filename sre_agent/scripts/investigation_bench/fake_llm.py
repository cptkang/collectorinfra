"""가짜 LLM — OpenAI 호환 대본 재생기 (plans/151 §4.11 · 테스트 전용).

holmes(litellm `openai/…`)가 부르는 그대로 받아 **현재 세계의 대본**(`script:`)을 한 턴씩 내준다.
실측 회차(W5·W6·W8)에서는 띄우지 않는다 — 사전 점검이 `--test` 없이는 이 백엔드를 거부한다.

- 경로: `GET /v1/models` · `POST /v1/chat/completions`
  (비스트리밍 — holmes가 `stream=False`로 부른다)
  · `POST|GET /_bench/active {world_id, trial, arm, script}`(루프백만).
- `script`: `model`(모범 답안) 또는 `neg.<코드>`(일부러 틀리는 대본). 없으면 `model`.
- 대본 줄: `{call: <도구>, args: {...}}` 또는 `{answer: "<서술>"}`. 몇 번째 줄인지는 요청 대화의
  **assistant 메시지 수**로 정한다 — 도구 결과(`tool`) 메시지가 붙어 다시 오면 다음 줄이 된다.
  상태를 들고 있지 않으므로 같은 요청 = 같은 응답이다.
- 대본이 끝나면 마지막 줄이 `repeat: true`인 호출이면 그 호출을 계속 내고(step 소진 대본 —
  `tools` 키가 없는 마지막 턴에도 낸다 · §4.11 대본 주의), 아니면 답변으로 끝낸다.
- 세계가 지정되지 않았으면(사전 점검의 tool-calling 왕복) 받은 첫 도구를 한 번 부르고 답한다.
- 응답에 `usage`를 싣는다(holmes가 그대로 누적 — 토큰 칸 검증용). 바인딩은 127.0.0.1 고정.

경계(D-139): `sre_agent`·`mcp_server`·`apm_gateway` 패키지를 import하지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from investigation_bench.worlds import World, load_worlds

BIND_HOST = "127.0.0.1"
LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1"})
CONTROL_PATH = "/_bench/active"
SERVED_MODEL = "fake-bench"
MAX_MODEL_LEN = 32768
DEFAULT_SCRIPT = "model"
FALLBACK_ANSWER = "대본 없음 — 가짜 LLM 기본 답변"


def script_steps(world: World, script: str) -> list[dict[str, Any]]:
    """세계 YAML에서 대본 하나를 꺼낸다(`model` 또는 `neg.<코드>`). 없으면 KeyError."""
    scripts = world.data.get("script") or {}
    if script == "model":
        steps = scripts.get("model")
    elif script.startswith("neg."):
        steps = (scripts.get("neg") or {}).get(script[len("neg."):])
    else:
        steps = None
    if not isinstance(steps, list) or not steps:
        raise KeyError(f"{world.id}: 대본 없음 {script!r}")
    return [dict(s) for s in steps]


def _estimate_tokens(text: str) -> int:
    return len(text) // 4 + 1


def _usage(body: Mapping[str, Any], output: str) -> dict[str, int]:
    prompt = _estimate_tokens(json.dumps(body.get("messages") or [], ensure_ascii=False))
    completion = _estimate_tokens(output)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion}


def _completion(body: Mapping[str, Any], message: dict[str, Any], finish: str) -> dict[str, Any]:
    output = json.dumps(message, ensure_ascii=False)
    return {
        "id": f"chatcmpl-bench-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model") or SERVED_MODEL,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": _usage(body, output),
    }


def _tool_call_message(name: str, args: Mapping[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(dict(args), ensure_ascii=False)},
        }],
    }


class FakeLLM:
    """대본 재생 OpenAI 호환 서버. 현재 세계·대본은 러너가 제어 경로로 지정한다."""

    def __init__(self, worlds: Mapping[str, World], log_path: Path | None = None) -> None:
        self.worlds = dict(worlds)
        self.log_path = Path(log_path) if log_path is not None else None
        self.world_id: str | None = None
        self.trial: int | None = None
        self.arm: str | None = None
        self.script: str = DEFAULT_SCRIPT
        self._lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    # ── 현재 세계 ──

    def set_active(self, world_id: str | None, trial: int | None = None,
                   arm: str | None = None, script: str | None = None) -> None:
        """현재 세계·대본을 바꾼다(None = 해제). 모르는 세계·대본이면 KeyError."""
        script = script or DEFAULT_SCRIPT
        if world_id is not None:
            if world_id not in self.worlds:
                raise KeyError(world_id)
            script_steps(self.worlds[world_id], script)
        with self._lock:
            self.world_id, self.trial, self.arm, self.script = world_id, trial, arm, script

    def state(self) -> dict[str, Any]:
        """현재 지정 상태."""
        with self._lock:
            return {"world_id": self.world_id, "trial": self.trial, "arm": self.arm,
                    "script": self.script}

    # ── 응답 ──

    def respond(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """chat 요청 하나 → `chat.completion` 응답(대본 한 줄)."""
        messages = list(body.get("messages") or [])
        tools = body.get("tools") or []
        turn = sum(1 for m in messages if isinstance(m, Mapping) and m.get("role") == "assistant")
        with self._lock:
            world = self.worlds.get(self.world_id) if self.world_id else None
            script = self.script
        if world is None:
            return self._fallback(body, messages, tools, turn)
        steps = script_steps(world, script)
        if turn < len(steps):
            step = steps[turn]
        elif steps[-1].get("repeat") and "call" in steps[-1]:
            step = steps[-1]
        else:
            step = {"answer": next((s["answer"] for s in reversed(steps) if "answer" in s),
                                   FALLBACK_ANSWER)}
        if "call" in step:
            message = _tool_call_message(str(step["call"]), step.get("args") or {},
                                         f"call_{turn}")
            out = _completion(body, message, "tool_calls")
        else:
            out = _completion(body, {"role": "assistant", "content": str(step.get("answer"))},
                              "stop")
        self._log(turn, len(messages), bool(tools), step)
        return out

    def _fallback(self, body: Mapping[str, Any], messages: list[Any], tools: list[Any],
                  turn: int) -> dict[str, Any]:
        """세계 미지정 — 첫 도구를 한 번 부르고 답한다(사전 점검 tool-calling 왕복용)."""
        last_is_tool = bool(messages) and isinstance(messages[-1], Mapping) \
            and messages[-1].get("role") == "tool"
        if tools and not last_is_tool:
            name = str(((tools[0] or {}).get("function") or {}).get("name") or "unknown")
            return _completion(body, _tool_call_message(name, {}, f"call_{turn}"), "tool_calls")
        return _completion(body, {"role": "assistant", "content": FALLBACK_ANSWER}, "stop")

    def _log(self, turn: int, n_messages: int, has_tools: bool, step: Mapping[str, Any]) -> None:
        if self.log_path is None:
            return
        with self._lock:
            row = {"ts": time.time(), "world_id": self.world_id, "trial": self.trial,
                   "arm": self.arm, "script": self.script, "turn": turn,
                   "n_messages": n_messages, "has_tools": has_tools,
                   "emitted": "call" if "call" in step else "answer"}
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ── 앱 ──

    def build_app(self) -> Starlette:
        """OpenAI 호환 경로 + 제어 경로."""

        async def models(_: Request) -> JSONResponse:
            return JSONResponse({"object": "list", "data": [{
                "id": SERVED_MODEL, "object": "model", "owned_by": "investigation_bench",
                "max_model_len": MAX_MODEL_LEN,
            }]})

        async def chat(request: Request) -> JSONResponse:
            body = await request.json()
            if body.get("stream"):
                return JSONResponse({"error": {"message": "stream 미지원(가짜 LLM)"}},
                                    status_code=400)
            try:
                return JSONResponse(self.respond(body))
            except KeyError as e:
                return JSONResponse({"error": {"message": str(e)}}, status_code=400)

        async def active(request: Request) -> JSONResponse:
            client = request.client.host if request.client else ""
            if client not in LOOPBACK_CLIENTS:
                return JSONResponse({"error": "loopback only"}, status_code=403)
            if request.method == "POST":
                body = await request.json()
                try:
                    self.set_active(body.get("world_id"), body.get("trial"), body.get("arm"),
                                    body.get("script"))
                except KeyError as e:
                    return JSONResponse({"error": f"unknown world/script: {e}"}, status_code=404)
            return JSONResponse(self.state())

        return Starlette(routes=[
            Route("/v1/models", models, methods=["GET"]),
            Route("/v1/chat/completions", chat, methods=["POST"]),
            Route(CONTROL_PATH, active, methods=["POST", "GET"]),
        ])

    # ── 기동·종료(테스트용 스레드) ──

    def start(self, port: int = 0, timeout: float = 10.0) -> int:
        """백그라운드 스레드로 띄우고 실제 포트를 돌려준다(0 = 임의 빈 포트)."""
        config = uvicorn.Config(self.build_app(), host=BIND_HOST, port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        server = self._server
        self._thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                self.stop()
                raise RuntimeError("가짜 LLM 기동 실패")
            time.sleep(0.02)
        self.port = int(server.servers[0].sockets[0].getsockname()[1])
        return self.port

    def stop(self) -> None:
        """띄운 서버를 내린다."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)


def main(argv: Sequence[str] | None = None) -> int:
    """가짜 LLM 단독 기동(러너가 별도 프로세스로 띄운다 — §4.7)."""
    p = argparse.ArgumentParser(prog="investigation_bench.fake_llm")
    p.add_argument("--worlds", type=Path, nargs="+", required=True, help="세계 YAML 파일·디렉터리")
    p.add_argument("--port", type=int, default=19180)
    p.add_argument("--log", type=Path, default=None, help="요청 요약 JSONL(선택)")
    args = p.parse_args(argv)
    fake = FakeLLM(load_worlds(args.worlds), args.log)
    uvicorn.run(fake.build_app(), host=BIND_HOST, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
