"""plans/213 W1(D-366 ③) 독립 검증 — 구현자 테스트(test_plan213_w1_deep_skip_diag)가 덮지 않은 경계.

실 LLM 0 — 가짜 diagnose_fn·가짜 HolmesGPT(`FakeDeep`)·가짜 폴스타 호출기만 쓴다.
"""

from __future__ import annotations

import json
import logging

import pytest
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher, _tools_run
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.domain.answer_markup import (
    MALFORMED_AFTER_TOOLS_LIMITATION,
    MALFORMED_ANSWER_LIMITATION,
    MALFORMED_BARE_JSON_LIMITATION,
    MALFORMED_KIND_LABELS,
    classify_tool_call_markup,
    has_tool_call_markup,
    malformed_limitation,
)
from sre_agent.infrastructure.log_masking import mask_record
from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import FakeDeep, alarm_of, build, make_settings, run_job
from test_plan188_w4_markup import SCREEN_RAW, _alarm_job
from test_plan188_w4_markup import _settings as markup_settings

_MAIN_GREP = "조사 LLM 답변에 도구 호출 마크업"
_DEEP_GREP = "심층 조사 답변에 도구 호출 형식"
_ONE = [ToolCallRecord("polestar_incident_alarms", "alarms", "success", "{}")]
_CALL_TEXT = "결론\ncall:TodoWrite{todos:[]}"
_BARE_JSON = '{"name": "polestar_incident_alarms", "arguments": {"lookback_minutes": 60}}'


def _main(result: DiagnosisResult, tmp_path, **kw):
    disp = InvestigationDispatcher(markup_settings(**kw), diagnose_fn=lambda job: result,
                                   briefing_fn=build_briefing, audit_path=tmp_path / "a.jsonl")
    job = _alarm_job("v1")
    disp(job)
    disp.wait_workers()
    rows = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines()]
    return job, next(r for r in rows if r["event"] == "done")


# ── 문구 자체 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    MALFORMED_ANSWER_LIMITATION, MALFORMED_BARE_JSON_LIMITATION,
    *(MALFORMED_AFTER_TOOLS_LIMITATION.format(n=3, kind=v) for v in MALFORMED_KIND_LABELS.values()),
])
def test_limitation_texts_are_not_themselves_markup(text):
    # 한계 문구가 브리핑에 실린 뒤 재판정돼도 형식 불일치로 오인되지 않는다.
    assert not has_tool_call_markup(text)
    assert classify_tool_call_markup(text) is None
    assert "파서 정상" not in text


def test_after_tools_kind_labels_cover_all_kinds():
    for kind, label in MALFORMED_KIND_LABELS.items():
        text = malformed_limitation(kind, 2)
        assert "도구 2건" in text and f"({label})" in text
    assert malformed_limitation("bare_json", 1) != MALFORMED_BARE_JSON_LIMITATION


def test_tools_run_counts_failed_and_missing():
    assert _tools_run(DiagnosisResult(answer="x")) == 0
    assert _tools_run(object()) == 0
    two = [*_ONE, ToolCallRecord("polestar_metric_trend", "t", "error", "boom")]
    assert _tools_run(DiagnosisResult(answer="x", tool_outputs=two)) == 2


# ── 주 경로 ───────────────────────────────────────────────────────


