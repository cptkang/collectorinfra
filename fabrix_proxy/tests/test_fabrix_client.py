"""fabrix_client — KBGenAI 호출 이식 · PII 차단 판정 이식 (plans/148 1단계 테스트 A).

실 네트워크 0건 — `httpx.MockTransport`를 주입한다.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import make_settings

from fabrix_proxy import fabrix_client as fc


@pytest.mark.parametrize(
    "result, blocked",
    [
        ({"status": "SUCCESS", "content": "ok"}, False),
        ({"status": "FILTER_INVALID"}, True),
        ({"data": [{"status": "filter_invalid"}]}, True),
        ({"filterBlockReason": {"result_code": "FR-400"}}, True),
        (
            {
                "filter_block_reason": {
                    "result_code": "FR-200",
                    "message": "The content was passed",
                    "policy_id": None,
                }
            },
            False,
        ),
        ({"filter_block_reason": [{"policy_id": 7}]}, True),
        ({"filterBlockReason": {"message": "Request blocked"}}, True),
        ({"status": "SUCCESS", "content": "blocked by the filter"}, True),
    ],
)
def test_is_filter_blocked_table(result: dict, blocked: bool) -> None:
    assert fc.is_filter_blocked(result) is blocked


def test_is_filter_blocked_apim_raw_text() -> None:
    raw = "{'error': '민감정보 감지됨: ', 'rule_name': '핸드폰번호'}"
    assert fc.is_filter_blocked(raw_text=raw) is True
    assert fc.is_filter_blocked(raw_text="ordinary error") is False


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch):
    seen: list[httpx.Request] = []

    def install(handler) -> list[httpx.Request]:
        real = httpx.AsyncClient

        def wrapped(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        def factory(**kwargs: Any) -> httpx.AsyncClient:
            kwargs.pop("verify", None)
            return real(transport=httpx.MockTransport(wrapped), **kwargs)

        monkeypatch.setattr(fc.httpx, "AsyncClient", factory)
        return seen

    return install


async def test_complete_success_headers_and_junk(mock_http) -> None:
    seen = mock_http(
        lambda r: httpx.Response(
            200,
            json={"status": "SUCCESS", "content": " hi<|eot_id|>", "usage": {"total_tokens": 2}},
        )
    )
    upstream = fc.KBGenAIUpstream(make_settings())
    result = await upstream.complete({"modelId": "m"})
    assert result.text == "hi"
    assert result.usage == {"total_tokens": 2}
    (req,) = seen
    assert req.headers["x-openapi-token"] == "Bearer secret-api-key"
    assert req.headers["x-generative-ai-client"] == "secret-client-key"
    assert json.loads(req.content) == {"modelId": "m"}


@pytest.mark.parametrize(
    "response, exc",
    [
        (httpx.Response(200, json={"status": "FAIL"}), fc.UpstreamError),
        (httpx.Response(200, json=["not", "dict"]), fc.UpstreamError),
        (httpx.Response(200, content=b"not json"), fc.UpstreamError),
        (httpx.Response(500, text="oops"), fc.UpstreamError),
        (httpx.Response(200, json={"status": "FILTER_INVALID"}), fc.ContentFilterError),
        (httpx.Response(400, text="{'error': '민감정보 감지됨: '}"), fc.ContentFilterError),
    ],
)
async def test_complete_errors(mock_http, response: httpx.Response, exc: type) -> None:
    mock_http(lambda r: response)
    with pytest.raises(exc):
        await fc.KBGenAIUpstream(make_settings()).complete({})


async def test_native_posts_body(mock_http) -> None:
    seen = mock_http(lambda r: httpx.Response(200, json={"choices": []}))
    upstream = fc.KBGenAIUpstream(make_settings(fabrix_native_url="https://native.invalid/v1"))
    assert await upstream.native({"model": "x"}) == {"choices": []}
    assert str(seen[0].url) == "https://native.invalid/v1"
