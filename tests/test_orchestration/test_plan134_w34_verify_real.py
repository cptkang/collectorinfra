"""plans/134 W3·W4 독립 검증(verify-134-w34 · 2026-10-06) — 실프로세스 종단.

합성 제니퍼 Open API(이 프로세스 스레드 · 127.0.0.1 임시 포트 · 소스 `bank`·`common` 각각 별도 서버
· 별도 토큰) ↔ `python -m apm_gateway`(임시 포트 · 임시 스풀 · 폴러 꺼짐) ↔ 실제 MCP SSE ↔ 본체
2단 노드(가짜 LLM 분해 → `agent_orchestrator` → `replanner` → `result_aggregator`).

호출 경로는 세 겹으로 본다.

- 본체 → 게이트웨이: 실제 SSE 세션을 감싼 기록기(`_Recorder` — 본체가 보낸 도구 이름·인자 원본)
- 게이트웨이 감사: 게이트웨이 표준 오류 로그의 `apm audit:` 줄(대상 요약 `targets(N)`)
- 게이트웨이 → 제니퍼: 합성 Open API의 접근 기록(소스별 · 경로 · 쿼리)

FastMCP는 **모르는 인자를 조용히 버린다** — 그래서 인자가 실제로 닿았는지를 접근 기록·결과 모양으로
가른다. 실 제니퍼·외부 네트워크·실 LLM·실 DB·Redis 0 · 포트 9096·9097·9099·8080 미사용.

조회용 이벤트 버퍼(A-7)는 폴러(Redis 필요)가 있어야 차므로 이 실프로세스에서는 꺼져 있다 —
버퍼 경로는 게이트웨이 패키지의 in-process 검증
(`apm_gateway/tests/test_plan134_w34_verify.py`)이 맡는다.

결함 재현은 `xfail(strict=True, reason="V34-n …")`이다(고치면 XPASS로 실패하니 표지를 걷는다).
"""

from __future__ import annotations

import asyncio
import heapq
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import defaultdict
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from src.clients import source_mcp_client as smc
from src.config import AppConfig, CompositeConfig, DBHubConfig, LLMConfig, ServerConfig
from src.domain import disclosure as disc
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_jobs
from src.orchestration import apm_query as aq
from src.orchestration.entity_link import UNLINKED, LinkEntry
from tests.test_orchestration.test_plan134_w567_body_verify import (
    FORBIDDEN_PORTS,
    GW_ROOT,
    _fresh,
    _two_tier_turn,
)

pytestmark = pytest.mark.asyncio

_SCRIPTS = GW_ROOT / "testdata" / "jennifer" / "scripts"
_CATALOG_FX = GW_ROOT / "testdata" / "jennifer" / "recorded" / "local-docker" / \
    "GET_api_metrics__ok.json"
_GW_TOKEN = "tok-chat-v34-8Rq"
_TOKENS = {"bank": "tok-bank-SECRET-v34-1Aa", "common": "tok-common-SECRET-v34-2Bb"}
#: 대상 해석·인벤토리 경로(데이터 조회 아님)
_RESOLUTION_PATHS = {"/api/domain", "/api/instance", "/api/business", "/api/metrics"}
#: 서비스(도메인) 이름 — 도메인 1·2·3은 이름으로 찾는 서비스, 나머지는 순위·이벤트 규모용
_NAMED = {1: "주문", 2: "결제", 3: "주문배치"}
_DOMAINS = 120
_HOSTS = [f"web{i:02d}" for i in range(1, 13)]
#: 업무 정의(도메인 → [(업무 id, 이름)])
_BUSINESS = {1: [(7, "주문처리"), (9, "결제승인")], 2: [(11, "결제"), (12, "환불")]}
_T_NOW = int(time.time() * 1000)


def _catalog_body() -> dict:
    return json.loads(_CATALOG_FX.read_text(encoding="utf-8"))["response"]["body_json"]


# ── 합성 제니퍼(소스 1개 = 서버 1개) ───────────────────────────────────────────────

