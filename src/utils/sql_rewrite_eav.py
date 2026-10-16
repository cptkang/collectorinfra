"""EAV 값 컬럼 캐스트 결정적 재작성 — 정수 캐스트 → NUMERIC(D-160) · 단위 정규화(D-199).

plans/166 N2(D-337)로 `src/utils/query_gen_common.py`에서 옮겼다(동작 불변 Move-only).
옮긴 이름은 `query_gen_common`이 모두 재노출한다.
"""

from __future__ import annotations

import logging
import re
import warnings
from typing import Any

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 N2)
logger = logging.getLogger("src.utils.query_gen_common")

# EAV 값 컬럼에 걸린 정수 캐스트 타입(교정 대상). NUMERIC은 PostgreSQL·DB2 공통 유효
# (DB2에서 DECIMAL 동의어)라 방언 분기가 필요 없다. 긴 이름을 앞에 둔다(INT가 INTEGER를
# 부분 선점하지 않도록 — 대안 순서 의존).
_EAV_INT_CAST_TYPES = r"(?:BIGINT|INTEGER|SMALLINT|INT8|INT4|INT2|INT)"


def eav_value_cast_columns(eav_pattern: dict[Any, Any] | None) -> tuple[str, ...]:
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
        open_pos = _matching_paren_backward(sql, m.start())  # type: ignore[assignment]
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


# 단위 정규화 대상 숫자 캐스트 타입(D-199). D-160과 달리 여기는 "이미 숫자 캐스트인데
# 값에 단위 접미('14.9 GB')가 붙어 실행이 깨지는" 케이스라 NUMERIC/DECIMAL이 대상이다.
_EAV_UNIT_CAST_TYPES = r"(?:NUMERIC|DECIMAL|DEC)"
# 이 가드가 생성하는 캐스트 피연산자의 공통 접두(단위 분기·ELSE 분기 양쪽) —
# 재적용(이중 래핑) 방지용 마커. LLM이 자발적으로 NULLIF(TRIM(…))을 쓴 경우도
# 스킵되지만, 그건 이미 정규화 시도가 있는 식이라 추가 래핑 실익이 없다.
_UNIT_GUARD_MARKER = "NULLIF(TRIM("


# 단위 접미 표본(D-199 실측 단위 TB/GB/MB/KB) — LLM 정규식 삭제가 단위를 지우는지 판정한다.
_UNIT_PROBES = ("1.5 TB", "14.9 GB", "965.5 MB", "512 KB")


def _regex_strips_unit(pattern_literal: str, flags_literal: str = "") -> bool:
    """정규식 삭제(``REGEXP_REPLACE(X, '<패턴>', '')``)가 값의 단위 접미를 지우는가.

    패턴은 파이썬 ``re``로 표본에 적용해 본다 — 컴파일되지 않으면(POSIX 전용 문법 등)
    지우지 않는 것으로 본다(무변경 쪽이 하방 안전).
    """
    pattern = pattern_literal.strip()[1:-1]
    rx_flags = re.IGNORECASE if "i" in flags_literal.lower() else 0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # 「[[:alpha:]]」 같은 중첩 집합 경고
            rx = re.compile(pattern, rx_flags)
    except re.error:
        return False
    return any(p.split()[1] not in rx.sub("", p) for p in _UNIT_PROBES)


