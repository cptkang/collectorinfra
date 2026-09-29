"""실행 SQL에서 읽어 내는 고지 판정 — 생성기 자기 고백(S-8)과 조건 반영 대조(S-11 1차).

**S-8 `generator_confessions`.** SQL 생성기는 조건을 버릴 때 주석으로 스스로 적곤 한다
(「…스키마에 없으므로 무시」). 그 주석을 **원문 그대로 인용**해 고지 kind `GENERATOR_NOTE`의
근거로 쓴다 — 요약·재서술하지 않는다(plans/123 S-8).

**S-11 1차 `condition_changes`.** 파서가 뽑은 수치 비교 조건이 최종 실행 SQL에 같은 뜻으로
들어갔는지 결정적으로 대조한다(조회 0 · LLM 0). 판정은 누락 · 부등호 반전 · AND→OR ·
항상 참 조건 4종이며 kind `CONDITION_CHANGED`의 근거가 된다. 단일 task 계획에서만 호출된다
(복합 계획은 전역 조건이 모든 task에 들어가 오탐한다 — plans/123 S-11).

판정은 **보수적**이다 — 정상 조건을 「바뀌었다」고 말하는 것(오탐)보다 놓치는 것(미탐)이 낫다.
연산자를 못 찾거나(`BETWEEN`·함수 인자) 등호가 섞이면 판정하지 않는다.

계층: domain — 순수 · I/O·LLM·전역 상태 0.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# 판정 결과를 싣는 고지 kind(정본 disclosure.py) — S-8 → GENERATOR_NOTE · S-11 → CONDITION_CHANGED.
from src.domain.disclosure import CONDITION_CHANGED, GENERATOR_NOTE

__all__ = [
    "CONDITION_CHANGED",
    "CONFESSION_TERMS",
    "GENERATOR_NOTE",
    "ConditionChange",
    "condition_changes",
    "extract_sql_comments",
    "generator_confessions",
    "strip_sql_comments",
]

# ── 주석 처리 ──────────────────────────────────────────────────────────────

#: 문자열 리터럴 · 큰따옴표 식별자 · 주석을 왼쪽부터 한 번에 자른다. 먼저 시작한 것이 이기므로
#: 문자열·식별자 안의 `--`·`/*`는 주석이 되지 않는다. 닫히지 않은 것은 끝까지 먹는다.
_LEXEME_RE = re.compile(
    r"'(?:[^']|'')*'?"
    r'|"(?:[^"]|"")*"?'
    r"|--[^\n]*"
    r"|/\*.*?(?:\*/|\Z)",
    re.DOTALL,
)


def _scan(sql: str | None, *, blank_strings: bool) -> tuple[str, list[str]]:
    """주석을 공백 하나로 바꾼 SQL과 주석 본문 목록을 함께 돌려준다.

    `blank_strings`면 작은따옴표 문자열 리터럴 내용도 비운다(`''`) — 조건 대조용.
    """
    comments: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        tok = m.group(0)
        if tok.startswith("--"):
            comments.append(tok[2:].strip())
            return " "
        if tok.startswith("/*"):
            closed = len(tok) >= 4 and tok.endswith("*/")
            comments.append((tok[2:-2] if closed else tok[2:]).strip())
            return " "
        if blank_strings and tok.startswith("'"):
            return "''"
        return tok

    text = _LEXEME_RE.sub(_replace, sql or "")
    return text, [c for c in comments if c]


def strip_sql_comments(sql: str | None) -> str:
    """SQL 주석(`--` 한 줄 · `/* */` 여러 줄)을 공백 하나로 바꿔 돌려준다. None이면 빈 문자열."""
    return _scan(sql, blank_strings=False)[0]


def extract_sql_comments(sql: str | None) -> list[str]:
    """SQL 주석 본문을 등장 순서로 돌려준다 — 표지(`--`·`/* */`)와 앞뒤 공백 제거 · 빈 것 제외."""
    return _scan(sql, blank_strings=False)[1]


# ── S-8 생성기 자기 고백 ──────────────────────────────────────────────────────

#: S-8 자기 고백 표지(plans/123 S-8 원문 목록)
CONFESSION_TERMS: tuple[str, ...] = (
    "무시", "생략", "적용하지 않", "상충", "스키마에 없", "대신", "초과이거나",
)


def generator_confessions(
    sqls: Iterable[str | None], *, max_items: int = 3, max_len: int = 200
) -> list[str]:
    """실행 SQL 주석 중 자기 고백 표지를 담은 것을 원문 그대로 인용한다(S-8).

    공백만 한 칸으로 정규화하고, `max_len`을 넘으면 끝을 「…」로 바꿔 전체 길이를 `max_len`에
    맞춘다. 정규화한 문자열 기준으로 중복을 지우고 등장 순서로 최대 `max_items`건.
    """
    out: list[str] = []
    seen: set[str] = set()
    for sql in sqls:
        for body in extract_sql_comments(sql):
            if len(out) >= max_items:
                return out
            text = " ".join(body.split())
            if text in seen or not any(term in text for term in CONFESSION_TERMS):
                continue
            seen.add(text)
            out.append(text if len(text) <= max_len else text[: max(max_len - 1, 0)] + "…")
    return out


# ── S-11 1차 조건 반영 대조 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConditionChange:
    """조건 반영 대조 결과 한 건 — 호출부가 `detail`을 사용자 고지 한 줄로 쓴다."""

    kind: str  # "missing" | "reversed" | "and_to_or" | "neutralized"
    field: str  # 파서 조건 field (neutralized는 "")
    condition: str  # 사람이 읽는 원 조건 표기 (예: ">= 80") · neutralized는 매치 스팬
    detail: str  # 사용자에게 보일 한 줄 설명


#: 부등호 방향 — 큰 쪽(+1)·작은 쪽(-1). 등호·부등(`=`·`<>`·`!=`)은 방향이 없다.
_DIRECTION: dict[str, int] = {">": 1, ">=": 1, "<": -1, "<=": -1}
#: 값이 왼쪽에 올 때(`80 <= col`) 열 기준으로 뒤집은 연산자.
_FLIP: dict[str, str] = {">": "<", ">=": "<=", "<": ">", "<=": ">="}
_OP_BEFORE_RE = re.compile(r"(<>|!=|>=|<=|>|<|=)\s*$")
_OP_AFTER_RE = re.compile(r"\s*(<>|!=|>=|<=|>|<|=)")
_NUMERIC_STR_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*%?\s*")
#: 원문의 선언형 「또는」 표지 — 있으면 OR 결합이 요청 그대로일 수 있어 판정하지 않는다.
_RAW_OR_RE = re.compile(r"또는|이거나|거나|혹은|아니면|(?<![A-Za-z])or(?![A-Za-z])", re.IGNORECASE)
#: `NOT (x < 80)`은 뜻이 `x >= 80`이다 — 부정 괄호가 있으면 부등호 반전을 판정하지 않는다.
_NOT_GROUP_RE = re.compile(r"\bNOT\s*\(", re.IGNORECASE)
_OR_RE = re.compile(r"\bOR\b", re.IGNORECASE)
_AND_RE = re.compile(r"\bAND\b", re.IGNORECASE)
#: 두 값 사이에 절 경계가 있으면 같은 WHERE 결합이 아니다 — 판정하지 않는다.
_CLAUSE_BREAK_RE = re.compile(r"\b(?:SELECT|FROM|WHERE|HAVING|UNION|GROUP|ORDER)\b", re.IGNORECASE)
#: 항상 참 조건. 작은따옴표 꼴은 문자열을 비우기 전 SQL에서 본다.
_ALWAYS_TRUE_RE = re.compile(r"\bOR\s+(?:1\s*=\s*1(?![\w.])|TRUE\b)", re.IGNORECASE)
_ALWAYS_TRUE_QUOTED_RE = re.compile(r"\bOR\s+'1'\s*=\s*'1'", re.IGNORECASE)


@dataclass(frozen=True)
class _Target:
    """대조 대상 수치 비교 조건 하나."""

    field: str
    op: str
    shown: str  # "<field> <op> <value>" (field가 없으면 "<op> <value>")
    condition: str  # "<op> <value>"
    token_re: re.Pattern[str]


def _to_decimal(value: Any) -> Decimal | None:
    """조건 값을 음이 아닌 유한 수로 읽는다 — int/float(bool 제외) · 숫자 문자열("90"·"90%")."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, str):
        m = _NUMERIC_STR_RE.fullmatch(value)
        if not m:
            return None
        text = m.group(1)
    else:
        return None
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    return d if d.is_finite() and d >= 0 else None


