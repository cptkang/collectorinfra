"""감사 로그 이벤트 필터가 옛 기록 이름까지 찾는다 (감사 결함 ①).

2026-09-27 이전 로그인·로그아웃은 `login`·`logout`으로 기록됐다. 기록 이름은 `AuditEvent`
(`user_login`·`user_logout`)로 맞추되, 이미 쌓인 행은 DB를 고치지 않고 **조회가 두 이름을
함께 찾는다**. 또 관리자 화면의 필터 값은 실제 기록 이름이어야 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.domain.audit import AuditEvent, event_type_filter_values
from src.infrastructure.audit_repository import PostgresAuditRepository

_ROOT = Path(__file__).resolve().parents[2]


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        return 0

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return []


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def test_filter_values_include_legacy_names() -> None:
    assert event_type_filter_values("user_login") == ["user_login", "login"]
    assert event_type_filter_values("user_logout") == ["user_logout", "logout"]
    assert event_type_filter_values("login_fail") == ["login_fail"]


@pytest.mark.asyncio
async def test_paginated_query_matches_legacy_login_rows() -> None:
    conn = _FakeConn()
    repo = PostgresAuditRepository(_FakePool(conn))  # type: ignore[arg-type]

    await repo.query_logs_paginated(event_type="user_login")

    for query, args in conn.calls:  # 건수·데이터 쿼리 둘 다
        assert "event_type = ANY(" in query
        assert ["user_login", "login"] in args


@pytest.mark.asyncio
async def test_query_logs_matches_legacy_logout_rows() -> None:
    conn = _FakeConn()
    repo = PostgresAuditRepository(_FakePool(conn))  # type: ignore[arg-type]

    await repo.query_logs(event_type="user_logout")

    query, args = conn.calls[0]
    assert "event_type = ANY(" in query
    assert ["user_logout", "logout"] in args


def _filter_option_values() -> list[str]:
    html = (_ROOT / "src" / "static" / "admin" / "dashboard.html").read_text(encoding="utf-8")
    select = html.split('id="filterEventType"')[1].split("</select>")[0]
    return [v for v in re.findall(r'<option value="([^"]*)"', select) if v]


def test_dashboard_filter_values_are_recorded_event_names() -> None:
    """필터에 없는 이름을 두면 그 선택지는 영원히 0건이다."""
    recorded = {e.value for e in AuditEvent}
    values = _filter_option_values()
    assert values
    assert set(values) <= recorded, set(values) - recorded


def test_dashboard_filter_offers_admin_action() -> None:
    """사용자 관리 변경·감사 로그 정리는 `admin_action`으로 남는다 — 필터로 찾을 수 있어야 한다."""
    assert AuditEvent.ADMIN_ACTION.value in _filter_option_values()


def test_user_auth_records_only_enum_event_names() -> None:
    """라우트가 이벤트 이름을 문자열로 적으면 필터 값과 다시 어긋난다(종전 `login`·`logout`)."""
    src = (_ROOT / "src" / "api" / "routes" / "user_auth.py").read_text(encoding="utf-8")
    literals = re.findall(r'"event_type":\s*"([^"]+)"', src)
    assert literals == [], literals
