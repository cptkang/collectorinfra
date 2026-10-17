"""plans/184 J2 — 괄호 없는 현재시각 낱말은 컬럼 참조가 아니라 식이다.

종전에는 `x.ts < CURRENT_TIMESTAMP`의 우변이 식별자 모양이라 `reason=column`(조인 키·CTE 컬럼)으로
분류돼, 시간 대조(`_judge_groups`)가 그 경계를 구간 계산에서 뺐다. `NOW()`·DB2 `CURRENT TIMESTAMP`
처럼 `reason=expression`으로 읽는다. 큰따옴표 식별자·접두 붙은 같은 이름은 여전히 컬럼이다.

대조 영향: 현재시각 낱말이 있으면 ①(현재시각 함수 금지) 오류는 종전과 같이 나고, 그 경계를 빼고
계산하던 「경계 불일치」 오류가 더는 덧붙지 않는다(묶음이 「읽기 불가」가 되고 ①이 그것을 대신한다).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.db_adapters.polestar.validators import check_time_conditions
from src.domain.query_time import resolve_query_time
from src.domain.time_spec import KST
from src.sql_time_conditions import (
    REASON_COLUMN,
    REASON_EXPRESSION,
    extract_column_conditions,
)

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)
_F = "SELECT a FROM t x WHERE "


def _reasons(where: str) -> list[tuple[bool, str]]:
    conds = extract_column_conditions(_F + where, "ts")
    assert conds is not None
    return [(c.literal, c.reason) for c in conds]


@pytest.mark.parametrize("word", [
    "CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME", "LOCALTIMESTAMP", "LOCALTIME",
    "SYSDATE", "SYSTIMESTAMP", "current_timestamp", "sysdate",
])
def test_bare_now_word_is_expression(word: str) -> None:
    assert _reasons(f"x.ts < {word}") == [(False, REASON_EXPRESSION)]
    assert _reasons(f"{word} > x.ts") == [(False, REASON_EXPRESSION)]  # 뒤집힌 비교


def test_now_call_and_db2_form_unchanged() -> None:
    assert _reasons("x.ts < NOW()") == [(False, REASON_EXPRESSION)]
    assert _reasons("x.ts < CURRENT TIMESTAMP") == [(False, REASON_EXPRESSION)]


@pytest.mark.parametrize("rhs", [
    '"CURRENT_TIMESTAMP"',  # 큰따옴표 식별자 — 같은 이름 컬럼
    "p.current_date",       # 접두 붙은 컬럼
    "p.sysdate",
    "p.m",                  # 일반 조인 키
    "now",                  # 괄호 없는 NOW는 현재시각 함수가 아니다
])
def test_identifiers_with_same_name_stay_column(rhs: str) -> None:
    assert _reasons(f"x.ts < {rhs}") == [(False, REASON_COLUMN)]


# ── 소비처 영향: polestar 시간 대조 ────────────────────────────────────────────


def _check(text: str, sql: str) -> list[str]:
    return check_time_conditions(sql, resolve_query_time(text, NOW))


def test_now_word_upper_bound_no_longer_yields_extra_mismatch() -> None:
    """하한은 맞고 상한이 `SYSDATE` — ①만 남는다(종전: ① + 상한을 뺀 구간의 경계 불일치)."""
    errors = _check(
        "지난달 알람",
        "SELECT a.id FROM polestar.cmm_alarm a "
        "WHERE a.ctime >= TIMESTAMP '2026-08-01 00:00:00' AND a.ctime < SYSDATE",
    )
    assert len(errors) == 1 and "현재시각 함수 금지(감지: SYSDATE)" in errors[0]


def test_now_word_with_wrong_literal_still_rejected() -> None:
    """리터럴이 틀려도 반려는 그대로(①) — 기대 리터럴이 ① 문구에 실린다."""
    errors = _check(
        "어제 CPU 평균",
        "SELECT s.avg_val FROM polestar.cmm_metric_stat_d s "
        "WHERE s.stat_date = '20260920' AND s.stat_date < CURRENT_DATE",
    )
    assert len(errors) == 1
    assert "현재시각 함수 금지(감지: CURRENT_DATE)" in errors[0]
    assert "s.stat_date >= '20260928' AND s.stat_date < '20260929'" in errors[0]


def test_quoted_identifier_bound_still_judged_as_column() -> None:
    """큰따옴표 식별자 상한은 종전처럼 컬럼 참조 — 구간 계산에서 빠져 경계 불일치로 반려된다."""
    errors = _check(
        "지난달 알람",
        "SELECT a.id FROM polestar.cmm_alarm a "
        "WHERE a.ctime >= TIMESTAMP '2026-08-01 00:00:00' AND a.ctime < \"CURRENT_TIMESTAMP\"",
    )
    assert len(errors) == 1 and "알람 기간 조건이 시스템이 해석한 기간" in errors[0]
