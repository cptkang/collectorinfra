"""PolestarHostResolver(Plan 48 §5.2) 단위 테스트.

read-only 단일 SELECT, `_sql_literal` 이스케이프(작은따옴표 인젝션 차단),
db_engine별 LIMIT(db2=FETCH FIRST / postgresql=LIMIT), 2건 조회, ORDER BY CASE,
1건 확정 / 서로 다른 hostname 2건 모호 / 같은 hostname 2건은 1건 취급 / 0건 /
예외 graceful, 비폴스타 db 빈 결과를 검증한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from src.infrastructure.polestar_host_resolver import (
    HostResolution,
    PolestarHostResolver,
    build_resolve_sql,
)


class _FakeResult:
    """execute_sql 결과 — rows 속성만 노출."""

    def __init__(self, rows):
        self.rows = rows


class _FakeClient:
    """execute_sql(sql)을 받아 미리 준비된 rows를 반환하는 fake DB 클라이언트."""

    def __init__(self, rows, *, raise_exc=None, capture):
        self._rows = rows
        self._raise = raise_exc
        self._capture = capture

    async def execute_sql(self, sql):
        self._capture["sql"] = sql
        if self._raise is not None:
            raise self._raise
        return _FakeResult(self._rows)


def _make_factory(rows, *, raise_exc=None, capture=None):
    """get_db_client 동등 async context manager 팩토리를 만든다."""
    capture = capture if capture is not None else {}

    @asynccontextmanager
    async def factory(app_config, *, db_id=None):
        capture["db_id"] = db_id
        yield _FakeClient(rows, raise_exc=raise_exc, capture=capture)

    return factory, capture


# polestar = db2, polestar_cm_gp = postgresql (domain_config 실제 값)
_DB2_ID = "polestar"
_PG_ID = "polestar_cm_gp"


@pytest.mark.asyncio
class TestResolveBasics:
    async def test_single_hostname_confirmed(self):
        factory, cap = _make_factory([{"hostname": "saisvd01"}])
        resolver = PolestarHostResolver(factory, app_config=object())
        res = await resolver.resolve(_PG_ID, "myserver")
        assert isinstance(res, HostResolution)
        assert res.hostname == "saisvd01"
        assert res.candidates == []
        # 활성 db_id가 팩토리에 전달됨
        assert cap["db_id"] == _PG_ID

    async def test_two_different_hostnames_ambiguous(self):
        factory, _ = _make_factory([{"hostname": "hostA"}, {"hostname": "hostB"}])
        resolver = PolestarHostResolver(factory, app_config=object())
        res = await resolver.resolve(_PG_ID, "dup")
        assert res.hostname is None
        assert res.candidates == ["hostA", "hostB"]

    async def test_same_hostname_twice_treated_as_one(self):
        factory, _ = _make_factory([{"hostname": "hostX"}, {"hostname": "hostX"}])
        resolver = PolestarHostResolver(factory, app_config=object())
        res = await resolver.resolve(_PG_ID, "x")
        assert res.hostname == "hostX"
        assert res.candidates == []

    async def test_zero_rows_empty(self):
        factory, _ = _make_factory([])
        resolver = PolestarHostResolver(factory, app_config=object())
        res = await resolver.resolve(_PG_ID, "nope")
        assert res.hostname is None
        assert res.candidates == []

    async def test_exception_graceful_empty(self):
        factory, _ = _make_factory(None, raise_exc=RuntimeError("cmm_resource 부재"))
        resolver = PolestarHostResolver(factory, app_config=object())
        res = await resolver.resolve(_PG_ID, "x")
        # 예외를 위로 던지지 않고 빈 결과
        assert res.hostname is None
        assert res.candidates == []

    async def test_non_polestar_db_skips_query(self):
        called = {"hit": False}

        @asynccontextmanager
        async def factory(app_config, *, db_id=None):
            called["hit"] = True
            yield _FakeClient([], capture={})

        resolver = PolestarHostResolver(factory, app_config=object())
        res = await resolver.resolve("itsm", "x")  # itsm은 폴스타 아님이지만 등록됨
        # itsm은 등록 db라 조회는 수행되나 cmm_resource 부재 시 예외→빈 결과.
        # 미등록 db는 즉시 빈 결과(조회 안 함).
        res2 = await resolver.resolve("does_not_exist", "x")
        assert res2.hostname is None
        assert res2.candidates == []

    async def test_empty_identifier_no_query(self):
        called = {"hit": False}

        @asynccontextmanager
        async def factory(app_config, *, db_id=None):
            called["hit"] = True
            yield _FakeClient([], capture={})

        resolver = PolestarHostResolver(factory, app_config=object())
        res = await resolver.resolve(_PG_ID, "")
        assert res.hostname is None
        assert called["hit"] is False  # 빈 identifier는 조회 안 함

    async def test_case_insensitive_row_key(self):
        # 드라이버가 대문자 HOSTNAME 키로 반환해도 해석됨
        factory, _ = _make_factory([{"HOSTNAME": "upperHost"}])
        resolver = PolestarHostResolver(factory, app_config=object())
        res = await resolver.resolve(_PG_ID, "x")
        assert res.hostname == "upperHost"


@pytest.mark.asyncio
class TestSqlSafety:
    async def test_sql_is_readonly_single_select(self):
        factory, cap = _make_factory([{"hostname": "h"}])
        resolver = PolestarHostResolver(factory, app_config=object())
        await resolver.resolve(_PG_ID, "srv")
        sql = cap["sql"]
        upper = sql.upper()
        assert upper.lstrip().startswith("SELECT")
        # 단일 SELECT — 변경 키워드 없음
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", ";"):
            assert forbidden not in upper.replace("FETCH FIRST", "")
        # 스키마 한정
        assert "polestar.cmm_resource" in sql.lower()
        # name·hostname 동시 매칭 + ORDER BY CASE
        assert "WHERE NAME =" in upper or "WHERE name =".upper() in upper
        assert "ORDER BY CASE WHEN" in upper

    async def test_single_quote_injection_escaped(self):
        factory, cap = _make_factory([])
        resolver = PolestarHostResolver(factory, app_config=object())
        # 작은따옴표 인젝션 시도
        await resolver.resolve(_PG_ID, "x' OR '1'='1")
        sql = cap["sql"]
        # 작은따옴표가 '' 로 이스케이프되어 종결되지 않음 → 인젝션 차단
        assert "x'' OR ''1''=''1" in sql
        # 원시 미이스케이프 인젝션 문자열은 존재하지 않음
        assert "x' OR '1'='1'" not in sql

    async def test_null_byte_removed(self):
        factory, cap = _make_factory([])
        resolver = PolestarHostResolver(factory, app_config=object())
        await resolver.resolve(_PG_ID, "ab\x00cd")
        assert "\x00" not in cap["sql"]
        assert "abcd" in cap["sql"]


class TestLimitByEngine:
    """db_engine별 LIMIT 절 분기 (동기 테스트)."""

    def test_limit_by_db_engine(self):
        # db2 → FETCH FIRST 2 ROWS ONLY
        sql_db2 = build_resolve_sql("srv", "db2")
        assert "FETCH FIRST 2 ROWS ONLY" in sql_db2
        assert "LIMIT" not in sql_db2.upper()
        # postgresql → LIMIT 2
        sql_pg = build_resolve_sql("srv", "postgresql")
        assert "LIMIT 2" in sql_pg
        assert "FETCH FIRST" not in sql_pg.upper()

    def test_resolves_two_rows_for_ambiguity(self):
        # LIMIT/FETCH 가 2건으로 고정 — 모호성 감지
        assert "2 ROWS ONLY" in build_resolve_sql("x", "db2")
        assert "LIMIT 2" in build_resolve_sql("x", "postgresql")
