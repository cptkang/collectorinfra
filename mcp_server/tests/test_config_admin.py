"""mcp_server 설정 관리 API(`/admin/config`) 배선·어댑터 검증 (plans/191 W4 · D-345 · SPEC §3).

공용 모듈(`config_admin.py`) 자체 로직은 정본 쪽 테스트가 맡는다. 여기서는 mcp_server에만 있는
것 — 관리 토큰 배선 · MCP 도구 표면 불변(D-003) · 서비스 Bearer와의 분리 · 시크릿 미노출 ·
가드 키 읽기 전용 · OS 원천 판정 · `os.environ`을 건드리지 않는 dry-run — 을 본다.
실 DB·외부 호출 없이 `create_server`(lifespan 미실행)와 Starlette TestClient만 쓴다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace

import mcp_server.config as config_mod
import mcp_server.server as server_mod
import pytest
from mcp_server.config import (
    DOTENV_INJECTED_KEYS,
    AppServerConfig,
    PrometheusConfig,
    ServerConfig,
    SourceConfig,
    _apply_env_overrides,
    load_config,
)
from mcp_server.config_admin import AdminPathRouter
from mcp_server.config_admin_adapter import build_field_specs, wrap_admin_app
from mcp_server.server import build_asgi_app, create_server
from starlette.testclient import TestClient

_PKG = Path(__file__).resolve().parents[1]
ADMIN = "admin-token-xyz"
BEARER = "bearer-token-abc"
CONN = "postgresql://u:conn-secret-1@h:5432/d"
AUTH_HEADER = "Bearer prom-secret-2"

_TOML = """
[server]
name = "t"
expose_execute_sql = true

[[sources]]
name = "src_a"
type = "postgresql"

