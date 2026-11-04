"""plans/210 W9 — 노드 주소 조회 시간 예산 · 소스 간 병렬 · 서버명 경로 표지 (§6.3 · D-355).

- A: 대상 지정 호출(`apm_node_addresses`의 domain_ids·instance_ids · `apm_instance_map`의 대상
  지정)은 호출 시간 예산(`call_budget_seconds`) 안에서만 웹 조회한다 — 소스 안 직렬(동시 1 ·
  뷰서버 부하 불변) · 소스 간 병렬 · 예산 종료 취소는 실패 기록에 넣지 않는다 · 못 본 도메인은
  캐시, 없으면 `budget_exceeded` + `[한계]` + partial. 무인자 `apm_node_addresses`(사전 스캔)는
  예산 없음.
- B: 조회기가 있으면(플래그 on) `instance_resolution.node_address = true`(단건·배치 항목) · off면
  키 없음.

Open API는 합성(`fleet_fake.FakeFleet`), 웹 목록은 지연 주입 대역(`SlowLister`) 또는 실측 규칙
가짜 웹(`test_plan202_web_session.FakeWeb` + `httpx.MockTransport`) — 실제 제니퍼·외부 네트워크·
실 LLM 없음.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from apm_gateway.adapters.jennifer.web_session import LIST_PATH, WebSession
from apm_gateway.application.node_address import (
    STATUS_BUDGET_EXCEEDED,
    NodeAddressService,
    lookup_fields,
)
from apm_gateway.application.node_address_tools import NodeAddressTools
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.interface.server import create_server
from conftest import NOW_S
from fleet_fake import FakeFleet
from test_plan202_web_session import MIXED_SID, PASSWORD, POD_SID, USER, VM_SID, WEB, FakeWeb
from test_plan210_w2_node_addresses import _web_rows

from apm_gateway.config import load_config

BUDGET = 0.5
SLACK = 0.5  # 예산 뒤 취소·응답 조립 여유(수용 기준 — 0.5초 이하)
HOSTS2 = ("bank.test", "common.test")
HOSTS3 = ("bank.test", "common.test", "third.test")
NEVER = 30.0  # 예산 안에 끝나지 않는 도메인의 지연(뒤에서 돌다 `aclose`로 정리된다)
_SERVICES: list[NodeAddressService] = []


@pytest.fixture(autouse=True)
async def _close_services():
    """시험마다 만든 조회기를 닫는다 — 예산 뒤 마저 도는 웹 조회 작업을 남기지 않는다."""
    yield
    while _SERVICES:
        await _SERVICES.pop().aclose()


class Meter:
    """소스를 가로지른 웹 조회 동시 진입 수."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0


class SlowLister:
    """웹 세션 대역 — 도메인마다 `asyncio.sleep` 지연을 주입하고 동시 진입 수(소스 안·전체)를
    잰다."""

    def __init__(
        self,
        source_id: str,
        domains: int,
        meter: Meter,
        *,
        delay: float = 0.05,
        delays: dict[int, float] | None = None,
    ) -> None:
        self.source_id = source_id
        self.rows = {d: _web_rows(d, source_id) for d in range(1, domains + 1)}
        self.meter = meter
        self.delay = delay
        self.delays = dict(delays or {})
        self.calls: list[int] = []
        self.active = 0
        self.max_active = 0

    @property
    def configured(self) -> bool:
        return True

    async def fetch_list(self, sid: int) -> list[dict[str, Any]]:
        self.calls.append(sid)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.meter.active += 1
        self.meter.max_active = max(self.meter.max_active, self.meter.active)
        try:
            await asyncio.sleep(self.delays.get(sid, self.delay))
            return self.rows.get(sid, [])
        finally:
            self.active -= 1
            self.meter.active -= 1

    async def aclose(self) -> None:
        return None


def _tools(
    tmp_path: Path,
    domains: int,
    service: NodeAddressService | None,
    *,
    hosts: tuple[str, ...] = HOSTS2,
    per_domain: int = 2,
    **fleet: Any,
) -> ApmTools:
    fake = FakeFleet(domains, hosts=hosts, per_domain=per_domain, **fleet)
    cfg = load_config(fake.env(APM_SPOOL_DIR=str(tmp_path / "spool")))
    sources = build_source_set(cfg, transport=httpx.MockTransport(fake.handler))
    return ApmTools(sources, cfg, clock=lambda: NOW_S, node_address=service)


