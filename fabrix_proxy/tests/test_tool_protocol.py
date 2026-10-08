"""tool_protocol — 규약 렌더 · 파서 판정표 · few-shot 가드 (plans/148 1단계 테스트 A)."""

from __future__ import annotations

import json
import re

import pytest
from conftest import TIME_TOOL, WEATHER_TOOL

from fabrix_proxy import tool_protocol as tp

TOOLS = [WEATHER_TOOL, TIME_TOOL]


def call(name: str, args: dict | str) -> str:
    return tp.serialize_tool_call(name, args)


# ─────────────────────────── 유틸 ───────────────────────────


def test_estimate_tokens_ascii_and_non_ascii() -> None:
    assert tp.estimate_tokens("") == 0
    assert tp.estimate_tokens("abcd") == 1
    assert tp.estimate_tokens("abcde") == 2
    assert tp.estimate_tokens("가나다") == 3
    assert tp.estimate_tokens("ab가") == 2  # ceil(2/4 + 1)


def test_tool_call_id_format() -> None:
    ids = {tp.new_tool_call_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(re.fullmatch(r"call_[0-9a-f]{24}", i) for i in ids)


def test_remove_llm_junk() -> None:
    assert tp.remove_llm_junk("  hi<|eot_id|><|start_header_id|>assistant ") == "hi"


# ─────────────────────────── 규약 블록 ───────────────────────────


def test_compact_schema_strips_title_and_inlines_refs() -> None:
    schema = {
        "title": "Args",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "title": {"type": "string", "title": "Title"},
            "item": {"$ref": "#/$defs/Item"},
        },
        "$defs": {
            "Item": {"title": "Item", "type": "object", "properties": {"n": {"type": "integer"}}}
        },
    }
    out = tp.compact_schema(schema)
    assert out == {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "item": {"type": "object", "properties": {"n": {"type": "integer"}}},
        },
    }


def test_compact_schema_cycle_keeps_ref() -> None:
    schema = {
        "type": "object",
        "properties": {"node": {"$ref": "#/$defs/Node"}},
        "$defs": {"Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}}},
    }
    out = tp.compact_schema(schema)
    assert out["properties"]["node"]["properties"]["child"] == {"$ref": "#/$defs/Node"}


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_render_protocol_placeholders_replaced(lang: str) -> None:
    template = tp.load_protocol_template(lang)
    for placeholder in (
        tp.PLACEHOLDER_TOOLS,
        tp.PLACEHOLDER_CHOICE_RULE,
        tp.PLACEHOLDER_PARALLEL_RULE,
    ):
        assert placeholder in template
    text = tp.render_protocol(TOOLS, "auto", True, template=template, lang=lang)
    assert "{{" not in text
    assert "get_weather" in text and "get_time" in text
    assert '"required":["city"]' in text
    assert "WeatherArgs" not in text  # title 제거
    assert "<tool_call>" in text and "<tool_response" in text


def test_render_protocol_choice_and_parallel_rules() -> None:
    template = tp.load_protocol_template("en")
    auto = tp.render_protocol(TOOLS, "auto", True, template=template, lang="en")
    required = tp.render_protocol(TOOLS, "required", True, template=template, lang="en")
    named = tp.render_protocol(
        TOOLS,
        {"type": "function", "function": {"name": "get_time"}},
        False,
        template=template,
        lang="en",
    )
    assert "must call at least one tool" not in auto
    assert "must call at least one tool" in required
    assert "must call the tool `get_time`" in named
    assert "several <tool_call> blocks" in auto
    assert "at most one tool per reply" in named


def test_validate_protocol_template_requires_tools_placeholder() -> None:
    with pytest.raises(ValueError):
        tp.validate_protocol_template("no placeholder here")


# ─────────────────────────── 이력 직렬화 ───────────────────────────


def test_serialize_turns_tool_calls_and_responses() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                },
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "get_time", "arguments": '{"tz":"UTC"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "sunny"},
        {"role": "tool", "tool_call_id": "c2", "name": "get_time", "content": "12:00"},
    ]
    turns = tp.serialize_turns(messages)
    assert [r for r, _ in turns] == ["user", "assistant", "user"]
    assert turns[1][1] == (
        'checking\n<tool_call>{"name": "get_weather", "arguments": {"city": "Paris"}}</tool_call>\n'
        '<tool_call>{"name": "get_time", "arguments": {"tz": "UTC"}}</tool_call>'
    )
    assert turns[2][1] == (
        '<tool_response name="get_weather" id="c1">sunny</tool_response>\n\n'
        '<tool_response name="get_time" id="c2">12:00</tool_response>'
    )


