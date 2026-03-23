"""LangGraph 그래프 구조 확장 검증 테스트.

노드 구성, 엣지 연결, 조건부 라우팅의 완전성을 검증한다.
spec의 아키텍처 요구사항과의 일치를 확인한다.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.graph import (
    _error_response_node,
    build_graph,
    route_after_execution,
    route_after_organization,
    route_after_validation,
)
from src.state import AgentState, create_initial_state


class TestGraphBuild:
    """그래프 빌드 검증."""

    def test_build_graph_succeeds(self, mock_config):
        """그래프를 성공적으로 빌드할 수 있다."""
        with patch("src.graph.create_llm") as mock_llm:
            mock_llm.return_value = MagicMock()
            graph = build_graph(mock_config)
        assert graph is not None

    def test_graph_has_all_nodes(self, mock_config):
        """그래프에 spec에 정의된 7개 노드 + error_response가 등록되어 있다."""
        with patch("src.graph.create_llm") as mock_llm:
            mock_llm.return_value = MagicMock()
            graph = build_graph(mock_config)

        # StateGraph의 노드는 graph.nodes에서 확인 가능
        expected_nodes = {
            "input_parser",
            "schema_analyzer",
            "query_generator",
            "query_validator",
            "query_executor",
            "result_organizer",
            "output_generator",
            "error_response",
        }
        # LangGraph compiled graph의 노드 확인
        graph_nodes = set(graph.nodes.keys()) - {"__start__", "__end__"}
        assert expected_nodes.issubset(graph_nodes), (
            f"누락된 노드: {expected_nodes - graph_nodes}"
        )


class TestConditionalEdgeCompleteness:
    """조건부 엣지 완전성 검증.

    spec 요구사항:
    1. query_validator 이후: 통과 -> executor, 실패+재시도 -> generator, 실패+초과 -> error
    2. query_executor 이후: 정상 -> organizer, 에러+재시도 -> generator, 에러+초과 -> error
    3. result_organizer 이후: 충분 -> output, 부족+재시도 -> generator, 부족+초과 -> output
    """

    # --- route_after_validation 경계값 테스트 ---

    def test_validation_retry_count_boundary_0(self):
        """retry_count=0에서 실패 시 재시도한다."""
        state = create_initial_state(user_query="test")
        state["validation_result"] = {"passed": False, "reason": "err", "auto_fixed_sql": None}
        state["retry_count"] = 0
        assert route_after_validation(state) == "query_generator"

    def test_validation_retry_count_boundary_2(self):
        """retry_count=2에서 실패 시 아직 재시도한다."""
        state = create_initial_state(user_query="test")
        state["validation_result"] = {"passed": False, "reason": "err", "auto_fixed_sql": None}
        state["retry_count"] = 2
        assert route_after_validation(state) == "query_generator"

    def test_validation_retry_count_boundary_3(self):
        """retry_count=3에서 실패 시 에러 응답으로 간다."""
        state = create_initial_state(user_query="test")
        state["validation_result"] = {"passed": False, "reason": "err", "auto_fixed_sql": None}
        state["retry_count"] = 3
        assert route_after_validation(state) == "error_response"

    def test_validation_retry_count_boundary_4(self):
        """retry_count=4에서 실패 시 에러 응답으로 간다."""
        state = create_initial_state(user_query="test")
        state["validation_result"] = {"passed": False, "reason": "err", "auto_fixed_sql": None}
        state["retry_count"] = 4
        assert route_after_validation(state) == "error_response"

    # --- route_after_execution 경계값 테스트 ---

    def test_execution_no_error(self):
        """에러 없이 실행 완료 시 result_organizer로 간다."""
        state = create_initial_state(user_query="test")
        state["error_message"] = None
        assert route_after_execution(state) == "result_organizer"

    def test_execution_empty_string_error(self):
        """빈 문자열 에러 메시지는 에러로 간주하지 않는다."""
        state = create_initial_state(user_query="test")
        state["error_message"] = ""
        # 빈 문자열은 falsy이므로 result_organizer로 간다
        assert route_after_execution(state) == "result_organizer"

    def test_execution_error_retry_boundary(self):
        """에러 + retry_count=2 시 재시도한다."""
        state = create_initial_state(user_query="test")
        state["error_message"] = "some error"
        state["retry_count"] = 2
        assert route_after_execution(state) == "query_generator"

    def test_execution_error_max_retry(self):
        """에러 + retry_count=3 시 에러 응답으로 간다."""
        state = create_initial_state(user_query="test")
        state["error_message"] = "some error"
        state["retry_count"] = 3
        assert route_after_execution(state) == "error_response"

    # --- route_after_organization 경계값 테스트 ---

    def test_organization_sufficient(self):
        """데이터 충분 시 output_generator로 간다."""
        state = create_initial_state(user_query="test")
        state["organized_data"]["is_sufficient"] = True
        state["retry_count"] = 0
        assert route_after_organization(state) == "output_generator"

    def test_organization_insufficient_retry_0(self):
        """데이터 부족 + retry_count=0 시 재시도한다."""
        state = create_initial_state(user_query="test")
        state["organized_data"]["is_sufficient"] = False
        state["retry_count"] = 0
        assert route_after_organization(state) == "query_generator"

    def test_organization_insufficient_retry_3(self):
        """데이터 부족 + retry_count=3 시 있는 데이터로 output_generator 진행."""
        state = create_initial_state(user_query="test")
        state["organized_data"]["is_sufficient"] = False
        state["retry_count"] = 3
        assert route_after_organization(state) == "output_generator"


class TestErrorResponseNode:
    """error_response 노드 상세 검증."""

    def test_error_message_included(self):
        """에러 메시지가 응답에 포함된다."""
        state = create_initial_state(user_query="test")
        state["error_message"] = "SQL 인젝션 패턴 감지"
        state["retry_count"] = 3
        result = _error_response_node(state)
        assert "SQL 인젝션 패턴 감지" in result["final_response"]

    def test_retry_count_included(self):
        """재시도 횟수가 응답에 포함된다."""
        state = create_initial_state(user_query="test")
        state["error_message"] = "test error"
        state["retry_count"] = 3
        result = _error_response_node(state)
        assert "3" in result["final_response"]

    def test_current_node_set(self):
        """current_node가 error_response로 설정된다."""
        state = create_initial_state(user_query="test")
        state["error_message"] = "test"
        state["retry_count"] = 3
        result = _error_response_node(state)
        assert result["current_node"] == "error_response"

    def test_none_error_message_handling(self):
        """error_message가 None일 때도 에러 없이 동작한다."""
        state = create_initial_state(user_query="test")
        state["error_message"] = None
        state["retry_count"] = 3
        result = _error_response_node(state)
        # 기본 메시지가 포함되어야 하나, 현재 구현은 "or" 연산자를 사용하여
        # None일 때 기본값을 사용. 하지만 실제로는 state["error_message"]가 None이면
        # "알 수 없는 에러" 메시지가 나와야 함
        assert "재시도 횟수가 최대" in result["final_response"]