def _number_form(d: Decimal) -> str:
    """수 d가 SQL에 적힐 수 있는 표기의 정규식 — 정수는 `.0…` 허용 · 소수는 끝 0 허용."""
    if d == d.to_integral_value():
        return rf"{int(d)}(?:\.0+)?"
    int_part, frac_part = format(d.normalize(), "f").split(".")
    lead = "0?" if int_part == "0" else int_part
    return rf"{lead}\.{frac_part}0*"


def _token_re(d: Decimal) -> re.Pattern[str]:
    """값 토큰 정규식 — 표기 그대로 · 비율 변환(v/100, 0<v<1이면 v×100)을 단어 경계로 인정한다."""
    forms = [_number_form(d)]
    if 0 < d < 100:
        forms.append(_number_form(d / 100))
    if 0 < d < 1:
        forms.append(_number_form(d * 100))
    return re.compile(rf"(?<![\w.])(?:{'|'.join(forms)})(?![\w.])")


def _targets(filter_conditions: list[dict[str, Any]] | None) -> list[_Target]:
    """파서 조건 중 수치 부등호 조건만 고른다 — 그 밖(=·!=·LIKE·IN·비수치)은 보지 않는다."""
    out: list[_Target] = []
    for cond in filter_conditions or []:
        if not isinstance(cond, dict):
            continue
        op = str(cond.get("op") or "").strip()
        value = cond.get("value")
        d = _to_decimal(value)
        if op not in _DIRECTION or d is None:
            continue
        field = str(cond.get("field") or "").strip()
        condition = f"{op} {str(value).strip()}"
        shown = f"{field} {condition}" if field else condition
        out.append(_Target(field, op, shown, condition, _token_re(d)))
    return out


