"""오라클 대조 어댑터 · SQL 분석 · 실패 분류 (plans/135 §3.3 · W4).

판정은 전부 **결정적**이다(LLM 0). SQL 은 파서 없이 주석·리터럴을 지운 뒤 본다. 방언 함정은 샌드박스
리허설(`testdata/itam/README.md`)로 **실측 확인된 4종만** 잡는다 — 추정 패턴으로 오탐을 만들지
않는다.

이 모듈은 메모리의 원값(실행 SQL · 결과 행 · 응답 문장)을 읽어 판정하지만 **아무것도 기록하지
않는다** — 기록은 `redact` 를 거친 뒤 드라이버가 한다.
"""

from __future__ import annotations

import copy
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .substitute import FakeValues

logger = logging.getLogger(__name__)

#: 실패 분류 → 고칠 곳(plans/135 §3.3 표). 순서가 리포트의 「첫 원인」 우선순위다.
TAXONOMY: dict[str, str] = {
    "permission_denied": "벤치 환경 문제(계정 인가 D-232) — 판정 제외",
    "routing_miss": "ITAM 프롬프트 밖 — 소스 선별(plans/132) · 분리 집계",
    "asked_back": "프롬프트 모호성 · 되묻기 규칙(plans/132) · 분리 집계",
    "backend_limit": "plans/139 W1·W2 LLM 입력 한도 — 조회 대상 크기(`schema_context.dbs`) · "
    "테이블 정의 선별(W4)",
    "selection_none": "plans/139 W4 선별 0개 — 「DB 구조」 탭 테이블 정의(manages·key_columns)",
    "no_sql": "plans/133 A8 · 프로필 query_guide",
    "fabricated": "plans/133 A8 · 고지 규칙",
    "dialect_error": "plans/133 A8 DB 전용 프롬프트 섹션 · 프로필 방언 규칙",
    "dialect_silent": "plans/133 A8 · 반복되면 결정적 교정 후보(plans/95 §4.2)",
    "schema_miss": "plans/133 A2 조회 대상 · A1 관계",
    "meaning_absent": "plans/133 A3 컬럼 설명",
    "column_misread": "plans/133 A3 컬럼 설명·유사어",
    "date_text": "plans/133 A6 쿼리 규칙",
    "join_key_partial": "plans/133 A1 · A6",
    "code_value": "plans/133 A4",
    "key_mismatch": "수동 검토",
}
#: 소스 선별·되묻기는 ITAM 프롬프트 수치와 분리 집계한다(§3.3).
SEPARATE = frozenset({"routing_miss", "asked_back", "permission_denied"})

_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.S)
_SINGLE_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*'")
_DOUBLE_LITERAL = re.compile(r"\"(?:[^\"\\]|\\.|\"\")*\"")
_SYNTAX_ERROR = re.compile(r"(?i)\b1064\b|syntax")
_DATE_FUNCS = re.compile(
    r"(?i)\b(curdate|current_date|now|sysdate|date_add|date_sub|adddate|subdate)\s*\(|"
    r"\bcurrent_date\b|\binterval\b|\bdate\s*'|\bdate\s*\("
)
_DATE_SAFE = re.compile(r"(?i)\b(date_format|str_to_date)\s*\(")
#: 술어 조각 경계 — 쉼표로는 자르지 않는다(함수 인자 `DATEDIFF(col, NOW())`가 갈라진다).
_PREDICATE_SPLIT = re.compile(
    r"(?i)\b(?:and|or|where|on|having|when|then|else|select|from|group|order)\b|;"
)
_COMPARISON = re.compile(r"(?i)[<>=]|\bbetween\b|\b(?:datediff|timestampdiff)\s*\(")
_TABULAR_LINE = re.compile(r"^\s*(\|.*\||[-*•]\s+\S|\d+[.)]\s+\S)")
#: 식별자 낱말 — 유니코드(한글 컬럼 `서버호스트명`)·백틱 안 이름까지(plans/149 W4 (3) · F11).
#: ASCII 낱말만 쓰는 영문 샌드박스 SQL 에서는 예전 `[A-Za-z_][A-Za-z0-9_]*`와 같은 토큰이다.
_WORD = re.compile(r"[^\W\d]\w*")
_BACKTICK = re.compile(r"`((?:[^`]|``)+)`")
#: 한정 식별자 한 쪽(`s.k` · `` s.`k` `` · `` `s`.`k` ``) — 백틱 이름은 공백·기호를 품을 수 있다.
_QUALIFIED_PART = r"(?:`(?:[^`]|``)+`|\w+)"
_JOIN_EQUAL = re.compile(
    rf"(?<![\w`]){_QUALIFIED_PART}\.({_QUALIFIED_PART})\s*=\s*"
    rf"{_QUALIFIED_PART}\.({_QUALIFIED_PART})(?!\w)"
)

#: 조인 키 판정(plans/150 W2 (6)) — 등호 양쪽(한정 이름 `별칭.칸` 또는 맨 칸 이름 · 백틱 포함).
#: 맨 이름은 숫자로 시작하지 않는다(`1 = 1`). 앞뒤가 한정·함수 호출의 일부면 잡지 않는다.
_SIDE_NAME = r"(?:`(?:[^`]|``)+`|[^\W\d]\w*)"
_EQUAL_SIDES = re.compile(
    rf"(?<![\w`.])(?:({_SIDE_NAME})\s*\.\s*)?({_SIDE_NAME})\s*=\s*"
    rf"(?:({_SIDE_NAME})\s*\.\s*)?({_SIDE_NAME})(?![\w`.(])"
)
#: `FROM`·`JOIN` 뒤 테이블 항목 하나 — `[스키마.]표 [[AS] 별칭]`(쉼표 목록은 이어서 읽는다).
_FROM_TABLE = re.compile(
    rf"\s*({_SIDE_NAME})(?:\s*\.\s*({_SIDE_NAME}))?(?:\s+(?:(?i:as)\s+)?({_SIDE_NAME}))?"
)
_FROM_KEYWORD = re.compile(r"(?i)\b(?:from|join)\b")
_USING = re.compile(r"(?i)\bUSING\s*\(([^)]*)\)")
#: 칸 참조 하나(`별칭.칸` 또는 맨 칸 이름) · 쪼개 읽기용
_REF = rf"(?:{_SIDE_NAME}\s*\.\s*)?{_SIDE_NAME}"
_REF_PARTS = re.compile(rf"\s*(?:({_SIDE_NAME})\s*\.\s*)?({_SIDE_NAME})\s*")
#: 칸 하나를 감싼 정리·형 변환 함수(`TRIM(a.칸)` · `UPPER(…)` · `CAST(a.칸 AS CHAR(10))` ·
#: `CONVERT(a.칸, CHAR)`) — 조인 등호 판정 전에 칸만 남긴다(교정 M4 · 겹침은 반복해 푼다).
_WRAPPED_REF = re.compile(
    rf"(?i)(?<![\w`.])(?:trim|ltrim|rtrim|upper|lower|ucase|lcase|cast|convert)\s*\(\s*({_REF})\s*"
    r"(?:(?:as|using)\s+\w+(?:\s+\w+)?(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?"
    r"|,\s*\w+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)?\s*\)"
)
#: 세미·안티 조인 `칸 [NOT] IN (SELECT 칸 FROM 표 …)` · `(칸, 칸) [NOT] IN (SELECT 칸, 칸 …)`
#: (교정 M5) — 바깥 칸과 하위 SELECT 칸을 자리 순서대로 잇는다.
_IN_SUBQUERY = re.compile(
    rf"(?i)(?:\(\s*(?P<tuple>{_REF}(?:\s*,\s*{_REF})+)\s*\)|(?<![\w`.])(?P<single>{_REF}))"
    rf"\s+(?:not\s+)?in\s*\(\s*select\s+(?:distinct\s+)?(?P<items>[^()]*?)\s+from\s+"
    rf"(?P<first>{_SIDE_NAME})(?:\s*\.\s*(?P<second>{_SIDE_NAME}))?"
    rf"(?:\s+(?:as\s+)?(?P<alias>{_SIDE_NAME}))?"
)
#: 별칭 자리에 올 수 없는 낱말(다음 절·조인 수식어).
_NOT_ALIAS = frozenset(
    "where join on using left right inner outer cross full natural straight_join group order "
    "having limit union where window for lock into set select from as and or not exists".split()
)
#: 모든 ITAM 테이블에 있는 공통 칸 — 정의 `related`에 없어 조인 키로 요구하지 않고, 이었는지만
#: 기록한다(`join_group_company`).
GROUP_COMPANY_COLUMN = "그룹회사코드"
#: 승인 코드값이 없는 코드·여부 칸에 리터럴 등호를 쓴 턴의 `unknown_code_value` 표시
#: (plans/150 W2 (5))
NO_APPROVED_CODES = "승인 코드값 없음"
#: 코드·여부 칸 이름·설명 끝말
_CODE_SUFFIXES = ("코드", "여부")


