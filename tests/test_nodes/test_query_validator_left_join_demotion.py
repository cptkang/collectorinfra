"""_check_left_join_where_demotion()의 LEFT JOIN 강등 감지 단위 테스트 (D-085).

2026-07-16 SYN-H-02 실측 회귀: LEFT JOIN한 cmm_metric_stat_m의 필터를
WHERE에 두어 server.Server 행이 탈락, 서버명이 전부 NULL로 반환된 사례.
"""

from src.nodes.query_validator import _check_left_join_where_demotion

# SYN-H-02 실측 회귀 SQL (audit-2026-07-16 05:19:44 원본)
H02_REGRESSION_SQL = """
SELECT
    MAX(CASE WHEN c.resource_type = 'server.Server' THEN COALESCE(c.name, c.hostname) END) AS server_name,
    MAX(CASE WHEN c.resource_type = 'server.Cpus'   AND cc.name = 'LOGICALCORE' THEN cc.stringvalue_short END) AS logical_cores
FROM polestar.cmm_resource c
LEFT JOIN polestar.core_config_prop cc
      ON c.resource_conf_id = cc.configuration_id
LEFT JOIN polestar.cmm_metric_stat_m s
    ON c.id = s.resource_id
WHERE c.resource_type IN ('server.Server', 'server.Cpus')
  AND s.definition_name = 'Utilization'
  AND s.stat_date = '202606'
  AND c.dtime IS NULL
GROUP BY COALESCE(c.platform_resource_id, c.id)
HAVING AVG(CASE WHEN c.resource_type = 'server.Cpus' AND s.definition_name = 'Utilization' THEN s.avg_val END) > 40
LIMIT 1000;
"""


class TestDetectsDemotion:
    """강등 패턴을 감지해야 하는 케이스."""

    def test_detects_h02_regression(self):
        """SYN-H-02 회귀 SQL에서 s(cmm_metric_stat_m)의 WHERE 필터를 감지한다."""
        errors = _check_left_join_where_demotion(H02_REGRESSION_SQL)
        assert len(errors) == 1
        assert "LEFT JOIN 강등" in errors[0]
        assert "cmm_metric_stat_m" in errors[0]
        assert "definition_name" in errors[0]
        assert "stat_date" in errors[0]

    def test_detects_left_outer_join_variant(self):
        """LEFT OUTER JOIN 표기도 동일하게 감지한다."""
        sql = """
        SELECT r.name FROM polestar.cmm_resource r
        LEFT OUTER JOIN polestar.cmm_metric_stat_m s ON r.id = s.resource_id
        WHERE s.stat_date = '202606'
        LIMIT 100;
        """
        errors = _check_left_join_where_demotion(sql)
        assert len(errors) == 1
        assert "stat_date" in errors[0]

    def test_detects_bare_table_reference_without_alias(self):
        """별칭 없이 테이블명으로 참조해도 감지한다."""
        sql = """
        SELECT r.name FROM polestar.cmm_resource r
        LEFT JOIN polestar.cmm_metric_stat_m ON r.id = cmm_metric_stat_m.resource_id
        WHERE cmm_metric_stat_m.stat_date >= '202601'
        LIMIT 100;
        """
        errors = _check_left_join_where_demotion(sql)
        assert len(errors) == 1

    def test_detects_like_and_in_operators(self):
        """=, LIKE, IN 등 비교 연산자 전반을 감지한다."""
        sql = """
        SELECT r.name FROM polestar.cmm_resource r
        LEFT JOIN polestar.core_config_prop cc ON r.resource_conf_id = cc.configuration_id
        WHERE cc.name IN ('Vendor', 'SerialNumber') AND cc.stringvalue_short LIKE 'KR%'
        LIMIT 100;
        """
        errors = _check_left_join_where_demotion(sql)
        assert len(errors) == 1
        assert "core_config_prop" in errors[0]


