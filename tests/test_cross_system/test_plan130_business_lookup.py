"""plans/130 W3 M-3 — 간선 E6 업무명 고정 조회 SQL(폴스타 어댑터 · LLM 0 · 읽기 전용).

고정하는 계약:
  1. 방언 2종 — PostgreSQL `LIMIT 1000` · DB2 `FETCH FIRST 1000 ROWS ONLY` + 대문자 스키마(D-057).
  2. 등록명·비고 두 칼럼 대소문자 무시 부분 일치 · 서버 유형 · 삭제 제외.
  3. 검색어 `%`·`_`·`!` 이스케이프(`ESCAPE '!'` — PG `standard_conforming_strings` 무관) · 따옴표
     이중화 · 빈·공백·2자 미만 검색어 제외.
  4. 읽기 전용(`SQLGuard.is_safe_select`) — 검색어에 DML 단어·`;`·주석 기호가 있어도 리터럴 안이다.
  5. 어댑터 위임(`PolestarAdapter.business_lookup_sql`).
DB 없음(순수 문자열 조립).
"""

from __future__ import annotations

import pytest

from src.db_adapters import get_adapter
from src.db_adapters.polestar.entity_probe import (
    BUSINESS_LOOKUP_ROW_LIMIT,
    build_business_lookup_sql,
)
from src.security.sql_guard import SQLGuard


def _pg(terms: list[str]) -> str:
    return build_business_lookup_sql(terms, db_engine="postgresql", db_schema="polestar")


def _db2(terms: list[str]) -> str:
    return build_business_lookup_sql(terms, db_engine="db2", db_schema="POLESTAR")


def test_postgresql_dialect_and_filters() -> None:
    sql = _pg(["이미지 업무"])
    assert sql.startswith(
        "SELECT r.name AS server_name, r.hostname AS hostname, r.description AS description\n")
    assert "FROM polestar.cmm_resource r" in sql
    assert "r.resource_type = 'server.Server'" in sql and "r.dtime IS NULL" in sql
    assert "LOWER(r.name) LIKE '%이미지 업무%' ESCAPE '!'" in sql
    assert "LOWER(r.description) LIKE '%이미지 업무%' ESCAPE '!'" in sql
    assert sql.rstrip().endswith("LIMIT 1000") and BUSINESS_LOOKUP_ROW_LIMIT == 1000


def test_db2_dialect_and_schema() -> None:
    sql = _db2(["이미지 업무"])
    assert "FROM POLESTAR.cmm_resource r" in sql
    assert sql.rstrip().endswith("FETCH FIRST 1000 ROWS ONLY") and "LIMIT" not in sql
    assert "LOWER(r.description) LIKE '%이미지 업무%' ESCAPE '!'" in sql


def test_unqualified_when_schema_empty() -> None:
    sql = build_business_lookup_sql(["ab"], db_engine="postgresql", db_schema=None)
    assert "FROM cmm_resource r" in sql


def test_like_wildcards_escape_char_and_quote_are_escaped() -> None:
    sql = _pg(["50%_x!y\\z", "O'K"])
    # %·_·! 이스케이프 · 역슬래시는 리터럴
    assert "LOWER(r.name) LIKE '%50!%!_x!!y\\z%' ESCAPE '!'" in sql
    assert "LOWER(r.name) LIKE '%o''k%' ESCAPE '!'" in sql, "따옴표 이중화 + 소문자"


def test_blank_short_and_duplicate_terms_are_dropped() -> None:
    sql = _pg(["", "   ", "a", " b ", "AB", "ab", " 결제 "])
    assert sql.count("LOWER(r.name) LIKE") == 2, "ab·결제 두 개만(대소문자 중복 제거)"
    assert "'%ab%'" in sql and "'%결제%'" in sql
    assert "'%a%'" not in sql and "'%b%'" not in sql, "2자 미만은 과다 일치라 버린다"


def test_no_usable_term_yields_empty_select() -> None:
    sql = _pg(["", " x "])
    assert "AND (1 = 0)" in sql and "LIKE" not in sql
    assert SQLGuard().is_safe_select(sql)[0]


@pytest.mark.parametrize("terms", [
    ["이미지 업무"],
    ["delete from", "drop;table"],
    ["a;b", "x--y", "/*c*/"],
    ["50%_x\\y", "o'k", "ab\\"],
])
def test_read_only_select_for_both_dialects(terms: list[str]) -> None:
    for sql in (_pg(terms), _db2(terms)):
        ok, reason = SQLGuard().is_safe_select(sql)
        assert ok, reason
        assert sql.lstrip().upper().startswith("SELECT")


def test_adapter_delegates_to_builder() -> None:
    build = getattr(get_adapter("polestar_b0", {"polestar_b0"}), "business_lookup_sql", None)
    assert callable(build), "선택 훅 계약 — 호출부는 getattr 로 찾는다"
    got = build(["이미지 업무"], db_engine="db2", db_schema="POLESTAR")
    assert got == _db2(["이미지 업무"])
