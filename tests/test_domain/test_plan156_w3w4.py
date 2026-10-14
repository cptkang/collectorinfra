"""plans/156 W3·W4 — 빈도 어휘 일반화(G-5) · 「N시간 전」(G-6) · 사건 시 단위 롤링 끝(G-3) (D-333).

기준 시각 2026-10-13(화) 10:37 KST(§5 사례 표). 고정하는 계약:
  G-5 (a) 빈도·수량 어휘(이상·이하·미만·초과·넘·째·번째·씩·마다·간격·주기·만에)는 분뿐 아니라
      시간·일·주·개월·영문 단위 뒤에서도 기간이 아니다 — `최근` 접두가 있어도 기간 미지정 경로(통계
      기본값+고지 · 사건 기간 조건 없음). 「이내」는 기간이다. LLM 슬롯도 같은 판정으로 거절한다
  G-6 (a) 「N시간 전」 = 「N분 전」과 같게 그때부터 지금까지 — 사건 `[기준−N시간, 기준)` 분 정밀 ·
      통계 `[직전 정시−N시간, 직전 정시)`. 「3일 전」 등 일 이상은 되묻기 유지
  G-3 (a) 사건 주체 시 단위 롤링의 끝 = 기준 시각 분 경계(`[07:37, 10:37)`) · 통계는 정시 유지
LLM 호출 0.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain.query_time import SLOT_REJECTED, resolve_query_time
from src.domain.time_expr import CLARIFY_UNRESOLVED, recognize, slot_to_spec
from src.domain.time_spec import (
    KST,
    NOTE_DEFAULT_PERIOD,
    NOTE_EVENT_NO_DEFAULT,
    NOTE_MINUTE_WIDENED,
    PartialDate,
    TimeSpec,
    TimeSpecError,
    resolve,
)

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)


def _at(h: int, m: int = 0, *, day: int = 13) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=KST)


def _both(text: str, now: datetime = NOW) -> tuple[tuple, tuple]:
    qt = resolve_query_time(text, now)
    assert qt.clarify is None, (text, qt.clarify)
    assert qt.metric is not None and qt.event is not None
    return (qt.metric.start, qt.metric.end), (qt.event.start, qt.event.end)


def _is_not_period(text: str) -> bool:
    """기간 미지정 경로 — 통계 기본값+고지 · 사건 기간 조건 없음."""
    qt = resolve_query_time(text, NOW)
    return (
        qt.clarify is None and qt.metric is not None and qt.event is not None
        and qt.metric.source == "default" and NOTE_DEFAULT_PERIOD in qt.metric.notes
        and qt.event.unbounded and NOTE_EVENT_NO_DEFAULT in qt.event.notes
    )


# ── G-5 (a) 빈도·수량 어휘 — 모든 단위 ──────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    # §5 사례 표 · §1.1 ⑤
    "최근 3시간 이상 걸린 거래", "최근 3시간 넘게 걸린 거래", "최근 3일 이상 걸린 거래",
    "최근 3시간 마다 알람", "최근 3시간마다 알람", "최근 3일 간격 알람",
    # 같은 어휘 · 다른 단위
    "최근 2주 이상 미사용 서버", "최근 2주일 이상 미사용 서버", "최근 3개월 이상 사용",
    "지난 6달 이하 데이터", "최근 2시간 미만 작업", "최근 5일 초과 지연", "최근 3일째 장애",
    "최근 2주 번째", "최근 4시간씩 배치", "최근 12시간 주기 점검", "최근 3일 만에 재발",
    # 영문 단위(W1 verifier V-6 — min만 가드하던 비대칭 해소)
    "최근 30 minutes 이상 걸린 거래", "최근 3 hours 이상 걸린 거래", "최근 2 hr 이상",
    "last 3 days 간격 알람",
    # 수사(G-4)
    "최근 세 시간 이상 걸린 거래", "최근 이십 일 간격 알람",
    # 접두 없는 맨 표현(흔적에서도 빠진다 — 되묻지 않는다)
    "3시간 이상 걸린 거래", "3일 간격 백업",
])
def test_frequency_words_are_not_a_period_for_every_unit(text: str) -> None:
    assert _is_not_period(text), resolve_query_time(text, NOW)


@pytest.mark.parametrize(("text", "metric", "event"), [
    ("최근 3시간 이내 알람", (_at(7), _at(10)), (_at(7, 37), _at(10, 37))),
    ("최근 3일 이내 알람", (_at(0, day=10), _at(0)), (_at(0, day=10), _at(0))),
])
def test_within_is_still_a_period(text: str, metric: tuple, event: tuple) -> None:
    """「이내」는 빈도 어휘가 아니다 — 「최근 3시간 이내」는 기간이다."""
    assert _both(text) == (metric, event)


@pytest.mark.parametrize("text", ["최근 3 min_avg 값", "최근 30 min1 알람", "최근 3 hours_x"])
def test_english_unit_boundary_rejects_underscore_and_digit(text: str) -> None:
    """영문 단위 뒤 밑줄·숫자는 단위가 아니다(「min_avg」 칼럼명 — 기간 미지정 · 되묻지 않음)."""
    assert _is_not_period(text), resolve_query_time(text, NOW)


def test_minute_composite_beside_interval_keeps_the_hour_period() -> None:
    """빈도 가드는 단위 바로 뒤만 본다 — 「최근 3시간 5분마다」 = 3시간 기간 + 5분 간격(159)."""
    periods = [(s.relation, s.n, s.unit) for s in recognize("최근 3시간 5분마다 CPU")
               if s.relation != "none"]
    assert periods == [("last", 3, "hour")]


@pytest.mark.parametrize(("text", "span", "unit", "n"), [
    ("최근 3시간 이상 걸린 거래", "최근 3시간", "hour", 3),
    ("최근 3일 간격 알람", "최근 3일", "day", 3),
    ("최근 2주 이상 미사용 서버", "최근 2주", "week", 2),
    ("최근 3 hours 이상 걸린 거래", "최근 3 hours", "hour", 3),
])
def test_slot_frequency_is_rejected_for_every_unit(
    text: str, span: str, unit: str, n: int
) -> None:
    """LLM 슬롯도 규칙과 같은 결정적 가드로 거절한다 — 기간 미지정 경로로 간다."""
    slot = {"relation": "last", "n": n, "unit": unit, "span": span}
    assert slot_to_spec(slot, text) == (None, "freq_not_period")
    qt = resolve_query_time(text, NOW, slot=slot)
    assert qt.slot_status == SLOT_REJECTED and qt.slot_reason == "freq_not_period"
    assert qt.metric is not None and qt.metric.source == "default"


def test_slot_minute_frequency_keeps_its_reason_code() -> None:
    """분 단위 사유 코드는 종전(`minute_not_period` · plans/159) 그대로다."""
    slot = {"relation": "last", "n": 30, "unit": "minute", "span": "최근 30분"}
    assert slot_to_spec(slot, "최근 30분 이상 걸린 거래") == (None, "minute_not_period")


def test_slot_period_without_frequency_is_accepted() -> None:
    slot = {"relation": "last", "n": 3, "unit": "hour", "span": "최근 3시간 동안"}
    spec, reason = slot_to_spec(slot, "최근 3시간 동안 알람")
    assert reason is None and spec is not None


# ── G-6 (a) 「N시간 전」 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "metric", "event"), [
    ("3시간 전 알람", (_at(7), _at(10)), (_at(7, 37), _at(10, 37))),  # §5 사례 표
    ("3시간 전부터 CPU", (_at(7), _at(10)), (_at(7, 37), _at(10, 37))),
    ("1시간 전 CPU", (_at(9), _at(10)), (_at(9, 37), _at(10, 37))),
    ("3시간 전에 발생한 알람", (_at(7), _at(10)), (_at(7, 37), _at(10, 37))),
    ("24시간 전 알람", (_at(10, day=12), _at(10)), (_at(10, 37, day=12), _at(10, 37))),
])
def test_hours_ago_reads_since_then(text: str, metric: tuple, event: tuple) -> None:
    assert _both(text) == (metric, event)
    qt = resolve_query_time(text, NOW)
    assert qt.metric is not None and NOTE_MINUTE_WIDENED not in qt.metric.notes  # 고지 신설 없음


def test_hours_ago_is_one_spec() -> None:
    (spec,) = [s for s in recognize("3시간 전 알람") if s.relation != "none"]
    assert (spec.relation, spec.n, spec.unit, spec.span) == ("ago", 3, "hour", "3시간 전")


def test_hours_ago_equals_recent_hours() -> None:
    """「3시간 전」과 「최근 3시간」은 같은 구간이다(두 주체 모두)."""
    assert _both("3시간 전 알람") == _both("최근 3시간 알람")


@pytest.mark.parametrize("text", [
    "3일 전 알람", "2주 전 알람",  # 일 이상은 되묻기 유지(G-6 범위 밖)
    "3시간 전까지 알람",           # 끝 표현 — 「N분 전까지」와 같다
])
def test_day_or_longer_ago_and_until_still_ask(text: str) -> None:
    assert resolve_query_time(text, NOW).clarify == CLARIFY_UNRESOLVED


def test_minutes_ago_unchanged() -> None:
    """「5분 전」은 종전(D-328 ②) 그대로 — 사건 분 정밀 · 통계 직전 정시 + 고지."""
    assert _both("5분 전 알람") == ((_at(9), _at(10)), (_at(10, 32), _at(10, 37)))


@pytest.mark.parametrize("subject, window", [
    ("event", (_at(7, 37), _at(10, 37))),
    ("metric", (_at(7), _at(10))),
])
def test_resolve_ago_hour_spec(subject: str, window: tuple) -> None:
    """슬롯 `ago·hour`도 같은 해석이다(종전: 그 시 한 칸)."""
    res = resolve(TimeSpec(relation="ago", n=3, unit="hour"), NOW, subject=subject)  # type: ignore[arg-type]
    assert (res.start, res.end) == window and res.completeness == "rolling"


# ── G-3 (a) 사건 시 단위 롤링 끝 ────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "metric", "event"), [
    ("최근 3시간 알람", (_at(7), _at(10)), (_at(7, 37), _at(10, 37))),  # §5 사례 표
    ("지난 1시간 알람", (_at(9), _at(10)), (_at(9, 37), _at(10, 37))),
    ("최근 24시간 동안 알람", (_at(10, day=12), _at(10)), (_at(10, 37, day=12), _at(10, 37))),
    ("last 3 hours alarms", (_at(7), _at(10)), (_at(7, 37), _at(10, 37))),
])
def test_event_hour_rolling_ends_at_the_anchor_minute(
    text: str, metric: tuple, event: tuple
) -> None:
    assert _both(text) == (metric, event)


def test_event_hour_rolling_drops_anchor_seconds() -> None:
    at = datetime(2026, 10, 13, 10, 37, 45, tzinfo=KST)
    assert _both("최근 3시간 알람", at)[1] == (_at(7, 37), _at(10, 37))


def test_event_hour_rolling_on_the_hour_matches_metric() -> None:
    at = _at(10)
    assert _both("최근 3시간 알람", at) == ((_at(7), _at(10)), (_at(7), _at(10)))


@pytest.mark.parametrize(("text", "event"), [
    ("지난 3일간 알람", (_at(0, day=10), _at(0))),     # 일 단위 완결 — 그대로
    ("최근 30분 알람", (_at(10, 7), _at(10, 37))),       # 분 — D-328 ② 그대로
])
def test_non_hour_rolling_event_unchanged(text: str, event: tuple) -> None:
    assert _both(text)[1] == event


def test_hour_last_complete_slot_is_not_rolling() -> None:
    """완결성 complete를 명시한 시 단위 슬롯은 롤링이 아니다 — 두 주체 모두 정시(종전)."""
    spec = TimeSpec(relation="last", n=3, unit="hour", completeness="complete")
    for subject in ("event", "metric"):
        res = resolve(spec, NOW, subject=subject)  # type: ignore[arg-type]
        assert (res.start, res.end) == (_at(7), _at(10))


# ── J-3 「…부터」 뒤 끝 표현(교정 라운드) ─────────────────────────────────────────

@pytest.mark.parametrize(("text", "event", "metric"), [
    ("어제 오전 9시부터 정오까지 알람", (_at(9, day=12), _at(12, day=12)),
     (_at(9, day=12), _at(12, day=12))),
    ("어제 오전 9시부터 자정까지 알람", (_at(9, day=12), _at(0)), (_at(9, day=12), _at(0))),
    ("어제 오후 2시부터 3시간 동안 알람", (_at(14, day=12), _at(17, day=12)),
     (_at(14, day=12), _at(17, day=12))),
    ("어제 오후 10시부터 4시간 알람", (_at(22, day=12), _at(2)), (_at(22, day=12), _at(2))),
    # 오늘 자정·자정을 넘는 끝은 기준 시각 뒤 — 종전처럼 기준 시각에서 자른다(사건 분 · 통계 정시)
    ("오늘 오전 9시부터 자정까지 알람", (_at(9), _at(10, 37)), (_at(9), _at(10))),
    ("오늘 오전 9시부터 15시간 알람", (_at(9), _at(10, 37)), (_at(9), _at(10))),
])
def test_since_end_expression(text: str, event: tuple, metric: tuple) -> None:
    assert _both(text) == (metric, event)


@pytest.mark.parametrize(("text", "code"), [
    ("어제 오전 9시부터 오후까지 알람", CLARIFY_UNRESOLVED),       # 오전·오후 낱말만
    ("12월 31일 오후 2시부터 자정까지 알람", CLARIFY_UNRESOLVED),  # 연도 미상 — 다음 날 모호
    # 끝이 명시된 범위의 통계 구간이 비면 되묻는다(2026-10-14 사용자 확정)
    ("오늘 오전 10시부터 자정까지 알람", "clock_range_in_progress"),
])
def test_since_end_expression_clarify(text: str, code: str) -> None:
    assert resolve_query_time(text, NOW).clarify == code


def test_next_day_midnight_offset_only_as_end() -> None:
    assert PartialDate(day_offset=1, hour=0).day_offset == 1
    for fields in ({"day_offset": 1, "hour": 9}, {"day_offset": 1, "hour": 0, "minute": 30}):
        with pytest.raises(TimeSpecError):
            PartialDate(**fields)  # type: ignore[arg-type]
