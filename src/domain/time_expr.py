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
  `invalid_date`(존재하지 않는 월·날짜) · `invalid_clock`(존재하지 않는 시각 — 「24시」·
  「10시 60분」) ·
  `future_explicit`(연도를 명시한 미래 기간) · `future_relative`(「내년」 등 규칙이 못 잡은
  미래 어휘) · `clock_meridiem`(오전·오후 표지 없는 1~12시) · `clock_range_in_progress`(끝이 명시된
  시각 범위가 진행 중인 시라 시 통계 구간이 빔) · `unresolved`(그 밖의 못 잡은 기간 흔적) ·
  `invalid_spec`(해석기가 거부한 명세)

규칙 설계 — 기존 정규식(`src/utils/query_gen_common.py` D-185 · D-102)의 오탐 차단을 지킨다:

- 「1-6」처럼 '월' 접미도 연도도 없는 숫자 범위는 월 범위가 아니다
- 「3개월」의 '월'은 달력 월이 아니다(숫자 바로 뒤 '월'만 달력 월)
- 「지난주」(달력 주)와 「지난 N주」(완결 N×7일)는 어휘로 가른다. 「지난 N년」은 완결 12N개월이고
  「작년」은 달력 연이다(plans/122 T-2 구현 시 정함)
- 분(D-328): 숫자 바로 뒤 「분」만 단위다(「분기」·「분석」·「분당」 등 제외 · 고유어 수사 불가 —
  「세 분」은 사람 수 · 십·백을 포함한 한자어 수사 「삼십 분」은 된다 — plans/156).
  「N시간 M분」·「N시간 반」은 한 명세(60N+M분)다. 시각 뒤 분(「10시 30분」)은 기간 단위가 아니라
  시각의 일부다(아래 시각 규칙). 「5분 간격」의 「간」은 기간 접미가 아니다
- 시각(plans/156 G-1·G-2 (a) — D-328 ⑤ 개정): (날짜 토큰) + (오전·오후…) + 「H시 (M분|반)」을
  한 명세로 묶는다 — 뒤에 「부터·이후」면 since, 「까지」면 until, 「A시부터 B시까지」·「A~B시」면
  between, 아무것도 없으면 시점 단독(absolute — 그 시 한 칸). 날짜 토큰이 없으면 오늘이다.
  「3시 방향」·「1시간」은 시각이 아니다. 시각 뒤 「전·이전·후·동안·사이」(「10시 30분 전」)와
  오전·오후로 못 푸는 시각(「밤 12시」), 규칙이 모르는 날짜 조각 뒤 시각(「10/12 9시」), 다른 기간과
  함께 온 날짜 없는 시각(「지난주 9시」)은 스팬을 잡아 두고 되묻는다(`unresolved`).
  오전·오후 등 표지 없는 1~12시(「2시 반」)는 오전·오후를 되묻는다(`clock_meridiem` — 2026-10-14
  사용자 확정 · 「0시」와 13~23시는 그대로 · 범위 끝은 시작의 표지를 잇는다).
  「9시 이후 생성된」·「9시 실행 배치」처럼 속성 동사가 붙은 **날짜 없는** 시각은 날짜 속성 필터와
  같이 조회 기간에서 뺀다(날짜가 있으면 조회 기간이다)

겹치는 스팬 처리: 아래 `_RULES`의 **우선순위 순서**로 매칭하고, 이미 채택된 스팬과 한 글자라도
겹치는 후순위 매칭은 버린다. 그래서 더 구체적인 표현(범위 > 날짜 > 반기·분기 > 연월 > 상대 N >
고정 어휘 > 연 > 입도 > 「적이 있는」)이 이긴다 — 「2026년 1월부터 6월까지」는 범위 하나이고,
「지난 3개월간」의 '월간'은 입도로 다시 잡히지 않는다. 결과는 원문 위치 순이다.

domain 계층 — 표준 라이브러리와 `src.domain.time_spec`만 의존한다.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, NamedTuple, cast

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
    "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12, "열세": 13, "열네": 14,
    "열다섯": 15, "열여섯": 16, "열일곱": 17, "열여덟": 18, "열아홉": 19, "스무": 20,
    "스물한": 21, "스물두": 22, "스물세": 23, "스물네": 24, "서른": 30, "마흔": 40,
}
# 긴 어휘가 먼저 오도록(「열한」이 「열」보다 먼저)
_KO_NUM = "|".join(sorted(_KO_NUMERALS, key=len, reverse=True))
# 한자어 수사(plans/156 G-4 (a)) — **십·백을 포함한 것만** 수다
# (「이십」·「삼십오」·「십」·「백이십」).
# 한 글자(일~구)는 「이 일」·「구 일정」·「오 분」처럼 낱말과 겹쳐 기간으로 읽지 않는다(흔적만 —
# `_TRACE_RE`). 값은 `_sino_value`가 자릿수 문법으로 계산한다
_SINO_DIGITS: dict[str, int] = {
    "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9,
}
_SINO_D = "[일이삼사오육칠팔구]"
_SINO_COEF = "[이삼사오육칠팔구]"  # 십·백 앞 계수(「일십」·「일백」은 쓰지 않는다)
_SINO_NUM = (
    rf"(?:{_SINO_COEF}?백(?:{_SINO_COEF}?십)?{_SINO_D}?|{_SINO_COEF}?십{_SINO_D}?)"
)
_NUM = rf"(?P<num>\d{{1,5}}|{_KO_NUM}|{_SINO_NUM})"
# 분(D-328) — 「분기·분석·분류·분야·분포·분당·분량·분산·분할·분배·분리·분위」와 분수(「10분의 1」)는
# 단위가 아니다
_MIN = r"분(?!기|석|류|야|포|당|량|산|할|배|리|위|의\s*\d)"
# 단위 뒤 빈도·수량 어휘 — 「5분 간격」·「3시간마다」·「3일 이상 걸린」은 기간이 아니다(D-328 ⑧ ⓓ →
# plans/156 G-5 (a)로 모든 단위). `최근` 접두가 있어도 같다. 「이내」 같은 만료 필터 어휘는 넣지
# 않는다 — 「최근 3시간 이내」는 기간이다. 단위 바로 뒤만 본다 — 「최근 3시간 5분마다」는 3시간
# 기간 + 5분 간격이다(plans/159). 규칙(`_UNIT`·`_MINUTE_PART`) · 흔적(`_NOT_PERIOD_AFTER`) · 슬롯
# 가드(`_FREQ_RE`)가 이 한 조각을 본다(사본 금지 · D-131). 「이상」 뒤에 비정상 뜻 낱말이
# 붙으면(「이상징후」·「이상 탐지」·「이상 없는」·「이상한」) 수량 어휘가 아니다 — 닫힌 목록
# (plans/156 J-1)
_ANOMALY_AFTER = r"징후|탐지|감지|없|한|여부|유무|치|현상|증상"
_NOT_PERIOD_WORDS = (
    rf"이상(?!\s*(?:{_ANOMALY_AFTER}))|이하|미만|초과|넘|째|번째|씩|마다|간격|주기|만에"
)
_FREQ_TAIL = rf"\s*(?:{_NOT_PERIOD_WORDS})"
# 단위어(빈도 가드 전). 영문 단위는 영문자·숫자·밑줄 경계로 「minimum·minor·min_avg」를 막는다
# (plans/156 ⑧ · G-5). 「주일」 안의 「주」로 되짚어 가드를 비켜 가지 않게 「주(?!일)」로 쓴다
_UNIT_WORDS = (
    rf"개\s*월|달|주일|주(?!일)|일|시간|년|{_MIN}"
    r"|(?:months?|weeks?|days?|hours?|minutes?|mins?|hrs?|years?)(?![A-Za-z0-9_])"
)
_UNIT = rf"(?P<unit>(?:{_UNIT_WORDS})(?!{_FREQ_TAIL}))"
_SUFFIX = r"(?P<suf>\s*(?:간(?!격)|동안|치))"  # 「5분 간격」은 빈도다(기간 아님)
_PREFIX = r"(?P<pre>지난|최근|과거|직전|last|past)"
# 한글 수사는 이 단위와만 어울린다(「세 달」은 되고 「세 일」은 안 된다)
_KO_NUM_UNITS: frozenset[str] = frozenset({"month", "week", "hour"})
_KO_UNIT_WORDS = r"개\s*월|달|주일|주(?!일)|시간"
# 한자어 수사는 분·시간·일·주·개월과만 어울린다(「세 분」은 사람 수 — 고유어 + 분은 기간이 아니다.
# 「이십 년」·「삼십 달」은 범위 밖 — 종전 동작). 단위 뒤가 낱말 일부면(「백분율」·「이십 주년」)
# 수가 아니다
_SINO_NUM_UNITS: frozenset[str] = frozenset({"minute", "hour", "day", "week", "month"})
_SINO_UNIT_WORDS = rf"개\s*월|주일|주(?!일)|일|시간|{_MIN}"
# 한 음절 흔적용 — 「일주일」의 「일」이 수사로 읽히지 않게 「주일」을 뺀다
_SINO_ONE_UNIT_WORDS = rf"개\s*월|주(?!일)|일|시간|{_MIN}"
# 「의」 뒤가 한자어 수사면 분수다(「이십분의 일」 — 기간 아님)
_SINO_END = r"(?=$|[^가-힣]|간(?!격)|동안|치|의(?!\s*[일이삼사오육칠팔구십백])|쯤|정도|가량)"
_SINO_END_RE = re.compile(_SINO_END)
_RANGE_SEP = r"(?:부터|에서|~|∼|〜|-|–|—)"


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _rel_offset(raw: str | None) -> int | None:
    return None if raw is None else _REL_YEAR_OFFSET[_compact(raw)]


