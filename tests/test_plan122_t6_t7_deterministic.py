"""plans/122 T-6·T-7 — 결정적 조립(알람·폼필·시맨틱 컴파일러)·도구의 기간 배선 (D-309).

기준 시각 2026-09-29(화) 10:00 KST(§10.3.1 정책 표 기준). LLM·DB 호출 0 — 가짜 LLM만 쓴다.

- T-6 알람: 사건 해석(`QueryTime.event`)의 `[start, end)`가 `ctime` TIMESTAMP 리터럴로 그대로
  들어간다(완결 월 절단 없음 · 기간 없음 = 조건 없음 · D-291). 종전 결함 ⑧(이력 어휘 + 월 범위
  None = 전 이력 조회)이 period 경로에서 사라진다
- 폼필: 통계 해석(`QueryTime.metric`)의 입도 테이블 + 반개구간 리터럴. 월 시리즈는 앵커를 기간
  해석으로 정하고 조인 조건은 항목 월 범위가 덮는다(D-185 우선순위 유지)
- T-7 시맨틱: 패턴 A/B = metric · C = event. `time_grain=hour` + 월 리터럴 0행 결함(⑧)을 해석
  입도의 리터럴로 막는다
- 도구: `ToolContext.time_resolution` → `resolve_time_range`가 요청 해석을 돌려준다
- period/query_time None이면 종전 SQL과 바이트 동일(골든 — 기준선 `71d7ff6`에서 채취)
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from src.db_adapters.polestar import assembler as asm
from src.db_adapters.polestar.time_period import sql_applies_period
from src.domain.query_time import QueryTime, resolve_query_time
from src.domain.time_spec import KST
from src.nodes import semantic_compiler as sc
from src.semantic import SMQ, guard_counters, reset_guard_counters
from src.tools.binding import ToolContext, build_query_tools
from src.tools.interpretation import resolve_time_range

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)
_GP = "polestar_cm_gp"
_B0 = "polestar_b0"
_DIALECTS = [("postgresql", "polestar"), ("db2", "POLESTAR")]
_EAV = {
    "entity_table": "cmm_resource", "config_table": "core_config_prop",
    "attribute_column": "name", "value_column": "stringvalue_short",
    "direct_join": {"entity_column": "resource_conf_id", "config_column": "configuration_id"},
}


def qt(text: str) -> QueryTime:
    """기준 시각 고정 요청 해석(규칙만 — 슬롯 없음)."""
    return resolve_query_time(text, NOW)


def _ctime_conds(sql: str) -> list[str]:
    return re.findall(r"a\.ctime [<>=]+ TIMESTAMP '[^']+'", sql)


def _alarm(q: str, engine: str = "postgresql", schema: str = "polestar", **kw) -> str | None:
    return asm.try_deterministic_alarm_sql(
        q, routing_intent="alarm_query", db_engine=engine, db_schema=schema,
        limit=100, enabled=True, **kw,
    )


# ──────────────────────────────────────────────
# T-6 알람 결정적 조립
# ──────────────────────────────────────────────

_ALARM_CASES = [
    ("어제 알람", ["a.ctime >= TIMESTAMP '2026-09-28 00:00:00'",
                 "a.ctime < TIMESTAMP '2026-09-29 00:00:00'"]),
    ("지난주 알람", ["a.ctime >= TIMESTAMP '2026-09-21 00:00:00'",
                  "a.ctime < TIMESTAMP '2026-09-28 00:00:00'"]),
    ("최근 30일 알람", ["a.ctime >= TIMESTAMP '2026-08-30 00:00:00'",
                    "a.ctime < TIMESTAMP '2026-09-29 00:00:00'"]),
    # 진행 중 기간 = 기준 시각까지(D-291 event_to_now — 통계의 「어제까지」 절단 없음)
    ("이번 달 알람", ["a.ctime >= TIMESTAMP '2026-09-01 00:00:00'",
                   "a.ctime < TIMESTAMP '2026-09-29 10:00:00'"]),
    ("지난달 알람 건수", ["a.ctime >= TIMESTAMP '2026-08-01 00:00:00'",
                     "a.ctime < TIMESTAMP '2026-09-01 00:00:00'"]),
    ("최근 3시간 심각 알람", ["a.ctime >= TIMESTAMP '2026-09-29 07:00:00'",
                       "a.ctime < TIMESTAMP '2026-09-29 10:00:00'"]),
    # 기간 없음 = 기간 조건 없음(D-291 event_no_default_period — 지난달 기본값 아님)
    ("최근 발생 순 알람 100건", []),
    ("알람 이력", []),
]


class TestAlarmPeriod:
    @pytest.mark.parametrize(("query", "expected"), _ALARM_CASES)
    @pytest.mark.parametrize(("engine", "schema"), _DIALECTS)
    def test_history_ctime_bounds(self, query, expected, engine, schema):
        sql = _alarm(query, engine, schema, period=qt(query).event)
        assert sql is not None
        assert f"FROM {schema}.cmm_alarm a " in sql
        assert _ctime_conds(sql) == expected
        if engine == "db2":
            assert "FETCH FIRST" in sql and "LIMIT" not in sql
        if expected:
            assert sql_applies_period(sql, qt(query).event)

    def test_server_group_with_period(self):
        sql = _alarm("지난주 서버별 알람 건수", period=qt("지난주 서버별 알람 건수").event)
        assert sql is not None and "GROUP BY" in sql
        assert _ctime_conds(sql) == [
            "a.ctime >= TIMESTAMP '2026-09-21 00:00:00'",
            "a.ctime < TIMESTAMP '2026-09-28 00:00:00'",
        ]

    def test_open_start_until(self):
        """「~까지」 = 왼쪽이 열린 구간 — 끝 조건만."""
        sql = _alarm("8월까지 알람", period=qt("8월까지 알람").event)
        assert _ctime_conds(sql or "") == ["a.ctime < TIMESTAMP '2026-09-01 00:00:00'"]

    def test_active_snapshot_has_no_period(self):
        """「현재 활성」 — 사건 해석이 기간 조건 없음이라 활성 스냅샷(종전과 같은 모드)."""
        spec = asm.recognize_active_alarm_query("현재 활성 알람", period=qt("현재 활성 알람").event)
        assert spec is not None and spec.mode == "active" and spec.ts_bounds is None

    def test_active_plus_period_still_ambiguous(self):
        """인식 보수 원칙(활성 + 기간 동시 = 모호 → LLM)은 그대로다."""
        assert asm.recognize_active_alarm_query(
            "활성 알람 지난달", period=qt("활성 알람 지난달").event
        ) is None

    def test_defect8_history_without_month_range_closed(self):
        """종전 ⑧: 「지난주」는 이력 어휘만 잡혀 월 범위 None → 전 이력.

        period 경로는 경계를 싣는다.
        """
        old = asm.recognize_active_alarm_query("지난주 알람")
        assert old is not None and old.mode == "history" and old.month_range is None
        new = asm.recognize_active_alarm_query("지난주 알람", period=qt("지난주 알람").event)
        assert new is not None and new.ts_bounds == ("2026-09-21 00:00:00", "2026-09-28 00:00:00")

    def test_period_ignores_parsed_time_range(self):
        """period가 단일 출처 — LLM 원시 time_range(2단 폴백)를 보지 않는다."""
        sql = _alarm(
            "어제 알람", period=qt("어제 알람").event,
            parsed_time_range={"start": "2026-05-01", "end": "2026-05-31"},
        )
        assert "2026-05" not in (sql or "")
        assert "TIMESTAMP '2026-09-28 00:00:00'" in (sql or "")

    def test_active_builder_honours_ts_bounds(self):
        """spec에 실린 기간을 활성 빌더가 버리지 않는다(침묵 드롭 금지)."""
        spec = asm.ActiveAlarmSpec(
            severity=None, severity_op="=", unack_only=False, count_only=True,
            ts_bounds=("2026-09-28 00:00:00", "2026-09-29 00:00:00"),
        )
        sql = asm.build_active_alarm_sql(
            spec, db_engine="postgresql", db_schema="polestar", limit=10
        )
        assert _ctime_conds(sql) == [
            "a.ctime >= TIMESTAMP '2026-09-28 00:00:00'",
            "a.ctime < TIMESTAMP '2026-09-29 00:00:00'",
        ]

    def test_period_none_byte_identical_golden(self):
        """period None = 종전 SQL 바이트 동일(기준선 `71d7ff6` 채취 · 날짜 비의존 질의)."""
        golden = (
            "SELECT a.id AS alarm_id, a.alarmseverity AS severity, a.conditionlogtext AS "
            "description, a.ctime AS alarm_time, COALESCE(srv.name, srv.hostname, res.name) AS "
            "server_name, srv.hostname AS hostname, srv.ipaddress AS ipaddress, res.name AS "
            "resource_name, d.name AS alarm_name FROM polestar.cmm_alarm a JOIN "
            "polestar.cmm_resource res ON a.resource_id = res.id LEFT JOIN polestar.cmm_alarm_def "
            "d ON a.definition_id = d.id LEFT JOIN polestar.cmm_resource srv ON srv.id = "
            "COALESCE(res.platform_resource_id, res.service_resource_id, res.id) WHERE "
            "res.dtime IS NULL ORDER BY a.ctime DESC, a.id DESC LIMIT 100"
        )
        assert _alarm("최근 발생 순 알람 100건") == golden
        assert _alarm("최근 발생 순 알람 100건", period=None) == golden
        assert asm.ActiveAlarmSpec(
            severity=None, severity_op="=", unack_only=False, count_only=False
        ).ts_bounds is None


# ──────────────────────────────────────────────
# 폼필 결정적 조립 — 피벗 기간 · 월 시리즈 앵커
# ──────────────────────────────────────────────

def _pivot(period=None, stat_month=None, month_measures=None, engine="postgresql",
           schema="polestar") -> str:
    return asm.build_form_fill_pivot_sql(
        [("서버명", "cmm_resource.name")], [("OS", "OSName")], [], _EAV,
        metric_fields=["CPU 평균"], db_engine=engine, db_schema=schema, limit=1000,
        stat_month=stat_month, month_measures=month_measures, period=period,
    )


def _metric_join(sql: str) -> str:
    return next(line for line in sql.splitlines() if " s ON " in line)


class TestFormFillPivotPeriod:
    @pytest.mark.parametrize(("query", "table", "cond"), [
        ("지난달 CPU", "cmm_metric_stat_m",
         "s.stat_date >= '202608' AND s.stat_date < '202609'"),
        ("지난 3개월 CPU", "cmm_metric_stat_m",
         "s.stat_date >= '202606' AND s.stat_date < '202609'"),
        ("최근 30일 CPU", "cmm_metric_stat_d",
         "s.stat_date >= '20260830' AND s.stat_date < '20260929'"),
        ("이번 달 CPU", "cmm_metric_stat_d",
         "s.stat_date >= '20260901' AND s.stat_date < '20260929'"),
        ("최근 3시간 CPU", "cmm_metric_stat_h",
         "s.stat_date >= '2026092907' AND s.stat_date < '2026092910'"),
        # 기간 미지정 = 직전 완결 월(source=default · 고지는 T-8)
        ("CPU 사용률", "cmm_metric_stat_m",
         "s.stat_date >= '202608' AND s.stat_date < '202609'"),
    ])
    @pytest.mark.parametrize(("engine", "schema"), _DIALECTS)
    def test_table_and_literal_bounds(self, query, table, cond, engine, schema):
        period = qt(query).metric
        sql = _pivot(period=period, engine=engine, schema=schema)
        join = _metric_join(sql)
        assert f"LEFT JOIN {schema}.{table} s ON" in join
        assert join.endswith(f"AND {cond}")
        assert sql_applies_period(sql, period)

    def test_unbounded_has_no_condition(self):
        period = qt("알람이 발생한 적이 있는 서버 CPU").metric
        assert period is not None and period.unbounded
        join = _metric_join(_pivot(period=period))
        assert "stat_date" not in join and "cmm_metric_stat_m" in join

    def test_period_overrides_stat_month(self):
        """period가 단일 출처 — 호출부가 함께 넘긴 종전 stat_month는 쓰지 않는다."""
        sql = _pivot(period=qt("최근 30일 CPU").metric, stat_month=("202608", "202609"))
        assert "BETWEEN" not in _metric_join(sql)
        assert "'20260830'" in sql

    def test_month_grain_same_set_as_stat_month(self):
        """월 입도는 종전 `BETWEEN` 표기와 같은 월 집합(lo = 시작 · last = 끝 포함 월)."""
        old = _metric_join(_pivot(stat_month=("202606", "202608")))
        new = _metric_join(_pivot(period=qt("지난 3개월 CPU").metric))
        assert "BETWEEN '202606' AND '202608'" in old
        assert "s.stat_date >= '202606' AND s.stat_date < '202609'" in new

    def test_month_measures_override_period(self):
        """D-185 우선순위: 월 시리즈 항목 월 범위가 조인 조건을 덮는다(월 통계 CASE 피벗)."""
        period = qt("최근 30일 CPU").metric
        mm = [("cpu|M", "server.Cpus", "avg_val", "202607"),
              ("cpu|M+1", "server.Cpus", "avg_val", "202608")]
        sql = _pivot(period=period, month_measures=mm)
        join = _metric_join(sql)
        assert "cmm_metric_stat_m" in join
        assert join.endswith("s.stat_date BETWEEN '202607' AND '202608'")
        assert "20260830" not in sql  # 일 경계는 의도적으로 덮였다(앵커에 반영)

    def test_period_none_byte_identical_golden(self):
        golden = (
            "LEFT JOIN polestar.cmm_metric_stat_m s ON s.resource_id = c.id AND "
            "s.definition_name = 'Utilization' AND s.stat_date BETWEEN '202604' AND '202606'"
        )
        assert _metric_join(_pivot(stat_month=("202604", "202606"))) == golden
        assert _pivot(stat_month="202604") == _pivot(stat_month="202604", period=None)


_AVG = "월중평균사용률(최근 6개월간)"
_REL3 = {f"{_AVG}|M": None, f"{_AVG}|M+1": None, f"{_AVG}|M+2": None, "서버명": "cmm_resource.name"}


def _ms(query: str, *, period=True, mapping=None):
    return asm.recognize_month_series(
        mapping or _REL3, context_text="CPU 사용률 보고", user_query=query,
        period=qt(query).metric if period else None,
    )


class TestMonthSeriesAnchor:
    @pytest.mark.parametrize(("query", "anchor", "requested", "source"), [
        ("지난 3개월", ("202606", "202608"), ("202606", "202608"), "query"),
        ("1월부터 6월까지", ("202604", "202606"), ("202601", "202606"), "query"),
        # 월 경계가 아닌 기간 — 앵커 끝은 마지막 완결 월(진행 중 9월 월 통계 없음)
        ("최근 30일", ("202606", "202608"), ("202608", "202609"), "query"),
        ("어제", ("202606", "202608"), ("202609", "202609"), "query"),
        ("지난주", ("202606", "202608"), ("202609", "202609"), "query"),
        # 기간 미지정 = 종전(지난달)
        ("", ("202606", "202608"), None, "default"),
        # 진행 중 반기는 달력 구간 그대로(D-185 현행 — 종전 정규식 해석과 같다)
        ("하반기", ("202610", "202612"), ("202607", "202612"), "query"),
    ])
    def test_anchor(self, query, anchor, requested, source):
        ms = _ms(query)
        assert ms is not None
        assert ms.anchor == anchor
        assert ms.requested == requested
        assert ms.anchor_source == source

    def test_anchor_uses_period_anchor_date(self):
        """기준일은 해석의 기준 시각 — 벽시계(date.today)가 아니다."""
        ms = _ms("")
        assert ms is not None and ms.anchor[1] == "202608"

    def test_month_series_sql_keeps_period_when_month_aligned(self):
        """월 경계 기간이면 앵커 = 기간 → 최종 SQL에 그 기간이 실제로 남는다."""
        ms = _ms("지난 3개월")
        assert ms is not None
        period = qt("지난 3개월").metric
        sql = _pivot(period=period, month_measures=ms.measures)
        assert _metric_join(sql).endswith("s.stat_date BETWEEN '202606' AND '202608'")
        assert sql_applies_period(sql, period)

    def test_period_none_matches_legacy(self):
        legacy = asm.recognize_month_series(
            _REL3, context_text="CPU 사용률 보고", user_query="지난 3개월",
            today=date(2026, 9, 29),
        )
        assert legacy is not None and legacy.anchor == ("202606", "202608")
        assert legacy.requested == ("202606", "202608")


# ──────────────────────────────────────────────
# T-7 시맨틱 컴파일러
# ──────────────────────────────────────────────

def _b(**extra) -> SMQ:
    d = {
        "pattern": "B", "resource_types": ["server.Cpus"], "dimensions": ["name"],
        "measures": [{"agg": "avg", "definition_name": "Utilization",
                      "resource_type": "server.Cpus"}],
    }
    d.update(extra)
    return SMQ.from_dict(d)


def _c(**extra) -> SMQ:
    d = {"pattern": "C", "entities": ["CMM_ALARM"], "dimensions": ["server_name"]}
    d.update(extra)
    return SMQ.from_dict(d)


def _stat_join(sql: str) -> str:
    return next(line for line in sql.splitlines() if "cmm_metric_stat" in line)


class TestSemanticCompilerPeriod:
    @pytest.mark.parametrize(("query", "table", "cond"), [
        ("지난달 CPU 사용률", "cmm_metric_stat_m",
         "s.stat_date >= '202608' AND s.stat_date < '202609'"),
        ("최근 30일 CPU 사용률", "cmm_metric_stat_d",
         "s.stat_date >= '20260830' AND s.stat_date < '20260929'"),
        ("최근 3시간 CPU 사용률", "cmm_metric_stat_h",
         "s.stat_date >= '2026092907' AND s.stat_date < '2026092910'"),
        ("시간별 지난달 CPU 사용률", "cmm_metric_stat_h",
         "s.stat_date >= '2026080100' AND s.stat_date < '2026090100'"),
    ])
    def test_pattern_b_bounds(self, query, table, cond):
        sql = sc.compile_smq(_b(), _GP, user_query=query, query_time=qt(query))
        join = _stat_join(sql)
        assert f"polestar.{table} s ON" in join and join.endswith(cond)
        assert sql_applies_period(sql, qt(query).metric)

    def test_db2_dialect(self):
        sql = sc.compile_smq(_b(), _B0, user_query="최근 30일 CPU 사용률",
                             query_time=qt("최근 30일 CPU 사용률"))
        assert "POLESTAR.cmm_metric_stat_d s ON" in sql
        assert "s.stat_date >= '20260830' AND s.stat_date < '20260929'" in sql
        assert "FETCH FIRST" in sql

    def test_defect8_hour_grain_with_month_literal(self):
        """⑧: time_grain=hour + YYYYMM이면 시간 통계에 월 리터럴(0행).

        query_time 경로는 해석 입도를 쓴다.
        """
        smq = _b(time_grain="hour", time_range=["202608"])
        legacy = sc.compile_smq(smq, _GP, user_query="지난달 CPU 사용률", stat_month="202608")
        assert "cmm_metric_stat_h s ON" in legacy and "s.stat_date = '202608'" in legacy
        reset_guard_counters()
        sql = sc.compile_smq(smq, _GP, user_query="지난달 CPU 사용률",
                             query_time=qt("지난달 CPU 사용률"))
        assert _stat_join(sql).endswith(
            "cmm_metric_stat_m s ON s.resource_id = c.id AND s.definition_name = 'Utilization' "
            "AND s.stat_date >= '202608' AND s.stat_date < '202609'"
        )
        assert guard_counters().get("normalize.time_grain_override") == 1

    def test_current_month_d201_same_set(self):
        """「이번 달」(D-201): 해석이 이미 일 입도 — 종전 일간 전환과 같은 [1일, 어제]."""
        sql = sc.compile_smq(_b(), _GP, user_query="이번 달 CPU 사용률",
                             query_time=qt("이번 달 CPU 사용률"))
        assert _stat_join(sql).endswith(
            "cmm_metric_stat_d s ON s.resource_id = c.id AND s.definition_name = 'Utilization' "
            "AND s.stat_date >= '20260901' AND s.stat_date < '20260929'"
        )

    def test_query_time_overrides_stat_month_and_ir(self):
        smq = _b(time_range=["202605"])
        sql = sc.compile_smq(smq, _GP, user_query="최근 30일 CPU 사용률", stat_month="202605",
                             query_time=qt("최근 30일 CPU 사용률"))
        assert "202605" not in sql and "'20260830'" in sql

    def test_present_default_keeps_legacy(self):
        """「현재」 + 기간 없음은 기본값(지난달)을 강제하지 않는다 — 종전 stat_month 경로."""
        q = qt("현재 CPU 사용률")
        assert q.present and not q.uses_default_period
        sql = sc.compile_smq(_b(), _GP, user_query="현재 CPU 사용률", query_time=q)
        assert sql == sc.compile_smq(_b(), _GP, user_query="현재 CPU 사용률")
        assert "stat_date" not in _stat_join(sql)

    def test_default_period_applied(self):
        """기간 미지정(현재·지금 아님) = 직전 완결 월 리터럴(§10.3.1 「기간 없음」)."""
        q = "CPU 사용률 상위"
        sql = sc.compile_smq(_b(), _GP, user_query=q, query_time=qt(q))
        assert _stat_join(sql).endswith("s.stat_date >= '202608' AND s.stat_date < '202609'")

    @pytest.mark.parametrize(("query", "expected"), [
        ("어제 알람", ["CA.CTIME >= TIMESTAMP '2026-09-28 00:00:00'",
                     "CA.CTIME < TIMESTAMP '2026-09-29 00:00:00'"]),
        ("이번 달 알람", ["CA.CTIME >= TIMESTAMP '2026-09-01 00:00:00'",
                       "CA.CTIME < TIMESTAMP '2026-09-29 10:00:00'"]),
        ("최근 발생 순 알람 100건", []),
    ])
    def test_pattern_c_event_bounds(self, query, expected):
        """패턴 C = 사건 해석 — IR 월 창(DATE)은 쓰지 않는다(기간 없음 = 조건 없음)."""
        sql = sc.compile_smq(_c(time_range=["202608"]), _GP, user_query=query, query_time=qt(query))
        assert re.findall(r"CA\.CTIME [<>=]+ [A-Z]+\s*\(?'[^']+'\)?", sql) == expected
        assert "DATE(" not in sql

    def test_pattern_c_missing_ctime_declines(self, monkeypatch):
        model = sc.load_semantic_model(_GP)
        stripped = {**model, "pattern_c": {
            **model["pattern_c"],
            "dimensions": {k: v for k, v in model["pattern_c"]["dimensions"].items()
                           if k.upper() != "CTIME"},
        }}
        with pytest.raises(sc.PeriodUncompilableError):
            sc.compile_smq(_c(), _GP, stripped, user_query="어제 알람", query_time=qt("어제 알람"))

    def test_pattern_b_missing_grain_table_declines(self):
        model = sc.load_semantic_model(_GP)
        stripped = {**model, "pattern_b": {
            **model["pattern_b"],
            "metric_tables": {k: v for k, v in model["pattern_b"]["metric_tables"].items()
                              if k != "hour"},
        }}
        with pytest.raises(sc.PeriodUncompilableError):
            sc.compile_smq(_b(), _GP, stripped, user_query="최근 3시간 CPU 사용률",
                           query_time=qt("최근 3시간 CPU 사용률"))

    def test_query_time_none_byte_identical_golden(self):
        golden = (
            "LEFT JOIN POLESTAR.cmm_metric_stat_m s ON s.resource_id = c.id AND "
            "s.definition_name = 'Utilization' AND s.stat_date BETWEEN '202604' AND '202606'"
        )
        sql = sc.compile_smq(_b(), _B0, user_query="CPU", stat_month=("202604", "202606"))
        assert _stat_join(sql) == golden
        assert sql == sc.compile_smq(
            _b(), _B0, user_query="CPU", stat_month=("202604", "202606"), query_time=None
        )


class TestPromoteTimeFilters:
    _TIME_FILTER = [{"field": "time", "op": "between", "value": ["202605", "202605"]}]

    def test_month_period_corrects_ir(self):
        model = sc.load_semantic_model(_GP)
        smq = sc.normalize_smq(_b(filters=self._TIME_FILTER), "지난달 CPU", model,
                               query_time=qt("지난달 CPU"))
        assert smq.filters == [] and smq.time_range == ["202608"]

    def test_day_period_strips_filter_and_clears_ir(self):
        """월로 표현되지 않는 기간 — 필터는 걷고 IR은 비운다(컴파일이 해석에서 경계를 만든다)."""
        model = sc.load_semantic_model(_GP)
        smq = sc.normalize_smq(_b(filters=self._TIME_FILTER), "지난주 CPU", model,
                               query_time=qt("지난주 CPU"))
        assert smq.filters == [] and smq.time_range is None
        assert sc.check_coverage(smq, model).covered

    def test_config_query_keeps_filter_for_fallback(self):
        """통계 조인이 없는 설정 조회(measure 없는 A)는 기간을 적용할 곳이 없다 — 폴백 유지."""
        model = sc.load_semantic_model(_GP)
        smq = SMQ.from_dict({"pattern": "A", "dimensions": ["name"], "filters": self._TIME_FILTER})
        out = sc.normalize_smq(smq, "지난주 서버 목록", model, query_time=qt("지난주 서버 목록"))
        assert out.filters and not sc.check_coverage(out, model).covered

    def test_query_time_none_legacy(self):
        """종전: 「지난주」는 결정적 월이 없어 LLM이 계산한 월(202605)을 IR에 그대로 싣는다."""
        model = sc.load_semantic_model(_GP)
        smq = sc.normalize_smq(_b(filters=self._TIME_FILTER), "지난주 CPU", model)
        assert smq.filters == [] and smq.time_range == ["202605"]


class _FakeLLM:
    def __init__(self, payload: dict):
        self._content = json.dumps(payload, ensure_ascii=False)

    async def ainvoke(self, messages):
        return SimpleNamespace(content=self._content)


_B_PAYLOAD = {
    "pattern": "B", "dimensions": ["name"], "time_grain": "month",
    "measures": [{"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}],
}


class TestCompileFromNl:
    @pytest.mark.asyncio
    async def test_query_time_reaches_sql(self):
        q = "최근 30일 CPU 사용률 상위 5대"
        sql, _smq, cov = await sc.compile_from_nl(
            _FakeLLM(_B_PAYLOAD), q, _GP, stat_month=("202608", "202609"), query_time=qt(q),
        )
        assert cov is not None and cov.covered
        assert "cmm_metric_stat_d" in (sql or "")
        assert sql_applies_period(sql or "", qt(q).metric)

    @pytest.mark.asyncio
    async def test_uncompilable_period_falls_back_with_reason(self, monkeypatch):
        model = sc.load_semantic_model(_GP)
        stripped = {**model, "pattern_b": {
            **model["pattern_b"],
            "metric_tables": {k: v for k, v in model["pattern_b"]["metric_tables"].items()
                              if k != "hour"},
        }}
        monkeypatch.setattr(sc, "load_semantic_model", lambda db_id, **kw: stripped)
        reset_guard_counters()
        q = "최근 3시간 CPU 사용률"
        sql, _smq, cov = await sc.compile_from_nl(_FakeLLM(_B_PAYLOAD), q, _GP, query_time=qt(q))
        assert sql is None and cov is not None and not cov.covered
        assert "hour" in cov.reason
        assert guard_counters().get("gate.period_uncompilable") == 1


# ──────────────────────────────────────────────
# 도구 — ToolContext.time_resolution · resolve_time_range
# ──────────────────────────────────────────────

class TestTimeRangeTool:
    def test_day_period(self):
        out = resolve_time_range("무관한 문장", query_time=qt("최근 30일 CPU"))
        assert out == {
            "resolved": True, "start": None, "end": None, "time_range": None,
            "grain": "day", "label": "2026-08-30 ~ 2026-09-28", "source": "rule",
            "period_start": "2026-08-30T00:00:00+09:00",
            "period_end": "2026-09-29T00:00:00+09:00",
        }

    def test_month_period_keeps_legacy_keys(self):
        out = resolve_time_range("x", query_time=qt("지난 3개월 CPU"))
        assert (out["resolved"], out["start"], out["end"]) == (True, "202606", "202608")
        assert out["time_range"] == ["202606", "202608"] and out["grain"] == "month"

    def test_default_period_is_not_resolved(self):
        out = resolve_time_range("x", query_time=qt("CPU 사용률"))
        assert out["resolved"] is False and out["source"] == "default"
        assert out["time_range"] == ["202608"]

    def test_present_default_and_none_are_legacy(self):
        legacy = {"resolved": False, "start": None, "end": None}
        assert resolve_time_range("현재 CPU 사용률", query_time=qt("현재 CPU 사용률")) == legacy
        assert resolve_time_range("CPU 사용률", today=date(2026, 9, 29)) == legacy
        assert resolve_time_range("지난달 CPU", today=date(2026, 9, 29)) == {
            "resolved": True, "start": "202608", "end": "202608",
        }

    def test_context_resolution_reaches_tool(self):
        q = "최근 30일 CPU"
        ctx = ToolContext(user_query=q, time_resolution=qt(q).to_state())
        tool = next(t for t in build_query_tools(ctx) if t.name == "resolve_time_range")
        payload = json.loads(tool.invoke({"query": "다른 문장"}))
        assert payload["grain"] == "day" and payload["label"] == "2026-08-30 ~ 2026-09-28"

    def test_context_without_resolution_is_legacy(self):
        tool = next(t for t in build_query_tools(ToolContext()) if t.name == "resolve_time_range")
        payload = json.loads(tool.invoke({"query": "CPU 사용률"}))
        assert payload == {"resolved": False, "start": None, "end": None}

    def test_validate_tool_passes_time_resolution(self, monkeypatch):
        captured: dict = {}

        def fake_validate(sql, schema_info, **kw):
            captured.update(kw)
            return {"valid": True, "errors": [], "warnings": [], "fixed_sql": sql}

        monkeypatch.setattr("src.tools.binding.validate_sql_draft", fake_validate)
        state = qt("어제 CPU").to_state()
        ctx = ToolContext(schema_info={"tables": {"t": {}}}, time_resolution=state)
        tool = next(t for t in build_query_tools(ctx) if t.name == "validate_sql_draft")
        tool.invoke({"sql": "SELECT 1"})
        assert captured["time_resolution"] == state
