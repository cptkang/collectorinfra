"""조사 LLM 응답의 Gemma 도구 호출 마크업을 `tool_calls`로 변환한다 (plans/188 C8).

**왜 필요한가** (폐쇄망 실측 2026-10-19):
사내 vLLM(Gemma 4 계열)의 `--tool-call-parser`가 모델 표기와 맞지 않으면 도구 호출이
`tool_calls`로 파싱되지 않고 content 본문(Gemma 블록 — 여는 표식 · `call:이름{인자}` · 닫는 표식,
문자열은 전용 구분자로 감쌈)으로 돌아온다. 표식 리터럴은 `domain/answer_markup.py`의
`GEMMA_*` 상수가 단일 출처다(이 모듈에 다시 쓰지 않는다).

holmes는 `tool_calls`가 비면 그 본문을 최종 답으로 받아 조사가 1턴에서 끝난다(W4는 그 답을
미결로 종결하는 방어뿐). 운영 측 파서 교정이 어려워 클라이언트 측에서 응답을 후처리한다 —
근본 조치(파서 교정)는 여전히 운영자 권장 사항이다(docs/26 §5.6.3).

**무엇을 하는가**: `choices[0].message.tool_calls`가 비고 content에 Gemma 블록이 있을 때만,
블록마다 OpenAI 형식 tool call(litellm 실 타입)을 만들고 content에서 블록을 지운 **사본**을
돌려준다(`finish_reason="tool_calls"` · usage 등 다른 필드 보존).

**안전성**: 그 밖의 응답은 **입력 객체 그대로** 돌려준다(no-op). 블록 하나라도 해석에 실패하면
부분 변환 없이 원본을 돌려주고 warning을 남긴다 — 그러면 W4 방어(미결 종결)가 그대로 작동한다.

**어디에 거는가**: `DefaultLLM.completion` — holmes 0.36.0 LLM 호출의 단일 관문(D-213
`llm_message_guard`와 같은 꼴 · 멱등 표식). 이 관문을 지나는 호출은 두 종류다: 조사 루프
(`tool_calling_llm` — `tools` + `tool_choice="auto"` · `stream=False`)와
컨텍스트 압축·도구 출력 요약(`truncation/compaction` · `transformers/llm_summarize` —
`tools` 없음 · `stream` 미지정). 뒤쪽은
content를 요약문으로 읽으므로 **변환하지 않는다** — `DefaultLLM.completion`이 tools를 실제로 보내는
조건(`tools`가 있고 `tool_choice == "auto"`)과 같은 조건일 때만 후처리한다.

계층: infrastructure(외부 패키지 어댑터). 상위는 `install_tool_call_markup_guard()`만 부른다.
"""

from __future__ import annotations

import functools
import json
import logging
import math
import re
import uuid
from typing import Any

from sre_agent.domain.answer_markup import (
    GEMMA_CALL_PREFIX,
    GEMMA_STRING_DELIM,
    GEMMA_TOOL_CALL_CLOSE,
    GEMMA_TOOL_CALL_OPEN,
    has_tool_call_markup,
    markup_log_excerpt,
)

logger = logging.getLogger(__name__)

# 몽키패치 멱등성 표식 — 재설치·중복 호출을 무해하게 만든다.
_GUARD_FLAG = "_sre_tool_call_markup_parsed"

_NAME_RE = re.compile(r"[A-Za-z_][\w.-]*")
_KEY_RE = _NAME_RE
_NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_LITERALS: dict[str, Any] = {"true": True, "false": False, "null": None}
_WS = " \t\r\n"


class MarkupParseError(ValueError):
    """Gemma 도구 호출 블록 해석 실패."""


