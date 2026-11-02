"""plans/154 W1 — 계측 보강(M-1~M-5) 회귀 고정.

W0(`test_plan154_w0_instrumentation_gaps.py`)가 키 이름·속성을 고정했고, 여기서는 W1 구현의 나머지
계약을 고정한다(§9.2 W1 행):

- step 상한 미완주 → `status=done` 유지 + `incomplete: true`(실 holmes 경로 종단)
  · 판정·브리핑은 종전 그대로
- 예외 없이 마지막 턴에서 결론(`steps == max_steps`)은 미완주가 아니다(팀 리드 결정 2)
- 타임아웃·실패 잡에도 `elapsed_s`·`model`·`max_steps`
- `done` 감사에 가산 키 전부 + 종전 키 유지 · `tool_calls[]`에 원시 출력·인자 원문이 없다
- `investigation_audit_path` 미설정 = 기본 경로 · 설정 시 그 경로에만 쓴다
- `_StreamDrain.run`(call_stream 직접 소비)의 결과가 holmes `ToolCallingLLM.call()`과
  필드 단위로 같다

LLM·네트워크 0 — W0의 가짜 holmes `LLM`·메모리 도구, 스크립트 스트림만 쓴다.
"""

from __future__ import annotations

import copy
import json

import pytest
from holmes.core.models import ToolApprovalDecision, ToolCallResult
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.utils.stream import StreamEvents, StreamMessage
from sre_agent.application import investigation_jobs
from sre_agent.application.investigation_dispatcher import INSTRUMENTATION_KEYS
from sre_agent.diagnosis import DiagnosisResult, _StreamDrain
from sre_agent.interface.mcp_service import create_service, get_job_store
from sre_agent.settings import AgentSettings
from test_plan154_w0_instrumentation_gaps import (
    MODEL,
    RAW_SENTINEL,
    _agent_with_real_holmes,
    _dispatcher,
    _events,
    _FakeLLM,
    _one,
    _record,
    _run,
    fake_diagnose,
    make_job,
)

_DONE_KEYS = {
    "incomplete",
    "elapsed_s",
    "prefetch_s",
    "steps",
    "tool_calls_n",
    "model",
    "max_steps",
    "tool_calls",
    # plans/200 I-0 턴 계측(가산)
    "tool_batch_sizes",
    "max_tools_per_turn",
    "turns_n",
}
_LEGACY_DONE_KEYS = {
    "event",
    "investigation_id",
    "level",
    "confidence",
    "escalate",
    "signals",
    "tokens",
    "cost",
}
_RUN_META_KEYS = {"elapsed_s", "model", "max_steps"}


def _strip_volatile(obj):
    """실행마다 달라지는 도구 소요 시간(`elapsed_seconds`)만 지운다 — 나머지는 비교 대상."""
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k != "elapsed_seconds"}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _messages(agent) -> list[dict]:
    from holmes.core.prompt import build_initial_ask_messages

    return build_initial_ask_messages(
        initial_user_prompt="w1 조사",
        file_paths=None,
        tool_executor=agent.llm.tool_executor,
        system_prompt_additions=None,
    )


# ── step 상한 미완주 · 정상 완주 (실 holmes 종단) ─────────────────────────


def test_step_limit_job_is_done_with_incomplete_and_recovered_counts(tmp_path):
    """실 holmes step 상한 예외 → 잡 `done` + `incomplete: true`.

    회수한 step·도구 수·토큰이 잡·감사에 남는다.
    """
    agent = _agent_with_real_holmes(loop=True, max_steps=3)
    audit = tmp_path / "a.jsonl"
    job = _run(_dispatcher(lambda j: agent.ask("w1 상한"), audit), make_job("w1-limit"))
    record = _one(_events(audit), "done")

    assert job.status == "done"
    for view in (job.summary(), record):
        assert view["incomplete"] is True
        assert view["steps"] == 3
        assert view["tool_calls_n"] == 3
        assert [c["name"] for c in view["tool_calls"]] == ["echo"] * 3
    assert record["tokens"] == 3 * 120  # 미완주 토큰 보존(종전 0)
    assert "RAW-" not in json.dumps(record, ensure_ascii=False)


def test_incomplete_partial_records_do_not_change_judgement_or_briefing(tmp_path):
    """미완주의 부분 도구 기록은 계측 키에만 쓴다.

    판정·브리핑은 도구 기록이 없던 종전과 같다(D-324 ⑦ 동작 무변경).
    """
    partial = [
        _record(
            "polestar_metric_trend", "success", RAW_SENTINEL, {"server_name": "w1", "kind": "cpu"}
        )
    ]
    with_partial = _run(
        _dispatcher(fake_diagnose(incomplete=True, outputs=partial), tmp_path / "a.jsonl"),
        make_job("w1-shield-a"),
    )
    legacy = _run(
        _dispatcher(fake_diagnose(incomplete=True, outputs=[]), tmp_path / "b.jsonl"),
        make_job("w1-shield-b"),
    )

    assert with_partial.verdict == legacy.verdict
    assert with_partial.briefing == legacy.briefing
    assert with_partial.summary()["tool_calls_n"] == 1 and legacy.summary()["tool_calls_n"] == 0


