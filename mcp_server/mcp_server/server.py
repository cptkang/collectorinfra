"""FastMCP 서버 정의 + 도구 등록 + lifespan.

서버 시작 시 DB 풀을 초기화하고, 종료 시 정리한다.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from mcp_server.config import AppServerConfig, load_config
from mcp_server.db import DBPoolManager
from mcp_server.polestar_tools import register_polestar_tools
from mcp_server.promql_tools import register_promql_tools
from mcp_server.tools import register_tools

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """서버 시작 시 DB 풀 초기화, 종료 시 정리."""
    config = load_config()
    pool_manager = DBPoolManager(config.sources)
    await pool_manager.initialize()

    logger.info(
        "MCP 서버 lifespan 시작: %d개 활성 소스",
        len(pool_manager.get_active_sources()),
    )

    try:
        yield {"pool_manager": pool_manager, "config": config}
    finally:
        await pool_manager.close_all()
        logger.info("MCP 서버 lifespan 종료: DB 풀 정리 완료")


def create_server(config: AppServerConfig | None = None) -> FastMCP:
    """FastMCP 서버 인스턴스를 생성한다.

    Args:
        config: 서버 설정 (없으면 기본 설정 로드)

    Returns:
        도구가 등록된 FastMCP 인스턴스
    """
    if config is None:
        config = load_config()

    mcp = FastMCP(
        config.server.name,
        host=config.server.host,
        port=config.server.port,
        lifespan=lifespan,
    )
    register_tools(mcp, expose_execute_sql=config.server.expose_execute_sql)
    register_polestar_tools(mcp)
    register_promql_tools(
        mcp, expose_raw_promql=config.prometheus.expose_raw_promql
    )

    logger.info(
        "MCP 서버 생성: name=%s, transport=%s, execute_sql노출=%s, raw_promql노출=%s",
        config.server.name,
        config.server.transport,
        config.server.expose_execute_sql,
        config.prometheus.expose_raw_promql,
    )
    return mcp
