"""공통 pytest fixture 모듈.

테스트 전반에서 사용되는 mock state, mock config, 샘플 데이터를 정의한다.
"""

import pytest

from src.config import (
    AppConfig,
    DBHubConfig,
    LLMConfig,
    QueryConfig,
    SecurityConfig,
    ServerConfig,
)
from src.state import AgentState, create_initial_state


@pytest.fixture
def sample_schema_info() -> dict:
    """테스트용 스키마 정보를 반환한다."""
    return {
        "tables": {
            "servers": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "hostname", "type": "varchar(255)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "ip_address", "type": "varchar(45)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "os", "type": "varchar(100)", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                ],
                "row_count_estimate": 50,
                "sample_data": [{"id": 1, "hostname": "web-01", "ip_address": "10.0.0.1", "os": "Ubuntu 22.04"}],
            },
            "cpu_metrics": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "server_id", "type": "integer", "nullable": False, "primary_key": False, "foreign_key": True, "references": "servers.id"},
                    {"name": "usage_pct", "type": "double", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "timestamp", "type": "timestamp", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                ],
                "row_count_estimate": 500000,
                "sample_data": [],
            },
            "memory_metrics": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "server_id", "type": "integer", "nullable": False, "primary_key": False, "foreign_key": True, "references": "servers.id"},
                    {"name": "usage_pct", "type": "double", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "total_gb", "type": "double", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "timestamp", "type": "timestamp", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                ],
                "row_count_estimate": 500000,
                "sample_data": [],
            },
        },
        "relationships": [
            {"from": "cpu_metrics.server_id", "to": "servers.id"},
            {"from": "memory_metrics.server_id", "to": "servers.id"},
        ],
    }


@pytest.fixture
def sample_state(sample_schema_info) -> AgentState:
    """기본 테스트용 AgentState를 반환한다."""
    state = create_initial_state(user_query="CPU 사용률이 80% 이상인 서버 목록")
    state["schema_info"] = sample_schema_info
    state["parsed_requirements"] = {
        "query_targets": ["서버", "CPU"],
        "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 80}],
        "time_range": None,
        "output_format": "text",
        "aggregation": None,
        "limit": None,
        "original_query": "CPU 사용률이 80% 이상인 서버 목록",
    }
    state["relevant_tables"] = ["cpu_metrics", "servers"]
    return state


@pytest.fixture
def mock_config() -> AppConfig:
    """테스트용 AppConfig를 반환한다."""
    return AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9090/sse", source_name="infra_db", mcp_call_timeout=60),
        query=QueryConfig(max_retry_count=3, default_limit=1000),
        security=SecurityConfig(sensitive_columns=["password", "secret", "token", "api_key"], mask_pattern="***MASKED***"),
        server=ServerConfig(host="0.0.0.0", port=8000),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )


@pytest.fixture
def sample_query_results() -> list[dict]:
    """테스트용 쿼리 결과를 반환한다."""
    return [
        {"hostname": "web-01", "ip_address": "10.0.0.1", "usage_pct": 85.3},
        {"hostname": "web-02", "ip_address": "10.0.0.2", "usage_pct": 92.1},
        {"hostname": "db-01", "ip_address": "10.0.1.1", "usage_pct": 88.7},
    ]