def test_normal_completion_is_not_incomplete(tmp_path):
    """정상 완주 → `incomplete: false` · steps = holmes 턴 수(도구 1 + 결론 1)."""
    agent = _agent_with_real_holmes(loop=False, max_steps=5)
    audit = tmp_path / "a.jsonl"
    job = _run(_dispatcher(lambda j: agent.ask("w1 완주"), audit), make_job("w1-ok"))

    assert job.status == "done"
    for view in (job.summary(), _one(_events(audit), "done")):
        assert view["incomplete"] is False
        assert view["steps"] == 2


def test_forced_conclusion_on_last_step_stays_complete():
    """마지막 턴(도구 없이 호출)에서 텍스트로 결론 → 예외 없음 → 미완주가 아니다.

    팀 리드 결정 2 — `steps == max_steps` 판단은 채점기 몫이다.
    """
    agent = _agent_with_real_holmes(loop=False, max_steps=2)
    r = agent.ask("w1 마지막 턴")

    assert r.incomplete is False
    assert r.steps == 2 == agent.settings.max_steps


# ── 타임아웃 · 실패 ────────────────────────────────────────────────


def test_timeout_job_has_run_meta_only(tmp_path):
    """타임아웃 잡·감사에는 얻을 수 있는 키(`elapsed_s`·`model`·`max_steps`)만.

    조사 스레드가 버려지므로 부분 토큰은 만들지 않는다.
    """
    import threading

    release = threading.Event()

    def hanging(job):
        release.wait()
        return DiagnosisResult(answer="")

    audit = tmp_path / "a.jsonl"
    try:
        job = _run(_dispatcher(hanging, audit, timeout_seconds=0.2), make_job("w1-timeout"))
    finally:
        release.set()
    summary = job.summary()
    record = _one(_events(audit), "timeout")

    assert job.status == "timeout"
    assert _RUN_META_KEYS <= set(summary) and _RUN_META_KEYS <= set(record)
    assert not ({"incomplete", "steps", "tool_calls"} & set(summary))
    assert summary["tokens"] == 0 and record["timeout_s"] == 0.2  # 종전 키 그대로
    assert summary["model"] == MODEL and summary["max_steps"] == 7


def test_failed_job_has_run_meta(tmp_path):
    """조사 함수가 예외로 끝난 `failed` 잡·감사에도 `elapsed_s`·`model`·`max_steps`."""

    def broken(job):
        raise RuntimeError("w1 조사 실패")

    audit = tmp_path / "a.jsonl"
    job = _run(_dispatcher(broken, audit), make_job("w1-failed"))
    record = _one(_events(audit), "failed")

    assert job.status == "failed"
    for view in (job.summary(), record):
        assert view["elapsed_s"] >= 0
        assert view["model"] == MODEL and view["max_steps"] == 7
    assert "w1 조사 실패" in record["error"]


# ── done 감사 · 도구 기록 요약 ─────────────────────────────────────────


def test_done_audit_keeps_legacy_keys_and_adds_all_instrumentation(tmp_path):
    """`done` 감사 = 종전 키 + 가산 키 전부(가산만 — R-8)."""
    audit = tmp_path / "a.jsonl"
    _run(_dispatcher(fake_diagnose(tokens=11), audit), make_job("w1-keys"))
    record = _one(_events(audit), "done")

    assert _LEGACY_DONE_KEYS <= set(record)
    assert _DONE_KEYS <= set(record)
    # 공개 상수(다른 테스트가 감사 키 집합에서 빼는 기준)가 실제 가산 키와 어긋나지 않는다.
    assert set(INSTRUMENTATION_KEYS) == _DONE_KEYS
    assert set(record) - set(INSTRUMENTATION_KEYS) == _LEGACY_DONE_KEYS | {"ts"}
    assert record["tokens"] == 11


