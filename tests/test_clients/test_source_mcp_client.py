"""plans/125 A-2 — 두 번째 MCP 엔드포인트 설정 · 관측 소스 MCP 세션(모의 세션 · 네트워크 0).

이 호스트의 루트 파이썬에는 `mcp` 가 없다 — 세션 공장을 주입해 봉투 해석·오류 사유·세션 재사용을
검증한다(실연결은 게이트웨이 기동 환경에서 — D-281 주의 ②).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.clients.source_mcp_client import SourceMcpError, open_source_session
from src.config import DBHubConfig


def _text(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))], isError=False)


class _Session:
    def __init__(self, replies: dict[str, object], delay: float = 0.0):
        self.replies = replies
        self.delay = delay
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.replies[name]


def _factory(session: _Session, opened: list[dict | None]):
    @asynccontextmanager
    async def factory(url, headers):
        opened.append(headers)
        yield session

    return factory


def test_endpoint_setting_is_off_by_default_and_parses_json() -> None:
    cfg = DBHubConfig(source_endpoints={"apm": "http://127.0.0.1:9096/sse", "dpm": " "},
                      source_tokens='{"apm": "tok-9f3a"}')
    assert cfg.active_source_codes() == ("apm",), "빈 URL 은 비활성"
    assert cfg.source_endpoint("apm") == ("http://127.0.0.1:9096/sse", "tok-9f3a")
    assert cfg.source_endpoint("dpm") is None
    broken = DBHubConfig(source_endpoints={"apm": "http://h/sse"}, source_tokens="not-json")
    assert broken.source_endpoint("apm") == ("http://h/sse", None), "깨진 토큰 JSON 은 무헤더"
    assert "tok-9f3a" not in repr(cfg), "토큰은 repr 에 나오지 않는다"


@pytest.mark.asyncio
async def test_session_is_reused_and_envelopes_pass_through() -> None:
    session = _Session({
        "apm_app_health": _text({"rows": [{"tps": 3}], "row_count": 1, "tool": "apm_app_health"}),
        "apm_events": _text({"error": "instance_unresolved", "reason": "정합 실패"}),
    })
    opened: list = []
    async with open_source_session("http://h/sse", "tok", call_timeout=1.0, label="APM",
                                   session_factory=_factory(session, opened)) as s:
        ok = await s.call_tool("apm_app_health", {"hostname": "web01", "instance_id": None})
        err = await s.call_tool("apm_events", {"hostname": "web01"})
    assert opened == [{"Authorization": "Bearer tok"}], "세션은 한 번만 연다"
    assert ok["rows"] == [{"tps": 3}]
    assert err == {"error": "instance_unresolved", "reason": "정합 실패"}, "계약 오류도 봉투 그대로"
    assert session.calls[0] == ("apm_app_health", {"hostname": "web01"}), "None 인자는 싣지 않는다"


@pytest.mark.asyncio
async def test_timeout_tool_error_and_connect_failure_are_structured() -> None:
    slow = _Session({"apm_runtime_health": _text({})}, delay=0.2)
    async with open_source_session("http://h/sse", None, call_timeout=0.01, label="APM",
                                   session_factory=_factory(slow, [])) as s:
        with pytest.raises(SourceMcpError, match="시간 초과"):
            await s.call_tool("apm_runtime_health", {"hostname": "h"})

    failing = _Session({"apm_pool": SimpleNamespace(content=[SimpleNamespace(text="boom")],
                                                    isError=True)})
    async with open_source_session("http://h/sse", None, call_timeout=1.0, label="APM",
                                   session_factory=_factory(failing, [])) as s:
        with pytest.raises(SourceMcpError, match="도구 오류"):
            await s.call_tool("apm_pool", {})

    @asynccontextmanager
    async def refused(url, headers):
        raise ConnectionRefusedError("refused")
        yield  # pragma: no cover

    with pytest.raises(SourceMcpError, match="연결·호출 실패"):
        async with open_source_session("http://h/sse", "secret-token", call_timeout=1.0,
                                       label="APM", session_factory=refused):
            pass
