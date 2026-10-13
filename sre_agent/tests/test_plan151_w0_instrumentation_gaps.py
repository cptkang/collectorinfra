"""plans/151 W0 — 계측 빈칸 M-1~M-5를 W1 완료 후 기대 동작으로 고정한다.

W0에서 `xfail(strict=True)`로 고정했고 W1(계측 보강)에서 해소해 마커를 지웠다(전부 통과).
키 이름은 계획 §7 W1 행 그대로 쓴다:
잡(`summary()` = `sre_get_investigation` 반환)·`done` 감사에 가산 키 `incomplete` · `elapsed_s` ·
`prefetch_s` · `steps` · `tool_calls_n` · `model` · `max_steps` ·
`tool_calls[{name, args_digest, status}]`(원시 출력 없음).
D-324 G-2 (a) — 가산 키는 상시(플래그 없음) · G-3 (a) — 미완주는 `status=done` 유지 +
`incomplete: true`(D-123 상태 어휘 불변).

조사 결과 쪽 속성 이름도 여기서 고정한다: `DiagnosisResult.incomplete`(기존) ·
`DiagnosisResult.steps`(신규 — holmes `LLMResult.num_llm_calls`). dispatcher는 구조적 Protocol로
읽으므로 가짜 결과도 같은 이름을 쓴다.

LLM·네트워크 0: dispatcher 쪽은 가짜 diagnose_fn(`test_investigation_dispatcher.py` 패턴),
DiagnosisAgent 쪽은 **실 holmes `ToolCallingLLM`**에 가짜 `LLM`(litellm `ModelResponse`를 직접
만든다)과 메모리 도구 1개를 물린다. `ask()`가 holmes를 `call`로 부르든 `call_stream`으로 부르든
같은 단언이 성립하게 하려는 것이다.

W0 실측(2026-10-13 · holmes 0.36.0): `ToolCallingLLM.call`은 `call_stream`을 끝까지 소비하는
래퍼이고, step 상한 예외는 `call`의 지역 변수 `all_tool_calls`·`accumulated_stats`
(tool_calling_llm.py:588·591)를 버린다. 회수하려면 `call_stream`의 `TOOL_RESULT`·`TOKEN_COUNT`
이벤트를 직접 소비해야 한다.
"""

from __future__ import annotations

import json
import threading
import time

from holmes.core.llm import LLM, ContextWindowUsage
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolParameter,
    Toolset,
    ToolsetStatusEnum,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor
from litellm import ModelResponse
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import InvestigationJob, JobStore
from sre_agent.diagnosis import DiagnosisAgent, DiagnosisResult, ToolCallRecord
from sre_agent.settings import AgentSettings

RAW_SENTINEL = "RAW-OUTPUT-SENTINEL-151"   # 잡·감사에 새면 안 되는 원시 출력 표지
MODEL = "openai/bench-fake"
_INSTR_KEYS = (
    "incomplete", "elapsed_s", "prefetch_s", "steps",
    "tool_calls_n", "model", "max_steps", "tool_calls",
)


def make_settings(**overrides) -> AgentSettings:
    """dispatcher 테스트 설정 — 검증 대상 필드를 명시해 `.env` 누수를 막는다."""
    defaults = {
        "model": MODEL,
        "api_key": None,
        "max_steps": 7,
        "gemini_api_key": "k",
        "service_bearer_token": None,
        "investigation_timeout_seconds": 300,
        "investigation_dedup_ttl_seconds": None,
        "investigation_max_concurrent": 2,
        "investigation_hourly_budget": None,
        "severity_judge_enabled": False,
    }
    return AgentSettings(_env_file=None, **{**defaults, **overrides})


def make_job(server: str, fingerprint: str = "fp") -> InvestigationJob:
    now = time.time()
    return InvestigationJob(
        investigation_id=f"id-{server}-{now}",
        kind="alarm",
        status="running",
        created_at=now,
        updated_at=now,
        fingerprint=fingerprint,
        payload={"event": {"serverName": server, "severity": 2}, "decision": {"tier": "PAGE"}},
    )


def _record(name: str, status: str, output: str, params: dict, error: str | None = None):
    return ToolCallRecord(name, f"{name} 호출", status, output, error=error, params=params)


