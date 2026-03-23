"""헬스체크 라우트.

시스템 상태와 DB 연결 상태를 확인하는 엔드포인트를 제공한다.
설정의 db_backend에 따라 적절한 DB 클라이언트로 헬스체크를 수행한다.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

from src.api.schemas import HealthResponse
from src.db import get_db_client

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """시스템 헬스체크.

    DB 연결 상태를 포함하여 시스템 상태를 반환한다.
    설정의 db_backend에 따라 적절한 클라이언트를 사용한다.

    Args:
        request: FastAPI Request

    Returns:
        헬스체크 응답
    """
    config = request.app.state.config
    db_connected = False

    try:
        async with get_db_client(config) as client:
            db_connected = await client.health_check()
    except Exception:
        pass

    return HealthResponse(
        status="healthy" if db_connected else "unhealthy",
        version="0.1.0",
        db_connected=db_connected,
        timestamp=datetime.now(),
    )
