"""테이블 정의 자산 — 테이블마다 「관리하는 정보」 형식·결정적 검증·가져오기.

D-308 ① · plans/139 W3.

**무엇을 하나.** 관리자가 「DB 구조」 탭에서 초안(가져오기 · 테이블 주석 · LLM 묶음 초안)을 만들어
검토·승인하는 프로필 키 `table_definitions`의 한 항목(테이블당)을 검증하고 정규화한다. 질의 경로는
승인된 정의를 읽기만 한다(D-227) — 이 모듈은 그 형식의 단일 출처다.

**항목 형식(테이블당).**

| 필드 | 필수 | 검증 |
|---|---|---|
| `manages` | ✓ | 1~300자 · 금지 텍스트 없음 |
| `kind` | — | `KINDS` 값만 |
| `key_columns` | — | 그 테이블에 실존하는 컬럼 · ≤ 10 |
| `related` | — | ``{상대 테이블: 연결 설명}`` — 상대 실존 · 설명 ≤ 100자 · 금지 텍스트 없음 |
| `notes` | — | ≤ 300자 · 금지 텍스트 없음 |
| `origin` | ✓ | `ORIGINS` 값만 |
| `group` | — | 관리 화면 묶음용 문자열 · ≤ 30자 · 금지 텍스트 없음(0개 안내 문구에 실린다) |

- **금지 텍스트**(프롬프트 주입 방지): 중괄호 · 코드 펜스(```` ``` ```` · ``~~~``) ·
  닫히지 않은 백틱 · SQL 문 키워드를 담은 백틱 구간. 검사는 NFKC 정규화 뒤에 한다(전각 문자
  우회 차단). 백틱으로 감싼 식별자는 허용한다. 제어문자(Cc)·서식 문자(Cf — 영폭 공백 등)는
  거절하고 연속 공백·줄바꿈은 한 칸으로 접는다.
- **값(표본·코드값)은 정의에 넣지 않는다** — 형식이 그런 칸을 두지 않는다.
- 테이블 이름 비교는 맨 이름 소문자(`bare_name`)로 한다. 정규화한 결과의 키는 스키마 쪽 테이블 키다.
- 오류 문구는 값을 `describe_value`로 짧게 싣는다 — 별칭으로 부푼 중첩 값을 펼치지 않는다.
- 질의 경로(선별 프롬프트·용도 블록)는 승인 검증을 거치지 않은 파일 편집에 대비해
  `sanitize_definitions_for_prompt`로 한 번 더 거른다(두 소비처가 같은 함수 — D-066).

계층: domain — 순수 함수 · I/O·LLM 0 · 표준 라이브러리와 domain만 import · 스키마 리터럴 0.
"""

from __future__ import annotations

import re
import reprlib
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.schema_snapshot import bare_name

#: 프로필 키
PROFILE_KEY = "table_definitions"

#: 테이블 성격 「수집적재」 — 선별(LLM 프롬프트 규칙 · 어휘 대체)이 고르지 않는다
KIND_COLLECT_LOAD = "수집적재"
#: 테이블 성격 — 시드 머리 주석의 목록(`신청·처리`·`기준·코드`는 한 값이다)
KINDS: tuple[str, ...] = (
    "현행", KIND_COLLECT_LOAD, "수집이력", "변경이력", "신청·처리", "점검", "매핑", "기준·코드",
    "로그", "게시판", "통계", "설정",
)
#: 정의 출처 — 사람이 쓴 값(`manual`)은 재생성 병합에서 보존한다
ORIGINS: tuple[str, ...] = ("import", "llm", "comment", "manual")
ORIGIN_IMPORT = "import"
ORIGIN_LLM = "llm"
ORIGIN_COMMENT = "comment"
ORIGIN_MANUAL = "manual"

MANAGES_MAX_CHARS = 300
NOTES_MAX_CHARS = 300
RELATED_TEXT_MAX_CHARS = 100
GROUP_MAX_CHARS = 30
KEY_COLUMNS_MAX = 10
#: 가져오기 YAML 본문 상한(문자 수 — API 요청 모델과 서비스가 함께 쓴다)
IMPORT_MAX_CHARS = 1_000_000
#: 오류 문구·표시용 사본에 싣는 값 발췌 상한(문자)
VALUE_EXCERPT_MAX_CHARS = 60
#: 프롬프트용 정제에서 대표 컬럼·연결 상대 이름 1개의 길이 상한(파일 직접 편집 방어)
_PROMPT_IDENTIFIER_MAX_CHARS = 128

