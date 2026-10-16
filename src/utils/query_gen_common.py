"""query_generator / multi_db_executor 공통 SQL 생성 헬퍼 (D-066).

단일 DB 경로(`query_generator` 노드)와 멀티 DB 경로(`multi_db_executor` 노드)가
갈라지면서 (1) few-shot 쿼리 예시 주입과 (2) "전체/모든" 조회 LIMIT 상향이 단일
경로에만 존재해, 멀티 DB 폼필(공동존=gp+yd 등)이 예시 없이·LIMIT 1000으로 열화됐다.
두 로직을 단일 출처로 공유하여 경로 간 SQL 품질 비대칭을 제거한다.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

# ──────────────────────────────────────────────
# 분할 모듈 재노출 (plans/166 N2 · D-337 ① — shim 1사이클)
# ──────────────────────────────────────────────
# 관심사별 모듈로 옮긴 이름을 `_` 이름까지 전부 다시 노출해 기존 import 경로를 유지한다.
# 이 모듈에 남긴 것(Facade): `date.today()`를 읽는 기간 진입 함수(테스트가 이 모듈의 `date`를
# 고정 — D-337 ②), 위치 표면어 값 사본과 그것을 읽는 용어 매칭·존 역질문·생략형 승계 진입
# 함수(위치 표면어 단일 정의 가드가 이 파일만 허용), 프롬프트 블록·폴스타 규약(계층 이동 보류).
# `X as X`는 mypy strict(no_implicit_reexport)용 명시 재노출이다 — isort는 `as` 임포트를
# 이름마다 한 문장으로 쪼개므로 이 블록만 정렬에서 뺀다.
# isort: off
from src.utils.limit_policy import (
    FALLBACK_ALL_SCOPE_REJECT as FALLBACK_ALL_SCOPE_REJECT,
    FALLBACK_LIMIT_LLM as FALLBACK_LIMIT_LLM,
    FALLBACK_TIME_RANGE_LLM as FALLBACK_TIME_RANGE_LLM,
    _ALL_QUERY_KEYWORDS as _ALL_QUERY_KEYWORDS,
    _ALL_QUERY_LIMIT as _ALL_QUERY_LIMIT,
    _ALL_SCOPE_DERIV_SUFFIXES as _ALL_SCOPE_DERIV_SUFFIXES,
    _EXPLICIT_COUNT_RE as _EXPLICIT_COUNT_RE,
    _FALLBACK_COUNTERS as _FALLBACK_COUNTERS,
    _FULL_SCOPE_REQUEST_KEYWORDS as _FULL_SCOPE_REQUEST_KEYWORDS,
    _GENERIC_EXAMPLE_CAP as _GENERIC_EXAMPLE_CAP,
    _LEGACY_DEFAULT_CAP as _LEGACY_DEFAULT_CAP,
    _TOP_N_RE as _TOP_N_RE,
    _TRAILING_LIMIT_RE as _TRAILING_LIMIT_RE,
    _has_scope_term as _has_scope_term,
    enforce_all_query_limit as enforce_all_query_limit,
    explicit_row_count as explicit_row_count,
    fallback_counters as fallback_counters,
    has_all_scope_keyword as has_all_scope_keyword,
    has_full_scope_request as has_full_scope_request,
    is_full_scan_query as is_full_scan_query,
    note_fallback as note_fallback,
    reset_fallback_counters as reset_fallback_counters,
    resolve_effective_limit as resolve_effective_limit,
    resolve_query_limit as resolve_query_limit,
)
from src.utils.llm_sql_extract import (
    _SQL_COMMENT_RE as _SQL_COMMENT_RE,
    _SQL_FENCE_RE as _SQL_FENCE_RE,
    _SQL_SEMI_RE as _SQL_SEMI_RE,
    _SQL_TAIL_RE as _SQL_TAIL_RE,
    extract_sql_from_llm_response as extract_sql_from_llm_response,
    extract_sql_from_response as extract_sql_from_response,
    looks_like_readonly_sql as looks_like_readonly_sql,
)
from src.utils.period_resolution import (
    _HALF_YEAR_RE as _HALF_YEAR_RE,
    _MONTH_RANGE_RE as _MONTH_RANGE_RE,
    BlockedComparison as BlockedComparison,
    ComparisonPeriods as ComparisonPeriods,
    StatMonth as StatMonth,
    _infer_year_not_future as _infer_year_not_future,
    _iso_to_month as _iso_to_month,
    _month_shift as _month_shift,
    _months_from_parsed_time_range as _months_from_parsed_time_range,
    _resolve_half_year_expr as _resolve_half_year_expr,
    _resolve_month_range_expr as _resolve_month_range_expr,
    normalize_stat_month as normalize_stat_month,
    previous_month as previous_month,
)
from src.utils.prior_turn_succession import (
    DEMONSTRATIVE_NOUNS as DEMONSTRATIVE_NOUNS,
    DEMONSTRATIVE_PREFIXES as DEMONSTRATIVE_PREFIXES,
    ELLIPTICAL_DISCOURSE_MARKERS as ELLIPTICAL_DISCOURSE_MARKERS,
    ELLIPTICAL_SUCCESSION_KEY as ELLIPTICAL_SUCCESSION_KEY,
    HOST_IDENTIFIER_FIELDS as HOST_IDENTIFIER_FIELDS,
    _BY_SUFFIX as _BY_SUFFIX,
    _DO_ENDING_NON_ADDITIVE as _DO_ENDING_NON_ADDITIVE,
    _DO_ENDING_NOUNS as _DO_ENDING_NOUNS,
    _HOSTNAME_KIND_FIELDS as _HOSTNAME_KIND_FIELDS,
    _IP_IDENTIFIER_FIELDS as _IP_IDENTIFIER_FIELDS,
    _NEW_IDENTIFIER_TOKEN as _NEW_IDENTIFIER_TOKEN,
    _PER_ENTITY_AGGREGATIONS as _PER_ENTITY_AGGREGATIONS,
    _SET_SELECTION_PREFIXES as _SET_SELECTION_PREFIXES,
    _SET_SELECTION_WORDS as _SET_SELECTION_WORDS,
    _TIME_BUCKET_STEMS as _TIME_BUCKET_STEMS,
    _WORD_TOKEN as _WORD_TOKEN,
    _has_additive_marker as _has_additive_marker,
    _has_new_identifier as _has_new_identifier,
    _selects_server_set as _selects_server_set,
    _single_previous_entity as _single_previous_entity,
    has_host_identifier_filter as has_host_identifier_filter,
    is_demonstrative_identifier as is_demonstrative_identifier,
    looks_like_process_rows as looks_like_process_rows,
    refers_to_demonstrative_server as refers_to_demonstrative_server,
    render_elliptical_succession_note as render_elliptical_succession_note,
)
from src.utils.sql_rewrite_eav import (
    _EAV_INT_CAST_TYPES as _EAV_INT_CAST_TYPES,
    _EAV_UNIT_CAST_TYPES as _EAV_UNIT_CAST_TYPES,
    _UNIT_GUARD_MARKER as _UNIT_GUARD_MARKER,
    _UNIT_PROBES as _UNIT_PROBES,
    _matching_paren as _matching_paren,
    _matching_paren_backward as _matching_paren_backward,
    _regex_strips_unit as _regex_strips_unit,
    _unit_normalized_expr as _unit_normalized_expr,
    _unwrap_llm_unit_replace as _unwrap_llm_unit_replace,
    eav_value_cast_columns as eav_value_cast_columns,
    normalize_eav_numeric_casts as normalize_eav_numeric_casts,
    normalize_eav_unit_casts as normalize_eav_unit_casts,
)
# isort: on


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
    # 절대 월 **범위**·반기 표현이 최우선(D-185) — "2026년 1월부터 6월까지"가 아래 단일 월
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


# ──────────────────────────────────────────────
# 기간 대비 비교(급증) — Plan 82 Wave 9 · D-176 후속2
# ──────────────────────────────────────────────

#: "1달 전 대비"류 월 단위 비교 표면어. 여기 있는 표현만 비교 모드로 들어간다.
_MONTH_COMPARISON_SIGNALS: tuple[str, ...] = (
    "1달 전", "한달 전", "한 달 전", "1개월 전", "전월 대비", "지난달 대비",
    "저번달 대비", "전달 대비", "month over month", "mom",
)
#: 주 단위 비교 표면어 — **열려 있지 않다**(일별 통계 보존기간 미확인 · §6.12 ③).
_WEEK_COMPARISON_SIGNALS: tuple[str, ...] = (
    "1주일 전", "일주일 전", "1주 전", "지난주 대비", "전주 대비", "주간 대비",
)


def resolve_comparison_periods(
    user_query: str | None, today: date | None = None
) -> ComparisonPeriods | BlockedComparison | None:
    """질의의 **기간 대비** 표현을 (기준 월, 비교 월) 쌍으로 해석한다(없으면 None).

    `resolve_stat_month_range`의 형제다 — 저쪽은 단일 기간 필터를, 이쪽은 두 기간의
    비교를 낸다. 기존 함수는 **건드리지 않는다**(단일 기간 경로 동작 불변).

    "1달 전 대비"·"전월 대비" → (직전월−1, 직전월). 진행 중인 달은 월 통계가
    미완결이라 비교 대상이 아니다(D-076 후속4와 같은 원칙).

    주 단위 요청은 `BlockedComparison`을 돌려준다 — `metric_tables`에 `week` grain이
    없고(hour/day/month만), 일별 통계의 **실제 보존일수가 저장소에 기록돼 있지 않다**.
    DBHub 가동 후 프로브 1회로 해소된다:
    `SELECT MIN(stat_date), MAX(stat_date), COUNT(DISTINCT stat_date) FROM <일별 통계>`

    Args:
        user_query: 사용자 원문 질의
        today: 상대 표현의 기준일(기본 오늘)

    Returns:
        비교 기간 쌍 / 차단 신호 / None(비교 표현 없음)
    """
    text = user_query or ""
    lowered = text.lower()
    ref = today or date.today()

    if any(sig in lowered for sig in _WEEK_COMPARISON_SIGNALS):
        return BlockedComparison(
            reason="주 단위 비교는 일별 통계 보존기간이 확인되지 않아 제공하지 않습니다.",
            suggestion="월 단위(전월 대비)로 조회하면 같은 판정을 지금 받을 수 있습니다.",
        )

    if any(sig in lowered for sig in _MONTH_COMPARISON_SIGNALS):
        return ComparisonPeriods(
            base_month=_month_shift(ref, -2), cur_month=_month_shift(ref, -1)
        )

    return None


def current_month_daily_range(
    stat_month: StatMonth, today: date | None = None
) -> tuple[str, str] | None:
    """기간이 정확히 진행 중인 달 하나면 일간 통계 범위 (당월 1일, 어제) YYYYMMDD를 돌려준다(D-201).

    월간 통계는 직전월까지만 집계돼 진행 중인 달을 월간 통계로 조회하면 전부 null이다. 검증기
    (`check_current_month_stat_table`)는 그 SQL을 반려하는데, 결정적 컴파일러와 폴백 기간 블록은
    월간 통계를 강제하고 있어 재시도가 같은 SQL을 반복하다 예산을 소진했다(2026-09-17 로컬 C-06 재현).
    세 곳이 같은 판정을 쓰도록 여기 둔다. 범위(시작≠끝)는 대상이 아니다(검증기와 동일).
    매월 1일에는 끝(어제)이 시작보다 앞서 0행이 되며, 그것이 D-201의 정답이다.
    """
    rng = _normalize_stat_month(stat_month)
    ref = today or date.today()
    cur = ref.strftime("%Y%m")
    if rng != (cur, cur):
        return None
    return (f"{cur}01", (ref - timedelta(days=1)).strftime("%Y%m%d"))


def build_stat_month_block(
    stat_month: StatMonth = None,
    metric_table: str = "cmm_metric_stat_m",
    today: date | None = None,
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
        today: 진행 중인 달 판정 기준일(기본 오늘 — 테스트 주입용)

    Returns:
        프롬프트에 덧붙일 섹션 텍스트(선행 개행 없음). stat_month가 없으면 "".
    """
    rng = _normalize_stat_month(stat_month)
    if not rng:
        return ""
    daily = current_month_daily_range(rng, today)
    if daily:
        # D-201: 진행 중인 달은 월간 통계를 강제하면 검증기 반려와 정면으로 부딪친다.
        return (
            "## 기간 조건 (시스템이 결정적으로 해석 — 최우선 준수)\n"
            f"질의의 기간은 진행 중인 달({rng[0]})입니다. 월간 통계({metric_table})는 직전월까지만 "
            "집계되어 이번 달 값이 없으므로 사용하지 마세요.\n"
            "- 스키마 규칙의 '이번 달' 분기대로 **일간 통계**를 "
            f"`s.stat_date BETWEEN '{daily[0]}' AND '{daily[1]}'`"
            "(당월 1일~어제, YYYYMMDD)로 조회하세요.\n"
            "- 서버별 GROUP BY로 AVG(s.avg_val)=월중 평균, MAX(s.max_val)=월중 최대를 집계하세요.\n"
            "- 이 값은 시스템이 계산해 주입한 것으로 하드코딩이 아닙니다(위 값 그대로 사용)."
        )
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


