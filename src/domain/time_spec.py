"""시간 표현 도메인 모델 + 결정적 해석기 (plans/122 T-1 · D-275 ⑪⑮ · G-15 정책 표 §10.3.1).

「LLM은 슬롯, 코드는 계산」(plans/122 §10.3)의 **계산** 쪽이다. 규칙 인식기(`time_expr.recognize`)나
LLM 슬롯(`time_expr.slot_to_spec`)이 만든 `TimeSpec`을 기준 시각(요청 수신 시각 · KST)과 함께 받아
반개구간 `[start, end)` · 입도(grain) · 완결성을 가진 `TimeResolution`으로 바꾼다.

해석 정책의 정본은 plans/122 §10.3.1 표(G-15 확정)다. 이 모듈은 그 표를 코드로 옮긴 것이고,
표가 정하지 않은 조합은 아래 「표 밖 조합」에 규칙을 적었다.

- 기준 시각: 요청 수신 시각(KST). 벽시계를 읽지 않고 인자로 받는다(`plans/121` U-17과 같은 축)
- 주 시작: 월요일
- `anchor=data_latest`(최신 적재 월 기준)는 쓰지 않는다 — 받으면 `TimeSpecError`
- 완결 월 절단은 월 **통계 테이블**의 사정이다. 해석기는 `completeness`만 싣고 테이블을
  고르지 않는다
  (테이블명은 DB 특화라 어댑터 몫 — D-089)
- 입도 = f(display_grain, 구간 경계 정렬). 월 입도는 구간 양 끝이 월 경계에 맞을 때만 나온다

표 밖 조합(plans/122 T-1 구현 시 정함 → **2026-09-30 사용자 확정 · D-291** — §10.3.1 표에
행으로 올렸다):

- `this` 기본 완결성: 연·반기 = complete(완결 월까지 + 「이번 달 제외」), 분기·월·주·일·시 = to_date
- 명시 달력 기간이 진행 중이면: 단일 월 → 「이번 달」과 같게(D-201 현행 동형), 분기 → 「이번 분기」,
  연 → 「올해」, 일 → 「오늘」과 같게 자른다. 반기·월 범위는 D-185 현행(「진행 중 반기 허용」)대로
  달력 구간을 그대로 쓰고 `period_in_progress`를 단다
- 연도 없는 절대값은 "미래가 아닌 가장 최근 발생"(D-185 · 당월 허용)
- 기간 표현이 여럿이면(비교 질의) 모두를 덮는 최소 구간(`cover`)과 `multiple_periods`

사건(알람) 예외(2026-09-30 사용자 확정 · D-291 — §10.3.1 「알람 경로」 행):
`subject="event"`로 부르면
- 기간 없음 = **기간 조건 없음**(unbounded · `event_no_default_period`) — 직전 완결 월
  기본값을 쓰지 않는다
  (「최근 발생 순 100건」은 최신 알람이지 지난달 알람이 아니다 · D-02)
- 진행 중 기간(`this`·`to_date` · 진행 중인 명시 달력 기간) = 시작 ~ **기준 시각**(`event_to_now`) —
  통계의 「어제까지·완결 월」 절단(D-201 · G-15 ③)은 **성능 통계** 사정이라 사건에는
  쓰지 않는다(D-05)

되묻기(2026-09-30 사용자 확정 · D-291): 연도를 명시한 기간이 **통째로 미래**면(시작 > 기준 시각)
`TimeSpecError("future_explicit_period")` — 호출부가 되묻는다. 존재하지 않는 월·날짜는 인식기
(`time_expr.interpret_detail`)가 `invalid_date`로 되묻기 대상에 올린다.

domain 계층 — 표준 라이브러리만 의존한다(src.config·src.utils import 금지).
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal, get_args

KST = timezone(timedelta(hours=9))

Relation = Literal[
    "last", "this", "next", "ago", "since", "until", "between", "to_date", "absolute", "none"
]
Unit = Literal["hour", "day", "week", "month", "quarter", "half", "year"]
Completeness = Literal["complete", "rolling", "to_date"]
Anchor = Literal["now", "explicit"]
Grain = Literal["hour", "day", "month"]
DisplayGrain = Literal["hour", "day", "month", "none"]
Source = Literal["rule", "llm", "default"]
#: 해석 대상 — 성능 통계(metric · 기본)와 사건(event · 알람 이력·활성 알람).
#: 사건 예외는 모듈 머리말.
Subject = Literal["metric", "event"]

RELATIONS: frozenset[str] = frozenset(get_args(Relation))
UNITS: frozenset[str] = frozenset(get_args(Unit))
COMPLETENESS: frozenset[str] = frozenset(get_args(Completeness))
ANCHORS: frozenset[str] = frozenset(get_args(Anchor))
GRAINS: frozenset[str] = frozenset(get_args(Grain))
DISPLAY_GRAINS: frozenset[str] = frozenset(get_args(DisplayGrain))
SOURCES: frozenset[str] = frozenset(get_args(Source))
SUBJECTS: frozenset[str] = frozenset(get_args(Subject))

#: 단위별 n 상한 — 보존 기간이 아니라 **값 범위 검사**(환각·오타 차단)용 상한이다.
N_MAX: dict[str, int] = {
    "hour": 24 * 366, "day": 3660, "week": 520, "month": 120,
    "quarter": 40, "half": 20, "year": 10,
}

# 고지 코드(TimeResolution.notes). 문구는 소비처(plans/122 T-8)가 만든다.
NOTE_DEFAULT_PERIOD = "default_period"              # 기간 미지정 — 직전 완결 월(지난달) 기준
NOTE_CURRENT_MONTH_EXCLUDED = "current_month_excluded"  # 완결 월까지만 — 「이번 달 제외」
NOTE_EMPTY_RANGE = "empty_range"                    # 완결 구간이 아직 없다(매월 1일 「이번 달」 등)
NOTE_PERIOD_IN_PROGRESS = "period_in_progress"      # 명시 달력 기간이 진행 중
NOTE_FUTURE_PERIOD = "future_period"                # 구간 시작이 기준 시각 이후
NOTE_DISPLAY_GRAIN_UNALIGNED = "display_grain_unaligned"  # 요청 입도가 구간 경계에 안 맞아 낮췄다
NOTE_YEAR_INFERRED = "year_inferred"                # 연도 미상 — 미래가 아닌 가장 최근으로 보정
NOTE_MULTIPLE_PERIODS = "multiple_periods"          # 기간 표현 여럿 — 모두를 덮는 구간
NOTE_EVENT_NO_DEFAULT = "event_no_default_period"   # 사건(알람) 기간 미지정 — 기간 조건 없음(D-291)
NOTE_EVENT_TO_NOW = "event_to_now"                  # 사건(알람) 진행 중 기간 — 기준 시각까지(D-291)

_GRAIN_RANK: dict[str, int] = {"hour": 0, "day": 1, "month": 2}
_GRAIN_OF: dict[str, Grain] = {"hour": "hour", "day": "day", "month": "month"}
_COARSE_TO_FINE: tuple[Grain, ...] = ("month", "day", "hour")
_UNIT_GRAIN: dict[str, Grain] = {
    "hour": "hour", "day": "day", "week": "day",
    "month": "month", "quarter": "month", "half": "month", "year": "month",
}
_MONTHS_PER: dict[str, int] = {"month": 1, "quarter": 3, "half": 6, "year": 12}
_NEEDS_UNIT: frozenset[str] = frozenset({"last", "this", "next", "ago", "to_date"})


class TimeSpecError(ValueError):
    """시간 슬롯·명세가 규약을 어겼다. `code`는 사유 코드(로그·폐기 사유로 쓴다)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# ──────────────────────────────────────────────
