"""Open API 클라이언트 — 오류 분류(본문 기준) · 크기 상한 · 토큰 비노출 · Accept 고정
(plans/87 §5.2(c) [v3.3] · §8).
"""

from __future__ import annotations

import logging

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.domain.errors import (
    API_ERROR,
    CONTRACT_VIOLATION,
    NOT_CONFIGURED,
    QUOTA_EXCEEDED,
    SOURCE_UNAVAILABLE,
    ApmError,
)

from apm_gateway.config import JenniferApiConfig

TOKEN = "zz-TOKEN-SHOULD-NOT-LEAK"


def _client(handler, **cfg) -> JenniferClient:
    base = {"url": "http://apm.test", "token": TOKEN, "rate_limit_per_sec": 0}
    base.update(cfg)
    return JenniferClient(JenniferApiConfig(**base), transport=httpx.MockTransport(handler))


async def _error(client: JenniferClient, template="/api/instance", params=None) -> ApmError:
    with pytest.raises(ApmError) as exc:
        await client.get_json(template, params if params is not None else {"domain_id": 1000})
    await client.aclose()
    return exc.value


@pytest.mark.asyncio
async def test_domain_not_connected_is_source_unavailable():
    body = {"exception": {"message": "1000 Domain is not connected"}}
    err = await _error(_client(lambda r: httpx.Response(500, json=body)))
    assert err.code == SOURCE_UNAVAILABLE and err.status == 500


@pytest.mark.asyncio
async def test_v2_string_body_not_connected():
    text = '"500 500 com.aries.view.core.nio.DataServerDownException: 1000 Domain is not connected"'
    client = _client(lambda r: httpx.Response(500, text=text))
    with pytest.raises(ApmError) as exc:
        await client.get_json(
            "/api-v2/deploy/{domainId}", {"startTime": 1, "endTime": 2}, {"domainId": 1000}
        )
    assert exc.value.code == SOURCE_UNAVAILABLE
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "Required request parameter 'domain_id' for method parameter type short is not present",
        "Cannot parse null string",
    ],
)
async def test_missing_param_is_contract_violation(message, caplog):
    caplog.set_level(logging.WARNING)
    err = await _error(
        _client(lambda r: httpx.Response(500, json={"exception": {"message": message}}))
    )
    assert err.code == CONTRACT_VIOLATION
    assert "계약 위반" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code", [(429, QUOTA_EXCEEDED), (401, API_ERROR), (404, API_ERROR), (503, API_ERROR)]
)
async def test_other_status_codes(status, code):
    err = await _error(_client(lambda r: httpx.Response(status, text="x")))
    assert err.code == code and err.status == status


@pytest.mark.asyncio
async def test_timeout_and_connect_error_are_source_unavailable():
    def boom(request):
        raise httpx.ConnectTimeout("t", request=request)

    assert (await _error(_client(boom))).code == SOURCE_UNAVAILABLE

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    assert (await _error(_client(refused))).code == SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_response_size_cap_declared_and_streamed():
    big = b"x" * 5000
    err = await _error(_client(lambda r: httpx.Response(200, content=big), max_response_bytes=1000))
    assert err.code == API_ERROR and "상한" in err.reason

    def streamed(request):
        return httpx.Response(200, stream=httpx.ByteStream(big))

    client = _client(streamed, max_response_bytes=1000)
    with pytest.raises(ApmError):
        await client.get_json("/api/domain")
    await client.aclose()


@pytest.mark.asyncio
async def test_not_configured():
    client = JenniferClient(JenniferApiConfig(url=""))
    with pytest.raises(ApmError) as exc:
        await client.get_json("/api/domain")
    assert exc.value.code == NOT_CONFIGURED


@pytest.mark.asyncio
async def test_token_only_in_header_and_accept_per_endpoint():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("profile.txt"):
            return httpx.Response(200, text="profile", headers={"content-type": "text/plain"})
        return httpx.Response(200, json={"result": []})

    client = _client(handler)
    await client.get_json("/api/domain")
    await client.get_text("/api/transaction/profile.txt", {"domain_id": 1, "txid": 2, "time": 3})
    await client.aclose()
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(seen[0].url) and "token" not in seen[0].url.params
    assert seen[0].headers["accept"] == "application/json"
    assert seen[1].headers["accept"] == "text/plain"


@pytest.mark.asyncio
async def test_token_never_in_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    # 서버가 토큰을 되비추는 최악의 경우에도 사유에서 가린다.
    body = {"exception": {"message": f"bad token {TOKEN} Required request parameter"}}
    err = await _error(_client(lambda r: httpx.Response(500, json=body)))
    assert TOKEN not in err.reason
    err2 = await _error(_client(lambda r: httpx.Response(418, text=f"echo {TOKEN}")))
    assert TOKEN not in err2.reason
    assert TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_rate_limit_spaces_calls(monkeypatch):
    import apm_gateway.adapters.jennifer.client as mod

    sleeps: list[float] = []

    async def fake_sleep(sec):
        sleeps.append(sec)

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    client = _client(lambda r: httpx.Response(200, json={"result": []}), rate_limit_per_sec=2)
    await client.get_json("/api/domain")
    await client.get_json("/api/domain")
    await client.aclose()
    assert sleeps and 0 < sleeps[0] <= 0.5
    assert client.calls_total == 2
