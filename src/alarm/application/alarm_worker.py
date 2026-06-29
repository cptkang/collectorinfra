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
from datetime import datetime

import redis.asyncio as aioredis

from src.alarm.domain.alarm import AlarmEvent
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
        # 핑거프린트 dedup(재발생 억제, §6.1) — alarm_id dedup과 별개 경로.
        self._gate_dedup: dict[str, float] = {}
        # 자가복구 상관용 발생 레지스트리(§3.7): fingerprint → (발생시각, severity).
        self._firing_registry: dict[str, tuple[float, int]] = {}

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
        self._redis = r
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

            if gate_on:
                # ── Plan 52 게이트 활성 경로 ──
                now = time.time()
                fingerprint = compute_fingerprint(event)

                # 핑거프린트 dedup(재발생 억제, §6.1). 해소 이벤트(severity 0)는
                # 자가복구 상관을 위해 dedup에서 제외하여 게이트까지 전달한다.
                if not event.is_clear and self._is_duplicate_fingerprint(fingerprint, now):
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
                },
                config={
                    "configurable": {
                        "app_config": self._config,
                        "history_repo": self._history_repo,
                        "history_redis": self._redis,
                        "process_client": self._process_client,
                        "noise_repo": self._noise_repo,
                        "decision_store": self._decision_store,
                    }
                },
            )
        except Exception:
            logger.exception("알람 처리 실패: msg_id=%s", msg_id)
        finally:
            await ack_message(r, stream_key, group, msg_id)

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

    def _is_duplicate_fingerprint(self, fingerprint: str, now: float) -> bool:
        """게이트 활성 시 핑거프린트 기반 재발생 dedup (Plan 52 §6.1).

        동일 핑거프린트(db_id+server+alarm_name+resource)가 repeat_interval_seconds 내에
        이미 처리되었으면 True를 반환한다(재통보 억제). 만료 항목은 함께 정리한다.
        alarm_id dedup(_is_duplicate)과 완전히 별개 경로다(self._gate_dedup 사용).

        Args:
            fingerprint: compute_fingerprint(event) 결과
            now: 현재 시각(time.time())

        Returns:
            True이면 재발생 중복(처리 건너뜀), False이면 신규 처리
        """
        ttl = self._config.noise_gate.repeat_interval_seconds
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
        if event.is_clear:
            rec = self._firing_registry.pop(fingerprint, None)
            if rec is not None:
                fired_ts, fired_sev = rec
                if now - fired_ts <= window and 1 <= fired_sev <= suppress_max:
                    self_heal = True
        elif 1 <= event.severity <= suppress_max:
            self._firing_registry[fingerprint] = (now, event.severity)

        # 만료 항목 정리
        expired = [
            k for k, (ts, _) in self._firing_registry.items() if now - ts > window
        ]
        for k in expired:
            del self._firing_registry[k]
        return self_heal
