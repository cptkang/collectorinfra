"""polestar_tools.py 테스트 (Plan 66 Wave 2-A · sre-agent/04 §9).

고수준 도구의 고정 SQL 조립(방언 분기)·이스케이프 계약·프로세스 마스킹·랭킹을
DB 연결 없이 단위 테스트한다. Docker PG 실연결 end-to-end는 별도(옵트인)로 gate한다.
"""

import os

import pytest

try:
    from mcp_server import polestar_tools as pt
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp 패키지가 설치되지 않음")


# =====================================================================
# 이스케이프 계약 (§6-2)
# =====================================================================


class TestSqlLiteral:
    """_sql_literal 이스케이프 계약 테스트."""

    def test_wraps_in_quotes(self):
        """값을 작은따옴표로 감싼다."""
        assert pt._sql_literal("web01") == "'web01'"

    def test_doubles_single_quote(self):
        """작은따옴표를 이중화한다(인젝션 방지)."""
        assert pt._sql_literal("a'b") == "'a''b'"

    def test_injection_neutralized(self):
        """따옴표 탈출 인젝션 시도가 리터럴 안에 갇힌다."""
        assert pt._sql_literal("x' OR '1'='1") == "'x'' OR ''1''=''1'"

    def test_strips_null_byte(self):
        """널바이트를 제거한다."""
        assert pt._sql_literal("a\x00b") == "'ab'"

    def test_non_str_coerced(self):
        """문자열이 아닌 값도 문자열로 변환해 이스케이프한다."""
        assert pt._sql_literal(123) == "'123'"


class TestIdLiteral:
    """_id_literal 테스트(숫자는 bare, 그 외는 이스케이프)."""

    def test_digits_bare(self):
        """순수 숫자는 따옴표 없이 반환한다."""
        assert pt._id_literal("12345") == "12345"

    def test_non_digits_quoted(self):
        """숫자가 아니면 이스케이프해 따옴표로 감싼다."""
        assert pt._id_literal("1; DROP TABLE t") == "'1; DROP TABLE t'"

    def test_injection_neutralized(self):
        """따옴표 인젝션이 리터럴에 갇힌다."""
        assert pt._id_literal("1' OR '1'='1") == "'1'' OR ''1''=''1'"


# =====================================================================
# 방언 분기 유틸
# =====================================================================


class TestDialectHelpers:
    """스키마 한정·행 제한 방언 유틸 테스트."""

    def test_schema_pg_lowercase(self):
        """PostgreSQL은 소문자 polestar 스키마로 한정한다."""
        assert pt._q(False, "cmm_alarm") == "polestar.cmm_alarm"

    def test_schema_db2_uppercase(self):
        """DB2는 대문자 POLESTAR 스키마로 한정한다."""
        assert pt._q(True, "cmm_alarm") == "POLESTAR.cmm_alarm"

    def test_row_limit_pg(self):
        """PostgreSQL은 LIMIT을 사용한다."""
        assert pt._row_limit(False, 50) == "LIMIT 50"

    def test_row_limit_db2(self):
        """DB2는 FETCH FIRST ... ROWS ONLY를 사용한다."""
        assert pt._row_limit(True, 50) == "FETCH FIRST 50 ROWS ONLY"

    def test_time_floor_pg(self):
        """PostgreSQL은 INTERVAL로 시간 창을 표현한다."""
        assert pt._time_floor(False, 24) == "NOW() - INTERVAL '24 hours'"

    def test_time_floor_db2(self):
        """DB2는 CURRENT TIMESTAMP - n HOURS로 시간 창을 표현한다."""
        assert pt._time_floor(True, 24) == "CURRENT TIMESTAMP - 24 HOURS"


# =====================================================================
# alarm_history 방언 SQL
# =====================================================================


