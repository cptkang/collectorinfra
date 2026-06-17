"""query_generator 노드 테스트.

SQL 생성, 재시도 로직, 프롬프트 구성을 검증한다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import SystemMessage


from src.nodes.query_generator import (
    _build_system_prompt,
    _build_user_prompt,
    _extract_sql_from_response,
    _fix_known_attribute_typos,
    _format_schema_for_prompt,
    _try_build_deterministic_sql,
    query_generator,
)
from src.state import create_initial_state


def _polestar_structure_meta() -> dict:
    """결정적 빌더 테스트용 폴스타 structure_meta(메트릭+EAV 메타데이터 포함)."""
    return {
        "patterns": [
            {
                "type": "eav",
                "attribute_column": "name",
                "value_joins": [
                    {"eav_attribute": "Hostname", "entity_column": "hostname"},
                    {"eav_attribute": "IPaddress", "entity_column": "ipaddress"},
                ],
                "known_attributes_detail": [
                    {"name": "OSType", "description": "OS [resource_type: server.Server]"},
                    {"name": "OSVerson", "description": "OS버전 [resource_type: server.Server]"},
                    {"name": "LOGICALCORE", "description": "코어 [resource_type: server.Cpus]"},
                    {"name": "TotalSize",
                     "description": "메모리 [resource_type: server.Memory] / 디스크 [resource_type: server.Disks]"},
                ],
            }
        ],
        "metric_patterns": {
            "stat_tables": {"month": {"table": "cmm_metric_stat_m", "stat_date_format": "YYYYMM"}},
            "default_resolution": "month",
            "value_columns": {"avg": "avg_val", "max": "max_val", "min": "min_val"},
            "aggregations": {"avg": ["평균"], "max": ["최고", "최대"], "min": ["최소"]},
            "metrics": [
                {"name": "cpu_utilization", "resource_type": "server.Cpus",
                 "definition_name": "Utilization", "domain_terms": ["CPU"], "synonyms": []},
                {"name": "memory_utilization", "resource_type": "server.Memory",
                 "definition_name": "Utilization", "domain_terms": ["메모리"], "synonyms": []},
            ],
        },
    }


def _template_fill_state() -> dict:
    return {
        "user_query": "여의도 폴스타 양식 채워줘 지난 1개월",
        "retry_count": 0,
        "error_message": None,
        "is_multi_db": False,
        "schema_info": {"_structure_meta": _polestar_structure_meta()},
        "parsed_requirements": {
            "output_format": "xlsx",
            "query_targets": ["서버", "CPU", "메모리"],
            "filter_conditions": [],
            "time_range": "지난 1개월",
        },
        "column_mapping": {
            "서버 이름": "cmm_resource.name",
            "IP주소": "EAV:IPaddress",
            "CPU 코어 수": "EAV:LOGICALCORE",
            "메모리 용량": "EAV:TotalSize",
            "OS 버전": "EAV:OSVerson",
            "CPU 평균": None,
            "메모리 최고": None,
        },
    }


class TestDeterministicBuilderGating:
    """D-038 Phase 2 — query_generator의 결정적 빌더 게이팅."""

    @pytest.mark.asyncio
    async def test_deterministic_path_skips_llm(self):
        app_config = MagicMock()
        app_config.query.default_limit = 1000
        llm = AsyncMock()

        result = await query_generator(
            _template_fill_state(), llm=llm, app_config=app_config
        )

        # LLM 미호출(결정적 경로)
        llm.ainvoke.assert_not_called()
        sql = result["generated_sql"]
        assert "LEFT JOIN polestar.cmm_metric_stat_m s" in sql
        assert "cc.name = 'OSVerson'" in sql
        assert "THEN res.ipaddress END" in sql  # IP 직접컬럼
        # 메트릭 필드가 column_mapping에 alias로 채워짐
        assert result["column_mapping"]["CPU 평균"] == "metric_cpu_utilization_avg"

    def test_skip_when_not_template(self):
        state = _template_fill_state()
        state["parsed_requirements"]["output_format"] = "text"
        assert _try_build_deterministic_sql(state, 1000) is None

    def test_skip_when_filter_present(self):
        state = _template_fill_state()
        state["parsed_requirements"]["filter_conditions"] = [
            {"field": "hostname", "op": "=", "value": "x"}
        ]
        assert _try_build_deterministic_sql(state, 1000) is None

    def test_skip_when_multi_db(self):
        state = _template_fill_state()
        state["is_multi_db"] = True
        assert _try_build_deterministic_sql(state, 1000) is None

    def test_skip_when_no_metric_patterns(self):
        state = _template_fill_state()
        state["schema_info"]["_structure_meta"].pop("metric_patterns")
        assert _try_build_deterministic_sql(state, 1000) is None

    def test_skip_when_no_column_mapping(self):
        state = _template_fill_state()
        state["column_mapping"] = {}
        assert _try_build_deterministic_sql(state, 1000) is None

    def test_kill_switch_disables_builder(self):
        """QUERY_ENABLE_DETERMINISTIC_REPORT_SQL=false → 항상 None(LLM 폴백)."""
        state = _template_fill_state()
        app_config = MagicMock()
        app_config.query.enable_deterministic_report_sql = False
        assert _try_build_deterministic_sql(state, 1000, app_config) is None

    def test_kill_switch_enabled_builds(self):
        state = _template_fill_state()
        app_config = MagicMock()
        app_config.query.enable_deterministic_report_sql = True
        assert _try_build_deterministic_sql(state, 1000, app_config) is not None


class TestFixKnownAttributeTypos:
    """알려진 EAV 속성명 오탈자 결정적 치환 검증 (D-037)."""

    def test_osversion_literal_corrected(self):
        """따옴표 리터럴 'OSVersion'을 실제 DB 값 'OSVerson'으로 되돌린다."""
        sql = "... AND cc.name = 'OSVersion' THEN cc.stringvalue_short END"
        assert "'OSVerson'" in _fix_known_attribute_typos(sql)
        assert "'OSVersion'" not in _fix_known_attribute_typos(sql)

    def test_case_insensitive_literal(self):
        """대소문자 무관하게 리터럴을 보정한다."""
        sql = "WHEN cc.name='osversion' THEN x END"
        assert "'OSVerson'" in _fix_known_attribute_typos(sql)

    def test_alias_not_touched(self):
        """따옴표 없는 alias/식별자(os_version, osversion)는 건드리지 않는다."""
        sql = "SELECT cc.stringvalue AS osversion, x AS os_version FROM t"
        assert _fix_known_attribute_typos(sql) == sql

    def test_already_correct_unchanged(self):
        """이미 올바른 'OSVerson'은 그대로 유지한다."""
        sql = "AND cc.name = 'OSVerson'"
        assert _fix_known_attribute_typos(sql) == sql

    def test_empty_sql(self):
        """빈 SQL은 그대로 반환한다."""
        assert _fix_known_attribute_typos("") == ""


class TestExtractSqlFromResponse:
    """LLM 응답에서 SQL 추출 검증."""

    def test_sql_code_block(self):
        """```sql ... ``` 패턴에서 SQL을 추출한다."""
        content = "```sql\nSELECT * FROM servers LIMIT 10;\n```"
        result = _extract_sql_from_response(content)
        assert result.startswith("SELECT")
        assert "LIMIT 10" in result

    def test_generic_code_block_with_select(self):
        """``` ... ``` 패턴(SELECT 시작)에서 SQL을 추출한다."""
        content = "```\nSELECT hostname FROM servers LIMIT 5;\n```"
        result = _extract_sql_from_response(content)
        assert "hostname" in result

    def test_plain_select(self):
        """코드 블록 없는 SELECT 문을 추출한다."""
        content = "Here is the query: SELECT id FROM servers LIMIT 10;"
        result = _extract_sql_from_response(content)
        assert "SELECT" in result

    def test_fallback_returns_full_content(self):
        """SQL을 추출할 수 없으면 전체 내용을 반환한다."""
        content = "I cannot generate SQL"
        result = _extract_sql_from_response(content)
        assert result == "I cannot generate SQL"


class TestQueryGeneratorNode:
    """query_generator 노드 전체 동작 검증."""

    @pytest.mark.asyncio
    async def test_basic_sql_generation(self, sample_state):
        """기본 SQL 생성이 동작한다."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT s.hostname, c.usage_pct FROM servers s JOIN cpu_metrics c ON s.id = c.server_id WHERE c.usage_pct >= 80 LIMIT 100;\n```"
        )
        mock_config = MagicMock()
        mock_config.query.default_limit = 1000

        result = await query_generator(sample_state, llm=mock_llm, app_config=mock_config)

        assert "generated_sql" in result
        assert "SELECT" in result["generated_sql"]
        assert result["error_message"] is None
        assert result["current_node"] == "query_generator"

    @pytest.mark.asyncio
    async def test_retry_increments_count(self, sample_state):
        """재시도 시 retry_count가 증가한다."""
        sample_state["error_message"] = "이전 SQL 검증 실패"
        sample_state["retry_count"] = 1
        sample_state["generated_sql"] = "SELECT bad_sql"

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT hostname FROM servers LIMIT 10;\n```"
        )
        mock_config = MagicMock()
        mock_config.query.default_limit = 1000

        result = await query_generator(sample_state, llm=mock_llm, app_config=mock_config)

        assert result["retry_count"] == 2  # 1 -> 2
        assert result["error_message"] is None  # 에러 초기화

    @pytest.mark.asyncio
    async def test_first_call_does_not_increment(self, sample_state):
        """첫 호출(에러 없음)에서는 retry_count를 증가시키지 않는다."""
        sample_state["error_message"] = None
        sample_state["retry_count"] = 0

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT * FROM servers LIMIT 10;\n```"
        )
        mock_config = MagicMock()
        mock_config.query.default_limit = 1000

        result = await query_generator(sample_state, llm=mock_llm, app_config=mock_config)

        assert result["retry_count"] == 0

    @pytest.mark.asyncio
    async def test_query_generator_is_all_query_sets_large_limit(self, sample_state):
        """'모든', '전체', '모두' 키워드가 들어간 경우 default_limit이 100000으로 설정된다."""
        sample_state["user_query"] = "모든 서버들의 정보를 조회하시오"
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT * FROM servers LIMIT 100000;\n```"
        )
        mock_config = MagicMock()
        mock_config.query.default_limit = 1000
        mock_config.get_polestar_db_ids.return_value = set()

        result = await query_generator(sample_state, llm=mock_llm, app_config=mock_config)

        # ainvoke가 호출되었을 때, 첫 번째 메시지(SystemMessage)의 내용에 100000이 포함되어 있는지 확인
        call_args = mock_llm.ainvoke.call_args[0][0]
        system_message = next(msg for msg in call_args if isinstance(msg, SystemMessage))
        assert "100000" in system_message.content
        assert "LIMIT 1000\n" not in system_message.content
        assert "LIMIT 1000;" not in system_message.content
        assert "LIMIT 1000 " not in system_message.content




class TestBuildUserPrompt:
    """사용자 프롬프트 구성 검증."""

    def test_includes_original_query(self):
        """원본 질의가 프롬프트에 포함된다."""
        prompt = _build_user_prompt(
            parsed_requirements={"original_query": "CPU 정보 알려줘"},
            template_structure=None,
            error_message=None,
            previous_sql=None,
        )
        assert "CPU 정보 알려줘" in prompt

    def test_includes_retry_context(self):
        """재시도 시 이전 에러와 SQL이 프롬프트에 포함된다."""
        prompt = _build_user_prompt(
            parsed_requirements={"original_query": "test"},
            template_structure=None,
            error_message="존재하지 않는 테이블 참조",
            previous_sql="SELECT * FROM bad_table",
        )
        assert "존재하지 않는 테이블" in prompt
        assert "bad_table" in prompt

    def test_includes_template_structure(self):
        """양식 구조가 프롬프트에 포함된다."""
        prompt = _build_user_prompt(
            parsed_requirements={"original_query": "test"},
            template_structure={"sheets": [{"headers": ["서버명", "IP"]}]},
            error_message=None,
            previous_sql=None,
        )
        assert "양식" in prompt
        assert "서버명" in prompt

    def test_includes_server_name_pivot_hint(self):
        """cmm_resource.name 매핑 시 서버 이름 피벗 특별 지침이 포함된다."""
        # 1. cmm_resource.name 매핑이 포함된 경우
        column_mapping = {
            "서버 이름": "polestar.cmm_resource.name",
            "IP": "cmm_resource.ipaddress"
        }
        prompt = _build_user_prompt(
            parsed_requirements={"original_query": "test"},
            template_structure=None,
            error_message=None,
            previous_sql=None,
            column_mapping=column_mapping
        )
        assert "특별 지침 (서버 이름 조회)" in prompt
        assert "MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END)" in prompt

        # 2. cmm_resource.name 매핑이 포함되지 않은 경우
        column_mapping_no_name = {
            "IP": "cmm_resource.ipaddress"
        }
        prompt_no_name = _build_user_prompt(
            parsed_requirements={"original_query": "test"},
            template_structure=None,
            error_message=None,
            previous_sql=None,
            column_mapping=column_mapping_no_name
        )
        assert "특별 지침 (서버 이름 조회)" not in prompt_no_name


class TestFormatSchemaForPrompt:
    """스키마 포맷팅 검증."""

    def test_empty_schema(self):
        """빈 스키마도 에러 없이 처리한다."""
        result = _format_schema_for_prompt({})
        assert isinstance(result, str)

    def test_schema_includes_table_info(self, sample_schema_info):
        """스키마 텍스트에 테이블 정보가 포함된다."""
        result = _format_schema_for_prompt(sample_schema_info)
        assert "servers" in result
        assert "hostname" in result
        assert "cpu_metrics" in result

    def test_schema_includes_fk_info(self, sample_schema_info):
        """FK 관계가 포함된다."""
        result = _format_schema_for_prompt(sample_schema_info)
        assert "cpu_metrics.server_id" in result
        assert "servers.id" in result
