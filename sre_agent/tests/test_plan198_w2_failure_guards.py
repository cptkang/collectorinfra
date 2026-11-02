"""plans/198 W2 — I-10 실패 모드 가드(정체 · 미조회 소스 · 부분 브리핑 · 154 H-5).

LLM·네트워크 0: 정체 가드는 실 holmes `ToolCallingLLM`에 가짜 `LLM`을 물린 종단(154 W0 픽스처 패턴),
dispatcher 쪽은 가짜 diagnose_fn이다. 플래그 셋(`partial_briefing_on_incomplete` ·
`stall_stop_enabled` · `unchecked_source_limitation_enabled`)이 모두 off면 브리핑·감사·잡 결과가
종전과 같아야 한다(D-162).
"""

from __future__ import annotations

import json
import threading

import pytest
from holmes.core.llm import LLM, ContextWindowUsage
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import ToolParameter, Toolset, ToolsetStatusEnum
from holmes.core.tools_utils.tool_executor import ToolExecutor
from litellm import ModelResponse
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.diagnosis import DiagnosisAgent, DiagnosisResult
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.domain.failure_guards import (
    ExpectedSources,
    StallTracker,
    ledger_from_activity,
    normalized_call_key,
    unchecked_sources,
)
from sre_agent.domain.severity_signatures import LEVEL_NAMES, ImportanceVerdict, clamp_level
from sre_agent.infrastructure.activity_masking import mask_activity_text
from sre_agent.infrastructure.expected_sources_loader import load_expected_sources
from sre_agent.settings import AgentSettings
from test_plan154_w0_instrumentation_gaps import (
    MODEL,
    RAW_SENTINEL,
    _EchoTool,
    _events,
    _one,
    _record,
    _run,
    make_job,
    make_settings,
)

_FLAGS_ON = {
    "partial_briefing_on_incomplete": True,
    "stall_stop_enabled": True,
    "unchecked_source_limitation_enabled": True,
}
_NEW_AUDIT_KEYS = {"stalled", "partial_briefing", "unchecked_sources"}


def _dispatcher(diagnose_fn, audit, *, timeout_seconds: float = 5.0, **flags):
    return InvestigationDispatcher(make_settings(**flags), diagnose_fn=diagnose_fn,
                                   briefing_fn=build_briefing, audit_path=audit,
                                   timeout_seconds=timeout_seconds)


def _incomplete_fn(outputs, *, stalled: str | None = None, steps: int = 4):
    def _fn(job):
        return DiagnosisResult(
            answer="[미완주] step 상한",
            tool_calls=[o.description for o in outputs],
            tool_outputs=outputs,
            total_tokens=50,
            incomplete=True,
            steps=steps,
            stalled=stalled,
        )

    return _fn


def _polestar_out():
    return _record("polestar_metric_trend", "success", f"cpu 97.5 {RAW_SENTINEL}",
                   {"server_name": "w2-a", "kind": "cpu"})


# ── 정체 판정기(domain) ───────────────────────────────────────────


def test_stall_tracker_repeat_with_normalized_args():
    """앞뒤 공백·None 값만 다른 인자는 같은 호출 → 3회째 step 경계에서 정체(대소문자 구분)."""
    t = StallTracker(repeat_threshold=3, idle_steps=99)
    for params in ({"host": "web01"}, {"host": " web01 ", "x": None}, {"host": "web01"}):
        t.observe("polestar_metric_trend", params, "", "success")
        reason = t.end_step()
    assert reason is not None and "같은 도구·같은 인자 호출 3회" in reason
    assert normalized_call_key("a", {"b": "x"}) == normalized_call_key("a", {"b": " x", "c": None})
    assert normalized_call_key("a", {"b": "X"}) != normalized_call_key("a", {"b": "x"})


def test_stall_tracker_idle_steps_without_new_evidence():
    """새 근거(처음 보는 성공 조회) 없는 step이 m회 이어지면 정체 — 오류·반복은 새 근거 아님."""
    t = StallTracker(repeat_threshold=99, idle_steps=2)
    t.observe("prom_query", {"q": "a"}, "", "success")
    assert t.end_step() is None                       # 새 근거 1
    t.observe("prom_query", {"q": "a"}, "", "success")
    assert t.end_step() is None                       # 반복 — idle 1
    t.observe("prom_query", {"q": "b"}, "", "error")
    reason = t.end_step()                             # 오류 — idle 2
    assert reason is not None and "새 근거 없는 도구 단계 2회" in reason


