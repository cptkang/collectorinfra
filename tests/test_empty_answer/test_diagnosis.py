"""0건 진단 — 미반영 판정(G-4) · 퍼널 XSS/MFS · 재생성 판정(G-5) (Plan 82 W8-T2·T3).

SPEC-empty-answer-diagnosis Success Criteria 1~4를 단언한다. 순수 함수만 다루므로
LLM·DB·네트워크 0(D-127) — 프로브 실행은 이 계층에 없다(`condition_probe` 소관).
"""

from __future__ import annotations

import pytest

from src.domain.change_terms import ChangeTerms
from src.domain.empty_answer import (
    SINGLE_GROUP,
    FunnelStage,
    build_diagnosis,
    detect_unexpressed_conditions,
    render_diagnosis,
)

TERMS = ChangeTerms(spike_terms=["갑자기", "급증"], default_delta_pp=20)


def _stage(label: str, counts: dict, source: str = "probe") -> FunnelStage:
    return FunnelStage(label=label, counts=counts, source=source)


# ──────────────────────────────────────────────
# G-4 — 표현되지 못한 조건
# ──────────────────────────────────────────────

class TestUnexpressed:
    def test_change_term_without_matching_condition_warns(self):
        result = detect_unexpressed_conditions(
            "CPU 80% 이상인 서버 중 파일시스템이 갑자기 80% 이상으로 상승한 목록",
            ["CPU 사용률 80% 이상", "파일시스템 사용률 80% 이상"],
            TERMS,
        )

        assert len(result) == 1
        assert "갑자기" in result[0]
        assert "반영되지 않았습니다" in result[0]

    def test_no_change_term_yields_no_warning(self):
        assert detect_unexpressed_conditions(
            "CPU 80% 이상인 서버", ["CPU 사용률 80% 이상"], TERMS
        ) == []

    def test_condition_expressing_the_term_yields_no_warning(self):
        assert detect_unexpressed_conditions(
            "파일시스템이 갑자기 상승한 서버",
            ["파일시스템 사용률이 갑자기 상승"],
            TERMS,
        ) == []

    def test_empty_filter_conditions_still_warns(self):
        assert detect_unexpressed_conditions("갑자기 오른 서버", None, TERMS)


# ──────────────────────────────────────────────
# 퍼널 XSS/MFS
# ──────────────────────────────────────────────

class TestFunnel:
    def test_xss_and_mfs_located(self):
        diag = build_diagnosis(
            parsed={},
            stage_counts=[
                _stage("지난 1달 대상 서버", {SINGLE_GROUP: 1204}),
                _stage("CPU 사용률 80% 이상", {SINGLE_GROUP: 12}),
                _stage("+ 파일시스템 사용률 80% 이상", {SINGLE_GROUP: 0}),
            ],
            unexpressed=[],
            notes=[],
        )

        (bp,) = diag.breakpoints
        assert bp.xss_index == 1, "결과가 남은 마지막 단계 = XSS"
        assert bp.mfs_index == 2, "처음 0이 된 단계 = MFS"

    def test_unmeasured_stage_does_not_become_a_breakpoint(self):
        diag = build_diagnosis(
            parsed={},
            stage_counts=[
                _stage("대상", {SINGLE_GROUP: 100}),
                _stage("절단됨", {SINGLE_GROUP: None}),
                _stage("조건 2", {SINGLE_GROUP: 0}),
            ],
            unexpressed=[],
            notes=[],
        )

        (bp,) = diag.breakpoints
        assert bp.xss_index == 0
        assert bp.mfs_index == 2

    def test_groups_are_tracked_separately(self):
        diag = build_diagnosis(
            parsed={},
            stage_counts=[
                _stage("대상 서버", {"은행존": 412, "공동존": 792}, source="group_results"),
                _stage("CPU 80% 이상", {"은행존": 12, "공동존": 0}),
                _stage("+ 파일시스템 80% 이상", {"은행존": 0, "공동존": None}),
            ],
            unexpressed=[],
            notes=[],
        )

        by_group = {bp.group: bp for bp in diag.breakpoints}
        assert by_group["공동존"].mfs_index == 1
        assert by_group["은행존"].mfs_index == 2
        assert by_group["은행존"].xss_index == 1


