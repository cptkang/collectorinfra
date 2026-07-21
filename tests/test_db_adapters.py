"""DB 어댑터 레지스트리·배선 검증 (Plan 63 P2, D-089).

- 부트스트랩이 폴스타 어댑터를 등록하는지(죽은 레지스트리 방지, D-086 계열)
- get_adapter가 db_id/polestar_db_ids로 담당 어댑터를 조회하는지
- system_template/validator_checks 훅이 이동-불변으로 동작하는지
- query_generator·query_validator가 어댑터 디스패치로 배선됐는지(정의만 있고 소비처 없는 것 방지)
"""

import inspect
import sys

from src.db_adapters import get_adapter, registered_adapters
from src.db_adapters.polestar.prompts import (
    POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE,
    POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE,
)


class TestRegistryBootstrap:
    def test_polestar_registered_on_import(self):
        """`from src.db_adapters import ...`만으로 폴스타 어댑터가 등록된다."""
        assert "polestar" in {a.name for a in registered_adapters()}

    def test_get_adapter_owns_polestar(self):
        adapter = get_adapter("polestar_cm_gp", {"polestar_cm_gp", "polestar_cm_yd"})
        assert adapter is not None
        assert adapter.name == "polestar"

    def test_get_adapter_none_for_non_polestar(self):
        assert get_adapter("generic_mon", {"polestar_cm_gp"}) is None
        assert get_adapter("polestar_cm_gp", None) is None
        assert get_adapter(None, {"polestar_cm_gp"}) is None


class TestAdapterHooks:
    def test_system_template_by_intent(self):
        adapter = get_adapter("polestar", {"polestar"})
        assert adapter.system_template("alarm_query") is POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE
        assert adapter.system_template("data_query") is POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
        assert adapter.system_template(None) is POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE

    def test_validator_checks_detect_routing_misuse(self):
        adapter = get_adapter("polestar", {"polestar"})
        checks = adapter.validator_checks()
        assert len(checks) >= 1
        bad_sql = "SELECT name FROM t WHERE GROUP_PATH LIKE '%x%' LIMIT 10"
        assert any(check(bad_sql) for check in checks)
        good_sql = "SELECT name FROM t WHERE avail_status = 0 LIMIT 10"
        assert not any(check(good_sql) for check in checks)


class TestConsumerWiring:
    """공용 코어가 어댑터 디스패치를 실제로 소비하는지(죽은 배선 방지)."""

    def test_query_generator_uses_get_adapter(self):
        # src.nodes.__init__이 동명 함수로 모듈 속성을 가리므로 sys.modules에서 모듈을 가져온다.
        import src.nodes.query_generator  # noqa: F401
        qg = sys.modules["src.nodes.query_generator"]
        assert "get_adapter" in inspect.getsource(qg._build_system_prompt)

    def test_query_validator_uses_get_adapter(self):
        import src.nodes.query_validator  # noqa: F401
        qv = sys.modules["src.nodes.query_validator"]
        src = inspect.getsource(qv)
        assert "get_adapter" in src
        assert "validator_checks" in src