def fake_diagnose(
    *,
    incomplete: bool = False,
    steps: int | None = 4,
    outputs: list[ToolCallRecord] | None = None,
    tokens: int = 50,
):
    """고정 결과를 돌려주는 diagnose_fn.

    `steps`는 W1 신설 속성이라 setattr로 싣는다(현행 dataclass도 받는다).
    """

    def _fn(job):
        outs = outputs if outputs is not None else [
            _record("polestar_metric_trend", "success", RAW_SENTINEL,
                    {"server_name": "w0-a", "kind": "cpu"}),
        ]
        result = DiagnosisResult(
            answer="[미완주] step 상한" if incomplete else "원인 서술 ← polestar_metric_trend",
            tool_calls=[o.description for o in outs],
            tool_outputs=outs,
            total_tokens=tokens,
            total_cost=0.0,
            incomplete=incomplete,
        )
        result.steps = steps  # type: ignore[attr-defined]
        return result

    return _fn


def _run(disp: InvestigationDispatcher, job: InvestigationJob) -> InvestigationJob:
    disp(job)
    disp.wait_workers(10)
    return job


def _events(path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _one(events: list[dict], name: str) -> dict:
    found = [e for e in events if e.get("event") == name]
    assert len(found) == 1, f"{name} 감사 {len(found)}건"
    return found[0]


def _dispatcher(diagnose_fn, audit, **kwargs) -> InvestigationDispatcher:
    kwargs.setdefault("timeout_seconds", 5.0)
    return InvestigationDispatcher(make_settings(), diagnose_fn=diagnose_fn,
                                   briefing_fn=build_briefing, audit_path=audit, **kwargs)


# ── M-1 · 미완주가 done으로만 기록된다 ─────────────────────────────


def test_m1_incomplete_result_is_done_with_incomplete_flag(tmp_path):
    """미완주(`incomplete=True`)는 status `done` 유지 + 잡·`done` 감사에 `incomplete: true`."""
    audit = tmp_path / "a.jsonl"
    job = _run(_dispatcher(fake_diagnose(incomplete=True), audit), make_job("w0-m1-inc"))

    assert job.status == "done"          # D-123 상태 어휘 불변 — 새 상태를 만들지 않는다(G-3 a)
    assert job.summary()["incomplete"] is True
    assert _one(_events(audit), "done")["incomplete"] is True


def test_m1_complete_result_has_incomplete_false(tmp_path):
    """완주도 키는 상시(G-2 a) — 값 `false`."""
    audit = tmp_path / "a.jsonl"
    job = _run(_dispatcher(fake_diagnose(), audit), make_job("w0-m1-ok"))

    assert job.status == "done"
    assert job.summary()["incomplete"] is False
    assert _one(_events(audit), "done")["incomplete"] is False


# ── M-2 · step 상한 예외 경로에서 토큰·도구 기록이 0이 된다 ────────────────


class _EchoTool(Tool):
    """메모리 도구 — 원시 출력 `RAW-<n>`. 네트워크 0."""

    def _invoke(self, params, context):  # noqa: ANN001 — holmes Tool 시그니처
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data=f"RAW-{params.get('n')}", params=params
        )

    def get_parameterized_one_liner(self, params) -> str:  # noqa: ANN001
        return f"echo {params.get('n')}"


class _FakeLLM(LLM):
    """holmes `LLM` 대역 — 턴마다 usage 120 토큰(100+20).

    `loop=True`면 매 턴 tool_calls(도구가 빠진 마지막 턴에도) → holmes가 step 상한 예외를 던진다.
    인자 `n`을 턴마다 바꿔 holmes 중복 호출 거부(safeguards.py:24)를 피한다.
    """

    def __init__(self, loop: bool) -> None:
        self.model = MODEL
        self.loop = loop
        self.calls = 0

    def get_context_window_size(self) -> int:
        return 100_000

    def get_maximum_output_token(self) -> int:
        return 1_000

    def count_tokens(self, messages, tools=None) -> ContextWindowUsage:  # noqa: ANN001
        return ContextWindowUsage(total_tokens=10, tools_tokens=0, system_tokens=0, user_tokens=0,
                                  tools_to_call_tokens=0, assistant_tokens=0, other_tokens=0)

    def completion(self, messages, tools=None, tool_choice=None,  # noqa: ANN001
                   response_format=None, temperature=None, drop_params=None, stream=None, **_kw):
        self.calls += 1
        if self.loop or messages[-1].get("role") != "tool":
            call = {"id": f"c{self.calls}", "type": "function",
                    "function": {"name": "echo", "arguments": json.dumps({"n": self.calls})}}
            message: dict = {"role": "assistant", "content": None, "tool_calls": [call]}
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "결론 — echo 출력 인용"}
            finish = "stop"
        return ModelResponse(
            id=f"r{self.calls}",
            model="bench-fake",
            choices=[{"index": 0, "message": message, "finish_reason": finish}],
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        )


