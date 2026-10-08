"""도구 호출 에뮬레이션 규약 — 규약 블록 · few-shot · 이력 직렬화 · 파서 · 교정 메시지.

plans/148 §3.3·§3.3.1·§3.4.

한 출처 원칙: 출력 규약 · few-shot 예시 · 대화 이력이 같은 표기(`<tool_call>{…}</tool_call>` ·
`<tool_response name id>…</tool_response>`)를 쓰도록, 직렬화는 `serialize_turns` 하나로 한다.
few-shot 예시는 문자열이 아니라 구조화 데이터(OpenAI 형식 메시지)로 두고 같은 함수로 렌더한다.

파서는 결정적이다. 교정 소진 시 평문으로 강등하지 않는다(침묵 폴백 금지 — 호출자가 502로 낸다).
이 모듈은 표준 라이브러리(+ 선택 `jsonschema`)만 쓴다 — I/O는 `load_*` 함수의 파일 읽기뿐이다.
"""

from __future__ import annotations

import copy
import json
import math
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:  # 미설치 환경은 축소 검증(required·속성 최상위 타입)으로 대체한다
    from jsonschema.validators import (  # type: ignore[import-untyped]
        validator_for as _validator_for,
    )
except ImportError:  # pragma: no cover - 루트 venv에는 설치돼 있다(4.26.0)
    _validator_for = None

_PACKAGE_DIR = Path(__file__).resolve().parent

# ── 규약 템플릿 자리표시 — str.format이 아닌 명시 치환(JSON 중괄호 충돌 방지) ──
PLACEHOLDER_TOOLS = "{{TOOLS}}"
PLACEHOLDER_CHOICE_RULE = "{{CHOICE_RULE}}"
PLACEHOLDER_PARALLEL_RULE = "{{PARALLEL_RULE}}"

TOOL_CALL_OPEN = "<tool_call>"
TOOL_CALL_CLOSE = "</tool_call>"

# 이식: src/clients/fabrix_kbgenai.py `LLAMA_JUNK_TOKENS`·`remove_llm_junk`
LLAMA_JUNK_TOKENS = [
    "<|eot_id|>",
    "<|end_header_id|>",
    "<|eom_id|>",
    "<|start_header_id|>assistant",
]

FEWSHOT_PATTERNS = ("single", "parallel", "after_result", "no_tool")
SAMPLE_SUFFIX = "_sample"

Turn = tuple[str, str]  # (role ∈ {"user","assistant"}, text)
ParseKind = Literal["tool_calls", "text", "invalid"]

