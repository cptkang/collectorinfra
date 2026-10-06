"""plans/134 W3·W4 독립 검증(verify-134-w34 · 2026-10-06) — 게이트웨이 적대·경계·보안·알람 불변.

구현 에이전트 시험(`test_plan134_w3_*`·`test_plan134_w34_scope`)과 **다른 입력**으로 다시 잰다.

1. `targets` 형식(HTTP 0회) — 모르는 키 · 도구가 받지 않는 키 · 형 · 빈 값 · 제어 문자 · 최상위 대상
   인자와 함께 줌 · 1,000개 규모(선형성 · 예상 = 배치 전체).
2. 하위 봉투 == 단건 봉투 — 두 소스에 같은 hostname · 이름+소스 항목 · 한 소스 조회 불가(하위
   partial) · 이름 AND hostname.
3. 배치 전부 실패(코드 섞임 → `apm_api_error` · 같으면 그 코드) · 일부 실패 고지.
4. 버퍼 — 밀어내기 뒤 그 구간은 API(0건 아님) · 보관 경과 sweep · 재기동(빈 버퍼) · 확정 경계 1ms.
5. 순위 — 값 None·NaN 제외 · 동점 결정적 순서(desc·asc · 소스 순서 → 도메인 → 인스턴스).
6. 서비스·업무 단계 검색 모호(여럿 일치 → 전부 + `[한계]`) · `service: []`·`business: []` ·
   명시 이름 미해결의 대체 조회 0(데이터 경로 HTTP 0).
7. 보안 — 자격증명 카나리아(W7 비밀 · 소스 토큰)가 배치 경로(`apm_environment`·`apm_config`)의
   반환·스풀·작업 기록·감사·로그·오류 사유 어디에도 없음 · 감사 대상 = `targets(N)`.
8. 알람 불변 — 폴러 XADD 페이로드·멱등 키·커서가 버퍼 없음/있음/밀어내기 중/기록 예외에서 바이트
   같다(독립 재현).

외부 네트워크 0 — `httpx.MockTransport` · 가상 시계 · 가짜 Redis.
"""

from __future__ import annotations

import json
import logging
import math
import time
from types import SimpleNamespace

import httpx
import pytest
from apm_gateway.application.event_buffer import EventBuffer
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.poller import OVERLAP_MS, EventPoller
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.errors import API_ERROR, INSTANCE_UNRESOLVED, INVALID_ARGUMENT
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import NOW_MS, NOW_S
from fleet_fake import Clock, FakeFleet, event
from jennifer_catalog import match_template
from mock_openapi import W7_SECRET, W7_TEMPLATES, W7Mock, w7_response
from test_poller import FakeRedis

from apm_gateway.config import JobConfig, load_config

T0 = NOW_MS
DATA_PATHS = {"/api/realtime/instance", "/api/realtime/domain", "/api/realtime/business",
              "/api/dbsearch/event", "/api/dbmetrics/domain", "/api/dbmetrics/business",
              "/api/activeService/list"}


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


