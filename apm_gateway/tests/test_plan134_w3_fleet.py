"""plans/134 W3 — 전 대상 순위·이벤트 `apm_fleet`(계약 A-6 · A-7 조회 쪽).

- ranking: 도메인 350개 × 소스 2개 합성 · 전부 모은 뒤 정렬(구현과 다른 독립 오라클 — 합성
  원자료에서 `heapq`로 상위 n · desc/asc · 값 없음 제외) · 도메인 1곳 실패 → `provisional` +
  `[한계]` · `full` · 호출 계획 = 도메인 수 → 승격 시 예상 시간 = 700 / 5초.
- events: 버퍼가 창을 덮음 = API 0회 · 일부만 덮음 = 나머지 API 보충 · 재기동(빈 버퍼) = 전부
  API · 폴러 실패 구간 = API · 실패 도메인은 0건이 아니라 `domains_failed` · 버퍼 행 == API 경로
  행(바이트) · 중복 제거 · 레벨·오류 유형 거르기는 `apm_events`와 같은 결과.

가상 시계 · 합성 Open API(`fleet_fake`) · 가짜 Redis — 실시간 대기·외부 네트워크 0.
"""

from __future__ import annotations

import asyncio
import heapq
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from apm_gateway.application.event_buffer import EventBuffer
from apm_gateway.application.fleet_tools import RANKING_METRICS, FleetTools
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.poller import EventPoller
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.errors import INVALID_ARGUMENT, SOURCE_UNAVAILABLE
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import NOW_MS, NOW_S
from fleet_fake import Clock, FakeFleet, event
from test_poller import FakeRedis

from apm_gateway.config import JobConfig, load_config

T0 = NOW_MS


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, ZoneInfo("Asia/Seoul")).isoformat()


def _tools(fake: FakeFleet, tmp_path, clock=lambda: NOW_S, **extra: str):
    cfg = load_config(fake.env(APM_SPOOL_DIR=str(tmp_path / "spool"), **extra))
    sources = build_source_set(cfg, transport=httpx.MockTransport(fake.handler))
    return ApmTools(sources, cfg, clock=clock), cfg


def _server(tools: ApmTools, tmp_path, *, rate: float = 0.0, buffer=None, **job_cfg):
    jcfg = JobConfig(spool_dir=tmp_path / "spool", **job_cfg)
    jobs = JobManager(
        Spool(jcfg.spool_dir),
        jcfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        rate_per_sec=rate,
        on_finish=audit_job_finished,
    )
    return create_server(tools, jobs=jobs, event_buffer=buffer), jobs


async def _warm(tools: ApmTools) -> None:
    for src in tools.sources:
        await src.resolver.inventory()


def _oracle(fake: FakeFleet, n: int | None, *, desc: bool, failed=frozenset()):
    """합성 원자료에서 바로 고른 상위 n(값이 모두 달라 동점이 없다 — 구현의 정렬 키와 무관)."""
    pool = [
        (v, sid, d, iid)
        for v, sid, d, iid in fake.truth
        if v is not None and (f"{sid}.test", d) not in failed
    ]
    size = len(pool) if n is None else n
    pick = heapq.nlargest if desc else heapq.nsmallest
    return [(sid, d, iid, v) for v, sid, d, iid in pick(size, pool, key=lambda x: x[0])]


# ── ranking ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def big() -> FakeFleet:
    return FakeFleet(350)