# 모델
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class PartialDate:
    """절대 시점의 부분값 — 연·월·일·시 중 아는 것만 둔다(plans/122 §10.3 「절대 시작·끝」).

    `year_offset`은 「작년 6월」·「올해 하반기」처럼 **기준 연도 대비 상대 연도**다(0 = 올해,
    -1 = 작년). 인식기가 기준 시각 없이 명세를 만들 수 있게 하고, LLM이 연도를 계산하지 않게 한다.
    `year`와 함께 쓸 수 없다. 둘 다 없으면 해석기가 "미래가 아닌 가장 최근"으로 연도를 정한다.
    """

    year: int | None = None
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    year_offset: int | None = None

    def __post_init__(self) -> None:
        if all(v is None for v in (self.year, self.month, self.day, self.hour, self.year_offset)):
            raise TimeSpecError("partial_date_empty")
        if self.year is not None and self.year_offset is not None:
            raise TimeSpecError("partial_date_year_conflict")
        if self.year is not None and not 1900 <= self.year <= 2100:
            raise TimeSpecError("year_out_of_range", str(self.year))
        if self.year_offset is not None and not -100 <= self.year_offset <= 1:
            raise TimeSpecError("year_out_of_range", f"offset {self.year_offset}")
        if self.month is not None and not 1 <= self.month <= 12:
            raise TimeSpecError("month_out_of_range", str(self.month))
        if self.day is not None:
            if self.month is None:
                raise TimeSpecError("partial_date_gap", "day without month")
            # 연도 미상이면 윤년 가능성을 열어 둔다(2월 29일 허용)
            ref_year = self.year if self.year is not None else 2000
            if not 1 <= self.day <= calendar.monthrange(ref_year, self.month)[1]:
                raise TimeSpecError("day_out_of_range", f"{self.month}/{self.day}")
        if self.hour is not None:
            if self.day is None:
                raise TimeSpecError("partial_date_gap", "hour without day")
            if not 0 <= self.hour <= 23:
                raise TimeSpecError("hour_out_of_range", str(self.hour))

    @property
    def precision(self) -> Unit:
        """가장 세밀한 필드의 단위(시 > 일 > 월 > 연)."""
        if self.hour is not None:
            return "hour"
        if self.day is not None:
            return "day"
        if self.month is not None:
            return "month"
        return "year"

    @property
    def has_year(self) -> bool:
        """연도(절대 또는 상대)가 주어졌는가."""
        return self.year is not None or self.year_offset is not None


