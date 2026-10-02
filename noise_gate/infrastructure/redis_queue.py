"""Redis Stream XREAD 소비 헬퍼.

AlarmWorker가 Redis Stream 'alarm:raw'에서 알람 메시지를 읽을 때 사용한다.
Consumer Group 방식(XREADGROUP)으로 여러 워커 인스턴스가 동시에 소비 가능하다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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


async def dead_letter_message(
    r: aioredis.Redis,
    dead_stream_key: str,
    source_stream_key: str,
    msg_id: bytes | str,
    fields: dict,
    error: BaseException | str,
    *,
    maxlen: int = 1000,
) -> None:
    """처리 실패 메시지를 dead-letter 스트림에 보관한다 (D-184).

    원문(`data`)·출처 스트림·원 msg_id·에러 사유·실패 시각을 XADD한다. `MAXLEN ~ maxlen`으로
    무상한 적재를 막는다(근사 트리밍). 재처리는 운영자가 `XRANGE`로 원문을 꺼내 수동/스크립트로
    다시 `alarm:raw`에 XADD하는 방식(자동 재투입 없음 — 같은 실패의 무한 루프 방지).

    Args:
        r: Redis 클라이언트
        dead_stream_key: dead-letter 스트림 키 (AlarmConfig.dead_letter_stream_key)
        source_stream_key: 실패 메시지가 있던 원 스트림 키
        msg_id: 원 메시지 ID
        fields: 원 메시지 필드(b"data" 또는 "data")
        error: 예외 또는 사유 문자열(500자로 절단)
        maxlen: 근사 상한
    """
    data = fields.get(b"data", fields.get("data", b""))
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode("utf-8", errors="replace")
    if isinstance(msg_id, (bytes, bytearray)):
        msg_id = bytes(msg_id).decode("utf-8", errors="replace")
    reason = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
    await r.xadd(
        dead_stream_key,
        {
            "data": str(data),
            "source_stream": source_stream_key,
            "source_msg_id": str(msg_id),
            "error": reason[:500],
            "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        maxlen=maxlen,
        approximate=True,
    )
    logger.warning(
        "dead-letter 적재: stream=%s source=%s msg_id=%s error=%s",
        dead_stream_key, source_stream_key, msg_id, reason[:120],
    )
