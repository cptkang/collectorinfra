"""지식 자산 원천 파일 — 형식 파싱 · 결정적 정적 검사 · 정의 파생 쿼리 규칙.

plans/143 W2 · D-316 ①②.

**원천 파일 계약**(Claude Code 스킬·검증 CLI·빌더가 함께 쓴다). 디렉터리
`testdata/itam_bench/closed/knowledge/` · 파일 6종. 모든 파일 머리는 ``version: 1``이고 ``items``
목록을 둔다. 항목 공통 칸:

| 칸 | 규칙 |
|---|---|
| `id` | 파일 안에서 유일한 비지 않은 문자열 |
| `origin` | `claude_code` |
| `evidence` | 근거 반출 run ID(비지 않은 문자열) |
| `status` | `active` · `withdrawn` — 철회 항목은 검증·빌드에서 빠지되 파일에 남는다(이력 보존) |
| `reason` | `withdrawn`이면 필수 |

| 파일 | 자산 | 항목 칸 | 산출 |
|---|---|---|---|
| `guide.yaml` | K1 | `text` | 통과한 active `text`를 순서대로 줄바꿈 연결 → `query_guide` |
| `examples.yaml` | K2 | `question`·`sql`·`description`·`tables` | `query_examples` + 이력 시드 |
| `descriptions.yaml` | K3 | `table`·`column`·`text` | 설명 정본 파일 |
| `synonyms.yaml` | K3 | `table`·`column`·`words`(목록) | 유사어 시드 `column_synonyms` |
| `prompt_section.yaml` | K4 | `text` | 통과한 active 항목 연결 → DB 전용 규칙 섹션 |
| `query_templates.yaml` | K8 | 별도 모듈(`query_templates`)이 정한다 | 조립 템플릿 |

- K2의 `description`은 프롬프트 예시 블록이 읽는 칸 이름(`explanation`)과 다르다 — 빌더가 옮길 때
  바꿔 쓴다.
- K6(데이터 기반 쿼리 규칙)은 원천 파일이 없다 — `derive_kind_rules`가 정의 `kind`에서 파생한다.

**정적 검사**(이 모듈 · I/O 0): 공통 칸 · 길이 · 중괄호·코드 펜스 · 언급 식별자 실존(백틱 ·
`table.column` · 테이블 이름형 토큰) · 조회 대상 밖 테이블 · 사용률을 이 DB에서 답하라는
규칙(D-308 G-6 — 사용률 정본은 관측 DB) · 설명의 값(따옴표 리터럴 · 코드 열거 · 긴 숫자열) ·
유사어 쓰기 가드(2자 미만 · 다른 컬럼 이름과 같음 · 같은 말이 다른 컬럼에도 걸림 — D-142 ·
사용률 낱말·컬럼). SQL
검사·DB 실행은 호출부가 더한다. 근거 run 리터럴 대조 집합(`sql_literals` ·
`evidence_literal_values`)도 여기서 만들고, 원천 파일 대조는 호출부가 한다.

계층: domain — 순수 함수 · I/O·LLM 0 · 스키마 리터럴 0.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.domain import query_templates as qt
from src.domain.schema_snapshot import bare_name
from src.domain.table_definitions import KIND_COLLECT_LOAD, KINDS, ORIGINS

VERSION = 1
ORIGIN = "claude_code"
STATUS_ACTIVE = "active"
STATUS_WITHDRAWN = "withdrawn"

GUIDE_FILE = "guide.yaml"
EXAMPLES_FILE = "examples.yaml"
DESCRIPTIONS_FILE = "descriptions.yaml"
SYNONYMS_FILE = "synonyms.yaml"
SECTION_FILE = "prompt_section.yaml"
TEMPLATES_FILE = "query_templates.yaml"
#: 검증 순서(K8은 별도 검증기)
KNOWLEDGE_FILES: tuple[str, ...] = (
    GUIDE_FILE, EXAMPLES_FILE, DESCRIPTIONS_FILE, SYNONYMS_FILE, SECTION_FILE, TEMPLATES_FILE,
)
#: 파일별 필수 항목 칸 → 형식(`str` 비지 않은 문자열 · `list` 비지 않은 문자열 목록)
ITEM_FIELDS: Mapping[str, Mapping[str, str]] = {
    GUIDE_FILE: {"text": "str"},
    EXAMPLES_FILE: {"question": "str", "sql": "str", "description": "str", "tables": "list"},
    DESCRIPTIONS_FILE: {"table": "str", "column": "str", "text": "str"},
    SYNONYMS_FILE: {"table": "str", "column": "str", "words": "list"},
    SECTION_FILE: {"text": "str"},
}

GUIDE_MAX_CHARS = 8000
DESCRIPTION_MAX_CHARS = 200
SYNONYM_MIN_CHARS = 2

# 문제 코드(짧은 열거)
FILE_INVALID = "file_invalid"
DUPLICATE_ID = "duplicate_id"
MISSING_FIELD = "missing_field"
BAD_FIELD = "bad_field"
BAD_ORIGIN = "bad_origin"
BAD_STATUS = "bad_status"
MISSING_REASON = "missing_reason"
TOO_LONG = "too_long"
BRACES = "braces"
CODE_FENCE = "code_fence"
UNKNOWN_IDENTIFIER = "unknown_identifier"
TABLE_NOT_ALLOWED = "table_not_allowed"
UTILIZATION_RULE = "utilization_rule"
VALUE_LITERAL = "value_literal"
UNKNOWN_COLUMN = "unknown_column"
TOO_SHORT = "too_short"
NAME_CONFLICT = "name_conflict"
AMBIGUOUS = "ambiguous"
DUPLICATE = "duplicate"
#: 근거 run 리터럴(실행 SQL 리터럴 · 주석·정의 글 코드 열거 값)이 원천 파일에 나온다
EVIDENCE_LITERAL = "evidence_literal"
#: 근거 리터럴 최소 길이 — 더 짧은 값(`Y`·`01`·`OS`·두 자리 이하 수)은 평문 낱말·수와 우연히
#: 겹칠 뿐 내부망 값을 드러내지 못한다
EVIDENCE_LITERAL_MIN_CHARS = 3

_FENCES: tuple[str, ...] = ("```", "~~~")
_FENCE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_BACKTICK_RE = re.compile(r"`([^`\n]{1,128})`")
_IDENT = r"[A-Za-z_가-힣][A-Za-z0-9_$#가-힣]*"
_IDENT_RE = re.compile(rf"^{_IDENT}(?:\.{_IDENT})?$")
_IDENT_TOKEN_RE = re.compile(rf"{_IDENT}(?:\.{_IDENT})?")
# 백틱 밖 `table.column` — 왼쪽은 라틴 식별자(테이블 이름형일 때만 대조한다)
_DOTTED_RE = re.compile(rf"(?<![A-Za-z0-9_.`])([A-Za-z_][A-Za-z0-9_]*)\.({_IDENT})")
_QUOTED_RE = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|‘[^’\n]*’|“[^”\n]*”")
_ALPHA_PREFIX_RE = re.compile(r"[A-Za-z]+")
# 백틱 SQL 조각 안에서 식별자 대조에서 빼는 낱말(키워드·자주 쓰는 함수)
_SQL_WORDS = frozenset({
    "select", "from", "where", "join", "left", "right", "inner", "outer", "on", "and", "or",
    "not", "in", "is", "null", "like", "between", "group", "by", "order", "having", "limit",
    "fetch", "first", "rows", "only", "distinct", "as", "case", "when", "then", "else", "end",
    "count", "sum", "max", "min", "avg", "upper", "lower", "trim", "cast", "substr",
    "substring", "concat", "coalesce", "ifnull", "nvl", "exists", "asc", "desc", "union",
    "all", "date_format", "str_to_date", "now", "curdate", "current_date", "interval", "day",
    "month", "year", "datediff", "date_add", "date_sub", "round", "length", "char_length",
    "replace", "if", "char", "varchar", "signed", "unsigned", "decimal", "numeric", "date",
})
# 사용률 — 관측 DB 정본(D-308 G-6). 사용률을 말한 절(clause)에 부정·관측 DB 안내가 함께 있어야
# 허용한다(같은 문장의 다른 절 부정어 — 「…로 답하고 NULL 행은 세지 않는다」 — 로는 풀리지 않는다)
_UTILIZATION_RE = re.compile(
    r"(?i)사용률|utili[sz]ation|(?:cpu|메모리|디스크|memory|disk)\s*사용량"
)
_REDIRECT_RE = re.compile(
    r"않|지\s*말|말\s*것|말라|금지|아니|없다|관측\s*DB|관측\s*데이터|범위\s*밖"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n+")
# 절 경계 — 연결 어미(-고·-며·-면서·-지만·-는데·-으나·-거나) 뒤 공백 · 대시
_CLAUSE_SPLIT_RE = re.compile(r"(?:고|며|면서|지만|는데|으나|거나)\s+|\s[—–-]\s|[—–]")
# 설명의 값 — 코드 열거(`1:정상` · `Y=사용`) · 긴 숫자열
_CODE_PAIR_RE = re.compile(r"(?<![A-Za-z0-9가-힣])[A-Za-z0-9]{1,4}\s*[:=]\s*[가-힣]")
_LONG_DIGITS_RE = re.compile(r"\d{5,}")
# K6 — 활성 여부 칸 · 날짜 문자열 기준일 칸(이름 단서)
_ACTIVE_FLAG_RE = re.compile(r"^활성화?여부$")
_BASE_DATE_RE = re.compile(r"^기준(년월일|년월|일자)$")
_STRING_TYPE_RE = re.compile(r"(?i)^(var)?char|^n?varchar|^text|^string")
# 근거 리터럴 — SQL 토큰(주석 · 문자열 · 인용 식별자 · 식별자 · 수 · 그 밖 한 글자)
_SQL_LITERAL_TOKEN_RE = re.compile(
    r"--[^\n]*|/\*.*?\*/|'(?:[^'\\]|\\.|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`"
    r"|[A-Za-z_가-힣][A-Za-z0-9_$#가-힣]*|\d+(?:\.\d+)?|\S",
    re.DOTALL,
)
#: 바로 뒤 숫자가 행 수 제한인 낱말(`LIMIT n[, m]` · `OFFSET n` · `FETCH FIRST|NEXT n` · `TOP n`)
_ROW_LIMIT_WORDS = frozenset({"limit", "offset", "first", "next", "top"})
#: 근거 리터럴에서 빼는 고정 열거(정의 `kind`·`origin` · 원천 공통 칸 · K8 슬롯 형식·종류)
_FIXED_ENUMS = frozenset(
    v.casefold()
    for v in (
        *KINDS, *ORIGINS, "claude_code", "active", "withdrawn",
        *qt.SLOT_TYPES, qt.FORMAT_YYYYMMDD, qt.FORMAT_ISO,
    )
)


def issue(code: str, message: str) -> dict[str, str]:
    """문제 하나 — ``{code, message}``."""
    return {"code": code, "message": message}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text)).strip()


# ──────────────────────────────────────────────
# 대조 기준(카탈로그 · 조회 대상)
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class Catalog:
    """식별자 대조 기준 — 테이블(맨 이름 소문자 → 원 이름) · 테이블별 컬럼(casefold) · 전체
    컬럼 이름(casefold → 원 이름) · 조회 대상(맨 이름 소문자) · 테이블 이름형 토큰 패턴."""

    tables: Mapping[str, str]
    columns: Mapping[str, frozenset[str]]
    column_names: Mapping[str, str]
    allowed: frozenset[str]
    table_token: re.Pattern[str] | None = None


def _table_token_pattern(names: Iterable[str]) -> re.Pattern[str] | None:
    """테이블 이름형 토큰 — 테이블 이름 영문 접두의 공통 앞부분(3자 이상) + 숫자.

    공통 앞부분이 짧으면 접두 목록을 쓴다. 영문 접두 + 숫자 꼴 이름이 없으면 None(백틱·
    `table.column` 표기만 대조).
    """
    prefixes = sorted({
        m.group(0).lower()
        for name in names
        if (m := _ALPHA_PREFIX_RE.match(name)) and name[m.end():m.end() + 1].isdigit()
    })
    if not prefixes:
        return None
    common = prefixes[0]
    for prefix in prefixes[1:]:
        while not prefix.startswith(common):
            common = common[:-1]
    head = re.escape(common) + "[A-Za-z]*" if len(common) >= 3 else (
        "(?:" + "|".join(re.escape(p) for p in prefixes) + ")"
    )
    return re.compile(rf"(?i)(?<![A-Za-z0-9_])({head}\d+[A-Za-z0-9_]*)(?![A-Za-z0-9_])")


def make_catalog(columns: Mapping[str, Sequence[str]], allowed: Iterable[str]) -> Catalog:
    """``{테이블: [컬럼…]}`` · 조회 대상 → 대조 기준."""
    tables = {bare_name(str(t)).lower(): bare_name(str(t)) for t in columns}
    per_table = {
        bare_name(str(t)).lower(): frozenset(_norm(c).casefold() for c in cols)
        for t, cols in columns.items()
    }
    names: dict[str, str] = {}
    for cols in columns.values():
        for c in cols:
            names.setdefault(_norm(c).casefold(), str(c))
    return Catalog(
        tables=tables,
        columns=per_table,
        column_names=names,
        allowed=frozenset(bare_name(str(t)).lower() for t in allowed),
        table_token=_table_token_pattern(tables.values()),
    )


# ──────────────────────────────────────────────
# 파일 파싱 · 공통 칸
# ──────────────────────────────────────────────


@dataclass
class Entry:
    """원천 항목 하나 — 결과 표지(`label` = id 또는 ``#순번``) · 항목 · 공통 칸 문제 · 철회 여부."""

    label: str
    item: dict[str, Any]
    issues: list[dict[str, str]] = field(default_factory=list)
    withdrawn: bool = False


@dataclass
class ParsedFile:
    """파싱 결과 — 파일 단위 문제(있으면 항목 없음) · 항목 목록."""

    name: str
    file_issues: list[dict[str, str]] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)

    @property
    def active(self) -> list[Entry]:
        return [e for e in self.entries if not e.withdrawn]


