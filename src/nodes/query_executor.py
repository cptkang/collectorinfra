"""쿼리 실행 노드.

검증된 SQL을 DB를 통해 실행하고 결과를 수집한다.
실행 에러 시 에러 메시지를 State에 기록하여 재시도를 유도한다.
각 실행 시도를 query_attempts에 기록한다.
"""

from __future__ import annotations

import logging
import time

from src.config import AppConfig, load_config
from src.db import get_db_client
from src.dbhub.models import QueryExecutionError, QueryTimeoutError
from src.security.audit_logger import log_query_execution
from src.state import AgentState, QueryAttempt

logger = logging.getLogger(__name__)


async def query_executor(
    state: AgentState,
    *,
    app_config: AppConfig | None = None,
) -> dict:
    """검증된 SQL을 실행하고 결과를 수집한다.

    각 실행 시도의 결과를 query_attempts에 기록한다.

    Args:
        state: 현재 에이전트 상태
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - query_results: 쿼리 결과 (dict 리스트)
        - error_message: 실행 에러 시 메시지, 정상 시 None
        - current_node: "query_executor"
        - query_attempts: 기존 이력 + 현재 시도 기록
    """
    if app_config is None:
        app_config = load_config()
    sql = state["generated_sql"]
    existing_attempts: list[QueryAttempt] = list(state.get("query_attempts", []))

    start_time = time.time()

    try:
        db_id = state.get("active_db_id")
        async with get_db_client(app_config, db_id=db_id if db_id and db_id != "_default" else None) as client:
            result = await client.execute_sql(sql)

        elapsed_ms = (time.time() - start_time) * 1000

        # 실행 이력 기록
        attempt = QueryAttempt(
            sql=sql,
            success=True,
            error=None,
            row_count=result.row_count,
            execution_time_ms=round(elapsed_ms, 2),
        )

        # 감사 로그 기록
        await log_query_execution(
            sql=sql,
            row_count=result.row_count,
            execution_time_ms=elapsed_ms,
            success=True,
            retry_attempt=state.get("retry_count", 0),
            user_id=state.get("user_id"),
            thread_id=state.get("thread_id"),
            source_name=state.get("active_db_id"),
        )

        logger.info(
            f"쿼리 실행 완료: {result.row_count}건, {elapsed_ms:.0f}ms"
        )

        return {
            "query_results": result.rows,
            "error_message": None,
            "current_node": "query_executor",
            "query_attempts": existing_attempts + [attempt],
        }

    except QueryTimeoutError as e:
        elapsed_ms = (time.time() - start_time) * 1000
        error_msg = (
            f"쿼리 타임아웃 초과. "
            f"쿼리를 최적화해주세요."
        )
        logger.warning(f"쿼리 타임아웃: {sql[:100]}...")

        attempt = QueryAttempt(
            sql=sql,
            success=False,
            error=str(e),
            row_count=0,
            execution_time_ms=round(elapsed_ms, 2),
        )

        await log_query_execution(
            sql=sql,
            row_count=0,
            execution_time_ms=elapsed_ms,
            success=False,
            error=str(e),
            retry_attempt=state.get("retry_count", 0),
            user_id=state.get("user_id"),
            thread_id=state.get("thread_id"),
            source_name=state.get("active_db_id"),
        )

        return {
            "query_results": [],
            "error_message": error_msg,
            "current_node": "query_executor",
            "query_attempts": existing_attempts + [attempt],
        }

    except QueryExecutionError as e:
        elapsed_ms = (time.time() - start_time) * 1000
        error_msg = f"SQL 실행 에러: {str(e)}"
        logger.error(f"쿼리 실행 실패: {e}")

        attempt = QueryAttempt(
            sql=sql,
            success=False,
            error=str(e),
            row_count=0,
            execution_time_ms=round(elapsed_ms, 2),
        )

        await log_query_execution(
            sql=sql,
            row_count=0,
            execution_time_ms=elapsed_ms,
            success=False,
            error=str(e),
            retry_attempt=state.get("retry_count", 0),
            user_id=state.get("user_id"),
            thread_id=state.get("thread_id"),
            source_name=state.get("active_db_id"),
        )

        return {
            "query_results": [],
            "error_message": error_msg,
            "current_node": "query_executor",
            "query_attempts": existing_attempts + [attempt],
        }

    except Exception as e:
        elapsed_ms = (time.time() - start_time) * 1000
        error_msg = f"DB 연결 에러: {str(e)}. DB 연결 상태를 확인해주세요."
        logger.error(f"예기치 않은 에러: {e}")

        attempt = QueryAttempt(
            sql=sql,
            success=False,
            error=str(e),
            row_count=0,
            execution_time_ms=round(elapsed_ms, 2),
        )

        await log_query_execution(
            sql=sql,
            row_count=0,
            execution_time_ms=elapsed_ms,
            success=False,
            error=str(e),
            retry_attempt=state.get("retry_count", 0),
            user_id=state.get("user_id"),
            thread_id=state.get("thread_id"),
            source_name=state.get("active_db_id"),
        )

        return {
            "query_results": [],
            "error_message": error_msg,
            "current_node": "query_executor",
            "query_attempts": existing_attempts + [attempt],
        }
