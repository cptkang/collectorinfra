"""plans/206 W1 독립 검증 — DB2 연결 풀(`_Db2Pool`)의 누수·동시 사용·경계 조건.

구현 테스트(`test_plan206_w1_db2_pool.py`)가 다루지 않는 경로를 본다:
connect 예외 · 취소 · 부하 중 예외/헬스체크/종료 혼합 · 읽기 전용 검사 선행 · 풀 크기 배선.
가짜 `ibm_db`는 열린 연결 집합과 연결별 동시 사용을 직접 추적한다(목이 검증 대상을 대신하지 않게
풀 코드는 그대로 돈다).
"""

from __future__ import annotations

import asyncio
import random
import sys
import threading
import time
from dataclasses import replace
from typing import Any

import pytest
from mcp_server.config import SourceConfig
from mcp_server.db import DBPoolManager
from mcp_server.security import ReadOnlyViolationError

_SQL = "SELECT 1 AS ok FROM SYSIBM.SYSDUMMY1"


class _TrackingIbmDb:
    """열린 연결·연결별 동시 사용을 추적하는 가짜 ibm_db."""

    SQL_ATTR_QUERY_TIMEOUT = 0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n = 0
        self.open: set[str] = set()
        self.connects = 0
        self.closes = 0
        self.busy: set[str] = set()
        self.violations = 0
        self.use_after_close = 0
        self.connect_fail = 0
        self.exec_fail_rate = 0.0
        self.exec_delay = (0.0, 0.0)
        self.rng = random.Random(206)
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

    def connect(self, conn_str: str, user: str, pw: str) -> str:
        with self._lock:
            if self.connect_fail > 0:
                self.connect_fail -= 1
                raise RuntimeError("SQL30081N connect boom")
            self._n += 1
            conn = f"c{self._n}"
            self.open.add(conn)
            self.connects += 1
        return conn

    def close(self, conn: str) -> bool:
        with self._lock:
            if conn not in self.open:
                self.use_after_close += 1
            self.open.discard(conn)
            self.closes += 1
        return True

    def active(self, conn: str) -> bool:
        with self._lock:
            if conn in self.busy:
                self.violations += 1
            return conn in self.open

    def exec_immediate(self, conn: str, sql: str, *options: Any) -> dict[str, Any]:
        with self._lock:
            if conn not in self.open:
                self.use_after_close += 1
            if conn in self.busy:
                self.violations += 1
            self.busy.add(conn)
            fail = self.rng.random() < self.exec_fail_rate
            delay = self.rng.uniform(*self.exec_delay)
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(2)
        if delay:
            time.sleep(delay)
        if fail:
            with self._lock:
                self.busy.discard(conn)
            raise RuntimeError("SQL0952N exec boom")
        return {"conn": conn, "it": iter([{"OK": 1}])}

    def fetch_assoc(self, stmt: dict[str, Any]) -> Any:
        return next(stmt["it"], False)

    def free_stmt(self, stmt: dict[str, Any]) -> bool:
        with self._lock:
            self.busy.discard(stmt["conn"])
        return True


async def _manager(
    monkeypatch: pytest.MonkeyPatch, fake: _TrackingIbmDb, **kw: Any
) -> DBPoolManager:
    monkeypatch.setitem(sys.modules, "ibm_db", fake)
    pm = DBPoolManager([SourceConfig(name="d", type="db2", connection="DATABASE=x", **kw)])
    await pm.initialize()
    return pm


# ---------------------------------------------------------------------------
# 누수 — connect 예외 · 유휴 초과 뒤 connect 예외
# ---------------------------------------------------------------------------


async def test_connect_failure_propagates_and_releases_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _TrackingIbmDb()
    fake.connect_fail = 1
    pm = await _manager(monkeypatch, fake, max_concurrent_queries=1)
    with pytest.raises(RuntimeError, match="connect boom"):
        await pm.execute("d", _SQL)
    assert not pm._query_limits["d"].locked()
    assert pm._db2_pools["d"]._idle.qsize() == 0
    assert await pm.execute("d", _SQL) == [{"ok": 1}]
    assert fake.open == {"c1"} and fake.connects == 1


async def test_expired_idle_closed_even_if_reconnect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """유휴 초과 연결을 닫은 뒤 새 connect가 실패해도 옛 연결은 열린 채 남지 않는다."""
    fake = _TrackingIbmDb()
    pm = await _manager(monkeypatch, fake)
    pool = pm._db2_pools["d"]
    pool._clock = lambda: 0.0  # type: ignore[method-assign]
    await pm.execute("d", _SQL)
    pool._clock = lambda: 10_000.0  # type: ignore[method-assign]
    fake.connect_fail = 1
    with pytest.raises(RuntimeError, match="connect boom"):
        await pm.execute("d", _SQL)
    assert fake.open == set()
    assert pool._idle.qsize() == 0


