"""Plan 59 Part A — 어드민 접근 체계 정합화(통합 RBAC) 회귀 테스트.

검증 대상:
- D-070 ①: verify_admin_token의 토큰 type 검증(권한 상승 차단).
- D-070 ②: 사용자/운영자 토큰 시크릿 분리.
- D-069: require_admin_user 통합 가드(개발 우회 · break-glass · role==admin).
- D-071: 기본 크레덴셜 제거 + 운영 모드 시크릿 미설정 기동 거부.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException

from src.api.dependencies import ANONYMOUS_USER, require_admin_user
from src.api.routes.admin_auth import verify_admin_token
from src.api.server import _validate_production_secrets
from src.domain.user import User, UserRole, UserStatus

ADMIN_SECRET = "admin-secret-AAA"
AUTH_SECRET = "auth-secret-BBB"


def _exp() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=1)


def _user_token(secret: str = AUTH_SECRET, role: str = "user", sub: str = "u1") -> str:
    return jwt.encode(
        {"sub": sub, "name": "U", "role": role, "type": "user", "exp": _exp()},
        secret,
        algorithm="HS256",
    )


def _admin_token(secret: str = ADMIN_SECRET, sub: str = "admin") -> str:
    return jwt.encode(
        {"sub": sub, "type": "admin", "exp": _exp()},
        secret,
        algorithm="HS256",
    )


def _fake_request(auth_enabled: bool = True, user_repo=None):
    config = SimpleNamespace(
        auth=SimpleNamespace(enabled=auth_enabled, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
    )
    state = SimpleNamespace(config=config, user_repo=user_repo)
    return SimpleNamespace(app=SimpleNamespace(state=state))


# --- D-070 ①: type 검증 (권한 상승 차단) ---


def test_verify_admin_token_rejects_user_token_even_same_secret():
    """옛 취약점 재현: 사용자 토큰이 운영자 시크릿으로 서명돼도 type!=admin이면 거부."""
    token = _user_token(secret=ADMIN_SECRET)  # 동일 시크릿 가정(옛 구조)
    with pytest.raises(HTTPException) as ei:
        verify_admin_token(token, ADMIN_SECRET)
    assert ei.value.status_code == 401


def test_verify_admin_token_accepts_admin_token():
    assert verify_admin_token(_admin_token(), ADMIN_SECRET) == "admin"


# --- D-070 ②: 시크릿 분리 ---


def test_user_token_not_verifiable_with_admin_secret():
    """사용자 토큰(auth 시크릿 서명)은 admin 시크릿으로 검증되지 않는다."""
    token = _user_token(secret=AUTH_SECRET, role="admin")
    with pytest.raises(HTTPException):
        verify_admin_token(token, ADMIN_SECRET)


# --- D-069: 통합 RBAC 가드 ---


async def test_require_admin_user_allows_admin_role():
    repo = SimpleNamespace(
        get_by_user_id=AsyncMock(
            return_value=User(
                user_id="u1", username="U", hashed_password="x",
                role=UserRole.ADMIN, status=UserStatus.ACTIVE,
            )
        )
    )
    req = _fake_request(auth_enabled=True, user_repo=repo)
    result = await require_admin_user(req, f"Bearer {_user_token(role='admin')}")
    assert result["role"] == UserRole.ADMIN.value


async def test_require_admin_user_rejects_plain_user():
    repo = SimpleNamespace(
        get_by_user_id=AsyncMock(
            return_value=User(
                user_id="u1", username="U", hashed_password="x",
                role=UserRole.USER, status=UserStatus.ACTIVE,
            )
        )
    )
    req = _fake_request(auth_enabled=True, user_repo=repo)
    with pytest.raises(HTTPException) as ei:
        await require_admin_user(req, f"Bearer {_user_token(role='user')}")
    assert ei.value.status_code == 403


async def test_require_admin_user_break_glass_admin_token():
    """DB(user_repo) 없이도 운영자 토큰이면 통과(break-glass, §9.2)."""
    req = _fake_request(auth_enabled=True, user_repo=None)
    result = await require_admin_user(req, f"Bearer {_admin_token()}")
    assert result["role"] == UserRole.ADMIN.value
    assert result["break_glass"] is True


async def test_require_admin_user_dev_mode_bypass():
    """AUTH_ENABLED=false면 인증 우회(Plan 39 원칙 유지)."""
    req = _fake_request(auth_enabled=False)
    result = await require_admin_user(req, None)
    assert result == ANONYMOUS_USER


async def test_require_admin_user_missing_auth_401():
    req = _fake_request(auth_enabled=True, user_repo=SimpleNamespace(get_by_user_id=AsyncMock()))
    with pytest.raises(HTTPException) as ei:
        await require_admin_user(req, None)
    assert ei.value.status_code == 401


# --- D-071: 크레덴셜/시크릿 하드닝 ---


def test_admin_config_has_no_default_credentials():
    """기본 크레덴셜(admin/admin123)이 제거되었다."""
    from src.config import AdminConfig

    cfg = AdminConfig(_env_file=None)
    assert cfg.username == ""
    assert cfg.password == ""


def test_jwt_secret_explicit_flag_true_when_provided():
    from src.config import AdminConfig

    cfg = AdminConfig(_env_file=None, jwt_secret="explicit-secret")
    assert cfg._jwt_secret_explicit is True
    assert cfg.jwt_secret == "explicit-secret"


def test_validate_production_refuses_when_secrets_missing():
    config = SimpleNamespace(
        auth=SimpleNamespace(enabled=True, _jwt_secret_explicit=False),
        admin=SimpleNamespace(username="", password="", _jwt_secret_explicit=False),
    )
    with pytest.raises(RuntimeError):
        _validate_production_secrets(config)


def test_validate_production_passes_when_all_set():
    config = SimpleNamespace(
        auth=SimpleNamespace(enabled=True, _jwt_secret_explicit=True),
        admin=SimpleNamespace(username="admin", password="pw", _jwt_secret_explicit=True),
    )
    _validate_production_secrets(config)  # 예외 없어야 함


def test_validate_dev_mode_skips_check():
    config = SimpleNamespace(
        auth=SimpleNamespace(enabled=False),
        admin=SimpleNamespace(),
    )
    _validate_production_secrets(config)  # 예외 없어야 함
