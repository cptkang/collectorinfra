"""query_validator 노드 확장 테스트.

DML/DDL 완전 차단, SQL 인젝션 방지, 다중 문장 차단 등
보안 핵심 기능을 추가 검증한다.
"""

import pytest
from unittest.mock import patch

from src.nodes.query_validator import (
    _extract_table_names,
    _get_statement_type,
    _validate_columns,
    query_validator,
)
from src.state import create_initial_state


@pytest.fixture
def base_state(sample_schema_info):
    """검증 테스트용 기본 State."""
    state = create_initial_state(user_query="test")
    state["schema_info"] = sample_schema_info
    return state


class TestDMLDDLBlocking:
    """DML/DDL 완전 차단 검증.

    spec 요구사항: SELECT 이외의 SQL 문이 100% 차단되어야 한다.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sql,desc", [
        ("INSERT INTO servers (hostname) VALUES ('hack')", "INSERT"),
        ("UPDATE servers SET hostname='hacked' WHERE 1=1", "UPDATE"),
        ("DELETE FROM servers", "DELETE"),
        ("DROP TABLE servers", "DROP"),
        ("ALTER TABLE servers ADD COLUMN backdoor TEXT", "ALTER"),
        ("TRUNCATE TABLE servers", "TRUNCATE"),
        ("CREATE TABLE hacker (id INT)", "CREATE"),
        ("GRANT ALL ON servers TO hacker", "GRANT"),
        ("REVOKE SELECT ON servers FROM admin", "REVOKE"),
    ])
    async def test_dml_ddl_blocked(self, base_state, sql, desc):
        """모든 DML/DDL 문이 차단된다."""
        base_state["generated_sql"] = sql
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        assert result["validation_result"]["passed"] is False, f"{desc} 차단 실패"

    @pytest.mark.asyncio
    async def test_union_injection_blocked(self, base_state):
        """UNION SELECT 인젝션이 차단된다."""
        base_state["generated_sql"] = (
            "SELECT hostname FROM servers UNION SELECT password FROM users"
        )
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        assert result["validation_result"]["passed"] is False

    @pytest.mark.asyncio
    async def test_multi_statement_injection_blocked(self, base_state):
        """세미콜론 다중 문장 인젝션이 차단된다."""
        base_state["generated_sql"] = (
            "SELECT * FROM servers; DROP TABLE servers"
        )
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        assert result["validation_result"]["passed"] is False


class TestComplexSafeQueries:
    """복잡하지만 안전한 쿼리 통과 검증."""

    @pytest.mark.asyncio
    async def test_join_query_passes(self, base_state):
        """JOIN 쿼리가 통과한다."""
        base_state["generated_sql"] = (
            "SELECT s.hostname, c.usage_pct "
            "FROM servers s "
            "JOIN cpu_metrics c ON s.id = c.server_id "
            "WHERE c.usage_pct >= 80 "
            "ORDER BY c.usage_pct DESC "
            "LIMIT 10"
        )
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        assert result["validation_result"]["passed"] is True

    @pytest.mark.asyncio
    async def test_aggregate_query_passes(self, base_state):
        """집계 쿼리가 통과한다."""
        base_state["generated_sql"] = (
            "SELECT s.hostname, AVG(c.usage_pct) AS avg_cpu "
            "FROM servers s "
            "JOIN cpu_metrics c ON s.id = c.server_id "
            "GROUP BY s.hostname "
            "HAVING AVG(c.usage_pct) > 80 "
            "ORDER BY avg_cpu DESC "
            "LIMIT 10"
        )
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        assert result["validation_result"]["passed"] is True

    @pytest.mark.asyncio
    async def test_subquery_passes(self, base_state):
        """서브쿼리가 통과한다."""
        base_state["generated_sql"] = (
            "SELECT s.hostname "
            "FROM servers s "
            "WHERE s.id IN (SELECT server_id FROM cpu_metrics WHERE usage_pct > 90) "
            "LIMIT 10"
        )
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        assert result["validation_result"]["passed"] is True


class TestTableColumnValidation:
    """테이블/컬럼 존재 검증."""

    @pytest.mark.asyncio
    async def test_nonexistent_table_rejected(self, base_state):
        """존재하지 않는 테이블을 거부한다."""
        base_state["generated_sql"] = "SELECT * FROM fake_table LIMIT 10"
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        assert result["validation_result"]["passed"] is False
        assert "fake_table" in result["validation_result"]["reason"]

    def test_extract_tables_from_complex_sql(self):
        """복잡한 SQL에서 테이블을 정확히 추출한다."""
        sql = (
            "SELECT s.hostname, c.usage_pct, m.total_gb "
            "FROM servers s "
            "JOIN cpu_metrics c ON s.id = c.server_id "
            "LEFT JOIN memory_metrics m ON s.id = m.server_id "
            "LIMIT 10"
        )
        tables = _extract_table_names(sql)
        assert "servers" in tables
        assert "cpu_metrics" in tables
        assert "memory_metrics" in tables

    def test_validate_columns_with_alias(self, sample_schema_info):
        """별칭(alias) 사용 시 실제 테이블명에 대해서만 검증한다."""
        sql = "SELECT s.hostname FROM servers s LIMIT 10"
        # s는 별칭이므로 available_columns에 없어 검증 건너뜀 = 에러 없음
        errors = _validate_columns(sql, sample_schema_info, {"servers"})
        assert errors == []


class TestLimitAutoCorrection:
    """LIMIT 자동 보정 검증."""

    @pytest.mark.asyncio
    async def test_limit_auto_added_to_simple_query(self, base_state):
        """LIMIT 없는 단순 쿼리에 자동 추가한다."""
        base_state["generated_sql"] = "SELECT hostname FROM servers"
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        assert result["validation_result"]["passed"] is True
        assert "LIMIT 1000" in result["generated_sql"]

    @pytest.mark.asyncio
    async def test_existing_limit_preserved(self, base_state):
        """이미 LIMIT이 있는 쿼리는 그대로 유지한다."""
        base_state["generated_sql"] = "SELECT hostname FROM servers LIMIT 50"
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        assert result["validation_result"]["passed"] is True
        assert "LIMIT 50" in result["generated_sql"]
        assert "LIMIT 1000" not in result["generated_sql"]


class TestErrorMessageQuality:
    """검증 실패 시 에러 메시지 품질 검증."""

    @pytest.mark.asyncio
    async def test_failure_reason_is_specific(self, base_state):
        """실패 사유가 구체적이다."""
        base_state["generated_sql"] = "DROP TABLE servers"
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        reason = result["validation_result"]["reason"]
        assert len(reason) > 10  # 충분히 구체적인 메시지
        assert result["error_message"] is not None

    @pytest.mark.asyncio
    async def test_success_clears_error_message(self, base_state):
        """검증 성공 시 error_message가 None이다."""
        base_state["generated_sql"] = "SELECT hostname FROM servers LIMIT 10"
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(base_state)
        assert result["error_message"] is None
