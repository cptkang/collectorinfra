"""함수형 REPLACE(…) 허용 · 쓰기 문 REPLACE 차단 유지.

D-003 부기 2026-11-04 · D-363 · plans/211.
"""

import pytest
from mcp_server.security import FORBIDDEN_KEYWORDS, ReadOnlyViolationError, validate_readonly

ALLOWED = [
    "SELECT REPLACE(hostname, '-', '') FROM t",
    "SELECT 1 FROM t WHERE REPLACE(REPLACE(LOWER(TRIM(x)), '-', ''), '_', '') = 'a'",
    "SELECT REPLACE (x, '-', '') FROM t",
    "SELECT REPLACE\n(x, '-', '') FROM t",
    "select replace(x, '-', '') from t",
    "SELECT REGEXP_REPLACE(x, '[-_]', '') FROM t",
]

REJECTED = [
    "REPLACE INTO t VALUES (1)",
    "REPLACE INTO t(a) VALUES(1)",
    "REPLACE t (a) VALUES (1)",
    "REPLACE\nINTO t VALUES (1)",
    "REPLACE LOW_PRIORITY INTO t VALUES (1)",
    "SELECT 1; REPLACE INTO t VALUES (1)",
    "WITH x AS (SELECT 1) REPLACE INTO t SELECT * FROM x",
    "SELECT REPLACE(x, '-', '') FROM t; REPLACE INTO t VALUES (1)",
    "REPLACE /* x */ INTO t VALUES (1)",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_replace_function_allowed(sql):
    validate_readonly(sql)


@pytest.mark.parametrize("sql", REJECTED)
def test_replace_statement_rejected(sql):
    with pytest.raises(ReadOnlyViolationError):
        validate_readonly(sql)


@pytest.mark.parametrize("sql", REJECTED[:5] + REJECTED[-1:])
def test_replace_statement_rejected_by_keyword(sql):
    # 문장 유형 검사보다 앞선 금지 키워드 검사가 REPLACE를 잡는다(이중 방어)
    with pytest.raises(ReadOnlyViolationError, match="REPLACE"):
        validate_readonly(sql)


def test_replace_stays_in_forbidden_list():
    assert "REPLACE" in FORBIDDEN_KEYWORDS
