"""plans/215 W2 — 업무 서비스 PG 저장소(asyncpg 가짜 풀 · 실 PG 불요).

`test_plan210_w1_repository.py`와 같은 방식이다 — 가짜 연결이 큐에 넣은 반환값을 돌려주고 SQL·인자를
기록한다. version 낙관적 잠금(409 예외) · UNIQUE 1서버 1서비스 · 옮기기 한 트랜잭션 · 일괄 상한 ·
정리 기준 · 파라미터 바인딩을 본다.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from noise_gate.infrastructure import biz_service_repository as r
from noise_gate.infrastructure.biz_service_repository import (
    BizServiceExistsError,
    BizServiceMemberExistsError,
    BizServiceVersionConflict,
    PostgresBizServiceRepository,
)

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


class _Tx:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> None:
        self._conn.calls.append(("begin", "", ()))

    async def __aexit__(self, *exc: Any) -> bool:
        self._conn.calls.append(("rollback" if exc[0] else "end", "", ()))
        return False


class _Conn:
    def __init__(self) -> None:
        self.fetchrow_q: deque[Any] = deque()
        self.fetchval_q: deque[Any] = deque()
        self.fetch_q: deque[Any] = deque()
        self.execute_q: deque[Any] = deque()
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetchrow", query, args))
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


def _repo() -> tuple[PostgresBizServiceRepository, _Conn]:
    conn = _Conn()
    return PostgresBizServiceRepository(_Pool(conn)), conn


def _sql(conn: _Conn) -> list[str]:
    return [" ".join(q.split()) for kind, q, _ in conn.calls if q]


def _svc(*, sid: int = 1, version: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "id": sid, "service_key": f"svc{sid}", "display_name": f"서비스{sid}", "tier": None,
        "origin": "manual", "state": "active", "verified": False, "monitored": False,
        "evidence": json.dumps({"a": 1}), "version": version, "updated_at": NOW, **extra,
    }


# ── 서비스 ──────────────────────────────────────────────────────────────
async def test_get_service_includes_members_and_parses_json() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append(_svc())
    conn.fetch_q.append([{"id": 7, "service_id": 1, "kind": "sms_server", "member_key": "pg|1",
                          "evidence": '{"aux": [1]}', "updated_at": NOW}])
    got = await repo.get_service(1)
    assert got is not None
    assert got["evidence"] == {"a": 1} and got["updated_at"] == NOW.isoformat()
    assert got["members"][0]["evidence"] == {"aux": [1]}


async def test_get_service_missing_reads_no_members() -> None:
    repo, conn = _repo()
    assert await repo.get_service(9) is None
    assert len(conn.calls) == 1


async def test_create_service_manual_and_key_conflict() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append(_svc())
    await repo.create_service(service_key=" svc1 ", display_name="서비스1", tier=2, by="adm")
    _, sql, args = conn.calls[0]
    assert "ON CONFLICT (service_key) DO NOTHING" in sql
    assert args[0] == "svc1" and args[2] == 2 and r.ORIGIN_MANUAL in args
    conn.fetchval_q.append(5)
    with pytest.raises(BizServiceExistsError) as exc:
        await repo.create_service(service_key="svc1", display_name="x")
    assert exc.value.existing_id == 5


@pytest.mark.parametrize("kwargs", [
    {"service_key": "", "display_name": "x"},
    {"service_key": "k", "display_name": " "},
    {"service_key": "k" * 129, "display_name": "x"},
    {"service_key": "k", "display_name": "x", "tier": 4},
    {"service_key": "k", "display_name": "x", "tier": 0},
    {"service_key": "k", "display_name": "x", "tier": True},
    {"service_key": "k", "display_name": "x", "description": "d" * 501},
    {"service_key": "k", "display_name": "x", "owner_org": "o" * 61},
])
async def test_create_service_rejects_invalid_values(kwargs: dict[str, Any]) -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.create_service(**kwargs)
    assert conn.calls == []


async def test_create_service_accepts_limits() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append(_svc())
    await repo.create_service(service_key="k", display_name="x", tier=3,
                              description="가" * 500, owner_org="나" * 60)
    assert conn.calls


async def test_update_service_binds_whitelisted_columns_and_bumps_version() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append(_svc(version=4))
    got = await repo.update_service(
        1, version=3, changes={"tier": None, "monitored": True, "description": "설명"}, by="adm",
    )
    assert got is not None and got["version"] == 4
    _, sql, args = conn.calls[0]
    flat = " ".join(sql.split())
    assert "WHERE id = $1 AND version = $2" in flat and "version = version + 1" in flat
    assert "tier = $" in flat and "monitored = $" in flat and "description = $" in flat
    assert args[:3] == (1, 3, "adm") and None in args and True in args and "설명" in args


async def test_update_service_version_conflict_raises_with_current() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(7)  # UPDATE 0행 → 현재 version 7
    with pytest.raises(BizServiceVersionConflict) as exc:
        await repo.update_service(1, version=3, changes={"note": "n"})
    assert exc.value.service_id == 1 and exc.value.current_version == 7


async def test_update_service_missing_returns_none() -> None:
    repo, conn = _repo()
    assert await repo.update_service(1, version=1, changes={"note": "n"}) is None


@pytest.mark.parametrize("changes", [
    {}, {"service_key": "x"}, {"origin": "auto"}, {"state": "ambiguous"}, {"tier": 5},
    {"monitored": "yes"}, {"display_name": ""}, {"owner_org": "x" * 61},
])
async def test_update_service_rejects_invalid_changes(changes: dict[str, Any]) -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.update_service(1, version=1, changes=changes)
    assert conn.calls == []


async def test_list_services_binds_filters_escapes_like_and_caps_page() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(3)
    conn.fetch_q.append([_svc(sms_count=2, apm_count=1)])
    rows, total = await repo.list_services(
        zone="z1", tier=1, state="active", monitored=True, query="web_0%", limit=500, offset=-5,
    )
    assert total == 3 and rows[0]["sms_count"] == 2
    _, count_sql, count_args = conn.calls[0]
    _, list_sql, list_args = conn.calls[1]
    assert count_args == ("active", 1, True, "z1", "%web\\_0\\%%")
    assert list_args[-2:] == (r.MAX_LIST_LIMIT, 0)
    flat = " ".join(list_sql.split())
    assert "ILIKE $5" in flat and "web_0" not in flat and "q.hostname ILIKE" in flat
    assert "ORDER BY s.tier ASC NULLS LAST" in flat


async def test_list_services_no_filters_has_no_where() -> None:
    repo, conn = _repo()
    await repo.list_services()
    assert "WHERE" not in conn.calls[0][1]
    assert conn.calls[1][2] == (50, 0)


async def test_active_services_with_members_groups_by_service() -> None:
    repo, conn = _repo()
    conn.fetch_q.extend([
        [_svc(sid=1), _svc(sid=2)],
        [{"id": 10, "service_id": 2, "kind": "sms_server", "member_key": "pg|1"}],
    ])
    got = await repo.active_services_with_members()
    assert [(s["id"], len(s["members"])) for s in got] == [(1, 0), (2, 1)]
    assert "state = 'active'" in conn.calls[0][1]
    assert "m.state = 'active'" in conn.calls[1][1]


# ── 구성원 ──────────────────────────────────────────────────────────────
async def test_add_member_manual_in_one_transaction_with_version_bump() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(2)
    conn.fetchrow_q.append({"id": 11, "service_id": 1, "kind": "sms_server",
                            "member_key": "pg_a|101", "evidence": "{}"})
    got = await repo.add_member(
        1, kind="sms_server", member_key="pg_a|101", version=2, layer="web",
        display={"db_id": "pg_a", "resource_id": 101, "hostname": "web01"}, by="adm",
    )
    assert got["id"] == 11
    kinds = [k for k, _, _ in conn.calls]
    assert kinds[0] == "begin" and kinds[-1] == "end"
    sql = _sql(conn)
    assert "FOR UPDATE" in sql[0] and "version = version + 1" in sql[1]
    assert "ON CONFLICT (kind, member_key) DO NOTHING" in sql[2]
    args = conn.calls[3][2]
    assert "101" in args and r.ORIGIN_MANUAL in args and "web" in args


async def test_add_member_duplicate_reports_owning_service() -> None:
    """UNIQUE(kind, member_key) — SMS 서버 1대는 서비스 1개(다른 서비스에 추가하면 거부)."""
    repo, conn = _repo()
    conn.fetchval_q.append(1)
    conn.fetchrow_q.extend([None, {"id": 5, "service_id": 9}])
    with pytest.raises(BizServiceMemberExistsError) as exc:
        await repo.add_member(2, kind="sms_server", member_key="pg_a|101")
    assert exc.value.member_id == 5 and exc.value.service_id == 9
    assert conn.calls[-1][0] == "rollback"


async def test_add_member_version_conflict_writes_nothing() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(5)
    with pytest.raises(BizServiceVersionConflict):
        await repo.add_member(1, kind="sms_server", member_key="pg|1", version=4)
    assert not any("INSERT" in q or " SET " in q for _, q, _ in conn.calls)


async def test_add_member_missing_service_raises_lookup() -> None:
    repo, _ = _repo()
    with pytest.raises(LookupError):
        await repo.add_member(1, kind="sms_server", member_key="pg|1")


@pytest.mark.parametrize("kwargs", [
    {"kind": "sms_server", "member_key": "pg"},
    {"kind": "sms_server", "member_key": "pg|1|2"},
    {"kind": "apm_instance", "member_key": "jen|7|pod|x"},
    {"kind": "nope", "member_key": "a|b"},
    {"kind": "sms_server", "member_key": "pg|1", "layer": "mq"},
    {"kind": "sms_server", "member_key": "pg|1", "confidence": "sure"},
    {"kind": "sms_server", "member_key": "pg|1", "display": {"evidence": "x"}},
])
async def test_add_member_rejects_invalid(kwargs: dict[str, Any]) -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.add_member(1, **kwargs)
    assert conn.calls == []


async def test_set_members_state_and_confirmed_scope_to_service() -> None:
    repo, conn = _repo()
    conn.fetchval_q.extend([1, 2])
    conn.execute_q.extend(["UPDATE 1", "UPDATE 2", "UPDATE 1", "UPDATE 2"])
    assert await repo.set_members_state(1, [3, 3, 4], state="excluded") == 2
    update = conn.calls[3]
    assert "service_id = $1 AND id = ANY($2::bigint[])" in update[1]
    assert update[2][:3] == (1, [3, 4], "excluded")
    assert await repo.set_members_confirmed(1, [3], confirmed=False) == 2
    assert conn.calls[-2][2][2] == r.CONFIDENCE_INFERRED
    with pytest.raises(ValueError):
        await repo.set_members_state(1, [3], state="target_gone")


async def test_set_members_layer_marks_manual_origin() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(1)
    conn.execute_q.extend(["UPDATE 1", "UPDATE 2"])
    assert await repo.set_members_layer(1, [3, 4], layer="was") == 2
    _, sql, args = conn.calls[3]
    assert "layer_origin = $4" in sql and args[2:4] == ("was", r.ORIGIN_MANUAL)
    with pytest.raises(ValueError):
        await repo.set_members_layer(1, [3], layer="mq")


async def test_move_members_locks_both_in_id_order_in_one_transaction() -> None:
    repo, conn = _repo()
    conn.fetchval_q.extend([4, 6])
    conn.execute_q.extend(["UPDATE 1", "UPDATE 1", "UPDATE 2"])
    moved = await repo.move_members([10, 11], from_service_id=9, to_service_id=3,
                                    from_version=6, to_version=4, by="adm")
    assert moved == 2
    kinds = [k for k, _, _ in conn.calls]
    assert kinds.count("begin") == 1 and kinds[0] == "begin" and kinds[-1] == "end"
    locks = [c for c in conn.calls if "FOR UPDATE" in c[1]]
    assert [c[2][0] for c in locks] == [3, 9]  # id 순 잠금(교착 방지)
    _, sql, args = conn.calls[-2]
    assert "WHERE id = ANY($1::bigint[]) AND service_id = $3" in sql and "moved_from" in sql
    assert args == ([10, 11], 3, 9, r.ORIGIN_MANUAL, "adm")


async def test_move_members_conflict_on_target_version_rolls_back() -> None:
    repo, conn = _repo()
    conn.fetchval_q.extend([5])  # 먼저 잠그는 3번의 version이 다르다
    with pytest.raises(BizServiceVersionConflict) as exc:
        await repo.move_members([10], from_service_id=9, to_service_id=3, to_version=4)
    assert exc.value.service_id == 3
    assert not any("biz_service_member" in q for _, q, _ in conn.calls)
    assert conn.calls[-1][0] == "rollback"


async def test_move_members_same_service_rejected() -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.move_members([1], from_service_id=2, to_service_id=2)
    assert conn.calls == []


# ── 일괄 상한 ───────────────────────────────────────────────────────────
async def test_bulk_limits_reject_over_200_and_empty() -> None:
    repo, conn = _repo()
    over = list(range(r.MAX_BULK + 1))
    with pytest.raises(ValueError):
        await repo.set_members_state(1, over, state="excluded")
    with pytest.raises(ValueError):
        await repo.set_members_layer(1, over, layer="db")
    with pytest.raises(ValueError):
        await repo.move_members(over, from_service_id=1, to_service_id=2)
    with pytest.raises(ValueError):
        await repo.set_candidates_status(over, status="accepted")
    with pytest.raises(ValueError):
        await repo.set_members_confirmed(1, [], confirmed=True)
    assert conn.calls == []


async def test_bulk_exactly_200_is_allowed() -> None:
    repo, conn = _repo()
    rows = await repo.set_candidates_status(list(range(r.MAX_BULK)), status="rejected", by="adm")
    assert rows == [] and len(conn.calls[0][2][0]) == r.MAX_BULK


# ── 후보 ───────────────────────────────────────────────────────────────
async def test_candidates_list_and_status_change() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(1)
    conn.fetch_q.append([{"id": 1, "kind": "member", "signals": '[{"k": "aux:ip_subnet"}]',
                          "evidence": "{}", "status": "open"}])
    rows, total = await repo.list_candidates(kind="member", service_id=4, limit=999)
    assert total == 1 and rows[0]["signals"] == [{"k": "aux:ip_subnet"}]
    assert conn.calls[1][2][-2:] == (r.MAX_BULK, 0)
    assert conn.calls[0][2] == ("open", "member", 4)
    conn.fetch_q.append([{"id": 1, "status": "rejected", "signals": "[]"}])
    out = await repo.set_candidates_status([1], status="rejected", by="adm")
    assert out[0]["status"] == "rejected"
    assert "status <> $2" in conn.calls[-1][1]
    with pytest.raises(ValueError):
        await repo.set_candidates_status([1], status="closed")


# ── 실행 기록 ───────────────────────────────────────────────────────────
async def test_runs_start_finish_list_and_abort() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append({"run_id": "r1", "mode": "preview", "status": "running",
                            "summary": "{}", "started_at": NOW})
    run = await repo.start_run(mode="preview", requested_by="adm", scan_run_id="s1", run_id="r1")
    assert run["started_at"] == NOW.isoformat()
    assert conn.calls[0][2] == ("r1", "adm", "s1", "preview", "running")
    conn.fetchrow_q.append({"run_id": "r1", "summary": '{"total": 3}', "status": "done"})
    done = await repo.finish_run("r1", status="done", summary={"total": 3})
    assert done is not None and done["summary"] == {"total": 3}
    assert json.loads(conn.calls[-1][2][2]) == {"total": 3}
    conn.fetch_q.append([{"run_id": "r1", "summary": '{"total": 3}'}])
    runs = await repo.list_runs(mode="apply", limit=1000)
    assert runs[0]["summary"] == {"total": 3} and conn.calls[-1][2] == ("apply", r.MAX_LIST_LIMIT)
    conn.execute_q.append("UPDATE 2")
    assert await repo.abort_running_runs() == 2
    with pytest.raises(ValueError):
        await repo.start_run(mode="dry")
    with pytest.raises(ValueError):
        await repo.finish_run("r1", status="running")


# ── 보강 속성 ───────────────────────────────────────────────────────────
async def test_save_attrs_replaces_run_and_keeps_latest_three_runs() -> None:
    repo, conn = _repo()
    n = await repo.save_attrs("r9", [
        {"db_id": "pg_a", "resource_id": 101, "tree_group_id": 5, "depends": [1, 2],
         "registered_at": "2026-01-01T00:00:00+00:00", "owner": "사람이름"},
        {"db_id": "", "resource_id": 2},
    ])
    assert n == 1
    kinds = [k for k, _, _ in conn.calls]
    assert kinds == ["begin", "execute", "executemany", "execute", "end"]
    record = conn.calls[2][2][0]
    assert record[:4] == ("r9", "pg_a", "101", "5")
    assert isinstance(record[6], datetime) and json.loads(record[7]) == [1, 2]
    assert "사람이름" not in record
    assert conn.calls[3][2] == (r.ATTR_KEEP_RUNS,) and "LIMIT $1" in conn.calls[3][1]


async def test_get_attrs_latest_run_or_empty() -> None:
    repo, conn = _repo()
    assert await repo.get_attrs() == (None, [])
    conn.fetchval_q.append("r3")
    conn.fetch_q.append([{"run_id": "r3", "db_id": "pg", "resource_id": "1", "depends": "[7]"}])
    rid, rows = await repo.get_attrs()
    assert rid == "r3" and rows[0]["depends"] == [7]


# ── 상태 전이 ───────────────────────────────────────────────────────────
async def test_state_log_add_and_list() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append({"id": 1, "service_id": 2, "reason": '{"r": 1}', "at": NOW})
    row = await repo.add_state_log(2, from_state="ok", to_state="risk", reason={"r": 1}, at=NOW)
    assert row["reason"] == {"r": 1}
    assert conn.calls[0][2] == (2, "ok", "risk", '{"r": 1}', NOW)
    await repo.list_state_log(2, since=NOW, limit=7)
    assert conn.calls[-1][2] == (2, NOW, 7)
    await repo.list_state_log(2)
    assert conn.calls[-1][2] == (2, 50)


# ── 정리 ───────────────────────────────────────────────────────────────
async def test_cleanup_cutoffs_and_counts() -> None:
    repo, conn = _repo()
    conn.execute_q.extend(["DELETE 2", "DELETE 3", "DELETE 1"])
    out = await repo.cleanup(now=NOW)
    assert out == {"runs": 2, "state_logs": 3, "gone_members": 1}
    runs, logs, gone = conn.calls
    assert runs[2] == (NOW - timedelta(days=90), "running")
    assert logs[2] == (NOW - timedelta(days=90),)
    assert gone[2] == ("target_gone", "auto", NOW - timedelta(days=180))
    assert "origin = $2" in gone[1]  # 수동 구성원은 지우지 않는다


async def test_transaction_helper_yields_one_conn_in_transaction() -> None:
    repo, conn = _repo()
    async with repo.transaction() as tx:
        assert tx is conn
        await tx.execute("SELECT 1")
    assert [k for k, _, _ in conn.calls] == ["begin", "execute", "end"]