def _listers(ids: tuple[str, ...], domains: int, meter: Meter, **kw: Any) -> list[SlowLister]:
    return [SlowLister(i, domains, meter, **kw) for i in ids]


def _service(
    listers: list[SlowLister], *, budget: float | None = BUDGET, clock: Any = None
) -> NodeAddressService:
    service = NodeAddressService(
        {x.source_id: x for x in listers},
        call_budget_seconds=budget,
        clock=clock or (lambda: NOW_S),
    )
    _SERVICES.append(service)
    return service


async def _drain(service: NodeAddressService, limit: float = 3.0) -> None:
    """예산 뒤 마저 도는 웹 조회가 끝날 때까지 기다린다."""
    deadline = time.perf_counter() + limit
    while service._background:
        assert time.perf_counter() < deadline, "뒤에서 도는 조회가 끝나지 않았다"
        await asyncio.sleep(0.01)


def _node_limit(budget: float, k: int, m: int) -> str:
    return (
        f"[한계] 노드 주소: 호출 시간 예산 {budget:g}초 안에 도메인 {k}/{m}개를 조회하지 못했다"
        "(budget_exceeded) — domain_ids·instance_ids로 좁혀 다시 호출하라"
    )


def _map_limit(budget: float, k: int, m: int) -> str:
    return (
        f"[한계] 노드 주소: 호출 시간 예산 {budget:g}초 안에 도메인 {k}/{m}개를 새로 조회하지"
        " 못했다 — 캐시 값만(없으면 빈 값) · domain_id로 좁혀 다시 호출하라"
    )


async def _warm(core: ApmTools) -> ApmTools:
    """Open API 인벤토리를 미리 읽는다 — 소요 측정이 노드 주소 조회만 보게."""
    await core.sources.available(core.sources.select(None))
    return core


async def _timed(coro: Any) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    out = await coro
    return out, time.perf_counter() - started


# ── A: apm_node_addresses 대상 지정 — 예산 · 소스 간 병렬 · 소스 안 직렬 ─────────────────


async def test_spread_sources_budget_cuts_with_limit_partial_and_input_order(tmp_path):
    """소스 2개 × 도메인 25 = 50쌍 · 도메인당 0.05초(직렬이면 소스당 1.25초) · 예산 0.5초."""
    meter = Meter()
    listers = _listers(("bank", "common"), 25, meter)
    tools = NodeAddressTools(await _warm(_tools(tmp_path, 25, _service(listers))))
    out, elapsed = await _timed(tools.apm_node_addresses(domain_ids=list(range(1, 26))))
    assert elapsed <= BUDGET + SLACK, elapsed
    assert all(x.max_active == 1 for x in listers)  # 소스 안 동시 1(뷰서버 부하 불변)
    assert meter.max_active >= 2  # 소스 간 병렬이 실제로 성립
    exceeded = [r for r in out["rows"] if r["status"] == STATUS_BUDGET_EXCEEDED]
    k = len({(r["source_id"], r["domain_id"]) for r in exceeded})
    assert 0 < k < 50
    assert all(r["host_address"] == "" and r["pod"] is None for r in exceeded)
    assert _node_limit(BUDGET, k, 50) in out["limits"]
    assert out["partial"] is True
    # 행 순서 = 입력(소스·도메인) 순서 — 병렬 도착 순서와 무관
    keys = [(r["source_id"], r["domain_id"], r["instance_id"]) for r in out["rows"]]
    assert keys == [
        (s, d, d * 100 + i) for s in ("bank", "common") for d in range(1, 26) for i in range(2)
    ]
    srcs = out["node_address_sources"]
    assert [s["source_id"] for s in srcs] == ["bank", "common"]
    for src in srcs:
        assert src["status"] == "partial"
        cut = sum(1 for f in src["failed_domains"] if f["status"] == STATUS_BUDGET_EXCEEDED)
        assert f"budget_exceeded {cut}곳" in src["reason"]


