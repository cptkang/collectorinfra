"""허용목록 정본 (plans/87 §5.2(e) · §6 [v3.2]·[v3.3] 수용 기준).

- §5.2(e) 거부 입력 23건(비GET 9 · 민감 GET 9 · 변형·우회 5) + v1 POST 변형 + 쿼리 `token` → **HTTP
  0회**로 거부.
- 카탈로그 사본(`testdata/jennifer/scripts/jennifer_catalog.py`) ↔ 정본 대조(템플릿·필수/선택
  키·Accept).
"""

from __future__ import annotations

import httpx
import pytest
from apm_gateway.adapters.jennifer.allowlist import (
    ALLOWED,
    NotAllowedError,
    build_path,
    check_request,
    match_template,
)
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.domain.errors import API_ERROR, ApmError

from apm_gateway.config import JenniferApiConfig

# §5.2(e) (a) 비GET 9
NON_GET = [
    ("POST", "/api-v2/manage/data-server/control"),
    ("POST", "/api-v2/manage/data-server/db/property/1000/copy"),
    ("POST", "/api-v2/manage/domain-group"),
    ("PUT", "/api-v2/manage/domain/put"),
    ("POST", "/restapi/user/"),
    ("PUT", "/api-v2/configuration/rdb-export-password-override"),
    ("PUT", "/api-v2/manage/rule/event/error/1000/ERROR_X/applied"),
    ("POST", "/api-v2/manual-rdb-export"),
    ("POST", "/api/domain"),  # v1 조회 API의 POST 변형
]
# (b) 민감 GET 9
SENSITIVE_GET = [
    "/api/auth/userlist",
    "/api/auth/userlist.xml",
    "/restapi/users",
    "/restapi/user/1",
    "/api-v2/environment-variable/1000",
    "/api-v2/active-service/detail/1000/1",
    "/api-v2/loaded-class/1000/1",
    "/api-v2/manage/data-server/system-property-config",
    "/api-v2/manage/rule/event/error/1000",
]
# (c) 변형·우회 — 경로 3건(쿼리 token·리다이렉트는 아래 별도 테스트)
VARIANTS = [
    "/api/domain.xml",
    "/api/realtime/instance.xml",
    "/api/transaction/time/../../auth/userlist",
]
EXTRA_VARIANTS = [
    "/api//domain",
    "/api/transaction/%2e%2e/x",
    "/api/domain/",
    "/api-v2/deploy/abc",
    "http://evil.example/api/domain",
    "/api/domain?token=x",
    "/API/DOMAIN",
]


class CountingTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"result": []})


def _client(transport: httpx.MockTransport) -> JenniferClient:
    return JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t0k", rate_limit_per_sec=0),
        transport=transport,
    )


@pytest.mark.parametrize("method,path", NON_GET)
def test_non_get_rejected(method, path):
    with pytest.raises(NotAllowedError):
        check_request(method, path.split("?")[0], {})


@pytest.mark.parametrize("path", SENSITIVE_GET + VARIANTS + EXTRA_VARIANTS)
def test_paths_outside_allowlist_rejected(path):
    assert match_template(path) is None
    with pytest.raises(NotAllowedError):
        check_request("GET", path, {})


def test_rejection_inputs_cover_plan_table():
    """§5.2(e) 거부 입력 표의 개수 — 비GET 9(v1 POST 변형 포함) · 민감 GET 9 · 변형 경로 3."""
    assert (len(NON_GET), len(SENSITIVE_GET), len(VARIANTS)) == (9, 9, 3)


@pytest.mark.parametrize("key", ["token", "TOKEN", "Token"])
def test_query_token_rejected_even_on_allowed_path(key):
    with pytest.raises(NotAllowedError):
        check_request("GET", "/api/domain", {key: "x"})


def test_unknown_query_key_and_missing_required_rejected():
    with pytest.raises(NotAllowedError):
        check_request("GET", "/api/instance", {"domain_id": 1, "evil": 1})
    with pytest.raises(NotAllowedError):
        check_request("GET", "/api/instance", {})
    with pytest.raises(NotAllowedError):
        check_request("GET", "/api/instance", {"domain_id": [1, 2]})


def test_allowed_paths_pass():
    assert check_request("GET", "/api/domain", {}).template == "/api/domain"
    assert check_request("GET", "/api-v2/deploy/1000", {"startTime": 1, "endTime": 2}).template == (
        "/api-v2/deploy/{domainId}"
    )
    assert check_request(
        "GET", "/api/transaction/profile.txt", {"domain_id": 1, "txid": 2, "time": 3}
    ).accept == ("text/plain")


def test_build_path_requires_numeric_vars():
    assert build_path("/api-v2/deploy/{domainId}", {"domainId": 1000}) == "/api-v2/deploy/1000"
    with pytest.raises(NotAllowedError):
        build_path("/api-v2/deploy/{domainId}", {"domainId": "../x"})
    with pytest.raises(NotAllowedError):
        build_path("/api-v2/deploy/{domainId}", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", SENSITIVE_GET + VARIANTS + ["/api-v2/manage/data-server/control"])
async def test_client_rejects_with_zero_http_calls(path):
    transport = CountingTransport()
    client = _client(transport)
    with pytest.raises(NotAllowedError):
        await client.get_json(path)
    with pytest.raises(NotAllowedError):
        await client.get_json("/api/domain", {"token": "x"})
    assert transport.requests == []
    assert client.calls_total == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_redirect_is_not_followed():
    """(c) 3xx 리다이렉트 — 대상(민감 경로)을 따라가지 않는다: HTTP 1회 · 오류."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(302, headers={"Location": "/api/auth/userlist"})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(ApmError) as exc:
        await client.get_json("/api/domain")
    assert exc.value.code == API_ERROR
    assert calls == ["/api/domain"]
    await client.aclose()


def test_catalog_copy_matches_canonical():
    """J0 도구용 사본과 정본이 같아야 한다 — 한쪽만 바뀌면 실패(§6 [v3.3] 수용 기준)."""
    from jennifer_catalog import ALLOWED as COPY

    assert set(COPY) == set(ALLOWED)
    for template, ep in ALLOWED.items():
        copy = COPY[template]
        assert tuple(copy.required) == ep.required, template
        assert tuple(copy.optional) == ep.optional, template
        assert copy.accept == ep.accept, template
        assert tuple(copy.path_vars) == ep.path_vars, template