class Fleet(FakeFleet):
    """`FakeFleet` + 소스 간 같은 hostname(선택) · 서비스(도메인) 이름 · 업무 · W7 경로
    (카나리아)."""

    def __init__(self, domains: int, *, shared_hosts: bool = False,
                 names: dict[int, str] | None = None,
                 businesses: dict[int, list[tuple[int, str]]] | None = None, **kw) -> None:
        super().__init__(domains, domain_names=names, **kw)
        self.shared_hosts = shared_hosts
        self.businesses = businesses or {}
        self.w7 = SimpleNamespace(w7=W7Mock(), mode="connected")
        self.instance_fail: set[tuple[str, int]] = set()

    def http(self) -> list[tuple[str, str, dict]]:
        return list(self.calls)

    def data_calls(self) -> list[tuple[str, str, dict]]:
        return [c for c in self.calls if c[1] in DATA_PATHS]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        query = dict(request.url.params)
        sid = host.split(".")[0]
        template = match_template(path)
        if template in W7_TEMPLATES:
            self.calls.append((host, path, query))
            status, body, _ = w7_response(self.w7, request.method, template, query, path)
            if template == "/api-v2/environment-variable/{domainId}" and isinstance(body, dict):
                d = int(path.rsplit("/", 1)[-1])  # 합성 본문(인스턴스 1001)을 이 도메인 인스턴스로
                body = {str(d * 100 + k): body["1001"] for k in range(self.per_domain)}
            return httpx.Response(status, json=body) if body is not None else httpx.Response(
                status)
        d = int(query.get("domain_id", "0") or 0)
        if path == "/api/instance" and self.shared_hosts:
            self.calls.append((host, path, query))
            return httpx.Response(200, json={"result": [
                {"instanceId": d * 100 + k, "name": f"{sid}-i{d}-{k}", "hostName": f"h{d}-{k}",
                 "ipAddress": "10.0.0.1", "platform": "JAVA", "status": "RUNNING",
                 "version": "5.6.5"} for k in range(self.per_domain)]})
        if path == "/api/realtime/instance" and (host, d) in self.instance_fail:
            self.calls.append((host, path, query))
            return httpx.Response(500, json={"exception": {"message": "Domain is not connected"}})
        if path == "/api/realtime/instance" and query.get("instance_id"):
            self.calls.append((host, path, query))
            ids = {int(x) for x in query["instance_id"].split(",")}
            rows = [r for r in self.realtime.get((host, d), []) if r["instanceId"] in ids]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/business":
            self.calls.append((host, path, query))
            return httpx.Response(200, json={"result": [
                {"businessId": b, "name": n, "description": "", "ruleList": []}
                for b, n in self.businesses.get(d, [])]})
        if path == "/api/realtime/business":
            self.calls.append((host, path, query))
            rows = [{"domainId": d, "businessId": b, "businessName": n, "tps": float(b)}
                    for b, n in self.businesses.get(d, [])
                    if not query.get("business_id") or str(b) == query["business_id"]]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/realtime/domain":
            self.calls.append((host, path, query))
            ds = [int(query["domain_id"])] if query.get("domain_id") else self.domains
            return httpx.Response(200, json={"result": [{"domainId": x, "tps": float(x)}
                                                        for x in ds]})
        if path == "/api/activeService/list":
            self.calls.append((host, path, query))
            return httpx.Response(200, json={"result": []})
        if path in ("/api/dbsearch/error", "/api/transaction/time"):
            self.calls.append((host, path, query))
            return httpx.Response(200, json={"result": []})
        return await super().handler(request)


def _stack(fake: Fleet, tmp_path, *, buffer: EventBuffer | None = None, clock=lambda: NOW_S,
           **job_cfg):
    cfg = load_config(fake.env(APM_SPOOL_DIR=str(tmp_path / "spool")))
    tools = ApmTools(build_source_set(cfg, transport=httpx.MockTransport(fake.handler)), cfg,
                     clock=clock)
    jcfg = JobConfig(spool_dir=tmp_path / "spool", **job_cfg)
    jobs = JobManager(Spool(jcfg.spool_dir), jcfg, envelope=tools.ok, error_envelope=tools.err,
                      rate_per_sec=0.0, on_finish=audit_job_finished)
    return create_server(tools, jobs=jobs, event_buffer=buffer), tools, jobs


async def _call(mcp, tool: str, **args) -> dict:
    return _json(await mcp.call_tool(tool, args))


# ── 1. targets 형식 — HTTP 0회 ──────────────────────────────────────────────────

