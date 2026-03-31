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

    # 5. 참조 테이블 존재 여부 (대소문자 무시 + bare name fallback)
    referenced_tables = _extract_table_names(sql)
    available_tables = set(schema_info.get("tables", {}).keys())
    available_tables_lower = {t.lower() for t in available_tables}
    # bare name → schema.table fallback 매핑 구축
    # 예: "cmm_resource" → "polestar.cmm_resource"
    bare_to_qualified: dict[str, str] = {}
    for t in available_tables:
        if "." in t:
            bare = t.rsplit(".", 1)[1].lower()
            bare_to_qualified[bare] = t
    unknown_tables = set()
    for t in referenced_tables:
        if t.lower() not in available_tables_lower:
            if t.lower() not in bare_to_qualified:
                unknown_tables.add(t)
    if unknown_tables and available_tables:
        errors.append(f"존재하지 않는 테이블 참조: {', '.join(unknown_tables)}")

    # 6. 참조 컬럼 존재 여부
    if not unknown_tables and available_tables:
        column_errors = _validate_columns(sql, schema_info, referenced_tables)
        errors.extend(column_errors)

    # 6.5. 금지 JOIN 컬럼 사용 감지 (warning)
    excluded_join_warnings = _check_excluded_join_columns(sql, schema_info)
    warnings.extend(excluded_join_warnings)

    # 6.6. EAV 프로필 기반 금지 조인 패턴 감지 (error → 재시도 유도)
    forbidden_join_errors = _validate_forbidden_joins(sql, schema_info)
    if forbidden_join_errors:
        errors.extend(forbidden_join_errors)

    # 7. LIMIT 절 존재 여부
    db_engine = state.get("active_db_engine") or "postgresql"
    if not _has_limit_clause(sql):
        default_limit = app_config.query.default_limit
        auto_fixed_sql = _add_limit_clause(sql, default_limit, db_engine)
        if db_engine == "db2":
            warnings.append(
                f"행 제한 절이 없어 자동으로 FETCH FIRST {default_limit} ROWS ONLY를 추가했습니다."
            )
        else:
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

    # schema.table 형태를 포함하는 식별자 패턴 (예: polestar.cmm_resource)
    _ident = r"[\w]+(?:\.[\w]+)?"

    # FROM 절 (콤마로 구분된 다중 테이블 지원: FROM t1, t2, t3)
    from_clauses = re.findall(
        rf"\bFROM\s+((?:{_ident}\s*,\s*)*{_ident})", sql, re.IGNORECASE
    )
    for clause in from_clauses:
        for table in clause.split(","):
            table = table.strip()
            if table:
                # 별칭 제거 (예: "t1 AS a" → "t1", "t1 a" → "t1")
                table_name = table.split()[0]
                tables.add(table_name)

    # JOIN 절 (schema.table 형태 지원)
    join_match = re.findall(rf"\bJOIN\s+({_ident})", sql, re.IGNORECASE)
    tables.update(join_match)

    # information_schema 참조는 무시
    tables = {t for t in tables if not t.lower().startswith("information_schema")}

    return tables