@dataclass(frozen=True)
class TimeSpec:
    """기간 표현 하나의 슬롯(plans/122 §10.3 「LLM의 역할」 스키마와 같은 형태).

    - `relation`: last(지난·최근 N) · this(이번) · next · ago(N 전) · since(~부터) · until(~까지)
      · between(A~B) · to_date(이번 단위 누적) · absolute(달력 기간 하나) · none(기간 없음)
    - `completeness`: None이면 정책 기본값(§10.3.1)을 해석기가 채운다
    - `abs_start`·`abs_end`: 절대 시작·끝. `anchor=explicit`이면 `abs_start`가 기준 시각이 된다
    - `unit`: last·this·next·ago·to_date에서 필수. absolute에서는 달력 단위(분기·반기 등)를 지정한다
    - `unbounded`: 「~한 적이 있는」(기간 조건 없음) — 규칙 인식 전용(LLM 슬롯 enum에는 없다)
    - `span`·`span_pos`: 원문 스팬과 원문 안 위치 `(시작, 끝)`
    """

    relation: Relation
    n: int | None = None
    unit: Unit | None = None
    completeness: Completeness | None = None
    anchor: Anchor = "now"
    abs_start: PartialDate | None = None
    abs_end: PartialDate | None = None
    display_grain: DisplayGrain = "none"
    unbounded: bool = False
    span: str = ""
    span_pos: tuple[int, int] | None = None
    source: Source = "rule"

    def __post_init__(self) -> None:
        if self.relation not in RELATIONS:
            raise TimeSpecError("invalid_relation", str(self.relation))
        if self.unit is not None and self.unit not in UNITS:
            raise TimeSpecError("invalid_unit", str(self.unit))
        if self.completeness is not None and self.completeness not in COMPLETENESS:
            raise TimeSpecError("invalid_completeness", str(self.completeness))
        if str(self.anchor) == "data_latest":
            raise TimeSpecError("anchor_data_latest_unsupported")  # G-15: 최신 적재 기준 미사용
        if self.anchor not in ANCHORS:
            raise TimeSpecError("invalid_anchor", str(self.anchor))
        if self.display_grain not in DISPLAY_GRAINS:
            raise TimeSpecError("invalid_display_grain", str(self.display_grain))
        if self.source not in SOURCES:
            raise TimeSpecError("invalid_source", str(self.source))
        if self.n is not None:
            if isinstance(self.n, bool) or not isinstance(self.n, int):
                raise TimeSpecError("invalid_n", repr(self.n))
            cap = N_MAX.get(self.unit or "", max(N_MAX.values()))
            if not 1 <= self.n <= cap:
                raise TimeSpecError("n_out_of_range", f"{self.n} {self.unit}")
        if self.relation in _NEEDS_UNIT and self.unit is None:
            raise TimeSpecError("unit_missing", self.relation)
        if self.relation in ("absolute", "since", "between") and self.abs_start is None:
            raise TimeSpecError("start_missing", self.relation)
        if self.relation in ("until", "between") and self.abs_end is None:
            raise TimeSpecError("end_missing", self.relation)
        if self.anchor == "explicit":
            if self.abs_start is None:
                raise TimeSpecError("anchor_explicit_without_date")
            if self.relation not in _NEEDS_UNIT:
                raise TimeSpecError("anchor_explicit_unsupported_relation", self.relation)
        if self.unbounded and self.relation != "none":
            raise TimeSpecError("unbounded_with_period", self.relation)
        if self.relation == "absolute" and self.unit is not None and self.abs_start is not None:
            _check_calendar_unit(self.unit, self.abs_start)


def _check_calendar_unit(unit: str, p: PartialDate) -> None:
    """absolute + unit의 시작이 그 달력 단위의 첫 칸인지 본다(3분기 = 7월 시작 등)."""
    prec_rank = {"hour": 0, "day": 1, "week": 1, "month": 2, "quarter": 3, "half": 4, "year": 5}
    # 주는 일 정밀도 이상이면 받는다(시작은 그 주 월요일로 내린다)
    if prec_rank[unit] < prec_rank[p.precision]:
        raise TimeSpecError("unit_finer_than_date", f"{unit} < {p.precision}")
    if unit in ("quarter", "half") and (p.month is None or (p.month - 1) % _MONTHS_PER[unit]):
        raise TimeSpecError("unit_misaligned", f"{unit} month={p.month}")


