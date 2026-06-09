"""Redis Stream XREAD 소비 헬퍼.

AlarmWorker가 Redis Stream 'alarm:raw'에서 알람 메시지를 읽을 때 사용한다.
Consumer Group 방식(XREADGROUP)으로 여러 워커 인스턴스가 동시에 소비 가능하다.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


async def ensure_consumer_group(
    r: aioredis.Redis,
    stream_key: str,
    group: str,
) -> None:
    """Consumer Group이 없으면 생성한다.

    이미 존재하는 경우 에러를 무시한다 (BUSYGROUP).

    Args:
        r: Redis 클라이언트
        stream_key: Redis Stream 키
        group: Consumer Group 이름
    """
    try:
        await r.xgroup_create(stream_key, group, id="0", mkstream=True)
        logger.info("Consumer Group 생성: stream=%s group=%s", stream_key, group)
    except Exception as e:
        # BUSYGROUP: 이미 존재하는 그룹 — 정상
        if "BUSYGROUP" not in str(e):
            logger.warning("Consumer Group 생성 실패: %s", e)


async def read_messages(
    r: aioredis.Redis,
    stream_key: str,
    group: str,
    consumer: str,
    count: int = 10,
    block_ms: int = 2000,
) -> list[tuple[bytes, dict]]:
    """Redis Stream에서 메시지를 읽는다.

    XREADGROUP으로 Consumer Group에서 미처리 메시지를 읽는다.
    block_ms 동안 새 메시지를 기다린다.

    Args:
        r: Redis 클라이언트
        stream_key: Redis Stream 키
        group: Consumer Group 이름
        consumer: 소비자 이름
        count: 한 번에 읽을 최대 메시지 수
        block_ms: 블로킹 대기 시간 (밀리초)

    Returns:
        (msg_id, fields) 튜플 목록 (메시지 없으면 빈 리스트)
    """
    results = await r.xreadgroup(
        group,
        consumer,
        {stream_key: ">"},
        count=count,
        block=block_ms,
    )
    if not results:
        return []
    messages: list[tuple[bytes, dict]] = []
    for _, msgs in results:
        messages.extend(msgs)
    return messages


async def ack_message(
    r: aioredis.Redis,
    stream_key: str,
    group: str,
    msg_id: bytes,
) -> None:
    """메시지 처리 완료를 확인(ACK)한다.

    Args:
        r: Redis 클라이언트
        stream_key: Redis Stream 키
        group: Consumer Group 이름
        msg_id: 확인할 메시지 ID
    """
    await r.xack(stream_key, group, msg_id)
