"""조사 LLM 답변의 도구 호출 마크업 판정 (plans/188 C7).

**순수 도메인 모듈**(stdlib만). 사내 vLLM의 `--tool-call-parser`가 모델과 맞지 않으면 도구 호출이
`tool_calls`로 파싱되지 않고 원문(`<|tool_call>call:이름{…}<tool_call|>` 등)이 content로 돌아와
holmes가 최종 답으로 받는다. 그 답은 서술이 아니므로 dispatcher가 원문 없이 미결로 종결한다 —
판정 기준(패턴)과 브리핑·한계·감사에 싣는 고정 문구를 여기 한 곳에 둔다.
"""

from __future__ import annotations

import re

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
#: 형식 불일치로 본다. Llama `<|python_tag|>`·Mistral `[TOOL_CALLS]`·맨 JSON 도구 호출은
#: 범위 밖이다.
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


def has_tool_call_markup(text: str | None) -> bool:
    """답변에 도구 호출 마크업(리터럴 또는 `call:이름{`)이 섞였는지 판정한다."""
    if not text:
        return False
    if any(lit in text for lit in TOOL_CALL_MARKUP_LITERALS):
        return True
    return _CALL_RE.search(text) is not None


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
    "TOOL_CALL_MARKUP_LITERALS",
    "has_tool_call_markup",
    "markup_log_excerpt",
]
