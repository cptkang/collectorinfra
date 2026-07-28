"""전송 구간 정적 Bearer 인증 검증 (Plan 04 §6-4, D-015).

라이브 서버 기동 없이: 인증 미들웨어 단위 검증 + config 토큰 로딩 + build_asgi_app 조립.
sre_agent 조사 서비스(Plan 05 §5)와 동일한 정적 Bearer 방식이다.
"""

import asyncio

from mcp_server.config import AppServerConfig, ServerConfig, _apply_env_overrides, _load_toml
from mcp_server.server import StaticBearerAuthMiddleware, build_asgi_app, create_server


# ── ASGI 미들웨어 단위 검증 ──────────────────────────────────────


class _Sink:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(message)


async def _noop_receive():
    return {"type": "http.request", "body": b""}


def _http_scope(auth=None):
    headers = []
    if auth is not None:
        headers.append((b"authorization", auth.encode("latin-1")))
    return {"type": "http", "headers": headers, "method": "GET", "path": "/sse"}


def test_auth_middleware_none_token_passthrough():
    """토큰 미설정(None)이면 인증 없이 통과한다(로컬/개발 — 회귀 0)."""
    called = {"downstream": False}

    async def app(scope, receive, send):
        called["downstream"] = True

    mw = StaticBearerAuthMiddleware(app, token=None)
    sink = _Sink()
    asyncio.run(mw(_http_scope(auth=None), _noop_receive, sink.send))
    assert called["downstream"] is True
    assert sink.messages == []  # 미들웨어가 응답을 직접 쓰지 않음


def test_auth_middleware_rejects_missing_token():
    """토큰 설정 시 Authorization 헤더가 없으면 401을 반환한다."""
    called = {"downstream": False}

    async def app(scope, receive, send):
        called["downstream"] = True

    mw = StaticBearerAuthMiddleware(app, token="secret")
    sink = _Sink()
    asyncio.run(mw(_http_scope(auth=None), _noop_receive, sink.send))

    assert called["downstream"] is False
    assert sink.messages[0]["status"] == 401


def test_auth_middleware_rejects_wrong_token():
    """토큰 설정 시 값이 불일치하면 401을 반환한다."""
    called = {"downstream": False}

    async def app(scope, receive, send):
        called["downstream"] = True

    mw = StaticBearerAuthMiddleware(app, token="secret")
    sink = _Sink()
    asyncio.run(mw(_http_scope(auth="Bearer wrong"), _noop_receive, sink.send))

    assert called["downstream"] is False
    assert sink.messages[0]["status"] == 401


def test_auth_middleware_accepts_valid_token():
    """토큰이 일치하면 다운스트림으로 통과한다."""
    called = {"downstream": False}

    async def app(scope, receive, send):
        called["downstream"] = True

    mw = StaticBearerAuthMiddleware(app, token="secret")
    sink = _Sink()
    asyncio.run(mw(_http_scope(auth="Bearer secret"), _noop_receive, sink.send))
    assert called["downstream"] is True


def test_auth_middleware_non_http_scope_passthrough():
    """HTTP가 아닌 scope(lifespan 등)는 토큰 설정 여부와 무관하게 통과한다."""
    called = {"downstream": False}

    async def app(scope, receive, send):
        called["downstream"] = True

    mw = StaticBearerAuthMiddleware(app, token="secret")
    sink = _Sink()
    asyncio.run(mw({"type": "lifespan"}, _noop_receive, sink.send))
    assert called["downstream"] is True


# ── build_asgi_app 조립 ─────────────────────────────────────────


def _minimal_server():
    """DB 소스 없이 도구만 등록한 FastMCP를 생성한다(lifespan은 run 시점에만 실행)."""
    return create_server(AppServerConfig(server=ServerConfig(name="test-mcp")))


def test_build_asgi_app_wraps_middleware():
    """build_asgi_app이 StaticBearerAuthMiddleware를 미들웨어 스택에 등록한다."""
    app = build_asgi_app(_minimal_server(), token="secret")
    classes = [m.cls for m in app.user_middleware]
    assert StaticBearerAuthMiddleware in classes


def test_build_asgi_app_none_token_still_wraps():
    """토큰 None이어도 미들웨어는 등록되며(통과 전용), 앱 조립이 성공한다."""
    app = build_asgi_app(_minimal_server(), token=None)
    classes = [m.cls for m in app.user_middleware]
    assert StaticBearerAuthMiddleware in classes


# ── config 토큰 로딩 ────────────────────────────────────────────


def test_bearer_token_default_empty():
    """bearer_token 기본값은 빈 문자열이다(무인증 통과 전제 — 회귀 0)."""
    assert ServerConfig().bearer_token == ""


def test_bearer_token_from_toml(tmp_path):
    """config.toml [server].bearer_token을 로드한다."""
    toml_content = """
[server]
name = "auth-server"
bearer_token = "toml-token"
"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(toml_content)
    config = _load_toml(config_file)
    assert config.server.bearer_token == "toml-token"


def test_bearer_token_env_override(monkeypatch):
    """MCP_BEARER_TOKEN 환경변수가 TOML 값을 오버라이드한다."""
    monkeypatch.setenv("MCP_BEARER_TOKEN", "env-token")
    config = AppServerConfig(server=ServerConfig(bearer_token="toml-token"))
    _apply_env_overrides(config)
    assert config.server.bearer_token == "env-token"


def test_bearer_token_no_env_keeps_toml(monkeypatch):
    """환경변수가 없으면 TOML(또는 기본값)을 유지한다(무인증 회귀 0)."""
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    config = AppServerConfig(server=ServerConfig(bearer_token=""))
    _apply_env_overrides(config)
    assert config.server.bearer_token == ""
