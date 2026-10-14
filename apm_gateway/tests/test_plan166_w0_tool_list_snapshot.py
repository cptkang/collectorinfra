"""plans/166 W0 특성화 스냅샷 — 단계 2(plans/163)만 갱신 가능, 단계 3 이후 갱신 금지(갱신이 필요하면
리팩터가 아니다).

MCP 도구 목록(스멜 M1·M2 — `interface/server.py` `register_tools` · 하위 등록 3종
`manage_server`·`scope_server`·`fleet_server`)을 `await mcp.list_tools()`의 도구마다
`name`·`description`·`inputSchema` 전문으로 고정한다(등록 순서 그대로 · 바이트 수준).

구성:
- 기본 — 단일 소스(`conftest.make_tools`) · 이벤트 버퍼 없음.
- 대조 — 다중 소스 2개(`fleet_fake.FakeFleet.env`) · 이벤트 버퍼 있음. 도구 등록은 설정·기능
  플래그와 무관하게 무조건이라(허용 목록·소스 수·버퍼는 노출을 바꾸지 않는다) 기본과 같은
  스냅샷이어야 한다 — 다르면 노출이 설정에 따라 갈리기 시작한 것이다.

갱신: `UPDATE_PLAN162_SNAPSHOT=1`일 때만 파일을 쓴다. 네트워크 0(`httpx.MockTransport`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from apm_gateway.application.event_buffer import EventBuffer
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.interface.server import create_server
from conftest import NOW_S, make_tools, synthetic_handler
from fleet_fake import FakeFleet
from mcp.server.fastmcp import FastMCP

from apm_gateway.config import load_config

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "plan166_w0" / "tool_list.json"
UPDATE = os.environ.get("UPDATE_PLAN162_SNAPSHOT") == "1"


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


async def _tool_list(mcp: FastMCP) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
        for t in await mcp.list_tools()
    ]


def _default_server(tmp_path: Path) -> FastMCP:
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(synthetic_handler()),
        extra={"APM_SPOOL_DIR": str(tmp_path / "spool")},
    )
    return create_server(tools)


def _multi_source_server(tmp_path: Path) -> FastMCP:
    fake = FakeFleet(2)
    cfg = load_config(fake.env(APM_SPOOL_DIR=str(tmp_path / "spool")))
    sources = build_source_set(cfg, transport=httpx.MockTransport(fake.handler))
    tools = ApmTools(sources, cfg, clock=lambda: NOW_S)
    return create_server(tools, event_buffer=EventBuffer(clock=lambda: NOW_S))


COMPARE = pytest.mark.skipif(UPDATE, reason="UPDATE_PLAN162_SNAPSHOT=1 — 채록 모드(대조 생략)")
RECORD = pytest.mark.skipif(not UPDATE, reason="채록은 UPDATE_PLAN162_SNAPSHOT=1일 때만")
MISSING = f"스냅샷이 없다: {SNAPSHOT} — UPDATE_PLAN162_SNAPSHOT=1로 최초 채록하라"


@RECORD
@pytest.mark.asyncio
async def test_record_snapshot(tmp_path: Path) -> None:
    """채록 전용(평시 skip) — 기본 구성의 목록을 한 번에 쓴다."""
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(_dump(await _tool_list(_default_server(tmp_path))), encoding="utf-8")


@COMPARE
@pytest.mark.asyncio
async def test_tool_list_matches_snapshot(tmp_path: Path) -> None:
    assert SNAPSHOT.exists(), MISSING
    actual = await _tool_list(_default_server(tmp_path))
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert [t["name"] for t in actual] == [t["name"] for t in expected]
    for got, want in zip(actual, expected, strict=True):
        assert got == want, got["name"]
    assert _dump(actual) == SNAPSHOT.read_text(encoding="utf-8")


@COMPARE
@pytest.mark.asyncio
async def test_tool_list_does_not_depend_on_sources_or_event_buffer(tmp_path: Path) -> None:
    assert SNAPSHOT.exists(), MISSING
    actual = await _tool_list(_multi_source_server(tmp_path))
    assert _dump(actual) == SNAPSHOT.read_text(encoding="utf-8")
