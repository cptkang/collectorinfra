"""필드 매핑 노드.

input_parser 직후에 실행되어 양식 필드와 DB 컬럼 간의 매핑을 수행한다.
3단계 매핑: 프롬프트 힌트 -> Redis synonyms -> LLM 추론.

template_structure가 없으면 (텍스트 출력 모드) 스킵한다.
매핑 결과가 이후 semantic_router, query_generator, output_generator를 주도한다.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.config import AppConfig, load_config
from src.document.field_mapper import extract_field_names, perform_3step_mapping
from src.llm import create_llm
from src.state import AgentState

logger = logging.getLogger(__name__)


async def field_mapper(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """양식 필드와 DB 컬럼 간 매핑을 수행한다.

    template_structure가 없으면 스킵하여 기존 텍스트 출력 흐름에 영향을 주지 않는다.

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스
        app_config: 앱 설정

    Returns:
        업데이트할 State 필드:
        - column_mapping: 통합 매핑 {field: "table.column"}
        - db_column_mapping: DB별 매핑 {db_id: {field: "table.column"}}
        - mapping_sources: 매핑 출처 {field: "hint"|"synonym"|"llm_inferred"}
        - mapped_db_ids: 매핑에서 식별된 DB 목록
        - pending_synonym_registrations: LLM 추론 매핑 대기 목록
        - current_node: "field_mapper"
    """
    # 유사어 등록 요청 처리 (멀티턴 대화에서 이전 상태 참조)
    parsed = state.get("parsed_requirements", {})
    synonym_reg = parsed.get("synonym_registration")
    if synonym_reg:
        reg_result = await _handle_synonym_registration(
            state, synonym_reg, app_config
        )
        if reg_result:
            return reg_result

    template = state.get("template_structure")
    if not template:
        # 텍스트 출력 모드: 매핑 불필요, 스킵
        logger.debug("template_structure 없음, field_mapper 스킵")
        return {
            "current_node": "field_mapper",
        }

    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    # 1. 양식에서 필드명 추출
    field_names = extract_field_names(template)
    if not field_names:
        logger.warning("양식에서 필드명을 추출할 수 없습니다. field_mapper 스킵")
        return {
            "current_node": "field_mapper",
        }

    # 2. 파싱 결과에서 매핑 힌트와 대상 DB 추출
    parsed = state.get("parsed_requirements", {})
    field_mapping_hints = parsed.get("field_mapping_hints", [])
    target_db_hints = parsed.get("target_db_hints", [])

    # 3. Redis 캐시에서 전체 DB의 synonyms/descriptions 로드
    active_db_ids = _get_active_db_ids(app_config)
    all_db_synonyms, all_db_descriptions, priority_db_ids, eav_name_synonyms, global_synonyms_raw, cache_mgr = await _load_db_cache_data(
        app_config, active_db_ids, target_db_hints
    )

    # 4. 3단계 매핑 수행 (cache_manager를 전달하여 LLM 매핑 즉시 Redis 등록)
    mapping_result, llm_inference_details = await perform_3step_mapping(
        llm=llm,
        field_names=field_names,
        field_mapping_hints=field_mapping_hints,
        all_db_synonyms=all_db_synonyms,
        all_db_descriptions=all_db_descriptions,
        priority_db_ids=priority_db_ids,
        eav_name_synonyms=eav_name_synonyms,
        cache_manager=cache_mgr,
        active_db_ids=active_db_ids,
        global_synonyms=global_synonyms_raw,
    )

    # 5. LLM 추론 매핑에 대한 pending_synonym_registrations 생성
    pending = _build_pending_registrations(mapping_result)

    if llm_inference_details:
        logger.info(
            "LLM 추론 매핑 %d건이 Redis에 즉시 등록되었습니다.",
            len(llm_inference_details),
        )

    # 6. 매핑 보고서 MD 생성
    mapping_report_md: str | None = None
    if mapping_result.column_mapping:
        from src.document.mapping_report import generate_mapping_report

        mapping_report_md = generate_mapping_report(
            field_names=field_names,
            mapping_result=mapping_result,
            template_name=state.get("output_file_name"),
            llm_inference_details=llm_inference_details,
        )

    logger.info(
        "field_mapper 완료: %d/%d 매핑, DB=%s, pending_synonyms=%d, report=%s",
        sum(1 for v in mapping_result.column_mapping.values() if v is not None),
        len(field_names),
        mapping_result.mapped_db_ids,
        len(pending),
        "생성됨" if mapping_report_md else "없음",
    )

    return {
        "column_mapping": mapping_result.column_mapping,
        "db_column_mapping": mapping_result.db_column_mapping,
        "mapping_sources": mapping_result.mapping_sources,
        "mapped_db_ids": mapping_result.mapped_db_ids,
        "pending_synonym_registrations": pending if pending else None,
        "llm_inference_details": llm_inference_details if llm_inference_details else None,
        "mapping_report_md": mapping_report_md,
        "current_node": "field_mapper",
    }


def _get_active_db_ids(app_config: AppConfig) -> list[str]:
    """활성 DB ID 목록을 반환한다.

    Args:
        app_config: 앱 설정

    Returns:
        활성 DB ID 목록
    """
    try:
        return app_config.multi_db.get_active_db_ids()
    except Exception:
        return []


async def _load_db_cache_data(
    app_config: AppConfig,
    active_db_ids: list[str],
    target_db_hints: list[str],
) -> tuple[dict[str, dict[str, list[str]]], dict[str, dict[str, str]], list[str], dict[str, list[str]], dict[str, list[str]], Any]:
    """Redis 캐시에서 전체 DB의 synonyms/descriptions를 로드한다.

    target_db_hints가 있으면 해당 DB를 우선 조회한다.
    Redis 미존재 시 빈 딕셔너리를 반환 (graceful fallback).

    Args:
        app_config: 앱 설정
        active_db_ids: 활성 DB ID 목록
        target_db_hints: 프롬프트에서 추출한 대상 DB 힌트

    Returns:
        (all_db_synonyms, all_db_descriptions, priority_db_ids, eav_name_synonyms, global_synonyms, cache_manager)
    """
    all_synonyms: dict[str, dict[str, list[str]]] = {}
    all_descriptions: dict[str, dict[str, str]] = {}

    # 우선순위 DB 결정
    priority_db_ids: list[str] = []
    remaining_db_ids: list[str] = []

    if target_db_hints:
        for db_id in active_db_ids:
            if db_id in target_db_hints:
                priority_db_ids.append(db_id)
            else:
                remaining_db_ids.append(db_id)
    else:
        remaining_db_ids = list(active_db_ids)

    ordered_db_ids = priority_db_ids + remaining_db_ids

    eav_name_synonyms: dict[str, list[str]] = {}
    cache_mgr: Any = None

    try:
        from src.schema_cache.cache_manager import get_cache_manager

        cache_mgr = get_cache_manager(app_config)

        for db_id in ordered_db_ids:
            try:
                synonyms = await cache_mgr.load_synonyms_with_global_fallback(db_id)
                if synonyms:
                    all_synonyms[db_id] = synonyms
            except Exception as e:
                logger.debug("DB '%s' synonyms 로드 실패: %s", db_id, e)

            try:
                descriptions = await cache_mgr.get_descriptions(db_id)
                if descriptions:
                    all_descriptions[db_id] = descriptions
            except Exception as e:
                logger.debug("DB '%s' descriptions 로드 실패: %s", db_id, e)

        # EAV name synonyms + global synonyms 로드
        try:
            if cache_mgr.redis_available:
                eav_name_synonyms = await cache_mgr._redis_cache.load_eav_name_synonyms()
        except Exception as e:
            logger.debug("eav_name_synonyms 로드 실패: %s", e)

        global_synonyms_raw: dict[str, list[str]] = {}
        try:
            if cache_mgr.redis_available:
                global_synonyms_raw = await cache_mgr.get_global_synonyms()
        except Exception as e:
            logger.debug("global_synonyms 로드 실패: %s", e)

    except Exception as e:
        logger.info(
            "Redis 캐시 로드 실패, LLM 폴백으로 동작합니다: %s", e
        )
        global_synonyms_raw = {}

    return all_synonyms, all_descriptions, priority_db_ids, eav_name_synonyms, global_synonyms_raw, cache_mgr


async def _handle_synonym_registration(
    state: AgentState,
    synonym_reg: dict,
    app_config: AppConfig | None,
) -> dict | None:
    """유사어 등록 요청을 처리한다.

    이전 대화에서 생성된 pending_synonym_registrations를 참조하여
    사용자가 선택한 항목을 Redis synonyms에 등록한다.

    Args:
        state: 에이전트 상태
        synonym_reg: {mode: "all"|"selective", indices: [int, ...]}
        app_config: 앱 설정

    Returns:
        State 업데이트 딕셔너리 또는 None (처리 불가 시)
    """
    pending = state.get("pending_synonym_registrations")
    if not pending:
        return {
            "final_response": "등록할 유사어 매핑이 없습니다. 먼저 양식 기반 조회를 수행해 주세요.",
            "current_node": "field_mapper",
        }

    mode = synonym_reg.get("mode", "all")
    indices = synonym_reg.get("indices", [])

    # 등록 대상 선택
    if mode == "all":
        targets = pending
    elif mode == "selective" and indices:
        idx_set = set(indices)
        targets = [p for p in pending if p.get("index") in idx_set]
    else:
        targets = pending

    if not targets:
        return {
            "final_response": "등록할 항목을 찾을 수 없습니다.",
            "current_node": "field_mapper",
        }

    # Redis에 등록
    registered_count = 0
    registered_items: list[str] = []

    try:
        if app_config is None:
            app_config = load_config()

        from src.schema_cache.cache_manager import get_cache_manager
        cache_mgr = get_cache_manager(app_config)

        for item in targets:
            db_id = item.get("db_id")
            column = item.get("column")
            field = item.get("field")
            if not db_id or not column or not field:
                continue

            try:
                # 기존 synonyms 로드
                existing = await cache_mgr.get_synonyms(db_id)
                col_synonyms = existing.get(column, [])

                # 중복 체크 후 추가
                if field not in col_synonyms:
                    col_synonyms.append(field)
                    existing[column] = col_synonyms
                    await cache_mgr.save_synonyms(db_id, existing)

                registered_count += 1
                registered_items.append(
                    f"{item.get('index', '?')}. {field} -> {column}"
                )
            except Exception as e:
                logger.warning("유사어 등록 실패 (%s.%s): %s", db_id, column, e)

    except Exception as e:
        logger.error("유사어 등록 중 오류: %s", e)
        return {
            "final_response": f"유사어 등록 중 오류가 발생했습니다: {e}",
            "current_node": "field_mapper",
        }

    items_text = "\n".join(f"  {item}" for item in registered_items)
    response = (
        f"{registered_count}건의 유사어가 등록되었습니다. "
        f"다음부터 해당 필드는 자동으로 매핑됩니다.\n{items_text}"
    )

    return {
        "final_response": response,
        "current_node": "field_mapper",
    }


def _build_pending_registrations(
    mapping_result: Any,
) -> list[dict]:
    """LLM 추론 매핑에 대한 pending_synonym_registrations를 생성한다.

    Args:
        mapping_result: MappingResult 객체

    Returns:
        등록 대기 목록
    """
    pending: list[dict] = []
    index = 1

    for field, source in mapping_result.mapping_sources.items():
        if source != "llm_inferred":
            continue

        # db_column_mapping에서 해당 필드의 DB와 컬럼 찾기
        for db_id, db_map in mapping_result.db_column_mapping.items():
            if field in db_map:
                pending.append({
                    "index": index,
                    "field": field,
                    "column": db_map[field],
                    "db_id": db_id,
                })
                index += 1
                break

    return pending
