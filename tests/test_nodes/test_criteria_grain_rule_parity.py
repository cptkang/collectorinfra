"""기준 칼럼 노출·집계 단위 규칙(C-04·C-07·C-11) — 단일/멀티 경로 대칭 주입 검증.

Known Mistakes 원칙: 프롬프트 블록은 단일 DB·멀티 DB 경로 **양쪽에 실제 주입됐는지
실측**한다(한쪽만 고치는 비대칭이 반복 원인). 블록 정본은
`src/nodes/prompt_blocks.CRITERIA_AND_GRAIN_RULE_BLOCK` 한 곳이다(사본 금지).
"""

from src.nodes.prompt_blocks import CRITERIA_AND_GRAIN_RULE_BLOCK


class TestCriteriaGrainRuleBlock:
    """블록 자체의 핵심 문구 고정 — 의도치 않은 문구 드리프트 감시."""

    def test_block_contains_projection_rule(self):
        assert "WHERE/HAVING/ORDER BY" in CRITERIA_AND_GRAIN_RULE_BLOCK
        assert "SELECT에도 별칭으로" in CRITERIA_AND_GRAIN_RULE_BLOCK
        assert "비교 기준값" in CRITERIA_AND_GRAIN_RULE_BLOCK

    def test_block_contains_grain_rule(self):
        assert "서버(자원)별 집계를 기본" in CRITERIA_AND_GRAIN_RULE_BLOCK
        assert "전역 집계를 명시한 경우에만" in CRITERIA_AND_GRAIN_RULE_BLOCK

    def test_block_forbids_pct_alias_on_count_columns(self):
        """C-10 재실측 보강: COUNT 별칭의 pct 표기 금지(응답 % 오부착의 SQL측 근본)."""
        assert "months_over_40pct 금지" in CRITERIA_AND_GRAIN_RULE_BLOCK
        # query_generator가 template.format() 인자로 넣으므로 %는 이스케이프 불요 —
        # 실수로 %%가 들어오면 프롬프트에 그대로 노출된다.
        assert "%%" not in CRITERIA_AND_GRAIN_RULE_BLOCK


class TestSinglePathInjection:
    """단일 DB 경로(query_generator._build_system_prompt)에 블록이 실린다."""

    def test_system_prompt_contains_rule_block(self):
        from src.nodes.query_generator import _build_system_prompt

        prompt = _build_system_prompt(
            schema_info={"tables": {}},
            default_limit=1000,
        )
        assert CRITERIA_AND_GRAIN_RULE_BLOCK in prompt

    def test_rule_block_present_with_db2_engine(self):
        """엔진 분기(DB2)에서도 블록이 탈락하지 않는다."""
        from src.nodes.query_generator import _build_system_prompt

        prompt = _build_system_prompt(
            schema_info={"tables": {}},
            default_limit=1000,
            active_db_engine="db2",
        )
        assert CRITERIA_AND_GRAIN_RULE_BLOCK in prompt


class TestMultiPathInjection:
    """멀티 DB 경로(multi_db_executor._build_multi_engine_hint)에 같은 블록이 실린다."""

    def test_engine_hint_contains_rule_block(self):
        from src.nodes.multi_db_executor import _build_multi_engine_hint

        hint = _build_multi_engine_hint("postgresql", "polestar_cm_gp")
        assert CRITERIA_AND_GRAIN_RULE_BLOCK in hint

    def test_rule_block_present_with_db2_engine(self):
        from src.nodes.multi_db_executor import _build_multi_engine_hint

        hint = _build_multi_engine_hint("db2", "polestar_b0")
        assert CRITERIA_AND_GRAIN_RULE_BLOCK in hint
