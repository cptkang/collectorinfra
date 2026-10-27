"""plans/186 L4 — 인증 활성 + 사용자 저장소 부재 시 fail-closed(503) 회귀 테스트.

검증 대상(`src/api/dependencies.py`):
- 유효 사용자 토큰 + `user_repo` None → 3곳(`get_current_user`·`resolve_stream_user`·`require_user`)
  모두 503 고정 문구(토큰·sub 원문 없음) + warning 로그(경로만).
- 판정 순서 유지: 무효 토큰은 여전히 401 · 토큰 없음은 각 함수의 종전 동작(None/401).
- break-glass 운영자 토큰은 저장소 부재에도 `require_admin_user`·운영자 SSE를 통과.
- AUTH_ENABLED=false는 ANONYMOUS_USER · 저장소가 있으면 종전 DB 조회.

설정은 SimpleNamespace로 검증 대상 필드만 둔다 — `.env`를 읽지 않는다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from src.api.dependencies import (
    ANONYMOUS_USER,
    AUTH_STORE_UNAVAILABLE_DETAIL,
    get_current_user,
    require_admin_user,
    require_user,
    resolve_stream_user,
)
from src.domain.user import User, UserRole, UserStatus

AUTH_SECRET = "auth-secret-L4-0123456789abcdef0123"
ADMIN_SECRET = "admin-secret-L4-0123456789abcdef012"
SUB = "secret-sub-u1"
PATH = "/api/v1/probe"
LOGGER = "src.api.dependencies"


def _exp() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def _user_token(secret: str = AUTH_SECRET, role: str = "user", sub: str = SUB) -> str:
    return jwt.encode(
        {"sub": sub, "name": "U", "role": role, "type": "user", "exp": _exp()},
        secret,
        algorithm="HS256",
    )


def _admin_token(secret: str = ADMIN_SECRET, sub: str = "ops") -> str:
    return jwt.encode({"sub": sub, "type": "admin", "exp": _exp()}, secret, algorithm="HS256")


def _config(auth_enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=auth_enabled, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
    )


def _request(
    auth_enabled: bool = True,
    user_repo=None,
    cookies: dict | None = None,
    headers: dict | None = None,
) -> SimpleNamespace:
    state = SimpleNamespace(config=_config(auth_enabled), user_repo=user_repo)
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(path=PATH),
        cookies=cookies or {},
        headers=headers or {},
    )


def _repo(user: User | None) -> SimpleNamespace:
    return SimpleNamespace(get_by_user_id=AsyncMock(return_value=user))


def _user(status: UserStatus = UserStatus.ACTIVE, role: UserRole = UserRole.USER) -> User:
    return User(
        user_id=SUB,
        username="U",
        hashed_password="x",
        role=role,
        status=status,
        allowed_db_ids=["db_a"],
    )


async def _call(site: str, req: SimpleNamespace, token: str | None):
    """3곳을 같은 입력으로 부른다 — 스트림은 쿠키, 나머지는 Authorization 헤더."""
    if site == "get_current_user":
        return await get_current_user(req, f"Bearer {token}" if token else None)
    if site == "require_user":
        return await require_user(req, f"Bearer {token}" if token else None)
    if token:
        req.cookies["user_token"] = token
    return await resolve_stream_user(req)


SITES = ["get_current_user", "resolve_stream_user", "require_user"]


# --- 바뀌는 동작: 저장소 부재 + 유효 토큰 → 503 ---


@pytest.mark.parametrize("site", SITES)
async def test_valid_token_without_repo_is_503(site, caplog):
    token = _user_token()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(HTTPException) as ei:
            await _call(site, _request(user_repo=None), token)
    assert ei.value.status_code == 503
    assert ei.value.detail == AUTH_STORE_UNAVAILABLE_DETAIL
    assert SUB not in ei.value.detail and token not in ei.value.detail

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == LOGGER]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert PATH in message
    assert token not in message and SUB not in message  # 민감값은 로그에 남기지 않는다


@pytest.mark.parametrize("site", SITES)
async def test_admin_role_payload_without_repo_is_not_trusted(site):
    """role=admin을 실은 사용자 토큰도 payload만으로는 통과하지 않는다(옛 fail-open)."""
    with pytest.raises(HTTPException) as ei:
        await _call(site, _request(user_repo=None), _user_token(role="admin"))
    assert ei.value.status_code == 503


# --- 판정 순서 유지 ---


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize(
    "token",
    [
        _user_token(secret="wrong-secret-0123456789abcdef01234"),  # 서명 불일치
        _admin_token(secret=AUTH_SECRET),  # 종류 불일치(type=admin)
        "not-a-jwt",
    ],
    # (plans/195) 토큰 문자열에 `exp`(지금+1h)가 들어가 xdist 워커마다 id가 달라졌다 — 고정 id
    ids=["bad_signature", "admin_type", "not_jwt"],
)
async def test_invalid_token_without_repo_stays_401(site, token):
    with pytest.raises(HTTPException) as ei:
        await _call(site, _request(user_repo=None), token)
    assert ei.value.status_code == 401


async def test_no_token_without_repo_keeps_current_behavior():
    assert await get_current_user(_request(user_repo=None), None) is None
    assert await resolve_stream_user(_request(user_repo=None)) is None
    with pytest.raises(HTTPException) as ei:
        await require_user(_request(user_repo=None), None)
    assert ei.value.status_code == 401


# --- break-glass ---


async def test_break_glass_admin_passes_without_repo():
    result = await require_admin_user(_request(user_repo=None), f"Bearer {_admin_token()}")
    assert result["role"] == UserRole.ADMIN.value
    assert result["break_glass"] is True


async def test_require_admin_user_with_user_token_without_repo_is_503():
    """break-glass가 아니면 require_user로 내려가 503 — role=admin payload로 통과하지 않는다."""
    with pytest.raises(HTTPException) as ei:
        await require_admin_user(_request(user_repo=None), f"Bearer {_user_token(role='admin')}")
    assert ei.value.status_code == 503


def _stream_request(user_repo=None, cookies=None, headers=None) -> SimpleNamespace:
    req = _request(user_repo=user_repo, cookies=cookies, headers=headers)
    req.app.state.alarm_bus = SimpleNamespace(subscribe=lambda: None, unsubscribe=lambda q: None)
    return req


async def test_noise_stream_break_glass_header_passes_without_repo():
    """운영자 관제 스트림은 break-glass를 먼저 본다 — 저장소 부재에도 운영자는 연결된다."""
    from src.api.routes.noise_dashboard import noise_stream

    req = _stream_request(headers={"authorization": f"Bearer {_admin_token()}"})
    response = await noise_stream(req, token=None)
    assert isinstance(response, StreamingResponse)


async def test_noise_stream_user_cookie_without_repo_is_503():
    from src.api.routes.noise_dashboard import noise_stream

    req = _stream_request(cookies={"user_token": _user_token(role="admin")})
    with pytest.raises(HTTPException) as ei:
        await noise_stream(req, token=None)
    assert ei.value.status_code == 503


async def test_alarm_notification_stream_user_cookie_without_repo_is_503():
    from src.api.routes.alarm import alarm_notifications_stream

    req = _stream_request(cookies={"user_token": _user_token()})
    with pytest.raises(HTTPException) as ei:
        await alarm_notifications_stream(req, token=None)
    assert ei.value.status_code == 503


# --- 유지되는 동작 ---


@pytest.mark.parametrize("site", SITES)
async def test_auth_disabled_returns_anonymous(site):
    assert await _call(site, _request(auth_enabled=False), _user_token()) == ANONYMOUS_USER


async def test_require_admin_user_auth_disabled_returns_anonymous():
    assert await require_admin_user(_request(auth_enabled=False), None) == ANONYMOUS_USER


@pytest.mark.parametrize("site", SITES)
async def test_repo_present_uses_db_lookup(site):
    repo = _repo(_user())
    result = await _call(site, _request(user_repo=repo), _user_token(role="admin"))
    repo.get_by_user_id.assert_awaited_once_with(SUB)
    assert result["role"] == UserRole.USER.value  # payload role이 아니라 DB 값
    assert result["allowed_db_ids"] == ["db_a"]


async def test_repo_present_inactive_user_keeps_current_behavior():
    inactive = _user(status=UserStatus.INACTIVE)
    token = _user_token()
    assert await get_current_user(_request(user_repo=_repo(inactive)), f"Bearer {token}") is None
    req = _request(user_repo=_repo(inactive), cookies={"user_token": token})
    assert await resolve_stream_user(req) is None
    with pytest.raises(HTTPException) as ei:
        await require_user(_request(user_repo=_repo(inactive)), f"Bearer {token}")
    assert ei.value.status_code == 401


# --- HTTP 종단: 응답 본문에 고정 문구만 ---


def _app(user_repo=None) -> FastAPI:
    app = FastAPI()
    app.state.config = _config()
    app.state.user_repo = user_repo

    @app.get("/required")
    async def _required(user: dict = Depends(require_user)) -> dict:
        return {"sub": user["sub"]}

    @app.get("/optional")
    async def _optional(user: dict | None = Depends(get_current_user)) -> dict:
        return {"user": user}

    @app.get("/stream")
    async def _stream(request: Request) -> dict:
        user = await resolve_stream_user(request)
        return {"user": user}

    @app.get("/admin")
    async def _admin(user: dict = Depends(require_admin_user)) -> dict:
        return {"sub": user["sub"]}

    return app


@pytest.mark.parametrize("path", ["/required", "/optional", "/admin"])
def test_http_503_body_is_fixed_detail(path):
    token = _user_token(role="admin")
    with TestClient(_app()) as client:
        res = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 503
    assert res.json() == {"detail": AUTH_STORE_UNAVAILABLE_DETAIL}
    assert SUB not in res.text


def test_http_stream_cookie_503_and_break_glass_admin_200():
    with TestClient(_app()) as client:
        client.cookies.set("user_token", _user_token())
        res = client.get("/stream")
        assert res.status_code == 503
        assert res.json() == {"detail": AUTH_STORE_UNAVAILABLE_DETAIL}
        client.cookies.clear()
        res = client.get("/admin", headers={"Authorization": f"Bearer {_admin_token()}"})
        assert res.status_code == 200
        assert res.json() == {"sub": "ops"}