def surface_query_for_judgment(state: Mapping[str, Any], current: str | None = None) -> str:
    """표면어 결정적 판정(순위·최상급·급증·비교기간·파일시스템·절대임계)에 쓸 질의 문자열.

    오케스트레이션(1·2단)은 단일 DB 파이프라인에 들어가며 ``user_query``를 재작성문
    (``sub_query_context``·``sub_query``)으로 바꾼다. 재작성이 "상위"·"급증"·"80% 이상" 같은
    표면어를 탈락·추가하면 결정적 판정이 원문과 달라진다(plans/107 W0.5 — ``resolved_limit``
    (D-066)·``realtime_usage_intent``(D-066 후속7)와 같은 결함 부류). 상태에 새 필드를 두지
    않고 **소비 시점에 원문을 다시 읽는다**(CU-8 선례).

    - 원문은 ``_make_isolated_input``이 교체 전 값으로 싣는 ``original_user_query``다.
      3단 그래프 경로에는 그 키가 없고 ``user_query``가 곧 원문이라 결과가 종전과 같다.
    - **복합 계획(``is_composite``)이면 원문을 쓰지 않는다** — 원문에 다른 task의 조건이 함께
      있어 "80% 이상"·"상위" 같은 표면어가 엉뚱한 task로 번진다. 이때는 task 스코프
      재작성문(현행)을 그대로 쓴다. 1단은 계획 크기를 미리 알 수 없어 ``is_composite``가
      항상 False다 — 선례(``resolved_limit``)와 같은 한계이며 1단 전용 판정은 두지 않는다
      (D-225 ⑦).

    Args:
        state: 에이전트 상태(또는 오케스트레이션 격리 입력)
        current: 호출부가 이미 쥔 현재 질의(``query_generator``의 ``ctx.user_query``). 없으면
            ``state["user_query"]``. 원문을 쓰지 않는 경우 이 값을 그대로 돌려준다.

    Returns:
        판정 입력 문자열(없으면 빈 문자열)
    """
    original = state.get("original_user_query")
    if original and not state.get("is_composite"):
        return str(original)
    if current is not None:
        return current
    return str(state.get("user_query") or "")


