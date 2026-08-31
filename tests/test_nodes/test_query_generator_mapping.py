"""query_generator column_mapping 기반 프롬프트 테스트."""

from __future__ import annotations

import pytest

from src.nodes.query_generator import _build_user_prompt


class TestBuildUserPromptWithMapping:
    """column_mapping 기반 프롬프트 생성 테스트."""

    def test_column_mapping_included(self):
        """column_mapping이 제공되면 매핑 지시가 포함된다."""
        result = _build_user_prompt(
            parsed_requirements={"original_query": "서버 정보 조회"},
            template_structure={"file_type": "xlsx"},
            error_message=None,
            previous_sql=None,
            column_mapping={
                "서버명": "servers.hostname",
                "IP주소": "servers.ip_address",
                "비고": None,
            },
        )

        assert "양식-DB 매핑" in result
        assert "servers.hostname" in result
        assert "servers.ip_address" in result
        assert 'AS "servers.hostname"' in result or "alias" in result.lower()

    def test_no_column_mapping_uses_template(self):
        """column_mapping이 없으면 기존 template_structure 방식."""
        result = _build_user_prompt(
            parsed_requirements={"original_query": "서버 정보 조회"},
            template_structure={"file_type": "xlsx", "sheets": [{"headers": ["서버명"]}]},
            error_message=None,
            previous_sql=None,
            column_mapping=None,
        )

        assert "양식 구조" in result
        assert "양식-DB 매핑" not in result

    def test_both_none(self):
        """template_structure와 column_mapping 모두 없으면 둘 다 없음."""
        result = _build_user_prompt(
            parsed_requirements={"original_query": "서버 정보 조회"},
            template_structure=None,
            error_message=None,
            previous_sql=None,
            column_mapping=None,
        )

        assert "양식" not in result

    def test_null_mappings_surface_as_unmapped_guidance(self):
        """★ None 매핑은 **매핑 지시에서는 빠지되 "미매핑 필드"로 노출**된다.

        종전 계약("프롬프트에서 제외")은 뒤집혔다 — 조용히 빼면 LLM이 그 필드를 SELECT에
        넣지 않아 양식 칸이 침묵 공란으로 남는다(D-066 후속4·D-147). 지금은 미매핑 필드를
        "헤더명 그대로 alias" 지시와 함께 싣는다. 이 테스트는 그 반전을 못박는다.
        """
        result = _build_user_prompt(
            parsed_requirements={"original_query": "서버 정보 조회"},
            template_structure=None,
            error_message=None,
            previous_sql=None,
            column_mapping={
                "서버명": "servers.hostname",
                "비고": None,
            },
        )

        assert "servers.hostname" in result
        # 미매핑 필드는 노출되지만 **가짜 컬럼으로 매핑되지는 않는다**
        assert "비고" in result
        assert "비고: " not in result and '"비고" ->' not in result
