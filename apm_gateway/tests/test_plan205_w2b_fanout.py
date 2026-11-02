"""plans/205 W2b — 도구 안 독립 API 호출 동시 실행(`gather_ordered`)의 봉투 동일 · 소요 단축.

W0 분류표 b 행(#7·#8·#10~#14·#16·#21~#24·#26~#28·#31·#32)마다 같은 합성 응답으로
`APM_FANOUT_CONCURRENCY=4`(동시)와 `=1`(순차)을 돌려 봉투(행·`limits` 순서·`partial`·오류 봉투)와
호출 맥락 감사(호출 수·소스별 호출 수·예상 호출 수·메모·가린 칸)가 같은지 본다. 가짜 전송은 도착
순서마다 지연을 달리 줘(먼저 온 요청이 늦게 끝난다) 응답 도착 순서가 입력 순서와 어긋나게 한다 —
그 경로의 동시 수(peak)가 1보다 큰지도 함께 단언해 순서 보존을 실제로 시험했음을 고정한다.

합성 데이터(외부 네트워크 0 · `httpx.MockTransport`): 소스 s1~s4 × 도메인 1000·1100 × 인스턴스 2개
(id = 도메인+1·+2 · hostName 모두 `was-host01`). 응답 본문은 `conftest.synthetic_fixtures`의
Open API 5.6.4 필드명 모양을 도메인·인스턴스만 바꿔 쓴다.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpx
import pytest
from apm_gateway.application.fleet_tools import FleetTools
from apm_gateway.application.manage_tools import ManageTools
from apm_gateway.application.scope_tools import ScopeTools
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.call_context import CallScope, use_scope
from apm_gateway.domain.errors import ApmError
from conftest import NOW_MS, NOW_S, TOKEN, synthetic_fixtures

from apm_gateway.config import load_config

SIDS = ("s1", "s2", "s3", "s4")
DOMAINS = (1000, 1100)
HOST = "was-host01"
STEP = 0.003  # 도착 순서별 지연 단위(초)
GUID = "g-205"

DOWN = "down"  # 도메인 미접속 → source_unavailable
BOOM = "boom"  # 원천 거부 → apm_api_error(사유가 `[한계]`에 실린다)
CONTRACT = "contract"  # 요청 계약 위반 → contract_violation(고지로 강등하지 않는다)

Fail = Callable[[str, int | None, str, dict[str, str]], str | None]


def _iids(d: int) -> tuple[int, int]:
    return (d + 1, d + 2)


_BASE = {
    fx["request"]["template"]: fx["response"].get("body_json")
    for fx in synthetic_fixtures()
    if not fx["label"]
}


def _retag(body: Any, d: int, iid: int, sid: str) -> Any:
    """합성 본문의 도메인·인스턴스 칸을 요청 대상으로 바꾼다(소스별로 값이 드러나게 이름도)."""
    out = copy.deepcopy(body)
    for row in out["result"]:
        row["domainId"] = d
        if "instanceId" in row:
            row["instanceId"] = iid
        if "instanceName" in row:
            row["instanceName"] = f"{sid}-i{iid}"
        if "txid" in row:
            row["txid"] = f"{row['txid']}{sid[1:]}{d}"
    return out


def _series(sid: str, d: int, salt: int) -> dict:
    base = SIDS.index(sid) * 10 + d // 100 + salt
    return {
        "result": [
            {"time": str(NOW_MS - (6 - i) * 300_000), "value": base + i} for i in range(6)
        ]
    }


def _rt_domain(d: int, sid: str) -> dict:
    k = SIDS.index(sid) + 1
    return {
        "domainId": d,
        "domainName": f"dom{d}",
        "tps": 10.0 + k,
        "responseTime": 100.0 * k,
        "activeService": k,
        "activeUser": 2 * k,
        "concurrentUser": 3.5 * k,
        "rejectRate": 0.5 * k,
        "visitDay": 100 + k,
        "visitHour": 10 + k,
        "hitDay": 1000 + k,
        "hitHour": 50 + k,
        "activeServiceRangeCount0": k,
        "activeServiceRangeCount1": k + 1,
        "activeServiceRangeCount2": k + 2,
        "activeServiceRangeCount3": k + 3,
        "ipAddress": "192.0.2.10",
        "port": 5000 + k,
    }


def _biz(d: int, sid: str) -> list[dict]:
    return [
        {"businessId": 7, "name": "order", "description": "주문 처리", "ruleList": ["/o/*"]},
        {"businessId": 9, "name": f"pay-{sid}-{d}", "description": "결제", "ruleList": []},
    ]


def _rt_business(d: int, b: dict, sid: str) -> dict:
    k = SIDS.index(sid) + b["businessId"]
    return {
        "domainId": d,
        "businessId": b["businessId"],
        "businessName": b["name"],
        "tps": 1.0 + k,
        "responseTime": 20.0 * k,
        "activeService": k,
        "concurrentUser": 1.5 * k,
        "activeServiceRangeCount0": k,
        "activeServiceRangeCount1": k + 1,
        "activeServiceRangeCount2": k + 2,
        "activeServiceRangeCount3": k + 3,
    }


def _changes(sid: str, d: int, lo: int, hi: int) -> list[dict]:
    """변경 감지 — 인스턴스마다 최근 2건(1분 전·k분 전) + 30시간 전 1건 + 같은 건 중복 1건 ·
    s2 도메인 1100에는 감지 시각 없는 1건."""
    a, b = _iids(d)
    k = SIDS.index(sid) + 2 + (d - 1000) // 100
    rows = [
        {"collectTime": NOW_MS - 60_000, "instanceId": a},
        {"collectTime": NOW_MS - k * 60_000, "instanceId": b},
        {"collectTime": NOW_MS - k * 60_000, "instanceId": b},  # 원천 중복 — 한 번만
        {"collectTime": NOW_MS - 30 * 3_600_000, "instanceId": a},
    ]
    picked = [r for r in rows if lo <= r["collectTime"] <= hi]
    if (sid, d) == ("s2", 1100):  # 감지 시각 없는 변경(비교 못 함 — 호출 없이 `[한계]`)
        picked.append({"instanceId": a})
    return picked


def _guid_rows(sid: str, d: int) -> list[dict]:
    a, b = _iids(d)
    rows = []
    for iid, txid, start_s in ((a, 9100, 50), (a, 9100, 50), (b, 9200, 40)):
        rows.append(
            {
                "domainId": d,
                "domainName": f"dom{d}",
                "instanceId": iid,
                "instanceName": f"{sid}-i{iid}",
                "applicationName": f"/svc/{txid}?user=kim",
                "txid": str(txid),
                "guid": GUID,
                "responseTime": 1000,
                "startTime": str(NOW_MS - start_s * 1000),
                "endTime": str(NOW_MS - start_s * 1000 + 800),
                "clientIp": "192.168.10.77",
                "userId": "kimcs01",
                "clientId": "client-kim",
                "errorType": "",
            }
        )
    return rows


class Fake:
    """소스 4 × 도메인 2 합성 Open API. `fail`: (소스, 도메인, 경로, 쿼리) → 실패 종류 또는 None.

    도착 순서 n번째 요청은 `STEP × (4 − n mod 5)`초 늦게 답한다 — 동시 실행이면 먼저 나간 요청이
    늦게 끝난다. `peak[경로]` = 그 경로의 최대 동시 처리 수."""

    def __init__(self, fail: Iterable[Fail] = (), step: float = STEP) -> None:
        self.fail = list(fail)
        self.step = step
        self.n = 0
        self.inflight: Counter[str] = Counter()
        self.peak: Counter[str] = Counter()

    async def handler(self, request: httpx.Request) -> httpx.Response:
        sid = request.url.host.split(".")[0]
        path = request.url.path
        q = dict(request.url.params)
        d: int | None = int(q["domain_id"]) if "domain_id" in q else None
        if path.startswith("/api-v2/deploy/"):
            d = int(path.rsplit("/", 1)[1])
            path = "/api-v2/deploy"
        delay = self.step * (4 - self.n % 5)
        self.n += 1
        self.inflight[path] += 1
        self.peak[path] = max(self.peak[path], self.inflight[path])
        try:
            await asyncio.sleep(delay)
            for rule in self.fail:
                kind = rule(sid, d, path, q)
                if kind is not None:
                    return _error(kind, sid, d)
            return self._answer(sid, d, path, q)
        finally:
            self.inflight[path] -= 1

    def _answer(self, sid: str, d: int | None, path: str, q: dict[str, str]) -> httpx.Response:
        def ok(body: Any) -> httpx.Response:
            return httpx.Response(200, json=body)

        if path == "/api/domain":
            return ok({"result": [{"domainId": x, "name": f"dom{x}"} for x in DOMAINS]})
        assert d is not None or path == "/api/realtime/domain" or path == "/api/metrics", path
        if path == "/api/metrics":
            return ok(_BASE["/api/metrics"])
        if path == "/api/realtime/domain":
            return ok({"result": [_rt_domain(x, sid) for x in DOMAINS if d in (None, x)]})
        assert d is not None
        a, b = _iids(d)
        first = int(q["instance_id"].split(",")[0]) if "instance_id" in q else a
        if path == "/api/instance":
            return ok(
                {
                    "result": [
                        {
                            "instanceId": iid,
                            "name": f"{sid}-i{iid}",
                            "hostName": HOST,
                            "ipAddress": "10.0.0.11",
                            "platform": "JAVA",
                            "status": "RUNNING",
                            "version": "5.6.5",
                            "description": "",
                        }
                        for iid in (a, b)
                    ]
                }
            )
        if path == "/api/realtime/instance":
            k = SIDS.index(sid)
            return ok(
                {
                    "result": [
                        {
                            "domainId": d,
                            "instanceId": iid,
                            "instanceName": f"{sid}-i{iid}",
                            "responseTime": float(100 * (j + 1) + k),  # 소스마다 겹치지 않게
                            "tps": 10.0,
                            "visitDay": 5,
                            "instanceOid": 70_000 + iid,
                        }
                        for j, iid in enumerate((a, b))
                    ]
                }
            )
        if path == "/api/business":
            return ok({"result": _biz(d, sid)})
        if path == "/api/realtime/business":
            bid = int(q["business_id"]) if "business_id" in q else None
            rows = [_rt_business(d, x, sid) for x in _biz(d, sid)]
            return ok({"result": [r for r in rows if bid in (None, r["businessId"])]})
        if path == "/api/dbmetrics/instance":
            return ok(_series(sid, d, first + len(q.get("metrics", ""))))
        if path in ("/api/dbmetrics/domain", "/api/dbmetrics/business"):
            return ok(_series(sid, d, int(q.get("business_id", "0")) + len(q["metrics"])))
        if path.startswith("/api/status/"):
            return ok(_BASE[path])  # 묶음마다 같은 행 — 정렬 동점이 받은 순서를 드러낸다
        if path in ("/api/transaction/time", "/api/dbsearch/error", "/api/activeService/list"):
            return ok(_retag(_BASE[path], d, first, sid))
        if path == "/api/dbsearch/event":
            body = _retag(_BASE[path], d, a, sid)
            body["result"][0]["time"] = str(NOW_MS - 60_000 - SIDS.index(sid) * 1000)
            return ok(body)
        if path == "/api-v2/deploy":
            return ok(_changes(sid, d, int(q["startTime"]), int(q["endTime"])))
        if path == "/api/transaction/guid":
            return ok({"result": _guid_rows(sid, d)})
        return httpx.Response(404, text=f"no fixture {path}")


def _error(kind: str, sid: str, d: int | None) -> httpx.Response:
    message = {
        DOWN: "Domain is not connected",
        BOOM: f"boom {sid} {d}",
        CONTRACT: "Cannot parse null string",
    }[kind]
    return httpx.Response(500, json={"exception": {"message": message}})


def on(path: str, kind: str, *, sids: Iterable[str] = SIDS, domains: Iterable[int | None] = ()):
    """`path` 요청 중 소스 `sids`(· 도메인 `domains` — 비우면 전부)를 `kind`로 실패시킨다."""
    sid_set, dom_set = set(sids), set(domains)

    def rule(sid: str, d: int | None, p: str, _q: dict[str, str]) -> str | None:
        if p == path and sid in sid_set and (not dom_set or d in dom_set):
            return kind
        return None

    return rule


class Audit(CallScope):
    """호출 맥락 감사 — 소스별 호출 수 · 예상 호출 수 · 메모 · 가린 칸."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: Counter[str] = Counter()
        self.expected = 0
        self.notes: list[str] = []
        self.masked_fields: list[list[str]] = []

    def count_call(self, source_id: str = "", *, shared: bool = False) -> None:
        self.calls[source_id] += 1

    def expect_calls(self, count: int) -> None:
        self.expected += count

    def note(self, text: str) -> None:
        self.notes.append(text)

    def masked(self, fields: Iterable[str]) -> None:
        self.masked_fields.append(list(fields))


