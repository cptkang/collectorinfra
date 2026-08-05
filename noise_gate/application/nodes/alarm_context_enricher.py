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
import time
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from noise_gate.domain.alarm import (
    AlarmEvent,
    AlarmHistoryStats,
    MessageEnrichment,
    ProcessSnapshot,
)
from noise_gate.domain.alarm_pattern import (
    compute_history_stats,
    history_entries_from_dicts,
    history_entries_to_dicts,
)
from noise_gate.domain.enrichment_profile import (
    parse_profile_map_csv,
    resolve_profile,
)
from noise_gate.domain.process_rank import classify_alarm_kind, select_top_processes
from noise_gate.infrastructure.embedding_provider import build_event_text
from noise_gate.infrastructure.polestar_noise_context import _unavailable

logger = logging.getLogger(__name__)


def _cache_key(event: AlarmEvent) -> str:
    """단기 조회 캐시 키 (Plan 47 §5.3)."""
    return f"alarm:histcache:{event.db_id}:{event.server_name}:{event.alarm_name}"


def _noise_cache_key(event: AlarmEvent) -> str:
    """노이즈 컨텍스트 단기 조회 캐시 키 (Plan 52 §8.3)."""
    return f"alarm:noisectx:{event.db_id}:{event.server_name}:{event.alarm_name}"


def _noise_unavailable() -> dict[str, Any]:
    """노이즈 컨텍스트 수집 불가 시 보수 dict (모든 신호 None, source='unavailable').

    정책 계층(decide_notification)이 source=='unavailable'을 보수적 PAGE로 처리한다(§6.3).
    계약 키는 polestar_noise_context._unavailable(_NOISE_CTX_KEYS 단일 출처, §7.2)에
    위임하여 세 구성점(여기·repo._unavailable·정상 fetch)이 키 불일치 없이 공유한다.
    """
    return _unavailable()


def _compute_root_notified(
    event: AlarmEvent,
    ctx: dict[str, Any],
    active_firings: Optional[dict],
    inhibition_window_seconds: int,
    *,
    now: Optional[float] = None,
) -> bool:
    """근본원인(root) 노드가 최근 통보됐는지 산출한다 (Plan 60 E4 하이브리드, §6.2).

    root 리소스명(`ctx["root_resource_name"]`)으로 워커 인히비션 상태 `active_firings`
    (스코프 키 `f"{db_id}|{server_name}"` → (severity, ts, alarm_key))를 조회해, root
    스코프가 inhibition_window_seconds 내 활성이면 True(=root 통보됨). active_firings
    미주입·root명 부재·창 만료면 False(보수적 — 게이트가 DASHBOARD 강등).
    """
    root_name = ctx.get("root_resource_name")
    if not root_name or not active_firings:
        return False
    rec = active_firings.get(f"{event.db_id}|{root_name}")
    if rec is None:
        return False
    try:
        _sev, ts, _key = rec
    except (TypeError, ValueError):
        return False
    clock = time.time() if now is None else now
    return (clock - ts) <= inhibition_window_seconds


def _annotate_root_text_similarity(
    event: AlarmEvent,
    ctx: dict[str, Any],
    noise_cfg,  # noqa: ANN001 — NoiseGateConfig
    embedding_provider,  # noqa: ANN001 — AlarmEmbeddingProvider | None
) -> None:
    """토폴로지+텍스트 융합 주석(root_text_similarity)을 ctx에 in-place 첨부한다 (B-7 L-4 · §15.4 D-035).

    E4 다홉 cascade가 산출한 root 리소스 NAME(`ctx["root_resource_name"]`)과 현재 알람 텍스트
    사이 코사인 유사도(0~1)를 로컬 임베딩으로 산출해 **root 귀속 신뢰도 설명 보강**으로 첨부한다
    (DiLink식 토폴로지+텍스트 융합). **cascaded/root_notified 판정은 불변** — 이 주석은 관측
    필드일 뿐 게이트 판정(SUPPRESS/DASHBOARD)에 영향을 주지 않는다(D-035 불변식).

    하위호환·회귀 0(옵트인·graceful):
        - topology_text_fusion_enabled off·provider 미주입(None)·root_resource 부재·유사도 None
          (provider inert/빈 텍스트) 중 하나라도 해당하면 **키를 첨부하지 않는다**(현행 비트동일).
        - 정책 모듈은 이 키를 참조하지 않는다(신규 noise_ctx 관측 필드일 뿐).
    """
    if embedding_provider is None:
        return
    if not bool(getattr(noise_cfg, "topology_text_fusion_enabled", False)):
        return
    # root_resource(연쇄 근본원인 ID)가 실제로 존재할 때만 — cascaded=False면 root는 None이라 skip.
    if not ctx.get("root_resource") or not ctx.get("root_resource_name"):
        return
    alarm_text = build_event_text(
        event.alarm_name, event.resource_type, event.condition_log
    )
    sim = embedding_provider.similarity(alarm_text, ctx["root_resource_name"])
    if sim is None:  # provider inert 또는 빈 텍스트 → 미첨부(하위호환)
        return
    ctx["root_text_similarity"] = round(sim, 4)