def _sino_value(raw: str) -> int:
    """한자어 수사 → 정수(「이십오」=25 · 「백이십」=120 · 「십」=10)."""
    total = digit = 0
    for ch in raw:
        if ch == "백":
            total, digit = total + (digit or 1) * 100, 0
        elif ch == "십":
            total, digit = total + (digit or 1) * 10, 0
        else:
            digit = _SINO_DIGITS[ch]
    return total + digit


def _num(raw: str) -> int:
    if raw.isdigit():
        return int(raw)
    return _KO_NUMERALS[raw] if raw in _KO_NUMERALS else _sino_value(raw)


def _unit_of(raw: str) -> str:
    r = _compact(raw).lower()
    if r in ("개월", "달") or r.startswith("month"):
        return "month"
    if r in ("주", "주일") or r.startswith("week"):
        return "week"
    if r == "일" or r.startswith("day"):
        return "day"
    if r == "시간" or r.startswith(("hour", "hr")):
        return "hour"
    if r == "분" or r.startswith("min"):
        return "minute"
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

# ── 시각(plans/156 G-1·G-2 (a)) ────────────────────────────────────────────────
#: 시각 앞 상대 일 → `PartialDate.day_offset`(인식기는 날짜를 계산하지 않는다)
_CLOCK_DAY_WORDS: dict[str, int] = {
    "오늘": 0, "금일": 0, "어제": -1, "전일": -1, "작일": -1,
    "그제": -2, "그저께": -2, "엊그제": -2,
}
#: 시각을 무효로 만드는 꼬리 — 규칙이 해석하지 않고 스팬을 잡아 되묻는다(「10시 30분 전」= 10시 30분
#: 이전인지 30분 전인지 · 「9시 전후」 · 「10시 30분 동안」)
#: 「9시와 10시 사이」 — 두 시점 사이는 범위로 풀지 않고 되묻는다(plans/156 W2 교정)
_CLOCK_DECLINE_RE = re.compile(
    r"\s*(?:이전|전(?=$|[^가-힣]|에|부터|이후|의|쯤|까지|후)|후(?=$|[^가-힣]|에|부터|로|의)"
    r"|동안|간(?!격)|치(?![가-힣])|사이)"
)
#: 시각 규칙 안의 공백 — 무한 `\s*` 연쇄는 긴 공백 입력에서 되짚기가 길이의 제곱으로 는다
_SP = r"\s{0,3}"
#: 시각 + (「이후·이전…」) + 속성 동사(「생성된」·「등록한」·「실행된」) — 자원 속성 시각이지 조회
#: 기간이 아니다(`_is_deadline_filter`와 같은 방식 — 날짜 토큰 없는 시각 규칙에만 건다)
_CLOCK_ATTR_RE = re.compile(
    rf"{_SP}(?:이전|이후|부터|까지|전|후)?{_SP}(?:에{_SP})?(?:[가-힣A-Za-z]{{1,4}}\s{{1,3}})?"
    r"(?:(?:생성|등록|수정|변경|갱신|작성|입력|배포|설치|접수|발급|추가)(?:된|한|됐|되었|일|시각|일시)"
    r"|(?:실행|수행|구동|기동)(?:된|한|됐|되었|되는|하는|시각|일시|(?![가-힣])))"
)
#: 날짜 토큰 없는 시각 바로 앞의 못 읽은 날짜 조각(「15일 10시」·「12일자 9시」·「월요일 9시」·
#: 「(월) 9시」·「매일 9시」·「10/12 9시」·「2026.10.12 9시」·「20261012 9시」) 또는 날짜처럼 보이는
#: 낱말(「오늘날」·「그제서야」) — 오늘로 읽지 않고 되묻는다. 시각 앞 짧은 창만 본다
_DAY_WORD_FRAGMENT = r"[월화수목금토일]\s*요일|\([월화수목금토일]\)|주말|평일|매일|매주|매월"
_CLOCK_BARE_DATE_BEFORE_RE = re.compile(
    rf"(?:\d\s*(?:일자?|월)|{_DAY_WORD_FRAGMENT}"
    r"|(?<![\w.])(?:\d{4}\s*[./-]\s*)?\d{1,2}\s*[./-]\s*\d{1,2}\.?|(?<!\d)\d{8}"
    r"|오늘날|그제서?야)\)?\s{0,3}$"
)
#: 시각과 떨어진 날짜 조각(「15일 서버 오전 9시」·「10/12 서버 … 9시 이후」·「월요일에 발생한 …
#: 9시 이후」) — 날짜 없는 시각이 있을 때 가린 원문에서 찾는다(plans/156 J-2). 떨어져 있으면
#: 소수(「80.5 이상」)·IP와 겹치므로 점 구분 날짜는 연도가 있을 때만 · 숫자 + 「일」은 흔적
#: (`_TRACE_RE`)이 본다
_DETACHED_DATE_RE = re.compile(
    rf"{_DAY_WORD_FRAGMENT}|\d\s*일자"
    r"|(?<![\w./])\d{1,2}\s*/\s*\d{1,2}(?![\d/])"
    r"|(?<![\w.])\d{4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}(?![\d.])"
)
_CLOCK_BARE_DATE_WINDOW = 24
_CLOCK_UNRESOLVED = "clock_unresolved"
#: 오전·오후 표지 없는 1~12시(2026-10-14 사용자 확정) — 오전·오후를 되묻는다
_CLOCK_MERIDIEM = "clock_meridiem_missing"
_CLOCK_PM: frozenset[str] = frozenset({"오후", "저녁", "밤"})


def _clock_date(p: str) -> str:
    """시각 앞 날짜 토큰(그룹 접두 `p`) — 상대 일 · ISO 날짜 · (연) 월 일."""
    return (
        rf"(?P<{p}dw>그저께|엊그제|그제(?![서야])|어제|오늘(?!날)|(?<![가-힣])(?:금일|전일|작일))"
        rf"|(?<![\d-])(?P<{p}iy>\d{{4}})-(?P<{p}im>\d{{1,2}})-(?P<{p}id>\d{{1,2}})"
        rf"|(?:(?P<{p}y>\d{{4}}){_SP}년{_SP}|(?P<{p}r>{_REL_YEAR}){_SP})?"
        rf"(?<!\d)(?P<{p}m>\d{{1,2}}){_SP}월{_SP}(?P<{p}d>\d{{1,2}}){_SP}일"
    )