class TestScopeFilterWhereDemotion:
    """피벗 스코프 필터 WHERE 강등 탐지 (D-096).

    회귀 방지(2026-07-20 라이브 실측): 다중 resource_type 피벗 alias에 서버명 필터를
    WHERE로 걸면 자식 리소스 행(name='Cpus')이 탈락해 메트릭이 침묵히 0건이 됐다
    (D-095 선행 스코프 주입 SQL의 오답 형태 — 당시 validator 통과).
    """

    # 라이브 1런 차의 실제 오답 SQL(요약) — validator를 통과해 0건을 반환했던 형태
    BAD_WHERE_SQL = """
    -- 2026년 6월 CPU 사용률 평균이 가장 높은 서버의 제조사와 일련번호 조회
    SELECT
        MAX(CASE WHEN c.resource_type = 'server.Server' AND cc.name = 'Vendor' THEN cc.stringvalue_short END) AS manufacturer
    FROM polestar.cmm_resource c
    LEFT JOIN polestar.core_config_prop cc ON c.resource_conf_id = cc.configuration_id
    JOIN polestar.cmm_metric_stat_m s ON c.id = s.resource_id
    WHERE c.resource_type IN ('server.Server', 'server.Cpus')
      AND s.definition_name = 'Utilization'
      AND s.stat_date = '202606'
      AND c.dtime IS NULL
      AND c.name IN ('SV-WEB-001', 'SV-BATCH-009') -- 선행 작업 결과 서버 스코프 적용
    GROUP BY COALESCE(c.platform_resource_id, c.id)
    ORDER BY AVG(CASE WHEN c.resource_type = 'server.Cpus' THEN s.avg_val END) DESC
    LIMIT 1;
    """

    # 라이브 3런 차의 실제 정답 SQL(요약) — HAVING 집계 패턴
    GOOD_HAVING_SQL = """
    SELECT
        MAX(CASE WHEN c.resource_type = 'server.Server' AND cc.name = 'Vendor' THEN cc.stringvalue_short END) AS manufacturer
    FROM polestar.cmm_resource c
    LEFT JOIN polestar.core_config_prop cc ON c.resource_conf_id = cc.configuration_id
    LEFT JOIN polestar.cmm_metric_stat_m s
        ON c.id = s.resource_id AND s.definition_name = 'Utilization' AND s.stat_date = '202606'
    WHERE c.resource_type IN ('server.Server', 'server.Cpus')
      AND c.dtime IS NULL
    GROUP BY COALESCE(c.platform_resource_id, c.id)
    HAVING MAX(CASE WHEN c.resource_type = 'server.Server' THEN COALESCE(c.name, c.hostname) END) IN ('SV-WEB-001', 'SV-BATCH-009')
    ORDER BY MAX(CASE WHEN c.resource_type = 'server.Cpus' THEN s.avg_val END) DESC
    LIMIT 1;
    """

    def test_flags_where_name_filter_on_multi_type_alias(self):
        """다중 resource_type alias의 WHERE name 필터(실측 오답 SQL)를 검출한다."""
        from src.db_adapters.polestar.validators import check_scope_filter_where_demotion

        errors = check_scope_filter_where_demotion(self.BAD_WHERE_SQL)
        assert len(errors) == 1
        assert "HAVING" in errors[0]
        assert "'c'" in errors[0]

    def test_flags_coalesce_variant(self):
        """COALESCE(name, hostname) IN 형태의 WHERE 필터도 검출한다."""
        from src.db_adapters.polestar.validators import check_scope_filter_where_demotion

        sql = self.BAD_WHERE_SQL.replace(
            "c.name IN ('SV-WEB-001', 'SV-BATCH-009')",
            "COALESCE(c.name, c.hostname) IN ('SV-WEB-001', 'SV-BATCH-009')",
        )
        assert len(check_scope_filter_where_demotion(sql)) == 1

    def test_passes_having_aggregate_pattern(self):
        """정상 HAVING 집계 패턴(실측 정답 SQL)은 검출하지 않는다."""
        from src.db_adapters.polestar.validators import check_scope_filter_where_demotion

        assert check_scope_filter_where_demotion(self.GOOD_HAVING_SQL) == []

    def test_passes_two_alias_split_join(self):
        """서버/자식 리소스를 별도 alias로 분리한 정상 조인은 검출하지 않는다."""
        from src.db_adapters.polestar.validators import check_scope_filter_where_demotion

        sql = """
        SELECT svr.name, AVG(s.avg_val)
        FROM polestar.cmm_resource svr
        JOIN polestar.cmm_resource cpu
            ON cpu.platform_resource_id = svr.id AND cpu.resource_type = 'server.Cpus'
        JOIN polestar.cmm_metric_stat_m s ON cpu.id = s.resource_id
        WHERE svr.resource_type = 'server.Server'
          AND svr.name IN ('SV-WEB-001', 'SV-BATCH-009')
        GROUP BY svr.name;
        """
        assert check_scope_filter_where_demotion(sql) == []

    def test_passes_single_resource_type(self):
        """단일 resource_type alias의 name 필터는 정상(검출 안 함)."""
        from src.db_adapters.polestar.validators import check_scope_filter_where_demotion

        sql = """
        SELECT r.name FROM polestar.cmm_resource r
        WHERE r.resource_type IN ('server.Server')
          AND r.name IN ('SV-WEB-001');
        """
        assert check_scope_filter_where_demotion(sql) == []

    def test_comment_only_pattern_not_flagged(self):
        """주석 속 패턴은 제거 후 판정한다(D-087 규약)."""
        from src.db_adapters.polestar.validators import check_scope_filter_where_demotion

        sql = """
        SELECT r.name FROM polestar.cmm_resource r
        -- c.resource_type IN ('server.Server','server.Cpus') AND c.name IN ('x','y')
        WHERE r.resource_type = 'server.Server';
        """
        assert check_scope_filter_where_demotion(sql) == []

    def test_adapter_exposes_new_check(self):
        """폴스타 어댑터 validator_checks에 신규 검증이 포함된다."""
        from src.db_adapters.polestar.validators import check_scope_filter_where_demotion

        adapter = get_adapter("polestar", {"polestar"})
        assert check_scope_filter_where_demotion in adapter.validator_checks()


