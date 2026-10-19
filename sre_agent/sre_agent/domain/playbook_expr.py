"""플레이북 `when` 조건식 — 화이트리스트 문법의 결정적 3값 평가기 (plans/187 §5.2 · §5.7.3 · D-341).

플레이북 스텝의 `when`, 런북 `mitigation[].when`, 알려진 오류 레코드의 `match`·`cause_test`가
같은 평가기를 쓴다. 파이썬 `eval`·`exec`·`ast.literal_eval`은 쓰지 않는다 — 자체 토크나이저와
재귀 하강 파서가 아래 문법만 받는다. 파싱은 로드 시 1회(`compile_expr`)이며 구문 오류는
`ExprSyntaxError`로 그 플레이북을 거부하게 한다.

문법::

    expr     := or_expr
    or_expr  := and_expr ("or" and_expr)*
    and_expr := not_expr ("and" not_expr)*
    not_expr := "not" not_expr | cmp
    cmp      := operand ( cmp_op operand | "in" "[" literal ("," literal)* "]" )?
    cmp_op   := "==" | "!=" | "<" | "<=" | ">" | ">="
    operand  := "(" expr ")" | literal | path
    literal  := '문자열' | "문자열" | ["-"] 숫자
    path     := 이름 ("." 이름)*
                # 루트 = 스텝 ID(S0~S9 · 접미 소문자 1자 — 예 S0r) 또는 판독 enum 이름

평가는 3값(`Tri.TRUE`·`Tri.FALSE`·`Tri.UNKNOWN`)이다. 문맥에 없는 경로·`None` 값은
예외가 아니라 결손이고, 결손이 낀 비교는 `UNKNOWN`이다(§5.7.3 「판단 보류」).
`and`·`or`·`not`은 Kleene 논리를 따른다.
대소 비교는 두 값이 모두 수치일 때만 성립하고 아니면 `UNKNOWN`이다.

판독 enum 이름이 아닌 맨 이름(예 `normal`)은 구문 오류다 — 문자열 값은 따옴표로 쓴다
(`S1.verdict != 'normal'`).
맨 이름을 경로로 읽어 늘 결손이 되는 조용한 오작동을 로드 시점에 막는다.

domain 계층 — 표준 라이브러리만 의존한다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

#: 경로 루트로 쓸 수 있는 판독 enum 이름(§4.2 R-A~R-H 출력). 스텝 결과 밖의 최상위 판독 값이다.
READING_NAMES: frozenset[str] = frozenset({
    "scope",
    "service_spread",
    "load_mode",
    "little_check",
    "dominant",
    "db_split",
    "cpu_owner",
    "heap_shape",
    "host_saturation",
    "fs_eta",
    "change_candidates",
})

#: 스텝 ID 형식 — S0~S9 + 선택 접미 소문자 1자(역방향 S0′ = `S0r` · 보조 스텝 = `S5a` 등).
STEP_ID_PATTERN = re.compile(r"^S[0-9][a-z]?$")

#: 식 길이·괄호 중첩 상한 — 재귀 하강 파서의 재귀 깊이를 묶는다.
MAX_EXPR_LENGTH = 500
MAX_NESTING = 16


class ExprSyntaxError(ValueError):
    """`when` 식의 구문 오류(로드 시 그 플레이북·레코드를 거부한다)."""


class Tri(StrEnum):
    """3값 평가 결과 — 결손이 낀 판정은 `UNKNOWN`(판단 보류)."""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def _tri(value: bool) -> Tri:
    return Tri.TRUE if value else Tri.FALSE


# ---------------------------------------------------------------------------
# 토크나이저
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>[0-9]+(?:\.[0-9]+)?)
  | (?P<str>'[^']*'|"[^"]*")
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)
  | (?P<op>==|!=|<=|>=|<|>)
  | (?P<punct>[()\[\],-])
    """,
    re.VERBOSE,
)

_KEYWORDS = frozenset({"and", "or", "not", "in"})


@dataclass(frozen=True)
class _Tok:
    kind: str  # num · str · name · op · punct · kw · end
    text: str
    pos: int