def _field_issues(item: Mapping[str, Any], spec: Mapping[str, str]) -> list[dict[str, str]]:
    out = []
    for name, kind in spec.items():
        value = item.get(name)
        if value is None or value == "" or value == []:
            out.append(issue(MISSING_FIELD, f"`{name}` 칸이 비었습니다"))
        elif kind == "str" and not isinstance(value, str):
            out.append(issue(BAD_FIELD, f"`{name}`은 문자열이어야 합니다"))
        elif kind == "list" and not (
            isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
        ):
            out.append(issue(BAD_FIELD, f"`{name}`은 비지 않은 문자열 목록이어야 합니다"))
    return out


def parse_file(name: str, document: Any) -> ParsedFile:
    """원천 파일 문서 → 항목 목록과 공통 칸 문제. 형식이 틀린 파일은 파일 단위 문제만 낸다."""
    parsed = ParsedFile(name=name)
    if not isinstance(document, Mapping):
        parsed.file_issues.append(issue(FILE_INVALID, "최상위가 매핑이 아닙니다"))
        return parsed
    if document.get("version") != VERSION:
        parsed.file_issues.append(issue(FILE_INVALID, f"`version: {VERSION}`이어야 합니다"))
    items = document.get("items")
    if not isinstance(items, list):
        parsed.file_issues.append(issue(FILE_INVALID, "`items`가 목록이 아닙니다"))
    if parsed.file_issues:
        return parsed
    spec = ITEM_FIELDS.get(name, {})
    counts: dict[str, int] = defaultdict(int)
    for raw in items or []:
        if isinstance(raw, Mapping) and isinstance(raw.get("id"), str) and raw["id"].strip():
            counts[raw["id"]] += 1
    for index, raw in enumerate(items or []):
        item = dict(raw) if isinstance(raw, Mapping) else {}
        raw_id = item.get("id")
        has_id = isinstance(raw_id, str) and bool(raw_id.strip())
        entry = Entry(label=str(raw_id) if has_id else f"#{index}", item=item)
        if not isinstance(raw, Mapping):
            entry.issues.append(issue(BAD_FIELD, "항목이 매핑이 아닙니다"))
        elif not has_id:
            entry.issues.append(issue(MISSING_FIELD, "`id` 칸이 비었습니다"))
        elif counts[str(raw_id)] > 1:
            entry.issues.append(issue(DUPLICATE_ID, f"id `{raw_id}`가 파일 안에서 겹칩니다"))
        if isinstance(raw, Mapping):
            if item.get("origin") != ORIGIN:
                entry.issues.append(issue(BAD_ORIGIN, f"`origin`은 `{ORIGIN}`이어야 합니다"))
            evidence = item.get("evidence")
            if not (isinstance(evidence, str) and evidence.strip()):
                entry.issues.append(issue(MISSING_FIELD, "`evidence`(근거 run ID) 칸이 비었습니다"))
            status = item.get("status")
            if status not in (STATUS_ACTIVE, STATUS_WITHDRAWN):
                entry.issues.append(
                    issue(BAD_STATUS, f"`status`는 `{STATUS_ACTIVE}`·`{STATUS_WITHDRAWN}`")
                )
            entry.withdrawn = status == STATUS_WITHDRAWN
            if entry.withdrawn:
                reason = item.get("reason")
                if not (isinstance(reason, str) and reason.strip()):
                    entry.issues.append(
                        issue(MISSING_REASON, "철회 항목에는 `reason`이 필요합니다")
                    )
            else:
                entry.issues += _field_issues(item, spec)
        parsed.entries.append(entry)
    return parsed