async def test_three_sources_run_in_parallel(tmp_path):
    meter = Meter()
    listers = _listers(("bank", "common", "third"), 16, meter)
    tools = NodeAddressTools(await _warm(_tools(tmp_path, 16, _service(listers), hosts=HOSTS3)))
    out, elapsed = await _timed(tools.apm_node_addresses(domain_ids=list(range(1, 17))))
    assert elapsed <= BUDGET + SLACK, elapsed
    assert all(x.max_active == 1 for x in listers) and meter.max_active == 3
    assert any(r["status"] == STATUS_BUDGET_EXCEEDED for r in out["rows"])
    assert out["partial"] is True


async def test_one_source_crowded_fifty_domains(tmp_path):
    """한 소스에 50도메인이 몰림 — 직렬이라 예산 안에 일부만 · 나머지 `budget_exceeded`."""
    meter = Meter()
    (lister,) = _listers(("default",), 50, meter)
    tools = NodeAddressTools(
        await _warm(_tools(tmp_path, 50, _service([lister]), hosts=("bank.test",)))
    )
    out, elapsed = await _timed(tools.apm_node_addresses(domain_ids=list(range(1, 51))))
    assert elapsed <= BUDGET + SLACK, elapsed
    assert lister.max_active == 1
    done = len(lister.calls)
    assert 0 < done < 50
    k = len({r["domain_id"] for r in out["rows"] if r["status"] == STATUS_BUDGET_EXCEEDED})
    assert k >= 50 - done and _node_limit(BUDGET, k, 50) in out["limits"]


async def test_whole_source_budget_exceeded_status_and_reason(tmp_path):
    meter = Meter()
    (lister,) = _listers(("default",), 2, meter, delays={1: NEVER})
    tools = NodeAddressTools(
        _tools(tmp_path, 2, _service([lister], budget=0.2), hosts=("bank.test",))
    )
    out = await tools.apm_node_addresses(domain_ids=[1, 2])
    (src,) = out["node_address_sources"]
    assert src["status"] == STATUS_BUDGET_EXCEEDED
    assert src["reason"] == (
        "호출 시간 예산 초과로 조회하지 못함(JENNIFER_NODE_ADDRESS_CALL_BUDGET_SECONDS)"
    )
    assert lister.calls == [1]  # 도메인 2는 도메인 1 뒤에서 기다리다 예산 종료
    assert _node_limit(0.2, 2, 2) in out["limits"]
    assert any("APM 소스 default 노드 주소 budget_exceeded" in x for x in out["limits"])


async def test_budget_exceeded_row_keeps_pod_from_remembered_kind(tmp_path):
    now = [NOW_S]
    meter = Meter()
    (lister,) = _listers(("default",), 2, meter, delay=0.0)
    service = _service([lister], budget=0.2, clock=lambda: now[0])
    tools = NodeAddressTools(_tools(tmp_path, 2, service, hosts=("bank.test",)))
    await tools.apm_node_addresses(domain_ids=[1])  # 종류(pod) 기억
    now[0] += 1000  # TTL(900) 밖 — 캐시 없음
    lister.delays = {1: NEVER}
    out = await tools.apm_node_addresses(domain_ids=[1])
    assert {r["status"] for r in out["rows"]} == {STATUS_BUDGET_EXCEEDED}
    assert all(r["pod"] is True for r in out["rows"])


async def test_unfinished_domain_with_fresh_cache_is_ok_from_cache(tmp_path):
    """예산 안에 끝내지 못했지만 TTL 안 캐시가 있으면 그 행(상태 ok) — 소스 잠금 뒤에서 기다리던
    도메인도 잠금 없이 캐시를 읽는다."""
    meter = Meter()
    (lister,) = _listers(("default",), 2, meter, delay=0.0)
    service = _service([lister], budget=0.2)
    tools = NodeAddressTools(_tools(tmp_path, 2, service, hosts=("bank.test",)))
    await tools.apm_node_addresses(domain_ids=[2])  # 도메인 2 캐시
    lister.delays = {1: NEVER}
    out = await tools.apm_node_addresses(domain_ids=[1, 2])
    by_domain = {r["domain_id"]: r for r in out["rows"] if r["instance_id"] % 100 == 0}
    assert by_domain[1]["status"] == STATUS_BUDGET_EXCEEDED
    assert by_domain[2]["status"] == "ok" and by_domain[2]["host_address"] == "10.136.2.7"
    assert lister.calls == [2, 1]
    assert _node_limit(0.2, 1, 2) in out["limits"]