_BAD = [
    ("empty-list", "apm_app_health", {"targets": []}),
    ("unknown-key", "apm_app_health", {"targets": [{"hostname": "bank-h1-0", "domain_id": 1}]}),
    ("no-host-no-name", "apm_app_health", {"targets": [{"instance_id": 100}]}),
    ("blank-host", "apm_app_health", {"targets": [{"hostname": "   "}]}),
    ("host-not-str", "apm_app_health", {"targets": [{"hostname": 7}]}),
    ("blank-name", "apm_app_health", {"targets": [{"instance_name": "  "}]}),
    ("long-name", "apm_app_health", {"targets": [{"instance_name": "x" * 201}]}),
    ("id-bool", "apm_app_health", {"targets": [{"hostname": "bank-h1-0", "instance_id": True}]}),
    ("id-negative", "apm_app_health", {"targets": [{"hostname": "bank-h1-0", "instance_id": -1}]}),
    ("id-str", "apm_app_health", {"targets": [{"hostname": "bank-h1-0", "instance_id": "100"}]}),
    ("id-float", "apm_app_health", {"targets": [{"hostname": "bank-h1-0", "instance_id": 1.5}]}),
    ("source-unknown", "apm_app_health", {"targets": [{"hostname": "bank-h1-0",
                                                       "source_id": "zz"}]}),
    ("source-not-str", "apm_app_health", {"targets": [{"hostname": "bank-h1-0",
                                                       "source_id": 1}]}),
    ("id-on-events", "apm_events", {"targets": [{"hostname": "bank-h1-0", "instance_id": 100}]}),
    ("name-on-instance-map", "apm_instance_map", {"targets": [{"instance_name": "bank-i1-0"}]}),
    ("with-top-hostname", "apm_app_health", {"hostname": "bank-h1-0",
                                             "targets": [{"hostname": "bank-h1-1"}]}),
    ("with-top-instance-id", "apm_resource_pool", {"instance_id": 100,
                                                   "targets": [{"hostname": "bank-h1-1"}]}),
    ("top-source-unknown", "apm_app_health", {"source_ids": ["zz"],
                                              "targets": [{"hostname": "bank-h1-1"}]}),
    ("second-item-bad", "apm_config", {"kind": "db_path",
                                       "targets": [{"hostname": "bank-h1-0"}, {"host": "x"}]}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _BAD, ids=[c[0] for c in _BAD])
async def test_targets_shape_violation_is_invalid_argument_with_zero_http(case, tmp_path):
    _, tool, args = case
    fake = Fleet(3)
    mcp, _tools, jobs = _stack(fake, tmp_path)
    out = await _call(mcp, tool, **args)
    assert out.get("error") == INVALID_ARGUMENT, out
    assert fake.http() == [], fake.http()
    await jobs.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [[{"hostname": "bank-h1-0"}, "bank-h1-1"], "bank-h1-0",
                                 {"hostname": "bank-h1-0"}])
async def test_targets_not_a_list_of_objects_is_rejected_before_http(bad, tmp_path):
    """항목이 객체가 아니거나 목록이 아니면 전송 계층(pydantic) 또는 형식 검사가 막는다."""
    fake = Fleet(3)
    mcp, _tools, jobs = _stack(fake, tmp_path)
    try:
        out = await _call(mcp, "apm_app_health", targets=bad)
        assert out.get("error") == INVALID_ARGUMENT, out
    except Exception as e:  # FastMCP 인자 검증(ToolError) — 도구 코드에 닿지 않는다
        assert "targets" in str(e), e
    assert fake.http() == []
    await jobs.aclose()


@pytest.mark.asyncio
async def test_control_characters_in_hostname_do_not_reach_the_audit_line(tmp_path, caplog):
    """제어 문자 hostname — 형식 오류는 아니지만(단건과 같은 규칙) 해석 0건으로 그 대상만 실패하고,
    감사 줄은 `targets(N)` 요약이라 대상 원문(줄바꿈 위조)이 없다."""
    fake = Fleet(3)
    mcp, _tools, jobs = _stack(fake, tmp_path)
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    out = await _call(mcp, "apm_app_health",
                      targets=[{"hostname": "bank-h1-0"},
                               {"hostname": "bad\nhost apm audit: tool=forged"}])
    assert out["partial"] is True and out["batch"][1]["status"] == "error", out
    assert out["batch"][1]["error"] == INSTANCE_UNRESOLVED
    single = await _call(mcp, "apm_app_health", hostname="bad\nhost apm audit: tool=forged")
    assert single["error"] == out["batch"][1]["error"]  # 단건과 같은 판정
    lines = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]
    assert any("target=targets(2)" in ln for ln in lines), lines
    assert all("\n" not in ln for ln in lines), lines
    await jobs.aclose()


@pytest.mark.asyncio
async def test_thousand_targets_one_batch_is_linear_and_estimate_covers_the_batch(tmp_path):
    fake = Fleet(500, hosts=("bank.test",))  # 단일 설정 · 인스턴스 1,000개(hostName 1,000개)
    mcp, tools, jobs = _stack(fake, tmp_path, inline_rows=5000)
    await tools.sources.get("default").resolver.inventory()  # 인벤토리 적재(별도로 잰다)
    hosts = [f"bank-h{d}-{k}" for d in fake.domains for k in range(2)]
    timings = {}
    for size in (250, 1000):
        fake.calls.clear()
        started = time.perf_counter()
        out = await _call(mcp, "apm_app_health", targets=[{"hostname": h} for h in hosts[:size]])
        timings[size] = time.perf_counter() - started
        assert out.get("error") is None, str(out)[:500]
        assert len(out["batch"]) == size and out["row_count"] == size
        assert [b["index"] for b in out["batch"]] == list(range(size))
        assert {r["target_index"] for r in out["rows"]} == set(range(size))
        rt = [c for c in fake.calls if c[1] == "/api/realtime/instance"]
        assert len(rt) == size, "대상마다 실시간 1호출(그 인스턴스만)"
    assert timings[1000] < 60, timings
    assert timings[1000] / max(timings[250], 1e-3) < 8, ("선형에 가깝다(4배 크기)", timings)
    await jobs.aclose()


