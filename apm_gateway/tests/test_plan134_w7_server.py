"""plans/134 W7 MCP 표면 — 도구 4종의 인자 이름(본체·조사 소비자 계약) · 작업 실행 · 감사 대상에
계정 ID 없음 · 설명은 벤더 중립.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from apm_gateway.interface.manage_server import TOOL_NAMES
from apm_gateway.interface.server import create_server
from conftest import DOMAIN, make_tools
from test_plan134_w7_tools import w7_handler

COMMON = {"investigation_id", "thread_id", "owner", "wait_seconds"}
EXPECTED_ARGS = {
    "apm_config": {
        "kind",
        "hostname",
        "source_ids",
        "rule_type",
        "target",
        "error_type",
        "process_id",
        "search",
        "instance_name",  # plans/130 N-3
    }
    | COMMON,
    "apm_environment": {"hostname", "instance_name", "source_ids", "scope", "key"} | COMMON,
    "apm_users": {"user_id", "source_ids"} | COMMON,
    "apm_active_detail": {
        "domain_id",
        "txid",
        "session_id",
        "thread_hash",
        "source_id",
        "hostname",
        "instance_name",
    }
    | COMMON,
}
REQUIRED = {
    "apm_config": ["kind"],
    "apm_environment": [],
    "apm_users": [],
    "apm_active_detail": ["domain_id", "txid"],
}


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _server(tmp_path, handler):
    """결과 파일은 테스트 임시 디렉터리에만(기본 스풀 `apm_gateway/var/`를 만들지 않는다)."""
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(handler),
        extra={"APM_SPOOL_DIR": str(tmp_path / "spool")},
    )
    return create_server(tools)


@pytest.fixture
def mcp(tmp_path):
    return _server(tmp_path, w7_handler())


@pytest.mark.asyncio
async def test_w7_tool_argument_names_are_the_contract(mcp):
    schemas = {t.name: t.inputSchema for t in await mcp.list_tools()}
    assert set(TOOL_NAMES) <= set(schemas)
    for name in TOOL_NAMES:
        assert set(schemas[name]["properties"]) == EXPECTED_ARGS[name], name
        assert sorted(schemas[name].get("required", [])) == REQUIRED[name], name


@pytest.mark.asyncio
async def test_w7_descriptions_are_vendor_neutral(mcp):
    for tool in await mcp.list_tools():
        if tool.name in TOOL_NAMES:
            text = json.dumps(tool.model_dump(), ensure_ascii=False).lower()
            assert "jennifer" not in text and "제니퍼" not in text, tool.name


@pytest.mark.asyncio
async def test_w7_tools_run_as_jobs_and_audit_hides_account_id(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    gate = asyncio.Event()
    inner = w7_handler()

    async def handler(request: httpx.Request) -> httpx.Response:
        if "color-range-boundary" in request.url.path:
            await gate.wait()  # 오래 걸리는 조회 — wait_seconds 안에 못 끝나 작업으로 승격
        return await inner(request)

    mcp = _server(tmp_path, handler)
    out = _json(await mcp.call_tool("apm_users", {"user_id": "kimcs01", "owner": "o"}))
    assert out["rows"][0]["user_id"] == "k***" and out["total_row_count"] == 1
    line = next(r.getMessage() for r in caplog.records if "tool=apm_users" in r.getMessage())
    assert "target=*" in line and "kimcs01" not in caplog.text
    handle = _json(
        await mcp.call_tool(
            "apm_config", {"kind": "color_boundary", "owner": "o", "wait_seconds": 0.01}
        )
    )
    job_id = handle["job"]["job_id"]
    assert handle["job"]["state"] == "running" and handle["rows"] == []
    gate.set()
    for _ in range(200):
        status = _json(await mcp.call_tool("apm_job_status", {"job_id": job_id, "owner": "o"}))
        if status["job"]["state"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.01)
    assert status["job"]["state"] == "completed" and status["total_row_count"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("txid", ["-8001", -8001, "8001"])
async def test_active_detail_accepts_active_ref_txid_as_string_or_int(mcp, txid):
    out = _json(await mcp.call_tool("apm_active_detail", {"domain_id": DOMAIN, "txid": txid}))
    assert "error" not in out and out["rows"][0]["txid"] == str(txid)


@pytest.mark.asyncio
async def test_w7_errors_are_contract_json(mcp):
    bad = _json(await mcp.call_tool("apm_config", {"kind": "nope"}))
    assert bad["error"] == "invalid_argument" and bad["tool"] == "apm_config"
    bad = _json(await mcp.call_tool("apm_environment", {"scope": "OS"}))
    assert bad["error"] == "invalid_argument"
