"""plans/125 A-7 — 관측 소스 인가 `allowed_sources`(D-270 ⑰ · D-272 ⑩ · D-281 ⑨ · D-285).

DB가 없는 관측 소스(APM 게이트웨이 등)의 사용자별 인가를 D-232 DB 인가와 같은 규칙으로 고정한다.
  1. 값의 의미 — `None`=전체 허용(기존 사용자) · `[]`=없음 · 목록=그 시스템만 · 관리자 전체.
  2. 신규 가입자 = `AUTH_DEFAULT_ALLOWED_SOURCES`(빈 값이면 없음 — 안전 실패).
  3. 처리기(`apm_query`) 실행 경계에서 거부 — 조회 0 · 거부 문구·결과에 소스 이름을 싣지 않는다.
  4. 요청 스코프 — 라우트가 매 턴 사용자 값으로 싣고, task 격리 입력이 처리기로 옮긴다.
  5. 관리 화면 — 후보 API(관리자 전용) · DB 권한과 **별도** 저장 · 감사 기록.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.admin import (
    list_observation_sources,
    list_users,
    update_user_source_permissions,
)
from src.api.routes.query import _with_current_identity
from src.api.schemas import UpdateSourcePermissionsRequest, UserInfoResponse
from src.config import DBHubConfig
from src.domain.user import User, UserRole, UserStatus
from src.orchestration import apm_query as aq
from src.orchestration.db_access import is_access_denied_result
from src.orchestration.subagents import _make_isolated_input
from src.routing.db_authz import (
    SOURCE_ACCESS_DENIED_MESSAGE,
    is_source_allowed,
    parse_allowed_sources,
)
from src.security.audit_service import AuditService
from src.state import create_initial_state

_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"
_ADMIN_JS = _STATIC / "js" / "admin.js"
_DASHBOARD_HTML = _STATIC / "admin" / "dashboard.html"


# ─── 1. 값의 의미 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("allowed", "role", "expected"),
    [
        (None, "user", True),          # 기존 사용자(NULL) = 전체 허용
        ([], "user", False),           # 없음
        (["apm"], "user", True),
        (["prometheus"], "user", False),
        ([], "admin", True),           # 관리자 전체(D-232 ⑤ 대칭)
        ([""], "user", False),
    ],
)
def test_is_source_allowed_semantics(allowed, role, expected) -> None:
    assert is_source_allowed("apm", allowed, role) is expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("", []), (None, []), ("apm, prometheus", ["apm", "prometheus"])]
)
def test_parse_allowed_sources(raw, expected) -> None:
    assert parse_allowed_sources(raw) == expected


def test_denied_message_does_not_name_a_source() -> None:
    """권한 밖 소스는 안내에도 드러내지 않는다(D-264 ② 선례)."""
    for word in ("APM", "apm", "제니퍼", "WAS", "Prometheus", "프로메테우스"):
        assert word not in SOURCE_ACCESS_DENIED_MESSAGE


# ─── 2. 신규 가입자 기본값 ───────────────────────────────────────────────────


async def _register_with(default_sources: str) -> User:
    from src.api.routes.user_auth import register
    from src.api.schemas import UserRegisterRequest

    created: dict = {}
    user_repo = MagicMock()
    user_repo.exists = AsyncMock(return_value=False)
    user_repo.create = AsyncMock(side_effect=lambda user: created.setdefault("user", user))
    config = SimpleNamespace(auth=SimpleNamespace(
        password_min_length=8, default_allowed_db_ids="", default_allowed_sources=default_sources))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(user_repo=user_repo, config=config)),
        state=SimpleNamespace(client_ip=None, request_id=None),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={},
    )
    body = UserRegisterRequest(user_id="new_user", username="새 사용자", password="password123")
    with patch("src.api.routes.user_auth._log_audit_event", AsyncMock(return_value=True)):
        await register(request, body)
    return created["user"]


@pytest.mark.asyncio
async def test_new_signup_gets_no_source_when_setting_empty() -> None:
    user = await _register_with("")
    assert user.allowed_sources == []


@pytest.mark.asyncio
async def test_new_signup_gets_configured_sources() -> None:
    user = await _register_with("apm")
    assert user.allowed_sources == ["apm"]


def test_setting_is_declared_with_help() -> None:
    from src.api.settings_catalog import field_index

    assert "AUTH_DEFAULT_ALLOWED_SOURCES" in field_index()


def test_existing_user_record_defaults_to_allow_all() -> None:
    """기존 행(컬럼 NULL)·기본 생성 사용자는 전체 허용이다(D-272 ⑩)."""
    user = User(user_id="u", username="u", hashed_password="x")
    assert user.allowed_sources is None
    assert user.to_auth_dict()["allowed_sources"] is None


def test_user_self_info_response_is_unchanged() -> None:
    """사용자 본인 응답(`/auth/me`·로그인)에는 소스 권한 칸을 더하지 않는다 — 관리 화면 전용."""
    assert "allowed_sources" not in UserInfoResponse.model_fields


# ─── 3. 처리기 실행 경계 ─────────────────────────────────────────────────────


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        dbhub=DBHubConfig(source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2),
    )


class _Gateway:
    def __init__(self) -> None:
        self.opened = 0

    def factory(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        env = {"rows": [{"instance_id": 1}], "row_count": 1, "source_kind": "apm_api",
               "tool": name, "limits": []}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(env))], isError=False)


def _isolated(**extra) -> dict:
    host = {"field": "hostname", "op": "=", "value": "web01"}
    return {"parsed_requirements": {"filter_conditions": [host]}, "conversation_context": {},
            "thread_id": "th-1", **extra}


@pytest.fixture
def gateway(monkeypatch) -> _Gateway:
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    return gw


@pytest.mark.asyncio
async def test_unauthorized_user_is_denied_without_query(gateway) -> None:
    res = await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "views": []},
                                 _isolated(allowed_sources=[], user_role="user"),
                                 llm=None, app_config=_cfg())
    assert gateway.opened == 0, "권한 밖이면 게이트웨이를 열지 않는다"
    assert is_access_denied_result(res)
    assert res["final_response"] == SOURCE_ACCESS_DENIED_MESSAGE
    dumped = json.dumps(res, ensure_ascii=False)
    assert "제니퍼" not in dumped and "apm_query" not in dumped and "source_status" not in dumped


@pytest.mark.asyncio
@pytest.mark.parametrize(("allowed", "role"), [(None, "user"), (["apm"], "user"), ([], "admin")])
async def test_authorized_user_queries(gateway, allowed, role) -> None:
    res = await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "views": []},
                                 _isolated(allowed_sources=allowed, user_role=role),
                                 llm=None, app_config=_cfg())
    assert gateway.opened == 1
    assert not is_access_denied_result(res)
    assert res["organized_data"]["rows"]


# ─── 4. 요청 스코프 전달 ─────────────────────────────────────────────────────


def test_state_and_isolated_input_carry_allowed_sources() -> None:
    state = create_initial_state(user_query="q", allowed_sources=["apm"])
    assert state["allowed_sources"] == ["apm"]
    isolated = _make_isolated_input({"task_id": "t1", "agent": "apm_query", "sub_query": "q"},
                                    dict(state), {})
    assert isolated["allowed_sources"] == ["apm"]


def test_every_turn_reloads_allowed_sources_from_token_user() -> None:
    """체크포인터는 델타만 병합한다 — 권한 회수가 기존 스레드에서도 즉시 반영돼야 한다."""
    delta = _with_current_identity({}, {"role": "user", "allowed_sources": []})
    assert delta["allowed_sources"] == []


def test_routes_pass_allowed_sources_at_every_state_entry() -> None:
    text = (Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / "query.py").read_text(
        encoding="utf-8")
    assert text.count('allowed_db_ids=current_user.get("allowed_db_ids"),') == 3
    assert text.count('allowed_sources=current_user.get("allowed_sources"),') == 3


# ─── 5. 관리 API ──────────────────────────────────────────────────────────────


class _AuditRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_event(self, event: dict) -> None:
        self.events.append(event)


def _request(target: User | None, active_sources: dict[str, str] | None = None):
    captured = _AuditRepo()
    audit_cfg = SimpleNamespace(jsonl_enabled=False, db_enabled=True,
                                alert_on_failed_login=5, alert_on_large_result=5000)
    users = SimpleNamespace(get_by_user_id=AsyncMock(return_value=target), update=AsyncMock(),
                            list_all=AsyncMock(return_value=[target] if target else []))
    state = SimpleNamespace(
        user_repo=users,
        audit_service=AuditService(audit_cfg, captured),
        config=SimpleNamespace(dbhub=DBHubConfig(source_endpoints=active_sources or {})),
    )
    req = SimpleNamespace(app=SimpleNamespace(state=state),
                          state=SimpleNamespace(client_ip="10.0.0.9", request_id="rid"))
    return req, users, captured


def _user(**overrides) -> User:
    fields = dict(user_id="u1", username="u1", hashed_password="x", role=UserRole.USER,
                  status=UserStatus.ACTIVE, allowed_db_ids=["db_a"], allowed_sources=None)
    fields.update(overrides)
    return User(**fields)


@pytest.mark.asyncio
async def test_source_permission_update_keeps_db_permissions_and_is_audited() -> None:
    target = _user()
    req, users, captured = _request(target)

    resp = await update_user_source_permissions(
        req, "u1", UpdateSourcePermissionsRequest(allowed_sources=["apm"]), {"sub": "root"})

    assert target.allowed_sources == ["apm"]
    assert target.allowed_db_ids == ["db_a"], "DB 권한은 그대로다(별도 요청)"
    users.update.assert_awaited_once()
    assert resp.allowed_sources == ["apm"] and resp.allowed_db_ids == ["db_a"]
    extra = captured.events[0]["detail"]["extra"]
    assert extra["action"] == "user_source_permissions_update"
    assert extra["changes"] == {"allowed_sources": {"before": None, "after": ["apm"]}}


@pytest.mark.asyncio
async def test_source_candidates_list_non_db_systems_with_activity() -> None:
    req, _, _ = _request(None, {"apm": "http://127.0.0.1:9096/sse"})
    data = await list_observation_sources(req, {"sub": "root"})
    codes = {s["code"]: s for s in data["sources"]}
    assert codes["apm"]["active"] is True and codes["apm"]["label"]
    assert "polestar" not in codes, "DB 시스템은 후보가 아니다(DB 권한을 따른다)"

    req, _, _ = _request(None)
    data = await list_observation_sources(req, {"sub": "root"})
    assert all(s["active"] is False for s in data["sources"]), "엔드포인트 없음 = 비활성"


@pytest.mark.asyncio
async def test_user_list_carries_allowed_sources() -> None:
    req, _, _ = _request(_user(allowed_sources=[]))
    rows = await list_users(req, {"sub": "root"})
    assert rows[0].allowed_sources == []


def test_admin_endpoints_require_admin() -> None:
    import time

    import jwt
    from fastapi.testclient import TestClient

    from src.api.server import create_app
    from src.config import AdminConfig, AppConfig, AuthConfig

    secret = "plan125-src-perm-auth-secret-0123456789"
    config = AppConfig(_env_file=None,
                       auth=AuthConfig(_env_file=None, enabled=True, jwt_secret=secret),
                       admin=AdminConfig(_env_file=None,
                                         jwt_secret="plan125-src-perm-admin-secret-0123456789"))
    client = TestClient(create_app(config))
    token = jwt.encode({"sub": "u1", "name": "U", "role": "user", "type": "user",
                        "exp": int(time.time()) + 3600}, secret, algorithm="HS256")
    for method, path, body in (("get", "/api/v1/admin/sources", None),
                               ("put", "/api/v1/admin/users/u1/source-permissions",
                                {"allowed_sources": ["apm"]})):
        call = getattr(client, method)
        kwargs = {"json": body} if body is not None else {}
        assert call(path, **kwargs).status_code == 401
        assert call(path, headers={"Authorization": f"Bearer {token}"},
                    **kwargs).status_code == 403


# ─── 6. 관리 화면 ─────────────────────────────────────────────────────────────


def _source_js_block() -> str:
    text = _ADMIN_JS.read_text(encoding="utf-8")
    start = text.index("// --- 사용자 관측 소스 권한 (plans/125 A-7")
    end = text.index("function escapeHtml(", start)
    return text[start:end]


def test_users_table_has_source_column_after_db_column() -> None:
    html = _DASHBOARD_HTML.read_text(encoding="utf-8")
    table = html[html.index('id="usersTable"'):html.index('id="usersBody"')]
    db_col, src_col = table.index("조회 가능 DB"), table.index("조회 가능 소스")
    assert db_col < src_col < table.index("마지막 로그인")


def test_source_cell_is_rendered_and_saved_separately() -> None:
    text = _ADMIN_JS.read_text(encoding="utf-8")
    assert 'renderSourceCell(srcTd, u.user_id, u.allowed_sources, u.role === "admin");' in text
    block = _source_js_block()
    assert '"/api/v1/admin/sources"' in block
    assert '"/source-permissions"' in block and "{allowed_sources: allowed}" in block
    assert '"활성 관측 소스 없음"' in block and '"전체 허용"' in block


def test_source_block_has_no_html_injection_sink() -> None:
    block = _source_js_block()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in block, sink
