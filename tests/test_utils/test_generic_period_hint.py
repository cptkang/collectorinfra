"""build_generic_period_hint 렌더 회귀 테스트 (Plan 69 P0-①).

D-102에서 resolve_stat_month_range 반환이 str → (시작, 끝) 튜플로 바뀌었는데
build_generic_period_hint만 미갱신되어 프롬프트에 파이썬 튜플 repr이 그대로
렌더되던 결함(`월(YYYYMM) '('202604', '202606')'`)의 재발 방지.
"""

from src.utils.query_gen_common import build_generic_period_hint


class TestBuildGenericPeriodHint:
    def test_tuple_range_renders_without_repr(self):
        """범위 튜플 입력 시 튜플 repr이 아니라 시작~끝 형태로 렌더된다."""
        hint = build_generic_period_hint(("202604", "202606"))
        assert "(" not in hint.split("월(YYYYMM)")[1].split("로 해석")[0].replace("'", "")
        assert "'202604'~'202606'" in hint
        assert "('202604', '202606')" not in hint

    def test_single_month_tuple_renders_as_single(self):
        """(월, 월) 동일 범위는 단일 월로 렌더된다."""
        hint = build_generic_period_hint(("202605", "202605"))
        assert "'202605'" in hint
        assert "~" not in hint

    def test_legacy_str_input_byte_compatible(self):
        """기존 단일 문자열 입력의 렌더 바이트는 불변이다(하위호환)."""
        hint = build_generic_period_hint("202605")
        assert "질의의 기간 표현은 월(YYYYMM) '202605'로 해석되었습니다" in hint

    def test_none_returns_empty(self):
        assert build_generic_period_hint(None) == ""