@pytest.mark.asyncio
async def test_ranking_collects_every_domain_before_sorting(big, tmp_path):
    big.realtime_fail = set()
    tools, _ = _tools(big, tmp_path)
    await _warm(tools)
    big.calls.clear()
    fleet = FleetTools(tools)
    desc = await fleet.ranking(n=10)
    asc = await fleet.ranking(order="asc", n=7)
    got = [(r["source_id"], r["domain_id"], r["instance_id"], r["value"]) for r in desc["rows"]]
    assert got == _oracle(big, 10, desc=True)
    got = [(r["source_id"], r["domain_id"], r["instance_id"], r["value"]) for r in asc["rows"]]
    assert got == _oracle(big, 7, desc=False)
    assert [r["rank"] for r in desc["rows"]] == list(range(1, 11))
    unranked = sum(1 for v, *_ in big.truth if v is None)
    assert unranked > 0
    assert desc["summary"] == {
        "metric": "response_time_avg_ms",
        "order": "desc",
        "domains_total": 700,
        "domains_ok": 700,
        "domains_failed": 0,
        "instances_total": 1400,
        "instances_ranked": 1400 - unranked,
        "instances_unranked": unranked,
    }
    assert desc["provisional"] is False and "partial" not in desc and desc["mode"] == "ranking"
    top = desc["rows"][0]
    sid, d, iid, _ = _oracle(big, 1, desc=True)[0]
    assert top["hostname"] == f"{sid}-h{d}-{iid % 100}"  # 역정합(인스턴스 hostName)
    assert top["domain_name"] == f"{sid}-dom-{d}" and top["metric"] == "response_time_avg_ms"
    assert set(RANKING_METRICS) <= set(top) and "instance_oid" in top
    assert top["instance_description"] == "담당 <email>"
    # 실시간 1호출/도메인(인스턴스 지정 없음)
    realtime = [q for _h, p, q in big.calls if p == "/api/realtime/instance"]
    assert len(realtime) == 1400 and all("instance_id" not in q for q in realtime)


@pytest.mark.asyncio
async def test_ranking_failed_domain_is_provisional(big, tmp_path):
    big.realtime_fail = {("bank.test", 17)}
    try:
        tools, _ = _tools(big, tmp_path)
        out = await FleetTools(tools).ranking(n=10)
        full = await FleetTools(tools).ranking(full=True)
    finally:
        big.realtime_fail = set()
    assert out["partial"] is True and out["provisional"] is True
    assert (
        "[한계] 잠정 순위 — 조회 실패 도메인 1곳(소스 bank · 도메인 17)은 순위에 없습니다"
        in out["limits"]
    )
    assert out["summary"]["domains_failed"] == 1 and out["summary"]["domains_ok"] == 699
    failed = frozenset({("bank.test", 17)})
    got = [(r["source_id"], r["domain_id"], r["instance_id"], r["value"]) for r in out["rows"]]
    assert got == _oracle(big, 10, desc=True, failed=failed)
    got = [(r["source_id"], r["domain_id"], r["instance_id"], r["value"]) for r in full["rows"]]
    assert got == _oracle(big, None, desc=True, failed=failed)
    assert not any(r["source_id"] == "bank" and r["domain_id"] == 17 for r in full["rows"])


