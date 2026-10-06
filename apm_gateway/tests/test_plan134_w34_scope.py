"""plans/134 W3·W4 — 서비스(도메인)·업무 조회(`apm_service_status`·`apm_business`) · 도메인·업무
시계열(`apm_metrics(series, scope domain·business)`) · 허용목록 +4.

합성 데이터(가짜 Open API · `httpx.MockTransport`):
- 소스 bank(하나만 쓰면 소스 id `default`): 도메인 1000 order-svc · 1130 settle · 1200
  order_batch. 업무 — 1000: 7 order · 9 pay-card · 10 pay-bank · 11 loan · 1130: 21 settle-night ·
  1200: 없음.
- 소스 common: 도메인 1000 order-svc(bank와 같은 id·이름) · 업무 1000: 7 order.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from apm_gateway.adapters.jennifer import fields as jf
from apm_gateway.adapters.jennifer.allowlist import ALLOWED, NotAllowedError, check_request
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.domain.call_context import CallScope, use_scope
from apm_gateway.domain.errors import INVALID_ARGUMENT, ApmError
from conftest import NOW_MS, NOW_S, TOKEN, recorded_body

from apm_gateway.config import JenniferApiConfig, load_config

BANK_URL = "http://bank.test"
COMMON_URL = "http://common.test"
EMAIL = "kim@example.com"  # 가림 확인용 카나리아


def _biz(bid: int, name: str, desc: str = "", rules: tuple[str, ...] = ()) -> dict:
    return {
        "businessId": bid,
        "name": name,
        "description": desc,
        "businessIndex": f"idx-{bid}",
        "businessOid": 70_000 + bid,
        "badResponseTime": 3000 + bid,
        "ruleList": list(rules),
    }


DATA = {
    "bank.test": {
        "domains": {1000: "order-svc", 1130: "settle", 1200: "order_batch"},
        "business": {
            1000: [
                _biz(7, "order", f"주문 처리 담당 {EMAIL}", ("/order/list?user=kim",)),
                _biz(9, "pay-card", "카드 결제"),
                _biz(10, "pay-bank", "계좌 결제"),
                _biz(11, "loan", "여신"),
            ],
            1130: [_biz(21, "settle-night", "야간 정산")],
            1200: [],
        },
    },
    "common.test": {
        "domains": {1000: "order-svc"},
        "business": {1000: [_biz(7, "order", "공동 주문")]},
    },
}


def _rt_domain(did: int, name: str, k: int) -> dict:
    """`RealtimeDomainData` 18필드 전부."""
    return {
        "domainId": did,
        "domainName": name,
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


def _rt_business(did: int, b: dict, k: int) -> dict:
    """`RealtimeBusinessData` 11필드 전부."""
    return {
        "domainId": did,
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


def _series(values: list[float]) -> dict:
    return {
        "result": [
            {"time": str(NOW_MS - (len(values) - i) * 300_000), "value": v}
            for i, v in enumerate(values)
        ]
    }


def _handler(calls: list[httpx.Request], fail: dict | None = None):
    """호스트별 합성 응답. `fail`: 경로 → 실패시킬 (호스트, 도메인) 집합(도메인 None = 그 호스트
    전부 · 빈 집합 = 전부) — 500 응답."""

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        host, path, q = request.url.host, request.url.path, request.url.params
        data = DATA[host]
        did = int(q["domain_id"]) if "domain_id" in q else None
        wanted = (fail or {}).get(path)
        if wanted is not None and (not wanted or (host, did) in wanted or (host, None) in wanted):
            return httpx.Response(500, json={"exception": {"message": "boom"}})
        if path == "/api/domain":
            rows = [{"domainId": d, "name": n} for d, n in data["domains"].items()]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/instance":
            return httpx.Response(200, json={"result": []})
        if path == "/api/metrics":
            return httpx.Response(200, json=recorded_body("GET_api_metrics__ok.json"))
        if path == "/api/business":
            return httpx.Response(200, json={"result": data["business"].get(did, [])})
        if path == "/api/realtime/domain":
            rows = [
                _rt_domain(d, n, k)
                for k, (d, n) in enumerate(data["domains"].items(), start=1)
                if did is None or d == did
            ]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/realtime/business":
            bid = int(q["business_id"]) if "business_id" in q else None
            rows = [
                _rt_business(did, b, k)
                for k, b in enumerate(data["business"].get(did, []), start=1)
                if bid is None or b["businessId"] == bid
            ]
            return httpx.Response(200, json={"result": rows})
        if path in ("/api/dbmetrics/domain", "/api/dbmetrics/business"):
            return httpx.Response(200, json=_series([1.0, 2.0, 3.0]))
        return httpx.Response(404, text="no fixture")

    return handler


def _tools(tmp_path, calls, *, two: bool = False, fail: dict | None = None, extra=None):
    from apm_gateway.application.sources import build_source_set
    from apm_gateway.application.tools import ApmTools

    if two:
        env = {
            "JENNIFER_SOURCES": '["bank", "common"]',
            "JENNIFER_BANK_API_URL": BANK_URL,
            "JENNIFER_BANK_API_TOKEN": TOKEN,
            "JENNIFER_COMMON_API_URL": COMMON_URL,
            "JENNIFER_COMMON_API_TOKEN": "tok-OTHER-77c1",
        }
    else:
        env = {"JENNIFER_API_URL": BANK_URL, "JENNIFER_API_TOKEN": TOKEN}
    env.update(
        {
            "JENNIFER_RATE_LIMIT_PER_SEC": "0",
            "APM_SPOOL_DIR": str(tmp_path / "spool"),
            **(extra or {}),
        }
    )
    cfg = load_config(env)
    transport = httpx.MockTransport(_handler(calls, fail))
    return ApmTools(build_source_set(cfg, transport=transport), cfg, clock=lambda: NOW_S)


def _scope(tools):
    from apm_gateway.application.scope_tools import ScopeTools

    return ScopeTools(tools)


def _hits(calls: list[httpx.Request], path: str) -> list[tuple[str, dict]]:
    return [(r.url.host, dict(r.url.params)) for r in calls if r.url.path == path]


def _unresolved(out: dict) -> list[str]:
    return list(out.get("_unresolved") or [])


class _Plan(CallScope):
    """호출 계획 신고(`expect_calls`)를 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self.planned = 0

    def expect_calls(self, count: int) -> None:
        self.planned += count


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