async def enrich_noise_context(
    event: AlarmEvent,
    noise_cfg,  # noqa: ANN001 — NoiseGateConfig
    repo,  # noqa: ANN001 — PolestarNoiseContextRepository
    redis_client=None,  # noqa: ANN001 — redis.asyncio.Redis | None
    *,
    collect_dependency: bool = False,
    multi_hop: bool = False,
    change_correlation: bool = False,
    active_firings: Optional[dict] = None,
    embedding_provider=None,  # noqa: ANN001 — AlarmEmbeddingProvider | None (B-7 L-4)
) -> dict[str, Any]:
    """캐시 확인 → 폴스타 노이즈 컨텍스트 고정 SQL 조회 → 캐시 적재 (Plan 52 §8.3 + Plan 60 E4).

    Redis 캐시는 폴스타 DB 부하 보호용 순수 최적화 — 캐시 실패는 무시하고 DB 조회한다.
    캐시 TTL은 noise_context_cache_ttl_seconds(0이면 캐시 비활성)를 사용한다.
    repo.fetch는 자체적으로 graceful degradation하여 실패 시 source="unavailable"을
    반환하므로, unavailable 결과는 캐시에 적재하지 않는다(재조회 기회 보존).

    collect_dependency(E2, §3.6): True면 repo.fetch가 의존성/부모 상태 SQL을 추가 실행해
    parent_avail_status를 채운다. False(기본·E1)면 의존성 SQL 미실행(parent_avail_status=None).

    multi_hop(E4, §6.2): True면 repo.fetch가 다홉 조상 연쇄(cascaded/root_resource/
    root_resource_name)를 추가 산출한다. 그 후 **캐시 이후**(정적-ish 값은 캐시, root_notified는
    동적이라 미캐시) `root_notified`를 active_firings로 신선 산출해 ctx에 채운다.

    change_correlation(E5, §7.2): True면 repo.fetch가 변경 피드를 조회·오버레이해
    change_nearby/change_candidates를 추가 산출한다(promote 전용 신호 — 노후 캐시 수용).

    embedding_provider(B-7 L-4, §15.4 D-035): topology_text_fusion_enabled + provider 가용 +
    root_resource 존재 시, **캐시 이후** root 리소스 NAME과 알람 텍스트 유사도(root_text_similarity)를
    산출해 ctx에 첨부한다(root 귀속 신뢰도 **설명 보강** 주석 — 판정 불변). None/off/inert면 미첨부.

    Returns:
        _NOISE_CTX_KEYS + source (+ multi_hop 시 root_notified) (+ L-4 시 root_text_similarity).
    """
    cache_enabled = (
        redis_client is not None and noise_cfg.noise_context_cache_ttl_seconds > 0
    )
    key = _noise_cache_key(event)

    ctx: Optional[dict[str, Any]] = None
    if cache_enabled:
        try:
            raw = await redis_client.get(key)
            if raw:
                ctx = json.loads(raw)
                # 캐시 히트 — 정책상 정상취급("unavailable"만 특별 처리되므로 "cache"는 정상)
                ctx["source"] = "cache"
        except Exception as e:
            logger.debug("노이즈 컨텍스트 캐시 조회 실패 — 무시하고 DB 조회 진행: %s", e)
            ctx = None

    if ctx is None:
        ctx = await repo.fetch(
            event,
            collect_dependency=collect_dependency,
            multi_hop=multi_hop,
            topology_max_hops=getattr(noise_cfg, "topology_max_hops", 5),
            topology_cache_ttl_seconds=getattr(
                noise_cfg, "topology_cache_ttl_seconds", 86400
            ),
            change_correlation=change_correlation,
            change_window_seconds=getattr(noise_cfg, "change_window_seconds", 3600),
        )

        if cache_enabled and ctx.get("source") != "unavailable":
            try:
                await redis_client.setex(
                    key,
                    noise_cfg.noise_context_cache_ttl_seconds,
                    json.dumps(ctx, ensure_ascii=False),
                )
            except Exception as e:
                logger.debug("노이즈 컨텍스트 캐시 적재 실패 — 무시: %s", e)

    # root_notified는 **캐시 이후** 동적 산출(캐시하지 않음 — 자연히 신선, §6.2).
    # multi_hop off면 미산출(게이트가 1홉 폴백이라 무의미). 캐시 왕복 dict는 root_notified가
    # 없으므로 여기서 항상 신선 계산해 덮어쓴다.
    if multi_hop:
        ctx["root_notified"] = _compute_root_notified(
            event,
            ctx,
            active_firings,
            int(getattr(noise_cfg, "inhibition_window_seconds", 300)),
        )
        # (B-7 L-4) root 귀속 신뢰도 설명 보강 주석 — root(연쇄 근본원인)가 산출된 다홉 경로에서만
        # 의미 있으므로 여기서 산출한다. off/provider 미주입/inert/root 부재면 미첨부(회귀 0).
        _annotate_root_text_similarity(event, ctx, noise_cfg, embedding_provider)

    return ctx


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

    # Plan 60 E6: classify_alarm_kind가 disk/network 등도 판정하게 확장됐으나, 이 함수
    # (process_enrich_enabled 경로)는 **현행처럼 cpu/memory만** 프로세스 조회한다(비트 동일).
    # 신규 kind의 host-wide 참고 스냅샷은 build_message_enrichment(message_enrichment_enabled)가 담당.
    kind = classify_alarm_kind(event)
    if kind not in ("cpu", "memory"):
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