class TestScopedPivotMissingServerIdentity:
    """스코프된 피벗 조회의 SELECT 서버 식별 컬럼 누락 탐지 (D-097).

    회귀 방지(2026-07-20 라이브 실측): 선행 스코프(HAVING name IN) 피벗 SQL의 SELECT가
    manufacturer/serial_number만 조회해, 결과 행에 서버명이 없어 사용자가 어느 서버의
    값인지 알 수 없었다(서버명과 제조사·일련번호가 같은 행에 나오지 않음).
    """

    # 사용자 런(06:15)의 실제 오답 SQL(요약) — SELECT에 서버 식별 컬럼 없음
    BAD_NO_IDENTITY_SQL = """
    SELECT
        MAX(CASE WHEN c.resource_type = 'server.Server' THEN cc_vendor.stringvalue_short END) AS manufacturer,
        MAX(CASE WHEN c.resource_type = 'server.Server' THEN cc_serial.stringvalue_short END) AS serial_number
    FROM polestar.cmm_resource c
    LEFT JOIN polestar.core_config_prop cc_vendor
        ON c.resource_conf_id = cc_vendor.configuration_id AND cc_vendor.name = 'Vendor'
    LEFT JOIN polestar.core_config_prop cc_serial
        ON c.resource_conf_id = cc_serial.configuration_id AND cc_serial.name = 'SerialNumber'
    LEFT JOIN polestar.cmm_metric_stat_m s
        ON c.id = s.resource_id AND s.definition_name = 'Utilization' AND s.stat_date = '202606'
    WHERE c.resource_type IN ('server.Server', 'server.Cpus')
      AND c.dtime IS NULL
    GROUP BY COALESCE(c.platform_resource_id, c.id)
    HAVING MAX(CASE WHEN c.resource_type = 'server.Server' THEN COALESCE(c.name, c.hostname) END) IN ('SV-WEB-001', 'SV-BATCH-009')
    ORDER BY AVG(CASE WHEN c.resource_type = 'server.Cpus' THEN s.avg_val END) DESC
    LIMIT 1;
    """

    def test_flags_scoped_pivot_without_identity_in_select(self):
        """HAVING 스코프 + SELECT 식별 컬럼 부재(실측 오답 SQL)를 검출한다."""
        from src.db_adapters.polestar.validators import (
            check_scoped_pivot_missing_server_identity,
        )

        errors = check_scoped_pivot_missing_server_identity(self.BAD_NO_IDENTITY_SQL)
        assert len(errors) == 1
        assert "server_name" in errors[0]
        # EAV alias(cc_vendor.name='Vendor')는 서버 식별로 오인하지 않아야 검출됨
        assert "식별 컬럼" in errors[0]

    def test_passes_when_identity_in_select(self):
        """SELECT에 서버 식별 컬럼(COALESCE(name, hostname))이 있으면 정상."""
        from src.db_adapters.polestar.validators import (
            check_scoped_pivot_missing_server_identity,
        )

        sql = self.BAD_NO_IDENTITY_SQL.replace(
            "SELECT\n",
            "SELECT\n        MAX(CASE WHEN c.resource_type = 'server.Server' "
            "THEN COALESCE(c.name, c.hostname) END) AS server_name,\n",
            1,
        )
        assert check_scoped_pivot_missing_server_identity(sql) == []

    def test_passes_unscoped_pivot(self):
        """HAVING 스코프 없는 전체 피벗(폼필 조립기 형태)은 검사하지 않는다(오검출 방지)."""
        from src.db_adapters.polestar.validators import (
            check_scoped_pivot_missing_server_identity,
        )

        sql = """
        SELECT
            MAX(CASE WHEN c.resource_type = 'server.Server' AND cc.name = 'Vendor' THEN cc.stringvalue_short END) AS manufacturer
        FROM polestar.cmm_resource c
        LEFT JOIN polestar.core_config_prop cc ON cc.configuration_id = c.resource_conf_id
        WHERE c.resource_type IN ('server.Server', 'server.Cpus')
        GROUP BY COALESCE(c.platform_resource_id, c.id)
        LIMIT 1000;
        """
        assert check_scoped_pivot_missing_server_identity(sql) == []

    def test_passes_two_alias_scoped_join_with_identity(self):
        """분리 alias 조인 + SELECT에 서버 alias 식별 컬럼이 있으면 정상."""
        from src.db_adapters.polestar.validators import (
            check_scoped_pivot_missing_server_identity,
        )

        sql = """
        SELECT svr.name AS server_name, AVG(s.avg_val) AS cpu_avg
        FROM polestar.cmm_resource svr
        JOIN polestar.cmm_resource cpu
            ON cpu.platform_resource_id = svr.id AND cpu.resource_type = 'server.Cpus'
        JOIN polestar.cmm_metric_stat_m s ON cpu.id = s.resource_id
        WHERE svr.resource_type = 'server.Server'
        GROUP BY svr.name
        HAVING MAX(svr.name) IN ('SV-WEB-001', 'SV-BATCH-009');
        """
        assert check_scoped_pivot_missing_server_identity(sql) == []

    def test_adapter_exposes_identity_check(self):
        """폴스타 어댑터 validator_checks에 신규 검증이 포함된다."""
        from src.db_adapters.polestar.validators import (
            check_scoped_pivot_missing_server_identity,
        )

        adapter = get_adapter("polestar", {"polestar"})
        assert check_scoped_pivot_missing_server_identity in adapter.validator_checks()


