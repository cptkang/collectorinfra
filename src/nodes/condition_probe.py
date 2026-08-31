"""조건 퍼널 프로브 — SQL 최상위 conjunct 수술 + COUNT 조립 (Plan 82 Wave 8 · D-176 후속1).

**무엇을 하나.** 0건이 난 SQL에서 **사용자 조건**만 골라내고, 조건을 하나씩 더해가는
누적 prefix SQL을 `SELECT COUNT(*)` 로 감싸 돌려준다. 단계별 잔존 건수가 나오면
`src/domain/empty_answer.py`가 끊긴 지점(MFS)을 판정한다.

**실행하지 않는다.** 조립만 하고 SQL 실행은 호출부(`result_organizer`)가 한다 —
이 모듈은 문자열 in / 문자열 out이라 DB 없이 전량 검증된다.

**LLM을 쓰지 않는다**(D-035 · 설계 제약 2). 조건 제거는 텍스트 조작이므로 결정적이어야
하고, 같은 SQL에 같은 프로브가 나와야 사용자가 단계 표를 신뢰할 수 있다.

## ★ 계획서 §6.4에서 벗어난 지점 — 프로브 대상을 화이트리스트로 좁혔다

계획서는 *"`filter_conditions` 누적 프로브"* 라고 썼지만 그대로는 구현할 수 없다 —
`filter_conditions`는 자연어 서술이고 SQL 조건과 1:1이 아니다. 실 SQL의 최상위 conjunct에는
**사용자 조건과 스키마 배관이 섞여 있고**, 배관을 벗기면 무의미한 수가 나온다:

    WHERE  <타입 등호>          -- 배관
      AND  <이름 등호>          -- 배관
      AND  <값 BETWEEN 가드>    -- 배관(쓰레기 값 상한)
      AND  <삭제시각 IS NULL>   -- 배관
    HAVING <집계> >= 80         -- ★ 사용자 조건

그래서 **사용자 조건 = 수치 비교(`>=` `>` `<=` `<`)로 끝나는 최상위 conjunct**로 좁힌다.
`BETWEEN`·`IS NULL`·문자열 등호는 배관으로 보고 건드리지 않는다.

**SPEC의 화이트리스트에서 한 겹 더 일반화했다**: SPEC은 값 컬럼명(지표 값 컬럼)을 열거했으나,
`src/nodes`는 `scripts/overfit_check.py`가 감시하는 **DB-agnostic 공용 계층**이라 특정 DB의
컬럼명을 코드에 박으면 D-088 위반이다. 비교 연산자 형태만으로 위 예시의 배관 4종이 전부
걸러지므로(등호·BETWEEN·IS NULL은 대상이 아님) 판정 결과는 같고 계층 규칙은 지킨다.
값 컬럼을 더 좁히고 싶으면 `value_columns` 인자로 주입한다(어댑터가 아는 지식은 어댑터가 준다).

계층: application (`src.nodes`) — 노드 간 직접 의존 0.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

#: 최상위 절 키워드. WHERE/HAVING의 끝을 찾는 데 쓴다.
_CLAUSE_ENDERS: tuple[str, ...] = (
    "GROUP", "HAVING", "ORDER", "LIMIT", "FETCH", "OFFSET", "UNION", "WINDOW",
    "INTERSECT", "EXCEPT",
)

#: 수치 비교로 끝나는 conjunct — 사용자 조건 판정식. 등호(`=`)는 배관(타입·이름)이라 제외한다.
_NUM_CMP_RE = re.compile(r"(>=|<=|>|<)\s*[-+]?\d+(?:\.\d+)?\s*$")

#: 파생 테이블 별칭 — DB2는 별칭 없는 파생 테이블을 거부한다.
_PROBE_ALIAS = "probe_src"


@dataclass(frozen=True)
class Conjunct:
    """최상위 conjunct 하나. `start`/`end`는 원본 SQL의 문자 오프셋이다."""

    text: str
    clause: str        # "WHERE" | "HAVING"
    start: int
    end: int
    is_user: bool


@dataclass(frozen=True)
class ConditionProbe:
    """퍼널 한 단계의 COUNT 프로브. `stage_index` 0 = 사용자 조건 0개(대상 전체)."""

    stage_index: int
    condition: str     # 이 단계에서 **더해진** 조건(0단계는 빈 문자열)
    sql: str


def _scan(sql: str) -> list[tuple[int, int]]:
    """문자별 (괄호 depth, 마스크 여부)를 계산한다.

    마스크 = 문자열 리터럴·따옴표 식별자·주석 내부. 마스킹된 구간의 키워드는 무시한다 —
    문자열 안의 `AND`·`WHERE`를 절 경계로 오인하면 SQL이 깨진다.
    """
    out: list[tuple[int, int]] = []
    depth = 0
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            out.append((depth, 1))
            i += 1
            while i < n:
                out.append((depth, 1))
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 1
                        out.append((depth, 1))
                    else:
                        i += 1
                        break
                i += 1
            continue
        if ch == '"':
            out.append((depth, 1))
            i += 1
            while i < n:
                out.append((depth, 1))
                if sql[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "-" and sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                out.append((depth, 1))
                i += 1
            continue
        if ch == "/" and sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            end = n if end < 0 else end + 2
            while i < end:
                out.append((depth, 1))
                i += 1
            continue
        if ch == "(":
            out.append((depth, 0))
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            out.append((depth, 0))
            i += 1
            continue
        out.append((depth, 0))
        i += 1
    return out


def _keyword_positions(sql: str, marks: Sequence[tuple[int, int]], word: str) -> list[int]:
    """최상위(depth 0 · 비마스크)에서 등장하는 키워드 위치를 찾는다(단어 경계)."""
    positions: list[int] = []
    for m in re.finditer(rf"\b{word}\b", sql, flags=re.IGNORECASE):
        idx = m.start()
        depth, masked = marks[idx]
        if depth == 0 and not masked:
            positions.append(idx)
    return positions


def _clause_span(
    sql: str, marks: Sequence[tuple[int, int]], keyword: str
) -> Optional[tuple[int, int, int]]:
    """(키워드 시작, 내용 시작, 내용 끝)을 돌려준다(최상위 키워드가 없으면 None)."""
    starts = _keyword_positions(sql, marks, keyword)
    if not starts:
        return None
    kw_start = starts[0]
    body_start = kw_start + len(keyword)
    end = len(sql)
    for ender in _CLAUSE_ENDERS:
        if ender.upper() == keyword.upper():
            continue
        for pos in _keyword_positions(sql, marks, ender):
            if body_start <= pos < end:
                end = pos
    return kw_start, body_start, end


def _split_and(
    sql: str, marks: Sequence[tuple[int, int]], start: int, end: int
) -> Optional[list[tuple[int, int]]]:
    """구간을 최상위 `AND`로 쪼갠 (시작, 끝) 목록. 최상위 `OR`가 있으면 None.

    `A OR B AND C`는 AND가 더 강하게 묶이므로 conjunct 하나를 떼면 의미가 달라진다 —
    쪼갤 수 없는 절은 **쪼개지 않고 포기**한다(잘못된 수보다 측정 안 함이 낫다).
    """
    for pos in _keyword_positions(sql, marks, "OR"):
        if start <= pos < end:
            return None

    ands = [p for p in _keyword_positions(sql, marks, "AND") if start <= p < end]
    # `BETWEEN a AND b`의 AND는 **연결자가 아니라 문법의 일부**다. 이걸 분리자로 세면
    # 값 타당성 가드가 두 조각으로 찢어져 배관이 조건처럼 보인다(실측 2026-08-28).
    for between in _keyword_positions(sql, marks, "BETWEEN"):
        if not start <= between < end:
            continue
        consumed = next((p for p in ands if p > between), None)
        if consumed is not None:
            ands.remove(consumed)

    bounds: list[tuple[int, int]] = []
    cursor = start
    for pos in ands:
        bounds.append((cursor, pos))
        cursor = pos + len("AND")
    bounds.append((cursor, end))
    return [(s, e) for s, e in bounds if sql[s:e].strip()]


def _is_user_condition(text: str, value_columns: Optional[Sequence[str]]) -> bool:
    """수치 비교로 끝나는 conjunct인가 — 배관(등호·BETWEEN·IS NULL)은 제외한다."""
    stripped = text.strip()
    marks = _scan(stripped)
    m = _NUM_CMP_RE.search(stripped)
    if not m or marks[m.start(1)][0] != 0 or marks[m.start(1)][1]:
        return False
    if re.search(r"\bBETWEEN\b", stripped, flags=re.IGNORECASE):
        return False
    if value_columns and not any(
        re.search(rf"\b{re.escape(col)}\b", stripped, flags=re.IGNORECASE)
        for col in value_columns
    ):
        return False
    return True


def _normalize(sql: str) -> str:
    """뒤쪽 공백·세미콜론만 떼어낸다 — 앞쪽은 건드리지 않아 문자 오프셋이 유지된다."""
    return (sql or "").rstrip().rstrip(";").rstrip()


def _conjuncts(sql: str, value_columns: Optional[Sequence[str]]) -> list[Conjunct]:
    marks = _scan(sql)
    found: list[Conjunct] = []
    for keyword in ("WHERE", "HAVING"):
        span = _clause_span(sql, marks, keyword)
        if not span:
            continue
        _kw_start, body_start, body_end = span
        parts = _split_and(sql, marks, body_start, body_end)
        if parts is None:
            logger.info("최상위 OR가 있어 %s 절은 분해하지 않는다(프로브 제외)", keyword)
            continue
        for start, end in parts:
            text = sql[start:end].strip()
            found.append(Conjunct(
                text=text,
                clause=keyword.upper(),
                start=start,
                end=end,
                is_user=_is_user_condition(text, value_columns),
            ))
    return found


def split_user_conditions(
    sql: str, *, value_columns: Optional[Sequence[str]] = None
) -> list[Conjunct]:
    """SQL의 최상위 WHERE·HAVING conjunct 중 **사용자 조건**만 원문 순서로 돌려준다.

    서브쿼리 내부(괄호 안)는 보지 않고, 문자열 리터럴·주석 안의 키워드는 무시한다.

    Args:
        sql: 0건이 난 원본 SQL
        value_columns: 지정하면 이 컬럼/별칭을 참조하는 비교만 사용자 조건으로 본다
            (어댑터가 아는 값 컬럼을 좁혀 넣는 주입점 — 미지정이면 연산자 형태만으로 판정).
    """
    return [c for c in _conjuncts(_normalize(sql), value_columns) if c.is_user]


def _strip_tail(sql: str) -> str:
    """최상위 ORDER BY·LIMIT·FETCH FIRST·OFFSET과 세미콜론을 떼어낸다.

    COUNT 프로브에서 정렬은 무의미하고, 행 제한이 남으면 **잔존 건수가 상한에 눌려**
    퍼널이 틀린 수를 보여준다(1,204를 100으로 읽는다).
    """
    body = sql.strip().rstrip(";").rstrip()
    marks = _scan(body)
    cut = len(body)
    for keyword in ("ORDER", "LIMIT", "FETCH", "OFFSET"):
        for pos in _keyword_positions(body, marks, keyword):
            cut = min(cut, pos)
    return body[:cut].rstrip()


def _rebuild(sql: str, conjuncts: Sequence[Conjunct], keep: set[int]) -> str:
    """유지할 conjunct만 남긴 SQL을 만든다(제거 대상이 절 전체면 절 키워드도 뗀다)."""
    marks = _scan(sql)
    edits: list[tuple[int, int, str]] = []

    for keyword in ("WHERE", "HAVING"):
        span = _clause_span(sql, marks, keyword)
        if not span:
            continue
        kw_start, _body_start, body_end = span
        in_clause = [
            (i, c) for i, c in enumerate(conjuncts) if c.clause == keyword.upper()
        ]
        if not in_clause:
            continue
        kept = [c.text for i, c in in_clause if i in keep or not c.is_user]
        if kept:
            replacement = f"{keyword} " + "\n  AND ".join(kept) + "\n"
        else:
            replacement = ""
        edits.append((kw_start, body_end, replacement))

    out = sql
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + replacement + out[end:]
    return out


def build_probe_sqls(
    sql: str,
    k_max: int = 5,
    *,
    value_columns: Optional[Sequence[str]] = None,
) -> list[ConditionProbe]:
    """조건을 하나씩 더해가는 누적 COUNT 프로브를 조립한다.

    0단계는 사용자 조건을 **전부 뺀** 대상 전체, i단계는 앞에서부터 i개를 적용한 것이다.
    마지막(전체 조건) 단계는 이미 0건임이 확인된 상태라 프로브를 만들지 않는다.

    조건 순서는 **SQL 원문 순서**를 따른다 — 순서를 바꾸면 끊긴 지점이 달라져 사용자가
    자기 문장과 대조할 수 없다(§6.4).

    Args:
        sql: 0건이 난 원본 SQL
        k_max: 프로브 상한(NP-hard 회피 · 초과분은 만들지 않는다 — 호출부가 절단 사실을 노출)
        value_columns: `split_user_conditions` 참조

    Returns:
        단계 오름차순 프로브 목록. **사용자 조건이 0개면 빈 리스트**(프로브를 돌리지 않는다).
    """
    sql = _normalize(sql)
    conjuncts = _conjuncts(sql, value_columns)
    user_indexes = [i for i, c in enumerate(conjuncts) if c.is_user]
    if not user_indexes:
        return []

    probes: list[ConditionProbe] = []
    for stage in range(min(len(user_indexes), max(0, k_max))):
        keep = set(user_indexes[:stage])
        inner = _strip_tail(_rebuild(sql, conjuncts, keep))
        probes.append(ConditionProbe(
            stage_index=stage,
            condition="" if stage == 0 else conjuncts[user_indexes[stage - 1]].text,
            sql=f"SELECT COUNT(*) FROM (\n{inner}\n) {_PROBE_ALIAS}",
        ))
    return probes


def truncated_stage_count(
    sql: str, k_max: int = 5, *, value_columns: Optional[Sequence[str]] = None
) -> int:
    """상한 때문에 측정하지 못하는 단계 수(0이면 절단 없음).

    호출부가 *"이후 단계는 측정하지 않았습니다"* 를 응답에 남기는 데 쓴다 —
    침묵 절단은 "전부 확인했다"로 읽힌다.
    """
    total = len(split_user_conditions(sql, value_columns=value_columns))
    return max(0, total - max(0, k_max))