def _tools(fake: Fake, fanout: int) -> ApmTools:
    env = {
        "JENNIFER_SOURCES": json.dumps(list(SIDS)),
        "JENNIFER_RATE_LIMIT_PER_SEC": "0",
        "APM_PARSE_WORKERS": "0",
        "APM_FANOUT_CONCURRENCY": str(fanout),
    }
    for sid in SIDS:
        env[f"JENNIFER_{sid.upper()}_API_URL"] = f"http://{sid}.test"
        env[f"JENNIFER_{sid.upper()}_API_TOKEN"] = f"{TOKEN}-{sid}"
    cfg = load_config(env)
    assert cfg.perf.fanout_concurrency == fanout
    transport = httpx.MockTransport(fake.handler)
    return ApmTools(build_source_set(cfg, transport=transport), cfg, clock=lambda: NOW_S)


Call = Callable[[ApmTools], Awaitable[dict[str, Any]]]


async def _run(
    call: Call, fanout: int, fail: Iterable[Fail] = (), step: float = STEP
) -> tuple[Any, Audit, Fake, float]:
    fake = Fake(fail, step)
    tools = _tools(fake, fanout)
    audit = Audit()
    started = time.perf_counter()
    with use_scope(audit):
        try:
            out: Any = await call(tools)
        except ApmError as e:
            out = {"error": e.code, "reason": e.reason}
    return out, audit, fake, time.perf_counter() - started