def _extract_alias_map(sql: str) -> dict[str, str]:
    """SQL에서 테이블 별칭 매핑을 추출한다.

    FROM, JOIN 절에서 "테이블명 별칭" 또는 "테이블명 AS 별칭" 패턴을 찾는다.

    Args:
        sql: SQL 쿼리

    Returns:
        별칭 → 테이블명 매핑 딕셔너리 (대소문자 원본 유지)
    """
    alias_map: dict[str, str] = {}

    # schema.table 형태를 포함하는 식별자 패턴
    _ident = r"[\w]+(?:\.[\w]+)?"

    # FROM 절: 콤마로 구분된 다중 테이블 지원
    # FROM schema.t1 AS a, t2 b, t3
    from_blocks = re.findall(
        rf"\bFROM\s+([\w.\s,]+?)(?:\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bHAVING\b|\bUNION\b|\bJOIN\b|\bINNER\b|\bLEFT\b|\bRIGHT\b|\bFULL\b|\bCROSS\b|\bON\b|\bFETCH\b|;|$)",
        sql,
        re.IGNORECASE,
    )
    for block in from_blocks:
        for item in block.split(","):
            parts = item.strip().split()
            if len(parts) >= 3 and parts[1].upper() == "AS":
                # schema.table AS alias
                alias_map[parts[2]] = parts[0]
            elif len(parts) == 2 and parts[1].upper() not in (
                "WHERE", "GROUP", "ORDER", "LIMIT", "HAVING",
                "UNION", "INNER", "LEFT", "RIGHT", "FULL",
                "CROSS", "ON", "FETCH", "JOIN",
            ):
                # schema.table alias
                alias_map[parts[1]] = parts[0]

    # JOIN 절: [LEFT|RIGHT|INNER|FULL|CROSS] JOIN schema.table [AS] alias ON
    join_pattern = re.findall(
        rf"\bJOIN\s+({_ident})(?:\s+AS\s+(\w+)|\s+(\w+))?\s+ON\b",
        sql,
        re.IGNORECASE,
    )
    for table, alias_as, alias_bare in join_pattern:
        alias = alias_as or alias_bare
        if alias:
            alias_map[alias] = table

    return alias_map


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
    alias_map = _extract_alias_map(sql)

    # bare name → qualified name fallback 매핑 구축
    all_tables = schema_info.get("tables", {})
    bare_to_qualified: dict[str, str] = {}
    for t in all_tables:
        if "." in t:
            bare = t.rsplit(".", 1)[1].lower()
            bare_to_qualified[bare] = t

    available_columns: dict[str, set[str]] = {}
    # 대소문자 무시를 위한 소문자→원본 테이블명 매핑
    table_name_lower_map: dict[str, str] = {}
    for table_name in referenced_tables:
        # 직접 매칭 → bare name fallback
        table_data = all_tables.get(table_name)
        if table_data is None:
            qualified = bare_to_qualified.get(table_name.lower())
            if qualified:
                table_data = all_tables.get(qualified)
        if table_data is None:
            table_data = {}
        columns = table_data.get("columns", [])
        available_columns[table_name] = {col["name"] for col in columns}
        table_name_lower_map[table_name.lower()] = table_name

    for table_ref, col_ref in col_refs:
        # 별칭 → 실제 테이블명 변환
        actual_table = alias_map.get(table_ref, table_ref)
        # 대소문자 무시 매칭
        resolved = table_name_lower_map.get(actual_table.lower(), actual_table)
        if resolved in available_columns:
            if col_ref not in available_columns[resolved] and col_ref != "*":
                # 대소문자 무시 컬럼 매칭도 시도
                col_lower_set = {c.lower() for c in available_columns[resolved]}
                if col_ref.lower() not in col_lower_set:
                    errors.append(
                        f"테이블 '{resolved}'에 컬럼 '{col_ref}'이 존재하지 않습니다."
                    )

    return errors


