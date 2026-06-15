"""알람 컨텍스트 보강 노드 (Plan 47).

폴스타 DB에서 동일 (서버, 알람명) 이력을 조회하고 통계를 계산하여
`AlarmState.history_stats`를 채운다.

graceful degradation 원칙:
    - 이력 조회 실패/타임아웃/db_id 미등록/Redis 캐시 장애 어느 경우에도
      알람 분석·발송 파이프라인을 절대 차단하지 않는다 — history_stats=None으로
      반환하고 alarm_analyzer가 이력 없이 기존 분석을 그대로 진행한다.
    - Redis 캐시는 폴스타 DB 부하 보호용 순수 최적화 — 캐시 실패는 무시하고
      DB 조회로 진행한다.

데이터 흐름 (Plan 47 §4.2):
    1. Redis GET alarm:histcache:{db_id}:{server_name}:{alarm_name}
       → HIT: 캐시된 이력 행으로 통계 계산 (DB 미접근)
    2. MISS → PolestarAlarmHistoryRepository.fetch() → Redis SETEX (TTL)
    3. compute_history_stats(이력, 현재 이벤트) → AlarmState.history_stats
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from src.alarm.domain.alarm import AlarmEvent, AlarmHistoryStats
from src.alarm.domain.alarm_pattern import (
    compute_history_stats,
    history_entries_from_dicts,
    history_entries_to_dicts,
)

logger = logging.getLogger(__name__)


def _cache_key(event: AlarmEvent) -> str:
    """단기 조회 캐시 키 (Plan 47 §5.3)."""
    return f"alarm:histcache:{event.db_id}:{event.server_name}:{event.alarm_name}"


async def enrich_history(
    event: AlarmEvent,
    alarm_cfg,  # noqa: ANN001 — AlarmConfig
    repo,  # noqa: ANN001 — PolestarAlarmHistoryRepository
    redis_client=None,  # noqa: ANN001 — redis.asyncio.Redis | None
) -> AlarmHistoryStats:
    """캐시 확인 → 폴스타 DB 이력 조회 → 캐시 적재 → 통계 계산.

    캐시는 조회 행 원본을 저장한다 (통계가 아니라) — 통계는 현재 이벤트에
    상대적이므로 매번 재계산한다. Redis 캐시 실패는 무시한다 (순수 최적화).

    Args:
        event: 현재 알람 이벤트
        alarm_cfg: AlarmConfig (history_* 설정)
        repo: PolestarAlarmHistoryRepository
        redis_client: 캐시용 Redis 클라이언트 (None이면 캐시 미사용)

    Returns:
        AlarmHistoryStats (source="cache" | "polestar_db")
    """
    cache_enabled = (
        redis_client is not None and alarm_cfg.history_cache_ttl_seconds > 0
    )
    key = _cache_key(event)
    entries = None
    source = "polestar_db"

    if cache_enabled:
        try:
            raw = await redis_client.get(key)
            if raw:
                entries = history_entries_from_dicts(json.loads(raw))
                source = "cache"
        except Exception as e:
            logger.debug("알람 이력 캐시 조회 실패 — 무시하고 DB 조회 진행: %s", e)
            entries = None

    if entries is None:
        entries = await repo.fetch(event)
        if cache_enabled:
            try:
                await redis_client.setex(
                    key,
                    alarm_cfg.history_cache_ttl_seconds,
                    json.dumps(history_entries_to_dicts(entries), ensure_ascii=False),
                )
            except Exception as e:
                logger.debug("알람 이력 캐시 적재 실패 — 무시: %s", e)

    truncated = len(entries) >= alarm_cfg.history_max_rows
    return compute_history_stats(
        event,
        entries,
        burst_threshold_24h=alarm_cfg.burst_threshold_24h,
        lookback_days=alarm_cfg.history_lookback_days,
        truncated=truncated,
        source=source,
    )


async def alarm_context_enricher_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """폴스타 DB 이력 조회 → 통계 계산 → history_stats 반환.

    - cfg.alarm.history_enabled=False 또는 조회/계산 실패 시 {"history_stats": None}
      반환 (분석 파이프라인을 절대 차단하지 않는다)
    - 캐시 확인 → DB 조회 → 캐시 적재 → 통계 계산 전체에
      asyncio.wait_for(enrich_timeout_seconds) 적용
    - 해소 알람(is_clear=True)은 이력 조회를 건너뛴다 (패턴 분석은 발생 알람 대상)
    - event.db_id가 레지스트리 미등록이면 이력 조회를 건너뛴다
      (빈 이력으로 통계를 계산하면 "첫 발생" 오판 — history_stats=None 유지)

    `PolestarAlarmHistoryRepository`(history_repo)와 Redis 캐시 클라이언트
    (history_redis)는 config["configurable"]로 주입한다. 미주입 시(테스트 API 경로 등)
    이력 조회를 건너뛴다.
    """
    event: AlarmEvent = state["alarm_event"]
    configurable = (config or {}).get("configurable", {})
    cfg = configurable.get("app_config")
    repo = configurable.get("history_repo")

    if cfg is None or not cfg.alarm.history_enabled or repo is None:
        return {"history_stats": None}

    if event.is_clear:
        logger.debug(
            "해소 알람 — 이력 조회 건너뜀: alarm_id=%s", event.alarm_id
        )
        return {"history_stats": None}

    if not repo.is_db_registered(event.db_id):
        logger.warning(
            "알람 이력 조회 건너뜀 — 미등록 db_id: %s (alarm_id=%s)",
            event.db_id,
            event.alarm_id,
        )
        return {"history_stats": None}

    redis_client = configurable.get("history_redis")
    try:
        stats: Optional[AlarmHistoryStats] = await asyncio.wait_for(
            enrich_history(event, cfg.alarm, repo, redis_client),
            timeout=cfg.alarm.enrich_timeout_seconds,
        )
        logger.info(
            "알람 이력 통계 계산 완료: alarm_id=%s 분류=%s 발생=%d건 source=%s",
            event.alarm_id,
            stats.pre_classification,
            stats.total_count,
            stats.source,
        )
        return {"history_stats": stats}
    except asyncio.TimeoutError:
        logger.warning(
            "알람 이력 조회 타임아웃 (%ds) — 이력 없이 분석 진행: alarm_id=%s",
            cfg.alarm.enrich_timeout_seconds,
            event.alarm_id,
        )
        return {"history_stats": None}
    except Exception:
        logger.exception(
            "알람 이력 조회 실패 — 이력 없이 분석 진행: alarm_id=%s", event.alarm_id
        )
        return {"history_stats": None}
