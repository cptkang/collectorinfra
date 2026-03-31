"""_check_excluded_join_columns()의 ON 절 감지 단위 테스트."""

from src.nodes.query_validator import _check_excluded_join_columns


def _make_schema_info():
    """테스트용 schema_info를 생성한다."""
    return {
        "tables": {},
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "excluded_join_columns": [
                    {
                        "table": "cmm_resource",
                        "column": "resource_conf_id",
                        "reason": "NULL",
                    },
                ]
            }]
        },
    }


class TestCheckExcludedJoinColumns:
    """_check_excluded_join_columns() 테스트."""

    def test_detects_excluded_column_in_on_clause(self):
        """금지 컬럼이 ON 절에 사용되면 경고를 반환한다."""
        sql = """
        SELECT r.hostname, p.stringvalue_short
        FROM polestar.cmm_resource r
        JOIN polestar.core_config_prop p
          ON p.configuration_id = r.resource_conf_id
        LIMIT 100;
        """
        warnings = _check_excluded_join_columns(sql, _make_schema_info())
        assert len(warnings) == 1
        assert "resource_conf_id" in warnings[0]

    def test_no_warning_for_legitimate_join(self):
        """hostname 기반 브릿지 조인은 경고를 발생시키지 않는다."""
        sql = """
        SELECT r.hostname
        FROM polestar.cmm_resource r
        LEFT JOIN polestar.core_config_prop p_host
          ON p_host.name = 'Hostname' AND p_host.stringvalue_short = r.hostname
        LIMIT 100;
        """
        warnings = _check_excluded_join_columns(sql, _make_schema_info())
        assert len(warnings) == 0

    def test_no_warning_when_no_excluded_columns(self):
        """excluded_join_columns 설정이 없으면 경고가 없다."""
        sql = """
        SELECT r.hostname
        FROM polestar.cmm_resource r
        JOIN polestar.core_config_prop p
          ON p.configuration_id = r.resource_conf_id
        LIMIT 100;
        """
        schema_info = {"tables": {}}
        warnings = _check_excluded_join_columns(sql, schema_info)
        assert len(warnings) == 0

    def test_no_warning_when_column_in_where_not_on(self):
        """resource_conf_id가 WHERE 절에만 있고 ON 절에 없으면 경고 없다."""
        sql = """
        SELECT r.hostname, r.resource_conf_id
        FROM polestar.cmm_resource r
        WHERE r.resource_conf_id IS NOT NULL
        LIMIT 100;
        """
        warnings = _check_excluded_join_columns(sql, _make_schema_info())
        assert len(warnings) == 0

    def test_detects_with_alias(self):
        """테이블 별칭을 사용한 경우에도 컬럼명으로 감지한다."""
        sql = """
        SELECT r.hostname
        FROM polestar.cmm_resource r
        INNER JOIN polestar.core_config_prop p
          ON p.configuration_id = r.resource_conf_id AND p.name = 'OSType'
        LIMIT 100;
        """
        warnings = _check_excluded_join_columns(sql, _make_schema_info())
        assert len(warnings) == 1
        assert "resource_conf_id" in warnings[0]

    def test_multiple_on_clauses(self):
        """여러 JOIN ON 절이 있을 때 각각을 검사한다."""
        sql = """
        SELECT r.hostname
        FROM polestar.cmm_resource r
        JOIN polestar.core_config_prop p1
          ON p1.configuration_id = r.resource_conf_id
        LEFT JOIN polestar.core_config_prop p2
          ON p2.name = 'Hostname' AND p2.stringvalue_short = r.hostname
        LIMIT 100;
        """
        warnings = _check_excluded_join_columns(sql, _make_schema_info())
        assert len(warnings) == 1
        assert "resource_conf_id" in warnings[0]

    def test_case_insensitive_detection(self):
        """대소문자에 관계없이 금지 컬럼을 감지한다."""
        sql = """
        SELECT r.hostname
        FROM polestar.cmm_resource r
        JOIN polestar.core_config_prop p
          ON p.configuration_id = r.RESOURCE_CONF_ID
        LIMIT 100;
        """
        warnings = _check_excluded_join_columns(sql, _make_schema_info())
        assert len(warnings) == 1
