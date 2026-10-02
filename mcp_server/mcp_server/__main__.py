"""MCP 서버 엔트리포인트.

python -m mcp_server 로 실행한다.
"""

from __future__ import annotations

import logging
import sys


def main() -> None:
    """MCP 서버를 시작한다."""
    from mcp_server.config import load_config

    config = load_config()

    # 로깅 설정 (.env의 SERVER_LOG_LEVEL 사용)
    log_level = getattr(logging, config.server.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    logger = logging.getLogger("mcp_server")

    logger.info(
        "MCP 서버 시작: %s (host=%s, port=%d, transport=%s)",
        config.server.name,
        config.server.host,
        config.server.port,
        config.server.transport,
    )

    from mcp_server.server import create_server

    server = create_server(config)

    # 전송 인증(Plan 04 §6-4): 정적 Bearer 토큰. 빈 값이면 None(무인증 통과).
    token = config.server.bearer_token or None

    if config.server.transport == "sse":
        # run(transport="sse")는 미들웨어 주입점이 없으므로, sse_app()에 인증
        # 미들웨어를 씌운 ASGI 앱을 uvicorn으로 직접 서빙한다(sre_agent run_service 전례).
        import uvicorn

        from mcp_server.server import build_asgi_app

        app = build_asgi_app(server, token)
        logger.info("전송 인증(정적 Bearer): %s", "on" if token else "off")
        uvicorn.run(
            app,
            host=config.server.host,
            port=config.server.port,
            log_level=config.server.log_level.lower(),
        )
    else:
        # stdio 등 비-HTTP transport는 전송 인증 대상이 아니다(FastMCP 기본 run).
        server.run(transport=config.server.transport)


if __name__ == "__main__":
    main()
