"""가벼운 도메인 루프 병렬화 — 실시간 순위 · 인스턴스 명단 적재 · 대상 실시간 · 시 단위 통계
(plans/207 W2 · D-296 ⑤).

- 소스 동시 요청 상한 4에서 동시 in-flight > 1이고, 결과·`limits`·실패 순서가 상한 1(종전 직렬)과
  같다(일부 도메인 실패를 섞는다 · 소스 2개).
- 계약 위반은 입력 순서상 첫 위반에서 올린다.
- 속도 상한(호출 시작 간격 ≥ 1/rate)은 병렬에서도 지켜진다.
- 공유 적재의 자식 호출은 시작한 요청의 우선순위에 끌려가지 않는다.
- 성능(축소 규모): 도메인 35 · 지연 0.033초 · rate 50 — 직렬 ≈ 1.16초 → 병렬 ≈ 0.7초(rate 지배).

외부 호출 0 — 합성 Open API(`fleet_fake`) + httpx `MockTransport`(지연).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
from apm_gateway.application.fleet_tools import FleetTools
from apm_gateway.application.resolver import InstanceResolver, Resolution
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tool_common import Limits, Window
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    CallScope,
    SharedLoadScope,
    current_scope,
    use_scope,
)
from apm_gateway.domain.errors import CONTRACT_VIOLATION, ApmError
from conftest import NOW_MS, NOW_S
from fleet_fake import NOT_CONNECTED, FakeFleet

from apm_gateway.config import load_config

REALTIME = "/api/realtime/instance"
INSTANCE = "/api/instance"
STATUS = "/api/status/application"
HOUR = 3_600_000


class SlowFleet(FakeFleet):
    """`FakeFleet` + 경로별 지연 · 동시 in-flight 측정(경로별 · (소스 호스트, 경로)별) · 호출 시작
    시각 · 도메인별 실패(명단·통계·계약 위반) · 시 단위 애플리케이션 통계(도메인마다 행 1개)."""

    def __init__(self, domains: int, **kw: Any) -> None:
        super().__init__(domains, **kw)
        self.delay: dict[str, float] = {}
        self.active: dict[Any, int] = {}
        self.peak: dict[Any, int] = {}
        self.starts: dict[str, list[float]] = {}
        self.instance_fail: set[tuple[str, int]] = set()
        self.status_fail: set[tuple[str, int]] = set()
        self.contract_fail: set[tuple[str, int]] = set()
        self.domain_delay: dict[int, float] = {}  # 도메인별 추가 지연(늦게 끝나는 묶음)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.starts.setdefault(path, []).append(time.monotonic())
        for key in (path, (host, path)):
            self.active[key] = self.active.get(key, 0) + 1
            self.peak[key] = max(self.peak.get(key, 0), self.active[key])
        d = int(request.url.params.get("domain_id", "0"))
        try:
            if self.delay.get(path):
                await asyncio.sleep(self.delay[path] + self.domain_delay.get(d, 0.0))
        finally:
            for key in (path, (host, path)):
                self.active[key] -= 1
        if (host, d) in self.contract_fail:
            message = f"Required request parameter 'x' is not present (domain {d})"
            return httpx.Response(500, json={"exception": {"message": message}})
        if path == INSTANCE and (host, d) in self.instance_fail:
            return httpx.Response(500, json=NOT_CONNECTED)
        if path == STATUS:
            if (host, d) in self.status_fail:
                return httpx.Response(500, json=NOT_CONNECTED)
            row = {
                "name": f"/app/{host.split('.')[0]}/{d}",
                "calls": d * 10,
                "failures": d,
                "responseTime": float(d * 3),
                "totalResponseTime": d * 30,
                "maxResponseTime": d * 7,
            }
            return httpx.Response(200, json={"result": [row]})
        return await super().handler(request)


def _tools(fake: SlowFleet, tmp_path, cap: int, rate: float = 0.0) -> ApmTools:
    cfg = load_config(
        fake.env(
            APM_SPOOL_DIR=str(tmp_path / "spool"),
            JENNIFER_MAX_INFLIGHT_PER_SOURCE=str(cap),
            # 폭 = min(도구 안 폭, 자리) — 자리 수가 폭을 정하게 한다(plans/205 · D-356 ①)
            APM_FANOUT_CONCURRENCY=str(cap),
            JENNIFER_RATE_LIMIT_PER_SEC=str(rate),
        )
    )
    return ApmTools(build_source_set(cfg, transport=httpx.MockTransport(fake.handler)), cfg,
                    clock=lambda: NOW_S)


async def _warm(tools: ApmTools) -> None:
    for src in tools.sources:
        await src.resolver.inventory()


def _fleet(**kw: Any) -> SlowFleet:
    fake = SlowFleet(12, per_domain=2, **kw)
    # 두 소스에 걸쳐 앞·뒤 도메인 실패를 섞는다(실패 목록·`[한계]` 순서 확인)
    fake.realtime_fail = {("bank.test", 2), ("common.test", 11), ("bank.test", 9)}
    fake.instance_fail = {("common.test", 3), ("bank.test", 10)}
    fake.status_fail = {("common.test", 5), ("bank.test", 1)}
    return fake


async def _resolution(tools: ApmTools) -> Resolution:
    """전 소스·전 도메인의 인스턴스 — (소스, 도메인) 묶음이 소스끼리 엇갈리게 섞는다."""
    per_source = [(await src.resolver.inventory()).instances for src in tools.sources]
    mixed = [inst for pair in zip(*per_source, strict=False) for inst in pair]
    return Resolution(hostname="", instances=mixed, confidence="high", reason="test")


# ── 결과·순서 = 종전 직렬 ─────────────────────────────────────────


async def test_fleet_ranking_parallel_matches_serial(tmp_path):
    out: dict[int, dict[str, Any]] = {}
    peaks: dict[int, tuple[int, int, int]] = {}
    for cap in (1, 4):
        fake = _fleet()
        tools = _tools(fake, tmp_path / str(cap), cap)
        await _warm(tools)
        fake.delay[REALTIME] = 0.01
        out[cap] = await FleetTools(tools).ranking(full=True)
        peaks[cap] = (
            fake.peak[("bank.test", REALTIME)],
            fake.peak[("common.test", REALTIME)],
            fake.peak[REALTIME],
        )
    # 상한 1 = 종전 직렬(전체 1) · 상한 4 = 소스마다 4 + 소스끼리도 겹친다(전체 8)
    assert peaks == {1: (1, 1, 1), 4: (4, 4, 8)}
    assert out[4] == out[1]
    assert out[1]["summary"]["domains_failed"] == 3
    assert (
        "[한계] 잠정 순위 — 조회 실패 도메인 3곳(소스 bank · 도메인 2, 소스 bank · 도메인 9,"
        " 소스 common · 도메인 11)은 순위에 없습니다"
    ) in out[1]["limits"]


async def test_resolver_load_parallel_matches_serial(tmp_path):
    invs = {}
    peaks: dict[int, int] = {}
    for cap in (1, 4):
        fake = _fleet()
        fake.delay[INSTANCE] = 0.01
        tools = _tools(fake, tmp_path / str(cap), cap)
        invs[cap] = [await src.resolver.inventory() for src in tools.sources]
        peaks[cap] = fake.peak[INSTANCE]
    assert peaks == {1: 1, 4: 4}
    for serial, parallel in zip(invs[1], invs[4], strict=True):
        assert parallel.domains == serial.domains
        assert parallel.instances == serial.instances
        assert list(parallel.unavailable.items()) == list(serial.unavailable.items())
    assert list(invs[1][0].unavailable) == [10] and list(invs[1][1].unavailable) == [3]


async def test_core_realtime_parallel_matches_serial(tmp_path):
    out: dict[int, tuple[Any, list[str], bool]] = {}
    peaks: dict[int, int] = {}
    for cap in (1, 4):
        fake = _fleet()
        tools = _tools(fake, tmp_path / str(cap), cap)
        res = await _resolution(tools)
        fake.delay[REALTIME] = 0.01
        limits = Limits()
        got = await tools.realtime(res, limits)
        out[cap] = (list(got.items()), list(limits), limits.partial)
        peaks[cap] = fake.peak[REALTIME]
    assert peaks[1] == 1 and peaks[4] > 1
    assert out[4] == out[1]
    fails = [x for x in out[1][1] if "실시간 조회 실패" in x]
    # 묶음 순서(소스가 엇갈린 해소 순서) 그대로
    assert fails == [
        "[한계] 실시간 조회 실패(소스 bank · 도메인 2): source_unavailable",
        "[한계] 실시간 조회 실패(소스 bank · 도메인 9): source_unavailable",
        "[한계] 실시간 조회 실패(소스 common · 도메인 11): source_unavailable",
    ]


async def test_core_hourly_parallel_matches_serial(tmp_path):
    window = Window(NOW_MS - 3 * HOUR, NOW_MS, True, "Asia/Seoul")
    out: dict[int, tuple[Any, list[str]]] = {}
    peaks: dict[int, int] = {}
    for cap in (1, 4):
        fake = _fleet()
        tools = _tools(fake, tmp_path / str(cap), cap)
        res = await _resolution(tools)
        fake.delay[STATUS] = 0.01
        limits = Limits()
        out[cap] = (await tools.hourly(res, window, limits), list(limits))
        peaks[cap] = fake.peak[STATUS]
    assert peaks[1] == 1 and peaks[4] > 1
    assert out[4] == out[1]
    assert [x for x in out[1][1] if "시 단위 통계 조회 실패" in x] == [
        "[한계] 시 단위 통계 조회 실패(소스 bank · 도메인 1): source_unavailable",
        "[한계] 시 단위 통계 조회 실패(소스 common · 도메인 5): source_unavailable",
    ]


async def test_contract_violation_raises_first_in_input_order(tmp_path):
    """계약 위반은 순서상 첫 위반에서 올린다(먼저 끝난 뒤 위반이 아니라) — 뒤 묶음 호출은 이미
    나갔을 수 있다(허용)."""
    fake = SlowFleet(8, hosts=("bank.test",))
    tools = _tools(fake, tmp_path, 4)
    await _warm(tools)
    fake.delay[REALTIME] = 0.01
    fake.realtime_fail = {("bank.test", 2)}  # 앞 묶음의 보통 실패는 실패 목록으로
    fake.contract_fail = {("bank.test", 3), ("bank.test", 4)}
    fake.domain_delay = {3: 0.05}  # 도메인 3이 4보다 늦게 끝난다
    with pytest.raises(ApmError) as e:
        await FleetTools(tools).ranking(full=True)
    assert e.value.code == CONTRACT_VIOLATION
    assert "(domain 3)" in e.value.reason


# ── 속도 상한 ─────────────────────────────────────────────────


async def test_rate_limit_spacing_holds_in_parallel(tmp_path):
    rate = 50.0
    fake = SlowFleet(16, hosts=("bank.test",))
    tools = _tools(fake, tmp_path, 4, rate=rate)
    await _warm(tools)
    fake.delay[REALTIME] = 0.05  # 지연 > 간격 — 겹쳐 부른다
    fake.starts.clear()
    await FleetTools(tools).ranking(full=True)
    starts = fake.starts[REALTIME]
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert len(starts) == 16 and fake.peak[REALTIME] > 1
    assert min(gaps) >= 1 / rate - 0.005, gaps


# ── 공유 적재 맥락 ────────────────────────────────────────────


class _ScopeApi:
    """도메인 조회·인스턴스 조회 때 보이는 호출 맥락을 적는다."""

    def __init__(self) -> None:
        self.seen: list[tuple[type, str]] = []

    async def domains(self):
        return [{"domain_id": d, "domain_name": f"d{d}"} for d in range(1, 9)]

    async def instances(self, domain_id, domain_name=""):
        scope = current_scope()
        self.seen.append((type(scope), scope.priority))
        await asyncio.sleep(0.005)
        return []


async def test_shared_load_children_keep_shared_scope():
    api = _ScopeApi()
    res = InstanceResolver(api, {}, max_in_flight=4)  # type: ignore[arg-type]
    with use_scope(CallScope(PRIORITY_BACKGROUND)):
        await res.inventory()
    assert len(api.seen) == 8
    assert {s for s in api.seen} == {(SharedLoadScope, "interactive")}


# ── 성능(축소 규모) ───────────────────────────────────────────


async def test_untargeted_ranking_parallel_is_faster(tmp_path):
    """도메인 35 · 지연 0.033초 · rate 50(= 운영 350 · 0.33초 · rate 5의 1/10 축소)."""
    elapsed: dict[int, float] = {}
    for cap in (1, 4):
        fake = SlowFleet(35, hosts=("bank.test",))
        tools = _tools(fake, tmp_path / str(cap), cap, rate=50.0)
        await _warm(tools)
        fake.delay[REALTIME] = 0.033
        started = time.monotonic()
        await FleetTools(tools).ranking(full=True)
        elapsed[cap] = time.monotonic() - started
    print(f"\n[plans/207 W2 성능] 직렬 {elapsed[1]:.3f}초 · 병렬(상한 4) {elapsed[4]:.3f}초")
    assert elapsed[1] >= 35 * 0.033 * 0.95
    assert elapsed[4] < elapsed[1] * 0.8
