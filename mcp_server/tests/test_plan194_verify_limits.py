"""plans/194 W4a 독립 검증 — 소스 동시 상한 취소·예외 반환 · PG 연결 반납 · readonly 선행.

실 DB 0 — 러너·풀은 목.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import pytest
from mcp_server.config import SourceConfig
from mcp_server.db import DBPoolManager, QueryQueueTimeoutError

_SQL = "SELECT 1 AS ok"


def _db2(name: str = "d", **kw: Any) -> SourceConfig:
    return SourceConfig(name=name, type="db2", connection="x", readonly=True, **kw)


def _manager(src: SourceConfig, runner) -> DBPoolManager:  # noqa: ANN001
    pm = DBPoolManager([src])
    pm._db2_configs[src.name] = src
    pm._execute_db2 = runner  # type: ignore[method-assign]
    return pm


async def test_random_cancel_timeout_error_never_leaks_permits() -> None:
    """무작위 지연·예외·취소·대기 초과가 섞여도 끝나면 세마포어 값이 상한 그대로다."""
    rng = random.Random(194)
    src = _db2(max_concurrent_queries=3, queue_timeout_seconds=1)
    active = {"now": 0, "peak": 0}

    async def runner(source_name: str, sql: str) -> list[dict[str, Any]]:
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        try:
            await asyncio.sleep(rng.uniform(0, 0.05))
            if rng.random() < 0.2:
                raise RuntimeError("db error")
            return [{"ok": 1}]
        finally:
            active["now"] -= 1

    pm = _manager(src, runner)
    tasks = [asyncio.create_task(pm.execute(src.name, _SQL)) for _ in range(80)]
    await asyncio.sleep(0.01)
    for t in rng.sample(tasks, 20):
        t.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    kinds = {type(r).__name__ for r in results}
    assert kinds <= {"list", "RuntimeError", "CancelledError", "QueryQueueTimeoutError"}, kinds
    assert active["peak"] <= 3
    sem = pm._query_limits[src.name]
    assert sem._value == 3, "슬롯 누수 또는 초과 반환"


async def test_readonly_violation_rejected_before_waiting_for_slot() -> None:
    """slot이 가득 차도 쓰기 SQL은 대기 없이 즉시 거부된다(readonly 3중 방어 불변 · 선행 검사)."""
    src = _db2(max_concurrent_queries=1, queue_timeout_seconds=5)
    gate = asyncio.Event()

    async def runner(source_name: str, sql: str) -> list[dict[str, Any]]:
        await gate.wait()
        return []

    pm = _manager(src, runner)
    holder = asyncio.create_task(pm.execute(src.name, _SQL))
    await asyncio.sleep(0.01)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with pytest.raises(Exception) as ei:
        await pm.execute(src.name, "DELETE FROM t")
    assert not isinstance(ei.value, QueryQueueTimeoutError)
    assert loop.time() - t0 < 1.0
    gate.set()
    await holder


class _Conn:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def fetch(self, sql: str) -> list[dict[str, Any]]:
        await asyncio.sleep(0.01)
        if self.fail:
            raise RuntimeError("fetch 실패")
        return [{"ok": 1}]


class _Pool:
    def __init__(self) -> None:
        self.out = 0
        self.fail_next = False

    async def acquire(self, *, timeout: float | None = None) -> _Conn:
        self.out += 1
        conn = _Conn(self.fail_next)
        self.fail_next = False
        return conn

    async def release(self, conn: Any) -> None:
        self.out -= 1


async def test_pg_connection_returned_on_fetch_error_and_cancel() -> None:
    src = SourceConfig(name="pg", type="postgresql", connection="x", pool_max_size=2,
                       query_timeout=5)
    pm = DBPoolManager([src])
    pool = _Pool()
    pm._pg_pools["pg"] = pool
    pool.fail_next = True
    with pytest.raises(RuntimeError):
        await pm.execute("pg", _SQL)
    assert pool.out == 0
    task = asyncio.create_task(pm.execute("pg", _SQL))
    await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.out == 0
    assert pm._query_limits["pg"]._value == 2
