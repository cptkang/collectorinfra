"""HTTP RED 계측 미들웨어 — 순수 ASGI (plans/92 트랙 B-1 · O4).

`OBS_METRICS_ENDPOINT_ENABLED`가 켜졌을 때만 앱에 추가된다(`src/api/server.py`). 꺼져 있으면
미들웨어 자체가 없어 요청 경로가 현행과 같다.

`route` 라벨은 **경로 템플릿**만 쓴다(원 URL·query 금지 — plans/92 §4.4). 템플릿은 라우팅이
끝난 뒤 ASGI scope에 남는 값에서 읽는다(2026-09-22 실측 · FastAPI 0.141.1 · Starlette 1.6.0).

1. FastAPI `include_router(prefix=...)`로 붙은 라우트: `scope["route"].path`는 라우터 **안쪽**
   경로(`/threads/{thread_id}`)이고 접두(`/api/v1`)가 빠져 있다. 접두까지 붙은 템플릿은 FastAPI가
   `scope["fastapi"]["effective_route_context"].path`에 남긴다(`/api/v1/threads/{thread_id}`).
   비공개 키라 먼저 읽고, 없으면 2로 내려간다.
2. 앱에 직접 붙은 라우트(`/`·`/docs` 등)와 구버전 FastAPI: `scope["route"].path`.
3. 마운트(`/static`): Starlette `Mount`는 `route`를 남기지 않고 `root_path`만 늘린다 →
   늘어난 부분 + `/{path}`.
4. 어디에도 걸리지 않은 요청(404): `unmatched`.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.observability.metrics import observe_http_request

#: 라우팅에 걸리지 않은 요청의 route 라벨 값.
UNMATCHED_ROUTE = "unmatched"


def route_template(scope: Scope, root_path_before: str) -> str:
    """라우팅이 끝난 scope에서 경로 템플릿을 읽는다(모듈 독스트링의 1→4 순서).

    Args:
        scope: 앱 호출이 끝난 뒤의 ASGI scope(라우터가 같은 dict를 갱신한다)
        root_path_before: 앱 호출 전 `root_path`(마운트가 늘린 부분을 가려내는 기준)

    Returns:
        경로 템플릿 또는 `unmatched`
    """
    fastapi_scope: Any = scope.get("fastapi")
    if isinstance(fastapi_scope, dict):
        effective_path = getattr(fastapi_scope.get("effective_route_context"), "path", None)
        if isinstance(effective_path, str) and effective_path:
            return effective_path

    route_path = getattr(scope.get("route"), "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path

    root_path = scope.get("root_path", "")
    if isinstance(root_path, str) and root_path != root_path_before:
        return f"{root_path[len(root_path_before):]}/{{path}}"

    return UNMATCHED_ROUTE


class MetricsMiddleware:
    """요청 수·지연을 `route`·`method`·`status` 라벨로 기록한다."""

    def __init__(self, app: ASGIApp) -> None:
        """감쌀 ASGI 앱을 받는다."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """HTTP 요청만 계측한다(lifespan·websocket은 그대로 통과).

        상태 코드는 `http.response.start`에서 잡는다 — 스트리밍 응답도 이 메시지가 먼저 나간다.
        예외가 빠져나오면 500으로 기록한 뒤 다시 던진다(바깥 `ServerErrorMiddleware`가 500을 낸다).
        응답을 시작하지 않고 끝난 요청도 500으로 센다(ASGI 서버가 그 경우 500을 보낸다).
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        root_path_before = str(scope.get("root_path", ""))
        method = str(scope.get("method", ""))
        status_code = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            status_code = 500
            raise
        finally:
            observe_http_request(
                route_template(scope, root_path_before),
                method,
                status_code,
                time.perf_counter() - started,
            )
