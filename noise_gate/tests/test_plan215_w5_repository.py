"""plans/215 W5 — 업무 서비스 저장소 P1 메서드(검색 · 구성원 통계 · 소속 · 일괄 지정 · 담당 부점).

W2 저장소 테스트의 asyncpg 가짜 풀을 쓴다 — 바인드 값·SQL 모양·한 트랜잭션·version 확인을 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from noise_gate.infrastructure.biz_service_repository import (
    SEARCH_CAP,
    BizServiceVersionConflict,
)
from noise_gate.tests.test_plan215_w2_repository import _repo, _sql


def _calls(conn: Any, kind: str) -> list[tuple[str, tuple[Any, ...]]]:
    return [(" ".join(q.split()), args) for k, q, args in conn.calls if k == kind]


async def test_search_services_binds_escaped_query_and_cap() -> None:
    repo, conn = _repo()
    conn.fetch_q.append([{"id": 1, "service_key": "a", "display_name": "A"}])
    rows = await repo.search_services(tier=2, monitored=True, query="10%_x'")
    assert rows == [{"id": 1, "service_key": "a", "display_name": "A"}]
    sql, args = _calls(conn, "fetch")[-1]
    assert args == (2, True, "%10\\%\\_x'%", SEARCH_CAP)
    assert "10%" not in sql and "ILIKE $3" in sql and sql.endswith("LIMIT $4")
    assert sql.startswith("SELECT s.* FROM biz_service s WHERE")


async def test_search_member_names_limited_to_scope_zones() -> None:
    repo, conn = _repo()
    await repo.search_services(query="card-01", member_zones=frozenset({"gongjon"}))
    sql, args = _calls(conn, "fetch")[-1]
    assert args == ("%card-01%", ["gongjon"], SEARCH_CAP)
    assert "q.zone = ANY($2::text[]) AND (q.server_name ILIKE $1 OR q.hostname ILIKE $1)" in sql
    await repo.search_services(query="card-01", member_zones=frozenset())
    assert _calls(conn, "fetch")[-1][1] == ("%card-01%", [], SEARCH_CAP)  # 빈 존 = 구성원 일치 0
    await repo.search_services(query="card-01")
    sql, args = _calls(conn, "fetch")[-1]
    assert args == ("%card-01%", SEARCH_CAP) and "q.zone" not in sql  # 전 존 = 종전 그대로
    await repo.list_services(query="card-01")
    assert "q.zone" not in _sql(conn)[-1]


async def test_member_stats_groups_active_members_without_names() -> None:
    repo, conn = _repo()
    await repo.member_stats()
    sql = _sql(conn)[-1]
    assert "state = 'active'" in sql and "GROUP BY" in sql
    assert "server_name" not in sql and "hostname" not in sql


async def test_member_owners_by_keys_and_all() -> None:
    repo, conn = _repo()
    assert await repo.member_owners("sms_server", []) == {}
    assert conn.calls == []
    conn.fetch_q.append([{"member_key": "pg|1", "id": 7, "service_id": 3, "state": "active",
                          "display_name": "여신"}])
    got = await repo.member_owners("sms_server", ["pg|1", "pg|1", ""])
    assert got == {"pg|1": {"member_id": 7, "service_id": 3, "service_name": "여신",
                            "state": "active"}}
    sql, args = _calls(conn, "fetch")[-1]
    assert args == ("sms_server", ["pg|1"]) and "ANY($2::text[])" in sql
    await repo.member_owners("sms_server")
    assert _calls(conn, "fetch")[-1][1] == ("sms_server",)
    with pytest.raises(ValueError):
        await repo.member_owners("bogus")


async def test_assign_members_add_move_keep_in_one_transaction() -> None:
    repo, conn = _repo()
    items = [("sms_server", "pg|1", {"server_name": "a"}), ("sms_server", "pg|2", {}),
             ("sms_server", "pg|3", {})]
    # 잠금 조회: 1 없음 · 2는 다른 서비스(9) · 3은 이 서비스(5)
    conn.fetchrow_q.extend([None, {"id": 22, "service_id": 9}, {"id": 33, "service_id": 5}])
    conn.fetchval_q.extend([1, 4])  # version 확인: 서비스 5(대상) → 서비스 9
    conn.fetchrow_q.extend([
        {"id": 11, "service_id": 5, "member_key": "pg|1"},
        {"id": 22, "service_id": 5, "member_key": "pg|2"},
        {"id": 33, "service_id": 5, "member_key": "pg|3"},
    ])
    out = await repo.assign_members(5, items, version=1, by="adm")
    assert [(r["outcome"], r["moved_from"]) for r in out] == [
        ("added", None), ("moved", 9), ("kept", None)]
    kinds = [k for k, _, _ in conn.calls]
    assert kinds[0] == "begin" and kinds[-1] == "end" and kinds.count("begin") == 1
    bumps = [a for s, a in _calls(conn, "fetchval") if "FOR UPDATE" in s]
    assert [a[0] for a in bumps] == [5, 9]
    insert = next((s, a) for s, a in _calls(conn, "fetchrow") if s.startswith("INSERT"))
    assert insert[1][:9] == (5, "sms_server", "pg|1", "manual", "manual", "confirmed", "active",
                             "adm", "adm") and insert[1][9:] == ("a",)
    moved = [(s, a) for s, a in _calls(conn, "fetchrow") if s.startswith("UPDATE")]
    assert "moved_from" in moved[0][0] and moved[0][1][-1] == 9
    assert "moved_from" not in moved[1][0] and len(moved[1][1]) == 5


async def test_assign_members_version_conflict_rolls_back() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append(None)
    conn.fetchval_q.append(2)
    with pytest.raises(BizServiceVersionConflict):
        await repo.assign_members(5, [("sms_server", "pg|1", {})], version=1)
    assert [k for k, _, _ in conn.calls][-1] == "rollback"
    assert not any(s.startswith("INSERT") for s in _sql(conn))


@pytest.mark.parametrize("items", [
    [], [("sms_server", f"pg|{i}", {}) for i in range(201)],
    [("sms_server", "no-sep", {})], [("sms_server", "pg|1", {"evil": "x"})],
])
async def test_assign_members_rejects_bad_input_before_sql(items: list[Any]) -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.assign_members(5, items, version=1)
    assert conn.calls == []


async def test_owner_branches_latest_run_non_empty_only() -> None:
    repo, conn = _repo()
    assert await repo.owner_branches([("", "1")]) == {}
    assert conn.calls == []
    conn.fetchval_q.append(None)
    assert await repo.owner_branches([("pg", "1")]) == {}
    conn.fetchval_q.append("run9")
    conn.fetch_q.append([{"db_id": "pg", "resource_id": "1", "owner_branch": "여의도"},
                         {"db_id": "pg", "resource_id": "2", "owner_branch": "엉뚱"}])
    got = await repo.owner_branches([("pg", "1"), ("db2", "2")])
    assert got == {("pg", "1"): "여의도"}  # 요청한 짝만(교차 짝 제거)
    sql, args = _calls(conn, "fetch")[-1]
    assert args == ("run9", ["db2", "pg"], ["2", "1"])
    assert "owner_branch <> ''" in sql
