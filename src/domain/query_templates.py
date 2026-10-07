"""조회 템플릿 계약 — 파싱·계약 검사·슬롯 형식 검증·바인딩 (plans/143 W6 · D-316 ③).

템플릿은 **데이터**다(`config/knowledge/{db_id}/query_templates.yaml` — Python 코드 생성 기각 D-294
G-1 유지). LLM은 템플릿 ID와 슬롯 값만 고르고, 이 모듈이 슬롯 값을 형식 정규식으로 검사한 뒤 단일
리터럴 인용 함수로만 SQL에 넣는다(D-004 — LLM 출력은 정합성 근거가 아니다).

- 자리표는 `:slot`(기간은 `:slot_start`·`:slot_end`). 치환은 토큰 단위이고 문자열 리터럴·인용
  식별자(백틱·큰따옴표) 안은 건드리지 않는다.
- 선택(required=false) 슬롯이 비면 SQL `NULL`을 넣는다 — 템플릿은 `(:x IS NULL OR col = :x)`
  처럼 쓴다.
- 따옴표·세미콜론·주석 기호·백슬래시는 어떤 슬롯에서도 거절한다(유니코드 따옴표 포함).
- LIKE 피연산자의 자리표는 keyword 슬롯만 쓴다(그 형식이 `%`·`_`를 거절한다) — 다른 슬롯 값의
  `_`는 LIKE 한 글자 와일드카드로 남는다. 계약 검사가 거절한다.
- MariaDB 실행 주석(`/*! … */`)은 주석 안이 실행된다 — `has_executable_comment`로 템플릿 계약과
  지식 자산 검증 실행 경로(plans/143)가 실행 전에 거절한다.

IO가 없는 순수 모듈이다 — 파일 읽기·LLM 호출·검증기 연결은 `src.db_adapters.template_assembler`.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

#: 템플릿 파일 형식 버전
TEMPLATE_FILE_VERSION = 1

STATUS_ACTIVE = "active"
STATUS_WITHDRAWN = "withdrawn"
_STATUSES = frozenset({STATUS_ACTIVE, STATUS_WITHDRAWN})

SLOT_CENTER = "center"
SLOT_DEPT_CODE = "dept_code"
SLOT_DATE = "date"
SLOT_DATE_RANGE = "date_range"
SLOT_HOSTNAME = "hostname"
SLOT_CODE = "code"
SLOT_KEYWORD = "keyword"
SLOT_TYPES = frozenset({
    SLOT_CENTER, SLOT_DEPT_CODE, SLOT_DATE, SLOT_DATE_RANGE, SLOT_HOSTNAME, SLOT_CODE, SLOT_KEYWORD,
})

FORMAT_YYYYMMDD = "yyyymmdd"
FORMAT_ISO = "iso"
_DATE_FORMATS = frozenset({FORMAT_YYYYMMDD, FORMAT_ISO})

# ── 결과 사유(조립기·상태 표지 공용 열거) ──
OUTCOME_ASSEMBLED = "assembled"
OUTCOME_FALLBACK = "fallback"

REASON_NO_FILE = "no_file"              # 템플릿 파일 없음 — 무동작(표지 없음)
REASON_INVALID_FILE = "invalid_file"    # 파일은 있으나 읽을 수 없거나 유효한 active 템플릿 0개
REASON_NO_MATCH = "no_match"            # LLM이 「none」을 골랐다
REASON_INVALID_JSON = "invalid_json"    # LLM 응답이 계약 JSON이 아니다
REASON_UNKNOWN_TEMPLATE = "unknown_template"
REASON_SLOT_MISSING = "slot_missing"
REASON_SLOT_REJECTED = "slot_rejected"
REASON_GUARD_REJECTED = "guard_rejected"
REASON_VALIDATION_FAILED = "validation_failed"
REASON_LLM_ERROR = "llm_error"

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}")
_SLOT_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_COLUMN_RE = re.compile(r"[^\s.`'\";\\]+\.[^\s.`'\";\\]+")
_PLACEHOLDER_RE = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)")
_SELECT_HEAD_RE = re.compile(r"\s*(?:select|with)\b", re.IGNORECASE)
_TABLE_REF_RE = re.compile(
    r"\b(?:from|join)\s+(`[^`]+`(?:\.`[^`]+`)?|[A-Za-z0-9_$\u3131-\u318e\uac00-\ud7a3.]+)",
    re.IGNORECASE,
)
#: `FROM`을 인자 문법으로 쓰는 함수(테이블 참조가 아니다) — 판정 전에 지운다
_FUNC_FROM_RE = re.compile(r"\b(?:extract|trim|substring|position)\s*\([^()]*\)", re.IGNORECASE)
_CTE_RE = re.compile(r"(?:\bwith|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", re.IGNORECASE)
_LIKE_RE = re.compile(r"\blike\b", re.IGNORECASE)
#: MariaDB·MySQL 실행 주석 — `/*! … */`·`/*M! … */`(버전 접두 포함)
_EXEC_COMMENT_RE = re.compile(r"/\*M?!")

# ── 슬롯 형식(엄격 — fullmatch · ASCII 범위 명시로 `\w`의 유니코드 확장을 피한다) ──
_HANGUL = "\uac00-\ud7a3"
_CENTER_RE = re.compile(
    rf"[{_HANGUL}A-Za-z0-9](?:[{_HANGUL}A-Za-z0-9_\-. ]{{0,28}}[{_HANGUL}A-Za-z0-9])?"
)
_DEPT_CODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{0,19}")
_HOSTNAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_\-.]{0,62}[A-Za-z0-9])?")
_KEYWORD_RE = re.compile(
    rf"[{_HANGUL}A-Za-z0-9](?:[{_HANGUL}A-Za-z0-9\-. ]{{0,28}}[{_HANGUL}A-Za-z0-9])?"
)
_CODE_MAX_LEN = 64
_DATE_RE = re.compile(r"([0-9]{4})[-./]?([0-9]{2})[-./]?([0-9]{2})")
#: 어떤 슬롯에서도 거절하는 문자·기호(유니코드 따옴표 포함)
_FORBIDDEN_CHARS = frozenset(
    "'\"`;\\#"
    "\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f\u2032\u2033\u2035\u2036"
    "\uff02\uff07\uff1b\u02bc\u02bb\u00b4"
)
_FORBIDDEN_SEQS = ("--", "/*", "*/")


@dataclass(frozen=True)
class SlotSpec:
    """템플릿 슬롯 하나의 계약."""

    name: str
    type: str
    required: bool = True
    column: str | None = None
    format: str | None = None

    def placeholders(self) -> tuple[str, ...]:
        """이 슬롯이 SQL에 쓰는 자리표 이름(기간은 시작·끝 둘)."""
        if self.type == SLOT_DATE_RANGE:
            return (f"{self.name}_start", f"{self.name}_end")
        return (self.name,)


@dataclass(frozen=True)
class QueryTemplate:
    """조회 템플릿 하나 — 의도·표면어·슬롯·SQL·참조 테이블."""

    id: str
    intent: str
    triggers: tuple[str, ...]
    slots: tuple[SlotSpec, ...]
    sql: str
    tables: tuple[str, ...]
    status: str = STATUS_ACTIVE


@dataclass(frozen=True)
class BindResult:
    """바인딩 결과 — 성공이면 ``sql``, 실패면 ``reason``·``detail``(슬롯 이름·규칙만 · 값 없음)."""

    sql: str | None
    reason: str | None = None
    detail: str = ""
    slot_names: tuple[str, ...] = ()


class SlotRejectedError(ValueError):
    """슬롯 값이 형식 밖이다(메시지에 값은 싣지 않는다)."""


# ──────────────────────────────────────────────
# SQL 토큰 구간 — 리터럴·인용 식별자·주석을 코드 구간과 나눈다
# ──────────────────────────────────────────────


def _segments(sql: str) -> Iterator[tuple[str, str]]:
    """SQL을 (종류, 조각)으로 나눈다 — 종류: code · literal · quoted · comment."""
    i, n, start = 0, len(sql), 0
    while i < n:
        ch = sql[i]
        if ch in "'\"`":
            if start < i:
                yield "code", sql[start:i]
            j = i + 1
            while j < n:
                if sql[j] == "\\" and ch != "`":
                    j += 2
                    continue
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:  # 같은 따옴표 두 번 = 이스케이프
                        j += 2
                        continue
                    break
                j += 1
            end = min(j + 1, n)
            yield ("literal" if ch == "'" else "quoted"), sql[i:end]
            i = start = end
            continue
        if sql.startswith("--", i) or ch == "#" or sql.startswith("/*", i):
            if start < i:
                yield "code", sql[start:i]
            if ch == "/":
                end = sql.find("*/", i + 2)
                end = n if end < 0 else end + 2
            else:
                end = sql.find("\n", i)
                end = n if end < 0 else end
            yield "comment", sql[i:end]
            i = start = end
            continue
        i += 1
    if start < n:
        yield "code", sql[start:]


def placeholders_of(sql: str) -> list[str]:
    """SQL 코드 구간의 자리표 이름(등장 순 · 중복 제거). 리터럴·인용 식별자 안은 세지 않는다."""
    names: list[str] = []
    for kind, seg in _segments(sql):
        if kind == "code":
            for m in _PLACEHOLDER_RE.finditer(seg):
                if m.group(1) not in names:
                    names.append(m.group(1))
    return names


def referenced_tables(sql: str) -> set[str]:
    """SQL이 FROM·JOIN으로 참조하는 테이블의 소문자 맨 이름(CTE 제외).

    쉼표 조인(`FROM a, b`)은 읽지 않는다 — 템플릿은 명시 JOIN으로 쓴다(계약 검사가 불일치로 잡는다).
    """
    names: set[str] = set()
    # 인용 식별자(백틱)는 이름으로 읽어야 하므로 코드 구간과 함께 이어 붙인다
    body = "".join(seg if kind in ("code", "quoted") else " " for kind, seg in _segments(sql))
    body = _FUNC_FROM_RE.sub(" ", body)
    ctes = {m.group(1).lower() for m in _CTE_RE.finditer(body)}
    for m in _TABLE_REF_RE.finditer(body):
        bare = m.group(1).rsplit(".", 1)[-1].strip("`").lower()
        if bare and bare not in ctes:
            names.add(bare)
    return names


def has_executable_comment(sql: str) -> bool:
    """MariaDB·MySQL 실행 주석(`/*! … */`·`/*M! … */`)을 담았는가 — 주석 안이 실행된다.

    `SQLGuard`가 이 주석 안을 보지 못하므로 이번 계획의 실행 경로가 실행 전에 따로 거절한다.
    문자열 리터럴 안까지 보수적으로 본다.
    """
    return _EXEC_COMMENT_RE.search(sql or "") is not None


def _like_operand(body: str, start: int) -> str:
    """`LIKE` 뒤 피연산자 하나 — 함수 호출·괄호식은 짝 괄호까지, 아니면 다음 공백·괄호·쉼표
    앞까지."""
    n = len(body)
    i = start
    while i < n and body[i].isspace():
        i += 1
    j = i
    while j < n and not body[j].isspace() and body[j] not in "(),":
        j += 1
    k = j
    while k < n and body[k].isspace():
        k += 1
    if k >= n or body[k] != "(":
        return body[i:j]
    depth = 0
    for end in range(k, n):
        if body[end] == "(":
            depth += 1
        elif body[end] == ")":
            depth -= 1
            if depth == 0:
                return body[i:end + 1]
    return body[i:]


def like_placeholders(sql: str) -> set[str]:
    """`LIKE`(`NOT LIKE` 포함) 피연산자에 쓰인 자리표 이름.

    리터럴·인용 식별자·주석 안은 보지 않는다.
    """
    body = "".join(seg if kind == "code" else " _ " for kind, seg in _segments(sql))
    names: set[str] = set()
    for m in _LIKE_RE.finditer(body):
        names.update(p.group(1) for p in _PLACEHOLDER_RE.finditer(_like_operand(body, m.end())))
    return names


# ──────────────────────────────────────────────
# 파싱 · 계약 검사
# ──────────────────────────────────────────────


def _parse_slot(raw: Any, issues: list[str], tid: str) -> SlotSpec | None:
    if not isinstance(raw, Mapping):
        issues.append(f"{tid}: 슬롯 항목이 매핑이 아니다")
        return None
    name, stype = raw.get("name"), raw.get("type")
    if not isinstance(name, str) or not _SLOT_NAME_RE.fullmatch(name):
        issues.append(f"{tid}: 슬롯 이름 형식 위반({name!r})")
        return None
    if stype not in SLOT_TYPES:
        issues.append(f"{tid}: 슬롯 {name} 형식이 허용 목록 밖({stype!r})")
        return None
    required = raw.get("required", True)
    if not isinstance(required, bool):
        issues.append(f"{tid}: 슬롯 {name} required가 bool이 아니다")
        return None
    column = raw.get("column")
    fmt = raw.get("format")
    if stype == SLOT_CODE:
        if not isinstance(column, str) or not _COLUMN_RE.fullmatch(column):
            issues.append(f"{tid}: code 슬롯 {name}에 column(table.column)이 없다")
            return None
    elif column is not None:
        issues.append(f"{tid}: 슬롯 {name}은 code가 아니라 column을 둘 수 없다")
        return None
    if stype in (SLOT_DATE, SLOT_DATE_RANGE):
        fmt = FORMAT_ISO if fmt is None else fmt
        if fmt not in _DATE_FORMATS:
            issues.append(f"{tid}: 슬롯 {name} format이 허용 목록 밖({fmt!r})")
            return None
    elif fmt is not None:
        issues.append(f"{tid}: 슬롯 {name}은 날짜가 아니라 format을 둘 수 없다")
        return None
    return SlotSpec(name=name, type=str(stype), required=required, column=column, format=fmt)


def _str_list(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        return None
    return tuple(v.strip() for v in value)


def _check_sql(
    tid: str, sql: str, slots: tuple[SlotSpec, ...], tables: tuple[str, ...],
) -> list[str]:
    issues: list[str] = []
    if "{" in sql or "}" in sql:
        issues.append(f"{tid}: sql에 중괄호가 있다")
    if ";" in sql:
        issues.append(f"{tid}: sql에 세미콜론이 있다")
    if any(kind == "comment" for kind, _ in _segments(sql)):
        issues.append(f"{tid}: sql에 주석이 있다")
    if has_executable_comment(sql):
        issues.append(f"{tid}: sql에 실행 주석(/*! … */)이 있다")
    if "\\" in sql:
        issues.append(f"{tid}: sql에 백슬래시가 있다")
    if not _SELECT_HEAD_RE.match(sql):
        issues.append(f"{tid}: sql이 SELECT(또는 WITH … SELECT) 단일문이 아니다")
    expected = {p for s in slots for p in s.placeholders()}
    found = set(placeholders_of(sql))
    if found != expected:
        missing, extra = sorted(expected - found), sorted(found - expected)
        issues.append(f"{tid}: 자리표 불일치(슬롯만 {missing} · sql만 {extra})")
    slot_types = {p: s.type for s in slots for p in s.placeholders()}
    like_bad = sorted(
        p for p in like_placeholders(sql) if p in slot_types and slot_types[p] != SLOT_KEYWORD
    )
    if like_bad:
        issues.append(
            f"{tid}: LIKE 피연산자에 keyword가 아닌 슬롯 {like_bad}"
            "(값의 `_`·`%`가 와일드카드로 남는다 — keyword 슬롯만)"
        )
    declared = {t.rsplit(".", 1)[-1].strip("`").lower() for t in tables}
    refs = referenced_tables(sql)
    if refs != declared:
        issues.append(
            f"{tid}: tables 불일치(sql만 {sorted(refs - declared)}"
            f" · tables만 {sorted(declared - refs)})"
        )
    return issues


def parse_templates(raw: Any) -> tuple[list[QueryTemplate], list[str]]:
    """원시 파일 내용을 템플릿 목록과 계약 위반 목록으로 나눈다(위반 템플릿은 목록에서 뺀다).

    Returns:
        (유효 템플릿 — 상태 무관, 위반 사유 ``"<id>: 사유"`` 목록)
    """
    issues: list[str] = []
    if not isinstance(raw, Mapping):
        return [], ["파일 최상위가 매핑이 아니다"]
    if raw.get("version") != TEMPLATE_FILE_VERSION:
        issues.append(f"version이 {TEMPLATE_FILE_VERSION}이 아니다({raw.get('version')!r})")
        return [], issues
    items = raw.get("templates")
    if not isinstance(items, list):
        return [], ["templates가 목록이 아니다"]
    out: list[QueryTemplate] = []
    seen: set[str] = set()
    for idx, item in enumerate(items):
        if not isinstance(item, Mapping):
            issues.append(f"#{idx}: 항목이 매핑이 아니다")
            continue
        tid = item.get("id")
        if not isinstance(tid, str) or not _ID_RE.fullmatch(tid):
            issues.append(f"#{idx}: id 형식 위반({tid!r})")
            continue
        if tid in seen:
            issues.append(f"{tid}: id 중복")
            continue
        seen.add(tid)
        before = len(issues)
        intent = item.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            issues.append(f"{tid}: intent가 비었다")
        triggers = _str_list(item.get("triggers", []))
        if triggers is None:
            issues.append(f"{tid}: triggers가 문자열 목록이 아니다")
        status = item.get("status", STATUS_ACTIVE)
        if status not in _STATUSES:
            issues.append(f"{tid}: status가 허용 목록 밖({status!r})")
        raw_slots = item.get("slots", [])
        slots: list[SlotSpec] = []
        if not isinstance(raw_slots, list):
            issues.append(f"{tid}: slots가 목록이 아니다")
        else:
            for rs in raw_slots:
                spec = _parse_slot(rs, issues, tid)
                if spec is not None:
                    slots.append(spec)
            names = [s.name for s in slots]
            if len(names) != len(set(names)):
                issues.append(f"{tid}: 슬롯 이름 중복")
        tables = _str_list(item.get("tables"))
        if not tables:
            issues.append(f"{tid}: tables가 비었거나 문자열 목록이 아니다")
        sql = item.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            issues.append(f"{tid}: sql이 비었다")
        elif len(issues) == before:
            issues.extend(_check_sql(tid, sql.strip(), tuple(slots), tables or ()))
        if len(issues) == before:
            out.append(QueryTemplate(
                id=tid, intent=str(intent).strip(), triggers=triggers or (),
                slots=tuple(slots), sql=str(sql).strip(), tables=tables or (),
                status=str(status),
            ))
    return out, issues


def check_templates(
    raw: Any, catalog_columns: Mapping[str, Collection[str]] | None = None,
) -> list[str]:
    """템플릿 파일의 계약 위반을 모두 돌려준다(빈 목록 = 통과).

    Args:
        raw: 파일 내용(YAML 적재 결과)
        catalog_columns: ``{테이블: 컬럼 이름들}``(선택) — 주면 active 템플릿의 `tables`와 code
            슬롯 `column`의 실존을 대조한다(이름 비교는 맨 이름·대소문자 무시)
    """
    templates, issues = parse_templates(raw)
    if catalog_columns is None:
        return issues
    catalog = {
        str(t).rsplit(".", 1)[-1].lower(): {str(c).lower() for c in cols}
        for t, cols in catalog_columns.items()
    }
    for tpl in templates:
        if tpl.status != STATUS_ACTIVE:
            continue
        for table in tpl.tables:
            if table.rsplit(".", 1)[-1].strip("`").lower() not in catalog:
                issues.append(f"{tpl.id}: 카탈로그에 없는 테이블 {table}")
        for spec in tpl.slots:
            if spec.type == SLOT_CODE and spec.column:
                table, _, column = spec.column.rpartition(".")
                cols = catalog.get(table.lower())
                if cols is None or column.lower() not in cols:
                    issues.append(f"{tpl.id}: 카탈로그에 없는 code 컬럼 {spec.column}")
    return issues


# ──────────────────────────────────────────────
# 슬롯 형식 검증 · 바인딩
# ──────────────────────────────────────────────


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SlotRejectedError("문자열이 아니다")
    text = str(value).strip()
    if not text:
        raise SlotRejectedError("빈 값")
    if any(ch in _FORBIDDEN_CHARS for ch in text) or any(s in text for s in _FORBIDDEN_SEQS):
        raise SlotRejectedError("금지 문자(따옴표·세미콜론·주석 기호·백슬래시)")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise SlotRejectedError("제어 문자")
    return text


def _norm_date(value: Any, fmt: str | None) -> str:
    text = _scalar_text(value)
    m = _DATE_RE.fullmatch(text)
    if not m:
        raise SlotRejectedError("날짜 형식(YYYY-MM-DD) 아님")
    try:
        day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise SlotRejectedError("없는 날짜") from None
    return day.strftime("%Y%m%d") if fmt == FORMAT_YYYYMMDD else day.isoformat()


def _range_parts(value: Any) -> tuple[Any, Any]:
    if isinstance(value, Mapping):
        return value.get("start"), value.get("end")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    raise SlotRejectedError("기간은 {start, end}여야 한다")


def normalize_slot(
    spec: SlotSpec, value: Any, code_values: Mapping[str, Collection[Any]] | None = None,
) -> dict[str, str]:
    """슬롯 값을 형식 검증·정규화해 ``{자리표: 값}``으로 돌려준다.

    형식 밖이면 ``SlotRejectedError``(메시지에 값 없음).
    """
    if spec.type == SLOT_DATE_RANGE:
        start_raw, end_raw = _range_parts(value)
        start, end = _norm_date(start_raw, spec.format), _norm_date(end_raw, spec.format)
        if start > end:
            raise SlotRejectedError("기간 시작이 끝보다 늦다")
        return {f"{spec.name}_start": start, f"{spec.name}_end": end}
    if spec.type == SLOT_DATE:
        return {spec.name: _norm_date(value, spec.format)}
    text = _scalar_text(value)
    if spec.type == SLOT_CODE:
        allowed = (code_values or {}).get(spec.column or "")
        if not allowed:
            raise SlotRejectedError("허용 코드 목록 없음")
        if len(text) > _CODE_MAX_LEN or text not in {str(v) for v in allowed}:
            raise SlotRejectedError("허용 코드 목록 밖")
        return {spec.name: text}
    pattern = {
        SLOT_CENTER: _CENTER_RE,
        SLOT_DEPT_CODE: _DEPT_CODE_RE,
        SLOT_HOSTNAME: _HOSTNAME_RE,
        SLOT_KEYWORD: _KEYWORD_RE,
    }[spec.type]
    if not pattern.fullmatch(text):
        raise SlotRejectedError(f"{spec.type} 형식 밖")
    return {spec.name: text}


def quote_literal(value: str) -> str:
    """정규화된 슬롯 값을 SQL 문자열 리터럴로 인용한다 — 유일한 인용 지점.

    형식 검증을 통과한 값만 온다. 그래도 따옴표·백슬래시가 섞이면 인용하지 않고 거절한다.
    """
    if "'" in value or "\\" in value:
        raise SlotRejectedError("인용 불가 문자")
    return f"'{value}'"


def _substitute(sql: str, literals: Mapping[str, str]) -> str:
    """코드 구간의 자리표만 인용 리터럴로 바꾼다(리터럴·인용 식별자·주석 안은 그대로)."""
    def _repl(m: re.Match[str]) -> str:
        return literals[m.group(1)] if m.group(1) in literals else m.group(0)

    return "".join(
        _PLACEHOLDER_RE.sub(_repl, seg) if kind == "code" else seg
        for kind, seg in _segments(sql)
    )


def bind_template(
    template: QueryTemplate,
    slots: Mapping[str, Any] | None,
    code_values: Mapping[str, Collection[Any]] | None = None,
) -> BindResult:
    """슬롯 값을 검증해 템플릿 SQL을 조립한다.

    템플릿에 없는 슬롯 키는 무시한다(SQL에 들어가지 않는다). 선택 슬롯이 비면 SQL ``NULL``.
    """
    given = dict(slots or {})
    literals: dict[str, str] = {}
    bound: list[str] = []
    for spec in template.slots:
        value = given.get(spec.name)
        if value is None or (isinstance(value, str) and not value.strip()):
            if spec.required:
                return BindResult(None, REASON_SLOT_MISSING, f"필수 슬롯 {spec.name} 없음")
            literals.update({p: "NULL" for p in spec.placeholders()})
            continue
        try:
            normalized = normalize_slot(spec, value, code_values)
            literals.update({p: quote_literal(v) for p, v in normalized.items()})
        except SlotRejectedError as e:
            return BindResult(None, REASON_SLOT_REJECTED, f"슬롯 {spec.name}: {e}")
        bound.append(spec.name)
    return BindResult(_substitute(template.sql, literals), slot_names=tuple(bound))


_SAMPLE_VALUES: dict[str, Any] = {
    SLOT_CENTER: "A",
    SLOT_DEPT_CODE: "D001",
    SLOT_DATE: "2026-01-01",
    SLOT_DATE_RANGE: {"start": "2026-01-01", "end": "2026-01-31"},
    SLOT_HOSTNAME: "host01",
    SLOT_KEYWORD: "test",
}


def sample_bindings(
    template: QueryTemplate, code_values: Mapping[str, Collection[Any]] | None = None,
) -> list[dict[str, Any]]:
    """모든 슬롯 조합(선택 슬롯 있음/없음)의 대표값 목록 — 모의 DB 실행 검증용.

    code 슬롯은 허용 목록의 첫 값을 쓴다. 목록이 없으면 그 슬롯은 대표값을 만들 수 없어 필수면
    빈 목록을 돌려준다(런타임과 같은 규칙 — 목록 없는 code 슬롯은 바인딩이 거절된다).
    """
    choices: list[list[tuple[str, Any]]] = []
    for spec in template.slots:
        if spec.type == SLOT_CODE:
            allowed = list((code_values or {}).get(spec.column or "") or [])
            sample: Any = str(allowed[0]) if allowed else None
        else:
            sample = _SAMPLE_VALUES[spec.type]
        options: list[tuple[str, Any]] = []
        if sample is not None:
            options.append((spec.name, sample))
        if not spec.required:
            options.append((spec.name, None))
        if not options:
            return []
        choices.append(options)
    combos: list[dict[str, Any]] = []
    for combo in itertools.product(*choices):
        combos.append({name: value for name, value in combo if value is not None})
    return combos
