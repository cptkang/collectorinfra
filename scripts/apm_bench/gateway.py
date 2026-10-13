"""벤치 전용 게이트웨이·목 Open API 수명과 감사 줄 tail (plans/152 §3.1·§3.7 · W2 · D-323).

- **게이트웨이 서브프로세스**: `python -m apm_gateway`(cwd `apm_gateway/`). 부모 env 의
  `JENNIFER_*`·`APM_*`를 걷어내고 벤치 값을 명시한다 — 속도 초당 2회(전역 + 소스별) · 폴러 끔 ·
  스풀 = run 임시 디렉터리 · Redis 차단(127.0.0.1:1) · 127.0.0.1 임시 포트. E1 은 목 서버
  URL·토큰을 소스별로 넣고, E2 는 소스 URL·토큰을 넣지 않아 게이트웨이가 자기
  `apm_gateway/.env`에서 읽는다(이미 있는 env 를 덮지 않으므로 벤치가 넣은 속도·폴러·스풀 값이
  이긴다 — `apm_gateway/config.py` `load_dotenv`).
- **목 Open API(E1)**: `mock_openapi.make_server(…, "connected")` + 합성 픽스처
  (`synthetic_fixtures()`) + W0 가 찾은 공백 3종(`/api/transaction/guid`·
  `/api/realtime/domain`·`/api/realtime/business`)과 통합 Wave 가 찾은 공백(서비스·업무 추세
  `/api/dbmetrics/domain`·`/api/dbmetrics/business` · 거래·액티브 서비스 행의 인스턴스 1002·1003
  복제)을 벤치 소유 픽스처로 보탠다. 장애 주입
  (429·지연)은 목 `Handler._route`를 감싸 서버별 상태로 건다.
- **감사 줄 tail**: 게이트웨이 감사 1줄(`interface/audit.py` 성공 갈래 70행 · 오류 갈래 55행)을
  파싱해 thread_id 로 턴에 귀속한다. 게이트웨이는 DEBUG 로 띄워 제니퍼 응답 줄(`apm http: …
  status=…`)도 읽는다 — 팬아웃 도구의 부분 429·타임아웃은 감사 줄에 남지 않아 가드가 이 줄로 본다.
- **MCP SSE 클라이언트** `GatewayProc.call_tool(name, args) -> dict` — P층·기준선 측정용(W3 주입).

**`apm_gateway` 패키지를 import 하지 않는다(D-274)** — 목 서버·픽스처 헬퍼는 파일 경로로만 로드한다
(`importlib.util.spec_from_file_location` · 테스트 선례). E2 경로는 이 둘을 로드하지 않는다.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import IO, Any

from . import REPO_ROOT

GW_ROOT = REPO_ROOT / "apm_gateway"
MOCK_SCRIPTS = GW_ROOT / "testdata" / "jennifer" / "scripts"
GW_CONFTEST = GW_ROOT / "tests" / "conftest.py"
#: 쓰지 않는 포트(운영·개발 기본 포트 — MCP 9099 · 게이트웨이 9096·9097 · MLX 8080 · 본체 8000).
FORBIDDEN_PORTS = frozenset({9096, 9097, 9099, 8080, 8000})
#: 벤치 게이트웨이 속도(초당 호출 · D-323 ③ — 운영 게이트웨이 5회와 합쳐 뷰 서버 초당 7회 이하).
BENCH_RATE_PER_SEC = 2.0
#: 벤치 게이트웨이의 제니퍼 호출 1회 상한(초 · 게이트웨이 기본값과 같다 — 타임아웃 판정 기준을
#: 고정하려고 명시한다).
BENCH_API_TIMEOUT_SEC = 10.0
#: 주체 이름(게이트웨이 `APM_GATEWAY_BEARER_TOKENS`) — 본체 질의 · 벤치 직접 호출(P층·기준선).
CHAT_PRINCIPAL = "chat"
BENCH_PRINCIPAL = "bench"
GATEWAY_START_TIMEOUT_SEC = 30.0

#: 감사 1줄(`apm audit: tool=… target=… elapsed_ms=… api_calls=… sources=… rows=…|error=… …`).
AUDIT_RE = re.compile(
    r"apm audit: tool=(?P<tool>\S+) target=(?P<target>.*?) elapsed_ms=(?P<elapsed>[0-9.]+) "
    r"api_calls=(?P<calls>\d+) sources=(?P<sources>\S+) "
    r"(?:rows=(?P<rows>\S+)|error=(?P<error>\S+)) "
    r"investigation_id=(?P<inv>\S+) thread_id=(?P<thread>\S+) principal=(?P<principal>\S+) "
    r"job_id=(?P<job>\S+)(?: search=(?P<search>\S+))?"
)
#: 제니퍼 응답 1건(게이트웨이 `adapters/jennifer/client.py` DEBUG — 응답을 받은 뒤 찍힌다 · httpx
#: 타임아웃·연결 실패는 이 줄 없이 끊긴다). 경로·키는 읽지 않는다(경로에 ID 가 들어간다).
HTTP_RE = re.compile(r"apm http: .*? status=(?P<status>\d+) elapsed_ms=[0-9.]+")
#: 게이트웨이 로그 수준 — 응답 줄(`HTTP_RE`)을 남기려면 DEBUG 여야 한다(가드 429·타임아웃 판정).
BENCH_LOG_LEVEL = "DEBUG"


def free_port() -> int:
    """127.0.0.1 에 바인딩 가능한 임시 포트(금지 포트 제외)."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in FORBIDDEN_PORTS:
            return port
    raise RuntimeError("빈 포트를 찾지 못했다")