# ──────────────────────────────────────────────
# G-5 — 0건 재생성 판정
# ──────────────────────────────────────────────

class TestRegenerable:
    def test_p0_positive_stops_regeneration(self):
        diag = build_diagnosis(
            parsed={},
            stage_counts=[
                _stage("대상", {SINGLE_GROUP: 1204}),
                _stage("조건 1", {SINGLE_GROUP: 0}),
            ],
            unexpressed=[], notes=[],
        )

        assert diag.regenerable is False

    def test_p0_zero_allows_regeneration_with_scope_note(self):
        diag = build_diagnosis(
            parsed={},
            stage_counts=[_stage("대상", {SINGLE_GROUP: 0})],
            unexpressed=[], notes=[],
        )

        assert diag.regenerable is True
        assert any("조회 범위" in n for n in diag.notes)

    def test_any_group_with_rows_stops_regeneration(self):
        diag = build_diagnosis(
            parsed={},
            stage_counts=[_stage("대상", {"은행존": 412, "공동존": 0})],
            unexpressed=[], notes=[],
        )

        assert diag.regenerable is False

    def test_unmeasured_p0_keeps_current_behaviour(self):
        """프로브 실패로 P0을 못 쟀으면 판정하지 않는다 — 진단 실패가 기존 경로를 바꾸면 안 된다."""
        diag = build_diagnosis(
            parsed={},
            stage_counts=[_stage("대상", {SINGLE_GROUP: None})],
            unexpressed=[], notes=[],
        )

        assert diag.regenerable is True
        assert not any("조회 범위" in n for n in diag.notes)

    def test_no_stages_keeps_current_behaviour(self):
        diag = build_diagnosis(parsed={}, stage_counts=[], unexpressed=[], notes=[])

        assert diag.regenerable is True
        assert render_diagnosis(diag) == ""


# ──────────────────────────────────────────────
# 렌더
# ──────────────────────────────────────────────

class TestRender:
    def test_single_group_table_marks_breakpoint(self):
        diag = build_diagnosis(
            parsed={},
            stage_counts=[
                _stage("지난 1달 대상 서버", {SINGLE_GROUP: 1204}),
                _stage("CPU 사용률 80% 이상", {SINGLE_GROUP: 12}),
                _stage("+ 파일시스템 사용률 80% 이상", {SINGLE_GROUP: 0}),
            ],
            unexpressed=["급증 조건은 반영되지 않았습니다."],
            notes=["프로브 상한 5개를 넘어 이후 단계는 측정하지 않았습니다."],
        )

        text = render_diagnosis(diag)

        assert "| 단계 | 조건 | 잔존 |" in text
        assert "1,204" in text
        assert "여기서 끊겼습니다" in text
        assert text.count("여기서 끊겼습니다") == 1
        assert "⚠ 급증 조건은 반영되지 않았습니다." in text
        assert "프로브 상한" in text
        assert "2단계" in text and "완화해 보세요" in text

    def test_multi_group_table_splits_columns(self):
        diag = build_diagnosis(
            parsed={},
            stage_counts=[
                _stage("대상 서버", {"은행존": 412, "공동존": 792}),
                _stage("CPU 80% 이상", {"은행존": 12, "공동존": 0}),
                _stage("+ 파일시스템 80% 이상", {"은행존": 0, "공동존": None}),
            ],
            unexpressed=[], notes=[],
        )

        text = render_diagnosis(diag)

        assert "| 단계 | 조건 | 은행존 | 공동존 |" in text
        assert "—" in text, "미측정 칸은 대시로 표시한다"
        assert "공동존: 1단계" in text
        assert "은행존: 2단계" in text

    def test_p0_zero_renders_scope_warning_and_no_relaxation_hint(self):
        diag = build_diagnosis(
            parsed={},
            stage_counts=[_stage("대상 서버", {SINGLE_GROUP: 0})],
            unexpressed=[], notes=[],
        )

        text = render_diagnosis(diag)

        assert "조회 범위" in text
        assert "완화해 보세요" not in text, "0단계는 완화 대상이 아니다 — 범위 오류 신호다"