# ──────────────────────────────────────────────
# 글 검사
# ──────────────────────────────────────────────


def text_form_issues(text: str, *, fences_allowed: bool = False) -> list[dict[str, str]]:
    """중괄호(프롬프트 자리표시자 충돌) · 코드 펜스."""
    out = []
    folded = _norm(text)
    if "{" in folded or "}" in folded:
        out.append(issue(BRACES, "중괄호를 쓸 수 없습니다(프롬프트 자리표시자와 충돌)"))
    if not fences_allowed and any(f in folded for f in _FENCES):
        out.append(issue(CODE_FENCE, "코드 펜스를 쓸 수 없습니다"))
    return out


def mentioned_identifiers(text: str, catalog: Catalog) -> tuple[set[str], set[str]]:
    """글이 언급한 ``(테이블 맨 이름 소문자, 실존하지 않는 식별자)``.

    - 백틱 안: 식별자 꼴이면 테이블·컬럼·`table.column`으로 대조하고, SQL 조각이면 따옴표 리터럴을
      지운 뒤 식별자 토큰을 하나씩 대조한다(SQL 낱말 제외).
    - 백틱 밖: `table.column`(왼쪽이 테이블 이름형일 때) · 테이블 이름형 토큰.
    """
    tables: set[str] = set()
    unknown: set[str] = set()

    def check(token: str) -> None:
        word = _norm(token)
        if "." in word:
            table, _, column = word.partition(".")
            key = table.lower()
            if key not in catalog.tables:
                unknown.add(word)
                return
            tables.add(key)
            if column.casefold() not in catalog.columns.get(key, frozenset()):
                unknown.add(word)
            return
        if word.lower() in catalog.tables:
            tables.add(word.lower())
        elif word.casefold() in catalog.column_names or word.lower() in _SQL_WORDS:
            return
        else:
            unknown.add(word)

    for content in _BACKTICK_RE.findall(text or ""):
        content = content.strip()
        if _IDENT_RE.match(content):
            check(content)
            continue
        for token in _IDENT_TOKEN_RE.findall(_QUOTED_RE.sub(" ", content)):
            if token.lower() not in _SQL_WORDS:
                check(token)
    prose = _BACKTICK_RE.sub(" ", text or "")
    for table, column in _DOTTED_RE.findall(prose):
        key = table.lower()
        if key in catalog.tables:
            tables.add(key)
            # 백틱 밖은 조사가 붙는다(`…80.호스트명으로`) — 실존 컬럼 이름으로 시작하면 실존
            folded = _norm(column).casefold()
            if not any(folded.startswith(c) for c in catalog.columns.get(key, frozenset())):
                unknown.add(f"{table}.{column}")
        elif catalog.table_token is not None and catalog.table_token.fullmatch(table):
            unknown.add(f"{table}.{column}")
    if catalog.table_token is not None:
        for token in catalog.table_token.findall(_DOTTED_RE.sub(" ", prose)):
            check(token)
    return tables, unknown


