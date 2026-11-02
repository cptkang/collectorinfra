"""plans/202 독립 검증(verifier) — 웹 세션 노드 주소 조회기 보안·동시성·정책 경계.

가짜 웹 서버(`httpx.MockTransport`)·대역만 쓴다 — 실제 제니퍼·외부 네트워크 없음.
`xfail(strict=True)`는 검증에서 찾은 결함을 고정한다(고치면 XPASS → 표식 제거).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from apm_gateway.adapters.jennifer.web_session import (
    MAX_LIST_BYTES,
    WebNotAllowedError,
    WebSession,
    check_web_request,
)
from apm_gateway.application import node_address as na
from apm_gateway.application.node_address import NodeAddressService
from test_plan202_web_session import (
    COOKIE_ID,
    COOKIE_PW,
    JMX_PW,
    PASSWORD,
    POD_SID,
    USER,
    WEB,
    Clock,
    FakeWeb,
    _lists,
    _service,
    _session,
)


# ── 허용목록 우회 표기 ──────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "params", "form"),
    [
        ("GET", "/agent/upgrade/list/", {"sid": 1}, None),  # 끝 슬래시
        ("GET", "/Agent/upgrade/list", {"sid": 1}, None),  # 대소문자
        ("GET", "/AGENT/UPGRADE/LIST", {"sid": 1}, None),
        ("GET", "/agent/upgrade/list;jsessionid=x", {"sid": 1}, None),  # 경로 매개변수
        ("GET", "/agent/./upgrade/list", {"sid": 1}, None),
        ("GET", "/agent/upgrade/../upgrade/list", {"sid": 1}, None),
        ("GET", "/agent//upgrade/list", {"sid": 1}, None),
        ("GET", "/agent/upgrade/list%2F..", {"sid": 1}, None),
        ("GET", "/agent\\upgrade\\list", {"sid": 1}, None),
        ("GET", "/agent/upgrade/list?sid=1", None, None),  # 경로에 질의 끼워 넣기
        ("GET", "/agent/upgrade/list#x", {"sid": 1}, None),
        ("GET", " /agent/upgrade/list", {"sid": 1}, None),
        ("GET", "/agent/upgrade/list ", {"sid": 1}, None),
        ("GET", "http://evil.test/agent/upgrade/list", {"sid": 1}, None),  # 절대 URL
        ("GET", "//evil.test/agent/upgrade/list", {"sid": 1}, None),
        ("GET", "/agent/upgrade/list", {"sid": 1, "domainId": 2}, None),  # 질의 키 추가
        ("GET", "/agent/upgrade/list", {"SID": 1}, None),
        ("GET", "/agent/upgrade/list", {}, None),
        ("GET", "/agent/upgrade/list", {"sid": "１２"}, None),  # 전각 숫자
        ("GET", "/agent/upgrade/list", {"sid": "1 "}, None),
        ("GET", "/agent/upgrade/list", {"sid": True}, None),
        ("GET", "/agent/upgrade/list", {"sid": 1.0}, None),
        ("GET", "/agent/upgrade/list", {"sid": 1}, {"x": "y"}),  # GET 폼
        ("POST", "/agent/upgrade/list", {"sid": 1}, None),
        ("PUT", "/agent/upgrade/list", {"sid": 1}, None),
        ("HEAD", "/agent/upgrade/list", {"sid": 1}, None),
        ("DELETE", "/login", None, None),
        ("GET", "/login", {"redirect": "/x"}, None),
        ("GET", "/login/page", None, None),
        ("POST", "/login/duplicate", None, None),
        ("POST", "/agent/upgrade/execute", None, None),
        ("GET", "/agent/upgrade/run", {"sid": 1}, None),
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
            {"id": "u", "password": "p", "autoLogin": "false", "redirect": "http://evil"},
        ),
        (
            "POST",
            "/login/page",
            None,
            {"id": "u", "password": "p", "autoLogin": "false", "redirect": "", "x": "1"},
        ),
        ("POST", "/login/page", {"next": "/"}, None),
    ],
)
async def test_allowlist_bypass_notations_rejected_without_network(method, path, params, form):
    fake = FakeWeb()
    session = _session(fake)
    with pytest.raises(WebNotAllowedError):
        check_web_request(method, path, params, form)
    with pytest.raises(WebNotAllowedError):
        await session._send(method, path, params=params, form=form)
    assert fake.calls == []
    await session.aclose()


def test_lowercase_method_is_normalized_not_widened():
    check_web_request("get", "/agent/upgrade/list", {"sid": 7})
    with pytest.raises(WebNotAllowedError):
        check_web_request("get", "/agent/upgrade/list", {"sid": 7, "a": 1})


# ── 리다이렉트 · 다른 호스트로 자격증명 유출 ──────────────────


async def test_redirect_to_other_host_is_not_followed_and_credentials_stay_on_web_host():
    """로그인 성공 302가 다른 호스트를 가리키거나 목록이 외부로 302해도 따라가지 않는다."""
    hosts: list[str] = []
    inner = FakeWeb()

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host != "web.test":
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path == "/login/page":
            response = inner(request)
            if response.status_code == 302 and "error" not in response.headers["location"]:
                return httpx.Response(
                    302,
                    headers={"location": "http://evil.test/steal"},
                )
            return response
        if request.url.path == "/agent/upgrade/list":
            inner(request)
            return httpx.Response(302, headers={"location": "http://evil.test/login"})
        return inner(request)

    session = WebSession(
        source_id="b0", url=WEB, user=USER, password=PASSWORD,
        transport=httpx.MockTransport(handler),
    )
    service = NodeAddressService({"b0": session}, clock=Clock())
    result = await service.lookup("b0", POD_SID, 1001)
    assert result.status in {"error", "login_failed"}
    assert set(hosts) == {"web.test"}
    await session.aclose()


# ── 응답 크기 상한 ──────────────────────────────────────────


async def test_oversized_list_is_error_and_not_cached():
    big = b"[" + b" " * (MAX_LIST_BYTES + 10) + b"]"
    inner = FakeWeb()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/upgrade/list":
            inner(request)
            return httpx.Response(
                200, content=big, headers={"content-type": "application/json"}
            )
        return inner(request)

    session = WebSession(
        source_id="b0", url=WEB, user=USER, password=PASSWORD,
        transport=httpx.MockTransport(handler),
    )
    service = NodeAddressService({"b0": session}, clock=Clock())
    result = await service.lookup("b0", POD_SID, 1001)
    assert result.payload_fields() == {"host_address": "", "host_address_status": "error"}
    assert service.status()["cached_domains"] == 0
    await session.aclose()


# ── 쿠키 · 비밀번호 · jmx 비노출 ─────────────────────────────


async def test_only_session_cookie_kept_and_status_describe_have_no_secret(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeWeb()
    session = _session(fake)
    service = NodeAddressService({"b0": session}, clock=Clock())
    await service.lookup("b0", POD_SID, 1001)
    names = {c.name for c in session._http().cookies.jar}
    assert names and all(n.upper().startswith("JSESSIONID") for n in names)
    blob = repr(service.status()) + repr(session) + caplog.text
    for secret in (PASSWORD, COOKIE_ID, COOKIE_PW, JMX_PW, "jmx"):
        assert secret not in blob
    # 쿠키 jar 값에도 ID·PASSWORD 쿠키가 남지 않는다
    values = {c.value for c in session._http().cookies.jar}
    assert COOKIE_ID not in values and COOKIE_PW not in values
    await session.aclose()


async def test_login_failure_reason_and_logs_have_no_password(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeWeb(password="other")
    session = _session(fake)
    service = NodeAddressService({"b0": session}, clock=Clock())
    for _ in range(4):
        await service.lookup("b0", POD_SID, 1001)
    assert PASSWORD not in caplog.text and USER not in caplog.text
    # 3회 실패 뒤 4번째는 네트워크 호출 0 — POST 3회뿐
    assert sum(1 for c in fake.calls if c == ("POST", "/login/page")) == 3
    await session.aclose()


# ── 동시성 · 취소 ───────────────────────────────────────────


async def test_wait_for_cancel_mid_request_releases_locks_and_next_lookup_works():
    inner = FakeWeb()
    gate = {"slow": True}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/upgrade/list" and gate["slow"]:
            gate["slow"] = False
            await asyncio.sleep(2)
        return inner(request)

    session = WebSession(
        source_id="b0", url=WEB, user=USER, password=PASSWORD,
        transport=httpx.MockTransport(handler),
    )
    clock = Clock()
    service = NodeAddressService({"b0": session}, clock=clock)
    first = await na.lookup_fields(service, "b0", POD_SID, 1001, timeout_seconds=0.1)
    assert first == {"host_address": "", "host_address_status": "error"}
    assert not session._lock.locked()
    assert not service._state("b0").lock.locked()
    clock.now += 61  # 취소는 소스 실패 간격 대상 — 간격이 지난 뒤 정상 재조회를 본다
    second = await na.lookup_fields(service, "b0", POD_SID, 1001, timeout_seconds=2)
    assert second == {"host_address": "10.136.1.7", "host_address_status": "ok", "pod": True}
    await session.aclose()


async def test_cancel_during_login_post_then_relogin_clean():
    inner = FakeWeb()
    gate = {"slow": True}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/page" and gate["slow"]:
            gate["slow"] = False
            await asyncio.sleep(2)
        return inner(request)

    session = WebSession(
        source_id="b0", url=WEB, user=USER, password=PASSWORD,
        transport=httpx.MockTransport(handler),
    )
    clock = Clock()
    service = NodeAddressService({"b0": session}, clock=clock)
    await na.lookup_fields(service, "b0", POD_SID, 1001, timeout_seconds=0.1)
    assert session._logged_in is False
    # 취소된 로그인은 차단기 실패로 세지 않는다
    assert service._state("b0").breaker.failures == 0
    clock.now += 61  # 취소는 소스 실패 간격 대상 — 간격이 지난 뒤 깨끗한 재로그인을 본다
    ok = await na.lookup_fields(service, "b0", POD_SID, 1001, timeout_seconds=2)
    assert ok["host_address_status"] == "ok"
    await session.aclose()


# ── 실패 시 재조회 간격(결함 고정) ───────────────────────────


class _Failing:
    source_id = "b0"
    configured = True

    def __init__(self, delay: float = 0.0) -> None:
        self.calls = 0
        self.delay = delay

    async def fetch_list(self, sid: int):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        raise na.WebSessionError("가짜 전송 실패")

    async def aclose(self) -> None:
        return None


async def test_failed_fetch_is_not_retried_within_min_refetch():
    lister = _Failing()
    clock = Clock()
    service = NodeAddressService({"b0": lister}, clock=clock)
    for _ in range(5):
        await service.lookup("b0", POD_SID, 1001)
        clock.now += 1
    assert lister.calls == 1


async def test_event_storm_with_hung_web_does_not_stall_serially():
    lister = _Failing(delay=10)
    service = NodeAddressService({"b0": lister}, clock=Clock())
    loop = asyncio.get_running_loop()
    start = loop.time()
    for _ in range(5):  # 폴러 `_publish`처럼 이벤트마다 직렬
        await na.lookup_fields(service, "b0", POD_SID, 1001, timeout_seconds=0.2)
    # 첫 1건만 상한까지 기다리고 나머지는 즉시(실패 간격 안) 돌아와야 한다
    assert loop.time() - start < 0.5


# ── 캐시 안 instId 미존재(이탈 판정 근거) ────────────────────


async def test_new_instance_within_min_refetch_is_not_found_without_refetch():
    """60초 안 캐시에 없는 instId(재기동한 새 파드)는 하한(10초) 안이면 재조회 없이 not_found,
    하한이 지나면 1회 재조회한다(교정 라운드 4 · `NOT_FOUND_REFETCH_SECONDS`)."""
    fake = FakeWeb()
    clock = Clock()
    service = _service(fake, clock)
    await service.lookup("b0", POD_SID, 1001)
    fake.rows[POD_SID].append(
        {**fake.rows[POD_SID][0], "instId": 1003, "ip": "10.139.0.7", "hostAddress": "10.136.1.9"}
    )
    clock.now += 5
    early = await service.lookup("b0", POD_SID, 1003)
    assert early.payload_fields() == {
        "host_address": "",
        "host_address_status": "not_found",
        "pod": True,
    }
    assert _lists(fake) == 1
    clock.now += na.NOT_FOUND_REFETCH_SECONDS
    new = await service.lookup("b0", POD_SID, 1003)
    assert new.payload_fields() == {
        "host_address": "10.136.1.9",
        "host_address_status": "ok",
        "pod": True,
    }
    assert _lists(fake) == 2
    # 다시 받은 직후 하한 안에서는 또 부르지 않는다
    await service.lookup("b0", POD_SID, 7777)
    assert _lists(fake) == 2


# ── 설정: TLS verify 오타 ───────────────────────────────────


def test_tls_verify_typo_disables_verification():
    """`_bool`은 관대하다(오타 → false). `WEB_TLS_VERIFY`는 `_strict_bool` — 모르는 값은 검증 켬."""
    from apm_gateway.config import _bool

    assert _bool("ture", True) is False
    assert _bool("Y", True) is False