# ── 허용목록 +4 (A-1) ─────────────────────────────────────────

NEW_TEMPLATES = [
    "/api/realtime/domain",
    "/api/dbmetrics/domain",
    "/api/realtime/business",
    "/api/dbmetrics/business",
]
_RANGE = {"interval_minute": "5", "metrics": "service_count", "start_time": "1", "end_time": "2"}
GOOD = {
    "/api/realtime/domain": ({}, ("domain_id",), ()),
    "/api/dbmetrics/domain": (
        {"domain_id": "1000", **_RANGE},
        (),
        ("domain_id", "interval_minute", "metrics", "start_time", "end_time"),
    ),
    "/api/realtime/business": ({"domain_id": "1000"}, ("business_id",), ("domain_id",)),
    "/api/dbmetrics/business": (
        {"domain_id": "1000", "business_id": "7", **_RANGE},
        (),
        ("domain_id", "business_id", "interval_minute", "metrics", "start_time", "end_time"),
    ),
}


def test_allowlist_has_41_templates_new_four_after_business_and_copy_in_order():
    from jennifer_catalog import ALLOWED as COPY

    order = list(ALLOWED)
    assert len(order) == 41 and order == list(COPY)
    at = order.index("/api/business")
    assert order[at + 1 : at + 5] == NEW_TEMPLATES
    for t in NEW_TEMPLATES:
        ep, cp = ALLOWED[t], COPY[t]
        assert (ep.required, ep.optional, ep.path_vars) == (
            tuple(cp.required),
            tuple(cp.optional),
            tuple(cp.path_vars),
        )


@pytest.mark.parametrize("template", NEW_TEMPLATES)
def test_new_paths_allow_declared_keys_only(template):
    params, optional, required = GOOD[template]
    ep = check_request("GET", template, params)
    assert ep.optional == optional and ep.required == required
    full = {**params, **{k: "1" for k in optional}}
    assert check_request("GET", template, full).template == template
    for extra in ({"time_pattern": "YYYYMMdd"}, {"token": "x"}, {"TOKEN": "x"}, {"other": "1"}):
        with pytest.raises(NotAllowedError):
            check_request("GET", template, {**full, **extra})
    for missing in required:
        with pytest.raises(NotAllowedError, match=f"필수 쿼리 키 누락: {missing}"):
            check_request("GET", template, {k: v for k, v in full.items() if k != missing})
    for method in ("POST", "PUT", "DELETE"):
        with pytest.raises(NotAllowedError):
            check_request(method, template, full)
    for variant in (template + ".xml", template + "/", template.upper()):
        with pytest.raises(NotAllowedError):
            check_request("GET", variant, full)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/realtime/domain", {"time_pattern": "YYYYMMddHH"}),
        ("/api/realtime/domain", {"token": "x"}),
        ("/api/realtime/domain.xml", {}),
        ("/api/realtime/domain/", {}),
        ("/api/dbmetrics/domain", {"domain_id": 1000, **_RANGE, "time_pattern": "YYYYMMdd"}),
        ("/api/realtime/business", {}),
        ("/api/realtime/business.xml", {"domain_id": 1000}),
        ("/api/dbmetrics/business", {"domain_id": 1000, **_RANGE}),
    ],
)
async def test_rejected_requests_make_no_http_call(path, params):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"result": []})

    client = JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t0k", rate_limit_per_sec=0),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(NotAllowedError):
        await client.get_json(path, params)
    assert requests == [] and client.calls_total == 0
    await client.aclose()