def _unwrap_llm_unit_replace(operand: str) -> str:
    """LLM이 만든 단위 제거 임기응변을 벗긴다(D-199 후속).

    - ``REPLACE(<식>, '<단위>', '')`` — REPLACE는 sql_guard 금지 키워드(MySQL
      ``REPLACE INTO`` 차단)라 검증기가 SQL 전체를 거부한다(2026-09-07 폐쇄망 실측 —
      B0/GP/YD 전부 재생성 루프 유발).
    - ``REGEXP_REPLACE(<식>, '<패턴>', ''[, '<플래그>'])`` 중 패턴이 단위를 지우는 것
      (예 ``'[^0-9\\.]'``) — 벗기지 않으면 CASE가 단위를 못 봐 전부 ELSE(GB 가정)로
      떨어진다. run ``20260923-103638`` R4-10: 「965.5 MB」가 965.5 GB로 읽혀 「64GB 넘는
      서버」가 198건이 됐다(원값으로 판정한 반복은 197건 — plans/123 S-1b).

    피연산자가 정확히 그 호출 하나면 X만 남긴다 — 단위 처리는 이 가드의 CASE가 원값
    기준으로 대신한다. 형태가 다르면 무변경.
    """
    m = re.match(r"\s*(REGEXP_REPLACE|REPLACE)\s*\(", operand, flags=re.IGNORECASE)
    if not m:
        return operand
    open_pos = m.end() - 1
    close_pos = _matching_paren(operand, open_pos)
    if close_pos is None or operand[close_pos + 1:].strip():
        return operand  # 호출이 피연산자 전체가 아니면 보수적으로 무변경
    # 최상위 콤마로 인자 분해
    inner = operand[open_pos + 1: close_pos]
    args: list[str] = []
    depth = 0
    last = 0
    for i, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(inner[last:i])
            last = i + 1
    args.append(inner[last:])

    def literal(arg: str) -> bool:
        return re.fullmatch(r"\s*'[^']*'\s*", arg) is not None

    if len(args) < 3 or not re.fullmatch(r"\s*'\s*'\s*", args[2]) or not literal(args[1]):
        return operand
    if m.group(1).upper() == "REPLACE":
        return args[0].strip() if len(args) == 3 else operand
    flags = args[3] if len(args) == 4 else "''"
    if len(args) > 4 or not literal(flags) or not _regex_strips_unit(args[1], flags):
        return operand
    return args[0].strip()


def unit_normalized_expr(operand: str) -> str:
    """단위 접미 문자열을 GB 기준 숫자로 정규화하는 방언 중립 SQL 식을 만든다(D-199).

    CASE·LIKE·SUBSTR·LENGTH·TRIM·NULLIF·UPPER·CAST만 사용 — PostgreSQL과 DB2에서
    동일 구문이며 **sql_guard 금지 키워드와 겹치지 않는다**(REPLACE는 금지 키워드,
    regexp_replace는 PG 'g' 플래그·DB2 인자 차이, TRANSLATE는 인자 순서가 방언마다
    달라 전부 배제 — 2026-09-07 폐쇄망 검증기 거부 실측 후 위치 기반으로 확정).
    단위 제거는 "값 끝의 2글자 단위" 위치 기반(SUBSTR)이라 대소문자 무관하다.
    단위가 없는 값('4.0' 등)은 ELSE에서 있는 그대로 캐스트한다(스케일 불변).
    """
    x = _unwrap_llm_unit_replace(operand.strip())
    y = f"TRIM({x})"
    strip_cast = (
        f"CAST(NULLIF(TRIM(SUBSTR({y}, 1, LENGTH({y}) - 2)), '')"
        " AS DECIMAL(31, 6))"
    )
    return (
        "CASE"
        f" WHEN UPPER({x}) LIKE '%TB%' THEN {strip_cast} * 1024"
        f" WHEN UPPER({x}) LIKE '%GB%' THEN {strip_cast}"
        f" WHEN UPPER({x}) LIKE '%MB%' THEN {strip_cast} / 1024.0"
        f" WHEN UPPER({x}) LIKE '%KB%' THEN {strip_cast} / 1048576.0"
        f" ELSE CAST(NULLIF(TRIM({x}), '') AS DECIMAL(31, 6)) END"
    )