class _Parser:
    """Gemma 인자 표기의 결정적 재귀 하강 파서.

    문법: 객체 `{키:값,…}`(키는 따옴표 없는 식별자 또는 구분자 문자열) · 배열 `[값,…]` ·
    문자열 `GEMMA_STRING_DELIM … GEMMA_STRING_DELIM`(이스케이프 없음 — 다음 구분자까지 그대로) ·
    JSON 숫자 · true/false/null. 문자열 구간을 먼저 소비하므로 그 안의 `:`·`,`·`{`·`}`·닫는 표식은
    구조로 읽지 않는다.
    """

    def __init__(self, text: str, pos: int) -> None:
        self.text = text
        self.pos = pos

    def ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in _WS:
            self.pos += 1

    def _peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _expect(self, ch: str) -> None:
        if self._peek() != ch:
            raise MarkupParseError(f"{self.pos}: {ch!r} 기대")
        self.pos += 1

    def value(self) -> Any:
        self.ws()
        if self.text.startswith(GEMMA_STRING_DELIM, self.pos):
            return self.string()
        ch = self._peek()
        if ch == "{":
            return self.obj()
        if ch == "[":
            return self.array()
        m = _NUMBER_RE.match(self.text, self.pos)
        if m and not _NAME_RE.match(self.text, m.end()):
            self.pos = m.end()
            number = json.loads(m.group())
            if isinstance(number, float) and not math.isfinite(number):
                raise MarkupParseError(f"{m.start()}: 비유한 숫자")
            return number
        w = _NAME_RE.match(self.text, self.pos)
        if w and w.group() in _LITERALS:
            self.pos = w.end()
            return _LITERALS[w.group()]
        raise MarkupParseError(f"{self.pos}: 해석할 수 없는 값")

    def string(self) -> str:
        start = self.pos + len(GEMMA_STRING_DELIM)
        end = self.text.find(GEMMA_STRING_DELIM, start)
        if end < 0:
            raise MarkupParseError(f"{self.pos}: 문자열 미종결")
        self.pos = end + len(GEMMA_STRING_DELIM)
        return self.text[start:end]

    def key(self) -> str:
        self.ws()
        if self.text.startswith(GEMMA_STRING_DELIM, self.pos):
            return self.string()
        m = _KEY_RE.match(self.text, self.pos)
        if not m:
            raise MarkupParseError(f"{self.pos}: 키 기대")
        self.pos = m.end()
        return m.group()

    def obj(self) -> dict[str, Any]:
        self._expect("{")
        out: dict[str, Any] = {}
        self.ws()
        if self._peek() == "}":
            self.pos += 1
            return out
        while True:
            k = self.key()
            if k in out:
                raise MarkupParseError(f"{self.pos}: 중복 키 {k!r}")
            self.ws()
            self._expect(":")
            out[k] = self.value()
            self.ws()
            if self._peek() == ",":
                self.pos += 1
                continue
            self._expect("}")
            return out

    def array(self) -> list[Any]:
        self._expect("[")
        out: list[Any] = []
        self.ws()
        if self._peek() == "]":
            self.pos += 1
            return out
        while True:
            out.append(self.value())
            self.ws()
            if self._peek() == ",":
                self.pos += 1
                continue
            self._expect("]")
            return out


def parse_gemma_tool_calls(content: str) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    """content에서 Gemma 도구 호출 블록을 모두 해석한다.

    Returns:
        (`[(도구 이름, 인자 dict), …]`, 블록을 지운 나머지 텍스트). 블록이 없으면 빈 목록.

    Raises:
        MarkupParseError: 블록 하나라도 해석 실패 · 블록 밖에 도구 호출 마크업이 남음.
    """
    calls: list[tuple[str, dict[str, Any]]] = []
    rest: list[str] = []
    pos = 0
    while True:
        start = content.find(GEMMA_TOOL_CALL_OPEN, pos)
        if start < 0:
            rest.append(content[pos:])
            break
        rest.append(content[pos:start])
        p = start + len(GEMMA_TOOL_CALL_OPEN)
        if not content.startswith(GEMMA_CALL_PREFIX, p):
            raise MarkupParseError(f"{p}: {GEMMA_CALL_PREFIX!r} 기대")
        m = _NAME_RE.match(content, p + len(GEMMA_CALL_PREFIX))
        if not m:
            raise MarkupParseError(f"{p}: 도구 이름 기대")
        parser = _Parser(content, m.end())
        args = parser.obj()
        parser.ws()
        if not content.startswith(GEMMA_TOOL_CALL_CLOSE, parser.pos):
            raise MarkupParseError(f"{parser.pos}: {GEMMA_TOOL_CALL_CLOSE!r} 기대")
        calls.append((m.group(), args))
        pos = parser.pos + len(GEMMA_TOOL_CALL_CLOSE)
    remaining = "".join(rest)
    if has_tool_call_markup(remaining):
        raise MarkupParseError("블록 밖에 도구 호출 마크업 잔존")
    return calls, remaining


