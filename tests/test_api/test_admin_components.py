"""plans/191 W2 — 분리 컴포넌트 설정 프록시 (`spec/SPEC-component-config-admin.md` §5 · D-345).

가짜 컴포넌트는 httpx `MockTransport`로 둔다(망·LLM 호출 0).

- 인가: 미인증 401 · 비관리자(role=user) 403 · 운영자(type=admin) 200
- `{name}`: 미설정·허용목록 밖 404 · `COMPONENT_ADMIN_ENDPOINTS` 비면 빈 목록
- 실패 사유: connect_error · timeout · unauthorized(401) · not_enabled(404) ·
  bad_response · http_error
- 한 컴포넌트 실패가 목록 전체를 깨지 않는다 · 리다이렉트 미추종
- PUT: 본문 그대로 전달 · `X-Request-ID` 전파 · 감사엔 키 이름만 · 400/409 본문 중계
- 시크릿 항목 `file_value`·`effective_value`는 응답에서 지운다
- UI 정적 단언(대상 선택·textContent·리로드 숨김·범위 밖 안내 축소)과 node 순수 함수 실행
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.api.routes import admin_components as routes
from src.config import COMPONENT_ADMIN_NAMES, ComponentAdminConfig
from src.infrastructure.component_admin_client import (
    ComponentAdminClient,
    parse_tokens,
    resolve_endpoints,
)

ROOT = Path(__file__).resolve().parent.parent.parent
ADMIN_SECRET = "admin-secret-for-test-0123456789abcdef"
USER_SECRET = "user-secret-for-test-0123456789abcdef"
SRE_TOKEN = "sre-admin-token"

_STATUS = {
    "component": {"name": "sre_agent", "version": "0.1.0", "pid": 4242,
                  "started_at": "2026-10-21T09:00:00+09:00",
                  "restart_hint": "cd sre_agent && .venv/bin/python -m sre_agent.run_service"},
    "writable": True, "write_block_reason": None, "restart_pending_keys": [],
}


def _schema() -> dict[str, Any]:
    return {
        **_STATUS,
        "cwd": "/opt/sre_agent",
        "sources": [{"path": "/opt/sre_agent/.env", "role": "write_target",
                     "exists": True, "priority": 1}],
        "groups": [{"group_key": "llm", "title": "LLM", "settings": [
            # 계약: default·effective_value는 `.env` 표기 문자열
            # (bool "true" · list JSON · 없음 None)
            {"env_key": "MAX_STEPS", "group_key": "llm", "type": "int", "default": "500",
             "file_value": "10", "effective_value": "10", "is_secret": False, "is_set": True},
            {"env_key": "PLAYBOOK_ENABLED", "group_key": "llm", "type": "bool", "default": "true",
             "file_value": None, "effective_value": "true", "is_secret": False, "is_set": True},
            {"env_key": "ZONES", "group_key": "llm", "type": "json_list", "default": '["bank"]',
             "file_value": None, "effective_value": '["bank"]', "is_secret": False,
             "is_set": True},
            {"env_key": "OPTIONAL_URL", "group_key": "llm", "type": "string", "default": None,
             "file_value": None, "effective_value": None, "is_secret": False, "is_set": False},
            # 컴포넌트가 실수로 시크릿 값을 실었다 — 본체가 한 번 더 지워야 한다
            {"env_key": "API_KEY", "group_key": "llm", "type": "secret", "file_value": "leak-1",
             "effective_value": "leak-2", "is_secret": True, "is_set": True},
        ]}],
        "warnings": [],
    }


class _FakeComponents:
    """호스트별로 다른 장애를 내는 가짜 컴포넌트 묶음."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.put_status = 200
        self.put_body: dict[str, Any] = {
            "saved_keys": ["MAX_STEPS"],
            "ignored_keys": [{"key": "API_KEY", "reason": "secret"}],
            "errors": [], "dry_run": False, "restart_required": True,
            "restart_pending_keys": ["MAX_STEPS"], "audit_logged": True,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        host = request.url.host
        if host == "sre.test":
            if request.headers.get("authorization") != f"Bearer {SRE_TOKEN}":
                return httpx.Response(401, json={"error": "unauthorized"})
            if request.url.path == "/admin/config/status":
                return httpx.Response(200, json=_STATUS)
            if request.url.path == "/admin/config/schema":
                return httpx.Response(200, json=_schema())
            if request.url.path == "/admin/config" and request.method == "PUT":
                return httpx.Response(self.put_status, json=self.put_body)
            return httpx.Response(404, json={"detail": "nf"})
        if host == "apm.test":
            raise httpx.ConnectError("refused", request=request)
        if host == "mcp.test":
            raise httpx.ReadTimeout("slow", request=request)
        if host == "alarm.test":
            return httpx.Response(404, json={"detail": "Not Found"})
        if host == "fabrix.test":
            return httpx.Response(302, headers={"location": "http://evil.test/"})
        if host == "bad.test":
            return httpx.Response(200, text="<html>not json</html>")
        raise AssertionError(f"예상 밖 호스트: {host}")  # 리다이렉트 추종 시 evil.test로 온다


_ENDPOINTS = {
    "sre_agent": "http://sre.test/",
    "apm_gateway": "http://apm.test",
    "mcp_server": "http://mcp.test",
    "alarm_server": "http://alarm.test",
    "fabrix_proxy": "http://fabrix.test",
}


def _component_config(endpoints: dict[str, str] | None = None,
                      tokens: dict[str, str] | None = None) -> ComponentAdminConfig:
    return ComponentAdminConfig(
        endpoints=_ENDPOINTS if endpoints is None else endpoints,
        tokens=SecretStr(json.dumps({"sre_agent": SRE_TOKEN} if tokens is None else tokens)),
        timeout_seconds=1.0,
        _env_file=None,
    )


class _UserRepo:
    async def get_by_user_id(self, user_id: str) -> Any:
        return SimpleNamespace(
            is_active=True,
            to_auth_dict=lambda: {"sub": user_id, "role": "user"},
        )


def _app(fake: _FakeComponents, *, endpoints: dict[str, str] | None = None,
         auth: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=auth, jwt_secret=USER_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
        component_admin=_component_config(endpoints),
    )
    app.state.user_repo = _UserRepo()
    app.state.component_admin_transport = httpx.MockTransport(fake.handler)

    @app.middleware("http")
    async def _request_id(request, call_next):  # noqa: ANN001, ANN202
        request.state.request_id = "rid-1234"
        return await call_next(request)

    return TestClient(app)


@pytest.fixture
def audits(monkeypatch) -> list[dict[str, Any]]:  # noqa: ANN001
    captured: list[dict[str, Any]] = []

    async def _log(request, admin_id, event, extra):  # noqa: ANN001, ANN202
        captured.append({"admin_id": admin_id, "event": event, "extra": extra})
        return True

    monkeypatch.setattr(routes, "log_admin_event", _log)
    return captured


def _token(secret: str, kind: str, sub: str) -> str:
    return jwt.encode({"sub": sub, "type": kind, "exp": int(time.time()) + 600}, secret,
                      algorithm="HS256")


# --- 인가 ---


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/admin/components"),
    ("GET", "/api/v1/admin/components/sre_agent/config"),
    ("PUT", "/api/v1/admin/components/sre_agent/config"),
])
def test_unauthenticated_401_and_non_admin_403(method, path):  # noqa: ANN001
    fake = _FakeComponents()
    client = _app(fake, auth=True)
    body = {"settings": {}} if method == "PUT" else None
    assert client.request(method, path, json=body).status_code == 401
    user = {"Authorization": "Bearer " + _token(USER_SECRET, "user", "u1")}
    assert client.request(method, path, json=body, headers=user).status_code == 403
    # 사용자 시크릿으로 서명했지만 type=admin — 종류 교차 통과 없음
    forged = {"Authorization": "Bearer " + _token(USER_SECRET, "admin", "u1")}
    assert client.request(method, path, json=body, headers=forged).status_code in (401, 403)
    assert fake.calls == []  # 인가 실패면 컴포넌트를 부르지 않는다


