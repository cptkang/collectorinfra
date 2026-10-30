"""plans/202 W1 — 웹 세션 노드 주소 조회기 (D-355 ①⑥⑦ · §2.1·§3).

가짜 웹 서버(`httpx.MockTransport`)만 쓴다 — 실제 제니퍼·외부 네트워크·실 LLM 없음. 가짜 서버는
실측(§0-16·17·19)과 같은 규칙으로 답한다: 사전 `GET /login`으로 받은 세션 쿠키 없이 POST하면
`/login`으로 되돌리고, 로그인 성공은 `/`로 302하며 `ID7900`·`PASSWORD7900` 쿠키를 내린다. 응답
원소에는 JMX 자격증명 칸이 섞여 있다.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from urllib.parse import parse_qs

import httpx
import pytest
from apm_gateway.adapters.jennifer import web_fields
from apm_gateway.adapters.jennifer.web_session import (
    LIST_PATH,
    WebNotAllowedError,
    WebRequestError,
    WebSession,
    check_web_request,
)
from apm_gateway.application import node_address as na
from apm_gateway.application.node_address import NodeAddressService, build_node_address
from apm_gateway.interface.config_admin_adapter import build_fields

from apm_gateway.config import describe, load_config

WEB = "http://web.test:7900"
USER = "aiops-viewer"
PASSWORD = "pw-SECRET-7f1c"
COOKIE_ID = "ID-COOKIE-SECRET-1"
COOKIE_PW = "PW-COOKIE-SECRET-2"
JMX_PW = "JMX-SECRET-pw-9"
POD_SID = 10020
VM_SID = 20030
MIXED_SID = 30040


def _row(inst: int, sid: int, ip: str, host: str, name: str) -> dict:
    return {
        "instId": inst,
        "sid": sid,
        "oid": 99,
        "hostName": name,
        "ip": ip,
        "hostAddress": host,
        "status": 3,
        "statusMsg": "",
        "lastLiveTime": 1790643600000,
        "os": "Linux",
        "connectedVersion": "5.6.4",
        "jmx": {"idOrEmpty": "jmxuser", "passwordOrEmpty": JMX_PW},
        "jmx.passwordOrEmpty": JMX_PW,
    }


ROWS = {
    POD_SID: [
        _row(1001, POD_SID, "10.139.0.5", "10.136.1.7", "pod-a-7d9f"),
        _row(1002, POD_SID, "10.139.0.6", "10.136.1.8", "pod-b-7d9f"),
    ],
    VM_SID: [_row(2001, VM_SID, "10.20.0.1", "10.20.0.1", "vm-01")],
    MIXED_SID: [
        _row(3001, MIXED_SID, "10.139.0.9", "10.136.1.9", "pod-c"),
        _row(3002, MIXED_SID, "10.20.0.2", "10.20.0.2", "vm-02"),
    ],
}


class FakeWeb:
    """실측 규칙대로 답하는 가짜 뷰서버 웹. `calls`에 (메서드, 경로)를 남긴다."""

    def __init__(self, *, password: str = PASSWORD) -> None:
        self.password = password
        self.calls: list[tuple[str, str]] = []
        self.requests: list[httpx.Request] = []
        self.pending: set[str] = set()  # GET /login으로 받은 로그인 전 세션
        self.valid: set[str] = set()  # 로그인된 세션
        self.counter = 0
        self.list_mode = "ok"  # ok · html · redirect_once · html_once · always_expired · dict · 500
        self.rows = {k: list(v) for k, v in ROWS.items()}

    def _session_of(self, request: httpx.Request) -> str:
        for part in (request.headers.get("cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "JSESSIONID7900":
                return value
        return ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        self.requests.append(request)
        if request.method == "GET" and path == "/login":
            self.counter += 1
            sid = f"S{self.counter}"
            self.pending.add(sid)
            return httpx.Response(
                200,
                text="<html><title>login</title></html>",
                headers={
                    "content-type": "text/html",
                    "set-cookie": f"JSESSIONID7900={sid}; Path=/",
                },
            )
        if request.method == "POST" and path == "/login/page":
            sid = self._session_of(request)
            form = parse_qs(request.content.decode())
            ok = (
                sid in self.pending  # 사전 GET 없이는 거부(b0 https 실측)
                and request.headers.get("origin") == WEB
                and request.headers.get("referer") == WEB + "/login"
                and form.get("id") == [USER]
                and form.get("password") == [self.password]
            )
            if not ok:
                return httpx.Response(302, headers={"location": WEB + "/login?error=true"})
            self.valid.add(sid)
            return httpx.Response(
                302,
                headers=[
                    ("location", WEB + "/"),
                    ("set-cookie", f"ID7900={COOKIE_ID}; Path=/"),
                    ("set-cookie", f"PASSWORD7900={COOKIE_PW}; Path=/"),
                ],
            )
        if request.method == "GET" and path == LIST_PATH:
            sid = self._session_of(request)
            mode = self.list_mode
            if mode in ("redirect_once", "html_once"):
                self.list_mode = "ok"
                self.valid.discard(sid)
            if mode == "always_expired" or sid not in self.valid:
                return httpx.Response(302, headers={"location": WEB + "/login"})
            if mode == "html":
                return httpx.Response(200, text="<!DOCTYPE html><html>login</html>")
            if mode == "html_once":
                return httpx.Response(
                    200, text="<html>login</html>", headers={"content-type": "text/html"}
                )
            if mode == "redirect_once":
                return httpx.Response(302, headers={"location": WEB + "/login"})
            if mode == "dict":
                return httpx.Response(200, json={"result": []})
            if mode == "500":
                return httpx.Response(500, text="boom")
            domain = int(request.url.params["sid"])
            return httpx.Response(200, json=self.rows.get(domain, []))
        return httpx.Response(404)


def _session(fake: FakeWeb, **kw) -> WebSession:
    return WebSession(
        source_id="b0",
        url=WEB,
        user=kw.pop("user", USER),
        password=kw.pop("password", PASSWORD),
        transport=httpx.MockTransport(fake),
        **kw,
    )


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _service(fake: FakeWeb, clock: Clock, **kw) -> NodeAddressService:
    return NodeAddressService({"b0": _session(fake, **kw)}, clock=clock)


def _lists(fake: FakeWeb) -> int:
    return sum(1 for c in fake.calls if c == ("GET", LIST_PATH))


# ── 로그인 · 세션 ──────────────────────────────────────────


async def test_plain_post_without_pre_get_is_rejected_by_fake_server():
    """가짜 서버 전제 확인 — 사전 GET 없이 POST하면 `/login`으로 되돌린다(b0 실측)."""
    fake = FakeWeb()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url=WEB) as client:
        r = await client.post(
            "/login/page",
            data={"id": USER, "password": PASSWORD, "autoLogin": "false", "redirect": ""},
            headers={"Origin": WEB, "Referer": WEB + "/login"},
        )
    assert r.status_code == 302 and r.headers["location"].endswith("/login?error=true")


async def test_browser_style_login_then_list():
    fake = FakeWeb()
    session = _session(fake)
    rows = await session.fetch_list(POD_SID)
    assert fake.calls == [("GET", "/login"), ("POST", "/login/page"), ("GET", LIST_PATH)]
    post = fake.requests[1]
    assert post.headers["origin"] == WEB and post.headers["referer"] == WEB + "/login"
    assert "Mozilla" in post.headers["user-agent"]
    form = parse_qs(post.content.decode(), keep_blank_values=True)
    assert form == {"id": [USER], "password": [PASSWORD], "autoLogin": ["false"], "redirect": [""]}
    assert [r["host_address"] for r in rows] == ["10.136.1.7", "10.136.1.8"]
    # 세션 쿠키만 남는다(ID·PASSWORD 쿠키는 받는 즉시 버림)
    names = {c.name for c in session._http().cookies.jar}
    assert names == {"JSESSIONID7900"}
    assert COOKIE_PW not in (fake.requests[2].headers.get("cookie") or "")
    # 두 번째 조회는 같은 세션(로그인 없음)
    await session.fetch_list(VM_SID)
    assert fake.calls.count(("POST", "/login/page")) == 1
    await session.aclose()


@pytest.mark.parametrize("mode", ["redirect_once", "html_once"])
async def test_expired_session_relogins_once(mode):
    fake = FakeWeb()
    session = _session(fake)
    await session.fetch_list(POD_SID)
    fake.list_mode = mode
    rows = await session.fetch_list(POD_SID)
    assert len(rows) == 2
    assert fake.calls.count(("POST", "/login/page")) == 2
    await session.aclose()


async def test_html_without_content_type_is_expired():
    fake = FakeWeb()
    session = _session(fake)
    await session.fetch_list(POD_SID)
    fake.list_mode = "html"
    with pytest.raises(WebRequestError):
        await session.fetch_list(POD_SID)
    assert fake.calls.count(("POST", "/login/page")) == 2  # 재로그인 1회뿐


async def test_still_expired_after_relogin_is_error_status():
    fake = FakeWeb()
    fake.list_mode = "always_expired"
    clock = Clock()
    service = _service(fake, clock)
    result = await service.lookup("b0", POD_SID, 1001)
    assert result.status == "error" and result.host_address == ""
    assert fake.calls.count(("POST", "/login/page")) == 2


# ── 응답 허용 칸 · 형태 ─────────────────────────────────────


def test_parser_keeps_only_allowed_fields_and_validates_ipv4():
    data = [_row(1, 1, "10.139.0.5", "10.136.1.7", "p")]
    data.append({**_row(2, 1, "010.1.1.1", "10.1.1.300", "q")})
    data.append({**_row(3, 1, "fe80::1", " 10.1.1.1", "r")})
    rows = web_fields.parse_upgrade_list(data)
    assert set(rows[0]) == set(web_fields.KEEP_FIELDS.values())
    assert JMX_PW not in repr(rows)
    assert (rows[1]["ip"], rows[1]["host_address"]) == ("", "")
    assert (rows[2]["ip"], rows[2]["host_address"]) == ("", "")
    assert web_fields.is_pod_row(rows[0]) is True and web_fields.is_pod_row(rows[1]) is None


@pytest.mark.parametrize(
    "data", [{"result": []}, "x", [1], [{"sid": 1, "hostAddress": "10.0.0.1"}], [{"instId": None}]]
)
def test_parser_shape_errors(data):
    with pytest.raises(web_fields.WebShapeError):
        web_fields.parse_upgrade_list(data)


def test_domain_kind():
    parse = web_fields.parse_upgrade_list
    assert web_fields.domain_kind(parse(ROWS[POD_SID])) == "pod"
    assert web_fields.domain_kind(parse(ROWS[VM_SID])) == "vm"
    assert web_fields.domain_kind(parse(ROWS[MIXED_SID])) == "mixed"
    assert web_fields.domain_kind([]) == ""


async def test_shape_error_is_error_and_cache_unchanged(caplog):
    fake = FakeWeb()
    clock = Clock()
    service = _service(fake, clock)
    first = await service.lookup("b0", POD_SID, 1001)
    assert first.status == "ok"
    cached_at = service._cache[("b0", POD_SID)].fetched_at
    fake.list_mode = "dict"
    clock.now += 61
    result = await service.lookup("b0", POD_SID, 1001)
    # 형태 오류 = 실패 → TTL 안 캐시가 있어 stale(캐시는 갱신되지 않았다)
    assert result.status == "stale" and result.host_address == "10.136.1.7"
    assert service._cache[("b0", POD_SID)].fetched_at == cached_at
    clock.now += 900
    assert (await service.lookup("b0", POD_SID, 1001)).status == "error"
    assert "형태 오류" in caplog.text


# ── 비밀 값 · JMX 칸 0 ──────────────────────────────────────


async def test_no_password_cookie_or_jmx_in_logs_or_outputs(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeWeb()
    clock = Clock()
    service = _service(fake, clock)
    ok = await service.lookup("b0", POD_SID, 1001)
    fake.password = "rotated"
    fake.valid.clear()
    clock.now += 61
    failed = await service.lookup("b0", POD_SID, 1001)
    assert failed.status == "stale"
    text = caplog.text + repr(ok) + repr(failed) + repr(service._sessions["b0"])
    text += repr(service._cache) + repr(service.status())
    for secret in (PASSWORD, COOKIE_ID, COOKIE_PW, JMX_PW, "jmx", "S1", "S2"):
        assert secret not in text, secret
    addresses = await service.domain_addresses("b0", POD_SID, fetch=False)
    assert JMX_PW not in repr(addresses)


# ── 로그인 차단기 ───────────────────────────────────────────


async def test_three_login_failures_block_for_30_minutes(caplog):
    fake = FakeWeb(password="other")
    clock = Clock()
    service = _service(fake, clock)
    statuses = []
    for _ in range(3):
        statuses.append((await service.lookup("b0", POD_SID, 1001)).status)
    assert statuses == ["login_failed"] * 3
    before = len(fake.calls)
    for _ in range(2):
        assert (await service.lookup("b0", POD_SID, 1001)).status == "login_blocked"
    assert len(fake.calls) == before  # 정지 중 네트워크 호출 0
    assert "비밀번호 변경·초기화 여부 확인" in caplog.text
    assert service.status()["sources"]["b0"]["blocked"] is True
    clock.now += 30 * 60 + 1
    fake.password = PASSWORD
    result = await service.lookup("b0", POD_SID, 1001)
    assert result.status == "ok" and len(fake.calls) > before


async def test_success_resets_failure_count():
    fake = FakeWeb(password="other")
    clock = Clock()
    service = _service(fake, clock)
    for _ in range(2):
        await service.lookup("b0", POD_SID, 1001)
    fake.password = PASSWORD
    assert (await service.lookup("b0", POD_SID, 1001)).status == "ok"
    fake.password = "other"
    fake.valid.clear()
    for _ in range(2):
        clock.now += 61
        await service.lookup("b0", POD_SID, 1001)
    assert service._state("b0").breaker.blocked_until == 0.0


# ── 허용목록 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "params", "form"),
    [
        ("GET", "/agent/upgrade/run", None, None),
        ("POST", LIST_PATH, {"sid": 1}, None),
        ("PUT", "/login/page", None, None),
        ("POST", "/login/duplicate", None, {"id": "x"}),
        ("GET", "/agent/upgrade/../upgrade/list", {"sid": 1}, None),
        ("GET", "//agent/upgrade/list", {"sid": 1}, None),
        ("GET", "/agent/upgrade/list%2F", {"sid": 1}, None),
        ("GET", "/agent\\upgrade\\list", {"sid": 1}, None),
        ("GET", LIST_PATH, {"sid": "1;drop"}, None),
        ("GET", LIST_PATH, {"sid": True}, None),
        ("GET", LIST_PATH, {"sid": 1, "x": 2}, None),
        ("GET", LIST_PATH, {}, None),
        ("GET", "/login", {"next": "/"}, None),
        ("GET", "/login", None, {"a": "b"}),
        (
            "POST",
            "/login/page",
            None,
            {"id": "u", "password": "p", "autoLogin": "true", "redirect": ""},
        ),
        (
            "POST",
            "/login/page",
            None,
            {"id": "u", "password": "p", "autoLogin": "false", "redirect": "/x"},
        ),
        ("POST", "/login/page", None, {"id": "u", "password": "p"}),
    ],
)
async def test_allowlist_rejects_before_network(method, path, params, form):
    with pytest.raises(WebNotAllowedError):
        check_web_request(method, path, params, form)
    fake = FakeWeb()
    session = _session(fake)
    with pytest.raises(WebNotAllowedError):
        await session._send(method, path, params=params, form=form)
    assert fake.calls == []


async def test_non_integer_sid_lookup_makes_no_call():
    fake = FakeWeb()
    service = _service(fake, Clock())
    assert (await service.lookup("b0", "abc", 1001)).status == "not_found"
    with pytest.raises(WebNotAllowedError):
        await service._sessions["b0"].fetch_list("1 OR 1")  # type: ignore[arg-type]
    assert fake.calls == []


# ── 캐시 · 재조회 · stale · 도메인 종류 ─────────────────────


async def test_refetch_after_min_interval_and_cache_within():
    fake = FakeWeb()
    clock = Clock()
    service = _service(fake, clock)
    await service.lookup("b0", POD_SID, 1001)
    clock.now += 30
    await service.lookup("b0", POD_SID, 1002)
    assert _lists(fake) == 1
    clock.now += 31
    await service.lookup("b0", POD_SID, 1001)
    assert _lists(fake) == 2


async def test_concurrent_same_domain_fetches_once():
    fake = FakeWeb()
    service = _service(fake, Clock())
    results = await asyncio.gather(*(service.lookup("b0", POD_SID, 1001) for _ in range(5)))
    assert {r.status for r in results} == {"ok"} and _lists(fake) == 1


async def test_failure_with_cache_is_stale_then_error_after_ttl():
    fake = FakeWeb()
    clock = Clock()
    service = _service(fake, clock)
    assert (await service.lookup("b0", POD_SID, 1001)).payload_fields() == {
        "host_address": "10.136.1.7",
        "host_address_status": "ok",
        "pod": True,
    }
    fake.list_mode = "500"
    clock.now += 61
    stale = await service.lookup("b0", POD_SID, 1001)
    assert (stale.status, stale.host_address, stale.pod) == ("stale", "10.136.1.7", True)
    clock.now += 900
    gone = await service.lookup("b0", POD_SID, 1001)
    # TTL 밖 — 값 없음 · 기억한 도메인 종류가 pod라 pod=true
    assert gone.payload_fields() == {
        "host_address": "",
        "host_address_status": "error",
        "pod": True,
    }


@pytest.mark.parametrize(("sid", "inst"), [(VM_SID, 2001), (MIXED_SID, 3001)])
async def test_failure_without_cache_pod_key_only_for_pod_domain(sid, inst):
    fake = FakeWeb()
    clock = Clock()
    service = _service(fake, clock)
    ok = await service.lookup("b0", sid, inst)
    assert ok.status == "ok"
    fake.list_mode = "500"
    clock.now += 2000
    failed = await service.lookup("b0", sid, inst)
    assert failed.payload_fields() == {"host_address": "", "host_address_status": "error"}


async def test_vm_row_pod_false_and_unknown_domain_no_pod_key():
    fake = FakeWeb()
    service = _service(fake, Clock())
    vm = await service.lookup("b0", VM_SID, 2001)
    assert vm.payload_fields() == {
        "host_address": "10.20.0.1",
        "host_address_status": "ok",
        "pod": False,
    }
    fake.list_mode = "500"
    never = await service.lookup("b0", 55555, 1)
    assert never.payload_fields() == {"host_address": "", "host_address_status": "error"}


async def test_not_found_and_pod_domain_memory():
    fake = FakeWeb()
    service = _service(fake, Clock())
    missing = await service.lookup("b0", POD_SID, 9999)
    assert missing.payload_fields() == {
        "host_address": "",
        "host_address_status": "not_found",
        "pod": True,
    }
    vm_missing = await service.lookup("b0", VM_SID, 9999)
    assert vm_missing.payload_fields() == {"host_address": "", "host_address_status": "not_found"}


async def test_disabled_without_account_makes_no_call():
    fake = FakeWeb()
    service = NodeAddressService(
        {"b0": _session(fake, password=""), "cm": _session(fake, user="")}, clock=Clock()
    )
    for sid in ("b0", "cm", "unknown"):
        result = await service.lookup(sid, POD_SID, 1001)
        assert result.payload_fields() == {"host_address": "", "host_address_status": "disabled"}
    assert fake.calls == []


async def test_lookup_fields_timeout_is_error(monkeypatch):
    class Slow:
        source_id = "b0"
        configured = True

        async def fetch_list(self, sid):
            await asyncio.sleep(5)
            return []

        async def aclose(self):
            return None

    service = NodeAddressService({"b0": Slow()}, clock=Clock())
    monkeypatch.setattr(na, "LOOKUP_TIMEOUT_SECONDS", 0.05)
    fields = await na.lookup_fields(service, "b0", POD_SID, 1001)
    assert fields == {"host_address": "", "host_address_status": "error"}


# ── TLS · 설정 · 설정 관리 표 ───────────────────────────────


async def test_tls_verify_off_disables_verification_and_warns_once(caplog):
    session = WebSession(source_id="b0", url="https://web.test:7901", user=USER, password=PASSWORD,
                         tls_verify=False)
    client = session._http()
    ctx = client._transport._pool._ssl_context  # type: ignore[attr-defined]
    assert ctx.verify_mode == ssl.CERT_NONE
    warnings = [r for r in caplog.records if "TLS 인증서 검증 off" in r.getMessage()]
    assert len(warnings) == 1
    await session.aclose()
    strict = WebSession(source_id="cm", url="https://web.test", user=USER, password=PASSWORD)
    strict_ctx = strict._http()._transport._pool._ssl_context  # type: ignore[attr-defined]
    assert strict_ctx.verify_mode == ssl.CERT_REQUIRED
    await strict.aclose()


def _multi(**extra: str) -> dict[str, str]:
    return {
        "JENNIFER_SOURCES": '["b0", "cm_prd"]',
        "JENNIFER_B0_API_URL": "http://api.b0.test:7900",
        "JENNIFER_B0_API_TOKEN": "tok-b0",
        "JENNIFER_CM_PRD_API_URL": "http://api.cm.test:7900",
        "JENNIFER_CM_PRD_API_TOKEN": "tok-cm",
        **extra,
    }


def test_config_web_keys_defaults_and_no_global_fallback():
    cfg = load_config(
        _multi(
            JENNIFER_B0_WEB_URL="https://web.b0.test:7901",
            JENNIFER_B0_WEB_USER="viewer",
            JENNIFER_B0_WEB_PASSWORD=PASSWORD,
            JENNIFER_B0_WEB_TLS_VERIFY="false",
            JENNIFER_WEB_USER="global-should-not-apply",
        )
    )
    b0, cm = cfg.sources
    assert (b0.web_url, b0.web_user, b0.web_password, b0.web_tls_verify) == (
        "https://web.b0.test:7901",
        "viewer",
        PASSWORD,
        False,
    )
    assert (cm.web_url, cm.web_user, cm.web_password, cm.web_tls_verify) == (
        "http://api.cm.test:7900",
        "",
        "",
        True,
    )
    node = load_config({}).node_address
    assert (node.enabled, node.ttl_seconds, node.min_refetch_seconds) == (False, 900, 60)
    assert build_node_address(cfg) is None  # 플래그 off = 조회기 없음
    on = load_config(_multi(JENNIFER_NODE_ADDRESS_ENABLED="true"))
    service = build_node_address(on)
    assert service is not None and set(service._sessions) == {"b0", "cm_prd"}
    text = str(describe(cfg))
    assert PASSWORD not in text and "'web_account_set': True" in text


def test_config_admin_web_password_is_secret():
    cfg = load_config(
        _multi(JENNIFER_B0_WEB_USER="viewer", JENNIFER_B0_WEB_PASSWORD=PASSWORD), dotenv=None
    )
    specs, values, _ = build_fields(cfg)
    kinds = {s.env_key: (s.type, s.is_secret) for s in specs}
    assert kinds["JENNIFER_B0_WEB_PASSWORD"] == ("secret", True)
    assert kinds["JENNIFER_B0_WEB_USER"] == ("string", False)
    assert kinds["JENNIFER_B0_WEB_URL"] == ("string", False)
    assert kinds["JENNIFER_B0_WEB_TLS_VERIFY"] == ("bool", False)
    for key in (
        "JENNIFER_NODE_ADDRESS_ENABLED",
        "JENNIFER_NODE_ADDRESS_TTL_SECONDS",
        "JENNIFER_NODE_ADDRESS_MIN_REFETCH_SECONDS",
    ):
        assert key in kinds
    assert values["JENNIFER_B0_WEB_PASSWORD"] is True
    assert PASSWORD not in repr(values)


# ── 교정 라운드(verifier·security-auditor 지적) ─────────────


def _login_reply(location: str | None, status: int = 302):
    """로그인 POST 응답만 바꾸는 가짜 서버."""
    inner = FakeWeb()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/login/page":
            inner(request)
            headers = {"location": location} if location else {}
            return httpx.Response(status, headers=headers, text="x")
        return inner(request)

    return inner, handler


@pytest.mark.parametrize(
    "location", ["/;jsessionid=AbC123", WEB + "/", WEB + "/;JSESSIONID=x", "/"]
)
async def test_login_success_only_root_after_normalization(location):
    inner, handler = _login_reply(location)
    session = WebSession(
        source_id="b0", url=WEB, user=USER, password=PASSWORD,
        transport=httpx.MockTransport(handler),
    )
    await session._login()
    assert session._logged_in is True
    await session.aclose()


@pytest.mark.parametrize(
    ("location", "status", "code"),
    [
        ("/LOGIN;jsessionid=SECRETSESSION", 302, "login_failed"),
        ("/login/?error=1", 302, "login_failed"),
        (None, 200, "login_failed"),
        ("/main", 302, "error"),
        ("http://evil.test/", 302, "error"),
        (None, 500, "error"),
    ],
)
async def test_login_outcome_classification_and_no_location_in_logs(
    location, status, code, caplog
):
    caplog.set_level(logging.DEBUG)
    inner, handler = _login_reply(location, status)
    session = WebSession(
        source_id="b0", url=WEB, user=USER, password=PASSWORD,
        transport=httpx.MockTransport(handler),
    )
    service = NodeAddressService({"b0": session}, clock=Clock())
    result = await service.lookup("b0", POD_SID, 1001)
    assert result.status == code
    breaker = service._state("b0").breaker
    assert breaker.failures == (1 if code == "login_failed" else 0)
    assert "SECRETSESSION" not in caplog.text and "evil.test" not in caplog.text
    await session.aclose()


async def test_non_transport_error_backs_off_only_that_domain():
    fake = FakeWeb()
    clock = Clock()
    service = _service(fake, clock)
    fake.list_mode = "500"
    assert (await service.lookup("b0", POD_SID, 1001)).status == "error"
    lists = _lists(fake)
    assert (await service.lookup("b0", POD_SID, 1001)).status == "error"
    assert _lists(fake) == lists  # 같은 도메인 — 간격 안 네트워크 0
    fake.list_mode = "ok"
    assert (await service.lookup("b0", VM_SID, 2001)).status == "ok"  # 다른 도메인은 조회
    clock.now += 61
    assert (await service.lookup("b0", POD_SID, 1001)).status == "ok"


async def test_transport_failure_backs_off_whole_source():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("down")

    session = WebSession(
        source_id="b0", url=WEB, user=USER, password=PASSWORD,
        transport=httpx.MockTransport(handler),
    )
    clock = Clock()
    service = NodeAddressService({"b0": session}, clock=clock)
    assert (await service.lookup("b0", POD_SID, 1001)).status == "error"
    assert (await service.lookup("b0", VM_SID, 2001)).status == "error"
    assert calls["n"] == 1
    clock.now += 61
    await service.lookup("b0", VM_SID, 2001)
    assert calls["n"] == 2
    await session.aclose()


async def test_rows_with_other_sid_are_dropped():
    fake = FakeWeb()
    fake.rows[POD_SID] = fake.rows[POD_SID] + [_row(4242, VM_SID, "10.1.1.1", "10.1.1.2", "x")]
    rows = await _session(fake).fetch_list(POD_SID)
    assert [r["inst_id"] for r in rows] == [1001, 1002]


async def test_login_page_body_over_cap_is_error(monkeypatch):
    from apm_gateway.adapters.jennifer import web_session as ws

    monkeypatch.setattr(ws, "MAX_PAGE_BYTES", 100)
    inner = FakeWeb()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login" and request.method == "GET":
            inner(request)
            return httpx.Response(200, content=b"<html>" + b"a" * 500)
        return inner(request)

    session = WebSession(
        source_id="b0", url=WEB, user=USER, password=PASSWORD,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(WebRequestError):
        await session._login()
    assert ("POST", "/login/page") not in inner.calls
    await session.aclose()


@pytest.mark.parametrize(
    "url", ["ftp://web.test", "http://user:pw-in-url@web.test:7900", "web.test:7900", "http://"]
)
def test_bad_web_url_disables_source_without_value_in_log(url, caplog):
    session = WebSession(source_id="b0", url=url, user=USER, password=PASSWORD)
    assert session.configured is False
    assert "pw-in-url" not in caplog.text and "형식 밖" in caplog.text


def test_tls_verify_strict_parse_fail_closed(caplog):
    base = {
        "JENNIFER_SOURCES": '["b0"]',
        "JENNIFER_B0_API_URL": "http://api.b0.test",
        "JENNIFER_B0_API_TOKEN": "tok",
    }
    for value, expected in [("false", False), ("OFF", False), ("0", False), ("no", False),
                            ("true", True), ("ture", True), ("Y", True), ("", True)]:
        cfg = load_config({**base, "JENNIFER_B0_WEB_TLS_VERIFY": value})
        assert cfg.sources[0].web_tls_verify is expected, value
    assert "WEB_TLS_VERIFY 값을 인식하지 못했다" in caplog.text


def test_config_repr_hides_token_and_password():
    cfg = load_config(
        {
            "JENNIFER_SOURCES": '["b0"]',
            "JENNIFER_B0_API_URL": "http://api.b0.test",
            "JENNIFER_B0_API_TOKEN": "tok-REPR-1",
            "JENNIFER_B0_WEB_USER": USER,
            "JENNIFER_B0_WEB_PASSWORD": PASSWORD,
        }
    )
    text = repr(cfg)
    assert "tok-REPR-1" not in text and PASSWORD not in text
