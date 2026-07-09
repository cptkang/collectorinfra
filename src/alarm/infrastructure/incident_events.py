"""Incident 이벤트 Redis pub/sub 발행기·구독기 (Plan 52 §9.1 · D-049).

워커는 PG 쓰기 풀을 신설하지 않고 incident 이벤트(open/resolved)를 Redis 채널
(`alarm:incident`)로 발행하고, API 프로세스의 단일 라이터(subscriber)가 수신하여
`IncidentStore`로 영속한다(쓰기 단일화 — D-048.10 브리지 패턴 재사용).

이벤트 스키마(양측 합의 §5):
    {"type": "open"|"resolved", "fingerprint": str, "alarm_id": str, "db_id": str,
     "server_name": str, "severity": int, "priority": str, "tier": str,
     "ts": isoformat, "resolution": str(resolved만)}

graceful: 발행/구독/영속 실패는 logger.warning 후 삼켜 파이프라인·서버 기동을 막지 않는다.
표준 라이브러리(json/logging/asyncio) + redis.asyncio만 사용한다(infrastructure 계층).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class RedisIncidentPublisher:
    """incident 이벤트를 Redis 채널로 publish하는 발행기(워커·API 공용, graceful)."""

    def __init__(self, redis_client, channel: str) -> None:  # noqa: ANN001
        """발행기를 초기화한다.

        Args:
            redis_client: redis.asyncio 클라이언트(보유 중인 연결 재사용)
            channel: pub/sub 채널명 (NoiseGateConfig.incident_event_channel)
        """
        self._redis = redis_client
        self._channel = channel

    async def publish(self, event: dict) -> None:
        """incident 이벤트를 Redis 채널에 publish한다(실패는 warning 후 삼킴).

        ensure_ascii=False로 한글을 보존하고, default=str로 datetime 등 비직렬화
        값을 방어한다. 발행 실패가 알람 파이프라인을 막지 않는다(graceful degradation).
        """
        try:
            data = json.dumps(event, ensure_ascii=False, default=str)
            await self._redis.publish(self._channel, data)
        except Exception:  # noqa: BLE001 — 발행 실패가 파이프라인을 막지 않는다
            logger.warning(
                "incident 이벤트 발행 실패(무시): channel=%s", self._channel
            )


def _parse_ts(raw) -> datetime:  # noqa: ANN001
    """이벤트의 ts(ISO8601)를 datetime으로 파싱한다(실패 시 현재 시각)."""
    if raw:
        try:
            return datetime.fromisoformat(str(raw))
        except (ValueError, TypeError):
            pass
    return datetime.now()


async def _handle_open(message: dict, incident_store, alarm_bus) -> None:  # noqa: ANN001
    """open 이벤트를 처리한다 — incident 생성 + (가능 시) alarm_bus 재발행.

    incident_store.create_open으로 행을 INSERT하고, 성공(iid>0) + alarm_bus 주입 시
    incident_id를 실어 SSE 카드를 재발행한다(후속 UI ack 연결용). 영속 실패(iid=0)는
    재발행하지 않는다(존재하지 않는 incident id 노출 방지).
    """
    iid = await incident_store.create_open(
        fingerprint=str(message.get("fingerprint", "")),
        alarm_id=str(message.get("alarm_id", "")),
        alarm_name=str(message.get("alarm_name", "")),
        db_id=str(message.get("db_id", "")),
        server_name=str(message.get("server_name", "")),
        severity=int(message.get("severity", 0) or 0),
        priority=str(message.get("priority", "")),
        tier=str(message.get("tier", "")),
        created_at=_parse_ts(message.get("ts")),
    )
    if iid and alarm_bus is not None:
        payload = {
            **message,
            "type": "alarm_notification",
            "tier": "page",
            "incident_id": iid,
        }
        await alarm_bus.publish(payload)


async def _handle_resolved(message: dict, incident_store) -> None:  # noqa: ANN001
    """resolved 이벤트를 처리한다 — fingerprint 매칭 open incident를 해소 UPDATE."""
    await incident_store.resolve_by_fingerprint(
        fingerprint=str(message.get("fingerprint", "")),
        resolved_at=_parse_ts(message.get("ts")),
        resolution=str(message.get("resolution", "clear")),
    )


async def run_incident_event_subscriber(
    redis_client,  # noqa: ANN001 — redis.asyncio 클라이언트
    channel: str,
    incident_store,  # noqa: ANN001 — IncidentStore (덕 타이핑)
    *,
    alarm_bus=None,  # noqa: ANN001 — AlarmNotificationBus | None (open 재발행용)
    stop_event: asyncio.Event | None = None,
) -> None:
    """Redis 채널을 구독해 incident 이벤트를 단일 PG 라이터로 영속한다(sse_bridge 미러).

    - type=="open": incident_store.create_open → (성공+alarm_bus 시) incident_id 재발행.
    - type=="resolved": incident_store.resolve_by_fingerprint.

    종료:
        - stop_event가 set되거나 CancelledError(task.cancel)를 받으면 정상 종료한다.
        - 손상 메시지·DB 오류는 warning 후 다음 메시지로 계속한다(graceful).
        - 종료 시 unsubscribe/close로 구독을 정리한다.

    Args:
        redis_client: redis.asyncio 클라이언트(API lifespan이 생성·소유)
        channel: 구독 채널명(NoiseGateConfig.incident_event_channel)
        incident_store: 영속 대상 IncidentStore
        alarm_bus: open 재발행 대상 in-memory 버스(app.state.alarm_bus, 옵션)
        stop_event: 협조적 종료 신호(없으면 cancel로만 종료)
    """
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)
    logger.info("incident 이벤트 구독 시작: channel=%s", channel)
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    continue
                data = message.get("data")
                if data is None:
                    continue
                event = json.loads(data)
                event_type = event.get("type")
                if event_type == "open":
                    await _handle_open(event, incident_store, alarm_bus)
                elif event_type == "resolved":
                    await _handle_resolved(event, incident_store)
                # 그 외 타입은 무시(향후 확장 대비)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — 손상 메시지·DB 오류는 무시하고 계속
                logger.warning(
                    "incident 이벤트 처리 실패(무시): channel=%s", channel
                )
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:  # noqa: BLE001
            logger.warning("incident 구독 unsubscribe 실패(무시): channel=%s", channel)
        closer = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
        if closer is not None:
            try:
                await closer()
            except Exception:  # noqa: BLE001
                logger.warning("incident 구독 close 실패(무시): channel=%s", channel)
        logger.info("incident 이벤트 구독 종료: channel=%s", channel)
