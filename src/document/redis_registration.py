"""양식 LLM 매핑 결과의 유사어 자동 등록 — 2.8단계 발견분·3단계 추론분 (G-11 (b) · plans/120 F-4).

`src.document.field_mapper`(3단계 매핑 Facade)에서 옮겼다(plans/166 I2). 종전 경로는 Facade가
옛 이름(`_` 접두 포함)으로 재노출한다(D-337 ①·③). Facade의 `perform_3step_mapping`은 이 모듈의
함수를 Facade 이름으로 부른다 — 테스트가 Facade 경로에서 patch한다(D-337 ②).
"""

from __future__ import annotations

import logging
from typing import Any

from src.document.mapping_steps import load_structure_declarations
from src.document.synonym_write_guard import (
    BLOCK_REASON_LABELS,
    synonym_registration_block_reason,
)
from src.utils.query_gen_common import is_servername_to_hostname

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 I2)
logger = logging.getLogger("src.document.field_mapper")

_NO_STRUCTURE_REASON = "수동 프로필·승인본 없음"
_AUTHORITY_UNKNOWN_REASON = "구조 정보 판정 실패"
_DB_SCOPE_REGISTERED_LOG = (
    "양식 LLM 매핑 DB별 등록: db_id=%s, field=%s, column=%s (%s — 전역 대신 DB별)"
)
_DB_SCOPE_EAV_SKIPPED_LOG = (
    "양식 LLM 매핑 EAV 등록 건너뜀: db_id=%s, field=%s, column=%s "
    "(%s — EAV 매핑은 구조 정보가 있는 DB에서만 성립해 DB별로도 등록하지 않음)"
)
_NO_TARGET_DB_LOG = (
    "양식 LLM 매핑 등록 건너뜀: field=%s, column=%s "
    "(대상 db_id 없음 — 전역·DB별 모두 등록하지 않음)"
)


async def _structure_authority(
    cache_manager: Any,
    db_id: str,
    memo: dict[str, bool | None],
) -> bool | None:
    """양식 LLM 매핑 대상 DB에 구조 정보(수동 프로필·승인본)가 있는지 판정한다 (plans/104 G-11 (b)).

    전역 사전은 DB에 귀속되지 않아, 구조 정보가 없는 DB에서 나온 검증 안 된 LLM 매핑이
    다른 DB의 매핑 재료로 번진다. 호출부는 참일 때만 전역에 쓰고, 그 밖에는 DB별 캐시에 쓴다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스
        db_id: 매핑 대상 DB 식별자 (비어 있지 않아야 한다)
        memo: 한 번의 등록 호출 안에서 db_id별 판정을 재사용하는 저장소

    Returns:
        True(구조 정보 있음) · False(없음) · None(판정 자체가 실패 — 전역 쓰기 금지)
    """
    if db_id not in memo:
        try:
            memo[db_id] = bool(await cache_manager.has_structure_authority(db_id))
        except Exception as e:
            logger.warning(
                "구조 정보 판정 실패 — 양식 LLM 매핑은 전역에 쓰지 않고 DB별로 처리: db_id=%s (%s)",
                db_id, e,
            )
            memo[db_id] = None
    return memo[db_id]


