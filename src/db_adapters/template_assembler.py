"""조회 템플릿 결정적 조립기 — 단일·멀티 공용 (plans/143 W6 · D-316 ③ · D-066).

`config/knowledge/{db_id}/query_templates.yaml`(데이터)이 있는 DB에서만 발동한다(존재 기반 —
파일이 없는 DB는 LLM 호출 0 · 표지 없음). 흐름:

1. LLM이 템플릿 목록을 보고 ID와 슬롯 값을 JSON으로만 고른다(`src.prompts.template_selection`).
2. 코드가 슬롯을 형식 검증·바인딩한다(`src.domain.query_templates` — 사용자 문자열 직접 연결 없음).
3. 조립 SQL을 `SQLGuard`와 기존 검증 코어(`validate_sql_draft`)에 통과시킨다.

어느 단계든 실패하면 구조화 사유를 남기고(`TemplateOutcome.reason`) 호출부가 기존 LLM SQL 생성으로
간다(침묵 폴백 금지). 슬롯 **값**은 표지·로그에 남기지 않는다(이름만).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from langchain_core.messages import HumanMessage

from src.domain.query_templates import (
    OUTCOME_ASSEMBLED,
    OUTCOME_FALLBACK,
    REASON_GUARD_REJECTED,
    REASON_INVALID_FILE,
    REASON_INVALID_JSON,
    REASON_LLM_ERROR,
    REASON_NO_MATCH,
    REASON_UNKNOWN_TEMPLATE,
    REASON_VALIDATION_FAILED,
    SLOT_CODE,
    STATUS_ACTIVE,
    QueryTemplate,
    bind_template,
    parse_templates,
)
from src.prompts.template_selection import SUB_QUERY_CONTEXT_LINE, TEMPLATE_SELECTION_PROMPT
from src.security.sql_guard import SQLGuard
from src.sql_validation import detect_llm_backend_error
from src.tools.validation import validate_sql_draft
from src.utils.json_extract import coerce_content_text, extract_json_from_response

logger = logging.getLogger(__name__)

#: 템플릿 파일 위치(작업 디렉터리 기준 — 앱은 저장소 루트에서 뜬다)
TEMPLATE_PATH = "config/knowledge/{db_id}/query_templates.yaml"
#: 파일 기준 경로(None = 작업 디렉터리). 테스트가 바꿔 끼운다.
DEFAULT_ROOT: Path | None = None

_DB_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
_PROMPT_MAX_CODES = 30
_NONE_ID = "none"


@dataclass(frozen=True)
class TemplateOutcome:
    """템플릿 조립 결과 — ``sql``이 있으면 조립 성공, 없으면 ``reason``으로 폴백 사유."""

    sql: str | None
    template_id: str | None
    slot_names: tuple[str, ...] = ()
    outcome: str = OUTCOME_FALLBACK
    reason: str | None = None
    detail: str = field(default="", compare=False)

    def as_state(self) -> dict[str, Any]:
        """상태 표지(`template_assembly[db_id]`) — 값 없이 이름·사유만.

        ``final_sql_from_template``은 조립 성공이면 True, 폴백이면 False다. 같은 요청에서 그 DB의
        SQL을 템플릿 밖(LLM 재생성 등)에서 다시 만들면 호출부가 `mark_regenerated`로 False로 바꾼다.
        """
        return {
            "outcome": self.outcome,
            "template_id": self.template_id,
            "slot_names": list(self.slot_names),
            "reason": self.reason,
            "final_sql_from_template": self.outcome == OUTCOME_ASSEMBLED and self.sql is not None,
        }


def mark_regenerated(entry: Any) -> dict[str, Any] | None:
    """템플릿 조립 뒤 같은 요청에서 그 DB의 SQL을 다시 만들었을 때(재시도 턴의 LLM 생성 등)의 표지.

    조립 성공 표지(``outcome: assembled``)이고 아직 ``final_sql_from_template``이 False가 아니면
    False로 바꾼 사본을 돌려준다. 그 밖(표지 없음·폴백·이미 False)은 None — 갱신 없음.
    """
    if not isinstance(entry, Mapping) or entry.get("outcome") != OUTCOME_ASSEMBLED:
        return None
    if entry.get("final_sql_from_template") is False:
        return None
    return {**entry, "final_sql_from_template": False}


@dataclass(frozen=True)
class _Loaded:
    templates: tuple[QueryTemplate, ...]
    issues: tuple[str, ...]
    readable: bool


_cache: dict[str, tuple[tuple[int, int], _Loaded]] = {}


def template_assembly_enabled(app_config: Any) -> bool:
    """전역 스위치(`TEXT2SQL_TEMPLATE_ASSEMBLY`) — 실제 bool True일 때만 켠다(대역은 꺼짐)."""
    t2s = getattr(app_config, "text2sql", None)
    return getattr(t2s, "template_assembly", False) is True


def _path(db_id: str, root: Path | None) -> Path:
    return (root or DEFAULT_ROOT or Path.cwd()) / TEMPLATE_PATH.format(db_id=db_id)


def load_templates(db_id: str | None, *, root: Path | None = None) -> _Loaded | None:
    """그 DB의 템플릿 파일을 읽는다 — 파일이 없으면 None. (수정 시각, 크기)로 캐시한다."""
    if not db_id or not _DB_ID_RE.fullmatch(db_id):
        return None
    path = _path(db_id, root)
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        _cache.pop(key, None)
        return None
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _cache.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        raw: Any = yaml.safe_load(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        logger.warning("[템플릿조립] db=%s 템플릿 파일 읽기 실패: %s", db_id, type(e).__name__)
        loaded = _Loaded((), (f"읽기 실패({type(e).__name__})",), readable=False)
    else:
        templates, issues = parse_templates(raw)
        active = tuple(t for t in templates if t.status == STATUS_ACTIVE)
        if issues:
            logger.warning(
                "[템플릿조립] db=%s 계약 위반 템플릿 제외 %d건: %s", db_id, len(issues), issues[:5],
            )
        loaded = _Loaded(active, tuple(issues), readable=True)
    _cache[key] = (stamp, loaded)
    return loaded


def _code_values(schema_info: Mapping[str, Any] | None) -> dict[str, list[Any]]:
    meta = (schema_info or {}).get("_structure_meta") or {}
    raw = meta.get("code_values") if isinstance(meta, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): list(v) for k, v in raw.items() if isinstance(v, list)}


def _template_line(tpl: QueryTemplate, code_values: Mapping[str, list[Any]]) -> str:
    slots: list[str] = []
    for spec in tpl.slots:
        need = "필수" if spec.required else "선택"
        label = f"{spec.name}({spec.type}·{need}"
        if spec.type == SLOT_CODE:
            allowed = [str(v) for v in code_values.get(spec.column or "", [])]
            shown = ", ".join(allowed[:_PROMPT_MAX_CODES])
            more = " …" if len(allowed) > _PROMPT_MAX_CODES else ""
            label += f" · 허용 값: {shown}{more}" if allowed else " · 허용 값 없음"
        slots.append(label + ")")
    triggers = ", ".join(tpl.triggers) or "-"
    return f"- {tpl.id}: {tpl.intent} | 표면어: {triggers} | 슬롯: {', '.join(slots) or '없음'}"


def build_selection_prompt(
    templates: tuple[QueryTemplate, ...],
    *,
    question: str,
    sub_query_context: str | None,
    code_values: Mapping[str, list[Any]],
    today: date,
) -> str:
    """선택 프롬프트 — 멀티 경로는 이 DB가 맡은 조회 설명이 질문과 다를 때만 덧붙인다."""
    text = (question or "").strip()
    context = (sub_query_context or "").strip()
    if context and context != text:
        text = f"{text}\n{SUB_QUERY_CONTEXT_LINE.format(sub_query_context=context)}"
    return TEMPLATE_SELECTION_PROMPT.format(
        template_lines="\n".join(_template_line(t, code_values) for t in templates),
        today=today.isoformat(),
        question=text,
    )


def _fallback(
    db_id: str, reason: str, detail: str = "", *,
    template_id: str | None = None, slot_names: tuple[str, ...] = (),
) -> TemplateOutcome:
    logger.info(
        "[템플릿조립] db=%s 폴백 — 사유=%s 템플릿=%s 슬롯=%s %s",
        db_id, reason, template_id or "-", list(slot_names), detail,
    )
    return TemplateOutcome(
        sql=None, template_id=template_id, slot_names=slot_names,
        outcome=OUTCOME_FALLBACK, reason=reason, detail=detail,
    )


async def assemble_from_template(
    *,
    llm: Any,
    question: str,
    db_id: str | None,
    schema_info: Mapping[str, Any] | None,
    app_config: Any,
    db_engine: str = "postgresql",
    user_query: str = "",
    default_limit: int = 100,
    sub_query_context: str | None = None,
    root: Path | None = None,
    today: date | None = None,
) -> TemplateOutcome | None:
    """템플릿으로 SQL을 조립한다(단일·멀티 공용).

    Returns:
        None — 발동 안 함(스위치 off · 파일 없음 · DB 미상). 호출부는 표지를 싣지 않는다.
        TemplateOutcome — 조립 성공(``sql``) 또는 폴백(``reason``). 폴백이면 호출부가 LLM
        생성으로 간다.
    """
    if not template_assembly_enabled(app_config) or not db_id:
        return None
    loaded = load_templates(db_id, root=root)
    if loaded is None:
        return None
    if not loaded.templates:
        detail = (
            "읽기 실패" if not loaded.readable
            else f"유효한 active 템플릿 0개(위반 {len(loaded.issues)}건)"
        )
        return _fallback(db_id, REASON_INVALID_FILE, detail)

    codes = _code_values(schema_info)
    prompt = build_selection_prompt(
        loaded.templates, question=question, sub_query_context=sub_query_context,
        code_values=codes, today=today or date.today(),
    )
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        text = coerce_content_text(response.content)
    except Exception as e:  # noqa: BLE001 — 선택 실패는 LLM SQL 생성으로 폴백(사유 남김)
        return _fallback(db_id, REASON_LLM_ERROR, type(e).__name__)
    backend = detect_llm_backend_error(text)
    if backend is not None:
        return _fallback(db_id, REASON_LLM_ERROR, backend.summary)

    data = extract_json_from_response(text)
    tid = data.get("template_id") if isinstance(data, dict) else None
    slots = data.get("slots", {}) if isinstance(data, dict) else None
    if not isinstance(tid, str) or not tid.strip() or not isinstance(slots, (dict, type(None))):
        return _fallback(db_id, REASON_INVALID_JSON)
    tid = tid.strip()
    if tid.lower() == _NONE_ID:
        return _fallback(db_id, REASON_NO_MATCH)
    template = next((t for t in loaded.templates if t.id == tid), None)
    if template is None:
        # 없는 ID는 LLM 산출이라 그대로 싣지 않는다(이름 길이·문자 미검증)
        return _fallback(db_id, REASON_UNKNOWN_TEMPLATE)

    bound = bind_template(template, slots, codes)
    if bound.sql is None:
        return _fallback(db_id, bound.reason or REASON_INVALID_JSON, bound.detail, template_id=tid)

    safe, why = SQLGuard().is_safe_select(bound.sql)
    if not safe:
        return _fallback(db_id, REASON_GUARD_REJECTED, why, template_id=tid,
                         slot_names=bound.slot_names)
    checked = validate_sql_draft(
        bound.sql, dict(schema_info or {}), db_engine=db_engine, user_query=user_query,
        default_limit=default_limit, db_id=db_id,
        adapter_db_ids=_polestar_ids(app_config),
    )
    if not checked["valid"]:
        return _fallback(
            db_id, REASON_VALIDATION_FAILED, "; ".join(checked["errors"])[:300],
            template_id=tid, slot_names=bound.slot_names,
        )
    sql = checked["fixed_sql"] or bound.sql
    logger.info(
        "[템플릿조립] db=%s 조립 성공(LLM SQL 생성 우회) — 템플릿=%s 슬롯=%s",
        db_id, tid, list(bound.slot_names),
    )
    return TemplateOutcome(
        sql=sql, template_id=tid, slot_names=bound.slot_names, outcome=OUTCOME_ASSEMBLED,
    )


def _polestar_ids(app_config: Any) -> set[str] | None:
    getter = getattr(app_config, "get_polestar_db_ids", None)
    if not callable(getter):
        return None
    ids = getter()
    return set(ids) if isinstance(ids, (set, frozenset, list, tuple)) and ids else None
