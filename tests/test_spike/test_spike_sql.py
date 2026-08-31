"""급증 비교 SQL 결정적 조립 (Plan 82 W9-T8 · SPEC-spike-condition).

**SQL을 문자열로 단언한다** — DB 미가동(DBHub localhost:9099 연결 거부)이라 실행 검증은
불가능하고, 방언 위반은 실행 시점에야 죽으므로 애초에 맞게 내는 것이 유일한 방어다.

판정 경계(§6.10 ①)는 SQL의 HAVING 2항으로 표현됨을 단언한다 — 파이썬으로 재구현한
판정식을 테스트하면 **SQL이 아니라 테스트를 검증**하게 된다.
"""

from __future__ import annotations

import pytest

from src.db_adapters.polestar.spike_sql import build_spike_sql


def _sql(engine: str, schema: str | None = None, **kwargs) -> str:
    params = dict(
        db_engine=engine,
        db_schema=schema,
        base_month="202606",
        cur_month="202607",
        threshold_pct=80,
        delta_pp=20,
        limit=100,
    )
    params.update(kwargs)
    return build_spike_sql(**params)


PG = _sql("postgresql", "polestar")
DB2 = _sql("db2", "POLESTAR")


class TestPostgresDialect:
    def test_decimal_cast_after_aggregate(self):
        assert "::numeric" in PG

    def test_row_limit(self):
        assert PG.rstrip().endswith("LIMIT 100")
        assert "FETCH FIRST" not in PG

    def test_lowercase_schema_qualification(self):
        assert "polestar.cmm_resource" in PG
        assert "polestar.cmm_metric_stat_m" in PG


class TestDb2Dialect:
    def test_cast_is_inside_the_aggregate(self):
        """DB2 `MAX()`는 정수 컬럼을 정수로 집계한다 — 집계 **전** 캐스트여야 소수가 보존된다."""
        assert "MAX(CASE WHEN s.stat_date = '202607' THEN CAST(s.max_val AS DOUBLE) END)" in DB2

    def test_no_postgres_cast_syntax(self):
        assert "::numeric" not in DB2, "`::numeric`은 DB2 문법 오류다"

    def test_row_limit(self):
        assert DB2.rstrip().endswith("FETCH FIRST 100 ROWS ONLY")
        assert "LIMIT 100" not in DB2

    def test_uppercase_schema_qualification(self):
        assert "POLESTAR.cmm_resource" in DB2
        assert "POLESTAR.cmm_metric_stat_m" in DB2

    def test_double_cast_avoids_overflow(self):
        """고정 정밀도 DECIMAL은 범위 밖 쓰레기 값에서 변환 오버플로로 쿼리를 죽인다(D-103)."""
        assert "AS DOUBLE" in DB2
        assert "DECIMAL(15,4)" not in DB2

    def test_dialect_guard_accepts_both(self):
        from src.nodes.multi_db_executor import _check_row_limit_dialect

        assert _check_row_limit_dialect(PG, "postgresql") is None
        assert _check_row_limit_dialect(DB2, "db2") is None


class TestSharedContract:
    @pytest.mark.parametrize("sql", [PG, DB2])
    def test_filesystem_granularity_is_preserved(self, sql):
        """서버 단위 AVG로 접으면 `/var` 30→90%가 서버 51%로 눌려 임계 미달로 놓친다(U19)."""
        assert "GROUP BY svr.name, r.name" in sql

    @pytest.mark.parametrize("sql", [PG, DB2])
    def test_two_period_scan_without_self_join(self, sql):
        assert "s.stat_date IN ('202606', '202607')" in sql
        assert sql.count("cmm_metric_stat_m") == 1, "조건부 집계라 통계 테이블 조인은 1회다"

    @pytest.mark.parametrize("sql", [PG, DB2])
    def test_value_guard_and_soft_delete_filters(self, sql):
        assert "s.max_val BETWEEN 0 AND 1000" in sql
        assert "r.dtime IS NULL" in sql
        assert "svr.dtime IS NULL" in sql

    @pytest.mark.parametrize("sql", [PG, DB2])
    def test_read_only(self, sql):
        assert sql.lstrip().upper().startswith("SELECT")
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER"):
            assert forbidden not in sql.upper()

    def test_unqualified_when_schema_absent(self):
        sql = _sql("postgresql", None)

        assert "FROM cmm_resource r" in sql
        assert "polestar." not in sql


class TestDecisionBoundary:
    """판정 경계 — **차분과 절대 임계를 둘 다** 만족해야 포함된다(§6.10 ①)."""

    def _having(self, sql: str) -> str:
        return sql.split("HAVING", 1)[1].split("ORDER BY", 1)[0]

    @pytest.mark.parametrize("sql", [PG, DB2])
    def test_having_has_both_gates(self, sql):
        having = self._having(sql)

        assert having.count(">=") == 2, "차분 1항 + 절대 임계 1항"
        assert ">= 80" in having, "절대 임계"
        assert ">= 20" in having, "차분 임계(%p)"

    @pytest.mark.parametrize("sql", [PG, DB2])
    def test_absolute_gate_is_on_the_current_period(self, sql):
        """기준 월이 아니라 **현재 월**이 임계를 넘어야 한다 — 하락분을 잡으면 안 된다."""
        having = self._having(sql)
        cur_first = having.split(">= 80")[0]

        assert "'202607'" in cur_first
        assert "'202606'" not in cur_first

    @pytest.mark.parametrize("sql", [PG, DB2])
    def test_delta_is_current_minus_base(self, sql):
        having = self._having(sql)
        delta_term = having.split("AND", 1)[1]
        cur_pos = delta_term.find("'202607'")
        base_pos = delta_term.find("'202606'")

        assert 0 <= cur_pos < base_pos, "현재 − 기준 순서여야 상승이 양수다"

    def test_boundary_cases_are_expressible(self):
        """5→10 배제 / 75→85 포함 / 85→90 배제 / 60→85 포함이 HAVING 2항으로 표현된다.

        차분 20%p · 절대 80% 기준으로:
          5→10  차분 +5   절대 10 → 둘 다 미달 → 배제
          75→85 차분 +10  절대 85 → **차분 미달로 배제**(임계 20%p)
          85→90 차분 +5   절대 90 → 차분 미달 → 배제
          60→85 차분 +25  절대 85 → 둘 다 충족 → **포함**
        """
        having = self._having(PG)

        assert ">= 80" in having and ">= 20" in having
        # 두 항이 AND로 묶여야 "둘 다"가 된다 — OR면 5→10%가 통과한다
        assert "AND" in having.split(">= 80", 1)[1]

    def test_thresholds_are_parameters_not_literals(self):
        sql = _sql("postgresql", "polestar", threshold_pct=70, delta_pp=15.5)

        assert ">= 70" in sql
        assert ">= 15.5" in sql
        assert ">= 80" not in sql