def test_main_call_syntax_after_tools_kind_and_audit(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        job, done = _main(DiagnosisResult(answer=_CALL_TEXT, tool_outputs=list(_ONE)), tmp_path)
    assert job.briefing["limitations"][-1] == malformed_limitation("call_syntax", 1)
    assert done["answer_malformed"] == "tool_call_markup"      # 종전 값 불변
    assert done["answer_malformed_kind"] == "call_syntax"
    msgs = [r.getMessage() for r in caplog.records if _MAIN_GREP in r.getMessage()]
    assert len(msgs) == 1 and "종류=call_syntax" in msgs[0] and "도구=1건" in msgs[0]
    assert "call:TodoWrite" not in json.dumps(job.briefing, ensure_ascii=False)


def test_main_bare_json_after_tools(tmp_path):
    job, done = _main(DiagnosisResult(answer=_BARE_JSON, tool_outputs=list(_ONE)), tmp_path)
    assert MALFORMED_AFTER_TOOLS_LIMITATION.format(n=1, kind="본문 JSON") in job.briefing["limitations"]
    assert done["answer_malformed_kind"] == "bare_json"
    assert "lookback_minutes" not in json.dumps(job.briefing, ensure_ascii=False)


def test_main_tools_unavailable_with_markup_answer_has_no_kind_no_warning(tmp_path, caplog):
    result = DiagnosisResult(answer=SCREEN_RAW, tools_unavailable="조사 불가 — 관측 도구 연결 실패")
    with caplog.at_level(logging.WARNING):
        job, done = _main(result, tmp_path)
    assert "answer_malformed" not in done and "answer_malformed_kind" not in done
    assert done["tools_unavailable"]
    assert not [r for r in caplog.records if _MAIN_GREP in r.getMessage()]
    assert not any("도구 호출" in x for x in job.briefing["limitations"])


# ── 심층 경로 ─────────────────────────────────────────────────────


def _deep(answer: str, outputs=None, incomplete: bool = False):
    settings = make_settings(sre_deep_dive_mode="parallel")

    class Deep(FakeDeep):
        def __call__(self, job):
            r = super().__call__(job)
            r.incomplete = incomplete
            return r

    audit: list = []
    job = run_job(build(settings, FakeCaller(_load("latency_sql")),
                        deep=Deep(settings, answer=answer, outputs=outputs), audit=audit),
                  alarm_of("latency_sql"))
    return job, next(r for r in audit if r["event"] == "done")


def test_deep_skip_warning_excerpt_is_masked_and_clipped(caplog):
    secret = "S3cr3tValue-should-not-leak"
    answer = f'<|tool_call>call:polestar_x{{api_key:<|"|>{secret}<|"|>}}<tool_call|>' + "z" * 400
    with caplog.at_level(logging.WARNING):
        job, done = _deep(answer)
    recs = [r for r in caplog.records if _DEEP_GREP in r.getMessage()]
    assert len(recs) == 1
    mask_record(recs[0])   # 운영 레코드 팩토리와 같은 가림(D-339)
    msg = recs[0].getMessage()
    assert secret not in msg
    assert "z" * 300 not in msg   # 앞 200자만
    text = json.dumps(job.briefing, ensure_ascii=False)
    assert secret not in text and "<|tool_call>" not in text and "z" * 50 not in text
    assert done["deep_dive_status"] == "done" and done["deep_dive_display"] == "skipped"


def test_deep_skip_call_syntax_multi_tools():
    outs = [*_ONE, ToolCallRecord("apm_resource_pool", "pool", "success", "{}")]
    job, done = _deep(_CALL_TEXT, outputs=outs)
    assert job.briefing["deep_dive"]["reason"] == malformed_limitation("call_syntax", 2)
    assert done["deep_dive_malformed"] == "call_syntax"


def test_deep_incomplete_malformed_still_skipped_with_kind():
    job, done = _deep(SCREEN_RAW, outputs=[], incomplete=True)
    dd = job.briefing["deep_dive"]
    assert dd["status"] == "skipped" and dd["reason"] == MALFORMED_ANSWER_LIMITATION
    assert done["deep_dive_status"] == "done"
    assert done["deep_dive_malformed"] == "markup"


def test_deep_normal_has_no_warning(caplog):
    with caplog.at_level(logging.WARNING):
        job, done = _deep("심층 서술 ← apm_resource_pool")
    assert not [r for r in caplog.records if _DEEP_GREP in r.getMessage()]
    assert "deep_dive_display" not in done and "answer_malformed_kind" not in done