class TestMetricJoinOnServerEntity:
    """성능 통계의 server.Server 고정 alias 조인 탐지 (D-098).

    회귀 방지(2026-07-20 라이브 실측): 통계를 서버 엔터티 id에 조인해 cpu_avg가 전부
    NULL → ORDER BY DESC(PostgreSQL NULLS FIRST)로 임의 서버가 1위로 선택되는 침묵 오답.
    """

    # 라이브 실측 오답 SQL(요약) — r은 server.Server로 고정, 통계는 r.id에 조인
    BAD_SERVER_JOIN_SQL = """
    SELECT
        MAX(CASE WHEN c.resource_type = 'server.Server' THEN COALESCE(c.name, c.hostname) END) AS server_name,
        AVG(CASE WHEN r.resource_type = 'server.Cpus' AND s.definition_name = 'Utilization' THEN s.avg_val END) AS cpu_avg_usage
    FROM polestar.cmm_resource c
    LEFT JOIN polestar.cmm_resource r ON c.platform_resource_id = r.id AND r.resource_type = 'server.Server'
    LEFT JOIN polestar.cmm_metric_stat_m s ON r.id = s.resource_id AND s.definition_name = 'Utilization' AND s.stat_date = '202606'
    WHERE c.resource_type IN ('server.Server', 'server.Cpus')
      AND c.dtime IS NULL
    GROUP BY COALESCE(c.platform_resource_id, c.id)
    HAVING MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) IN ('SV-WEB-001', 'SV-BATCH-009')
    ORDER BY cpu_avg_usage DESC
    LIMIT 1;
    """

    def test_flags_metric_join_to_server_fixed_alias(self):
        """server.Server 고정 alias의 id에 통계를 조인한 실측 오답 SQL을 검출한다."""
        from src.db_adapters.polestar.validators import check_metric_join_on_server_entity

        errors = check_metric_join_on_server_entity(self.BAD_SERVER_JOIN_SQL)
        assert len(errors) == 1
        assert "'r'" in errors[0]
        assert "자식 리소스" in errors[0]

    def test_passes_child_alias_join(self):
        """server.Cpus 고정 alias에 조인한 정상 형태는 검출하지 않는다."""
        from src.db_adapters.polestar.validators import check_metric_join_on_server_entity

        sql = """
        SELECT svr.name, AVG(s.avg_val)
        FROM polestar.cmm_resource svr
        JOIN polestar.cmm_resource cpu
            ON cpu.platform_resource_id = svr.id AND cpu.resource_type = 'server.Cpus'
        JOIN polestar.cmm_metric_stat_m s ON cpu.id = s.resource_id
        WHERE svr.resource_type = 'server.Server'
        GROUP BY svr.name;
        """
        assert check_metric_join_on_server_entity(sql) == []

    def test_passes_multi_type_pivot_alias_join(self):
        """다중 resource_type 피벗 alias(c)에 조인한 정상 형태(실측 정답 SQL)는 통과한다."""
        from src.db_adapters.polestar.validators import check_metric_join_on_server_entity

        sql = """
        SELECT MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) AS server_name
        FROM polestar.cmm_resource c
        LEFT JOIN polestar.cmm_metric_stat_m s
            ON c.id = s.resource_id AND s.definition_name = 'Utilization' AND s.stat_date = '202606'
        WHERE c.resource_type IN ('server.Server', 'server.Cpus')
        GROUP BY COALESCE(c.platform_resource_id, c.id);
        """
        assert check_metric_join_on_server_entity(sql) == []

    def test_passes_without_metric_table(self):
        """통계 테이블이 없는 SQL은 검사하지 않는다."""
        from src.db_adapters.polestar.validators import check_metric_join_on_server_entity

        sql = "SELECT r.name FROM polestar.cmm_resource r WHERE r.resource_type = 'server.Server';"
        assert check_metric_join_on_server_entity(sql) == []

    def test_adapter_exposes_metric_join_check(self):
        """폴스타 어댑터 validator_checks에 신규 검증이 포함된다."""
        from src.db_adapters.polestar.validators import check_metric_join_on_server_entity

        adapter = get_adapter("polestar", {"polestar"})
        assert check_metric_join_on_server_entity in adapter.validator_checks()


