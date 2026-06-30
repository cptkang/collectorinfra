"""워커/노티파이어 incident 발행 테스트 (D-049).

- AlarmWorker._build_incident_publisher: 게이트/트래커 플래그/Redis 부재 분기
- AlarmWorker._publish_incident_resolved: resolved 이벤트 payload(self_heal|clear), 미주입 스킵
- alarm_notifier_node: PAGE 결정 시 open 발행, 비PAGE/None/미주입 시 스킵(workb 무변경)
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import src.alarm.application.nodes.alarm_notifier as notifier_mod
from src.alarm.application.alarm_worker import AlarmWorker
from src.alarm.application.nodes.alarm_notifier import alarm_notifier_node
from src.alarm.domain.alarm import AlarmAnalysisResult, AlarmEvent
from src.alarm.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_PAGE,
)
from src.alarm.infrastructure.incident_events import RedisIncidentPublisher

CHANNEL = "alarm:incident"
REF = datetime(2026, 6, 30, 9, 0, 0)


# ─── _build_incident_publisher 분기 ──────────────────────────────────────────

def _worker_cfg(*, gate: bool, tracking: bool, channel: str = CHANNEL) -> SimpleNamespace:
    return SimpleNamespace(noise_gate=SimpleNamespace(
        enable_noise_gate=gate,
        incident_tracking_enabled=tracking,
        incident_event_channel=channel,
    ))


def test_build_incident_publisher_none_when_gate_off():
    w = AlarmWorker(_worker_cfg(gate=False, tracking=True))
    w._redis = AsyncMock()
    assert w._build_incident_publisher() is None


def test_build_incident_publisher_none_when_tracking_off():
    w = AlarmWorker(_worker_cfg(gate=True, tracking=False))
    w._redis = AsyncMock()
    assert w._build_incident_publisher() is None


def test_build_incident_publisher_none_without_redis():
    w = AlarmWorker(_worker_cfg(gate=True, tracking=True))
    w._redis = None
    assert w._build_incident_publisher() is None


def test_build_incident_publisher_returns_publisher_when_enabled():
    w = AlarmWorker(_worker_cfg(gate=True, tracking=True))
    w._redis = AsyncMock()
    pub = w._build_incident_publisher()
    assert isinstance(pub, RedisIncidentPublisher)
    assert pub._channel == CHANNEL


# ─── _publish_incident_resolved ──────────────────────────────────────────────

def _event() -> SimpleNamespace:
    return SimpleNamespace(
        alarm_id="A-1", db_id="polestar", server_name="srv-1", severity=0,
    )


async def test_publish_resolved_self_heal_payload():
    w = AlarmWorker(_worker_cfg(gate=True, tracking=True))
    pub = AsyncMock()
    w._incident_publisher = pub
    await w._publish_incident_resolved(_event(), "fp", self_heal=True)

    pub.publish.assert_awaited_once()
    payload = pub.publish.await_args.args[0]
    assert payload["type"] == "resolved"
    assert payload["fingerprint"] == "fp"
    assert payload["resolution"] == "self_heal"


async def test_publish_resolved_clear_payload():
    w = AlarmWorker(_worker_cfg(gate=True, tracking=True))
    pub = AsyncMock()
    w._incident_publisher = pub
    await w._publish_incident_resolved(_event(), "fp", self_heal=False)
    assert pub.publish.await_args.args[0]["resolution"] == "clear"


async def test_publish_resolved_skips_when_no_publisher():
    w = AlarmWorker(_worker_cfg(gate=True, tracking=True))
    w._incident_publisher = None
    # 예외 없이 반환되면 성공(트래커 off → 발행 스킵, 회귀 0)
    await w._publish_incident_resolved(_event(), "fp", self_heal=True)


# ─── notifier open 발행 ──────────────────────────────────────────────────────

def _make_result() -> AlarmAnalysisResult:
    event = AlarmEvent(
        db_id="polestar", server_name="srv-1", hostname="h-1", ip_address="10.0.0.1",
        resource_ancestry="/Servers", alarm_id="A-1", severity=2, alarm_status="NOT_ACK",
        resource_type="server.Cpus", resource_name="svr-1-CPU", alarm_name="CPU 임계",
        alarm_time=REF, conditions="", condition_log="", is_clear=False,
    )
    return AlarmAnalysisResult(
        alarm_event=event, severity_label="경고", summary="s",
        probable_cause="c", recommended_action="a", notification_channels=["workb"],
    )


def _decision(tier: str) -> NotificationDecision:
    return NotificationDecision(
        tier=tier, reason="t", priority=320, signals={"severity": 2}, fingerprint="fp-x",
    )


def _patch_senders(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def fake_workb(cfg, result, snap=None):  # noqa: ANN001
        calls.append("workb")

    async def fake_webhook(cfg, result, snap=None):  # noqa: ANN001
        calls.append("webhook")

    monkeypatch.setattr(notifier_mod, "_send_workb", fake_workb)
    monkeypatch.setattr(notifier_mod, "_send_webhook", fake_webhook)
    return calls


def _config(incident_publisher=None) -> dict:  # noqa: ANN001
    return {"configurable": {
        "app_config": SimpleNamespace(workb=SimpleNamespace(), alarm=SimpleNamespace()),
        "incident_publisher": incident_publisher,
    }}


async def test_page_decision_publishes_open(monkeypatch):
    calls = _patch_senders(monkeypatch)
    pub = AsyncMock()
    state = {"analysis_result": _make_result(), "notification_decision": _decision(TIER_PAGE)}
    await alarm_notifier_node(state, _config(incident_publisher=pub))

    # open 발행 + 기존 workb 발송 모두 수행
    pub.publish.assert_awaited_once()
    payload = pub.publish.await_args.args[0]
    assert payload["type"] == "open"
    assert payload["fingerprint"] == "fp-x"
    assert payload["alarm_id"] == "A-1"
    assert payload["tier"] == TIER_PAGE
    assert payload["priority"] == "320"           # int → str
    assert calls == ["workb"]


async def test_page_decision_without_publisher_skips_open_keeps_workb(monkeypatch):
    # 트래커 off(incident_publisher=None) → open 발행 스킵, workb 그대로(회귀 0)
    calls = _patch_senders(monkeypatch)
    state = {"analysis_result": _make_result(), "notification_decision": _decision(TIER_PAGE)}
    out = await alarm_notifier_node(state, _config(incident_publisher=None))
    assert calls == ["workb"]
    assert out["analysis_result"].notifications_sent == {"workb": True}


async def test_non_page_decision_does_not_publish_open(monkeypatch):
    _patch_senders(monkeypatch)
    pub = AsyncMock()
    state = {"analysis_result": _make_result(), "notification_decision": _decision(TIER_DASHBOARD)}
    await alarm_notifier_node(state, _config(incident_publisher=pub))
    pub.publish.assert_not_awaited()              # DASHBOARD는 incident 아님


async def test_decision_none_does_not_publish_open(monkeypatch):
    # 게이트 off(notification_decision 없음) → open 발행 없음(회귀 0)
    calls = _patch_senders(monkeypatch)
    pub = AsyncMock()
    state = {"analysis_result": _make_result()}    # notification_decision 키 없음
    await alarm_notifier_node(state, _config(incident_publisher=pub))
    pub.publish.assert_not_awaited()
    assert calls == ["workb"]


async def test_page_open_publish_failure_is_graceful(monkeypatch):
    # open 발행 실패가 workb 발송을 막지 않는다(graceful)
    calls = _patch_senders(monkeypatch)
    pub = AsyncMock()
    pub.publish.side_effect = RuntimeError("redis down")
    state = {"analysis_result": _make_result(), "notification_decision": _decision(TIER_PAGE)}
    out = await alarm_notifier_node(state, _config(incident_publisher=pub))
    assert calls == ["workb"]                      # 발송은 정상 진행
    assert out["analysis_result"].notifications_sent == {"workb": True}