# --- 파일 경로 로드 (D-274 — 패키지 import 아님) ---------------------------------------------

_LOADED: dict[str, ModuleType] = {}


def _load_path_module(name: str, path: Path) -> ModuleType:
    """파일 경로로 모듈을 읽는다 — 그 모듈이 바꾼 `sys.path`는 되돌린다(벤치 프로세스의 `tests`·
    `config` 이름이 게이트웨이 쪽으로 가려지지 않게)."""
    if name in _LOADED:
        return _LOADED[name]
    saved = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"모듈을 읽지 못했다: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    _LOADED[name] = module
    return module


def mock_module() -> ModuleType:
    """목 Open API 서버 모듈(`apm_gateway/testdata/jennifer/scripts/mock_openapi.py`)."""
    return _load_path_module("apm_bench_mock_openapi", MOCK_SCRIPTS / "mock_openapi.py")


def synthetic_fixtures() -> list[dict[str, Any]]:
    """게이트웨이 테스트의 합성 픽스처(`apm_gateway/tests/conftest.py synthetic_fixtures`)."""
    conftest = _load_path_module("apm_bench_gw_conftest", GW_CONFTEST)
    return list(conftest.synthetic_fixtures())


def _fx(template: str, body: Any) -> dict[str, Any]:
    """픽스처 1건(합성 · 목 서버 형식)."""
    return {
        "fixture_version": 1,
        "source": "apm-bench-synthetic",
        "jennifer_version": "5.6.4-spec",
        "request": {"method": "GET", "template": template, "path": template, "query": {}},
        "response": {"status": 200, "content_type": "application/json", "body_json": body},
        "variant": "ok",
        "label": "",
    }


#: 합성 픽스처 기준 도메인·인스턴스(`synthetic_fixtures` 와 같은 값 — 합성 이름).
_DOMAIN = 1000
_NOW_MS = 1790643600000


#: 거래·액티브 서비스 행을 복제할 인스턴스(합성 `/api/instance`의 나머지 둘) — 합성 행은 1001 뿐이라
#: 다른 인스턴스로 조회한 멀티턴 참조(「그중 첫 번째 프로파일」)가 0행에서 끊긴다(통합 Wave 실측).
_CLONE_INSTANCES: tuple[tuple[int, str, int], ...] = ((1002, "was01_b", 1), (1003, "api02_main", 2))
#: 합성 픽스처에서 호스트가 빈 인스턴스 → 벤치 호스트(합성).
#: 게이트웨이 테스트는 「호스트 없는 행」을 일부러 두지만, 벤치 자리표 `inst_2`(api02_main)의
#: 참조 후속(프로파일·추적)은 앞 행 hostname 이 있어야 성립한다 — 실 제니퍼 인스턴스는 호스트를
#: 늘 가진다(통합 Wave).
_BENCH_HOSTS: dict[int, str] = {1003: "api-host02.example.local"}