# ---------------------------------------------------------------------------
# 풀 크기 배선 — effective_max_concurrent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kw", "expected"), [({}, 4), ({"max_concurrent_queries": 7}, 7)])
async def test_pool_size_equals_effective_max_concurrent(
    monkeypatch: pytest.MonkeyPatch, kw: dict[str, Any], expected: int
) -> None:
    pm = await _manager(monkeypatch, _TrackingIbmDb(), **kw)
    assert pm._db2_pools["d"]._idle.maxsize == expected
    assert pm._sources["d"].effective_max_concurrent() == expected


async def test_pool_max_size_does_not_cap_db2_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pool_max_size`는 DB2 풀 크기에 쓰지 않는다(plans/206 W1) — 브리지의 1-1 복제도 4다."""
    monkeypatch.setitem(sys.modules, "ibm_db", _TrackingIbmDb())
    src = SourceConfig(name="d", type="db2", connection="x")
    pm = DBPoolManager([replace(src, pool_min_size=1, pool_max_size=1)])
    await pm.initialize()
    assert pm._db2_pools["d"]._idle.maxsize == 4


async def test_pool_keys_ignored_for_non_db2(monkeypatch: pytest.MonkeyPatch) -> None:
    pm = DBPoolManager([SourceConfig(name="p", type="unknown_engine", connection="x")])
    await pm.initialize()
    assert pm._db2_pools == {}


# ---------------------------------------------------------------------------
# 읽기 전용 검사는 풀보다 먼저 — 세션 상태를 바꾸는 문장은 연결에 닿지 않는다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SET CURRENT SCHEMA = OTHER",
        "SET CURRENT ISOLATION = RR",
        "DECLARE GLOBAL TEMPORARY TABLE t (a INT)",
        "CALL SYSPROC.ADMIN_CMD('x')",
        "VALUES NEXT VALUE FOR s1",
        "SELECT NEXT VALUE FOR s1 FROM SYSIBM.SYSDUMMY1",
        "SELECT 1 FROM SYSIBM.SYSDUMMY1; SET CURRENT SCHEMA = X",
    ],
)
async def test_session_mutating_sql_rejected_before_checkout(
    monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    fake = _TrackingIbmDb()
    pm = await _manager(monkeypatch, fake)
    with pytest.raises(ReadOnlyViolationError):
        await pm.execute("d", sql)
    assert fake.connects == 0


# ---------------------------------------------------------------------------
# 취소 — 대기 중 코루틴이 취소돼도 스레드가 끝나면 연결은 반납·재사용된다
# ---------------------------------------------------------------------------


async def test_cancelled_request_returns_connection_after_thread_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _TrackingIbmDb()
    fake.gate = threading.Event()
    pm = await _manager(monkeypatch, fake, max_concurrent_queries=1)
    pool = pm._db2_pools["d"]
    task = asyncio.create_task(pm.execute("d", _SQL))
    assert await asyncio.to_thread(fake.entered.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # 슬롯은 취소 즉시 풀린다(종전과 같음) — 스레드는 아직 연결을 쥐고 있다.
    assert not pm._query_limits["d"].locked()
    fake.gate.set()
    deadline = time.monotonic() + 2
    while pool._idle.qsize() == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert pool._idle.qsize() == 1
    fake.gate = None
    assert await pm.execute("d", _SQL) == [{"ok": 1}]
    assert fake.connects == 1 and fake.violations == 0


# ---------------------------------------------------------------------------
# 부하 혼합 — 동시 요청 + 세마포어 밖 헬스체크 + 간헐 실패 + 종료
# ---------------------------------------------------------------------------


async def test_stress_no_shared_connection_no_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _TrackingIbmDb()
    fake.exec_fail_rate = 0.2
    fake.exec_delay = (0.0, 0.004)
    pm = await _manager(monkeypatch, fake, max_concurrent_queries=3, queue_timeout_seconds=30)
    pool = pm._db2_pools["d"]

    async def one(i: int) -> None:
        if i % 7 == 0:
            await pm.health_check("d")
            return
        try:
            await pm.execute("d", _SQL)
        except RuntimeError:
            pass

    await asyncio.gather(*(one(i) for i in range(300)))
    assert fake.violations == 0
    assert fake.use_after_close == 0
    # 열린 연결 = 풀 안 유휴 연결뿐(누수 0) · 상한 이하
    assert len(fake.open) == pool._idle.qsize() <= 3
    assert fake.connects - fake.closes == pool._idle.qsize()
    await pm.close_all()
    assert fake.open == set()


async def test_close_all_during_load_closes_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _TrackingIbmDb()
    fake.exec_delay = (0.001, 0.01)
    pm = await _manager(monkeypatch, fake, max_concurrent_queries=4)
    tasks = [asyncio.create_task(pm.execute("d", _SQL)) for _ in range(40)]
    await asyncio.sleep(0.02)
    await pm.close_all()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(r == [{"ok": 1}] for r in results)
    assert fake.violations == 0 and fake.use_after_close == 0
    assert fake.open == set()
