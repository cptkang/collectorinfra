"""plans/122 T-5b — 생성 후 시간 조건 검증기 (D-306).

- 공용 틀(`src.sql_validation`): 현재시각 함수 탐지 · 컬럼 조건 추출 · 반개구간 정규화 · 테이블 접두
- 폴스타 규칙(`check_time_conditions`): ①현재시각 함수 ②통계 입도 ③stat_date 경계 ④알람 ctime 경계
  · 기본값·「현재」·기간 조건 없음·비교 질의·파싱 불가는 경고만(거짓 거부 방지)
- 드리프트(D-231 교훈): 생산자 리터럴(`time_period`)로 만든 SQL은 전부 통과 · §10.1 오답은 전부 반려
- 배선: 어댑터 훅 · query_validator 노드 · validate_sql_draft 도구 · 재생성 예산(3)

실 LLM 호출 0. 기준 시각은 2026-09-29(화) 10:00 KST로 고정한다(계약 기준 예시).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.db_adapters.polestar.adapter import PolestarAdapter
from src.db_adapters.polestar.time_period import STAT_TABLES, alarm_ts_bounds, stat_bounds
from src.db_adapters.polestar.validators import (
    check_current_month_stat_table,
    check_time_conditions,
)
from src.domain.query_time import QueryTime, resolve_query_time
from src.domain.time_spec import KST
from src.graph import route_after_validation
from src.sql_time_conditions import (
    REASON_COLUMN,
    REASON_EXPRESSION,
    REASON_OR,
    ColumnCondition,
    extract_column_conditions,
    find_now_functions,
    half_open_bounds,
    table_qualifiers,
)

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)
_LOGGER = "src.db_adapters.polestar.validators"


def qt_of(text: str, now: datetime = NOW) -> QueryTime:
    return resolve_query_time(text, now)


# ── 생산자 리터럴로 만든 SQL (프롬프트 기간 블록·결정적 조립과 같은 투영) ──────────────────


def stat_sql(text: str, *, db2: bool = False, form: str = "where") -> str:
    """질의 해석 → 통계 SQL(`stat_bounds(res).table` + 조건). form: where·eq·between."""
    sb = stat_bounds(qt_of(text).metric)
    assert sb is not None
    col = "S.STAT_DATE" if db2 else "s.stat_date"
    if form == "where":
        cond = sb.where(col)
    elif form == "eq":
        cond = f"{col} = '{sb.last}'"
    else:
        cond = f"{col} BETWEEN '{sb.lo}' AND '{sb.last}'"
    if db2:
        return (
            "SELECT R.HOSTNAME, AVG(S.AVG_VAL) AS CPU_AVG FROM POLESTAR.CMM_RESOURCE R "
            f"JOIN POLESTAR.{sb.table.upper()} S ON S.RESOURCE_ID = R.ID "
            f"WHERE R.DTIME IS NULL AND {cond} GROUP BY R.HOSTNAME "
            "ORDER BY CPU_AVG DESC FETCH FIRST 10 ROWS ONLY"
        )
    return (
        "SELECT r.hostname, AVG(s.avg_val) AS cpu_avg FROM polestar.cmm_resource r "
        f"JOIN polestar.{sb.table} s ON s.resource_id = r.id "
        f"WHERE r.dtime IS NULL AND {cond} GROUP BY r.hostname "
        "ORDER BY cpu_avg DESC NULLS LAST LIMIT 10"
    )


def alarm_sql(text: str, *, db2: bool = False) -> str:
    """질의 해석(사건 주체) → 알람 SQL(`alarm_ts_bounds` TIMESTAMP 조건)."""
    bounds = alarm_ts_bounds(qt_of(text).event)
    assert bounds is not None
    start, end = bounds
    col = "A.CTIME" if db2 else "a.ctime"
    cond = (f"{col} >= TIMESTAMP '{start}' AND " if start else "") + f"{col} < TIMESTAMP '{end}'"
    if db2:
        return (
            "SELECT A.ID, A.CTIME, A.MESSAGE FROM POLESTAR.CMM_ALARM A "
            f"WHERE {cond} ORDER BY A.CTIME DESC FETCH FIRST 100 ROWS ONLY"
        )
    return (
        "SELECT a.id, a.ctime, a.message FROM polestar.cmm_alarm a "
        f"WHERE {cond} ORDER BY a.ctime DESC LIMIT 100"
    )


_STAT_QUERIES = [
    "지난달 CPU 평균", "지난 3개월 CPU 평균", "이번 달 CPU 평균", "최근 30일 CPU 평균",
    "어제 CPU 평균", "지난주 CPU 평균", "최근 3시간 CPU 평균", "오늘 CPU 평균",
]
_ALARM_QUERIES = ["어제 알람", "이번 달 알람", "지난주 알람"]


# ══════════════════════════════════════════════════════════════════════════════
# 공용 틀
# ══════════════════════════════════════════════════════════════════════════════


class TestFindNowFunctions:
    def test_pg_db2_and_others(self):
        sql = (
            "SELECT * FROM t WHERE a >= CURRENT_DATE - INTERVAL '1 day' "
            "AND b < CURRENT DATE - 1 DAY AND c > NOW( ) AND d < CURRENT TIMESTAMP "
            "AND e > SYSDATE"
        )
        assert find_now_functions(sql) == [
            "CURRENT_DATE", "INTERVAL", "CURRENT DATE", "- 1 DAY", "NOW()",
            "CURRENT TIMESTAMP", "SYSDATE",
        ]

    def test_literals_comments_and_same_named_columns_ignored(self):
        sql = (
            "SELECT 'CURRENT_DATE' AS x, t.interval, \"interval\" FROM t -- NOW()\n"
            "/* INTERVAL '1 day' */ WHERE t.k = 'INTERVAL 1 DAY'"
        )
        assert find_now_functions(sql) == []


class TestExtractColumnConditions:
    def test_half_open_pair_with_alias_and_db2_case(self):
        conds = extract_column_conditions(
            "SELECT * FROM X.T S WHERE S.TS_KEY >= '20260830' AND S.TS_KEY < '20260929' "
            "FETCH FIRST 10 ROWS ONLY",
            "ts_key",
        )
        assert [(c.qualifier, c.op, c.values, c.literal) for c in conds] == [
            ("s", ">=", ("20260830",), True),
            ("s", "<", ("20260929",), True),
        ]

    def test_between_in_reversed_and_typed_literals(self):
        conds = extract_column_conditions(
            "SELECT * FROM t s WHERE s.k BETWEEN '202606' AND '202608' "
            "AND s.k IN ('202606', '202607') AND '202606' <= s.k "
            "AND s.k < TIMESTAMP '2026-09-01 00:00:00' AND s.k > CAST('2026-08-01' AS DATE) "
            "AND s.k <= '2026-09-01'::timestamp",
            "k",
        )
        assert [(c.op, c.values) for c in conds] == [
            ("between", ("202606", "202608")),
            ("in", ("202606", "202607")),
            (">=", ("202606",)),
            ("<", ("2026-09-01 00:00:00",)),
            (">", ("2026-08-01",)),
            ("<=", ("2026-09-01",)),
        ]
        assert all(c.literal for c in conds)

    def test_case_when_pivot_select_list_and_is_null_are_not_conditions(self):
        """CASE WHEN 안 비교(월 피벗 칼럼)·SELECT 목록·IS NULL은 거르는 조건이 아니다.

        조인 키(`u.k = s.k`)는 상대가 컬럼 참조인 조건(`REASON_COLUMN`)으로 남긴다 — 호출부가 구간
        계산에서 빼고, 그것만 있으면 「누락」이 아니라 「읽기 불가」로 본다(리뷰 m-4).
        """
        sql = (
            "SELECT MAX(CASE WHEN s.k = '202606' THEN s.v END) AS m6, s.k, MAX(s.k) "
            "FROM t s JOIN u ON u.k = s.k AND u.id = s.id "
            "WHERE s.k IS NOT NULL AND s.k BETWEEN '202606' AND '202608' GROUP BY s.k"
        )
        conds = extract_column_conditions(sql, "k")
        assert [(c.qualifier, c.op, c.values, c.reason) for c in conds] == [
            ("u", "=", (), REASON_COLUMN),
            ("s", "=", (), REASON_COLUMN),
            ("s", "between", ("202606", "202608"), ""),
        ]

    def test_on_clause_condition_is_a_filter(self):
        conds = extract_column_conditions(
            "SELECT * FROM r LEFT JOIN t s ON s.rid = r.id AND s.k = '202608'", "k"
        )
        assert [(c.op, c.values) for c in conds] == [("=", ("202608",))]

    @pytest.mark.parametrize(
        "where",
        [
            "s.k = TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMMDD')",  # 우변이 식
            "s.k >= (SELECT MAX(k) FROM t)",                                # 서브쿼리
            "TO_DATE(s.k || '01', 'YYYYMMDD') BETWEEN DATE '2026-01-01' AND DATE '2026-02-01'",
            "SUBSTR(s.k, 1, 6) = '202609'",                                 # 함수로 감싼 컬럼
            "s.k LIKE '202609%'",
            "s.k NOT BETWEEN 'a' AND 'b'",
            "s.k >= '20260901' - 1",                                        # 리터럴 뒤 산술
        ],
    )
    def test_unparsable_marked_not_literal(self, where):
        conds = extract_column_conditions(f"SELECT * FROM t s WHERE {where}", "k")
        assert conds and all(not c.literal and c.reason == REASON_EXPRESSION for c in conds)

    def test_column_reference_rhs_marked(self):
        """CTE 컬럼 우변(`s.k = p.m`)은 조인 키와 같은 컬럼 참조 사유다."""
        conds = extract_column_conditions(
            "WITH p AS (SELECT '202608' AS m) SELECT * FROM t s, p WHERE s.k = p.m", "k"
        )
        assert [(c.literal, c.reason) for c in conds] == [(False, REASON_COLUMN)]

    def test_now_functions_attached_to_condition(self):
        conds = extract_column_conditions(
            "SELECT * FROM t s WHERE CURRENT DATE - 1 DAY <= s.k AND s.k = '1'", "k"
        )
        assert conds[0].now_functions == ("CURRENT DATE", "- 1 DAY") and not conds[0].literal
        assert conds[1].now_functions == () and conds[1].literal

    def test_or_joined_conditions_are_not_literal(self):
        conds = extract_column_conditions(
            "SELECT * FROM t s WHERE (s.k >= 'a' AND s.k < 'b') OR (s.k >= 'c')", "k"
        )
        assert conds and all(not c.literal and c.reason == REASON_OR for c in conds)
        # OR가 다른 묶음 괄호 안에만 있으면 영향 없다
        conds = extract_column_conditions(
            "SELECT * FROM t s WHERE (s.k >= 'a' AND s.k < 'b') AND (s.q = 1 OR s.q = 2)", "k"
        )
        assert all(c.literal for c in conds)

    def test_scope_separates_subqueries_and_union_branches(self):
        conds = extract_column_conditions(
            "WITH c AS (SELECT * FROM t s WHERE s.k = 'a') "
            "SELECT * FROM t s WHERE s.k = 'b' UNION ALL SELECT * FROM t s WHERE s.k = 'c'",
            "k",
        )
        assert len({c.scope for c in conds}) == 3

    def test_unbalanced_returns_none(self):
        assert extract_column_conditions("SELECT * FROM t WHERE k = 'open", "k") is None
        assert extract_column_conditions("SELECT * FROM t WHERE (k = 'a'", "k") is None


class TestHalfOpenBounds:
    @staticmethod
    def _next_day(v: str) -> str:
        return str(int(v) + 1)  # 테스트용 단순 칸(같은 달 안)

    def _conds(self, where: str) -> list[ColumnCondition]:
        conds = extract_column_conditions(f"SELECT * FROM t WHERE {where}", "k")
        assert conds is not None
        return conds

    @pytest.mark.parametrize(
        "where",
        [
            "k >= '20260910' AND k < '20260913'",
            "k BETWEEN '20260910' AND '20260912'",
            "k > '20260909' AND k <= '20260912'",
            "k IN ('20260911', '20260910', '20260912')",
        ],
    )
    def test_equivalent_forms_normalize_to_same_set(self, where):
        bounds = half_open_bounds(self._conds(where), key=str, next_cell=self._next_day)
        assert bounds == ("20260910", "20260913")

    def test_open_side_and_non_contiguous_in(self):
        assert half_open_bounds(
            self._conds("k >= '20260910'"), key=str, next_cell=self._next_day
        ) == ("20260910", None)
        assert half_open_bounds(
            self._conds("k IN ('20260910', '20260912')"), key=str, next_cell=self._next_day
        ) is None

    def test_key_failure_returns_none(self):
        def key(v: str) -> str:
            raise ValueError(v)

        assert half_open_bounds(self._conds("k = 'x'"), key=key, next_cell=str) is None


class TestTableQualifiers:
    def test_aliases_schema_prefix_and_case(self):
        sql = (
            "SELECT * FROM POLESTAR.TBL_A A JOIN tbl_b AS b ON a.id = b.id, tbl_c "
            "WHERE tbl_c.x = 1"
        )
        assert table_qualifiers(sql, ["tbl_a", "tbl_b", "tbl_c", "tbl_d"]) == {
            "tbl_a": {"tbl_a", "a"}, "tbl_b": {"tbl_b", "b"}, "tbl_c": {"tbl_c"},
        }


# ══════════════════════════════════════════════════════════════════════════════
# 폴스타 규칙
# ══════════════════════════════════════════════════════════════════════════════


class TestStatRules:
    def test_correct_boundary_passes(self):
        text = "최근 30일 CPU 평균"
        assert check_time_conditions(stat_sql(text), qt_of(text)) == []

    def test_equivalent_closed_form_passes(self):
        """BETWEEN(끝 포함)·반개구간은 같은 칸 집합이면 통과한다."""
        sql = stat_sql("최근 30일 CPU 평균").replace(
            "s.stat_date >= '20260830' AND s.stat_date < '20260929'",
            "s.stat_date BETWEEN '20260830' AND '20260928'",
        )
        assert "BETWEEN" in sql
        assert check_time_conditions(sql, qt_of("최근 30일 CPU 평균")) == []

    def test_shifted_boundary_rejected_with_expected_literal(self):
        sql = stat_sql("최근 30일 CPU 평균").replace("'20260830'", "'20260829'")
        errors = check_time_conditions(sql, qt_of("최근 30일 CPU 평균"))
        assert len(errors) == 1
        assert "s.stat_date >= '20260830' AND s.stat_date < '20260929'" in errors[0]
        assert "2026-08-30 ~ 2026-09-28" in errors[0]

    def test_wrong_literal_format_rejected_with_format_hint(self):
        sql = stat_sql("어제 CPU 평균").replace("'20260928'", "'2026-09-28'")
        errors = check_time_conditions(sql, qt_of("어제 CPU 평균"))
        assert errors and "YYYYMMDD 문자열" in errors[0]

    def test_missing_condition_rejected(self):
        sql = (
            "SELECT r.hostname, AVG(s.avg_val) FROM polestar.cmm_resource r "
            "JOIN polestar.cmm_metric_stat_d s ON s.resource_id = r.id "
            "WHERE r.dtime IS NULL GROUP BY r.hostname"
        )
        errors = check_time_conditions(sql, qt_of("어제 CPU 평균"))
        assert errors == [
            "cmm_metric_stat_d에 기간 「2026-09-28 ~ 2026-09-28」 조건이 없습니다 — "
            "s.stat_date >= '20260928' AND s.stat_date < '20260929'를 넣으세요."
        ]

    def test_grain_mismatch_rejected_with_expected_table(self):
        sql = stat_sql("지난달 CPU 평균")  # 월 통계
        errors = check_time_conditions(sql, qt_of("최근 30일 CPU 평균"))
        assert len(errors) == 1
        assert "cmm_metric_stat_d(일 통계)로 조회해야 합니다" in errors[0]
        assert "s.stat_date >= '20260830' AND s.stat_date < '20260929'" in errors[0]
        assert "GROUP BY" in errors[0]

    def test_now_function_rejected(self):
        sql = (
            "SELECT r.hostname FROM polestar.cmm_resource r "
            "JOIN polestar.cmm_metric_stat_d s ON s.resource_id = r.id WHERE r.dtime IS NULL "
            "AND s.stat_date >= TO_CHAR(CURRENT_DATE - INTERVAL '30 day', 'YYYYMMDD')"
        )
        errors = check_time_conditions(sql, qt_of("최근 30일 CPU 평균"))
        assert len(errors) == 1
        assert "현재시각 함수 금지" in errors[0] and "CURRENT_DATE, INTERVAL" in errors[0]
        assert "s.stat_date >= '20260830' AND s.stat_date < '20260929'" in errors[0]

    def test_now_function_via_cte_rejected(self):
        """조건이 CTE 컬럼을 거쳐 현재시각 함수로 기간을 계산해도 ①로 반려한다."""
        sql = (
            "WITH p AS (SELECT TO_CHAR(CURRENT_DATE - INTERVAL '30 day', 'YYYYMMDD') AS lo) "
            "SELECT r.hostname FROM polestar.cmm_resource r "
            "JOIN polestar.cmm_metric_stat_d s ON s.resource_id = r.id CROSS JOIN p "
            "WHERE r.dtime IS NULL AND s.stat_date >= p.lo"
        )
        errors = check_time_conditions(sql, qt_of("최근 30일 CPU 평균"))
        assert len(errors) == 1 and "현재시각 함수 금지" in errors[0]

    def test_or_joined_time_condition_rejected(self):
        """OR로 묶인 시간 조건은 다른 달이 섞인 SQL이 실행되므로 반려한다(리뷰 m-5 · D6)."""
        sql = (
            "SELECT AVG(s.avg_val) FROM polestar.cmm_metric_stat_m s "
            "WHERE s.stat_date >= '202608' AND s.stat_date < '202609' OR s.stat_date = '202607'"
        )
        errors = check_time_conditions(sql, qt_of("지난달 CPU 평균"))
        assert len(errors) == 1 and "OR로 다른 조건과 묶여" in errors[0]
        assert "s.stat_date >= '202608' AND s.stat_date < '202609'" in errors[0]
        sql = (
            "SELECT AVG(s.avg_val) FROM polestar.cmm_metric_stat_d s WHERE (s.stat_date >= "
            "'20260922' AND s.stat_date < '20260929' OR s.stat_date >= '20260101')"
        )
        assert check_time_conditions(sql, qt_of("최근 7일 CPU 평균"))

    @pytest.mark.parametrize(
        "sql",
        [
            # CTE 컬럼 우변(리뷰 m-4) — 「누락」이 아니라 「읽기 불가」
            "WITH p AS (SELECT '202608' AS m) SELECT AVG(s.avg_val) "
            "FROM polestar.cmm_metric_stat_m s, p WHERE s.stat_date = p.m",
            # CTE 서브쿼리 우변 · 컬럼 캐스트(D6) — 경고 유지
            "WITH p AS (SELECT '202608' lo) SELECT AVG(s.avg_val) "
            "FROM polestar.cmm_metric_stat_m s WHERE s.stat_date >= (SELECT lo FROM p)",
            "SELECT AVG(s.avg_val) FROM polestar.cmm_metric_stat_m s "
            "WHERE CAST(s.stat_date AS INTEGER) = 202608",
        ],
    )
    def test_non_literal_rhs_warns_not_missing(self, sql, caplog):
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert check_time_conditions(sql, qt_of("지난달 CPU 평균")) == []
        assert "리터럴로 읽지 못해" in caplog.text

    def test_self_join_key_does_not_hide_literal_range(self):
        """조인 키(컬럼 참조)는 구간 계산에서 빠진다 — 같은 별칭의 리터럴 범위로 판정한다."""
        sql = (
            "SELECT a.avg_val - b.avg_val FROM polestar.cmm_metric_stat_d a "
            "JOIN polestar.cmm_metric_stat_d b ON a.stat_date = b.stat_date "
            "AND a.resource_id = b.resource_id "
            "WHERE a.stat_date >= '20260830' AND a.stat_date < '20260929'"
        )
        assert check_time_conditions(sql, qt_of("최근 30일 CPU 평균")) == []
        assert check_time_conditions(
            sql.replace("'20260830'", "'20260801'"), qt_of("최근 30일 CPU 평균")
        )

    def test_unparsable_only_warns(self, caplog):
        sql = (
            "SELECT r.hostname FROM polestar.cmm_resource r "
            "JOIN polestar.cmm_metric_stat_m s ON s.resource_id = r.id WHERE r.dtime IS NULL "
            "AND TO_DATE(s.stat_date || '01', 'YYYYMMDD') = DATE '2026-08-01'"
        )
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert check_time_conditions(sql, qt_of("지난달 CPU 평균")) == []
        assert "리터럴로 읽지 못해" in caplog.text

    def test_pivot_case_when_months_with_join_range_passes(self):
        """월 피벗(CASE WHEN 월별 칼럼) + 조인 ON 범위 — 피벗 비교는 기간 조건이 아니다."""
        sql = (
            "SELECT r.hostname, "
            "MAX(CASE WHEN s.stat_date='202606' THEN s.avg_val END) AS m6, "
            "MAX(CASE WHEN s.stat_date='202607' THEN s.avg_val END) AS m7, "
            "MAX(CASE WHEN s.stat_date='202608' THEN s.avg_val END) AS m8 "
            "FROM polestar.cmm_resource r LEFT JOIN polestar.cmm_metric_stat_m s "
            "ON s.resource_id = r.id AND s.stat_date BETWEEN '202606' AND '202608' "
            "WHERE r.dtime IS NULL GROUP BY r.hostname"
        )
        assert check_time_conditions(sql, qt_of("지난 3개월 CPU 평균")) == []

    def test_comparison_baseline_in_other_block_passes(self):
        """같은 블록의 한 묶음이라도 기대 구간과 같으면 통과(전월 대비 기준 CTE 등)."""
        sql = (
            "WITH prev AS (SELECT s.resource_id, AVG(s.avg_val) v "
            "FROM polestar.cmm_metric_stat_m s "
            "WHERE s.stat_date = '202607' GROUP BY s.resource_id), "
            "cur AS (SELECT s.resource_id, AVG(s.avg_val) v "
            "FROM polestar.cmm_metric_stat_m s "
            "WHERE s.stat_date = '202608' GROUP BY s.resource_id) "
            "SELECT cur.resource_id, cur.v - prev.v FROM cur JOIN prev "
            "ON prev.resource_id = cur.resource_id"
        )
        assert check_time_conditions(sql, qt_of("지난달 CPU 평균")) == []

    def test_non_stat_sql_untouched(self):
        sql = "SELECT name, hostname FROM polestar.cmm_resource WHERE dtime IS NULL"
        assert check_time_conditions(sql, qt_of("이번 달 등록된 서버 목록")) == []


class TestAlarmRules:
    def test_correct_boundary_passes_with_string_and_closed_forms(self):
        qt = qt_of("어제 알람")
        assert check_time_conditions(alarm_sql("어제 알람"), qt) == []
        for cond in (
            "a.ctime >= '2026-09-28' AND a.ctime < '2026-09-29'",
            "a.ctime BETWEEN '2026-09-28 00:00:00' AND '2026-09-28 23:59:59'",
            "a.ctime >= TIMESTAMP('2026-09-28-00.00.00') AND a.ctime < '2026-09-29 00:00:00'",
        ):
            sql = f"SELECT a.id FROM polestar.cmm_alarm a WHERE {cond} LIMIT 10"
            assert check_time_conditions(sql, qt) == [], cond

    def test_whole_month_for_yesterday_rejected(self):
        sql = (
            "SELECT a.id FROM polestar.cmm_alarm a WHERE a.ctime >= TIMESTAMP "
            "'2026-09-01 00:00:00' AND a.ctime < TIMESTAMP '2026-10-01 00:00:00' LIMIT 100"
        )
        errors = check_time_conditions(sql, qt_of("어제 알람"))
        assert len(errors) == 1
        assert (
            "a.ctime >= TIMESTAMP '2026-09-28 00:00:00' AND a.ctime < TIMESTAMP "
            "'2026-09-29 00:00:00'" in errors[0]
        )

    def test_date_only_closed_end_excluding_the_day_rejected(self):
        """`<= '2026-09-28'`은 그날 0시까지다 — 하루를 빼먹는 오답."""
        sql = (
            "SELECT a.id FROM polestar.cmm_alarm a "
            "WHERE a.ctime >= '2026-09-28' AND a.ctime <= '2026-09-28'"
        )
        assert check_time_conditions(sql, qt_of("어제 알람"))

    def test_in_progress_period_accepts_open_or_future_end(self):
        """진행 중 기간(기준 시각까지 · D-291)은 끝 생략·미래 끝이 같은 행이다(§10.1 D-05)."""
        qt = qt_of("이번 달 알람")
        for cond in (
            "a.ctime >= TIMESTAMP '2026-09-01 00:00:00'",
            "a.ctime >= '2026-09-01' AND a.ctime < '2026-10-01'",
        ):
            sql = f"SELECT a.id FROM polestar.cmm_alarm_active a WHERE {cond}"
            assert check_time_conditions(sql, qt) == [], cond

    def test_missing_alarm_condition_rejected(self):
        sql = "SELECT a.id FROM polestar.cmm_alarm a ORDER BY a.ctime DESC LIMIT 10"
        errors = check_time_conditions(sql, qt_of("지난주 알람"))
        assert errors and "알람 기간" in errors[0] and "조건이 없습니다" in errors[0]

    def test_no_alarm_period_requires_nothing(self):
        """알람 기간 없음 = 기간 조건 없음(D-291) — 요구하지 않는다."""
        sql = "SELECT a.id FROM polestar.cmm_alarm a ORDER BY a.ctime DESC LIMIT 100"
        assert check_time_conditions(sql, qt_of("알람 최근 발생 순 100건")) == []

    def test_or_joined_alarm_condition_rejected(self):
        sql = (
            "SELECT a.id FROM polestar.cmm_alarm a WHERE a.ctime >= '2026-09-28' "
            "AND a.ctime < '2026-09-29' OR a.alarmseverity = 'Critical'"
        )
        errors = check_time_conditions(sql, qt_of("어제 알람"))
        assert errors and "OR로 다른 조건과 묶여" in errors[0]

    def test_period_with_present_active_snapshot_warns_only(self, caplog):
        """기간 + 「현재」(「지난달 서버 CPU와 현재 활성 알람 수」) — 활성 스냅샷은 현재 몫.

        리뷰 M-1 — 종전에는 기간 조건 누락으로 반려돼 재생성 예산을 다 썼다.
        """
        qt = qt_of("지난달 서버 CPU와 현재 활성 알람 수")
        assert qt.explicit and qt.present
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert check_time_conditions(
                "SELECT count(*) FROM polestar.cmm_alarm_active a", qt
            ) == []
        assert "활성 알람을 현재 스냅샷 조회로" in caplog.text
        # 알람 이력은 그대로 엄격하다
        assert check_time_conditions("SELECT count(*) FROM polestar.cmm_alarm a", qt)

    def test_active_snapshot_without_present_stays_strict(self):
        """「현재」가 없으면 활성 알람도 기간 조건을 요구한다(「이번 달 발생한 활성 알람」)."""
        qt = qt_of("이번 달 발생한 활성 알람")
        assert qt.explicit and not qt.present
        assert check_time_conditions("SELECT a.id FROM polestar.cmm_alarm_active a", qt)

    def test_non_alarm_ctime_conditions_ignored_when_alarm_alias_matches(self):
        sql = (
            "SELECT a.id FROM polestar.cmm_alarm a JOIN polestar.cmm_resource r "
            "ON r.id = a.resource_id WHERE r.dtime IS NULL AND r.ctime < '2020-01-01' "
            "AND a.ctime >= '2026-09-28' AND a.ctime < '2026-09-29'"
        )
        assert check_time_conditions(sql, qt_of("어제 알람")) == []


class TestModes:
    """사용자가 기간을 말했을 때만 오류 — 그 밖은 경고만(거짓 거부 방지)."""

    _WRONG = (
        "SELECT r.hostname FROM polestar.cmm_resource r "
        "JOIN polestar.cmm_metric_stat_h s ON s.resource_id = r.id WHERE r.dtime IS NULL "
        "AND s.stat_date = TO_CHAR(CURRENT_TIMESTAMP - INTERVAL '1 hour', 'YYYYMMDDHH24')"
    )

    def test_default_period_warns_only(self, caplog):
        qt = qt_of("CPU 사용률 상위 10대")
        assert qt.uses_default_period
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert check_time_conditions(self._WRONG, qt) == []
        assert "default_period" in caplog.text

    def test_present_is_not_compared(self, caplog):
        qt = qt_of("현재 CPU 사용률")
        assert qt.present and not qt.uses_default_period
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert check_time_conditions(self._WRONG, qt) == []
        assert caplog.text == ""

    def test_unbounded_requires_nothing(self):
        qt = qt_of("CPU가 90% 넘은 적이 있는 서버")
        assert qt.metric is not None and qt.metric.unbounded
        assert check_time_conditions(self._WRONG, qt) == []

    def test_multiple_periods_warns_only(self, caplog):
        qt = qt_of("8월과 9월 CPU 비교")
        assert "multiple_periods" in qt.metric.notes
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert check_time_conditions(self._WRONG, qt) == []
        assert "multiple_periods" in caplog.text

    def test_period_with_present_hour_stats_warns_only(self, caplog):
        """기간 + 「현재」(「지난달 알람이 난 서버의 현재 CPU」) — 시간 통계는 현재 값 몫이다."""
        text = "지난달 알람이 발생한 서버의 현재 CPU 사용률"
        qt = qt_of(text)
        assert qt.explicit and qt.present
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert check_time_conditions(self._WRONG, qt) == []
        assert "「현재」 표현" in caplog.text
        # 알람 기간(④)은 그대로 본다
        sql = self._WRONG.replace(
            "WHERE r.dtime IS NULL",
            "JOIN polestar.cmm_alarm a ON a.resource_id = r.id WHERE r.dtime IS NULL "
            "AND a.ctime >= '2026-09-01'",
        )
        assert any("알람 기간" in e for e in check_time_conditions(sql, qt))

    def test_period_with_present_month_stats_still_rejected(self):
        """「이번 달 현재까지」 + 월 통계는 D-201 그대로 반려한다(시간 통계만 완화)."""
        qt = qt_of("이번 달 현재까지 CPU 평균")
        assert qt.explicit and qt.present
        assert check_time_conditions(_STAT_M_THIS_MONTH, qt)

    def test_clarify_is_defended(self):
        qt = QueryTime(NOW, None, None, clarify="invalid_date")
        assert check_time_conditions(self._WRONG, qt) == []


# ══════════════════════════════════════════════════════════════════════════════
# 드리프트 — 「검증기가 반려하는 SQL = 생산자가 바꾸는 SQL」(D-231 교훈)
# ══════════════════════════════════════════════════════════════════════════════


def _forms(text: str) -> list[str]:
    sb = stat_bounds(qt_of(text).metric)
    assert sb is not None
    if sb.grain != "month":
        return ["where"]
    return ["where", "eq" if sb.lo == sb.last else "between"]


class TestProducerDrift:
    @pytest.mark.parametrize("db2", [False, True], ids=["pg", "db2"])
    @pytest.mark.parametrize("text", _STAT_QUERIES)
    def test_producer_stat_literals_pass(self, text, db2):
        for form in _forms(text):
            sql = stat_sql(text, db2=db2, form=form)
            assert check_time_conditions(sql, qt_of(text)) == [], (form, sql)

    @pytest.mark.parametrize("db2", [False, True], ids=["pg", "db2"])
    @pytest.mark.parametrize("text", _ALARM_QUERIES)
    def test_producer_alarm_literals_pass(self, text, db2):
        assert check_time_conditions(alarm_sql(text, db2=db2), qt_of(text)) == []

    @pytest.mark.parametrize("text", _STAT_QUERIES + _ALARM_QUERIES)
    def test_prompt_period_block_conditions_pass(self, text):
        """프롬프트 기간 블록(T-5 · 생산자)이 지시하는 조건 문자열 그대로 만든 SQL은 통과한다."""
        from src.db_adapters.polestar import time_period

        build = getattr(time_period, "build_period_block", None)
        if build is None:
            pytest.skip("T-5 프롬프트 기간 블록 미배선")
        qt = qt_of(text)
        block = build(qt)
        conds = re.findall(r"`([^`]*(?:stat_date|ctime)[^`]*)`", block)
        assert conds, block
        for cond in conds:
            if "ctime" in cond:
                sql = f"SELECT a.id FROM polestar.cmm_alarm a WHERE {cond} LIMIT 100"
            else:
                sb = stat_bounds(qt.metric)
                sql = (
                    f"SELECT r.hostname FROM polestar.cmm_resource r JOIN polestar.{sb.table} s "
                    f"ON s.resource_id = r.id WHERE r.dtime IS NULL AND {cond} LIMIT 10"
                )
            assert check_time_conditions(sql, qt) == [], (cond, block)


class TestMeasuredWrongSqlRejected:
    """§10.1 실측 오답 — 전부 반려(PG·DB2)."""

    @pytest.mark.parametrize(
        "text,sql",
        [
            (  # K-09·R4-02C 「최근 30일」 → 월 통계 한 달
                "최근 30일 CPU 평균",
                "SELECT r.hostname, s.avg_val FROM polestar.cmm_resource r JOIN "
                "polestar.cmm_metric_stat_m s ON s.resource_id = r.id WHERE r.dtime IS NULL "
                "AND s.stat_date = '202608' LIMIT 100",
            ),
            (
                "최근 30일 CPU 평균",
                "SELECT R.HOSTNAME, S.AVG_VAL FROM POLESTAR.CMM_RESOURCE R JOIN "
                "POLESTAR.CMM_METRIC_STAT_M S ON S.RESOURCE_ID = R.ID WHERE R.DTIME IS NULL "
                "AND S.STAT_DATE = '202608' FETCH FIRST 100 ROWS ONLY",
            ),
            (  # R4-03C 「최근 1시간」 → 일 통계 이번 달 1일~
                "최근 1시간 CPU 사용률",
                "SELECT r.hostname, s.avg_val FROM polestar.cmm_resource r JOIN "
                "polestar.cmm_metric_stat_d s ON s.resource_id = r.id WHERE r.dtime IS NULL "
                "AND s.stat_date BETWEEN '20260901' AND '20260928' LIMIT 100",
            ),
            (
                "최근 1시간 CPU 사용률",
                "SELECT R.HOSTNAME FROM POLESTAR.CMM_RESOURCE R JOIN POLESTAR.CMM_METRIC_STAT_D S "
                "ON S.RESOURCE_ID = R.ID WHERE R.DTIME IS NULL AND S.STAT_DATE >= '20260901' "
                "AND S.STAT_DATE < '20260929' FETCH FIRST 100 ROWS ONLY",
            ),
            (  # R1-08·R1-10 「어제 알람」 → ctime 9월 전체
                "어제 알람",
                "SELECT a.id FROM polestar.cmm_alarm a WHERE a.ctime >= TIMESTAMP "
                "'2026-09-01 00:00:00' AND a.ctime < TIMESTAMP '2026-10-01 00:00:00' LIMIT 100",
            ),
            (
                "어제 알람",
                "SELECT A.ID FROM POLESTAR.CMM_ALARM A WHERE A.CTIME >= '2026-09-01' "
                "AND A.CTIME < '2026-10-01' FETCH FIRST 100 ROWS ONLY",
            ),
            (  # R3-06C 지난주 알람 → 9월 전체
                "지난주 알람",
                "SELECT a.id FROM polestar.cmm_alarm a WHERE a.ctime >= '2026-09-01 00:00:00' "
                "AND a.ctime < '2026-10-01 00:00:00' LIMIT 100",
            ),
            (  # R4-11·R1-10 「어제」 → CURRENT_DATE - INTERVAL '1 day'
                "어제 CPU 평균",
                "SELECT r.hostname FROM polestar.cmm_resource r JOIN polestar.cmm_metric_stat_d s "
                "ON s.resource_id = r.id WHERE r.dtime IS NULL "
                "AND s.stat_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMMDD') LIMIT 10",
            ),
            (
                "어제 CPU 평균",
                "SELECT R.HOSTNAME FROM POLESTAR.CMM_RESOURCE R JOIN POLESTAR.CMM_METRIC_STAT_D S "
                "ON S.RESOURCE_ID = R.ID WHERE R.DTIME IS NULL "
                "AND S.STAT_DATE = VARCHAR_FORMAT(CURRENT DATE - 1 DAY, 'YYYYMMDD') "
                "FETCH FIRST 10 ROWS ONLY",
            ),
            (
                "어제 알람",
                "SELECT a.id FROM polestar.cmm_alarm a "
                "WHERE a.ctime >= CURRENT_DATE - INTERVAL '1 day' AND a.ctime < CURRENT_DATE",
            ),
            (  # C-06 「이번 달」 → stat_m (D-201)
                "이번 달 CPU 평균",
                "SELECT r.name, s.avg_val FROM polestar.cmm_metric_stat_m s "
                "JOIN polestar.cmm_resource r ON r.id = s.resource_id WHERE r.dtime IS NULL "
                "AND s.stat_date = '202609'",
            ),
        ],
    )
    def test_rejected(self, text, sql):
        assert check_time_conditions(sql, qt_of(text))


# ══════════════════════════════════════════════════════════════════════════════
# D-201 흡수 — 「이번 달 → 일 통계」는 ②③이 같은 판정을 낸다
# ══════════════════════════════════════════════════════════════════════════════


class TestD201Absorbed:
    """종전 검사는 `date.today()`를 읽으므로 같은 기준(실제 오늘)으로 대조한다."""

    _Q = "이번 달 서버별 CPU 사용률 보여줘"

    def _qt(self) -> QueryTime:
        return resolve_query_time(self._Q, datetime.now(KST))

    def test_stat_m_rejected_by_both(self):
        sql = (
            "SELECT r.name, s.avg_val FROM polestar.cmm_metric_stat_m s "
            "JOIN polestar.cmm_resource r ON r.id = s.resource_id "
            "WHERE r.dtime IS NULL AND s.definition_name = 'Utilization'"
        )
        assert check_current_month_stat_table(sql, self._Q)
        errors = check_time_conditions(sql, self._qt())
        assert errors and STAT_TABLES["day"] in errors[0] and STAT_TABLES["month"] in errors[0]

    def test_producer_stat_d_passes_both(self):
        qt = self._qt()
        sb = stat_bounds(qt.metric)
        assert sb is not None and sb.table == STAT_TABLES["day"]
        sql = (
            f"SELECT r.name, AVG(s.avg_val) FROM polestar.{sb.table} s "
            "JOIN polestar.cmm_resource r ON r.id = s.resource_id "
            f"WHERE r.dtime IS NULL AND {sb.where()} GROUP BY r.name"
        )
        assert check_current_month_stat_table(sql, self._Q) == []
        assert check_time_conditions(sql, qt) == []

    def test_stat_d_without_period_is_stricter_now(self):
        """종전 검사는 stat_d면 무조건 통과했다 — 새 검증기는 기간 조건 누락(③)을 반려한다."""
        sql = (
            "SELECT r.name, AVG(s.avg_val) FROM polestar.cmm_metric_stat_d s "
            "JOIN polestar.cmm_resource r ON r.id = s.resource_id "
            "WHERE r.dtime IS NULL GROUP BY r.name"
        )
        assert check_current_month_stat_table(sql, self._Q) == []
        assert check_time_conditions(sql, self._qt())


# ══════════════════════════════════════════════════════════════════════════════
# 배선 — 어댑터 훅 · 노드 · 도구 · 재생성 예산
# ══════════════════════════════════════════════════════════════════════════════

_STAT_M_THIS_MONTH = (
    "SELECT r.name, s.avg_val FROM polestar.cmm_metric_stat_m s "
    "JOIN polestar.cmm_resource r ON r.id = s.resource_id "
    "WHERE r.dtime IS NULL AND s.stat_date = '202609'"
)


class TestAdapterWiring:
    def test_flag_off_keeps_legacy_d201_check(self):
        adapter = PolestarAdapter()
        base = adapter.validator_checks()
        legacy = adapter.validator_checks(user_query="이번 달 CPU 사용률", time_resolution=None)
        assert len(legacy) == len(base) + 1

    def test_time_resolution_replaces_d201_check_not_adds(self):
        adapter = PolestarAdapter()
        state_value = qt_of("최근 30일 CPU 평균").to_state()
        checks = adapter.validator_checks(
            user_query="최근 30일 CPU 평균", time_resolution=state_value
        )
        assert len(checks) == len(adapter.validator_checks()) + 1
        errors = [e for c in checks for e in c(stat_sql("지난달 CPU 평균"))]
        assert any("cmm_metric_stat_d(일 통계)" in e for e in errors)
        assert not any("당월 1일" in e for e in errors)  # 종전 D-201 문구는 없다

    def test_malformed_time_resolution_falls_back_to_legacy(self):
        adapter = PolestarAdapter()
        checks = adapter.validator_checks(
            user_query="이번 달 서버별 CPU 사용률", time_resolution={"version": 999}
        )
        errors = [e for c in checks for e in c(_STAT_M_THIS_MONTH)]
        assert any("당월 1일" in e for e in errors)

    def test_generated_adapters_accept_kwarg(self):
        from src.db_adapters.generated import BoundGeneratedTemplate, GeneratedTemplateAdapter

        tr = qt_of("어제 알람").to_state()
        assert BoundGeneratedTemplate("x", "s").validator_checks(time_resolution=tr) == []
        assert GeneratedTemplateAdapter().validator_checks(time_resolution=tr) == []


def _app_config() -> SimpleNamespace:
    return SimpleNamespace(
        get_polestar_db_ids=lambda: {"polestar"},
        query=SimpleNamespace(default_limit=100),
    )


def _validator_state(sql: str, text: str, *, with_time: bool = True) -> dict:
    from src.state import create_initial_state

    state = create_initial_state(user_query=text)
    state["schema_info"] = {"tables": {}}
    state["generated_sql"] = sql
    state["active_db_id"] = "polestar"
    state["time_resolution"] = qt_of(text).to_state() if with_time else None
    return state


class TestNodeWiring:
    async def test_validator_node_rejects_with_expected_literal_in_error_message(self):
        from src.nodes.query_validator import query_validator

        state = _validator_state(stat_sql("지난달 CPU 평균"), "최근 30일 CPU 평균")
        out = await query_validator(state, app_config=_app_config())
        assert out["validation_result"]["passed"] is False
        # 재생성 프롬프트(`error_message`)에 기대 테이블·조건 리터럴이 그대로 실린다
        assert "cmm_metric_stat_d" in out["error_message"]
        assert "s.stat_date >= '20260830' AND s.stat_date < '20260929'" in out["error_message"]

    async def test_validator_node_passes_producer_sql(self):
        from src.nodes.query_validator import query_validator

        state = _validator_state(stat_sql("최근 30일 CPU 평균"), "최근 30일 CPU 평균")
        out = await query_validator(state, app_config=_app_config())
        assert out["validation_result"]["passed"] is True, out

    async def test_validator_node_flag_off_is_legacy(self):
        from src.nodes.query_validator import query_validator

        # 플래그 off(time_resolution None) — 월 통계 '202608'은 종전처럼 통과한다
        state = _validator_state(stat_sql("지난달 CPU 평균"), "최근 30일 CPU 평균", with_time=False)
        out = await query_validator(state, app_config=_app_config())
        assert out["validation_result"]["passed"] is True, out


class TestToolWiring:
    def test_validate_sql_draft_passes_time_resolution(self):
        from src.tools.validation import validate_sql_draft

        kwargs = dict(db_id="polestar", adapter_db_ids={"polestar"}, user_query="어제 알람")
        bad = alarm_sql("이번 달 알람")
        on = validate_sql_draft(
            bad, {"tables": {}}, time_resolution=qt_of("어제 알람").to_state(), **kwargs
        )
        off = validate_sql_draft(bad, {"tables": {}}, **kwargs)
        assert on["valid"] is False and any("알람 기간" in e for e in on["errors"])
        assert off["valid"] is True, off


class TestRegenerationBudget:
    """검증 실패 → query_generator 회귀(예산 `QUERY_MAX_RETRY_COUNT` 3) 경로는 그대로다."""

    async def _loop(self, regenerate) -> tuple[str, int]:  # noqa: ANN001
        from src.nodes.query_validator import query_validator

        text = "최근 30일 CPU 평균"
        state = _validator_state(stat_sql("지난달 CPU 평균"), text)
        for _ in range(10):  # 안전 상한 — 예산이 끝을 낸다
            state.update(await query_validator(state, app_config=_app_config()))
            route = route_after_validation(state)
            if route != "query_generator":
                return route, state["retry_count"]
            # query_generator 재시도 — retry_count 증가 + 오류 메시지를 보고 다시 쓴다
            state["retry_count"] += 1
            state["generated_sql"] = regenerate(state["generated_sql"], state["error_message"])
        raise AssertionError("재생성 예산 안에서 끝나지 않았다")

    async def test_regeneration_following_message_succeeds_within_budget(self):
        def follow(sql: str, message: str) -> str:
            where = re.search(r"s\.stat_date >= '\d+' AND s\.stat_date < '\d+'", message)
            assert where, message
            return (
                "SELECT r.hostname, AVG(s.avg_val) FROM polestar.cmm_resource r "
                "JOIN polestar.cmm_metric_stat_d s ON s.resource_id = r.id "
                f"WHERE r.dtime IS NULL AND {where.group(0)} GROUP BY r.hostname"
            )

        route, retries = await self._loop(follow)
        assert (route, retries) == ("query_executor", 1)

    async def test_stubborn_regeneration_exhausts_budget(self):
        route, retries = await self._loop(lambda sql, _msg: sql)
        assert (route, retries) == ("error_response", 3)
