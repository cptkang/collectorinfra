"""GENERIC_LLM_MAPPING 옵트인 — 무선언 DB 범용 기간 힌트 (Plan 63 P3, D-090).

선언 우선(폴스타 EX 동치): 폴스타는 프로필/어댑터 결정적 경로를 그대로 쓰고, 이 플래그는
프로필 없는 DB에만 범용 기간 힌트(폴스타 리터럴 없음)를 추가한다. 기본 OFF = 호출/주입 증가 0.
"""

import json
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.nodes.query_generator import query_generator
from src.state import create_initial_state

_SCHEMA = {
    "tables": json.loads(
        (Path(__file__).resolve().parents[2] / "testdata" / "generic_mon" / "schema.json").read_text()
    )["tables"]
}
POLESTAR_IDS = {"polestar_cm_gp"}


def _cfg(generic_on: bool) -> MagicMock:
    c = MagicMock()
    c.query.default_limit = 1000
    c.text2sql.semantic_compose = False
    c.text2sql.multi_candidate = False
    c.text2sql.generic_llm_mapping = generic_on
    c.synonym.value_retrieval = False
    c.get_polestar_db_ids.return_value = POLESTAR_IDS
    return c


async def _run(db_id: str, generic_on: bool) -> str:
    state = create_initial_state(user_query="지난달 CPU 사용률이 높은 서버")
    state["schema_info"] = _SCHEMA
    state["active_db_id"] = db_id
    state["active_db_engine"] = "postgresql"
    state["parsed_requirements"] = {
        "query_targets": ["CPU"], "filter_conditions": [],
        "original_query": "지난달 CPU 사용률이 높은 서버",
    }
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content="```sql\nSELECT 1 LIMIT 10;\n```")
    await query_generator(state, llm=llm, app_config=_cfg(generic_on))
    return llm.ainvoke.call_args[0][0][-1].content


class TestGenericPeriodHintOptIn:
    @pytest.mark.asyncio
    async def test_off_by_default_no_hint(self):
        """기본 OFF: 무선언 DB에 기간 힌트 미주입(현행 동작 무변경)."""
        prompt = await _run("generic_mon", generic_on=False)
        assert "기간 조건 해석" not in prompt

    @pytest.mark.asyncio
    async def test_on_injects_generic_hint_no_polestar_literal(self):
        """ON: 무선언 DB에 범용 기간 힌트 주입 — 폴스타 리터럴 없음."""
        prompt = await _run("generic_mon", generic_on=True)
        assert "기간 조건 해석" in prompt
        # 해석월(YYYYMM 런타임 값)은 힌트에 포함되나, 폴스타 스키마 리터럴은 없어야 한다.
        for lit in ("cmm_", "server.Server", "stat_date"):
            assert lit not in prompt, f"범용 힌트에 폴스타 리터럴 누수: {lit}"

    @pytest.mark.asyncio
    async def test_polestar_unchanged_uses_deterministic_block(self):
        """선언 우선: 폴스타는 플래그와 무관하게 결정적 통계 블록을 쓰고 범용 힌트를 안 쓴다."""
        prompt_on = await _run("polestar_cm_gp", generic_on=True)
        # 폴스타는 결정적 블록(build_stat_month_block) 사용, 범용 힌트 미사용
        assert "기간 조건 (시스템이 결정적" in prompt_on
        assert "기간 조건 해석" not in prompt_on
