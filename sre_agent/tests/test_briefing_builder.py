"""briefing_builder 검증 (Plan 02 §7) — 6요소·인용 검증→가설 강등·human-gated·한계 강제."""

from sre_agent.application.briefing_builder import (
    BRIEFING_ELEMENTS,
    HUMAN_GATED_NOTE,
    HYPOTHESIS_PREFIX,
    build_briefing,
    stub_briefing,
)
from sre_agent.domain.severity_signatures import judge

OOM_LOG = "kernel: Out of memory: Killed process 12345 (java)"


def _verdict(gate=2, outputs=None):
    return judge(gate_severity=gate, tool_outputs=outputs or [OOM_LOG])


# ── 6요소 스키마 ──────────────────────────────────────────────────


def test_six_elements_present():
    b = build_briefing(
        answer="web-01 메모리 고갈 확인 ← polestar_metric_trend",
        verdict=_verdict(),
        tool_names=["polestar_metric_trend"],
        gate_tier="PAGE",
    )
    for el in BRIEFING_ELEMENTS:
        assert el in b, f"6요소 결측: {el}"
    # 중요도 헤더는 severity_judge 판정에서 온다.
    assert b["severity"]["level"] == "심각"
    assert b["severity"]["gate_tier"] == "PAGE"


# ── 소비자 계약 (collectorinfra tests/test_briefing_contract.py 와 같은 리터럴) ──

CONSUMER_CONTRACT_KEYS = frozenset({
    "severity", "summary", "timeline", "bottleneck", "cause", "root_cause_hypotheses",
    "recommendation", "limitations", "citations_verified", "hypotheses",
})


def test_output_keys_match_consumer_contract():
    """build_briefing() 산출 키 == 본체 소비자 렌더러가 인지하는 키(plans/50 G6 · D-197).

    키를 늘리거나 개명하면 본체의 `tests/test_briefing_contract.py::CONTRACT_KEYS`와
    `noise_gate/domain/investigation_briefing.py::_ORDERED`도 함께 갱신한다(양방향 import 0이라
    같은 리터럴을 양쪽에 둔다).
    """
    b = build_briefing(answer="x ← t", verdict=_verdict(), tool_names=["t"], gate_tier="PAGE")
    assert set(b) == CONSUMER_CONTRACT_KEYS


# ── 인용 검증 → 가설 강등 ─────────────────────────────────────────


def test_cited_claim_not_downgraded():
    b = build_briefing(
        answer="메모리 고갈로 java OOM 종료 ← journalctl [원문]",
        verdict=_verdict(),
        tool_names=["journalctl"],
    )
    assert b["citations_verified"] is True
    assert not b["summary"].startswith(HYPOTHESIS_PREFIX)


def test_uncited_claim_downgraded_to_hypothesis():
    # 도구 출력 없음(tool_names 비어 있음) → 서술 전체가 근거 없는 단정 → 가설 강등.
    b = build_briefing(
        answer="아마 메모리 누수로 추정됨",
        verdict=_verdict(),
        tool_names=[],
    )
    assert b["citations_verified"] is False
    assert b["summary"].startswith(HYPOTHESIS_PREFIX)
    assert "아마 메모리 누수로 추정됨" in b["hypotheses"]
    # 한계에 검증 불가 사유가 명시된다.
    assert any("가설로 강등" in x for x in b["limitations"])


def test_tool_name_reference_counts_as_citation():
    b = build_briefing(
        answer="CPU 포화 지속 polestar_metric_trend 참조",
        verdict=judge(gate_severity=2, tool_outputs=["clean"]),
        tool_names=["polestar_metric_trend"],
    )
    assert b["citations_verified"] is True


# ── 권고는 human-gated(실행 경로 없음) ────────────────────────────


def test_recommendation_is_human_gated():
    b = build_briefing(
        answer="원인: 힙 상한 미설정 ← 프로세스 RSS 추이",
        verdict=_verdict(),
        tool_names=["polestar_process_snapshot"],
        remediation=["java.service 힙 상향·재기동", "메모리 누수 점검"],
    )
    assert b["recommendation"]["note"] == HUMAN_GATED_NOTE
    assert "승인" in b["recommendation"]["note"]
    assert b["recommendation"]["items"][0] == "java.service 힙 상향·재기동"


def test_recommendation_default_when_none():
    b = build_briefing(answer="x ← t", verdict=_verdict(), tool_names=["t"])
    assert "조치 권고 없음" in b["recommendation"]["items"][0]


# ── 한계 서술 강제 ────────────────────────────────────────────────


def test_limitations_always_include_snapshot_note():
    b = build_briefing(answer="x ← t", verdict=_verdict(), tool_names=["t"])
    assert any("단면" in x for x in b["limitations"])


