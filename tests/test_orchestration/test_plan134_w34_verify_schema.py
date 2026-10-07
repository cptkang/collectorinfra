"""plans/134 W3·W4 독립 검증(verify-134-w34) — 본체 보기 28종 인자 ↔ 게이트웨이 MCP 입력
스키마 정적 대조.

FastMCP는 **모르는 최상위 인자를 조용히 버린다**(검증 오류 없음). `targets` 항목(중첩 객체) 키는
게이트웨이가 실행 시 검사한다(`application.batch.parse_targets` — 도구마다 받는 키가 다르다). 그래서
본체가 실제로 만드는 인자(모의 세션으로 포착)를 게이트웨이 실코드의

1. MCP 입력 스키마(`create_server(...).list_tools()` — 별도 프로세스 · cwd `apm_gateway`)와
   jsonschema(Draft 2020-12)로 대조하고(모르는 키 · 빠진 필수 · 형 불일치 0),
2. `targets` 항목 키를 그 도구가 실제로 받는 키(게이트웨이 프로세스에서 모르는 키 탐침으로 읽은
   목록)와 대조한다.

사례 = 레지스트리 보기 28종 × (대상 모양: 대상 1개 · 다건 hostname 배치 · 해석 인스턴스 배치 ·
첫 홉 · 이름 · 참조 · 없음) × (선택 조건 전 이름 · enum 전 선택지) × (`sources` 있음/없음).
사례 목록은 레지스트리에서 만든다 — 보기·조건이 늘면 사례도 는다. `apm.ranking` `metric`
enum == 게이트웨이 `ALL_RANKING_METRICS`(실시간 + 기간 전용)도 함께 본다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.domain.query_time import resolve_query_time
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from src.routing.registry import get_registry
from tests.test_orchestration import apm_batch_mock
from tests.test_orchestration.test_plan134_w567_body_verify import (
    GW_ROOT,
    _active,
    _ctx,
    _error_row,
    _tx,
)

NOW = datetime(2026, 10, 6, 10, 0, 0)
_RANGE = {"start": (NOW - timedelta(hours=3)).isoformat(), "end": (NOW - timedelta(minutes=5))
          .isoformat()}

_DUMP = """
import asyncio, json, sys, tempfile
from apm_gateway.application import fleet_tools
from apm_gateway.application.fleet_tools import RANKING_METRICS
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.config import load_config
from apm_gateway.interface.server import create_server

async def main():
    spool = tempfile.mkdtemp(prefix="verify-134-w34-schema-")
    cfg = load_config({"JENNIFER_SOURCES": '["bank","common"]',
                       "JENNIFER_BANK_API_URL": "http://127.0.0.1:1",
                       "JENNIFER_BANK_API_TOKEN": "x1",
                       "JENNIFER_COMMON_API_URL": "http://127.0.0.1:1",
                       "JENNIFER_COMMON_API_TOKEN": "x2", "APM_SPOOL_DIR": spool})
    tools = ApmTools(build_source_set(cfg), cfg)
    jobs = JobManager.from_config(cfg, envelope=tools.ok, error_envelope=tools.err)
    mcp = create_server(tools, jobs=jobs, port=0)
    listed = await mcp.list_tools()
    schemas = {t.name: t.inputSchema for t in listed}
    keys = {}
    for name, schema in schemas.items():
        if "targets" not in schema.get("properties", {}):
            continue
        def dummy(prop):
            kinds = [prop.get("type")] + [x.get("type") for x in prop.get("anyOf") or []]
            if "integer" in kinds:
                return 1
            if "boolean" in kinds:
                return False
            if "array" in kinds:
                return ["x"]
            return "x"
        required = {k: dummy(schema["properties"][k]) for k in schema.get("required") or []}
        out = await mcp.call_tool(name, {**required, "targets": [{"zz_probe": 1}]})
        content = out[0] if isinstance(out, tuple) else out
        text = content[0].text
        env = json.loads(text)
        reason = env.get("reason") or ""
        tail = reason.split("받는 키:")[-1].strip()
        keys[name] = sorted(json.loads(tail.replace("'", '"'))) if "받는 키" in reason else reason
    sys.stdout.write(json.dumps({"schemas": schemas, "target_keys": keys,
                                 "ranking_metrics": list(RANKING_METRICS),
                                 "all_ranking_metrics": list(getattr(
                                     fleet_tools, "ALL_RANKING_METRICS", RANKING_METRICS))},
                                ensure_ascii=False))
    await jobs.aclose()

