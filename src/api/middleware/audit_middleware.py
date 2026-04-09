"""요청별 감사 컨텍스트를 자동 설정하는 미들웨어."""

from __future__ import annotations

import uuid

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class AuditMiddleware(BaseHTTPMiddleware):
    """요청별 감사 컨텍스트를 자동 설정하는 미들웨어."""

    async def dispatch(self, request: Request, call_next):
        """요청마다 request_id와 client_ip를 생성하여 request.state와 structlog에 바인딩한다."""
        # 요청 ID 생성 (8자리 UUID)
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        # 클라이언트 IP 추출 (X-Forwarded-For 우선)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client else "unknown"
        request.state.client_ip = client_ip

        # structlog 컨텍스트에 바인딩
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            client_ip=client_ip,
        )

        try:
            response = await call_next(request)
            return response
        finally:
            structlog.contextvars.unbind_contextvars("request_id", "client_ip")
