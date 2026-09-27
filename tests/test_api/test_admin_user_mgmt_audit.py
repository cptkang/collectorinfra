"""사용자 관리 변경·감사 로그 정리의 감사 기록 (감사 결함 ①).

종전에는 역할·상태·조회 권한 변경, 비밀번호 초기화, 삭제, 오래된 감사 로그 정리가 서버 로그에만
남고 감사 DB에는 남지 않았다. 고정하는 계약:
  - `admin_action` 한 행에 행위자(user_id)·대상(`target_user_id`)·변경 전후(`changes`)가 남는다.
  - 비밀번호(임시 비밀번호 포함)는 어떤 형태로도 남기지 않는다.
  - 거절된 변경(보호 계정 403 등)은 기록하지 않는다 — 실제로 바뀐 것만 남긴다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes.admin import (
    cleanup_audit_logs,
    delete_user,
    reset_user_password,
    update_user,
    update_user_permissions,
)
from src.api.schemas import UpdatePermissionsRequest, UpdateUserRequest
from src.domain.audit import AuditEvent
from src.domain.user import User, UserRole, UserStatus
from src.security.audit_service import AuditService

_ADMIN = {"sub": "root"}


class _AuditRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_event(self, event: dict) -> None:
        self.events.append(event)


def _user(**overrides) -> User:
    fields = dict(
        user_id="u1", username="u1", hashed_password="x",
        role=UserRole.USER, status=UserStatus.ACTIVE,
        allowed_db_ids=["db_a"], is_protected=False,
    )
    fields.update(overrides)
    return User(**fields)


def _users(target: User) -> SimpleNamespace:
    return SimpleNamespace(
        get_by_user_id=AsyncMock(return_value=target),
        update=AsyncMock(),
        delete=AsyncMock(),
        list_all=AsyncMock(return_value=[target]),
    )


def _request(user_repo=None, audit_repo=None) -> tuple[SimpleNamespace, _AuditRepo]:
    captured = _AuditRepo()
    config = SimpleNamespace(jsonl_enabled=False, db_enabled=True,
                             alert_on_failed_login=5, alert_on_large_result=5000)
    state = SimpleNamespace(
        user_repo=user_repo,
        audit_repo=audit_repo,
        audit_service=AuditService(config, captured),
        config=SimpleNamespace(audit=SimpleNamespace(retention_days=90)),
    )
    req = SimpleNamespace(
        app=SimpleNamespace(state=state),
        state=SimpleNamespace(client_ip="10.0.0.9", request_id="rid-9"),
    )
    return req, captured


def _only_admin_action(captured: _AuditRepo) -> dict:
    assert [e["event_type"] for e in captured.events] == [AuditEvent.ADMIN_ACTION.value]
    row = captured.events[0]
    assert row["user_id"] == "root"          # 행위자
    assert row["ip_address"] == "10.0.0.9"
    return row["detail"]["extra"]


async def test_update_user_records_before_after() -> None:
    req, captured = _request(_users(_user()))

    await update_user(req, "u1", UpdateUserRequest(role="admin", department="IT"), _ADMIN)

    extra = _only_admin_action(captured)
    assert extra["action"] == "user_update"
    assert extra["target_user_id"] == "u1"
    assert extra["changes"] == {
        "role": {"before": "user", "after": "admin"},
        "department": {"before": None, "after": "IT"},
    }


async def test_update_user_status_change_is_recorded() -> None:
    req, captured = _request(_users(_user(status=UserStatus.LOCKED)))

    await update_user(req, "u1", UpdateUserRequest(status="active"), _ADMIN)

    extra = _only_admin_action(captured)
    assert extra["changes"] == {"status": {"before": "locked", "after": "active"}}


async def test_update_permissions_records_before_after() -> None:
    req, captured = _request(_users(_user(allowed_db_ids=["db_a"])))

    await update_user_permissions(
        req, "u1", UpdatePermissionsRequest(allowed_db_ids=["db_a", "db_b"]), _ADMIN
    )

    extra = _only_admin_action(captured)
    assert extra["action"] == "user_permissions_update"
    assert extra["target_user_id"] == "u1"
    assert extra["changes"] == {
        "allowed_db_ids": {"before": ["db_a"], "after": ["db_a", "db_b"]},
    }


async def test_reset_password_never_records_password() -> None:
    req, captured = _request(_users(_user(status=UserStatus.LOCKED)))

    resp = await reset_user_password(req, "u1", _ADMIN)

    extra = _only_admin_action(captured)
    assert extra["action"] == "user_password_reset"
    assert extra["target_user_id"] == "u1"
    assert extra["changes"] == {"status": {"before": "locked", "after": "active"}}
    dumped = json.dumps(captured.events, ensure_ascii=False)
    assert resp["temp_password"] not in dumped
    assert "password" not in json.dumps(extra.get("changes"))


async def test_delete_user_records_snapshot() -> None:
    req, captured = _request(_users(_user()))

    await delete_user(req, "u1", _ADMIN)

    extra = _only_admin_action(captured)
    assert extra["action"] == "user_delete"
    assert extra["target_user_id"] == "u1"
    assert extra["before"] == {"role": "user", "status": "active", "allowed_db_ids": ["db_a"]}


async def test_refused_change_is_not_recorded() -> None:
    """보호 계정의 역할 변경은 403 — 바뀐 것이 없으므로 감사 행도 없다."""
    req, captured = _request(_users(_user(role=UserRole.ADMIN, is_protected=True)))

    with pytest.raises(HTTPException) as ei:
        await update_user(req, "u1", UpdateUserRequest(role="user"), _ADMIN)

    assert ei.value.status_code == 403
    assert captured.events == []


async def test_audit_cleanup_is_recorded() -> None:
    audit_repo = SimpleNamespace(cleanup_old_logs=AsyncMock(return_value=7))
    req, captured = _request(audit_repo=audit_repo)

    resp = await cleanup_audit_logs(req, 30, _ADMIN)

    assert resp == {"deleted": 7, "retention_days": 30}
    extra = _only_admin_action(captured)
    assert extra == {"action": "audit_cleanup", "retention_days": 30, "deleted": 7}
