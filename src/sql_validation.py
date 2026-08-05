"""SQL 검증 코어 — 상태 비결합 순수 함수 (Plan 69 후속 2단계).

``query_validator`` 노드와 단계적 도출 루프의 사전 검증 도구(``validate_sql_draft``)가
**같은 코어**를 쓴다. 종전에는 이 코어가 노드 모듈에 있어 도구 계층이 노드를 역참조했고
(`tools → nodes`), 노드도 도구를 쓰려면 함수 지역 임포트로 순환을 피해야 했다. 코어를
노드·도구 어느 쪽에도 속하지 않는 독립 모듈로 내려 의존을 **nodes → tools 단방향**으로
정리했다. ``src/tools``에 두지 않은 이유는 그 계층이 DB 특화 리터럴 금지를 테스트로
강제하는데(``tests/test_tools/test_db_agnostic.py``), 이 코어의 EAV 조인 검증이 폴스타
리터럴을 보유하기 때문이다(리터럴의 어댑터 이관은 별건 D-088 작업).

여기 있는 것은 전부 상태(AgentState)·설정(AppConfig)·감사 로거에 결합하지 않는 순수
함수다 — 노드는 state에서 인자를 뽑아 이 코어를 호출하고 감사 로그·State 변환만 맡는다.
DB 특화 검증은 호출부가 어댑터 레지스트리에서 조회해 ``adapter_checks``로 주입한다(D-089).

계층: application — ``src.nodes``·``src.tools``와 동일(``scripts/arch_check.py`` 참조).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import sqlparse

from src.security.sql_guard import FORBIDDEN_SQL_KEYWORDS, INJECTION_PATTERNS, SQLGuard
from src.utils.sql_dialect import is_db2, row_limit_clause
from src.utils.query_gen_common import (
    MISSING_DTIME_ERROR,
    has_all_scope_keyword,
    missing_dtime_filter,
)

logger = logging.getLogger(__name__)


@dataclass
class SQLValidationOutcome:
    """상태 비결합 SQL 검증 결과.

    Attributes:
        errors: 재생성을 유도해야 하는 검증 실패 사유(하나라도 있으면 불합격)
        warnings: 통과하되 사용자/로그에 남길 경고
        auto_fixed_sql: 행 제한 절 자동 보정본(보정하지 않았으면 None)
        forbidden_keywords: 감지된 금지 키워드(감사 로그용)
        injection_count: 감지된 인젝션 패턴 수(감사 로그용)
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    auto_fixed_sql: Optional[str] = None
    forbidden_keywords: list[str] = field(default_factory=list)
    injection_count: int = 0

    @property
    def passed(self) -> bool:
        """검증 통과 여부."""
        return not self.errors


