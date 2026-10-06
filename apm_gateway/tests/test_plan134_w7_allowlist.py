"""plans/134 W7 허용목록 — 관리·민감 조회 GET 19템플릿 · 경로 변수 형식 · 거부 유지 · C-10 반전
(D-296 ①② · SPEC-apm-question-coverage §0 ①·§2.4 · COV E-01·E-15·E-17).

- 허용: 19템플릿(GET만 · `compare`·`comparing` 두 표기 포함 — COV E-01 재질의 대상) · 형식
  `account`·`int`·`sint`(음수 txid)·`token`·`enum:`.
- 거부(HTTP 0회): 같은 경로의 PUT·POST·DELETE · 쿼리 `token` · 허용 밖 쿼리 키 · 필수 키 누락 ·
  `.xml` 변형 · 시험 경로 · 형식 밖 경로 변수(`..`·`%`·소문자 errorType·영문 txid·compare의
  business…).
- C-10 반전: 민감 GET이 목 서버로 **실제로 나가고**, 단언한 쿼리 키·값만 실린다(`/__mock/hits`) ·
  허용목록 밖 0건 · 쿼리 `token` 0건.
"""

from __future__ import annotations

import json
import urllib.request

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
from apm_gateway.application.manage_tools import ManageTools
from conftest import DOMAIN, make_tools
from mock_openapi import W7_TEMPLATES, w7_state

from apm_gateway.config import JenniferApiConfig

# (템플릿, 경로 변수, 쿼리) — 허용되는 대표 요청
W7_REQUESTS = [
    ("/api/auth/userlist", {}, {}),
    ("/restapi/users", {}, {}),
    ("/restapi/user/{id}", {"id": "kim.cs-01@corp_x"}, {}),
    ("/api-v2/manage/data-server/domains", {}, {}),
    ("/api-v2/manage/data-server/resource", {}, {}),
    ("/api-v2/manage/data-server/system-property-config", {}, {}),
    ("/api-v2/manage/rule/active-service-color-range-boundary", {}, {}),
    (
        "/api-v2/active-service/detail/{domainId}/{txid}",
        {"domainId": 1000, "txid": "-9223372036854775808"},
        {"sessionId": "-1", "threadHash": "77"},
    ),
    ("/api-v2/manage/db/path/{domainId}", {"domainId": 1000}, {}),
    ("/api-v2/environment-variable/{domainId}", {"domainId": 1000}, {}),
    ("/api-v2/manage/instance", {}, {"processId": "4242", "hostname": "was-host01"}),
    (
        "/api-v2/loaded-class/{domainId}/{instanceId}",
        {"domainId": 1000, "instanceId": 1001},
        {"search": "Order"},
    ),
    ("/api-v2/manage/rule/event/error/{domainId}", {"domainId": 1000}, {}),
    (
        "/api-v2/manage/rule/event/metric/{domainId}/{targetType}",
        {"domainId": 1000, "targetType": "business"},
        {},
    ),
    (
        "/api-v2/manage/rule/event/compare/{domainId}/{targetType}",
        {"domainId": 1000, "targetType": "instance"},
        {},
    ),
    (
        "/api-v2/manage/rule/event/comparing/{domainId}/{targetType}",
        {"domainId": 1000, "targetType": "domain"},
        {},
    ),
    (
        "/api-v2/manage/rule/event/error/{domainId}/{errorType}/applied",
        {"domainId": 1000, "errorType": "AGENT_STOP"},
        {},
    ),
    (
        "/api-v2/manage/rule/event/error/{domainId}/{errorType}/individual-setting/{instanceId}",
        {"domainId": 1000, "errorType": "ERROR_X", "instanceId": 1001},
        {},
    ),
    ("/api-v2/manual-rdb-export", {}, {}),
]
W7_ALLOWED = [t for t, _, _ in W7_REQUESTS]


class CountingTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=[])


def _client(transport: httpx.MockTransport) -> JenniferClient:
    return JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t0k", rate_limit_per_sec=0),
        transport=transport,
    )


def test_w7_templates_are_the_19_tail_entries_and_mock_covers_them():
    """W7 19템플릿은 `ALLOWED` 끝에 있다(16 + 19 = 35 — gw-w5w6의 거래 경로와 병합 시 합산)."""
    assert list(ALLOWED)[-19:] == W7_ALLOWED
    assert len(ALLOWED) == 36  # 16 + W5 guid 1 + W7 19
    assert set(W7_ALLOWED) == set(W7_TEMPLATES)  # 목 서버 합성 응답이 전부 덮는다


@pytest.mark.parametrize(("template", "path_vars", "params"), W7_REQUESTS)
def test_w7_get_is_allowed_with_declared_formats(template, path_vars, params):
    path = build_path(template, path_vars)
    assert match_template(path) == template
    assert check_request("GET", path, params).template == template
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        with pytest.raises(NotAllowedError):
            check_request(method, path, params)


