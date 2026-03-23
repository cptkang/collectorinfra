"""AgentState spec 준수 확장 검증.

spec.md와 requirements.md에 정의된 State 스키마와의 일치를 검증한다.
"""

import pytest

from src.state import AgentState, QueryAttempt, create_initial_state


class TestAgentStateSpecCompliance:
    """AgentState가 spec에 정의된 필드를 모두 포함하는지 검증."""

    def test_spec_original_fields_present(self):
        """spec.md 섹션 2.4에 정의된 원본 필드가 모두 존재한다."""
        state = create_initial_state(user_query="test")

        # spec 원본 필드
        spec_fields = [
            "user_query",
            "uploaded_file",
            "file_type",
            "parsed_requirements",
            "template_structure",
            "relevant_tables",
            "schema_info",
            "generated_sql",
            "query_results",
            "retry_count",
            "error_message",
            "final_response",
            "output_file",
        ]
        for field in spec_fields:
            assert field in state, f"spec 원본 필드 '{field}'가 AgentState에 없음"

    def test_requirements_additional_fields_present(self):
        """requirements.md 섹션 3.1에 추가된 필드가 존재한다."""
        state = create_initial_state(user_query="test")

        additional_fields = [
            "validation_result",
            "organized_data",
            "current_node",
            "output_file_name",
            "query_attempts",
        ]
        for field in additional_fields:
            assert field in state, f"추가 필드 '{field}'가 AgentState에 없음"

    def test_initial_retry_count_is_zero(self):
        """초기 retry_count가 0이다."""
        state = create_initial_state(user_query="test")
        assert state["retry_count"] == 0

    def test_query_attempts_initialized_empty(self):
        """query_attempts가 빈 리스트로 초기화된다."""
        state = create_initial_state(user_query="test")
        assert state["query_attempts"] == []


class TestQueryAttempt:
    """QueryAttempt TypedDict 검증."""

    def test_create_success_attempt(self):
        """성공 기록을 생성할 수 있다."""
        attempt = QueryAttempt(
            sql="SELECT 1",
            success=True,
            error=None,
            row_count=1,
            execution_time_ms=5.0,
        )
        assert attempt["success"] is True
        assert attempt["error"] is None

    def test_create_failure_attempt(self):
        """실패 기록을 생성할 수 있다."""
        attempt = QueryAttempt(
            sql="SELECT bad_col FROM servers",
            success=False,
            error="column not found",
            row_count=0,
            execution_time_ms=2.0,
        )
        assert attempt["success"] is False
        assert "column" in attempt["error"]


class TestCreateInitialStateVariations:
    """다양한 초기 상태 생성 검증."""

    def test_korean_query(self):
        """한국어 질의로 초기 상태를 생성한다."""
        state = create_initial_state(user_query="메모리 사용률이 80% 이상인 서버 목록")
        assert state["user_query"] == "메모리 사용률이 80% 이상인 서버 목록"

    def test_long_query(self):
        """긴 질의도 처리한다."""
        long_query = "서버 " * 500
        state = create_initial_state(user_query=long_query)
        assert state["user_query"] == long_query

    def test_file_upload_state(self):
        """파일 업로드 상태를 올바르게 초기화한다."""
        state = create_initial_state(
            user_query="양식 채우기",
            uploaded_file=b"PK\x03\x04",  # xlsx magic bytes
            file_type="xlsx",
        )
        assert state["uploaded_file"] == b"PK\x03\x04"
        assert state["file_type"] == "xlsx"
