"""상대 기간 `period_covers` 통합 (plans/122 H-2 · 통합 담당).

고정하는 계약:
  1. 창은 해석기 `relative_window`(단일 출처)가 턴 앵커(`obs.anchor_at` · KST)로 정한다 — 지난달 ·
     이번 달 · 최근 N개월 · 연도 없는 월 범위(연 넘김) · 1월 → 전년 12월.
  2. 판정은 절대 기간과 같은 「덮는다」 규칙 — 실행 SQL(주석 제외) 날짜 리터럴 경계가 창을
     덮으면 통과.
  3. 앵커가 없거나 월 경계 ±1일(말일·1일·2일)이면 보류(`unobservable`).
  4. SQL 에 날짜 리터럴이 없으면 결과 행 기간 열로 월 단위 판정 · 둘 다 없으면 보류 ·
     잘린 결과는 보류.
  5. `unbounded: true` — 날짜 리터럴·DB 현재시각 함수가 없어야 통과 · SQL 미관측이면 보류.
  6. 로더가 새 형식의 틀린 모양을 거부한다(relative 값 · n 범위 · month_span 월 1~12 · 형식 혼용).
  7. 절대 기간 `{from, to}` 판정은 그대로다(새 검사가 아무것도 더하지 않는다).
전부 무과금이다(LLM·DB·서버 0).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario.assertions import (
    Observation,
    Verdict,
    evaluate_turn,
    near_month_boundary,
)
from scripts.scenario.catalog import CatalogError, Group, Scenario, Turn, load_catalog
from tests.test_scenario.conftest import GOOD_GROUP, write

GROUP = Group("T", "T", 60000)
MID = "2026-09-15T10:00:00+09:00"


def _judge(spec: dict[str, Any], *, sqls: list[str] | None = None, anchor: str | None = MID,
           result: dict[str, Any] | None = None, mock: bool = False) -> Verdict:
    expect = {"period_covers": spec}
    scenario = Scenario(id="T-01", group="T", plans=[122], title="t",
                        turns=[Turn({"query": "q"}, expect)])
    obs = Observation(http_status=200, status="completed", response="r",
                      executed_sqls=list(sqls or []), anchor_at=anchor, result=result)
    return evaluate_turn(scenario, 1, scenario.turns[0], obs, GROUP, mock=mock)


def _period_failure(verdict: Verdict) -> dict[str, Any]:
    (failure,) = [f for f in verdict.failures if f.key == "period_covers"]
    return failure.as_dict()


def _rows(column: str, values: list[str], **extra: Any) -> dict[str, Any]:
    return {"status": "ok", "columns": ["server_name", column],
            "rows": [{"server_name": f"s{i}", column: v} for i, v in enumerate(values)],
            "total_rows": len(values), "truncated": False, "reason": None, **extra}


# --- 1·2 창과 「덮는다」 규칙 -------------------------------------------------------------

@pytest.mark.parametrize("spec, anchor, sql, verdict", [
    # 지난달 = [2026-08-01, 2026-09-01)
    ({"relative": "last_month"}, MID, "SELECT 1 FROM m WHERE stat_date = '202608'", "pass"),
    ({"relative": "last_month"}, MID, "SELECT 1 FROM m WHERE stat_date = '202607'", "fail"),
    ({"relative": "last_month"}, MID,
     "SELECT 1 FROM m WHERE stat_date BETWEEN '202606' AND '202608'", "pass"),   # 넓게는 통과
    # 1월 → 전년 12월
    ({"relative": "last_month"}, "2026-01-15T10:00:00+09:00",
     "SELECT 1 FROM m WHERE stat_date = '202512'", "pass"),
    ({"relative": "last_month"}, "2026-01-15T10:00:00+09:00",
     "SELECT 1 FROM m WHERE stat_date = '202601'", "fail"),
    # 이번 달 = [2026-09-01, 2026-09-15)
    ({"relative": "this_month"}, MID,
     "SELECT 1 FROM a WHERE ctime >= TIMESTAMP '2026-09-01' AND ctime < TIMESTAMP '2026-10-01'",
     "pass"),
    ({"relative": "this_month"}, MID, "SELECT 1 FROM d WHERE stat_date >= '20260901' "
                                      "AND stat_date <= '20260914'", "pass"),
    ({"relative": "this_month"}, MID, "SELECT 1 FROM m WHERE stat_date = '202608'", "fail"),
    # 최근 3개월 = [2026-06-01, 2026-09-01)
    ({"relative": "last_n_months", "n": 3}, MID,
     "SELECT 1 FROM m WHERE stat_date BETWEEN '202606' AND '202608'", "pass"),
    ({"relative": "last_n_months", "n": 3}, MID,
     "SELECT 1 FROM m WHERE stat_date BETWEEN '202607' AND '202608'", "fail"),
    # 11월~2월(연 넘김) = [2025-11-01, 2026-03-01)
    ({"month_span": {"from": 11, "to": 2}}, MID,
     "SELECT 1 FROM m WHERE stat_date BETWEEN '202511' AND '202602'", "pass"),
    ({"month_span": {"from": 11, "to": 2}}, MID,
     "SELECT 1 FROM m WHERE stat_date BETWEEN '202611' AND '202702'", "fail"),
    # 1월에 「11월~2월」 = 미래가 아닌 가장 최근 → [2024-11-01, 2025-03-01)
    ({"month_span": {"from": 11, "to": 2}}, "2026-01-15T10:00:00+09:00",
     "SELECT 1 FROM m WHERE stat_date BETWEEN '202411' AND '202502'", "pass"),
    ({"month_span": {"from": 11, "to": 2}}, "2026-01-15T10:00:00+09:00",
     "SELECT 1 FROM m WHERE stat_date BETWEEN '202511' AND '202602'", "fail"),
])
def test_window_from_resolver_and_sql_cover(
    spec: dict[str, Any], anchor: str, sql: str, verdict: str,
) -> None:
    """창을 해석기로 정하고 SQL 리터럴이 덮는지 본다."""
    result = _judge(spec, sqls=[sql], anchor=anchor)
    assert result.func == verdict, (result.failures, result.manual_notes)


def test_failure_detail_shape() -> None:
    """불합격 상세는 기대값에 선언 그대로 실제값에 창과 관측 기간을 싣는다."""
    spec = {"relative": "last_month"}
    failure = _period_failure(_judge(spec, sqls=["SELECT 1 WHERE d = '202607'"]))
    assert failure["expected"] == spec, "재판정기가 기대값으로 카탈로그 변경을 가린다"
    assert failure["actual"] == {"window": "2026-08-01~2026-09-01", "anchor_at": MID,
                                 "sql_period": "2026-07-01~2026-08-01"}


def test_sql_comment_dates_ignored() -> None:
    """SQL 주석의 날짜는 경계로 보지 않는다."""
    sql = "-- 2026-08 기준 조회\nSELECT 1 FROM m WHERE stat_date = '202607'"
    assert _judge({"relative": "last_month"}, sqls=[sql]).func == "fail"


# --- 3 앵커 보류 --------------------------------------------------------------------------

@pytest.mark.parametrize("anchor, held", [
    ("2026-09-30T23:00:00+09:00", True),    # 말일
    ("2026-10-01T00:00:05+09:00", True),    # 1일
    ("2026-10-02T09:00:00+09:00", True),    # 2일
    ("2026-10-03T09:00:00+09:00", False),
    ("2026-09-29T09:00:00+09:00", False),
    ("2028-02-29T09:00:00+09:00", True),    # 윤년 말일
    ("2028-02-28T09:00:00+09:00", False),
    ("2026-09-30T16:00:00Z", True),         # UTC 9/30 16시 = KST 10/1 01시
    ("2026-09-29T20:00:00Z", True),         # UTC 9/29 20시 = KST 9/30 05시(말일)
])
def test_month_boundary_is_held(anchor: str, held: bool) -> None:
    """월 경계 앞뒤 하루는 보류한다."""
    verdict = _judge({"relative": "last_month"},
                     sqls=["SELECT 1 FROM m WHERE stat_date = '200001'"], anchor=anchor)
    if held:
        assert verdict.func == "manual" and verdict.manual_sources == ["unobservable"]
        assert "월 경계 ±1일" in verdict.manual_notes[0]
    else:
        assert verdict.func == "fail", "경계가 아니면 판정한다(여기선 틀린 기간이라 불합격)"


def test_near_month_boundary_rule() -> None:
    """near month boundary 규칙은 말일 1일 2일이다."""
    assert [d for d in range(1, 31) if near_month_boundary(date(2026, 9, d))] == [1, 2, 30]
    assert near_month_boundary(date(2026, 12, 31)) and near_month_boundary(date(2027, 1, 2))
    assert not near_month_boundary(date(2026, 2, 27)) and near_month_boundary(date(2026, 2, 28))


@pytest.mark.parametrize("anchor", [None, "", "지난달"])
def test_missing_or_bad_anchor_is_held(anchor: str | None) -> None:
    """앵커가 없거나 읽지 못하면 보류한다."""
    verdict = _judge({"relative": "this_month"}, sqls=["SELECT 1 WHERE d = '202609'"],
                     anchor=anchor)
    assert verdict.func == "manual" and verdict.manual_sources == ["unobservable"]
    assert "앵커" in verdict.manual_notes[0]


# --- 4 리터럴 없음 → 결과 행 기간 열 ----------------------------------------------------------

FUNC_SQL = ("SELECT svr.name, TO_DATE(s.stat_date || '01', 'YYYYMMDD') AS stat_month FROM m s "
            "WHERE s.stat_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMM')")


@pytest.mark.parametrize("column, values, verdict", [
    ("stat_month", ["2026-08-01", "2026-08-01"], "pass"),
    ("stat_month", ["2026-07-01"], "fail"),
    ("STAT_DATE", ["202608", "202608"], "pass"),           # 이름은 대소문자 무시
    ("yyyymm", ["202608"], "pass"),
    ("alarm_time", ["2026-08-03 10:00:00", "2026-08-29T23:00:00+09:00"], "pass"),  # 월 단위
    ("alarm_time", ["2026-09-03 10:00:00"], "fail"),
])
def test_result_period_column_fallback(
    column: str, values: list[str], verdict: str,
) -> None:
    """리터럴이 없으면 결과 행 기간 열로 월 단위 판정한다."""
    result = _judge({"relative": "last_month"}, sqls=[FUNC_SQL], result=_rows(column, values))
    assert result.func == verdict, (result.failures, result.manual_notes)
    if verdict == "fail":
        actual = _period_failure(result)["actual"]
        assert actual["column"] == column and "result_period" in actual


def test_result_fallback_multi_month_window() -> None:
    """여러 달 창은 결과 행의 첫 달과 끝 달이 덮어야 한다."""
    spec = {"relative": "last_n_months", "n": 3}
    full = _rows("stat_month", ["2026-06-01", "2026-07-01", "2026-08-01"])
    assert _judge(spec, sqls=[FUNC_SQL], result=full).func == "pass"
    narrow = _rows("stat_month", ["2026-08-01"])
    assert _judge(spec, sqls=[FUNC_SQL], result=narrow).func == "fail"


def test_result_fallback_without_sql() -> None:
    """SQL 을 못 봤어도 결과 행이 있으면 판정한다."""
    assert _judge({"relative": "last_month"}, sqls=[],
                  result=_rows("stat_date", ["202608"])).func == "pass"


@pytest.mark.parametrize("result, needle", [
    (None, "결과 행을 수집하지 않았다"),
    ({"status": "unavailable", "reason": "축출"}, "결과 행을 받지 못했다(축출)"),
    ({"status": "empty", "columns": [], "rows": [], "total_rows": 0}, "0건"),
    (_rows("도입일자", ["2026-08-01"]), "기간 열"),             # 부분·유사 이름은 기간 열이 아니다
    (_rows("cpu_avg_month", ["202608"]), "기간 열"),
    (_rows("month", ["8월"]), "날짜로 읽지 못했다"),
])
def test_no_literal_and_no_result_period_is_held(
    result: dict[str, Any] | None, needle: str,
) -> None:
    """리터럴도 결과 행 기간도 없으면 보류한다."""
    verdict = _judge({"relative": "last_month"}, sqls=[FUNC_SQL], result=result)
    assert verdict.func == "manual" and verdict.manual_sources == ["unobservable"]
    assert "실행 SQL 에 날짜 리터럴이 없고" in verdict.manual_notes[0]
    assert needle in verdict.manual_notes[0]


def test_truncated_result_not_covering_is_held() -> None:
    """잘린 결과가 창을 덮지 못하면 보류다."""
    cut = _rows("stat_month", ["2026-07-01"], truncated=True, total_rows=9000)
    verdict = _judge({"relative": "last_month"}, sqls=[FUNC_SQL], result=cut)
    assert verdict.func == "manual" and "잘려" in verdict.manual_notes[0]
    covered = _rows("stat_month", ["2026-08-01"], truncated=True, total_rows=9000)
    assert _judge({"relative": "last_month"}, sqls=[FUNC_SQL], result=covered).func == "pass"


# --- 5 날짜 한정 없음 ---------------------------------------------------------------------

@pytest.mark.parametrize("sql, verdict", [
    ("SELECT name FROM m WHERE max_val > 90", "pass"),
    ("-- 2026-08 이후 전 기간\nSELECT name FROM m WHERE max_val > 90", "pass"),   # 주석 제외
    ("SELECT name FROM m WHERE max_val > 90 AND stat_date >= '202603'", "fail"),
    ("SELECT name FROM m WHERE stat_date >= TO_CHAR(CURRENT_DATE - INTERVAL '6 months', 'YYYYMM')",
     "fail"),
    ("SELECT name FROM POLESTAR.m WHERE ctime >= CURRENT TIMESTAMP - 30 DAYS", "fail"),  # DB2
    ("SELECT name FROM m WHERE ctime > NOW() - 1", "fail"),
])
def test_unbounded_requires_no_date_restriction(sql: str, verdict: str) -> None:
    """unbounded 는 날짜 한정이 없어야 통과한다."""
    result = _judge({"unbounded": True}, sqls=["SELECT 1", sql], anchor=None)
    assert result.func == verdict, (result.failures, result.manual_notes)


def test_unbounded_failure_detail_and_unobserved_hold() -> None:
    """unbounded 불합격 상세와 SQL 미관측 보류."""
    failure = _period_failure(_judge({"unbounded": True}, sqls=[
        "SELECT 1 FROM m WHERE stat_date >= TO_CHAR(CURRENT_DATE - INTERVAL '6 months', 'YYYYMM') "
        "AND stat_date <> '202601'"]))
    assert failure["expected"] == {"unbounded": True}
    assert failure["actual"] == {"sql_period": "2026-01-01~2026-02-01",
                                 "db_time_functions": ["CURRENT_DATE", "INTERVAL"]}
    held = _judge({"unbounded": True}, sqls=[])
    assert held.func == "manual" and held.manual_sources == ["unobservable"]
    assert "실행 SQL 을 관측하지 못했다" in held.manual_notes[0]


def test_unbounded_needs_no_anchor() -> None:
    """unbounded 는 앵커가 없어도 판정한다."""
    assert _judge({"unbounded": True}, sqls=["SELECT 1 FROM m"], anchor=None).func == "pass"


# --- 7 절대 기간은 그대로 ------------------------------------------------------------------

def test_absolute_period_unchanged() -> None:
    """절대 기간 판정은 종전과 같다."""
    spec = {"from": "2026-07-01", "to": "2026-08-01"}
    assert _judge(spec, sqls=["SELECT 1 WHERE d = '202607'"], anchor=None).func == "pass"
    failure = _period_failure(_judge(spec, sqls=["SELECT 1 WHERE d = '202606'"], anchor=None))
    assert failure == {"key": "period_covers", "expected": spec,
                       "actual": {"sql_period": "2026-06-01~2026-07-01"}}
    no_literal = _period_failure(_judge(spec, sqls=["SELECT 1"], anchor=None))
    assert no_literal["actual"] == "SQL 에 날짜 리터럴이 없다", (
        "절대 기간은 결과 행으로 가지 않는다")


def test_mock_canned_holds_relative_period() -> None:
    """모의 실행은 canned 응답이면 상대 기간도 보류한다."""
    verdict = _judge({"relative": "last_month"}, sqls=["SELECT 1 WHERE d = '202607'"], mock=True)
    assert verdict.func == "manual" and "period_covers" in verdict.manual_notes[0]


# --- 6 로더 --------------------------------------------------------------------------------

def _load_expect(scenario_dir: Path, profiles_path: Path, expect: str) -> Any:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        "        expect: {status: completed}", f"        expect: {expect}"))
    return load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)


@pytest.mark.parametrize("expect", [
    "{period_covers: {relative: last_month}}",
    "{period_covers: {relative: this_month}}",
    "{period_covers: {relative: last_n_months, n: 3}}",
    "{period_covers: {relative: last_n_months, n: 120}}",
    "{period_covers: {month_span: {from: 11, to: 2}}}",
    "{period_covers: {month_span: {from: 3, to: 3}}}",
    "{period_covers: {unbounded: true}}",
    "{period_covers: {from: '2026-07-01', to: '2026-08-01'}}",
])
def test_loader_accepts_new_forms(scenario_dir: Path, profiles_path: Path, expect: str) -> None:
    """새 형식은 로드된다."""
    catalog = _load_expect(scenario_dir, profiles_path, expect)
    assert "period_covers" in catalog.scenarios[0].turns[0].expect


@pytest.mark.parametrize("expect, needle", [
    # plans/122 T-9 — last_week 는 이제 유효한 종류다(일·주·시 종류 추가). 정의 밖 종류로 바꿨다.
    ("{period_covers: {relative: next_week}}", "relative 는"),
    ("{period_covers: {relative: last_month, n: 2}}", "n 은 relative="),
    ("{period_covers: {relative: last_n_months}}", "n(1~120 정수)이 필요하다"),
    ("{period_covers: {relative: last_n_months, n: 0}}", "n(1~120 정수)"),
    ("{period_covers: {relative: last_n_months, n: 121}}", "n(1~120 정수)"),
    ("{period_covers: {relative: last_n_months, n: true}}", "n(1~120 정수)"),
    ("{period_covers: {relative: last_n_months, n: '3'}}", "n(1~120 정수)"),
    ("{period_covers: {n: 3}}", "relative 는"),
    ("{period_covers: {month_span: {from: 13, to: 2}}}", "month_span 은"),
    ("{period_covers: {month_span: {from: 0, to: 2}}}", "month_span 은"),
    ("{period_covers: {month_span: {from: 11}}}", "month_span 은"),
    ("{period_covers: {month_span: {from: 11, to: 2, year: 2025}}}", "month_span 은"),
    ("{period_covers: {month_span: [11, 2]}}", "month_span 은"),
    ("{period_covers: {unbounded: false}}", "unbounded 는 true 만"),
    ("{period_covers: {relative: last_month, from: '2026-07-01', to: '2026-08-01'}}",
     "섞어 쓰지 않는다"),
    ("{period_covers: {unbounded: true, month_span: {from: 1, to: 2}}}", "섞어 쓰지 않는다"),
    ("{period_covers: {relativ: last_month}}", "정의 밖 키 ['relativ']"),
    ("{period_covers: {}}", "from·to 는 둘 다"),     # 빈 선언은 종전 문구 그대로
])
def test_loader_rejects_bad_new_forms(
    scenario_dir: Path, profiles_path: Path, expect: str, needle: str,
) -> None:
    """새 형식의 틀린 모양은 로드 시점에 거부한다."""
    with pytest.raises(CatalogError) as exc:
        _load_expect(scenario_dir, profiles_path, expect)
    assert any(needle in error for error in exc.value.errors), exc.value.errors