def test_serialized_history_roundtrips_through_parser() -> None:
    text = call("get_weather", {"city": "Paris"})
    result = tp.parse_response(text, TOOLS, "auto", True)
    assert result.kind == "tool_calls"
    assert result.tool_calls[0].arguments == {"city": "Paris"}


# ─────────────────────────── 파서 판정표 ───────────────────────────


def test_parse_single_block_with_outside_text() -> None:
    result = tp.parse_response(
        "Let me check.\n" + call("get_weather", {"city": "Paris"}), TOOLS, "auto", True
    )
    assert result.kind == "tool_calls"
    assert result.content == "Let me check."
    assert [c.name for c in result.tool_calls] == ["get_weather"]


def test_parse_block_only_content_none() -> None:
    result = tp.parse_response(call("get_weather", {"city": "Paris"}), TOOLS, "auto", True)
    assert result.content is None


def test_parse_parallel_blocks() -> None:
    text = call("get_weather", {"city": "Paris"}) + "\n" + call("get_time", {"tz": "UTC"})
    result = tp.parse_response(text, TOOLS, "auto", True)
    assert result.kind == "tool_calls"
    assert [c.name for c in result.tool_calls] == ["get_weather", "get_time"]


def test_parse_parallel_not_allowed() -> None:
    text = call("get_weather", {"city": "Paris"}) + call("get_time", {"tz": "UTC"})
    result = tp.parse_response(text, TOOLS, "auto", False)
    assert result.kind == "invalid"
    assert "parallel_not_allowed" in result.reasons


@pytest.mark.parametrize("choice", ["auto", "none"])
def test_plain_text_with_none_or_auto_is_stop(choice: str) -> None:
    result = tp.parse_response("Paris is sunny.", TOOLS, choice, True)
    assert result.kind == "text"
    assert result.content == "Paris is sunny."


def test_choice_none_keeps_blocks_as_text() -> None:
    text = call("get_weather", {"city": "Paris"})
    result = tp.parse_response(text, TOOLS, "none", True)
    assert result.kind == "text"
    assert result.content == text


@pytest.mark.parametrize(
    "choice", ["required", {"type": "function", "function": {"name": "get_time"}}]
)
def test_plain_text_with_required_or_named_is_invalid(choice: object) -> None:
    result = tp.parse_response("no tools", TOOLS, choice, True)
    assert result.kind == "invalid"
    assert result.reasons == ["missing_required_call"]


def test_named_choice_wrong_tool() -> None:
    choice = {"type": "function", "function": {"name": "get_time"}}
    result = tp.parse_response(call("get_weather", {"city": "Paris"}), TOOLS, choice, True)
    assert result.kind == "invalid"
    assert result.reasons == ["wrong_tool"]


def test_named_choice_right_tool() -> None:
    choice = {"type": "function", "function": {"name": "get_time"}}
    result = tp.parse_response(call("get_time", {"tz": "KST"}), TOOLS, choice, True)
    assert result.kind == "tool_calls"


def test_required_with_block_ok() -> None:
    result = tp.parse_response(call("get_time", {"tz": "KST"}), TOOLS, "required", True)
    assert result.kind == "tool_calls"


def test_json_error() -> None:
    result = tp.parse_response("<tool_call>{not json}</tool_call>", TOOLS, "auto", True)
    assert result.kind == "invalid"
    assert result.reasons == ["json_error"]


def test_unknown_tool() -> None:
    result = tp.parse_response(call("delete_everything", {}), TOOLS, "auto", True)
    assert result.reasons == ["unknown_tool"]
    assert result.called_names == ["delete_everything"]


def test_schema_error_missing_and_type() -> None:
    missing = tp.parse_response(call("get_weather", {"days": 2}), TOOLS, "auto", True)
    assert missing.reasons == ["schema_error:city"]
    wrong_type = tp.parse_response(
        call("get_weather", {"city": "Paris", "days": "two"}), TOOLS, "auto", True
    )
    assert wrong_type.reasons == ["schema_error:days"]
    enum = tp.parse_response(call("get_time", {"tz": "PST"}), TOOLS, "auto", True)
    assert enum.reasons == ["schema_error:tz"]


def test_mixed_valid_and_invalid_block_is_invalid() -> None:
    text = call("get_weather", {"city": "Paris"}) + call("nope", {})
    result = tp.parse_response(text, TOOLS, "auto", True)
    assert result.kind == "invalid"
    assert result.reasons == ["unknown_tool"]


