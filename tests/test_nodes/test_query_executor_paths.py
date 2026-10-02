"""query_executor 미커버 경로 테스트 (Plan 69 P1-2 / §1.8 공백 보강).

- 일반 `Exception`(타임아웃·실행 에러가 아닌 연결 계열) 경로는 소스·테스트 통틀어
  단언이 0건이었다 — 사용자에게 나가는 "DB 연결 에러" 문구와 감사 기록을 고정한다.
- `query_attempts`는 기존 이력에 **누적**돼야 한다(덮어쓰기 금지 — 재시도 이력 유실).

현행 동작 고정이 목적이다(버그 수정 아님). P4-1이 4중 블록을 기록 헬퍼로 합칠 때
이 단언들이 동작 불변 판정 기준이 된다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.dbhub.models import QueryExecutionError
from src.nodes.query_executor import query_executor
from src.state import QueryAttempt, create_initial_state

_SQL = "SELECT hostname FROM servers LIMIT 100;"


class _Ctx:
    """get_db_client가 돌려주는 async 컨텍스트 대역."""

    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *args):
        return False


def _client_raising(exc: Exception) -> AsyncMock:
    client = AsyncMock()
    client.execute_sql = AsyncMock(side_effect=exc)
    return client


def _client_returning(rows: list[dict]) -> AsyncMock:
    client = AsyncMock()
    result = AsyncMock()
    result.rows = rows
    result.row_count = len(rows)
    client.execute_sql = AsyncMock(return_value=result)
    return client


async def _run(state: dict, client: AsyncMock) -> tuple[dict, AsyncMock]:
    audit = AsyncMock()
    with patch("src.nodes.query_executor.get_db_client", return_value=_Ctx(client)):
        with patch("src.nodes.query_executor.log_query_execution", audit):
            result = await query_executor(state)
    return result, audit


def _state(**overrides) -> dict:
    state = create_initial_state(user_query="서버 목록")
    state["generated_sql"] = _SQL
    state.update(overrides)
    return state


class TestUnexpectedExceptionPath:
    """예상 밖 예외 = DB 연결 계열로 분류하고 재시도 유도 메시지를 남긴다."""

    async def test_generic_exception_reports_connection_error(self):
        result, audit = await _run(_state(), _client_raising(RuntimeError("connection reset")))

        assert result["error_message"].startswith("DB 연결 에러")
        assert "connection reset" in result["error_message"]
        assert result["query_results"] == []
        assert result["current_node"] == "query_executor"

        attempts = result["query_attempts"]
        assert len(attempts) == 1
        assert attempts[0]["success"] is False
        assert attempts[0]["sql"] == _SQL
        assert attempts[0]["error"] == "connection reset"
        assert attempts[0]["row_count"] == 0

        assert audit.await_count == 1
        kwargs = audit.await_args.kwargs
        assert kwargs["success"] is False
        assert kwargs["sql"] == _SQL
        assert kwargs["error"] == "connection reset"
        assert kwargs["row_count"] == 0

    async def test_execution_error_keeps_distinct_message(self):
        """실행 에러(SQL 문제)는 연결 에러와 다른 문구로 분류된다 — 경계 고정."""
        result, _ = await _run(_state(), _client_raising(QueryExecutionError("syntax error")))

        assert result["error_message"].startswith("SQL 실행 에러")
        assert "DB 연결 에러" not in result["error_message"]

    async def test_audit_carries_request_context_on_failure(self):
        """실패 경로도 user_id·thread_id·retry_attempt를 감사에 전달한다."""
        state = _state(retry_count=2)
        state["user_id"] = "u-1"
        state["thread_id"] = "t-1"
        state["active_db_id"] = "polestar_cm_gp"
        _, audit = await _run(state, _client_raising(RuntimeError("boom")))

        kwargs = audit.await_args.kwargs
        assert kwargs["retry_attempt"] == 2
        assert kwargs["user_id"] == "u-1"
        assert kwargs["thread_id"] == "t-1"
        assert kwargs["source_name"] == "polestar_cm_gp"


class TestQueryAttemptsAccumulate:
    """기존 이력 위에 이번 시도가 덧붙는다(재시도 이력 보존)."""

    _PRIOR = QueryAttempt(
        sql="SELECT 1", success=False, error="이전 실패", row_count=0, execution_time_ms=1.0
    )

    async def test_success_appends_to_existing_attempts(self):
        state = _state(query_attempts=[dict(self._PRIOR)])
        result, _ = await _run(state, _client_returning([{"hostname": "web-01"}]))

        attempts = result["query_attempts"]
        assert len(attempts) == 2
        assert attempts[0]["error"] == "이전 실패"
        assert attempts[1]["success"] is True
        assert attempts[1]["sql"] == _SQL
        assert attempts[1]["row_count"] == 1
        assert result["error_message"] is None
        assert result["query_results"] == [{"hostname": "web-01"}]

    async def test_failure_also_appends(self):
        state = _state(query_attempts=[dict(self._PRIOR)])
        result, _ = await _run(state, _client_raising(RuntimeError("boom")))

        assert [a["success"] for a in result["query_attempts"]] == [False, False]

    async def test_input_state_attempts_not_mutated(self):
        """입력 state의 리스트를 제자리 변형하지 않는다(체크포인터 델타 오염 방지)."""
        prior = [dict(self._PRIOR)]
        state = _state(query_attempts=prior)
        await _run(state, _client_returning([]))

        assert len(prior) == 1
