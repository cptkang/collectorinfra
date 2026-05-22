"""헬스체크 라우트.

시스템 상태와 DB 연결 상태를 확인하는 엔드포인트를 제공한다.
멀티 DB 모드에서는 활성 DB 중 하나라도 연결되면 healthy로 판단한다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request

from src.api.schemas import HealthResponse
from src.db import get_db_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """시스템 헬스체크.

    DB 연결 상태를 포함하여 시스템 상태를 반환한다.

    멀티 DB 모드(ACTIVE_DB_IDS 설정 시):
      활성 DB 목록에서 하나라도 연결 가능하면 db_connected=True.
    단일 DB 모드:
      기존 방식대로 db_backend 설정에 따라 헬스체크.

    Args:
        request: FastAPI Request

    Returns:
        헬스체크 응답
    """
    config = request.app.state.config
    db_connected = False

    # 멀티 DB 모드: ACTIVE_DB_IDS가 설정되어 있으면 활성 소스로 헬스체크
    active_db_ids = config.multi_db.get_active_db_ids()
    if active_db_ids:
        for db_id in active_db_ids:
            try:
                async with get_db_client(config, db_id=db_id) as client:
                    if await client.health_check():
                        db_connected = True
                        break
            except Exception as e:
                logger.debug("헬스체크 실패 (%s): %s", db_id, e)
                continue
    else:
        # 단일 DB 모드 (레거시)
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