@pytest.mark.asyncio
async def test_promoted_thousand_batch_estimate_is_the_whole_batch(tmp_path):
    fake = Fleet(500, hosts=("bank.test",))
    mcp, tools, jobs = _stack(fake, tmp_path, inline_rows=5000)
    await tools.sources.get("default").resolver.inventory()
    hosts = [f"bank-h{d}-{k}" for d in fake.domains for k in range(2)]
    out = await _call(mcp, "apm_app_health", targets=[{"hostname": h} for h in hosts],
                      wait_seconds=0.001, owner="user:alice")
    if "job" in out and out["job"]["state"] in ("queued", "running"):
        import asyncio

        await asyncio.sleep(0.05)  # 첫 하위 호출이 계획을 신고한 뒤
        mid = await _call(mcp, "apm_job_status", job_id=out["job"]["job_id"], owner="user:alice")
        if mid["job"]["state"] in ("queued", "running"):
            assert mid["job"]["estimate"]["api_calls"] >= 1000, mid["job"]
            assert mid["job"]["progress"]["total"] >= 1000, mid["job"]
        for _ in range(400):
            st = await _call(mcp, "apm_job_status", job_id=out["job"]["job_id"], owner="user:alice")
            if st["job"]["state"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.02)
        prog = st["job"]["progress"]
        assert prog["done"] == prog["total"] == 1000, prog
        assert st["job"]["estimate"]["api_calls"] == 1000
    else:  # 너무 빨라 승격되지 않았다 — 잴 것이 없다
        pytest.skip("1,000건이 대기 시간 안에 끝나 승격되지 않음")
    await jobs.aclose()


# ── 2. 하위 봉투 == 단건 봉투(다른 입력) ─────────────────────────────────────────

_HEAD = {"rows", "row_count", "queried_at", "source", "source_kind", "tool", "total_row_count"}
_ITEM = {"index", "target", "status", "row_count"}


def _strip_single(env: dict) -> dict:
    return {k: v for k, v in env.items() if k not in _HEAD}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,extra", [
    ("apm_app_health", {}),
    ("apm_resource_pool", {}),
    ("apm_events", {"lookback_minutes": 20}),
    ("apm_instance_map", {}),
])
async def test_sub_envelope_equals_single_call_on_shared_hosts_and_down_source(tool, extra,
                                                                              tmp_path):
    # 두 소스에 같은 hostname(h<d>-<k>) · common 도메인 3 조회 불가
    fake = Fleet(4, shared_hosts=True)
    fake.instance_fail.add(("common.test", 3))
    fake.event_fail.add(("common.test", 3))
    mcp, _tools, jobs = _stack(fake, tmp_path)
    items = [{"hostname": "h2-0"}, {"hostname": "h3-1"}, {"hostname": "h4-0", "source_id": "bank"}]
    if tool != "apm_instance_map":
        items += [{"instance_name": "common-i2-1", "source_id": "common"},
                  {"hostname": "h2-1", "instance_name": "bank-i2-1"}]
    batch = await _call(mcp, tool, targets=items, **extra)
    assert batch.get("error") is None, batch
    for i, item in enumerate(items):
        args = {("source_ids" if k == "source_id" else k): ([v] if k == "source_id" else v)
                for k, v in item.items()}
        single = await _call(mcp, tool, **args, **extra)
        entry = batch["batch"][i]
        if "error" in single:
            assert entry["status"] == "error" and entry["error"] == single["error"], (i, entry)
            continue
        sub = {k: v for k, v in entry.items() if k not in _ITEM}
        mine = [{k: v for k, v in r.items() if k != "target_index"} for r in batch["rows"]
                if r["target_index"] == i]
        assert mine == single["rows"], (tool, i)
        assert json.dumps(sub, sort_keys=True, default=str) == json.dumps(
            _strip_single(single), sort_keys=True, default=str), (tool, i, sub, single)
    if tool != "apm_instance_map":  # 명단 도구는 실시간을 부르지 않는다
        # 한 항목의 하위 partial(소스 하나 조회 불가)이 배치에 선다
        assert batch.get("partial") is True
    await jobs.aclose()


