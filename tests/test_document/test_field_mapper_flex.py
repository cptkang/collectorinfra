"""_apply_synonym_mapping 유연 매칭 폴백(E5-1 (a)) 검증.

무회귀 최우선: fuzzy 플래그 OFF(기본)일 때 기존 정확 매칭과 동일 결과임을 고정하고,
ON일 때만 임계 이상 유연 매칭을 확정 채택함을 검증한다. 임계 미달은 확정하지 않아
다운스트림(LLM/pending) 경로로 위임되어야 한다.
"""

from src.document.field_mapper import MappingResult, _apply_synonym_mapping


def _syns() -> dict[str, dict[str, list[str]]]:
    return {
        "db1": {
            "cmm_resource.mem_usage": ["메모리 사용률"],
            "cmm_resource.cpu_usage": ["CPU 사용률"],
        }
    }


class TestFuzzyOffByteIdentical:
    def test_off_exact_match_maps(self):
        result = MappingResult()
        remaining = {"메모리 사용률"}
        _apply_synonym_mapping(remaining, _syns(), ["db1"], result)
        assert result.db_column_mapping["db1"]["메모리 사용률"] == "cmm_resource.mem_usage"
        assert "메모리 사용률" not in remaining

    def test_off_partial_not_mapped(self):
        # 부분어("메모리")는 기존 정확/공백제거 매칭이 잡지 못함 → OFF에서 remaining 유지.
        # (기존 _synonym_match는 공백 제거는 하지만 부분 포함은 매칭하지 않는다.)
        result = MappingResult()
        remaining = {"메모리"}
        _apply_synonym_mapping(remaining, _syns(), ["db1"], result)
        assert "메모리" in remaining
        assert result.db_column_mapping == {}

    def test_off_explicit_fuzzy_false_same(self):
        result = MappingResult()
        remaining = {"메모리"}
        _apply_synonym_mapping(remaining, _syns(), ["db1"], result, fuzzy=False)
        assert "메모리" in remaining


class TestFuzzyOn:
    def test_on_partial_mapped_confident(self):
        # 부분어("메모리")가 "메모리 사용률"에 포함(신뢰도 0.90) → ON에서 확정 채택
        result = MappingResult()
        remaining = {"메모리"}
        _apply_synonym_mapping(
            remaining, _syns(), ["db1"], result, fuzzy=True, min_score=0.85
        )
        assert result.db_column_mapping["db1"]["메모리"] == "cmm_resource.mem_usage"
        assert result.mapping_sources["메모리"] == "synonym"
        assert "메모리" not in remaining

    def test_on_below_threshold_not_confirmed(self):
        # 완전 무관한 필드는 임계 미달 → 확정하지 않고 remaining 유지(후보 제시 위임)
        result = MappingResult()
        remaining = {"전혀다른회계항목"}
        _apply_synonym_mapping(
            remaining, _syns(), ["db1"], result, fuzzy=True, min_score=0.85
        )
        assert "전혀다른회계항목" in remaining
        assert result.db_column_mapping == {}

    def test_on_high_threshold_blocks_weak_match(self):
        # min_score를 1.0으로 올리면 부분어(0.90)도 확정 안 됨 → remaining 유지
        result = MappingResult()
        remaining = {"메모리"}
        _apply_synonym_mapping(
            remaining, _syns(), ["db1"], result, fuzzy=True, min_score=1.0
        )
        assert "메모리" in remaining
