"""데이터 충분성 검사 로직 단위 테스트.

_check_data_sufficiency가 LLM 기반으로 변경됨에 따라,
LLM 응답을 모킹하여 충분성 판단 로직을 검증한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import AppConfig, QueryConfig
from src.nodes.result_organizer import _check_data_sufficiency


# ---------------------------------------------------------------------------
# 테스트용 헬퍼
# ---------------------------------------------------------------------------

def _make_config(
    required_threshold: float = 0.7,
) -> AppConfig:
    """테스트용 AppConfig를 생성한다."""
    config = AppConfig()
    config.query = QueryConfig(
        sufficiency_required_threshold=required_threshold,
    )
    return config


def _make_llm(matched_cols: list[str]) -> MagicMock:
    """LLM 모킹 객체를 생성한다. matched_cols를 JSON 배열로 응답한다."""
    import json
    mock_llm = MagicMock()
    response = MagicMock()
    response.content = f"```json\n{json.dumps(matched_cols, ensure_ascii=False)}\n```"
    mock_llm.ainvoke = AsyncMock(return_value=response)
    return mock_llm


# ===========================================================================
# _check_data_sufficiency 단위 테스트
# ===========================================================================

class TestCheckDataSufficiency:
    """_check_data_sufficiency 함수 테스트 (LLM 기반)."""

    def _make_results(self, keys: list[str]) -> list[dict]:
        """주어진 키 목록으로 결과 행 1건을 생성한다."""
        return [{k: "dummy" for k in keys}]

    # --- 빈 결과 ---

    @pytest.mark.asyncio
    async def test_empty_results_normal_query(self):
        """빈 결과 + 일반 쿼리 -> True."""
        assert await _check_data_sufficiency(
            [], {"query_targets": ["서버"]}, None,
        ) is True

    @pytest.mark.asyncio
    async def test_empty_results_aggregation_query(self):
        """빈 결과 + 집계 쿼리 -> False."""
        assert await _check_data_sufficiency(
            [], {"aggregation": "top_n"}, None,
        ) is False

    # --- column_mapping + LLM 매칭 ---

    @pytest.mark.asyncio
    async def test_all_columns_matched(self):
        """LLM이 모든 매핑 컬럼 매칭 -> True."""
        results = self._make_results(["hostname", "ip_address", "os_type"])
        column_mapping = {
            "서버명": "servers.hostname",
            "IP": "servers.ip_address",
            "OS": "servers.os_type",
        }
        llm = _make_llm(["servers.hostname", "servers.ip_address", "servers.os_type"])
        config = _make_config()

        assert await _check_data_sufficiency(
            results, {}, None,
            column_mapping=column_mapping,
            llm=llm,
            app_config=config,
        ) is True

    @pytest.mark.asyncio
    async def test_above_threshold(self):
        """LLM이 70% 이상 매칭 -> True."""
        results = self._make_results([f"col{i}" for i in range(7)])
        column_mapping = {f"field{i}": f"t.col{i}" for i in range(10)}
        # 10개 중 7개 매칭 = 70%
        llm = _make_llm([f"t.col{i}" for i in range(7)])
        config = _make_config()

        assert await _check_data_sufficiency(
            results, {}, None,
            column_mapping=column_mapping,
            llm=llm,
            app_config=config,
        ) is True

    @pytest.mark.asyncio
    async def test_below_threshold(self):
        """LLM이 70% 미만 매칭 -> False."""
        results = self._make_results([f"col{i}" for i in range(6)])
        column_mapping = {f"field{i}": f"t.col{i}" for i in range(10)}
        # 10개 중 6개 매칭 = 60%
        llm = _make_llm([f"t.col{i}" for i in range(6)])
        config = _make_config()

        assert await _check_data_sufficiency(
            results, {}, None,
            column_mapping=column_mapping,
            llm=llm,
            app_config=config,
        ) is False

    @pytest.mark.asyncio
    async def test_all_none_column_mapping(self):
        """column_mapping 전부 None -> True (LLM 호출 없음)."""
        results = self._make_results(["hostname"])
        column_mapping = {"비고": None, "담당자": None}
        config = _make_config()

        assert await _check_data_sufficiency(
            results, {}, None,
            column_mapping=column_mapping,
            app_config=config,
        ) is True

    @pytest.mark.asyncio
    async def test_llm_failure_returns_sufficient(self):
        """LLM 호출 실패 시 충분으로 간주 (graceful fallback)."""
        results = self._make_results(["hostname"])
        column_mapping = {"서버명": "servers.hostname"}
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM error"))
        config = _make_config()

        assert await _check_data_sufficiency(
            results, {}, None,
            column_mapping=column_mapping,
            llm=mock_llm,
            app_config=config,
        ) is True

    # --- text 모드 ---

    @pytest.mark.asyncio
    async def test_text_mode_with_results(self):
        """text 모드 (column_mapping 없음) + 결과 컬럼 1개 이상 -> True."""
        results = self._make_results(["hostname", "ip"])
        assert await _check_data_sufficiency(results, {}, None) is True

    @pytest.mark.asyncio
    async def test_text_mode_empty_keys(self):
        """text 모드 + 결과 컬럼 0개 -> False."""
        results = [{}]
        assert await _check_data_sufficiency(results, {}, None) is False


# ===========================================================================
# 레거시 template 기반 테스트 (Case 3 유지 확인)
# ===========================================================================

class TestLegacyTemplateSufficiency:
    """레거시 template 기반 충분성 검사가 유지되는지 확인한다."""

    @pytest.mark.asyncio
    async def test_legacy_template_headers_sufficient(self):
        """template headers 대비 결과 컬럼이 50% 이상이면 True."""
        results = [{"hostname": "srv1", "ip": "1.1.1.1", "os": "linux"}]
        template = {"sheets": [{"headers": ["hostname", "ip", "os", "location"]}]}
        assert await _check_data_sufficiency(results, {}, template) is True

    @pytest.mark.asyncio
    async def test_legacy_template_headers_insufficient(self):
        """template headers 대비 결과 컬럼이 50% 미만이면 False."""
        results = [{"hostname": "srv1"}]
        template = {"sheets": [{"headers": ["hostname", "ip", "os", "location", "rack"]}]}
        assert await _check_data_sufficiency(results, {}, template) is False
