"""`PostgresAuditRepository.get_stats` 특성화 — 발행 SQL·파라미터 순서·반환 집계 dict.

plans/173 T8 · 166 I5.

plans/173 W2 T8 특성화 스냅샷 — Extract Function 전에 채록했다. 기대값은 「맞는 값」이 아니라
「지금 값」이다(D-337 ⑤). 리팩터 뒤 스냅샷을 갱신해야 한다면 그 변경은 리팩터가 아니다.

가짜 풀/커넥션이 `fetchrow`·`fetch`·`fetchval` 호출을 **발행 순서대로** 기록한다(SQL 문자열 원문 ·
인자). 실 DB 0. 시나리오는 날짜 필터 4조합 × 결과 모양(정상·빈 행·None 집계) + Top 테이블 실패 +
전체 실패 + 날짜 형식 오류(예외 전파).

스냅샷 (재)채록: `UPDATE_PLAN173_T8_SNAPSHOT=1 python -m pytest <이 파일> -q -p no:cacheprovider`
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from src.infrastructure.audit_repository import PostgresAuditRepository

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "snapshots" / "plan173_t8" / "audit_get_stats.json"
)
_UPDATE_ENV = "UPDATE_PLAN173_T8_SNAPSHOT"


class _Conn:
    """발행 순서·SQL·인자를 기록하는 가짜 커넥션.

    `fail_on`이 SQL에 포함되면 그 호출에서 예외를 낸다.
    """

    def __init__(self, *, basic: Any, qe: Any, users: list, tables: list, daily: list,
                 vals: list, fail_on: str | None = None) -> None:
        self.basic, self.qe = basic, qe
        self.users, self.tables, self.daily = users, tables, daily
        self.vals = list(vals)
        self.fail_on = fail_on
        self.calls: list[list[Any]] = []

    def _record(self, kind: str, query: str, args: tuple) -> None:
        self.calls.append([kind, query, [_arg(a) for a in args]])
        if self.fail_on and self.fail_on in query:
            raise RuntimeError(f"boom:{self.fail_on}")

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self._record("fetchrow", query, args)
        return self.basic if "total_requests" in query else self.qe

    async def fetch(self, query: str, *args: Any) -> list:
        self._record("fetch", query, args)
        if "tbl" in query:
            return self.tables
        if "DATE(created_at)" in query:
            return self.daily
        return self.users

    async def fetchval(self, query: str, *args: Any) -> Any:
        self._record("fetchval", query, args)
        return self.vals.pop(0)


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _arg(a: Any) -> Any:
    return a.isoformat() if isinstance(a, datetime) else a


_FULL = {
    "basic": {"total_requests": 42, "unique_users": 3},
    "qe": {"success_rate": 0.833333333, "avg_execution_time_ms": 123.456789},
    "users": [{"user_id": "alice", "count": 30}, {"user_id": "bob", "count": 12}],
    "tables": [{"tbl": "cmm_resource", "count": 9}, {"tbl": "t2", "count": 1}],
    "daily": [{"date": date(2026, 9, 23), "count": 40, "unique_users": 3},
              {"date": None, "count": 2, "unique_users": 1}],
    "vals": [5, 7],
}
_EMPTY = {"basic": None, "qe": None, "users": [], "tables": [], "daily": [], "vals": [None, None]}
_NONE_AGG = {**_EMPTY, "basic": {"total_requests": 0, "unique_users": 0},
             "qe": {"success_rate": None, "avg_execution_time_ms": None}, "vals": [0, 0]}

_DATES: dict[str, dict[str, str]] = {
    "none": {},
    "start": {"start_date": "2026-09-01"},
    "end": {"end_date": "2026-09-23T10:00:00"},
    "both": {"start_date": "2026-09-01T00:00:00+00:00", "end_date": "2026-09-23"},
}


def _scenarios() -> list[tuple[str, dict[str, str], dict[str, Any], str | None]]:
    out: list[tuple[str, dict[str, str], dict[str, Any], str | None]] = []
    for did, dates in _DATES.items():
        out.append((f"full|{did}", dates, _FULL, None))
        out.append((f"tables_fail|{did}", dates, _FULL, "jsonb_array_elements_text"))
    out.append(("empty|none", {}, _EMPTY, None))
    out.append(("none_agg|both", _DATES["both"], _NONE_AGG, None))
    out.append(("outer_fail_basic|start", _DATES["start"], _FULL, "total_requests"))
    out.append(("outer_fail_login|both", _DATES["both"], _FULL, "login_fail"))
    return out


def _run(dates: dict[str, str], data: dict[str, Any], fail_on: str | None,
         caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    conn = _Conn(**data, fail_on=fail_on)
    repo = PostgresAuditRepository(_Pool(conn))  # type: ignore[arg-type]
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="src.infrastructure.audit_repository"):
        try:
            result: Any = asyncio.run(repo.get_stats(**dates))
        except Exception as exc:  # noqa: BLE001 — 예외 타입도 현재 값으로 채록한다
            result = ["!", type(exc).__name__]
    return {
        "calls": conn.calls,
        "result": result,
        "logs": [[r.levelname, r.getMessage()] for r in caplog.records
                 if r.name == "src.infrastructure.audit_repository"],
    }


def _collect(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    out = {sid: _run(dates, data, fail_on, caplog) for sid, dates, data, fail_on in _scenarios()}
    out["bad_date|start"] = _run({"start_date": "not-a-date"}, _FULL, None, caplog)
    return out


def test_get_stats_matches_snapshot(caplog):
    actual = json.loads(json.dumps(_collect(caplog), ensure_ascii=False))
    if os.environ.get(_UPDATE_ENV) == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert sorted(actual) == sorted(expected)
    for sid in expected:
        assert actual[sid] == expected[sid], sid


def test_get_stats_issues_seven_queries_in_fixed_order(caplog):
    """정상 경로는 7회(기본·성공률·Top 사용자·Top 테이블·일별·보안 경고·로그인 실패) — 순서 고정."""
    snap = _run(_DATES["both"], _FULL, None, caplog)
    kinds = [c[0] for c in snap["calls"]]
    assert kinds == ["fetchrow", "fetchrow", "fetch", "fetch", "fetch", "fetchval", "fetchval"]
    assert all(c[2] == snap["calls"][0][2] for c in snap["calls"])  # 같은 인자를 같은 순서로
