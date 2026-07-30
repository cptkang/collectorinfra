"""재시도 예산의 설정 소비 회귀 테스트 (Plan 69 P0-⑧).

QueryConfig.max_retry_count(QUERY_MAX_RETRY_COUNT, D-099)가 정의만 있고 소비처
0곳 — graph 라우팅·subagents 루프·result_organizer가 리터럴 3을 하드코딩해
상수 변경 시 7곳 동기화가 필요하던 결함의 재발 방지.
"""

from src.graph import (
    route_after_execution,
    route_after_organization,
    route_after_validation,
    route_after_validation_with_approval,
)
from src.state import create_initial_state


def _failed_validation_state(retry_count: int):
    state = create_initial_state(user_query="test")
    state["validation_result"] = {"passed": False, "reason": "x"}
    state["retry_count"] = retry_count
    return state


class TestRouteMaxRetryParam:
    def test_validation_default_budget_unchanged(self):
        """기본값(3)은 종전 동작과 동일하다."""
        assert route_after_validation(_failed_validation_state(3)) == "error_response"
        assert route_after_validation(_failed_validation_state(2)) == "query_generator"

    def test_validation_respects_config_budget(self):
        """max_retry 인자로 예산이 조절된다(배선은 config.query.max_retry_count)."""
        state = _failed_validation_state(4)
        assert route_after_validation(state, max_retry=5) == "query_generator"
        assert route_after_validation(state, max_retry=4) == "error_response"

    def test_validation_with_approval_respects_budget(self):
        state = _failed_validation_state(4)
        assert (
            route_after_validation_with_approval(state, max_retry=5)
            == "query_generator"
        )

    def test_execution_respects_budget(self):
        state = create_initial_state(user_query="test")
        state["error_message"] = "SQL 실행 에러"
        state["retry_count"] = 4
        assert route_after_execution(state, max_retry=5) == "query_generator"
        assert route_after_execution(state, max_retry=4) == "error_response"

    def test_organization_respects_budget(self):
        state = create_initial_state(user_query="test")
        state["organized_data"] = {"is_sufficient": False}
        state["retry_count"] = 4
        assert route_after_organization(state, max_retry=5) == "query_generator"
        assert route_after_organization(state, max_retry=4) == "output_generator"


class TestNoHardcodedRetryLiterals:
    def test_retry_literal_consumed_from_config(self):
        """graph·subagents·result_organizer에 retry_count 리터럴 3 비교가 남지 않는다."""
        import re
        from pathlib import Path

        pattern = re.compile(r'retry_count.{0,20}[<>]=?\s*3\b')
        offenders = []
        for path in [
            "src/graph.py",
            "src/orchestration/subagents.py",
            "src/nodes/result_organizer.py",
        ]:
            text = Path(path).read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path}:{i}")
        assert not offenders, f"재시도 상한 리터럴 잔존: {offenders}"
