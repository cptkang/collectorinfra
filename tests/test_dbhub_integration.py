"""클라이언트 - MCP 서버 연동 통합 테스트.

DB 연결 없이 DBPoolManager를 mock하여
MCP 서버 도구 함수와 DBHubClient 파서의 end-to-end 흐름을 검증한다.

테스트 대상:
- MCP 서버 도구 5개 (list_sources, health_check, search_objects, execute_sql, get_table_schema)
- DBHubClient의 결과 파싱 로직 (_parse_table_list, _parse_table_schema, _parse_query_result)
- 읽기 전용 위반 거부
- 타임아웃 처리
- 재연결 로직
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# mcp_server 패키지는 독립 서브패키지(mcp_server/)로 pip install 없이 사용하므로
# sys.path에 mcp_server/ 디렉토리를 추가하여 import가 가능하게 한다.
_MCP_SERVER_ROOT = str(Path(__file__).resolve().parent.parent / "mcp_server")
if _MCP_SERVER_ROOT not in sys.path:
    sys.path.insert(0, _MCP_SERVER_ROOT)

from mcp_server.config import SourceConfig
from mcp_server.db import DBPoolManager
from mcp_server.security import ReadOnlyViolationError

# --- MCP 서버 도구 함수 임포트 (register_tools 내부 함수 직접 호출 불가하므로
#     tools 모듈의 헬퍼 + register_tools 후 도구 함수를 추출하는 방식 사용) ---
from mcp_server.tools import register_tools

from src.config import DBHubConfig, QueryConfig
from src.dbhub.client import DBHubClient
from src.dbhub.models import (
    ColumnInfo,
    DBConnectionError,
    DBHubError,
    QueryExecutionError,
    QueryResult,
    QueryTimeoutError,
    TableInfo,
)


# ============================================================================
# Fixture: 테스트용 SourceConfig
# ============================================================================


def _make_source_config(
    name: str = "infra_db",
    db_type: str = "postgresql",
    readonly: bool = True,
    max_rows: int = 10000,
    query_timeout: int = 30,
) -> SourceConfig:
    """테스트용 SourceConfig를 생성한다."""
    return SourceConfig(
        name=name,
        type=db_type,
        connection="postgresql://test:test@localhost:5432/testdb",
        readonly=readonly,
        query_timeout=query_timeout,
        max_rows=max_rows,
        pool_min_size=1,
        pool_max_size=5,
    )


# ============================================================================
# Fixture: mock DBPoolManager
# ============================================================================


@pytest.fixture
def mock_pool_manager() -> DBPoolManager:
    """DB 연결 없이 동작하는 mock DBPoolManager를 생성한다.

    Returns:
        모든 메서드가 mock된 DBPoolManager 인스턴스
    """
    pm = MagicMock(spec=DBPoolManager)

    source_config = _make_source_config()

    # 기본 메서드 mock
    pm.get_active_sources.return_value = ["infra_db"]
    pm.is_source_active.side_effect = lambda name: name == "infra_db"
    pm.get_source_config.return_value = source_config
    pm.get_source_type.return_value = "postgresql"

    # async 메서드 mock
    pm.execute = AsyncMock(return_value=[])
    pm.health_check = AsyncMock(return_value=True)

    return pm


# ============================================================================
# Fixture: mock MCP Context
# ============================================================================


@pytest.fixture
def mock_ctx(mock_pool_manager: DBPoolManager) -> MagicMock:
    """MCP Context를 mock하여 pool_manager를 주입한다.

    Args:
        mock_pool_manager: mock된 DBPoolManager

    Returns:
        pool_manager가 주입된 mock Context
    """
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {
        "pool_manager": mock_pool_manager,
        "config": MagicMock(),
    }
    return ctx


# ============================================================================
# Fixture: MCP 서버 도구 함수 추출
# ============================================================================


@pytest.fixture
def mcp_tools() -> dict[str, Any]:
    """FastMCP에 등록된 도구 함수들을 추출한다.

    register_tools()가 @mcp.tool() 데코레이터로 등록하는 함수들을
    캡처하여 dict로 반환한다.

    Returns:
        도구 이름 -> 함수 매핑
    """
    tools: dict[str, Any] = {}

    mock_mcp = MagicMock()

    def capture_tool(*args: Any, **kwargs: Any) -> Any:
        """@mcp.tool() 데코레이터를 가로채서 함수를 캡처한다."""
        def decorator(func: Any) -> Any:
            tools[func.__name__] = func
            return func
        return decorator

    mock_mcp.tool = capture_tool
    register_tools(mock_mcp)
    return tools


# ============================================================================
# Fixture: DBHubClient (mock 연결)
# ============================================================================


@pytest.fixture
def dbhub_client() -> DBHubClient:
    """테스트용 DBHubClient를 생성한다.

    MCP 세션을 mock하여 실제 서버 연결 없이 파서 로직을 테스트한다.

    Returns:
        mock 세션이 설정된 DBHubClient
    """
    config = DBHubConfig(
        server_url="http://localhost:9090/sse",
        source_name="infra_db",
        mcp_call_timeout=10,
    )
    query_config = QueryConfig(max_retry_count=3, default_limit=1000)
    client = DBHubClient(config, query_config)
    client._connected = True
    client._mcp_session = AsyncMock()
    return client


# ============================================================================
# 테스트: list_sources
# ============================================================================


class TestListSources:
    """list_sources 도구 호출 및 활성 소스 목록 반환 테스트."""

    async def test_returns_active_sources(
        self, mcp_tools: dict, mock_ctx: MagicMock
    ) -> None:
        """활성 소스 목록이 JSON으로 반환된다."""
        result_json = await mcp_tools["list_sources"](ctx=mock_ctx)
        result = json.loads(result_json)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "infra_db"
        assert result[0]["type"] == "postgresql"
        assert result[0]["readonly"] is True

    async def test_includes_source_settings(
        self, mcp_tools: dict, mock_ctx: MagicMock
    ) -> None:
        """소스 설정(query_timeout, max_rows)이 포함된다."""
        result_json = await mcp_tools["list_sources"](ctx=mock_ctx)
        result = json.loads(result_json)

        source = result[0]
        assert "query_timeout" in source
        assert "max_rows" in source
        assert source["query_timeout"] == 30
        assert source["max_rows"] == 10000

    async def test_multiple_sources(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """여러 소스가 등록된 경우 모두 반환된다."""
        source_configs = {
            "infra_db": _make_source_config("infra_db", "postgresql"),
            "infra_db2": _make_source_config("infra_db2", "db2"),
        }
        mock_pool_manager.get_active_sources.return_value = [
            "infra_db",
            "infra_db2",
        ]
        mock_pool_manager.get_source_config.side_effect = (
            lambda name: source_configs[name]
        )

        result_json = await mcp_tools["list_sources"](ctx=mock_ctx)
        result = json.loads(result_json)

        assert len(result) == 2
        names = [s["name"] for s in result]
        assert "infra_db" in names
        assert "infra_db2" in names


# ============================================================================
# 테스트: health_check
# ============================================================================


class TestHealthCheck:
    """health_check 도구 호출 및 정상/비정상 응답 처리 테스트."""

    async def test_healthy_source(
        self, mcp_tools: dict, mock_ctx: MagicMock
    ) -> None:
        """정상 소스에 대해 healthy 상태를 반환한다."""
        result_json = await mcp_tools["health_check"](
            source="infra_db", ctx=mock_ctx
        )
        result = json.loads(result_json)

        assert result["source"] == "infra_db"
        assert result["status"] == "healthy"
        assert "response_time_ms" in result
        assert result["source_type"] == "postgresql"

    async def test_unhealthy_source(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """비정상 소스에 대해 unhealthy 상태를 반환한다."""
        mock_pool_manager.health_check = AsyncMock(return_value=False)

        result_json = await mcp_tools["health_check"](
            source="infra_db", ctx=mock_ctx
        )
        result = json.loads(result_json)

        assert result["status"] == "unhealthy"

    async def test_unknown_source(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """미등록 소스에 대해 not_found 상태를 반환한다."""
        mock_pool_manager.is_source_active.return_value = False

        result_json = await mcp_tools["health_check"](
            source="unknown_db", ctx=mock_ctx
        )
        result = json.loads(result_json)

        assert result["status"] == "not_found"
        assert "unknown_db" in result["message"]


# ============================================================================
# 테스트: search_objects
# ============================================================================


class TestSearchObjects:
    """search_objects 도구 호출 및 TableInfo 변환 테스트."""

    async def test_returns_table_list(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """테이블 목록이 JSON으로 반환된다."""
        mock_pool_manager.execute = AsyncMock(
            return_value=[
                {"name": "servers", "schema": "public"},
                {"name": "cpu_metrics", "schema": "public"},
                {"name": "memory_metrics", "schema": "public"},
            ]
        )

        result_json = await mcp_tools["search_objects"](
            source="infra_db", pattern="*", type="table", ctx=mock_ctx
        )
        result = json.loads(result_json)

        assert isinstance(result, list)
        assert len(result) == 3
        names = [r["name"] for r in result]
        assert "servers" in names
        assert "cpu_metrics" in names

    async def test_pattern_filtering(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """패턴 필터링으로 특정 테이블만 반환된다."""
        mock_pool_manager.execute = AsyncMock(
            return_value=[{"name": "cpu_metrics", "schema": "public"}]
        )

        result_json = await mcp_tools["search_objects"](
            source="infra_db", pattern="cpu*", type="table", ctx=mock_ctx
        )
        result = json.loads(result_json)

        assert len(result) == 1
        assert result[0]["name"] == "cpu_metrics"

    async def test_client_parse_table_list(
        self, dbhub_client: DBHubClient
    ) -> None:
        """클라이언트가 search_objects 결과를 TableInfo 목록으로 변환한다."""
        # MCP 결과 형식을 시뮬레이션 (TextContent 구조)
        mock_text_content = MagicMock()
        mock_text_content.text = json.dumps([
            {"name": "servers", "schema": "public"},
            {"name": "cpu_metrics", "schema": "public"},
        ])

        mock_result = MagicMock()
        mock_result.content = [mock_text_content]

        tables = dbhub_client._parse_table_list(mock_result)

        assert len(tables) == 2
        assert all(isinstance(t, TableInfo) for t in tables)
        assert tables[0].name == "servers"
        assert tables[0].schema_name == "public"
        assert tables[1].name == "cpu_metrics"

    async def test_db_error_returns_error_json(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """DB 에러 발생 시 에러 JSON을 반환한다."""
        mock_pool_manager.execute = AsyncMock(
            side_effect=Exception("connection refused")
        )

        result_json = await mcp_tools["search_objects"](
            source="infra_db", pattern="*", type="table", ctx=mock_ctx
        )
        result = json.loads(result_json)

        assert "error" in result
        assert "connection refused" in result["error"]


# ============================================================================
# 테스트: execute_sql
# ============================================================================


class TestExecuteSql:
    """execute_sql 도구 호출 및 QueryResult 변환 테스트."""

    async def test_returns_query_result(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """쿼리 결과가 columns, rows, row_count 형식으로 반환된다."""
        mock_pool_manager.execute = AsyncMock(
            return_value=[
                {"hostname": "web-01", "ip_address": "10.0.0.1", "usage_pct": 85.3},
                {"hostname": "web-02", "ip_address": "10.0.0.2", "usage_pct": 92.1},
            ]
        )

        result_json = await mcp_tools["execute_sql"](
            source="infra_db",
            sql="SELECT hostname, ip_address, usage_pct FROM servers",
            ctx=mock_ctx,
        )
        result = json.loads(result_json)

        assert result["row_count"] == 2
        assert "hostname" in result["columns"]
        assert len(result["rows"]) == 2
        assert result["truncated"] is False
        assert "execution_time_ms" in result

    async def test_truncation_on_max_rows(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """max_rows 초과 시 결과가 잘리고 truncated=True가 된다."""
        # max_rows=2인 소스 설정
        small_source = _make_source_config(max_rows=2)
        mock_pool_manager.get_source_config.return_value = small_source

        mock_pool_manager.execute = AsyncMock(
            return_value=[
                {"id": 1, "name": "a"},
                {"id": 2, "name": "b"},
                {"id": 3, "name": "c"},
            ]
        )

        result_json = await mcp_tools["execute_sql"](
            source="infra_db",
            sql="SELECT id, name FROM servers",
            ctx=mock_ctx,
        )
        result = json.loads(result_json)

        assert result["row_count"] == 2
        assert result["truncated"] is True

    async def test_empty_result(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """빈 결과도 정상적으로 처리된다."""
        mock_pool_manager.execute = AsyncMock(return_value=[])

        result_json = await mcp_tools["execute_sql"](
            source="infra_db",
            sql="SELECT * FROM servers WHERE 1=0",
            ctx=mock_ctx,
        )
        result = json.loads(result_json)

        assert result["row_count"] == 0
        assert result["rows"] == []
        assert result["columns"] == []

    async def test_client_parse_query_result(
        self, dbhub_client: DBHubClient
    ) -> None:
        """클라이언트가 execute_sql 결과를 QueryResult로 변환한다."""
        mock_text_content = MagicMock()
        mock_text_content.text = json.dumps({
            "columns": ["hostname", "usage_pct"],
            "rows": [
                {"hostname": "web-01", "usage_pct": 85.3},
                {"hostname": "web-02", "usage_pct": 92.1},
            ],
            "row_count": 2,
            "truncated": False,
            "execution_time_ms": 12.5,
        })

        mock_result = MagicMock()
        mock_result.content = [mock_text_content]

        query_result = dbhub_client._parse_query_result(mock_result)

        assert isinstance(query_result, QueryResult)
        assert query_result.row_count == 2
        assert query_result.columns == ["hostname", "usage_pct"]
        assert query_result.rows[0]["hostname"] == "web-01"
        assert query_result.truncated is False

    async def test_client_parse_error_result(
        self, dbhub_client: DBHubClient
    ) -> None:
        """에러 응답이 QueryExecutionError로 변환된다."""
        mock_text_content = MagicMock()
        mock_text_content.text = json.dumps({
            "error": "relation 'nonexistent' does not exist"
        })

        mock_result = MagicMock()
        mock_result.content = [mock_text_content]

        with pytest.raises(QueryExecutionError) as exc_info:
            dbhub_client._parse_query_result(mock_result)

        assert "nonexistent" in str(exc_info.value)

    async def test_sql_execution_error(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """SQL 실행 에러 시 에러 JSON을 반환한다."""
        mock_pool_manager.execute = AsyncMock(
            side_effect=Exception("syntax error at or near 'SELEC'")
        )

        result_json = await mcp_tools["execute_sql"](
            source="infra_db",
            sql="SELEC * FROM servers",
            ctx=mock_ctx,
        )
        result = json.loads(result_json)

        assert "error" in result
        assert "syntax error" in result["error"]


# ============================================================================
# 테스트: get_table_schema
# ============================================================================


class TestGetTableSchema:
    """get_table_schema 도구 호출 및 TableInfo (컬럼, PK, FK) 변환 테스트."""

    @pytest.fixture
    def schema_mock_data(self) -> dict[str, list[dict]]:
        """get_table_schema 테스트용 mock 데이터를 반환한다."""
        return {
            "columns": [
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "is_nullable": "NO",
                    "column_default": "nextval('servers_id_seq')",
                },
                {
                    "column_name": "hostname",
                    "data_type": "character varying",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "column_name": "ip_address",
                    "data_type": "character varying",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "column_name": "os",
                    "data_type": "character varying",
                    "is_nullable": "YES",
                    "column_default": None,
                },
            ],
            "primary_keys": [{"column_name": "id"}],
            "foreign_keys": [],
        }

    async def test_returns_table_schema(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
        schema_mock_data: dict,
    ) -> None:
        """테이블 스키마가 컬럼, PK, FK 정보를 포함하여 반환된다."""
        # execute 호출 시 순서대로 columns, PK, FK 결과 반환
        mock_pool_manager.execute = AsyncMock(
            side_effect=[
                schema_mock_data["columns"],
                schema_mock_data["primary_keys"],
                schema_mock_data["foreign_keys"],
            ]
        )

        result_json = await mcp_tools["get_table_schema"](
            source="infra_db", table_name="servers", ctx=mock_ctx
        )
        result = json.loads(result_json)

        assert result["table_name"] == "servers"
        assert result["source"] == "infra_db"
        assert result["source_type"] == "postgresql"
        assert len(result["columns"]) == 4
        assert result["primary_keys"] == ["id"]

        # PK 컬럼 표시 확인
        id_col = next(c for c in result["columns"] if c["column_name"] == "id")
        assert id_col["is_primary_key"] is True

        hostname_col = next(
            c for c in result["columns"] if c["column_name"] == "hostname"
        )
        assert hostname_col["is_primary_key"] is False

    async def test_schema_with_foreign_keys(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """FK가 있는 테이블의 스키마가 FK 관계를 포함한다."""
        columns = [
            {
                "column_name": "id",
                "data_type": "integer",
                "is_nullable": "NO",
                "column_default": None,
            },
            {
                "column_name": "server_id",
                "data_type": "integer",
                "is_nullable": "NO",
                "column_default": None,
            },
            {
                "column_name": "usage_pct",
                "data_type": "double precision",
                "is_nullable": "YES",
                "column_default": None,
            },
        ]
        pk = [{"column_name": "id"}]
        fk = [
            {
                "from_column": "server_id",
                "to_table": "servers",
                "to_column": "id",
            }
        ]

        mock_pool_manager.execute = AsyncMock(
            side_effect=[columns, pk, fk]
        )

        result_json = await mcp_tools["get_table_schema"](
            source="infra_db", table_name="cpu_metrics", ctx=mock_ctx
        )
        result = json.loads(result_json)

        assert len(result["foreign_keys"]) == 1
        assert result["foreign_keys"][0]["from_column"] == "server_id"
        assert result["foreign_keys"][0]["to_table"] == "servers"

    async def test_client_parse_table_schema(
        self, dbhub_client: DBHubClient
    ) -> None:
        """클라이언트가 get_table_schema 결과를 TableInfo로 변환한다."""
        schema_data = {
            "table_name": "cpu_metrics",
            "source": "infra_db",
            "source_type": "postgresql",
            "columns": [
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "is_nullable": "NO",
                    "is_primary_key": True,
                },
                {
                    "column_name": "server_id",
                    "data_type": "integer",
                    "is_nullable": "NO",
                    "is_primary_key": False,
                },
                {
                    "column_name": "usage_pct",
                    "data_type": "double precision",
                    "is_nullable": "YES",
                    "is_primary_key": False,
                },
            ],
            "primary_keys": ["id"],
            "foreign_keys": [
                {
                    "from_column": "server_id",
                    "to_table": "servers",
                    "to_column": "id",
                }
            ],
        }

        mock_text_content = MagicMock()
        mock_text_content.text = json.dumps(schema_data)
        mock_result = MagicMock()
        mock_result.content = [mock_text_content]

        table_info = dbhub_client._parse_table_schema(mock_result)

        assert isinstance(table_info, TableInfo)
        assert table_info.name == "cpu_metrics"
        assert len(table_info.columns) == 3

        # PK 컬럼 확인
        id_col = next(c for c in table_info.columns if c.name == "id")
        assert id_col.is_primary_key is True

        # FK 컬럼 확인
        server_id_col = next(
            c for c in table_info.columns if c.name == "server_id"
        )
        assert server_id_col.is_foreign_key is True
        assert server_id_col.references == "servers.id"

        # nullable 확인
        usage_col = next(
            c for c in table_info.columns if c.name == "usage_pct"
        )
        assert usage_col.nullable is True
        assert usage_col.data_type == "double precision"

    async def test_client_parse_schema_error(
        self, dbhub_client: DBHubClient
    ) -> None:
        """스키마 조회 에러 응답이 DBHubError로 변환된다."""
        mock_text_content = MagicMock()
        mock_text_content.text = json.dumps({
            "error": "relation 'nonexistent' does not exist"
        })
        mock_result = MagicMock()
        mock_result.content = [mock_text_content]

        with pytest.raises(DBHubError) as exc_info:
            dbhub_client._parse_table_schema(mock_result)

        assert "nonexistent" in str(exc_info.value)


# ============================================================================
# 테스트: 읽기 전용 위반 (INSERT/UPDATE/DELETE 거부)
# ============================================================================


class TestReadOnlyViolation:
    """읽기 전용 위반 시 에러 응답을 확인한다."""

    @pytest.mark.parametrize(
        "sql,keyword",
        [
            ("INSERT INTO servers (hostname) VALUES ('test')", "INSERT"),
            ("UPDATE servers SET hostname = 'test' WHERE id = 1", "UPDATE"),
            ("DELETE FROM servers WHERE id = 1", "DELETE"),
            ("DROP TABLE servers", "DROP"),
            ("ALTER TABLE servers ADD COLUMN test VARCHAR(10)", "ALTER"),
            ("TRUNCATE TABLE servers", "TRUNCATE"),
            ("CREATE TABLE test (id INT)", "CREATE"),
        ],
        ids=[
            "insert",
            "update",
            "delete",
            "drop",
            "alter",
            "truncate",
            "create",
        ],
    )
    async def test_readonly_violation_returns_error(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        sql: str,
        keyword: str,
    ) -> None:
        """읽기 전용 소스에 대해 변경 SQL이 거부된다."""
        result_json = await mcp_tools["execute_sql"](
            source="infra_db", sql=sql, ctx=mock_ctx
        )
        result = json.loads(result_json)

        assert "error" in result
        assert "읽기 전용 위반" in result["error"]

    async def test_select_is_allowed(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """SELECT 문은 정상적으로 실행된다."""
        mock_pool_manager.execute = AsyncMock(
            return_value=[{"count": 42}]
        )

        result_json = await mcp_tools["execute_sql"](
            source="infra_db",
            sql="SELECT COUNT(*) AS count FROM servers",
            ctx=mock_ctx,
        )
        result = json.loads(result_json)

        assert "error" not in result
        assert result["rows"][0]["count"] == 42

    async def test_non_readonly_source_allows_write(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """readonly=False인 소스에서는 변경 SQL이 허용된다."""
        writable_source = _make_source_config(readonly=False)
        mock_pool_manager.get_source_config.return_value = writable_source
        mock_pool_manager.execute = AsyncMock(return_value=[])

        result_json = await mcp_tools["execute_sql"](
            source="infra_db",
            sql="INSERT INTO servers (hostname) VALUES ('test')",
            ctx=mock_ctx,
        )
        result = json.loads(result_json)

        # readonly=False이므로 에러 없이 실행됨
        assert "error" not in result


# ============================================================================
# 테스트: 타임아웃 처리
# ============================================================================


class TestTimeout:
    """타임아웃 처리를 검증한다."""

    async def test_client_execute_sql_timeout(
        self, dbhub_client: DBHubClient
    ) -> None:
        """MCP 호출 타임아웃 초과 시 QueryTimeoutError가 발생한다."""
        # mcp_call_timeout=10초로 설정된 클라이언트에서
        # call_tool이 오래 걸리면 타임아웃 발생

        async def slow_call(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(20)

        dbhub_client._mcp_session.call_tool = slow_call

        with pytest.raises(QueryTimeoutError) as exc_info:
            await dbhub_client.execute_sql("SELECT * FROM servers")

        assert "타임아웃" in str(exc_info.value)

    async def test_client_health_check_timeout(
        self, dbhub_client: DBHubClient
    ) -> None:
        """health_check가 HEALTH_CHECK_TIMEOUT 내에 응답하지 않으면 False를 반환한다."""
        async def slow_call(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(10)

        dbhub_client._mcp_session.call_tool = slow_call

        # health_check는 HEALTH_CHECK_TIMEOUT(5초) 이내에 응답해야 함
        result = await dbhub_client.health_check()
        assert result is False


# ============================================================================
# 테스트: 재연결 로직
# ============================================================================


class TestReconnection:
    """연결 실패 시 재연결 로직을 검증한다."""

    async def test_reconnect_on_disconnected_state(self) -> None:
        """연결이 끊긴 상태에서 execute_sql 호출 시 재연결을 시도한다."""
        config = DBHubConfig(
            server_url="http://localhost:9090/sse",
            source_name="infra_db",
            mcp_call_timeout=10,
        )
        client = DBHubClient(config)
        # 연결되지 않은 상태
        client._connected = False
        client._mcp_session = None

        connect_count = 0

        async def mock_connect() -> None:
            nonlocal connect_count
            connect_count += 1
            client._connected = True
            client._mcp_session = AsyncMock()

            # connect 성공 후 call_tool이 정상 결과 반환하도록 설정
            mock_text = MagicMock()
            mock_text.text = json.dumps({
                "columns": ["ok"],
                "rows": [{"ok": 1}],
                "row_count": 1,
                "truncated": False,
                "execution_time_ms": 1.0,
            })
            mock_result = MagicMock()
            mock_result.content = [mock_text]
            client._mcp_session.call_tool = AsyncMock(return_value=mock_result)

        client.connect = mock_connect

        result = await client.execute_sql("SELECT 1 AS ok")

        assert connect_count == 1
        assert result.row_count == 1

    async def test_reconnect_max_attempts_exceeded(self) -> None:
        """최대 재연결 시도(3회) 초과 시 DBConnectionError가 발생한다."""
        config = DBHubConfig(
            server_url="http://localhost:9090/sse",
            source_name="infra_db",
            mcp_call_timeout=10,
        )
        client = DBHubClient(config)
        client._connected = False
        client._mcp_session = None
        client.RECONNECT_DELAY = 0.01  # 테스트 속도를 위해 지연 최소화

        connect_attempts = 0

        async def failing_connect() -> None:
            nonlocal connect_attempts
            connect_attempts += 1
            raise Exception("Connection refused")

        client.connect = failing_connect

        with pytest.raises(DBConnectionError) as exc_info:
            await client.execute_sql("SELECT 1")

        assert connect_attempts == 3
        assert "재연결 실패" in str(exc_info.value)

    async def test_reconnect_succeeds_on_second_attempt(self) -> None:
        """첫 번째 연결 실패 후 두 번째에 성공한다."""
        config = DBHubConfig(
            server_url="http://localhost:9090/sse",
            source_name="infra_db",
            mcp_call_timeout=10,
        )
        client = DBHubClient(config)
        client._connected = False
        client._mcp_session = None
        client.RECONNECT_DELAY = 0.01

        connect_attempts = 0

        async def flaky_connect() -> None:
            nonlocal connect_attempts
            connect_attempts += 1
            if connect_attempts == 1:
                raise Exception("Temporary failure")
            # 두 번째 시도에서 성공
            client._connected = True
            client._mcp_session = AsyncMock()

            mock_text = MagicMock()
            mock_text.text = json.dumps({
                "columns": ["ok"],
                "rows": [{"ok": 1}],
                "row_count": 1,
                "truncated": False,
                "execution_time_ms": 1.0,
            })
            mock_result = MagicMock()
            mock_result.content = [mock_text]
            client._mcp_session.call_tool = AsyncMock(return_value=mock_result)

        client.connect = flaky_connect

        result = await client.execute_sql("SELECT 1 AS ok")

        assert connect_attempts == 2
        assert result.row_count == 1

    async def test_ensure_connected_skips_when_already_connected(
        self, dbhub_client: DBHubClient
    ) -> None:
        """이미 연결된 상태에서는 재연결을 시도하지 않는다."""
        connect_called = False

        async def mock_connect() -> None:
            nonlocal connect_called
            connect_called = True

        dbhub_client.connect = mock_connect

        await dbhub_client._ensure_connected_with_retry()

        assert connect_called is False


# ============================================================================
# 테스트: 클라이언트 _parse_json_result 공통 파서
# ============================================================================


class TestParseJsonResult:
    """_parse_json_result 공통 파서의 다양한 입력 처리를 검증한다."""

    def test_none_input(self, dbhub_client: DBHubClient) -> None:
        """None 입력 시 빈 dict를 반환한다."""
        assert dbhub_client._parse_json_result(None) == {}

    def test_string_content(self, dbhub_client: DBHubClient) -> None:
        """문자열 content를 파싱한다."""
        mock_result = MagicMock()
        mock_result.content = json.dumps({"key": "value"})

        result = dbhub_client._parse_json_result(mock_result)
        assert result == {"key": "value"}

    def test_text_content_list(self, dbhub_client: DBHubClient) -> None:
        """TextContent 리스트를 파싱한다."""
        mock_text = MagicMock()
        mock_text.text = json.dumps({"status": "ok"})
        mock_result = MagicMock()
        mock_result.content = [mock_text]

        result = dbhub_client._parse_json_result(mock_result)
        assert result == {"status": "ok"}

    def test_invalid_json(self, dbhub_client: DBHubClient) -> None:
        """유효하지 않은 JSON 입력 시 빈 dict를 반환한다."""
        mock_result = MagicMock()
        mock_result.content = "not valid json {"

        result = dbhub_client._parse_json_result(mock_result)
        assert result == {}


# ============================================================================
# 테스트: 클라이언트 _parse_table_list 엣지 케이스
# ============================================================================


class TestParseTableListEdgeCases:
    """_parse_table_list 파서의 엣지 케이스를 검증한다."""

    def test_none_result(self, dbhub_client: DBHubClient) -> None:
        """None 결과 시 빈 리스트를 반환한다."""
        tables = dbhub_client._parse_table_list(None)
        assert tables == []

    def test_error_response_returns_empty(
        self, dbhub_client: DBHubClient
    ) -> None:
        """에러 응답 시 빈 리스트를 반환한다 (경고 로깅)."""
        mock_text = MagicMock()
        mock_text.text = json.dumps({"error": "permission denied"})
        mock_result = MagicMock()
        mock_result.content = [mock_text]

        tables = dbhub_client._parse_table_list(mock_result)
        assert tables == []

    def test_single_dict_result(self, dbhub_client: DBHubClient) -> None:
        """단일 dict 결과도 TableInfo로 변환된다."""
        mock_text = MagicMock()
        mock_text.text = json.dumps({"name": "servers", "schema": "public"})
        mock_result = MagicMock()
        mock_result.content = [mock_text]

        tables = dbhub_client._parse_table_list(mock_result)
        assert len(tables) == 1
        assert tables[0].name == "servers"


# ============================================================================
# 테스트: 클라이언트 연결 상태 검증
# ============================================================================


class TestConnectionState:
    """연결 상태 검증 로직을 테스트한다."""

    def test_ensure_connected_raises_when_disconnected(self) -> None:
        """연결되지 않은 상태에서 _ensure_connected 호출 시 예외가 발생한다."""
        config = DBHubConfig(
            server_url="http://localhost:9090/sse",
            source_name="infra_db",
        )
        client = DBHubClient(config)

        with pytest.raises(DBConnectionError) as exc_info:
            client._ensure_connected()

        assert "연결되지 않았습니다" in str(exc_info.value)

    async def test_search_objects_requires_connection(self) -> None:
        """search_objects는 연결이 필요하다."""
        config = DBHubConfig(
            server_url="http://localhost:9090/sse",
            source_name="infra_db",
        )
        client = DBHubClient(config)

        with pytest.raises(DBConnectionError):
            await client.search_objects()

    async def test_get_table_schema_requires_connection(self) -> None:
        """get_table_schema는 연결이 필요하다."""
        config = DBHubConfig(
            server_url="http://localhost:9090/sse",
            source_name="infra_db",
        )
        client = DBHubClient(config)

        with pytest.raises(DBConnectionError):
            await client.get_table_schema("servers")

    async def test_call_tool_requires_session(
        self, dbhub_client: DBHubClient
    ) -> None:
        """_call_tool은 MCP 세션이 초기화되어야 한다."""
        dbhub_client._mcp_session = None

        with pytest.raises(DBConnectionError) as exc_info:
            await dbhub_client._call_tool("test", {})

        assert "세션이 초기화되지 않았습니다" in str(exc_info.value)


# ============================================================================
# 테스트: 테이블명 검증 (SQL 인젝션 방어)
# ============================================================================


class TestTableNameValidation:
    """get_table_schema의 테이블명 검증을 테스트한다."""

    async def test_valid_table_name(
        self, dbhub_client: DBHubClient
    ) -> None:
        """유효한 테이블명은 통과한다."""
        schema_data = {
            "table_name": "servers",
            "columns": [],
            "primary_keys": [],
            "foreign_keys": [],
        }
        mock_text = MagicMock()
        mock_text.text = json.dumps(schema_data)
        mock_result = MagicMock()
        mock_result.content = [mock_text]
        dbhub_client._mcp_session.call_tool = AsyncMock(
            return_value=mock_result
        )

        result = await dbhub_client.get_table_schema("servers")
        assert result.name == "servers"

    async def test_valid_schema_qualified_name(
        self, dbhub_client: DBHubClient
    ) -> None:
        """스키마 수식 테이블명(public.servers)은 통과한다."""
        schema_data = {
            "table_name": "public.servers",
            "columns": [],
            "primary_keys": [],
            "foreign_keys": [],
        }
        mock_text = MagicMock()
        mock_text.text = json.dumps(schema_data)
        mock_result = MagicMock()
        mock_result.content = [mock_text]
        dbhub_client._mcp_session.call_tool = AsyncMock(
            return_value=mock_result
        )

        result = await dbhub_client.get_table_schema("public.servers")
        assert result.name == "public.servers"

    @pytest.mark.parametrize(
        "invalid_name",
        [
            "servers; DROP TABLE users",
            "servers' OR '1'='1",
            "1invalid",
            "table name with spaces",
            "",
        ],
        ids=[
            "sql_injection_semicolon",
            "sql_injection_quote",
            "starts_with_number",
            "contains_spaces",
            "empty_string",
        ],
    )
    async def test_invalid_table_name_rejected(
        self, dbhub_client: DBHubClient, invalid_name: str
    ) -> None:
        """유효하지 않은 테이블명은 DBHubError로 거부된다."""
        with pytest.raises(DBHubError) as exc_info:
            await dbhub_client.get_table_schema(invalid_name)

        assert "유효하지 않은 테이블명" in str(exc_info.value)


# ============================================================================
# 테스트: end-to-end 서버 도구 -> 클라이언트 파서 흐름
# ============================================================================


class TestEndToEndFlow:
    """서버 도구 출력을 클라이언트 파서에 직접 전달하는 end-to-end 흐름을 검증한다."""

    async def test_search_objects_to_parse_table_list(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
        dbhub_client: DBHubClient,
    ) -> None:
        """서버 search_objects 출력이 클라이언트 _parse_table_list로 올바르게 변환된다."""
        mock_pool_manager.execute = AsyncMock(
            return_value=[
                {"name": "servers", "schema": "public"},
                {"name": "cpu_metrics", "schema": "public"},
            ]
        )

        # 서버 도구 호출 (JSON 문자열 반환)
        server_json = await mcp_tools["search_objects"](
            source="infra_db", pattern="*", type="table", ctx=mock_ctx
        )

        # 클라이언트 파서에 전달 (MCP TextContent 형식으로 감싸기)
        mock_text = MagicMock()
        mock_text.text = server_json
        mock_result = MagicMock()
        mock_result.content = [mock_text]

        tables = dbhub_client._parse_table_list(mock_result)

        assert len(tables) == 2
        assert tables[0].name == "servers"
        assert tables[1].name == "cpu_metrics"

    async def test_execute_sql_to_parse_query_result(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
        dbhub_client: DBHubClient,
    ) -> None:
        """서버 execute_sql 출력이 클라이언트 _parse_query_result로 올바르게 변환된다."""
        mock_pool_manager.execute = AsyncMock(
            return_value=[
                {"hostname": "web-01", "usage_pct": 85.3},
                {"hostname": "db-01", "usage_pct": 91.2},
            ]
        )

        server_json = await mcp_tools["execute_sql"](
            source="infra_db",
            sql="SELECT hostname, usage_pct FROM servers",
            ctx=mock_ctx,
        )

        mock_text = MagicMock()
        mock_text.text = server_json
        mock_result = MagicMock()
        mock_result.content = [mock_text]

        query_result = dbhub_client._parse_query_result(mock_result)

        assert isinstance(query_result, QueryResult)
        assert query_result.row_count == 2
        assert query_result.rows[0]["hostname"] == "web-01"
        assert query_result.rows[1]["usage_pct"] == 91.2
        assert "hostname" in query_result.columns
        assert query_result.truncated is False

    async def test_get_table_schema_to_parse_table_schema(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
        dbhub_client: DBHubClient,
    ) -> None:
        """서버 get_table_schema 출력이 클라이언트 _parse_table_schema로 올바르게 변환된다."""
        columns = [
            {
                "column_name": "id",
                "data_type": "integer",
                "is_nullable": "NO",
                "column_default": None,
            },
            {
                "column_name": "server_id",
                "data_type": "integer",
                "is_nullable": "NO",
                "column_default": None,
            },
            {
                "column_name": "usage_pct",
                "data_type": "double precision",
                "is_nullable": "YES",
                "column_default": None,
            },
        ]
        pk = [{"column_name": "id"}]
        fk = [
            {
                "from_column": "server_id",
                "to_table": "servers",
                "to_column": "id",
            }
        ]

        mock_pool_manager.execute = AsyncMock(
            side_effect=[columns, pk, fk]
        )

        server_json = await mcp_tools["get_table_schema"](
            source="infra_db", table_name="cpu_metrics", ctx=mock_ctx
        )

        mock_text = MagicMock()
        mock_text.text = server_json
        mock_result = MagicMock()
        mock_result.content = [mock_text]

        table_info = dbhub_client._parse_table_schema(mock_result)

        assert isinstance(table_info, TableInfo)
        assert table_info.name == "cpu_metrics"
        assert len(table_info.columns) == 3

        # PK 확인
        id_col = next(c for c in table_info.columns if c.name == "id")
        assert id_col.is_primary_key is True

        # FK 확인
        server_id_col = next(
            c for c in table_info.columns if c.name == "server_id"
        )
        assert server_id_col.is_foreign_key is True
        assert server_id_col.references == "servers.id"

        # nullable 확인
        usage_col = next(
            c for c in table_info.columns if c.name == "usage_pct"
        )
        assert usage_col.nullable is True

    async def test_readonly_violation_to_parse_query_result(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        dbhub_client: DBHubClient,
    ) -> None:
        """서버의 읽기 전용 위반 에러가 클라이언트에서 QueryExecutionError로 변환된다."""
        server_json = await mcp_tools["execute_sql"](
            source="infra_db",
            sql="DELETE FROM servers WHERE id = 1",
            ctx=mock_ctx,
        )

        mock_text = MagicMock()
        mock_text.text = server_json
        mock_result = MagicMock()
        mock_result.content = [mock_text]

        with pytest.raises(QueryExecutionError) as exc_info:
            dbhub_client._parse_query_result(mock_result)

        assert "읽기 전용 위반" in str(exc_info.value)
