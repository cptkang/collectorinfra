"""규약 문구 · static few-shot 2차 교정 (plans/148 1단계 — 2차 내부망 측정 G-4 미달 대응).

- 병렬 규칙: 인자를 이미 아는 호출만 한꺼번에 · 의존 인자는 앞 도구 결과를 기다린다(S5 추측 병렬).
- 즉답 금지(좁게): 도구로만 알 수 있는 사실은 짐작하지 않는다
  — 「도구 불필요 → 평문」 줄은 유지(S4).
- 계획 ≠ 답: 계획·할 일 목록 뒤에 평문 종료하지 않는다(S6) — 특정 도구명 하드코딩 없음.
- few-shot 연쇄(chain) 예시: A 결과 값으로 B 호출.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import TIME_TOOL, WEATHER_TOOL

from fabrix_proxy import tool_protocol as tp

TOOLS = [WEATHER_TOOL, TIME_TOOL]
SCENARIOS = Path(__file__).resolve().parents[1] / "testdata" / "scenarios"

DEPENDENCY_RULE = {
    "en": "If an argument depends on another tool's result, call that tool first",
    "ko": "인자가 다른 도구의 결과에 달려 있으면",
}
GUESS_RULE = {
    "en": "If the answer needs facts that only the tools above can provide",
    "ko": "답에 위 도구로만 알 수 있는 사실이 필요하면",
}
NO_TOOL_RULE = {
    "en": "If no tool is needed, answer in plain text without any <tool_call> block.",
    "ko": "도구가 필요 없으면 <tool_call> 블록 없이 평문으로 답한다.",
}
PLAN_RULE = {
    "en": "Making a plan or a to-do list is not an answer.",
    "ko": "계획이나 할 일 목록을 세우는 것은 답이 아니다.",
}


def _render(lang: str, parallel: bool) -> str:
    template = tp.load_protocol_template(lang)
    return tp.render_protocol(TOOLS, "auto", parallel, template=template, lang=lang)


def _string_values(node: object) -> set[str]:
    if isinstance(node, dict):
        return set().union(*(_string_values(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(_string_values(v) for v in node))
    return {node} if isinstance(node, str) and len(node) >= 3 else set()


def _chain(data: dict) -> dict:
    return next(ex for ex in data["examples"] if ex["pattern"] == "chain")


# ─────────────────────────── 규약 문구 ───────────────────────────


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_parallel_rule_requires_known_arguments(lang: str) -> None:
    assert DEPENDENCY_RULE[lang] in _render(lang, True)
    # 한 번에 하나(False) 규칙은 그대로다 — 의존 문구가 붙지 않는다
    single = _render(lang, False)
    assert DEPENDENCY_RULE[lang] not in single
    assert tp._PARALLEL_RULES[lang][False] in single


def test_parallel_false_rule_text_unchanged() -> None:
    assert tp._PARALLEL_RULES["en"][False] == (
        "Call at most one tool per reply (a single <tool_call> block)."
    )
    assert tp._PARALLEL_RULES["ko"][False] == (
        "한 답에서는 도구를 하나만 호출한다(<tool_call> 블록 1개)."
    )


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("parallel", [True, False])
def test_guess_rule_sits_right_before_no_tool_rule(lang: str, parallel: bool) -> None:
    """즉답 금지는 「도구로만 알 수 있는 사실」로 좁히고, 평문 허용 줄 바로 앞에 둔다(S4 유지)."""
    lines = _render(lang, parallel).splitlines()
    no_tool = lines.index(NO_TOOL_RULE[lang])
    assert lines[no_tool - 1].startswith(GUESS_RULE[lang])


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_plan_rule_in_after_result_section(lang: str) -> None:
    text = _render(lang, True)
    head = "# After a tool result" if lang == "en" else "# 도구 결과를 받은 뒤"
    assert PLAN_RULE[lang] in text.split(head, 1)[1]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_protocol_template_has_no_specific_tool_names(lang: str) -> None:
    template = tp.load_protocol_template(lang)
    for name in ("write_todos", "task", "get_ticket", "get_person", "get_office"):
        assert name not in template


# ─────────────────────────── few-shot 연쇄 예시 ───────────────────────────


def test_chain_example_follows_after_result() -> None:
    patterns = [ex["pattern"] for ex in tp.load_fewshot()["examples"]]
    assert patterns == ["single", "parallel", "after_result", "chain", "no_tool"]


def test_chain_argument_comes_from_previous_result() -> None:
    """연쇄 예시의 기대 호출 인자는 직전 도구 결과에 든 값이다(질문에는 없다)."""
    chain = _chain(tp.load_fewshot())
    user_text = chain["messages"][0]["content"]
    tool_msg = chain["messages"][-1]
    assert tool_msg["role"] == "tool"
    result = json.loads(tool_msg["content"])
    calls = chain["expected"]["tool_calls"]
    assert len(calls) == 1
    (value,) = calls[0]["arguments"].values()
    assert value in result.values()
    assert value not in user_text
    assert calls[0]["name"] != tool_msg["name"]


def test_chain_kept_when_parallel_false() -> None:
    fs = tp.select_fewshot(tp.load_fewshot(), "static", TOOLS, False)
    assert fs is not None
    assert [ex["pattern"] for ex in fs.examples] == ["single", "after_result", "chain", "no_tool"]


def test_dynamic_has_no_chain() -> None:
    fs = tp.select_fewshot(None, "dynamic", TOOLS, True)
    assert fs is not None
    assert "chain" not in [ex["pattern"] for ex in fs.examples]


def test_chain_tools_counted_as_example_tools() -> None:
    """연쇄 예시가 부르는 도구도 예시 도구 오호출로 판정된다."""
    fs = tp.select_fewshot(tp.load_fewshot(), "static", TOOLS, True)
    assert fs is not None
    chain = _chain({"examples": fs.examples})
    called = {chain["messages"][-1]["name"], chain["expected"]["tool_calls"][0]["name"]}
    assert called <= fs.example_tool_names
    answer = tp.example_turns(chain)[-1][1]
    result = tp.parse_response(answer, TOOLS, "auto", True, fs.example_tool_names)
    assert result.example_tool_called


def test_chain_renamed_on_collision() -> None:
    clash = {
        "type": "function",
        "function": {"name": "example_get_status", "parameters": {"type": "object"}},
    }
    fs = tp.select_fewshot(tp.load_fewshot(), "static", [clash, WEATHER_TOOL], True)
    assert fs is not None
    chain = _chain({"examples": fs.examples})
    assert chain["expected"]["tool_calls"][0]["name"] == "example_get_status_sample"


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_chain_rendered_in_both_placements(lang: str) -> None:
    fs = tp.select_fewshot(tp.load_fewshot(), "static", TOOLS, True)
    assert fs is not None
    block = tp.render_fewshot_block(fs, lang)
    contents = "\n".join(text for _, text in tp.fewshot_contents_turns(fs))
    marker = '"name": "example_get_status", "arguments": {"order_id": "512"}'
    assert marker in block and marker in contents
    assert ("## Example 5" if lang == "en" else "## 예시 5") in block


def test_fewshot_disjoint_from_scenario_tools_and_values() -> None:
    """few-shot 본문에 시나리오 도구 이름·결과 값·누출 표지가 없다."""
    text = (tp._PACKAGE_DIR / "fewshot_default.json").read_text(encoding="utf-8")
    forbidden: set[str] = set()
    for path in SCENARIOS.glob("poc_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "poc_tools.json":
            forbidden |= set(data.get("tools") or {})  # {도구 이름: OpenAI 도구 JSON}
            continue
        forbidden |= {str(n) for n in data.get("tools") or [] if isinstance(n, str)}
        forbidden |= {str(m) for m in data.get("leak_markers") or []}
        forbidden |= _string_values(data.get("tool_results") or {})
        for case in data.get("cases") or []:
            forbidden |= {str(v) for v in case.get("reflect") or []}
    forbidden.discard("")
    assert forbidden, "시나리오 표지를 하나도 못 읽었다"
    assert sorted(w for w in forbidden if w in text) == []
