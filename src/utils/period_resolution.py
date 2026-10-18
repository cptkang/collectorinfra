"""기간 표현 해석 원시 함수 — 월 산술·월 범위/반기 정규식·LLM 기간 환산·비교 기간 타입.

plans/166 N2(D-337)로 `src/utils/query_gen_common.py`에서 옮겼다(동작 불변 Move-only).
`date.today()`를 읽는 진입 함수(`resolve_stat_month_range`·`resolve_comparison_periods`·
`current_month_daily_range`·`build_stat_month_block`)는 테스트가 `query_gen_common.date`를
고정하므로(D-337 ②) 원 모듈에 남고, 이 모듈의 함수를 기준일 인자로 호출한다.
옮긴 이름은 `query_gen_common`이 모두 재노출한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

# 절대 월 **범위** 표현(D-185): "1월부터 6월까지" / "2026년 1월~6월" / "1월에서 6월" /
# "2026-01~2026-06". 연도는 양끝 모두 선택. 오탐 차단: 각 끝점은 '월' 접미 또는 연도 중
# 하나를 반드시 가진다("1-6" 무매칭).
# 금감원 감사자료 폼필 실측(2026-08-25): "1월부터 6월까지"가 어느 정규식에도 안 잡혀 기준월이
# 기본값(지난달=M+5 → 2~7월)으로 침묵 폴백했다. 정규식 1순위 원칙(Known Mistakes)으로 결정적 해석.
_MONTH_RANGE_RE = re.compile(
    r"(?:(?P<y1>\d{4})\s*(?:년\s*|[-/.]))?(?<![\d.])(?P<m1>\d{1,2})\s*(?P<w1>월)?\s*"
    r"(?:부터|에서|~|∼|〜|-|–|—)\s*"
    r"(?:(?P<y2>\d{4})\s*(?:년\s*|[-/.]))?(?<![\d.])(?P<m2>\d{1,2})\s*(?P<w2>월)?"
)
# 반기 표현(D-185): "(2026년|올해|작년) 상반기/하반기" — 연도 미상은 "미래가 아닌 가장 최근 반기".
_HALF_YEAR_RE = re.compile(
    r"(?:(?P<y>\d{4})\s*년\s*|(?P<rel>올해|금년|당해|작년|전년|지난해)\s*)?(?P<half>상반기|하반기)"
)

# 기간 필터 표현: 단일 월 "YYYYMM" 또는 (시작월, 끝월) 범위. None이면 기간 표현 없음.
StatMonth = str | tuple[str, str] | None


def months_from_parsed_time_range(
    parsed_time_range: dict[Any, Any] | None, ref: date
) -> tuple[str, str] | None:
    """input_parser LLM 산출물 ``time_range``를 통계 월 범위로 환산한다(R3 2단 폴백).

    shape는 `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`(둘 다 ISO 8601, 미지정 시 None)로
    `src/prompts/input_parser.py`가 규정하고 `input_parser`가 `parsed_requirements`에 세팅한다.
    한쪽만 있으면 그 달 단일 범위로 본다.

    끝 월은 **직전 완결 월까지로 절단**한다 — 진행 중인 달의 월 통계는 미완결이고, LLM이
    진행 중인 달까지 포함하는 기간을 재계산해 서버가 중복·왜곡됐던 실측(D-076 후속4)이
    폴백 경로로 되살아나는 것을 막는다. 시작 월이 당월이면(당월만 요구) 절단하지 않는다.
    """
    if not isinstance(parsed_time_range, dict):
        return None
    start_ym = _iso_to_month(parsed_time_range.get("start"))
    end_ym = _iso_to_month(parsed_time_range.get("end"))
    if not start_ym and not end_ym:
        return None
    start_ym = start_ym or end_ym
    end_ym = end_ym or start_ym
    if start_ym > end_ym:  # type: ignore[operator]
        start_ym, end_ym = end_ym, start_ym
    last_closed = month_shift(ref, -1)
    end_ym = max(min(end_ym, last_closed), start_ym)  # type: ignore[type-var]
    return (start_ym, end_ym)  # type: ignore[return-value]


def _iso_to_month(value: object) -> str | None:
    """ISO 8601 날짜/월 문자열("2026-06-15"·"2026-06")을 YYYYMM으로 바꾼다(형식 불일치 시 None)."""
    if not isinstance(value, str):
        return None
    m = re.match(r"\s*(\d{4})-(\d{1,2})", value)
    if not m:
        return None
    month = int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{m.group(1)}{month:02d}"


def month_shift(ref: date, delta: int) -> str:
    """ref 기준 delta개월 이동한 월을 YYYYMM 문자열로 반환한다(delta<0이면 과거)."""
    total = ref.year * 12 + (ref.month - 1) + delta
    return f"{total // 12}{total % 12 + 1:02d}"


def _infer_year_not_future(month: int, ref: date) -> int:
    """연도 미상 월의 연도를 "미래가 아닌 가장 최근 발생"으로 보정한다(당월 포함).

    "1월부터 6월까지"를 8월에 물으면 올해 1~6월, "11월부터 2월까지"를 8월에 물으면
    작년 11월~올해 2월이 된다(끝 월 기준). 진행 중인 당월은 명시 요청이므로 허용
    (`_CUR_MONTH_SIGNALS`와 동형 — 완결 월 절단은 상대 표현에만 적용).
    """
    return ref.year if month <= ref.month else ref.year - 1


def resolve_month_range_expr(text: str, ref: date) -> tuple[str, str] | None:
    """절대 월 범위 표현("1월부터 6월까지"·"2026년 1월~6월"·"2026-01~2026-06")을 해석한다(D-185).

    각 끝점은 '월' 접미 또는 연도를 가져야 한다("1-6"·"3-5개" 등 숫자 범위 오탐 차단).
    연도 규칙: 끝 월 연도 미상이면 시작 월 연도(있으면) 또는 미래가 아닌 최근 발생;
    시작 월 연도 미상이면 끝 월 연도, 단 시작>끝이면 전년(연말→연초 범위).
    시작>끝으로 남으면(예: "2026년 6월부터 2025년 1월") 정렬해 반환한다.
    """
    for m in _MONTH_RANGE_RE.finditer(text):
        g = m.groupdict()
        if not (g["w1"] or g["y1"]) or not (g["w2"] or g["y2"]):
            continue
        m1, m2 = int(g["m1"]), int(g["m2"])
        if not (1 <= m1 <= 12 and 1 <= m2 <= 12):
            continue
        y2 = int(g["y2"]) if g["y2"] else None
        y1 = int(g["y1"]) if g["y1"] else None
        if y2 is None:
            if y1 is not None:
                y2 = y1 + 1 if m2 < m1 else y1
            else:
                y2 = _infer_year_not_future(m2, ref)
        if y1 is None:
            y1 = y2 - 1 if m1 > m2 else y2
        start, end = f"{y1}{m1:02d}", f"{y2}{m2:02d}"
        if start > end:
            start, end = end, start
        return (start, end)
    return None


def resolve_half_year_expr(text: str, ref: date) -> tuple[str, str] | None:
    """반기 표현("2026년 상반기"·"작년 하반기"·"상반기")을 (시작, 끝) 월 범위로 해석한다(D-185)."""
    m = _HALF_YEAR_RE.search(text)
    if not m:
        return None
    start_m, end_m = (1, 6) if m.group("half") == "상반기" else (7, 12)
    if m.group("y"):
        year = int(m.group("y"))
    elif m.group("rel") in ("작년", "전년", "지난해"):
        year = ref.year - 1
    elif m.group("rel"):
        year = ref.year
    else:
        # 연도 미상: 아직 시작하지 않은 반기면 전년 것으로 본다(미래 기간 조회 방지)
        year = ref.year if start_m <= ref.month else ref.year - 1
    return (f"{year}{start_m:02d}", f"{year}{end_m:02d}")


# ──────────────────────────────────────────────
# 기간 대비 비교(급증) 결과 타입 — Plan 82 Wave 9 · D-176 후속2
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class ComparisonPeriods:
    """비교 기간 쌍 — (기준 월, 비교 월). 둘 다 YYYYMM."""

    base_month: str
    cur_month: str


@dataclass(frozen=True)
class BlockedComparison:
    """열려 있지 않은 비교 요청. **사유와 대체 제안을 반드시 응답에 표기**한다.

    약속하고 조용히 누락시키는 것이 최악이다 — 지원하지 않는다고 말하면 사용자가
    다른 방법을 찾지만, 침묵하면 틀린 답을 믿는다(§6.12 ③).
    """

    reason: str
    suggestion: str


def previous_month(stat_month: str) -> str:
    """YYYYMM 문자열의 직전 월을 돌려준다(연 경계 처리)."""
    year, month = int(stat_month[:4]), int(stat_month[4:6])
    total = year * 12 + (month - 1) - 1
    return f"{total // 12}{total % 12 + 1:02d}"


def normalize_stat_month(stat_month: StatMonth) -> tuple[str, str] | None:
    """stat_month 인자(단일 월 문자열 또는 범위 튜플)를 (시작, 끝) 범위로 정규화한다."""
    if not stat_month:
        return None
    if isinstance(stat_month, str):
        return (stat_month, stat_month)
    start, end = stat_month
    return (start, end)


# 하위호환 별칭(D-337 ③ — plans/166 N2) — `query_gen_common`이 옛 이름으로 import한다.
# 다음 계획에서 `grep -rw <이름>` 0 확인 후 제거한다.
_months_from_parsed_time_range = months_from_parsed_time_range
_month_shift = month_shift
_resolve_month_range_expr = resolve_month_range_expr
_resolve_half_year_expr = resolve_half_year_expr