def validate_sql(
    sql: str,
    schema_info: dict,
    *,
    db_engine: str = "postgresql",
    user_query: str = "",
    default_limit: int = 100,
    adapter_checks: Sequence[Callable[[str], list[str]]] = (),
) -> SQLValidationOutcome:
    """생성된 SQL을 규칙 기반으로 검증한다(상태·설정 비결합 코어).

    ``query_validator`` 노드의 검증 본문을 그대로 옮긴 순수 함수다. 검사 순서·메시지·
    자동 보정 동작은 노드 시절과 동일하며(동작 불변), DB 특화 검증은 호출부가 주입한
    ``adapter_checks``로만 수행한다(공용 코어는 DB를 모른다 — D-088/D-089).

    Args:
        sql: 검증 대상 SQL
        schema_info: 스키마 정보(tables·_structure_meta 포함)
        db_engine: DB 엔진 타입("postgresql"·"db2" 등 — 행 제한 절 방언 결정)
        user_query: 사용자 원문 질의("모든/전체" 조회면 LIMIT 자동 추가 생략)
        default_limit: 행 제한 절 자동 추가 시 사용할 기본값
        adapter_checks: DB 어댑터 전용 검증 함수들(SQL → 오류 메시지 목록)

    Returns:
        SQLValidationOutcome — 오류·경고·자동 보정 SQL·감사 신호
    """
    guard = SQLGuard()
    errors: list[str] = []
    warnings: list[str] = []
    auto_fixed_sql: Optional[str] = None
    forbidden: list[str] = []
    injection_count = 0

    # 1. SQL 파싱 가능 여부
    try:
        parsed = sqlparse.parse(sql)
        if not parsed:
            errors.append("SQL을 파싱할 수 없습니다.")
    except Exception as e:
        errors.append(f"SQL 파싱 에러: {str(e)}")
        return SQLValidationOutcome(errors=errors)

    # 2. SELECT 문 여부 확인
    statement_type = _get_statement_type(sql, parsed=parsed)  # 파스 재사용(Plan 69 P4-5)
    if statement_type != "SELECT":
        errors.append(f"SELECT 문만 허용됩니다. 감지된 타입: {statement_type}")

    # 3. 금지 키워드 확인
    forbidden = guard.detect_forbidden_keywords(sql, FORBIDDEN_SQL_KEYWORDS)
    if forbidden:
        errors.append(f"금지된 키워드가 포함되어 있습니다: {', '.join(forbidden)}")

    # 4. SQL 인젝션 패턴 탐지
    injections = guard.detect_injection_patterns(sql, INJECTION_PATTERNS)
    injection_count = len(injections)
    if injections:
        errors.append(
            f"SQL 인젝션 위험 패턴이 감지되었습니다: {injection_count}건"
        )

    # 4.5. 따옴표 밖 자연어(한글) 토큰 잔존 검출 — LLM이 "해당"/"현재" 같은 자연어 조각을
    # SQL 구조 영역에 남기면 DB 구문 오류로 실행이 실패한다(폐쇄망 실측 2026-07-20, 2회
    # 재현·토큰 가변 — 프롬프트로는 못 막는 비결정 오류라 결정적 가드로 재생성을 유도, D-104).
    # 따옴표 안 한글(별칭 "CPU 평균", 리터럴 '서울')은 정당하므로 제외한다.
    bare_hangul = _find_bare_hangul_tokens(sql)
    if bare_hangul:
        shown = ", ".join(sorted(set(bare_hangul))[:5])
        # 메시지는 ASCII 구두점만 사용 - 이 문자열은 평가 하네스 스킵 사유로 cp949 콘솔에
        # 출력될 수 있음(em-dash는 UnicodeEncodeError, Known Mistakes 2026-07-16)
        errors.append(
            f"SQL 구조에 자연어(한글) 토큰이 남아 있습니다: {shown} - "
            "따옴표 안 별칭/문자열 리터럴 외의 한글은 모두 제거하고 완전한 SQL로 다시 작성하세요."
        )

    # 4.6. 삭제 리소스 제외 필터(dtime IS NULL) 부재 검출 — LLM이 필수 필터를 통째로
    # 누락하면 삭제된 서버가 결과에 섞인다(폐쇄망 실측 2026-07-21 b0-005: +99대).
    # 프롬프트 규칙만으로는 비결정적으로 재발하므로 결정적 가드로 재생성을 유도한다(D-104 계열).
    # 알람 뷰의 부모 조인 등 일부 별칭 무필터는 정당하므로 "SQL 전체에 한 번도 없음"만 차단한다.
    if missing_dtime_filter(sql):
        errors.append(MISSING_DTIME_ERROR)

    # 5. 참조 테이블 존재 여부 (대소문자 무시 + bare name fallback)
    referenced_tables = _extract_table_names(sql)  # 이후 검사와 공유(1회 추출)
    # CTE는 가상 테이블이므로 검증 대상에서 제외
    cte_names = _extract_cte_names(sql)
    cte_names_lower = {c.lower() for c in cte_names}
    referenced_tables = {
        t for t in referenced_tables if t.lower() not in cte_names_lower
    }
    available_tables = set(schema_info.get("tables", {}).keys())
    available_tables_lower = {t.lower() for t in available_tables}
    # bare name → schema.table fallback 매핑 구축
    bare_to_qualified: dict[str, str] = {}
    for t in available_tables:
        bare = t.rsplit(".", 1)[-1].lower()
        bare_to_qualified[bare] = t
    unknown_tables = set()
    for t in referenced_tables:
        if t.lower() not in available_tables_lower:
            bare_t = t.rsplit(".", 1)[-1].lower()
            if bare_t not in bare_to_qualified:
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

    # 6.7. LEFT JOIN 강등(outer 조인 테이블 필터의 WHERE 배치) 감지 (error → 재시도 유도, D-085)
    demotion_errors = _check_left_join_where_demotion(sql)
    errors.extend(demotion_errors)

    # 7. LIMIT 절 존재 여부
    # LIMIT 상향(resolve_query_limit)과 동일한 경계 판정을 공유한다 — 종전 인라인 부분문자열
    # 튜플은 "전체적으로 …"를 전체 조회로 오탐해 LIMIT 자동 추가를 건너뛰었다(Plan 67 R3-(iii)).
    is_all_query = has_all_scope_keyword(user_query)
    if not _has_limit_clause(sql):
        if is_all_query:
            # 모든/전체 결과 조회 질의의 경우 LIMIT 자동 추가 생략
            logger.info("모든/전체 결과 조회 질의이므로 LIMIT 자동 추가를 건너뜁니다.")
        else:
            auto_fixed_sql = _add_limit_clause(sql, default_limit, db_engine)
            if is_db2(db_engine):
                warnings.append(
                    f"행 제한 절이 없어 자동으로 FETCH FIRST {default_limit} ROWS ONLY를 추가했습니다."
                )
            else:
                warnings.append(
                    f"LIMIT 절이 없어 자동으로 LIMIT {default_limit}을 추가했습니다."
                )

    # 8. 성능 위험 패턴
    perf_warnings = _check_performance_risks(sql, schema_info, tables=referenced_tables)
    warnings.extend(perf_warnings)

    # 9. DB 어댑터 전용 검증(라우팅 필터 오용 등) — 호출부가 주입한 훅만 실행
    for _check in adapter_checks:
        errors.extend(_check(sql))

    return SQLValidationOutcome(
        errors=errors,
        warnings=warnings,
        auto_fixed_sql=auto_fixed_sql,
        forbidden_keywords=forbidden,
        injection_count=injection_count,
    )

