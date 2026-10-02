"""E3 동적 baseline → ai_message_severity 상향 가드 (analyzer 후처리) 테스트 (Plan 60 §11).

alarm_analyzer_node의 LLM 파싱 직후 **결정적 후처리**(LLM 무관)를 검증한다(§5.2 확정 설계):
  - 상향 가드: anomaly_severity가 event.severity 초과 AND 기존 ai 초과일 때만 반영.
  - AND 조건: dynamic_baseline_enabled & enable_ai_severity_boost 둘 다 True여야 상향.
  - max 유지: sig/llm 기존 ai와 공존 시 하향 불가·max 의미 유지.
inject된 state["anomaly_severity"]로 결정적으로 시험한다(LLM은 대역으로 고정).
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from noise_gate.application.nodes.alarm_analyzer import alarm_analyzer_node
from noise_gate.domain.alarm import AlarmEvent

REF = datetime(2026, 7, 22, 10, 0, 0)


def make_event(*, severity=1, resource_type="server.LogMonitor",
               condition_log="") -> AlarmEvent:
    return AlarmEvent(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h1",
        ip_address="10.0.0.1", resource_ancestry="/Servers/srv", alarm_id="A-1",
        severity=severity, alarm_status="NOT_ACK", resource_type=resource_type,
        resource_name="r1", alarm_name="알람", alarm_time=REF, conditions="",
        condition_log=condition_log, is_clear=(severity == 0),
    )


class _StubLLM:
    """고정 JSON을 반환하는 analyzer LLM 대역."""

    def __init__(self, response: dict) -> None:
        self._response = response

    async def ainvoke(self, messages, *args, **kwargs):
        return SimpleNamespace(content=json.dumps(self._response, ensure_ascii=False))


def _resp(*, ai_message_severity=None) -> dict:
    return {
        "severity_label": "주의",
        "summary": "요약",
        "probable_cause": "원인",
        "recommended_action": "조치",
        "pattern_type": "",
        "ai_message_severity": ai_message_severity,
    }


def _cfg(*, boost=True, dynamic=True) -> SimpleNamespace:
    alarm = SimpleNamespace(get_notification_channels=lambda: ["workb"])
    noise_gate = SimpleNamespace(
        enable_ai_severity_boost=boost,
        dynamic_baseline_enabled=dynamic,
        enable_llm_actionability=False,
    )
    return SimpleNamespace(alarm=alarm, noise_gate=noise_gate)


async def _run(monkeypatch, *, event, cfg, anomaly_severity, response):
    monkeypatch.setattr(
        "noise_gate.application.nodes.alarm_analyzer.create_llm",
        lambda cfg, **kw: _StubLLM(response),
    )
    state = {
        "alarm_event": event,
        "history_stats": None,
        "process_snapshot": None,
        "analysis_result": None,
        "error": None,
        "anomaly_severity": anomaly_severity,
    }
    out = await alarm_analyzer_node(state, {"configurable": {"app_config": cfg}})
    return out["analysis_result"]


class TestEscalateGuard:
    async def test_applied_when_candidate_exceeds_severity_and_existing_ai(self, monkeypatch):
        # event sev=1, 기존 ai 없음, anomaly=2 → 2>1 & 2>0 → 상향 반영.
        r = await _run(
            monkeypatch, event=make_event(severity=1), cfg=_cfg(),
            anomaly_severity=2, response=_resp(),
        )
        assert r.ai_message_severity == 2
        assert r.ai_severity_reason == "동적 baseline 이상탐지 (z-score 상향)"

    async def test_not_applied_when_candidate_not_above_severity(self, monkeypatch):
        # event sev=2, anomaly=2 → 2>2 거짓 → 미반영(하향/무변경, max 의미).
        r = await _run(
            monkeypatch, event=make_event(severity=2), cfg=_cfg(),
            anomaly_severity=2, response=_resp(),
        )
        assert r.ai_message_severity is None

    async def test_none_anomaly_noop(self, monkeypatch):
        r = await _run(
            monkeypatch, event=make_event(severity=1), cfg=_cfg(),
            anomaly_severity=None, response=_resp(),
        )
        assert r.ai_message_severity is None


class TestAndCondition:
    async def test_dynamic_off_no_escalation(self, monkeypatch):
        r = await _run(
            monkeypatch, event=make_event(severity=1), cfg=_cfg(dynamic=False),
            anomaly_severity=3, response=_resp(),
        )
        assert r.ai_message_severity is None  # dynamic_baseline_enabled=False → 무변경

    async def test_boost_off_no_escalation(self, monkeypatch):
        r = await _run(
            monkeypatch, event=make_event(severity=1), cfg=_cfg(boost=False),
            anomaly_severity=3, response=_resp(),
        )
        assert r.ai_message_severity is None  # enable_ai_severity_boost=False → 무변경


class TestMaxMaintainedWithExistingAi:
    async def test_candidate_not_above_existing_ai_kept(self, monkeypatch):
        # LLM ai_message_severity=2(기존 ai), anomaly=2 → 2>2 거짓 → 기존 2 유지(max).
        r = await _run(
            monkeypatch, event=make_event(severity=1, condition_log="로그 라인"),
            cfg=_cfg(), anomaly_severity=2, response=_resp(ai_message_severity=2),
        )
        assert r.ai_message_severity == 2

    async def test_candidate_exceeds_existing_ai_applied(self, monkeypatch):
        # 기존 ai=2(LLM)와 공존, anomaly=3 → 3>2 → 상향(max 의미 유지·상향 전용).
        r = await _run(
            monkeypatch, event=make_event(severity=1, condition_log="로그 라인"),
            cfg=_cfg(), anomaly_severity=3, response=_resp(ai_message_severity=2),
        )
        assert r.ai_message_severity == 3
        assert r.ai_severity_reason == "동적 baseline 이상탐지 (z-score 상향)"
