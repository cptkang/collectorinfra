"""DDL 텍스트 → 스키마 수집 결과(`SchemaInfo`) — 관리자 「DB 구조」 DDL 등록(D-292).

MCP `get_full_schema`가 돌려주는 것과 같은 모양을 DDL 텍스트에서 결정적으로 만든다
(LLM 0 · DB 접속 0). SQL은 실행하지 않고 텍스트로만 읽는다. 같은 구조라면 MCP 수집과 스냅샷
해시가 같아야 이후 변경 점검에서 가짜 차이가 생기지 않으므로, 테이블 키·타입 표기를 MCP 서버의
엔진별 조회 결과에 맞춘다.

- **테이블 키**: PostgreSQL은 `public` 스키마면 bare · 그 밖은 `schema.table` · MariaDB는 기본
  database(레지스트리 `db_schema` — 모르면 한정자 무시)면 bare · DB2는 항상 bare(`TABNAME`)
- **타입**: PG·MariaDB `information_schema.columns.data_type` · DB2 `SYSCAT.COLUMNS.TYPENAME` 표기 —
  길이·정밀도는 뺀다
- **인용 없는 식별자**: PG 소문자 · DB2 대문자 · MariaDB 그대로
- **PK 컬럼은 NOT NULL**(세 엔진 모두 카탈로그가 NOT NULL로 보고한다)
- **읽는 문장**: `CREATE TABLE`(열 목록이 있는 것) · `ALTER TABLE … ADD [CONSTRAINT] PRIMARY KEY /
  FOREIGN KEY` · `ALTER TABLE … ADD [COLUMN]` · `COMMENT ON TABLE/COLUMN`(+ MariaDB `COMMENT '…'`).
  그 밖의 문장(인덱스·뷰·권한·데이터·임시 테이블 등)은 종류별 건수만 센다. 읽지 못한 테이블 정의·
  참조는 경고로 남긴다(조용히 버리지 않는다).
- **컬럼을 하나도 읽지 못한 테이블은 뺀다**(경고) — 스키마 캐시 저장이 컬럼 없는 테이블 하나로
  전체를 거부하기 때문이다(`cache_manager._validate_schema_dict`).
- **문장 끝**은 `;` — MariaDB 덤프의 `DELIMITER` 지시와 DB2 CLP의 `--#SET TERMINATOR` 지시도 따른다.

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from src.dbhub.models import ColumnInfo, SchemaInfo, TableInfo

#: 지원 엔진(MCP 소스 type 정규형)
DDL_ENGINES: tuple[str, ...] = ("postgresql", "db2", "mariadb")
#: 경고 목록 상한 — 넘는 건수는 마지막 한 줄로 요약한다
MAX_WARNINGS = 200

# --- 타입 표기 (MCP 서버 엔진별 조회 결과에 맞춘다) ---

_PG_TYPES: dict[str, str] = {
    "int": "integer", "int4": "integer", "integer": "integer",
    "serial": "integer", "serial4": "integer",
    "bigint": "bigint", "int8": "bigint", "bigserial": "bigint", "serial8": "bigint",
    "smallint": "smallint", "int2": "smallint", "smallserial": "smallint", "serial2": "smallint",
    "decimal": "numeric", "numeric": "numeric",
    "real": "real", "float4": "real",
    "double precision": "double precision", "float8": "double precision",
    "float": "double precision",
    "varchar": "character varying", "character varying": "character varying",
    "char varying": "character varying",
    "char": "character", "character": "character", "bpchar": "character",
    "national character": "character", "national char": "character",
    "national character varying": "character varying",
    "national char varying": "character varying",
    "bool": "boolean", "boolean": "boolean",
    "timestamp": "timestamp without time zone",
    "timestamp without time zone": "timestamp without time zone",
    "timestamptz": "timestamp with time zone",
    "timestamp with time zone": "timestamp with time zone",
    "time": "time without time zone", "time without time zone": "time without time zone",
    "timetz": "time with time zone", "time with time zone": "time with time zone",
    "varbit": "bit varying", "bit varying": "bit varying",
}
# PG 내장 타입 중 이름이 그대로 `data_type`인 것 — 여기도 `_PG_TYPES`에도 없으면 사용자 정의 타입
_PG_BUILTIN: frozenset[str] = frozenset({
    "text", "date", "bytea", "json", "jsonb", "uuid", "inet", "cidr", "macaddr", "macaddr8",
    "money", "xml", "tsvector", "tsquery", "oid", "point", "line", "lseg", "box", "path",
    "polygon", "circle", "bit", "name", "regclass", "pg_lsn", "int4range", "int8range",
    "numrange", "tsrange", "tstzrange", "daterange", "txid_snapshot",
})
_DB2_TYPES: dict[str, str] = {
    "int": "INTEGER", "integer": "INTEGER",
    "dec": "DECIMAL", "decimal": "DECIMAL", "numeric": "DECIMAL", "num": "DECIMAL",
    "float": "DOUBLE", "double": "DOUBLE", "double precision": "DOUBLE",
    "char": "CHARACTER", "character": "CHARACTER",
    "char varying": "VARCHAR", "character varying": "VARCHAR",
    "char large object": "CLOB", "character large object": "CLOB",
    "binary large object": "BLOB",
    "nchar": "GRAPHIC", "national char": "GRAPHIC", "national character": "GRAPHIC",
    "nvarchar": "VARGRAPHIC", "nclob": "DBCLOB",
}
_MARIADB_TYPES: dict[str, str] = {
    "integer": "int", "int4": "int", "int1": "tinyint", "int2": "smallint",
    "int3": "mediumint", "middleint": "mediumint", "int8": "bigint", "serial": "bigint",
    "dec": "decimal", "numeric": "decimal", "fixed": "decimal",
    "double precision": "double", "real": "double",
    "bool": "tinyint", "boolean": "tinyint",
    "character": "char", "nchar": "char", "national char": "char", "national character": "char",
    "character varying": "varchar", "char varying": "varchar", "nvarchar": "varchar",
    "national varchar": "varchar", "national character varying": "varchar",
    "national char varying": "varchar",
    "long": "mediumtext", "long varchar": "mediumtext", "long varbinary": "mediumblob",
    "json": "longtext",
}

# 타입 이름 뒤에 이어 붙는 단어(다단어 타입) — 이 목록 밖의 단어부터는 컬럼 속성이다
_TYPE_CONTINUATIONS: dict[tuple[str, ...], tuple[tuple[str, ...], ...]] = {
    ("double",): (("precision",),),
    ("char",): (("varying",), ("large", "object")),
    ("character",): (("varying",), ("large", "object")),
    ("national",): (("char",), ("character",), ("varchar",)),
    ("national", "char"): (("varying",),),
    ("national", "character"): (("varying",),),
    ("binary",): (("large", "object"),),
    ("long",): (("varchar",), ("vargraphic",), ("varbinary",)),
    ("bit",): (("varying",),),
    ("timestamp",): (("with", "time", "zone"), ("without", "time", "zone"),
                     ("with", "local", "time", "zone")),
    ("time",): (("with", "time", "zone"), ("without", "time", "zone")),
}
_INTERVAL_FIELDS = frozenset({"year", "month", "day", "hour", "minute", "second", "to"})

# 테이블 본문에서 컬럼이 아니라 제약·인덱스로 시작하는 요소
_CONSTRAINT_STARTS = frozenset({
    "CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "KEY", "INDEX", "FULLTEXT", "SPATIAL",
    "EXCLUDE", "LIKE", "PERIOD",
})
# `ALTER TABLE … ADD` 뒤에서 컬럼 추가가 아닌 것
_ADD_NON_COLUMN = _CONSTRAINT_STARTS | frozenset({
    "PARTITION", "RESTRICT", "MATERIALIZED", "VERSIONING", "SYSTEM",
})
_CREATE_TABLE_MODIFIERS = frozenset({
    "OR", "REPLACE", "GLOBAL", "LOCAL", "TEMPORARY", "TEMP", "UNLOGGED", "VOLATILE",
})
_TEMP_MODIFIERS = frozenset({"TEMPORARY", "TEMP", "VOLATILE"})
_OBJECT_WORDS = frozenset({
    "TABLE", "VIEW", "INDEX", "SEQUENCE", "FUNCTION", "PROCEDURE", "TRIGGER", "SCHEMA",
    "DATABASE", "TYPE", "ALIAS", "SYNONYM", "TABLESPACE", "EXTENSION", "DOMAIN", "ROLE", "USER",
    "EVENT", "BUFFERPOOL", "MATERIALIZED", "COLUMN", "PACKAGE", "MASK", "PERMISSION", "VARIABLE",
})


@dataclass
class DDLParseResult:
    """DDL 해석 결과.

    Attributes:
        schema: MCP `get_full_schema`와 같은 모양의 스키마(테이블·컬럼은 DDL 등장 순서)
        comments: 주석 — 키 ``"<테이블 키>"`` 또는 ``"<테이블 키>.<컬럼>"``(미리보기 표시용)
        warnings: 읽지 못한 정의·참조 사유
        skipped: 읽지 않은 문장 종류별 건수(예: ``{"CREATE INDEX": 12}``)
        statement_count: 문장 수(빈 문장 제외)
    """

    schema: SchemaInfo
    comments: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    statement_count: int = 0


# ──────────────────────────────────────────────
# 토큰화 · 문장 분리
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class _Tok:
    """토큰 — kind: word(인용 없는 단어·숫자) · ident(인용 식별자) · str(문자열) · punct · op."""

    kind: str
    text: str

    @property
    def word(self) -> str:
        """인용 없는 단어면 대문자, 아니면 빈 문자열(키워드 비교용)."""
        return self.text.upper() if self.kind == "word" else ""

    def is_punct(self, char: str) -> bool:
        return self.kind == "punct" and self.text == char


# 문장 시작의 `DELIMITER xx`(MariaDB 덤프 — 트리거·프로시저 본문용)
_DELIMITER_RE = re.compile(r"DELIMITER[ \t]+(\S+)[^\n]*", re.IGNORECASE)
# DB2 CLP 종결자 지시 주석(`--#SET TERMINATOR @`)
_DB2_TERMINATOR_RE = re.compile(r"--#SET\s+TERMINATOR\s+(\S+)", re.IGNORECASE)
_PUNCT = "(),.;[]"
_NEVER = r"(?!x)x"


@lru_cache(maxsize=len(DDL_ENGINES))
def _token_re(engine: str) -> re.Pattern[str]:
    """엔진별 토큰 정규식 — 문자열 이스케이프·주석·인용 식별자 규칙이 다르다."""
    if engine == "mariadb":
        string = r"'(?:[^'\\]|''|\\.)*(?:'|\Z)"
        quoted = r'"(?:[^"]|"")*(?:"|\Z)|`(?:[^`]|``)*(?:`|\Z)'
        comment = r"--[^\n]*|\#[^\n]*|/\*.*?(?:\*/|\Z)"
    else:
        string = r"'(?:[^']|'')*(?:'|\Z)"
        quoted = r'"(?:[^"]|"")*(?:"|\Z)'
        comment = r"--[^\n]*|/\*.*?(?:\*/|\Z)"
    dollar = _NEVER
    if engine == "postgresql":
        dollar = r"\$(?P<dtag>(?:[A-Za-z_]\w*)?)\$.*?\$(?P=dtag)\$"
    return re.compile(
        rf"(?P<ws>\s+)|(?P<comment>{comment})|(?P<dollar>{dollar})|(?P<str>{string})"
        rf"|(?P<quoted>{quoted})",
        re.DOTALL,
    )


@lru_cache(maxsize=len(DDL_ENGINES))
def _word_re(engine: str) -> re.Pattern[str]:
    """인용 없는 단어 — DB2·PG는 `#`·`$`를 식별자에 허용하고, MariaDB에서 `#`는 주석 시작이다."""
    return re.compile(r"[\w$]+" if engine == "mariadb" else r"[\w$#]+")


def _unquote(raw: str) -> str:
    quote = raw[0]
    body = raw[1:-1] if len(raw) >= 2 and raw[-1] == quote else raw[1:]
    return body.replace(quote * 2, quote)


def _unstring(raw: str, engine: str) -> str:
    body = raw[1:-1] if len(raw) >= 2 and raw.endswith("'") else raw[1:]
    if engine == "mariadb":
        body = re.sub(r"\\(.)", r"\1", body)
    return body.replace("''", "'")


def split_statements(text: str, engine: str) -> list[list[_Tok]]:
    """DDL 텍스트를 문장별 토큰 목록으로 나눈다(주석 제거 · 빈 문장 제외).

    Args:
        text: DDL 원문
        engine: `DDL_ENGINES` 중 하나

    Returns:
        문장마다 토큰 목록
    """
    pattern = _token_re(engine)
    word_re = _word_re(engine)
    delimiter = ";"
    statements: list[list[_Tok]] = []
    current: list[_Tok] = []
    i, n = 0, len(text)
    while i < n:
        if not current:
            ws = pattern.match(text, i)
            start = ws.end() if ws is not None and ws.lastgroup == "ws" else i
            directive = _DELIMITER_RE.match(text, start)
            if directive is not None:
                delimiter = directive.group(1)
                i = directive.end()
                continue
        if text.startswith(delimiter, i):
            if current:
                statements.append(current)
                current = []
            i += len(delimiter)
            continue
        match = pattern.match(text, i)
        if match is not None:
            kind = match.lastgroup
            raw = match.group(0)
            if kind == "str":
                current.append(_Tok("str", _unstring(raw, engine)))
            elif kind == "dollar":
                current.append(_Tok("str", raw))
            elif kind == "quoted":
                current.append(_Tok("ident", _unquote(raw)))
            elif kind == "comment" and engine == "db2":
                terminator = _DB2_TERMINATOR_RE.match(raw)
                if terminator is not None:
                    delimiter = terminator.group(1)
            i = match.end()
            continue
        word = word_re.match(text, i)
        if word is not None:
            current.append(_Tok("word", word.group(0)))
            i = word.end()
            continue
        char = text[i]
        current.append(_Tok("punct" if char in _PUNCT else "op", char))
        i += 1
    if current:
        statements.append(current)
    return statements


# ──────────────────────────────────────────────
# 커서 · 공용 조각
# ──────────────────────────────────────────────


class _Cursor:
    def __init__(self, toks: Sequence[_Tok]) -> None:
        self.toks = toks
        self.i = 0

    def peek(self, offset: int = 0) -> _Tok | None:
        index = self.i + offset
        return self.toks[index] if index < len(self.toks) else None

    def done(self) -> bool:
        return self.i >= len(self.toks)

    def next(self) -> _Tok | None:
        tok = self.peek()
        if tok is not None:
            self.i += 1
        return tok

    def at_words(self, *words: str) -> bool:
        return all(
            (tok := self.peek(k)) is not None and tok.word == word for k, word in enumerate(words)
        )

    def take_words(self, *words: str) -> bool:
        if self.at_words(*words):
            self.i += len(words)
            return True
        return False

    def take_name(self) -> list[_Tok] | None:
        """`a` · `"a"."b"` · `a.b.c` 같은 점 이음 이름."""
        first = self.peek()
        if first is None or first.kind not in ("word", "ident"):
            return None
        parts = [first]
        self.i += 1
        while True:
            dot, part = self.peek(), self.peek(1)
            if (
                dot is not None and dot.is_punct(".")
                and part is not None and part.kind in ("word", "ident")
            ):
                parts.append(part)
                self.i += 2
            else:
                return parts

    def take_group(self) -> list[_Tok] | None:
        """`( … )` 하나를 소비하고 안쪽 토큰을 돌려준다(다음이 `(`가 아니면 None)."""
        tok = self.peek()
        if tok is None or not tok.is_punct("("):
            return None
        depth, start = 0, self.i
        while self.i < len(self.toks):
            current = self.toks[self.i]
            self.i += 1
            if current.is_punct("("):
                depth += 1
            elif current.is_punct(")"):
                depth -= 1
                if depth == 0:
                    return list(self.toks[start + 1:self.i - 1])
        return list(self.toks[start + 1:])


def _split_commas(toks: Sequence[_Tok]) -> list[list[_Tok]]:
    """괄호 깊이 0의 쉼표로 나눈다(빈 조각 제외)."""
    parts: list[list[_Tok]] = [[]]
    depth = 0
    for tok in toks:
        if tok.is_punct("("):
            depth += 1
        elif tok.is_punct(")"):
            depth -= 1
        elif tok.is_punct(",") and depth == 0:
            parts.append([])
            continue
        parts[-1].append(tok)
    return [part for part in parts if part]


def _statement_kind(toks: Sequence[_Tok]) -> str:
    """문장 종류 표기(건수 집계용) — 예: ``CREATE INDEX`` · ``GRANT``."""
    first = toks[0].word or toks[0].text
    if first not in ("CREATE", "ALTER", "DROP", "COMMENT"):
        return first or "(알 수 없음)"
    temporary = False
    for tok in toks[1:8]:
        word = tok.word
        if word in _TEMP_MODIFIERS:
            temporary = True
        if word in _OBJECT_WORDS:
            if word == "MATERIALIZED":
                word = "MATERIALIZED VIEW"
            if word == "TABLE" and temporary:
                word = "TEMPORARY TABLE"
            return f"{first} {word}"
    return first


# ──────────────────────────────────────────────
# 해석기
# ──────────────────────────────────────────────


@dataclass
class _Column:
    name: str
    type: str
    nullable: bool = True


@dataclass
class _ForeignKey:
    columns: list[str]
    target_parts: list[_Tok]
    target_columns: list[str]
    owner: str


@dataclass
class _Table:
    key: str
    schema: str
    columns: dict[str, _Column] = field(default_factory=dict)
    pk: list[str] = field(default_factory=list)
    fks: list[_ForeignKey] = field(default_factory=list)


class _DDLParser:
    def __init__(self, engine: str, default_schema: str | None) -> None:
        self.engine = engine
        self.default_schema = default_schema or None
        self.tables: dict[str, _Table] = {}
        self.comments: dict[str, str] = {}
        self.warnings: list[str] = []
        self.skipped: Counter[str] = Counter()

    # --- 이름 ---

    def fold(self, tok: _Tok) -> str:
        """식별자 정규화 — 인용 식별자는 그대로, 인용 없는 이름은 엔진 규칙으로 접는다.

        DB2 인용 식별자는 뒤 공백을 뗀다 — db2look은 스키마명을 8자로 채워(`"APP     "`) 내보내고
        MCP 서버는 `TRIM(TABSCHEMA)`로 돌려준다.
        """
        if self.engine == "db2":
            return tok.text.rstrip() if tok.kind != "word" else tok.text.upper()
        if tok.kind != "word":
            return tok.text
        if self.engine == "postgresql":
            return tok.text.lower()
        return tok.text

    def table_key(self, parts: Sequence[_Tok]) -> tuple[str, str]:
        """이름 조각 → (테이블 키, 스키마명) — MCP `search_objects`의 키 규칙."""
        table = self.fold(parts[-1])
        qualifier = self.fold(parts[-2]) if len(parts) >= 2 else None
        if self.engine == "postgresql":
            schema = qualifier or "public"
            return (table if schema == "public" else f"{schema}.{table}"), schema
        if self.engine == "db2":
            return table, qualifier or self.default_schema or ""
        default = self.default_schema
        if qualifier is None or default is None or qualifier.casefold() == default.casefold():
            return table, qualifier or default or ""
        return f"{qualifier}.{table}", qualifier

    def find_table(self, parts: Sequence[_Tok], near: str | None = None) -> str:
        """정의된 테이블 키를 찾는다 — 없으면 키 규칙대로 만든 이름.

        한정자 없는 참조는 참조하는 테이블(`near`)과 같은 스키마를 먼저 본다
        (MCP 관계 파생과 같은 순서).
        """
        key, _schema = self.table_key(parts)
        candidates = [key]
        if len(parts) == 1 and near and "." in near:
            candidates.insert(0, f"{near.rsplit('.', 1)[0]}.{key}")
        for candidate in candidates:
            if candidate in self.tables:
                return candidate
        for candidate in candidates:
            matches = [k for k in self.tables if k.casefold() == candidate.casefold()]
            if len(matches) == 1:
                return matches[0]
        return candidates[-1]

    def column_names(self, toks: Sequence[_Tok]) -> list[str]:
        """`(a, b DESC, c(10))` 안쪽 → 컬럼명 목록(각 조각의 첫 이름)."""
        names: list[str] = []
        for part in _split_commas(toks):
            head = part[0]
            if head.kind in ("word", "ident"):
                names.append(self.fold(head))
        return names

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    # --- 문장 ---

    def feed(self, toks: Sequence[_Tok]) -> None:
        kind = _statement_kind(toks)
        if kind == "CREATE TABLE":
            self.create_table(toks)
        elif kind == "ALTER TABLE":
            self.alter_table(toks)
        elif kind in ("COMMENT TABLE", "COMMENT COLUMN") or (
            kind == "COMMENT" and len(toks) > 1 and toks[1].word == "ON"
        ):
            self.comment_on(toks)
        else:
            self.skipped[kind] += 1

    def create_table(self, toks: Sequence[_Tok]) -> None:
        cur = _Cursor(toks)
        cur.next()  # CREATE
        while (tok := cur.peek()) is not None and tok.word in _CREATE_TABLE_MODIFIERS:
            cur.next()
        cur.take_words("TABLE")
        cur.take_words("IF", "NOT", "EXISTS")
        parts = cur.take_name()
        if parts is None:
            self.warn(f"테이블 이름을 읽지 못한 CREATE TABLE: {_head(toks)}")
            return
        key, schema = self.table_key(parts)
        body = cur.take_group()
        if body is None:
            self.warn(
                f"열 목록이 없는 CREATE TABLE(AS·LIKE·PARTITION OF 등)은 읽지 않습니다: {key}"
            )
            return
        if key in self.tables:
            self.warn(f"같은 테이블이 다시 정의되어 뒤 정의를 씁니다: {key}")
        table = _Table(key=key, schema=schema)
        self.tables[key] = table
        elements = _split_commas(body)
        # 컬럼을 먼저 모은 뒤 제약을 적용한다 — 본문 안 제약이 컬럼보다 앞에 올 수 있다
        constraints = [e for e in elements if e[0].word in _CONSTRAINT_STARTS]
        for element in elements:
            if element[0].word not in _CONSTRAINT_STARTS:
                self.add_column(table, element)
        for element in constraints:
            self.add_constraint(table, element)
        self.table_options(table, cur)

    def table_options(self, table: _Table, cur: _Cursor) -> None:
        """본문 뒤 테이블 옵션 — MariaDB `COMMENT[=]'…'`만 읽고, PG `INHERITS`는 경고한다."""
        while not cur.done():
            tok = cur.next()
            if tok is not None and tok.word == "INHERITS":
                self.warn(f"INHERITS로 상속한 컬럼은 읽지 않습니다: {table.key}")
            elif tok is not None and tok.word == "COMMENT":
                if (eq := cur.peek()) is not None and eq.text == "=":
                    cur.next()
                text = cur.peek()
                if text is not None and text.kind == "str":
                    self.comments[table.key] = text.text
                    cur.next()
            elif tok is not None and tok.is_punct("("):
                cur.i -= 1
                cur.take_group()

    def add_column(self, table: _Table, element: Sequence[_Tok]) -> None:
        cur = _Cursor(element)
        cur.take_words("COLUMN")
        cur.take_words("IF", "NOT", "EXISTS")
        name_tok = cur.next()
        if name_tok is None or name_tok.kind not in ("word", "ident"):
            self.warn(f"컬럼 정의를 읽지 못했습니다({table.key}): {_head(element)}")
            return
        name = self.fold(name_tok)
        type_text = self.take_type(cur)
        if type_text is None:
            self.warn(f"컬럼 타입을 읽지 못했습니다: {table.key}.{name}")
            return
        if name in table.columns:
            self.warn(f"같은 컬럼이 다시 정의되어 앞 정의를 씁니다: {table.key}.{name}")
            return
        column = _Column(name=name, type=type_text)
        table.columns[name] = column
        self.column_attributes(table, column, cur)

    def column_attributes(self, table: _Table, column: _Column, cur: _Cursor) -> None:
        """컬럼 속성 — NOT NULL · NULL · PRIMARY KEY · REFERENCES · COMMENT만 읽는다."""
        previous = ""
        while not cur.done():
            tok = cur.peek()
            if tok is not None and tok.is_punct("("):
                cur.take_group()
                previous = ""
                continue
            cur.next()
            word = tok.word if tok is not None else ""
            if word == "NULL" and previous not in ("NOT", "DEFAULT"):
                column.nullable = True
            elif word == "NULL" and previous == "NOT":
                column.nullable = False
            elif word == "PRIMARY" and cur.take_words("KEY"):
                if column.name not in table.pk:
                    table.pk.append(column.name)
            elif word == "REFERENCES":
                self.references(table, [column.name], cur)
            elif word == "COMMENT":
                text = cur.peek()
                if text is not None and text.kind == "str":
                    self.comments[f"{table.key}.{column.name}"] = text.text
                    cur.next()
            previous = word

    def references(self, table: _Table, columns: list[str], cur: _Cursor) -> None:
        """`REFERENCES name [(cols)]` — 대상 컬럼이 없으면 대상 PK로 나중에 푼다."""
        parts = cur.take_name()
        if parts is None:
            self.warn(f"REFERENCES 대상을 읽지 못했습니다: {table.key}({', '.join(columns)})")
            return
        group = cur.take_group()
        target_columns = self.column_names(group) if group is not None else []
        table.fks.append(_ForeignKey(columns, parts, target_columns, table.key))

    def add_constraint(self, table: _Table, element: Sequence[_Tok]) -> None:
        """본문·ALTER의 제약 — PRIMARY KEY · FOREIGN KEY만 반영(UNIQUE·CHECK·인덱스는 무시)."""
        cur = _Cursor(element)
        if cur.take_words("CONSTRAINT"):
            cur.take_name()
        if cur.take_words("PRIMARY", "KEY"):
            while (tok := cur.peek()) is not None and not tok.is_punct("("):
                cur.next()  # MariaDB `USING BTREE` 등
            group = cur.take_group()
            names = self.column_names(group or [])
            unknown = [n for n in names if n not in table.columns]
            if unknown or not names:
                self.warn(f"PRIMARY KEY 컬럼을 찾지 못했습니다: {table.key}({', '.join(unknown)})")
            table.pk = [n for n in names if n in table.columns]
        elif cur.take_words("FOREIGN", "KEY"):
            while (tok := cur.peek()) is not None and not tok.is_punct("("):
                cur.next()  # MariaDB `FOREIGN KEY 인덱스명 (…)`
            names = self.column_names(cur.take_group() or [])
            if not cur.take_words("REFERENCES") or not names:
                self.warn(f"FOREIGN KEY를 읽지 못했습니다: {table.key} — {_head(element)}")
                return
            self.references(table, names, cur)
        elif element[0].word == "LIKE":
            self.warn(f"LIKE로 복사한 컬럼은 읽지 않습니다: {table.key}")

    def alter_table(self, toks: Sequence[_Tok]) -> None:
        cur = _Cursor(toks)
        cur.take_words("ALTER", "TABLE")
        cur.take_words("IF", "EXISTS")
        cur.take_words("ONLY")
        parts = cur.take_name()
        if parts is None:
            self.warn(f"테이블 이름을 읽지 못한 ALTER TABLE: {_head(toks)}")
            return
        key = self.find_table(parts)
        rest = list(toks[cur.i:])
        for clause in _split_commas(rest):
            if clause[0].word != "ADD":
                self.skipped["ALTER TABLE (그 밖의 변경)"] += 1
                continue
            body = clause[1:]
            if not body:
                continue
            table = self.tables.get(key)
            if table is None:
                self.warn(f"정의되지 않은 테이블에 대한 ALTER TABLE ADD: {key}")
                return
            if body[0].word in _ADD_NON_COLUMN:
                self.add_constraint(table, body)
            else:
                self.add_column(table, body)

    def comment_on(self, toks: Sequence[_Tok]) -> None:
        """`COMMENT ON TABLE|COLUMN name IS '…'` · DB2 `COMMENT ON name (col IS '…', …)`."""
        cur = _Cursor(toks)
        cur.take_words("COMMENT", "ON")
        if cur.take_words("TABLE"):
            parts = cur.take_name()
            if parts and cur.take_words("IS") and (text := cur.peek()) and text.kind == "str":
                self.comments[self.find_table(parts)] = text.text
            return
        if cur.take_words("COLUMN"):
            parts = cur.take_name()
            if parts and len(parts) >= 2 and cur.take_words("IS") and (text := cur.peek()) \
                    and text.kind == "str":
                table_key = self.find_table(parts[:-1])
                self.comments[f"{table_key}.{self.fold(parts[-1])}"] = text.text
            return
        parts = cur.take_name()
        group = cur.take_group()
        if parts is None or group is None:
            self.skipped["COMMENT ON (그 밖의 대상)"] += 1
            return
        table_key = self.find_table(parts)
        for part in _split_commas(group):
            if len(part) >= 3 and part[1].word == "IS" and part[2].kind == "str":
                self.comments[f"{table_key}.{self.fold(part[0])}"] = part[2].text

    # --- 타입 ---

    def take_type(self, cur: _Cursor) -> str | None:
        """컬럼 타입을 소비해 MCP 표기로 돌려준다(길이·정밀도 제외)."""
        parts = cur.take_name()
        if parts is None:
            return None
        if len(parts) > 1 or parts[0].kind == "ident":
            # 스키마 한정·인용된 타입 = 사용자 정의 타입
            self.skip_type_suffix(cur)
            if self.engine == "postgresql":
                return "USER-DEFINED"
            return ".".join(self.fold(p) for p in parts)
        words = [parts[0].text.lower()]
        while True:
            if cur.take_group() is not None:
                continue
            extra = self.continuation(tuple(words), cur)
            if not extra:
                break
            words.extend(extra)
        is_array = self.skip_type_suffix(cur)
        return self.canonical(" ".join(words), is_array=is_array)

    @staticmethod
    def continuation(words: tuple[str, ...], cur: _Cursor) -> list[str]:
        if words[0] == "interval":
            tok = cur.peek()
            if tok is not None and tok.word.lower() in _INTERVAL_FIELDS:
                cur.next()
                return [tok.word.lower()]
            return []
        for option in _TYPE_CONTINUATIONS.get(words, ()):
            if cur.at_words(*(w.upper() for w in option)):
                cur.i += len(option)
                return list(option)
        return []

    def skip_type_suffix(self, cur: _Cursor) -> bool:
        """PG 배열 접미사(`[]` · `[n]` · `ARRAY`)를 소비하고 배열이었는지 돌려준다."""
        is_array = False
        while True:
            tok = cur.peek()
            if tok is not None and tok.is_punct("["):
                while (inner := cur.next()) is not None and not inner.is_punct("]"):
                    pass
                is_array = True
            elif tok is not None and tok.word == "ARRAY" and self.engine == "postgresql":
                cur.next()
                is_array = True
            else:
                return is_array

    def canonical(self, raw: str, *, is_array: bool) -> str:
        if self.engine == "postgresql":
            if is_array:
                return "ARRAY"
            if raw.startswith("interval"):
                return "interval"
            if raw in _PG_TYPES:
                return _PG_TYPES[raw]
            return raw if raw in _PG_BUILTIN else "USER-DEFINED"
        if self.engine == "db2":
            return _DB2_TYPES.get(raw, raw.upper())
        return _MARIADB_TYPES.get(raw, raw)

    # --- 결과 ---

    def build(self) -> SchemaInfo:
        """MCP `get_full_schema`와 같은 모양으로 조립한다(FK 해석 · 관계 파생)."""
        for key in [k for k, t in self.tables.items() if not t.columns]:
            self.warn(f"컬럼을 하나도 읽지 못한 테이블은 등록에서 뺍니다: {key}")
            del self.tables[key]
        schema = SchemaInfo()
        references: dict[str, dict[str, str]] = {}
        relationships: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for table in self.tables.values():
            for fk in table.fks:
                for column, target, to_column in self.resolve_fk(table, fk):
                    # 컬럼 `references`는 MCP처럼 대상 테이블 이름(한정자 없음)
                    # · 같은 컬럼이면 마지막 것
                    bare = target.rsplit(".", 1)[-1]
                    references.setdefault(table.key, {})[column] = f"{bare}.{to_column}"
                    pair = (f"{table.key}.{column}", f"{target}.{to_column}")
                    if pair not in seen:
                        seen.add(pair)
                        relationships.append({"from": pair[0], "to": pair[1]})
        for table in self.tables.values():
            fk_map = references.get(table.key, {})
            schema.tables[table.key] = TableInfo(
                name=table.key,
                schema_name=table.schema,
                columns=[
                    ColumnInfo(
                        name=column.name,
                        data_type=column.type,
                        nullable=column.nullable and column.name not in table.pk,
                        is_primary_key=column.name in table.pk,
                        is_foreign_key=column.name in fk_map,
                        references=fk_map.get(column.name),
                    )
                    for column in table.columns.values()
                ],
            )
        schema.relationships = relationships
        return schema

    def resolve_fk(self, table: _Table, fk: _ForeignKey) -> list[tuple[str, str, str]]:
        target = self.find_table(fk.target_parts, near=table.key)
        target_table = self.tables.get(target)
        label = f"{table.key}({', '.join(fk.columns)}) → {target}"
        if target_table is None:
            self.warn(f"참조 대상 테이블이 DDL에 없습니다: {label}")
        to_columns = fk.target_columns or (list(target_table.pk) if target_table else [])
        if not to_columns:
            if target_table is not None:
                self.warn(f"참조 컬럼을 정할 수 없어(대상 PK 없음) 관계를 만들지 않습니다: {label}")
            return []
        if len(to_columns) != len(fk.columns):
            self.warn(f"참조 컬럼 수가 맞지 않아 관계를 만들지 않습니다: {label}")
            return []
        missing = [c for c in fk.columns if c not in table.columns]
        if missing:
            self.warn(f"FOREIGN KEY 컬럼이 테이블에 없습니다: {table.key}({', '.join(missing)})")
            return []
        return [(c, target, t) for c, t in zip(fk.columns, to_columns)]


def _head(toks: Sequence[_Tok], limit: int = 12) -> str:
    """경고 문구용 문장 머리(토큰 몇 개)."""
    text = " ".join(t.text for t in toks[:limit])
    return text + (" …" if len(toks) > limit else "")


def parse_ddl(text: str, engine: str, *, default_schema: str | None = None) -> DDLParseResult:
    """DDL 텍스트를 해석해 MCP 수집과 같은 모양의 스키마를 만든다.

    Args:
        text: DDL 원문(여러 문장)
        engine: `DDL_ENGINES` 중 하나 — 식별자 대소문자·타입 표기·문자열 이스케이프가 갈린다
        default_schema: 한정자 없는 테이블의 스키마(DB2 `TABSCHEMA` · MariaDB 기본 database).
            PostgreSQL은 무시한다(`public`)

    Returns:
        해석 결과(테이블이 0개여도 돌려준다 — 판단은 호출자)

    Raises:
        ValueError: 지원하지 않는 엔진
    """
    if engine not in DDL_ENGINES:
        raise ValueError(f"engine은 {', '.join(DDL_ENGINES)} 중 하나여야 합니다: {engine!r}")
    parser = _DDLParser(engine, default_schema)
    statements = split_statements(text, engine)
    for toks in statements:
        parser.feed(toks)
    schema = parser.build()
    warnings = parser.warnings
    if len(warnings) > MAX_WARNINGS:
        warnings = [*warnings[:MAX_WARNINGS], f"… 외 경고 {len(warnings) - MAX_WARNINGS}건"]
    return DDLParseResult(
        schema=schema,
        comments=parser.comments,
        warnings=warnings,
        skipped=dict(parser.skipped.most_common()),
        statement_count=len(statements),
    )