def _tokenize(source: str) -> list[_Tok]:
    toks: list[_Tok] = []
    pos = 0
    while pos < len(source):
        m = _TOKEN_RE.match(source, pos)
        if m is None:
            raise ExprSyntaxError(f"허용되지 않는 문자 {source[pos]!r} (위치 {pos})")
        kind = m.lastgroup or ""
        text = m.group()
        if kind == "name" and text in _KEYWORDS:
            kind = "kw"
        if kind != "ws":
            toks.append(_Tok(kind, text, pos))
        pos = m.end()
    toks.append(_Tok("end", "", len(source)))
    return toks


# ---------------------------------------------------------------------------
# 구문 트리
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Lit:
    value: str | int | float


@dataclass(frozen=True)
class _Path:
    parts: tuple[str, ...]


@dataclass(frozen=True)
class _Cmp:
    op: str
    left: _Node
    right: _Node


@dataclass(frozen=True)
class _In:
    left: _Node
    items: tuple[str | int | float, ...]


@dataclass(frozen=True)
class _And:
    items: tuple[_Node, ...]


@dataclass(frozen=True)
class _Or:
    items: tuple[_Node, ...]


@dataclass(frozen=True)
class _Not:
    item: _Node


_Node = _Lit | _Path | _Cmp | _In | _And | _Or | _Not


# ---------------------------------------------------------------------------
# 재귀 하강 파서
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, source: str) -> None:
        self._toks = _tokenize(source)
        self._i = 0
        self._depth = 0

    def _peek(self) -> _Tok:
        return self._toks[self._i]

    def _next(self) -> _Tok:
        tok = self._toks[self._i]
        self._i += 1
        return tok

    def _expect(self, kind: str, text: str) -> None:
        tok = self._next()
        if tok.kind != kind or tok.text != text:
            raise ExprSyntaxError(
                f"{text!r}이(가) 필요하다 (위치 {tok.pos}, 실제 {tok.text or '끝'!r})"
            )

    def parse(self) -> _Node:
        node = self._or()
        tok = self._peek()
        if tok.kind != "end":
            raise ExprSyntaxError(f"식 뒤에 남은 토큰 {tok.text!r} (위치 {tok.pos})")
        return node

    def _or(self) -> _Node:
        items = [self._and()]
        while self._peek().kind == "kw" and self._peek().text == "or":
            self._next()
            items.append(self._and())
        return items[0] if len(items) == 1 else _Or(tuple(items))

    def _and(self) -> _Node:
        items = [self._not()]
        while self._peek().kind == "kw" and self._peek().text == "and":
            self._next()
            items.append(self._not())
        return items[0] if len(items) == 1 else _And(tuple(items))

    def _not(self) -> _Node:
        tok = self._peek()
        if tok.kind == "kw" and tok.text == "not":
            self._next()
            self._enter()
            node = _Not(self._not())
            self._depth -= 1
            return node
        return self._cmp()

    def _cmp(self) -> _Node:
        left = self._operand()
        tok = self._peek()
        if tok.kind == "op":
            self._next()
            return _Cmp(tok.text, left, self._operand())
        if tok.kind == "kw" and tok.text == "in":
            self._next()
            return _In(left, self._list())
        return left

    def _list(self) -> tuple[str | int | float, ...]:
        self._expect("punct", "[")
        items = [self._literal()]
        while self._peek().kind == "punct" and self._peek().text == ",":
            self._next()
            items.append(self._literal())
        self._expect("punct", "]")
        return tuple(items)

    def _literal(self) -> str | int | float:
        tok = self._next()
        if tok.kind == "str":
            return tok.text[1:-1]
        neg = False
        if tok.kind == "punct" and tok.text == "-":
            neg = True
            tok = self._next()
        if tok.kind == "num":
            value: int | float = float(tok.text) if "." in tok.text else int(tok.text)
            return -value if neg else value
        raise ExprSyntaxError(f"리터럴이 필요하다 (위치 {tok.pos}, 실제 {tok.text or '끝'!r})")

    def _operand(self) -> _Node:
        tok = self._peek()
        if tok.kind == "punct" and tok.text == "(":
            self._next()
            self._enter()
            node = self._or()
            self._depth -= 1
            self._expect("punct", ")")
            return node
        if tok.kind == "name":
            self._next()
            return _Path(_check_path(tok.text, tok.pos))
        if tok.kind in ("str", "num") or (tok.kind == "punct" and tok.text == "-"):
            return _Lit(self._literal())
        raise ExprSyntaxError(f"피연산자가 필요하다 (위치 {tok.pos}, 실제 {tok.text or '끝'!r})")

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > MAX_NESTING:
            raise ExprSyntaxError(f"중첩 상한 {MAX_NESTING} 초과")


