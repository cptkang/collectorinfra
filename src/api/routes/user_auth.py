"""사용자 인증 관련 라우트.

사용자 가입, 로그인, 로그아웃, 비밀번호 변경, 인증 상태 확인 엔드포인트를 제공한다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.dependencies import get_current_user, require_user
from src.api.schemas import (
    AuthStatusResponse,
    ChangePasswordRequest,
    UserInfoResponse,
    UserLoginRequest,
    UserLoginResponse,
    UserRegisterRequest,
)
from src.domain.user import User, UserRole, UserStatus
from src.utils.password import hash_password, verify_password

logger = logging.getLogger(__name__)
router = APIRouter()


def _create_user_token(user_id: str, username: str, role: str, config) -> tuple[str, int]:
    """사용자 JWT 토큰을 생성한다.

    Args:
        user_id: 사용자 ID
        username: 표시 이름
        role: 역할
        config: AppConfig

    Returns:
        (토큰 문자열, 만료 시간(초))
    """
    expires_in = config.auth.jwt_expire_hours * 3600
    expire = datetime.now(timezone.utc) + timedelta(hours=config.auth.jwt_expire_hours)
    payload = {
        "sub": user_id,
        "name": username,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "user",
    }
    token = jwt.encode(payload, config.admin.jwt_secret, algorithm="HS256")
    return token, expires_in


def _get_client_ip(request: Request) -> str:
    """클라이언트 IP를 추출한다."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _log_audit_event(request: Request, event: dict) -> None:
    """감사 이벤트를 기록한다 (AuditService + 기존 AuditRepository 폴백)."""
    # AuditService를 통한 기록 (JSONL + DB 이중 기록)
    audit_service = getattr(request.app.state, "audit_service", None)
    if audit_service:
        from src.domain.audit import AuditLogEntry

        entry = AuditLogEntry(
            event=event.get("event_type", "unknown"),
            user_id=event.get("user_id"),
            client_ip=event.get("ip_address"),
            extra=event.get("detail"),
        )
        try:
            await audit_service.log(entry)
            return  # AuditService가 DB도 기록하므로 폴백 불필요
        except Exception as e:
            logger.error("AuditService 기록 실패, 폴백: %s", e)

    # 폴백: 기존 AuditRepository 직접 사용
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if audit_repo:
        try:
            await audit_repo.log_event(event)
        except Exception as e:
            logger.error("감사 로그 기록 실패: %s", e)


@router.get(
    "/auth/status",
    response_model=AuthStatusResponse,
)
async def auth_status(
    request: Request,
    current_user: Optional[dict] = Depends(get_current_user),
) -> AuthStatusResponse:
    """인증 상태를 반환한다.

    클라이언트가 AUTH_ENABLED 여부와 현재 로그인 상태를 확인하는 데 사용한다.
    """
    config = request.app.state.config
    user_info = None

    if current_user and current_user.get("sub") != "anonymous":
        user_info = UserInfoResponse(
            user_id=current_user["sub"],
            username=current_user.get("name", ""),
            role=current_user.get("role", "user"),
            department=current_user.get("department"),
            allowed_db_ids=current_user.get("allowed_db_ids"),
        )
    elif current_user and current_user.get("sub") == "anonymous" and not config.auth.enabled:
        user_info = UserInfoResponse(
            user_id="anonymous",
            username="Anonymous",
            role="user",
        )

    return AuthStatusResponse(
        auth_enabled=config.auth.enabled,
        user=user_info,
    )


@router.post(
    "/auth/register",
    response_model=UserInfoResponse,
    responses={400: {"description": "가입 실패"}, 409: {"description": "ID 중복"}},
)
async def register(
    request: Request,
    body: UserRegisterRequest,
) -> UserInfoResponse:
    """사용자 가입. 승인 없이 즉시 가입한다.

    Args:
        request: FastAPI Request
        body: 가입 요청

    Returns:
        생성된 사용자 정보

    Raises:
        HTTPException: 가입 실패 시
    """
    user_repo = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise HTTPException(status_code=503, detail="인증 서비스를 사용할 수 없습니다.")

    config = request.app.state.config

    # 비밀번호 길이 검증
    if len(body.password) < config.auth.password_min_length:
        raise HTTPException(
            status_code=400,
            detail=f"비밀번호는 최소 {config.auth.password_min_length}자 이상이어야 합니다.",
        )

    # 중복 확인
    if await user_repo.exists(body.user_id):
        raise HTTPException(status_code=409, detail="이미 존재하는 사용자 ID입니다.")

    # 사용자 생성
    user = User(
        user_id=body.user_id,
        username=body.username,
        hashed_password=hash_password(body.password),
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        department=body.department,
        allowed_db_ids=None,
        auth_method="local",
    )
    await user_repo.create(user)

    # 감사 로그
    await _log_audit_event(request, {
        "event_type": "register",
        "user_id": body.user_id,
        "detail": {"username": body.username, "department": body.department},
        "ip_address": _get_client_ip(request),
    })

    logger.info("사용자 가입 완료: %s", body.user_id)

    return UserInfoResponse(
        user_id=user.user_id,
        username=user.username,
        role=user.role.value,
        department=user.department,
        allowed_db_ids=user.allowed_db_ids,
        status=user.status.value,
    )


