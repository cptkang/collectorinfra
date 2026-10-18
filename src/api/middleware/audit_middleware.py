"""요청별 감사 컨텍스트를 자동 설정하는 미들웨어."""

from __future__ import annotations

import uuid

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.observability.trace_collector import start_request
from src.observability.trace_writer import flush_if_failed


def _observability_config():
    """관측 설정을 반환한다. 설정 로드 실패 시에도 미들웨어가 죽지 않게 기본값을 준다."""
    try:
        from src.config import load_config

        return load_config().observability
    except Exception:  # pragma: no cover - 방어
        from src.config import ObservabilityConfig

        return ObservabilityConfig()


class AuditMiddleware(BaseHTTPMiddleware):
    """요청별 감사 컨텍스트를 자동 설정하는 미들웨어."""

    async def dispatch(self, request: Request, call_next):
        """요청마다 request_id·client_ip를 바인딩하고, 실패 트레이스 수명을 관리한다.

        request_id는 응답 헤더 `X-Request-ID`로도 돌려준다(plans/163 S3 — 오류 문구의 요청 번호).

        트레이스를 여기서 시작·종료하는 이유(D-141): 그래프 실행 진입점이 4곳
        (`ainvoke` 2 + `astream_events` 2)인데 각각 종료 지점이 달라, 라우트마다 배선하면
        한 곳만 빠져도 그 경로의 관측이 빈다. 모든 HTTP 요청이 이 미들웨어를 지나므로
        여기 한 곳이 유일한 대칭 지점이다.
        """
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

        # 트레이스 수집 시작. 실패로 판정된 요청만 파일로 덤프되므로 정상 경로 비용은 0이다.
        obs = _observability_config()
        if obs.trace_enabled:
            start_request(request_id, max_steps=obs.trace_max_steps)

        try:
            response = await call_next(request)
            # 요청 번호를 응답 헤더로 돌려준다(plans/163 S3) — 오류 응답 본문의 「(요청 번호)」와
            # 같은 값이라 운영자가 서버 로그의 원문을 찾는 열쇠가 된다. 스트림 응답도 본문 전송
            # 전에 헤더가 나가므로 여기서 붙이면 된다.
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            if obs.trace_enabled:
                # 예외로 빠져나온 경우에도 덤프한다 — 그때가 가장 진단이 필요한 순간이다.
                flush_if_failed(request_id, enabled=True)
            structlog.contextvars.unbind_contextvars("request_id", "client_ip")
