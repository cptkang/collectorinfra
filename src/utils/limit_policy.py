"""조회 건수(LIMIT) 정책 — 명시 건수·「전체」 스코프 상향·말미 캡 교정 + 표면어 폴백 계측.

plans/166 N2(D-337)로 `src/utils/query_gen_common.py`에서 옮겼다(동작 불변 Move-only).
폴백 계측 카운터(`note_fallback`)는 이 모듈의 LIMIT 판정 두 곳과 `query_gen_common`의 기간
해석(`resolve_stat_month_range`)이 함께 쓴다 — 호출처가 많은 쪽으로 옮겼다.
옮긴 이름은 `query_gen_common`이 모두 재노출한다.
"""

from __future__ import annotations

import logging
import re
from typing import Any

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 N2)
logger = logging.getLogger("src.utils.query_gen_common")

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

    LIMIT 상향(`resolve_query_limit`)과 검증기의 행 제한 **상향**(`query_validator`·멀티 경로
    `_auto_limit_or_none`)이 같은 판정을 공유하도록 단일 출처로 둔다(종전에는 두 곳이 각자
    부분문자열 매칭 튜플을 들고 있었다). **「스킵」이 아니라 「상향」이다** — 종전 검증기는
    전체 조회에서 자동 추가를 통째로 건너뛰어 두 경로가 반대로 말했다(plans/98 CU-2).

    Args:
        text: 사용자 원문 질의

    Returns:
        전체 스코프 지시가 있으면 True
    """
    return _has_scope_term(text, _ALL_QUERY_KEYWORDS, instrument=True)


# 고지용 「전체」 표면어 — D-279 ⑦ 문구(전체·모든·모두) 그대로다. LIMIT 상향용
# `_ALL_QUERY_KEYWORDS`의 「서버별」·「서버들」·「각 서버」는 전 서버 **나열** 의도일 뿐
# 「조회 가능 존 전체」 요청이 아니다 — 고지에 쓰면 거짓 「전체가 아니라 …」가 붙는다(plans/151 F3).
_FULL_SCOPE_REQUEST_KEYWORDS: tuple[str, ...] = ("모든", "전체", "모두")


def has_full_scope_request(text: str | None) -> bool:
    """질의가 「전체·모든·모두」로 범위 전체를 요청했는지 판정한다 — 범위 고지 전용(plans/151 W3).

    「전체가 아니라 …」 고지(`render_narrowed_note`·`scope_partial_text`)의 발동 판정이다.
    파생 접미 거름(「전체적」)은 `has_all_scope_keyword`와 같다. 계측 카운터는 LIMIT 판정만
    남긴다(같은 질의를 두 번 세지 않게).

    Args:
        text: 사용자 원문 질의

    Returns:
        「전체·모든·모두」 지시가 있으면 True
    """
    return _has_scope_term(text, _FULL_SCOPE_REQUEST_KEYWORDS, instrument=False)


def _has_scope_term(
    text: str | None, keywords: tuple[str, ...], *, instrument: bool
) -> bool:
    """표면어가 파생 접미(`_ALL_SCOPE_DERIV_SUFFIXES`) 없이 한 번이라도 나오는지 본다."""
    body = text or ""
    for kw in keywords:
        start = body.find(kw)
        while start != -1:
            tail = body[start + len(kw):]
            if not tail.startswith(_ALL_SCOPE_DERIV_SUFFIXES):
                return True
            if instrument:
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
# 「N건 이상·이하·초과·미만·넘는」은 **조건**이지 결과 건수가 아니다(plans/123 W-0 — 「알람 3건
# 이상 발생한 서버」가 LIMIT 3으로 읽혀 격리 입력 `resolved_limit`으로 모든 task에 승격됐다
# · R1-08).
_EXPLICIT_COUNT_RE = re.compile(
    r"(\d{1,6})\s*건(?!\s*(?:을|를|이|가)?\s*(?:이상|이하|초과|미만|넘|보다|까지|부터))"
)
_TOP_N_RE = re.compile(r"상위\s*(\d{1,6})")


def explicit_row_count(user_query: str | None) -> int | None:
    """질의가 명시한 결과 건수("100건"·"상위 10") — 없으면 None (plans/123 W-0·W-1 ②).

    `resolve_query_limit`의 첫 판정과 같은 규칙이다. 상한 절단 고지(`output_generator`)가 이 값과
    같은 상한이면 「요청한 만큼 가져온 것」이라 절단으로 말하지 않는다.
    """
    text = user_query or ""
    m = _EXPLICIT_COUNT_RE.search(text) or _TOP_N_RE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    return n if n > 0 else None


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
    """  # noqa: E501
    text = user_query or ""
    n = explicit_row_count(text)
    if n is not None:
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
    state: dict[Any, Any],
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