# ── '가동률' 미지원 지표 pre-gate (D-200, 2026-09-07) ────────────────────────
# '가동률'은 실무 통용대로 가동 시간 비율(uptime/availability) 계열로 확정(사용자 결정).
# 폴스타는 해당 지표를 집계하지 않는다(3존 stat_m definition_name 전수 실측 —
# scripts/diag_metric_definitions.sql). CPU 사용률로의 임의 해석(오답)과 B0 재계획
# 폭주(C-08 실측)를 함께 차단하기 위해 LLM 이전에 결정적으로 단락한다.
# 어간이 달라 퍼지 매칭 원리상 도달 불가한 어휘라(수동 결정 필수) 표면어 포함 판정로 충분하다.
UPTIME_RATE_TERM = "가동률"

#: 사용자에게 그대로 노출되는 고정 안내문 — LLM을 통과시키지 않는다(D-150 선례).
UPTIME_RATE_GUIDANCE = (
    "'가동률'은 서버 가동 시간 비율(uptime/availability)을 뜻하는 용어로 해석합니다.\n"
    "폴스타는 가동 시간 비율을 통계 지표로 집계하지 않아 해당 조회를 제공할 수 없습니다"
    "(은행존·공동존 전체 실측 확인).\n\n"
    "다음 중 원하시는 조회로 다시 질의해 주세요:\n"
    "- 현재 가동(가용성) 상태 기준: \"가용 상태가 비정상인 서버 목록 보여줘\"\n"
    "- CPU 사용률 기준: \"지난달 CPU 사용률이 낮은 서버 순으로 보여줘\""
)