def normalize_eav_unit_casts(sql: str, value_columns: tuple[str, ...] | list[str]) -> str:
    """EAV 값 컬럼의 숫자 캐스트를 단위 인지 정규화 식으로 교체한다 (D-199).

    폐쇄망 실측(2026-09-07 B-11): 메모리 크기 EAV 값이 ``"14.9 GB"`` 단위 문자열이라
    ``CAST(… AS NUMERIC)``이 ``invalid input syntax for type numeric``으로 거부됐다.
    LLM은 ``REPLACE(…, 'GB', '')`` 임기응변을 만들기도 하는데 GB만 처리해 TB 값에서
    깨진다. 실측 단위 분포는 세 존 모두 TB/GB/MB 혼재(B0: TB 26,073·GB 21,783·MB 21 /
    GP: GB 10,449·TB 1,455·MB 43 / YD: GB 4,796·TB 743·MB 1)라 **단위 제거만으로는
    900 MB > 14.9 GB로 정렬되는 침묵 오류**가 되므로 GB 기준 환산 CASE로 교체한다.

    스코프·안전:
    - 값 컬럼이 포함된 NUMERIC/DECIMAL 캐스트만 대상(D-160과 동일 탐지 골격).
      ``CAST(r.id AS NUMERIC)`` 같은 무관 캐스트는 불변.
    - 단위 없는 값('4.0' vcore 등)은 ELSE 분기에서 스케일 불변 — vcore 합계류(D-160
      계열) 결과에 영향 없음.
    - LLM의 ``REPLACE(…, 'GB', '')`` 임기응변 위에 겹쳐 적용돼도 정합(GB는 이미
      제거돼 ELSE=GB 기준, 잔존 TB/MB는 해당 분기에서 환산).
    - 이 가드가 만든 식은 재적용 시 스킵(마커 검사) — 이중 래핑 방지.
    - 괄호 불균형·형태 미상은 무변경(하방 안전, D-160과 동일).

    Args:
        sql: LLM이 생성한(그리고 D-160 교정을 거친) SQL
        value_columns: 교정 대상 값 컬럼명들 (``eav_value_cast_columns`` 산출)

    Returns:
        교정된(또는 원본 그대로의) SQL — 교정 발생 시 INFO 로그
    """
    if not sql or not value_columns:
        return sql
    cols_re = r"\b(?:" + "|".join(re.escape(c) for c in value_columns if c) + r")\b"
    if cols_re == r"\b(?:)\b":
        return sql

    # (치환 시작, 치환 끝, 피연산자) — 오른쪽부터 치환해 앞쪽 오프셋을 보존한다.
    repls: list[tuple[int, int, str]] = []

    # 형태 1: CAST(<값 컬럼 포함 식> AS NUMERIC|DECIMAL[(p[,s])]) — 균형 스캔
    for m in re.finditer(r"CAST\s*\(", sql, flags=re.IGNORECASE):
        open_pos = m.end() - 1
        close_pos = _matching_paren(sql, open_pos)
        if close_pos is None:
            continue
        inner = sql[open_pos + 1: close_pos]
        tm = re.search(
            rf"\s+AS\s+{_EAV_UNIT_CAST_TYPES}\s*(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?\s*$",
            inner, flags=re.IGNORECASE,
        )
        if not tm:
            continue
        operand = inner[: tm.start()]
        if not re.search(cols_re, operand, flags=re.IGNORECASE):
            continue
        if _UNIT_GUARD_MARKER in operand:  # 이미 이 가드가 만든 식 — 재래핑 금지
            continue
        repls.append((m.start(), close_pos + 1, operand))

    # 형태 2: PostgreSQL 축약 캐스트 — <값 컬럼>::numeric[(p,s)]
    for m in re.finditer(
        rf"((?:\w+\.)?{cols_re})\s*::\s*{_EAV_UNIT_CAST_TYPES}"
        r"(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?\b",
        sql, flags=re.IGNORECASE,
    ):
        repls.append((m.start(), m.end(), m.group(1)))

    # 형태 3: (…값 컬럼…)::numeric — 닫는 괄호 역방향 균형 스캔
    for m in re.finditer(
        rf"\)\s*::\s*{_EAV_UNIT_CAST_TYPES}(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?\b",
        sql, flags=re.IGNORECASE,
    ):
        open_pos = _matching_paren_backward(sql, m.start())  # type: ignore[assignment]
        if open_pos is None:
            continue
        operand = sql[open_pos: m.start() + 1]
        if not re.search(cols_re, operand, flags=re.IGNORECASE):
            continue
        if _UNIT_GUARD_MARKER in operand:
            continue
        repls.append((open_pos, m.end(), operand))

    if not repls:
        return sql
    # 중첩 스팬 제거(바깥 CAST가 안쪽 :: 캐스트를 포함하는 경우 바깥 우선)
    repls.sort(key=lambda r: (r[0], -r[1]))
    picked: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, operand in repls:
        if start >= last_end:
            picked.append((start, end, operand))
            last_end = end
    result = sql
    for start, end, operand in sorted(picked, reverse=True):
        result = result[:start] + unit_normalized_expr(operand) + result[end:]
    logger.info(
        "[EAV단위캐스트] 값 컬럼 숫자 캐스트 → 단위 정규화(GB 기준) 교정 %d건 (cols=%s)",
        len(picked), list(value_columns),
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


# 하위호환 별칭(D-337 ③ — plans/166 N2) — `query_gen_common`이 옛 이름으로 import한다.
# 다음 계획에서 `grep -rw <이름>` 0 확인 후 제거한다.
_unit_normalized_expr = unit_normalized_expr
