"""알람 워커.

Redis Stream 'alarm:raw'에서 알람을 소비하여 AlarmAnalysisGraph를 실행한다.

주요 기능:
    - Consumer Group 기반 Redis Stream 소비 (XREADGROUP)
    - 중복 알람 제거 (alarm_id TTL 기반 in-memory dedup)
    - 심각도 임계값 필터링 (min_severity 미만 무시)
    - AlarmAnalysisGraph ainvoke → LLM 분석 → 채널 발송
    - 처리 완료 후 XACK

ALARM_ENABLED=false이면 즉시 반환한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import redis.asyncio as aioredis

if TYPE_CHECKING:
    from src.alarm.domain.topology import DependencyGraph

from src.alarm.domain.alarm import AlarmEvent
from src.alarm.domain.correlation import (
    ClusterState,
    match_cluster,
    signature_tokens,
)
from src.alarm.domain.flapping import MAX_STATES, flap_percent, update_flap_state
from src.alarm.domain.notification_policy import compute_fingerprint
from src.alarm.domain.severity_signatures import scan_signature_severity
from src.alarm.infrastructure.redis_queue import (
    ack_message,
    ensure_consumer_group,
    read_messages,
)
from src.alarm.orchestration.alarm_graph import build_alarm_graph

logger = logging.getLogger(__name__)

_CONSUMER_NAME = "worker-1"

# 위상 가중 그래프 로드 실패 시 음성 캐시 TTL(초) — 재시도 허용하되 hot-path 폭주 방지.
_TOPOLOGY_NEGATIVE_TTL_SECONDS = 60


class AlarmWorker:
    """Redis Stream에서 알람을 소비하여 분석 그래프를 실행한다."""

    def __init__(self, config) -> None:  # noqa: ANN001
        """AlarmWorker를 초기화한다.

        Args:
            config: AppConfig 인스턴스
        """
        self._config = config
        self._graph = None
        self._history_repo = None
        self._process_client = None
        self._redis = None
        # ── Plan 52: 노이즈 게이트 (enable_noise_gate 활성 시에만 사용) ──
        self._noise_repo = None
        # (Plan 60 E3) 동적 baseline 이상탐지 어댑터 — dynamic_baseline_enabled 시에만 생성.
        self._metric_baseline = None
        self._decision_store = None
        self._ticket_queue = None  # (E3) TICKET 티어 일배치 요약 큐
        self._feedback_store = None  # (E4) 운영자 피드백 few-shot 저장소
        self._sse_publisher = None  # (E3 후속) 워커→UI 실시간 SSE Redis pub/sub 발행기
        self._incident_publisher = None  # (D-049) incident 이벤트 Redis pub/sub 발행기
        # (D-049) 직전 self-heal 매칭 소요시간(초) — _update_firing_registry가 설정,
        # _process가 decision_store.record_resolution 기록에 사용(매칭 없으면 None).
        self._last_self_heal_duration: Optional[float] = None
        # 핑거프린트 dedup(재발생 억제, §6.1 · Plan 60 E1) — alarm_id dedup과 별개 경로.
        # 레코드 = {first_seen, last_notified, last_seen, count}.
        #   판정 필드 last_notified = 마지막 *통보* 시각(중복 판정 시 갱신 안 함 → 고정창).
        #   정리 필드 last_seen     = 마지막 *목격* 시각(중복마다 갱신 → 만료 sweep 기준).
        # 둘의 의미가 다름에 주의(§3.3): last_seen으로 TTL 비교하면 슬라이딩 창 회귀 발생.
        self._gate_dedup: dict[str, dict] = {}
        # 자가복구 상관용 발생 레지스트리(§3.7): fingerprint → (발생시각, severity).
        self._firing_registry: dict[str, tuple[float, int]] = {}
        # 인히비션(§3.4·E2): 스코프(db_id|server) → (최고심각도, 발생시각, 알람키).
        # 스코프별 현재 활성 최고 심각도 인히비터를 유지(self-inhibition 방지용 알람키 동반).
        self._active_firings: dict[str, tuple[int, float, str]] = {}
        # 플래핑(§3.7·E2): 핑거프린트 → 최근 상태(firing 여부) 시퀀스(maxlen=MAX_STATES).
        self._flap_states: dict[str, deque] = {}
        # 플래핑(§3.7·E2): 핑거프린트 → 직전 플래핑 상태(히스테리시스 유지용).
        self._flap_flag: dict[str, bool] = {}
        # 플래핑(§3.7·E2): 핑거프린트 → 마지막 갱신 시각(만료 키 정리용 — 메모리 일관성).
        self._flap_last_seen: dict[str, float] = {}
        # 스톰(§3.8·E2): 스코프(db_id|server) → 사건창 발생 타임스탬프 deque.
        self._storm_window: dict[str, deque] = {}
        # 크로스-호스트 상관(Plan 60 E2·§4): db_id(존) 스코프 → 활성 클러스터 리스트.
        # 존 경계는 넘지 않는다(§8 B-6). 만료 sweep(last_ts window 밖 제거·빈 스코프 키 삭제)
        # + 버퍼 상한(correlation_buffer_max)으로 메모리 가드(§10, 형제 상태 정리 루프와 일관).
        self._correlation_clusters: dict[str, list[ClusterState]] = {}
        # 위상 가중 그래프 캐시(Plan 60 E2 위상 가중): db_id → (만료 epoch, DependencyGraph|None).
        # correlation_topology_weight_enabled일 때만 사용. hot-path 클라이언트 open을 회피하기 위해
        # cold/만료 시에만 로드하고, 실패(None)도 짧은 음성 TTL로 캐시해 재시도를 허용한다(폭주 방지).
        self._topology_graph_cache: dict[str, tuple[float, Optional[DependencyGraph]]] = {}

    def _build_history_repo(self):  # noqa: ANN202
        """이력 조회 리포지토리를 생성한다 (Plan 47).

        history_enabled=False이거나 생성 실패 시 None을 반환한다 —
        패턴 분석만 생략되고 알람 분석·발송은 정상 진행된다 (graceful degradation).
        """
        if not self._config.alarm.history_enabled:
            return None
        try:
            from src.alarm.infrastructure.polestar_history import (
                PolestarAlarmHistoryRepository,
            )
            from src.routing.db_registry import DBRegistry

            return PolestarAlarmHistoryRepository(
                DBRegistry(self._config), self._config.alarm
            )
        except Exception:
            logger.exception("알람 이력 리포지토리 생성 실패 — 패턴 분석 비활성으로 진행")
            return None

    def _build_process_client(self):  # noqa: ANN202
        """영향 프로세스 API 클라이언트를 생성한다 (Plan 47-1).

        process_enrich_enabled=False이거나 생성 실패 시 None을 반환한다 —
        프로세스 보강만 생략되고 알람 분석·발송은 정상 진행된다 (graceful degradation).
        """
        if not self._config.alarm.process_enrich_enabled:
            return None
        try:
            from src.alarm.infrastructure.polestar_process_api import (
                PolestarProcessApiClient,
            )

            return PolestarProcessApiClient(self._config.alarm)
        except Exception:
            logger.exception("영향 프로세스 클라이언트 생성 실패 — 프로세스 보강 비활성으로 진행")
            return None

    def _build_noise_repo(self):  # noqa: ANN202
        """노이즈 컨텍스트 리포지토리를 생성한다 (Plan 52).

        enable_noise_gate=False이거나 생성 실패 시 None을 반환한다 —
        노이즈 컨텍스트 수집만 생략되고(정책 계층이 보수적 PAGE 처리) 발송은 진행된다.
        """
        if not self._config.noise_gate.enable_noise_gate:
            return None
        try:
            from src.alarm.infrastructure.polestar_noise_context import (
                PolestarNoiseContextRepository,
            )
            from src.routing.db_registry import DBRegistry

            return PolestarNoiseContextRepository(
                DBRegistry(self._config), self._config.alarm
            )
        except Exception:
            logger.exception("노이즈 컨텍스트 리포지토리 생성 실패 — 보수적(수집 없이) 진행")
            return None

    def _build_metric_baseline(self):  # noqa: ANN202
        """동적 baseline 이상탐지 어댑터를 생성한다 (Plan 60 E3 · D-079).

        enable_noise_gate=False 또는 dynamic_baseline_enabled=False이면 None을 반환한다 —
        이상탐지만 생략되고(anomaly_severity 미산출) 분석·발송은 정상 진행된다(회귀 0·graceful).
        """
        if not self._config.noise_gate.enable_noise_gate:
            return None
        if not getattr(self._config.noise_gate, "dynamic_baseline_enabled", False):
            return None
        try:
            from src.alarm.infrastructure.polestar_metric_baseline import (
                PolestarMetricBaselineAdapter,
            )
            from src.routing.db_registry import DBRegistry

            return PolestarMetricBaselineAdapter(
                DBRegistry(self._config), self._config.alarm
            )
        except Exception:
            logger.exception("메트릭 baseline 어댑터 생성 실패 — 이상탐지 없이 진행")
            return None

    def _build_decision_store(self):  # noqa: ANN202
        """발송 판단 감사 저장소를 생성한다 (Plan 52 §8.3).

        enable_noise_gate=False이거나 생성 실패 시 None을 반환한다 —
        감사 적재만 생략되고 발송 판단·발송은 정상 진행된다 (graceful degradation).
        """
        if not self._config.noise_gate.enable_noise_gate:
            return None
        try:
            from src.alarm.infrastructure.decision_store import DecisionStore

            return DecisionStore(
                self._config.noise_gate.decision_store_path,
                self._config.noise_gate.decision_store_enabled,
            )
        except Exception:
            logger.exception("발송 판단 감사 저장소 생성 실패 — 감사 없이 진행")
            return None

    def _build_ticket_queue(self):  # noqa: ANN202
        """TICKET 티어 일배치 요약 큐를 생성한다 (Plan 52 §7 · Phase E3).

        enable_noise_gate=False이거나 ticket_batch_queue_enabled=False이면 None을 반환한다 —
        큐 적재만 생략되고 발송 판단·발송은 정상 진행된다 (graceful degradation).
        """
        if not self._config.noise_gate.enable_noise_gate:
            return None
        if not self._config.noise_gate.ticket_batch_queue_enabled:
            return None
        try:
            from src.alarm.infrastructure.ticket_queue import TicketBatchQueue

            return TicketBatchQueue(
                self._config.noise_gate.ticket_batch_queue_path,
                self._config.noise_gate.ticket_batch_queue_enabled,
            )
        except Exception:
            logger.exception("TICKET 일배치 큐 생성 실패 — 큐 없이 진행")
            return None

    def _build_feedback_store(self):  # noqa: ANN202
        """운영자 피드백 few-shot 저장소를 생성한다 (Plan 52 Phase E4).

        enable_noise_gate=False이거나 enable_llm_actionability=False이면 None을 반환한다 —
        few-shot 조회만 생략되고(analyzer가 피드백 섹션 없이 진행) 발송은 정상 진행된다
        (graceful degradation·회귀 0).
        """
        if not self._config.noise_gate.enable_noise_gate:
            return None
        if not getattr(self._config.noise_gate, "enable_llm_actionability", False):
            return None
        try:
            from src.alarm.infrastructure.feedback_store import FeedbackStore

            return FeedbackStore(
                self._config.noise_gate.feedback_store_path,
                self._config.noise_gate.feedback_store_enabled,
            )
        except Exception:
            logger.exception("피드백 저장소 생성 실패 — few-shot 없이 진행")
            return None

    def _build_sse_publisher(self):  # noqa: ANN202
        """워커→UI 실시간 SSE Redis pub/sub 발행기를 생성한다 (E3 후속 · D-048.9 해소).

        enable_noise_gate=False·sse_bridge_enabled=False·Redis 클라이언트 부재 시 None을
        반환한다 — 티어 SSE는 로그 폴백(E3 무변경)으로 진행된다 (graceful degradation).
        self._redis가 설정된 이후(run() 내)에 호출해야 한다.
        """
        if not self._config.noise_gate.enable_noise_gate:
            return None
        if not getattr(self._config.noise_gate, "sse_bridge_enabled", False):
            return None
        if self._redis is None:
            return None
        try:
            from src.alarm.infrastructure.sse_bridge import RedisSseBridgePublisher

            return RedisSseBridgePublisher(
                self._redis, self._config.noise_gate.sse_bridge_channel
            )
        except Exception:
            logger.exception("SSE 브리지 발행기 생성 실패 — 로그 폴백으로 진행")
            return None

    def _build_incident_publisher(self):  # noqa: ANN202
        """incident 이벤트 Redis pub/sub 발행기를 생성한다 (D-049 · _build_sse_publisher 미러).

        enable_noise_gate=False·incident_tracking_enabled=False·Redis 클라이언트 부재 시
        None을 반환한다 — incident 발행은 스킵되고 발송은 정상 진행된다(회귀 0).
        self._redis가 설정된 이후(run() 내)에 호출해야 한다.
        """
        if not self._config.noise_gate.enable_noise_gate:
            return None
        if not getattr(self._config.noise_gate, "incident_tracking_enabled", False):
            return None
        if self._redis is None:
            return None
        try:
            from src.alarm.infrastructure.incident_events import RedisIncidentPublisher

            return RedisIncidentPublisher(
                self._redis, self._config.noise_gate.incident_event_channel
            )
        except Exception:
            logger.exception("incident 발행기 생성 실패 — incident 계측 없이 진행")
            return None

    async def run(self) -> None:
        """알람 소비 루프를 실행한다.

        ALARM_ENABLED=false이면 즉시 반환한다.
        asyncio.CancelledError를 받으면 Redis 연결을 닫고 종료한다.
        """
        if not self._config.alarm.enabled:
            logger.info("알람 워커 비활성 (ALARM_ENABLED=false)")
            return

        r = aioredis.from_url(
            f"redis://{self._config.redis.host}:{self._config.redis.port}",
            password=self._config.redis.password or None,
            db=self._config.redis.db,
        )
        stream_key = self._config.alarm.redis_stream_key
        group = self._config.alarm.redis_consumer_group

        await ensure_consumer_group(r, stream_key, group)
        self._graph = build_alarm_graph(self._config)
        self._history_repo = self._build_history_repo()
        self._process_client = self._build_process_client()
        self._noise_repo = self._build_noise_repo()
        self._metric_baseline = self._build_metric_baseline()
        self._decision_store = self._build_decision_store()
        self._ticket_queue = self._build_ticket_queue()
        self._feedback_store = self._build_feedback_store()
        self._redis = r
        self._sse_publisher = self._build_sse_publisher()
        self._incident_publisher = self._build_incident_publisher()
        dedup: dict[str, float] = {}

        logger.info(
            "알람 워커 시작 (stream=%s group=%s min_severity=%s)",
            stream_key,
            group,
            self._config.alarm.min_severity,
        )

        try:
            while True:
                try:
                    messages = await read_messages(
                        r, stream_key, group, _CONSUMER_NAME,
                        count=10, block_ms=2000,
                    )
                except asyncio.CancelledError:
                    break
                for msg_id, fields in messages:
                    await self._process(r, stream_key, group, msg_id, fields, dedup)
        finally:
            await r.aclose()
            logger.info("알람 워커 종료")

    async def _process(
        self,
        r: aioredis.Redis,
        stream_key: str,
        group: str,
        msg_id: bytes,
        fields: dict,
        dedup: dict[str, float],
    ) -> None:
        """단일 알람 메시지를 처리한다.

        파싱 → 중복 제거 → 심각도 필터 → 그래프 실행 → ACK 순서로 진행한다.
        예외 발생 시에도 ACK하여 메시지가 무한 재처리되지 않도록 한다.

        Args:
            r: Redis 클라이언트
            stream_key: Redis Stream 키
            group: Consumer Group 이름
            msg_id: 메시지 ID
            fields: 메시지 필드 딕셔너리 (b"data" 키에 JSON 문자열)
            dedup: 중복 제거용 alarm_id → 마지막 처리 시각 딕셔너리
        """
        try:
            payload = json.loads(fields[b"data"])
            alarm_time_str = payload.get("alarmTime", "")
            try:
                alarm_time = datetime.strptime(alarm_time_str, "%Y%m%d%H%M%S")
            except ValueError:
                alarm_time = datetime.now()

            alarm_status = payload.get("alarmStatus", "")
            severity = int(payload["severity"])
            # is_clear는 severity == 0 단독 기준 — alarmStatus는 폴스타 UI 인지(ACK)
            # 상태(NOT_ACK 등)로 해소 여부와 무관하다 (Plan 47 §9, D-035)
            is_clear = (severity == 0)

            event = AlarmEvent(
                db_id=payload.get("dbId", ""),
                server_name=payload.get("serverName", ""),
                hostname=payload.get("hostname", ""),
                ip_address=payload.get("ipAddress", ""),
                resource_ancestry=payload.get("resourceAncestry", ""),
                alarm_id=str(payload["alarmId"]),
                severity=severity,
                alarm_status=alarm_status,
                resource_type=payload.get("resourceType", ""),
                resource_name=payload.get("resourceName", ""),
                alarm_name=payload.get("alarmName", ""),
                alarm_time=alarm_time,
                conditions=payload.get("conditions", ""),
                condition_log=payload.get("conditionLog", ""),
                is_clear=is_clear,
                raw_payload=payload,
            )

            gate_on = bool(self._config.noise_gate.enable_noise_gate)
            self_heal = False
            inhibited = False
            flapping = False
            storm = False
            correlated = False  # (Plan 60 E2) 크로스-호스트 상관 매칭 여부
            # (Plan 60 E2) 상관 억제 시 클러스터 메타(대표 지문·멤버 순번·유사도) — 억제일 때만.
            correlation_meta: Optional[dict] = None
            # (Plan 60 E1) 재통보 시 직전 창 재발 메타 — 대표 알람 표기용 그래프 state.
            recurrence_prev: Optional[dict] = None

            if gate_on:
                # ── Plan 52 게이트 활성 경로 ──
                now = time.time()
                fingerprint = compute_fingerprint(event)

                # 핑거프린트 dedup(재발생 억제, §6.1). 해소 이벤트(severity 0)는
                # 자가복구 상관을 위해 dedup에서 제외하여 게이트까지 전달한다.
                # 심각도3은 sev3_repeat_interval_seconds(기본=공통)로 재통보 간격 분리(§6.1).
                # (Plan 60 E1) 억제 시 count 집계 감사(record_recurrence), 재통보 시
                # 직전 창 재발 메타(prev)를 그래프 state로 전달(대표 알람 표기).
                if not event.is_clear:
                    is_dup, rec_meta = self._is_duplicate_fingerprint(
                        fingerprint, now, event.severity
                    )
                    if is_dup:
                        logger.debug(
                            "중복(재발생) 알람 무시: fingerprint=%s alarm_id=%s count=%s",
                            fingerprint,
                            event.alarm_id,
                            rec_meta.get("count") if rec_meta else None,
                        )
                        self._record_recurrence(fingerprint, event, rec_meta)
                        await ack_message(r, stream_key, group, msg_id)
                        return
                    # 비중복(재통보) — 직전 창 재발 메타가 있으면 대표 알람 표기용으로 보관.
                    recurrence_prev = rec_meta

                # min_severity 역할 분리(§4.8): severity 0(해소)·3은 절대 드롭 금지.
                # 1 <= severity < min_severity 인 경우만 드롭(강등·억제는 게이트가 수행).
                if 1 <= event.severity < self._config.alarm.min_severity:
                    logger.debug(
                        "심각도 미달 알람 무시(게이트): alarm_id=%s severity=%s min=%s",
                        event.alarm_id,
                        event.severity,
                        self._config.alarm.min_severity,
                    )
                    await ack_message(r, stream_key, group, msg_id)
                    return

                # 자가복구 상관 시드(§3.7) — 발생 기록/해소 매칭.
                self_heal = self._update_firing_registry(event, fingerprint, now)

                # (D-049) 해소 이벤트 시 incident resolved 발행(트래커 off면 스킵) +
                # self-heal 매칭 시 자가복구 소요시간을 decision_store에 기록(편향 부분지표).
                if event.is_clear:
                    await self._publish_incident_resolved(event, fingerprint, self_heal)
                    if (
                        self_heal
                        and self._decision_store is not None
                        and self._last_self_heal_duration is not None
                    ):
                        self._decision_store.record_resolution(
                            fingerprint=fingerprint,
                            duration_seconds=self._last_self_heal_duration,
                        )

                # 인히비션 시드(§3.4·E2) — inhibition_enabled일 때만 탐지(아니면 detection
                # 자체 스킵 → 회귀 0). 동일 서버 상위 심각도 활성 시 inhibited=True.
                # getattr 기본 False — 경량 설정(테스트 SimpleNamespace 등)도 안전 처리.
                if getattr(self._config.noise_gate, "inhibition_enabled", False):
                    inhibited = self._detect_inhibition(event, now)

                # 플래핑 시드(§3.7·E2) — flapping_enabled일 때만 탐지(아니면 스킵 → 회귀 0).
                # 핑거프린트별 상태 시퀀스로 Nagios 가중 %-state-change·히스테리시스 산출.
                if getattr(self._config.noise_gate, "flapping_enabled", False):
                    flapping = self._detect_flapping(fingerprint, event, now)

                # 스톰 시드(§3.8·E2) — storm_grouping_enabled일 때만 탐지(아니면 스킵 → 회귀 0).
                # 스코프(db_id|server) 사건창 내 발생 다발 시 대표 외 storm=True.
                if getattr(self._config.noise_gate, "storm_grouping_enabled", False):
                    storm = self._detect_storm(event, now)

                # 크로스-호스트 상관 시드(§4·E2) — cross_host_correlation_enabled일 때만
                # 탐지(아니면 미수행 → detection 스킵·회귀 0). storm(동일 서버)과 독립 병존.
                # db_id(존) 스코프에서 온라인 그리디 군집, 대표 외 멤버부터 correlated=True.
                # 위상 가중(correlation_topology_weight_enabled)일 때만 그래프를 async 로드해
                # sync detection에 주입한다(워커 캐시로 hot-path 클라이언트 open 회피).
                if getattr(
                    self._config.noise_gate, "cross_host_correlation_enabled", False
                ):
                    topo_graph = None
                    if getattr(
                        self._config.noise_gate,
                        "correlation_topology_weight_enabled",
                        False,
                    ):
                        topo_graph = await self._load_topology_graph(event.db_id, now)
                    correlated, correlation_meta = self._detect_correlated_storm(
                        event, now, graph=topo_graph
                    )
            else:
                # ── 기존 경로 (게이트 off — 무변경) ──
                if self._is_duplicate(event, dedup):
                    logger.debug("중복 알람 무시: alarm_id=%s", event.alarm_id)
                    await ack_message(r, stream_key, group, msg_id)
                    return

                if event.severity < self._config.alarm.min_severity:
                    logger.debug(
                        "심각도 미달 알람 무시: alarm_id=%s severity=%s min=%s",
                        event.alarm_id,
                        event.severity,
                        self._config.alarm.min_severity,
                    )
                    await ack_message(r, stream_key, group, msg_id)
                    return

            logger.info(
                "알람 처리 시작: alarm_id=%s severity=%s name=%s",
                event.alarm_id,
                event.severity,
                event.alarm_name,
            )

            await self._graph.ainvoke(
                {
                    "alarm_event": event,
                    "history_stats": None,
                    "process_snapshot": None,
                    "analysis_result": None,
                    "error": None,
                    "noise_context": None,
                    "notification_decision": None,
                    "self_heal": self_heal,
                    "inhibited": inhibited,
                    "flapping": flapping,
                    "storm": storm,
                    # (Plan 60 E2) 크로스-호스트 상관 매칭 여부(off/미매칭이면 False).
                    "correlated": correlated,
                    # (Plan 60 E2) 상관 억제 시 클러스터 메타(비상관/미억제면 None).
                    "correlation_meta": correlation_meta,
                    # (Plan 60 E1) 재통보 시 직전 창 재발 메타(없으면 None).
                    "recurrence": recurrence_prev,
                    # (Plan 60 E6) 메시지 기반 L1 보강 블록(enricher가 채움, off면 None).
                    "enrichment": None,
                    # (Plan 60 E3) 동적 baseline 이상 상향 후보(enricher가 채움, off면 None).
                    "anomaly_severity": None,
                },
                config={
                    "configurable": {
                        "app_config": self._config,
                        "history_repo": self._history_repo,
                        "history_redis": self._redis,
                        "process_client": self._process_client,
                        "noise_repo": self._noise_repo,
                        # (Plan 60 E3) 동적 baseline 이상탐지 어댑터 — off/미생성 시 None →
                        # enricher는 이상탐지 태스크 미추가(anomaly_severity 미산출·회귀 0).
                        "metric_baseline": self._metric_baseline,
                        "decision_store": self._decision_store,
                        # (E3) TICKET 일배치 큐 — 워커는 cross-process라 alarm_bus는 미주입.
                        # 큐 적재는 동작하고, 티어 SSE는 sse_publisher(Redis 브리지)로 중계한다.
                        "ticket_queue": self._ticket_queue,
                        # (E4) 운영자 피드백 few-shot 저장소 — off/비활성 시 None →
                        # analyzer는 피드백 섹션 없이 진행(회귀 0).
                        "feedback_store": self._feedback_store,
                        # (E3 후속·D-048.9) 워커→UI 실시간 SSE Redis pub/sub 발행기.
                        # off/Redis 부재 시 None → notifier는 로그 폴백(E3 무변경).
                        "sse_publisher": self._sse_publisher,
                        # (D-049) incident open 발행기 — notifier가 PAGE 결정 시 사용.
                        # off/Redis 부재 시 None → notifier는 발행 스킵(회귀 0).
                        "incident_publisher": self._incident_publisher,
                        # (Plan 60 E4) 인히비션 활성 상태 참조 전달(읽기전용) — enricher가
                        # root_notified(다홉 하이브리드) 산출에 소비. 신규 저장소 불필요.
                        "active_firings": self._active_firings,
                    }
                },
            )
        except Exception:
            logger.exception("알람 처리 실패: msg_id=%s", msg_id)
        finally:
            await ack_message(r, stream_key, group, msg_id)

    async def _publish_incident_resolved(
        self, event: AlarmEvent, fingerprint: str, self_heal: bool
    ) -> None:
        """해소 이벤트 시 incident resolved 이벤트를 발행한다 (D-049 · graceful).

        incident_publisher 미주입(트래커 off) 시 발행을 스킵한다(회귀 0). resolution은
        self-heal 매칭이면 'self_heal', 아니면 'clear'다. subscriber(API)가 fingerprint로
        매칭 open incident를 resolved UPDATE하며, 매칭이 없으면 no-op이다.
        발행 실패는 발행기 내부에서 삼킨다(graceful — 워커 파이프라인 무차단).
        """
        if self._incident_publisher is None:
            return
        payload = {
            "type": "resolved",
            "fingerprint": fingerprint,
            "alarm_id": event.alarm_id,
            "db_id": event.db_id,
            "server_name": event.server_name,
            "severity": event.severity,
            "tier": "",
            "ts": datetime.now().isoformat(),
            "resolution": "self_heal" if self_heal else "clear",
        }
        await self._incident_publisher.publish(payload)

    def _is_duplicate(self, event: AlarmEvent, dedup: dict[str, float]) -> bool:
        """중복 알람 여부를 확인하고 dedup 딕셔너리를 갱신한다.

        동일 alarm_id가 dedup_ttl_seconds 내에 이미 처리된 경우 True를 반환한다.
        만료된 항목은 함께 정리한다.

        Args:
            event: 확인할 알람 이벤트
            dedup: alarm_id → 마지막 처리 시각 딕셔너리 (in-memory)

        Returns:
            True이면 중복 (처리 건너뜀), False이면 신규 처리
        """
        now = time.time()
        ttl = self._config.alarm.dedup_ttl_seconds
        last = dedup.get(event.alarm_id)
        if last is not None and now - last < ttl:
            return True
        dedup[event.alarm_id] = now
        # 만료 항목 정리 (메모리 누수 방지)
        expired = [k for k, v in dedup.items() if now - v >= ttl]
        for k in expired:
            del dedup[k]
        return False

    def _is_duplicate_fingerprint(
        self, fingerprint: str, now: float, severity: int
    ) -> tuple[bool, Optional[dict]]:
        """게이트 활성 시 핑거프린트 기반 재발생 dedup + count 집계 (Plan 52 §6.1 · Plan 60 E1).

        동일 핑거프린트(db_id+server+alarm_name+resource)가 재통보 간격(TTL) 내에
        이미 처리되었으면 (True, 재발생 메타)를 반환한다(재통보 억제). 만료 항목은
        함께 정리한다. alarm_id dedup(_is_duplicate)과 완전히 별개 경로다(_gate_dedup).

        **억제 판정 로직(TTL·심각도 분기)은 현행과 비트 동일**하다 — 집계(count/first_seen/
        last_seen)만 추가했다. TTL 비교 기준은 반드시 `last_notified`(마지막 *통보* 시각,
        중복 판정 시 갱신하지 않음 → 고정창 유지)다. `last_seen`(중복마다 갱신)으로 비교하면
        슬라이딩 창으로 변질되어 지속 재발 알람이 영원히 재통보되지 않는 회귀가 발생한다(§3.3).

        TTL은 심각도별로 분리한다(§6.1):
            - severity==3 → sev3_repeat_interval_seconds (미해결 sev3 재통보 단축용)
            - 그 외        → repeat_interval_seconds (기본 4h)
            기본값은 둘 다 14400(동일)이라 무변경(회귀 0). 운영 시 sev3만 단축 가능.
            (최초 발생 sev3는 항상 PAGE — 이 dedup은 *이미 PAGE한 같은 알람의 반복 빈도*
            조절이지 발송 판단 억제가 아니다, §4.8/§6.1.)

        Args:
            fingerprint: compute_fingerprint(event) 결과
            now: 현재 시각(time.time())
            severity: 알람 심각도 (TTL 분기용)

        Returns:
            (is_dup, meta) 튜플.
            - is_dup=True(재발생 중복): meta=현재 창의 집계 스냅샷(count 포함).
            - is_dup=False(신규/재통보): meta=리셋 직전 창 메타(직전 창 count>1일 때만,
              그 외 None). 대표 알람 "직전 재발 후 재통보" 표기용.
        """
        repeat_ttl = self._config.noise_gate.repeat_interval_seconds
        if severity == 3:
            # getattr 가드 — 경량 설정(테스트 SimpleNamespace 등)은 공통 간격으로 폴백(무변경).
            ttl = getattr(
                self._config.noise_gate,
                "sev3_repeat_interval_seconds",
                repeat_ttl,
            )
        else:
            ttl = repeat_ttl
        rec = self._gate_dedup.get(fingerprint)
        # 억제 판정 — 반드시 last_notified(통보 시각) 기준(고정창). 갱신하지 않는다.
        if rec is not None and now - rec["last_notified"] < ttl:
            # 억제(중복) — 통보 시각은 고정, 집계(count)·정리(last_seen)만 갱신.
            rec["count"] += 1
            rec["last_seen"] = now
            return True, dict(rec)
        # 신규(비중복, TTL 만료 후 재통보 포함) — 리셋 직전 창 메타를 prev로 캡처.
        # prev는 직전 창 count>1(억제 이력 있음)일 때만 의미 있다(대표 알람 표기용).
        prev: Optional[dict] = None
        if rec is not None and rec["count"] > 1:
            prev = dict(rec)
        self._gate_dedup[fingerprint] = {
            "first_seen": now,
            "last_notified": now,
            "last_seen": now,
            "count": 1,
        }
        # 만료 sweep은 last_seen 기준(연속 재발 레코드의 count 보존) — 현행(통보시각 기준)보다
        # 레코드 수명이 길어지는 메모리 의미 변화(§3.3). 판정(last_notified)과 정리(last_seen) 상이.
        expired = [
            k for k, v in self._gate_dedup.items() if now - v["last_seen"] >= ttl
        ]
        for k in expired:
            del self._gate_dedup[k]
        return False, prev

    def _record_recurrence(
        self, fingerprint: str, event: AlarmEvent, rec_meta: Optional[dict]
    ) -> None:
        """재발생 억제를 decision_store에 감사 적재한다 (Plan 60 E1 · graceful).

        억제된 재발생은 그래프에 진입하지 않아 gate 감사 사각지대였다(§3.1). 워커가 직접
        type="recurrence" 레코드를 적재해 "억제≠삭제"를 강화한다. decision_store 미주입
        (게이트 off/생성 실패)이거나 메타가 없으면 스킵한다(회귀 0). recurrence_audit_every_n
        샘플링으로 적재 빈도를 조절한다(기본 1=매번, count % N == 0일 때만).
        """
        if self._decision_store is None or rec_meta is None:
            return
        every_n = getattr(self._config.noise_gate, "recurrence_audit_every_n", 1)
        if every_n < 1:
            every_n = 1
        count = rec_meta.get("count", 0)
        if count % every_n != 0:
            return
        self._decision_store.record_recurrence(
            fingerprint=fingerprint,
            count=count,
            first_seen_ts=rec_meta.get("first_seen"),
            alarm_id=event.alarm_id,
        )

    def _update_firing_registry(
        self, event: AlarmEvent, fingerprint: str, now: float
    ) -> bool:
        """자가복구 상관용 발생 레지스트리를 갱신하고 self_heal 매칭 여부를 반환한다 (§3.7).

        - 발생 이벤트(not is_clear)이고 저심각도(1 <= severity <= suppress_max_severity)면
          (now, severity)를 기록한다.
        - 해소 이벤트(is_clear)면 동일 핑거프린트가 self_heal_window_seconds 내에 저심각도로
          발생했는지 확인하여 매칭되면 True를 반환한다(매칭 항목은 1회성 제거).
        - 심각도 3은 자가복구 후보에서 제외한다(해소가 와도 발생 PAGE 보존, §3.7).
        - 만료 항목은 정리한다(메모리 누수 방지).

        Returns:
            self_heal 매칭 여부 (해소 이벤트에서만 True 가능)
        """
        suppress_max = self._config.noise_gate.suppress_max_severity
        window = self._config.noise_gate.self_heal_window_seconds
        self_heal = False
        # (D-049) 직전 self-heal 소요시간 리셋 — 매칭 시에만 채운다(호출부가 기록).
        self._last_self_heal_duration = None
        if event.is_clear:
            rec = self._firing_registry.pop(fingerprint, None)
            if rec is not None:
                fired_ts, fired_sev = rec
                if now - fired_ts <= window and 1 <= fired_sev <= suppress_max:
                    self_heal = True
                    self._last_self_heal_duration = now - fired_ts
        elif 1 <= event.severity <= suppress_max:
            self._firing_registry[fingerprint] = (now, event.severity)

        # 만료 항목 정리
        expired = [
            k for k, (ts, _) in self._firing_registry.items() if now - ts > window
        ]
        for k in expired:
            del self._firing_registry[k]
        return self_heal

    def _detect_inhibition(self, event: AlarmEvent, now: float) -> bool:
        """인히비션(§3.4·E2): 동일 서버 상위 심각도 발생 중인지 결정적으로 탐지한다.

        스코프(db_id|server)별로 현재 활성 **최고 심각도 인히비터**를
        `(severity, 발생시각, alarm_key)`로 유지한다. 발생 이벤트가 도착하면:

        - 같은 스코프에 **더 높은 심각도**가 inhibition_window_seconds 내 활성이고
          그 인히비터가 **다른 알람**(self-inhibition 금지 — alarm_key 비교)이면 inhibited=True.
        - 활성 기록 갱신: 기존 기록이 없거나 만료됐거나 현재 발생이 같거나 더 높은 심각도면
          현재 발생으로 교체(스코프별 최고 심각도 인히비터 유지). 만료 스코프는 정리한다.
        - 해소 이벤트(is_clear)·무효 심각도는 인히비션 대상 아님(False) — 해당 스코프 기록은
          window 만료로 자연 정리된다(§3.4 해소는 detection 비대상).

        alarm_key는 alarm_name(없으면 fingerprint)으로, 동일 알람의 자기억제를 방지한다.
        inhibition_enabled일 때만 호출된다(_process 게이트 경로) — off면 detection 미수행.

        Returns:
            inhibited 여부(상위 심각도 다른 알람이 활성이면 True).
        """
        if event.is_clear or event.severity <= 0:
            return False

        scope = f"{event.db_id}|{event.server_name}"
        window = self._config.noise_gate.inhibition_window_seconds
        alarm_key = event.alarm_name or compute_fingerprint(event)

        inhibited = False
        rec = self._active_firings.get(scope)
        if rec is not None:
            rec_sev, rec_ts, rec_key = rec
            if (
                now - rec_ts <= window
                and rec_sev > event.severity
                and rec_key != alarm_key
            ):
                inhibited = True

        # 활성 기록 갱신 — 스코프별 최고 심각도 인히비터 유지(만료·동급/상위 발생 시 교체).
        if rec is None or now - rec[1] > window or event.severity >= rec[0]:
            self._active_firings[scope] = (event.severity, now, alarm_key)

        # 만료 스코프 정리(메모리 누수 방지)
        expired = [
            s for s, (_, ts, _) in self._active_firings.items() if now - ts > window
        ]
        for s in expired:
            del self._active_firings[s]
        return inhibited

    def _detect_flapping(
        self, fingerprint: str, event: AlarmEvent, now: float
    ) -> bool:
        """플래핑(§3.7·E2): 핑거프린트별 상태 진동을 Nagios 알고리즘으로 탐지한다.

        핑거프린트별 최근 상태 시퀀스(firing=not is_clear)를 maxlen=MAX_STATES(21) deque로
        유지하고, 도메인 순수함수로 가중 %-state-change(`flap_percent`)와 히스테리시스
        (`update_flap_state`, high/low 임계)를 적용하여 플래핑 여부를 산출한다.

        - 발생·해소 모두 상태 전이의 한 점이므로 시퀀스에 append한다(is_clear 제외하지 않음).
        - 직전 플래핑 상태(self._flap_flag)를 히스테리시스 입력으로 사용하고 갱신한다.
        - flapping_enabled일 때만 호출된다(_process 게이트 경로) — off면 detection 미수행(회귀 0).

        만료 키 정리(메모리 일관성): 핑거프린트별 마지막 갱신 시각(self._flap_last_seen)을
        추적하여, repeat_interval_seconds(기본 4h, 관대) 동안 재등장하지 않은 핑거프린트의
        상태를 _flap_states/_flap_flag/_flap_last_seen 세 dict에서 함께 제거한다.
        ttl이 관대하므로 활성 플래핑 상태(짧은 간격 재등장)는 보존되어 산출값이 불변이다.

        Returns:
            갱신된 플래핑 상태(True=플래핑 중 → 게이트에서 억제 대상).
        """
        states = self._flap_states.get(fingerprint)
        if states is None:
            states = deque(maxlen=MAX_STATES)
            self._flap_states[fingerprint] = states
        states.append(not event.is_clear)

        percent = flap_percent(list(states))
        prev = self._flap_flag.get(fingerprint, False)
        cfg = self._config.noise_gate
        new = update_flap_state(
            prev, percent, cfg.flap_high_threshold, cfg.flap_low_threshold
        )
        self._flap_flag[fingerprint] = new
        self._flap_last_seen[fingerprint] = now

        # 만료 핑거프린트 정리(메모리 누수 방지) — ttl은 재통보 간격 재사용(관대한 4h).
        # getattr 가드 — 경량 설정(테스트 SimpleNamespace 등)은 기본 4h로 폴백(무변경).
        ttl = getattr(cfg, "repeat_interval_seconds", 14400)
        expired = [
            k for k, ts in self._flap_last_seen.items() if now - ts > ttl
        ]
        for k in expired:
            del self._flap_last_seen[k]
            self._flap_states.pop(k, None)
            self._flap_flag.pop(k, None)
        return new

    def _detect_storm(self, event: AlarmEvent, now: float) -> bool:
        """스톰(§3.8·E2): 동일 서버 사건창 내 발생 다발 여부를 탐지한다.

        스코프(db_id|server)별 사건창 deque에 **발생 이벤트만** now를 append하고
        (해소 이벤트는 카운트 제외 — 발생 다발만 스톰으로 본다), storm_window_seconds 밖
        타임스탬프를 제거한다. 창 크기가 storm_threshold를 **초과**하면 storm=True.

        경계 규칙(대표/억제):
            - 창에 threshold개 이하가 쌓이는 동안(첫 threshold건)은 storm=False → 통보(대표).
            - 이번 건을 더해 창 크기 > threshold이면 storm=True → 억제(대표 외 다발).
            예) threshold=5 → 1~5번째 발생은 통보, 6번째부터 억제. 경계는 테스트로 고정.

        - storm_grouping_enabled일 때만 호출된다(_process 게이트 경로) — off면 미수행(회귀 0).

        만료 키 정리(메모리 일관성): window 밖 타임스탬프를 popleft한 뒤 해당 scope의 deque가
        비면 scope 키를 dict에서 제거한다(빈 deque 미보관). 각 scope는 자체 발생 이벤트로
        다시 트리거되므로 현재 처리 중 scope만 정리해도 단조 증가를 막을 수 있다.

        Returns:
            storm 여부(창 크기가 임계를 초과하면 True).
        """
        if event.is_clear:
            return False

        scope = f"{event.db_id}|{event.server_name}"
        window_sec = self._config.noise_gate.storm_window_seconds
        threshold = self._config.noise_gate.storm_threshold

        win = self._storm_window.get(scope)
        if win is None:
            win = deque()
            self._storm_window[scope] = win
        win.append(now)
        # 창 밖(window_seconds 초과) 항목 제거(가장 오래된 쪽부터)
        while win and now - win[0] > window_sec:
            win.popleft()
        # 빈 deque scope 키 제거(메모리 누수 방지) — append 직후라 보통 1건 이상이나,
        # window_sec<=0 등 경계에서 모두 만료되면 빈 키를 남기지 않는다.
        if not win:
            del self._storm_window[scope]
        return len(win) > threshold

    def _sweep_correlation_clusters(self, now: float, window_sec: int) -> None:
        """만료된 상관 클러스터를 제거하고 빈 스코프 키를 정리한다 (Plan 60 E2·§10).

        전 스코프(db_id 존)를 순회하여 last_ts가 window_sec 밖인 클러스터를 제거하고,
        클러스터가 모두 사라진 스코프 키를 dict에서 삭제한다. 스코프 수는 존 개수로
        자연 유한하므로 전수 순회가 저렴하며, 형제 상태(_flap_last_seen·_gate_dedup)의
        전-키 정리 루프와 일관되게 조용한 존의 클러스터도 만료시킨다(단조 증가 방지).
        """
        empty_scopes = [
            scope
            for scope, clusters in self._correlation_clusters.items()
            if not self._prune_expired(clusters, now, window_sec)
        ]
        for scope in empty_scopes:
            del self._correlation_clusters[scope]

    @staticmethod
    def _prune_expired(
        clusters: list[ClusterState], now: float, window_sec: int
    ) -> list[ClusterState]:
        """window_sec 밖 클러스터를 in-place 제거하고 남은 리스트를 반환한다."""
        clusters[:] = [c for c in clusters if now - c.last_ts <= window_sec]
        return clusters

    async def _load_topology_graph(
        self, db_id: str, now: float
    ) -> Optional[DependencyGraph]:
        """위상 가중용 정적 의존 그래프를 로드한다(워커 캐시 — hot-path 클라이언트 open 회피).

        correlation_topology_weight_enabled일 때만 _process(async)가 호출한다. cold/만료
        시에만 registry 클라이언트를 열어 TopologyLoader().load_graph로 로드하고, db_id별로
        (만료 epoch, graph)를 캐시한다. 실패(미등록·비PostgreSQL·조회 실패)는 None을 짧은
        음성 TTL(_TOPOLOGY_NEGATIVE_TTL_SECONDS)로 캐시해 재시도를 허용하되 hot-path 폭주를
        막는다(graceful — 보너스 없이 Jaccard로 진행).

        registry 접근은 _build_metric_baseline 패턴(DBRegistry(self._config))을 재사용한다.
        그래프는 event.db_id(존)로 로드하므로 인접성도 존 내에서만 계산된다(B-6 불변).

        Args:
            db_id: 대상 폴스타 DB(존).
            now: 캐시 만료 판정 기준 시각(time.time()).

        Returns:
            DependencyGraph 또는 None(실패·미지원).
        """
        cached = self._topology_graph_cache.get(db_id)
        if cached is not None and now < cached[0]:
            return cached[1]

        cfg = self._config.noise_gate
        ttl = getattr(cfg, "topology_cache_ttl_seconds", 86400)
        max_hops = getattr(cfg, "topology_max_hops", 5)
        graph: Optional[DependencyGraph] = None
        try:
            from src.alarm.infrastructure.topology_loader import TopologyLoader
            from src.routing.db_registry import DBRegistry

            registry = DBRegistry(self._config)
            if registry.is_registered(db_id):
                async with registry.get_client(db_id) as client:
                    graph = await TopologyLoader().load_graph(
                        client,
                        db_id,
                        cache_ttl_seconds=ttl,
                        max_hops=max_hops,
                    )
        except Exception:
            logger.exception(
                "위상 가중 그래프 로드 실패 — 보너스 없이 진행: db_id=%s", db_id
            )
            graph = None

        # 성공=장기 TTL, 실패(None)=짧은 음성 TTL(재시도 허용·폭주 방지).
        expiry = now + (ttl if graph is not None else _TOPOLOGY_NEGATIVE_TTL_SECONDS)
        self._topology_graph_cache[db_id] = (expiry, graph)
        return graph

    def _detect_correlated_storm(
        self,
        event: AlarmEvent,
        now: float,
        *,
        graph: Optional[DependencyGraph] = None,
    ) -> tuple[bool, Optional[dict]]:
        """크로스-호스트 상관(§4·E2): db_id(존) 스코프 온라인 그리디 군집을 탐지한다.

        서버 경계를 넘어 필드 유사도(Jaccard)·시간 근접으로 사건을 군집한다. 첫 도착
        이벤트가 클러스터 **대표**(통보)이고, 이후 유사 이벤트가 correlation_min_cluster_size
        번째 멤버부터 억제(correlated=True)된다(소급 선출 없음 — 앞선 판정 불변).

        절차:
            - 해소(is_clear) 이벤트는 군집/카운트에서 제외한다(_detect_storm 동일).
            - ① 만료 sweep: last_ts가 correlation_window_seconds 밖인 클러스터를 전 스코프에서
              제거하고 빈 스코프 키를 삭제한다(§10 — 키 만료 sweep 의무).
            - ② 버퍼 상한: 스코프의 활성 클러스터가 correlation_buffer_max 이상이면 oldest
              (first_ts 최소)부터 제거해 append 여지를 확보하고 warning을 남긴다(침묵 금지).
            - sig_label은 워커가 domain scan_signature_severity(event.condition_log)로 사전
              산출한다(정책 순수성 — correlation.py는 값만 소비). signature_tokens는 server_name을
              제외한 정규화 토큰을 만든다.
            - **위상 가중(E4 인접성)**: graph가 주어지고 event_node(=graph.id_of(server_name))
              해소에 성공하면, 각 클러스터의 대표 노드와의 is_related(max_hops)로 adjacent bool
              리스트를 산출해 match_cluster에 주입한다(인접 클러스터에 보너스). graph 미주어짐/
              이름 해소 실패(엣지 미보유 root 등) → adjacent=None(보너스 없음·Jaccard 폴백·graceful).
            - match_cluster로 최고 유사도 클러스터를 찾는다(보너스 반영 후 점수, 동점 시 first_ts
              오름차순). 매칭되면 member_count+1·last_ts=now 후 member_count>=min_cluster_size면
              억제하고 **클러스터 메타**(대표 지문·멤버 순번·유사도)를 반환한다. 미매칭이면 대표
              (첫 도착) 클러스터를 append(대표 노드 저장)하고 (False, None)을 반환한다.

        cross_host_correlation_enabled일 때만 호출된다(_process 게이트 경로) — off면 미수행(회귀 0).

        Args:
            event: 알람 이벤트.
            now: 현재 시각(time.time()).
            graph: 위상 가중용 DependencyGraph(위상 가중 off·로드 실패면 None → Jaccard 폴백).

        Returns:
            (correlated, cluster_meta) 튜플. 억제(correlated=True)일 때만 메타
            (`{representative_fp, member_seq, similarity}`)를 싣고, 그 외에는 (…, None).
        """
        if event.is_clear:
            return False, None

        cfg = self._config.noise_gate
        window_sec = cfg.correlation_window_seconds

        # ① 만료 sweep — 전 스코프의 window 밖 클러스터 제거 + 빈 스코프 키 삭제.
        self._sweep_correlation_clusters(now, window_sec)

        scope = event.db_id  # db_id(존) 스코프 — 존 경계는 넘지 않는다(§8 B-6).
        clusters = self._correlation_clusters.get(scope)
        if clusters is None:
            clusters = []
            self._correlation_clusters[scope] = clusters

        # ② 버퍼 상한 — 초과 시 oldest(first_ts 최소)부터 제거 + warning(침묵 금지).
        buffer_max = cfg.correlation_buffer_max
        if len(clusters) >= buffer_max:
            clusters.sort(key=lambda c: c.first_ts)
            overflow = len(clusters) - buffer_max + 1  # append 여지 1건 확보
            del clusters[:overflow]
            logger.warning(
                "상관 버퍼 상한 초과 — oldest %d건 제거: scope=%s max=%s",
                overflow,
                scope,
                buffer_max,
            )

        # sig_label은 domain 순수함수로 사전 산출(policy 순수성 — infra 미의존).
        hit = scan_signature_severity(getattr(event, "condition_log", "") or "")
        sig_label = hit[1] if hit else ""
        tokens = signature_tokens(event.alarm_name, event.resource_type, sig_label)

        # 위상 가중(E4 인접성) — graph·event_node 해소 성공 시에만 adjacent 산출(그 외 None).
        topo_enabled = getattr(cfg, "correlation_topology_weight_enabled", False)
        topo_weight = (
            getattr(cfg, "correlation_topology_weight", 0.0) if topo_enabled else 0.0
        )
        event_node = graph.id_of(event.server_name) if graph is not None else None
        adjacent: Optional[list[bool]] = None
        if graph is not None and event_node is not None:
            max_hops = getattr(cfg, "topology_max_hops", 5)
            adjacent = [
                bool(
                    c.representative_node
                    and graph.is_related(
                        event_node, c.representative_node, max_hops=max_hops
                    )
                )
                for c in clusters
            ]

        idx, score = match_cluster(
            clusters,
            tokens,
            sim_threshold=cfg.correlation_sim_threshold,
            adjacent=adjacent,
            topo_weight=topo_weight,
        )
        if idx is not None:
            c = clusters[idx]
            c.member_count += 1
            c.last_ts = now
            correlated = c.member_count >= cfg.correlation_min_cluster_size
            if correlated:
                logger.debug(
                    "크로스-호스트 상관 억제: scope=%s 대표=%s member=%s sim=%.4f",
                    scope,
                    c.representative_fp,
                    c.member_count,
                    score,
                )
                # 감사 메타(decision_store 최상위 필드) — signals 동결 스키마 미훼손(§10,
                # E1 recurrence 전례). similarity는 위상 보너스 반영 후 최종 점수다.
                meta = {
                    "representative_fp": c.representative_fp,
                    "member_seq": c.member_count,
                    "similarity": round(score, 4),
                }
                return True, meta
            return False, None
        # 첫 도착 = 대표 → 통보(소급 선출 없음). 대표 노드(resource_id)를 해소해 저장.
        clusters.append(
            ClusterState(
                compute_fingerprint(event),
                tokens,
                now,
                now,
                representative_node=event_node,
            )
        )
        return False, None
