"""EAV 값 컬럼 정수 캐스트 → NUMERIC 결정적 교정 테스트 (D-160 FIX-D).

폐쇄망 실측(2026-08-21 공동존): LLM이 "vcore 수" 집계에
``SUM(CAST(cc.stringvalue_short AS BIGINT))``를 생성 — EAV 값이 부동소수 표기
문자열('4.0')이라 PostgreSQL이 실행을 거부했다. 교정 가드의 형태 매트릭스와
오교정 방지(스코프 밖 보존)를 함께 고정한다.
"""

from __future__ import annotations

from src.utils.query_gen_common import (
    eav_value_cast_columns,
    normalize_eav_numeric_casts,
)

_COLS = ("stringvalue", "stringvalue_short")

# 폐쇄망 실측 실패 SQL의 핵심부 (audit log 전사, 2026-08-21)
_REAL_FAILED_SQL = (
    "SELECT COALESCE(srv.name, srv.hostname) AS server_name, "
    "SUM(CAST(cc.stringvalue_short AS BIGINT)) AS vcore_count "
    "FROM polestar.cmm_resource r"
)


class TestCastFormMatrix:
    """교정 대상 형태 전수 — CAST(... AS 정수형) · ::정수형 · 괄호식."""

    def test_real_failed_sql_corrected(self):
        out = normalize_eav_numeric_casts(_REAL_FAILED_SQL, _COLS)
        assert "SUM(CAST(cc.stringvalue_short AS NUMERIC))" in out
        assert "BIGINT" not in out.upper()

    def test_cast_as_int_variants(self):
        for t in ("INT", "INTEGER", "SMALLINT", "INT2", "INT4", "INT8"):
            sql = f"SELECT CAST(cc.stringvalue AS {t}) FROM t"
            out = normalize_eav_numeric_casts(sql, _COLS)
            assert "AS NUMERIC" in out, t

    def test_case_insensitive(self):
        out = normalize_eav_numeric_casts(
            "select cast(cc.stringvalue_short as bigint) from t", _COLS
        )
        assert "as NUMERIC" in out

    def test_pg_shorthand_cast(self):
        out = normalize_eav_numeric_casts(
            "SELECT cc.stringvalue_short::bigint FROM t", _COLS
        )
        assert "cc.stringvalue_short::NUMERIC" in out

    def test_pg_shorthand_parenthesized(self):
        out = normalize_eav_numeric_casts(
            "SELECT (COALESCE(cc.stringvalue_short, '0'))::int8 FROM t", _COLS
        )
        assert "::NUMERIC" in out

    def test_nested_one_level_function(self):
        out = normalize_eav_numeric_casts(
            "SELECT SUM(CAST(COALESCE(cc.stringvalue_short, '0') AS BIGINT)) FROM t",
            _COLS,
        )
        assert "AS NUMERIC" in out

    def test_multiple_occurrences_all_corrected(self):
        sql = (
            "SELECT CAST(a.stringvalue AS BIGINT), CAST(b.stringvalue_short AS INT) "
            "FROM t"
        )
        out = normalize_eav_numeric_casts(sql, _COLS)
        assert out.upper().count("AS NUMERIC") == 2

    def test_where_clause_comparison(self):
        out = normalize_eav_numeric_casts(
            "SELECT 1 FROM t WHERE CAST(cc.stringvalue_short AS BIGINT) > 2", _COLS
        )
        assert "AS NUMERIC" in out


class TestScopePreservation:
    """오교정 방지 — 값 컬럼 무관 캐스트·기존 NUMERIC·미매칭은 불변."""

    def test_unrelated_int_cast_preserved(self):
        sql = "SELECT CAST(r.id AS BIGINT) FROM t"
        assert normalize_eav_numeric_casts(sql, _COLS) == sql

    def test_already_numeric_unchanged(self):
        sql = "SELECT SUM(CAST(cc.stringvalue_short AS NUMERIC)) FROM t"
        assert normalize_eav_numeric_casts(sql, _COLS) == sql

    def test_numeric_shorthand_unchanged(self):
        sql = "SELECT cc.stringvalue_short::numeric FROM t"
        assert normalize_eav_numeric_casts(sql, _COLS) == sql

    def test_no_value_columns_noop(self):
        assert normalize_eav_numeric_casts(_REAL_FAILED_SQL, ()) == _REAL_FAILED_SQL

    def test_empty_sql_noop(self):
        assert normalize_eav_numeric_casts("", _COLS) == ""

    def test_mixed_only_value_cast_corrected(self):
        sql = (
            "SELECT CAST(r.id AS BIGINT) AS rid, "
            "CAST(cc.stringvalue_short AS BIGINT) AS core FROM t"
        )
        out = normalize_eav_numeric_casts(sql, _COLS)
        assert "CAST(r.id AS BIGINT)" in out
        assert "CAST(cc.stringvalue_short AS NUMERIC)" in out

    def test_idempotent(self):
        once = normalize_eav_numeric_casts(_REAL_FAILED_SQL, _COLS)
        assert normalize_eav_numeric_casts(once, _COLS) == once


class TestValueColumnDerivation:
    """eav_pattern 선언 → 교정 대상 컬럼 패밀리 도출 (리터럴은 선언에서만, D-088)."""

    def test_short_column_expands_to_family(self):
        assert eav_value_cast_columns({"value_column": "stringvalue_short"}) == (
            "stringvalue", "stringvalue_short",
        )

    def test_base_column_expands_to_family(self):
        assert eav_value_cast_columns({"value_column": "stringvalue"}) == (
            "stringvalue", "stringvalue_short",
        )

    def test_no_pattern_returns_empty(self):
        assert eav_value_cast_columns(None) == ()
        assert eav_value_cast_columns({}) == ()
