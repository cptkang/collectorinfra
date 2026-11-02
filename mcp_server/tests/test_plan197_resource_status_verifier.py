"""plans/197 F2 독립 검증 — MCP `build_resource_status_sql` 조인형 SQL 의미(sqlite 실행) · DB2 비트 동일.

PG 실기동 불가라 sqlite(스키마 ATTACH)로 LEFT JOIN·GROUP BY·MAX·NULL 의미와 유지보수 테이블
중복 행(PK 없음)에서 자원 행이 늘지 않는지만 확인한다.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

try:
    from mcp_server import polestar_tools as pt
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp 패키지가 설치되지 않음")

_BASE_SHA = "37b206e3"
_REPO = Path(__file__).resolve().parents[2]


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS polestar")
    conn.execute(
        "CREATE TABLE polestar.cmm_resource (id INTEGER, name TEXT, resource_type TEXT,"
        " avail_status INTEGER, importance_id INTEGER, dtime INTEGER, platform_resource_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE polestar.cmm_resource_maintenance (resource_id INTEGER, is_maintenance INTEGER)"
    )
    conn.executemany(
        "INSERT INTO polestar.cmm_resource VALUES (?,?,?,?,?,?,?)",
        [
            (10, "srv-a", "server.Server", 0, 3, None, None),
            (11, "cpu", "server.Cpu", 0, 3, None, 10),
            (12, "disk", "server.Disk", 1, 3, None, 10),
        ],
    )
    conn.executemany(
        "INSERT INTO polestar.cmm_resource_maintenance VALUES (?,?)",
        [(10, 1), (10, 0), (11, 0), (11, 0)],
    )
    return conn


def test_pg_join_no_dup_and_null():
    conn = _db()
    cur = conn.execute(pt.build_resource_status_sql(False, "srv-a", 100))
    cols = [d[0].lower() for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    assert len(rows) == 3  # 서버 + 하위 2 — 유지보수 중복 행으로 늘지 않음
    by = {r["resource_name"]: r["is_maintenance"] for r in rows}
    assert by == {"srv-a": 1, "cpu": 0, "disk": None}


def test_pg_injection_escaped():
    conn = _db()
    rows = conn.execute(pt.build_resource_status_sql(False, "x' OR '1'='1", 100)).fetchall()
    assert rows == []


def test_db2_bit_identical_to_base():
    try:
        src = subprocess.run(
            ["git", "show", f"{_BASE_SHA}:mcp_server/mcp_server/polestar_tools.py"],
            cwd=_REPO, capture_output=True, text=True, check=True,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"기준선 소스 읽기 실패: {exc}")
    import sys
    import types

    mod = types.ModuleType("_base197_polestar_tools")
    sys.modules[mod.__name__] = mod
    ns = mod.__dict__
    exec(compile(src, "polestar_tools_base.py", "exec"), ns)  # noqa: S102
    for name in ("srv-a", "a'b"):
        assert pt.build_resource_status_sql(True, name, 50) == ns["build_resource_status_sql"](
            True, name, 50
        )