class _Model:
    """소스 1개의 합성 제니퍼 — 필드 이름은 Open API 5.6.4 스키마 이름이다(합성)."""

    def __init__(self, sid: str, token: str, *, hosts: list[str], domains: int = _DOMAINS,
                 hostless: bool = False) -> None:
        self.sid, self.token = sid, token
        self.domains = {d: _NAMED.get(d, f"{sid}-svc-{d:03d}") for d in range(1, domains + 1)}
        self.instances: dict[int, list[dict]] = defaultdict(list)
        self.realtime: dict[int, list[dict]] = defaultdict(list)
        self.events: dict[int, list[dict]] = defaultdict(list)
        self.fail: dict[str, set[int]] = defaultdict(set)
        self.delay: dict[str, float] = {}
        self.hits: list[dict] = []
        self.lock = threading.Lock()
        base = 1 if sid == "bank" else 2
        for k, host in enumerate(hosts, 1):  # 도메인 1(주문) — 이름 있는 서버
            self._add(1, 100 + k, f"{host}-was", host, rt=float(base * 1000 + k * 10))
        if hostless:  # 호스트 정합이 안 되는 인스턴스(hostName 빈 값)
            self._add(1, 199, "batch-was", "", rt=float(base * 1000 + 999))
        for d in range(2, domains + 1):
            for k in range(2):
                iid = d * 100 + k
                value = float(((d * 37 + k * 11 + base * 5) % 997) + 1)
                if (d + k) % 29 == 0:
                    value = None  # 응답시간 칸 없음 → 순위에서 빠진다
                self._add(d, iid, f"{sid}-i{d}-{k}", f"{sid}-h{d}-{k}", rt=value)
        self.rt_domain = {d: {"domainId": d, "domainName": n, "tps": float(d), "responseTime":
                              float(d * 3), "activeService": d % 7, "activeUser": d,
                              "concurrentUser": float(d), "rejectRate": 0.0, "visitDay": d * 10,
                              "visitHour": d, "hitDay": d * 20, "hitHour": d * 2,
                              "activeServiceRangeCount0": 1, "activeServiceRangeCount1": 0,
                              "activeServiceRangeCount2": 0, "activeServiceRangeCount3": 0,
                              "ipAddress": "10.9.9.9", "port": 5555}
                          for d, n in self.domains.items()}
        self.business = {d: [{"businessId": b, "name": n, "description": f"{n} 담당 kim@x.io",
                              "badResponseTime": 3000, "businessIndex": str(b),
                              "businessOid": 900 + b, "ruleList": ["/pay/*"]}
                             for b, n in items] for d, items in _BUSINESS.items()}
        self.rt_business = {d: [{"domainId": d, "businessId": b, "businessName": n,
                                 "tps": float(b) + base, "responseTime": float(b * 100 + base),
                                 "activeService": b, "concurrentUser": 1.0,
                                 "activeServiceRangeCount0": 0, "activeServiceRangeCount1": 0,
                                 "activeServiceRangeCount2": 0, "activeServiceRangeCount3": 0}
                                for b, n in items] for d, items in _BUSINESS.items()}
        self.catalog = _catalog_body()

    def _add(self, d: int, iid: int, name: str, host: str, *, rt: float | None) -> None:
        self.instances[d].append({"instanceId": iid, "name": name, "hostName": host,
                                  "ipAddress": "10.0.0.1", "platform": "JAVA",
                                  "status": "RUNNING", "version": "5.6.5"})
        raw: dict[str, Any] = {"domainId": d, "instanceId": iid, "instanceName": name,
                               "tps": float(iid % 50), "activeService": iid % 5,
                               "activeDBConnection": 2, "averageDbPoolConfiguredCount": 10,
                               "threadCurrent": 30, "visitDay": 1, "instanceOid": 70_000 + iid}
        if rt is not None:
            raw["responseTime"] = rt
        self.realtime[d].append(raw)

    def realtime_value(self, d: int, iid: int) -> float | None:
        for r in self.realtime[d]:
            if r["instanceId"] == iid:
                return r.get("responseTime")
        return None

    def hits_of(self, path: str | None = None) -> list[dict]:
        with self.lock:
            return [h for h in self.hits if path is None or h["path"] == path]

    def clear(self) -> None:
        with self.lock:
            self.hits.clear()

    # ── 응답 ──
    def respond(self, template: str, query: dict[str, str], path: str) -> tuple[int, Any]:
        sys.path.insert(0, str(_SCRIPTS)) if str(_SCRIPTS) not in sys.path else None
        import mock_openapi

        if template in mock_openapi.W7_TEMPLATES:  # W7 합성 응답(카나리아 비밀 포함)
            status, body, _ = mock_openapi.w7_response(self, "GET", template, query, path)
            return status, body
        d = int(query["domain_id"]) if query.get("domain_id", "").isdigit() else None
        if d is not None and d in self.fail.get(template, ()):
            return 500, {"exception": {"message": f"{d} Domain is not connected"}}
        ids = {int(x) for x in query.get("instance_id", "").split(",") if x.strip().isdigit()}
        if template == "/api/domain":
            return 200, {"result": [{"domainId": k, "name": v} for k, v in self.domains.items()]}
        if template == "/api/instance":
            return 200, {"result": list(self.instances.get(d or -1, []))}
        if template == "/api/realtime/instance":
            rows = [r for r in self.realtime.get(d or -1, []) if not ids or r["instanceId"] in ids]
            return 200, {"result": rows}
        if template == "/api/activeService/list":
            rows = [{"domainId": d, "instanceId": i["instanceId"],
                     "instanceName": i["name"], "application": "/order", "status": "RUNNING",
                     "txid": str(i["instanceId"] * 10), "startTime": _T_NOW - 1000,
                     "elapseTime": 1500, "runningMode": "SQL", "sessionId": 1,
                     "threadHash": 2}
                    for i in self.instances.get(d or -1, []) if not ids or i["instanceId"] in ids]
            return 200, {"result": rows}
        if template == "/api/dbsearch/event":
            lo, hi = int(query["start_time"]), int(query["end_time"])
            rows = [e for e in self.events.get(d or -1, []) if lo <= int(e["time"]) <= hi
                    and (not ids or e["instanceId"] in ids)]
            if query.get("level"):
                rows = [e for e in rows if e["eventLevel"].upper() == query["level"].upper()]
            return 200, {"result": rows}
        if template in ("/api/dbsearch/error", "/api/transaction/time"):
            return 200, {"result": []}
        if template == "/api/realtime/domain":
            rows = list(self.rt_domain.values()) if d is None else [self.rt_domain[d]] \
                if d in self.rt_domain else []
            return 200, {"result": rows}
        if template == "/api/business":
            return 200, {"result": list(self.business.get(d or -1, []))}
        if template == "/api/realtime/business":
            rows = list(self.rt_business.get(d or -1, []))
            if query.get("business_id"):
                rows = [r for r in rows if str(r["businessId"]) == query["business_id"]]
            return 200, {"result": rows}
        if template == "/api/metrics":
            return 200, self.catalog
        if template.startswith("/api/dbmetrics/"):
            lo = int(query["start_time"])
            step = int(query["interval_minute"]) * 60_000
            return 200, {"result": [{"time": lo + k * step, "value": float(k + 1)}
                                    for k in range(3)]}
        return 501, {"mock_error": f"합성 응답 없음: {template}"}


def _handler(model: _Model) -> type[BaseHTTPRequestHandler]:
    sys.path.insert(0, str(_SCRIPTS)) if str(_SCRIPTS) not in sys.path else None
    from jennifer_catalog import ALLOWED, match_template, missing_param_message

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def _send(self, status: int, body: Any) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json;charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            split = urllib.parse.urlsplit(self.path)
            query = {k: v[-1] for k, v in urllib.parse.parse_qs(
                split.query, keep_blank_values=True).items()}
            template = match_template(split.path)
            authed = self.headers.get("Authorization", "") == f"Bearer {model.token}"
            hit = {"source": model.sid, "path": split.path, "template": template,
                   "query": {k: ("***" if k.lower() == "token" else v) for k, v in query.items()},
                   "authed": authed, "ts": time.time()}
            with model.lock:
                model.hits.append(hit)
            if not authed:
                return self._send(401, "401 unauthorized")
            if template is None or "token" in query:
                return self._send(404, {"error": "허용목록 밖"})
            for name in ALLOWED[template].required:
                if name not in query:
                    return self._send(500, {"exception": {"message": missing_param_message(name)}})
            delay = model.delay.get(template)
            if delay:
                time.sleep(delay)
            status, body = model.respond(template, query, split.path)
            hit["status"] = status
            self._send(status, body)

        def do_POST(self) -> None:  # noqa: N802
            with model.lock:
                model.hits.append({"source": model.sid, "path": self.path, "method": "POST"})
            self._send(405, {"error": "쓰기 금지"})

    return Handler


