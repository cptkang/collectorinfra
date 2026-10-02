"""tools.py 테스트.

MCP 도구의 SQL 생성 및 결과 처리를 검증한다.
DB 연결 없이 SQL 생성 로직만 단위 테스트한다.
"""

import pytest

try:
    from mcp_server.tools import (
        _db2_search_objects_sql,
        _mariadb_literal,
        _mariadb_search_objects_sql,
        _mariadb_table_filter,
        _pg_search_objects_sql,
        register_tools,
    )
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp 패키지가 설치되지 않음")


class TestPgSearchObjectsSql:
    """PostgreSQL search_objects SQL 생성 테스트."""

    def test_all_tables(self):
        """전체 테이블 검색 SQL을 올바르게 생성한다."""
        sql = _pg_search_objects_sql("*", "table")
        assert "information_schema.tables" in sql
        assert "table_schema = 'public'" in sql
        assert "BASE TABLE" in sql
        assert "LIKE" not in sql

    def test_pattern_search(self):
        """패턴 검색 SQL을 올바르게 생성한다."""
        sql = _pg_search_objects_sql("cpu*", "table")
        assert "LIKE 'cpu%'" in sql

    def test_view_type(self):
        """뷰 검색 SQL을 올바르게 생성한다."""
        sql = _pg_search_objects_sql("*", "view")
        assert "VIEW" in sql
        assert "BASE TABLE" not in sql


class TestDb2SearchObjectsSql:
    """DB2 search_objects SQL 생성 테스트."""

    def test_all_tables(self):
        """전체 테이블 검색 SQL을 올바르게 생성한다."""
        sql = _db2_search_objects_sql("*", "table")
        assert "SYSCAT.TABLES" in sql
        assert "TYPE = 'T'" in sql
        assert "TABSCHEMA NOT LIKE 'SYS%'" in sql

    def test_pattern_search(self):
        """패턴 검색 SQL을 올바르게 생성한다."""
        sql = _db2_search_objects_sql("server*", "table")
        assert "LIKE 'server%'" in sql

    def test_view_type(self):
        """뷰 검색 SQL을 올바르게 생성한다."""
        sql = _db2_search_objects_sql("*", "view")
        assert "TYPE = 'V'" in sql


class TestSqlInjectionPrevention:
    """SQL 인젝션 방어 테스트 (패턴 검색)."""

    def test_pattern_quotes_escaped(self):
        """패턴의 작은따옴표가 이스케이프된다."""
        sql = _pg_search_objects_sql("test'--", "table")
        assert "test''--" in sql

    def test_db2_pattern_quotes_escaped(self):
        """DB2 패턴의 작은따옴표가 이스케이프된다."""
        sql = _db2_search_objects_sql("test'--", "table")
        assert "test''--" in sql


class TestMariadbSearchObjectsSql:
    """MariaDB search_objects SQL 생성 테스트 (plans/95 W-3)."""

    def test_all_tables(self):
        sql = _mariadb_search_objects_sql("*", "table")
        assert "information_schema.tables" in sql
        assert "table_type = 'BASE TABLE'" in sql
        assert "'mysql', 'performance_schema', 'sys'" in sql
        assert "LIKE" not in sql

    def test_concat_not_pipes(self):
        """MariaDB 기본 sql_mode에서 `||`는 OR다 — 결합은 CONCAT이어야 한다."""
        sql = _mariadb_search_objects_sql("*", "table")
        assert "CONCAT(table_schema, '.', table_name)" in sql
        assert "||" not in sql

    def test_schema_alias_quoted(self):
        """`schema`는 예약어라 인용하지 않으면 구문 오류다."""
        assert "AS `schema`" in _mariadb_search_objects_sql("*", "table")

    def test_pattern_search(self):
        assert "LIKE 'cpu%'" in _mariadb_search_objects_sql("cpu*", "table")

    def test_view_type(self):
        sql = _mariadb_search_objects_sql("*", "view")
        assert "table_type = 'VIEW'" in sql


class TestMariadbLiteral:
    def test_quote_doubled(self):
        assert _mariadb_literal("a'b") == "'a''b'"

    def test_backslash_cannot_escape_literal(self):
        """백슬래시로 따옴표를 무력화하는 탈출이 리터럴 안에 갇힌다."""
        assert _mariadb_literal("x\\' OR 1=1 -- ") == "'x\\\\'' OR 1=1 -- '"

    def test_table_filter_bare_uses_connection_database(self):
        assert _mariadb_table_filter("T1") == "table_schema = DATABASE() AND table_name = 'T1'"

    def test_table_filter_qualified(self):
        assert _mariadb_table_filter("db1.T1") == "table_schema = 'db1' AND table_name = 'T1'"


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


class _RecordingPool:
    """get_table_schema가 MariaDB 헬퍼 3종을 부르는지와 반환 shape를 연결 없이 확인한다."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def get_source_type(self, source):
        return "mariadb"

    async def execute(self, source, sql):
        self.executed.append(sql)
        if "constraint_name = 'PRIMARY'" in sql:
            return [{"column_name": "k1"}, {"column_name": "k2"}]
        if "referenced_table_name IS NOT NULL" in sql:
            return []
        return [
            {"column_name": name, "data_type": dtype, "is_nullable": nullable,
             "column_default": None}
            for name, dtype, nullable in (
                ("k1", "char", "NO"), ("k2", "varchar", "NO"), ("v", "decimal", "YES")
            )
        ]


class TestMariadbGetTableSchemaDispatch:
    def test_schema_shape_matches_other_engines(self):
        import asyncio
        import json
        from types import SimpleNamespace

        capture = _CaptureMCP()
        register_tools(capture)
        pool = _RecordingPool()
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(lifespan_context={"pool_manager": pool})
        )
        out = json.loads(
            asyncio.run(capture.tools["get_table_schema"](source="asset", table_name="T1", ctx=ctx))
        )
        assert out["source_type"] == "mariadb"
        assert out["primary_keys"] == ["k1", "k2"]
        assert [c["is_primary_key"] for c in out["columns"]] == [True, True, False]
        assert out["foreign_keys"] == []
        assert len(pool.executed) == 3
        assert all("table_schema = DATABASE()" in sql for sql in pool.executed)
