"""LLM 기반 필드-컬럼 매핑 모듈.

양식 필드명과 DB 컬럼명 간의 의미적 매핑을 수행한다.
3단계 매핑: 프롬프트 힌트 -> Redis synonyms -> LLM 추론.

decision.md D-007에 따라 LLM 의미 매핑을 사용하며,
xls_plan.md에 따라 매핑을 최우선 수행하고 그 결과로
대상 DB를 결정하는 Mapping-First 전략을 구현한다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.prompts.field_mapper import (
    FIELD_MAPPER_SYSTEM_PROMPT,
    FIELD_MAPPER_USER_PROMPT,
    FIELD_MAPPER_MULTI_DB_SYSTEM_PROMPT,
    FIELD_MAPPER_MULTI_DB_USER_PROMPT,
)
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)


# === 3-Step Mapping Results ===


class MappingResult:
    """3단계 매핑의 통합 결과."""

    def __init__(self) -> None:
        self.column_mapping: dict[str, Optional[str]] = {}
        self.db_column_mapping: dict[str, dict[str, str]] = {}
        self.mapping_sources: dict[str, str] = {}
        self.mapped_db_ids: list[str] = []


# === Public API ===


async def map_fields(
    llm: BaseChatModel,
    template_structure: dict[str, Any],
    schema_info: dict[str, Any],
) -> dict[str, Optional[str]]:
    """양식 필드명과 DB 컬럼명 간의 의미적 매핑을 수행한다.

    단일 DB 모드에서 기존 호환성을 유지하는 API.

    Args:
        llm: LLM 인스턴스
        template_structure: 양식 구조 정보 (excel_parser/word_parser 출력)
        schema_info: DB 스키마 정보 (schema_analyzer 출력)

    Returns:
        매핑 딕셔너리: {"양식필드명": "테이블.컬럼" | None}
    """
    field_names = extract_field_names(template_structure)
    if not field_names:
        logger.warning("양식에서 필드명을 추출할 수 없습니다.")
        return {}

    schema_columns = _format_schema_columns(schema_info)
    if not schema_columns:
        logger.warning("스키마에서 컬럼 정보를 추출할 수 없습니다.")
        return {name: None for name in field_names}

    mapping = await _invoke_llm_mapping(llm, field_names, schema_columns)
    validated = _validate_mapping(mapping, schema_info, field_names)

    logger.info(
        "필드 매핑 완료: %d/%d 필드 매핑 성공",
        sum(1 for v in validated.values() if v is not None),
        len(validated),
    )

    return validated


async def map_fields_per_sheet(
    llm: BaseChatModel,
    template_structure: dict[str, Any],
    schema_info: dict[str, Any],
    target_sheets: list[str] | None = None,
) -> dict[str, dict[str, Optional[str]]]:
    """시트별로 독립적으로 필드 매핑을 수행한다.

    각 시트의 헤더를 개별적으로 분석하여 시트마다 다른 매핑을 생성한다.

    Args:
        llm: LLM 인스턴스
        template_structure: 양식 구조 정보 (excel_parser 출력)
        schema_info: DB 스키마 정보
        target_sheets: 대상 시트명 목록 (None이면 전체 시트)

    Returns:
        시트별 매핑 딕셔너리: {"시트명": {"양식필드명": "테이블.컬럼" | None}}
    """
    sheets = template_structure.get("sheets", [])
    if not sheets:
        return {}

    if target_sheets:
        target_set = set(target_sheets)
        sheets = [s for s in sheets if s.get("name") in target_set]

    schema_columns = _format_schema_columns(schema_info)
    if not schema_columns:
        logger.warning("스키마에서 컬럼 정보를 추출할 수 없습니다.")
        result: dict[str, dict[str, Optional[str]]] = {}
        for sheet in sheets:
            sheet_name = sheet.get("name", "")
            headers = sheet.get("headers", [])
            result[sheet_name] = {h: None for h in headers}
        return result

    sheet_mappings: dict[str, dict[str, Optional[str]]] = {}

    for sheet in sheets:
        sheet_name = sheet.get("name", "")
        headers = sheet.get("headers", [])
        if not headers:
            continue

        mapping = await _invoke_llm_mapping(llm, headers, schema_columns)
        validated = _validate_mapping(mapping, schema_info, headers)

        logger.info(
            "시트 '%s' 필드 매핑: %d/%d 필드 매핑 성공",
            sheet_name,
            sum(1 for v in validated.values() if v is not None),
            len(validated),
        )
        sheet_mappings[sheet_name] = validated

    return sheet_mappings


async def perform_3step_mapping(
    llm: BaseChatModel,
    field_names: list[str],
    field_mapping_hints: list[dict],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    all_db_descriptions: dict[str, dict[str, str]],
    priority_db_ids: list[str],
) -> MappingResult:
    """3단계 매핑을 수행한다.

    1단계: 사용자 프롬프트 힌트 (field_mapping_hints)
    2단계: Redis synonyms 기반 규칙 매핑
    3단계: LLM 의미 매핑

    Args:
        llm: LLM 인스턴스
        field_names: 양식 필드명 목록
        field_mapping_hints: 사용자 지정 매핑 힌트
        all_db_synonyms: DB별 synonyms {db_id: {table.column: [words]}}
        all_db_descriptions: DB별 descriptions {db_id: {table.column: desc}}
        priority_db_ids: 우선순위 DB 목록

    Returns:
        MappingResult 객체
    """
    result = MappingResult()
    remaining = set(field_names)

    # --- 1단계: 프롬프트 힌트 ---
    _apply_hint_mapping(
        remaining, field_mapping_hints, all_db_synonyms, result
    )

    # --- 2단계: Redis synonyms 규칙 매핑 ---
    if remaining and all_db_synonyms:
        _apply_synonym_mapping(
            remaining, all_db_synonyms, priority_db_ids, result
        )

    # --- 3단계: LLM 추론 매핑 ---
    if remaining:
        await _apply_llm_mapping(
            llm, list(remaining), all_db_descriptions, priority_db_ids, result
        )

    # mapped_db_ids 생성
    result.mapped_db_ids = list(result.db_column_mapping.keys())

    # column_mapping (통합 뷰) 생성
    for db_id, db_map in result.db_column_mapping.items():
        for field, column in db_map.items():
            result.column_mapping[field] = column

    # 매핑되지 않은 필드도 column_mapping에 None으로 포함
    for field in field_names:
        if field not in result.column_mapping:
            result.column_mapping[field] = None

    logger.info(
        "3단계 매핑 완료: %d/%d 필드 매핑 (힌트=%d, 유사어=%d, LLM=%d), DB=%s",
        sum(1 for v in result.column_mapping.values() if v is not None),
        len(field_names),
        sum(1 for s in result.mapping_sources.values() if s == "hint"),
        sum(1 for s in result.mapping_sources.values() if s == "synonym"),
        sum(1 for s in result.mapping_sources.values() if s == "llm_inferred"),
        result.mapped_db_ids,
    )

    return result


# === Helper: Extract field names from template ===


def extract_field_names(template_structure: dict[str, Any]) -> list[str]:
    """양식 구조에서 필드명을 추출한다.

    Args:
        template_structure: 양식 구조 정보

    Returns:
        필드명 목록 (중복 제거)
    """
    names: list[str] = []
    seen: set[str] = set()

    file_type = template_structure.get("file_type", "")

    # Excel: sheets[*].headers
    if file_type == "xlsx":
        for sheet in template_structure.get("sheets", []):
            for header in sheet.get("headers", []):
                if header and header not in seen:
                    names.append(header)
                    seen.add(header)

    # Word: placeholders + tables[*].headers
    elif file_type in ("docx", "doc"):
        for ph in template_structure.get("placeholders", []):
            if ph and ph not in seen:
                names.append(ph)
                seen.add(ph)
        for table in template_structure.get("tables", []):
            for header in table.get("headers", []):
                if header and header not in seen:
                    names.append(header)
                    seen.add(header)

    return names


# === Step 1: Hint Mapping ===


def _apply_hint_mapping(
    remaining: set[str],
    hints: list[dict],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    result: MappingResult,
) -> None:
    """프롬프트 힌트로 매핑을 수행한다.

    Args:
        remaining: 아직 매핑되지 않은 필드 set
        hints: field_mapping_hints 목록
        all_db_synonyms: DB별 synonyms (db_id 결정용)
        result: 매핑 결과 객체
    """
    for hint in hints:
        field = hint.get("field", "")
        if field not in remaining:
            continue

        column = hint.get("column")
        if not column:
            continue

        db_id = hint.get("db_id")
        if not db_id:
            db_id = _find_db_for_column(column, all_db_synonyms)

        if db_id:
            result.db_column_mapping.setdefault(db_id, {})[field] = column
            result.mapping_sources[field] = "hint"
            remaining.discard(field)


def _find_db_for_column(
    column: str,
    all_db_synonyms: dict[str, dict[str, list[str]]],
) -> Optional[str]:
    """컬럼명이 속한 DB를 synonyms에서 찾는다.

    Args:
        column: table.column 또는 column 형식
        all_db_synonyms: DB별 synonyms

    Returns:
        db_id 또는 None
    """
    for db_id, synonyms in all_db_synonyms.items():
        for col_key in synonyms:
            if col_key == column or col_key.endswith(f".{column}"):
                return db_id
    return None


# === Step 2: Synonym Mapping ===


def _apply_synonym_mapping(
    remaining: set[str],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    priority_db_ids: list[str],
    result: MappingResult,
) -> None:
    """Redis synonyms 기반 매핑을 수행한다.

    priority_db_ids를 먼저 검색하여 우선순위를 부여한다.

    Args:
        remaining: 매핑되지 않은 필드 set
        all_db_synonyms: DB별 synonyms {db_id: {table.column: [words]}}
        priority_db_ids: 우선순위 DB 목록
        result: 매핑 결과 객체
    """
    ordered_db_ids = priority_db_ids + [
        d for d in all_db_synonyms if d not in priority_db_ids
    ]

    for field in list(remaining):
        field_lower = field.lower().strip()

        for db_id in ordered_db_ids:
            synonyms = all_db_synonyms.get(db_id, {})
            matched_column = _synonym_match(field_lower, synonyms)
            if matched_column:
                result.db_column_mapping.setdefault(db_id, {})[field] = matched_column
                result.mapping_sources[field] = "synonym"
                remaining.discard(field)
                break


def _synonym_match(
    field_lower: str,
    synonyms: dict[str, list[str]],
) -> Optional[str]:
    """필드명을 synonyms에서 매칭한다.

    Args:
        field_lower: 소문자 필드명
        synonyms: {table.column: [synonym_words]}

    Returns:
        매칭된 table.column 또는 None
    """
    for col_key, words in synonyms.items():
        for word in words:
            if word.lower().strip() == field_lower:
                return col_key
        # 컬럼명 자체도 매칭 시도
        col_name = col_key.split(".", 1)[-1] if "." in col_key else col_key
        if col_name.lower() == field_lower:
            return col_key

    return None


# === Step 3: LLM Mapping ===


async def _apply_llm_mapping(
    llm: BaseChatModel,
    remaining_fields: list[str],
    all_db_descriptions: dict[str, dict[str, str]],
    priority_db_ids: list[str],
    result: MappingResult,
) -> None:
    """LLM을 통해 남은 필드의 매핑을 수행한다.

    여러 DB의 descriptions를 포함한 프롬프트로 LLM에 매핑을 요청하며,
    LLM은 각 필드에 대해 db_id와 table.column을 반환한다.

    Args:
        llm: LLM 인스턴스
        remaining_fields: 매핑되지 않은 필드 목록
        all_db_descriptions: DB별 descriptions {db_id: {table.column: desc}}
        priority_db_ids: 우선순위 DB 목록
        result: 매핑 결과 객체
    """
    if not remaining_fields:
        return

    if not all_db_descriptions:
        logger.warning("DB descriptions가 없어 LLM 매핑을 수행할 수 없습니다.")
        return

    # 멀티 DB descriptions를 하나의 프롬프트로 구성
    llm_mapping = await _invoke_llm_mapping_multi_db(
        llm, remaining_fields, all_db_descriptions, priority_db_ids
    )

    for field, mapping_info in llm_mapping.items():
        if mapping_info:
            db_id = mapping_info.get("db_id")
            column = mapping_info.get("column")
            if db_id and column:
                result.db_column_mapping.setdefault(db_id, {})[field] = column
                result.mapping_sources[field] = "llm_inferred"


async def _invoke_llm_mapping_multi_db(
    llm: BaseChatModel,
    field_names: list[str],
    all_db_descriptions: dict[str, dict[str, str]],
    priority_db_ids: list[str],
) -> dict[str, Optional[dict[str, str]]]:
    """멀티 DB 환경에서 LLM 매핑을 수행한다.

    Args:
        llm: LLM 인스턴스
        field_names: 매핑할 필드 목록
        all_db_descriptions: DB별 descriptions
        priority_db_ids: 우선순위 DB

    Returns:
        {field: {"db_id": str, "column": str} | None}
    """
    # DB별 스키마 정보를 프롬프트용으로 포맷
    db_schema_parts: list[str] = []
    ordered_db_ids = priority_db_ids + [
        d for d in all_db_descriptions if d not in priority_db_ids
    ]
    for db_id in ordered_db_ids:
        descs = all_db_descriptions.get(db_id, {})
        if not descs:
            continue
        lines = [f"## DB: {db_id}"]
        for col_key, desc in descs.items():
            lines.append(f"- {col_key}: {desc}")
        db_schema_parts.append("\n".join(lines))

    schema_text = "\n\n".join(db_schema_parts)

    user_prompt = FIELD_MAPPER_MULTI_DB_USER_PROMPT.format(
        field_names="\n".join(f"- {name}" for name in field_names),
        db_schema_columns=schema_text,
    )

    messages = [
        SystemMessage(content=FIELD_MAPPER_MULTI_DB_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    for attempt in range(2):
        try:
            response = await llm.ainvoke(messages)
            parsed = extract_json_from_response(response.content)
            if parsed:
                # {"field": {"db_id": "x", "column": "y"}} 형식 기대
                result: dict[str, Optional[dict[str, str]]] = {}
                for field in field_names:
                    mapping = parsed.get(field)
                    if isinstance(mapping, dict):
                        result[field] = mapping
                    elif isinstance(mapping, str) and mapping:
                        # 단순 "table.column" 형식인 경우 첫 번째 DB에 할당
                        result[field] = {
                            "db_id": ordered_db_ids[0] if ordered_db_ids else "unknown",
                            "column": mapping,
                        }
                    else:
                        result[field] = None
                return result
        except Exception as e:
            logger.warning("LLM 멀티DB 매핑 호출 실패 (시도 %d): %s", attempt + 1, e)

        messages.append(HumanMessage(
            content="반드시 유효한 JSON만 출력하세요. 각 필드에 대해 {\"db_id\": \"...\", \"column\": \"...\"} 형식으로 응답합니다."
        ))

    logger.error("LLM 멀티DB 매핑 실패: 모든 시도 소진")
    return {name: None for name in field_names}


# === Legacy LLM Mapping (Single DB) ===


async def _invoke_llm_mapping(
    llm: BaseChatModel,
    field_names: list[str],
    schema_columns: str,
) -> dict[str, Optional[str]]:
    """LLM을 호출하여 필드 매핑을 수행한다 (단일 DB).

    JSON 파싱 실패 시 1회 재시도한다.

    Args:
        llm: LLM 인스턴스
        field_names: 양식 필드명 목록
        schema_columns: 포맷된 스키마 컬럼 문자열

    Returns:
        매핑 딕셔너리
    """
    user_prompt = FIELD_MAPPER_USER_PROMPT.format(
        field_names="\n".join(f"- {name}" for name in field_names),
        schema_columns=schema_columns,
    )

    messages = [
        SystemMessage(content=FIELD_MAPPER_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    for attempt in range(2):
        try:
            response = await llm.ainvoke(messages)
            mapping = extract_json_from_response(response.content)
            if mapping:
                return mapping
        except Exception as e:
            logger.warning("LLM 매핑 호출 실패 (시도 %d): %s", attempt + 1, e)

        messages.append(HumanMessage(
            content="반드시 유효한 JSON만 출력하세요. 설명 텍스트 없이 JSON만 응답합니다."
        ))

    logger.error("LLM 필드 매핑 실패: 모든 시도 소진")
    return {name: None for name in field_names}


# === Utility ===


def _format_schema_columns(
    schema_info: dict[str, Any],
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
) -> str:
    """스키마 정보를 LLM 프롬프트용 문자열로 포맷한다.

    Args:
        schema_info: DB 스키마 정보
        column_descriptions: 컬럼 설명 (선택)
        column_synonyms: 유사 단어 (선택)

    Returns:
        포맷된 스키마 문자열
    """
    descs = column_descriptions or {}
    syns = column_synonyms or {}
    lines: list[str] = []
    tables = schema_info.get("tables", {})

    for table_name, table_info in tables.items():
        columns = table_info.get("columns", [])
        for col in columns:
            col_name = col.get("name", "")
            col_type = col.get("type", "")
            if col_name:
                col_key = f"{table_name}.{col_name}"
                entry = f"- {col_key} ({col_type})"
                desc = descs.get(col_key)
                if desc:
                    entry += f" -- {desc}"
                syn_list = syns.get(col_key)
                if syn_list:
                    entry += f" [유사: {', '.join(syn_list[:5])}]"
                lines.append(entry)

    return "\n".join(lines)


def _validate_mapping(
    mapping: dict[str, Optional[str]],
    schema_info: dict[str, Any],
    field_names: list[str],
) -> dict[str, Optional[str]]:
    """매핑 결과를 검증한다.

    존재하지 않는 테이블.컬럼을 참조하는 매핑은 None으로 변경한다.

    Args:
        mapping: LLM이 반환한 매핑
        schema_info: DB 스키마 정보
        field_names: 원본 필드명 목록

    Returns:
        검증된 매핑 딕셔너리
    """
    valid_columns: set[str] = set()
    lower_to_original: dict[str, str] = {}
    tables = schema_info.get("tables", {})
    for table_name, table_info in tables.items():
        for col in table_info.get("columns", []):
            col_name = col.get("name", "")
            if col_name:
                full_name = f"{table_name}.{col_name}"
                valid_columns.add(full_name)
                lower_to_original[full_name.lower()] = full_name

    validated: dict[str, Optional[str]] = {}
    for name in field_names:
        mapped_col = mapping.get(name)
        if mapped_col:
            if mapped_col in valid_columns:
                validated[name] = mapped_col
            elif mapped_col.lower() in lower_to_original:
                validated[name] = lower_to_original[mapped_col.lower()]
            else:
                logger.warning(
                    "매핑 검증 실패: '%s' -> '%s' (존재하지 않는 컬럼)",
                    name,
                    mapped_col,
                )
                validated[name] = None
        else:
            validated[name] = None

    return validated
