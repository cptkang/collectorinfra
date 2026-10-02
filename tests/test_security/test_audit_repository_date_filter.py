"""감사 로그 날짜 필터 변환 테스트.

asyncpg는 timestamptz 인자로 str을 거부한다(DataError). 날짜를 문자열 그대로 넘기면
조회가 예외로 끝나 날짜를 넣은 검색이 항상 0건이 되던 회귀를 막는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.infrastructure.audit_repository import PostgresAuditRepository, _to_bound

KST = timezone(timedelta(hours=9))


def test_date_only_start_is_kst_midnight():
    assert _to_bound("2026-09-23", end=False) == datetime(2026, 9, 23, tzinfo=KST)


def test_date_only_end_includes_whole_day():
    bound = _to_bound("2026-09-23", end=True)
    assert bound.date().isoformat() == "2026-09-23"
    assert bound.tzinfo == KST
    assert bound > datetime(2026, 9, 23, 23, 59, 59, tzinfo=KST)


def test_explicit_offset_is_kept():
    bound = _to_bound("2026-09-23T01:00:00+00:00", end=False)
    assert bound.utcoffset() == timedelta(0)


def test_invalid_date_raises_value_error():
    with pytest.raises(ValueError):
        _to_bound("not-a-date", end=False)


class _FakeConn:
    def __init__(self) -> None:
        self.args: list[tuple] = []

    async def fetchval(self, query, *args):
        self.args.append(args)
        return 0

    async def fetch(self, query, *args):
        self.args.append(args)
        return []


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_paginated_query_binds_datetimes_not_strings():
    conn = _FakeConn()
    repo = PostgresAuditRepository(_FakePool(conn))  # type: ignore[arg-type]
    await repo.query_logs_paginated(start_date="2026-09-01", end_date="2026-09-23")
    count_args = conn.args[0]
    assert all(isinstance(a, datetime) for a in count_args)