def _clone_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """1001 행을 `_CLONE_INSTANCES`로 복제한다 — txid·guid 는 인스턴스마다 다르게."""
    out = [dict(r) for r in rows]
    for inst_id, name, k in _CLONE_INSTANCES:
        for row in rows:
            if row.get("instanceId") != 1001:
                continue
            clone = {**row, "instanceId": inst_id, "instanceName": name,
                     "instanceOid": 50_000 + k}
            if "txid" in clone:
                clone["txid"] = str(int(clone["txid"]) + k * 100)
            if "guid" in clone:
                clone["guid"] = f"{clone['guid']}-{k}"
            out.append(clone)
    return out


def _series(values: Sequence[float]) -> dict[str, Any]:
    """추세 응답(합성 `series`와 같은 모양 · 5분 간격 · `_NOW_MS`에서 끝남)."""
    return {"result": [{"time": str(_NOW_MS - (len(values) - k) * 300_000), "value": v}
                       for k, v in enumerate(values)]}


def bench_fixtures() -> list[dict[str, Any]]:
    """합성 픽스처 + 벤치 공백 보충(벤치 소유 · 합성 이름만)."""
    out = synthetic_fixtures()
    for fx in out:
        if fx["request"]["template"] == "/api/instance":
            body = dict(fx["response"]["body_json"])
            body["result"] = [
                {**row, "hostName": _BENCH_HOSTS[row.get("instanceId")]}
                if not row.get("hostName") and row.get("instanceId") in _BENCH_HOSTS else row
                for row in body.get("result") or []
            ]
            fx["response"] = {**fx["response"], "body_json": body}
        if fx["request"]["template"] in ("/api/transaction/time", "/api/activeService/list"):
            body = dict(fx["response"]["body_json"])
            body["result"] = _clone_rows(body.get("result") or [])
            fx["response"] = {**fx["response"], "body_json": body}
    have = {fx["request"]["template"] for fx in out}
    # 시각은 합성 거래 행(`/api/transaction/time`)과 같다 — 참조 보기(trace)가 앞 행 시각
    # ±5분 창으로 다시 찾는다(통합 Wave — 10분 앞 시각이면 창 밖이라 0행)
    guid_rows = _clone_rows([
        {
            "domainId": _DOMAIN, "domainName": "demo-domain", "instanceId": 1001,
            "instanceName": "was01_a", "applicationName": "/order/list", "txid": str(9000 + i),
            "guid": f"guid-{i:04d}", "responseTime": 900 - i * 100,
            "startTime": str(_NOW_MS - 34_000 - i * 1000),
            "endTime": str(_NOW_MS - 30_000 - i * 1000), "errorType": "",
        }
        for i in range(3)
    ])
    extra = {
        "/api/transaction/guid": {"result": guid_rows},
        "/api/realtime/domain": {"result": [{
            "domainId": _DOMAIN, "domainName": "demo-domain", "tps": 82.5, "responseTime": 480.0,
            "activeService": 40, "concurrentUser": 200.0, "arrivalRate": 84.0,
        }]},
        "/api/realtime/business": {"result": [
            {"domainId": _DOMAIN, "businessId": bid, "businessName": name, "tps": tps,
             "responseTime": rt, "activeService": 3}
            for bid, name, tps, rt in ((7, "주문", 30.0, 520.0), (9, "결제", 12.0, 610.0))
        ]},
        # 서비스·업무 추세(`apm.service_trend`·`apm.business_trend`) — 라벨 없음 = 모든 지표
        "/api/dbmetrics/domain": _series([80.0, 82.0, 85.0, 81.0, 79.0, 83.0]),
        "/api/dbmetrics/business": _series([30.0, 31.0, 29.0, 32.0, 30.0, 28.0]),
    }
    out += [_fx(template, body) for template, body in extra.items() if template not in have]
    return out


# --- 목 Open API (E1) ---------------------------------------------------------------------


@dataclass
class Faults:
    """목 서버 장애 주입(서버별) — 429·지연.

    Attributes:
        status: 이 상태 코드로 답한다(예 429) · None 이면 정상 응답
        delay_sec: 응답 전 지연(초)
        templates: 적용할 경로 템플릿(빈 집합 = 전부)
        remaining: 남은 적용 횟수(None = 무제한)
    """

    status: int | None = None
    delay_sec: float = 0.0
    templates: frozenset[str] = frozenset()
    remaining: int | None = None

    def take(self, template: str | None) -> bool:
        if self.templates and template not in self.templates:
            return False
        if self.remaining is not None:
            if self.remaining <= 0:
                return False
            self.remaining -= 1
        return True


_ROUTE_PATCHED = threading.Lock()


