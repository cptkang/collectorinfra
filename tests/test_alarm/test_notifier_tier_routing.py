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

    async def fake_workb(cfg, result, snap=None):
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
