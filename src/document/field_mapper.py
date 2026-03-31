"""LLM 기반 필드-컬럼 매핑 모듈.

양식 필드명과 DB 컬럼명 간의 의미적 매핑을 수행한다.
다단계 매핑: 프롬프트 힌트 -> Redis synonyms -> EAV synonyms -> LLM 유사어 발견 -> LLM 통합 추론.

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
    FIELD_MAPPER_USER_PROMPT_WITH_EXAMPLES,
    FIELD_MAPPER_MULTI_DB_SYSTEM_PROMPT,
    FIELD_MAPPER_MULTI_DB_USER_PROMPT,
    FIELD_MAPPER_ENHANCED_SYSTEM_PROMPT,
    FIELD_MAPPER_ENHANCED_USER_PROMPT,
    FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT,
    FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT,
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


def _resolve_fallback_db_id(
    priority_db_ids: list[str],
    active_db_ids: list[str] | None,
    all_db_synonyms: dict[str, dict[str, list[str]]] | None = None,
) -> str:
    """EAV 매핑 등에서 사용할 폴백 DB ID를 결정한다.

    우선순위: priority_db_ids[0] > active_db_ids[0] > all_db_synonyms 첫 번째 키 > "_default"

    Args:
        priority_db_ids: 우선순위 DB 목록
        active_db_ids: 활성 DB ID 목록
        all_db_synonyms: DB별 synonyms (키가 db_id)

    Returns:
        유효한 DB 식별자 문자열
    """
    if priority_db_ids:
        return priority_db_ids[0]
    if active_db_ids:
        return active_db_ids[0]
    if all_db_synonyms:
        first_key = next(iter(all_db_synonyms), None)
        if first_key:
            return first_key
    return "_default"


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
    example_rows_by_sheet: dict[str, list[list[str]]] | None = None,
) -> dict[str, dict[str, Optional[str]]]:
    """시트별로 독립적으로 필드 매핑을 수행한다.

    각 시트의 헤더를 개별적으로 분석하여 시트마다 다른 매핑을 생성한다.

    Args:
        llm: LLM 인스턴스
        template_structure: 양식 구조 정보 (excel_parser 출력)
        schema_info: DB 스키마 정보
        target_sheets: 대상 시트명 목록 (None이면 전체 시트)
        example_rows_by_sheet: 시트별 예시 데이터 행 (선택).
            {"시트명": [[val1, val2, ...], ...]}

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

        sheet_examples = (
            example_rows_by_sheet.get(sheet_name)
            if example_rows_by_sheet
            else None
        )
        mapping = await _invoke_llm_mapping(
            llm, headers, schema_columns, example_rows=sheet_examples
        )
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
    example_rows: Optional[list[list[str]]] = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
    cache_manager: Optional[Any] = None,
    active_db_ids: list[str] | None = None,
    global_synonyms: dict[str, list[str]] | None = None,
) -> tuple[MappingResult, list[dict]]:
    """3단계 매핑을 수행한다.

    1단계: 사용자 프롬프트 힌트 (field_mapping_hints)
    2단계: Redis synonyms 기반 규칙 매핑
    2.5단계: EAV name synonyms 정확 일치
    2.8단계: LLM 유사어 발견 (경량 LLM 호출로 이름 수준 매칭)
    3단계: LLM 통합 추론 (Redis 유사어 + descriptions + EAV 결합 컨텍스트)
           + 결과를 즉시 Redis에 등록

    Args:
        llm: LLM 인스턴스
        field_names: 양식 필드명 목록
        field_mapping_hints: 사용자 지정 매핑 힌트
        all_db_synonyms: DB별 synonyms {db_id: {table.column: [words]}}
        all_db_descriptions: DB별 descriptions {db_id: {table.column: desc}}
        priority_db_ids: 우선순위 DB 목록
        example_rows: 예시 데이터 행 목록 (선택). 각 행은 field_names와 동일 순서의 값 리스트.
        eav_name_synonyms: EAV 속성명 유사어 매핑 (선택)
        cache_manager: SchemaCacheManager 인스턴스 (선택, Redis 즉시 등록용)
        active_db_ids: 활성 DB ID 목록 (선택, EAV 폴백 DB 결정용)

    Returns:
        (MappingResult 객체, LLM 추론 상세 정보 리스트)
    """
    result = MappingResult()
    remaining = set(field_names)
    llm_inference_details: list[dict] = []

    # EAV 폴백 DB ID 결정: priority > active > synonyms 키 > _default
    _fallback_db_id = _resolve_fallback_db_id(
        priority_db_ids, active_db_ids, all_db_synonyms
    )

    # --- 1단계: 프롬프트 힌트 ---
    _apply_hint_mapping(
        remaining, field_mapping_hints, all_db_synonyms, result
    )

    # --- 2단계: Redis synonyms 규칙 매핑 ---
    if remaining and all_db_synonyms:
        _apply_synonym_mapping(
            remaining, all_db_synonyms, priority_db_ids, result
        )

    # --- 2.5단계: EAV name synonyms 매칭 ---
    if remaining and eav_name_synonyms:
        _apply_eav_synonym_mapping(
            remaining, eav_name_synonyms, result, eav_db_id=_fallback_db_id,
            global_synonyms=global_synonyms,
        )

    # --- 2.8단계: LLM 유사어 발견 ---
    if remaining:
        await _apply_llm_synonym_discovery(
            llm=llm,
            remaining=remaining,
            all_db_synonyms=all_db_synonyms,
            eav_name_synonyms=eav_name_synonyms,
            priority_db_ids=priority_db_ids,
            result=result,
            cache_manager=cache_manager,
            fallback_db_id=_fallback_db_id,
        )

    # --- 3단계: LLM 통합 추론 (강화된 컨텍스트) ---
    if remaining:
        # 남은 필드에 해당하는 예시 데이터만 필터링
        remaining_examples: Optional[list[list[str]]] = None
        if example_rows:
            remaining_indices = [
                i for i, f in enumerate(field_names) if f in remaining
            ]
            remaining_examples = [
                [row[i] for i in remaining_indices if i < len(row)]
                for row in example_rows
            ]
        llm_inference_details = await _apply_llm_mapping_with_synonyms(
            llm,
            list(remaining),
            all_db_synonyms,
            all_db_descriptions,
            priority_db_ids,
            result,
            eav_name_synonyms=eav_name_synonyms,
            example_rows=remaining_examples,
        )

        # LLM 추론 결과를 즉시 Redis에 등록
        if llm_inference_details:
            await _register_llm_mappings_to_redis(
                cache_manager, llm_inference_details, eav_name_synonyms
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
        "3단계 매핑 완료: %d/%d 필드 매핑 (힌트=%d, 유사어=%d, EAV유사어=%d, LLM유사어=%d, LLM=%d), DB=%s",
        sum(1 for v in result.column_mapping.values() if v is not None),
        len(field_names),
        sum(1 for s in result.mapping_sources.values() if s == "hint"),
        sum(1 for s in result.mapping_sources.values() if s == "synonym"),
        sum(1 for s in result.mapping_sources.values() if s == "eav_synonym"),
        sum(1 for s in result.mapping_sources.values() if s == "llm_synonym"),
        sum(1 for s in result.mapping_sources.values() if s == "llm_inferred"),
        result.mapped_db_ids,
    )

    return result, llm_inference_details


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

    from src.utils.schema_utils import normalize_field_name

    for field in list(remaining):
        field_lower = normalize_field_name(field).lower()

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

    정규화된 소문자 필드명과 synonym 단어들을 비교한다.

    Args:
        field_lower: 정규화된 소문자 필드명
        synonyms: {table.column: [synonym_words]}

    Returns:
        매칭된 table.column 또는 None
    """
    from src.utils.schema_utils import normalize_field_name

    for col_key, words in synonyms.items():
        for word in words:
            if normalize_field_name(word).lower() == field_lower:
                return col_key
        # 컬럼명 자체도 매칭 시도
        col_name = col_key.split(".", 1)[-1] if "." in col_key else col_key
        if col_name.lower() == field_lower:
            return col_key

    return None


# === Step 2.5: EAV Synonym Mapping ===


def _apply_eav_synonym_mapping(
    remaining: set[str],
    eav_name_synonyms: dict[str, list[str]],
    result: MappingResult,
    eav_db_id: str = "_default",
    global_synonyms: dict[str, list[str]] | None = None,
) -> None:
    """EAV 속성 유사어로 매핑을 수행한다.

    EAV 속성명(OSType, Vendor 등)의 유사어에서 필드명이 매칭되면
    "EAV:속성명" 형식으로 매핑한다. global_synonyms에서도 병합하여 비교.

    Args:
        remaining: 아직 매핑되지 않은 필드 set
        eav_name_synonyms: {eav_name: [유사어 목록]}
        result: 매핑 결과 객체
        eav_db_id: EAV 패턴이 존재하는 DB의 식별자
        global_synonyms: 글로벌 유사어 사전 (선택)
    """
    from src.utils.schema_utils import normalize_field_name

    for field in list(remaining):
        field_norm = normalize_field_name(field).lower()
        for eav_name, words in eav_name_synonyms.items():
            # eav_names의 words + global에 같은 이름으로 등록된 words 병합
            combined_words = list(words)
            if global_synonyms and eav_name in global_synonyms:
                for gw in global_synonyms[eav_name]:
                    if gw not in combined_words:
                        combined_words.append(gw)

            matched = False
            for word in combined_words:
                if normalize_field_name(word).lower() == field_norm:
                    matched = True
                    break
            # EAV 속성명 자체도 매칭 시도
            if not matched and normalize_field_name(eav_name).lower() == field_norm:
                matched = True
            if matched:
                eav_key = f"EAV:{eav_name}"
                result.db_column_mapping.setdefault(eav_db_id, {})[field] = eav_key
                result.mapping_sources[field] = "eav_synonym"
                remaining.discard(field)
                break


# === Step 2.8: LLM Synonym Discovery ===


async def _apply_llm_synonym_discovery(
    llm: BaseChatModel,
    remaining: set[str],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    eav_name_synonyms: dict[str, list[str]] | None,
    priority_db_ids: list[str],
    result: MappingResult,
    cache_manager: Optional[Any] = None,
    fallback_db_id: str = "_default",
) -> None:
    """LLM 1회 호출로 미매핑 필드의 이름 수준 유사어 매칭을 수행한다.

    Step 2.5(EAV 유사어 정확 일치)와 Step 3(LLM 통합 추론) 사이의
    경량 LLM 호출 단계로, 컬럼/속성 이름만을 기반으로 매칭한다.

    Args:
        llm: LLM 인스턴스
        remaining: 아직 매핑되지 않은 필드 set
        all_db_synonyms: DB별 synonyms {db_id: {table.column: [words]}}
        eav_name_synonyms: EAV 속성명 유사어 매핑 (선택)
        priority_db_ids: 우선순위 DB 목록
        result: 매핑 결과 객체
        cache_manager: SchemaCacheManager 인스턴스 (선택, synonym 자동 등록용)
        fallback_db_id: EAV 매핑 시 사용할 폴백 DB 식별자
    """
    if not remaining:
        return

    # DB 컬럼명 + synonym words를 {db_id:table.column: [유의어]} 형식으로 구성
    import json as _json

    ordered_db_ids = priority_db_ids + [
        d for d in all_db_synonyms if d not in priority_db_ids
    ]
    db_schema_dict: dict[str, list[str]] = {}
    for db_id in ordered_db_ids:
        synonyms = all_db_synonyms.get(db_id, {})
        if not synonyms:
            continue
        for col_key, words in synonyms.items():
            full_key = f"{db_id}:{col_key}"
            db_schema_dict[full_key] = words if isinstance(words, list) else []

    db_columns_with_synonyms = (
        _json.dumps(db_schema_dict, ensure_ascii=False, indent=2)
        if db_schema_dict
        else "(없음)"
    )

    # EAV 속성도 유의어 포함하여 JSON 구성
    eav_dict: dict[str, list[str]] = {}
    if eav_name_synonyms:
        for eav_name, words in eav_name_synonyms.items():
            eav_dict[f"EAV:{eav_name}"] = words if isinstance(words, list) else []
    eav_attributes_with_synonyms = (
        _json.dumps(eav_dict, ensure_ascii=False, indent=2)
        if eav_dict
        else "(없음)"
    )

    # 미매핑 필드 목록 (JSON 배열)
    unmapped_fields_text = _json.dumps(sorted(remaining), ensure_ascii=False)

    user_prompt = FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT.format(
        unmapped_fields=unmapped_fields_text,
        db_columns_with_synonyms=db_columns_with_synonyms,
        eav_attributes_with_synonyms=eav_attributes_with_synonyms,
    )

    messages = [
        SystemMessage(content=FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    try:
        response = await llm.ainvoke(messages)
        parsed = extract_json_from_response(response.content)
        if not parsed:
            logger.warning("Step 2.8 LLM 유사어 발견: JSON 파싱 실패")
            return
    except Exception as e:
        logger.warning("Step 2.8 LLM 유사어 발견 호출 실패: %s", e)
        return

    eav_db_id = priority_db_ids[0] if priority_db_ids else fallback_db_id
    mapped_fields: list[tuple[str, str, str]] = []  # (field, matched_key, type)

    # LLM 응답 키를 정규화하여 역매핑 구축 (퍼지 매칭)
    from src.utils.schema_utils import normalize_field_name

    normalized_lookup: dict[str, dict] = {}
    for key, value in parsed.items():
        if isinstance(value, dict):
            norm_key = normalize_field_name(key).lower()
            normalized_lookup[norm_key] = value

    for field in list(remaining):
        norm_field = normalize_field_name(field).lower()
        mapping_info = parsed.get(field) or normalized_lookup.get(norm_field)
        if not mapping_info or not isinstance(mapping_info, dict):
            continue

        matched_key = mapping_info.get("matched_key")
        if not matched_key:
            continue

        if matched_key.startswith("EAV:"):
            # EAV 매핑
            result.db_column_mapping.setdefault(eav_db_id, {})[field] = matched_key
            result.mapping_sources[field] = "llm_synonym"
            remaining.discard(field)
            mapped_fields.append((field, matched_key, "eav"))
        elif ":" in matched_key:
            # db_id:table.column 형식
            parts = matched_key.split(":", 1)
            db_id = parts[0]
            column = parts[1]
            result.db_column_mapping.setdefault(db_id, {})[field] = column
            result.mapping_sources[field] = "llm_synonym"
            remaining.discard(field)
            mapped_fields.append((field, matched_key, "column"))

    if mapped_fields:
        logger.info(
            "Step 2.8 LLM 유사어 발견: %d건 매핑 성공", len(mapped_fields)
        )

    # 글로벌 synonym 자동 등록
    await _register_llm_synonym_discoveries_to_redis(
        cache_manager, mapped_fields, eav_name_synonyms
    )


async def _register_llm_synonym_discoveries_to_redis(
    cache_manager: Optional[Any],
    mapped_fields: list[tuple[str, str, str]],
    eav_name_synonyms: dict[str, list[str]] | None = None,
) -> None:
    """Step 2.8에서 발견한 유사어를 Redis에 자동 등록한다.

    컬럼 매핑의 경우 add_global_synonym()으로 등록하고,
    EAV 매핑의 경우 eav_name_synonyms에 필드명을 추가하여 저장한다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스 (None 가능)
        mapped_fields: (field_name, matched_key, type) 튜플 리스트
        eav_name_synonyms: 기존 EAV 속성명 유사어 매핑 (선택)
    """
    if not mapped_fields:
        return

    if cache_manager is None:
        logger.debug("cache_manager가 None이므로 LLM 유사어 Redis 등록 스킵")
        return

    if not getattr(cache_manager, "redis_available", False):
        logger.debug("Redis 미연결 상태이므로 LLM 유사어 Redis 등록 스킵")
        return

    registered_count = 0
    eav_updated = False

    for field, matched_key, match_type in mapped_fields:
        try:
            if match_type == "eav":
                # EAV 매핑: eav_name_synonyms에 필드명 추가 + global에도 등록
                eav_name = matched_key[4:]  # "EAV:" 접두사 제거
                redis_cache = getattr(cache_manager, "_redis_cache", None)
                if redis_cache is not None:
                    current_eav = await redis_cache.load_eav_name_synonyms()
                    existing_words = current_eav.get(eav_name, [])
                    if field not in existing_words:
                        existing_words.append(field)
                        current_eav[eav_name] = existing_words
                        await redis_cache.save_eav_name_synonyms(current_eav)
                        eav_updated = True
                    # global에도 등록 (통합 관리)
                    await redis_cache.add_global_synonym(eav_name, [field])
                    registered_count += 1
                    logger.debug(
                        "Step 2.8 EAV 유사어 등록 (eav+global): %s -> EAV:%s",
                        field,
                        eav_name,
                    )
            elif match_type == "column":
                # 컬럼 매핑: add_global_synonym으로 등록
                # matched_key = "db_id:table.column"
                column = matched_key.split(":", 1)[1]
                # bare_column_name: table.column에서 column 부분만 추출
                bare_column_name = (
                    column.split(".", 1)[1] if "." in column else column
                )
                success = await cache_manager.add_global_synonym(
                    bare_column_name, [field]
                )
                if success:
                    registered_count += 1
                    logger.debug(
                        "Step 2.8 글로벌 유사어 등록: %s -> %s",
                        field,
                        bare_column_name,
                    )
        except Exception as e:
            logger.warning(
                "Step 2.8 유사어 Redis 등록 실패 (%s -> %s): %s",
                field,
                matched_key,
                e,
            )

    if registered_count > 0:
        logger.info(
            "Step 2.8 유사어 %d건 Redis 등록 완료 (EAV 갱신: %s)",
            registered_count,
            eav_updated,
        )


# === Step 3: Enhanced LLM Mapping (with synonyms context) ===


async def _apply_llm_mapping_with_synonyms(
    llm: BaseChatModel,
    remaining_fields: list[str],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    all_db_descriptions: dict[str, dict[str, str]],
    priority_db_ids: list[str],
    result: MappingResult,
    eav_name_synonyms: dict[str, list[str]] | None = None,
    example_rows: Optional[list[list[str]]] = None,
) -> list[dict]:
    """Redis 유사어 + DB descriptions + EAV를 결합한 강화 컨텍스트로 LLM 매핑을 수행한다.

    기존 _apply_llm_mapping()과 달리, synonyms/descriptions/EAV를 하나의
    강화된 프롬프트로 결합하여 LLM에게 1회 호출로 매핑을 요청한다.
    confidence가 "low"인 매핑은 제외한다.

    Args:
        llm: LLM 인스턴스
        remaining_fields: 매핑되지 않은 필드 목록
        all_db_synonyms: DB별 synonyms {db_id: {table.column: [words]}}
        all_db_descriptions: DB별 descriptions {db_id: {table.column: desc}}
        priority_db_ids: 우선순위 DB 목록
        result: 매핑 결과 객체
        eav_name_synonyms: EAV 속성명 유사어 매핑 (선택)
        example_rows: 예시 데이터 행 목록 (선택)

    Returns:
        LLM 추론 상세 정보 리스트 (보고서 생성용).
        각 항목: {field, db_id, column, matched_synonym, confidence, reason}
    """
    if not remaining_fields:
        return []

    if not all_db_descriptions:
        logger.warning("DB descriptions가 없어 강화된 LLM 매핑을 수행할 수 없습니다.")
        return []

    # DB별 스키마 정보를 synonyms + descriptions 결합 형식으로 포맷
    ordered_db_ids = priority_db_ids + [
        d for d in all_db_descriptions if d not in priority_db_ids
    ]

    db_schema_parts: list[str] = []
    for db_id in ordered_db_ids:
        descs = all_db_descriptions.get(db_id, {})
        syns = all_db_synonyms.get(db_id, {})
        if not descs and not syns:
            continue

        lines = [f"## DB: {db_id}"]
        # descriptions에 있는 컬럼 기준
        all_columns = set(descs.keys()) | set(syns.keys())
        for col_key in sorted(all_columns):
            desc = descs.get(col_key, "")
            syn_list = syns.get(col_key, [])

            # synonyms에서 단어 목록 추출 (리스트 또는 dict 형식 지원)
            words: list[str] = []
            if isinstance(syn_list, list):
                words = syn_list
            elif isinstance(syn_list, dict):
                words = syn_list.get("words", [])

            entry = f"- {col_key}"
            if desc:
                entry += f" -- {desc}"
            if words:
                entry += f" [유사: {', '.join(words[:7])}]"
            lines.append(entry)

        db_schema_parts.append("\n".join(lines))

    schema_text = "\n\n".join(db_schema_parts)

    # EAV 컨텍스트 구성
    eav_context_text = "(없음)"
    if eav_name_synonyms:
        eav_lines: list[str] = []
        for eav_name, words in eav_name_synonyms.items():
            if words:
                eav_lines.append(f"- EAV:{eav_name} [유사: {', '.join(words[:7])}]")
            else:
                eav_lines.append(f"- EAV:{eav_name}")
        if eav_lines:
            eav_context_text = "\n".join(eav_lines)

    # 필드명 포맷
    if example_rows:
        field_names_text = _format_field_names_with_examples(
            remaining_fields, example_rows
        )
    else:
        field_names_text = "\n".join(f"- {name}" for name in remaining_fields)

    # EAV 패턴이 감지된 경우 범용 EAV 매핑 가이드 삽입
    if eav_name_synonyms:
        eav_guide = _build_eav_mapping_guide(eav_name_synonyms)
        schema_text = eav_guide + "\n\n" + schema_text

    user_prompt = FIELD_MAPPER_ENHANCED_USER_PROMPT.format(
        field_names=field_names_text,
        db_schema_with_synonyms=schema_text,
        eav_context=eav_context_text,
    )

    messages = [
        SystemMessage(content=FIELD_MAPPER_ENHANCED_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    llm_inference_details: list[dict] = []

    for attempt in range(2):
        try:
            response = await llm.ainvoke(messages)
            parsed = extract_json_from_response(response.content)
            if parsed:
                # LLM 응답 키를 정규화하여 역매핑 구축 (퍼지 매칭)
                from src.utils.schema_utils import normalize_field_name

                normalized_lookup_s3: dict[str, dict] = {}
                for key, value in parsed.items():
                    if isinstance(value, dict):
                        norm_key = normalize_field_name(key).lower()
                        normalized_lookup_s3[norm_key] = value

                for field in remaining_fields:
                    norm_field = normalize_field_name(field).lower()
                    mapping_info = parsed.get(field) or normalized_lookup_s3.get(norm_field)
                    if not mapping_info or not isinstance(mapping_info, dict):
                        continue

                    confidence = mapping_info.get("confidence", "low")
                    if confidence == "low":
                        logger.debug(
                            "LLM 매핑 low confidence 제외: %s -> %s",
                            field,
                            mapping_info.get("column"),
                        )
                        continue

                    db_id = mapping_info.get("db_id")
                    column = mapping_info.get("column")
                    if not db_id or not column:
                        continue

                    result.db_column_mapping.setdefault(db_id, {})[field] = column
                    result.mapping_sources[field] = "llm_inferred"

                    llm_inference_details.append({
                        "field": field,
                        "db_id": db_id,
                        "column": column,
                        "matched_synonym": mapping_info.get("matched_synonym"),
                        "confidence": confidence,
                        "reason": mapping_info.get("reason", ""),
                    })

                return llm_inference_details
        except Exception as e:
            logger.warning(
                "강화된 LLM 매핑 호출 실패 (시도 %d): %s", attempt + 1, e
            )

        messages.append(HumanMessage(
            content=(
                "반드시 유효한 JSON만 출력하세요. 각 필드에 대해 "
                '{"db_id": "...", "column": "...", "matched_synonym": ..., '
                '"confidence": "high|medium|low", "reason": "..."} 형식으로 응답합니다.'
            )
        ))

    logger.error("강화된 LLM 매핑 실패: 모든 시도 소진")
    return []


async def _register_llm_mappings_to_redis(
    cache_manager: Optional[Any],
    llm_inference_details: list[dict],
    eav_name_synonyms: dict[str, list[str]] | None = None,
) -> None:
    """LLM 추론 결과를 즉시 Redis에 등록한다.

    EAV 매핑(EAV: 접두사)은 eav_name_synonyms에 필드명을 추가하여 저장하고,
    일반 매핑은 cache_manager.add_synonyms()로 등록한다.
    cache_manager가 None이거나 redis_available이 False이면 스킵한다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스 (None 가능)
        llm_inference_details: LLM 추론 상세 정보 리스트
        eav_name_synonyms: 기존 EAV 속성명 유사어 매핑 (선택)
    """
    if not llm_inference_details:
        return

    if cache_manager is None:
        logger.debug("cache_manager가 None이므로 LLM 매핑 Redis 등록 스킵")
        return

    if not getattr(cache_manager, "redis_available", False):
        logger.debug("Redis 미연결 상태이므로 LLM 매핑 Redis 등록 스킵")
        return

    registered_count = 0
    eav_updated = False

    for detail in llm_inference_details:
        field = detail.get("field", "")
        db_id = detail.get("db_id", "")
        column = detail.get("column", "")

        if not field or not column:
            continue

        try:
            if column.startswith("EAV:"):
                # EAV 매핑: eav_name_synonyms + global 양쪽 저장
                eav_name = column[4:]
                redis_cache = getattr(cache_manager, "_redis_cache", None)
                if redis_cache is not None:
                    current_eav = await redis_cache.load_eav_name_synonyms()
                    existing_words = current_eav.get(eav_name, [])
                    if field not in existing_words:
                        existing_words.append(field)
                        current_eav[eav_name] = existing_words
                        await redis_cache.save_eav_name_synonyms(current_eav)
                        eav_updated = True
                    # global에도 등록 (통합 관리)
                    await redis_cache.add_global_synonym(eav_name, [field])
                    registered_count += 1
                    logger.debug(
                        "EAV 유사어 등록 (eav+global): %s -> EAV:%s",
                        field,
                        eav_name,
                    )
            else:
                # 일반 매핑: global에 bare column name으로 저장
                bare_name = column.split(".", 1)[1] if "." in column else column
                redis_cache = getattr(cache_manager, "_redis_cache", None)
                if redis_cache is not None:
                    success = await redis_cache.add_global_synonym(
                        bare_name, [field]
                    )
                else:
                    # 폴백: cache_manager 경유
                    success = await cache_manager.add_synonyms(
                        db_id, column, [field], source="llm_inferred"
                    )
                if success:
                    registered_count += 1
                    logger.debug(
                        "글로벌 유사어 등록: %s -> %s", field, bare_name
                    )
                else:
                    logger.warning(
                        "synonym 등록 실패: %s -> %s", field, bare_name
                    )
        except Exception as e:
            logger.warning(
                "LLM 매핑 Redis 등록 실패 (%s -> %s): %s", field, column, e
            )

    if registered_count > 0:
        logger.info(
            "LLM 추론 매핑 %d건 Redis 즉시 등록 완료 (EAV 갱신: %s)",
            registered_count,
            eav_updated,
        )


# === Step 3 (Legacy): LLM Mapping ===


async def _apply_llm_mapping(
    llm: BaseChatModel,
    remaining_fields: list[str],
    all_db_descriptions: dict[str, dict[str, str]],
    priority_db_ids: list[str],
    result: MappingResult,
    example_rows: Optional[list[list[str]]] = None,
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
        example_rows: 예시 데이터 행 목록 (선택). 각 행은 remaining_fields와 동일 순서의 값 리스트.
    """
    if not remaining_fields:
        return

    if not all_db_descriptions:
        logger.warning("DB descriptions가 없어 LLM 매핑을 수행할 수 없습니다.")
        return

    # 멀티 DB descriptions를 하나의 프롬프트로 구성
    llm_mapping = await _invoke_llm_mapping_multi_db(
        llm,
        remaining_fields,
        all_db_descriptions,
        priority_db_ids,
        example_rows=example_rows,
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
    example_rows: Optional[list[list[str]]] = None,
) -> dict[str, Optional[dict[str, str]]]:
    """멀티 DB 환경에서 LLM 매핑을 수행한다.

    Args:
        llm: LLM 인스턴스
        field_names: 매핑할 필드 목록
        all_db_descriptions: DB별 descriptions
        priority_db_ids: 우선순위 DB
        example_rows: 예시 데이터 행 목록 (선택). 각 행은 field_names와 동일 순서의 값 리스트.

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

    if example_rows:
        field_names_text = _format_field_names_with_examples(
            field_names, example_rows
        )
    else:
        field_names_text = "\n".join(f"- {name}" for name in field_names)

    user_prompt = FIELD_MAPPER_MULTI_DB_USER_PROMPT.format(
        field_names=field_names_text,
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
                            "db_id": ordered_db_ids[0] if ordered_db_ids else "_default",
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
    example_rows: Optional[list[list[str]]] = None,
) -> dict[str, Optional[str]]:
    """LLM을 호출하여 필드 매핑을 수행한다 (단일 DB).

    JSON 파싱 실패 시 1회 재시도한다.

    Args:
        llm: LLM 인스턴스
        field_names: 양식 필드명 목록
        schema_columns: 포맷된 스키마 컬럼 문자열
        example_rows: 예시 데이터 행 목록 (선택). 각 행은 field_names와 동일한 순서의 값 리스트.

    Returns:
        매핑 딕셔너리
    """
    if example_rows:
        field_names_with_examples = _format_field_names_with_examples(
            field_names, example_rows
        )
        user_prompt = FIELD_MAPPER_USER_PROMPT_WITH_EXAMPLES.format(
            field_names_with_examples=field_names_with_examples,
            schema_columns=schema_columns,
        )
    else:
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


def _build_eav_mapping_guide(
    eav_name_synonyms: dict[str, list[str]],
) -> str:
    """EAV 속성 유사어 정보에서 범용 매핑 가이드를 동적으로 생성한다.

    EAV 패턴이 감지된 DB에 대해, 속성명과 유사어 정보를 기반으로
    LLM이 매핑에 활용할 수 있는 가이드 텍스트를 생성한다.

    Args:
        eav_name_synonyms: {eav_name: [유사어 목록]}

    Returns:
        EAV 매핑 가이드 문자열
    """
    lines = ["## EAV 매핑 가이드"]
    lines.append("")
    lines.append("이 DB는 EAV(Entity-Attribute-Value) 패턴을 사용합니다.")
    lines.append("엔티티 속성이 별도 설정 테이블의 행으로 저장되어 있습니다.")
    lines.append("매핑 시 'EAV:속성명' 형식을 사용하세요.")
    lines.append("")
    lines.append("### EAV 속성 목록")
    for eav_name, words in eav_name_synonyms.items():
        if words:
            synonyms_text = ", ".join(words[:5])
            lines.append(f"- EAV:{eav_name} (유사어: {synonyms_text})")
        else:
            lines.append(f"- EAV:{eav_name}")
    return "\n".join(lines)


def _format_field_names_with_examples(
    field_names: list[str],
    example_rows: list[list[str]],
) -> str:
    """필드명과 예시 데이터를 결합하여 프롬프트용 문자열로 포맷한다.

    Args:
        field_names: 양식 필드명 목록
        example_rows: 예시 데이터 행 목록. 각 행은 field_names와 동일 순서의 값 리스트.

    Returns:
        포맷된 문자열. 예: '- 서버명 (예시: "web-server-01", "db-server-02")'
    """
    lines: list[str] = []
    for i, name in enumerate(field_names):
        examples: list[str] = []
        for row in example_rows:
            if i < len(row):
                val = str(row[i]).strip()
                if val:
                    examples.append(val)
            if len(examples) >= 3:
                break
        if examples:
            quoted = ", ".join(f'"{ex}"' for ex in examples)
            lines.append(f"- {name} (예시: {quoted})")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)


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

    # EAV 가상 컬럼 추가 (_structure_meta에서 동적 추출)
    structure_meta = schema_info.get("_structure_meta")
    if structure_meta:
        eav_patterns = [
            p for p in structure_meta.get("patterns", [])
            if p.get("type") == "eav"
        ]
        if eav_patterns:
            lines.append("")
            lines.append("# EAV 속성 (피벗 쿼리로 추출)")
            lines.append("# 매핑 시 'EAV:속성명' 형식을 사용하세요.")
            for p in eav_patterns:
                for attr in p.get("known_attributes", []):
                    lines.append(f"- EAV:{attr} -- EAV 피벗 속성")

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

    # EAV known attributes (_structure_meta에서 동적 추출)
    structure_meta = schema_info.get("_structure_meta")
    known_eav_attrs: set[str] = set()
    if structure_meta:
        for p in structure_meta.get("patterns", []):
            if p.get("type") == "eav":
                known_eav_attrs.update(p.get("known_attributes", []))

    validated: dict[str, Optional[str]] = {}
    for name in field_names:
        mapped_col = mapping.get(name)
        if mapped_col:
            # EAV 속성 검증
            if mapped_col.startswith("EAV:"):
                attr_name = mapped_col[4:]
                if attr_name in known_eav_attrs:
                    validated[name] = mapped_col
                else:
                    logger.warning(
                        "EAV 매핑 검증 실패: '%s' -> '%s' (알 수 없는 EAV 속성)",
                        name,
                        mapped_col,
                    )
                    validated[name] = None
            # 정규 컬럼 검증
            elif mapped_col in valid_columns:
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


# === MD Feedback: Diff & Redis Apply ===


def analyze_md_diff(
    original_mappings: list[dict],
    modified_mappings: list[dict],
) -> dict:
    """원본과 수정된 매핑 보고서를 비교하여 변경사항을 추출한다.

    규칙 기반 비교로 처리한다 (LLM 불필요).
    원본과 수정본의 매핑 테이블을 필드명 기준으로 비교하여
    추가/수정/삭제를 감지한다.

    Args:
        original_mappings: parse_mapping_report()의 원본 파싱 결과
        modified_mappings: parse_mapping_report()의 수정본 파싱 결과

    Returns:
        {
            "added": [{"field": "...", "column": "...", "db_id": "..."}],
            "modified": [{"field": "...", "old_column": "...", "new_column": "...",
                          "old_db_id": "...", "new_db_id": "..."}],
            "deleted": [{"field": "...", "old_column": "...", "old_db_id": "..."}],
            "unchanged": int,
            "summary": "변경 요약 문자열"
        }
    """
    added: list[dict] = []
    modified: list[dict] = []
    deleted: list[dict] = []
    unchanged = 0

    # 원본/수정본을 field명 기준으로 인덱싱
    orig_by_field: dict[str, dict] = {
        m["field"]: m for m in original_mappings if m.get("field")
    }
    mod_by_field: dict[str, dict] = {
        m["field"]: m for m in modified_mappings if m.get("field")
    }

    # 원본 기준으로 순회
    for field, orig in orig_by_field.items():
        orig_col = orig.get("column")  # None이면 매핑 불가였음
        orig_db = orig.get("db_id")

        if field in mod_by_field:
            mod = mod_by_field[field]
            mod_col = mod.get("column")
            mod_db = mod.get("db_id")

            if orig_col is None and mod_col is not None:
                # 원본에서 매핑 불가 -> 수정본에서 매핑 추가
                added.append({
                    "field": field,
                    "column": mod_col,
                    "db_id": mod_db,
                })
            elif orig_col is not None and mod_col is None:
                # 원본에서 매핑 있음 -> 수정본에서 매핑 불가 또는 제거
                deleted.append({
                    "field": field,
                    "old_column": orig_col,
                    "old_db_id": orig_db,
                })
            elif orig_col is not None and mod_col is not None:
                # 양쪽 모두 매핑 있음 -> 변경 여부 확인
                if orig_col != mod_col or orig_db != mod_db:
                    modified.append({
                        "field": field,
                        "old_column": orig_col,
                        "new_column": mod_col,
                        "old_db_id": orig_db,
                        "new_db_id": mod_db,
                    })
                else:
                    unchanged += 1
            else:
                # 양쪽 모두 매핑 불가 -> 변경 없음
                unchanged += 1
        else:
            # 수정본에서 행이 삭제됨
            if orig_col is not None:
                deleted.append({
                    "field": field,
                    "old_column": orig_col,
                    "old_db_id": orig_db,
                })
            # 원본에서도 매핑 불가였으면 의미 있는 변경이 아님

    # 수정본에만 있는 field (원본에 없음) -> 무시 (원본 양식에 없는 필드)

    # 요약 생성
    parts: list[str] = []
    if added:
        parts.append(f"{len(added)}건 추가")
    if modified:
        parts.append(f"{len(modified)}건 수정")
    if deleted:
        parts.append(f"{len(deleted)}건 삭제")
    summary = ", ".join(parts) if parts else "변경사항 없음"

    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
        "summary": summary,
    }


async def apply_mapping_feedback_to_redis(
    cache_manager: Any,
    diff_result: dict,
) -> dict:
    """MD 비교 결과를 Redis synonyms에 반영한다.

    added/modified/deleted 각각을 Redis에 반영하며,
    개별 항목이 실패해도 나머지는 계속 처리한다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스
            (cache_manager.redis_available, cache_manager._redis_cache 사용)
        diff_result: analyze_md_diff()의 반환값

    Returns:
        {"registered": N, "modified": N, "deleted": N, "errors": [...], "summary": "..."}
    """
    errors: list[str] = []
    registered = 0
    modified_count = 0
    deleted_count = 0

    # cache_manager 유효성 검증
    if cache_manager is None:
        return {
            "registered": 0,
            "modified": 0,
            "deleted": 0,
            "errors": ["cache_manager가 None입니다."],
            "summary": "Redis 반영 실패: cache_manager 없음",
        }

    if not getattr(cache_manager, "redis_available", False):
        return {
            "registered": 0,
            "modified": 0,
            "deleted": 0,
            "errors": ["Redis가 연결되지 않았습니다."],
            "summary": "Redis 반영 실패: Redis 미연결",
        }

    redis_cache = getattr(cache_manager, "_redis_cache", None)

    # --- added: 새 매핑 등록 ---
    for item in diff_result.get("added", []):
        field = item.get("field", "")
        column = item.get("column", "")
        db_id = item.get("db_id")

        if not field or not column:
            continue

        try:
            if column.startswith("EAV:"):
                # EAV 매핑: eav_name_synonyms에 field 추가
                eav_name = column[4:]
                if redis_cache is not None:
                    current_eav = await redis_cache.load_eav_name_synonyms()
                    existing_words = current_eav.get(eav_name, [])
                    if field not in existing_words:
                        existing_words.append(field)
                        current_eav[eav_name] = existing_words
                        await redis_cache.save_eav_name_synonyms(current_eav)
                    registered += 1
                else:
                    errors.append(f"EAV 등록 실패 ({field}): redis_cache 없음")
            else:
                # 일반 매핑: DB별 synonym에 등록
                success = await cache_manager.add_synonyms(
                    db_id, column, [field], source="user_corrected"
                )
                # global에도 bare name으로 등록
                bare_name = column.split(".", 1)[1] if "." in column else column
                global_ok = await cache_manager.add_global_synonym(bare_name, [field])
                if success or global_ok:
                    registered += 1
                else:
                    errors.append(
                        f"유사어 등록 실패 ({field} -> {column}): "
                        "add_synonyms 반환 False"
                    )
        except Exception as e:
            errors.append(f"등록 실패 ({field} -> {column}): {e}")

    # --- modified: 기존 매핑 변경 ---
    for item in diff_result.get("modified", []):
        field = item.get("field", "")
        old_column = item.get("old_column", "")
        new_column = item.get("new_column", "")
        old_db_id = item.get("old_db_id")
        new_db_id = item.get("new_db_id")

        if not field:
            continue

        try:
            # 기존 매핑 제거
            if old_column and old_column.startswith("EAV:"):
                eav_name = old_column[4:]
                if redis_cache is not None:
                    current_eav = await redis_cache.load_eav_name_synonyms()
                    existing_words = current_eav.get(eav_name, [])
                    if field in existing_words:
                        existing_words.remove(field)
                        current_eav[eav_name] = existing_words
                        await redis_cache.save_eav_name_synonyms(current_eav)
            elif old_column and old_db_id:
                await cache_manager.remove_synonyms(
                    old_db_id, old_column, [field]
                )

            # 새 매핑 등록
            if new_column and new_column.startswith("EAV:"):
                eav_name = new_column[4:]
                if redis_cache is not None:
                    current_eav = await redis_cache.load_eav_name_synonyms()
                    existing_words = current_eav.get(eav_name, [])
                    if field not in existing_words:
                        existing_words.append(field)
                        current_eav[eav_name] = existing_words
                        await redis_cache.save_eav_name_synonyms(current_eav)
            elif new_column and new_db_id:
                await cache_manager.add_synonyms(
                    new_db_id, new_column, [field], source="user_corrected"
                )
                # global에도 bare name으로 등록
                bare_name = new_column.split(".", 1)[1] if "." in new_column else new_column
                await cache_manager.add_global_synonym(bare_name, [field])

            modified_count += 1
        except Exception as e:
            errors.append(
                f"수정 실패 ({field}: {old_column} -> {new_column}): {e}"
            )

    # --- deleted: 매핑 제거 ---
    for item in diff_result.get("deleted", []):
        field = item.get("field", "")
        old_column = item.get("old_column", "")
        old_db_id = item.get("old_db_id")

        if not field:
            continue

        try:
            if old_column and old_column.startswith("EAV:"):
                eav_name = old_column[4:]
                if redis_cache is not None:
                    current_eav = await redis_cache.load_eav_name_synonyms()
                    existing_words = current_eav.get(eav_name, [])
                    if field in existing_words:
                        existing_words.remove(field)
                        current_eav[eav_name] = existing_words
                        await redis_cache.save_eav_name_synonyms(current_eav)
                    deleted_count += 1
                else:
                    errors.append(f"EAV 삭제 실패 ({field}): redis_cache 없음")
            elif old_column and old_db_id:
                success = await cache_manager.remove_synonyms(
                    old_db_id, old_column, [field]
                )
                if success:
                    deleted_count += 1
                else:
                    errors.append(
                        f"유사어 삭제 실패 ({field} <- {old_column}): "
                        "remove_synonyms 반환 False"
                    )
            else:
                # column이나 db_id가 없으면 삭제 불가
                errors.append(
                    f"삭제 스킵 ({field}): column 또는 db_id 정보 없음"
                )
        except Exception as e:
            errors.append(f"삭제 실패 ({field} <- {old_column}): {e}")

    # 요약
    parts: list[str] = []
    if registered:
        parts.append(f"{registered}건 등록")
    if modified_count:
        parts.append(f"{modified_count}건 수정")
    if deleted_count:
        parts.append(f"{deleted_count}건 삭제")
    if errors:
        parts.append(f"{len(errors)}건 오류")
    summary = ", ".join(parts) if parts else "처리 없음"

    logger.info(
        "매핑 피드백 Redis 반영: %s (오류 %d건)", summary, len(errors)
    )

    return {
        "registered": registered,
        "modified": modified_count,
        "deleted": deleted_count,
        "errors": errors,
        "summary": summary,
    }