_HANGUL_RE = re.compile(r"[가-힣]+")


def find_bare_hangul_tokens(sql: str) -> list[str]:
    """문자열 리터럴·따옴표 식별자·주석을 제거한 뒤 남는 한글 토큰을 찾는다(D-104).

    LLM 생성 SQL에 자연어 조각(지시어 "해당", "현재" 등)이 구조 영역에 잔존하면 DB가
    구문 오류를 낸다. 따옴표 안 한글(별칭 `"CPU 평균"`, 리터럴 `'서울'`)은 정당한 사용이라
    제거 후 검사한다. 문자열 리터럴은 표준 SQL의 `''` 이스케이프를 허용한다.

    Args:
        sql: 검사할 SQL 문자열

    Returns:
        구조 영역에 남은 한글 토큰 목록(없으면 빈 리스트)
    """
    body = re.sub(r"--[^\n]*", " ", sql or "")
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S)
    body = re.sub(r"'(?:[^']|'')*'", " ", body)  # 문자열 리터럴 ('' 이스케이프 포함)
    body = re.sub(r'"[^"]*"', " ", body)  # 따옴표 식별자(별칭)
    return _HANGUL_RE.findall(body)

def _get_statement_type(sql: str, parsed: object | None = None) -> str:
    """SQL 문의 타입을 판별한다.

    sqlparse는 CTE(`WITH ... SELECT`)의 get_type()을 UNKNOWN으로 반환한다(실측 2026-07-21
    gp-014: 정당한 읽기 전용 CTE 쿼리가 "SELECT 문만 허용" 검증에 원천 거부돼 재시도 소진).
    UNKNOWN이면 주석 제거 후 WITH로 시작하고 DML/DDL 키워드가 없는 경우 SELECT로 재분류한다
    (data-modifying CTE는 키워드 검사로 계속 차단).

    Args:
        sql: SQL 쿼리

    Returns:
        SQL 문 타입 문자열 (SELECT, INSERT, UNKNOWN 등)
    """
    if parsed is None:
        parsed = sqlparse.parse(sql)
    stype = (parsed[0].get_type() or "UNKNOWN") if parsed else "UNKNOWN"
    if stype == "UNKNOWN":
        body = re.sub(r"--[^\n]*", " ", sql or "")
        body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S).strip()
        if re.match(r"^WITH\b", body, re.IGNORECASE) and not re.search(
            r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE)\b",
            body, re.IGNORECASE,
        ):
            return "SELECT"
    return stype


