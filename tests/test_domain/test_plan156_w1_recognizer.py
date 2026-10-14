"""plans/156 W1 — 시간 표현 인식기 결함 교정(자릿수 · 「반쯤」 · 영문 약어 · 슬롯 단위 대조 · 수사).

기준 시각 2026-10-13 10:37 KST(계획서 §5 사례 표). LLM 호출 0 — 슬롯은 손으로 만든 dict다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from src.domain.query_time import SLOT_REJECTED, SLOT_UNUSED, resolve_query_time
from src.domain.time_expr import CLARIFY_UNRESOLVED, slot_to_spec
from src.domain.time_spec import KST, NOTE_DEFAULT_PERIOD

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=KST)


def windows(text: str, slot: Any = None) -> tuple[Any, Any, Any]:
    """(clarify, 통계 구간, 사건 구간) — 구간은 (start, end) 또는 None."""
    qt = resolve_query_time(text, NOW, slot=slot)
    metric = None if qt.metric is None else (qt.metric.start, qt.metric.end)
    event = None if qt.event is None else (qt.event.start, qt.event.end)
    return qt.clarify, metric, event


def is_default(text: str) -> bool:
    """기간 미지정 기본값(되묻기 아님) — 「기간 아님」 판정."""
    qt = resolve_query_time(text, NOW)
    return (
        qt.clarify is None and qt.metric is not None
        and qt.metric.source == "default" and NOTE_DEFAULT_PERIOD in qt.metric.notes
    )


# ──────────────────────────────────────────────
# ② 수사 — G-4 (a)
# ──────────────────────────────────────────────


class TestNumerals:
    @pytest.mark.parametrize("text", ["최근 삼십 분 알람", "삼십 분 동안 응답시간"])
    def test_sino_thirty_minutes(self, text: str) -> None:
        """「삼십 분」 — 사건 [10:07, 10:37) · 통계 [09:00, 10:00)+고지(§5)."""
        clarify, metric, event = windows(text)
        assert clarify is None
        assert event == (at(13, 10, 7), at(13, 10, 37))
        assert metric == (at(13, 9), at(13, 10))
        notes = resolve_query_time(text, NOW).metric.notes  # type: ignore[union-attr]
        assert "minute_widened_to_hour" in notes

    def test_sino_twenty_days(self) -> None:
        """「지난 이십 일」 = 완결 20일."""
        assert windows("지난 이십 일 알람")[1] == (datetime(2026, 9, 23, tzinfo=KST), at(13, 0))

    def test_native_fifteen_hours(self) -> None:
        """「최근 열다섯 시간」 = 15시간 롤링(고유어 열세~열아홉 확장)."""
        _, metric, event = windows("최근 열다섯 시간 알람")
        assert metric == (at(12, 19), at(13, 10))
        assert event == (at(12, 19, 37), at(13, 10, 37))  # plans/156 G-3 (D-333)

    @pytest.mark.parametrize(("text", "hours"), [
        ("최근 이십 시간 알람", 20), ("최근 이십사 시간 알람", 24), ("최근 스무 시간 알람", 20),
        ("최근 서른 시간 알람", 30), ("최근 마흔 시간 알람", 40), ("최근 열세 시간 알람", 13),
    ])
    def test_hours_numerals(self, text: str, hours: int) -> None:
        _, metric, _ = windows(text)
        assert metric is not None and metric[1] == at(13, 10)
        assert (metric[1] - metric[0]).total_seconds() == hours * 3600

    def test_sino_composite_values(self) -> None:
        """자릿수 문법 — 백이십 = 120 · 이십일 = 21."""
        assert windows("최근 백이십 분 알람")[2] == (at(13, 8, 37), at(13, 10, 37))
        assert windows("최근 이십일 일 알람")[1] == (datetime(2026, 9, 22, tzinfo=KST), at(13, 0))

    @pytest.mark.parametrize("text", ["세 분 대기", "두 분 담당자", "세 분이 확인한 알람"])
    def test_native_plus_minute_is_people(self, text: str) -> None:
        """고유어 + 분은 사람 수 — 기간도 흔적도 아니다(종전)."""
        assert is_default(text)

    @pytest.mark.parametrize("text", ["최근 삼 일 알람", "최근 오 분 알람", "삼 일 동안 알람"])
    def test_single_syllable_sino_is_trace(self, text: str) -> None:
        """한 글자 한자어(일~구)는 기간으로 읽지 않고 흔적 → 되묻기."""
        assert windows(text)[0] == CLARIFY_UNRESOLVED

    @pytest.mark.parametrize("text", [
        "이 일 처리 현황", "구 일정 목록", "오 분 대기", "이 분 접속 이력",
        "자원을 십분 활용한 서버", "CPU 백분율 상위", "최근 백분율 추이", "이십 주년 기념 서버",
        "십일월 알람",
    ])
    def test_single_syllable_and_word_parts_no_false_clarify(self, text: str) -> None:
        """비기간 문장 반례 — 한 글자 한자어·낱말 일부(백분율·주년·십분)는 거짓 되묻기가 없다."""
        assert is_default(text)

    @pytest.mark.parametrize("text", [
        "최근 삼십 분 간격 알람", "삼십 분마다 알람", "최근 삼십 분 이상 걸린 거래",
    ])
    def test_sino_minute_frequency_guard(self, text: str) -> None:
        """새 수사 규칙에도 분 빈도·수량 가드가 그대로 걸린다(「5분 간격」과 같다 — D-328 ⑧)."""
        assert is_default(text)

    def test_bare_multi_syllable_sino_is_trace(self) -> None:
        """접두·접미 없는 맨 「삼십 분」은 맨 「30분」처럼 되묻는다."""
        assert windows("삼십 분 알람")[0] == CLARIFY_UNRESOLVED

    @pytest.mark.parametrize("text", ["지난 이십 년 매출", "최근 삼십 달 알람"])
    def test_sino_year_and_dal_asks_back(self, text: str) -> None:
        """한자어 + 년·달은 해석 범위 밖 — 침묵 기본값 대신 흔적으로 되묻는다(검증 V-7)."""
        assert windows(text)[0] == CLARIFY_UNRESOLVED


# ──────────────────────────────────────────────
# ⑥ 자릿수 · ⑦ 반쯤 · ⑧ 영문 약어
# ──────────────────────────────────────────────


class TestDigitsHalfEnglish:
    @pytest.mark.parametrize(("text", "start"), [
        ("최근 10000분 알람", at(6, 11, 57)), ("최근 10080분 알람", at(6, 10, 37)),
    ])
    def test_five_digit_minutes_within_cap(self, text: str, start: datetime) -> None:
        clarify, metric, event = windows(text)
        assert clarify is None and metric is not None
        assert event == (start, at(13, 10, 37))

    @pytest.mark.parametrize("text", ["최근 10081분 알람", "최근 123456분", "최근 20260101분 알람"])
    def test_over_cap_or_long_number_asks_back(self, text: str) -> None:
        """`N_MAX` 초과 · 6자리 이상 연속 숫자는 해석하지 않고 되묻는다."""
        assert windows(text)[0] == CLARIFY_UNRESOLVED

    def test_long_number_without_unit_untouched(self) -> None:
        assert is_default("20260101 알람")

    @pytest.mark.parametrize("text", [
        "최근 1시간 반쯤 알람", "최근 1시간 반 정도 알람", "최근 1시간 반가량",
        "최근 1시간 반여 알람",
    ])
    def test_half_with_approx_suffix(self, text: str) -> None:
        """「1시간 반쯤」 — 사건 [09:07, 10:37) · 통계 [08:00, 10:00)+고지."""
        _, metric, event = windows(text)
        assert event == (at(13, 9, 7), at(13, 10, 37))
        assert metric == (at(13, 8), at(13, 10))

    @pytest.mark.parametrize("text", ["최근 1시간 반영된 알람", "최근 1시간 반기 알람"])
    def test_half_word_parts_still_blocked(self, text: str) -> None:
        """「반영·반기」는 「반」이 아니다 — 1시간만 남는다(종전)."""
        assert windows(text)[1] == (at(13, 9), at(13, 10))

    def test_half_repeat_still_blocked(self) -> None:
        assert windows("1시간 반복 알람")[0] == CLARIFY_UNRESOLVED

    @pytest.mark.parametrize("text", ["최근 30 min 알람", "최근 30min 알람", "최근 30 mins 알람"])
    def test_english_min(self, text: str) -> None:
        clarify, metric, event = windows(text)
        assert clarify is None
        assert event == (at(13, 10, 7), at(13, 10, 37))
        assert metric == (at(13, 9), at(13, 10))

    @pytest.mark.parametrize(("text", "start"), [
        ("최근 2 hrs 알람", at(13, 8)), ("최근 1 hr 알람", at(13, 9)),
    ])
    def test_english_hr(self, text: str, start: datetime) -> None:
        assert windows(text)[1] == (start, at(13, 10))

    @pytest.mark.parametrize("text", [
        "minimum 30 알람", "최근 30 minimum", "최근 30 minor 알람", "최근 30 min 이상 걸린 거래",
    ])
    def test_english_min_boundary_and_frequency(self, text: str) -> None:
        """영문자 경계(minimum·minor) · 분 빈도·수량 가드."""
        assert is_default(text)


# ──────────────────────────────────────────────
# ⑨ 슬롯 unit ↔ 스팬 단위
# ──────────────────────────────────────────────


def slot(n: int | None, unit: str, span: str, relation: str = "last") -> dict[str, Any]:
    return {"relation": relation, "n": n, "unit": unit, "span": span}


class TestSlotUnitCheck:
    def test_hour_slot_on_min_span_rejected(self) -> None:
        assert slot_to_spec(slot(30, "hour", "30 min"), "최근 30 min 알람") == (
            None, "unit_mismatch_span")

    def test_rule_wins_when_rule_catches(self) -> None:
        """규칙이 잡으면 슬롯은 쓰지 않는다 — 30시간 오조회 없음."""
        qt = resolve_query_time("최근 30 min 알람", NOW, slot=slot(30, "hour", "30 min"))
        assert qt.slot_status == SLOT_UNUSED
        assert qt.event is not None and (qt.event.start, qt.event.end) == (
            at(13, 10, 7), at(13, 10, 37))

    def test_rejected_slot_falls_back_to_trace(self) -> None:
        qt = resolve_query_time("삼십 분 대기 알람", NOW, slot=slot(30, "hour", "삼십 분"))
        assert qt.slot_status == SLOT_REJECTED and qt.slot_reason == "unit_mismatch_span"
        assert qt.clarify == CLARIFY_UNRESOLVED

    def test_rejected_slot_on_bare_english_asks_back(self) -> None:
        """맨 「30 min」은 흔적이다(검증 V-8) — 슬롯이 거절되면 되묻는다."""
        qt = resolve_query_time("30 min 알람", NOW, slot=slot(30, "hour", "30 min"))
        assert qt.slot_status == SLOT_REJECTED and qt.slot_reason == "unit_mismatch_span"
        assert qt.clarify == CLARIFY_UNRESOLVED

    @pytest.mark.parametrize(("text", "s"), [
        ("최근 30 min 알람", slot(30, "minute", "30 min")),
        ("삼십 분 대기 알람", slot(30, "minute", "삼십 분")),
        ("지난 2주 알람", slot(14, "day", "지난 2주")),        # week → day 환산
        ("지난 2주 알람", slot(2, "week", "지난 2주")),
        ("최근 1년 알람", slot(12, "month", "최근 1년")),      # year → month 환산
        ("지난 한 달 알람", slot(1, "month", "지난 한 달")),
        ("이번 주 알람", slot(None, "week", "이번 주", "this")),  # 수 + 단위어 없음 — 검사 생략
        ("지난 분기 알람", slot(1, "quarter", "지난 분기")),
        ("5분 전 알람", slot(5, "minute", "5분 전", "ago")),
    ])
    def test_matching_or_convertible_units_accepted(self, text: str, s: dict[str, Any]) -> None:
        spec, reason = slot_to_spec(s, text)
        assert reason is None and spec is not None

    def test_existing_reasons_take_precedence(self) -> None:
        """기존 `n_mismatch_span`이 단위 대조보다 먼저 난다(사유 불변)."""
        assert slot_to_spec(slot(4, "hour", "최근 3일"), "최근 3일 알람")[1] == "n_mismatch_span"
        assert slot_to_spec(slot(20, "minute", "삼십 분"), "삼십 분 대기")[1] == "n_mismatch_span"

    @pytest.mark.parametrize(("text", "s"), [
        ("최근 3일 알람", slot(3, "hour", "최근 3일")),
        ("지난 한 달 알람", slot(1, "week", "지난 한 달")),
    ])
    def test_other_unit_mismatches_rejected(self, text: str, s: dict[str, Any]) -> None:
        assert slot_to_spec(s, text)[1] == "unit_mismatch_span"


# ──────────────────────────────────────────────
# 교정 라운드(검증 V-1~V-5 · V-7 · V-8)
# ──────────────────────────────────────────────


class TestVerifyRound:
    @pytest.mark.parametrize("text", [
        "일주일 동안 알람", "일주일치 데이터 보여줘", "일 주간 보고", "이 주간 보고서 보여줘",
    ])
    def test_iljuil_and_weekly_report_not_traced(self, text: str) -> None:
        """「일주일」·「이 주간」은 한 글자 한자어 + 단위가 아니다(V-2·V-3)."""
        assert windows(text)[0] != CLARIFY_UNRESOLVED

    @pytest.mark.parametrize("text", ["지난 삼 주 동안 알람", "삼 일 동안 알람", "최근 오 일 알람"])
    def test_single_syllable_trace_kept(self, text: str) -> None:
        assert windows(text)[0] == CLARIFY_UNRESOLVED

    def test_hour_half_slot_minute_accepted(self) -> None:
        """「최근 1시간 반」 unit=minute n=90 — 한 명세 90분으로 받는다(V-4)."""
        spec, reason = slot_to_spec(slot(90, "minute", "최근 1시간 반"), "최근 1시간 반 알람")
        assert reason is None and spec is not None

    @pytest.mark.parametrize(("n", "unit"), [(30, "minute"), (1, "hour"), (90, "hour")])
    def test_hour_minute_slot_truncation_rejected(self, n: int, unit: str) -> None:
        """「최근 1시간 30분」을 30분·1시간·90시간으로 자르는 슬롯은 거절한다(V-4 기존 틈)."""
        _, reason = slot_to_spec(slot(n, unit, "최근 1시간 30분"), "최근 1시간 30분 알람")
        assert reason in ("n_mismatch_span", "unit_mismatch_span")

    def test_huge_digit_span_is_spec_error(self) -> None:
        """정수 변환 한도 초과 숫자열 — 예외가 새지 않고 사유 코드로 거절(V-5)."""
        span = "1" * 5000
        assert slot_to_spec(slot(30, "hour", span), f"최근 {span} 알람") == (
            None, "n_out_of_range")

    @pytest.mark.parametrize("text", ["최근 이십분의 일", "이십분의 일 수준"])
    def test_sino_fraction_not_period(self, text: str) -> None:
        assert is_default(text)

    @pytest.mark.parametrize("text", ["30 min 알람", "2 hrs 알람", "30 minutes 알람"])
    def test_bare_english_unit_asks_back(self, text: str) -> None:
        assert windows(text)[0] == CLARIFY_UNRESOLVED

    @pytest.mark.parametrize("text", [
        "30 min 간격 알람", "5 min 마다 알람", "3 hrs 이상 걸린 거래",
    ])
    def test_bare_english_unit_frequency_not_traced(self, text: str) -> None:
        assert is_default(text)
