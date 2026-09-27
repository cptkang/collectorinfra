"""로그인·로그아웃 감사 이벤트 이름과 연속 실패 경보 (감사 결함 ①).

종전 결함:
  - 라우트가 로그인·로그아웃을 `login`·`logout`으로 기록했는데 관리자 화면 필터 값은
    `user_login`·`user_logout`(`AuditEvent`)이라 필터로 찾으면 0건이었다.
  - 계정을 잠그는 실패는 423을 먼저 던져 `login_fail` 기록조차 없었다.
  - 연속 실패 경보(`AUDIT_ALERT_ON_FAILED_LOGIN`)는 호출되지 않는 메서드 안에만 있어
    보안 경고가 한 건도 남지 않았다.

고정하는 계약:
  ① 로그인·로그아웃은 `AuditEvent` 이름으로 기록한다.
  ② 잠금으로 끝나는 실패도 `login_fail`로 남고 잠금 사실(`locked`)이 보인다.
  ③ 연속 실패가 임계에 닿으면 `security_alert`(critical)가 남는다. 연속의 기준은 계정 잠금
     카운터(`login_fail_count`)다 — 로그인에 성공하면 0으로 돌아가므로 누적 실패로는 울리지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from src.api.routes.user_auth import login, logout
from src.api.schemas import UserLoginRequest
from src.domain.audit import AuditEvent
from src.domain.user import User, UserRole, UserStatus
from src.security.audit_service import AuditService
from src.utils.password import hash_password

_PW = "correct-horse-1"
_HASH = hash_password(_PW)  # bcrypt는 느리므로 모듈에서 한 번만 만든다
_ADMIN_JS = Path(__file__).resolve().parents[2] / "src" / "static" / "js" / "admin.js"


class _AuditRepo:
    """DB 감사 저장소 대역 — 기록된 행을 그대로 모은다."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_event(self, event: dict) -> None:
        self.events.append(event)

    async def query_logs(self, **kwargs) -> list[dict]:
        return [e for e in self.events if e["event_type"] == kwargs.get("event_type")]


class _Users:
    def __init__(self, user: User) -> None:
        self.user = user

    async def get_by_user_id(self, user_id: str):
        return self.user if user_id == self.user.user_id else None

    async def update(self, user: User) -> None:
        self.user = user


def _audit_config(alert_on_failed_login: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        jsonl_enabled=False,  # 파일 I/O 없이 DB 경로만 본다
        db_enabled=True,
        alert_on_failed_login=alert_on_failed_login,
        alert_on_large_result=5000,
    )


def _request(users: _Users, service: AuditService, max_attempts: int = 5) -> SimpleNamespace:
    auth = SimpleNamespace(
        max_login_attempts=max_attempts,
        lockout_minutes=30,
        jwt_expire_hours=1,
        jwt_secret="audit-login-test-secret-0123456789",
    )
    state = SimpleNamespace(
        user_repo=users,
        audit_service=service,
        audit_repo=None,
        config=SimpleNamespace(auth=auth),
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        headers={},
        client=SimpleNamespace(host="10.0.0.5"),
        state=SimpleNamespace(request_id="rid-1", client_ip="10.0.0.5"),
    )


def _user() -> User:
    return User(
        user_id="u1", username="u1", hashed_password=_HASH,
        role=UserRole.USER, status=UserStatus.ACTIVE,
    )


async def _fail(req) -> int:
    with pytest.raises(HTTPException) as ei:
        await login(req, UserLoginRequest(user_id="u1", password="wrong-pw-123"), Response())
    return ei.value.status_code


def _of(repo: _AuditRepo, event: AuditEvent) -> list[dict]:
    return [e for e in repo.events if e["event_type"] == event.value]


# ─── ① 이벤트 이름 ───


