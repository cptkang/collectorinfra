"""SQL Guard 확장 보안 테스트.

SQL 인젝션 방지, DML/DDL 차단, 주석 우회 방어 등
보안에 중요한 경계 조건을 집중 검증한다.
"""

import pytest

from src.security.sql_guard import FORBIDDEN_SQL_KEYWORDS, SQLGuard


@pytest.fixture
def guard():
    return SQLGuard()


class TestCommentBypassPrevention:
    """주석을 이용한 우회 시도 차단 검증."""

    def test_keyword_inside_line_comment_not_detected(self, guard):
        """라인 주석 안의 금지 키워드는 오탐이 아니어야 한다."""
        sql = "SELECT * FROM servers -- DROP TABLE users"
        found = guard.detect_forbidden_keywords(sql)
        # sqlparse strip_comments가 주석을 제거하므로 DROP이 탐지되지 않아야 한다
        assert "DROP" not in found

    def test_keyword_inside_block_comment_not_detected(self, guard):
        """블록 주석 안의 금지 키워드는 오탐이 아니어야 한다."""
        sql = "SELECT * FROM servers /* DELETE FROM users */"
        found = guard.detect_forbidden_keywords(sql)
        assert "DELETE" not in found

    def test_keyword_in_string_literal_not_detected(self, guard):
        """문자열 리터럴 안의 금지 키워드는 오탐이 아니어야 한다."""
        sql = "SELECT * FROM servers WHERE status = 'DELETE_PENDING'"
        found = guard.detect_forbidden_keywords(sql)
        # 문자열 리터럴을 제거한 후 검사하므로 DELETE가 탐지되지 않아야 한다
        assert "DELETE" not in found


class TestMultiStatementPrevention:
    """다중 SQL 문 차단 검증."""

    def test_two_selects_blocked(self, guard):
        """두 개의 SELECT 문도 차단한다."""
        is_safe, reason = guard.is_safe_select(
            "SELECT 1; SELECT 2"
        )
        assert is_safe is False
        assert "다중" in reason

    def test_select_then_drop_blocked(self, guard):
        """SELECT 뒤에 DROP이 오면 차단한다."""
        is_safe, reason = guard.is_safe_select(
            "SELECT * FROM servers; DROP TABLE users;"
        )
        assert is_safe is False

    def test_newline_separated_statements(self, guard):
        """개행으로 분리된 다중 문장을 차단한다."""
        sql = "SELECT * FROM servers\n;\nDELETE FROM users"
        is_safe, reason = guard.is_safe_select(sql)
        assert is_safe is False


class TestAdvancedInjectionPatterns:
    """고급 SQL 인젝션 패턴 탐지."""

    def test_sleep_injection(self, guard):
        """SLEEP 기반 시간 지연 공격을 탐지한다."""
        detected = guard.detect_injection_patterns(
            "SELECT * FROM servers WHERE id=1 AND SLEEP(5)"
        )
        assert len(detected) > 0

    def test_benchmark_injection(self, guard):
        """BENCHMARK 기반 시간 공격을 탐지한다."""
        detected = guard.detect_injection_patterns(
            "SELECT BENCHMARK(10000000, SHA1('test'))"
        )
        assert len(detected) > 0

    def test_load_file_injection(self, guard):
        """LOAD_FILE 파일 읽기를 탐지한다."""
        detected = guard.detect_injection_patterns(
            "SELECT LOAD_FILE('/etc/passwd')"
        )
        assert len(detected) > 0

    def test_system_variable_access(self, guard):
        """시스템 변수 접근을 탐지한다."""
        detected = guard.detect_injection_patterns(
            "SELECT @@version"
        )
        assert len(detected) > 0

    def test_information_schema_access(self, guard):
        """INFORMATION_SCHEMA 직접 접근을 탐지한다."""
        detected = guard.detect_injection_patterns(
            "SELECT * FROM INFORMATION_SCHEMA.TABLES"
        )
        assert len(detected) > 0

    def test_into_dumpfile(self, guard):
        """INTO DUMPFILE를 탐지한다."""
        detected = guard.detect_injection_patterns(
            "SELECT * INTO DUMPFILE '/tmp/shell.php' FROM users"
        )
        assert len(detected) > 0


class TestSpecForbiddenKeywords:
    """spec.md에 명시된 모든 금지 키워드 차단 검증.

    spec 섹션 6.1: INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, REVOKE, EXEC, EXECUTE
    """

    @pytest.mark.parametrize("keyword", [
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
        "TRUNCATE", "CREATE", "GRANT", "REVOKE", "EXEC", "EXECUTE",
    ])
    def test_spec_forbidden_keyword_in_set(self, keyword):
        """spec에 명시된 금지 키워드가 FORBIDDEN_SQL_KEYWORDS에 포함되어 있다."""
        assert keyword in FORBIDDEN_SQL_KEYWORDS

    @pytest.mark.parametrize("sql,keyword", [
        ("INSERT INTO t VALUES (1)", "INSERT"),
        ("UPDATE t SET x=1", "UPDATE"),
        ("DELETE FROM t WHERE 1=1", "DELETE"),
        ("DROP TABLE t", "DROP"),
        ("ALTER TABLE t ADD col INT", "ALTER"),
        ("TRUNCATE TABLE t", "TRUNCATE"),
        ("CREATE TABLE t (id INT)", "CREATE"),
        ("GRANT ALL ON t TO user", "GRANT"),
        ("REVOKE SELECT ON t FROM user", "REVOKE"),
        ("EXEC sp_help", "EXEC"),
    ])
    def test_spec_forbidden_keyword_detected(self, guard, sql, keyword):
        """spec에 명시된 금지 키워드가 실제로 탐지된다."""
        found = guard.detect_forbidden_keywords(sql)
        assert keyword in found


class TestEdgeCases:
    """경계 조건 테스트."""

    def test_empty_sql(self, guard):
        """빈 SQL에 대해 안전하다고 판단한다."""
        is_safe, reason = guard.is_safe_select("")
        # 빈 SQL은 파싱 결과가 없으므로 안전으로 판단될 수 있음
        # 구현에 따라 다를 수 있으나, 최소한 에러가 발생하지 않아야 한다
        assert isinstance(is_safe, bool)

    def test_whitespace_only_sql(self, guard):
        """공백만 있는 SQL을 처리한다."""
        is_safe, reason = guard.is_safe_select("   ")
        assert isinstance(is_safe, bool)

    def test_very_long_sql(self, guard):
        """매우 긴 SQL을 처리할 수 있다."""
        long_where = " AND ".join([f"col{i} = {i}" for i in range(100)])
        sql = f"SELECT * FROM servers WHERE {long_where} LIMIT 10"
        is_safe, reason = guard.is_safe_select(sql)
        assert is_safe is True

    def test_mixed_case_select(self, guard):
        """대소문자 혼합 SELECT를 허용한다."""
        is_safe, reason = guard.is_safe_select(
            "SeLeCt hostname FROM servers LIMIT 10"
        )
        assert is_safe is True

    def test_cte_query_allowed(self, guard):
        """WITH (CTE) 쿼리를 허용한다."""
        sql = (
            "WITH recent AS (SELECT * FROM cpu_metrics WHERE timestamp > '2026-01-01') "
            "SELECT * FROM recent LIMIT 10"
        )
        is_safe, reason = guard.is_safe_select(sql)
        assert is_safe is True
