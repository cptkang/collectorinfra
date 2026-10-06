"""정의 기반 LLM 테이블 선별 — 단일·멀티 경로 공용 (plans/139 W4 · D-308 G-1~G-3).

**발동**: 그 DB 프로필에 테이블 정의(`table_definitions`)가 있고 알람 의도가 아닐 때
(`uses_definition_selection`). 단일 경로(`schema_analyzer`)와 멀티 경로
(`multi_db_executor._analyze_schema`)가 같은 함수 `select_tables`를 부른다 — 같은 입력이면 같은
선별이다(D-066).

**흐름**:
1. 후보 = 스키마 테이블 ∩ 프로필 `allowed_tables`(선언됐을 때 · 맨 이름 비교). 강제 보충은 없다.
2. 프롬프트(`src.prompts.table_selection`) — 후보를 이름순으로 싣는다. 정의가 있으면 성격·관리하는
   정보·대표 컬럼·연결 상대를, 없으면 앞쪽 컬럼 10개를 싣는다.
3. 결정적 후처리 — 후보에 있는 이름만 남긴다(맨 이름 비교) → 상한 K로 자른다(LLM 순서) → 선별
   테이블 둘을 잇는 중간 테이블(다리)을 보완한다 → 다리를 포함해 K를 넘으면 다리부터 자른다.
4. 실패(호출 예외 · 백엔드 오류 응답 · 유효 0개) → 질문 어휘로 정의·컬럼 이름을 매칭한 상위 K개
   (`source="lexical"` · `[테이블선별] 폴백` 경고 · 프롬프트 규칙처럼 「수집적재」 성격은 제외).
   그것도 0개면 `source="none"` — 호출부는 SQL을 만들지 않고 `selection_none_guidance` 문구로
   끝낸다. **전체 테이블을 돌려주지 않는다.**

정의는 용도 블록과 같은 정제(`sanitize_definitions_for_prompt`)를 거친 것만 쓴다 — 승인 검증을
거치지 않은 파일 편집 정의가 프롬프트에 원문 그대로 실리지 않게 한다(뺀 테이블은 WARNING).

결과(`TableSelection.as_state`)는 상태 `table_selection[db_id]`에 실린다 — 테이블 이름·개수만이고
데이터 값은 없다.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from src.domain.schema_snapshot import bare_name
from src.domain.table_definitions import (
    KIND_COLLECT_LOAD,
    PROFILE_KEY,
    has_table_definitions,
    sanitize_definitions_for_prompt,
)
from src.prompts.table_selection import (
    NO_DB_DESCRIPTION,
    SUB_QUERY_CONTEXT_LINE,
    TABLE_SELECTION_PROMPT,
)
from src.sql_validation import detect_llm_backend_error
from src.utils.json_extract import (
    coerce_content_text,
    extract_json_from_response,
    strip_code_fence,
)

logger = logging.getLogger(__name__)

#: `table_selection[db_id].mode` — 정의 기반 선별
MODE_DEFINITIONS = "definitions"
#: `table_selection[db_id].source` — LLM 선별 / 어휘 매칭 대체 / 선별 없음(안내 종결)
SOURCE_LLM = "llm"
SOURCE_LEXICAL = "lexical"
SOURCE_NONE = "none"

#: 설정(`text2sql.schema_table_select_max`)을 읽지 못할 때의 상한 K
DEFAULT_SELECT_MAX = 8
#: 정의가 없는 후보 줄에 싣는 앞쪽 컬럼 수
_UNDEFINED_COLUMN_PREVIEW = 10
#: 0개 안내 문구에 싣는 업무 영역(group) 예시 수
_GUIDANCE_EXAMPLE_GROUPS = 3

#: 0개 안내 문구 본문 — 예시가 있으면 뒤에 「(예: …)」를 붙인다
SELECTION_NONE_MESSAGE = (
    "질문에 맞는 조회 대상 테이블을 찾지 못했습니다 — 무엇을 찾는지 알려 주세요"
)

# 어휘 대체 가중치 — 관리하는 정보 > 대표 컬럼 > 주의·컬럼 이름
_W_MANAGES = 3
_W_KEY_COLUMNS = 2
_W_NOTES = 1
_W_COLUMNS = 1

_WORD_SPLIT_RE = re.compile(r"[^\w]+")
_HANGUL_TAIL_RE = re.compile(r"[가-힣]$")
_LIST_ITEM_PREFIX_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


@dataclass(frozen=True)
class TableSelection:
    """정의 기반 테이블 선별 결과.

    Attributes:
        mode: 항상 ``"definitions"``
        source: ``"llm"``(LLM 선별) · ``"lexical"``(어휘 매칭 대체) · ``"none"``(선별 없음)
        candidates: 후보 테이블 수(스키마 ∩ 허용 목록)
        selected: 최종 선별 테이블 — 스키마 원래 키 · 다리 테이블 포함
        bridged: `selected` 중 다리 테이블로 보완한 것
    """

    mode: str
    source: str
    candidates: int
    selected: tuple[str, ...] = ()
    bridged: tuple[str, ...] = ()

    def as_state(self) -> dict[str, Any]:
        """상태 `table_selection[db_id]` 값(이름·개수만 — 데이터 값 없음)."""
        return {
            "mode": self.mode,
            "source": self.source,
            "candidates": self.candidates,
            "selected": list(self.selected),
            "bridged": list(self.bridged),
        }


def uses_definition_selection(
    profile: Mapping[str, Any] | None, routing_intent: str | None,
) -> bool:
    """정의 기반 선별을 쓸지 — 프로필에 테이블 정의가 있고 알람 의도가 아닐 때(G-1)."""
    return has_table_definitions(profile) and routing_intent != "alarm_query"


def select_max(app_config: Any) -> int:
    """상한 K(`text2sql.schema_table_select_max`) — 설정 대역(MagicMock 등)이면 기본값."""
    value = getattr(getattr(app_config, "text2sql", None), "schema_table_select_max", None)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DEFAULT_SELECT_MAX


def selection_none_guidance(definitions: Mapping[str, Any] | None) -> str:
    """선별 0개 종결의 사용자 안내 — 그래프·2단·멀티 경로 공용 문구.

    예시는 그 DB 정의의 업무 영역(`group`) 중 테이블이 많은 순으로 몇 개를 싣는다. 영역이 없으면
    예시를 붙이지 않는다.
    """
    counts: Counter[str] = Counter()
    for entry in (definitions or {}).values():
        if isinstance(entry, Mapping):
            group = entry.get("group")
            if isinstance(group, str) and group.strip():
                counts[group.strip()] += 1
    examples = [
        g for g, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ][:_GUIDANCE_EXAMPLE_GROUPS]
    if not examples:
        return SELECTION_NONE_MESSAGE
    return f"{SELECTION_NONE_MESSAGE}(예: {', '.join(examples)})"


def definitions_of(schema_info: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """스키마 정보에 부착된 구조 메타의 테이블 정의(없으면 None)."""
    meta = (schema_info or {}).get("_structure_meta")
    defs = meta.get(PROFILE_KEY) if isinstance(meta, Mapping) else None
    return defs if isinstance(defs, Mapping) else None


def selection_none_active(state: Mapping[str, Any]) -> bool:
    """활성 DB의 선별 결과가 「선별 없음」인가 — 단일 경로 SQL 생성·검증의 종결 신호.

    키는 `schema_analyzer`와 같은 규칙(`active_db_id`, 없으면 ``"_default"``)이다.
    """
    selections = state.get("table_selection")
    if not isinstance(selections, Mapping):
        return False
    entry = selections.get(state.get("active_db_id") or "_default")
    return isinstance(entry, Mapping) and entry.get("source") == SOURCE_NONE


async def load_db_description(cache_mgr: Any, db_id: str) -> str | None:
    """선별 프롬프트에 싣는 DB 설명 — 캐시 매니저에서 읽고, 못 읽으면 None(설명 없이 진행)."""
    try:
        description = await cache_mgr.get_db_description(db_id)
    except Exception as e:  # noqa: BLE001 — 설명은 보조 재료다(사유 로그)
        logger.info("[테이블선별] db=%s DB 설명 조회 실패 — 설명 없이 진행: %s", db_id, e)
        return None
    return description if isinstance(description, str) and description.strip() else None


def narrow_schema_dict(schema_dict: Mapping[str, Any], selected: Sequence[str]) -> dict[str, Any]:
    """스키마 딕셔너리를 선별 테이블로 좁힌 얕은 사본 — 캐시 공유 객체는 바꾸지 않는다.

    테이블은 선별 순서로 싣고, 관계는 두 끝이 모두 선별 테이블인 것만 남긴다(단일 경로
    `schema_to_dict`와 같은 규칙 — 경로 대칭).
    """
    tables = schema_dict.get("tables") or {}
    kept = {t: tables[t] for t in selected if t in tables}
    relationships = [
        rel for rel in schema_dict.get("relationships") or []
        if isinstance(rel, Mapping)
        and str(rel.get("from") or "").split(".")[0] in kept
        and str(rel.get("to") or "").split(".")[0] in kept
    ]
    return {**schema_dict, "tables": kept, "relationships": relationships}


# ──────────────────────────────────────────────
# 후보·프롬프트
# ──────────────────────────────────────────────


def _column_names(info: Any) -> list[str]:
    """테이블 정보의 컬럼 이름 — 스키마 딕셔너리(멀티)·`TableInfo`(단일) 모양을 모두 받는다."""
    columns = info.get("columns") if isinstance(info, Mapping) else getattr(info, "columns", None)
    names: list[str] = []
    for col in columns or []:
        name = col.get("name") if isinstance(col, Mapping) else getattr(col, "name", col)
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _candidate_tables(
    schema_tables: Mapping[str, Any], profile: Mapping[str, Any],
) -> list[str]:
    """후보 = 스키마 테이블 ∩ `allowed_tables`(선언됐을 때) — 이름순."""
    names = sorted(str(t) for t in schema_tables)
    if "allowed_tables" not in profile:
        return names
    allowed = {bare_name(str(t)) for t in profile.get("allowed_tables") or []}
    return [t for t in names if bare_name(t) in allowed]


def _definition_index(profile: Mapping[str, Any], db_id: str) -> dict[str, Mapping[str, Any]]:
    """맨 이름 → 정의 항목 — 프롬프트용 정제를 통과한 것만(뺀 테이블은 WARNING 1줄)."""
    clean, dropped = sanitize_definitions_for_prompt(profile.get(PROFILE_KEY))
    if dropped:
        logger.warning(
            "[테이블선별] db=%s 검증을 통과하지 못한 정의 %d개를 선별 재료에서 뺌"
            "(승인 검증을 거치지 않은 편집 의심): %s", db_id, len(dropped), dropped[:20],
        )
    out: dict[str, Mapping[str, Any]] = {}
    for name, entry in clean.items():
        out.setdefault(bare_name(name), entry)
    return out


def _candidate_line(
    name: str, entry: Mapping[str, Any] | None, columns: Sequence[str],
) -> str:
    """후보 한 줄 — 정의가 있으면 성격·관리하는 정보·대표 컬럼·연결 상대, 없으면 앞쪽 컬럼."""
    if entry is None:
        return f"- {name} [{', '.join(columns[:_UNDEFINED_COLUMN_PREVIEW])}]"
    head = f"- {name}"
    kind = entry.get("kind")
    if isinstance(kind, str) and kind:
        head += f" [{kind}]"
    manages = str(entry.get("manages") or "").strip()
    if manages:
        head += f" {manages}"
    parts = [head]
    keys = [str(c) for c in entry.get("key_columns") or [] if c]
    if keys:
        parts.append(f"대표 컬럼: {', '.join(keys)}")
    related = entry.get("related")
    if isinstance(related, Mapping) and related:
        parts.append(f"연결: {', '.join(sorted(str(t) for t in related))}")
    return " | ".join(parts)


def build_selection_prompt(
    *,
    question: str,
    sub_query_context: str | None,
    db_description: str | None,
    table_lines: Sequence[str],
    max_tables: int,
) -> str:
    """선별 프롬프트를 만든다 — 멀티 경로는 이 DB가 맡은 조회 설명을 질문 뒤에 덧붙인다.

    조회 설명이 질문과 같으면(멀티 경로 기본값) 덧붙이지 않는다 — 단일 경로와 같은 프롬프트가 된다.
    """
    text = (question or "").strip()
    context = (sub_query_context or "").strip()
    if context and context != text:
        text = f"{text}\n{SUB_QUERY_CONTEXT_LINE.format(sub_query_context=context)}"
    return TABLE_SELECTION_PROMPT.format(
        db_description=(db_description or "").strip() or NO_DB_DESCRIPTION,
        table_lines="\n".join(table_lines),
        max_tables=max_tables,
        question=text,
    )


def parse_selected_names(text: str) -> list[str]:
    """LLM 응답에서 테이블 이름 목록을 읽는다.

    JSON ``{"tables": [...]}``(또는 JSON 배열)을 먼저 보고, 아니면 쉼표·줄바꿈 목록으로 읽는다.
    """
    data: Any = extract_json_from_response(text)
    items: Any = None
    if isinstance(data, Mapping):
        items = data.get("tables")
    elif isinstance(data, list):
        items = data
    if isinstance(items, list):
        return [s.strip() for s in items if isinstance(s, str) and s.strip()]
    names: list[str] = []
    for part in re.split(r"[,\n]", strip_code_fence(text)):
        cleaned = _LIST_ITEM_PREFIX_RE.sub("", part).strip().strip("`'\"").strip()
        if cleaned:
            names.append(cleaned)
    return names


# ──────────────────────────────────────────────
# 결정적 후처리 — 다리 테이블
# ──────────────────────────────────────────────


def _table_of(end: str) -> str:
    """관계 끝(``[스키마.]테이블.컬럼``)의 맨 테이블 이름(없으면 빈 문자열)."""
    table, sep, _ = end.rpartition(".")
    return bare_name(table) if sep and table else ""


def _approved_edges(
    relationships: Sequence[Mapping[str, Any]] | None, profile: Mapping[str, Any],
) -> set[frozenset[str]]:
    """승인 관계(선언 FK + 프로필 `relationships` · D-294)의 테이블 쌍."""
    edges: set[frozenset[str]] = set()
    for rel in [*(relationships or []), *(profile.get("relationships") or [])]:
        if not isinstance(rel, Mapping):
            continue
        a = _table_of(str(rel.get("from") or ""))
        b = _table_of(str(rel.get("to") or ""))
        if a and b and a != b:
            edges.add(frozenset((a, b)))
    return edges


def _related_edges(def_index: Mapping[str, Mapping[str, Any]]) -> set[frozenset[str]]:
    """정의 `related`의 테이블 쌍 — 이름 일치 추정이라 값 겹침은 확인되지 않았다."""
    edges: set[frozenset[str]] = set()
    for table, entry in def_index.items():
        related = entry.get("related")
        if isinstance(related, Mapping):
            for other in related:
                b = bare_name(str(other))
                if b and b != table:
                    edges.add(frozenset((table, b)))
    return edges


def _adjacency(edges: set[frozenset[str]]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for edge in edges:
        a, b = tuple(edge)
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def _bridge_tables(
    selected: Sequence[str],
    cand_index: Mapping[str, str],
    approved: set[frozenset[str]],
    related: set[frozenset[str]],
    db_id: str,
) -> list[str]:
    """선별 테이블 둘을 잇는 중간 테이블(한 단계)만 보완한다.

    쌍마다 승인 관계를 먼저 본다 — 직접 이어지거나 이미 고른 테이블을 거쳐 이어지면 보완하지
    않는다. 승인 관계로 잇는 후보 중간 테이블이 없을 때만 정의 `related`(+승인 관계)로 찾는다.
    중간 테이블이 여럿이면 이름순 첫 번째 하나만 넣는다.
    """
    sel_bare = [bare_name(t) for t in selected]
    chosen = set(sel_bare)
    graphs = (
        (_adjacency(approved), "승인 관계"),
        (_adjacency(approved | related), "정의 related(미검증 추정)"),
    )
    bridges: list[str] = []
    for i, a in enumerate(sel_bare):
        for b in sel_bare[i + 1:]:
            for adj, source in graphs:
                if b in adj.get(a, set()):
                    break
                mids = sorted(
                    m for m in (adj.get(a, set()) & adj.get(b, set())) - {a, b}
                    if m in cand_index
                )
                if not mids:
                    continue
                if not chosen.intersection(mids):
                    chosen.add(mids[0])
                    bridges.append(cand_index[mids[0]])
                    logger.info(
                        "[테이블선별] db=%s 다리 테이블 보완: %s (%s ↔ %s · 근거=%s)",
                        db_id, cand_index[mids[0]], cand_index.get(a, a), cand_index.get(b, b),
                        source,
                    )
                break
    return bridges


# ──────────────────────────────────────────────
# 어휘 대체
# ──────────────────────────────────────────────


def _query_terms(text: str) -> list[tuple[str, ...]]:
    """질문 어절(2자 이상)과 그 꼬리 1~2자를 뗀 형태 — 한글 어절만 조사 몫을 뗀다."""
    terms: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for word in _WORD_SPLIT_RE.split(text.casefold()):
        if len(word) < 2 or word in seen:
            continue
        seen.add(word)
        forms = [word]
        if _HANGUL_TAIL_RE.search(word):
            forms.extend(word[:-n] for n in (1, 2) if len(word) - n >= 2)
        terms.append(tuple(forms))
    return terms


def _text_hit(forms: Sequence[str], text: str) -> bool:
    return bool(text) and any(f in text for f in forms)


def _names_hit(forms: Sequence[str], names: Sequence[str]) -> bool:
    word = forms[0]
    return any((len(n) >= 2 and n in word) or _text_hit(forms, n) for n in names)


def _lexical_score(
    terms: Sequence[tuple[str, ...]], entry: Mapping[str, Any] | None, columns: Sequence[str],
) -> int:
    manages = str((entry or {}).get("manages") or "").casefold()
    notes = str((entry or {}).get("notes") or "").casefold()
    keys = [str(c).casefold() for c in (entry or {}).get("key_columns") or []]
    cols = [c.casefold() for c in columns]
    score = 0
    for forms in terms:
        if _text_hit(forms, manages):
            score += _W_MANAGES
        if keys and _names_hit(forms, keys):
            score += _W_KEY_COLUMNS
        if _text_hit(forms, notes):
            score += _W_NOTES
        if cols and _names_hit(forms, cols):
            score += _W_COLUMNS
    return score


def _lexical_select(
    query_text: str,
    candidates: Sequence[str],
    def_index: Mapping[str, Mapping[str, Any]],
    columns: Mapping[str, Sequence[str]],
    k: int,
) -> list[str]:
    """질문 어휘로 정의(관리하는 정보·주의·대표 컬럼)·컬럼 이름을 매칭해 점수 상위 K개(점수 > 0).

    「수집적재」 성격 테이블은 후보에서 뺀다 — LLM 선별 프롬프트 규칙과 같은 결정적 필터다.
    """
    terms = _query_terms(query_text)
    if not terms:
        return []
    scored = [
        (_lexical_score(terms, def_index.get(bare_name(t)), columns[t]), t)
        for t in candidates
        if (def_index.get(bare_name(t)) or {}).get("kind") != KIND_COLLECT_LOAD
    ]
    ranked = sorted((pair for pair in scored if pair[0] > 0), key=lambda p: (-p[0], p[1]))
    return [t for _, t in ranked[:k]]


# ──────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────


async def select_tables(
    *,
    llm: Any,
    question: str,
    sub_query_context: str | None,
    db_id: str,
    db_description: str | None,
    schema_tables: Mapping[str, Any],
    profile: Mapping[str, Any],
    app_config: Any,
    relationships: Sequence[Mapping[str, Any]] | None = None,
) -> TableSelection:
    """정의 기반으로 조회 대상 테이블을 고른다(단일·멀티 공용).

    Args:
        llm: 선별 LLM(``ainvoke`` 보유)
        question: 사용자 질문 원문
        sub_query_context: 멀티 경로에서 이 DB가 맡은 조회 설명(단일 경로는 None)
        db_id: DB 식별자(로그용)
        db_description: DB 설명(없으면 None)
        schema_tables: ``{스키마 테이블 키: 테이블 정보}`` — 딕셔너리·`TableInfo` 모두 받는다
        profile: 수동 프로필(`table_definitions`·`allowed_tables`·`relationships`)
        app_config: 앱 설정(상한 K)
        relationships: 스키마 선언 관계(``[{"from": "t.c", "to": "t.c"}]``)

    Returns:
        선별 결과 — 실패해도 예외를 내지 않는다(`source`로 구분).
    """
    k = select_max(app_config)
    def_index = _definition_index(profile, db_id)
    candidates = _candidate_tables(schema_tables, profile)
    if not candidates:
        logger.warning(
            "[테이블선별] 폴백 — db=%s 사유: 후보 0개(스키마 %d개 ∩ 허용 목록) → 선별 없음",
            db_id, len(schema_tables),
        )
        return TableSelection(MODE_DEFINITIONS, SOURCE_NONE, 0)
    cand_index: dict[str, str] = {}
    for t in candidates:
        cand_index.setdefault(bare_name(t), t)
    columns = {t: _column_names(schema_tables[t]) for t in candidates}
    prompt = build_selection_prompt(
        question=question,
        sub_query_context=sub_query_context,
        db_description=db_description,
        table_lines=[
            _candidate_line(t, def_index.get(bare_name(t)), columns[t]) for t in candidates
        ],
        max_tables=k,
    )

    reason: str | None = None
    picked: list[str] = []
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        text = coerce_content_text(response.content)
    except Exception as e:  # noqa: BLE001 — 선별 실패는 어휘 대체로 간다(사유 로그)
        backend = detect_llm_backend_error(str(e))
        reason = (
            f"LLM 백엔드 오류({backend.summary})" if backend is not None
            else f"LLM 호출 예외({type(e).__name__}: {' '.join(str(e).split())[:200]})"
        )
    else:
        backend = detect_llm_backend_error(text)
        if backend is not None:
            reason = f"LLM 백엔드 오류 응답({backend.summary})"
        else:
            names = parse_selected_names(text)
            dropped: list[str] = []
            for name in names:
                key = cand_index.get(bare_name(name))
                if key is None:
                    dropped.append(name)
                elif key not in picked:
                    picked.append(key)
            if dropped:
                logger.info(
                    "[테이블선별] db=%s 후보 밖·없는 이름 제거 %d개: %s",
                    db_id, len(dropped), dropped[:20],
                )
            if not picked:
                reason = f"유효한 테이블 0개(응답 이름 {len(names)}개)"

    if reason is None:
        if len(picked) > k:
            logger.info(
                "[테이블선별] db=%s 상한 %d개 초과 — LLM 순서로 자름: 제외 %s",
                db_id, k, picked[k:],
            )
            picked = picked[:k]
        bridges = _bridge_tables(
            picked, cand_index, _approved_edges(relationships, profile),
            _related_edges(def_index), db_id,
        )
        room = max(0, k - len(picked))
        if len(bridges) > room:
            logger.info(
                "[테이블선별] db=%s 상한 %d개 — 다리 테이블 제외: %s", db_id, k, bridges[room:],
            )
            bridges = bridges[:room]
        selection = TableSelection(
            MODE_DEFINITIONS, SOURCE_LLM, len(candidates), tuple(picked + bridges), tuple(bridges),
        )
        logger.info(
            "[테이블선별] db=%s 출처=llm 후보 %d → 선별 %d: %s (다리 %s)",
            db_id, len(candidates), len(selection.selected), list(selection.selected), bridges,
        )
        return selection

    query_text = " ".join(p for p in (question, sub_query_context) if p)
    lexical = _lexical_select(query_text, candidates, def_index, columns, k)
    if lexical:
        logger.warning(
            "[테이블선별] 폴백 — db=%s 사유: %s → 어휘 매칭 %d개: %s",
            db_id, reason, len(lexical), lexical,
        )
        return TableSelection(MODE_DEFINITIONS, SOURCE_LEXICAL, len(candidates), tuple(lexical))
    logger.warning(
        "[테이블선별] 폴백 — db=%s 사유: %s → 어휘 매칭 0개 — 선별 없음(SQL 생성 없이 안내 종결)",
        db_id, reason,
    )
    return TableSelection(MODE_DEFINITIONS, SOURCE_NONE, len(candidates))
