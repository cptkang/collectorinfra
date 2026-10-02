"""비-SQL(산문) 생성 응답의 조기 종결 회귀 테스트 (plans/108 CU-A2).

run `20260918-182507` 실측: `SQL 생성 완료` 424회 중 **130회가 SQL이 아닌 한국어 산문**
이었고, 그 전량이 `SELECT 문만 허용됩니다. 감지된 타입: UNKNOWN` 검증 실패로 이어졌다.
산문은 프롬프트가 지시한 동작이다 — polestar 시스템 템플릿 [Strict Constraints] 1이
*"요청이 모호하거나 스키마 범위를 벗어나는 경우, 쿼리를 생성하지 말고 추가 맥락을
요청하라"*고 못박는다. 그런데 파이프라인은 그 되물음을 **버리고** 같은 프롬프트로
3회를 더 태운 뒤 "재시도 횟수가 최대(3회)에 도달"만 답했다.

회복률 실측(체인 39건): retry=1 **7건** · retry=2 1건 · retry=3 2건 · 끝까지 산문 29건.
→ 예산 1이 회복의 70%를 지키면서 소모 호출을 124회에서 약 절반으로 줄인다.
"""

from __future__ import annotations

from src.graph import (
    NON_SQL_RETRY_BUDGET,
    _error_response_node,
    route_after_validation,
    route_after_validation_with_approval,
)
from src.state import create_initial_state


def _non_sql_state(retry_count: int, sql: str = "요청하신 알람 데이터는 스키마에 없습니다."):
    state = create_initial_state(user_query="경고 이상 알람 상위 10건")
    state["validation_result"] = {
        "passed": False,
        "reason": "SELECT 문만 허용됩니다. 감지된 타입: UNKNOWN",
        "auto_fixed_sql": None,
        "non_sql": True,
    }
    state["retry_count"] = retry_count
    state["generated_sql"] = sql
    state["error_message"] = "SQL 검증 실패: SELECT 문만 허용됩니다. 감지된 타입: UNKNOWN"
    return state


def _ordinary_failure_state(retry_count: int):
    state = create_initial_state(user_query="test")
    state["validation_result"] = {"passed": False, "reason": "dtime IS NULL 누락"}
    state["retry_count"] = retry_count
    return state


class TestNonSqlRetryBudget:
    def test_budget_is_one(self):
        assert NON_SQL_RETRY_BUDGET == 1

    def test_first_prose_still_gets_one_retry(self):
        """retry=0 산문은 한 번 더 생성한다 — 실측 회복의 7/10이 여기서 난다."""
        assert route_after_validation(_non_sql_state(0)) == "query_generator"

    def test_second_prose_terminates_without_burning_budget(self):
        """retry=1 산문은 전체 예산(3)이 남아 있어도 종결한다."""
        assert route_after_validation(_non_sql_state(1)) == "error_response"
        assert route_after_validation(_non_sql_state(2)) == "error_response"

    def test_approval_route_is_symmetric(self):
        """승인 경로도 같은 예산을 쓴다(단일/승인 경로 비대칭 금지)."""
        assert (
            route_after_validation_with_approval(_non_sql_state(0)) == "query_generator"
        )
        assert (
            route_after_validation_with_approval(_non_sql_state(1)) == "error_response"
        )

    def test_ordinary_validation_failure_budget_unchanged(self):
        """산문이 아닌 검증 실패는 종전 예산(3) 그대로다."""
        assert route_after_validation(_ordinary_failure_state(1)) == "query_generator"
        assert route_after_validation(_ordinary_failure_state(2)) == "query_generator"
        assert route_after_validation(_ordinary_failure_state(3)) == "error_response"


class TestNonSqlResponseSurfacing:
    def test_model_text_is_surfaced_not_discarded(self):
        """생성기가 남긴 되물음·사유를 응답에 싣는다(침묵적 폐기 금지)."""
        prose = (
            "요청하신 '프로세스 이력'은 제공된 스키마에 없습니다. "
            "어떤 서버의 어떤 지표가 필요하신가요?"
        )
        out = _error_response_node(_non_sql_state(1, sql=prose))
        assert prose in out["final_response"]
        assert "재시도 횟수가 최대" not in out["final_response"]

    def test_ordinary_error_response_unchanged(self):
        """일반 실패 응답 문구는 종전 그대로다."""
        state = _ordinary_failure_state(3)
        state["error_message"] = "SQL 검증 실패: dtime IS NULL 누락"
        out = _error_response_node(state)
        assert "재시도 횟수가 최대(3회)에 도달" in out["final_response"]
