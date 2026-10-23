"""배치 동시 호출 — 한 SSE 세션 안 상한 동시 `call_tool` · 순서 유지 · 오류 격리 (plans/187 W21 P1).

실 서버 없이 `sse_client`·`ClientSession`을 대역(호출별 지연·일부 예외)으로 바꿔 검증한다.
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sre_agent.infrastructure import mcp_tool_client as mc

_D = 0.1  # 호출당 기본 지연(초)


def _install(monkeypatch, delays=None, fail=()):
    """대역 세션을 설치하고 관측 기록(started 순서·최대 동시 수)을 돌려준다."""
    obs = {"started": [], "active": 0, "peak": 0}

    class _Session:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def initialize(self):
            pass

        async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
            obs["started"].append(name)
            obs["active"] += 1
            obs["peak"] = max(obs["peak"], obs["active"])
            try:
                await asyncio.sleep((delays or {}).get(name, _D))
                if name in fail:
                    raise RuntimeError("도구 폭발")
                return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"tool": name}))])
            finally:
                obs["active"] -= 1

    @asynccontextmanager
    async def fake_sse(url, headers=None, timeout=5, **kw):
        yield ("r", "w")

    import mcp
    import mcp.client.sse
    monkeypatch.setattr(mcp.client.sse, "sse_client", fake_sse)
    monkeypatch.setattr(mcp, "ClientSession", _Session)
    return obs


def _calls(n):
    return [(f"t{i}", {}) for i in range(n)]


def test_concurrent_batch_is_faster_and_bounded(monkeypatch):
    obs = _install(monkeypatch)
    t0 = time.perf_counter()
    out = mc.run_tool_batch("http://x/sse", None, _calls(8), concurrency=4)
    elapsed = time.perf_counter() - t0
    assert [json.loads(o)["tool"] for o in out] == [f"t{i}" for i in range(8)]
    # 순차면 8d, 상한 4면 ≈2d
    assert elapsed < 5 * _D
    assert obs["peak"] == 4


def test_order_follows_calls_even_with_reverse_delays(monkeypatch):
    delays = {f"t{i}": 0.02 * (6 - i) for i in range(6)}
    _install(monkeypatch, delays=delays)
    out = mc.run_tool_batch("http://x/sse", None, _calls(6), concurrency=6)
    assert [json.loads(o)["tool"] for o in out] == [f"t{i}" for i in range(6)]


def test_one_failure_isolated_under_concurrency(monkeypatch):
    _install(monkeypatch, fail=("t2",))
    out = mc.run_tool_batch("http://x/sse", None, _calls(5), concurrency=3)
    assert len(out) == 5
    assert "t2 호출 실패" in json.loads(out[2])["error"]
    assert [json.loads(out[i])["tool"] for i in (0, 1, 3, 4)] == ["t0", "t1", "t3", "t4"]


@pytest.mark.parametrize("concurrency", [1, 0, -3])
def test_concurrency_one_is_sequential(monkeypatch, concurrency):
    delays = {f"t{i}": 0.01 * (4 - i) for i in range(4)}
    obs = _install(monkeypatch, delays=delays)
    out = mc.run_tool_batch("http://x/sse", None, _calls(4), concurrency=concurrency)
    assert obs["peak"] == 1
    assert obs["started"] == ["t0", "t1", "t2", "t3"]
    assert [json.loads(o)["tool"] for o in out] == ["t0", "t1", "t2", "t3"]


def test_session_failure_still_errors_every_call(monkeypatch):
    @asynccontextmanager
    async def broken_sse(url, headers=None, timeout=5, **kw):
        raise ConnectionError("refused")
        yield  # pragma: no cover

    import mcp.client.sse
    monkeypatch.setattr(mcp.client.sse, "sse_client", broken_sse)
    out = mc.run_tool_batch("http://x/sse", None, _calls(3), concurrency=4)
    assert len(out) == 3 and all(json.loads(o)["error"] == "MCP 세션 실패: refused" for o in out)


def _settings(**kw):
    base = dict(polestar_mcp_url="http://p/sse", polestar_mcp_token=None,
                apm_mcp_url="http://a/sse", apm_mcp_token=None,
                evidence_prefetch_timeout_seconds=9)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("factory", ["make_batch_caller", "make_apm_batch_caller"])
@pytest.mark.parametrize("kw,expect", [
    ({"sre_tool_batch_concurrency": 4}, 4),
    ({"sre_tool_batch_concurrency": 0}, 1),
    ({"sre_tool_batch_concurrency": "x"}, 1),
    ({}, 1),
])
def test_callers_pass_concurrency_setting(monkeypatch, factory, kw, expect):
    captured = {}

    def fake_run(url, headers, calls, *, timeout_seconds, concurrency):
        captured.update(url=url, timeout=timeout_seconds, concurrency=concurrency)
        return []

    monkeypatch.setattr(mc, "run_tool_batch", fake_run)
    getattr(mc, factory)(_settings(**kw))([("a", {})])
    assert captured["concurrency"] == expect and captured["timeout"] == 9.0
