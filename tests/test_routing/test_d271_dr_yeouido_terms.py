"""운영 = 김포 · 개발·스테이징·DR = 여의도 폴스타 기준 용어 (D-271 · 사용자 확정 2026-09-28
"DR은 여의도이다" · "운영은 김포로 매핑하면 된다").

종전 레지스트리는 김포를 「운영/DR」로 표기하고 `공동존 DR 폴스타`를 김포 별칭에 두어 힌트 "DR"이
김포로 풀렸다. 「개발」은 위치어가 아니라 환경어라 결정적 힌트 보강·생략형 승계(PL-1)의 위치어
판정·존 역질문 스킵에서 빠졌고, "개발 서버"는 어느 DB로도 풀리지 않았으며 "공동존 개발 폴스타"는
김포까지 끌어들였다. 라틴 표면어 "DR"은 "DRM"·"ADDRESS"에 부분 일치하면 안 되고, "운영"은
"운영체제"(OS · OSType 공식 별칭)·"운영 중"·"운영자"·"운영팀" 안에서는 위치가 아니다.
실 LLM 0 · DB 0.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.nodes.input_parser import _ensure_location_hints
from src.routing.location_hints import resolve_priority_db_ids, strip_location_terms
from src.routing.registry import get_registry
from src.utils.query_gen_common import (
    LOCATION_HINT_TERMS,
    elliptical_succession_filter,
    remove_term,
    term_in_text,
)

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_GP, _YD = "polestar_cm_gp", "polestar_cm_yd"


class TestRegistry:
    def test_yeouido_exclusive_terms(self):
        hints = get_registry().location_db_hints()
        assert {"여의도", "개발", "스테이징", "DR"} <= set(hints[_YD])
        assert "DR" not in hints.get(_GP, ())

    def test_gimpo_exclusive_terms(self):
        hints = get_registry().location_db_hints()
        assert set(hints[_GP]) == {"김포", "운영"}

    def test_gimpo_display_name_has_no_dr(self):
        reg = get_registry()
        gp, yd = reg.get(_GP), reg.get(_YD)
        assert "DR" not in gp.display_name and "DR" not in gp.description
        assert "DR" not in " ".join(gp.aliases)
        assert "DR" in yd.display_name and "공동존 DR 폴스타" in yd.aliases

    def test_signal_terms_have_no_duplicates(self):
        terms = get_registry().location_signal_terms()
        assert len(terms) == len(set(terms))
        assert {"운영", "개발", "스테이징", "DR"} <= set(terms)

    def test_utils_copy_matches_registry(self):
        assert tuple(get_registry().location_terms()) == LOCATION_HINT_TERMS


class TestTermInText:
    @pytest.mark.parametrize(
        "text", ["DR 서버 CPU", "DR도 보여줘", "dr 서버", "(DR)", "DR서버 목록", "김포/DR"],
    )
    def test_latin_term_matches_at_boundary(self, text):
        assert term_in_text("DR", text)

    @pytest.mark.parametrize("text", ["DRM 해제 이력", "ADDRESS 조회", "HDR 설정", "DR2 서버"])
    def test_latin_term_ignores_embedded(self, text):
        assert not term_in_text("DR", text)

    def test_korean_term_is_substring(self):
        assert term_in_text("개발", "개발도 보여줘")
        assert term_in_text("여의도", "여의도에서")
        assert term_in_text("운영", "운영 서버 CPU")
        assert term_in_text("운영", "운영도 보여줘")

    @pytest.mark.parametrize(
        "text",
        [
            "운영체제 종류", "서버별 운영체제와 버전",
            "현재 운영 중인 서버 목록", "운영중인 서버 CPU", "운영  중인 서버",
            "운영자 계정", "운영팀 알람",
        ],
    )
    def test_non_location_operation_words(self, text):
        """OS·상태·사람·조직의 「운영」은 김포가 아니다(사용자 확정 「넷 다 제외」)."""
        assert not term_in_text("운영", text)

    @pytest.mark.parametrize(
        "text", ["운영 서버 목록", "운영 중요 서버", "운영 서버 중 CPU 높은 것"],
    )
    def test_operation_as_location(self, text):
        assert term_in_text("운영", text)

    def test_operating_system_and_location_together(self):
        assert term_in_text("운영", "운영 서버의 운영체제")

    def test_remove_term_keeps_embedded(self):
        assert remove_term("DR 서버 DRM", "DR").split() == ["서버", "DRM"]
        assert remove_term("운영 서버 운영체제", "운영").split() == ["서버", "운영체제"]


class TestResolve:
    @pytest.mark.parametrize(
        "hints,expected",
        [
            (["개발"], [_YD]),
            (["개발 서버"], [_YD]),
            (["스테이징"], [_YD]),
            (["DR"], [_YD]),
            (["DR 서버"], [_YD]),
            (["dr"], [_YD]),
            (["공동존 개발 폴스타"], [_YD]),
            (["공동존 DR 폴스타"], [_YD]),
            (["김포"], [_GP]),
            (["운영"], [_GP]),
            (["운영 서버"], [_GP]),
            (["공동존 운영 폴스타"], [_GP]),
            (["운영체제"], []),
            (["운영 중인 서버"], []),
            (["운영과 개발"], [_GP, _YD]),
            (["공동존"], [_GP, _YD]),
            (["김포와 DR"], [_GP, _YD]),
            (["DRM"], []),
        ],
    )
    def test_hint_resolution(self, hints, expected):
        assert resolve_priority_db_ids(hints, _ACTIVE) == expected

    def test_strip_location_terms_keeps_embedded_latin(self):
        assert strip_location_terms("DR 서버 CPU") == "서버 CPU"
        assert strip_location_terms("DRM 서버 개발 목록") == "DRM 서버 목록"
        assert strip_location_terms("운영 서버 운영체제") == "서버 운영체제"


class TestHintAugmentation:
    def test_dr_query_adds_hint(self):
        parsed = _ensure_location_hints({"target_db_hints": []}, "DR 서버 CPU 사용률")
        assert "DR" in parsed["target_db_hints"]

    def test_drm_query_adds_no_hint(self):
        parsed = _ensure_location_hints({"target_db_hints": []}, "DRM 해제 이력 알려줘")
        assert parsed["target_db_hints"] == []

    def test_operation_query_adds_hint(self):
        parsed = _ensure_location_hints({"target_db_hints": []}, "운영 서버 CPU 사용률")
        assert parsed["target_db_hints"] == ["운영"]

    @pytest.mark.parametrize(
        "query", ["전체 서버 운영체제 종류", "현재 운영 중인 서버 목록", "운영팀 알람 이력"],
    )
    def test_non_location_operation_query_adds_no_hint(self, query):
        parsed = _ensure_location_hints({"target_db_hints": []}, query)
        assert parsed["target_db_hints"] == []

    def test_development_query_adds_hint(self):
        parsed = _ensure_location_hints({"target_db_hints": []}, "개발 서버 목록")
        assert parsed["target_db_hints"] == ["개발"]


def _ctx() -> dict[str, Any]:
    return {
        "turn_count": 3,
        "previous_result_count": 1,
        "previous_entities": [{"field": "server_name", "value": "cocm-hdkapp01"}],
        "previous_entities_complete": True,
    }


def _parsed() -> dict[str, Any]:
    return {
        "query_targets": ["서버"],
        "filter_conditions": [],
        "target_db_hints": [],
        "output_format": "text",
    }


class TestEllipticalSuccession:
    """PL-1: 김포·여의도 기준 용어가 붙은 후속 턴은 직전 서버를 잇지 않는다(대상 DB 전환 신호)."""

    def test_control_still_fires(self):
        assert elliptical_succession_filter("그럼 메모리도 보여줘", _parsed(), _ctx()) is not None

    @pytest.mark.parametrize(
        "query",
        ["그럼 개발도 보여줘", "그럼 DR도 보여줘", "스테이징도 보여줘", "그럼 운영도 보여줘"],
    )
    def test_yeouido_terms_block_succession(self, query):
        assert elliptical_succession_filter(query, _parsed(), _ctx()) is None
