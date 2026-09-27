"""agentic 보강이 노이즈 컨텍스트 「수집 실패」 표시를 지우지 않는다.

회귀 대상(2026-09-27 · 결함 ⑩): 노이즈 컨텍스트 수집이 실패해 ``noise_context`` 가 None 일 때
(보강 5초 초과 · 저장소 생성 실패), agentic 보강이 신호를 1건 이상 모으면 ``source`` 키 없는
새 dict 를 만들어 넣었다. 정책 계층은 ``None`` 또는 ``source == "unavailable"`` 만 수집 실패로
보므로 「수집 실패 보수 PAGE」 대신 중요도 「보통」으로 매트릭스를 돌았다(심각도 1 → DASHBOARD,
심각도 2 → TICKET).

도달 조건: agentic on + 게이트 on + 비심각 메시지형 알람 + 수집 신호 1건 이상.
노드를 워커 순서(context_enricher → analyzer → agentic_enricher → gate)대로 이어 실행한다.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from noise_gate.application.nodes.agentic_enricher import agentic_enricher_node
from noise_gate.application.nodes.alarm_analyzer import alarm_analyzer_node
from noise_gate.application.nodes.alarm_context_enricher import alarm_context_enricher_node
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import STAGE_COLLECTION_FAILED, TIER_PAGE

REF = datetime(2026, 9, 27, 10, 0, 0)


def make_event(*, severity: int = 1, condition_log: str = "routine ok heartbeat") -> AlarmEvent:
    """비심각 메시지형 알람(condition_log 보유 · 비메트릭 자원)."""
    return AlarmEvent(
        db_id="polestar_cm_gp", server_name="srv-log01", hostname="hlog01",
        ip_address="10.1.2.9", resource_ancestry="/Servers/srv/LogMonitor", alarm_id="LOG-001",
        severity=severity, alarm_status="NOT_ACK", resource_type="server.LogMonitor",
        resource_name="syslog", alarm_name="로그 패턴 감지", alarm_time=REF, conditions="",
        condition_log=condition_log, is_clear=(severity == 0),
    )


def make_cfg(*, boost: bool = False) -> SimpleNamespace:
    """게이트·agentic on(트랙 A) 구성 — 노드 4개가 읽는 필드만 둔다."""
    return SimpleNamespace(
        alarm=SimpleNamespace(
            history_enabled=False,
            process_enrich_enabled=False,
            enrich_timeout_seconds=0.05,
            get_notification_channels=lambda: ["workb"],
        ),
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            enable_agentic_enricher=True,
            agentic_enricher_message_alarms_only=True,
            agentic_enricher_fallback="semantic_routing",  # vLLM 미가용 → 트랙 A
            agentic_enricher_timeout_seconds=8.0,
            agentic_enricher_max_tool_calls=5,
            enable_ai_severity_boost=boost,
            dynamic_baseline_enabled=False,
            enable_llm_actionability=False,
            message_enrichment_enabled=False,
            noise_context_cache_ttl_seconds=0,
            suppress_max_severity=2,
            importance_value_map={"HIGH": "높음", "MID": "보통", "LOW": "낮음"},
            resolved_to_dashboard=False,
        ),
        orchestrator=SimpleNamespace(provider="vllm", base_url="", api_key="", health_timeout=1),
        llm=SimpleNamespace(gemini_api_key=""),
    )


class _HangingNoiseRepo:
    """조회가 보강 제한 시간을 넘기는 저장소(보강 타임아웃 재현)."""

    async def fetch(self, event, **kwargs):  # noqa: ANN001, ANN003
        await asyncio.sleep(5)
        return {"source": "polestar_db"}


class _AnalyzerLLM:
    async def ainvoke(self, messages, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return SimpleNamespace(content=json.dumps({
            "severity_label": "주의", "summary": "요약", "probable_cause": "원인",
            "recommended_action": "조치", "pattern_type": "", "ai_message_severity": None,
        }, ensure_ascii=False))


class _TrackALLM:
    async def ainvoke(self, messages, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return SimpleNamespace(content='{"needed_signals": ["message_signature"]}')


async def _run_chain(monkeypatch, *, event: AlarmEvent, cfg, noise_repo) -> tuple[dict, object]:
    """워커 그래프 순서대로 노드를 이어 실행하고 (최종 상태, 결정)을 돌려준다."""
    import src.llm as llm_mod

    monkeypatch.setattr(
        "noise_gate.application.nodes.alarm_analyzer.create_llm", lambda c, **kw: _AnalyzerLLM()
    )
    monkeypatch.setattr(llm_mod, "create_llm", lambda c, **kw: _TrackALLM())

    config = {"configurable": {"app_config": cfg, "noise_repo": noise_repo}}
    # 워커 초기 상태(alarm_worker 의 ainvoke 입력과 같은 None 시드)
    state: dict = {
        "alarm_event": event, "history_stats": None, "process_snapshot": None,
        "analysis_result": None, "error": None, "noise_context": None,
        "notification_decision": None, "self_heal": False,
    }
    for node in (alarm_context_enricher_node, alarm_analyzer_node, agentic_enricher_node):
        state.update(await node(state, config))
    decision = (await notification_gate_node(state, config))["notification_decision"]
    return state, decision


class TestCollectionFailureSurvivesAgentic:
    @pytest.mark.parametrize(
        ("severity", "condition_log", "boost"),
        [
            (1, "routine ok heartbeat", False),       # 수정 전: 1×보통 → DASHBOARD
            (1, "too many open files", True),         # 수정 전: 상향 2×보통 → TICKET
            (2, "routine ok heartbeat", False),       # 수정 전: 2×보통 → TICKET
        ],
    )
    async def test_enrich_timeout_then_agentic_pages(
        self, monkeypatch, severity, condition_log, boost
    ):
        state, decision = await _run_chain(
            monkeypatch,
            event=make_event(severity=severity, condition_log=condition_log),
            cfg=make_cfg(boost=boost),
            noise_repo=_HangingNoiseRepo(),
        )
        # agentic 이 컨텍스트를 채웠더라도 수집 실패 표시는 남는다
        assert state["noise_context"]["agentic"]["backend"] == "track_a"
        assert state["noise_context"]["source"] == "unavailable"
        assert decision.stage == STAGE_COLLECTION_FAILED
        assert decision.tier == TIER_PAGE

    async def test_repo_build_failure_then_agentic_pages(self, monkeypatch):
        # 워커가 저장소 생성에 실패하면 noise_repo=None → 컨텍스트 미수집(None)
        state, decision = await _run_chain(
            monkeypatch, event=make_event(), cfg=make_cfg(), noise_repo=None
        )
        assert state["noise_context"]["source"] == "unavailable"
        assert decision.stage == STAGE_COLLECTION_FAILED
        assert decision.tier == TIER_PAGE


class TestCollectedContextUntouched:
    async def test_existing_context_keeps_its_source(self, monkeypatch):
        # 수집에 성공한 컨텍스트는 종전대로 보존·확장만 한다(source 덮어쓰기 없음)
        import src.llm as llm_mod

        monkeypatch.setattr(llm_mod, "create_llm", lambda c, **kw: _TrackALLM())
        cfg = make_cfg()
        ctx = {"importance_id": "LOW", "maintenance": False, "source": "polestar_db"}
        state = {
            "alarm_event": make_event(),
            "analysis_result": SimpleNamespace(ai_message_severity=None, ai_severity_reason=""),
            "noise_context": ctx,
        }
        out = await agentic_enricher_node(state, {"configurable": {"app_config": cfg}})
        assert out["noise_context"]["source"] == "polestar_db"
        assert out["noise_context"]["importance_id"] == "LOW"
        assert out["noise_context"]["agentic"]["backend"] == "track_a"
