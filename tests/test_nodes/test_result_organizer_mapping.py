"""result_organizer 매핑 기반 충분성 검사 테스트."""

from __future__ import annotations

import pytest

from src.nodes.result_organizer import _check_data_sufficiency


class TestCheckDataSufficiencyWithMapping:
    """column_mapping 기반 충분성 검사 테스트."""

    def test_sufficient_with_alias_keys(self):
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

        assert _check_data_sufficiency(results, {}, None, column_mapping=mapping)

    def test_sufficient_with_column_only_keys(self):
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

        assert _check_data_sufficiency(results, {}, None, column_mapping=mapping)

    def test_insufficient_data(self):
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

        # 5개 중 1개만 매칭 = 20% < 50%
        assert not _check_data_sufficiency(results, {}, None, column_mapping=mapping)

    def test_empty_results_is_sufficient(self):
        """결과가 0건이면 충분으로 판단 (빈 결과는 정상 응답)."""
        mapping = {"서버명": "servers.hostname"}
        assert _check_data_sufficiency([], {}, None, column_mapping=mapping)

    def test_no_mapping_uses_legacy(self):
        """column_mapping이 없으면 레거시 방식."""
        results = [{"a": 1}]
        template = {"sheets": [{"headers": ["x", "y", "z", "w", "v"]}]}

        # 1 col < 5 headers * 0.5 = 2.5 -> insufficient
        assert not _check_data_sufficiency(results, {}, template, column_mapping=None)

    def test_all_null_mappings(self):
        """모든 매핑이 None이면 충분으로 판단."""
        results = [{"a": 1}]
        mapping = {"비고": None, "메모": None}
        assert _check_data_sufficiency(results, {}, None, column_mapping=mapping)
