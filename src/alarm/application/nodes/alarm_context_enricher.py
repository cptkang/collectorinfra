"""알람 컨텍스트 보강 노드 (Plan 47 + 47-1).

폴스타 DB 이력 통계(`history_stats`)와 영향 프로세스 스냅샷(`process_snapshot`)을
**서로 독립적으로** 수집하여 AlarmState를 채운다. 두 조회는 asyncio.gather로 동시
실행하며, 한쪽 실패가 다른 쪽·전체 분석을 막지 않는다 (Plan 47-1 §3.2).

graceful degradation 원칙 (Plan 47 계승):
    - 이력 조회 실패/타임아웃/db_id 미등록/Redis 캐시 장애 어느 경우에도
      파이프라인을 절대 차단하지 않는다 — history_stats=None으로 반환.
    - 프로세스 조회 실패/타임아웃/비200/미주입 어느 경우에도 process_snapshot=None.
    - Redis 캐시는 폴스타 DB 부하 보호용 순수 최적화 — 캐시 실패는 무시하고 DB 조회.

이력 데이터 흐름 (Plan 47 §4.2):
    1. Redis GET alarm:histcache:{db_id}:{server_name}:{alarm_name}
       → HIT: 캐시된 이력 행으로 통계 계산 (DB 미접근)
    2. MISS → PolestarAlarmHistoryRepository.fetch() → Redis SETEX (TTL)
    3. compute_history_stats(이력, 현재 이벤트) → AlarmState.history_stats

프로세스 데이터 흐름 (Plan 47-1 §5.4):
    CPU/메모리 발생 알람 + base_url 매핑 존재 시에만:
    classify_alarm_kind → client.list_by_hostname(hostname) → select_top_processes
    → ProcessSnapshot
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from src.alarm.domain.alarm import AlarmEvent, AlarmHistoryStats, ProcessSnapshot
from src.alarm.domain.alarm_pattern import (
    compute_history_stats,
    history_entries_from_dicts,
    history_entries_to_dicts,
)
from src.alarm.domain.process_rank import classify_alarm_kind, select_top_processes

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


async def enrich_processes(
    event: AlarmEvent,
    alarm_cfg,  # noqa: ANN001 — AlarmConfig
    process_client,  # noqa: ANN001 — PolestarProcessApiClient
) -> Optional[ProcessSnapshot]:
    """CPU/메모리 발생 알람 시점의 영향 프로세스를 조회·선별한다 (Plan 47-1 §5.4).

    게이팅 (어느 하나라도 미충족 시 None):
        1. process_enrich_enabled
        2. classify_alarm_kind(event) → cpu | memory
        3. is_clear == False (발생 알람)
        4. get_process_api_base_url(db_id) 매핑 존재
        5. process_client 주입됨

    조회는 hostname으로 수행한다 (server_name 아님 — Plan 47-1 §2, DB 이력과 정반대 키).
    정렬·선별·마스킹은 select_top_processes(순수 함수)가 결정적으로 수행한다.

    Returns:
        ProcessSnapshot (조회·선별 성공) 또는 None (게이팅 미충족/조회 불가).
    """
    if not alarm_cfg.process_enrich_enabled:
        return None
    if process_client is None:
        return None
    if event.is_clear:
        return None

    kind = classify_alarm_kind(event)
    if kind is None:
        logger.debug(
            "프로세스 조회 건너뜀 — CPU/메모리 알람 아님: alarm_id=%s type=%s",
            event.alarm_id,
            event.resource_type,
        )
        return None

    if process_client.get_base_url(event.db_id) is None:
        logger.debug(
            "프로세스 조회 건너뜀 — base_url 미매핑 db_id: %s (alarm_id=%s)",
            event.db_id,
            event.alarm_id,
        )
        return None

    # ★ 조회 키는 hostname (server_name 아님) — Plan 47-1 §2
    result = await process_client.list_by_hostname(event.db_id, event.hostname)
    if result is None:
        return None

    top, total = select_top_processes(result.processes, kind, alarm_cfg.process_top_n)
    return ProcessSnapshot(
        alarm_kind=kind,
        captured_at=result.captured_at,
        top=top,
        total_count=total,
        source_host=event.hostname,
    )


async def alarm_context_enricher_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """이력 통계(history_stats) + 영향 프로세스 스냅샷(process_snapshot)을 채운다.

    이력 조회와 프로세스 조회는 서로 독립이므로 asyncio.gather로 동시 실행하며,
    각각 자체 try/except로 실패 시 None — 한쪽 실패가 다른 쪽·전체 분석을 막지 않는다.
    노드 전체는 asyncio.wait_for(enrich_timeout_seconds)로 상한을 둔다 (Plan 47-1 §3.2).

    이력(history_stats):
        - cfg.alarm.history_enabled=False / repo 미주입 / 해소 알람 / 미등록 db_id /
          조회·계산 실패·타임아웃 시 None (Plan 47).
    프로세스(process_snapshot):
        - process_enrich_enabled=False / client 미주입 / 비대상 알람(디스크·네트워크) /
          해소 알람 / 미매핑 db_id / API 실패·타임아웃·비200 시 None (Plan 47-1).

    리포지토리·클라이언트(history_repo / history_redis / process_client)는
    config["configurable"]로 주입한다. 미주입 시(테스트 API 경로 등) 해당 조회를 건너뛴다.
    """
    event: AlarmEvent = state["alarm_event"]
    configurable = (config or {}).get("configurable", {})
    cfg = configurable.get("app_config")
    if cfg is None:
        return {"history_stats": None, "process_snapshot": None}

    repo = configurable.get("history_repo")
    redis_client = configurable.get("history_redis")
    process_client = configurable.get("process_client")

    async def _history() -> Optional[AlarmHistoryStats]:
        """이력 통계 수집 — 게이팅·실패 시 None (독립 degradation)."""
        if not cfg.alarm.history_enabled or repo is None:
            return None
        if event.is_clear:
            logger.debug("해소 알람 — 이력 조회 건너뜀: alarm_id=%s", event.alarm_id)
            return None
        if not repo.is_db_registered(event.db_id):
            logger.warning(
                "알람 이력 조회 건너뜀 — 미등록 db_id: %s (alarm_id=%s)",
                event.db_id,
                event.alarm_id,
            )
            return None
        try:
            stats = await enrich_history(event, cfg.alarm, repo, redis_client)
            logger.info(
                "알람 이력 통계 계산 완료: alarm_id=%s 분류=%s 발생=%d건 source=%s",
                event.alarm_id,
                stats.pre_classification,
                stats.total_count,
                stats.source,
            )
            return stats
        except Exception:
            logger.exception(
                "알람 이력 조회 실패 — 이력 없이 분석 진행: alarm_id=%s", event.alarm_id
            )
            return None

    async def _processes() -> Optional[ProcessSnapshot]:
        """영향 프로세스 수집 — 게이팅·실패 시 None (독립 degradation)."""
        try:
            snapshot = await enrich_processes(event, cfg.alarm, process_client)
            if snapshot is not None:
                logger.info(
                    "영향 프로세스 조회 완료: alarm_id=%s kind=%s top=%d/%d",
                    event.alarm_id,
                    snapshot.alarm_kind,
                    len(snapshot.top),
                    snapshot.total_count,
                )
            return snapshot
        except Exception:
            logger.exception(
                "영향 프로세스 조회 실패 — 프로세스 없이 분석 진행: alarm_id=%s",
                event.alarm_id,
            )
            return None

    try:
        history_stats, process_snapshot = await asyncio.wait_for(
            asyncio.gather(_history(), _processes()),
            timeout=cfg.alarm.enrich_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "알람 컨텍스트 보강 타임아웃 (%ds) — 컨텍스트 없이 분석 진행: alarm_id=%s",
            cfg.alarm.enrich_timeout_seconds,
            event.alarm_id,
        )
        return {"history_stats": None, "process_snapshot": None}

    return {"history_stats": history_stats, "process_snapshot": process_snapshot}
