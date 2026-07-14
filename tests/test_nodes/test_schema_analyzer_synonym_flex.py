"""_synonym_tables_matching_query 유연 매칭(E5-1) + config 상한(E5-3) 검증.

무회귀 최우선: fuzzy 플래그 OFF(기본)일 때 기존 정확 부분어 매칭과 동일 결과임을
고정하고, ON일 때만 유연 근사 매칭이 추가로 동작함을 검증한다.
"""

from src.nodes.schema_analyzer import (
    _MAX_SYNONYM_SUPPLEMENT_TABLES,
    _synonym_tables_matching_query,
)


class TestFuzzyOffIsByteIdentical:
    """플래그 OFF(기본)는 기존 정확 부분어 매칭과 동일해야 한다(회귀 0)."""

    def test_off_spacing_variant_not_matched(self):
        # 질의는 "메모리사용률"(붙임), 유사어는 "메모리 사용률"(띄움) → 정확 부분어 실패
        syns = {"cmm_resource.mem": ["메모리 사용률"]}
        match = "서버의 메모리사용률 조회"
        assert _synonym_tables_matching_query(syns, match) == set()
        # 명시적으로 fuzzy=False도 동일
        assert _synonym_tables_matching_query(syns, match, fuzzy=False) == set()

    def test_off_exact_substring_still_matches(self):
        syns = {"cmm_resource.cpu": ["씨피유"]}
        assert _synonym_tables_matching_query(syns, "씨피유 조회") == {"cmm_resource"}


class TestFuzzyOn:
    """플래그 ON일 때 표기 변형·근사를 추가로 잡는다."""

    def test_on_spacing_variant_matched(self):
        syns = {"cmm_resource.mem": ["메모리 사용률"]}
        match = "서버의 메모리사용률 조회"
        result = _synonym_tables_matching_query(
            syns, match, fuzzy=True, min_score=0.85
        )
        assert "cmm_resource" in result

    def test_on_unrelated_still_excluded(self):
        # 유연 매칭이 켜져도 완전 무관 용어는 유입되지 않아야 한다(정밀도 유지)
        syns = {"accounting.x": ["회계전표"]}
        result = _synonym_tables_matching_query(
            syns, "cpu 메모리 조회", fuzzy=True, min_score=0.85
        )
        assert result == set()

    def test_on_respects_guard_single_char(self):
        # 1글자 유사어는 fuzzy에서도 제외(길이 2 미만 스킵)
        syns = {"noise.c": ["a"]}
        result = _synonym_tables_matching_query(
            syns, "a server query", fuzzy=True, min_score=0.85
        )
        assert result == set()


class TestConfigCap:
    """E5-3: cap이 config에서 주입되어 상한을 제어한다(기본 15 유지)."""

    def test_cap_override_from_config_value(self):
        syns = {f"t{i}.c": ["코어"] for i in range(50)}
        # config가 3으로 낮추면 3개까지만 (fuzzy 무관)
        result = _synonym_tables_matching_query(syns, "코어 조회", cap=3)
        assert len(result) == 3

    def test_default_cap_unchanged(self):
        assert _MAX_SYNONYM_SUPPLEMENT_TABLES == 15
