"""plans/134 W5·W6·W7 본체 독립 검증(verify-body · 2026-10-06).

1. **본체 ↔ 게이트웨이 실계약**: 새 보기 10종이 `apm_query`에서 만드는 도구 인자
   (모의 세션으로 포착)를 게이트웨이 `create_server(...).list_tools()`의 실제 입력
   스키마(별도 프로세스 · cwd `apm_gateway`)와 jsonschema로 대조한다. FastMCP는
   **모르는 인자를 조용히 버린다**(검증 오류 없음) — 키 이름 어긋남은 이 정적
   대조로만 잡힌다.
2. **실프로세스 왕복**: 목 Open API(이 프로세스 스레드 · 127.0.0.1 임시 포트) ↔
   `python -m apm_gateway`(임시 포트 · 임시 스풀) ↔ 실제 MCP SSE ↔ `run_apm_query`.
   FastMCP 인자 검증(형 불일치)과 봉투 칸 이름(trace `summary.*` · change
   `before/after/delta`)을 실코드로 확인한다.
3. **경계 회귀**: 기존 12개 보기 선언 불변(기준 `da3ea4f`) · 선택 대상·대상 없음
   보기의 첫 홉 없음 · 직전 턴 대상으로 조용히 좁히지 않음 · 필수 조건 되묻기(다른
   보기는 조회) · 참조 번호 = 화면 표 순서 · 다른 스레드의 참조 없음.
4. **결함 재현**(`xfail(strict=True)` — 고치면 XPASS로 실패하니 표지를 걷는다):
   V-1 ~ V-6.

실 제니퍼·외부 네트워크·실 LLM 0 · 게이트웨이 포트 9096·9097·9099와 MLX 8080은
쓰지 않는다.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.config import AppConfig, DBHubConfig, LLMConfig
from src.domain import disclosure as disc
from src.domain.result_refs import extract_result_refs
from src.orchestration import apm_query as aq
from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.replanner import replanner
from src.orchestration.result_aggregator import result_aggregator
from src.state import create_followup_input, create_initial_state

ip = importlib.import_module("src.orchestration.intent_planner")
cr = importlib.import_module("src.nodes.context_resolver")

ROOT = Path(__file__).resolve().parents[2]
GW_ROOT = ROOT / "apm_gateway"
BASELINE = "da3ea4f"
NOW = datetime(2026, 10, 2, 10, 0, 0)
T0 = 1759366000000
FORBIDDEN_PORTS = {9096, 9097, 9099, 8080}
#: 계약 §3.3 `apm_config(kind)` 값
CONFIG_KINDS = {"event_rules", "color_boundary", "process_instance", "data_server", "db_path",
                "loaded_classes", "rdb_export"}
#: 새 보기 10종(계약 §4.1 순서)
NEW_VIEWS = ("apm.profile", "apm.trace", "apm.change_impact", "apm.event_rules", "apm.process",
             "apm.jennifer_server", "apm.loaded_classes", "apm.environment", "apm.users",
             "apm.active_detail")


# ── 공용 ──────────────────────────────────────────────────────────────────────

def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:1/sse"},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
    )


def _app_config(url: str = "http://127.0.0.1:1/sse", token: str = "gw-token") -> AppConfig:
    return AppConfig(
        _env_file=None,
        llm=LLMConfig(_env_file=None, provider="ollama", model="none",
                      ollama_base_url="http://127.0.0.1:9"),
        dbhub=DBHubConfig(_env_file=None, server_url="http://127.0.0.1:9/sse",
                          source_name="infra_db", source_endpoints={"apm": url},
                          source_tokens=json.dumps({"apm": token}), source_call_timeout=30.0),
        checkpoint_backend="sqlite", checkpoint_db_url=":memory:",
    )


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-02T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


class _Gateway:
    """모의 MCP 세션 — 호출(도구 · None을 지운 인자)을 기록하고 도구별 봉투를 돌려준다."""

    def __init__(self, replies: dict | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[tuple[str, dict]] = []
        self.opened = 0

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        reply = self.replies.get(name)
        if callable(reply):
            reply = reply(arguments)
        if reply is None:
            rows = ([{"hostname": "web01", "instance_id": 11, "match_confidence": "exact"}]
                    if name == "apm_instance_map" else [])
            reply = _env(name, rows)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


@pytest.fixture
def gateway(monkeypatch):
    def install(replies: dict | None = None) -> _Gateway:
        gw = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _tx(i: int, host: str = "web01", **extra: Any) -> dict:
    end = T0 + i * 1000 + 500
    row = {"source_id": "default", "domain_id": 1000, "instance_id": 11,
           "instance_name": f"{host}-was1", "application": f"/order/{i}", "txid": str(100 + i),
           "guid": f"g-{i}", "response_time_ms": 1000 * i, "start_time_ms": T0 + i * 1000,
           "end_time_ms": end, "hostname": host,
           "profile_ref": {"source_id": "default", "domain_id": 1000, "txid": str(100 + i),
                           "time_ms": end}}
    row.update(extra)
    return row


def _error_row(i: int, host: str = "web01") -> dict:
    return {"time_ms": T0 + i, "error_type": "SQL_EXCEPTION", "source_id": "default",
            "domain_id": 1000, "instance_id": 11, "txid": str(500 + i), "profile_index": 14,
            "hostname": host,
            "profile_ref": {"source_id": "default", "domain_id": 1000, "txid": str(500 + i),
                            "time_ms": T0 + i, "profile_no": 14}}


def _active(i: int, host: str = "web01") -> dict:
    txid = str(-(800 + i)) if i % 2 else str(800 + i)
    return {"source_id": "default", "domain_id": 1000, "instance_id": 11, "txid": txid,
            "hostname": host, "start_time_ms": T0,
            "active_ref": {"source_id": "default", "domain_id": 1000, "txid": txid,
                           "session_id": 3100 + i, "thread_hash": 9900 + i}}


def _ctx(rows: list[dict], *, turn: int = 1, turn_count: int = 2,
         previous_entities: list[dict] | None = None) -> dict:
    return {"turn_count": turn_count, "previous_entities": previous_entities or [],
            "previous_result_refs": {"turn": turn, **extract_result_refs(rows)}}


def _isolated(*hosts: str, ctx: dict | None = None, time_range: dict | None = None,
              query: str = "질의", **extra: Any) -> dict:
    out = {"parsed_requirements": {
               "filter_conditions": [{"field": "hostname", "op": "=", "value": h} for h in hosts],
               "time_range": time_range, "limit": None},
           "conversation_context": ctx or {}, "thread_id": "th-1", "user_id": "alice",
           "original_user_query": query, "user_query": query}
    out.update(extra)
    return out


async def _run(views: list[str], isolated: dict, *, view_args: dict | None = None,
               areas: list[str] | None = None, llm: Any = None,
               app_config: Any = None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "views": views,
                            "sub_query": isolated.get("user_query") or "질의", "input_from": []}
    if view_args is not None:
        task["view_args"] = view_args
    if areas is not None:
        task["areas"] = areas
    return await aq.run_apm_query(task, isolated, llm=llm, app_config=app_config or _cfg(),
                                  now=NOW)


def _merge(state: dict, update: dict) -> dict:
    out = {**state, **{k: v for k, v in update.items() if k != "messages"}}
    out["messages"] = list(state.get("messages") or []) + list(update.get("messages") or [])
    return out


async def _two_tier_turn(state: dict, query: str, tasks: list[dict], config: AppConfig,
                         hosts: tuple[str, ...] = ()) -> dict:
    """2단 한 턴 — 분해(가짜 LLM 계획) → 오케스트레이터 → 재계획기 → 집계기(실함수)."""
    llm = FakeListChatModel(responses=[json.dumps({"tasks": tasks}, ensure_ascii=False)] * 3)
    decomposed = await ip._llm_decompose(llm, query, config)
    state = _merge(state, {"parsed_requirements": {
        "filter_conditions": [{"field": "hostname", "op": "=", "value": h} for h in hosts],
        "original_query": query, "time_range": None, "limit": None},
        "task_plan": decomposed["tasks"], "task_results": {}, "replan_count": 0,
        "original_user_query": query})
    state = _merge(state, await agent_orchestrator(
        state, llm=FakeListChatModel(responses=["unused"] * 4), app_config=config))
    nf = json.dumps({"needs_followup": False, "reason": "충분", "new_tasks": []})
    state = _merge(state, await replanner(state, llm=FakeListChatModel(responses=[nf] * 3),
                                          app_config=config))
    out = await result_aggregator(state, llm=FakeListChatModel(responses=["요약입니다."] * 8),
                                  app_config=config, synthesize=True, composite_answer="steps")
    return _merge(state, out)


async def _next_turn(state: dict, query: str) -> dict:
    state = _merge(state, create_followup_input(query))
    return _merge(state, await cr.context_resolver(state))


def _apm_task(views: list[str], *, tid: str = "t1", view_args: dict | None = None,
              sub: str = "q", input_from: list[str] | None = None,
              depends_on: list[str] | None = None, order: int = 1) -> dict:
    out: dict[str, Any] = {"task_id": tid, "agent": "apm_query", "sub_query": sub,
                           "views": views, "depends_on": depends_on or [],
                           "input_from": input_from or [], "order": order}
    if view_args:
        out["view_args"] = view_args
    return out


def _fresh(query: str, thread: str, user: str = "alice") -> dict:
    return create_initial_state(user_query=query, thread_id=thread, user_id=user,
                                user_role="user", allowed_sources=None)


# ── 1. 본체 ↔ 게이트웨이 실계약(정적 스키마 대조) ───────────────────────────────

_DUMP_SCHEMA = """
import asyncio, json, sys, tempfile
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.config import load_config
from apm_gateway.interface.server import create_server

