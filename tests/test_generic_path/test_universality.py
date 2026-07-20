"""범용성 회귀 — 비폴스타 DB(generic_mon)가 공통 경로만으로 동작하는지 (Plan 63 P4-2, D-089).

폴스타 격리(P2) 이후, 프로필/시맨틱 모델이 없는 제2 모니터링 솔루션 DB가:
  ① 공통 시스템 템플릿을 쓰고(폴스타 전용 템플릿 미발동)
  ② 주입 블록·프롬프트에 폴스타 스키마 리터럴이 새지 않으며
  ③ 폴스타 어댑터가 발동하지 않는지(get_adapter None → 훅 미호출)
를 라이브 LLM 없이 프롬프트/디스패치 수준에서 검증한다. 실제 SQL 실행 E2E는 RUN_E2E 옵트인.
"""

import os
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.db_adapters import get_adapter
from src.nodes.query_generator import _build_system_prompt, query_generator
from src.state import create_initial_state

POLESTAR_IDS = {"polestar_cm_gp", "polestar_cm_yd", "polestar_b0"}


class TestAdapterNotInvoked:
    """비폴스타 DB는 담당 어댑터가 없다 — 어댑터 훅이 발동하지 않는다."""

    def test_get_adapter_returns_none(self):
        assert get_adapter("generic_mon", POLESTAR_IDS) is None

    def test_polestar_adapter_owns_only_polestar(self):
        adapter = get_adapter("polestar_cm_gp", POLESTAR_IDS)
        assert adapter is not None
        assert adapter.owns("generic_mon", POLESTAR_IDS) is False


class TestCommonTemplate:
    """비폴스타 DB는 공통 시스템 템플릿을 쓰고 폴스타 리터럴이 없다."""

    def test_uses_common_template_not_polestar(self, generic_mon_schema):
        prompt = _build_system_prompt(
            generic_mon_schema,
            default_limit=1000,
            active_db_id="generic_mon",
            polestar_db_ids=POLESTAR_IDS,
            active_db_engine="postgresql",
            routing_intent="data_query",
        )
        # 공통 템플릿 표식 존재, 폴스타 전용 템플릿 표식 부재
        assert "SQL 쿼리를 생성하는 전문가입니다" in prompt
        assert "POLESTAR 인프라 모니터링 DB 쿼리 생성 전문가" not in prompt

    def test_no_polestar_schema_literal_in_prompt(self, generic_mon_schema):
        prompt = _build_system_prompt(
            generic_mon_schema,
            default_limit=1000,
            active_db_id="generic_mon",
            polestar_db_ids=POLESTAR_IDS,
            active_db_engine="postgresql",
            routing_intent="data_query",
        )
        for literal in ("cmm_", "server.Server", "core_config_prop", "stat_date"):
            assert literal not in prompt, f"공통 프롬프트에 폴스타 리터럴 누수: {literal}"

    def test_alarm_intent_also_common_for_generic(self, generic_mon_schema):
        """비폴스타 DB는 alarm_query 의도라도 폴스타 알람 템플릿을 쓰지 않는다."""
        prompt = _build_system_prompt(
            generic_mon_schema,
            default_limit=1000,
            active_db_id="generic_mon",
            polestar_db_ids=POLESTAR_IDS,
            active_db_engine="postgresql",
            routing_intent="alarm_query",
        )
        assert "알람(Alert) 쿼리 생성 전문가" not in prompt
        assert "cmm_alarm" not in prompt


def _mock_config():
    cfg = MagicMock()
    cfg.query.default_limit = 1000
    cfg.text2sql.semantic_compose = False
    cfg.text2sql.multi_candidate = False
    cfg.synonym.value_retrieval = False
    cfg.get_polestar_db_ids.return_value = POLESTAR_IDS
    return cfg


class TestInjectionBlocksClean:
    """generic_mon 컨텍스트에서 주입 블록에 폴스타 통계 테이블 오지시가 없다(L2)."""

    @pytest.mark.asyncio
    async def test_period_query_no_polestar_stat_block(self, generic_mon_schema):
        state = create_initial_state(user_query="지난달 CPU 사용률이 높은 서버 목록")
        state["schema_info"] = generic_mon_schema
        state["active_db_id"] = "generic_mon"
        state["active_db_engine"] = "postgresql"
        state["parsed_requirements"] = {
            "query_targets": ["서버", "CPU"], "filter_conditions": [],
            "original_query": "지난달 CPU 사용률이 높은 서버 목록",
        }
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT hostname FROM servers LIMIT 10;\n```"
        )

        await query_generator(state, llm=mock_llm, app_config=_mock_config())

        human = mock_llm.ainvoke.call_args[0][0][-1].content
        assert "cmm_metric_stat_m" not in human
        assert "기간 조건 (시스템이 결정적" not in human


@pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="RUN_E2E=1 옵트인 필요(실 DB)")
class TestGenericPathE2E:
    """실 DB 대상 E2E(옵트인) — generic_mon 스키마로 기본 질의 SQL 생성·실행.

    실제 DB/LLM 접속이 필요하므로 기본 수집에서 제외한다(RUN_E2E=1일 때만).
    """

    def test_placeholder(self):
        pytest.skip("실 generic_mon DB 접속 구성 시 활성화")