def _agent_with_real_holmes(*, loop: bool, max_steps: int = 3) -> DiagnosisAgent:
    s = AgentSettings(_env_file=None, model=MODEL, api_key=None, max_steps=max_steps)
    agent = DiagnosisAgent(settings=s, toolsets={})
    echo = _EchoTool(name="echo", description="echo",
                     parameters={"n": ToolParameter(type="integer")})
    toolset = Toolset(name="w0_echo", description="w0", enabled=True, tools=[echo])
    toolset.status = ToolsetStatusEnum.ENABLED
    # lazy llm(create_toolcalling_llm — prerequisite 검사·MCP 접속)을 우회한다.
    # 그 자리에 실 ToolCallingLLM을 꽂는다.
    agent._llm = ToolCallingLLM(tool_executor=ToolExecutor([toolset]), max_steps=max_steps,
                                llm=_FakeLLM(loop), tool_results_dir=None)
    return agent


def test_m2_step_limit_keeps_partial_tool_records_tokens_and_steps():
    """step 상한 예외에서도 그때까지의 도구 기록·토큰·step 수를 보존한다(H-5 입력).

    W1 「가능 범위」 = 전부 가능(W0 실측): holmes 0.36.0 `call_stream`이 턴마다 `TOOL_RESULT`
    (도구 1건 `to_client_dict`)와 `TOKEN_COUNT`(`metadata["costs"]` = 누적 RequestStats —
    tool_calling_llm.py:1028)를 내므로 예외 직전까지 회수된다. 덤프 서버 실측도 같다 —
    3턴 loop에서 도구 결과 3건 · 누적 360 토큰을 예외 전에 받았다.
    """
    agent = _agent_with_real_holmes(loop=True, max_steps=3)
    r = agent.ask("w0-m2 장애 원인을 조사하라")

    assert r.incomplete is True
    assert [o.tool_name for o in r.tool_outputs] == ["echo", "echo", "echo"]
    assert {o.output for o in r.tool_outputs} == {"RAW-1", "RAW-2", "RAW-3"}  # 판정 입력 보존
    assert r.total_tokens == 3 * 120
    assert r.steps == 3  # type: ignore[attr-defined]


def test_m4_steps_from_holmes_on_normal_completion():
    """완주 결과도 step 수(`LLMResult.num_llm_calls`)를 싣는다 — 도구 1턴 + 결론 1턴 = 2."""
    agent = _agent_with_real_holmes(loop=False, max_steps=5)
    r = agent.ask("w0-m4 장애 원인을 조사하라")

    assert r.incomplete is False
    assert r.total_tokens == 2 * 120
    assert r.steps == 2  # type: ignore[attr-defined]


# ── M-3 · 타임아웃 잡은 계측이 비어 있다 ─────────────────────────────


def test_m3_timeout_job_carries_elapsed_model_max_steps(tmp_path):
    """타임아웃 잡·`timeout` 감사에도 `elapsed_s`·`model`·`max_steps`가 실린다.

    토큰·도구 기록 보존은 단언하지 않는다(「가능 범위」로 좁힘): 타임아웃은 dispatcher가 조사
    스레드를 **버리는** join 타임박스라(investigation_dispatcher.py:549-550 — 스레드 미종료·결과
    폐기) diagnose_fn이 반환하지 않는다. 부분 토큰을 얻으려면 조사 진행 상황을 잡 쪽으로 흘리는
    별도 통로가 필요하고 그 모양은 W1 설계 몫이다(holmes 쪽은 `call_stream` 이벤트로 가능 — M-2).
    """
    audit = tmp_path / "a.jsonl"
    release = threading.Event()

    def hanging_fn(job):
        release.wait()
        return DiagnosisResult(answer="")

    try:
        job = _run(_dispatcher(hanging_fn, audit, timeout_seconds=0.2), make_job("w0-m3"))
    finally:
        release.set()

    assert job.status == "timeout"
    for view in (job.summary(), _one(_events(audit), "timeout")):
        assert view["elapsed_s"] >= 0.15
        assert view["model"] == MODEL
        assert view["max_steps"] == 7


# ── M-4 · done 감사에 지연·step·도구 수·모델이 없다 ──────────────────────