async def test_untargeted_call_has_no_budget(tmp_path):
    """무인자(전 도메인 — 사전 스캔)는 예산보다 오래 걸려도 전부 조회한다(소스 간 병렬만)."""
    meter = Meter()
    listers = _listers(("bank", "common"), 4, meter, delay=0.06)  # 소스당 0.24초 > 예산 0.1
    tools = NodeAddressTools(await _warm(_tools(tmp_path, 4, _service(listers, budget=0.1))))
    out = await tools.apm_node_addresses()
    assert all(x.calls == [1, 2, 3, 4] for x in listers)
    assert {r["status"] for r in out["rows"]} <= {"ok", "not_found"}
    assert not any("budget_exceeded" in x for x in out["limits"]) and not out.get("partial")
    assert meter.max_active == 2  # 소스 간 병렬


async def test_budget_end_keeps_inflight_fetch_and_records_no_failure(tmp_path):
    """예산 종료(W9 교정 2) — 웹 조회 중인 도메인은 취소하지 않고 뒤에서 끝나 캐시된다(다음 호출
    hit) · 그 뒤 도메인은 시작하지 않는다 · 실패 기록 0(다른 도메인이 백오프로 막히지 않는다)."""
    meter = Meter()
    (lister,) = _listers(("default",), 2, meter, delay=0.0, delays={1: 0.4})  # 예산 < 0.4 < 상한
    service = _service([lister], budget=0.2)
    tools = NodeAddressTools(await _warm(_tools(tmp_path, 2, service, hosts=("bank.test",))))
    out, elapsed = await _timed(tools.apm_node_addresses(domain_ids=[1, 2]))
    assert elapsed <= 0.2 + SLACK, elapsed
    assert {r["status"] for r in out["rows"]} == {STATUS_BUDGET_EXCEEDED}
    assert service._background  # 도메인 1 조회는 뒤에서 계속
    await _drain(service)
    assert lister.calls == [1]  # 도메인 2는 시작하지 않았다
    assert service._failures == {}
    assert service._state("default").backoff_until == 0.0
    assert service._state("default").breaker.failures == 0
    again = await tools.apm_node_addresses(domain_ids=[1, 2])
    assert lister.calls == [1, 2]  # 도메인 1은 뒤에서 끝난 결과가 캐시 hit
    assert {r["status"] for r in again["rows"]} == {"ok", "not_found"}
    assert "budget_exceeded" not in json.dumps(again["rows"]) and not again.get("partial")


async def test_alarm_path_cap_cancel_still_records_source_backoff():
    """알람 경로 `lookup_fields` 상한 취소는 종전대로 소스 백오프를 건다(예산 취소와 구분)."""
    meter = Meter()
    (lister,) = _listers(("default",), 1, meter, delay=NEVER)
    service = _service([lister])
    fields = await lookup_fields(service, "default", 1, 100, timeout_seconds=0.05)
    assert fields["host_address_status"] == "error"
    assert service._state("default").backoff_until > 0
    assert service._failures[("default", 1)][1] == "error"


async def test_warm_cache_second_call_makes_no_web_call(tmp_path):
    meter = Meter()
    listers = _listers(("bank", "common"), 4, meter, delay=0.0)
    tools = NodeAddressTools(_tools(tmp_path, 4, _service(listers)))
    first = await tools.apm_node_addresses(domain_ids=[1, 3])
    calls = [list(x.calls) for x in listers]
    second = await tools.apm_node_addresses(domain_ids=[1, 3])
    assert [x.calls for x in listers] == calls == [[1, 3], [1, 3]]
    assert first["rows"] == second["rows"]


# ── A: apm_instance_map 대상 지정 — 같은 예산 실행기 ─────────────────────────────────


