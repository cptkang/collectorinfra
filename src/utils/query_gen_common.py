"""query_generator / multi_db_executor 공통 SQL 생성 헬퍼 (D-066).

단일 DB 경로(`query_generator` 노드)와 멀티 DB 경로(`multi_db_executor` 노드)가
갈라지면서 (1) few-shot 쿼리 예시 주입과 (2) "전체/모든" 조회 LIMIT 상향이 단일
경로에만 존재해, 멀티 DB 폼필(공동존=gp+yd 등)이 예시 없이·LIMIT 1000으로 열화됐다.
두 로직을 단일 출처로 공유하여 경로 간 SQL 품질 비대칭을 제거한다.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from src.utils.json_extract import coerce_content_text

logger = logging.getLogger(__name__)

_PREV_MONTH_SIGNALS: tuple[str, ...] = (
    "지난달", "지난 달", "전월", "저번달", "저번 달", "지난1개월", "지난 1개월",
    "지난달 1개월", "last month", "previous month",
)
_CUR_MONTH_SIGNALS: tuple[str, ...] = ("이번달", "이번 달", "당월", "금월", "this month", "current month")
# 절대 월 표현: "2026년 6월" / "2026-06" / "2026/6" (상대 표현보다 우선 — 더 명시적).
# '개월'(지난 3개월)은 매칭되지 않는다(년 접두 + 월 종결 요구).
_ABS_MONTH_RE = re.compile(r"(\d{4})\s*(?:년\s*|[-/])\s*(\d{1,2})\s*월?")
# "지난/최근/과거 N개월(간)" — 직전 완결 월부터 N개월 범위(진행 중인 달 제외, D-076 후속4 원칙 유지)
_N_MONTHS_RE = re.compile(r"(?:지난|최근|과거|last)\s*(\d{1,2})\s*(?:개\s*월|months?)")
# 절대 월 **범위** 표현(D-176): "1월부터 6월까지" / "2026년 1월~6월" / "1월에서 6월" / "2026-01~2026-06".
# 연도는 양끝 모두 선택. 오탐 차단: 각 끝점은 '월' 접미 또는 연도 중 하나를 반드시 가진다("1-6" 무매칭).
# 금감원 감사자료 폼필 실측(2026-08-25): "1월부터 6월까지"가 어느 정규식에도 안 잡혀 기준월이
# 기본값(지난달=M+5 → 2~7월)으로 침묵 폴백했다. 정규식 1순위 원칙(Known Mistakes)으로 결정적 해석.
_MONTH_RANGE_RE = re.compile(
    r"(?:(?P<y1>\d{4})\s*(?:년\s*|[-/.]))?(?<![\d.])(?P<m1>\d{1,2})\s*(?P<w1>월)?\s*"
    r"(?:부터|에서|~|∼|〜|-|–|—)\s*"
    r"(?:(?P<y2>\d{4})\s*(?:년\s*|[-/.]))?(?<![\d.])(?P<m2>\d{1,2})\s*(?P<w2>월)?"
)
# 반기 표현(D-176): "(2026년|올해|작년) 상반기/하반기" — 연도 미상은 "미래가 아닌 가장 최근 반기".
_HALF_YEAR_RE = re.compile(
    r"(?:(?P<y>\d{4})\s*년\s*|(?P<rel>올해|금년|당해|작년|전년|지난해)\s*)?(?P<half>상반기|하반기)"
)

# 기간 필터 표현: 단일 월 "YYYYMM" 또는 (시작월, 끝월) 범위. None이면 기간 표현 없음.
StatMonth = str | tuple[str, str] | None


# ──────────────────────────────────────────────
# 표면어 해석 폴백 계측 (Plan 67 R3 — 정규식 미커버율 실측 재료)
# ──────────────────────────────────────────────

#: 이름 → 누적 발동 횟수. 정규식 1순위가 미매칭이라 LLM 산출물(parsed_requirements) 폴백이
#: 발동한 횟수와, 스코프 표면어 경계 판정이 오탐을 걸러낸 횟수를 계측한다. utils 계층은
#: `nodes.semantic_compiler.note_guard`를 참조할 수 없어(역방향 금지) 같은 로그 스타일의
#: 독립 카운터를 둔다. **계측만** 하고 정규식은 제거하지 않는다(검토 §4.3 되돌림 이력).
_FALLBACK_COUNTERS: dict[str, int] = {}

FALLBACK_TIME_RANGE_LLM = "interpret.time_range_llm_fallback"  # 기간 표현 미매칭 → LLM 산출물 채택
FALLBACK_LIMIT_LLM = "interpret.limit_llm_fallback"            # 건수 표현 미매칭 → LLM 산출물 채택
FALLBACK_ALL_SCOPE_REJECT = "interpret.all_scope_boundary_reject"  # 전체 스코프 표면어 오탐 차단


def note_fallback(name: str, detail: str = "") -> None:
    """표면어 해석 폴백·경계 판정의 발동을 계측한다(R3/R4 — 미커버율 비교 재료).

    Args:
        name: 폴백 식별자(``FALLBACK_*`` 상수)
        detail: 로그에 남길 부가 사유(선택)
    """
    _FALLBACK_COUNTERS[name] = _FALLBACK_COUNTERS.get(name, 0) + 1
    logger.info(
        "[가드계측] %s 발동(누적 %d)%s",
        name, _FALLBACK_COUNTERS[name], f" — {detail}" if detail else "",
    )


def fallback_counters() -> dict[str, int]:
    """폴백 발동 누적 카운터의 사본을 반환한다."""
    return dict(_FALLBACK_COUNTERS)


def reset_fallback_counters() -> None:
    """폴백 발동 카운터를 초기화한다(계측 구간 분리·테스트용)."""
    _FALLBACK_COUNTERS.clear()


def _months_from_parsed_time_range(
    parsed_time_range: dict | None, ref: date
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
    if start_ym > end_ym:
        start_ym, end_ym = end_ym, start_ym
    last_closed = _month_shift(ref, -1)
    end_ym = max(min(end_ym, last_closed), start_ym)
    return (start_ym, end_ym)


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


def _month_shift(ref: date, delta: int) -> str:
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


def _resolve_month_range_expr(text: str, ref: date) -> tuple[str, str] | None:
    """절대 월 범위 표현("1월부터 6월까지"·"2026년 1월~6월"·"2026-01~2026-06")을 해석한다(D-176).

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


def _resolve_half_year_expr(text: str, ref: date) -> tuple[str, str] | None:
    """반기 표현("2026년 상반기"·"작년 하반기"·"상반기")을 (시작, 끝) 월 범위로 해석한다(D-176)."""
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


def resolve_stat_month_range(
    user_query: str | None,
    today: date | None = None,
    *,
    parsed_time_range: dict | None = None,
) -> tuple[str, str] | None:
    """질의의 기간 표현을 사용률 통계 월 범위 (시작 YYYYMM, 끝 YYYYMM)로 해석한다(없으면 None).

    "지난/최근 N개월" → 직전 완결 월부터 과거 N개월 범위(진행 중인 달 제외),
    "지난달"/"전월" → 직전 월 단일, "이번달"/"당월" → 당월 단일. 그 외에는 None(전체 월 평균).
    폼필 SQL을 코드가 결정적으로 조립할 때 `s.stat_date` 필터에 사용한다(D-102).
    N=1이면 "지난달"과 동일한 (직전월, 직전월)이라 종전 동작과 호환된다.

    Args:
        user_query: 사용자 원문 질의
        today: 상대 표현의 기준일(기본 오늘)
        parsed_time_range: `parsed_requirements["time_range"]`(input_parser LLM 산출물).
            **정규식이 미매칭일 때만** 2단 폴백으로 사용한다 — "지난 반년"·"작년 6월"처럼
            정규식이 못 잡는 표현이 침묵 소실(전 기간 평균)되던 것을 회복한다(Plan 67 R3-(i),
            `docs/regex_llm_conversion_review.md` §4.4). 미지정(None)이면 종전 동작 그대로.
    """
    text = user_query or ""
    ref = today or date.today()
    # 절대 월 **범위**·반기 표현이 최우선(D-176) — "2026년 1월부터 6월까지"가 아래 단일 월
    # 정규식(.search=첫 매치)에 1월 단일로 오해석되던 것을 막는다. 범위가 없을 때만 내려간다.
    rng = _resolve_month_range_expr(text, ref) or _resolve_half_year_expr(text, ref)
    if rng:
        return rng
    # 절대 월 표현이 있으면 최우선(가장 명시적) — 없으면 기존 상대 표현 해석으로 내려간다.
    # 과거에는 절대 월이 None을 반환해 결정적 조립 SQL에 기간 필터가 빠지고 전 기간 평균으로
    # 순위가 뒤집혔다(2026-07-20 라이브 실측 — D-099). 범위 반환(D-102) 계약에 맞춰 (월, 월).
    abs_m = _ABS_MONTH_RE.search(text)
    if abs_m:
        year, month = int(abs_m.group(1)), int(abs_m.group(2))
        if 1 <= month <= 12:
            ym = f"{year}{month:02d}"
            return (ym, ym)
    m = _N_MONTHS_RE.search(text)
    if m and int(m.group(1)) >= 1:
        n = int(m.group(1))
        return (_month_shift(ref, -n), _month_shift(ref, -1))
    if any(sig in text for sig in _PREV_MONTH_SIGNALS):
        prev = _month_shift(ref, -1)
        return (prev, prev)
    if any(sig in text for sig in _CUR_MONTH_SIGNALS):
        cur = _month_shift(ref, 0)
        return (cur, cur)
    # 2단 폴백(R3-(i)): 정규식 전건 미매칭 → 이미 계산돼 있던 LLM 기간 산출물을 채택한다.
    llm_range = _months_from_parsed_time_range(parsed_time_range, ref)
    if llm_range:
        note_fallback(FALLBACK_TIME_RANGE_LLM, f"{parsed_time_range} → {llm_range}")
        return llm_range
    return None


