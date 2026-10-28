"""심층 답변 원문 `deep_dive.narrative` — [서술 보기]가 첫 줄(summary)만 보이던 결함 교정.

summary는 답변 첫 줄이라 마크다운 제목으로 시작하면 제목 한 줄만 남는다. 원문은 상한까지 따로 싣는다.
"""

from __future__ import annotations

from sre_agent.application import investigation_dispatcher as disp_mod
from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import FakeDeep, FakeLLM, alarm_of, build, make_settings, run_job

REPORT = "### 장애 분석 결과 보고서: web01\n\n- 원인: 커넥션 풀 고갈 ← apm_resource_pool\n- 조치: 풀 확대"


def test_done_deep_dive_carries_full_answer_in_order() -> None:
    settings = make_settings(sre_deep_dive_mode="parallel")
    d = build(settings, FakeCaller(_load("latency_sql")), deep=FakeDeep(settings, answer=REPORT),
              llm=FakeLLM())
    dd = run_job(d, alarm_of("latency_sql")).briefing["deep_dive"]
    assert dd["status"] == "done"
    assert dd["summary"].endswith("### 장애 분석 결과 보고서: web01")  # 종전 첫 줄 그대로
    assert dd["narrative"] == REPORT


def test_skipped_deep_dive_has_no_narrative() -> None:
    settings = make_settings(sre_deep_dive_mode="off")
    d = build(settings, FakeCaller(_load("latency_sql")), llm=FakeLLM())
    assert "narrative" not in run_job(d, alarm_of("latency_sql")).briefing["deep_dive"]


def test_long_answer_clipped_with_note() -> None:
    long = "가" * (disp_mod.DEEP_NARRATIVE_MAX_CHARS + 50)
    out = disp_mod._clip_narrative(long)
    assert out.startswith("가" * disp_mod.DEEP_NARRATIVE_MAX_CHARS)
    assert out.endswith(disp_mod.DEEP_NARRATIVE_CLIPPED.format(
        n=len(long), keep=disp_mod.DEEP_NARRATIVE_MAX_CHARS))
    assert disp_mod._clip_narrative("  짧은 답  ") == "짧은 답"
