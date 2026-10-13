"""가짜 세계 MCP 서버 (plans/154 §4.10 — 이 모듈의 정본).

스냅샷(`tool_schemas.json`)의 도구를 이름·설명·`inputSchema`·`outputSchema` 그대로 등록하고, 러너가
`POST /_bench/active {world_id, trial[, arm]}`로 지정한 **현재 세계** 하나의 `tools:` 규칙으로
답한다.
`arm`(선택 · W3)은 호출 로그 행에 그대로 실린다 — 실험군별 구간을 로그만으로 가를 수 있게.

- 등록(W0 실측 · mcp 1.25): FastMCP `add_tool`은 함수 시그니처로 스키마를 만들어 스냅샷을 그대로 못
  낸다. 그래서 `FastMCP`의 저수준 서버(`_mcp_server`)에
  `list_tools`·`call_tool(validate_input=False)` 핸들러를 직접 건다. 스키마 밖 인자도
  핸들러까지 온다.
- 응답 모양(실 서버 grep 실측): 두 서버 모두 도구가 **JSON 문자열**을 돌려주고 FastMCP가
  `structuredContent={"result": <그 문자열>}`을 붙인다(`outputSchema`가 그 모양). 도구 오류도 예외가
  아니라 `{"error": …}` JSON 문자열이다(`isError=false` — 폴스타 `_err` · APM `ApmTools.err`).
- 인자 처리 순서: 필수 인자 누락 → 호스트 → 시간창 → 매처. `null` 인자는 「없음」이다(holmes가 선택
  인자를 `anyOf[T,null]`·required로 바꿔 LLM이 `null`을 보낸다).
- 필수 인자 누락(또는 `null`)은 실 FastMCP 서버처럼 `isError=true` 검증 오류로 돌려준다
  (`arg_error=required` — W2 결정 · §4.10 「실 서버 거동과 맞출지는 W2가 정한다」).
- 헬스체크 도구(`list_sources`·`gateway_health`)는 현재 세계가 없어도 성공한다 — holmes가 toolset
  등록 때 1회 부르고 실패하면 그 toolset을 프로세스 수명 내내 끈다(W0 실측).
- 모든 호출을 `world_calls.jsonl`에 한 줄씩 쓴다(§4.9). 바인딩은 127.0.0.1 고정, 제어 경로는
  루프백 클라이언트만 받는다.

경계(D-139 · §9.5): `mcp_server`·`apm_gateway`·`sre_agent` 패키지를 import하지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import mcp.types as types
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from investigation_bench import schema
from investigation_bench.worlds import World, load_worlds, parse_offset, parse_wall_time

BIND_HOST = "127.0.0.1"
LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1"})
CONTROL_PATH = "/_bench/active"

# 호스트 인자 키(스냅샷 실측 2026-10-13): 폴스타 `server_name`(6종)·`hostname`(4종) ·
# APM `hostname`(17종). APM `instance_name`은 WAS 인스턴스 이름이라 호스트가 아니다.
# 도구별로는 스냅샷 `inputSchema`에 있는 키만 검사한다(`host_arg_keys`).
HOST_ARG_KEYS: tuple[str, ...] = ("hostname", "server_name")
# 대상 목록 인자 — 항목(dict)마다 호스트 키를 검사한다(APM `targets` 17종).
TARGET_LIST_KEYS: tuple[str, ...] = ("targets",)
# 시간창 인자(스냅샷 실측): 끝 = `reference_time`, 폭 = `lookback_minutes`(분) → `hours`(시) →
# `window`(기간 문자열 · prom_metric_range). 인자가 없으면 스키마 기본값을 쓰고, 그래도 폭을 모르면
# 검사하지 않는다(잘못된 F-ARG를 만들지 않는다). 시작·끝을 직접 받는 쌍은 `RANGE_PAIRS`.
ANCHOR_KEY = "reference_time"
WIDTH_MINUTE_KEYS: tuple[tuple[str, int], ...] = (("lookback_minutes", 1), ("hours", 60))
DURATION_KEYS: tuple[str, ...] = ("window",)
RANGE_PAIRS: tuple[tuple[str, str], ...] = (("current_start", "current_end"),)

HEALTH_TOOLS: dict[str, str] = {"polestar": "list_sources", "apm": "gateway_health"}
_APM_ENVELOPE = {"source_kind": "apm_api", "source": "jennifer"}
HEALTH_DEFAULT_REPLIES: dict[str, Any] = {
    "list_sources": [
        {"name": "polestar", "type": "postgresql", "readonly": True,
         "query_timeout": 30, "max_rows": 1000},
    ],
    "gateway_health": {"rows": [], "row_count": 0, **_APM_ENVELOPE,
                       "tool": "gateway_health", "status": "ok"},
}
EMPTY_ROWS: dict[str, Any] = {"rows": [], "row_count": 0}
NO_WORLD_ERROR = "벤치 세계 미지정 — /_bench/active로 세계를 지정해야 한다"

LOG_FIELDS: tuple[str, ...] = (
    "ts", "world_id", "trial", "arm", "endpoint", "tool", "args",
    "matcher", "arg_error", "reply", "reply_hash",
)


# ─────────────────────────────── 인자 해석 ───────────────────────────────


def clean_args(args: Mapping[str, Any] | None) -> dict[str, Any]:
    """`null` 인자를 「없음」으로 지운다."""
    return {k: v for k, v in (args or {}).items() if v is not None}


def host_arg_keys(tool: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """도구 스키마에 있는 (호스트 키, 대상 목록 키)."""
    props = schema.tool_properties(tool)
    return (
        tuple(k for k in HOST_ARG_KEYS if k in props),
        tuple(k for k in TARGET_LIST_KEYS if k in props),
    )


def _hosts_in(
    args: Mapping[str, Any], host_keys: Sequence[str], list_keys: Sequence[str]
) -> list[str]:
    found = [str(args[k]) for k in host_keys if k in args]
    for k in list_keys:
        for item in args.get(k) or []:
            if isinstance(item, Mapping):
                found += [str(item[h]) for h in HOST_ARG_KEYS if item.get(h) is not None]
    return found


def _duration_minutes(text: Any) -> float:
    return parse_offset(text).total_seconds() / 60


def arg_time_range(
    args: Mapping[str, Any], tool: Mapping[str, Any]
) -> tuple[datetime, datetime] | None:
    """인자가 가리키는 조회 구간 — 판정할 수 없으면 None(검사 생략)."""
    for start_key, end_key in RANGE_PAIRS:
        if start_key in args and end_key in args:
            return parse_wall_time(args[start_key]), parse_wall_time(args[end_key])
    if ANCHOR_KEY not in args:
        return None
    end = parse_wall_time(args[ANCHOR_KEY])
    props = schema.tool_properties(tool)
    defaults = {k: p.get("default") for k, p in props.items() if p.get("default") is not None}
    width: float | None = None
    for source in (args, defaults):
        for key, factor in WIDTH_MINUTE_KEYS:
            if key in source:
                width = float(source[key]) * factor
                break
        else:
            for key in DURATION_KEYS:
                if key in source:
                    width = _duration_minutes(source[key])
                    break
        if width is not None:
            break
    if width is None:
        return None
    return end - timedelta(minutes=width), end


# ─────────────────────────────── 생성기 ───────────────────────────────


def gen_seed(world_id: str, tool: str, args: Mapping[str, Any]) -> int:
    """`(world_id, tool, 정렬 args)` 해시 시드 — `null` 인자는 빠진 것과 같다."""
    key = json.dumps([world_id, tool, sorted(clean_args(args).items())],
                     ensure_ascii=False, sort_keys=True, default=str)
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16)


def _expand_series(
    spec: Mapping[str, Any], rng: random.Random, anchor: datetime
) -> list[dict[str, Any]]:
    """`gen` 지시 전개 — 키: base · to · spike_at · noise · points · step · end · decimals ·
    ts_key · value_key.

    `end`(기본 0m)에서 끝나는 `points`개(기본 12) 점을 `step`(기본 5m) 간격으로 만든다.
    값은 `base`이고 `spike_at`(알람 시각 기준) 이후는 `to`다. 각 점에 ±`noise` 균등 잡음을 더한다.
    """
    base = float(spec["base"])
    to = float(spec["to"]) if spec.get("to") is not None else None
    spike = anchor + parse_offset(spec["spike_at"]) if spec.get("spike_at") is not None else None
    noise = float(spec.get("noise", 0))
    points = int(spec.get("points", 12))
    step = parse_offset(spec.get("step", "5m"))
    end = anchor + parse_offset(spec.get("end", "0m"))
    decimals = int(spec.get("decimals", 1))
    ts_key = str(spec.get("ts_key", "ts"))
    value_key = str(spec.get("value_key", "value"))
    series = []
    for i in range(points):
        t = end - step * (points - 1 - i)
        value = to if (to is not None and spike is not None and t >= spike) else base
        value += rng.uniform(-noise, noise) if noise else 0.0
        series.append({ts_key: t.strftime("%Y-%m-%dT%H:%M:%S"), value_key: round(value, decimals)})
    return series


def expand_gen(node: Any, rng: random.Random, anchor: datetime | None) -> Any:
    """응답 안의 `{gen: {...}}` 마디를 시계열로 펼친다(나머지는 그대로)."""
    if isinstance(node, Mapping):
        if set(node) == {"gen"} and isinstance(node["gen"], Mapping):
            if anchor is None:
                raise ValueError("gen에는 event.alarmTime이 필요하다")
            return _expand_series(node["gen"], rng, anchor)
        return {k: expand_gen(v, rng, anchor) for k, v in node.items()}
    if isinstance(node, list):
        return [expand_gen(v, rng, anchor) for v in node]
    return node


# ─────────────────────────────── 응답 모양 ───────────────────────────────


def _empty(endpoint: str, tool: str) -> dict[str, Any]:
    if endpoint == "apm":
        return {**EMPTY_ROWS, **_APM_ENVELOPE, "tool": tool}
    return dict(EMPTY_ROWS)


def _host_miss(endpoint: str, tool: str, hosts: Sequence[str]) -> dict[str, Any]:
    reason = f"대상 없음 — 등록되지 않은 서버: {', '.join(hosts)}"
    if endpoint == "apm":
        return {"error": "instance_unresolved", "reason": reason, **_APM_ENVELOPE, "tool": tool}
    return {**EMPTY_ROWS, "message": reason}


def _injected_error(endpoint: str, tool: str, reply: Mapping[str, Any]) -> dict[str, Any]:
    if endpoint == "apm":
        return {"error": reply.get("code", "apm_api_error"), "reason": str(reply.get("error")),
                **_APM_ENVELOPE, "tool": tool}
    return {"error": reply.get("error")}


# ─────────────────────────────── 세계 서버 ───────────────────────────────


@dataclass
class CallOutcome:
    """도구 호출 1건의 판정 결과."""

    reply: Any
    matcher: str | None = None
    arg_error: str | None = None
    is_error: bool = False


class WorldServer:
    """스냅샷 도구 표면 + 현재 세계 응답. 엔드포인트(`polestar`·`apm`)마다 SSE 앱 하나."""

    def __init__(
        self, snapshot: schema.Snapshot, worlds: Mapping[str, World], log_path: Path
    ) -> None:
        self.snapshot = snapshot
        self.worlds = dict(worlds)
        self.log_path = Path(log_path)
        self.world_id: str | None = None
        self.trial: int | None = None
        self.arm: str | None = None
        self._lock = threading.Lock()
        self._tools = schema.tool_index(snapshot)
        self._servers: list[uvicorn.Server] = []
        self._thread: threading.Thread | None = None
        self.ports: dict[str, int] = {}

    # ── 현재 세계 ──

    def set_active(
        self, world_id: str | None, trial: int | None = None, arm: str | None = None
    ) -> None:
        """현재 세계를 바꾼다(None = 해제). `arm`은 로그 표지(선택). 모르는 세계면 KeyError."""
        if world_id is not None and world_id not in self.worlds:
            raise KeyError(world_id)
        with self._lock:
            self.world_id, self.trial, self.arm = world_id, trial, arm

    # ── 호출 판정 ──

    def resolve(self, endpoint: str, name: str, arguments: Mapping[str, Any] | None) -> CallOutcome:
        """도구 호출 1건의 응답을 정한다(로그는 `call`이 쓴다)."""
        with self._lock:
            world = self.worlds.get(self.world_id) if self.world_id else None
        tool = self._tools.get(name, {})
        args = clean_args(arguments)

        required = (tool.get("inputSchema") or {}).get("required") or []
        missing = [k for k in required if k not in args]
        if missing:
            text = f"Error executing tool {name}: 필수 인자 누락 — {', '.join(missing)}"
            return CallOutcome(reply=text, arg_error="required", is_error=True)

        if world is None:
            if name in HEALTH_DEFAULT_REPLIES:
                return CallOutcome(reply=HEALTH_DEFAULT_REPLIES[name], matcher="default")
            return CallOutcome(reply={"error": NO_WORLD_ERROR})

        host_keys, list_keys = host_arg_keys(tool)
        world_hosts = world.hosts
        if world_hosts:
            outside = [h for h in _hosts_in(args, host_keys, list_keys)
                       if h.strip().casefold() not in world_hosts]
            if outside:
                return CallOutcome(reply=_host_miss(endpoint, name, outside), arg_error="host")

        incident = world.incident_range()
        if incident is not None:
            try:
                span = arg_time_range(args, tool)
            except (ValueError, TypeError):
                span = None  # 시각 형식 오류는 실 서버 몫 — 여기서 F-ARG로 세지 않는다
            if span is not None and not (span[0] <= incident[1] and span[1] >= incident[0]):
                return CallOutcome(reply=_empty(endpoint, name), arg_error="time_window")

        rules = world.tools.get(name)
        reply: Any = None
        matcher: str | None = None
        for i, rule in enumerate(rules if isinstance(rules, list) else []):
            match = (rule or {}).get("match") or {}
            if all(k in args and args[k] == v for k, v in match.items()):
                reply, matcher = rule.get("reply"), f"{name}#{i}"
                break
        if matcher is None:
            matcher = "default"
            # 헬스체크 도구는 세계 `default`보다 기본 헬스 응답이 먼저다 — 세계 진행 중에 LLM이
            # `list_sources`를 불러도 소스 목록을 받는다(세계가 규칙으로 덮을 수는 있다).
            if name in HEALTH_DEFAULT_REPLIES:
                reply = HEALTH_DEFAULT_REPLIES[name]
            elif "default" in world.tools:
                reply = world.tools["default"]
            else:
                reply = _empty(endpoint, name)

        if isinstance(reply, Mapping) and reply.get("inject") is True:
            return CallOutcome(reply=_injected_error(endpoint, name, reply), matcher=matcher)
        rng = random.Random(gen_seed(world.id, name, args))
        return CallOutcome(reply=expand_gen(reply, rng, world.alarm_time), matcher=matcher)

    def call(
        self, endpoint: str, name: str, arguments: Mapping[str, Any] | None
    ) -> types.CallToolResult:
        """도구 호출 1건 — 판정 → 실 서버 응답 모양 → `world_calls.jsonl` 1줄."""
        try:
            outcome = self.resolve(endpoint, name, arguments)
        except Exception as e:  # 세계 YAML 결함(gen 지시 오류 등) — 오류 응답 + 로그
            outcome = CallOutcome(reply=f"Error executing tool {name}: {e}", is_error=True)
        text = outcome.reply if isinstance(outcome.reply, str) else json.dumps(
            outcome.reply, ensure_ascii=False, default=str)
        structured = None
        out_props = (self._tools.get(name, {}).get("outputSchema") or {}).get("properties") or {}
        if not outcome.is_error and "result" in out_props:
            structured = {"result": text}
        self._log(endpoint, name, arguments, outcome, text)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structuredContent=structured,
            isError=outcome.is_error,
        )

    def _log(self, endpoint: str, name: str, arguments: Mapping[str, Any] | None,
             outcome: CallOutcome, text: str) -> None:
        with self._lock:
            row = {
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "world_id": self.world_id,
                "trial": self.trial,
                "arm": self.arm,
                "endpoint": endpoint,
                "tool": name,
                "args": dict(arguments or {}),
                "matcher": outcome.matcher,
                "arg_error": outcome.arg_error,
                "reply": outcome.reply,
                "reply_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    # ── 앱 조립 ──

    def build_app(self, endpoint: str, *, control: bool) -> Starlette:
        """엔드포인트 하나의 SSE 앱. `control=True`면 `/_bench/active`를 함께 싣는다."""
        mcp = FastMCP(f"bench-world-{endpoint}", host=BIND_HOST)
        low = mcp._mcp_server
        tools = [types.Tool.model_validate(t) for t in self.snapshot.get(endpoint) or []]

        @low.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
        async def _list_tools() -> list[types.Tool]:
            return tools

        @low.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
        async def _call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
            return self.call(endpoint, name, arguments)

        if control:
            @mcp.custom_route(CONTROL_PATH, methods=["POST", "GET"])  # type: ignore[untyped-decorator]
            async def _active(request: Request) -> JSONResponse:
                client = request.client.host if request.client else ""
                if client not in LOOPBACK_CLIENTS:
                    return JSONResponse({"error": "loopback only"}, status_code=403)
                if request.method == "POST":
                    body = await request.json()
                    try:
                        self.set_active(body.get("world_id"), body.get("trial"), body.get("arm"))
                    except KeyError:
                        return JSONResponse({"error": f"unknown world: {body.get('world_id')}"},
                                            status_code=404)
                state: dict[str, Any] = {"world_id": self.world_id, "trial": self.trial}
                if self.arm is not None:  # arm 없이 부르면 응답 모양이 종전 그대로다
                    state["arm"] = self.arm
                return JSONResponse(state)

        return mcp.sse_app()

    # ── 기동·종료 ──

    def _make_servers(self, ports: Mapping[str, int]) -> list[tuple[str, uvicorn.Server]]:
        out = []
        for i, (endpoint, port) in enumerate(ports.items()):
            app = self.build_app(endpoint, control=(i == 0))
            config = uvicorn.Config(app, host=BIND_HOST, port=port, log_level="warning")
            out.append((endpoint, uvicorn.Server(config)))
        return out

    async def serve(self, ports: Mapping[str, int]) -> None:
        """엔드포인트들을 한 이벤트 루프에서 띄운다(첫 엔드포인트가 제어 경로를 싣는다)."""
        pairs = self._make_servers(ports)
        self._servers = [s for _, s in pairs]

        async def _record_ports() -> None:
            while not all(s.started for s in self._servers):
                await asyncio.sleep(0.02)
            for endpoint, s in pairs:
                self.ports[endpoint] = s.servers[0].sockets[0].getsockname()[1]

        await asyncio.gather(*(s.serve() for s in self._servers), _record_ports())

    def start(self, ports: Mapping[str, int], timeout: float = 10.0) -> dict[str, int]:
        """백그라운드 스레드로 띄우고 실제 포트를 돌려준다(포트 0 = 임의 빈 포트 · 테스트용)."""
        self._thread = threading.Thread(target=lambda: asyncio.run(self.serve(ports)), daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while len(self.ports) < len(ports):
            if time.monotonic() > deadline or not self._thread.is_alive():
                self.stop()
                raise RuntimeError("세계 서버 기동 실패")
            time.sleep(0.02)
        return dict(self.ports)

    def stop(self) -> None:
        """띄운 엔드포인트를 모두 내린다."""
        for s in self._servers:
            s.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)


def main(argv: Sequence[str] | None = None) -> int:
    """세계 서버 단독 기동(러너가 별도 프로세스로 띄운다 — §4.7)."""
    p = argparse.ArgumentParser(prog="investigation_bench.world_server")
    p.add_argument("--snapshot", type=Path, default=schema.DEFAULT_SNAPSHOT_PATH)
    p.add_argument("--worlds", type=Path, nargs="+", required=True, help="세계 YAML 파일·디렉터리")
    p.add_argument("--log", type=Path, required=True, help="world_calls.jsonl 경로")
    p.add_argument("--port", type=int, default=19190, help="polestar 엔드포인트(+제어 경로)")
    p.add_argument("--apm-port", type=int, default=None, help="apm 엔드포인트(지정할 때만 띄운다)")
    args = p.parse_args(argv)
    server = WorldServer(schema.load_snapshot(args.snapshot), load_worlds(args.worlds), args.log)
    ports = {"polestar": args.port}
    if args.apm_port is not None:
        ports["apm"] = args.apm_port
    asyncio.run(server.serve(ports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