class TestPivotMetricInnerJoin:
    """다중 타입 피벗의 성능 통계 INNER JOIN 탐지 (D-098).

    회귀 방지(2026-07-20 라이브 실측): 통계 INNER JOIN이 server.Server 행을 그룹에서
    떨어뜨려 HAVING 서버 필터가 전부 NULL → 침묵 0건("데이터 없음") 오답.
    """

    # 라이브 실측 오답 SQL(요약) — 다중 타입 피벗 alias c에 통계 INNER JOIN
    BAD_INNER_JOIN_SQL = """
    SELECT
        MAX(CASE WHEN c.resource_type = 'server.Server' THEN COALESCE(c.name, c.hostname) END) AS server_name,
        AVG(CASE WHEN c.resource_type = 'server.Cpus' AND s.definition_name = 'Utilization' THEN s.avg_val END) AS cpu_usage_avg
    FROM polestar.cmm_resource c
    JOIN polestar.cmm_metric_stat_m s
        ON c.id = s.resource_id
    WHERE c.resource_type IN ('server.Server', 'server.Cpus')
      AND s.definition_name = 'Utilization'
      AND s.stat_date = '202606'
      AND c.dtime IS NULL
    GROUP BY COALESCE(c.platform_resource_id, c.id)
    HAVING MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) IN ('SV-WEB-001', 'SV-BATCH-009')
    ORDER BY cpu_usage_avg DESC
    LIMIT 1;
    """

    def test_flags_inner_join_on_multi_type_pivot(self):
        """다중 타입 피벗 alias에 통계 INNER JOIN(실측 오답 SQL)을 검출한다."""
        from src.db_adapters.polestar.validators import check_pivot_metric_inner_join

        errors = check_pivot_metric_inner_join(self.BAD_INNER_JOIN_SQL)
        assert len(errors) == 1
        assert "LEFT JOIN" in errors[0]
        assert "'c'" in errors[0]

    def test_passes_left_join_on_multi_type_pivot(self):
        """같은 피벗의 LEFT JOIN + ON 조건(정답 형태)은 검출하지 않는다."""
        from src.db_adapters.polestar.validators import check_pivot_metric_inner_join

        sql = self.BAD_INNER_JOIN_SQL.replace(
            "JOIN polestar.cmm_metric_stat_m s\n        ON c.id = s.resource_id",
            "LEFT JOIN polestar.cmm_metric_stat_m s\n        ON c.id = s.resource_id "
            "AND s.definition_name = 'Utilization' AND s.stat_date = '202606'",
        )
        assert check_pivot_metric_inner_join(sql) == []

    def test_passes_inner_join_on_child_only_alias(self):
        """자식 리소스 단일 타입 alias(cpu)에 대한 INNER JOIN은 정상(검출 안 함)."""
        from src.db_adapters.polestar.validators import check_pivot_metric_inner_join

        sql = """
        SELECT svr.name, AVG(s.avg_val)
        FROM polestar.cmm_resource svr
        JOIN polestar.cmm_resource cpu
            ON cpu.platform_resource_id = svr.id AND cpu.resource_type = 'server.Cpus'
        JOIN polestar.cmm_metric_stat_m s ON cpu.id = s.resource_id
        WHERE svr.resource_type = 'server.Server'
        GROUP BY svr.name;
        """
        assert check_pivot_metric_inner_join(sql) == []

    def test_adapter_exposes_inner_join_check(self):
        """폴스타 어댑터 validator_checks에 신규 검증이 포함된다."""
        from src.db_adapters.polestar.validators import check_pivot_metric_inner_join

        adapter = get_adapter("polestar", {"polestar"})
        assert check_pivot_metric_inner_join in adapter.validator_checks()


