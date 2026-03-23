"""SQL 안전성 검사 테스트.

SELECT 외 SQL 차단, SQL 인젝션 패턴 탐지, 다중 문장 검출을 검증한다.
이 테스트는 시스템의 보안 핵심이므로 철저히 검증한다.
"""

import pytest

from src.security.sql_guard import FORBIDDEN_SQL_KEYWORDS, INJECTION_PATTERNS, SQLGuard


@pytest.fixture
def guard():
    return SQLGuard()


class TestDetectForbiddenKeywords:
    """금지 키워드 탐지 검증."""

    def test_insert_detected(self, guard):
        found = guard.detect_forbidden_keywords("INSERT INTO servers VALUES (1, 'x')")
        assert "INSERT" in found

    def test_update_detected(self, guard):
        found = guard.detect_forbidden_keywords("UPDATE servers SET hostname = 'x'")
        assert "UPDATE" in found

    def test_delete_detected(self, guard):
        found = guard.detect_forbidden_keywords("DELETE FROM servers WHERE id = 1")
        assert "DELETE" in found

    def test_drop_detected(self, guard):
        found = guard.detect_forbidden_keywords("DROP TABLE servers")
        assert "DROP" in found

    def test_alter_detected(self, guard):
        found = guard.detect_forbidden_keywords("ALTER TABLE servers ADD COLUMN x INT")
        assert "ALTER" in found

    def test_truncate_detected(self, guard):
        found = guard.detect_forbidden_keywords("TRUNCATE TABLE servers")
        assert "TRUNCATE" in found

    def test_create_detected(self, guard):
        found = guard.detect_forbidden_keywords("CREATE TABLE hack (id INT)")
        assert "CREATE" in found

    def test_grant_detected(self, guard):
        found = guard.detect_forbidden_keywords("GRANT ALL ON servers TO hacker")
        assert "GRANT" in found

    def test_revoke_detected(self, guard):
        found = guard.detect_forbidden_keywords("REVOKE SELECT ON servers FROM user")
        assert "REVOKE" in found

    def test_exec_detected(self, guard):
        found = guard.detect_forbidden_keywords("EXEC xp_cmdshell 'dir'")
        assert "EXEC" in found

    def test_safe_select_no_detection(self, guard):
        found = guard.detect_forbidden_keywords("SELECT hostname, ip_address FROM servers LIMIT 10")
        assert found == []

    def test_case_insensitive(self, guard):
        found = guard.detect_forbidden_keywords("insert into servers values (1)")
        assert "INSERT" in found

    def test_merge_detected(self, guard):
        found = guard.detect_forbidden_keywords("MERGE INTO servers USING temp ON ...")
        assert "MERGE" in found

    def test_shutdown_detected(self, guard):
        found = guard.detect_forbidden_keywords("SHUTDOWN IMMEDIATE")
        assert "SHUTDOWN" in found


class TestDetectInjectionPatterns:
    """SQL 인젝션 패턴 탐지 검증."""

    def test_union_select_detected(self, guard):
        detected = guard.detect_injection_patterns(
            "SELECT * FROM servers UNION SELECT * FROM users"
        )
        assert len(detected) > 0

    def test_union_all_select_detected(self, guard):
        detected = guard.detect_injection_patterns(
            "SELECT * FROM servers UNION ALL SELECT password FROM users"
        )
        assert len(detected) > 0

    def test_semicolon_drop_detected(self, guard):
        detected = guard.detect_injection_patterns(
            "SELECT * FROM servers; DROP TABLE users"
        )
        assert len(detected) > 0

    def test_comment_injection_detected(self, guard):
        detected = guard.detect_injection_patterns(
            "SELECT * FROM servers /* injected comment */"
        )
        assert len(detected) > 0

    def test_xp_cmdshell_detected(self, guard):
        detected = guard.detect_injection_patterns(
            "EXEC xp_cmdshell 'whoami'"
        )
        assert len(detected) > 0

    def test_into_outfile_detected(self, guard):
        detected = guard.detect_injection_patterns(
            "SELECT * INTO OUTFILE '/tmp/data.csv' FROM servers"
        )
        assert len(detected) > 0

    def test_safe_select_no_detection(self, guard):
        detected = guard.detect_injection_patterns(
            "SELECT hostname FROM servers WHERE id = 1 LIMIT 10"
        )
        assert detected == []

    def test_sys_table_detected(self, guard):
        detected = guard.detect_injection_patterns(
            "SELECT * FROM sys.tables"
        )
        assert len(detected) > 0


class TestIsSafeSelect:
    """종합 안전성 검사 검증."""

    def test_safe_select(self, guard):
        is_safe, reason = guard.is_safe_select(
            "SELECT hostname FROM servers WHERE id = 1 LIMIT 10"
        )
        assert is_safe is True

    def test_insert_blocked(self, guard):
        is_safe, reason = guard.is_safe_select(
            "INSERT INTO servers (hostname) VALUES ('hack')"
        )
        assert is_safe is False
        assert "금지된 키워드" in reason

    def test_injection_blocked(self, guard):
        is_safe, reason = guard.is_safe_select(
            "SELECT * FROM servers UNION SELECT * FROM users"
        )
        assert is_safe is False
        assert "인젝션" in reason

    def test_multi_statement_blocked(self, guard):
        is_safe, reason = guard.is_safe_select(
            "SELECT * FROM servers; SELECT * FROM users"
        )
        assert is_safe is False
        assert "다중 SQL" in reason

    def test_trailing_semicolon_allowed(self, guard):
        """단일 SQL 끝의 세미콜론은 허용한다."""
        is_safe, reason = guard.is_safe_select(
            "SELECT hostname FROM servers LIMIT 10;"
        )
        assert is_safe is True

    def test_complex_safe_query(self, guard):
        """복잡하지만 안전한 SELECT 쿼리가 통과한다."""
        sql = (
            "SELECT s.hostname, AVG(c.usage_pct) as avg_cpu "
            "FROM servers s "
            "JOIN cpu_metrics c ON s.id = c.server_id "
            "WHERE c.timestamp >= '2026-03-01' "
            "GROUP BY s.hostname "
            "HAVING AVG(c.usage_pct) > 80 "
            "ORDER BY avg_cpu DESC "
            "LIMIT 10"
        )
        is_safe, reason = guard.is_safe_select(sql)
        assert is_safe is True


class TestForbiddenKeywordsCompleteness:
    """금지 키워드 목록의 완전성 검증."""

    def test_all_dml_covered(self):
        """DML 키워드가 모두 포함되어 있다."""
        dml_keywords = {"INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE"}
        assert dml_keywords.issubset(FORBIDDEN_SQL_KEYWORDS)

    def test_all_ddl_covered(self):
        """DDL 키워드가 모두 포함되어 있다."""
        ddl_keywords = {"CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME"}
        assert ddl_keywords.issubset(FORBIDDEN_SQL_KEYWORDS)

    def test_dcl_covered(self):
        """DCL 키워드가 포함되어 있다."""
        dcl_keywords = {"GRANT", "REVOKE"}
        assert dcl_keywords.issubset(FORBIDDEN_SQL_KEYWORDS)

    def test_exec_covered(self):
        """프로시저 실행 키워드가 포함되어 있다."""
        assert "EXEC" in FORBIDDEN_SQL_KEYWORDS
        assert "EXECUTE" in FORBIDDEN_SQL_KEYWORDS
        assert "CALL" in FORBIDDEN_SQL_KEYWORDS
