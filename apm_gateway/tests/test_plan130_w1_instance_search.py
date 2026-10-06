"""plans/130 W1 — 인스턴스 이름 검색(`apm_instance_map(query=…)` · N-1)과 인스턴스 이름으로 부르기
(`instance_name` · N-3).

목 서버 2개(bank·common)를 한 `httpx.MockTransport`에서 호스트로 가른다. 픽스처: 두 소스에 같은
이름(`shared_was`) · hostName이 빈 인스턴스(`api02_main`) · 한글 설명(「이미지 업무 WAS#1」) ·
도메인 2개(1000·1130).
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from apm_gateway.domain.errors import ApmError
from conftest import NOW_S, TOKEN, make_tools, synthetic_handler

BANK_URL = "http://bank.test"
COMMON_URL = "http://common.test"


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
            _inst(1005, "old-was01x", "old-host"),
        ],
    ),
    1130: (
        "image",
        [_inst(2001, "pay_was_1", "pay-host01"), _inst(2002, "shared_was", "shr-host01")],
    ),
}
COMMON = {
    1000: (
        "order",
        [_inst(3001, "shared_was", "shr-host02"), _inst(3002, "batch-runner", "batch01")],
    ),
}


def _handler(data: dict, calls: list[str]):
    """도메인·인벤토리·실시간(요청한 인스턴스만)을 `data`로 답하는 합성 핸들러."""

    def instances(request: httpx.Request) -> httpx.Response:
        _, rows = data.get(int(request.url.params["domain_id"]), ("", []))
        return httpx.Response(200, json={"result": rows})

    def realtime(request: httpx.Request) -> httpx.Response:
        did = int(request.url.params["domain_id"])
        ids = {int(x) for x in (request.url.params.get("instance_id") or "").split(",") if x}
        _, rows = data.get(did, ("", []))
        result = [
            {
                "domainId": did,
                "instanceId": r["instanceId"],
                "instanceName": r["name"],
                "responseTime": 100.0,
                "tps": 10.0,
                "activeService": 1,
            }
            for r in rows
            if not ids or r["instanceId"] in ids
        ]
        return httpx.Response(200, json={"result": result})

    domains = {"result": [{"domainId": d, "name": n} for d, (n, _) in data.items()]}
    inner = synthetic_handler(
        override={
            "/api/domain": httpx.Response(200, json=domains),
            "/api/instance": instances,
            "/api/realtime/instance": realtime,
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.url.host}{request.url.path}")
        return await inner(request)

    return handler


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def one(tmp_path, calls):
    """소스 하나(bank 데이터) — conftest `make_tools`."""
    out, _ = make_tools(
        BANK_URL,
        extra={"APM_SPOOL_DIR": str(tmp_path / "spool")},
        transport=httpx.MockTransport(_handler(BANK, calls)),
    )
    return out


@pytest.fixture
def two(tmp_path, calls):
    """소스 둘(bank → common 선언 순서)."""
    from apm_gateway.application.sources import build_source_set
    from apm_gateway.application.tools import ApmTools

    from apm_gateway.config import load_config

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
        }
    )
    sources = build_source_set(cfg, transport=httpx.MockTransport(route))
    return ApmTools(sources, cfg, clock=lambda: NOW_S)


def _ids(out: dict) -> list[int]:
    return [r["instance_id"] for r in out["rows"]]


# ── N-1 검색 단계 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exact_tier_ignores_case(one):
    out = await one.apm_instance_map(query="WAS01_A")
    assert _ids(out) == [1001]
    row = out["rows"][0]
    assert row["match_kind"] == "instance_name"
    assert row["match_tier"] == "exact"
    assert row["search_confidence"] == "high"
    assert row["hostname"] == "was-host01"
    assert (row["match_confidence"], row["match_reason"]) == ("high", "host_name")
    assert out["search"] == {
        "tier": "exact",
        "counts": {"exact": 1, "normalized": 0, "prefix": 0, "contains": 0},
    }
    assert "suggestions" not in out and "_unresolved" not in out


@pytest.mark.asyncio
async def test_normalized_tier_drops_separators_and_spaces(one):
    out = await one.apm_instance_map(query="img was 01")
    assert _ids(out) == [1004]
    assert out["rows"][0]["match_tier"] == "normalized"
    assert out["rows"][0]["search_confidence"] == "high"


@pytest.mark.asyncio
async def test_prefix_tier_wins_over_contains(one):
    """「was01」 — 앞부분(was01_a·was01_b) 2건이 포함(old-was01x·IMG-WAS-01) 2건보다 앞선다."""
    out = await one.apm_instance_map(query="was01")
    assert _ids(out) == [1001, 1002]
    assert {r["match_tier"] for r in out["rows"]} == {"prefix"}
    assert {r["search_confidence"] for r in out["rows"]} == {"medium"}
    assert out["search"]["counts"] == {"exact": 0, "normalized": 0, "prefix": 2, "contains": 2}


@pytest.mark.asyncio
async def test_prefix_needs_separator_boundary(one):
    """「was0」 뒤가 숫자라 앞부분이 아니다 — 포함 단계로 4건."""
    out = await one.apm_instance_map(query="was0")
    assert out["search"]["tier"] == "contains"
    assert sorted(_ids(out)) == [1001, 1002, 1004, 1005]


@pytest.mark.asyncio
async def test_contains_matches_korean_description(one):
    out = await one.apm_instance_map(query="이미지 업무")
    assert _ids(out) == [1004]
    assert out["rows"][0]["match_tier"] == "contains"
    assert out["rows"][0]["description"] == "이미지 업무 WAS#1"


@pytest.mark.asyncio
async def test_contains_needs_three_normalized_chars(one):
    short = await one.apm_instance_map(query="ap")
    assert short["rows"] == []
    assert short["search"]["counts"] == {"exact": 0, "normalized": 0, "prefix": 0, "contains": 0}
    enough = await one.apm_instance_map(query="api")
    assert _ids(enough) == [1003]
    assert enough["search"]["tier"] == "contains"


@pytest.mark.asyncio
async def test_similar_names_only_in_suggestions(one):
    out = await one.apm_instance_map(query="was01_c")
    assert out["rows"] == [] and out["row_count"] == 0
    assert out["search"]["tier"] is None
    assert out["suggestions"] == [
        {"instance_name": "was01_a", "source_id": "default", "domain_id": 1000},
        {"instance_name": "was01_b", "source_id": "default", "domain_id": 1000},
    ]
    assert out["_unresolved"] == [
        "인스턴스 이름 'was01_c'과(와) 일치하는 인스턴스를 찾지 못했습니다"
    ]


@pytest.mark.asyncio
async def test_search_uses_cached_inventory_only(one, calls):
    await one.apm_instance_map(query="was01")
    before = len(calls)
    await one.apm_instance_map(query="이미지")
    assert len(calls) == before  # 인벤토리 캐시 — 새 HTTP 없음


@pytest.mark.asyncio
async def test_same_name_in_two_sources_gives_two_rows(two):
    out = await two.apm_instance_map(query="shared_was")
    assert [(r["source_id"], r["domain_id"], r["hostname"]) for r in out["rows"]] == [
        ("bank", 1130, "shr-host01"),
        ("common", 1000, "shr-host02"),
    ]
    assert out["search"]["counts"]["exact"] == 2


@pytest.mark.asyncio
async def test_query_and_domain_are_and(two):
    out = await two.apm_instance_map(query="shared_was", domain_id=1130)
    assert [(r["source_id"], r["instance_id"]) for r in out["rows"]] == [("bank", 2002)]
    assert {s["source_id"]: s["status"] for s in out["sources"]} == {
        "bank": "ok",
        "common": "no_match",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"query": "was01", "hostname": "was-host01"}, "hostname과 query는 함께 줄 수 없다"),
        ({"query": "   "}, "query가 비어 있다"),
        ({"query": "a" * 201}, "200자 이하"),
        ({"query": "a" * 1_000_000}, "200자 이하"),
        ({"query": 123}, "문자열"),
    ],
)
async def test_invalid_query_is_rejected_before_http(one, calls, kwargs, needle):
    with pytest.raises(ApmError) as exc:
        await one.apm_instance_map(**kwargs)
    assert exc.value.code == "invalid_argument"
    assert needle in exc.value.reason
    assert calls == []


@pytest.mark.asyncio
async def test_unspecified_query_keeps_listing_and_hostname_shapes(one):
    listing = await one.apm_instance_map()
    assert "search" not in listing and "suggestions" not in listing
    assert "match_kind" not in listing["rows"][0]
    by_host = await one.apm_instance_map("was-host01")
    assert _ids(by_host) == [1001, 1002]
    assert "search" not in by_host and "match_kind" not in by_host["rows"][0]


# ── N-3 인스턴스 이름으로 부르기 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_by_instance_name_with_empty_hostname(one):
    out = await one.apm_app_health(instance_name="  API02_MAIN ")
    assert _ids(out) == [1003]
    assert out["rows"][0]["hostname"] == ""
    res = out["instance_resolution"]
    assert (res["confidence"], res["reason"]) == ("high", "instance_name")
    assert res["instance_refs"] == [
        {"source_id": "default", "domain_id": 1000, "instance_id": 1003, "hostname": ""}
    ]


@pytest.mark.asyncio
async def test_instance_name_across_two_sources_returns_all(two):
    out = await two.apm_app_health(instance_name="shared_was")
    assert [(r["source_id"], r["instance_id"], r["hostname"]) for r in out["rows"]] == [
        ("bank", 2002, "shr-host01"),
        ("common", 3001, "shr-host02"),
    ]
    assert [r["hostname"] for r in out["instance_resolution"]["instance_refs"]] == [
        "shr-host01",
        "shr-host02",
    ]


@pytest.mark.asyncio
async def test_partial_instance_name_is_unresolved(one):
    with pytest.raises(ApmError) as exc:
        await one.apm_runtime_health(instance_name="api02")
    assert exc.value.code == "instance_unresolved"
    assert "apm_instance_map(query=" in exc.value.reason


@pytest.mark.asyncio
async def test_hostname_and_instance_name_are_and(one):
    out = await one.apm_app_health("was-host01", instance_name="WAS01_B")
    assert _ids(out) == [1002]
    with pytest.raises(ApmError) as exc:
        await one.apm_app_health("was-host01", instance_name="api02_main")
    assert exc.value.code == "instance_unresolved"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{}, {"hostname": "  ", "instance_name": ""}])
async def test_required_target_missing_is_invalid(one, kwargs):
    with pytest.raises(ApmError) as exc:
        await one.apm_app_health(**kwargs)
    assert exc.value.code == "invalid_argument"
    assert "hostname 또는 instance_name" in exc.value.reason


@pytest.mark.asyncio
async def test_hostname_mode_rows_unchanged(one):
    out = await one.apm_app_health("was-host01")
    assert all("hostname" not in r for r in out["rows"])
    assert all("hostname" not in r for r in out["instance_resolution"]["instance_refs"])
    assert out["instance_resolution"]["reason"] == "host_name"


@pytest.mark.asyncio
async def test_manage_tools_take_instance_name(one):
    from apm_gateway.application.manage_tools import ManageTools

    manage = ManageTools(one)
    with pytest.raises(ApmError) as exc:
        await manage.apm_config("loaded_classes")
    assert exc.value.code == "invalid_argument"
    assert "hostname 또는 instance_name" in exc.value.reason
    with pytest.raises(ApmError) as exc:
        await manage.apm_active_detail(domain_id=1000, txid="1", instance_name="pay_was_1")
    assert exc.value.code == "profile_ref_mismatch"
    assert "instance_name 'pay_was_1'" in exc.value.reason


# ── MCP 등록·감사 ────────────────────────────────────────────────────────────


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.mark.asyncio
async def test_mcp_schema_exposes_query_and_instance_name(one):
    from apm_gateway.interface.server import create_server

    mcp = create_server(one)
    schemas = {t.name: t.inputSchema for t in await mcp.list_tools()}
    assert "query" in schemas["apm_instance_map"]["properties"]
    named = sorted(n for n, s in schemas.items() if "instance_name" in s["properties"])
    assert len(named) == 16
    for name in named:
        assert "hostname" in schemas[name]["properties"], name
        assert "hostname" not in schemas[name].get("required", []), name
    assert set(schemas["apm_period_compare"]["required"]) == {
        "current_start",
        "current_end",
        "baseline_start",
        "baseline_end",
    }


@pytest.mark.asyncio
async def test_mcp_passes_instance_name(one):
    from apm_gateway.interface.server import create_server

    mcp = create_server(one)
    payload = _payload(await mcp.call_tool("apm_app_health", {"instance_name": "api02_main"}))
    assert [(r["instance_id"], r["hostname"]) for r in payload["rows"]] == [(1003, "")]


@pytest.mark.asyncio
async def test_mcp_query_audit_carries_tier_counts(two, caplog):
    from apm_gateway.interface.server import create_server

    mcp = create_server(two)
    with caplog.at_level(logging.INFO, logger="apm_gateway.audit"):
        payload = _payload(await mcp.call_tool("apm_instance_map", {"query": "shared_was"}))
    assert payload["row_count"] == 2
    line = next(r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit")
    assert "target=query:shared_was" in line
    assert line.endswith(" search=exact(exact:2,normalized:0,prefix:0,contains:0)")


@pytest.mark.asyncio
async def test_mcp_query_is_masked_in_audit_and_payload(one, caplog):
    from apm_gateway.interface.server import create_server

    mcp = create_server(one)
    with caplog.at_level(logging.INFO, logger="apm_gateway.audit"):
        result = await mcp.call_tool("apm_instance_map", {"query": "kim@example.com"})
    text = (result[0] if isinstance(result, tuple) else result)[0].text
    payload = json.loads(text)
    assert payload["rows"] == []
    assert "kim@example.com" not in text
    # MCP 응답에서는 `_unresolved`가 의무 고지(`apm_unresolved_condition`)로 바뀐다
    notes = [d["text"] for d in payload["disclosures"] if d["kind"] == "apm_unresolved_condition"]
    assert notes == ["인스턴스 이름 '<email>'과(와) 일치하는 인스턴스를 찾지 못했습니다"]
    lines = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]
    assert lines and all("kim@example.com" not in x for x in lines)
    assert "search=none(" in lines[0]
