"""PostgresIncidentStore 단위 테스트 (D-049).

asyncpg를 fake conn/pool로 모킹하여 SQL 호출·반환 매핑을 검증한다(실 PG 불요).
- create_open: RETURNING id → int
- resolve_by_fingerprint: 최근 1건 선택 후 UPDATE, 매칭 없으면 0
- ack: open만 영향(이미 ack/resolved면 False)
- metrics: None-안전, page_count=0 division 방지
- graceful: 예외 시 안전값 반환
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from src.alarm.infrastructure.incident_repository import PostgresIncidentStore

REF = datetime(2026, 6, 30, 9, 0, 0, tzinfo=timezone.utc)


class _FakeConn:
    """asyncpg 연결 대역 — 큐된 반환값과 호출 이력을 제공한다."""

    def __init__(self) -> None:
        self.fetchval_returns: deque = deque()
        self.execute_returns: deque = deque()
        self.fetchrow_return = None
        self.fetch_return: list = []
        self.calls: list[tuple] = []
        self.raise_on: set[str] = set()

    async def fetchval(self, query, *args):  # noqa: ANN001
        self.calls.append(("fetchval", query, args))
        if "fetchval" in self.raise_on:
            raise RuntimeError("boom")
        return self.fetchval_returns.popleft() if self.fetchval_returns else None

    async def execute(self, query, *args):  # noqa: ANN001
        self.calls.append(("execute", query, args))
        if "execute" in self.raise_on:
            raise RuntimeError("boom")
        return self.execute_returns.popleft() if self.execute_returns else "UPDATE 0"

    async def fetchrow(self, query, *args):  # noqa: ANN001
        self.calls.append(("fetchrow", query, args))
        if "fetchrow" in self.raise_on:
            raise RuntimeError("boom")
        return self.fetchrow_return

    async def fetch(self, query, *args):  # noqa: ANN001
        self.calls.append(("fetch", query, args))
        if "fetch" in self.raise_on:
            raise RuntimeError("boom")
        return self.fetch_return


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *a) -> bool:  # noqa: ANN002
        return False


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


def _store(conn: _FakeConn) -> PostgresIncidentStore:
    return PostgresIncidentStore(_FakePool(conn))


# ─── create_open ─────────────────────────────────────────────────────────────

async def test_create_open_returns_inserted_id():
    conn = _FakeConn()
    conn.fetchval_returns.append(42)
    iid = await _store(conn).create_open(
        fingerprint="fp", alarm_id="A-1", alarm_name="CPU 임계", db_id="polestar",
        server_name="srv-1", severity=2, priority="320", tier="page", created_at=REF,
    )
    assert iid == 42
    # INSERT ... RETURNING id 가 호출되었는지(첫 호출이 fetchval)
    assert conn.calls[0][0] == "fetchval"
    assert "INSERT INTO alarm_incidents" in conn.calls[0][1]
    # alarm_name 컬럼이 INSERT에 포함되고 값이 바인딩되는지 (D-049 delta)
    assert "alarm_name" in conn.calls[0][1]
    assert "CPU 임계" in conn.calls[0][2]


async def test_create_open_binds_args_in_exact_column_order():
    """INSERT의 컬럼 순서와 위치 인자($N) 매핑이 정확한지 고정한다 (D-049 $오프셋 회귀 가드).

    alarm_name을 3번째 컬럼($3)으로 추가하면서 이후 모든 플레이스홀더가 한 칸씩
    밀린다. 멤버십(`value in args`) 단언만으로는 위치가 틀려도 통과하므로, 고유 센티넬
    값으로 args 튜플 전체를 **순서까지** 단언하고 컬럼 목록의 상대 위치를 검증한다.
    """
    conn = _FakeConn()
    conn.fetchval_returns.append(7)
    await _store(conn).create_open(
        fingerprint="FP", alarm_id="AID", alarm_name="ANAME", db_id="DBID",
        server_name="SRV", severity=2, priority="PRIO", tier="page", created_at=REF,
    )
    sql, args = conn.calls[0][1], conn.calls[0][2]
    # 위치 인자 순서 고정: status는 리터럴('open'), created_at은 마지막($9)
    assert args == ("FP", "AID", "ANAME", "DBID", "SRV", 2, "PRIO", "page", REF)
    # 컬럼 목록 상대 순서: alarm_id → alarm_name → db_id (3번째 컬럼 삽입 확인)
    assert sql.index("alarm_id") < sql.index("alarm_name") < sql.index("db_id")
    # VALUES 플레이스홀더 시퀀스 고정 — $오프셋 밀림(예: alarm_name→$4) 즉시 포착
    normalized = " ".join(sql.split())
    assert "($1, $2, $3, $4, $5, $6, $7, $8, 'open', $9)" in normalized
    assert "$10" not in normalized


async def test_create_open_graceful_returns_zero_on_error():
    conn = _FakeConn()
    conn.raise_on.add("fetchval")
    iid = await _store(conn).create_open(
        fingerprint="fp", alarm_id="A-1", db_id="polestar", server_name="srv-1",
        severity=2, priority="320", tier="page", created_at=REF,
    )
    assert iid == 0


# ─── resolve_by_fingerprint ──────────────────────────────────────────────────

async def test_resolve_updates_most_recent_open():
    conn = _FakeConn()
    conn.fetchval_returns.append(7)        # SELECT id → 7
    conn.execute_returns.append("UPDATE 1")
    n = await _store(conn).resolve_by_fingerprint(
        fingerprint="fp", resolved_at=REF, resolution="self_heal"
    )
    assert n == 1
    # 최근 1건 선택 SQL + UPDATE 모두 호출
    assert conn.calls[0][0] == "fetchval" and "ORDER BY created_at DESC" in conn.calls[0][1]
    assert conn.calls[1][0] == "execute" and "status = 'resolved'" in conn.calls[1][1]


async def test_resolve_returns_zero_when_no_open_match():
    conn = _FakeConn()
    conn.fetchval_returns.append(None)     # 매칭 open 없음
    n = await _store(conn).resolve_by_fingerprint(
        fingerprint="fp", resolved_at=REF, resolution="clear"
    )
    assert n == 0
    # UPDATE는 호출되지 않아야 함(SELECT만)
    assert all(c[0] != "execute" for c in conn.calls)


# ─── ack ─────────────────────────────────────────────────────────────────────

async def test_ack_true_when_open_affected():
    conn = _FakeConn()
    conn.execute_returns.append("UPDATE 1")
    ok = await _store(conn).ack(incident_id=5, acked_at=REF, acked_by="operator")
    assert ok is True
    assert "status = 'ack'" in conn.calls[0][1]


async def test_ack_false_when_already_handled():
    conn = _FakeConn()
    conn.execute_returns.append("UPDATE 0")  # 이미 ack/resolved → 영향 0
    ok = await _store(conn).ack(incident_id=5, acked_at=REF, acked_by="operator")
    assert ok is False


async def test_ack_graceful_false_on_error():
    conn = _FakeConn()
    conn.raise_on.add("execute")
    ok = await _store(conn).ack(incident_id=5, acked_at=REF, acked_by="operator")
    assert ok is False


# ─── list_open ───────────────────────────────────────────────────────────────

async def test_list_open_maps_rows_with_iso_times():
    conn = _FakeConn()
    conn.fetch_return = [{
        "id": 1, "fingerprint": "fp", "alarm_id": "A-1", "alarm_name": "CPU 임계",
        "db_id": "polestar", "server_name": "srv-1", "severity": 2, "priority": "320",
        "tier": "page", "status": "open", "created_at": REF, "acked_at": None,
        "acked_by": None, "resolved_at": None, "resolution": None,
    }]
    rows = await _store(conn).list_open(limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == 1
    assert rows[0]["alarm_name"] == "CPU 임계"          # D-049 delta: 알람명 매핑
    assert rows[0]["created_at"] == REF.isoformat()   # datetime → ISO
    assert rows[0]["acked_at"] is None


async def test_list_open_graceful_empty_on_error():
    conn = _FakeConn()
    conn.raise_on.add("fetch")
    assert await _store(conn).list_open() == []


# ─── metrics ─────────────────────────────────────────────────────────────────

async def test_metrics_computes_rates_and_conversion():
    conn = _FakeConn()
    conn.fetchrow_return = {
        "mtta_seconds": 12.0, "mttr_seconds": 100.0, "incident_count": 5,
    }
    conn.fetchval_returns.append(3)        # open_count
    m = await _store(conn).metrics(window_seconds=3600, page_count=10)
    assert m["mtta_seconds"] == 12.0
    assert m["incident_mttr_seconds"] == 100.0
    assert m["incident_conversion_rate"] == 0.5    # 5/10
    assert m["incident_count"] == 5
    assert m["open_count"] == 3


async def test_metrics_none_safe_and_zero_division_guard():
    conn = _FakeConn()
    conn.fetchrow_return = {
        "mtta_seconds": None, "mttr_seconds": None, "incident_count": 0,
    }
    conn.fetchval_returns.append(0)
    m = await _store(conn).metrics(window_seconds=3600, page_count=0)
    assert m["mtta_seconds"] is None
    assert m["incident_mttr_seconds"] is None
    assert m["incident_conversion_rate"] is None    # page_count=0 → None (no division)
    assert m["incident_count"] == 0
    assert m["open_count"] == 0


async def test_metrics_graceful_empty_on_error():
    conn = _FakeConn()
    conn.raise_on.add("fetchrow")
    m = await _store(conn).metrics(window_seconds=3600, page_count=10)
    assert m["mtta_seconds"] is None
    assert m["incident_mttr_seconds"] is None
    assert m["incident_conversion_rate"] is None
    assert m["incident_count"] == 0
    assert m["open_count"] == 0
