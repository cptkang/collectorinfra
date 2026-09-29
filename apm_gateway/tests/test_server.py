"""MCP 서버 — 도구 표면 · 오류 계약 · 감사 · 정적 Bearer.

plans/87 §0.7 (3) · §5.8 · R-19 · D-125.
"""

from __future__ import annotations

import json
import logging

import pytest
from conftest import TOKEN, make_tools
from starlette.testclient import TestClient

EXPECTED_TOOLS = {
    "apm_instance_map",
    "apm_app_health",
    "apm_runtime_health",
    "apm_resource_pool",
    "apm_slow_transactions",
    "apm_active_services",
    "apm_events",
    "apm_transaction_profile",
    "gateway_health",
}


def _text(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


@pytest.fixture
def server(mock_server_factory, synthetic_dir):
    from apm_gateway.interface.server import create_server

    base, _ = mock_server_factory(synthetic_dir, "connected")
    tools, _ = make_tools(base)
    return create_server(tools), tools


@pytest.mark.asyncio
async def test_tool_surface_is_eight_apm_tools_plus_health(server):
    mcp, _ = server
    names = {t.name for t in await mcp.list_tools()}
    assert names == EXPECTED_TOOLS
    assert len([n for n in names if n.startswith("apm_")]) == 8  # P4 상한


@pytest.mark.asyncio
async def test_tool_descriptions_are_vendor_neutral(server):
    mcp, _ = server
    for tool in await mcp.list_tools():
        text = (tool.description or "").lower()
        assert "jennifer" not in text and "제니퍼" not in text, tool.name


@pytest.mark.asyncio
async def test_call_tool_ok_and_audit_carries_ids(server, caplog):
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    mcp, _ = server
    out = _text(
        await mcp.call_tool(
            "apm_runtime_health",
            {"hostname": "was-host01", "investigation_id": "inv-77", "thread_id": "th-9"},
        )
    )
    assert out["tool"] == "apm_runtime_health" and out["row_count"] == 2
    line = next(r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit")
    assert (
        "tool=apm_runtime_health" in line
        and "investigation_id=inv-77" in line
        and "thread_id=th-9" in line
    )
    assert "api_calls=" in line and TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_call_tool_error_is_contract_json(server, caplog):
    caplog.set_level(logging.WARNING, logger="apm_gateway.audit")
    mcp, _ = server
    out = _text(await mcp.call_tool("apm_app_health", {"hostname": "nohost"}))
    assert out["error"] == "instance_unresolved" and out["source_kind"] == "apm_api"
    assert "error=instance_unresolved" in caplog.text
    bad = _text(await mcp.call_tool("apm_events", {"hostname": "was-host01", "level": "bogus"}))
    assert bad["error"] == "invalid_argument"


def test_bearer_middleware_rejects_without_token(server):
    from apm_gateway.interface.server import build_asgi_app

    mcp, _ = server
    app = build_asgi_app(mcp, "gw-bearer")
    client = TestClient(app)
    assert client.get("/sse").status_code == 401
    assert client.get("/sse", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_bearer_middleware_matches_mcp_server_copy():
    """복제한 Bearer 미들웨어가 원본과 같은 판정 문장을 쓴다(R-21 드리프트 감시 — 소스 비교)."""
    from pathlib import Path

    here = Path(__file__).resolve().parents[2]
    original = (here / "mcp_server" / "mcp_server" / "server.py").read_text(encoding="utf-8")
    copy = (here / "apm_gateway" / "apm_gateway" / "interface" / "server.py").read_text(
        encoding="utf-8"
    )
    for needle in (
        'if scope["type"] != "http" or self.token is None:',
        'provided = headers.get(b"authorization", b"").decode("latin-1")',
        'if provided != f"Bearer {self.token}":',
        '"status": 401,',
    ):
        assert needle in original and needle in copy, needle
