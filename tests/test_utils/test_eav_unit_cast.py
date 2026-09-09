"""EAV 단위 문자열 캐스트 GB 정규화(D-199) 테스트.

폐쇄망 실측(2026-09-07 B-11): 메모리 TotalSize EAV 값이 "14.9 GB" 단위 문자열이라
NUMERIC 캐스트가 실행 거부. 단위 분포는 세 존 모두 TB/GB/MB 혼재(B0 TB 26,073건)라
단위 제거만으로는 900 MB > 14.9 GB 정렬 오류가 된다 — GB 기준 환산 CASE로 교체한다.

주의: testdata/pg 픽스처의 TotalSize는 순수 숫자(MB)라 운영 shape와 다르다 —
이 테스트는 운영 shape(단위 접미 문자열)를 기준으로 작성됐다.
"""

from __future__ import annotations

from src.security.sql_guard import SQLGuard
from src.utils.query_gen_common import (
    normalize_eav_numeric_casts,
    normalize_eav_unit_casts,
)

COLS = ("stringvalue", "stringvalue_short")


class TestUnitCastRewrite:
    def test_b11_real_shape_db2_replace_hack(self):
        """B-11 실제 executed_sql 형태 — LLM의 REPLACE('GB') 임기응변을 벗겨서 정규화.

        REPLACE는 sql_guard 금지 키워드라 남겨두면 검증기가 SQL 전체를 거부한다
        (2026-09-07 폐쇄망 실측 — 재생성 루프 유발). 가드는 REPLACE(X,'GB','')를
        X로 벗기고 단위 처리는 CASE가 원값 기준으로 수행한다.
        """
        sql = (
            "SELECT CAST(REPLACE(MAX(CASE WHEN c.resource_type = 'server.Memory' "
            "AND cc.name = 'TotalSize' THEN cc.stringvalue_short END), 'GB', '') "
            "AS DECIMAL(31, 2)) AS mem_size_gb FROM POLESTAR.cmm_resource c"
        )
        out = normalize_eav_unit_casts(sql, COLS)
        assert "LIKE '%TB%'" in out and "* 1024" in out
        assert "LIKE '%MB%'" in out and "/ 1024.0" in out
        # 별칭은 CASE 식 밖에 보존된다
        assert "END AS mem_size_gb" in out
        # LLM의 REPLACE 임기응변은 제거되고 안쪽 식만 남는다
        assert "REPLACE" not in out.upper()
        assert "MAX(CASE WHEN c.resource_type" in out

    def test_guard_output_passes_sql_guard(self):
        """가드 산출 SQL은 자체 보안 검증기(금지 키워드)를 통과해야 한다.

        1차 구현이 REPLACE 함수를 써서 검증기 거부 → 재생성 루프를 유발했던
        실측 회귀(2026-09-07)를 고정하는 테스트.
        """
        sql = (
            "SELECT CAST(REPLACE(MAX(CASE WHEN cc.name = 'TotalSize' THEN "
            "cc.stringvalue_short END), 'GB', '') AS DECIMAL(31, 2)) AS m, "
            "SUM(cc.stringvalue_short::numeric) FROM t"
        )
        out = normalize_eav_unit_casts(sql, COLS)
        assert SQLGuard().detect_forbidden_keywords(out) == []

    def test_plain_cast_pg(self):
        sql = "SELECT CAST(cc.stringvalue_short AS NUMERIC) FROM t"
        out = normalize_eav_unit_casts(sql, COLS)
        assert out != sql
        assert "WHEN UPPER(cc.stringvalue_short) LIKE '%GB%'" in out
        # 단위 없는 값은 ELSE에서 스케일 불변 캐스트
        assert "ELSE CAST(NULLIF(TRIM(cc.stringvalue_short), '') AS DECIMAL(31, 6)) END" in out

    def test_pg_shorthand_cast_with_prefix(self):
        sql = "SELECT SUM(cc.stringvalue_short::numeric) FROM t"
        out = normalize_eav_unit_casts(sql, COLS)
        assert "::numeric" not in out
        assert out.startswith("SELECT SUM(CASE")

    def test_pg_shorthand_cast_parenthesized(self):
        sql = "SELECT (COALESCE(cc.stringvalue_short, '0'))::numeric FROM t"
        out = normalize_eav_unit_casts(sql, COLS)
        assert "::numeric" not in out
        assert "COALESCE(cc.stringvalue_short, '0')" in out

    def test_unrelated_cast_untouched(self):
        sql = "SELECT CAST(r.id AS NUMERIC), CAST(cnt AS DECIMAL(10, 2)) FROM t"
        assert normalize_eav_unit_casts(sql, COLS) == sql

    def test_int_cast_untouched_without_d160(self):
        """정수 캐스트는 D-160 소관 — 이 가드는 NUMERIC/DECIMAL만 본다."""
        sql = "SELECT CAST(cc.stringvalue_short AS BIGINT) FROM t"
        assert normalize_eav_unit_casts(sql, COLS) == sql

    def test_chained_with_d160(self):
        """D-160(INT→NUMERIC) 뒤에 체인되면 정수 캐스트도 최종 단위 정규화된다."""
        sql = "SELECT SUM(CAST(cc.stringvalue_short AS BIGINT)) FROM t"
        step1 = normalize_eav_numeric_casts(sql, COLS)
        out = normalize_eav_unit_casts(step1, COLS)
        assert "BIGINT" not in out
        assert "LIKE '%TB%'" in out

    def test_idempotent(self):
        """재적용해도 이중 래핑되지 않는다(재시도 경로 안전)."""
        sql = "SELECT CAST(cc.stringvalue_short AS NUMERIC) FROM t"
        once = normalize_eav_unit_casts(sql, COLS)
        twice = normalize_eav_unit_casts(once, COLS)
        assert twice == once

    def test_no_value_columns_noop(self):
        sql = "SELECT CAST(cc.stringvalue_short AS NUMERIC) FROM t"
        assert normalize_eav_unit_casts(sql, ()) == sql
        assert normalize_eav_unit_casts("", COLS) == ""

    def test_where_comparison_wrapped(self):
        """비교 문맥(WHERE/HAVING)에서도 교체된다 — CASE 식은 비교 피연산자로 유효."""
        sql = "SELECT 1 FROM t WHERE CAST(cc.stringvalue_short AS NUMERIC) >= 8"
        out = normalize_eav_unit_casts(sql, COLS)
        assert "END >= 8" in out

    def test_unbalanced_paren_safe(self):
        """괄호 불균형은 무변경(하방 안전)."""
        sql = "SELECT CAST(cc.stringvalue_short AS NUMERIC"
        assert normalize_eav_unit_casts(sql, COLS) == sql
