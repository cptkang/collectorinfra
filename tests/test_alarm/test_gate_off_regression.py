"""게이트 비활성(enable_noise_gate=False) 회귀 0 검증 (Plan 52 ⓓ).

게이트가 꺼져 있을 때 (1) 그래프에 notification_gate 노드가 없고, (2) alarm_notifier_node 가
notification_decision 없이 기존 채널 발송 경로를 그대로 타며, (3) 워커 dedup 이 alarm_id 기반으로
유지됨을 검증한다. 즉 게이트 도입 전과 동작이 무변경이어야 한다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import src.alarm.application.nodes.alarm_notifier as notifier_mod
from src.alarm.application.alarm_worker import AlarmWorker
from src.alarm.application.nodes.alarm_notifier import alarm_notifier_node
from src.alarm.domain.alarm import AlarmAnalysisResult, AlarmEvent
from src.alarm.orchestration.alarm_graph import build_alarm_graph

REF = datetime(2026, 6, 29, 14, 0, 0)


def make_event(**kwargs) -> AlarmEvent:
    defaults = dict(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h-1", ip_address="10.0.0.1",
        resource_ancestry="/Servers/svr/Cpus", alarm_id="A-1", severity=2, alarm_status="NOT_ACK",
        resource_type="server.Cpus", resource_name="svr-1-CPU", alarm_name="CPU 임계",
        alarm_time=REF, conditions="", condition_log="",
    )
    defaults.update(kwargs)
    if "is_clear" not in defaults:
        defaults["is_clear"] = defaults["severity"] == 0
    return AlarmEvent(**defaults)


def make_result(channels: list[str]) -> AlarmAnalysisResult:
    return AlarmAnalysisResult(
        alarm_event=make_event(),
        severity_label="경고",
        summary="요약",
        probable_cause="원인",
        recommended_action="조치",
        notification_channels=channels,
    )


def make_gate_off_config() -> SimpleNamespace:
    return SimpleNamespace(
        alarm=SimpleNamespace(history_enabled=True),
        noise_gate=SimpleNamespace(enable_noise_gate=False),
    )


class TestGraphHasNoGateNode:
    def test_no_gate_node_when_off(self):
        g = build_alarm_graph(make_gate_off_config())
        nodes = set(g.get_graph().nodes.keys())
        assert "notification_gate" not in nodes
        # 게이트 off 시 노드 집합은 기존(enricher/analyzer/notifier)과 동일
        assert {"alarm_context_enricher", "alarm_analyzer", "alarm_notifier"} <= nodes

    def test_no_gate_node_history_off(self):
        cfg = SimpleNamespace(
            alarm=SimpleNamespace(history_enabled=False),
            noise_gate=SimpleNamespace(enable_noise_gate=False),
        )
        nodes = set(build_alarm_graph(cfg).get_graph().nodes.keys())
        assert nodes == {"__start__", "__end__", "alarm_analyzer", "alarm_notifier"}


class TestNotifierWithoutDecision:
    """notification_decision 이 없으면(게이트 off) 기존 발송 경로 그대로 — 채널 모두 발송."""

    async def test_sends_all_channels_when_decision_absent(self, monkeypatch):
        calls: list[str] = []

        async def fake_workb(cfg, result, snap=None, **kwargs):
            calls.append("workb")

        async def fake_webhook(cfg, result, snap=None):
            calls.append("webhook")

        monkeypatch.setattr(notifier_mod, "_send_workb", fake_workb)
        monkeypatch.setattr(notifier_mod, "_send_webhook", fake_webhook)

        result = make_result(["workb", "webhook"])
        state = {
            "analysis_result": result,
            "process_snapshot": None,
            # notification_decision 키 자체를 두지 않음 → state.get(...) == None (게이트 off 경로)
        }
        config = {"configurable": {"app_config": SimpleNamespace(
            workb=SimpleNamespace(), alarm=SimpleNamespace())}}

        out = await alarm_notifier_node(state, config)

        assert calls == ["workb", "webhook"]
        assert out["analysis_result"].notifications_sent == {"workb": True, "webhook": True}

    async def test_explicit_none_decision_uses_send_path(self, monkeypatch):
        # decision=None 분기도 발송 경로(보수적)임을 명시적으로 확인
        calls: list[str] = []

        async def fake_workb(cfg, result, snap=None, **kwargs):
            calls.append("workb")

        monkeypatch.setattr(notifier_mod, "_send_workb", fake_workb)

        result = make_result(["workb"])
        state = {"analysis_result": result, "notification_decision": None}
        config = {"configurable": {"app_config": SimpleNamespace(
            workb=SimpleNamespace(), alarm=SimpleNamespace())}}

        await alarm_notifier_node(state, config)
        assert calls == ["workb"]


class TestWorkerAlarmIdDedup:
    """게이트 off 경로의 dedup 은 alarm_id 기반(_is_duplicate)으로 유지된다."""

    def _worker(self) -> AlarmWorker:
        cfg = SimpleNamespace(alarm=SimpleNamespace(dedup_ttl_seconds=300))
        return AlarmWorker(cfg)

    def test_same_alarm_id_is_duplicate(self):
        worker = self._worker()
        dedup: dict[str, float] = {}
        ev = make_event(alarm_id="A-1")
        assert worker._is_duplicate(ev, dedup) is False  # 첫 처리
        assert worker._is_duplicate(ev, dedup) is True   # TTL 내 재처리 → 중복

    def test_different_alarm_id_not_duplicate(self):
        worker = self._worker()
        dedup: dict[str, float] = {}
        assert worker._is_duplicate(make_event(alarm_id="A-1"), dedup) is False
        assert worker._is_duplicate(make_event(alarm_id="A-2"), dedup) is False
