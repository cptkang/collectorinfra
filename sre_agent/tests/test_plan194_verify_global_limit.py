"""plans/194 W3 독립 검증 — 전역 도구 동시 상한의 취소·예외 반환과 대기 중 CPU(verifier 작성).

실 MCP 0 — 세션은 대역.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from sre_agent.infrastructure import mcp_tool_client as mc


def _install(monkeypatch: pytest.MonkeyPatch, delay: float, fail: set[str] | None = None) -> None:
    fail = fail or set()

    class _Session:
        def __init__(self, read: Any, write: Any) -> None:
            pass

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        async def initialize(self) -> None:
            pass

        async def call_tool(self, name: str, arguments: Any = None,
                            read_timeout_seconds: Any = None) -> SimpleNamespace:
            await asyncio.sleep(delay)
            if name in fail:
                raise RuntimeError("도구 실패")
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"tool": name}))])

    @asynccontextmanager
    async def fake_sse(url: str, headers: Any = None, timeout: float = 5, **kw: Any):
        yield ("r", "w")

    import mcp
    import mcp.client.sse
    monkeypatch.setattr(mcp.client.sse, "sse_client", fake_sse)
    monkeypatch.setattr(mcp, "ClientSession", _Session)


def _free(sem: threading.BoundedSemaphore, size: int) -> bool:
    got = [sem.acquire(blocking=False) for _ in range(size)]
    ok = all(got) and not sem.acquire(blocking=False)
    for g in got:
        if g:
            sem.release()
    return ok


def test_cancel_during_call_and_wait_returns_every_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, delay=5.0)
    sem = threading.BoundedSemaphore(2)

    async def main() -> None:
        task = asyncio.create_task(mc.run_tool_batch_async(
            "http://x/sse", None, [(f"t{i}", {}) for i in range(6)], timeout_seconds=10,
            concurrency=6, global_limit=sem,
        ))
        await asyncio.sleep(0.2)  # 2건은 호출 중 · 4건은 전역 자리 대기 중
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert _free(sem, 2), "취소 뒤 자리 누수"


def test_tool_exception_releases_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, delay=0.01, fail={"t1", "t3"})
    sem = threading.BoundedSemaphore(2)
    out = mc.run_tool_batch("http://x/sse", None, [(f"t{i}", {}) for i in range(5)],
                            timeout_seconds=5, concurrency=5, global_limit=sem)
    assert [("error" in json.loads(x)) for x in out] == [False, True, False, True, False]
    assert _free(sem, 2)


def test_waiting_for_global_slot_is_not_a_cpu_spin() -> None:
    """자리 대기 중 프로세스 CPU 시간 — 0.05s 간격 재시도라 벽시계 대비 작아야 한다."""
    sem = threading.BoundedSemaphore(1)
    sem.acquire()
    try:
        async def wait_many() -> list[bool]:
            return list(await asyncio.gather(*(mc._acquire_global(sem, 1.0) for _ in range(20))))

        cpu0, wall0 = time.process_time(), time.monotonic()
        got = asyncio.run(wait_many())
        cpu, wall = time.process_time() - cpu0, time.monotonic() - wall0
    finally:
        sem.release()
    assert got == [False] * 20
    assert wall >= 0.9
    assert cpu < wall * 0.5, f"cpu={cpu:.3f}s wall={wall:.3f}s"
