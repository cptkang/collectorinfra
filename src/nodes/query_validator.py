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
from collections.abc import Iterable, Mapping
from typing import Any

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
from src.utils.query_gen_common import surface_query_for_judgment

logger = logging.getLogger(__name__)
_audit_logger = structlog.get_logger("audit")

#: 생성기가 SQL 대신 산문(되물음·불가 사유)을 반환한 경우의 재시도 예산.
#: 전체 예산(`QUERY_MAX_RETRY_COUNT`)과 별도로 둔다 — 산문은 프롬프트가 지시한 동작이라
#: (polestar 템플릿 [Strict Constraints] 1: *"모호하거나 스키마 범위를 벗어나면 쿼리를
#: 생성하지 말고 추가 맥락을 요청하라"*) 같은 프롬프트를 다시 돌려도 대개 같은 산문이
#: 돌아온다. run `20260918-182507` 실측(체인 39건): 회복은 retry=1 **7건**인데 retry=2·3은
#: 합해 3건이고, 회복하지 못한 29건이 각 3회를 더 태워 턴을 60초 벽으로 밀어냈다.
#:
#: **단일 출처다**(plans/119 N-5). 그래프 경로(`graph.route_after_validation`)·2단 단일 DB 루프
#: (`subagents._run_single_db_pipeline`)·멀티 DB 재생성 루프(`multi_db_executor.
#: _generate_validated_sql`)가 이 상수를 import 한다 — 종전에는 그래프에만 배선돼 기준 경로(2단)가
#: 일반 예산 3까지 돌았다(run `20260923-103638`: 재시도 2~3회차 산문 83회).
NON_SQL_RETRY_BUDGET = 1

#: 검증 사유 중 "생성 산출물이 SELECT 문이 아님"을 뜻하는 접두 — 검증 코어(`validate_sql` 2번
#: 검사)와 멀티 DB 간이 검증(`multi_db_executor._validate_sql_simple`)의 두 문구다.
NON_SELECT_ERROR_PREFIXES: tuple[str, ...] = ("SELECT 문만 허용됩니다", "SELECT 문이 아닙니다")

#: SQL 재생성 루프 종결 표지(`regen_stop.reason`) — 유효 SQL 없이 끝난 task 결과에 싣고 재계획기가
#: 소비한다(plans/119 Q-3 · 계약: ``{"reason": …, "detail": 마지막 검증/실패 사유}``).
REGEN_STOP_VALIDATION_BUDGET = "validation_budget"
REGEN_STOP_NON_SQL = "non_sql"
REGEN_STOP_DEADLINE = "deadline"


def is_non_sql_prose(sql: str, errors: Iterable[str]) -> bool:
    """생성 산출물이 SQL이 아니라 산문(되물음·불가 사유)인가 — 산문 전용 예산의 판정 신호.

    검증 사유에 "SELECT 문이 아님" 계열이 있고, 그 산출물이 FabriX PII 필터 차단 안내문이 아닐 때
    참이다. PII 차단 변형은 원인이 달라(D-153 후속2) 제외한다 — 종전 경로. 단일(노드)·멀티
    (간이·전체 검증) 경로가 같은 판정을 쓴다(D-066).
    """
    if not any(str(e).startswith(NON_SELECT_ERROR_PREFIXES) for e in errors):
        return False
    from src.security.pii_filter import is_filter_blocked

    return not is_filter_blocked(raw_text=sql)


def non_sql_budget_exhausted(
    validation_result: Mapping[str, Any] | None, retry_count: int,
) -> bool:
    """산문 응답이고 그 전용 예산을 소진했는가(그래프·2단 단일 루프 공용 판정)."""
    return bool((validation_result or {}).get("non_sql")) and retry_count >= NON_SQL_RETRY_BUDGET


def non_sql_prose_response(prose: str) -> str:
    """산문 조기 종결의 사용자 응답 본문 — 그래프 `error_response`와 2단 단일 DB 경로가 같은 문구다.

    생성기가 남긴 되물음·불가 사유를 그대로 싣는다 — 그 텍스트가 사용자가 받아야 할 답이고,
    종전에는 "재시도 3회 초과"로 덮여 통째로 버려졌다(침묵적 폐기 금지 · plans/108 CU-A2).
    """
    text = (prose or "").strip()[:1500]
    return (
        "요청을 SQL로 옮기지 못했습니다. 조회 엔진이 대신 남긴 설명입니다.\n\n"
        f"{text}\n\n"
        "조회 대상(서버·지표·기간)을 구체적으로 지정해 주시면 다시 시도하겠습니다."
    )


def retrieval_reserve_sec(app_config: Any) -> float:
    """서술 예약 초(`ServerConfig.answer_reserve_sec`) — 조회 마감 = 처리 마감 − 이 값(D-267 ⑥).

    설정 대역(SimpleNamespace·MagicMock)에서는 0이다. 마감(`request_deadline`)이 없는 상태에서는
    이 값과 무관하게 시간 게이트가 항상 통과한다(종전 동작).
    """
    value = getattr(getattr(app_config, "server", None), "answer_reserve_sec", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, float(value))


