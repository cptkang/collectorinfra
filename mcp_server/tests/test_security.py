"""security.py 테스트.

읽기 전용 SQL 가드의 정상 동작을 검증한다.
"""

import pytest

from mcp_server.security import (
    PolestarDomainViolationError,
    ReadOnlyViolationError,
    validate_polestar_domain,
    validate_readonly,
)


class TestValidateReadonly:
    """validate_readonly 함수 테스트."""

    def test_valid_select(self):
        """일반 SELECT 문은 통과한다."""
        validate_readonly("SELECT * FROM servers")
        validate_readonly("SELECT hostname, ip FROM servers WHERE id = 1")
        validate_readonly(
            "SELECT s.hostname, c.usage_pct "
            "FROM servers s JOIN cpu_metrics c ON s.id = c.server_id"
        )

    def test_select_with_aggregation(self):
        """집계 함수가 포함된 SELECT도 통과한다."""
        validate_readonly("SELECT COUNT(*) FROM servers")
        validate_readonly(
            "SELECT server_id, AVG(usage_pct) FROM cpu_metrics GROUP BY server_id"
        )

    def test_select_with_subquery(self):
        """서브쿼리가 포함된 SELECT도 통과한다."""
        validate_readonly(
            "SELECT * FROM servers WHERE id IN (SELECT server_id FROM cpu_metrics)"
        )

    def test_empty_sql_raises(self):
        """빈 SQL은 예외를 발생시킨다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("")
        assert "빈 SQL" in str(exc_info.value)

    def test_insert_blocked(self):
        """INSERT 문은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("INSERT INTO servers (hostname) VALUES ('test')")
        assert "금지된 키워드" in str(exc_info.value)

    def test_update_blocked(self):
        """UPDATE 문은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("UPDATE servers SET hostname = 'test' WHERE id = 1")
        assert "금지된 키워드" in str(exc_info.value)

    def test_delete_blocked(self):
        """DELETE 문은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("DELETE FROM servers WHERE id = 1")
        assert "금지된 키워드" in str(exc_info.value)

    def test_drop_blocked(self):
        """DROP 문은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("DROP TABLE servers")
        assert "금지된 키워드" in str(exc_info.value)

    def test_alter_blocked(self):
        """ALTER 문은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("ALTER TABLE servers ADD COLUMN test VARCHAR(10)")
        assert "금지된 키워드" in str(exc_info.value)

    def test_truncate_blocked(self):
        """TRUNCATE 문은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("TRUNCATE TABLE servers")
        assert "금지된 키워드" in str(exc_info.value)

    def test_create_blocked(self):
        """CREATE 문은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("CREATE TABLE test (id INT)")
        assert "금지된 키워드" in str(exc_info.value)

    def test_grant_blocked(self):
        """GRANT 문은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("GRANT SELECT ON servers TO user1")
        assert "금지된 키워드" in str(exc_info.value)

    def test_multiple_statements_blocked(self):
        """다중 문장은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("SELECT 1; SELECT 2")
        assert "다중 SQL 문" in str(exc_info.value)

    def test_keyword_in_comment_not_blocked(self):
        """주석 내의 금지 키워드는 오탐하지 않는다."""
        validate_readonly("SELECT * FROM servers /* INSERT test */")

    def test_keyword_in_string_not_blocked(self):
        """문자열 리터럴 내의 금지 키워드는 오탐하지 않는다."""
        validate_readonly("SELECT * FROM servers WHERE name = 'DELETE_ME'")

    def test_semicolon_injection_blocked(self):
        """세미콜론 뒤 위험한 SQL이 오면 차단된다."""
        with pytest.raises(ReadOnlyViolationError):
            validate_readonly("SELECT 1; DROP TABLE servers")

    def test_merge_blocked(self):
        """MERGE 문은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly(
                "MERGE INTO target USING source ON target.id = source.id "
                "WHEN MATCHED THEN UPDATE SET name = source.name"
            )
        assert "금지된 키워드" in str(exc_info.value)

    def test_call_blocked(self):
        """CALL 문은 차단된다."""
        with pytest.raises(ReadOnlyViolationError) as exc_info:
            validate_readonly("CALL some_procedure()")
        assert "금지된 키워드" in str(exc_info.value)


class TestValidatePolestarDomain:
    """validate_polestar_domain 도메인 deny 테스트 (§6, D-022/D-028)."""

    def test_resource_conf_join_blocked(self):
        """RESOURCE_CONF_ID = CONFIGURATION_ID 조인은 차단된다 (D-022)."""
        with pytest.raises(PolestarDomainViolationError) as exc_info:
            validate_polestar_domain(
                "SELECT 1 FROM cmm_resource r "
                "JOIN core_config_prop p ON r.resource_conf_id = p.configuration_id"
            )
        assert "RESOURCE_CONF_ID" in str(exc_info.value)

    def test_resource_conf_join_reverse_blocked(self):
        """CONFIGURATION_ID = RESOURCE_CONF_ID 역방향 조인도 차단된다."""
        with pytest.raises(PolestarDomainViolationError):
            validate_polestar_domain(
                "SELECT 1 FROM a WHERE p.configuration_id = r.RESOURCE_CONF_ID"
            )

    def test_cmm_vendor_blocked(self):
        """cmm_vendor lookup 참조는 차단된다 (D-028)."""
        with pytest.raises(PolestarDomainViolationError) as exc_info:
            validate_polestar_domain("SELECT * FROM cmm_vendor")
        assert "CMM_VENDOR" in str(exc_info.value)

    def test_cmm_os_blocked(self):
        """cmm_os lookup 참조는 차단된다 (D-028)."""
        with pytest.raises(PolestarDomainViolationError):
            validate_polestar_domain("SELECT * FROM cmm_resource r JOIN cmm_os o ON r.x = o.id")

    def test_cmm_os_param_blocked(self):
        """cmm_os_param lookup 참조는 차단된다 (D-028)."""
        with pytest.raises(PolestarDomainViolationError):
            validate_polestar_domain("SELECT * FROM cmm_os_param")

    def test_legit_hostname_bridge_passes(self):
        """올바른 hostname 브릿지 조인은 통과한다 (D-022 준수)."""
        validate_polestar_domain(
            "SELECT p.name, p.stringvalue_short "
            "FROM core_config_prop p_host "
            "JOIN core_config_prop p ON p.configuration_id = p_host.configuration_id "
            "WHERE p_host.name = 'Hostname'"
        )

    def test_plain_resource_query_passes(self):
        """cmm_resource 단순 조회는 통과한다."""
        validate_polestar_domain("SELECT NAME, AVAIL_STATUS FROM cmm_resource")

    def test_forbidden_table_in_literal_not_blocked(self):
        """문자열 리터럴 내의 금지 테이블명은 오탐하지 않는다."""
        validate_polestar_domain("SELECT * FROM cmm_resource WHERE name = 'cmm_vendor'")

    def test_forbidden_table_in_comment_not_blocked(self):
        """주석 내의 금지 테이블명은 오탐하지 않는다."""
        validate_polestar_domain("SELECT * FROM cmm_resource /* not cmm_os_param */")

    def test_empty_sql_no_raise(self):
        """빈 SQL은 도메인 검증에서 예외 없이 통과한다(읽기전용에서 별도 차단)."""
        validate_polestar_domain("")
