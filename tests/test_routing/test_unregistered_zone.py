"""미등록 존 토큰 탐지 (plans/108 CU-B2 · G-3 사용자 확정 2026-09-21).

run `20260918-182507` R3-03(3회 전건 동일): 질의 "판교존 서버 목록"에 대해 생성 SQL 주석이
`-- 판교존(지역 힌트는 스키마에 없으므로 무시)` 였고 전 서버 1,690건을 반환했다. 등록되지 않은
존을 **침묵 무시**한 것이라 「침묵적 폴백 금지」 위반이다.

탐지 규칙은 **좁게** 못 박는다 — 오탐(정상 질의를 역질문으로 가로채기)의 대가가 미탐보다 크다.
"""

from __future__ import annotations

import pytest

from src.routing.db_scope import find_unregistered_zone_terms


class TestUnregisteredZoneDetected:
    """레지스트리에 없는 `…존` 토큰은 탐지된다."""

    def test_pangyo_zone_is_detected(self):
        assert find_unregistered_zone_terms("판교존 서버 목록") == ["판교존"]

    def test_detected_with_surrounding_text(self):
        assert find_unregistered_zone_terms(
            "판교존의 모든 서버 CPU 사용률을 보여줘"
        ) == ["판교존"]

    def test_multiple_unregistered_zones_preserve_order(self):
        assert find_unregistered_zone_terms("판교존과 분당존 서버") == ["판교존", "분당존"]

    def test_duplicates_collapse(self):
        assert find_unregistered_zone_terms("판교존 서버와 판교존 알람") == ["판교존"]


class TestRegisteredZonesNotFlagged:
    """레지스트리 위치 표면어·존 그룹 라벨은 탐지 대상이 아니다."""

    @pytest.mark.parametrize("query", [
        "은행존 서버 목록",
        "공동존 서버 목록",
        "은행존과 공동존 서버 모두",
        "공동존 김포 서버",
    ])
    def test_registered_zone_terms(self, query):
        assert find_unregistered_zone_terms(query) == []


class TestNoFalsePositives:
    """`…존`으로 끝나는 일반 한국어 낱말을 존으로 오인하지 않는다."""

    @pytest.mark.parametrize("query", [
        "기존 서버 목록 보여줘",
        "보존 정책이 어떻게 되나",
        "의존 관계를 알려줘",
        "잔존 용량 조회",
        "공존 가능한 설정",
        "현존하는 장비 목록",
        "생존 신호가 있는 서버",
        "자존 시간 확인",
        # 3음절 이상 합성어 — `[가-힣]{2,}존` 만으로는 걸리는 형태
        "상호의존 관계 서버",
        "적자생존 정책",
        "데이터 보존 기간",
    ])
    def test_common_words_not_flagged(self, query):
        assert find_unregistered_zone_terms(query) == []

    def test_zone_placeholder_not_flagged(self):
        """`ㅇㅇ존` 플레이스홀더(D-143)는 자모라 탐지 대상이 아니다."""
        assert find_unregistered_zone_terms("ㅇㅇ존 서버 목록") == []

    @pytest.mark.parametrize("query", ["", "   ", "서버 목록 보여줘", "CPU 사용률 상위 10대"])
    def test_no_zone_token(self, query):
        assert find_unregistered_zone_terms(query) == []

    def test_none_input(self):
        assert find_unregistered_zone_terms(None) == []
