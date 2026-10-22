"""설정 관리 API(`/admin/config`) 배선·어댑터 검증 (plans/191 W5 · D-345 · SPEC §3).

실 FabriX 호출 0건(가짜 업스트림). 패키지 루트는 tmp_path로 주입해 실제 `.env`·`.encenv`를
건드리지 않는다. 설정은 `make_settings()`로 필드를 명시한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import fabrix_proxy.__main__ as entry
import httpx
import pytest
from conftest import AUTH, TOKEN, FakeUpstream, chat_body, make_settings
from fabrix_proxy.app import create_app
from fabrix_proxy.config import PACKAGE_ROOT, ProxySettings
from fabrix_proxy.config_admin_adapter import build_field_specs, wrap_admin

ADMIN = "proxy-admin-tok"
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN}"}
ENV_TEXT = (
    "# 프록시 표면\n"
    f"FABRIX_PROXY_TOKEN={TOKEN}\n"
    "FABRIX_PROXY_PORT=9095\n"
    "# 업스트림\n"
    "FABRIX_API_KEY=supersecret-api\n"
    "FABRIX_TIMEOUT=300\n"
)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """가짜 패키지 루트 — `.env`만 둔다. FABRIX_* OS env 제거."""
    for key in list(os.environ):
        if key.upper().startswith("FABRIX_"):
            monkeypatch.delenv(key)
    (tmp_path / ".env").write_text(ENV_TEXT, encoding="utf-8")
    return tmp_path


def _client(root: Path, **overrides: Any) -> httpx.AsyncClient:
    settings = make_settings(**overrides)
    app = create_app(settings, upstream=FakeUpstream())
    wrapped = wrap_admin(settings, app, package_root=root, audit_path=root / "audit.jsonl")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped), base_url="http://p.test")


def _items(body: dict) -> dict[str, dict]:
    return {item["env_key"]: item for group in body["groups"] for item in group["settings"]}


# ── 배선 ──


def test_no_admin_token_returns_app_unchanged() -> None:
    settings = make_settings()
    app = create_app(settings, upstream=FakeUpstream())
    assert wrap_admin(settings, app) is app


def test_admin_token_equal_to_proxy_token_not_registered(caplog) -> None:
    settings = make_settings(fabrix_proxy_admin_token=TOKEN)
    app = create_app(settings, upstream=FakeUpstream())
    with caplog.at_level(logging.WARNING, logger="fabrix_proxy"):
        assert wrap_admin(settings, app) is app
    assert "등록하지 않는다" in caplog.text
    assert TOKEN not in caplog.text


async def test_without_admin_token_admin_path_is_404(root) -> None:
    async with _client(root) as client:
        resp = await client.get("/admin/config/schema", headers=ADMIN_AUTH)
    assert resp.status_code == 404


async def test_unauthorized(root) -> None:
    async with _client(root, fabrix_proxy_admin_token=ADMIN) as client:
        assert (await client.get("/admin/config/schema")).status_code == 401
        # 프록시(소비자) 토큰으로는 관리 API를 쓸 수 없다
        assert (await client.get("/admin/config/status", headers=AUTH)).status_code == 401
        resp = await client.put("/admin/config", json={"settings": {}}, headers=AUTH)
        assert resp.status_code == 401


async def test_existing_routes_unchanged_with_admin(root) -> None:
    async with _client(root, fabrix_proxy_admin_token=ADMIN) as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/v1/models")).status_code == 200
        body = chat_body()
        assert (await client.post("/v1/chat/completions", json=body)).status_code == 401
        # 관리 토큰은 `/v1/*` 인증에 쓰이지 않는다
        resp = await client.post("/v1/chat/completions", json=body, headers=ADMIN_AUTH)
        assert resp.status_code == 401
        chat = await client.post("/v1/chat/completions", json=body, headers=AUTH)
        assert chat.status_code == 200


def test_main_wraps_only_with_admin_token(monkeypatch: pytest.MonkeyPatch, request) -> None:
    original = logging.getLogRecordFactory()
    request.addfinalizer(lambda: logging.setLogRecordFactory(original))
    monkeypatch.setitem(ProxySettings.model_config, "env_file", None)
    for key in list(os.environ):
        if key.upper().startswith("FABRIX_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("FABRIX_PROXY_TOKEN", "tok")
    seen: list[Any] = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **k: seen.append(app))
    assert entry.main([]) == 0
    assert type(seen[-1]).__name__ == "FastAPI"
    monkeypatch.setenv("FABRIX_PROXY_ADMIN_TOKEN", "admin-x")
    assert entry.main([]) == 0
    assert type(seen[-1]).__name__ == "AdminPathRouter"


# ── 관리 앱 · 어댑터 ──


async def test_schema_hides_secret_values(root) -> None:
    async with _client(root, fabrix_proxy_admin_token=ADMIN) as client:
        resp = await client.get("/admin/config/schema", headers=ADMIN_AUTH)
    assert resp.status_code == 200
    for value in (TOKEN, ADMIN, "supersecret-api", "secret-api-key", "secret-client-key"):
        assert value not in resp.text
    items = _items(resp.json())
    for key in (
        "FABRIX_PROXY_TOKEN",
        "FABRIX_API_KEY",
        "FABRIX_CLIENT_KEY",
        "FABRIX_PROXY_ADMIN_TOKEN",
    ):
        item = items[key]
        assert item["is_secret"] and item["is_set"]
        assert item["file_value"] is None and item["effective_value"] is None
        assert item["writable"] is False
    assert items["FABRIX_PROXY_HOST"]["readonly_reason"] == "readonly"
    assert items["FABRIX_PROXY_FEWSHOT"]["type"] == "enum"
    assert items["FABRIX_PROXY_MODEL_ALIASES"]["type"] == "json_list"
    assert items["FABRIX_LLM_CONFIG"]["type"] == "json_object"


async def test_encenv_key_is_override_encenv(root) -> None:
    (root / ".encenv").write_text("FABRIX_TIMEOUT=120\n", encoding="utf-8")
    async with _client(root, fabrix_proxy_admin_token=ADMIN) as client:
        item = _items((await client.get("/admin/config/schema", headers=ADMIN_AUTH)).json())[
            "FABRIX_TIMEOUT"
        ]
        resp = await client.put(
            "/admin/config", json={"settings": {"FABRIX_TIMEOUT": "60"}}, headers=ADMIN_AUTH
        )
    assert item["override"] == "encenv"
    assert resp.json()["ignored_keys"] == [{"key": "FABRIX_TIMEOUT", "reason": "override_encenv"}]
    assert (root / ".env").read_text(encoding="utf-8") == ENV_TEXT


async def test_os_env_is_override_os(root, monkeypatch) -> None:
    monkeypatch.setenv("FABRIX_PROXY_PORT", "9999")
    async with _client(root, fabrix_proxy_admin_token=ADMIN) as client:
        resp = await client.put(
            "/admin/config", json={"settings": {"FABRIX_PROXY_PORT": "9096"}}, headers=ADMIN_AUTH
        )
    assert resp.json()["ignored_keys"] == [{"key": "FABRIX_PROXY_PORT", "reason": "override_os"}]


READONLY = (
    "FABRIX_PROXY_HOST",
    "FABRIX_BASE_URL",
    "FABRIX_NATIVE_URL",
    "FABRIX_VERIFY_SSL",
    "FABRIX_PROXY_PROTOCOL_FILE",
    "FABRIX_PROXY_FEWSHOT_FILE",
)


async def test_boundary_keys_readonly_before_override(root) -> None:
    """자격증명이 따라가는 URL·TLS 검증·주입 파일 경로는 읽기 전용 — override보다 먼저다."""
    (root / ".encenv").write_text("FABRIX_BASE_URL=https://x.invalid\n", encoding="utf-8")
    async with _client(root, fabrix_proxy_admin_token=ADMIN) as client:
        items = _items((await client.get("/admin/config/schema", headers=ADMIN_AUTH)).json())
        resp = await client.put(
            "/admin/config",
            json={"settings": {key: "1" for key in READONLY}},
            headers=ADMIN_AUTH,
        )
    for key in READONLY:
        assert items[key]["readonly_reason"] == "readonly" and items[key]["writable"] is False
    assert resp.json()["ignored_keys"] == [{"key": k, "reason": "readonly"} for k in READONLY]
    assert (root / ".env").read_text(encoding="utf-8") == ENV_TEXT


async def test_dry_run_failure_leaves_file_unchanged(root) -> None:
    """dry-run은 실 로더(`(후보, root/.encenv)`)로 읽는다 — `.encenv`가 깨지면 저장 안 함."""
    (root / ".encenv").write_text("FABRIX_PROXY_REPAIR_MAX=-5\n", encoding="utf-8")
    async with _client(root, fabrix_proxy_admin_token=ADMIN) as client:
        resp = await client.put(
            "/admin/config", json={"settings": {"FABRIX_TIMEOUT": "60"}}, headers=ADMIN_AUTH
        )
    assert resp.status_code == 400
    assert any("fabrix_proxy_repair_max" in e["message"] for e in resp.json()["errors"])
    assert (root / ".env").read_text(encoding="utf-8") == ENV_TEXT
    assert not (root / ".env.bak").exists()


async def test_dry_run_flag_and_save(root) -> None:
    async with _client(root, fabrix_proxy_admin_token=ADMIN) as client:
        dry = await client.put(
            "/admin/config",
            json={"settings": {"FABRIX_TIMEOUT": "60"}, "dry_run": True},
            headers=ADMIN_AUTH,
        )
        assert dry.status_code == 200 and dry.json()["saved_keys"] == []
        assert (root / ".env").read_text(encoding="utf-8") == ENV_TEXT
        resp = await client.put(
            "/admin/config",
            json={"settings": {"FABRIX_TIMEOUT": "60", "FABRIX_PROXY_FEWSHOT": "none"}},
            headers={**ADMIN_AUTH, "X-Request-ID": "r-1"},
        )
        status = (await client.get("/admin/config/status", headers=ADMIN_AUTH)).json()
    assert resp.status_code == 200, resp.text
    assert resp.json()["saved_keys"] == ["FABRIX_TIMEOUT", "FABRIX_PROXY_FEWSHOT"]
    text = (root / ".env").read_text(encoding="utf-8")
    assert text == ENV_TEXT.replace("FABRIX_TIMEOUT=300", "FABRIX_TIMEOUT=60") + (
        "FABRIX_PROXY_FEWSHOT=none\n"
    )
    assert (root / ".env.bak").read_text(encoding="utf-8") == ENV_TEXT
    assert set(status["restart_pending_keys"]) == {"FABRIX_TIMEOUT", "FABRIX_PROXY_FEWSHOT"}
    loaded = ProxySettings(_env_file=root / ".env")  # type: ignore[call-arg]
    assert loaded.fabrix_timeout == 60.0 and loaded.fabrix_proxy_fewshot == "none"
    audit = [json.loads(x) for x in (root / "audit.jsonl").read_text().splitlines()]
    assert [a["result"] for a in audit] == ["dry_run", "saved"]
    assert audit[-1]["request_id"] == "r-1" and "supersecret" not in json.dumps(audit)


async def test_invalid_enum_rejected(root) -> None:
    async with _client(root, fabrix_proxy_admin_token=ADMIN) as client:
        resp = await client.put(
            "/admin/config", json={"settings": {"FABRIX_PROXY_FEWSHOT": "lots"}}, headers=ADMIN_AUTH
        )
    assert resp.status_code == 400
    assert (root / ".env").read_text(encoding="utf-8") == ENV_TEXT


def test_field_table_covers_env_example() -> None:
    """필드 표 ⊇ 예시 키 − 시크릿(주석 처리된 `# KEY=` 포함)."""
    specs = {s.env_key: s for s in build_field_specs()}
    text = (PACKAGE_ROOT / ".env.example").read_text(encoding="utf-8")
    keys = set(re.findall(r"^#?\s*(FABRIX_[A-Z_]+)=", text, re.M))
    non_secret = {k for k in keys if not (k in specs and specs[k].is_secret)}
    assert non_secret <= set(specs)
    assert "FABRIX_PROXY_ADMIN_TOKEN" in keys
    assert {k for k, s in specs.items() if s.is_secret} == {
        "FABRIX_PROXY_TOKEN",
        "FABRIX_API_KEY",
        "FABRIX_CLIENT_KEY",
        "FABRIX_PROXY_ADMIN_TOKEN",
    }
