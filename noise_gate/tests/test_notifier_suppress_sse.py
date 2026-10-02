"""Plan 83 T9·T10 — SUPPRESS 티어 SSE 발행(옵트인)과 관리자 전용 전달.

현재 SUPPRESS는 로그만 남고 UI로 흐르지 않아, "잘못 억제한 알람"을 운영자가 감사 파일
tail로만 확인할 수 있다(docs/28 실측). 이 스위치는 그 경로를 UI로 여는 것이되,
**억제 알람 전문이 비관리자 브라우저에 도달해서는 안 된다**(권한은 서버가 판정한다).
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from noise_gate.application.nodes.alarm_notifier import _route_non_page_tier
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_SUPPRESS,
)


class _SpyBus:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, payload: dict) -> None:
        self.published.append(payload)


def _result() -> AlarmAnalysisResult:
    event = AlarmEvent(
        db_id="polestar_cm_gp", server_name="was01", hostname="was01",
        ip_address="10.0.0.1", resource_ancestry="", alarm_id="A1", severity=1,
        alarm_status="", resource_type="cpu", resource_name="cpu_usage",
        alarm_name="CPU 임계", alarm_time=datetime(2026, 8, 28, 10, 0, 0),
        conditions="", condition_log="", raw_payload={},
    )
    return AlarmAnalysisResult(
        alarm_event=event, severity_label="주의", summary="s",
        probable_cause="c", recommended_action="a", notification_channels=[],
    )


def _decision(tier: str) -> NotificationDecision:
    return NotificationDecision(
        tier=tier, reason="테스트", priority=100, signals={}, fingerprint="fp",
    )


@pytest.mark.asyncio
async def test_suppress_not_published_by_default():
    """기본(플래그 off)은 현행 그대로 — 발행 0(비트 동일)."""
    bus = _SpyBus()
    await _route_non_page_tier(_result(), _decision(TIER_SUPPRESS), None, bus, None)
    assert bus.published == []


@pytest.mark.asyncio
async def test_suppress_published_when_enabled():
    bus = _SpyBus()
    await _route_non_page_tier(
        _result(), _decision(TIER_SUPPRESS), None, bus, None, suppress_sse=True
    )
    assert len(bus.published) == 1
    assert bus.published[0]["tier"] == TIER_SUPPRESS


@pytest.mark.asyncio
async def test_dashboard_unaffected_by_flag():
    """DASHBOARD 경로는 플래그와 무관하게 종전대로 발행된다(회귀 0)."""
    for flag in (False, True):
        bus = _SpyBus()
        await _route_non_page_tier(
            _result(), _decision(TIER_DASHBOARD), None, bus, None, suppress_sse=flag
        )
        assert len(bus.published) == 1
        assert bus.published[0]["tier"] == TIER_DASHBOARD
