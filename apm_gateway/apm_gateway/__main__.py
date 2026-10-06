"""게이트웨이 엔트리 — `cd apm_gateway && ../.venv/bin/python -m apm_gateway` (plans/87 §0.7 ·
SPEC-apm-gateway §2).

SSE MCP 서버(uvicorn · 주체별 Bearer)와 이벤트 폴러(옵트인)를 한 이벤트 루프에서 함께 돌린다. 기동
로그 1줄에 허용 경로 수 · 폴러 상태 · 소스 id와 설정 여부 · 호출 주체 이름을 남긴다(비밀 값 없음 —
R-20). 설정 오류(단일·다중 설정 동시 · 소스 필수 키 누락 · id 형식 · 같은 토큰 두 주체)는
기동 실패다(plans/87 J8 · D-287 ③ · plans/134 W0-B). 기동 시 작업 스풀을 훑어 남은 진행 중 작업을
interrupted로 바꾸고, 종료 시 진행 중 백그라운드 작업을 interrupted로 끝낸다(종료 감사 포함).

로그: HTTP·MCP 전송 라이브러리 로거는 경고 이상만 남긴다(로그 수준 설정과 무관).
- httpx·httpcore: INFO로 `HTTP Request: GET <전체 URL>`을 찍어 계정 ID 경로 변수·쿼리 값
  (`search`·`hostname`) 원값이 운영 기본 INFO 로그에 실린다(plans/134 W7 VG-7).
- MCP 전송(`mcp` 이하 — SSE 전송·저수준 서버 로거)·`sse_starlette`·`uvicorn.access`: DEBUG에서
  들어온 `tools/call` 본문(인자 원값 — `apm_users(user_id=…)`)·보낸 메시지·청크를 찍는다
  (REAUDIT-7). 부모 `mcp` 로거를 올려 하위 로거 전부를 덮는다(라이브러리는 하위 로거 수준을 따로
  정하지 않는다 — 상속). `uvicorn.Config`가 자기 로거 수준을 다시 정하므로 서버 설정을 만든 뒤 한
  번 더 올린다.
게이트웨이 자신의 요청 로그(`apm http:` DEBUG)는 계정 경로를 템플릿으로 · 쿼리는 키만 남긴다.

종료 신호(SIGTERM·SIGINT): uvicorn 0.52의 `Server.capture_signals`는 serve() 안에서 받은 신호를
곧바로 다시 올려 정리(`finally`)가 돌기 전에 프로세스가 끝난다. 그래서 신호를 다시 올리는 일을
정리가 끝난 뒤(`main`)로 미룬다 — 종료 코드·신호 의미는 같다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import threading
from collections.abc import Iterator
from typing import Any

# 요청 URL·도구 호출 인자 원값을 찍는 HTTP·MCP 전송 라이브러리 로거(VG-7 · REAUDIT-7)
_QUIET_HTTP_LOGGERS = ("httpx", "httpcore", "mcp", "sse_starlette", "uvicorn.access")


def quiet_http_loggers() -> None:
    """HTTP·MCP 전송 라이브러리 로거를 경고 이상으로 올린다(로그 수준 설정과 무관 — VG-7 ·
    REAUDIT-7)."""
    for name in _QUIET_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _server_class() -> Any:
    import uvicorn
    from uvicorn import server as uvicorn_server

    class GatewayServer(uvicorn.Server):
        """받은 종료 신호를 serve() 안에서 다시 올리지 않는다 — 정리 뒤 `main`이 올린다."""

        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:
            if threading.current_thread() is not threading.main_thread():
                yield
                return
            handled = getattr(
                uvicorn_server, "HANDLED_SIGNALS", (signal.SIGINT, signal.SIGTERM)
            )
            original = {sig: signal.signal(sig, self.handle_exit) for sig in handled}
            try:
                yield
            finally:
                for sig, handler in original.items():
                    signal.signal(sig, handler)

        def captured(self) -> list[int]:
            return list(getattr(self, "_captured_signals", []))

    return GatewayServer


async def _serve() -> list[int]:
    import uvicorn

    from apm_gateway.adapters.jennifer.allowlist import ALLOWED
    from apm_gateway.application.jobs import JobManager
    from apm_gateway.application.poller import EventPoller
    from apm_gateway.application.sources import build_source_set
    from apm_gateway.application.tools import ApmTools
    from apm_gateway.config import describe, load_config
    from apm_gateway.interface.server import audit_job_finished, build_asgi_app, create_server

    try:
        cfg = load_config()
    except ValueError as e:
        print(f"APM 게이트웨이 설정 오류 — 기동 중단: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    logging.basicConfig(
        level=getattr(logging, cfg.server.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    quiet_http_loggers()
    logger = logging.getLogger("apm_gateway")

    sources = build_source_set(cfg)
    poller: EventPoller | None = None
    redis_client = None
    if cfg.poller.enabled:
        import redis.asyncio as aioredis

        redis_client = aioredis.Redis(
            host=cfg.redis.host,
            port=cfg.redis.port,
            db=cfg.redis.db,
            password=cfg.redis.password or None,
        )
        poller = EventPoller(sources, cfg, redis_client)
    tools = ApmTools(sources, cfg, poller_status=poller.status if poller else None)
    jobs = JobManager.from_config(
        cfg, envelope=tools.ok, error_envelope=tools.err, on_finish=audit_job_finished
    )
    await jobs.start()  # 스풀 기동 스캔(남은 진행 중 작업 → interrupted) + 정체·만료 감시
    mcp = create_server(tools, jobs=jobs, host=cfg.server.host, port=cfg.server.port)
    app = build_asgi_app(mcp, cfg.server.bearer_tokens)

    logger.info(
        "APM 게이트웨이 시작: %s:%d · 허용 경로 %d · %s",
        cfg.server.host,
        cfg.server.port,
        len(ALLOWED),
        describe(cfg),
    )
    if not cfg.server.bearer_tokens:
        logger.warning(
            "전송 인증 off(APM_GATEWAY_BEARER_TOKEN·APM_GATEWAY_BEARER_TOKENS 미설정)"
            " — 운영 배치에서는 필수"
        )

    server = _server_class()(
        uvicorn.Config(
            app, host=cfg.server.host, port=cfg.server.port, log_level=cfg.server.log_level.lower()
        )
    )
    quiet_http_loggers()  # uvicorn.Config가 `uvicorn.access` 수준을 log_level로 다시 정했다
    poll_task = asyncio.create_task(poller.run_forever()) if poller else None
    # 인스턴스 목록 선적재(소스마다) — 도메인이 많으면 적재에 수십 초가 걸려 첫 요청이 본체 호출
    # 상한을 넘긴다
    warm_tasks = [asyncio.create_task(src.resolver.warm_up()) for src in sources]
    try:
        await server.serve()
    finally:
        for task in (poll_task, *warm_tasks):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await jobs.aclose()  # 진행 중 백그라운드 작업 → interrupted(종료 감사 1줄씩)
        for src in sources:
            await src.api.client.aclose()
        if redis_client is not None:
            await redis_client.aclose()
    captured: list[int] = server.captured()
    return captured


def main() -> None:
    captured = asyncio.run(_serve())
    for sig in reversed(captured):  # 정리를 마친 뒤 받은 신호를 다시 올린다(uvicorn 종전 동작)
        signal.raise_signal(sig)


if __name__ == "__main__":
    main()