def _ops_around(text: str, m: re.Match[str]) -> list[str]:
    """값 토큰에 붙은 비교 연산자를 열 기준으로 돌려준다 — 뒤에 붙은 것(`80 <= col`)은 뒤집는다."""
    ops: list[str] = []
    before = _OP_BEFORE_RE.search(text[max(0, m.start() - 32) : m.start()])
    if before:
        ops.append(before.group(1))
    after = _OP_AFTER_RE.match(text, m.end())
    if after:
        ops.append(_FLIP.get(after.group(1), after.group(1)))
    return ops


def _check_target(target: _Target, text: str, uncommented: str) -> ConditionChange | None:
    """조건 하나의 누락·부등호 반전을 판정한다. 연산자를 못 찾으면 판정하지 않는다.

    누락은 **문자열 리터럴 안까지** 본 뒤에만 말한다 — EAV 값처럼 `>= '80'`으로 비교하는 SQL을
    누락으로 오탐하지 않게 한다(보수적 판정). 부등호 반전은 문자열을 비운 텍스트로 본다.
    """
    if not target.token_re.search(uncommented):
        return ConditionChange(
            "missing", target.field, target.condition,
            f"실행한 SQL에서 요청 조건 '{target.shown}'의 값이 확인되지 않습니다.",
        )
    matches = list(target.token_re.finditer(text))
    if not matches or _NOT_GROUP_RE.search(text):
        return None
    ops = [op for m in matches for op in _ops_around(text, m)]
    want = _DIRECTION[target.op]
    if ops and all(_DIRECTION.get(op, 0) == -want for op in ops):
        return ConditionChange(
            "reversed", target.field, target.condition,
            f"요청 조건 '{target.shown}'의 부등호가 실행한 SQL에서 반대 방향('{ops[0]}')으로 "
            "적용되었습니다.",
        )
    return None