def mention_issues(text: str, catalog: Catalog) -> list[dict[str, str]]:
    """언급 식별자 실존 · 조회 대상 밖 테이블 언급."""
    tables, unknown = mentioned_identifiers(text, catalog)
    out = []
    if unknown:
        out.append(issue(
            UNKNOWN_IDENTIFIER, f"스키마에 없는 식별자: {', '.join(sorted(unknown)[:20])}"
        ))
    outside = sorted(catalog.tables[t] for t in tables if t not in catalog.allowed)
    if outside:
        out.append(issue(TABLE_NOT_ALLOWED, f"조회 대상 밖 테이블 언급: {', '.join(outside[:20])}"))
    return out


def mentions_utilization(text: str) -> bool:
    """사용률(·CPU/메모리/디스크 사용량)을 말하는가 — K1·K2·K3·K4 사용률 규칙의 공용 판정."""
    return _UTILIZATION_RE.search(_norm(text or "")) is not None


def _redirects_utilization(sentence: str) -> bool:
    """사용률을 말한 절 가운데 하나라도 부정·관측 DB 안내를 함께 담는가."""
    return any(
        mentions_utilization(clause) and _REDIRECT_RE.search(clause)
        for clause in _CLAUSE_SPLIT_RE.split(sentence)
    )