def test_arguments_as_json_string() -> None:
    text = (
        '<tool_call>{"name": "get_weather", "arguments": "{\\"city\\": \\"Paris\\"}"}</tool_call>'
    )
    result = tp.parse_response(text, TOOLS, "auto", True)
    assert result.kind == "tool_calls"
    assert result.tool_calls[0].arguments == {"city": "Paris"}


def test_bad_arguments_string_is_json_error() -> None:
    text = '<tool_call>{"name": "get_weather", "arguments": "{city"}</tool_call>'
    result = tp.parse_response(text, TOOLS, "auto", True)
    assert result.reasons == ["json_error"]
    assert result.called_names == ["get_weather"]


def test_fence_inside_block() -> None:
    text = (
        '<tool_call>\n```json\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n```\n'
        "</tool_call>"
    )
    result = tp.parse_response(text, TOOLS, "auto", True)
    assert result.kind == "tool_calls"


def test_unclosed_trailing_block_is_validated() -> None:
    text = '<tool_call>{"name": "get_weather", "arguments": {"city": "Paris"}}'
    assert tp.parse_response(text, TOOLS, "auto", True).kind == "tool_calls"
    broken = tp.parse_response('<tool_call>{"name": "get_weather", "argu', TOOLS, "auto", True)
    assert broken.kind == "invalid"


def test_aux_fenced_name_arguments() -> None:
    text = '```json\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n```'
    result = tp.parse_response(text, TOOLS, "auto", True)
    assert result.kind == "tool_calls"
    assert result.content is None


def test_aux_fenced_legacy_tool_name() -> None:
    text = '```json\n{"tool_name": "get_time", "arguments": {"tz": "UTC"}}\n```'
    result = tp.parse_response(text, TOOLS, "auto", True)
    assert result.kind == "tool_calls"
    assert result.tool_calls[0].name == "get_time"


def test_fenced_non_call_json_is_text() -> None:
    text = '```json\n{"answer": 42}\n```'
    assert tp.parse_response(text, TOOLS, "auto", True).kind == "text"


def test_unfenced_bare_json_is_text() -> None:
    text = '{"name": "get_weather", "arguments": {"city": "Paris"}}'
    assert tp.parse_response(text, TOOLS, "auto", True).kind == "text"


def test_reduced_validation_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tp, "_validator_for", None)
    ok = tp.parse_response(call("get_weather", {"city": "Paris"}), TOOLS, "auto", True)
    assert ok.kind == "tool_calls"
    missing = tp.parse_response(call("get_weather", {}), TOOLS, "auto", True)
    assert missing.reasons == ["schema_error:city"]
    wrong_type = tp.parse_response(call("get_weather", {"city": 3}), TOOLS, "auto", True)
    assert wrong_type.reasons == ["schema_error:city"]
    bool_not_int = tp.parse_response(
        call("get_weather", {"city": "x", "days": True}), TOOLS, "auto", True
    )
    assert bool_not_int.reasons == ["schema_error:days"]


def test_example_tool_called_flag() -> None:
    result = tp.parse_response(
        call("example_lookup_item", {"code": "A"}), TOOLS, "auto", True, {"example_lookup_item"}
    )
    assert result.kind == "invalid"
    assert result.example_tool_called is True


def test_repair_message_mentions_reasons_and_tools() -> None:
    result = tp.parse_response(call("nope", {}) + call("get_weather", {}), TOOLS, "required", False)
    msg = tp.build_repair_message(result, TOOLS, "required", False, "en")
    assert "nope" in msg and "'city'" in msg
    assert "get_weather, get_time" in msg
    assert "<tool_call>" in msg
    assert "must call at least one tool" in msg
    assert "at most one tool per reply" in msg
    ko = tp.build_repair_message(result, TOOLS, "required", False, "ko")
    assert "쓸 수 있는 도구" in ko


# ─────────────────────────── few-shot 가드 ───────────────────────────

DOMAIN_WORDS = (
    "폴스타",
    "polestar",
    "제니퍼",
    "jennifer",
    "itam",
    "cmm_",
    "알람",
    "alarm",
    "서버",
    "server",
    "hostname",
    "호스트",
    "cpu",
    "db2",
    "postgres",
)


def _expected_calls(example: dict) -> list[tuple[str, dict]]:
    return [(c["name"], c["arguments"]) for c in example["expected"].get("tool_calls") or []]


def test_default_fewshot_has_all_patterns() -> None:
    data = tp.load_fewshot()
    assert sorted(ex["pattern"] for ex in data["examples"]) == sorted(tp.FEWSHOT_PATTERNS)


