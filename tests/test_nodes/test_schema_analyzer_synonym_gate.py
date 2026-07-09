"""allowed_tables 유사어 동적 보완 게이트 검증 (D-051, Plan 57 §4.★).

`_synonym_tables_matching_query()`가 등록된 컬럼 유사어 중 **이번 질의 용어와
매칭된 것의 테이블만** 추리는지 검증한다. 게이트 부재 시 누적 유사어 전 테이블이
allowed_tables(relevant)로 유입되어 프롬프트 토큰이 폭증했다(실측: b0 _allowed 5→407,
system_prompt 104K > FabriX 한도 95K).
"""

import pytest

from src.nodes.schema_analyzer import (
    _MAX_SYNONYM_SUPPLEMENT_TABLES,
    _synonym_tables_matching_query,
)


def _wide_synonym_dict(n_tables: int) -> dict[str, list[str]]:
    """n_tables개 테이블에 걸친 유사어 사전을 생성한다(전 테이블 누적 상황 모사)."""
    return {f"mon_hw_{i:06d}.col": [f"별칭{i}"] for i in range(n_tables)}


class TestSynonymGate:
    def test_empty_match_text_returns_empty(self):
        """질의 매칭 텍스트가 비면 어떤 테이블도 보완하지 않는다."""
        syns = {"cmm_resource.hostname": ["서버명", "호스트명"]}
        assert _synonym_tables_matching_query(syns, "") == set()

    def test_only_matched_tables_added(self):
        """질의 용어와 매칭된 유사어의 테이블만 추가된다 (무관 테이블 제외)."""
        syns = {
            "cmm_resource.cpu": ["씨피유", "CPU"],
            "cmm_resource.mem": ["메모리"],
            "unrelated_table.x": ["회계전표"],
        }
        match = "은행 폴스타 ### 서버의 cpu 및 메모리 용량".lower()
        result = _synonym_tables_matching_query(syns, match)
        assert "cmm_resource" in result
        assert "unrelated_table" not in result

    def test_wide_dict_does_not_explode(self):
        """수백 테이블 유사어가 등록돼도, 질의 무관분은 유입되지 않는다(폭증 회귀 차단)."""
        syns = _wide_synonym_dict(400)
        # 질의에 어떤 별칭도 등장하지 않음 → 한 테이블도 보완되면 안 됨
        result = _synonym_tables_matching_query(syns, "cpu 메모리 조회")
        assert result == set()

    def test_cap_enforced(self):
        """매칭 테이블이 상한을 넘으면 cap개까지만 보완한다."""
        # 모든 테이블이 동일 별칭 '코어'를 가지며 질의에 '코어'가 등장 → 전부 매칭 후보
        syns = {f"t{i}.c": ["코어"] for i in range(100)}
        result = _synonym_tables_matching_query(syns, "코어 수 조회", cap=5)
        assert len(result) == 5

    def test_empty_synonym_string_not_match_all(self):
        """빈 문자열 유사어는 항상 매칭(=in)되는 함정이므로 제외된다."""
        syns = {"trap_table.c": [""], "cmm_resource.cpu": ["씨피유"]}
        result = _synonym_tables_matching_query(syns, "씨피유 조회")
        assert "trap_table" not in result
        assert "cmm_resource" in result

    def test_single_char_synonym_excluded(self):
        """1글자 유사어는 오매칭 위험이 커 제외한다(길이 2 이상만 인정)."""
        syns = {"noise_table.c": ["a"]}
        # 'a'는 영어 텍스트 어디서나 매칭되기 쉬움 → 제외되어야 함
        result = _synonym_tables_matching_query(syns, "data query about a server")
        assert "noise_table" not in result

    def test_col_key_without_dot_skipped(self):
        """'table.column' 형식이 아닌 키는 무시한다."""
        syns = {"no_dot_key": ["메모리"]}
        assert _synonym_tables_matching_query(syns, "메모리 조회") == set()

    def test_default_cap_constant(self):
        """기본 상한 상수가 합리적 범위(폭증 방지)인지 회귀 고정."""
        assert _MAX_SYNONYM_SUPPLEMENT_TABLES <= 50
