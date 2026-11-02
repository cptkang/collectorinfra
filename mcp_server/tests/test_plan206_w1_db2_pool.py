"""plans/206 W1 — DB2 연결 풀(기본 on · D-357).

ibm_db는 로컬에 없다 — 가짜 `ibm_db`를 `sys.modules`에 넣고 connect/close/active/free_stmt/
exec_immediate/fetch_assoc 호출을 기록해 풀 동작과 off 경로(종전 요청별 연결) 불변을 본다.
동시성 테스트는 `DBPoolManager.execute`를 `asyncio.gather`로 돌려 `to_thread` 경로를 그대로 탄다.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from mcp_server.config import SourceConfig, _load_toml
from mcp_server.db import DBPoolManager, QueryQueueTimeoutError

_SQL = "SELECT 1 AS ok"


class _FakeIbmDb:
    """호출을 기록하는 가짜 ibm_db. 연결은 `c1`, `c2`… 문자열이다."""

    SQL_ATTR_QUERY_TIMEOUT = 0
    SQL_AUTOCOMMIT_ON = 1

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.options: list[tuple[Any, ...]] = []
        self.rows = rows if rows is not None else [{"OK": 1}]
        self.exec_delay = 0.0
        self.fail_exec = 0  # 남은 exec_immediate 실패 횟수
        self.fail_fetch = 0  # 남은 fetch_assoc 실패 횟수
        self.active_value = True
        self.autocommit_value = 1
        self.autocommit_calls: list[tuple[Any, ...]] = []  # calls 순서 단언과 분리해 둔다
        self.barrier: threading.Barrier | None = None
        self.started = threading.Event()
        self.release: threading.Event | None = None
        self.violations = 0  # 같은 연결을 두 스레드가 동시에 쓴 횟수
        self._in_use: set[str] = set()
        self._lock = threading.Lock()
        self._n = 0

    def _rec(self, name: str, conn: str) -> None:
        with self._lock:
            self.calls.append((name, conn))

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)

    def connect(self, conn_str: str, user: str, password: str) -> str:
        with self._lock:
            self._n += 1
            conn = f"c{self._n}"
        self._rec("connect", conn)
        return conn

    def close(self, conn: str) -> bool:
        self._rec("close", conn)
        with self._lock:
            self._in_use.discard(conn)
        return True

    def active(self, conn: str) -> bool:
        self._rec("active", conn)
        return self.active_value

    def autocommit(self, conn: str, *value: int) -> Any:
        self.autocommit_calls.append((conn, *value))
        if value:
            self.autocommit_value = value[0]
            return True
        return self.autocommit_value

    def exec_immediate(self, conn: str, sql: str, *options: Any) -> dict[str, Any]:
        self._rec("exec", conn)
        with self._lock:
            self.options.append(options)
            if conn in self._in_use:
                self.violations += 1
            self._in_use.add(conn)
            failing = self.fail_exec > 0
            self.fail_exec -= 1 if failing else 0
        self.started.set()
        if self.barrier is not None:
            self.barrier.wait()
        if self.release is not None:
            self.release.wait(1)
        if self.exec_delay:
            time.sleep(self.exec_delay)
        if failing:
            raise RuntimeError("exec boom")
        return {"conn": conn, "it": iter([dict(r) for r in self.rows])}

    def fetch_assoc(self, stmt: dict[str, Any]) -> Any:
        with self._lock:
            failing = self.fail_fetch > 0
            self.fail_fetch -= 1 if failing else 0
        if failing:
            raise RuntimeError("fetch boom")
        return next(stmt["it"], False)

    def free_stmt(self, stmt: dict[str, Any]) -> bool:
        self._rec("free_stmt", stmt["conn"])
        with self._lock:
            self._in_use.discard(stmt["conn"])
        return True


async def _manager(
    monkeypatch: pytest.MonkeyPatch, fake: _FakeIbmDb, **kw: Any
) -> tuple[DBPoolManager, SourceConfig]:
    monkeypatch.setitem(sys.modules, "ibm_db", fake)
    src = SourceConfig(name="d", type="db2", connection="DATABASE=x", **kw)
    pm = DBPoolManager([src])
    await pm.initialize()
    return pm, src


# ---------------------------------------------------------------------------
# ⓪ 설정
# ---------------------------------------------------------------------------


def _toml(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f'[[sources]]\nname = "d"\ntype = "db2"\n{extra}', encoding="utf-8")
    return path


def test_toml_defaults_pool_on(tmp_path: Path) -> None:
    """키 미기재 = 풀 켜짐 · 유휴 300초."""
    src = _load_toml(_toml(tmp_path)).sources[0]
    assert src.pool_enabled is True
    assert src.pool_idle_seconds == 300


def test_toml_explicit_values(tmp_path: Path) -> None:
    src = _load_toml(_toml(tmp_path, "pool_enabled = false\npool_idle_seconds = 60\n")).sources[0]
    assert src.pool_enabled is False
    assert src.pool_idle_seconds == 60


@pytest.mark.parametrize(
    "extra",
    ['pool_enabled = "false"\n', "pool_enabled = 0\n", "pool_idle_seconds = 0\n",
     'pool_idle_seconds = "300"\n', "pool_idle_seconds = true\n"],
)
def test_toml_invalid_values_raise(tmp_path: Path, extra: str) -> None:
    """잘못된 값은 기동 실패로 드러낸다(문자열 "false"가 참으로 읽히지 않게)."""
    with pytest.raises(ValueError, match="pool_"):
        _load_toml(_toml(tmp_path, extra))


# ---------------------------------------------------------------------------
# ① off = 종전 요청별 연결
# ---------------------------------------------------------------------------


async def test_pool_off_keeps_per_request_connect_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pool_enabled=false`면 요청마다 connect 1·close 1, free_stmt·active 없음(종전 순서)."""
    fake = _FakeIbmDb()
    pm, _ = await _manager(monkeypatch, fake, pool_enabled=False)
    assert pm._db2_pools == {}
    for _ in range(3):
        assert await pm.execute("d", _SQL) == [{"ok": 1}]
    assert fake.calls == [
        (name, f"c{i}") for i in (1, 2, 3) for name in ("connect", "exec", "close")
    ]
    assert fake.autocommit_calls == []