class TestAlarmHistorySql:
    """build_alarm_history_sql 방언·조인·필터 테스트."""

    def test_pg_schema_and_limit(self):
        """PG는 polestar. 스키마 한정 + LIMIT을 명시한다."""
        sql = pt.build_alarm_history_sql(False, "web01", "CPU", 24, None, 100)
        assert "polestar.cmm_alarm" in sql
        assert "polestar.cmm_alarm_def" in sql
        assert "polestar.cmm_resource" in sql
        assert "LIMIT 100" in sql

    def test_db2_schema_and_fetch(self):
        """DB2는 POLESTAR. 스키마 한정 + FETCH FIRST를 명시한다."""
        sql = pt.build_alarm_history_sql(True, "web01", "CPU", 24, None, 100)
        assert "POLESTAR.cmm_alarm" in sql
        assert "FETCH FIRST 100 ROWS ONLY" in sql

    def test_coalesce_join_and_no_resource_conf(self):
        """PLATFORM_RESOURCE_ID COALESCE 조인을 쓰고 RESOURCE_CONF_ID 조인은 없다(D-022)."""
        sql = pt.build_alarm_history_sql(False, "web01", "CPU", 24, None, 100)
        assert "COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)" in sql
        assert "resource_conf_id" not in sql.lower()

    def test_severity_includes_zero(self):
        """해소(0) 포함 심각도 필터를 사용한다(D-030)."""
        sql = pt.build_alarm_history_sql(False, "web01", "CPU", 24, None, 100)
        assert "CA.ALARMSEVERITY IN (0, 1, 2, 3)" in sql

    def test_server_name_escaped(self):
        """server_name이 이스케이프되어 보간된다."""
        sql = pt.build_alarm_history_sql(False, "a'b", "CPU", 24, None, 100)
        assert "SVR.NAME = 'a''b'" in sql

    def test_exclude_alarm_id_optional(self):
        """exclude_alarm_id가 있으면 제외 절을 추가하고, 없으면 넣지 않는다."""
        with_ex = pt.build_alarm_history_sql(False, "web01", "CPU", 24, "999", 100)
        without_ex = pt.build_alarm_history_sql(False, "web01", "CPU", 24, None, 100)
        assert "CA.ID <> 999" in with_ex
        assert "CA.ID <>" not in without_ex


# =====================================================================
# metric_trend 방언 SQL + kind/granularity 매핑
# =====================================================================


class TestMetricTrendSql:
    """build_metric_trend_sql 매핑·방언 테스트."""

    @pytest.mark.parametrize(
        "kind,resource_type,definition",
        [
            ("cpu", "server.Cpus", "Utilization"),
            ("memory", "server.Memory", "Utilization"),
            ("filesystem", "server.FileSystems", "Utilization"),
            ("disk_io", "server.Disks", "MaxIORate"),
        ],
    )
    def test_kind_mapping(self, kind, resource_type, definition):
        """kind가 (resource_type, definition_name) 상수로 매핑된다."""
        sql = pt.build_metric_trend_sql(False, "web01", kind, "h", 24)
        assert f"child.resource_type = '{resource_type}'" in sql
        assert f"s.definition_name = '{definition}'" in sql

    @pytest.mark.parametrize(
        "gran,table",
        [("h", "cmm_metric_stat_h"), ("d", "cmm_metric_stat_d"), ("m", "cmm_metric_stat_m")],
    )
    def test_granularity_table(self, gran, table):
        """granularity가 통계 테이블로 매핑된다."""
        sql = pt.build_metric_trend_sql(False, "web01", "cpu", gran, 24)
        assert f"polestar.{table}" in sql

    def test_invalid_kind_raises(self):
        """지원하지 않는 kind는 ValueError."""
        with pytest.raises(ValueError, match="지원하지 않는 kind"):
            pt.build_metric_trend_sql(False, "web01", "network", "h", 24)

    def test_invalid_granularity_raises(self):
        """지원하지 않는 granularity는 ValueError."""
        with pytest.raises(ValueError, match="지원하지 않는 granularity"):
            pt.build_metric_trend_sql(False, "web01", "cpu", "y", 24)

    def test_db2_dialect(self):
        """DB2는 POLESTAR. + FETCH FIRST."""
        sql = pt.build_metric_trend_sql(True, "web01", "cpu", "h", 12)
        assert "POLESTAR.cmm_metric_stat_h" in sql
        assert "FETCH FIRST 12 ROWS ONLY" in sql

    def test_no_numeric_cast(self):
        """집계를 쓰지 않으므로 ::numeric 방언 함정을 피한다."""
        sql = pt.build_metric_trend_sql(False, "web01", "cpu", "h", 24)
        assert "::numeric" not in sql


# =====================================================================
# resource_status / topology / os_config / condition_log / change_history
# =====================================================================