# ── 파서 (COV 중립 이름) ─────────────────────────────────────


def test_parse_realtime_domain_all_18_fields():
    out = jf.parse_realtime_domain(_rt_domain(1000, "order-svc", 2))
    assert out == {
        "domain_id": 1000,
        "domain_name": "order-svc",
        "tps": 12.0,
        "response_time_avg_ms": 200.0,
        "active_services": 2,
        "active_users": 4,
        "concurrent_users": 7.0,
        "reject_rate": 1.0,
        "visit_day": 102,
        "visit_hour": 12,
        "hit_day": 1002,
        "hit_hour": 52,
        "active_range_count_0": 2,
        "active_range_count_1": 3,
        "active_range_count_2": 4,
        "active_range_count_3": 5,
        "data_server_ip": "192.0.2.10",
        "data_server_port": 5002,
    }
    assert len(jf.REALTIME_DOMAIN_FIELDS) == 18
    broken = jf.parse_realtime_domain({"domainId": "x", "tps": "NaN?", "port": "²"})
    assert (
        broken["domain_id"] is None and broken["tps"] is None and broken["data_server_port"] is None
    )
    assert broken["domain_name"] == "" and broken["active_users"] is None


def test_parse_realtime_business_all_11_fields():
    raw = _rt_business(1000, _biz(9, "pay-card"), 3)
    assert jf.parse_realtime_business(raw) == {
        "domain_id": 1000,
        "business_id": 9,
        "business_name": "pay-card",
        "tps": 4.0,
        "response_time_avg_ms": 60.0,
        "active_services": 3,
        "concurrent_users": 4.5,
        "active_range_count_0": 3,
        "active_range_count_1": 4,
        "active_range_count_2": 5,
        "active_range_count_3": 6,
    }
    assert len(jf.REALTIME_BUSINESS_FIELDS) == 11


def test_parse_business_all_7_fields():
    out = jf.parse_business(_biz(7, "order", "d", ("/a/*", "/b?x=1")))
    assert out == {
        "business_id": 7,
        "business_name": "order",
        "business_description": "d",
        "bad_response_time_ms": 3007,
        "business_index": "idx-7",
        "business_oid": 70_007,
        "rules": ["/a/*", "/b?x=1"],
    }


# ── apm_service_status (A-3) ─────────────────────────────────


@pytest.mark.asyncio
async def test_service_without_name_is_one_call_per_source_all_domains(tmp_path, calls):
    tools = _tools(tmp_path, calls, two=True)
    with use_scope(_Plan()) as plan:
        out = await _scope(tools).apm_service_status()
    assert sorted(_hits(calls, "/api/realtime/domain")) == [("bank.test", {}), ("common.test", {})]
    assert plan.planned == 2
    assert [(r["source_id"], r["domain_id"], r["domain_name"]) for r in out["rows"]] == [
        ("bank", 1000, "order-svc"),
        ("bank", 1130, "settle"),
        ("bank", 1200, "order_batch"),
        ("common", 1000, "order-svc"),
    ]
    row = out["rows"][1]
    assert set(row) == {"source_id", "domain_id", "domain_name", *SERVICE_COLUMNS}
    assert row["tps"] == 12.0 and row["data_server_port"] == 5002
    assert "service_text" not in row and "partial" not in out
    assert any("방문·호출 수" in x and "하루" in x for x in out["limits"])
    assert out["tool"] == "apm_service_status" and out["source_kind"] == "apm_api"


SERVICE_COLUMNS = (
    "tps",
    "response_time_avg_ms",
    "active_services",
    "active_users",
    "concurrent_users",
    "reject_rate",
    "visit_day",
    "visit_hour",
    "hit_day",
    "hit_hour",
    "active_range_count_0",
    "active_range_count_1",
    "active_range_count_2",
    "active_range_count_3",
    "data_server_ip",
    "data_server_port",
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "tier", "domains"),
    [
        ("settle", "exact", [1130]),
        ("ORDER SVC", "normalized", [1000]),
        ("order", "prefix", [1000, 1200]),
        ("batch", "contains", [1200]),
    ],
)
async def test_service_name_tiers(tmp_path, calls, query, tier, domains):
    tools = _tools(tmp_path, calls)
    out = await _scope(tools).apm_service_status(query)
    assert [(r["domain_id"], r["match_tier"], r["service_text"]) for r in out["rows"]] == [
        (d, tier, query) for d in domains
    ]
    assert [p["domain_id"] for _, p in _hits(calls, "/api/realtime/domain")] == [
        str(d) for d in domains
    ]
    multi = [x for x in out["limits"] if "이름이 맞은 도메인" in x]
    assert bool(multi) == (len(domains) > 1)
    if multi:
        assert "order-svc(도메인 1000)" in multi[0] and "order_batch(도메인 1200)" in multi[0]


