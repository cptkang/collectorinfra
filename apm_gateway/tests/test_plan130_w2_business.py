"""plans/130 W2 — 업무명 해석(`apm_instance_map(business=…)` · N-2)과 허용목록 `/api/business`(N-5).

근거: B0 수동 매핑(정합 파일 `business_map` — 있으면 그것만) · 없으면 B1 도메인 이름 · B2
제니퍼 업무 정의(`/api/business` → 액티브 서비스·최근 5분 트랜잭션의 업무 id 역추적) · B3 인스턴스
이름·설명(W1 검색) 합집합.

합성 데이터(소스 bank): 도메인 1000 order(was01_a·was01_b·api02_main·IMG-WAS-01) · 1130 settle
(settle-was01·batch-runner·shared_was) · 1200 image(img_front). 업무 정의 — 1000: 7 주문 · 9 결제 ·
11 대출(최근 처리 없음) · 1130: 21 settle. 최근 처리 — 1000: 액티브 1002[9] · 거래 1001[7, 9] ·
1003[7] · 1130: 거래 2001[21]. 소스 common: 도메인 1000 order(shared_was·batch-x).
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from apm_gateway.adapters.jennifer.allowlist import ALLOWED, NotAllowedError, check_request
from apm_gateway.domain.errors import INVALID_ARGUMENT, ApmError
from conftest import NOW_MS, NOW_S, TOKEN, make_tools, synthetic_handler

from apm_gateway.config import load_config

BANK_URL = "http://bank.test"
COMMON_URL = "http://common.test"
KINDS = ("business_map", "domain", "business", "instance_text")


def _inst(instance_id: int, name: str, host: str, description: str = "") -> dict:
    return {
        "instanceId": instance_id,
        "name": name,
        "hostName": host,
        "ipAddress": "10.0.0.11",
        "platform": "JAVA",
        "status": "RUNNING",
        "version": "5.6.5",
        "description": description,
    }


BANK = {
    1000: (
        "order",
        [
            _inst(1001, "was01_a", "was-host01"),
            _inst(1002, "was01_b", "was-host01"),
            _inst(1003, "api02_main", ""),
            _inst(1004, "IMG-WAS-01", "img-host01", "이미지 업무 WAS#1"),
        ],
    ),
    1130: (
        "settle",
        [
            _inst(2001, "settle-was01", "stl-host01"),
            _inst(2002, "batch-runner", "batch01"),
            _inst(2003, "shared_was", "shr-host01"),
        ],
    ),
    1200: ("image", [_inst(3001, "img_front", "img-front01")]),
}
COMMON = {
    1000: ("order", [_inst(4001, "shared_was", "shr-host02"), _inst(4002, "batch-x", "bx01")])
}


def _biz(bid: int, name: str, desc: str) -> dict:
    return {"businessId": bid, "name": name, "description": desc, "ruleList": [f"/{bid}/*"]}


BUSINESS = {
    1000: [
        _biz(7, "주문", "주문 처리"),
        _biz(9, "결제", "카드 결제 승인 담당 kim@example.com"),
        _biz(11, "대출", "여신 심사"),
    ],
    1130: [_biz(21, "settle", "야간 배치 정산")],
    1200: [],
}
ACTIVE = {1000: [{"instanceId": 1002, "businessId": [9], "businessName": ["결제"]}]}
TXS = {
    1000: [
        {"instanceId": 1001, "txid": "1", "businessId": [7, 9], "businessName": ["주문", "결제"]},
        {"instanceId": 1003, "txid": "2", "businessId": [7], "businessName": ["주문"]},
    ],
    1130: [{"instanceId": 2001, "txid": "3", "businessId": [21], "businessName": ["settle"]}],
}


def _handler(data: dict, calls: list[httpx.Request], *, fail: dict | None = None):
    """도메인·인벤토리·업무 정의·액티브 서비스·거래를 도메인별로 답한다. `fail`: 경로 → 실패시킬
    도메인 집합(빈 집합 = 전 도메인) — 500 응답."""

    def did(request: httpx.Request) -> int:
        return int(request.url.params["domain_id"])

    def inventory(request: httpx.Request) -> httpx.Response:
        _, rows = data.get(did(request), ("", []))
        return httpx.Response(200, json={"result": rows})

    def per_domain(table: dict):
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": table.get(did(request), [])})

        return respond

    domains = {"result": [{"domainId": d, "name": n} for d, (n, _) in data.items()]}
    bank = data is BANK
    inner = synthetic_handler(
        override={
            "/api/domain": httpx.Response(200, json=domains),
            "/api/instance": inventory,
            "/api/business": per_domain(BUSINESS if bank else {}),
            "/api/activeService/list": per_domain(ACTIVE if bank else {}),
            "/api/transaction/time": per_domain(TXS if bank else {}),
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        wanted = (fail or {}).get(request.url.path)
        if wanted is not None and (not wanted or did(request) in wanted):
            return httpx.Response(500, json={"exception": {"message": "boom"}})
        return await inner(request)

    return handler


def _paths(calls: list[httpx.Request]) -> list[str]:
    return [f"{r.url.host}{r.url.path}" for r in calls]


def _policy(tmp_path, body: str):
    directory = tmp_path / "policy"
    directory.mkdir(exist_ok=True)
    (directory / "instance_map.yaml").write_text(
        "version: 1\nmatch_rules: [{kind: host_name}, {kind: exact}, {kind: prefix}]\n" + body,
        encoding="utf-8",
    )
    return directory


def _one(tmp_path, calls, *, policy: str = "", fail: dict | None = None, clock=lambda: NOW_S):
    from apm_gateway.application.sources import build_source_set
    from apm_gateway.application.tools import ApmTools

    cfg = load_config(
        {
            "JENNIFER_API_URL": BANK_URL,
            "JENNIFER_API_TOKEN": TOKEN,
            "JENNIFER_RATE_LIMIT_PER_SEC": "0",
            "APM_SPOOL_DIR": str(tmp_path / "spool"),
        },
        policy_dir=_policy(tmp_path, policy),
    )
    transport = httpx.MockTransport(_handler(BANK, calls, fail=fail))
    return ApmTools(build_source_set(cfg, transport=transport), cfg, clock=clock)


def _two(tmp_path, calls, *, policy: str = ""):
    from apm_gateway.application.sources import build_source_set
    from apm_gateway.application.tools import ApmTools

    by_host = {"bank.test": _handler(BANK, calls), "common.test": _handler(COMMON, calls)}

    async def route(request: httpx.Request) -> httpx.Response:
        return await by_host[request.url.host](request)

    cfg = load_config(
        {
            "JENNIFER_SOURCES": '["bank", "common"]',
            "JENNIFER_BANK_API_URL": BANK_URL,
            "JENNIFER_BANK_API_TOKEN": TOKEN,
            "JENNIFER_COMMON_API_URL": COMMON_URL,
            "JENNIFER_COMMON_API_TOKEN": "tok-OTHER-77c1",
            "JENNIFER_RATE_LIMIT_PER_SEC": "0",
            "APM_SPOOL_DIR": str(tmp_path / "spool"),
        },
        policy_dir=_policy(tmp_path, policy),
    )
    return ApmTools(
        build_source_set(cfg, transport=httpx.MockTransport(route)), cfg, clock=lambda: NOW_S
    )


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


def _ids(out: dict) -> list[int]:
    return [r["instance_id"] for r in out["rows"]]


def _counts(**kw: int) -> dict:
    return {k: kw.get(k, 0) for k in KINDS}


MAPPING = (
    "business_map:\n"
    "  - {business: 카드, aliases: [card-biz], instances: [was01_b, ghost_was]}\n"
    "  - {business: 결제, instances: [WAS01_A]}\n"
)


# ── B0 수동 매핑 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_b0_mapping_only_and_no_other_evidence_calls(tmp_path, calls):
    """「결제」는 업무 정의(B2)로도 맞지만 수동 매핑이 있으면 그것만 — 업무·거래 호출 0건."""
    tools = _one(tmp_path, calls, policy=MAPPING)
    out = await tools.apm_instance_map(business="결제")
    assert _ids(out) == [1001]
    row = out["rows"][0]
    assert (row["match_kind"], row["match_kinds"], row["search_confidence"]) == (
        "business_map",
        ["business_map"],
        "high",
    )
    assert row["hostname"] == "was-host01" and row["business_names"] == []
    assert row["match_tier"] == ""
    assert out["business"] == {"counts": _counts(business_map=1)}
    assert not [p for p in _paths(calls) if not p.endswith(("/api/domain", "/api/instance"))]
    assert "_unresolved" not in out and "suggestions" not in out


@pytest.mark.asyncio
async def test_b0_alias_normalized_and_missing_name_limit(tmp_path, calls):
    tools = _one(tmp_path, calls, policy=MAPPING)
    out = await tools.apm_instance_map(business="CARD BIZ")
    assert _ids(out) == [1002]
    missing = [x for x in out["limits"] if "business_map" in x]
    assert missing == [
        "[한계] 업무 수동 매핑(business_map)의 인스턴스 이름을 조회한 APM 인벤토리에서 찾지"
        " 못했다: ghost_was"
    ]


@pytest.mark.asyncio
async def test_b0_source_scoped_mapping(tmp_path, calls):
    """`source_id`를 준 매핑은 그 소스에서만 찾는다 — 그 소스를 고르지 않았으면 쓰지 않는다."""
    policy = "business_map:\n  - {business: 공유, instances: [shared_was], source_id: common}\n"
    tools = _two(tmp_path, calls, policy=policy)
    out = await tools.apm_instance_map(business="공유")
    assert [(r["source_id"], r["instance_id"], r["match_kind"]) for r in out["rows"]] == [
        ("common", 4001, "business_map")
    ]
    assert {s["source_id"]: s["status"] for s in out["sources"]} == {
        "bank": "no_match",
        "common": "ok",
    }
    out = await tools.apm_instance_map(business="공유", source_ids=["bank"])
    assert out["business"]["counts"]["business_map"] == 0  # 매핑 미적용 → 합집합(0건)
    assert out["rows"] == []


def test_malformed_business_map_items_warn_and_are_ignored(tmp_path, calls, caplog):
    policy = (
        "business_map:\n"
        "  - {business: 결제, instances: [was01_a]}\n"
        "  - {business: '', instances: [x]}\n"
        "  - {business: 주문}\n"
        "  - {business: 대출, instances: x}\n"
        "  - {business: 여신, aliases: [1], instances: [x]}\n"
        "  - {business: 송금, instances: [x], source_id: 3}\n"
        "  - just-a-string\n"
    )
    with caplog.at_level(logging.WARNING):
        tools = _one(tmp_path, calls, policy=policy)
    assert [m.business for m in tools._business_map] == ["결제"]
    warned = [r.getMessage() for r in caplog.records if "business_map[" in r.getMessage()]
    assert [w.split("]")[0] for w in warned] == [f"정합 파일 business_map[{i}" for i in range(1, 7)]


def test_business_map_not_a_list_warns(tmp_path, calls, caplog):
    with caplog.at_level(logging.WARNING):
        tools = _one(tmp_path, calls, policy="business_map: {business: 결제}\n")
    assert tools._business_map == []
    assert "business_map은 목록이어야 한다" in caplog.text


def test_unknown_business_map_source_warns_at_load(tmp_path, caplog):
    policy = _policy(
        tmp_path, "business_map:\n  - {business: x, instances: [y], source_id: ghost}\n"
    )
    with caplog.at_level(logging.WARNING):
        load_config({"JENNIFER_API_URL": BANK_URL, "JENNIFER_API_TOKEN": TOKEN}, policy_dir=policy)
    assert "['ghost']" in caplog.text


# ── B1 · B2 · B3 단독 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_b1_domain_name_only(tmp_path, calls):
    tools = _one(tmp_path, calls)
    out = await tools.apm_instance_map(business="IMAGE")
    assert _ids(out) == [3001]
    row = out["rows"][0]
    assert (row["match_kind"], row["match_kinds"], row["search_confidence"]) == (
        "domain",
        ["domain"],
        "medium",
    )
    assert out["business"] == {"counts": _counts(domain=1)}
    # 업무 정의는 도메인마다 읽었지만(맞는 정의 없음) 역추적 호출은 없다
    assert sorted(r.url.params["domain_id"] for r in calls if r.url.path == "/api/business") == [
        "1000",
        "1130",
        "1200",
    ]
    assert not [
        r for r in calls if r.url.path in ("/api/activeService/list", "/api/transaction/time")
    ]
    assert not [x for x in out["limits"] if "업무 정의 근거" in x]


@pytest.mark.asyncio
async def test_b2_business_definition_traces_active_and_recent_transactions(tmp_path, calls):
    tools = _one(tmp_path, calls)
    out = await tools.apm_instance_map(business="결제")
    assert _ids(out) == [1001, 1002]  # 거래(1001) + 액티브 서비스(1002) · 주문만 처리한 1003 제외
    for row in out["rows"]:
        assert (row["match_kind"], row["match_kinds"], row["business_names"]) == (
            "business",
            ["business"],
            ["결제"],
        )
        assert row["search_confidence"] == "medium"
    assert out["business"] == {"counts": _counts(business=2)}
    assert (
        "[한계] 업무 정의 근거는 최근 처리한 인스턴스만 찾는다(현재 액티브 서비스 + 최근 5분"
        " 트랜잭션)" in out["limits"]
    )
    # 역추적은 업무 정의가 맞은 도메인(1000)만 · 액티브 서비스는 인스턴스 지정 없이 1회
    active = [r for r in calls if r.url.path == "/api/activeService/list"]
    assert [dict(r.url.params) for r in active] == [{"domain_id": "1000"}]
    # 거래 창: 최근 5분을 1분 조각 5개로(끝 포함 경계는 `_xview`와 같다) · 인스턴스 지정 없음
    txs = [r for r in calls if r.url.path == "/api/transaction/time"]
    spans = [(int(r.url.params["start_time"]), int(r.url.params["end_time"])) for r in txs]
    assert {r.url.params["domain_id"] for r in txs} == {"1000"}
    assert all("instance_id" not in r.url.params for r in txs)
    assert len(spans) == 5 and spans[0][0] == NOW_MS - 300_000 and spans[-1][1] == NOW_MS
    assert all(end - start <= 60_000 for start, end in spans)


@pytest.mark.asyncio
async def test_b2_cache_second_call_makes_no_http_and_expires_after_ttl(tmp_path, calls):
    now = [NOW_S]
    tools = _one(tmp_path, calls, clock=lambda: now[0])
    first = await tools.apm_instance_map(business="결제")
    before = len(calls)
    second = await tools.apm_instance_map(business="결제")
    assert len(calls) == before  # 업무 정의·역추적 표 TTL 10분 캐시(인벤토리도 캐시)
    assert second["rows"] == first["rows"]
    # 같은 도메인의 다른 업무도 같은 표에서 찾는다
    other = await tools.apm_instance_map(business="주문")
    assert len(calls) == before and _ids(other) == [1003, 1001]  # 이름순(api02_main·was01_a)
    now[0] = NOW_S + 601
    await tools.apm_instance_map(business="결제")
    refetched = [r.url.path for r in calls[before:]]
    assert "/api/business" in refetched and "/api/activeService/list" in refetched


@pytest.mark.asyncio
async def test_b2_defined_but_not_recently_processed(tmp_path, calls):
    """업무 정의는 있으나 최근 처리 인스턴스가 없으면 0건 + 근거 한계 + 미해석 고지."""
    tools = _one(tmp_path, calls)
    out = await tools.apm_instance_map(business="대출")
    assert out["rows"] == []
    assert any("업무 정의 근거는 최근 처리한 인스턴스만" in x for x in out["limits"])
    assert out["_unresolved"] == ["업무명 '대출'에 해당하는 APM 인스턴스를 찾지 못했습니다"]


@pytest.mark.asyncio
async def test_b3_instance_name_only(tmp_path, calls):
    tools = _one(tmp_path, calls)
    out = await tools.apm_instance_map(business="img-was")
    assert _ids(out) == [1004]
    row = out["rows"][0]
    assert (row["match_kind"], row["match_kinds"], row["match_tier"]) == (
        "instance_text",
        ["instance_text"],
        "prefix",
    )
    assert row["search_confidence"] == "medium" and row["business_names"] == []
    assert out["business"] == {"counts": _counts(instance_text=1)}


# ── 합집합 · 부분 실패 · AND · 0건 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_union_dedupes_instances_and_lists_all_kinds(tmp_path, calls):
    """「settle」 — 도메인 이름(2001·2002·2003) · 업무 정의(2001) · 인스턴스 이름(2001)."""
    tools = _one(tmp_path, calls)
    out = await tools.apm_instance_map(business="settle")
    assert _ids(out) == [2002, 2001, 2003]  # 정렬: 소스 · 도메인 · 인스턴스 이름
    by_id = {r["instance_id"]: r for r in out["rows"]}
    assert by_id[2001]["match_kinds"] == ["domain", "business", "instance_text"]
    assert by_id[2001]["match_kind"] == "domain"
    assert by_id[2001]["business_names"] == ["settle"]
    assert by_id[2001]["match_tier"] == "prefix"
    assert by_id[2002]["match_kinds"] == ["domain"] and by_id[2002]["match_tier"] == ""
    assert out["business"] == {"counts": _counts(domain=3, business=1, instance_text=1)}
    assert len(out["rows"]) == len({(r["source_id"], r["instance_id"]) for r in out["rows"]})


@pytest.mark.asyncio
async def test_b2_http_failure_is_partial_and_other_evidence_rows_remain(tmp_path, calls):
    tools = _one(tmp_path, calls, fail={"/api/business": set()})
    out = await tools.apm_instance_map(business="settle")
    assert out["partial"] is True
    assert _ids(out) == [2002, 2001, 2003]
    assert all("business" not in r["match_kinds"] for r in out["rows"])
    assert out["business"]["counts"]["business"] == 0
    failed = [x for x in out["limits"] if x.startswith("[한계] 업무 목록 조회 실패")]
    assert failed == [
        "[한계] 업무 목록 조회 실패(도메인 1000): apm_api_error",
        "[한계] 업무 목록 조회 실패(도메인 1130): apm_api_error",
        "[한계] 업무 목록 조회 실패(도메인 1200): apm_api_error",
    ]


@pytest.mark.asyncio
async def test_trace_failure_is_partial_and_not_cached(tmp_path, calls):
    tools = _one(tmp_path, calls, fail={"/api/activeService/list": set()})
    out = await tools.apm_instance_map(business="결제")
    assert out["partial"] is True
    assert _ids(out) == [1001]  # 거래 쪽 근거는 남는다
    assert any("액티브 서비스 조회 실패(도메인 1000)" in x for x in out["limits"])
    before = len(calls)
    await tools.apm_instance_map(business="결제")
    assert "/api/activeService/list" in [r.url.path for r in calls[before:]]  # 다시 묻는다


@pytest.mark.asyncio
async def test_business_and_domain_id_are_and(tmp_path, calls):
    tools = _one(tmp_path, calls)
    out = await tools.apm_instance_map(business="settle", domain_id=1000)
    assert out["rows"] == [] and out["business"] == {"counts": _counts()}
    assert {r.url.params["domain_id"] for r in calls if r.url.path == "/api/business"} == {"1000"}
    out = await tools.apm_instance_map(business="결제", domain_id="1000")
    assert _ids(out) == [1001, 1002]
    out = await tools.apm_instance_map(business="결제", domain_id=9999)
    assert out["rows"] == []
    assert any("도메인 9999은 조회한 APM 소스의 도메인 목록에 없다" in x for x in out["limits"])
    assert len(out["_unresolved"]) == 2  # 도메인 미존재 + 업무명 미해석


@pytest.mark.asyncio
async def test_zero_rows_gives_unresolved_and_suggestions(tmp_path, calls):
    tools = _one(tmp_path, calls)
    out = await tools.apm_instance_map(business="settle-was02")
    assert out["rows"] == []
    assert out["suggestions"] == [
        {"instance_name": "settle-was01", "source_id": "default", "domain_id": 1130}
    ]
    assert out["_unresolved"] == ["업무명 'settle-was02'에 해당하는 APM 인스턴스를 찾지 못했습니다"]
    assert out["business"] == {"counts": _counts()}
    assert out["sources"][0]["status"] == "no_match"
    out = await tools.apm_instance_map(business="zz")
    assert out["suggestions"] == []


# ── 인자 검사 · 종전 모양 ────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"business": 123}, "business는 문자열이어야 한다"),
        ({"business": ["결제"]}, "business는 문자열이어야 한다"),
        ({"business": "   "}, "business가 비어 있다"),
        ({"business": "가" * 201}, "business는 200자 이하여야 한다(201자)"),
        ({"business": "결제", "query": "was01"}, "business는 query·hostname과 함께 줄 수 없다"),
        ({"business": "결제", "hostname": "was-host01"}, "business는 query·hostname과"),
    ],
)
async def test_invalid_business_is_rejected_before_http(tmp_path, calls, kwargs, needle):
    tools = _one(tmp_path, calls)
    with pytest.raises(ApmError) as exc:
        await tools.apm_instance_map(**kwargs)
    assert exc.value.code == INVALID_ARGUMENT and needle in exc.value.reason
    assert calls == []


@pytest.mark.asyncio
async def test_unspecified_business_keeps_previous_shapes(tmp_path, calls):
    tools = _one(tmp_path, calls, policy=MAPPING)
    listed = await tools.apm_instance_map()
    assert "business" not in listed and len(listed["rows"]) == 8
    assert all("match_kinds" not in r and "business_names" not in r for r in listed["rows"])
    searched = await tools.apm_instance_map(query="was01")
    assert "business" not in searched and "match_kinds" not in searched["rows"][0]
    host = await tools.apm_instance_map("was-host01", business=None)
    assert "business" not in host and "hostname" not in host["rows"][0]
    assert not [r for r in calls if r.url.path == "/api/business"]
    out = await tools.apm_instance_map(hostname="", business="결제")  # 빈 hostname은 주지 않은 것
    assert out["business"]["counts"]["business_map"] == 1


# ── MCP · 감사 ───────────────────────────────────────────────────────────────


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.mark.asyncio
async def test_mcp_schema_has_business(tmp_path, calls):
    from apm_gateway.interface.server import create_server

    mcp = create_server(_one(tmp_path, calls))
    tool = next(t for t in await mcp.list_tools() if t.name == "apm_instance_map")
    assert "business" in tool.inputSchema["properties"]
    assert "business" not in tool.inputSchema.get("required", [])
    assert "business를 주면 업무명 → 인스턴스" in (tool.description or "")


@pytest.mark.asyncio
async def test_mcp_passes_business_and_audits_counts(tmp_path, calls, caplog):
    from apm_gateway.interface.server import create_server

    mcp = create_server(_one(tmp_path, calls))
    with caplog.at_level(logging.INFO, logger="apm_gateway.audit"):
        payload = _payload(await mcp.call_tool("apm_instance_map", {"business": "settle"}))
    assert [r["instance_id"] for r in payload["rows"]] == [2002, 2001, 2003]
    line = next(r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit")
    assert "target=business:settle " in line
    assert line.endswith(" search=business(business_map:0,domain:3,business:1,instance_text:1)")


@pytest.mark.asyncio
async def test_mcp_business_is_masked_in_audit_and_payload(tmp_path, calls, caplog):
    from apm_gateway.interface.server import create_server

    mcp = create_server(_one(tmp_path, calls))
    with caplog.at_level(logging.INFO, logger="apm_gateway.audit"):
        result = await mcp.call_tool("apm_instance_map", {"business": "kim@example.com"})
    text = (result[0] if isinstance(result, tuple) else result)[0].text
    payload = json.loads(text)
    assert payload["rows"] == [] and "kim@example.com" not in text
    notes = [d["text"] for d in payload["disclosures"] if d["kind"] == "apm_unresolved_condition"]
    assert notes == ["업무명 '<email>'에 해당하는 APM 인스턴스를 찾지 못했습니다"]
    lines = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]
    assert lines and all("kim@example.com" not in x for x in lines)
    assert "target=business:<email>" in lines[0]
    assert "search=business(business_map:0,domain:0,business:0,instance_text:0)" in lines[0]


# ── 허용목록 `/api/business` (N-5 · D-290 ④) ──────────────────────────────────


def test_allowlist_business_requires_domain_id():
    from jennifer_catalog import ALLOWED as COPY

    ep = check_request("GET", "/api/business", {"domain_id": "1000"})
    assert ep.template == "/api/business" and ep.required == ("domain_id",) and ep.optional == ()
    assert COPY["/api/business"].required == ("domain_id",)
    assert list(ALLOWED).index("/api/business") == list(COPY).index("/api/business")
    with pytest.raises(NotAllowedError, match="필수 쿼리 키 누락: domain_id"):
        check_request("GET", "/api/business", {})
    with pytest.raises(NotAllowedError, match="허용 밖 쿼리 키"):
        check_request("GET", "/api/business", {"domain_id": "1000", "business_id": "7"})
    with pytest.raises(NotAllowedError):
        check_request("POST", "/api/business", {"domain_id": "1000"})


# 업무 지표 두 경로는 D-296(D-290 ④ 부기)이 허용으로 바꿨다 — `plans/134` W4가 열면 여기서 뺀다
@pytest.mark.parametrize(
    "path",
    ["/api/realtime/business", "/api/dbmetrics/business", "/api/business.xml", "/api/business/"],
)
def test_other_business_paths_are_still_rejected(path):
    with pytest.raises(NotAllowedError):
        check_request("GET", path, {"domain_id": "1000"})


@pytest.mark.asyncio
async def test_business_via_mock_server_uses_allowlisted_path(
    tmp_path, synthetic_dir, mock_server_factory
):
    """목 서버(합성 업무 응답) 종단 — 「결제」(업무 9)는 액티브 서비스·거래가 처리한 1001."""
    base, state = mock_server_factory(synthetic_dir)
    tools, _ = make_tools(base, extra={"APM_SPOOL_DIR": str(tmp_path / "spool")})
    out = await tools.apm_instance_map(business="결제")
    assert [(r["instance_id"], r["business_names"]) for r in out["rows"]] == [(1001, ["결제"])]
    business_hits = [h for h in state.hits if h["path"] == "/api/business"]
    assert [(h["allowlisted"], h["query"], h["status"]) for h in business_hits] == [
        (True, {"domain_id": "1000"}, 200)
    ]
    assert all(h["allowlisted"] for h in state.hits)
