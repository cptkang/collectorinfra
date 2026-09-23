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
        """의도별 템플릿 분기. 데이터 조회 템플릿은 지식 정본에서 렌더된다(Plan 67 R1-2)."""
        adapter = get_adapter("polestar", {"polestar"})
        assert adapter.system_template("alarm_query") is POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE
        assert adapter.system_template("data_query") == POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
        assert adapter.system_template(None) == POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE

    def test_system_template_follows_knowledge_render_flag(self, monkeypatch):
        """잔여 블록 렌더 플래그가 어댑터 훅까지 배선돼 있다(Plan 67 R1 잔여, 기본 OFF).

        알람 템플릿은 전 블록이 정본과 바이트 일치해 ON에서도 동일하고(무해 전환), 데이터
        템플릿만 `hi` 조인 키 교정·지표 설명 표기 때문에 달라진다.
        """
        from src.db_adapters.polestar import prompts as polestar_prompts

        adapter = get_adapter("polestar", {"polestar"})
        monkeypatch.setattr(polestar_prompts, "_rendered_cache", {})
        monkeypatch.setattr(polestar_prompts, "knowledge_render_enabled", lambda: True)
        assert adapter.system_template("alarm_query") == POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE
        assert adapter.system_template("data_query") != POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
        assert ") hi ON svr.id = hi.id" in adapter.system_template("data_query")

    def test_validator_checks_detect_routing_misuse(self):
        adapter = get_adapter("polestar", {"polestar"})
        checks = adapter.validator_checks()
        assert len(checks) >= 1
        bad_sql = "SELECT name FROM t WHERE GROUP_PATH LIKE '%x%' LIMIT 10"
        assert any(check(bad_sql) for check in checks)
        good_sql = "SELECT name FROM t WHERE avail_status = 0 LIMIT 10"
        assert not any(check(good_sql) for check in checks)

    def test_classify_metric_field_hook_delegates_to_assembler(self):
        """src/tools/metrics.py optional 훅 계약 — 어댑터가 assembler 분류를 노출한다(Plan 67 S1 후속)."""
        from src.db_adapters.polestar.assembler import classify_metric_field as assembler_classify
        from src.tools.metrics import classify_metric_field as tool_classify

        adapter = get_adapter("polestar", {"polestar"})
        sample = "CPU 사용률(평균)"
        expected = assembler_classify(sample)
        assert expected is not None
        assert adapter.classify_metric_field(sample) == expected

        result = tool_classify(sample, db_id="polestar", adapter_db_ids={"polestar"})
        assert result["source"] == "adapter"
        assert (result["resource_type"], result["agg_function"], result["value_column"]) == expected


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
        from src.db_adapters.polestar.assembler import build_semantic_pivot_sql

        return build_semantic_pivot_sql(
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


class TestAlarmSeverityChecks:
    """알람 허용 테이블·심각도 라벨 필터 검사 (2026-09-01 A-04 라이브 실측 재발 방지)."""

    # A-04 실측 SQL 형태 — 허용 목록 밖 표시 테이블 조인 + 영문 라벨 ILIKE → 침묵 0건
    _A04_SQL = (
        "SELECT a.id, sd.displayname AS severity FROM polestar.cmm_alarm a "
        "JOIN polestar.cmm_alarm_severity_display sd ON sd.severity = a.alarmseverity "
        "JOIN polestar.cmm_resource r ON r.id = a.resource_id "
        "WHERE a.currentalarmstatus = 'ACTIVE' AND sd.displayname ILIKE '%critical%' "
        "AND r.dtime IS NULL LIMIT 100"
    )

    def test_allowlist_rejects_severity_display(self):
        from src.db_adapters.polestar.validators import check_alarm_table_allowlist

        errors = check_alarm_table_allowlist(self._A04_SQL)
        assert len(errors) == 1
        assert "cmm_alarm_severity_display" in errors[0]
        assert "alarmseverity" in errors[0]  # 정수 비교 안내 동봉

    def test_allowlist_passes_allowed_tables(self):
        from src.db_adapters.polestar.validators import check_alarm_table_allowlist

        sql = (
            "SELECT ca.* FROM polestar.cmm_alarm_active ca "
            "JOIN polestar.cmm_alarm a ON a.id = ca.alarm_id "
            "JOIN polestar.cmm_alarm_def d ON d.id = a.alarm_def_id "
            "JOIN polestar.cmm_alarm_def_noti n ON n.alarm_def_id = d.id LIMIT 100"
        )
        assert check_alarm_table_allowlist(sql) == []

    def test_severity_label_string_compare_rejected(self):
        from src.db_adapters.polestar.validators import check_severity_label_filter

        errors = check_severity_label_filter(
            "SELECT * FROM cmm_alarm_active WHERE alarmseverity = '심각' LIMIT 10"
        )
        assert errors
        assert "alarmseverity" in errors[0]

    def test_severity_numeric_and_numeric_string_pass(self):
        from src.db_adapters.polestar.validators import check_severity_label_filter

        assert check_severity_label_filter(
            "SELECT * FROM cmm_alarm_active a WHERE a.alarmseverity = 3 LIMIT 10"
        ) == []
        # 숫자 문자열은 암묵 캐스트로 동작하므로 허용
        assert check_severity_label_filter(
            "SELECT * FROM cmm_alarm_active a WHERE a.alarmseverity IN ('1', '2', '3')"
        ) == []

    def test_severity_in_label_rejected(self):
        from src.db_adapters.polestar.validators import check_severity_label_filter

        errors = check_severity_label_filter(
            "SELECT * FROM cmm_alarm WHERE alarmseverity IN ('critical', 'warning')"
        )
        assert errors

    def test_displayname_label_filter_rejected(self):
        from src.db_adapters.polestar.validators import check_severity_label_filter

        errors = check_severity_label_filter(self._A04_SQL)
        assert errors
        assert "displayname" in errors[0]

    def test_display_case_when_labels_pass(self):
        """정당한 표시 패턴(CASE WHEN alarmseverity = 3 THEN '심각')은 필터가 아니므로 통과."""
        from src.db_adapters.polestar.validators import check_severity_label_filter

        sql = (
            "SELECT CASE WHEN ca.alarmseverity = 3 THEN '심각' "
            "WHEN ca.alarmseverity = 2 THEN '경고' ELSE '기타' END AS severity_grade "
            "FROM cmm_alarm_active ca WHERE ca.alarmseverity >= 2 LIMIT 100"
        )
        assert check_severity_label_filter(sql) == []

    def test_checks_registered_in_adapter(self):
        """정의만 있고 배선 안 된 검사 방지 — validator_checks()에 실제 등재."""
        adapter = get_adapter("polestar", {"polestar"})
        names = {c.__name__ for c in adapter.validator_checks()}
        assert {"check_alarm_table_allowlist", "check_severity_label_filter"} <= names


class TestActiveStatusLiteralFilter:
    """currentalarmstatus 문자열 비교 반려 (2026-09-01 V1-4/V1-5 실측 재발 방지)."""

    def test_active_string_compare_rejected(self):
        from src.db_adapters.polestar.validators import check_active_status_literal_filter

        errors = check_active_status_literal_filter(
            "SELECT * FROM polestar.cmm_alarm a "
            "WHERE a.currentalarmstatus = 'ACTIVE' LIMIT 100"
        )
        assert errors
        assert "cmm_alarm_active" in errors[0]  # 정본 조인 안내 동봉

    def test_active_in_list_rejected(self):
        from src.db_adapters.polestar.validators import check_active_status_literal_filter

        errors = check_active_status_literal_filter(
            "SELECT * FROM cmm_alarm WHERE currentalarmstatus IN ('ACTIVE', 'OPEN')"
        )
        assert errors

    def test_function_wrapped_and_reversed_compare_rejected(self):
        """함수로 감싼 칼럼·좌우 뒤집힌 비교도 같은 오답이다(plans/116 §10.3 — 'active' 0건)."""
        from src.db_adapters.polestar.validators import check_active_status_literal_filter

        for where in (
            "LOWER(a.currentalarmstatus) = 'active'",
            "UPPER(TRIM(a.currentalarmstatus)) = 'ACTIVE'",
            "'active' = a.currentalarmstatus",
        ):
            sql = f"SELECT * FROM polestar.cmm_alarm a WHERE {where} LIMIT 100"
            assert check_active_status_literal_filter(sql), where

    def test_ack_vocabulary_passes(self):
        """확인(ACK) 상태 어휘 비교는 정당하다 — 미확인 알람 질의 (2026-09-02 실측:
        cmm_alarm_active.currentalarmstatus='NOT_ACK' 9건 실반환)."""
        from src.db_adapters.polestar.validators import check_active_status_literal_filter

        sql = (
            "SELECT * FROM polestar.cmm_alarm_active aa "
            "WHERE aa.alarmseverity = 3 AND aa.currentalarmstatus = 'NOT_ACK' LIMIT 100"
        )
        assert check_active_status_literal_filter(sql) == []

    def test_canonical_active_join_passes(self):
        from src.db_adapters.polestar.validators import check_active_status_literal_filter

        sql = (
            "SELECT a.* FROM polestar.cmm_alarm a "
            "JOIN polestar.cmm_alarm_active ca ON ca.alarm_id = a.id "
            "WHERE a.alarmseverity = 3 LIMIT 100"
        )
        assert check_active_status_literal_filter(sql) == []

    def test_select_column_without_filter_passes(self):
        """필터가 아닌 SELECT 표시용 참조는 반려하지 않는다."""
        from src.db_adapters.polestar.validators import check_active_status_literal_filter

        sql = (
            "SELECT a.currentalarmstatus AS alarm_status FROM cmm_alarm a "
            "WHERE a.alarmseverity = 3 LIMIT 100"
        )
        assert check_active_status_literal_filter(sql) == []

    def test_registered_in_adapter(self):
        adapter = get_adapter("polestar", {"polestar"})
        names = {c.__name__ for c in adapter.validator_checks()}
        assert "check_active_status_literal_filter" in names


class TestActiveAlarmAssembly:
    """활성 알람 결정적 조립 (2026-09-02 폐쇄망 실측 — 건수 요동·CSV 칼럼 분리 해소)."""

    def _spec(self, q):
        from src.db_adapters.polestar.assembler import recognize_active_alarm_query
        return recognize_active_alarm_query(q)

    def test_recognize_active_severity3(self):
        spec = self._spec("현재 활성 상태인 심각(severity 3) 알람 목록을 조회해줘")
        assert spec is not None
        assert spec.severity == 3 and spec.severity_op == "="
        assert not spec.unack_only and not spec.count_only

    def test_recognize_warning_or_above(self):
        spec = self._spec("활성 경고 이상 알람 보여줘")
        assert spec is not None and spec.severity == 2 and spec.severity_op == ">="

    def test_severity_word_in_sort_phrase_is_not_a_filter(self):
        """「심각도가 높은 순서로」의 '심각'은 등급어가 아니다(plans/116 §10.3).

        종전에는 부분 문자열로 심각(3) 필터가 걸려 경고·주의 활성 알람이 빠졌다.
        """
        spec = self._spec("현재 발생 중인 알람을 심각도가 높은 순서로 보여줘")
        assert spec is not None and spec.mode == "active"
        assert spec.severity is None
        # 등급어는 그대로 인식한다
        assert self._spec("현재 발생 중인 심각 알람 보여줘").severity == 3
        assert self._spec("현재 발생 중인 심각도 2 알람 보여줘").severity == 2

    def test_recognize_unack_count(self):
        spec = self._spec("현재 활성 미확인 알람 몇 건이야")
        assert spec is not None and spec.unack_only and spec.count_only

    def test_history_query_routes_to_history_mode(self):
        """이력 질의는 활성 조립이 아니라 이력 조립으로 간다 (2.5차 계약 변경)."""
        spec = self._spec("최근 3개월 심각 알람 이력 보여줘")
        assert spec is not None and spec.mode == "history"

    def test_non_alarm_not_recognized(self):
        assert self._spec("현재 활성 서버 목록") is None

    def test_pg_sql_shape(self):
        from src.db_adapters.polestar.assembler import build_active_alarm_sql
        spec = self._spec("현재 활성 심각 알람 목록")
        sql = build_active_alarm_sql(
            spec, db_engine="postgresql", db_schema="polestar", limit=10000
        )
        assert "polestar.cmm_alarm_active" in sql
        assert "a.alarmseverity = 3" in sql
        assert "LIMIT 10000" in sql
        # 실측 확정 골격: 승격 LEFT JOIN — resource_type INNER 축소 금지
        assert "LEFT JOIN" in sql
        assert "COALESCE(res.platform_resource_id, res.id) = srv.id" in sql
        # 활성 판정에 ACK 상태 칼럼 필터 금지
        assert "currentalarmstatus =" not in sql.replace("AS ack_status", "")
        # dtime 필터는 ON 절 (LEFT JOIN WHERE 강등 검출과 충돌하지 않게)
        assert "res.dtime IS NULL" in sql and "srv.dtime IS NULL" in sql

    def test_db2_sql_shape(self):
        from src.db_adapters.polestar.assembler import build_active_alarm_sql
        spec = self._spec("현재 활성 심각 알람 목록")
        sql = build_active_alarm_sql(
            spec, db_engine="db2", db_schema="POLESTAR", limit=10000
        )
        assert "POLESTAR.cmm_alarm_active" in sql
        assert "FETCH FIRST 10000 ROWS ONLY" in sql
        assert "LIMIT" not in sql

    def test_same_aliases_across_engines(self):
        """3존 동일 별칭 — 병합 CSV 칼럼 분리 해소의 계약."""
        from src.db_adapters.polestar.assembler import build_active_alarm_sql
        spec = self._spec("현재 활성 심각 알람 목록")
        pg = build_active_alarm_sql(spec, db_engine="postgresql", db_schema="polestar", limit=10)
        db2 = build_active_alarm_sql(spec, db_engine="db2", db_schema="POLESTAR", limit=10)
        import re as _re
        aliases = lambda s: _re.findall(r"AS (\w+)", s.split("FROM")[0])
        assert aliases(pg) == aliases(db2)

    def test_unack_filter_and_count_sql(self):
        from src.db_adapters.polestar.assembler import build_active_alarm_sql
        spec = self._spec("현재 활성 미확인 알람 몇 건")
        sql = build_active_alarm_sql(spec, db_engine="postgresql", db_schema="polestar", limit=10)
        assert "COUNT(*) AS alarm_count" in sql
        assert "a.currentalarmstatus = 'NOT_ACK'" in sql

    def test_flag_off_returns_none(self):
        from src.db_adapters.polestar.assembler import try_deterministic_alarm_sql
        assert try_deterministic_alarm_sql(
            "현재 활성 심각 알람", routing_intent="alarm_query",
            db_engine="postgresql", db_schema="polestar", limit=10, enabled=False,
        ) is None

    def test_non_alarm_intent_returns_none(self):
        from src.db_adapters.polestar.assembler import try_deterministic_alarm_sql
        assert try_deterministic_alarm_sql(
            "현재 활성 심각 알람", routing_intent="data_query",
            db_engine="postgresql", db_schema="polestar", limit=10, enabled=True,
        ) is None

    def test_wired_in_both_paths(self):
        """정의만 있고 소비처 없는 것 방지 — 단일·멀티 양 경로 배선 고정 (비대칭 재발 방지).

        1.5차에서 어댑터 훅을 등재만 하고 멀티 경로 소비를 실측하지 않아 폐쇄망에서
        가드가 미발동했다(docs/18 2026-09-02). 조립 훅은 소스 소비를 직접 고정한다.
        """
        import importlib
        import inspect

        mde = importlib.import_module("src.nodes.multi_db_executor")
        qg = importlib.import_module("src.nodes.query_generator")
        multi_src = inspect.getsource(mde._generate_validated_sql)
        assert "_deterministic_alarm_sql_or_none" in multi_src
        single_src = inspect.getsource(qg.query_generator)
        assert "_try_deterministic_alarm_single" in single_src

    def test_assembled_sql_passes_own_guards(self):
        """조립 SQL이 1차·1.5차 가드를 전부 통과한다 (조립·검증 자기모순 방지)."""
        from src.db_adapters.polestar.assembler import build_active_alarm_sql
        from src.db_adapters.polestar.validators import (
            check_active_status_literal_filter,
            check_alarm_table_allowlist,
            check_severity_label_filter,
        )
        spec = self._spec("현재 활성 심각 알람 목록")
        for engine, schema in (("postgresql", "polestar"), ("db2", "POLESTAR")):
            sql = build_active_alarm_sql(spec, db_engine=engine, db_schema=schema, limit=100)
            assert check_alarm_table_allowlist(sql) == []
            assert check_severity_label_filter(sql) == []
            assert check_active_status_literal_filter(sql) == []


class TestAlarmHistoryAssembly:
    """알람 이력 결정적 조립 (2.5차 — B0 severity_display 수렴 실패·이력 건수 요동 해소)."""

    def _spec(self, q, ptr=None):
        from src.db_adapters.polestar.assembler import recognize_active_alarm_query
        return recognize_active_alarm_query(q, parsed_time_range=ptr)

    def test_history_with_month_range(self):
        spec = self._spec("최근 3개월 심각 알람 이력 보여줘")
        assert spec is not None and spec.mode == "history"
        assert spec.severity == 3
        assert spec.month_range is not None and len(spec.month_range) == 2

    def test_history_without_period_filter(self):
        spec = self._spec("경고 이상 알람 이력을 최근 발생 순으로 100건 조회해줘")
        assert spec is not None and spec.mode == "history"
        assert spec.severity == 2 and spec.severity_op == ">="
        assert spec.month_range is None

    def test_active_plus_period_ambiguous_none(self):
        assert self._spec("현재 활성 심각 알람 최근 3개월") is None

    def test_unack_history_falls_back(self):
        assert self._spec("지난달 미확인 알람 이력") is None

    def test_month_bounds_conversion(self):
        from src.db_adapters.polestar.assembler import _month_range_to_ts_bounds
        assert _month_range_to_ts_bounds(("202601", "202603")) == (
            "2026-01-01 00:00:00", "2026-04-01 00:00:00",
        )
        # 연도 넘김: 12월 종료 → 다음 해 1월 1일 상한
        assert _month_range_to_ts_bounds(("202511", "202512")) == (
            "2025-11-01 00:00:00", "2026-01-01 00:00:00",
        )

    def test_history_sql_shape_pg(self):
        from src.db_adapters.polestar.assembler import build_alarm_history_sql
        spec = self._spec("2026년 1월부터 3월까지 심각 알람 이력")
        assert spec is not None and spec.month_range == ("202601", "202603")
        sql = build_alarm_history_sql(
            spec, db_engine="postgresql", db_schema="polestar", limit=1000
        )
        assert "polestar.cmm_alarm a" in sql
        assert "a.alarmseverity = 3" in sql
        assert "a.ctime >= TIMESTAMP '2026-01-01 00:00:00'" in sql
        assert "a.ctime < TIMESTAMP '2026-04-01 00:00:00'" in sql
        # 골드 정본(gp-012/gp-015) 골격: 자원 INNER + dtime, 부모 서버 3단 승격 LEFT
        assert "JOIN polestar.cmm_resource res ON a.resource_id = res.id" in sql
        assert "res.dtime IS NULL" in sql
        assert (
            "COALESCE(res.platform_resource_id, res.service_resource_id, res.id)"
            in sql
        )
        assert "LIMIT 1000" in sql

    def test_history_sql_shape_db2(self):
        from src.db_adapters.polestar.assembler import build_alarm_history_sql
        spec = self._spec("최근 3개월 심각 알람 이력")
        sql = build_alarm_history_sql(
            spec, db_engine="db2", db_schema="POLESTAR", limit=1000
        )
        assert "POLESTAR.cmm_alarm a" in sql
        assert "FETCH FIRST 1000 ROWS ONLY" in sql and "LIMIT" not in sql

    def test_history_aliases_match_active(self):
        """활성·이력 별칭 동일 — CSV 칼럼 통일 계약이 이력까지 확장된다."""
        import re as _re
        from src.db_adapters.polestar.assembler import (
            build_active_alarm_sql, build_alarm_history_sql,
        )
        active = build_active_alarm_sql(
            self._spec("현재 활성 심각 알람"), db_engine="postgresql",
            db_schema="polestar", limit=10,
        )
        history = build_alarm_history_sql(
            self._spec("최근 3개월 심각 알람 이력"), db_engine="postgresql",
            db_schema="polestar", limit=10,
        )
        def out_columns(sql):
            select_list = sql.split("FROM")[0].replace("SELECT", "", 1)
            names = []
            for item in select_list.split(","):
                item = item.strip()
                m = _re.search(r"AS (\w+)$", item)
                names.append(m.group(1) if m else item.split(".")[-1])
            return names

        assert out_columns(active) == out_columns(history)

    def test_history_count_sql(self):
        from src.db_adapters.polestar.assembler import build_alarm_history_sql
        spec = self._spec("지난달 심각 알람 몇 건이야")
        assert spec is not None and spec.mode == "history" and spec.count_only
        sql = build_alarm_history_sql(
            spec, db_engine="postgresql", db_schema="polestar", limit=10
        )
        assert "COUNT(*) AS alarm_count" in sql
        assert "res.dtime IS NULL" in sql

    def test_history_sql_passes_own_guards(self):
        from src.db_adapters.polestar.assembler import build_alarm_history_sql
        from src.db_adapters.polestar.validators import (
            check_active_status_literal_filter,
            check_alarm_table_allowlist,
            check_severity_label_filter,
        )
        spec = self._spec("최근 3개월 심각 알람 이력")
        for engine, schema in (("postgresql", "polestar"), ("db2", "POLESTAR")):
            sql = build_alarm_history_sql(
                spec, db_engine=engine, db_schema=schema, limit=100
            )
            assert check_alarm_table_allowlist(sql) == []
            assert check_severity_label_filter(sql) == []
            assert check_active_status_literal_filter(sql) == []


class TestAlarmAssemblyCoverageGuard:
    """커버리지 밖 신호 가드 (D-202) — 조립기가 표현 못 하는 의도는 조립하지 않는다.

    D군 실측: "서버별 … 상위 10개"가 전역 COUNT 1행으로(D-04), "CPU 임계값 초과"가
    무필터 전체 이력으로(D-05) 조립되어 조건이 침묵 드롭됐다. 이런 질의는 None을
    반환해 LLM(집계는 알람 템플릿 C-4·시맨틱 패턴 C)에 맡긴다.
    """

    def _spec(self, q):
        from src.db_adapters.polestar.assembler import recognize_active_alarm_query
        return recognize_active_alarm_query(q)

    def test_non_server_group_axis_falls_back(self):
        # 서버 축 외 집계는 여전히 LLM 폴백 (서버 축은 3·4차에서 조립 승격 — 아래 클래스)
        assert self._spec("이번달 존별 알람 건수 집계") is None
        assert self._spec("알람 유형별 발생 건수 순위") is None

    def test_unmapped_metric_filter_falls_back(self):
        # 자원 타입으로 매핑되지 않는 조건만 폴백 (매핑 유형은 4차부터 조립 — 아래 클래스)
        assert self._spec("지난달 사용률 초과 알람 이력") is None
        assert self._spec("최근 트래픽 알람 보여줘") is None
        assert self._spec("현재 활성 응답 시간 지연 알람") is None

    def test_plain_queries_still_recognized(self):
        # 가드가 정상 커버리지를 침식하지 않는다 — 기존 인식 형태 유지 재확인
        assert self._spec("현재 활성 심각 알람 목록") is not None
        assert self._spec("최근 3개월 심각 알람 이력 보여줘") is not None
        spec = self._spec("지난달 심각 알람 몇 건이야")
        assert spec is not None and spec.count_only  # 전역 건수 질의는 계속 조립 대상


class TestAlarmServerGroupAssembly:
    """서버별 알람 건수 집계 결정적 조립 (D-202 3차).

    1차에서는 서버별 집계를 LLM 폴백 대상으로 뒀으나, 3차 폐쇄망 실측에서 LLM 재생성이
    3존 모두 비수렴(시도마다 상이한 위반 — 금지 테이블·통계 조인 환각·server.Server
    WHERE 반복)했다. Known Mistakes 규약(반복 실패 형태 = 결정적 조립)대로 승격한다.
    """

    def _spec(self, q):
        from src.db_adapters.polestar.assembler import recognize_active_alarm_query
        return recognize_active_alarm_query(q)

    def test_recognize_d04_history_group(self):
        # D-04 원형·재계획 sub_query·4차 실측 문구 — 전부 결정적 조립 대상
        for q in (
            "지난 3개월 서버별 알람 발생 건수 상위 10개 서버",
            "서버별 알람 발생 건수를 집계하여 상위 10개 서버 조회",
            "최근 3개월 알람이 가장 많이 발생한 서버 상위 10개",  # 4차 실측 표현
            "지난달 알람 많이 발생한 호스트 알려줘",
        ):
            spec = self._spec(q)
            assert spec is not None and spec.group_by == "server", q
            assert not spec.count_only  # "건수"는 서버당 건수 — 전역 COUNT와 배타

    def test_recognize_active_group(self):
        spec = self._spec("현재 활성 알람 서버별 건수 보여줘")
        assert spec is not None and spec.mode == "active" and spec.group_by == "server"

    def test_group_composes_with_type_filter(self):
        # 서버별 집계 + 유형 필터 조합(D-202 4차) — 둘 다 결정적으로 표현 가능
        spec = self._spec("지난달 서버별 CPU 알람 건수")
        assert spec is not None and spec.group_by == "server"
        assert spec.resource_types == ("server.Cpus",)

    def test_unmapped_filter_still_falls_back(self):
        assert self._spec("지난달 서버별 트래픽 알람 건수") is None  # 비매핑 조건은 밖

    def test_history_group_sql_shape(self):
        from src.db_adapters.polestar.assembler import build_alarm_history_sql
        spec = self._spec("지난 3개월 서버별 경고 이상 알람 발생 건수 상위 10개 서버")
        assert spec is not None and spec.mode == "history"
        pg = build_alarm_history_sql(
            spec, db_engine="postgresql", db_schema="polestar", limit=10
        )
        assert "COUNT(*) AS alarm_count" in pg
        assert "GROUP BY COALESCE(srv.name, srv.hostname, res.name)" in pg
        assert "ORDER BY alarm_count DESC NULLS LAST" in pg
        assert "res.dtime IS NULL" in pg and "a.alarmseverity >= 2" in pg
        assert "LIMIT 10" in pg
        db2 = build_alarm_history_sql(
            spec, db_engine="db2", db_schema="POLESTAR", limit=10
        )
        assert "POLESTAR.cmm_alarm a" in db2
        assert "FETCH FIRST 10 ROWS ONLY" in db2 and "LIMIT" not in db2

    def test_active_group_sql_shape(self):
        from src.db_adapters.polestar.assembler import build_active_alarm_sql
        spec = self._spec("현재 활성 알람 서버별 건수")
        sql = build_active_alarm_sql(
            spec, db_engine="postgresql", db_schema="polestar", limit=100
        )
        assert "cmm_alarm_active a" in sql
        assert "GROUP BY COALESCE(srv.name, srv.hostname, res.name)" in sql
        assert "ORDER BY alarm_count DESC NULLS LAST" in sql
        # 타입 한정은 승격 LEFT JOIN의 ON 절 — WHERE 강등 금지 골격 유지
        assert "AND srv.resource_type = 'server.Server'" in sql

    def test_group_sql_passes_all_registered_validators(self):
        """조립 SQL이 등록 검증기 전체를 통과한다 — 조립·검증 자기모순 방지(전수)."""
        from src.db_adapters import get_adapter
        from src.db_adapters.polestar.assembler import (
            build_active_alarm_sql, build_alarm_history_sql,
        )
        checks = get_adapter("polestar", {"polestar"}).validator_checks()
        hist = self._spec("지난 3개월 서버별 심각 알람 건수 상위 10개 서버")
        act = self._spec("현재 활성 알람 서버별 건수")
        for engine, schema in (("postgresql", "polestar"), ("db2", "POLESTAR")):
            for sql in (
                build_alarm_history_sql(
                    hist, db_engine=engine, db_schema=schema, limit=10
                ),
                build_active_alarm_sql(
                    act, db_engine=engine, db_schema=schema, limit=10
                ),
            ):
                for check in checks:
                    assert check(sql) == [], f"{engine}: {check.__name__}"


class TestAlarmResourceTypeFilter:
    """알람 유형 → 자원 타입 결정적 필터 (D-202 4차 — diag_alarm_definitions.sql 실측).

    정의명(cmm_alarm_def.name)은 센터별 임의 등록이라 축으로 부적합(사용자 확인).
    res.resource_type이 3존 공통 안정 축(CPU=server.Cpus 지배적)이라 이것으로 조립한다.
    """

    def _spec(self, q):
        from src.db_adapters.polestar.assembler import recognize_active_alarm_query
        return recognize_active_alarm_query(q)

    def test_recognize_d05_shape(self):
        # D-05 원형 — "임계값"은 유형어 동반 시 의미 잉여(알람 자체가 임계 이벤트)
        spec = self._spec("이번달 CPU 임계값 초과 알람 조회")
        assert spec is not None and spec.mode == "history"
        assert spec.resource_types == ("server.Cpus",)
        assert spec.type_label == "CPU"

    def test_category_mappings(self):
        cases = {
            "지난달 메모리 알람": ("server.Memory",),
            "지난달 파일시스템 알람": ("server.FileSystem", "server.FileSystems"),
            "지난달 디스크 알람": ("server.Disks",),
            "지난달 스왑 알람": ("server.VirtualMemory",),
            "지난달 네트워크 인터페이스 알람": ("server.NetworkInterface",),
            "지난달 프로세스 알람": ("server.Process", "server.ProcessMonitor"),
        }
        for q, types in cases.items():
            spec = self._spec(q)
            assert spec is not None and spec.resource_types == types, q

    def test_history_type_sql_shape(self):
        from src.db_adapters.polestar.assembler import build_alarm_history_sql
        spec = self._spec("지난달 CPU 심각 알람 이력")
        pg = build_alarm_history_sql(
            spec, db_engine="postgresql", db_schema="polestar", limit=100
        )
        assert "res.resource_type IN ('server.Cpus')" in pg
        assert "res.dtime IS NULL" in pg and "a.alarmseverity = 3" in pg
        db2 = build_alarm_history_sql(
            spec, db_engine="db2", db_schema="POLESTAR", limit=100
        )
        assert "res.resource_type IN ('server.Cpus')" in db2
        assert "FETCH FIRST 100 ROWS ONLY" in db2

    def test_active_type_join_is_inner(self):
        """유형 필터 활성 조회는 자원 조인이 INNER(타입 한정 의도) — 무필터는 LEFT 유지."""
        from src.db_adapters.polestar.assembler import build_active_alarm_sql
        typed = build_active_alarm_sql(
            self._spec("현재 활성 CPU 알람"), db_engine="postgresql",
            db_schema="polestar", limit=100,
        )
        assert "JOIN polestar.cmm_resource res" in typed
        assert "LEFT JOIN polestar.cmm_resource res" not in typed
        assert "res.resource_type IN ('server.Cpus')" in typed
        plain = build_active_alarm_sql(
            self._spec("현재 활성 심각 알람 목록"), db_engine="postgresql",
            db_schema="polestar", limit=100,
        )
        assert "LEFT JOIN polestar.cmm_resource res" in plain

    def test_typed_count_sql_joins_resource(self):
        from src.db_adapters.polestar.assembler import build_active_alarm_sql
        spec = self._spec("현재 활성 CPU 알람 몇 건이야")
        sql = build_active_alarm_sql(
            spec, db_engine="postgresql", db_schema="polestar", limit=10
        )
        assert "COUNT(*) AS alarm_count" in sql
        assert "cmm_resource res" in sql and "res.resource_type IN" in sql

    def test_type_sql_passes_all_registered_validators(self):
        from src.db_adapters import get_adapter
        from src.db_adapters.polestar.assembler import (
            build_active_alarm_sql, build_alarm_history_sql,
        )
        checks = get_adapter("polestar", {"polestar"}).validator_checks()
        hist = self._spec("지난달 CPU 심각 알람 이력")
        act = self._spec("현재 활성 메모리 알람")
        grp = self._spec("지난달 서버별 CPU 알람 건수 상위 10개 서버")
        for engine, schema in (("postgresql", "polestar"), ("db2", "POLESTAR")):
            for sql in (
                build_alarm_history_sql(
                    hist, db_engine=engine, db_schema=schema, limit=10
                ),
                build_active_alarm_sql(
                    act, db_engine=engine, db_schema=schema, limit=10
                ),
                build_alarm_history_sql(
                    grp, db_engine=engine, db_schema=schema, limit=10
                ),
            ):
                for check in checks:
                    assert check(sql) == [], f"{engine}: {check.__name__}"


class TestEnsureRankingNullsLast:
    """순위 정렬 NULLS LAST 결정적 교정 (D-202 2차).

    D군 2차 실측(D-04 CM) — LLM이 검증기 힌트를 받고도 재시도 전부에서 NULLS LAST를
    누락해 재생성 예산 소진. 반려 대신 생성 직후 결정적 부가로 교정한다.
    """

    _RANKING_SQL = (
        "SELECT COALESCE(srv.name, srv.hostname) AS server_name, "
        "COUNT(*) AS alarm_count FROM polestar.cmm_alarm a "
        "JOIN polestar.cmm_resource res ON a.resource_id = res.id "
        "GROUP BY COALESCE(srv.name, srv.hostname) "
        "ORDER BY alarm_count DESC LIMIT 10"
    )

    def test_appends_nulls_last_and_passes_check(self):
        from src.db_adapters.polestar.validators import (
            check_ranking_order_by_nulls_last,
            ensure_ranking_nulls_last,
        )
        assert check_ranking_order_by_nulls_last(self._RANKING_SQL)  # 교정 전 반려 대상
        fixed = ensure_ranking_nulls_last(self._RANKING_SQL)
        assert "DESC NULLS LAST" in fixed
        assert check_ranking_order_by_nulls_last(fixed) == []

    def test_direct_aggregate_and_db2_fetch(self):
        from src.db_adapters.polestar.validators import ensure_ranking_nulls_last
        sql = (
            "SELECT res.name FROM POLESTAR.cmm_alarm a "
            "GROUP BY res.name ORDER BY COUNT(*) DESC FETCH FIRST 10 ROWS ONLY"
        )
        assert "COUNT(*) DESC NULLS LAST" in ensure_ranking_nulls_last(sql)

    def test_compliant_and_non_ranking_unchanged(self):
        from src.db_adapters.polestar.validators import ensure_ranking_nulls_last
        compliant = self._RANKING_SQL.replace("DESC LIMIT", "DESC NULLS LAST LIMIT")
        assert ensure_ranking_nulls_last(compliant) == compliant  # 바이트 불변
        listing = (
            "SELECT a.id FROM polestar.cmm_alarm a "
            "ORDER BY a.ctime DESC, a.id DESC LIMIT 100"
        )
        assert ensure_ranking_nulls_last(listing) == listing  # 비집계 정렬 불변

    def test_multi_key_order_by(self):
        from src.db_adapters.polestar.validators import ensure_ranking_nulls_last
        sql = self._RANKING_SQL.replace(
            "ORDER BY alarm_count DESC", "ORDER BY alarm_count DESC, server_name ASC"
        )
        fixed = ensure_ranking_nulls_last(sql)
        assert "alarm_count DESC NULLS LAST, server_name ASC" in fixed

    def test_wired_in_both_paths(self):
        """단일·멀티 양 경로 배선 고정 — 정의만 있고 소비처 없는 것 방지(비대칭 재발 방지)."""
        import importlib
        import inspect

        qg = importlib.import_module("src.nodes.query_generator")
        mde = importlib.import_module("src.nodes.multi_db_executor")
        # 단일 경로: 호출부 1곳 이상, 멀티 경로: _invoke_llm_for_sql 내 2곳(후보 선택·직접 생성)
        assert inspect.getsource(qg).count("ensure_ranking_nulls_last(") >= 1
        assert inspect.getsource(mde._invoke_llm_for_sql).count(
            "ensure_ranking_nulls_last("
        ) >= 2


class TestAlarmResourceServerTypeFilter:
    """알람 자원 WHERE server.Server 필터 검증기 (D-202 2차) — D-05 0건 근본원인."""

    # D-05 실측 SQL 골격 (CM-GP, 0건 원인)
    _D05_SQL = (
        "SELECT COALESCE(r.name, r.hostname) AS server_name, a.id AS alarm_id "
        "FROM polestar.cmm_alarm AS a "
        "JOIN polestar.cmm_alarm_def AS d ON a.definition_id = d.id "
        "JOIN polestar.cmm_resource AS r ON a.resource_id = r.id "
        "WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL "
        "AND d.name ILIKE '%CPU%' ORDER BY a.ctime DESC LIMIT 10000"
    )

    def test_flags_d05_shape(self):
        from src.db_adapters.polestar.validators import (
            check_alarm_resource_server_type_filter,
        )
        errors = check_alarm_resource_server_type_filter(self._D05_SQL)
        assert len(errors) == 1 and "자식 리소스" in errors[0]

    def test_on_clause_type_filter_passes(self):
        """결정적 조립 골격(승격 LEFT JOIN의 ON 절 타입 한정)은 대상이 아니다."""
        from src.db_adapters.polestar.assembler import (
            build_active_alarm_sql, recognize_active_alarm_query,
        )
        from src.db_adapters.polestar.validators import (
            check_alarm_resource_server_type_filter,
        )
        spec = recognize_active_alarm_query("현재 활성 심각 알람 목록")
        for engine, schema in (("postgresql", "polestar"), ("db2", "POLESTAR")):
            sql = build_active_alarm_sql(
                spec, db_engine=engine, db_schema=schema, limit=100
            )
            assert check_alarm_resource_server_type_filter(sql) == []

    def test_non_alarm_sql_passes(self):
        from src.db_adapters.polestar.validators import (
            check_alarm_resource_server_type_filter,
        )
        sql = (
            "SELECT r.name FROM polestar.cmm_resource r "
            "WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL LIMIT 100"
        )
        assert check_alarm_resource_server_type_filter(sql) == []

    def test_registered_in_adapter(self):
        from src.db_adapters import get_adapter
        adapter = get_adapter("polestar", {"polestar"})
        names = {c.__name__ for c in adapter.validator_checks()}
        assert "check_alarm_resource_server_type_filter" in names


class TestEnsureEavValueOrder:
    """LLM 경로 EAV 문자열 값 순위 정렬 교정 (plans/116 §10.3 — 메모리 상위 3대 8GB 결함).

    원본·교정 SQL을 sqlite(샌드박스 실측 값 분포)에 실제로 돌려 상위 행을 비교한다.
    """

    _PIVOT = (
        "SELECT\n"
        "    COALESCE(c.platform_resource_id, c.id) AS id,\n"
        "    MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) AS server_name,\n"
        "    MAX(CASE WHEN c.resource_type = 'server.Memory' AND cc.name = 'TotalSize'"
        " THEN cc.stringvalue_short END) AS mem_size  -- 메모리(GB\n"
        "FROM polestar.cmm_resource c\n"
        "LEFT JOIN polestar.core_config_prop cc ON c.resource_conf_id = cc.configuration_id\n"
        "WHERE c.resource_type IN ('server.Server', 'server.Memory') AND c.dtime IS NULL\n"
        "GROUP BY COALESCE(c.platform_resource_id, c.id)\n"
        "ORDER BY mem_size DESC\n"
        "LIMIT 3;"
    )

    @staticmethod
    def _db():
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("ATTACH ':memory:' AS polestar")
        conn.execute(
            "CREATE TABLE polestar.cmm_resource (id INT, name TEXT, resource_type TEXT, "
            "platform_resource_id INT, resource_conf_id INT, dtime TEXT)"
        )
        conn.execute(
            "CREATE TABLE polestar.core_config_prop "
            "(configuration_id INT, name TEXT, stringvalue_short TEXT)"
        )
        mem = {"s8a": "8.0 GB", "s8b": "8.0 GB", "s8c": "8.0 GB", "s64": "1.0 TB",
               "s64g": "64.0 GB", "s32": "512 MB", "s62": "62.1 GB", "s16": "16.0 GB"}
        for i, (name, value) in enumerate(mem.items(), start=1):
            conn.execute("INSERT INTO polestar.cmm_resource VALUES (?,?,?,?,?,NULL)",
                         (i, name, "server.Server", None, 100 + i))
            conn.execute("INSERT INTO polestar.cmm_resource VALUES (?,?,?,?,?,NULL)",
                         (50 + i, "Memory", "server.Memory", i, 200 + i))
            conn.execute("INSERT INTO polestar.core_config_prop VALUES (?,?,?)",
                         (200 + i, "TotalSize", value))
        return conn

    def _top_names(self, sql):
        return [r[1] for r in self._db().execute(sql.rstrip().rstrip(";"))]

    def test_pivot_alias_order_becomes_capacity_order(self):
        from src.db_adapters.polestar.validators import ensure_eav_value_order
        # 결함 재현: 문자열 순 1위가 8GB
        assert self._top_names(self._PIVOT)[0] in {"s8a", "s8b", "s8c"}
        fixed = ensure_eav_value_order(self._PIVOT)
        assert set(self._top_names(fixed)) == {"s64", "s64g", "s62"}
        assert fixed.startswith(self._PIVOT[: self._PIVOT.index("ORDER BY")])  # SELECT 표시값 불변
        assert "DESC NULLS LAST" in fixed and fixed.rstrip().endswith("LIMIT 3;")

    def test_join_alias_value_order(self):
        from src.db_adapters.polestar.validators import ensure_eav_value_order
        sql = (
            "SELECT r.name, cc_mem.stringvalue_short AS mem FROM polestar.cmm_resource r\n"
            "JOIN polestar.cmm_resource m ON m.platform_resource_id = r.id\n"
            "LEFT JOIN polestar.core_config_prop cc_mem ON m.resource_conf_id = "
            "cc_mem.configuration_id AND cc_mem.name = 'TotalSize'\n"
            "WHERE r.resource_type = 'server.Server'\n"
            "ORDER BY \"mem\" DESC, r.name LIMIT 3"
        )
        fixed = ensure_eav_value_order(sql)
        rows = self._db().execute(fixed).fetchall()
        assert {r[0] for r in rows} == {"s64", "s64g", "s62"}
        assert ", r.name LIMIT 3" in fixed

    def test_cast_string_and_distinct_are_unchanged(self):
        from src.db_adapters.polestar.validators import ensure_eav_value_order
        casted = self._PIVOT.replace(
            "THEN cc.stringvalue_short END", "THEN CAST(cc.stringvalue_short AS NUMERIC) END")
        assert ensure_eav_value_order(casted) == casted
        os_sql = self._PIVOT.replace("'TotalSize'", "'OSType'")
        assert ensure_eav_value_order(os_sql) == os_sql
        distinct = self._PIVOT.replace("SELECT\n", "SELECT DISTINCT\n", 1)
        assert ensure_eav_value_order(distinct) == distinct

    def test_window_order_by_is_not_rewritten(self):
        from src.db_adapters.polestar.validators import ensure_eav_value_order
        sql = self._PIVOT.replace(
            "ORDER BY mem_size DESC",
            "ORDER BY id",
        ).replace(
            "AS id,", "AS id, ROW_NUMBER() OVER (ORDER BY mem_size) AS rn,", 1)
        assert ensure_eav_value_order(sql) == sql


class TestAdapterOwnershipStartupWarning:
    """활성 DB가 어댑터 제품군인데 담당 ID 설정(POLESTAR_DB_IDS)이 비면 기동 WARNING 1줄.

    plans/116 §10.3 — 사례 녹화 .env에 POLESTAR_DB_IDS가 없어 폴스타 검증기·결정적 조립·
    시맨틱 컴파일이 전부 꺼진 채 돌았고, 로그에는 아무 흔적이 없었다. 동작은 바꾸지 않는다.
    """

    def _config(self, active: str, owned: str):
        from types import SimpleNamespace
        return SimpleNamespace(
            multi_db=SimpleNamespace(get_active_db_ids=lambda: [
                x for x in active.split(",") if x]),
            get_polestar_db_ids=lambda: {x for x in owned.split(",") if x},
        )

    def test_warns_when_adapter_family_db_active_but_unowned(self, caplog):
        import logging
        from src.db_adapters import log_adapter_ownership_startup

        with caplog.at_level(logging.WARNING, logger="src.db_adapters"):
            unowned = log_adapter_ownership_startup(self._config("polestar,itam", ""))
        assert unowned == {"polestar": ["polestar"]}
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "POLESTAR_DB_IDS" in warnings[0].getMessage()

    def test_silent_when_owned_or_no_adapter_family(self, caplog):
        import logging
        from src.db_adapters import log_adapter_ownership_startup

        with caplog.at_level(logging.WARNING, logger="src.db_adapters"):
            assert log_adapter_ownership_startup(self._config("polestar", "polestar")) == {}
            assert log_adapter_ownership_startup(self._config("cloud_portal", "")) == {}
            assert log_adapter_ownership_startup(self._config("", "")) == {}
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_wired_at_graph_build(self):
        import inspect
        import src.graph as graph_module

        assert "log_adapter_ownership_startup(" in inspect.getsource(graph_module.build_graph)