def normalize_stat_month(stat_month: StatMonth) -> tuple[str, str] | None:
    """stat_month 인자(단일 월 문자열 또는 범위 튜플)를 (시작, 끝) 범위로 정규화한다."""
    if not stat_month:
        return None
    if isinstance(stat_month, str):
        return (stat_month, stat_month)
    start, end = stat_month
    return (start, end)

def build_stat_month_block(
    stat_month: StatMonth = None, metric_table: str = "cmm_metric_stat_m"
) -> str:
    """질의 기간 표현의 결정적 해석(YYYYMM 단일 월)을 LLM 폴백 프롬프트에 강제하는 블록.

    "지난 1개월/지난달" 질의에서 LLM이 시스템 템플릿의 일반 규칙("하드코딩 날짜 금지 —
    CURRENT_DATE 동적 계산")을 따라 `BETWEEN 직전월 AND 이번달`처럼 진행 중인 달까지 포함하는
    기간을 재계산하고 월별 GROUP BY로 서버를 중복 출력하는 실측 사례가 있었다(D-076 후속4).
    코드가 이미 해석한 월/범위(`resolve_stat_month_range`)를 등호·BETWEEN 필터로 강제해
    기간 해석을 결정화한다(N개월 범위는 D-102).
    단일 DB(query_generator)·멀티 DB(multi_db_executor) 폴백 경로가 공유한다(D-066 단일 출처).

    Args:
        stat_month: resolve_stat_month_range 결과 (시작, 끝) 범위 또는 단일 월 YYYYMM 문자열
            (None이면 기간 표현 없음 → 빈 문자열)
        metric_table: 월별 통계 테이블명

    Returns:
        프롬프트에 덧붙일 섹션 텍스트(선행 개행 없음). stat_month가 없으면 "".
    """
    rng = _normalize_stat_month(stat_month)
    if not rng:
        return ""
    start, end = rng
    if start == end:
        filter_line = f"`s.stat_date = '{start}'` (단일 월 등호 필터)"
    else:
        filter_line = f"`s.stat_date BETWEEN '{start}' AND '{end}'` (완결 월 범위 필터)"
    return (
        "## 기간 조건 (시스템이 결정적으로 해석 — 최우선 준수)\n"
        f"질의의 기간 표현은 이미 월 통계 기준으로 해석되었습니다. {metric_table} 조인에 반드시 "
        f"{filter_line}를 사용하세요.\n"
        "- 이 지시는 '하드코딩 날짜 금지·CURRENT_DATE 동적 계산' 일반 규칙보다 **우선**합니다"
        "(이 값은 시스템이 계산해 주입한 것으로 하드코딩이 아닙니다).\n"
        "- BETWEEN·INTERVAL 재계산으로 진행 중인 달을 포함하지 마세요(위 값 그대로 사용).\n"
        "- 시간별(_h)/일별(_d) 테이블로 대체하지 마세요."
    )


def build_generic_period_hint(stat_month: StatMonth) -> str:
    """무선언(프로필 없음) DB용 범용 기간 해석 힌트 — 특정 DB 스키마 리터럴 없음(Plan 63 P3, D-090).

    프로필/시맨틱 모델이 없는 DB는 통계 테이블명·기간 컬럼이 선언돼 있지 않다. 특정 DB의 통계 테이블·
    기간 컬럼 규약에 특화된 `build_stat_month_block`(폴스타 게이트)과 달리, 코드가 해석한 월(YYYYMM)만
    알려주고 **대상 스키마에 실제 존재하는 시간/날짜 컬럼**으로 매핑하도록 LLM에 위임한다(테이블/컬럼 지어내기 금지).
    `GENERIC_LLM_MAPPING` 옵트인(기본 OFF)일 때만 주입한다.
    """
    rng = _normalize_stat_month(stat_month)
    if not rng:
        return ""
    start, end = rng
    period = f"'{start}'" if start == end else f"'{start}'~'{end}'"
    return (
        "## 기간 조건 해석 (참고)\n"
        f"질의의 기간 표현은 월(YYYYMM) {period}로 해석되었습니다. 대상 스키마에 **실제 존재하는**"
        " 시간/날짜 컬럼에 이 월을 필터로 적용하세요.\n"
        "- 스키마에 없는 테이블/컬럼을 지어내지 마세요(환각 금지).\n"
        "- 진행 중인 달을 포함하는 BETWEEN·INTERVAL 재계산으로 서버가 중복되지 않게 하세요."
    )


# "전체/모든/모두" 조회는 기본 LIMIT(default_limit)로 절단하면 안 되므로 상향한다.
# 2026-07-24 실측 확장: "서버별 CPU/메모리 사용률 평균을 … 보여줘"처럼 **"모든" 없이도
# 전 서버 나열을 의도하는 표면어**("서버별"/"서버들"/"각 서버")가 기본 LIMIT(1000)에
# 절단되고 존 역질문 게이트도 비발동(침묵 전 존 폴백)했다 — 두 게이트가 공유하는 이
# 집합을 실사용 표현으로 확장한다. 명시 건수("100건"/"상위 N")는 여전히 우선.
_ALL_QUERY_KEYWORDS: tuple[str, ...] = (
    "모든", "전체", "모두", "서버별", "서버 별", "서버들", "각 서버",
)
_ALL_QUERY_LIMIT: int = 10_000  # spec.md '최대 반환 행 수 10,000행' 정합(Plan 69 §0.3-5 —
# 감사 이력 5,372건 실측 최대 1,000행, 초과 실적 0 확인 후 하향. 2026-08-04)

# 스코프 표면어 뒤에 붙으면 낱말의 품사가 바뀌어 "전체 조회" 의미가 아닌 파생 접미사.
# "**전체**적으로 CPU 높은 서버"가 LIMIT 100000으로 상향되던 오탐(검토 §4.2 A6) 차단용.
# 조사(전체를/전체의)·합성(전체서버)은 정상 스코프 지시라 그대로 인정한다 — 표면어 자체를
# 제거하지 않고 경계만 좁힌다(Plan 67 R3-(iii): 제거 금지, R4 계측 대상 유지).
_ALL_SCOPE_DERIV_SUFFIXES: tuple[str, ...] = ("적", "화")


def has_all_scope_keyword(text: str | None) -> bool:
    """질의가 "전체/모든/모두" 스코프 지시를 담고 있는지 조사·파생 경계까지 보고 판정한다.

    LIMIT 상향(`resolve_query_limit`)과 LIMIT 자동 추가 스킵(`query_validator`)이 같은
    판정을 공유하도록 단일 출처로 둔다(종전에는 두 곳이 각자 부분문자열 매칭 튜플을 들고 있었다).

    Args:
        text: 사용자 원문 질의

    Returns:
        전체 스코프 지시가 있으면 True
    """
    body = text or ""
    for kw in _ALL_QUERY_KEYWORDS:
        start = body.find(kw)
        while start != -1:
            tail = body[start + len(kw):]
            if not tail.startswith(_ALL_SCOPE_DERIV_SUFFIXES):
                return True
            note_fallback(FALLBACK_ALL_SCOPE_REJECT, f"{kw}{tail[:2]}")
            start = body.find(kw, start + 1)
    return False


def is_full_scan_query(user_query: str | None) -> bool:
    """질의가 전량 나열 의도(대량 조회 표면어 포함)인지 결정적으로 판정한다.

    resolve_query_limit의 LIMIT 상향과 존 역질문 게이트(Plan 75 §4)가 같은 판정을
    공유한다 — 한쪽만 넓히는 비대칭 방지(D-066 계열).
    """
    text = user_query or ""
    return any(k in text for k in _ALL_QUERY_KEYWORDS)

# 명시 건수 표현("100건", "상위 10개") — "건"은 레코드 수 전용 조사라 안전. 단독 "개"는
# "개월"·"4개인 서버" 등 수량 한정과 혼동되므로 "상위 N(개)" 꼴에서만 인정한다.
_EXPLICIT_COUNT_RE = re.compile(r"(\d{1,6})\s*건")
_TOP_N_RE = re.compile(r"상위\s*(\d{1,6})")


