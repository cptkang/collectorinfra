"""MCP 서버 — 도구 표면 · 오류 계약 · 감사 · 주체별 Bearer.

plans/87 §0.7 (3) · §5.8 · R-19 · D-125 · plans/134 W0-B(작업 도구 3종 · 주체 토큰 — D-299 ③이
D-195 ①의 APM 도구 8종 상한을 폐지했다).
"""

from __future__ import annotations

import json
import logging

import pytest
from conftest import TOKEN, make_tools
from starlette.testclient import TestClient

DATA_TOOLS = {
    "apm_instance_map",
    "apm_app_health",
    "apm_runtime_health",
    "apm_resource_pool",
    "apm_slow_transactions",
    "apm_active_services",
    "apm_events",
    "apm_transaction_profile",
    # plans/134 W2(N-5~N-7)
    "apm_status_stats",
    "apm_metrics",
    "apm_source_changes",
    # plans/134 W5·W6(GUID 추적 · 변경 전후 · 기간 비교)
    "apm_transaction_trace",
    "apm_change_impact",
    "apm_period_compare",
    # plans/134 W7(N-15·N-16 — 관리·민감 조회)
    "apm_config",
    "apm_environment",
    "apm_users",
    "apm_active_detail",
    # plans/134 W3·W4(N-9·N-12 — 서비스·업무)
    "apm_service_status",
    "apm_business",
    # plans/134 W3(N-10 — 전 대상 순위·이벤트)
    "apm_fleet",
}
JOB_TOOLS = {"apm_job_status", "apm_job_cancel", "apm_job_read"}
EXPECTED_TOOLS = DATA_TOOLS | JOB_TOOLS | {"gateway_health"}


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
async def test_tool_surface_is_data_job_and_health_tools(server):
    """데이터 도구 21종(W2 +3 · W5·W6 +3 · W7 +4 · W3 +1 · W3·W4 +2) + 작업 도구 3종 + 헬스
    (plans/134 W0-B) — 숫자 상한은 D-299 ③이 폐지했다."""
    from apm_gateway.interface.server import register_tools
    from mcp.server.fastmcp import FastMCP

    mcp, tools = server
    names = {t.name for t in await mcp.list_tools()}
    assert names == EXPECTED_TOOLS
    assert set(register_tools(FastMCP("x"), tools)) == EXPECTED_TOOLS


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
    """주체별 Bearer 미들웨어가 원본의 헤더 판독·401 응답 문장을 그대로 쓴다(R-21 드리프트 감시 —
    소스 비교). 토큰 비교는 주체별 토큰으로 넓혀 원본과 다르다(plans/134 W0-B §3.6)."""
    from pathlib import Path

    here = Path(__file__).resolve().parents[2]
    original = (here / "mcp_server" / "mcp_server" / "server.py").read_text(encoding="utf-8")
    copy = (here / "apm_gateway" / "apm_gateway" / "interface" / "server.py").read_text(
        encoding="utf-8"
    )
    for needle in (
        'if scope["type"] != "http"',
        'provided = headers.get(b"authorization", b"").decode("latin-1")',
        'body = json.dumps({"error": "unauthorized"}, ensure_ascii=False).encode("utf-8")',
        '"status": 401,',
    ):
        assert needle in original and needle in copy, needle


# ── 주체별 Bearer (plans/134 W0-B §3.6) ──────────────────────


def _echo_principal_app():
    from apm_gateway.interface.server import PRINCIPAL_SCOPE_KEY

    async def app(scope, receive, send):
        body = str(scope.get(PRINCIPAL_SCOPE_KEY)).encode()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})

    return app


def test_principal_middleware_maps_token_to_principal():
    from apm_gateway.interface.server import BearerPrincipalMiddleware

    app = BearerPrincipalMiddleware(
        _echo_principal_app(), {"chat": "tok-chat", "investigation": "tok-inv"}
    )
    client = TestClient(app)
    assert client.get("/", headers={"Authorization": "Bearer tok-chat"}).text == "chat"
    assert client.get("/", headers={"Authorization": "Bearer tok-inv"}).text == "investigation"
    for headers in ({}, {"Authorization": "Bearer tok-chatx"}, {"Authorization": "tok-chat"}):
        denied = client.get("/", headers=headers)
        assert denied.status_code == 401 and denied.json() == {"error": "unauthorized"}
    open_app = TestClient(BearerPrincipalMiddleware(_echo_principal_app(), {}))
    assert open_app.get("/").text == "anonymous"  # 인증 꺼짐 = 주체 anonymous


def test_build_asgi_app_single_token_is_default_principal(server):
    from apm_gateway.interface.server import build_asgi_app

    mcp, _ = server
    client = TestClient(build_asgi_app(mcp, {"default": "gw-bearer"}))
    assert client.get("/sse", headers={"Authorization": "Bearer other"}).status_code == 401


@pytest.mark.asyncio
async def test_data_tools_accept_owner_and_wait_seconds(server):
    mcp, _ = server
    for tool in await mcp.list_tools():
        props = tool.inputSchema["properties"]
        if tool.name in DATA_TOOLS:
            assert {"owner", "wait_seconds"} <= set(props), tool.name
        elif tool.name in JOB_TOOLS:
            assert "job_id" in props and "owner" in props, tool.name
