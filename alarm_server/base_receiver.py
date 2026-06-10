"""알람 수신기 추상 기반 클래스.

수신 방식(TCP / HTTP 웹훅 등)이 달라도 Redis Stream 발행 인터페이스를 공유한다.
추후 HttpReceiver를 추가할 때 BaseReceiver만 상속하면 발행 코드 중복 없이 구현 가능하다.
"""

from __future__ import annotations

import abc
import json
import logging

import redis.asyncio as aioredis

from alarm_server.config import AlarmServerConfig

logger = logging.getLogger(__name__)


class BaseReceiver(abc.ABC):
    """알람 수신기 추상 기반 클래스.

    수신 방식(TCP / HTTP 웹훅 등)이 달라도 Redis Stream 발행 인터페이스를 공유한다.
    """

    def __init__(self, config: AlarmServerConfig) -> None:
        self._config = config
        self._redis: aioredis.Redis | None = None

    async def _init_redis(self) -> None:
        """Redis 연결을 초기화한다."""
        self._redis = aioredis.from_url(
            f"redis://{self._config.redis_host}:{self._config.redis_port}",
            password=self._config.redis_password or None,
            db=self._config.redis_db,
        )

    async def _publish(self, payload: dict) -> None:
        """파싱된 알람을 Redis Stream에 발행한다.

        Args:
            payload: 알람 데이터 딕셔너리
        """
        await self._redis.xadd(
            self._config.stream_key,
            {"data": json.dumps(payload, ensure_ascii=False)},
        )
        logger.debug(
            "알람 발행 완료: alarmId=%s severity=%s alarmStatus=%s",
            payload.get("alarmId"),
            payload.get("severity"),
            payload.get("alarmStatus"),
        )

    @abc.abstractmethod
    async def start(self) -> None:
        """수신 루프를 시작한다 (서브클래스에서 구현)."""
        ...
