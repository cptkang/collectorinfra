"""SQL 시간 조건 대조 공용 틀 — 상태 비결합 순수 함수 (plans/122 T-5b · D-309).

생성 SQL의 시간 조건(컬럼 대 리터럴 비교 · BETWEEN · IN)을 읽어 반개구간으로 정규화한다.
어느 테이블·컬럼이 시간 축이고 리터럴이 어떤 형식인지는 DB 특화라 어댑터가 정한다(D-089) —
이 틀은 컬럼·테이블 이름과 칸 증가 함수를 인자로 받는다. 읽지 못하는 조건은 `literal=False`와
사유(`reason`)로 표시해 호출부가 반려·경고를 고르게 한다(거짓 거부 방지).

`src.sql_validation`의 정규식 헬퍼(`_strip_comments_and_literals`·`_extract_alias_map`·
`_extract_table_names`)를 쓰지 않는 이유: 위치를 보존하지 않고 괄호 깊이·CASE·하위 질의 범위·
큰따옴표 식별자·쉼표 조인 별칭을 가리지 못하는 근사라, 조건을 SELECT 블록 단위로 묶고 CASE WHEN
피벗·조인 키를 걸러내야 하는 이 대조에는 맞지 않는다(리뷰 m-3 — 검증 코어에서 분리).

계층: application — `src.sql_validation`·`src.db_adapters`와 같은 높이(`scripts/arch_check.py`).
DB 특화 리터럴(테이블·컬럼명)을 두지 않는다 — DB별 규칙은 `src/db_adapters/<db>/validators.py`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

#: DB 현재시각 함수·상대 기간 산술 — PG(`CURRENT_DATE`·`NOW()`·`INTERVAL`) · DB2(`CURRENT DATE`·
#: `- 1 DAY` 표지 기간) · 기타 엔진(`SYSDATE`·`GETDATE()`·`CURDATE()`).
#: 낱말 함수는 접두(`x.`)·큰따옴표 식별자(`"interval"`) 안에서는 잡지 않는다
#: (같은 이름 컬럼 오탐 방지).
_NOW_FUNCTION_RE = re.compile(
    r'(?<![\w."])(?:'
    r"CURRENT(?:_|\s+)(?:DATE|TIMESTAMP|TIME)"
    r"|LOCALTIMESTAMP|LOCALTIME|SYSDATE|SYSTIMESTAMP"
    r"|(?:NOW|GETDATE|CURDATE|SYSDATETIME|UTC_TIMESTAMP)\s*\(\s*\)"
    r"|INTERVAL"
    r')(?![\w"])'
    r"|[-+]\s*\d+\s*(?:YEARS?|MONTHS?|DAYS?|HOURS?|MINUTES?|SECONDS?)\b",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(
    r"(?P<str>'[^']*')"
    r"|(?P<num>\d+(?:\.\d+)?)"
    r'|(?P<word>[^\W\d][\w$#@]*|"[^"]*")'
    r"|(?P<cast>::)"
    r"|(?P<op>>=|<=|<>|!=|\|\||=|<|>|[-+*/%])"
    r"|(?P<lp>\()|(?P<rp>\))|(?P<comma>,)|(?P<dot>\.)|(?P<semi>;)"
    r"|(?P<other>\S)"
)
_CMP_OPS = frozenset({"=", "<>", "!=", ">", ">=", "<", "<="})
_ARITH_OPS = frozenset({"||", "+", "-", "*", "/", "%"})
_FLIP_OP = {"=": "=", "<>": "<>", "!=": "!=", ">": "<", ">=": "<=", "<": ">", "<=": ">="}
_TYPED_LITERAL_WORDS = frozenset({"TIMESTAMP", "DATE", "TIME"})
_SET_OP_WORDS = frozenset({"UNION", "EXCEPT", "INTERSECT", "MINUS"})
#: 여는 괄호 앞에 와도 함수 호출이 아닌 낱말(묶음 괄호 · 목록 · 하위 질의).
_NON_FUNCTION_WORDS = frozenset({
    "AND", "OR", "NOT", "IN", "EXISTS", "WHERE", "ON", "HAVING", "WHEN", "THEN", "ELSE",
    "SELECT", "FROM", "JOIN", "AS", "BY", "VALUES", "CASE", "ALL", "ANY", "SOME", "LATERAL",
    "USING", "UNION", "EXCEPT", "INTERSECT", "MINUS", "BETWEEN", "LIKE", "IS", "DISTINCT",
    "WITH", "RETURN", "SET", "INTO", "TABLE",
})
#: 조건 하나의 오른쪽 끝을 이루는 낱말.
_RIGHT_BOUNDARY_WORDS = frozenset({
    "AND", "OR", "THEN", "WHEN", "ELSE", "END", "GROUP", "ORDER", "LIMIT", "FETCH", "OFFSET",
    "UNION", "EXCEPT", "INTERSECT", "MINUS", "WHERE", "HAVING", "ON", "JOIN", "LEFT", "RIGHT",
    "INNER", "FULL", "CROSS", "FROM", "SELECT", "WINDOW", "QUALIFY", "USING",
})
#: 조건 하나의 왼쪽 끝을 이루는 낱말.
_LEFT_BOUNDARY_WORDS = frozenset({
    "AND", "OR", "NOT", "WHERE", "ON", "HAVING", "WHEN", "THEN", "ELSE", "SELECT", "BY",
})
#: 조건이 걸린 절 — 행을 거르는 절(WHERE·ON·HAVING)만 시간 조건으로 본다(CASE WHEN 피벗 제외).
_FILTER_CLAUSE_WORDS = frozenset({"WHERE", "ON", "HAVING"})
_NON_FILTER_CLAUSE_WORDS = frozenset({
    "SELECT", "THEN", "ELSE", "BY", "FROM", "SET", "VALUES", "USING", "RETURN",
})
#: OR 결합 판정의 절 경계 — 이 안에서 OR를 만나면 조건을 교집합으로 읽을 수 없다.
_GROUP_STOP_WORDS = frozenset({
    "OR", "WHERE", "ON", "HAVING", "SELECT", "FROM", "GROUP", "ORDER", "LIMIT", "FETCH",
    "OFFSET", "UNION", "EXCEPT", "INTERSECT", "MINUS", "JOIN", "LEFT", "RIGHT", "INNER", "FULL",
    "CROSS", "WHEN", "THEN", "ELSE", "WINDOW", "QUALIFY",
})
_TABLE_LEAD_WORDS = frozenset({"FROM", "JOIN"})
_ALIAS_STOP_WORDS = _RIGHT_BOUNDARY_WORDS | frozenset({
    "AS", "OUTER", "NATURAL", "LATERAL", "WITH", "TABLESAMPLE", "FOR",
})


@dataclass(frozen=True)
class _Tok:
    kind: str
    text: str
    start: int
    end: int

    @property
    def upper(self) -> str:
        return self.text.upper()


@dataclass(frozen=True)
class _Structure:
    match: dict[int, int]            # 여는 괄호 ↔ 닫는 괄호(토큰 위치)
    parent: list[int]                # 토큰별 가장 안쪽 여는 괄호(-1 = 최상위)
    subquery: frozenset[int]         # 하위 질의를 여는 괄호
    scope: list[tuple[int, int]]     # 토큰별 (하위 질의 괄호 · -1 = 최상위, 집합 연산 가지 번호)
    case_match: dict[int, int]       # CASE ↔ END


@dataclass(frozen=True)
class ColumnCondition:
    """시간 컬럼 하나에 대한 거르는 조건 하나(WHERE·ON·HAVING — CASE WHEN 안은 제외).

    Attributes:
        qualifier: 컬럼 접두(별칭·테이블 이름의 마지막 마디 · 소문자 · 없으면 "")
        op: `=`·`>`·`>=`·`<`·`<=`·`between`·`in`(읽은 경우) 또는 원 연산자(`like`·`not in` 등)
        values: 리터럴 값(따옴표·`TIMESTAMP` 접두·형 변환 제거). `literal=False`면 비어 있다
        literal: 조건을 리터럴 비교로 읽었는가. False = 파싱 불가 — 사유는 `reason`
        scope: (하위 질의를 여는 괄호 위치 · -1 = 최상위, 집합 연산 가지 번호) — 같은 SELECT
            블록의 조건끼리만 교집합을 낸다
        text: 조건 원문(공백 정규화 — 로그·재생성 사유용)
        now_functions: 조건 안의 DB 현재시각 함수(`find_now_functions`와 같은 표기)
        reason: 파싱 불가 사유(`literal=True`면 "") — `REASON_OR`(OR로 다른 조건과 묶임) ·
            `REASON_COLUMN`(상대가 컬럼 참조 — 조인 키·CTE 컬럼) · `REASON_EXPRESSION`(그 밖:
            식·서브쿼리·함수로 감싼 컬럼·LIKE 등)
    """

    qualifier: str
    op: str
    values: tuple[str, ...]
    literal: bool
    scope: tuple[int, int]
    text: str
    now_functions: tuple[str, ...] = ()
    reason: str = ""


#: `ColumnCondition.reason` 값.
REASON_OR = "or"
REASON_COLUMN = "column"
REASON_EXPRESSION = "expression"


def _mask_sql(sql: str) -> str | None:
    """주석은 공백으로, 작은따옴표 리터럴은 따옴표만 남기고 내용을 공백으로 바꾼 같은 길이 본문.

    큰따옴표 식별자는 그대로 둔다. 닫히지 않은 리터럴·식별자·블록 주석이면 None(구조를 믿을 수
    없다 — 호출부는 파싱 불가로 다룬다).
    """
    out = list(sql)
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while True:
                if j >= n:
                    return None
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out[i + 1:j] = " " * (j - i - 1)
            i = j + 1
        elif ch == '"':
            j = sql.find('"', i + 1)
            if j < 0:
                return None
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                return None
            out[i:j + 2] = " " * (j + 2 - i)
            i = j + 2
        else:
            i += 1
    return "".join(out)


def _now_functions_in(masked: str) -> tuple[str, ...]:
    found: list[str] = []
    for m in _NOW_FUNCTION_RE.finditer(masked):
        name = re.sub(r"\s+", " ", m.group(0).upper())
        name = re.sub(r"\s*\(\s*\)", "()", name)
        if name not in found:
            found.append(name)
    return tuple(found)


def find_now_functions(sql: str) -> list[str]:
    """SQL의 DB 현재시각 함수·상대 기간 산술을 찾는다(문자열 리터럴·주석 안은 무시).

    `CURRENT_DATE`·`CURRENT DATE`·`CURRENT_TIMESTAMP`·`CURRENT TIMESTAMP`·`NOW()`·`SYSDATE`·
    `INTERVAL`·DB2 표지 기간(`- 1 DAYS`)을 대문자·공백 정규화 표기로 돌려준다(중복 제거 · 등장 순).
    """
    masked = _mask_sql(sql or "")
    if masked is None:  # 닫히지 않은 리터럴 — 리터럴 제거 없이 주석만 지우고 찾는다
        masked = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql or "", flags=re.S)
    return list(_now_functions_in(masked))


def _tokenize(masked: str) -> list[_Tok]:
    return [
        _Tok(m.lastgroup or "other", m.group(0), m.start(), m.end())
        for m in _TOKEN_RE.finditer(masked)
    ]


def _structure(toks: Sequence[_Tok]) -> _Structure | None:
    """괄호 짝·하위 질의·집합 연산 가지·CASE 짝을 한 번에 잰다. 괄호가 맞지 않으면 None."""
    match: dict[int, int] = {}
    parent: list[int] = []
    subquery: set[int] = set()
    scope: list[tuple[int, int]] = []
    case_match: dict[int, int] = {}
    stack: list[int] = []
    scopes: list[int] = [-1]
    branch: dict[int, int] = {-1: 0}
    case_stack: list[int] = []
    n = len(toks)
    for idx, t in enumerate(toks):
        parent.append(stack[-1] if stack else -1)
        if t.kind == "lp":
            nxt = toks[idx + 1] if idx + 1 < n else None
            stack.append(idx)
            if nxt is not None and nxt.kind == "word" and nxt.upper in ("SELECT", "WITH"):
                subquery.add(idx)
                scopes.append(idx)
                branch[idx] = 0
        elif t.kind == "rp":
            if not stack:
                return None
            lp = stack.pop()
            match[lp], match[idx] = idx, lp
            scope.append((scopes[-1], branch[scopes[-1]]))
            if lp in subquery:
                scopes.pop()
            continue
        elif t.kind == "word":
            if t.upper in _SET_OP_WORDS:
                branch[scopes[-1]] += 1
            elif t.upper == "CASE":
                case_stack.append(idx)
            elif t.upper == "END" and case_stack:
                c = case_stack.pop()
                case_match[c], case_match[idx] = idx, c
        scope.append((scopes[-1], branch[scopes[-1]]))
    if stack:
        return None
    return _Structure(match, parent, frozenset(subquery), scope, case_match)


def _ident(text: str) -> str:
    return text.strip('"').lower()


def _is_function_paren(toks: Sequence[_Tok], st: _Structure, lp: int) -> bool:
    if lp <= 0 or lp in st.subquery:
        return False
    prev = toks[lp - 1]
    return prev.kind == "word" and prev.upper not in _NON_FUNCTION_WORDS


def _chain_start(toks: Sequence[_Tok], idx: int) -> int:
    """`a.b.c`의 마지막 마디 위치 → 첫 마디 위치."""
    while idx >= 2 and toks[idx - 1].kind == "dot" and toks[idx - 2].kind == "word":
        idx -= 2
    return idx


def _skip_operand(toks: Sequence[_Tok], st: _Structure, k: int) -> int | None:
    """k에서 시작하는 피연산자 하나(리터럴·식별자·함수 호출·괄호 묶음)의 다음 위치."""
    n = len(toks)
    if k >= n:
        return None
    t = toks[k]
    if t.kind in ("str", "num"):
        return k + 1
    if t.kind == "lp":
        return st.match[k] + 1
    if t.kind == "word":
        if t.upper == "CASE" and k in st.case_match:
            return st.case_match[k] + 1
        if t.upper in _TYPED_LITERAL_WORDS and k + 1 < n and toks[k + 1].kind == "str":
            return k + 2
        while k + 2 < n and toks[k + 1].kind == "dot" and toks[k + 2].kind == "word":
            k += 2
        if k + 1 < n and toks[k + 1].kind == "lp":
            return st.match[k + 1] + 1
        return k + 1
    return None


def _is_boundary(toks: Sequence[_Tok], k: int) -> bool:
    if k >= len(toks):
        return True
    t = toks[k]
    return t.kind in ("rp", "comma", "semi") or (
        t.kind == "word" and t.upper in _RIGHT_BOUNDARY_WORDS
    )


def _str_value(sql: str, t: _Tok) -> str:
    return sql[t.start + 1:t.end - 1].replace("''", "'")


def _read_literal(
    sql: str, toks: Sequence[_Tok], st: _Structure, j: int
) -> tuple[str, int] | None:
    """j에서 시작하는 리터럴 하나 → (값, 다음 위치). 리터럴이 아니거나 뒤에 산술이 붙으면 None.

    인정 형태: `'…'` · 숫자 · `TIMESTAMP '…'`(`DATE`·`TIME`) · `TIMESTAMP('…')` ·
    `CAST('…' AS 형)` · PG 형 변환 접미 `'…'::형`.
    """
    n = len(toks)
    if j >= n:
        return None
    t = toks[j]
    value: str
    if t.kind == "str":
        value, k = _str_value(sql, t), j + 1
    elif t.kind == "num":
        value, k = t.text, j + 1
    elif t.kind == "word" and t.upper in _TYPED_LITERAL_WORDS and j + 1 < n:
        if toks[j + 1].kind == "str":
            value, k = _str_value(sql, toks[j + 1]), j + 2
        elif (
            toks[j + 1].kind == "lp" and j + 3 < n
            and toks[j + 2].kind == "str" and toks[j + 3].kind == "rp"
        ):
            value, k = _str_value(sql, toks[j + 2]), j + 4
        else:
            return None
    elif (
        t.kind == "word" and t.upper == "CAST" and j + 3 < n and toks[j + 1].kind == "lp"
        and toks[j + 2].kind == "str" and toks[j + 3].upper == "AS"
    ):
        rp = st.match[j + 1]
        if any(x.kind not in ("word", "num", "lp", "rp", "comma") for x in toks[j + 4:rp]):
            return None
        value, k = _str_value(sql, toks[j + 2]), rp + 1
    else:
        return None
    if k < n and toks[k].kind == "cast":
        k += 1
        if k < n and toks[k].kind == "word":
            k += 1
            if k < n and toks[k].kind == "lp":
                k = st.match[k] + 1
    if k < n and toks[k].kind == "op" and toks[k].text in _ARITH_OPS:
        return None
    return value, k


def _is_plain_column(toks: Sequence[_Tok], a: int, b: int) -> bool:
    """[a, b) 토큰이 `x` 또는 `a.b.x` 형태의 식별자 하나인가."""
    if b <= a or (b - a) % 2 == 0:
        return False
    return all(
        toks[k].kind == ("word" if (k - a) % 2 == 0 else "dot") for k in range(a, b)
    )


def _scan(
    toks: Sequence[_Tok], st: _Structure, k: int, step: int, stops: frozenset[str],
    *, skip_and: bool = False,
) -> int:
    """k부터 step(+1 오른쪽 · -1 왼쪽) 방향으로 같은 깊이를 걷는다 — 괄호 묶음·CASE 블록은 건너뛴다.

    괄호 끝·쉼표·`stops` 낱말에서 멈춘다. 오른쪽이면 멈춘 위치(배타 끝), 왼쪽이면 그 다음 위치
    (시작)를 돌려준다. `skip_and`면 처음 만나는 AND 하나는 지나간다(BETWEEN a AND b).
    """
    n = len(toks)
    open_kind, close_kind = ("lp", "rp") if step > 0 else ("rp", "lp")
    block_word = "CASE" if step > 0 else "END"
    while 0 <= k < n:
        t = toks[k]
        if t.kind == open_kind:
            k = st.match[k] + step
            continue
        if t.kind in (close_kind, "comma", "semi"):
            break
        if t.kind == "word":
            if t.upper == block_word and k in st.case_match:
                k = st.case_match[k] + step
                continue
            if skip_and and t.upper == "AND":
                skip_and = False
            elif t.upper in stops:
                break
        k += step
    return k if step > 0 else k + 1


def _clause_of(toks: Sequence[_Tok], st: _Structure, idx: int) -> str | None:
    """조건이 걸린 절 — "filter"(WHERE·ON·HAVING) · "case"(CASE WHEN) · None(그 밖)."""
    k = idx - 1
    while k >= 0:
        t = toks[k]
        if t.kind == "rp":
            k = st.match[k] - 1
            continue
        if t.kind == "lp":
            if k in st.subquery or _is_function_paren(toks, st, k):
                return None
            k -= 1  # 묶음 괄호 — 바깥 절을 계속 찾는다
            continue
        if t.kind in ("comma", "semi"):
            return None
        if t.kind == "word":
            w = t.upper
            if w == "END" and k in st.case_match:
                k = st.case_match[k] - 1
                continue
            if w in _FILTER_CLAUSE_WORDS:
                return "filter"
            if w == "WHEN":
                return "case"
            if w in _NON_FILTER_CLAUSE_WORDS:
                return None
        k -= 1
    return None


def _or_joined(toks: Sequence[_Tok], st: _Structure, a: int, b: int) -> bool:
    """조건 [a, b)가 같은 절(또는 감싼 묶음 괄호) 안에서 OR로 다른 조건과 묶였는가."""
    while True:
        lo = _scan(toks, st, a - 1, -1, _GROUP_STOP_WORDS)
        hi = _scan(toks, st, b, +1, _GROUP_STOP_WORDS)
        if (lo > 0 and toks[lo - 1].upper == "OR") or (hi < len(toks) and toks[hi].upper == "OR"):
            return True
        # 감싼 괄호가 묶음 괄호면 그 괄호 전체를 조건 하나로 보고 바깥 절에서 다시 본다
        lp = st.parent[a]
        if lp < 0 or lp in st.subquery or _is_function_paren(toks, st, lp):
            return False
        a, b = lp, st.match[lp] + 1


def _read_condition(
    sql: str, masked: str, toks: Sequence[_Tok], st: _Structure, qs: int, qe: int,
    qualifier: str,
) -> ColumnCondition | None:
    """컬럼 참조 [qs, qe) 하나 → 거르는 조건(조건이 아니거나 CASE WHEN 안이면 None)."""
    n = len(toks)
    start, end, wrapped = qs, qe, False
    # 함수로 감싼 컬럼(`TO_CHAR(x.col, …)`) — 감싼 호출 전체를 피연산자로 본다
    while True:
        lp = st.parent[start]
        if lp >= 0 and _is_function_paren(toks, st, lp):
            start, end, wrapped = _chain_start(toks, lp - 1), st.match[lp] + 1, True
            continue
        break
    # 산술·형 변환이 붙은 컬럼(`x.col || '01'` · `x.col::int`) — 식 전체를 피연산자로 본다
    while end < n:
        t = toks[end]
        if t.kind == "op" and t.text in _ARITH_OPS:
            nxt = _skip_operand(toks, st, end + 1)
            if nxt is None:
                break
            end, wrapped = nxt, True
        elif t.kind == "cast":
            end, wrapped = end + 1, True
            if end < n and toks[end].kind == "word":
                end += 1
                if end < n and toks[end].kind == "lp":
                    end = st.match[end] + 1
        else:
            break

    op: str | None = None
    values: tuple[str, ...] = ()
    literal = column_ref = False
    pstart, pend = start, end
    nxt_tok = toks[end] if end < n else None
    nxt_word = nxt_tok.upper if nxt_tok is not None and nxt_tok.kind == "word" else ""
    if nxt_tok is not None and nxt_tok.kind == "op" and nxt_tok.text in _CMP_OPS:
        op = nxt_tok.text
        lit = None if wrapped else _read_literal(sql, toks, st, end + 1)
        if lit is not None and _is_boundary(toks, lit[1]):
            values, literal, pend = (lit[0],), True, lit[1]
        else:
            rhs_end = _scan(toks, st, end + 1, +1, _RIGHT_BOUNDARY_WORDS)
            if not wrapped and _is_plain_column(toks, end + 1, rhs_end):
                column_ref = True  # 조인 키·CTE 컬럼(`s.col = p.m`) — 리터럴이 아니다
            pend = rhs_end
    elif nxt_word == "BETWEEN":
        op = "between"
        lo = None if wrapped else _read_literal(sql, toks, st, end + 1)
        hi = None
        if lo is not None and lo[1] < n and toks[lo[1]].upper == "AND":
            hi = _read_literal(sql, toks, st, lo[1] + 1)
        if lo is not None and hi is not None and _is_boundary(toks, hi[1]):
            values, literal, pend = (lo[0], hi[0]), True, hi[1]
        else:
            pend = _scan(toks, st, end + 1, +1, _RIGHT_BOUNDARY_WORDS, skip_and=True)
    elif nxt_word == "IN" and end + 1 < n and toks[end + 1].kind == "lp":
        op = "in"
        lp = end + 1
        rp = st.match[lp]
        pend = rp + 1
        items: list[str] = []
        k = lp + 1
        ok = not wrapped and lp not in st.subquery and k < rp
        while ok and k < rp:
            lit = _read_literal(sql, toks, st, k)
            if lit is None or (lit[1] != rp and toks[lit[1]].kind != "comma"):
                ok = False
                break
            items.append(lit[0])
            k = lit[1] + 1
        if ok and _is_boundary(toks, pend):
            values, literal = tuple(items), True
    elif nxt_word in ("NOT", "LIKE", "ILIKE"):
        if nxt_word == "NOT":
            if end + 1 >= n or toks[end + 1].upper not in ("BETWEEN", "IN", "LIKE", "ILIKE"):
                return None
            op = "not " + toks[end + 1].text.lower()
            pend = _scan(
                toks, st, end + 2, +1, _RIGHT_BOUNDARY_WORDS, skip_and=op == "not between"
            )
        else:
            op = nxt_word.lower()
            pend = _scan(toks, st, end + 1, +1, _RIGHT_BOUNDARY_WORDS)
    elif nxt_word == "IS" or start == 0:
        return None
    elif toks[start - 1].kind == "op" and toks[start - 1].text in _CMP_OPS:
        # 뒤집힌 비교(`'…' <= x.col`)
        raw_op = toks[start - 1].text
        op = _FLIP_OP[raw_op]
        lhs_start = _scan(toks, st, start - 2, -1, _LEFT_BOUNDARY_WORDS)
        lit = None if wrapped else _read_literal(sql, toks, st, lhs_start)
        if lit is not None and lit[1] == start - 1:
            values, literal = (lit[0],), True
        elif not wrapped and _is_plain_column(toks, lhs_start, start - 1):
            column_ref = True  # 조인 키·CTE 컬럼
        pstart = lhs_start
    else:
        return None

    if _clause_of(toks, st, pstart) != "filter":
        return None
    reason = "" if literal else (REASON_COLUMN if column_ref else REASON_EXPRESSION)
    if _or_joined(toks, st, pstart, pend):
        literal, values, reason = False, (), REASON_OR
    assert op is not None  # 위 분기가 전부 정하거나 None을 돌려준다
    a, b = toks[pstart].start, toks[pend - 1].end
    return ColumnCondition(
        qualifier=qualifier,
        op=op,
        values=values,
        literal=literal,
        scope=st.scope[pstart],
        text=re.sub(r"\s+", " ", sql[a:b]).strip(),
        now_functions=_now_functions_in(masked[a:b]),
        reason=reason,
    )


def extract_column_conditions(sql: str, column: str) -> list[ColumnCondition] | None:
    """SQL에서 지정 컬럼(별칭 접두 허용 — `x.col`)에 걸린 거르는 조건을 뽑는다.

    `=` · `BETWEEN a AND b` · `>=`·`>`·`<`·`<=`(뒤집힌 비교 포함) · `IN (…)`의 리터럴 비교를 읽는다.
    WHERE·ON·HAVING 절의 조건만 본다 — CASE WHEN 안의 비교(월 피벗 칼럼 등)·SELECT 목록·
    `IS NULL`은 조건이 아니다. 리터럴로 읽지 못한 조건은 `literal=False`와 사유(`reason`)로
    남긴다 — OR 결합 · 상대가 컬럼 참조(조인 키·CTE 컬럼) · 그 밖의 식·서브쿼리·함수로 감싼 컬럼.

    Args:
        sql: 검사할 SQL
        column: 컬럼 이름(대소문자 무시 · 접두 없이)

    Returns:
        조건 목록(등장 순). 괄호·리터럴이 닫히지 않아 구조를 읽을 수 없으면 None
    """
    masked = _mask_sql(sql or "")
    if masked is None:
        return None
    toks = _tokenize(masked)
    st = _structure(toks)
    if st is None:
        return None
    target = column.lower()
    conds: list[ColumnCondition] = []
    for i, t in enumerate(toks):
        if t.kind != "word" or _ident(t.text) != target:
            continue
        if i + 1 < len(toks) and toks[i + 1].kind in ("dot", "lp"):
            continue  # 접두 마디·함수 이름 자리
        if i > 0 and toks[i - 1].kind == "word" and toks[i - 1].upper == "AS":
            continue  # 별칭 선언
        qs = _chain_start(toks, i)
        qualifier = _ident(toks[i - 2].text) if qs < i else ""
        cond = _read_condition(sql, masked, toks, st, qs, i + 1, qualifier)
        if cond is not None:
            conds.append(cond)
    return conds


def table_qualifiers(sql: str, tables: Sequence[str]) -> dict[str, set[str]] | None:
    """SQL이 FROM·JOIN·쉼표 조인으로 참조하는 대상 테이블 → 그 테이블을 가리키는 접두 집합.

    접두 집합은 테이블 이름(마지막 마디 · 소문자)과 별칭(소문자)이다. 스키마 접두(`S.T`)·대소문자는
    무시한다. 참조하지 않는 테이블은 키가 없다. 구조를 읽을 수 없으면 None.
    """
    masked = _mask_sql(sql or "")
    if masked is None:
        return None
    toks = _tokenize(masked)
    wanted = {t.lower() for t in tables}
    out: dict[str, set[str]] = {}
    n = len(toks)
    for i, t in enumerate(toks):
        if t.kind != "word" or _ident(t.text) not in wanted:
            continue
        if i + 1 < n and toks[i + 1].kind == "dot":
            continue  # 컬럼 접두로 쓰인 자리
        qs = _chain_start(toks, i)
        lead = toks[qs - 1] if qs > 0 else None
        if lead is None or not (
            lead.kind == "comma" or (lead.kind == "word" and lead.upper in _TABLE_LEAD_WORDS)
        ):
            continue
        name = _ident(t.text)
        quals = out.setdefault(name, {name})
        k = i + 1
        if k < n and toks[k].kind == "word" and toks[k].upper == "AS":
            k += 1
        if k < n and toks[k].kind == "word" and toks[k].upper not in _ALIAS_STOP_WORDS:
            quals.add(_ident(toks[k].text))
    return out


_K = TypeVar("_K", str, datetime)


def half_open_bounds(
    conds: Sequence[ColumnCondition],
    *,
    key: Callable[[str], _K],
    next_cell: Callable[[_K], _K],
) -> tuple[_K | None, _K | None] | None:
    """조건들(같은 SELECT 블록 · 같은 컬럼)의 교집합을 반개구간 `[lo, hi)`로 정규화한다.

    `= v` → `[v, 다음(v))` · `BETWEEN a AND b` → `[a, 다음(b))` · `>= a` → `[a, ∞)` ·
    `> a` → `[다음(a), ∞)` · `< b` → `(-∞, b)` · `<= b` → `(-∞, 다음(b))` · `IN (…)` → 연속 칸이면
    `[최소, 다음(최대))`. 끝이 없는 쪽은 None이다.

    Args:
        conds: 조건 목록(전부 리터럴이어야 한다)
        key: 리터럴 값 → 비교 키(형식이 어긋나면 ValueError)
        next_cell: 입도 한 칸 다음 키(연속 시각이면 항등 함수를 넘긴다 — 형식은 어댑터 몫)

    Returns:
        `(lo, hi)`(빈 구간이면 lo >= hi). 리터럴이 아닌 조건 · 지원하지 않는 연산자 · 키 변환
        실패 · 비연속 IN이면 None
    """
    lo: _K | None = None
    hi: _K | None = None
    for c in conds:
        if not c.literal or not c.values:
            return None
        try:
            vals = [key(v) for v in c.values]
        except ValueError:
            return None
        a: _K | None
        b: _K | None
        if c.op == "=":
            a, b = vals[0], next_cell(vals[0])
        elif c.op == "between":
            a, b = vals[0], next_cell(vals[1])
        elif c.op == "in":
            ordered = sorted(set(vals))
            if any(next_cell(x) != y for x, y in zip(ordered, ordered[1:])):
                return None
            a, b = ordered[0], next_cell(ordered[-1])
        elif c.op == ">=":
            a, b = vals[0], None
        elif c.op == ">":
            a, b = next_cell(vals[0]), None
        elif c.op == "<":
            a, b = None, vals[0]
        elif c.op == "<=":
            a, b = None, next_cell(vals[0])
        else:
            return None
        if a is not None:
            lo = a if lo is None else max(lo, a)
        if b is not None:
            hi = b if hi is None else min(hi, b)
    return lo, hi