async def _registration_blocked(
    cache_manager: Any,
    db_id: str,
    field: str,
    column: str,
    structure_metas: dict[str, dict[str, Any]] | None,
    memo: dict[str, dict[str, Any] | None],
) -> bool:
    """양식 LLM 매핑 자동 등록을 쓰기 가드가 막는지 판정하고 막으면 사유를 남긴다(plans/120 F-4).

    판정은 `synonym_registration_block_reason` 하나다(오염 진단 도구와 공유). 전역·EAV·DB별 등록
    분기보다 앞에서 부르므로 세 저장 공간이 같은 규칙을 받는다. 구조 선언은 호출부가 넘긴
    `structure_metas`를 쓰고, 넘기지 않은 단독 호출이면 그 DB 선언을 1회 조회해 `memo`에 둔다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스
        db_id: 매핑 대상 DB 식별자
        field: 양식 필드명(등록할 단어)
        column: 매핑된 컬럼(`table.column`) 또는 `EAV:` 접두 속성명
        structure_metas: {db_id: 구조 선언} 또는 None(단독 호출)
        memo: 한 번의 등록 호출 안에서 db_id별 구조 선언을 재사용하는 저장소

    Returns:
        등록하지 말아야 하면 True
    """
    if db_id not in memo:
        if structure_metas is not None:
            memo[db_id] = structure_metas.get(db_id)
        else:
            loaded = await load_structure_declarations(cache_manager, [db_id])
            memo[db_id] = loaded.get(db_id)
    reason = synonym_registration_block_reason(field, column, structure_meta=memo[db_id])
    if reason is None:
        return False
    logger.info(
        "양식 LLM 매핑 자동 등록 차단(%s — plans/120 F-4): db_id=%s, field=%s, column=%s",
        BLOCK_REASON_LABELS[reason], db_id, field, column,
    )
    return True


async def _register_llm_mapping_to_db_scope(
    cache_manager: Any,
    db_id: str,
    field: str,
    column: str,
    reason: str,
) -> bool:
    """구조 정보를 확인하지 못한 DB의 양식 LLM 매핑을 그 DB의 유사어 캐시에 등록한다 (G-11 (b)).

    전역 사전(`synonyms:global`·EAV 속성명 유사어)에는 쓰지 않는다. 오염이 그 DB 밖으로
    번지지 않게 하면서 그 DB의 양식 매핑 학습은 남긴다(출처 태그 `llm`).
    EAV 매핑(`EAV:` 접두)은 구조 정보(EAV 패턴)가 있는 DB에서만 성립하므로 등록하지 않는다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스
        db_id: 매핑 대상 DB 식별자
        field: 양식 필드명
        column: 매핑된 컬럼(`table.column`) 또는 `EAV:` 접두 속성명
        reason: 로그에 남길 DB별 처리 사유

    Returns:
        등록 성공 여부
    """
    if column.startswith("EAV:"):
        logger.warning(_DB_SCOPE_EAV_SKIPPED_LOG, db_id, field, column, reason)
        return False
    if not await cache_manager.add_synonyms(db_id, column, [field], source="llm"):
        logger.warning(
            "양식 LLM 매핑 DB별 등록 실패: db_id=%s, field=%s, column=%s", db_id, field, column
        )
        return False
    logger.info(_DB_SCOPE_REGISTERED_LOG, db_id, field, column, reason)
    return True