# ── 정체 가드 종단(실 holmes · 가짜 LLM) ───────────────────────────


class _RepeatLLM(LLM):
    """매 턴 echo를 부른다 — 인자 `n`은 `cycle`을 돈다(cycle=[1]이면 같은 인자 반복)."""

    def __init__(self, cycle: list[int]) -> None:
        self.model = MODEL
        self.cycle = cycle
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
        n = self.cycle[self.calls % len(self.cycle)]
        self.calls += 1
        call = {"id": f"c{self.calls}", "type": "function",
                "function": {"name": "echo", "arguments": json.dumps({"n": n})}}
        return ModelResponse(
            id=f"r{self.calls}", model="bench-fake",
            choices=[{"index": 0, "message": {"role": "assistant", "content": None,
                                              "tool_calls": [call]},
                      "finish_reason": "tool_calls"}],
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        )


def _agent(cycle: list[int], max_steps: int = 10, **flags) -> tuple[DiagnosisAgent, _RepeatLLM]:
    s = AgentSettings(_env_file=None, model=MODEL, api_key=None, max_steps=max_steps, **flags)
    agent = DiagnosisAgent(settings=s, toolsets={})
    echo = _EchoTool(name="echo", description="echo",
                     parameters={"n": ToolParameter(type="integer")})
    toolset = Toolset(name="w2_echo", description="w2", enabled=True, tools=[echo])
    toolset.status = ToolsetStatusEnum.ENABLED
    fake = _RepeatLLM(cycle)
    agent._llm = ToolCallingLLM(tool_executor=ToolExecutor([toolset]), max_steps=max_steps,
                                llm=fake, tool_results_dir=None)
    return agent, fake


def test_repeated_calls_stop_investigation_at_threshold():
    """같은 인자 반복 → 3번째 도구 step 경계에서 협조적 중단 · 정체 사유 · 회수 기록 보존."""
    agent, fake = _agent([1], stall_stop_enabled=True)
    r = agent.ask("w2 정체")

    assert r.incomplete is True and r.stalled is not None
    assert "같은 도구·같은 인자 호출 3회" in r.stalled
    assert fake.calls == 3 and r.steps == 3            # max_steps(10)까지 가지 않는다
    assert [o.tool_name for o in r.tool_outputs] == ["echo"] * 3
    assert r.total_tokens == 3 * 120


def test_idle_steps_stop_investigation():
    """두 인자만 오가며 새 근거가 없는 step이 2회 이어지면 중단(반복 임계는 높게)."""
    agent, fake = _agent([1, 2], stall_stop_enabled=True, stall_repeat_threshold=10,
                         stall_idle_steps=2)
    r = agent.ask("w2 공회전")

    assert r.stalled is not None and "새 근거 없는 도구 단계 2회" in r.stalled
    assert fake.calls == 4 and r.steps == 4


def test_stall_guard_off_runs_to_step_limit():
    """가드 off → 종전대로 step 상한 미완주 · `stalled` 없음."""
    agent, fake = _agent([1], max_steps=5)
    r = agent.ask("w2 off")

    assert r.incomplete is True and r.stalled is None
    assert fake.calls == 5 and r.steps == 5


# ── 미조회 소스 ───────────────────────────────────────────────────


def test_expected_sources_yaml_and_lookup():
    """기본 YAML: host = polestar·prometheus · was = apm·polestar · 세부 kind는 앞 마디로."""
    spec = load_expected_sources()
    assert spec.expected_for("host") == ("polestar", "prometheus")
    assert spec.expected_for("host.cpu") == ("polestar", "prometheus")
    assert spec.expected_for("was") == ("apm", "polestar")
    assert unchecked_sources("host", ["polestar_metric_trend"], spec) == ["prometheus"]
    assert unchecked_sources("was", ["prom_query"], spec) == ["apm", "polestar"]
    assert unchecked_sources(None, [], spec) == []


