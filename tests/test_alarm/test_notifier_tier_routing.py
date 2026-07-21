"""alarm_notifier_node 4-티어 라우팅 통합 테스트 (Plan 52 §7).

notification_decision 의 tier 에 따라:
    - PAGE → 기존 발송 경로(_send_workb/_send_webhook) 호출
    - TICKET/DASHBOARD/SUPPRESS → 발송 함수 호출 안 함(0회), 예외 없이 반환
을 monkeypatch 호출 캡처로 검증한다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import src.alarm.application.nodes.alarm_notifier as notifier_mod
from src.alarm.application.nodes.alarm_notifier import alarm_notifier_node
from src.alarm.domain.alarm import AlarmAnalysisResult, AlarmEvent
from src.alarm.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)

REF = datetime(2026, 6, 29, 14, 0, 0)


def make_result() -> AlarmAnalysisResult:
    event = AlarmEvent(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h-1", ip_address="10.0.0.1",
        resource_ancestry="/Servers/svr/Cpus", alarm_id="A-1", severity=2, alarm_status="NOT_ACK",
        resource_type="server.Cpus", resource_name="svr-1-CPU", alarm_name="CPU 임계",
        alarm_time=REF, conditions="", condition_log="", is_clear=False,
    )
    return AlarmAnalysisResult(
        alarm_event=event, severity_label="경고", summary="s",
        probable_cause="c", recommended_action="a",
        notification_channels=["workb", "webhook"],
    )


def make_decision(tier: str) -> NotificationDecision:
    return NotificationDecision(
        tier=tier, reason="테스트", priority=300,
        signals={"severity": 2}, fingerprint="fp",
    )


def _patch_senders(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def fake_workb(cfg, result, snap=None, **kwargs):
        calls.append("workb")

    async def fake_webhook(cfg, result, snap=None):
        calls.append("webhook")

    monkeypatch.setattr(notifier_mod, "_send_workb", fake_workb)
    monkeypatch.setattr(notifier_mod, "_send_webhook", fake_webhook)
    return calls


def _config() -> dict:
    return {"configurable": {"app_config": SimpleNamespace(
        workb=SimpleNamespace(), alarm=SimpleNamespace())}}


async def test_page_tier_sends_both_channels(monkeypatch):
    calls = _patch_senders(monkeypatch)
    result = make_result()
    state = {"analysis_result": result, "notification_decision": make_decision(TIER_PAGE)}
    out = await alarm_notifier_node(state, _config())
    assert calls == ["workb", "webhook"]
    assert out["analysis_result"].notifications_sent == {"workb": True, "webhook": True}


async def test_ticket_tier_does_not_send(monkeypatch):
    calls = _patch_senders(monkeypatch)
    state = {"analysis_result": make_result(), "notification_decision": make_decision(TIER_TICKET)}
    out = await alarm_notifier_node(state, _config())
    assert calls == []  # 발송 함수 미호출
    assert out["analysis_result"] is state["analysis_result"]  # 예외 없이 반환


async def test_dashboard_tier_does_not_send(monkeypatch):
    calls = _patch_senders(monkeypatch)
    state = {"analysis_result": make_result(), "notification_decision": make_decision(TIER_DASHBOARD)}
    out = await alarm_notifier_node(state, _config())
    assert calls == []
    assert "analysis_result" in out


async def test_suppress_tier_does_not_send(monkeypatch):
    calls = _patch_senders(monkeypatch)
    state = {"analysis_result": make_result(), "notification_decision": make_decision(TIER_SUPPRESS)}
    out = await alarm_notifier_node(state, _config())
    assert calls == []
    assert "analysis_result" in out


async def test_non_page_tiers_leave_notifications_sent_empty(monkeypatch):
    # 발송하지 않으므로 notifications_sent 가 비어 있어야 한다(채널별 결과 미기록)
    _patch_senders(monkeypatch)
    for tier in (TIER_TICKET, TIER_DASHBOARD, TIER_SUPPRESS):
        result = make_result()
        state = {"analysis_result": result, "notification_decision": make_decision(tier)}
        await alarm_notifier_node(state, _config())
        assert result.notifications_sent == {}, f"{tier} 는 발송 기록이 없어야 함"


# ─── Phase E3: TICKET 큐 적재 + DASHBOARD/TICKET SSE 라우팅 ─────────────────

class _RecordingQueue:
    """ticket_queue 대역 — enqueue 호출을 수집한다."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def enqueue(self, decision, *, alarm_id="") -> None:  # noqa: ANN001
        self.calls.append((decision, alarm_id))