@pytest.mark.asyncio
async def test_ranking_other_metric_domain_filter_and_unknown_domain(tmp_path):
    fake = FakeFleet(4)
    tools, _ = _tools(fake, tmp_path)
    fleet = FleetTools(tools)
    out = await fleet.ranking(metric="visit_day", n=3, domain_id=2)
    assert [(r["source_id"], r["domain_id"], r["value"]) for r in out["rows"]] == [
        ("bank", 2, 21),
        ("common", 2, 21),
        ("bank", 2, 20),
    ]  # 같은 값은 소스 선언 순 → 도메인 → 인스턴스
    assert out["summary"]["domains_total"] == 2
    assert any("visit_day" in line for line in out["limits"])  # 단위 미확인 고지
    none = await fleet.ranking(domain_id=99)
    assert none["rows"] == [] and none["summary"]["domains_total"] == 0
    assert any("도메인 ID 99" in t for t in none["_unresolved"])
    assert any("[한계] 도메인 99은" in line for line in none["limits"])
    with pytest.raises(Exception) as exc:
        await fleet.ranking(metric="cpu")
    assert getattr(exc.value, "code", None) == INVALID_ARGUMENT
    fake.realtime_fail = {(h, d) for h in fake.hosts for d in fake.domains}
    with pytest.raises(Exception) as exc:
        await fleet.ranking()
    assert getattr(exc.value, "code", None) == SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_ranking_promoted_estimate_is_domains_over_rate(big, tmp_path):
    big.gate, big.gate_paths = asyncio.Event(), ("/api/realtime/instance",)
    try:
        tools, _ = _tools(big, tmp_path)
        await _warm(tools)
        mcp, jobs = _server(tools, tmp_path, rate=5.0)
        try:
            handle = _json(
                await mcp.call_tool(
                    "apm_fleet", {"mode": "ranking", "wait_seconds": 0.05, "owner": "o"}
                )
            )
            assert handle["job"]["estimate"] == {"api_calls": 700, "seconds": 140.0}
            assert handle["job"]["progress"]["total"] == 700
            big.gate.set()
            for _ in range(1000):
                status = _json(
                    await mcp.call_tool(
                        "apm_job_status", {"job_id": handle["job"]["job_id"], "owner": "o"}
                    )
                )
                if status["job"]["state"] not in ("queued", "running"):
                    break
                await asyncio.sleep(0.01)
        finally:
            big.gate.set()
            await jobs.aclose()
    finally:
        big.gate, big.gate_paths = None, ()
    assert status["job"]["state"] == "completed"
    assert status["job"]["progress"]["done"] == 700
    assert status["result_meta"]["summary"]["domains_total"] == 700
    assert [r["rank"] for r in status["rows"]] == list(range(1, 11))


@pytest.mark.asyncio
async def test_fleet_mcp_schema_exposes_metric_enum(tmp_path):
    fake = FakeFleet(1)
    tools, _ = _tools(fake, tmp_path)
    mcp, jobs = _server(tools, tmp_path)
    await jobs.aclose()
    schema = {t.name: t.inputSchema for t in await mcp.list_tools()}["apm_fleet"]
    props = schema["properties"]
    assert props["metric"]["enum"] == list(RANKING_METRICS) and len(RANKING_METRICS) == 30
    assert props["metric"]["default"] == "response_time_avg_ms"
    assert props["mode"]["enum"] == ["ranking", "events"] and schema["required"] == ["mode"]
    assert props["order"]["enum"] == ["desc", "asc"]
    assert {
        "n",
        "full",
        "level",
        "level_mode",
        "error_type",
        "reference_time",
        "lookback_minutes",
        "domain_id",
        "source_ids",
        "owner",
        "wait_seconds",
        "investigation_id",
        "thread_id",
    } <= set(props)
    assert "hostname" not in props and "targets" not in props
    # 서비스로 좁힘(plans/134 W3 병합 뒤 연결 — 서비스 도구와 같은 인자 주석)
    assert [p.get("type") for p in props["service"]["anyOf"]] == ["string", "array", "null"]
    assert "service" not in schema.get("required", [])


@pytest.mark.asyncio
async def test_fleet_mcp_ignored_args_are_noted(tmp_path):
    fake = FakeFleet(1, hosts=("apm.test",))
    tools, _ = _tools(fake, tmp_path)
    mcp, jobs = _server(tools, tmp_path)
    try:
        ranking = _json(
            await mcp.call_tool("apm_fleet", {"mode": "ranking", "level": "fatal", "n": 1})
        )
        events = _json(await mcp.call_tool("apm_fleet", {"mode": "events", "order": "asc"}))
    finally:
        await jobs.aclose()
    assert "[한계] mode ranking은 인자 level를 쓰지 않는다 — 빼고 조회했다" in ranking["limits"]
    assert "[한계] mode events는 인자 order를 쓰지 않는다 — 빼고 조회했다" in events["limits"]


# ── events (조회용 버퍼) ────────────────────────────────────────

HOST = "apm.test"