def is_uptime_rate_query(user_query: str | None) -> bool:
    """질의가 '가동률'(미지원 지표)을 요구하는지 결정적으로 판정한다(D-200)."""
    return UPTIME_RATE_TERM in (user_query or "")


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
# 멀티 DB 병합이 행마다 붙이는 출처 태그(`multi_db_executor._merge_results`). `prior_targets.SOURCE_DB_KEY`와
# 같은 리터럴이며, 그 모듈이 이 모듈을 import하므로 여기서 따로 둔다(순환 방지).
PRIOR_SOURCE_DB_KEY: str = "_source_db"

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


def collect_prior_identity_values(
    prior_rows: dict, *, limit: int | None = _MAX_PRIOR_SCOPE_VALUES,
) -> tuple[str, list[str]]:
    """prior_rows에서 서버 식별 컬럼 종류와 값 목록을 추출한다.

    hostname류 컬럼이 하나라도 있으면 hostname을 우선하고, 없으면 name류를 쓴다
    (폴스타는 name≠hostname — D-061 계열, 컬럼 종류를 섞으면 0건 위험).

    Args:
        prior_rows: {task_id: [식별 키 행, ...]}
        limit: 값 상한(기본 100). None이면 절단하지 않는다 — 절단 건수를 보고해야 하는
            게이트(D-203 `prior_dependency`)가 전체 개수를 셀 때 쓴다.

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
        return "hostname", (hostnames if limit is None else hostnames[:limit])
    if names:
        return "name", (names if limit is None else names[:limit])
    return "", []


def filter_prior_rows_for_db(prior_rows: dict | None, db_id: str) -> dict:
    """prior_rows에서 `db_id` 소속 행(+출처 태그 없는 행)만 남긴다 (D-203 · plans/88 §4.9).

    태그가 있는 행이 하나도 없으면 입력을 그대로 돌려준다(단일 DB 선행 — 현행 동작).
    """
    if not prior_rows:
        return {}
    tagged = any(
        isinstance(r, dict) and r.get(PRIOR_SOURCE_DB_KEY)
        for rows in prior_rows.values() for r in (rows or [])
    )
    if not tagged:
        return prior_rows
    out: dict = {}
    for tid, rows in prior_rows.items():
        kept = [
            r for r in (rows or [])
            if isinstance(r, dict) and (not r.get(PRIOR_SOURCE_DB_KEY) or str(r.get(PRIOR_SOURCE_DB_KEY)) == db_id)
        ]
        if kept:
            out[tid] = kept
    return out


def collect_prior_identity_values_by_db(prior_rows: dict | None) -> dict[str, tuple[str, list[str]]]:
    """prior_rows를 행의 `_source_db`로 나눠 DB별 (식별컬럼, 값목록)을 돌려준다 (D-203 · plans/88 §4.9).

    태그 없는 행은 "" 버킷 — 호출부가 전 DB에 적용한다(현행 동작). 컬럼 종류 우선 규칙은
    `collect_prior_identity_values`와 동일(hostname류 우선 · D-061 혼합 금지)하며 버킷별로 적용한다.
    `_source_db` 자체는 식별 컬럼이 아니라 값 목록에 새지 않는다.
    """
    buckets: dict[str, list[dict]] = {}
    for rows in (prior_rows or {}).values():
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get(PRIOR_SOURCE_DB_KEY) or "")
            buckets.setdefault(key, []).append(row)
    out: dict[str, tuple[str, list[str]]] = {}
    for key, rows in buckets.items():
        col, values = collect_prior_identity_values({"_": rows})
        if col and values:
            out[key] = (col, values)
    return out


def build_prior_rows_block(
    prior_rows: dict | None,
    *,
    db_id: str | None = None,
    target_scope: dict[str, Any] | None = None,
) -> str:
    """선행 task 결과 서버 목록을 SQL 스코프 강제 블록으로 렌더링한다(없으면 빈 문자열).

    orchestration 데이터 의존(input_from) 경로에서 선행 task가 선별한 서버들로
    이번 SQL의 대상을 결정적으로 한정한다. 선별 조건(알람 상태·심각도 등)을 LLM이
    재표현하다 환각(존재하지 않는 resource_type='alarm.Alarm' 등)으로 0건이 되는
    것을 차단한다(D-086). 단일/멀티 DB 경로가 동일 블록을 사용한다(D-066).

    Args:
        prior_rows: {task_id: [식별 키 행, ...]} (subagents._make_isolated_input 산출)
        db_id: 주어지면 그 DB 소속 행(+태그 없는 행)만 렌더한다(D-203 DB별 분할). None=현행(전체).
        target_scope: 값 기반 키 브리지(plans/102 §3.3-③ · `nodes.key_bridge`)가
            **이미 계산한** 스코프. 주어지면 컬럼명 판정 대신 이 값으로 렌더한다
            (이 모듈은 값 판정·매니페스트를 모른다 — utils 계층).
            필수 키: `source_col`·`values`(상한 적용 정규형)·`key_type`.
            `condition`(코드가 확정한 SQL 조건)·`target_col`·`target_table`이 있으면
            대상 컬럼 확정 블록, 없으면 대상 매니페스트 부재 — 종전 위임 문구로 렌더.
            `total`·`truncated_count`는 절단 표기용. None=현행.

    Returns:
        프롬프트에 덧붙일 스코프 강제 블록(유효한 식별 값이 없으면 "")
    """
    if not prior_rows:
        return ""
    if db_id is not None:
        prior_rows = filter_prior_rows_for_db(prior_rows, db_id)
        if not prior_rows:
            return ""
    if target_scope is not None:
        return _render_target_scope_block(target_scope)
    col, values = _collect_prior_identity_values(prior_rows)
    if not values:
        return ""
    return _render_prior_scope_block(col, values)


def _quote_scope_values(values: list[str]) -> str:
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


def _render_target_scope_block(scope: dict[str, Any]) -> str:
    """키 브리지 스코프 블록 — 대상 컬럼·조건이 확정됐으면 LLM에 대응을 맡기지 않는다(X-T2)."""
    values = [str(v) for v in (scope.get("values") or []) if str(v).strip()]
    source_col = str(scope.get("source_col") or "")
    if not values or not source_col:
        return ""
    condition = str(scope.get("condition") or "")
    target_col = str(scope.get("target_col") or "")
    if not (condition and target_col):
        return _render_prior_scope_block(source_col, values)
    target_table = str(scope.get("target_table") or "")
    key_type = str(scope.get("key_type") or "")
    quoted = _quote_scope_values(values)
    shown_col = f"{target_table}.{target_col}" if target_table else target_col
    lines = [
        "## 선행 작업 결과 서버 스코프 (필수 준수)",
        "이번 조회 대상은 선행 작업에서 이미 선별된 아래 서버들로 **한정**합니다.",
        f"- 대상 서버 (선행 결과 `{source_col}` 컬럼의 {key_type} 값 · 정규형): {quoted}",
        f"- 대상 컬럼 (키 매니페스트로 확정): `{shown_col}`",
    ]
    truncated = int(scope.get("truncated_count") or 0)
    if truncated:
        total = int(scope.get("total") or len(values) + truncated)
        lines.append(
            f"- 선행 결과 {total}대 중 상한 {len(values)}대만 "
            f"대상입니다(초과 {truncated}대 제외)."
        )
    lines += [
        "규칙:",
        f"1. SQL에 서버 한정 조건 `{condition}` 을 그대로 포함하세요 "
        "(대상 컬럼은 코드가 확정했습니다 — 다른 컬럼으로 바꾸지 마세요. "
        "테이블 별칭을 쓰면 컬럼을 그 별칭으로 한정하고, "
        "GROUP BY 피벗 쿼리면 기존 규칙대로 HAVING의 집계 CASE WHEN으로 적용).",
        _PRIOR_SCOPE_RULE_NO_RESELECT,
        "3. 위 목록 외의 서버가 결과에 포함되어서는 안 됩니다.",
        f"4. 결과의 각 행이 어느 서버의 값인지 판정할 수 있도록 `{target_col}` 컬럼을 "
        "**별칭 없이 원래 이름 그대로** SELECT에 반드시 포함하세요 "
        "(GROUP BY 피벗 쿼리면 집계 CASE WHEN으로 포함).",
    ]
    return "\n".join(lines)


# 규칙 2 — 종전 블록(`_render_prior_scope_block`)과 같은 문장(선별 조건 재표현·환각 금지).
_PRIOR_SCOPE_RULE_NO_RESELECT = (
    "2. 서버를 선별했던 조건(알람 발생·심각도·활성 상태 등)은 "
    "선행 작업에서 이미 처리 완료되었습니다 — "
    "선별에 사용한 테이블·컬럼·조건(알람/이벤트 등)을 이 SQL에서 다시 표현하지 마세요. "
    "대상 DB에 존재하지 않는 테이블/컬럼/값으로 선별 조건을 지어내지 마세요(환각 금지)."
)


def _render_prior_scope_block(col: str, values: list[str]) -> str:
    """종전 스코프 블록(컬럼명 판정 · 대상 컬럼 대응은 LLM 위임)."""
    quoted = _quote_scope_values(values)
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


# 하위호환 별칭 — 교차 임포트 공개화(Plan 69 P2). 신규 코드는 공개명을 쓴다.
_normalize_stat_month = normalize_stat_month
_utilization_guard = utilization_guard
_collect_prior_identity_values = collect_prior_identity_values

# ── 폼필 확인 이력 명령 판정 (Plan 73 Phase 3, D-151 — 단일 출처) ────────────────
# intent_planner(②.7 단락)·query.py(존 역질문 스킵, FIX-20)·field_mapper(매핑 스킵,
# FIX-24)가 공유한다. "기억" 계열 명사 필수 — 일반 조회와 충돌 차단.
FORM_MEMORY_NOUN_KEYWORDS = (
    "기억", "저장된 답", "저장된 값", "확인 이력",
    # D-186: "이 양식에 저장된 내용은?" — '저장된 답/값'만 있어 미탐(라이브 실측 2026-08-25)
    "저장된 내용", "저장한 내용",
)
FORM_MEMORY_VIEW_KEYWORDS = (
    "보여", "조회", "알려",
    # D-186: 의문형("기억하는 내용은 뭐지?") — 명령형만 잡혀 존 역질문으로 흘렀다.
    # '?'는 명사 동반이 전제(아래 AND 규칙)라 "CPU 사용률은?"류 일반 질의에는 걸리지 않는다.
    "뭐", "무엇", "뭔지", "어떤", "?", "？",
)
FORM_MEMORY_DELETE_KEYWORDS = ("삭제", "지워", "잊어", "다시 물어")
FORM_MEMORY_ALL_KEYWORDS = ("전부", "전체", "모두", "모든")
# D-186: 채움 동사가 동반되면 이력 조회가 아니라 채움 요청("기억한 값으로 채워줘") — 조회로
# 단락되면 DB 조회가 통째로 이력 응답으로 대체되므로(3중 게이트) 미탐 쪽으로 보수적 판정.
_FORM_MEMORY_FILL_VERBS = ("채워", "채우", "작성", "기입", "반영")
# D-186: 양식 업로드 후 '?'만 입력 — 저장 값 조회 단축키(반각·전각·반복 허용). 정확 일치라 오탐 0.
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
        return True  # '?' 단축키(D-186)
    q = memory_query_normalized(text)
    if not any(k in q for k in FORM_MEMORY_NOUN_KEYWORDS):
        return False
    if any(k in q for k in FORM_MEMORY_DELETE_KEYWORDS):
        return True
    if any(v in q for v in _FORM_MEMORY_FILL_VERBS):
        return False  # 채움 요청은 이력 조회가 아니다(D-186 보수적 가드)
    return any(k in q for k in FORM_MEMORY_VIEW_KEYWORDS)


def is_form_memory_shortcut(text: str) -> bool:
    """'?'만 입력한 저장 값 조회 단축키인지(D-186) — 안내 문구 분기용."""
    stripped = (text or "").strip()
    return bool(stripped) and all(ch in _FORM_MEMORY_SHORTCUT_CHARS for ch in stripped)


# ── 존 역질문 공용 판정·페이로드 (Plan 75 §4 / D-143 후속2) ──────────────────────
# 위치 표면어 단일 출처(D-053 사본 금지). 종전 canonical은 input_parser였으나,
# 레거시 경로(semantic_router, infrastructure 계층)가 후단 게이트에서 같은 목록을
# 써야 해 계층 규칙(infrastructure→application 금지)상 utils로 내렸다.
# input_parser가 re-export하므로 기존 임포트 지점은 그대로 동작한다.
# 개발·스테이징·DR은 여의도 폴스타의 기준 용어다(D-271).
LOCATION_HINT_TERMS: tuple[str, ...] = (
    "공동존", "김포", "운영", "여의도", "개발", "스테이징", "DR", "은행", "레거시", "은행존",
)

# 존 역질문 스킵 신호 — 위치어 + 제품/DB 표면어(사용자가 대상을 이미 지목한 형태).
ZONE_SKIP_SIGNAL_TERMS: tuple[str, ...] = (*LOCATION_HINT_TERMS, "폴스타", "polestar")

_LATIN_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def _is_latin_term(term: str) -> bool:
    return bool(term) and all(ch in _LATIN_ALNUM for ch in term)


# 한글 표면어 뒤에 이 꼬리(정규식)가 오면 다른 뜻의 말이라 위치로 보지 않는다(D-271).
# "운영체제"(OS)는 OSType·OSVerson의 공식 별칭이다. "운영 중"·"운영중"(상태)·"운영자"·"운영팀"은
# 김포가 아니라 상태·사람·조직이다 — 김포로 좁히면 존 역질문이 생략되고 여의도가 빠진다
# (사용자 확정 2026-09-28 「넷 다 제외」). "운영 중요"처럼 "중" 뒤에 "요"가 오면 제외하지 않는다.
_TERM_EXCLUDED_TAILS: dict[str, tuple[str, ...]] = {
    "운영": ("체제", "자", "팀", r"\s*중(?!요)"),
}


def excluded_tail_lookahead(term: str) -> str:
    """표면어의 제외 꼬리를 부정 전방탐색으로 만든다(없으면 빈 문자열) — 정규식 소비처용."""
    tails = _TERM_EXCLUDED_TAILS.get(term, ())
    return "(?!" + "|".join(tails) + ")" if tails else ""


@functools.lru_cache(maxsize=256)
def _term_re(term: str) -> re.Pattern[str] | None:
    """부분 문자열 판정으로 부족한 표면어의 정규식(라틴 단어 경계 · 제외 꼬리). 없으면 None."""
    if _is_latin_term(term):
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.IGNORECASE)
    lookahead = excluded_tail_lookahead(term)
    if lookahead:
        return re.compile(re.escape(term) + lookahead)
    return None


def term_in_text(term: str, text: str) -> bool:
    """표면어가 텍스트에 있는가 — 위치·DB 표면어 판정의 공용 규칙(D-271).

    라틴 문자·숫자로만 된 표면어(DR 등)는 앞뒤가 라틴 문자·숫자가 아닐 때만 맞고 대소문자를
    무시한다 — "DRM"·"ADDRESS"가 여의도로 오라우팅되지 않게 한다. 한글 조사는 바로 붙으므로
    ("DR도"·"DR서버") 경계로 보지 않는다. 한글 표면어는 종전과 같은 부분 문자열 판정이되,
    다른 뜻의 복합어("운영체제")에 든 것은 세지 않는다.
    """
    if not term or not text:
        return False
    pattern = _term_re(term)
    if pattern is not None:
        return pattern.search(text) is not None
    return term in text


def term_spans(term: str, text: str) -> list[tuple[int, int]]:
    """`term_in_text`와 같은 규칙으로 표면어가 걸린 구간(시작·끝)을 왼쪽부터 모두 돌려준다."""
    if not term or not text:
        return []
    pattern = _term_re(term) or re.compile(re.escape(term))
    return [m.span() for m in pattern.finditer(text)]


def remove_term(text: str, term: str, repl: str = " ") -> str:
    """`term_in_text`와 같은 규칙으로 표면어를 지운다(라틴 단어 경계 · 제외 복합어 보존)."""
    if not term or not text:
        return text
    pattern = _term_re(term)
    if pattern is not None:
        return pattern.sub(repl, text)
    return text.replace(term, repl)


# ── 생략형 후속 턴 승계 진입 (plans/120 PL-1) — 보조 판정은 `prior_turn_succession` ──────
# 위치 표면어(`LOCATION_HINT_TERMS`·`term_in_text`)를 읽어 이 모듈에 남긴다(plans/166 N2).
#: 전역 조회 신호 — `refers_to_demonstrative_server`와 같은 어휘.
_GLOBAL_SCOPE_TERMS: tuple[str, ...] = ("전체", "모든", "모두")


def elliptical_succession_filter(
    text: str,
    parsed_requirements: Mapping[str, Any] | None,
    conversation_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """생략형 후속 턴이면 직전 서버의 식별 필터를, 아니면 None을 돌려준다 (plans/120 PL-1).

    발동 조건(전부):
      ⓐ 직전 턴이 단일 엔티티 스코프 — 엔티티가 직전 결과 전체를 담고 서버 1대로 해소된다.
      ⓑ 이번 턴에 새 식별자가 없다 — 서버·IP 식별 필터도, 원문의 식별자 표면형도 없다.
      ⓒ 이번 턴에 위치어가 없다(`target_db_hints` · 위치 표면어).
      ⓓ 이번 턴에 "전체·모든·모두"가 없다.
      ⓔ 이번 턴에 지시어가 없고 첨가 표지("~도" · "그럼"·"그러면")가 있다 — 지시어 턴은 종전
         경로(2단 `_inject_demonstrative_hostname` · 노드 `demonstrative_entity_scope`)가 잇는다.
      ⓕ 이번 턴 원문에 서버 계열 명사가 없다 — 대상 명사 자체가 빠진 턴만 생략형이다.
      ⓖ 이번 턴에 선택·집계 신호가 없다 — 식별 필드 밖 필터 · `limit` · 시계열 밖 집계 ·
         순위·집합어(상위·하위·top·순위·정렬·목록·리스트·나머지·다른·~별·각).
      그리고 조회 대상이 파싱된 질의다(잡담성 턴 제외 — 존 역질문 게이트와 같은 기준).

    Args:
        text: 이번 턴 사용자 원문
        parsed_requirements: 이번 턴 파싱 결과
        conversation_context: context_resolver가 채운 직전 턴 맥락

    Returns:
        ``{"field", "op": "=", "value"}`` 또는 None
    """
    text = text or ""
    parsed = parsed_requirements or {}
    if not parsed.get("query_targets"):
        return None
    if any(term in text for term in _GLOBAL_SCOPE_TERMS):
        return None
    if parsed.get("target_db_hints") or any(term_in_text(term, text) for term in LOCATION_HINT_TERMS):
        return None
    if refers_to_demonstrative_server(text) or not _has_additive_marker(text):
        return None
    if _has_new_identifier(text, parsed):
        return None
    if _selects_server_set(text, parsed):
        return None
    entity = _single_previous_entity(conversation_context)
    if entity is None:
        return None
    field, value = entity
    return {"field": field, "op": "=", "value": value}


# 존 선택지 — DB 라우팅 입도와 일치(D-143 §4.4). group은 존 그룹 상호배타(D-143 후속3):
# 은행존(bank)과 공동존(common)은 담당 조직이 달라 동시 조회 실수요가 없고(사용자 확정
# 2026-08-05), b0+gp 조합에서 FabriX PII 필터가 gp 생성 요청을 차단하는 미종결 이슈의
# 회피를 겸한다. 공동존 내 김포/여의도는 다중 선택 유지.
# 종전 canonical은 api/routes/query.py._ZONE_OPTIONS — 후단 게이트(orchestration·
# routing 계층)와 공유하기 위해 utils로 내림(query.py가 alias 유지).
# `group` 값은 레지스트리 `zone_groups`(D-176)와 **같은 값이어야 한다**. 다만 여기서
# 임포트 시점에 레지스트리를 읽으면 ①순환 임포트(`src/routing/__init__.py`가
# `semantic_router`를 즉시 임포트하고 그것이 이 모듈을 다시 읽는다) ②계층 위반
# (utils → infrastructure 금지)이 된다 — 둘 다 2026-08-28 실측. 그래서 값은 리터럴로
# 두고 **정합성은 테스트가 지킨다**
# (`tests/test_orchestration/test_execution_groups.py::test_group_values_match_registry`).
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
    "common": ("공동존", "김포", "운영", "여의도", "개발", "스테이징", "DR"),
}

ZONE_CLARIFY_QUESTION = (
    "조회할 존이 지정되지 않았습니다. 아래에서 대상 존을 선택해 주세요. "
    "(복수 선택 가능 — 전체 조회는 모두 선택. 은행존과 공동존을 함께 선택하면 "
    "은행존을 먼저 조회한 뒤 공동존을 조회합니다)"
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
        any(term_in_text(term, t) for term in terms) for terms in _ZONE_GROUP_TERMS.values()
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
_ZONE_ENUM_DR = r"(?<![A-Za-z0-9])DR(?![A-Za-z0-9])"  # 라틴 표면어는 단어 경계(D-271)
_ZONE_ENUM_OP = "운영" + excluded_tail_lookahead("운영")  # 운영체제·운영 중 등 제외(D-271)
_ZONE_ENUM_HEAD = (
    rf"(?:은행존|공동존|김포|{_ZONE_ENUM_OP}|여의도|개발|스테이징|{_ZONE_ENUM_DR}|레거시)"
)
_ZONE_ENUM_TAIL = (
    rf"(?:은행존|공동존|은행|레거시|김포|{_ZONE_ENUM_OP}|여의도|개발|스테이징|{_ZONE_ENUM_DR}"
    r"|센터|센타)"
)
_ZONE_ENUM_RE = re.compile(
    rf"{_ZONE_ENUM_HEAD}(?:\s*(?:및|와|과|,|·|/|그리고)?\s*{_ZONE_ENUM_TAIL})*",
    re.IGNORECASE,
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
        return replacement if any(term_in_text(t, span) for t in non_selected_terms) else span

    rewritten = _ZONE_ENUM_RE.sub(_sub, q)
    return re.sub(r"[ \t]{2,}", " ", rewritten).strip()
