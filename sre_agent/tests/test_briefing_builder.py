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
    assert "W-C 소관" in b["recommendation"]["items"][0]


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
