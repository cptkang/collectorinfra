"""이름 칸 등호 0행 재생성 판정 (plans/149 W3 · G-4 (b) · D-004 취지 · D-297 부기).

**무엇을 하나.** 실행한 SQL이 0행이고 WHERE에 **이름 칸**에 대한 문자열 등호
(`= '값'`)·`IN ('값', …)`이 있으면, 「이 칸은 핵심어 부분 일치로 다시 써라」 힌트 문구를
돌려준다. 호출부(단일 그래프 경로 `result_organizer` · 2단 task 루프
`subagents._run_single_db_pipeline`)가 이 문구를 재시도 사유(`error_message`)로 실어 SQL을
**1회만** 다시 만든다. 재생성도 0행이면 그대로 0행을 답한다 — 결과를 넓히거나 숨기지 않는다.

**이름 칸은 데이터에서 읽는다.** 그 DB의 설명 정본 파일
(`config/knowledge/{db_id}/column_descriptions.yaml` · D-316 ④)에서 설명에 「부분 일치」가
적힌 칸이다. 칸 이름 목록을 코드에 두지 않는다(D-088 · `overfit_check`). 런타임 상태의 컬럼
설명(Redis)이 아니라 정본 파일을 직접 읽는 까닭: 정본 파일은 Redis에 없는 키만 채우므로(HSETNX)
이미 설명(DB 주석 등)이 있는 칸에는 표시가 런타임 설명에 닿지 않는다. 정본 파일이 없는 DB(폴스타
계열)는 이름 칸이 0개라 판정이 언제나 None이다 — 동작 비트 동일. 표시 판정은 설명에
「부분 일치」 낱말이 **들어 있는지만** 본다 — 「부분 일치로 걸지 않는다」 같은 부정문도 표시가
된다(정본 작성자 주의).

**1회 제한.** 이번 파이프라인의 실행 기록(`query_attempts`)에서 성공한 0행 실행이 **지금 한
건뿐**일 때만 발동한다 — 재생성한 SQL이 또 0행이면(두 번째 0행) 발동하지 않는다. 재시도 예산
안에서만 발동한다(남은 횟수 ≥ `min_retries_left`). 멀티 DB 실행(`is_multi_db`)은 대상이 아니다.

**경로 범위(검증 149 L-1).** 2단 task 루프는 task마다 실행 기록을 비우므로 「이번 질의」 기준이다.
단일 그래프 경로(3·4단)는 실행 기록이 스레드에 누적된다(후속 턴 델타가 `query_attempts`를 비우지
않는다 · 턴 경계 표지 없음) — 앞 턴에 성공한 0행 실행이 있으면 이번 턴 첫 0행에도 발동하지 않는다.
상태 필드 없이 턴을 가를 수 없어 그대로 둔다(덜 발동하는 쪽 — 0행 답은 그대로 나간다).

엔진 분기가 없다 — 판정은 SQL 텍스트와 설명 데이터만 본다(D-089: DB별 SQL 특화 로직 아님).
LLM을 쓰지 않는다.

계층: application (`src.nodes`).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from src.schema_cache.knowledge_descriptions import KNOWLEDGE_ROOT, load_knowledge_descriptions

logger = logging.getLogger(__name__)

#: 2단 task 루프(`subagents._run_single_db_pipeline`)가 판정을 마쳤다는 표지 — 그 루프의
#: 상태 dict에만 싣는다(`AgentState` 필드가 아니다). 있으면 판정은 None — 루프 뒤 결과 정리가
#: 같은 판정을 다시 내지 않게 한다. 그래프 경로는 실행 기록(성공한 0행 실행 수)만으로 1회를 지킨다.
NAME_MATCH_CHECKED_KEY = "name_match_retry_checked"

#: 설명에서 이름 칸을 가리키는 표시 — 「이 칸은 부분 일치로 건다」.
PARTIAL_MATCH_MARKERS: tuple[str, ...] = ("부분 일치", "부분일치")

#: 식별자 — 백틱·큰따옴표 인용 또는 맨 이름(유니코드 글자 시작).
_IDENT = r"(?:`[^`]+`|\"[^\"]+\"|[^\W\d]\w*)"

#: 마스킹 대상 — 인용 식별자는 그대로 두고, 문자열 리터럴 내부와 주석만 가린다.
_MASK_RE = re.compile(r"`[^`]*`|\"[^\"]*\"|'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/", re.DOTALL)

#: FROM/JOIN 뒤 테이블(스키마 한정 허용)과 별칭.
_TABLE_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+(?:{_IDENT}\s*\.\s*)?(?P<table>{_IDENT})"
    rf"(?:\s+(?:AS\s+)?(?P<alias>{_IDENT}))?",
    re.IGNORECASE,
)

#: 칸 = '값' · 칸 IN ('값', …) — 빈 리터럴(`''`)은 대상이 아니다. `<=`·`>=`·`!=`·`<>`는 칸 뒤에
#: 다른 글자가 와서 걸리지 않는다.
_PREDICATE_RE = re.compile(
    rf"(?<![\w`\".])(?:(?P<qual>{_IDENT})\s*\.\s*)?(?P<col>{_IDENT})"
    r"\s*(?:=\s*'(?!')|\bIN\s*\(\s*'(?!'))",
    re.IGNORECASE,
)

#: CASE … END 경계 — 이 구간의 조건(선택 목록 등)은 조회 조건이 아니다(검증 149 L-2).
_CASE_TOKEN_RE = re.compile(r"\b(CASE|END)\b", re.IGNORECASE)

#: 별칭 자리에 올 수 없는 키워드(별칭 없는 FROM/JOIN 다음 낱말).
_NOT_ALIAS = frozenset({
    "where", "join", "on", "left", "right", "inner", "outer", "cross", "full", "natural",
    "group", "order", "limit", "union", "having", "using", "set", "fetch", "offset",
    "window", "for", "straight_join", "intersect", "except", "lateral",
})


def _norm(ident: str) -> str:
    """인용 부호를 벗기고 NFC 정규화 + 대소문자 무시."""
    text = ident.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "`\"":
        text = text[1:-1]
    return unicodedata.normalize("NFC", text).casefold()


def name_match_columns(descriptions: Mapping[str, str] | None) -> dict[str, frozenset[str]]:
    """설명에 부분 일치 표시가 있는 칸 — `{칸: {테이블, …}}`(정규화한 이름).

    키는 `table.column`(스키마 한정이면 마지막 두 조각)이다. 형식이 맞지 않는 항목은 건너뛴다.
    """
    out: dict[str, set[str]] = {}
    for key, text in (descriptions or {}).items():
        if not isinstance(text, str) or not any(m in text for m in PARTIAL_MATCH_MARKERS):
            continue
        table, _, column = str(key).rpartition(".")
        if not table or not column:
            continue
        out.setdefault(_norm(column), set()).add(_norm(table.rpartition(".")[2]))
    return {col: frozenset(tables) for col, tables in out.items()}


def _mask(sql: str) -> str:
    """문자열 리터럴 내부·주석을 같은 길이로 가린다(리터럴 따옴표와 비어 있음은 남긴다)."""
    def _sub(m: re.Match[str]) -> str:
        token = m.group(0)
        if token[0] == "'":
            return "'" + "x" * (len(token) - 2) + "'"
        if token[0] in "`\"":
            return token
        return " " * len(token)

    return _MASK_RE.sub(_sub, sql)


def _blank_case(masked: str) -> str:
    """`CASE … END` 구간(중첩 포함)을 같은 길이 공백으로 가린다 — 닫히지 않으면 끝까지 가린다."""
    out = list(masked)
    depth = 0
    start = 0
    for m in _CASE_TOKEN_RE.finditer(masked):
        if m.group(1).upper() == "CASE":
            if depth == 0:
                start = m.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                out[start:m.end()] = " " * (m.end() - start)
    if depth:
        out[start:] = " " * (len(out) - start)
    return "".join(out)


def find_name_equalities(sql: str, columns: Mapping[str, frozenset[str]]) -> list[str]:
    """SQL에서 이름 칸에 건 문자열 등호·IN의 칸 이름(SQL에 적힌 모양 · 중복 제거)을 찾는다.

    한정자(`별칭.칸`)가 FROM/JOIN 테이블로 풀리면 그 테이블이 표시된 테이블일 때만 센다. 풀리지
    않는 한정자(파생 테이블·CTE 별칭)와 한정자 없는 칸은 칸 이름만으로 센다. `CASE … END` 안의
    조건(`CASE WHEN … AND 칸 = '값'` 포함)은 조회 조건이 아니라 세지 않는다.
    """
    if not sql or not columns:
        return []
    masked = _mask(sql)
    aliases: dict[str, str] = {}
    for m in _TABLE_RE.finditer(masked):
        table = _norm(m.group("table"))
        aliases.setdefault(table, table)
        alias = m.group("alias")
        if alias and _norm(alias) not in _NOT_ALIAS:
            aliases[_norm(alias)] = table
    hits: list[str] = []
    for m in _PREDICATE_RE.finditer(_blank_case(masked)):
        tables = columns.get(_norm(m.group("col")))
        if tables is None:
            continue
        qual = m.group("qual")
        if qual is not None:
            resolved = aliases.get(_norm(qual))
            if resolved is not None and resolved not in tables:
                continue
        name = m.group("col").strip()
        if name not in hits:
            hits.append(name)
    return hits


def build_name_match_hint(columns: Sequence[str]) -> str:
    """재생성 힌트 문구 — 재시도 사유(`error_message`)로 SQL 생성기에 그대로 실린다."""
    names = "·".join(columns)
    return (
        f"이전 SQL은 0행이었습니다. 이름 칸 {names}에 등호(=)·IN으로 값을 그대로 걸었습니다 — "
        "이 칸은 컬럼 설명이 부분 일치로 표시한 이름 칸입니다. "
        "이 칸 조건만 질문에서 고유한 핵심어를 뽑아 LIKE '%핵심어%' 부분 일치로 다시 쓰고, "
        "다른 조건·조인·선택 칼럼은 그대로 두세요. "
        "컬럼 설명이 이 칸의 사용 범위를 제한하면(예: 특정 질문에만 쓴다) 그 설명을 따르세요."
    )


def _zero_row_successes(attempts: Any) -> int:
    """이번 파이프라인에서 성공한 0행 실행 수."""
    count = 0
    for a in attempts or []:
        if isinstance(a, Mapping) and a.get("success") and not a.get("row_count"):
            count += 1
    return count


def _knowledge_columns(db_id: str, knowledge_root: Path) -> dict[str, frozenset[str]]:
    """설명 정본 파일에서 이름 칸을 읽는다(파일 없음·형식 오류·이상한 db_id면 빈 dict)."""
    try:
        descriptions = load_knowledge_descriptions(knowledge_root, db_id)
    except ValueError as e:
        logger.debug("이름 칸 판정 — 설명 정본 경로 해석 불가(db_id=%s): %s", db_id, e)
        return {}
    return name_match_columns(descriptions)


def name_match_retry_hint(
    state: Mapping[str, Any],
    *,
    max_retry: int,
    min_retries_left: int = 1,
    knowledge_root: Path | None = None,
) -> str | None:
    """0행 실행 뒤 이름 칸 부분 일치 재생성 힌트를 돌려준다(발동 조건이 아니면 None).

    발동 조건(모두): 결과 0행 · 단일 DB · 판정 표지(`NAME_MATCH_CHECKED_KEY`) 없음 · 남은 재시도
    횟수(`max_retry - retry_count`) ≥ `min_retries_left` · 성공한 0행 실행이 이번 한 건뿐 · 활성 DB
    설명 정본에 이름 칸 표시가 있음 · 실행 SQL에 그 칸의 문자열 등호·IN이 있음.

    Args:
        state: 파이프라인 상태(`query_results`·`generated_sql`·`query_attempts`·`retry_count`·
            `active_db_id`·`is_multi_db`를 읽는다)
        max_retry: SQL 재생성 예산(`query.max_retry_count`)
        min_retries_left: 발동에 필요한 남은 재시도 횟수. 2단 루프는 재생성이 실패하면 0행 답으로
            되돌리므로 1, 그래프 경로는 되돌릴 수단이 없어 2(재생성 실패 1회를 예산 안에서 흡수 ·
            검증 149 M-1)
        knowledge_root: 설명 정본 루트(None이면 저장소 `config/knowledge`)
    """
    if state.get("query_results") or state.get("is_multi_db") or state.get(NAME_MATCH_CHECKED_KEY):
        return None
    if _zero_row_successes(state.get("query_attempts")) != 1:
        return None
    db_id = str(state.get("active_db_id") or "")
    sql = str(state.get("generated_sql") or "")
    if not db_id or not sql:
        return None
    columns = _knowledge_columns(db_id, knowledge_root or KNOWLEDGE_ROOT)
    if not columns:
        return None
    hits = find_name_equalities(sql, columns)
    if not hits:
        return None
    if max_retry - int(state.get("retry_count") or 0) < min_retries_left:
        logger.info(
            "이름 칸 등호 0행 — 남은 재시도 예산 부족으로 부분 일치 재생성 생략(plans/149 W3): "
            "db=%s columns=%s retry=%s/%s 필요=%s",
            db_id, hits, state.get("retry_count", 0), max_retry, min_retries_left,
        )
        return None
    logger.info(
        "이름 칸 등호 0행 — 부분 일치 힌트로 1회 재생성(plans/149 W3): db=%s columns=%s retry=%s",
        db_id, hits, state.get("retry_count", 0),
    )
    return build_name_match_hint(hits)
