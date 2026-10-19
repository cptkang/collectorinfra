"""plans/188 W4(C7) 독립 검증 — 감지 경계 · 원문 누출 경로 · 로그 비밀 가림 · 타임아웃 경로.

실 LLM 0 — 가짜 diagnose_fn만 쓴다. `xfail(strict=True)`는 검증에서 찾은 결함의 재현이다
(고쳐지면 XPASS로 실패해 표지를 걷게 한다). 경계 표기 테스트는 현 동작을 기록한다.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import pytest
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.domain.answer_markup import has_tool_call_markup
from sre_agent.infrastructure import log_masking
from sre_agent.settings import AgentSettings

_SUMMARY = "[미결] 조사 실패 — 조사 LLM 도구 호출 형식 불일치"
_CAUSE = "판단 불가(조사 LLM 응답 형식 불일치)"
_DISPATCHER_LOGGER = "sre_agent.application.investigation_dispatcher"


def _settings(**kw) -> AgentSettings:
    kw.setdefault("_env_file", None)
    kw.setdefault("model", "openai/test-model")
    kw.setdefault("api_key", "x")
    kw.setdefault("gemini_api_key", "k")
    kw.setdefault("service_bearer_token", None)
    kw.setdefault("evidence_correlation_enabled", False)
    kw.setdefault("investigation_dedup_ttl_seconds", None)
    kw.setdefault("severity_judge_enabled", False)
    kw.setdefault("apm_guidance_enabled", False)
    kw.setdefault("cross_source_investigation_enabled", False)
    return AgentSettings(**kw)


def _job(iid: str = "v-1", payload: dict | None = None, ref: str | None = None) -> InvestigationJob:
    job = InvestigationJob(
        investigation_id=iid, kind="alarm", status="running", created_at=time.time(),
        updated_at=time.time(), fingerprint=iid,
        payload=payload or {"event": {"serverName": "web-01", "severity": 2},
                            "decision": {"tier": "PAGE"}},
    )
    if ref:
        job.reference_time = ref
    return job


def _result(answer: str) -> DiagnosisResult:
    return DiagnosisResult(
        answer=answer, tool_calls=["alarms"],
        tool_outputs=[ToolCallRecord("polestar_incident_alarms", "alarms", "success", "{}")],
    )


def _run(result_or_fn, tmp_path, **kw):
    fn = result_or_fn if callable(result_or_fn) else (lambda job: result_or_fn)
    timeout = kw.pop("timeout_seconds", None)
    disp = InvestigationDispatcher(_settings(**kw), diagnose_fn=fn, briefing_fn=build_briefing,
                                   audit_path=tmp_path / "a.jsonl", timeout_seconds=timeout)
    job = _job()
    disp(job)
    disp.wait_workers(10)
    recs = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text().splitlines()]
    return job, recs


# ── 감지 경계 ────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    # Hermes·Qwen 계열 — 리터럴 `<tool_call>`로 잡힌다
    '<tool_call>\n{"name": "polestar_x", "arguments": {}}\n</tool_call>',
    # FunctionGemma 계열 — `call:이름{`으로 잡힌다
    "<start_function_call>call:foo{a:1}<end_function_call>",
    # 리터럴 없는 Gemma 꼴
    "call:polestar_x{lookback_minutes:120}",
])
def test_other_families_detected(text):
    assert has_tool_call_markup(text)


@pytest.mark.parametrize("text", [
    '<|python_tag|>{"name": "polestar_x", "parameters": {}}',   # Llama 3.x
    '[TOOL_CALLS][{"name": "polestar_x", "arguments": {}}]',     # Mistral
    '{"name": "polestar_x", "arguments": {"lookback_minutes": 120}}',  # 맨 JSON
])
def test_out_of_scope_families_not_detected(text):
    """범위 밖(계획서 감지 패턴 6종에 없음) — 현 동작 기록. 미탐이다(Minor)."""
    assert not has_tool_call_markup(text)


@pytest.mark.parametrize("text", [
    "| 도구 | 예 |\n|---|---|\n| call:polestar_x{a:1} | 설명 |",   # Markdown 표 셀
    "```\ncall:foo{x}\n```",                                    # 펜스 코드 행 시작
    "예시:\n    call:foo{x}",                                    # 들여쓴 코드
    "Then the agent will call:foo{ ... } style",                # 영어 서술 중 공백 뒤
])
def test_code_like_narrative_is_detected_current_behavior(text):
    """`call:이름{`가 공백·행 시작·`|` 뒤면 서술·코드 인용이어도 잡힌다.

    현 동작 기록(Minor 오탐).
    """
    assert has_tool_call_markup(text)


@pytest.mark.parametrize("text", [
    "(call:foo{x})", "다음call:foo{x}", "`call:foo{x}`", '"call:foo{x}"', "a=call:foo{x}",
])
def test_call_pattern_requires_boundary(text):
    assert not has_tool_call_markup(text)


# ── 원문 누출 경로 ───────────────────────────────────────────────


def test_narrative_plus_markup_drops_narrative_too(tmp_path):
    """서술 첫 줄 + 마크업 — 서술도 요약에 쓰지 않는다(일부만 믿지 않는다)."""
    answer = ("CPU 포화가 원인 ← polestar_incident_alarms\n"
              '<|tool_call>call:x{a:<|"|>b<|"|>}<tool_call|>')
    job, recs = _run(_result(answer), tmp_path)
    assert job.status == "done"
    b = job.briefing
    assert b["summary"] == _SUMMARY and b["cause"] == _CAUSE and b["hypotheses"] == []
    dumped = json.dumps(job.summary(), ensure_ascii=False)
    for needle in ("CPU 포화가 원인", "<|tool_call>", "call:x{", '<|"|>'):
        assert needle not in dumped
    done = [r for r in recs if r["event"] == "done"][0]
    assert done["answer_malformed"] == "tool_call_markup"
    assert "<|" not in json.dumps(done, ensure_ascii=False)


def test_cross_source_episode_malformed_keeps_block_without_raw(tmp_path):
    from tests.test_cross_source_investigation import REF, _episode, _payload

    raw = '<|tool_call>call:apm_events{a:<|"|>secret-ish<|"|>}<tool_call|>'
    disp = InvestigationDispatcher(
        _settings(cross_source_investigation_enabled=True, investigation_llm_enabled=True),
        diagnose_fn=lambda job: _result(raw), briefing_fn=build_briefing,
        audit_path=tmp_path / "a.jsonl", clock=lambda: 0.0,
    )
    job = _job("v-xs", _payload(_episode()), REF)
    disp(job)
    disp.wait_workers(10)
    b = job.briefing
    assert b["summary"] == _SUMMARY and b["cause"] == _CAUSE
    assert b["cross_source"]["episode_id"] == "ep-p150fixture"
    dumped = json.dumps(job.summary(), ensure_ascii=False)
    assert "secret-ish" not in dumped and "<|tool_call>" not in dumped


def test_timeout_path_unaffected(tmp_path):
    """타임아웃 — 답이 늦게 마크업으로 와도 timeout 종결 그대로, answer_malformed 키 없음."""
    release = threading.Event()

    def slow(job):
        release.wait(5)
        return _result("<|tool_call>call:x{}<tool_call|>")

    job, recs = _run(slow, tmp_path, timeout_seconds=0.2)
    release.set()
    assert job.status == "timeout"
    assert job.briefing["stub"] is True
    assert all("answer_malformed" not in r for r in recs)


# ── 로그 비밀 가림(레코드 팩토리 실제 적용) ───────────────────────


def _warning_with_factory(answer: str, tmp_path, caplog) -> str:
    orig = logging.getLogRecordFactory()
    try:
        log_masking.install_log_record_factory()
        with caplog.at_level(logging.WARNING, logger=_DISPATCHER_LOGGER):
            _run(_result(answer), tmp_path)
    finally:
        logging.setLogRecordFactory(orig)
    msgs = [r.getMessage() for r in caplog.records if "도구 호출 마크업" in r.getMessage()]
    assert len(msgs) == 1
    return msgs[0]


def test_log_masks_plain_key_value_secret(tmp_path, caplog):
    raw = "<|tool_call>call:ssh{password=hunter2}<tool_call|>"
    msg = _warning_with_factory(raw, tmp_path, caplog)
    assert "hunter2" not in msg and "***" in msg


def test_log_masks_dsn_password_inside_gemma_quotes(tmp_path, caplog):
    msg = _warning_with_factory(
        '<|tool_call>call:x{url:<|"|>http://u:pw123@h/sse<|"|>}<tool_call|>', tmp_path, caplog)
    assert "pw123" not in msg


@pytest.mark.parametrize("raw", [
    '<|tool_call>call:x{api_key:<|"|>sk-SECRET123<|"|>}<tool_call|>',
    '<|tool_call>call:x{password:<|"|>hunter2SECRET123<|"|>}<tool_call|>',
    '<|tool_call>call:x{token:<|"|>tokSECRET123<|"|>}<tool_call|>',
])
def test_log_masks_gemma_quoted_secret(raw, tmp_path, caplog):
    msg = _warning_with_factory(raw, tmp_path, caplog)
    assert "SECRET123" not in msg
