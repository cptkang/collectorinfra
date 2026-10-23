"""설정 관리 API(`/admin/config`) 검증 (plans/191 W1 · D-345 · SPEC-component-config-admin §2·§3).

실제 `.env`·`.encenv` 누수를 막기 위해 모든 테스트는 tmp_path를 CWD로 쓰고 설정 필드를 명시한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

import pytest
from sre_agent.application.investigation_jobs import JobStore
from sre_agent.interface import config_admin_adapter
from sre_agent.interface.config_admin import (
    MASK,
    MAX_BODY_BYTES,
    AdminPathRouter,
    _candidate_matches,
    build_admin_app,
    parse_env_lines,
    split_env_lines,
)
from sre_agent.interface.config_admin_adapter import (
    build_adapter,
    build_field_specs,
    readonly_keys,
)
from sre_agent.interface.mcp_service import (
    StaticBearerAuthMiddleware,
    build_asgi_app,
    create_service,
)
from sre_agent.settings import AgentSettings
from starlette.applications import Starlette
from starlette.testclient import TestClient

import sre_agent

_TOP = Path(__file__).resolve().parents[1]
ADMIN = "admin-tok"
SERVICE = "service-tok"
AUTH = {"Authorization": f"Bearer {ADMIN}"}
ENV_TEXT = (
    "# 머리 주석\n"
    "MODEL=anthropic/claude-sonnet-5\n"
    "\n"
    "# 스텝 상한\n"
    "MAX_STEPS=40\n"
    "# OVERRIDE_MAX_CONTENT_SIZE=30000\n"
    "API_KEY=supersecret-value\n"
)
# .env.example에 있지만 AgentSettings 필드가 아닌 키(holmes·litellm이 OS env로 읽는다).
_OS_KEYS = frozenset({"INVESTIGATION_MAX_CONCURRENT"})
NON_FIELD_EXAMPLE_KEYS = frozenset({"ANTHROPIC_API_KEY", "OPENAI_API_KEY"})


def _settings(**kwargs) -> AgentSettings:
    base = {"model": "anthropic/claude-sonnet-5", "max_steps": 40, "service_bearer_token": None}
    base.update(kwargs)
    return AgentSettings(_env_file=None, **base)


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    """패키지 루트 = CWD = tmp_path. `.env`를 0640으로 둔다."""
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text(ENV_TEXT, encoding="utf-8")
    os.chmod(env, 0o640)
    return tmp_path


def _client(
    root: Path,
    cwd: Path | None = None,
    settings: AgentSettings | None = None,
    env_keys: frozenset[str] = frozenset(),
):
    adapter = build_adapter(
        settings or _settings(),
        cwd=cwd or root,
        package_root=root,
        audit_path=root / "audit.jsonl",
        process_env_keys=env_keys,
    )
    return TestClient(build_admin_app(adapter, ADMIN))


def _put(client: TestClient, body: dict, **headers):
    return client.put("/admin/config", json=body, headers={**AUTH, **headers})


def _items(schema: dict) -> dict[str, dict]:
    return {i["env_key"]: i for g in schema["groups"] for i in g["settings"]}


def _audit_lines(root: Path) -> list[dict]:
    path = root / "audit.jsonl"
    return (
        [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        if path.exists()
        else []
    )


def _mcp(tmp_path):
    settings = _settings()
    return create_service(
        settings, job_store=JobStore(settings, audit_path=tmp_path / "jobs.jsonl")
    )


# ── 배선 · 인증 ────────────────────────────────────────────────


def test_no_admin_token_is_unchanged(tmp_path, monkeypatch):
    """관리 토큰이 없으면 반환 객체가 종전과 같고 관리 경로는 기존 앱이 처리한다(404/401)."""
    monkeypatch.chdir(tmp_path)
    app = build_asgi_app(_mcp(tmp_path), None, settings=_settings())
    assert isinstance(app, Starlette)
    assert StaticBearerAuthMiddleware in [m.cls for m in app.user_middleware]
    assert TestClient(app).get("/admin/config/schema", headers=AUTH).status_code == 404
    guarded = build_asgi_app(_mcp(tmp_path), SERVICE, settings=_settings())
    assert TestClient(guarded).get("/admin/config/schema", headers=AUTH).status_code == 401


def test_settings_does_not_read_body_admin_token(monkeypatch):
    """본체 `ADMIN_TOKEN`은 관리 토큰으로 읽히지 않는다(루트 CWD 기동 충돌 방지)."""
    monkeypatch.setenv("ADMIN_TOKEN", "body-admin")
    monkeypatch.delenv("SRE_AGENT_ADMIN_TOKEN", raising=False)
    assert AgentSettings(_env_file=None).sre_agent_admin_token is None
    monkeypatch.setenv("SRE_AGENT_ADMIN_TOKEN", "x")
    assert AgentSettings(_env_file=None).sre_agent_admin_token.get_secret_value() == "x"


def test_admin_routes_require_admin_token_only(tmp_path, monkeypatch):
    """래퍼 뒤: 관리 경로는 관리 토큰만, MCP 경로는 서비스 토큰 규칙 그대로."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_admin_adapter, "AUDIT_PATH", tmp_path / "audit.jsonl")
    settings = _settings(service_bearer_token=SERVICE, sre_agent_admin_token=ADMIN)
    app = build_asgi_app(_mcp(tmp_path), SERVICE, settings=settings)
    assert isinstance(app, AdminPathRouter)
    client = TestClient(app)
    assert client.get("/admin/config/schema").status_code == 401
    assert client.get("/admin/config/schema", headers={"Authorization": "Bearer x"}).json() == {
        "error": "unauthorized"
    }
    assert (
        client.get(
            "/admin/config/status", headers={"Authorization": f"Bearer {SERVICE}"}
        ).status_code
        == 401
    )
    resp = client.get("/admin/config/schema", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["component"]["name"] == "sre_agent"
    # MCP 메시지 경로 — 서비스 토큰 없으면 401, 관리 토큰으로도 401, 서비스 토큰이면 미들웨어 통과.
    assert client.post("/messages/", json={}).status_code == 401
    assert client.post("/messages/", json={}, headers=AUTH).status_code == 401
    passed = client.post("/messages/", json={}, headers={"Authorization": f"Bearer {SERVICE}"})
    assert passed.status_code != 401


def test_admin_token_equal_to_service_token_not_registered(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    settings = _settings(service_bearer_token=SERVICE, sre_agent_admin_token=SERVICE)
    with caplog.at_level(logging.WARNING):
        app = build_asgi_app(_mcp(tmp_path), SERVICE, settings=settings)
    assert isinstance(app, Starlette)
    assert any("미등록" in r.message for r in caplog.records)


def test_router_dispatch_keeps_scope_and_non_http():
    seen: list[tuple[str, dict]] = []

    def recorder(name):
        async def app(scope, receive, send):
            seen.append((name, scope))

        return app

    router = AdminPathRouter(recorder("base"), recorder("admin"))
    scopes = [
        {"type": "lifespan"},
        {"type": "http", "path": "/admin/configX"},
        {"type": "http", "path": "/admin/config"},
        {"type": "http", "path": "/admin/config/schema"},
        {"type": "websocket", "path": "/admin/config/schema"},
    ]
    for scope in scopes:
        asyncio.run(router(scope, None, None))
    assert [name for name, _ in seen] == ["base", "base", "admin", "admin", "base"]
    assert all(original is got for original, (_, got) in zip(scopes, seen))


# ── 스키마 ─────────────────────────────────────────────────────


def test_schema_masks_secrets_and_marks_override(pkg, monkeypatch):
    (pkg / ".encenv").write_text("INVESTIGATION_LLM_MODEL=gemini/x\n", encoding="utf-8")
    settings = _settings(api_key="supersecret-value", sre_agent_admin_token=ADMIN)
    resp = _client(pkg, settings=settings, env_keys=_OS_KEYS).get(
        "/admin/config/schema", headers=AUTH
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "supersecret-value" not in resp.text and ADMIN not in resp.text
    items = _items(body)
    secret = items["API_KEY"]
    assert secret["is_secret"] and secret["is_set"] and secret["file_value"] is None
    assert secret["effective_value"] is None and secret["writable"] is False
    assert items["SRE_AGENT_ADMIN_TOKEN"]["is_secret"]
    assert items["SERVICE_HOST"]["readonly_reason"] == "readonly"
    assert items["INVESTIGATION_LLM_MODEL"]["override"] == "encenv"
    assert items["INVESTIGATION_MAX_CONCURRENT"]["override"] == "os"
    assert items["MAX_STEPS"]["file_value"] == "40" and items["MAX_STEPS"]["writable"]
    assert items["MAX_STEPS"]["apply_mode"] == "restart" and items["MAX_STEPS"]["requires_restart"]
    assert items["SRE_DEEP_DIVE_MODE"]["type"] == "enum"
    assert body["writable"] is True and body["sources"][0]["role"] == "write_target"


# ── 저장 ───────────────────────────────────────────────────────


def test_ignored_keys_reasons(pkg, monkeypatch):
    (pkg / ".encenv").write_text("INVESTIGATION_LLM_MODEL=gemini/x\n", encoding="utf-8")
    resp = _put(
        _client(pkg, env_keys=_OS_KEYS),
        {
            "settings": {
                "API_KEY": "new",
                "MODEL": MASK,
                "SERVICE_HOST": "0.0.0.0",
                "INVESTIGATION_LLM_MODEL": "gemini/y",
                "INVESTIGATION_MAX_CONCURRENT": "5",
            }
        },
    )
    assert resp.status_code == 200
    reasons = {x["key"]: x["reason"] for x in resp.json()["ignored_keys"]}
    assert reasons == {
        "API_KEY": "secret",
        "MODEL": "masked",
        "SERVICE_HOST": "readonly",
        "INVESTIGATION_LLM_MODEL": "override_encenv",
        "INVESTIGATION_MAX_CONCURRENT": "override_os",
    }
    assert resp.json()["saved_keys"] == []
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT


@pytest.mark.parametrize(
    "settings",
    [
        {"NOT_A_FIELD": "1"},
        {"MODEL": "a\nb"},
        {"MODEL": "a#b"},
        {"MODEL": "a\x00b"},
        {"MODEL": " a"},
        {"MODEL": "'a'"},
        {"MAX_STEPS": "many"},
        {"SRE_DEEP_DIVE_MODE": "always"},
        {"SEVERITY_JUDGE_ENABLED": "maybe"},
    ],
)
def test_rejected_inputs_leave_file_unchanged(pkg, settings):
    resp = _put(_client(pkg), {"settings": settings})
    assert resp.status_code == 400
    assert resp.json()["errors"]
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT
    assert not (pkg / ".env.bak").exists()
    assert _audit_lines(pkg)[-1]["result"] == "rejected"


def test_duplicate_active_key_rejected(pkg):
    (pkg / ".env").write_text(ENV_TEXT + "MAX_STEPS=41\n", encoding="utf-8")
    resp = _put(_client(pkg), {"settings": {"MAX_STEPS": "50"}})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["key"] == "MAX_STEPS"


def test_dry_run_loader_failure_keeps_file(pkg):
    """타입 검증은 통과하지만 자기 로더(gt=0)가 거부하는 값."""
    resp = _put(_client(pkg), {"settings": {"OVERRIDE_MAX_CONTENT_SIZE": "0"}})
    assert resp.status_code == 400
    assert "override_max_content_size" in resp.json()["errors"][0]["message"]
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT
    assert not (pkg / ".env.bak").exists()


def test_save_preserves_comments_order_mode_and_backup(pkg):
    client = _client(pkg)
    resp = _put(
        client,
        {"settings": {"MAX_STEPS": 41, "SEVERITY_JUDGE_ENABLED": True}},
        **{"X-Request-ID": "req-1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved_keys"] == ["MAX_STEPS", "SEVERITY_JUDGE_ENABLED"]
    assert body["restart_required"] is True and body["restart_pending_keys"] == [
        "MAX_STEPS",
        "SEVERITY_JUDGE_ENABLED",
    ]
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT.replace(
        "MAX_STEPS=40", "MAX_STEPS=41"
    ) + "SEVERITY_JUDGE_ENABLED=true\n"
    assert (pkg / ".env.bak").read_text(encoding="utf-8") == ENV_TEXT
    assert oct((pkg / ".env").stat().st_mode & 0o777) == oct(0o640)
    line = _audit_lines(pkg)[-1]
    assert line["result"] == "saved" and line["request_id"] == "req-1"
    assert line["component"] == "sre_agent"
    assert {"key": "MAX_STEPS", "old": "40", "new": "41"} in line["changes"]


def test_reset_keys_removes_active_line_only(pkg):
    resp = _put(_client(pkg), {"reset_keys": ["MAX_STEPS"]})
    assert resp.status_code == 200 and resp.json()["saved_keys"] == ["MAX_STEPS"]
    text = (pkg / ".env").read_text(encoding="utf-8")
    assert "MAX_STEPS=" not in text and "# 스텝 상한" in text
    assert "# OVERRIDE_MAX_CONTENT_SIZE=30000" in text


def test_dry_run_true_keeps_file(pkg):
    resp = _put(_client(pkg), {"settings": {"MAX_STEPS": "41"}, "dry_run": True})
    assert resp.status_code == 200 and resp.json()["dry_run"] is True
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT
    assert _audit_lines(pkg)[-1]["result"] == "dry_run"


def test_restart_pending_compares_values(pkg):
    client = _client(pkg)
    assert _put(client, {"settings": {"MAX_STEPS": "40"}}).json()["restart_pending_keys"] == []
    assert _put(client, {"settings": {"MAX_STEPS": "41"}}).json()["restart_pending_keys"] == [
        "MAX_STEPS"
    ]
    assert _put(client, {"settings": {"MAX_STEPS": "40"}}).json()["restart_pending_keys"] == []
    status = client.get("/admin/config/status", headers=AUTH).json()
    assert status["restart_pending_keys"] == [] and status["writable"] is True


def test_cwd_not_package_root_blocks_writes(pkg, tmp_path_factory):
    other = tmp_path_factory.mktemp("root")
    client = _client(other, cwd=pkg)
    resp = _put(client, {"settings": {"MAX_STEPS": "41"}})
    assert resp.status_code == 409 and resp.json()["error"] == "write_blocked"
    assert resp.json()["reason"]
    schema = client.get("/admin/config/schema", headers=AUTH).json()
    assert schema["writable"] is False
    assert _items(schema)["MAX_STEPS"]["writable"] is False
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT


def test_body_size_limit(pkg):
    resp = _client(pkg).put(
        "/admin/config", content=b"{" + b" " * MAX_BODY_BYTES + b"}", headers=AUTH
    )
    assert resp.status_code == 413
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT


def test_audit_has_no_secret(pkg):
    _put(_client(pkg), {"settings": {"API_KEY": "leak-me-please", "MAX_STEPS": "41"}})
    raw = (pkg / "audit.jsonl").read_text(encoding="utf-8")
    assert "leak-me-please" not in raw and "supersecret-value" not in raw
    assert "API_KEY" not in raw


# ── 필드 표 커버리지 ───────────────────────────────────────────


def test_env_example_coverage():
    """필드 표 ⊇ .env.example 키(주석 `# KEY=` 포함) − 시크릿."""
    specs = {s.env_key: s for s in build_field_specs()}
    example = (_TOP / ".env.example").read_text(encoding="utf-8")
    keys = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", example, flags=re.MULTILINE))
    assert "SRE_AGENT_ADMIN_TOKEN" in keys
    missing = {k for k in keys - NON_FIELD_EXAMPLE_KEYS if k not in specs}
    assert missing == set()
    secret_str = {
        "API_KEY",
        "POLESTAR_MCP_TOKEN",
        "APM_MCP_TOKEN",
        "GEMINI_API_KEY",
        "SERVICE_BEARER_TOKEN",
        "SRE_AGENT_ADMIN_TOKEN",
    }
    assert {k for k, s in specs.items() if s.is_secret} == secret_str
    assert specs["OVERRIDE_MAX_OUTPUT_TOKEN"].type == "int"


# ── 교정 라운드(verifier·security-auditor 지적) ─────────────────


@pytest.mark.parametrize(
    "bad", ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " ", "\t", "$", "`"]
)
def test_line_separator_and_expansion_chars_rejected(pkg, bad):
    """H-1·M-1 — splitlines 줄 경계 문자·제어 문자·변수 확장 문자로 다른 키를 심지 못한다."""
    resp = _put(_client(pkg), {"settings": {"MODEL": f"a{bad}SERVICE_HOST=0.0.0.0"}})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["key"] == "MODEL" and resp.json()["errors"][0]["message"]
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT


def test_parser_only_splits_on_cr_lf():
    lines = split_env_lines("A=1\x0bB=2 C=3\r\nD=4\rE=5\n")
    assert lines == ["A=1\x0bB=2 C=3", "D=4", "E=5"]
    assert set(parse_env_lines(lines)) == {"A", "D", "E"}


def test_candidate_reparse_guard():
    before = parse_env_lines(["A=1", "B=2"])
    assert _candidate_matches(before, "A=9\nB=2\n", {"A": "9"}, set())
    assert not _candidate_matches(before, "A=9\nB=2\nC=3\n", {"A": "9"}, set())
    assert not _candidate_matches(before, "A=9\nB=7\n", {"A": "9"}, set())
    assert _candidate_matches(before, "A=1\n", {}, {"B"})


def test_schema_default_and_effective_are_env_strings(pkg):
    items = _items(_client(pkg).get("/admin/config/schema", headers=AUTH).json())
    fix = items["SYSTEM_MESSAGE_POSITION_FIX"]
    assert fix["default"] == "true" and fix["effective_value"] == "true"
    assert items["MAX_STEPS"]["default"] == "40" and items["MAX_STEPS"]["effective_value"] == "40"
    assert items["SEVERITY_JUDGE_ENABLED"]["default"] == "false"
    assert items["INVESTIGATION_HOURLY_BUDGET"]["default"] is None
    # 시크릿은 effective가 truthy일 때만 is_set (파일에 줄이 있어도 기동 값이 없으면 False).
    assert items["API_KEY"]["is_set"] is False


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"[1, 2]",
        b'{"settings": {"MAX_STEPS": "41"}, "dry_run": "yes"}',
        b'{"settings": ["MAX_STEPS"]}',
        b'{"reset_keys": "MAX_STEPS"}',
    ],
)
def test_malformed_body_is_400(pkg, content):
    resp = _client(pkg).put("/admin/config", content=content, headers=AUTH)
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["message"]
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT


def test_bak_symlink_is_refused(pkg, tmp_path_factory):
    victim = tmp_path_factory.mktemp("victim") / "target.txt"
    victim.write_text("keep", encoding="utf-8")
    (pkg / ".env.bak").symlink_to(victim)
    resp = _put(_client(pkg), {"settings": {"MAX_STEPS": "41"}})
    assert resp.status_code == 500 and resp.json()["errors"][0]["message"]
    assert victim.read_text(encoding="utf-8") == "keep"
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT


def test_bak_and_audit_created_0600(pkg):
    assert _put(_client(pkg), {"settings": {"MAX_STEPS": "41"}}).json()["audit_logged"] is True
    assert oct((pkg / ".env.bak").stat().st_mode & 0o777) == oct(0o600)
    assert oct((pkg / "audit.jsonl").stat().st_mode & 0o777) == oct(0o600)


def test_audit_failure_reports_flag_and_keeps_save(pkg):
    adapter = build_adapter(
        _settings(), cwd=pkg, package_root=pkg, audit_path=pkg, process_env_keys=frozenset()
    )
    resp = _put(TestClient(build_admin_app(adapter, ADMIN)), {"settings": {"MAX_STEPS": "41"}})
    assert resp.status_code == 200 and resp.json()["audit_logged"] is False
    assert "MAX_STEPS=41" in (pkg / ".env").read_text(encoding="utf-8")


def test_audit_masks_userinfo_and_truncates(pkg):
    client = _client(pkg)
    _put(client, {"settings": {"MODEL": "https://user:pw@host/x"}})
    assert _audit_lines(pkg)[-1]["changes"][0]["new"] == "https://***@host/x"
    _put(client, {"settings": {"MODEL": "m" * 300}})
    assert len(_audit_lines(pkg)[-1]["changes"][0]["new"]) == 200


def test_override_os_uses_import_snapshot(pkg, monkeypatch):
    """C — import 뒤에 들어온 env(litellm load_dotenv 등)는 override_os가 아니다."""
    monkeypatch.setenv("MAX_STEPS", "40")
    adapter = build_adapter(
        _settings(),
        cwd=pkg,
        package_root=pkg,
        audit_path=pkg / "a.jsonl",
        process_env_keys=frozenset(),
    )
    assert adapter.override_for("MAX_STEPS") is None
    monkeypatch.setattr(sre_agent, "PROCESS_ENV_KEYS_AT_IMPORT", frozenset({"MAX_STEPS"}))
    adapter = build_adapter(_settings(), cwd=pkg, package_root=pkg, audit_path=pkg / "a.jsonl")
    assert adapter.override_for("MAX_STEPS") == "os"


def test_dry_run_ignores_env_injected_after_import(pkg, monkeypatch):
    """스냅샷 밖 os.environ 값이 후보 파일을 이기면 dry-run이 옛 값을 검증한다 — 걸러야 한다."""
    monkeypatch.setenv("OVERRIDE_MAX_CONTENT_SIZE", "100")
    resp = _put(_client(pkg), {"settings": {"OVERRIDE_MAX_CONTENT_SIZE": "0"}})
    assert resp.status_code == 400
    assert (pkg / ".env").read_text(encoding="utf-8") == ENV_TEXT


def test_readonly_keys_cover_boundary_fields():
    assert readonly_keys(build_field_specs()) == {
        "SERVICE_HOST",
        "POLESTAR_MCP_URL",
        "APM_MCP_URL",
        "API_BASE",
        "BASH_WRITE_GUARD_ENABLED",
        "INVESTIGATION_AUDIT_PATH",
        "SRE_PLAYBOOK_DIR",
        "INVESTIGATION_GUIDANCE_EXTRA",
    }


def test_blank_admin_token_is_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = build_asgi_app(_mcp(tmp_path), None, settings=_settings(sre_agent_admin_token="   "))
    assert isinstance(app, Starlette)
