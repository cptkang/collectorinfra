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

import redis.asyncio as aioredis

from src.alarm.domain.alarm import AlarmEvent
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
            event = AlarmEvent(
                alarm_id=str(payload["alarmId"]),
                severity=int(payload["severity"]),
                alarm_name=payload.get("alarmName", ""),
                alarm_description=payload.get("alarmDescription", ""),
                alarm_definition=payload.get("alarmDefinition", ""),
                hostname=payload.get("hostname", ""),
                resource_name=payload.get("resourceName", ""),
                resource_description=payload.get("resourceDescription", ""),
                resource_type=payload.get("resourceType", ""),
                condition_log=payload.get("conditionLog", ""),
                source_db_id=payload.get("sourceDbId", ""),
                raw_payload=payload,
            )

            # 중복 제거
            if self._is_duplicate(event, dedup):
                logger.debug("중복 알람 무시: alarm_id=%s", event.alarm_id)
                await ack_message(r, stream_key, group, msg_id)
                return

            # 심각도 필터 (min_severity 미만 무시)
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
                {"alarm_event": event, "analysis_result": None, "error": None},
                config={"configurable": {"app_config": self._config}},
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
