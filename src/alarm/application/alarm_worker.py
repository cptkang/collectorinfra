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
from typing import Optional

import redis.asyncio as aioredis

from src.alarm.domain.alarm import AlarmEvent
from src.alarm.domain.flapping import MAX_STATES, flap_percent, update_flap_state
from src.alarm.domain.notification_policy import compute_fingerprint
from src.alarm.infrastructure.redis_queue import (
    ack_message,
    ensure_consumer_group,
    read_messages,
)
from src.alarm.orchestration.alarm_graph import build_alarm_graph

logger = logging.getLogger(__name__)

_CONSUMER_NAME = "worker-1"


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
        self._decision_store = None
        self._ticket_queue = None  # (E3) TICKET 티어 일배치 요약 큐
        self._sse_publisher = None  # (E3 후속) 워커→UI 실시간 SSE Redis pub/sub 발행기
        self._incident_publisher = None  # (D-049) incident 이벤트 Redis pub/sub 발행기
        # (D-049) 직전 self-heal 매칭 소요시간(초) — _update_firing_registry가 설정,
        # _process가 decision_store.record_resolution 기록에 사용(매칭 없으면 None).
        self._last_self_heal_duration: Optional[float] = None
        # 핑거프린트 dedup(재발생 억제, §6.1) — alarm_id dedup과 별개 경로.
        self._gate_dedup: dict[str, float] = {}
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
        self._decision_store = self._build_decision_store()
        self._ticket_queue = self._build_ticket_queue()
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

            if gate_on:
                # ── Plan 52 게이트 활성 경로 ──
                now = time.time()
                fingerprint = compute_fingerprint(event)

                # 핑거프린트 dedup(재발생 억제, §6.1). 해소 이벤트(severity 0)는
                # 자가복구 상관을 위해 dedup에서 제외하여 게이트까지 전달한다.
                # 심각도3은 sev3_repeat_interval_seconds(기본=공통)로 재통보 간격 분리(§6.1).
                if not event.is_clear and self._is_duplicate_fingerprint(
                    fingerprint, now, event.severity
                ):
                    logger.debug(
                        "중복(재발생) 알람 무시: fingerprint=%s alarm_id=%s",
                        fingerprint,
                        event.alarm_id,
                    )
                    await ack_message(r, stream_key, group, msg_id)
                    return

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
                },
                config={
                    "configurable": {
                        "app_config": self._config,
                        "history_repo": self._history_repo,
                        "history_redis": self._redis,
                        "process_client": self._process_client,
                        "noise_repo": self._noise_repo,
                        "decision_store": self._decision_store,
                        # (E3) TICKET 일배치 큐 — 워커는 cross-process라 alarm_bus는 미주입.
                        # 큐 적재는 동작하고, 티어 SSE는 sse_publisher(Redis 브리지)로 중계한다.
                        "ticket_queue": self._ticket_queue,
                        # (E3 후속·D-048.9) 워커→UI 실시간 SSE Redis pub/sub 발행기.
                        # off/Redis 부재 시 None → notifier는 로그 폴백(E3 무변경).
                        "sse_publisher": self._sse_publisher,
                        # (D-049) incident open 발행기 — notifier가 PAGE 결정 시 사용.
                        # off/Redis 부재 시 None → notifier는 발행 스킵(회귀 0).
                        "incident_publisher": self._incident_publisher,
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
    ) -> bool:
        """게이트 활성 시 핑거프린트 기반 재발생 dedup (Plan 52 §6.1).

        동일 핑거프린트(db_id+server+alarm_name+resource)가 재통보 간격(TTL) 내에
        이미 처리되었으면 True를 반환한다(재통보 억제). 만료 항목은 함께 정리한다.
        alarm_id dedup(_is_duplicate)과 완전히 별개 경로다(self._gate_dedup 사용).

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
            True이면 재발생 중복(처리 건너뜀), False이면 신규 처리
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
        last = self._gate_dedup.get(fingerprint)
        if last is not None and now - last < ttl:
            return True
        self._gate_dedup[fingerprint] = now
        expired = [k for k, v in self._gate_dedup.items() if now - v >= ttl]
        for k in expired:
            del self._gate_dedup[k]
        return False

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
