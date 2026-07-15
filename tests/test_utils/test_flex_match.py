"""flex_match 공유 유연 매칭 유틸 테스트 (Plan 61 트랙 B / E5-1).

계단식 매칭(정확→구분자제거→부분어→자모 편집거리), 가드(빈문자열·1글자),
신뢰도 경계, best_flex_match 임계 동작을 검증한다.
"""

from src.utils.flex_match import (
    _decompose_jamo,
    _levenshtein,
    best_flex_match,
    flex_match_score,
)


class TestExactAndNormalization:
    def test_exact_equal(self):
        assert flex_match_score("메모리 사용률", "메모리 사용률") == 1.0

    def test_case_insensitive_exact(self):
        assert flex_match_score("CPU", "cpu") == 1.0

    def test_whitespace_normalized_exact(self):
        # 앞뒤·연속 공백은 정규화되어 정확 동등
        assert flex_match_score("  메모리   사용률 ", "메모리 사용률") == 1.0

    def test_space_stripped_equal(self):
        # 공백만 다르면 구분자 제거 동등(0.97)
        assert flex_match_score("메모리사용률", "메모리 사용률") == 0.97

    def test_underscore_and_case(self):
        assert flex_match_score("CPU_USAGE", "cpu usage") == 0.97


class TestContainment:
    def test_substring_containment_scored(self):
        # "메모리"가 "메모리 사용률"에 포함 → 0.85~0.95 사이
        score = flex_match_score("메모리 사용률", "메모리")
        assert 0.85 <= score < 0.97

    def test_longer_shorter_symmetric(self):
        assert flex_match_score("서버명", "서버") == flex_match_score("서버", "서버명")


class TestJamoEditDistance:
    def test_jamo_decompose(self):
        # NFD 자모 분해로 음절이 자모열로 늘어난다
        assert len(_decompose_jamo("메모리")) > len("메모리")

    def test_close_typo_scores_between_zero_and_one(self):
        # 자모 1~2개 차이는 0과 1 사이의 근사 점수
        score = flex_match_score("메모리", "메모으리")
        assert 0.0 < score < 1.0

    def test_levenshtein_basic(self):
        assert _levenshtein("abc", "abc") == 0
        assert _levenshtein("abc", "abd") == 1
        assert _levenshtein("abc", "") == 3


class TestGuards:
    def test_empty_string_zero(self):
        assert flex_match_score("", "메모리") == 0.0
        assert flex_match_score("메모리", "") == 0.0

    def test_single_char_only_exact(self):
        # 1글자는 정확 동등만 인정(근사 금지)
        assert flex_match_score("a", "ab") == 0.0
        assert flex_match_score("a", "a") == 1.0

    def test_cross_script_no_false_match(self):
        # 표기 체계가 완전히 다르면(한글↔영문) 유연 매칭 안 됨(E5-4 영역)
        assert flex_match_score("호스트명", "hostname") == 0.0


class TestBestFlexMatch:
    def test_returns_candidate_above_threshold(self):
        cand, score = best_flex_match(
            "메모리사용률", ["디스크", "메모리 사용률", "네트워크"], 0.85
        )
        assert cand == "메모리 사용률"
        assert score == 0.97

    def test_returns_none_below_threshold_but_keeps_score(self):
        cand, score = best_flex_match("완전다른값", ["디스크", "메모리"], 0.85)
        assert cand is None
        assert 0.0 <= score < 0.85

    def test_empty_candidates(self):
        cand, score = best_flex_match("메모리", [], 0.85)
        assert cand is None
        assert score == 0.0

    def test_threshold_boundary_inclusive(self):
        # min_score가 실제 점수와 정확히 같으면 확정 채택(>= 경계 포함, float 안전)
        s = flex_match_score("메모리 사용률", "메모리")
        cand, score = best_flex_match("메모리 사용률", ["메모리"], s)
        assert cand == "메모리"
        assert score == s

    def test_threshold_boundary_exclusive_above(self):
        # min_score가 점수보다 조금이라도 높으면 미확정
        s = flex_match_score("메모리 사용률", "메모리")
        cand, _score = best_flex_match("메모리 사용률", ["메모리"], s + 0.01)
        assert cand is None
