"""관리자 작업 감사 기록 헬퍼 (plans/104 S4).

설정 변경·리로드(`admin.py`), 스키마 캐시 변경(`schema_cache.py`), DB 구조 관리(`db_structure.py`)가
같은 함수로 감사 로그를 남긴다(사본 금지 — D-053). 기록 성공 여부를 돌려주어 호출부가 응답에
`audit_logged`로 표시한다(침묵 금지).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

logger = logging.getLogger(__name__)


async def log_admin_event(
    request: Request,
    admin_id: str | None,
    event_value: str,
    extra: dict[str, Any],
) -> bool:
    """관리자 작업을 감사 로그에 기록한다.

    `app.state.audit_service`(JSONL + DB 이중 기록)를 우선 쓰고, 실패하거나 없으면
    `app.state.audit_repo`로 폴백한다.

    Args:
        request: FastAPI Request (`state.client_ip`·`state.request_id`는 감사 미들웨어가 채운다)
        admin_id: 작업한 관리자 식별자(`sub`)
        event_value: `AuditEvent` 값 문자열
        extra: 이벤트 상세(값 노출 정책은 호출부 책임)

    Returns:
        기록 성공 여부(둘 다 미구성·실패면 False — 호출부가 응답에 명시한다)
    """
    from src.domain.audit import AuditLogEntry

    entry = AuditLogEntry(
        event=event_value,
        user_id=admin_id,
        client_ip=getattr(request.state, "client_ip", None),
        request_id=getattr(request.state, "request_id", None),
        success=True,
        extra=extra,
    )

    audit_service = getattr(request.app.state, "audit_service", None)
    if audit_service:
        try:
            await audit_service.log(entry)
            return True
        except Exception as e:
            logger.error("관리자 감사 기록 실패, 폴백: %s", e)

    audit_repo = getattr(request.app.state, "audit_repo", None)
    if audit_repo:
        try:
            await audit_repo.log_event(
                {
                    "event_type": event_value,
                    "user_id": admin_id,
                    "ip_address": getattr(request.state, "client_ip", None),
                    "detail": extra,
                }
            )
            return True
        except Exception as e:
            logger.error("관리자 감사 기록 실패: %s", e)

    logger.warning("감사 저장소가 없어 관리자 이벤트를 기록하지 못했습니다: %s", event_value)
    return False
