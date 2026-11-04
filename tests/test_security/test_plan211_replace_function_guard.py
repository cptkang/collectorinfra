"""함수형 REPLACE(…) 허용 · 쓰기 문 REPLACE 차단 유지.

D-003 부기 2026-11-04 · D-363 · plans/211.
"""

import pytest

from src.security.sql_guard import FORBIDDEN_SQL_KEYWORDS, SQLGuard


@pytest.fixture
def guard():
    return SQLGuard()


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
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_replace_function_not_forbidden(guard, sql):
    assert guard.detect_forbidden_keywords(sql) == []
    assert guard.is_safe_select(sql)[0] is True


@pytest.mark.parametrize("sql", REJECTED)
def test_replace_statement_still_forbidden(guard, sql):
    assert "REPLACE" in guard.detect_forbidden_keywords(sql)
    assert guard.is_safe_select(sql)[0] is False


def test_replace_stays_in_forbidden_list():
    assert "REPLACE" in FORBIDDEN_SQL_KEYWORDS


def test_comment_between_replace_and_into_still_forbidden(guard):
    # 주석을 지운 뒤 `REPLACE  INTO`가 되어 뒤가 `(`가 아니다
    assert "REPLACE" in guard.detect_forbidden_keywords("REPLACE /* x */ INTO t VALUES (1)")