# ── 3. 전부 실패 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_failed_mixed_codes_is_api_error_and_same_code_is_kept(tmp_path):
    fake = Fleet(3, hosts=("bank.test",))
    fake.instance_fail.add(("bank.test", 2))
    mcp, _tools, jobs = _stack(fake, tmp_path)
    same = await _call(mcp, "apm_app_health", targets=[{"hostname": "zz1"}, {"hostname": "zz2"}])
    assert same["error"] == INSTANCE_UNRESOLVED and len(same["batch"]) == 2, same
    assert same["reason"].startswith("대상 2개 모두 실패 — "), same["reason"]
    mixed = await _call(mcp, "apm_app_health",
                        targets=[{"hostname": "zz1"}, {"hostname": "bank-h2-0"}])
    assert mixed["error"] == API_ERROR, mixed
    assert [b["status"] for b in mixed["batch"]] == ["error", "error"]
    await jobs.aclose()


# ── 4. 버퍼 경계(밀어내기 · sweep · 재기동 · 확정 경계) ─────────────────────────────

def _poller(fake: Fleet, clock: Clock, redis: FakeRedis, buffer):
    cfg = load_config(fake.env(APM_EVENT_POLLER_ENABLED="true"))
    sources = build_source_set(cfg, transport=httpx.MockTransport(fake.handler))
    return EventPoller(sources, cfg, redis, clock=clock, event_buffer=buffer)


def _events_fake() -> Fleet:
    fake = Fleet(2, hosts=("bank.test",))
    for d in fake.domains:
        for j in range(12):
            fake.add_event("bank.test", event(d, d * 100 + j % 2, T0 + j * 20_000 + d,
                                              level=("FATAL", "WARNING")[j % 2], txid=str(j)))
    return fake


async def _run_polls(poller, clock: Clock, until_s: float) -> None:
    while clock.t <= until_s:
        await poller.poll_once()
        clock.t += 30


@pytest.mark.asyncio
async def test_evicted_span_is_answered_by_the_api_not_as_zero(tmp_path):
    fake = _events_fake()
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock, max_events=10)  # 2도메인 × 12건 → 밀어내기
    poller = _poller(fake, clock, FakeRedis(), buffer)
    await _run_polls(poller, clock, NOW_S + 300)
    assert buffer.evicted_total > 0
    mcp, _tools, jobs = _stack(fake, tmp_path, buffer=buffer, clock=clock)
    fake.calls.clear()
    out = await _call(mcp, "apm_fleet", mode="events", lookback_minutes=10, full=True)
    from datetime import datetime

    lo, hi = (int(datetime.fromisoformat(out["window"][k]).timestamp() * 1000)
              for k in ("start", "end"))
    total = sum(1 for (_h, _d), evs in fake.events.items() for e in evs
                if lo <= int(e["time"]) <= hi + 999)
    assert out["row_count"] == total, (out["coverage"], out["row_count"], total)
    assert out["coverage"]["from_buffer"] == 0, "밀어낸 구간을 버퍼만으로 답하지 않는다"
    assert fake.event_calls(), "API로 보충"
    await jobs.aclose()


@pytest.mark.asyncio
async def test_retention_sweep_restart_and_one_ms_boundary(tmp_path):
    fake = _events_fake()
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock, retention_minutes=5)
    poller = _poller(fake, clock, FakeRedis(), buffer)
    await _run_polls(poller, clock, NOW_S + 240)
    confirmed_end = int((clock.t - 30) * 1000) - OVERLAP_MS
    # 확정 경계 1ms — 끝을 넘기면 그 1ms만 빈 구간
    assert buffer.gaps("default", 1, confirmed_end - 1000, confirmed_end) == []
    assert buffer.gaps("default", 1, confirmed_end - 1000, confirmed_end + 1) == [
        (confirmed_end + 1, confirmed_end + 1)]
    # 보관 경과 → sweep이 키를 지운다(이벤트·확정 구간 0)
    clock.t += 6 * 60
    status = buffer.status()
    assert status["events"] == 0 and status["domains"] == 0, status
    # 재기동 = 빈 버퍼 → 전 도메인 API · 결과는 버퍼 없는 조회와 같다
    restarted = EventBuffer(clock=clock)
    mcp, _tools, jobs = _stack(fake, tmp_path, buffer=restarted, clock=clock)
    a = await _call(mcp, "apm_fleet", mode="events", lookback_minutes=30, full=True)
    mcp2, _t2, jobs2 = _stack(fake, tmp_path / "nb", buffer=None, clock=clock)
    b = await _call(mcp2, "apm_fleet", mode="events", lookback_minutes=30, full=True)
    assert a["coverage"]["from_api"] == 2 and a["rows"] == b["rows"]
    await jobs.aclose()
    await jobs2.aclose()


