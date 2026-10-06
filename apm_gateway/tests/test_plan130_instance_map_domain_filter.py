"""`apm_instance_map`의 `domain_id` 필터 — 「도메인 아이디가 1130인 인스턴스 리스트」 오답 교정.

종전에는 도메인 조건을 받을 인자가 없어 전 도메인의 인스턴스를 돌려줬고, 고르기는 답변 LLM 몫이라
다른 도메인 인스턴스가 섞였다. 이제 게이트웨이가 인벤토리를 도메인으로 거른다.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import make_tools, synthetic_handler

from apm_gateway.domain.errors import ApmError


def _inst(instance_id: int, name: str, host: str) -> dict:
    return {
        "instanceId": instance_id,
        "name": name,
        "hostName": host,
        "ipAddress": "10.0.0.11",
        "platform": "JAVA",
        "status": "RUNNING",
        "version": "5.6.5",
    }


_BY_DOMAIN = {
    "1000": [_inst(1001, "was01_a", "was-host01"), _inst(1003, "api02_main", "")],
    "1130": [_inst(2001, "was_1130_a", "was-host01"), _inst(2002, "was_1130_b", "was-host02")],
}


def _instances(request: httpx.Request) -> httpx.Response:
    rows = _BY_DOMAIN.get(request.url.params.get("domain_id") or "", [])
    return httpx.Response(200, json={"result": rows})


@pytest.fixture
def tools():
    domains = {"result": [{"domainId": 1000, "name": "order"}, {"domainId": 1130, "name": "pay"}]}
    handler = synthetic_handler(
        override={"/api/domain": httpx.Response(200, json=domains), "/api/instance": _instances}
    )
    out, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    return out


@pytest.mark.asyncio
async def test_listing_without_domain_keeps_all_domains(tools):
    out = await tools.apm_instance_map()
    assert {r["domain_id"] for r in out["rows"]} == {1000, 1130}


@pytest.mark.asyncio
@pytest.mark.parametrize("domain_id", [1130, "1130"])
async def test_listing_filters_to_requested_domain(tools, domain_id):
    out = await tools.apm_instance_map(domain_id=domain_id)
    assert [r["instance_id"] for r in out["rows"]] == [2001, 2002]
    assert {r["domain_id"] for r in out["rows"]} == {1130}
    assert "_unresolved" not in out


@pytest.mark.asyncio
async def test_hostname_and_domain_filter_together(tools):
    out = await tools.apm_instance_map("was-host01", domain_id=1130)
    assert [r["instance_id"] for r in out["rows"]] == [2001]


@pytest.mark.asyncio
async def test_unknown_domain_is_reported_not_silent(tools):
    out = await tools.apm_instance_map(domain_id=9999)
    assert out["rows"] == []
    assert any("도메인 9999" in x and "1000, 1130" in x for x in out["limits"])
    assert out["_unresolved"] == ["도메인 ID 9999는 제니퍼 도메인 목록에 없습니다(있는 도메인: 1000, 1130)"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [-1, "abc", True, 1.5])
async def test_invalid_domain_id_is_rejected(tools, bad):
    with pytest.raises(ApmError) as exc:
        await tools.apm_instance_map(domain_id=bad)
    assert exc.value.code == "invalid_argument"


@pytest.mark.asyncio
async def test_mcp_tool_exposes_and_passes_domain_id(tools):
    import json

    from apm_gateway.interface.server import create_server

    mcp = create_server(tools)
    schema = {t.name: t.inputSchema["properties"] for t in await mcp.list_tools()}
    assert "domain_id" in schema["apm_instance_map"]
    result = await mcp.call_tool("apm_instance_map", {"domain_id": 1130})
    content = result[0] if isinstance(result, tuple) else result
    payload = json.loads(content[0].text)
    assert {r["domain_id"] for r in payload["rows"]} == {1130}
