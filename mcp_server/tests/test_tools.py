"""tools.py 테스트.

MCP 도구의 SQL 생성 및 결과 처리를 검증한다.
DB 연결 없이 SQL 생성 로직만 단위 테스트한다.
"""

import pytest

try:
    from mcp_server.tools import (
        _pg_search_objects_sql,
        _db2_search_objects_sql,
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