def test_unchecked_source_limitation_in_briefing_and_audit(tmp_path):
    """호스트 알람에서 prom_* 호출 0 → 한계 「확인하지 않은 소스: prometheus」 · 감사 가산 키."""
    audit = tmp_path / "a.jsonl"

    def done(job):
        return DiagnosisResult(answer="원인 서술 ← polestar_metric_trend",
                               tool_calls=["d"], tool_outputs=[_polestar_out()])

    job = _run(_dispatcher(done, audit, unchecked_source_limitation_enabled=True),
               make_job("w2-src"))
    assert "확인하지 않은 소스: prometheus" in job.briefing["limitations"]
    assert _one(_events(audit), "done")["unchecked_sources"] == ["prometheus"]


def test_unchecked_source_was_kind_for_apm_trigger(tmp_path):
    """APM 사건(resourceType apm.Instance)은 was kind — apm 미조회를 적는다."""
    job = make_job("w2-was")
    job.payload["event"]["resourceType"] = "apm.Instance"  # type: ignore[index]

    def done(j):
        return DiagnosisResult(answer="서술 ← polestar_metric_trend", tool_calls=["d"],
                               tool_outputs=[_polestar_out()])

    job = _run(_dispatcher(done, tmp_path / "a.jsonl", unchecked_source_limitation_enabled=True),
               job)
    assert "확인하지 않은 소스: apm" in job.briefing["limitations"]


# ── 부분 브리핑 ───────────────────────────────────────────────────


def test_incomplete_builds_partial_briefing_from_ledger(tmp_path):
    """미완주 + 플래그 on → 원장 행 기반 부분 브리핑 · 원시 출력은 싣지 않는다."""
    audit = tmp_path / "a.jsonl"
    outs = [_polestar_out(),
            _record("prom_query", "error", "", {"q": "x"}, error="boom")]
    job = _run(_dispatcher(_incomplete_fn(outs), audit, **_FLAGS_ON), make_job("w2-inc"))
    b = job.briefing

    assert job.status == "done"
    assert b["summary"].startswith("[미결] 조사 미완주(step 상한 도달)")
    assert b["partial"] == {"reason": "incomplete", "evidence_rows": 1, "steps": 4}
    assert [r["tool"] for r in b["evidence_ledger"]] == ["polestar_metric_trend"]
    assert any(line.startswith("[ev1] polestar_metric_trend") for line in b["timeline"])
    assert any("실패" in line or "오류" in line for line in b["limitations"])  # 실패 호출 한계
    assert RAW_SENTINEL not in json.dumps(b, ensure_ascii=False)
    record = _one(_events(audit), "done")
    assert record["partial_briefing"] == "incomplete"
    assert "stalled" not in record


def test_stalled_result_partial_briefing_and_audit(tmp_path):
    """정체 결과 → 사유를 한계에 · 부분 브리핑 사유 stalled · 감사 `stalled` 키."""
    audit = tmp_path / "a.jsonl"
    reason = "조사 정체 — 같은 도구·같은 인자 호출 3회(echo {}) → 조사 중단"
    job = _run(_dispatcher(_incomplete_fn([_polestar_out()], stalled=reason), audit, **_FLAGS_ON),
               make_job("w2-stall"))

    assert job.briefing["partial"]["reason"] == "stalled"
    assert reason in job.briefing["limitations"]
    record = _one(_events(audit), "done")
    assert record["stalled"] == reason and record["partial_briefing"] == "stalled"


def test_timeout_partial_briefing_from_activity(tmp_path):
    """타임아웃 + 플래그 on → stub 대신 실행 기록의 HolmesGPT 도구 결과로 부분 브리핑."""
    release = threading.Event()

    def hanging(job, activity=None):
        activity.append("tool_end", engine="holmes", tool="polestar_metric_trend",
                        label="polestar_metric_trend server=w2-t", status="success",
                        excerpt="cpu 99")
        activity.append("tool_end", engine="holmes", tool="prom_query", label="prom_query",
                        status="error", excerpt="boom")
        release.wait()
        return DiagnosisResult(answer="")

    job = make_job("w2-tmo")
    job.activity = ActivityLog(masker=mask_activity_text)
    audit = tmp_path / "a.jsonl"
    try:
        job = _run(_dispatcher(hanging, audit, timeout_seconds=0.3, **_FLAGS_ON), job)
    finally:
        release.set()

    assert job.status == "timeout" and job.reason == "investigation_timeout"
    b = job.briefing
    assert "stub" not in b
    assert b["partial"]["reason"] == "timeout" and b["partial"]["evidence_rows"] == 1
    assert b["evidence_ledger"][0]["tool"] == "polestar_metric_trend"
    # prom_query는 오류뿐이라 원장 행이 아니지만 호출은 했으므로 미조회 소스가 아니다.
    assert not any(line.startswith("확인하지 않은 소스") for line in b["limitations"])
    assert _one(_events(audit), "timeout")["partial_briefing"] == "timeout"


