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


async def _record_attempt(
    state: AgentState,
    sql: str,
    *,
    rows: list,
    row_count: int,
    error_message: str | None,
    error: str | None,
    elapsed_ms: float,
    validation_warnings: list[str] | None,
) -> dict:
    """실행 시도 1건의 attempt·감사 기록과 State 갱신 dict를 만든다 (Plan 69 P4-1).

    성공/타임아웃/실행 에러/일반 예외 4경로가 경과→QueryAttempt→감사→반환을 각각
    복제하던 것의 단일 출처 — 기록 필드가 경로별로 어긋나는 비대칭(P0-⑤ 유형)을
    구조적으로 차단한다. 경로별 차이는 로그 문구와 error_message뿐이다.
    """
    attempt = QueryAttempt(
        sql=sql,
        success=error is None,
        error=error,
        row_count=row_count,
        execution_time_ms=round(elapsed_ms, 2),
    )
    await log_query_execution(
        sql=sql,
        row_count=row_count,
        execution_time_ms=elapsed_ms,
        success=error is None,
        error=error,
        retry_attempt=state.get("retry_count", 0),
        user_id=state.get("user_id"),
        thread_id=state.get("thread_id"),
        source_name=state.get("active_db_id"),
        validation_warnings=validation_warnings,
    )
    return {
        "query_results": rows,
        "error_message": error_message,
        "current_node": "query_executor",
        "query_attempts": list(state.get("query_attempts", [])) + [attempt],
    }


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
    # 검증 경고를 감사 로그로 전달 (Plan 69 P0-④)
    validation_warnings = (state.get("validation_result") or {}).get("warnings") or None

    start_time = time.time()

    try:
        db_id = state.get("active_db_id")
        async with get_db_client(app_config, db_id=db_id if db_id and db_id not in ("_default", "default") else None) as client:
            result = await client.execute_sql(sql)

        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(
            f"쿼리 실행 완료: {result.row_count}건, {elapsed_ms:.0f}ms"
        )
        return await _record_attempt(
            state, sql,
            rows=result.rows, row_count=result.row_count,
            error_message=None, error=None,
            elapsed_ms=elapsed_ms, validation_warnings=validation_warnings,
        )

    except QueryTimeoutError as e:
        elapsed_ms = (time.time() - start_time) * 1000
        logger.warning(f"쿼리 타임아웃: {sql[:100]}...")
        return await _record_attempt(
            state, sql,
            rows=[], row_count=0,
            error_message="쿼리 타임아웃 초과. 쿼리를 최적화해주세요.",
            error=str(e),
            elapsed_ms=elapsed_ms, validation_warnings=validation_warnings,
        )

    except QueryExecutionError as e:
        elapsed_ms = (time.time() - start_time) * 1000
        logger.error(f"쿼리 실행 실패: {e}")
        return await _record_attempt(
            state, sql,
            rows=[], row_count=0,
            error_message=f"SQL 실행 에러: {str(e)}",
            error=str(e),
            elapsed_ms=elapsed_ms, validation_warnings=validation_warnings,
        )

    except Exception as e:
        elapsed_ms = (time.time() - start_time) * 1000
        logger.error(f"예기치 않은 에러: {e}")
        return await _record_attempt(
            state, sql,
            rows=[], row_count=0,
            error_message=f"DB 연결 에러: {str(e)}. DB 연결 상태를 확인해주세요.",
            error=str(e),
            elapsed_ms=elapsed_ms, validation_warnings=validation_warnings,
        )
