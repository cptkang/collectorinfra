"""LLM 기반 필드-컬럼 매핑 모듈.

양식 필드명과 DB 컬럼명 간의 의미적 매핑을 수행한다.
다단계 매핑: 프롬프트 힌트 -> Redis synonyms -> EAV synonyms -> LLM 유사어 발견 -> LLM 통합 추론.

decision.md D-007에 따라 LLM 의미 매핑을 사용하며,
xls_plan.md에 따라 매핑을 최우선 수행하고 그 결과로
대상 DB를 결정하는 Mapping-First 전략을 구현한다.

plans/166 I2 — 단계별 구현은 같은 패키지의 다섯 모듈로 나눴고 이 모듈은 Facade다.
`mapping_steps`(구조 선언 판정·결과 객체·필드 추출·1단계 힌트) · `synonym_matching`(2·2.5단계)
· `llm_mapping`(레거시 LLM 호출·포맷·검증) · `redis_registration`(LLM 매핑 자동 등록)
· `mapping_feedback`(MD 피드백). 옮긴 이름은 여기서 옛 이름으로 재노출한다(D-337 ①·③).
여기 남은 함수(`perform_3step_mapping`·2.8단계·3단계 강화 추론)는 테스트가 이 모듈 경로에서
patch하는 심볼(프롬프트 상수·`_apply_llm_mapping_with_synonyms`·`_register_llm_mappings_to_redis`)을
읽거나 부르는 seam 소유자다(D-337 ②).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.document.llm_mapping import build_eav_mapping_guide as _build_eav_mapping_guide
from src.document.llm_mapping import (
    format_field_names_with_examples as _format_field_names_with_examples,
)
from src.document.llm_mapping import format_schema_columns as _format_schema_columns
from src.document.llm_mapping import invoke_llm_mapping as _invoke_llm_mapping
from src.document.llm_mapping import validate_mapping as _validate_mapping
from src.document.mapping_feedback import analyze_md_diff, apply_mapping_feedback_to_redis
from src.document.mapping_steps import (
    MappingResult,
    extract_field_names,
)
from src.document.mapping_steps import apply_hint_mapping as _apply_hint_mapping
from src.document.mapping_steps import core_entity_tables as _core_entity_tables
from src.document.mapping_steps import is_metric_usage_field as _is_metric_usage_field
from src.document.mapping_steps import load_structure_declarations as _load_structure_declarations
from src.document.mapping_steps import resolve_fallback_db_id as _resolve_fallback_db_id
from src.document.mapping_steps import schema_uses_eav_metric_pivot as _schema_uses_eav_metric_pivot
from src.document.mapping_steps import sub_table_scope as _sub_table_scope
from src.document.redis_registration import (
    register_llm_mappings_to_redis as _register_llm_mappings_to_redis,
)
from src.document.redis_registration import (
    register_llm_synonym_discoveries_to_redis as _register_llm_synonym_discoveries_to_redis,
)
from src.document.synonym_matching import apply_eav_synonym_mapping as _apply_eav_synonym_mapping
from src.document.synonym_matching import apply_synonym_mapping as _apply_synonym_mapping
from src.document.synonym_matching import synonym_match as _synonym_match
from src.prompts.field_mapper import (
    FIELD_MAPPER_ENHANCED_SYSTEM_PROMPT,
    FIELD_MAPPER_ENHANCED_USER_PROMPT,
    FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT,
    FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT,
)
from src.utils.json_extract import extract_json_from_response
from src.utils.month_structure import is_month_structure_field

logger = logging.getLogger(__name__)

#: 이 모듈이 계속 노출하는 이름 — Facade 자체 API + plans/166 I2 이동분의 하위호환 재노출이다
#: (D-337 ① 1사이클). 신규 코드는 이동한 모듈에서 공개 이름으로 직접 임포트할 것.
__all__ = [
    # Facade 자체(seam 소유 — D-337 ②)
    "map_fields",
    "map_fields_per_sheet",
    "perform_3step_mapping",
    "_apply_llm_synonym_discovery",
    "_apply_llm_mapping_with_synonyms",
    # mapping_steps
    "_is_metric_usage_field",
    "_load_structure_declarations",
    "_core_entity_tables",
    "_schema_uses_eav_metric_pivot",
    "_sub_table_scope",
    "MappingResult",
    "_resolve_fallback_db_id",
    "extract_field_names",
    "_apply_hint_mapping",
    # synonym_matching
    "_apply_synonym_mapping",
    "_synonym_match",
    "_apply_eav_synonym_mapping",
    # redis_registration
    "_register_llm_synonym_discoveries_to_redis",
    "_register_llm_mappings_to_redis",
    # llm_mapping
    "_invoke_llm_mapping",
    "_build_eav_mapping_guide",
    "_format_field_names_with_examples",
    "_format_schema_columns",
    "_validate_mapping",
    # mapping_feedback
    "analyze_md_diff",
    "apply_mapping_feedback_to_redis",
]


# === Public API ===


async def map_fields(
    llm: BaseChatModel,
    template_structure: dict[str, Any],
    schema_info: dict[str, Any],
    priority_db_ids: list[str] | None = None,
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
    priority_db_ids: list[str] | None = None,
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

    # 대상 DB의 구조 선언(patterns)을 1회 조회해 아래 두 판정의 근거로 쓴다(편향 검토 §2-2·3).
    # 특정 DB의 테이블명 리터럴 대신 선언을 읽어 판정한다(D-088 공용 계층 DB-agnostic).
    structure_metas = await _load_structure_declarations(
        cache_manager,
        list(dict.fromkeys([
            *priority_db_ids,
            *(active_db_ids or []),
            *all_db_synonyms,
            *(all_db_descriptions or {}),
        ])),
    )

    # 사용률 지표 필드는 매핑 대상에서 제외한다(D-066 후속/RC2). 피벗 파생이라 단일 컬럼 매핑이
    # 불가능하며, 매핑 시도가 존재하지 않는 컬럼 환각·step3 LLM 호출 hang을 유발한다. 미매핑(None)으로
    # 두면 뒤에서 column_mapping에 None으로 채워져 SQL 생성의 쿼리 예시(지표 피벗)로 위임된다.
    # 단, 사용률이 직접 컬럼인 스키마(cpu_metrics.usage_pct 등)에서는 정상 매핑 대상이므로 스킵 금지 —
    # EAV 피벗 구조로 선언된 스키마일 때만 스킵한다.
    # 월 구조 필드(명사 없는 "…사용률|M+k")도 여기서 스킵된다 — 유사어 사전과 무관하게 None으로
    # 남아야 월 시리즈 인식기가 발동한다(plans/120 F-1).
    metric_usage_fields: set[str] = set()
    if _schema_uses_eav_metric_pivot(structure_metas):
        metric_usage_fields = {f for f in remaining if _is_metric_usage_field(f)}
    if metric_usage_fields:
        remaining -= metric_usage_fields
        logger.info(
            "사용률 지표 필드는 매핑 스킵 → 쿼리 예시 피벗에 위임: %s (월 구조 필드 %d개)",
            sorted(metric_usage_fields),
            sum(1 for f in metric_usage_fields if is_month_structure_field(f)),
        )

    # EAV 폴백 DB ID 결정: priority > active > synonyms 키 > _default
    _fallback_db_id = _resolve_fallback_db_id(
        priority_db_ids, active_db_ids, all_db_synonyms
    )

    # --- 1단계: 프롬프트 힌트 ---
    _apply_hint_mapping(
        remaining, field_mapping_hints, all_db_synonyms, result, priority_db_ids
    )

    # --- 2단계: EAV name synonyms 매칭 ---
    if remaining and eav_name_synonyms:
        _apply_eav_synonym_mapping(
            remaining, eav_name_synonyms, result, eav_db_id=_fallback_db_id,
            global_synonyms=global_synonyms,
        )

    # --- 2.5단계: Redis synonyms 규칙 매핑 ---
    if remaining and all_db_synonyms:
        # E5-1(퍼지)·E5-4(임베딩) 폴백 플래그를 config에서 읽어 계단 활성 여부를 결정한다
        # (둘 다 기본 OFF — 정확→퍼지→임베딩 계단, D-075/D-084).
        from src.config import load_config

        _syn_cfg = load_config().synonym
        _apply_synonym_mapping(
            remaining,
            all_db_synonyms,
            priority_db_ids,
            result,
            fuzzy=_syn_cfg.fuzzy_match,
            min_score=_syn_cfg.match_confidence_min,
            semantic=_syn_cfg.semantic_match,
            semantic_min=_syn_cfg.semantic_confidence_min,
            core_tables=_core_entity_tables(structure_metas),
            sub_table_scope=_sub_table_scope(
                structure_metas, priority_db_ids or list(all_db_synonyms)
            ),
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
            structure_metas=structure_metas,
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
                cache_manager, llm_inference_details, eav_name_synonyms,
                structure_metas=structure_metas,
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
    *,
    structure_metas: dict[str, dict[str, Any]] | None = None,
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
        structure_metas: {db_id: 구조 선언} — 자동 등록 쓰기 가드 판정용(plans/120 F-4)
    """
    if not remaining:
        return

    # DB 컬럼명 + synonym words를 {db_id:table.column: [유의어]} 형식으로 구성
    import json as _json

    if priority_db_ids:
        ordered_db_ids = priority_db_ids
    else:
        logger.warning(
            "Step 2.8 LLM 유사어 발견: priority_db_ids가 비어 있습니다. "
            "모든 DB(%s)의 유사어 정보가 LLM 프롬프트에 포함되어 "
            "HTTP 413 (Request entity too large) 에러가 발생할 수 있습니다.",
            list(all_db_synonyms.keys())
        )
        ordered_db_ids = list(all_db_synonyms.keys())
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

    messages: list[BaseMessage] = [
        SystemMessage(content=FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT)
    ]
    if type(llm) is KBGenAIChat:
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user_prompt))

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
            if field not in result.mapping_sources:
                result.db_column_mapping.setdefault(eav_db_id, {})[field] = matched_key
                result.mapping_sources[field] = "llm_synonym"
                remaining.discard(field)
                mapped_fields.append((field, matched_key, "eav"))
        elif ":" in matched_key:
            # db_id:table.column 형식
            parts = matched_key.split(":", 1)
            db_id = parts[0]
            column = parts[1]
            if priority_db_ids and db_id not in priority_db_ids:
                logger.warning(
                    "Step 2.8 LLM 유사어 발견: 우선순위 DB 이외의 DB 매핑 감지되어 제외. %s -> %s (우선순위 DB: %s)",
                    field,
                    matched_key,
                    priority_db_ids,
                )
                continue
            if field not in result.mapping_sources:
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
        cache_manager, mapped_fields, eav_name_synonyms, eav_db_id=eav_db_id,
        structure_metas=structure_metas,
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
    example_rows: list[list[str]] | None = None,
) -> list[dict[str, Any]]:
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
    if priority_db_ids:
        ordered_db_ids = priority_db_ids
    else:
        logger.warning(
            "Step 3 LLM 통합 추론: priority_db_ids가 비어 있습니다. "
            "모든 DB(%s)의 설명 정보가 LLM 프롬프트에 포함되어 "
            "HTTP 413 (Request entity too large) 에러가 발생할 수 있습니다.",
            list(all_db_descriptions.keys())
        )
        ordered_db_ids = list(all_db_descriptions.keys())

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

    messages: list[BaseMessage] = [
        SystemMessage(content=FIELD_MAPPER_ENHANCED_SYSTEM_PROMPT)
    ]
    if isinstance(llm, KBGenAIChat):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user_prompt))

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

                    if priority_db_ids and db_id not in priority_db_ids:
                        logger.warning(
                            "Step 3 LLM 통합 추론: 우선순위 DB 이외의 DB 매핑 감지되어 제외. %s -> %s.%s",
                            field,
                            db_id,
                            column,
                        )
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
