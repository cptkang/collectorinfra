"""인증 의존성 모듈.

FastAPI Depends()로 사용하는 인증 관련 의존성 함수를 제공한다.
AUTH_ENABLED=false일 때는 anonymous 사용자를 반환한다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import jwt
from fastapi import Header, HTTPException, Request

from src.domain.user import UserRepository, UserRole

logger = logging.getLogger(__name__)

# 인증 비활성화 시 반환되는 기본 사용자
ANONYMOUS_USER: dict = {
    "sub": "anonymous",
    "name": "Anonymous",
    "role": "user",
    "department": None,
    "allowed_db_ids": None,
    "allowed_sources": None,
    "alarm_zones": None,
}


def is_admin(user: dict[str, Any] | None) -> bool:
    """관리자 역할 사용자인지 판정한다(D-069 통합 RBAC · 인가 판정 단일 술어).

    None·빈 dict·role 누락은 관리자가 아니다. 개발 모드(auth 비활성) 우회는 이 술어가 아니라
    호출처가 판단한다.
    """
    if not user:
        return False
    return user.get("role") == UserRole.ADMIN.value


#: 토큰 종류(`type` 클레임)별 불일치 거부 문구 — 사용자(plans/163 S2) · 운영자(D-070 ①).
_TOKEN_TYPE_MISMATCH_DETAIL: dict[str, str] = {
    "user": "사용자 토큰이 아닙니다.",
    "admin": "관리자 토큰이 아닙니다.",
}


def decode_typed_token(token: str, secret: str, expected_type: str) -> dict[str, Any]:
    """JWT 서명·만료를 검증하고 `type` 클레임이 ``expected_type``인 payload를 반환한다.

    서명 통과 ≠ 해당 종류 토큰이다 — 시크릿이 같게 설정돼도 사용자/운영자 토큰의 종류 교차
    통과를 막는다(plans/163 S2 · D-070 ①). `sub` 검사는 종류마다 기준이 달라 호출처가 한다.

    Args:
        token: JWT 토큰 문자열
        secret: JWT 시크릿(사용자/운영자 시크릿은 호출처가 고른다)
        expected_type: 기대하는 `type` 클레임(``"user"`` 또는 ``"admin"``)

    Returns:
        토큰 payload dict

    Raises:
        HTTPException: 만료·서명 불일치·종류 불일치(모두 401)
    """
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="토큰이 만료되었습니다.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=401, detail=_TOKEN_TYPE_MISMATCH_DETAIL[expected_type]
        )
    return payload


#: 인증 활성인데 사용자 저장소(`app.state.user_repo`)가 없을 때의 고정 응답 문구(plans/186 L4).
AUTH_STORE_UNAVAILABLE_DETAIL = "인증 서비스를 사용할 수 없습니다. 관리자에게 문의하세요."


def _auth_store_unavailable(request: Request) -> HTTPException:
    """인증 활성 + 사용자 저장소 부재를 fail-closed(503)로 거부할 예외를 만든다(plans/186 L4).

    저장소가 없으면 활성 여부·DB 권한(`allowed_db_ids` 등)을 확인할 수 없다 — 토큰 payload만
    믿고 통과시키면 전 DB 허용으로 인가가 강등된다(docs/18 2026-10-17). 토큰·sub는 로그에
    남기지 않고 요청 경로만 남긴다(`%r` — 제어문자 인코딩).
    """
    logger.warning(
        "인증 활성인데 사용자 저장소가 없어 요청을 거부합니다(503): path=%r",
        request.url.path,
    )
    return HTTPException(status_code=503, detail=AUTH_STORE_UNAVAILABLE_DETAIL)


def _verify_user_token(token: str, secret: str) -> dict[str, Any]:
    """사용자 JWT 토큰을 검증하고 payload를 반환한다.

    Args:
        token: JWT 토큰 문자열
        secret: JWT 시크릿

    Returns:
        토큰 payload dict

    Raises:
        HTTPException: 토큰이 유효하지 않을 때(사용자 토큰이 아닌 경우 포함)
    """
    # 서명 통과 ≠ 사용자 토큰(plans/163 S2): 운영자 쪽(verify_admin_token)과 대칭으로
    # type=="user"가 아니면 거부한다 — 시크릿이 같게 설정돼도 종류 교차 통과를 막는다.
    payload = decode_typed_token(token, secret, "user")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
    return payload


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

    Raises:
        HTTPException: 토큰 무효(401) · 유효 토큰인데 사용자 저장소 부재(503 · plans/186 L4)
    """
    config = request.app.state.config
    if not config.auth.enabled:
        return ANONYMOUS_USER

    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[7:]
    payload = _verify_user_token(token, config.auth.jwt_secret)

    # DB에서 최신 사용자 정보 조회 (권한 실시간 반영) — 저장소가 없으면 fail-closed(plans/186 L4)
    user_repo: UserRepository | None = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise _auth_store_unavailable(request)
    user = await user_repo.get_by_user_id(payload["sub"])
    if user and user.is_active:
        return user.to_auth_dict()
    return None


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
        "allowed_sources": None,
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

    Raises:
        HTTPException: 토큰 무효(401) · 유효 토큰인데 사용자 저장소 부재(503 · plans/186 L4)
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
    user_repo: UserRepository | None = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise _auth_store_unavailable(request)
    user = await user_repo.get_by_user_id(payload["sub"])
    if user and user.is_active:
        return user.to_auth_dict()
    return None


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
    if is_admin(user):
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
    if not is_admin(user):
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
        HTTPException: 인증 실패(401) · 유효 토큰인데 사용자 저장소 부재(503 · plans/186 L4)
    """
    config = request.app.state.config
    if not config.auth.enabled:
        return ANONYMOUS_USER

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")

    token = authorization[7:]
    payload = _verify_user_token(token, config.auth.jwt_secret)

    # DB에서 최신 사용자 정보 조회 — 저장소가 없으면 fail-closed(plans/186 L4)
    user_repo: UserRepository | None = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise _auth_store_unavailable(request)
    user = await user_repo.get_by_user_id(payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="비활성 사용자입니다.")
    return user.to_auth_dict()
