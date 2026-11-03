"""plans/210 W1 — 연계 매핑 PG 저장소(asyncpg 가짜 풀 · 실 PG 불요).

`test_incident_repository.py`와 같은 방식이다 — 가짜 연결이 큐에 넣은 반환값을 돌려주고 SQL·인자를
기록한다. 쓰기 규칙 자체는 도메인 테스트가 보고, 여기서는 「잠금 → 판정 → 어느 SQL」 배선과
파라미터 바인딩·version 충돌·행별 격리를 본다.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from typing import Any

import pytest

from noise_gate.domain import apm_host_map as m
from noise_gate.infrastructure.apm_host_map_repository import (
    SNAPSHOT_COLUMNS,
    PostgresApmHostMapRepository,
)

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


class _Tx:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> None:
        self._conn.calls.append(("begin", "", ()))

    async def __aexit__(self, *exc: Any) -> bool:
        self._conn.calls.append(("end", "", ()))
        return False


class _Conn:
    def __init__(self) -> None:
        self.fetchrow_q: deque[Any] = deque()
        self.fetchval_q: deque[Any] = deque()
        self.fetch_q: deque[Any] = deque()
        self.execute_q: deque[Any] = deque()
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.raise_on_fetchrow = 0

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetchrow", query, args))
        if self.raise_on_fetchrow:
            self.raise_on_fetchrow -= 1
            raise RuntimeError("boom")
        return self.fetchrow_q.popleft() if self.fetchrow_q else None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetchval", query, args))
        return self.fetchval_q.popleft() if self.fetchval_q else None

    async def fetch(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetch", query, args))
        return self.fetch_q.popleft() if self.fetch_q else []

    async def execute(self, query: str, *args: Any) -> Any:
        self.calls.append(("execute", query, args))
        return self.execute_q.popleft() if self.execute_q else "UPDATE 0"

    async def executemany(self, query: str, records: Any) -> None:
        self.calls.append(("executemany", query, tuple(records)))

    def transaction(self) -> _Tx:
        return _Tx(self)


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Conn:
        return self._conn

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


def _repo() -> tuple[PostgresApmHostMapRepository, _Conn]:
    conn = _Conn()
    return PostgresApmHostMapRepository(_Pool(conn)), conn


def _target(name: str) -> dict[str, Any]:
    return m.make_target(db_id="pg_a", server_name=name)


def _row(*, rid: int = 1, origin: str = "auto", state: str = "active", verified: bool = False,
         targets: list[dict[str, Any]] | None = None, version: int = 1) -> dict[str, Any]:
    return {
        "id": rid, "source_id": "jen", "domain_id": "7", "scope": "instance",
        "match_key": "web01", "origin": origin, "basis": "vm_exact", "state": state,
        "verified": verified,
        "targets": json.dumps(targets if targets is not None else [_target("x")]),
        "evidence": json.dumps({}), "version": version, "updated_at": NOW,
    }


def _proposal(name: str = "x", state: str = m.STATE_ACTIVE) -> m.AutoProposal:
    return m.AutoProposal(
        domain_id="7", scope=m.SCOPE_INSTANCE, match_key="web01", basis=m.BASIS_VM_EXACT,
        state=state, targets=(_target(name),), evidence={"stage": "vm_exact"},
    )


def _sql(conn: _Conn) -> list[str]:
    return [" ".join(q.split()) for kind, q, _ in conn.calls if kind not in ("begin", "end")]


# ── 행 변환 ─────────────────────────────────────────────────────────────
async def test_get_parses_jsonb_and_iso_times() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append(_row())
    got = await repo.get(1)
    assert got is not None
    assert got["targets"][0]["server_name"] == "x"
    assert got["evidence"] == {} and got["updated_at"] == NOW.isoformat()
    assert conn.calls[0][2] == (1,)


# ── 자동 반영 (잠금 → 판정 → SQL) ───────────────────────────────────────
async def test_apply_auto_insert_new_key() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.extend([None, _row()])
    action, row = await repo.apply_auto("jen", _proposal(), now=NOW)
    assert action == m.WRITE_INSERT and row is not None
    sql = _sql(conn)
    assert "FOR UPDATE" in sql[0] and sql[1].startswith("INSERT INTO apm_host_map")
    assert "ON CONFLICT" in sql[1]
    assert conn.calls[0][0] == "begin"
    args = conn.calls[2][2]
    assert args[:4] == ("jen", "7", "instance", "web01")
    assert m.ORIGIN_AUTO in args and NOW in args


async def test_apply_auto_insert_race_retries_and_applies_rule() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.extend([None, None, _row(targets=[_target("y")]), _row(version=2)])
    action, _row_out = await repo.apply_auto("jen", _proposal("x"), now=NOW)
    assert action == m.WRITE_UPDATE
    sql = _sql(conn)
    assert "version = version + 1" in sql[3] and "previous_targets" in conn.calls[-2][2][4]


@pytest.mark.parametrize(("existing", "action", "marker"), [
    (_row(origin="manual", targets=[_target("y")]), m.WRITE_SUGGEST, "SET evidence = $2::jsonb"),
    (_row(verified=True, targets=[_target("y")]), m.WRITE_SUGGEST, "SET evidence = $2::jsonb"),
    (_row(origin="manual", state="disabled", targets=[]), m.WRITE_SKIP,
     "SET last_seen_at = $2 WHERE"),
    (_row(targets=[_target("x")]), m.WRITE_SAME, "SET updated_at = $2, last_seen_at = $2"),
    (_row(targets=[_target("y")]), m.WRITE_UPDATE, "targets = $4::jsonb"),
])
async def test_apply_auto_existing_rows_follow_write_rule(
    existing: dict[str, Any], action: str, marker: str,
) -> None:
    repo, conn = _repo()
    conn.fetchrow_q.extend([existing, existing])
    got, _ = await repo.apply_auto("jen", _proposal("x"), now=NOW)
    assert got == action
    sql = _sql(conn)
    assert len(sql) == 2 and marker in sql[1]
    if action == m.WRITE_SUGGEST:
        evidence = json.loads(conn.calls[-2][2][1])
        assert evidence["scan_suggests"]["targets"][0]["server_name"] == "x"
    if action == m.WRITE_SAME:
        assert "version" not in sql[1]


async def test_apply_auto_failed_state_sets_retry() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.extend([None, _row(state="failed")])
    await repo.apply_auto("jen", _proposal(state=m.STATE_FAILED), now=NOW, retry_hours=3)
    args = conn.calls[2][2]
    assert args[-1] == datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


async def test_apply_auto_rejects_bad_key_and_disabled_state() -> None:
    repo, _ = _repo()
    with pytest.raises(ValueError):
        await repo.apply_auto("jen:x", _proposal())
    with pytest.raises(ValueError):
        await repo.apply_auto("jen", _proposal(state=m.STATE_DISABLED))


async def test_apply_auto_many_isolates_row_failures() -> None:
    repo, conn = _repo()
    conn.raise_on_fetchrow = 1
    conn.fetchrow_q.extend([None, _row()])
    out = await repo.apply_auto_many("jen", [_proposal("a"), _proposal("b")], now=NOW)
    assert [a for a, _ in out] == ["error", m.WRITE_INSERT]


# ── 관리 동작 ───────────────────────────────────────────────────────────
async def test_create_manual_and_conflict() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append(_row(origin="manual"))
    row = await repo.create_manual(source_id="jen", domain_id="7", scope="instance",
                                   match_key="web01", targets=[_target("x")], by="admin")
    assert row["origin"] == "manual"
    args = conn.calls[0][2]
    assert args[6:9] == ("manual", "manual", "active")
    conn.fetchrow_q.append(None)
    conn.fetchval_q.append(9)
    with pytest.raises(m.ApmHostMapExistsError) as e:
        await repo.create_manual(source_id="jen", domain_id="7", scope="instance",
                                 match_key="web01", targets=[])
    assert e.value.existing_id == 9
    with pytest.raises(ValueError):
        await repo.create_manual(source_id="jen", domain_id="7", scope="instance",
                                 match_key="web01", targets=[], state="failed")


async def test_update_sets_manual_origin_and_version_conflict() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append(_row(origin="manual", version=3))
    row = await repo.update(1, version=2, targets=[_target("z")], note="교정", by="admin")
    assert row is not None
    sql = _sql(conn)[0]
    assert "origin = $8" in sql and "version = version + 1" in sql
    assert "WHERE id = $1 AND version = $2" in sql
    assert conn.calls[0][2][-1] == m.ORIGIN_MANUAL
    # version 불일치 → 전용 예외 · 없는 행 → None
    conn.fetchrow_q.append(None)
    conn.fetchval_q.append(5)
    with pytest.raises(m.ApmHostMapVersionError) as e:
        await repo.update(1, version=2, state="disabled")
    assert e.value.current_version == 5
    conn.fetchrow_q.append(None)
    conn.fetchval_q.append(None)
    assert await repo.update(99, version=1, state="disabled") is None
    with pytest.raises(ValueError):
        await repo.update(1, version=1, state="ambiguous")


async def test_verify_and_delete_check_version() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append(_row(verified=True))
    assert (await repo.verify(1, version=1, by="admin")) is not None
    assert "verified = TRUE" in _sql(conn)[0]
    conn.fetchrow_q.append(None)
    conn.fetchval_q.append(4)
    with pytest.raises(m.ApmHostMapVersionError):
        await repo.delete(1, version=1)
    conn.fetchrow_q.append(_row())
    assert (await repo.delete(1, version=4)) is not None


async def test_list_maps_filters_are_bound_parameters() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(2)
    conn.fetch_q.append([_row(), _row(rid=2)])
    rows, total = await repo.list_maps(
        source_id="jen", state="ambiguous", query="50%_x'; DROP", review_only=True,
        limit=10_000, offset=-5,
    )
    assert total == 2 and len(rows) == 2
    count_sql, list_sql = _sql(conn)
    assert "source_id = $1" in count_sql and "state = $2" in count_sql
    assert "ILIKE $3" in count_sql and "evidence ? 'scan_suggests'" in count_sql
    assert "DROP" not in list_sql
    args = conn.calls[1][2]
    assert args[2] == "%50\\%\\_x'; DROP%"
    assert args[-2:] == (500, 0)


async def test_touch_and_set_targets_gone() -> None:
    repo, conn = _repo()
    assert await repo.touch([]) == 0 and conn.calls == []
    conn.execute_q.append("UPDATE 2")
    assert await repo.touch([3, 3, 4], at=NOW) == 2
    assert conn.calls[0][2] == ([3, 4], NOW)
    conn.calls.clear()
    conn.fetch_q.append([
        {"id": 1, "version": 4, "targets": json.dumps([_target("a")])},
        {"id": 2, "version": 1, "targets": json.dumps([_target("b")])},
    ])
    conn.execute_q.append("UPDATE 1")
    changed = await repo.set_targets_gone({("pg_a", "b")}, {"pg_a"})
    assert changed == 1
    upd = conn.calls[-1][2]
    assert upd[0] == 1 and json.loads(upd[1])[0]["target_gone"] is True and upd[2] == 4
    assert await repo.set_targets_gone(set(), set()) == 0


async def test_set_targets_gone_skips_rows_changed_after_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """읽은 뒤 version이 바뀐 행(관리자 수정·자동 반영)은 덮지 않는다 — 낙관적 잠금."""
    repo, conn = _repo()
    conn.fetch_q.append([
        {"id": 1, "version": 2, "targets": json.dumps([_target("a")])},
        {"id": 2, "version": 7, "targets": json.dumps([_target("c")])},
    ])
    conn.execute_q.extend(["UPDATE 0", "UPDATE 1"])  # 1번 행은 그 사이 바뀌었다
    caplog.set_level("INFO")
    assert await repo.set_targets_gone(set(), {"pg_a"}) == 1
    updates = [(" ".join(q.split()), args) for kind, q, args in conn.calls if kind == "execute"]
    assert len(updates) == 2
    assert all(q.endswith("WHERE id = $1 AND version = $3") for q, _ in updates)
    assert [(a[0], a[2]) for _, a in updates] == [(1, 2), (2, 7)]
    assert any("건너뜀: 1건" in r.getMessage() for r in caplog.records)


async def test_cleanup_counts() -> None:
    repo, conn = _repo()
    conn.execute_q.extend(["DELETE 3", "DELETE 1", "DELETE 0"])
    out = await repo.cleanup(now=NOW)
    assert out == {"instance_idle": 3, "unseen": 1, "scan_runs": 0}
    sql = _sql(conn)
    assert "scope = $3" in sql[0] and "verified = FALSE" in sql[1]
    assert conn.calls[0][2][:3] == ("auto", "disabled", "instance")


# ── 스캔 실행 기록 ─────────────────────────────────────────────────────
async def test_scan_runs() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append({"run_id": "r1", "status": "running", "scanners": "{}",
                            "link_summary": "{}", "started_at": NOW})
    run = await repo.start_scan_run(requested_by="admin", run_id="r1")
    assert run["status"] == "running" and run["scanners"] == {}
    conn.fetchrow_q.append({"run_id": "r1", "status": "ok",
                            "scanners": json.dumps({"sms": {"status": "ok"}}),
                            "link_summary": json.dumps({"new": 1})})
    run2 = await repo.update_scan_run("r1", status="ok", scanners={"sms": {"status": "ok"}},
                                      link_summary={"new": 1}, finished=True)
    assert run2 is not None and run2["link_summary"] == {"new": 1}
    with pytest.raises(ValueError):
        await repo.update_scan_run("r1", status="weird")
    conn.execute_q.append("UPDATE 2")
    assert await repo.abort_running_scan_runs() == 2
    assert conn.calls[-1][2] == ("aborted", "running")


# ── 스냅샷 ─────────────────────────────────────────────────────────────
async def test_snapshot_load_swap_and_read() -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.load_snapshot("users; DROP", "r1", [])
    rows = [{"db_id": "pg_a", "server_name": "web01", "ip": "192.0.2.1", "nic_ips": ["192.0.2.2"],
             "password": "never"}]
    assert await repo.load_snapshot("sms_inventory", "r1", rows) == 1
    kinds = [c[0] for c in conn.calls]
    assert kinds == ["begin", "execute", "executemany", "end"]
    many = conn.calls[2]
    assert many[1].startswith("INSERT INTO sms_inventory (run_id, db_id, resource_id")
    assert many[2][0] == ("r1", "pg_a", None, "web01", None, "192.0.2.1", ["192.0.2.2"])
    conn.calls.clear()
    await repo.swap_snapshot("sms_inventory", "r1")
    sql = _sql(conn)
    assert "apm_host_map_snapshot_current" in sql[0] and "run_id <> $1" in sql[1]
    conn.calls.clear()
    assert await repo.snapshot_rows("sms_inventory") == []  # 포인터 없음
    conn.fetchval_q.append("r1")
    conn.fetch_q.append([{"id": 1, "run_id": "r1", "server_name": "web01"}])
    got = await repo.snapshot_rows("sms_inventory", query="web", limit=5)
    assert got[0]["server_name"] == "web01"
    assert "ILIKE $2" in _sql(conn)[-1] and conn.calls[-1][2] == ("r1", "%web%", 5)


def test_snapshot_columns_have_no_pii() -> None:
    cols = {c for cols in SNAPSHOT_COLUMNS.values() for c in cols}
    assert not cols & {"owner", "manager", "email", "phone", "user_name", "password"}
