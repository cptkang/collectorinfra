"""plans/188 W5(C8) — Gemma 도구 호출 마크업을 클라이언트 측에서 `tool_calls`로 변환.

실 LLM 0 — 단위는 litellm 실 타입 응답 객체, 종단은 가짜 OpenAI 호환 LLM(`investigation_bench.
fake_llm.FakeLLM` 하위 클래스 · 127.0.0.1)과 가짜 세계 MCP 서버(`WorldServer`)로 실 holmes를 돌린다.
몽키패치는 프로세스 전역이라 모든 테스트가 `DefaultLLM.completion`을 원복한다.
"""

from __future__ import annotations

import inspect
import json
import logging
import socket
from pathlib import Path
from typing import Any

import pytest
from holmes.core.llm import DefaultLLM
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Message,
    ModelResponse,
    Usage,
)
from sre_agent.application.briefing_builder import UNRESOLVED_PREFIX, build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.diagnosis import DiagnosisAgent
from sre_agent.domain.answer_markup import (
    GEMMA_STRING_DELIM,
    GEMMA_TOOL_CALL_CLOSE,
    GEMMA_TOOL_CALL_OPEN,
    MALFORMED_ANSWER_SUMMARY,
    markup_log_excerpt,
)
from sre_agent.infrastructure import tool_call_markup_guard as guard_mod
from sre_agent.infrastructure.llm_message_guard import install_system_message_guard
from sre_agent.infrastructure.tool_call_markup_guard import (
    MarkupParseError,
    install_tool_call_markup_guard,
    parse_gemma_tool_calls,
    rewrite_tool_call_markup,
)
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings

# 폐쇄망 실측 원문(2026-10-19 · 응답 content 그대로).
RAW = (
    '<|tool_call>call:TodoWrite{todos:[{content:<|"|>Identify server name and data source for the '
    'pod stg-app-rcdg-query-api-deploy-59b679b7b7-47b59<|"|>,id:<|"|>1<|"|>,status:<|"|>in_progress'
    '<|"|>},{content:<|"|>Check for alarms and incidents around 2026-10-19T10:38:41<|"|>,id:<|"|>2'
    '<|"|>,status:<|"|>in_progress<|"|>},{content:<|"|>Analyze core resource metrics (CPU, Memory, '
    'Disk, Network) for the identified server<|"|>,id:<|"|>3<|"|>,status:<|"|>pending<|"|>}]}'
    '<tool_call|><|tool_call>call:polestar_incident_alarms{lookback_minutes:120,reference_time:'
    '<|"|>2026-10-19T10:38:41<|"|>}<tool_call|>'
)
TODOS = [
    {"content": "Identify server name and data source for the pod "
                "stg-app-rcdg-query-api-deploy-59b679b7b7-47b59",
     "id": "1", "status": "in_progress"},
    {"content": "Check for alarms and incidents around 2026-10-19T10:38:41", "id": "2",
     "status": "in_progress"},
    {"content": "Analyze core resource metrics (CPU, Memory, Disk, Network) for the identified "
                "server", "id": "3", "status": "pending"},
]
Q = '<|"|>'


def _block(name: str, args: str) -> str:
    return f"<|tool_call>call:{name}{args}<tool_call|>"


