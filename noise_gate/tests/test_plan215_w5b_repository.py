"""plans/215 W5b — 업무 서비스 저장소 가져오기(`services_brief` · `import_members`).

W2 저장소 테스트의 asyncpg 가짜 풀을 쓴다 — 바인드 값 · 한 트랜잭션 · version 확인 순서 ·
소속 확인 · 새 서비스 키 경합을 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from noise_gate.infrastructure.biz_service_repository import (
    IMPORT_MAX,
    BizServiceExistsError,
    BizServiceVersionConflict,
)
from noise_gate.tests.test_plan215_w2_repository import _repo


def _calls(conn: Any, kind: str) -> list[tuple[str, tuple[Any, ...]]]:
    return [(" ".join(q.split()), args) for k, q, args in conn.calls if k == kind]


def _item(key: str, *, sid: int | None, owner: int | None, layer: str | None = None,
          service_key: str = "ibk") -> dict[str, Any]:
    return {"line": 2, "service_key": service_key, "service_id": sid, "kind": "sms_server",
            "member_key": key, "display": {"db_id": "pg", "resource_id": key.split("|")[1],
                                           "server_name": "web01"},
            "layer": layer, "owner_id": owner}


async def test_services_brief_binds_keys_and_ids() -> None:
    repo, conn = _repo()
    assert await repo.services_brief() == []
    assert conn.calls == []
    conn.fetch_q.append([{"id": 3, "service_key": "ibk", "version": 2}])
    got = await repo.services_brief(keys=["ibk", "ibk", ""], ids=[9, 3])
    assert got == [{"id": 3, "service_key": "ibk", "version": 2}]
    sql, args = _calls(conn, "fetch")[-1]
    assert args == (["ibk"], [3, 9])
    assert "service_key = ANY($1::text[]) OR id = ANY($2::bigint[])" in sql


async def test_import_members_create_bump_lock_insert_move_keep_in_one_transaction() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append({"id": 50, "service_key": "new", "display_name": "새"})  # 생성
    conn.fetchval_q.extend([4, 2])  # version 확인: 서비스 7(대상) → 서비스 9(옮겨 나가는 쪽)
    conn.fetch_q.append([{"id": 22, "service_id": 9, "member_key": "pg|2"},
                         {"id": 33, "service_id": 7, "member_key": "pg|3"}])
    conn.fetchrow_q.extend([{"id": 11, "service_id": 50}, {"id": 22, "service_id": 7},
                            {"id": 33, "service_id": 7}])
    out = await repo.import_members(
        creates=[{"service_key": "new", "display_name": "새", "tier": 2, "owner_org": None}],
        versions={9: 2, 7: 4},
        items=[_item("pg|1", sid=None, owner=None, layer="web", service_key="new"),
               _item("pg|2", sid=7, owner=9), _item("pg|3", sid=7, owner=7, layer="db")],
        by="ops",
    )
    assert [m["outcome"] for m in out["members"]] == ["added", "moved", "kept"]
    assert out["members"][1]["moved_from"] == 9 and out["created"][0]["id"] == 50
    kinds = [k for k, _, _ in conn.calls]
    assert kinds[0] == "begin" and kinds[-1] == "end" and kinds.count("begin") == 1
    create = _calls(conn, "fetchrow")[0]
    assert create[0].startswith("INSERT INTO biz_service ") and "ON CONFLICT" in create[0]
    assert create[1] == ("new", "새", 2, None, "manual", "ops")
    bumps = [a for s, a in _calls(conn, "fetchval") if "FOR UPDATE" in s]
    assert [a[0] for a in bumps] == [7, 9]
    lock_sql, lock_args = _calls(conn, "fetch")[0]
    assert "FOR UPDATE" in lock_sql and lock_args == ("sms_server", ["pg|1", "pg|2", "pg|3"])
    insert = next((s, a) for s, a in _calls(conn, "fetchrow")
                  if s.startswith("INSERT INTO biz_service_member"))
    assert insert[1][:11] == (50, "sms_server", "pg|1", "manual", "manual", "confirmed",
                              "active", "web", "manual", "ops", "ops")
    updates = [(s, a) for s, a in _calls(conn, "fetchrow") if s.startswith("UPDATE")]
    move_sql, move_args = updates[0]
    assert "moved_from" in move_sql and "layer =" not in move_sql
    assert move_args == (22, 7, "manual", "confirmed", "ops", 9)
    keep_sql, keep_args = updates[1]
    assert "layer = $6, layer_origin = $7" in keep_sql and "moved_from" not in keep_sql
    assert keep_args == (33, 7, "manual", "confirmed", "ops", "db", "manual")


async def test_import_members_owner_changed_since_preview_raises_conflict() -> None:
    repo, conn = _repo()
    conn.fetchval_q.extend([4, 6])  # 대상 bump → 그 사이 소속이 된 서비스 12의 version
    conn.fetch_q.append([{"id": 22, "service_id": 12, "member_key": "pg|2"}])
    with pytest.raises(BizServiceVersionConflict) as exc:
        await repo.import_members(creates=[], versions={7: 4},
                                  items=[_item("pg|2", sid=7, owner=None)], by="ops")
    assert (exc.value.service_id, exc.value.current_version) == (12, 6)
    assert [k for k, _, _ in conn.calls][-1] == "rollback"


async def test_import_members_version_mismatch_and_new_key_race() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(5)
    with pytest.raises(BizServiceVersionConflict):
        await repo.import_members(creates=[], versions={7: 4},
                                  items=[_item("pg|1", sid=7, owner=None)])
    repo, conn = _repo()
    conn.fetchrow_q.append(None)
    conn.fetchval_q.append(31)
    with pytest.raises(BizServiceExistsError) as exc:
        await repo.import_members(creates=[{"service_key": "new", "display_name": "새"}],
                                  versions={}, items=[])
    assert exc.value.existing_id == 31


@pytest.mark.parametrize("bad", [
    {"kind": "bogus"}, {"member_key": "nopipe"}, {"display": {"evil": "x"}}, {"layer": "front"},
])
async def test_import_members_rejects_bad_items_before_sql(bad: dict[str, Any]) -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.import_members(creates=[], versions={},
                                  items=[{**_item("pg|1", sid=7, owner=None), **bad}])
    assert conn.calls == []


async def test_import_members_cap_and_bad_create() -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.import_members(creates=[], versions={},
                                  items=[_item("pg|1", sid=7, owner=None)] * (IMPORT_MAX + 1))
    with pytest.raises(ValueError):
        await repo.import_members(creates=[{"service_key": "k", "display_name": "", "tier": 9}],
                                  versions={}, items=[])
    assert conn.calls == []