# ── 5. 순위 None·NaN·동점 ─────────────────────────────────────────────────────

class _TieFleet(Fleet):
    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/realtime/instance":
            host = request.url.host
            sid = host.split(".")[0]
            d = int(request.url.params["domain_id"])
            self.calls.append((host, request.url.path, dict(request.url.params)))
            values = {0: 50.0, 1: 50.0, 2: "NaN", 3: None}
            rows = []
            for k, v in values.items():
                raw = {"domainId": d, "instanceId": d * 100 + k, "instanceName": f"{sid}-{d}-{k}"}
                if v is not None:
                    raw["responseTime"] = v
                rows.append(raw)
            return httpx.Response(200, json={"result": rows})
        return await super().handler(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["desc", "asc"])
async def test_ranking_ties_none_and_nan_are_deterministic(order, tmp_path):
    fake = _TieFleet(2, per_domain=4)
    mcp, _tools, jobs = _stack(fake, tmp_path)
    out = await _call(mcp, "apm_fleet", mode="ranking", order=order, full=True)
    got = [(r["source_id"], r["domain_id"], r["instance_id"]) for r in out["rows"]]
    expected = [(s, d, d * 100 + k) for s in ("bank", "common") for d in (1, 2) for k in (0, 1)]
    assert got == expected, got  # 모두 50 — 소스 순서 → 도메인 → 인스턴스(정렬 방향과 무관)
    s = out["summary"]
    assert (s["instances_total"], s["instances_ranked"], s["instances_unranked"]) == (16, 8, 8)
    assert all(not (isinstance(r["value"], float) and math.isnan(r["value"])) for r in out["rows"])
    top = await _call(mcp, "apm_fleet", mode="ranking", order=order, n=3)
    assert [(r["source_id"], r["domain_id"], r["instance_id"]) for r in top["rows"]] == \
        expected[:3]
    await jobs.aclose()


# ── 6. 서비스·업무 모호 · 빈 목록 · 미해결 대체 조회 0 ───────────────────────────────

def _named_fleet() -> Fleet:
    return Fleet(5, names={1: "결제", 2: "order-api", 3: "order-batch", 4: "pay-batch",
                           5: "주문"},
                 businesses={1: [(11, "카드결제"), (12, "결제")], 2: [(21, "결제")],
                             3: [(31, "주문접수")]})


