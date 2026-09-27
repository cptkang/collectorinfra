"""알람 분석 테스트 API(`/alarm/analyze-test`·`/raw`)는 관리자 전용이다 (D-263 잔여 후속).

종전에는 두 라우트가 `require_user`만 거쳐, 로그인 사용자면 임의 db_id로 다른 존의 이력·
프로세스를 조회하고 화면 게시(`push_to_ui`)·실제 통보(`dry_run=false`)까지 할 수 있었다.
시험 도구이므로 `require_admin_user`(DB 실시간 역할 admin 또는 운영자 break-glass 토큰)로 좁힌다.

의존성 오버라이드 없이 실제 토큰·실제 가드를 태운다 — 서명만이 아니라 역할 판정까지 본다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.domain.alarm import AlarmAnalysisResult
from src.api.routes import alarm as alarm_routes
from src.domain.user import User, UserRole, UserStatus
from src.routing.zones import ZONE_GONGJON

AUTH_SECRET = "auth-secret-analyze-test-0123456789ab"
ADMIN_SECRET = "admin-secret-analyze-test-0123456789ab"
DB_ID = "polestar_cm_gp"

RAW = "/api/v1/alarm/analyze-test/raw"
STRUCTURED = "/api/v1/alarm/analyze-test"

_PAYLOAD = {
    "dbId": DB_ID,
    "serverName": "admin-only-test",
    "hostname": "admin-only-test",
    "ipAddress": "10.0.0.1",
    "resourceAncestry": "",
    "alarmId": "ADMIN-ONLY-1",
    "severity": 2,
    "alarmStatus": "NOT_ACK",
    "resourceType": "server.Server",
    "resourceName": "CPU",
    "alarmName": "관리자 전용 회귀",
    "alarmTime": "20260927090000",
    "conditions": "cpu>90",
    "conditionLog": "cpu=95",
}

# 라우트별 최소 본문 — 화면 게시·이력·프로세스 조회를 끄고 분석(가짜 노드)만 돈다.
_BODIES = {
    RAW: {
        "message": json.dumps(_PAYLOAD, ensure_ascii=False),
        "push_to_ui": False,
        "query_history": False,
        "query_process": False,
    },
    STRUCTURED: {
        "db_id": DB_ID,
        "server_name": "admin-only-test",
        "alarm_name": "관리자 전용 회귀",
        "push_to_ui": False,
        "query_history": False,
        "query_process": False,
    },
}
ROUTES = list(_BODIES)


async def _fake_analyzer_node(state, config):  # noqa: ANN001
    return {
        "analysis_result": AlarmAnalysisResult(
            alarm_event=state["alarm_event"],
            severity_label="경고",
            summary="요약",
            probable_cause="원인",
            recommended_action="조치",
            notification_channels=[],
        )
    }


class _Users:
    """user_repo 대역 — DB에 저장된 역할이 판정 근거다(토큰 클레임이 아니라)."""

    def __init__(self, users: dict[str, User]) -> None:
        self._users = users

    async def get_by_user_id(self, user_id: str):  # noqa: ANN201
        return self._users.get(user_id)


def _user(user_id: str, role: UserRole, zones: list[str] | None = None) -> User:
    return User(
        user_id=user_id, username=user_id, hashed_password="x",
        role=role, status=UserStatus.ACTIVE, alarm_zones=zones or [],
    )


_USERS = {
    "plain": _user("plain", UserRole.USER),
    "zoned": _user("zoned", UserRole.USER, [ZONE_GONGJON]),  # 알림 존이 있는 운영 사용자
    "boss": _user("boss", UserRole.ADMIN),
}


def _exp() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def _user_token(sub: str, role: str = "user") -> str:
    return jwt.encode(
        {"sub": sub, "name": sub, "role": role, "type": "user", "exp": _exp()},
        AUTH_SECRET, algorithm="HS256",
    )


def _operator_token() -> str:
    """`/admin/login`이 발급하는 운영자(break-glass) 토큰 — 운영자 시크릿·type=admin."""
    return jwt.encode({"sub": "operator", "type": "admin", "exp": _exp()},
                      ADMIN_SECRET, algorithm="HS256")


@pytest.fixture
def client(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(
        "noise_gate.application.nodes.alarm_analyzer.alarm_analyzer_node",
        _fake_analyzer_node,
    )

    def make(*, auth_enabled: bool = True) -> TestClient:
        app = FastAPI()
        app.include_router(alarm_routes.router, prefix="/api/v1")
        app.state.config = SimpleNamespace(
            auth=SimpleNamespace(enabled=auth_enabled, jwt_secret=AUTH_SECRET),
            admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
            alarm=SimpleNamespace(
                get_notification_channels=lambda: [],
                history_enabled=False,
                process_enrich_enabled=False,
            ),
            workb=SimpleNamespace(),
            noise_gate=SimpleNamespace(
                enable_noise_gate=False,
                enable_llm_actionability=False,
                decision_store_path=str(tmp_path / "decisions.jsonl"),
            ),
        )
        app.state.user_repo = _Users(_USERS)
        return TestClient(app)

    return make


def _post(tc: TestClient, route: str, token: str | None):  # noqa: ANN202
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return tc.post(route, json=_BODIES[route], headers=headers)


@pytest.mark.parametrize("route", ROUTES)
def test_plain_user_is_forbidden(client, route):  # noqa: ANN001
    """일반 사용자 토큰 — 수정 전 200(다른 존 조회·게시·발송 가능), 수정 후 403."""
    resp = _post(client(), route, _user_token("plain"))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "관리자 권한이 필요합니다."


@pytest.mark.parametrize("route", ROUTES)
def test_zone_operator_is_forbidden(client, route):  # noqa: ANN001
    """알림 존이 있는 운영 사용자도 시험 도구는 못 쓴다 — 판정은 존이 아니라 관리자 역할이다."""
    resp = _post(client(), route, _user_token("zoned"))
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("route", ROUTES)
def test_role_claim_alone_is_not_enough(client, route):  # noqa: ANN001
    """토큰 클레임에 role=admin이 있어도 DB 역할이 user면 거부한다(실시간 역할 판정)."""
    resp = _post(client(), route, _user_token("plain", role="admin"))
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("route", ROUTES)
def test_admin_role_user_is_allowed(client, route):  # noqa: ANN001
    resp = _post(client(), route, _user_token("boss", role="admin"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["error"] is None


@pytest.mark.parametrize("route", ROUTES)
def test_operator_break_glass_token_is_allowed(client, route):  # noqa: ANN001
    resp = _post(client(), route, _operator_token())
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("route", ROUTES)
def test_missing_token_is_unauthorized(client, route):  # noqa: ANN001
    resp = _post(client(), route, None)
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize("route", ROUTES)
def test_dev_mode_still_open(client, route):  # noqa: ANN001
    """AUTH_ENABLED=false(개발 모드)는 종전대로 통과한다(D-069 ①)."""
    resp = _post(client(auth_enabled=False), route, None)
    assert resp.status_code == 200, resp.text
