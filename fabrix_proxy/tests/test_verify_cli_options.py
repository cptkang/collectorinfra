"""독립 검증(verifier) — 러너 CLI 선택지 → `fabrix_proxy_options` → 프록시 변환 결과.

plans/148 §5 1단계. 내부망 무수정 실행 요건: 측정 선택지가 하나라도 무시되면 판정이 무의미하다.
러너의 실제 argparse → `resolve_run_options` → `proxy_options()`를 거친 바디를 in-process
프록시(가짜 업스트림)에 보내, 업스트림이 받은 KBGenAI 페이로드가 선택지대로 바뀌는지 본다.
러너 쪽 선택지(`--concurrency`·
`--sleep`·`--only`·`--repeat`·`--out`·`--proxy-url`)는 측정 함수로 전달되는지 본다.

실 LLM 0건 — 업스트림은 `FakeUpstream`(in-process)과 `httpx.MockTransport`뿐이다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import ALIAS, AUTH, TIME_TOOL, WEATHER_TOOL, FakeUpstream, make_settings
from fabrix_proxy.app import create_app

from fabrix_proxy import fabrix_client as fc
from fabrix_proxy import tool_protocol as tp

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc_run as pr  # noqa: E402

TOOLS = [WEATHER_TOOL, TIME_TOOL]
USER_TEXT = "weather in Paris?"
GOOD_CALL = tp.serialize_tool_call("get_weather", {"city": "Paris"})
BAD_CALL = "<tool_call>not json</tool_call>"


def _run_opts(argv: list[str], **settings: Any) -> pr.RunOptions:
    args = pr.build_parser().parse_args(["run", *argv])
    return pr.resolve_run_options(args, make_settings(fabrix_proxy_poc_mode=True, **settings))


async def _send(
    argv: list[str], script: list[Any] | None = None, native_reply: Any = None, **settings: Any
) -> tuple[httpx.Response, FakeUpstream]:
    opts = _run_opts(argv)
    upstream = FakeUpstream(script or [GOOD_CALL], native_reply=native_reply)
    app = create_app(make_settings(fabrix_proxy_poc_mode=True, **settings), upstream=upstream)
    body = {
        "model": ALIAS,
        "messages": [{"role": "user", "content": USER_TEXT}],
        "tools": TOOLS,
        pr.OPTIONS_FIELD: opts.proxy_options(),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    ) as client:
        resp = await client.post("/v1/chat/completions", json=body, headers=AUTH)
    return resp, upstream


def _joined(payload: dict[str, Any]) -> str:
    return "\n".join(payload["contents"])


# ─────────────────────────── 에뮬레이션 선택지 → 페이로드 ───────────────────────────


async def test_baseline_payload_defaults() -> None:
    resp, up = await _send([])
    assert resp.status_code == 200
    payload = up.payloads[0]
    assert payload["contents"] == ["", USER_TEXT]
    assert "# Tools" in payload["systemPrompt"]
    assert "# Format examples" in payload["systemPrompt"]
    assert "example_lookup_item" in payload["systemPrompt"]


async def test_contents_mode_transcript_reaches_payload() -> None:
    _, up = await _send(["--contents-mode", "transcript"])
    assert up.payloads[0]["contents"] == ["", f"[user]\n{USER_TEXT}"]


async def test_protocol_lang_ko_reaches_payload() -> None:
    _, up = await _send(["--protocol-lang", "ko"])
    system = up.payloads[0]["systemPrompt"]
    assert "# 도구" in system and "# 형식 예시" in system
    assert "# Tools" not in system and "# Format examples" not in system


async def test_protocol_file_reaches_payload(tmp_path: Path) -> None:
    path = tmp_path / "proto.txt"
    path.write_text("CUSTOM_PROTOCOL_MARK_41\n{{TOOLS}}\n{{PARALLEL_RULE}}", encoding="utf-8")
    _, up = await _send(["--protocol-file", str(path)])
    system = up.payloads[0]["systemPrompt"]
    assert "CUSTOM_PROTOCOL_MARK_41" in system
    assert "- name: get_weather" in system
    assert "# Tools" not in system


async def test_fewshot_none_removes_examples() -> None:
    _, up = await _send(["--fewshot", "none"])
    payload = up.payloads[0]
    assert "# Format examples" not in payload["systemPrompt"]
    assert "example_lookup_item" not in payload["systemPrompt"] + _joined(payload)


async def test_fewshot_dynamic_uses_request_tools() -> None:
    _, up = await _send(["--fewshot", "dynamic"])
    system = up.payloads[0]["systemPrompt"]
    assert "placeholders" in system
    assert "example_lookup_item" not in system
    assert '"name": "get_weather"' in system  # 예시가 요청 도구 이름으로 렌더


async def test_fewshot_placement_contents_moves_examples() -> None:
    _, up = await _send(["--fewshot-placement", "contents"])
    payload = up.payloads[0]
    assert "# Format examples" not in payload["systemPrompt"]
    assert "example_lookup_item" in _joined(payload)
    assert payload["contents"][-1].endswith(USER_TEXT)  # 실제 질문은 맨 끝


async def test_fewshot_file_reaches_payload(tmp_path: Path) -> None:
    data = tp.load_fewshot()
    text = json.dumps(data).replace("example_lookup_item", "zz_demo_probe_tool")
    path = tmp_path / "fs.json"
    path.write_text(text, encoding="utf-8")
    _, up = await _send(["--fewshot-file", str(path)])
    system = up.payloads[0]["systemPrompt"]
    assert "zz_demo_probe_tool" in system
    assert "example_lookup_item" not in system


@pytest.mark.parametrize(("value", "calls"), [("0", 1), ("2", 3)])
async def test_repair_max_controls_attempts(value: str, calls: int) -> None:
    resp, up = await _send(["--repair-max", value], script=[BAD_CALL] * 5)
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "tool_call_invalid"
    assert len(up.payloads) == calls


async def test_passthrough_reaches_native() -> None:
    reply = {
        "choices": [{"message": {"role": "assistant", "content": "native"},
                     "finish_reason": "stop"}]
    }
    resp, up = await _send(
        ["--passthrough"], native_reply=reply,
        fabrix_native_url="https://native.invalid/v1/chat/completions",
    )
    assert resp.status_code == 200
    assert up.payloads == [] and len(up.native_bodies) == 1
    assert pr.OPTIONS_FIELD not in up.native_bodies[0]
    assert resp.json()[pr.DIAG_FIELD]["passthrough"] is True


async def test_label_records_every_emulation_option(tmp_path: Path) -> None:
    proto = tmp_path / "p.txt"
    proto.write_text("X\n{{TOOLS}}", encoding="utf-8")
    opts = _run_opts([
        "--contents-mode", "transcript", "--protocol-lang", "ko", "--protocol-file", str(proto),
        "--fewshot", "dynamic", "--fewshot-placement", "contents", "--repair-max", "3",
        "--passthrough",
    ])
    assert opts.label() == (
        "contents=transcript,fewshot=dynamic,placement=contents,lang=ko,repair=3,"
        "passthrough=true,protocol_file=p.txt"
    )
    sent = opts.proxy_options()
    assert sent["protocol_text"].startswith("X")
    assert {k: sent[k] for k in ("contents_mode", "protocol_lang", "fewshot",
                                 "fewshot_placement", "repair_max", "passthrough")} == {
        "contents_mode": "transcript", "protocol_lang": "ko", "fewshot": "dynamic",
        "fewshot_placement": "contents", "repair_max": 3, "passthrough": True,
    }


# ─────────────────────────── 러너 쪽 선택지 → 측정 함수 ───────────────────────────


def test_runner_options_reach_measure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class _Client:
        async def aclose(self) -> None:
            seen["closed"] = True

    async def fake_connect(url: str, token: str, guard: Any) -> tuple[Any, str]:
        seen["url"], seen["token"] = url, token
        return _Client(), ALIAS

    async def fake_measure(client: Any, alias: str, opts: Any, only: list[str],
                           repeat: dict[str, int], out: Path, guard: Any,
                           concurrency: int, sleep: float) -> None:
        seen.update(only=only, repeat=repeat, out=out, concurrency=concurrency, sleep=sleep)

    monkeypatch.setattr(pr, "load_settings", lambda: make_settings(fabrix_proxy_poc_mode=True))
    monkeypatch.setattr(pr, "connect", fake_connect)
    monkeypatch.setattr(pr, "measure", fake_measure)
    monkeypatch.setattr(pr, "rebuild_reports", lambda out, guard: None)
    out = tmp_path / "o"
    code = pr.main(["run", "--proxy-url", "http://127.0.0.1:9", "--only", "S4,S1",
                    "--repeat", "S1=2", "--concurrency", "3", "--sleep", "0.25",
                    "--out", str(out)])
    assert code == 0
    assert seen["url"] == "http://127.0.0.1:9" and seen["token"] == "test-proxy-token"
    assert seen["only"] == ["S4", "S1"]
    assert seen["repeat"]["S1"] == 2 and seen["repeat"]["S4"] == pr.DEFAULT_REPEAT["S4"]
    assert seen["out"] == out.resolve()
    assert seen["concurrency"] == 3 and seen["sleep"] == 0.25
    assert seen["closed"] is True


class _CountingClient:
    """동시 호출 수와 호출 간 대기를 세는 가짜 프록시 클라이언트."""

    def __init__(self) -> None:
        self.inflight = 0
        self.peak = 0
        self.calls = 0
        self.url, self.token = "http://fake", "tok"

    async def chat(self, body: dict[str, Any]) -> pr.CallOutcome:
        self.calls += 1
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        await asyncio.sleep(0.02)
        self.inflight -= 1
        return pr.CallOutcome(200, 1, None, {"emulation": "none", "attempts": []},
                              {"role": "assistant", "content": "ok"})


@pytest.mark.parametrize("concurrency", [1, 3])
async def test_measure_honors_concurrency(concurrency: int, tmp_path: Path) -> None:
    client = _CountingClient()
    opts = _run_opts([])
    await pr.measure(client, ALIAS, opts, ["S4"], {**pr.DEFAULT_REPEAT, "S4": 6},  # type: ignore[arg-type]
                     tmp_path, pr.ExportGuard(), concurrency, 0.0)
    assert client.calls == 6
    assert client.peak == concurrency


async def test_measure_honors_sleep(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def spy(delay: float, *a: Any, **k: Any) -> None:
        if delay >= 0.5:
            slept.append(delay)
            return
        await real_sleep(delay)

    monkeypatch.setattr(pr.asyncio, "sleep", spy)
    client = _CountingClient()
    await pr.measure(client, ALIAS, _run_opts([]), ["S4"], {**pr.DEFAULT_REPEAT, "S4": 2},  # type: ignore[arg-type]
                     tmp_path, pr.ExportGuard(), 1, 0.75)
    assert slept == [0.75, 0.75]


# ─────────────────── 실 업스트림 호출자 오류 경로 — 본문·URL·키 0 ───────────────────

MARK = "UPSTREAM_BODY_MARK_5521"


_REAL_CLIENT = httpx.AsyncClient


def _install(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """업스트림 호출자의 httpx.AsyncClient만 목 전송으로 바꾼다(테스트 클라이언트는 원본)."""

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("verify", None)
        return _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(fc.httpx, "AsyncClient", factory)


@pytest.mark.parametrize(
    ("response", "status", "code"),
    [
        (httpx.Response(500, text=f"boom {MARK}"), 502, "upstream_error"),
        (httpx.Response(400, text=f"민감정보 감지됨 {MARK}"), 400, "content_filter"),
        (httpx.Response(200, text=f"<html>{MARK}</html>"), 502, "upstream_error"),
        (httpx.Response(200, json={"status": "FAIL", "content": MARK}), 502, "upstream_error"),
        (httpx.Response(200, json={"status": "FILTER_INVALID", "content": MARK}), 400,
         "content_filter"),
    ],
)
async def test_real_upstream_error_paths_leak_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    response: httpx.Response, status: int, code: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    _install(monkeypatch, lambda request: response)
    settings = make_settings(fabrix_proxy_poc_mode=False)
    app = create_app(settings)  # 실 KBGenAIUpstream(전송만 목)
    async with _REAL_CLIENT(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    ) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": ALIAS, "messages": [{"role": "user", "content": "hi"}],
                  "tools": TOOLS},
            headers=AUTH,
        )
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code
    # httpx·httpcore 요청 URL 로그는 create_app이 WARNING으로 올려 막는다
    # (test_fix_review_round ⑥) — 여기서는 프록시 자체 로거만 본다.
    own_logs = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("fabrix_proxy")
    )
    for text in (resp.text, own_logs):
        assert MARK not in text
        assert "upstream.invalid" not in text
        assert "secret-api-key" not in text and "secret-client-key" not in text


async def test_real_upstream_sends_original_headers_and_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "SUCCESS", "content": "hi<|eot_id|>"})

    _install(monkeypatch, handler)
    app = create_app(make_settings(fabrix_llm_config={"temperature": 0.1}))
    async with _REAL_CLIENT(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    ) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": ALIAS, "messages": [{"role": "system", "content": "S"},
                                               {"role": "user", "content": "hi"}]},
            headers=AUTH,
        )
    assert resp.json()["choices"][0]["message"]["content"] == "hi"
    req = seen[0]
    assert req.headers["x-openapi-token"] == "Bearer secret-api-key"
    assert req.headers["x-generative-ai-client"] == "secret-client-key"
    body = json.loads(req.content)
    assert list(body) == ["modelId", "contents", "isStream", "isRagOn", "executeRagFinalAnswer",
                          "executeRagStandaloneQuery", "systemPrompt", "llmConfig"]
    assert body["modelId"] == "asset-123" and body["isStream"] is False
    assert body["systemPrompt"] == "S" and body["contents"] == ["", "hi"]
    assert body["llmConfig"] == {"temperature": 0.1}