#: 정규화 결과의 필드 순서(없는 선택 필드는 싣지 않는다)
FIELDS: tuple[str, ...] = (
    "group", "kind", "manages", "key_columns", "related", "notes", "origin",
)
#: 가져오기 문서에서 읽는 필드(`origin`은 가져오기가 정한다 · 그 밖의 키는 무시)
IMPORT_FIELDS: tuple[str, ...] = (
    "group", "kind", "manages", "key_columns", "related", "notes",
)

# 백틱 구간 안에서 거절하는 SQL 문 키워드(영문 경계 — `update_time` 같은 식별자는 통과)
_SQL_KEYWORD_RE = re.compile(
    r"(?<![A-Za-z0-9_])(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|"
    r"REVOKE|EXEC|EXECUTE|CALL|UNION)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_BACKTICK_SPAN_RE = re.compile(r"`([^`]*)`")
_ALLOWED_CONTROL = frozenset("\t\n\r")
_CODE_FENCES: tuple[str, ...] = ("```", "~~~")

# 값 발췌 — 앞쪽 몇 개 원소·두 단계까지만 본다(별칭으로 부푼 중첩 값도 펼치지 않는다)
_EXCERPT_REPR = reprlib.Repr()
_EXCERPT_REPR.maxlevel = 2
_EXCERPT_REPR.maxlist = _EXCERPT_REPR.maxtuple = _EXCERPT_REPR.maxdict = 3
_EXCERPT_REPR.maxset = _EXCERPT_REPR.maxfrozenset = 3
_EXCERPT_REPR.maxstring = _EXCERPT_REPR.maxother = 30


def describe_value(value: Any) -> str:
    """오류 문구·표시용 사본에 싣는 값 표기(≤ `VALUE_EXCERPT_MAX_CHARS`자 남짓).

    문자열은 `repr`(길면 앞부분 + ``…``), 그 밖의 값은 ``타입 이름 + 짧은 발췌``다. 가져오기 YAML의
    별칭으로 부푼 중첩 값도 앞쪽 몇 개 원소만 보므로 펼치지 않는다.
    """
    if isinstance(value, str):
        if len(value) <= VALUE_EXCERPT_MAX_CHARS:
            return repr(value)
        return repr(value[:VALUE_EXCERPT_MAX_CHARS]) + "…"
    text = f"{type(value).__name__} {_EXCERPT_REPR.repr(value)}"
    if len(text) > VALUE_EXCERPT_MAX_CHARS:
        text = text[: VALUE_EXCERPT_MAX_CHARS - 1] + "…"
    return text


def _forbidden_reason(text: str) -> str | None:
    """프롬프트 주입 금지 텍스트 사유(없으면 None) — NFKC 정규화한 글로 본다(전각 우회 차단)."""
    folded = unicodedata.normalize("NFKC", text)
    if "{" in folded or "}" in folded:
        return "중괄호({ })를 쓸 수 없습니다"
    for fence in _CODE_FENCES:
        if fence in folded:
            return f"코드 펜스({fence})를 쓸 수 없습니다"
    if folded.count("`") % 2:
        return "닫히지 않은 백틱(`)이 있습니다"
    for span in _BACKTICK_SPAN_RE.findall(folded):
        found = _SQL_KEYWORD_RE.search(span)
        if found is not None:
            return f"백틱 구간에 SQL 키워드({found.group(1).upper()})를 쓸 수 없습니다"
    return None


def _clean_text(value: Any, label: str) -> tuple[str, str | None]:
    """문자열 확인 · 제어문자·서식 문자 거절 · 연속 공백·줄바꿈을 한 칸으로 접는다.

    Returns:
        ``(정규화 값, 오류)`` — 서식 문자(Cf)는 영폭 공백처럼 보이지 않게 금지 검사를 피하는 데 쓰일
        수 있어 거절한다.
    """
    if not isinstance(value, str):
        return "", f"{label}은(는) 문자열이어야 합니다"
    if any(unicodedata.category(ch) == "Cc" and ch not in _ALLOWED_CONTROL for ch in value):
        return "", f"{label}에 제어문자가 있습니다"
    if any(unicodedata.category(ch) == "Cf" for ch in value):
        return "", f"{label}에 보이지 않는 서식 문자(영폭 공백 등)가 있습니다"
    return " ".join(value.split()), None