_CHOICE_RULES = {
    "en": {
        "required": "You must call at least one tool in this reply.",
        "named": "You must call the tool `{name}` in this reply.",
    },
    "ko": {
        "required": "이번 답에서는 반드시 도구를 하나 이상 호출한다.",
        "named": "이번 답에서는 반드시 `{name}` 도구를 호출한다.",
    },
}
_PARALLEL_RULES = {
    "en": {
        True: (
            "You may call several tools at once by writing several <tool_call> blocks "
            "one after another."
        ),
        False: "Call at most one tool per reply (a single <tool_call> block).",
    },
    "ko": {
        True: "여러 도구를 한 번에 호출하려면 <tool_call> 블록을 여러 개 이어서 쓴다.",
        False: "한 답에서는 도구를 하나만 호출한다(<tool_call> 블록 1개).",
    },
}
_FEWSHOT_TEXT = {
    "en": {
        "title": "# Format examples",
        "fictional": (
            "The examples below only illustrate the output format. Their tools ({names}) are "
            "fictional and are not available to you. Never call them; use only the tools "
            "listed above."
        ),
        "dynamic": (
            "The examples below only illustrate the output format. Argument values in them are "
            "placeholders, not real values."
        ),
        "tools": "Example tools:",
        "example": "## Example {n}",
    },
    "ko": {
        "title": "# 형식 예시",
        "fictional": (
            "아래 예시는 출력 형식만 보여 준다. 예시의 도구({names})는 가상이라 쓸 수 없다. "
            "절대 호출하지 말고 위에 나온 도구만 쓴다."
        ),
        "dynamic": (
            "아래 예시는 출력 형식만 보여 준다. 예시의 인자 값은 자리표시이며 실제 값이 아니다."
        ),
        "tools": "예시 도구:",
        "example": "## 예시 {n}",
    },
}
_REPAIR_TEXT = {
    "en": {
        "head": "Your previous reply could not be used as a tool call: {reasons}.",
        "tools": "Available tools: {names}.",
        "format": (
            'To call a tool, reply with only <tool_call>{"name": "<tool name>", "arguments": {...}}'
            "</tool_call> blocks whose arguments satisfy the parameters schema of that tool."
        ),
        "json_error": (
            'a <tool_call> block was not valid JSON of the form {"name": ..., "arguments": {...}}'
        ),
        "unknown_tool": "it called a tool that does not exist ({names})",
        "schema_error": "argument '{arg}' is missing or does not match the schema",
        "missing_required_call": "a tool call is required but none was made",
        "wrong_tool": "it called a different tool than the required one",
        "parallel_not_allowed": "only one tool call is allowed per reply",
        "empty_output": "the reply was empty",
    },
    "ko": {
        "head": "직전 답은 도구 호출로 쓸 수 없다: {reasons}.",
        "tools": "쓸 수 있는 도구: {names}.",
        "format": (
            "도구를 호출하려면 인자가 그 도구의 스키마를 만족하는 "
            '<tool_call>{"name": "<도구 이름>", '
            '"arguments": {...}}</tool_call> 블록만 출력한다.'
        ),
        "json_error": (
            '<tool_call> 블록이 {"name": ..., "arguments": {...}} 형태의 올바른 JSON이 아니다'
        ),
        "unknown_tool": "없는 도구를 호출했다({names})",
        "schema_error": "인자 '{arg}'가 없거나 스키마와 맞지 않는다",
        "missing_required_call": "도구 호출이 필요한데 호출하지 않았다",
        "wrong_tool": "지정된 도구가 아닌 다른 도구를 호출했다",
        "parallel_not_allowed": "한 답에서는 도구를 하나만 호출할 수 있다",
        "empty_output": "답이 비어 있었다",
    },
}


# ─────────────────────────────── 공용 유틸 ───────────────────────────────


def remove_llm_junk(text: str, strip: bool = True) -> str:
    """Llama 계열 특수 토큰을 제거한다(이식: `KBGenAIChat.remove_llm_junk`)."""
    for token in LLAMA_JUNK_TOKENS:
        text = text.replace(token, "")
    return text.strip() if strip else text


def estimate_tokens(text: str) -> int:
    """입력 토큰 수를 보수적으로 **추정**한다 — ceil(ASCII 문자/4 + 비ASCII 문자/1).

    F3 실측(FabriX 토크나이저) 전의 추정 계수다. 한국어 등 비ASCII는 문자당 1토큰으로 넉넉히 센다.
    1-2 측정 스크립트가 재사용한다.
    """
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return math.ceil(ascii_chars / 4 + (len(text) - ascii_chars))


def new_tool_call_id() -> str:
    """OpenAI 형식 도구 호출 id(`call_` + uuid4 hex 앞 24자)를 발급한다."""
    return "call_" + uuid.uuid4().hex[:24]


def tool_function(tool: dict[str, Any]) -> dict[str, Any]:
    """OpenAI 도구 정의에서 `function` 객체를 꺼낸다."""
    fn = tool.get("function")
    return fn if isinstance(fn, dict) else {}


def tool_names(tools: Sequence[dict[str, Any]]) -> list[str]:
    """도구 정의 목록의 이름을 순서대로 돌려준다."""
    return [str(tool_function(t).get("name", "")) for t in tools]


def choice_mode(tool_choice: Any) -> tuple[str, str | None]:
    """tool_choice를 (모드, 지정 이름)으로 정규화한다. 모드: none·auto·required·named."""
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function")
        name = fn.get("name") if isinstance(fn, dict) else None
        return "named", str(name) if name is not None else None
    if tool_choice in ("none", "auto", "required"):
        return str(tool_choice), None
    return "auto", None


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ─────────────────────────────── 규약 블록 ───────────────────────────────