@router.post(
    "/auth/login",
    response_model=UserLoginResponse,
    responses={401: {"description": "인증 실패"}, 423: {"description": "계정 잠금"}},
)
async def login(
    request: Request,
    body: UserLoginRequest,
) -> UserLoginResponse:
    """사용자 로그인.

    ID/PW를 검증하고 JWT 토큰을 발급한다.
    로그인 실패 시 실패 횟수를 증가시키고, 초과 시 계정을 잠근다.
    """
    user_repo = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise HTTPException(status_code=503, detail="인증 서비스를 사용할 수 없습니다.")

    config = request.app.state.config

    user = await user_repo.get_by_user_id(body.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="ID 또는 비밀번호가 올바르지 않습니다.")

    # 계정 잠금 확인
    if user.status == UserStatus.LOCKED:
        # 잠금 시간 경과 확인
        if user.last_login_at:
            lockout_end = user.last_login_at + timedelta(minutes=config.auth.lockout_minutes)
            if datetime.now(timezone.utc) < lockout_end:
                raise HTTPException(
                    status_code=423,
                    detail=f"계정이 잠겼습니다. {config.auth.lockout_minutes}분 후 다시 시도하세요.",
                )
            # 잠금 시간 경과 -> 잠금 해제
            user.status = UserStatus.ACTIVE
            user.login_fail_count = 0

    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="비활성 계정입니다.")

    # 비밀번호 검증
    if not verify_password(body.password, user.hashed_password):
        user.login_fail_count += 1
        user.last_login_at = datetime.now(timezone.utc)

        # 최대 시도 초과 시 잠금
        if user.login_fail_count >= config.auth.max_login_attempts:
            user.status = UserStatus.LOCKED
            await user_repo.update(user)
            logger.warning("계정 잠금: %s (로그인 %d회 실패)", body.user_id, user.login_fail_count)
            raise HTTPException(
                status_code=423,
                detail=f"로그인 {config.auth.max_login_attempts}회 실패로 계정이 잠겼습니다.",
            )

        await user_repo.update(user)

        # 감사 로그
        await _log_audit_event(request, {
            "event_type": "login_fail",
            "user_id": body.user_id,
            "detail": {"fail_count": user.login_fail_count},
            "ip_address": _get_client_ip(request),
        })

        raise HTTPException(status_code=401, detail="ID 또는 비밀번호가 올바르지 않습니다.")

    # 로그인 성공
    user.login_fail_count = 0
    user.last_login_at = datetime.now(timezone.utc)
    await user_repo.update(user)

    token, expires_in = _create_user_token(
        user.user_id, user.username, user.role.value, config
    )

    # 감사 로그
    await _log_audit_event(request, {
        "event_type": "login",
        "user_id": body.user_id,
        "detail": {"role": user.role.value},
        "ip_address": _get_client_ip(request),
    })

    logger.info("사용자 로그인 성공: %s", body.user_id)

    return UserLoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserInfoResponse(
            user_id=user.user_id,
            username=user.username,
            role=user.role.value,
            department=user.department,
            allowed_db_ids=user.allowed_db_ids,
            status=user.status.value,
            last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        ),
    )


@router.post(
    "/auth/logout",
)
async def logout(
    request: Request,
    current_user: dict = Depends(require_user),
) -> dict:
    """로그아웃. 감사 로그를 기록한다.

    Phase 1은 클라이언트 측 토큰 삭제 + DB 감사 로그 기록.
    향후 Redis 블랙리스트 확장 가능.
    """
    await _log_audit_event(request, {
        "event_type": "logout",
        "user_id": current_user.get("sub"),
        "detail": {},
        "ip_address": _get_client_ip(request),
    })

    return {"message": "로그아웃되었습니다."}


@router.get(
    "/auth/me",
    response_model=UserInfoResponse,
)
async def get_me(
    current_user: dict = Depends(require_user),
) -> UserInfoResponse:
    """현재 로그인된 사용자 정보를 반환한다."""
    return UserInfoResponse(
        user_id=current_user.get("sub", ""),
        username=current_user.get("name", ""),
        role=current_user.get("role", "user"),
        department=current_user.get("department"),
        allowed_db_ids=current_user.get("allowed_db_ids"),
    )


@router.put(
    "/auth/password",
)
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    current_user: dict = Depends(require_user),
) -> dict:
    """비밀번호를 변경한다. 현재 비밀번호 확인 필수."""
    user_repo = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise HTTPException(status_code=503, detail="인증 서비스를 사용할 수 없습니다.")

    config = request.app.state.config

    if len(body.new_password) < config.auth.password_min_length:
        raise HTTPException(
            status_code=400,
            detail=f"새 비밀번호는 최소 {config.auth.password_min_length}자 이상이어야 합니다.",
        )

    user = await user_repo.get_by_user_id(current_user["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    # 현재 비밀번호 확인
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=401, detail="현재 비밀번호가 올바르지 않습니다.")

    # 비밀번호 변경
    user.hashed_password = hash_password(body.new_password)
    await user_repo.update(user)

    # 감사 로그
    await _log_audit_event(request, {
        "event_type": "password_change",
        "user_id": current_user["sub"],
        "detail": {},
        "ip_address": _get_client_ip(request),
    })

    return {"message": "비밀번호가 변경되었습니다."}
