"""읽기 전용 SQL 가드.

MCP 서버 레벨에서 SQL의 읽기 전용 여부를 검증한다.
src/security/sql_guard.py와 독립적으로 자체 구현하여
이중 방어(클라이언트 + 서버)를 구성한다.
"""

from __future__ import annotations

import re

import sqlparse

# 금지 키워드 (대문자) - DML, DDL, DCL, 관리 명령 포함
FORBIDDEN_KEYWORDS: frozenset[str] = frozenset({
    # DML
    "INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE",
    # DDL
    "CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME",
    # DCL
    "GRANT", "REVOKE",
    # 프로시저/함수
    "EXEC", "EXECUTE", "CALL",
    # 관리 명령
    "SHUTDOWN", "KILL",
})


# 폴스타 도메인 deny — execute_sql 옵트인 노출 시에만 추가 검증한다 (§6, D-022/D-028).
# 데이터는 core_config_prop EAV(Vendor/OSType/OSParameter)에 있으므로 아래 lookup
# 테이블 직접 참조와 RESOURCE_CONF_ID↔CONFIGURATION_ID 조인은 잘못된 결과를 낳는다.
POLESTAR_FORBIDDEN_TABLES: frozenset[str] = frozenset({
    "CMM_VENDOR",   # D-028: vendor_id lookup 금지
    "CMM_OS",       # D-028: os_id lookup 금지
    "CMM_OS_PARAM", # D-028: os_param_id lookup 금지
})

# RESOURCE_CONF_ID = CONFIGURATION_ID 조인(D-022 금지) — 양방향·테이블 한정자 허용.
_RESOURCE_CONF_JOIN_RE = re.compile(
    r"\b(?:[A-Z_][A-Z0-9_]*\.)?RESOURCE_CONF_ID\s*=\s*(?:[A-Z_][A-Z0-9_]*\.)?CONFIGURATION_ID\b"
    r"|\b(?:[A-Z_][A-Z0-9_]*\.)?CONFIGURATION_ID\s*=\s*(?:[A-Z_][A-Z0-9_]*\.)?RESOURCE_CONF_ID\b",
    re.IGNORECASE,
)


class ReadOnlyViolationError(Exception):
    """읽기 전용 정책 위반 시 발생하는 예외."""

    def __init__(self, reason: str, sql: str = "") -> None:
        self.reason = reason
        self.sql = sql
        super().__init__(f"읽기 전용 위반: {reason}")


class PolestarDomainViolationError(Exception):
    """폴스타 도메인 금지 패턴(D-022/D-028) 위반 시 발생하는 예외."""

    def __init__(self, reason: str, sql: str = "") -> None:
        self.reason = reason
        self.sql = sql
        super().__init__(f"폴스타 도메인 위반: {reason}")


def _clean_sql(sql: str) -> str:
    """주석을 제거하고 문자열 리터럴을 마스킹한다(내부 키워드 오탐 방지)."""
    # 주석 제거
    sql_clean = sqlparse.format(sql, strip_comments=True)
    # 문자열 리터럴 제거 (내부 키워드 오탐 방지)
    return re.sub(r"'[^']*'", "''", sql_clean)


def validate_readonly(sql: str) -> None:
    """SQL이 읽기 전용인지 검증한다. 위반 시 예외를 발생시킨다.

    검증 항목:
    1. 금지 키워드 검사 (주석/문자열 리터럴 제거 후)
    2. 다중 문장 검사
    3. SQL 인젝션 패턴 기본 검사

    Args:
        sql: 검증할 SQL 문자열

    Raises:
        ReadOnlyViolationError: 읽기 전용 위반 시
    """
    if not sql or not sql.strip():
        raise ReadOnlyViolationError("빈 SQL", sql)

    # 주석 제거 + 문자열 리터럴 마스킹
    sql_clean = _clean_sql(sql)

    # 금지 키워드 검사
    tokens = re.findall(r"\b([A-Z_]+)\b", sql_clean.upper())
    found = [t for t in tokens if t in FORBIDDEN_KEYWORDS]
    if found:
        raise ReadOnlyViolationError(
            f"금지된 키워드: {', '.join(set(found))}", sql
        )

    # 다중 문장 검사
    statements = sqlparse.parse(sql)
    non_empty = [s for s in statements if s.get_type() is not None]
    if len(non_empty) > 1:
        raise ReadOnlyViolationError(
            f"다중 SQL 문 감지 ({len(non_empty)}개)", sql
        )

    # 세미콜론 뒤 위험 패턴 검사
    if re.search(
        r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE)",
        sql_clean,
        re.IGNORECASE,
    ):
        raise ReadOnlyViolationError("세미콜론 뒤 위험한 SQL 감지", sql)


def validate_polestar_domain(sql: str) -> None:
    """폴스타 도메인 금지 패턴을 검증한다(D-022/D-028). 위반 시 예외를 발생시킨다.

    `execute_sql`을 옵트인(`expose_execute_sql=true`)으로 노출할 때만 추가로 적용한다.
    고수준 도구는 SQL을 받지 않으므로(값 인자만) 이 검증 대상이 아니다.

    검증 항목:
    1. `RESOURCE_CONF_ID` = `CONFIGURATION_ID` 조인 (D-022 — hostname 브릿지 조인만 허용)
    2. `cmm_vendor`/`cmm_os`/`cmm_os_param` lookup 테이블 참조 (D-028 — EAV 속성 사용)

    Args:
        sql: 검증할 SQL 문자열

    Raises:
        PolestarDomainViolationError: 폴스타 금지 패턴 감지 시
    """
    if not sql or not sql.strip():
        return

    # 주석 제거 + 문자열 리터럴 마스킹 (리터럴 내 테이블명 오탐 방지)
    sql_clean = _clean_sql(sql)

    # 1. RESOURCE_CONF_ID = CONFIGURATION_ID 조인 (D-022)
    if _RESOURCE_CONF_JOIN_RE.search(sql_clean):
        raise PolestarDomainViolationError(
            "RESOURCE_CONF_ID = CONFIGURATION_ID 조인 금지 (D-022 — hostname 브릿지 조인 사용)",
            sql,
        )

    # 2. 금지 lookup 테이블 참조 (D-028) — 단어 경계 매칭
    tokens = set(re.findall(r"\b([A-Z_][A-Z0-9_]*)\b", sql_clean.upper()))
    forbidden = tokens & POLESTAR_FORBIDDEN_TABLES
    if forbidden:
        raise PolestarDomainViolationError(
            f"금지된 lookup 테이블 참조: {', '.join(sorted(forbidden))} "
            "(D-028 — core_config_prop EAV 속성 사용)",
            sql,
        )
