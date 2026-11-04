"""맨 JSON 도구 호출 — C7 판정 확장 · C8 변환 확장 · 도구 호출 점검 로그.

D-342 W4 부기 2 · D-343 부기 1.

폐쇄망 2026-10-30 실측: 조사 LLM 답 전체가 TodoWrite `todos` 배열 JSON이었고(도구 호출 0건),
그 JSON이 「1순위 원인」으로 화면에 실렸다. 실 LLM 0 — 단위는 litellm 실 타입, 종단은 가짜
OpenAI 호환 LLM + 가짜 세계 MCP로 실 holmes를 돌린다(픽스처는 W5 테스트 재사용).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
import test_plan188_w5_tool_call_parse as w5
from litellm.types.utils import ChatCompletionMessageToolCall, Function
from sre_agent.application.briefing_builder import UNRESOLVED_PREFIX
from sre_agent.domain.answer_markup import (
    MALFORMED_ANSWER_SUMMARY,
    TODO_WRITE_TOOL,
    has_tool_call_markup,
    parse_bare_json_tool_calls,
)
from sre_agent.infrastructure.tool_call_markup_guard import (
    install_tool_call_markup_guard,
    rewrite_tool_call_markup,
)
from test_plan188_w5_tool_call_parse import _args, _resp, _run_e2e, _world_calls

# W5 픽스처 재사용(모듈 속성으로 노출해야 pytest가 찾는다)
clean_completion = w5.clean_completion
world = w5.world

# 폐쇄망 2026-10-30 화면 원문(내용 요약 · 꼴 그대로).
TODOS = [
    {"id": "1", "content": "사건 구간의 전 알람 조회 (2026-10-30T03:38:14 이전 120분)",
     "status": "in_progress"},
    {"id": "2",
     "content": "CPU, 메모리, 디스크, 네트워크 지표 조회 (2026-10-30T03:38:14 이전 120분)",
     "status": "in_progress"},
    {"id": "3", "content": "서버 상태 및 토폴로지 확인", "status": "in_progress"},
    {"id": "4", "content": "프로세스 및 OS 구성 확인", "status": "in_progress"},
]
TODOS_RAW = json.dumps(TODOS, ensure_ascii=False)
ALARMS_ARGS = {"source": "polestar", "server_name": "web-01", "lookback_minutes": 120,
               "reference_time": "2026-10-19T10:38:41"}
TOOLS_SENT = frozenset({TODO_WRITE_TOOL, "polestar_incident_alarms"})


# ── 판정(domain) ──────────────────────────────────────────────────


@pytest.mark.parametrize(("text", "expected"), [
    (TODOS_RAW, [(TODO_WRITE_TOOL, {"todos": TODOS})]),
    (f"  {TODOS_RAW}\n", [(TODO_WRITE_TOOL, {"todos": TODOS})]),
    (f"```json\n{TODOS_RAW}\n```", [(TODO_WRITE_TOOL, {"todos": TODOS})]),
    (json.dumps({"todos": TODOS}), [(TODO_WRITE_TOOL, {"todos": TODOS})]),
    ('[{"content": "a", "status": "pending"}]',
     [(TODO_WRITE_TOOL, {"todos": [{"content": "a", "status": "pending"}]})]),
    (json.dumps({"name": "polestar_incident_alarms", "arguments": ALARMS_ARGS}),
     [("polestar_incident_alarms", ALARMS_ARGS)]),
    (json.dumps({"name": "t", "arguments": json.dumps({"a": 1})}), [("t", {"a": 1})]),
    (json.dumps({"type": "function", "name": "t", "parameters": {"a": 1}}), [("t", {"a": 1})]),
    (json.dumps([{"name": "a", "arguments": {}}, {"name": "b", "arguments": {"x": 2}}]),
     [("a", {}), ("b", {"x": 2})]),
])
def test_bare_json_parsed(text, expected):
    assert parse_bare_json_tool_calls(text) == expected
    assert has_tool_call_markup(text) is True


@pytest.mark.parametrize("text", [
    "",
    None,
    "web-01 CPU 사용률이 03:30부터 상승(polestar_metric_trend) — 유력 원인",
    f"원인 미확정. 계획:\n{TODOS_RAW}",                          # 서술 속 JSON 조각
    f"{TODOS_RAW}\n이상입니다.",                                   # JSON 뒤에 서술
    "[]",
    "{}",
    '{"a": 1}',
    '[1, 2]',
    '[{"content": "a", "status": "done"}]',                        # holmes 상태 값 아님
    '[{"content": "a", "status": "pending", "owner": "x"}]',       # 할 일 키 아님
    '[{"id": "1", "status": "pending"}]',                          # content 없음
    '{"todos": []}',
    '{"todos": [{"content": "a", "status": "pending"}], "x": 1}',
    '{"name": "t"}',                                               # 인자 없음
    '{"name": "t", "arguments": {}, "parameters": {}}',            # 인자 키 둘
    '{"name": "t", "arguments": [1]}',
    '{"name": "t", "arguments": "not json"}',
    '{"name": "a b", "arguments": {}}',
    '{"name": "t", "arguments": {}, "extra": 1}',
    '{"type": "tool", "name": "t", "arguments": {}}',
    '[{"name": "a", "arguments": {}}, {"x": 1}]',                  # 하나라도 아니면 전체 아님
    "```json\n{\"a\": 1}\n```",
    "```\n" + TODOS_RAW + "\n```\n뒷말",
])
def test_not_bare_json_call(text):
    assert parse_bare_json_tool_calls(text) is None
    assert has_tool_call_markup(text) is False


# ── 변환(infrastructure) ─────────────────────────────────────────


def test_todos_array_converted_to_todowrite():
    resp = _resp(TODOS_RAW)
    out = rewrite_tool_call_markup(resp, TOOLS_SENT, [])
    assert out is not resp
    assert _args(out) == [(TODO_WRITE_TOOL, {"todos": TODOS})]
    choice = out.choices[0]
    assert choice.message.content is None
    assert choice.finish_reason == "tool_calls"
    assert out.usage.total_tokens == 18
    assert resp.choices[0].message.content == TODOS_RAW            # 원본 불변


def test_named_call_converted():
    raw = json.dumps({"name": "polestar_incident_alarms", "arguments": ALARMS_ARGS})
    out = rewrite_tool_call_markup(_resp(raw), TOOLS_SENT, [])
    assert _args(out) == [("polestar_incident_alarms", ALARMS_ARGS)]


@pytest.mark.parametrize("tool_names", [frozenset(), frozenset({"polestar_incident_alarms"})])
def test_tool_not_sent_is_identity(tool_names, caplog):
    resp = _resp(TODOS_RAW)
    with caplog.at_level(logging.WARNING):
        assert rewrite_tool_call_markup(resp, tool_names, []) is resp
    assert "이번 요청에 없는 도구" in caplog.text


def test_default_args_do_not_convert_bare_json():
    """종전 호출 형태(`rewrite_tool_call_markup(resp)`) — 허용 도구가 없으니 맨 JSON은 그대로."""
    resp = _resp(TODOS_RAW)
    assert rewrite_tool_call_markup(resp) is resp


def _assistant_turn(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


def test_identical_repeat_of_last_turn_is_identity(caplog):
    history = [{"role": "user", "content": "q"}, _assistant_turn(TODO_WRITE_TOOL, {"todos": TODOS}),
               {"role": "tool", "tool_call_id": "c1", "content": "ok"}]
    resp = _resp(TODOS_RAW)
    with caplog.at_level(logging.WARNING):
        assert rewrite_tool_call_markup(resp, TOOLS_SENT, history) is resp
    assert "같은 호출 반복" in caplog.text


def test_changed_todos_after_last_turn_converted():
    """상태가 바뀐 TodoWrite(진행 갱신)는 반복이 아니다."""
    history = [_assistant_turn(TODO_WRITE_TOOL, {"todos": TODOS})]
    done = [{**TODOS[0], "status": "completed"}, *TODOS[1:]]
    out = rewrite_tool_call_markup(_resp(json.dumps(done, ensure_ascii=False)), TOOLS_SENT, history)
    assert _args(out) == [(TODO_WRITE_TOOL, {"todos": done})]


def test_native_tool_calls_untouched():
    tc = ChatCompletionMessageToolCall(id="c", type="function",
                                       function=Function(name="t", arguments="{}"))
    resp = _resp(TODOS_RAW, tool_calls=[tc], finish="tool_calls")
    assert rewrite_tool_call_markup(resp, TOOLS_SENT, []) is resp


# ── 턴 로그(가드 래퍼) ────────────────────────────────────────────

SENT = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in sorted(TOOLS_SENT)]
MSGS = [{"role": "user", "content": "q"}]


@pytest.mark.parametrize(("resp", "expect"), [
    (lambda: _resp(TODOS_RAW), "도구 호출 1건 경로=bare_json 도구=['TodoWrite']"),
    (lambda: _resp("<|tool_call>call:t{a:1}<tool_call|>"), "경로=gemma_markup 도구=['t']"),
    (lambda: _resp(None, tool_calls=[ChatCompletionMessageToolCall(
        id="c", type="function", function=Function(name="t", arguments="{}"))],
        finish="tool_calls"),
     "도구 호출 1건 경로=native 도구=['t']"),
    (lambda: _resp("결론: 이상 없음"),
     "도구 호출 0건 경로=none — holmes가 최종 답으로 받음(본문 9자 · 도구 호출 형식 섞임=False)"),
])
def test_turn_log(clean_completion, caplog, resp, expect):
    r = resp()
    clean_completion.completion = lambda self, *a, **kw: r
    install_tool_call_markup_guard()
    with caplog.at_level(logging.INFO, logger="sre_agent.infrastructure.tool_call_markup_guard"):
        clean_completion.completion(None, MSGS, SENT, "auto")
    assert expect in caplog.text


def test_no_turn_log_without_tools(clean_completion, caplog):
    r = _resp(TODOS_RAW)
    clean_completion.completion = lambda self, *a, **kw: r
    install_tool_call_markup_guard()
    with caplog.at_level(logging.INFO, logger="sre_agent.infrastructure.tool_call_markup_guard"):
        assert clean_completion.completion(None, MSGS) is r      # 압축·요약 호출
    assert "조사 LLM 턴" not in caplog.text


# ── 종단: 가짜 LLM(맨 JSON) + 가짜 세계 MCP · 실 holmes ─────────────

_FINAL = "web-01 알람 이력 조회 결과 신규 장애 신호 없음(polestar_incident_alarms)"


def _llm(turns):  # noqa: ANN001, ANN202
    from investigation_bench.fake_llm import FakeLLM, _completion

    class BareJsonLLM(FakeLLM):
        def __init__(self) -> None:
            super().__init__({})
            self.requests: list[dict[str, Any]] = []

        def respond(self, body):  # noqa: ANN001, ANN201
            self.requests.append(json.loads(json.dumps(body)))
            turn = sum(1 for m in body.get("messages") or [] if m.get("role") == "assistant")
            content = turns[min(turn, len(turns) - 1)]
            return _completion(body, {"role": "assistant", "content": content}, "stop")

    llm = BareJsonLLM()
    llm.start()
    return llm


@pytest.fixture
def bare_llm():
    llm = _llm([TODOS_RAW,
                json.dumps({"name": "polestar_incident_alarms", "arguments": ALARMS_ARGS}),
                _FINAL])
    yield llm
    llm.stop()


@pytest.fixture
def parrot_llm():
    """매 턴 같은 todos 배열만 낸다(진행 없음)."""
    llm = _llm([TODOS_RAW])
    yield llm
    llm.stop()


def test_e2e_bare_json_tool_calls_run(clean_completion, bare_llm, world, tmp_path, caplog):
    world_port, calls_path = world
    with caplog.at_level(logging.INFO):
        job, results = _run_e2e(bare_llm.port, world_port, tmp_path, parse=True)

    calls = [c for c in _world_calls(calls_path) if c["tool"] == "polestar_incident_alarms"]
    assert [c["args"] for c in calls] == [ALARMS_ARGS]
    assert len(bare_llm.requests) == 3
    (result,) = results
    assert result.answer == _FINAL
    assert [t.tool_name for t in result.tool_outputs] == [
        TODO_WRITE_TOOL, "polestar_incident_alarms"]
    b = job.briefing
    assert job.status == "done"
    assert MALFORMED_ANSWER_SUMMARY not in b["summary"]
    # 점검 로그 — 턴별 경로 + 조사 요약
    assert "경로=bare_json 도구=['TodoWrite']" in caplog.text
    assert "경로=bare_json 도구=['polestar_incident_alarms']" in caplog.text
    assert "도구 호출 0건 경로=none" in caplog.text
    assert ("조사 도구 호출 요약: LLM step=3 · 도구 호출 2건(오류 0) "
            "{'TodoWrite': 1, 'polestar_incident_alarms': 1}") in caplog.text


def test_e2e_parrot_stops_after_one_repeat_and_unresolved(
        clean_completion, parrot_llm, world, tmp_path, caplog):
    world_port, _ = world
    with caplog.at_level(logging.INFO):
        job, results = _run_e2e(parrot_llm.port, world_port, tmp_path, parse=True)

    assert len(parrot_llm.requests) == 2              # 1턴 변환 · 2턴 반복은 미변환 → 종결
    assert results[0].answer == TODOS_RAW
    b = job.briefing
    assert b["summary"] == UNRESOLVED_PREFIX + MALFORMED_ANSWER_SUMMARY
    assert "프로세스 및 OS 구성 확인" not in json.dumps(b, ensure_ascii=False)
    assert "같은 호출 반복" in caplog.text
    assert "답 도구 호출 형식 섞임=True" in caplog.text


def test_e2e_flag_off_bare_json_answer_unresolved(
        clean_completion, bare_llm, world, tmp_path, caplog):
    """변환이 꺼져 있어도 맨 JSON 답은 원인 서술로 실리지 않는다.

    종전에는 그 JSON이 「1순위 원인」으로 노출됐다.
    """
    world_port, calls_path = world
    with caplog.at_level(logging.INFO):
        job, results = _run_e2e(bare_llm.port, world_port, tmp_path, parse=False)

    assert [c for c in _world_calls(calls_path) if c["tool"] == "polestar_incident_alarms"] == []
    assert len(bare_llm.requests) == 1
    assert results[0].answer == TODOS_RAW
    b = job.briefing
    assert b["summary"] == UNRESOLVED_PREFIX + MALFORMED_ANSWER_SUMMARY
    assert "프로세스 및 OS 구성 확인" not in json.dumps(b, ensure_ascii=False)
    assert "조사 도구 호출 요약: LLM step=1 · 도구 호출 0건" in caplog.text