def _check_path(text: str, pos: int) -> tuple[str, ...]:
    parts = tuple(text.split("."))
    root = parts[0]
    if not (STEP_ID_PATTERN.match(root) or root in READING_NAMES):
        raise ExprSyntaxError(
            f"미지 이름 {root!r} (위치 {pos}) — 경로 루트는 스텝 ID 또는 판독 enum 이름이다. "
            "문자열 값은 따옴표로 쓴다"
        )
    return parts


# ---------------------------------------------------------------------------
# 평가
# ---------------------------------------------------------------------------


class _Missing:
    """결손 표식 — 문맥에 없는 경로 · `None` 값 · 스칼라가 아닌 값."""


_MISSING = _Missing()

_Scalar = str | int | float | bool


def _resolve(parts: tuple[str, ...], context: Mapping[str, object]) -> _Scalar | _Missing:
    cur: object = context
    for part in parts:
        if not isinstance(cur, Mapping) or part not in cur:
            return _MISSING
        cur = cur[part]
    if isinstance(cur, (str, int, float, bool)):
        return cur
    return _MISSING


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _eq(a: _Scalar, b: _Scalar) -> bool:
    if _is_num(a) and _is_num(b):
        return a == b
    if type(a) is not type(b):
        return False
    return a == b


def _value(node: _Node, context: Mapping[str, object]) -> _Scalar | _Missing:
    if isinstance(node, _Lit):
        return node.value
    if isinstance(node, _Path):
        return _resolve(node.parts, context)
    t = _truth(node, context)
    return _MISSING if t is Tri.UNKNOWN else t is Tri.TRUE


def _truth(node: _Node, context: Mapping[str, object]) -> Tri:
    if isinstance(node, _And):
        results = [_truth(n, context) for n in node.items]
        if Tri.FALSE in results:
            return Tri.FALSE
        return Tri.UNKNOWN if Tri.UNKNOWN in results else Tri.TRUE
    if isinstance(node, _Or):
        results = [_truth(n, context) for n in node.items]
        if Tri.TRUE in results:
            return Tri.TRUE
        return Tri.UNKNOWN if Tri.UNKNOWN in results else Tri.FALSE
    if isinstance(node, _Not):
        inner = _truth(node.item, context)
        if inner is Tri.UNKNOWN:
            return Tri.UNKNOWN
        return Tri.FALSE if inner is Tri.TRUE else Tri.TRUE
    if isinstance(node, _In):
        left = _value(node.left, context)
        if isinstance(left, _Missing):
            return Tri.UNKNOWN
        return _tri(any(_eq(left, item) for item in node.items))
    if isinstance(node, _Cmp):
        a = _value(node.left, context)
        b = _value(node.right, context)
        if isinstance(a, _Missing) or isinstance(b, _Missing):
            return Tri.UNKNOWN
        if node.op == "==":
            return _tri(_eq(a, b))
        if node.op == "!=":
            return _tri(not _eq(a, b))
        if not (_is_num(a) and _is_num(b)):
            return Tri.UNKNOWN
        fa, fb = float(a), float(b)
        if node.op == "<":
            return _tri(fa < fb)
        if node.op == "<=":
            return _tri(fa <= fb)
        if node.op == ">":
            return _tri(fa > fb)
        return _tri(fa >= fb)
    # 맨 피연산자(경로·리터럴) — 불리언 값만 참·거짓, 나머지는 판단 보류.
    v = _value(node, context)
    if isinstance(v, bool):
        return _tri(v)
    return Tri.UNKNOWN


