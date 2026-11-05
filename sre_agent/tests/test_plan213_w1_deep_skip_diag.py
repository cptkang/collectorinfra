"""plans/213 W1(D-366 ③) — 형식 불일치 종류 분류 · 한계 문구 3종 · 심층 생략 경로 진단.

실 LLM 0 — 가짜 diagnose_fn·가짜 HolmesGPT(`FakeDeep`)·가짜 폴스타 호출기만 쓴다.
"""

from __future__ import annotations

import json
import logging

import pytest
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import (
    InvestigationDispatcher,
    _deep_malformed_kind,
    _deep_skip_reason,
)
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.domain.answer_markup import (
    MALFORMED_AFTER_TOOLS_LIMITATION,
    MALFORMED_ANSWER_LIMITATION,
    MALFORMED_BARE_JSON_LIMITATION,
    TOOL_CALL_MARKUP_LITERALS,
    classify_tool_call_markup,
    has_tool_call_markup,
    malformed_limitation,
)
from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import FakeDeep, alarm_of, build, make_settings, run_job
from test_plan188_w4_markup import SCREEN_RAW, _alarm_job
from test_plan188_w4_markup import _settings as markup_settings

_CALL_TEXT = "결론\ncall:TodoWrite{todos:[]}"
_BARE_JSON = '[{"id": "1", "content": "알람 확인", "status": "in_progress"}]'
_BARE_LIMIT = ("조사 LLM이 도구 호출을 본문 JSON으로 냄 — 파서 불일치 또는 모델 응답 문제"
               "(docs/26 §5.6.3 판정으로 구분)")
_AFTER = ("도구 {n}건은 처리됐으나 마지막 답이 서술이 아니라 도구 호출 꼴({kind})로 끝남 — "
          "파서보다 모델 응답 문제 · 다시 분석 또는 모델 교체 검토")
_DEEP_GREP = "심층 조사 답변에 도구 호출 형식"


# ── 분류 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("text", "kind"), [
    (SCREEN_RAW, "markup"),
    (_CALL_TEXT, "call_syntax"),
    ("|call:foo{}", "call_syntax"),
    (_BARE_JSON, "bare_json"),
    ('{"name": "polestar_incident_alarms", "arguments": {}}', "bare_json"),
    ("web-01 CPU 88% ← polestar_metric_trend", None),
    ("", None),
    (None, None),
])
def test_classify_kinds(text, kind):
    assert classify_tool_call_markup(text) == kind


# 기존 has_tool_call_markup 테스트 입력(test_plan188_w4_markup) + 맨 JSON 꼴로 동치를 대조한다.
_EQUIV_INPUTS = [
    SCREEN_RAW, "원인은 아래와 같습니다.\n" + SCREEN_RAW,
    *(f"분석 결과 {lit} 끝" for lit in TOOL_CALL_MARKUP_LITERALS),
    "call:polestar_incident_alarms{lookback_minutes:120}", _CALL_TEXT,
    "다음 단계 call:apm.query-x{a:1}", "<x>call:foo{}", "|call:foo{}",
    "", None, "web-01 CPU 사용률이 13:48부터 88%까지 상승 ← polestar_metric_trend",
    "Disk IO saturation started at 13:50 and preceded the CPU spike.",
    "recall:foo{bar} 같은 낱말 일부", "call: 담당자에게 연락", "call:담당자{연락}",
    "on-call: 운영팀 {긴급}", "참고 http://x/call:foo{a} 경로",
    '설정 예 {"call:foo{": 1, "tool": "call"}',
    "```python\ndef call(x):\n    return {'call': x}\n```", "`call:foo{}`는 인용입니다",
    "tool_call 횟수 3회 · tool_calls 비어 있음",
    _BARE_JSON, "```json\n" + _BARE_JSON + "\n```", '{"todos": []}', "[]", "{}",
    '{"name": "x", "arguments": {}, "extra": 1}',
]


@pytest.mark.parametrize("text", _EQUIV_INPUTS)
def test_classify_none_iff_not_markup(text):
    assert (classify_tool_call_markup(text) is None) == (not has_tool_call_markup(text))


# ── 한계 문구 3종 ─────────────────────────────────────────────────


def test_limitation_texts():
    assert MALFORMED_BARE_JSON_LIMITATION == _BARE_LIMIT
    assert MALFORMED_AFTER_TOOLS_LIMITATION.format(n=3, kind="마크업 리터럴") == _AFTER.format(
        n=3, kind="마크업 리터럴")
    assert malformed_limitation("markup", 0) == MALFORMED_ANSWER_LIMITATION
    assert malformed_limitation("call_syntax", 0) == MALFORMED_ANSWER_LIMITATION
    assert malformed_limitation(None, 0) == MALFORMED_ANSWER_LIMITATION
    assert malformed_limitation("bare_json", 0) == _BARE_LIMIT
    assert malformed_limitation("markup", 1) == _AFTER.format(n=1, kind="마크업 리터럴")
    assert malformed_limitation("call_syntax", 2) == _AFTER.format(n=2, kind="call:이름{…} 구문")
    assert malformed_limitation("bare_json", 4) == _AFTER.format(n=4, kind="본문 JSON")


def test_after_tools_text_does_not_claim_parser_ok():
    assert "파서 정상" not in malformed_limitation("markup", 5)