async def register_llm_synonym_discoveries_to_redis(
    cache_manager: Any | None,
    mapped_fields: list[tuple[str, str, str]],
    eav_name_synonyms: dict[str, list[str]] | None = None,
    *,
    eav_db_id: str = "",
    structure_metas: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Step 2.8에서 발견한 유사어를 Redis에 자동 등록한다.

    컬럼 매핑의 경우 add_global_synonym()으로 등록하고,
    EAV 매핑의 경우 eav_name_synonyms에 필드명을 추가하여 저장한다.
    둘 다 전역 저장소라 대상 DB에 구조 정보(수동 프로필·승인본)가 있을 때만 쓰고,
    없으면 컬럼 매핑만 그 DB의 유사어 캐시에 등록한다(G-11 (b)).
    쓰기 가드(plans/120 F-4)가 막는 매핑은 어느 저장 공간에도 쓰지 않는다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스 (None 가능)
        mapped_fields: (field_name, matched_key, type) 튜플 리스트
        eav_name_synonyms: 기존 EAV 속성명 유사어 매핑 (선택)
        eav_db_id: EAV 매핑의 대상 DB 식별자 (컬럼 매핑은 matched_key의 db_id를 쓴다)
        structure_metas: {db_id: 구조 선언} — 쓰기 가드 판정용(없으면 DB별 1회 조회)
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
    authority: dict[str, bool | None] = {}
    declarations: dict[str, dict[str, Any] | None] = {}

    for field, matched_key, match_type in mapped_fields:
        # 재오염 차단: 서버명/서버이름류 → hostname(컬럼/EAV) 자동 등록 거부(D-068 후속).
        if is_servername_to_hostname(field, matched_key):
            logger.info(
                "자동 유사어 등록 차단(서버명→hostname 오연관): %s -> %s", field, matched_key
            )
            continue
        if match_type == "eav":
            target_db_id, target_column = eav_db_id, matched_key
        else:
            target_db_id, _, target_column = matched_key.partition(":")
        if not target_db_id:
            logger.warning(_NO_TARGET_DB_LOG, field, target_column)
            continue
        if await _registration_blocked(
            cache_manager, target_db_id, field, target_column, structure_metas, declarations
        ):
            continue
        has_structure = await _structure_authority(cache_manager, target_db_id, authority)
        try:
            if has_structure is not True:
                reason = (
                    _NO_STRUCTURE_REASON if has_structure is False else _AUTHORITY_UNKNOWN_REASON
                )
                if await _register_llm_mapping_to_db_scope(
                    cache_manager, target_db_id, field, target_column, reason
                ):
                    registered_count += 1
            elif match_type == "eav":
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



async def register_llm_mappings_to_redis(
    cache_manager: Any | None,
    llm_inference_details: list[dict[str, Any]],
    eav_name_synonyms: dict[str, list[str]] | None = None,
    *,
    structure_metas: dict[str, dict[str, Any]] | None = None,
) -> None:
    """LLM 추론 결과를 즉시 Redis에 등록한다.

    EAV 매핑(EAV: 접두사)은 eav_name_synonyms에 필드명을 추가하여 저장하고,
    일반 매핑은 cache_manager.add_synonyms()로 등록한다.
    cache_manager가 None이거나 redis_available이 False이면 스킵한다.
    전역(EAV 속성명 사전·`synonyms:global`) 쓰기는 대상 db_id에 구조 정보(수동 프로필·승인본)가
    있을 때만 하고, 없으면 일반 매핑만 그 DB의 유사어 캐시에 출처 `llm`으로 등록한다
    (G-11 (b) — EAV는 등록하지 않음).
    쓰기 가드(plans/120 F-4)가 막는 매핑은 어느 저장 공간에도 쓰지 않는다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스 (None 가능)
        llm_inference_details: LLM 추론 상세 정보 리스트
        eav_name_synonyms: 기존 EAV 속성명 유사어 매핑 (선택)
        structure_metas: {db_id: 구조 선언} — 쓰기 가드 판정용(없으면 DB별 1회 조회)
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
    authority: dict[str, bool | None] = {}
    declarations: dict[str, dict[str, Any] | None] = {}

    for detail in llm_inference_details:
        field = detail.get("field", "")
        db_id = detail.get("db_id", "")
        column = detail.get("column", "")

        if not field or not column:
            continue

        # 재오염 차단: 서버명/서버이름류 → hostname(컬럼/EAV) 자동 등록 거부(D-068 후속).
        if is_servername_to_hostname(field, column):
            logger.info(
                "자동 유사어 등록 차단(서버명→hostname 오연관): %s -> %s", field, column
            )
            continue

        if not db_id:
            logger.warning(_NO_TARGET_DB_LOG, field, column)
            continue
        if await _registration_blocked(
            cache_manager, db_id, field, column, structure_metas, declarations
        ):
            continue
        has_structure = await _structure_authority(cache_manager, db_id, authority)

        try:
            if has_structure is not True:
                reason = (
                    _NO_STRUCTURE_REASON if has_structure is False else _AUTHORITY_UNKNOWN_REASON
                )
                if await _register_llm_mapping_to_db_scope(
                    cache_manager, db_id, field, column, reason
                ):
                    registered_count += 1
            elif column.startswith("EAV:"):
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
