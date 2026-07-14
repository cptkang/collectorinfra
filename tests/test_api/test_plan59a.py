"""Plan 59-a 회귀 테스트.

- 보호 root 계정(is_protected): 역할/상태 변경·삭제·PW초기화 차단, 부서/알림그룹은 허용.
- seed admin은 is_protected=True로 생성.
- 감사 로그 로테이션 헬퍼(_cleanup_audit_once): retention_days>0일 때만 삭제 호출.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes.admin import delete_user, reset_user_password, update_user
from src.api.schemas import UpdateUserRequest
from src.domain.user import User, UserRole, UserStatus


def _protected_root() -> User:
    return User(
        user_id="admin", username="admin", hashed_password="x",
        role=UserRole.ADMIN, status=UserStatus.ACTIVE, is_protected=True,
    )


def _plain_user(uid="u1", role=UserRole.USER) -> User:
    return User(
        user_id=uid, username=uid, hashed_password="x",
        role=role, status=UserStatus.ACTIVE, is_protected=False,
    )


def _req(user_repo):
    state = SimpleNamespace(
        user_repo=user_repo,
        config=SimpleNamespace(audit=SimpleNamespace(retention_days=90)),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _repo(target: User):
    return SimpleNamespace(
        get_by_user_id=AsyncMock(return_value=target),
        update=AsyncMock(),
        delete=AsyncMock(),
        list_all=AsyncMock(return_value=[target]),
    )


# --- 보호 계정: 차단되는 작업 ---


async def test_protected_role_change_blocked():
    repo = _repo(_protected_root())
    with pytest.raises(HTTPException) as ei:
        await update_user(_req(repo), "admin", UpdateUserRequest(role="user"), {"sub": "admin"})
    assert ei.value.status_code == 403
    repo.update.assert_not_awaited()


async def test_protected_status_change_blocked():
    repo = _repo(_protected_root())
    with pytest.raises(HTTPException) as ei:
        await update_user(_req(repo), "admin", UpdateUserRequest(status="inactive"), {"sub": "admin"})
    assert ei.value.status_code == 403


async def test_protected_delete_blocked():
    repo = _repo(_protected_root())
    with pytest.raises(HTTPException) as ei:
        await delete_user(_req(repo), "admin", {"sub": "admin"})
    assert ei.value.status_code == 403
    repo.delete.assert_not_awaited()


async def test_protected_reset_password_blocked():
    repo = _repo(_protected_root())
    with pytest.raises(HTTPException) as ei:
        await reset_user_password(_req(repo), "admin", {"sub": "admin"})
    assert ei.value.status_code == 403


# --- 보호 계정: 허용되는 작업(부서·알림그룹) ---


async def test_protected_department_allowed():
    repo = _repo(_protected_root())
    resp = await update_user(_req(repo), "admin", UpdateUserRequest(department="IT"), {"sub": "admin"})
    repo.update.assert_awaited_once()
    assert resp.department == "IT"
    assert resp.is_protected is True


async def test_protected_alarm_zones_allowed():
    repo = _repo(_protected_root())
    resp = await update_user(
        _req(repo), "admin", UpdateUserRequest(alarm_zones=["gongjon"]), {"sub": "admin"}
    )
    repo.update.assert_awaited_once()
    assert resp.alarm_zones == ["gongjon"]


# --- 일반 계정은 영향 없음 ---


async def test_plain_user_role_change_ok():
    repo = _repo(_plain_user(role=UserRole.USER))
    resp = await update_user(_req(repo), "u1", UpdateUserRequest(role="admin"), {"sub": "admin"})
    assert resp.role == "admin"
    assert resp.is_protected is False


# --- seed admin은 is_protected=True ---


async def test_seed_admin_is_protected():
    from src.api.server import _seed_admin_user

    created = {}
    repo = SimpleNamespace(
        list_all=AsyncMock(return_value=[]),
        exists=AsyncMock(return_value=False),
        create=AsyncMock(side_effect=lambda u: created.setdefault("user", u)),
    )
    config = SimpleNamespace(admin=SimpleNamespace(username="root", password="pw123"))
    await _seed_admin_user(repo, config)
    assert created["user"].is_protected is True
    assert created["user"].role == UserRole.ADMIN


# --- 감사 로그 로테이션 헬퍼 ---


async def test_cleanup_audit_calls_repo_when_positive():
    from src.api.server import _cleanup_audit_once

    repo = SimpleNamespace(cleanup_old_logs=AsyncMock(return_value=7))
    await _cleanup_audit_once(repo, 90)
    repo.cleanup_old_logs.assert_awaited_once_with(90)


async def test_cleanup_audit_skips_when_disabled():
    from src.api.server import _cleanup_audit_once

    repo = SimpleNamespace(cleanup_old_logs=AsyncMock())
    await _cleanup_audit_once(repo, 0)      # 0 이하 = 비활성
    await _cleanup_audit_once(None, 90)     # repo 없음
    repo.cleanup_old_logs.assert_not_awaited()
