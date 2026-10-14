"""plans/156 W1 독립 검증 — 인식기 교정(자릿수 · 반쯤 · 영문 min/hr · 슬롯 unit 대조 · 수사)의 경계.

통과 테스트는 지켜져야 할 가드(오탐 방지)를 고정한다. `xfail(strict=True)`는 검증에서 찾은
결함이다 —
고치면 XPASS로 실패하므로 그때 마커를 지운다. 기준 시각 2026-10-13 10:37 KST · LLM 호출 0.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pytest

from src.domain.query_time import resolve_query_time
from src.domain.time_expr import CLARIFY_UNRESOLVED, slot_to_spec
from src.domain.time_spec import KST, NOTE_DEFAULT_PERIOD

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=KST)


def is_default(text: str) -> bool:
    qt = resolve_query_time(text, NOW)
    return (
        qt.clarify is None and qt.metric is not None
        and qt.metric.source == "default" and NOTE_DEFAULT_PERIOD in qt.metric.notes
    )


def event_window(text: str) -> tuple[datetime, datetime] | None:
    qt = resolve_query_time(text, NOW)
    return None if qt.event is None else (qt.event.start, qt.event.end)


def slot(relation: str, unit: str | None, span: str, n: int | None = None) -> dict[str, Any]:
    return {"relation": relation, "unit": unit, "span": span, "n": n}


# ---------------------------------------------------------------- 오탐 가드(통과해야 함)

@pytest.mark.parametrize("text", [
    # 한 글자 한자어 + 「일」 계열 낱말 — 접두가 있어도 기간·흔적이 아니다
    "최근 오 일차 점검 결과", "지난 구 일정 확인", "최근 이 일반 서버", "최근 오 일자 데이터",
    "지난 사 일정", "최근 이 일들 정리", "최근 구 일선 장애", "지난 일 일정 공유",
    "최근 사 분기 실적", "최근 이 주년 행사", "최근 이 시간대 알람",
    # 두 음절 이상 한자어의 낱말 경계
    "백분율 상위 서버", "최근 백분율", "CPU 백분위 상위", "십분 활용하세요", "삼십대 사용자",
    "최근 삼십대 사용자", "이십 주년 기념", "최근 이십 주년", "삼일절 알람", "최근 이십일세기",
    "최근 이십 일정", "최근 이십 일차", "최근 이십 주차", "최근 십오분기", "최근 삼십 분기 실적",
    "지난 십일월 알람", "최근 십이월 데이터",
    # 고유어 + 분 = 사람 수(G-4 ④) — 확장 수사도 같다
    "세 분 대기", "두 분 오셨다", "스무 분이 오셨다", "열다섯 분 참석",
    # 한자어 + 분 빈도·수량(D-328 ⑧ ⓓ와 같은 가드)
    "최근 삼십 분 이상 걸린 거래",
    # 만료 필터(「후」·「이내 만료」·「안에 만료」)
    "삼십 일 후 만료 서버", "삼십일 이내 만료", "삼십 일 안에 만료되는 인증서",
])
def test_not_period_stays_default(text: str) -> None:
    assert is_default(text), resolve_query_time(text, NOW)


@pytest.mark.parametrize("text", [
    "최근 30 minimum 값", "최근 30 mini 서버", "최근 10 mint", "최근 3 hrz",
    "최근 30 min 이상 걸린 거래", "최근 30 min마다 알람", "최근 5 min 간격",
])
def test_english_abbrev_word_boundary_and_freq_guard(text: str) -> None:
    assert is_default(text)


@pytest.mark.parametrize("text, window", [
    ("구십일 동안 알람", (datetime(2026, 7, 15, tzinfo=KST), at(13, 0))),
    ("최근 구십 일 알람", (datetime(2026, 7, 15, tzinfo=KST), at(13, 0))),
    ("최근 백이십 분 알람", (at(13, 8, 37), at(13, 10, 37))),
    ("최근 십오 분 알람", (at(13, 10, 22), at(13, 10, 37))),
    ("최근 이십사 시간 반 알람", (at(12, 10, 7), at(13, 10, 37))),
    ("최근 열두 시간 반쯤 알람", (at(12, 22, 7), at(13, 10, 37))),
    ("최근 2시간 반여 알람", (at(13, 8, 7), at(13, 10, 37))),
    ("최근 3 HR 알람", (at(13, 7, 37), at(13, 10, 37))),  # plans/156 G-3 (D-333)
    ("최근 3 hrs. 알람", (at(13, 7, 37), at(13, 10, 37))),  # plans/156 G-3 (D-333)
    ("최근 3 MIN 알람", (at(13, 10, 34), at(13, 10, 37))),
    ("12345번 서버 최근 3일 알람", (at(10, 0), at(13, 0))),
    ("요청번호 98765 최근 2일", (at(11, 0), at(13, 0))),
])
def test_interpreted_windows(text: str, window: tuple[datetime, datetime]) -> None:
    assert event_window(text) == window


@pytest.mark.parametrize(
    "text", ["최근 1시간 반영 내역", "최근 1시간 반복 알람", "최근 1시간 반기"]
)
def test_half_word_part_not_half_hour(text: str) -> None:
    assert event_window(text) == (at(13, 9, 37), at(13, 10, 37))  # plans/156 G-3 (D-333)


def test_long_digit_runs_not_period() -> None:
    assert is_default("20260101 알람")
    for text in ("최근 10081분", "최근 100000분"):
        assert resolve_query_time(text, NOW).clarify == CLARIFY_UNRESOLVED


# ---------------------------------------------------------------- 슬롯 unit 대조(⑨)

@pytest.mark.parametrize("text, s", [
    ("이번 주 알람", slot("this", "week", "이번 주")),
    ("지난 한 달 알람", slot("last", "month", "지난 한 달", 1)),
    ("최근 2주 알람", slot("last", "day", "최근 2주", 14)),
    ("최근 2주 알람", slot("last", "week", "최근 2주", 2)),
    ("작년 알람", slot("last", "year", "작년", 1)),
    ("최근 2년 알람", slot("last", "month", "최근 2년", 24)),
    ("최근 3시간 동안 5분 단위 알람", slot("last", "hour", "최근 3시간 동안 5분 단위", 3)),
    ("최근 5 hrs 알람", slot("last", "hour", "최근 5 hrs", 5)),
    ("3시간 전 알람", slot("ago", "hour", "3시간 전", 3)),
    ("최근 삼십 분 알람", slot("last", "minute", "최근 삼십 분", 30)),
    ("최근 반나절 알람", slot("last", "hour", "최근 반나절", 12)),
])
def test_unit_check_accepts_normal_slots(text: str, s: dict[str, Any]) -> None:
    assert slot_to_spec(s, text)[1] is None


@pytest.mark.parametrize("text, s", [
    ("최근 30 min 알람", slot("last", "hour", "최근 30 min", 30)),
    ("최근 삼십 분 알람", slot("last", "hour", "최근 삼십 분", 30)),
])
def test_unit_check_rejects_mismatch(text: str, s: dict[str, Any]) -> None:
    assert slot_to_spec(s, text)[1] == "unit_mismatch_span"


# ---------------------------------------------------------------- 검증에서 찾은 결함(xfail strict)

@pytest.mark.parametrize("text", ["최근 이십분의 일", "이십분의 일 수준"])
def test_sino_fraction_not_period(text: str) -> None:
    assert is_default(text)


@pytest.mark.parametrize(
    "text", ["일주일 동안 알람", "일주일치 데이터 보여줘", "CPU 일주일치 추이"]
)
def test_il_juil_not_traced_as_sino_numeral(text: str) -> None:
    assert resolve_query_time(text, NOW).clarify != CLARIFY_UNRESOLVED


def test_demonstrative_weekly_report_not_traced() -> None:
    assert is_default("이 주간 보고서 보여줘")


def test_unit_check_accepts_hour_half_as_minute() -> None:
    from src.domain.time_expr import _check_span_unit

    _check_span_unit("last", "minute", "최근 1시간 반")


def test_span_unit_regex_linear_on_long_digits() -> None:
    span = "1" * 4000
    t0 = time.perf_counter()
    slot_to_spec(slot("last", "hour", span), f"최근 {span} 알람")
    assert time.perf_counter() - t0 < 0.15


def test_minutes_word_freq_guard_symmetric_with_min() -> None:
    assert is_default("최근 30 minutes 이상 걸린 거래")


@pytest.mark.parametrize("text", ["최근 이십 년 알람", "최근 삼십 달 알람"])
def test_sino_year_month_word_not_silent_default(text: str) -> None:
    assert resolve_query_time(text, NOW).clarify == CLARIFY_UNRESOLVED


def test_bare_english_minute_traced_like_korean() -> None:
    assert resolve_query_time("30분 알람", NOW).clarify == CLARIFY_UNRESOLVED
    assert resolve_query_time("30 min 알람", NOW).clarify == CLARIFY_UNRESOLVED
