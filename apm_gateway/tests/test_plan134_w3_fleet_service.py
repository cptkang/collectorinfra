"""plans/134 W3 — `apm_fleet(service=…)`(계약 A-6 · 병합 뒤 연결 · D-290 ⑥).

- 서비스 이름으로 범위를 좁힌다 — 소스 2개에 같은 도메인 id·이름 · ranking·events 모두.
- 이름을 줬는데 하나도 못 찾으면 실시간·이벤트 호출 0 · 행 0(전 도메인으로 넓히지 않는다) +
  미해결 고지·`suggestions`(서비스 도구 `apm_service_status`와 같은 문구·모양).
- 일부만 찾으면 찾은 도메인만 + 못 찾은 이름 고지 · `domain_id`와 AND · 잠정 순위는 좁힌 범위 기준 ·
  조회용 버퍼 경로도 좁힌 도메인만 · 감사 대상에 서비스 요약.

가상 시계 · 합성 Open API(`fleet_fake`) — 외부 네트워크 0.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from apm_gateway.application.event_buffer import EventBuffer
from apm_gateway.application.fleet_tools import FleetTools
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.poller import EventPoller
from apm_gateway.application.scope_tools import ScopeTools
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.errors import INVALID_ARGUMENT
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import NOW_MS, NOW_S
from fleet_fake import Clock, FakeFleet, event
from test_plan134_w3_fleet import _iso, _oracle
from test_poller import FakeRedis

from apm_gateway.config import JobConfig, load_config

T0 = NOW_MS
NAMES = {1: "order-svc", 2: "payment", 3: "payment-batch", 4: "loan"}
DATA_PATHS = ("/api/realtime/instance", "/api/dbsearch/event")


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _fake(**kw) -> FakeFleet:
    return FakeFleet(4, domain_names=NAMES, **kw)


def _tools(fake: FakeFleet, tmp_path, clock=lambda: NOW_S, **extra: str) -> ApmTools:
    cfg = load_config(fake.env(APM_SPOOL_DIR=str(tmp_path / "spool"), **extra))
    sources = build_source_set(cfg, transport=httpx.MockTransport(fake.handler))
    return ApmTools(sources, cfg, clock=clock)


def _data_calls(fake: FakeFleet) -> list[tuple[str, int]]:
    return [(h, int(q["domain_id"])) for h, p, q in fake.calls if p in DATA_PATHS]


async def _warm(tools: ApmTools, fake: FakeFleet) -> None:
    for src in tools.sources:
        await src.resolver.inventory()
    fake.calls.clear()


# ── ranking ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ranking_service_narrows_to_that_domain_in_both_sources(tmp_path):
    fake = _fake()
    tools = _tools(fake, tmp_path)
    await _warm(tools, fake)
    out = await FleetTools(tools).ranking(service="payment", full=True)
    # 같은 도메인 id(2)·같은 이름이 두 소스에 있다 — 둘 다 그 도메인만 묻는다(가장 앞 단계 exact만 ·
    # payment-batch는 prefix 단계라 빠진다)
    assert sorted(_data_calls(fake)) == [("bank.test", 2), ("common.test", 2)]
    assert {(r["source_id"], r["domain_id"]) for r in out["rows"]} == {("bank", 2), ("common", 2)}
    assert all(r["domain_name"] == "payment" for r in out["rows"])
    expected = [x for x in _oracle(fake, None, desc=True) if x[1] == 2]
    got = [(r["source_id"], r["domain_id"], r["instance_id"], r["value"]) for r in out["rows"]]
    assert got == expected
    assert out["summary"]["domains_total"] == 2 and out["provisional"] is False
    assert "_unresolved" not in out and "suggestions" not in out
    assert [s["status"] for s in out["sources"]] == ["ok", "ok"]


@pytest.mark.asyncio
async def test_ranking_all_names_unresolved_makes_no_data_calls(tmp_path):
    fake = _fake()
    tools = _tools(fake, tmp_path)
    await _warm(tools, fake)
    out = await FleetTools(tools).ranking(service=["paymnt", "zzz-none"])
    assert _data_calls(fake) == [] and fake.calls == []  # 인벤토리는 캐시 · 데이터 API 0회
    assert out["rows"] == [] and out["summary"]["domains_total"] == 0
    assert out["provisional"] is False and "partial" not in out
    assert out["_unresolved"] == [
        "서비스 'paymnt'에 해당하는 제니퍼 도메인을 찾지 못했습니다",
        "서비스 'zzz-none'에 해당하는 제니퍼 도메인을 찾지 못했습니다",
    ]
    assert [(s["query"], s["source_id"], s["domain_id"]) for s in out["suggestions"]] == [
        ("paymnt", "bank", 2),
        ("paymnt", "common", 2),
    ]
    # 서비스 도구와 같은 문구·모양(같은 범위 해석 코드)
    same = await ScopeTools(tools).apm_service_status(service=["paymnt", "zzz-none"])
    assert same["rows"] == [] and same["_unresolved"] == out["_unresolved"]
    assert same["suggestions"] == out["suggestions"] and fake.calls == []


@pytest.mark.asyncio
async def test_ranking_partially_resolved_keeps_found_domains_and_notes_missing(tmp_path):
    fake = _fake()
    tools = _tools(fake, tmp_path)
    await _warm(tools, fake)
    out = await FleetTools(tools).ranking(service=["loan", "nosuch-svc"], full=True)
    assert sorted(_data_calls(fake)) == [("bank.test", 4), ("common.test", 4)]
    assert {r["domain_id"] for r in out["rows"]} == {4}
    assert out["_unresolved"] == ["서비스 'nosuch-svc'에 해당하는 제니퍼 도메인을 찾지 못했습니다"]
    assert "suggestions" in out and out["provisional"] is False
    # 한 이름에 여럿이 맞으면 전부 조회하고 [한계]에 맞은 도메인을 적는다(되묻지 않는다)
    fake.calls.clear()
    multi = await FleetTools(tools).ranking(service="pay")
    assert sorted(_data_calls(fake)) == [
        ("bank.test", 2),
        ("bank.test", 3),
        ("common.test", 2),
        ("common.test", 3),
    ]
    assert any("서비스 'pay' — 이름이 맞은 도메인 4곳" in line for line in multi["limits"])


@pytest.mark.asyncio
async def test_ranking_service_and_domain_id_are_and(tmp_path):
    fake = _fake()
    tools = _tools(fake, tmp_path)
    await _warm(tools, fake)
    both = await FleetTools(tools).ranking(service="pay", domain_id=3, full=True)
    assert sorted(_data_calls(fake)) == [("bank.test", 3), ("common.test", 3)]
    assert {r["domain_id"] for r in both["rows"]} == {3}
    fake.calls.clear()
    none = await FleetTools(tools).ranking(service="payment", domain_id=4)
    assert _data_calls(fake) == [] and none["rows"] == []  # AND가 0곳 — 넓히지 않는다
    assert none["summary"]["domains_total"] == 0
    with pytest.raises(Exception) as exc:
        await FleetTools(tools).ranking(service=[])
    assert getattr(exc.value, "code", None) == INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_ranking_provisional_is_judged_on_the_narrowed_scope(tmp_path):
    fake = _fake()
    fake.realtime_fail = {("bank.test", 1)}  # 좁힌 범위 밖 실패 — 이번 순위와 무관
    tools = _tools(fake, tmp_path)
    outside = await FleetTools(tools).ranking(service="payment")
    assert outside["provisional"] is False and "partial" not in outside
    fake.realtime_fail = {("bank.test", 2)}  # 좁힌 범위 안 실패
    inside = await FleetTools(tools).ranking(service="payment", full=True)
    assert inside["provisional"] is True and inside["partial"] is True
    assert inside["summary"]["domains_failed"] == 1 and inside["summary"]["domains_total"] == 2
    assert (
        "[한계] 잠정 순위 — 조회 실패 도메인 1곳(소스 bank · 도메인 2)은 순위에 없습니다"
        in inside["limits"]
    )
    assert {r["source_id"] for r in inside["rows"]} == {"common"}


# ── events ──────────────────────────────────────────────────────


async def _polled(tmp_path):
    fake = FakeFleet(4, hosts=("apm.test",), domain_names=NAMES)
    for d in fake.domains:
        for offset in (10, 150, 299):
            fake.add_event("apm.test", event(d, d * 100, T0 + offset * 1000, txid=str(offset)))
    clock = Clock(NOW_S)
    tools = _tools(fake, tmp_path, clock=clock, APM_EVENT_POLLER_ENABLED="true")
    buffer = EventBuffer(clock=clock)
    poller = EventPoller(tools.sources, tools.cfg, FakeRedis(), clock=clock, event_buffer=buffer)
    for k in range(13):
        clock.t = NOW_S + 30 * k
        await poller.poll_once()
    fake.calls.clear()
    return fake, tools, buffer


@pytest.mark.asyncio
async def test_events_buffer_path_is_narrowed_by_service(tmp_path):
    fake, tools, buffer = await _polled(tmp_path)
    window = {"reference_time": _iso(T0 + 300_000), "lookback_minutes": 5}
    out = await FleetTools(tools, buffer).events(service="payment", **window)
    assert fake.calls == []  # 버퍼만 · 좁힌 도메인만
    assert out["coverage"] == {
        "domains_total": 1,
        "from_buffer": 1,
        "from_api": 0,
        "mixed": 0,
        "failed": 0,
    }
    assert {r["domain_id"] for r in out["rows"]} == {2} and out["row_count"] == 3
    api = await FleetTools(tools, None).events(service="payment", **window)  # API 경로도 같은 범위
    assert [(int(q["domain_id"])) for _h, p, q in fake.calls if p in DATA_PATHS] == [2]
    assert json.dumps(api["rows"], ensure_ascii=False) == json.dumps(
        out["rows"], ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_events_unresolved_service_makes_no_calls_even_without_buffer(tmp_path):
    fake, tools, buffer = await _polled(tmp_path)
    for buf in (buffer, None):
        out = await FleetTools(tools, buf).events(service="no-such-service")
        assert fake.calls == [] and out["rows"] == []
        assert out["coverage"]["domains_total"] == 0 and out["summary"]["domains_total"] == 0
        assert out["_unresolved"] == [
            "서비스 'no-such-service'에 해당하는 제니퍼 도메인을 찾지 못했습니다"
        ]
        assert "suggestions" in out and "partial" not in out
    part = await FleetTools(tools, None).events(service=["order-svc", "no-such-service"])
    assert [int(q["domain_id"]) for _h, p, q in fake.calls if p in DATA_PATHS] == [1]
    assert {r["domain_id"] for r in part["rows"]} == {1} and len(part["_unresolved"]) == 1


# ── MCP ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fleet_service_through_mcp_with_audit_and_disclosure(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    fake = _fake()
    tools = _tools(fake, tmp_path)
    jcfg = JobConfig(spool_dir=tmp_path / "spool")
    jobs = JobManager(
        Spool(jcfg.spool_dir),
        jcfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        on_finish=audit_job_finished,
    )
    mcp = create_server(tools, jobs=jobs)
    try:
        ok = _json(
            await mcp.call_tool("apm_fleet", {"mode": "ranking", "service": "payment", "n": 3})
        )
        miss = _json(
            await mcp.call_tool("apm_fleet", {"mode": "events", "service": ["paymnt"]})
        )
    finally:
        await jobs.aclose()
    assert ok["row_count"] == 3 and {r["domain_id"] for r in ok["rows"]} == {2}
    assert miss["rows"] == [] and miss["suggestions"]
    assert {
        "kind": "apm_unresolved_condition",
        "text": "서비스 'paymnt'에 해당하는 제니퍼 도메인을 찾지 못했습니다",
    } in miss["disclosures"]
    lines = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]
    assert any("target=ranking:response_time_avg_ms:service:payment" in x for x in lines)
    assert any("target=events:service:paymnt" in x for x in lines)