def _joined_by_or(first: _Target, second: _Target, text: str) -> bool:
    """두 조건의 가장 가까운 값 토큰 사이가 OR로만 이어졌는가(AND·절 경계가 있으면 아니다)."""
    best: tuple[int, int] | None = None
    for a in first.token_re.finditer(text):
        for b in second.token_re.finditer(text):
            lo, hi = (a.end(), b.start()) if a.end() <= b.start() else (b.end(), a.start())
            if lo > hi:  # 겹치는 토큰
                continue
            if best is None or hi - lo < best[1] - best[0]:
                best = (lo, hi)
    if best is None:
        return False
    segment = text[best[0] : best[1]]
    return (
        bool(_OR_RE.search(segment))
        and not _AND_RE.search(segment)
        and not _CLAUSE_BREAK_RE.search(segment)
    )


def _and_to_or(targets: list[_Target], text: str, raw_query: str | None) -> ConditionChange | None:
    """원문이 「또는」을 말하지 않았는데 두 조건이 SQL에서 OR로 결합됐으면 1건.

    원문이 없으면(None) 「또는」 여부를 알 수 없으므로 판정하지 않는다.
    """
    if len(targets) < 2 or raw_query is None or _RAW_OR_RE.search(raw_query):
        return None
    for i, first in enumerate(targets):
        for second in targets[i + 1 :]:
            if _joined_by_or(first, second, text):
                return ConditionChange(
                    "and_to_or", first.field, f"{first.condition} / {second.condition}",
                    f"요청하신 두 조건('{first.shown}', '{second.shown}')이 실행한 SQL에서 "
                    "'또는(OR)'로 결합되었습니다.",
                )
    return None


def _neutralized(uncommented: str, text: str) -> ConditionChange | None:
    """항상 참 조건(`OR 1=1` 꼴)이 있으면 1건 — 필터가 무력화됐을 수 있다."""
    m = _ALWAYS_TRUE_RE.search(text) or _ALWAYS_TRUE_QUOTED_RE.search(uncommented)
    if not m:
        return None
    span = " ".join(m.group(0).split())
    return ConditionChange(
        "neutralized", "", span,
        f"실행한 SQL에 항상 참인 조건('{span}')이 있어 필터가 적용되지 않았을 수 있습니다.",
    )


def condition_changes(
    filter_conditions: list[dict[str, Any]] | None, sql: str | None, raw_query: str | None
) -> list[ConditionChange]:
    """파서 수치 조건이 실행 SQL에 같은 뜻으로 들어갔는지 대조한다(S-11 1차 · 결정적 · 조회 0).

    SQL은 주석을 지우고 작은따옴표 문자열 내용을 비운 뒤 본다 — 주석·문자열 안의 숫자는 증거가
    아니다. 결과는 발견 순서이며 (kind, field, condition) 기준으로 중복을 지운다.
    """
    if not sql or not sql.strip():
        return []
    uncommented = strip_sql_comments(sql)
    text = _scan(sql, blank_strings=True)[0]
    targets = _targets(filter_conditions)

    found: list[ConditionChange | None] = [_check_target(t, text, uncommented) for t in targets]
    found.append(_and_to_or(targets, text, raw_query))
    found.append(_neutralized(uncommented, text))

    out: list[ConditionChange] = []
    seen: set[tuple[str, str, str]] = set()
    for change in found:
        if change is None:
            continue
        key = (change.kind, change.field, change.condition)
        if key not in seen:
            seen.add(key)
            out.append(change)
    return out