def _events_fake() -> FakeFleet:
    """소스 1개 · 도메인 3개 · 도메인마다 이벤트 — 창 밖·확정 구간·꼬리(확정 전) 시각을 섞는다."""
    fake = FakeFleet(3, hosts=(HOST,))
    for d in fake.domains:
        for j, offset in enumerate((-120, 10, 70, 150, 240, 299, 330)):
            level = ("FATAL", "WARNING", "NORMAL")[j % 3]
            etype = "ERROR_OUTOFMEMORY" if j % 2 else "ERROR_SERVICE_QUEUING"
            fake.add_event(
                HOST,
                event(d, d * 100 + j % 2, T0 + offset * 1000, level=level, error_type=etype,
                      txid=str(j) if j % 3 else ""),
            )
    return fake


async def _polled(fake: FakeFleet, tmp_path, polls: int = 13, *, fail_from=None):
    """가상 시계로 30초마다 폴링(`polls`회) — 확정 구간 [T0−30초, T0+30×(polls−1)−60초]."""
    clock = Clock(NOW_S)
    tools, cfg = _tools(fake, tmp_path, clock=clock, APM_EVENT_POLLER_ENABLED="true")
    buffer = EventBuffer(clock=clock)
    poller = EventPoller(tools.sources, cfg, FakeRedis(), clock=clock, event_buffer=buffer)
    for k in range(polls):
        clock.t = NOW_S + 30 * k
        if fail_from is not None and k == fail_from[0]:
            fake.event_fail = set(fail_from[1])
        await poller.poll_once()
    fake.calls.clear()
    return tools, buffer, clock, poller


def _key(r: dict) -> tuple:
    return (r["domain_id"], r["instance_id"], r["time_ms"], r["event_type"])


@pytest.mark.asyncio
async def test_events_window_covered_by_buffer_needs_no_api_and_matches_api_bytes(tmp_path):
    fake = _events_fake()
    tools, buffer, _clock, _ = await _polled(fake, tmp_path)
    args = {"reference_time": _iso(T0 + 300_000), "lookback_minutes": 5}
    out = await FleetTools(tools, buffer).events(**args)
    assert fake.event_calls() == []  # API 0회
    assert out["coverage"] == {
        "domains_total": 3,
        "from_buffer": 3,
        "from_api": 0,
        "mixed": 0,
        "failed": 0,
    }
    api = await FleetTools(tools, None).events(**args)  # 버퍼 없음 = 전부 API
    assert len(fake.event_calls()) == 3 and api["coverage"]["from_api"] == 3
    assert json.dumps(out["rows"], ensure_ascii=False) == json.dumps(
        api["rows"], ensure_ascii=False
    )
    # 창 [T0, T0+300초] 안 이벤트 5건 × 도메인 3 · 시각 내림차순
    assert out["row_count"] == 15 and out["summary"]["events_total"] == 15
    assert [r["time_ms"] for r in out["rows"]] == sorted(
        (r["time_ms"] for r in out["rows"]), reverse=True
    )
    row = out["rows"][0]
    assert row["domain_name"] == f"apm-dom-{row['domain_id']}"  # 응답에 없으면 인벤토리
    assert row["hostname"] == f"apm-h{row['domain_id']}-{row['instance_id'] % 100}"
    assert row["source_id"] == "default"
    assert "<email>" in row["message"] and "kim@" not in json.dumps(out["rows"])


