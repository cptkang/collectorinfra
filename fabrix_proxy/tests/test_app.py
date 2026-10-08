"""app — HTTP 계약 · 오류 봉투 · 교정 · 스트림 합성 · POC_MODE · passthrough · 로그 본문 0.

plans/148 1단계 테스트 A.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
import pytest
from conftest import ALIAS, AUTH, TOKEN, FakeUpstream, chat_body
from fabrix_proxy.fabrix_client import ContentFilterError, UpstreamError, UpstreamResult

from fabrix_proxy import tool_protocol as tp

WEATHER_CALL = tp.serialize_tool_call("get_weather", {"city": "Paris"})
TIME_CALL = tp.serialize_tool_call("get_time", {"tz": "UTC"})


async def post(
    client: httpx.AsyncClient, body: dict[str, Any], headers: dict | None = None
) -> httpx.Response:
    return await client.post(
        "/v1/chat/completions", json=body, headers=AUTH if headers is None else headers
    )


def error_of(resp: httpx.Response) -> dict[str, Any]:
    body = resp.json()
    assert set(body["error"]) == {"message", "type", "code"}
    return body["error"]


# ─────────────────────────── 인증 · 무인증 엔드포인트 ───────────────────────────


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}]
)
async def test_401_without_or_wrong_token(make_client, headers: dict) -> None:
    upstream = FakeUpstream()
    async with make_client(upstream) as client:
        resp = await post(client, chat_body(), headers=headers)
    assert resp.status_code == 401
    assert error_of(resp)["code"] == "invalid_api_key"
    assert upstream.payloads == []


async def test_models_no_auth_and_no_secrets(make_client) -> None:
    async with make_client(FakeUpstream()) as client:
        resp = await client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    entry = body["data"][0]
    assert entry["id"] == ALIAS
    assert entry["backend"] == "fabrix"
    assert entry["capabilities"] == {"tools": "emulated", "stream": True}
    for secret in ("upstream.invalid", "secret-api-key", "secret-client-key", "asset-123", TOKEN):
        assert secret not in resp.text


async def test_health_does_not_call_upstream(make_client) -> None:
    upstream = FakeUpstream()
    async with make_client(upstream) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert upstream.payloads == []


# ─────────────────────────── 오류 봉투 ───────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        chat_body(model="unknown-alias"),
        chat_body(tools=[{"type": "code_interpreter"}]),
        chat_body(
            messages=[
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}
            ]
        ),
    ],
)
async def test_400_invalid_request(make_client, body: dict) -> None:
    async with make_client(FakeUpstream()) as client:
        resp = await post(client, body)
    assert resp.status_code == 400
    err = error_of(resp)
    assert err["code"] == "invalid_request"
    assert err["type"] == "invalid_request_error"


async def test_400_non_json_body(make_client) -> None:
    async with make_client(FakeUpstream()) as client:
        resp = await client.post("/v1/chat/completions", content=b"{nope", headers=AUTH)
    assert resp.status_code == 400
    assert error_of(resp)["code"] == "invalid_request"


async def test_400_content_filter(make_client) -> None:
    async with make_client(FakeUpstream([ContentFilterError("x")])) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 400
    assert error_of(resp)["code"] == "content_filter"


async def test_502_upstream_error(make_client) -> None:
    async with make_client(FakeUpstream([UpstreamError("업스트림 status=FAIL")])) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 502
    assert error_of(resp)["code"] == "upstream_error"


async def test_502_upstream_http_error(make_client) -> None:
    async with make_client(FakeUpstream([httpx.ConnectError("boom")])) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 502
    assert error_of(resp)["code"] == "upstream_error"


async def test_504_total_timeout(make_client) -> None:
    upstream = FakeUpstream(["never"], delay=1.0)
    async with make_client(upstream, fabrix_total_timeout=0.05) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 504
    assert error_of(resp)["code"] == "upstream_timeout"


async def test_504_covers_repair_round(make_client) -> None:
    """총상한은 교정 재질의까지 포함한 호출 1건 전체에 걸린다."""

    calls = {"n": 0}

    class SlowRepair(FakeUpstream):
        async def complete(self, payload: dict) -> UpstreamResult:
            calls["n"] += 1
            if calls["n"] == 1:
                return UpstreamResult(text=tp.serialize_tool_call("nope", {}))
            await asyncio.sleep(1.0)
            return UpstreamResult(text=WEATHER_CALL)

    async with make_client(SlowRepair(), fabrix_total_timeout=0.1) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 504
    assert calls["n"] == 2


async def test_unknown_fields_ignored_not_400(make_client) -> None:
    body = chat_body(
        max_tokens=5,
        max_completion_tokens=5,
        extra_body={"a": 1},
        chat_template_kwargs={},
        stream_options={"include_usage": True},
        user="u",
        brand_new_field=True,
    )
    async with make_client(FakeUpstream(["plain"])) as client:
        resp = await post(client, body)
    assert resp.status_code == 200


async def test_no_422_on_wrong_types(make_client) -> None:
    async with make_client(FakeUpstream()) as client:
        resp = await client.post("/v1/chat/completions", json=[1, 2], headers=AUTH)
    assert resp.status_code == 400


# ─────────────────────────── 응답 변환 ───────────────────────────


async def test_tool_calls_response_shape(make_client) -> None:
    upstream = FakeUpstream(
        [UpstreamResult(text="<|eot_id|>" + WEATHER_CALL, usage={"total_tokens": 9})]
    )
    async with make_client(upstream) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 200
    assert resp.headers["X-Proxy-Backend"] == "fabrix"
    assert resp.headers["X-Proxy-Tool-Emulation"] == "parsed"
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == ALIAS
    assert body["usage"] == {"total_tokens": 9}
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    message = choice["message"]
    assert message["content"] is None
    (call,) = message["tool_calls"]
    assert re.fullmatch(r"call_[0-9a-f]{24}", call["id"])
    assert call["type"] == "function"
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "Paris"}
    assert "fabrix_proxy_diag" not in body


async def test_plain_answer_without_usage(make_client) -> None:
    async with make_client(FakeUpstream(["Paris is sunny."])) as client:
        resp = await post(client, chat_body())
    body = resp.json()
    assert resp.headers["X-Proxy-Tool-Emulation"] == "none"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Paris is sunny."}
    assert "usage" not in body


async def test_tool_choice_none_returns_blocks_as_text_without_protocol(make_client) -> None:
    upstream = FakeUpstream([WEATHER_CALL])
    async with make_client(upstream) as client:
        resp = await post(client, chat_body(tool_choice="none"))
    body = resp.json()
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["choices"][0]["message"]["content"] == WEATHER_CALL
    assert "<tool_call>" not in upstream.payloads[0]["systemPrompt"]


async def test_tool_choice_required_plain_text_repairs_then_502(make_client) -> None:
    upstream = FakeUpstream(["just text", "still text"])
    async with make_client(upstream) as client:
        resp = await post(client, chat_body(tool_choice="required"))
    assert resp.status_code == 502
    err = error_of(resp)
    assert err["code"] == "tool_call_invalid"
    assert "missing_required_call" in err["message"]
    assert resp.headers["X-Proxy-Tool-Emulation"] == "invalid"
    assert len(upstream.payloads) == 2


async def test_tool_choice_named(make_client) -> None:
    choice = {"type": "function", "function": {"name": "get_time"}}
    upstream = FakeUpstream([WEATHER_CALL, TIME_CALL])
    async with make_client(upstream) as client:
        resp = await post(client, chat_body(tool_choice=choice))
    assert resp.status_code == 200
    assert resp.headers["X-Proxy-Tool-Emulation"] == "repaired"
    assert resp.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_time"
    assert "must call the tool `get_time`" in upstream.payloads[0]["systemPrompt"]


async def test_parallel_false_two_blocks_repairs(make_client) -> None:
    upstream = FakeUpstream([WEATHER_CALL + TIME_CALL, WEATHER_CALL])
    async with make_client(upstream) as client:
        resp = await post(client, chat_body(parallel_tool_calls=False))
    assert resp.status_code == 200
    assert resp.headers["X-Proxy-Tool-Emulation"] == "repaired"
    assert len(resp.json()["choices"][0]["message"]["tool_calls"]) == 1


async def test_parallel_true_two_calls(make_client) -> None:
    async with make_client(FakeUpstream([WEATHER_CALL + "\n" + TIME_CALL])) as client:
        resp = await post(client, chat_body())
    calls = resp.json()["choices"][0]["message"]["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["get_weather", "get_time"]
    assert calls[0]["id"] != calls[1]["id"]


async def test_repair_once_success_payload(make_client) -> None:
    bad = tp.serialize_tool_call("get_weather", {"days": 1})
    upstream = FakeUpstream([bad, WEATHER_CALL])
    async with make_client(upstream) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 200
    assert resp.headers["X-Proxy-Tool-Emulation"] == "repaired"
    first, second = upstream.payloads
    assert second["contents"][: len(first["contents"])] == first["contents"]
    assert second["contents"][-2] == bad
    assert "'city'" in second["contents"][-1]
    assert second["systemPrompt"] == first["systemPrompt"]


async def test_repair_exhausted_is_502_never_plain(make_client) -> None:
    bad = "<tool_call>{broken</tool_call> some words"
    upstream = FakeUpstream([bad, bad])
    async with make_client(upstream) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 502
    err = error_of(resp)
    assert err["code"] == "tool_call_invalid"
    assert "json_error" in err["message"]
    assert "some words" not in resp.text  # 원출력 본문을 싣지 않는다
    assert "choices" not in resp.json()


async def test_repair_max_zero(make_client) -> None:
    upstream = FakeUpstream([tp.serialize_tool_call("nope", {})])
    async with make_client(upstream, fabrix_proxy_repair_max=0) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 502
    assert error_of(resp)["code"] == "tool_call_invalid"
    assert len(upstream.payloads) == 1


async def test_temperature_into_llm_config(make_client) -> None:
    upstream = FakeUpstream(["ok"])
    async with make_client(upstream, fabrix_llm_config={"top_k": 3}) as client:
        await post(client, chat_body(temperature=0.2))
    assert upstream.payloads[0]["llmConfig"] == {"top_k": 3, "temperature": 0.2}


# ─────────────────────────── 스트림 합성 ───────────────────────────


def sse_events(text: str) -> list[Any]:
    events = []
    for block in text.strip().split("\n\n"):
        assert block.startswith("data: ")
        data = block[len("data: ") :]
        events.append(data if data == "[DONE]" else json.loads(data))
    return events


async def test_stream_tool_calls_chunks(make_client) -> None:
    async with make_client(FakeUpstream([WEATHER_CALL + TIME_CALL])) as client:
        resp = await post(client, chat_body(stream=True))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["X-Proxy-Tool-Emulation"] == "parsed"
    events = sse_events(resp.text)
    assert events[-1] == "[DONE]"
    chunks = events[:-1]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert len({c["id"] for c in chunks}) == 1
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    tool_delta = chunks[1]["choices"][0]["delta"]["tool_calls"]
    assert [d["index"] for d in tool_delta] == [0, 1]
    assert tool_delta[0]["function"]["name"] == "get_weather"
    assert json.loads(tool_delta[0]["function"]["arguments"]) == {"city": "Paris"}
    assert tool_delta[0]["type"] == "function" and tool_delta[0]["id"].startswith("call_")
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert chunks[-1]["choices"][0]["delta"] == {}


async def test_stream_text_chunks(make_client) -> None:
    async with make_client(FakeUpstream(["hello there"])) as client:
        resp = await post(client, chat_body(stream=True))
    chunks = sse_events(resp.text)[:-1]
    assert chunks[1]["choices"][0]["delta"] == {"content": "hello there"}
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


async def test_stream_error_is_json_envelope(make_client) -> None:
    async with make_client(FakeUpstream([UpstreamError("x")])) as client:
        resp = await post(client, chat_body(stream=True))
    assert resp.status_code == 502
    assert error_of(resp)["code"] == "upstream_error"


# ─────────────────────────── POC_MODE ───────────────────────────


async def test_poc_off_ignores_options_and_no_diag(make_client) -> None:
    upstream = FakeUpstream(["ok"])
    async with make_client(upstream) as client:
        resp = await post(client, chat_body(fabrix_proxy_options={"fewshot": "none", "bogus": 1}))
    assert resp.status_code == 200
    assert "fabrix_proxy_diag" not in resp.json()
    assert "# Format examples" in upstream.payloads[0]["systemPrompt"]


async def test_poc_on_applies_options_and_diag(make_client) -> None:
    bad = tp.serialize_tool_call("example_lookup_item", {"code": "A-100"})
    upstream = FakeUpstream([bad, WEATHER_CALL])
    options = {
        "contents_mode": "transcript",
        "protocol_lang": "ko",
        "fewshot": "static",
        "repair_max": 1,
    }
    async with make_client(upstream, fabrix_proxy_poc_mode=True) as client:
        resp = await post(client, chat_body(fabrix_proxy_options=options))
    assert resp.status_code == 200
    payload = upstream.payloads[0]
    assert "# 도구" in payload["systemPrompt"]
    assert len(payload["contents"]) == 2
    diag = resp.json()["fabrix_proxy_diag"]
    assert set(diag) == {
        "emulation",
        "passthrough",
        "attempts",
        "payload_chars",
        "payload_est_tokens",
        "protocol_chars",
        "fewshot_chars",
        "contents_len",
        "upstream_ms",
        "raw_heads",
    }
    assert diag["emulation"] == "repaired"
    assert diag["attempts"] == [
        {
            "kind": "invalid",
            "reasons": ["unknown_tool"],
            "called_names": ["example_lookup_item"],
            "example_tool_called": True,
            "call_like_text": False,
        },
        {
            "kind": "tool_calls",
            "reasons": [],
            "called_names": ["get_weather"],
            "example_tool_called": False,
            "call_like_text": False,
        },
    ]
    joined = payload["systemPrompt"] + "".join(payload["contents"])
    assert diag["payload_chars"] == len(joined)
    assert diag["payload_est_tokens"] == tp.estimate_tokens(joined)
    assert diag["contents_len"] == 2
    assert diag["protocol_chars"] > 0 and diag["fewshot_chars"] > 0
    assert diag["passthrough"] is False
    # 시도별 원출력 앞부분은 raw_heads에만 — 그 밖의 진단 필드는 판정값만
    assert diag["raw_heads"] == [bad, WEATHER_CALL]
    judged = {k: v for k, v in diag.items() if k != "raw_heads"}
    assert "Paris" not in json.dumps(judged)


async def test_poc_inline_protocol_and_fewshot(make_client) -> None:
    upstream = FakeUpstream(["ok"])
    fewshot = {"tools": [], "examples": []}
    options = {
        "protocol_text": "MY RULES\n{{TOOLS}}",
        "fewshot_examples": fewshot,
        "fewshot_placement": "contents",
    }
    async with make_client(upstream, fabrix_proxy_poc_mode=True) as client:
        resp = await post(client, chat_body(fabrix_proxy_options=options))
    assert resp.status_code == 200
    assert upstream.payloads[0]["systemPrompt"].startswith("MY RULES\n- name: get_weather")
    assert upstream.payloads[0]["contents"] == ["", "weather in Paris?"]


async def test_poc_bad_option_is_400(make_client) -> None:
    async with make_client(FakeUpstream(), fabrix_proxy_poc_mode=True) as client:
        resp = await post(client, chat_body(fabrix_proxy_options={"fewshot_file": "/etc/passwd"}))
    assert resp.status_code == 400


async def test_poc_diag_on_invalid_error(make_client) -> None:
    upstream = FakeUpstream(["text", "text"])
    async with make_client(upstream, fabrix_proxy_poc_mode=True) as client:
        resp = await post(client, chat_body(tool_choice="required"))
    assert resp.status_code == 502
    diag = resp.json()["fabrix_proxy_diag"]
    assert diag["emulation"] == "invalid"
    assert [a["kind"] for a in diag["attempts"]] == ["invalid", "invalid"]
    assert diag["raw_heads"] == ["text", "text"]


async def test_poc_raw_heads_truncated(make_client) -> None:
    upstream = FakeUpstream(["x" * 800])
    async with make_client(upstream, fabrix_proxy_poc_mode=True) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 200
    assert resp.json()["fabrix_proxy_diag"]["raw_heads"] == ["x" * 500]


async def test_poc_off_error_envelope_has_no_diag(make_client) -> None:
    upstream = FakeUpstream(["text", "text"])
    async with make_client(upstream) as client:
        resp = await post(client, chat_body(tool_choice="required"))
    assert resp.status_code == 502
    body = resp.json()
    assert "fabrix_proxy_diag" not in body
    assert "raw_heads" not in json.dumps(body)


# ─────────────────────────── passthrough ───────────────────────────

NATIVE_REPLY = {
    "id": "x",
    "object": "chat.completion",
    "model": "native-model",
    "choices": [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_n1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                    }
                ],
            },
        }
    ],
    "usage": {"total_tokens": 3},
}


async def test_passthrough_unconfigured_is_400(make_client) -> None:
    async with make_client(FakeUpstream(), fabrix_proxy_passthrough=True) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 400


async def test_passthrough_setting(make_client) -> None:
    upstream = FakeUpstream(native_reply=NATIVE_REPLY)
    async with make_client(
        upstream,
        fabrix_proxy_passthrough=True,
        fabrix_native_url="https://native.invalid/v1",
        fabrix_native_model="native-model",
    ) as client:
        resp = await post(client, chat_body(max_tokens=5))
    assert resp.status_code == 200
    assert upstream.payloads == []
    (sent,) = upstream.native_bodies
    assert sent["model"] == "native-model" and sent["max_tokens"] == 5 and sent["stream"] is False
    body = resp.json()
    assert body["model"] == ALIAS
    assert body["choices"][0]["message"]["tool_calls"][0]["id"] == "call_n1"
    assert resp.headers["X-Proxy-Tool-Emulation"] == "none"


async def test_passthrough_poc_option_stream(make_client) -> None:
    upstream = FakeUpstream(native_reply=NATIVE_REPLY)
    async with make_client(
        upstream,
        fabrix_proxy_poc_mode=True,
        fabrix_native_url="https://native.invalid/v1",
    ) as client:
        resp = await post(
            client, chat_body(stream=True, fabrix_proxy_options={"passthrough": True})
        )
    assert upstream.native_bodies[0]["model"] == "asset-123"  # NATIVE_MODEL이 비면 FABRIX_MODEL
    events = sse_events(resp.text)
    assert events[-1] == "[DONE]"
    assert events[1]["choices"][0]["delta"]["tool_calls"][0]["id"] == "call_n1"
    assert events[-2]["choices"][0]["finish_reason"] == "tool_calls"


async def test_passthrough_malformed_native_is_502(make_client) -> None:
    upstream = FakeUpstream(native_reply={"choices": []})
    async with make_client(
        upstream, fabrix_proxy_passthrough=True, fabrix_native_url="https://n.invalid"
    ) as client:
        resp = await post(client, chat_body())
    assert resp.status_code == 502


# ─────────────────────────── 로그 본문 0 ───────────────────────────

MARK_REQ = "MARKER_REQUEST_7f3a"
MARK_RESP = "MARKER_RESPONSE_9c1d"


async def test_logs_never_contain_bodies(make_client, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    leaky_call = tp.serialize_tool_call("get_weather", {"city": MARK_RESP})
    upstream = FakeUpstream(
        [
            f"{MARK_RESP} plain",  # 평문
            leaky_call,  # tool_calls
            f"<tool_call>{MARK_RESP}</tool_call>",
            f"<tool_call>{MARK_RESP}</tool_call>",  # 교정 소진
            UpstreamError("status=FAIL"),
        ]
    )
    body = chat_body(
        messages=[{"role": "system", "content": MARK_REQ}, {"role": "user", "content": MARK_REQ}],
        max_tokens=1,
    )
    async with make_client(upstream, fabrix_proxy_poc_mode=True) as client:
        for _ in range(4):
            await post(client, body)
        await post(client, {**body, "stream": True})
    assert "chat id=" in caplog.text  # 감사 줄은 남는다
    assert MARK_REQ not in caplog.text
    assert MARK_RESP not in caplog.text
    assert "secret-api-key" not in caplog.text
    assert "upstream.invalid" not in caplog.text