def strip_comments(sql: str) -> str:
    return _COMMENTS.sub(" ", sql or "")


def strip_literals(sql: str) -> str:
    """주석·작은따옴표 리터럴을 지운다.

    큰따옴표는 남긴다 — MariaDB 에서 문자열이지만 식별자 오용의 증거다.
    """
    return _SINGLE_LITERAL.sub("''", strip_comments(sql))


def _bare(name: str) -> str:
    return str(name).strip('`"').rsplit(".", 1)[-1]


def _unquote(name: str) -> str:
    """백틱 이름 → 안의 이름(겹 백틱은 하나로)."""
    return name[1:-1].replace("``", "`") if name.startswith("`") else name


def _identifier_tokens(text: str) -> set[str]:
    """텍스트의 식별자 낱말(소문자) — 유니코드 낱말 + 백틱 안 이름 통째."""
    tokens = {token.casefold() for token in _WORD.findall(text)}
    return tokens | {m.group(1).replace("``", "`").casefold() for m in _BACKTICK.finditer(text)}


def sql_tables(sql: str) -> list[str]:
    """사용 테이블(백틱을 먼저 벗긴다 — `_extract_table_names` 식별자 패턴이 `[\\w]+`다)."""
    from src.sql_validation import _extract_table_names

    return sorted({_bare(t) for t in _extract_table_names(strip_literals(sql).replace("`", ""))})


def sql_columns(sql: str, catalog_columns: Iterable[str]) -> list[str]:
    """사용 컬럼 — 카탈로그 컬럼 이름의 단어 경계 일치(대소문자 무시 — MariaDB 컬럼 이름 규칙).

    한글·백틱 식별자도 읽는다(plans/149 W4 (3)).
    """
    body = _DOUBLE_LITERAL.sub(" ", strip_literals(sql))
    present = _identifier_tokens(body)
    return sorted({c for c in catalog_columns if c.casefold() in present})


def dialect_hazards(sql: str, identifiers: Iterable[str]) -> list[str]:
    """실측 확인된 방언 함정 4종(정적 · 실행 성공에도 침묵 오답이 되는 것 포함)."""
    body = strip_literals(sql)
    names = {str(n).casefold() for n in identifiers}
    hazards = []
    if "||" in _DOUBLE_LITERAL.sub(" ", body):
        hazards.append("pipes_concat")
    if any(m.group(0)[1:-1].casefold() in names for m in _DOUBLE_LITERAL.finditer(body)):
        hazards.append("double_quoted_identifier")
    if re.search(r"::\s*[A-Za-z]", body):
        hazards.append("pg_cast")
    if re.search(r"(?i)\binterval\s+'\s*\d+\s+[a-z]+\s*'", strip_comments(sql)):
        hazards.append("interval_string")
    return hazards


def date_text_misuse(sql: str, date_columns: Iterable[str]) -> bool:
    """`CHAR(8)` 날짜 문자열 컬럼을 날짜 함수·DATE 리터럴과 직접 비교했는가(술어 조각 단위).

    같은 조각에 `DATE_FORMAT(`·`STR_TO_DATE(`가 있으면 변환했다고 본다.
    """
    columns = {c.casefold() for c in date_columns}
    if not columns:
        return False
    for segment in _PREDICATE_SPLIT.split(strip_comments(sql)):
        tokens = _identifier_tokens(segment)
        if (
            tokens & columns
            and _COMPARISON.search(segment)
            and _DATE_FUNCS.search(segment)
            and not _DATE_SAFE.search(segment)
        ):
            return True
    return False


def join_key_partial(sql: str, key_groups: Iterable[tuple[frozenset[str], Sequence[str]]]) -> bool:
    """같은 복합 키를 가진 테이블 둘을 함께 쓰면서 조인 조건이 키 전부를 잇지 않았는가.

    `key_groups`: (테이블 이름 집합(소문자), 공통 키 컬럼 목록). 조인 조건은 `a.k = b.k`(같은 이름 ·
    백틱 `` a.`키` `` 포함 · plans/149 W4 (3))·`USING (k, …)` 형태만 센다.
    """
    body = strip_literals(sql)
    used = {t.casefold() for t in sql_tables(sql)}
    joined = {
        _unquote(m.group(2)).casefold()
        for m in _JOIN_EQUAL.finditer(body)
        if _unquote(m.group(1)).casefold() == _unquote(m.group(2)).casefold()
    }
    for using in re.finditer(r"(?i)\bUSING\s*\(([^)]*)\)", body):
        joined |= {part.strip().strip("`").casefold() for part in using.group(1).split(",")}
    for tables, keys in key_groups:
        if len(tables & used) >= 2 and not {k.casefold() for k in keys} <= joined:
            return True
    return False


def _table_items(body: str) -> list[tuple[int, str, str]]:
    """`FROM`·`JOIN` 절의 테이블 항목 ``(위치, 표, 인스턴스)``(나온 순서 · 소문자).

    인스턴스는 별칭이 있으면 별칭, 없으면 표 이름이다 — 같은 표를 두 번 쓰면(`72 a`·`72 b`)
    인스턴스가 둘이다. 하위 질의(`EXISTS (SELECT … FROM 표 별칭 …)`)의 항목도 든다.
    """
    items: list[tuple[int, str, str]] = []
    for keyword in _FROM_KEYWORD.finditer(body):
        position = keyword.end()
        while True:
            match = _FROM_TABLE.match(body, position)
            if match is None:
                break
            first, second, alias = match.group(1), match.group(2), match.group(3)
            table = _unquote(second or first).casefold()
            if table in _NOT_ALIAS:
                break
            name = _unquote(alias).casefold() if alias else None
            end = match.end()
            if name is not None and (alias.startswith("`") or name not in _NOT_ALIAS):
                items.append((match.start(1), table, name))
            else:
                items.append((match.start(1), table, table))
                end = match.end(2) if second else match.end(1)
            comma = re.compile(r"\s*,").match(body, end)
            if comma is None or keyword.group(0).casefold() == "join":
                break
            position = comma.end()
    return items