def utilization_issues(text: str) -> list[dict[str, str]]:
    """사용률을 이 DB에서 답하라는 문장(D-308 G-6) — 사용률을 말한 절에 부정·관측 DB 안내가
    없으면 거절(같은 문장의 무관한 절의 부정어로는 풀리지 않는다)."""
    hits = [
        sentence.strip()[:60]
        for sentence in _SENTENCE_SPLIT_RE.split(text or "")
        if mentions_utilization(sentence) and not _redirects_utilization(sentence)
    ]
    if not hits:
        return []
    return [issue(
        UTILIZATION_RULE,
        "사용률 정본은 관측 DB다(D-308 G-6) — 이 DB에서 답하라는 문장을 쓸 수 없다: " + hits[0],
    )]


def prose(text: str) -> str:
    """코드 펜스 블록을 뺀 글."""
    return _FENCE_BLOCK_RE.sub(" ", text or "")


def join_texts(texts: Iterable[str]) -> str:
    """항목 글 연결(앞뒤 공백 제거 · 줄바꿈 하나) — K1 `query_guide`·K4 섹션 본문."""
    return "\n".join(str(t).strip() for t in texts)


def guide_item_issues(text: str, catalog: Catalog) -> list[dict[str, str]]:
    """K1 항목 — 중괄호·펜스 · 언급 식별자 · 조회 대상 · 사용률 규칙."""
    return text_form_issues(text) + mention_issues(text, catalog) + utilization_issues(text)