def test_deep_skip_reason_uses_kind_and_tool_count():
    one = [ToolCallRecord("polestar_incident_alarms", "alarms", "success", "{}")]
    assert _deep_skip_reason(DiagnosisResult(answer=_BARE_JSON)) == _BARE_LIMIT
    assert _deep_skip_reason(DiagnosisResult(answer=_CALL_TEXT, tool_outputs=one)) == _AFTER.format(
        n=1, kind="call:이름{…} 구문")
    unavailable = DiagnosisResult(answer=SCREEN_RAW, tools_unavailable="조사 불가 — 연결 실패")
    assert _deep_skip_reason(unavailable) == "조사 불가 — 연결 실패"
    assert _deep_malformed_kind(unavailable) is None


# ── 주 경로 감사 ──────────────────────────────────────────────────


def _main_run(result: DiagnosisResult, tmp_path) -> tuple:
    disp = InvestigationDispatcher(markup_settings(), diagnose_fn=lambda job: result,
                                   briefing_fn=build_briefing, audit_path=tmp_path / "a.jsonl")
    job = _alarm_job("w1")
    disp(job)
    disp.wait_workers()
    done = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(x)["event"] == "done"]
    return job, done[0]


def test_main_path_bare_json_zero_tools(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        job, rec = _main_run(DiagnosisResult(answer=_BARE_JSON), tmp_path)
    assert job.briefing["limitations"] == [_BARE_LIMIT]
    assert rec["answer_malformed"] == "tool_call_markup"   # 기존 값 불변
    assert rec["answer_malformed_kind"] == "bare_json"
    msgs = [r.getMessage() for r in caplog.records if "도구 호출 마크업" in r.getMessage()]
    assert len(msgs) == 1 and "종류=bare_json" in msgs[0] and "도구=0건" in msgs[0]


def test_main_path_markup_zero_tools_keeps_legacy_text(tmp_path):
    job, rec = _main_run(DiagnosisResult(answer=SCREEN_RAW), tmp_path)
    assert job.briefing["limitations"] == [MALFORMED_ANSWER_LIMITATION]
    assert rec["answer_malformed_kind"] == "markup"


def test_main_path_normal_has_no_kind_key(tmp_path):
    _job, rec = _main_run(DiagnosisResult(answer="OOM ← polestar_incident_alarms"), tmp_path)
    assert "answer_malformed" not in rec and "answer_malformed_kind" not in rec


# ── 심층 생략(플레이북) ────────────────────────────────────────────


def _deep_run(answer: str, outputs: list[ToolCallRecord] | None = None):
    settings = make_settings(sre_deep_dive_mode="parallel")
    deep = FakeDeep(settings, answer=answer, outputs=outputs)
    audit: list = []
    job = run_job(build(settings, FakeCaller(_load("latency_sql")), deep=deep, audit=audit),
                  alarm_of("latency_sql"))
    done = next(r for r in audit if r["event"] == "done")
    return job, done


def test_deep_skip_warning_and_audit_keys(caplog):
    with caplog.at_level(logging.WARNING):
        job, done = _deep_run(SCREEN_RAW)
    dd = job.briefing["deep_dive"]
    assert dd["status"] == "skipped"
    assert dd["reason"] == _AFTER.format(n=1, kind="마크업 리터럴")
    # 기존 키 의미·값 불변(엔진 종결 상태) + 가산 키
    assert done["deep_dive_status"] == "done"
    assert done["deep_dive_display"] == "skipped"
    assert done["deep_dive_malformed"] == "markup"
    msgs = [r.getMessage() for r in caplog.records if _DEEP_GREP in r.getMessage()]
    assert len(msgs) == 1
    assert "investigation_id=inv-1" in msgs[0] and "종류=markup" in msgs[0]
    assert "도구=1건" in msgs[0] and SCREEN_RAW[200:] not in msgs[0]
    # 원문은 브리핑에 싣지 않는다(plans/188 C7)
    text = json.dumps(job.briefing, ensure_ascii=False)
    assert "<|tool_call>" not in text and '<|"|>' not in text


def test_deep_skip_bare_json_zero_tools():
    job, done = _deep_run(_BARE_JSON, outputs=[])
    assert job.briefing["deep_dive"]["reason"] == _BARE_LIMIT
    assert done["deep_dive_display"] == "skipped" and done["deep_dive_malformed"] == "bare_json"


def test_deep_tools_unavailable_has_no_warning_and_no_kind(caplog):
    settings = make_settings(sre_deep_dive_mode="parallel")

    class Unavailable(FakeDeep):
        def __call__(self, job):
            r = super().__call__(job)
            r.tools_unavailable = "조사 불가 — 관측 도구 연결 실패"
            return r

    audit: list = []
    with caplog.at_level(logging.WARNING):
        job = run_job(build(settings, FakeCaller(_load("latency_sql")),
                            deep=Unavailable(settings, answer=SCREEN_RAW), audit=audit),
                      alarm_of("latency_sql"))
    done = next(r for r in audit if r["event"] == "done")
    assert job.briefing["deep_dive"]["reason"] == "조사 불가 — 관측 도구 연결 실패"
    assert done["deep_dive_display"] == "skipped" and "deep_dive_malformed" not in done
    assert not [r for r in caplog.records if _DEEP_GREP in r.getMessage()]


def test_deep_normal_record_shape_unchanged():
    job, done = _deep_run("심층 서술 ← apm_resource_pool")
    assert job.briefing["deep_dive"]["status"] == "done"
    assert done["deep_dive_status"] == "done"
    assert "deep_dive_display" not in done and "deep_dive_malformed" not in done


def test_mode_off_skip_has_no_display_key():
    audit: list = []
    run_job(build(make_settings(), FakeCaller(_load("latency_sql")), audit=audit),
            alarm_of("latency_sql"))
    done = next(r for r in audit if r["event"] == "done")
    assert done["deep_dive_status"] == "skipped" and "deep_dive_display" not in done