def _alias_map(items: Iterable[tuple[int, str, str]]) -> dict[str, set[str]]:
    """테이블 항목 → ``{별칭·표 이름(소문자): {표(소문자)…}}``.

    같은 별칭이 다른 테이블을 가리키면 값이 둘 이상이고, 판정은 그 별칭을 「해석 못 함」으로 본다.
    """
    aliases: dict[str, set[str]] = {}
    for _, table, instance in items:
        aliases.setdefault(table, set()).add(table)
        aliases.setdefault(instance, set()).add(table)
    return aliases


def _unwrap_refs(body: str) -> str:
    """칸 하나를 감싼 정리·형 변환 함수를 벗긴다(`TRIM(UPPER(a.칸))` → `a.칸`)."""
    for _ in range(4):
        unwrapped = _WRAPPED_REF.sub(lambda m: m.group(1), body)
        if unwrapped == body:
            break
        body = unwrapped
    return body


def _refs(text: str) -> list[tuple[str | None, str]] | None:
    """쉼표 목록 → ``[(한정자, 칸)…]``(칸 참조가 아닌 항목이 있으면 None)."""
    out: list[tuple[str | None, str]] = []
    for part in text.split(","):
        match = _REF_PARTS.fullmatch(part)
        if match is None:
            return None
        out.append((match.group(1), match.group(2)))
    return out


def join_key_judgement(
    sql: str, pairs: Sequence[tuple[tuple[str, str], tuple[str, str]]]
) -> dict[str, Any]:
    """조인 키 쌍 판정(plans/150 W2 (6) · 관찰 표지 — 판정 불변).

    쌍 ``((표, 칸), (표, 칸))``의 두 테이블을 SQL 이 함께 쓸 때, 그 두 칸을 잇는 조인이 없으면
    `missed`에 싣는다. 조인으로 보는 것:

    - 등호 `별칭.칸 = 별칭.칸`(이름 다른 칸 포함 · `EXISTS`/`NOT EXISTS` 하위 질의 안 포함 ·
      `TRIM`·`UPPER`·`CAST` 같은 칸 하나 감싼 함수는 벗겨 본다)
    - `USING (칸, …)` — 그 `JOIN` 의 테이블과 바로 앞 테이블 항목 사이만
    - `칸 [NOT] IN (SELECT 칸 FROM 표 …)` · `(칸, 칸) IN (SELECT 칸, 칸 …)` — 자리 순서대로

    이어진 칸끼리는 같은 묶음이다(추이 — `a = b`·`b = c`면 `a`와 `c`도 이어진 것). 노드는
    테이블 **인스턴스**(별칭)의 칸이라 같은 표를 두 번 쓰면 따로 본다 — 같은 두 테이블 사이의
    쌍(복합 키)은 한 인스턴스 쌍이 다 이어야 하고, 가장 많이 이은 인스턴스 쌍 기준으로 빠진 쌍을
    싣는다. 해석하지 못한 조인(맨 칸 이름 · 모르는 별칭 · 두 테이블을 가리키는 별칭)은 칸 이름
    쌍으로만 보고 `alias_unresolved`를 단다. `group_company`는 `그룹회사코드`끼리 이었는가
    (요구하지 않음).
    """
    body = _unwrap_refs(strip_literals(sql))
    used = {t.casefold() for t in sql_tables(sql)}
    items = _table_items(body)
    aliases = _alias_map(items)
    instances = {name: next(iter(tables)) for name, tables in aliases.items() if len(tables) == 1}
    parent: dict[tuple[str, str], tuple[str, str]] = {}
    loose: set[frozenset[str]] = set()

    def find(node: tuple[str, str]) -> tuple[str, str]:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def instance_of(qualifier: str | None) -> str | None:
        if qualifier is None:
            return None
        name = _unquote(qualifier).casefold()
        return name if name in instances else None

    def link(left: str | None, left_col: str, right: str | None, right_col: str) -> None:
        left_col, right_col = _unquote(left_col).casefold(), _unquote(right_col).casefold()
        if left is not None and right is not None:
            parent[find((left, left_col))] = find((right, right_col))
        else:
            loose.add(frozenset((left_col, right_col)))

    group_company = False
    folded_group = GROUP_COMPANY_COLUMN.casefold()
    for match in _EQUAL_SIDES.finditer(body):
        if _unquote(match.group(2)).casefold() == _unquote(match.group(4)).casefold() == (
            folded_group
        ):
            group_company = True
        link(instance_of(match.group(1)), match.group(2), instance_of(match.group(3)),
             match.group(4))
    for match in _USING.finditer(body):
        before = [item for item in items if item[0] < match.start()]
        left = instance_of(before[-2][2]) if len(before) >= 2 else None
        right = instance_of(before[-1][2]) if before else None
        for part in match.group(1).split(","):
            column = part.strip()
            if not column:
                continue
            group_company = group_company or _unquote(column).casefold() == folded_group
            link(left, column, right, column)
    for match in _IN_SUBQUERY.finditer(body):
        outer = _refs(match.group("tuple") or match.group("single"))
        inner = _refs(match.group("items"))
        if outer is None or inner is None or len(outer) != len(inner):
            continue
        alias = match.group("alias")
        source = (
            alias
            if alias and (alias.startswith("`") or alias.casefold() not in _NOT_ALIAS)
            else match.group("second") or match.group("first")
        )
        for (outer_q, outer_col), (inner_q, inner_col) in zip(outer, inner, strict=True):
            link(instance_of(outer_q), outer_col, instance_of(inner_q or source), inner_col)

    groups: dict[tuple[str, str], list[tuple[int, str, str]]] = {}
    for index, ((table_a, column_a), (table_b, column_b)) in enumerate(pairs):
        a, b = table_a.casefold(), table_b.casefold()
        ca, cb = column_a.casefold(), column_b.casefold()
        if (a, ca) > (b, cb):
            a, b, ca, cb = b, a, cb, ca
        groups.setdefault((a, b), []).append((index, ca, cb))
    missed_index: list[int] = []
    unresolved = False
    for (a, b), members in groups.items():
        if not {a, b} <= used:
            continue
        best: set[int] = set()
        for left in (name for name, table in instances.items() if table == a):
            for right in (name for name, table in instances.items() if table == b):
                if a == b and left == right:
                    continue
                joined = {i for i, ca, cb in members if find((left, ca)) == find((right, cb))}
                if len(joined) > len(best):
                    best = joined
        for index, ca, cb in members:
            if index in best:
                continue
            if frozenset((ca, cb)) in loose:
                unresolved = True
                continue
            missed_index.append(index)
    missed = [
        [f"{pairs[i][0][0]}.{pairs[i][0][1]}", f"{pairs[i][1][0]}.{pairs[i][1][1]}"]
        for i in sorted(missed_index)
    ]
    return {"missed": missed, "alias_unresolved": unresolved, "group_company": group_company}


