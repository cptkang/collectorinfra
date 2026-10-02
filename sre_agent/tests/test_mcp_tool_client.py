"""mcp_server 도구 배치 클라이언트 — 세션 1개·호출 단위 격리·세션 실패 전파 (plans/50 B′).

실 서버 없이 `sse_client`·`ClientSession`을 대역으로 바꿔 검증한다.
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from sre_agent.infrastructure import mcp_tool_client as mc
from sre_agent.settings import AgentSettings


class _FakeSession:
    def __init__(self, read, write):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def initialize(self):
        pass

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls.append((name, arguments))
        if name == "boom":
            raise RuntimeError("도구 폭발")
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"tool": name, "args": arguments}))])


def _install(monkeypatch, session_cls=_FakeSession, connect_error=None):
    opened = []

    @asynccontextmanager
    async def fake_sse(url, headers=None, timeout=5, **kw):
        opened.append((url, headers, timeout))
        if connect_error:
            raise connect_error
        yield ("r", "w")

    import mcp
    import mcp.client.sse
    monkeypatch.setattr(mcp.client.sse, "sse_client", fake_sse)
    monkeypatch.setattr(mcp, "ClientSession", session_cls)
    return opened


def test_batch_uses_single_session_and_isolates_failures(monkeypatch):
    opened = _install(monkeypatch)
    out = mc.run_tool_batch("http://mcp:9099/sse", {"Authorization": "Bearer t"},
                            [("a", {"x": 1}), ("boom", {}), ("b", {})], timeout_seconds=7)
    assert len(opened) == 1 and opened[0][1] == {"Authorization": "Bearer t"} and opened[0][2] == 7
    assert json.loads(out[0]) == {"tool": "a", "args": {"x": 1}}
    assert "boom 호출 실패" in json.loads(out[1])["error"]
    assert json.loads(out[2])["tool"] == "b"


def test_session_failure_errors_every_call(monkeypatch):
    _install(monkeypatch, connect_error=ConnectionError("refused"))
    out = mc.run_tool_batch("http://mcp:9099/sse", None, [("a", {}), ("b", {})])
    assert len(out) == 2 and all("MCP 세션 실패" in json.loads(o)["error"] for o in out)


def test_text_of_without_text_content():
    assert "텍스트 콘텐츠 없음" in json.loads(mc._text_of(SimpleNamespace(content=[])))["error"]


def test_make_batch_caller_requires_url():
    s = AgentSettings(_env_file=None, model="m", gemini_api_key=None, polestar_mcp_url="")
    assert mc.make_batch_caller(s) is None
    s2 = AgentSettings(_env_file=None, model="m", gemini_api_key=None, polestar_mcp_url="http://x/sse",
                       evidence_prefetch_timeout_seconds=3)
    assert callable(mc.make_batch_caller(s2))


@pytest.mark.parametrize("token,expect", [(None, None), ("", None), ("tok", {"Authorization": "Bearer tok"})])
def test_make_batch_caller_headers(monkeypatch, token, expect):
    captured = {}

    def fake_run(url, headers, calls, *, timeout_seconds):
        captured.update(url=url, headers=headers, timeout=timeout_seconds)
        return []

    monkeypatch.setattr(mc, "run_tool_batch", fake_run)
    s = AgentSettings(_env_file=None, model="m", gemini_api_key=None, polestar_mcp_url="http://x/sse",
                      polestar_mcp_token=token, evidence_prefetch_timeout_seconds=9)
    mc.make_batch_caller(s)([("a", {})])
    assert captured == {"url": "http://x/sse", "headers": expect, "timeout": 9.0}
