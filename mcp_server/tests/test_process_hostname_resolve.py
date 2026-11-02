"""프로세스 API 조회 키 해소 — 폴스타 등록명으로 와도 OS 호스트명으로 조회한다.

공동존은 등록명(NAME) ≠ OS 호스트명(HOSTNAME)이라 sre_agent S6가 등록명을 그대로 넘기면
프로세스 API가 0건을 돌려줬다(질의 경로는 본체가 해소해 정상).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

try:
    import httpx

    from mcp_server import polestar_tools as pt
    from mcp_server.config import AppServerConfig, ServerConfig

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

from tests.test_plan189_prom_anchor import _B0, _GP, _YD, _FakePool, _row
from tests.test_polestar_tools import _CaptureMCP, _fake_ctx

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp/httpx 패키지가 설치되지 않음")

_PROC = {"date": "2026-10-29 10:00:00", "data": {"list": [
    {"name": "java", "pid": 1, "user": "was", "pcpu": 80.0, "pmem": 10.0, "rss": 1, "args": "java"},
]}}


def _pool(rows: dict[str, Any] | None = None) -> _FakePool:
    return _FakePool({_GP: "postgresql", _YD: "postgresql", _B0: "db2"}, rows)


async def _snapshot(monkeypatch: pytest.MonkeyPatch, pool: _FakePool, hostname: str,
                    source: str | None = _YD) -> tuple[dict, list[str]]:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(parse_qs(urlparse(str(request.url)).query)["hostname"][0])
        return httpx.Response(200, json=_PROC)

    real = httpx.AsyncClient
    monkeypatch.setattr(pt.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler)))
    capture = _CaptureMCP()
    pt.register_polestar_tools(capture)
    config = AppServerConfig(server=ServerConfig(process_api_base_url="http://proc"))
    out = await capture.tools["polestar_process_snapshot"](
        hostname=hostname, source=source, ctx=_fake_ctx(pool, config))
    return json.loads(out), asked


async def test_registered_name_is_resolved_to_os_hostname(monkeypatch):
    pool = _pool({_YD: [_row("등록명-A", "yd-web01", "10.0.0.1")]})
    body, asked = await _snapshot(monkeypatch, pool, "등록명-A")
    assert asked == ["yd-web01"]
    assert body["hostname"] == "yd-web01" and body["row_count"] == 1
    assert "등록명-A" in body["hostname_note"] and "yd-web01" in body["hostname_note"]
    assert [c[0] for c in pool.calls] == [_YD]  # 지정 소스 하나만 조회


async def test_os_hostname_input_unchanged_without_note(monkeypatch):
    body, asked = await _snapshot(monkeypatch, _pool({_YD: [_row("A", "yd-web01", "10.0.0.1")]}),
                                  "yd-web01")
    assert asked == ["yd-web01"] and "hostname_note" not in body


async def test_ambiguous_distinct_hostnames_not_queried(monkeypatch):
    pool = _pool({_YD: [_row("dup", "h1", "10.0.0.1"), _row("dup", "h2", "10.0.0.2")]})
    body, asked = await _snapshot(monkeypatch, pool, "dup")
    assert asked == [] and "호스트명 모호" in body["error"]


async def test_ambiguous_with_same_hostname_uses_it(monkeypatch):
    pool = _pool({_YD: [_row("dup", "h1", "10.0.0.1"), _row("dup2", "h1", "10.0.0.1")]})
    body, asked = await _snapshot(monkeypatch, pool, "dup")
    assert asked == ["h1"]


async def test_not_found_falls_back_to_input_with_note(monkeypatch):
    body, asked = await _snapshot(monkeypatch, _pool(), "unknown")
    assert asked == ["unknown"] and "같은 등록명·호스트명 없음" in body["hostname_note"]


async def test_lookup_error_falls_back_to_input_with_note(monkeypatch):
    body, asked = await _snapshot(monkeypatch, _pool({_YD: RuntimeError("db down")}), "A")
    assert asked == ["A"] and "호스트명 해소 실패" in body["hostname_note"]


async def test_no_source_keeps_legacy_behavior(monkeypatch):
    pool = _pool({_YD: [_row("A", "yd-web01", "10.0.0.1")]})
    body, asked = await _snapshot(monkeypatch, pool, "A", source=None)
    assert asked == ["A"] and pool.calls == [] and "hostname_note" not in body