def _extract_cte_names(sql: str) -> set[str]:
    """CTE 이름과 파생 테이블 별칭을 추출한다(가상 테이블 — 존재 검증 제외 대상).

    실측(2026-07-21 gp-014): ① SQL이 주석(`-- ...`)으로 시작하면 `^\\s*WITH` 앵커가 실패해
    CTE 이름(server_specs)이 실존 테이블 검증에 걸림 → 주석 제거 후 판정. ② 파생 테이블
    별칭(`(SELECT ...) ref`)도 가상 이름이므로 함께 제외한다(닫는 괄호 뒤 식별자는 문법상
    항상 별칭 — 실존 테이블명이 그 위치에 올 수 없음).

    테이블 추출(_extract_table_names)과 동일하게 **주석 제거 후** 판정한다 —
    생성 규칙이 SQL 선두에 `-- 설명` 주석을 강제하므로, 원본 기준 `^WITH` 앵커는
    주석 달린 CTE 쿼리에서 항상 실패해 CTE가 "존재하지 않는 테이블"로 오판되는
    회귀가 있었다(2026-07-18 SYN-I-03 확장 실측, D-087).

    Args:
        sql: SQL 쿼리 문자열

    Returns:
        CTE·파생 테이블 별칭 이름 집합
    """
    cte_names: set[str] = set()
    body = re.sub(r"--[^\n]*", " ", sql or "")
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S)
    _skip_keywords = frozenset({
        "SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "RECURSIVE",
        "NOT", "CAST", "TREAT", "EXTRACT", "TRIM", "SUBSTRING",
    })
    if re.search(r"^\s*WITH\b", body, re.IGNORECASE):
        # "name AS (" 패턴에서 CTE 이름 추출
        for m in re.finditer(r"\b(\w+)\s+AS\s*\(", body, re.IGNORECASE):
            name = m.group(1)
            if name.upper() not in _skip_keywords:
                cte_names.add(name)
    # 파생 테이블 별칭: ") alias" / ") AS alias" — 뒤따르는 SQL 키워드는 별칭이 아님
    _keywords_after_paren = frozenset({
        "ON", "WHERE", "GROUP", "ORDER", "LIMIT", "FETCH", "OFFSET", "HAVING",
        "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "FULL",
        "UNION", "INTERSECT", "EXCEPT", "AND", "OR", "AS", "SELECT", "FROM",
        "WHEN", "THEN", "ELSE", "END", "IN", "NOT", "IS", "BETWEEN", "LIKE",
        "EXISTS", "DESC", "ASC", "OVER", "FILTER",
    })
    for m in re.finditer(r"\)\s*(?:AS\s+)?([A-Za-z_]\w*)", body, re.IGNORECASE):
        name = m.group(1)
        if name.upper() not in _keywords_after_paren:
            cte_names.add(name)
    return cte_names


def _clean_sql_for_table_extraction(sql: str) -> str:
    """테이블 추출 전 SQL에서 오탐 원인이 되는 요소를 제거한다.

    1. 주석 제거 (-- 주석 내 FROM이 테이블로 오인)
    2. 문자열 리터럴 제거
    3. SQL 함수 내부의 FROM 제거 (EXTRACT, SUBSTRING, TRIM 등)

    Args:
        sql: 원본 SQL

    Returns:
        정제된 SQL
    """
    # 주석 제거
    sql_clean = sqlparse.format(sql, strip_comments=True)
    # 문자열 리터럴 제거
    sql_clean = re.sub(r"'[^']*'", "''", sql_clean)
    # SQL 함수 내부의 FROM 제거 (EXTRACT(... FROM ...), SUBSTRING(... FROM ...) 등)
    sql_clean = re.sub(
        r"\b(?:EXTRACT|SUBSTRING|TRIM|OVERLAY|POSITION)\s*\([^)]*\)",
        " ",
        sql_clean,
        flags=re.IGNORECASE,
    )
    return sql_clean