def _checked_text(
    value: Any, label: str, max_chars: int, *, required: bool
) -> tuple[str, list[str]]:
    """설명 텍스트 1칸 — 정규화 · 빈 값(필수면 거절) · 길이 상한 · 금지 텍스트."""
    text, error = _clean_text("" if value is None else value, label)
    if error:
        return "", [error]
    if not text:
        return "", [f"{label}이(가) 비었습니다"] if required else []
    problems: list[str] = []
    if len(text) > max_chars:
        problems.append(f"{label}은(는) {max_chars}자 이하여야 합니다({len(text)}자)")
    reason = _forbidden_reason(text)
    if reason:
        problems.append(f"{label}: {reason}")
    return text, problems


def _is_absent(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _key_columns(
    value: Any, columns: Sequence[str]
) -> tuple[list[str], list[str]]:
    """대표 컬럼 — 목록 · 문자열 · 실존(대소문자 무시 · 스키마 원형 정규화) · 중복 제거 · 상한."""
    if not isinstance(value, (list, tuple)):
        return [], ["key_columns는 목록이어야 합니다"]
    by_fold = {str(c).casefold(): str(c) for c in columns}
    out: list[str] = []
    unknown: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return [], ["key_columns 항목은 비지 않은 문자열이어야 합니다"]
        actual = by_fold.get(item.strip().casefold())
        if actual is None:
            unknown.append(item.strip())
        elif actual not in out:
            out.append(actual)
    problems: list[str] = []
    if unknown:
        problems.append(f"테이블에 없는 key_columns: {', '.join(unknown)}")
    if len(out) > KEY_COLUMNS_MAX:
        problems.append(f"key_columns는 {KEY_COLUMNS_MAX}개 이하여야 합니다({len(out)}개)")
    return out, problems


def _related(
    value: Any, table_index: Mapping[str, str]
) -> tuple[dict[str, str], list[str]]:
    """연결 후보 — 매핑 · 상대 테이블 실존(스키마 키로 정규화) · 설명 텍스트 검증."""
    if not isinstance(value, Mapping):
        return {}, ["related는 {상대 테이블: 연결 설명} 매핑이어야 합니다"]
    out: dict[str, str] = {}
    problems: list[str] = []
    unknown: list[str] = []
    for other, text in value.items():
        target = table_index.get(bare_name(str(other)))
        if target is None:
            unknown.append(str(other))
            continue
        cleaned, errors = _checked_text(
            text, f"related[{other}]", RELATED_TEXT_MAX_CHARS, required=True,
        )
        problems.extend(errors)
        if not errors:
            out[target] = cleaned
    if unknown:
        problems.insert(0, f"스키마에 없는 related 테이블: {', '.join(unknown)}")
    return out, problems


def _validate_entry(
    raw: Any, columns: Sequence[str], table_index: Mapping[str, str]
) -> tuple[dict[str, Any], list[str]]:
    """정의 1건 검증 → ``(정규화 항목, 오류)`` (오류가 있으면 항목은 쓰지 않는다)."""
    if not isinstance(raw, Mapping):
        return {}, ["정의가 매핑이 아닙니다"]
    problems: list[str] = []
    fields: dict[str, Any] = {}

    group = raw.get("group")
    if not _is_absent(group):
        text, errors = _checked_text(group, "group", GROUP_MAX_CHARS, required=False)
        problems.extend(errors)
        if text and not errors:
            fields["group"] = text

    kind = raw.get("kind")
    if not _is_absent(kind):
        if not isinstance(kind, str) or kind.strip() not in KINDS:
            problems.append(
                f"kind는 {'·'.join(KINDS)} 중 하나여야 합니다: {describe_value(kind)}"
            )
        else:
            fields["kind"] = kind.strip()

    manages, errors = _checked_text(
        raw.get("manages"), "manages", MANAGES_MAX_CHARS, required=True,
    )
    problems.extend(errors)
    fields["manages"] = manages

    if raw.get("key_columns") is not None:
        keys, errors = _key_columns(raw.get("key_columns"), columns)
        problems.extend(errors)
        if keys:
            fields["key_columns"] = keys

    if raw.get("related") is not None:
        related, errors = _related(raw.get("related"), table_index)
        problems.extend(errors)
        if related:
            fields["related"] = related

    if not _is_absent(raw.get("notes")):
        notes, errors = _checked_text(raw.get("notes"), "notes", NOTES_MAX_CHARS, required=False)
        problems.extend(errors)
        if notes:
            fields["notes"] = notes

    origin = raw.get("origin")
    if not isinstance(origin, str) or origin not in ORIGINS:
        problems.append(
            f"origin은 {'·'.join(ORIGINS)} 중 하나여야 합니다: {describe_value(origin)}"
        )
    else:
        fields["origin"] = origin

    return {k: fields[k] for k in FIELDS if k in fields}, problems


def validate_table_definitions(
    defs: Mapping[str, Any], table_columns: Mapping[str, Sequence[str]]
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """테이블 정의 묶음을 결정적으로 검증·정규화한다.

    Args:
        defs: ``{테이블: 정의}`` — 테이블 이름은 스키마 접두·대소문자가 달라도 된다
        table_columns: ``{스키마 테이블 키: [컬럼 이름…]}`` — 실존 판정 기준

    Returns:
        ``(유효 정의, 오류)`` — 유효 정의는 스키마 테이블 키로, 오류는 입력 테이블 이름으로 묶는다.
        한 테이블에 오류가 하나라도 있으면 그 테이블은 유효 정의에 넣지 않는다. 같은 테이블을 두 번
        정의하면 두 번째부터 오류다.
    """
    table_index: dict[str, str] = {}
    for key in table_columns:
        table_index.setdefault(bare_name(str(key)), str(key))
    valid: dict[str, dict[str, Any]] = {}
    errors: dict[str, list[str]] = {}
    seen: set[str] = set()
    for name, raw in defs.items():
        label = str(name)
        target = table_index.get(bare_name(label))
        if target is None:
            errors[label] = ["스키마에 없는 테이블입니다"]
            continue
        if target in seen:
            errors[label] = [f"같은 테이블을 두 번 정의했습니다({target})"]
            continue
        seen.add(target)
        entry, problems = _validate_entry(raw, table_columns[target], table_index)
        if problems:
            errors[label] = problems
        else:
            valid[target] = entry
    return valid, errors


def parse_import_document(document: Any) -> dict[str, Any]:
    """가져오기 문서(시드 YAML을 읽은 값)에서 테이블별 정의 원본을 꺼낸다(검증 전).

    최상위 `tables:` 매핑의 테이블마다 `IMPORT_FIELDS`만 읽고 `origin`은 `import`로 정한다.
    `allowed` 등 그 밖의 키는 무시한다(조회 대상은 `allowed_tables` 자산 소관). 매핑이 아닌 항목은
    검증이 사유를 남기도록 그대로 넘긴다.

    Raises:
        ValueError: 최상위 `tables:` 매핑이 없음
    """
    tables = document.get("tables") if isinstance(document, Mapping) else None
    if not isinstance(tables, Mapping):
        raise ValueError("가져오기 문서에 최상위 `tables:` 매핑이 없습니다")
    out: dict[str, Any] = {}
    for name, raw in tables.items():
        if isinstance(raw, Mapping):
            item = {k: raw[k] for k in IMPORT_FIELDS if k in raw}
            item["origin"] = ORIGIN_IMPORT
            out[str(name)] = item
        else:
            out[str(name)] = raw
    return out


def has_table_definitions(profile: Mapping[str, Any] | None) -> bool:
    """프로필이 비지 않은 `table_definitions` 매핑을 갖는지."""
    if not isinstance(profile, Mapping):
        return False
    defs = profile.get(PROFILE_KEY)
    return isinstance(defs, Mapping) and bool(defs)


def defined_table_count(definitions: Any, tables: Sequence[str]) -> int:
    """`tables` 중 정의가 있는 테이블 수(맨 이름 비교 · 정의가 매핑이 아니면 0)."""
    if not isinstance(definitions, Mapping):
        return 0
    defined = {bare_name(str(k)) for k, v in definitions.items() if isinstance(v, Mapping)}
    return len({bare_name(str(t)) for t in tables} & defined)


def _prompt_entry(raw: Any) -> dict[str, Any] | None:
    """정의 1건을 프롬프트용으로 정제한다 — 한 칸이라도 어긋나면 None(그 테이블 정의를 쓰지 않는다).

    승인 검증과 같은 텍스트 규칙(줄 접기 · 길이 상한 · 금지 텍스트 · kind 허용값 · 대표 컬럼 수)을
    적용한다. 스키마를 보지 않으므로 컬럼·상대 테이블 실존은 따지지 않고 이름 길이만 묶는다.
    `origin`은 프롬프트에 싣지 않으므로 필수로 보지 않는다(허용값일 때만 남긴다).
    """
    if not isinstance(raw, Mapping):
        return None
    out: dict[str, Any] = {}

    manages, errors = _checked_text(
        raw.get("manages"), "manages", MANAGES_MAX_CHARS, required=True,
    )
    if errors:
        return None
    out["manages"] = manages

    if not _is_absent(raw.get("group")):
        group, errors = _checked_text(raw.get("group"), "group", GROUP_MAX_CHARS, required=False)
        if errors:
            return None
        out["group"] = group

    kind = raw.get("kind")
    if not _is_absent(kind):
        if not isinstance(kind, str) or kind.strip() not in KINDS:
            return None
        out["kind"] = kind.strip()

    keys = raw.get("key_columns")
    if keys is not None:
        if not isinstance(keys, (list, tuple)) or len(keys) > KEY_COLUMNS_MAX:
            return None
        cleaned: list[str] = []
        for item in keys:
            text, errors = _checked_text(
                item, "key_columns", _PROMPT_IDENTIFIER_MAX_CHARS, required=True,
            )
            if errors:
                return None
            cleaned.append(text)
        if cleaned:
            out["key_columns"] = cleaned

    related = raw.get("related")
    if related is not None:
        if not isinstance(related, Mapping):
            return None
        pairs: dict[str, str] = {}
        for other, text in related.items():
            name, errors = _checked_text(
                other, "related", _PROMPT_IDENTIFIER_MAX_CHARS, required=True,
            )
            note, note_errors = _checked_text(
                text, "related", RELATED_TEXT_MAX_CHARS, required=True,
            )
            if errors or note_errors:
                return None
            pairs[name] = note
        if pairs:
            out["related"] = pairs

    if not _is_absent(raw.get("notes")):
        notes, errors = _checked_text(raw.get("notes"), "notes", NOTES_MAX_CHARS, required=False)
        if errors:
            return None
        out["notes"] = notes

    origin = raw.get("origin")
    if isinstance(origin, str) and origin in ORIGINS:
        out["origin"] = origin
    return {k: out[k] for k in FIELDS if k in out}


def sanitize_definitions_for_prompt(
    defs: Any,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """질의 경로가 프롬프트에 싣기 전 테이블 정의를 거른다 — 선별 프롬프트·용도 블록 공용(D-066).

    승인 경로(`validate_table_definitions`)를 거친 정의는 그대로 통과한다(같은 값 · 같은 필드 순서).
    프로필 파일을 직접 고쳐 검증을 거치지 않은 정의는 줄을 접고, 길이 상한(manages·notes
    `MANAGES_MAX_CHARS`·`NOTES_MAX_CHARS` · related `RELATED_TEXT_MAX_CHARS` · group
    `GROUP_MAX_CHARS`)·kind 허용값·금지 텍스트 중 하나라도 어긋나면 그 테이블 정의를 통째로 뺀다.

    Args:
        defs: 프로필 `table_definitions` 값(매핑이 아니면 정의 없음으로 본다)

    Returns:
        ``(정제한 정의, 뺀 테이블 이름 목록)`` — 키는 입력 키 그대로다. 호출부가 뺀 목록을
        로그로 남긴다.
    """
    if not isinstance(defs, Mapping):
        return {}, []
    clean: dict[str, dict[str, Any]] = {}
    dropped: list[str] = []
    for name, raw in defs.items():
        entry = _prompt_entry(raw)
        if entry is None:
            dropped.append(str(name))
        else:
            clean[str(name)] = entry
    return clean, dropped
