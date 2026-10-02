"""동일 용어 다중 컬럼 충돌 우선순위 규칙 테스트 (Plan 61 트랙 B / E5-3 / D-075).

결정적 우선순위(operator → usage_count → confidence → 컬럼명 사전순)와
메타 없는 후보 기본값(usage_count=0/confidence=0), tie-break 안정성을 검증한다.
"""

from src.utils.synonym_governance import (
    rank_synonym_candidates,
    resolve_synonym_conflict,
)


class TestOperatorPriority:
    def test_operator_beats_higher_usage_llm(self):
        # operator는 usage_count가 더 높은 llm보다 우선
        candidates = [
            {"column": "a.col", "source": "llm", "usage_count": 100},
            {"column": "b.col", "source": "operator", "usage_count": 1},
        ]
        winner = resolve_synonym_conflict(candidates)
        assert winner["column"] == "b.col"

    def test_operator_beats_higher_confidence_llm(self):
        candidates = [
            {"column": "a.col", "source": "llm", "confidence": 0.99},
            {"column": "b.col", "source": "operator", "confidence": 0.10},
        ]
        assert resolve_synonym_conflict(candidates)["column"] == "b.col"


class TestUsageThenConfidence:
    def test_usage_count_ranks_before_confidence(self):
        # 둘 다 llm이면 usage_count 우선
        candidates = [
            {"column": "a.col", "source": "llm", "usage_count": 5, "confidence": 0.1},
            {"column": "b.col", "source": "llm", "usage_count": 3, "confidence": 0.9},
        ]
        assert resolve_synonym_conflict(candidates)["column"] == "a.col"

    def test_confidence_breaks_equal_usage(self):
        candidates = [
            {"column": "a.col", "source": "llm", "usage_count": 3, "confidence": 0.4},
            {"column": "b.col", "source": "llm", "usage_count": 3, "confidence": 0.8},
        ]
        assert resolve_synonym_conflict(candidates)["column"] == "b.col"


class TestMissingMetaDefaults:
    def test_missing_meta_treated_as_zero(self):
        # 메타 없는 후보는 usage_count=0/confidence=0으로 간주 → 있는 쪽이 이김
        candidates = [
            {"column": "a.col"},  # 메타 없음
            {"column": "b.col", "source": "llm", "usage_count": 1},
        ]
        assert resolve_synonym_conflict(candidates)["column"] == "b.col"

    def test_missing_source_defaults_to_llm(self):
        # source 미지정은 llm으로 간주 → operator가 이김
        candidates = [
            {"column": "a.col", "usage_count": 50},
            {"column": "b.col", "source": "operator"},
        ]
        assert resolve_synonym_conflict(candidates)["column"] == "b.col"


class TestTieBreak:
    def test_column_name_alpha_tiebreak(self):
        # 모든 우선순위 동률 → 컬럼명 사전순 오름차순
        candidates = [
            {"column": "z.col", "source": "llm", "usage_count": 2, "confidence": 0.5},
            {"column": "a.col", "source": "llm", "usage_count": 2, "confidence": 0.5},
            {"column": "m.col", "source": "llm", "usage_count": 2, "confidence": 0.5},
        ]
        assert resolve_synonym_conflict(candidates)["column"] == "a.col"

    def test_ranking_is_deterministic_regardless_of_input_order(self):
        base = [
            {"column": "b.col", "source": "llm", "usage_count": 2, "confidence": 0.5},
            {"column": "a.col", "source": "operator", "usage_count": 0},
            {"column": "c.col", "source": "llm", "usage_count": 2, "confidence": 0.9},
        ]
        ranked1 = [c["column"] for c in rank_synonym_candidates(base)]
        ranked2 = [c["column"] for c in rank_synonym_candidates(list(reversed(base)))]
        assert ranked1 == ranked2
        # operator 최우선, 그 다음 confidence 높은 c, 마지막 b
        assert ranked1 == ["a.col", "c.col", "b.col"]


class TestEdgeCases:
    def test_empty_candidates_returns_empty_dict(self):
        assert resolve_synonym_conflict([]) == {}
        assert rank_synonym_candidates([]) == []

    def test_none_candidates_safe(self):
        assert resolve_synonym_conflict(None) == {}
        assert rank_synonym_candidates(None) == []

    def test_single_candidate(self):
        c = [{"column": "a.col", "source": "llm"}]
        assert resolve_synonym_conflict(c)["column"] == "a.col"

    def test_rank_does_not_mutate_input(self):
        candidates = [
            {"column": "b.col"},
            {"column": "a.col"},
        ]
        original = list(candidates)
        rank_synonym_candidates(candidates)
        assert candidates == original  # 원본 순서 불변