@dataclass(frozen=True)
class TimeResolution:
    """해석 결과 — 반개구간 `[start, end)`(tz-aware · KST) · 입도 · 완결성 · 출처.

    `plans/121` TP-3.1의 `time_window` 의존 타입이 이 클래스를 그대로 쓴다(D-275 ⑭ · 넷째 사본
    금지 D-131). 필드 이름과 의미를 바꾸지 않는다.

    - `unbounded=True`: 기간 조건 없음(「~한 적이 있는」) — `start`·`end`가 둘 다 None
    - `start=None`·`end` 있음: 「~까지」(왼쪽이 열린 구간)
    - `start == end`: 빈 구간(매월 1일의 「이번 달」 등 — 0행이 정답)
    - `anchor`: now(요청 시각 기준) · explicit(사건 시각 등 명시 기준 — `window_around`)
    """

    start: datetime | None
    end: datetime | None
    grain: Grain
    completeness: Completeness
    source: Source
    anchor_at: datetime
    span: str = ""
    unbounded: bool = False
    notes: tuple[str, ...] = ()
    anchor: Anchor = "now"

    def __post_init__(self) -> None:
        if self.grain not in GRAINS:
            raise TimeSpecError("invalid_grain", str(self.grain))
        for dt in (self.start, self.end, self.anchor_at):
            if dt is not None and dt.tzinfo is None:
                raise TimeSpecError("naive_datetime", str(dt))
        if self.unbounded:
            if self.start is not None or self.end is not None:
                raise TimeSpecError("unbounded_with_bounds")
            return
        if self.end is None:
            raise TimeSpecError("end_missing")
        if self.start is not None and self.start > self.end:
            raise TimeSpecError("start_after_end", f"{self.start} > {self.end}")

    @property
    def is_empty(self) -> bool:
        """빈 구간인가(시작 == 끝)."""
        return self.start is not None and self.start == self.end

    def month_range(self) -> tuple[str, str] | None:
        """YYYYMM 월 투영 `(시작월, 끝월)` — 양 끝이 월 경계에 맞는 비지 않은 구간만(아니면 None).

        종전 `resolve_stat_month_range` 계약(끝 월 포함 범위)과 같은 형태다.
        """
        if self.unbounded or self.start is None or self.end is None or self.start >= self.end:
            return None
        if not (_aligned(self.start, "month") and _aligned(self.end, "month")):
            return None
        last = _add_months(self.end, -1)
        return (f"{self.start:%Y%m}", f"{last:%Y%m}")

    def label(self) -> str:
        """사람이 읽는 기간 표기(`2026-08-24 ~ 2026-09-22`). 'YYYYMM'은 노출하지 않는다.

        일·월 입도는 끝을 **포함하는 마지막 날**로 적는다. 시 입도는 시각을 그대로 적는다.
        """
        if self.unbounded:
            return "전체 보관 기간(기간 조건 없음)"
        assert self.end is not None  # __post_init__이 보장
        hourly = not all(_aligned(x, "day") for x in (self.start, self.end) if x is not None)
        if self.is_empty:
            fmt = "%Y-%m-%d %H:%M" if hourly else "%Y-%m-%d"
            return f"{self.end:{fmt}} (완결된 구간 없음)"
        if hourly:
            head = f"{self.start:%Y-%m-%d %H:%M}" if self.start is not None else ""
            return f"{head} ~ {self.end:%Y-%m-%d %H:%M}".strip()
        last_day = self.end - timedelta(days=1)
        head = f"{self.start:%Y-%m-%d}" if self.start is not None else ""
        return f"{head} ~ {last_day:%Y-%m-%d}".strip()


# ──────────────────────────────────────────────
# 달력 연산
# ──────────────────────────────────────────────


