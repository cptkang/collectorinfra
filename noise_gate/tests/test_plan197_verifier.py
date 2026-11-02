"""plans/197 독립 검증(verifier) — F2 SQL 의미·DB2 비트 동일 · F3 스코프 sweep.

A. F2 PostgreSQL 조인형 SQL을 sqlite(스키마 ATTACH)로 실제 실행해 의미를 본다 — PG 실기동 불가
   (5434 꺼짐)라 방언 고유 문법은 확인하지 못하지만, LEFT JOIN·GROUP BY 서브쿼리·MAX·NULL 의미와
   유지보수 테이블 중복 행(PK 없음)에서 행이 늘지 않는지는 확인한다.
B. DB2 경로가 계획 시작 SHA(37b206e3)의 함수 출력과 문자 단위로 같은지 — 기준선 소스를 git에서
   읽어 별도 모듈로 적재해 비교한다.
C. F3 도메인 스코프 키(`db_id|domain`)가 만료 sweep에서 함께 정리되는지.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from noise_gate.infrastructure import polestar_hostname_resolver as hr
from noise_gate.infrastructure import polestar_noise_context as nc
from src.security.sql_guard import SQLGuard

_BASE_SHA = "37b206e3"
_REPO = Path(__file__).resolve().parents[2]


def _load_base(rel_path: str, name: str) -> ModuleType:
    try:
        src = subprocess.run(
            ["git", "show", f"{_BASE_SHA}:{rel_path}"],
            cwd=_REPO, capture_output=True, text=True, check=True,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"기준선 소스 읽기 실패: {exc}")
    spec = importlib.util.spec_from_loader(name, loader=None)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    exec(compile(src, rel_path, "exec"), mod.__dict__)  # noqa: S102
    return mod


# ─── A. sqlite 의미 실행 ──────────────────────────────────────────────────────


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS polestar")
    conn.execute(
        "CREATE TABLE polestar.cmm_resource (id INTEGER, name TEXT, hostname TEXT,"
        " avail_status INTEGER, importance_id INTEGER, resource_type TEXT, dtime INTEGER,"
        " platform_resource_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE polestar.cmm_resource_maintenance (resource_id INTEGER, is_maintenance INTEGER)"
    )
    conn.executemany(
        "INSERT INTO polestar.cmm_resource VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "srv-dup", "h-dup", 0, 3, "server.Server", None, None),
            (2, "srv-none", "h-none", 0, 2, "server.Server", None, None),
            (3, "srv-zero", "h-zero", 0, 1, "server.Server", None, None),
        ],
    )
    # PK 없음 — 같은 자원에 0·1 두 행(중복) · srv-none은 행 없음 · srv-zero는 0 한 행
    conn.executemany(
        "INSERT INTO polestar.cmm_resource_maintenance VALUES (?,?)",
        [(1, 0), (1, 1), (1, 1), (3, 0)],
    )
    return conn


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict]:
    cur = conn.execute(sql)
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


class TestPgJoinSemantics:
    def test_noise_signal_no_dup_and_max_and_null(self):
        conn = _db()
        for name, expected in (("srv-dup", 1), ("srv-none", None), ("srv-zero", 0)):
            rows = _rows(conn, nc.build_resource_signal_sql("polestar_cm_yd", name))
            assert len(rows) == 1, name
            assert rows[0]["maintenance"] == expected, name

    def test_hostname_single_no_dup(self):
        conn = _db()
        rows = _rows(conn, hr.build_hostname_sql("polestar_cm_gp", "h-dup", "postgresql"))
        assert len(rows) == 1
        assert rows[0]["is_maintenance"] == 1
        assert rows[0]["hostname"] == "h-dup"

    def test_host_status_many_one_row_per_resource(self):
        conn = _db()
        sql = hr.build_host_status_sql(
            "polestar_cm_gp", ["srv-dup", "srv-none", "h-zero"], "postgresql"
        )
        rows = _rows(conn, sql)
        assert sorted(r["name"] for r in rows) == ["srv-dup", "srv-none", "srv-zero"]
        by = {r["name"]: r["is_maintenance"] for r in rows}
        assert by == {"srv-dup": 1, "srv-none": None, "srv-zero": 0}

    def test_injection_literal_is_escaped(self):
        conn = _db()
        evil = "x' OR '1'='1"
        assert _rows(conn, nc.build_resource_signal_sql("polestar_cm_yd", evil)) == []
        assert _rows(conn, hr.build_hostname_sql("polestar_cm_gp", evil, "postgresql")) == []

    @pytest.mark.parametrize("db_id", ["polestar_cm_gp", "polestar_cm_yd", "polestar"])
    def test_sql_guard_single_safe_select(self, db_id):
        guard = SQLGuard()
        for sql in (
            nc.build_resource_signal_sql(db_id, "a'b"),
            hr.build_hostname_sql(db_id, "a'b", "postgresql"),
            hr.build_host_status_sql(db_id, ["a", "b'c"], "postgresql"),
        ):
            ok, reason = guard.is_safe_select(sql)
            assert ok, (reason, sql)
            assert sql.lstrip().upper().startswith("SELECT")


# ─── B. DB2 비트 동일(기준선 함수 출력 대조) ─────────────────────────────────


class TestDb2BitIdentical:
    def test_noise_resource_signal_db2(self):
        base = _load_base(
            "noise_gate/infrastructure/polestar_noise_context.py", "_base197_noise_ctx"
        )
        for name in ("srv-1", "a'b"):
            assert nc.build_resource_signal_sql(
                "polestar_b0", name, db_engine="db2"
            ) == base.build_resource_signal_sql("polestar_b0", name)

    def test_noise_resource_signal_pg_default_kwarg_is_join(self):
        # 키워드 미지정 호출자(외부 소비처)는 조인형을 받는다 — 호출부 grep상 fetch 하나뿐.
        assert "cmm_resource_maintenance" in nc.build_resource_signal_sql("polestar_b0", "x")

    def test_hostname_db2(self):
        base = _load_base(
            "noise_gate/infrastructure/polestar_hostname_resolver.py", "_base197_hostname"
        )
        assert hr.build_hostname_sql("polestar_b0", "a'b", "db2") == base.build_hostname_sql(
            "polestar_b0", "a'b", "db2"
        )
        assert hr.build_host_status_sql(
            "polestar_b0", ["a", "b'c"], "db2"
        ) == base.build_host_status_sql("polestar_b0", ["a", "b'c"], "db2")

    def test_b0_resolves_db2(self):
        assert nc._is_db2_dialect("polestar_b0") is True
        for db_id in ("polestar_cm_gp", "polestar_cm_yd", "polestar", "jennifer", "nope"):
            assert nc._is_db2_dialect(db_id) is False


# ─── C. F3 스코프 키 sweep ───────────────────────────────────────────────────


def test_domain_scope_keys_swept_after_window():
    from noise_gate.tests.test_plan197_apm_correlation_scope import _apm_event, _worker

    w = _worker(flag=True)
    w._detect_correlated_storm(_apm_event(domain_id=1000, instance="was-a_8080"), 1000.0)
    w._detect_correlated_storm(_apm_event(domain_id=2000, instance="was-b_8080"), 1001.0)
    assert set(w._correlation_clusters) == {
        "jennifer_default|1000",
        "jennifer_default|2000",
    }
    # 창(120초) 밖 다른 도메인 이벤트 1건 — sweep이 옛 두 스코프 키를 모두 지운다.
    w._detect_correlated_storm(_apm_event(domain_id=3000, instance="was-c_8080"), 1300.0)
    assert set(w._correlation_clusters) == {"jennifer_default|3000"}