@pytest.mark.asyncio
async def test_stage_search_ambiguity_queries_all_matches_and_discloses(tmp_path):
    fake = _named_fleet()
    mcp, _tools, jobs = _stack(fake, tmp_path)
    out = await _call(mcp, "apm_service_status", service="order")  # prefix: order-api·order-batch
    keys = sorted((r["source_id"], r["domain_id"]) for r in out["rows"])
    assert keys == [("bank", 2), ("bank", 3), ("common", 2), ("common", 3)], keys
    assert {r["match_tier"] for r in out["rows"]} == {"prefix"}
    assert any("이름이 맞은 도메인 4곳을 모두 조회했다(prefix 단계)" in x for x in out["limits"]), \
        out["limits"]
    contains = await _call(mcp, "apm_service_status", service="batch")  # contains 두 곳 × 두 소스
    assert sorted((r["source_id"], r["domain_id"]) for r in contains["rows"]) == [
        ("bank", 3), ("bank", 4), ("common", 3), ("common", 4)]
    assert {r["match_tier"] for r in contains["rows"]} == {"contains"}
    exact = await _call(mcp, "apm_service_status", service="order-api")  # exact가 이긴다
    assert sorted((r["source_id"], r["domain_id"]) for r in exact["rows"]) == [
        ("bank", 2), ("common", 2)]
    biz = await _call(mcp, "apm_business", business="결제")  # exact 결제(도메인 1·2 × 두 소스)
    assert sorted((r["source_id"], r["domain_id"], r["business_id"]) for r in biz["rows"]) == [
        ("bank", 1, 12), ("bank", 2, 21), ("common", 1, 12), ("common", 2, 21)]
    assert any("이름이 맞은 업무 4건을 모두 조회했다(exact 단계)" in x for x in biz["limits"])
    await jobs.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("apm_service_status", {"service": []}),
    ("apm_service_status", {"service": [""]}),
    ("apm_business", {"business": []}),
    ("apm_business", {"service": []}),
    ("apm_fleet", {"mode": "ranking", "service": []}),
    ("apm_fleet", {"mode": "events", "service": []}),
    ("apm_metrics", {"mode": "series", "scope": "domain", "metrics": ["service_time"],
                     "service": []}),
    ("apm_metrics", {"mode": "series", "scope": "business", "metrics": ["service_time"],
                     "business": []}),
])
async def test_empty_name_list_is_invalid_not_everything(tool, args, tmp_path):
    fake = _named_fleet()
    mcp, _tools, jobs = _stack(fake, tmp_path)
    out = await _call(mcp, tool, **args)
    assert out.get("error") == INVALID_ARGUMENT, out
    assert fake.data_calls() == []
    await jobs.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("apm_service_status", {"service": "없는서비스"}),
    ("apm_service_status", {"service": ["없는서비스", "또없음"]}),
    ("apm_business", {"business": "없는업무"}),
    ("apm_business", {"service": "없는서비스", "business": "결제"}),
    ("apm_fleet", {"mode": "ranking", "service": "없는서비스"}),
    ("apm_fleet", {"mode": "events", "service": "없는서비스"}),
    ("apm_fleet", {"mode": "ranking", "service": "주문", "domain_id": 1}),  # AND 0곳(주문=5)
    ("apm_metrics", {"mode": "series", "scope": "domain", "metrics": ["service_time"],
                     "service": "없는서비스"}),
    ("apm_metrics", {"mode": "series", "scope": "business", "metrics": ["service_time"],
                     "business": "없는업무"}),
])
async def test_unresolved_explicit_name_makes_no_substitute_data_call(tool, args, tmp_path):
    fake = _named_fleet()
    mcp, _tools, jobs = _stack(fake, tmp_path)
    out = await _call(mcp, tool, **args)
    assert out.get("error") is None, out
    assert out["rows"] == [] and fake.data_calls() == [], fake.data_calls()
    kinds = {d.get("kind") for d in out.get("disclosures") or []}
    assert "apm_unresolved_condition" in kinds, out
    if args.get("service") == "없는서비스" and tool == "apm_business":
        assert not [c for c in fake.calls if c[1] == "/api/business"], "업무 정의도 묻지 않는다"
    await jobs.aclose()


# ── 7. 보안 — 배치 경로 카나리아 ─────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("apm_environment", {}),
    ("apm_config", {"kind": "db_path"}),
    ("apm_config", {"kind": "event_rules"}),
    ("apm_config", {"kind": "loaded_classes"}),
])
async def test_batch_path_never_leaks_credentials(tool, args, tmp_path, caplog):
    fake = Fleet(2, shared_hosts=True)
    mcp, tools, jobs = _stack(fake, tmp_path, inline_rows=1)  # 결과 파일(스풀)까지 쓰게
    caplog.set_level(logging.DEBUG)
    items = [{"hostname": "h1-0"}, {"hostname": "h2-1", "source_id": "common"},
             {"hostname": "nohost"}]
    out = await _call(mcp, tool, targets=items, owner="user:alice", **args)
    dumped = json.dumps(out, ensure_ascii=False)
    secrets = [W7_SECRET, "tok-bank-SECRET", "tok-common-SECRET"]
    assert out.get("batch") and out["batch"][2]["status"] == "error", dumped[:800]
    # 비밀이 실린 원천 응답을 실제로 거쳤다
    assert all(b["row_count"] > 0 for b in out["batch"][:2])
    for s in secrets:
        assert s not in dumped, (s, "반환")
        assert all(s not in r.getMessage() for r in caplog.records), (s, "로그")
    spool = tmp_path / "spool"
    for f in (p for p in spool.rglob("*") if p.is_file()):
        text = f.read_text(encoding="utf-8", errors="replace")
        for s in secrets:
            assert s not in text, (s, f.name)
    if out.get("job"):
        st = await _call(mcp, "apm_job_status", job_id=out["job"]["job_id"], owner="user:alice")
        assert all(s not in json.dumps(st, ensure_ascii=False) for s in secrets)
    audits = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]
    assert any("targets(3)" in a for a in audits) and not any("h2-1" in a for a in audits), audits
    # 작업 기록(job.json) — 기록의 대상 칸은 요약, 결과 메타의 배치 항목에는 대상 원문
    # (처분 판단 근거)
    records = [json.loads(f.read_text(encoding="utf-8")) for f in spool.rglob("job.json")]
    assert len(records) == 1, records
    rec = records[0]
    assert rec["target"].endswith("targets(3)"), rec["target"]
    meta_targets = [b["target"] for b in (rec.get("result_meta") or {}).get("batch") or []]
    assert meta_targets == items, meta_targets
    await jobs.aclose()