def test_limitations_include_evidence_insufficient():
    v = judge(gate_severity=3, tool_outputs=["nothing"], remote=True)  # evidence_insufficient
    b = build_briefing(answer="원격 조사", verdict=v, tool_names=[])
    assert v.evidence_insufficient is True
    assert any("증거 불충분" in x for x in b["limitations"])


# ── 병목: 시그니처 라벨 도출 ──────────────────────────────────────


def test_bottleneck_from_signals():
    b = build_briefing(answer="x ← journalctl", verdict=_verdict(), tool_names=["journalctl"])
    assert "메모리 고갈" in b["bottleneck"]


def test_bottleneck_undetermined_without_signals():
    v = judge(gate_severity=2, tool_outputs=["clean"])
    b = build_briefing(answer="x ← t", verdict=v, tool_names=["t"])
    assert "미확정" in b["bottleneck"]


# ── 스텁 브리핑 ───────────────────────────────────────────────────


def test_stub_briefing_shape():
    s = stub_briefing("조사 미실행 — LLM 키 부재(스텁)")
    assert s["stub"] is True
    assert s["elements"] is None
    assert "LLM 키 부재" in s["message"]


# ── 결정적 상관 → 타임라인·가설·한계 (plans/50 §7.2·§9.1 · SPEC-diagnosis-briefing) ──

CORR = {
    "reference_time": "2026-09-01T14:00:00",
    "timeline": [
        {"t_offset_min": -15, "kind": "metric", "detail": "disk_io sustained 시작 (peak 97.0 @ 2026-09-01T13:50:00 z=12.5)"},
        {"t_offset_min": -12, "kind": "metric", "detail": "cpu spike 시작 (peak 88.0 @ 2026-09-01T13:48:00)"},
        {"t_offset_min": -10, "kind": "alarm", "detail": "[severity 3] CPU Utilization Critical (cpu0)"},
        {"t_offset_min": -2, "kind": "alarm", "detail": "[해소] CPU Utilization Critical (cpu0)"},
    ],
    "metric_findings": {
        "disk_io": {"is_anomalous": True, "kind": "sustained", "onset_offset_min": -15, "peak_value": 97.0,
                    "peak_time": "2026-09-01T13:50:00", "z_score": 12.5, "lead_lag_min": -5},
        "cpu": {"is_anomalous": True, "kind": "spike", "onset_offset_min": -12, "peak_value": 88.0,
                "peak_time": "2026-09-01T13:48:00", "z_score": None, "lead_lag_min": -2},
        "memory": {"is_anomalous": False, "lead_lag_min": None},
    },
    "alarm_summary": {"count": 2, "first_offset_min": -10, "resolved": 1},
    "leading_signal": "disk_io",
    "notes": ["지표 정밀도 60분 단위 — 그보다 짧은 선후는 판정 불가"],
}


def _with_corr(answer="원인: 디스크 IO 폭주 ← polestar_metric_trend", corr=CORR):
    return build_briefing(answer=answer, verdict=_verdict(), tool_names=["polestar_metric_trend"],
                          gate_tier="PAGE", correlation=corr)


def test_correlation_timeline_is_relative_and_first():
    b = _with_corr()
    assert b["timeline"][0] == "T-15m 메트릭 disk_io sustained 시작 (peak 97.0 @ 2026-09-01T13:50:00 z=12.5)"
    assert b["timeline"][2] == "T-10m 알람 [severity 3] CPU Utilization Critical (cpu0)"
    assert b["timeline"][-1] == "원인: 디스크 IO 폭주 ← polestar_metric_trend"   # 인용 라인은 뒤에


def test_hypotheses_ranked_leading_first():
    b = _with_corr()
    h = b["root_cause_hypotheses"]
    assert [x["rank"] for x in h] == [1, 2, 3]
    assert h[0]["cause"] == "disk_io sustained 이상이 첫 알람에 5분 선행" and h[0]["confidence"] == "high"
    assert h[0]["evidence"][0].startswith("T-15m 메트릭 disk_io") and any("알람" in e for e in h[0]["evidence"])
    assert h[1]["cause"].startswith("cpu spike 이상") and h[1]["confidence"] == "medium"
    assert h[2]["cause"] == "원인: 디스크 IO 폭주 ← polestar_metric_trend" and h[2]["confidence"] == "medium"
    assert b["cause"] == h[0]["cause"]
    assert all("상관 ≠ 인과" in x["reasoning"] for x in h[:2])


def test_correlation_notes_reach_limitations():
    b = _with_corr()
    assert "지표 정밀도 60분 단위 — 그보다 짧은 선후는 판정 불가" in b["limitations"]
    assert any("상관 ≠ 인과" in x for x in b["limitations"])


def test_numbers_come_only_from_injected_values():
    """환각 0 — 브리핑의 수치는 correlation 주입값에만 존재한다."""
    b = _with_corr(answer="원인 서술 ← polestar_metric_trend")
    text = "\n".join(b["timeline"]) + "\n".join(h["cause"] + h["reasoning"] for h in b["root_cause_hypotheses"])
    import re
    for num in set(re.findall(r"\d+(?:\.\d+)?", text)):
        assert num in str(CORR) or num in ("1", "2", "3", "5"), num   # rank·시차(5=|−5|)


