"""AgentState 초기화 및 구조 검증 테스트."""

import pytest

from src.state import AgentState, OrganizedData, ValidationResult, create_initial_state


class TestCreateInitialState:
    """create_initial_state 함수 검증."""

    def test_basic_text_query(self):
        """기본 텍스트 질의로 초기 State를 생성한다."""
        state = create_initial_state(user_query="서버 목록 조회")

        assert state["user_query"] == "서버 목록 조회"
        assert state["uploaded_file"] is None
        assert state["file_type"] is None

    def test_initial_state_has_all_required_fields(self):
        """초기 State가 spec에 정의된 모든 필드를 포함한다."""
        state = create_initial_state(user_query="test")

        required_fields = [
            "user_query", "uploaded_file", "file_type",
            "parsed_requirements", "template_structure",
            "relevant_tables", "schema_info", "generated_sql",
            "validation_result", "query_results",
            "organized_data",
            "retry_count", "error_message", "current_node",
            "final_response", "output_file", "output_file_name",
        ]
        for field in required_fields:
            assert field in state, f"필드 '{field}'가 AgentState에 없음"

    def test_initial_default_values(self):
        """초기 State의 기본값이 올바르다."""
        state = create_initial_state(user_query="test")

        assert state["parsed_requirements"] == {}
        assert state["template_structure"] is None
        assert state["relevant_tables"] == []
        assert state["schema_info"] == {}
        assert state["generated_sql"] == ""
        assert state["validation_result"]["passed"] is False
        assert state["query_results"] == []
        assert state["retry_count"] == 0
        assert state["error_message"] is None
        assert state["final_response"] == ""
        assert state["output_file"] is None
        assert state["output_file_name"] is None

    def test_with_uploaded_file(self):
        """파일 업로드 시 State가 올바르게 초기화된다."""
        file_data = b"fake-excel-content"
        state = create_initial_state(
            user_query="양식 채우기",
            uploaded_file=file_data,
            file_type="xlsx",
        )

        assert state["uploaded_file"] == file_data
        assert state["file_type"] == "xlsx"

    def test_organized_data_structure(self):
        """OrganizedData의 초기 구조가 올바르다."""
        state = create_initial_state(user_query="test")
        organized = state["organized_data"]

        assert organized["summary"] == ""
        assert organized["rows"] == []
        assert organized["column_mapping"] is None
        assert organized["is_sufficient"] is False


class TestValidationResult:
    """ValidationResult TypedDict 검증."""

    def test_validation_result_creation(self):
        """ValidationResult를 올바르게 생성할 수 있다."""
        result: ValidationResult = {
            "passed": True,
            "reason": "검증 통과",
            "auto_fixed_sql": None,
        }
        assert result["passed"] is True
        assert result["reason"] == "검증 통과"

    def test_validation_failure_with_auto_fix(self):
        """자동 보정 SQL이 포함된 ValidationResult를 생성할 수 있다."""
        result: ValidationResult = {
            "passed": True,
            "reason": "LIMIT 자동 추가",
            "auto_fixed_sql": "SELECT * FROM servers LIMIT 1000",
        }
        assert result["auto_fixed_sql"] is not None


class TestOrganizedData:
    """OrganizedData TypedDict 검증."""

    def test_organized_data_with_rows(self):
        """데이터가 있는 OrganizedData를 생성할 수 있다."""
        data: OrganizedData = {
            "summary": "3건 조회",
            "rows": [{"hostname": "web-01"}],
            "column_mapping": {"서버명": "hostname"},
            "is_sufficient": True,
        }
        assert data["is_sufficient"] is True
        assert len(data["rows"]) == 1