def resolve_query_limit(
    user_query: str | None,
    default_limit: int,
    *,
    parsed_limit: object = None,
) -> int:
    """질의의 명시 건수("100건"/"상위 10")를 최우선 반영하고, "전체/모든/모두"면 상향, 아니면 기본값.

    단일 DB 경로(query_generator)와 동일한 규칙을 멀티 DB 경로에도 적용하기 위한 공용 함수.
    실측(2026-07-21 yd-006): "…100건 조회해줘"가 기본 LIMIT(1000)로 나가 골드(100행)와 행수
    불일치 — 명시 건수는 결정적으로 파싱한다.

    Args:
        user_query: 사용자 원문 질의
        default_limit: 일반 조회 기본 LIMIT
        parsed_limit: `parsed_requirements["limit"]`(input_parser LLM 산출물). **결정적 판정이
            전부 미매칭일 때만** 2단 폴백으로 사용한다 — "100개만"·"백 건"·"10줄"처럼 정규식이
            못 잡는 표현이 기본 LIMIT으로 흐르던 것을 회복한다(Plan 67 R3-(i)).
            미지정(None)이면 종전 동작 그대로.

    Returns:
        적용할 LIMIT 값
    """
    text = user_query or ""
    m = _EXPLICIT_COUNT_RE.search(text) or _TOP_N_RE.search(text)
    if m:
        n = int(m.group(1))
        if n > 0:
            return min(n, _ALL_QUERY_LIMIT)
    if has_all_scope_keyword(text):
        return _ALL_QUERY_LIMIT
    # 2단 폴백(R3-(i)): 표면어 미매칭 → 이미 계산돼 있던 LLM 건수 산출물을 채택한다.
    if isinstance(parsed_limit, bool) or not isinstance(parsed_limit, int):
        return default_limit
    if parsed_limit <= 0:
        return default_limit
    limit = min(parsed_limit, _ALL_QUERY_LIMIT)
    note_fallback(FALLBACK_LIMIT_LLM, f"limit={limit}")
    return limit


def resolve_effective_limit(
    state: dict,
    user_query: str | None,
    default_limit: int,
    parsed_limit: object = None,
) -> int:
    """state에 승격된 원문 기준 limit(resolved_limit)을 우선하고, 없으면 표면어로 계산한다.

    폐쇄망 실측(2026-07-24, Plan 75 §3): 오케스트레이션 단일 DB 경로는 user_query를
    semantic_router 정제 질의(sub_query_context)로 교체하는데, 이 정제(문장 압축)가
    "모든" 등 수량 한정어까지 탈락시켜 resolve_query_limit이 기본 1,000으로 떨어졌다
    (은행존 2,328대 중 1,328대 절단 — 멀티 경로는 sub_query 유지로 미발현, 구조적 비대칭).
    limit 신호는 문자열이 아니라 state(resolved_limit)로 운반해 상류의 어떤 문자열
    훼손과도 무관하게 보존한다. 단일/멀티 두 소비 경로가 이 함수를 공유한다(D-066).

    Args:
        state: 에이전트 상태 (resolved_limit이 승격돼 있으면 그 값을 신뢰)
        user_query: 폴백 계산용 질의 문자열 (그래프 직행 경로에선 원문)
        default_limit: 일반 조회 기본 LIMIT

    Returns:
        적용할 LIMIT 값
    """
    promoted = state.get("resolved_limit")
    if isinstance(promoted, int) and promoted > 0:
        return promoted
    # 표면어 미매칭이면 input_parser LLM 산출물(parsed_limit)로 2단 폴백(Plan 67 R3-(i)) —
    # 단일/멀티 경로 동일 규칙(한쪽만 폴백하는 비대칭 금지).
    return resolve_query_limit(user_query, default_limit, parsed_limit=parsed_limit)


# ── 실시간 사용률 라우팅 게이트 (Plan 71 / Plan 75 §1, B안 확정 2026-07-24) ──
# LLM 의도 분류에 의존하지 않는 결정적 게이트(D-035). B안: "실시간/현재/지금" 명시 +
# CPU/메모리 지표어 + 기간 표현 부재일 때만 실시간 API 경로. "현황" 단독은 비트리거
# (실무 한국어에서 "현황"은 목록/정리 광의 — 기존 DB 질의 습관과 충돌 방지).
_REALTIME_TERMS: tuple[str, ...] = ("실시간", "현재", "지금")
_REALTIME_METRIC_TERMS: tuple[str, ...] = ("cpu", "씨피유", "메모리", "mem")
# 기간/추이 표현이 하나라도 있으면 통계 경로 우선(혼합 질의 오분기 방지 — "지난달 실시간 …").
_PERIOD_TERMS: tuple[str, ...] = ("지난", "개월", "월별", "추이", "통계", "기간", "부터", "까지")


def is_realtime_usage_query(user_query: str | None) -> bool:
    """질의가 실시간 CPU/메모리 사용률 조회(API 경로) 대상인지 결정적으로 판정한다.

    B안(Plan 75 §5.1 항목 1 확정): "실시간/현재/지금" 명시 시에만 — 오분기 비용이
    비대칭(통계 질의가 순간 스냅샷으로 답하는 사고 > 실시간 질의가 몇 분 낡은 DB 값)이라
    좁게 시작한다. 판정은 **원문 기준**이어야 한다(sub_query 재작성으로 표면어가 탈락할
    수 있음 — D-066 후속7과 동일 원리).
    """
    text = (user_query or "").lower()
    if not text:
        return False
    if not any(t in text for t in _REALTIME_TERMS):
        return False
    if not any(t in text for t in _REALTIME_METRIC_TERMS):
        return False
    if any(t in text for t in _PERIOD_TERMS):
        return False
    if resolve_stat_month_range(text) is not None:
        return False
    return True


# 프로필 few-shot 예시(config/db_profiles/*.yaml)가 말미에 일반 캡(FETCH FIRST 100 /
# LIMIT 100)을 달고 있어, LLM이 프롬프트의 LIMIT 지시 대신 예시 캡을 모방하는 사례가
# 실측됐다(2026-07-24: b0 "모든 서버 CPU 사용률" — 지시 limit 100,000인데 SQL은
# FETCH FIRST 100 → 2,328대 중 100행). 지시 vs few-shot 경쟁은 비결정적(OS 질의는
# 지시를 따름)이라 프롬프트 강화로는 부족 — 결정적 후처리로 교정한다(Known Mistakes).
_TRAILING_LIMIT_RE = re.compile(
    r"(?is)(LIMIT\s+(\d+)|FETCH\s+FIRST\s+(\d+)\s+ROWS?\s+ONLY)(\s*;)?\s*$"
)
_GENERIC_EXAMPLE_CAP = 100  # 프로필 query_examples 말미의 관례적 캡
# 역대 기본 LIMIT(1000) — 프로필 예시·LLM 학습 관례에 굳어 있어, 운영이
# QUERY_DEFAULT_LIMIT을 상향(예: 10,000)해도 LLM은 말미 캡 1000을 계속 모방한다.
# config_default만 캡 집합에 넣으면 설정 변경 순간 1000이 교정 집합에서 빠져
# 전량 질의가 1,000건에 절단된다(2026-08-05 라이브 실측: "서버들…조회" 1,000건).
_LEGACY_DEFAULT_CAP = 1000


def enforce_all_query_limit(sql: str, effective_limit: int, config_default_limit: int) -> str:
    """"모든/전체" 상향 질의의 생성 SQL 말미가 일반 캡이면 상향값으로 결정적 교정한다.

    좁은 가드만 적용한다(오교정 방지):
    - effective_limit이 "모든/전체" 상향값(_ALL_QUERY_LIMIT)일 때만 발동 — 명시 건수·기본
      질의는 건드리지 않는다.
    - SQL **말미**의 LIMIT/FETCH FIRST 절만 대상 — 서브쿼리의 `FETCH FIRST 1 ROW ONLY`
      (최신값 조회 패턴)는 보존된다.
    - 말미 값이 일반 캡(예시 관례 100, 설정 기본 LIMIT)일 때만 교체 — `LIMIT 1`(최상위 1건)
      같은 의도적 TOP-N은 캡 집합 밖이라 보존된다.

    Args:
        sql: LLM이 생성한 SQL
        effective_limit: resolve_effective_limit 결과 (원문 기준 확정 LIMIT)
        config_default_limit: 설정상 기본 LIMIT (일반 캡 판정용)

    Returns:
        교정된(또는 원본 그대로의) SQL
    """
    if not sql or effective_limit != _ALL_QUERY_LIMIT:
        return sql
    m = _TRAILING_LIMIT_RE.search(sql)
    if not m:
        return sql
    n = int(m.group(2) or m.group(3))
    if n == effective_limit or n not in {
        _GENERIC_EXAMPLE_CAP, _LEGACY_DEFAULT_CAP, config_default_limit,
    }:
        return sql
    clause = (
        f"LIMIT {effective_limit}" if m.group(2)
        else f"FETCH FIRST {effective_limit} ROWS ONLY"
    )
    return sql[: m.start(1)] + clause + (m.group(4) or "")


# EAV 값 컬럼에 걸린 정수 캐스트 타입(교정 대상). NUMERIC은 PostgreSQL·DB2 공통 유효
# (DB2에서 DECIMAL 동의어)라 방언 분기가 필요 없다. 긴 이름을 앞에 둔다(INT가 INTEGER를
# 부분 선점하지 않도록 — 대안 순서 의존).
_EAV_INT_CAST_TYPES = r"(?:BIGINT|INTEGER|SMALLINT|INT8|INT4|INT2|INT)"


def eav_value_cast_columns(eav_pattern: dict | None) -> tuple[str, ...]:
    """eav_pattern 선언에서 숫자 캐스트 교정 대상 값 컬럼 패밀리를 도출한다 (D-160).

    ``value_column``과 그 단축/전문 변형(``_short`` 접미 유무 쌍)을 함께 반환한다 —
    컬럼 리터럴은 코드가 아니라 구조 메타 선언(프로필)에서만 온다(D-088: 공용 계층
    DB-agnostic). eav_pattern이 없는 DB는 빈 튜플 → 교정 자체가 스킵돼 동작 불변.
    """
    base = (eav_pattern or {}).get("value_column") or ""
    if not base:
        return ()
    if base.endswith("_short"):
        return (base[: -len("_short")], base)
    return (base, base + "_short")


