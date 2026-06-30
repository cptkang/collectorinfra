"""incident 이벤트 발행기·구독기 테스트 (D-049, sse_bridge 미러).

- RedisIncidentPublisher.publish → redis.publish(channel, json) (graceful)
- run_incident_event_subscriber:
    open → incident_store.create_open 호출 + (성공 시) alarm_bus 재발행(incident_id 포함)
    resolved → incident_store.resolve_by_fingerprint 호출
    손상 JSON·DB 오류는 무시하고 계속(graceful), stop_event/CancelledError 종료

redis는 unittest.mock으로 모킹한다(라이브 Redis 불요).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from src.alarm.infrastructure.incident_events import (
    RedisIncidentPublisher,
    run_incident_event_subscriber,
)

CHANNEL = "alarm:incident"


# ─── 발행기 ──────────────────────────────────────────────────────────────────

async def test_publisher_publishes_json_to_channel():
    redis = AsyncMock()
    pub = RedisIncidentPublisher(redis, CHANNEL)
    event = {"type": "open", "fingerprint": "fp", "server_name": "한글서버"}

    await pub.publish(event)

    redis.publish.assert_awaited_once()
    args = redis.publish.await_args.args
    assert args[0] == CHANNEL
    assert json.loads(args[1]) == event
    assert "한글서버" in args[1]            # ensure_ascii=False


async def test_publisher_serializes_datetime_with_default_str():
    redis = AsyncMock()
    pub = RedisIncidentPublisher(redis, CHANNEL)
    await pub.publish({"ts": datetime(2026, 6, 30, 9, 0, 0)})
    assert "2026-06-30" in redis.publish.await_args.args[1]


async def test_publisher_graceful_on_redis_error():
    redis = AsyncMock()
    redis.publish.side_effect = RuntimeError("redis down")
    pub = RedisIncidentPublisher(redis, CHANNEL)
    await pub.publish({"type": "open"})    # 예외 전파 없이 반환되면 성공


# ─── 구독기 ──────────────────────────────────────────────────────────────────

class _FakeIncidentStore:
    """IncidentStore 대역 — create_open/resolve 호출을 수집한다."""

    def __init__(self, create_returns: int = 99) -> None:
        self.create_returns = create_returns
        self.create_calls: list[dict] = []
        self.resolve_calls: list[dict] = []

    async def create_open(self, **kwargs) -> int:  # noqa: ANN003
        self.create_calls.append(kwargs)
        return self.create_returns

    async def resolve_by_fingerprint(self, **kwargs) -> int:  # noqa: ANN003
        self.resolve_calls.append(kwargs)
        return 1


def _mock_redis_with_pubsub(get_message_side_effect):  # noqa: ANN001
    pubsub = AsyncMock()
    pubsub.get_message.side_effect = get_message_side_effect
    redis = MagicMock()
    redis.pubsub.return_value = pubsub
    return redis, pubsub


def _msg(payload: dict) -> dict:
    return {"type": "message", "channel": CHANNEL, "data": json.dumps(payload)}


async def test_subscriber_open_creates_incident_and_refanouts_with_id():
    open_event = {
        "type": "open", "fingerprint": "fp", "alarm_id": "A-1", "db_id": "polestar",
        "server_name": "srv-1", "severity": 2, "priority": "320", "tier": "page",
        "ts": "2026-06-30T09:00:00",
    }
    redis, pubsub = _mock_redis_with_pubsub([_msg(open_event), asyncio.CancelledError()])
    store = _FakeIncidentStore(create_returns=77)
    bus = AsyncMock()

    await run_incident_event_subscriber(redis, CHANNEL, store, alarm_bus=bus)

    assert len(store.create_calls) == 1
    assert store.create_calls[0]["fingerprint"] == "fp"
    assert store.create_calls[0]["severity"] == 2
    # alarm_bus 재발행 — incident_id 포함(후속 UI ack 연결용)
    bus.publish.assert_awaited_once()
    refanout = bus.publish.await_args.args[0]
    assert refanout["incident_id"] == 77
    pubsub.unsubscribe.assert_awaited_once_with(CHANNEL)


async def test_subscriber_open_no_refanout_when_persist_failed():
    # create_open이 0(영속 실패) 반환 → 존재하지 않는 id 노출 방지(재발행 안 함)
    open_event = {"type": "open", "fingerprint": "fp", "severity": 2}
    redis, _ = _mock_redis_with_pubsub([_msg(open_event), asyncio.CancelledError()])
    store = _FakeIncidentStore(create_returns=0)
    bus = AsyncMock()

    await run_incident_event_subscriber(redis, CHANNEL, store, alarm_bus=bus)

    assert len(store.create_calls) == 1
    bus.publish.assert_not_awaited()


async def test_subscriber_resolved_calls_resolve():
    resolved_event = {
        "type": "resolved", "fingerprint": "fp",
        "ts": "2026-06-30T09:05:00", "resolution": "self_heal",
    }
    redis, _ = _mock_redis_with_pubsub([_msg(resolved_event), asyncio.CancelledError()])
    store = _FakeIncidentStore()

    await run_incident_event_subscriber(redis, CHANNEL, store)

    assert len(store.resolve_calls) == 1
    assert store.resolve_calls[0]["fingerprint"] == "fp"
    assert store.resolve_calls[0]["resolution"] == "self_heal"


async def test_subscriber_ignores_bad_json_and_continues():
    bad = {"type": "message", "data": "NOT JSON"}
    good = _msg({"type": "resolved", "fingerprint": "fp"})
    redis, _ = _mock_redis_with_pubsub([None, bad, good, asyncio.CancelledError()])
    store = _FakeIncidentStore()

    await run_incident_event_subscriber(redis, CHANNEL, store)

    # 손상/None은 무시, 정상 1건만 처리
    assert len(store.resolve_calls) == 1


async def test_subscriber_stops_on_stop_event():
    stop = asyncio.Event()
    stop.set()
    redis, pubsub = _mock_redis_with_pubsub([])
    store = _FakeIncidentStore()

    await run_incident_event_subscriber(redis, CHANNEL, store, stop_event=stop)

    pubsub.get_message.assert_not_awaited()    # 루프 진입 직후 break
    assert store.create_calls == []
    pubsub.unsubscribe.assert_awaited_once_with(CHANNEL)