def test_no_correlation_keeps_previous_shape_and_empty_hypotheses():
    b = build_briefing(answer="x ← t", verdict=_verdict(), tool_names=["t"])
    assert b["root_cause_hypotheses"] == []
    assert b["timeline"] == ["x ← t"] and b["cause"] == "x ← t"
    assert not any("상관" in x for x in b["limitations"])


def test_uncited_llm_cause_is_low_confidence_hypothesis():
    b = build_briefing(answer="아마 누수", verdict=_verdict(), tool_names=[],
                       correlation={"timeline": [], "metric_findings": {}, "alarm_summary": {}, "leading_signal": None, "notes": []})
    h = b["root_cause_hypotheses"]
    assert len(h) == 1 and h[0]["confidence"] == "low" and h[0]["evidence"] == []


# ── "변경 직후" 가설 (plans/91 1-2 · C′-2) ────────────────────────────────────────────────
def test_change_before_first_alarm_becomes_rank_one_medium():
    corr = {
        **CORR,
        "timeline": [{"t_offset_min": -20, "kind": "change", "detail": "[UPDATE] 메모리 증설"}] + list(CORR["timeline"]),
        "change_finding": {"count": 1, "last_change_offset_min": -20, "before_first_alarm": True,
                           "descriptions": ["[UPDATE] 메모리 증설"]},
    }
    b = build_briefing(answer="원인: 디스크 IO 폭주 ← polestar_metric_trend", verdict=_verdict(), tool_names=["polestar_metric_trend"],
                       correlation=corr)
    h = b["root_cause_hypotheses"]
    assert h[0]["rank"] == 1 and h[0]["confidence"] == "medium"
    assert h[0]["cause"].startswith("변경 직후") and "[UPDATE] 메모리 증설" in h[0]["cause"] and "10분" in h[0]["cause"]
    assert h[0]["evidence"] == ["T-20m 변경 [UPDATE] 메모리 증설"]
    assert [x["rank"] for x in h] == list(range(1, len(h) + 1)) and h[1]["cause"].startswith("disk_io")
    assert b["timeline"][0] == "T-20m 변경 [UPDATE] 메모리 증설" and b["cause"] == h[0]["cause"]


def test_change_after_first_alarm_adds_no_hypothesis():
    corr = {**CORR, "change_finding": {"count": 1, "last_change_offset_min": -3, "before_first_alarm": False, "descriptions": ["d"]}}
    b = build_briefing(answer="x ← t", verdict=_verdict(), tool_names=["t"], correlation=corr)
    assert not any(h["cause"].startswith("변경 직후") for h in b["root_cause_hypotheses"])


# ── 도구 출력 0건이면 인용 마커가 있어도 인용이 아니다 (2026-09-17 MLX e2e 실측) ──


def _incomplete_answer() -> str:
    """DiagnosisAgent.ask가 step 상한에서 돌려주는 실제 미완주 서술(문구에 인용 마커 '근거' 포함)."""
    from unittest.mock import MagicMock, patch

    from sre_agent.diagnosis import DiagnosisAgent
    from sre_agent.settings import AgentSettings

    agent = DiagnosisAgent(settings=AgentSettings(_env_file=None, model="test/model", max_steps=40), toolsets={})
    agent._llm = MagicMock()
    agent._llm.call.side_effect = Exception("Too many LLM calls - exceeded max_steps: 40/40")
    with patch("sre_agent.diagnosis.build_initial_ask_messages", return_value=[]):
        r = agent.ask("q")
    assert r.incomplete and r.tool_outputs == []
    return r.answer


def test_incomplete_notice_is_not_treated_as_citation():
    """미완주 안내는 도구 근거가 아니다 — 원인은 가설 강등, 타임라인은 '근거 없음'(상관 on·off 모두)."""
    answer = _incomplete_answer()
    empty_corr = {"timeline": [], "metric_findings": {}, "alarm_summary": {"count": 0}, "leading_signal": None,
                  "notes": ["사건 구간 알람 0건"]}

    off = build_briefing(answer=answer, verdict=_verdict(), tool_names=[])
    on = build_briefing(answer=answer, verdict=_verdict(), tool_names=[], correlation=empty_corr)

    assert off["cause"].startswith(HYPOTHESIS_PREFIX)
    assert off["timeline"] == ["타임라인 근거 없음(도구 출력 인용 결여)"]
    assert on["cause"].startswith(HYPOTHESIS_PREFIX)
    assert on["timeline"] == ["타임라인 근거 없음(상관 타임라인·도구 출력 인용 모두 결여)"]
    assert on["citations_verified"] is False