def normalize_eav_numeric_casts(sql: str, value_columns: tuple[str, ...] | list[str]) -> str:
    """EAV 값 컬럼에 걸린 정수 캐스트를 NUMERIC으로 결정적 교정한다 (D-160).

    폐쇄망 실측(2026-08-21 공동존): LLM이 "vcore 수" 집계에
    ``SUM(CAST(<EAV 값 컬럼> AS BIGINT))``를 생성 — EAV 값은 부동소수 표기
    문자열('4.0')이라 PostgreSQL이 ``invalid input syntax for type bigint``로 거부.
    캐스트 선택은 LLM 비결정 영역이라 프롬프트 규칙(프로필 query_guide)만으로는
    재발을 막을 수 없어 후처리로 교정한다(Known Mistakes: 결정적 가드 원칙,
    enforce_all_query_limit·correct_servername_hostname_mapping과 동일 계열).

    좁은 스코프(오교정 방지):
    - **값 컬럼이 포함된** 캐스트만 대상 — ``CAST(r.id AS BIGINT)`` 같은 정당한 정수
      캐스트는 불변.
    - CAST 인자·괄호식은 균형 괄호 스캔으로 식별(``CAST(COALESCE(col,'0') AS INT)``
      같은 임의 깊이 중첩 커버). 괄호 불균형·형태 미상은 무변경(하방 안전 —
      교정 실패는 현행 실패 유지일 뿐 악화 없음).
    - 문자열 리터럴 내부는 구분하지 않는다 — LLM SQL의 리터럴에 캐스트 구문이 들어갈
      확률은 사실상 0이고, 오매칭해도 결과는 여전히 유효 SQL이다.

    Args:
        sql: LLM이 생성한 SQL
        value_columns: 교정 대상 값 컬럼명들 (``eav_value_cast_columns`` 산출)

    Returns:
        교정된(또는 원본 그대로의) SQL — 교정 발생 시 INFO 로그
    """
    if not sql or not value_columns:
        return sql
    cols_re = r"\b(?:" + "|".join(re.escape(c) for c in value_columns if c) + r")\b"
    if cols_re == r"\b(?:)\b":
        return sql

    # 교체 대상 타입 토큰의 (시작, 끝) 스팬을 모은 뒤 오른쪽부터 NUMERIC으로 치환한다.
    spans: list[tuple[int, int]] = []

    # 형태 1: CAST(<값 컬럼 포함 식> AS 정수형) — 인자를 균형 스캔으로 확정
    for m in re.finditer(r"CAST\s*\(", sql, flags=re.IGNORECASE):
        open_pos = m.end() - 1
        close_pos = _matching_paren(sql, open_pos)
        if close_pos is None:
            continue
        inner = sql[open_pos + 1: close_pos]
        tm = re.search(rf"\s+AS\s+({_EAV_INT_CAST_TYPES})\s*$", inner, flags=re.IGNORECASE)
        if not tm:
            continue
        if not re.search(cols_re, inner[: tm.start()], flags=re.IGNORECASE):
            continue
        spans.append((open_pos + 1 + tm.start(1), open_pos + 1 + tm.end(1)))

    # 형태 2: PostgreSQL 축약 캐스트 — <값 컬럼>::정수형
    for m in re.finditer(
        rf"(?:\w+\.)?{cols_re}\s*::\s*({_EAV_INT_CAST_TYPES})\b", sql, flags=re.IGNORECASE
    ):
        spans.append((m.start(1), m.end(1)))

    # 형태 3: (…값 컬럼…)::정수형 — 닫는 괄호에서 역방향 균형 스캔으로 괄호식 확정
    for m in re.finditer(rf"\)\s*::\s*({_EAV_INT_CAST_TYPES})\b", sql, flags=re.IGNORECASE):
        open_pos = _matching_paren_backward(sql, m.start())
        if open_pos is None:
            continue
        if re.search(cols_re, sql[open_pos: m.start() + 1], flags=re.IGNORECASE):
            spans.append((m.start(1), m.end(1)))

    if not spans:
        return sql
    result = sql
    for start, end in sorted(set(spans), reverse=True):
        result = result[:start] + "NUMERIC" + result[end:]
    logger.info(
        "[EAV캐스트] 값 컬럼 정수 캐스트 → NUMERIC 교정 %d건 (cols=%s)",
        len(set(spans)), list(value_columns),
    )
    return result


def _matching_paren(sql: str, open_pos: int) -> int | None:
    """``open_pos``의 여는 괄호와 짝이 되는 닫는 괄호 위치를 찾는다(불균형은 None)."""
    depth = 0
    for i in range(open_pos, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def _matching_paren_backward(sql: str, close_pos: int) -> int | None:
    """``close_pos``의 닫는 괄호와 짝이 되는 여는 괄호 위치를 찾는다(불균형은 None)."""
    depth = 0
    for i in range(close_pos, -1, -1):
        if sql[i] == ")":
            depth += 1
        elif sql[i] == "(":
            depth -= 1
            if depth == 0:
                return i
    return None


# 헤더/필드명이 사용률 지표(명사+집계어)인지 **표면어로만** 판정한다 — DB 스키마 리터럴
# (server.* resource_type 등)을 쓰지 않아 공용 계층 과적합 가드(D-088)를 통과한다.
# 문서 계층(document.excel_writer, infrastructure)이 지표 컬럼 서식 힌트로 사용한다.
# resource_type/집계함수/값컬럼 매핑이 필요한 결정적 조립은 어댑터의 `classify_metric_field`
# (폴스타 특화 리터럴 포함, db_adapters — 가드 스캔 제외)를 쓴다. infra→application 역방향
# 금지 때문에 문서 계층은 이 스키마-무관 헬퍼만 참조한다(2026-07-22 머지 정리).
_METRIC_NOUN_TERMS: tuple[str, ...] = ("cpu", "메모리", "mem", "디스크", "disk", "사용률")
_METRIC_AGG_TERMS: tuple[str, ...] = ("평균", "최고", "최대", "최소", "avg", "max", "min", "peak", "피크")


def is_metric_field_name(field: str) -> bool:
    """헤더/필드명이 사용률 지표(명사+집계어)인지 판정한다(스키마 리터럴 불사용).

    예: "CPU 평균"·"메모리 최고" → True, "메모리 용량"(집계어 없음)·"서버 이름" → False.
    metric 명사와 집계어가 **둘 다** 있어야 인정한다(어댑터 `classify_metric_field`와 동일 규칙).
    """
    low = (field or "").lower()
    has_noun = any(n in low for n in _METRIC_NOUN_TERMS)
    has_agg = any(a in low for a in _METRIC_AGG_TERMS)
    return has_noun and has_agg


def missing_dtime_filter(sql: str) -> bool:
    """cmm_resource를 조회하면서 dtime IS NULL(삭제 리소스 제외) 필터가 전무한지 판정한다.

    폐쇄망 실측(2026-07-21 b0-005): LLM 폴백 SQL이 dtime 필터를 통째로 누락해 삭제된 서버
    약 99대가 결과에 섞임(rows 475 vs 골드 376). 규약상 cmm_resource 조회에는 최소 1회
    `dtime IS NULL`이 필요하다. 별칭(테이블 인스턴스) 단위 강제는 알람 표준 뷰의 부모 서버
    LEFT JOIN(SVR)·계층 조인(C2~C10)처럼 무필터가 정당한 사용에 오탐하므로, "SQL 전체에
    한 번도 없음"만 결정적으로 차단한다. 주석·문자열 리터럴 안의 표기는 제외하고 검사한다.
    단일(query_validator)·멀티(_validate_sql_simple) 검증 경로가 공유한다(D-066).
    """
    body = re.sub(r"--[^\n]*", " ", sql or "")
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S)
    body = re.sub(r"'(?:[^']|'')*'", " ", body)
    if not re.search(r"\bcmm_resource\b", body, re.IGNORECASE):
        return False
    return not re.search(r"\bdtime\s+is\s+(?:not\s+)?null\b", body, re.IGNORECASE)


# cmm_resource 조회에 dtime 필터가 없을 때 재생성을 유도하는 검증 실패 메시지(ASCII 구두점 —
# 평가 하네스 스킵 사유로 cp949 콘솔 출력 가능, Known Mistakes 2026-07-16).
MISSING_DTIME_ERROR = (
    "cmm_resource 조회에 dtime IS NULL(삭제 리소스 제외) 필터가 없습니다 - "
    "삭제된 서버/리소스가 결과에 섞입니다. WHERE에 dtime IS NULL 조건을 추가해 다시 작성하세요."
)


# Utilization(사용률 %) 값의 타당성 게이트 범위(D-086). 실측(gp/yd): 만재 피크가 부동소수
# 슬롭·미세 초과로 100.0000…1~100.1까지 기록되므로 상한 100은 정상 만재 기록을 잘라낸다 —
# 여유를 둔 1000 채택(실측상 100.1과 쓰레기 5.5e13 사이에 값이 없어 그 사이 어떤 경계든 동작 동일).
# 하한 0은 b0 실측 음수(센티널 추정) 21행 차단. Utilization 외 지표(MaxIORate 등)엔 적용 금지
# (0~1000 의미가 없음 — `_metric_select_line`이 definition_name으로 게이팅).
UTILIZATION_VALID_RANGE: tuple[int, int] = (0, 1000)