def guide_total_issues(texts: Sequence[str]) -> list[dict[str, str]]:
    """K1 연결본 길이(≤ `GUIDE_MAX_CHARS`)."""
    length = len(join_texts(texts))
    if length > GUIDE_MAX_CHARS:
        return [issue(TOO_LONG, f"연결 길이 {length}자 — 상한 {GUIDE_MAX_CHARS}자")]
    return []


def section_item_issues(text: str, catalog: Catalog) -> list[dict[str, str]]:
    """K4 항목 글(펜스 밖) — 한글 식별자까지 언급 실존 · 조회 대상 · 사용률 규칙.

    섹션 구조 검사(길이·중괄호·펜스 SQL·블록 수)는 호출부가 D-294 ③ 검증기로 한다 — 그 검증기의
    식별자 대조는 라틴 식별자만 보므로 여기서 한글 식별자를 더 본다.
    """
    body = prose(text)
    return mention_issues(body, catalog) + utilization_issues(body)


def example_item_issues(item: Mapping[str, Any], catalog: Catalog) -> list[dict[str, str]]:
    """K2 항목 정적 검사(SQL 밖) — `tables` 실존·조회 대상 · 질문·설명 중괄호·펜스 · 사용률 질문
    (D-308 G-6 — 사용률을 이 DB로 답하는 예시는 그 답을 가르친다)."""
    out = []
    for name in ("question", "description"):
        if isinstance(item.get(name), str):
            out += text_form_issues(item[name])
    if isinstance(item.get("question"), str):
        out += utilization_issues(item["question"])
    raw_tables = item.get("tables")
    tables: list[Any] = raw_tables if isinstance(raw_tables, list) else []
    unknown = sorted(str(t) for t in tables if bare_name(str(t)).lower() not in catalog.tables)
    if unknown:
        out.append(issue(UNKNOWN_IDENTIFIER, f"`tables`에 없는 테이블: {', '.join(unknown)}"))
    outside = sorted(
        str(t) for t in tables
        if bare_name(str(t)).lower() in catalog.tables
        and bare_name(str(t)).lower() not in catalog.allowed
    )
    if outside:
        out.append(issue(TABLE_NOT_ALLOWED, f"조회 대상 밖 테이블: {', '.join(outside)}"))
    return out


def _column_exists(item: Mapping[str, Any], catalog: Catalog) -> list[dict[str, str]]:
    table = bare_name(str(item.get("table") or "")).lower()
    column = _norm(str(item.get("column") or "")).casefold()
    if table not in catalog.tables or column not in catalog.columns.get(table, frozenset()):
        return [issue(UNKNOWN_COLUMN, f"없는 컬럼: {item.get('table')}.{item.get('column')}")]
    return []


def description_item_issues(item: Mapping[str, Any], catalog: Catalog) -> list[dict[str, str]]:
    """K3 설명 — 컬럼 실존 · ≤ 200자 · 중괄호·펜스 · 사용률 규칙(D-308 G-6) · 값(따옴표 리터럴 ·
    코드 열거 · 긴 숫자열)."""
    out = _column_exists(item, catalog)
    text = _norm(str(item.get("text") or ""))
    if len(text) > DESCRIPTION_MAX_CHARS:
        out.append(issue(TOO_LONG, f"{len(text)}자 — 상한 {DESCRIPTION_MAX_CHARS}자"))
    out += text_form_issues(text) + utilization_issues(text)
    found = []
    if _QUOTED_RE.search(text):
        found.append("따옴표 리터럴")
    if _CODE_PAIR_RE.search(text):
        found.append("코드 열거")
    if _LONG_DIGITS_RE.search(text):
        found.append("긴 숫자열")
    if found:
        out.append(issue(VALUE_LITERAL, f"설명에 값을 쓸 수 없습니다({' · '.join(found)})"))
    return out


def _key(item: Mapping[str, Any]) -> tuple[str, str]:
    return (
        bare_name(str(item.get("table") or "")).lower(),
        _norm(str(item.get("column") or "")).casefold(),
    )