[[sources]]
name = "src_b"
type = "db2"
"""


@pytest.fixture
def injected_keys():
    """`DOTENV_INJECTED_KEYS`(모듈 전역 누적 집합)를 테스트 뒤 원복한다."""
    saved = set(DOTENV_INJECTED_KEYS)
    yield DOTENV_INJECTED_KEYS
    DOTENV_INJECTED_KEYS.clear()
    DOTENV_INJECTED_KEYS.update(saved)


@pytest.fixture
def clean_env(monkeypatch):
    """필드 표 키를 프로세스 환경에서 지워 `override_os` 누수를 막는다."""
    for spec in build_field_specs(_PKG / "config.toml"):
        monkeypatch.delenv(spec.env_key, raising=False)
    for key in ("SRC_A_CONNECTION", "SRC_B_CONNECTION"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def env_dir(tmp_path, clean_env, injected_keys):
    """config.toml + .env 한 쌍(관리 대상 디렉토리)."""
    (tmp_path / "config.toml").write_text(_TOML, encoding="utf-8")
    (tmp_path / ".env").write_text(
        f"# 주석\nSERVER_PORT=9099\nSRC_A_CONNECTION={CONN}\n"
        f"PROMETHEUS_AUTH_HEADER={AUTH_HEADER}\n",
        encoding="utf-8",
    )
    return tmp_path


def _config(admin: str = ADMIN, bearer: str = BEARER) -> AppServerConfig:
    return AppServerConfig(
        server=ServerConfig(name="t", admin_token=admin, bearer_token=bearer),
        sources=[SourceConfig(name="src_a", connection=CONN)],
        prometheus=PrometheusConfig(auth_header=AUTH_HEADER),
    )


def _client(env_dir: Path, cfg: AppServerConfig | None = None) -> TestClient:
    cfg = cfg or _config()
    app = wrap_admin_app(
        build_asgi_app(create_server(cfg), cfg.server.bearer_token or None),
        cfg,
        config_path=env_dir / "config.toml",
        audit_path=env_dir / "audit.jsonl",
    )
    return TestClient(app)


def _auth(token: str = ADMIN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _tool_names(cfg: AppServerConfig) -> set[str]:
    return {t.name for t in asyncio.run(create_server(cfg).list_tools())}


# ── 배선: 토큰 없음 = 현행 동일 · 토큰 = Bearer면 미등록 ──────────────


def test_no_admin_token_returns_same_app(env_dir):
    cfg = _config(admin="")
    app = build_asgi_app(create_server(cfg), BEARER)
    assert wrap_admin_app(app, cfg, config_path=env_dir / "config.toml") is app
    # 관리 경로는 기존 앱이 처리한다 — 서비스 Bearer가 켜져 있으면 401(오늘과 같다).
    assert TestClient(app).get("/admin/config/schema", headers=_auth()).status_code == 401


def test_admin_token_equal_to_bearer_not_registered(env_dir, caplog):
    cfg = _config(admin=BEARER, bearer=BEARER)
    app = build_asgi_app(create_server(cfg), BEARER)
    with caplog.at_level(logging.WARNING):
        assert wrap_admin_app(app, cfg, config_path=env_dir / "config.toml") is app
    assert "등록하지 않는다" in caplog.text
    assert BEARER not in caplog.text


def test_admin_token_wraps_router(env_dir):
    cfg = _config()
    app = wrap_admin_app(
        build_asgi_app(create_server(cfg), BEARER), cfg, config_path=env_dir / "config.toml"
    )
    assert isinstance(app, AdminPathRouter)


def test_tool_surface_unchanged_by_admin_token(env_dir):
    """D-003: 관리 토큰 유무·래퍼 적용과 무관하게 MCP 도구 이름 집합이 같다."""
    without = _tool_names(_config(admin=""))
    cfg = _config()
    mcp = create_server(cfg)
    wrap_admin_app(build_asgi_app(mcp, BEARER), cfg, config_path=env_dir / "config.toml")
    assert {t.name for t in asyncio.run(mcp.list_tools())} == without
    assert _tool_names(cfg) == without
    assert without


# ── 인증 분리 ────────────────────────────────────────────────────


def test_admin_path_requires_admin_token(env_dir):
    client = _client(env_dir)
    assert client.get("/admin/config/schema").status_code == 401
    assert client.get("/admin/config/schema", headers=_auth("wrong")).status_code == 401
    # 서비스 Bearer로는 관리 경로를 열 수 없다.
    assert client.get("/admin/config/schema", headers=_auth(BEARER)).status_code == 401
    # 관리 토큰만으로 통과 — 서비스 Bearer 불필요.
    assert client.get("/admin/config/schema", headers=_auth()).status_code == 200
    assert client.get("/admin/config/status", headers=_auth()).status_code == 200


def test_mcp_paths_keep_bearer_rule(env_dir):
    client = _client(env_dir)
    assert client.get("/nope").status_code == 401
    assert client.get("/nope", headers=_auth()).status_code == 401  # 관리 토큰은 MCP 경로 무효
    assert client.get("/nope", headers=_auth(BEARER)).status_code == 404


# ── 스키마: 시크릿 미노출 · 소스 · readonly ────────────────────────


def test_schema_never_contains_secret_values(env_dir):
    resp = _client(env_dir).get("/admin/config/schema", headers=_auth())
    text = resp.text
    for secret in (CONN, "conn-secret-1", AUTH_HEADER, "prom-secret-2", ADMIN, BEARER):
        assert secret not in text
    items = {i["env_key"]: i for g in resp.json()["groups"] for i in g["settings"]}
    assert items["SRC_A_CONNECTION"]["is_secret"] is True
    assert items["SRC_A_CONNECTION"]["is_set"] is True
    assert items["SRC_A_CONNECTION"]["effective_value"] is None
    assert items["SRC_B_CONNECTION"]["is_set"] is False
    assert "db2" in items["SRC_B_CONNECTION"]["description"]
    assert items["PROMETHEUS_AUTH_HEADER"]["file_value"] is None
    assert items["MCP_ADMIN_TOKEN"]["is_secret"] is True
    # toml 값(expose_execute_sql=true 등)은 effective로 보이되 가드 키는 읽기 전용.
    assert items["SERVER_PORT"]["file_value"] == "9099"
    for key in ("SERVER_HOST", "EXPOSE_EXECUTE_SQL", "POLESTAR_DOMAIN_GUARD", "EXPOSE_RAW_PROMQL"):
        assert items[key]["writable"] is False
        assert items[key]["readonly_reason"] == "readonly"
    roles = {s["role"] for s in resp.json()["sources"]}
    assert roles == {"readonly", "write_target", "override"}


def test_guard_keys_readonly_on_put(env_dir):
    before = (env_dir / ".env").read_text()
    resp = _client(env_dir).put(
        "/admin/config",
        headers=_auth(),
        json={"settings": {
            "SERVER_HOST": "127.0.0.1", "EXPOSE_EXECUTE_SQL": False,
            "POLESTAR_DOMAIN_GUARD": False, "EXPOSE_RAW_PROMQL": True,
        }},
    )
    assert resp.status_code == 200
    reasons = {i["key"]: i["reason"] for i in resp.json()["ignored_keys"]}
    assert reasons == dict.fromkeys(
        ("SERVER_HOST", "EXPOSE_EXECUTE_SQL", "POLESTAR_DOMAIN_GUARD", "EXPOSE_RAW_PROMQL"),
        "readonly",
    )
    assert resp.json()["saved_keys"] == []
    assert (env_dir / ".env").read_text() == before


def test_url_and_path_keys_readonly(env_dir, monkeypatch):
    """URL·호스트·가드 꼴 키는 읽기 전용(자격증명이 그 호스트로 따라간다). OS 원천보다 앞선다."""
    from mcp_server.config_admin_adapter import READONLY_KEYS

    assert {"PROMETHEUS_URL", "PROCESS_API_BASE_URL", "SERVER_HOST"} <= READONLY_KEYS
    pattern = re.compile(r"(_URL|_BASE|_PATH|_DIR|_FILE|_HOST)$|GUARD|VERIFY")
    keys = {s.env_key for s in build_field_specs(_PKG / "config.toml") if not s.is_secret}
    assert {k for k in keys if pattern.search(k)} <= READONLY_KEYS
    monkeypatch.setenv("PROMETHEUS_URL", "http://os-set:9090")
    before = (env_dir / ".env").read_text()
    resp = _client(env_dir).put(
        "/admin/config",
        headers=_auth(),
        json={"settings": {"PROMETHEUS_URL": "http://evil:9090",
                           "PROCESS_API_BASE_URL": "http://evil:1"}},
    )
    reasons = {i["key"]: i["reason"] for i in resp.json()["ignored_keys"]}
    assert reasons == {"PROMETHEUS_URL": "readonly", "PROCESS_API_BASE_URL": "readonly"}
    assert (env_dir / ".env").read_text() == before


def test_secret_keys_not_writable(env_dir):
    resp = _client(env_dir).put(
        "/admin/config",
        headers=_auth(),
        json={"settings": {"SRC_A_CONNECTION": "x", "MCP_ADMIN_TOKEN": "y"}},
    )
    reasons = {i["key"]: i["reason"] for i in resp.json()["ignored_keys"]}
    assert reasons == {"SRC_A_CONNECTION": "secret", "MCP_ADMIN_TOKEN": "secret"}


# ── OS 원천 판정 ──────────────────────────────────────────────────


def test_os_origin_key_is_override_os(env_dir, monkeypatch, injected_keys):
    monkeypatch.setenv("OPENMETRICS_MAX_SERIES", "50")
    monkeypatch.setenv("SERVER_NAME", "from-dotenv")
    injected_keys.add("SERVER_NAME")  # .env가 넣은 키는 override가 아니다
    resp = _client(env_dir).put(
        "/admin/config",
        headers=_auth(),
        json={"settings": {"OPENMETRICS_MAX_SERIES": 60, "SERVER_NAME": "n2"},
              "dry_run": True},
    )
    assert resp.status_code == 200
    assert resp.json()["ignored_keys"] == [
        {"key": "OPENMETRICS_MAX_SERIES", "reason": "override_os"}
    ]


def test_load_config_records_injected_keys(tmp_path, monkeypatch, injected_keys):
    monkeypatch.delenv("ADMIN_TEST_INJECTED", raising=False)
    monkeypatch.setenv("ADMIN_TEST_PRESET", "os")
    (tmp_path / "config.toml").write_text("[server]\nname='x'\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "ADMIN_TEST_INJECTED=1\nADMIN_TEST_PRESET=file\n", encoding="utf-8"
    )
    try:
        load_config(tmp_path / "config.toml")
        assert "ADMIN_TEST_INJECTED" in injected_keys
        assert "ADMIN_TEST_PRESET" not in injected_keys
        assert os.environ["ADMIN_TEST_PRESET"] == "os"
    finally:
        # load_config가 직접 넣은 키는 monkeypatch가 원복하지 않는다 — 직접 지운다.
        os.environ.pop("ADMIN_TEST_INJECTED", None)
    assert "ADMIN_TEST_INJECTED" not in os.environ


def test_read_dotenv_splits_crlf_and_cr(tmp_path):
    from mcp_server.config import read_dotenv

    path = tmp_path / ".env"
    path.write_bytes(b"A_KEY=1\r\nB_KEY=2\rC_KEY=3\nD_KEY=4")
    assert read_dotenv(path) == {"A_KEY": "1", "B_KEY": "2", "C_KEY": "3", "D_KEY": "4"}


# ── PUT: 저장 · dry-run · 실패 시 파일 불변 ─────────────────────────


def test_save_and_restart_pending(env_dir):
    client = _client(env_dir)
    resp = client.put("/admin/config", headers=_auth(), json={"settings": {"SERVER_PORT": 9100}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["saved_keys"] == ["SERVER_PORT"]
    assert body["restart_required"] is True
    assert body["restart_pending_keys"] == ["SERVER_PORT"]
    text = (env_dir / ".env").read_text()
    assert "SERVER_PORT=9100" in text and text.startswith("# 주석\n")
    assert (env_dir / ".env.bak").is_file()
    status = client.get("/admin/config/status", headers=_auth()).json()
    assert status["restart_pending_keys"] == ["SERVER_PORT"]
    assert status["component"]["name"] == "mcp_server"
    audit = (env_dir / "audit.jsonl").read_text()
    assert '"saved"' in audit and CONN not in audit


def test_dry_run_does_not_touch_os_environ_or_file(env_dir):
    client = _client(env_dir)
    before_env = dict(os.environ)
    before_file = (env_dir / ".env").read_text()
    resp = client.put(
        "/admin/config", headers=_auth(),
        json={"settings": {"SERVER_PORT": 9200, "OPENMETRICS_MAX_SERIES": 50}, "dry_run": True},
    )
    assert resp.status_code == 200 and resp.json()["dry_run"] is True
    assert (env_dir / ".env").read_text() == before_file
    client.put("/admin/config", headers=_auth(), json={"settings": {"SERVER_PORT": 9200}})
    assert dict(os.environ) == before_env


def test_dry_run_failure_leaves_file_unchanged(env_dir):
    path = env_dir / ".env"
    path.write_text(path.read_text() + "OPENMETRICS_MAX_SERIES=abc\n", encoding="utf-8")
    before = path.read_text()
    resp = _client(env_dir).put(
        "/admin/config", headers=_auth(), json={"settings": {"SERVER_PORT": 9300}}
    )
    assert resp.status_code == 400
    assert resp.json()["errors"] == [{"key": "", "message": "설정 로드 실패: ValueError"}]
    assert path.read_text() == before
    assert not (env_dir / ".env.bak").exists()


def test_type_validation_error(env_dir):
    resp = _client(env_dir).put(
        "/admin/config", headers=_auth(),
        json={"settings": {"SERVER_PORT": "abc", "OPENMETRICS_FALLBACK_POLICY": "nope"}},
    )
    assert resp.status_code == 400
    assert {e["key"] for e in resp.json()["errors"]} == {
        "SERVER_PORT", "OPENMETRICS_FALLBACK_POLICY"
    }


# ── 필드 표 커버리지 ─────────────────────────────────────────────


def test_field_table_covers_env_example_and_overrides():
    """필드 표 ⊇ `.env.example` 키 − 시크릿, 그리고 `_apply_env_overrides`가 읽는 키 전부."""
    import inspect

    specs = build_field_specs(_PKG / "config.toml")
    keys = {s.env_key for s in specs}
    secrets = {s.env_key for s in specs if s.is_secret}
    documented = set(
        re.findall(
            r"^\s*#?\s*([A-Z][A-Z0-9_]{2,})\s*=",
            (_PKG / ".env.example").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    assert "MCP_ADMIN_TOKEN" in documented
    assert sorted(documented - secrets - keys) == []
    read = set(re.findall(r'"([A-Z][A-Z0-9_]{2,})"', inspect.getsource(_apply_env_overrides)))
    assert sorted(read - keys) == []


# ── `_apply_env_overrides` 매핑 주입 · 기본 동작 불변 ────────────────


def test_apply_env_overrides_default_reads_os_environ(monkeypatch):
    monkeypatch.setenv("SERVER_PORT", "1111")
    cfg = AppServerConfig()
    _apply_env_overrides(cfg)
    assert cfg.server.port == 1111


def test_apply_env_overrides_mapping_ignores_os_environ(monkeypatch):
    monkeypatch.setenv("SERVER_PORT", "1111")
    cfg = AppServerConfig()
    _apply_env_overrides(cfg, env={"SERVER_PORT": "2222", "MCP_ADMIN_TOKEN": "a"})
    assert cfg.server.port == 2222
    assert cfg.server.admin_token == "a"
    cfg2 = AppServerConfig()
    _apply_env_overrides(cfg2, env={})
    assert cfg2.server.port == 9099


def test_admin_token_value_not_logged(monkeypatch, caplog):
    monkeypatch.setenv("MCP_ADMIN_TOKEN", "never-log-me")
    cfg = AppServerConfig()
    with caplog.at_level(logging.DEBUG, logger=config_mod.__name__):
        _apply_env_overrides(cfg)
    assert cfg.server.admin_token == "never-log-me"
    assert "never-log-me" not in caplog.text


def test_admin_token_default_empty_and_not_from_toml(tmp_path):
    assert ServerConfig().admin_token == ""
    (tmp_path / "config.toml").write_text("[server]\nadmin_token='t'\n", encoding="utf-8")
    assert config_mod._load_toml(tmp_path / "config.toml").server.admin_token == ""


# ── 진입점 배선 ──────────────────────────────────────────────────


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    factory = logging.getLogRecordFactory()
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.setLogRecordFactory(factory)


@pytest.mark.parametrize("admin, wrapped", [(ADMIN, True), ("", False)])
def test_main_wires_admin_router_only_with_token(
    monkeypatch, restore_logging, env_dir, admin, wrapped
):
    import mcp_server.config_admin_adapter as adapter_mod
    import uvicorn
    from mcp_server.__main__ import main

    cfg = _config(admin=admin)
    cfg.server.transport = "sse"
    captured: dict[str, object] = {}
    monkeypatch.setattr(config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.setdefault("app", app))
    monkeypatch.setattr(adapter_mod, "_DEFAULT_CONFIG_PATH", env_dir / "config.toml")
    monkeypatch.setattr(adapter_mod, "AUDIT_PATH", env_dir / "audit.jsonl")
    main()
    assert isinstance(captured["app"], AdminPathRouter) is wrapped


@pytest.mark.parametrize("admin, warned", [(ADMIN, True), ("", False)])
def test_main_warns_admin_token_on_non_sse(
    monkeypatch, restore_logging, caplog, admin, warned
):
    from mcp_server.__main__ import main

    cfg = _config(admin=admin)
    cfg.server.transport = "stdio"
    runs: list[str] = []
    monkeypatch.setattr(config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(
        server_mod,
        "create_server",
        lambda c: SimpleNamespace(run=lambda transport: runs.append(transport)),
    )
    # main()의 basicConfig(force=True)가 caplog 핸들러를 걷어내지 않게 막는다.
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: None)
    with caplog.at_level(logging.WARNING, logger="mcp_server"):
        main()
    assert runs == ["stdio"]
    assert ("sse에서만 등록된다" in caplog.text) is warned
    assert ADMIN not in caplog.text