async def main():
    spool = tempfile.mkdtemp(prefix="gw-schema-")
    cfg = load_config({"JENNIFER_API_URL": "http://127.0.0.1:1", "JENNIFER_API_TOKEN": "x",
                       "APM_SPOOL_DIR": spool})
    tools = ApmTools(build_source_set(cfg), cfg)
    jobs = JobManager.from_config(cfg, envelope=tools.ok, error_envelope=tools.err)
    mcp = create_server(tools, jobs=jobs, port=0)
    listed = await mcp.list_tools()
    sys.stdout.write(json.dumps({t.name: t.inputSchema for t in listed}, ensure_ascii=False))
    await jobs.aclose()

asyncio.run(main())
"""


@pytest.fixture(scope="module")
def gw_schema() -> dict[str, dict]:
    """게이트웨이 실코드의 MCP 입력 스키마(별도 프로세스 · 게이트웨이 cwd — import 경계 유지)."""
    pytest.importorskip("mcp")
    env = {**os.environ, "PYTHONPATH": "."}
    proc = subprocess.run([sys.executable, "-c", _DUMP_SCHEMA], cwd=GW_ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _slow_ctx() -> dict:
    return _ctx([_tx(1), _tx(2), _tx(3)])


#: (사례 id, 보기, view_args, 대상 서버, 앞 결과 맥락, 기대 도구, 기대 인자 부분집합)
_CASES: list[tuple[str, str, dict | None, tuple[str, ...], dict | None, str, dict]] = [
    ("profile-ref", "apm.profile", {"ref": 2, "top_k": "5", "include_param_key": "true"}, (),
     _slow_ctx(), "apm_transaction_profile",
     {"hostname": "web01", "source_id": "default", "domain_id": 1000, "txid": "102",
      "time_ms": T0 + 2500, "top_k": 5, "include_param_key": True}),
    ("profile-error-row", "apm.profile", {"ref": 1}, (), _ctx([_error_row(1)]),
     "apm_transaction_profile", {"txid": "501", "profile_no": 14, "hostname": "web01"}),
    ("trace-guid", "apm.trace", {"guid": " g-abc "}, (), None, "apm_transaction_trace",
     {"guid": "g-abc"}),
    ("trace-guid-host", "apm.trace", {"guid": "g-abc"}, ("web01",), None,
     "apm_transaction_trace", {"guid": "g-abc", "hostname": "web01"}),
    ("trace-ref", "apm.trace", {"ref": 3}, (), _slow_ctx(), "apm_transaction_trace",
     {"guid": "g-3", "around_ms": T0 + 3000}),
    ("change-impact", "apm.change_impact", {"width_minutes": 30, "n": 2, "full": "false"},
     ("web01",), None, "apm_change_impact",
     {"hostname": "web01", "width_minutes": 30, "n": 2, "full": False}),
    ("event-rules-host", "apm.event_rules",
     {"rule_type": "METRIC", "target": "Instance", "error_type": "outofmemory"}, ("web01",),
     None, "apm_config", {"kind": "event_rules", "hostname": "web01", "rule_type": "metric",
                          "target": "instance", "error_type": "OUTOFMEMORY"}),
    ("event-rules-all", "apm.event_rules", None, (), None, "apm_config", {"kind": "event_rules"}),
    ("color-boundary", "apm.event_rules", {"kind": "color_boundary"}, ("web01",), None,
     "apm_config", {"kind": "color_boundary", "hostname": None}),
    ("process", "apm.process", {"process_id": "4242"}, ("web01",), None, "apm_config",
     {"kind": "process_instance", "process_id": 4242, "hostname": "web01"}),
    ("data-server", "apm.jennifer_server", None, ("web01",), None, "apm_config",
     {"kind": "data_server", "hostname": None}),
    ("db-path", "apm.jennifer_server", {"kind": "db_path"}, ("web01",), None, "apm_config",
     {"kind": "db_path", "hostname": "web01"}),
    ("rdb-export", "apm.jennifer_server", {"kind": "rdb_export"}, (), None, "apm_config",
     {"kind": "rdb_export"}),
    ("loaded-classes", "apm.loaded_classes", {"search": "OrderService"}, ("web01",), None,
     "apm_config", {"kind": "loaded_classes", "hostname": "web01", "search": "OrderService"}),
    ("environment", "apm.environment", {"scope": "java", "key": "JAVA_OPTS"}, ("web01",), None,
     "apm_environment", {"hostname": "web01", "scope": "JAVA", "key": "JAVA_OPTS"}),
    ("users", "apm.users", None, (), None, "apm_users", {}),
    ("user-one", "apm.users", {"user_id": "op01"}, (), None, "apm_users", {"user_id": "op01"}),
    ("active-detail", "apm.active_detail", {"ref": 2}, (), _ctx([_active(0), _active(1)]),
     "apm_active_detail", {"domain_id": 1000, "txid": "-801", "session_id": 3101,
                           "thread_hash": 9901, "source_id": "default", "hostname": "web01"}),
]


@pytest.mark.parametrize("case", _CASES, ids=[c[0] for c in _CASES])
async def test_new_view_tool_args_match_the_gateway_schema(case, gw_schema, gateway) -> None:
    from jsonschema import Draft202012Validator

    _, view, args, hosts, ctx, tool, expected = case
    gw = gateway()
    res = await _run([view], _isolated(*hosts, ctx=ctx),
                     view_args={view: args} if args is not None else None)
    assert not res.get("error"), res.get("error")
    calls = gw.named(tool)
    assert len(calls) == 1 and all(n == tool for n, _ in gw.calls), gw.calls
    sent = calls[0]
    schema = gw_schema[tool]
    unknown = set(sent) - set(schema["properties"])
    assert not unknown, f"게이트웨이가 모르는 인자(FastMCP는 조용히 버린다): {unknown}"
    assert set(schema.get("required") or []) <= set(sent), (schema.get("required"), sent)
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(sent)]
    assert not errors, errors
    for key, value in expected.items():
        assert sent.get(key) == value, (key, sent.get(key), value)
    if tool == "apm_config":
        assert sent["kind"] in CONFIG_KINDS
    assert "ref" not in sent and "investigation_id" not in sent
    assert sent["owner"] == "user:alice" and sent["thread_id"] == "th-1"


def test_gateway_schema_covers_every_new_view_tool(gw_schema) -> None:
    tools = {v.tool for v in aq.apm_views() if v.id in NEW_VIEWS}
    assert tools <= set(gw_schema), tools - set(gw_schema)
    # 사례가 새 보기 10종을 모두 덮는다
    assert {c[1] for c in _CASES} == set(NEW_VIEWS)


# ── 2. 실프로세스 왕복(목 Open API ↔ 게이트웨이 프로세스 ↔ 실제 SSE ↔ run_apm_query) ─────

_GW_TOKEN = "tok-chat-verify-9Lm"
_MOCK_TOKEN = "tok-mock-verify-2Hx"


def _free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in FORBIDDEN_PORTS:
            return port


def _fx(template: str, body: Any, content_type: str = "application/json") -> dict:
    resp: dict[str, Any] = {"status": 200, "content_type": content_type}
    resp["body_text" if isinstance(body, str) else "body_json"] = body
    return {"fixture_version": 1, "source": "verify-body", "jennifer_version": "5.6.4-spec",
            "request": {"method": "GET", "template": template, "path": template, "query": {}},
            "response": resp, "variant": "ok", "label": ""}


def _raw_tx(domain: int, inst: int, name: str, txid: str, end: int, rt: int, *,
            err: str = "", guid: str | None = None) -> dict:
    return {"domainId": domain, "instanceId": inst, "instanceName": name,
            "domainName": f"d{domain}", "applicationName": f"/o?tx={txid}", "txid": txid,
            "responseTime": rt, "errorType": err, "endTime": str(end),
            "startTime": str(end - rt), "collectTime": str(end + 1000),
            "guid": guid or f"g-{txid}", "userId": "kimcs01", "clientId": "c-kim"}


@pytest.fixture(scope="module")
def real_gateway(tmp_path_factory):
    """목 Open API(스레드) + 게이트웨이 실프로세스 — `(sse_url, mock_state, now_ms, change_ms)`."""
    pytest.importorskip("mcp")
    scripts = GW_ROOT / "testdata" / "jennifer" / "scripts"
    sys.path.insert(0, str(scripts))
    import mock_openapi

    now_ms = int(time.time() * 1000)
    minute = 60_000
    change = now_ms - 30 * minute
    txs = [_raw_tx(1000, 1001, "was01_a", str(7000 + i), change - 9 * minute + i * 50_000, 500,
                   err="SQL_EXCEPTION" if i == 0 else "") for i in range(10)]
    txs += [_raw_tx(1000, 1001, "was01_a", str(7100 + i), change + minute + i * 25_000, 2000,
                    err="SQL_EXCEPTION" if i < 5 else "") for i in range(20)]
    txs += [_raw_tx(1000, 1001, "was01_a", "9002", now_ms - 5 * minute, 3000),
            _raw_tx(1000, 1001, "was01_a", "9001", now_ms - 10 * minute, 4000, guid="g-link"),
            _raw_tx(2000, 2001, "api02", "9501", now_ms - 10 * minute + 200, 3500,
                    guid="g-link")]
    instances = [
        {"instanceId": 1001, "name": "was01_a", "hostName": "was-host01", "domainId": 1000,
         "ipAddress": "10.0.0.11", "platform": "JAVA", "status": "RUNNING"},
        {"instanceId": 2001, "name": "api02", "hostName": "was-host02", "domainId": 2000,
         "ipAddress": "10.0.0.12", "platform": "JAVA", "status": "RUNNING"},
    ]
    active = [{"domainId": 1000, "instanceId": 1001, "instanceName": "was01_a",
               "application": "/pay", "status": "RUNNING", "statusName": "SQL",
               "elapseTime": 1000, "statusElapseTime": 1, "runningMode": "SQL",
               "runningFullText": "", "runningDataSourceName": "", "clientIp": "10.1.1.1",
               "txid": txid, "startTime": now_ms - 1000, "sessionId": 31000 + i,
               "threadHash": 99000 + i} for i, txid in enumerate(("8000", "-8001"))]
    errors = [{"domainId": 1000, "instanceId": 1001, "instanceName": "was01_a",
               "errorType": "SQL_EXCEPTION", "message": "ORA-1", "time": str(t), "txid": txid,
               "applicationName": "/o", "profileIndex": 14}
              for t, txid in ((change - 5 * minute, "7000"), (change + 2 * minute, "7100"),
                              (change + 3 * minute, "7101"))]
    fixtures = tmp_path_factory.mktemp("fx")
    for i, fx in enumerate([
        _fx("/api/domain", {"result": [{"domainId": 1000, "name": "d1000"},
                                       {"domainId": 2000, "name": "d2000"}]}),
        _fx("/api/instance", {"result": instances}),
        _fx("/api/transaction/time", {"result": txs}),
        _fx("/api/transaction/guid", {"result": txs}),
        _fx("/api/activeService/list", {"result": active}),
        _fx("/api/dbsearch/event", {"result": []}),
        _fx("/api/dbsearch/error", {"result": errors}),
        _fx("/api-v2/deploy/{domainId}", [{"collectTime": change, "instanceId": 1001}]),
        _fx("/api/transaction/txid", {"result": [txs[-2]]}),
        _fx("/api/transaction/profile.txt", "START\nEND\n", "text/plain"),
        _fx("/api/transaction/sql", {"result": [{"sql": "select * from t where id = 1"}]}),
    ]):
        (fixtures / f"fx_{i:02d}.json").write_text(json.dumps(fx), encoding="utf-8")

    original = mock_openapi.Handler._narrow

    def narrow(template: str, query: dict[str, str], body: Any) -> Any:
        body = original(template, query, body)
        if not isinstance(body, dict) or not isinstance(body.get("result"), list):
            return body
        rows = body["result"]
        if "domain_id" in query:
            rows = [r for r in rows if str(r.get("domainId", query["domain_id"]))
                    == query["domain_id"]]
        if template in ("/api/transaction/time", "/api/dbsearch/error") and "start_time" in query:
            lo, hi = int(query["start_time"]), int(query["end_time"])
            key = "endTime" if template == "/api/transaction/time" else "time"
            rows = [r for r in rows if lo <= int(r.get(key) or 0) <= hi]
        return {**body, "result": rows}

    mock_openapi.Handler._narrow = staticmethod(narrow)
    server, state = mock_openapi.make_server(fixtures, _MOCK_TOKEN, "connected")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = _free_port()
    spool = tmp_path_factory.mktemp("spool")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JENNIFER_", "APM_"))}
    env.update({
        "JENNIFER_SOURCES": "", "JENNIFER_API_URL": f"http://127.0.0.1:{server.server_address[1]}",
        "JENNIFER_API_TOKEN": _MOCK_TOKEN, "JENNIFER_DOMAIN_IDS": "[]",
        "JENNIFER_RATE_LIMIT_PER_SEC": "200", "APM_GATEWAY_HOST": "127.0.0.1",
        "APM_GATEWAY_PORT": str(port), "APM_GATEWAY_BEARER_TOKEN": "",
        "APM_GATEWAY_BEARER_TOKENS": json.dumps({"chat": _GW_TOKEN}),
        "APM_EVENT_POLLER_ENABLED": "false", "APM_SPOOL_DIR": str(spool),
        "APM_GATEWAY_LOG_LEVEL": "WARNING", "APM_TIMEZONE": "Asia/Seoul",
        "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "1",
    })
    proc = subprocess.Popen([sys.executable, "-m", "apm_gateway"], cwd=GW_ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(300):
            try:
                socket.create_connection(("127.0.0.1", port), 0.1).close()
                break
            except OSError:
                assert proc.poll() is None, f"게이트웨이 기동 실패(exit {proc.returncode})"
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/sse", state, now_ms, change
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        server.shutdown()
        server.server_close()
        mock_openapi.Handler._narrow = staticmethod(original)
        sys.path.remove(str(scripts))


async def _real(views: list[str], isolated: dict, url: str,
                view_args: dict | None = None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "views": views,
                            "sub_query": "q", "input_from": []}
    if view_args:
        task["view_args"] = view_args
    return await aq.run_apm_query(task, isolated, llm=None,
                                  app_config=_app_config(url, _GW_TOKEN))


async def test_real_gateway_trace_and_change_impact_lines(real_gateway, monkeypatch) -> None:
    url, _, _, _ = real_gateway
    monkeypatch.setattr(aq, "_SESSION_FACTORY", None)
    trace = await _real(["apm.trace"], _isolated(), url, {"apm.trace": {"guid": "g-link"}})
    assert trace["source_status"][0]["status"] == "ok", trace["source_status"]
    line = trace["answer_lines"][0]
    assert line.startswith("GUID g-link: 거래 2건 · 도메인 2곳(조회 2곳 · 실패 0곳) · ")
    assert line.endswith("인스턴스 was01_a, api02")
    assert [r["trace_order"] for r in trace["query_results"]] == [1, 2]

    change = await _real(["apm.change_impact"], _isolated("was-host01"), url,
                         {"apm.change_impact": {"width_minutes": 10}})
    assert change["source_status"][0]["status"] == "ok", change["source_status"]
    (line,) = change["answer_lines"]
    assert re.fullmatch(
        r"was01_a 변경 감지 \S+ — 오류율 10\.0% → 25\.0%\(\+15\.0%p\) · 평균 응답 500ms → 2,000ms"
        r"\(\+300\.0%\) · 오류 기록 1 → 2 · 호출 10 → 20", line), line
    kinds = {d["kind"] for d in change.get("disclosures") or []}
    assert disc.APM_CHANGE_DETECTION in kinds


async def test_real_gateway_reference_views_pass_fastmcp_validation(real_gateway,
                                                                    monkeypatch) -> None:
    """참조 칸을 그대로 넘긴 인자가 실제 FastMCP 인자 검증(형)을 통과한다 — 음수 txid 포함."""
    url, mock_state, _, _ = real_gateway
    monkeypatch.setattr(aq, "_SESSION_FACTORY", None)
    slow = await _real(["apm.slow_tx"], _isolated("was-host01"), url)
    active = await _real(["apm.active"], _isolated("was-host01"), url)
    ctx = _ctx(slow["query_results"])
    profile = await _real(["apm.profile"], _isolated(ctx=ctx), url, {"apm.profile": {"ref": 1}})
    assert profile["source_status"][0]["status"] == "ok", profile.get("error")
    detail = await _real(["apm.active_detail"], _isolated(ctx=_ctx(active["query_results"])),
                         url, {"apm.active_detail": {"ref": 2}})
    assert detail["source_status"][0]["status"] == "ok", detail.get("error")
    with mock_state.lock:
        paths = [h["path"] for h in mock_state.hits]
    assert "/api-v2/active-service/detail/1000/-8001" in paths
    pid = await _real(["apm.process"], _isolated("was-host01"), url,
                      {"apm.process": {"process_id": "4242"}})
    assert pid["source_status"][0]["status"] == "ok", pid.get("error")


# ── 3. 경계 회귀 ───────────────────────────────────────────────────────────────

def test_first_twelve_views_are_unchanged_from_baseline() -> None:
    """기존 12개 보기 선언(대상·창·도구·조건)이 기준 커밋과 같다.

    M-5·W3 소유 — 이번 Wave에서 바꾸지 않는다.
    """
    base = subprocess.run(["git", "show", f"{BASELINE}:config/db_registry.yaml"], cwd=ROOT,
                          capture_output=True, text=True)
    if base.returncode != 0:
        pytest.skip("기준 커밋을 읽을 수 없다")
    def apm_views_of(text: str) -> list[dict]:
        return next(s for s in yaml.safe_load(text)["solutions"] if s["code"] == "apm")["views"]

    old = apm_views_of(base.stdout)
    new = apm_views_of((ROOT / "config/db_registry.yaml").read_text(encoding="utf-8"))
    assert len(old) == 12
    assert new[:12] == old
    assert [v["id"] for v in new[12:]] == list(NEW_VIEWS)
    for spec in aq.apm_views()[:12]:
        assert spec.target == "" and spec.reference == ""


@pytest.mark.parametrize("view,args", [
    ("apm.event_rules", None), ("apm.event_rules", {"kind": "color_boundary"}),
    ("apm.jennifer_server", None), ("apm.jennifer_server", {"kind": "db_path"}),
    ("apm.jennifer_server", {"kind": "rdb_export"}), ("apm.environment", None),
    ("apm.process", {"process_id": 7}), ("apm.users", None),
])
async def test_target_free_views_never_insert_a_first_hop_or_inherit_the_previous_target(
        gateway, view, args) -> None:
    gw = gateway()
    ctx = {"turn_count": 2,
           "previous_entities": [{"field": "hostname", "value": "web09"}]}
    res = await _run([view], _isolated(ctx=ctx, query="제니퍼 설정 보여줘"),
                     view_args={view: args} if args else None)
    assert not res.get("error"), res.get("error")
    assert not gw.named("apm_instance_map"), "선택 대상 보기는 인스턴스 목록 첫 홉을 끼우지 않는다"
    assert len(gw.calls) == 1 and "hostname" not in gw.calls[0][1], gw.calls


async def test_optional_view_takes_the_previous_target_only_when_pointed_at(gateway) -> None:
    gw = gateway()
    ctx = {"turn_count": 2, "previous_entities": [{"field": "hostname", "value": "web09"}]}
    await _run(["apm.environment"], _isolated(ctx=ctx, query="그 서버 환경변수 보여줘"))
    assert gw.calls[0][1].get("hostname") == "web09"


async def test_trace_does_not_narrow_to_the_previous_target_without_a_demonstrative(
        gateway) -> None:
    gw = gateway()
    ctx = {"turn_count": 2, "previous_entities": [{"field": "hostname", "value": "web09"}]}
    await _run(["apm.trace"], _isolated(ctx=ctx, query="GUID g-1 연계 거래"),
               view_args={"apm.trace": {"guid": "g-1"}})
    assert "hostname" not in gw.calls[0][1]


async def test_missing_required_condition_asks_back_while_the_other_view_runs(gateway) -> None:
    gw = gateway({"apm_environment": _env("apm_environment", [
        {"instance_id": 11, "scope": "JAVA", "name": "java.vendor", "value": "x"}])})
    res = await _run(["apm.process", "apm.environment"], _isolated("web01"))
    assert not gw.named("apm_config") and gw.named("apm_environment")
    notices = [d["text"] for d in res.get("disclosures") or []
               if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    assert any("프로세스 ID" in t and "조회하지 않았습니다" in t for t in notices), notices
    assert res["source_status"][0]["status"] in ("ok", "partial")


async def test_reference_numbers_follow_the_displayed_table_order(gateway) -> None:
    """2단 최종 답 표의 행 순서 = 다음 턴 참조 번호(두 서버 · 응답시간 순 · 실제 집계기)."""
    rows_by_host = {
        "web01": [{k: v for k, v in _tx(i, "web01").items() if k != "hostname"} for i in (3, 1)],
        "web02": [{k: v for k, v in _tx(i, "web02").items() if k != "hostname"} for i in (5, 2)],
    }
    gw = gateway({
        "apm_slow_transactions": lambda a: _env("apm_slow_transactions",
                                                rows_by_host[a["hostname"]]),
        "apm_transaction_profile": _env("apm_transaction_profile", [{"txid": "x"}]),
    })
    config = _app_config()
    q1 = "web01 web02 느린 트랜잭션"
    state = await _two_tier_turn(_fresh(q1, "th-order"), q1,
                                 [_apm_task(["apm.slow_tx"], sub=q1)], config,
                                 hosts=("web01", "web02"))
    table = [line for line in (state.get("final_response") or "").splitlines()
             if line.startswith("|")]
    header = [c.strip() for c in table[0].strip("|").split("|")]
    shown = [[c.strip() for c in line.strip("|").split("|")][header.index("txid")]
             for line in table[2:]]
    state = await _next_turn(state, "세 번째 트랜잭션 프로파일")
    refs = state["conversation_context"]["previous_result_refs"]["profile_ref"]
    assert [e["value"]["txid"] for e in refs] == shown
    await _two_tier_turn(state, "세 번째 트랜잭션 프로파일", [_apm_task(
        ["apm.profile"], view_args={"apm.profile": {"ref": 3}})], config)
    assert gw.named("apm_transaction_profile")[-1]["txid"] == shown[2]


async def test_another_thread_has_no_reference_candidates(gateway) -> None:
    gw = gateway()
    config = _app_config()
    q1 = "web01 느린 트랜잭션"
    gw.replies["apm_slow_transactions"] = _env("apm_slow_transactions", [_tx(1), _tx(2)])
    await _two_tier_turn(_fresh(q1, "th-A"), q1, [_apm_task(["apm.slow_tx"])], config,
                         hosts=("web01",))
    other = await cr.context_resolver(_fresh("두 번째 트랜잭션 프로파일", "th-B", user="bob"))
    assert other["conversation_context"] is None
    before = len(gw.calls)
    res = await _run(["apm.profile"], _isolated(ctx=other["conversation_context"]),
                     view_args={"apm.profile": {"ref": 2}})
    assert res["degraded_reason"] == "apm_unresolved_condition"
    assert len(gw.calls) == before and gw.opened == 1, "되묻기만 남으면 게이트웨이를 열지 않는다"


# ── 4. 결함 재현(strict xfail) ─────────────────────────────────────────────────

_PROFILE_103 = {"source_id": "default", "domain_id": 1000, "txid": "103", "time_ms": T0 + 3500,
                "transaction": {"txid": "103", "guid": "g-3"}, "profile_excerpt": "STEP 103",
                "sqls": ["select 103"]}


async def test_v1_list_then_reference_plan_keeps_every_row_and_its_own_profile(gateway) -> None:
    """실측(실 게이트웨이 종단 X1): t1 느린 트랜잭션 2행(9002·9003) + t2 프로파일
    ref=2(9003) → 최종 표 1행 = txid 9002 행에 9003의 `transaction`·`profile_excerpt`·
    `sqls`가 붙고 9003 행은 사라졌다. 9B 분해는 이 계획(t2 `input_from=[t1]`)을 6/6
    냈다(로컬 MLX 복합 3문항 × 2회)."""
    gateway({"apm_slow_transactions": _env("apm_slow_transactions", [
        {k: v for k, v in _tx(i).items() if k != "hostname"} for i in (2, 3)]),
        "apm_transaction_profile": _env("apm_transaction_profile", [_PROFILE_103])})
    q = "web01 느린 트랜잭션 보고 두 번째 프로파일도"
    state = await _two_tier_turn(_fresh(q, "th-v1"), q, [
        _apm_task(["apm.slow_tx"], tid="t1"),
        _apm_task(["apm.profile"], tid="t2", view_args={"apm.profile": {"ref": 2}},
                  input_from=["t1"], depends_on=["t1"], order=2)], _app_config(),
        hosts=("web01",))
    rows = state.get("query_results") or []
    mixed = [r for r in rows if isinstance(r.get("transaction"), dict)
             and r["transaction"].get("txid") != r.get("txid")]
    pairs = [(r["txid"], r["transaction"]) for r in mixed]
    assert not mixed, f"다른 거래의 프로파일이 붙은 행: {pairs}"
    assert {"102", "103"} <= {r.get("txid") for r in rows}, "목록 행이 사라지면 안 된다"


class _SelectLLM:
    """선택 재시도 LLM 대역 — 받은 메시지를 기록하고 고정 JSON을 돌려준다."""

    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.messages: list = []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(content=json.dumps(self.reply, ensure_ascii=False))


async def test_v2_agreed_retry_does_not_fill_a_required_condition_the_plan_left_empty(
        gateway) -> None:
    """로컬 MLX 실측: 「was-host01 프로세스 ID로 WAS 인스턴스 찾아줘」 3/3 — 분해
    `views=[apm.process]`·`view_args` 없음·`areas=[was_instance]`(보기 영역
    `apm_management`와 다름) → 선택 재시도 → 재시도가 예문 값 `process_id=12345`를
    내고 결과 `agreed` → 되묻기 없이 PID 12345로 조회."""
    gw = gateway()
    retry = _SelectLLM({"views": ["apm.process"],
                        "view_args": {"apm.process": {"process_id": 12345}}})
    isolated = _isolated("web01", query="web01 프로세스 ID로 인스턴스 찾아줘")
    res = await _run(["apm.process"], isolated, areas=["was_instance"], llm=retry)
    assert retry.messages, "재시도가 실제로 불렸다(영역 라벨 불일치)"
    assert not gw.named("apm_config"), f"말하지 않은 PID로 조회했다: {gw.named('apm_config')}"
    assert res.get("degraded_reason") == "apm_unresolved_condition"


@pytest.mark.parametrize("rows,limit", [
    ([], "[한계] 기간 미지정 — 최근 60분"),
    ([{"source_id": "default", "domain_id": 1000, "txid": "1", "guid": "g-1",
       "instance_name": "a", "trace_order": 1}], "[한계] 기간 미지정 — ±5분(앞 결과 시각 기준)"),
], ids=["zero-hits", "around-row"])
async def test_v3_trace_default_window_notice_reaches_the_final_answer(gateway, rows,
                                                                        limit) -> None:
    """계약 §4.2 「기본값이 쓰이면 고지가 답에 실린다(게이트웨이 limits)」.

    실 게이트웨이 종단 X2: 0건 답 = 「조건에 해당하는 … 데이터가 없습니다」 +
    「GUID …: 거래 0건 …」 — 찾은 구간이 답에 없다.
    """
    limits = ["[한계] GUID가 같은 거래 묶음이다 — 호출 관계(토폴로지)를 뜻하지 않는다", limit]
    if not rows:
        limits.append("[한계] 구간 안에서 GUID 거래를 찾지 못했다(구간 2026-10-02T09:00:00+09:00~"
                      "2026-10-02T10:00:00+09:00)")
    gateway({"apm_transaction_trace": _env("apm_transaction_trace", rows, limits=limits,
                                           summary={"guid": "g-1", "transactions": len(rows),
                                                    "domains_queried": 2,
                                                    "domains_with_hits": len(rows),
                                                    "domains_failed": 0})})
    q = "GUID g-1 연계 거래"
    state = await _two_tier_turn(_fresh(q, "th-v3"), q, [_apm_task(
        ["apm.trace"], view_args={"apm.trace": {"guid": "g-1"}})], _app_config())
    answer = state.get("final_response") or ""
    assert limit.removeprefix("[한계] ") in answer, answer


async def test_v4_trace_over_two_hosts_of_one_domain_is_not_duplicated(gateway) -> None:
    row = {"source_id": "default", "domain_id": 1000, "txid": "1", "guid": "g-1",
           "instance_name": "a", "trace_order": 1}
    gateway({"apm_transaction_trace": _env("apm_transaction_trace", [row], summary={
        "guid": "g-1", "transactions": 1, "domains_queried": 1, "domains_with_hits": 1,
        "domains_failed": 0})})
    res = await _run(["apm.trace"], _isolated("web01", "web02"),
                     view_args={"apm.trace": {"guid": "g-1"}})
    assert len(res["query_results"]) == 1, res["query_results"]
    assert len([x for x in res["answer_lines"] if x.startswith("GUID g-1")]) == 1


async def test_v5_same_plan_dependency_without_input_from_does_not_use_an_older_list(
        gateway) -> None:
    gw = gateway({"apm_slow_transactions": _env("apm_slow_transactions", [
        {k: v for k, v in _tx(i).items() if k != "hostname"} for i in (7, 8)]),
        "apm_transaction_profile": _env("apm_transaction_profile", [{"txid": "x"}])})
    config = _app_config()
    q0 = "web01 느린 트랜잭션"
    gw.replies["apm_slow_transactions"] = _env("apm_slow_transactions", [_tx(1), _tx(2)])
    state = await _two_tier_turn(_fresh(q0, "th-v5"), q0, [_apm_task(["apm.slow_tx"])], config,
                                 hosts=("web01",))
    gw.replies["apm_slow_transactions"] = _env("apm_slow_transactions", [
        {k: v for k, v in _tx(i).items() if k != "hostname"} for i in (7, 8)])
    q = "web01 다시 느린 트랜잭션 보고 첫 번째 프로파일"
    state = await _next_turn(state, q)
    await _two_tier_turn(state, q, [
        _apm_task(["apm.slow_tx"], tid="t1"),
        _apm_task(["apm.profile"], tid="t2", view_args={"apm.profile": {"ref": 1}},
                  depends_on=["t1"], order=2)], config, hosts=("web01",))
    assert gw.named("apm_transaction_profile")[-1]["txid"] == "107", (
        "이번 턴 t1 표의 첫 행(107)이 아니라 직전 턴 목록의 첫 행을 골랐다")


@pytest.mark.xfail(strict=True, reason=(
    "V-6(134 밖 기존 결함) /query 후속 턴이 요청 thread_id의 체크포인트를 소유 확인 없이 "
    "잇는다 — 앞 결과 참조도 남의 스레드에서 쓰인다 · 사용자 결정 대기 · 134 범위 밖"))
def test_v6_followup_turn_on_someone_elses_thread_is_refused() -> None:
    """`_build_turn_input_state`는 체크포인트의 `user_id`와 요청 토큰 `sub`를 비교하지
    않는다(`_with_current_identity`는 role·DB·소스 권한만 덮고 `user_id`는 첫 턴 값
    그대로). `/conversation/{thread_id}`도 소유 확인이 없다
    (`src/api/routes/conversation.py`)."""
    from fastapi import HTTPException

    from src.api.routes.query import _build_turn_input_state
    from src.api.schemas import QueryRequest

    checkpoint = {"user_id": "alice", "messages": [], "conversation_context": {
        "previous_result_refs": {"turn": 1, **extract_result_refs([_tx(1)])}}}
    body = QueryRequest(query="첫 번째 트랜잭션 프로파일", thread_id="th-alice")
    try:
        delta = _build_turn_input_state(body, "th-alice", checkpoint,
                                        {"sub": "bob", "role": "user"})
    except HTTPException:
        return
    assert delta.get("user_id") == "bob" and "conversation_context" in delta, (
        "남의 스레드 체크포인트를 그대로 이어 받는다")


async def test_v7_single_account_answer_shows_the_account_row(gateway) -> None:
    """실 게이트웨이 종단 S12b: 「제니퍼 사용자 op01」 → 「조회는 1건 수행되었으나, 값 칼럼(email,
    phone_number)이 전 행 null이어서 목록 표시를 생략합니다」 — 계정(그룹·허용 IP)이 답에 없다.
    게이트웨이 계약(gw-w7 §5.3)상 `origin=account` 행은 email·phone_number가 null이다."""
    account = {"source_id": "default", "origin": "account", "user_id": "o***",
               "user_name": "O***", "email": None, "phone_number": None, "group": "admin",
               "allow_ip": "192.168.*.*", "extra": {"creationTime": 0, "lastLoginTime": 0}}
    gateway({"apm_users": _env("apm_users", [account])})
    q = "제니퍼 계정 op01 정보"
    state = await _two_tier_turn(_fresh(q, "th-v7"), q, [_apm_task(
        ["apm.users"], view_args={"apm.users": {"user_id": "op01"}})], _app_config())
    answer = state.get("final_response") or ""
    assert "목록 표시를 생략" not in answer and "admin" in answer, answer


@pytest.mark.parametrize("bad_ref", ["5번째", 0, -1], ids=["text", "zero", "negative"])
async def test_v8_unreadable_ref_with_one_candidate_asks_back(gateway, bad_ref) -> None:
    """코드 리뷰 지적(검증자 재현): `ref`가 검증에서 버려져 `deferred`로 가면 `_resolve_reference`는
    `ref`를 못 받아 「ref 없음 + 후보 1 = 그 행」 규칙으로 조회한다. 정수 5(범위 밖)는 되묻기다."""
    gw = gateway()
    res = await _run(["apm.profile"], _isolated(ctx=_ctx([_tx(1)])),
                     view_args={"apm.profile": {"ref": bad_ref}})
    assert not gw.named("apm_transaction_profile"), gw.named("apm_transaction_profile")
    assert res.get("degraded_reason") == "apm_unresolved_condition"
