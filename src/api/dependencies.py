"""인증 의존성 모듈.

FastAPI Depends()로 사용하는 인증 관련 의존성 함수를 제공한다.
AUTH_ENABLED=false일 때는 anonymous 사용자를 반환한다.
"""

from __future__ import annotations

import logging
from typing import Optional

import jwt
from fastapi import Header, HTTPException, Request

from src.domain.user import UserRole

logger = logging.getLogger(__name__)

# 인증 비활성화 시 반환되는 기본 사용자
ANONYMOUS_USER: dict = {
    "sub": "anonymous",
    "name": "Anonymous",
    "role": "user",
    "department": None,
    "allowed_db_ids": None,
    "alarm_zones": None,
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
    payload = _verify_user_token(token, config.auth.jwt_secret)

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


def _try_break_glass_admin(request: Request, authorization: Optional[str]) -> Optional[dict]:
    """운영자(break-glass) 토큰이면 가상 admin 사용자 dict를 반환, 아니면 None.

    DB/`user_repo` 장애 시에도 운영자가 진입할 수 있도록, `/admin/login`이 발급한
    admin 시크릿 서명·`type=="admin"` 토큰을 허용한다(Plan 59 §9.2). 시크릿이 분리되어
    (D-070) 사용자 토큰은 이 경로에서 검증에 실패하므로 교차 통과는 발생하지 않는다.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    admin_secret = request.app.state.config.admin.jwt_secret
    try:
        payload = jwt.decode(token, admin_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    if payload.get("type") != "admin" or not payload.get("sub"):
        return None
    username = payload["sub"]
    return {
        "sub": username,
        "name": username,
        "role": UserRole.ADMIN.value,
        "department": None,
        "allowed_db_ids": None,
        "break_glass": True,
    }


async def resolve_stream_user(
    request: Request,
    token_from_query: Optional[str] = None,
) -> Optional[dict]:
    """SSE(EventSource) 요청의 사용자를 해소한다(Plan 59 §17, 쿠키 기반 인증).

    브라우저 EventSource는 Authorization 헤더를 못 실으므로 **HttpOnly 쿠키(user_token)**를
    우선 사용하고, 없으면 Authorization 헤더, 마지막으로 쿼리 토큰(내부망 폴백)을 시도한다.

    Returns:
        사용자 dict(개발 모드는 ANONYMOUS_USER), 또는 인증 실패 시 None.
    """
    config = request.app.state.config
    if not config.auth.enabled:
        return ANONYMOUS_USER

    token = request.cookies.get("user_token")
    if not token:
        authz = request.headers.get("authorization")
        if authz and authz.startswith("Bearer "):
            token = authz[7:]
    if not token and token_from_query:
        token = token_from_query
    if not token:
        return None

    payload = _verify_user_token(token, config.auth.jwt_secret)
    user_repo = getattr(request.app.state, "user_repo", None)
    if user_repo:
        user = await user_repo.get_by_user_id(payload["sub"])
        if user and user.is_active:
            return user.to_auth_dict()
        return None
    return {
        "sub": payload.get("sub"),
        "name": payload.get("name", ""),
        "role": payload.get("role", "user"),
        "department": None,
        "allowed_db_ids": None,
        "alarm_zones": payload.get("alarm_zones"),
    }


def alarm_zones_for_user(user: Optional[dict], auth_enabled: bool) -> set[str]:
    """사용자가 수신 가능한 알림 존 집합을 산출한다(Plan 59 §17).

    - 개발 모드(auth 비활성): 전 존(진입성 보존).
    - 관리자(role==admin): 전 존.
    - 그 외: alarm_zones에 부여된 존만(빈 집합 = 일반, 수신 안 함).
    """
    from src.routing.zones import all_zones, normalize_zones

    if not auth_enabled:
        return set(all_zones())
    if not user:
        return set()
    if user.get("role") == UserRole.ADMIN.value:
        return set(all_zones())
    return set(normalize_zones(user.get("alarm_zones")))


async def require_admin_user(
    request: Request,
    authorization: Optional[str] = Header(None, description="Bearer {token}"),
) -> dict:
    """관리자 권한 사용자를 필수로 요구한다(통합 RBAC, D-069).

    판정 우선순위:
    - AUTH_ENABLED=false(개발 모드): 인증 우회, ANONYMOUS_USER 반환(Plan 39 원칙 유지).
    - break-glass 운영자 토큰(type="admin", admin 시크릿): DB 장애 대비 진입 경로(§9.2).
    - 사용자 토큰 + DB 실시간 role==admin: 회원 리스트에서 부여한 어드민 권한이 즉시 반영.

    Raises:
        HTTPException: 미인증(401) 또는 관리자 아님(403).
    """
    config = request.app.state.config
    if not config.auth.enabled:
        return ANONYMOUS_USER

    # break-glass: 운영자 토큰이면 즉시 허용(DB 조회 없이 진입)
    admin_user = _try_break_glass_admin(request, authorization)
    if admin_user is not None:
        return admin_user

    # 일반 경로: 사용자 토큰 검증 + 실시간 role 확인
    user = await require_user(request, authorization)
    if user.get("role") != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user


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
    payload = _verify_user_token(token, config.auth.jwt_secret)

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