def _clock_time(p: str, *, si_optional: bool = False) -> str:
    """시각(그룹 접두 `p`) — (오전·오후…) H시 (M분 | 반) (경·쯤).

    「시간」·「시스템」·「3시 방향」은 시각이 아니다.
    """
    si = rf"(?P<{p}si>시)" + ("?" if si_optional else "")
    return (
        rf"(?:(?P<{p}ap>오전|오후|새벽|아침|저녁|밤){_SP})?(?<![\d.:])(?P<{p}h>\d{{1,2}}){_SP}{si}"
        r"(?=$|[^가-힣]|부터|이후|이전|에|까지|경|쯤|께|정각|반|의|전|후|대|정도|와|과|랑|는|도|부근)"
        r"(?!\s*방향)"
        rf"(?:{_SP}(?P<{p}mi>\d{{1,2}}){_SP}{_MIN}|{_SP}(?P<{p}half>반)(?=$|[^가-힣]|부터|이후|에|까지|쯤|경))?"
        rf"(?:{_SP}(?:경|쯤|께|정각))?"
    )


def _clock_hour(h: int, meridiem: str | None) -> int:
    """오전·오후 표지 → 24시제.

    표지와 맞지 않는 시(「오후 14시」·「밤 1시」·「밤 12시」)는 되묻는다. 표지 없는 1~12시는
    오전·오후를 되묻는다(2026-10-14 사용자 확정 — 「0시」·13~23시는 그대로).
    """
    if meridiem is None:
        if 1 <= h <= 12:
            raise TimeSpecError(_CLOCK_MERIDIEM, str(h))
        return h  # 0~23 밖은 PartialDate가 `hour_out_of_range`로 막는다
    if not 1 <= h <= 12:
        raise TimeSpecError(_CLOCK_UNRESOLVED, f"{meridiem} {h}")
    if meridiem == "오후":
        return h if h == 12 else h + 12
    if meridiem in _CLOCK_PM:  # 저녁·밤 — 6~11시만(「밤 1시」·「밤 12시」는 날짜가 모호하다)
        if not 6 <= h <= 11:
            raise TimeSpecError(_CLOCK_UNRESOLVED, f"{meridiem} {h}")
        return h + 12
    return 0 if h == 12 else h  # 오전·새벽·아침


def _clock_date_fields(g: Mapping[str, Any], p: str) -> dict[str, int | None] | None:
    if g.get(f"{p}dw"):
        return {"day_offset": _CLOCK_DAY_WORDS[g[f"{p}dw"]]}
    if g.get(f"{p}iy"):
        return {"year": int(g[f"{p}iy"]), "month": int(g[f"{p}im"]), "day": int(g[f"{p}id"])}
    if g.get(f"{p}m"):
        return {
            "year": int(g[f"{p}y"]) if g.get(f"{p}y") else None,
            "year_offset": _rel_offset(g.get(f"{p}r")),
            "month": int(g[f"{p}m"]), "day": int(g[f"{p}d"]),
        }
    return None


def _clock_partial(
    g: Mapping[str, Any], p: str, *, date: Mapping[str, int | None] | None = None,
    meridiem: str | None = None,
) -> PartialDate:
    """날짜 토큰 + 시각 → PartialDate. 날짜 토큰이 없으면 `date`(범위 끝 = 시작 날짜) 또는 오늘."""
    minute = 30 if g.get(f"{p}half") else int(g[f"{p}mi"]) if g.get(f"{p}mi") else None
    # 「10시 60분」 — 없는 시각(`invalid_clock`)이 오전·오후 되묻기에 앞선다(값 검증은 PartialDate가
    # 하지만 그 전에 오전·오후 판정이 되묻기로 끝내지 않게 먼저 본다)
    if minute is not None and minute > 59:
        raise TimeSpecError("minute_out_of_range", str(minute))
    hour = _clock_hour(int(g[f"{p}h"]), g.get(f"{p}ap") or meridiem)
    fields = _clock_date_fields(g, p) or dict(date or {"day_offset": 0})
    return PartialDate(**fields, hour=hour, minute=minute)


def _b_clock_range(m: re.Match[str], text: str) -> _Built | None:
    """「오늘 9시부터 10시까지」·「어제 22시부터 오늘 2시까지」·「9~10시」 → between(끝 미포함)."""
    g = m.groupdict()
    if g["asi"] is None and _compact(g["sep"]) in ("부터", "에서"):
        return None  # 「9부터 10시」 — 앞 시각이 아니다
    start = _clock_partial(g, "a")
    own_date = _clock_date_fields(g, "b")
    date = None if own_date else {
        k: getattr(start, k) for k in ("year", "year_offset", "month", "day", "day_offset")
        if getattr(start, k) is not None
    }
    # 「오후 2시부터 4시까지」 — 날짜·표지가 없는 끝은 시작의 오전·오후 표지를 잇는다
    inherit = g["aap"] if own_date is None and not g["bap"] and int(g["bh"]) <= 12 else None
    end = _clock_partial(g, "b", date=date, meridiem=inherit)
    return TimeSpec(relation="between", abs_start=start, abs_end=end), m.end()


def _next_day(p: PartialDate) -> dict[str, int | None]:
    """시각의 날짜 다음 날(날짜 필드만). 연도 미상 2월 28일·12월 31일은 다음 날이 모호해
    되묻는다."""
    if p.day_offset is not None:
        return {"day_offset": p.day_offset + 1}
    if p.month is not None and p.day is not None:
        y = p.year
        if p.day < calendar.monthrange(y if y is not None else 2001, p.month)[1]:
            return {"year": y, "year_offset": p.year_offset, "month": p.month, "day": p.day + 1}
        if p.month < 12 and not (p.month == 2 and p.day == 28 and y is None):
            return {"year": y, "year_offset": p.year_offset, "month": p.month + 1, "day": 1}
        if p.month == 12 and y is not None:
            return {"year": y + 1, "month": 1, "day": 1}
    raise TimeSpecError(_CLOCK_UNRESOLVED, "end beyond representable next day")


def _since_end(point: PartialDate, tail: re.Match[str]) -> PartialDate:
    """「…부터」 뒤 끝 표현(`_SINCE_END_RE`) → 끝 시점. 정오 = 그날 12:00 · 자정 = 다음 날 0:00 ·
    「N시간 (동안)」 = 시작 + N시간. 오전·오후 낱말만(「오후까지」)은 되묻는다(plans/156 J-3)."""
    date: dict[str, int | None] = {
        k: getattr(point, k) for k in ("year", "year_offset", "month", "day", "day_offset")
    }
    if tail["noon"]:
        return PartialDate(**date, hour=12)
    if tail["midnight"]:
        return PartialDate(**_next_day(point), hour=0)
    if tail["dur"] is None:
        raise TimeSpecError(_CLOCK_UNRESOLVED, "meridiem-only end")
    assert point.hour is not None  # 시각 규칙은 시를 늘 채운다
    hour = point.hour + int(tail["dur"])
    if hour >= 24:
        if point.day_offset == 0:
            # 「오늘」에서 자정을 넘는 끝은 늘 기준 시각 뒤라 해석기가 기준 시각에서 자른다 —
            # 다음 날 0시(표현 가능한 끝)로 두어도 결과가 같다
            return PartialDate(day_offset=1, hour=0)
        date, hour = _next_day(point), hour - 24
    return PartialDate(**date, hour=hour, minute=point.minute)


