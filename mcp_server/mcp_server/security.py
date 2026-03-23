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


class ReadOnlyViolationError(Exception):
    """읽기 전용 정책 위반 시 발생하는 예외."""

    def __init__(self, reason: str, sql: str = "") -> None:
        self.reason = reason
        self.sql = sql
        super().__init__(f"읽기 전용 위반: {reason}")


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

    # 주석 제거
    sql_clean = sqlparse.format(sql, strip_comments=True)

    # 문자열 리터럴 제거 (내부 키워드 오탐 방지)
    sql_clean = re.sub(r"'[^']*'", "''", sql_clean)

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