def _resp(content: Any, tool_calls: list | None = None, finish: str = "stop") -> ModelResponse:
    return ModelResponse(
        id="chatcmpl-x",
        model="m",
        choices=[Choices(finish_reason=finish,
                         message=Message(content=content, tool_calls=tool_calls))],
        usage=Usage(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )


def _args(resp: ModelResponse) -> list[tuple[str, Any]]:
    return [(t.function.name, json.loads(t.function.arguments))
            for t in resp.choices[0].message.tool_calls]


@pytest.fixture
def clean_completion():
    """가드 없는 holmes 원본으로 시작하고, 끝나면 테스트 전 상태로 되돌린다."""
    saved = DefaultLLM.__dict__["completion"]
    DefaultLLM.completion = inspect.unwrap(saved)  # type: ignore[method-assign]
    yield DefaultLLM
    DefaultLLM.completion = saved  # type: ignore[method-assign]


# ── 파서 ──────────────────────────────────────────────────────────


def test_raw_fixture_parsed():
    calls, rest = parse_gemma_tool_calls(RAW)
    assert [n for n, _ in calls] == ["TodoWrite", "polestar_incident_alarms"]
    assert calls[0][1] == {"todos": TODOS}
    assert calls[1][1] == {"lookback_minutes": 120, "reference_time": "2026-10-19T10:38:41"}
    assert rest == ""


def test_string_special_chars_preserved():
    inner = 'a:b,c{d}e[f]"g\\h\n한글 <tool_call|> <|tool_call>call:x{y:1}'
    calls, rest = parse_gemma_tool_calls(_block("t", f"{{q:{Q}{inner}{Q},n:1}}"))
    assert calls == [("t", {"q": inner, "n": 1})]
    assert rest == ""
    # JSON 왕복도 정확(따옴표·역슬래시·줄바꿈 이스케이프)
    out = rewrite_tool_call_markup(_resp(_block("t", f"{{q:{Q}{inner}{Q}}}")))
    assert _args(out) == [("t", {"q": inner})]


def test_scalars():
    calls, _ = parse_gemma_tool_calls(
        _block("t", "{a:1,b:-2,c:3.5,d:-0.25,e:1e3,f:true,g:false,h:null}"))
    assert calls[0][1] == {"a": 1, "b": -2, "c": 3.5, "d": -0.25, "e": 1000.0,
                           "f": True, "g": False, "h": None}
    assert isinstance(calls[0][1]["a"], int)


def test_empty_object_array_and_whitespace():
    calls, _ = parse_gemma_tool_calls(
        _block("e", "{}") + _block("t", "{ a : [ ] , b : { } , c : [ 1 , [ 2 ] , { d : 3 } ] }"))
    assert calls == [("e", {}), ("t", {"a": [], "b": {}, "c": [1, [2], {"d": 3}]})]


def test_quoted_key_accepted():
    calls, _ = parse_gemma_tool_calls(_block("t", f"{{{Q}my key{Q}:1}}"))
    assert calls == [("t", {"my key": 1})]


BROKEN = [
    "<|tool_call>call:t{a:1}",                       # 닫는 표식 없음
    _block("t", "{a:{b:1}"),                         # 중괄호 불균형
    _block("t", "{a:[1,2}"),                         # 대괄호 불균형
    _block("t", f"{{a:{Q}abc}}"),                    # 미종결 문자열
    _block("t", "{a:abc}"),                          # 잘못된 리터럴
    _block("t", "{a:True}"),                         # 대문자 리터럴
    _block("t", "{a:1,}"),                           # 꼬리 쉼표
    _block("t", '{a:"x"}'),                          # 구분자가 아닌 큰따옴표
    _block("t", "{a:1}x"),                           # 객체 뒤 잔여
    "<|tool_call>t{a:1}<tool_call|>",                # call: 없음
    "<|tool_call>call:{a:1}<tool_call|>",            # 이름 없음
    _block("ok", "{a:1}") + _block("bad", "{a:}"),   # 둘째 블록 실패 → 부분 변환 금지
    _block("ok", "{a:1}") + " 남은 <tool_call|>",     # 블록 밖 마크업 잔존
    _block("ok", "{a:1}") + " call:x{y:1}",          # 블록 밖 call: 표기 잔존
    _block("t", "{a:1,a:2}"),                        # 중복 키
    _block("t", "{a:1e400}"),                        # 비유한 숫자(inf)
    _block("t", "{a:-1e400}"),
]


@pytest.mark.parametrize("content", BROKEN)
def test_broken_blocks_raise(content):
    with pytest.raises(MarkupParseError):
        parse_gemma_tool_calls(content)


@pytest.mark.parametrize("content", BROKEN)
def test_broken_blocks_return_original_with_warning(content, caplog):
    resp = _resp(content)
    with caplog.at_level(logging.WARNING, logger=guard_mod.__name__):
        assert rewrite_tool_call_markup(resp) is resp
    assert resp.choices[0].message.content == content and resp.choices[0].message.tool_calls is None
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "원본 응답 유지" in warns[0]
    assert GEMMA_STRING_DELIM not in warns[0]   # markup_log_excerpt 정규화


def test_excerpt_cut_inside_string_hides_value_start():
    raw = "x" * 180 + "{api_key:" + Q + "sk-SECRETSECRETSECRET" + Q + "}"
    out = markup_log_excerpt(raw)
    assert out == "x" * 180 + "{api_key:"
    assert "sk-" not in out and Q not in out


def test_excerpt_even_delimiters_unchanged():
    raw = "a" + Q + "b" + Q + "c" * 300
    assert markup_log_excerpt(raw) == raw.replace(Q, '"')[:200]


def test_warning_excerpt_bounded(caplog):
    long = _block("t", f"{{a:{Q}" + "x" * 500 + "}")
    with caplog.at_level(logging.WARNING, logger=guard_mod.__name__):
        rewrite_tool_call_markup(_resp(long))
    assert "x" * 300 not in caplog.records[-1].getMessage()


# ── 응답 변환 ─────────────────────────────────────────────────────


def test_rewrite_raw_fixture(caplog):
    resp = _resp(RAW)
    with caplog.at_level(logging.INFO, logger=guard_mod.__name__):
        out = rewrite_tool_call_markup(resp)
    assert out is not resp and isinstance(out, ModelResponse)
    # 원본 불변
    assert resp.choices[0].message.content == RAW and resp.choices[0].message.tool_calls is None
    assert resp.choices[0].finish_reason == "stop"
    msg = out.choices[0].message
    assert msg.content is None and out.choices[0].finish_reason == "tool_calls"
    assert all(isinstance(t, ChatCompletionMessageToolCall) and t.type == "function"
               for t in msg.tool_calls)
    assert _args(out) == [
        ("TodoWrite", {"todos": TODOS}),
        ("polestar_incident_alarms",
         {"lookback_minutes": 120, "reference_time": "2026-10-19T10:38:41"}),
    ]
    ids = [t.id for t in msg.tool_calls]
    assert len(set(ids)) == 2 and all(i.startswith("call_") for i in ids)
    assert out.usage.total_tokens == 18 and out.id == "chatcmpl-x"
    # holmes가 대화에 붙이는 형태(tool_calling_llm: model_dump(exclude_*))
    dumped = msg.model_dump(exclude_defaults=True, exclude_unset=True, exclude_none=True)
    assert "content" not in dumped and len(dumped["tool_calls"]) == 2
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1 and "2건" in infos[0] and "TodoWrite" in infos[0]
    assert "2026-10-19T10:38:41" not in infos[0]   # 인자 원문은 싣지 않는다


def test_ids_unique_across_calls():
    a = rewrite_tool_call_markup(_resp(_block("t", "{}")))
    b = rewrite_tool_call_markup(_resp(_block("t", "{}")))
    assert a.choices[0].message.tool_calls[0].id != b.choices[0].message.tool_calls[0].id


def test_narrative_around_blocks_kept_as_content():
    content = "분석을 시작합니다.\n" + _block("t", "{a:1}") + "\n이어서 확인합니다.  "
    out = rewrite_tool_call_markup(_resp(content))
    assert out.choices[0].message.content == "분석을 시작합니다.\n\n이어서 확인합니다."
    assert _args(out) == [("t", {"a": 1})]


def test_whitespace_only_rest_becomes_none():
    out = rewrite_tool_call_markup(_resp("  \n" + _block("t", "{}") + "\n"))
    assert out.choices[0].message.content is None


EXISTING_CALL = ChatCompletionMessageToolCall(
    id="c1", type="function", function={"name": "t", "arguments": "{}"})


@pytest.mark.parametrize("resp", [
    _resp("정상 서술 — 원인은 디스크 포화(polestar_metric_trend)"),
    _resp(None, tool_calls=[EXISTING_CALL], finish="tool_calls"),
    _resp(RAW, tool_calls=[EXISTING_CALL], finish="tool_calls"),   # tool_calls 이미 있음
    _resp(""),
    _resp(None),
    _resp('JSON 인용 {"a": 1} 과 call: 설명'),
])
def test_normal_response_identity(resp):
    assert rewrite_tool_call_markup(resp) is resp


@pytest.mark.parametrize("obj", [object(), None, {"choices": []}, ModelResponse(choices=[])])
def test_unexpected_types_identity(obj):
    assert rewrite_tool_call_markup(obj) is obj


def test_no_literal_redefinition_in_guard_module():
    src = Path(guard_mod.__file__).read_text(encoding="utf-8")
    for lit in (GEMMA_TOOL_CALL_OPEN, GEMMA_TOOL_CALL_CLOSE, GEMMA_STRING_DELIM):
        assert lit not in src


# ── 설치 · 두 가드 상호작용 ───────────────────────────────────────


COMPACTED = [
    {"role": "system", "content": "s"},
    {"role": "user", "content": "u"},
    {"role": "system", "content": "compacted"},
]


def _fake_base(seen: list):
    def completion(self, messages, *args, **kwargs):  # noqa: ANN001, ANN202
        seen.append(messages)
        return _resp(_block("t", "{a:1}"))

    return completion


TOOLS = [{"type": "function", "function": {"name": "t", "parameters": {}}}]


@pytest.mark.parametrize("order", ["system_first", "markup_first"])
def test_both_guards_work_in_any_order_and_reinstall_is_noop(clean_completion, order):
    seen: list = []
    clean_completion.completion = _fake_base(seen)
    installs = ([install_system_message_guard, install_tool_call_markup_guard]
                if order == "system_first"
                else [install_tool_call_markup_guard, install_system_message_guard])
    assert [f() for f in installs] == [True, True]
    # functools.wraps가 바깥 래퍼에 안쪽 표식을 복사하지만, 그 가드는 실제로 사슬 안에 있으므로
    # 「이미 설치됨」 판정이 맞다 — 재설치는 둘 다 False이고 래핑 층은 2개 그대로다.
    assert install_system_message_guard() is False
    assert install_tool_call_markup_guard() is False
    layers, fn = 0, clean_completion.completion
    while hasattr(fn, "__wrapped__"):
        layers, fn = layers + 1, fn.__wrapped__
    assert layers == 2

    out = clean_completion.completion(None, COMPACTED, TOOLS, "auto")
    assert [m["role"] for m in seen[-1]] == ["system", "user", "user"]   # D-213 가드 작동
    assert _args(out) == [("t", {"a": 1})]                                # C8 가드 작동


@pytest.mark.parametrize("call", [
    lambda f: f(None, COMPACTED),                                   # 압축: tools 없음
    lambda f: f(None, COMPACTED, drop_params=True),
    lambda f: f(None, COMPACTED, None, "auto"),
    lambda f: f(None, COMPACTED, [], "auto"),
    lambda f: f(None, COMPACTED, TOOLS),                            # tool_choice 없음
    lambda f: f(None, COMPACTED, TOOLS, "required"),
    lambda f: f(None, messages=COMPACTED, tools=TOOLS, tool_choice="none"),
])
def test_calls_without_tools_auto_are_identity(clean_completion, call):
    """압축·요약(tools 없음)과 tools를 실제로 보내지 않는 호출은 원본 그대로.

    `DefaultLLM.completion`은 `tools`가 있고 `tool_choice == "auto"`일 때만 tools를 보낸다.
    """
    resp = _resp(_block("t", "{a:1}"))
    clean_completion.completion = lambda self, *a, **kw: resp
    install_tool_call_markup_guard()
    assert call(clean_completion.completion) is resp


@pytest.mark.parametrize("call", [
    lambda f: f(None, COMPACTED, TOOLS, "auto"),
    lambda f: f(None, COMPACTED, tools=TOOLS, tool_choice="auto", stream=False),
    lambda f: f(None, messages=COMPACTED, tools=TOOLS, tool_choice="auto"),
])
def test_calls_with_tools_auto_are_converted(clean_completion, call):
    clean_completion.completion = lambda self, *a, **kw: _resp(_block("t", "{a:1}"))
    install_tool_call_markup_guard()
    assert _args(call(clean_completion.completion)) == [("t", {"a": 1})]


def test_install_on_real_holmes_idempotent(clean_completion):
    assert install_tool_call_markup_guard() is True
    assert install_tool_call_markup_guard() is False


def test_flag_default_on():
    assert AgentSettings(_env_file=None, model="m").tool_call_markup_parse_enabled is True


@pytest.mark.parametrize("enabled", [True, False])
def test_diagnosis_agent_installs_by_flag(clean_completion, enabled):
    base = clean_completion.completion
    DiagnosisAgent(AgentSettings(
        _env_file=None, model="m", tool_call_markup_parse_enabled=enabled,
        system_message_position_fix=False, bash_write_guard_enabled=False,
    ))
    installed = getattr(clean_completion.completion, guard_mod._GUARD_FLAG, False)
    assert installed is enabled
    if not enabled:
        assert clean_completion.completion is base


# ── 종단: 가짜 LLM(1턴 Gemma 마크업 → 2턴 결론) + 가짜 세계 MCP ──────────────

_FIX = Path(__file__).resolve().parent / "fixtures" / "investigation_bench"
_E2E_CALL = _block(
    "polestar_incident_alarms",
    f"{{source:{Q}polestar{Q},server_name:{Q}web-01{Q},lookback_minutes:120,"
    f"reference_time:{Q}2026-10-19T10:38:41{Q}}}",
)
_E2E_ANSWER = "web-01 알람 이력 조회 결과 신규 장애 신호 없음(polestar_incident_alarms)"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def gemma_llm():
    from investigation_bench.fake_llm import FakeLLM, _completion

    class GemmaMarkupLLM(FakeLLM):
        """1턴째는 tool_calls 없이 Gemma 마크업 content, 그 뒤는 정상 결론."""

        def __init__(self) -> None:
            super().__init__({})
            self.requests: list[dict[str, Any]] = []

        def respond(self, body):  # noqa: ANN001, ANN201
            self.requests.append(json.loads(json.dumps(body)))
            turn = sum(1 for m in body.get("messages") or [] if m.get("role") == "assistant")
            content = _E2E_CALL if turn == 0 else _E2E_ANSWER
            return _completion(body, {"role": "assistant", "content": content}, "stop")

    llm = GemmaMarkupLLM()
    llm.start()
    yield llm
    llm.stop()


@pytest.fixture
def world(tmp_path):
    from investigation_bench import schema
    from investigation_bench.world_server import WorldServer
    from investigation_bench.worlds import load_worlds

    port = _free_port()
    ws = WorldServer(schema.load_snapshot(_FIX / "tool_schemas.json"),
                     load_worlds([_FIX / "worlds"]), tmp_path / "world_calls.jsonl")
    ws.start({"polestar": port})
    yield port, tmp_path / "world_calls.jsonl"
    ws.stop()


def _run_e2e(llm_port: int, world_port: int, tmp_path: Path, *, parse: bool):
    s = AgentSettings(
        _env_file=None,
        model="openai/fake-bench",
        api_base=f"http://127.0.0.1:{llm_port}/v1",
        api_key="dummy",
        gemini_api_key=None,
        service_bearer_token=None,
        investigation_llm_enabled=True,
        evidence_correlation_enabled=False,
        investigation_dedup_ttl_seconds=None,
        severity_judge_enabled=False,
        polestar_mcp_url=f"http://127.0.0.1:{world_port}/sse",
        tool_call_markup_parse_enabled=parse,
    )
    fn = mcp_service._default_diagnose_fn(s, mcp_service.ToolsetCheck())
    results: list = []

    def diagnose(job):  # noqa: ANN001, ANN202
        r = fn(job)
        results.append(r)
        return r

    disp = InvestigationDispatcher(s, diagnose_fn=diagnose, briefing_fn=build_briefing,
                                   audit_path=tmp_path / "audit.jsonl")
    job = InvestigationJob(
        investigation_id="id-w5", kind="alarm", status="running", created_at=0.0, updated_at=0.0,
        fingerprint="w5",
        payload={"event": {"serverName": "web-01", "severity": 2}, "decision": {"tier": "PAGE"}},
    )
    disp(job)
    disp.wait_workers()
    return job, results


def _world_calls(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_e2e_markup_parsed_tool_runs_and_investigation_concludes(
        clean_completion, gemma_llm, world, tmp_path):
    world_port, calls_path = world
    job, results = _run_e2e(gemma_llm.port, world_port, tmp_path, parse=True)

    calls = [c for c in _world_calls(calls_path) if c["tool"] == "polestar_incident_alarms"]
    assert len(calls) == 1
    assert calls[0]["args"] == {"source": "polestar", "server_name": "web-01",
                                "lookback_minutes": 120, "reference_time": "2026-10-19T10:38:41"}
    # 2턴째 요청에 assistant tool_calls + role=tool 결과가 실렸다(holmes 왕복)
    assert len(gemma_llm.requests) == 2
    second = gemma_llm.requests[1]["messages"]
    asst = [m for m in second if m.get("role") == "assistant"]
    assert asst and asst[-1]["tool_calls"][0]["function"]["name"] == "polestar_incident_alarms"
    assert not asst[-1].get("content")
    assert any(m.get("role") == "tool" for m in second)

    (result,) = results
    assert result.answer == _E2E_ANSWER
    assert "polestar_incident_alarms" in [t.tool_name for t in result.tool_outputs]
    b = job.briefing
    assert job.status == "done"
    assert MALFORMED_ANSWER_SUMMARY not in b["summary"]
    assert GEMMA_TOOL_CALL_OPEN not in json.dumps(b, ensure_ascii=False)


def test_e2e_flag_off_falls_back_to_w4_unresolved(clean_completion, gemma_llm, world, tmp_path):
    world_port, calls_path = world
    job, results = _run_e2e(gemma_llm.port, world_port, tmp_path, parse=False)

    # 도구 미실행(툴셋 선행검사 `list_sources`만 남는다)
    assert [c for c in _world_calls(calls_path) if c["tool"] == "polestar_incident_alarms"] == []
    assert len(gemma_llm.requests) == 1             # 마크업 본문을 최종 답으로 받고 끝
    assert results[0].answer == _E2E_CALL
    b = job.briefing
    assert job.status == "done"
    assert b["summary"] == UNRESOLVED_PREFIX + MALFORMED_ANSWER_SUMMARY
    assert GEMMA_TOOL_CALL_OPEN not in json.dumps(b, ensure_ascii=False)