def drop_entries_missing_columns(
    entries: list[tuple[str, str]],
    schema_info: dict | None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(필드, "table.column") 항목 중 스키마에 없는 칼럼을 걸러낸다(결정적 조립 유입 차단).

    라이브 실측(2026-07-28 gp+yd): field_mapper의 환각 매핑(구분→cmm_resource.category)이
    결정적 피벗 SELECT에 그대로 들어가 `column c.category does not exist`로 쿼리 전체가
    죽었다(멀티 경로의 기존 필터는 **테이블 존재만** 검사). 스키마에 칼럼 목록이 있는
    테이블에 한해 칼럼 부재 항목을 제외한다 — 칼럼 정보가 없으면 제외하지 않는다(오탐 방지).
    비교는 대소문자 무시(DB2 대문자 칼럼).

    Returns:
        (유지 항목, 제외 항목) — 제외 항목은 호출부가 로그로 가시화한다(침묵 금지).
    """
    tables = (schema_info or {}).get("tables") or {}
    cols_by_table: dict[str, set[str]] = {}
    for tname, tinfo in tables.items():
        # 실 런타임 스키마 shape 가변성 대응: 칼럼이 dict({"name":...}) 또는 문자열일 수 있음
        cols = set()
        for c in (tinfo or {}).get("columns", []):
            name = c.get("name") if isinstance(c, dict) else c
            if name:
                cols.add(str(name).lower())
        if not cols:
            continue
        cols_by_table[tname.lower()] = cols
        if "." in tname:
            cols_by_table.setdefault(tname.rsplit(".", 1)[-1].lower(), cols)

    kept: list[tuple[str, str]] = []
    dropped: list[tuple[str, str]] = []
    for field, col in entries:
        parts = str(col).split(".")
        if len(parts) < 2:
            kept.append((field, col))
            continue
        table, column = parts[-2].lower(), parts[-1].lower()
        known = cols_by_table.get(table)
        if known is not None and column not in known:
            dropped.append((field, col))
        else:
            kept.append((field, col))
    return kept, dropped


def template_context_text(template_structure: dict | None) -> str:
    """양식 구조에서 문맥 텍스트(시트 제목 title_text·시트명)를 모은다.

    월 시리즈 인식기(D-146)의 리소스 판정 등 양식 종류 판별에 쓴다. 단일(query_generator)·
    멀티(multi_db_executor) 경로가 공유한다(대칭 — Known Mistakes 단일/멀티 비대칭 방지).
    """
    parts: list[str] = []
    for sheet in (template_structure or {}).get("sheets", []) or []:
        for key in ("title_text", "name"):
            val = sheet.get(key)
            if val:
                parts.append(str(val))
    return " ".join(parts)


def utilization_guard(val_col: str, definition_name: str) -> str:
    """Utilization일 때만 값 타당성 게이트 조건(` AND s.<col> BETWEEN 0 AND 1000`)을 반환한다."""
    if definition_name != "Utilization":
        return ""
    lo, hi = UTILIZATION_VALID_RANGE
    return f" AND s.{val_col} BETWEEN {lo} AND {hi}"


# 서버 등록명(cmm_resource.name) 의미의 폼 필드 표면어(공백 제거·소문자 정규화 후 비교).
# 프로필이 명시하듯 이 표현들은 EAV Hostname(=호스트명)이 아니라 등록명 컬럼이다.
_SERVER_NAME_TERMS = frozenset({
    "서버명", "서버이름", "장비명", "장비이름", "리소스명", "등록명",
    "폴스타등록명", "장비식별명",
})


def _norm_name_term(text: str) -> str:
    """폼 필드명을 공백 제거+소문자로 정규화한다('서버 이름'→'서버이름')."""
    return "".join((text or "").split()).lower()


def is_servername_field(field: str) -> bool:
    """필드명이 서버 등록명(cmm_resource.name) 의미의 표면어인지 판정한다."""
    return _norm_name_term(field) in _SERVER_NAME_TERMS


def is_hostname_target(column: str) -> bool:
    """매핑 대상 컬럼이 hostname(직접 컬럼 `*.hostname` 또는 EAV Hostname 속성)인지 판정한다.

    - EAV 매핑: `EAV:Hostname`
    - 직접 컬럼: `hostname` / `cmm_resource.hostname` / `polestar.cmm_resource.hostname` /
      `db_id:table.hostname` 등 — 표기·스키마 접두사·db_id 접두사 무관하게 bare 컬럼명으로 판정.
    """
    if not isinstance(column, str) or not column:
        return False
    if column.startswith("EAV:"):
        return column[4:].strip().lower() == "hostname"
    return column.rsplit(".", 1)[-1].strip().lower() == "hostname"


def is_servername_to_hostname(field: str, column: str) -> bool:
    """서버명/서버이름류 필드가 hostname(컬럼 or EAV)으로 (오)매핑되는지 판정한다.

    자동 유사어 등록 차단(재오염 방지)과 폼필 매핑 교정에서 공용으로 쓰는 결정적 판정.
    '호스트네임' 등 호스트명 표면어는 name-term이 아니므로 False.
    """
    return is_servername_field(field) and is_hostname_target(column)


def correct_servername_hostname_mapping(
    column_mapping: dict, entity_table: str
) -> None:
    """서버명/서버이름류 폼 필드가 hostname으로 오매핑되면 등록명 컬럼으로 교정한다(in-place).

    프로필(gp/yd/b0)이 명시적으로 규정하듯 '서버명/서버 이름'은 hostname(호스트명 값)이
    아니라 등록명 컬럼(`<entity>.name`, 예: cmm_resource.name)이다. 그러나 전역/EAV 유사어의
    `Hostname: [..., 서버명, ...]` 미끼 + 유사어/LLM 매핑의 비결정성으로 "서버 이름"이
    **EAV Hostname 또는 직접 `*.hostname` 컬럼**에 붙어 두 칼럼(서버 이름·호스트네임)이 모두
    hostname으로 채워지는 문제가 반복됐다. 프로필의 확정 규칙을 결정적 가드로 못박아,
    유사어/Redis 상태·LLM 변동과 무관하게 교정한다. EAV·직접 컬럼 **두 경로 모두** 처리한다.
    ('호스트네임' 등 호스트명 표면어는 name-term이 아니므로 건드리지 않는다.)
    """
    if not entity_table or not column_mapping:
        return
    for field, col in list(column_mapping.items()):
        if is_servername_to_hostname(field, col):
            column_mapping[field] = f"{entity_table}.name"




def build_query_examples_block(structure_meta: dict | None) -> str:
    """structure_meta.query_examples를 few-shot 블록 텍스트로 만든다(없으면 빈 문자열).

    프로필의 검증된 질문→SQL 패턴을 LLM에 그대로 제시하여 조인 환각을 줄인다.
    단일/멀티 DB 경로가 동일한 예시 블록을 사용하도록 단일 출처화한다.

    Args:
        structure_meta: 구조 분석 메타(수동 프로필 로드 결과, query_examples 포함 가능)

    Returns:
        시스템 프롬프트에 덧붙일 예시 블록 텍스트(없으면 "")
    """
    if not structure_meta:
        return ""
    query_examples = structure_meta.get("query_examples", [])
    if not query_examples:
        return ""

    block = "\n\n## 쿼리 예시 (반드시 이 패턴을 따르세요)"
    block += "\n아래 예시의 JOIN 패턴을 그대로 따라하세요. 임의로 다른 조인 조건을 만들지 마세요.\n"
    for i, ex in enumerate(query_examples, 1):
        question = ex.get("question", "")
        sql_example = ex.get("sql", "").rstrip()
        explanation = ex.get("explanation", "")
        block += f'\n### 예시 {i}: "{question}"'
        block += f"\n```sql\n{sql_example}\n```"
        if explanation:
            block += f"\n설명: {explanation}"
        block += "\n"
    return block


def build_value_index_block(matched: dict[str, list[str]] | None) -> str:
    """E5-2 값 검색 매칭 리터럴을 프롬프트 주입 블록으로 만든다(없으면 빈 문자열).

    질의 키워드로 검증된 **실측 리터럴**(예: `resource_type='server.Server'`,
    EAV `NAME='Hostname'`)만 제시하여 WHERE 리터럴 환각(Plan 25 유형)을 차단한다.
    단일/멀티 DB 경로가 동일 블록을 사용하도록 단일 출처화한다.

    Args:
        matched: {인덱스 키: [매칭 리터럴, ...]} (search_value_index 결과)

    Returns:
        프롬프트에 덧붙일 검증 리터럴 블록(없으면 "")
    """
    if not matched:
        return ""
    lines = [
        "\n\n## 검증된 값 리터럴 (WHERE 절에 이 실측 값만 사용)",
        "아래는 질의와 관련해 DB에서 실측 확인된 값입니다. WHERE 절 리터럴은 반드시 "
        "이 목록의 값만 사용하고, 목록에 없는 값을 임의로 지어내지 마세요.",
    ]
    for key, values in matched.items():
        if not values:
            continue
        vals = ", ".join(f"'{v}'" for v in values)
        lines.append(f"- {key}: {vals}")
    return "\n".join(lines) if len(lines) > 2 else ""


# 선행 task 결과(prior_rows) 서버 스코프 강제 (orchestration 데이터 의존 패턴 ②, D-086)
# hostname 힌트를 name 힌트보다 먼저 검사한다 — "hostname"은 "name"을 부분 문자열로 포함.
_PRIOR_HOSTNAME_HINTS: tuple[str, ...] = ("hostname", "host_name")
_PRIOR_NAME_HINTS: tuple[str, ...] = ("server_name", "name")
_MAX_PRIOR_SCOPE_VALUES: int = 100

# 서버 식별 컬럼 판정(D-100): 정확 매칭 집합 + server/host/os 계열 *_name/_id만 인정한다.
# 선행 조회가 서버명 외 컬럼(alarm_name/definition_name/severity 등)도 반환하게 되면서,
# "name" 부분매칭이 alarm_name을 서버 식별값으로 오수집해 스코프 HAVING이 오염됐다(실측).
_SERVER_ID_EXACT: frozenset[str] = frozenset(
    {"server_name", "hostname", "host_name", "os_hostname", "name", "id", "server_id"}
)
_SERVER_ID_PREFIXES: tuple[str, ...] = ("server", "host", "os")


def is_server_identity_col(col: str) -> bool:
    """컬럼명이 서버 식별 컬럼(병합 키·선행 스코프 키 후보)인지 엄격 판정한다 (D-100).

    정확 매칭(server_name/hostname/name/id 등)이거나 server/host/os로 시작하는 *_name/_id만
    서버 식별로 인정한다. alarm_name·definition_name·severity 등 비서버 컬럼은 제외한다.

    Args:
        col: 컬럼명

    Returns:
        서버 식별 컬럼이면 True
    """
    cl = str(col).strip().lower()
    if cl in _SERVER_ID_EXACT:
        return True
    if (cl.endswith("_name") or cl.endswith("_id")) and any(
        cl.startswith(p) for p in _SERVER_ID_PREFIXES
    ):
        return True
    return False


def collect_prior_identity_values(prior_rows: dict) -> tuple[str, list[str]]:
    """prior_rows에서 서버 식별 컬럼 종류와 값 목록을 추출한다.

    hostname류 컬럼이 하나라도 있으면 hostname을 우선하고, 없으면 name류를 쓴다
    (폴스타는 name≠hostname — D-061 계열, 컬럼 종류를 섞으면 0건 위험).

    Args:
        prior_rows: {task_id: [식별 키 행, ...]}

    Returns:
        ("hostname" | "name" | "", 중복 제거된 값 목록[상한 적용])
    """
    hostnames: list[str] = []
    names: list[str] = []
    seen_h: set[str] = set()
    seen_n: set[str] = set()
    for rows in (prior_rows or {}).values():
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            for col, val in row.items():
                if val is None or str(val).strip() == "":
                    continue
                if not is_server_identity_col(col):
                    continue  # 서버 식별 컬럼만 스코프 키로 인정(alarm_name 등 오염 차단 — D-100)
                col_l = str(col).lower()
                text = str(val).strip()
                if any(h in col_l for h in _PRIOR_HOSTNAME_HINTS):
                    if text not in seen_h:
                        seen_h.add(text)
                        hostnames.append(text)
                else:
                    if text not in seen_n:
                        seen_n.add(text)
                        names.append(text)
    if hostnames:
        return "hostname", hostnames[:_MAX_PRIOR_SCOPE_VALUES]
    if names:
        return "name", names[:_MAX_PRIOR_SCOPE_VALUES]
    return "", []


def build_prior_rows_block(prior_rows: dict | None) -> str:
    """선행 task 결과 서버 목록을 SQL 스코프 강제 블록으로 렌더링한다(없으면 빈 문자열).

    orchestration 데이터 의존(input_from) 경로에서 선행 task가 선별한 서버들로
    이번 SQL의 대상을 결정적으로 한정한다. 선별 조건(알람 상태·심각도 등)을 LLM이
    재표현하다 환각(존재하지 않는 resource_type='alarm.Alarm' 등)으로 0건이 되는
    것을 차단한다(D-086). 단일/멀티 DB 경로가 동일 블록을 사용한다(D-066).

    Args:
        prior_rows: {task_id: [식별 키 행, ...]} (subagents._make_isolated_input 산출)

    Returns:
        프롬프트에 덧붙일 스코프 강제 블록(유효한 식별 값이 없으면 "")
    """
    if not prior_rows:
        return ""
    col, values = _collect_prior_identity_values(prior_rows)
    if not values:
        return ""
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return (
        "## 선행 작업 결과 서버 스코프 (필수 준수)\n"
        "이번 조회 대상은 선행 작업에서 이미 선별된 아래 서버들로 **한정**합니다.\n"
        f"- 대상 서버 (선행 결과 {col} 컬럼 기준): {quoted}\n"
        "규칙:\n"
        f"1. SQL에 서버 한정 조건 `{col} IN ({quoted})` 을 반드시 포함하세요 "
        f"(위 값은 선행 결과의 식별 컬럼 값 목록입니다 — 대상 DB에서 서버를 식별하는 컬럼(`{col}` "
        "또는 동등한 식별 컬럼)에 적용하세요. 서버 엔터티 행 기준, GROUP BY 피벗 쿼리면 기존 규칙대로 "
        "HAVING의 집계 CASE WHEN으로 적용).\n"
        "2. 서버를 선별했던 조건(알람 발생·심각도·활성 상태 등)은 선행 작업에서 이미 처리 완료되었습니다 — "
        "선별에 사용한 테이블·컬럼·조건(알람/이벤트 등)을 이 SQL에서 다시 표현하지 마세요. "
        "대상 DB에 존재하지 않는 테이블/컬럼/값으로 선별 조건을 지어내지 마세요(환각 금지).\n"
        "3. 위 목록 외의 서버가 결과에 포함되어서는 안 됩니다.\n"
        "4. 결과의 각 행이 어느 서버의 값인지 알 수 있도록 서버 식별 컬럼(예: server_name)을 "
        "SELECT에 반드시 포함하세요 (GROUP BY 피벗 쿼리면 집계 CASE WHEN으로 포함)."
    )


def extract_sql_from_response(content: str | list) -> str:
    """LLM 응답에서 SQL 쿼리를 추출한다.

    단일 DB 경로(query_generator)와 멀티 DB 경로(multi_db_executor)가 동일 추출
    규칙을 쓰도록 단일 출처로 공유한다(D-066). 추출 엔진은 강화판
    `extract_sql_from_llm_response`(펜스 태그 변형·세미콜론 생략·WITH 지원, D-153)에
    위임하고, 여기서는 실 모델의 콘텐츠 블록 리스트 정규화만 얹는다(json_extract와 대칭).

    Args:
        content: LLM 응답 텍스트(콘텐츠 블록 리스트 허용)

    Returns:
        추출된 SQL 문자열
    """
    return extract_sql_from_llm_response(coerce_content_text(content))


# 하위호환 별칭 — 교차 임포트 공개화(Plan 69 P2). 신규 코드는 공개명을 쓴다.
_normalize_stat_month = normalize_stat_month
_utilization_guard = utilization_guard
_collect_prior_identity_values = collect_prior_identity_values

# ── 폼필 확인 이력 명령 판정 (Plan 73 Phase 3, D-151 — 단일 출처) ────────────────
# intent_planner(②.7 단락)·query.py(존 역질문 스킵, FIX-20)·field_mapper(매핑 스킵,
# FIX-24)가 공유한다. "기억" 계열 명사 필수 — 일반 조회와 충돌 차단.
FORM_MEMORY_NOUN_KEYWORDS = (
    "기억", "저장된 답", "저장된 값", "확인 이력",
    # D-177: "이 양식에 저장된 내용은?" — '저장된 답/값'만 있어 미탐(라이브 실측 2026-08-25)
    "저장된 내용", "저장한 내용",
)
FORM_MEMORY_VIEW_KEYWORDS = (
    "보여", "조회", "알려",
    # D-177: 의문형("기억하는 내용은 뭐지?") — 명령형만 잡혀 존 역질문으로 흘렀다.
    # '?'는 명사 동반이 전제(아래 AND 규칙)라 "CPU 사용률은?"류 일반 질의에는 걸리지 않는다.
    "뭐", "무엇", "뭔지", "어떤", "?", "？",
)
FORM_MEMORY_DELETE_KEYWORDS = ("삭제", "지워", "잊어", "다시 물어")
FORM_MEMORY_ALL_KEYWORDS = ("전부", "전체", "모두", "모든")
# D-177: 채움 동사가 동반되면 이력 조회가 아니라 채움 요청("기억한 값으로 채워줘") — 조회로
# 단락되면 DB 조회가 통째로 이력 응답으로 대체되므로(3중 게이트) 미탐 쪽으로 보수적 판정.
_FORM_MEMORY_FILL_VERBS = ("채워", "채우", "작성", "기입", "반영")
# D-177: 양식 업로드 후 '?'만 입력 — 저장 값 조회 단축키(반각·전각·반복 허용). 정확 일치라 오탐 0.
_FORM_MEMORY_SHORTCUT_CHARS = frozenset({"?", "？"})
# 단축키 발견성 안내(결정적 문구) — 이력 조회 응답·HITL 패널 응답 말미에 붙는다(utils 계층에 두어
# nodes.output_generator·orchestration.intent_planner가 역방향 import 없이 공유).
FORM_MEMORY_SHORTCUT_HINT = "'?'만 입력하면 이 양식에 저장된 값을 조회합니다."


def memory_query_normalized(text: str) -> str:
    """'기억' 키워드 매칭 전 정규화 — 하드웨어 명사 '(주)기억장치'를 제거한다.

    "주기억장치 사용현황 보여줘"(메모리 양식의 관용 표현)가 '기억'+'보여'로 이력
    명령에 오매칭되면 정상 폼필이 이력 조회로 오탈취된다(FIX-20 사이드이펙트 교정).
    """
    return (text or "").replace("기억장치", "")


def is_form_memory_command(text: str) -> bool:
    """폼필 확인 이력 조회·삭제 명령 여부.

    이 명령은 DB 조회·매핑이 전부 불필요하다 — 존 역질문 스킵(FIX-20)·field_mapper
    매핑 스킵(FIX-24)·intent_planner 결정적 단락(②.7)의 공통 게이트.
    """
    stripped = (text or "").strip()
    if stripped and all(ch in _FORM_MEMORY_SHORTCUT_CHARS for ch in stripped):
        return True  # '?' 단축키(D-177)
    q = memory_query_normalized(text)
    if not any(k in q for k in FORM_MEMORY_NOUN_KEYWORDS):
        return False
    if any(k in q for k in FORM_MEMORY_DELETE_KEYWORDS):
        return True
    if any(v in q for v in _FORM_MEMORY_FILL_VERBS):
        return False  # 채움 요청은 이력 조회가 아니다(D-177 보수적 가드)
    return any(k in q for k in FORM_MEMORY_VIEW_KEYWORDS)


def is_form_memory_shortcut(text: str) -> bool:
    """'?'만 입력한 저장 값 조회 단축키인지(D-177) — 안내 문구 분기용."""
    stripped = (text or "").strip()
    return bool(stripped) and all(ch in _FORM_MEMORY_SHORTCUT_CHARS for ch in stripped)


# ── LLM 응답 SQL 추출 (D-153 — 단일/멀티 경로 2벌 중복 해소, D-067 취지) ─────────
# 폐쇄망 실측(2026-08-04): 추출 실패 시 응답 전문(산문)이 SQL로 흘러 간이 검증
# "SELECT 문이 아닙니다"로 떨어졌고, 멀티 경로에서는 동일 스키마 SQL 캐시(D-066 후속6)
# 구조상 첫 번째 DB(공동존 gp)만 간헐 누락됐다. LLM 출력 형식(펜스 태그 대소문자·
# 언어 태그 변형·세미콜론 생략)의 비결정성은 프롬프트 강제가 아니라 결정적 후처리로
# 흡수한다(Known Mistakes 원칙).
_SQL_FENCE_RE = re.compile(
    r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n?(.*?)```", re.DOTALL
)
# 세미콜론 종결 SQL(서두 산문 허용). WITH는 영어 산문("with the ...")과의 오탐을 막기 위해
# CTE 형태(WITH <이름> AS ()만 인정한다.
_SQL_SEMI_RE = re.compile(
    r'((?:SELECT\s+|WITH\s+[\w"]+\s+AS\s*\().*?;)', re.IGNORECASE | re.DOTALL
)
# 세미콜론 생략 SQL — SELECT/WITH부터 말미까지(닫는 펜스 잔재는 호출부에서 제거).
_SQL_TAIL_RE = re.compile(
    r'((?:SELECT\s+|WITH\s+[\w"]+\s+AS\s*\().*)', re.IGNORECASE | re.DOTALL
)
_SQL_COMMENT_RE = re.compile(r"(?:--[^\n]*\n?|/\*.*?\*/)", re.DOTALL)


def looks_like_readonly_sql(text: str) -> bool:
    """주석 제거 후 SELECT/WITH로 시작하는 읽기 전용 SQL 형태인지 판정한다."""
    cleaned = _SQL_COMMENT_RE.sub("", text or "").strip()
    return cleaned.upper().startswith(("SELECT", "WITH"))


def extract_sql_from_llm_response(content: str) -> str:
    """LLM 응답에서 SQL을 추출한다 (query_generator·multi_db_executor 공용 단일 출처).

    흡수하는 실패 형태(종전 구현은 전부 응답 전문 폴백 → 검증 실패):
    ① 펜스 언어 태그 변형: ```SQL / ```postgresql / 무태그 — 태그 불문 매칭 후
       블록 내용이 SQL 형태(SELECT/WITH 시작)인지로 판정.
    ② 세미콜론 생략: SELECT/WITH부터 말미까지 추출(닫는 펜스 잔재 제거).
    ③ 서두 산문 + 펜스 없는 SQL(세미콜론 유무 불문).

    말미 산문이 딸려 들어가는 과추출은 후단 검증(_find_bare_hangul_tokens·실행 에러
    재시도)이 잡는다 — 종전의 "확정 추출 실패"보다 항상 좁거나 같은 실패면이다.

    Args:
        content: LLM 응답 텍스트

    Returns:
        추출된 SQL 문자열 (추출 불가 시 전문 반환 — 기존 폴백 시맨틱 유지)
    """
    text = content or ""

    # 1) 코드 펜스(언어 태그·대소문자 불문) 중 SQL 형태인 첫 블록
    for m in _SQL_FENCE_RE.finditer(text):
        block = m.group(1).strip()
        if block and looks_like_readonly_sql(block):
            return block

    # 2) 세미콜론 종결 SQL (서두 산문 허용)
    m = _SQL_SEMI_RE.search(text)
    if m:
        return m.group(1).strip()

    # 3) 세미콜론 생략 SQL — SELECT/WITH부터 말미까지, 닫는 펜스·백틱 잔재 제거
    m = _SQL_TAIL_RE.search(text)
    if m:
        tail = m.group(1).split("```", 1)[0].strip().strip("`").strip()
        if tail:
            return tail

    # 4) 최후 수단: 전문 반환 (후단 검증이 거른다 — 기존 동작 유지)
    return text.strip()


# ── 존 역질문 공용 판정·페이로드 (Plan 75 §4 / D-143 후속2) ──────────────────────
# 위치 표면어 단일 출처(D-053 사본 금지). 종전 canonical은 input_parser였으나,
# 레거시 경로(semantic_router, infrastructure 계층)가 후단 게이트에서 같은 목록을
# 써야 해 계층 규칙(infrastructure→application 금지)상 utils로 내렸다.
# input_parser가 re-export하므로 기존 임포트 지점은 그대로 동작한다.
LOCATION_HINT_TERMS: tuple[str, ...] = ("공동존", "김포", "여의도", "은행", "레거시", "은행존")

# 존 역질문 스킵 신호 — 위치어 + 제품/DB 표면어(사용자가 대상을 이미 지목한 형태).
ZONE_SKIP_SIGNAL_TERMS: tuple[str, ...] = (*LOCATION_HINT_TERMS, "폴스타", "polestar")

# 서버 식별자로 인정할 filter_conditions field (종전 canonical: process_query._HOST_FIELDS —
# 존 게이트가 routing 계층에서도 필요해 utils로 내림, process_query가 re-export).
HOST_IDENTIFIER_FIELDS: tuple[str, ...] = (
    "hostname", "host_name", "server_name", "name", "host", "device_name",
    "서버명", "서버이름", "장비명", "호스트명", "서버", "장비",
)
# 지시어(demonstrative) 접두/명사 — 실제 서버명이 아니라 직전 대상을 가리키는 표현.
DEMONSTRATIVE_PREFIXES: tuple[str, ...] = ("해당", "그", "이", "저", "위", "방금", "앞서", "직전", "이전")
DEMONSTRATIVE_NOUNS: tuple[str, ...] = ("서버", "장비", "호스트명", "호스트", "인스턴스", "노드", "머신", "시스템")


def is_demonstrative_identifier(value: object) -> bool:
    """서버 식별자 값이 실제 이름이 아니라 지시어/플레이스홀더인지 판정한다.

    (process_query._is_demonstrative_value의 단일 출처 이동분 — 동작 동일.)
    """
    if value is None:
        return True
    v = str(value).strip()
    if not v:
        return True
    compact = v.lower().replace(" ", "").replace("_", "").replace("-", "")
    if ("previous" in compact or compact.startswith("prev")) and (
        "server" in compact or "host" in compact
    ):
        return True
    body = v
    for noun in DEMONSTRATIVE_NOUNS:
        if body.endswith(noun):
            body = body[: -len(noun)].strip()
            break
    return body in DEMONSTRATIVE_PREFIXES


def looks_like_process_rows(rows: list[dict] | None) -> bool:
    """결과 행이 프로세스 조회 결과인지 판별한다 (엔티티·조사 대상 오수집 방지용).

    프로세스 조회 결과 행은 `pid`를 갖는다(process_query._process_to_dict:
    {name, pid, user, cpu_pct, mem_pct, rss, args}). `name`은 프로세스명, `pid`는 서버가
    아니므로 이런 행에서 서버 식별 엔티티를 수집하면 안 된다. 서버 조회 결과 행에는 `pid`가 없다.

    (context_resolver._looks_like_process_rows의 단일 출처 이동분 — 동작 동일.
    utils로 내린 이유: 조사 대상 해소(prior_targets)가 application 계층을 import할 수 없다.)

    Args:
        rows: 결과 행 목록

    Returns:
        첫 행이 `pid` 키를 가지면 True(프로세스 행으로 간주)
    """
    if not rows:
        return False
    first = rows[0]
    if not isinstance(first, dict):
        return False
    keys = {str(k).lower() for k in first.keys()}
    return "pid" in keys


def refers_to_demonstrative_server(text: str) -> bool:
    """질의가 지시어("해당/그/위 … 서버")로 특정 서버를 가리키는지 판정한다.

    "전체/모든/모두" 전역 조회 신호가 있으면 False(서버 스코프 강제 금지).
    지시어 접두 + (선택 공백) + 서버 명사 인접 구문만 인정 — 단순 부분매칭은 단일 문자
    접두("이"/"그"/"위")가 "이상"/"그래서" 등에 오탐하므로 금지.
    (subagents._refers_to_specific_server의 단일 출처 이동분 — 동작 동일.)
    """
    if not text:
        return False
    if any(k in text for k in ("전체", "모든", "모두")):
        return False
    for p in DEMONSTRATIVE_PREFIXES:
        for n in DEMONSTRATIVE_NOUNS:
            if f"{p}{n}" in text or f"{p} {n}" in text:
                return True
    return False


def has_host_identifier_filter(parsed_requirements: dict | None) -> bool:
    """이번 턴 filter_conditions에 실제 서버 식별자(지시어 제외)가 있는지 판정한다.

    존 역질문 비발동 조건 ⓐ(D-143 §4.2 — 서버명 지목 질의는 존이 결과에 영향 없음)의
    결정적 판정. 라우트 레벨 표면어 게이트는 이 정보가 없어 판정할 수 없었다(후속2 동기).
    """
    parsed = parsed_requirements or {}
    for cond in parsed.get("filter_conditions") or []:
        if not isinstance(cond, dict):
            continue
        if str(cond.get("field", "")).lower() in HOST_IDENTIFIER_FIELDS:
            value = cond.get("value")
            if value and not is_demonstrative_identifier(value):
                return True
    return False


# 존 선택지 — DB 라우팅 입도와 일치(D-143 §4.4). group은 존 그룹 상호배타(D-143 후속3):
# 은행존(bank)과 공동존(common)은 담당 조직이 달라 동시 조회 실수요가 없고(사용자 확정
# 2026-08-05), b0+gp 조합에서 FabriX PII 필터가 gp 생성 요청을 차단하는 미종결 이슈의
# 회피를 겸한다. 공동존 내 김포/여의도는 다중 선택 유지.
# 종전 canonical은 api/routes/query.py._ZONE_OPTIONS — 후단 게이트(orchestration·
# routing 계층)와 공유하기 위해 utils로 내림(query.py가 alias 유지).
ZONE_CLARIFY_OPTIONS: tuple[dict, ...] = (
    {"db_id": "polestar_b0", "label": "은행존", "group": "bank"},
    {"db_id": "polestar_cm_gp", "label": "공동존 김포", "group": "common"},
    {"db_id": "polestar_cm_yd", "label": "공동존 여의도", "group": "common"},
)

_ZONE_GROUP_BY_DB: dict[str, str] = {
    o["db_id"]: o["group"] for o in ZONE_CLARIFY_OPTIONS
}

# 존 그룹 표면어 — 텍스트 질의에서 은행존·공동존 동시 지정을 결정적으로 감지(D-143 후속3).
# LOCATION_HINT_TERMS의 그룹 분할(단일 출처 파생 — 사본 아님, 항목 추가 시 여기도 갱신).
_ZONE_GROUP_TERMS: dict[str, tuple[str, ...]] = {
    "bank": ("은행존", "은행", "레거시"),
    "common": ("공동존", "김포", "여의도"),
}

ZONE_CLARIFY_QUESTION = (
    "조회할 존이 지정되지 않았습니다. 아래에서 대상 존을 선택해 주세요. "
    "(복수 선택 가능 — 전체 조회는 모두 선택)"
)

# 존 그룹 상호배타(D-143 후속3) 활성 시의 기본 안내 — "전체는 모두 선택" 문구 제거
ZONE_CLARIFY_QUESTION_EXCLUSIVE = (
    "조회할 존이 지정되지 않았습니다. 아래에서 대상 존을 선택해 주세요. "
    "(은행존과 공동존은 동시 선택 불가 — 공동존은 김포/여의도 복수 선택 가능)"
)

ZONE_GROUP_EXCLUSIVE_QUESTION = (
    "은행존과 공동존은 동시에 조회할 수 없습니다(담당 영역 분리). "
    "아래에서 조회할 존을 선택해 주세요. (공동존은 김포/여의도 복수 선택 가능)"
)


def mixed_zone_groups(db_ids: list[str] | None) -> bool:
    """선택 DB 목록이 은행존(bank)과 공동존(common)을 동시에 포함하는지 판정한다."""
    groups = {
        _ZONE_GROUP_BY_DB[d] for d in (db_ids or []) if d in _ZONE_GROUP_BY_DB
    }
    return len(groups) > 1


def has_mixed_zone_group_terms(text: str) -> bool:
    """질의 텍스트가 은행존·공동존 표면어를 동시에 포함하는지 판정한다(D-143 후속3).

    두 그룹 표면어가 모두 있을 때만 True — 단일 그룹 지정·존 무관 질의는 영향 없다.
    오탐의 대가는 존 선택창 재표시(막다른 에러 아님)라 낮다.
    """
    t = text or ""
    return all(
        any(term in t for term in terms) for terms in _ZONE_GROUP_TERMS.values()
    )


def build_zone_clarification(
    active_db_ids: list[str] | None,
    original_query: str,
    *,
    question: str | None = None,
    has_file: bool = False,
    group_exclusive: bool = False,
) -> dict | None:
    """존 선택 역질문 페이로드를 만든다 (라우트 pre-gate와 동일 shape — 프론트 재사용).

    Args:
        active_db_ids: 활성 DB 목록(비활성 존은 선택지에서 제외)
        original_query: 원문 질의(프론트가 selected_db_ids와 함께 재전송)
        question: 질문 문구(기본 ZONE_CLARIFY_QUESTION)
        has_file: 파일 경로 여부(프론트가 보관 파일 재전송)

    Returns:
        clarification 페이로드 dict. 폴스타 존이 전부 비활성이면 None(기존 폴백 유지).
    """
    active = set(active_db_ids or [])
    options = [o for o in ZONE_CLARIFY_OPTIONS if not active or o["db_id"] in active]
    if not options:
        return None
    if question is None:
        question = (
            ZONE_CLARIFY_QUESTION_EXCLUSIVE if group_exclusive else ZONE_CLARIFY_QUESTION
        )
    payload: dict = {
        "kind": "zone_select",
        "question": question,
        "options": options,
        "original_query": original_query or "",
        "multi": True,
    }
    if has_file:
        payload["has_file"] = True
    if group_exclusive:
        # 존 그룹 상호배타(D-143 후속3) — 프론트가 bank/common 그룹 간 라디오 동작 적용
        payload["group_exclusive"] = True
    return payload


# ── 존 선택 재개 턴 원문 재작성 (D-154 — D-143 후속3의 잔여 버그) ─────────────────
_ZONE_LABEL_BY_DB: dict[str, str] = {
    o["db_id"]: o["label"] for o in ZONE_CLARIFY_OPTIONS
}

# 존 열거 구간: 존 표면어로 시작해 접속어·존 표면어·"센터" 표기가 이어지는 최장 span.
# 예: "은행존 및 공동존 여의도 센터" / "공동존 김포와 여의도". 바깥 조사("…센터의")는
# span에 포함하지 않아 치환 후 자연스럽게 이어진다("은행존" + "의 모든 서버…").
_ZONE_ENUM_RE = re.compile(
    r"(?:은행존|공동존|김포|여의도|레거시)"
    r"(?:\s*(?:및|와|과|,|·|/|그리고)?\s*(?:은행존|공동존|은행|레거시|김포|여의도|센터|센타))*"
)


def rewrite_zone_mentions_for_selection(
    query: str, selected_db_ids: list[str] | None
) -> str:
    """존 재선택 재개 턴에서 원문의 존 열거를 선택 존 라벨로 결정적으로 재작성한다.

    (D-154) 상호배타 재선택("은행존 및 공동존 여의도…" → 은행존만 선택) 후에도 원문이
    그대로 파이프라인에 들어가면 ①처리 현황·응답 서술에 미선택 존이 남고 ②단일 경로는
    sub_query_context가 원문이라 미선택 존 위치어(여의도 등)가 SQL WHERE로 누출된다.
    라우팅은 selected_db_ids가 이미 고정이므로 텍스트만 교정한다 — 결정적 문자열 치환
    (ㅇㅇ존 플레이스홀더 치환과 동형, LLM 재해석 아님).

    발동 조건을 "혼합 존 그룹 표면어 + selected_db_ids"로 좁혀, 단일 그룹 지정·일반
    재개 턴(치환 불필요)은 원문을 그대로 반환한다(호스트명 등 오치환 면적 최소화).

    Args:
        query: 원문 질의
        selected_db_ids: 존 선택 UI에서 확정된 DB 목록

    Returns:
        존 열거가 선택 존 라벨로 치환된 질의 (비발동 시 원문 그대로)
    """
    q = query or ""
    if not selected_db_ids or not has_mixed_zone_group_terms(q):
        return q
    labels = [_ZONE_LABEL_BY_DB.get(d, d) for d in selected_db_ids]
    replacement = ", ".join(labels)
    selected_groups = {
        _ZONE_GROUP_BY_DB[d] for d in selected_db_ids if d in _ZONE_GROUP_BY_DB
    }
    non_selected_terms = tuple(
        term
        for grp, terms in _ZONE_GROUP_TERMS.items()
        if grp not in selected_groups
        for term in terms
    )

    def _sub(m: re.Match) -> str:
        span = m.group(0)
        # 미선택 그룹 표면어를 포함한 열거 구간만 치환 — 선택 존만 언급한 구간은 유지
        return replacement if any(t in span for t in non_selected_terms) else span

    rewritten = _ZONE_ENUM_RE.sub(_sub, q)
    return re.sub(r"[ \t]{2,}", " ", rewritten).strip()