def compact_schema(schema: Any) -> Any:
    """JSON Schema를 규약 블록용으로 압축한다.

    `title`·`$schema`를 지우고, `$ref`를 `$defs`로 인라인한 뒤 `$defs`를 지운다.

    `properties` 아래의 키는 인자 이름이라 `title`이어도 지우지 않는다. 순환 참조는 `$ref`로 남긴다.
    """
    if not isinstance(schema, dict):
        return schema
    defs: dict[str, Any] = {}
    for key in ("$defs", "definitions"):
        if isinstance(schema.get(key), dict):
            defs.update(schema[key])

    def resolve(node: Any, stack: tuple[str, ...], in_properties: bool) -> Any:
        if isinstance(node, list):
            return [resolve(item, stack, False) for item in node]
        if not isinstance(node, dict):
            return node
        if in_properties:
            return {k: resolve(v, stack, False) for k, v in node.items()}
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            if name in defs and name not in stack:
                merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged, stack + (name,), False)
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in ("title", "$schema", "$defs", "definitions"):
                continue
            out[key] = resolve(value, stack, key in ("properties", "patternProperties"))
        return out

    return resolve(schema, (), False)


def load_protocol_template(lang: str, path: str | Path | None = None) -> str:
    """규약 템플릿을 읽는다. path가 없으면 패키지 기본(`protocol_<lang>.txt`)."""
    target = Path(path) if path else _PACKAGE_DIR / f"protocol_{lang}.txt"
    text = target.read_text(encoding="utf-8")
    validate_protocol_template(text)
    return text


def validate_protocol_template(text: str) -> None:
    """규약 템플릿에 도구 자리표시가 있는지 확인한다(없으면 도구가 렌더되지 않는다)."""
    if PLACEHOLDER_TOOLS not in text:
        raise ValueError(f"규약 템플릿에 {PLACEHOLDER_TOOLS} 자리표시가 없다")


def render_tool_list(tools: Sequence[dict[str, Any]]) -> str:
    """도구 정의를 규약 블록의 목록 문자열로 렌더한다."""
    lines: list[str] = []
    for tool in tools:
        fn = tool_function(tool)
        lines.append(f"- name: {fn.get('name', '')}")
        desc = str(fn.get("description") or "").strip()
        if desc:
            lines.append(f"  description: {desc}")
        lines.append(
            f"  parameters: {_dumps(compact_schema(fn.get('parameters') or {'type': 'object'}))}"
        )
    return "\n".join(lines)


def render_protocol(
    tools: Sequence[dict[str, Any]],
    tool_choice: Any,
    parallel_tool_calls: bool,
    *,
    template: str,
    lang: str,
) -> str:
    """도구 규약 블록을 렌더한다(tools가 있고 tool_choice≠none일 때만 호출한다)."""
    mode, name = choice_mode(tool_choice)
    rules = _CHOICE_RULES.get(lang, _CHOICE_RULES["en"])
    choice_rule = ""
    if mode == "required":
        choice_rule = rules["required"]
    elif mode == "named":
        choice_rule = rules["named"].replace("{name}", name or "")
    parallel_rule = _PARALLEL_RULES.get(lang, _PARALLEL_RULES["en"])[bool(parallel_tool_calls)]
    text = (
        template.replace(PLACEHOLDER_TOOLS, render_tool_list(tools))
        .replace(PLACEHOLDER_CHOICE_RULE, choice_rule)
        .replace(PLACEHOLDER_PARALLEL_RULE, parallel_rule)
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ─────────────────────────────── 이력 직렬화 ───────────────────────────────


def _coerce_arguments(raw: Any) -> Any:
    """OpenAI `function.arguments`(JSON 문자열)를 객체로 되돌린다. 파싱 불가면 원 문자열."""
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return raw
    return {} if raw is None else raw


def serialize_tool_call(name: str, arguments: Any) -> str:
    """도구 호출 1건을 `<tool_call>{"name":…,"arguments":{…}}</tool_call>` 블록으로 직렬화한다."""
    body = json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False)
    return f"{TOOL_CALL_OPEN}{body}{TOOL_CALL_CLOSE}"


def serialize_tool_response(name: str, call_id: str, content: str) -> str:
    """도구 결과 1건을 `<tool_response name id>…</tool_response>`로 직렬화한다."""
    return f'<tool_response name="{name}" id="{call_id}">{content}</tool_response>'


def merge_turns(turns: Iterable[Turn]) -> list[Turn]:
    """연속한 같은 역할 턴을 `\\n\\n`로 합친다."""
    merged: list[Turn] = []
    for role, text in turns:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, f"{merged[-1][1]}\n\n{text}")
        else:
            merged.append((role, text))
    return merged