# ── 경로별 호출(도구 진입점) ───────────────────────────────────


def stats(**kw: Any) -> Call:
    return lambda t: t.apm_status_stats(kind="application", hostname=HOST, **kw)


def inst_series(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return t.apm_metrics(
        mode="series", scope="instance", hostname=HOST, metrics=["heap_used", "gc_time_usage"]
    )


def service(**kw: Any) -> Call:
    return lambda t: ScopeTools(t).apm_service_status(**kw)


def business(**kw: Any) -> Call:
    return lambda t: ScopeTools(t).apm_business(**kw)


def scope_series(**kw: Any) -> Call:
    return lambda t: t.apm_metrics(mode="series", **kw)


def changes(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return t.apm_source_changes(hostname=HOST, lookback_minutes=3 * 24 * 60)


def trace(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return t.apm_transaction_trace(guid=GUID)


def impact(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return t.apm_change_impact(hostname=HOST, lookback_minutes=60, width_minutes=2, full=True)


def period(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return t.apm_period_compare(
        hostname=HOST,
        current_start="2026-09-29T08:00:00+09:00",
        current_end="2026-09-29T10:00:00+09:00",
        baseline_start="2026-09-28T08:00:00+09:00",
        baseline_end="2026-09-28T09:30:00+09:00",
    )


def fleet_now(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return FleetTools(t).ranking(full=True)


def fleet_period(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return FleetTools(t).ranking(metric="calls", lookback_minutes=120, full=True)


def fleet_events(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return FleetTools(t).events(lookback_minutes=30, full=True)


def instance_business(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return t.apm_instance_map(business="order")


def _sorted_refused(sid: str, d: int | None, p: str, q: dict[str, str]) -> str | None:
    """정렬 기준을 s2·s3이 거부한다(조건 없는 재조회는 성공)."""
    if p == "/api/status/application" and "sort_by_metrics" in q and sid in ("s2", "s3"):
        return BOOM
    return None


def _second_chunk(path: str, sids: Iterable[str]) -> Fail:
    """변경 이력의 두 번째 25시간 조각부터 실패(받은 첫 조각의 행은 남는다)."""
    chosen = set(sids)

    def rule(sid: str, d: int | None, p: str, q: dict[str, str]) -> str | None:
        first_lo = NOW_MS - 3 * 24 * 3_600_000
        if p == path and sid in chosen and int(q["startTime"]) > first_lo:
            return BOOM
        return None

    return rule


def _late_xview(sids: Iterable[str]) -> Fail:
    """업무 역추적 트랜잭션의 두 번째 1분 조각부터 실패(조각 while 안 `break`)."""
    chosen = set(sids)

    def rule(sid: str, d: int | None, p: str, q: dict[str, str]) -> str | None:
        if p == "/api/transaction/time" and sid in chosen:
            if int(q["start_time"]) > NOW_MS - 4 * 60_000:
                return BOOM
        return None

    return rule


# (이름, 호출, 실패 규칙, 동시 확인 경로, 감사 호출 수 비교 여부)
CASES: list[tuple[str, Call, list[Fail], str, bool]] = [
    # #7 _status_fetch
    ("status-ok", stats(), [], "/api/status/application", True),
    ("status-sort", stats(sort_by="calls"), [], "/api/status/application", True),
    ("status-refused", stats(sort_by="calls"), [_sorted_refused], "/api/status/application", True),
    (
        "status-partial",
        stats(),
        [on("/api/status/application", BOOM, sids=("s2",)),
         on("/api/status/application", DOWN, sids=("s4",), domains=(1000,))],
        "/api/status/application",
        True,
    ),
    ("status-all", stats(), [on("/api/status/application", BOOM)], "/api/status/application", True),
    # #8 인스턴스 × 지표
    ("inst-series-ok", inst_series, [], "/api/dbmetrics/instance", True),
    (
        "inst-series-partial",
        inst_series,
        [on("/api/dbmetrics/instance", BOOM, sids=("s1", "s3"), domains=(1100,))],
        "/api/dbmetrics/instance",
        True,
    ),
    (
        "inst-series-all",
        inst_series,
        [on("/api/dbmetrics/instance", DOWN)],
        "/api/dbmetrics/instance",
        True,
    ),
    # #10 apm_service_status
    ("service-all-domains", service(), [], "/api/realtime/domain", True),
    ("service-narrowed", service(domain_id=1000), [], "/api/realtime/domain", True),
    (
        "service-partial",
        service(),
        [on("/api/realtime/domain", BOOM, sids=("s2",)),
         on("/api/realtime/domain", DOWN, sids=("s3",))],
        "/api/realtime/domain",
        True,
    ),
    ("service-all", service(), [on("/api/realtime/domain", BOOM)], "/api/realtime/domain", True),
    # #11 _business_defs(list) · #13 _current_named
    ("business-list", business(mode="list"), [], "/api/business", True),
    (
        "business-list-partial",
        business(mode="list"),
        [on("/api/business", BOOM, sids=("s1",), domains=(1100,)),
         on("/api/business", DOWN, sids=("s4",))],
        "/api/business",
        True,
    ),
    ("business-list-all", business(mode="list"), [on("/api/business", BOOM)], "/api/business",
     True),
    ("business-list-contract", business(mode="list"),
     [on("/api/business", CONTRACT, sids=("s3",), domains=(1000,))], "/api/business", False),
    # #12 _current_all
    ("business-current", business(), [], "/api/realtime/business", True),
    (
        "business-current-partial",
        business(),
        [on("/api/realtime/business", BOOM, sids=("s2", "s4"), domains=(1000,))],
        "/api/realtime/business",
        True,
    ),
    ("business-current-all", business(), [on("/api/realtime/business", DOWN)],
     "/api/realtime/business", True),
    # #13 _current_named
    ("business-named", business(business="order"), [], "/api/realtime/business", True),
    (
        "business-named-partial",
        business(business="order"),
        [on("/api/realtime/business", BOOM, sids=("s3",)),
         on("/api/business", BOOM, sids=("s1",), domains=(1000,))],
        "/api/realtime/business",
        True,
    ),
    ("business-named-all", business(business="order"), [on("/api/realtime/business", BOOM)],
     "/api/realtime/business", True),
    # #14 도메인·업무 시계열
    ("domain-series", scope_series(scope="domain"), [], "/api/dbmetrics/domain", True),
    (
        "domain-series-partial",
        scope_series(scope="domain", metrics=["service_count", "service_time"]),
        [on("/api/dbmetrics/domain", BOOM, sids=("s2",), domains=(1100,))],
        "/api/dbmetrics/domain",
        True,
    ),
    ("domain-series-all", scope_series(scope="domain"), [on("/api/dbmetrics/domain", DOWN)],
     "/api/dbmetrics/domain", True),
    ("business-series", scope_series(scope="business", business="order"), [],
     "/api/dbmetrics/business", True),
    (
        "business-series-partial",
        scope_series(scope="business", business="order"),
        [on("/api/dbmetrics/business", BOOM, sids=("s4",))],
        "/api/dbmetrics/business",
        True,
    ),
    # #16 _business_by_id
    ("business-by-id", scope_series(scope="business", business_id=7, domain_id=1000), [],
     "/api/business", True),
    (
        "business-by-id-partial",
        scope_series(scope="business", business_id=9, domain_id=1100),
        [on("/api/business", BOOM, sids=("s2",))],
        "/api/business",
        True,
    ),
    # #21 _change_rows(바깥 묶음만 동시 · 조각 while·break 유지)
    ("changes", changes, [], "/api-v2/deploy", True),
    (
        "changes-partial",
        changes,
        [_second_chunk("/api-v2/deploy", ("s2", "s3")),
         on("/api-v2/deploy", BOOM, sids=("s4",), domains=(1000,))],
        "/api-v2/deploy",
        True,
    ),
    ("changes-all", changes, [on("/api-v2/deploy", DOWN)], "/api-v2/deploy", True),
    # #22 apm_transaction_trace
    ("trace", trace, [], "/api/transaction/guid", True),
    (
        "trace-partial",
        trace,
        [on("/api/transaction/guid", BOOM, sids=("s1", "s4"), domains=(1100,))],
        "/api/transaction/guid",
        True,
    ),
    ("trace-all", trace, [on("/api/transaction/guid", BOOM)], "/api/transaction/guid", True),
    # #23 apm_change_impact(변경 건끼리 동시)
    ("impact", impact, [], "/api/transaction/time", True),
    (
        "impact-partial",
        impact,
        [on("/api/transaction/time", BOOM, sids=("s2",)),
         on("/api/dbsearch/error", DOWN, sids=("s3",))],
        "/api/transaction/time",
        True,
    ),
    (
        "impact-all",
        impact,
        [on("/api/transaction/time", BOOM), on("/api/dbsearch/error", BOOM)],
        "/api/transaction/time",
        True,
    ),
    ("impact-contract", impact, [on("/api/dbsearch/error", CONTRACT, sids=("s3",))],
     "/api/transaction/time", False),
    # #24 apm_period_compare
    ("period", period, [], "/api/status/application", True),
    (
        "period-partial",
        period,
        [on("/api/status/application", BOOM, sids=("s3",), domains=(1000,))],
        "/api/status/application",
        True,
    ),
    ("period-all", period, [on("/api/status/application", DOWN)], "/api/status/application",
     True),
    # #26 _realtime_records
    ("fleet-now", fleet_now, [], "/api/realtime/instance", True),
    (
        "fleet-now-partial",
        fleet_now,
        [on("/api/realtime/instance", DOWN, sids=("s2",), domains=(1100,)),
         on("/api/realtime/instance", BOOM, sids=("s4",))],
        "/api/realtime/instance",
        True,
    ),
    ("fleet-now-all", fleet_now, [on("/api/realtime/instance", BOOM)], "/api/realtime/instance",
     True),
    # CONTRACT_VIOLATION — 순차는 그 자리 뒤 묶음을 부르지 않지만 동시는 이미 나간 호출이 있어
    # 감사 호출 수가 클 수 있다(계획서 §5 「오류 경로 호출 수 차이(허용 · 봉투 불변)」)
    ("fleet-now-contract", fleet_now,
     [on("/api/realtime/instance", CONTRACT, sids=("s2",), domains=(1000,))],
     "/api/realtime/instance", False),
    # #27 _period_records(도메인 × 인스턴스)
    ("fleet-period", fleet_period, [], "/api/status/application", True),
    (
        "fleet-period-partial",
        fleet_period,
        [on("/api/status/application", BOOM, sids=("s1",), domains=(1000,)),
         on("/api/status/application", DOWN, sids=("s3",))],
        "/api/status/application",
        True,
    ),
    ("fleet-period-all", fleet_period, [on("/api/status/application", BOOM)],
     "/api/status/application", True),
    ("fleet-period-contract", fleet_period,
     [on("/api/status/application", CONTRACT, sids=("s2",), domains=(1100,))],
     "/api/status/application", False),
    # #28 events
    ("fleet-events", fleet_events, [], "/api/dbsearch/event", True),
    (
        "fleet-events-partial",
        fleet_events,
        [on("/api/dbsearch/event", BOOM, sids=("s2", "s3"), domains=(1000,))],
        "/api/dbsearch/event",
        True,
    ),
    ("fleet-events-all", fleet_events, [on("/api/dbsearch/event", DOWN)], "/api/dbsearch/event",
     True),
    ("fleet-events-contract", fleet_events,
     [on("/api/dbsearch/event", CONTRACT, sids=("s1",), domains=(1100,))],
     "/api/dbsearch/event", False),
    # #31 _business_trace 1단(업무 정의) · #32 2단(역추적 — 안쪽 1분 조각 while 순차)
    ("instance-business", instance_business, [], "/api/business", True),
    ("instance-business-seen", instance_business, [], "/api/activeService/list", True),
    (
        "instance-business-partial",
        instance_business,
        [on("/api/business", BOOM, sids=("s2",), domains=(1100,)),
         on("/api/activeService/list", DOWN, sids=("s4",), domains=(1000,)),
         _late_xview(("s1", "s3"))],
        "/api/activeService/list",
        True,
    ),
    ("instance-business-defs-all", instance_business, [on("/api/business", BOOM)],
     "/api/business", True),
]


@pytest.mark.parametrize(
    ("call", "fail", "path", "compare_calls"),
    [pytest.param(c, f, p, k, id=name) for name, c, f, p, k in CASES],
)
async def test_concurrent_envelope_equals_sequential(
    call: Call, fail: list[Fail], path: str, compare_calls: bool
) -> None:
    seq, seq_audit, seq_fake, _ = await _run(call, 1, fail)
    par, par_audit, par_fake, _ = await _run(call, 4, fail)
    assert par == seq
    assert par_audit.notes == seq_audit.notes
    assert par_audit.masked_fields == seq_audit.masked_fields
    if compare_calls:
        assert par_audit.expected == seq_audit.expected
        assert par_audit.calls == seq_audit.calls
        assert sum(par_audit.calls.values()) == sum(seq_audit.calls.values())
    else:  # 계약 위반 — 봉투(오류)는 같고 호출 수·단위 안에서 신고한 예상 호출 수는 순차 이상
        assert par.get("error") == "contract_violation", par
        assert par_audit.expected >= seq_audit.expected
        assert sum(par_audit.calls.values()) >= sum(seq_audit.calls.values())
    # 그 경로가 실제로 동시에 나갔고(순서 보존을 시험했다) 순차는 1개씩이었다
    assert seq_fake.peak[path] == 1
    assert par_fake.peak[path] > 1


def test_cases_cover_every_w2b_path_with_partial_and_all_failure() -> None:
    names = {name for name, *_ in CASES}
    for stem in (
        "status", "inst-series", "service", "business-list", "business-current",
        "business-named", "domain-series", "changes", "trace", "impact", "period",
        "fleet-now", "fleet-period", "fleet-events",
    ):
        assert {f"{stem}-partial", f"{stem}-all"} <= names, stem


async def test_partial_envelopes_keep_input_order_of_failures() -> None:
    """일부 실패 고지가 입력(소스) 순서다 — 도착 순서와 무관(대표: 서비스 · 인스턴스 시계열)."""
    out, *_ = await _run(
        service(),
        4,
        [on("/api/realtime/domain", BOOM, sids=("s3",)),
         on("/api/realtime/domain", BOOM, sids=("s1",))],
    )
    fails = [x for x in out["limits"] if "조회 실패" in x]
    assert len(fails) == 2 and "s1" in fails[0] and "s3" in fails[1], fails
    assert out["partial"] is True


async def test_status_sort_ties_keep_received_order() -> None:
    """같은 값 행의 정렬(안정 정렬)이 묶음 입력 순서를 따른다 — 받은 순서가 아니라."""
    out, *_ = await _run(stats(sort_by="calls", full=True), 4)
    keys = [(r["source_id"], r["domain_id"]) for r in out["rows"]]
    firsts = list(dict.fromkeys(keys))
    assert firsts == [(s, d) for s in SIDS for d in DOMAINS], firsts


# ── 순차(=1) 계약 위반 — 그 단위 뒤로 호출 0(종전 루프와 호출 수·봉투 같음) ─────────────


def _log(seen: list[tuple[str, int | None, str]]) -> Fail:
    """실패 규칙 앞에 두는 기록기 — 요청을 응답 순서대로 적는다(순차면 호출 순서)."""

    def rule(sid: str, d: int | None, p: str, _q: dict[str, str]) -> str | None:
        seen.append((sid, d, p))
        return None

    return rule


def _from(path: str, sid: str, d: int, at: int, key: str = "start_time") -> Fail:
    """`path`의 (sid, d) 요청 중 시작 시각 `at` 이상 조각부터 계약 위반 — 묶음 안 조각 중간."""

    def rule(s: str, dd: int | None, p: str, q: dict[str, str]) -> str | None:
        return CONTRACT if p == path and (s, dd) == (sid, d) and int(q[key]) >= at else None

    return rule


def _env_contract(sid: str, d: int | None, p: str, _q: dict[str, str]) -> str | None:
    """환경 변수 — s1은 원천 거부(고지 · 계속), s2는 계약 위반(멈춤)."""
    if not p.startswith("/api-v2/environment-variable/"):
        return None
    return CONTRACT if sid == "s2" else BOOM


def _app_health(minutes: int) -> Call:
    return lambda t: t.apm_app_health(hostname=HOST, lookback_minutes=minutes)


def _runtime(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return t.apm_runtime_health(hostname=HOST, lookback_minutes=30)


CV_CASES: list[tuple[str, Call, list[Fail], str, tuple[str, int | None] | None]] = [
    # (이름, 호출, 실패, 계약 위반 경로, 그 (소스, 도메인) — None이면 경로만 본다)
    ("realtime", _app_health(11), [on("/api/realtime/instance", CONTRACT, sids=("s2",),
                                      domains=(1000,))], "/api/realtime/instance", ("s2", 1000)),
    ("xview", _app_health(11), [on("/api/transaction/time", CONTRACT, sids=("s2",),
                                   domains=(1100,))], "/api/transaction/time", ("s2", 1100)),
    ("xview-mid-chunk", _app_health(11),
     [_from("/api/transaction/time", "s2", 1000, NOW_MS - 5 * 60_000)],
     "/api/transaction/time", ("s2", 1000)),
    ("hourly", _app_health(180), [on("/api/status/application", CONTRACT, sids=("s3",),
                                     domains=(1000,))], "/api/status/application", ("s3", 1000)),
    ("runtime-series", _runtime, [on("/api/dbmetrics/instance", CONTRACT, sids=("s2",),
                                     domains=(1100,))], "/api/dbmetrics/instance", ("s2", 1100)),
    ("pool-active", lambda t: t.apm_resource_pool(hostname=HOST),
     [on("/api/activeService/list", CONTRACT, sids=("s2",), domains=(1000,))],
     "/api/activeService/list", ("s2", 1000)),
    ("active", lambda t: t.apm_active_services(hostname=HOST, full=True),
     [on("/api/activeService/list", CONTRACT, sids=("s3",), domains=(1100,))],
     "/api/activeService/list", ("s3", 1100)),
    ("events-event", lambda t: t.apm_events(hostname=HOST, record="error", full=True),
     [on("/api/dbsearch/event", CONTRACT, sids=("s2",), domains=(1000,))],
     "/api/dbsearch/event", ("s2", 1000)),
    ("events-error", lambda t: t.apm_events(hostname=HOST, record="error", full=True),
     [on("/api/dbsearch/error", CONTRACT, sids=("s2",), domains=(1100,))],
     "/api/dbsearch/error", ("s2", 1100)),
    ("manage-run", lambda t: ManageTools(t).apm_environment(hostname=HOST), [_env_contract],
     "/api-v2/environment-variable/1000", None),
    ("status", stats(), [on("/api/status/application", CONTRACT, sids=("s2",), domains=(1000,))],
     "/api/status/application", ("s2", 1000)),
    ("inst-series", inst_series, [on("/api/dbmetrics/instance", CONTRACT, sids=("s2",),
                                     domains=(1000,))], "/api/dbmetrics/instance", ("s2", 1000)),
    ("service", service(), [on("/api/realtime/domain", CONTRACT, sids=("s2",))],
     "/api/realtime/domain", ("s2", None)),
    ("business-list", business(mode="list"), [on("/api/business", CONTRACT, sids=("s3",),
                                                 domains=(1000,))], "/api/business", ("s3", 1000)),
    ("business-current", business(), [on("/api/realtime/business", CONTRACT, sids=("s2",),
                                         domains=(1000,))], "/api/realtime/business", ("s2", 1000)),
    ("domain-series", scope_series(scope="domain", metrics=["tps"]),
     [on("/api/dbmetrics/domain", CONTRACT, sids=("s2",), domains=(1000,))],
     "/api/dbmetrics/domain", ("s2", 1000)),
    ("changes", changes, [on("/api-v2/deploy", CONTRACT, sids=("s2",), domains=(1000,))],
     "/api-v2/deploy", ("s2", 1000)),
    ("changes-mid-chunk", changes,
     [_from("/api-v2/deploy", "s2", 1000, NOW_MS - 30 * 3_600_000, key="startTime")],
     "/api-v2/deploy", ("s2", 1000)),
    ("trace", trace, [on("/api/transaction/guid", CONTRACT, sids=("s2",), domains=(1000,))],
     "/api/transaction/guid", ("s2", 1000)),
    ("impact", impact, [on("/api/dbsearch/error", CONTRACT, sids=("s3",), domains=(1000,))],
     "/api/dbsearch/error", ("s3", 1000)),
    ("period", period, [on("/api/status/application", CONTRACT, sids=("s2",), domains=(1000,))],
     "/api/status/application", ("s2", 1000)),
    ("fleet-now", fleet_now, [on("/api/realtime/instance", CONTRACT, sids=("s2",),
                                 domains=(1000,))], "/api/realtime/instance", ("s2", 1000)),
    ("fleet-period", fleet_period, [on("/api/status/application", CONTRACT, sids=("s2",),
                                       domains=(1100,))], "/api/status/application", ("s2", 1100)),
    ("fleet-events", fleet_events, [on("/api/dbsearch/event", CONTRACT, sids=("s1",),
                                       domains=(1100,))], "/api/dbsearch/event", ("s1", 1100)),
    ("instance-business", instance_business,
     [on("/api/activeService/list", CONTRACT, sids=("s2",), domains=(1000,))],
     "/api/activeService/list", ("s2", 1000)),
]


@pytest.mark.parametrize(
    ("call", "fail", "path", "at"), [pytest.param(c, f, p, a, id=n) for n, c, f, p, a in CV_CASES]
)
async def test_sequential_contract_violation_stops_at_that_unit(
    call: Call, fail: list[Fail], path: str, at: tuple[str, int | None] | None
) -> None:
    """`APM_FANOUT_CONCURRENCY=1`에서 계약 위반을 받은 호출이 마지막 호출이다 — 종전 루프가 그
    자리에서 던져 뒤 단위를 부르지 않던 것과 같다(기준선 16ea44fa 대조: 봉투·소스별 호출 수
    동일)."""
    seen: list[tuple[str, int | None, str]] = []
    out, audit, *_ = await _run(call, 1, [_log(seen), *fail], step=0.0)
    assert out.get("error") == "contract_violation", out
    sid, d, last = seen[-1]
    assert last == path, seen[-3:]
    if at is not None:
        assert (sid, d) == at, seen[-3:]
    assert sum(audit.calls.values()) == len(seen)


async def test_sequential_resolver_keeps_loading_after_contract_violation() -> None:
    """인스턴스 명단 적재(#1)는 종전에도 계약 위반을 「조회 불가 도메인」으로 적고 다음 도메인을
    불렀다 — 차례 경로도 멈추지 않는다(`stop_on_contract_violation=False`)."""
    seen: list[tuple[str, int | None, str]] = []
    out, *_ = await _run(
        _runtime, 1, [_log(seen), on("/api/instance", CONTRACT, sids=("s1",), domains=(1000,))],
        step=0.0,
    )
    assert "error" not in out, out
    assert ("s1", 1100, "/api/instance") in seen
    assert seen.index(("s1", 1100, "/api/instance")) > seen.index(("s1", 1000, "/api/instance"))


# ── 소요 단축(느슨) ────────────────────────────────────────────


@pytest.mark.parametrize(
    "call", [pytest.param(fleet_period, id="fleet-period"), pytest.param(inst_series, id="series"),
             pytest.param(changes, id="changes")]
)
async def test_concurrency_shortens_elapsed(call: Call) -> None:
    step = 0.01
    _, _, _, seq_s = await _run(call, 1, step=step)
    _, _, _, par_s = await _run(call, 4, step=step)
    assert par_s < seq_s * 0.75, (seq_s, par_s)