# ---------------------------------------------------------------------------
# ② on — 재사용·상한
# ---------------------------------------------------------------------------


async def test_pool_sequential_reuses_one_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeIbmDb()
    pm, _ = await _manager(monkeypatch, fake)
    for _ in range(5):
        assert await pm.execute("d", _SQL) == [{"ok": 1}]
    assert fake.count("connect") == 1
    assert fake.count("free_stmt") == 5
    assert fake.count("close") == 0
    assert pm._db2_pools["d"]._idle.qsize() == 1


async def test_pool_initialize_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """지연 생성 — 기동만으로는 연결하지 않는다."""
    fake = _FakeIbmDb()
    pm, _ = await _manager(monkeypatch, fake)
    assert "d" in pm._db2_pools
    assert fake.calls == []


async def test_pool_concurrent_connects_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """동시 요청이 상한을 넘어도 누적 연결은 상한 이하 · 한 연결을 두 스레드가 같이 쓰지 않는다."""
    fake = _FakeIbmDb()
    fake.exec_delay = 0.03
    pm, src = await _manager(monkeypatch, fake, max_concurrent_queries=3)
    results = await asyncio.gather(*(pm.execute("d", _SQL) for _ in range(9)))
    assert results == [[{"ok": 1}]] * 9
    assert 1 <= fake.count("connect") <= src.effective_max_concurrent() == 3
    assert fake.violations == 0
    assert fake.count("close") == 0
    assert pm._db2_pools["d"]._idle.qsize() == fake.count("connect")


async def test_pool_full_on_checkin_closes_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """세마포어 밖 헬스체크가 만든 +1 연결은 반납 때 풀이 가득 차 있으면 닫는다(대기 없음)."""
    fake = _FakeIbmDb()
    fake.barrier = threading.Barrier(2, timeout=1)
    pm, _ = await _manager(monkeypatch, fake, max_concurrent_queries=1)
    rows, healthy = await asyncio.gather(pm.execute("d", _SQL), pm.health_check("d"))
    assert rows == [{"ok": 1}] and healthy is True
    assert fake.count("connect") == 2
    assert fake.count("close") == 1
    assert pm._db2_pools["d"]._idle.qsize() == 1


