"""매핑 보고서(MD) 피드백 — 원본·수정본 비교와 유사어 사전 반영.

`src.document.field_mapper`에서 옮겼다(plans/166 I2). 종전 경로는 Facade가 재노출한다(D-337 ①).
"""

from __future__ import annotations

import logging
from typing import Any

from src.utils.query_gen_common import is_servername_to_hostname

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 I2)
logger = logging.getLogger("src.document.field_mapper")


# === MD Feedback: Diff & Redis Apply ===


def analyze_md_diff(
    original_mappings: list[dict[str, Any]],
    modified_mappings: list[dict[str, Any]],
) -> dict[str, Any]:
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
    added: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    unchanged = 0

    # 원본/수정본을 field명 기준으로 인덱싱
    orig_by_field: dict[str, dict[str, Any]] = {
        m["field"]: m for m in original_mappings if m.get("field")
    }
    mod_by_field: dict[str, dict[str, Any]] = {
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
    diff_result: dict[str, Any],
) -> dict[str, Any]:
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

        # 재오염 차단: 서버명/서버이름류 → hostname(컬럼/EAV) 등록 거부(D-068 후속).
        if is_servername_to_hostname(field, column):
            logger.info(
                "유사어 피드백 등록 차단(서버명→hostname 오연관): %s -> %s", field, column
            )
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
