"""SQL 검증 노드.

생성된 SQL의 문법, 안전성, 성능을 사전 검증한다.
LLM에 의존하지 않고 규칙 기반으로 검증한다.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import sqlparse

from src.config import AppConfig, load_config
from src.security.sql_guard import FORBIDDEN_SQL_KEYWORDS, INJECTION_PATTERNS, SQLGuard
from src.state import AgentState

logger = logging.getLogger(__name__)


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

    guard = SQLGuard()
    errors: list[str] = []
    warnings: list[str] = []
    auto_fixed_sql: Optional[str] = None

    # 1. SQL 파싱 가능 여부
    try:
        parsed = sqlparse.parse(sql)
        if not parsed:
            errors.append("SQL을 파싱할 수 없습니다.")
    except Exception as e:
        errors.append(f"SQL 파싱 에러: {str(e)}")
        return _build_failure_result(errors)

    # 2. SELECT 문 여부 확인
    statement_type = _get_statement_type(sql)
    if statement_type != "SELECT":
        errors.append(f"SELECT 문만 허용됩니다. 감지된 타입: {statement_type}")

    # 3. 금지 키워드 확인
    forbidden = guard.detect_forbidden_keywords(sql, FORBIDDEN_SQL_KEYWORDS)
    if forbidden:
        errors.append(f"금지된 키워드가 포함되어 있습니다: {', '.join(forbidden)}")

    # 4. SQL 인젝션 패턴 탐지
    injections = guard.detect_injection_patterns(sql, INJECTION_PATTERNS)
    if injections:
        errors.append(
            f"SQL 인젝션 위험 패턴이 감지되었습니다: {len(injections)}건"
        )

    # 5. 참조 테이블 존재 여부
    referenced_tables = _extract_table_names(sql)
    available_tables = set(schema_info.get("tables", {}).keys())
    unknown_tables = referenced_tables - available_tables
    if unknown_tables and available_tables:
        errors.append(f"존재하지 않는 테이블 참조: {', '.join(unknown_tables)}")

    # 6. 참조 컬럼 존재 여부
    if not unknown_tables and available_tables:
        column_errors = _validate_columns(sql, schema_info, referenced_tables)
        errors.extend(column_errors)

    # 7. LIMIT 절 존재 여부
    if not _has_limit_clause(sql):
        default_limit = app_config.query.default_limit
        auto_fixed_sql = _add_limit_clause(sql, default_limit)
        warnings.append(
            f"LIMIT 절이 없어 자동으로 LIMIT {default_limit}을 추가했습니다."
        )

    # 8. 성능 위험 패턴
    perf_warnings = _check_performance_risks(sql, schema_info)
    warnings.extend(perf_warnings)

    # 결과 결정
    if errors:
        logger.warning(f"SQL 검증 실패: {errors}")
        return _build_failure_result(errors)

    # 자동 보정된 SQL 적용
    final_sql = auto_fixed_sql if auto_fixed_sql else sql

    reason_parts = ["검증 통과"]
    if warnings:
        reason_parts.append(f"경고: {'; '.join(warnings)}")

    logger.info(f"SQL 검증 통과: {final_sql[:100]}...")

    return {
        "validation_result": {
            "passed": True,
            "reason": ". ".join(reason_parts),
            "auto_fixed_sql": auto_fixed_sql,
        },
        "generated_sql": final_sql,
        "error_message": None,
        "current_node": "query_validator",
    }


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


def _get_statement_type(sql: str) -> str:
    """SQL 문의 타입을 판별한다.

    Args:
        sql: SQL 쿼리

    Returns:
        SQL 문 타입 문자열 (SELECT, INSERT, UNKNOWN 등)
    """
    parsed = sqlparse.parse(sql)
    if parsed:
        return parsed[0].get_type() or "UNKNOWN"
    return "UNKNOWN"


def _extract_table_names(sql: str) -> set[str]:
    """SQL에서 참조하는 테이블명을 추출한다.

    FROM, JOIN 절에서 테이블명을 추출한다.

    Args:
        sql: SQL 쿼리 문자열

    Returns:
        테이블명 집합
    """
    tables: set[str] = set()

    # FROM 절 (콤마로 구분된 다중 테이블 지원: FROM t1, t2, t3)
    from_clauses = re.findall(
        r"\bFROM\s+((?:\w+\s*,\s*)*\w+)", sql, re.IGNORECASE
    )
    for clause in from_clauses:
        for table in clause.split(","):
            table = table.strip()
            if table:
                # 별칭 제거 (예: "t1 AS a" → "t1", "t1 a" → "t1")
                table_name = table.split()[0]
                tables.add(table_name)

    # JOIN 절
    join_match = re.findall(r"\bJOIN\s+(\w+)", sql, re.IGNORECASE)
    tables.update(join_match)

    # information_schema 참조는 무시
    tables = {t for t in tables if not t.lower().startswith("information_schema")}

    return tables


def _validate_columns(
    sql: str,
    schema_info: dict,
    referenced_tables: set[str],
) -> list[str]:
    """SQL에서 참조하는 컬럼이 실제 존재하는지 검증한다.

    Args:
        sql: SQL 쿼리
        schema_info: 스키마 정보
        referenced_tables: 참조 테이블 집합

    Returns:
        에러 메시지 목록
    """
    errors: list[str] = []
    # 테이블.컬럼 패턴으로 참조된 컬럼 추출
    col_refs = re.findall(r"(\w+)\.(\w+)", sql)

    available_columns: dict[str, set[str]] = {}
    for table_name in referenced_tables:
        table_data = schema_info.get("tables", {}).get(table_name, {})
        columns = table_data.get("columns", [])
        available_columns[table_name] = {col["name"] for col in columns}

    for table_ref, col_ref in col_refs:
        # 별칭(alias)일 수 있으므로 실제 테이블에 대해서만 검증
        if table_ref in available_columns:
            if col_ref not in available_columns[table_ref] and col_ref != "*":
                errors.append(
                    f"테이블 '{table_ref}'에 컬럼 '{col_ref}'이 존재하지 않습니다."
                )

    return errors


def _has_limit_clause(sql: str) -> bool:
    """LIMIT 절이 있는지 확인한다.

    Args:
        sql: SQL 쿼리

    Returns:
        LIMIT 절 존재 여부
    """
    return bool(re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE))


def _add_limit_clause(sql: str, limit: int) -> str:
    """SQL에 LIMIT 절을 추가한다.

    Args:
        sql: SQL 쿼리
        limit: LIMIT 값

    Returns:
        LIMIT이 추가된 SQL
    """
    sql = sql.rstrip().rstrip(";")
    return f"{sql}\nLIMIT {limit};"


def _check_performance_risks(
    sql: str,
    schema_info: dict,
) -> list[str]:
    """성능 위험 패턴을 탐지한다.

    Args:
        sql: SQL 쿼리
        schema_info: 스키마 정보

    Returns:
        경고 메시지 목록
    """
    warnings: list[str] = []

    # SELECT * 패턴 (대형 테이블에서)
    if re.search(r"SELECT\s+\*", sql, re.IGNORECASE):
        tables = _extract_table_names(sql)
        for table in tables:
            table_data = schema_info.get("tables", {}).get(table, {})
            row_count = table_data.get("row_count_estimate", 0)
            if row_count and row_count > 100000:
                warnings.append(
                    f"대형 테이블 '{table}'({row_count:,}행)에 SELECT * 사용 주의"
                )

    # WHERE 절 없는 전체 스캔
    if not re.search(r"\bWHERE\b", sql, re.IGNORECASE):
        warnings.append(
            "WHERE 절이 없습니다. 전체 테이블 스캔이 발생할 수 있습니다."
        )

    # 카테시안 곱 가능성 (JOIN 조건 없는 다중 테이블)
    tables = _extract_table_names(sql)
    if len(tables) > 1 and not re.search(r"\bON\b", sql, re.IGNORECASE):
        warnings.append(
            "다중 테이블 참조에 JOIN 조건(ON)이 없습니다. 카테시안 곱 주의."
        )

    return warnings