@pytest.mark.parametrize(
    ("template", "path_vars"),
    [
        ("/api-v2/active-service/detail/{domainId}/{txid}", {"domainId": 1, "txid": "12a"}),
        ("/api-v2/active-service/detail/{domainId}/{txid}", {"domainId": 1, "txid": "1" * 21}),
        ("/api-v2/active-service/detail/{domainId}/{txid}", {"domainId": 1, "txid": "--1"}),
        ("/api-v2/active-service/detail/{domainId}/{txid}", {"domainId": -1, "txid": "1"}),
        ("/api-v2/manage/rule/event/compare/{domainId}/{targetType}",
         {"domainId": 1, "targetType": "business"}),
        ("/api-v2/manage/rule/event/metric/{domainId}/{targetType}",
         {"domainId": 1, "targetType": "DOMAIN"}),
        ("/api-v2/manage/rule/event/error/{domainId}/{errorType}/applied",
         {"domainId": 1, "errorType": "error_x"}),
        ("/api-v2/manage/rule/event/error/{domainId}/{errorType}/applied",
         {"domainId": 1, "errorType": "A" * 65}),
        ("/api-v2/manage/rule/event/error/{domainId}/{errorType}/applied",
         {"domainId": 1, "errorType": "../X"}),
        ("/restapi/user/{id}", {"id": "a b"}),
        ("/restapi/user/{id}", {"id": "a/b"}),
        ("/restapi/user/{id}", {"id": "%2e%2e"}),
        ("/restapi/user/{id}", {"id": "x" * 65}),
        ("/restapi/user/{id}", {"id": "김"}),
        ("/api-v2/loaded-class/{domainId}/{instanceId}", {"domainId": 1, "instanceId": "1.5"}),
        ("/api-v2/environment-variable/{domainId}", {"domainId": "abc"}),
    ],
)
def test_out_of_format_path_vars_are_rejected(template, path_vars):
    with pytest.raises(NotAllowedError):
        build_path(template, path_vars)


@pytest.mark.parametrize(
    ("method", "path", "params"),
    [
        ("GET", "/restapi/user/..", {}),
        ("GET", "/restapi/user/.", {}),
        ("GET", "/restapi/user/canary.xml", {}),
        ("GET", "/restapi/user/canary.XML", {}),
        ("GET", "/api/auth/userlist.xml", {}),
        ("GET", "/restapi/users.xml", {}),
        ("GET", "/api-v2/test-response/json", {}),
        ("GET", "/api-v2/auth-test", {}),
        ("GET", "/api/auth/userlist", {"token": "x"}),
        ("GET", "/api-v2/environment-variable/1000", {"TOKEN": "x"}),
        ("GET", "/api/auth/userlist", {"password": "x"}),
        ("GET", "/api-v2/manage/instance", {}),  # 필수 processId 누락
        ("GET", "/api-v2/manage/instance", {"processId": "1", "evil": "1"}),
        ("GET", "/api-v2/manual-rdb-export", {"date": "2026-01-01"}),
        ("GET", "/api-v2/loaded-class/1000/1001", {"search": ["a", "b"]}),
        ("GET", "/api-v2/manage/rule/event/error/1000/X/applied/", {}),
        ("GET", "/api-v2/manage/rule/event/error/1000/X/individual-setting", {}),
        ("GET", "/api-v2/manage/rule/event/compar/1000/domain", {}),
        ("PUT", "/api-v2/manage/rule/event/error/1000/ERROR_X/applied", {}),
        ("PUT", "/api-v2/manage/rule/event/error/1000/ERROR_X/individual-setting/1001", {}),
        ("DELETE", "/api-v2/manage/rule/event/error/1000/ERROR_X/individual-setting/1001", {}),
        ("POST", "/api-v2/manual-rdb-export", {"date": "2026-01-01"}),
        ("POST", "/restapi/user/", {}),
        ("PUT", "/restapi/user/canary", {}),
        ("DELETE", "/restapi/user/canary", {}),
        ("POST", "/api/auth/userlist", {}),
    ],
)
def test_w7_rejections_stay(method, path, params):
    with pytest.raises(NotAllowedError):
        check_request(method, path, params)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/restapi/user/canary.xml",
        "/api/auth/userlist.xml",
        "/api-v2/test-response/json",
        "/api-v2/auth-test",
        "/api-v2/manage/rule/event/compare/1000/business",
        "/api-v2/manage/rule/event/error/1000/error_x/applied",
        "/api-v2/active-service/detail/1000/abc",
        "/restapi/user/..",
    ],
)
async def test_client_rejects_w7_variants_with_zero_http_calls(path):
    transport = CountingTransport()
    client = _client(transport)
    with pytest.raises(NotAllowedError):
        await client.get_json(path)
    with pytest.raises(NotAllowedError):
        await client.get_json("/api/auth/userlist", {"token": "x"})
    with pytest.raises(NotAllowedError):
        await client.get_json("/restapi/user/{id}", path_vars={"id": "../users"})
    assert transport.requests == [] and client.calls_total == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_redirect_on_sensitive_get_is_not_followed():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(302, headers={"Location": "/api-v2/manage/data-server/control"})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc:
        await client.get_json("/restapi/users")
    assert getattr(exc.value, "code", None) == "apm_api_error"
    assert calls == ["/restapi/users"]
    await client.aclose()