def _extract_table_names(sql: str) -> set[str]:
    """SQL에서 참조하는 테이블명을 추출한다.

    FROM, JOIN 절에서 테이블명을 추출한다.
    주석, 문자열 리터럴, 함수 내 FROM은 사전에 제거하여 오탐을 방지한다.

    Args:
        sql: SQL 쿼리 문자열

    Returns:
        테이블명 집합
    """
    tables: set[str] = set()

    sql_clean = _clean_sql_for_table_extraction(sql)

    # schema.table 형태를 포함하는 식별자 패턴 (예: polestar.cmm_resource)
    _ident = r"[\w]+(?:\.[\w]+)?"

    # FROM 절 (콤마로 구분된 다중 테이블 지원: FROM t1, t2, t3)
    from_clauses = re.findall(
        rf"\bFROM\s+((?:{_ident}\s*,\s*)*{_ident})", sql_clean, re.IGNORECASE
    )
    for clause in from_clauses:
        for table in clause.split(","):
            table = table.strip()
            if table:
                # 별칭 제거 (예: "t1 AS a" → "t1", "t1 a" → "t1")
                table_name = table.split()[0]
                tables.add(table_name)

    # JOIN 절 (schema.table 형태 지원)
    join_match = re.findall(rf"\bJOIN\s+({_ident})", sql_clean, re.IGNORECASE)
    tables.update(join_match)

    # 필터링
    tables = {
        t for t in tables
        if not t.lower().startswith("information_schema")
        and t  # 빈 문자열 제외
        and not t[0].isdigit()  # 숫자로 시작하면 테이블명 아님
    }

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
        bare = t.rsplit(".", 1)[-1].lower()
        bare_to_qualified[bare] = t

    available_columns: dict[str, set[str]] = {}
    # 대소문자 무시를 위한 소문자→원본 테이블명 매핑
    table_name_lower_map: dict[str, str] = {}
    for table_name in referenced_tables:
        # 직접 매칭 → bare name fallback
        table_data = all_tables.get(table_name)
        if table_data is None:
            bare_t = table_name.rsplit(".", 1)[-1].lower()
            qualified = bare_to_qualified.get(bare_t)
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


def _eav_bridge_hint(eav_pat: dict, entity_table: str, config_table: str) -> str:
    """EAV 브릿지 조인 안내 문구를 **패턴 메타에서** 조립한다(정보가 없으면 빈 문자열).

    속성명 컬럼·값 컬럼·브릿지 속성/컬럼·config 조인 컬럼을 전부 프로필 선언에서 읽는다
    — 종전에는 특정 DB의 컬럼·속성 이름이 문구에 박혀 있어, 다른 EAV 프로필에서는 존재하지
    않는 컬럼을 쓰라는 지시가 나갔다(D-088 공용 계층 DB-agnostic).

    브릿지 선언(`value_joins`)이나 config 조인 컬럼이 없으면 **안내를 붙이지 않는다** —
    자리표시자로 조립하면 존재하지 않는 컬럼을 쓰라는 지시가 재시도 프롬프트로 들어간다.

    Args:
        eav_pat: `_structure_meta.patterns`의 EAV 패턴 항목
        entity_table/config_table: 스키마 접두사를 제거한 bare 테이블명

    Returns:
        "반드시 … 브릿지한 후, …" 안내 문장(조립 불가하면 빈 문자열)
    """
    value_joins = eav_pat.get("value_joins") or []
    bridge = value_joins[0] if value_joins else {}
    bridge_attr = bridge.get("eav_attribute")
    bridge_column = bridge.get("entity_column")
    config_column = (eav_pat.get("direct_join") or {}).get("config_column")
    if not (bridge_attr and bridge_column and config_column):
        return ""
    # 값 컬럼은 브릿지 자신의 선언을 우선하고, 없으면 패턴 기본값을 쓴다.
    # 공용 계층 폴백은 DB-agnostic 일반명이어야 한다(특정 DB 값을 기본값으로 박지 않는다).
    attribute_column = eav_pat.get("attribute_column") or "NAME"
    value_column = (
        bridge.get("eav_value_column") or eav_pat.get("value_column") or "VALUE"
    )
    return (
        f"반드시 {bridge_column} 기반 브릿지 조인을 사용하세요: "
        f"{config_table}.{attribute_column}='{bridge_attr}' AND "
        f"{config_table}.{value_column} = {entity_table}.{bridge_column} 으로 "
        f"브릿지한 후, {config_column}로 다른 속성을 조인하세요."
    )


