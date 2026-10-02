"""CTE(WITH ... SELECT) 검증 허용 회귀 (2026-07-21 gp-014).

sqlparse는 CTE의 get_type()을 UNKNOWN으로 반환해 "SELECT 문만 허용" 검증이 정당한 읽기 전용
CTE 쿼리를 원천 거부했다(재시도 소진 SKIP). 단일(_get_statement_type)·멀티(_validate_sql_simple)
경로 모두 WITH+SELECT를 허용하되, data-modifying CTE(DML 포함)는 계속 차단한다.
"""

from __future__ import annotations

from src.nodes.multi_db_executor import _validate_sql_simple
from src.nodes.query_validator import _get_statement_type


class TestStatementTypeCTE:
    def test_plain_select(self):
        assert _get_statement_type("SELECT 1;") == "SELECT"

    def test_with_cte_reclassified_as_select(self):
        sql = (
            "WITH ref AS (SELECT logicalcore FROM polestar.cmm_resource r WHERE r.dtime IS NULL) "
            "SELECT * FROM ref;"
        )
        assert _get_statement_type(sql) == "SELECT"

    def test_with_cte_leading_comment(self):
        sql = "-- 유사 사양 비교\nWITH ref AS (SELECT 1 AS x) SELECT * FROM ref;"
        assert _get_statement_type(sql) == "SELECT"

    def test_data_modifying_cte_still_blocked(self):
        sql = "WITH ref AS (DELETE FROM t RETURNING id) SELECT * FROM ref;"
        assert _get_statement_type(sql) != "SELECT"

    def test_non_select_stays_rejected(self):
        assert _get_statement_type("DROP TABLE t;") != "SELECT"


class TestExtractCteNames:
    """CTE·파생 테이블 별칭 추출 — 실존 테이블 검증 제외 대상(2026-07-21 gp-014 2회 실측)."""

    def test_leading_comment_does_not_break_cte_extraction(self):
        """주석으로 시작하는 SQL의 CTE 이름도 추출한다(실측: server_specs가 실존 검증에 걸림)."""
        from src.nodes.query_validator import _extract_cte_names

        sql = "-- 유사 사양 서버 조회\nWITH server_specs AS (SELECT 1 AS x) SELECT * FROM server_specs;"
        assert "server_specs" in _extract_cte_names(sql)

    def test_derived_table_alias_extracted(self):
        """파생 테이블 별칭((SELECT ...) ref)도 가상 이름으로 제외한다(실측: ref가 걸림)."""
        from src.nodes.query_validator import _extract_cte_names

        sql = (
            "SELECT * FROM polestar.cmm_resource r "
            "JOIN (SELECT id FROM polestar.cmm_resource WHERE dtime IS NULL) ref ON ref.id = r.id"
        )
        assert "ref" in _extract_cte_names(sql)

    def test_keywords_after_paren_not_extracted(self):
        from src.nodes.query_validator import _extract_cte_names

        sql = "SELECT COUNT(*) FROM polestar.cmm_resource WHERE (dtime IS NULL) AND id > 0 GROUP BY id"
        names = {n.upper() for n in _extract_cte_names(sql)}
        assert not names & {"AND", "GROUP", "WHERE"}


class TestMultiDbSimpleValidatorCTE:
    def test_with_cte_accepted(self):
        sql = (
            "WITH ref AS (SELECT id FROM polestar.cmm_resource WHERE dtime IS NULL) "
            "SELECT COUNT(*) FROM ref"
        )
        assert _validate_sql_simple(sql, {}) is None

    def test_with_cte_dml_rejected(self):
        sql = "WITH ref AS (SELECT 1) DELETE FROM t"
        assert _validate_sql_simple(sql, {}) is not None