def _b_clock(m: re.Match[str], text: str) -> _Built:
    """(날짜) + 시각 — 「부터·이후」 since · 「까지」 until · 없으면 시점 단독(absolute)."""
    g = m.groupdict()
    if _clock_date_fields(g, "a") is None and _CLOCK_BARE_DATE_BEFORE_RE.search(
        text, max(0, m.start() - _CLOCK_BARE_DATE_WINDOW), m.start()
    ):
        raise TimeSpecError(_CLOCK_UNRESOLVED, "date fragment before clock")
    # 꼬리를 먼저 본다 — 「10시 30분 전」은 오전·오후보다 앞서 무효다(되묻기 사유 우선)
    since = _SINCE_RE.match(text, m.end())
    until = None if since else _UNTIL_RE.match(text, m.end())
    if since is None and until is None and _CLOCK_DECLINE_RE.match(text, m.end()):
        raise TimeSpecError(_CLOCK_UNRESOLVED, "clock with before/after/duration tail")
    point = _clock_partial(g, "a")
    if since:
        tail = _SINCE_END_RE.match(text, since.end())
        if tail is None:
            return TimeSpec(relation="since", abs_start=point), since.end()
        return TimeSpec(relation="between", abs_start=point, abs_end=_since_end(point, tail)), \
            tail.end()
    if until:
        return TimeSpec(relation="until", abs_end=point), until.end()
    return TimeSpec(relation="absolute", abs_start=point), m.end()


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
    if unit in ("hour", "minute"):
        return TimeSpec(relation="last", n=n, unit=cast(Unit, unit), completeness="rolling")
    return TimeSpec(relation="last", n=n, unit=cast(Unit, unit))


def _b_last_n(m: re.Match[str], text: str) -> _Built | None:
    raw = m.group("num")
    unit = _unit_of(m.group("unit"))
    if raw in _KO_NUMERALS and unit not in _KO_NUM_UNITS:
        return None
    if not raw.isdigit() and raw not in _KO_NUMERALS and (
        unit not in _SINO_NUM_UNITS
        or _compact(m.group("unit")) == "달"
        or (m.group("suf") is None and not _SINO_END_RE.match(text, m.end("unit")))
    ):
        return None
    if unit == "minute" and _after_clock(text, m.start("num")):
        return None
    if unit == "hour" and m.group("suf") is None and _MINUTE_TAIL_RE.match(text, m.end("unit")):
        # 「N시간 M분」 합성이 거부됐다(분 상한 초과) — M분을 버리지 않고 흔적으로 둔다
        return None
    return _last_n(_num(raw), unit), m.end()


# 시각 뒤 분(「10시 30분」 · D-328 ⑤) — 기간 단위가 아니다. 시각 규칙(plans/156)이 먼저 소유하므로
# 평소에는 걸리지 않는다 — 시각 규칙이 잡지 못한 경우의 방어 가드로 남긴다
_CLOCK_BEFORE_RE = re.compile(r"\d{1,2}\s*시\s*$")
# 「N시간 반」의 「반」 — 「반영·반기·반복」처럼 낱말 일부면 아니다
# 근사 접미 「쯤·정도·가량·여」도 받는다 — 「1시간 반쯤」의 「반」을 버리지 않는다(plans/156 ⑦)
_HALF_END = r"(?=$|[^가-힣]|동안|간(?!격)|치|전|쯤|정도|가량|여(?![가-힣]))"
_MINUTE_PART = rf"(?:\d{{1,2}}\s*{_MIN}|반{_HALF_END})(?!{_FREQ_TAIL})"
_MINUTE_TAIL_RE = re.compile(rf"\s*{_MINUTE_PART}")
# 「N분 전」 뒤 — 「전체·전부·전송」 등 낱말 일부와 「전까지·전보다」(끝·비교)는 받지 않는다
_AGO = r"\s*전(?=$|[^가-힣]|에|부터|이후|의|쯤)(?:\s*(?:부터|이후))?"


def _after_clock(text: str, start: int) -> bool:
    return bool(_CLOCK_BEFORE_RE.search(text[:start]))


# 슬롯 가드(D-328 ⑤⑧ · plans/156 G-5) — LLM이 내는 unit도 규칙과 같은 판정으로 거절한다
_CLOCK_MINUTE_RE = re.compile(rf"\d{{1,2}}\s*시\s*\d{{1,2}}\s*{_MIN}")
_FREQ_RE = re.compile(
    rf"(?:(?<!\d)\d+|(?<![가-힣])(?:{_KO_NUM}|{_SINO_NUM}))\s*(?:{_UNIT_WORDS}){_FREQ_TAIL}",
    re.IGNORECASE,
)


def _span_hits(patterns: tuple[re.Pattern[str], ...], text: str, start: int, end: int) -> bool:
    """원문 `[start, end)` 스팬이 패턴 매치 하나와라도 겹치는가."""
    return any(
        m.start() < end and start < m.end()
        for pattern in patterns
        for m in pattern.finditer(text)
    )


def _b_hour_minute(m: re.Match[str], text: str) -> _Built | None:
    """「최근 1시간 30분」·「2시간 반 동안」·「1시간 30분 전」 → 한 명세 60N+M분(D-328 ④).

    접두(지난·최근)·접미(간·동안·치)·「전」이 없는 맨 「1시간 30분」은 맨 「N시간」처럼 흔적이다.
    """
    part = _compact(m.group("mpart"))
    minutes = 30 if part.startswith("반") else int(re.sub(r"\D", "", part))
    if minutes >= 60:
        return None  # 「1시간 70분」 — 합성하지 않는다(시간 규칙도 꼬리를 보고 물러나 되묻는다)
    n = _num(m.group("hh")) * 60 + minutes
    if m.group("ago"):
        return TimeSpec(relation="ago", n=n, unit="minute"), m.end()
    if m.group("pre") is None and m.group("suf") is None:
        return None
    return TimeSpec(relation="last", n=n, unit="minute", completeness="rolling"), m.end()


def _b_unit_ago(m: re.Match[str], text: str) -> _Built | None:
    """「5분 전」·「3시간 전부터」 → 기준 시각까지의 N분·N시간(D-328 ② · plans/156 G-6 (a)).

    「3일 전」 등 일 이상은 잡지 않는다(그날 하루인지 그때부터인지 모호 — 흔적으로 되묻는다).
    """
    unit = _unit_of(m.group("unit"))
    if unit == "minute" and _after_clock(text, m.start("num")):
        return None
    return TimeSpec(relation="ago", n=int(m.group("num")), unit=cast(Unit, unit)), m.end()


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

_CLOCK_RULES: frozenset[str] = frozenset({"clock_range", "clock"})

