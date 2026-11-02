"""도구 호출 점검 로그 · 세션 초기화 상한 (D-347 부기).

2026-11-02 폐쇄망 「요청 1건 중 0건 완료」 진단용.

호출마다 「축 · 도구 · 자리 대기 · 호출 시간 · 결과」 1줄, 배치마다 세션 연결 1줄을 남기고,
인자·출력은 싣지 않는다. 세션 초기화가 멈추면 도구 1건 상한에서 끊는다.
"""

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sre_agent.infrastructure import mcp_tool_client as mc

_LOGGER = "sre_agent.infrastructure.mcp_tool_client"


class _Session:
    init_delay = 0.0

    def __init__(self, read, write):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def initialize(self):
        await asyncio.sleep(self.init_delay)

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        if name == "boom":
            raise RuntimeError("도구 폭발")
        if name == "slow":
            raise TimeoutError("read timed out")
        if name == "flagged":
            return SimpleNamespace(isError=True, content=[SimpleNamespace(text="실패")])
        if name == "envelope":
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"error": "x"}))])
        body = json.dumps({"secret_arg": arguments})
        return SimpleNamespace(content=[SimpleNamespace(text=body)])


def _install(monkeypatch, session_cls=_Session):
    @asynccontextmanager
    async def fake_sse(url, headers=None, timeout=5, **kw):
        yield ("r", "w")

    import mcp
    import mcp.client.sse
    monkeypatch.setattr(mcp.client.sse, "sse_client", fake_sse)
    monkeypatch.setattr(mcp, "ClientSession", session_cls)


def _lines(caplog, prefix):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


def test_each_call_logs_axis_tool_wait_and_outcome(monkeypatch, caplog):
    _install(monkeypatch)
    caplog.set_level(logging.INFO, logger=_LOGGER)
    calls = [("a", {"hostname": "비밀호스트"}), ("boom", {}), ("slow", {}),
             ("flagged", {}), ("envelope", {})]
    mc.run_tool_batch("http://x/sse", None, calls, timeout_seconds=5, axis="apm")
    lines = _lines(caplog, "도구 호출 [apm]")
    assert len(lines) == 5
    by_tool = {ln.split("] ")[1].split(":")[0]: ln for ln in lines}
    assert by_tool["a"].endswith("결과=ok")
    assert by_tool["boom"].endswith("결과=error")
    assert by_tool["slow"].endswith("결과=timeout")
    assert by_tool["flagged"].endswith("결과=error")
    assert by_tool["envelope"].endswith("결과=error")
    assert all("자리 대기" in ln and "· 호출 " in ln for ln in lines)
    assert not any("비밀호스트" in r.getMessage() for r in caplog.records)  # 인자 미기재
    batch = _lines(caplog, "도구 배치 [apm] 세션 연결")
    assert len(batch) == 1 and "호출 5건" in batch[0]


def test_axis_defaults_to_mcp(monkeypatch, caplog):
    _install(monkeypatch)
    caplog.set_level(logging.INFO, logger=_LOGGER)
    mc.run_tool_batch("http://x/sse", None, [("a", {})])
    assert _lines(caplog, "도구 호출 [mcp] a:")


def test_global_wait_timeout_logged(monkeypatch, caplog):
    _install(monkeypatch)
    caplog.set_level(logging.INFO, logger=_LOGGER)
    sem = threading.BoundedSemaphore(1)
    sem.acquire()  # 자리를 미리 다 차지
    try:
        out = mc.run_tool_batch("http://x/sse", None, [("a", {})], timeout_seconds=0.2,
                                global_limit=sem, axis="polestar")
    finally:
        sem.release()
    assert mc.TOOL_CONCURRENCY_WAIT_TIMEOUT in json.loads(out[0])["error"]
    line = _lines(caplog, "도구 호출 [polestar] a:")
    assert len(line) == 1 and line[0].endswith("결과=wait_timeout")


def test_initialize_hang_is_bounded(monkeypatch):
    class _Hang(_Session):
        init_delay = 30.0

    _install(monkeypatch, _Hang)
    out = mc.run_tool_batch("http://x/sse", None, [("a", {}), ("b", {})], timeout_seconds=0.2)
    errs = [json.loads(o)["error"] for o in out]
    assert len(errs) == 2 and all("세션 초기화 0.2s 초과" in e for e in errs)


@pytest.mark.parametrize("factory,axis", [("make_batch_caller", "polestar"),
                                          ("make_apm_batch_caller", "apm")])
def test_factories_pass_axis(monkeypatch, factory, axis):
    seen = {}

    def fake_run(url, headers, calls, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(mc, "run_tool_batch", fake_run)
    ns = SimpleNamespace(polestar_mcp_url="http://p/sse", polestar_mcp_token=None,
                         apm_mcp_url="http://a/sse", apm_mcp_token=None)
    getattr(mc, factory)(ns)([("t", {})])
    assert seen["axis"] == axis