def serialize_turns(messages: Sequence[dict[str, Any]]) -> list[Turn]:
    """OpenAI 형식 메시지(content는 문자열 또는 None)를 user/assistant 턴 목록으로 직렬화한다.

    이력 · few-shot 예시 · 교정 재질의가 모두 이 함수를 지난다(표기 한 출처).
    - system/developer는 건너뛴다(호출자가 systemPrompt로 보낸다).
    - assistant `tool_calls` → content 뒤에 `<tool_call>` 블록들.
    - tool → user 턴의 `<tool_response>`(이름은 메시지 `name` 또는 이력의 같은 id 호출 이름).
    - 연속 같은 역할 턴(연속 tool 결과 포함)은 병합한다.
    """
    call_names: dict[str, str] = {}
    turns: list[Turn] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        text = content if isinstance(content, str) else ""
        if role == "user":
            turns.append(("user", text))
        elif role == "assistant":
            parts = [text] if text else []
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                name = str(fn.get("name", ""))
                if call.get("id"):
                    call_names[str(call["id"])] = name
                parts.append(serialize_tool_call(name, _coerce_arguments(fn.get("arguments"))))
            turns.append(("assistant", "\n".join(parts)))
        elif role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            name = str(msg.get("name") or call_names.get(call_id, ""))
            turns.append(("user", serialize_tool_response(name, call_id, text)))
    return merge_turns(turns)


def render_transcript(turns: Sequence[Turn]) -> str:
    """턴 목록을 역할 표지가 붙은 단일 문자열(`[user]\\n…\\n\\n[assistant]\\n…`)로 렌더한다."""
    return "\n\n".join(f"[{role}]\n{text}" for role, text in turns)


# ─────────────────────────────── few-shot ───────────────────────────────


def validate_fewshot(data: Any) -> dict[str, Any]:
    """few-shot 데이터 스키마를 확인한다.

    형식: `{tools:[OpenAI 도구…], examples:[{pattern, messages, expected}]}`.
    """
    if not isinstance(data, dict):
        raise ValueError("few-shot 데이터는 객체여야 한다")
    tools = data.get("tools")
    examples = data.get("examples")
    if not isinstance(tools, list) or not isinstance(examples, list):
        raise ValueError("few-shot 데이터에 tools·examples 배열이 필요하다")
    for tool in tools:
        if not isinstance(tool, dict) or not tool_function(tool).get("name"):
            raise ValueError("few-shot tools 항목에 function.name이 필요하다")
    for ex in examples:
        if not isinstance(ex, dict) or ex.get("pattern") not in FEWSHOT_PATTERNS:
            raise ValueError(f"few-shot 예시 pattern은 {FEWSHOT_PATTERNS} 중 하나여야 한다")
        if not isinstance(ex.get("messages"), list):
            raise ValueError("few-shot 예시에 messages 배열이 필요하다")
        expected = ex.get("expected")
        if not isinstance(expected, dict) or not (
            isinstance(expected.get("tool_calls"), list) or isinstance(expected.get("text"), str)
        ):
            raise ValueError("few-shot 예시 expected는 tool_calls 배열 또는 text 문자열이어야 한다")
    return data