@pytest.mark.parametrize("mode", ["static", "dynamic"])
def test_fewshot_roundtrip_through_parser(mode: str) -> None:
    """렌더된 예시의 기대 답을 파서에 넣으면 기대 tool_calls가 그대로 나온다(왕복)."""
    fs = tp.select_fewshot(tp.load_fewshot(), mode, TOOLS, True)
    assert fs is not None
    system_block = tp.render_fewshot_block(fs, "en")
    contents_turns = tp.fewshot_contents_turns(fs)
    contents_text = "\n".join(text for _, text in contents_turns)
    for ex in fs.examples:
        turns = tp.example_turns(ex)
        role, answer = turns[-1]
        assert role == "assistant"
        # 두 배치 모두 같은 표기로 들어간다
        assert answer in system_block
        assert answer in contents_text
        result = tp.parse_response(answer, fs.tools, "auto", True)
        if ex["expected"].get("tool_calls"):
            assert result.kind == "tool_calls"
            assert [(c.name, c.arguments) for c in result.tool_calls] == _expected_calls(ex)
        else:
            assert result.kind == "text"
            assert result.content == ex["expected"]["text"]


def test_fewshot_names_disjoint_from_request_tools() -> None:
    fs = tp.select_fewshot(tp.load_fewshot(), "static", TOOLS, True)
    assert fs is not None
    assert fs.example_tool_names.isdisjoint(tp.tool_names(TOOLS))


def test_fewshot_rename_on_collision() -> None:
    clash = {
        "type": "function",
        "function": {"name": "example_lookup_item", "parameters": {"type": "object"}},
    }
    request_tools = [clash, WEATHER_TOOL]
    fs = tp.select_fewshot(tp.load_fewshot(), "static", request_tools, True)
    assert fs is not None
    assert "example_lookup_item_sample" in fs.example_tool_names
    assert fs.example_tool_names.isdisjoint(tp.tool_names(request_tools))
    block = tp.render_fewshot_block(fs, "en")
    assert '"name": "example_lookup_item"' not in block
    assert '"name": "example_lookup_item_sample"' in block
    assert 'name="example_lookup_item_sample"' in block  # tool_response 이름도 일관 치환
    # 원본 데이터는 바뀌지 않는다
    assert "example_lookup_item" in tp.tool_names(tp.load_fewshot()["tools"])


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("placement", ["system", "contents"])
def test_fewshot_block_has_no_domain_words(lang: str, placement: str) -> None:
    fs = tp.select_fewshot(tp.load_fewshot(), "static", TOOLS, True)
    assert fs is not None
    if placement == "system":
        text = tp.render_fewshot_block(fs, lang)
    else:
        text = json.dumps(tp.fewshot_contents_turns(fs), ensure_ascii=False)
    low = text.lower()
    assert [w for w in DOMAIN_WORDS if w in low] == []


def test_parallel_false_excludes_parallel_pattern() -> None:
    for mode in ("static", "dynamic"):
        fs = tp.select_fewshot(tp.load_fewshot(), mode, TOOLS, False)
        assert fs is not None
        assert "parallel" not in [ex["pattern"] for ex in fs.examples]
        with_parallel = tp.select_fewshot(tp.load_fewshot(), mode, TOOLS, True)
        assert with_parallel is not None
        assert "parallel" in [ex["pattern"] for ex in with_parallel.examples]


def test_fewshot_none_mode_is_none() -> None:
    assert tp.select_fewshot(tp.load_fewshot(), "none", TOOLS, True) is None
    assert tp.select_fewshot(tp.load_fewshot(), "static", [], True) is None


def test_dynamic_fewshot_uses_request_tools_with_placeholders() -> None:
    fs = tp.select_fewshot(None, "dynamic", TOOLS, True)
    assert fs is not None
    assert fs.example_tool_names == frozenset()
    single = next(ex for ex in fs.examples if ex["pattern"] == "single")
    assert single["expected"]["tool_calls"] == [
        {"name": "get_weather", "arguments": {"city": "<value>"}}
    ]
    parallel = next(ex for ex in fs.examples if ex["pattern"] == "parallel")
    assert parallel["expected"]["tool_calls"][1] == {"name": "get_time", "arguments": {"tz": "UTC"}}


def test_validate_fewshot_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError):
        tp.validate_fewshot([])
    with pytest.raises(ValueError):
        tp.validate_fewshot(
            {"tools": [], "examples": [{"pattern": "bogus", "messages": [], "expected": {}}]}
        )