def _check_excluded_join_columns(sql: str, schema_info: dict) -> list[str]:
    """금지된 컬럼이 JOIN ON 절에 사용되었는지 감지한다.

    Args:
        sql: SQL 쿼리
        schema_info: 스키마 정보

    Returns:
        경고 메시지 목록
    """
    from src.utils.schema_utils import build_excluded_join_map

    excluded_map = build_excluded_join_map(schema_info)
    if not excluded_map:
        return []

    warnings: list[str] = []
    # ON 절 추출 (간이)
    on_clauses = re.findall(
        r"\bON\s+(.+?)(?:\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bLEFT\b|\bRIGHT\b|\bINNER\b|\bFULL\b|\bCROSS\b|\bJOIN\b|\bFETCH\b|;|$)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )

    for clause in on_clauses:
        for (table_lower, col_lower), reason in excluded_map.items():
            # alias.column 또는 table.column 패턴에서 컬럼명 매칭
            if re.search(rf"\b\w+\.{re.escape(col_lower)}\b", clause, re.IGNORECASE):
                warnings.append(
                    f"JOIN 금지 컬럼 '{table_lower}.{col_lower}'이 ON 절에 사용되었습니다. "
                    f"사유: {reason}. hostname 값 기반 브릿지 조인을 사용하세요."
                )
    return warnings


def _validate_forbidden_joins(sql: str, schema_info: dict) -> list[str]:
    """EAV 프로필에서 금지된 조인 패턴을 검출한다.

    _structure_meta의 EAV 패턴에서 entity_table, config_table,
    excluded_join_columns 정보를 사용하여 다음 패턴을 감지한다:

    1. entity_table.id = config_table.configuration_id 직접 조인
       (서로 다른 ID 체계이므로 잘못된 결과 반환)
    2. excluded_join_columns에 정의된 컬럼이 config_table과의 조인에 사용되는 패턴
       (운영 DB에서 NULL 등의 이유로 사용 불가)

    Args:
        sql: SQL 쿼리 문자열
        schema_info: 스키마 정보 딕셔너리 (_structure_meta 포함)

    Returns:
        에러 메시지 목록. 금지 패턴이 없거나 EAV 프로필이 없으면 빈 리스트.
    """
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return []

    patterns = structure_meta.get("patterns", [])
    eav_patterns = [p for p in patterns if p.get("type") == "eav"]
    if not eav_patterns:
        return []

    errors: list[str] = []
    alias_map = _extract_alias_map(sql)

    # 별칭→실제 테이블명 변환 (스키마 접두사 제거, 소문자)
    def _resolve_table(ref: str) -> str:
        """별칭이면 실제 테이블명으로 변환하고, 스키마 접두사를 제거하여 bare name을 반환한다."""
        actual = alias_map.get(ref, ref)
        # 스키마 접두사 제거 (예: polestar.cmm_resource → cmm_resource)
        if "." in actual:
            actual = actual.rsplit(".", 1)[1]
        return actual.lower()

    # ON 절에서 조인 조건 추출: alias.column = alias.column
    on_clauses = re.findall(
        r"\bON\s+(.+?)(?:\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bLEFT\b|\bRIGHT\b|\bINNER\b|\bFULL\b|\bCROSS\b|\bJOIN\b|\bFETCH\b|;|$)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )

    # 각 ON 절에서 "X.col = Y.col" 또는 "X.col = Y.col AND ..." 형태의 등호 조건 추출
    join_conditions: list[tuple[str, str, str, str]] = []
    for clause in on_clauses:
        # 등호 조건: alias.col = alias.col (AND로 연결된 복합 조건도 처리)
        eq_matches = re.findall(
            r"(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)",
            clause,
            re.IGNORECASE,
        )
        join_conditions.extend(eq_matches)

    for eav_pat in eav_patterns:
        entity_table = eav_pat.get("entity_table", "").lower()
        config_table = eav_pat.get("config_table", "").lower()
        # 스키마 접두사 제거
        if "." in entity_table:
            entity_table = entity_table.rsplit(".", 1)[1]
        if "." in config_table:
            config_table = config_table.rsplit(".", 1)[1]

        if not entity_table or not config_table:
            continue

        excluded_join_columns = eav_pat.get("excluded_join_columns", [])

        for left_ref, col_left, right_ref, col_right in join_conditions:
            actual_left = _resolve_table(left_ref)
            actual_right = _resolve_table(right_ref)
            col_left_lower = col_left.lower()
            col_right_lower = col_right.lower()

            # 패턴 1: entity_table.id = config_table.configuration_id
            if (
                actual_left == entity_table
                and col_left_lower == "id"
                and actual_right == config_table
                and col_right_lower == "configuration_id"
            ):
                errors.append(
                    f"금지된 조인 감지: {entity_table}.id = {config_table}.configuration_id 직접 조인은 "
                    f"ID 체계가 달라 잘못된 결과를 반환합니다. "
                    f"반드시 hostname 기반 브릿지 조인을 사용하세요: "
                    f"{config_table}.name='Hostname' AND {config_table}.stringvalue_short = {entity_table}.hostname 으로 "
                    f"브릿지한 후, configuration_id로 다른 속성을 조인하세요."
                )

            # 패턴 1 역방향: config_table.configuration_id = entity_table.id
            if (
                actual_left == config_table
                and col_left_lower == "configuration_id"
                and actual_right == entity_table
                and col_right_lower == "id"
            ):
                errors.append(
                    f"금지된 조인 감지: {config_table}.configuration_id = {entity_table}.id 직접 조인은 "
                    f"ID 체계가 달라 잘못된 결과를 반환합니다. "
                    f"반드시 hostname 기반 브릿지 조인을 사용하세요: "
                    f"{config_table}.name='Hostname' AND {config_table}.stringvalue_short = {entity_table}.hostname 으로 "
                    f"브릿지한 후, configuration_id로 다른 속성을 조인하세요."
                )

            # 패턴 2: excluded_join_columns에 정의된 컬럼이 config_table과의 조인에 사용
            for exc in excluded_join_columns:
                exc_table = exc.get("table", "").lower()
                exc_column = exc.get("column", "").lower()
                exc_reason = exc.get("reason", "NULL")
                # 스키마 접두사 제거
                if "." in exc_table:
                    exc_table = exc_table.rsplit(".", 1)[1]

                if not exc_table or not exc_column:
                    continue

                # 왼쪽이 excluded 컬럼, 오른쪽이 config_table
                if (
                    actual_left == exc_table
                    and col_left_lower == exc_column
                    and actual_right == config_table
                ):
                    errors.append(
                        f"금지된 조인 감지: {exc_table}.{exc_column}이 {config_table}과의 조인에 사용되었습니다. "
                        f"사유: {exc_reason}. "
                        f"반드시 hostname 기반 브릿지 조인을 사용하세요: "
                        f"{config_table}.name='Hostname' AND {config_table}.stringvalue_short = {entity_table}.hostname 으로 "
                        f"브릿지한 후, configuration_id로 다른 속성을 조인하세요."
                    )

                # 역방향: 오른쪽이 excluded 컬럼, 왼쪽이 config_table
                if (
                    actual_right == exc_table
                    and col_right_lower == exc_column
                    and actual_left == config_table
                ):
                    errors.append(
                        f"금지된 조인 감지: {exc_table}.{exc_column}이 {config_table}과의 조인에 사용되었습니다. "
                        f"사유: {exc_reason}. "
                        f"반드시 hostname 기반 브릿지 조인을 사용하세요: "
                        f"{config_table}.name='Hostname' AND {config_table}.stringvalue_short = {entity_table}.hostname 으로 "
                        f"브릿지한 후, configuration_id로 다른 속성을 조인하세요."
                    )

    return errors


def _has_limit_clause(sql: str) -> bool:
    """LIMIT 절이 있는지 확인한다.

    Args:
        sql: SQL 쿼리

    Returns:
        LIMIT 절 존재 여부
    """
    return bool(
        re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE)
        or re.search(r"\bFETCH\s+FIRST\s+\d+\s+ROWS?\s+ONLY\b", sql, re.IGNORECASE)
    )


def _add_limit_clause(sql: str, limit: int, db_engine: str = "postgresql") -> str:
    """SQL에 행 제한 절을 추가한다.

    DB 엔진에 따라 적절한 형식을 사용한다:
    - DB2: FETCH FIRST N ROWS ONLY
    - 그 외: LIMIT N

    Args:
        sql: SQL 쿼리
        limit: 행 제한 값
        db_engine: DB 엔진 타입 ("postgresql", "db2" 등)

    Returns:
        행 제한 절이 추가된 SQL
    """
    sql = sql.rstrip().rstrip(";")
    if db_engine == "db2":
        return f"{sql}\nFETCH FIRST {limit} ROWS ONLY;"
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
