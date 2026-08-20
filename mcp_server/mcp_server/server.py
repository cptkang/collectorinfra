"""FastMCP 서버 정의 + 도구 등록 + lifespan.

서버 시작 시 DB 풀을 초기화하고, 종료 시 정리한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.types import Receive, Scope, Send

from mcp_server import sql_log
from mcp_server.config import AppServerConfig, load_config
from mcp_server.db import DBPoolManager
from mcp_server.polestar_tools import register_polestar_tools
from mcp_server.promql_tools import register_promql_tools
from mcp_server.tools import register_tools

logger = logging.getLogger(__name__)


class StaticBearerAuthMiddleware:
    """정적 Bearer 토큰 검증 ASGI 미들웨어 (Plan 04 §6-4, D-015).

    token이 None이면(로컬/개발 — 네트워크 격리 전제) 검증을 통과시킨다. 설정되면
    모든 HTTP 요청에 `Authorization: Bearer <token>` 헤더를 요구하고, 불일치 시
    401을 반환한다. sre_agent 조사 서비스(Plan 05 §5)와 동일한 정적 Bearer 방식으로,
    협의 후 mTLS 승격은 본 범위 밖이다.
    """

    def __init__(self, app, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.token is None:
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        provided = headers.get(b"authorization", b"").decode("latin-1")
        if provided != f"Bearer {self.token}":
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps({"error": "unauthorized"}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def build_asgi_app(mcp: FastMCP, token: str | None) -> Starlette:
    """FastMCP SSE 앱에 정적 Bearer 인증 미들웨어를 씌워 반환한다 (Plan 04 §6-4).

    `mcp.run(transport="sse")`는 내부에서 자체 앱을 만들어 미들웨어 주입점이 없으므로,
    엔트리(`__main__`)는 이 함수로 앱을 조립해 uvicorn에 직접 넘긴다(sre_agent run_service 전례).

    Args:
        mcp: create_server가 생성한 FastMCP 인스턴스.
        token: 정적 Bearer 토큰. None이면 미들웨어가 검증 없이 통과(무인증).

    Returns:
        Bearer 인증 미들웨어가 씌워진 Starlette ASGI 앱.
    """
    app = mcp.sse_app()
    app.add_middleware(StaticBearerAuthMiddleware, token=token)
    return app


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """서버 시작 시 SQL 파일 로거·DB 풀 초기화, 종료 시 정리."""
    config = load_config()
    sql_log.init()  # 실행 SQL을 프로젝트 공용 logs/sql/에 기록 (D-140)
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
    register_tools(
        mcp,
        expose_execute_sql=config.server.expose_execute_sql,
        polestar_domain_guard=config.server.polestar_domain_guard,
    )
    # 폴스타 고수준 도구는 폴스타 소스를 서빙하는 배치에서만 필요하다(기본 등록 — 현행 동작 보존).
    if config.server.expose_polestar_tools:
        register_polestar_tools(mcp)
    else:
        logger.info("폴스타 고수준 도구 비노출 (expose_polestar_tools=False)")
    register_promql_tools(
        mcp, expose_raw_promql=config.prometheus.expose_raw_promql
    )

    logger.info(
        "MCP 서버 생성: name=%s, transport=%s, execute_sql노출=%s, raw_promql노출=%s, "
        "폴스타도구노출=%s, 폴스타도메인가드=%s",
        config.server.name,
        config.server.transport,
        config.server.expose_execute_sql,
        config.prometheus.expose_raw_promql,
        config.server.expose_polestar_tools,
        config.server.polestar_domain_guard,
    )
    return mcp
