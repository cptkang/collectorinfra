"""plans/122 T-9 — 하네스 상대 기간 `relative_window` 의 일·주·시 종류(추가분).

고정하는 계약:
  1. 새 종류의 창은 §10.3.1 정책 표의 값이다(기준 2026-09-29 화 10:00 KST) — 최근 30일 ·
     어제 · 지난주(직전 달력 주) · 이번 주 · 오늘 · 최근 N시간.
  2. 창은 규칙 인식기(`time_expr.recognize`)가 그 한국어 표현에 만드는 명세를 해석기(`resolve`)로
     푼 값과 같다 — 하네스와 제품이 같은 정책을 쓴다(단일 출처 · 자체 날짜 산술 금지).
  3. 시 단위 창(오늘 · 최근 N시간)은 그 시각 창을 덮는 날짜 창(시작일 · 끝 올림)으로 내려간다.
     일·월 창은 끝이 자정이라 종전 종류와 같은 모양이다.
  4. 종전 종류(last_month · this_month · last_n_months · month_span)는 그대로다.
전부 무과금이다(LLM·DB·서버 0).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from src.domain.time_expr import recognize
from src.domain.time_spec import (
    KST,
    RELATIVE_WINDOW_KINDS,
    TimeSpecError,
    relative_window,
    resolve,
)

BASE = datetime(2026, 9, 29, 10, 0, tzinfo=KST)  # §10.3.1 기준 시각(화)
NEW_KINDS = {"last_n_days", "yesterday", "last_week", "this_week", "today", "last_n_hours"}


def kst(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST)


def test_new_kinds_are_registered_and_old_kinds_kept() -> None:
    """새 종류가 종류 집합에 더해졌고 종전 종류는 남아 있다."""
    assert NEW_KINDS <= RELATIVE_WINDOW_KINDS
    assert {"last_month", "this_month", "last_n_months", "month_span"} <= RELATIVE_WINDOW_KINDS


@pytest.mark.parametrize("kind, kwargs, anchor, want", [
    # §10.3.1 표 행 그대로(기준 2026-09-29 화 10:00)
    ("last_n_days", {"n": 30}, BASE, (date(2026, 8, 30), date(2026, 9, 29))),
    ("last_n_days", {"n": 7}, BASE, (date(2026, 9, 22), date(2026, 9, 29))),
    ("yesterday", {}, BASE, (date(2026, 9, 28), date(2026, 9, 29))),
    ("last_week", {}, BASE, (date(2026, 9, 21), date(2026, 9, 28))),
    ("this_week", {}, BASE, (date(2026, 9, 28), date(2026, 9, 29))),
    # 시 단위 — 그 시각 창을 덮는 날짜 창(끝 올림)
    ("today", {}, BASE, (date(2026, 9, 29), date(2026, 9, 30))),
    ("last_n_hours", {"n": 1}, BASE, (date(2026, 9, 29), date(2026, 9, 30))),
    ("last_n_hours", {"n": 3}, BASE, (date(2026, 9, 29), date(2026, 9, 30))),
    ("last_n_hours", {"n": 1}, kst(2026, 9, 29, 0, 30),   # [23:00, 00:00) → 전날
     (date(2026, 9, 28), date(2026, 9, 29))),
    ("last_n_hours", {"n": 12}, kst(2026, 9, 29, 5),      # [17:00 전날, 05:00) → 이틀
     (date(2026, 9, 28), date(2026, 9, 30))),
    # 빈 창 — 자정 직후의 「오늘」 · 월요일의 「이번 주」
    ("today", {}, kst(2026, 9, 29, 0, 30), (date(2026, 9, 29), date(2026, 9, 29))),
    ("this_week", {}, kst(2026, 9, 28, 10), (date(2026, 9, 28), date(2026, 9, 28))),
    # 월·연 넘김
    ("last_n_days", {"n": 30}, kst(2026, 9, 5, 10), (date(2026, 8, 6), date(2026, 9, 5))),
    ("yesterday", {}, kst(2026, 1, 1, 10), (date(2025, 12, 31), date(2026, 1, 1))),
    ("last_week", {}, kst(2026, 1, 1, 10), (date(2025, 12, 22), date(2025, 12, 29))),  # 목
    ("last_week", {}, kst(2026, 9, 28, 10), (date(2026, 9, 21), date(2026, 9, 28))),   # 월
])
def test_policy_table_values(
    kind: str, kwargs: dict[str, int], anchor: datetime, want: tuple[date, date],
) -> None:
    """새 종류의 창은 정책 표 값이다."""
    assert relative_window(kind, anchor, **kwargs) == want


@pytest.mark.parametrize("kind, kwargs, text", [
    ("last_n_days", {"n": 30}, "최근 30일"),
    ("last_n_days", {"n": 7}, "지난 7일"),
    ("yesterday", {}, "어제"),
    ("last_week", {}, "지난주"),
    ("this_week", {}, "이번 주"),
    ("today", {}, "오늘"),
    ("last_n_hours", {"n": 1}, "최근 1시간"),
    ("last_n_hours", {"n": 3}, "최근 3시간"),
])
@pytest.mark.parametrize("anchor", [BASE, kst(2026, 9, 23, 10), kst(2026, 1, 1, 0, 30),
                                    kst(2028, 2, 29, 23, 59)])
def test_same_spec_as_recognizer(
    kind: str, kwargs: dict[str, int], text: str, anchor: datetime,
) -> None:
    """창 = 규칙 인식기 명세의 해석 결과를 덮는 날짜 창(제품과 같은 정책)."""
    (spec,) = recognize(text)
    res = resolve(spec, anchor)
    assert res.start is not None and res.end is not None
    end = res.end.date() if res.end == res.end.replace(hour=0, minute=0) else (
        res.end.date() + timedelta(days=1))
    assert relative_window(kind, anchor, **kwargs) == (res.start.date(), end)


@pytest.mark.parametrize("kind", ["last_n_days", "last_n_hours"])
def test_n_required(kind: str) -> None:
    """n 이 필요한 종류는 n 없이 부르면 거부한다."""
    with pytest.raises(TimeSpecError) as exc:
        relative_window(kind, BASE)
    assert exc.value.code == "n_missing"


def test_n_range_checked_by_spec() -> None:
    """n 범위는 TimeSpec 검사(`N_MAX`)가 그대로 막는다."""
    with pytest.raises(TimeSpecError) as exc:
        relative_window("last_n_days", BASE, n=0)
    assert exc.value.code == "n_out_of_range"


def test_old_kinds_unchanged() -> None:
    """종전 종류의 창은 그대로다(§10.3.1 기준 시각)."""
    assert relative_window("last_month", BASE) == (date(2026, 8, 1), date(2026, 9, 1))
    assert relative_window("this_month", BASE) == (date(2026, 9, 1), date(2026, 9, 29))
    assert relative_window("last_n_months", BASE, n=6) == (date(2026, 3, 1), date(2026, 9, 1))
    assert relative_window("month_span", BASE, month_from=11, month_to=2) == (
        date(2025, 11, 1), date(2026, 3, 1))
