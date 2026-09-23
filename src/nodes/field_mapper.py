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
from src.routing.location_hints import resolve_priority_db_ids as resolve_priority_db_ids
from src.state import AgentState

logger = logging.getLogger(__name__)

# 이번 턴에 양식(template_structure)이 없으면 폼필 매핑 산출물이 존재해선 안 된다(D-064).
# 체크포인터가 직전 폼업로드 턴의 매핑을 복원해 잔존시키면 intent_planner가 옛 DB로
# 고정된다([intent_planner] mapped_db_ids 단축). 스킵 경로에서 명시적으로 비워, 진입 경로와
# 무관하게 "template 없음 → 매핑 없음" 불변식을 보장한다.
# 단, pending_synonym_registrations 는 멀티턴 유사어 등록 흐름의 신호이므로 여기서 비우지 않는다.
_CLEARED_MAPPING_FIELDS: dict[str, None] = {
    "column_mapping": None,
    "db_column_mapping": None,
    "mapping_sources": None,
    "mapped_db_ids": None,
    "llm_inference_details": None,
    "mapping_report_md": None,
}


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
    # 결정적 가드(D-252 부기): 등록 대기 목록도 없고 질의에 「등록」도 없으면 LLM 의 등록 신호를
    # 버린다. 양식 첨부 질의(「첨부한 양식을 채워줘」)를 입력 분석 LLM 이 {mode: all} 로 오분류해
    # 「등록할 유사어 매핑이 없습니다」로 양식 채우기가 시작 전에 종결됐다
    # (2026-09-23 로컬 MLX 실측).
    if (
        synonym_reg
        and not state.get("pending_synonym_registrations")
        and "등록" not in (state.get("user_query") or "")
    ):
        logger.warning(
            "synonym_registration 신호 무시 — 대기 목록 없음·질의에 「등록」 없음"
            "(LLM 오분류 가드): %r",
            (state.get("user_query") or "")[:80],
        )
        synonym_reg = None
    if synonym_reg:
        # 신규 동의어 집합 선언("vcore, cpu, core은 동의어이다. 등록하라")은 이 분기의
        # 소관이 아니다 — pending 없이 가로채면 "등록할 매핑 없음"으로 오종결되어
        # cache_management의 결정적 셋 파서(D-142)에 도달하지 못한다(2026-09-01 라이브
        # 실측 A-10). pending이 있거나 셋 선언이 아닐 때만 기존 등록 흐름을 처리한다.
        from src.utils.synonym_set_parser import parse_synonym_set

        if state.get("pending_synonym_registrations") or not parse_synonym_set(
            state.get("user_query", "")
        ):
            reg_result = await _handle_synonym_registration(
                state, synonym_reg, app_config
            )
            if reg_result:
                return reg_result

    template = state.get("template_structure")
    if not template:
        # 텍스트 출력 모드: 매핑 불필요, 스킵. 잔존 매핑 산출물 정리(D-064).
        logger.debug("template_structure 없음, field_mapper 스킵")
        return {
            "current_node": "field_mapper",
            **_CLEARED_MAPPING_FIELDS,
        }

    # 폼필 확인 이력 조회·삭제 턴(D-151, FIX-24): intent_planner ②.7이 결정적으로
    # 단락하므로 매핑 산출물이 전부 불필요하다. 여기서 전체 매핑을 수행하면 —
    # 이력 명령 질의에는 위치어가 없어 priority_db_ids가 비고 → 전 DB 유사어가 LLM
    # 프롬프트에 실려 413(FabriX 95K) 재시도로 수십 초 낭비(라이브 실측 2026-08-03).
    from src.utils.query_gen_common import is_form_memory_command

    if is_form_memory_command(state.get("user_query", "")):
        logger.info("field_mapper: 폼필 확인 이력 명령 감지 — 매핑 스킵(FIX-24, LLM 미호출)")
        return {
            "current_node": "field_mapper",
            **_CLEARED_MAPPING_FIELDS,
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
            **_CLEARED_MAPPING_FIELDS,
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

    # 4.5. 다중 위치(공동존=김포+여의도) 대응 — 전 priority DB 조회.
    _replicate_mapping_for_multi_location(mapping_result, priority_db_ids)

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


def _replicate_mapping_for_multi_location(
    mapping_result: Any,
    priority_db_ids: list[str],
) -> None:
    """다중 위치(공동존=김포+여의도)일 때 매핑을 전 priority DB에 복제한다(in-place).

    priority_db_ids가 여러 폴스타 DB를 포괄하면(공동존→[gp,yd]), 스키마가 동일해 각 필드가 첫
    priority DB(gp)에만 매핑돼 mapped_db_ids=[gp]가 된다. 그러나 데이터(김포/여의도 서버)는 다르므로
    둘 다 조회해야 한다. 매핑(union)을 모든 priority DB에 복제하고 mapped_db_ids를 priority 전체로
    확장한다. 스키마가 다른 DB로 잘못 복제돼도 multi_db_executor의 테이블 존재 필터가 걸러낸다.

    단일 위치(priority 1개)거나 매핑이 없으면 아무것도 하지 않는다.
    """
    if len(priority_db_ids) <= 1 or not mapping_result.db_column_mapping:
        return
    union_mapping: dict[str, str] = {}
    for db_map in mapping_result.db_column_mapping.values():
        union_mapping.update(db_map)
    for db_id in priority_db_ids:
        mapping_result.db_column_mapping[db_id] = dict(union_mapping)
    mapping_result.mapped_db_ids = list(priority_db_ids)
    logger.info(
        "다중 위치 감지(priority=%s) → 매핑을 전 priority DB에 복제하여 모두 조회",
        priority_db_ids,
    )


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


# 결정적 위치→DB 해소 단일 출처 — 폼필(field_mapper)·텍스트 경로
# (subagents._apply_turn_hint_pinning)·3단 라우터(semantic_router)가 같은 로직을 공유한다
# (경로별 사본 금지). 본문은 3단 라우터가
# infrastructure 계층이라 `src/routing/location_hints.py`로 옮겼다(plans/113 F-1 — 계층 규칙).
# 두 이름(`_resolve_priority_db_ids`·`resolve_priority_db_ids`)은 기존 소비처를 위해 재노출한다.
_resolve_priority_db_ids = resolve_priority_db_ids


def _load_local_yaml_fallback(
    active_db_ids: list[str],
) -> tuple[dict[str, dict[str, list[str]]], dict[str, list[str]], dict[str, list[str]]]:
    """Redis 캐시 미존재 시 로컬 YAML 설정 파일들에서 유사어 사전을 직접 로드하여 폴백한다.

    Returns:
        (all_synonyms, eav_name_synonyms, global_synonyms)
    """
    import yaml
    from pathlib import Path

    all_synonyms: dict[str, dict[str, list[str]]] = {}
    eav_name_synonyms: dict[str, list[str]] = {}
    global_synonyms: dict[str, list[str]] = {}

    # 1. global_synonyms.yaml 로드
    global_yaml_path = Path("config/global_synonyms.yaml")
    if global_yaml_path.exists():
        try:
            with open(global_yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                # columns -> global_synonyms
                columns = data.get("columns", {})
                for col, info in columns.items():
                    global_synonyms[col] = info.get("words", [])
                
                # eav_name_values -> eav_name_synonyms
                eav_values = data.get("eav_name_values", {})
                for eav_name, info in eav_values.items():
                    eav_name_synonyms[eav_name] = info.get("words", [])
        except Exception as e:
            logger.debug("로컬 global_synonyms.yaml 로드 실패: %s", e)

    # 2. 각 DB 프로필 YAML 파일 로드
    for db_id in active_db_ids:
        profile_path = Path(f"config/db_profiles/{db_id}.yaml")
        if profile_path.exists():
            try:
                with open(profile_path, encoding="utf-8") as f:
                    profile_data = yaml.safe_load(f)
                if isinstance(profile_data, dict):
                    db_syns: dict[str, list[str]] = {}
                    
                    # patterns에서 eav 타입과 known_attributes 추출
                    patterns = profile_data.get("patterns", [])
                    for pattern in patterns:
                        if pattern.get("type") == "eav":
                            known_attrs = pattern.get("known_attributes", [])
                            for attr in known_attrs:
                                if isinstance(attr, dict):
                                    name = attr.get("name")
                                    syns = attr.get("synonyms", [])
                                    if name:
                                        # EAV synonym에 병합
                                        eav_name_synonyms.setdefault(name, [])
                                        for s in syns:
                                            if s not in eav_name_synonyms[name]:
                                                eav_name_synonyms[name].append(s)
                                        # EAV 속성명 자체도 유사어에 포함
                                        if name not in eav_name_synonyms[name]:
                                            eav_name_synonyms[name].append(name)
                    
                    # DB 테이블 컬럼에 대한 synonyms를 global_synonyms 기반으로 가상 구축
                    allowed_tables = profile_data.get("allowed_tables", ["cmm_resource"])
                    for table in allowed_tables:
                        for col_name, words in global_synonyms.items():
                            col_lower = col_name.lower()
                            db_syns[f"{table}.{col_lower}"] = words
                    
                    all_synonyms[db_id] = db_syns
            except Exception as e:
                logger.debug("로컬 DB 프로필 '%s' 로드 실패: %s", db_id, e)

    return all_synonyms, eav_name_synonyms, global_synonyms


async def _load_db_cache_data(
    app_config: AppConfig,
    active_db_ids: list[str],
    target_db_hints: list[str],
) -> tuple[dict[str, dict[str, list[str]]], dict[str, dict[str, str]], list[str], dict[str, list[str]], dict[str, list[str]], Any]:
    """Redis 캐시에서 전체 DB의 synonyms/descriptions를 로드한다.

    target_db_hints가 있으면 해당 DB를 우선 조회한다.
    Redis 미존재 시 로컬 YAML 파일에서 로드하여 폴백한다.

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
    priority_db_ids = _resolve_priority_db_ids(target_db_hints, active_db_ids)
    remaining_db_ids = [db_id for db_id in active_db_ids if db_id not in priority_db_ids]

    ordered_db_ids = priority_db_ids + remaining_db_ids

    eav_name_synonyms: dict[str, list[str]] = {}
    global_synonyms_raw: dict[str, list[str]] = {}
    cache_mgr: Any = None

    try:
        from src.schema_cache.cache_manager import get_cache_manager

        cache_mgr = get_cache_manager(app_config)
        redis_available = getattr(cache_mgr, "redis_available", False)

        if redis_available:
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
                eav_name_synonyms = await cache_mgr._redis_cache.load_eav_name_synonyms()
            except Exception as e:
                logger.debug("eav_name_synonyms 로드 실패: %s", e)

            try:
                global_synonyms_raw = await cache_mgr.get_global_synonyms()
            except Exception as e:
                logger.debug("global_synonyms 로드 실패: %s", e)
        else:
            logger.info("Redis 연결 실패 혹은 미사용 상태. 로컬 YAML 설정을 직접 로드하여 폴백합니다.")
            local_syns, local_eav, local_global = _load_local_yaml_fallback(active_db_ids)
            all_synonyms.update(local_syns)
            eav_name_synonyms.update(local_eav)
            global_synonyms_raw = local_global

    except Exception as e:
        logger.info(
            "Redis 캐시 로드 중 예외 발생, 로컬 YAML 폴백 및 LLM으로 동작합니다: %s", e
        )
        local_syns, local_eav, local_global = _load_local_yaml_fallback(active_db_ids)
        all_synonyms.update(local_syns)
        eav_name_synonyms.update(local_eav)
        global_synonyms_raw = local_global

    # 일부 캐시 데이터가 비어있다면 로컬 YAML 설정으로 보강
    if not eav_name_synonyms or not global_synonyms_raw or not all_synonyms:
        logger.info("일부 캐시 데이터가 비어있어 로컬 YAML 설정으로 보강(폴백)합니다.")
        local_syns, local_eav, local_global = _load_local_yaml_fallback(active_db_ids)
        if not all_synonyms:
            all_synonyms.update(local_syns)
        if not eav_name_synonyms:
            eav_name_synonyms.update(local_eav)
        if not global_synonyms_raw:
            global_synonyms_raw = local_global

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
