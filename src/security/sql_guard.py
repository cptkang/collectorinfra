"""SQL 안전성 검사 유틸리티.

생성된 SQL 쿼리의 안전성을 검사하여 위험한 명령이나
인젝션 패턴을 탐지한다. SELECT 문 외의 SQL은 절대 허용하지 않는다.
"""

from __future__ import annotations

import re

import sqlparse


# 금지 키워드 (대문자)
FORBIDDEN_SQL_KEYWORDS: frozenset[str] = frozenset({
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

# SQL 인젝션 패턴
INJECTION_PATTERNS: list[str] = [
    # 기존 패턴
    r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE)",
    r"UNION\s+(ALL\s+)?SELECT",
    r"/\*.*?\*/",
    r"(xp_|sp_)\w+",
    r"INTO\s+(OUTFILE|DUMPFILE)",
    r"\bsys\.\w+",

    # 추가 패턴
    r"BENCHMARK\s*\(",            # MySQL 시간 기반 인젝션
    r"SLEEP\s*\(",                # 시간 지연 공격
    r"LOAD_FILE\s*\(",            # 파일 읽기 시도
    r"@@\w+",                     # 시스템 변수 접근
    r"INFORMATION_SCHEMA\.",      # 스키마 직접 접근 시도
    r"CHAR\s*\(\s*\d+",          # 문자열 인코딩 우회
    r"CONCAT\s*\(.+SELECT",      # CONCAT으로 감싼 서브쿼리
]


class SQLGuard:
    """SQL 쿼리의 안전성을 검사한다.

    query_validator 노드와 함께 이중 검증 체계를 구성한다.
    Layer 1: DBHub TOML 설정 (readonly=true)
    Layer 2: 본 모듈의 애플리케이션 레벨 검사
    """

    def detect_forbidden_keywords(
        self,
        sql: str,
        forbidden: frozenset[str] | None = None,
    ) -> list[str]:
        """금지된 키워드를 탐지한다. 주석을 제거한 후 검사한다.

        주석 내의 키워드는 오탐이므로 sqlparse로 주석을 제거하고,
        문자열 리터럴 내부도 제거한 뒤 검사한다.

        Args:
            sql: SQL 쿼리
            forbidden: 금지 키워드 집합 (기본: FORBIDDEN_SQL_KEYWORDS)

        Returns:
            감지된 금지 키워드 목록
        """
        if forbidden is None:
            forbidden = FORBIDDEN_SQL_KEYWORDS

        # 주석 제거
        sql_clean = sqlparse.format(sql, strip_comments=True)

        # 문자열 리터럴 내부도 제거 (안전을 위해)
        sql_clean = re.sub(r"'[^']*'", "''", sql_clean)

        # SQL을 토큰화하여 키워드만 추출
        tokens = re.findall(r'\b([A-Z_]+)\b', sql_clean.upper())
        return [t for t in tokens if t in forbidden]

    def detect_injection_patterns(
        self,
        sql: str,
        patterns: list[str] | None = None,
    ) -> list[str]:
        """SQL 인젝션 패턴을 탐지한다.

        Args:
            sql: SQL 쿼리
            patterns: 검사할 정규식 패턴 목록 (기본: INJECTION_PATTERNS)

        Returns:
            감지된 패턴 설명 목록
        """
        if patterns is None:
            patterns = INJECTION_PATTERNS

        detected: list[str] = []
        for pattern in patterns:
            if re.search(pattern, sql, re.IGNORECASE | re.MULTILINE):
                detected.append(pattern)
        return detected

    def is_safe_select(self, sql: str) -> tuple[bool, str]:
        """SQL이 안전한 SELECT 문인지 종합 검사한다.

        Args:
            sql: SQL 쿼리

        Returns:
            (안전 여부, 사유) 튜플
        """
        # 금지 키워드 검사
        forbidden_found = self.detect_forbidden_keywords(sql)
        if forbidden_found:
            return False, f"금지된 키워드가 포함됨: {', '.join(forbidden_found)}"

        # 인젝션 패턴 검사
        injections = self.detect_injection_patterns(sql)
        if injections:
            return False, f"SQL 인젝션 위험 패턴 감지: {len(injections)}건"

        # 다중 문장 검증 개선: sqlparse로 파싱하여 문장 수 확인
        statements = sqlparse.parse(sql)
        non_empty = [s for s in statements if s.get_type() is not None]
        if len(non_empty) > 1:
            return False, f"다중 SQL 문이 감지됨 ({len(non_empty)}개)"

        return True, "안전한 SELECT 문"
