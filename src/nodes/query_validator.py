"""SQL 검증 노드.

생성된 SQL의 문법, 안전성, 성능을 사전 검증한다.
LLM에 의존하지 않고 규칙 기반으로 검증한다.

검증 코어(상태 비결합 순수 함수)는 ``src.sql_validation``에 있다 — 노드와 단계적 도출
루프의 사전 검증 도구가 같은 코어를 쓰는데, 코어가 노드에 있으면 도구 계층이 노드를
역참조하게 되어(`tools → nodes`) 순환이 생기기 때문이다(Plan 69 후속 2단계). 이 노드는
state에서 인자를 뽑아 코어를 호출하고 감사 로그·State 변환만 담당한다.

코어 심볼은 아래에서 재노출하므로 기존 임포트 경로
(``from src.nodes.query_validator import validate_sql`` 등)는 무수정으로 동작한다.
"""

from __future__ import annotations

import logging

import structlog

from src.config import AppConfig, load_config
from src.db_adapters import get_adapter
from src.state import AgentState
# 검증 코어는 도구 계층에 있다(위 참조). 이 모듈이 쓰는 것과 하위호환 재노출분을 함께
# 임포트하고 `__all__`로 공표한다 — 노드 경로와 도구 경로가 같은 코어를 공유한다(D-067).
from src.sql_validation import (
    SQLValidationOutcome,
    _add_limit_clause,
    _check_excluded_join_columns,
    _check_left_join_where_demotion,
    _check_performance_risks,
    _clean_sql_for_table_extraction,
    _extract_alias_map,
    _extract_cte_names,
    _extract_table_names,
    _find_bare_hangul_tokens,
    _get_statement_type,
    _has_limit_clause,
    _strip_parenthesized,
    _validate_columns,
    _validate_forbidden_joins,
    check_left_join_where_demotion,
    find_bare_hangul_tokens,
    validate_sql,
)

logger = logging.getLogger(__name__)
_audit_logger = structlog.get_logger("audit")

#: 이 모듈이 계속 노출하는 이름 — 노드 자체 API + 코어의 하위호환 재노출이다.
#: (신규 코드는 코어 심볼을 ``src.tools.sql_validation``에서 직접 임포트할 것.)
__all__ = [
    "query_validator",
    "validate_sql",
    "SQLValidationOutcome",
    "check_left_join_where_demotion",
    "find_bare_hangul_tokens",
    "_add_limit_clause",
    "_check_excluded_join_columns",
    "_check_left_join_where_demotion",
    "_check_performance_risks",
    "_clean_sql_for_table_extraction",
    "_extract_alias_map",
    "_extract_cte_names",
    "_extract_table_names",
    "_find_bare_hangul_tokens",
    "_get_statement_type",
    "_has_limit_clause",
    "_strip_parenthesized",
    "_validate_columns",
    "_validate_forbidden_joins",
]


async def query_validator(
    state: AgentState,
    *,
    app_config: AppConfig | None = None,
) -> dict:
    """생성된 SQL을 검증한다.

    검증 항목:
    1. SQL 파싱 가능 여부 (문법)
    2. SELECT 문 여부 (DML/DDL 차단)
    3. 금지 키워드 포함 여부 (주석 제거 후)
    4. SQL 인젝션 패턴 탐지
    5. 참조 테이블 존재 여부
    6. 참조 컬럼 존재 여부
    7. LIMIT 절 존재 여부
    8. 성능 위험 패턴 탐지

    Args:
        state: 현재 에이전트 상태
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - validation_result: 검증 결과 딕셔너리
        - generated_sql: 자동 보정된 SQL (LIMIT 추가 시)
        - error_message: 검증 실패 사유 (실패 시), 정상 시 None
        - current_node: "query_validator"
    """
    sql = state["generated_sql"]
    schema_info = state["schema_info"]
    if app_config is None:
        app_config = load_config()

    # DB 어댑터 전용 검증(폴스타 라우팅 필터 오용 등) — 담당 어댑터가 있으면 훅을 주입
    # (기존 _check_routing_filter_misuse를 폴스타 어댑터로 이동, Plan 63 P2/D-089).
    adapter = get_adapter(state.get("active_db_id"), app_config.get_polestar_db_ids() or None)
    adapter_checks = adapter.validator_checks() if adapter is not None else []

    outcome = validate_sql(
        sql,
        schema_info,
        db_engine=_engine_or_fallback(state),
        user_query=state.get("user_query", "") or "",
        default_limit=app_config.query.default_limit,
        adapter_checks=adapter_checks,
    )

    if outcome.forbidden_keywords:
        _audit_logger.warning(
            "security_alert",
            alert_type="forbidden_sql",
            forbidden_keywords=outcome.forbidden_keywords,
            sql=sql[:200],
            user_id=state.get("user_id"),
        )
    if outcome.injection_count:
        _audit_logger.warning(
            "security_alert",
            alert_type="sql_injection_attempt",
            pattern_count=outcome.injection_count,
            sql=sql[:200],
            user_id=state.get("user_id"),
        )

    # 결과 결정
    if outcome.errors:
        logger.warning(f"SQL 검증 실패: {outcome.errors}")
        return _build_failure_result(outcome.errors)

    # 자동 보정된 SQL 적용
    auto_fixed_sql = outcome.auto_fixed_sql
    final_sql = auto_fixed_sql if auto_fixed_sql else sql

    reason_parts = ["검증 통과"]
    if outcome.warnings:
        reason_parts.append(f"경고: {'; '.join(outcome.warnings)}")

    logger.info(f"SQL 검증 통과: {final_sql[:100]}...")

    return {
        "validation_result": {
            "passed": True,
            "reason": ". ".join(reason_parts),
            "auto_fixed_sql": auto_fixed_sql,
            # 경고를 구조화 노출 — reason 문자열에만 접혀 감사 로그로 전달 불가능하던
            # 결함 수정 (Plan 69 P0-④, executor가 validation_warnings로 전달)
            "warnings": list(outcome.warnings),
        },
        "generated_sql": final_sql,
        "error_message": None,
        "current_node": "query_validator",
    }





def _engine_or_fallback(state: AgentState) -> str:
    """active_db_engine 또는 postgresql 폴백 — 폴백 발동을 계측한다 (Plan 69 P4-4).

    그래프 경로는 active_db_engine 쓰기 지점이 없어 항상 폴백으로 동작해 왔다(계획서
    §1.3 실측). DB2 DB가 이 경로로 흐르면 잘못된 방언이 된다 — 결정적 주입 전환은
    이 로그의 라이브 실측(발동 시 db_id) 후 별도 판단한다.
    """
    engine = state.get("active_db_engine")
    if engine:
        return engine
    if state.get("active_db_id"):
        logger.info(
            "[엔진폴백] active_db_engine 미설정(db=%s) — postgresql 가정",
            state.get("active_db_id"),
        )
    return "postgresql"


def _build_failure_result(errors: list[str]) -> dict:
    """검증 실패 결과를 구성한다.

    Args:
        errors: 에러 메시지 목록

    Returns:
        State 업데이트 딕셔너리
    """
    reason = "; ".join(errors)
    return {
        "validation_result": {
            "passed": False,
            "reason": reason,
            "auto_fixed_sql": None,
        },
        "error_message": f"SQL 검증 실패: {reason}",
        "current_node": "query_validator",
    }