def synonym_item_issues(item: Mapping[str, Any], catalog: Catalog) -> list[dict[str, str]]:
    """K3 유사어 항목 — 컬럼 실존 · 낱말 2자 미만 · 다른 컬럼 이름과 같음(D-142 쓰기 가드) ·
    사용률(D-308 G-6 — 낱말은 부정을 담을 수 없으니 사용률 낱말·사용률 컬럼 대상 모두 거절)."""
    out = _column_exists(item, catalog)
    if mentions_utilization(str(item.get("column") or "")):
        out.append(issue(
            UTILIZATION_RULE,
            "사용률 정본은 관측 DB다(D-308 G-6) — 사용률 컬럼에 유사어를 걸 수 없다",
        ))
    own = _key(item)[1]
    raw_words = item.get("words")
    words: list[Any] = raw_words if isinstance(raw_words, list) else []
    short = [str(w) for w in words if len(_norm(str(w))) < SYNONYM_MIN_CHARS]
    if short:
        out.append(issue(TOO_SHORT, f"{SYNONYM_MIN_CHARS}자 미만 낱말: {', '.join(short)}"))
    conflict = sorted({
        str(w) for w in words
        if (folded := _norm(str(w)).casefold()) in catalog.column_names and folded != own
    })
    if conflict:
        out.append(issue(NAME_CONFLICT, f"다른 컬럼 이름과 같은 낱말: {', '.join(conflict)}"))
    utilization = [str(w) for w in words if mentions_utilization(str(w))]
    if utilization:
        out.append(issue(
            UTILIZATION_RULE,
            f"사용률 정본은 관측 DB다(D-308 G-6) — 사용률 낱말: {', '.join(utilization)}",
        ))
    return out


def ambiguous_synonyms(items: Sequence[Mapping[str, Any]]) -> dict[int, list[dict[str, str]]]:
    """다의어 — 같은 낱말이 서로 다른 컬럼 이름에 걸리면 그 낱말을 가진 항목 모두 거절.

    같은 이름의 컬럼이 여러 테이블에 있는 것(현행·적재·이력 쌍둥이)은 한 뜻으로 본다 — 유사어
    사전 키가 컬럼 이름이다.

    Returns:
        ``{항목 순번: [문제]}``
    """
    targets: dict[str, set[str]] = defaultdict(set)
    for item in items:
        for word in item.get("words") or []:
            targets[_norm(str(word)).casefold()].add(_key(item)[1])
    ambiguous = {w for w, columns in targets.items() if len(columns) > 1}
    out: dict[int, list[dict[str, str]]] = {}
    for index, item in enumerate(items):
        hit = sorted(
            {str(w) for w in item.get("words") or [] if _norm(str(w)).casefold() in ambiguous}
        )
        if hit:
            out[index] = [issue(AMBIGUOUS, f"여러 컬럼에 걸린 낱말(다의어): {', '.join(hit)}")]
    return out


