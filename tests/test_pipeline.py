"""파이프라인 프로세스 흐름 테스트.

에이전트 파이프라인의 실제 프로세스 흐름을 순서대로 검증한다:

  input_parser → schema_analyzer → query_generator → query_validator
       → query_executor → result_organizer → output_generator

각 테스트 클래스는 파이프라인 흐름 시나리오 단위로 구성한다:
  1. 정상 흐름 (Happy Path): 7단계 전체 순차 실행
  2. 검증 실패 재시도 흐름: validator 실패 → generator 회귀 → 재검증
  3. 실행 에러 재시도 흐름: executor 에러 → generator 회귀 → 재실행
  4. 최대 재시도 초과 흐름: 3회 실패 → error_response 종료
  5. 빈 결과 흐름: 데이터 0건 → 빈 결과 응답
  6. 보안 차단 흐름: DML/인젝션 차단, 민감 데이터 마스킹
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import (
    AppConfig,
    DBHubConfig,
    LLMConfig,
    QueryConfig,
    SecurityConfig,
    ServerConfig,
)
from src.dbhub.models import (
    ColumnInfo,
    QueryExecutionError,
    QueryResult,
    QueryTimeoutError,
    SchemaInfo,
    TableInfo,
)
from src.graph import (
    _error_response_node,
    route_after_execution,
    route_after_organization,
    route_after_semantic_router,
    route_after_validation,
)
from src.nodes.input_parser import input_parser
from src.nodes.output_generator import output_generator
from src.nodes.query_executor import query_executor
from src.nodes.query_generator import query_generator
from src.nodes.query_validator import query_validator
from src.nodes.result_organizer import result_organizer
from src.nodes.schema_analyzer import _schema_cache, schema_analyzer
from src.state import create_initial_state


# ──────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────


def _make_config() -> AppConfig:
    """테스트용 AppConfig를 생성한다."""
    return AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9090/sse", source_name="infra_db", mcp_call_timeout=60),
        query=QueryConfig(max_retry_count=3, default_limit=1000),
        security=SecurityConfig(
            sensitive_columns=["password", "secret", "token", "api_key"],
            mask_pattern="***MASKED***",
        ),
        server=ServerConfig(host="0.0.0.0", port=8000),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )


def _make_schema() -> SchemaInfo:
    """인프라 DB 전체 스키마를 생성한다."""
    return SchemaInfo(
        tables={
            "servers": TableInfo(
                name="servers",
                columns=[
                    ColumnInfo(name="id", data_type="integer", nullable=False, is_primary_key=True),
                    ColumnInfo(name="hostname", data_type="varchar(255)", nullable=False),
                    ColumnInfo(name="ip_address", data_type="varchar(45)", nullable=False),
                    ColumnInfo(name="os", data_type="varchar(100)", nullable=True),
                    ColumnInfo(name="password", data_type="varchar(255)", nullable=True),
                ],
                row_count_estimate=50,
            ),
            "cpu_metrics": TableInfo(
                name="cpu_metrics",
                columns=[
                    ColumnInfo(name="id", data_type="integer", nullable=False, is_primary_key=True),
                    ColumnInfo(name="server_id", data_type="integer", nullable=False, is_foreign_key=True, references="servers.id"),
                    ColumnInfo(name="usage_pct", data_type="double", nullable=True),
                    ColumnInfo(name="timestamp", data_type="timestamp", nullable=False),
                ],
                row_count_estimate=500000,
            ),
            "memory_metrics": TableInfo(
                name="memory_metrics",
                columns=[
                    ColumnInfo(name="id", data_type="integer", nullable=False, is_primary_key=True),
                    ColumnInfo(name="server_id", data_type="integer", nullable=False, is_foreign_key=True, references="servers.id"),
                    ColumnInfo(name="usage_pct", data_type="double", nullable=True),
                    ColumnInfo(name="total_gb", data_type="double", nullable=True),
                    ColumnInfo(name="timestamp", data_type="timestamp", nullable=False),
                ],
                row_count_estimate=500000,
            ),
        },
        relationships=[
            {"from": "cpu_metrics.server_id", "to": "servers.id"},
            {"from": "memory_metrics.server_id", "to": "servers.id"},
        ],
    )


# servers 테이블만 포함된 간소화 스키마 dict (validator/generator 테스트용)
SERVERS_ONLY_SCHEMA: dict = {
    "tables": {
        "servers": {
            "columns": [
                {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                {"name": "hostname", "type": "varchar(255)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
            ],
            "row_count_estimate": 50,
            "sample_data": [],
        },
    },
    "relationships": [],
}


def _make_mock_db_client(
    schema: SchemaInfo,
    query_rows: list[dict[str, Any]],
) -> AsyncMock:
    """mock DB 클라이언트를 생성한다."""
    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.get_full_schema = AsyncMock(return_value=schema)
    client.get_sample_data = AsyncMock(return_value=[{"id": 1, "hostname": "web-01"}])
    client.execute_sql = AsyncMock(
        return_value=QueryResult(
            columns=list(query_rows[0].keys()) if query_rows else [],
            rows=query_rows,
            row_count=len(query_rows),
            execution_time_ms=45.0,
        )
    )
    return client


def _make_mock_llm(responses: list[str]) -> AsyncMock:
    """순서대로 응답을 반환하는 mock LLM을 생성한다."""
    llm = AsyncMock()
    side_effects = []
    for resp in responses:
        msg = MagicMock()
        msg.content = resp
        side_effects.append(msg)
    llm.ainvoke = AsyncMock(side_effect=side_effects)
    return llm


@asynccontextmanager
async def _mock_db_context(client):
    """get_db_client 대체용 async context manager."""
    yield client


CPU_QUERY_ROWS = [
    {"hostname": "web-01", "ip_address": "10.0.0.1", "usage_pct": 85.3},
    {"hostname": "web-02", "ip_address": "10.0.0.2", "usage_pct": 92.1},
    {"hostname": "db-01", "ip_address": "10.0.1.1", "usage_pct": 88.7},
]


# ──────────────────────────────────────────────
# 1. 정상 흐름 (Happy Path)
#    input_parser → schema_analyzer → query_generator
#    → query_validator → query_executor → result_organizer → output_generator
# ──────────────────────────────────────────────


class TestHappyPath:
    """7단계 파이프라인을 순차적으로 실행하여 전체 정상 흐름을 검증한다."""

    @pytest.mark.asyncio
    async def test_step1_input_parser(self):
        """Step 1: 사용자 질의를 구조화된 요구사항으로 파싱한다."""
        cfg = _make_config()
        state = create_initial_state(user_query="CPU 사용률이 80% 이상인 서버 목록을 보여줘")

        llm_resp = json.dumps({
            "query_targets": ["서버", "CPU"],
            "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 80}],
            "time_range": None,
            "output_format": "text",
            "aggregation": None,
            "limit": None,
        }, ensure_ascii=False)
        mock_llm = _make_mock_llm([llm_resp])

        result = await input_parser(state, llm=mock_llm, app_config=cfg)
        state.update(result)

        assert state["current_node"] == "input_parser"
        assert state["error_message"] is None
        assert state["parsed_requirements"]["query_targets"] == ["서버", "CPU"]
        assert state["parsed_requirements"]["original_query"] == state["user_query"]

    @pytest.mark.asyncio
    async def test_step2_schema_analyzer(self):
        """Step 2: DB 스키마를 조회하여 관련 테이블을 식별한다."""
        _schema_cache.invalidate()
        cfg = _make_config()
        schema = _make_schema()

        state = create_initial_state(user_query="CPU 사용률이 80% 이상인 서버")
        state["parsed_requirements"] = {
            "query_targets": ["서버", "CPU"],
            "original_query": "CPU 사용률이 80% 이상인 서버",
        }

        mock_client = _make_mock_db_client(schema, [])
        # schema_analyzer: table selection(쉼표 구분) + structure analysis(JSON)
        schema_llm_responses = [
            "servers, cpu_metrics",
            json.dumps({"patterns": [], "query_guide": ""}, ensure_ascii=False),
        ]
        mock_llm = _make_mock_llm(schema_llm_responses)

        with patch("src.nodes.schema_analyzer.get_db_client", return_value=_mock_db_context(mock_client)):
            result = await schema_analyzer(state, llm=mock_llm, app_config=cfg)
        state.update(result)

        assert state["current_node"] == "schema_analyzer"
        assert state["error_message"] is None
        assert "servers" in state["relevant_tables"]
        assert "cpu_metrics" in state["relevant_tables"]
        assert "servers" in state["schema_info"]["tables"]
        assert "cpu_metrics" in state["schema_info"]["tables"]

    @pytest.mark.asyncio
    async def test_step3_query_generator(self):
        """Step 3: 파싱 결과와 스키마를 기반으로 SQL을 생성한다."""
        cfg = _make_config()
        state = create_initial_state(user_query="CPU 사용률이 80% 이상인 서버")
        state["parsed_requirements"] = {
            "query_targets": ["서버", "CPU"],
            "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 80}],
            "output_format": "text",
            "original_query": "CPU 사용률이 80% 이상인 서버",
        }
        state["schema_info"] = SERVERS_ONLY_SCHEMA

        sql = "SELECT s.hostname, c.usage_pct FROM servers s JOIN cpu_metrics c ON s.id = c.server_id WHERE c.usage_pct >= 80 LIMIT 1000;"
        mock_llm = _make_mock_llm([f"```sql\n{sql}\n```"])

        result = await query_generator(state, llm=mock_llm, app_config=cfg)
        state.update(result)

        assert state["current_node"] == "query_generator"
        assert state["error_message"] is None
        assert state["retry_count"] == 0
        assert "SELECT" in state["generated_sql"].upper()

    @pytest.mark.asyncio
    async def test_step4_query_validator_passes(self):
        """Step 4: 올바른 SELECT SQL은 검증을 통과한다."""
        cfg = _make_config()
        state = create_initial_state(user_query="test")
        state["generated_sql"] = (
            "SELECT s.hostname, c.usage_pct "
            "FROM servers s JOIN cpu_metrics c ON s.id = c.server_id "
            "WHERE c.usage_pct >= 80 LIMIT 1000;"
        )
        state["schema_info"] = {
            "tables": {
                "servers": {"columns": [{"name": "id", "type": "integer"}, {"name": "hostname", "type": "varchar"}]},
                "cpu_metrics": {"columns": [{"name": "server_id", "type": "integer"}, {"name": "usage_pct", "type": "double"}]},
            },
        }

        result = await query_validator(state, app_config=cfg)
        state.update(result)

        assert state["current_node"] == "query_validator"
        assert state["validation_result"]["passed"] is True

        # 라우팅 결정 검증
        assert route_after_validation(state) == "query_executor"

    @pytest.mark.asyncio
    async def test_step5_query_executor(self):
        """Step 5: 검증된 SQL을 실행하고 결과를 수집한다."""
        cfg = _make_config()
        schema = _make_schema()
        state = create_initial_state(user_query="test")
        state["generated_sql"] = "SELECT hostname, usage_pct FROM servers LIMIT 100;"

        mock_client = _make_mock_db_client(schema, CPU_QUERY_ROWS)

        with patch("src.nodes.query_executor.get_db_client", return_value=_mock_db_context(mock_client)):
            with patch("src.nodes.query_executor.log_query_execution", new_callable=AsyncMock):
                result = await query_executor(state, app_config=cfg)
        state.update(result)

        assert state["current_node"] == "query_executor"
        assert state["error_message"] is None
        assert len(state["query_results"]) == 3
        assert state["query_results"][0]["hostname"] == "web-01"
        assert len(state["query_attempts"]) == 1
        assert state["query_attempts"][0]["success"] is True
        assert state["query_attempts"][0]["row_count"] == 3

        # 라우팅 결정 검증
        assert route_after_execution(state) == "result_organizer"

    @pytest.mark.asyncio
    async def test_step6_result_organizer(self):
        """Step 6: 결과를 정리하고 포맷팅하며 충분성을 판단한다."""
        cfg = _make_config()
        state = create_initial_state(user_query="test")
        state["query_results"] = CPU_QUERY_ROWS.copy()
        state["parsed_requirements"] = {"query_targets": ["서버", "CPU"], "output_format": "text"}

        result = await result_organizer(state, app_config=cfg)
        state.update(result)

        assert state["current_node"] == "result_organizer"
        assert state["organized_data"]["is_sufficient"] is True
        assert len(state["organized_data"]["rows"]) == 3
        # usage_pct에 % 포맷 적용 확인
        assert "85.3%" in state["organized_data"]["rows"][0]["usage_pct"]
        assert "92.1%" in state["organized_data"]["rows"][1]["usage_pct"]
        # 요약에 건수 포함 확인
        assert "3건" in state["organized_data"]["summary"]

        # 라우팅 결정 검증
        assert route_after_organization(state) == "output_generator"

    @pytest.mark.asyncio
    async def test_step7_output_generator(self):
        """Step 7: 정리된 데이터로 자연어 응답을 생성한다."""
        cfg = _make_config()
        state = create_initial_state(user_query="CPU 사용률이 높은 서버")
        state["organized_data"] = {
            "summary": "총 3건의 데이터를 조회했습니다.",
            "rows": [
                {"hostname": "web-01", "usage_pct": "85.3%"},
                {"hostname": "web-02", "usage_pct": "92.1%"},
                {"hostname": "db-01", "usage_pct": "88.7%"},
            ],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버", "CPU"],
            "output_format": "text",
            "original_query": "CPU 사용률이 높은 서버",
        }
        state["generated_sql"] = "SELECT hostname, usage_pct FROM cpu_metrics LIMIT 100;"

        nl_response = "CPU 사용률이 높은 서버 3대를 조회했습니다."
        mock_llm = _make_mock_llm([nl_response])

        result = await output_generator(state, llm=mock_llm, app_config=cfg)
        state.update(result)

        assert state["current_node"] == "output_generator"
        assert state["final_response"] == nl_response
        assert state["output_file"] is None
        assert state["error_message"] is None

    @pytest.mark.asyncio
    async def test_full_pipeline_end_to_end(self):
        """전체 7단계를 하나의 State로 순차 실행하여 E2E를 검증한다."""
        _schema_cache.invalidate()
        cfg = _make_config()
        schema = _make_schema()

        state = create_initial_state(user_query="CPU 사용률이 80% 이상인 서버 목록을 보여줘")

        # LLM 응답: input_parser(1) + schema_analyzer(최대 3: table selection, structure analysis, retry)
        #           + query_generator(1) + output_generator(1)
        # schema_analyzer table selection은 쉼표 구분 텍스트 기대 (line 995)
        # schema_analyzer structure analysis는 JSON 기대
        llm_responses = [
            # 1. input_parser
            json.dumps({
                "query_targets": ["서버", "CPU"],
                "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 80}],
                "output_format": "text",
            }, ensure_ascii=False),
            # 2. schema_analyzer: table selection (쉼표 구분)
            "servers, cpu_metrics",
            # 3. schema_analyzer: structure analysis (JSON)
            json.dumps({"patterns": [], "query_guide": ""}, ensure_ascii=False),
            # 4. query_generator
            "```sql\nSELECT s.hostname, s.ip_address, c.usage_pct FROM servers s JOIN cpu_metrics c ON s.id = c.server_id WHERE c.usage_pct >= 80 LIMIT 1000;\n```",
            # 5. output_generator
            "CPU 사용률이 80% 이상인 서버 3대를 조회했습니다.",
        ]
        mock_llm = _make_mock_llm(llm_responses)
        mock_client = _make_mock_db_client(schema, CPU_QUERY_ROWS)

        # Step 1: input_parser
        result = await input_parser(state, llm=mock_llm, app_config=cfg)
        state.update(result)
        assert state["parsed_requirements"]["query_targets"] == ["서버", "CPU"]

        # Step 2: schema_analyzer
        with patch("src.nodes.schema_analyzer.get_db_client", return_value=_mock_db_context(mock_client)):
            result = await schema_analyzer(state, llm=mock_llm, app_config=cfg)
        state.update(result)
        assert "cpu_metrics" in state["relevant_tables"]

        # Step 3: query_generator
        result = await query_generator(state, llm=mock_llm, app_config=cfg)
        state.update(result)
        assert "SELECT" in state["generated_sql"].upper()

        # Step 4: query_validator
        result = await query_validator(state, app_config=cfg)
        state.update(result)
        assert state["validation_result"]["passed"] is True
        assert route_after_validation(state) == "query_executor"

        # Step 5: query_executor
        with patch("src.nodes.query_executor.get_db_client", return_value=_mock_db_context(mock_client)):
            with patch("src.nodes.query_executor.log_query_execution", new_callable=AsyncMock):
                result = await query_executor(state, app_config=cfg)
        state.update(result)
        assert len(state["query_results"]) == 3
        assert route_after_execution(state) == "result_organizer"

        # Step 6: result_organizer
        result = await result_organizer(state, app_config=cfg)
        state.update(result)
        assert state["organized_data"]["is_sufficient"] is True
        assert route_after_organization(state) == "output_generator"

        # Step 7: output_generator
        result = await output_generator(state, llm=mock_llm, app_config=cfg)
        state.update(result)
        assert "CPU 사용률" in state["final_response"]
        assert state["output_file"] is None


# ──────────────────────────────────────────────
# 2. 검증 실패 → 재시도 흐름
#    query_generator → query_validator(실패) → route → query_generator(재시도)
#    → query_validator(성공) → query_executor
# ──────────────────────────────────────────────


class TestValidationRetryFlow:
    """SQL 검증 실패 시 query_generator로 회귀하여 재생성하는 흐름."""

    @pytest.mark.asyncio
    async def test_bad_sql_rejected_then_retried_successfully(self):
        """존재하지 않는 테이블 → 검증 실패 → 재생성 → 검증 통과."""
        cfg = _make_config()
        state = create_initial_state(user_query="서버 목록")
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "filter_conditions": [],
            "output_format": "text",
            "original_query": "서버 목록",
        }
        state["relevant_tables"] = ["servers"]
        state["schema_info"] = SERVERS_ONLY_SCHEMA

        bad_sql = "SELECT * FROM nonexistent_table;"
        good_sql = "SELECT hostname FROM servers LIMIT 1000;"

        mock_llm = _make_mock_llm([
            f"```sql\n{bad_sql}\n```",
            f"```sql\n{good_sql}\n```",
        ])

        # 1차: generator → validator (실패)
        result = await query_generator(state, llm=mock_llm, app_config=cfg)
        state.update(result)
        assert state["generated_sql"] == bad_sql

        result = await query_validator(state, app_config=cfg)
        state.update(result)
        assert state["validation_result"]["passed"] is False
        assert state["error_message"] is not None

        # 라우팅: generator로 회귀 (retry_count=0 < 3)
        assert route_after_validation(state) == "query_generator"

        # 2차: generator (재시도) → validator (성공)
        result = await query_generator(state, llm=mock_llm, app_config=cfg)
        state.update(result)
        assert state["retry_count"] == 1
        assert state["error_message"] is None  # generator가 에러 초기화
        assert "servers" in state["generated_sql"]

        result = await query_validator(state, app_config=cfg)
        state.update(result)
        assert state["validation_result"]["passed"] is True
        assert route_after_validation(state) == "query_executor"

    @pytest.mark.asyncio
    async def test_delete_sql_rejected(self):
        """DELETE 문 → 검증 실패 → generator 회귀."""
        cfg = _make_config()
        state = create_initial_state(user_query="test")
        state["generated_sql"] = "DELETE FROM servers WHERE id = 1;"
        state["schema_info"] = SERVERS_ONLY_SCHEMA
        state["retry_count"] = 0

        result = await query_validator(state, app_config=cfg)
        state.update(result)

        assert state["validation_result"]["passed"] is False
        assert "SELECT" in result["validation_result"]["reason"]
        assert route_after_validation(state) == "query_generator"

    @pytest.mark.asyncio
    async def test_auto_limit_added_and_passes(self):
        """LIMIT 없는 SELECT → 자동 LIMIT 추가 → 검증 통과."""
        cfg = _make_config()
        state = create_initial_state(user_query="test")
        state["generated_sql"] = "SELECT hostname FROM servers;"
        state["schema_info"] = {
            "tables": {"servers": {"columns": [{"name": "hostname", "type": "varchar"}]}},
        }

        result = await query_validator(state, app_config=cfg)
        state.update(result)

        assert state["validation_result"]["passed"] is True
        assert "LIMIT" in state["generated_sql"]
        assert state["validation_result"]["auto_fixed_sql"] is not None
        assert route_after_validation(state) == "query_executor"


# ──────────────────────────────────────────────
# 3. 실행 에러 → 재시도 흐름
#    query_executor(에러) → route → query_generator(재시도) → ...
# ──────────────────────────────────────────────


class TestExecutionRetryFlow:
    """SQL 실행 에러 시 query_generator로 회귀하여 재시도하는 흐름."""

    @pytest.mark.asyncio
    async def test_execution_error_routes_to_generator(self):
        """QueryExecutionError → 에러 기록 → generator 회귀."""
        cfg = _make_config()
        state = create_initial_state(user_query="서버 목록")
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "text",
            "original_query": "서버 목록",
        }
        state["schema_info"] = SERVERS_ONLY_SCHEMA
        state["generated_sql"] = "SELECT hostname FROM servers LIMIT 1000;"

        error_client = AsyncMock()
        error_client.connect = AsyncMock()
        error_client.disconnect = AsyncMock()
        error_client.execute_sql = AsyncMock(
            side_effect=QueryExecutionError("relation 'servers' does not exist")
        )

        with patch("src.nodes.query_executor.get_db_client", return_value=_mock_db_context(error_client)):
            with patch("src.nodes.query_executor.log_query_execution", new_callable=AsyncMock):
                result = await query_executor(state, app_config=cfg)
        state.update(result)

        # 에러 상태 확인
        assert "SQL 실행 에러" in state["error_message"]
        assert state["query_results"] == []
        assert state["query_attempts"][0]["success"] is False

        # 라우팅: generator로 회귀
        assert route_after_execution(state) == "query_generator"

        # generator가 에러 컨텍스트를 참조하여 재생성
        fixed_sql = "SELECT hostname FROM servers LIMIT 1000;"
        mock_llm = _make_mock_llm([f"```sql\n{fixed_sql}\n```"])

        result = await query_generator(state, llm=mock_llm, app_config=cfg)
        state.update(result)
        assert state["retry_count"] == 1
        assert state["error_message"] is None

    @pytest.mark.asyncio
    async def test_timeout_error_routes_to_generator(self):
        """QueryTimeoutError → 타임아웃 메시지 → generator 회귀."""
        cfg = _make_config()
        state = create_initial_state(user_query="test")
        state["generated_sql"] = "SELECT * FROM huge_table;"

        timeout_client = AsyncMock()
        timeout_client.connect = AsyncMock()
        timeout_client.disconnect = AsyncMock()
        timeout_client.execute_sql = AsyncMock(side_effect=QueryTimeoutError("30초 초과"))

        with patch("src.nodes.query_executor.get_db_client", return_value=_mock_db_context(timeout_client)):
            with patch("src.nodes.query_executor.log_query_execution", new_callable=AsyncMock):
                result = await query_executor(state, app_config=cfg)
        state.update(result)

        assert "타임아웃" in state["error_message"]
        assert state["query_attempts"][0]["success"] is False
        assert route_after_execution(state) == "query_generator"


# ──────────────────────────────────────────────
# 4. 최대 재시도 초과 → error_response 종료
# ──────────────────────────────────────────────


class TestMaxRetryExhaustion:
    """retry_count >= 3 도달 시 error_response로 종료되는 흐름."""

    def test_validation_failure_at_max_retry(self):
        """검증 실패 + retry_count=3 → error_response."""
        state = create_initial_state(user_query="테스트")
        state["validation_result"] = {"passed": False, "reason": "금지된 키워드", "auto_fixed_sql": None}
        state["retry_count"] = 3
        state["error_message"] = "SQL 검증 실패: 금지된 키워드"

        assert route_after_validation(state) == "error_response"

        result = _error_response_node(state)
        assert "재시도 횟수가 최대" in result["final_response"]
        assert "3" in result["final_response"]
        assert "금지된 키워드" in result["final_response"]

    def test_execution_error_at_max_retry(self):
        """실행 에러 + retry_count=3 → error_response."""
        state = create_initial_state(user_query="test")
        state["error_message"] = "SQL 실행 에러: timeout"
        state["retry_count"] = 3

        assert route_after_execution(state) == "error_response"

    def test_insufficient_data_at_max_retry_falls_through(self):
        """데이터 부족 + retry_count=3 → 있는 데이터로 output_generator 진행."""
        state = create_initial_state(user_query="test")
        state["organized_data"]["is_sufficient"] = False
        state["retry_count"] = 3

        # max retry 시에도 output_generator로 진행 (best effort)
        assert route_after_organization(state) == "output_generator"


# ──────────────────────────────────────────────
# 5. 빈 결과 흐름
#    정상 실행 → 결과 0건 → 빈 결과 응답 (LLM 호출 없이)
# ──────────────────────────────────────────────


class TestEmptyResultFlow:
    """조건에 맞는 데이터가 0건일 때의 파이프라인 흐름."""

    @pytest.mark.asyncio
    async def test_empty_result_full_flow(self):
        """전체 파이프라인을 거쳐 결과 0건 → 안내 응답 생성."""
        _schema_cache.invalidate()
        cfg = _make_config()
        schema = _make_schema()
        state = create_initial_state(user_query="CPU 사용률이 99% 이상인 서버")

        llm_responses = [
            # 1. input_parser
            json.dumps({
                "query_targets": ["서버", "CPU"],
                "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 99}],
                "output_format": "text",
            }, ensure_ascii=False),
            # 2. schema_analyzer: table selection (쉼표 구분)
            "servers, cpu_metrics",
            # 3. schema_analyzer: structure analysis (JSON)
            json.dumps({"patterns": [], "query_guide": ""}, ensure_ascii=False),
            # 4. query_generator
            "```sql\nSELECT s.hostname, c.usage_pct FROM servers s JOIN cpu_metrics c ON s.id = c.server_id WHERE c.usage_pct >= 99 LIMIT 1000;\n```",
        ]
        mock_llm = _make_mock_llm(llm_responses)
        mock_client = _make_mock_db_client(schema, query_rows=[])
        mock_client.execute_sql = AsyncMock(
            return_value=QueryResult(columns=[], rows=[], row_count=0, execution_time_ms=10.0)
        )

        # Step 1~5
        result = await input_parser(state, llm=mock_llm, app_config=cfg)
        state.update(result)

        with patch("src.nodes.schema_analyzer.get_db_client", return_value=_mock_db_context(mock_client)):
            result = await schema_analyzer(state, llm=mock_llm, app_config=cfg)
        state.update(result)

        result = await query_generator(state, llm=mock_llm, app_config=cfg)
        state.update(result)

        result = await query_validator(state, app_config=cfg)
        state.update(result)
        assert state["validation_result"]["passed"] is True

        with patch("src.nodes.query_executor.get_db_client", return_value=_mock_db_context(mock_client)):
            with patch("src.nodes.query_executor.log_query_execution", new_callable=AsyncMock):
                result = await query_executor(state, app_config=cfg)
        state.update(result)
        assert state["query_results"] == []

        # Step 6: result_organizer — 빈 결과도 is_sufficient=True
        result = await result_organizer(state, app_config=cfg)
        state.update(result)
        assert state["organized_data"]["is_sufficient"] is True
        assert route_after_organization(state) == "output_generator"

        # Step 7: output_generator — LLM 호출 없이 빈 결과 안내 생성
        result = await output_generator(state, llm=mock_llm, app_config=cfg)
        state.update(result)
        assert "데이터가 없습니다" in state["final_response"]


# ──────────────────────────────────────────────
# 6. 보안 차단 흐름
#    DML/DDL 차단, SQL 인젝션 차단, 민감 데이터 마스킹
# ──────────────────────────────────────────────


class TestSecurityFlow:
    """파이프라인 내 보안 검증 흐름."""

    @pytest.mark.asyncio
    async def test_insert_blocked_at_validator(self):
        """INSERT SQL → validator 차단 → generator 회귀."""
        cfg = _make_config()
        state = create_initial_state(user_query="test")
        state["generated_sql"] = "INSERT INTO servers (hostname) VALUES ('evil');"
        state["schema_info"] = SERVERS_ONLY_SCHEMA
        state["retry_count"] = 0

        result = await query_validator(state, app_config=cfg)
        state.update(result)

        assert state["validation_result"]["passed"] is False
        assert route_after_validation(state) == "query_generator"

    @pytest.mark.asyncio
    async def test_drop_table_blocked_at_validator(self):
        """DROP TABLE → validator 차단."""
        cfg = _make_config()
        state = create_initial_state(user_query="test")
        state["generated_sql"] = "DROP TABLE servers;"
        state["schema_info"] = SERVERS_ONLY_SCHEMA

        result = await query_validator(state, app_config=cfg)

        assert result["validation_result"]["passed"] is False

    @pytest.mark.asyncio
    async def test_union_injection_blocked_at_validator(self):
        """UNION 기반 SQL 인젝션 → validator 차단."""
        cfg = _make_config()
        state = create_initial_state(user_query="test")
        state["generated_sql"] = (
            "SELECT hostname FROM servers WHERE id = 1 "
            "UNION SELECT password FROM users LIMIT 100;"
        )
        state["schema_info"] = SERVERS_ONLY_SCHEMA

        result = await query_validator(state, app_config=cfg)

        assert result["validation_result"]["passed"] is False

    @pytest.mark.asyncio
    async def test_sensitive_columns_masked_at_organizer(self):
        """password, api_key, token → result_organizer에서 마스킹."""
        cfg = _make_config()
        state = create_initial_state(user_query="test")
        state["query_results"] = [
            {"hostname": "web-01", "password": "secret123", "api_key": "sk-abc", "token": "eyJhbG..."},
        ]
        state["parsed_requirements"] = {"query_targets": ["서버"], "output_format": "text"}

        result = await result_organizer(state, app_config=cfg)
        row = result["organized_data"]["rows"][0]

        assert row["hostname"] == "web-01"
        assert row["password"] == "***MASKED***"
        assert row["api_key"] == "***MASKED***"
        assert row["token"] == "***MASKED***"


# ──────────────────────────────────────────────
# 7. 에러 내성 (Fault Tolerance)
#    LLM 실패, DB 연결 실패 시에도 파이프라인이 중단되지 않음
# ──────────────────────────────────────────────


class TestFaultTolerance:
    """외부 의존성 장애 시 파이프라인의 내성을 검증한다."""

    @pytest.mark.asyncio
    async def test_input_parser_survives_llm_json_failure(self):
        """LLM이 잘못된 JSON을 반환해도 기본값으로 진행한다."""
        cfg = _make_config()
        state = create_initial_state(user_query="서버 목록")
        mock_llm = _make_mock_llm(["이건 JSON이 아닙니다", "이것도 아닙니다"])

        result = await input_parser(state, llm=mock_llm, app_config=cfg)

        assert result["parsed_requirements"]["original_query"] == "서버 목록"
        assert result["error_message"] is None

    @pytest.mark.asyncio
    async def test_input_parser_survives_llm_exception(self):
        """LLM 호출 자체가 예외를 발생시켜도 최소 파싱 결과로 진행한다."""
        cfg = _make_config()
        state = create_initial_state(user_query="디스크 사용량")
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM 연결 실패"))

        result = await input_parser(state, llm=mock_llm, app_config=cfg)

        assert result["parsed_requirements"]["original_query"] == "디스크 사용량"
        assert result["parsed_requirements"]["query_targets"] == []

    @pytest.mark.asyncio
    async def test_schema_analyzer_survives_db_failure(self):
        """DB 연결 실패 시 에러 메시지를 기록하고 파이프라인이 중단되지 않는다."""
        _schema_cache.invalidate()
        cfg = _make_config()
        state = create_initial_state(user_query="서버 목록")
        state["parsed_requirements"] = {"query_targets": ["서버"], "original_query": "서버 목록"}

        @asynccontextmanager
        async def _error_ctx(_config):
            raise ConnectionError("DB 연결 실패")
            yield  # noqa: unreachable

        mock_llm = _make_mock_llm([])

        with patch("src.nodes.schema_analyzer.get_db_client", _error_ctx):
            result = await schema_analyzer(state, llm=mock_llm, app_config=cfg)

        assert "DB 스키마 조회 실패" in result["error_message"]
        assert result["relevant_tables"] == []


# ──────────────────────────────────────────────
# 8. 시멘틱 라우팅 분기
# ──────────────────────────────────────────────


class TestSemanticRouting:
    """시멘틱 라우터의 라우팅 결정을 검증한다."""

    def test_single_db_routes_to_schema_analyzer(self):
        """단일 DB 대상 → schema_analyzer 진행."""
        state = create_initial_state(user_query="test")
        state["is_multi_db"] = False

        assert route_after_semantic_router(state) == "schema_analyzer"

    def test_multi_db_routes_to_multi_db_executor(self):
        """멀티 DB 대상 → multi_db_executor 진행."""
        state = create_initial_state(user_query="test")
        state["is_multi_db"] = True

        assert route_after_semantic_router(state) == "multi_db_executor"
