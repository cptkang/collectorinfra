"""plans/188 W4(C7) — 조사 LLM 답변에 도구 호출 마크업이 섞이면 원문 없이 미결 종결.

실 LLM 0 — 가짜 diagnose_fn·가짜 에이전트만 쓴다. 화면 원문은 폐쇄망(2026-10-19) 브리핑에 실린
그대로다(vLLM `--tool-call-parser` 불일치).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sre_agent.application.briefing_builder import (
    SKIPPED_CAUSE,
    UNRESOLVED_PREFIX,
    build_briefing,
)
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import CONTRACT_VERSION, InvestigationJob
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord, tools_unavailable_result
from sre_agent.domain.answer_markup import (
    ANSWER_MALFORMED_TOOL_CALL,
    MALFORMED_ANSWER_CAUSE,
    MALFORMED_ANSWER_LIMITATION,
    MALFORMED_ANSWER_SUMMARY,
    TOOL_CALL_MARKUP_LITERALS,
    has_tool_call_markup,
)
from sre_agent.domain.severity_signatures import judge
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings

SCREEN_RAW = (
    '<|tool_call>call:TodoWrite{todos:[{content:<|"|>Identify server name and alarm details<|"|>,'
    'id:<|"|>1<|"|>,status:<|"|>in_progress<|"|>},{content:<|"|>Query alarms<|"|>,id:<|"|>2<|"|>,'
    'status:<|"|>pending<|"|>}]}<tool_call|><|tool_call>call:polestar_incident_alarms'
    '{lookback_minutes:120,reference_time:<|"|>2026-10-19T10:38:41<|"|>}<tool_call|>'
)
_SUMMARY = "[미결] 조사 실패 — 조사 LLM 도구 호출 형식 불일치"
_CAUSE = "판단 불가(조사 LLM 응답 형식 불일치)"
_LIMIT = ("조사 LLM 응답에 도구 호출 원문이 섞임 — "
          "vLLM --enable-auto-tool-choice·--tool-call-parser를 모델에 맞게 확인(docs/26 §5.6.3)")
_OOM = "kernel: Out of memory: Killed process 4242 (java)"


# ── 감지 단위 ─────────────────────────────────────────────────────


def test_fixed_texts_are_exact():
    assert UNRESOLVED_PREFIX + MALFORMED_ANSWER_SUMMARY == _SUMMARY
    assert MALFORMED_ANSWER_CAUSE == _CAUSE
    assert MALFORMED_ANSWER_LIMITATION == _LIMIT
    assert ANSWER_MALFORMED_TOOL_CALL == "tool_call_markup"


def test_screen_raw_detected():
    assert has_tool_call_markup(SCREEN_RAW)
    assert has_tool_call_markup("원인은 아래와 같습니다.\n" + SCREEN_RAW)


@pytest.mark.parametrize("literal", TOOL_CALL_MARKUP_LITERALS)
def test_each_literal_detected(literal):
    assert has_tool_call_markup(f"분석 결과 {literal} 끝")


@pytest.mark.parametrize("text", [
    "call:polestar_incident_alarms{lookback_minutes:120}",
    "결론\ncall:TodoWrite{todos:[]}",
    "다음 단계 call:apm.query-x{a:1}",
    "<x>call:foo{}",
    "|call:foo{}",
])
def test_call_pattern_detected(text):
    assert has_tool_call_markup(text)


@pytest.mark.parametrize("text", [
    "",
    None,
    "web-01 CPU 사용률이 13:48부터 88%까지 상승 ← polestar_metric_trend",
    "Disk IO saturation started at 13:50 and preceded the CPU spike.",
    "recall:foo{bar} 같은 낱말 일부",
    "call: 담당자에게 연락",
    "call:담당자{연락}",   # 식별자는 영문·밑줄로 시작
    "on-call: 운영팀 {긴급}",
    "참고 http://x/call:foo{a} 경로",
    '설정 예 {"call:foo{": 1, "tool": "call"}',
    "```python\ndef call(x):\n    return {'call': x}\n```",
    "`call:foo{}`는 인용입니다",
    "tool_call 횟수 3회 · tool_calls 비어 있음",
])
def test_normal_text_not_detected(text):
    assert not has_tool_call_markup(text)


# ── 브리핑 조립 ───────────────────────────────────────────────────


def _settings(**kw) -> AgentSettings:
    kw.setdefault("_env_file", None)
    kw.setdefault("model", "openai/test-model")
    kw.setdefault("api_key", "x")
    kw.setdefault("gemini_api_key", "k")
    kw.setdefault("service_bearer_token", None)
    kw.setdefault("evidence_correlation_enabled", False)
    kw.setdefault("investigation_dedup_ttl_seconds", None)
    kw.setdefault("severity_judge_enabled", False)
    return AgentSettings(**kw)


def _verdict():
    return judge(2, [])


def _assert_no_raw(obj: dict, needles: tuple[str, ...] = ("tool_call",)) -> None:
    """직렬화 문자열에 원문 흔적이 없다 — 브리핑은 `tool_call` 자체가 0건이어야 한다."""
    text = json.dumps(obj, ensure_ascii=False)
    for needle in (*needles, '<|"|>', "TodoWrite", "polestar_incident_alarms{"):
        assert needle not in text


def test_skipped_cause_param_default_keeps_a_plan():
    a = build_briefing(answer="조사 불가", verdict=_verdict(), investigation_skipped=True)
    b = build_briefing(answer="조사 불가", verdict=_verdict(), investigation_skipped=True,
                       skipped_cause=SKIPPED_CAUSE)
    assert a == b and a["cause"] == SKIPPED_CAUSE


def test_malformed_briefing_texts():
    b = build_briefing(answer=MALFORMED_ANSWER_SUMMARY, verdict=_verdict(), tool_names=["t"],
                       limitations=[MALFORMED_ANSWER_LIMITATION], investigation_skipped=True,
                       skipped_cause=MALFORMED_ANSWER_CAUSE)
    assert b["summary"] == _SUMMARY
    assert b["cause"] == _CAUSE
    assert b["hypotheses"] == [] and b["citations_verified"] is False
    assert b["limitations"] == [_LIMIT]


# ── dispatcher 종단 ──────────────────────────────────────────────


def _alarm_job(server: str) -> InvestigationJob:
    return InvestigationJob(
        investigation_id=f"id-{server}",
        kind="alarm",
        status="running",
        created_at=0.0,
        updated_at=0.0,
        fingerprint=server,
        payload={"event": {"serverName": server, "severity": 2}, "decision": {"tier": "PAGE"}},
    )


def _result(answer: str, *, failed: bool = False) -> DiagnosisResult:
    outputs = [ToolCallRecord("polestar_incident_alarms", "alarms", "success", _OOM)]
    if failed:
        outputs.append(ToolCallRecord("polestar_metric_trend", "trend", "error", "boom"))
    return DiagnosisResult(answer=answer, tool_calls=["alarms"], tool_outputs=outputs,
                           total_tokens=10)


def _run(result: DiagnosisResult, tmp_path, **kw) -> tuple[InvestigationJob, dict, list]:
    disp = InvestigationDispatcher(_settings(**kw), diagnose_fn=lambda job: result,
                                   briefing_fn=build_briefing, audit_path=tmp_path / "a.jsonl")
    seen: list = []
    orig = disp._run_severity_judge

    def _spy(gate, basis):
        seen.append(basis)
        return orig(gate, basis)

    disp._run_severity_judge = _spy   # type: ignore[method-assign]
    job = _alarm_job("w4")
    disp(job)
    disp.wait_workers()
    done = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text().splitlines()
            if json.loads(x)["event"] == "done"]
    return job, done[0], seen


def test_dispatcher_malformed_answer_closes_unresolved_without_raw(tmp_path, caplog):
    result = _result(SCREEN_RAW)
    with caplog.at_level("WARNING"):
        job, rec, seen = _run(result, tmp_path, severity_judge_enabled=True)
    b = job.briefing
    assert job.status == "done"
    assert b["summary"] == _SUMMARY
    assert b["cause"] == _CAUSE
    assert b["hypotheses"] == [] and b["citations_verified"] is False
    assert b["limitations"] == [_LIMIT]
    _assert_no_raw(b)
    assert rec["answer_malformed"] == "tool_call_markup"
    assert "tools_unavailable" not in rec
    # 판정 입력 불변 — 원 결과 그대로, 같은 도구 출력이면 정상 답과 같은 판정
    assert seen == [result]
    msgs = [r.getMessage() for r in caplog.records if "도구 호출 마크업" in r.getMessage()]
    assert len(msgs) == 1 and SCREEN_RAW[200:] not in msgs[0]


def test_severity_unchanged_vs_clean_answer(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "good").mkdir()
    bad, _, _ = _run(_result(SCREEN_RAW), tmp_path / "bad", severity_judge_enabled=True)
    good, _, _ = _run(_result("OOM ← polestar_incident_alarms"), tmp_path / "good",
                      severity_judge_enabled=True)
    assert bad.verdict == good.verdict


def test_failed_call_line_kept_and_apm_limits_dropped(tmp_path):
    job, _, _ = _run(_result(SCREEN_RAW, failed=True), tmp_path,
                     apm_guidance_enabled=True, apm_mcp_url="")
    b = job.briefing
    assert b["limitations"] == ["도구 호출 실패 1건: polestar_metric_trend", _LIMIT]
    _assert_no_raw(b)


def test_correlation_timeline_kept(tmp_path):
    from tests.test_briefing_builder import CORR

    result = _result(SCREEN_RAW)
    disp = InvestigationDispatcher(_settings(), diagnose_fn=lambda job: result,
                                   briefing_fn=build_briefing,
                                   prefetch_fn=lambda job: CORR)
    job = _alarm_job("corr")
    job.reference_time = "2026-09-01T14:00:00"   # 사전수집은 기준시각이 있을 때만 돈다
    disp(job)
    disp.wait_workers()
    assert job.briefing["timeline"] and job.briefing["timeline"][0].startswith("T-15m")
    assert job.briefing["root_cause_hypotheses"]
    assert job.briefing["cause"] == _CAUSE
    _assert_no_raw(job.briefing)


def test_incomplete_with_markup_also_closes(tmp_path):
    result = _result(SCREEN_RAW)
    result.incomplete = True
    job, rec, _ = _run(result, tmp_path)
    assert job.briefing["summary"] == _SUMMARY and rec["answer_malformed"] == "tool_call_markup"


def test_normal_answer_unchanged(tmp_path):
    job, rec, _ = _run(_result("OOM ← polestar_incident_alarms"), tmp_path)
    assert "answer_malformed" not in rec
    expected = build_briefing(answer="OOM ← polestar_incident_alarms", verdict=_verdict(),
                              tool_names=["polestar_incident_alarms"], gate_tier="PAGE")
    assert job.briefing == expected


def test_tools_unavailable_takes_precedence(tmp_path):
    result = tools_unavailable_result({"polestar": "x"})
    result.answer = result.answer + " <tool_call>"
    job, rec, _ = _run(result, tmp_path)
    assert job.briefing["cause"] == SKIPPED_CAUSE
    assert "answer_malformed" not in rec and rec["tools_unavailable"]


# ── 헬스 선택 키 ──────────────────────────────────────────────────


def _call(mcp, name, args=None) -> dict:
    out = asyncio.run(mcp.call_tool(name, args or {}))
    content = out[0] if isinstance(out, tuple) else out
    return json.loads(content[0].text)


class _Agent:
    answers: list[str] = []

    def __init__(self, settings, toolsets=None, mcp_servers=None):
        pass

    def mcp_unavailable(self) -> dict[str, str]:
        return {}

    def tool_names(self) -> frozenset[str]:
        return frozenset()

    def ask(self, question, system_prompt_additions=None, **_kw):
        # `**_kw` — plans/194 W3 협조적 중단 `cancel_event`(dispatcher가 잡마다 싣는다)
        return DiagnosisResult(answer=type(self).answers.pop(0))


def test_health_last_answer_malformed(monkeypatch, tmp_path):
    class Agent(_Agent):
        answers = [SCREEN_RAW, "정상 답"]

    monkeypatch.setattr(mcp_service, "DiagnosisAgent", Agent)
    s = _settings(polestar_mcp_url="http://p/sse",
                  investigation_audit_path=str(tmp_path / "audit.jsonl"))
    mcp = mcp_service.create_service(s)
    assert "last_answer_malformed" not in _call(mcp, "sre_health")

    for fp in ("fp-1", "fp-2"):
        payload = {
            "contract_version": CONTRACT_VERSION,
            "event": {"serverName": f"web-{fp}", "hostname": f"web-{fp}.local", "severity": 2},
            "decision": {"fingerprint": fp},
        }
        iid = _call(mcp, "sre_investigate_alarm", {"payload": payload})["investigation_id"]
        mcp_service.get_job_store(mcp)._executor.wait_workers()
        health = _call(mcp, "sre_health")
        got = _call(mcp, "sre_get_investigation", {"investigation_id": iid})
        if fp == "fp-1":
            assert health["last_answer_malformed"] is True
            assert got["briefing"]["summary"] == _SUMMARY
            _assert_no_raw(got["briefing"])
            _assert_no_raw(got, needles=("<|tool_call>", "<tool_call|>"))   # 잡 dict 전체
        else:
            assert health["last_answer_malformed"] is False
        assert health["contract_version"] == CONTRACT_VERSION


def test_diagnose_fn_records_without_polestar_url(monkeypatch):
    class Agent(_Agent):
        answers = [SCREEN_RAW]

    monkeypatch.setattr(mcp_service, "DiagnosisAgent", Agent)
    check = mcp_service.ToolsetCheck()
    job = SimpleNamespace(kind="alarm", question=None, investigation_id="i", payload={
        "event": {"serverName": "w", "resourceType": "cpu"}},
        reference_time=None, lookback_minutes=None)
    mcp_service._default_diagnose_fn(_settings(polestar_mcp_url=""), check)(job)
    assert check.last_answer_malformed is True and check.polestar_reachable is None
