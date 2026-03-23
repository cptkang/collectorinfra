"""MCP 서버 엔트리포인트.

python -m mcp_server 로 실행한다.
"""

from __future__ import annotations

import logging
import sys


def main() -> None:
    """MCP 서버를 시작한다."""
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("mcp_server")

    from mcp_server.config import load_config

    config = load_config()

    logger.info(
        "MCP 서버 시작: %s (host=%s, port=%d, transport=%s)",
        config.server.name,
        config.server.host,
        config.server.port,
        config.server.transport,
    )

    from mcp_server.server import create_server

    server = create_server(config)

    # FastMCP의 run() 메서드를 사용하여 서버 실행
    server.run(transport=config.server.transport)


if __name__ == "__main__":
    main()