# --- 오라클 어댑터 (§3.3 v1.2) -------------------------------------------------------


def _first_name(ref: Any) -> str:
    return str(ref[0] if isinstance(ref, list) else ref)


def evaluate(
    spec: Mapping[str, Any],
    outcome: Mapping[str, Any] | None,
    result: Mapping[str, Any] | None,
    *,
    count_rows_ok: bool = False,
) -> tuple[str, Any, str]:
    """하네스 `evaluate_oracle` + 벤치 어댑터 2종 → (판정, 상세, 판정 방식).

    판정 방식: `as_is` · `single_cell_renamed`(1×1 결과의 열 이름을 명세 첫 후보로 바꿔 판정) ·
    `count_fallback`(건수 질문에 목록으로 답함 → 같은 오라클을 행 수로 판정).

    keyset 상세는 시스템 결과 행 수가 키 수와 다를 때만 `system_rows`를 더한다 — keyset 은 중복
    행을 보지 않으므로 「행 수 ≠ 키 수」 표지다(plans/149 W4 (6) · F12 · 리포트가 표시한다).
    """
    from scripts.scenario.oracle import _resolve, evaluate_oracle

    spec = dict(spec)
    mode = "as_is"
    rows = list((result or {}).get("rows") or [])
    columns = list((result or {}).get("columns") or [])
    if (
        spec.get("compare") == "value"
        and isinstance(result, Mapping)
        and result.get("status") == "ok"
    ):
        if count_rows_ok and len(rows) != 1:
            spec = {k: v for k, v in spec.items() if k != "value"}
            spec["compare"] = "count"
            mode = "count_fallback"
        elif len(rows) == 1 and len(columns) == 1 and _resolve(spec.get("value"), columns) is None:
            new = _first_name(spec["value"])
            result = {**result, "columns": [new], "rows": [{new: rows[0].get(columns[0])}]}
            mode = "single_cell_renamed"
    verdict, detail = evaluate_oracle(
        spec, dict(outcome) if outcome else None, dict(result) if result else None
    )
    if isinstance(detail, dict) and detail.get("compare") == "keyset":
        keys = detail.get("system_keys")
        system_rows = sum(1 for row in rows if isinstance(row, Mapping))
        if isinstance(keys, int) and system_rows != keys:
            detail["system_rows"] = system_rows
    return verdict, detail, mode


_KEY_LISTS = (
    "missing",
    "extra",
    "overlap",
    "lost_stable",
    "outside_union",
    "missing_must",
    "not_allowed",
    "system_top",
    "oracle_top",
)
_ASCII_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


#: 판정 상세에서 가짜 값으로 바뀔 수 있는 칸(`oracle.substituted_fields` 어휘 · 관문 ② 양성 검사).
#: 키 목록은 이름 그대로, `oracle_top`·`value_diffs`는 `.key`(키)·`.value`(값) 로 나누고,
#: `value.scalar`는 `compare: value` 상세의 최상위 수(`oracle`·`system`·`pre`·`low`·`high`)다.
DETAIL_FIELDS: tuple[str, ...] = (
    *(name for name in _KEY_LISTS if name != "oracle_top"),
    "oracle_top.key",
    "oracle_top.value",
    "value_diffs.key",
    "value_diffs.value",
    "value.scalar",
)
_VALUE_SCALARS = ("oracle", "system", "pre", "low", "high")
_DIFF_VALUES = ("oracle", "system")