# ── C-10 반전 — 목 서버로 실제로 나간다 ─────────────────────────


def _hits(base: str) -> list[dict]:
    with urllib.request.urlopen(f"{base}/__mock/hits", timeout=5) as r:
        return json.loads(r.read())


@pytest.mark.asyncio
async def test_sensitive_and_management_gets_go_out_with_exact_queries(
    mock_server_factory, synthetic_dir
):
    base, state = mock_server_factory(synthetic_dir, "connected")
    w7_state(state).individual[f"{DOMAIN}/OUTOFMEMORY/1001"] = True
    w7_state(state).compare_404 = True
    tools, _ = make_tools(base)
    manage = ManageTools(tools)
    outs = [
        await manage.apm_users(),
        await manage.apm_users(user_id="canary"),
        await manage.apm_environment(hostname="was-host01"),
        await manage.apm_active_detail(
            domain_id=DOMAIN, txid="-8001", session_id=31001, thread_hash=99001
        ),
        await manage.apm_config("loaded_classes", hostname="was-host01", search="Order"),
        await manage.apm_config("data_server"),
        await manage.apm_config(
            "event_rules", hostname="was-host01", target="instance", error_type="OUTOFMEMORY"
        ),
        await manage.apm_config("color_boundary"),
        await manage.apm_config("process_instance", process_id=4242, hostname="was-host01"),
        await manage.apm_config("db_path", hostname="was-host01"),
        await manage.apm_config("rdb_export"),
    ]
    assert all("error" not in o for o in outs), [o.get("reason") for o in outs]
    # `.xml` 변형은 계속 거부(HTTP 0회)
    with pytest.raises(NotAllowedError):
        await tools.sources.get("default").api.client.get_json("/api/auth/userlist.xml")
    hits = _hits(base)
    assert [h for h in hits if not h["allowlisted"]] == []
    assert [h for h in hits if h["query_token"]] == []
    w7 = [(h["method"], h["path"], h["query"]) for h in hits if h["template"] in W7_TEMPLATES]
    d = DOMAIN
    assert w7 == [
        ("GET", "/api/auth/userlist", {}),
        ("GET", "/restapi/users", {}),
        ("GET", "/restapi/user/canary", {}),
        ("GET", f"/api-v2/environment-variable/{d}", {}),
        ("GET", f"/api-v2/active-service/detail/{d}/-8001",
         {"sessionId": "31001", "threadHash": "99001"}),
        ("GET", f"/api-v2/loaded-class/{d}/1001", {"search": "Order"}),
        ("GET", f"/api-v2/loaded-class/{d}/1002", {"search": "Order"}),
        ("GET", "/api-v2/manage/data-server/domains", {}),
        ("GET", "/api-v2/manage/data-server/resource", {}),
        ("GET", "/api-v2/manage/data-server/system-property-config", {}),
        ("GET", f"/api-v2/manage/rule/event/error/{d}", {}),
        ("GET", f"/api-v2/manage/rule/event/metric/{d}/instance", {}),
        ("GET", f"/api-v2/manage/rule/event/compare/{d}/instance", {}),
        ("GET", f"/api-v2/manage/rule/event/comparing/{d}/instance", {}),
        ("GET", f"/api-v2/manage/rule/event/error/{d}/OUTOFMEMORY/applied", {}),
        ("GET", f"/api-v2/manage/rule/event/error/{d}/OUTOFMEMORY/individual-setting/1001", {}),
        ("GET", f"/api-v2/manage/rule/event/error/{d}/OUTOFMEMORY/individual-setting/1002", {}),
        ("GET", "/api-v2/manage/rule/active-service-color-range-boundary", {}),
        ("GET", "/api-v2/manage/instance", {"processId": "4242", "hostname": "was-host01"}),
        ("GET", f"/api-v2/manage/db/path/{d}", {}),
        ("GET", "/api-v2/manual-rdb-export", {}),
    ]
    assert {h["template"] for h in hits if h["template"] in W7_TEMPLATES} == W7_TEMPLATES
    statuses = {h["path"]: h["status"] for h in hits}
    assert statuses[f"/api-v2/manage/rule/event/compare/{d}/instance"] == 404
    assert statuses[f"/api-v2/manage/rule/event/error/{d}/OUTOFMEMORY/individual-setting/1002"] == (
        404
    )