# ── 8. 알람 불변(독립 재현) ──────────────────────────────────────────────────────

class _BrokenBuffer(EventBuffer):
    def record(self, *a, **kw):
        raise RuntimeError("buffer down")


@pytest.mark.asyncio
async def test_alarm_publication_is_byte_identical_with_or_without_buffer():
    def fake() -> Fleet:
        f = Fleet(3, hosts=("bank.test", "common.test"))
        for host in f.hosts:
            for d in f.domains:
                for j in range(9):
                    lvl = ("FATAL", "WARNING", "NORMAL")[j % 3]
                    f.add_event(host, event(d, d * 100 + j % 2, T0 + j * 7_000, level=lvl,
                                            txid=str(j % 4)))
                f.add_event(host, event(d, d * 100, T0 + 7_000, level="FATAL", txid="1"))  # 중복
        return f

    runs = {}
    for label, make in (("none", lambda c: None), ("buffer", lambda c: EventBuffer(clock=c)),
                        ("evicting", lambda c: EventBuffer(clock=c, max_events=2)),
                        ("broken", lambda c: _BrokenBuffer(clock=c))):
        clock = Clock(NOW_S)
        redis = FakeRedis()
        poller = _poller(fake(), clock, redis, make(clock))
        await _run_polls(poller, clock, NOW_S + 150)
        runs[label] = (json.dumps(redis.stream, ensure_ascii=False, sort_keys=True),
                       json.dumps(redis.kv, sort_keys=True), poller.published_total,
                       poller.duplicates_total)
    assert runs["none"][2] > 0
    for label in ("buffer", "evicting", "broken"):
        assert runs[label] == runs["none"], label


@pytest.mark.asyncio
async def test_buffer_path_failed_domain_is_unknown_not_zero_with_two_sources(tmp_path):
    """두 소스 · 폴러가 common 도메인 2를 못 받았고(확정 없음) 조회 때도 API가 실패 — 그 도메인은
    0건이 아니라 실패로 센다. 나머지는 버퍼(API 0회)."""
    fake = Fleet(2, hosts=("bank.test", "common.test"))
    for host in fake.hosts:
        for d in fake.domains:
            fake.add_event(host, event(d, d * 100, T0 + 100_000 + d, level="FATAL", txid="1"))
    fake.event_fail.add(("common.test", 2))
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock)
    poller = _poller(fake, clock, FakeRedis(), buffer)
    await _run_polls(poller, clock, NOW_S + 600)
    mcp, _tools, jobs = _stack(fake, tmp_path, buffer=buffer, clock=clock)
    fake.calls.clear()
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ref = datetime.fromtimestamp(clock.t - 180, ZoneInfo("Asia/Seoul")).isoformat()
    out = await _call(mcp, "apm_fleet", mode="events", reference_time=ref, lookback_minutes=5,
                      full=True)
    cov = out["coverage"]
    assert cov["failed"] == 1 and cov["from_buffer"] == 3, cov
    assert out["partial"] is True and out["summary"]["domains_failed"] == 1
    assert any("0건이 아니라 확인하지 못했다" in x for x in out["limits"]), out["limits"]
    assert [(h, q["domain_id"]) for h, q in fake.event_calls()] == [("common.test", "2")]
    await jobs.aclose()
