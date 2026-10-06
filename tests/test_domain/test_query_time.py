"""plans/122 T-3·T-4 — 요청 단위 시간 해석(`src/domain/query_time.py`) 단위 테스트.

규칙 1순위 · LLM 슬롯 폴백(채택·폐기) · 되묻기 사유 · 두 주체(metric·event) · state 직렬화 왕복.
LLM 호출 0.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from src.domain.query_time import (
    SLOT_ABSENT,
    SLOT_ACCEPTED,
    SLOT_REJECTED,
    SLOT_UNUSED,
    QueryTime,
    resolve_query_time,
    resolve_task_time,
)
from src.domain.time_spec import KST, TimeResolution

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)


def kst(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=KST)


def slot(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "relation": None, "n": None, "unit": None, "completeness": None, "anchor": None,
        "start": None, "end": None, "display_grain": None, "span": None,
    }
    base.update(kw)
    return base


class TestRuleFirst:
    def test_rule_period_ignores_slot(self) -> None:
        qt = resolve_query_time(
            "어제 CPU 사용률", NOW, slot=slot(relation="last", n=30, unit="day", span="어제")
        )
        assert qt.slot_status == SLOT_UNUSED
        assert qt.metric is not None and qt.metric.source == "rule"
        assert (qt.metric.start, qt.metric.end) == (kst(2026, 9, 28), kst(2026, 9, 29))
        assert qt.metric.grain == "day"

    def test_metric_and_event_share_anchor_but_differ_by_subject(self) -> None:
        qt = resolve_query_time("이번 달 알람", NOW)
        assert qt.metric is not None and qt.event is not None
        assert qt.metric.end == kst(2026, 9, 29)            # 통계: 어제까지(D-201)
        assert qt.event.end == NOW                           # 사건: 기준 시각까지(D-291)
        assert qt.metric.anchor_at == qt.event.anchor_at == NOW

    def test_no_period_is_default_metric_and_unbounded_event(self) -> None:
        qt = resolve_query_time("CPU 사용률 상위 10대", NOW)
        assert qt.clarify is None and qt.slot_status == SLOT_ABSENT
        assert qt.metric is not None and qt.metric.source == "default"
        assert (qt.metric.start, qt.metric.end) == (kst(2026, 8, 1), kst(2026, 9, 1))
        assert qt.event is not None and qt.event.unbounded

    def test_naive_now_is_kst(self) -> None:
        qt = resolve_query_time("지난달", datetime(2026, 9, 29, 10, 0))
        assert qt.anchor_at == NOW


class TestSlotFallback:
    def test_slot_accepted_when_rule_misses(self) -> None:
        # 「3일 전」은 규칙 미매칭 흔적(unresolved) — 슬롯이 잡는다
        qt = resolve_query_time(
            "3일 전 메모리 사용률", NOW, slot=slot(relation="ago", n=3, unit="day", span="3일 전")
        )
        assert qt.slot_status == SLOT_ACCEPTED and qt.clarify is None
        assert qt.metric is not None and qt.metric.source == "llm"
        assert (qt.metric.start, qt.metric.end) == (kst(2026, 9, 26), kst(2026, 9, 27))

    def test_hallucinated_span_rejected_and_clarify_on_trace(self) -> None:
        qt = resolve_query_time(
            "3일 전 메모리 사용률", NOW, slot=slot(relation="ago", n=3, unit="day", span="사흘 전")
        )
        assert qt.slot_status == SLOT_REJECTED and qt.slot_reason == "span_not_in_text"
        assert qt.clarify == "unresolved" and qt.metric is None and qt.event is None

    def test_rejected_slot_without_trace_falls_back_to_default(self) -> None:
        qt = resolve_query_time(
            "CPU 사용률 상위 10대", NOW, slot=slot(relation="last", n=7, unit="day", span="지난주")
        )
        assert qt.slot_status == SLOT_REJECTED
        assert qt.metric is not None and qt.metric.source == "default"

    def test_present_word_is_not_a_period(self) -> None:
        qt = resolve_query_time(
            "현재 CPU 사용률", NOW, slot=slot(relation="this", unit="hour", span="현재")
        )
        assert qt.slot_status == SLOT_REJECTED and qt.slot_reason == "present_not_period"
        assert qt.metric is not None and qt.metric.source == "default"

    def test_slot_none_relation_is_absent(self) -> None:
        qt = resolve_query_time("CPU 사용률", NOW, slot=slot(relation="none"))
        assert qt.slot_status == SLOT_ABSENT and qt.slot_reason is None

    def test_rule_grain_applies_to_slot(self) -> None:
        qt = resolve_query_time(
            "3일 전 시간 단위 CPU", NOW, slot=slot(relation="ago", n=3, unit="day", span="3일 전")
        )
        assert qt.metric is not None and qt.metric.grain == "hour"


class TestClarify:
    @pytest.mark.parametrize("text, code", [
        ("13월 CPU 사용률", "invalid_date"),
        ("2027년 3월 CPU 사용률", "future_explicit"),
        ("내년 3월 CPU 사용률", "future_relative"),
    ])
    def test_rule_clarify_is_not_overridden_by_slot(self, text: str, code: str) -> None:
        qt = resolve_query_time(text, NOW, slot=slot(relation="last", n=1, unit="month", span="월"))
        assert qt.clarify == code and qt.metric is None and qt.event is None
        assert qt.resolution("metric") is None and qt.resolution("event") is None


class TestStateRoundTrip:
    @pytest.mark.parametrize("text", ["지난달", "최근 3시간", "이번 달 알람", "CPU 사용률", "13월"])
    def test_round_trip_through_json(self, text: str) -> None:
        qt = resolve_query_time(text, NOW)
        value = json.loads(json.dumps(qt.to_state(), ensure_ascii=False))
        assert QueryTime.from_state(value) == qt

    @pytest.mark.parametrize("value", [None, {}, {"version": 99}, {"version": 1}, "x"])
    def test_invalid_state_is_none(self, value: Any) -> None:
        assert QueryTime.from_state(value) is None

    def test_resolution_dict_has_label(self) -> None:
        qt = resolve_query_time("최근 30일", NOW)
        assert qt.metric is not None
        d = qt.metric.to_dict()
        assert d["label"] == "2026-08-30 ~ 2026-09-28"
        assert TimeResolution.from_dict(d) == qt.metric


class TestTaskTime:
    def test_task_with_explicit_period_resolves_with_base_anchor(self) -> None:
        base = resolve_query_time("지난달 대비 이번 달 CPU", NOW)
        task = resolve_task_time("이번 달 CPU 사용률", base)
        assert task is not base and task.anchor_at == base.anchor_at
        assert task.metric is not None and task.metric.start == kst(2026, 9, 1)

    def test_task_without_period_keeps_base(self) -> None:
        base = resolve_query_time("지난달 CPU 상위 서버의 메모리", NOW)
        assert resolve_task_time("해당 서버의 메모리 사용률", base) is base


class TestPresent:
    def test_present_without_period_does_not_force_default(self) -> None:
        qt = resolve_query_time("현재 CPU 사용률 상위 10대", NOW)
        assert qt.present and not qt.explicit and not qt.uses_default_period
        assert qt.metric is not None and qt.metric.source == "default"

    def test_present_with_explicit_period_is_explicit(self) -> None:
        qt = resolve_query_time("지금까지 지난주 알람", NOW)
        assert qt.present and qt.explicit

    def test_plain_default_uses_default_period(self) -> None:
        qt = resolve_query_time("CPU 사용률 상위 10대", NOW)
        assert not qt.present and qt.uses_default_period

    def test_present_survives_state_round_trip(self) -> None:
        qt = resolve_query_time("실시간 메모리", NOW)
        assert QueryTime.from_state(qt.to_state()) == qt
