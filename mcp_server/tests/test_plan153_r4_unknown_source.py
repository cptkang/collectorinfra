"""plans/153 W0 · R-4 — 제니퍼 소스 id로 폴스타 사전수집 도구를 부르면 「알 수 없는 소스」.

sre_agent 사전수집은 `polestar_incident_alarms` 1건 + `polestar_metric_trend` 4건을
같은 `source`로 부른다. 제니퍼 트리거의 dbId(`jennifer`·`jennifer_<source_id>`)는 mcp_server
소스 설정에 없으므로 `_resolve_engine`이 ValueError를 내고, 도구는 예외를 전파하지 않고
`{error}` JSON을 돌려준다(DB 미접속).
sre_agent 쪽 재현은 `sre_agent/tests/test_plan153_r4_apm_prefetch_source.py`.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

try:
    from mcp_server.config import AppServerConfig, SourceConfig
    from mcp_server.db import DBPoolManager

    from mcp_server import polestar_tools as pt
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp 패키지가 설치되지 않음")


class _CaptureMCP:
    """@mcp.tool() 등록 함수를 이름→함수로 포획하는 최소 스텁."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tools_and_ctx() -> tuple[dict[str, Any], SimpleNamespace]:
    # 연결 문자열만 있으면 활성 소스로 등록된다(initialize 없이 — 미등록 소스는 DB 접근 전에 거부).
    src = SourceConfig(name="polestar_cm_gp", type="postgresql", connection="postgresql://u:p@127.0.0.1:1/x")
    pool = DBPoolManager([src])
    capture = _CaptureMCP()
    pt.register_polestar_tools(capture)
    ctx = SimpleNamespace(request_context=SimpleNamespace(
        lifespan_context={"pool_manager": pool, "config": AppServerConfig(sources=[src])}
    ))
    return capture.tools, ctx


@pytest.mark.parametrize("source", ["jennifer", "jennifer_common"])
def test_prefetch_tools_reject_jennifer_source(source: str):
    tools, ctx = _tools_and_ctx()
    base = {"source": source, "server_name": "xsapp01", "reference_time": "2026-10-07T09:04:30",
            "lookback_minutes": 60}

    async def _run() -> list[dict]:
        outs = [await tools["polestar_incident_alarms"](**base, ctx=ctx)]
        for kind in ("cpu", "memory", "filesystem", "disk_io"):
            outs.append(await tools["polestar_metric_trend"](
                **base, kind=kind, granularity="h", periods=3, baseline_periods=24, ctx=ctx
            ))
        return [json.loads(o) for o in outs]

    results = asyncio.run(_run())
    assert len(results) == 5
    for data in results:
        assert set(data) == {"error"}
        assert data["error"].startswith(f"알 수 없는 소스: {source}")
        assert "polestar_cm_gp" in data["error"]  # 활성 소스 목록 동봉