async def test_instance_map_targeted_budget_spread_sources(tmp_path):
    meter = Meter()
    listers = _listers(("bank", "common"), 25, meter)
    names = dict.fromkeys(range(1, 26), "주문")
    tools = await _warm(_tools(tmp_path, 25, _service(listers), domain_names=names))
    out, elapsed = await _timed(tools.apm_instance_map(business="주문"))
    pairs = {(r["source_id"], r["domain_id"]) for r in out["rows"]}
    assert len(pairs) == 50
    assert elapsed <= BUDGET + SLACK, elapsed
    assert all(x.max_active == 1 for x in listers) and meter.max_active >= 2
    started = {(x.source_id, d) for x in listers for d in x.calls}
    assert len(started) < 50  # 일부는 시작도 못 했다
    # 예산 안에 못 본 도메인(진행 중 취소 포함)은 빈 값 · 본 도메인은 노드 주소
    blank = {
        (r["source_id"], r["domain_id"])
        for r in out["rows"]
        if r["instance_id"] % 100 == 0 and r["host_address"] == ""
    }
    assert pairs - started <= blank and 0 < len(blank) < 50
    assert _map_limit(BUDGET, len(blank), 50) in out["limits"]
    assert out["partial"] is True


async def test_instance_map_one_source_crowded_query(tmp_path):
    meter = Meter()
    (lister,) = _listers(("default",), 50, meter)
    tools = await _warm(
        _tools(tmp_path, 50, _service([lister]), hosts=("bank.test",), per_domain=1)
    )
    out, elapsed = await _timed(tools.apm_instance_map(query="bank-i"))
    assert len({r["domain_id"] for r in out["rows"]}) == 50
    assert elapsed <= BUDGET + SLACK, elapsed
    assert lister.max_active == 1 and 0 < len(lister.calls) < 50
    blank = sum(1 for r in out["rows"] if r["host_address"] == "")
    assert blank >= 50 - len(lister.calls)
    assert _map_limit(BUDGET, blank, 50) in out["limits"] and out["partial"] is True


async def test_instance_map_over_cap_reads_cache_without_waiting_for_source_lock(tmp_path):
    """다른 조회가 소스 잠금을 쥔 채 진행 중 · 51쌍 → 호출은 예산 + 여유 안에 끝나고, 상한 밖
    51번째 쌍은 잠금 없이 캐시 값을 받는다(종전은 잠금을 5초 기다린 뒤 빈 값)."""
    meter = Meter()
    (lister,) = _listers(("default",), 52, meter, delay=0.0, delays={52: NEVER})
    service = _service([lister])
    tools = await _warm(_tools(tmp_path, 51, service, hosts=("bank.test",), per_domain=1))
    await service.domain_snapshot("default", 51, fetch=True)  # 51번째 쌍 캐시
    holder = asyncio.create_task(service.domain_snapshot("default", 52, fetch=True))
    while 52 not in lister.calls:  # 소스 잠금을 쥔 채 웹 조회 중
        await asyncio.sleep(0)
    try:
        out, elapsed = await _timed(tools.apm_instance_map(query="bank-i"))
    finally:
        holder.cancel()
    assert elapsed <= BUDGET + SLACK, elapsed
    by_domain = {r["domain_id"]: r["host_address"] for r in out["rows"]}
    assert len(by_domain) == 51 and by_domain[51] == "10.136.51.7"
    assert all(by_domain[d] == "" for d in range(1, 51))  # 잠금 뒤에서 예산 종료
    assert any("[한계] 노드 주소: 도메인 51개 중 50개만 새로 조회" in x for x in out["limits"])
    assert _map_limit(BUDGET, 50, 50) in out["limits"] and out["partial"] is True
    assert lister.calls == [51, 52]


async def test_instance_map_within_budget_has_no_budget_limit(tmp_path):
    meter = Meter()
    (lister,) = _listers(("default",), 6, meter, delay=0.0)
    tools = _tools(tmp_path, 6, _service([lister]), hosts=("bank.test",), per_domain=1)
    out = await tools.apm_instance_map(query="bank-i")
    assert lister.calls == [1, 2, 3, 4, 5, 6]
    assert not out.get("partial") and not any("예산" in x for x in out["limits"])


async def test_instance_map_untargeted_stays_cache_only(tmp_path):
    meter = Meter()
    (lister,) = _listers(("default",), 3, meter, delay=0.0)
    tools = _tools(tmp_path, 3, _service([lister], budget=0.01), hosts=("bank.test",))
    out = await tools.apm_instance_map()
    assert lister.calls == [] and all(r["host_address"] == "" for r in out["rows"])
    assert not out.get("partial") and not any("노드 주소" in x for x in out["limits"])


