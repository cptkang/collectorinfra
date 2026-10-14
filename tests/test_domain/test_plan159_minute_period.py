"""plans/159 W1 — 분 단위 기간 해석 사례 표(§6 ① · D-328).

기준 시각 2026-10-13 10:37 KST. 사건(event)은 분 경계까지 N분(정시로 깎지 않는다), 통계(metric)는
시 경계로 넓히고 `minute_widened_to_hour`를 고지한다. 시각 뒤 분·상한 초과는 종전처럼 되묻고,
빈도·수량 비교·「분기」류는 종전처럼 기간이 아니다. LLM 호출 0.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain.time_expr import interpret_detail, recognize, slot_to_spec
from src.domain.time_spec import KST, NOTE_MINUTE_WIDENED, TimeResolution

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)


def _hm(res: TimeResolution | None) -> tuple[str, str]:
    assert res is not None and res.start is not None and res.end is not None
    assert res.start.date() == res.end.date() == NOW.date()
    return res.start.strftime("%H:%M"), res.end.strftime("%H:%M")


def _both(text: str, now: datetime = NOW) -> tuple[TimeResolution | None, TimeResolution | None]:
    event = interpret_detail(text, now, subject="event")
    metric = interpret_detail(text, now, subject="metric")
    assert event.clarify is None and metric.clarify is None, (event, metric)
    return event.resolution, metric.resolution


# (질의, 사건 구간, 통계 구간 — None이면 통계 단언 생략)
_CASES: list[tuple[str, tuple[str, str], tuple[str, str] | None]] = [
    ("최근 30분 응답시간", ("10:07", "10:37"), ("09:00", "10:00")),
    ("지난 30분", ("10:07", "10:37"), ("09:00", "10:00")),
    ("30분간", ("10:07", "10:37"), ("09:00", "10:00")),
    ("30분 동안", ("10:07", "10:37"), ("09:00", "10:00")),
    ("30분치", ("10:07", "10:37"), ("09:00", "10:00")),
    ("최근 90분", ("09:07", "10:37"), ("08:00", "10:00")),
    ("최근 1시간 30분", ("09:07", "10:37"), ("08:00", "10:00")),
    ("5분 전 에러", ("10:32", "10:37"), ("09:00", "10:00")),
    ("최근 5분 내 종료된 프로세스", ("10:32", "10:37"), None),
    # 152 벤치 실패 턴 원문(사건 기대값)
    ("hud65_i 최근 10분 느린 거래 보여줘", ("10:27", "10:37"), None),
    ("최근 5분 트랜잭션 전부 다", ("10:32", "10:37"), None),
    ("15분 전부터 지금까지", ("10:22", "10:37"), None),
    ("느린 거래도 10분치", ("10:27", "10:37"), None),
    ("hud-antc65 30분 동안 느린 거래", ("10:07", "10:37"), None),
    # 「N시간 반」 — 종전에는 「최근 1시간」으로 반을 조용히 버렸다(D-328 ④ 확장)
    ("최근 1시간 반", ("09:07", "10:37"), ("08:00", "10:00")),
    ("2시간 반 동안", ("08:07", "10:37"), ("07:00", "10:00")),
    ("1시간 30분 전", ("09:07", "10:37"), ("08:00", "10:00")),
]


class TestCaseTable:
    @pytest.mark.parametrize("text, event, metric", _CASES)
    def test_minute_period(
        self, text: str, event: tuple[str, str], metric: tuple[str, str] | None
    ) -> None:
        ev, me = _both(text)
        assert _hm(ev) == event
        assert ev is not None and NOTE_MINUTE_WIDENED not in ev.notes
        assert ev.completeness == "rolling"
        if metric is not None:
            assert _hm(me) == metric
        assert me is not None and NOTE_MINUTE_WIDENED in me.notes

    def test_seconds_in_anchor_are_dropped_not_hours(self) -> None:
        """기준 시각에 초가 있으면 사건 끝은 그 분의 경계(10:37:00)다 — 정시가 아니다."""
        now = datetime(2026, 10, 13, 10, 37, 42, tzinfo=KST)
        ev, me = _both("최근 30분 응답시간", now)
        assert ev is not None and ev.end == datetime(2026, 10, 13, 10, 37, tzinfo=KST)
        assert _hm(ev) == ("10:07", "10:37")
        assert _hm(me) == ("09:00", "10:00")

    def test_composite_is_one_spec(self) -> None:
        """「N시간 M분」은 한 명세(60N+M분) — M분을 버리지 않는다."""
        (spec,) = recognize("최근 1시간 30분 응답시간")
        assert (spec.relation, spec.n, spec.unit) == ("last", 90, "minute")
        assert spec.span == "최근 1시간 30분"

    def test_interval_beside_period_is_not_a_period(self) -> None:
        """「지난 3시간 TPS 추세 10분 간격」 — 기간은 「3시간」 하나뿐(10분 간격은 빈도)."""
        periods = [s for s in recognize("지난 3시간 TPS 추세 10분 간격") if s.relation != "none"]
        assert [(s.n, s.unit, s.span) for s in periods] == [(3, "hour", "지난 3시간")]
        ev, me = _both("지난 3시간 TPS 추세 10분 간격")
        # plans/156 G-3 (D-333) — 사건 시 단위 롤링은 기준 시각 분 경계까지 · 통계는 정시 그대로
        assert _hm(ev) == ("07:37", "10:37")
        assert _hm(me) == ("07:00", "10:00")
        assert me is not None and NOTE_MINUTE_WIDENED not in me.notes


class TestNotPeriod:
    @pytest.mark.parametrize("text", [
        "5분 간격", "5분마다", "30분 이상 걸린 거래", "30분 넘게",
        "분석", "분류", "세 분", "분당 요청 수", "1시간 간격으로 CPU",
    ])
    def test_default_period(self, text: str) -> None:
        """기간 아님 — 종전과 같이 기본값(통계 직전 완결 월 · 사건 기간 조건 없음)."""
        assert not [s for s in recognize(text) if s.relation != "none"]
        ev, me = _both(text)
        assert ev is not None and ev.unbounded
        assert me is not None and me.source == "default"

    def test_quarter_is_not_minute(self) -> None:
        (spec,) = recognize("3분기")
        assert spec.unit == "quarter"


class TestClarify:
    # plans/156 G-1 — 「10시 30분부터」·「14시 30분 알람」은 시각 규칙이 해석한다(D-328 ⑤ 개정) →
    # 아래 test_clock_minute_is_resolved_by_clock_rule로 옮겼다. 시각 뒤 「동안·전」은 계속 되묻는다
    @pytest.mark.parametrize("text", [
        "10시 30분 동안 CPU", "10시 30분 전 알람",
    ])
    def test_minute_after_clock_is_asked(self, text: str) -> None:
        """시각 뒤 분은 기간 단위가 아니다 — 흔적으로 남아 종전처럼 되묻는다(⑤)."""
        for subject in ("event", "metric"):
            assert interpret_detail(text, NOW, subject=subject).clarify == "unresolved"

    def test_clock_minute_is_resolved_by_clock_rule(self) -> None:
        """plans/156 G-1 — 시각 뒤 분은 시각의 일부다. 10:37 기준 「14시 30분」은 미래라 되묻고,
        「오전 10시 30분부터」는 사건 [10:30, 10:37) · 통계는 넓힌 구간이 비어 직전 정시 한 시간으로
        대체한다(plans/156 확정 (c)). 표지 없는 「10시 30분부터」는 오전·오후를 되묻는다(2026-10-14
        확정)."""
        for subject in ("event", "metric"):
            assert interpret_detail("14시 30분 알람", NOW, subject=subject).clarify == (
                "future_explicit")
            assert interpret_detail("10시 30분부터", NOW, subject=subject).clarify == (
                "clock_meridiem")
        ev = interpret_detail("오전 10시 30분부터", NOW, subject="event").resolution
        assert ev is not None and (ev.start, ev.end) == (
            NOW.replace(minute=30), NOW.replace(minute=37))
        # plans/156 확정 (c) — 되묻지 않고 직전 정시 [09:00, 10:00)
        mt = interpret_detail("오전 10시 30분부터", NOW, subject="metric").resolution
        assert mt is not None and (mt.start, mt.end) == (
            NOW.replace(hour=9, minute=0), NOW.replace(minute=0))

    @pytest.mark.parametrize("text", ["최근 10081분", "최근 200시간 30분"])
    def test_over_n_max_is_asked(self, text: str) -> None:
        """분 상한(7일) 초과는 되묻는다 — 합성이 거부돼도 「최근 200시간」으로 M분을 버리지 않음."""
        assert interpret_detail(text, NOW).clarify == "unresolved"

    def test_bare_minutes_like_bare_hours(self) -> None:
        """맨 「N분」(접두·접미 없음)은 맨 「N시간」처럼 흔적이다 — 되묻는다."""
        for text in ("은행 쪽 hud65_i 응답시간 30분", "은행 쪽 hud65_i 응답시간 3시간"):
            assert interpret_detail(text, NOW, subject="event").clarify == "unresolved"

    @pytest.mark.parametrize("text", ["5분 전까지 알람", "30분 전후 알람", "5분 전체 거래"])
    def test_ago_variants_not_read_as_window(self, text: str) -> None:
        """「전까지」(끝)·「전후」·「전체」는 「N분 전부터 지금까지」가 아니다 — 흔적(되묻기)."""
        assert interpret_detail(text, NOW).clarify == "unresolved"


class TestCorrectionRound:
    """W1 교정 라운드(verifier V-1~V-4·경미) 경계 — 결함 재현은 `test_plan159_verify.py`."""

    @pytest.mark.parametrize("text", ["최근 30분 이내 알람", "최근 30분의 응답시간"])
    def test_within_and_genitive_stay_periods(self, text: str) -> None:
        """빈도·수량 가드는 「이내」(만료 필터 어휘)·「의」(속격)를 막지 않는다."""
        ev, _ = _both(text)
        assert _hm(ev) == ("10:07", "10:37")

    @pytest.mark.parametrize("text", ["최근 1시간 70분", "최근 5분위 응답"])
    def test_malformed_minutes_are_asked(self, text: str) -> None:
        """M ≥ 60 합성·「분위」는 기간으로 만들지 않고 되묻는다(조용히 「최근 1시간」 아님)."""
        assert interpret_detail(text, NOW).clarify == "unresolved"

    @pytest.mark.parametrize("text, span", [
        ("30분 이상 걸린 거래", "30분"),
        ("5분 간격 CPU", "5분 간격"),
    ])
    def test_slot_minute_frequency_is_rejected(self, text: str, span: str) -> None:
        """슬롯 unit=minute도 규칙과 같은 결정적 가드로 거절한다 — LLM 출력에 맡기지 않는다."""
        spec, reason = slot_to_spec(
            {"relation": "last", "n": int(span.split("분")[0]), "unit": "minute", "span": span},
            text,
        )
        assert spec is None and reason == "minute_not_period"