@pytest.mark.asyncio
async def test_events_partially_covered_window_is_filled_from_api(tmp_path):
    fake = _events_fake()
    tools, buffer, clock, _ = await _polled(fake, tmp_path)
    late = event(2, 200, T0 + 350_000, level="FATAL", txid="late")  # 마지막 폴링 뒤 들어온 이벤트
    fake.add_event(HOST, late)
    out = await FleetTools(tools, buffer).events(lookback_minutes=5)  # 지금(T0+360초) 기준
    assert out["coverage"]["mixed"] == 3 and out["coverage"]["from_api"] == 0
    calls = fake.event_calls()
    # 확정 구간 끝(T0+300초) 다음부터 창 끝까지만 묻는다
    assert [(int(q["start_time"]), int(q["end_time"])) for _, q in calls] == [
        (T0 + 300_001, T0 + 360_000)
    ] * 3
    times = {(r["domain_id"], r["time_ms"]) for r in out["rows"]}
    assert (2, T0 + 350_000) in times and (1, T0 + 330_000) in times and (1, T0 + 70_000) in times
    assert (1, T0 + 10_000) not in times  # 창 [T0+60초, T0+360초] 밖
    assert len(out["rows"]) == len({_key(r) for r in out["rows"]})  # 중복 없음
    fresh = await FleetTools(tools, None).events(lookback_minutes=5)
    assert json.dumps(out["rows"], ensure_ascii=False) == json.dumps(
        fresh["rows"], ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_events_after_restart_empty_buffer_asks_api(tmp_path):
    fake = _events_fake()
    tools, _buffer, clock, _ = await _polled(fake, tmp_path)
    restarted = EventBuffer(clock=clock)  # 재기동 = 빈 버퍼(확정 0)
    out = await FleetTools(tools, restarted).events(
        reference_time=_iso(T0 + 300_000), lookback_minutes=5
    )
    assert out["coverage"]["from_api"] == 3 and len(fake.event_calls()) == 3
    assert out["row_count"] == 15


@pytest.mark.asyncio
async def test_events_poller_failure_gap_goes_to_api_and_failure_is_not_zero(tmp_path):
    fake = _events_fake()
    tools, buffer, _clock, poller = await _polled(
        fake, tmp_path, fail_from=(7, {(HOST, 2)})
    )  # 도메인 2는 7번째 폴링부터 수집 불가(백오프)
    assert poller.status()["domains"]["default:2"]["state"] == "unavailable"
    args = {"reference_time": _iso(T0 + 300_000), "lookback_minutes": 5}
    failed = await FleetTools(tools, buffer).events(**args)  # 지금도 API 실패
    assert failed["coverage"] == {
        "domains_total": 3,
        "from_buffer": 2,
        "from_api": 0,
        "mixed": 0,
        "failed": 1,
    }
    assert failed["partial"] is True and failed["summary"]["domains_failed"] == 1
    assert "[한계] 이벤트 조회 실패 도메인 1곳(도메인 2) — 0건이 아니라 확인하지 못했다" in (
        failed["limits"]
    )
    assert {r["domain_id"] for r in failed["rows"]} == {1, 3}
    fake.event_fail = set()
    fake.calls.clear()
    healed = await FleetTools(tools, buffer).events(**args)
    assert healed["coverage"]["mixed"] == 1 and healed["coverage"]["from_buffer"] == 2
    # 도메인 2는 마지막 확정(6번째 폴링 → T0+120초) 뒤부터만 API로 묻는다
    assert [(int(q["start_time"]), int(q["end_time"])) for _, q in fake.event_calls()] == [
        (T0 + 120_001, T0 + 300_000)
    ]
    assert sum(1 for r in healed["rows"] if r["domain_id"] == 2) == 5
    assert "partial" not in healed


@pytest.mark.asyncio
async def test_events_all_domains_failed_is_error(tmp_path):
    fake = _events_fake()
    tools, _ = _tools(fake, tmp_path)
    fake.event_fail = {(HOST, d) for d in fake.domains}
    with pytest.raises(Exception) as exc:
        await FleetTools(tools, None).events()
    assert getattr(exc.value, "code", None) == SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_events_level_and_error_type_filters_match_apm_events(tmp_path):
    fake = _events_fake()
    tools, buffer, _clock, _ = await _polled(fake, tmp_path)
    fleet = FleetTools(tools, buffer)
    window = {"reference_time": _iso(T0 + 300_000), "lookback_minutes": 5}
    for flt in (
        {"level": "warning"},
        {"level": "normal", "level_mode": "exact"},
        {"error_type": "outofmemory"},
        {"level": "warning", "error_type": "SERVICE_QUEUING"},
        {"level": "fatal", "level_mode": "exact", "error_type": "ERROR_OUTOFMEMORY"},
    ):
        out = await fleet.events(**window, **flt)
        assert fake.event_calls() == []  # 버퍼만으로 거른다(레벨을 API에 넘기지 않는다)
        assert out["rows"], flt
        compared = 0
        for d in fake.domains:
            for k in (0, 1):
                single = await tools.apm_events(hostname=f"apm-h{d}-{k}", **window, **flt)
                # 같은 행 + 도메인 이름(응답에 없으면 인벤토리로 채운다)·역정합 hostname
                mine = [
                    {key: v for key, v in r.items() if key != "hostname"}
                    for r in out["rows"]
                    if (r["domain_id"], r["instance_id"]) == (d, d * 100 + k)
                ]
                assert all(r["domain_name"] == f"apm-dom-{d}" for r in mine)
                expected = [{**r, "domain_name": f"apm-dom-{d}"} for r in single["rows"]]
                assert mine == expected, (flt, d, k)
                compared += len(mine)
        assert compared == len(out["rows"])
        fake.calls.clear()
    with pytest.raises(Exception) as exc:
        await fleet.events(level="bogus")
    assert getattr(exc.value, "code", None) == INVALID_ARGUMENT
    with pytest.raises(Exception) as exc:
        await fleet.events(level_mode="exact")
    assert getattr(exc.value, "code", None) == INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_events_duplicate_records_and_two_sources_same_values(tmp_path):
    fake = FakeFleet(1, hosts=("bank.test", "common.test"))
    same = event(1, 100, T0 - 60_000, txid="9")
    for host in fake.hosts:
        fake.add_event(host, same)
        fake.add_event(host, dict(same))  # 원천이 같은 이벤트를 두 번 돌려준다
    tools, _ = _tools(fake, tmp_path)
    out = await FleetTools(tools, None).events(n=10)
    # 소스가 다르면 다른 이벤트 · 같은 출처 응답 안의 행은 합치지 않는다(gw-fix-34 G-4 —
    # apm_events와 같은 건수 · 겹침 제거는 버퍼↔API 사이에만)
    assert [r["source_id"] for r in out["rows"]] == ["bank", "bank", "common", "common"]


@pytest.mark.asyncio
async def test_events_n_and_full_select_latest(tmp_path):
    fake = _events_fake()
    tools, buffer, _clock, _ = await _polled(fake, tmp_path)
    window = {"reference_time": _iso(T0 + 300_000), "lookback_minutes": 5}
    top = await FleetTools(tools, buffer).events(**window, n=4)
    every = await FleetTools(tools, buffer).events(**window, full=True)
    assert top["rows"] == every["rows"][:4] and top["summary"]["events_total"] == 15
    assert [r["time_ms"] for r in top["rows"]] == [T0 + 299_000] * 3 + [T0 + 240_000]


@pytest.mark.asyncio
async def test_fleet_events_through_mcp_uses_server_buffer(tmp_path):
    fake = _events_fake()
    tools, buffer, _clock, _ = await _polled(fake, tmp_path)
    mcp, jobs = _server(tools, tmp_path, buffer=buffer)
    try:
        out = _json(
            await mcp.call_tool(
                "apm_fleet",
                {"mode": "events", "reference_time": _iso(T0 + 300_000), "lookback_minutes": 5},
            )
        )
    finally:
        await jobs.aclose()
    assert out["coverage"]["from_buffer"] == 3 and fake.event_calls() == []
    assert out["total_row_count"] == 15 and all("instance_oid" not in r for r in out["rows"])
