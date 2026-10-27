"""plans/194 W5 · Q-1 — 수동 전용이면 PAGE 통보에만 「자동 조사 꺼짐」 안내 한 줄.

사용자 결정(2026-10-26 · D-347): 수동 전용(게이트 on ∧ 자동 조사 off ∧ 수동 접수 on)일 때 PAGE
통보 본문 끝에 안내 한 줄을 붙이고, 웹 base url이 있으면 「웹 알람 카드」를 포털 링크로 만든다.
그 밖의 경우(자동 조사 켜짐 · PAGE 아님 · 조사 서비스 URL 없음 · 게이트 off)는 발송 호출·본문이
종전과 같다. 실 발송 0 — `_send_workb`를 가짜로 바꾼다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from noise_gate.application.nodes import alarm_notifier as notifier_mod
from noise_gate.application.nodes.alarm_notifier import (
    MANUAL_ONLY_NOTICE_TEXT,
    build_workb_body,
    manual_only_mode,
    manual_only_notice_html,
)
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.notification_policy import TIER_PAGE, TIER_TICKET, NotificationDecision

BASE = "https://aiops.example.internal"


def _result() -> AlarmAnalysisResult:
    ev = AlarmEvent(
        db_id="db1", server_name="srv-1", hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/root/srv-1", alarm_id="A-1", severity=3,
        alarm_status="NOT_ACK", resource_type="was.Instance", resource_name="r1",
        alarm_name="WAS 응답 지연", alarm_time=datetime(2026, 10, 26, 9, 0, 0),
        conditions=">3000ms", condition_log="p95 3500ms",
    )
    return AlarmAnalysisResult(
        alarm_event=ev, severity_label="심각", summary="s", probable_cause="c",
        recommended_action="a", notification_channels=["workb"],
    )


def _gate(**over) -> SimpleNamespace:
    base = dict(
        enable_noise_gate=True,
        investigation_trigger_enabled=False,
        investigation_manual_enabled=True,
        investigation_service_url="http://127.0.0.1:9/sse",
        investigation_web_base_url=BASE,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _decision(tier: str = TIER_PAGE) -> NotificationDecision:
    return NotificationDecision(
        tier=tier, reason="심각도3", priority=3, signals={"root_resource": None},
        fingerprint="fp-1",
    )


# ── 판정 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "over, expected",
    [
        ({}, True),
        ({"investigation_trigger_enabled": True}, False),
        ({"investigation_manual_enabled": False}, False),
        ({"enable_noise_gate": False}, False),
        ({"investigation_service_url": ""}, False),  # 웹 카드에 [조사 시작]이 없다
    ],
)
def test_manual_only_mode(over, expected) -> None:  # noqa: ANN001
    assert manual_only_mode(_gate(**over)) is expected


def test_manual_only_mode_without_gate_config() -> None:
    assert manual_only_mode(None) is False


# ── 문구 ────────────────────────────────────────────────────────────────
def test_notice_links_web_alarm_card_when_base_given() -> None:
    html = manual_only_notice_html(BASE + "/")
    assert html.startswith("<br><br>자동 조사 꺼짐 — ")
    assert f'<a href="{BASE}/">웹 알람 카드</a>에서 [조사 시작]' in html


@pytest.mark.parametrize("base", ["", "javascript:alert(1)", "ftp://x"])
def test_notice_text_only_without_safe_base(base) -> None:  # noqa: ANN001
    html = manual_only_notice_html(base)
    assert html == f"<br><br>{MANUAL_ONLY_NOTICE_TEXT}"
    assert "<a " not in html


def test_body_appends_notice_and_none_is_unchanged() -> None:
    plain = build_workb_body(_result())
    assert build_workb_body(_result(), manual_only_notice=None) == plain
    notice = manual_only_notice_html(BASE)
    assert build_workb_body(_result(), manual_only_notice=notice) == plain + notice


# ── 노드 배선 ────────────────────────────────────────────────────────────
async def _run(monkeypatch, gate: SimpleNamespace, decision) -> list[dict]:  # noqa: ANN001
    calls: list[dict] = []

    async def fake_workb(cfg, result, snap=None, **kwargs):  # noqa: ANN001, ANN003, ANN202
        calls.append(kwargs)

    async def fake_incident(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return None

    monkeypatch.setattr(notifier_mod, "_send_workb", fake_workb)
    monkeypatch.setattr(notifier_mod, "_publish_incident_open", fake_incident)
    monkeypatch.setattr(notifier_mod, "_route_non_page_tier", fake_incident)
    state = {"analysis_result": _result(), "notification_decision": decision}
    config = {"configurable": {"app_config": SimpleNamespace(
        workb=SimpleNamespace(), alarm=SimpleNamespace(), noise_gate=gate,
    )}}
    await notifier_mod.alarm_notifier_node(state, config)
    return calls


async def test_page_in_manual_only_mode_carries_notice(monkeypatch) -> None:  # noqa: ANN001
    calls = await _run(monkeypatch, _gate(), _decision())
    assert len(calls) == 1
    assert calls[0]["manual_only_notice"] == manual_only_notice_html(BASE)


@pytest.mark.parametrize(
    "over",
    [
        {"investigation_trigger_enabled": True},
        {"investigation_manual_enabled": False},
        {"investigation_service_url": ""},
    ],
)
async def test_page_outside_manual_only_mode_is_unchanged(monkeypatch, over) -> None:  # noqa: ANN001
    calls = await _run(monkeypatch, _gate(**over), _decision())
    assert len(calls) == 1 and "manual_only_notice" not in calls[0]


async def test_non_page_tier_never_gets_notice(monkeypatch) -> None:  # noqa: ANN001
    # TICKET은 발송 경로가 아니다 — workb 호출 자체가 없다(안내도 없다).
    assert await _run(monkeypatch, _gate(), _decision(TIER_TICKET)) == []


async def test_gate_off_without_decision_is_unchanged(monkeypatch) -> None:  # noqa: ANN001
    calls = await _run(monkeypatch, _gate(), None)
    assert len(calls) == 1 and "manual_only_notice" not in calls[0]
