"""OpenAI Chat Completions 요청 ↔ KBGenAI 페이로드 변환 (plans/148 §3.2·§3.3).

KBGenAI 페이로드 키·순서·고정값은 `src/clients/fabrix_kbgenai.py` `_get_payload`(104-126행)
이식이다:
`{modelId, contents, isStream:false, isRagOn:false, executeRagFinalAnswer:false,
executeRagStandaloneQuery:false, systemPrompt, llmConfig?}`.

`contents` 배열 규칙 두 가지는 모두 **추정 규칙**이다(F2 내부망 실측으로 확정):
- `turns`(기본): 본체 KBGenAI 규약 「System 다음 빈 assistant 자리」 — 첫 원소 `""` 뒤에
  user/assistant 교대 문자열. 대화가 assistant로 시작하면 첫 원소가 그 assistant 텍스트다.
- `transcript`: `["", 역할 표지가 붙은 단일 문자열]`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from fabrix_proxy import tool_protocol as tp

KNOWN_FIELDS = frozenset(
    {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "stream",
        "temperature",
        "top_p",
    }
)
OPTIONS_FIELD = "fabrix_proxy_options"
OPTION_KEYS = frozenset(
    {
        "contents_mode",
        "protocol_lang",
        "protocol_text",
        "fewshot",
        "fewshot_placement",
        "fewshot_examples",
        "repair_max",
        "passthrough",
    }
)
_CHOICES = {
    "contents_mode": ("turns", "transcript"),
    "protocol_lang": ("en", "ko"),
    "fewshot": ("none", "static", "dynamic"),
    "fewshot_placement": ("system", "contents"),
}
_ROLES = ("system", "developer", "user", "assistant", "tool")
MAX_TOOLS = 128  # 요청 1건의 도구 정의 상한(규약 블록 폭주 방지)
REPAIR_MAX_LIMIT = 3  # POC 옵션 repair_max 상한(업스트림 호출 = repair_max + 1)


class InvalidRequestError(ValueError):
    """400 `invalid_request` — 메시지는 형식 사유만(본문 없음)."""


@dataclass
class ChatRequest:
    """검증·정규화된 요청. messages의 content는 문자열 또는 None으로 정규화돼 있다."""

    alias: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    tool_choice: Any
    parallel_tool_calls: bool
    stream: bool
    temperature: float | None
    top_p: float | None
    options: dict[str, Any] | None
    ignored_fields: list[str]
    raw: dict[str, Any]


@dataclass(frozen=True)
class EmulationOptions:
    """요청 1건에 적용할 에뮬레이션 선택지(설정 기본값 + POC 요청 옵션 덮어쓰기)."""

    contents_mode: str
    protocol_lang: str
    protocol_template: str
    fewshot: str
    fewshot_placement: str
    fewshot_data: dict[str, Any] | None
    repair_max: int
    passthrough: bool


@dataclass
class PreparedRequest:
    """변환 결과 — 페이로드 조립 재료와 진단 수치."""

    system_prompt: str
    turns: list[tp.Turn]
    example_tool_names: frozenset[str] = field(default_factory=frozenset)
    protocol_chars: int = 0
    fewshot_chars: int = 0
    emulate: bool = False


def _text_content(content: Any, where: str) -> str | None:
    """content(문자열 · None · text parts 배열)를 문자열로 만든다. 비텍스트 part는 400."""
    if content is None or isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if (
                not isinstance(part, dict)
                or part.get("type") != "text"
                or not isinstance(part.get("text"), str)
            ):
                raise InvalidRequestError(f"{where}: content part는 text만 지원한다")
            texts.append(part["text"])
        return "".join(texts)
    raise InvalidRequestError(f"{where}: content 형식 오류")


def _normalize_message(msg: Any, idx: int) -> dict[str, Any]:
    where = f"messages[{idx}]"
    if not isinstance(msg, dict):
        raise InvalidRequestError(f"{where}: 객체가 아니다")
    role = msg.get("role")
    if role not in _ROLES:
        raise InvalidRequestError(f"{where}: 지원하지 않는 role")
    out: dict[str, Any] = {"role": role, "content": _text_content(msg.get("content"), where)}
    if role == "assistant" and msg.get("tool_calls"):
        calls = msg["tool_calls"]
        if not isinstance(calls, list):
            raise InvalidRequestError(f"{where}: tool_calls는 배열이어야 한다")
        for call in calls:
            fn = call.get("function") if isinstance(call, dict) else None
            if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
                raise InvalidRequestError(f"{where}: tool_calls 항목에 function.name이 필요하다")
        out["tool_calls"] = calls
    if role == "tool":
        if not isinstance(msg.get("tool_call_id"), str):
            raise InvalidRequestError(f"{where}: tool 메시지에 tool_call_id가 필요하다")
        out["tool_call_id"] = msg["tool_call_id"]
        if isinstance(msg.get("name"), str):
            out["name"] = msg["name"]
    return out


def _number(body: dict[str, Any], key: str) -> float | None:
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRequestError(f"{key}는 숫자여야 한다")
    return float(value)


def _check_parameters(params: Any, idx: int) -> None:
    """`function.parameters`는 없거나 객체 스키마(`type` 없음 또는 object · properties는 객체)."""
    if params is None:
        return
    where = f"tools[{idx}].function.parameters"
    if not isinstance(params, dict) or params.get("type", "object") != "object":
        raise InvalidRequestError(f"{where}: 객체 스키마(type object)여야 한다")
    if not isinstance(params.get("properties", {}), dict):
        raise InvalidRequestError(f"{where}: properties는 객체여야 한다")


def parse_chat_request(body: Any, aliases: list[str], poc_mode: bool) -> ChatRequest:
    """요청 바디를 검증·정규화한다. 모르는 필드는 무시 목록(이름만)으로 돌려준다."""
    if not isinstance(body, dict):
        raise InvalidRequestError("요청 바디는 JSON 객체여야 한다")
    alias = body.get("model")
    if not isinstance(alias, str) or alias not in aliases:
        raise InvalidRequestError("모르는 model 별칭")
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise InvalidRequestError("messages는 비어 있지 않은 배열이어야 한다")
    messages = [_normalize_message(m, i) for i, m in enumerate(raw_messages)]

    tools = body.get("tools") or []
    if not isinstance(tools, list):
        raise InvalidRequestError("tools는 배열이어야 한다")
    if len(tools) > MAX_TOOLS:
        raise InvalidRequestError(f"tools는 {MAX_TOOLS}개 이하여야 한다")
    for i, tool in enumerate(tools):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise InvalidRequestError(f"tools[{i}]: type function만 지원한다")
        fn = tp.tool_function(tool)
        if not isinstance(fn.get("name"), str):
            raise InvalidRequestError(f"tools[{i}]: function.name이 필요하다")
        _check_parameters(fn.get("parameters"), i)
    names = tp.tool_names(tools)

    tool_choice = body.get("tool_choice")
    if tool_choice is None:
        tool_choice = "auto" if tools else "none"
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function")
        if (
            tool_choice.get("type") != "function"
            or not isinstance(fn, dict)
            or fn.get("name") not in names
        ):
            raise InvalidRequestError("tool_choice가 지정한 함수가 tools에 없다")
    elif tool_choice not in ("none", "auto", "required"):
        raise InvalidRequestError("tool_choice 값 오류")
    elif tool_choice == "required" and not tools:
        raise InvalidRequestError("tool_choice=required인데 tools가 없다")
    if not tools:
        tool_choice = "none"

    parallel = body.get("parallel_tool_calls", True)
    if not isinstance(parallel, bool):
        raise InvalidRequestError("parallel_tool_calls는 불리언이어야 한다")
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise InvalidRequestError("stream은 불리언이어야 한다")

    known = KNOWN_FIELDS | ({OPTIONS_FIELD} if poc_mode else frozenset())
    options = body.get(OPTIONS_FIELD) if poc_mode else None
    if options is not None and not isinstance(options, dict):
        raise InvalidRequestError(f"{OPTIONS_FIELD}는 객체여야 한다")
    return ChatRequest(
        alias=alias,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel,
        stream=stream,
        temperature=_number(body, "temperature"),
        top_p=_number(body, "top_p"),
        options=options,
        ignored_fields=sorted(k for k in body if k not in known),
        raw=body,
    )


def resolve_options(
    defaults: EmulationOptions,
    override: dict[str, Any] | None,
    templates: dict[str, str],
    package_fewshot: dict[str, Any],
) -> EmulationOptions:
    """POC 요청 옵션으로 설정 기본값을 덮어쓴다. 값은 인라인만 받는다(경로를 읽지 않는다).

    요청 선택지는 프록시 설정보다 항상 우선한다(라벨 = 실제 동작):
    - `protocol_lang`이 오고 `protocol_text`가 없으면 설정 `FABRIX_PROXY_PROTOCOL_FILE`이 아니라
      그 언어의 패키지 기본 템플릿(`templates`)을 쓴다.
    - `fewshot`이 오고 `fewshot_examples`가 없으면 설정 `FABRIX_PROXY_FEWSHOT_FILE`이 아니라
      패키지 기본 few-shot(`package_fewshot`)을 쓴다.
    - `passthrough: false`는 설정 `FABRIX_PROXY_PASSTHROUGH=true`도 끈다.
    """
    if not override:
        return defaults
    unknown = sorted(set(override) - OPTION_KEYS)
    if unknown:
        raise InvalidRequestError(f"{OPTIONS_FIELD}: 모르는 키 {unknown}")
    changes: dict[str, Any] = {}
    for key, allowed in _CHOICES.items():
        if key in override:
            if override[key] not in allowed:
                raise InvalidRequestError(f"{OPTIONS_FIELD}.{key}: {allowed} 중 하나여야 한다")
            changes[key] = override[key]
    if "protocol_lang" in changes:
        changes["protocol_template"] = templates[changes["protocol_lang"]]
    if "protocol_text" in override:
        text = override["protocol_text"]
        if not isinstance(text, str):
            raise InvalidRequestError(f"{OPTIONS_FIELD}.protocol_text는 문자열이어야 한다")
        try:
            tp.validate_protocol_template(text)
        except ValueError as exc:
            raise InvalidRequestError(f"{OPTIONS_FIELD}.protocol_text: {exc}") from exc
        changes["protocol_template"] = text
    if "fewshot" in override and "fewshot_examples" not in override:
        changes["fewshot_data"] = package_fewshot
    if "fewshot_examples" in override:
        try:
            changes["fewshot_data"] = tp.validate_fewshot(override["fewshot_examples"])
        except ValueError as exc:
            raise InvalidRequestError(f"{OPTIONS_FIELD}.fewshot_examples: {exc}") from exc
    if "repair_max" in override:
        value = override["repair_max"]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= REPAIR_MAX_LIMIT
        ):
            raise InvalidRequestError(
                f"{OPTIONS_FIELD}.repair_max는 0~{REPAIR_MAX_LIMIT} 정수여야 한다"
            )
        changes["repair_max"] = value
    if "passthrough" in override:
        if not isinstance(override["passthrough"], bool):
            raise InvalidRequestError(f"{OPTIONS_FIELD}.passthrough는 불리언이어야 한다")
        changes["passthrough"] = override["passthrough"]
    return replace(defaults, **changes)


def prepare_request(req: ChatRequest, opts: EmulationOptions) -> PreparedRequest:
    """systemPrompt와 턴 목록을 만든다.

    systemPrompt = [system/developer 결합] + [도구 규약 블록(tools 있고 tool_choice≠none)]
    + [few-shot 블록(placement=system · fewshot≠none · 규약 블록이 있을 때)].
    """
    system_parts = [
        m["content"] for m in req.messages if m["role"] in ("system", "developer") and m["content"]
    ]
    turns = tp.serialize_turns(req.messages)
    emulate = bool(req.tools) and req.tool_choice != "none"
    if not emulate:
        return PreparedRequest(system_prompt="\n\n".join(system_parts), turns=turns)

    protocol = tp.render_protocol(
        req.tools,
        req.tool_choice,
        req.parallel_tool_calls,
        template=opts.protocol_template,
        lang=opts.protocol_lang,
    )
    system_parts.append(protocol)
    fewshot = tp.select_fewshot(opts.fewshot_data, opts.fewshot, req.tools, req.parallel_tool_calls)
    fewshot_chars = 0
    if fewshot is not None:
        if opts.fewshot_placement == "system":
            block = tp.render_fewshot_block(fewshot, opts.protocol_lang)
            system_parts.append(block)
            fewshot_chars = len(block)
        else:
            example = tp.fewshot_contents_turns(fewshot)
            fewshot_chars = sum(len(text) for _, text in example)
            turns = tp.merge_turns([*example, *turns])
    return PreparedRequest(
        system_prompt="\n\n".join(system_parts),
        turns=turns,
        example_tool_names=fewshot.example_tool_names if fewshot else frozenset(),
        protocol_chars=len(protocol),
        fewshot_chars=fewshot_chars,
        emulate=True,
    )


def turns_to_contents(turns: list[tp.Turn], mode: str) -> list[str]:
    """턴 목록을 KBGenAI `contents` 배열로 만든다(추정 규칙 — 모듈 독스트링)."""
    merged = tp.merge_turns(turns)
    if mode == "transcript":
        return ["", tp.render_transcript(merged)]
    contents = [] if merged and merged[0][0] == "assistant" else [""]
    contents.extend(text for _, text in merged)
    return contents


def build_llm_config(
    base: dict[str, Any], temperature: float | None, top_p: float | None
) -> dict[str, Any] | None:
    """`FABRIX_LLM_CONFIG`에 요청 temperature·top_p를 덮는다. 결과가 비면 None(키 미포함)."""
    config = dict(base)
    if temperature is not None:
        config["temperature"] = temperature
    if top_p is not None:
        config["top_p"] = top_p
    return config or None


def build_payload(
    prepared: PreparedRequest,
    *,
    model_id: str,
    contents_mode: str,
    llm_config: dict[str, Any] | None,
    extra_turns: list[tp.Turn] | None = None,
) -> dict[str, Any]:
    """KBGenAI 페이로드를 조립한다(키 순서·고정값은 `_get_payload` 이식)."""
    payload: dict[str, Any] = {
        "modelId": model_id,
        "contents": turns_to_contents([*prepared.turns, *(extra_turns or [])], contents_mode),
        "isStream": False,
        "isRagOn": False,
        "executeRagFinalAnswer": False,
        "executeRagStandaloneQuery": False,
        "systemPrompt": prepared.system_prompt,
    }
    if llm_config:
        payload["llmConfig"] = llm_config
    return payload


def repair_turns(raw_output: str, message: str) -> list[tp.Turn]:
    """교정 재질의용 추가 턴 — assistant(모델 원출력) + user(오류 사유)."""
    return [("assistant", raw_output), ("user", message)]


def native_body(raw: dict[str, Any], model: str) -> dict[str, Any]:
    """passthrough 바디 — `model`만 바꾸고 프록시 옵션을 뺀다. 업스트림은 비스트림으로 부른다."""
    body = {k: v for k, v in raw.items() if k != OPTIONS_FIELD}
    body["model"] = model
    body["stream"] = False
    body.pop("stream_options", None)
    return body
