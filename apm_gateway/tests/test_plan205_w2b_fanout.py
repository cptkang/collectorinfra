"""도구 안 독립 호출 동시 실행 — W2b 경로(plans/205 W2 · D-356 ① · W0 표 #7·#8·#10~#14·#16·
#21·#22·#24·#27·#28·#31).

경로마다 폭 4(`APM_FANOUT_CONCURRENCY` 기본)와 폭 1(종전 직렬)을 같은 합성 원천에 돌려:

- ① 봉투가 같다 — 행·`limits` 순서·`partial`·오류 봉투(일부 단위 실패 · 전부 실패).
- ② 지연 가짜 전송에서 폭 4가 겹쳐 부르고(동시 in-flight > 1) 더 빨리 끝난다(느슨한 단언).
- ③ 호출 맥락 감사(`api_calls`·소스별 호출 수·예상 호출 수 `expect_calls`)가 같다.
- 계약 위반(`CONTRACT_VIOLATION`)은 입력 순서상 첫 위반에서 올린다(#11·#27·#28).

외부 호출 0 — 합성 Open API(`fleet_fake`) + httpx `MockTransport`(지연).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from apm_gateway.application.fleet_tools import FleetTools
from apm_gateway.application.scope_tools import ScopeTools
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.call_context import CallScope, use_scope
from apm_gateway.domain.errors import CONTRACT_VIOLATION, ApmError
from conftest import NOW_MS, NOW_S, recorded_body
from fleet_fake import NOT_CONNECTED, FakeFleet, event

from apm_gateway.config import load_config

HOST = "shared-host"  # 모든 인스턴스가 같은 hostName — 해소 1번에 (소스, 도메인) 12묶음
NAMES = {1: "alpha", 2: "bravo", 3: "charlie", 4: "delta", 5: "echo", 6: "foxtrot"}
GUID = "g-205"
HOUR = 3_600_000

STATUS = "/api/status/application"
SERIES_INSTANCE = "/api/dbmetrics/instance"
SERIES_DOMAIN = "/api/dbmetrics/domain"
SERIES_BUSINESS = "/api/dbmetrics/business"
RT_DOMAIN = "/api/realtime/domain"
RT_BUSINESS = "/api/realtime/business"
BUSINESS = "/api/business"
DEPLOY = "/api-v2/deploy"
TX_GUID = "/api/transaction/guid"
EVENT = "/api/dbsearch/event"


def _biz(bid: int, name: str) -> dict[str, Any]:
    return {
        "businessId": bid,
        "name": name,
        "description": f"{name} 업무",
        "businessIndex": f"idx-{bid}",
        "businessOid": 70_000 + bid,
        "badResponseTime": 3000,
        "ruleList": [],
    }


def _series(seed: int) -> dict[str, Any]:
    return {
        "result": [
            {"time": str(NOW_MS - (3 - i) * 300_000), "value": float(seed + i)} for i in range(3)
        ]
    }


class FanFake(FakeFleet):
    """`FakeFleet` + W2b 경로 응답 · 경로별 지연·동시 in-flight 측정 · 경로·(호스트, 도메인)별
    실패(미접속 = `source_unavailable`) · 계약 위반 · 정렬 기준 거부 · 변경 이력 조각 실패."""

    def __init__(self, domains: int = 6, **kw: Any) -> None:
        super().__init__(domains, per_domain=2, domain_names=NAMES, **kw)
        self.delay: dict[str, float] = {}
        self.domain_delay: dict[int, float] = {}
        self.active: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.fail: dict[str, set[tuple[str, int]]] = {}
        self.contract: dict[str, set[tuple[str, int]]] = {}
        self.refuse_sort: set[tuple[str, int]] = set()
        # 변경 이력 — (호스트, 도메인) → 성공하는 앞 조각 수(그다음 조각부터 실패)
        self.deploy_ok: dict[tuple[str, int], int] = {}
        self.deploy_calls: dict[tuple[str, int], int] = {}

    @staticmethod
    def _key(path: str) -> str:
        return DEPLOY if path.startswith(DEPLOY + "/") else path

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host, path, q = request.url.host, request.url.path, request.url.params
        key = self._key(path)
        d = int(path.rsplit("/", 1)[1]) if key == DEPLOY else int(q.get("domain_id", "0"))
        self.active[key] = self.active.get(key, 0) + 1
        self.peak[key] = max(self.peak.get(key, 0), self.active[key])
        try:
            if self.delay.get(key):
                await asyncio.sleep(self.delay[key] + self.domain_delay.get(d, 0.0))
        finally:
            self.active[key] -= 1
        if (host, d) in self.contract.get(key, set()):
            message = f"Required request parameter 'x' is not present (domain {d})"
            return httpx.Response(500, json={"exception": {"message": message}})
        if (host, d) in self.fail.get(key, set()):
            return httpx.Response(500, json=NOT_CONNECTED)
        sid = host.split(".")[0]
        if key == "/api/instance":
            rows = [
                {
                    "instanceId": d * 100 + k,
                    "name": f"{sid}-i{d}-{k}",
                    "hostName": HOST,
                    "ipAddress": "10.0.0.1",
                    "platform": "JAVA",
                    "status": "RUNNING",
                    "version": "5.6.5",
                }
                for k in range(self.per_domain)
            ]
            return httpx.Response(200, json={"result": rows})
        if key == STATUS:
            if "sort_by_metrics" in q and (host, d) in self.refuse_sort:
                return httpx.Response(500, json={"exception": {"message": "unknown sort"}})
            iid = int(q.get("instance_id", "0").split(",")[0] or 0)
            rows = [
                {
                    "name": f"/app/{sid}/{d}/{iid}/{k}",
                    "calls": d * 10 + k + iid % 7,
                    "failures": d + k,
                    "responseTime": float(d * 3 + k),
                    "totalResponseTime": (d * 10 + k + iid % 7) * (d * 3 + k),
                    "maxResponseTime": d * 7 + k,
                }
                for k in range(2)
            ]
            return httpx.Response(200, json={"result": rows})
        if key == "/api/metrics":
            return httpx.Response(200, json=recorded_body("GET_api_metrics__ok.json"))
        if key in (SERIES_INSTANCE, SERIES_DOMAIN, SERIES_BUSINESS):
            seed = d * 10 + len(q.get("metrics", "")) + int(q.get("instance_id", "0")) % 5
            return httpx.Response(200, json=_series(seed))
        if key == BUSINESS:
            return httpx.Response(
                200, json={"result": [_biz(d * 10 + 1, f"order-{d}"), _biz(d * 10 + 2, f"pay-{d}")]}
            )
        if key == RT_DOMAIN:
            wanted = [d] if "domain_id" in q else self.domains
            rows = [
                {"domainId": x, "domainName": NAMES[x], "tps": float(x), "responseTime": 10.0 * x}
                for x in wanted
            ]
            return httpx.Response(200, json={"result": rows})
        if key == RT_BUSINESS:
            bid = int(q["business_id"]) if "business_id" in q else None
            rows = [
                {
                    "domainId": d,
                    "businessId": b,
                    "businessName": f"{'order' if b % 10 == 1 else 'pay'}-{d}",
                    "tps": float(b),
                    "responseTime": 2.0 * b,
                }
                for b in (d * 10 + 1, d * 10 + 2)
                if bid is None or b == bid
            ]
            return httpx.Response(200, json={"result": rows})
        if key == DEPLOY:
            n = self.deploy_calls.get((host, d), 0)
            self.deploy_calls[(host, d)] = n + 1
            if (host, d) in self.deploy_ok and n >= self.deploy_ok[(host, d)]:
                return httpx.Response(500, json=NOT_CONNECTED)
            start = int(q["startTime"])
            rows = [
                {"instanceId": d * 100 + k, "collectTime": str(start + d * 60_000 + k * 1000)}
                for k in range(2)
            ]
            return httpx.Response(200, json=rows)
        if key == TX_GUID:
            rows = [
                {
                    "domainId": d,
                    "instanceId": d * 100,
                    "instanceName": f"{sid}-i{d}-0",
                    "applicationName": f"/svc/{d}",
                    "txid": f"{sid}{d}",
                    "guid": GUID,
                    "responseTime": 100 * d,
                    "startTime": str(NOW_MS - d * 1000),
                    "endTime": str(NOW_MS - d * 1000 + 50),
                }
            ]
            return httpx.Response(200, json={"result": rows})
        if key in ("/api/activeService/list", "/api/transaction/time"):
            return httpx.Response(200, json={"result": []})
        return await super().handler(request)


class Audit(CallScope):
    """작업 감사와 같은 셈 — 호출 수 · 소스별 호출 수 · 예상 호출 수(`expect_calls` 누적)."""

    def __init__(self) -> None:
        super().__init__()
        self.api_calls = 0
        self.by_source: dict[str, int] = {}
        self.expected = 0

    def count_call(self, source_id: str = "", *, shared: bool = False) -> None:
        if not shared:
            self.api_calls += 1
        if source_id:
            self.by_source[source_id] = self.by_source.get(source_id, 0) + 1

    def expect_calls(self, count: int) -> None:
        self.expected += int(count)

    def summary(self) -> tuple[int, dict[str, int], int]:
        return self.api_calls, dict(self.by_source), self.expected


def _tools(fake: FanFake, tmp_path, width: int) -> ApmTools:
    cfg = load_config(
        fake.env(
            APM_SPOOL_DIR=str(tmp_path / "spool"),
            APM_FANOUT_CONCURRENCY=str(width),
        )
    )
    return ApmTools(
        build_source_set(cfg, transport=httpx.MockTransport(fake.handler)), cfg, clock=lambda: NOW_S
    )


Call = Callable[[ApmTools], Awaitable[dict[str, Any]]]


async def _run(
    setup: Callable[[FanFake], None], call: Call, path: str, tmp_path, *, delay: float = 0.02
) -> dict[int, dict[str, Any]]:
    """폭 1·4로 같은 원천을 돌린다 → 폭마다 {out(봉투 또는 오류), audit, peak, elapsed}."""
    got: dict[int, dict[str, Any]] = {}
    for width in (1, 4):
        fake = FanFake()
        setup(fake)
        tools = _tools(fake, tmp_path / str(width), width)
        for src in tools.sources:  # 인스턴스 명단은 미리 적재(공유 적재 호출은 감사 밖)
            await src.resolver.inventory()
        fake.delay[path] = delay
        audit = Audit()
        started = time.monotonic()
        with use_scope(audit):
            try:
                out: Any = await call(tools)
            except ApmError as e:
                out = ("error", e.code, e.reason)
        got[width] = {
            "out": out,
            "audit": audit.summary(),
            "peak": fake.peak.get(path, 0),
            "elapsed": time.monotonic() - started,
        }
    return got


def _assert_same(got: dict[int, dict[str, Any]], *, faster: bool = True) -> dict[str, Any]:
    """① 봉투 동일 · ③ 감사 동일 · ② 폭 4만 겹쳐 부르고 더 빨리 끝난다."""
    serial, fan = got[1], got[4]
    assert fan["out"] == serial["out"]
    assert fan["audit"] == serial["audit"]
    assert serial["peak"] == 1
    assert fan["peak"] > 1
    if faster:
        assert fan["elapsed"] < serial["elapsed"], (fan["elapsed"], serial["elapsed"])
    return serial["out"]


def _fails(out: dict[str, Any], marker: str) -> list[str]:
    return [x for x in out["limits"] if marker in x]


# ── #7 apm_status_stats `_status_fetch` ───────────────────────────


async def test_status_stats_partial_and_sort_refusal(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[STATUS] = {("bank.test", 2), ("common.test", 5)}
        fake.refuse_sort = {("common.test", 1), ("bank.test", 4)}  # 재조회(expect_calls(1))

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await tools.apm_status_stats(
            "application", hostname=HOST, sort_by="calls", full=True
        )

    got = await _run(setup, call, STATUS, tmp_path)
    out = _assert_same(got)
    assert out["partial"] is True
    assert _fails(out, "통계 조회 실패") == [
        "[한계] application 통계 조회 실패(소스 bank · 도메인 2): source_unavailable",
        "[한계] application 통계 조회 실패(소스 common · 도메인 5): source_unavailable",
    ]
    refusal = next(x for x in out["limits"] if "정렬 기준 calls를 받지 않아" in x)
    # 거부한 묶음 순서(소스가 엇갈린 해소 순서가 아니라 묶음 순서) 그대로
    assert refusal.index("소스 bank · 도메인 4") < refusal.index("소스 common · 도메인 1")
    api_calls, _, expected = got[1]["audit"]
    assert (api_calls, expected) == (12 + 2, 12 + 2)


async def test_status_stats_all_failed(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[STATUS] = {(h, d) for h in fake.hosts for d in fake.domains}

    got = await _run(
        setup, lambda t: t.apm_status_stats("application", hostname=HOST), STATUS, tmp_path
    )
    out = _assert_same(got)
    assert out[0] == "error" and out[1] == "source_unavailable"


# ── #8 apm_metrics series instance ───────────────────────────────


async def test_instance_series_partial_and_all_failed(tmp_path):
    def call(tools: ApmTools) -> Awaitable[dict[str, Any]]:
        return tools.apm_metrics(
            mode="series", scope="instance", hostname=HOST, metrics=["gc_time", "heap_committed"]
        )

    def setup(fake: FanFake) -> None:
        fake.fail[SERIES_INSTANCE] = {("common.test", 1), ("bank.test", 6)}

    out = _assert_same(await _run(setup, call, SERIES_INSTANCE, tmp_path / "p"))
    assert out["partial"] is True
    assert len(_fails(out, "조회 실패")) == 2 * 2 * 2  # 묶음 2 × 인스턴스 2 × 지표 2
    assert len(out["rows"]) == (24 - 4) * 2 * 3

    def setup_all(fake: FanFake) -> None:
        fake.fail[SERIES_INSTANCE] = {(h, d) for h in fake.hosts for d in fake.domains}

    out = _assert_same(await _run(setup_all, call, SERIES_INSTANCE, tmp_path / "a"))
    assert out[0] == "error"


# ── #10 apm_service_status ──────────────────────────────────────


async def test_service_status_narrowed_and_all_domains(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[RT_DOMAIN] = {("bank.test", 3), ("common.test", 1)}

    async def narrowed(tools: ApmTools) -> dict[str, Any]:
        return await ScopeTools(tools).apm_service_status(
            service=["alpha", "charlie", "echo", "foxtrot"]
        )

    out = _assert_same(await _run(setup, narrowed, RT_DOMAIN, tmp_path / "n"))
    assert out["partial"] is True
    assert len(_fails(out, "실시간 도메인 조회 실패")) == 2
    assert len(out["rows"]) == 8 - 2

    def setup_down(fake: FanFake) -> None:  # 전 도메인 — 소스당 1호출 · 한 소스 실패
        fake.fail[RT_DOMAIN] = {("common.test", 0)}

    async def everything(tools: ApmTools) -> dict[str, Any]:
        return await ScopeTools(tools).apm_service_status()

    out = _assert_same(await _run(setup_down, everything, RT_DOMAIN, tmp_path / "a"))
    assert [s["status"] for s in out["sources"]] == ["ok", "unavailable"]


# ── #11·#12·#13 apm_business ──────────────────────────────────────


async def test_business_list_defs_partial(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[BUSINESS] = {("bank.test", 2), ("common.test", 6)}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await ScopeTools(tools).apm_business(mode="list")

    got = await _run(setup, call, BUSINESS, tmp_path)
    out = _assert_same(got)
    assert out["partial"] is True
    assert _fails(out, "업무 목록 조회 실패") == [
        "[한계] 업무 목록 조회 실패(소스 bank · 도메인 2): source_unavailable",
        "[한계] 업무 목록 조회 실패(소스 common · 도메인 6): source_unavailable",
    ]
    assert len(out["rows"]) == (12 - 2) * 2
    assert got[1]["audit"] == (12, {"bank": 6, "common": 6}, 12)


async def test_business_defs_all_failed(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[BUSINESS] = {(h, d) for h in fake.hosts for d in fake.domains}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await ScopeTools(tools).apm_business(mode="list")

    out = _assert_same(await _run(setup, call, BUSINESS, tmp_path))
    assert out[0] == "error" and out[1] == "source_unavailable"


async def test_business_defs_contract_violation_first_in_order(tmp_path):
    fake = FanFake(hosts=("bank.test",))
    tools = _tools(fake, tmp_path, 4)
    await tools.sources.get("default").resolver.inventory()
    fake.delay[BUSINESS] = 0.01
    fake.fail[BUSINESS] = {("bank.test", 2)}
    fake.contract[BUSINESS] = {("bank.test", 3), ("bank.test", 4)}
    fake.domain_delay = {3: 0.05}  # 도메인 3이 4보다 늦게 끝난다
    with pytest.raises(ApmError) as e:
        await ScopeTools(tools).apm_business(mode="list")
    assert e.value.code == CONTRACT_VIOLATION
    assert "(domain 3)" in e.value.reason


async def test_business_current_all(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[RT_BUSINESS] = {("common.test", 2), ("bank.test", 5)}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await ScopeTools(tools).apm_business(mode="current")

    out = _assert_same(await _run(setup, call, RT_BUSINESS, tmp_path))
    assert out["partial"] is True
    assert len(_fails(out, "실시간 업무 조회 실패")) == 2
    assert len(out["rows"]) == (12 - 2) * 2


async def test_business_current_named(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[RT_BUSINESS] = {("bank.test", 1), ("common.test", 4)}
        fake.fail[BUSINESS] = {("common.test", 6)}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await ScopeTools(tools).apm_business(mode="current", business=["pay"])

    for path in (RT_BUSINESS, BUSINESS):  # 업무 정의(#11)·현재값(#13) 둘 다 겹친다
        out = _assert_same(await _run(setup, call, path, tmp_path / path.replace("/", "_")))
        assert out["partial"] is True
        assert len(_fails(out, "실시간 업무 조회 실패")) == 2
        assert len(_fails(out, "업무 목록 조회 실패")) == 1
        assert len(out["rows"]) == 11 - 2


# ── #14·#16 apm_metrics series domain·business ─────────────────────


async def test_domain_series(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[SERIES_DOMAIN] = {("bank.test", 4), ("common.test", 2)}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await tools.apm_metrics(
            mode="series", scope="domain", metrics=["service_count", "service_time"]
        )

    got = await _run(setup, call, SERIES_DOMAIN, tmp_path)
    out = _assert_same(got)
    assert out["partial"] is True
    assert len(_fails(out, "조회 실패")) == 2 * 2
    assert len(out["rows"]) == (12 - 2) * 2 * 3
    assert got[1]["audit"][2] == 2 + 24  # 지표 카탈로그(소스 2) + (도메인 12 × 지표 2)


async def test_business_series_all_failed(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[SERIES_BUSINESS] = {(h, d) for h in fake.hosts for d in fake.domains}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await tools.apm_metrics(
            mode="series", scope="business", business="pay", metrics=["sql_count"]
        )

    out = _assert_same(await _run(setup, call, SERIES_BUSINESS, tmp_path))
    assert out[0] == "error"


async def test_business_by_id(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[BUSINESS] = {("bank.test", 1)}  # 정의를 못 읽은 도메인 — 이름 없이 조회

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await tools.apm_metrics(
            mode="series",
            scope="business",
            business_id=11,
            domain_id=1,
            metrics=["sql_count", "concurrent_user"],
        )

    # 단위 2개(소스마다 1개)뿐 — 소스끼리 겹친다(소요 단축은 단언하지 않는다)
    out = _assert_same(await _run(setup, call, BUSINESS, tmp_path), faster=False)
    assert out["partial"] is True
    assert [r["business_name"] for r in out["rows"][::3]] == ["", "", "order-1", "order-1"]


# ── #21 apm_source_changes(바깥 묶음만 · 조각 직렬) ─────────────────


async def test_source_changes_outer_parallel_chunks_serial(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[DEPLOY] = {("bank.test", 2)}  # 첫 조각 실패 — 묶음 실패
        fake.deploy_ok = {("common.test", 4): 1}  # 둘째 조각부터 실패 — 받은 조각은 남긴다

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await tools.apm_source_changes(hostname=HOST, lookback_minutes=50 * 60)

    got = await _run(setup, call, DEPLOY, tmp_path)
    out = _assert_same(got)
    assert out["partial"] is True
    assert _fails(out, "변경 이력 조회 실패") == [
        "[한계] 변경 이력 조회 실패(소스 bank · 도메인 2 · 조각 1/2부터 — 남은 조각 생략):"
        " source_unavailable",
        "[한계] 변경 이력 조회 실패(소스 common · 도메인 4 · 조각 2/2부터 — 남은 조각 생략):"
        " source_unavailable",
    ]
    # 조각 실패 뒤 남은 조각은 부르지 않는다(묶음 안 직렬) — 2 × 12 − 1(bank 2의 둘째 조각)
    api_calls, _, expected = got[1]["audit"]
    assert (api_calls, expected) == (23, 24)
    assert len(out["rows"]) == (12 - 1) * 2 * 2 - 2


# ── #22 apm_transaction_trace ──────────────────────────────────


async def test_transaction_trace(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[TX_GUID] = {("common.test", 3), ("bank.test", 6)}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await tools.apm_transaction_trace(GUID, lookback_minutes=60)

    out = _assert_same(await _run(setup, call, TX_GUID, tmp_path))
    assert out["partial"] is True
    assert len(_fails(out, "GUID 거래 조회 실패")) == 2
    assert [r["trace_order"] for r in out["rows"]] == list(range(1, 11))


# ── #24 apm_period_compare ─────────────────────────────────────


async def test_period_compare(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[STATUS] = {("bank.test", 5), ("common.test", 2)}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await tools.apm_period_compare(
            hostname=HOST,
            current_start="2026-09-29T08:00:00+09:00",
            current_end="2026-09-29T10:00:00+09:00",
            baseline_start="2026-09-28T08:00:00+09:00",
            baseline_end="2026-09-28T10:00:00+09:00",
            full=True,
        )

    got = await _run(setup, call, STATUS, tmp_path)
    out = _assert_same(got)
    assert out["partial"] is True
    assert len(_fails(out, "구간 통계 조회 실패")) == 2 * 2 * 2
    assert got[1]["audit"][0] == got[1]["audit"][2] == 24 * 2
    total = next(x for x in out["limits"] if "현재 구간 전체 합계 계산 불가" in x)
    assert total.index("bank:500") < total.index("common:200")  # 입력(해소) 순서


# ── #27·#28 apm_fleet ─────────────────────────────────────────


async def test_period_ranking(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[STATUS] = {("bank.test", 3), ("common.test", 6)}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await FleetTools(tools).ranking(metric="calls", full=True, lookback_minutes=120)

    got = await _run(setup, call, STATUS, tmp_path)
    out = _assert_same(got)
    assert out["partial"] is True
    assert out["summary"]["domains_failed"] == 2
    assert got[1]["audit"][0] == 24


async def test_period_ranking_contract_violation_first_in_order(tmp_path):
    fake = FanFake(hosts=("bank.test",))
    tools = _tools(fake, tmp_path, 4)
    await tools.sources.get("default").resolver.inventory()
    fake.delay[STATUS] = 0.01
    fake.contract[STATUS] = {("bank.test", 3), ("bank.test", 4)}
    fake.domain_delay = {3: 0.05}
    with pytest.raises(ApmError) as e:
        await FleetTools(tools).ranking(metric="calls", full=True, lookback_minutes=120)
    assert e.value.code == CONTRACT_VIOLATION
    assert "(domain 3)" in e.value.reason


def _events(fake: FanFake) -> None:
    for host in fake.hosts:
        for d in fake.domains:
            for k in range(2):
                fake.add_event(host, event(d, d * 100 + k, NOW_MS - (d * 10 + k) * 1000))


async def test_fleet_events(tmp_path):
    def setup(fake: FanFake) -> None:
        _events(fake)
        fake.event_fail = {("common.test", 1), ("bank.test", 5)}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await FleetTools(tools).events(full=True)

    got = await _run(setup, call, EVENT, tmp_path)
    out = _assert_same(got)
    assert out["partial"] is True
    assert out["summary"]["domains_failed"] == 2
    assert len(out["rows"]) == (12 - 2) * 2
    assert got[1]["audit"] == (12, {"bank": 6, "common": 6}, 12)


async def test_fleet_events_contract_violation_first_in_order(tmp_path):
    fake = FanFake(hosts=("bank.test",))
    _events(fake)
    tools = _tools(fake, tmp_path, 4)
    await tools.sources.get("default").resolver.inventory()
    fake.delay[EVENT] = 0.01
    fake.contract[EVENT] = {("bank.test", 3), ("bank.test", 4)}
    fake.domain_delay = {3: 0.05}
    with pytest.raises(ApmError) as e:
        await FleetTools(tools).events(full=True)
    assert e.value.code == CONTRACT_VIOLATION
    assert "(domain 3)" in e.value.reason


# ── #31 apm_instance_map(business) 1단 업무 정의 ─────────────────────


async def test_instance_map_business_trace_defs(tmp_path):
    def setup(fake: FanFake) -> None:
        fake.fail[BUSINESS] = {("common.test", 2), ("bank.test", 5)}

    async def call(tools: ApmTools) -> dict[str, Any]:
        return await tools.apm_instance_map(business="pay-3")

    got = await _run(setup, call, BUSINESS, tmp_path)
    out = _assert_same(got)
    assert out["partial"] is True
    assert _fails(out, "업무 목록 조회 실패") == [
        "[한계] 업무 목록 조회 실패(소스 bank · 도메인 5): source_unavailable",
        "[한계] 업무 목록 조회 실패(소스 common · 도메인 2): source_unavailable",
    ]