def test_timeout_partial_briefing_failure_keeps_timeout_and_stub(tmp_path, monkeypatch):
    """부분 브리핑 조립이 실패해도 잡은 timeout으로 확정되고 stub이 남는다(best-effort)."""
    release = threading.Event()

    def hanging(job, activity=None):
        release.wait()
        return DiagnosisResult(answer="")

    def boom(self, job, log):
        raise RuntimeError("partial boom")

    monkeypatch.setattr(InvestigationDispatcher, "_timeout_partial_briefing", boom)
    job = make_job("w2-tmo-fail")
    job.activity = ActivityLog(masker=mask_activity_text)
    audit = tmp_path / "a.jsonl"
    try:
        job = _run(_dispatcher(hanging, audit, timeout_seconds=0.2, **_FLAGS_ON), job)
    finally:
        release.set()
    assert job.status == "timeout" and job.reason == "investigation_timeout"
    assert job.briefing == {"stub": True, "message": job.verdict, "elements": None}
    assert "partial_briefing" not in _one(_events(audit), "timeout")


def test_ledger_from_activity_skips_non_holmes_and_errors():
    events = [
        {"engine": "playbook", "kind": "tool_end", "tool": "polestar_x", "status": "success"},
        {"engine": "holmes", "kind": "tool_start", "tool": "polestar_x"},
        {"engine": "holmes", "kind": "tool_end", "tool": "prom_q", "status": "error"},
        {"engine": "holmes", "kind": "tool_end", "tool": "prom_q", "status": "cached",
         "label": "prom_q a", "excerpt": "1", "at": 3.0},
    ]
    ledger = ledger_from_activity(events)
    assert [(r.ev_id, r.tool, r.at) for r in ledger.rows] == [("ev1", "prom_q", 3.0)]


# ── 플래그 off 비트 동일 ─────────────────────────────────────────


def test_flags_off_incomplete_briefing_and_audit_unchanged(tmp_path):
    """플래그 off → 미완주 브리핑은 답변만으로 조립한 종전 결과와 같고 감사에 새 키가 없다."""
    audit = tmp_path / "a.jsonl"
    job = _run(_dispatcher(_incomplete_fn([_polestar_out()]), audit), make_job("w2-off"))
    expected = build_briefing(
        answer="[미완주] step 상한",
        verdict=ImportanceVerdict(level=LEVEL_NAMES[clamp_level(2)], confidence="none",
                                  escalate=False),
        tool_names=[], gate_tier="PAGE", remediation=None,
    )
    assert job.briefing == expected
    assert not (_NEW_AUDIT_KEYS & set(_one(_events(audit), "done")))


def test_flags_off_timeout_stays_stub(tmp_path):
    release = threading.Event()

    def hanging(job):
        release.wait()
        return DiagnosisResult(answer="")

    audit = tmp_path / "a.jsonl"
    try:
        job = _run(_dispatcher(hanging, audit, timeout_seconds=0.2), make_job("w2-off-tmo"))
    finally:
        release.set()
    assert job.briefing == {"stub": True, "message": job.verdict, "elements": None}
    assert not (_NEW_AUDIT_KEYS & set(_one(_events(audit), "timeout")))


@pytest.mark.parametrize("field", sorted(_FLAGS_ON))
def test_flag_defaults_are_off(field):
    assert getattr(AgentSettings(_env_file=None), field) is False


def test_expected_sources_spec_is_plain_mapping():
    spec = ExpectedSources(sources={"a": ("a_",)}, kinds={"k": ("a",)})
    assert spec.source_of("a_tool") == "a" and spec.source_of("b_tool") is None
