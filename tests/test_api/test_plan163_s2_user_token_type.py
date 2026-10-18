"""plans/163 S2 — 사용자 토큰 `type` 클레임 검증 회귀 테스트.

검증 대상:
- `_verify_user_token`이 `type != "user"` 토큰(type 없음·admin)을 401로 거부한다
  (운영자 쪽 `verify_admin_token`과 대칭 · RFC 8725 §3.12 종류별 배타 검증).
- 호출 3곳(`get_current_user`·`resolve_stream_user`·`require_user`)이 같은 규칙을 따른다.
- 정상 사용자 토큰은 그대로 통과한다.
- 두 JWT 시크릿이 명시 설정되고 같으면 기동 시 경고 로그 1줄(D-070 · 기동 거부는 하지 않음).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

from src.api.dependencies import (
    _verify_user_token,
    get_current_user,
    require_user,
    resolve_stream_user,
)
from src.api.server import _warn_shared_jwt_secret

AUTH_SECRET = "auth-secret-S2"
ADMIN_SECRET = "admin-secret-S2"


def _token(type_: str | None, secret: str = AUTH_SECRET, sub: str = "u1") -> str:
    payload: dict = {
        "sub": sub,
        "name": "U",
        "role": "user",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    if type_ is not None:
        payload["type"] = type_
    return jwt.encode(payload, secret, algorithm="HS256")


def _request(cookie_token: str | None = None, user_repo=None):
    config = SimpleNamespace(
        auth=SimpleNamespace(enabled=True, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
    )
    state = SimpleNamespace(config=config, user_repo=user_repo)
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        cookies={"user_token": cookie_token} if cookie_token else {},
        headers={},
    )


# --- _verify_user_token 직접 ---


@pytest.mark.parametrize("bad_type", [None, "admin", "", "USER"])
def test_verify_user_token_rejects_non_user_type(bad_type):
    """type이 정확히 "user"가 아니면 서명이 맞아도 401."""
    with pytest.raises(HTTPException) as ei:
        _verify_user_token(_token(bad_type), AUTH_SECRET)
    assert ei.value.status_code == 401


def test_verify_user_token_accepts_user_type():
    payload = _verify_user_token(_token("user"), AUTH_SECRET)
    assert payload["sub"] == "u1"
    assert payload["type"] == "user"


# --- 호출 3곳 대표 ---


@pytest.mark.parametrize("bad_type", [None, "admin"])
async def test_get_current_user_rejects_non_user_type(bad_type):
    with pytest.raises(HTTPException) as ei:
        await get_current_user(_request(), f"Bearer {_token(bad_type)}")
    assert ei.value.status_code == 401


@pytest.mark.parametrize("bad_type", [None, "admin"])
async def test_resolve_stream_user_rejects_non_user_type(bad_type):
    with pytest.raises(HTTPException) as ei:
        await resolve_stream_user(_request(cookie_token=_token(bad_type)))
    assert ei.value.status_code == 401


@pytest.mark.parametrize("bad_type", [None, "admin"])
async def test_require_user_rejects_non_user_type(bad_type):
    with pytest.raises(HTTPException) as ei:
        await require_user(_request(), f"Bearer {_token(bad_type)}")
    assert ei.value.status_code == 401


async def test_user_token_passes_all_three_call_sites():
    # 저장소 부재는 503(plans/186 L4) — 통과 경로는 저장소 조회로 확인한다
    from unittest.mock import AsyncMock

    from src.domain.user import User

    repo = SimpleNamespace(
        get_by_user_id=AsyncMock(return_value=User(user_id="u1", username="U", hashed_password="x"))
    )
    token = _token("user")
    assert (await get_current_user(_request(user_repo=repo), f"Bearer {token}"))["sub"] == "u1"
    req = _request(cookie_token=token, user_repo=repo)
    assert (await resolve_stream_user(req))["sub"] == "u1"
    assert (await require_user(_request(user_repo=repo), f"Bearer {token}"))["sub"] == "u1"


# --- 두 시크릿이 같을 때 기동 경고 (선택 항목) ---


def _secret_config(
    auth_secret: str,
    admin_secret: str,
    auth_explicit: bool = True,
    admin_explicit: bool = True,
    enabled: bool = True,
):
    return SimpleNamespace(
        auth=SimpleNamespace(
            enabled=enabled, jwt_secret=auth_secret, _jwt_secret_explicit=auth_explicit
        ),
        admin=SimpleNamespace(jwt_secret=admin_secret, _jwt_secret_explicit=admin_explicit),
    )


def test_warns_when_explicit_secrets_equal(caplog):
    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        _warn_shared_jwt_secret(_secret_config("same", "same"))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "AUTH_JWT_SECRET" in warnings[0].getMessage()
    assert "same" not in warnings[0].getMessage()  # 시크릿 값은 로그에 남기지 않는다


@pytest.mark.parametrize(
    "cfg",
    [
        _secret_config("a", "b"),
        _secret_config("same", "same", auth_explicit=False),
        _secret_config("same", "same", admin_explicit=False),
        _secret_config("same", "same", enabled=False),
    ],
    ids=["different", "auth_generated", "admin_generated", "auth_disabled"],
)
def test_no_warning_otherwise(caplog, cfg):
    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        _warn_shared_jwt_secret(cfg)
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