asyncio.run(main())
"""


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    pytest.importorskip("mcp")
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(("JENNIFER_", "APM_"))},
           "PYTHONPATH": "."}
    proc = subprocess.run([sys.executable, "-c", _DUMP], cwd=GW_ROOT, env=env,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


# ── 모의 게이트웨이(본체 인자 포착) ────────────────────────────────────────────────

def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-06T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


class _Capture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    @staticmethod
    def _single(name: str, args: dict) -> dict:
        if name == "apm_instance_map":
            if args.get("query") or args.get("business"):  # 대상 텍스트 해석 — 인스턴스 2개
                return _env(name, [
                    {"source_id": "bank", "instance_id": 1002, "instance_name": "abc-was02",
                     "domain_id": 2000, "hostname": "", "match_confidence": None,
                     "match_tier": "exact"},
                    {"source_id": "common", "instance_id": 3003, "instance_name": "abc-was02",
                     "domain_id": 2000, "hostname": "", "match_confidence": None,
                     "match_tier": "exact"}])
            if not args.get("hostname") and "targets" not in args:  # 첫 홉 — 정합 2 · 호스트 없음 1
                return _env(name, [
                    {"source_id": "bank", "instance_id": 11, "instance_name": "w1",
                     "domain_id": 1, "hostname": "web01", "match_confidence": "exact"},
                    {"source_id": "bank", "instance_id": 12, "instance_name": "w2",
                     "domain_id": 1, "hostname": "web02", "match_confidence": "exact"},
                    {"source_id": "common", "instance_id": 13, "instance_name": "batch-was",
                     "domain_id": 1, "hostname": "", "match_confidence": None}])
            return _env(name, [{"hostname": args.get("hostname"), "instance_id": 11,
                                "match_confidence": "exact", "source_id": "bank"}])
        return _env(name, [{"tool_row": name, "hostname": args.get("hostname")}])

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, json.loads(json.dumps(
            {k: v for k, v in arguments.items() if v is not None}))))
        reply = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:1/sse"},
                        source_tokens='{"apm": "gw"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]))


# ── 사례 생성(레지스트리에서) ───────────────────────────────────────────────────

_TEXT = {"service": "주문", "search": "OrderService", "application_name": "/order/list",
         "key": "JAVA_OPTS"}
_STR = {"sort_by": "responseTime", "error_type": "OUTOFMEMORY"}


def _value(view, arg) -> Any:
    if arg.type == "int":
        return {"ref": 1, "process_id": 4242, "domain_id": 1000}.get(arg.name, (arg.min or 1) + 2)
    if arg.type == "bool":
        return True
    if arg.type == "str_list":
        return ["heap_used_mb"] if arg.catalog == "instance" else ["response_time_avg_ms"]
    if arg.type == "text":
        return _TEXT.get(arg.name, "abc")
    if arg.type in ("str", "token"):
        return _STR.get(arg.name, "OUTOFMEMORY")
    if arg.type == "opaque":
        return "g-1"
    if arg.type == "account":
        return "op01"
    if arg.type == "catalog":
        return "instance"
    raise AssertionError((view.id, arg.name, arg.type))


def _arg_variants(view) -> list[tuple[str, dict]]:
    """보기 조건 사례 — 기본(조건 없음) · 비enum 조건 전부 · enum 선택지마다(다른 조건은 비움)."""
    plain = {a.name: _value(view, a) for a in view.args if a.type != "enum"}
    out: list[tuple[str, dict]] = [("noargs", {})]
    if plain:
        out.append(("allplain", plain))
    for a in view.args:
        if a.type == "enum":
            for choice in a.choices:
                required = {r.name: _value(view, r) for r in view.args
                            if r.required and r.type != "enum"}
                out.append((f"{a.name}={choice}", {**required, a.name: choice}))
    return out


def _target_variants(view) -> list[tuple[str, dict, dict]]:
    """(이름, task 덧붙임, isolated 덧붙임)."""
    hosts1 = {"hosts": ("web01",)}
    hosts2 = {"hosts": ("web01", "web02")}
    if view.target == "named":
        return [("all", {}, {}), ("names2", {"targets": [{"text": "주문", "kind": "auto"},
                                                         {"text": "결제", "kind": "business"}]},
                                  {})]
    if view.target == "reference":
        ref = view.reference
        rows = ([_active(0), _active(1)] if ref == "active_ref"
                else [_tx(1), _tx(2), _error_row(3)])
        return [("ref", {}, {"ctx": _ctx(rows)})] + (
            [("guid-2hosts", {}, {**hosts2})] if ref == "guid" else [])
    if view.target in ("none",):
        return [("none", {}, {})]
    if view.target == "optional":
        return [("none", {}, {}), ("host1", {}, hosts1), ("hosts2", {}, hosts2)]
    if view.first_hop:
        return [("none", {}, {}), ("hosts2", {}, hosts2)]
    if view.required_input:
        return [("host1", {}, hosts1), ("hosts2", {}, hosts2),
                ("instances", {"targets": [{"text": "abc-was02", "kind": "instance"}]}, {}),
                ("first-hop", {}, {})]
    return [("none", {}, {}), ("hosts2", {}, hosts2)]


def _cases() -> list[tuple[str, str, dict, dict, dict, list[str] | None]]:
    out = []
    for view in get_registry().views_of("apm"):
        for tname, task_extra, iso_extra in _target_variants(view):
            for aname, args in _arg_variants(view):
                if view.target == "reference" and "ref" not in args and aname != "noargs":
                    args = {**args, "ref": 1} if any(a.name == "ref" for a in view.args) else args
                if view.reference == "guid" and tname == "guid-2hosts":
                    args = {**{k: v for k, v in args.items() if k != "ref"}, "guid": "g-1"}
                out.append((f"{view.id}|{tname}|{aname}", view.id, task_extra, iso_extra, args,
                            None))
            # 소스 지목(대상 모양마다 1번 · 조건 없음)
            out.append((f"{view.id}|{tname}|sources", view.id, task_extra, iso_extra, {},
                        ["bank"]))
    return out


_CASES = _cases()


def _isolated(extra: dict) -> dict:
    hosts = extra.get("hosts", ())
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts],
                "time_range": _RANGE, "limit": None},
            "conversation_context": extra.get("ctx") or {}, "thread_id": "th-1",
            "user_id": "alice", "original_user_query": "q", "user_query": "q"}


def _item_errors(item: Any, keys: list[str]) -> list[str]:
    if not isinstance(item, dict):
        return [f"항목이 객체가 아님: {item!r}"]
    errs = [f"모르는 항목 키 {k}" for k in item if k not in keys]
    if "hostname" not in item and "instance_name" not in item:
        errs.append("hostname·instance_name 둘 다 없음")
    if "hostname" in item and not (isinstance(item["hostname"], str) and item["hostname"].strip()):
        errs.append(f"hostname 형식 {item['hostname']!r}")
    if "instance_name" in item and not (isinstance(item["instance_name"], str)
                                        and 1 <= len(item["instance_name"]) <= 200):
        errs.append(f"instance_name 형식 {item['instance_name']!r}")
    iid = item.get("instance_id")
    if iid is not None and (isinstance(iid, bool) or not isinstance(iid, int) or iid < 0):
        errs.append(f"instance_id 형식 {iid!r}")
    if "source_id" in item and item["source_id"] not in ("bank", "common", "legacy"):
        errs.append(f"source_id {item['source_id']!r}")
    return errs


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _CASES, ids=[c[0] for c in _CASES])
async def test_every_view_argument_shape_matches_the_gateway(case, contract, monkeypatch) -> None:
    from jsonschema import Draft202012Validator

    cid, vid, task_extra, iso_extra, args, sources = case
    cap = _Capture()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", cap.factory())
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "sub_query": "q",
                            "views": [vid], "input_from": [], **task_extra}
    if args:
        task["view_args"] = {vid: args}
    if sources:
        task["sources"] = sources
    view = next(v for v in get_registry().views_of("apm") if v.id == vid)
    isolated = _isolated(iso_extra)
    if view.window == "compare":  # 두 기간 비교(plans/134 W6 A-1) — 기간 해석이 있어야 부른다
        isolated["time_resolution"] = resolve_query_time("지난주 대비", NOW).to_state()
    res = await aq.run_apm_query(task, isolated, llm=None, app_config=_cfg(), now=NOW)
    sent = [(n, a) for n, a in cap.calls if not n.startswith("apm_job_")]
    if not sent:  # 되묻기(필수 조건) 사례만 호출 0이 정상
        assert any(a.required for a in view.args) or view.target == "reference", (cid, res)
        return
    assert any(n == view.tool for n, _ in sent), (cid, sent, res.get("final_response"))
    for name, call in sent:
        schema = contract["schemas"][name]
        unknown = set(call) - set(schema["properties"])
        assert not unknown, f"{cid}: {name} 게이트웨이가 모르는 인자(조용히 버려진다): {unknown}"
        missing = set(schema.get("required") or []) - set(call)
        assert not missing, f"{cid}: {name} 필수 인자 누락 {missing}"
        errors = [e.message for e in Draft202012Validator(schema).iter_errors(call)]
        assert not errors, f"{cid}: {name} {errors}"
        if "targets" in call:
            keys = contract["target_keys"][name]
            assert isinstance(keys, list), (name, keys)
            assert len(call["targets"]) >= 2, f"{cid}: 대상 1개는 배치가 아니다"
            for i, item in enumerate(call["targets"]):
                assert not _item_errors(item, keys), (cid, name, i, item, keys)
            assert not {"hostname", "instance_name", "instance_id"} & set(call), (cid, call)
        if sources and name != "apm_job_status":
            items = call.get("targets") or []
            assert call.get("source_ids") == sources or all(
                "source_id" in i for i in items) and items, (cid, name, call)


def test_case_matrix_covers_every_view_and_every_choice() -> None:
    views = get_registry().views_of("apm")
    assert len(views) == 29  # plans/134 W6 A-1 — apm.period_compare
    covered = {c[1] for c in _CASES}
    assert covered == {v.id for v in views}
    for v in views:
        for a in v.args:
            if a.type == "enum":
                for choice in a.choices:
                    assert any(c[1] == v.id and f"{a.name}={choice}" in c[0] for c in _CASES)


def test_ranking_metric_enum_equals_gateway_ranking_metrics(contract) -> None:
    view = next(v for v in get_registry().views_of("apm") if v.id == "apm.ranking")
    choices = next(a.choices for a in view.args if a.name == "metric")
    # plans/134 W6 A-4 교정 — 레지스트리도 기간 전용 지표 4개를 싣는다(실시간 지표가 앞 ·
    # 순서까지 같다)
    assert list(choices) == contract["all_ranking_metrics"], "순서까지 같다"
    assert list(choices)[:len(contract["ranking_metrics"])] == contract["ranking_metrics"]
    prop = contract["schemas"]["apm_fleet"]["properties"]["metric"]
    enum = prop.get("enum") or next((x.get("enum") for x in prop.get("anyOf") or []
                                     if x.get("enum")), None)
    # plans/134 W6 A-4 — 게이트웨이 스키마 enum = 실시간 지표 + 기간 전용 지표 = 레지스트리 enum
    assert list(enum or []) == contract["all_ranking_metrics"] == list(choices), prop


def test_every_targets_tool_accepts_the_body_item_keys(contract) -> None:
    """본체가 배치로 부르는 도구(대상 보기 도구)가 모두 `targets`를 받는다(빠지면 대상별
    `batch_missing` 실패)."""
    batch_tools = {v.tool for v in get_registry().views_of("apm")
                   if v.required_input or v.target in ("optional",) or v.first_hop
                   or v.reference == "guid"}
    assert batch_tools <= set(contract["target_keys"]), batch_tools - set(contract["target_keys"])
