"""한국어 시간 표현 규칙 인식기 + LLM 슬롯 검증 (plans/122 T-2 · T-3 순수 부분 · D-275 ⑪).

「LLM은 슬롯, 코드는 계산」(plans/122 §10.3)의 **입력** 쪽이다.

- `recognize(text)` — 결정적 한국어 규칙으로 원문의 기간·입도·「~한 적이 있는」 표현을 찾아
  `TimeSpec`(source=rule · 원문 스팬 포함) 목록을 만든다. 날짜 계산은 하지 않는다
  (기준 시각이 없어도 명세를 만들 수 있게 상대 연도는 `PartialDate.year_offset`으로 둔다)
- `slot_to_spec(slot, text)` — input_parser LLM이 뽑은 시간 슬롯(enum JSON)을 검증해 `TimeSpec`
  (source=llm)으로 바꾼다. 스팬이 원문에 없으면 폐기한다(환각 기간 차단)
- `interpret(text, now)` — 규칙만으로 원문 → `TimeResolution`(골드 채점 · 섀도 비교용 합성).
  기간 표현 흔적이 있는데 규칙이 못 잡으면 None(LLM 슬롯 또는 되묻기 대상 — 침묵 기본값 금지)
- `interpret_detail(text, now)` — 같은 해석 + **되묻기 사유 코드**(D-291):
  `invalid_date`(존재하지 않는 월·날짜) · `future_explicit`(연도를 명시한 미래 기간) ·
  `future_relative`(「내년」 등 규칙이 못 잡은
  미래 어휘) · `unresolved`(그 밖의 못 잡은 기간 흔적) · `invalid_spec`(해석기가 거부한 명세)

규칙 설계 — 기존 정규식(`src/utils/query_gen_common.py` D-185 · D-102)의 오탐 차단을 지킨다:

- 「1-6」처럼 '월' 접미도 연도도 없는 숫자 범위는 월 범위가 아니다
- 「3개월」의 '월'은 달력 월이 아니다(숫자 바로 뒤 '월'만 달력 월)
- 「지난주」(달력 주)와 「지난 N주」(완결 N×7일)는 어휘로 가른다. 「지난 N년」은 완결 12N개월이고
  「작년」은 달력 연이다(plans/122 T-2 구현 시 정함)

겹치는 스팬 처리: 아래 `_RULES`의 **우선순위 순서**로 매칭하고, 이미 채택된 스팬과 한 글자라도
겹치는 후순위 매칭은 버린다. 그래서 더 구체적인 표현(범위 > 날짜 > 반기·분기 > 연월 > 상대 N >
고정 어휘 > 연 > 입도 > 「적이 있는」)이 이긴다 — 「2026년 1월부터 6월까지」는 범위 하나이고,
「지난 3개월간」의 '월간'은 입도로 다시 잡히지 않는다. 결과는 원문 위치 순이다.

domain 계층 — 표준 라이브러리와 `src.domain.time_spec`만 의존한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, cast

from src.domain.time_spec import (
    ANCHORS,
    COMPLETENESS,
    DISPLAY_GRAINS,
    RELATIONS,
    UNITS,
    Anchor,
    Completeness,
    DisplayGrain,
    PartialDate,
    Relation,
    Subject,
    TimeResolution,
    TimeSpec,
    TimeSpecError,
    Unit,
    cover,
    default_resolution,
    resolve,
)

# ──────────────────────────────────────────────
# 공통 조각
# ──────────────────────────────────────────────

_REL_YEAR = r"올해|(?<![가-힣])금년|(?<![가-힣])당해|작년|(?<![가-힣])전년|지난\s*해(?!당)"
_REL_YEAR_OFFSET: dict[str, int] = {
    "올해": 0, "금년": 0, "당해": 0, "작년": -1, "전년": -1, "지난해": -1,
}
_KO_NUMERALS: dict[str, int] = {
    "한": 1, "하나": 1, "두": 2, "세": 3, "석": 3, "네": 4, "넉": 4, "다섯": 5, "여섯": 6,
    "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12, "스물네": 24,
}
# 긴 어휘가 먼저 오도록(「열한」이 「열」보다 먼저)
_KO_NUM = "|".join(sorted(_KO_NUMERALS, key=len, reverse=True))
_NUM = rf"(?P<num>\d{{1,4}}|{_KO_NUM})"
_UNIT = r"(?P<unit>개\s*월|달|주일|주|일|시간|년|months?|weeks?|days?|hours?|years?)"
_SUFFIX = r"(?P<suf>\s*(?:간|동안|치))"
_PREFIX = r"(?P<pre>지난|최근|과거|직전|last|past)"
# 한글 수사는 이 단위와만 어울린다(「세 달」은 되고 「세 일」은 안 된다)
_KO_NUM_UNITS: frozenset[str] = frozenset({"month", "week", "hour"})
_RANGE_SEP = r"(?:부터|에서|~|∼|〜|-|–|—)"


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _rel_offset(raw: str | None) -> int | None:
    return None if raw is None else _REL_YEAR_OFFSET[_compact(raw)]


def _num(raw: str) -> int:
    return int(raw) if raw.isdigit() else _KO_NUMERALS[raw]


def _unit_of(raw: str) -> str:
    r = _compact(raw).lower()
    if r in ("개월", "달") or r.startswith("month"):
        return "month"
    if r in ("주", "주일") or r.startswith("week"):
        return "week"
    if r == "일" or r.startswith("day"):
        return "day"
    if r == "시간" or r.startswith("hour"):
        return "hour"
    return "year"


def _partial(
    year: str | None = None,
    rel: str | None = None,
    month: str | int | None = None,
    day: str | None = None,
) -> PartialDate:
    return PartialDate(
        year=int(year) if year else None,
        year_offset=_rel_offset(rel),
        month=int(month) if month is not None else None,
        day=int(day) if day else None,
    )


# ──────────────────────────────────────────────
# 규칙 (우선순위 순)
# ──────────────────────────────────────────────

_Built = tuple[TimeSpec, int]  # (명세 — 스팬 미기입, 스팬 끝 위치)
_Builder = Callable[[re.Match[str], str], _Built | None]


@dataclass(frozen=True)
class _Rule:
    name: str
    pattern: re.Pattern[str]
    build: _Builder


_SINCE_RE = re.compile(r"\s*(?:부터|이후)")
_UNTIL_RE = re.compile(r"\s*까지")


def _absolute_or_open(
    m: re.Match[str], text: str, start: PartialDate, unit: Unit | None = None,
    last: PartialDate | None = None,
) -> _Built:
    """달력 기간 하나. 바로 뒤에 「부터·이후」가 붙으면 since, 「까지」가 붙으면 until로 바꾼다."""
    tail = text[m.end():]
    since = _SINCE_RE.match(tail)
    if since:
        return TimeSpec(relation="since", abs_start=start), m.end() + since.end()
    until = _UNTIL_RE.match(tail)
    if until:
        return TimeSpec(relation="until", abs_end=last or start), m.end() + until.end()
    return TimeSpec(relation="absolute", unit=unit, abs_start=start), m.end()


def _b_iso_day_range(m: re.Match[str], text: str) -> _Built:
    g = m.groupdict()
    return TimeSpec(
        relation="between",
        abs_start=_partial(g["y1"], None, g["m1"], g["d1"]),
        abs_end=_partial(g["y2"], None, g["m2"], g["d2"]),
    ), m.end()


def _b_day_range(m: re.Match[str], text: str) -> _Built:
    g = m.groupdict()
    start = _partial(g["y1"], g["r1"], g["m1"], g["d1"])
    if g["m2"] is None:  # 「9월 1일~15일」 — 끝은 시작과 같은 연·월
        end = _partial(g["y1"], g["r1"], g["m1"], g["d2"])
    else:
        end = _partial(g["y2"], g["r2"], g["m2"], g["d2"])
    return TimeSpec(relation="between", abs_start=start, abs_end=end), m.end()


def _b_month_range(m: re.Match[str], text: str) -> _Built | None:
    g = m.groupdict()
    # D-185 오탐 차단 — 각 끝점은 '월' 접미 또는 연도를 가져야 한다(「1-6」 무매칭)
    if not (g["w1"] or g["y1"] or g["r1"]) or not (g["w2"] or g["y2"] or g["r2"]):
        return None
    start = _partial(g["y1"], g["r1"], g["m1"])
    end = _partial(g["y2"], g["r2"], g["m2"])
    return TimeSpec(relation="between", abs_start=start, abs_end=end), m.end()


def _b_iso_day(m: re.Match[str], text: str) -> _Built:
    g = m.groupdict()
    return _absolute_or_open(m, text, _partial(g["y"], None, g["m"], g["d"]))


def _b_day(m: re.Match[str], text: str) -> _Built:
    g = m.groupdict()
    return _absolute_or_open(m, text, _partial(g["y"], g["r"], g["m"], g["d"]))


def _b_half(m: re.Match[str], text: str) -> _Built:
    g = m.groupdict()
    first, last = (1, 6) if g["half"] == "상반기" else (7, 12)
    return _absolute_or_open(
        m, text, _partial(g["y"], g["r"], first), "half", _partial(g["y"], g["r"], last)
    )


def _b_quarter(m: re.Match[str], text: str) -> _Built:
    g = m.groupdict()
    first = (int(g["q"]) - 1) * 3 + 1
    return _absolute_or_open(
        m, text, _partial(g["y"], g["r"], first), "quarter", _partial(g["y"], g["r"], first + 2)
    )


def _b_year_month(m: re.Match[str], text: str) -> _Built:
    g = m.groupdict()
    month = g["m1"] or g["m2"]
    return _absolute_or_open(m, text, _partial(g["y"], None, month))


def _b_rel_year_month(m: re.Match[str], text: str) -> _Built:
    g = m.groupdict()
    return _absolute_or_open(m, text, _partial(None, g["r"], g["m"]))


def _b_month(m: re.Match[str], text: str) -> _Built:
    return _absolute_or_open(m, text, _partial(month=m.group("m")))


def _b_year(m: re.Match[str], text: str) -> _Built:
    return _absolute_or_open(m, text, _partial(m.group("y")))


def _last_n(n: int, unit: str) -> TimeSpec:
    """「지난·최근 N 단위」 → 명세. 주는 완결 N×7일, 연은 완결 12N개월(어휘 결정 — 모듈 머리말)."""
    if unit == "week":
        return TimeSpec(relation="last", n=n * 7, unit="day")
    if unit == "year":
        return TimeSpec(relation="last", n=n * 12, unit="month")
    if unit == "hour":
        return TimeSpec(relation="last", n=n, unit="hour", completeness="rolling")
    return TimeSpec(relation="last", n=n, unit=cast(Unit, unit))


def _b_last_n(m: re.Match[str], text: str) -> _Built | None:
    raw = m.group("num")
    unit = _unit_of(m.group("unit"))
    if not raw.isdigit() and unit not in _KO_NUM_UNITS:
        return None
    return _last_n(_num(raw), unit), m.end()


_DAY_WORDS: dict[str, int] = {"일주일": 7, "하루": 1, "이틀": 2, "사흘": 3, "나흘": 4}


def _b_last_day_word(m: re.Match[str], text: str) -> _Built:
    return TimeSpec(relation="last", n=_DAY_WORDS[m.group("word")], unit="day"), m.end()


def _b_half_year(m: re.Match[str], text: str) -> _Built:
    # 반년 = 지난 6개월(상·하반기와 구분 — §10.3.1 · D-185)
    return TimeSpec(relation="last", n=6, unit="month"), m.end()


def _fixed(spec: TimeSpec) -> _Builder:
    def build(m: re.Match[str], text: str) -> _Built:
        return spec, m.end()
    return build


def _grain(value: DisplayGrain) -> _Builder:
    return _fixed(TimeSpec(relation="none", display_grain=value))


_UNBOUNDED_SPEC = TimeSpec(relation="none", unbounded=True)

_RULES: tuple[_Rule, ...] = (
    _Rule("iso_day_range", re.compile(
        r"(?<![\d-])(?P<y1>\d{4})-(?P<m1>\d{1,2})-(?P<d1>\d{1,2})\s*(?:~|∼|〜|부터|에서)\s*"
        r"(?P<y2>\d{4})-(?P<m2>\d{1,2})-(?P<d2>\d{1,2})(?!\d)(?:\s*까지)?"
    ), _b_iso_day_range),
    _Rule("day_range", re.compile(
        rf"(?:(?P<y1>\d{{4}})\s*년\s*|(?P<r1>{_REL_YEAR})\s*)?(?<!\d)(?P<m1>\d{{1,2}})\s*월\s*"
        rf"(?P<d1>\d{{1,2}})\s*일\s*{_RANGE_SEP}\s*"
        rf"(?:(?:(?P<y2>\d{{4}})\s*년\s*|(?P<r2>{_REL_YEAR})\s*)?(?P<m2>\d{{1,2}})\s*월\s*)?"
        r"(?P<d2>\d{1,2})\s*일(?:\s*까지)?"
    ), _b_day_range),
    _Rule("month_range", re.compile(  # D-185 _MONTH_RANGE_RE + 상대 연도
        rf"(?:(?P<y1>\d{{4}})\s*(?:년\s*|[-/.])|(?P<r1>{_REL_YEAR})\s*)?"
        r"(?<![\d.])(?P<m1>\d{1,2})\s*(?P<w1>월)?\s*"
        rf"{_RANGE_SEP}\s*"
        rf"(?:(?P<y2>\d{{4}})\s*(?:년\s*|[-/.])|(?P<r2>{_REL_YEAR})\s*)?"
        r"(?<![\d.])(?P<m2>\d{1,2})\s*(?P<w2>월)?(?!\s*\d*\s*(?:개|일))(?:\s*까지)?"
    ), _b_month_range),
    _Rule("iso_day", re.compile(
        r"(?<![\d-])(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})(?![\d-])"
    ), _b_iso_day),
    _Rule("day", re.compile(
        rf"(?:(?P<y>\d{{4}})\s*년\s*|(?P<r>{_REL_YEAR})\s*)?(?<!\d)(?P<m>\d{{1,2}})\s*월\s*"
        r"(?P<d>\d{1,2})\s*일"
    ), _b_day),
    _Rule("half", re.compile(
        rf"(?:(?P<y>\d{{4}})\s*년\s*|(?P<r>{_REL_YEAR})\s*)?(?P<half>상반기|하반기)"
    ), _b_half),
    _Rule("quarter", re.compile(
        rf"(?:(?P<y>\d{{4}})\s*년\s*|(?P<r>{_REL_YEAR})\s*)?(?<![\d./])(?P<q>[1-4])\s*분기"
    ), _b_quarter),
    _Rule("year_month", re.compile(
        r"(?<!\d)(?P<y>\d{4})\s*(?:년\s*(?P<m1>\d{1,2})\s*월|[-/]\s*(?P<m2>\d{1,2})(?![\d]|\s*[-/]\s*\d))"
    ), _b_year_month),
    _Rule("rel_year_month", re.compile(
        rf"(?P<r>{_REL_YEAR})\s*(?P<m>\d{{1,2}})\s*월"
    ), _b_rel_year_month),
    _Rule("month", re.compile(
        r"(?<![\d.\-/])(?P<m>\d{1,2})\s*월"
    ), _b_month),
    _Rule("last_n", re.compile(rf"{_PREFIX}\s*{_NUM}\s*{_UNIT}{_SUFFIX}?", re.IGNORECASE),
          _b_last_n),
    _Rule("n_suffixed", re.compile(
        rf"(?<![가-힣0-9A-Za-z]){_NUM}\s*{_UNIT}{_SUFFIX}"
    ), _b_last_n),
    _Rule("last_day_word", re.compile(
        r"(?P<pre>지난|최근|과거|직전)\s*(?P<word>일주일|하루|이틀|사흘|나흘)(?:\s*(?:간|동안|치))?"
    ), _b_last_day_word),
    _Rule("half_year", re.compile(
        r"(?:(?:지난|최근|과거|직전)\s*)?반\s*년(?:\s*(?:간|동안|치))?"
    ), _b_half_year),
    _Rule("two_quarters_ago", re.compile(r"지지난\s*분기"),
          _fixed(TimeSpec(relation="ago", n=2, unit="quarter"))),
    _Rule("last_quarter", re.compile(r"(?:지난|저번|직전|전)\s*분기"),
          _fixed(TimeSpec(relation="last", n=1, unit="quarter"))),
    _Rule("this_quarter", re.compile(r"이번\s*분기|(?<![가-힣])(?:당|금)\s*분기"),
          _fixed(TimeSpec(relation="this", unit="quarter"))),
    # 두 칸 전 — 「지난달」보다 먼저 잡아야 「지지난달」 안의 「지난달」로 오해석되지 않는다
    _Rule("two_months_ago", re.compile(r"지지난\s*달|(?<![가-힣])전전\s*월|(?<![가-힣])전전\s*달"),
          _fixed(TimeSpec(relation="ago", n=2, unit="month"))),
    _Rule("two_weeks_ago", re.compile(r"지지난\s*주(?![일말간])"),
          _fixed(TimeSpec(relation="ago", n=2, unit="week"))),
    _Rule("two_years_ago", re.compile(r"재작년"),
          _fixed(TimeSpec(relation="ago", n=2, unit="year"))),
    _Rule("last_month", re.compile(
        r"지난\s*달|저번\s*달|(?<![가-힣])전월|last\s+month|previous\s+month", re.IGNORECASE
    ), _fixed(TimeSpec(relation="last", n=1, unit="month"))),
    _Rule("this_month", re.compile(
        r"이번\s*달|요번\s*달|(?<![가-힣])(?:당월|금월|이달)(?![라리려력])|this\s+month"
        r"|current\s+month", re.IGNORECASE
    ), _fixed(TimeSpec(relation="this", unit="month"))),
    _Rule("last_week", re.compile(
        r"지난\s*주(?![일말간])|저번\s*주(?![일말간])|last\s+week", re.IGNORECASE
    ), _fixed(TimeSpec(relation="last", n=1, unit="week"))),
    _Rule("this_week", re.compile(r"(?:이번|요번)\s*주(?![일말간])|this\s+week", re.IGNORECASE),
          _fixed(TimeSpec(relation="this", unit="week"))),
    _Rule("day_before_yesterday", re.compile(r"그저께|엊그제|그제(?![서야])"),
          _fixed(TimeSpec(relation="ago", n=2, unit="day"))),
    _Rule("yesterday", re.compile(r"어제|(?<![가-힣])(?:전일|작일)|yesterday", re.IGNORECASE),
          _fixed(TimeSpec(relation="last", n=1, unit="day"))),
    _Rule("today", re.compile(r"오늘(?!날)|(?<![가-힣])금일|today", re.IGNORECASE),
          _fixed(TimeSpec(relation="this", unit="day"))),
    _Rule("last_year", re.compile(r"작년|지난\s*해(?!당)|(?<![가-힣])전년|last\s+year",
                                  re.IGNORECASE),
          _fixed(TimeSpec(relation="last", n=1, unit="year"))),
    _Rule("this_year", re.compile(r"올해|(?<![가-힣])금년|this\s+year", re.IGNORECASE),
          _fixed(TimeSpec(relation="this", unit="year"))),
    _Rule("year", re.compile(r"(?<!\d)(?P<y>\d{4})\s*년(?:도)?(?!\s*\d)"), _b_year),
    _Rule("grain_hour", re.compile(r"시간\s*단위|시간대별|시간별|매\s*시간|시간당|hourly",
                                   re.IGNORECASE), _grain("hour")),
    _Rule("grain_day", re.compile(r"일\s*단위|일자별|날짜별|일별|일간|일\s*평균|daily",
                                  re.IGNORECASE), _grain("day")),
    _Rule("grain_month", re.compile(r"월\s*단위|월별|월간|월\s*평균|monthly", re.IGNORECASE),
          _grain("month")),
    _Rule("unbounded", re.compile(
        r"[한된난은던]\s*적\s*(?:이|은|도)?\s*(?:있|없)|한\s*번도|지금까지|이제까지|여태(?:까지)?"
    ), _fixed(_UNBOUNDED_SPEC)),
)


def _overlaps(claimed: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < e and s < end for s, e in claimed)


#: 값 범위 밖이라 **존재하지 않는** 날짜 표현의 명세 거부 코드(「13월」 · 「2월 30일」 ·
#: 윤년 아닌 해의
#: 「2월 29일」). 이 스팬은 채택된 것처럼 막아 후순위 규칙이 안쪽 「2월」만 떼어 가지 못하게 하고
#: 되묻기 사유 `invalid_date`로 올린다(D-291 — 종전에는 「2월 30일」이 조용히 2월 한 달이 됐다).
_INVALID_VALUE_CODES: frozenset[str] = frozenset(
    {"month_out_of_range", "day_out_of_range", "hour_out_of_range", "year_out_of_range"}
)


def recognize(text: str) -> list[TimeSpec]:
    """원문에서 시간 표현을 찾아 `TimeSpec` 목록(source=rule · 원문 위치 순)을 돌려준다.

    기간(relation != none) 외에 입도 표현(「시간 단위」 — relation=none · display_grain)과
    「~한 적이 있는」(relation=none · unbounded)도 따로 돌려준다. 합치는 것은 `interpret`의 몫이다.
    값이 범위를 벗어난 표현(「13월」)은 명세를 만들지 않는다 — `interpret_detail`이
    `invalid_date`로 본다.
    """
    return recognize_detail(text)[0]


def recognize_detail(text: str) -> tuple[list[TimeSpec], list[tuple[int, int, str]]]:
    """`recognize` + 존재하지 않는 날짜 표현의 스팬 목록 `[(시작, 끝, 거부 코드)]`."""
    claimed: list[tuple[int, int]] = []
    found: list[TimeSpec] = []
    invalid: list[tuple[int, int, str]] = []
    for rule in _RULES:
        for m in rule.pattern.finditer(text):
            if _overlaps(claimed, m.start(), m.end()):
                continue
            try:
                built = rule.build(m, text)
            except TimeSpecError as exc:
                if exc.code in _INVALID_VALUE_CODES:
                    claimed.append((m.start(), m.end()))
                    invalid.append((m.start(), m.end(), exc.code))
                continue  # 그 밖의 거부는 종전대로 흔적으로 남긴다
            if built is None:
                continue
            spec, end = built
            if _overlaps(claimed, m.start(), end):
                continue
            claimed.append((m.start(), end))
            found.append(replace(spec, span=text[m.start():end], span_pos=(m.start(), end)))
    found.sort(key=lambda s: s.span_pos[0] if s.span_pos else -1)
    return found, invalid


# ──────────────────────────────────────────────
# 규칙 합성 — 원문 → TimeResolution
# ──────────────────────────────────────────────

# 규칙이 못 잡은 기간 표현의 흔적(숫자 + 시간 단위 · 미래 어휘). 「2개월 이상」·「5년 이상」처럼
# 수량 비교는 기간이 아니므로 뺀다. 한글 수사는 앞 글자가 한글이면 보지 않는다(「초과한 달」).
_TRACE_RE = re.compile(
    r"(?<![\d.])\d+\s*(?:개\s*월|달|월|일|주일|주|시간|년|분기|반기|시)"
    r"(?!\s*(?:이상|이하|미만|초과|넘|째|번째|차))"
    rf"|(?<![가-힣])(?:{_KO_NUM})\s*(?:개\s*월|달|주일|주|시간)(?!\s*(?:이상|이하|미만|초과|넘))"
    r"|내일|모레|다음\s*(?:달|주|분기|해)|내년|익월|차주|그끄저께"
    r"|(?:요번|지지난|다다음|전전)\s*(?:달|월|주|분기|해|년)"
)


# 규칙이 못 잡은 **미래** 어휘(`_TRACE_RE`의 미래 부분). 기간 표현이 따로 잡혀도 남아 있으면
# 되묻는다 — 「내년 3월」이 「3월」만 잡혀 조용히 올해 3월이 되던 것(D-291 · 미래 기간 = 되묻기).
_FUTURE_TRACE_RE = re.compile(
    r"내일|모레|다음\s*(?:달|주|분기|해)|내년|익월|차주|(?:다다음)\s*(?:달|월|주|분기|해|년)"
)

#: 되묻기 사유 코드(`interpret_detail` · D-291).
CLARIFY_INVALID_DATE = "invalid_date"
CLARIFY_FUTURE_EXPLICIT = "future_explicit"
CLARIFY_FUTURE_RELATIVE = "future_relative"
CLARIFY_UNRESOLVED = "unresolved"
CLARIFY_INVALID_SPEC = "invalid_spec"
CLARIFY_CODES: frozenset[str] = frozenset({
    CLARIFY_INVALID_DATE, CLARIFY_FUTURE_EXPLICIT, CLARIFY_FUTURE_RELATIVE,
    CLARIFY_UNRESOLVED, CLARIFY_INVALID_SPEC,
})


@dataclass(frozen=True)
class Interpretation:
    """`interpret_detail` 결과 — 해석(또는 None)과 되묻기 사유 코드(해석되면 None)."""

    resolution: TimeResolution | None
    clarify: str | None = None


def _masked(text: str, specs: list[TimeSpec]) -> str:
    chars = list(text)
    for spec in specs:
        if spec.span_pos:
            s, e = spec.span_pos
            chars[s:e] = " " * (e - s)
    return "".join(chars)


def _residual_trace(text: str, specs: list[TimeSpec]) -> str | None:
    """인식된 스팬을 가린 원문에 남은 기간 표현 흔적(없으면 None)."""
    chars = list(text)
    for spec in specs:
        if spec.span_pos:
            s, e = spec.span_pos
            chars[s:e] = " " * (e - s)
    m = _TRACE_RE.search("".join(chars))
    return m.group(0) if m else None


def interpret(text: str, now: datetime, *, subject: Subject = "metric") -> TimeResolution | None:
    """규칙만으로 원문을 해석한다(골드 채점 · 섀도 비교용 — plans/122 T-0·T-4).

    - 기간 표현이 하나면 그것, 여럿이면(비교 질의) 모두를 덮는 구간(`cover`)
    - 입도 표현(「시간 단위」)은 모든 기간에 적용한다
    - 기간이 없고 「~한 적이 있는」이 있으면 기간 조건 없음(unbounded)
    - 기간이 없고 규칙이 못 잡은 흔적(「13월」·「3일 전」)이 있으면 **None** — LLM 슬롯 또는
      되묻기 대상이다(§10.3 「해석 불가」 · 침묵 기본값 금지)
    - 그 밖에는 기본값(직전 완결 월 · source=default · 고지 대상)
    - 해석기가 명세를 거부하면(존재하지 않는 날짜 등) None — 해석 불가와 같게 다룬다
    - 연도를 명시한 미래 기간 · 존재하지 않는 월·날짜 · 「내년」 같은 미래 어휘도 None
      (되묻기 · D-291)
    - `subject="event"`(알람)는 사건 예외를 쓴다(`time_spec` 머리말 · D-291)
    """
    return interpret_detail(text, now, subject=subject).resolution


def interpret_detail(text: str, now: datetime, *, subject: Subject = "metric") -> Interpretation:
    """`interpret` + 되묻기 사유 코드(`CLARIFY_*`) — 호출부가 되묻기 문구를 고른다."""
    try:
        return _interpret(text, now, subject)
    except TimeSpecError as exc:
        code = (CLARIFY_FUTURE_EXPLICIT if exc.code == "future_explicit_period"
                else CLARIFY_INVALID_DATE if exc.code in _INVALID_VALUE_CODES
                else CLARIFY_INVALID_SPEC)
        return Interpretation(None, code)


def _interpret(text: str, now: datetime, subject: Subject) -> Interpretation:
    specs, invalid = recognize_detail(text)
    if invalid:
        return Interpretation(None, CLARIFY_INVALID_DATE)
    periods = [s for s in specs if s.relation != "none"]
    grains = [s.display_grain for s in specs if s.display_grain != "none"]
    # 입도 표현이 여럿이면 가장 세밀한 것(시 > 일 > 월)
    display: DisplayGrain = (
        min(grains, key=lambda g: {"hour": 0, "day": 1, "month": 2}[g]) if grains else "none"
    )
    if _FUTURE_TRACE_RE.search(_masked(text, specs)):
        return Interpretation(None, CLARIFY_FUTURE_RELATIVE)
    if not periods:
        if _residual_trace(text, specs):
            return Interpretation(None, CLARIFY_UNRESOLVED)
        unbounded = next((s for s in specs if s.unbounded), None)
        if unbounded is not None:
            return Interpretation(
                resolve(replace(unbounded, display_grain=display), now, subject=subject))
        grain_spec = next((s for s in specs if s.display_grain != "none"), None)
        if grain_spec is not None:
            return Interpretation(
                resolve(replace(grain_spec, display_grain=display), now, subject=subject))
        return Interpretation(default_resolution(now, subject=subject))
    resolved = [
        resolve(replace(p, display_grain=display), now, subject=subject) for p in periods
    ]
    return Interpretation(cover(resolved))


# ──────────────────────────────────────────────
# LLM 슬롯 검증 (T-3 순수 부분)
# ──────────────────────────────────────────────

#: LLM 슬롯 스키마(§10.3 「LLM의 역할」) — enum JSON. 날짜를 계산하지 않고 원문 스팬을
#: 그대로 싣는다. `start`·`end`는 {year?, year_offset?, month?, day?, hour?} —
#: 「작년 6월」은 year_offset=-1(계산 금지).
SLOT_KEYS: tuple[str, ...] = (
    "relation", "n", "unit", "completeness", "anchor", "start", "end", "display_grain", "span",
)
_PARTIAL_KEYS: tuple[str, ...] = ("year", "year_offset", "month", "day", "hour")


def _locate(span: str, text: str) -> tuple[int, int] | None:
    """스팬의 원문 위치 — 그대로 없으면 공백·대소문자 차이만 무시하고 찾는다."""
    idx = text.find(span)
    if idx >= 0:
        return idx, idx + len(span)
    target = _compact(span).casefold()
    if not target:
        return None
    positions = [i for i, ch in enumerate(text) if not ch.isspace()]
    compact_text = "".join(text[i] for i in positions).casefold()
    j = compact_text.find(target)
    if j < 0:
        return None
    return positions[j], positions[j + len(target) - 1] + 1


def _span_numbers(span: str) -> tuple[set[int], set[int]]:
    """스팬 안의 수 — (모든 수, 네 자리 연도). 한글 수사는 시간 단위 앞에 올 때만 센다."""
    nums = {int(x) for x in re.findall(r"\d+", span)}
    years = {x for x in nums if x >= 1000}
    for word in re.findall(rf"(?<![가-힣])({_KO_NUM})\s*(?:개\s*월|달|주|시간)", span):
        nums.add(_KO_NUMERALS[word])
    return nums, years


def _opt_int(value: Any, code: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TimeSpecError(code, repr(value))
    return value


def _opt_enum(value: Any, allowed: frozenset[str], code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise TimeSpecError(code, repr(value))
    return value


def _slot_partial(value: Any) -> PartialDate | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TimeSpecError("invalid_partial_date", repr(value))
    fields = {k: _opt_int(value.get(k), "invalid_partial_date") for k in _PARTIAL_KEYS}
    if all(v is None for v in fields.values()):
        return None
    return PartialDate(**fields)


def _check_span_values(
    relation: str, n: int | None, parts: list[PartialDate], span: str
) -> None:
    """슬롯 값이 스팬의 수와 맞는가(환각·계산 차단). 스팬에 수가 없으면 검사하지 않는다."""
    nums, years = _span_numbers(span)
    if relation in ("last", "next", "ago") and n is not None and nums:
        derived = nums | {x * 7 for x in nums} | {x * 12 for x in nums}
        if n not in derived:
            raise TimeSpecError("n_mismatch_span", f"n={n} span={span!r}")
    for p in parts:
        if p.year is not None and p.year not in years:
            # 연도는 스팬에 적혀 있을 때만 받는다 — 「작년」은 year_offset으로(LLM 계산 금지)
            raise TimeSpecError("year_not_in_span", f"{p.year} span={span!r}")
        if not nums:
            continue
        for value in (p.month, p.day, p.hour):
            if value is not None and value not in nums:
                raise TimeSpecError("date_mismatch_span", f"{value} span={span!r}")


def slot_to_spec(slot: Any, text: str) -> tuple[TimeSpec | None, str | None]:
    """LLM 시간 슬롯(enum JSON)을 검증해 `TimeSpec`(source=llm)으로 바꾼다(plans/122 T-3).

    실패하면 `(None, 사유 코드)` — 슬롯을 폐기하고 사유를 로그에 남기는 것은 호출부 몫이다.
    사유 코드: slot_not_object · relation_missing · invalid_relation · no_time_expression ·
    span_missing · span_not_in_text(환각 기간 차단) · invalid_n · n_out_of_range ·
    n_mismatch_span · invalid_unit · unit_missing · invalid_completeness · invalid_anchor ·
    anchor_data_latest_unsupported(G-15) · invalid_display_grain · invalid_partial_date ·
    month_out_of_range · day_out_of_range · hour_out_of_range · year_out_of_range ·
    year_not_in_span · date_mismatch_span · start_missing · end_missing 등(`TimeSpecError.code`)

    Args:
        slot: input_parser 출력의 시간 슬롯(dict)
        text: 사용자 원문 — 스팬 대조 대상
    """
    if not isinstance(slot, Mapping):
        return None, "slot_not_object"
    relation = slot.get("relation")
    if relation is None:
        return None, "relation_missing"
    if not isinstance(relation, str) or relation not in RELATIONS:
        return None, "invalid_relation"
    try:
        display = _opt_enum(slot.get("display_grain"), DISPLAY_GRAINS, "invalid_display_grain")
        if relation == "none" and display in (None, "none"):
            return None, "no_time_expression"
        span = slot.get("span")
        if not isinstance(span, str) or not span.strip():
            return None, "span_missing"
        pos = _locate(span.strip(), text)
        if pos is None:
            return None, "span_not_in_text"
        n = _opt_int(slot.get("n"), "invalid_n")
        unit = _opt_enum(slot.get("unit"), UNITS, "invalid_unit")
        completeness = _opt_enum(slot.get("completeness"), COMPLETENESS, "invalid_completeness")
        anchor_raw = slot.get("anchor")
        if anchor_raw == "data_latest":
            raise TimeSpecError("anchor_data_latest_unsupported")
        anchor = _opt_enum(anchor_raw, ANCHORS, "invalid_anchor") or "now"
        start = _slot_partial(slot.get("start"))
        end = _slot_partial(slot.get("end"))
        if relation in ("this", "to_date"):
            n = None  # 「이번 달」에 n은 의미가 없다(1을 싣는 LLM이 있다)
        spec = TimeSpec(
            relation=cast(Relation, relation),
            n=n,
            unit=cast(Unit | None, unit),
            completeness=cast(Completeness | None, completeness),
            anchor=cast(Anchor, anchor),
            abs_start=start,
            abs_end=end,
            display_grain=cast(DisplayGrain, display or "none"),
            span=text[pos[0]:pos[1]],
            span_pos=pos,
            source="llm",
        )
        # 값 범위(TimeSpec 검증) 다음에 스팬 대조 — 사유 코드가 더 근본적인 쪽을 먼저 낸다
        _check_span_values(relation, n, [p for p in (start, end) if p is not None], span)
    except TimeSpecError as exc:
        return None, exc.code
    return spec, None


__all__ = [
    "CLARIFY_CODES", "CLARIFY_FUTURE_EXPLICIT", "CLARIFY_FUTURE_RELATIVE", "CLARIFY_INVALID_DATE",
    "CLARIFY_INVALID_SPEC", "CLARIFY_UNRESOLVED", "SLOT_KEYS", "Interpretation", "interpret",
    "interpret_detail", "recognize", "recognize_detail", "slot_to_spec",
]