def deadline_stop_message(
    stage: str,
    *,
    remaining_sec: float | None,
    need_sec: float | None,
    last_reason: str | None = None,
) -> str:
    """조회 마감 때문에 SQL 작업을 시작하지 않고 끝낼 때의 사용자 문구.

    plans/119 T-1ⓑ·T-3 — 침묵 금지.

    Args:
        stage: 시작하지 않은 단계("스키마 분석"·"SQL 생성"·"SQL 재생성")
        remaining_sec: 조회 마감까지 남은 초(음수면 이미 지남)
        need_sec: 비교한 직전 생성 소요(초). 없으면(첫 생성·진입 전) 마감 경과로 판정한 것이다
        last_reason: 마지막 검증·실행 실패 사유(있으면 덧붙인다)

    멀티 DB 경로는 이 문구가 존 커버리지 각주(DB당 150자 절단)에 실리므로 앞부분을 짧게 둔다.
    """
    if need_sec is None or remaining_sec is None:
        why = "조회 마감 경과"
    else:
        why = f"조회 마감까지 {max(0.0, remaining_sec):.0f}초 < 직전 SQL 생성 {need_sec:.0f}초"
    message = f"답변할 시간을 남기려고 {stage}을(를) 하지 않았습니다({why})."
    if last_reason:
        message += f" 마지막 실패 사유: {last_reason}"
    return message


#: 이 모듈이 계속 노출하는 이름 — 노드 자체 API + 코어의 하위호환 재노출이다.
#: (신규 코드는 코어 심볼을 ``src.tools.sql_validation``에서 직접 임포트할 것.)
__all__ = [
    "query_validator",
    "NON_SQL_RETRY_BUDGET",
    "NON_SELECT_ERROR_PREFIXES",
    "REGEN_STOP_VALIDATION_BUDGET",
    "REGEN_STOP_NON_SQL",
    "REGEN_STOP_DEADLINE",
    "is_non_sql_prose",
    "non_sql_budget_exhausted",
    "non_sql_prose_response",
    "retrieval_reserve_sec",
    "deadline_stop_message",
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
    adapter_checks = (
        adapter.validator_checks(user_query=state.get("user_query", "") or "")
        if adapter is not None else []
    )

    outcome = validate_sql(
        sql,
        schema_info,
        db_engine=_engine_or_fallback(state),
        # "전체/모든" 행 상한 상향 판정 입력 — 원문 기준(plans/107 W0.5). 3단은 종전과 같다.
        user_query=surface_query_for_judgment(state),
        default_limit=app_config.query.default_limit,
        adapter_checks=adapter_checks,
    )

    # FabriX PII 필터 차단 안내문이 content로 온 변형 감지(D-153 후속2) — 검증 코어의
    # "SELECT 아님" 판정을 차단 원인 진단으로 치환해 정확히 노출한다(멀티 경로와 대칭).
    # 코어는 state 접근이 없으므로 진단 결합은 노드 계층에서 수행한다.
    _non_select_errors = [
        e for e in outcome.errors if e.startswith(NON_SELECT_ERROR_PREFIXES)
    ]
    #: 생성기가 SQL이 아니라 산문(되물음·불가 사유)을 반환한 경우. PII 차단 변형은 원인이
    #: 달라 제외한다 — 여기 True면 그래프·2단 루프가 전용 예산(`NON_SQL_RETRY_BUDGET`)으로 조기
    #: 종결하고 그 산문을 사용자 응답에 싣는다(plans/108 CU-A2 · plans/119 N-5). 판정은 멀티 경로와
    #: 같은 함수다(`is_non_sql_prose`).
    _is_non_sql_prose = False
    if _non_select_errors:
        _is_non_sql_prose = is_non_sql_prose(sql, _non_select_errors)
        _pii_blocked = not _is_non_sql_prose
        if _pii_blocked:
            # D-155: query_generator가 차단 시점에 산출한 섹션별 로컬 스캔 진단을
            # 에러에 실어 "어느 블록의 어떤 값이 걸렸는지"를 UI에서 바로 읽게 한다
            # (폐쇄망은 로그 접근이 어려워 UI 노출이 1차 진단 채널).
            _diag = state.get("pii_block_diagnosis")
            _pii_msg = "FabriX PII 필터 차단 응답(비-SQL) — " + (
                f"원인 후보: {_diag}"
                if _diag
                else "프롬프트에 PII성 텍스트 포함 (로그 [PII-FILTER] 참조)"
            )
            outcome.errors[:] = [
                _pii_msg if e in _non_select_errors else e for e in outcome.errors
            ]

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
        return _build_failure_result(outcome.errors, non_sql=_is_non_sql_prose)

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


def _build_failure_result(errors: list[str], *, non_sql: bool = False) -> dict:
    """검증 실패 결과를 구성한다.

    Args:
        errors: 에러 메시지 목록
        non_sql: 생성 산출물이 SQL이 아닌 산문이었는가(그래프 조기 종결 신호)

    Returns:
        State 업데이트 딕셔너리
    """
    reason = "; ".join(errors)
    return {
        "validation_result": {
            "passed": False,
            "reason": reason,
            "auto_fixed_sql": None,
            "non_sql": non_sql,
        },
        "error_message": f"SQL 검증 실패: {reason}",
        "current_node": "query_validator",
    }


