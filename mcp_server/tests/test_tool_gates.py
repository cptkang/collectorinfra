"""폴스타 게이트 2종 동작 테스트 (Plan 67 Phase 0 ⑬/⑭).

- ⑬ `execute_sql`의 폴스타 도메인 deny(D-022/D-028)가 `polestar_domain_guard`로 게이트되는지.
  범용 SQL 실행 경로가 DB 종류와 무관하게 폴스타 스키마 전제를 강제하던 구조를 옵트아웃 가능하게 했다.
- ⑭ 폴스타 고수준 도구 8종 등록이 `expose_polestar_tools`로 게이트되는지.

DB 연결 없이 동작한다 — pool_manager는 최소 대역(fake)으로 대체하고, 도구 등록 여부는
FastMCP `list_tools()` 발견 표면으로 확인한다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

try:
    from mcp_server.config import AppServerConfig, ServerConfig
    from mcp_server.server import create_server
    from mcp_server.tools import register_tools
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp 패키지가 설치되지 않음")

# 폴스타 도메인 deny 대상 SQL (D-028 — CMM_VENDOR lookup 금지)
_POLESTAR_DENIED_SQL = "SELECT name FROM CMM_VENDOR"


class _CaptureMCP:
    """@mcp.tool() 등록 함수를 이름→함수로 포획하는 최소 스텁."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


class _FakePool:
    """execute_sql이 참조하는 pool_manager 계약만 구현한 대역."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self._src = SimpleNamespace(readonly=True, max_rows=100)

    def get_source_config(self, source: str) -> Any:
        return self._src

    async def execute(self, source: str, sql: str) -> list[dict]:
        self.executed.append(sql)
        return [{"n": 1}]


def _ctx(pool: _FakePool) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context={"pool_manager": pool})
    )


def _execute_sql_tool(**kwargs):
    """register_tools가 등록한 execute_sql 클로저를 포획해 돌려준다."""
    capture = _CaptureMCP()
    register_tools(capture, expose_execute_sql=True, **kwargs)
    return capture.tools["execute_sql"]


# --- ⑬ execute_sql 폴스타 도메인 가드 게이트 ---


@pytest.mark.asyncio
async def test_domain_guard_on_by_default_blocks_polestar_pattern():
    """기본값(게이트 켜짐)에서는 종전대로 폴스타 금지 패턴을 차단한다(동작 불변)."""
    tool = _execute_sql_tool()
    pool = _FakePool()

    result = json.loads(await tool(source="s", sql=_POLESTAR_DENIED_SQL, ctx=_ctx(pool)))

    assert "폴스타 도메인 위반" in result["error"]
    assert pool.executed == []  # 실행에 도달하지 않는다


@pytest.mark.asyncio
async def test_domain_guard_off_allows_polestar_pattern():
    """게이트를 끄면 폴스타 스키마 전제 검증을 건너뛰고 실행한다(폴스타 미서빙 배치)."""
    tool = _execute_sql_tool(polestar_domain_guard=False)
    pool = _FakePool()

    result = json.loads(await tool(source="s", sql=_POLESTAR_DENIED_SQL, ctx=_ctx(pool)))

    assert "error" not in result
    assert pool.executed == [_POLESTAR_DENIED_SQL]


@pytest.mark.asyncio
async def test_readonly_guard_survives_domain_guard_off():
    """도메인 가드를 꺼도 읽기 전용 검증은 그대로 적용된다(안전선 유지)."""
    tool = _execute_sql_tool(polestar_domain_guard=False)
    pool = _FakePool()

    result = json.loads(await tool(source="s", sql="DELETE FROM servers", ctx=_ctx(pool)))

    assert "읽기 전용 위반" in result["error"]
    assert pool.executed == []


# --- ⑭ 폴스타 고수준 도구 등록 게이트 ---


def _server(**server_kwargs):
    return create_server(AppServerConfig(server=ServerConfig(**server_kwargs)))


@pytest.mark.asyncio
async def test_polestar_tools_registered_by_default():
    """기본값(True)에서는 폴스타 고수준 도구가 종전대로 등록된다(동작 불변)."""
    names = {t.name for t in await _server(name="gate-on").list_tools()}
    assert "polestar_resource_status" in names
    assert "polestar_alarm_history" in names


@pytest.mark.asyncio
async def test_polestar_tools_hidden_when_gated_off():
    """False면 폴스타 도구가 발견 표면에서 사라지고 범용 도구는 남는다."""
    names = {
        t.name
        for t in await _server(name="gate-off", expose_polestar_tools=False).list_tools()
    }
    assert not any(n.startswith("polestar_") for n in names)
    assert "list_sources" in names