def load_fewshot(path: str | Path | None = None) -> dict[str, Any]:
    """few-shot 데이터 파일을 읽는다. path가 없으면 패키지 기본 `fewshot_default.json`."""
    target = Path(path) if path else _PACKAGE_DIR / "fewshot_default.json"
    return validate_fewshot(json.loads(target.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class FewshotSet:
    """요청 하나에 쓸 few-shot 예시 묶음(이름 충돌 치환·패턴 필터 적용 후)."""

    tools: list[dict[str, Any]]
    examples: list[dict[str, Any]]
    fictional: bool  # True = 정적 가상 도구 · False = dynamic(요청 도구 이름 사용)

    @property
    def example_tool_names(self) -> frozenset[str]:
        """가상 예시 도구 이름(오호출 계수용). dynamic이면 빈 집합."""
        return frozenset(tool_names(self.tools)) if self.fictional else frozenset()


def _rename_map(example_names: Iterable[str], taken: set[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name in example_names:
        if name not in taken:
            continue
        candidate, n = name + SAMPLE_SUFFIX, 2
        while candidate in taken:
            candidate, n = f"{name}{SAMPLE_SUFFIX}{n}", n + 1
        mapping[name] = candidate
    return mapping


def _apply_rename(data: dict[str, Any], mapping: dict[str, str]) -> None:
    for tool in data["tools"]:
        fn = tool_function(tool)
        fn["name"] = mapping.get(fn.get("name", ""), fn.get("name", ""))
    for ex in data["examples"]:
        for msg in ex["messages"]:
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                if fn.get("name") in mapping:
                    fn["name"] = mapping[fn["name"]]
            if msg.get("role") == "tool" and msg.get("name") in mapping:
                msg["name"] = mapping[msg["name"]]
        for call in ex["expected"].get("tool_calls") or []:
            if call.get("name") in mapping:
                call["name"] = mapping[call["name"]]


def _placeholder(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return "<value>"
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    return {"integer": 1, "number": 1, "boolean": True, "array": [], "object": {}}.get(
        str(kind), "<value>"
    )


def _placeholder_args(tool: dict[str, Any]) -> dict[str, Any]:
    params = compact_schema(tool_function(tool).get("parameters") or {})
    props = params.get("properties") or {} if isinstance(params, dict) else {}
    required = params.get("required") or [] if isinstance(params, dict) else []
    return {name: _placeholder(props.get(name)) for name in required}


def _call_message(calls: list[tuple[str, dict[str, Any]]], prefix: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"{prefix}_{i}",
                "type": "function",
                "function": {"name": n, "arguments": _dumps(a)},
            }
            for i, (n, a) in enumerate(calls, 1)
        ],
    }


def _dynamic_examples(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """요청 도구 이름으로 패턴 ①~④를 만든다(인자는 스키마 기반 자리표시 값).

    비교군 전용이다 — 자리표시 값을 모델이 베끼면 스키마 검증을 통과해 조용히 틀릴 수 있다
    (R-8 위험).
    """
    first = tools[0]
    name0 = tool_function(first).get("name", "")
    args0 = _placeholder_args(first)
    examples: list[dict[str, Any]] = [
        {
            "pattern": "single",
            "messages": [{"role": "user", "content": f"<question that needs {name0}>"}],
            "expected": {"tool_calls": [{"name": name0, "arguments": args0}]},
        }
    ]
    if len(tools) >= 2:
        name1 = tool_function(tools[1]).get("name", "")
        examples.append(
            {
                "pattern": "parallel",
                "messages": [
                    {"role": "user", "content": f"<question that needs {name0} and {name1}>"}
                ],
                "expected": {
                    "tool_calls": [
                        {"name": name0, "arguments": args0},
                        {"name": name1, "arguments": _placeholder_args(tools[1])},
                    ]
                },
            }
        )
    examples.append(
        {
            "pattern": "after_result",
            "messages": [
                {"role": "user", "content": f"<question that needs {name0}>"},
                _call_message([(name0, args0)], "call_example"),
                {
                    "role": "tool",
                    "tool_call_id": "call_example_1",
                    "name": name0,
                    "content": '{"result": "<value>"}',
                },
            ],
            "expected": {"text": "<final answer based on the result>"},
        }
    )
    examples.append(
        {
            "pattern": "no_tool",
            "messages": [{"role": "user", "content": "<question that needs no tool>"}],
            "expected": {"text": "<plain answer>"},
        }
    )
    return examples


def select_fewshot(
    data: dict[str, Any] | None,
    mode: str,
    tools: Sequence[dict[str, Any]],
    parallel_tool_calls: bool,
) -> FewshotSet | None:
    """요청에 쓸 few-shot 묶음을 고른다. mode=none이거나 도구가 없으면 None.

    - static: 데이터의 가상 도구 예시. 요청 도구 이름과 겹치는 예시 도구 이름에는 `_sample` 접미사를
      붙여 일관 치환한다.
    - dynamic: 요청 도구 이름으로 같은 패턴을 만든다(비교군 · R-8 위험).
    - `parallel_tool_calls=false`면 병렬 패턴(②)을 뺀다.
    """
    if mode == "none" or not tools:
        return None
    if mode == "dynamic":
        fs_tools = [copy.deepcopy(t) for t in tools]
        examples = _dynamic_examples(tools)
        fictional = False
    else:
        if data is None:
            return None
        work = copy.deepcopy({"tools": data["tools"], "examples": data["examples"]})
        _apply_rename(work, _rename_map(tool_names(work["tools"]), set(tool_names(tools))))
        fs_tools, examples, fictional = work["tools"], work["examples"], True
    if not parallel_tool_calls:
        examples = [ex for ex in examples if ex["pattern"] != "parallel"]
    return FewshotSet(tools=fs_tools, examples=examples, fictional=fictional)


def expected_message(example: dict[str, Any]) -> dict[str, Any]:
    """예시의 기대 답을 OpenAI assistant 메시지로 만든다(이력 직렬화 함수로 렌더하기 위함)."""
    expected = example["expected"]
    if expected.get("tool_calls"):
        calls = [(str(c["name"]), c.get("arguments") or {}) for c in expected["tool_calls"]]
        return _call_message(calls, "call_expected")
    return {"role": "assistant", "content": str(expected.get("text", ""))}


def example_turns(example: dict[str, Any]) -> list[Turn]:
    """예시 1건(대화 + 기대 답)을 턴 목록으로 렌더한다 — `serialize_turns`와 같은 표기."""
    return serialize_turns([*example["messages"], expected_message(example)])


def fewshot_contents_turns(fewshot: FewshotSet) -> list[Turn]:
    """contents 배치용 — 예시들을 가짜 턴으로 이어 붙인다."""
    turns: list[Turn] = []
    for ex in fewshot.examples:
        turns.extend(example_turns(ex))
    return merge_turns(turns)


def render_fewshot_block(fewshot: FewshotSet, lang: str) -> str:
    """system 배치용 「형식 예시」 절을 렌더한다(예시마다 역할 표지 대화)."""
    text = _FEWSHOT_TEXT.get(lang, _FEWSHOT_TEXT["en"])
    names = ", ".join(tool_names(fewshot.tools))
    intro = text["fictional"].replace("{names}", names) if fewshot.fictional else text["dynamic"]
    lines = [text["title"], intro]
    if fewshot.fictional:
        lines += ["", text["tools"], render_tool_list(fewshot.tools)]
    for i, ex in enumerate(fewshot.examples, 1):
        lines += ["", text["example"].replace("{n}", str(i)), render_transcript(example_turns(ex))]
    return "\n".join(lines)


# ─────────────────────────────── 파서 ───────────────────────────────

# 닫는 태그가 없는 마지막 블록도 블록으로 본다(평문으로 흘려보내지 않고 검증 대상으로).
_BLOCK_RE = re.compile(r"<tool_call>(.*?)(?:</tool_call>|\Z)", re.S)
_FENCE_RE = re.compile(r"^```(?:json)?[ \t]*\n?(.*?)\n?[ \t]*```$", re.S | re.I)
# 사유 코드 `schema_error:<인자명>`에 남길 수 있는 인자명(모델 유래 문자열 — 그 밖은 `?`)
_SAFE_ARG_RE = re.compile(r"[A-Za-z0-9_.-]{1,40}")


@dataclass
class ParsedCall:
    """검증을 통과한 도구 호출 1건."""

    name: str
    arguments: dict[str, Any]


@dataclass
class ParseResult:
    """모델 응답 1건의 판정 결과. 본문은 `content`·`tool_calls`에만 있다(진단용 필드는 판정값만)."""

    kind: ParseKind
    content: str | None = None
    tool_calls: list[ParsedCall] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    called_names: list[str] = field(default_factory=list)
    example_tool_called: bool = False
    # 태그·펜스 없이 호출 모양 JSON(`name|tool_name` + `arguments`)만 낸 평문 — 진단 판정값.
    # 프록시는 평문으로 돌려준다(보조 형식 확대 금지 · §3.4). 러너가 형식 실패·오탐으로 센다.
    call_like_text: bool = False


def safe_arg_name(arg: str) -> str:
    """사유 코드에 실을 인자명 — `[A-Za-z0-9_.-]{1,40}`·루트 표지 `*`만 그대로, 나머지는 `?`."""
    return arg if arg == "*" or _SAFE_ARG_RE.fullmatch(arg) else "?"


def _is_call_shape(obj: Any) -> bool:
    return isinstance(obj, dict) and "arguments" in obj and ("name" in obj or "tool_name" in obj)


def looks_like_call_text(text: str) -> bool:
    """텍스트 안의 최상위 JSON 객체 중 호출 모양(`name|tool_name` + `arguments`)이 있는가.

    응답 전체가 그 객체인 경우도 포함한다. 객체 안에 중첩된 객체는 따로 보지 않는다.
    """
    decoder = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except (ValueError, RecursionError):
            i = text.find("{", i + 1)
            continue
        if _is_call_shape(obj):
            return True
        i = text.find("{", end)
    return False


def _strip_fence(text: str) -> str:
    match = _FENCE_RE.match(text.strip())
    return match.group(1).strip() if match else text.strip()


def _parse_call_object(raw: str, legacy: bool = False) -> tuple[str | None, dict[str, Any] | None]:
    """블록 본문을 (이름, 인자)로 파싱한다. 형식 오류면 인자 None(이름은 읽혔으면 돌려준다)."""
    try:
        obj = json.loads(_strip_fence(raw))
    except json.JSONDecodeError:
        return None, None
    if not isinstance(obj, dict):
        return None, None
    name = obj.get("name")
    if legacy and name is None:
        name = obj.get("tool_name")
    if not isinstance(name, str) or not name:
        return None, None
    args = obj.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return name, None
    if args is None:
        args = {}
    return name, args if isinstance(args, dict) else None


def _aux_call(text: str) -> str | None:
    """보조 형식 — 응답 전체가 ```json 펜스 하나이고 내용이 호출 객체면 그 본문을 돌려준다."""
    match = _FENCE_RE.match(text.strip())
    if not match:
        return None
    try:
        obj = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if _is_call_shape(obj):
        return match.group(1)
    return None


_PY_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _type_ok(value: Any, kind: Any) -> bool:
    kinds = kind if isinstance(kind, list) else [kind]
    for k in kinds:
        if k == "null" and value is None:
            return True
        types = _PY_TYPES.get(str(k))
        if types is None:
            return True  # 모르는 타입 표기는 축소 검증에서 통과시킨다
        if isinstance(value, bool) and k in ("integer", "number"):
            continue
        if isinstance(value, types):
            return True
    return False


def _reduced_errors(args: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """jsonschema 없이 하는 축소 검증 — required 누락과 속성 최상위 타입만 본다."""
    bad = [str(name) for name in schema.get("required") or [] if name not in args]
    props = schema.get("properties") or {}
    for name, value in args.items():
        spec = props.get(name)
        if isinstance(spec, dict) and "type" in spec and not _type_ok(value, spec["type"]):
            bad.append(str(name))
    return bad


def _schema_errors(args: dict[str, Any], schema: Any) -> list[str]:
    """인자 스키마 위반 인자 이름 목록(루트 수준 위반은 `*`)."""
    if not isinstance(schema, dict) or not schema:
        return []
    if _validator_for is None:
        return _reduced_errors(args, schema)
    try:
        validator = _validator_for(schema)(schema)
        errors = list(validator.iter_errors(args))
    except Exception:  # 비정상 스키마 — 축소 검증으로 대체
        return _reduced_errors(args, schema)
    bad: list[str] = []
    for err in errors:
        if err.path:
            bad.append(str(err.path[0]))
        elif err.validator == "required" and isinstance(err.instance, dict):
            bad += [str(k) for k in err.validator_value if k not in err.instance]
        elif err.validator == "additionalProperties" and isinstance(err.instance, dict):
            known = schema.get("properties") or {}
            bad += [str(k) for k in err.instance if k not in known]
        else:
            bad.append("*")
    return list(dict.fromkeys(bad))


def parse_response(
    text: str,
    tools: Sequence[dict[str, Any]],
    tool_choice: Any,
    parallel_tool_calls: bool,
    example_tool_names: Iterable[str] = (),
) -> ParseResult:
    """모델 텍스트를 판정한다 — tool_calls · text · invalid(교정 대상).

    - `<tool_call>…</tool_call>` 블록 전부 추출(블록 안 ```json 펜스 허용). 블록이 없으면 보조 형식
      2종(응답 전체가 펜스 하나 + `{"name","arguments"}` 또는 구형 `{"tool_name","arguments"}`)만.
    - 블록마다 JSON → 이름 ∈ tools → 인자 스키마(jsonschema · 미설치면 축소 검증).
    - tool_choice=none이면 블록이 섞여도 평문이다.
    - 도구 에뮬레이션 중 빈 출력(junk 제거 후 공백뿐)은 교정 대상이다(`empty_output`).
    - 블록이 없으면 `call_like_text`(태그 없는 호출 모양 JSON)를 판정값으로 싣는다 — 동작은 그대로.
    - 사유 코드: json_error · unknown_tool · schema_error:<인자명> · missing_required_call ·
      wrong_tool · parallel_not_allowed · empty_output. 인자명은 `safe_arg_name`으로 정화한다.
    """
    clean = remove_llm_junk(text)
    mode, forced = choice_mode(tool_choice)
    if mode == "none" or not tools:
        return ParseResult(kind="text", content=clean)
    if not clean:
        return ParseResult(kind="invalid", reasons=["empty_output"])

    raw_blocks = [m.group(1) for m in _BLOCK_RE.finditer(clean)]
    outside = _BLOCK_RE.sub("", clean).strip()
    legacy = False
    if not raw_blocks:
        aux = _aux_call(clean)
        if aux is not None:
            raw_blocks, outside, legacy = [aux], "", True
    if not raw_blocks:
        call_like = looks_like_call_text(clean)
        if mode in ("required", "named"):
            return ParseResult(
                kind="invalid", reasons=["missing_required_call"], call_like_text=call_like
            )
        return ParseResult(kind="text", content=clean, call_like_text=call_like)

    schemas = {
        str(tool_function(t).get("name", "")): tool_function(t).get("parameters") for t in tools
    }
    examples = set(example_tool_names)
    reasons: list[str] = []
    calls: list[ParsedCall] = []
    called: list[str] = []
    for raw in raw_blocks:
        name, args = _parse_call_object(raw, legacy=legacy)
        if name is not None:
            called.append(name)
        if name is None or args is None:
            reasons.append("json_error")
            continue
        if name not in schemas:
            reasons.append("unknown_tool")
            continue
        bad = _schema_errors(args, schemas[name])
        if bad:
            reasons += [f"schema_error:{safe_arg_name(arg)}" for arg in bad]
            continue
        if mode == "named" and name != forced:
            reasons.append("wrong_tool")
            continue
        calls.append(ParsedCall(name=name, arguments=args))
    if not parallel_tool_calls and len(raw_blocks) >= 2:
        reasons.append("parallel_not_allowed")
    reasons = list(dict.fromkeys(reasons))
    example_called = any(n in examples for n in called)
    if reasons or not calls:
        return ParseResult(
            kind="invalid",
            reasons=reasons or ["json_error"],
            called_names=called,
            example_tool_called=example_called,
        )
    return ParseResult(
        kind="tool_calls",
        content=outside or None,
        tool_calls=calls,
        called_names=called,
        example_tool_called=example_called,
    )


def build_repair_message(
    result: ParseResult,
    tools: Sequence[dict[str, Any]],
    tool_choice: Any,
    parallel_tool_calls: bool,
    lang: str,
) -> str:
    """교정 재질의용 user 문단 — 어긴 규약 · 도구 이름 목록 · 형식 재안내."""
    text = _REPAIR_TEXT.get(lang, _REPAIR_TEXT["en"])
    known = set(tool_names(tools))
    phrases: list[str] = []
    for code in result.reasons:
        if code.startswith("schema_error:"):
            phrases.append(text["schema_error"].replace("{arg}", code.split(":", 1)[1]))
        elif code == "unknown_tool":
            unknown = ", ".join(n for n in dict.fromkeys(result.called_names) if n not in known)
            phrases.append(text["unknown_tool"].replace("{names}", unknown))
        else:
            phrases.append(text.get(code, code))
    parts = [
        text["head"].replace("{reasons}", "; ".join(phrases)),
        text["tools"].replace("{names}", ", ".join(tool_names(tools))),
        text["format"],
    ]
    mode, name = choice_mode(tool_choice)
    rules = _CHOICE_RULES.get(lang, _CHOICE_RULES["en"])
    if mode == "required":
        parts.append(rules["required"])
    elif mode == "named":
        parts.append(rules["named"].replace("{name}", name or ""))
    if not parallel_tool_calls:
        parts.append(_PARALLEL_RULES.get(lang, _PARALLEL_RULES["en"])[False])
    return " ".join(parts)