def _fake_number(value: Any, fakes: FakeValues) -> Any:
    """값 → 같은 모양의 가짜 값(None 은 그대로).

    가짜 글이 같은 타입의 수로 되읽혀 글자가 같을 때만 수로 싣고(정수는 정수), 아니면 가짜 글
    그대로 싣는다 — 앞자리 0 등으로 모양이 바뀐 수는 이 run 가짜 값으로 다시 알아볼 수 없다.
    `1500`과 `1500.0`은 같은 가짜 정수부를 받는다(`FakeValues`의 소수 0 정규화).
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fakes.fake(str(value))
    fake = fakes.fake(str(value))
    try:
        number: int | float = int(fake) if isinstance(value, int) else float(fake)
    except ValueError:
        return fake
    return number if str(number) == fake else fake


def _values_faked(value_grade: str | None) -> bool:
    """생성기 경로에서 행별 값을 가짜 값으로 바꾸는 등급 — 일반이 아니면 전부(None = 가장 엄격)."""
    return value_grade != "general"


def scalar_grade(
    value_grade: str | None, system_grade: str | None, *, count_result: bool = False
) -> str:
    """`compare: value` 최상위 수의 등급(plans/149 W4 (5) · F10 · 교정 1).

    기본은 명세 값 컬럼 등급과 시스템 결과 값 열 등급 중 **엄격한 쪽**이다(None 은 `unclassified`).
    예외는 시스템 값 열이 순수 `COUNT(…)` 결과일 때(`count_result` · `redact.is_count_column`)
    뿐이다: 결과 요약이 원값으로 남긴 건수를 판정 상세만 가짜 값으로 내면 같은 수가 두 값으로
    보이므로, 명세 등급을 모르면 general 로 본다. 명세 등급을 알면(금액 등) 그 등급을 유지한다.
    """
    from .catalog import strictest

    if count_result:
        return value_grade if value_grade is not None else "general"
    return strictest([value_grade or "unclassified", system_grade or "unclassified"])


def substituted_fields(
    detail: Any,
    *,
    value_grade: str | None,
    keys_allowed: bool,
    system_grade: str | None = None,
    count_result: bool = False,
) -> list[str]:
    """생성기 경로 `sanitize_detail`이 가짜 값으로 바꾼 칸(`DETAIL_FIELDS` 어휘 · 있는 칸만)."""
    if not isinstance(detail, Mapping):
        return []
    out: list[str] = []
    values = _values_faked(value_grade)
    if not keys_allowed:
        out += [
            name
            for name in _KEY_LISTS
            if name != "oracle_top" and isinstance(detail.get(name), list)
        ]
    if isinstance(detail.get("oracle_top"), list):
        out += ["oracle_top.key"] if not keys_allowed else []
        out += ["oracle_top.value"] if values else []
    if isinstance(detail.get("value_diffs"), list):
        out += ["value_diffs.key"] if not keys_allowed else []
        out += ["value_diffs.value"] if values else []
    scalar = scalar_grade(value_grade, system_grade, count_result=count_result)
    if _values_faked(scalar) and detail.get("compare") == "value":
        out.append("value.scalar")
    return out


def detail_key_fields(detail: Any) -> list[str]:
    """판정 상세에 키 값이 목록으로 실린 칸(`DETAIL_FIELDS` 어휘) — 키를 남기지 않는 기록 검사용."""
    if not isinstance(detail, Mapping):
        return []
    out = [
        name
        for name in _KEY_LISTS
        if name != "oracle_top" and isinstance(detail.get(name), list) and detail[name]
    ]
    if isinstance(detail.get("oracle_top"), list) and detail["oracle_top"]:
        out.append("oracle_top.key")
    if isinstance(detail.get("value_diffs"), list) and detail["value_diffs"]:
        out.append("value_diffs.key")
    return out


#: 등록 DB id 집합 캐시(성공만 담는다) · 레지스트리 로드 실패로 내린 횟수(프로세스 누적).
_REGISTERED_DB_IDS: frozenset[str] | None = None
_REGISTRY_FALLBACKS = 0


def registered_db_ids() -> frozenset[str]:
    """per_db 키 태그로 치환 없이 남길 수 있는 DB id — `config/db_registry.yaml` 등록 id ∪ 벤치
    `DB_ID`(교정 3차c).

    레지스트리는 git 에 있는 등록점(신규 DB 는 여기와 `.env`에만 등록)이라 값이 데이터가 아니다.
    오라클 `rows_by_db`의 키는 시나리오 `db_ids`(등록 id)라 이 집합 안이다 — 관문은 기록만 보고
    판정하므로 run 마다 달라지는 집합을 쓰지 않는다.

    레지스트리를 못 읽으면(파일 없음·파싱 실패 `RegistryError` · `src.routing` import 실패) run 을
    죽이지 않고 `{DB_ID}`로 내린다(교정 3차d M-3c-1) — 다른 태그는 전부 치환 칸이 되니 안전한
    쪽이다. 실패는 캐시하지 않고(다음 호출이 다시 읽는다), 첫 실패에 경고 1줄을 남기고
    `registry_fallbacks()`를 올린다(부르는 쪽이 run 카운터 `registry_fallback`로 옮긴다).
    """
    global _REGISTERED_DB_IDS, _REGISTRY_FALLBACKS
    from . import DB_ID

    if _REGISTERED_DB_IDS is not None:
        return _REGISTERED_DB_IDS
    try:
        from src.routing.registry import get_registry

        ids = frozenset(get_registry().db_ids()) | {DB_ID}
    except Exception as exc:  # noqa: BLE001 — 어떤 실패든 닫힌 쪽으로 내린다
        if _REGISTRY_FALLBACKS == 0:
            logger.warning(
                "DB 레지스트리 로드 실패 — per_db 키 태그를 %s 외 전부 치환한다(%s)",
                DB_ID,
                type(exc).__name__,
            )
        _REGISTRY_FALLBACKS += 1
        return frozenset({DB_ID})
    _REGISTERED_DB_IDS = ids
    return ids


def registry_fallbacks() -> int:
    """`registered_db_ids`가 레지스트리 로드 실패로 내린 횟수(프로세스 누적)."""
    return _REGISTRY_FALLBACKS


def registered_db_ids_cache_clear() -> None:
    """등록 DB id 캐시와 폴백 횟수를 비운다(레지스트리를 바꿔 끼우는 테스트용)."""
    global _REGISTERED_DB_IDS, _REGISTRY_FALLBACKS
    _REGISTERED_DB_IDS = None
    _REGISTRY_FALLBACKS = 0


def map_detail(
    detail: Any,
    fields: Iterable[str],
    *,
    substituted: Callable[[Any], Any],
    other: Callable[[Any], Any],
    key_tagged: bool = False,
) -> Any:
    """판정 상세의 치환 칸에는 `substituted`, 나머지에는 `other`를 씌운 사본(구조 유지).

    기록 쪽은 치환 칸을 그대로 두고 나머지만 가린다(가짜 값을 다시 가리면 관문이 알아보지 못한다).
    관문 쪽은 치환 칸 잎만 이 run 가짜 값인지 본다.

    `key_tagged`(per_db 판정 — 시스템 결과에 `_source_db` 열)면 키마다 맨 앞 DB id 태그는 치환
    칸에서 빼 `other`를 씌운다(교정 3차b Minor-A). DB id 는 데이터 값이 아니라 git 에 있는 등록
    식별자라 치환하면 DB 출처만 사라지고, 태그가 2차에서 처음 생성기에 들어가 관문 ①을 세웠다.
    예외는 **등록 DB id**(`registered_db_ids`)인 태그에만 준다(교정 3차c) — 시스템 쪽 태그는 결과
    셀(`_source_db`)이라 생성 SQL 이 다른 열을 그 이름으로 별칭하면 호스트명 같은 원값이 온다.
    등록 밖 태그는 예전처럼 치환 칸이다. 관문도 이 함수로 같은 집합을 본다(표지만 믿지 않는다).
    """
    if not isinstance(detail, Mapping):
        return other(detail)
    chosen = set(fields)

    def keyed(apply: Callable[[Any], Any]) -> Callable[[Any], Any]:
        if not key_tagged:
            return apply

        def run(key: Any) -> Any:
            if isinstance(key, list) and key and str(key[0]) in registered_db_ids():
                return [*other([key[0]]), *apply(list(key[1:]))]
            return apply(key)

        return run

    out: dict[str, Any] = {}
    for name, node in detail.items():
        if name in chosen and key_tagged and name in _KEY_LISTS and isinstance(node, list):
            out[name] = [keyed(substituted)(item) for item in node]
        elif name in chosen:
            out[name] = substituted(node)
        elif name == "oracle_top" and isinstance(node, list):
            key = keyed(substituted) if "oracle_top.key" in chosen else other
            value = substituted if "oracle_top.value" in chosen else other
            out[name] = [
                [*key(list(item[:-1])), *value(list(item[-1:]))]
                if isinstance(item, list)
                else other(item)
                for item in node
            ]
        elif name == "value_diffs" and isinstance(node, list):
            key = keyed(substituted) if "value_diffs.key" in chosen else other
            value = substituted if "value_diffs.value" in chosen else other
            out[name] = [
                {
                    k: key(v) if k == "key" else value(v) if k in _DIFF_VALUES else other(v)
                    for k, v in item.items()
                }
                if isinstance(item, Mapping)
                else other(item)
                for item in node
            ]
        elif (
            name in _VALUE_SCALARS
            and "value.scalar" in chosen
            and detail.get("compare") == "value"
        ):
            out[name] = substituted(node)
        else:
            out[name] = other(node)
    return out


def sanitize_detail(
    detail: Any,
    *,
    value_grade: str | None,
    keys_allowed: bool = True,
    labels: Mapping[str, str] | None = None,
    fakes: FakeValues | None = None,
    key_tagged: bool = False,
    system_grade: str | None = None,
    count_result: bool = False,
) -> Any:
    """판정 상세 거르기(§3.5.6).

    - 금액 값 열의 행별 값은 지운다(`oracle_top`의 값 · `value_diffs`의 수치).
    - `keys_allowed=False`(시스템 쪽 키 열이 일반 컬럼이 아니거나 값이 강등됨)면 키 목록을 건수로만
      남긴다 — 키 명세가 general 이어도 시스템 값은 별칭으로 찾은 「아무 열」에서 온다.
    - `header`(시스템이 지은 열 이름 — 값일 수 있다)는 요약과 같은 이름(`labels`)으로 바꾸고, 모르는
      이름은 ASCII 식별자 모양일 때만 남긴다.
    - `fakes`(run 의 가짜 값 생성기 · plans/145 L8)가 있으면 지우거나 건수로 줄이는 대신 구조를
      유지한 채 키 값마다 가짜 값을 싣고, 값 등급이 일반이 아니면(None 포함 — 가장 엄격) 행별 값
      (`oracle_top` 끝 원소 · `value_diffs`의 수 · `compare: value`의 최상위 수)도 가짜 값으로
      싣는다(같은 원값 → 같은 가짜 값). 바뀐 칸은 `substituted_fields`가 같은 규칙으로 센다.
      `key_tagged`(per_db)면 키 맨 앞 DB id 태그는 바꾸지 않는다(`map_detail`).
      `compare: value` 최상위 수는 `scalar_grade`(명세 등급과 `system_grade` 중 엄격한 쪽 · 순수
      `COUNT` 결과 열(`count_result`)이면 명세 등급, 모르면 general)로 정한다.
    """
    if not isinstance(detail, Mapping):
        return detail
    out = copy.deepcopy(dict(detail))
    if fakes is not None:
        generator = fakes
        out = map_detail(
            out,
            substituted_fields(
                out,
                value_grade=value_grade,
                keys_allowed=keys_allowed,
                system_grade=system_grade,
                count_result=count_result,
            ),
            substituted=lambda node: _fake_tree(node, generator),
            other=lambda node: node,
            key_tagged=key_tagged,
        )
    elif value_grade == "amount":
        if isinstance(out.get("oracle_top"), list):
            out["oracle_top"] = [
                list(item[:-1]) for item in out["oracle_top"] if isinstance(item, list)
            ]
        if isinstance(out.get("value_diffs"), list):
            out["value_diffs"] = [
                {"key": d.get("key"), "differs": True}
                for d in out["value_diffs"]
                if isinstance(d, Mapping)
            ]
    if not keys_allowed and fakes is None:
        for name in _KEY_LISTS:
            if isinstance(out.get(name), list):
                out[name] = len(out[name])
        if isinstance(out.get("value_diffs"), list):
            out["value_diffs"] = len(out["value_diffs"])
    if isinstance(out.get("header"), list):
        known = dict(labels or {})
        out["header"] = [
            known.get(str(name), str(name) if _ASCII_NAME.match(str(name)) else f"열#{i}")
            for i, name in enumerate(out["header"], start=1)
        ]
    return out


def _fake_tree(node: Any, fakes: FakeValues) -> Any:
    """치환 칸 하위 트리 → 잎마다 가짜 값(목록 구조 유지 · 빈 값은 그대로)."""
    if isinstance(node, list):
        return [_fake_tree(part, fakes) for part in node]
    if node is None or (isinstance(node, str) and node == ""):
        return node
    return _fake_number(node, fakes)


# --- 실패 분류 -------------------------------------------------------------------------


@dataclass
class TurnFacts:
    """턴 하나의 판정 재료(메모리 전용 — 원값 포함)."""

    status: str
    expected_db_ids: list[str] = field(default_factory=list)
    observed_db_ids: list[str] = field(default_factory=list)
    executed: list[dict[str, Any]] = field(default_factory=list)  # sql·source·success·error
    response: str = ""
    result: Mapping[str, Any] | None = None
    verdict: str | None = None  # pass·fail·hold · observe 턴은 None
    observe: Mapping[str, Any] | None = None
    reply_declared: bool = False
    key_refs: list[list[str]] = field(default_factory=list)
    gold_tables: list[str] = field(default_factory=list)
    schema_context: Mapping[str, Any] | None = None
    access_denied: bool = False
    #: 조인 키 쌍 ``((표, 칸), (표, 칸))``(plans/150 W2 (6)) — 있을 때만 `join_*` 분석 칸이 생긴다
    join_keys: list[tuple[tuple[str, str], tuple[str, str]]] = field(default_factory=list)


@dataclass(frozen=True)
class CatalogFacts:
    """카탈로그에서 뽑은 판정용 사실 — 컬럼·테이블 이름 · 날짜 문자열 컬럼 · 복합 키 군."""

    columns: frozenset[str]
    tables: frozenset[str]
    date_columns: frozenset[str]
    key_groups: tuple[tuple[frozenset[str], tuple[str, ...]], ...]
    #: 승인된 코드값(plans/133 A4) — 컬럼 → 값. 비었으면 `code_value` 판정은 보류(분류하지 않는다).
    code_values: Mapping[str, frozenset[str]] = field(default_factory=dict)
    #: 코드·여부 칸(plans/150 W2 (5)) — 값 종류 `code_text`·`flag_yn` · P1 `profile.code` · 이름이나
    #: 설명이 「코드」·「여부」로 끝나는 칸. 승인 코드값이 없는 이 칸에 리터럴 등호를 쓰면
    #: `unknown_code_value`에 `NO_APPROVED_CODES`를 단다(관측 · 판정 불변).
    code_marked: frozenset[str] = frozenset()

    @classmethod
    def from_catalog(
        cls, catalog: Mapping[str, Any], *, code_values: Mapping[str, Any] | None = None
    ) -> CatalogFacts:
        """카탈로그(이름·값 종류·키) + 승인 프로필 코드값.

        코드값은 메모리 전용이다 — 카탈로그에는 건수만 싣는다.
        """
        tables = catalog.get("tables") or {}
        columns = {c["name"] for t in tables.values() for c in t.get("columns") or []}
        dates = {
            c["name"]
            for t in tables.values()
            for c in t.get("columns") or []
            if c.get("value_kind") == "date_text_yyyymmdd"
        }
        groups = []
        for group in catalog.get("same_key_groups") or []:
            keys = tuple(tables.get(group[0], {}).get("key") or [])
            if keys:
                groups.append((frozenset(t.casefold() for t in group), keys))
        codes = {
            str(key).rsplit(".", 1)[-1]: frozenset(str(v) for v in values)
            for key, values in (code_values or {}).items()
            if isinstance(values, (list, tuple)) and values
        }
        marked = {
            c["name"]
            for t in tables.values()
            for c in t.get("columns") or []
            if c.get("value_kind") in ("code_text", "flag_yn")
            or (c.get("profile") or {}).get("code") is True
            or any(
                str(text or "").strip().endswith(_CODE_SUFFIXES)
                for text in (c.get("name"), c.get("meaning"))
            )
        }
        return cls(
            frozenset(columns),
            frozenset(tables),
            frozenset(dates),
            tuple(groups),
            codes,
            frozenset(marked),
        )


def _sqls_for(facts: TurnFacts, db_id: str) -> list[dict[str, Any]]:
    return [e for e in facts.executed if e.get("sql") and (e.get("source") in (None, "", db_id))]


def _has_tabular_answer(response: str) -> bool:
    lines = [line for line in (response or "").splitlines() if _TABULAR_LINE.match(line)]
    return len(lines) >= 2


def analyze_sql(facts: TurnFacts, catalog: CatalogFacts, *, db_id: str) -> dict[str, Any]:
    """실행 SQL 분석 — 테이블·컬럼·방언 함정·날짜 문자열 오용·조인 키 부족.

    기록해도 되는 이름·불린만 돌려준다.
    """
    entries = _sqls_for(facts, db_id)
    tables: set[str] = set()
    columns: set[str] = set()
    hazards: set[str] = set()
    date_misuse = join_partial = schema_probe = unknown_code = no_codes = False
    identifiers = catalog.columns | catalog.tables
    approved = {k.casefold() for k in catalog.code_values}
    unapproved = sorted(c for c in catalog.code_marked if c.casefold() not in approved)
    joins: list[dict[str, Any]] = []
    for entry in entries:
        sql = str(entry["sql"])
        tables |= set(sql_tables(sql))
        columns |= set(sql_columns(sql, catalog.columns))
        hazards |= set(dialect_hazards(sql, identifiers))
        date_misuse = date_misuse or date_text_misuse(sql, catalog.date_columns)
        join_partial = join_partial or join_key_partial(sql, catalog.key_groups)
        schema_probe = schema_probe or bool(
            re.search(r"(?i)information_schema|show\s+(create|columns)", strip_comments(sql))
        )
        unknown_code = unknown_code or _unknown_code_value(sql, catalog.code_values)
        no_codes = no_codes or _literal_equal(sql, unapproved)
        if facts.join_keys:
            joins.append(join_key_judgement(sql, facts.join_keys))
    used = {c.casefold() for c in columns}
    key_used = [alt for ref in facts.key_refs for alt in ref if alt.casefold() in used]
    unknown: bool | str | None = unknown_code if catalog.code_values else None
    if not unknown_code and no_codes:
        unknown = NO_APPROVED_CODES
    analysis: dict[str, Any] = {
        "tables": sorted(tables),
        "columns": sorted(columns),
        "key_columns_used": sorted(set(key_used)),
        "dialect_hazards": sorted(hazards),
        "date_text_misuse": date_misuse,
        "join_key_partial": join_partial,
        "schema_query_by_system": schema_probe,
        "unknown_code_value": unknown,
        "sql_count": len(entries),
        "sql_count_all": sum(1 for e in facts.executed if e.get("sql")),
        "failed_syntax": sum(
            1
            for e in entries
            if e.get("success") is False and _SYNTAX_ERROR.search(str(e.get("error") or ""))
        ),
    }
    if facts.join_keys:  # 조인 키를 선언한 턴만(plans/150 W2 (6) — 없는 턴은 기록 바이트 그대로)
        missed = sorted({tuple(pair) for j in joins for pair in j["missed"]})
        analysis["join_key_miss"] = [list(pair) for pair in missed]
        analysis["join_alias_unresolved"] = any(j["alias_unresolved"] for j in joins)
        analysis["join_group_company"] = any(j["group_company"] for j in joins)
    return analysis


def classify(facts: TurnFacts, analysis: Mapping[str, Any], *, db_id: str) -> list[str]:
    """실패 분류(한 턴 여러 개 · `TAXONOMY` 순서). 통과 턴은 빈 목록."""
    if facts.access_denied:
        return ["permission_denied"]
    labels: set[str] = set()
    observed = set(facts.observed_db_ids) | {
        str(e.get("source")) for e in facts.executed if e.get("source")
    }
    if facts.expected_db_ids and not set(facts.expected_db_ids) <= observed:
        labels.add("routing_miss")
    if facts.status == "clarification" and not facts.reply_declared and facts.observe is None:
        labels.add("asked_back")
    if analysis.get("failed_syntax"):
        labels.add("dialect_error")
    if analysis.get("dialect_hazards"):
        labels.add("dialect_silent")
    labels |= _prompt_stop_labels(facts.schema_context, db_id)
    rows = list((facts.result or {}).get("rows") or [])
    if facts.observe is not None:
        if facts.observe.get("no_data") and _fabricated(facts, rows):
            labels.add("fabricated")
        return [
            label
            for label in TAXONOMY
            if label in labels
            and label
            in (
                "routing_miss",
                "backend_limit",
                "selection_none",
                "fabricated",
                "dialect_error",
                "dialect_silent",
            )
        ]
    if facts.verdict == "pass":
        return []
    if not analysis.get("sql_count_all") and facts.status != "clarification":
        labels.add("no_sql")
    if analysis.get("date_text_misuse"):
        labels.add("date_text")
    if analysis.get("join_key_partial"):
        labels.add("join_key_partial")
    if analysis.get("unknown_code_value") is True:  # 「승인 코드값 없음」 표시는 관측만(W2 (5))
        labels.add("code_value")
    context = facts.schema_context
    if context is not None:
        if facts.gold_tables and not context.get("gold_tables_presented"):
            labels.add("schema_miss")
        with_meaning = context.get("key_columns_with_meaning")
        presented = {c.casefold() for c in context.get("key_columns_presented") or []}
        if with_meaning is not None:
            meaning = {c.casefold() for c in with_meaning}
            if any(
                any(a.casefold() in presented for a in ref)
                and not any(a.casefold() in meaning for a in ref)
                for ref in facts.key_refs
            ):
                labels.add("meaning_absent")
    if analysis.get("sql_count"):
        used = {c.casefold() for c in analysis.get("key_columns_used") or []}
        if any(not any(a.casefold() in used for a in ref) for ref in facts.key_refs):
            labels.add("column_misread")
    if facts.verdict == "fail" and not labels:
        labels.add("key_mismatch")
    return [label for label in TAXONOMY if label in labels]


def _prompt_stop_labels(context: Mapping[str, Any] | None, db_id: str) -> set[str]:
    """측정 연결점이 옮긴 DB 별 프롬프트 칸(plans/139 W6-d) → `backend_limit`·`selection_none`.

    입력 한도: 재생성 종결 사유 `backend_limit` · 예산 단계 `exceeded` · 백엔드 보고 토큰 수 있음.
    선별 0개: 선별 출처 `none` · 종결 사유 `selection_none`. 이 벤치 DB 칸만 본다.
    """
    entry = ((context or {}).get("dbs") or {}).get(db_id)
    if not isinstance(entry, Mapping):
        return set()
    reasons = set(entry.get("stop_reasons") or [])
    labels: set[str] = set()
    if (
        "backend_limit" in reasons
        or entry.get("budget_stage") == "exceeded"
        or entry.get("backend_reported_tokens") is not None
    ):
        labels.add("backend_limit")
    if "selection_none" in reasons or entry.get("selection_source") == "none":
        labels.add("selection_none")
    return labels


def _unknown_code_value(sql: str, code_values: Mapping[str, frozenset[str]]) -> bool:
    """코드 컬럼과 비교한 리터럴이 승인된 코드값에 없는가(A4 미승인 = 판정하지 않음)."""
    from src.utils.synonym_usage import _extract_column_literals

    for column, allowed in code_values.items():
        if any(
            value not in allowed
            for value in _extract_column_literals(strip_comments(sql), [column])
        ):
            return True
    return False


def _literal_equal(sql: str, columns: Iterable[str]) -> bool:
    """칸에 리터럴 등호(`칸 = '값'`·`칸 = 0`·`칸 IN ('값', …)`)를 썼는가 — 한정·백틱 이름 포함."""
    body = strip_comments(sql)
    for column in columns:
        name = re.escape(column)
        pattern = (
            rf"(?:`{name}`|(?<![\w`]){name}(?![\w`]))\s*"
            r"(?:=\s*(?:'|[-+]?\d)|in\s*\(\s*(?:'|[-+]?\d))"
        )
        if re.search(pattern, body, re.IGNORECASE):
            return True
    return False


def _fabricated(facts: TurnFacts, rows: list[Mapping[str, Any]]) -> bool:
    """데이터가 없어야 할 질문에 값을 내놓았는가.

    결과 행의 핵심 컬럼이 채워졌거나, SQL 없이 표·목록을 냈다.
    """
    if not any(e.get("sql") for e in facts.executed):
        return _has_tabular_answer(facts.response)
    if not rows:
        return False
    if not facts.key_refs:
        return True
    keys = {a.casefold() for ref in facts.key_refs for a in ref}
    return any(
        str(k).casefold() in keys and str(v or "").strip() not in ("", "None", "null")
        for row in rows
        for k, v in row.items()
    )


#: 예산 단계·선별 출처의 「나쁜 정도」 순서.
#: 한 턴에 같은 DB task 가 여럿이면 가장 나쁜 값을 남긴다.
_STAGE_RANK = {"within": 0, "materials": 1, "samples": 2, "exceeded": 3}
_SELECTION_RANK = {"llm": 0, "lexical": 1, "none": 2}


def _worst(current: Any, new: Any, rank: Mapping[str, int]) -> Any:
    if new not in rank:
        return current
    return new if current not in rank or rank[new] > rank[current] else current


def _larger(current: Any, new: Any) -> Any:
    if not isinstance(new, int) or isinstance(new, bool):
        return current
    return new if current is None else max(current, new)


def prompt_by_db(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """측정 수신 레코드의 DB 별 프롬프트 칸(plans/139 W6-d) → 턴 단위 DB 별 요약.

    같은 DB task 가 여럿이면 칸마다 따로 모은다 — 수는 최댓값, 예산 단계·선별 출처는 가장 나쁜 값,
    종결 사유는 정렬한 합집합. 칸이 없던 옛 레코드는 null 로 남는다(숫자·열거만 · 값 없음).
    """
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        for db_id, shape in (record.get("dbs") or {}).items():
            if not isinstance(shape, Mapping):
                continue
            entry = out.setdefault(
                str(db_id),
                {
                    "prompt_tokens_est": None,
                    "budget_stage": None,
                    "backend_reported_tokens": None,
                    "selection_source": None,
                    "selected_count": None,
                    "stop_reasons": [],
                },
            )
            for key in ("prompt_tokens_est", "backend_reported_tokens", "selected_count"):
                entry[key] = _larger(entry[key], shape.get(key))
            entry["budget_stage"] = _worst(
                entry["budget_stage"], shape.get("budget_stage"), _STAGE_RANK
            )
            entry["selection_source"] = _worst(
                entry["selection_source"], shape.get("selection_source"), _SELECTION_RANK
            )
            reason = shape.get("stop_reason")
            if isinstance(reason, str) and reason:
                entry["stop_reasons"] = sorted({*entry["stop_reasons"], reason})
            _merge_selection(entry, shape)
            _merge_usage(entry, shape)
    return out


def _merge_selection(entry: dict[str, Any], shape: Mapping[str, Any]) -> None:
    """선별 판독 재료(plans/151 W5) — 다리 테이블 이름(나온 순 합집합)·폴백 사유 코드(정렬한
    합집합). 실렸을 때만 칸이 생긴다(옛 레코드·폴백 없는 턴은 칸 없음)."""
    bridged = shape.get("selection_bridged")
    if isinstance(bridged, list) and bridged:
        merged = entry.setdefault("selection_bridged", [])
        merged += [str(t) for t in bridged if str(t) not in merged]
    reason = shape.get("selection_reason")
    if isinstance(reason, str) and reason:
        entry["selection_reasons"] = sorted({*entry.get("selection_reasons", []), reason})


_FINGERPRINT = re.compile(r"^[0-9a-f]{12}$")
#: 같은 턴의 task 들이 서로 다른 지문을 실었을 때
MIXED_FINGERPRINT = "mixed"


def _merge_usage(entry: dict[str, Any], shape: Mapping[str, Any]) -> None:
    """자산 사용 표지(plans/143 W1)를 턴 칸에 모은다 — 실렸을 때만 칸이 생긴다.

    `assets`는 키별 지문(다르면 `mixed`)·건수 최댓값, `templates`는 task 별 템플릿 표지(중복 제거 ·
    `final_sql_from_template` False면 「적중 후 LLM 수정」 — 같은 템플릿이라도 따로 남는다).
    """
    assets = shape.get("assets")
    if isinstance(assets, Mapping):
        merged = entry.setdefault("assets", {})
        for key, mark in assets.items():
            if not isinstance(mark, Mapping):
                continue
            fp = mark.get("fp")
            fp = fp if isinstance(fp, str) and _FINGERPRINT.match(fp) else None
            count = _larger(None, mark.get("n"))
            if key in merged:
                if merged[key]["fp"] != fp:
                    fp = MIXED_FINGERPRINT
                count = _larger(merged[key]["n"], count)
            merged[str(key)] = {"fp": fp, "n": count}
    template = shape.get("template")
    if isinstance(template, Mapping):
        templates = entry.setdefault("templates", [])
        item = {
            k: template.get(k)
            for k in ("outcome", "template_id", "slot_names", "reason", "final_sql_from_template")
        }
        if item not in templates:
            templates.append(item)


def schema_context(
    records: Sequence[Mapping[str, Any]],
    *,
    db_id: str,
    gold_tables: list[str],
    key_refs: list[list[str]],
) -> dict[str, Any] | None:
    """측정 수신 레코드(같은 thread_id · 이 턴) → 턴의 스키마 맥락 요약(§3.4 (2)).

    레코드가 없으면 None. `dbs`는 이 턴에 잡힌 DB 전부의 프롬프트 크기·선별 칸이다(`prompt_by_db`).
    """
    if not records:
        return None
    tables: dict[str, set[str]] = {}
    meaning: set[str] | None = set()
    sample = structure = False
    for record in records:
        shape = (record.get("dbs") or {}).get(db_id)
        if not isinstance(shape, Mapping):
            continue
        structure = structure or bool(shape.get("structure_meta"))
        for name, table in (shape.get("tables") or {}).items():
            bare = _bare(name)
            tables.setdefault(bare, set()).update(table.get("columns") or [])
            sample = sample or bool(table.get("sample_rows"))
            if table.get("with_meaning") is None:
                meaning = None
            elif meaning is not None:
                meaning.update(table["with_meaning"])
    presented_cols = {c.casefold(): c for cols in tables.values() for c in cols}
    keys_presented = sorted(
        {
            presented_cols[a.casefold()]
            for ref in key_refs
            for a in ref
            if a.casefold() in presented_cols
        }
    )
    folded_tables = {t.casefold() for t in tables}
    return {
        "tasks": len(records),
        "presented_tables": sorted(tables),
        "presented_column_count": len(presented_cols),
        "columns_with_meaning": None if meaning is None else len(meaning),
        "sample_rows_presented": sample,
        "structure_meta_present": structure,
        "gold_tables_presented": all(g.casefold() in folded_tables for g in gold_tables),
        "key_columns_presented": keys_presented,
        "key_columns_with_meaning": None
        if meaning is None
        else sorted(c for c in keys_presented if c.casefold() in {m.casefold() for m in meaning}),
        "dbs": prompt_by_db(records),
    }