def _patch_route(mock: ModuleType) -> None:
    """목 `Handler._route`를 한 번만 감싼다 — 서버 상태(`state.bench_faults`)가 있을 때만
    주입한다."""
    with _ROUTE_PATCHED:
        handler = mock.Handler
        if getattr(handler, "_bench_patched", False):
            return
        original = handler._route

        def _route(self: Any, method: str, path: str, query: dict[str, str],
                   template: str | None) -> int:
            faults: Faults | None = getattr(self.state, "bench_faults", None)
            if faults is not None and template is not None and faults.take(template):
                if faults.delay_sec:
                    time.sleep(faults.delay_sec)
                if faults.status is not None:
                    return int(self._send(faults.status, {"exception": {"message": "bench fault"}}))
            return int(original(self, method, path, query, template))

        handler._route = _route
        handler._bench_patched = True


@dataclass
class MockServer:
    """스레드에서 도는 목 Open API 1개."""

    url: str
    token: str
    state: Any
    server: Any
    thread: threading.Thread

    def inject(self, faults: Faults | None) -> None:
        """장애 주입을 건다(None = 해제)."""
        self.state.bench_faults = faults

    def hits(self) -> list[dict[str, Any]]:
        with self.state.lock:
            return list(self.state.hits)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def start_mock(
    fixtures: Sequence[Mapping[str, Any]], token: str, fixtures_dir: Path, mode: str = "connected"
) -> MockServer:
    """픽스처를 `fixtures_dir`에 쓰고 목 서버를 127.0.0.1 임시 포트로 띄운다."""
    mock = mock_module()
    _patch_route(mock)
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    for idx, fx in enumerate(fixtures):
        (fixtures_dir / f"fx_{idx:03d}.json").write_text(
            json.dumps(fx, ensure_ascii=False), encoding="utf-8"
        )
    server, state = mock.make_server(fixtures_dir, token, mode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return MockServer(f"http://127.0.0.1:{server.server_address[1]}", token, state, server, thread)


# --- 게이트웨이 서브프로세스 -----------------------------------------------------------------


def dotenv_sources(path: Path = GW_ROOT / ".env") -> list[str]:
    """게이트웨이 `.env`의 `JENNIFER_SOURCES` 소스 id(그 키 한 줄만 · 토큰·URL 은 읽지 않는다)."""
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().partition("=")
        if key.strip() == "JENNIFER_SOURCES":
            try:
                ids = json.loads(value.strip())
            except json.JSONDecodeError:
                return []
            return [str(i) for i in ids if isinstance(i, str)] if isinstance(ids, list) else []
    return []


def gateway_env(
    base: Mapping[str, str],
    *,
    port: int,
    spool: Path,
    tokens: Mapping[str, str],
    sources: Sequence[str],
    mock: MockServer | None = None,
    rate: float = BENCH_RATE_PER_SEC,
    timeout_sec: float = BENCH_API_TIMEOUT_SEC,
    log_level: str = BENCH_LOG_LEVEL,
) -> dict[str, str]:
    """게이트웨이 자식 env — 부모의 `JENNIFER_*`·`APM_*`를 걷어낸 뒤 벤치 값을 명시한다.

    Args:
        base: 부모 env
        port: 게이트웨이 포트(127.0.0.1)
        spool: 작업 스풀 디렉터리(run 임시 디렉터리 — 필수)
        tokens: 주체 → Bearer 토큰
        sources: 소스 id — 속도·타임아웃을 소스별로도 고정한다(E1 은 목 URL·토큰도 소스별로 넣는다)
        mock: E1 목 서버(None = E2 · 소스 URL·토큰은 게이트웨이 `.env`)
        rate: 초당 호출(전역·소스별)
        timeout_sec: 제니퍼 호출 1회 상한(전역·소스별)
        log_level: 게이트웨이 로그 수준(기본 DEBUG — 응답 줄을 가드가 읽는다 · 로그는 세션 임시
            디렉터리에만 남는다)
    """
    env = {k: v for k, v in base.items() if not k.startswith(("JENNIFER_", "APM_"))}
    env.update({
        "JENNIFER_RATE_LIMIT_PER_SEC": str(rate),
        "JENNIFER_API_TIMEOUT_SECONDS": str(timeout_sec),
        "APM_GATEWAY_HOST": "127.0.0.1",
        "APM_GATEWAY_PORT": str(port),
        "APM_GATEWAY_BEARER_TOKEN": "",
        "APM_GATEWAY_BEARER_TOKENS": json.dumps(dict(tokens)),
        "APM_EVENT_POLLER_ENABLED": "false",
        "APM_SPOOL_DIR": str(spool),
        "APM_GATEWAY_LOG_LEVEL": log_level,
        "APM_TIMEZONE": "Asia/Seoul",
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    for sid in sources:
        prefix = f"JENNIFER_{sid.upper()}_"
        env[prefix + "RATE_LIMIT_PER_SEC"] = str(rate)
        env[prefix + "API_TIMEOUT_SECONDS"] = str(timeout_sec)
    if mock is not None:
        env.update({
            "JENNIFER_SOURCES": json.dumps(list(sources)),
            "JENNIFER_API_URL": "",
            "JENNIFER_API_TOKEN": "",
            "JENNIFER_DOMAIN_IDS": "[]",
        })
        for sid in sources:
            env[f"JENNIFER_{sid.upper()}_API_URL"] = mock.url
            env[f"JENNIFER_{sid.upper()}_API_TOKEN"] = mock.token
    return env


@dataclass
class GatewayProc:
    """벤치 게이트웨이 서브프로세스 1개."""

    port: int
    tokens: dict[str, str]
    log_path: Path
    env: dict[str, str]
    proc: subprocess.Popen[bytes] | None = None
    _log: IO[bytes] | None = field(default=None, repr=False)

    @property
    def sse_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/sse"

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    def start(self, timeout_sec: float = GATEWAY_START_TIMEOUT_SEC) -> None:
        """기동 후 포트가 열릴 때까지 기다린다 — 조기 종료면 RuntimeError(로그 끝 일부)."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "ab")  # noqa: SIM115 — 프로세스 수명 동안 연다
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "apm_gateway"], cwd=str(GW_ROOT), env=self.env,
            stdout=self._log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                tail = self.log_path.read_bytes()[-1500:].decode("utf-8", errors="replace")
                self.stop()
                raise RuntimeError(f"게이트웨이 기동 실패(exit {self.proc.returncode}): {tail}")
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.1).close()
                return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError(f"게이트웨이 포트 대기 {timeout_sec:.0f}초 초과")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, grace_sec: float = 15.0) -> None:
        """자기 PID 만 정리한다(terminate → wait → kill)."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(grace_sec)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self._log is not None:
            self._log.close()
            self._log = None

    def call_tool(
        self, name: str, args: Mapping[str, Any] | None = None, *, timeout_sec: float = 60.0,
        principal: str = BENCH_PRINCIPAL,
    ) -> dict[str, Any]:
        """MCP SSE 로 도구 1회 — 봉투 dict(JSON 이 아니면 `{"text": …}` · MCP 오류면 `error` 칸)."""
        import asyncio

        return asyncio.run(self._call(name, dict(args or {}), timeout_sec, self.tokens[principal]))

    async def _call(self, name: str, args: dict[str, Any], timeout_sec: float,
                    token: str) -> dict[str, Any]:
        import asyncio

        from mcp import ClientSession
        from mcp.client.sse import sse_client

        headers = {"Authorization": f"Bearer {token}"}
        async with sse_client(self.sse_url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await asyncio.wait_for(session.call_tool(name, args), timeout_sec)
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {"text": text}
        if not isinstance(body, dict):
            body = {"value": body}
        if result.isError and "error" not in body:
            body["error"] = "mcp_error"
        return body


def start_gateway(
    session: Path,
    *,
    sources: Sequence[str],
    mock: MockServer | None = None,
    rate: float = BENCH_RATE_PER_SEC,
    tokens: Mapping[str, str] | None = None,
    base_env: Mapping[str, str] | None = None,
) -> GatewayProc:
    """벤치 게이트웨이를 띄운다(스풀·로그 = `session` 아래)."""
    import secrets

    port = free_port()
    tokens = dict(tokens or {
        CHAT_PRINCIPAL: "bench-chat-" + secrets.token_hex(8),
        BENCH_PRINCIPAL: "bench-probe-" + secrets.token_hex(8),
    })
    env = gateway_env(
        os.environ if base_env is None else base_env, port=port, spool=session / "spool",
        tokens=tokens, sources=sources, mock=mock, rate=rate,
    )
    proc = GatewayProc(port=port, tokens=tokens, log_path=session / "gateway.log", env=env)
    proc.start()
    return proc


def gateway_var_absent() -> bool:
    """게이트웨이가 기본 스풀(`apm_gateway/var`)을 남기지 않았는가."""
    return not (GW_ROOT / "var").exists()


# --- 감사 줄 tail ---------------------------------------------------------------------------


def _sources(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    if text in ("", "-"):
        return out
    for part in text.split(","):
        sid, _, count = part.partition(":")
        if sid and count.isdigit():
            out[sid] = out.get(sid, 0) + int(count)
    return out


def parse_audit_line(line: str) -> dict[str, Any] | None:
    """감사 1줄 → dict(대상 칸은 싣지 않는다 — 마스킹본이지만 운영 이름 조각일 수 있다)."""
    match = AUDIT_RE.search(line)
    if match is None:
        return None
    rows = match.group("rows")

    def dash(value: str | None) -> str | None:
        return None if value in (None, "-") else value

    return {
        "tool": match.group("tool"),
        "elapsed_ms": float(match.group("elapsed")),
        "api_calls": int(match.group("calls")),
        "sources": _sources(match.group("sources")),
        "rows": int(rows) if rows and rows.lstrip("-").isdigit() else None,
        "error": dash(match.group("error")),
        "thread_id": dash(match.group("thread")),
        "principal": dash(match.group("principal")),
        "job_id": dash(match.group("job")),
    }


def _fold_http(entry: dict[str, Any], statuses: Counter[str]) -> dict[str, Any]:
    if statuses:
        entry["http"] = {"responses": sum(statuses.values()), "statuses": dict(statuses)}
    return entry


class AuditTail:
    """게이트웨이 로그 파일의 감사 줄 — 크기 오프셋(mark)부터 읽는다.

    응답 줄(`HTTP_RE` · DEBUG)은 뒤따르는 감사 항목의 `http` 칸(`{"responses", "statuses"}`)으로
    접는다(응답 줄이 없으면 칸도 없다). 읽은 끝에 감사 줄 없이 남은 응답 줄은 합성 항목(`tool`·
    `principal` None · `api_calls` 0)으로 내 턴 도중에도 가드가 429 를 보게 한다 — `collect`는
    합성 항목을 뺀다.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def mark(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def read(self, since: int) -> tuple[list[dict[str, Any]], int]:
        """`since`부터 끝까지의 감사 항목과 새 오프셋(마지막 완전한 줄까지)."""
        if not self.path.exists():
            return [], since
        with open(self.path, "rb") as handle:
            handle.seek(since)
            data = handle.read()
        cut = data.rfind(b"\n") + 1
        entries: list[dict[str, Any]] = []
        statuses: Counter[str] = Counter()
        for line in data[:cut].decode("utf-8", errors="replace").splitlines():
            entry = parse_audit_line(line)
            if entry is not None:
                entries.append(_fold_http(entry, statuses))
                statuses = Counter()
                continue
            http = HTTP_RE.search(line)
            if http is not None:
                statuses[http.group("status")] += 1
        if statuses:
            entries.append(_fold_http({"tool": None, "elapsed_ms": 0.0, "api_calls": 0,
                                       "sources": {}, "rows": None, "error": None,
                                       "thread_id": None, "principal": None, "job_id": None},
                                      statuses))
        return entries, since + cut

    def collect(self, since: int, thread_id: str | None = None) -> list[dict[str, Any]]:
        """`since` 이후 감사 항목(`thread_id`를 주면 그 스레드 것만 · 합성 항목 제외)."""
        entries = [e for e in self.read(since)[0] if e.get("tool") is not None]
        if thread_id is None:
            return entries
        return [e for e in entries if e.get("thread_id") == thread_id]


__all__ = [
    "GW_ROOT",
    "FORBIDDEN_PORTS",
    "BENCH_RATE_PER_SEC",
    "BENCH_API_TIMEOUT_SEC",
    "CHAT_PRINCIPAL",
    "BENCH_PRINCIPAL",
    "BENCH_LOG_LEVEL",
    "HTTP_RE",
    "free_port",
    "mock_module",
    "synthetic_fixtures",
    "bench_fixtures",
    "Faults",
    "MockServer",
    "start_mock",
    "dotenv_sources",
    "gateway_env",
    "GatewayProc",
    "start_gateway",
    "gateway_var_absent",
    "parse_audit_line",
    "AuditTail",
]
