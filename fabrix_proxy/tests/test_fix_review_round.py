"""검증·보안 감사 교정 라운드 고정 테스트 (plans/148 1단계 PoC).

① 태그 없는 호출 모양 평문 판정값 · ② 빈 출력 교정 · ③ 요청 선택지 우선 · ④ 러너 400 즉시 중단 ·
⑤ 예상 못 한 예외 봉투·parameters 검증 · ⑥ httpx 로그 억제 · ⑦ litellm 비용표 외부 조회 차단 ·
⑧ 요청 크기·repair_max·tools 상한 · ⑨ 모델 유래 문자열 정화 · ⑩ 반출물 비밀 전체 값 검사.

실 LLM 0건 — 업스트림은 `FakeUpstream`(in-process)뿐이다.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import (
    ALIAS,
    AUTH,
    TIME_TOOL,
    TOKEN,
    WEATHER_TOOL,
    FakeUpstream,
    chat_body,
    make_settings,
)
from fabrix_proxy.app import create_app
from fabrix_proxy.convert import MAX_TOOLS
from fabrix_proxy.fabrix_client import KBGenAIUpstream

from fabrix_proxy import app as app_module
from fabrix_proxy import tool_protocol as tp

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc_run as pr  # noqa: E402
import record_consumer_requests as rcr  # noqa: E402

TOOLS = [WEATHER_TOOL, TIME_TOOL]
GOOD_CALL = tp.serialize_tool_call("get_weather", {"city": "Paris"})
BARE_CALL = '{"name": "get_weather", "arguments": {"city": "Paris"}}'


def _client(upstream: FakeUpstream, **settings: Any) -> httpx.AsyncClient:
    app = create_app(make_settings(**settings), upstream=upstream)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy.test")


async def _post(upstream: FakeUpstream, body: dict[str, Any], **settings: Any) -> httpx.Response:
    async with _client(upstream, **settings) as client:
        return await client.post("/v1/chat/completions", json=body, headers=AUTH)


# ─────────────────────────── ① 호출 모양 평문 ───────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        BARE_CALL,
        '{"tool_name": "get_weather", "arguments": {"city": "Paris"}}',
        f"Sure, calling it now:\n{BARE_CALL}\nDone.",
        '[{"name": "get_weather", "arguments": {}}]',
        "Answer: {not json} then " + BARE_CALL,
    ],
)
def test_call_like_text_flagged_but_stays_text(text: str) -> None:
    parsed = tp.parse_response(text, TOOLS, "auto", True)
    assert parsed.kind == "text" and parsed.content == text.strip()
    assert parsed.call_like_text is True


@pytest.mark.parametrize(
    "text",
    [
        "It is sunny in Paris.",
        '{"city": "Paris", "temp": 20}',
        '{"result": {"name": "x", "arguments": {}}}',  # 중첩 객체는 최상위가 아니다
        "{broken json",
    ],
)
def test_plain_text_not_call_like(text: str) -> None:
    parsed = tp.parse_response(text, TOOLS, "auto", True)
    assert parsed.kind == "text" and parsed.call_like_text is False


def test_call_like_flag_on_required_and_tagged_paths() -> None:
    required = tp.parse_response(BARE_CALL, TOOLS, "required", True)
    assert required.kind == "invalid" and required.reasons == ["missing_required_call"]
    assert required.call_like_text is True
    tagged = tp.parse_response(GOOD_CALL, TOOLS, "auto", True)
    assert tagged.kind == "tool_calls" and tagged.call_like_text is False
    fenced = tp.parse_response(f"```json\n{BARE_CALL}\n```", TOOLS, "auto", True)
    assert fenced.kind == "tool_calls"  # 보조 형식(펜스 하나)은 그대로 호출


async def test_call_like_text_reaches_diag_and_stays_plain_200() -> None:
    resp = await _post(FakeUpstream([BARE_CALL]), chat_body(), fabrix_proxy_poc_mode=True)
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["choices"][0]["message"]["content"] == BARE_CALL
    assert body[app_module.DIAG_FIELD]["attempts"][0]["call_like_text"] is True


# ─────────────────────────── ② 빈 출력 ───────────────────────────


@pytest.mark.parametrize("empty", ["", "   \n", "<|eot_id|>  "])
def test_empty_output_with_tools_is_repair_target(empty: str) -> None:
    parsed = tp.parse_response(empty, TOOLS, "auto", True)
    assert parsed.kind == "invalid" and parsed.reasons == ["empty_output"]
    assert "empty" in tp.build_repair_message(parsed, TOOLS, "auto", True, "en")
    assert "비어" in tp.build_repair_message(parsed, TOOLS, "auto", True, "ko")


def test_empty_output_without_tools_unchanged() -> None:
    assert tp.parse_response("", [], "none", True).kind == "text"
    assert tp.parse_response("", TOOLS, "none", True).kind == "text"


async def test_empty_output_exhausted_is_502() -> None:
    upstream = FakeUpstream(["", "  "])
    resp = await _post(upstream, chat_body(), fabrix_proxy_poc_mode=True)
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "tool_call_invalid"
    assert "empty_output" in resp.json()["error"]["message"]
    assert len(upstream.payloads) == 2


async def test_empty_output_repaired() -> None:
    resp = await _post(FakeUpstream(["", GOOD_CALL]), chat_body())
    assert resp.status_code == 200
    assert resp.headers[app_module.HEADER_EMULATION] == "repaired"


async def test_empty_output_without_tools_stays_200() -> None:
    resp = await _post(FakeUpstream([""]), chat_body(tools=None))
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == ""


# ─────────────────────────── ③ 요청 선택지 우선 ───────────────────────────


def _poc_body(**options: Any) -> dict[str, Any]:
    return chat_body(fabrix_proxy_options=options)


async def test_protocol_lang_option_beats_protocol_file_setting(tmp_path: Path) -> None:
    custom = tmp_path / "proto.txt"
    custom.write_text("ZZ_SETTING_PROTOCOL {{TOOLS}}", encoding="utf-8")
    upstream = FakeUpstream([GOOD_CALL, GOOD_CALL])
    settings = {"fabrix_proxy_poc_mode": True, "fabrix_proxy_protocol_file": str(custom)}
    async with _client(upstream, **settings) as client:
        await client.post("/v1/chat/completions", json=chat_body(), headers=AUTH)
        await client.post(
            "/v1/chat/completions", json=_poc_body(protocol_lang="en"), headers=AUTH
        )
    assert "ZZ_SETTING_PROTOCOL" in upstream.payloads[0]["systemPrompt"]  # 옵션 없으면 설정
    assert "ZZ_SETTING_PROTOCOL" not in upstream.payloads[1]["systemPrompt"]


async def test_fewshot_option_beats_fewshot_file_setting(tmp_path: Path) -> None:
    data = tp.load_fewshot()
    text = json.dumps(data).replace("example_lookup_item", "zz_setting_tool")
    custom = tmp_path / "fs.json"
    custom.write_text(text, encoding="utf-8")
    upstream = FakeUpstream([GOOD_CALL, GOOD_CALL])
    settings = {"fabrix_proxy_poc_mode": True, "fabrix_proxy_fewshot_file": str(custom)}
    async with _client(upstream, **settings) as client:
        await client.post("/v1/chat/completions", json=chat_body(), headers=AUTH)
        await client.post("/v1/chat/completions", json=_poc_body(fewshot="static"), headers=AUTH)
    assert "zz_setting_tool" in upstream.payloads[0]["systemPrompt"]
    second = upstream.payloads[1]["systemPrompt"]
    assert "zz_setting_tool" not in second and "example_lookup_item" in second


async def test_passthrough_false_option_beats_setting() -> None:
    upstream = FakeUpstream([GOOD_CALL])
    resp = await _post(
        upstream, _poc_body(passthrough=False), fabrix_proxy_poc_mode=True,
        fabrix_proxy_passthrough=True, fabrix_native_url="https://native.invalid/v1",
    )
    assert resp.status_code == 200
    assert len(upstream.payloads) == 1 and upstream.native_bodies == []


def test_runner_sends_every_choice_explicitly() -> None:
    args = pr.build_parser().parse_args(["run"])
    options = pr.resolve_run_options(args, make_settings()).proxy_options()
    assert {
        "contents_mode", "protocol_lang", "fewshot", "fewshot_placement", "repair_max",
        "passthrough",
    } <= set(options)


# ─────────────────────────── ④ 러너 400 invalid_request 중단 ───────────────────────────


class _ScriptedClient:
    """대본 응답을 돌려주는 가짜 프록시 클라이언트."""

    def __init__(self, outcomes: list[pr.CallOutcome]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.url, self.token = "http://fake", "tok"

    async def chat(self, body: dict[str, Any]) -> pr.CallOutcome:
        self.calls += 1
        return self.outcomes[min(self.calls, len(self.outcomes)) - 1]

    async def aclose(self) -> None:
        return None


def _opts(**kw: Any) -> pr.RunOptions:
    base: dict[str, Any] = {
        "contents_mode": "turns", "protocol_lang": "en", "fewshot": "static",
        "fewshot_placement": "system", "repair_max": 1, "passthrough": False,
    }
    base.update(kw)
    return pr.RunOptions(**base)


def _repeat(**kw: int) -> dict[str, int]:
    return {**pr.DEFAULT_REPEAT, **kw}


async def test_invalid_request_from_real_app_stops_runner(tmp_path: Path) -> None:
    app = create_app(make_settings(fabrix_proxy_poc_mode=True), upstream=FakeUpstream())
    client = pr.ProxyClient("http://proxy.test", TOKEN, pr.ExportGuard())
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test", headers=AUTH
    )
    with pytest.raises(pr.RunError) as info:
        await pr.measure(client, ALIAS, _opts(passthrough=True), ["S1"], _repeat(S1=3),
                         tmp_path, pr.ExportGuard(), 2, 0.0)
    await client.aclose()
    message = str(info.value)
    assert "invalid_request" in message and "FABRIX_NATIVE_URL" in message
    assert "설정되지" not in message  # 프록시 메시지 원문은 싣지 않는다(식별자만)
    assert not (tmp_path / pr.RESULTS).exists()


def test_runner_main_returns_1_on_invalid_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = pr.outcome_from_body(400, 1, {"error": {
        "code": "invalid_request",
        "message": "fabrix_proxy_options.repair_max는 0~3 정수여야 한다"}})
    assert bad.hints == ("fabrix_proxy_options", "repair_max")

    async def fake_connect(url: str, token: str, guard: Any) -> tuple[Any, str]:
        return _ScriptedClient([bad]), ALIAS

    monkeypatch.setattr(pr, "load_settings", lambda: make_settings(fabrix_proxy_poc_mode=True))
    monkeypatch.setattr(pr, "connect", fake_connect)
    code = pr.main(["run", "--only", "S1", "--repeat", "1", "--repair-max", "5",
                    "--out", str(tmp_path / "o")])
    assert code == 1
    assert "repair_max" in capsys.readouterr().err


async def test_content_filter_is_recorded_and_continues(tmp_path: Path) -> None:
    blocked = pr.outcome_from_body(400, 1, {"error": {"code": "content_filter", "message": "x"}})
    client = _ScriptedClient([blocked])
    await pr.measure(client, ALIAS, _opts(), ["S1"], _repeat(S1=2),  # type: ignore[arg-type]
                     tmp_path, pr.ExportGuard(), 1, 0.0)
    rows = pr.load_results(tmp_path)
    assert client.calls == 2 and [r["error_code"] for r in rows] == ["content_filter"] * 2


# ─────────────────────────── ① 러너 판정 ───────────────────────────


def _plain(text: str, call_like: bool) -> pr.CallOutcome:
    diag = {"emulation": "none", "attempts": [
        {"kind": "text", "reasons": [], "called_names": [], "example_tool_called": False,
         "call_like_text": call_like}]}
    return pr.CallOutcome(200, 1, None, diag, {"role": "assistant", "content": text})


async def test_runner_counts_call_like_text_as_format_failure(tmp_path: Path) -> None:
    client = _ScriptedClient([_plain(BARE_CALL, True)])
    await pr.measure(client, ALIAS, _opts(), ["S1", "S4", "S5"],  # type: ignore[arg-type]
                     _repeat(S1=1, S4=1, S5=1), tmp_path, pr.ExportGuard(), 1, 0.0)
    rows = pr.load_results(tmp_path)
    s1 = next(r for r in rows if r["scenario"] == "S1")
    assert s1["call_like_text"] is True and s1["format_ok"] is False
    assert s1["failure_type"] == "format" and s1["selection_ok"] is False
    s4 = next(r for r in rows if r["scenario"] == "S4")
    assert s4["false_positive"] is True and s4["failure_type"] == "format"
    s5 = next(r for r in rows if r["scenario"] == "S5" and r["record"] == "case")
    assert s5["completed"] is False and s5["failure_type"] == "format"
    s5_calls = [r for r in rows if r["scenario"] == "S5" and r["record"] == "call"]
    assert len(s5_calls) == 1  # 최종 답으로 받지 않고 거기서 끝난다

    metrics = pr.label_metrics(rows)
    assert metrics["call_like"] == 3
    assert metrics["format_s123"] == (0, 1) and metrics["fp_s4"] == (1, 1)
    summary = pr.render_summary(rows, None, None, pr.Recommendation())
    assert "호출 모양 평문" in summary and "호출 모양 평문(태그 없는 호출 JSON" in summary


async def test_runner_plain_text_still_passes_s4(tmp_path: Path) -> None:
    client = _ScriptedClient([_plain("No tool needed: 42.", False)])
    await pr.measure(client, ALIAS, _opts(), ["S4"], _repeat(S4=1),  # type: ignore[arg-type]
                     tmp_path, pr.ExportGuard(), 1, 0.0)
    (row,) = pr.load_results(tmp_path)
    assert row["false_positive"] is False and row["format_ok"] is True
    assert row["call_like_text"] is False and row["failure_type"] is None


# ─────────────────────────── ⑤ 예외 봉투 · parameters 검증 ───────────────────────────


async def test_unexpected_exception_becomes_500_envelope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    resp = await _post(FakeUpstream([RuntimeError("SECRET_BODY_TEXT_88")]), chat_body())
    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "internal_error" and "RuntimeError" in error["message"]
    assert "SECRET_BODY_TEXT_88" not in resp.text
    assert all("SECRET_BODY_TEXT_88" not in r.getMessage() for r in caplog.records)


def _tool(parameters: Any) -> dict[str, Any]:
    return {"type": "function", "function": {"name": "t", "parameters": parameters}}


@pytest.mark.parametrize(
    "parameters",
    ["string", ["a"], {"type": "string"}, {"type": "object", "properties": ["city"]}],
)
async def test_bad_parameters_schema_is_400(parameters: Any) -> None:
    resp = await _post(FakeUpstream(["x"]), chat_body(tools=[_tool(parameters)]))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("parameters", [None, {}, {"properties": {}}, {"type": "object"}])
async def test_good_parameters_schema_passes(parameters: Any) -> None:
    tool = _tool(parameters)
    if parameters is None:
        del tool["function"]["parameters"]
    resp = await _post(FakeUpstream(["plain"]), chat_body(tools=[tool]))
    assert resp.status_code == 200


# ─────────────────────────── ⑥ httpx 로그 억제 ───────────────────────────


def test_create_app_quiets_http_loggers() -> None:
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.NOTSET)
    create_app(make_settings(), upstream=FakeUpstream())
    assert all(logging.getLogger(n).level == logging.WARNING for n in ("httpx", "httpcore"))
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.NOTSET)
    KBGenAIUpstream(make_settings())
    assert all(logging.getLogger(n).level == logging.WARNING for n in ("httpx", "httpcore"))


# ─────────────────────────── ⑦ litellm 비용표 ───────────────────────────


async def test_record_harness_blocks_litellm_cost_map_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LITELLM_LOCAL_MODEL_COST_MAP", raising=False)
    seen: dict[str, Any] = {}

    def fake_import(name: str) -> Any:
        seen[name] = rcr.os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP")
        raise ImportError(name)

    monkeypatch.setattr(rcr.importlib, "import_module", fake_import)
    with pytest.raises(ImportError):
        await rcr._run_litellm("http://127.0.0.1:9", None)  # type: ignore[arg-type]
    assert seen == {"litellm": "True"}


# ─────────────────────────── ⑧ 상한 ───────────────────────────


async def test_body_over_limit_by_content_length_is_400() -> None:
    upstream = FakeUpstream()
    big = json.dumps(chat_body(pad="x" * (app_module.MAX_BODY_BYTES + 1)))
    async with _client(upstream) as client:
        resp = await client.post(
            "/v1/chat/completions", content=big,
            headers={**AUTH, "Content-Type": "application/json"},
        )
        unauth = await client.post("/v1/chat/completions", content=big)
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "invalid_request"
    assert unauth.status_code == 401  # 인증이 먼저다
    assert upstream.payloads == []


async def test_body_over_limit_without_content_length_is_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "MAX_BODY_BYTES", 1024)
    data = json.dumps(chat_body(pad="x" * 4096)).encode()

    async def chunks() -> Any:
        for i in range(0, len(data), 512):
            yield data[i : i + 512]

    upstream = FakeUpstream()
    async with _client(upstream) as client:
        resp = await client.post("/v1/chat/completions", content=chunks(), headers=AUTH)
        small = await client.post("/v1/chat/completions", json=chat_body(), headers=AUTH)
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "invalid_request"
    assert small.status_code == 200


async def test_bad_content_length_is_400() -> None:
    async with _client(FakeUpstream()) as client:
        resp = await client.post(
            "/v1/chat/completions", content=b"{}",
            headers={**AUTH, "Content-Length": "abc"},
        )
    assert resp.status_code == 400


@pytest.mark.parametrize(("value", "status"), [(3, 200), (4, 400), (-1, 400)])
async def test_repair_max_option_range(value: int, status: int) -> None:
    resp = await _post(FakeUpstream([GOOD_CALL]), _poc_body(repair_max=value),
                       fabrix_proxy_poc_mode=True)
    assert resp.status_code == status


@pytest.mark.parametrize(("count", "status"), [(MAX_TOOLS, 200), (MAX_TOOLS + 1, 400)])
async def test_tools_count_limit(count: int, status: int) -> None:
    tools = [
        {"type": "function", "function": {"name": f"t{i}", "parameters": {"type": "object"}}}
        for i in range(count)
    ]
    resp = await _post(FakeUpstream(["plain"]), chat_body(tools=tools))
    assert resp.status_code == status


# ─────────────────────────── ⑨ 모델 유래 문자열 정화 ───────────────────────────


def test_schema_error_arg_names_sanitized() -> None:
    tool = {"type": "function", "function": {"name": "f", "parameters": {
        "type": "object",
        "properties": {"ok_name": {"type": "string"}},
        "additionalProperties": False,
    }}}
    text = tp.serialize_tool_call("f", {"evil\nkey": 1, "x" * 41: 2, "ok_name": 3})
    parsed = tp.parse_response(text, [tool], "auto", True)
    assert parsed.kind == "invalid"
    assert sorted(parsed.reasons) == ["schema_error:?", "schema_error:ok_name"]
    assert tp.safe_arg_name("*") == "*" and tp.safe_arg_name("a.b-c_1") == "a.b-c_1"


async def test_ignored_fields_log_is_escaped(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="fabrix_proxy")
    body = chat_body(**{"evil\nINJECTED line": 1, "y" * 80: 2})
    resp = await _post(FakeUpstream(["plain"]), body)
    assert resp.status_code == 200
    (record,) = [r for r in caplog.records if "무시한 요청 필드" in r.getMessage()]
    message = record.getMessage()
    assert "\n" not in message and "n=2" in message
    assert "y" * 41 not in message


# ─────────────────────────── ⑩ 반출물 비밀 전체 값 ───────────────────────────


def test_guard_checks_full_secret_value() -> None:
    guard = pr.ExportGuard.build([make_settings()], [])
    key = next(k for k in guard.secret_needles if k.startswith("FABRIX_API_KEY"))
    assert guard.secret_values[key] == "secret-api-key"
    # 앞 8자 조각이 다른 값이어도(조각 검사 우회) 전체 값이 있으면 걸린다
    guard.secret_needles[key] = "\x00never\x00"
    assert "secret:FABRIX_API_KEY" in guard.violations("x secret-api-key y")
    assert guard.violations("x secret-ap y") == []