class TestContradictoryAliasResourceType:
    """모순 alias resource_type 조건 탐지 (D-099).

    회귀 방지(2026-07-20 라이브 실측): r은 조인에서 server.Server로 고정됐는데 집계가
    server.Cpus를 검사 → 항상 NULL → DESC NULLS FIRST로 임의 서버(SV-BATCH-009)가 1위.
    """

    BAD_CONTRADICTORY_SQL = """
    SELECT
        MAX(CASE WHEN c.resource_type = 'server.Server' THEN COALESCE(c.name, c.hostname) END) AS server_name,
        AVG(CASE WHEN r.resource_type = 'server.Cpus' AND s.definition_name = 'Utilization' THEN s.avg_val END) AS cpu_avg_usage
    FROM polestar.cmm_resource c
    LEFT JOIN polestar.cmm_resource r
        ON c.platform_resource_id = r.id AND r.resource_type = 'server.Server'
    LEFT JOIN polestar.cmm_metric_stat_m s
        ON c.id = s.resource_id AND s.definition_name = 'Utilization' AND s.stat_date = '202606'
    WHERE c.resource_type IN ('server.Server', 'server.Cpus')
      AND c.dtime IS NULL
    GROUP BY COALESCE(c.platform_resource_id, c.id)
    HAVING MAX(CASE WHEN c.resource_type = 'server.Server' THEN COALESCE(c.name, c.hostname) END) IN ('SV-WEB-001', 'SV-BATCH-009')
    ORDER BY cpu_avg_usage DESC NULLS LAST
    LIMIT 1;
    """

    def test_flags_contradictory_predicate(self):
        """고정 alias를 다른 resource_type으로 검사하는 실측 오답 SQL을 검출한다."""
        from src.db_adapters.polestar.validators import (
            check_contradictory_alias_resource_type,
        )

        errors = check_contradictory_alias_resource_type(self.BAD_CONTRADICTORY_SQL)
        assert len(errors) == 1
        assert "'r'" in errors[0]
        assert "server.Cpus" in errors[0]

    def test_passes_multi_type_alias(self):
        """다중 타입 피벗 alias(c)는 두 타입 모두 허용되므로 검출하지 않는다."""
        from src.db_adapters.polestar.validators import (
            check_contradictory_alias_resource_type,
        )

        sql = self.BAD_CONTRADICTORY_SQL.replace(
            "AVG(CASE WHEN r.resource_type = 'server.Cpus'",
            "AVG(CASE WHEN c.resource_type = 'server.Cpus'",
        )
        assert check_contradictory_alias_resource_type(sql) == []

    def test_passes_child_alias_with_matching_type(self):
        """자식 타입으로 고정된 alias를 같은 타입으로 검사하면 정상."""
        from src.db_adapters.polestar.validators import (
            check_contradictory_alias_resource_type,
        )

        sql = """
        SELECT AVG(CASE WHEN cpu.resource_type = 'server.Cpus' THEN s.avg_val END) AS cpu_avg
        FROM polestar.cmm_resource svr
        JOIN polestar.cmm_resource cpu
            ON cpu.platform_resource_id = svr.id AND cpu.resource_type = 'server.Cpus'
        JOIN polestar.cmm_metric_stat_m s ON cpu.id = s.resource_id
        WHERE svr.resource_type = 'server.Server';
        """
        assert check_contradictory_alias_resource_type(sql) == []


