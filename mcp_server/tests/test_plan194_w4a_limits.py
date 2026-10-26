"""plans/194 W4a — 조회 대상 보호(소스별 동시 상한·대기 타임아웃 · DB2 쿼리 타임아웃 · D-347).

실 DB 없이 `DBPoolManager`의 러너를 목으로 바꿔 동시 상한·대기 타임아웃을 확인한다.
DB2는 가짜 `ibm_db` 모듈을 `sys.modules`에 넣어 statement 옵션 전달만 본다
(ibm_db 미설치 · 로컬 미검증).
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from mcp_server.config import DB2_DEFAULT_MAX_CONCURRENT, SourceConfig, _load_toml
from mcp_server.db import DBPoolManager, QueryQueueTimeoutError

_SQL = "SELECT 1 AS ok"


def _manager(*sources: SourceConfig, delay: float = 0.0) -> tuple[DBPoolManager, dict[str, int]]:
    """러너를 목으로 바꾼 관리자와 소스별 최대 동시 실행 수 기록을 돌려준다."""
    pm = DBPoolManager(list(sources))
    stats: dict[str, int] = {}
    running: dict[str, int] = {}

    async def _fake_runner(source_name: str, sql: str) -> list[dict[str, Any]]:
        running[source_name] = running.get(source_name, 0) + 1
        stats[source_name] = max(stats.get(source_name, 0), running[source_name])
        try:
            await asyncio.sleep(delay)
            return [{"ok": 1}]
        finally:
            running[source_name] -= 1

    for src in sources:
        pm._db2_configs[src.name] = src  # 목 러너로 가는 등록만 필요하다
    pm._execute_db2 = _fake_runner  # type: ignore[method-assign]
    return pm, stats


def _src(name: str, **kw: Any) -> SourceConfig:
    return SourceConfig(name=name, type="db2", connection="x", **kw)


# ---------------------------------------------------------------------------
# 기본값
# ---------------------------------------------------------------------------


def test_defaults_follow_pool_size_and_query_timeout() -> None:
    """미기재면 상한 = 풀 크기(DB2는 4), 대기 타임아웃 = query_timeout."""
    pg = SourceConfig(name="pg", type="postgresql", pool_max_size=3, query_timeout=17)
    assert pg.effective_max_concurrent() == 3
    assert pg.effective_queue_timeout() == 17.0
    db2 = SourceConfig(name="d", type="db2")
    assert db2.effective_max_concurrent() == DB2_DEFAULT_MAX_CONCURRENT == 4
    explicit = SourceConfig(name="e", max_concurrent_queries=7, queue_timeout_seconds=2)
    assert explicit.effective_max_concurrent() == 7
    assert explicit.effective_queue_timeout() == 2.0


def test_toml_parses_new_keys(tmp_path: Path) -> None:
    """`[[sources]]`의 두 키를 읽고, 미기재는 None(자동)으로 둔다."""
    path = tmp_path / "c.toml"
    path.write_text(
        '[[sources]]\nname = "a"\ntype = "db2"\nmax_concurrent_queries = 2\n'
        'queue_timeout_seconds = 5\n\n[[sources]]\nname = "b"\n',
        encoding="utf-8",
    )
    a, b = _load_toml(path).sources
    assert (a.max_concurrent_queries, a.queue_timeout_seconds) == (2, 5)
    assert (b.max_concurrent_queries, b.queue_timeout_seconds) == (None, None)


@pytest.mark.parametrize("value", ["0", "-1", '"3"', "true"])
def test_toml_rejects_invalid_limit(tmp_path: Path, value: str) -> None:
    """1 미만·정수 아님은 기동 실패(상한 0은 모든 조회를 막는다)."""
    path = tmp_path / "c.toml"
    path.write_text(
        f'[[sources]]\nname = "a"\nmax_concurrent_queries = {value}\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="max_concurrent_queries"):
        _load_toml(path)


# ---------------------------------------------------------------------------
# 동시 상한 · 대기 타임아웃
# ---------------------------------------------------------------------------


async def test_normal_load_runs_without_waiting() -> None:
    """상한 이하 동시 요청은 대기 없이 모두 함께 실행된다."""
    pm, stats = _manager(_src("a", max_concurrent_queries=3, queue_timeout_seconds=1), delay=0.05)
    loop = asyncio.get_running_loop()
    started = loop.time()
    results = await asyncio.gather(*(pm.execute("a", _SQL) for _ in range(3)))
    assert all(r == [{"ok": 1}] for r in results)
    assert stats["a"] == 3
    assert loop.time() - started < 0.5  # 직렬화됐다면 0.15s 이상 + 대기 없음


async def test_over_limit_waits_then_runs() -> None:
    """상한을 넘은 요청은 슬롯이 빌 때까지 기다렸다 실행된다(동시 수는 상한을 넘지 않는다)."""
    pm, stats = _manager(_src("a", max_concurrent_queries=2, queue_timeout_seconds=5), delay=0.05)
    results = await asyncio.gather(*(pm.execute("a", _SQL) for _ in range(5)))
    assert len(results) == 5
    assert stats["a"] == 2


async def test_queue_timeout_raises_clear_error() -> None:
    """대기 타임아웃을 넘기면 `QueryQueueTimeoutError` — 소스·동시 수를 실은 안내 문구."""
    pm, _ = _manager(_src("a", max_concurrent_queries=1, queue_timeout_seconds=1), delay=1.5)
    first = asyncio.create_task(pm.execute("a", _SQL))
    await asyncio.sleep(0)  # 첫 요청이 슬롯을 쥐게 한다
    with pytest.raises(QueryQueueTimeoutError) as excinfo:
        await pm.execute("a", _SQL)
    msg = str(excinfo.value)
    assert "대상 DB 조회 대기 초과" in msg
    assert "소스 a" in msg and "동시 1건" in msg and "잠시 뒤 다시" in msg
    assert await first == [{"ok": 1}]
    # 타임아웃 뒤에도 슬롯이 새지 않았다 — 다음 요청은 즉시 통과한다
    assert await pm.execute("a", _SQL) == [{"ok": 1}]


async def test_runner_error_releases_slot() -> None:
    """실행 오류도 슬롯을 반납한다(상한 1에서 연속 실패 뒤 정상 실행)."""
    pm, _ = _manager(_src("a", max_concurrent_queries=1, queue_timeout_seconds=1))

    async def _boom(source_name: str, sql: str) -> list[dict[str, Any]]:
        raise RuntimeError("db down")

    pm._execute_db2 = _boom  # type: ignore[method-assign]
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await pm.execute("a", _SQL)
    assert not pm._query_limits["a"].locked()


async def test_sources_are_independent() -> None:
    """한 소스가 상한에 막혀도 다른 소스는 대기 없이 실행된다."""
    pm, _ = _manager(
        _src("a", max_concurrent_queries=1, queue_timeout_seconds=1),
        _src("b", max_concurrent_queries=1, queue_timeout_seconds=1),
        delay=0.5,
    )
    busy = asyncio.create_task(pm.execute("a", _SQL))
    await asyncio.sleep(0)
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await pm.execute("b", _SQL) == [{"ok": 1}]
    assert loop.time() - started < 0.9  # a를 기다렸다면 1.0s 근처
    await busy


async def test_queue_timeout_is_logged_and_surfaces_as_tool_error() -> None:
    """대기 초과도 `execute`의 SQL 로그 경로를 탄다 — 예외 메시지가 도구 오류 문자열이 된다."""
    import json

    from mcp_server import sql_log

    pm, _ = _manager(_src("a", max_concurrent_queries=1, queue_timeout_seconds=1), delay=1.5)
    logged: list[dict[str, Any]] = []
    orig = sql_log.log_sql
    sql_log.log_sql = lambda sql, **kw: logged.append(kw)  # type: ignore[assignment]
    try:
        first = asyncio.create_task(pm.execute("a", _SQL))
        await asyncio.sleep(0)
        try:
            await pm.execute("a", _SQL)
        except Exception as e:  # 도구 관행: json.dumps({"error": str(e)})
            payload = json.loads(json.dumps({"error": str(e)}, ensure_ascii=False))
        await first
    finally:
        sql_log.log_sql = orig  # type: ignore[assignment]
    assert payload["error"].startswith("대상 DB 조회 대기 초과")
    assert any("대기 초과" in (kw.get("error") or "") for kw in logged)


# ---------------------------------------------------------------------------
# PostgreSQL 풀 acquire 대기 타임아웃
# ---------------------------------------------------------------------------


class _FakePgConn:
    async def fetch(self, sql: str) -> list[dict[str, Any]]:
        return [{"ok": 1}]


class _FakePgPool:
    """acquire(timeout=)·release를 기록하는 가짜 asyncpg 풀."""

    def __init__(self, hang: bool = False) -> None:
        self.hang = hang
        self.acquire_timeouts: list[float | None] = []
        self.released = 0

    async def acquire(self, *, timeout: float | None = None) -> _FakePgConn:
        self.acquire_timeouts.append(timeout)
        if self.hang:
            await asyncio.wait_for(asyncio.Event().wait(), timeout=timeout)
        return _FakePgConn()

    async def release(self, conn: Any) -> None:
        self.released += 1


async def test_pg_acquire_gets_queue_timeout_and_releases() -> None:
    """PG 풀 acquire에 대기 타임아웃이 전달되고 연결은 반납된다."""
    src = SourceConfig(
        name="pg", type="postgresql", connection="x", pool_max_size=2, query_timeout=9
    )
    pm = DBPoolManager([src])
    pool = _FakePgPool()
    pm._pg_pools["pg"] = pool
    assert await pm.execute("pg", _SQL) == [{"ok": 1}]
    assert pool.acquire_timeouts == [9.0]
    assert pool.released == 1


async def test_pg_acquire_timeout_maps_to_queue_error() -> None:
    """풀 연결 대기 초과도 같은 `QueryQueueTimeoutError`로 끊긴다."""
    src = SourceConfig(
        name="pg", type="postgresql", connection="x", queue_timeout_seconds=1, pool_max_size=1
    )
    pm = DBPoolManager([src])
    pm._pg_pools["pg"] = _FakePgPool(hang=True)
    with pytest.raises(QueryQueueTimeoutError, match="대상 DB 조회 대기 초과"):
        await pm.execute("pg", _SQL)
    assert not pm._query_limits["pg"].locked()


# ---------------------------------------------------------------------------
# DB2 쿼리 타임아웃 — statement 옵션 전달(목)
# ---------------------------------------------------------------------------


def _fake_ibm_db(monkeypatch: pytest.MonkeyPatch, with_const: bool = True) -> list[tuple]:
    """가짜 ibm_db 모듈을 넣고 exec_immediate 호출 인자 기록을 돌려준다."""
    calls: list[tuple] = []
    mod = types.ModuleType("ibm_db")
    if with_const:
        mod.SQL_ATTR_QUERY_TIMEOUT = 0  # type: ignore[attr-defined]
    mod.connect = lambda *a: "conn"  # type: ignore[attr-defined]
    mod.close = lambda conn: None  # type: ignore[attr-defined]

    def exec_immediate(conn: Any, sql: str, *options: Any) -> str:
        calls.append(options)
        return "stmt"

    rows = iter([{"OK": 1}, False])
    mod.exec_immediate = exec_immediate  # type: ignore[attr-defined]
    mod.fetch_assoc = lambda stmt: next(rows)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ibm_db", mod)
    return calls


async def test_db2_passes_query_timeout_option(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB2 경로가 `query_timeout`을 `SQL_ATTR_QUERY_TIMEOUT` statement 옵션으로 넘긴다."""
    calls = _fake_ibm_db(monkeypatch)
    pm = DBPoolManager([SourceConfig(name="d", type="db2", connection="x", query_timeout=12)])
    await pm.initialize()
    assert await pm.execute("d", _SQL) == [{"ok": 1}]
    assert calls == [({0: 12},)]


def test_db2_without_constant_runs_without_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """상수가 없는 ibm_db 빌드면 옵션 없이 실행한다(종전 동작 · 경고)."""
    calls = _fake_ibm_db(monkeypatch, with_const=False)
    assert DBPoolManager._execute_db2_sync("x", _SQL, 12) == [{"ok": 1}]
    assert calls == [()]


def test_db2_zero_timeout_sends_no_option(monkeypatch: pytest.MonkeyPatch) -> None:
    """query_timeout 0이면 옵션을 싣지 않는다."""
    calls = _fake_ibm_db(monkeypatch)
    DBPoolManager._execute_db2_sync("x", _SQL, 0)
    assert calls == [()]