def _free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in FORBIDDEN_PORTS:
            return port


class _Stack:
    """합성 제니퍼 2대 + 게이트웨이 실프로세스."""

    def __init__(self, models: dict[str, _Model], tmp: Path, extra_env: dict[str, str]) -> None:
        self.models = models
        self.servers = []
        for model in models.values():
            server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(model))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            model.url = f"http://127.0.0.1:{server.server_address[1]}"  # type: ignore[attr-defined]
            self.servers.append(server)
        self.port = _free_port()
        self.spool = tmp / "spool"
        self.log_path = tmp / "gateway.log"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("JENNIFER_", "APM_"))}
        env.update({
            "JENNIFER_SOURCES": json.dumps(list(models)), "JENNIFER_API_URL": "",
            "JENNIFER_API_TOKEN": "", "JENNIFER_DOMAIN_IDS": "[]",
            "JENNIFER_RATE_LIMIT_PER_SEC": "400",
            "APM_GATEWAY_HOST": "127.0.0.1", "APM_GATEWAY_PORT": str(self.port),
            "APM_GATEWAY_BEARER_TOKEN": "",
            "APM_GATEWAY_BEARER_TOKENS": json.dumps({"chat": _GW_TOKEN}),
            "APM_EVENT_POLLER_ENABLED": "false", "APM_SPOOL_DIR": str(self.spool),
            "APM_GATEWAY_LOG_LEVEL": "INFO", "APM_TIMEZONE": "Asia/Seoul",
            "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "1",
        })
        for sid, model in models.items():
            env[f"JENNIFER_{sid.upper()}_API_URL"] = model.url  # type: ignore[attr-defined]
            env[f"JENNIFER_{sid.upper()}_API_TOKEN"] = model.token
        env.update(extra_env)
        self._log = self.log_path.open("wb")
        self.proc = subprocess.Popen([sys.executable, "-m", "apm_gateway"], cwd=GW_ROOT, env=env,
                                     stdout=self._log, stderr=subprocess.STDOUT)
        for _ in range(300):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.1).close()
                break
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"게이트웨이 기동 실패: {self.log_text()[-2000:]}")
                time.sleep(0.1)
        self.url = f"http://127.0.0.1:{self.port}/sse"
        self._settle_warm_up()

    def _settle_warm_up(self) -> None:
        """기동 직후 인벤토리 선적재(`resolver.warm_up` — 종전 동작)가 끝날 때까지 기다리고 접근
        기록을 비운다 — 시나리오의 접근 기록에는 그 질의가 부른 것만 남는다."""
        want = {sid: 1 + len(m.domains) for sid, m in self.models.items()}
        for _ in range(200):
            if all(len(m.hits_of()) >= want[sid] for sid, m in self.models.items()):
                break
            time.sleep(0.05)
        time.sleep(0.2)
        self.clear()

    def log_text(self) -> str:
        self._log.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def audits(self, since: int = 0) -> list[str]:
        return [ln for ln in self.log_text().splitlines()[since:] if "apm audit:" in ln]

    def log_mark(self) -> int:
        return len(self.log_text().splitlines())

    def clear(self) -> None:
        for model in self.models.values():
            model.clear()

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self._log.close()
        for server in self.servers:
            server.shutdown()
            server.server_close()


def _models(hostless: bool = False) -> dict[str, _Model]:
    return {"bank": _Model("bank", _TOKENS["bank"], hosts=_HOSTS, hostless=hostless),
            "common": _Model("common", _TOKENS["common"], hosts=_HOSTS[:4])}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    pytest.importorskip("mcp")
    s = _Stack(_models(), tmp_path_factory.mktemp("v34"), {})
    try:
        yield s
    finally:
        s.close()


# ── 본체 쪽 ───────────────────────────────────────────────────────────────────

