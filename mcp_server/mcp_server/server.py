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
    register_tools(mcp)

    logger.info(
        "MCP 서버 생성: name=%s, transport=%s",
        config.server.name,
        config.server.transport,
    )
    return mcp
