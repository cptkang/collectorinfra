"""해소(심각도 0) 알람은 AI 심각도 상향으로 PAGE 가 되지 않는다.

회귀 대상(2026-09-27 · 결함 ⑪): ``NOISE_ENABLE_AI_SEVERITY_BOOST`` 가 켜져 있으면 조건 로그에
3급 시그니처(예: OOM)가 든 **해소** 알람이 상향돼 실효 심각도 3 → 심각도3 단락(PAGE)에 걸렸다.
해소 알림은 해소·자가복구 단계로 가야 한다.

상향 원천 4종(결정적 시그니처 스캔 · LLM 메시지 해석 · 동적 baseline · agentic 보강)과 분석 실패
폴백(D-256) 경로를 각각 실제 노드로 돌려 결과를 게이트에 넣고, 어느 원천이든 해소 알람의 실효
심각도가 0 으로 남는지 확인한다. 발생 알람의 상향은 그대로인지도 함께 본다.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from noise_gate.application.nodes.agentic_enricher import agentic_enricher_node
from noise_gate.application.nodes.alarm_analyzer import alarm_analyzer_node
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.notification_policy import (
    STAGE_RESOLVED,
    STAGE_SELF_HEAL,
    STAGE_SEVERITY3,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    decide_notification,
)

REF = datetime(2026, 9, 27, 10, 0, 0)
OOM_LOG = "kernel: Out of memory: Killed process 42 (java)"  # 3급 시그니처
PLAIN_LOG = "service state changed"                          # 시그니처 없음


def make_event(*, severity: int = 0, condition_log: str = OOM_LOG) -> AlarmEvent:
    """메시지형 알람(비메트릭 자원). severity 0 이면 해소."""
    return AlarmEvent(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h1",
        ip_address="10.0.0.1", resource_ancestry="/Servers/srv/LogMonitor", alarm_id="A-1",
        severity=severity, alarm_status="NOT_ACK", resource_type="server.LogMonitor",
        resource_name="syslog", alarm_name="로그 패턴 감지", alarm_time=REF, conditions="",
        condition_log=condition_log, is_clear=(severity == 0),
    )


def make_cfg(*, resolved_to_dashboard: bool = False) -> SimpleNamespace:
    """상향 원천이 모두 켜진 구성(시그니처·LLM·동적 baseline·agentic 트랙 A)."""
    return SimpleNamespace(
        alarm=SimpleNamespace(get_notification_channels=lambda: ["workb"]),
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            enable_ai_severity_boost=True,
            dynamic_baseline_enabled=True,
            enable_llm_actionability=False,
            enable_agentic_enricher=True,
            agentic_enricher_message_alarms_only=True,
            agentic_enricher_fallback="semantic_routing",
            agentic_enricher_timeout_seconds=8.0,
            agentic_enricher_max_tool_calls=5,
            suppress_max_severity=2,
            importance_value_map={"HIGH": "높음", "MID": "보통", "LOW": "낮음"},
            resolved_to_dashboard=resolved_to_dashboard,
        ),
        orchestrator=SimpleNamespace(provider="vllm", base_url="", api_key="", health_timeout=1),
        llm=SimpleNamespace(gemini_api_key=""),
    )


NOISE_CTX = {
    "importance_id": "MID", "maintenance": False, "noti_policy": None,
    "parent_avail_status": None, "source": "polestar_db",
}


class _AnalyzerLLM:
    def __init__(self, ai_message_severity=None) -> None:  # noqa: ANN001
        self._ai = ai_message_severity

    async def ainvoke(self, messages, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return SimpleNamespace(content=json.dumps({
            "severity_label": "해소", "summary": "요약", "probable_cause": "원인",
            "recommended_action": "조치", "pattern_type": "", "ai_message_severity": self._ai,
        }, ensure_ascii=False))


class _RaisingLLM:
    async def ainvoke(self, messages, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise ConnectionError("llm endpoint unreachable")


class _TrackALLM:
    async def ainvoke(self, messages, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return SimpleNamespace(content='{"needed_signals": ["message_signature"]}')


def _plain_analysis(event: AlarmEvent) -> AlarmAnalysisResult:
    """상향이 비어 있는 분석 결과(agentic 원천만 따로 보기 위함)."""
    return AlarmAnalysisResult(
        alarm_event=event, severity_label="해소", summary="요약", probable_cause="원인",
        recommended_action="조치", notification_channels=["workb"],
    )


async def _analysis_from_source(monkeypatch, source: str, event: AlarmEvent, cfg):
    """상향 원천 하나만 살아 있는 상태로 분석 결과를 만든다."""
    import src.llm as llm_mod

    anomaly = None
    analyzer_llm: object = _AnalyzerLLM()
    if source == "signature":
        pass  # 조건 로그 OOM → 결정적 시그니처 스캔이 3 을 낸다
    elif source == "llm":
        analyzer_llm = _AnalyzerLLM(ai_message_severity=3)
    elif source == "baseline":
        anomaly = 3
    elif source == "fallback":
        analyzer_llm = _RaisingLLM()  # D-256 폴백 결과에도 결정적 상향은 적용된다
    elif source == "agentic":
        monkeypatch.setattr(llm_mod, "create_llm", lambda c, **kw: _TrackALLM())
        state = {"alarm_event": event, "analysis_result": _plain_analysis(event)}
        out = await agentic_enricher_node(state, {"configurable": {"app_config": cfg}})
        return out.get("analysis_result", state["analysis_result"])
    else:  # pragma: no cover — 테스트 오기 방지
        raise AssertionError(source)

    monkeypatch.setattr(
        "noise_gate.application.nodes.alarm_analyzer.create_llm", lambda c, **kw: analyzer_llm
    )
    state = {
        "alarm_event": event, "history_stats": None, "process_snapshot": None,
        "analysis_result": None, "error": None, "anomaly_severity": anomaly,
    }
    out = await alarm_analyzer_node(state, {"configurable": {"app_config": cfg}})
    return out["analysis_result"]


async def _gate(event: AlarmEvent, analysis, cfg, *, self_heal: bool = False):
    state = {
        "alarm_event": event, "history_stats": None, "analysis_result": analysis,
        "error": None, "noise_context": dict(NOISE_CTX), "self_heal": self_heal,
    }
    out = await notification_gate_node(state, {"configurable": {"app_config": cfg}})
    return out["notification_decision"]


SOURCES = ["signature", "llm", "baseline", "fallback", "agentic"]


class TestClearAlarmIgnoresEverySource:
    @pytest.mark.parametrize("source", SOURCES)
    async def test_source_produces_candidate_but_clear_is_not_paged(self, monkeypatch, source):
        cfg = make_cfg()
        event = make_event(
            severity=0, condition_log=PLAIN_LOG if source in ("llm", "baseline") else OOM_LOG
        )
        analysis = await _analysis_from_source(monkeypatch, source, event, cfg)
        # 원천은 상향 후보를 냈다(결함 재현 전제) — 정책 계층이 해소에는 쓰지 않아야 한다
        assert analysis.ai_message_severity == 3

        decision = await _gate(event, analysis, cfg)
        assert decision.tier != TIER_PAGE
        assert decision.stage == STAGE_RESOLVED
        assert decision.tier == TIER_SUPPRESS
        assert decision.signals["effective_severity"] == 0
        assert decision.signals["ai_severity"] is None

    @pytest.mark.parametrize("source", SOURCES)
    async def test_self_heal_pair_still_suppressed(self, monkeypatch, source):
        cfg = make_cfg()
        event = make_event(
            severity=0, condition_log=PLAIN_LOG if source in ("llm", "baseline") else OOM_LOG
        )
        analysis = await _analysis_from_source(monkeypatch, source, event, cfg)
        decision = await _gate(event, analysis, cfg, self_heal=True)
        assert decision.stage == STAGE_SELF_HEAL
        assert decision.tier == TIER_SUPPRESS

    async def test_resolved_to_dashboard_setting_is_respected(self, monkeypatch):
        cfg = make_cfg(resolved_to_dashboard=True)
        event = make_event(severity=0)
        analysis = await _analysis_from_source(monkeypatch, "signature", event, cfg)
        decision = await _gate(event, analysis, cfg)
        assert decision.stage == STAGE_RESOLVED
        assert decision.tier == TIER_DASHBOARD


class TestFiringAlarmBoostUnchanged:
    @pytest.mark.parametrize("source", SOURCES)
    async def test_firing_alarm_still_escalates_to_page(self, monkeypatch, source):
        # 대조군: 같은 원천·같은 로그라도 발생(심각도 1) 알람은 종전대로 3 으로 상향 → PAGE
        cfg = make_cfg()
        event = make_event(
            severity=1, condition_log=PLAIN_LOG if source in ("llm", "baseline") else OOM_LOG
        )
        analysis = await _analysis_from_source(monkeypatch, source, event, cfg)
        decision = await _gate(event, analysis, cfg)
        assert decision.stage == STAGE_SEVERITY3
        assert decision.tier == TIER_PAGE
        assert decision.signals["effective_severity"] == 3


class TestPolicyDirect:
    def test_clear_event_with_ai_three_goes_to_resolved(self):
        # 정책 계층 단위: 해소 알람의 ai_message_severity 는 실효 심각도에 반영하지 않는다
        d = decide_notification(
            make_event(severity=0),
            None,
            SimpleNamespace(pattern_type="", is_routine=None, ai_message_severity=3),
            dict(NOISE_CTX),
            make_cfg().noise_gate,
        )
        assert d.stage == STAGE_RESOLVED
        assert d.signals["effective_severity"] == 0