async def test_resolution_then_node_addresses_reuses_cache(tmp_path):
    """해석 `apm_instance_map(hostname=…)` 뒤 같은 도메인 `apm_node_addresses(instance_ids=…)` —
    그 도메인 웹 조회 합계 1(TTL 캐시)."""
    meter = Meter()
    (lister,) = _listers(("default",), 6, meter, delay=0.0)
    core = _tools(tmp_path, 6, _service([lister]), hosts=("bank.test",), per_domain=1)
    resolved = await core.apm_instance_map(hostname="bank-h5-0")
    assert [r["host_address"] for r in resolved["rows"]] == ["10.136.5.7"]
    out = await NodeAddressTools(core).apm_node_addresses(instance_ids=[500])
    assert lister.calls == [5]
    assert [(r["instance_id"], r["status"]) for r in out["rows"]] == [(500, "ok")]


# ── A: 실 WebSession — 로그인 소스당 1회 ───────────────────────────────────────────


async def test_real_web_sessions_login_once_per_source_in_one_call():
    webs = {"b0": FakeWeb(), "b1": FakeWeb()}
    sessions = {
        source_id: WebSession(
            source_id=source_id,
            url=WEB,
            user=USER,
            password=PASSWORD,
            transport=httpx.MockTransport(fake),
        )
        for source_id, fake in webs.items()
    }
    service = NodeAddressService(sessions, call_budget_seconds=5.0, clock=lambda: NOW_S)
    pairs = [(s, d) for s in webs for d in (POD_SID, VM_SID, MIXED_SID)]
    snapshots, missed = await service.domain_snapshots(pairs, budget_seconds=5.0)
    assert missed == set() and list(snapshots) == pairs
    assert {s.status for s in snapshots.values()} == {"ok"}
    assert snapshots[("b1", POD_SID)].rows["1001"]["host_address"] == "10.136.1.7"
    for fake in webs.values():
        assert fake.calls.count(("POST", "/login/page")) == 1
        assert fake.calls.count(("GET", LIST_PATH)) == 3
    await service.aclose()


# ── B: 서버명 경로 표지 — instance_resolution.node_address ─────────────────────────


def _json(result: Any) -> dict[str, Any]:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


async def test_flag_off_envelope_has_no_node_address_key(tmp_path):
    mcp = create_server(_tools(tmp_path, 3, None))
    single = _json(await mcp.call_tool("apm_app_health", {"hostname": "bank-h1-0"}))
    batch = _json(
        await mcp.call_tool(
            "apm_app_health", {"targets": [{"hostname": "bank-h1-0"}, {"hostname": "common-h2-1"}]}
        )
    )
    assert "node_address" not in single["instance_resolution"]
    assert all("node_address" not in b["instance_resolution"] for b in batch["batch"])
    assert "node_address" not in json.dumps(single) + json.dumps(batch)


async def test_flag_on_marks_single_and_batch_items(tmp_path):
    meter = Meter()
    listers = _listers(("bank", "common"), 3, meter, delay=0.0)
    on = create_server(_tools(tmp_path, 3, _service(listers)))
    off = create_server(_tools(tmp_path, 3, None))
    single = _json(await on.call_tool("apm_app_health", {"hostname": "bank-h1-0"}))
    plain = _json(await off.call_tool("apm_app_health", {"hostname": "bank-h1-0"}))
    assert single["instance_resolution"]["node_address"] is True
    # 표지 하나 말고는 플래그 off와 같다 — instance_refs 모양 불변
    resolution = {k: v for k, v in single["instance_resolution"].items() if k != "node_address"}
    assert resolution == plain["instance_resolution"]
    assert single["instance_resolution"]["instance_refs"] == [
        {"source_id": "bank", "domain_id": 1, "instance_id": 100}
    ]
    targets = [{"hostname": "bank-h1-0"}, {"hostname": "common-h2-1"}]
    batch = _json(await on.call_tool("apm_app_health", {"targets": targets}))
    assert [b["instance_resolution"]["node_address"] for b in batch["batch"]] == [True, True]
    assert "instance_resolution" not in batch  # 배치 최상위에는 해석이 없다(표지 없음 — 정상)
    assert all(x.calls == [] for x in listers)  # 표지는 웹 조회를 부르지 않는다