def _validate_forbidden_joins(sql: str, schema_info: dict) -> list[str]:
    """EAV 프로필에서 금지된 조인 패턴을 검출한다.

    _structure_meta의 EAV 패턴에서 entity_table, config_table,
    excluded_join_columns 정보를 사용하여 다음 패턴을 감지한다:

    1. entity_table.id = config_table.<direct_join.config_column> 직접 조인
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
        actual = actual.rsplit(".", 1)[-1]
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
        entity_table = entity_table.rsplit(".", 1)[-1]
        config_table = config_table.rsplit(".", 1)[-1]

        if not entity_table or not config_table:
            continue

        excluded_join_columns = eav_pat.get("excluded_join_columns", [])
        # 금지 조인 판정·안내에 쓰는 컬럼명은 전부 프로필 선언에서 읽는다(D-088).
        # config 조인 컬럼 선언이 없으면 패턴 1(직접 조인)은 식별할 수 없어 건너뛴다.
        config_column = (eav_pat.get("direct_join") or {}).get("config_column") or ""
        config_column_lower = config_column.lower()
        bridge_hint = _eav_bridge_hint(eav_pat, entity_table, config_table)
        hint_suffix = f" {bridge_hint}" if bridge_hint else ""

        for left_ref, col_left, right_ref, col_right in join_conditions:
            actual_left = _resolve_table(left_ref)
            actual_right = _resolve_table(right_ref)
            col_left_lower = col_left.lower()
            col_right_lower = col_right.lower()

            # 패턴 1: entity_table.id = config_table.<config 조인 컬럼>
            if (
                config_column_lower
                and actual_left == entity_table
                and col_left_lower == "id"
                and actual_right == config_table
                and col_right_lower == config_column_lower
            ):
                errors.append(
                    f"금지된 조인 감지: {entity_table}.id = {config_table}.{config_column} 직접 조인은 "
                    f"ID 체계가 달라 잘못된 결과를 반환합니다.{hint_suffix}"
                )

            # 패턴 1 역방향: config_table.<config 조인 컬럼> = entity_table.id
            if (
                config_column_lower
                and actual_left == config_table
                and col_left_lower == config_column_lower
                and actual_right == entity_table
                and col_right_lower == "id"
            ):
                errors.append(
                    f"금지된 조인 감지: {config_table}.{config_column} = {entity_table}.id 직접 조인은 "
                    f"ID 체계가 달라 잘못된 결과를 반환합니다.{hint_suffix}"
                )

            # 패턴 2: excluded_join_columns에 정의된 컬럼이 config_table과의 조인에 사용
            for exc in excluded_join_columns:
                exc_table = exc.get("table", "").lower()
                exc_column = exc.get("column", "").lower()
                exc_reason = exc.get("reason", "NULL")
                # 스키마 접두사 제거
                exc_table = exc_table.rsplit(".", 1)[-1]

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
                        f"사유: {exc_reason}.{hint_suffix}"
                    )

                # 역방향: 오른쪽이 excluded 컬럼, 왼쪽이 config_table
                if (
                    actual_right == exc_table
                    and col_right_lower == exc_column
                    and actual_left == config_table
                ):
                    errors.append(
                        f"금지된 조인 감지: {exc_table}.{exc_column}이 {config_table}과의 조인에 사용되었습니다. "
                        f"사유: {exc_reason}.{hint_suffix}"
                    )

                # 패턴 3: excluded_join_columns 컬럼이 임의 테이블과의 조인에 사용
                # config_table 대상이 아니더라도 차단 (cmm_vendor, cmm_os 등 레거시 lookup 테이블)
                if (
                    actual_left == exc_table
                    and col_left_lower == exc_column
                    and actual_right != config_table
                ):
                    errors.append(
                        f"금지된 조인 감지: {exc_table}.{exc_column}이 JOIN ON 절에 사용되었습니다. "
                        f"사유: {exc_reason}"
                    )
                # 패턴 3 역방향
                if (
                    actual_right == exc_table
                    and col_right_lower == exc_column
                    and actual_left != config_table
                ):
                    errors.append(
                        f"금지된 조인 감지: {exc_table}.{exc_column}이 JOIN ON 절에 사용되었습니다. "
                        f"사유: {exc_reason}"
                    )

    return errors


def check_left_join_where_demotion(sql: str) -> list[str]:
    """LEFT JOIN 테이블의 컬럼이 WHERE 절에서 필터로 사용된 패턴(조인 강등)을 감지한다.

    LEFT JOIN된 테이블의 컬럼에 비교 필터를 WHERE에 두면 미매칭 행(NULL)이
    모두 탈락하여 LEFT JOIN이 INNER JOIN으로 강등된다. 피벗 패턴에서는
    측정치가 없는 엔티티 행이 제거되어 식별 컬럼(서버명 등)이 NULL로 나오는
    결함이 된다(2026-07-16 SYN-H-02 실측, D-085).

    보수적 판정(오탐 최소화)을 위해 다음은 감지 대상에서 제외한다:
    - IS NULL / IS NOT NULL 검사 (anti-join 등 정당한 패턴)
    - COALESCE() 등 함수 인자로 감싼 컬럼 참조 (NULL 처리 명시로 간주)
    - 서브쿼리 내부 (파렌 마스킹으로 전체 제외 — LEFT JOIN (SELECT ...) 포함)

    Args:
        sql: SQL 쿼리 문자열

    Returns:
        에러 메시지 목록. 강등 패턴이 없으면 빈 리스트.
    """
    # 주석·문자열 리터럴 제거 후 괄호 내부를 반복 마스킹하여
    # 서브쿼리·함수 인자·IN 리스트를 감지 대상에서 제외한다.
    # 마커는 괄호 비포함(종료 보장) + 비단어 문자(식별자·별칭 오인 방지)여야 한다.
    masked = sqlparse.format(sql, strip_comments=True)
    masked = re.sub(r"'[^']*'", "''", masked)
    prev = None
    while prev != masked:
        prev = masked
        masked = re.sub(r"\([^()]*\)", " ~ ", masked)

    _ident = r"[\w]+(?:\.[\w]+)?"

    # LEFT [OUTER] JOIN 대상 수집: 참조명(별칭 또는 bare 테이블명) → 테이블명
    left_join_refs: dict[str, str] = {}
    for m in re.finditer(
        rf"\bLEFT\s+(?:OUTER\s+)?JOIN\s+({_ident})(?:\s+AS\s+(\w+)|\s+(\w+))?",
        masked,
        re.IGNORECASE,
    ):
        table, alias_as, alias_bare = m.group(1), m.group(2), m.group(3)
        alias = alias_as or alias_bare
        if alias and alias.upper() == "ON":
            alias = None
        ref = alias or table.rsplit(".", 1)[-1]
        left_join_refs[ref.lower()] = table
    if not left_join_refs:
        return []

    # 최상위 WHERE 절 추출 (서브쿼리 WHERE는 마스킹으로 이미 제거됨)
    where_clauses = re.findall(
        r"\bWHERE\b(.*?)(?=\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b"
        r"|\bOFFSET\b|\bFETCH\b|\bUNION\b|\bINTERSECT\b|\bEXCEPT\b|;|$)",
        masked,
        re.IGNORECASE | re.DOTALL,
    )
    if not where_clauses:
        return []

    # 비교 필터 연산자 (IS NULL/IS NOT NULL은 매칭되지 않아 자연 제외)
    _op = r"(?:!=|<>|>=|<=|=|>|<|\bNOT\s+I?LIKE\b|\bI?LIKE\b|\bNOT\s+IN\b|\bIN\b|\bBETWEEN\b)"

    offending: dict[str, set[str]] = {}
    for clause in where_clauses:
        for m in re.finditer(rf"\b(\w+)\.(\w+)\s*{_op}", clause, re.IGNORECASE):
            ref, col = m.group(1).lower(), m.group(2)
            if ref in left_join_refs:
                offending.setdefault(ref, set()).add(col)

    errors: list[str] = []
    for ref in sorted(offending):
        cols = ", ".join(sorted(offending[ref]))
        errors.append(
            f"LEFT JOIN 강등 감지: LEFT JOIN 테이블 '{left_join_refs[ref]}'(참조명 '{ref}')의 "
            f"컬럼({cols})이 WHERE 절에서 필터로 사용되었습니다. "
            "outer 조인 테이블의 필터가 WHERE에 있으면 미매칭 행이 모두 제거되어 LEFT JOIN이 "
            "INNER JOIN으로 강등되고, 피벗 패턴에서는 기준 행(예: 피벗 기준 엔터티 행)이 탈락해 "
            "해당 컬럼이 NULL로 나옵니다. 해당 조건을 그 LEFT JOIN의 ON 절로 이동하세요. "
            "행 자체를 걸러내려는 의도라면 LEFT JOIN 대신 JOIN(INNER)을 사용하세요. "
            "(IS NULL / IS NOT NULL 검사는 WHERE에 두어도 됩니다)"
        )
    return errors


def _strip_parenthesized(sql: str) -> str:
    """괄호(서브쿼리 등) 내부를 제거해 최상위 레벨 텍스트만 남긴다.

    문자열 리터럴('...') 속 괄호는 깊이 계산에서 제외한다 — 리터럴의 홑괄호가
    깊이를 어긋나게 하면 외곽 LIMIT을 못 보고 이중 LIMIT을 붙일 수 있다.
    """
    out: list[str] = []
    depth = 0
    in_str = False
    for ch in sql:
        if in_str:
            if ch == "'":
                in_str = False
            if depth == 0:
                out.append(ch)
            continue
        if ch == "'":
            in_str = True
            if depth == 0:
                out.append(ch)
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def _has_limit_clause(sql: str) -> bool:
    """최상위 레벨에 행 제한 절(LIMIT/FETCH FIRST)이 있는지 확인한다.

    서브쿼리 내부 LIMIT에 오매칭되면 외곽 자동 보정이 억제되어 무제한 반환이
    가능했다(Plan 69 P0-⑦) — 괄호 내부를 제거한 최상위 텍스트만 검사한다.

    Args:
        sql: SQL 쿼리

    Returns:
        최상위 행 제한 절 존재 여부
    """
    top_level = _strip_parenthesized(sql)
    return bool(
        re.search(r"\bLIMIT\s+\d+", top_level, re.IGNORECASE)
        or re.search(r"\bFETCH\s+FIRST\s+\d+\s+ROWS?\s+ONLY\b", top_level, re.IGNORECASE)
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
    return f"{sql}\n{row_limit_clause(db_engine, limit)};"


def _check_performance_risks(
    sql: str,
    schema_info: dict,
    *,
    tables: set[str] | None = None,
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
        tables = tables if tables is not None else _extract_table_names(sql)
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
    tables = tables if tables is not None else _extract_table_names(sql)
    if len(tables) > 1 and not re.search(r"\bON\b", sql, re.IGNORECASE):
        warnings.append(
            "다중 테이블 참조에 JOIN 조건(ON)이 없습니다. 카테시안 곱 주의."
        )

    return warnings


# 하위호환 별칭 — 교차 임포트 공개화(Plan 69 P2). 신규 코드는 공개명을 쓴다.
_check_left_join_where_demotion = check_left_join_where_demotion
_find_bare_hangul_tokens = find_bare_hangul_tokens
