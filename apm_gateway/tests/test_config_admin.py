"""설정 관리 API(`/admin/config`) 검증 (plans/191 W3 · D-345 · D-274 · SPEC-component-config-admin).

쓰기 대상·감사 경로는 tmp_path로 주입하고, 필드 표 키는 프로세스 환경에서 지운 뒤 시작한다
(개발 셸·실 `.env` 누수 차단 — 원복은 monkeypatch). 실 제니퍼 호출 없음.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pytest
from apm_gateway.interface import config_admin_adapter as adapter_mod
from apm_gateway.interface.config_admin import AdminPathRouter, build_admin_app
from apm_gateway.interface.config_admin_adapter import build_adapter, build_fields
from apm_gateway.interface.server import build_asgi_app
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from apm_gateway import config as config_mod
from apm_gateway.config import describe, load_config, load_dotenv

GATEWAY = Path(__file__).resolve().parents[1]
ADMIN = "adm-TOKEN-71c2"
BEARER = "brr-TOKEN-55d0"
JTOKEN = "jen-TOKEN-0b9e"
BANK_TOKEN = "bank-TOKEN-3a11"
REDIS_PW = "redis-PW-9e41"
AUTH = {"Authorization": f"Bearer {ADMIN}"}
SECRETS = (ADMIN, BEARER, JTOKEN, BANK_TOKEN, REDIS_PW)
ENV_TEXT = (
    "# 머리 주석\n"
    "JENNIFER_API_URL=http://jen.test\n"
    f"JENNIFER_API_TOKEN={JTOKEN}\n"
    "APM_INLINE_ROWS=500\n"
    f"REDIS_PASSWORD={REDIS_PW}\n"
)
_DOCUMENTED = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]{2,})\s*=", re.MULTILINE)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """필드 표·소스 키를 프로세스 환경에서 지우고 주입 키 기록을 비운다(원복은 monkeypatch)."""
    keys = {key for key, *_ in adapter_mod._TABLE}
    keys |= {f"JENNIFER_BANK_{s}" for s in config_mod.SOURCE_KEY_SUFFIXES}
    for key in keys | {"APM_TEST_ADMIN_X"}:
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)
    monkeypatch.setattr(config_mod, "DOTENV_INJECTED_KEYS", set())


def _cfg(**extra: str):
    env = {
        "JENNIFER_API_URL": "http://jen.test",
        "JENNIFER_API_TOKEN": JTOKEN,
        "APM_INLINE_ROWS": "500",
        "REDIS_PASSWORD": REDIS_PW,
        **extra,
    }
    return load_config(env, dotenv=None)


@pytest.fixture
def target(tmp_path) -> Path:
    path = tmp_path / ".env"
    path.write_text(ENV_TEXT, encoding="utf-8")
    path.chmod(0o640)
    return path


def _client(target: Path, cfg=None) -> TestClient:
    adapter = build_adapter(
        cfg or _cfg(), write_target=target, audit_path=target.parent / "audit.jsonl"
    )
    return TestClient(build_admin_app(adapter, ADMIN))


def _items(body: dict) -> dict[str, dict]:
    return {item["env_key"]: item for g in body["groups"] for item in g["settings"]}


# ── 배선 (기본 off = 현행 동일 · 관리 경로 인증 분리) ──────────────


def test_no_admin_token_is_unchanged():
    app = build_asgi_app(FastMCP("t"), None, _cfg())
    assert not isinstance(app, AdminPathRouter)
    assert TestClient(app).get("/admin/config/schema", headers=AUTH).status_code == 404
    guarded = build_asgi_app(FastMCP("t"), {"chat": BEARER}, _cfg())
    assert not isinstance(guarded, AdminPathRouter)
    assert TestClient(guarded).get("/admin/config/schema", headers=AUTH).status_code == 401


def test_admin_route_uses_admin_token_and_mcp_keeps_bearer(target, monkeypatch):
    monkeypatch.setattr(adapter_mod, "PACKAGE_ROOT", target.parent)
    monkeypatch.setattr(adapter_mod, "AUDIT_PATH", target.parent / "audit.jsonl")
    cfg = _cfg(APM_GATEWAY_ADMIN_TOKEN=ADMIN)
    app = build_asgi_app(FastMCP("t"), {"chat": BEARER}, cfg)
    assert isinstance(app, AdminPathRouter)
    client = TestClient(app)
    ok = client.get("/admin/config/schema", headers=AUTH)  # Bearer 주체 토큰 불필요
    assert ok.status_code == 200 and ok.json()["component"]["name"] == "apm_gateway"
    for headers in ({}, {"Authorization": f"Bearer {BEARER}"}):
        bad = client.get("/admin/config/status", headers=headers)
        assert bad.status_code == 401 and bad.json() == {"error": "unauthorized"}
    # MCP 경로는 기존 주체별 Bearer 규칙 그대로 — 관리 토큰으로는 열리지 않는다
    assert client.get("/sse").status_code == 401
    assert client.get("/sse", headers=AUTH).status_code == 401


def test_admin_token_equal_to_any_bearer_not_registered(caplog):
    cfg = _cfg(APM_GATEWAY_ADMIN_TOKEN=BEARER)
    with caplog.at_level(logging.WARNING):
        app = build_asgi_app(FastMCP("t"), {"chat": "other-tok", "alarm": BEARER}, cfg)
    assert not isinstance(app, AdminPathRouter)
    assert "설정 관리 API 미등록" in caplog.text and BEARER not in caplog.text


def test_describe_admin_flag_has_no_value():
    text = str(describe(_cfg(APM_GATEWAY_ADMIN_TOKEN=ADMIN)))
    assert "'admin_api': True" in text and ADMIN not in text
    assert describe(_cfg())["admin_api"] is False


# ── 조회 (시크릿 is_set만 · override · 읽기 전용) ─────────────────


def test_schema_has_no_secret_values(target):
    cfg = _cfg(APM_GATEWAY_BEARER_TOKEN=BEARER, APM_GATEWAY_ADMIN_TOKEN=ADMIN)
    response = _client(target, cfg).get("/admin/config/schema", headers=AUTH)
    assert response.status_code == 200
    for secret in SECRETS:
        assert secret not in response.text
    items = _items(response.json())
    token = items["JENNIFER_API_TOKEN"]
    assert token["is_secret"] and token["is_set"] and token["effective_value"] is None
    assert token["file_value"] is None and token["writable"] is False
    assert items["APM_GATEWAY_BEARER_TOKENS"]["is_set"] is False
    assert items["APM_GATEWAY_ADMIN_TOKEN"]["is_secret"] is True
    assert items["APM_INLINE_ROWS"]["file_value"] == "500"
    assert items["APM_INLINE_ROWS"]["effective_value"] == "500"  # .env 표기 문자열
    assert response.json()["writable"] is True


def test_os_origin_is_override_but_dotenv_injected_is_not(target, monkeypatch):
    monkeypatch.setenv("APM_TIMEZONE", "UTC")  # 셸·systemd 원천
    loaded = target.parent / "loaded.env"
    loaded.write_text("APM_INLINE_ROWS=500\n", encoding="utf-8")
    load_dotenv(loaded)  # 파일 주입 — clean_env가 지운 키라 새로 들어간다(원복은 delenv 기록)
    assert "APM_INLINE_ROWS" in config_mod.DOTENV_INJECTED_KEYS
    client = _client(target)
    items = _items(client.get("/admin/config/schema", headers=AUTH).json())
    assert items["APM_TIMEZONE"]["override"] == "os"
    assert items["APM_INLINE_ROWS"]["override"] is None
    response = client.put(
        "/admin/config", headers=AUTH, json={"settings": {"APM_TIMEZONE": "Asia/Tokyo"}}
    )
    assert response.json()["ignored_keys"] == [{"key": "APM_TIMEZONE", "reason": "override_os"}]
    assert target.read_text(encoding="utf-8") == ENV_TEXT


def test_readonly_and_secret_keys_ignored(target):
    response = _client(target).put(
        "/admin/config",
        headers=AUTH,
        json={
            "settings": {
                "JENNIFER_SOURCES": '["bank"]',
                "APM_GATEWAY_HOST": "0.0.0.0",
                "JENNIFER_API_TOKEN": "new-token",
                "APM_GATEWAY_ADMIN_TOKEN": "new-admin",
            }
        },
    )
    reasons = {i["key"]: i["reason"] for i in response.json()["ignored_keys"]}
    assert reasons == {
        "JENNIFER_SOURCES": "readonly",
        "APM_GATEWAY_HOST": "readonly",
        "JENNIFER_API_TOKEN": "secret",
        "APM_GATEWAY_ADMIN_TOKEN": "secret",
    }
    assert response.json()["saved_keys"] == []
    assert target.read_text(encoding="utf-8") == ENV_TEXT


def test_source_keys_follow_declared_sources(tmp_path):
    path = tmp_path / ".env"
    text = (
        'JENNIFER_SOURCES=["bank"]\n'
        "JENNIFER_BANK_API_URL=http://bank.test\n"
        f"JENNIFER_BANK_API_TOKEN={BANK_TOKEN}\n"
    )
    path.write_text(text, encoding="utf-8")
    cfg = load_config(
        {
            "JENNIFER_SOURCES": '["bank"]',
            "JENNIFER_BANK_API_URL": "http://bank.test",
            "JENNIFER_BANK_API_TOKEN": BANK_TOKEN,
        },
        dotenv=None,
    )
    client = _client(path, cfg)
    schema = client.get("/admin/config/schema", headers=AUTH)
    assert BANK_TOKEN not in schema.text
    items = _items(schema.json())
    assert (
        items["JENNIFER_BANK_API_TOKEN"]["is_secret"] and items["JENNIFER_BANK_API_TOKEN"]["is_set"]
    )
    assert items["JENNIFER_SOURCES"]["effective_value"] == '["bank"]'
    response = client.put(
        "/admin/config",
        headers=AUTH,
        json={
            "settings": {
                "JENNIFER_BANK_API_TIMEOUT_SECONDS": "3",
                "JENNIFER_NEW_API_URL": "http://new.test",
            }
        },
    )
    assert response.status_code == 400
    assert response.json()["errors"] == [
        {"key": "JENNIFER_NEW_API_URL", "message": "등록되지 않은 키"}
    ]
    assert path.read_text(encoding="utf-8") == text
    saved = client.put(
        "/admin/config", headers=AUTH, json={"settings": {"JENNIFER_BANK_API_TIMEOUT_SECONDS": "3"}}
    )
    assert saved.status_code == 200 and saved.json()["saved_keys"] == [
        "JENNIFER_BANK_API_TIMEOUT_SECONDS"
    ]
    assert "JENNIFER_BANK_API_TIMEOUT_SECONDS=3" in path.read_text(encoding="utf-8")


# ── 저장 (검증·dry-run·재기동 대기·감사) ────────────────────────


def test_type_error_keeps_file(target):
    response = _client(target).put(
        "/admin/config", headers=AUTH, json={"settings": {"APM_INLINE_ROWS": "abc"}}
    )
    assert response.status_code == 400 and response.json()["errors"][0]["key"] == "APM_INLINE_ROWS"
    assert target.read_text(encoding="utf-8") == ENV_TEXT


def test_dry_run_loader_failure_keeps_file(target):
    response = _client(target).put(
        "/admin/config", headers=AUTH, json={"settings": {"APM_INLINE_ROWS": "0"}}
    )
    assert response.status_code == 400
    assert response.json()["errors"] == [
        {"key": "", "message": "설정 로드 실패: ValueError"}  # 예외 문구(값 포함 가능) 미노출
    ]
    assert target.read_text(encoding="utf-8") == ENV_TEXT
    assert not target.with_name(".env.bak").exists()


def test_dry_run_builtin_message_has_no_value(target):
    """파일에 이미 있던 잘못된 int — 로더 실패 사유에 값을 싣지 않는다."""
    target.write_text(ENV_TEXT + "REDIS_PORT=notaport\n", encoding="utf-8")
    response = _client(target).put(
        "/admin/config", headers=AUTH, json={"settings": {"APM_INLINE_ROWS": "600"}}
    )
    assert response.status_code == 400
    assert response.json()["errors"] == [{"key": "", "message": "설정 로드 실패: ValueError"}]
    assert "notaport" not in response.text


def test_save_marks_restart_pending_and_audits_without_secrets(target):
    client = _client(target)
    dry = client.put(
        "/admin/config",
        headers=AUTH,
        json={"settings": {"APM_INLINE_ROWS": 600}, "dry_run": True},
    )
    assert dry.status_code == 200 and target.read_text(encoding="utf-8") == ENV_TEXT
    response = client.put(
        "/admin/config",
        headers=AUTH | {"X-Request-ID": "req-1"},
        json={"settings": {"APM_INLINE_ROWS": 600, "APM_EVENT_POLLER_ENABLED": True}},
    )
    body = response.json()
    assert response.status_code == 200 and body["restart_required"] is True
    assert set(body["saved_keys"]) == {"APM_INLINE_ROWS", "APM_EVENT_POLLER_ENABLED"}
    assert set(body["restart_pending_keys"]) == set(body["saved_keys"])
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# 머리 주석\n") and "APM_INLINE_ROWS=600\n" in text
    assert text.endswith("APM_EVENT_POLLER_ENABLED=true\n")
    assert (target.stat().st_mode & 0o777) == 0o640
    assert target.with_name(".env.bak").read_text(encoding="utf-8") == ENV_TEXT
    status = client.get("/admin/config/status", headers=AUTH).json()
    assert set(status["restart_pending_keys"]) == set(body["saved_keys"])
    audit = (target.parent / "audit.jsonl").read_text(encoding="utf-8")
    lines = [json.loads(line) for line in audit.splitlines()]
    assert [r["result"] for r in lines] == ["dry_run", "saved"]
    assert lines[-1]["request_id"] == "req-1" and lines[-1]["component"] == "apm_gateway"
    for secret in SECRETS:
        assert secret not in audit


# ── 필드 표 커버리지 ─────────────────────────────────────────


def test_field_table_covers_env_example():
    documented = set(_DOCUMENTED.findall((GATEWAY / ".env.example").read_text(encoding="utf-8")))
    specs, _, _ = build_fields(load_config({}, dotenv=None))
    keys = {spec.env_key for spec in specs}
    assert "APM_GATEWAY_ADMIN_TOKEN" in documented
    assert documented - keys == set()
    assert set(adapter_mod._SOURCE_SUFFIX_META) == set(config_mod.SOURCE_KEY_SUFFIXES)


def test_secret_keys_are_explicit():
    specs, _, _ = build_fields(_cfg())
    secrets = {spec.env_key for spec in specs if spec.is_secret}
    assert secrets == {
        "JENNIFER_API_TOKEN",
        "APM_GATEWAY_BEARER_TOKEN",
        "APM_GATEWAY_BEARER_TOKENS",
        "APM_GATEWAY_ADMIN_TOKEN",
        "REDIS_PASSWORD",
    }


def test_url_host_path_domain_keys_are_readonly(tmp_path):
    """자격증명이 따라가는 URL·호스트와 경로·도메인 제한은 관리 화면에서 바꾸지 않는다."""
    cfg = load_config(
        {
            "JENNIFER_SOURCES": '["bank"]',
            "JENNIFER_BANK_API_URL": "http://bank.test",
            "JENNIFER_BANK_API_TOKEN": BANK_TOKEN,
        },
        dotenv=None,
    )
    specs, _, _ = build_fields(cfg)
    assert adapter_mod.readonly_keys(specs) == {
        "APM_GATEWAY_HOST",
        "JENNIFER_SOURCES",
        "JENNIFER_API_URL",
        "JENNIFER_DOMAIN_IDS",
        "APM_SPOOL_DIR",
        "REDIS_HOST",
        "JENNIFER_BANK_API_URL",
        "JENNIFER_BANK_DOMAIN_IDS",
        # 노드 주소 웹 세션(plans/199) — 자격증명이 따라가는 주소 · 인증서 검증
        "JENNIFER_BANK_WEB_URL",
        "JENNIFER_BANK_WEB_TLS_VERIFY",
    }
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    response = _client(path, cfg).put(
        "/admin/config",
        headers=AUTH,
        json={
            "settings": {
                "JENNIFER_BANK_API_URL": "http://evil.test",
                "REDIS_HOST": "evil.test",
                "APM_SPOOL_DIR": "/tmp/x",
                "JENNIFER_DOMAIN_IDS": "[1]",
            }
        },
    )
    assert {i["reason"] for i in response.json()["ignored_keys"]} == {"readonly"}
    assert len(response.json()["ignored_keys"]) == 4
    assert path.read_text(encoding="utf-8") == ""


def test_load_dotenv_splits_only_on_cr_lf(tmp_path, monkeypatch):
    """유니코드 줄 구분자(U+2028·U+0085)로 값 안에 키 줄을 끼워 넣을 수 없다."""
    env = tmp_path / ".env"
    env.write_text(
        "APM_TIMEZONE=UTC\u2028APM_TEST_ADMIN_X=injected\r\nAPM_INLINE_ROWS=7\rREDIS_DB=2\n",
        encoding="utf-8",
    )
    load_dotenv(env)
    assert "APM_TEST_ADMIN_X" not in os.environ
    assert os.environ["APM_TIMEZONE"] == "UTC\u2028APM_TEST_ADMIN_X=injected"
    assert os.environ["APM_INLINE_ROWS"] == "7" and os.environ["REDIS_DB"] == "2"
