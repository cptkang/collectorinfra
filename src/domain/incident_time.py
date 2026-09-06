"""사건 시각 파싱 — 질의의 시간 표현을 사건 좌표계(기준시각·구간)로 (plans/50 A′-5 · SPEC-incident-scope).

*"어제 14시쯤 장애 원인"* 에서 **결정적으로** 기준시각을 뽑는다(LLM 비의존 — D-035). `now`를 주입받아
벽시계를 쓰지 않으며, 표현이 없으면 None(앵커 없는 조사 — 종전 동작).

도구 창은 `[reference_time − lookback, reference_time]`(상한 = 기준시각)이라 "쯤"의 뒤쪽을 덮도록
해상도별 여유(slack)를 더한다: 시 단위 60분 · 분 단위 30분. 날짜만 있으면 그 날 전체.

domain 계층 — 표준 라이브러리만 의존한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# 기본 사건 구간(분) — sre_agent domain.incident_scope.DEFAULT_LOOKBACK_MINUTES와 같은 값(양방향 import 0).
DEFAULT_LOOKBACK_MINUTES = 120
_SLACK_MINUTES = {"hour": 60, "minute": 30}

_DAY_WORDS: dict[str, int] = {"오늘": 0, "어제": 1, "그제": 2, "그저께": 2, "엊그제": 2}
_RE_DAY_WORD = re.compile("|".join(sorted(_DAY_WORDS, key=len, reverse=True)))
_RE_YMD = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?")
_RE_MD = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_RE_RECENT = re.compile(r"최근\s*(\d+)\s*(시간|분)")
_RE_AGO = re.compile(r"(\d+)\s*(시간|분)\s*(?:쯤|정도)?\s*전")
_RE_HHMM = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?!\d)")
_RE_H_M = re.compile(r"(?<!\d)(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분|(반))?(?!간)")
_RE_MERIDIEM = re.compile(r"(오전|오후|새벽|아침|저녁|밤)")


@dataclass(frozen=True)
class IncidentTime:
    """파싱 결과. `reference_time`은 도구 창의 상한(ISO), `lookback_minutes`는 폭."""

    anchor: datetime
    resolution: str  # "day" | "hour" | "minute" | "range"
    reference_time: str
    lookback_minutes: int


def _apply_meridiem(hour: int, text: str, pos: int) -> int:
    """시각 앞 12자 안의 오전/오후류 표지로 24시제로 보정한다."""
    m = _RE_MERIDIEM.search(text[max(0, pos - 12):pos])
    if not m:
        return hour
    word = m.group(1)
    if word in ("오후", "저녁", "밤") and hour < 12:
        return hour + 12
    if word == "오전" and hour == 12:
        return 0
    return hour


def _parse_day(text: str, now: datetime) -> datetime | None:
    m = _RE_YMD.search(text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return datetime(y, mo, d)
    m = _RE_MD.search(text)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        day = datetime(now.year, mo, d)
        return day if day <= now else day.replace(year=now.year - 1)
    m = _RE_DAY_WORD.search(text)
    if m:
        return (now - timedelta(days=_DAY_WORDS[m.group(0)])).replace(hour=0, minute=0, second=0, microsecond=0)
    return None


def _parse_clock(text: str) -> tuple[int, int, str] | None:
    """(hour, minute, resolution) — HH:MM 우선, 그다음 'H시 (M분|반)'. 'N시간'은 제외."""
    m = _RE_HHMM.search(text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h < 24 and mi < 60:
            return _apply_meridiem(h, text, m.start()), mi, "minute"
    m = _RE_H_M.search(text)
    if m:
        h = int(m.group(1))
        if h >= 24:
            return None
        h = _apply_meridiem(h, text, m.start())
        if m.group(2) is not None:
            return h, min(int(m.group(2)), 59), "minute"
        if m.group(3):
            return h, 30, "minute"
        return h, 0, "hour"
    return None


def _result(anchor: datetime, resolution: str) -> IncidentTime:
    if resolution == "day":
        ref = anchor.replace(hour=23, minute=59, second=59)
        return IncidentTime(anchor, "day", ref.isoformat(), 24 * 60)
    slack = _SLACK_MINUTES[resolution]
    ref = anchor + timedelta(minutes=slack)
    return IncidentTime(anchor, resolution, ref.isoformat(), DEFAULT_LOOKBACK_MINUTES + slack)


def parse_incident_time(query: str, now: datetime) -> IncidentTime | None:
    """질의에서 사건 시각을 뽑는다. 시간 표현이 없으면 None.

    우선순위: 최근 N시간/분(창의 끝 = now) → N시간/분 전 → 날짜(+시각) → 시각만(오늘, 미래면 어제).
    """
    text = (query or "").strip()
    if not text:
        return None
    now = now.replace(second=0, microsecond=0)

    m = _RE_RECENT.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        minutes = n * 60 if unit == "시간" else n
        return IncidentTime(now - timedelta(minutes=minutes), "range", now.isoformat(), max(1, minutes))

    m = _RE_AGO.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "시간":
            return _result(now - timedelta(hours=n), "hour")
        return _result(now - timedelta(minutes=n), "minute")

    day = _parse_day(text, now)
    clock = _parse_clock(text)
    if day is None and clock is None:
        return None
    if clock is None:
        return _result(day, "day")  # type: ignore[arg-type]
    h, mi, resolution = clock
    base = day if day is not None else now.replace(hour=0, minute=0)
    anchor = base.replace(hour=h, minute=mi)
    if day is None and anchor > now:
        anchor -= timedelta(days=1)
    return _result(anchor, resolution)


__all__ = ["DEFAULT_LOOKBACK_MINUTES", "IncidentTime", "parse_incident_time"]
