"""plans/215 W4 — 업무 서비스 저장소 관리자 동작(병합·분할·후보 수락·관리자 후보) SQL.

W2 저장소 테스트의 가짜 연결(`_Conn`·`_Pool`)을 쓴다 — 한 트랜잭션 · id 순 잠금 ·
version 충돌 롤백 · 비활성 병합 거부 · 분할 0건 롤백 · 후보 닫힘 판정 · 관리자 후보 중복을 본다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from noise_gate.infrastructure import biz_service_repository as r
from noise_gate.infrastructure.biz_service_repository import (
    BizServiceCandidateNotOpen,
    BizServiceExistsError,
    BizServiceVersionConflict,
    PostgresBizServiceRepository,
)
from noise_gate.tests.test_plan215_w2_repository import _Conn, _Pool


def _repo() -> tuple[PostgresBizServiceRepository, _Conn]:
    conn = _Conn()
    return PostgresBizServiceRepository(_Pool(conn)), conn


def _kinds(conn: _Conn) -> list[str]:
    return [k for k, _, _ in conn.calls]


def _cand(**extra: Any) -> dict[str, Any]:
    return {"id": 7, "kind": "member", "status": "open", "service_id": 3,
            "member_kind": "sms_server", "member_key": "pg_a|101",
            "signals": json.dumps(["aux_alarm_path"]), "evidence": "{}", **extra}


# ── 후보 수락(member) ─────────────────────────────────────────────────
async def test_accept_member_candidate_inserts_manual_confirmed_with_aux() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.extend([
        _cand(),  # 후보 FOR UPDATE
        None,  # 기존 구성원 없음
        {"id": 50, "service_id": 3, "kind": "sms_server", "member_key": "pg_a|101",
         "evidence": "{}"},  # INSERT RETURNING
        {"id": 7, "status": "accepted", "evidence": "{}"},  # 후보 닫기
    ])
    conn.fetchval_q.append(4)
    got = await repo.accept_member_candidate(7, display={"hostname": "web01", "bogus": 1},
                                             by="adm")
    assert got["member"]["id"] == 50 and got["moved_from"] is None
    assert got["service_version"] == 5 and got["candidate"]["status"] == "accepted"
    kinds = _kinds(conn)
    assert kinds[0] == "begin" and kinds[-1] == "end" and kinds.count("begin") == 1
    insert = next(c for c in conn.calls if "INSERT INTO biz_service_member" in c[1])
    assert "hostname" in insert[1] and "bogus" not in insert[1]
    args = insert[2]
    assert args[3:7] == (r.ORIGIN_MANUAL, r.BASIS_MANUAL, r.CONFIDENCE_CONFIRMED,
                         r.MEMBER_ACTIVE)
    assert json.loads(args[7]) == {"aux": ["aux_alarm_path"], "candidate_id": 7}
    assert "web01" in args


async def test_accept_member_candidate_moves_existing_and_bumps_both_in_id_order() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.extend([
        _cand(service_id=9),
        {"id": 50, "service_id": 2},
        {"id": 50, "service_id": 9, "evidence": "{}"},
        {"id": 7, "status": "accepted"},
    ])
    conn.fetchval_q.extend([1, 6])
    got = await repo.accept_member_candidate(7, by="adm")
    assert got["moved_from"] == 2 and got["service_version"] == 7
    locks = [c[2][0] for c in conn.calls if "FOR UPDATE" in c[1] and "biz_service WHERE" in c[1]]
    assert locks == [2, 9]
    update = next(c for c in conn.calls if c[1].startswith("UPDATE biz_service_member"))
    assert "moved_from" in update[1] and update[2][-2:] == (2, "adm")


@pytest.mark.parametrize(("cand", "exc"), [
    (None, LookupError),
    (_cand(status="rejected"), BizServiceCandidateNotOpen),
    (_cand(kind="merge"), ValueError),
    (_cand(service_id=None), ValueError),
    (_cand(member_key="bad key"), ValueError),
])
async def test_accept_member_candidate_rejects_and_rolls_back(
    cand: dict[str, Any] | None, exc: type[Exception]
) -> None:
    repo, conn = _repo()
    conn.fetchrow_q.append(cand)
    with pytest.raises(exc):
        await repo.accept_member_candidate(7, by="adm")
    assert _kinds(conn)[-1] == "rollback"
    assert not any("INSERT" in q or q.startswith("UPDATE") for _, q, _ in conn.calls)


async def test_close_candidate_distinguishes_missing_and_closed() -> None:
    repo, conn = _repo()
    conn.fetchrow_q.extend([None])
    conn.fetchval_q.append("rejected")
    with pytest.raises(BizServiceCandidateNotOpen) as info:
        await repo._close_candidate(conn, 7, "adm")
    assert info.value.status == "rejected"
    conn.fetchrow_q.append(None)
    conn.fetchval_q.append(None)
    with pytest.raises(LookupError):
        await repo._close_candidate(conn, 8, "adm")


# ── 관리자 후보(APM 대조) ─────────────────────────────────────────────
async def test_insert_member_candidates_counts_existing_and_keeps_rejected() -> None:
    repo, conn = _repo()
    conn.fetchval_q.extend([11, None])  # 두 번째는 이미 있음(거절 포함) → DO NOTHING
    items = [
        {"member_kind": "apm_instance", "member_key": "jen1|10|instance|web-a",
         "signals": ["gateway_business"], "display": {"instance_name": "web-a", "x": 1}},
        {"member_kind": "apm_instance", "member_key": "jen1|10|instance|web-b",
         "signals": ["gateway_business"], "display": {}},
    ]
    got = await repo.insert_member_candidates(3, "svc3", items, run_id=None)
    assert got == {"inserted": 1, "existing": 1}
    first = conn.calls[1]
    assert "ON CONFLICT (candidate_key) DO NOTHING" in first[1]
    assert first[2][0] == r.candidate_key(
        "member", "svc3", "apm_instance", "jen1|10|instance|web-a")
    evidence = json.loads(first[2][5])
    assert evidence["display"] == {"instance_name": "web-a"} and evidence["target_kind"] == (
        "apm_instance")
    assert not any("biz_service_member" in q for _, q, _ in conn.calls)


async def test_insert_member_candidates_rejects_bad_key_and_over_cap() -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.insert_member_candidates(3, "svc3", [{"member_kind": "apm_instance",
                                                         "member_key": "nope"}])
    assert _kinds(conn)[-1] == "rollback"
    with pytest.raises(ValueError):
        await repo.insert_member_candidates(3, "svc3", [{}] * (r.MAX_BULK + 1))


# ── 병합 ──────────────────────────────────────────────────────────────
async def test_merge_moves_members_disables_source_in_one_transaction() -> None:
    repo, conn = _repo()
    conn.fetchval_q.extend([4, 2])  # id 순: 3(B), 9(A)
    conn.fetch_q.append([{"id": 3, "state": "active"}, {"id": 9, "state": "active"}])
    conn.execute_q.extend(["UPDATE 1", "UPDATE 1", "UPDATE 3"])
    got = await repo.merge_services(9, 3, from_version=2, to_version=4, by="adm")
    assert got == {"moved": 3, "from_version": 3, "to_version": 5, "candidate": None}
    kinds = _kinds(conn)
    assert kinds.count("begin") == 1 and kinds[-1] == "end"
    locks = [c[2][0] for c in conn.calls if "FOR UPDATE" in c[1]]
    assert locks == [3, 9]
    sql = [" ".join(q.split()) for _, q, _ in conn.calls if q]
    move = next(s for s in sql if s.startswith("UPDATE biz_service_member"))
    assert "'merged_from'" in move and "WHERE service_id = $1" in move
    assert any("state = 'disabled'" in s and "merged_into" in s for s in sql)
    assert any("jsonb_set(evidence, '{merged_from}'" in s for s in sql)


async def test_merge_rejects_disabled_same_and_version_conflict() -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.merge_services(3, 3)
    conn.fetchval_q.extend([1, 1])
    conn.fetch_q.append([{"id": 3, "state": "active"}, {"id": 9, "state": "disabled"}])
    with pytest.raises(ValueError):
        await repo.merge_services(9, 3)
    assert _kinds(conn)[-1] == "rollback"
    repo, conn = _repo()
    conn.fetchval_q.append(5)
    with pytest.raises(BizServiceVersionConflict) as info:
        await repo.merge_services(9, 3, to_version=4)
    assert info.value.current_version == 5 and _kinds(conn)[-1] == "rollback"


async def test_merge_closes_candidate_in_same_transaction() -> None:
    repo, conn = _repo()
    conn.fetchval_q.extend([1, 1])
    conn.fetch_q.append([{"id": 3, "state": "active"}, {"id": 9, "state": "active"}])
    conn.fetchrow_q.append({"id": 70, "status": "accepted"})
    got = await repo.merge_services(9, 3, by="adm", candidate_id=70)
    assert got["candidate"]["status"] == "accepted"
    assert "status = 'open'" in conn.calls[-2][1] and _kinds(conn)[-1] == "end"


# ── 분할 ──────────────────────────────────────────────────────────────
async def test_split_creates_manual_service_and_moves_selected_members() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(2)
    conn.fetchrow_q.append({"id": 40, "service_key": "svc_new", "evidence": "{}"})
    conn.execute_q.extend(["UPDATE 1", "UPDATE 2"])
    got = await repo.split_service(9, [10, 11], version=2, service_key=" svc_new ",
                                   display_name="새 서비스", tier=2, by="adm")
    assert got["service"]["id"] == 40 and got["moved"] == 2 and got["from_version"] == 3
    insert = next(c for c in conn.calls if "INSERT INTO biz_service" in c[1])
    assert "split_from" in insert[1] and insert[2][:3] == ("svc_new", "새 서비스", 2)
    assert insert[2][5] == r.ORIGIN_MANUAL
    _, sql, args = conn.calls[-2]
    assert "AND service_id = $3" in sql and args[:3] == ([10, 11], 40, 9)


async def test_split_rolls_back_on_zero_moved_and_existing_key() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(1)
    conn.fetchrow_q.append({"id": 40})
    conn.execute_q.extend(["UPDATE 1", "UPDATE 0"])
    with pytest.raises(ValueError):
        await repo.split_service(9, [99], service_key="svc_new", display_name="새")
    assert _kinds(conn)[-1] == "rollback"
    repo, conn = _repo()
    conn.fetchval_q.extend([1, 12])
    conn.fetchrow_q.append(None)
    with pytest.raises(BizServiceExistsError) as info:
        await repo.split_service(9, [10], service_key="svc3", display_name="새")
    assert info.value.existing_id == 12 and _kinds(conn)[-1] == "rollback"


async def test_split_validates_before_transaction() -> None:
    repo, conn = _repo()
    with pytest.raises(ValueError):
        await repo.split_service(9, [10], service_key="svc", display_name="새", tier=4)
    with pytest.raises(ValueError):
        await repo.split_service(9, [10], service_key="svc", display_name="새",
                                 description="x" * 501)
    assert conn.calls == []


# ── 확인 = 관리자 판단(origin manual) ─────────────────────────────────
async def test_confirm_pins_origin_manual_so_recompute_keeps_it() -> None:
    repo, conn = _repo()
    conn.fetchval_q.append(1)
    conn.execute_q.extend(["UPDATE 1", "UPDATE 1"])
    assert await repo.set_members_confirmed(1, [3], confirmed=True, by="adm") == 1
    _, sql, args = conn.calls[-2]
    assert "origin = $5" in sql and "OR origin <> $5" in sql
    assert args == (1, [3], r.CONFIDENCE_CONFIRMED, "adm", r.ORIGIN_MANUAL)
