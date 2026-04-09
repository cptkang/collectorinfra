"""인증 의존성 모듈.

FastAPI Depends()로 사용하는 인증 관련 의존성 함수를 제공한다.
AUTH_ENABLED=false일 때는 anonymous 사용자를 반환한다.
"""

from __future__ import annotations

import logging
from typing import Optional

import jwt
from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)

# 인증 비활성화 시 반환되는 기본 사용자
ANONYMOUS_USER: dict = {
    "sub": "anonymous",
    "name": "Anonymous",
    "role": "user",
    "department": None,
    "allowed_db_ids": None,
}


def _verify_user_token(token: str, secret: str) -> dict:
    """사용자 JWT 토큰을 검증하고 payload를 반환한다.

    Args:
        token: JWT 토큰 문자열
        secret: JWT 시크릿

    Returns:
        토큰 payload dict

    Raises:
        HTTPException: 토큰이 유효하지 않을 때
    """
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="토큰이 만료되었습니다.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, description="Bearer {token}"),
) -> Optional[dict]:
    """현재 인증된 사용자 정보를 반환한다.

    - AUTH_ENABLED=false: ANONYMOUS_USER 반환
    - 토큰이 없으면 None, 있으면 검증 후 DB에서 최신 정보 조회

    Args:
        request: FastAPI Request
        authorization: Authorization 헤더

    Returns:
        사용자 정보 dict 또는 None
    """
    config = request.app.state.config
    if not config.auth.enabled:
        return ANONYMOUS_USER

    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[7:]
    payload = _verify_user_token(token, config.admin.jwt_secret)

    # DB에서 최신 사용자 정보 조회 (권한 실시간 반영)
    user_repo = getattr(request.app.state, "user_repo", None)
    if user_repo:
        user = await user_repo.get_by_user_id(payload["sub"])
        if user and user.is_active:
            return user.to_auth_dict()
        return None

    # user_repo가 없으면 토큰 payload만으로 반환
    return {
        "sub": payload.get("sub"),
        "name": payload.get("name", ""),
        "role": payload.get("role", "user"),
        "department": None,
        "allowed_db_ids": None,
    }


async def require_user(
    request: Request,
    authorization: Optional[str] = Header(None, description="Bearer {token}"),
) -> dict:
    """인증된 사용자를 필수로 요구한다.

    - AUTH_ENABLED=false: ANONYMOUS_USER 반환 (인증 우회)
    - AUTH_ENABLED=true: JWT 토큰 필수, DB에서 사용자 조회

    Args:
        request: FastAPI Request
        authorization: Authorization 헤더

    Returns:
        사용자 정보 dict

    Raises:
        HTTPException: 인증 실패 시
    """
    config = request.app.state.config
    if not config.auth.enabled:
        return ANONYMOUS_USER

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")

    token = authorization[7:]
    payload = _verify_user_token(token, config.admin.jwt_secret)

    # DB에서 최신 사용자 정보 조회
    user_repo = getattr(request.app.state, "user_repo", None)
    if user_repo:
        user = await user_repo.get_by_user_id(payload["sub"])
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="비활성 사용자입니다.")
        return user.to_auth_dict()

    # user_repo가 없으면 토큰 payload만으로 반환
    return {
        "sub": payload.get("sub"),
        "name": payload.get("name", ""),
        "role": payload.get("role", "user"),
        "department": None,
        "allowed_db_ids": None,
    }