@pytest.mark.asyncio
async def test_service_unresolved_makes_no_api_call_and_suggests(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await _scope(tools).apm_service_status(["ordr-svc", EMAIL])
    assert out["rows"] == [] and _hits(calls, "/api/realtime/domain") == []
    assert _unresolved(out) == [
        "서비스 'ordr-svc'에 해당하는 제니퍼 도메인을 찾지 못했습니다",
        "서비스 '<email>'에 해당하는 제니퍼 도메인을 찾지 못했습니다",
    ]
    assert out["suggestions"] == [
        {"query": "ordr-svc", "domain_name": "order-svc", "source_id": "default", "domain_id": 1000}
    ]
    assert EMAIL not in json.dumps(out, ensure_ascii=False)
    assert [s["status"] for s in out["sources"]] == ["no_match"]


@pytest.mark.asyncio
async def test_service_suggestions_are_at_most_three_per_name(tmp_path, calls):
    data = DATA["bank.test"]["domains"]
    saved = dict(data)
    data.update({1301: "payment-a", 1302: "payment-b", 1303: "payment-c", 1304: "payment-d"})
    try:
        tools = _tools(tmp_path, calls)
        out = await _scope(tools).apm_service_status("paymentx")
    finally:
        data.clear()
        data.update(saved)
    assert out["rows"] == [] and len(out["suggestions"]) == 3
    assert {s["query"] for s in out["suggestions"]} == {"paymentx"}


@pytest.mark.asyncio
async def test_service_several_names_one_unresolved(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await _scope(tools).apm_service_status(["settle", "nothing-here", "SETTLE"])
    assert [r["domain_id"] for r in out["rows"]] == [1130]  # 대소문자 무시 중복은 한 번
    assert _unresolved(out) == ["서비스 'nothing-here'에 해당하는 제니퍼 도메인을 찾지 못했습니다"]
    assert "suggestions" in out and len(_hits(calls, "/api/realtime/domain")) == 1


@pytest.mark.asyncio
async def test_service_same_domain_id_in_two_sources(tmp_path, calls):
    tools = _tools(tmp_path, calls, two=True)
    out = await _scope(tools).apm_service_status("order-svc")
    assert [(r["source_id"], r["domain_id"], r["match_tier"]) for r in out["rows"]] == [
        ("bank", 1000, "exact"),
        ("common", 1000, "exact"),
    ]
    assert sorted(_hits(calls, "/api/realtime/domain")) == [
        ("bank.test", {"domain_id": "1000"}),
        ("common.test", {"domain_id": "1000"}),
    ]
    multi = next(x for x in out["limits"] if "이름이 맞은 도메인 2곳" in x)
    assert "소스 bank · 도메인 1000" in multi and "소스 common · 도메인 1000" in multi


@pytest.mark.asyncio
async def test_service_one_source_failure_is_partial(tmp_path, calls):
    fail = {"/api/realtime/domain": {("common.test", None)}}
    tools = _tools(tmp_path, calls, two=True, fail=fail)
    out = await _scope(tools).apm_service_status()
    assert out["partial"] is True
    assert {r["source_id"] for r in out["rows"]} == {"bank"} and len(out["rows"]) == 3
    assert any("실시간 도메인 조회 실패(소스 common)" in x for x in out["limits"])
    status = {s["source_id"]: s["status"] for s in out["sources"]}
    assert status == {"bank": "ok", "common": "unavailable"}


@pytest.mark.asyncio
async def test_service_all_sources_failing_is_an_error(tmp_path, calls):
    tools = _tools(tmp_path, calls, two=True, fail={"/api/realtime/domain": set()})
    with pytest.raises(ApmError):
        await _scope(tools).apm_service_status()


@pytest.mark.asyncio
async def test_service_unknown_domain_id_is_not_widened(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await _scope(tools).apm_service_status(domain_id=4242)
    assert out["rows"] == [] and _hits(calls, "/api/realtime/domain") == []
    assert _unresolved(out) == [
        "도메인 ID 4242는 제니퍼 도메인 목록에 없습니다(있는 도메인: 1000, 1130, 1200)"
    ]
    narrowed = await _scope(tools).apm_service_status("order", domain_id=1200)
    assert [r["domain_id"] for r in narrowed["rows"]] == [1200]  # AND


@pytest.mark.asyncio
async def test_service_argument_validation(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    for kwargs in ({"service": []}, {"service": [""]}, {"service": 3}, {"domain_id": -1}):
        with pytest.raises(ApmError) as exc:
            await _scope(tools).apm_service_status(**kwargs)
        assert exc.value.code == INVALID_ARGUMENT
    assert calls == []  # 형식 오류는 HTTP 전에 거른다


@pytest.mark.asyncio
async def test_service_respects_source_domain_filter(tmp_path, calls):
    tools = _tools(tmp_path, calls, extra={"JENNIFER_DOMAIN_IDS": "[1000, 1200]"})
    out = await _scope(tools).apm_service_status()
    assert [r["domain_id"] for r in out["rows"]] == [1000, 1200]  # 1130은 소스 범위 밖


# ── apm_business (A-4) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_business_list_rows_mask_description_and_rules(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await _scope(tools).apm_business("list")
    assert [(r["domain_id"], r["business_id"], r["business_name"]) for r in out["rows"]] == [
        (1000, 7, "order"),
        (1000, 9, "pay-card"),
        (1000, 10, "pay-bank"),
        (1000, 11, "loan"),
        (1130, 21, "settle-night"),
    ]
    row = out["rows"][0]
    assert row["business_description"] == "주문 처리 담당 <email>"
    assert row["rules"] == ["/order/list?user=<v>"]
    assert (row["bad_response_time_ms"], row["business_index"], row["business_oid"]) == (
        3007,
        "idx-7",
        70_007,
    )
    assert out["_file_only"] == ["business_oid"] and out["mode"] == "list"
    text = json.dumps(out, ensure_ascii=False)
    assert EMAIL not in text and "user=kim" not in text
    assert _hits(calls, "/api/realtime/business") == []
    assert len(_hits(calls, "/api/business")) == 3


@pytest.mark.asyncio
async def test_business_current_without_name_is_one_call_per_domain(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    with use_scope(_Plan()) as plan:
        out = await _scope(tools).apm_business()
    assert _hits(calls, "/api/realtime/business") == [
        ("bank.test", {"domain_id": "1000"}),
        ("bank.test", {"domain_id": "1130"}),
        ("bank.test", {"domain_id": "1200"}),
    ]
    assert _hits(calls, "/api/business") == []  # 이름이 없으면 업무 정의를 읽지 않는다
    assert plan.planned == 3
    assert [(r["domain_id"], r["business_id"]) for r in out["rows"]] == [
        (1000, 7),
        (1000, 9),
        (1000, 10),
        (1000, 11),
        (1130, 21),
    ]
    assert set(out["rows"][0]) == {
        "source_id",
        "domain_id",
        "domain_name",
        "business_id",
        "business_name",
        *BUSINESS_COLUMNS,
    }
    assert out["rows"][0]["domain_name"] == "order-svc" and out["mode"] == "current"


BUSINESS_COLUMNS = (
    "tps",
    "response_time_avg_ms",
    "active_services",
    "concurrent_users",
    "active_range_count_0",
    "active_range_count_1",
    "active_range_count_2",
    "active_range_count_3",
)


@pytest.mark.asyncio
async def test_business_current_by_name(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    with use_scope(_Plan()) as plan:
        out = await _scope(tools).apm_business(business="loan")
    assert [(r["business_id"], r["match_tier"], r["business_text"]) for r in out["rows"]] == [
        (11, "exact", "loan")
    ]
    assert _hits(calls, "/api/realtime/business") == [
        ("bank.test", {"domain_id": "1000", "business_id": "11"})
    ]
    assert plan.planned == 3 + 1  # 업무 정의 3도메인(캐시 밖) + 현재값 1
    assert out["rows"][0]["tps"] == 5.0  # 도메인 1000의 넷째 업무(k=4)


@pytest.mark.asyncio
async def test_business_one_name_many_matches_queries_all_and_discloses(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await _scope(tools).apm_business(business="pay")
    assert [(r["business_id"], r["match_tier"]) for r in out["rows"]] == [
        (9, "prefix"),
        (10, "prefix"),
    ]
    note = next(x for x in out["limits"] if "이름이 맞은 업무 2건" in x)
    assert "pay-card(도메인 1000 · 업무 9)" in note and "pay-bank(도메인 1000 · 업무 10)" in note
    assert _unresolved(out) == []  # 되묻지 않는다


@pytest.mark.asyncio
async def test_business_unresolved_no_realtime_call(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await _scope(tools).apm_business(business=["loann", f"x {EMAIL}"])
    assert out["rows"] == [] and _hits(calls, "/api/realtime/business") == []
    assert _unresolved(out) == [
        "업무 'loann'에 해당하는 제니퍼 업무를 찾지 못했습니다",
        "업무 'x <email>'에 해당하는 제니퍼 업무를 찾지 못했습니다",
    ]
    assert out["suggestions"][0] == {
        "query": "loann",
        "business_name": "loan",
        "business_id": 11,
        "source_id": "default",
        "domain_id": 1000,
    }
    assert EMAIL not in json.dumps(out, ensure_ascii=False)


@pytest.mark.asyncio
async def test_business_compare_several_and_narrow_by_service(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await _scope(tools).apm_business(business=["order", "settle-night"])
    assert [(r["domain_id"], r["business_id"]) for r in out["rows"]] == [(1000, 7), (1130, 21)]
    calls.clear()
    narrowed = await _scope(tools).apm_business("list", service="settle")
    assert [r["business_id"] for r in narrowed["rows"]] == [21]
    assert _hits(calls, "/api/business") == []  # 업무 정의 캐시(TTL) 재사용


@pytest.mark.asyncio
async def test_business_service_narrowing_fetches_only_that_domain(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await _scope(tools).apm_business(business="order", service="order-svc")
    assert [(r["domain_id"], r["business_id"]) for r in out["rows"]] == [(1000, 7)]
    assert [p for _, p in _hits(calls, "/api/business")] == [{"domain_id": "1000"}]


@pytest.mark.asyncio
async def test_business_unresolved_service_blocks_every_call(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await _scope(tools).apm_business(business="order", service="no-such-svc")
    assert out["rows"] == []
    assert _hits(calls, "/api/business") == [] and _hits(calls, "/api/realtime/business") == []
    assert _unresolved(out) == ["서비스 'no-such-svc'에 해당하는 제니퍼 도메인을 찾지 못했습니다"]


@pytest.mark.asyncio
async def test_business_two_sources_and_partial_definitions(tmp_path, calls):
    fail = {"/api/business": {("bank.test", 1130)}}
    tools = _tools(tmp_path, calls, two=True, fail=fail)
    out = await _scope(tools).apm_business(business=["order", "settle-night"])
    assert [(r["source_id"], r["business_id"]) for r in out["rows"]] == [
        ("bank", 7),
        ("common", 7),
    ]
    assert out["partial"] is True
    assert _unresolved(out) == [
        "업무 'settle-night'에 해당하는 제니퍼 업무를 찾지 못했습니다(업무 목록을 읽지 못한 도메인"
        " 1곳은 확인하지 못했습니다)"
    ]


@pytest.mark.asyncio
async def test_business_all_definitions_failing_is_an_error(tmp_path, calls):
    tools = _tools(tmp_path, calls, fail={"/api/business": set()})
    with pytest.raises(ApmError):
        await _scope(tools).apm_business("list")
    with pytest.raises(ApmError):
        await _scope(tools).apm_business(mode="bogus")


# ── apm_metrics series domain·business (A-5) ─────────────────


@pytest.mark.asyncio
async def test_domain_series_all_domains_and_expect_calls(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    with use_scope(_Plan()) as plan:
        out = await tools.apm_metrics("series", "domain", metrics=["service_count"])
    hits = _hits(calls, "/api/dbmetrics/domain")
    assert [p["domain_id"] for _, p in hits] == ["1000", "1130", "1200"]
    assert all(
        p["metrics"] == "service_count" and p["interval_minute"] == "5" and "time_pattern" not in p
        for _, p in hits
    )
    assert plan.planned == 1 + 3  # 카탈로그 1 + 도메인 3 × 지표 1
    assert out["rows"][0] == {
        "source_id": "default",
        "domain_id": 1000,
        "domain_name": "order-svc",
        "metric": "service_count",
        "time_ms": NOW_MS - 900_000,
        "value": 1.0,
    }
    assert (out["mode"], out["scope"], out["interval_minute"]) == ("series", "domain", 5)
    assert out["window"]["minutes"] == 60 and out["row_count"] == 9
    assert any("interval_minute 허용값" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_domain_series_by_service_and_neutral_metric(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await tools.apm_metrics(
        "series", "domain", metrics=["response_time_avg_ms"], service="settle"
    )
    assert [p for _, p in _hits(calls, "/api/dbmetrics/domain")] == [
        {
            "domain_id": "1130",
            "interval_minute": "5",
            "metrics": "service_time",
            "start_time": str(NOW_MS - 3_600_000),
            "end_time": str(NOW_MS),
        }
    ]
    assert {r["domain_id"] for r in out["rows"]} == {1130}


@pytest.mark.asyncio
async def test_domain_series_unknown_metric_has_candidates_and_no_series_call(tmp_path, calls):
    """plans/134 V34-1(gw-fix-34 G-1) — 요청 지표를 전부 모르면 오류가 아니라 기본 지표로 조회하고
    미해결 고지에 후보를 싣는다(종전: invalid_argument). 모르는 지표 자체로는 묻지 않는다."""
    tools = _tools(tmp_path, calls)
    out = await tools.apm_metrics("series", "domain", metrics=["service_cnt"], domain_id=1000)
    asked = [p["metrics"] for _, p in _hits(calls, "/api/dbmetrics/domain")]
    assert "service_cnt" not in asked
    assert asked == ["service_time", "service_count", "service_err_count"]
    (note,) = out["_unresolved"]
    assert note.startswith("요청한 지표 service_cnt는 목록에 없어 기본 지표(")
    candidates = note.split("후보: ")[1].split(")")[0].split(", ")
    assert "service_count" in candidates and 1 <= len(candidates) <= 3
    assert out["partial"] is True
    assert any("domain 지표 카탈로그" in x for x in out["limits"])
    partly = await tools.apm_metrics(
        "series", "domain", metrics=["service_count", "service_cnt"], domain_id=1000
    )
    assert {r["metric"] for r in partly["rows"]} == {"service_count"}
    assert partly["partial"] is True and partly["_unresolved"]
    assert "후보: " in partly["_unresolved"][0] and "빼고 조회했습니다" in partly["_unresolved"][0]


@pytest.mark.asyncio
async def test_domain_series_catalog_failure_queries_unvalidated(tmp_path, calls):
    tools = _tools(tmp_path, calls, fail={"/api/metrics": set()})
    out = await tools.apm_metrics("series", "domain", metrics=["whatever_metric"], domain_id=1000)
    assert [p["metrics"] for _, p in _hits(calls, "/api/dbmetrics/domain")] == ["whatever_metric"]
    assert out["partial"] is True
    assert any("지표 카탈로그 조회 실패" in x and "검증하지 못하고" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_domain_series_unresolved_service_makes_no_call(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await tools.apm_metrics("series", "domain", metrics=["service_count"], service="zzz")
    assert out["rows"] == [] and out["suggestions"] == []
    assert _hits(calls, "/api/dbmetrics/domain") == [] and _hits(calls, "/api/metrics") == []
    assert _unresolved(out) == ["서비스 'zzz'에 해당하는 제니퍼 도메인을 찾지 못했습니다"]


@pytest.mark.asyncio
async def test_business_series_by_name_all_matches(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    with use_scope(_Plan()) as plan:
        out = await tools.apm_metrics(
            "series", "business", metrics=["service_count"], business="pay"
        )
    hits = _hits(calls, "/api/dbmetrics/business")
    assert [(p["domain_id"], p["business_id"]) for _, p in hits] == [("1000", "9"), ("1000", "10")]
    assert plan.planned == 3 + 1 + 2  # 업무 정의 3 + 카탈로그 1 + 업무 2 × 지표 1
    row = out["rows"][0]
    assert {k: row[k] for k in ("domain_id", "domain_name", "business_id", "business_name")} == {
        "domain_id": 1000,
        "domain_name": "order-svc",
        "business_id": 9,
        "business_name": "pay-card",
    }
    assert out["scope"] == "business" and any("이름이 맞은 업무 2건" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_business_series_without_target_is_every_business(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await tools.apm_metrics("series", "business", metrics=["service_count"])
    hits = _hits(calls, "/api/dbmetrics/business")
    assert [(p["domain_id"], p["business_id"]) for _, p in hits] == [
        ("1000", "7"),
        ("1000", "9"),
        ("1000", "10"),
        ("1000", "11"),
        ("1130", "21"),
    ]
    assert out["row_count"] == 15


@pytest.mark.asyncio
async def test_business_series_by_id_and_argument_rules(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    out = await tools.apm_metrics(
        "series", "business", metrics=["service_count"], business_id=21, domain_id=1130
    )
    assert [p["business_id"] for _, p in _hits(calls, "/api/dbmetrics/business")] == ["21"]
    assert out["rows"][0]["business_name"] == "settle-night"
    missing = await tools.apm_metrics(
        "series", "business", metrics=["service_count"], business_id=99, domain_id=1130
    )
    assert missing["rows"] == [] and _unresolved(missing) == [
        "업무 ID 99는 settle(도메인 1130) 업무 목록에 없습니다"
    ]
    bad = [
        {"business_id": 21},  # domain_id 없음
        {"business_id": 21, "domain_id": 1130, "business": "x"},
        {"business": []},
    ]
    for kwargs in bad:
        with pytest.raises(ApmError) as exc:
            await tools.apm_metrics("series", "business", metrics=["service_count"], **kwargs)
        assert exc.value.code == INVALID_ARGUMENT, kwargs
    with pytest.raises(ApmError) as exc:
        await tools.apm_metrics("series", "domain", metrics=["service_count"], business="x")
    assert "scope business 시계열에서만" in exc.value.reason


@pytest.mark.asyncio
async def test_business_series_one_call_failing_is_partial(tmp_path, calls):
    fail = {"/api/dbmetrics/business": {("bank.test", 1130)}}
    tools = _tools(tmp_path, calls, fail=fail)
    out = await tools.apm_metrics("series", "business", metrics=["service_count"])
    assert out["partial"] is True and {r["domain_id"] for r in out["rows"]} == {1000}
    assert any("지표 service_count 조회 실패(도메인 1130 · 업무 21)" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_instance_series_rejects_scope_arguments(tmp_path, calls):
    tools = _tools(tmp_path, calls)
    for kwargs in ({"service": "x"}, {"business_id": 1}, {"domain_id": 1000}):
        with pytest.raises(ApmError) as exc:
            await tools.apm_metrics("series", hostname="h", metrics=["heap_used"], **kwargs)
        assert "scope domain·business 시계열에서만" in exc.value.reason
    assert calls == []


# ── MCP (작업 경로 · 감사 · 스키마) ─────────────────────────────


def _server(tmp_path, calls, **kw):
    from apm_gateway.application.jobs import JobManager
    from apm_gateway.application.spool import Spool
    from apm_gateway.interface.server import audit_job_finished, create_server

    from apm_gateway.config import JobConfig

    tools = _tools(tmp_path, calls, **kw)
    cfg = JobConfig(spool_dir=tmp_path / "jobs")
    jobs = JobManager(
        Spool(cfg.spool_dir),
        cfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        on_finish=audit_job_finished,
    )
    return create_server(tools, jobs=jobs), jobs


def _payload(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_mcp_tools_run_as_jobs_and_mask_audit(tmp_path, calls, caplog):
    mcp, jobs = _server(tmp_path, calls)
    try:
        with caplog.at_level(logging.INFO, logger="apm_gateway.audit"):
            svc = _payload(await mcp.call_tool("apm_service_status", {"service": [EMAIL]}))
            biz = _payload(
                await mcp.call_tool("apm_business", {"business": "loan", "owner": "user:kim"})
            )
            series = _payload(
                await mcp.call_tool(
                    "apm_metrics",
                    {
                        "mode": "series",
                        "scope": "domain",
                        "metrics": ["service_count"],
                        "domain_id": 1130,
                    },
                )
            )
    finally:
        await jobs.aclose()
    notes = [d["text"] for d in svc["disclosures"] if d["kind"] == "apm_unresolved_condition"]
    assert notes == ["서비스 '<email>'에 해당하는 제니퍼 도메인을 찾지 못했습니다"]
    assert svc["rows"] == [] and EMAIL not in json.dumps(svc, ensure_ascii=False)
    assert [r["business_id"] for r in biz["rows"]] == [11]
    assert {r["domain_id"] for r in series["rows"]} == {1130}
    lines = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]
    assert any("tool=apm_service_status" in x and "target=service:<email>" in x for x in lines)
    assert any("tool=apm_business" in x and "target=business:loan" in x for x in lines)
    assert any("tool=apm_metrics" in x and "target=domain:1130" in x for x in lines)
    assert all(EMAIL not in x and TOKEN not in x for x in lines)
    from apm_gateway.config import PACKAGE_ROOT

    assert not (PACKAGE_ROOT / "var").exists()  # 기본 스풀을 만들지 않는다


@pytest.mark.asyncio
async def test_mcp_schemas_of_new_and_extended_tools(tmp_path, calls):
    mcp, jobs = _server(tmp_path, calls)
    try:
        schemas = {t.name: t.inputSchema["properties"] for t in await mcp.list_tools()}
    finally:
        await jobs.aclose()
    common = {"source_ids", "investigation_id", "thread_id", "owner", "wait_seconds"}
    assert set(schemas["apm_service_status"]) == {"service", "domain_id", *common}
    assert set(schemas["apm_business"]) == {"mode", "business", "service", "domain_id", *common}
    assert {"service", "business", "business_id", "domain_id"} <= set(schemas["apm_metrics"])
    kinds = {p.get("type") for p in schemas["apm_service_status"]["service"]["anyOf"]}
    assert kinds == {"string", "array", "null"}


@pytest.mark.asyncio
async def test_service_via_mock_server_uses_allowlisted_path(tmp_path, mock_server_factory):
    """목 Open API 서버(127.0.0.1 · J0 카탈로그 사본으로 허용 판정) 종단 — 새 경로가 사본에서도
    허용으로 기록된다."""
    from conftest import _fx, make_tools, synthetic_fixtures, write_fixtures

    fixtures = synthetic_fixtures() + [
        _fx("/api/realtime/domain", {"result": [_rt_domain(1000, "demo-domain", 1)]})
    ]
    base, state = mock_server_factory(write_fixtures(tmp_path / "fx", fixtures))
    tools, _ = make_tools(base, extra={"APM_SPOOL_DIR": str(tmp_path / "spool")})
    out = await _scope(tools).apm_service_status("demo-domain")
    assert [(r["domain_id"], r["match_tier"], r["tps"]) for r in out["rows"]] == [
        (1000, "exact", 11.0)
    ]
    hits = [h for h in state.hits if h["path"] == "/api/realtime/domain"]
    assert [(h["allowlisted"], h["query"], h["status"]) for h in hits] == [
        (True, {"domain_id": "1000"}, 200)
    ]
    assert all(h["allowlisted"] for h in state.hits)