_RULES: tuple[_Rule, ...] = (
    # 시각(plans/156) — 날짜 토큰을 함께 소유하므로 날짜 규칙보다 먼저 잡는다
    _Rule("clock_range", re.compile(
        rf"(?:(?:{_clock_date('a')}){_SP})?{_clock_time('a', si_optional=True)}"
        rf"{_SP}(?P<sep>부터|에서|~|∼|〜|-|–){_SP}"
        rf"(?:(?:{_clock_date('b')}){_SP})?{_clock_time('b')}(?:{_SP}까지)?"
    ), _b_clock_range),
    _Rule("clock", re.compile(rf"(?:(?:{_clock_date('a')}){_SP})?{_clock_time('a')}"), _b_clock),
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
        r"(?<![\d.])(?P<m1>\d{1,2})\s{0,3}(?P<w1>월)?\s{0,3}"  # 공백 상한 — 긴 공백 되짚기 차단
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
    _Rule("hour_minute", re.compile(
        rf"(?:{_PREFIX}\s*|(?<![가-힣0-9A-Za-z]))(?P<hh>\d{{1,3}}|{_KO_NUM}|{_SINO_NUM})\s*시간\s*"
        rf"(?P<mpart>{_MINUTE_PART})(?:(?P<ago>{_AGO})|{_SUFFIX})?", re.IGNORECASE
    ), _b_hour_minute),
    _Rule("unit_ago", re.compile(
        rf"(?<![가-힣0-9A-Za-z])(?P<num>\d{{1,5}})\s*(?P<unit>{_MIN}|시간){_AGO}"
    ), _b_unit_ago),
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
_INVALID_VALUE_CODES: frozenset[str] = frozenset({
    "month_out_of_range", "day_out_of_range", "hour_out_of_range", "minute_out_of_range",
    "year_out_of_range",
})
#: 그중 시각 값(「24시」·「10시 60분」) — 되묻기 문구가 날짜가 아니라 시각이다
_INVALID_CLOCK_CODES: frozenset[str] = frozenset({"hour_out_of_range", "minute_out_of_range"})


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
    scan = _recognize(text)
    return scan.found, scan.invalid


# 지원 종료일(EoS·EoL)·만료일처럼 **자원의 날짜 속성 조건**에 붙은 기간은 조회 기간이 아니다 —
# 「2027년에 EoS가 되는 서버」·「EoS가 2027년인 서버」·「내년에 만료되는 인증서」는 속성값 필터라
# 미래여도 되묻지 않는다(데이터가 있다). 마감어 앞에는 조사(「에·까지·안에…」)와 수식어 두 단어
# (「하드웨어 지원」·「유지보수 계약」)까지만 허용한다 — 「2025년 3월 CPU 사용률과 EOS」처럼
# 다른 대상을 잇는 말(과·와·및·고·며)이 끼면 조회 기간 그대로다. 「만료」의 과거·완료형은
# 지난 사건의 조회 기간이라 그대로 둔다(D-319 ③과 같은 기준).
_ATTR_DEADLINE = (
    r"(?:(?i:EO[SL])|지원\s*(?:이\s*)?(?:종료|만료|끝나|끝날)|단종|만료(?!된|됐|되었))"
)
_ATTR_DEADLINE_RE = re.compile(_ATTR_DEADLINE)
# 공백은 `\s{0,3}`로 묶는다 — 무한 `\s*` 연쇄는 긴 공백 입력에서 되짚기가 길이의 제곱으로 는다
# (plans/156 W2 교정 — 1만 자 입력 1초 미만)
_DEADLINE_AFTER_RE = re.compile(
    r"\s{0,3}(?:까지|중에?|이내에?|안에|안으로|내에?|내로|사이에?|이후에?|에)?\s{0,3}"
    r"(?:[가-힣A-Za-z/]{1,5}(?<![과와및고며랑])\s{0,3}){0,2}?" + _ATTR_DEADLINE
)
_DEADLINE_BEFORE_RE = re.compile(
    _ATTR_DEADLINE
    + r"\s{0,3}(?:예정\s{0,3})?(?:일자?|날짜|시점|년월일)?\s{0,3}(?:이|가|은|는)?\s{0,3}"
    r"(?:예정\s{0,3})?$"
)


def _is_deadline_filter(text: str, start: int, end: int) -> bool:
    """`text[start:end]` 기간 표현이 날짜 속성 조건(지원 종료·만료)의 값인지."""
    return bool(
        _DEADLINE_AFTER_RE.match(text, end) or _DEADLINE_BEFORE_RE.search(text[:start])
    )


class _Scan(NamedTuple):
    """`_recognize` 결과."""

    found: list[TimeSpec]
    #: 존재하지 않는 날짜·시각 표현 `[(시작, 끝, 거부 코드)]`
    invalid: list[tuple[int, int, str]]
    #: 날짜 속성 조건으로 빠진 기간 스팬(흔적 검사에서 가린다)
    filtered: list[tuple[int, int]]
    #: 규칙이 잡았지만 해석하지 않은 시각 스팬(되묻기 — plans/156)
    unresolved: list[tuple[int, int]]
    #: 오전·오후 표지 없는 1~12시 스팬(오전·오후 되묻기 — 2026-10-14 사용자 확정)
    meridiem: list[tuple[int, int]]
    #: 날짜 토큰 없는 시각 스팬(해석됐든 표지 되묻기든) — 다른 기간과 함께 오면 되묻는다
    dateless: list[tuple[int, int]]


def _clock_dateless(m: re.Match[str]) -> bool:
    """시각 규칙 매치에 날짜 토큰이 하나도 없는가(오늘로 읽는 시각)."""
    g = m.groupdict()
    return all(_clock_date_fields(g, p) is None for p in ("a", "b") if f"{p}h" in g)


def _recognize(text: str) -> _Scan:
    """`recognize_detail` + 되묻기·필터 판정에 쓰는 스팬 목록(`_Scan`)."""
    claimed: list[tuple[int, int]] = []
    scan = _Scan([], [], [], [], [], [])
    for rule in _RULES:
        clock = rule.name in _CLOCK_RULES
        for m in rule.pattern.finditer(text):
            if _overlaps(claimed, m.start(), m.end()):
                continue
            dateless = clock and _clock_dateless(m)
            # 속성 동사(「9시 이후 생성된」)는 날짜 없는 시각에만 건다 — 「어제 9시 이후 등록된」은
            # 명시 기간이다(plans/156 W2 교정 V-3)
            attr = dateless and _CLOCK_ATTR_RE.match(text, m.end()) is not None
            try:
                built = rule.build(m, text)
            except TimeSpecError as exc:
                if exc.code in _INVALID_VALUE_CODES:
                    claimed.append((m.start(), m.end()))
                    scan.invalid.append((m.start(), m.end(), exc.code))
                elif exc.code in (_CLOCK_UNRESOLVED, _CLOCK_MERIDIEM):
                    # 날짜 토큰까지 잡아 둔다 — 「어제 9시 전」이 「어제」 하루로 조용히 풀리지 않게
                    span = (m.start(), m.end())
                    claimed.append(span)
                    if attr:
                        scan.filtered.append(span)
                        continue
                    if exc.code == _CLOCK_MERIDIEM:
                        scan.meridiem.append(span)
                        if dateless:
                            scan.dateless.append(span)
                    else:
                        scan.unresolved.append(span)
                continue  # 그 밖의 거부는 종전대로 흔적으로 남긴다
            if built is None:
                continue
            spec, end = built
            if _overlaps(claimed, m.start(), end):
                continue
            claimed.append((m.start(), end))
            if spec.relation != "none" and (attr or _is_deadline_filter(text, m.start(), end)):
                scan.filtered.append((m.start(), end))
                continue
            if dateless:
                scan.dateless.append((m.start(), end))
            scan.found.append(replace(spec, span=text[m.start():end], span_pos=(m.start(), end)))
    scan.found.sort(key=lambda s: s.span_pos[0] if s.span_pos else -1)
    return scan


# ──────────────────────────────────────────────
# 규칙 합성 — 원문 → TimeResolution
# ──────────────────────────────────────────────

# 규칙이 못 잡은 기간 표현의 흔적(숫자 + 시간 단위 · 미래 어휘). 「2개월 이상」·「5년 이상」처럼
# 수량 비교는 기간이 아니므로 뺀다. 한글 수사는 앞 글자가 한글이면 보지 않는다(「초과한 달」).
# 만료·마감 필터(「6개월 안 남은」·「3개월 이내」·「30일 후 만료」)와
# 빈도(「하루 2시간씩」·「5분 간격」)도 조회 기간이 아니다 — 흔적으로 보면 데이터 조회가
# 되묻기로 끝난다(plans/122 T-3 검증 D2 · FS-12).
# 「6개월 안에 지원 종료」·「6개월 내 만료」도 같은 만료 필터다 — 「안에·내」 뒤 한 문장·15자 안에
# 마감 동사가 올 때만 뺀다(plans/132 ITAM-103·109). 맨 「3개월 안에 발생한 알람」은 흔적 그대로다.
# 과거·완료형(「종료된」·「만료됐」·「끝난」·「도래한」)은 지난 사건의 조회 기간이라 흔적으로 남긴다
# — 맨 「5분 내 종료된 프로세스」는 되묻는다(plans/132 3회차 검증 · 「최근 5분 내」는 D-328로
# 규칙이 잡는다).
# 단위 대안은 서로 접두가 겹치지 않게 막는다 — 「1시간 이상」이 「시간」에서 막힌 뒤 「시」로
# 되짚어 흔적이 되던 것(검증 D1)을 끊는다. 「3시스템」의 「시」도 시각이 아니다(리뷰 M-2).
# 「분」은 규칙(「최근 5분」·「30분간」·「5분 전」 — D-328)이 못 잡은 것만 흔적이다 — 시각 뒤 분
# (「10시 30분부터」)·맨 「30분」은 기간 미지정 기본값으로 조용히 가지 않고 되묻는다(검증 D8).
_NOT_PERIOD_AFTER = (
    rf"(?!{_FREQ_TAIL}|\s*(?:차|이내|안\s*남|남은|남았|후|뒤"
    r"|(?:안에|안으로|내에?|내로)[^.?!\n]{0,15}?"
    r"(?:종료(?!된|됐|되었)|만료(?!된|됐|되었)|마감(?!된|됐|되었)|도래(?!한|했)|끝나|끝날"
    r"|(?i:EO[SL]))))"
)
_TRACE_RE = re.compile(
    r"(?<![\d.])\d+\s*(?:개\s*월|달|월|일|주일|주(?!일)|시간|시(?!간|스템|설|리즈|즌|\s*방향)|년|분기|반기"
    r"|분(?!기))"
    + _NOT_PERIOD_AFTER
    + rf"|(?<![가-힣])(?:{_KO_NUM})\s*(?:{_KO_UNIT_WORDS})"
    + _NOT_PERIOD_AFTER
    # 한자어 수사(plans/156 G-4 (a)) — 두 음절 이상(「삼십 분」)은 규칙이 못 잡으면 흔적이다.
    # 한 음절(일~구·십·백)은 「이 일 처리」·「구 일정」·「십분 활용」과 겹치므로 기간 접두
    # (지난·최근…)나 접미(간·동안·치)가 붙을 때만 흔적이다(규칙은 일~구를 기간으로 읽지 않는다
    # — 되묻는다)
    # 「이십 년」·「삼십 달」은 해석하지 않지만(규칙 단위 밖) 침묵 기본값 대신 되묻는다
    + rf"|(?<![가-힣])(?=[일이삼사오육칠팔구십백]{{2}})(?:{_SINO_NUM})"
    + rf"\s*(?:{_SINO_UNIT_WORDS}|년|달)"
    + _SINO_END + _NOT_PERIOD_AFTER
    # 한 음절은 「일주일 동안」·「이 주간 보고서」와 겹치므로 「주일」을 단위로 보지 않고, 접두 없는
    # 접미 분기에서는 「주간」도 뺀다
    + rf"|(?:지난|최근|과거|직전)\s*(?:{_SINO_D}|십|백)\s*(?:{_SINO_ONE_UNIT_WORDS})"
    + _SINO_END + _NOT_PERIOD_AFTER
    + rf"|(?<![가-힣])(?:{_SINO_D}|십|백)\s*(?:개\s*월|주(?![일간])|일|시간|{_MIN})"
    + r"\s*(?:간(?!격)|동안|치)"
    # 맨 영문 단위(「30 min 알람」) — 맨 「30분」처럼 되묻는다(해석은 접두·접미가 있을 때 규칙이)
    + r"|(?<![\d.])\d+\s*(?:minutes?|mins?|hours?|hrs?)(?![A-Za-z0-9_])"
    + _NOT_PERIOD_AFTER
    + r"|내일|모레|다음\s*(?:달|주|분기|해)|내년|익월|차주|그끄저께"
    r"|(?:요번|지지난|다다음|전전)\s*(?:달|월|주|분기|해|년)"
)


# 규칙이 못 잡은 **미래** 어휘(`_TRACE_RE`의 미래 부분). 기간 표현이 따로 잡혀도 남아 있으면
# 되묻는다 — 「내년 3월」이 「3월」만 잡혀 조용히 올해 3월이 되던 것(D-291 · 미래 기간 = 되묻기).
_FUTURE_TRACE_RE = re.compile(
    r"내일|모레|다음\s*(?:달|주|분기|해)|내년|익월|차주|(?:다다음)\s*(?:달|월|주|분기|해|년)"
)

#: 되묻기 사유 코드(`interpret_detail` · D-291).
CLARIFY_INVALID_DATE = "invalid_date"
CLARIFY_INVALID_CLOCK = "invalid_clock"    # 「24시」·「10시 60분」(plans/156 W2 교정)
CLARIFY_FUTURE_EXPLICIT = "future_explicit"
CLARIFY_FUTURE_RELATIVE = "future_relative"
CLARIFY_CLOCK_MERIDIEM = "clock_meridiem"  # 표지 없는 1~12시 — 오전·오후 되묻기(2026-10-14 확정)
#: 끝이 명시된 시각 범위(「10시부터 10시 30분까지」를 10:37에)의 시 통계 구간이 빔(2026-10-14 확정)
CLARIFY_CLOCK_RANGE_IN_PROGRESS = "clock_range_in_progress"
CLARIFY_UNRESOLVED = "unresolved"
CLARIFY_INVALID_SPEC = "invalid_spec"
CLARIFY_CODES: frozenset[str] = frozenset({
    CLARIFY_INVALID_DATE, CLARIFY_INVALID_CLOCK, CLARIFY_FUTURE_EXPLICIT, CLARIFY_FUTURE_RELATIVE,
    CLARIFY_CLOCK_MERIDIEM, CLARIFY_CLOCK_RANGE_IN_PROGRESS, CLARIFY_UNRESOLVED,
    CLARIFY_INVALID_SPEC,
})
#: 해석기 거부 코드 → 전용 되묻기 코드(그 밖의 거부는 `invalid_spec`)
_SPEC_ERROR_CLARIFY: dict[str, str] = {
    "future_explicit_period": CLARIFY_FUTURE_EXPLICIT,
    "clock_range_empty": CLARIFY_CLOCK_RANGE_IN_PROGRESS,
}
#: 슬롯 거부 사유 중 오전·오후 되묻기로 올리는 것(`query_time` — 규칙과 같은 되묻기)
SLOT_REASON_CLOCK_MERIDIEM = _CLOCK_MERIDIEM


@dataclass(frozen=True)
class Interpretation:
    """`interpret_detail` 결과 — 해석(또는 None)과 되묻기 사유 코드(해석되면 None)."""

    resolution: TimeResolution | None
    clarify: str | None = None


def _masked(text: str, specs: list[TimeSpec], filtered: list[tuple[int, int]]) -> str:
    chars = list(text)
    spans = [spec.span_pos for spec in specs if spec.span_pos] + filtered
    for s, e in spans:
        chars[s:e] = " " * (e - s)
    return "".join(chars)


def _first_trace(pattern: re.Pattern[str], text: str, masked: str) -> str | None:
    """가린 원문에서 찾은 흔적 중 날짜 속성 조건(「내년에 EoS」)이 아닌 첫 흔적."""
    for m in pattern.finditer(masked):
        if not _is_deadline_filter(text, m.start(), m.end()):
            return m.group(0)
    return None


def _detached_date(text: str, masked: str) -> bool:
    """가린 원문에 시각과 결합하지 못한 날짜 조각(요일·「10/12」·「15일」 흔적)이 남았는가."""
    if _DETACHED_DATE_RE.search(masked):
        return True
    return _first_trace(_TRACE_RE, text, masked) is not None


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
        code = (_SPEC_ERROR_CLARIFY[exc.code] if exc.code in _SPEC_ERROR_CLARIFY
                else CLARIFY_INVALID_CLOCK if exc.code in _INVALID_CLOCK_CODES
                else CLARIFY_INVALID_DATE if exc.code in _INVALID_VALUE_CODES
                else CLARIFY_INVALID_SPEC)
        return Interpretation(None, code)


def _interpret(text: str, now: datetime, subject: Subject) -> Interpretation:
    scan = _recognize(text)
    specs, filtered = scan.found, scan.filtered
    if scan.invalid:
        clock_only = all(code in _INVALID_CLOCK_CODES for *_, code in scan.invalid)
        return Interpretation(None, CLARIFY_INVALID_CLOCK if clock_only else CLARIFY_INVALID_DATE)
    periods = [s for s in specs if s.relation != "none"]
    grains = [s.display_grain for s in specs if s.display_grain != "none"]
    # 입도 표현이 여럿이면 가장 세밀한 것(시 > 일 > 월)
    display: DisplayGrain = (
        min(grains, key=lambda g: {"hour": 0, "day": 1, "month": 2}[g]) if grains else "none"
    )
    masked = _masked(text, specs, filtered)
    if _first_trace(_FUTURE_TRACE_RE, text, masked):
        return Interpretation(None, CLARIFY_FUTURE_RELATIVE)
    if scan.unresolved:  # 「어제 9시 전」·「밤 12시」 — 시각을 잡았지만 해석하지 않았다(plans/156)
        return Interpretation(None, CLARIFY_UNRESOLVED)
    if scan.dateless and any(p.span_pos not in scan.dateless for p in periods):
        # 「지난주 9시」·「최근 7일간 오전 9시」 — 날짜 없는 시각을 오늘로 읽으면 다른 기간과 덮여
        # 구간이 오늘까지 넓어진다. 어느 날의 시각인지 되묻는다(plans/156 W2 교정 V-2)
        return Interpretation(None, CLARIFY_UNRESOLVED)
    if scan.dateless and _detached_date(text, _masked(masked, [], scan.meridiem)):
        # 「15일 서버 오전 9시 알람」 — 시각과 결합하지 못한 날짜 조각이 있으면 오늘로 읽지 않고
        # 되묻는다(plans/156 J-2 · 침묵 오해석 금지)
        return Interpretation(None, CLARIFY_UNRESOLVED)
    if scan.meridiem:  # 「2시 반」 — 오전·오후를 되묻는다(2026-10-14 사용자 확정)
        return Interpretation(None, CLARIFY_CLOCK_MERIDIEM)
    if not periods:
        if _first_trace(_TRACE_RE, text, masked):
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
#: 그대로 싣는다. `start`·`end`는 {year?, year_offset?, month?, day?, hour?, minute?} —
#: 「작년 6월」은 year_offset=-1(계산 금지). minute은 시각의 분(hour 필수 · plans/156 G-1).
SLOT_KEYS: tuple[str, ...] = (
    "relation", "n", "unit", "completeness", "anchor", "start", "end", "display_grain", "span",
)
_PARTIAL_KEYS: tuple[str, ...] = ("year", "year_offset", "month", "day", "hour", "minute")


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
    """스팬 안의 수 — (모든 수, 네 자리 연도). 한글 수사는 어울리는 시간 단위 앞에 올 때만 센다."""
    try:
        nums = {int(x) for x in re.findall(r"\d+", span)}
    except ValueError:  # 정수 변환 자릿수 한도(4300자리) 초과 — 기간 수일 수 없다
        raise TimeSpecError("n_out_of_range", f"span={span[:40]!r}…") from None
    years = {x for x in nums if x >= 1000}
    for word in re.findall(rf"(?<![가-힣])({_KO_NUM})\s*(?:{_KO_UNIT_WORDS})", span):
        nums.add(_KO_NUMERALS[word])
    for word in re.findall(rf"(?<![가-힣])({_SINO_NUM})\s*(?:{_SINO_UNIT_WORDS}){_SINO_END}", span):
        nums.add(_sino_value(word))
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
    totals, rest = _span_hour_minutes(span)
    n_nums = _span_numbers(rest)[0] if totals else nums
    if relation in ("last", "next", "ago") and n is not None and (n_nums or totals):
        # 「1시간 30분」은 한 명세 90분이다 — 그 안의 30·1을 따로 받으면 기간이 잘린다
        derived = n_nums | {x * 7 for x in n_nums} | {x * 12 for x in n_nums} | totals
        if n not in derived:
            raise TimeSpecError("n_mismatch_span", f"n={n} span={span!r}")
    hours, minutes, marked = _span_clock_values(span)
    digit_clock = _SPAN_CLOCK_RE.search(span) is not None
    for p in parts:
        if p.year is not None and p.year not in years:
            # 연도는 스팬에 적혀 있을 때만 받는다 — 「작년」은 year_offset으로(LLM 계산 금지)
            raise TimeSpecError("year_not_in_span", f"{p.year} span={span!r}")
        if not nums:
            continue
        checks = ((p.month, nums), (p.day, nums), (p.hour if digit_clock else None, hours),
                  (p.minute, minutes))
        for value, allowed in checks:
            if value is None or (value == 0 and allowed is minutes):  # 0분 = 정시(「10시 정각」)
                continue
            if value not in allowed:
                raise TimeSpecError("date_mismatch_span", f"{value} span={span!r}")
    slot_hours = [p.hour for p in parts if p.hour is not None]
    if not digit_clock and slot_hours:
        # 숫자로 쓴 시가 없다(「아홉시」·「오후 아홉시」·「정오」) — 한글 수사 시는 1~12시뿐이라
        # 표지가 없으면 hour 값(13~23 포함)과 무관하게 오전·오후를 되묻는다.
        # 표지가 있으면 환산값만 받는다
        # (2026-10-14 사용자 확정 · W2 교정 3)
        word_hours = _span_word_clock_hours(span)
        for hour in slot_hours:
            if hour in word_hours:
                continue
            if not marked and _SPAN_KO_CLOCK_RE.search(span):
                raise TimeSpecError(_CLOCK_MERIDIEM, f"span={span!r}")
            raise TimeSpecError("date_mismatch_span", f"{hour} span={span!r}")
    elif not marked and any(1 <= h <= 12 for h in slot_hours):
        # 표지 없는 1~12시 — 슬롯이 오전으로 읽었어도 규칙과 같이 되묻는다(2026-10-14 사용자 확정)
        raise TimeSpecError(_CLOCK_MERIDIEM, f"span={span!r}")


_MERIDIEM = "오전|오후|새벽|아침|저녁|밤"
# 스팬의 시각(규칙 `_clock_time`과 같은 조각) — 「9~10시」의 앞 9도 시다. 슬롯 hour·minute은 「H시」
# 「M분」 바로 앞의 수로만 대조한다(월·일의 수로 통과하지 않게 — plans/156 W2 교정 V-4)
_SPAN_CLOCK_RE = re.compile(
    rf"(?:(?P<ap>{_MERIDIEM}){_SP})?(?<![\d.:])(?P<h>\d{{1,2}}){_SP}"
    rf"(?:시(?!간)|(?={_SP}[~∼〜\-–]{_SP}(?:(?:{_MERIDIEM}){_SP})?\d{{1,2}}{_SP}시(?!간)))"
    rf"(?:{_SP}(?P<mi>\d{{1,2}}){_SP}{_MIN}|{_SP}(?P<half>반))?"
)
_SPAN_MERIDIEM_RE = re.compile(_MERIDIEM)
# 한글 수사로 쓴 시(「오후 아홉시」·「열두 시」)와 낱말 시(「영시」·「자정」=0 · 「정오」=12)
_SPAN_KO_CLOCK_RE = re.compile(
    rf"(?:(?P<ap>{_MERIDIEM}){_SP}|(?<![가-힣]))(?P<k>{_KO_NUM}){_SP}시(?!간)"
)
_SPAN_ZERO_HOUR_RE = re.compile(r"영시|자정")


def _span_word_clock_hours(span: str) -> set[int]:
    """숫자 시가 없는 스팬의 시 값 — 표지 붙은 한글 수사 시 환산값 · 「영시·자정」 0 · 「정오」 12.

    표지 없는 한글 수사 시는 값을 내지 않는다(오전·오후 모호 — 되묻기).
    """
    hours: set[int] = set()
    if _SPAN_ZERO_HOUR_RE.search(span):
        hours.add(0)
    if "정오" in span:
        hours.add(12)
    for m in _SPAN_KO_CLOCK_RE.finditer(span):
        if m.group("ap"):
            try:
                hours.add(_clock_hour(_KO_NUMERALS[m.group("k")], m.group("ap")))
            except TimeSpecError:
                pass
    return hours
#: 「(시각)부터」 뒤 끝 표현(`_b_clock` · plans/156 J-3) — 「정오까지」·「자정까지」·
#: 「3시간 (동안)」은 끝으로 풀고, 오전·오후 낱말만(「오후까지」)은 되묻는다. 끝을 버리고 기준
#: 시각까지로 풀지 않는다.
#: 「1시간 단위」·「3시간 이상」·「3시간 전」은 끝이 아니다
_SINCE_END_RE = re.compile(
    rf"{_SP}(?:(?P<noon>정오){_SP}까지|(?P<midnight>자정){_SP}까지|(?:{_MERIDIEM}){_SP}까지"
    rf"|(?P<dur>\d{{1,2}}){_SP}시간(?:{_SP}(?:동안|간(?!격)))?"
    rf"(?!{_FREQ_TAIL}|\s*(?:단위|별|평균|전|후|뒤|이내|내|치)))"
)


def _span_clock_values(span: str) -> tuple[set[int], set[int], bool]:
    """슬롯 대조용 스팬 시각 값 — (24시제 시, 분, 표지 유무).

    「오후 2시」는 14만, 표지 없는 「2시」는 2만 시다. 표지 없는 시는 스팬의 표지를 잇는다
    (「오후 2시부터 4시까지」의 16 — 규칙의 범위 끝과 같다). 표지와 맞지 않는 시(「밤 12시」)는
    값을 내지 않는다.
    """
    markers = set(_SPAN_MERIDIEM_RE.findall(span))
    hours: set[int] = set()
    minutes: set[int] = set()
    for m in _SPAN_CLOCK_RE.finditer(span):
        h, ap = int(m.group("h")), m.group("ap")
        if ap is None and not (markers and 1 <= h <= 12):
            hours.add(h)  # 「14시」·「0시」 — 그대로(표지 없는 1~12시는 아래에서 되묻는다)
        for meridiem in ([ap] if ap else markers if 1 <= h <= 12 else ()):
            try:
                hours.add(_clock_hour(h, meridiem))
            except TimeSpecError:
                pass
        if m.group("mi"):
            minutes.add(int(m.group("mi")))
        if m.group("half"):
            minutes.add(30)
    return hours, minutes, bool(markers)


# 스팬의 수 + 단위어(규칙과 같은 `_UNIT`) — 슬롯 `unit` 대조용(plans/156 ⑨). 숫자 앞 경계가
# 없으면 긴 숫자열에서 시작점마다 되짚어 길이의 제곱 시간이 든다
_SPAN_UNIT_RE = re.compile(
    rf"(?:(?<!\d)\d+|(?<![가-힣])(?:{_KO_NUM}|{_SINO_NUM}))\s*{_UNIT}", re.IGNORECASE
)
# 스팬의 「N시간 M분」·「N시간 반」(규칙 `hour_minute`와 같은 조각) — 한 명세 60N+M분이다
_SPAN_HOUR_MINUTE_RE = re.compile(
    rf"(?P<hh>(?<!\d)\d{{1,3}}|(?<![가-힣])(?:{_KO_NUM}|{_SINO_NUM}))\s*시간\s*"
    rf"(?P<mpart>{_MINUTE_PART})"
)


def _span_hour_minutes(span: str) -> tuple[set[int], str]:
    """스팬의 「N시간 M분|반」 합성 분 값들과, 그 부분을 공백으로 가린 나머지 스팬."""
    totals: set[int] = set()
    chars = list(span)
    for m in _SPAN_HOUR_MINUTE_RE.finditer(span):
        part = _compact(m.group("mpart"))
        minutes = 30 if part.startswith("반") else int(re.sub(r"\D", "", part))
        totals.add(_num(m.group("hh")) * 60 + minutes)
        chars[m.start():m.end()] = " " * (m.end() - m.start())
    return totals, "".join(chars)
# 주는 일×7, 연은 월×12로 환산되므로(`_last_n`) 서로 맞는 단위로 본다 — `n` 대조의 derived와 같다
_UNIT_EQUIV: dict[str, frozenset[str]] = {
    "day": frozenset({"day", "week"}), "week": frozenset({"week", "day"}),
    "month": frozenset({"month", "year"}), "year": frozenset({"year", "month"}),
}


def _check_span_unit(relation: str, unit: str | None, span: str) -> None:
    """슬롯 `unit`이 스팬의 단위어와 맞는가(「30 min」을 30시간으로 읽는 슬롯 차단).

    스팬에 수 + 단위어가 없으면(「이번 주」·「지난 한 달」 외 비수치) 검사하지 않는다.
    """
    if relation not in ("last", "next", "ago") or unit is None:
        return
    totals, rest = _span_hour_minutes(span)
    found = {_unit_of(m.group("unit")) for m in _SPAN_UNIT_RE.finditer(rest)}
    if totals:
        found.add("minute")  # 「1시간 반」 = 90분 — 시간이 아니라 분 명세다
    if found and not found & _UNIT_EQUIV.get(unit, frozenset({unit})):
        raise TimeSpecError("unit_mismatch_span", f"unit={unit} span={span!r}")


def slot_to_spec(slot: Any, text: str) -> tuple[TimeSpec | None, str | None]:
    """LLM 시간 슬롯(enum JSON)을 검증해 `TimeSpec`(source=llm)으로 바꾼다(plans/122 T-3).

    실패하면 `(None, 사유 코드)` — 슬롯을 폐기하고 사유를 로그에 남기는 것은 호출부 몫이다.
    사유 코드: slot_not_object · relation_missing · invalid_relation · no_time_expression ·
    span_missing · span_not_in_text(환각 기간 차단) · deadline_value(지원 종료·만료 같은 날짜 속성
    조건의 값 — 조회 기간 아님) · invalid_n · n_out_of_range ·
    n_mismatch_span · unit_mismatch_span(스팬 단위어와 다른 `unit` — plans/156) ·
    minute_not_period(시각 뒤 분을 기간 unit=minute으로 낸 슬롯·「5분 간격」 — D-328) ·
    freq_not_period(「3시간 이상 걸린」·「3일 간격」 — 시·일 이상 단위 빈도·수량 · plans/156) ·
    invalid_unit ·
    unit_missing · invalid_completeness · invalid_anchor ·
    anchor_data_latest_unsupported(G-15) · invalid_display_grain · invalid_partial_date ·
    month_out_of_range · day_out_of_range · hour_out_of_range · minute_out_of_range ·
    year_out_of_range ·
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
        if relation != "none" and (
            _is_deadline_filter(text, *pos) or _ATTR_DEADLINE_RE.search(text, *pos)
        ):
            # 「2027년에 EoS」 — 날짜 속성 조건의 값(규칙 `_recognize`와 같은 판정)
            return None, "deadline_value"
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
        if unit == "minute" and _span_hits((_CLOCK_MINUTE_RE, _FREQ_RE), text, *pos):
            # 「10시 30분부터」·「5분 간격」 — 규칙이 일부러 흔적·비기간으로 둔 분(D-328 ⑤⑧)
            raise TimeSpecError("minute_not_period", text[pos[0]:pos[1]])
        if unit is not None and _span_hits((_FREQ_RE,), text, *pos):
            # 「3시간 이상 걸린」·「3일 간격」 — 시·일 이상 단위의 빈도·수량(plans/156 G-5 (a))
            raise TimeSpecError("freq_not_period", text[pos[0]:pos[1]])
        # 값 범위(TimeSpec 검증) 다음에 스팬 대조 — 사유 코드가 더 근본적인 쪽을 먼저 낸다
        _check_span_values(relation, n, [p for p in (start, end) if p is not None], span)
        _check_span_unit(relation, unit, span)
    except TimeSpecError as exc:
        return None, exc.code
    return spec, None


__all__ = [
    "CLARIFY_CLOCK_MERIDIEM", "CLARIFY_CLOCK_RANGE_IN_PROGRESS",
    "CLARIFY_CODES", "CLARIFY_FUTURE_EXPLICIT",
    "CLARIFY_FUTURE_RELATIVE", "CLARIFY_INVALID_CLOCK", "CLARIFY_INVALID_DATE",
    "CLARIFY_INVALID_SPEC", "CLARIFY_UNRESOLVED", "SLOT_KEYS", "SLOT_REASON_CLOCK_MERIDIEM",
    "Interpretation", "interpret", "interpret_detail", "recognize", "recognize_detail",
    "slot_to_spec",
]