def test_tool_calls_digest_excludes_raw_output_error_and_arg_values(tmp_path):
    """`tool_calls[]`는 이름·인자 해시·상태만 — 원시 출력·오류 본문·인자 원문이 잡·감사에 없다."""
    secret = "w1-arg-secret-host"
    outputs = [
        _record("polestar_metric_trend", "success", RAW_SENTINEL, {"server_name": secret}),
        _record(
            "polestar_incident_alarms",
            "error",
            "",
            {"server_name": secret, "kind": "cpu"},
            error=f"{RAW_SENTINEL}-err",
        ),
    ]
    audit = tmp_path / "a.jsonl"
    job = _run(_dispatcher(fake_diagnose(outputs=outputs), audit), make_job("w1-digest"))

    for calls in (job.summary()["tool_calls"], _one(_events(audit), "done")["tool_calls"]):
        dumped = json.dumps(calls, ensure_ascii=False)
        assert RAW_SENTINEL not in dumped and secret not in dumped
        assert [c["status"] for c in calls] == ["success", "error"]
        assert calls[0]["args_digest"] != calls[1]["args_digest"]  # 인자가 다르면 다른 해시


# ── 감사 경로 분리 (investigation_audit_path) ───────────────────────────


def _service_settings(**overrides) -> AgentSettings:
    """실 dispatcher를 배선하되 조사 LLM 게이트에서 스텁으로 끝나게 한다(LLM 0)."""
    return AgentSettings(
        _env_file=None,
        model=MODEL,
        api_key=None,
        max_steps=3,
        gemini_api_key=None,
        investigation_llm_enabled=None,
        service_bearer_token=None,
        **overrides,
    )


def _submit(mcp) -> None:
    get_job_store(mcp).submit(
        {
            "contract_version": "1",
            "event": {"serverName": "w1-audit", "hostname": "w1-audit.local", "severity": 2},
            "decision": {"fingerprint": "w1-audit"},
        }
    )


def test_audit_path_unset_uses_default_path(tmp_path, monkeypatch):
    """미설정(None) = 종전 기본 경로 — JobStore·dispatcher 양쪽 모두."""
    default = tmp_path / "default.jsonl"
    monkeypatch.setattr(investigation_jobs, "_DEFAULT_AUDIT_PATH", default)
    mcp = create_service(_service_settings())
    _submit(mcp)

    events = [e["event"] for e in _events(default)]
    assert {"accepted", "running", "stub", "terminal"} <= set(events)  # stub = dispatcher 기록


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_audit_path_falls_back_to_default(blank, tmp_path, monkeypatch):
    """빈 값(`INVESTIGATION_AUDIT_PATH=`)은 None과 같다.

    `Path("")`(현재 디렉토리)로 쓰지 않는다 — 기동 복구가 `IsADirectoryError`로 죽던 결함.
    """
    default = tmp_path / "default.jsonl"
    monkeypatch.setattr(investigation_jobs, "_DEFAULT_AUDIT_PATH", default)
    env = tmp_path / ".env"
    env.write_text(f"INVESTIGATION_AUDIT_PATH={blank}\n", encoding="utf-8")

    assert AgentSettings(_env_file=env).investigation_audit_path is None
    mcp = create_service(_service_settings(investigation_audit_path=blank))
    _submit(mcp)

    assert {"accepted", "running", "stub", "terminal"} <= {e["event"] for e in _events(default)}
    assert get_job_store(mcp).recover_on_start() == 0   # 기동 복구가 디렉토리를 읽다 죽지 않는다


def test_audit_path_set_writes_only_there(tmp_path, monkeypatch):
    """설정 시 그 파일에만 쓴다 — 기본 경로 파일은 생기지 않는다(운영 감사 오염 금지 · §4.7)."""
    default = tmp_path / "default.jsonl"
    custom = tmp_path / "bench" / "arm-a" / "audit.jsonl"
    monkeypatch.setattr(investigation_jobs, "_DEFAULT_AUDIT_PATH", default)
    mcp = create_service(_service_settings(investigation_audit_path=str(custom)))
    _submit(mcp)

    events = [e["event"] for e in _events(custom)]
    assert {"accepted", "running", "stub", "terminal"} <= set(events)
    assert not default.exists()


# ── call_stream 직접 소비 = holmes call() 동등성 ─────────────────────────


@pytest.mark.parametrize("max_steps", [5, 2])
def test_stream_drain_equals_holmes_call_on_real_loop(max_steps):
    """같은 가짜 LLM·도구에서 `_StreamDrain.run`과 holmes `call()`의 결과가 필드 단위로 같다."""
    via_call = _agent_with_real_holmes(loop=False, max_steps=max_steps)
    via_drain = _agent_with_real_holmes(loop=False, max_steps=max_steps)

    messages = _messages(
        via_call
    )  # 시스템 프롬프트에 현재 시각이 들어가므로 한 번만 만들어 복사한다
    a = via_call.llm.call(copy.deepcopy(messages))
    b = _StreamDrain(via_drain.llm).run(copy.deepcopy(messages))

    assert _strip_volatile(a.model_dump()) == _strip_volatile(b.model_dump())
    assert type(a.tool_calls[0]) is type(b.tool_calls[0])


