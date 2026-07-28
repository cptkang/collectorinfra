"""?대씪?댁뼵??- MCP ?쒕쾭 ?곕룞 ?듯빀 ?뚯뒪??

DB ?곌껐 ?놁씠 DBPoolManager瑜?mock?섏뿬
MCP ?쒕쾭 ?꾧뎄 ?⑥닔?� DBHubClient ?뚯꽌??end-to-end ?먮쫫??寃�利앺븳??

?뚯뒪???�??
- MCP ?쒕쾭 ?꾧뎄 5媛?(list_sources, health_check, search_objects, execute_sql, get_table_schema)
- DBHubClient??寃곌낵 ?뚯떛 濡쒖쭅 (_parse_table_list, _parse_table_schema, _parse_query_result)
- ?쎄린 ?꾩슜 ?꾨컲 嫄곕?
- ?�?꾩븘??泥섎━
- ?ъ뿰寃?濡쒖쭅
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# mcp_server ?⑦궎吏�???낅┰ ?쒕툕?⑦궎吏�(mcp_server/)濡?pip install ?놁씠 ?ъ슜?섎?濡?
# sys.path??mcp_server/ ?붾젆?좊━瑜?異붽??섏뿬 import媛� 媛�?ν븯寃??쒕떎.
_MCP_SERVER_ROOT = str(Path(__file__).resolve().parent.parent / "mcp_server")
if _MCP_SERVER_ROOT not in sys.path:
    sys.path.insert(0, _MCP_SERVER_ROOT)

from mcp_server.config import SourceConfig
from mcp_server.db import DBPoolManager
from mcp_server.security import ReadOnlyViolationError

# --- MCP ?쒕쾭 ?꾧뎄 ?⑥닔 ?꾪룷??(register_tools ?대? ?⑥닔 吏곸젒 ?몄텧 遺덇??섎?濡?
#     tools 紐⑤뱢???ы띁 + register_tools ???꾧뎄 ?⑥닔瑜?異붿텧?섎뒗 諛⑹떇 ?ъ슜) ---
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
# Fixture: ?뚯뒪?몄슜 SourceConfig
# ============================================================================


def _make_source_config(
    name: str = "infra_db",
    db_type: str = "postgresql",
    readonly: bool = True,
    max_rows: int = 10000,
    query_timeout: int = 30,
) -> SourceConfig:
    """?뚯뒪?몄슜 SourceConfig瑜??앹꽦?쒕떎."""
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
    """DB ?곌껐 ?놁씠 ?숈옉?섎뒗 mock DBPoolManager瑜??앹꽦?쒕떎.

    Returns:
        紐⑤뱺 硫붿꽌?쒓? mock??DBPoolManager ?몄뒪?댁뒪
    """
    pm = MagicMock(spec=DBPoolManager)

    source_config = _make_source_config()

    # 湲곕낯 硫붿꽌??mock
    pm.get_active_sources.return_value = ["infra_db"]
    pm.is_source_active.side_effect = lambda name: name == "infra_db"
    pm.get_source_config.return_value = source_config
    pm.get_source_type.return_value = "postgresql"

    # async 硫붿꽌??mock
    pm.execute = AsyncMock(return_value=[])
    pm.health_check = AsyncMock(return_value=True)

    return pm


# ============================================================================
# Fixture: mock MCP Context
# ============================================================================


@pytest.fixture
def mock_ctx(mock_pool_manager: DBPoolManager) -> MagicMock:
    """MCP Context瑜?mock?섏뿬 pool_manager瑜?二쇱엯?쒕떎.

    Args:
        mock_pool_manager: mock??DBPoolManager

    Returns:
        pool_manager媛� 二쇱엯??mock Context
    """
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {
        "pool_manager": mock_pool_manager,
        "config": MagicMock(),
    }
    return ctx


# ============================================================================
# Fixture: MCP ?쒕쾭 ?꾧뎄 ?⑥닔 異붿텧
# ============================================================================


@pytest.fixture
def mcp_tools() -> dict[str, Any]:
    """FastMCP???깅줉???꾧뎄 ?⑥닔?ㅼ쓣 異붿텧?쒕떎.

    register_tools()媛� @mcp.tool() ?곗퐫?덉씠?곕줈 ?깅줉?섎뒗 ?⑥닔?ㅼ쓣
    罹≪쿂?섏뿬 dict濡?諛섑솚?쒕떎.

    Returns:
        ?꾧뎄 ?대쫫 -> ?⑥닔 留ㅽ븨
    """
    tools: dict[str, Any] = {}

    mock_mcp = MagicMock()

    def capture_tool(*args: Any, **kwargs: Any) -> Any:
        """@mcp.tool() ?곗퐫?덉씠?곕? 媛�濡쒖콈???⑥닔瑜?罹≪쿂?쒕떎."""
        def decorator(func: Any) -> Any:
            tools[func.__name__] = func
            return func
        return decorator

    mock_mcp.tool = capture_tool
    # execute_sql은 D-122(2-A)로 기본 비노출(expose_execute_sql=False) — 이 통합 테스트는
    # execute_sql 흐름을 검증하므로 배치 config.toml(true)과 동일하게 명시 opt-in한다.
    register_tools(mock_mcp, expose_execute_sql=True)
    return tools


# ============================================================================
# Fixture: DBHubClient (mock ?곌껐)
# ============================================================================


@pytest.fixture
def dbhub_client() -> DBHubClient:
    """?뚯뒪?몄슜 DBHubClient瑜??앹꽦?쒕떎.

    MCP ?몄뀡??mock?섏뿬 ?ㅼ젣 ?쒕쾭 ?곌껐 ?놁씠 ?뚯꽌 濡쒖쭅???뚯뒪?명븳??

    Returns:
        mock ?몄뀡???ㅼ젙??DBHubClient
    """
    config = DBHubConfig(
        server_url="http://localhost:9099/sse",
        source_name="infra_db",
        mcp_call_timeout=10,
    )
    query_config = QueryConfig(max_retry_count=3, default_limit=1000)
    client = DBHubClient(config, query_config)
    client._connected = True
    client._mcp_session = AsyncMock()
    return client


# ============================================================================
# ?뚯뒪?? list_sources
# ============================================================================


class TestListSources:
    """list_sources ?꾧뎄 ?몄텧 諛??쒖꽦 ?뚯뒪 紐⑸줉 諛섑솚 ?뚯뒪??"""

    async def test_returns_active_sources(
        self, mcp_tools: dict, mock_ctx: MagicMock
    ) -> None:
        """?쒖꽦 ?뚯뒪 紐⑸줉??JSON?쇰줈 諛섑솚?쒕떎."""
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
        """?뚯뒪 ?ㅼ젙(query_timeout, max_rows)???ы븿?쒕떎."""
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
        """?щ윭 ?뚯뒪媛� ?깅줉??寃쎌슦 紐⑤몢 諛섑솚?쒕떎."""
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
# ?뚯뒪?? health_check
# ============================================================================


class TestHealthCheck:
    """health_check ?꾧뎄 ?몄텧 諛??뺤긽/鍮꾩젙???묐떟 泥섎━ ?뚯뒪??"""

    async def test_healthy_source(
        self, mcp_tools: dict, mock_ctx: MagicMock
    ) -> None:
        """?뺤긽 ?뚯뒪???�??healthy ?곹깭瑜?諛섑솚?쒕떎."""
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
        """鍮꾩젙???뚯뒪???�??unhealthy ?곹깭瑜?諛섑솚?쒕떎."""
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
        """誘몃벑濡??뚯뒪???�??not_found ?곹깭瑜?諛섑솚?쒕떎."""
        mock_pool_manager.is_source_active.return_value = False

        result_json = await mcp_tools["health_check"](
            source="unknown_db", ctx=mock_ctx
        )
        result = json.loads(result_json)

        assert result["status"] == "not_found"
        assert "unknown_db" in result["message"]


# ============================================================================
# ?뚯뒪?? search_objects
# ============================================================================


class TestSearchObjects:
    """search_objects ?꾧뎄 ?몄텧 諛?TableInfo 蹂�???뚯뒪??"""

    async def test_returns_table_list(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """?뚯씠釉?紐⑸줉??JSON?쇰줈 諛섑솚?쒕떎."""
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
        """?⑦꽩 ?꾪꽣留곸쑝濡??뱀젙 ?뚯씠釉붾쭔 諛섑솚?쒕떎."""
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
        """?대씪?댁뼵?멸? search_objects 寃곌낵瑜?TableInfo 紐⑸줉?쇰줈 蹂�?섑븳??"""
        # MCP 寃곌낵 ?뺤떇???쒕??덉씠??(TextContent 援ъ“)
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
        """DB ?먮윭 諛쒖깮 ???먮윭 JSON??諛섑솚?쒕떎."""
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
# ?뚯뒪?? execute_sql
# ============================================================================


class TestExecuteSql:
    """execute_sql ?꾧뎄 ?몄텧 諛?QueryResult 蹂�???뚯뒪??"""

    async def test_returns_query_result(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
    ) -> None:
        """荑쇰━ 寃곌낵媛� columns, rows, row_count ?뺤떇?쇰줈 諛섑솚?쒕떎."""
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
        """max_rows 珥덇낵 ??寃곌낵媛� ?섎━怨?truncated=True媛� ?쒕떎."""
        # max_rows=2???뚯뒪 ?ㅼ젙
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
        """鍮?寃곌낵???뺤긽?곸쑝濡?泥섎━?쒕떎."""
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
        """?대씪?댁뼵?멸? execute_sql 寃곌낵瑜?QueryResult濡?蹂�?섑븳??"""
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
        """?먮윭 ?묐떟??QueryExecutionError濡?蹂�?섎맂??"""
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
        """SQL ?ㅽ뻾 ?먮윭 ???먮윭 JSON??諛섑솚?쒕떎."""
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
# ?뚯뒪?? get_table_schema
# ============================================================================


class TestGetTableSchema:
    """get_table_schema ?꾧뎄 ?몄텧 諛?TableInfo (而щ읆, PK, FK) 蹂�???뚯뒪??"""

    @pytest.fixture
    def schema_mock_data(self) -> dict[str, list[dict]]:
        """get_table_schema ?뚯뒪?몄슜 mock ?곗씠?곕? 諛섑솚?쒕떎."""
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
        """?뚯씠釉??ㅽ궎留덇? 而щ읆, PK, FK ?뺣낫瑜??ы븿?섏뿬 諛섑솚?쒕떎."""
        # execute ?몄텧 ???쒖꽌?�濡?columns, PK, FK 寃곌낵 諛섑솚
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

        # PK 而щ읆 ?쒖떆 ?뺤씤
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
        """FK媛� ?덈뒗 ?뚯씠釉붿쓽 ?ㅽ궎留덇? FK 愿�怨꾨? ?ы븿?쒕떎."""
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
        """?대씪?댁뼵?멸? get_table_schema 寃곌낵瑜?TableInfo濡?蹂�?섑븳??"""
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

        # PK 而щ읆 ?뺤씤
        id_col = next(c for c in table_info.columns if c.name == "id")
        assert id_col.is_primary_key is True

        # FK 而щ읆 ?뺤씤
        server_id_col = next(
            c for c in table_info.columns if c.name == "server_id"
        )
        assert server_id_col.is_foreign_key is True
        assert server_id_col.references == "servers.id"

        # nullable ?뺤씤
        usage_col = next(
            c for c in table_info.columns if c.name == "usage_pct"
        )
        assert usage_col.nullable is True
        assert usage_col.data_type == "double precision"

    async def test_client_parse_schema_error(
        self, dbhub_client: DBHubClient
    ) -> None:
        """?ㅽ궎留?議고쉶 ?먮윭 ?묐떟??DBHubError濡?蹂�?섎맂??"""
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
# ?뚯뒪?? ?쎄린 ?꾩슜 ?꾨컲 (INSERT/UPDATE/DELETE 嫄곕?)
# ============================================================================


class TestReadOnlyViolation:
    """?쎄린 ?꾩슜 ?꾨컲 ???먮윭 ?묐떟???뺤씤?쒕떎."""

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
        """?쎄린 ?꾩슜 ?뚯뒪???�??蹂�寃?SQL??嫄곕??쒕떎."""
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
        """SELECT 臾몄? ?뺤긽?곸쑝濡??ㅽ뻾?쒕떎."""
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
        """readonly=False???뚯뒪?먯꽌??蹂�寃?SQL???덉슜?쒕떎."""
        writable_source = _make_source_config(readonly=False)
        mock_pool_manager.get_source_config.return_value = writable_source
        mock_pool_manager.execute = AsyncMock(return_value=[])

        result_json = await mcp_tools["execute_sql"](
            source="infra_db",
            sql="INSERT INTO servers (hostname) VALUES ('test')",
            ctx=mock_ctx,
        )
        result = json.loads(result_json)

        # readonly=False?대?濡??먮윭 ?놁씠 ?ㅽ뻾??
        assert "error" not in result


# ============================================================================
# ?뚯뒪?? ?�?꾩븘??泥섎━
# ============================================================================


class TestTimeout:
    """?�?꾩븘??泥섎━瑜?寃�利앺븳??"""

    async def test_client_execute_sql_timeout(
        self, dbhub_client: DBHubClient
    ) -> None:
        """MCP ?몄텧 ?�?꾩븘??珥덇낵 ??QueryTimeoutError媛� 諛쒖깮?쒕떎."""
        # mcp_call_timeout=10珥덈줈 ?ㅼ젙???대씪?댁뼵?몄뿉??
        # call_tool???ㅻ옒 嫄몃━硫??�?꾩븘??諛쒖깮

        async def slow_call(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(20)

        dbhub_client._mcp_session.call_tool = slow_call

        with pytest.raises(QueryTimeoutError) as exc_info:
            await dbhub_client.execute_sql("SELECT * FROM servers")

        assert "타임아웃" in str(exc_info.value)

    async def test_client_health_check_timeout(
        self, dbhub_client: DBHubClient
    ) -> None:
        """health_check媛� HEALTH_CHECK_TIMEOUT ?댁뿉 ?묐떟?섏? ?딆쑝硫?False瑜?諛섑솚?쒕떎."""
        async def slow_call(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(10)

        dbhub_client._mcp_session.call_tool = slow_call

        # health_check??HEALTH_CHECK_TIMEOUT(5珥? ?대궡???묐떟?댁빞 ??
        result = await dbhub_client.health_check()
        assert result is False


# ============================================================================
# ?뚯뒪?? ?ъ뿰寃?濡쒖쭅
# ============================================================================


class TestReconnection:
    """?곌껐 ?ㅽ뙣 ???ъ뿰寃?濡쒖쭅??寃�利앺븳??"""

    async def test_reconnect_on_disconnected_state(self) -> None:
        """?곌껐???딄릿 ?곹깭?먯꽌 execute_sql ?몄텧 ???ъ뿰寃곗쓣 ?쒕룄?쒕떎."""
        config = DBHubConfig(
            server_url="http://localhost:9099/sse",
            source_name="infra_db",
            mcp_call_timeout=10,
        )
        client = DBHubClient(config)
        # ?곌껐?섏? ?딆? ?곹깭
        client._connected = False
        client._mcp_session = None

        connect_count = 0

        async def mock_connect() -> None:
            nonlocal connect_count
            connect_count += 1
            client._connected = True
            client._mcp_session = AsyncMock()

            # connect ?깃났 ??call_tool???뺤긽 寃곌낵 諛섑솚?섎룄濡??ㅼ젙
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
        """理쒕? ?ъ뿰寃??쒕룄(3?? 珥덇낵 ??DBConnectionError媛� 諛쒖깮?쒕떎."""
        config = DBHubConfig(
            server_url="http://localhost:9099/sse",
            source_name="infra_db",
            mcp_call_timeout=10,
        )
        client = DBHubClient(config)
        client._connected = False
        client._mcp_session = None
        client.RECONNECT_DELAY = 0.01  # ?뚯뒪???띾룄瑜??꾪빐 吏�??理쒖냼??

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
        """泥?踰덉㎏ ?곌껐 ?ㅽ뙣 ????踰덉㎏???깃났?쒕떎."""
        config = DBHubConfig(
            server_url="http://localhost:9099/sse",
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
            # ??踰덉㎏ ?쒕룄?먯꽌 ?깃났
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
        """?대? ?곌껐???곹깭?먯꽌???ъ뿰寃곗쓣 ?쒕룄?섏? ?딅뒗??"""
        connect_called = False

        async def mock_connect() -> None:
            nonlocal connect_called
            connect_called = True

        dbhub_client.connect = mock_connect

        await dbhub_client._ensure_connected_with_retry()

        assert connect_called is False


# ============================================================================
# ?뚯뒪?? ?대씪?댁뼵??_parse_json_result 怨듯넻 ?뚯꽌
# ============================================================================


class TestParseJsonResult:
    """_parse_json_result 怨듯넻 ?뚯꽌???ㅼ뼇???낅젰 泥섎━瑜?寃�利앺븳??"""

    def test_none_input(self, dbhub_client: DBHubClient) -> None:
        """None ?낅젰 ??鍮?dict瑜?諛섑솚?쒕떎."""
        assert dbhub_client._parse_json_result(None) == {}

    def test_string_content(self, dbhub_client: DBHubClient) -> None:
        """臾몄옄??content瑜??뚯떛?쒕떎."""
        mock_result = MagicMock()
        mock_result.content = json.dumps({"key": "value"})

        result = dbhub_client._parse_json_result(mock_result)
        assert result == {"key": "value"}

    def test_text_content_list(self, dbhub_client: DBHubClient) -> None:
        """TextContent 由ъ뒪?몃? ?뚯떛?쒕떎."""
        mock_text = MagicMock()
        mock_text.text = json.dumps({"status": "ok"})
        mock_result = MagicMock()
        mock_result.content = [mock_text]

        result = dbhub_client._parse_json_result(mock_result)
        assert result == {"status": "ok"}

    def test_invalid_json(self, dbhub_client: DBHubClient) -> None:
        """?좏슚?섏? ?딆? JSON ?낅젰 ??鍮?dict瑜?諛섑솚?쒕떎."""
        mock_result = MagicMock()
        mock_result.content = "not valid json {"

        result = dbhub_client._parse_json_result(mock_result)
        assert result == {}


# ============================================================================
# ?뚯뒪?? ?대씪?댁뼵??_parse_table_list ?ｌ? 耳�?댁뒪
# ============================================================================


class TestParseTableListEdgeCases:
    """_parse_table_list ?뚯꽌???ｌ? 耳�?댁뒪瑜?寃�利앺븳??"""

    def test_none_result(self, dbhub_client: DBHubClient) -> None:
        """None 寃곌낵 ??鍮?由ъ뒪?몃? 諛섑솚?쒕떎."""
        tables = dbhub_client._parse_table_list(None)
        assert tables == []

    def test_error_response_returns_empty(
        self, dbhub_client: DBHubClient
    ) -> None:
        """?먮윭 ?묐떟 ??鍮?由ъ뒪?몃? 諛섑솚?쒕떎 (寃쎄퀬 濡쒓퉭)."""
        mock_text = MagicMock()
        mock_text.text = json.dumps({"error": "permission denied"})
        mock_result = MagicMock()
        mock_result.content = [mock_text]

        tables = dbhub_client._parse_table_list(mock_result)
        assert tables == []

    def test_single_dict_result(self, dbhub_client: DBHubClient) -> None:
        """?⑥씪 dict 寃곌낵??TableInfo濡?蹂�?섎맂??"""
        mock_text = MagicMock()
        mock_text.text = json.dumps({"name": "servers", "schema": "public"})
        mock_result = MagicMock()
        mock_result.content = [mock_text]

        tables = dbhub_client._parse_table_list(mock_result)
        assert len(tables) == 1
        assert tables[0].name == "servers"


# ============================================================================
# ?뚯뒪?? ?대씪?댁뼵???곌껐 ?곹깭 寃�利?
# ============================================================================


class TestConnectionState:
    """?곌껐 ?곹깭 寃�利?濡쒖쭅???뚯뒪?명븳??"""

    def test_ensure_connected_raises_when_disconnected(self) -> None:
        """?곌껐?섏? ?딆? ?곹깭?먯꽌 _ensure_connected ?몄텧 ???덉쇅媛� 諛쒖깮?쒕떎."""
        config = DBHubConfig(
            server_url="http://localhost:9099/sse",
            source_name="infra_db",
        )
        client = DBHubClient(config)

        with pytest.raises(DBConnectionError) as exc_info:
            client._ensure_connected()

        assert "연결되지 않았습니다" in str(exc_info.value)

    async def test_search_objects_requires_connection(self) -> None:
        """search_objects???곌껐???꾩슂?섎떎."""
        config = DBHubConfig(
            server_url="http://localhost:9099/sse",
            source_name="infra_db",
        )
        client = DBHubClient(config)

        with pytest.raises(DBConnectionError):
            await client.search_objects()

    async def test_get_table_schema_requires_connection(self) -> None:
        """get_table_schema???곌껐???꾩슂?섎떎."""
        config = DBHubConfig(
            server_url="http://localhost:9099/sse",
            source_name="infra_db",
        )
        client = DBHubClient(config)

        with pytest.raises(DBConnectionError):
            await client.get_table_schema("servers")

    async def test_call_tool_requires_session(
        self, dbhub_client: DBHubClient
    ) -> None:
        """_call_tool?� MCP ?몄뀡??珥덇린?붾릺?댁빞 ?쒕떎."""
        dbhub_client._mcp_session = None

        with pytest.raises(DBConnectionError) as exc_info:
            await dbhub_client._call_tool("test", {})

        assert "초기화되지 않았습니다" in str(exc_info.value)


# ============================================================================
# ?뚯뒪?? ?뚯씠釉붾챸 寃�利?(SQL ?몄젥??諛⑹뼱)
# ============================================================================


class TestTableNameValidation:
    """get_table_schema???뚯씠釉붾챸 寃�利앹쓣 ?뚯뒪?명븳??"""

    async def test_valid_table_name(
        self, dbhub_client: DBHubClient
    ) -> None:
        """?좏슚???뚯씠釉붾챸?� ?듦낵?쒕떎."""
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
        """?ㅽ궎留??섏떇 ?뚯씠釉붾챸(public.servers)?� ?듦낵?쒕떎."""
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
        """?좏슚?섏? ?딆? ?뚯씠釉붾챸?� DBHubError濡?嫄곕??쒕떎."""
        with pytest.raises(DBHubError) as exc_info:
            await dbhub_client.get_table_schema(invalid_name)

        assert "유효하지 않은 테이블명" in str(exc_info.value)


# ============================================================================
# ?뚯뒪?? end-to-end ?쒕쾭 ?꾧뎄 -> ?대씪?댁뼵???뚯꽌 ?먮쫫
# ============================================================================


class TestEndToEndFlow:
    """?쒕쾭 ?꾧뎄 異쒕젰???대씪?댁뼵???뚯꽌??吏곸젒 ?꾨떖?섎뒗 end-to-end ?먮쫫??寃�利앺븳??"""

    async def test_search_objects_to_parse_table_list(
        self,
        mcp_tools: dict,
        mock_ctx: MagicMock,
        mock_pool_manager: MagicMock,
        dbhub_client: DBHubClient,
    ) -> None:
        """?쒕쾭 search_objects 異쒕젰???대씪?댁뼵??_parse_table_list濡??щ컮瑜닿쾶 蹂�?섎맂??"""
        mock_pool_manager.execute = AsyncMock(
            return_value=[
                {"name": "servers", "schema": "public"},
                {"name": "cpu_metrics", "schema": "public"},
            ]
        )

        # ?쒕쾭 ?꾧뎄 ?몄텧 (JSON 臾몄옄??諛섑솚)
        server_json = await mcp_tools["search_objects"](
            source="infra_db", pattern="*", type="table", ctx=mock_ctx
        )

        # ?대씪?댁뼵???뚯꽌???꾨떖 (MCP TextContent ?뺤떇?쇰줈 媛먯떥湲?
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
        """?쒕쾭 execute_sql 異쒕젰???대씪?댁뼵??_parse_query_result濡??щ컮瑜닿쾶 蹂�?섎맂??"""
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
        """?쒕쾭 get_table_schema 異쒕젰???대씪?댁뼵??_parse_table_schema濡??щ컮瑜닿쾶 蹂�?섎맂??"""
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

        # PK ?뺤씤
        id_col = next(c for c in table_info.columns if c.name == "id")
        assert id_col.is_primary_key is True

        # FK ?뺤씤
        server_id_col = next(
            c for c in table_info.columns if c.name == "server_id"
        )
        assert server_id_col.is_foreign_key is True
        assert server_id_col.references == "servers.id"

        # nullable ?뺤씤
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
        """?쒕쾭???쎄린 ?꾩슜 ?꾨컲 ?먮윭媛� ?대씪?댁뼵?몄뿉??QueryExecutionError濡?蹂�?섎맂??"""
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
