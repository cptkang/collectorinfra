"""조사 LLM 답변의 도구 호출 마크업 판정 (plans/188 C7).

**순수 도메인 모듈**(stdlib만). 사내 vLLM의 `--tool-call-parser`가 모델과 맞지 않으면 도구 호출이
`tool_calls`로 파싱되지 않고 원문(`<|tool_call>call:이름{…}<tool_call|>` 등)이 content로 돌아와
holmes가 최종 답으로 받는다. 그 답은 서술이 아니므로 dispatcher가 원문 없이 미결로 종결한다 —
판정 기준(패턴)과 브리핑·한계·감사에 싣는 고정 문구를 여기 한 곳에 둔다.

**맨 JSON 도구 호출**(폐쇄망 2026-10-30 실측 · D-342 W4 부기 2 · D-343 부기 1): 답 **전체**가
마크업 없는 JSON 도구 호출(TodoWrite `todos` 배열 등)인 경우도 같은 형식 불일치로 본다 — 종전에는
그 JSON이 「1순위 원인」 서술로 화면에 실렸다. 해석은 `parse_bare_json_tool_calls` 한 곳이다
(C8 변환도 이것을 쓴다).
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Gemma 4 계열 도구 호출 표기 — `<|tool_call>call:이름{…}<tool_call|>` · 문자열은 `<|"|>…<|"|>`.
#: sre_agent 클라이언트 측 파서(plans/188 C8)도 이 상수를 쓴다(리터럴 단일 출처).
GEMMA_TOOL_CALL_OPEN = "<|tool_call>"
GEMMA_TOOL_CALL_CLOSE = "<tool_call|>"
GEMMA_CALL_PREFIX = "call:"
GEMMA_STRING_DELIM = '<|"|>'

#: 도구 호출 마크업 리터럴 — 하나라도 있으면 형식 불일치로 본다.
TOOL_CALL_MARKUP_LITERALS: tuple[str, ...] = (
    GEMMA_TOOL_CALL_OPEN, GEMMA_TOOL_CALL_CLOSE, "<tool_call>", "</tool_call>", GEMMA_STRING_DELIM,
)

#: `call:<식별자>{` — 행 시작·공백·`>`·`|` 바로 뒤만 본다
#: (「recall:」·URL 경로·따옴표 안 인용은 제외). 그 위치의 `call:이름{`는 코드 블록·표 안이어도
#: 형식 불일치로 본다. Llama `<|python_tag|>`·Mistral `[TOOL_CALLS]`는 범위 밖이다
#: (맨 JSON 도구 호출은 `parse_bare_json_tool_calls`가 따로 본다).
_CALL_RE = re.compile(r"(?:^|(?<=[\s>|]))call:[A-Za-z_][\w.-]*\{", re.MULTILINE)

#: 감사 `done` 레코드 선택 키 `answer_malformed`의 값.
ANSWER_MALFORMED_TOOL_CALL = "tool_call_markup"
#: 형식 불일치 종결의 요약(접두 `[미결] `은 브리핑이 붙인다) · 원인 · 한계 1줄.
MALFORMED_ANSWER_SUMMARY = "조사 실패 — 조사 LLM 도구 호출 형식 불일치"
MALFORMED_ANSWER_CAUSE = "판단 불가(조사 LLM 응답 형식 불일치)"
MALFORMED_ANSWER_LIMITATION = (
    "조사 LLM 응답에 도구 호출 원문이 섞임 — vLLM --enable-auto-tool-choice·--tool-call-parser를 "
    "모델에 맞게 확인(docs/26 §5.6.3)"
)


#: holmes 내장 할 일 목록 도구 이름과 상태 값
#: (holmes 0.36.0 `TODO_WRITE_TOOL_NAME`·`TaskStatus` 실측).
TODO_WRITE_TOOL = "TodoWrite"
_TODO_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})
_TODO_KEYS = frozenset({"id", "content", "status"})
#: 맨 JSON 호출 객체가 가질 수 있는 키 — 인자 키는 `arguments`·`parameters` 중 정확히 하나.
_CALL_KEYS = frozenset({"type", "name", "arguments", "parameters"})
_TOOL_NAME_RE = re.compile(r"[A-Za-z_][\w.-]*\Z")
#: 답 전체를 감싼 코드 펜스(```json … ```) — 펜스 밖에 글이 있으면 맨 JSON 답이 아니다.
_FENCE_RE = re.compile(r"\A```[A-Za-z]*\s*\n(.*?)\n?```\Z", re.DOTALL)


def _is_todo(item: object) -> bool:
    return (isinstance(item, dict) and bool(item) and set(item) <= _TODO_KEYS
            and isinstance(item.get("content"), str) and item.get("status") in _TODO_STATUSES)


def _todo_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(_is_todo(t) for t in value)


def _call(obj: object) -> tuple[str, dict[str, Any]] | None:
    """`{"name": 이름, "arguments"|"parameters": {…}}` 한 건 → (이름, 인자). 아니면 None."""
    if not isinstance(obj, dict) or not set(obj) <= _CALL_KEYS:
        return None
    name = obj.get("name")
    arg_keys = [k for k in ("arguments", "parameters") if k in obj]
    if not isinstance(name, str) or not _TOOL_NAME_RE.match(name) or len(arg_keys) != 1:
        return None
    if "type" in obj and obj["type"] != "function":
        return None
    args = obj[arg_keys[0]]
    if isinstance(args, str):  # OpenAI 꼴 — 인자가 JSON 문자열
        try:
            args = json.loads(args)
        except ValueError:
            return None
    return (name, args) if isinstance(args, dict) else None


def parse_bare_json_tool_calls(text: str | None) -> list[tuple[str, dict[str, Any]]] | None:
    """답 **전체**가 맨 JSON 도구 호출이면 `[(도구 이름, 인자), …]`, 아니면 None.

    인정하는 꼴은 셋뿐이다(서술 속에 섞인 JSON 조각은 보지 않는다 — 전체가 JSON이어야 한다):

    ① `{"name": 이름, "arguments"|"parameters": {…}}` 또는 그 배열(키는 `type`·`name`·인자 키만)
    ② TodoWrite 인자 `{"todos": [할 일, …]}`
    ③ TodoWrite `todos` 배열 그 자체 `[{"id", "content", "status"}, …]`(폐쇄망 2026-10-30 실측 꼴)

    할 일 항목은 키가 `id`·`content`·`status`뿐이고 `status`가 holmes 상태 값이어야 한다.
    답 전체를 감싼 코드 펜스 한 겹은 벗긴다.
    """
    if not text:
        return None
    body = text.strip()
    fence = _FENCE_RE.match(body)
    if fence:
        body = fence.group(1).strip()
    if not body.startswith(("{", "[")):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if _todo_list(data):
        return [(TODO_WRITE_TOOL, {"todos": data})]
    if isinstance(data, dict) and set(data) == {"todos"} and _todo_list(data["todos"]):
        return [(TODO_WRITE_TOOL, {"todos": data["todos"]})]
    items = data if isinstance(data, list) else [data]
    calls = [_call(x) for x in items]
    if not calls or any(c is None for c in calls):
        return None
    return [c for c in calls if c is not None]


def has_tool_call_markup(text: str | None) -> bool:
    """답변에 도구 호출 마크업이 섞였는지 판정한다.

    리터럴 · `call:이름{` · 답 전체가 맨 JSON 도구 호출 중 하나면 True.
    """
    if not text:
        return False
    if any(lit in text for lit in TOOL_CALL_MARKUP_LITERALS):
        return True
    if _CALL_RE.search(text) is not None:
        return True
    return parse_bare_json_tool_calls(text) is not None


#: Gemma 문자열 구분자 — 로그 가림(`k:"v"` 꼴 인식) 전에 큰따옴표로 바꾼다.
_GEMMA_QUOTE = GEMMA_STRING_DELIM


def markup_log_excerpt(text: str, limit: int = 200) -> str:
    """경고 로그용 원문 발췌 — `<|"|>`를 `"`로 정규화한 뒤 앞 `limit`자.

    구분자가 남아 있으면 로그 레코드 팩토리의 민감 키 값 가림(`api_key:"…"`)이 값을 못 잡는다.
    잘린 범위 안 구분자가 홀수면(문자열이 열린 채 잘림 — 닫는 따옴표가 없어 가림이 값을 못 잡는다)
    마지막 여는 구분자 직전에서 자른다.
    """
    raw = text or ""
    out: list[str] = []
    i = count = 0
    last_open = 0
    while i < len(raw) and len(out) < limit:
        if raw.startswith(_GEMMA_QUOTE, i):
            if count % 2 == 0:
                last_open = len(out)
            count += 1
            out.append('"')
            i += len(_GEMMA_QUOTE)
        else:
            out.append(raw[i])
            i += 1
    if count % 2:
        del out[last_open:]
    return "".join(out)


__all__ = [
    "ANSWER_MALFORMED_TOOL_CALL",
    "GEMMA_CALL_PREFIX",
    "GEMMA_STRING_DELIM",
    "GEMMA_TOOL_CALL_CLOSE",
    "GEMMA_TOOL_CALL_OPEN",
    "MALFORMED_ANSWER_CAUSE",
    "MALFORMED_ANSWER_LIMITATION",
    "MALFORMED_ANSWER_SUMMARY",
    "TODO_WRITE_TOOL",
    "TOOL_CALL_MARKUP_LITERALS",
    "has_tool_call_markup",
    "markup_log_excerpt",
    "parse_bare_json_tool_calls",
]