def test_m4_done_job_and_audit_carry_instrumentation_keys(tmp_path):
    """JobStore 경유(`sre_get_investigation` 반환)와 `done` 감사 양쪽에 가산 키 전부(상시)."""
    audit = tmp_path / "audit.jsonl"
    settings = make_settings()
    outputs = [
        _record("polestar_metric_trend", "success", RAW_SENTINEL,
                {"server_name": "w0-m4", "kind": "cpu"}),
        _record("polestar_incident_alarms", "error", "", {"server_name": "w0-m4"}, error="boom"),
    ]
    disp = InvestigationDispatcher(
        settings, diagnose_fn=fake_diagnose(steps=4, outputs=outputs, tokens=77),
        briefing_fn=build_briefing, timeout_seconds=5.0, audit_path=audit,
    )
    store = JobStore(settings, executor=disp, audit_path=audit)
    res = store.submit({
        "contract_version": "1",
        "event": {"serverName": "w0-m4", "hostname": "w0-m4.local", "severity": 2},
        "decision": {"fingerprint": "w0-m4", "tier": "PAGE"},
    })
    disp.wait_workers(10)
    got = store.get(res["investigation_id"])
    record = _one(_events(audit), "done")

    assert got["status"] == "done"
    for view in (got, record):
        missing = [k for k in _INSTR_KEYS if k not in view]
        assert not missing, f"가산 키 누락: {missing}"
        assert view["incomplete"] is False
        assert isinstance(view["elapsed_s"], (int, float)) and view["elapsed_s"] >= 0
        assert view["steps"] == 4
        assert view["tool_calls_n"] == 2
        assert view["model"] == MODEL
        assert view["max_steps"] == 7
    assert record["tokens"] == 77                     # 종전 키는 그대로(가산만)


def test_m4_prefetch_seconds_measured(tmp_path):
    """사전수집이 돌면 `prefetch_s`가 그 소요를 싣는다 — 벤치 phase 구분·지연 분해의 입력."""
    audit = tmp_path / "a.jsonl"

    def slow_prefetch(job):
        time.sleep(0.12)
        return {"leading_signal": None, "metric_findings": {}, "alarm_summary": {"count": 0},
                "timeline": [], "notes": []}

    job = make_job("w0-m4-pf")
    job.reference_time = "2026-10-13T10:00:00"
    _run(_dispatcher(fake_diagnose(), audit, prefetch_fn=slow_prefetch), job)

    for view in (job.summary(), _one(_events(audit), "done")):
        assert view["prefetch_s"] >= 0.1
        assert view["elapsed_s"] >= view["prefetch_s"]


# ── M-5 · 원시 도구 기록이 잡에 남지 않는다 ───────────────────────────


def test_m5_tool_call_digests_without_raw_output(tmp_path):
    """잡·`done` 감사의 `tool_calls`는 `{name, args_digest, status}`만 — 원시 출력·오류 본문 제외.

    `args_digest`는 같은 인자면 같고 다른 인자면 달라야 한다(정체·반복 판정이 가능하도록).
    알고리즘은 W1 몫.
    """
    audit = tmp_path / "a.jsonl"
    outputs = [
        _record("polestar_metric_trend", "success", RAW_SENTINEL,
                {"server_name": "w0-m5", "kind": "cpu"}),
        _record("polestar_metric_trend", "success", RAW_SENTINEL,
                {"kind": "cpu", "server_name": "w0-m5"}),
        _record("polestar_metric_trend", "error", "",
                {"server_name": "w0-m5", "kind": "memory"}, error=f"{RAW_SENTINEL}-err"),
    ]
    job = _run(_dispatcher(fake_diagnose(outputs=outputs), audit), make_job("w0-m5"))
    record = _one(_events(audit), "done")

    for calls in (job.summary()["tool_calls"], record["tool_calls"]):
        assert [set(c) for c in calls] == [{"name", "args_digest", "status"}] * 3
        assert [c["name"] for c in calls] == ["polestar_metric_trend"] * 3
        assert [c["status"] for c in calls] == ["success", "success", "error"]
        assert calls[0]["args_digest"] == calls[1]["args_digest"]   # 키 순서만 다른 같은 인자
        assert calls[0]["args_digest"] != calls[2]["args_digest"]
        assert RAW_SENTINEL not in json.dumps(calls, ensure_ascii=False)
    assert RAW_SENTINEL not in json.dumps(record, ensure_ascii=False)
