"""결과 병합 노드.

멀티 DB 실행 결과를 통합하여 result_organizer가
처리할 수 있는 형태로 변환한다.
DB별 에러가 있으면 부분 에러 정보도 포함한다.
DB별 결과 요약 정보를 생성하여 result_organizer에 전달한다.
"""

from __future__ import annotations

import logging

from src.config import AppConfig, load_config
from src.routing.domain_config import get_domain_by_id
from src.state import AgentState

logger = logging.getLogger(__name__)


async def result_merger(
    state: AgentState,
    *,
    app_config: AppConfig | None = None,
) -> dict:
    """멀티 DB 결과를 통합한다.

    Args:
        state: 현재 에이전트 상태
        app_config: 앱 설정

    Returns:
        업데이트할 State 필드:
        - query_results: 병합된 결과 행
        - error_message: 부분 에러 메시지 (모든 DB 실패 시)
        - current_node: "result_merger"
    """
    if app_config is None:
        app_config = load_config()

    db_results = state.get("db_results", {})
    db_errors = state.get("db_errors", {})

    # 결과 병합 (이미 multi_db_executor에서 query_results로 병합됨)
    merged_results = state.get("query_results", [])

    # DB별 결과 요약 정보 생성
    db_result_summary: dict[str, dict] = {}
    for db_id, rows in db_results.items():
        domain = get_domain_by_id(db_id)
        db_result_summary[db_id] = {
            "display_name": domain.display_name if domain else db_id,
            "row_count": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
        }

    # 에러 요약 생성
    error_summary = _build_error_summary(db_results, db_errors)

    # 결과 통계 로그
    total_rows = len(merged_results)
    logger.info(
        "결과 병합 완료: %d개 DB에서 총 %d건, 에러 %d개",
        len(db_results),
        total_rows,
        len(db_errors),
    )

    return {
        "query_results": merged_results,
        "error_message": error_summary if not db_results else None,
        "current_node": "result_merger",
    }


def _build_error_summary(
    db_results: dict[str, list],
    db_errors: dict[str, str],
) -> str | None:
    """에러 요약 메시지를 생성한다.

    Args:
        db_results: DB별 쿼리 결과
        db_errors: DB별 에러 메시지

    Returns:
        에러 요약 문자열 또는 None (에러 없음)
    """
    if not db_errors:
        return None

    error_parts = []
    for db_id, error_msg in db_errors.items():
        domain = get_domain_by_id(db_id)
        display_name = domain.display_name if domain else db_id
        error_parts.append(f"[{display_name}] {error_msg}")

    if not db_results:
        # 모든 DB 실패
        return "모든 DB 쿼리가 실패했습니다:\n" + "\n".join(error_parts)
    else:
        # 부분 실패 - 성공한 결과는 있으므로 경고로 처리
        return (
            "일부 DB 쿼리가 실패했습니다 "
            f"(성공: {len(db_results)}개, 실패: {len(db_errors)}개):\n"
            + "\n".join(error_parts)
        )
