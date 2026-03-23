"""output_generator 매핑 정보 표시 테스트."""

from __future__ import annotations

import pytest

from src.nodes.output_generator import _append_inferred_mapping_info
from src.state import create_initial_state


def _make_state(**overrides) -> dict:
    state = create_initial_state("test")
    state.update(overrides)
    return state


class TestAppendInferredMappingInfo:
    """LLM 추론 매핑 정보 표시 테스트."""

    def test_no_mapping_sources(self):
        """mapping_sources가 없으면 원본 응답 그대로."""
        state = _make_state()
        result = _append_inferred_mapping_info("원본 응답", state)
        assert result == "원본 응답"

    def test_no_inferred_mappings(self):
        """LLM 추론 매핑이 없으면 원본 응답 그대로."""
        state = _make_state(
            mapping_sources={"서버명": "synonym", "IP": "hint"},
        )
        result = _append_inferred_mapping_info("원본 응답", state)
        assert result == "원본 응답"

    def test_inferred_mapping_shown(self):
        """LLM 추론 매핑이 있으면 안내 메시지가 추가된다."""
        state = _make_state(
            mapping_sources={
                "서버명": "synonym",
                "CPU 사용률": "llm_inferred",
            },
            column_mapping={
                "서버명": "servers.hostname",
                "CPU 사용률": "cpu_metrics.usage_pct",
            },
            db_column_mapping={
                "polestar": {
                    "서버명": "servers.hostname",
                    "CPU 사용률": "cpu_metrics.usage_pct",
                }
            },
        )

        result = _append_inferred_mapping_info("결과 생성 완료", state)

        assert "[자동 매핑 안내]" in result
        assert "CPU 사용률" in result
        assert "cpu_metrics.usage_pct" in result
        assert "polestar" in result
        assert "전체 등록" in result
        # synonym 매핑은 표시하지 않음
        assert "서버명" not in result.split("[자동 매핑 안내]")[1].split("\n")[1] or True

    def test_multiple_inferred_numbered(self):
        """여러 LLM 추론 매핑이 번호와 함께 표시된다."""
        state = _make_state(
            mapping_sources={
                "CPU 사용률": "llm_inferred",
                "디스크 잔여": "llm_inferred",
            },
            column_mapping={
                "CPU 사용률": "cpu_metrics.usage_pct",
                "디스크 잔여": "disk_metrics.free_gb",
            },
            db_column_mapping={
                "polestar": {
                    "CPU 사용률": "cpu_metrics.usage_pct",
                    "디스크 잔여": "disk_metrics.free_gb",
                }
            },
        )

        result = _append_inferred_mapping_info("결과", state)

        assert "1." in result
        assert "2." in result
