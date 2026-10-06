"""plans/122 T-9 — 시나리오 시간 단언(정확 창 `exact` · 일·주·시 상대 기간 · 입도 · 함수식 부정).

고정하는 계약:
  1. `sql_period_bounds` 는 시 단위 리터럴(`YYYYMMDDHH`)을 그 날짜로 읽는다. 10자리 숫자가 없는
     SQL 의 경계는 종전과 같다(종전 정규식 사본으로 대조 — 「덮는다」 판정 불변).
  2. `sql_period_extent` 는 비교 연산자로 끝을 읽는다(`<` 배타 · `<=`·`=`·BETWEEN·IN 포함 ·
     TIMESTAMP 시각은 끝을 자정으로 올린다).
  3. `exact: true` — 창과 같아야 통과(과대 확장·축소·리터럴 없음은 불합격 · SQL 미관측은 보류).
     키가 없으면 종전 「덮는다」 판정.
  4. 일·시 단위 창은 앵커가 자정 ±10분이면 보류 · 월 경계 보류는 전 종류 그대로.
  5. 로더가 새 종류·`exact` 를 받고 틀린 모양을 거부한다 · 하네스 사본이 해석기 종류와 같다.
  6. 카탈로그 장부(122 소유 11파일) — 시간 골드셋(`testdata/time_gold/catalog.yaml`)의 턴마다 단언을
     붙였거나 제외 사유가 있다. 함수식 부정 패턴은 하네스 `_DB_NOW_RE` 와 같은 목록이다.
  7. 정답 SQL(제품 해석기 `resolve_query_time` + 폴스타 리터럴 투영 `stat_bounds`·
     `alarm_ts_bounds`)은
     붙인 단언을 통과하고(PG·DB2), §10.1 오답 형태는 불합격이다(새 거짓 합격 0 · 정답 불합격 0).
  8. 122 소유 밖(R·E군) §10.1 오답도 같은 단언 수단으로 불합격이 된다 — 카탈로그 반영은
     소유 계획 몫.
앵커는 월 경계가 아닌 날(2026-09-23 수 10:00 KST · run `anchor_at` 형식)이다. 전부 무과금이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.scenario.assertions import (
    _DB_NOW_RE,
    DAY_LEVEL_KINDS,
    Observation,
    Verdict,
    _period_window,
    evaluate_turn,
    near_day_boundary,
    sql_period_bounds,
    sql_period_extent,
)
from scripts.scenario.catalog import (
    RELATIVE_N_UNITS,
    RELATIVE_PERIODS,
    CatalogError,
    Group,
    Scenario,
    Turn,
    load_catalog,
)
from src.db_adapters.polestar.time_period import alarm_ts_bounds, stat_bounds
from src.domain.query_time import resolve_query_time
from src.domain.time_spec import (
    KST,
    N_MAX,
    RELATIVE_WINDOW_KINDS,
    TimeResolution,
    _add_months,
)
from tests.test_scenario.conftest import GOOD_GROUP, write

REPO_ROOT = Path(__file__).resolve().parents[2]
GROUP = Group("T", "T", 60000)
ANCHOR = "2026-09-23T10:00:00+09:00"          # 수요일 · 월 경계 아님(near_month_boundary 거짓)
ANCHOR_AT = datetime.fromisoformat(ANCHOR)
#: 하네스 `_DB_NOW_RE` 와 같은 목록 — 카탈로그 함수식 부정 단언의 정본 문자열.
FN = _DB_NOW_RE.pattern
#: 122 소유 카탈로그(plans/122 §14.10 경계 — R군은 123 · E·M·FS군은 121·125 소유).
OWNED = ("a_routing", "b_resource", "c_metric", "d_alarm", "f_zone_hitl", "g_multiturn",
         "h_formfill", "i_formfill_hitl", "j_guard", "k_load", "l_synonym")


def _judge(expect: dict[str, Any], sqls: list[str], *, anchor: str | None = ANCHOR) -> Verdict:
    scenario = Scenario(id="T-01", group="T", plans=[122], title="t",
                        turns=[Turn({"query": "q"}, expect)])
    obs = Observation(http_status=200, status="completed", response="r",
                      executed_sqls=list(sqls), anchor_at=anchor)
    return evaluate_turn(scenario, 1, scenario.turns[0], obs, GROUP)


def _keys(verdict: Verdict) -> set[str]:
    return {failure.key for failure in verdict.failures}


# --- 1 시 단위 리터럴 읽기 · 종전 경계 불변 ------------------------------------------------------

#: 종전(`71d7ff6`) `_DATE_LITERAL_RE`·`sql_period_bounds` 사본 — 대조용이다.
_OLD_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{4})(\d{2})(\d{2})\b|\b(\d{4})(\d{2})\b")


def _old_bounds(sqls: list[str]) -> tuple[date | None, date | None]:
    bounds: list[date] = []
    for sql in sqls:
        for match in _OLD_RE.finditer(sql):
            try:
                if match.group(1):
                    bounds.append(date(int(match.group(1)), int(match.group(2)),
                                       int(match.group(3))))
                elif match.group(4):
                    bounds.append(date(int(match.group(4)), int(match.group(5)),
                                       int(match.group(6))))
                else:
                    start = date(int(match.group(7)), int(match.group(8)), 1)
                    nxt = (date(start.year + 1, 1, 1) if start.month == 12
                           else date(start.year, start.month + 1, 1))
                    bounds.extend([start, nxt])
            except ValueError:
                continue
    return (min(bounds), max(bounds)) if bounds else (None, None)


#: 10자리 숫자가 없는 SQL 형태 — 종전 테스트·세 run 실측 표기(월 등호·BETWEEN·IN·TIMESTAMP·DB2·
#: 함수식·식별자 숫자·LIMIT).
BOUNDS_CORPUS = [
    "SELECT 1 FROM m WHERE stat_date = '202608'",
    "SELECT 1 FROM m WHERE stat_date BETWEEN '202606' AND '202608'",
    "SELECT 1 FROM m WHERE s.stat_date IN ('202605','202606')",
    "SELECT 1 FROM d WHERE stat_date >= '20260901' AND stat_date <= '20260914'",
    "SELECT 1 FROM a WHERE ctime >= TIMESTAMP '2026-07-01 00:00:00' AND a.ctime < "
    "TIMESTAMP '2026-08-01 00:00:00'",
    "SELECT 1 FROM POLESTAR.CMM_ALARM A WHERE A.CTIME >= TIMESTAMP '2026-09-01' FETCH FIRST 10 "
    "ROWS ONLY",
    "SELECT 1 FROM m WHERE s.stat_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMM')",
    "SELECT TO_DATE(s.stat_date || '01', 'YYYYMMDD') AS stat_month FROM m s LIMIT 10000",
    "SELECT 1 FROM r WHERE serial = 'KR2023ORA0023' AND total_size = '65536'",
    "SELECT 1 FROM m WHERE d = '20261341' OR d = '202613'",            # 날짜가 아닌 숫자
    "SELECT 1 FROM m WHERE stat_date LIKE '202608%'",
    "SELECT 1 FROM r LIMIT 100000",
]


@pytest.mark.parametrize("sql", BOUNDS_CORPUS)
def test_bounds_unchanged_without_hour_literals(sql: str) -> None:
    """10자리 숫자가 없는 SQL 의 경계는 종전과 같다(「덮는다」 판정 불변)."""
    assert sql_period_bounds([sql]) == _old_bounds([sql])


@pytest.mark.parametrize("sql, want", [
    ("SELECT 1 FROM h WHERE stat_date >= '2026082400' AND stat_date < '2026092300'",
     (date(2026, 8, 24), date(2026, 9, 23))),
    ("SELECT 1 FROM h WHERE stat_date = '2026092309'", (date(2026, 9, 23), date(2026, 9, 23))),
    ("SELECT 1 FROM h WHERE stat_date = '2026092324'", (None, None)),   # 24시는 시가 아니다
    ("SELECT 1 FROM r WHERE id = '1234567890'", (None, None)),          # 날짜가 아닌 10자리
])
def test_hour_literal_read_as_its_date(sql: str, want: tuple[date | None, date | None]) -> None:
    """시 단위 리터럴은 그 날짜로 읽는다 — 종전에는 리터럴 없음이었다."""
    assert _old_bounds([sql]) == (None, None)
    assert sql_period_bounds([sql]) == want


# --- 2 연산자 읽기 ------------------------------------------------------------------------------

@pytest.mark.parametrize("sql, want", [
    ("s.stat_date >= '202608' AND s.stat_date < '202609'", (date(2026, 8, 1), date(2026, 9, 1))),
    ("s.stat_date = '202608'", (date(2026, 8, 1), date(2026, 9, 1))),
    ("s.stat_date BETWEEN '202606' AND '202608'", (date(2026, 6, 1), date(2026, 9, 1))),
    ("s.stat_date IN ('202605', '202606')", (date(2026, 5, 1), date(2026, 7, 1))),
    ("s.stat_date > '202605' AND s.stat_date <= '202608'", (date(2026, 6, 1), date(2026, 9, 1))),
    ("s.stat_date >= '20260922' AND s.stat_date < '20260923'",
     (date(2026, 9, 22), date(2026, 9, 23))),
    ("s.stat_date >= '2026092309' AND s.stat_date < '2026092310'",     # 시 → 끝 올림
     (date(2026, 9, 23), date(2026, 9, 24))),
    ("s.stat_date >= '2026082400' AND s.stat_date < '2026092300'",
     (date(2026, 8, 24), date(2026, 9, 23))),
    ("a.ctime >= TIMESTAMP '2026-09-22 00:00:00' AND a.ctime < TIMESTAMP '2026-09-23 00:00:00'",
     (date(2026, 9, 22), date(2026, 9, 23))),
    ("a.ctime >= TIMESTAMP '2026-09-01 00:00:00' AND a.ctime < TIMESTAMP '2026-09-23 10:00:00'",
     (date(2026, 9, 1), date(2026, 9, 24))),
    ("A.CTIME >= TIMESTAMP('2026-09-22 00:00:00') AND A.CTIME < TIMESTAMP('2026-09-23 00:00:00')",
     (date(2026, 9, 22), date(2026, 9, 23))),                          # DB2 함수형
    ("s.stat_date >= TO_DATE('20260922', 'YYYYMMDD')", (date(2026, 9, 22), None)),
    ("s.stat_date >= '20260901'", (date(2026, 9, 1), None)),           # 끝이 열렸다
    ("s.stat_date < '202609'", (None, date(2026, 9, 1))),
    ("s.max_val > 90", (None, None)),
])
def test_sql_period_extent_reads_operators(
    sql: str, want: tuple[date | None, date | None],
) -> None:
    """정확 창은 연산자로 끝을 읽는다(`<` 배타 끝 · 그 밖 포함)."""
    assert sql_period_extent([sql]) == want


# --- 3 exact 판정 -------------------------------------------------------------------------------

@pytest.mark.parametrize("spec, sql, verdict", [
    # 어제 = [09-22, 09-23)
    ({"relative": "yesterday", "exact": True},
     "SELECT 1 FROM a WHERE a.ctime >= TIMESTAMP '2026-09-22 00:00:00' "
     "AND a.ctime < TIMESTAMP '2026-09-23 00:00:00'", "pass"),
    ({"relative": "yesterday", "exact": True}, "SELECT 1 FROM d WHERE s.stat_date = '20260922'",
     "pass"),
    ({"relative": "yesterday", "exact": True},        # §10.1 R1-08 — 어제 알람 → 9월 전체
     "SELECT 1 FROM a WHERE a.ctime >= '2026-09-01' AND a.ctime < '2026-10-01'", "fail"),
    ({"relative": "yesterday"},                        # 키가 없으면 종전 「덮는다」 — 통과
     "SELECT 1 FROM a WHERE a.ctime >= '2026-09-01' AND a.ctime < '2026-10-01'", "pass"),
    # 지난달 = [08-01, 09-01) — 배타 끝 `< '202609'` 은 9월 전체가 아니다
    ({"relative": "last_month", "exact": True},
     "SELECT 1 FROM m WHERE s.stat_date >= '202608' AND s.stat_date < '202609'", "pass"),
    ({"relative": "last_month", "exact": True},
     "SELECT 1 FROM m WHERE s.stat_date BETWEEN '202608' AND '202609'", "fail"),
    ({"relative": "last_month"},
     "SELECT 1 FROM m WHERE s.stat_date BETWEEN '202608' AND '202609'", "pass"),
    ({"relative": "last_month", "exact": True},        # 축소도 불합격
     "SELECT 1 FROM d WHERE s.stat_date >= '20260810' AND s.stat_date < '20260901'", "fail"),
    # 최근 30일 = [08-24, 09-23) — §10.1 K-09·R4-02C 월 붕괴
    ({"relative": "last_n_days", "n": 30, "exact": True},
     "SELECT 1 FROM h WHERE s.stat_date >= '2026082400' AND s.stat_date < '2026092300'", "pass"),
    ({"relative": "last_n_days", "n": 30, "exact": True},
     "SELECT 1 FROM m WHERE s.stat_date = '202608'", "fail"),
    # 최근 1시간 → 날짜 창 [09-23, 09-24) — §10.1 R4-03C 일 통계 이번 달 1일~
    ({"relative": "last_n_hours", "n": 1, "exact": True},
     "SELECT 1 FROM h WHERE s.stat_date >= '2026092309' AND s.stat_date < '2026092310'", "pass"),
    ({"relative": "last_n_hours", "n": 1, "exact": True},
     "SELECT 1 FROM d WHERE s.stat_date >= '20260901'", "fail"),
    # 지난주 = [09-14, 09-21) — §10.1 R3-06C 9월 전체
    ({"relative": "last_week", "exact": True},
     "SELECT 1 FROM a WHERE ctime >= TIMESTAMP '2026-09-14 00:00:00' "
     "AND ctime < TIMESTAMP '2026-09-21 00:00:00'", "pass"),
    ({"relative": "last_week", "exact": True},
     "SELECT 1 FROM a WHERE ctime >= TIMESTAMP '2026-09-01 00:00:00' "
     "AND ctime < TIMESTAMP '2026-10-01 00:00:00'", "fail"),
    # 월 범위 정확 창
    ({"month_span": {"from": 11, "to": 2}, "exact": True},
     "SELECT 1 FROM m WHERE s.stat_date BETWEEN '202511' AND '202602'", "pass"),
    ({"month_span": {"from": 11, "to": 2}, "exact": True},
     "SELECT 1 FROM m WHERE s.stat_date BETWEEN '202510' AND '202602'", "fail"),
    # 리터럴 없음 = 불합격(결과 행으로 가지 않는다) — §10.1 H-14 기간 조건 없음
    ({"relative": "last_n_months", "n": 6, "exact": True},
     "SELECT r.hostname FROM polestar.cmm_resource r", "fail"),
])
def test_exact_window(spec: dict[str, Any], sql: str, verdict: str) -> None:
    """정확 창은 같아야 통과하고, 키가 없으면 종전 「덮는다」다."""
    result = _judge({"period_covers": spec}, [sql])
    assert result.func == verdict, (result.failures, result.manual_notes)


def test_exact_failure_detail_and_unobserved_hold() -> None:
    """불합격 상세는 선언 그대로 기대값에, 창·관측 구간·DB 현재시각 함수는 실제값에 싣는다."""
    spec = {"relative": "last_month", "exact": True}
    (failure,) = _judge({"period_covers": spec}, [
        "SELECT 1 FROM m WHERE s.stat_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMM')",
    ]).failures
    assert failure.as_dict() == {"key": "period_covers", "expected": spec, "actual": {
        "window": "2026-08-01~2026-09-01", "anchor_at": ANCHOR, "sql_period": "날짜 리터럴 없음",
        "db_time_functions": ["CURRENT_DATE", "INTERVAL"]}}
    (wide,) = _judge({"period_covers": spec}, ["SELECT 1 WHERE d >= '202608'"]).failures
    assert wide.actual["sql_period"] == "2026-08-01~…"
    held = _judge({"period_covers": spec}, [])
    assert held.func == "manual" and held.manual_sources == ["unobservable"]
    assert "정확 창은 SQL 리터럴로만 판정한다" in held.manual_notes[0]


# --- 4 보류 -------------------------------------------------------------------------------------

@pytest.mark.parametrize("anchor, held", [
    ("2026-09-23T23:55:00+09:00", True),
    ("2026-09-24T00:05:00+09:00", True),
    ("2026-09-23T23:45:00+09:00", False),
    ("2026-09-24T00:15:00+09:00", False),
    ("2026-09-23T14:58:00Z", True),          # UTC 14:58 = KST 23:58
])
@pytest.mark.parametrize("kind", sorted(DAY_LEVEL_KINDS))
def test_day_level_kinds_hold_near_midnight(kind: str, anchor: str, held: bool) -> None:
    """일·시 단위 창은 자정 ±10분이면 보류한다."""
    spec: dict[str, Any] = {"relative": kind}
    if kind in RELATIVE_N_UNITS:
        spec["n"] = 1
    verdict = _judge({"period_covers": spec}, ["SELECT 1 WHERE d = '200001'"], anchor=anchor)
    if held:
        assert verdict.func == "manual" and "자정 ±10분" in verdict.manual_notes[0]
    elif kind == "today" and anchor.startswith("2026-09-24T00:15"):
        # 00시대의 「오늘」은 빈 창이다
        assert verdict.func == "manual" and "비었다" in verdict.manual_notes[0]
    else:
        assert verdict.func == "fail", (verdict.failures, verdict.manual_notes)


def test_month_kinds_not_held_at_midnight_and_month_boundary_kept() -> None:
    """월 단위 종류는 자정 보류가 없고, 월 경계 보류는 새 종류에도 그대로다."""
    assert not near_day_boundary(datetime(2026, 9, 23, 12, tzinfo=KST))
    late = _judge({"period_covers": {"relative": "last_month", "exact": True}},
                  ["SELECT 1 WHERE d = '202608'"], anchor="2026-09-23T23:55:00+09:00")
    assert late.func == "pass"
    boundary = _judge({"period_covers": {"relative": "yesterday", "exact": True}},
                      ["SELECT 1 WHERE d = '20260930'"], anchor="2026-10-01T10:00:00+09:00")
    assert boundary.func == "manual" and "월 경계 ±1일" in boundary.manual_notes[0]


# --- 5 로더 · 사본 ------------------------------------------------------------------------------

def _load(scenario_dir: Path, profiles_path: Path, expect: str) -> Any:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        "        expect: {status: completed}", f"        expect: {expect}"))
    return load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)


@pytest.mark.parametrize("expect", [
    "{period_covers: {relative: yesterday}}",
    "{period_covers: {relative: last_week, exact: true}}",
    "{period_covers: {relative: this_week}}",
    "{period_covers: {relative: today}}",
    "{period_covers: {relative: last_n_days, n: 30, exact: true}}",
    "{period_covers: {relative: last_n_days, n: 3660}}",
    "{period_covers: {relative: last_n_hours, n: 8784}}",
    "{period_covers: {relative: last_n_months, n: 3, exact: true}}",
    "{period_covers: {month_span: {from: 11, to: 2}, exact: true}}",
])
def test_loader_accepts_t9_forms(scenario_dir: Path, profiles_path: Path, expect: str) -> None:
    """새 종류와 exact 는 로드된다."""
    catalog = _load(scenario_dir, profiles_path, expect)
    assert "period_covers" in catalog.scenarios[0].turns[0].expect


@pytest.mark.parametrize("expect, needle", [
    ("{period_covers: {relative: yesterday, n: 1}}", "n 은 relative=last_n_days|last_n_hours|"),
    ("{period_covers: {relative: last_n_days}}", "relative=last_n_days 는 n(1~3660 정수)"),
    ("{period_covers: {relative: last_n_hours, n: 8785}}", "n(1~8784 정수)"),
    ("{period_covers: {from: '2026-07-01', to: '2026-08-01', exact: true}}",
     "exact 는 relative·month_span 과만"),
    ("{period_covers: {unbounded: true, exact: true}}", "exact 는 relative·month_span 과만"),
    ("{period_covers: {exact: true}}", "exact 는 relative·month_span 과만"),
    ("{period_covers: {relative: last_month, exact: false}}", "exact 는 true 만"),
    ("{period_covers: {relative: last_month, exact: 'yes'}}", "exact 는 true 만"),
])
def test_loader_rejects_bad_t9_forms(
    scenario_dir: Path, profiles_path: Path, expect: str, needle: str,
) -> None:
    """틀린 모양은 로드 시점에 거부한다(조용히 판정 0 으로 새지 않게)."""
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path, expect)
    assert any(needle in error for error in exc.value.errors), exc.value.errors


def test_harness_copies_match_resolver() -> None:
    """하네스 종류 사본이 해석기 종류와 같다(낡지 않게)."""
    assert RELATIVE_PERIODS == RELATIVE_WINDOW_KINDS - {"month_span"}
    assert DAY_LEVEL_KINDS <= RELATIVE_PERIODS
    assert set(RELATIVE_N_UNITS) == {k for k in RELATIVE_PERIODS if k.startswith("last_n_")}
    assert set(RELATIVE_N_UNITS.values()) <= set(N_MAX)


# --- 6 카탈로그 장부 ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    """T-9 단언 장부 1행 — 카탈로그 선언과 정답 SQL 을 만드는 방법."""

    period: dict[str, Any]
    grain: str | None              # 카탈로그 sql_must_match 에 있어야 하는 입도·테이블 패턴
    subject: str = "metric"        # metric(통계) · event(알람)
    form_months: int | None = None  # 양식 월 칼럼 수 — 해석 끝 월 기준으로 정렬한 월 범위가 정답
    fn_neg: bool = True            # 함수식 부정 단언(unbounded 는 자체가 함수식을 본다)


STAT_M = "(?i)cmm_metric_stat_m"
STAT_M_B = r"(?i)\bcmm_metric_stat_m\b"
ALARM = r"(?i)\bcmm_alarm"
LAST_MONTH_X = {"relative": "last_month", "exact": True}
JUN = {"from": "2026-06-01", "to": "2026-07-01"}
JAN_JUN = {"from": "2026-01-01", "to": "2026-07-01"}
H15 = ("(?is)\\bcmm_metric_stat_m\\b.*'server\\.Memory'|'server\\.Memory'.*"
       "\\bcmm_metric_stat_m\\b")

LEDGER: dict[tuple[str, int], Entry] = {
    ("A-01", 1): Entry(LAST_MONTH_X, STAT_M_B),                       # 기간 없음 = 지난달
    ("B-12", 1): Entry(JUN, STAT_M),
    ("C-01", 1): Entry(LAST_MONTH_X, STAT_M),
    ("C-02", 1): Entry({"relative": "last_n_months", "n": 3, "exact": True}, STAT_M),
    ("C-03", 1): Entry(JAN_JUN, STAT_M),
    ("C-04", 1): Entry(JAN_JUN, STAT_M),
    ("C-05", 1): Entry({"month_span": {"from": 11, "to": 2}, "exact": True}, STAT_M),
    ("C-06", 1): Entry({"relative": "this_month"}, None),           # 테이블은 G-3 수동
    ("C-07", 1): Entry({"unbounded": True}, STAT_M, fn_neg=False),
    ("C-09", 1): Entry({"from": "2026-05-01", "to": "2026-07-01"}, STAT_M),
    ("C-11", 1): Entry(JUN, STAT_M),
    ("C-13", 1): Entry(LAST_MONTH_X, STAT_M),
    ("D-03", 1): Entry({"from": "2026-07-01", "to": "2026-08-01"}, ALARM, "event"),
    ("D-04", 1): Entry({"relative": "last_n_months", "n": 3, "exact": True}, ALARM, "event"),
    ("D-05", 1): Entry({"relative": "this_month"}, ALARM, "event"),
    ("G-01", 2): Entry({"relative": "last_n_months", "n": 1, "exact": True}, STAT_M_B),
    ("G-01", 3): Entry({"relative": "last_n_months", "n": 1, "exact": True}, STAT_M_B),
    ("G-01", 5): Entry({"relative": "last_n_months", "n": 1}, r"(?i)\bcmm_alarm\w*\b", "event"),
    ("H-05", 1): Entry(JUN, STAT_M),
    ("H-10", 1): Entry(JAN_JUN, STAT_M, form_months=6),
    ("H-11", 1): Entry(JAN_JUN, STAT_M_B, form_months=6),
    ("H-12", 1): Entry({"relative": "last_n_months", "n": 6, "exact": True}, STAT_M_B,
                       form_months=6),
    ("H-13", 1): Entry({"month_span": {"from": 1, "to": 3}}, STAT_M_B, form_months=6),
    ("H-14", 1): Entry({"relative": "last_n_months", "n": 6, "exact": True}, STAT_M_B,
                       form_months=6),
    ("H-15", 1): Entry(JUN, H15),
    ("H-17", 1): Entry(JAN_JUN, STAT_M_B, form_months=6),
    ("I-07", 1): Entry(JAN_JUN, STAT_M_B, form_months=6),
    ("K-08", 1): Entry({"relative": "last_n_months", "n": 24, "exact": True}, None),
    ("K-09", 1): Entry({"relative": "last_n_days", "n": 30, "exact": True},
                       r"(?i)\bcmm_metric_stat_h\b"),
    ("SYN-E-01", 1): Entry(JUN, STAT_M_B),
    ("SYN-F-01", 1): Entry(JUN, STAT_M_B),
    ("SYN-F-02", 1): Entry({"from": "2026-07-01", "to": "2026-08-01"}, None, "event"),
    ("SYN-F-04", 1): Entry(JUN, STAT_M_B),
    ("SYN-H-01", 1): Entry({"unbounded": True}, None, "event", fn_neg=False),
    ("SYN-H-02", 1): Entry(JUN, STAT_M_B),
    ("SYN-H-04", 1): Entry({"from": "2026-07-01", "to": "2026-08-01"}, STAT_M_B),
    ("SYN-I-01", 1): Entry(JUN, STAT_M_B),
    ("SYN-I-02", 1): Entry({"from": "2026-05-01", "to": "2026-07-01"}, STAT_M_B),
    ("SYN-I-04", 1): Entry({"unbounded": True}, None, "event", fn_neg=False),
    ("SYN-I-05", 1): Entry(JUN, STAT_M_B),
    ("SYN-I-06", 1): Entry(JUN, STAT_M_B),
    ("SYN-I-07", 1): Entry(JUN, STAT_M_B),
}

#: 골드셋 밖인데 장부에 넣은 턴과 사유 — 원문에 기간이 없어 골드셋이 모으지 않았다.
EXTRA: dict[tuple[str, int], str] = {
    ("G-01", 3): ("§10.1 G-01 함수식 실측 턴 — 앞 턴 「최근 1개월」 승계 · "
                  "기간 없음 기본값과 같은 창"),
}

#: 골드셋 턴 중 단언을 붙이지 않은 턴과 사유(§14.10 ⑩ 비answer 턴 제외 취지).
SKIPPED: dict[tuple[str, int], str] = {
    ("A-04", 1): "현재·지금류(골드 out_of_scope) — 활성 알람 상태 조회",
    ("A-07", 1): "응답이 SQL 이 아니다 — 범용 대화(오늘 날씨)",
    ("C-08", 1): "관측 전용 probe — 기계 판정하지 않는다(manual_review)",
    ("C-10", 1): "G-10 모호(골드 ambiguous)",
    ("C-12", 1): "현재·지금류(골드 out_of_scope)",
    ("D-01", 1): "현재·지금류(골드 out_of_scope) — 현재 발생 중 알람",
    ("SYN-I-03", 1): "G-10 모호(골드 ambiguous)",
    ("SYN-I-03b", 1): "G-10 모호(골드 ambiguous)",
}


def _owned_ids() -> set[str]:
    ids: set[str] = set()
    for name in OWNED:
        data = yaml.safe_load((REPO_ROOT / "testdata" / "scenarios" / f"{name}.yaml").read_text(
            encoding="utf-8"))
        ids.update(s["id"] for s in data.get("scenarios") or [])
    return ids


@pytest.fixture(scope="module")
def catalog_turns() -> dict[tuple[str, int], tuple[Scenario, Turn]]:
    return {(s.id, i): (s, t) for s in load_catalog().scenarios
            for i, t in enumerate(s.turns, start=1)}


def test_every_owned_gold_time_turn_is_accounted() -> None:
    """122 소유 파일의 골드셋 시간 턴은 전부 장부에 있거나 제외 사유가 있다."""
    gold = yaml.safe_load((REPO_ROOT / "testdata" / "time_gold" / "catalog.yaml").read_text(
        encoding="utf-8"))["cases"]
    owned = _owned_ids()
    wanted = {(c["scenario"], int(c["turn"])) for c in gold if c["scenario"] in owned}
    covered = (set(LEDGER) - set(EXTRA)) | set(SKIPPED)
    assert wanted == covered, (
        f"장부 밖 {sorted(wanted - covered)} · 골드 밖 {sorted(covered - wanted)}")
    assert set(EXTRA) <= set(LEDGER) and not set(EXTRA) & wanted


def test_catalog_matches_ledger(
    catalog_turns: dict[tuple[str, int], tuple[Scenario, Turn]],
) -> None:
    """카탈로그 선언이 장부와 같다 — 기간 · 입도 · 함수식 부정(정본 문자열)."""
    for key, entry in LEDGER.items():
        _, turn = catalog_turns[key]
        assert turn.expect.get("period_covers") == entry.period, key
        if entry.grain is not None:
            assert entry.grain in (turn.expect.get("sql_must_match") or []), key
        assert (FN in (turn.expect.get("sql_must_not_match") or [])) == entry.fn_neg, key
    for key in SKIPPED:
        assert "period_covers" not in catalog_turns[key][1].expect, key


@pytest.mark.parametrize("key", [("C-04", 1), ("H-11", 1)])
@pytest.mark.parametrize("sql, hit", [
    ("s.stat_date >= '202601' AND s.stat_date < '202607'", False),   # 표준형(배타 끝) — 정답
    ("s.stat_date<'202607'", False),
    ("s.stat_date BETWEEN '202601' AND '202606'", False),
    ("s.stat_date BETWEEN '202602' AND '202607'", True),             # D-176 「2~7월 밀림」
    ("s.stat_date <= '202607'", True),
    ("s.stat_date = '202607'", True),
    ("s.stat_date IN ('202606', '202607')", True),
])
def test_july_guard_allows_exclusive_end(
    key: tuple[str, int], sql: str, hit: bool,
    catalog_turns: dict[tuple[str, int], tuple[Scenario, Turn]],
) -> None:
    """C-04·H-11 7월 부정 단언은 배타 끝 표준형을 허용하고 7월을 포함하는 표기만 잡는다."""
    (guard,) = [p for p in catalog_turns[key][1].expect["sql_must_not_match"] if p != FN]
    assert bool(re.search(guard, sql)) is hit


def test_function_negative_pattern_is_harness_list() -> None:
    """카탈로그의 DB 현재시각 함수 부정 패턴은 전부 하네스 `_DB_NOW_RE` 와 같은 문자열이다."""
    drifted = [(s.id, pattern) for s in load_catalog().scenarios for t in s.turns
               for pattern in t.expect.get("sql_must_not_match") or []
               if "current[_ ]" in pattern and pattern != FN]
    assert not drifted


# --- 7 정답 SQL 통과 · §10.1 오답 불합격 --------------------------------------------------------

def _resolution(entry: Entry, query: str) -> TimeResolution:
    """제품 해석기(원문 · 앵커) → 정답 해석. 양식 월 칼럼은 해석 끝 월 기준 N개월(끝 월 정렬)."""
    qt = resolve_query_time(query, ANCHOR_AT)
    res = qt.event if entry.subject == "event" else qt.metric
    assert res is not None, query
    if entry.form_months is not None:
        assert res.end is not None
        return TimeResolution(start=_add_months(res.end, -entry.form_months), end=res.end,
                              grain="month", completeness="complete", source="rule",
                              anchor_at=res.anchor_at)
    return res


def _correct_sqls(entry: Entry, query: str, *, db2: bool) -> list[str]:
    """정답 SQL 표기들 — 반개구간(`where()`) · 월 통계면 BETWEEN(포함 끝)도."""
    res = _resolution(entry, query)
    schema = "POLESTAR" if db2 else "polestar"
    if entry.subject == "event":
        bounds = alarm_ts_bounds(res)
        cond = "1 = 1"
        if bounds is not None:
            start, end = bounds
            cond = f"a.ctime < TIMESTAMP '{end}'"
            if start is not None:
                cond = f"a.ctime >= TIMESTAMP '{start}' AND {cond}"
        table = "CMM_ALARM" if db2 else "cmm_alarm"
        return [f"SELECT a.resource_id FROM {schema}.{table} a WHERE a.alarmseverity >= 2 "
                f"AND {cond}"]
    sb = stat_bounds(res)
    table = (sb.table if sb is not None else "cmm_metric_stat_m")
    table = table.upper() if db2 else table
    head = (f"SELECT r.hostname, s.avg_val FROM {schema}.cmm_resource r JOIN {schema}.{table} s "
            f"ON s.resource_id = r.id WHERE r.resource_type = 'server.Memory' AND ")
    if sb is None:
        return [head + "s.max_val > 90"]
    sqls = [head + sb.where()]
    if sb.grain == "month" and sb.lo is not None and sb.last is not None:
        sqls.append(head + f"s.stat_date BETWEEN '{sb.lo}' AND '{sb.last}'")
    return sqls


def _reduced(entry: Entry, turn: Turn) -> dict[str, Any]:
    """T-9 단언만 남긴 기대 — 기간 · 입도 · 그 턴의 부정 단언 전부(정답 SQL 이 기존 부정과 충돌하지
    않는지도 본다)."""
    expect: dict[str, Any] = {"period_covers": entry.period,
                              "sql_must_not_match": list(turn.expect.get("sql_must_not_match")
                                                         or [])}
    if entry.grain is not None:
        expect["sql_must_match"] = [entry.grain]
    return expect


@pytest.mark.parametrize("db2", [False, True], ids=["pg", "db2"])
@pytest.mark.parametrize("key", sorted(LEDGER), ids=lambda k: f"{k[0]}-t{k[1]}")
def test_correct_sql_passes(
    key: tuple[str, int], db2: bool,
    catalog_turns: dict[tuple[str, int], tuple[Scenario, Turn]],
) -> None:
    """정답 SQL(해석기 리터럴)은 붙인 단언을 통과한다 — 정답 불합격 0."""
    entry = LEDGER[key]
    _, turn = catalog_turns[key]
    for sql in _correct_sqls(entry, str(turn.send["query"]), db2=db2):
        verdict = _judge(_reduced(entry, turn), [sql])
        assert verdict.func == "pass", (sql, verdict.failures, verdict.manual_notes)


@pytest.mark.parametrize("key", sorted(k for k, e in LEDGER.items()
                                       if e.period.get("exact") is True),
                         ids=lambda k: f"{k[0]}-t{k[1]}")
def test_exact_window_equals_resolver(
    key: tuple[str, int], catalog_turns: dict[tuple[str, int], tuple[Scenario, Turn]],
) -> None:
    """정확 창 선언은 해석기 정책값과 같다(창 = `relative_window` · 정답 = 제품 해석 · §10.3.1)."""
    entry = LEDGER[key]
    _, turn = catalog_turns[key]
    window, reason = _period_window(entry.period, ANCHOR)
    assert window is not None, reason
    res = _resolution(entry, str(turn.send["query"]))
    assert res.start is not None and res.end is not None
    end = res.end.date() if res.end.hour == 0 and res.end.minute == 0 else (
        res.end.date() + timedelta(days=1))
    assert window == (res.start.date(), end)


@pytest.mark.parametrize("key", sorted(LEDGER), ids=lambda k: f"{k[0]}-t{k[1]}")
def test_function_expression_fails(
    key: tuple[str, int], catalog_turns: dict[tuple[str, int], tuple[Scenario, Turn]],
) -> None:
    """DB 현재시각 함수식(§10.1 A-01·C-06·E-05·R4-11 형태)은 불합격이다 — 리터럴만(§10.3 ④)."""
    entry = LEDGER[key]
    _, turn = catalog_turns[key]
    sql = ("SELECT 1 FROM polestar.cmm_metric_stat_m s JOIN polestar.cmm_alarm a ON 1 = 1 "
           "WHERE r.resource_type = 'server.Memory' "
           "AND s.stat_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMM')")
    verdict = _judge(_reduced(entry, turn), [sql])
    assert verdict.func == "fail", (verdict.failures, verdict.manual_notes)


@pytest.mark.parametrize("key", sorted(k for k, e in LEDGER.items()
                                       if not e.period.get("unbounded")),
                         ids=lambda k: f"{k[0]}-t{k[1]}")
def test_over_expansion_fails_only_on_exact(
    key: tuple[str, int], catalog_turns: dict[tuple[str, int], tuple[Scenario, Turn]],
) -> None:
    """정답 구간을 한 달 앞으로 넓히면 정확 창 턴만 불합격이다(「덮는다」 턴은 종전대로 통과)."""
    entry = LEDGER[key]
    _, turn = catalog_turns[key]
    res = _resolution(entry, str(turn.send["query"]))
    assert res.start is not None and res.end is not None
    wide = TimeResolution(start=_add_months(res.start, -1), end=res.end, grain=res.grain,
                          completeness=res.completeness, source=res.source,
                          anchor_at=res.anchor_at)
    sqls = _sqls_for(entry.subject, wide)
    verdict = _judge(_reduced(entry, turn), sqls)
    want = "fail" if entry.period.get("exact") is True else "pass"
    assert verdict.func == want, (sqls, verdict.failures, verdict.manual_notes)


def _sqls_for(subject: str, res: TimeResolution) -> list[str]:
    """해석 결과 하나로 반개구간 리터럴 SQL 을 만든다."""
    if subject == "event":
        bounds = alarm_ts_bounds(res)
        assert bounds is not None and bounds[0] is not None
        return [f"SELECT a.resource_id FROM polestar.cmm_alarm a WHERE a.ctime >= TIMESTAMP "
                f"'{bounds[0]}' AND a.ctime < TIMESTAMP '{bounds[1]}'"]
    sb = stat_bounds(res)
    assert sb is not None
    return [f"SELECT s.avg_val FROM polestar.cmm_resource r JOIN polestar.{sb.table} s "
            f"ON s.resource_id = r.id WHERE r.resource_type = 'server.Memory' AND {sb.where()}"]


@pytest.mark.parametrize("key, sql, keys", [
    # K-09 — 시간 단위 최근 30일 → 월 통계 '202608' 한 달(입도·기간 둘 다 ·
    #   run 20260923-140539 실측)
    (("K-09", 1), "SELECT 1 FROM polestar.cmm_resource r LEFT JOIN polestar.cmm_metric_stat_m s "
                  "ON r.id = s.resource_id AND s.stat_date = '202608'",
     {"period_covers", "sql_must_match"}),
    # H-14 — 지난 반년 → 월 칼럼 SQL 자체가 없다(기간 조건 없음 · 같은 run 실측)
    (("H-14", 1), "SELECT MAX(cc.stringvalue_short) FROM polestar.cmm_resource c "
                  "WHERE c.resource_type IN ('server.Memory', 'server.Server') AND c.dtime IS NULL",
     {"period_covers", "sql_must_match"}),
    # A-01 — 기간 없음 → `CURRENT_DATE - INTERVAL '1 month'`(함수식)
    (("A-01", 1), "SELECT 1 FROM polestar.cmm_metric_stat_m s "
                  "WHERE s.stat_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMM')",
     {"period_covers", "sql_must_not_match"}),
    # C-06 — 이번 달 → `TO_CHAR(date_trunc('month', CURRENT_DATE), …)`(함수식)
    (("C-06", 1), "SELECT 1 FROM polestar.cmm_metric_stat_m s WHERE s.stat_date BETWEEN "
                  "TO_CHAR(date_trunc('month', CURRENT_DATE), 'YYYYMM') AND "
                  "TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMM')",
     {"sql_must_not_match"}),
])
def test_owned_section_10_1_wrong_answers_fail(
    key: tuple[str, int], sql: str, keys: set[str],
    catalog_turns: dict[tuple[str, int], tuple[Scenario, Turn]],
) -> None:
    """§10.1 오답 형태(122 소유 턴)는 붙인 단언으로 불합격이다 — 어느 단언이 잡는지까지 고정."""
    entry = LEDGER[key]
    _, turn = catalog_turns[key]
    verdict = _judge(_reduced(entry, turn), [sql])
    assert verdict.func == "fail" and keys <= _keys(verdict), (
        verdict.failures, verdict.manual_notes)


def test_g01_follow_up_function_expressions_fail(
    catalog_turns: dict[tuple[str, int], tuple[Scenario, Turn]],
) -> None:
    """G-01 t3 — DB2 `VARCHAR_FORMAT(CURRENT DATE - 1 MONTH …)` · PG `TO_CHAR(CURRENT_DATE -
    INTERVAL …)`(run 20260922-162132 실측 형태)는 함수식 부정 단언과 정확 창(리터럴 없음)이
    잡는다(§10.1 G-01)."""
    entry = LEDGER[("G-01", 3)]
    _, turn = catalog_turns[("G-01", 3)]
    verdict = _judge(_reduced(entry, turn), [
        "SELECT 1 FROM POLESTAR.cmm_metric_stat_m s WHERE s.stat_date = "
        "VARCHAR_FORMAT(CURRENT DATE - 1 MONTH, 'YYYYMM')",
        "SELECT 1 FROM polestar.cmm_metric_stat_m s "
        "WHERE s.stat_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMM')",
    ])
    assert verdict.func == "fail"
    assert _keys(verdict) == {"sql_must_not_match", "period_covers"}


# --- 8 122 소유 밖(R·E군) — 단언 수단 준비 · 카탈로그 반영은 소유 계획 몫 ------------------------

#: (시나리오 · 질의 · 권고 단언 · 정답 SQL · §10.1 오답 SQL). 권고 단언은 보고서 표와 같다.
FOREIGN_CASES: list[tuple[str, str, dict[str, Any], str, str]] = [
    ("R4-02C", "지난 30일 CPU 평균 10건만 알려줘",
     {"period_covers": {"relative": "last_n_days", "n": 30, "exact": True},
      "sql_must_match": [r"(?i)\bcmm_metric_stat_d\b"], "sql_must_not_match": [FN]},
     "SELECT 1 FROM polestar.cmm_metric_stat_d s WHERE s.stat_date >= '20260824' "
     "AND s.stat_date < '20260923'",
     "SELECT 1 FROM polestar.cmm_metric_stat_m s WHERE s.stat_date = '202608'"),
    ("R4-03C", "최근 1시간 CPU 사용률 평균 10건만",
     {"period_covers": {"relative": "last_n_hours", "n": 1, "exact": True},
      "sql_must_match": [r"(?i)\bcmm_metric_stat_h\b"], "sql_must_not_match": [FN]},
     "SELECT 1 FROM polestar.cmm_metric_stat_h s WHERE s.stat_date >= '2026092309' "
     "AND s.stat_date < '2026092310'",
     "SELECT 1 FROM polestar.cmm_metric_stat_d s WHERE s.stat_date >= '20260901' "
     "AND s.stat_date < '20260923'"),
    ("R1-08", "어제 심각 알람이 3건 이상 난 서버의 메모리 용량과 벤더를 같이 보여줘",
     {"period_covers": {"relative": "yesterday", "exact": True},
      "sql_must_match": [ALARM], "sql_must_not_match": [FN]},
     "SELECT 1 FROM polestar.cmm_alarm a WHERE a.ctime >= TIMESTAMP '2026-09-22 00:00:00' "
     "AND a.ctime < TIMESTAMP '2026-09-23 00:00:00'",
     "SELECT 1 FROM polestar.cmm_alarm a WHERE a.ctime >= TIMESTAMP '2026-09-01 00:00:00' "
     "AND a.ctime < TIMESTAMP '2026-10-01 00:00:00'"),
    ("R1-10", "어제 CPU가 급등한 서버를 찾고 왜 그랬는지도 분석해줘",
     {"period_covers": {"relative": "yesterday", "exact": True}, "sql_must_not_match": [FN]},
     "SELECT 1 FROM polestar.cmm_metric_stat_d s WHERE s.stat_date = '20260922'",
     "SELECT 1 FROM polestar.cmm_alarm a WHERE a.ctime >= TIMESTAMP '2026-09-01 00:00:00' "
     "AND a.ctime < TIMESTAMP '2026-10-01 00:00:00'"),
    ("R3-06C", "지난주 알람 10건만 보여줘",
     {"period_covers": {"relative": "last_week", "exact": True},
      "sql_must_match": [ALARM], "sql_must_not_match": [FN]},
     "SELECT 1 FROM polestar.cmm_alarm a WHERE a.ctime >= TIMESTAMP '2026-09-14 00:00:00' "
     "AND a.ctime < TIMESTAMP '2026-09-21 00:00:00'",
     "SELECT 1 FROM polestar.cmm_alarm a WHERE a.ctime >= TIMESTAMP '2026-09-01 00:00:00' "
     "AND a.ctime < TIMESTAMP '2026-10-01 00:00:00'"),
    ("R2-05", "전체 서버의 전체 지표를 지난 2년치 시간 단위로 전부",
     {"period_covers": {"relative": "last_n_months", "n": 24},
      "sql_must_match": [r"(?i)\bcmm_metric_stat_h\b"], "sql_must_not_match": [FN]},
     "SELECT 1 FROM polestar.cmm_metric_stat_h s WHERE s.stat_date >= '2024090100' "
     "AND s.stat_date < '2026090100'",
     "SELECT 1 FROM polestar.cmm_metric_stat_m s WHERE s.stat_date BETWEEN '202409' "
     "AND '202608'"),
    ("E-05", "해당 서버 프로세스 이력 지난 7일 보여줘",
     {"period_covers": {"relative": "last_n_days", "n": 7, "exact": True},
      "sql_must_not_match": [FN]},
     "SELECT 1 FROM polestar.cmm_metric_stat_d s WHERE s.stat_date >= '20260916' "
     "AND s.stat_date < '20260923'",
     "SELECT 1 FROM polestar.cmm_metric_stat_d s WHERE s.stat_date BETWEEN "
     "TO_CHAR(CURRENT_DATE - INTERVAL '7 day', 'YYYYMMDD') AND "
     "TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMMDD')"),
    ("R4-11", "어제 CPU 사용률이 제일 높았던 값 알려줘",
     {"period_covers": {"relative": "yesterday", "exact": True},
      "sql_must_match": [r"(?i)\bcmm_metric_stat_d\b"], "sql_must_not_match": [FN]},
     "SELECT 1 FROM polestar.cmm_metric_stat_d s WHERE s.stat_date >= '20260922' "
     "AND s.stat_date < '20260923'",
     "SELECT 1 FROM polestar.cmm_metric_stat_d s "
     "WHERE s.stat_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMMDD')"),
    ("R4-02", "이번 달 CPU 평균 10건만 알려줘",
     {"period_covers": {"relative": "this_month"},
      "sql_must_match": [r"(?i)\bcmm_metric_stat_d\b"], "sql_must_not_match": [FN]},
     "SELECT 1 FROM polestar.cmm_metric_stat_d s WHERE s.stat_date >= '20260901' "
     "AND s.stat_date < '20260923'",
     "SELECT 1 FROM polestar.cmm_metric_stat_m s WHERE s.stat_date = "
     "TO_CHAR(date_trunc('month', CURRENT_DATE), 'YYYYMM')"),
]


@pytest.mark.parametrize("sid, query, expect, good, bad", FOREIGN_CASES,
                         ids=[case[0] for case in FOREIGN_CASES])
def test_foreign_wrong_answers_are_judgeable(
    sid: str, query: str, expect: dict[str, Any], good: str, bad: str,
) -> None:
    """122 소유 밖 §10.1 오답도 권고 단언으로 불합격 · 정답은 통과 — 정답은 제품 해석과 같다."""
    assert _judge(expect, [good]).func == "pass", sid
    assert _judge(expect, [bad]).func == "fail", sid
    qt = resolve_query_time(query, ANCHOR_AT)
    res = qt.event if "cmm_alarm" in good else qt.metric
    assert res is not None and res.start is not None and res.end is not None
    good_extent = sql_period_extent([good])
    assert good_extent[0] == res.start.date(), (sid, good_extent, res.label())
