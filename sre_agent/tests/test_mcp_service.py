"""FastMCP 조사 서비스 도구 계약·인증 미들웨어 검증 (Plan 05 §3·§5·§8).

라이브 서버 기동 없이: 도구 등록 스모크 + call_tool 왕복 + ASGI 미들웨어 단위 검증.
"""

import asyncio
import json

from sre_agent.application.investigation_jobs import CONTRACT_VERSION, JobStore
from sre_agent.interface.mcp_service import (
    DEFAULT_PORT,
    StaticBearerAuthMiddleware,
    build_asgi_app,
    create_service,
    get_job_store,
)
from sre_agent.settings import AgentSettings


def make_settings(gemini_api_key=None) -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        model="test/model",
        api_key=None,
        max_steps=3,
        gemini_api_key=gemini_api_key,
        service_bearer_token=None,
    )


def valid_payload(fingerprint="fp-svc") -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "event": {"serverName": "web-01", "hostname": "web-01.local", "severity": 2},
        "decision": {"fingerprint": fingerprint},
    }


def _service(tmp_path, gemini_api_key=None):
    settings = make_settings(gemini_api_key=gemini_api_key)
    store = JobStore(settings, audit_path=tmp_path / "audit.jsonl")
    return create_service(settings, job_store=store), store


def _tool_json(result) -> dict:
    """call_tool 반환(tuple 또는 list)에서 TextContent JSON을 파싱한다."""
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def _call(mcp, name, args=None) -> dict:
    return _tool_json(asyncio.run(mcp.call_tool(name, args or {})))


# ── 도구 등록 스모크 ─────────────────────────────────────────────


def test_five_tools_registered(tmp_path):
    mcp, _ = _service(tmp_path)
    names = sorted(t.name for t in asyncio.run(mcp.list_tools()))
    assert names == [
        "sre_diagnose",
        "sre_get_investigation",
        "sre_health",
        "sre_investigate_alarm",
        "sre_list_investigations",
    ]


def test_default_port_9098():
    assert DEFAULT_PORT == 9098


# ── 도구 계약 왕복 ───────────────────────────────────────────────


def test_investigate_then_get(tmp_path):
    mcp, _ = _service(tmp_path)
    res = _call(mcp, "sre_investigate_alarm", {"payload": valid_payload()})
    assert res["status"] == "accepted"
    jid = res["investigation_id"]

    got = _call(mcp, "sre_get_investigation", {"investigation_id": jid})
    assert got["status"] == "stub"
    assert "조사 미실행" in got["verdict"]


def test_investigate_duplicate(tmp_path):
    mcp, _ = _service(tmp_path)
    a = _call(mcp, "sre_investigate_alarm", {"payload": valid_payload("dup")})
    b = _call(mcp, "sre_investigate_alarm", {"payload": valid_payload("dup")})
    assert a["status"] == "accepted"
    assert b["status"] == "duplicate"
    assert b["investigation_id"] == a["investigation_id"]


def test_investigate_rejected_missing_field(tmp_path):
    mcp, _ = _service(tmp_path)
    p = valid_payload()
    p["event"].pop("hostname")
    res = _call(mcp, "sre_investigate_alarm", {"payload": p})
    assert res["status"] == "rejected"
    assert "hostname" in res["reason"]


def test_investigate_wait_returns_state(tmp_path):
    mcp, _ = _service(tmp_path)
    # wait_seconds>0 → 스텁은 즉시 확정되므로 현재 상태(stub)를 함께 반환한다.
    res = _call(mcp, "sre_investigate_alarm", {"payload": valid_payload("w"), "wait_seconds": 1})
    assert res["status"] == "stub"


def test_diagnose_tool(tmp_path):
    mcp, _ = _service(tmp_path)
    res = _call(mcp, "sre_diagnose", {"question": "web-01 원인 분석", "server_name": "web-01"})
    assert res["status"] == "accepted"
    got = _call(mcp, "sre_get_investigation", {"investigation_id": res["investigation_id"]})
    assert got["kind"] == "diagnosis"


def test_list_tool(tmp_path):
    mcp, _ = _service(tmp_path)
    _call(mcp, "sre_investigate_alarm", {"payload": valid_payload("l1")})
    _call(mcp, "sre_investigate_alarm", {"payload": valid_payload("l2")})
    listing = _call(mcp, "sre_list_investigations", {"limit": 20})
    assert listing["count"] == 2
    assert len(listing["investigations"]) == 2


def test_health_tool_fields(tmp_path):
    mcp, _ = _service(tmp_path, gemini_api_key=None)
    health = _call(mcp, "sre_health")
    assert set(health) == {
        "status",
        "version",
        "contract_version",
        "holmes_ready",
        "polestar_mcp_reachable",
    }
    assert health["status"] == "ok"
    assert health["contract_version"] == CONTRACT_VERSION
    assert health["holmes_ready"] is False  # 키 부재 → 조사 불가를 정직 보고
    assert health["polestar_mcp_reachable"] is None  # 라이브 프로브 2-D 소관


def test_health_holmes_ready_with_key(tmp_path):
    mcp, _ = _service(tmp_path, gemini_api_key="k")
    health = _call(mcp, "sre_health")
    assert health["holmes_ready"] is True


def test_get_job_store_accessor(tmp_path):
    mcp, store = _service(tmp_path)
    assert get_job_store(mcp) is store


# ── 정적 Bearer 인증 미들웨어 ────────────────────────────────────


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


def test_auth_middleware_rejects_missing_token():
    called = {"downstream": False}

    async def app(scope, receive, send):
        called["downstream"] = True

    mw = StaticBearerAuthMiddleware(app, token="secret")
    sink = _Sink()
    asyncio.run(mw(_http_scope(auth=None), _noop_receive, sink.send))

    assert called["downstream"] is False
    start = sink.messages[0]
    assert start["status"] == 401


def test_auth_middleware_accepts_valid_token():
    called = {"downstream": False}

    async def app(scope, receive, send):
        called["downstream"] = True

    mw = StaticBearerAuthMiddleware(app, token="secret")
    sink = _Sink()
    asyncio.run(mw(_http_scope(auth="Bearer secret"), _noop_receive, sink.send))
    assert called["downstream"] is True


def test_auth_middleware_none_token_passthrough():
    called = {"downstream": False}

    async def app(scope, receive, send):
        called["downstream"] = True

    mw = StaticBearerAuthMiddleware(app, token=None)
    sink = _Sink()
    asyncio.run(mw(_http_scope(auth=None), _noop_receive, sink.send))
    assert called["downstream"] is True


def test_build_asgi_app_wraps_middleware(tmp_path):
    mcp, _ = _service(tmp_path)
    app = build_asgi_app(mcp, token="secret")
    # StaticBearerAuthMiddleware가 미들웨어 스택에 등록됐는지 확인.
    classes = [m.cls for m in app.user_middleware]
    assert StaticBearerAuthMiddleware in classes
