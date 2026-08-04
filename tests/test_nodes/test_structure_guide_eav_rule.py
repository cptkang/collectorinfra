"""_format_structure_guide EAV 조인 규칙 렌더 회귀 테스트 (Plan 69 P0-③).

query_guide가 빈 문자열인 프로필에서 `if eav_patterns and guide:` 조건 때문에
EAV 조인 금지 규칙이 통째로 빠지고, 유사어·value_joins만 부분 렌더되던 결함의
재발 방지.
"""

from src.nodes.query_generator import _format_structure_guide

_EAV_META = {
    "query_guide": "",
    "patterns": [
        {
            "type": "eav",
            "entity_table": "ent_t",
            "config_table": "cfg_t",
            "attribute_column": "NAME",
            "value_joins": [],
        }
    ],
}


class TestEavJoinRuleRendering:
    def test_empty_guide_still_renders_eav_join_rule(self):
        """query_guide가 비어도 EAV 패턴이 있으면 조인 금지 규칙은 렌더된다."""
        out = _format_structure_guide(_EAV_META)
        assert "EAV 테이블 조인 규칙" in out
        assert "id 컬럼으로 직접 조인하지 마세요" in out

    def test_nonempty_guide_bytes_unchanged(self):
        """guide가 있는 기존 경로의 출력 바이트는 불변이다(규칙 + guide 연접)."""
        meta = {**_EAV_META, "query_guide": "기존 가이드 본문"}
        out = _format_structure_guide(meta)
        assert out.startswith("## EAV 테이블 조인 규칙\n")
        assert "그대로 사용하세요.\n\n기존 가이드 본문" in out

    def test_no_eav_pattern_no_rule(self):
        """EAV 패턴이 없으면 규칙을 삽입하지 않는다(기존 동작 보존)."""
        out = _format_structure_guide({"query_guide": "본문", "patterns": []})
        assert "EAV 테이블 조인 규칙" not in out


class TestEavJoinRuleMultiPathSymmetry:
    """멀티 경로의 EAV 조인 규칙 렌더 대칭 (Plan 69 W-8 — P0-③의 멀티 완결).

    P0-③이 단일 경로만 고쳐, 멀티는 query_guide 빈 프로필에서 조인 금지 규칙이
    통째로 빠지는 동일 결함이 잔존했다(문구 diff 리포트 실측). 폴스타 guide는
    비어 있지 않아 이 수정의 폴스타 경로 바이트는 불변이다.
    """

    def test_multi_path_renders_rule_with_empty_guide(self):
        import re

        from src.nodes import multi_db_executor as mdb

        src = open(mdb.__file__, encoding="utf-8").read()
        # 조건이 guide 존재에 묶여 있지 않음을 소스 레벨로 고정(빌드 함수가 노드 내부라
        # 전체 노드 목 없이 렌더를 떼기 어려움 — 조건식 자체를 단언)
        assert re.search(r"if eav_patterns:\n\s+structure_guide = EAV_JOIN_RULE_BLOCK", src), \
            "멀티 경로 EAV 규칙이 guide 존재에 게이트되면 안 된다(W-8)"
        assert "if eav_patterns and structure_guide:" not in src
