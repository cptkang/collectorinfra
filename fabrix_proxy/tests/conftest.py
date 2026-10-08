"""fabrix_proxy 테스트 공용 — 설정 팩토리 · 가짜 업스트림 · ASGI 클라이언트.

실 FabriX 호출 0건이다. 설정은 `_env_file=None` + 필드 명시로 `.env` 누수를 막는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fabrix_proxy.app import create_app
from fabrix_proxy.config import ProxySettings
from fabrix_proxy.fabrix_client import UpstreamResult

TOKEN = "test-proxy-token"
ALIAS = "fabrix-tools"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_settings(**overrides: Any) -> ProxySettings:
    """테스트 설정 — 검증 대상 필드를 명시한다."""
    values: dict[str, Any] = {
        "fabrix_proxy_token": TOKEN,
        "fabrix_proxy_model_aliases": [ALIAS],
        "fabrix_base_url": "https://upstream.invalid/kbgenai",
        "fabrix_api_key": "secret-api-key",
        "fabrix_client_key": "secret-client-key",
        "fabrix_model": "asset-123",
        "fabrix_total_timeout": 5.0,
        "fabrix_llm_config": {},
        "fabrix_native_url": "",
        "fabrix_proxy_contents_mode": "turns",
        "fabrix_proxy_protocol_lang": "en",
        "fabrix_proxy_fewshot": "static",
        "fabrix_proxy_fewshot_placement": "system",
        "fabrix_proxy_repair_max": 1,
        "fabrix_proxy_passthrough": False,
        "fabrix_proxy_poc_mode": False,
    }
    values.update(overrides)
    return ProxySettings(_env_file=None, **values)  # type: ignore[call-arg]


class FakeUpstream:
    """대본 업스트림. 항목은 텍스트 · UpstreamResult · 예외 · (payload → 항목) 콜러블."""

    def __init__(
        self, script: list[Any] | None = None, native_reply: Any = None, delay: float = 0.0
    ) -> None:
        self.script = list(script or [])
        self.native_reply = native_reply
        self.delay = delay
        self.payloads: list[dict[str, Any]] = []
        self.native_bodies: list[dict[str, Any]] = []

    async def complete(self, payload: dict[str, Any]) -> UpstreamResult:
        self.payloads.append(payload)
        if self.delay:
            await asyncio.sleep(self.delay)
        item = self.script.pop(0) if self.script else "ok"
        if callable(item) and not isinstance(item, type):
            item = item(payload)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, UpstreamResult):
            return item
        return UpstreamResult(text=str(item))

    async def native(self, body: dict[str, Any]) -> dict[str, Any]:
        self.native_bodies.append(body)
        if isinstance(self.native_reply, BaseException):
            raise self.native_reply
        return self.native_reply


@pytest.fixture
def make_client() -> Callable[..., Any]:
    """(settings 덮어쓰기, upstream) → AsyncClient 컨텍스트 팩토리."""

    def factory(upstream: FakeUpstream, **overrides: Any) -> httpx.AsyncClient:
        app = create_app(make_settings(**overrides), upstream=upstream)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        )

    return factory


@pytest.fixture
async def client_and_upstream() -> AsyncIterator[tuple[httpx.AsyncClient, FakeUpstream]]:
    upstream = FakeUpstream()
    app = create_app(make_settings(), upstream=upstream)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    ) as client:
        yield client, upstream


WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get weather for a city.",
        "parameters": {
            "type": "object",
            "title": "WeatherArgs",
            "properties": {
                "city": {"type": "string", "title": "City"},
                "days": {"type": "integer"},
            },
            "required": ["city"],
        },
    },
}
TIME_TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Get the current time in a timezone.",
        "parameters": {
            "type": "object",
            "properties": {"tz": {"type": "string", "enum": ["UTC", "KST"]}},
            "required": ["tz"],
        },
    },
}


def chat_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": ALIAS,
        "messages": [{"role": "user", "content": "weather in Paris?"}],
        "tools": [WEATHER_TOOL, TIME_TOOL],
    }
    body.update(overrides)
    return body
