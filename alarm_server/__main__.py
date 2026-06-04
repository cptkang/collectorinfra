"""alarm_server 진입점.

기동 방법:
    python -m alarm_server

설정 우선순위:
    alarm_server.env (전용) > .env (공용) > 기본값

환경변수 접두사: ALARM_SERVER_
    ALARM_SERVER_SOCKET_HOST   기본: 0.0.0.0
    ALARM_SERVER_SOCKET_PORT   기본: 9100
    ALARM_SERVER_REDIS_HOST    기본: localhost
    ALARM_SERVER_REDIS_PORT    기본: 6379
    ALARM_SERVER_STREAM_KEY    기본: alarm:raw
    ALARM_SERVER_LOG_LEVEL     기본: INFO
"""

from __future__ import annotations

import asyncio
import logging

from alarm_server.config import AlarmServerConfig
from alarm_server.tcp_receiver import TcpReceiver


def main() -> None:
    """alarm_server 메인 진입점."""
    config = AlarmServerConfig()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info(
        "alarm_server 시작: stream_key=%s port=%s",
        config.stream_key,
        config.socket_port,
    )
    asyncio.run(TcpReceiver(config).start())


if __name__ == "__main__":
    main()
