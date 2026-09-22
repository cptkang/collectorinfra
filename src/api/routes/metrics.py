"""본체 자기 관측 `/metrics` 라우트 (plans/92 트랙 B-1 · O4).

`OBS_METRICS_ENDPOINT_ENABLED`가 켜졌을 때만 `/api/v1` 아래에 등록된다(`src/api/server.py`) →
실제 경로는 `GET /api/v1/metrics`다. 기존 JSON 운영 지표 `GET /api/v1/alarm/metrics`와는 다른
경로이며 형식도 다르다(이쪽은 Prometheus 노출 형식).

인증은 정적 Bearer(G-5 (ii))다 — Prometheus `authorization.credentials`로 바로 붙는다. 토큰은
기동 시 1회 읽어 라우터에 묶는다. 켜졌는데 토큰이 비어 있으면 503으로 거부한다(fail-closed —
무인증 노출 금지, plans/92 §7.2).

OpenAPI 스키마에는 싣지 않는다 — 수집기 전용 운영 엔드포인트라 API 표면이 아니고, 플래그를
켜도 공개 API 문서가 바뀌지 않게 한다.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request, Response

from src.observability.metrics import render_latest

#: 토큰 미설정(fail-closed) 응답 사유.
TOKEN_MISSING_DETAIL = (
    "OBS_METRICS_BEARER_TOKEN 미설정 — /metrics는 인증 토큰 없이 노출하지 않는다"
)

_BEARER_PREFIX = "Bearer "


def _bearer_matches(authorization: str | None, token: str) -> bool:
    """`Authorization: Bearer <token>`이 설정 토큰과 같은지 상수 시간으로 비교한다."""
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        return False
    provided = authorization[len(_BEARER_PREFIX):].strip()
    return hmac.compare_digest(provided.encode("utf-8"), token.encode("utf-8"))


def build_metrics_router(bearer_token: str) -> APIRouter:
    """토큰을 묶은 `/metrics` 라우터를 만든다(기동 시 1회).

    Args:
        bearer_token: 정적 Bearer 토큰(빈 값이면 모든 요청을 503으로 거부)

    Returns:
        `GET /metrics` 하나를 가진 라우터
    """
    router = APIRouter()

    @router.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        """전역 레지스트리를 `Accept` 협상 형식(OpenMetrics 1.0 / text 0.0.4)으로 돌려준다."""
        if not bearer_token:
            raise HTTPException(status_code=503, detail=TOKEN_MISSING_DETAIL)
        if not _bearer_matches(request.headers.get("authorization"), bearer_token):
            raise HTTPException(
                status_code=401,
                detail="인증 실패",
                headers={"WWW-Authenticate": "Bearer"},
            )
        body, content_type = render_latest(request.headers.get("accept"))
        return Response(content=body, media_type=content_type)

    return router