def duplicate_targets(
    items: Sequence[Mapping[str, Any]], key: Any = _key, label: str = "같은 컬럼"
) -> dict[int, list[dict[str, str]]]:
    """같은 대상(기본: 테이블·컬럼)을 둘 이상의 항목이 가지면 모두 거절."""
    seen: dict[Any, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        seen[key(item)].append(index)
    return {
        index: [issue(DUPLICATE, f"{label}을 가진 항목이 {len(indices)}개입니다")]
        for indices in seen.values() if len(indices) > 1 for index in indices
    }


# ──────────────────────────────────────────────
# 근거 run 리터럴 — 커밋되는 원천 파일에 내부망 값 거절 (D-301 · D-308)
# ──────────────────────────────────────────────


def sql_literals(sql: str) -> list[str]:
    """실행 SQL의 문자열·숫자 리터럴(발생 순 · 중복 포함) — 주석·인용 식별자·식별자 안은
    보지 않는다.

    - 문자열은 LIKE 와일드카드 `%`로 갈라 조각마다 싣는다(앞뒤 공백 제거 · `''`는 `'`).
    - 행 수 제한 낱말(`LIMIT`·`OFFSET`·`FETCH FIRST|NEXT`·`TOP`) 바로 뒤 숫자(쉼표로 이은 것 포함)는
      뺀다 — 제한 값은 내부망 값이 아니다.

    길이·식별자·고정 열거 제외는 `evidence_literal_values`가 한다.
    """
    out: list[str] = []
    after_limit = False
    for match in _SQL_LITERAL_TOKEN_RE.finditer(str(sql or "")):
        token = match.group(0)
        if token.startswith(("--", "/*")) or token[0] in '"`':
            after_limit = False
            continue
        if token[0] == "'":
            content = token[1:-1].replace("''", "'")
            out += [part.strip() for part in content.split("%") if part.strip()]
            after_limit = False
        elif token[0].isdigit():
            if not after_limit:
                out.append(token)
        elif token == ",":
            continue  # `LIMIT n, m`의 두 번째 수도 제한 값이다
        else:
            after_limit = token.casefold() in _ROW_LIMIT_WORDS
    return out


def evidence_literal_values(values: Iterable[str], exempt: Iterable[str] = ()) -> set[str]:
    """근거 리터럴 후보 → 원천 파일 대조 집합.

    빼는 것: `EVIDENCE_LITERAL_MIN_CHARS`보다 짧은 값 · SQL 키워드 · 고정 열거(정의
    `kind`·`origin` · 원천 공통 칸 · K8 슬롯 종류·날짜 형식) · ``exempt``(스키마 테이블·컬럼
    이름 등 — casefold 대조).
    """
    skip = {str(v).casefold() for v in exempt} | _SQL_WORDS | _FIXED_ENUMS
    return {
        v for v in (str(x).strip() for x in values)
        if len(v) >= EVIDENCE_LITERAL_MIN_CHARS and v.casefold() not in skip
    }


# ──────────────────────────────────────────────
# K6 — 정의 kind 파생 쿼리 규칙
# ──────────────────────────────────────────────


def derive_kind_rules(
    definitions: Mapping[str, Any],
    columns: Mapping[str, Sequence[str]],
    *,
    types: Mapping[str, Mapping[str, str]] | None = None,
    allowed: Iterable[str] | None = None,
) -> list[str]:
    """테이블 정의 `kind`에서 `query_rules` 문장을 결정적으로 만든다(값 0 · 테이블 이름 순).

    - `현행` + 활성 여부 칸(`활성여부`·`활성화여부`) — 지금 상태는 그 칸으로 활성 행만 센다(값은
      코드값 안내를 따른다).
    - `수집이력` + 기준일 칸(`기준년월일`·`기준년월`·`기준일자` · 문자열 타입) — 날짜 문자열
      형식(이름 기준 추정) · 같은 형식 문자열 비교 · 현재 상태는 현행 원장 먼저, 이력은 최신
      기준일 조건.
    - `수집적재` — 선택 금지(같은 군의 현행 원장을 쓴다).
    그 밖의 kind는 규칙을 만들지 않는다.

    Args:
        definitions: `table_definitions`
        columns: ``{테이블: [컬럼…]}``
        types: ``{테이블: {컬럼: 타입}}`` — 있으면 기준일 칸은 문자열 타입만
        allowed: 조회 대상(있으면 그 밖 테이블은 뺀다)
    """
    scope = None if allowed is None else {bare_name(str(t)).lower() for t in allowed}
    by_bare = {bare_name(str(t)).lower(): (str(t), cols) for t, cols in columns.items()}
    type_map = {bare_name(str(t)).lower(): dict(v) for t, v in (types or {}).items()}
    active_refs: list[str] = []
    date_refs: list[str] = []
    load_tables: list[str] = []
    for name in sorted(definitions, key=lambda n: bare_name(str(n)).lower()):
        key = bare_name(str(name)).lower()
        if (scope is not None and key not in scope) or key not in by_bare:
            continue
        table, cols = by_bare[key]
        raw = definitions[name]
        kind = raw.get("kind") if isinstance(raw, Mapping) else None
        if kind == "현행":
            active_refs += [f"`{table}.{c}`" for c in cols if _ACTIVE_FLAG_RE.match(str(c))]
        elif kind == "수집이력":
            for c in cols:
                match = _BASE_DATE_RE.match(str(c))
                col_type = type_map.get(key, {}).get(str(c))
                if match and (col_type is None or _STRING_TYPE_RE.match(col_type)):
                    fmt = "YYYYMM" if match.group(1) == "년월" else "YYYYMMDD"
                    date_refs.append(f"`{table}.{c}`({fmt} 추정)")
        elif kind == KIND_COLLECT_LOAD:
            load_tables.append(f"`{table}`")
    rules: list[str] = []
    if active_refs:
        rules.append(
            "현행 원장의 활성 여부 칸 — 지금 상태(현재 대수·목록)를 물으면 이 칸으로 활성 행만 "
            f"고른다: {', '.join(active_refs)}. "
            "활성을 뜻하는 값은 코드값 안내를 따르고 짐작하지 않는다."
        )
    if date_refs:
        rules.append(
            "수집 이력의 기준일 칸은 날짜 문자열이다(형식은 이름 기준 추정): "
            f"{', '.join(date_refs)}. "
            "기간·시점 조건은 같은 형식 문자열로 비교하고 날짜 함수·CAST로 바꾸지 않는다. "
            "현재 상태 질문은 현행 원장을 먼저 쓰고, "
            "이력을 쓸 때는 최신 기준일 조건을 건다."
        )
    if load_tables:
        rules.append(
            "수집 적재 테이블은 현행 원장의 수집 원본 적재본이다 — "
            "조회에 고르지 않고 같은 군의 현행 "
            f"원장을 쓴다: {', '.join(load_tables)}."
        )
    return rules