# ---------------------------------------------------------------------------
# ③ 실행 예외 연결은 버린다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["exec", "fetch"])
async def test_pool_error_connection_closed_not_returned(
    monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    fake = _FakeIbmDb()
    pm, _ = await _manager(monkeypatch, fake)
    await pm.execute("d", _SQL)  # c1이 풀에 들어간다
    if where == "exec":
        fake.fail_exec = 1
    else:
        fake.fail_fetch = 1
    with pytest.raises(RuntimeError, match=f"{where} boom"):
        await pm.execute("d", _SQL)
    assert ("close", "c1") in fake.calls
    assert pm._db2_pools["d"]._idle.qsize() == 0
    assert await pm.execute("d", _SQL) == [{"ok": 1}]
    assert fake.calls[-3:] == [("connect", "c2"), ("exec", "c2"), ("free_stmt", "c2")]
    assert not pm._query_limits["d"].locked()


async def test_pool_free_stmt_failure_returns_rows_and_drops_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """문장 핸들 해제 실패 — 행은 돌려주되 그 연결은 재사용하지 않는다."""
    fake = _FakeIbmDb()

    def _boom(stmt: Any) -> None:
        raise RuntimeError("free boom")

    fake.free_stmt = _boom  # type: ignore[method-assign]
    pm, _ = await _manager(monkeypatch, fake)
    assert await pm.execute("d", _SQL) == [{"ok": 1}]
    assert fake.calls[-1] == ("close", "c1")
    assert pm._db2_pools["d"]._idle.qsize() == 0


# ---------------------------------------------------------------------------
# ④ 꺼낼 때 검사 — 비활성·유휴 초과
# ---------------------------------------------------------------------------


async def test_pool_inactive_connection_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeIbmDb()
    pm, _ = await _manager(monkeypatch, fake)
    await pm.execute("d", _SQL)
    fake.active_value = False
    await pm.execute("d", _SQL)
    assert fake.calls[3:6] == [("active", "c1"), ("close", "c1"), ("connect", "c2")]


@pytest.mark.parametrize(("elapsed", "reused"), [(299.0, True), (301.0, False)])
async def test_pool_idle_expiry(
    monkeypatch: pytest.MonkeyPatch, elapsed: float, reused: bool
) -> None:
    fake = _FakeIbmDb()
    pm, _ = await _manager(monkeypatch, fake)
    pool = pm._db2_pools["d"]
    pool._clock = lambda: 1000.0  # type: ignore[method-assign]
    await pm.execute("d", _SQL)
    pool._clock = lambda: 1000.0 + elapsed  # type: ignore[method-assign]
    await pm.execute("d", _SQL)
    if reused:
        assert fake.count("connect") == 1 and fake.count("close") == 0
    else:
        assert fake.calls[3:5] == [("close", "c1"), ("connect", "c2")]
        assert ("active", "c1") not in fake.calls


async def test_checkin_sweeps_expired_idle_below_top(monkeypatch: pytest.MonkeyPatch) -> None:
    """LIFO 밑에 깔린 만료 연결도 반납 때 닫힌다 — 위의 쓰던 연결은 남는다."""
    fake = _FakeIbmDb()
    pm, _ = await _manager(monkeypatch, fake, max_concurrent_queries=3)
    pool = pm._db2_pools["d"]
    pool._clock = lambda: 1000.0  # type: ignore[method-assign]
    fake.barrier = threading.Barrier(3, timeout=1)
    await asyncio.gather(*(pm.execute("d", _SQL) for _ in range(3)))
    fake.barrier = None
    assert fake.count("connect") == 3 and pool._idle.qsize() == 3
    pool._clock = lambda: 1200.0  # type: ignore[method-assign]
    await pm.execute("d", _SQL)  # 맨 위 1개만 다시 쓴다(사용 시각 1200)
    assert fake.count("close") == 0
    pool._clock = lambda: 1350.0  # type: ignore[method-assign]
    await pm.execute("d", _SQL)  # 위 연결은 150초 · 밑 2개는 350초 유휴
    assert fake.count("connect") == 3
    assert fake.count("close") == 2
    assert pool._idle.qsize() == 1


# ---------------------------------------------------------------------------
# autocommit — 새 연결당 1회 확인
# ---------------------------------------------------------------------------


async def test_autocommit_off_is_turned_on(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeIbmDb()
    fake.autocommit_value = 0
    pm, _ = await _manager(monkeypatch, fake)
    for _ in range(2):
        await pm.execute("d", _SQL)
    assert fake.autocommit_calls == [("c1",), ("c1", 1)]


async def test_autocommit_on_needs_no_set(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeIbmDb()
    pm, _ = await _manager(monkeypatch, fake)
    for _ in range(2):
        await pm.execute("d", _SQL)
    assert fake.autocommit_calls == [("c1",)]


@pytest.mark.parametrize("mode", ["missing_api", "missing_const", "raises"])
async def test_autocommit_unavailable_warns_and_proceeds(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, mode: str
) -> None:
    fake = _FakeIbmDb()
    if mode == "missing_api":
        fake.autocommit = None  # type: ignore[assignment,method-assign]
    elif mode == "missing_const":
        fake.SQL_AUTOCOMMIT_ON = None  # type: ignore[assignment]
    else:

        def _boom(conn: str, *value: int) -> Any:
            raise RuntimeError("autocommit boom")

        fake.autocommit = _boom  # type: ignore[method-assign]
    pm, _ = await _manager(monkeypatch, fake)
    with caplog.at_level(logging.WARNING, logger="mcp_server.db"):
        assert await pm.execute("d", _SQL) == [{"ok": 1}]
    assert "autocommit 확인 불가" in caplog.text and "(d)" in caplog.text
    assert fake.count("close") == 0 and pm._db2_pools["d"]._idle.qsize() == 1


async def test_readonly_false_with_pool_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """readonly=false + 풀 — 경고만 남기고 풀은 그대로 쓴다."""
    with caplog.at_level(logging.WARNING, logger="mcp_server.db"):
        pm, _ = await _manager(monkeypatch, _FakeIbmDb(), readonly=False)
    assert "readonly=false 소스는 세션 상태가 요청 간 이월된다" in caplog.text
    assert "d" in pm._db2_pools


# ---------------------------------------------------------------------------
# ⑤ 종료
# ---------------------------------------------------------------------------


async def test_close_all_closes_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeIbmDb()
    fake.exec_delay = 0.02
    pm, _ = await _manager(monkeypatch, fake)
    await asyncio.gather(pm.execute("d", _SQL), pm.execute("d", _SQL))
    pool = pm._db2_pools["d"]
    opened = fake.count("connect")
    await pm.close_all()
    assert pool._idle.qsize() == 0
    assert fake.count("close") == opened
    assert pm._db2_pools == {}


async def test_close_all_closes_borrowed_on_checkin(monkeypatch: pytest.MonkeyPatch) -> None:
    """대여 중 연결은 close_all 때 닫지 않고 반납 때 닫힘 표시를 보고 닫는다."""
    fake = _FakeIbmDb()
    pm, _ = await _manager(monkeypatch, fake)
    pool = pm._db2_pools["d"]
    fake.release = threading.Event()
    task = asyncio.create_task(pm.execute("d", _SQL))
    assert await asyncio.to_thread(fake.started.wait, 1)
    await pm.close_all()
    assert fake.count("close") == 0
    fake.release.set()
    assert await task == [{"ok": 1}]
    assert fake.calls[-1] == ("close", "c1")
    assert pool._idle.qsize() == 0


async def test_close_all_without_db2_use_needs_no_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 번도 쓰지 않은 풀은 드라이버 없이도 종료된다."""
    monkeypatch.setitem(sys.modules, "ibm_db", None)  # import 시 ImportError
    pm = DBPoolManager([SourceConfig(name="d", type="db2", connection="x")])
    await pm.initialize()
    await pm.close_all()
    assert pm._db2_pools == {}


# ---------------------------------------------------------------------------
# ⑥ 행 결과·statement 옵션 = off와 같음
# ---------------------------------------------------------------------------


async def test_pool_rows_and_options_match_off(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"ID": 1, "AMT": Decimal("1.5"), "TS": datetime(2026, 1, 1), "RAW": b"\x01"}]
    fake = _FakeIbmDb(rows=rows)
    pm, _ = await _manager(monkeypatch, fake, query_timeout=12)
    pooled = await pm.execute("d", _SQL)
    off = DBPoolManager._execute_db2_sync("x", _SQL, 12)
    assert pooled == off == [{"id": 1, "amt": 1.5, "ts": "2026-01-01T00:00:00", "raw": "01"}]
    assert fake.options == [({0: 12},), ({0: 12},)]


# ---------------------------------------------------------------------------
# ⑦ 세마포어 대기 초과 불변
# ---------------------------------------------------------------------------


async def test_queue_timeout_unchanged_with_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeIbmDb()
    pm, src = await _manager(monkeypatch, fake, max_concurrent_queries=1)
    src.queue_timeout_seconds = 0.1  # type: ignore[assignment]  # 테스트 시간 단축
    fake.release = threading.Event()
    first = asyncio.create_task(pm.execute("d", _SQL))
    assert await asyncio.to_thread(fake.started.wait, 1)
    with pytest.raises(QueryQueueTimeoutError, match="대상 DB 조회 대기 초과"):
        await pm.execute("d", _SQL)
    fake.release.set()
    assert await first == [{"ok": 1}]
    assert fake.count("connect") == 1
    assert not pm._query_limits["d"].locked()