async def test_login_success_records_user_login() -> None:
    repo = _AuditRepo()
    req = _request(_Users(_user()), AuditService(_audit_config(), repo))

    await login(req, UserLoginRequest(user_id="u1", password=_PW), Response())

    assert [e["event_type"] for e in repo.events] == [AuditEvent.USER_LOGIN.value]
    assert repo.events[0]["user_id"] == "u1"
    assert repo.events[0]["ip_address"] == "10.0.0.5"


async def test_logout_records_user_logout() -> None:
    repo = _AuditRepo()
    req = _request(_Users(_user()), AuditService(_audit_config(), repo))

    await logout(req, Response(), {"sub": "u1"})

    assert [e["event_type"] for e in repo.events] == [AuditEvent.USER_LOGOUT.value]
    assert repo.events[0]["user_id"] == "u1"


# ─── ② 잠금 실패도 기록 · ③ 연속 실패 경보 ───


async def test_locking_failure_is_audited_and_raises_alert() -> None:
    """기본 임계(잠금 5 · 경보 5)면 다섯 번째 실패에서 잠김과 경보가 함께 남는다."""
    repo = _AuditRepo()
    req = _request(_Users(_user()), AuditService(_audit_config(5), repo), max_attempts=5)

    codes = [await _fail(req) for _ in range(5)]

    assert codes == [401, 401, 401, 401, 423]
    fails = _of(repo, AuditEvent.LOGIN_FAIL)
    assert len(fails) == 5  # 종전: 잠그는 다섯 번째가 빠져 4건
    assert fails[-1]["detail"]["extra"] == {"fail_count": 5, "locked": True}
    assert fails[0]["detail"]["extra"]["locked"] is False

    alerts = _of(repo, AuditEvent.SECURITY_ALERT)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["user_id"] == "u1"
    assert alert["ip_address"] == "10.0.0.5"
    assert alert["detail"]["severity"] == "critical"
    assert "5회 연속 실패" in alert["detail"]["extra"]["detail"]


async def test_below_threshold_does_not_alert() -> None:
    repo = _AuditRepo()
    req = _request(_Users(_user()), AuditService(_audit_config(5), repo))

    for _ in range(4):
        assert await _fail(req) == 401

    assert len(_of(repo, AuditEvent.LOGIN_FAIL)) == 4
    assert _of(repo, AuditEvent.SECURITY_ALERT) == []


async def test_success_resets_consecutive_count() -> None:
    """실패 3 → 성공 → 실패 4: `login_fail` 행은 7건이지만 연속은 4회라 경보가 없다."""
    repo = _AuditRepo()
    req = _request(_Users(_user()), AuditService(_audit_config(5), repo))

    for _ in range(3):
        await _fail(req)
    await login(req, UserLoginRequest(user_id="u1", password=_PW), Response())
    for _ in range(4):
        await _fail(req)

    assert len(_of(repo, AuditEvent.LOGIN_FAIL)) == 7
    assert _of(repo, AuditEvent.SECURITY_ALERT) == []


async def test_alert_threshold_below_lockout_fires_before_lock() -> None:
    """경보 임계가 잠금보다 낮으면 잠기기 전에 경보가 먼저 남는다(설정 존중)."""
    repo = _AuditRepo()
    req = _request(_Users(_user()), AuditService(_audit_config(3), repo), max_attempts=5)

    for _ in range(3):
        assert await _fail(req) == 401

    alerts = _of(repo, AuditEvent.SECURITY_ALERT)
    assert len(alerts) == 1
    assert "3회 연속 실패" in alerts[0]["detail"]["extra"]["detail"]


# ─── 보안 경고 표가 문구를 보여 준다 ───


def test_alert_table_reads_nested_detail_text() -> None:
    """`AuditService`는 경고 문구를 DB 상세의 `extra.detail`에 둔다.

    화면이 최상위 `detail`만 읽으면 내용 칸에 JSON 원문이 찍힌다.
    """
    src = _ADMIN_JS.read_text(encoding="utf-8")
    body = src.split("async function loadSecurityAlerts()")[1].split("\n    }\n")[0]
    assert "a.detail.extra" in body
