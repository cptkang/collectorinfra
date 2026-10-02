"""실 조사 LLM 게이트 — `investigation_llm_enabled`(tri-state) × `gemini_api_key` 단일 판정 (D-230 · D-123 ⑦ 개정).

종전 게이트는 `gemini_api_key is None` 단일 조건이 4곳(dispatcher 2 · stub executor 1 · `sre_health`
`holmes_ready` 1)에 흩어져 있어, Gemini를 쓰지 않는 사내 vLLM 경로에도 `GEMINI_API_KEY=dummy`가 필요했다.
None(기본)은 종전 판정과 비트 동일하고, True는 키 없이 실 조사, False는 항상 명시 스텁이다. LLM 호출 0.
"""

import asyncio
import json
import time

import pytest

from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import InvestigationJob, JobStore, make_stub_executor
from sre_agent.diagnosis import DiagnosisResult
from sre_agent.interface.mcp_service import create_service
from sre_agent.settings import STUB_LLM_DISABLED, STUB_LLM_KEY_ABSENT, AgentSettings


def _settings(enabled, key) -> AgentSettings:
    return AgentSettings(_env_file=None, model="test/model", api_key=None, max_steps=3,
                         gemini_api_key=key, investigation_llm_enabled=enabled, service_bearer_token=None)


# (enabled, key) → 스텁 사유(None = 실 조사 가능)
MATRIX = [
    (None, None, STUB_LLM_KEY_ABSENT),   # 종전 동작
    (None, "k", None),                   # 종전 동작
    (True, None, None),                  # 키 없이 실 조사(vLLM 등)
    (True, "k", None),
    (False, None, STUB_LLM_DISABLED),    # 항상 스텁 — 키 유무보다 우선
    (False, "k", STUB_LLM_DISABLED),
]


@pytest.mark.parametrize(("enabled", "key", "reason"), MATRIX)
def test_gate_matrix(enabled, key, reason):
    assert _settings(enabled, key).investigation_llm_stub_reason() == reason


def test_stub_reasons_are_distinct_and_explicit():
    # 침묵 금지 — 사유를 구분해 사용자에게 그대로 보인다. 키 부재 문구는 종전 그대로(본체 계약 테스트 리터럴).
    assert STUB_LLM_KEY_ABSENT == "조사 미실행 — LLM 키 부재(스텁)"
    assert STUB_LLM_DISABLED != STUB_LLM_KEY_ABSENT and "INVESTIGATION_LLM_ENABLED" in STUB_LLM_DISABLED


def _job(server="web-01") -> InvestigationJob:
    now = time.time()
    return InvestigationJob(investigation_id=f"id-{server}-{now}", kind="alarm", status="running",
                            created_at=now, updated_at=now, fingerprint=f"fp-{server}",
                            payload={"event": {"serverName": server, "severity": 2}, "decision": {"tier": "PAGE"}})


def _fake_diagnose(job):
    return DiagnosisResult(answer="원인 ← journalctl")


@pytest.mark.parametrize(("enabled", "key", "reason"), MATRIX)
def test_dispatcher_follows_gate(enabled, key, reason):
    disp = InvestigationDispatcher(_settings(enabled, key), diagnose_fn=_fake_diagnose, briefing_fn=build_briefing)
    job = _job()
    disp(job)
    disp.wait_workers(5)
    if reason is None:
        assert job.status == "done"
    else:
        assert job.status == "stub" and job.verdict == reason and job.briefing["message"] == reason


def test_dispatcher_missing_diagnose_fn_reason_when_gate_open():
    disp = InvestigationDispatcher(_settings(True, None), diagnose_fn=None)
    job = _job()
    disp(job)
    assert job.status == "stub" and "조사함수 미주입" in job.verdict


@pytest.mark.parametrize(("enabled", "key", "reason"), MATRIX)
def test_stub_executor_follows_gate(enabled, key, reason):
    job = _job()
    make_stub_executor(_settings(enabled, key))(job)
    assert job.status == "stub"
    assert job.verdict == (reason or "조사 미실행 — dispatcher 미배선(2-D 소관, 스텁)")


@pytest.mark.parametrize(("enabled", "key", "reason"), MATRIX)
def test_health_holmes_ready_follows_gate(tmp_path, enabled, key, reason):
    s = _settings(enabled, key)
    mcp = create_service(s, job_store=JobStore(s, audit_path=tmp_path / "audit.jsonl"))
    result = asyncio.run(mcp.call_tool("sre_health", {}))
    content = result[0] if isinstance(result, tuple) else result
    assert json.loads(content[0].text)["holmes_ready"] is (reason is None)


def test_all_four_sites_use_the_single_gate(tmp_path, monkeypatch):
    """게이트 4곳이 같은 판정 함수를 쓴다 — 함수만 바꾸면 4곳이 함께 바뀐다(흩어진 조건 재발 방지)."""
    s = _settings(None, None)   # 종전 판정이면 키 부재 스텁
    monkeypatch.setattr(AgentSettings, "investigation_llm_stub_reason", lambda self: None)

    disp = InvestigationDispatcher(s, diagnose_fn=_fake_diagnose, briefing_fn=build_briefing)
    j1 = _job("a")
    disp(j1)
    disp.wait_workers(5)
    assert j1.status == "done"                                     # dispatcher 진입 게이트

    j2 = _job("b")
    InvestigationDispatcher(s, diagnose_fn=None)(j2)
    assert "조사함수 미주입" in j2.verdict                          # dispatcher 스텁 사유

    j3 = _job("c")
    make_stub_executor(s)(j3)
    assert "dispatcher 미배선" in j3.verdict                        # stub executor

    mcp = create_service(s, job_store=JobStore(s, audit_path=tmp_path / "audit.jsonl"))
    result = asyncio.run(mcp.call_tool("sre_health", {}))
    content = result[0] if isinstance(result, tuple) else result
    assert json.loads(content[0].text)["holmes_ready"] is True     # sre_health


def test_flag_loads_from_env_file(tmp_path):
    for text, expected in (("true", True), ("false", False)):
        env = tmp_path / ".env"
        env.write_text(f"INVESTIGATION_LLM_ENABLED={text}\n")
        assert AgentSettings(_env_file=str(env)).investigation_llm_enabled is expected


def test_flag_empty_value_fails_load(tmp_path):
    """빈 값(KEY=)은 None이 아니라 로드 실패다 — None으로 두려면 줄 자체를 두지 않는다(.env.example 주석 근거)."""
    env = tmp_path / ".env"
    env.write_text("INVESTIGATION_LLM_ENABLED=\n")
    with pytest.raises(ValueError):
        AgentSettings(_env_file=str(env))