class TestRankingOrderByNullsLast:
    """순위 정렬 NULLS LAST 누락 탐지 (D-099)."""

    def test_flags_desc_aggregate_without_nulls_last(self):
        """집계 alias DESC + LIMIT인데 NULLS LAST가 없으면 검출한다."""
        from src.db_adapters.polestar.validators import check_ranking_order_by_nulls_last

        sql = """
        SELECT c.name, AVG(s.avg_val) AS cpu_avg
        FROM polestar.cmm_resource c
        LEFT JOIN polestar.cmm_metric_stat_m s ON c.id = s.resource_id
        GROUP BY c.name
        ORDER BY cpu_avg DESC
        LIMIT 1;
        """
        errors = check_ranking_order_by_nulls_last(sql)
        assert len(errors) == 1
        assert "NULLS FIRST" in errors[0]

    def test_flags_inline_aggregate_expression(self):
        """집계식 직접 정렬도 검출한다."""
        from src.db_adapters.polestar.validators import check_ranking_order_by_nulls_last

        sql = """
        SELECT c.name FROM polestar.cmm_resource c
        LEFT JOIN polestar.cmm_metric_stat_m s ON c.id = s.resource_id
        GROUP BY c.name
        ORDER BY AVG(s.avg_val) DESC
        LIMIT 1;
        """
        assert len(check_ranking_order_by_nulls_last(sql)) == 1

    def test_passes_with_nulls_last(self):
        """NULLS LAST가 있으면 통과한다."""
        from src.db_adapters.polestar.validators import check_ranking_order_by_nulls_last

        sql = """
        SELECT c.name, AVG(s.avg_val) AS cpu_avg
        FROM polestar.cmm_resource c
        LEFT JOIN polestar.cmm_metric_stat_m s ON c.id = s.resource_id
        GROUP BY c.name
        ORDER BY cpu_avg DESC NULLS LAST
        LIMIT 1;
        """
        assert check_ranking_order_by_nulls_last(sql) == []

    def test_passes_non_aggregate_ordering(self):
        """비집계 컬럼 정렬(이름·시각 등)은 검사하지 않는다(오검출 방지)."""
        from src.db_adapters.polestar.validators import check_ranking_order_by_nulls_last

        sql = """
        SELECT CR.NAME AS server_name FROM polestar.cmm_resource CR
        JOIN polestar.cmm_alarm CA ON CA.RESOURCE_ID = CR.ID
        ORDER BY CA.CTIME DESC
        LIMIT 1000;
        """
        assert check_ranking_order_by_nulls_last(sql) == []

    def test_passes_without_limit(self):
        """행 제한이 없으면 순위 질의가 아니므로 검사하지 않는다."""
        from src.db_adapters.polestar.validators import check_ranking_order_by_nulls_last

        sql = """
        SELECT c.name, AVG(s.avg_val) AS cpu_avg
        FROM polestar.cmm_resource c
        LEFT JOIN polestar.cmm_metric_stat_m s ON c.id = s.resource_id
        GROUP BY c.name ORDER BY cpu_avg DESC;
        """
        assert check_ranking_order_by_nulls_last(sql) == []


