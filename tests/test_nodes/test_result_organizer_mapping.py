"""result_organizer 매핑 기반 충분성 검사 테스트."""

from __future__ import annotations

import pytest

from src.nodes.result_organizer import _check_data_sufficiency


class TestCheckDataSufficiencyWithMapping:
    """column_mapping 기반 충분성 검사 테스트."""

    @pytest.mark.asyncio
    async def test_sufficient_with_alias_keys(self, column_coverage_llm):
        """결과 키가 table.column 형식이면 충분."""
        results = [
            {
                "servers.hostname": "web-01",
                "servers.ip_address": "10.0.0.1",
                "cpu_metrics.usage_pct": 85.2,
            }
        ]
        mapping = {
            "서버명": "servers.hostname",
            "IP주소": "servers.ip_address",
            "CPU 사용률": "cpu_metrics.usage_pct",
        }

        assert await _check_data_sufficiency(
            results, {}, None, column_mapping=mapping, llm=column_coverage_llm,
        )
        if column_coverage_llm is not None:
            assert column_coverage_llm.calls[-1][2] == [
                "servers.hostname", "servers.ip_address", "cpu_metrics.usage_pct",
            ]

    @pytest.mark.asyncio
    async def test_sufficient_with_column_only_keys(self, column_coverage_llm):
        """결과 키가 column 형식만이어도 충분 (table.column -> column 폴백)."""
        results = [
            {
                "hostname": "web-01",
                "ip_address": "10.0.0.1",
            }
        ]
        mapping = {
            "서버명": "servers.hostname",
            "IP주소": "servers.ip_address",
        }

        assert await _check_data_sufficiency(
            results, {}, None, column_mapping=mapping, llm=column_coverage_llm,
        )
        # 결과 키가 bare 컬럼명이어도 table.column 매핑이 폴백 매칭된다 (스텁 모드 한정 단언)
        if column_coverage_llm is not None:
            assert column_coverage_llm.calls[-1][2] == ["servers.hostname", "servers.ip_address"]

    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        """매핑된 컬럼의 50% 미만이 결과에 있으면 부족."""
        results = [
            {"hostname": "web-01"}
        ]
        mapping = {
            "서버명": "servers.hostname",
            "IP주소": "servers.ip_address",
            "CPU": "cpu_metrics.usage_pct",
            "메모리": "memory_metrics.total_gb",
            "디스크": "disk_metrics.total_gb",
        }

        from unittest.mock import AsyncMock, MagicMock
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content='["servers.hostname"]')

        # 5개 중 1개만 매칭 = 20% < 50%
        assert not await _check_data_sufficiency(
            results, {}, None, column_mapping=mapping, llm=mock_llm
        )

    @pytest.mark.asyncio
    async def test_empty_results_is_sufficient(self):
        """결과가 0건이면 충분으로 판단 (빈 결과는 정상 응답)."""
        mapping = {"서버명": "servers.hostname"}
        assert await _check_data_sufficiency([], {}, None, column_mapping=mapping)

    @pytest.mark.asyncio
    async def test_no_mapping_uses_legacy(self):
        """column_mapping이 없으면 레거시 방식."""
        results = [{"a": 1}]
        template = {"sheets": [{"headers": ["x", "y", "z", "w", "v"]}]}

        # 1 col < 5 headers * 0.5 = 2.5 -> insufficient
        assert not await _check_data_sufficiency(results, {}, template, column_mapping=None)

    @pytest.mark.asyncio
    async def test_all_null_mappings(self):
        """모든 매핑이 None이면 충분으로 판단."""
        results = [{"a": 1}]
        mapping = {"비고": None, "메모": None}
        assert await _check_data_sufficiency(results, {}, None, column_mapping=mapping)
