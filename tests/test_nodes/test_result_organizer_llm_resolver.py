"""result_organizer의 LLM 유사성 판단 (_resolve_unmatched_via_llm) 테스트.

LLM을 모킹하여 Layer 2 해석 로직을 검증한다.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.result_organizer import _resolve_unmatched_via_llm


@pytest.fixture()
def mock_llm() -> AsyncMock:
    """LLM 모킹 객체."""
    llm = AsyncMock()
    return llm


def _make_llm_response(content: str) -> MagicMock:
    """LLM 응답 객체를 생성한다."""
    resp = MagicMock()
    resp.content = content
    return resp


RESULT_KEYS = {
    "resource_id",
    "cmm_resource_hostname",
    "resource_desc",
    "os_type",
    "agent_ver",
    "serial_number",
}


class TestResolveUnmatchedViaLlm:
    """_resolve_unmatched_via_llm 단위 테스트."""

    @pytest.mark.asyncio
    async def test_abbreviation_alias(self, mock_llm: AsyncMock) -> None:
        """축약 alias 해석: description -> resource_desc, AgentVersion -> agent_ver."""
        column_mapping = {
            "업무내용": "cmm_resource.description",
            "에이전트버전": "EAV:AgentVersion",
        }
        unresolved_fields = ["업무내용", "에이전트버전"]

        # LLM이 매핑값 -> 결과 키 매핑을 반환
        llm_response = json.dumps({
            "cmm_resource.description": "resource_desc",
            "EAV:AgentVersion": "agent_ver",
        })
        mock_llm.ainvoke.return_value = _make_llm_response(llm_response)

        result = await _resolve_unmatched_via_llm(
            llm=mock_llm,
            column_mapping=column_mapping,
            unresolved_fields=unresolved_fields,
            result_keys=RESULT_KEYS,
        )

        assert result is not None
        assert result["업무내용"] == "resource_desc"
        assert result["에이전트버전"] == "agent_ver"

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, mock_llm: AsyncMock) -> None:
        """매칭 불가 시 None 반환."""
        column_mapping = {"필드A": "completely_unknown_column"}
        unresolved_fields = ["필드A"]

        # LLM이 빈 객체 반환 (매칭 불가)
        mock_llm.ainvoke.return_value = _make_llm_response("{}")

        result = await _resolve_unmatched_via_llm(
            llm=mock_llm,
            column_mapping=column_mapping,
            unresolved_fields=unresolved_fields,
            result_keys=RESULT_KEYS,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self, mock_llm: AsyncMock) -> None:
        """LLM 호출 실패 시 graceful하게 None 반환."""
        column_mapping = {"업무내용": "cmm_resource.description"}
        unresolved_fields = ["업무내용"]

        mock_llm.ainvoke.side_effect = Exception("LLM API error")

        result = await _resolve_unmatched_via_llm(
            llm=mock_llm,
            column_mapping=column_mapping,
            unresolved_fields=unresolved_fields,
            result_keys=RESULT_KEYS,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self, mock_llm: AsyncMock) -> None:
        """LLM이 잘못된 JSON을 반환해도 graceful 처리."""
        column_mapping = {"업무내용": "cmm_resource.description"}
        unresolved_fields = ["업무내용"]

        mock_llm.ainvoke.return_value = _make_llm_response("not valid json")

        result = await _resolve_unmatched_via_llm(
            llm=mock_llm,
            column_mapping=column_mapping,
            unresolved_fields=unresolved_fields,
            result_keys=RESULT_KEYS,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_returns_nonexistent_key_ignored(
        self, mock_llm: AsyncMock,
    ) -> None:
        """LLM이 result_keys에 없는 키를 반환하면 무시."""
        column_mapping = {"업무내용": "cmm_resource.description"}
        unresolved_fields = ["업무내용"]

        # LLM이 존재하지 않는 키를 매핑
        llm_response = json.dumps({
            "cmm_resource.description": "nonexistent_key",
        })
        mock_llm.ainvoke.return_value = _make_llm_response(llm_response)

        result = await _resolve_unmatched_via_llm(
            llm=mock_llm,
            column_mapping=column_mapping,
            unresolved_fields=unresolved_fields,
            result_keys=RESULT_KEYS,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_json_in_code_block(self, mock_llm: AsyncMock) -> None:
        """LLM이 ```json 코드 블록으로 감싼 경우에도 파싱 성공."""
        column_mapping = {"업무내용": "cmm_resource.description"}
        unresolved_fields = ["업무내용"]

        response_text = '```json\n{"cmm_resource.description": "resource_desc"}\n```'
        mock_llm.ainvoke.return_value = _make_llm_response(response_text)

        result = await _resolve_unmatched_via_llm(
            llm=mock_llm,
            column_mapping=column_mapping,
            unresolved_fields=unresolved_fields,
            result_keys=RESULT_KEYS,
        )

        assert result is not None
        assert result["업무내용"] == "resource_desc"

    @pytest.mark.asyncio
    async def test_empty_unresolved_columns(self, mock_llm: AsyncMock) -> None:
        """미해결 필드의 매핑값이 모두 None이면 LLM 호출 없이 None 반환."""
        column_mapping = {"필드A": None}
        unresolved_fields = ["필드A"]

        result = await _resolve_unmatched_via_llm(
            llm=mock_llm,
            column_mapping=column_mapping,
            unresolved_fields=unresolved_fields,
            result_keys=RESULT_KEYS,
        )

        assert result is None
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_llm_provided(self) -> None:
        """LLM이 None이고 config도 없으면 graceful 스킵."""
        column_mapping = {"업무내용": "cmm_resource.description"}
        unresolved_fields = ["업무내용"]

        # LLM=None, app_config=None -> create_llm 실패
        with patch("src.nodes.result_organizer.load_config", side_effect=Exception("no config")):
            result = await _resolve_unmatched_via_llm(
                llm=None,
                column_mapping=column_mapping,
                unresolved_fields=unresolved_fields,
                result_keys=RESULT_KEYS,
            )

        assert result is None