class TestPivotScopeAndRanking:
    """조립기의 선행 스코프 HAVING + 순위 ORDER BY NULLS LAST 조립 (D-099)."""

    EAV = {
        "entity_table": "cmm_resource", "config_table": "core_config_prop",
        "attribute_column": "name", "value_column": "stringvalue_short",
        "direct_join": {"entity_column": "resource_conf_id", "config_column": "configuration_id"},
    }

    def _build(self, **kwargs):
        from src.db_adapters.polestar.assembler import build_multi_resource_pivot_sql

        return build_multi_resource_pivot_sql(
            [("server_name", "cmm_resource.name")],
            [("manufacturer", "Vendor"), ("serial_number", "SerialNumber")],
            [], self.EAV,
            db_schema="polestar", limit=1, stat_month="202606",
            explicit_measures=[("cpus_avg", "server.Cpus", "AVG", "avg_val", "Utilization")],
            **kwargs,
        )

    def test_scope_emits_having_not_where(self):
        """서버 스코프는 HAVING 집계 CASE WHEN으로 나가야 한다(WHERE면 자식 행 탈락 — D-096)."""
        sql = self._build(server_scope=("name", ["SV-WEB-001", "SV-BATCH-009"]))
        assert "HAVING MAX(CASE WHEN c.resource_type='server.Server' THEN c.name END) IN " in sql
        assert "'SV-WEB-001', 'SV-BATCH-009'" in sql
        # WHERE 절에는 서버명 필터가 없어야 함
        where_seg = sql.split("WHERE", 1)[1].split("GROUP BY", 1)[0]
        assert "SV-WEB-001" not in where_seg

    def test_ranking_emits_nulls_last(self):
        """순위 정렬은 항상 NULLS LAST를 부여한다(D-098)."""
        sql = self._build(order_by=("cpus_avg", "DESC"))
        assert 'ORDER BY "cpus_avg" DESC NULLS LAST' in sql

    def test_ascending_ranking(self):
        """오름차순 순위도 NULLS LAST를 유지한다."""
        sql = self._build(order_by=("cpus_avg", "ASC"))
        assert 'ORDER BY "cpus_avg" ASC NULLS LAST' in sql

    def test_no_scope_no_ranking_unchanged(self):
        """스코프·순위 미지정이면 기존 출력 그대로(회귀 0)."""
        sql = self._build()
        assert "HAVING" not in sql
        assert "ORDER BY" not in sql

    def test_scope_value_quote_escaped(self):
        """작은따옴표가 포함된 값은 이스케이프한다(SQL 인젝션·문법 오류 방지)."""
        sql = self._build(server_scope=("name", ["O'Brien"]))
        assert "'O''Brien'" in sql
