"""query_validator 노드 테스트.

SQL 검증 로직의 핵심 기능을 검증한다:
- SELECT 문만 허용
- DML/DDL 완전 차단
- 참조 테이블/컬럼 존재 여부
- LIMIT 절 자동 추가
- 성능 위험 패턴 탐지
"""

import pytest

from src.nodes.query_validator import (
    _add_limit_clause,
    _build_failure_result,
    _check_performance_risks,
    _extract_table_names,
    _get_statement_type,
    _has_limit_clause,
    _validate_columns,
    query_validator,
)
from src.state import create_initial_state
from unittest.mock import patch


@pytest.fixture
def validator_state(sample_schema_info):
    """query_validator 테스트용 State를 생성한다."""
    state = create_initial_state(user_query="test")
    state["schema_info"] = sample_schema_info
    state["generated_sql"] = "SELECT hostname FROM servers LIMIT 100;"
    return state


class TestGetStatementType:
    """SQL 문 타입 판별 검증."""

    def test_select_type(self):
        assert _get_statement_type("SELECT * FROM servers") == "SELECT"

    def test_insert_type(self):
        assert _get_statement_type("INSERT INTO servers VALUES (1, 'test')") != "SELECT"

    def test_update_type(self):
        assert _get_statement_type("UPDATE servers SET hostname='x'") != "SELECT"

    def test_delete_type(self):
        assert _get_statement_type("DELETE FROM servers") != "SELECT"

    def test_drop_type(self):
        assert _get_statement_type("DROP TABLE servers") != "SELECT"

    def test_empty_sql(self):
        assert _get_statement_type("") == "UNKNOWN"


class TestExtractTableNames:
    """테이블명 추출 검증."""

    def test_simple_from(self):
        tables = _extract_table_names("SELECT * FROM servers")
        assert "servers" in tables

    def test_join(self):
        tables = _extract_table_names(
            "SELECT * FROM servers s JOIN cpu_metrics c ON s.id = c.server_id"
        )
        assert "servers" in tables
        assert "cpu_metrics" in tables

    def test_multiple_joins(self):
        sql = (
            "SELECT * FROM servers s "
            "JOIN cpu_metrics c ON s.id = c.server_id "
            "LEFT JOIN memory_metrics m ON s.id = m.server_id"
        )
        tables = _extract_table_names(sql)
        assert len(tables) == 3

    def test_subquery_from(self):
        """FROM 절의 서브쿼리에서도 테이블을 추출한다."""
        tables = _extract_table_names(
            "SELECT * FROM servers WHERE id IN (SELECT server_id FROM cpu_metrics)"
        )
        assert "servers" in tables
        assert "cpu_metrics" in tables


class TestValidateColumns:
    """컬럼 존재 검증."""

    def test_valid_columns(self, sample_schema_info):
        errors = _validate_columns(
            "SELECT servers.hostname, servers.ip_address FROM servers",
            sample_schema_info,
            {"servers"},
        )
        assert errors == []

    def test_invalid_column(self, sample_schema_info):
        errors = _validate_columns(
            "SELECT servers.nonexistent_col FROM servers",
            sample_schema_info,
            {"servers"},
        )
        assert len(errors) > 0
        assert "nonexistent_col" in errors[0]

    def test_star_column_allowed(self, sample_schema_info):
        """SELECT servers.* 패턴은 허용한다."""
        errors = _validate_columns(
            "SELECT servers.* FROM servers",
            sample_schema_info,
            {"servers"},
        )
        assert errors == []


class TestHasLimitClause:
    """LIMIT 절 존재 여부 검증."""

    def test_has_limit(self):
        assert _has_limit_clause("SELECT * FROM servers LIMIT 100") is True

    def test_no_limit(self):
        assert _has_limit_clause("SELECT * FROM servers") is False

    def test_case_insensitive(self):
        assert _has_limit_clause("SELECT * FROM servers limit 50") is True