def rewrite_tool_call_markup(response: Any) -> Any:
    """Gemma 마크업 응답이면 `tool_calls` 사본을, 아니면 **입력 객체 그대로** 돌려준다."""
    try:
        from litellm.types.utils import ChatCompletionMessageToolCall, Function, ModelResponse
    except Exception:  # noqa: BLE001 — litellm 부재·구조 변경이면 손대지 않는다
        return response
    if not isinstance(response, ModelResponse):  # 스트리밍 래퍼 등 예상 밖 타입
        return response
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return response
    content = getattr(message, "content", None)
    if getattr(message, "tool_calls", None) or not isinstance(content, str):
        return response
    if GEMMA_TOOL_CALL_OPEN not in content:
        return response
    try:
        calls, remaining = parse_gemma_tool_calls(content)
        copied = response.model_copy(deep=True)
        choice = copied.choices[0]
        choice.message.content = remaining.strip() or None
        choice.message.tool_calls = [
            ChatCompletionMessageToolCall(
                id=f"call_{uuid.uuid4().hex[:24]}",
                type="function",
                function=Function(name=name, arguments=json.dumps(args, ensure_ascii=False)),
            )
            for name, args in calls
        ]
        choice.finish_reason = "tool_calls"
    except Exception as exc:  # noqa: BLE001 — 부분 변환 금지: 원본 그대로 두고 W4 방어에 맡긴다
        logger.warning(
            "도구 호출 마크업 해석 실패 — 원본 응답 유지(W4 방어): %s · 원문=%s",
            exc,
            markup_log_excerpt(content),
        )
        return response
    logger.info(
        "도구 호출 마크업 %d건을 tool_calls로 변환(도구=%s) — plans/188 C8",
        len(calls),
        [name for name, _ in calls],
    )
    return copied


def _sends_tools(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    """`DefaultLLM.completion(self, messages, tools, tool_choice, …)` 호출이 tools를 보내는지.

    `DefaultLLM.completion`과 같은 조건(`tools`가 있고 `tool_choice == "auto"`).
    위치·키워드 인자 모두 읽는다.
    """
    tools = kwargs["tools"] if "tools" in kwargs else (args[1] if len(args) > 1 else None)
    tool_choice = (
        kwargs["tool_choice"] if "tool_choice" in kwargs else (args[2] if len(args) > 2 else None)
    )
    return bool(tools) and tool_choice == "auto"


def install_tool_call_markup_guard() -> bool:
    """holmes LLM 경계(`DefaultLLM.completion`)에 응답 후처리를 설치한다.

    Returns:
        True면 이번 호출에서 설치했고, False면 설치하지 않았다(이미 설치됨 · holmes 부재 ·
        시그니처 불일치). **어느 경우에도 예외를 올리지 않는다**(사유는 로그로 가시화).
    """
    try:
        from holmes.core.llm import DefaultLLM
    except Exception as exc:  # noqa: BLE001 — holmes 부재/구조 변경은 무해하게 건너뛴다
        logger.warning("도구 호출 마크업 가드 미설치 — holmes.core.llm 임포트 실패: %s", exc)
        return False

    original = getattr(DefaultLLM, "completion", None)
    if original is None:
        logger.warning("도구 호출 마크업 가드 미설치 — DefaultLLM.completion 부재(상위 버전 변경?)")
        return False
    if getattr(original, _GUARD_FLAG, False):
        return False  # 이미 설치됨(멱등)

    @functools.wraps(original)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        response = original(self, *args, **kwargs)
        if not _sends_tools(args, kwargs):
            return response  # 압축·요약 호출 — content가 곧 결과물이라 손대지 않는다
        return rewrite_tool_call_markup(response)

    setattr(guarded, _GUARD_FLAG, True)
    DefaultLLM.completion = guarded  # type: ignore[method-assign]
    logger.info("도구 호출 마크업 가드 설치 완료 — Gemma 표기 → tool_calls (plans/188 C8)")
    return True