class TestNoFalsePositives:
    """감지하지 않아야 하는 정상 케이스."""

    def test_pass_when_filter_moved_to_on(self):
        """H-02 교정본(필터를 ON 절로 이동)은 감지하지 않는다."""
        sql = """
        SELECT
            MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) AS server_name
        FROM polestar.cmm_resource c
        LEFT JOIN polestar.cmm_metric_stat_m s
            ON c.id = s.resource_id
           AND s.definition_name = 'Utilization'
           AND s.stat_date = '202606'
        WHERE c.resource_type IN ('server.Server', 'server.Cpus')
          AND c.dtime IS NULL
        GROUP BY COALESCE(c.platform_resource_id, c.id)
        HAVING AVG(CASE WHEN c.resource_type = 'server.Cpus' THEN s.avg_val END) > 40
        LIMIT 1000;
        """
        assert _check_left_join_where_demotion(sql) == []

    def test_pass_inner_join_filter_in_where(self):
        """INNER JOIN 테이블의 WHERE 필터는 정상 의미이므로 감지하지 않는다 (Template B 형태)."""
        sql = """
        SELECT svr.name, s.avg_val
        FROM polestar.cmm_resource r
        JOIN polestar.cmm_resource svr ON svr.id = r.platform_resource_id
        JOIN polestar.cmm_metric_stat_m s ON r.id = s.resource_id
        WHERE r.resource_type = 'server.Cpus'
          AND s.definition_name = 'Utilization'
          AND s.stat_date = '202606'
        LIMIT 100;
        """
        assert _check_left_join_where_demotion(sql) == []

    def test_pass_base_table_filter_only(self):
        """기준 테이블에 대한 WHERE 필터만 있으면 감지하지 않는다 (Template A 형태)."""
        sql = """
        SELECT
            MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) AS server_name,
            MAX(CASE WHEN cc.name = 'LOGICALCORE' THEN cc.stringvalue_short END) AS logicalcore
        FROM polestar.cmm_resource c
        LEFT JOIN polestar.core_config_prop cc ON c.resource_conf_id = cc.configuration_id
        WHERE c.resource_type IN ('server.Server', 'server.Cpus')
          AND c.dtime IS NULL
        GROUP BY COALESCE(c.platform_resource_id, c.id)
        LIMIT 100;
        """
        assert _check_left_join_where_demotion(sql) == []

    def test_pass_is_null_and_is_not_null(self):
        """IS NULL / IS NOT NULL 검사는 NULL 허용 조건이므로 감지하지 않는다."""
        sql = """
        SELECT r.name FROM polestar.cmm_resource r
        LEFT JOIN polestar.cmm_metric_stat_m s ON r.id = s.resource_id
        WHERE s.resource_id IS NULL AND s.stat_date IS NOT NULL
        LIMIT 100;
        """
        assert _check_left_join_where_demotion(sql) == []

    def test_pass_coalesce_wrapped_reference(self):
        """COALESCE 등 함수로 감싼 참조는 NULL 처리 명시로 간주해 감지하지 않는다."""
        sql = """
        SELECT r.name FROM polestar.cmm_resource r
        LEFT JOIN polestar.cmm_metric_stat_m s ON r.id = s.resource_id
        WHERE COALESCE(s.avg_val, 0) > 40
        LIMIT 100;
        """
        assert _check_left_join_where_demotion(sql) == []

    def test_pass_subquery_internal_where(self):
        """LEFT JOIN 서브쿼리 내부의 WHERE는 감지하지 않는다 (Template B hi 서브쿼리 형태)."""
        sql = """
        SELECT svr.name, hi.mem_size
        FROM polestar.cmm_resource svr
        LEFT JOIN (
            SELECT
                COALESCE(c.platform_resource_id, c.id) AS id,
                MAX(CASE WHEN cc.name = 'TotalSize' THEN cc.stringvalue_short END) AS mem_size
            FROM polestar.cmm_resource c
            LEFT JOIN polestar.core_config_prop cc ON c.resource_conf_id = cc.configuration_id
            WHERE c.resource_type IN ('server.Server', 'server.Memory')
              AND c.dtime IS NULL
            GROUP BY COALESCE(c.platform_resource_id, c.id)
        ) hi ON svr.id = hi.id
        WHERE svr.resource_type = 'server.Server'
        LIMIT 100;
        """
        assert _check_left_join_where_demotion(sql) == []

    def test_pass_no_left_join(self):
        """LEFT JOIN이 없는 SQL은 감지 대상이 아니다."""
        sql = "SELECT name FROM polestar.cmm_resource WHERE resource_type = 'server.Server' LIMIT 10;"
        assert _check_left_join_where_demotion(sql) == []


class TestMultiDbPathSymmetry:
    """멀티 DB 경로(_validate_sql_simple) 대칭 배선 검증 (D-066 계열)."""

    def test_multi_db_simple_validator_detects_demotion(self):
        """멀티 DB 간이 검증기도 동일 회귀 SQL을 차단한다."""
        from src.nodes.multi_db_executor import _validate_sql_simple

        error = _validate_sql_simple(H02_REGRESSION_SQL, {"tables": {}})
        assert error is not None
        assert "LEFT JOIN 강등" in error

    def test_multi_db_simple_validator_passes_corrected(self):
        """교정본(ON 절 배치)은 멀티 DB 간이 검증기를 통과한다."""
        from src.nodes.multi_db_executor import _validate_sql_simple

        # dtime IS NULL 포함 — 구 UX dtime 가드(D-104 계열, cmm_resource 조회 삭제서버 제외)가
        # 멀티 경로에도 병합돼(2026-07-22), 이를 누락하면 그 가드에 걸린다. 본 테스트 의도는
        # LEFT JOIN ON-절 배치 교정본이 통과함을 확인하는 것이므로 dtime 조건을 함께 둔다.
        sql = """
        SELECT r.name FROM polestar.cmm_resource r
        LEFT JOIN polestar.cmm_metric_stat_m s
            ON r.id = s.resource_id AND s.stat_date = '202606'
        WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL
        LIMIT 100;
        """
        assert _validate_sql_simple(sql, {"tables": {}}) is None