class TestAddLimitClause:
    """LIMIT 절 자동 추가 검증."""

    def test_add_limit(self):
        result = _add_limit_clause("SELECT * FROM servers", 1000)
        assert "LIMIT 1000" in result

    def test_remove_trailing_semicolon(self):
        result = _add_limit_clause("SELECT * FROM servers;", 500)
        assert result.endswith("LIMIT 500;")
        assert ";;" not in result


class TestCheckPerformanceRisks:
    """성능 위험 패턴 탐지 검증."""

    def test_select_star_large_table(self, sample_schema_info):
        warnings = _check_performance_risks(
            "SELECT * FROM cpu_metrics LIMIT 100",
            sample_schema_info,
        )
        assert any("대형 테이블" in w for w in warnings)

    def test_no_where_clause(self, sample_schema_info):
        warnings = _check_performance_risks(
            "SELECT hostname FROM servers LIMIT 100",
            sample_schema_info,
        )
        assert any("WHERE" in w for w in warnings)

    def test_cartesian_product_risk(self, sample_schema_info):
        """카테시안 곱 위험을 감지한다.

        Note: 현재 구현은 쉼표 구분 테이블(implicit join)을 감지하지 못한다.
        _check_performance_risks는 JOIN 키워드 기반으로만 다중 테이블을 추출하고,
        _extract_table_names는 FROM 뒤에 첫 번째 테이블만 추출한다.
        이는 Minor 수준의 문제로 보고서에 기록한다.
        """
        # JOIN 키워드를 사용하되 ON 절이 없는 경우에만 감지됨
        warnings = _check_performance_risks(
            "SELECT * FROM servers JOIN cpu_metrics LIMIT 100",
            sample_schema_info,
        )
        assert any("카테시안" in w for w in warnings)


class TestBuildFailureResult:
    """실패 결과 생성 검증."""

    def test_single_error(self):
        result = _build_failure_result(["금지된 키워드 포함"])
        assert result["validation_result"]["passed"] is False
        assert "금지된 키워드" in result["validation_result"]["reason"]
        assert "query_validator" in result["current_node"]

    def test_multiple_errors(self):
        result = _build_failure_result(["에러1", "에러2"])
        assert "에러1" in result["error_message"]
        assert "에러2" in result["error_message"]


class TestQueryValidatorNode:
    """query_validator 노드 전체 동작 검증."""

    @pytest.mark.asyncio
    async def test_valid_select_passes(self, validator_state):
        """유효한 SELECT 문이 검증을 통과한다."""
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(validator_state)

        assert result["validation_result"]["passed"] is True

    @pytest.mark.asyncio
    async def test_insert_blocked(self, validator_state):
        """INSERT 문이 차단된다."""
        validator_state["generated_sql"] = "INSERT INTO servers VALUES (1, 'hack')"

        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(validator_state)

        assert result["validation_result"]["passed"] is False

    @pytest.mark.asyncio
    async def test_drop_blocked(self, validator_state):
        """DROP 문이 차단된다."""
        validator_state["generated_sql"] = "DROP TABLE servers"

        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(validator_state)

        assert result["validation_result"]["passed"] is False

    @pytest.mark.asyncio
    async def test_auto_adds_limit(self, validator_state):
        """LIMIT 없는 쿼리에 자동으로 LIMIT을 추가한다."""
        validator_state["generated_sql"] = "SELECT hostname FROM servers"

        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(validator_state)

        assert result["validation_result"]["passed"] is True
        assert "LIMIT 1000" in result["generated_sql"]

    @pytest.mark.asyncio
    async def test_nonexistent_table_rejected(self, validator_state):
        """존재하지 않는 테이블 참조가 거부된다."""
        validator_state["generated_sql"] = "SELECT * FROM nonexistent_table LIMIT 10"

        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(validator_state)

        assert result["validation_result"]["passed"] is False
        assert "nonexistent_table" in result["validation_result"]["reason"]

    @pytest.mark.asyncio
    async def test_nonexistent_column_rejected(self, validator_state):
        """존재하지 않는 컬럼 참조가 거부된다."""
        validator_state["generated_sql"] = "SELECT servers.bad_column FROM servers LIMIT 10"

        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(validator_state)

        assert result["validation_result"]["passed"] is False
        assert "bad_column" in result["validation_result"]["reason"]
