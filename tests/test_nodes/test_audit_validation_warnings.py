"""검증 경고의 감사 로그 전달 회귀 테스트 (Plan 69 P0-④).

validator가 만든 경고가 reason 문자열에만 접혀 들어가고 구조화 필드로는
노출되지 않아, log_query_execution의 validation_warnings 인자를 어느 호출자도
전달하지 못하던 결함(감사 로그에 경고 유실)의 재발 방지.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.nodes.query_executor import query_executor
from src.nodes.query_validator import query_validator
from src.state import create_initial_state


class TestValidatorExposesWarnings:
    @pytest.mark.asyncio
    async def test_validation_result_has_structured_warnings(
        self, sample_schema_info
    ):
        """LIMIT 자동 보정 경고가 validation_result['warnings']로 구조화 노출된다."""
        state = create_initial_state(user_query="test")
        state["schema_info"] = sample_schema_info
        state["generated_sql"] = "SELECT hostname FROM servers"  # LIMIT 없음 → 자동 보정 + 경고

        result = await query_validator(state)

        assert result["validation_result"]["passed"] is True
        warnings = result["validation_result"].get("warnings")
        assert isinstance(warnings, list) and warnings, "경고 리스트가 구조화 노출되어야 한다"
        assert any("LIMIT" in w for w in warnings)


class TestExecutorForwardsWarningsToAudit:
    @pytest.mark.asyncio
    async def test_audit_receives_validation_warnings(self, sample_schema_info):
        """query_executor의 감사 호출이 검증 경고를 validation_warnings로 전달한다."""
        state = create_initial_state(user_query="test")
        state["schema_info"] = sample_schema_info
        state["generated_sql"] = "SELECT hostname FROM servers LIMIT 100;"
        state["validation_result"] = {
            "passed": True,
            "reason": "검증 통과. 경고: w1",
            "warnings": ["w1"],
        }

        mock_client = AsyncMock()
        mock_result = AsyncMock()
        mock_result.rows = [{"hostname": "web-01"}]
        mock_result.row_count = 1
        mock_client.execute_sql = AsyncMock(return_value=mock_result)

        class _Ctx:
            async def __aenter__(self):
                return mock_client

            async def __aexit__(self, *args):
                return False

        audit_mock = AsyncMock()
        with patch("src.nodes.query_executor.get_db_client", return_value=_Ctx()):
            with patch("src.nodes.query_executor.log_query_execution", audit_mock):
                await query_executor(state)

        assert audit_mock.await_count == 1
        assert audit_mock.await_args.kwargs.get("validation_warnings") == ["w1"]
