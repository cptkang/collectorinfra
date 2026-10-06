"""plans/130 독립 검증(verify-130 · 2026-10-06) — 간선 E6 업무명 SQL의 **실행 의미**.

기존 W3 테스트는 SQL 문자열 모양만 본다. 여기서는 조립된 SQL을 실제 SQL 엔진에서 실행해
검색어의 `%`·`_`·`!`·따옴표·역슬래시가 **글자 그대로** 맞고 와일드카드로 새지 않는지 확인한다.

엔진은 표준 문자열 리터럴(역슬래시 = 보통 글자) · `LIKE … ESCAPE` 표준 의미를 따르는 메모리
SQLite다 — PostgreSQL 기본값(`standard_conforming_strings=on`)과 같은 리터럴 규칙이다. 로컬 폴스타
PG 샌드박스·DB2는 이번 검증 시점에 떠 있지 않아 실DB 실행은 미측정이다(보고서에 적는다).
"""

from __future__ import annotations

import sqlite3

import pytest

from src.db_adapters.polestar.entity_probe import build_business_lookup_sql
from src.security.sql_guard import SQLGuard

_SERVER = "server.Server"
#: (등록명, hostname, 비고, 유형, 삭제 시각)
_ROWS = [
    ("promo 50%off svc", "h-pct", "", _SERVER, None),
    ("50xxoff svc", "h-pct-decoy", "", _SERVER, None),
    ("a_b-batch", "h-us", "", _SERVER, None),
    ("axb-batch", "h-us-decoy", "", _SERVER, None),
    ("x!y-api", "h-bang", "", _SERVER, None),
    ("xy-api", "h-bang-decoy", "", _SERVER, None),
    ("o'brien-db", "h-quote", "", _SERVER, None),
    ("c:\\temp-share", "h-bs", "", _SERVER, None),
    ("c:temp-share", "h-bs-decoy", "", _SERVER, None),
    ("PAY-WAS01", "h-case", "", _SERVER, None),
    ("misc01", "h-desc", "카드 결제 승인 담당", _SERVER, None),
    ("결제 old", "h-deleted", "", _SERVER, "2026-01-01"),
    ("결제 net", "h-network", "", "network.Switch", None),
]


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE cmm_resource (name TEXT, hostname TEXT, description TEXT,"
                 " resource_type TEXT, dtime TEXT)")
    conn.executemany("INSERT INTO cmm_resource VALUES (?, ?, ?, ?, ?)", _ROWS)
    yield conn
    conn.close()


def _hosts(conn: sqlite3.Connection, terms: list[str]) -> list[str]:
    sql = build_business_lookup_sql(terms, db_engine="postgresql", db_schema=None)
    ok, reason = SQLGuard().is_safe_select(sql)
    assert ok, (reason, sql)
    return sorted(row[1] for row in conn.execute(sql).fetchall())


@pytest.mark.parametrize(("term", "expected"), [
    ("50%off", ["h-pct"]),
    ("a_b", ["h-us"]),
    ("x!y", ["h-bang"]),
    ("o'brien", ["h-quote"]),
    ("c:\\temp", ["h-bs"]),
], ids=["percent", "underscore", "escape-char", "quote", "backslash"])
def test_special_characters_match_literally_only(db, term, expected) -> None:
    assert _hosts(db, [term]) == expected


def test_case_insensitive_name_and_description_with_server_and_live_filters(db) -> None:
    assert _hosts(db, ["pay-was"]) == ["h-case"]
    # 비고만 맞는 행 포함 · 삭제 행(dtime)·서버 아닌 유형 제외
    assert _hosts(db, ["결제"]) == ["h-desc"]


def test_terms_are_or_combined(db) -> None:
    assert _hosts(db, ["a_b", "x!y"]) == ["h-bang", "h-us"]


@pytest.mark.parametrize("term", [
    "x' OR '1'='1",
    "\\' OR 1=1 --",
    "a'; DROP TABLE cmm_resource; --",
])
def test_quote_injection_stays_inside_the_literal(db, term) -> None:
    """따옴표·역슬래시로 리터럴을 닫으려는 검색어도 0행이고 표는 그대로다(표준 리터럴 규칙)."""
    assert _hosts(db, [term]) == []
    assert db.execute("SELECT COUNT(*) FROM cmm_resource").fetchone()[0] == len(_ROWS)
