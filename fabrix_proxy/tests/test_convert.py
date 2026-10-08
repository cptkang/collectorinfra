"""convert — OpenAI 요청 → KBGenAI 페이로드 변환표 (plans/148 1단계 테스트 A)."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ALIAS, TIME_TOOL, WEATHER_TOOL, chat_body
from fabrix_proxy.convert import (
    EmulationOptions,
    InvalidRequestError,
    build_llm_config,
    build_payload,
    native_body,
    parse_chat_request,
    prepare_request,
    repair_turns,
    resolve_options,
    turns_to_contents,
)

from fabrix_proxy import tool_protocol as tp

# 이식 원본 src/clients/fabrix_kbgenai.py `_get_payload`의 키·순서
ORIGINAL_KEYS = [
    "modelId",
    "contents",
    "isStream",
    "isRagOn",
    "executeRagFinalAnswer",
    "executeRagStandaloneQuery",
    "systemPrompt",
]
TEMPLATES = {lang: tp.load_protocol_template(lang) for lang in ("en", "ko")}


def options(**changes: Any) -> EmulationOptions:
    base = EmulationOptions(
        contents_mode="turns",
        protocol_lang="en",
        protocol_template=TEMPLATES["en"],
        fewshot="static",
        fewshot_placement="system",
        fewshot_data=tp.load_fewshot(),
        repair_max=1,
        passthrough=False,
    )
    return resolve_options(base, changes, TEMPLATES, tp.load_fewshot()) if changes else base


def payload_for(
    body: dict[str, Any], opts: EmulationOptions | None = None, llm: dict | None = None
) -> dict:
    req = parse_chat_request(body, [ALIAS], poc_mode=False)
    opts = opts or options()
    prepared = prepare_request(req, opts)
    return build_payload(
        prepared, model_id="asset-1", contents_mode=opts.contents_mode, llm_config=llm
    )


# ─────────────────────────── 페이로드 형태 ───────────────────────────


def test_payload_keys_and_fixed_values_match_original() -> None:
    payload = payload_for(chat_body(tools=None))
    assert list(payload) == ORIGINAL_KEYS
    assert payload["modelId"] == "asset-1"
    assert payload["isStream"] is False
    assert payload["isRagOn"] is False
    assert payload["executeRagFinalAnswer"] is False
    assert payload["executeRagStandaloneQuery"] is False


def test_llm_config_merge_and_omission() -> None:
    assert build_llm_config({}, None, None) is None
    assert build_llm_config({"top_k": 5}, 0.2, None) == {"top_k": 5, "temperature": 0.2}
    assert build_llm_config({"temperature": 0.9}, 0.1, 0.5) == {"temperature": 0.1, "top_p": 0.5}
    payload = payload_for(chat_body(), llm={"temperature": 0.1})
    assert list(payload) == [*ORIGINAL_KEYS, "llmConfig"]
    assert payload["llmConfig"] == {"temperature": 0.1}


# ─────────────────────────── systemPrompt ───────────────────────────


def test_system_and_developer_join_into_system_prompt() -> None:
    body = chat_body(
        tools=None,
        messages=[
            {"role": "system", "content": "S1"},
            {"role": "developer", "content": [{"type": "text", "text": "D1"}]},
            {"role": "user", "content": "q"},
        ],
    )
    payload = payload_for(body)
    assert payload["systemPrompt"] == "S1\n\nD1"
    assert payload["contents"] == ["", "q"]


def test_protocol_and_fewshot_only_with_tools_and_choice_not_none() -> None:
    with_tools = payload_for(
        chat_body(messages=[{"role": "system", "content": "S"}, {"role": "user", "content": "q"}])
    )
    assert with_tools["systemPrompt"].startswith("S\n\n# Tools")
    assert "# Format examples" in with_tools["systemPrompt"]
    none_choice = payload_for(chat_body(tool_choice="none"))
    assert "<tool_call>" not in none_choice["systemPrompt"]
    no_tools = payload_for(chat_body(tools=[]))
    assert no_tools["systemPrompt"] == ""


def test_fewshot_none_has_no_example_block() -> None:
    payload = payload_for(chat_body(), options(fewshot="none"))
    assert "# Tools" in payload["systemPrompt"]
    assert "# Format examples" not in payload["systemPrompt"]
    assert "example_lookup_item" not in payload["systemPrompt"]


def test_fewshot_contents_placement_inserts_fake_turns() -> None:
    payload = payload_for(chat_body(), options(fewshot_placement="contents"))
    assert "# Format examples" not in payload["systemPrompt"]
    contents = payload["contents"]
    assert contents[0] == ""
    assert contents[1] == "What is the price of item A-100?"
    assert "example_lookup_item" in contents[2]
    assert contents[-1] == "weather in Paris?"
    # user/assistant 교대가 유지된다(빈 첫 원소 뒤 user부터)
    assert len(contents) % 2 == 0


# ─────────────────────────── contents 규칙 ───────────────────────────

HISTORY = [
    {"role": "user", "content": "u1"},
    {"role": "user", "content": "u2"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"P"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "sunny"},
    {"role": "tool", "tool_call_id": "c2", "name": "get_time", "content": "noon"},
]


def test_turns_mode_merges_and_serializes() -> None:
    payload = payload_for(chat_body(messages=HISTORY))
    assert payload["contents"] == [
        "",
        "u1\n\nu2",
        '<tool_call>{"name": "get_weather", "arguments": {"city": "P"}}</tool_call>',
        '<tool_response name="get_weather" id="c1">sunny</tool_response>\n\n'
        '<tool_response name="get_time" id="c2">noon</tool_response>',
    ]


def test_turns_mode_assistant_first() -> None:
    assert turns_to_contents([("assistant", "hi"), ("user", "q")], "turns") == ["hi", "q"]
    assert turns_to_contents([], "turns") == [""]


def test_transcript_mode() -> None:
    payload = payload_for(chat_body(messages=HISTORY), options(contents_mode="transcript"))
    contents = payload["contents"]
    assert len(contents) == 2 and contents[0] == ""
    assert contents[1].startswith("[user]\nu1\n\nu2\n\n[assistant]\n<tool_call>")
    assert "[user]\n<tool_response" in contents[1]


def test_repair_turns_append_after_original() -> None:
    req = parse_chat_request(chat_body(), [ALIAS], poc_mode=False)
    prepared = prepare_request(req, options())
    payload = build_payload(
        prepared,
        model_id="m",
        contents_mode="turns",
        llm_config=None,
        extra_turns=repair_turns("bad output", "fix it"),
    )
    assert payload["contents"][-3:] == ["weather in Paris?", "bad output", "fix it"]


# ─────────────────────────── 요청 검증 ───────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        chat_body(model="gpt-4"),
        chat_body(messages=[]),
        chat_body(messages=[{"role": "robot", "content": "x"}]),
        chat_body(
            messages=[
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}
            ]
        ),
        chat_body(tools=[{"type": "retrieval"}]),
        chat_body(tool_choice={"type": "function", "function": {"name": "missing"}}),
        chat_body(tool_choice="sometimes"),
        chat_body(tools=None, tool_choice="required"),
        chat_body(messages=[{"role": "tool", "content": "x"}]),
        chat_body(parallel_tool_calls="no"),
        chat_body(temperature="hot"),
    ],
)
def test_invalid_requests(body: dict) -> None:
    with pytest.raises(InvalidRequestError):
        parse_chat_request(body, [ALIAS], poc_mode=False)


def test_unknown_fields_ignored_by_name() -> None:
    body = chat_body(
        max_tokens=10, extra_body={"x": 1}, stream_options={}, user="u", some_new_field=1
    )
    req = parse_chat_request(body, [ALIAS], poc_mode=False)
    assert req.ignored_fields == [
        "extra_body",
        "max_tokens",
        "some_new_field",
        "stream_options",
        "user",
    ]


def test_options_field_ignored_when_poc_off() -> None:
    body = chat_body(fabrix_proxy_options={"fewshot": "none"})
    off = parse_chat_request(body, [ALIAS], poc_mode=False)
    assert off.options is None and "fabrix_proxy_options" in off.ignored_fields
    on = parse_chat_request(body, [ALIAS], poc_mode=True)
    assert on.options == {"fewshot": "none"}


def test_tool_choice_defaults() -> None:
    assert parse_chat_request(chat_body(), [ALIAS], False).tool_choice == "auto"
    assert parse_chat_request(chat_body(tools=None), [ALIAS], False).tool_choice == "none"


def test_resolve_options_overrides_and_rejects() -> None:
    opts = options(protocol_lang="ko", repair_max=0, fewshot="dynamic")
    assert opts.protocol_template == TEMPLATES["ko"]
    assert opts.repair_max == 0 and opts.fewshot == "dynamic"
    inline = options(protocol_text="CUSTOM {{TOOLS}}")
    assert inline.protocol_template == "CUSTOM {{TOOLS}}"
    for bad in (
        {"fewshot": "lots"},
        {"protocol_text": "no tools"},
        {"repair_max": -1},
        {"bogus": 1},
        {"fewshot_examples": {"tools": 1}},
        {"passthrough": "yes"},
    ):
        with pytest.raises(InvalidRequestError):
            options(**bad)


def test_native_body_replaces_model_and_drops_options() -> None:
    raw = chat_body(
        stream=True,
        fabrix_proxy_options={"passthrough": True},
        stream_options={"include_usage": True},
    )
    body = native_body(raw, "native-model")
    assert body["model"] == "native-model"
    assert body["stream"] is False
    assert "fabrix_proxy_options" not in body and "stream_options" not in body
    assert body["tools"] == [WEATHER_TOOL, TIME_TOOL]
