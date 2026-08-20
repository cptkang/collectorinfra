"""폼필 확인 이력 (Plan 73 Phase 3, D-148) — 양식 시그니처 스코프의 TTL 단기 캐시.

사용자가 역질문 패널에서 확정한 답을 양식 시그니처(헤더 필드 집합 해시) 단위로
Redis에 저장한다. 설계 원칙(plans/73 §2.4 개정):

- **TTL 필수(sliding)**: 무기한 저장 금지 — 만료 비용(패널 재답변 1회) ≪ 부패 지속
  비용(감사자료 오기재). 적용(사용)이 곧 재확인이므로 사용 시 TTL을 연장한다.
  `QueryConfig.form_memory_ttl_days=0`이면 기능 전체 OFF(읽기·쓰기 모두 무동작).
- **쓰기 게이트 = 사용자 명시 확인**: LLM 산출물 자동 저장 금지(C3). 저장 진입점은
  패널의 "기억" 옵트인뿐.
- **전역 격리**: 키 공간 `formfill:memory:{signature}` — 유사어·스키마 캐시와 분리,
  일반 질의에 영향 없음(C6).
- **우아한 강등**: Redis 불가·TTL 0·시그니처 불가 시 빈 결과 반환 — 현행 동작과
  동일(C4). 이력 유실 복구 비용은 재답변 1회라 파일 폴백을 두지 않는다.

저장 형식(JSON):
    {"display_name": str, "original_query": str, "created_at": iso,
     "use_count": int, "fields": {필드명: {"action", "value", "confirmed_at"}}}
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from src.config import AppConfig
from src.utils.schema_utils import form_signature

logger = logging.getLogger(__name__)


def _ttl_seconds(app_config: AppConfig) -> int:
    days = getattr(app_config.query, "form_memory_ttl_days", 0) or 0
    return int(days) * 86400


def _display_name(template_structure: dict | None) -> str:
    """사람이 알아볼 표시 이름 — 시트 제목(title_text) 우선, 없으면 헤더 요약."""
    sheets = (template_structure or {}).get("sheets", [])
    for sheet in sheets:
        title = (sheet.get("title_text") or "").strip()
        if title:
            return title[:40]
    headers = [str(h) for s in sheets for h in (s.get("headers") or []) if h]
    if headers:
        summary = ", ".join(headers[:3])
        rest = len(headers) - 3
        return f"{summary}{f' 외 {rest}개' if rest > 0 else ''} 양식"
    return "이름 없는 양식"


async def _get_redis(app_config: AppConfig):
    """연결 보장된 RedisSchemaCache 또는 None(강등)."""
    try:
        from src.schema_cache.cache_manager import get_cache_manager

        mgr = get_cache_manager(app_config)
        if not await mgr.ensure_redis_connected():
            return None
        return mgr._redis_cache  # noqa: SLF001 — 폼필 이력은 Redis 전용(파일 폴백 없음)
    except Exception as e:  # noqa: BLE001 — 이력 불가는 강등이지 실패가 아님
        logger.debug("폼필 확인 이력 Redis 접근 불가(강등): %s", e)
        return None


async def load_form_memory_answers(
    template_structure: dict | None,
    app_config: AppConfig,
    *,
    touch: bool = True,
    signature: Optional[str] = None,
) -> tuple[Optional[str], dict[str, dict], Optional[dict]]:
    """이력을 답변 형식({field: {action, value, origin:"memory"}})으로 로드한다.

    touch=True면 sliding TTL 연장 + use_count 증가(적용=재확인). 조회 전용(뷰)은
    touch=False로 호출한다. signature를 직접 주면 template 없이 조회한다
    (FIX-23 — 직전 양식 시그니처 기반 파일 없는 조회·삭제).

    Returns:
        (signature, answers, meta) — 이력 없음/기능 OFF면 (signature|None, {}, None).
    """
    ttl = _ttl_seconds(app_config)
    signature = signature or form_signature(template_structure)
    if not signature or ttl <= 0:
        return signature, {}, None
    redis = await _get_redis(app_config)
    if redis is None:
        return signature, {}, None
    data = await redis.load_form_memory(signature)
    if not data or not isinstance(data.get("fields"), dict):
        return signature, {}, None
    answers: dict[str, dict] = {
        field: {"action": e.get("action"), "value": e.get("value"), "origin": "memory"}
        for field, e in data["fields"].items()
        if isinstance(e, dict) and e.get("action")
    }
    if answers and touch:
        data["use_count"] = int(data.get("use_count") or 0) + 1
        await redis.save_form_memory(signature, data, ttl)
        logger.info(
            "폼필 확인 이력 적용(D-148): '%s' %d개 항목 (사용 %d회, TTL %d일 연장)",
            data.get("display_name", signature), len(answers),
            data["use_count"], ttl // 86400,
        )
    return signature, answers, data


async def save_form_memory_entries(
    template_structure: dict | None,
    entries: dict[str, dict],
    original_query: str,
    app_config: AppConfig,
) -> Optional[str]:
    """사용자 확정 답변(entries={field: {action, value}})을 이력에 병합 저장한다.

    기존 이력이 있으면 필드 단위 병합(최신 확인이 승리). TTL 리셋.

    Returns:
        표시 이름(저장 성공) 또는 None(기능 OFF·Redis 불가·저장 실패).
    """
    ttl = _ttl_seconds(app_config)
    signature = form_signature(template_structure)
    if not signature or ttl <= 0 or not entries:
        return None
    redis = await _get_redis(app_config)
    if redis is None:
        return None
    existing = await redis.load_form_memory(signature) or {}
    now = datetime.now().isoformat(timespec="seconds")
    fields: dict[str, Any] = dict(existing.get("fields") or {})
    for field, ans in entries.items():
        fields[field] = {
            "action": ans.get("action"),
            "value": ans.get("value"),
            "confirmed_at": now,
        }
    display = existing.get("display_name") or _display_name(template_structure)
    data = {
        "display_name": display,
        "original_query": existing.get("original_query") or original_query,
        "created_at": existing.get("created_at") or now,
        "use_count": int(existing.get("use_count") or 0),
        "fields": fields,
    }
    if await redis.save_form_memory(signature, data, ttl):
        logger.info(
            "폼필 확인 이력 저장(D-148): '%s' %d개 항목 (TTL %d일, 총 %d개)",
            display, len(entries), ttl // 86400, len(fields),
        )
        return display
    return None


async def delete_form_memory_entries(
    template_structure: dict | None,
    app_config: AppConfig,
    field_names: list[str] | None = None,
    *,
    signature: Optional[str] = None,
) -> tuple[int, Optional[str]]:
    """이력을 삭제한다 — field_names 지정 시 해당 필드만, None이면 전체.

    signature를 직접 주면 template 없이 삭제한다(FIX-23 — 직전 양식 기반).

    Returns:
        (삭제 항목 수, 표시 이름) — 이력 없음이면 (0, None).
    """
    ttl = _ttl_seconds(app_config)
    signature = signature or form_signature(template_structure)
    if not signature:
        return 0, None
    redis = await _get_redis(app_config)
    if redis is None:
        return 0, None
    data = await redis.load_form_memory(signature)
    if not data:
        return 0, None
    display = data.get("display_name")
    fields: dict = data.get("fields") or {}
    if field_names is None:
        await redis.delete_form_memory(signature)
        logger.info("폼필 확인 이력 전체 삭제(D-148): '%s' %d개 항목", display, len(fields))
        return len(fields), display
    removed = [f for f in field_names if fields.pop(f, None) is not None]
    if not removed:
        return 0, display
    if fields:
        data["fields"] = fields
        # 저장-백 실패를 침묵하면 "삭제했습니다" 안내와 실제 상태가 어긋난다(FIX-23)
        if not await redis.save_form_memory(signature, data, max(ttl, 60)):
            logger.warning("폼필 확인 이력 필드 삭제 저장 실패(%s) — 삭제 미반영", signature)
            return 0, display
    else:
        if not await redis.delete_form_memory(signature):
            return 0, display
    logger.info("폼필 확인 이력 필드 삭제(D-148): '%s' — %s", display, removed)
    return len(removed), display