class TestResourceStatusSql:
    """build_resource_status_sql 테스트."""

    def test_selects_status_importance_maintenance(self):
        """가용 상태·중요도·유지보수 컬럼을 선택한다."""
        sql = pt.build_resource_status_sql(False, "web01", 100)
        assert "CR.AVAIL_STATUS" in sql
        assert "CR.IMPORTANCE_ID" in sql
        assert "CR.IS_MAINTENANCE" in sql
        assert "COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)" in sql
        assert "LIMIT 100" in sql

    def test_no_resource_conf_join(self):
        """RESOURCE_CONF_ID 조인이 없다(D-022)."""
        sql = pt.build_resource_status_sql(False, "web01", 100)
        assert "resource_conf_id" not in sql.lower()


class TestTopologySql:
    """build_topology_sql 방언(PG 다홉 / DB2 1홉) 테스트."""

    def test_pg_recursive_with_hop_bound(self):
        """PG는 WITH RECURSIVE로 max_hops까지 조상/자손을 탐색한다."""
        sql = pt.build_topology_sql(False, "web01", 3, 50)
        assert "WITH RECURSIVE" in sql
        assert "up.hop < 3" in sql
        assert "down.hop < 3" in sql
        assert "'ancestor'" in sql and "'descendant'" in sql
        assert "LIMIT 50" in sql

    def test_db2_one_hop_fallback(self):
        """DB2(b0)는 재귀 없이 1홉 폴백(§5)."""
        sql = pt.build_topology_sql(True, "web01", 3, 50)
        assert "WITH RECURSIVE" not in sql
        assert "POLESTAR.cmm_resource" in sql
        assert "FETCH FIRST 50 ROWS ONLY" in sql

    def test_avail_depend_columns(self):
        """AVAIL_DEPEND_RESOURCE_ID(_2)로 부모/자식을 잇는다."""
        sql = pt.build_topology_sql(False, "web01", 2, 50)
        assert "AVAIL_DEPEND_RESOURCE_ID" in sql
        assert "AVAIL_DEPEND_RESOURCE_ID_2" in sql


class TestOsConfigSql:
    """build_os_config_sql hostname 브릿지(D-022) 테스트."""

    def test_hostname_bridge_anchor(self):
        """NAME='Hostname' 속성으로 앵커하고 configuration_id로 자기조인한다."""
        sql = pt.build_os_config_sql(False, "host-a", 100)
        assert "p_host.name = 'Hostname'" in sql
        assert "p_host.stringvalue_short = 'host-a'" in sql
        assert "p.configuration_id = p_host.configuration_id" in sql

    def test_no_resource_conf_id_no_lookup(self):
        """RESOURCE_CONF_ID 조인·cmm_vendor/cmm_os lookup을 쓰지 않는다(D-022/D-028)."""
        sql = pt.build_os_config_sql(False, "host-a", 100).lower()
        assert "resource_conf_id" not in sql
        assert "cmm_vendor" not in sql
        assert "cmm_os" not in sql

    def test_db2_dialect(self):
        """DB2는 POLESTAR. + FETCH FIRST."""
        sql = pt.build_os_config_sql(True, "host-a", 100)
        assert "POLESTAR.core_config_prop" in sql
        assert "FETCH FIRST 100 ROWS ONLY" in sql


class TestConditionLogSql:
    """build_condition_log_sql 테스트."""

    def test_selects_conditionlogtext(self):
        """CONDITIONLOGTEXT를 선택하고 alarm_id로 필터한다."""
        sql = pt.build_condition_log_sql(False, "555")
        assert "CA.CONDITIONLOGTEXT" in sql
        assert "CA.ID = 555" in sql
        assert "LIMIT 1" in sql

    def test_db2_fetch_first(self):
        """DB2는 FETCH FIRST 1 ROW."""
        sql = pt.build_condition_log_sql(True, "555")
        assert "FETCH FIRST 1 ROWS ONLY" in sql


class TestChangeHistorySql:
    """build_change_history_sql(PG 전용) 테스트."""

    def test_pg_schema_and_scope(self):
        """PG 스키마 한정 + 서버 스코프 조인 + event_time 창을 사용한다."""
        sql = pt.build_change_history_sql("web01", 1700000000, 200)
        assert "polestar.cmm_resource_lifecycle_history" in sql
        assert "svr.name = 'web01'" in sql
        assert "h.event_time >= 1700000000" in sql
        assert "LIMIT 200" in sql
        assert "resource_conf_id" not in sql.lower()


# =====================================================================
# 프로세스 마스킹 · 랭킹 (§6-3)
# =====================================================================


