"""감사 로그 조회 라우트 — `/admin/audit-logs` · `/admin/audit/{logs,stats,users/…/activity,alerts}`
(plans/166 A7 — `admin.py`에서 이동).

`admin.router`가 이 라우터를 원래 선언 위치에 포함한다(경로·이름·OpenAPI 불변). 감사 로그 정리
(`POST /admin/audit/cleanup`)는 `_audit_admin_action`(테스트 patch 대상)을 부르므로 `admin.py`에
남는다(D-337 ②). 옛 경로 `src.api.routes.admin`에서
`get_audit_logs_paginated`·`get_audit_stats`를 재노출한다(D-337 ① · 테스트가 그 경로로 import).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.api.dependencies import require_admin_user
from src.api.error_detail import admin_error_detail

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 A7)
logger = logging.getLogger("src.api.routes.admin")
router = APIRouter()


class AuditLogFilterParams(BaseModel):
    """감사 로그 조회 필터."""

    start_date: str | None = None
    end_date: str | None = None
    user_id: str | None = None
    event_type: str | None = None
    target_db: str | None = None
    success: bool | None = None
    keyword: str | None = None
    page: int = 1
    page_size: int = 50


class AuditLogPageResponse(BaseModel):
    """페이지네이션 감사 로그 응답."""

    logs: list[dict]  # type: ignore[type-arg]
    total: int
    page: int
    page_size: int
    total_pages: int


@router.get(
    "/admin/audit-logs",
)
async def get_audit_logs(
    request: Request,
    user_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> list[dict]:  # type: ignore[type-arg]
    """감사 로그를 조회한다.

    Args:
        request: FastAPI Request
        user_id: 필터: 사용자 ID
        event_type: 필터: 이벤트 타입
        limit: 최대 조회 수
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        감사 로그 목록
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        return []

    return await audit_repo.query_logs(  # type: ignore[no-any-return]
        user_id=user_id,
        event_type=event_type,
        limit=min(limit, 1000),
    )


# --- 엔드포인트: 감사 로그 확장 ---


@router.get(
    "/admin/audit/logs",
    response_model=AuditLogPageResponse,
)
async def get_audit_logs_paginated(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    user_id: str | None = None,
    event_type: str | None = None,
    target_db: str | None = None,
    success: bool | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 50,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> AuditLogPageResponse:
    """확장된 감사 로그 조회 (페이지네이션, 필터).

    Args:
        request: FastAPI Request
        start_date: 시작 날짜 (ISO 형식)
        end_date: 종료 날짜 (ISO 형식)
        user_id: 필터: 사용자 ID
        event_type: 필터: 이벤트 타입
        target_db: 필터: 대상 DB
        success: 필터: 성공 여부
        keyword: 키워드 검색
        page: 페이지 번호
        page_size: 페이지 크기
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        페이지네이션된 감사 로그 응답
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        return AuditLogPageResponse(
            logs=[], total=0, page=page, page_size=page_size, total_pages=0,
        )

    page_size = min(page_size, 200)  # 최대 200

    try:
        logs, total = await audit_repo.query_logs_paginated(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id,
            event_type=event_type,
            target_db=target_db,
            success=success,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=admin_error_detail(
                "감사 로그 날짜 해석", e, logger, request, input_error=True
            ),
        ) from e
    except AttributeError:
        # query_logs_paginated 미구현 시 기존 메서드 폴백
        logs = await audit_repo.query_logs(
            user_id=user_id, event_type=event_type, limit=page_size,
        )
        total = len(logs)

    total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0

    return AuditLogPageResponse(
        logs=logs,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/admin/audit/stats",
)
async def get_audit_stats(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> dict:  # type: ignore[type-arg]
    """감사 통계를 반환한다.

    Args:
        request: FastAPI Request
        start_date: 통계 시작 날짜 (ISO 형식)
        end_date: 통계 종료 날짜 (ISO 형식)
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        감사 통계 딕셔너리
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        return {"error": "감사 서비스를 사용할 수 없습니다."}

    try:
        return await audit_repo.get_stats(  # type: ignore[no-any-return]
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=admin_error_detail(
                "감사 통계 날짜 해석", e, logger, request, input_error=True
            ),
        ) from e
    except AttributeError:
        return {"error": "통계 기능이 지원되지 않습니다."}


@router.get(
    "/admin/audit/users/{user_id}/activity",
)
async def get_user_activity(
    request: Request,
    user_id: str,
    limit: int = 100,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> list[dict]:  # type: ignore[type-arg]
    """특정 사용자의 활동 이력을 반환한다.

    Args:
        request: FastAPI Request
        user_id: 대상 사용자 ID
        limit: 최대 조회 수
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        사용자 활동 이력 목록
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        return []

    try:
        return await audit_repo.get_user_activity(  # type: ignore[no-any-return]
            user_id=user_id,
            limit=min(limit, 500),
        )
    except AttributeError:
        return await audit_repo.query_logs(  # type: ignore[no-any-return]
            user_id=user_id, limit=min(limit, 500),
        )


@router.get(
    "/admin/audit/alerts",
)
async def get_security_alerts(
    request: Request,
    limit: int = 100,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> list[dict]:  # type: ignore[type-arg]
    """보안 경고 목록을 반환한다.

    Args:
        request: FastAPI Request
        limit: 최대 조회 수
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        보안 경고 목록
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        return []

    try:
        return await audit_repo.get_alerts(limit=min(limit, 500))  # type: ignore[no-any-return]
    except AttributeError:
        return await audit_repo.query_logs(  # type: ignore[no-any-return]
            event_type="security_alert", limit=min(limit, 500),
        )
