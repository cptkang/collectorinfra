"""entry — sre_agent 조사 서비스의 **유일한 기동 단위** (Plan 05 §2·§5).

FastMCP(SSE)를 uvicorn으로 기동한다. 기동 순서:
1. 감사 JSONL 기반 재기동 복구(running/accepted 잡 → failed(restart) 확정, 침묵 유실 금지).
2. 정적 Bearer 인증 미들웨어를 씌운 ASGI 앱 조립.
3. uvicorn 서빙.

수신부·게이트 엔트리는 통합으로 소멸했다 — 엔트리는 run_service 하나뿐이다(§2).
"""

from __future__ import annotations

import faulthandler
import logging
import signal

import uvicorn

from sre_agent.interface.mcp_service import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    build_asgi_app,
    create_service,
    get_job_store,
)
from sre_agent.settings import AgentSettings

logger = logging.getLogger(__name__)


def main() -> None:
    """조사 서비스를 기동한다."""
    logging.basicConfig(level=logging.INFO)

    # D-211 진단: SIGUSR1 수신 시 **전 스레드 파이썬 스택**을 stderr(journald)로
    # 덤프한다 — 잡이 running에서 멈췄을 때 어느 코드 줄에 매달렸는지 실측하는 유일한
    # 수단이다(py-spy 미반입 폐쇄망 전제). 전송은 `systemctl kill sre-agent --signal=USR1`.
    # SIGUSR1이 없는 플랫폼(Windows 개발)은 조용히 건너뛴다.
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, all_threads=True)

    settings = AgentSettings()

    mcp = create_service(settings)

    # 1. 재기동 복구 — 이전 프로세스가 남긴 running/accepted 잡을 failed(restart)로 확정.
    recovered = get_job_store(mcp).recover_on_start()
    if recovered:
        logger.warning("재기동 복구: running/accepted 잡 %d건을 failed(restart)로 확정", recovered)

    # 2. 정적 Bearer 인증(설정 시). 토큰 판정은 pydantic 필드로만 한다(os.getenv 금지).
    token = (
        settings.service_bearer_token.get_secret_value()
        if settings.service_bearer_token is not None
        else None
    )
    app = build_asgi_app(mcp, token)

    logger.info(
        "sre_agent 조사 서비스 기동: http://%s:%d/sse (인증=%s)",
        DEFAULT_HOST,
        DEFAULT_PORT,
        "on" if token else "off",
    )
    # 3. uvicorn 서빙.
    uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT)


if __name__ == "__main__":
    main()