class _Recorder:
    """본체가 실제 SSE 세션으로 보낸 도구 호출(이름 · None을 뺀 인자 원본)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.opened = 0

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            async with smc._mcp_sse_session(url, headers) as session:
                yield _Tap(session, self)

        return open_

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]

    def data_calls(self) -> list[tuple[str, dict]]:
        return [(n, a) for n, a in self.calls if not n.startswith("apm_job_")]


class _Tap:
    def __init__(self, session: Any, rec: _Recorder) -> None:
        self._s, self._rec = session, rec

    async def call_tool(self, name: str, arguments: dict) -> Any:
        self._rec.calls.append((name, json.loads(json.dumps(arguments))))
        return await self._s.call_tool(name, arguments)


class _NoEdge:
    """업무명 간선(E6) 0건 — 실 DB를 읽지 않는다."""

    async def __call__(self, terms, *, app_config, authorized_db_ids):
        entries = [LinkEntry(t, "business_name", "E6", UNLINKED, grade="none") for t in terms]
        return {t: [] for t in terms}, entries, []


def _config(url: str, *, call_timeout: float = 60.0, query_timeout: int = 120,
            reserve: int = 15, top_n: int = 20) -> AppConfig:
    return AppConfig(
        _env_file=None,
        composite=CompositeConfig(_env_file=None, apm_untargeted_top_n=top_n),
        llm=LLMConfig(_env_file=None, provider="ollama", model="none",
                      ollama_base_url="http://127.0.0.1:9"),
        dbhub=DBHubConfig(_env_file=None, server_url="http://127.0.0.1:9/sse",
                          source_name="infra_db", source_endpoints={"apm": url},
                          source_tokens=json.dumps({"apm": _GW_TOKEN}),
                          source_call_timeout=call_timeout),
        server=ServerConfig(_env_file=None, query_timeout=query_timeout,
                            answer_reserve_sec=reserve),
        checkpoint_backend="sqlite", checkpoint_db_url=":memory:",
    )


@pytest.fixture
def live(stack, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", rec.factory())
    monkeypatch.setattr(aq, "link_business_names", _NoEdge())
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    stack.clear()
    return stack, rec


def _task(views: list[str], *, targets: list[dict] | None = None,
          view_args: dict | None = None, sources: list[str] | None = None,
          sub: str = "q") -> dict:
    out: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "sub_query": sub,
                           "views": views, "depends_on": [], "input_from": [], "order": 1}
    if targets is not None:
        out["targets"] = targets
    if view_args:
        out["view_args"] = view_args
    if sources is not None:
        out["sources"] = sources
    return out


async def _turn(stack: _Stack, query: str, task: dict, *, hosts: tuple[str, ...] = (),
                config: AppConfig | None = None, thread: str = "th-v34") -> dict:
    state = _fresh(query, thread)
    return await _two_tier_turn(state, query, [task], config or _config(stack.url), hosts=hosts)


def _apm_result(state: dict) -> dict:
    return (state.get("task_results") or {}).get("t1") or {}


def _final(state: dict) -> str:
    return str(state.get("final_response") or "")


def _judgement_block(text: str) -> str:
    m = re.search(r"\*\*판정·집계\*\*(.*?)(?:\n\*\*|\Z)", text, re.S)
    return m.group(1) if m else ""


def _data_hits(model: _Model) -> list[dict]:
    return [h for h in model.hits_of() if h["path"] not in _RESOLUTION_PATHS]


# ── E1: hostname 12개 × 보기 4개 = 보기마다 배치 1호출 ───────────────────────────────

_E1_VIEWS = ["apm.app_health", "apm.pool", "apm.active", "apm.events"]


async def test_e1_twelve_hosts_four_views_one_batch_each(live) -> None:
    """plans/125 A-6 ② 증거 — hostname 12개(종전 상한 10 초과) · 보기 4개(종전 상한 3 초과)."""
    stack, rec = live
    mark = stack.log_mark()
    state = await _turn(stack, "web01~web12 응답시간·커넥션 풀·실행 중 서비스·이벤트",
                        _task(_E1_VIEWS), hosts=tuple(_HOSTS))
    res = _apm_result(state)
    # 본체 → 게이트웨이: 보기마다 1호출 · targets 12항목(hostname) · 최상위 대상 인자 없음
    data = rec.data_calls()
    assert [n for n, _ in data] == ["apm_app_health", "apm_resource_pool",
                                    "apm_active_services", "apm_events"], data
    for name, args in data:
        assert args["targets"] == [{"hostname": h} for h in _HOSTS], name
        assert not {"hostname", "instance_name", "instance_id"} & set(args), name
    # 게이트웨이 감사: 배치 단위 1줄 · 대상 요약 targets(12)(대상 원문 없음)
    audits = [a for a in stack.audits(mark) if "tool=apm_job" not in a]
    assert len(audits) == 4 and all("target=targets(12)" in a for a in audits), audits
    assert not any(h in a for a in audits for h in ("web07", "web12")), "감사에 대상 원문 없음"
    # 게이트웨이 → 제니퍼: 대상마다 실시간 조회(도메인 1 · 그 인스턴스) · common 소스도 web01~04
    rt_bank = stack.models["bank"].hits_of("/api/realtime/instance")
    assert {h["query"].get("instance_id") for h in rt_bank} >= {str(100 + k) for k in range(1, 13)}
    # 대상별 복원 — 행·집계·출처가 대상별
    rows = state.get("query_results") or []
    health = [r for r in rows if r.get("response_time_avg_ms") is not None and "db_pool_active"
              not in r and r.get("source_id") == "bank"]
    by_host = {r["hostname"]: r["response_time_avg_ms"] for r in health}
    for k, host in enumerate(_HOSTS, 1):
        assert by_host.get(host) == 1000 + k * 10, (host, by_host)
    meta = res.get("apm_query") or {}
    assert [b["targets"] for b in meta.get("batches") or []] == [12, 12, 12, 12]
    assert all(b["failed"] == 0 for b in meta["batches"])
    assert meta["hostnames"] == _HOSTS
    # 최종 답(CSV = 저장 `query_results`)에 대상별 값 — web12 응답시간 1120
    final = _final(state)
    assert "web12" in final, final[:3000]
    assert any(r.get("hostname") == "web12" and r.get("response_time_avg_ms") == 1120.0
               for r in rows)


async def test_e1_batch_partial_failure_is_per_target_not_zero(live) -> None:
    """배치 일부 실패 — 실패 대상은 meta failures(대상별) · 나머지 대상은 값 · 0건으로 안 셈."""
    stack, rec = live
    hosts = ("web01", "web02", "nohost-zz")
    state = await _turn(stack, "web01 web02 nohost-zz 응답시간", _task(["apm.app_health"]),
                        hosts=hosts)
    res = _apm_result(state)
    (args,) = rec.named("apm_app_health")
    assert args["targets"] == [{"hostname": h} for h in hosts]
    fails = (res.get("apm_query") or {}).get("failures") or []
    assert [f.get("hostname") for f in fails] == ["nohost-zz"], fails
    assert res["source_status"][0]["status"] == "partial", res["source_status"]
    assert {r["hostname"] for r in res["query_results"]} >= {"web01", "web02"}
    # 명시 hostname 정합 실패 = 그 대상의 실패로 끝 — 첫 홉(인스턴스 목록) 대체 없음
    assert [n for n, _ in rec.data_calls()] == ["apm_app_health"], rec.calls


# ── E2: 전 대상 순위 ───────────────────────────────────────────────────────────

def _oracle(stack: _Stack, *, n: int, failed: set[tuple[str, int]] = frozenset(),
            domains: set[int] | None = None) -> list[tuple[str, int, int, float]]:
    """독립 오라클 — 합성 원천 값에서 직접 (값 내림차순 · 소스 순서 · 도메인 · 인스턴스)."""
    order = {"bank": 0, "common": 1}
    items = []
    for sid, model in stack.models.items():
        for d, recs in model.realtime.items():
            if (sid, d) in failed or (domains is not None and d not in domains):
                continue
            for r in recs:
                if r.get("responseTime") is not None:
                    items.append((-r["responseTime"], order[sid], d, r["instanceId"], sid))
    return [(sid, d, iid, -neg) for neg, _, d, iid, sid in heapq.nsmallest(n, items)]


async def test_e2_ranking_provisional_and_independent_oracle(live) -> None:
    stack, rec = live
    stack.models["bank"].fail["/api/realtime/instance"].add(50)
    try:
        state = await _turn(stack, "전체 WAS 중 응답시간 가장 느린 7개",
                            _task(["apm.ranking"], view_args={"apm.ranking": {"n": 7}}))
    finally:
        stack.models["bank"].fail["/api/realtime/instance"].discard(50)
    res = _apm_result(state)
    (args,) = rec.named("apm_fleet")
    assert args["mode"] == "ranking" and args["n"] == 7 and "targets" not in args
    rt = stack.models["bank"].hits_of("/api/realtime/instance") + \
        stack.models["common"].hits_of("/api/realtime/instance")
    assert len(rt) == 2 * _DOMAINS, "도메인마다 1호출 × 소스 2"
    assert all("instance_id" not in h["query"] for h in rt)
    got = [(r["source_id"], r["domain_id"], r["instance_id"], r["value"])
           for r in res["query_results"]]
    assert got == _oracle(stack, n=7, failed={("bank", 50)}), got
    block = _judgement_block(_final(state))
    assert "잠정 순위 — 조회 실패 도메인 1곳 제외" in block, _final(state)[-3000:]
    assert "전체 인스턴스" in block and "상위 7" in block
    kinds = {d["kind"] for d in state.get("disclosures") or res.get("disclosures") or []}
    assert disc.APM_PARTIAL_SOURCES in kinds, kinds


async def test_e2_ranking_narrowed_by_service(live) -> None:
    stack, rec = live
    state = await _turn(stack, "주문 서비스에서 응답시간 가장 느린 WAS 3개",
                        _task(["apm.ranking"],
                              view_args={"apm.ranking": {"n": 3, "service": "주문"}}))
    res = _apm_result(state)
    (args,) = rec.named("apm_fleet")
    assert args.get("service") == "주문", args
    rt = [h for m in stack.models.values() for h in m.hits_of("/api/realtime/instance")]
    # 주문(도메인 1)만 — 주문배치는 prefix 단계도 아니다(구분자 없음)
    assert {h["query"]["domain_id"] for h in rt} == {"1"}
    got = [(r["source_id"], r["domain_id"], r["instance_id"]) for r in res["query_results"]]
    assert got == [(s, d, i) for s, d, i, _ in _oracle(stack, n=3, domains={1})], got


async def test_e2_ranking_unresolved_service_is_not_found_not_whole_fleet(live) -> None:
    """서비스 이름을 못 찾으면 전 도메인으로 넓히지 않는다(실시간 호출 0) — 답에 「찾지 못함」."""
    stack, rec = live
    state = await _turn(stack, "없는서비스 중 가장 느린 WAS",
                        _task(["apm.ranking"],
                              view_args={"apm.ranking": {"service": "없는서비스zz"}}))
    rt = [h for m in stack.models.values() for h in m.hits_of("/api/realtime/instance")]
    assert rt == [], "데이터 API 0"
    final = _final(state)
    assert "찾지 못" in final, final[-2500:]


# ── E3: 전 대상 이벤트(API 경로 — 실프로세스는 폴러·버퍼 꺼짐) ──────────────────────────

def _event(d: int, iid: int, t: int, level: str, etype: str = "ERROR_SERVICE_QUEUING") -> dict:
    return {"domainId": d, "domainName": "", "instanceId": iid, "instanceName": f"i{iid}",
            "errorType": etype, "metricsName": "", "eventLevel": level,
            "message": "queue full user=kim@example.com", "value": 1.0, "time": str(t),
            "txid": "", "applicationName": "/order", "instanceOid": 1}


async def test_e3_fleet_events_api_path_failed_domain_is_not_zero(live) -> None:
    stack, rec = live
    now = int(time.time() * 1000)
    bank, common = stack.models["bank"], stack.models["common"]
    bank.events[5] = [_event(5, 500, now - 60_000, "FATAL"), _event(5, 501, now - 90_000,
                                                                    "WARNING")]
    common.events[9] = [_event(9, 900, now - 30_000, "FATAL")]
    common.fail["/api/dbsearch/event"].add(7)
    try:
        state = await _turn(stack, "최근 30분 fatal 난 WAS 전부",
                            _task(["apm.fleet_events"], view_args={"apm.fleet_events": {
                                "level": "fatal", "level_mode": "exact", "full": True}}))
    finally:
        common.fail["/api/dbsearch/event"].discard(7)
        bank.events.pop(5, None)
        common.events.pop(9, None)
    res = _apm_result(state)
    (args,) = rec.named("apm_fleet")
    assert args["mode"] == "events" and args["level"] == "fatal"
    ev = [h for m in stack.models.values() for h in m.hits_of("/api/dbsearch/event")]
    assert len(ev) == 2 * _DOMAINS and all("level" not in h["query"] for h in ev)
    rows = res["query_results"]
    assert sorted((r["source_id"], r["domain_id"], r["instance_id"]) for r in rows) == [
        ("bank", 5, 500), ("common", 9, 900)], rows
    block = _judgement_block(_final(state))
    assert "이벤트 2건" in block and f"도메인 {2 * _DOMAINS}곳" in block, block
    assert "실패 1" in block and "0건이 아니라 확인하지 못함" in block, block
    assert res["source_status"][0]["status"] == "partial"


# ── E4: 서비스·업무 ─────────────────────────────────────────────────────────────

async def test_e4_service_current_by_name(live) -> None:
    stack, rec = live
    state = await _turn(stack, "주문 서비스 지금 상태 어때?",
                        _task(["apm.service"], targets=[{"text": "주문", "kind": "auto"}]))
    res = _apm_result(state)
    (args,) = rec.named("apm_service_status")
    assert args["service"] == ["주문"], args
    rd = [h for m in stack.models.values() for h in m.hits_of("/api/realtime/domain")]
    assert sorted((h["source"], h["query"].get("domain_id")) for h in rd) == [
        ("bank", "1"), ("common", "1")]
    rows = res["query_results"]
    assert sorted((r["source_id"], r["domain_id"], r["domain_name"]) for r in rows) == [
        ("bank", 1, "주문"), ("common", 1, "주문")]
    assert all(r["match_tier"] == "exact" for r in rows)


async def test_e4_service_without_name_is_every_domain_one_call_per_source(live) -> None:
    stack, rec = live
    state = await _turn(stack, "서비스별 현재 TPS", _task(["apm.service"]))
    (args,) = rec.named("apm_service_status")
    assert "service" not in args
    rd = [h for m in stack.models.values() for h in m.hits_of("/api/realtime/domain")]
    assert sorted(h["source"] for h in rd) == ["bank", "common"]
    assert all("domain_id" not in h["query"] for h in rd)
    assert len(_apm_result(state)["query_results"]) == 2 * _DOMAINS


async def test_e4_service_trend_with_metric(live) -> None:
    stack, rec = live
    state = await _turn(stack, "주문 서비스 지난 3시간 응답시간 추세",
                        _task(["apm.service_trend"], targets=[{"text": "주문", "kind": "auto"}],
                              view_args={"apm.service_trend": {
                                  "metrics": ["response_time_avg_ms"], "interval_minute": 10}}))
    res = _apm_result(state)
    (args,) = rec.named("apm_metrics")
    assert args["mode"] == "series" and args["scope"] == "domain" and args["service"] == ["주문"]
    dm = [h for m in stack.models.values() for h in m.hits_of("/api/dbmetrics/domain")]
    assert sorted((h["source"], h["query"]["domain_id"], h["query"]["metrics"],
                   h["query"]["interval_minute"]) for h in dm) == [
        ("bank", "1", "service_time", "10"), ("common", "1", "service_time", "10")]
    assert len(res["query_results"]) == 6, res["query_results"]


async def test_v34_1_trend_without_metric_is_answered(live) -> None:
    stack, rec = live
    state = await _turn(stack, "주문 서비스 지난 3시간 TPS 추세 10분 간격",
                        _task(["apm.service_trend"], targets=[{"text": "주문", "kind": "auto"}],
                              view_args={"apm.service_trend": {"interval_minute": 10}}))
    res = _apm_result(state)
    assert not res.get("error"), res.get("error") or res.get("final_response")
    assert res["source_status"][0]["status"] in ("ok", "partial"), res["source_status"]


async def test_e4_business_current_and_list(live) -> None:
    stack, rec = live
    state = await _turn(stack, "결제 업무 지금 TPS",
                        _task(["apm.business"], targets=[{"text": "결제", "kind": "business"}]))
    res = _apm_result(state)
    (args,) = rec.named("apm_business")
    assert args["business"] == ["결제"] and args.get("mode", "current") == "current"
    rb = [h for m in stack.models.values() for h in m.hits_of("/api/realtime/business")]
    assert sorted((h["source"], h["query"]["domain_id"], h["query"].get("business_id"))
                  for h in rb) == [("bank", "2", "11"), ("common", "2", "11")]
    rows = res["query_results"]
    assert sorted((r["source_id"], r["business_id"], r["tps"]) for r in rows) == [
        ("bank", 11, 12.0), ("common", 11, 13.0)]
    rec.calls.clear()
    stack.clear()
    state = await _turn(stack, "업무 목록 보여줘",
                        _task(["apm.business"], view_args={"apm.business": {"mode": "list"}}))
    (args,) = rec.named("apm_business")
    assert args["mode"] == "list" and "business" not in args
    rows = _apm_result(state)["query_results"]
    assert len(rows) == 2 * 4 and all("business_oid" not in r for r in rows)
    assert all("kim@x.io" not in str(r.get("business_description")) for r in rows), rows[:2]


async def test_e4_business_trend_with_metric(live) -> None:
    stack, rec = live
    state = await _turn(stack, "결제 업무 지난 3시간 응답시간 추세",
                        _task(["apm.business_trend"],
                              targets=[{"text": "결제", "kind": "business"}],
                              view_args={"apm.business_trend": {
                                  "metrics": ["response_time_avg_ms"]}}))
    res = _apm_result(state)
    (args,) = rec.named("apm_metrics")
    assert args["scope"] == "business" and args["business"] == ["결제"]
    bm = [h for m in stack.models.values() for h in m.hits_of("/api/dbmetrics/business")]
    assert sorted((h["source"], h["query"]["business_id"]) for h in bm) == [
        ("bank", "11"), ("common", "11")]
    assert res["source_status"][0]["status"] == "ok", res["source_status"]


@pytest.mark.parametrize("view,kind,data_path", [
    ("apm.service", "auto", "/api/realtime/domain"),
    ("apm.business", "business", "/api/realtime/business"),
])
async def test_e4_every_name_unresolved_is_not_found_with_zero_data_calls(live, view, kind,
                                                                          data_path) -> None:
    stack, rec = live
    state = await _turn(stack, "없는이름zz 상태",
                        _task([view], targets=[{"text": "없는이름zz", "kind": kind}]))
    res = _apm_result(state)
    assert res.get("degraded_reason") == "apm_target_unresolved", res
    assert "찾지 못" in _final(state)
    for model in stack.models.values():
        assert model.hits_of(data_path) == [], (view, data_path)
        assert {h["path"] for h in model.hits_of()} <= _RESOLUTION_PATHS


async def test_e4_partially_unresolved_names_query_only_the_found(live) -> None:
    stack, rec = live
    state = await _turn(stack, "주문, 없는이름zz 서비스 상태",
                        _task(["apm.service"], targets=[{"text": "주문", "kind": "auto"},
                                                        {"text": "없는이름zz", "kind": "auto"}]))
    res = _apm_result(state)
    (args,) = rec.named("apm_service_status")
    assert args["service"] == ["주문", "없는이름zz"]
    rd = [h for m in stack.models.values() for h in m.hits_of("/api/realtime/domain")]
    assert {h["query"].get("domain_id") for h in rd} == {"1"}, "찾은 도메인만"
    assert {(r["source_id"], r["domain_id"]) for r in res["query_results"]} == {
        ("bank", 1), ("common", 1)}
    texts = [d["text"] for d in res.get("disclosures") or []
             if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    assert any("없는이름zz" in t for t in texts), texts


# ── E6: 소스 선택 ──────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_stack(tmp_path, monkeypatch):
    """캐시가 빈 게이트웨이(소스 선택이 인벤토리 적재까지 좁히는지 보려고)."""
    pytest.importorskip("mcp")
    s = _Stack(_models(), tmp_path, {})
    rec = _Recorder()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", rec.factory())
    monkeypatch.setattr(aq, "link_business_names", _NoEdge())
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    try:
        yield s, rec
    finally:
        s.close()


async def test_e6_sources_bank_never_touches_common(fresh_stack) -> None:
    stack, rec = fresh_stack
    state = await _turn(stack, "은행존 제니퍼에서 web01 web02 응답시간과 주문 서비스 상태",
                        _task(["apm.app_health", "apm.service"],
                              targets=[{"text": "주문", "kind": "auto"}], sources=["bank"]),
                        hosts=("web01", "web02"))
    res = _apm_result(state)
    for name, args in rec.data_calls():
        assert args.get("source_ids") == ["bank"], (name, args)
    assert stack.models["common"].hits_of() == [], "다른 소스 호출 0(인벤토리 포함)"
    assert {r["source_id"] for r in res["query_results"]} == {"bank"}
    assert (res.get("apm_query") or {}).get("source_selection") == {"given": ["bank"],
                                                                    "used": ["bank"]}


async def test_e6_every_source_invalid_asks_back_without_the_gateway(fresh_stack) -> None:
    stack, rec = fresh_stack
    mark = stack.log_mark()
    state = await _turn(stack, "레거시2 제니퍼에서 web01 응답시간",
                        _task(["apm.app_health"], sources=["nowhere"]), hosts=("web01",))
    res = _apm_result(state)
    assert rec.opened == 0 and rec.calls == []
    assert stack.audits(mark) == []
    assert all(m.hits_of() == [] for m in stack.models.values())
    assert "찾지 못해 조회하지 않았습니다" in str(res.get("final_response")), res
    assert "「은행존 제니퍼」" in str(res.get("final_response"))


async def test_e6_registry_source_not_on_gateway_is_a_gateway_error_not_silent(fresh_stack) -> None:
    """레지스트리에는 있으나 게이트웨이에 설정되지 않은 소스(legacy) — 조용한 0건이 아니다."""
    stack, rec = fresh_stack
    state = await _turn(stack, "레거시 제니퍼에서 web01 응답시간",
                        _task(["apm.app_health"], sources=["legacy"]), hosts=("web01",))
    res = _apm_result(state)
    assert rec.named("apm_app_health")[0]["source_ids"] == ["legacy"]
    assert res.get("source_status", [{}])[0].get("status") != "ok", res
    assert all(m.hits_of() == [] for m in stack.models.values())


# ── E7: 대상 없음 + 대상 필수 보기 = 첫 홉 전부 ─────────────────────────────────────

@pytest.fixture
def small_stack(tmp_path, monkeypatch):
    """도메인 4곳 · 호스트 없는 인스턴스 포함 · 인라인 행 상한을 작게(결과 파일 경로)."""
    pytest.importorskip("mcp")

    def make(inline_rows: int) -> tuple[_Stack, _Recorder]:
        models = {"bank": _Model("bank", _TOKENS["bank"], hosts=_HOSTS, domains=4,
                                 hostless=True),
                  "common": _Model("common", _TOKENS["common"], hosts=_HOSTS[:4], domains=4)}
        where = tmp_path / f"inline{inline_rows}-{len(made)}"
        where.mkdir()
        s = _Stack(models, where, {"APM_INLINE_ROWS": str(inline_rows)})
        made.append(s)
        return s, rec

    made: list[_Stack] = []
    rec = _Recorder()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", rec.factory())
    monkeypatch.setattr(aq, "link_business_names", _NoEdge())
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    try:
        yield make
    finally:
        for s in made:
            s.close()


def _all_instances(stack: _Stack) -> set[tuple[str, int]]:
    return {(sid, i["instanceId"]) for sid, m in stack.models.items()
            for recs in m.instances.values() for i in recs}


async def test_e7_first_hop_takes_every_instance_including_hostless(small_stack) -> None:
    """plans/134 W6 ④ — 첫 홉은 부하(TPS) 순위다. 상위 N(여기선 100)이 전 인스턴스보다 크면
    전부다."""
    stack, rec = small_stack(500)
    state = await _turn(stack, "WAS 응답시간", _task(["apm.app_health"]),
                        config=_config(stack.url, top_n=100))
    res = _apm_result(state)
    first = rec.calls[0]
    assert first[0] == "apm_fleet" and "hostname" not in first[1]
    assert (first[1]["mode"], first[1]["metric"], first[1]["order"], first[1]["n"]) == (
        "ranking", "tps", "desc", 100)
    assert "lookback_minutes" not in first[1] and "reference_time" not in first[1], "실시간"
    (batch,) = rec.named("apm_app_health")
    items = batch["targets"]
    hosts = {t["hostname"] for t in items if "hostname" in t}
    hostless = [t for t in items if "hostname" not in t]
    expected_hosts = {i["hostName"] for m in stack.models.values()
                      for recs in m.instances.values() for i in recs if i["hostName"]}
    assert hosts == expected_hosts, "호스트 정합 인스턴스 전부(절단 0)"
    assert hostless == [{"instance_name": "batch-was", "source_id": "bank", "instance_id": 199}]
    got = {(r["source_id"], r["instance_id"]) for r in res["query_results"]}
    assert got == _all_instances(stack), sorted(_all_instances(stack) - got)
    notes = [d["text"] for d in res.get("disclosures") or [] if "대상 서버 미지정" in d["text"]]
    total = len(_all_instances(stack))
    assert notes and f"전체 인스턴스 {total}개" in notes[0], notes


async def test_v34_2_first_hop_beyond_inline_rows_is_not_silently_cut(small_stack) -> None:
    stack, rec = small_stack(5)
    state = await _turn(stack, "WAS 응답시간", _task(["apm.app_health"]),
                        config=_config(stack.url, top_n=100))
    (batch,) = rec.named("apm_app_health")
    total = len(_all_instances(stack))
    sent = len(batch["targets"])
    notes = [d["text"] for d in _apm_result(state).get("disclosures") or []
             if "대상 서버 미지정" in d["text"]]
    assert sent >= len({i["hostName"] for m in stack.models.values()
                        for recs in m.instances.values() for i in recs if i["hostName"]}) + 1, (
        sent, total, notes)


async def test_w6_untargeted_default_queries_only_the_top_n_by_load(small_stack) -> None:
    """plans/134 W6 ④ — 기본 상위 N(20)이 전 인스턴스보다 작으면 그 N개만 · 의무 고지(중립 —
    건강한 순위로 고른 범위 제한은 부분 실패가 아니다 · 교정 1)."""
    stack, rec = small_stack(5)
    state = await _turn(stack, "WAS 응답시간", _task(["apm.app_health"]))
    res = _apm_result(state)
    (ranking,) = rec.named("apm_fleet")
    assert ranking["n"] == 20
    assert rec.named("apm_instance_map") == [], "순위로 골랐으면 목록을 부르지 않는다"
    (batch,) = rec.named("apm_app_health")
    total = len(_all_instances(stack))
    assert total > 20
    step = res["apm_query"]["inserted_steps"][0]
    assert step["instances"] == 20 and step["fleet_total"] == total, step
    # hostname 대상은 (호스트 · 순위 행 소스)마다 1개(W6 교정 2 V6-3) · 나머지는 호스트 없는
    # 인스턴스
    host_items = [t for t in batch["targets"] if "hostname" in t]
    assert len({t["hostname"] for t in host_items}) == step["hosts"]
    assert all(t.get("source_id") for t in host_items), host_items
    assert len({(t["hostname"], t["source_id"]) for t in host_items}) == len(host_items)
    assert len(batch["targets"]) - len(host_items) == step.get("hostless", 0)
    (note,) = [d for d in res.get("disclosures") or [] if "대상 서버 미지정" in d["text"]]
    assert note["kind"] == "apm_untargeted_scope"
    assert f"전체 인스턴스 {total}개 중 현재 부하(TPS) 상위 20개" in note["text"]
    assert "특정 서버(또는 업무)를 지정하면 그 대상만 정확히 조회합니다" in note["text"]


async def test_e7_batch_rows_beyond_inline_are_disclosed_not_zero(small_stack) -> None:
    """배치 결과가 인라인 행 상한을 넘으면(결과 파일) 화면은 앞 N행 + 배치 단위 고지 1줄이고, 대상별
    출처의 행 수는 실제 건수다(인라인 밖 대상을 0건으로 세지 않는다)."""
    stack, rec = small_stack(5)
    state = await _turn(stack, "web01~web12 응답시간", _task(["apm.app_health"]),
                        hosts=tuple(_HOSTS))
    res = _apm_result(state)
    meta = res.get("apm_query") or {}
    rows_by_host = {p["hostname"]: p["rows"] for p in meta["provenance"]}
    assert rows_by_host == {h: (2 if h in _HOSTS[:4] else 1) for h in _HOSTS}, rows_by_host
    final = _final(state)
    assert "앞 5행" in final and "전체 16행" in final, final[-1500:]
    assert not meta.get("failures")


# ── E5: 배치 작업 승격 → 접수 답(배치 전체 예상·진행) → 작업 API로 결과 회수 ──────────────

async def test_e5_promoted_batch_is_one_accepted_job_then_recovered(small_stack) -> None:
    stack, rec = small_stack(500)
    stack.models["bank"].delay["/api/realtime/instance"] = 0.4
    config = _config(stack.url, call_timeout=4.0, query_timeout=5, reserve=2)
    state = await _turn(stack, "web01~web12 응답시간", _task(["apm.app_health"]),
                        hosts=tuple(_HOSTS), config=config, thread="th-v34-job")
    res = _apm_result(state)
    (batch,) = rec.named("apm_app_health")
    assert len(batch["targets"]) == 12 and batch["wait_seconds"] <= 2.0
    assert res["source_status"][0]["status"] == "accepted", res["source_status"]
    text = str(res.get("final_response"))
    assert "작업으로 접수" in text and "아직 조회 결과가 아닙니다" in text, text
    m = re.search(r"진행 (\d+)/(\d+) API 호출", text)
    assert m, text
    # 배치 전체 기준 — 대상 12개(bank 12 + common 4 · 대상마다 실시간 1호출 이상)
    assert int(m.group(2)) >= 16, text
    jobs = res.get("accepted_jobs") or (res.get("apm_query") or {}).get("accepted_jobs") or []
    assert len(jobs) == 1, ("배치 = 작업 1건", jobs)
    job_id = jobs[0]
    # 작업 API(본체 `ApmJobService`) — 장부 소유자 확인 → 게이트웨이 작업 도구
    service = apm_jobs.ApmJobService(config, store_mod._STORE)
    user = {"sub": "alice", "role": "user"}
    for _ in range(80):
        view = await service.status(job_id, user)
        if view["state"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.25)
    assert view["state"] == "completed", view
    assert view["progress"]["done"] == view["progress"]["total"], view["progress"]
    download = await service.open_download(job_id, user, "jsonl")
    lines = []
    async for chunk in download.body:
        lines += [json.loads(x) for x in chunk.decode().splitlines() if x.strip()]
    # 결과 행은 대상 순번(`target_index`)으로 대상과 잇는다
    # (배치 항목 순서 = 본체가 보낸 targets 순서)
    shown = {(r["source_id"], batch["targets"][r["target_index"]]["hostname"],
              r["response_time_avg_ms"]) for r in lines}
    assert {(h, v) for s_, h, v in shown if s_ == "bank"} == {
        (h, 1000 + k * 10) for k, h in enumerate(_HOSTS, 1)}, shown
    assert {r["target_index"] for r in lines} == set(range(12))


# ── E8: 대상 1개 = 종전 호출 모양 ────────────────────────────────────────────────

async def test_e8_one_target_keeps_the_former_call_shape(live) -> None:
    """대상 1개는 `targets` 없이 최상위 `hostname` 1호출(인자 집합이 세션 시작 커밋과 같다 —
    `bac814c` 본체의 같은 질의 인자 집합은 검증 보고 E8 절에 대조)."""
    stack, rec = live
    mark = stack.log_mark()
    state = await _turn(stack, "web01 응답시간", _task(["apm.app_health", "apm.events"]),
                        hosts=("web01",))
    res = _apm_result(state)
    calls = rec.data_calls()
    assert [n for n, _ in calls] == ["apm_app_health", "apm_events"]
    for name, args in calls:
        assert set(args) == {"hostname", "thread_id", "owner", "wait_seconds"} | (
            {"lookback_minutes"} if "lookback_minutes" in args else set()), (name, args)
        assert args["hostname"] == "web01" and args["owner"] == "user:alice"
    audits = [a for a in stack.audits(mark) if "tool=apm_job" not in a]
    assert all("target=web01 " in a for a in audits) and len(audits) == 2, audits
    assert "batches" not in (res.get("apm_query") or {})