def test_stream_drain_raises_same_step_limit_error_as_call():
    """상한 예외는 `call()`과 같은 문구로 그대로 오른다(회수는 `partial()`이 맡는다)."""
    via_call = _agent_with_real_holmes(loop=True, max_steps=3)
    via_drain = _agent_with_real_holmes(loop=True, max_steps=3)

    with pytest.raises(Exception) as call_err:
        via_call.llm.call(_messages(via_call))
    drain = _StreamDrain(via_drain.llm)
    with pytest.raises(Exception) as drain_err:
        drain.run(_messages(via_drain))

    assert str(call_err.value) == str(drain_err.value)
    partial = drain.partial(3)
    assert len(partial.tool_calls or []) == 3 and partial.total_tokens == 360


def _tool_event(call_id: str, name: str, data: str) -> StreamMessage:
    tcr = ToolCallResult(
        tool_call_id=call_id,
        tool_name=name,
        description=f"{name} 호출",
        result=StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data=data, params={"id": call_id}
        ),
    )
    return StreamMessage(event=StreamEvents.TOOL_RESULT, data=tcr.to_client_dict())


class _ScriptedLLM(ToolCallingLLM):
    """라운드별로 정해진 이벤트를 내는 `call_stream` — 승인 재개·frontend 정지·dedup 경로 비교용."""

    def __init__(self, rounds: list[list[StreamMessage]]) -> None:
        super().__init__(
            tool_executor=ToolExecutor([]), max_steps=5, llm=_FakeLLM(False), tool_results_dir=None
        )
        self._rounds = list(rounds)
        self.seen: list[dict] = []

    def call_stream(self, **kwargs):  # type: ignore[override]
        self.seen.append(
            {
                k: kwargs.get(k)
                for k in ("msgs", "tool_decisions", "tool_number_offset", "iteration_offset")
            }
        )
        yield from self._rounds.pop(0)

    def _prompt_for_approval_decisions(self, pending_approvals, approval_callback=None):  # noqa: ANN001
        return [
            ToolApprovalDecision(tool_call_id=p["tool_call_id"], approved=False)
            for p in pending_approvals
        ]


def _approval_then_answer() -> list[list[StreamMessage]]:
    """1라운드: 도구 2건 + 승인 대기 → 2라운드: 같은 id 재실행(dedup 대상) + 결론."""
    msgs = [{"role": "user", "content": "q"}]
    return [
        [
            _tool_event("c1", "a", "A1"),
            StreamMessage(
                event=StreamEvents.TOKEN_COUNT, data={"metadata": {"costs": {"total_tokens": 40}}}
            ),
            _tool_event("c2", "b", "B1"),
            StreamMessage(
                event=StreamEvents.APPROVAL_REQUIRED,
                data={
                    "content": None,
                    "messages": msgs + [{"role": "assistant", "content": "x"}],
                    "pending_approvals": [
                        {"tool_call_id": "c2", "tool_name": "b", "description": "", "params": {}}
                    ],
                    "pending_frontend_tool_calls": [],
                    "num_llm_calls": 2,
                    "costs": {"total_tokens": 100, "total_cost": 0.1},
                },
            ),
        ],
        [
            _tool_event("c2", "b", "B2"),
            StreamMessage(
                event=StreamEvents.ANSWER_END,
                data={
                    "content": "결론",
                    "messages": msgs,
                    "metadata": {"finish_reason": "stop"},
                    "num_llm_calls": 3,
                    "costs": {"total_tokens": 50, "total_cost": 0.05},
                },
            ),
        ],
    ]


def _frontend_pause() -> list[list[StreamMessage]]:
    return [
        [
            _tool_event("c1", "a", "A1"),
            StreamMessage(
                event=StreamEvents.APPROVAL_REQUIRED,
                data={
                    "content": None,
                    "messages": [],
                    "pending_approvals": [],
                    "pending_frontend_tool_calls": [
                        {"tool_call_id": "f1", "tool_name": "fe", "arguments": {}}
                    ],
                    "num_llm_calls": 1,
                    "costs": {"total_tokens": 30, "total_cost": 0.0},
                },
            ),
        ]
    ]


@pytest.mark.parametrize("script", [_approval_then_answer, _frontend_pause])
def test_stream_drain_equals_holmes_call_on_scripted_streams(script):
    """승인 재개(라운드 누적·턴 수 대입·id dedup)·frontend 정지 경로도 `call()`과 같다.

    결과(`LLMResult`)와 라운드별 `call_stream` 재호출 인자 둘 다 비교한다.
    """
    llm_call, llm_drain = _ScriptedLLM(script()), _ScriptedLLM(script())

    a = ToolCallingLLM.call(llm_call, [{"role": "user", "content": "q"}])
    b = _StreamDrain(llm_drain).run([{"role": "user", "content": "q"}])

    assert a.model_dump() == b.model_dump()
    assert llm_call.seen == llm_drain.seen
