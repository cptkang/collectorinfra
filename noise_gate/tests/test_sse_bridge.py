"""알람 SSE Redis pub/sub 브리지 테스트 (Plan 52 E3 후속 · D-048.9 해소).

검증 범위:
    - RedisSseBridgePublisher.publish → redis.publish(channel, json) 호출 (① 발행)
    - publish 실패 graceful — 예외 전파 안 함 (④)
    - run_sse_bridge_subscriber → 수신 메시지를 alarm_bus.publish로 재팬아웃 + CancelledError
      정상 종료 (②), 손상 메시지는 무시하고 계속, stop_event 협조 종료
    - AlarmWorker._build_sse_publisher 게이트/플래그/Redis 부재에 따른 None/발행기 분기 (③)
    - 워커 None-경로 end-to-end: notifier가 sse_publisher로 redis.publish까지 도달

redis는 unittest.mock.AsyncMock으로 모킹한다(라이브 Redis 불요).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_notifier import (
    _tier_sse_payload,
    alarm_notifier_node,
)
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_TICKET,
)
from noise_gate.infrastructure.sse_bridge import (
    RedisSseBridgePublisher,
    run_sse_bridge_subscriber,
)

CHANNEL = "alarm:sse"
REF = datetime(2026, 6, 30, 9, 0, 0)


def _make_result() -> AlarmAnalysisResult:
    event = AlarmEvent(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h-1", ip_address="10.0.0.1",
        resource_ancestry="/Servers/svr/Cpus", alarm_id="A-1", severity=2, alarm_status="NOT_ACK",
        resource_type="server.Cpus", resource_name="svr-1-CPU", alarm_name="CPU 임계",
        alarm_time=REF, conditions="", condition_log="", is_clear=False,
    )
    return AlarmAnalysisResult(
        alarm_event=event, severity_label="경고", summary="s",
        probable_cause="c", recommended_action="a", notification_channels=["workb"],
    )


def _make_decision(tier: str) -> NotificationDecision:
    return NotificationDecision(
        tier=tier, reason="테스트", priority=300, signals={"severity": 2}, fingerprint="fp"
    )


# ─── ① RedisSseBridgePublisher.publish ──────────────────────────────────────

async def test_publisher_publishes_json_to_channel():
    redis = AsyncMock()
    pub = RedisSseBridgePublisher(redis, CHANNEL)
    event = {"type": "alarm_notification", "tier": TIER_TICKET, "msg": "한글"}

    await pub.publish(event)

    redis.publish.assert_awaited_once()
    args = redis.publish.await_args.args
    assert args[0] == CHANNEL                 # 채널명
    assert json.loads(args[1]) == event       # JSON round-trip
    assert "한글" in args[1]                   # ensure_ascii=False (한글 보존)


async def test_publisher_serializes_non_json_with_default_str():
    redis = AsyncMock()
    pub = RedisSseBridgePublisher(redis, CHANNEL)
    # datetime 등 비직렬화 값도 default=str로 방어되어 예외 없이 발행된다.
    await pub.publish({"captured_at": REF})
    redis.publish.assert_awaited_once()
    assert "2026-06-30" in redis.publish.await_args.args[1]


# ─── ④ publish 실패 graceful ────────────────────────────────────────────────

async def test_publisher_graceful_on_redis_error():
    redis = AsyncMock()
    redis.publish.side_effect = RuntimeError("redis down")
    pub = RedisSseBridgePublisher(redis, CHANNEL)
    # 예외가 전파되지 않아야 한다(파이프라인 차단 금지).
    await pub.publish({"tier": TIER_TICKET})  # 예외 없이 반환되면 성공


# ─── ② run_sse_bridge_subscriber 재팬아웃 + 종료 ────────────────────────────

def _mock_redis_with_pubsub(get_message_side_effect) -> tuple[MagicMock, AsyncMock]:
    """pubsub.get_message에 side_effect를 부착한 mock redis/pubsub을 만든다."""
    pubsub = AsyncMock()
    pubsub.get_message.side_effect = get_message_side_effect
    redis = MagicMock()
    redis.pubsub.return_value = pubsub     # .pubsub()은 동기 호출
    return redis, pubsub


async def test_subscriber_fanouts_message_to_bus():
    payload = {"type": "alarm_notification", "tier": TIER_DASHBOARD}
    message = {"type": "message", "channel": CHANNEL, "data": json.dumps(payload)}
    redis, pubsub = _mock_redis_with_pubsub([message, asyncio.CancelledError()])
    bus = AsyncMock()

    await run_sse_bridge_subscriber(redis, CHANNEL, bus)

    pubsub.subscribe.assert_awaited_once_with(CHANNEL)
    bus.publish.assert_awaited_once_with(payload)        # json round-trip 재팬아웃
    pubsub.unsubscribe.assert_awaited_once_with(CHANNEL)  # 종료 시 구독 정리
    pubsub.aclose.assert_awaited_once()


async def test_subscriber_ignores_none_and_continues_on_bad_message():
    good = {"type": "message", "data": json.dumps({"ok": 1})}
    bad = {"type": "message", "data": "NOT JSON"}
    redis, pubsub = _mock_redis_with_pubsub(
        [None, bad, good, asyncio.CancelledError()]
    )
    bus = AsyncMock()

    await run_sse_bridge_subscriber(redis, CHANNEL, bus)

    # None은 건너뛰고, 손상 메시지는 무시하고 계속 → 정상 1건만 재팬아웃
    bus.publish.assert_awaited_once_with({"ok": 1})


async def test_subscriber_stops_on_stop_event():
    stop = asyncio.Event()
    stop.set()                                 # 사전 set → 즉시 종료
    redis, pubsub = _mock_redis_with_pubsub([])
    bus = AsyncMock()

    await run_sse_bridge_subscriber(redis, CHANNEL, bus, stop_event=stop)

    pubsub.get_message.assert_not_awaited()    # 루프 진입 직후 break
    bus.publish.assert_not_awaited()
    pubsub.unsubscribe.assert_awaited_once_with(CHANNEL)


# ─── ③ AlarmWorker._build_sse_publisher 분기 ────────────────────────────────

def _worker_cfg(*, gate: bool, bridge: bool, channel: str = CHANNEL) -> SimpleNamespace:
    return SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=gate,
            sse_bridge_enabled=bridge,
            sse_bridge_channel=channel,
        )
    )


def test_build_sse_publisher_none_when_gate_off():
    worker = AlarmWorker(_worker_cfg(gate=False, bridge=True))
    worker._redis = AsyncMock()
    assert worker._build_sse_publisher() is None


def test_build_sse_publisher_none_when_bridge_flag_off():
    # 게이트 on이어도 sse_bridge_enabled=False면 미주입(E3 무변경 — ③)
    worker = AlarmWorker(_worker_cfg(gate=True, bridge=False))
    worker._redis = AsyncMock()
    assert worker._build_sse_publisher() is None


def test_build_sse_publisher_none_without_redis():
    worker = AlarmWorker(_worker_cfg(gate=True, bridge=True))
    worker._redis = None                       # Redis 부재 → None (graceful)
    assert worker._build_sse_publisher() is None


def test_build_sse_publisher_returns_publisher_when_enabled():
    worker = AlarmWorker(_worker_cfg(gate=True, bridge=True))
    worker._redis = AsyncMock()
    pub = worker._build_sse_publisher()
    assert isinstance(pub, RedisSseBridgePublisher)
    assert pub._channel == CHANNEL


# ─── ① end-to-end: 워커 None-경로 → notifier → redis.publish 도달 ──────────

def _config_worker_path(sse_publisher) -> dict:  # noqa: ANN001
    """워커 경로 configurable — alarm_bus 미주입, sse_publisher만 주입."""
    return {"configurable": {
        "app_config": SimpleNamespace(workb=SimpleNamespace(), alarm=SimpleNamespace()),
        "ticket_queue": None,
        "alarm_bus": None,
        "sse_publisher": sse_publisher,
    }}


async def test_worker_path_ticket_reaches_redis_publish():
    redis = AsyncMock()
    publisher = RedisSseBridgePublisher(redis, CHANNEL)
    result = _make_result()
    decision = _make_decision(TIER_TICKET)
    state = {"analysis_result": result, "notification_decision": decision}

    await alarm_notifier_node(state, _config_worker_path(publisher))

    redis.publish.assert_awaited_once()
    channel, data = redis.publish.await_args.args
    assert channel == CHANNEL
    # notifier가 보낸 payload는 _tier_sse_payload 결과와 동일(json round-trip)
    assert json.loads(data) == _tier_sse_payload(result, decision)
    assert json.loads(data)["tier"] == TIER_TICKET


async def test_worker_path_dashboard_reaches_redis_publish():
    redis = AsyncMock()
    publisher = RedisSseBridgePublisher(redis, CHANNEL)
    result = _make_result()
    decision = _make_decision(TIER_DASHBOARD)
    state = {"analysis_result": result, "notification_decision": decision}

    await alarm_notifier_node(state, _config_worker_path(publisher))

    redis.publish.assert_awaited_once()
    assert json.loads(redis.publish.await_args.args[1])["tier"] == TIER_DASHBOARD
