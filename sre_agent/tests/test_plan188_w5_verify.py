"""plans/188 W5(C8) 독립 검증 — Gemma 도구 호출 마크업 클라이언트 측 파싱.

가짜 LLM만 쓴다(실 LLM·과금 API 없음). 실 holmes `DefaultLLM.completion`을 건드리는 테스트는
`restore_completion` fixture로 원복한다.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from typing import Any

import pytest
from holmes.core.llm import DefaultLLM
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Function,
    Message,
    ModelResponse,
    Usage,
)
from sre_agent.infrastructure import log_masking
from sre_agent.infrastructure import tool_call_markup_guard as guard_mod
from sre_agent.infrastructure.llm_message_guard import install_system_message_guard
from sre_agent.infrastructure.tool_call_markup_guard import (
    MarkupParseError,
    install_tool_call_markup_guard,
    parse_gemma_tool_calls,
    rewrite_tool_call_markup,
)

Q = '<|"|>'
_LOGGER = guard_mod.__name__


def _block(name: str, args: str) -> str:
    return f"<|tool_call>call:{name}{args}<tool_call|>"


def _resp(content: Any, tool_calls: list | None = None) -> ModelResponse:
    return ModelResponse(
        id="chatcmpl-x",
        model="m",
        choices=[Choices(finish_reason="stop",
                         message=Message(content=content, tool_calls=tool_calls))],
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _parsed(content: str) -> list[tuple[str, Any]]:
    out = rewrite_tool_call_markup(_resp(content))
    return [(t.function.name, json.loads(t.function.arguments))
            for t in out.choices[0].message.tool_calls]


@pytest.fixture
def restore_completion():
    saved = DefaultLLM.__dict__["completion"]
    DefaultLLM.completion = inspect.unwrap(saved)  # type: ignore[method-assign]
    yield DefaultLLM
    DefaultLLM.completion = saved  # type: ignore[method-assign]


# ── 1. 파서 견고성 ────────────────────────────────────────────────


@pytest.mark.parametrize("s", [
    "a:b,c{d}e",
    "<tool_call|> 닫는 표식",
    "<|tool_call>call:x{a:1}<tool_call|> 여는 표식",
    'back\\slash \\" \\n "dq"',
    "줄\n바꿈\r\n탭\t",
    "",
    "[1,2]",
])
def test_string_payload_preserved_verbatim(s):
    assert _parsed(_block("t", "{v:" + Q + s + Q + "}")) == [("t", {"v": s})]


def test_open_marker_inside_string_does_not_start_new_block():
    content = _block("t", "{v:" + Q + "<|tool_call>call:evil{x:1}<tool_call|>" + Q + "}")
    assert [n for n, _ in _parsed(content)] == ["t"]


@pytest.mark.parametrize("num,expected", [
    ("0", 0), ("-0", 0), ("-12", -12), ("1.5", 1.5), ("1e5", 1e5), ("-2.5E-3", -2.5e-3),
])
def test_number_edges_ok(num, expected):
    assert _parsed(_block("t", "{n:" + num + "}")) == [("t", {"n": expected})]


@pytest.mark.parametrize("num", ["1.5.3", "01", "-", "1.", ".5", "+1", "1abc", "truex",
                                 "NaN", "Infinity", "None", "True"])
def test_number_or_literal_garbage_returns_original(num, caplog):
    r = _resp(_block("t", "{n:" + num + "}"))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert rewrite_tool_call_markup(r) is r
    assert any("해석 실패" in rec.getMessage() for rec in caplog.records)


def test_deep_nesting_recursion_error_is_swallowed_to_original(caplog):
    depth = 5000
    content = _block("t", "{a:" + "[" * depth + "]" * depth + "}")
    r = _resp(content)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert rewrite_tool_call_markup(r) is r
    assert any("해석 실패" in rec.getMessage() for rec in caplog.records)


def test_large_input_is_linear_time():
    """블록 수천 개·긴 문자열에서도 이차 시간이 아니다(느슨한 상한)."""
    many = "".join(_block("t", f"{{i:{i},s:{Q}xx{Q}}}") for i in range(5000))
    t0 = time.perf_counter()
    calls, rest = parse_gemma_tool_calls(many)
    assert len(calls) == 5000 and rest == ""
    big = _block("t", "{s:" + Q + "x" * 2_000_000 + Q + "}")
    calls, _ = parse_gemma_tool_calls(big)
    assert len(calls[0][1]["s"]) == 2_000_000
    assert time.perf_counter() - t0 < 5.0


def test_large_unterminated_string_fails_fast():
    t0 = time.perf_counter()
    with pytest.raises(MarkupParseError):
        parse_gemma_tool_calls("<|tool_call>call:t{s:" + Q + "x" * 2_000_000 + "}<tool_call|>")
    assert time.perf_counter() - t0 < 2.0


def test_duplicate_keys_return_original():
    # 교정 반영: 중복 키는 해석 실패 → 부분 변환 없이 원본 그대로(W4 방어로).
    r = _resp(_block("t", "{a:1,a:2}"))
    assert rewrite_tool_call_markup(r) is r


def test_narrative_between_blocks_and_surrounding_whitespace():
    content = "  앞 서술\n" + _block("a", "{x:1}") + "\n 사이 \n" + _block("b", "{}") + "  \n"
    out = rewrite_tool_call_markup(_resp(content))
    msg = out.choices[0].message
    assert [t.function.name for t in msg.tool_calls] == ["a", "b"]
    assert msg.content == "앞 서술\n\n 사이"
    assert out.choices[0].finish_reason == "tool_calls"


@pytest.mark.parametrize("name", ["polestar_incident_alarms", "a.b-c", "_x", "TodoWrite", "x9"])
def test_valid_names(name):
    assert _parsed(_block(name, "{}")) == [(name, {})]


@pytest.mark.parametrize("content", [
    "<|tool_call>call:9x{}<tool_call|>",          # 숫자 시작
    "<|tool_call>call:도구{}<tool_call|>",         # 한글 시작
    "<|tool_call>call:x {}<tool_call|>",          # 이름과 { 사이 공백
    "<|tool_call>call:x{}",                        # 닫는 표식 없음(길이 잘림)
    "<|tool_call>call:x{a:1}<tool_call|> 꼬리 <tool_call>",   # 블록 밖 잔존 마크업
    "앞 " + Q + " " + _block("x", "{}"),           # 블록 밖 구분자
    "<|tool_call>call:x[1]<tool_call|>",           # 인자가 객체 아님
    "<|tool_call>call:x{a:1,}<tool_call|>",        # 끝 쉼표
    "<|tool_call>call:x{a:1}}<tool_call|>",        # 여분 닫는 괄호
    "<|tool_call>x{a:1}<tool_call|>",              # call: 없음
])
def test_malformed_returns_identity(content):
    r = _resp(content)
    assert rewrite_tool_call_markup(r) is r


def test_partial_failure_is_not_partial_conversion():
    r = _resp(_block("ok", "{a:1}") + _block("bad", "{a:}"))
    assert rewrite_tool_call_markup(r) is r


# ── 3. 정상 경로 불변 ─────────────────────────────────────────────


def test_existing_tool_calls_identity_even_with_markup():
    tc = [ChatCompletionMessageToolCall(id="c1", type="function",
                                        function=Function(name="x", arguments="{}"))]
    r = _resp(_block("t", "{}"), tool_calls=tc)
    assert rewrite_tool_call_markup(r) is r


def test_list_content_identity():
    r = _resp(None)
    r.choices[0].message.content = [{"type": "text", "text": _block("t", "{}")}]  # type: ignore
    assert rewrite_tool_call_markup(r) is r


def test_empty_choices_identity():
    r = ModelResponse(id="x", model="m", choices=[])
    assert rewrite_tool_call_markup(r) is r


def test_input_object_not_mutated_and_hidden_params_kept():
    content = _block("t", "{a:1}")
    r = _resp(content)
    r._hidden_params["response_cost"] = 0.5
    out = rewrite_tool_call_markup(r)
    assert out is not r
    assert r.choices[0].message.content == content
    assert not r.choices[0].message.tool_calls
    assert r.choices[0].finish_reason == "stop"
    assert out._hidden_params.get("response_cost") == 0.5
    assert out.usage.total_tokens == 2


def test_holmes_dump_shape():
    out = rewrite_tool_call_markup(_resp(_block("t", "{a:[1,{b:" + Q + "x" + Q + "}]}")))
    d = out.choices[0].message.model_dump(exclude_defaults=True, exclude_unset=True,
                                          exclude_none=True)
    assert d["role"] == "assistant"
    assert "content" not in d
    (tc,) = d["tool_calls"]
    assert tc["type"] == "function"
    assert json.loads(tc["function"]["arguments"]) == {"a": [1, {"b": "x"}]}


# ── 2. 몽키패치 순서·멱등·원복 ─────────────────────────────────────


@pytest.mark.parametrize("order", ["system_first", "markup_first"])
def test_reinstall_after_restore_reinstalls(restore_completion, order):
    """원복 fixture 뒤에 다시 설치하면 True — 표식이 원본 함수에 새지 않는다."""
    base = DefaultLLM.completion
    fns = ([install_system_message_guard, install_tool_call_markup_guard]
           if order == "system_first"
           else [install_tool_call_markup_guard, install_system_message_guard])
    assert [f() for f in fns] == [True, True]
    assert not hasattr(base, guard_mod._GUARD_FLAG)
    DefaultLLM.completion = base  # type: ignore[method-assign]
    assert install_tool_call_markup_guard() is True


def test_restore_fixture_leaves_original_unflagged(restore_completion):
    install_tool_call_markup_guard()
    assert getattr(DefaultLLM.completion, guard_mod._GUARD_FLAG, False)
    assert not getattr(inspect.unwrap(DefaultLLM.completion), guard_mod._GUARD_FLAG, False)


def test_wrapper_passes_kwargs_through(restore_completion):
    seen: dict = {}

    def base(self, messages, tools=None, tool_choice=None, **kw):  # noqa: ANN001, ANN202
        seen.update(tools=tools, tool_choice=tool_choice, kw=kw)
        return _resp("plain")

    DefaultLLM.completion = base  # type: ignore[method-assign]
    install_tool_call_markup_guard()
    r = DefaultLLM.completion(None, [], tools=[{"x": 1}], tool_choice="auto", stream=False)
    assert r.choices[0].message.content == "plain"
    assert seen["tools"] == [{"x": 1}] and seen["kw"] == {"stream": False}


# ── 5. tools 없는 호출(압축·요약) ──────────────────────────────────


def test_tools_less_call_is_not_converted(restore_completion):
    """교정 반영: holmes 압축(compaction)·요약처럼 tools 없이 부른 호출은 변환하지 않는다.

    압축은 `response_message.model_dump(...)`를 압축 대화에 그대로 넣고 요약은 content만 읽으므로,
    변환하면 짝 없는 assistant `tool_calls`가 남고 요약 content가 사라진다 — 원본 그대로여야 한다.
    """
    def base(self, messages, *a, **kw):  # noqa: ANN001, ANN202
        return _resp(_block("t", "{a:1}"))

    DefaultLLM.completion = base  # type: ignore[method-assign]
    install_tool_call_markup_guard()
    out = DefaultLLM.completion(None, [{"role": "user", "content": "summarize"}], drop_params=True)
    msg = out.choices[0].message
    assert msg.tool_calls is None and msg.content == _block("t", "{a:1}")


@pytest.mark.parametrize(("args", "kwargs", "converted"), [
    (([{"role": "user"}], [{"type": "function"}], "auto"), {}, True),     # 위치 인자
    (([{"role": "user"}],), {"tools": [{"type": "function"}], "tool_choice": "auto"}, True),
    (([{"role": "user"}], [{"type": "function"}], "none"), {}, False),
    (([{"role": "user"}], []), {"tool_choice": "auto"}, False),           # 빈 tools
    (([{"role": "user"}], [{"type": "function"}]), {}, False),            # tool_choice 미지정
])
def test_tools_condition_positional_and_keyword(restore_completion, args, kwargs, converted):
    """팀 리드 보충: tools·tool_choice를 위치·키워드 어느 쪽으로 넘겨도 같은 조건으로 판정한다."""
    raw = _resp(_block("t", "{a:1}"))

    def base(self, *a, **kw):  # noqa: ANN001, ANN202
        return raw

    DefaultLLM.completion = base  # type: ignore[method-assign]
    install_tool_call_markup_guard()
    out = DefaultLLM.completion(None, *args, **kwargs)
    if converted:
        assert out is not raw and out.choices[0].message.tool_calls[0].function.name == "t"
    else:
        assert out is raw


# ── 6. 로그 비밀 가림 ─────────────────────────────────────────────


@pytest.mark.parametrize("secret_arg", [
    "api_key:" + Q + "sk-SECRET123" + Q,
    Q + "password" + Q + ":" + Q + "SECRET123" + Q,
    "token:" + Q + "tokSECRET123" + Q,
])
def test_warning_masked_by_record_factory(secret_arg, caplog):
    r = _resp(_block("t", "{" + secret_arg + ",bad:}"))
    orig = logging.getLogRecordFactory()
    try:
        log_masking.install_log_record_factory()
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert rewrite_tool_call_markup(r) is r
    finally:
        logging.setLogRecordFactory(orig)
    msgs = [x.getMessage() for x in caplog.records if "해석 실패" in x.getMessage()]
    assert len(msgs) == 1
    assert "SECRET123" not in msgs[0]
    assert Q not in msgs[0]


def test_info_log_has_names_only(caplog):
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        rewrite_tool_call_markup(_resp(_block("t", "{password:" + Q + "SECRET123" + Q + "}")))
    text = " ".join(x.getMessage() for x in caplog.records)
    assert "SECRET123" not in text and "'t'" in text
