"""plans/215 W3 — 보강 읽기 고정 SELECT · 방언 · sql_guard · IN ≤500 · ITAM 개인 칸 0.

적용 연산 SQL도 본다.

적용 연산 실행(`apply_ops`)은 W2 저장소 테스트와 같은 가짜 연결(큐 반환 · SQL·인자 기록)로 본다 —
한 트랜잭션 · 자동 쓰기 WHERE 가드 · 바인드 인자 · 서비스 version 증가.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

import pytest
from src.security.sql_guard import SQLGuard

from noise_gate.domain.biz_service_apply import ApplyOps, CandidateOp, MemberOp, ServiceOp
from noise_gate.domain.biz_service_model import Candidate, CandidateSignal
from noise_gate.infrastructure import biz_service_sql as q
from noise_gate.infrastructure.apm_host_map_sql_scanners import ITAM_DB_ID, ScanSqlError
from noise_gate.infrastructure.biz_service_repository import (
    BizServiceExistsError,
    PostgresBizServiceRepository,
    candidate_key,
)


class _Exec:
    """가짜 실행기 — 호출을 기록하고 처리기가 행을 돌려준다."""

    def __init__(self, handler: Any) -> None:
        self.calls: list[tuple[str, str]] = []
        self._handler = handler

    async def __call__(self, db_id: str, sql: str) -> list[dict[str, Any]]:
        self.calls.append((db_id, sql))
        return self._handler(db_id, sql)


# ── 빌더 · 방언 · 가드 ─────────────────────────────────────────────────
def test_server_page_db2_fetch_first_upper_schema() -> None:
    sql = q.build_attr_server_page_sql("polestar_b0", "db2", after_id=10, page_rows=500)
    assert "FETCH FIRST 500 ROWS ONLY" in sql and "LIMIT" not in sql
    assert "FROM POLESTAR.cmm_resource r" in sql
    assert "r.resource_type = 'server.Server'" in sql and "r.dtime IS NULL" in sql
    assert "r.id > 10" in sql and "ORDER BY r.id" in sql
    for col in ("r.id_ancestry", "r.ctime", "r.avail_depend_resource_id ",
                "r.avail_depend_resource_id_2"):
        assert col in sql


def test_server_page_pg_limit_and_page_cap() -> None:
    sql = q.build_attr_server_page_sql("polestar_cm_gp", "postgresql", after_id=0,
                                       page_rows=5000)
    assert sql.rstrip().endswith("LIMIT 1000")  # 페이지 상한 1000
    assert "FETCH FIRST" not in sql and "FROM polestar.cmm_resource r" in sql


def test_all_builders_pass_sql_guard() -> None:
    guard = SQLGuard()
    sqls = [
        q.build_attr_server_page_sql("polestar_b0", "db2", after_id=0),
        q.build_attr_server_page_sql("polestar_cm_gp", "postgresql", after_id=99),
        q.build_tree_folder_sql("polestar_b0", "db2", list(range(1, 501))),
        q.build_tree_folder_sql("polestar_cm_yd", "postgresql", [3, 1]),
        q.build_itam_contract_page_sql(after=None, inclusive=False),
        q.build_itam_contract_page_sql(after="web01", inclusive=True, page_rows=10),
    ]
    for sql in sqls:
        ok, reason = guard.is_safe_select(sql)
        assert ok, (reason, sql)
        assert sql.startswith("SELECT ")


def test_folder_sql_in_list_cap_and_int_only() -> None:
    sql = q.build_tree_folder_sql("polestar_b0", "db2", [3, 1, 3])
    assert "WHERE f.id IN (1, 3)" in sql and "POLESTAR.cmm_resource" in sql
    assert "f.name AS folder_name" in sql and "f.resource_type AS resource_type" in sql
    with pytest.raises(ScanSqlError):
        q.build_tree_folder_sql("polestar_b0", "db2", list(range(1, 502)))
    with pytest.raises(ScanSqlError):
        q.build_tree_folder_sql("polestar_b0", "db2", [])
    with pytest.raises(ScanSqlError):
        q.build_tree_folder_sql("polestar_b0", "db2", ["1 OR 1=1"])
    with pytest.raises(ScanSqlError) as exc:
        q.build_attr_server_page_sql("polestar_b0", "db2", after_id="1;DROP")  # type: ignore[arg-type]
    assert exc.value.kind == "page_key_invalid"


def test_itam_sql_has_no_personal_columns() -> None:
    """수용 31 — ITAM SELECT 문자열에 담당자 성명·사번 칸 0."""
    sql = q.build_itam_contract_page_sql(after="web01", inclusive=False, page_rows=50)
    for pii in q.ITAM_PII_COLUMNS:
        assert pii not in sql
    assert "Emnm" not in sql and "Empid" not in sql
    assert "FROM `TCDMSIF80`" in sql and sql.rstrip().endswith("LIMIT 50")
    assert "`sevrHostName` > 'web01'" in sql
    for col, alias in q.ITAM_CONTRACT_COLUMNS:
        assert f"`{col}` AS {alias}" in sql
    with pytest.raises(ScanSqlError):
        q.build_itam_contract_page_sql(after="x' OR '1'='1", inclusive=False)


def test_parse_ancestry_drops_root_and_self() -> None:
    assert q.parse_ancestry("1>10812>150578>72616>9001>", "9001") == ("10812", "150578", "72616")
    assert q.parse_ancestry("1>5>x>7", "9") == ("5", "7")
    assert q.parse_ancestry(None, "9") == ()


# ── 읽기 루프 ───────────────────────────────────────────────────────────
async def test_read_server_attrs_keyset_pages() -> None:
    data = [{"RESOURCE_ID": i, "ID_ANCESTRY": f"1>50>{i}>", "CTIME": 1574822258074 + i,
             "DEPEND_1": 3 if i == 1 else None, "DEPEND_2": i} for i in range(1, 6)]

    def handler(db_id: str, sql: str) -> list[dict[str, Any]]:
        after = int(re.search(r"r\.id > (\d+)", sql).group(1))  # type: ignore[union-attr]
        limit = int(re.search(r"FETCH FIRST (\d+)", sql).group(1))  # type: ignore[union-attr]
        return [r for r in data if r["RESOURCE_ID"] > after][:limit]

    ex = _Exec(handler)
    got = await q.read_server_attrs(ex, "polestar_b0", "db2", page_rows=2)
    assert sorted(got) == ["1", "2", "3", "4", "5"]
    assert [re.search(r"r\.id > (\d+)", s).group(1) for _, s in ex.calls] == [  # type: ignore[union-attr]
        "0", "2", "4"]
    assert got["1"].ancestors == ("50",) and got["1"].ctime_ms == 1574822258075
    assert got["1"].depends == ("3",)  # 자기 자신(_2 = 1)은 뺀다
    assert got["4"].depends == ()


async def test_read_tree_folders_chunks_of_500() -> None:
    ex = _Exec(lambda db, sql: [{"folder_id": 7, "folder_name": "카드업무", "resource_type": "g"}]
               if "IN (7" in sql or ", 7," in sql else [])
    ids = [str(i) for i in range(1, 1201)]
    got = await q.read_tree_folders(ex, "polestar_cm_gp", "postgresql", ids)
    assert len(ex.calls) == 3
    for _, sql in ex.calls:
        n = len(re.search(r"IN \(([^)]*)\)", sql).group(1).split(","))  # type: ignore[union-attr]
        assert n <= q.TREE_IN_CHUNK
    assert got["7"].name == "카드업무"


async def test_read_itam_contracts_uses_itam_db() -> None:
    rows = [{"hostname": "cardweb01", "ip_text": "10.0.0.1", "contract_no": "C1",
             "contract_name": "구매", "maint_contract_no": "M1", "owner_branch": "부점"}]
    ex = _Exec(lambda db, sql: rows)
    got = await q.read_itam_contracts(ex)
    assert ex.calls[0][0] == ITAM_DB_ID
    assert got[0].contract_no == "C1" and got[0].owner_branch == "부점"


# ── 후보 키 ─────────────────────────────────────────────────────────────
def test_candidate_key_is_deterministic_and_bounded() -> None:
    k = candidate_key("member", "카드", "sms_server", "pg_a|101")
    assert k == "member|카드|sms_server|pg_a|101"
    assert candidate_key("member", "카드", "sms_server", "pg_a|101") == k
    long = candidate_key("merge", "a" * 300, "service", "b" * 300)
    assert len(long) <= 512 and long.startswith("merge|sha256:")


# ── 적용 연산 SQL ───────────────────────────────────────────────────────
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
        self.fetchval_q: deque[Any] = deque()
        self.fetch_q: deque[Any] = deque()
        self.execute_q: deque[Any] = deque()
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetchval", query, args))
        return self.fetchval_q.popleft() if self.fetchval_q else None

    async def fetch(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetch", query, args))
        return self.fetch_q.popleft() if self.fetch_q else []

    async def execute(self, query: str, *args: Any) -> Any:
        self.calls.append(("execute", query, args))
        return self.execute_q.popleft() if self.execute_q else "UPDATE 1"

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


def _ops() -> ApplyOps:
    return ApplyOps(
        services=(
            ServiceOp("insert", "카드", None, "카드", "active", {"sms_members": 1}),
            ServiceOp("update", "여신", 5, "여신", "active", {"previous": {}}),
            ServiceOp("suggest", "수동", 6, "수동", "active", {"scan_suggests": {"add": ["x"]}}),
            ServiceOp("same", "예금", 7, "예금", "active"),
        ),
        members=(
            MemberOp("insert", "카드", "sms_server", "pg_a|101", None, basis="biz_suffix",
                     confidence="confirmed", display={"server_name": "cardweb01(카드)",
                                                      "zone": "z1", "bogus": "x"}),
            MemberOp("target_gone", "여신", "sms_server", "pg_a|9", 5),
            MemberOp("delete", "여신", "sms_server", "pg_a|8", 5),
        ),
        candidates=(CandidateOp("insert", Candidate(
            "member", "카드", "sms_server", "pg_a|102",
            (CandidateSignal("aux:ip_subnet", "10.0.1.0/24", 2, 0.9),))),),
    )


async def test_apply_ops_one_transaction_guards_and_binding() -> None:
    conn = _Conn()
    repo = PostgresBizServiceRepository(_Pool(conn))
    conn.fetch_q.append([{"id": 5, "service_key": "여신"}, {"id": 6, "service_key": "수동"},
                         {"id": 7, "service_key": "예금"}])
    conn.fetchval_q.extend([11, 21, 31])  # 서비스 insert id · 구성원 insert id · 후보 insert id
    counts = await repo.apply_ops(_ops(), run_id="run1", by="adm")
    assert counts["service_insert"] == 1 and counts["service_update"] == 1
    assert counts["service_suggest"] == 1 and counts["service_same"] == 1
    assert counts["member_insert"] == 1 and counts["member_target_gone"] == 1
    assert counts["member_delete"] == 1 and counts["candidate_insert"] == 1
    kinds = [c[0] for c in conn.calls]
    assert kinds[0] == "begin" and kinds[-1] == "end" and kinds.count("begin") == 1
    sql = {i: " ".join(c[1].split()) for i, c in enumerate(conn.calls)}
    update = next(s for s in sql.values() if s.startswith("UPDATE biz_service SET display_name"))
    assert "origin = 'auto' AND NOT verified AND state <> 'disabled'" in update
    member_insert = next(c for c in conn.calls if "INSERT INTO biz_service_member" in c[1])
    assert member_insert[2][0] == 11  # 같은 트랜잭션에서 만든 서비스 id
    assert "bogus" not in member_insert[1] and "pg_a" in member_insert[2]
    assert "ON CONFLICT (kind, member_key) DO NOTHING" in member_insert[1]
    delete = next(s for s in sql.values() if s.startswith("DELETE FROM biz_service_member"))
    assert "origin = 'auto'" in delete and "state <> 'excluded'" in delete
    cand = next(c for c in conn.calls if "INSERT INTO biz_service_candidate" in c[1])
    assert "ON CONFLICT (candidate_key) DO NOTHING" in cand[1]
    assert cand[2][1] == "member|카드|sms_server|pg_a|102" and cand[2][2] == 11
    bump = conn.calls[-2]
    assert "version = version + 1" in bump[1] and bump[2][0] == [5, 11]
    for _, query, _args in conn.calls:  # 값은 바인드 — SQL 문자열에 업무명·키가 없다
        assert "카드" not in query and "pg_a" not in query


async def test_apply_ops_skips_when_guard_misses() -> None:
    conn = _Conn()
    repo = PostgresBizServiceRepository(_Pool(conn))
    conn.execute_q.extend(["UPDATE 0"])
    ops = ApplyOps(services=(ServiceOp("update", "여신", 5, "여신", "active"),))
    counts = await repo.apply_ops(ops, run_id="r", by="adm")
    assert counts == {"skipped_service_update": 1}
    assert not any("version = version + 1" in c[1] for c in conn.calls)  # 안 바뀐 서비스는 그대로


async def test_apply_ops_new_key_conflict_rolls_back() -> None:
    conn = _Conn()
    repo = PostgresBizServiceRepository(_Pool(conn))
    conn.fetchval_q.extend([None, 42])
    ops = ApplyOps(services=(ServiceOp("insert", "카드", None, "카드", "active"),))
    with pytest.raises(BizServiceExistsError) as exc:
        await repo.apply_ops(ops, run_id="r", by="adm")
    assert exc.value.existing_id == 42
    assert conn.calls[-1][0] == "rollback"