def test_break_glass_admin_passes():
    fake = _FakeComponents()
    client = _app(fake, auth=True)
    headers = {"Authorization": "Bearer " + _token(ADMIN_SECRET, "admin", "ops")}
    assert client.get("/api/v1/admin/components", headers=headers).status_code == 200


# --- 목록 ---


def test_empty_endpoints_returns_empty_list_without_calls():
    fake = _FakeComponents()
    client = _app(fake, endpoints={})
    resp = client.get("/api/v1/admin/components")
    assert resp.status_code == 200
    assert resp.json() == {"components": [], "warnings": []}
    assert fake.calls == []


def test_list_isolates_each_failure_reason():
    fake = _FakeComponents()
    client = _app(fake)
    resp = client.get("/api/v1/admin/components")
    assert resp.status_code == 200
    rows = {row["name"]: row for row in resp.json()["components"]}
    assert list(rows) == list(COMPONENT_ADMIN_NAMES)  # 허용 상수 순서
    assert rows["sre_agent"]["reachable"] is True
    assert rows["sre_agent"]["status"]["component"]["version"] == "0.1.0"
    assert rows["apm_gateway"]["reason"] == "connect_error"
    assert rows["mcp_server"]["reason"] == "timeout"
    assert rows["alarm_server"]["reason"] == "not_enabled"
    assert rows["fabrix_proxy"]["reason"] == "http_error"
    for name in ("apm_gateway", "mcp_server", "alarm_server", "fabrix_proxy"):
        assert rows[name]["reachable"] is False and rows[name]["status"] is None
        assert rows[name]["message"]  # 침묵 금지 — 문구가 있다