class _RecordingBus:
    """alarm_bus 대역 — publish된 SSE payload를 수집한다."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.events.append(event)


def _config_with(ticket_queue=None, alarm_bus=None) -> dict:  # noqa: ANN001
    return {"configurable": {
        "app_config": SimpleNamespace(workb=SimpleNamespace(), alarm=SimpleNamespace()),
        "ticket_queue": ticket_queue,
        "alarm_bus": alarm_bus,
    }}


async def test_ticket_tier_enqueues_and_publishes_sse(monkeypatch):
    calls = _patch_senders(monkeypatch)
    queue, bus = _RecordingQueue(), _RecordingBus()
    result = make_result()
    state = {"analysis_result": result, "notification_decision": make_decision(TIER_TICKET)}
    await alarm_notifier_node(state, _config_with(queue, bus))

    assert calls == []                       # 외부 발송(PAGE) 안 함
    assert len(queue.calls) == 1             # 일배치 큐 적재 1회
    assert queue.calls[0][1] == "A-1"        # alarm_id 전달
    assert len(bus.events) == 1              # SSE 표시 1회
    assert bus.events[0]["tier"] == TIER_TICKET
    assert bus.events[0]["tier_reason"] == "테스트"


async def test_dashboard_tier_publishes_sse_no_enqueue(monkeypatch):
    _patch_senders(monkeypatch)
    queue, bus = _RecordingQueue(), _RecordingBus()
    state = {"analysis_result": make_result(), "notification_decision": make_decision(TIER_DASHBOARD)}
    await alarm_notifier_node(state, _config_with(queue, bus))

    assert queue.calls == []                 # DASHBOARD는 큐 미적재
    assert len(bus.events) == 1              # SSE 표시
    assert bus.events[0]["tier"] == TIER_DASHBOARD


async def test_suppress_tier_no_queue_no_sse(monkeypatch):
    _patch_senders(monkeypatch)
    queue, bus = _RecordingQueue(), _RecordingBus()
    state = {"analysis_result": make_result(), "notification_decision": make_decision(TIER_SUPPRESS)}
    await alarm_notifier_node(state, _config_with(queue, bus))

    assert queue.calls == []                 # SUPPRESS는 큐·SSE 모두 없음
    assert bus.events == []


async def test_ticket_without_bus_still_enqueues(monkeypatch):
    # 워커 경로(bus 미주입) — 큐 적재는 동작, SSE는 로그 폴백(예외 없음)
    _patch_senders(monkeypatch)
    queue = _RecordingQueue()
    state = {"analysis_result": make_result(), "notification_decision": make_decision(TIER_TICKET)}
    await alarm_notifier_node(state, _config_with(queue, alarm_bus=None))
    assert len(queue.calls) == 1


async def test_page_tier_does_not_touch_queue_or_bus(monkeypatch):
    calls = _patch_senders(monkeypatch)
    queue, bus = _RecordingQueue(), _RecordingBus()
    state = {"analysis_result": make_result(), "notification_decision": make_decision(TIER_PAGE)}
    await alarm_notifier_node(state, _config_with(queue, bus))

    assert calls == ["workb", "webhook"]     # 기존 발송 경로
    assert queue.calls == []
    assert bus.events == []


async def test_decision_none_unchanged_send_path(monkeypatch):
    # 게이트 off(notification_decision 없음) → 기존 발송 경로 무변경, 큐/SSE 미사용(회귀 0)
    calls = _patch_senders(monkeypatch)
    queue, bus = _RecordingQueue(), _RecordingBus()
    state = {"analysis_result": make_result()}  # notification_decision 키 없음
    out = await alarm_notifier_node(state, _config_with(queue, bus))

    assert calls == ["workb", "webhook"]
    assert queue.calls == []
    assert bus.events == []
    assert out["analysis_result"].notifications_sent == {"workb": True, "webhook": True}


# ─── E3 후속: 워커 경로 SSE Redis 브리지 발행기(sse_publisher) ────────────────


class _RecordingPublisher:
    """sse_publisher(RedisSseBridgePublisher) 대역 — publish된 payload를 수집한다."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.events.append(event)


def _config_bridge(*, ticket_queue=None, alarm_bus=None, sse_publisher=None) -> dict:  # noqa: ANN001
    return {"configurable": {
        "app_config": SimpleNamespace(workb=SimpleNamespace(), alarm=SimpleNamespace()),
        "ticket_queue": ticket_queue,
        "alarm_bus": alarm_bus,
        "sse_publisher": sse_publisher,
    }}


async def test_worker_path_ticket_uses_sse_publisher(monkeypatch):
    # 워커 경로(alarm_bus=None) → sse_publisher로 SSE 발행(payload=_tier_sse_payload)
    _patch_senders(monkeypatch)
    pub = _RecordingPublisher()
    decision = make_decision(TIER_TICKET)
    state = {"analysis_result": make_result(), "notification_decision": decision}
    await alarm_notifier_node(state, _config_bridge(sse_publisher=pub))

    assert len(pub.events) == 1
    assert pub.events[0]["tier"] == TIER_TICKET
    assert pub.events[0]["tier_reason"] == "테스트"


async def test_worker_path_dashboard_uses_sse_publisher(monkeypatch):
    _patch_senders(monkeypatch)
    pub = _RecordingPublisher()
    state = {"analysis_result": make_result(), "notification_decision": make_decision(TIER_DASHBOARD)}
    await alarm_notifier_node(state, _config_bridge(sse_publisher=pub))

    assert len(pub.events) == 1
    assert pub.events[0]["tier"] == TIER_DASHBOARD


async def test_double_publish_prevention_bus_wins(monkeypatch):
    # ⑤ alarm_bus + sse_publisher 동시 주입 → alarm_bus만 호출(sse_publisher 미호출)
    _patch_senders(monkeypatch)
    bus, pub = _RecordingBus(), _RecordingPublisher()
    state = {"analysis_result": make_result(), "notification_decision": make_decision(TIER_TICKET)}
    await alarm_notifier_node(state, _config_bridge(alarm_bus=bus, sse_publisher=pub))

    assert len(bus.events) == 1          # API 경로 버스 우선
    assert pub.events == []              # 브리지는 이중 발행하지 않음


async def test_worker_path_no_publisher_log_fallback(monkeypatch):
    # ③ 플래그 off(sse_publisher=None, alarm_bus=None) → 발행 대상 없음, 예외 없이 로그 폴백
    _patch_senders(monkeypatch)
    queue = _RecordingQueue()
    state = {"analysis_result": make_result(), "notification_decision": make_decision(TIER_TICKET)}
    out = await alarm_notifier_node(
        state, _config_bridge(ticket_queue=queue, alarm_bus=None, sse_publisher=None)
    )
    # 큐 적재는 동작(워커 무변경), SSE는 로그 폴백 — 예외 없이 정상 반환
    assert len(queue.calls) == 1
    assert "analysis_result" in out
