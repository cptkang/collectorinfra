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
