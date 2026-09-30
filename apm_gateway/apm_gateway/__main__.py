"""게이트웨이 엔트리 — `cd apm_gateway && ../.venv/bin/python -m apm_gateway` (plans/87 §0.7 ·
SPEC-apm-gateway §2).

SSE MCP 서버(uvicorn · 정적 Bearer)와 이벤트 폴러(옵트인)를 한 이벤트 루프에서 함께 돌린다. 기동
로그 1줄에 허용 경로 수 · 폴러 상태 · 설정 여부를 남긴다(비밀 값 없음 — R-20).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys


async def _serve() -> None:
    import uvicorn

    from apm_gateway.adapters.jennifer.allowlist import ALLOWED
    from apm_gateway.adapters.jennifer.api import JenniferApi
    from apm_gateway.adapters.jennifer.client import JenniferClient
    from apm_gateway.application.poller import EventPoller
    from apm_gateway.application.resolver import InstanceResolver
    from apm_gateway.application.tools import ApmTools
    from apm_gateway.config import describe, load_config
    from apm_gateway.interface.server import build_asgi_app, create_server

    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg.server.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    logger = logging.getLogger("apm_gateway")

    api = JenniferApi(JenniferClient(cfg.jennifer))
    resolver = InstanceResolver(
        api,
        cfg.policies.instance_map,
        domain_filter=cfg.jennifer.domain_ids,
        cache_seconds=cfg.runtime.instance_cache_seconds,
    )
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
        poller = EventPoller(api, resolver, cfg, redis_client)
    tools = ApmTools(api, resolver, cfg, poller_status=poller.status if poller else None)
    mcp = create_server(tools, host=cfg.server.host, port=cfg.server.port)
    app = build_asgi_app(mcp, cfg.server.bearer_token or None)

    logger.info(
        "APM 게이트웨이 시작: %s:%d · 허용 경로 %d · %s",
        cfg.server.host,
        cfg.server.port,
        len(ALLOWED),
        describe(cfg),
    )
    if not cfg.server.bearer_token:
        logger.warning("전송 인증 off(APM_GATEWAY_BEARER_TOKEN 미설정) — 운영 배치에서는 필수")

    server = uvicorn.Server(
        uvicorn.Config(
            app, host=cfg.server.host, port=cfg.server.port, log_level=cfg.server.log_level.lower()
        )
    )
    poll_task = asyncio.create_task(poller.run_forever()) if poller else None
    # 인스턴스 목록 선적재 — 도메인이 많으면 적재에 수십 초가 걸려 첫 요청이 본체 호출 상한을 넘긴다
    warm_task = asyncio.create_task(resolver.warm_up()) if api.configured else None
    try:
        await server.serve()
    finally:
        for task in (poll_task, warm_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await api.client.aclose()
        if redis_client is not None:
            await redis_client.aclose()


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