class TestProcessMasking:
    """_mask_process_args 마스킹 테스트."""

    def test_mask_password_kv(self):
        """password 키의 값을 마스킹하고 키는 보존한다."""
        out = pt._mask_process_args("java --password=secret123 -Dx")
        assert "secret123" not in out
        assert "--password=***" in out

    def test_mask_token_colon(self):
        """token: 값도 마스킹한다."""
        out = pt._mask_process_args("app token: abc.def.ghi")
        assert "abc.def.ghi" not in out
        assert "***" in out

    def test_mask_connection_string_password(self):
        """접속 문자열의 비밀번호 부분만 마스킹한다."""
        out = pt._mask_process_args("psql postgresql://user:pass@db:5432/x")
        assert "pass@" not in out
        assert "user:***@" in out

    def test_mask_various_keys(self):
        """secret/api_key/access_key/credential 값을 마스킹한다."""
        for key in ("secret", "api_key", "access_key", "credential", "pwd"):
            out = pt._mask_process_args(f"--{key}=topsecretval")
            assert "topsecretval" not in out, key

    def test_truncates_long_args(self):
        """지나치게 긴 인자는 절단한다."""
        out = pt._mask_process_args("x" * 300)
        assert len(out) <= 121
        assert out.endswith("…")

    def test_empty_returns_empty(self):
        """빈 인자는 빈 문자열."""
        assert pt._mask_process_args("") == ""


class TestProcessRanking:
    """_rank_processes 정렬·선별·마스킹 테스트."""

    def test_cpu_sort_desc(self):
        """cpu 정렬은 p100cpu 내림차순."""
        raw = [{"name": "a", "p100cpu": 10}, {"name": "b", "p100cpu": 90}]
        top, total = pt._rank_processes(raw, "cpu", 2)
        assert [p["name"] for p in top] == ["b", "a"]
        assert total == 2

    def test_cpu_fallback_to_pcpu(self):
        """p100cpu가 없으면 pcpu로 폴백 정렬한다."""
        raw = [{"name": "a", "pcpu": 5}, {"name": "b", "pcpu": 50}]
        top, _ = pt._rank_processes(raw, "cpu", 2)
        assert top[0]["name"] == "b"

    def test_mem_sort_desc(self):
        """mem 정렬은 pmem 내림차순."""
        raw = [{"name": "a", "pmem": 80}, {"name": "b", "pmem": 20}]
        top, _ = pt._rank_processes(raw, "mem", 2)
        assert top[0]["name"] == "a"

    def test_top_n_limits(self):
        """top_n으로 상위 N만 반환한다."""
        raw = [{"name": str(i), "p100cpu": i} for i in range(10)]
        top, total = pt._rank_processes(raw, "cpu", 3)
        assert len(top) == 3
        assert total == 10

    def test_args_masked_in_result(self):
        """반환 프로세스의 args가 마스킹된다."""
        raw = [{"name": "a", "p100cpu": 1, "args": "svc --password=hunter2"}]
        top, _ = pt._rank_processes(raw, "cpu", 1)
        assert "hunter2" not in top[0]["args"]
        assert "***" in top[0]["args"]


# =====================================================================
# 반환 계약 (§4.2)
# =====================================================================


class TestReturnContract:
    """_ok / _err 반환 계약 테스트."""

    def test_ok_has_required_keys(self):
        """정상 반환은 rows/row_count/queried_at/source_kind를 포함한다."""
        import json

        out = json.loads(pt._ok([{"a": 1}], "polestar_cm_gp", "postgresql"))
        assert out["rows"] == [{"a": 1}]
        assert out["row_count"] == 1
        assert "queried_at" in out
        assert out["source_kind"] == "polestar_db"
        assert out["source"] == "polestar_cm_gp"
        assert out["engine"] == "postgresql"

    def test_err_shape(self):
        """오류 반환은 {error} 형태."""
        import json

        out = json.loads(pt._err("boom"))
        assert out == {"error": "boom"}


# =====================================================================
# Docker PG 통합 (옵트인 — 미기동 시 skip, 사유 명시)
# =====================================================================


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_IT") != "1",
    reason="Docker PG 미기동 — RUN_DOCKER_IT=1로 옵트인 시에만 실행(폴스타 스키마 서브셋 픽스처 필요)",
)
class TestDockerIntegration:
    """로컬 Docker PG 픽스처 대상 고수준 도구 end-to-end(옵트인)."""

    def test_placeholder(self):
        """Docker 픽스처 기동 후 실연결 검증 지점(현재 미기동으로 기본 skip)."""
        pytest.skip("Docker PG 픽스처 기동 필요 — M-D 실 DB 런타임 검증에서 다룸")
