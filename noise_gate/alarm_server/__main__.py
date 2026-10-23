"""alarm_server 진입점.

기동 방법:
    python -m noise_gate.alarm_server   (레포 루트에서)

설정 우선순위 (env 파일은 CWD 기준):
    OS 환경변수 > alarm_server.env (전용) > .env (공용) > 기본값
    — pydantic-settings는 env_file 목록 뒤 파일이 이긴다(실측) · 2026-10-21 순서 교정(D-345 부기).

환경변수 접두사: ALARM_SERVER_
    ALARM_SERVER_SOCKET_HOST   기본: 0.0.0.0
    ALARM_SERVER_SOCKET_PORT   기본: 9100
    ALARM_SERVER_REDIS_HOST    기본: localhost
    ALARM_SERVER_REDIS_PORT    기본: 6379
    ALARM_SERVER_STREAM_KEY    기본: alarm:raw
    ALARM_SERVER_LOG_LEVEL     기본: INFO
    ALARM_SERVER_ADMIN_PORT    기본: 없음 (토큰과 함께 있어야 설정 관리 리스너 기동)
    ALARM_SERVER_ADMIN_HOST    기본: 127.0.0.1
    ALARM_SERVER_ADMIN_TOKEN   기본: 없음

설정 관리 리스너(plans/191 · D-345): 관리 포트·토큰이 모두 있을 때만 같은 이벤트 루프에서
`/admin/config` 관리 앱(uvicorn)을 TCP 수신과 함께 돌린다. 관리 리스너가 실패해도(바인딩 실패 등)
경고만 남기고 TCP 수신은 계속한다. 미설정이면 코드 경로가 종전과 같다.
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn
from src.utils.log_masking import install_log_record_factory
from starlette.applications import Starlette

from noise_gate.alarm_server.config import AlarmServerConfig
from noise_gate.alarm_server.config_admin import build_admin_app
from noise_gate.alarm_server.config_admin_adapter import build_adapter
from noise_gate.alarm_server.tcp_receiver import TcpReceiver


def build_admin(config: AlarmServerConfig) -> Starlette | None:
    """관리 앱 — 관리 포트·토큰이 모두 있을 때만 만든다.

    한쪽만 있거나 관리 포트가 소켓 포트와 같으면 None을 돌려주고 경고 1줄을 남긴다.
    """
    logger = logging.getLogger(__name__)
    if config.admin_port is None and not config.admin_token:
        return None
    if config.admin_port is None or not config.admin_token:
        logger.warning(
            "설정 관리 리스너 미기동: "
            "ALARM_SERVER_ADMIN_PORT·ALARM_SERVER_ADMIN_TOKEN이 함께 필요하다"
        )
        return None
    if config.admin_port == config.socket_port:
        logger.warning(
            "설정 관리 리스너 미기동: 관리 포트(%s)가 알람 소켓 포트와 같다", config.admin_port
        )
        return None
    return build_admin_app(build_adapter(config), config.admin_token)


async def _run_admin(server: uvicorn.Server) -> None:
    """관리 리스너 실행. 실패(uvicorn 바인딩 실패의 `sys.exit` 포함)는 경고로 삼킨다.

    삼키지 않으면 SystemExit가 루프 밖으로 나가 TCP 수신까지 죽는다.
    """
    try:
        await server.serve()
    except (Exception, SystemExit):
        logging.getLogger(__name__).warning(
            "설정 관리 리스너 실패 — TCP 수신은 계속한다", exc_info=True
        )


async def serve_with_admin(config: AlarmServerConfig, admin_app: Starlette) -> None:
    """TCP 수신과 관리 리스너를 한 루프에서 돌린다. TCP 수신이 끝나면(예외 포함) 관리도 내린다."""
    server = uvicorn.Server(
        uvicorn.Config(
            admin_app,
            host=config.admin_host,
            port=config.admin_port or 0,
            log_level=config.log_level.lower(),
            log_config=None,  # 루트 basicConfig·비밀값 가림 팩토리를 그대로 쓴다
        )
    )
    admin_task = asyncio.create_task(_run_admin(server))
    try:
        await TcpReceiver(config).start()
    finally:
        server.should_exit = True
        await admin_task


def main() -> None:
    """alarm_server 메인 진입점."""
    config = AlarmServerConfig()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    install_log_record_factory()  # 로그 비밀값 가림(plans/167)
    logger = logging.getLogger(__name__)
    logger.info(
        "alarm_server 시작: stream_key=%s port=%s",
        config.stream_key,
        config.socket_port,
    )
    admin_app = build_admin(config)
    if admin_app is None:
        asyncio.run(TcpReceiver(config).start())
        return
    logger.info("설정 관리 리스너 기동: %s:%s", config.admin_host, config.admin_port)
    asyncio.run(serve_with_admin(config, admin_app))


if __name__ == "__main__":
    main()
