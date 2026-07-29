"""SQL 초안 사전 검증 도구 (Plan 67 Phase S1 §4.3).

단계적 도출 루프가 조립 중인 SQL을 실행 전에 점검할 수 있게, ``query_validator`` 노드에서
분리한 검증 코어(``validate_sql``)를 도구 형태로 노출한다. 검사 항목·메시지는 노드 경로와
동일하며, DB 특화 검증은 어댑터 레지스트리에서 조회한 훅으로만 수행한다(D-089).
"""

from __future__ import annotations

from typing import Optional

from src.db_adapters import get_adapter
from src.nodes.query_validator import validate_sql


def validate_sql_draft(
    sql: str,
    schema_info: dict,
    *,
    db_engine: str = "postgresql",
    user_query: str = "",
    default_limit: int = 100,
    db_id: Optional[str] = None,
    adapter_db_ids: Optional[set[str]] = None,
) -> dict:
    """SQL 초안을 실행 전에 검증한다(문법·안전성·스키마 정합·행 제한).

    Args:
        sql: 검증할 SQL 초안
        schema_info: 스키마 정보(tables·구조 메타 포함)
        db_engine: DB 엔진 타입(행 제한 절 방언 결정)
        user_query: 사용자 원문 질의("모든/전체" 조회면 행 제한 자동 추가 생략)
        default_limit: 행 제한 자동 추가 시 기본값
        db_id: 대상 DB 식별자(어댑터 디스패치용)
        adapter_db_ids: 어댑터 담당 db_id 집합(런타임 설정에서 주입)

    Returns:
        {"valid": bool, "errors": [...], "warnings": [...], "fixed_sql": str}
        — valid=False면 errors가 재작성 사유, fixed_sql은 행 제한이 보정된 SQL이다.
    """
    adapter = get_adapter(db_id, adapter_db_ids)
    adapter_checks = adapter.validator_checks() if adapter is not None else []
    outcome = validate_sql(
        sql,
        schema_info,
        db_engine=db_engine,
        user_query=user_query,
        default_limit=default_limit,
        adapter_checks=adapter_checks,
    )
    return {
        "valid": outcome.passed,
        "errors": list(outcome.errors),
        "warnings": list(outcome.warnings),
        "fixed_sql": outcome.auto_fixed_sql or sql,
    }
