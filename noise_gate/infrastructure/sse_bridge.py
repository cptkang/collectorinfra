"""알람 SSE Redis pub/sub 브리지 (Plan 52 E3 후속 · D-048.9 한계 해소).

워커 프로세스(cross-process)는 API의 in-memory `AlarmNotificationBus`(app.state.alarm_bus)를
공유할 수 없어, 워커 경로의 TICKET/DASHBOARD 티어 SSE가 로그 폴백에 그쳤다(D-048.9).
이 모듈은 Redis pub/sub을 경유해 워커 → API in-memory 버스로 SSE 이벤트를 중계한다.

구성:
    - RedisSseBridgePublisher: 워커측 발행기. `AlarmNotificationBus`와 동일한
      `publish(event)` 인터페이스(덕 타이핑)로, payload를 Redis 채널에 publish한다.
    - run_sse_bridge_subscriber: API측 구독기. Redis 채널을 구독해 수신 이벤트를
      in-memory `alarm_bus`로 재팬아웃하여 기존 SSE 엔드포인트가 그대로 전달한다.

graceful: 발행/구독 실패는 logger.warning 후 삼켜 파이프라인·서버 기동을 막지 않는다.
표준 라이브러리(json/logging/asyncio) + redis.asyncio만 사용한다(infrastructure 계층).
"""

from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class RedisSseBridgePublisher:
    """티어 SSE payload를 Redis 채널로 publish하는 워커측 발행기.

    `AlarmNotificationBus.publish(event)`와 동일한 시그니처(덕 타이핑)를 제공하여
    notifier가 alarm_bus와 구분 없이 호출할 수 있다. publish 실패는 삼킨다(graceful).
    """

    def __init__(self, redis_client, channel: str) -> None:  # noqa: ANN001
        """발행기를 초기화한다.

        Args:
            redis_client: redis.asyncio 클라이언트(워커가 보유한 self._redis 재사용)
            channel: pub/sub 채널명 (NoiseGateConfig.sse_bridge_channel)
        """
        self._redis = redis_client
        self._channel = channel

    async def publish(self, event: dict) -> None:
        """SSE 이벤트를 Redis 채널에 publish한다(실패는 warning 후 삼킴).

        ensure_ascii=False로 한글을 보존하고, default=str로 datetime 등 비직렬화
        값을 방어한다. 발행 실패가 알람 파이프라인을 막지 않는다(graceful degradation).
        """
        try:
            data = json.dumps(event, ensure_ascii=False, default=str)
            await self._redis.publish(self._channel, data)
        except Exception:  # noqa: BLE001 — SSE 발행 실패가 파이프라인을 막지 않는다
            logger.warning(
                "SSE 브리지 발행 실패(무시): channel=%s", self._channel
            )


async def run_sse_bridge_subscriber(
    redis_client,  # noqa: ANN001 — redis.asyncio 클라이언트
    channel: str,
    alarm_bus,  # noqa: ANN001 — AlarmNotificationBus (덕 타이핑 publish)
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Redis 채널을 구독해 수신 SSE 이벤트를 in-memory alarm_bus로 재팬아웃한다.

    워커가 RedisSseBridgePublisher로 publish한 payload(JSON)를 수신하여
    `alarm_bus.publish(dict)`로 전달하면, 기존 `/alarm/notifications/stream`
    엔드포인트가 구독 중인 UI에 그대로 브로드캐스트한다.

    종료:
        - stop_event가 set되거나 CancelledError(task.cancel)를 받으면 정상 종료한다.
        - 그 외 메시지 처리 예외(JSON 손상·버스 오류)는 warning 후 다음 메시지로 계속한다.
        - 종료 시 unsubscribe/close로 구독을 정리한다.

    Args:
        redis_client: redis.asyncio 클라이언트(API lifespan이 생성·소유)
        channel: 구독 채널명
        alarm_bus: 재팬아웃 대상 in-memory 버스(app.state.alarm_bus)
        stop_event: 협조적 종료 신호(없으면 cancel로만 종료)
    """
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)
    logger.info("SSE 브리지 구독 시작: channel=%s", channel)
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
                await alarm_bus.publish(json.loads(data))
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — 손상 메시지·버스 오류는 무시하고 계속
                logger.warning(
                    "SSE 브리지 메시지 처리 실패(무시): channel=%s", channel
                )
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:  # noqa: BLE001 — 정리 실패는 무시
            logger.warning("SSE 브리지 unsubscribe 실패(무시): channel=%s", channel)
        closer = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
        if closer is not None:
            try:
                await closer()
            except Exception:  # noqa: BLE001 — 정리 실패는 무시
                logger.warning("SSE 브리지 close 실패(무시): channel=%s", channel)
        logger.info("SSE 브리지 구독 종료: channel=%s", channel)