def test_redirect_is_not_followed():
    fake = _FakeComponents()
    client = _app(fake, endpoints={"fabrix_proxy": "http://fabrix.test"})
    row = client.get("/api/v1/admin/components").json()["components"][0]
    assert row["reason"] == "http_error" and "리다이렉트" in row["message"]
    assert [c.url.host for c in fake.calls] == ["fabrix.test"]  # evil.test로 가지 않았다


def test_unknown_name_and_non_http_url_are_ignored_with_warning():
    config = _component_config(endpoints={
        "sre_agent": "http://sre.test", "evil": "http://evil.test", "mcp_server": "file:///etc",
    })
    resolved, warnings = resolve_endpoints(config)
    assert resolved == {"sre_agent": "http://sre.test"}
    assert any("evil" in w for w in warnings) and any("mcp_server" in w for w in warnings)


def test_bad_tokens_json_warns_without_echoing_value():
    config = ComponentAdminConfig(endpoints={}, tokens=SecretStr("not-json-secret"),
                                  _env_file=None)
    tokens, warnings = parse_tokens(config)
    assert tokens == {} and warnings
    assert all("not-json-secret" not in w for w in warnings)


def test_token_sent_only_to_its_component():
    fake = _FakeComponents()
    client = _app(fake)
    client.get("/api/v1/admin/components")
    for call in fake.calls:
        auth = call.headers.get("authorization")
        assert (auth == f"Bearer {SRE_TOKEN}") == (call.url.host == "sre.test")


def test_missing_token_surfaces_unauthorized():
    fake = _FakeComponents()
    client = _app(fake, endpoints={"sre_agent": "http://sre.test"})
    client.app.state.config.component_admin = _component_config(
        endpoints={"sre_agent": "http://sre.test"}, tokens={})
    row = client.get("/api/v1/admin/components").json()["components"][0]
    assert row["reason"] == "unauthorized"


# --- 스키마 ---


@pytest.mark.parametrize("name", ["evil", "sre_agent%2F..%2F", "../sre_agent"])
def test_unknown_or_unconfigured_name_404(name):  # noqa: ANN001
    fake = _FakeComponents()
    client = _app(fake, endpoints={"sre_agent": "http://sre.test"})
    assert client.get(f"/api/v1/admin/components/{name}/config").status_code == 404
    # 허용 상수지만 설정에 없는 이름도 404
    assert client.get("/api/v1/admin/components/mcp_server/config").status_code == 404
    assert fake.calls == []


def test_oversized_component_response_is_502(monkeypatch):  # noqa: ANN001
    """L-4 — 컴포넌트 응답이 상한을 넘으면 본문을 다 읽지 않고 502 구조화 오류로 돌려준다."""
    import src.infrastructure.component_admin_client as client_mod

    monkeypatch.setattr(client_mod, "MAX_RESPONSE_BYTES", 2048)
    fake = _FakeComponents()
    client = _app(fake)
    big = _schema()
    big["warnings"] = ["x" * 4096]
    original = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/config/schema":
            return httpx.Response(200, json=big)
        return original(request)

    client.app.state.component_admin_transport = httpx.MockTransport(handler)
    resp = client.get("/api/v1/admin/components/sre_agent/config")
    assert resp.status_code == 502
    assert resp.json()["reason"] == "bad_response" and "상한" in resp.json()["message"]
    assert client_mod.MAX_RESPONSE_BYTES == 2048
    # 상한 안의 정상 응답은 그대로 통과한다
    assert client.get("/api/v1/admin/components").json()["components"][0]["reachable"]


