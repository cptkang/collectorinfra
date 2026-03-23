"""운영자 인증 관련 라우트.

운영자 로그인, 토큰 검증 엔드포인트를 제공한다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src.config import AdminConfig

logger = logging.getLogger(__name__)
router = APIRouter()


# --- 요청/응답 모델 ---


class LoginRequest(BaseModel):
    """로그인 요청."""

    username: str = Field(..., min_length=1, description="관리자 ID")
    password: str = Field(..., min_length=1, description="관리자 비밀번호")


class LoginResponse(BaseModel):
    """로그인 응답."""

    access_token: str = Field(..., description="JWT 토큰")
    token_type: str = Field(default="bearer", description="토큰 타입")
    expires_in: int = Field(..., description="만료 시간(초)")


class AdminMeResponse(BaseModel):
    """관리자 정보 응답."""

    username: str = Field(..., description="관리자 ID")
    authenticated: bool = Field(default=True)


# --- 유틸리티 ---


def _get_admin_config(request: Request) -> AdminConfig:
    """Request에서 AdminConfig를 가져온다."""
    return request.app.state.config.admin


def _create_token(username: str, config: AdminConfig) -> tuple[str, int]:
    """JWT 토큰을 생성한다.

    Args:
        username: 사용자명
        config: 관리자 설정

    Returns:
        (토큰 문자열, 만료 시간(초))
    """
    expires_in = config.jwt_expire_hours * 3600
    expire = datetime.now(timezone.utc) + timedelta(hours=config.jwt_expire_hours)
    payload = {
        "sub": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "admin",
    }
    token = jwt.encode(payload, config.jwt_secret, algorithm="HS256")
    return token, expires_in


def verify_admin_token(
    token: str,
    secret: str,
) -> str:
    """JWT 토큰을 검증하고 사용자명을 반환한다.

    Args:
        token: JWT 토큰 문자열
        secret: JWT 시크릿

    Returns:
        사용자명

    Raises:
        HTTPException: 토큰이 유효하지 않을 때
    """
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        username: Optional[str] = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="토큰이 만료되었습니다.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")


async def require_admin(
    request: Request,
    authorization: str = Header(..., description="Bearer {token}"),
) -> str:
    """관리자 인증 의존성.

    Authorization 헤더에서 JWT 토큰을 추출하여 검증한다.

    Args:
        request: FastAPI Request
        authorization: Authorization 헤더

    Returns:
        관리자 사용자명

    Raises:
        HTTPException: 인증 실패 시
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization 헤더 형식이 올바르지 않습니다. 'Bearer {token}' 형식이어야 합니다.",
        )
    token = authorization[7:]  # "Bearer " 이후
    config = _get_admin_config(request)
    return verify_admin_token(token, config.jwt_secret)


# --- 엔드포인트 ---


@router.post(
    "/admin/login",
    response_model=LoginResponse,
    responses={401: {"description": "인증 실패"}},
)
async def admin_login(
    request: Request,
    body: LoginRequest,
) -> LoginResponse:
    """운영자 로그인.

    ID/PW를 검증하고 JWT 토큰을 발급한다.

    Args:
        request: FastAPI Request
        body: 로그인 요청

    Returns:
        JWT 토큰 정보

    Raises:
        HTTPException: 인증 실패 시
    """
    config = _get_admin_config(request)

    if body.username != config.username or body.password != config.password:
        logger.warning(f"운영자 로그인 실패: {body.username}")
        raise HTTPException(status_code=401, detail="ID 또는 비밀번호가 올바르지 않습니다.")

    token, expires_in = _create_token(body.username, config)
    logger.info(f"운영자 로그인 성공: {body.username}")

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
    )


@router.get(
    "/admin/me",
    response_model=AdminMeResponse,
)
async def admin_me(
    username: str = Depends(require_admin),
) -> AdminMeResponse:
    """현재 로그인된 관리자 정보를 반환한다.

    Args:
        username: 인증된 관리자 사용자명 (의존성 주입)

    Returns:
        관리자 정보
    """
    return AdminMeResponse(username=username)
