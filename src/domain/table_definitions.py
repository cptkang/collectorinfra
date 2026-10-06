"""테이블 정의 자산 — 테이블마다 「관리하는 정보」 형식·결정적 검증·가져오기.

D-305 ① · plans/138 W3.

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
| `group` | — | 관리 화면 묶음용 문자열(프롬프트 미사용) |

- **금지 텍스트**(프롬프트 주입 방지): 중괄호 · 코드 펜스 · 닫히지 않은 백틱 · SQL 문 키워드를 담은
  백틱 구간. 백틱으로 감싼 식별자는 허용한다. 제어문자는 거절하고 연속 공백·줄바꿈은 한 칸으로
  접는다.
- **값(표본·코드값)은 정의에 넣지 않는다** — 형식이 그런 칸을 두지 않는다.
- 테이블 이름 비교는 맨 이름 소문자(`bare_name`)로 한다. 정규화한 결과의 키는 스키마 쪽 테이블 키다.

계층: domain — 순수 함수 · I/O·LLM 0 · 표준 라이브러리와 domain만 import · 스키마 리터럴 0.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.schema_snapshot import bare_name

#: 프로필 키
PROFILE_KEY = "table_definitions"

#: 테이블 성격 — 시드 머리 주석의 목록(`신청·처리`·`기준·코드`는 한 값이다)
KINDS: tuple[str, ...] = (
    "현행", "수집적재", "수집이력", "변경이력", "신청·처리", "점검", "매핑", "기준·코드",
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
KEY_COLUMNS_MAX = 10
#: 가져오기 YAML 본문 상한(문자 수 — API 요청 모델과 서비스가 함께 쓴다)
IMPORT_MAX_CHARS = 1_000_000

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


def _forbidden_reason(text: str) -> str | None:
    """프롬프트 주입 금지 텍스트 사유(없으면 None)."""
    if "{" in text or "}" in text:
        return "중괄호({ })를 쓸 수 없습니다"
    if "```" in text:
        return "코드 펜스(```)를 쓸 수 없습니다"
    if text.count("`") % 2:
        return "닫히지 않은 백틱(`)이 있습니다"
    for span in _BACKTICK_SPAN_RE.findall(text):
        found = _SQL_KEYWORD_RE.search(span)
        if found is not None:
            return f"백틱 구간에 SQL 키워드({found.group(1).upper()})를 쓸 수 없습니다"
    return None


def _clean_text(value: Any, label: str) -> tuple[str, str | None]:
    """문자열 확인 · 제어문자 거절 · 연속 공백·줄바꿈을 한 칸으로 접는다 → ``(정규화 값, 오류)``."""
    if not isinstance(value, str):
        return "", f"{label}은(는) 문자열이어야 합니다"
    if any(unicodedata.category(ch) == "Cc" and ch not in _ALLOWED_CONTROL for ch in value):
        return "", f"{label}에 제어문자가 있습니다"
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
        text, error = _clean_text(group, "group")
        if error:
            problems.append(error)
        elif text:
            fields["group"] = text

    kind = raw.get("kind")
    if not _is_absent(kind):
        if not isinstance(kind, str) or kind.strip() not in KINDS:
            problems.append(f"kind는 {'·'.join(KINDS)} 중 하나여야 합니다: {kind!r}")
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
        problems.append(f"origin은 {'·'.join(ORIGINS)} 중 하나여야 합니다: {origin!r}")
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