def test_schema_relayed_and_secret_values_stripped():
    fake = _FakeComponents()
    client = _app(fake)
    resp = client.get("/api/v1/admin/components/sre_agent/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["reachable"] is True and data["sources"][0]["role"] == "write_target"
    items = {i["env_key"]: i for g in data["groups"] for i in g["settings"]}
    assert items["MAX_STEPS"]["file_value"] == "10"
    assert items["PLAYBOOK_ENABLED"]["default"] == "true"  # 문자열 표기 그대로 중계
    assert items["ZONES"]["effective_value"] == '["bank"]'
    assert items["OPTIONAL_URL"]["default"] is None
    assert items["API_KEY"]["file_value"] is None and items["API_KEY"]["effective_value"] is None
    assert items["API_KEY"]["is_set"] is True
    assert "leak-" not in resp.text


@pytest.mark.parametrize("name,reason", [
    ("apm_gateway", "connect_error"), ("mcp_server", "timeout"),
    ("alarm_server", "not_enabled"), ("fabrix_proxy", "http_error"),
])
def test_schema_failure_is_502_with_reason(name, reason):  # noqa: ANN001
    fake = _FakeComponents()
    client = _app(fake)
    resp = client.get(f"/api/v1/admin/components/{name}/config")
    assert resp.status_code == 502
    assert resp.json()["reason"] == reason and resp.json()["message"]


def test_non_json_schema_is_bad_response():
    fake = _FakeComponents()
    client = _app(fake)
    client.app.state.config.component_admin = _component_config(
        endpoints={"sre_agent": "http://bad.test"})
    resp = client.get("/api/v1/admin/components/sre_agent/config")
    assert resp.status_code == 502 and resp.json()["reason"] == "bad_response"


# --- 저장 ---


def test_put_forwards_body_and_request_id_and_audits_key_names_only(audits):  # noqa: ANN001
    fake = _FakeComponents()
    client = _app(fake)
    body = {"settings": {"MAX_STEPS": "20"}, "reset_keys": ["OLD_KEY"], "dry_run": False}
    resp = client.put("/api/v1/admin/components/sre_agent/config", json=body)
    assert resp.status_code == 200
    assert resp.json()["restart_pending_keys"] == ["MAX_STEPS"]
    put = [c for c in fake.calls if c.method == "PUT"][0]
    assert json.loads(put.content) == body
    assert put.headers["x-request-id"] == "rid-1234"
    assert len(audits) == 1
    entry = audits[0]
    assert entry["event"] == "settings_update"
    assert entry["extra"] == {
        "component": "sre_agent", "component_request_id": "rid-1234",
        "saved_keys": ["MAX_STEPS"], "ignored_keys": ["API_KEY"],
    }
    assert "20" not in json.dumps(entry["extra"])  # 값은 남기지 않는다
    # 컴포넌트 감사 결과는 그대로 두고 본체 감사 결과를 따로 싣는다
    assert resp.json()["audit_logged"] is True and resp.json()["proxy_audit_logged"] is True


def test_put_keeps_component_audit_failure(audits):  # noqa: ANN001
    fake = _FakeComponents()
    fake.put_body = {**fake.put_body, "audit_logged": False}
    resp = _app(fake).put("/api/v1/admin/components/sre_agent/config",
                          json={"settings": {"MAX_STEPS": "20"}})
    assert resp.json()["audit_logged"] is False
    assert resp.json()["proxy_audit_logged"] is True


def test_put_generates_request_id_without_middleware(audits):  # noqa: ANN001
    fake = _FakeComponents()
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(auth=SimpleNamespace(enabled=False),
                                       component_admin=_component_config())
    app.state.component_admin_transport = httpx.MockTransport(fake.handler)
    TestClient(app).put("/api/v1/admin/components/sre_agent/config", json={"settings": {}})
    put = [c for c in fake.calls if c.method == "PUT"][0]
    assert len(put.headers["x-request-id"]) >= 8


@pytest.mark.parametrize("status,body", [
    (400, {"saved_keys": [], "ignored_keys": [],
           "errors": [{"key": "X", "message": "bad"}, {"key": "", "message": "로더 실패"}],
           "dry_run": False, "restart_required": False, "restart_pending_keys": []}),
    (409, {"error": "write_blocked", "reason": "cwd"}),
])
def test_put_relays_component_rejection_without_audit(audits, status, body):  # noqa: ANN001
    fake = _FakeComponents()
    fake.put_status, fake.put_body = status, body
    client = _app(fake)
    resp = client.put("/api/v1/admin/components/sre_agent/config", json={"settings": {"X": "1"}})
    assert resp.status_code == status and resp.json() == body
    assert audits == []


def test_dry_run_is_not_audited(audits):  # noqa: ANN001
    fake = _FakeComponents()
    fake.put_body = {**fake.put_body, "dry_run": True}
    client = _app(fake)
    resp = client.put("/api/v1/admin/components/sre_agent/config",
                      json={"settings": {"MAX_STEPS": "20"}, "dry_run": True})
    assert resp.status_code == 200 and audits == []


def test_put_failure_is_502(audits):  # noqa: ANN001
    fake = _FakeComponents()
    client = _app(fake)
    resp = client.put("/api/v1/admin/components/apm_gateway/config", json={"settings": {}})
    assert resp.status_code == 502 and resp.json()["reason"] == "connect_error"
    assert audits == []


def test_put_unknown_name_404_without_call(audits):  # noqa: ANN001
    fake = _FakeComponents()
    client = _app(fake)
    assert client.put("/api/v1/admin/components/evil/config", json={}).status_code == 404
    assert fake.calls == []


def test_client_never_builds_url_from_request_input():
    """URL은 설정값 + 고정 경로 3종뿐이다(SSRF)."""
    fake = _FakeComponents()
    client = _app(fake)
    client.get("/api/v1/admin/components")
    client.get("/api/v1/admin/components/sre_agent/config")
    client.put("/api/v1/admin/components/sre_agent/config", json={"settings": {}})
    paths = {c.url.path for c in fake.calls}
    assert paths <= {"/admin/config/status", "/admin/config/schema", "/admin/config"}


def test_client_names_follow_allowlist_order():
    client = ComponentAdminClient(_component_config())
    assert client.names() == list(COMPONENT_ADMIN_NAMES)
    assert not client.has("evil")


def test_server_registers_router():
    text = (ROOT / "src/api/server.py").read_text(encoding="utf-8")
    assert 'application.include_router(admin_components.router, prefix="/api/v1"' in text


# --- UI ---

_JS = ROOT / "src/static/js/admin-components.js"
_HTML = ROOT / "src/static/admin/dashboard.html"


def test_dashboard_loads_component_script_and_target_bar():
    html = _HTML.read_text(encoding="utf-8")
    assert '<script src="/static/js/admin-components.js' in html
    assert 'id="settingsTargetBar"' in html and 'id="componentCard"' in html
    # 기본은 숨김 — 컴포넌트 0개면 현행 화면과 같다
    at = html.index('id="settingsTargetBar"')
    bar = html[at - 120: at + 60]
    assert "hidden" in bar


def test_scope_note_narrowed():
    html = _HTML.read_text(encoding="utf-8")
    end = html.index('settings-help" id="settingsHelp"')
    note = html[html.index("이 화면 범위 밖 설정"): end]
    assert "설정 대상" in note
    assert "apm_gateway/config/" in note and "sre_agent/config/playbooks/" in note
    assert "config.toml" in note and "[[sources]]" in note and ".encenv" in note
    assert "<code>sre_agent/.env</code>" not in note  # 이제 대상 선택에서 관리한다


def test_component_script_conventions():
    js = _JS.read_text(encoding="utf-8")
    assert "innerHTML" not in js  # 컴포넌트 문자열은 textContent로만
    assert "status === 401" in js and "status === 403" in js
    assert "/api/v1/admin/components" in js
    admin = (ROOT / "src/static/js/admin.js").read_text(encoding="utf-8")
    assert "reloadSettingsBtn" in admin and "AdminSettings" in admin
    # 컴포넌트 항목에는 consumed가 없다 — 미정의를 미소비로 읽으면 목록 전체가 숨는다
    assert "!item.consumed && !showUnconsumed" not in admin
    assert "item.consumed === false && !showUnconsumed" in admin


def _node(body: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 순수 함수 실행 테스트 생략")
    script = "\n".join([
        f"var AC = require({json.dumps(str(_JS))});",
        "var out = null;",
        body,
        "process.stdout.write(JSON.stringify(out));",
    ])
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                          timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_js_restart_banner_text():
    out = _node('out = AC.restartBannerText(["A", "B"], "cd sre_agent && run");')
    assert out == "재기동 대기: 2키 — cd sre_agent && run로 재기동하세요"
    assert _node('out = AC.restartBannerText([], "x");') == ""
    assert "재기동하세요" in _node('out = AC.restartBannerText(["A"], "");')


def test_js_fail_and_ignored_texts():
    out = _node(
        'out = [AC.failText(401, {}), AC.failText(403, {}), '
        'AC.failText(502, {reason: "timeout", message: "늦음"}), '
        'AC.ignoredReasonText("override_os"), AC.ignoredReasonText("zzz")];'
    )
    assert "로그인" in out[0] and "관리자 권한" in out[1]
    assert "늦음" in out[2]
    assert "OS" in out[3] and out[4] == "zzz"


def test_js_card_rows_are_plain_strings():
    schema = _schema()
    schema["writable"] = False
    schema["write_block_reason"] = "<img src=x onerror=alert(1)>"
    schema["restart_pending_keys"] = ["MAX_STEPS"]
    out = _node(f"out = AC.cardRows({json.dumps(schema, ensure_ascii=False)});")
    labels = [row[0] for row in out]
    assert {"버전", "기동 시각", "읽는 파일", "쓰기"} <= set(labels)
    joined = json.dumps(out, ensure_ascii=False)
    assert "<img src=x onerror=alert(1)>" in joined  # 원문 그대로(렌더는 textContent)
    assert "/opt/sre_agent/.env" in joined


def test_js_error_lines_include_global_errors():
    body = {"errors": [{"key": "X", "message": "bad"}, {"key": "", "message": "로더 실패"}]}
    out = _node(f"out = AC.errorLines({json.dumps(body, ensure_ascii=False)});")
    assert out == ["X: bad", "전체: 로더 실패"]
    banner = _node(f"out = AC.failText(400, {json.dumps(body, ensure_ascii=False)});")
    assert "오류 2건" in banner


def test_js_saved_lines_warn_on_audit_failure():
    data = {"restart_pending_keys": ["A"], "ignored_keys": [{"key": "K", "reason": "secret"}],
            "audit_logged": False, "proxy_audit_logged": False}
    out = _node(f"out = AC.savedLines({json.dumps(data)}, \"restart-cmd\");")
    assert out[0] == "재기동 대기: 1키 — restart-cmd로 재기동하세요"
    assert any("K — 시크릿" in line for line in out)
    assert any("컴포넌트 감사 기록에 실패" in line for line in out)
    assert any("본체 감사 기록 불가" in line for line in out)
    ok = _node('out = AC.savedLines({restart_pending_keys: [], audit_logged: true}, "x");')
    assert ok == []


def _admin_js_function(name: str) -> str:
    """admin.js에서 함수 선언 하나를 잘라 낸다(중괄호 짝 맞춤)."""
    text = (ROOT / "src/static/js/admin.js").read_text(encoding="utf-8")
    start = text.index(f"    function {name}(")
    depth, i = 0, text.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
        if depth == 0:
            return text[start:i]


def test_admin_js_toggle_renders_string_and_bool_true_as_on():
    """계약 표기("true" 문자열)와 bool true 모두 켜짐으로 그린다 · "false"·None은 꺼짐."""
    harness = """
var document = {createElement: function () { return {
    classList: {_s: {}, add: function (c) { this._s[c] = 1; }, toggle: function () {},
                contains: function (c) { return !!this._s[c]; }},
    setAttribute: function () {}, appendChild: function () {}, addEventListener: function () {},
    set className(v) { this._cls = v; }, get className() { return this._cls; }}; }};
var edits = {};
function currentValue(item) { return item.file_value === undefined ? null : item.file_value; }
function setValue() {}
"""
    fn = _admin_js_function("buildToggle")
    cases = [
        {"default": "true", "file_value": None},
        {"default": True, "file_value": None},
        {"default": "false", "file_value": "true"},
        {"default": "false", "file_value": None},
        {"default": None, "file_value": None},
    ]
    out = _node(harness + fn + f"""
out = {json.dumps(cases)}.map(function (item) {{
    item.env_key = "K";
    return buildToggle(item).className.indexOf(" on") !== -1;
}});""")
    assert out == [True, True, True, False, False]