async def _collect_host_snapshot(
    event: AlarmEvent,
    kind: str,
    alarm_cfg,  # noqa: ANN001 — AlarmConfig
    process_client,  # noqa: ANN001 — PolestarProcessApiClient | None
    timeout_seconds: float,
) -> Optional[ProcessSnapshot]:
    """disk/network 보강용 host-wide 프로세스 스냅샷을 수집한다 (Plan 60 E6 §16.3).

    기존 `list_by_hostname`(폴스타 REST GET, 읽기전용)만 재사용한다 — **신규 SQL 없음**.
    정렬은 select_top_processes의 기본(cpu)으로 host-wide 상위 N을 참고 첨부한다
    (신규 kind는 특화 정렬 대상이 아니므로 cpu 기준, §16.1). 게이팅·실패 시 None(graceful).
    """
    if process_client is None or event.is_clear:
        return None
    if process_client.get_base_url(event.db_id) is None:
        return None
    result = await asyncio.wait_for(
        process_client.list_by_hostname(event.db_id, event.hostname),
        timeout=timeout_seconds,
    )
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


async def build_message_enrichment(
    event: AlarmEvent,
    alarm_cfg,  # noqa: ANN001 — AlarmConfig
    noise_cfg,  # noqa: ANN001 — NoiseGateConfig
    process_client,  # noqa: ANN001 — PolestarProcessApiClient | None
) -> Optional[MessageEnrichment]:
    """kind별 L1 보강 블록을 조립한다 (Plan 60 E6 §16.3 — message_enrichment_enabled 뒤).

    cpu/memory는 기존 ProcessSnapshot("영향 프로세스" 표)로 이미 처리되므로 여기서는
    None을 반환한다(중복 첨부 방지). 신규 kind(disk/network/process/log)만 프로파일 요지를
    조립하고, 데이터 소스가 확정된 disk/network에 한해 host-wide 프로세스 스냅샷을 참고 첨부한다.

    소스 미확정 kind(process/log)는 요지 제목만 첨부(snapshot=None) — 관제 L1 데이터 소스가
    계획서에 정밀 정의되지 않아 확인 안 된 신규 SQL을 만들지 않는다(graceful, §16.3 제약).

    Returns:
        MessageEnrichment(disk/network/process/log) 또는 None(cpu/memory·비대상 kind·해소).
    """
    if event.is_clear:
        return None
    kind = classify_alarm_kind(event)
    if kind is None or kind in ("cpu", "memory"):
        return None
    profile = resolve_profile(
        kind, parse_profile_map_csv(getattr(noise_cfg, "enrichment_profile_map_csv", ""))
    )
    if profile is None:
        return None

    snapshot: Optional[ProcessSnapshot] = None
    # 데이터 소스 확정 kind(disk/network)만 host-wide 프로세스 스냅샷 참고 첨부.
    # 자체 try/except — 수집 실패가 요지 첨부(통보)를 막지 않는다(§16.3, 기존 gather 패턴).
    if profile.has_l1_data and kind in ("disk", "network"):
        timeout = float(getattr(noise_cfg, "enrichment_l1_timeout_seconds", 3.0))
        try:
            snapshot = await _collect_host_snapshot(
                event, kind, alarm_cfg, process_client, timeout
            )
        except Exception:
            logger.warning(
                "E6 host-wide 스냅샷 수집 실패 — 요지만 첨부: alarm_id=%s kind=%s",
                event.alarm_id,
                kind,
            )
            snapshot = None

    return MessageEnrichment(
        kind=kind,
        title=profile.title,
        signals=profile.signals,
        snapshot=snapshot,
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

    리포지토리·클라이언트(history_repo / history_redis / process_client / noise_repo)는
    config["configurable"]로 주입한다. 미주입 시(테스트 API 경로 등) 해당 조회를 건너뛴다.

    Plan 52: enable_noise_gate 활성 + noise_repo 주입 시에만 noise_context(중요도·유지보수·
    알림정책)를 추가 수집한다. **게이트 비활성 시 반환 dict는 기존과 동일한 2키
    ({history_stats, process_snapshot})를 유지**하여 회귀를 방지한다(noise_context 키 미포함).
    """
    event: AlarmEvent = state["alarm_event"]
    configurable = (config or {}).get("configurable", {})
    cfg = configurable.get("app_config")
    if cfg is None:
        return {"history_stats": None, "process_snapshot": None}

    repo = configurable.get("history_repo")
    redis_client = configurable.get("history_redis")
    process_client = configurable.get("process_client")
    noise_repo = configurable.get("noise_repo")
    # (Plan 60 E4) 워커 인히비션 상태(참조 전달·읽기전용) — root_notified 산출용. 미주입 시 None.
    active_firings = configurable.get("active_firings")
    # (Plan 60 E3) 동적 baseline 이상탐지 어댑터 — dynamic_baseline_enabled 시 워커가 주입.
    # 미주입/off면 이상탐지 태스크·키 미추가(anomaly_severity 산출 없음·회귀 0).
    metric_baseline = configurable.get("metric_baseline")
    # (Plan 60 B-7 L-4) 로컬 임베딩 provider(주석 전용) — topology_text_fusion_enabled 시 워커가
    # 주입. 미주입/off/inert면 root_text_similarity 미첨부(회귀 0). repo는 provider를 생성하지 않는다.
    embedding_provider = configurable.get("embedding_provider")

    # 게이트 활성 + noise_repo 주입 시에만 noise_context 수집(그 외 기존 2키 반환 유지).
    gate_cfg = getattr(cfg, "noise_gate", None)
    gate_on = bool(getattr(gate_cfg, "enable_noise_gate", False)) and noise_repo is not None

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

    async def _noise_context() -> Optional[dict[str, Any]]:
        """노이즈 컨텍스트 수집 — 게이트 비활성/repo 미주입 시 None, 실패 시 unavailable.

        예외를 전파하지 않는다(보수적 처리). 수집 실패(unavailable)는 정책 계층이
        보수적 PAGE로 처리하므로 안전하다(§6.3). 게이트 off면 호출되지 않는다.
        """
        if not gate_on:
            return None
        # E2 §3.6: dependency_suppression=True일 때만 의존성 SQL 실행(기본 off → E1 무변경).
        dep = bool(getattr(gate_cfg, "dependency_suppression", False))
        # E4 §6.2: 다홉은 의존성 억제의 상위 모드 — dependency_suppression AND multi_hop_cascade_enabled.
        multi_hop = dep and bool(getattr(gate_cfg, "multi_hop_cascade_enabled", False))
        # E5 §7.2: 변경 상관은 독립 옵트인(기본 off → 변경 피드 미조회·noise_ctx 키 None, 회귀 0).
        change_corr = bool(getattr(gate_cfg, "change_correlation_enabled", False))
        try:
            return await enrich_noise_context(
                event,
                gate_cfg,
                noise_repo,
                redis_client,
                collect_dependency=dep,
                multi_hop=multi_hop,
                change_correlation=change_corr,
                active_firings=active_firings,
                embedding_provider=embedding_provider,
            )
        except Exception:
            logger.exception(
                "노이즈 컨텍스트 조회 실패 — 보수적 처리로 진행: alarm_id=%s", event.alarm_id
            )
            return _noise_unavailable()

    # (Plan 60 E6) message_enrichment_enabled 뒤에서만 신규 kind 보강 수집(기본 off → 회귀 0).
    message_on = bool(getattr(gate_cfg, "message_enrichment_enabled", False))

    async def _message_enrichment() -> Optional[MessageEnrichment]:
        """메시지 기반 L1 보강 조립 — 실패 시 None (독립 degradation, E6 §16.3)."""
        try:
            return await build_message_enrichment(
                event, cfg.alarm, gate_cfg, process_client
            )
        except Exception:
            logger.exception(
                "메시지 보강 조립 실패 — 보강 없이 진행: alarm_id=%s", event.alarm_id
            )
            return None

    # (Plan 60 E3) gate_on AND dynamic_baseline_enabled일 때만 이상탐지 태스크·키 추가
    # (off면 키셋 불변·회귀 0 — E6 _message_enrichment 패턴과 동일). anomaly_severity만 산출.
    dynamic_baseline_on = gate_on and bool(
        getattr(gate_cfg, "dynamic_baseline_enabled", False)
    )

    async def _anomaly_baseline() -> Optional[int]:
        """동적 baseline 이상탐지 — 상향 후보 심각도 산출, 실패 시 None (독립 degradation, E3 §5.2).

        어댑터 미주입(테스트 API 경로 등) 시 None. 게이팅(kind 화이트리스트·해소·미등록 db_id)과
        시계열 조회·적합·캐시는 어댑터가 담당한다. 자체 try/except로 실패가 분석을 막지 않는다.
        """
        if metric_baseline is None:
            return None
        try:
            return await metric_baseline.compute_severity(event, gate_cfg, redis_client)
        except Exception:
            logger.exception(
                "동적 baseline 이상탐지 실패 — 상향 없이 진행: alarm_id=%s", event.alarm_id
            )
            return None

    # 수집 태스크·반환 키를 동적 구성한다. gate off·message off면 키셋은 기존 2키
    # ({history_stats, process_snapshot})로 **비트 동일**(회귀 0). gate_on → noise_context,
    # message_on → enrichment 키를 추가한다. gather 순서·값은 기존과 동일하다.
    tasks = [_history(), _processes()]
    keys = ["history_stats", "process_snapshot"]
    if gate_on:
        tasks.append(_noise_context())
        keys.append("noise_context")
    if message_on:
        tasks.append(_message_enrichment())
        keys.append("enrichment")
    if dynamic_baseline_on:
        tasks.append(_anomaly_baseline())
        keys.append("anomaly_severity")

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks), timeout=cfg.alarm.enrich_timeout_seconds
        )
    except asyncio.TimeoutError:
        logger.warning(
            "알람 컨텍스트 보강 타임아웃 (%ds) — 컨텍스트 없이 분석 진행: alarm_id=%s",
            cfg.alarm.enrich_timeout_seconds,
            event.alarm_id,
        )
        # 각 키 None — noise_context=None은 정책 계층이 보수적 PAGE로 처리(§6.3).
        return {k: None for k in keys}

    return dict(zip(keys, results))