def _collect_roots(node: _Node, out: set[str]) -> None:
    if isinstance(node, _Path):
        out.add(node.parts[0])
    elif isinstance(node, _Cmp):
        _collect_roots(node.left, out)
        _collect_roots(node.right, out)
    elif isinstance(node, _In):
        _collect_roots(node.left, out)
    elif isinstance(node, (_And, _Or)):
        for n in node.items:
            _collect_roots(n, out)
    elif isinstance(node, _Not):
        _collect_roots(node.item, out)


@dataclass(frozen=True)
class Expr:
    """컴파일된 `when` 식(불변 · 재사용). `compile_expr`로만 만든다."""

    source: str
    _root: _Node

    def evaluate(self, context: Mapping[str, object]) -> Tri:
        """문맥(스텝 ID·판독 이름 → 결과 Mapping)으로 3값 평가한다. 예외를 내지 않는다."""
        return _truth(self._root, context)

    def step_refs(self) -> frozenset[str]:
        """식이 참조하는 스텝 ID 루트(판독 이름 제외)."""
        roots: set[str] = set()
        _collect_roots(self._root, roots)
        return frozenset(r for r in roots if STEP_ID_PATTERN.match(r))


def compile_expr(source: str) -> Expr:
    """`when` 문자열을 파싱한다. 구문 오류·길이 초과는 `ExprSyntaxError`."""
    if not isinstance(source, str) or not source.strip():
        raise ExprSyntaxError("빈 식")
    if len(source) > MAX_EXPR_LENGTH:
        raise ExprSyntaxError(f"식 길이 상한 {MAX_EXPR_LENGTH}자 초과")
    return Expr(source, _Parser(source).parse())


# ---------------------------------------------------------------------------
# 목록 조건(all/any) — 알려진 오류 레코드 `match`·`cause_test` 재사용(§5.7.3)
# ---------------------------------------------------------------------------


def eval_all(exprs: Sequence[Expr], context: Mapping[str, object]) -> Tri:
    """모두 참이면 TRUE · 하나라도 거짓이면 FALSE · 그 밖에 결손이 있으면 UNKNOWN."""
    return _truth(_And(tuple(e._root for e in exprs)), context) if exprs else Tri.TRUE


def eval_any(exprs: Sequence[Expr], context: Mapping[str, object]) -> Tri:
    """하나라도 참이면 TRUE · 모두 거짓이면 FALSE · 그 밖에 결손이 있으면 UNKNOWN."""
    return _truth(_Or(tuple(e._root for e in exprs)), context) if exprs else Tri.FALSE


@dataclass(frozen=True)
class ListCondition:
    """`{all: [...]}` · `{any: [...]}` 목록 조건(둘 다 있으면 둘의 AND)."""

    all_of: tuple[Expr, ...] = ()
    any_of: tuple[Expr, ...] = ()

    def evaluate(self, context: Mapping[str, object]) -> Tri:
        """목록 조건을 3값 평가한다."""
        parts: list[Tri] = []
        if self.all_of:
            parts.append(eval_all(self.all_of, context))
        if self.any_of:
            parts.append(eval_any(self.any_of, context))
        if Tri.FALSE in parts:
            return Tri.FALSE
        return Tri.UNKNOWN if Tri.UNKNOWN in parts else Tri.TRUE


def compile_list_condition(spec: Mapping[str, object]) -> ListCondition:
    """`{all: [...], any: [...]}`를 컴파일한다.

    키는 all·any만 받고, 목록은 비어 있지 않은 문자열 목록이어야 한다.
    """
    if not isinstance(spec, Mapping) or not spec:
        raise ExprSyntaxError("목록 조건은 all 또는 any 키를 가진 매핑이다")
    unknown = set(spec) - {"all", "any"}
    if unknown:
        raise ExprSyntaxError(f"목록 조건의 미지 키 {sorted(unknown)}")
    compiled: dict[str, tuple[Expr, ...]] = {}
    for key in ("all", "any"):
        if key not in spec:
            continue
        items = spec[key]
        if not isinstance(items, list) or not items or not all(isinstance(s, str) for s in items):
            raise ExprSyntaxError(f"{key}는 비어 있지 않은 문자열 목록이다")
        compiled[key] = tuple(compile_expr(s) for s in items)
    return ListCondition(all_of=compiled.get("all", ()), any_of=compiled.get("any", ()))
