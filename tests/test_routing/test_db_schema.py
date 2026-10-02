"""D-057: DB 스키마 한정 헬퍼 및 SQL 생성 경로 스키마 규칙 주입 테스트."""

from __future__ import annotations

import pytest

from src.routing.db_schema import get_schema_prefix, qualify_table


class TestSchemaPrefix:
    def test_b0_db2_uses_polestar_schema(self):
        # 2026-07-02 실측: b0 테이블은 POLESTAR 스키마(연결 CURRENT SCHEMA=SDQ000과 다름).
        assert get_schema_prefix("polestar_b0") == "POLESTAR."
        assert qualify_table("polestar_b0", "cmm_resource") == "POLESTAR.cmm_resource"

    def test_postgresql_polestar_dbs(self):
        assert get_schema_prefix("polestar_cm_gp") == "polestar."
        assert get_schema_prefix("polestar_cm_yd") == "polestar."
        assert qualify_table("polestar_cm_yd", "cmm_resource") == "polestar.cmm_resource"

    def test_unknown_or_unset_db_is_unqualified(self):
        # db_schema 미설정 DB(cloud_portal 등)와 미등록 db_id는 무스키마.
        assert get_schema_prefix("cloud_portal") == ""
        assert get_schema_prefix("does_not_exist") == ""
        assert qualify_table("does_not_exist", "t") == "t"


class _RecordingLLM:
    """system prompt를 기록하는 최소 LLM 스텁 (KBGenAIChat 아님)."""

    def __init__(self):
        self.system_prompt = ""

    async def ainvoke(self, messages):
        self.system_prompt = messages[0].content

        class _R:
            content = "```sql\nSELECT 1 FROM POLESTAR.cmm_resource FETCH FIRST 1 ROWS ONLY\n```"

        return _R()


@pytest.mark.asyncio
async def test_generate_sql_injects_schema_rule_per_db():
    from src.nodes.multi_db_executor import _generate_sql

    schema_info = {"tables": {"cmm_resource": {"columns": [{"name": "id", "type": "int"}]}}}

    # b0(DB2) → POLESTAR. 한정 규칙 + FETCH FIRST 방언
    llm_b0 = _RecordingLLM()
    await _generate_sql(
        llm_b0, {}, schema_info, "서버 조회", 100,
        db_engine="db2", db_id="polestar_b0",
    )
    assert "POLESTAR." in llm_b0.system_prompt
    assert "FETCH FIRST" in llm_b0.system_prompt

    # PostgreSQL 공동존 → polestar. 한정 규칙
    llm_yd = _RecordingLLM()
    await _generate_sql(
        llm_yd, {}, schema_info, "서버 조회", 100,
        db_engine="postgresql", db_id="polestar_cm_yd",
    )
    assert "polestar." in llm_yd.system_prompt

    # 스키마 미설정 → 무스키마 규칙
    llm_x = _RecordingLLM()
    await _generate_sql(
        llm_x, {}, schema_info, "조회", 100,
        db_engine="postgresql", db_id="cloud_portal",
    )
    assert "무스키마" in llm_x.system_prompt