def _to_kst(dt: datetime) -> datetime:
    """naive면 KST로 보고, aware면 KST로 옮긴다."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _floor(dt: datetime, unit: str) -> datetime:
    """단위 시작으로 내린다(주 = 월요일 · 분기 = 1·4·7·10월 · 반기 = 1·7월)."""
    dt = dt.replace(minute=0, second=0, microsecond=0)
    if unit == "hour":
        return dt
    dt = dt.replace(hour=0)
    if unit == "day":
        return dt
    if unit == "week":
        return dt - timedelta(days=dt.weekday())
    if unit == "month":
        return dt.replace(day=1)
    if unit == "quarter":
        return dt.replace(month=(dt.month - 1) // 3 * 3 + 1, day=1)
    if unit == "half":
        return dt.replace(month=1 if dt.month <= 6 else 7, day=1)
    return dt.replace(month=1, day=1)  # year


def _add_months(dt: datetime, k: int) -> datetime:
    """k개월 이동(말일 초과는 그 달 말일로 맞춘다)."""
    total = dt.year * 12 + dt.month - 1 + k
    year, month = divmod(total, 12)
    month += 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _shift(dt: datetime, unit: str, k: int) -> datetime:
    """단위 k칸 이동."""
    if unit == "hour":
        return dt + timedelta(hours=k)
    if unit == "day":
        return dt + timedelta(days=k)
    if unit == "week":
        return dt + timedelta(weeks=k)
    return _add_months(dt, k * _MONTHS_PER[unit])


def _aligned(dt: datetime, grain: str) -> bool:
    """dt가 grain 경계(정시 · 0시 · 1일 0시)에 있는가."""
    if dt.minute or dt.second or dt.microsecond:
        return False
    if grain == "hour":
        return True
    if dt.hour:
        return False
    return grain == "day" or dt.day == 1


def _alignment(start: datetime | None, end: datetime) -> Grain:
    """양 끝이 함께 맞는 가장 굵은 경계(월 > 일 > 시)."""
    points = [x for x in (start, end) if x is not None]
    for grain in _COARSE_TO_FINE:
        if all(_aligned(x, grain) for x in points):
            return grain
    return "hour"


def _finer(a: Grain, b: Grain) -> Grain:
    return a if _GRAIN_RANK[a] <= _GRAIN_RANK[b] else b


def _select_grain(
    start: datetime | None, end: datetime, display: str, cap: Grain
) -> tuple[Grain, tuple[str, ...]]:
    """입도 = f(display_grain, 구간 경계 정렬) — §10.3 「입도 선택」.

    요청 입도(display)가 있으면 그것을 쓰되, 구간 경계에 맞지 않는 굵은 입도(예: 「월별 최근
    30일」)는 경계가 맞는 입도로 낮추고 사유를 남긴다. 요청이 없으면 경계 정렬과 정책 입도(cap)
    중 세밀한 쪽이다 — 월 입도는 구간 양 끝이 월 경계에 맞을 때만 나온다.
    """
    align = _alignment(start, end)
    if display != "none":
        requested = _GRAIN_OF[display]
        if _GRAIN_RANK[requested] <= _GRAIN_RANK[align]:
            return requested, ()
        return align, (NOTE_DISPLAY_GRAIN_UNALIGNED,)
    return _finer(align, cap), ()


def _infer_year(p: PartialDate, now: datetime) -> tuple[int, bool]:
    """PartialDate의 연도 — 절대·상대가 없으면 "시작이 기준 시각 이후가 아닌 가장 최근"(D-185)."""
    if p.year is not None:
        return p.year, False
    if p.year_offset is not None:
        return now.year + p.year_offset, False
    for back in range(0, 9):  # 2월 29일은 최대 8년 전까지 거슬러 윤년을 찾는다
        year = now.year - back
        try:
            start = _partial_datetime(p, year)
        except ValueError:
            continue
        if start <= now:
            return year, True
    raise TimeSpecError("year_unresolvable", str(p))


def _partial_datetime(p: PartialDate, year: int) -> datetime:
    """연도를 정한 PartialDate의 시작 시각(KST)."""
    return datetime(year, p.month or 1, p.day or 1, p.hour or 0, tzinfo=KST)


def _partial_start(p: PartialDate, now: datetime) -> tuple[datetime, bool]:
    year, inferred = _infer_year(p, now)
    try:
        return _partial_datetime(p, year), inferred
    except ValueError as exc:  # 윤년 아닌 해의 2월 29일(연도 명시)
        raise TimeSpecError("day_out_of_range", str(p)) from exc


def _between_bounds(
    a: PartialDate, b: PartialDate, now: datetime
) -> tuple[datetime, datetime, bool]:
    """between 양 끝 — 연도 규칙은 D-185(`_resolve_month_range_expr`)와 같다.

    끝 연도 미상: 시작 연도가 있으면 그 해(끝이 시작보다 앞서면 다음 해), 없으면 "미래가 아닌
    가장 최근". 시작 연도 미상: 끝 연도(시작이 끝보다 뒤면 전년 — 「11월~2월」). 뒤집히면 정렬한다.
    끝은 끝 부분값 단위의 다음 칸(반개구간)이다.
    """
    y1: int | None = None
    if a.has_year:
        y1, _ = _infer_year(a, now)
    if b.has_year:
        y2, _ = _infer_year(b, now)
    elif y1 is not None:
        y2 = y1 + 1 if _key(b) < _key(a) else y1
    else:
        y2, _ = _infer_year(b, now)
    if y1 is None:
        y1 = y2 - 1 if _key(a) > _key(b) else y2
    inferred = not a.has_year and not b.has_year
    try:
        s = _partial_datetime(a, y1)
        e0 = _partial_datetime(b, y2)
    except ValueError as exc:
        raise TimeSpecError("day_out_of_range", f"{a} ~ {b}") from exc
    if s <= e0:
        return s, _shift(e0, b.precision, 1), inferred
    return e0, _shift(s, a.precision, 1), inferred  # 뒤집힌 범위는 정렬한다(D-185 현행)


def _key(p: PartialDate) -> tuple[int, int, int]:
    return (p.month or 1, p.day or 1, p.hour or 0)


# ──────────────────────────────────────────────
# 해석기
# ──────────────────────────────────────────────


def default_resolution(now: datetime, *, subject: Subject = "metric") -> TimeResolution:
    """기간 없음 = 직전 완결 월(source=default · 고지 대상 — §10.3.1 마지막 행).

    사건(`subject="event"`)은 기간 조건 없음이다(D-291 알람 예외 — 모듈 머리말).
    """
    now = _to_kst(now)
    if _check_subject(subject) == "event":
        return TimeResolution(
            start=None, end=None, grain="month", completeness="complete", source="default",
            anchor_at=now, unbounded=True, notes=(NOTE_EVENT_NO_DEFAULT,),
        )
    cur = _floor(now, "month")
    return TimeResolution(
        start=_add_months(cur, -1), end=cur, grain="month", completeness="complete",
        source="default", anchor_at=now, notes=(NOTE_DEFAULT_PERIOD,),
    )


def _check_subject(subject: str) -> str:
    if subject not in SUBJECTS:
        raise TimeSpecError("invalid_subject", str(subject))
    return subject


def _explicit_year(*parts: PartialDate | None) -> bool:
    """사용자가 연도를 적은 부분값이 있는가(절대 연도 또는 상대 연도)."""
    return any(p is not None and p.has_year for p in parts)


def _reject_future_explicit(
    start: datetime | None, now: datetime, *parts: PartialDate | None
) -> None:
    """연도를 명시한 기간이 통째로 미래면 되묻기(D-291).

    연도 미상은 "미래가 아닌 가장 최근"으로 정하므로 해당 없다.
    """
    if start is not None and start > now and _explicit_year(*parts):
        raise TimeSpecError("future_explicit_period", start.isoformat())


def _this_complete_end(now: datetime, unit: str) -> datetime:
    """this + complete의 끝 — 연·반기·분기는 이번 달 1일(완결 월), 월·주는 오늘 0시, 일·시는 정시.
    """
    if unit in ("year", "half", "quarter"):
        return _floor(now, "month")
    if unit in ("month", "week"):
        return _floor(now, "day")
    return _floor(now, "hour")


def _to_date_end(now: datetime, unit: str) -> datetime:
    """to_date의 끝 — 일 이상 단위는 오늘 0시(어제까지), 일·시 단위는 현재 시각의 정시."""
    return _floor(now, "hour") if unit in ("hour", "day") else _floor(now, "day")


def _default_this_completeness(unit: str) -> Completeness:
    """this 기본 완결성 — 연·반기는 완결 월만(G-15 「올해」), 나머지는 어제·직전 정시까지."""
    return "complete" if unit in ("year", "half") else "to_date"


def _policy_cap(unit: str, completeness: str) -> Grain:
    """진행 중 단위를 자른 구간의 정책 입도(§10.3.1 표의 「테이블」 열).

    연·반기·분기의 완결 월 → 월, 롤링 → 시, 그 밖의 누적(to_date)은 일(일·시 단위는 시).
    경계 정렬이 우연히 더 굵게 맞아도(예: 8월 1일의 「이번 분기」 = 7월 한 달) 표의 입도를 쓴다.
    """
    if completeness == "rolling":
        return "hour"
    if completeness == "complete" and unit in ("year", "half", "quarter"):
        return "month"
    return "hour" if unit in ("hour", "day") else "day"


def resolve(spec: TimeSpec, now: datetime, *, subject: Subject = "metric") -> TimeResolution:
    """TimeSpec + 기준 시각 → TimeResolution(§10.3.1 정책 표 전 행).

    Args:
        spec: 규칙·LLM 슬롯이 만든 기간 명세
        now: 기준 시각(요청 수신 시각). naive면 KST로 본다
        subject: 해석 대상 — `metric`(성능 통계 · 기본) · `event`(알람 — D-291 예외)

    Raises:
        TimeSpecError: 명세가 해석 불가(연도 추론 실패 · 존재하지 않는 날짜 등) ·
            연도를 명시한 기간이 통째로 미래(`future_explicit_period` — 되묻기 · D-291)
    """
    now = _to_kst(now)
    event = _check_subject(subject) == "event"
    anchor_at = now
    if spec.anchor == "explicit":
        assert spec.abs_start is not None  # TimeSpec 검증이 보장
        anchor_at, _ = _partial_start(spec.abs_start, now)
    rel = spec.relation
    notes: list[str] = []
    start: datetime | None
    completeness: Completeness

    if rel == "none":
        if spec.unbounded:
            grain = _GRAIN_OF.get(spec.display_grain, "month")
            return TimeResolution(
                start=None, end=None, grain=grain, completeness="complete", source=spec.source,
                anchor_at=now, span=spec.span, unbounded=True,
            )
        if event:
            base_event = default_resolution(now, subject="event")
            return TimeResolution(
                start=None, end=None, grain=_GRAIN_OF.get(spec.display_grain, "month"),
                completeness="complete", source="default", anchor_at=now, span=spec.span,
                unbounded=True, notes=base_event.notes,
            )
        base = default_resolution(now)
        assert base.end is not None
        grain, gnotes = _select_grain(base.start, base.end, spec.display_grain, "month")
        return TimeResolution(
            start=base.start, end=base.end, grain=grain, completeness="complete",
            source="default", anchor_at=now, span=spec.span,
            notes=(NOTE_DEFAULT_PERIOD, *gnotes),
        )

    unit = spec.unit
    n = spec.n or 1
    if rel == "last":
        assert unit is not None
        completeness = spec.completeness or ("rolling" if unit == "hour" else "complete")
        cur = _floor(anchor_at, unit)
        if completeness == "complete":
            start, end = _shift(cur, unit, -n), cur
            cap = _UNIT_GRAIN[unit]
        elif completeness == "rolling":
            end = _floor(anchor_at, "hour")
            start = _shift(end, unit, -n)
            cap = "hour"
        else:  # to_date — 현재 칸을 포함한 N칸(현재 칸은 완결 경계까지)
            start, end = _shift(cur, unit, -(n - 1)), _to_date_end(anchor_at, unit)
            cap = _policy_cap(unit, completeness)
    elif rel in ("this", "to_date"):
        assert unit is not None
        default = "to_date" if rel == "to_date" else _default_this_completeness(unit)
        completeness = spec.completeness or default
        start = _floor(anchor_at, unit)
        if event:
            # 사건 예외(D-291): 진행 중 기간은 기준 시각까지 — 완결 월·어제까지 절단은 통계 사정이다
            completeness = "to_date"
            end = max(anchor_at.replace(microsecond=0), start)
            notes.append(NOTE_EVENT_TO_NOW)
            return _finish(spec, start, end, completeness, "hour", notes, now, anchor_at)
        if completeness == "complete":
            end = _this_complete_end(anchor_at, unit)
            if unit in ("year", "half", "quarter"):
                notes.append(NOTE_CURRENT_MONTH_EXCLUDED)
        elif completeness == "to_date":
            end = _to_date_end(anchor_at, unit)
        else:
            end = _floor(anchor_at, "hour")
        end = max(end, start)
        cap = _policy_cap(unit, completeness)
    elif rel == "next":
        assert unit is not None
        completeness = spec.completeness or "complete"
        start = _shift(_floor(anchor_at, unit), unit, 1)
        end = _shift(start, unit, n)
        cap = _UNIT_GRAIN[unit]
    elif rel == "ago":
        assert unit is not None
        completeness = spec.completeness or "complete"
        start = _shift(_floor(anchor_at, unit), unit, -n)
        end = _shift(start, unit, 1)
        cap = _UNIT_GRAIN[unit]
    elif rel == "absolute":
        assert spec.abs_start is not None
        return _resolve_absolute(spec, now, subject=subject)
    elif rel == "between":
        assert spec.abs_start is not None and spec.abs_end is not None
        start, end, inferred = _between_bounds(spec.abs_start, spec.abs_end, now)
        _reject_future_explicit(start, now, spec.abs_start, spec.abs_end)
        completeness = spec.completeness or "complete"
        cap = _finer(_UNIT_GRAIN[spec.abs_start.precision], _UNIT_GRAIN[spec.abs_end.precision])
        if inferred:
            notes.append(NOTE_YEAR_INFERRED)
        if start > now:
            notes.append(NOTE_FUTURE_PERIOD)
        elif end > _floor(now, "day"):
            notes.append(NOTE_PERIOD_IN_PROGRESS)
    elif rel == "since":
        assert spec.abs_start is not None
        start, inferred = _partial_start(spec.abs_start, now)
        _reject_future_explicit(start, now, spec.abs_start)
        completeness = spec.completeness or "to_date"
        precision = spec.abs_start.precision
        end = _floor(now, "hour") if precision == "hour" else _floor(now, "day")
        end = max(end, start)
        cap = _policy_cap(precision, completeness)
        if inferred:
            notes.append(NOTE_YEAR_INFERRED)
    else:  # until
        assert spec.abs_end is not None
        e_start, inferred = _partial_start(spec.abs_end, now)
        start, end = None, _shift(e_start, spec.abs_end.precision, 1)
        completeness = spec.completeness or "complete"
        cap = _UNIT_GRAIN[spec.abs_end.precision]
        if inferred:
            notes.append(NOTE_YEAR_INFERRED)

    return _finish(spec, start, end, completeness, cap, notes, now, anchor_at)


def _finish(
    spec: TimeSpec,
    start: datetime | None,
    end: datetime,
    completeness: Completeness,
    cap: Grain,
    notes: list[str],
    now: datetime,
    anchor_at: datetime,
) -> TimeResolution:
    grain, gnotes = _select_grain(start, end, spec.display_grain, cap)
    notes.extend(gnotes)
    if start is not None and start == end:
        notes.append(NOTE_EMPTY_RANGE)
    if (
        start is not None and start > now
        and NOTE_FUTURE_PERIOD not in notes
    ):
        notes.append(NOTE_FUTURE_PERIOD)
    return TimeResolution(
        start=start, end=end, grain=grain, completeness=completeness, source=spec.source,
        anchor_at=anchor_at, span=spec.span, notes=tuple(dict.fromkeys(notes)),
        anchor=spec.anchor,
    )


def _resolve_absolute(
    spec: TimeSpec, now: datetime, *, subject: Subject = "metric"
) -> TimeResolution:
    """달력 기간 하나(2026년 6월 · 3분기 · 상반기 · 9월 15일 · 2025년).

    진행 중이면(시작 ≤ 기준 시각 < 끝) 같은 단위의 `this`와 같게 자른다 — 월 → 「이번 달」
    (D-201 현행 동형), 분기 → 「이번 분기」, 연 → 「올해」, 일 → 「오늘」. 반기는 D-185 현행
    (「진행 중 반기 허용」)대로 달력 구간을 그대로 쓰고 `period_in_progress`를 단다.
    """
    assert spec.abs_start is not None
    unit: Unit = spec.unit or spec.abs_start.precision
    start, inferred = _partial_start(spec.abs_start, now)
    start = _floor(start, unit)
    end = _shift(start, unit, 1)
    _reject_future_explicit(start, now, spec.abs_start)
    notes: list[str] = [NOTE_YEAR_INFERRED] if inferred else []
    if start <= now < end and (unit != "half" or subject == "event"):
        clipped = TimeSpec(
            relation="this", unit=unit,
            display_grain=spec.display_grain, span=spec.span, span_pos=spec.span_pos,
            source=spec.source, completeness=spec.completeness,
        )
        res = resolve(clipped, now, subject=subject)
        return TimeResolution(
            start=res.start, end=res.end, grain=res.grain, completeness=res.completeness,
            source=res.source, anchor_at=res.anchor_at, span=res.span,
            notes=tuple(dict.fromkeys([*notes, NOTE_PERIOD_IN_PROGRESS, *res.notes])),
        )
    if start <= now < end:
        notes.append(NOTE_PERIOD_IN_PROGRESS)
    completeness = spec.completeness or "complete"
    return _finish(spec, start, end, completeness, _UNIT_GRAIN[unit], notes, now, now)


def cover(resolutions: Sequence[TimeResolution]) -> TimeResolution:
    """여러 해석 결과를 모두 덮는 최소 구간 — 비교 질의(「5월과 비교해서 6월」 등).

    구간이 떨어져 있어도 사이를 포함한다(데이터 필터의 상위 집합). 입도는 입력 중 가장 세밀한
    입도와 경계 정렬 중 세밀한 쪽이다. `multiple_periods` 고지를 단다.
    """
    if not resolutions:
        raise TimeSpecError("cover_empty")
    if len(resolutions) == 1:
        return resolutions[0]
    first = resolutions[0]
    if any(r.unbounded for r in resolutions):
        return TimeResolution(
            start=None, end=None, grain=first.grain, completeness="complete",
            source=first.source, anchor_at=first.anchor_at, unbounded=True,
            span=" · ".join(r.span for r in resolutions if r.span),
            notes=(NOTE_MULTIPLE_PERIODS,),
        )
    starts = [r.start for r in resolutions]
    start = None if any(s is None for s in starts) else min(s for s in starts if s is not None)
    end = max(r.end for r in resolutions if r.end is not None)
    cap: Grain = "month"
    for r in resolutions:
        cap = _finer(cap, r.grain)
    grain, gnotes = _select_grain(start, end, "none", cap)
    kinds = {r.completeness for r in resolutions}
    completeness: Completeness = (
        "to_date" if "to_date" in kinds else "rolling" if "rolling" in kinds else "complete"
    )
    sources = {r.source for r in resolutions}
    source: Source = first.source if len(sources) == 1 else ("llm" if "llm" in sources else "rule")
    notes = [n for r in resolutions for n in r.notes if n != NOTE_EMPTY_RANGE]
    return TimeResolution(
        start=start, end=end, grain=grain, completeness=completeness, source=source,
        anchor_at=first.anchor_at, span=" · ".join(r.span for r in resolutions if r.span),
        notes=tuple(dict.fromkeys([*notes, *gnotes, NOTE_MULTIPLE_PERIODS])),
    )


# ──────────────────────────────────────────────
# 소비처용 원시 연산 (121 사건 창 · 122 하네스)
# ──────────────────────────────────────────────


def _ceil(dt: datetime, grain: str) -> datetime:
    floored = _floor(dt, grain)
    return floored if floored == dt else _shift(floored, grain, 1)


def window_around(
    center: datetime, before: timedelta, after: timedelta, grain: str
) -> TimeResolution:
    """사건 기준 창 `[center − before, center + after)`(anchor=explicit · `plans/121` TP-3.1).

    알람 발생 시각 전후처럼 요청 시각이 아닌 명시 기준의 창을 만든다. 양 끝은 grain 경계로
    **바깥쪽** 맞춤한다(시작 내림 · 끝 올림 — 통계 입도의 리터럴 경계가 창을 덮도록).
    Prometheus step·rate 창은 121 TP-10.6 몫이라 여기서 만들지 않는다.
    """
    if grain not in GRAINS:
        raise TimeSpecError("invalid_grain", grain)
    if before < timedelta(0) or after < timedelta(0):
        raise TimeSpecError("negative_window")
    center = _to_kst(center)
    start = _floor(center - before, grain)
    end = _ceil(center + after, grain)
    return TimeResolution(
        start=start, end=end, grain=_GRAIN_OF[grain],
        completeness="rolling", source="rule", anchor_at=center, anchor="explicit",
    )


RELATIVE_WINDOW_KINDS: frozenset[str] = frozenset(
    {"last_month", "this_month", "last_n_months", "month_span"}
)


def relative_window(
    kind: str,
    anchor: datetime,
    *,
    n: int | None = None,
    month_from: int | None = None,
    month_to: int | None = None,
) -> tuple[date, date]:
    """하네스용 상대 기간 `[시작일, 끝일)`(plans/122 H-2 `period_covers.relative` · O-2 자리표).

    kind = last_month · this_month · last_n_months(n) · month_span(month_from, month_to).
    내부에서 `resolve`를 부른다 — 제품과 하네스가 같은 정책 표를 쓴다(단일 출처).
    `anchor`는 턴 송신 시각(KST)이다.
    """
    if kind == "last_month":
        spec = TimeSpec(relation="last", n=1, unit="month")
    elif kind == "this_month":
        spec = TimeSpec(relation="this", unit="month")
    elif kind == "last_n_months":
        if n is None:
            raise TimeSpecError("n_missing", kind)
        spec = TimeSpec(relation="last", n=n, unit="month")
    elif kind == "month_span":
        if month_from is None or month_to is None:
            raise TimeSpecError("month_span_missing", kind)
        spec = TimeSpec(
            relation="between",
            abs_start=PartialDate(month=month_from),
            abs_end=PartialDate(month=month_to),
        )
    else:
        raise TimeSpecError("invalid_kind", kind)
    res = resolve(spec, anchor)
    assert res.start is not None and res.end is not None
    return res.start.date(), res.end.date()


__all__ = [
    "ANCHORS", "COMPLETENESS", "DISPLAY_GRAINS", "GRAINS", "KST", "N_MAX",
    "NOTE_CURRENT_MONTH_EXCLUDED", "NOTE_DEFAULT_PERIOD", "NOTE_DISPLAY_GRAIN_UNALIGNED",
    "NOTE_EMPTY_RANGE", "NOTE_EVENT_NO_DEFAULT", "NOTE_EVENT_TO_NOW", "NOTE_FUTURE_PERIOD",
    "NOTE_MULTIPLE_PERIODS", "NOTE_PERIOD_IN_PROGRESS", "NOTE_YEAR_INFERRED", "RELATIONS",
    "RELATIVE_WINDOW_KINDS", "SOURCES", "SUBJECTS", "UNITS",
    "Anchor", "Completeness", "DisplayGrain", "Grain", "PartialDate", "Relation", "Source",
    "Subject", "TimeResolution", "TimeSpec", "TimeSpecError", "Unit",
    "cover", "default_resolution", "relative_window", "resolve", "window_around",
]
