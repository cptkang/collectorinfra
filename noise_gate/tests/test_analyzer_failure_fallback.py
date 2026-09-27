"""AI 분석 실패가 알람을 삼키지 않는다 — 원문 알람으로 규칙 판단·통보를 계속한다.

회귀 대상(2026-09-27 · plans/116 §11.8.3 ⑦): 분석 LLM 호출·JSON 해석이 실패하면 analyzer 가
``{"error": ...}`` 만 돌려줘, 게이트·통보 노드가 빈 결과로 끝났다 — 심각(3) 알람도 결정·통보·
결정 기록 없이 사라지고 로그 한 줄만 남았다(D-035 「분석·발송 절대 차단 금지」 취지와 반대).
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from noise_gate.application.nodes.alarm_analyzer import alarm_analyzer_node
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import TIER_PAGE
from noise_gate.infrastructure.decision_store import DecisionStore

REF = datetime(2026, 9, 27, 10, 0, 0)


def make_event(*, severity=3, condition_log="") -> AlarmEvent:
    return AlarmEvent(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h1",
        ip_address="10.0.0.1", resource_ancestry="/Servers/srv", alarm_id="A-1",
        severity=severity, alarm_status="NOT_ACK", resource_type="server.LogMonitor",
        resource_name="r1", alarm_name="프로세스 다운", alarm_time=REF, conditions="",
        condition_log=condition_log, is_clear=(severity == 0),
    )


class _RaisingLLM:
    async def ainvoke(self, messages, *args, **kwargs):
        raise ConnectionError("llm endpoint unreachable")


class _TextLLM:
    async def ainvoke(self, messages, *args, **kwargs):
        return SimpleNamespace(content="죄송합니다, 지금은 분석할 수 없습니다.")


def _cfg(*, gate=True, boost=False) -> SimpleNamespace:
    return SimpleNamespace(
        alarm=SimpleNamespace(get_notification_channels=lambda: ["workb"]),
        noise_gate=SimpleNamespace(
            enable_noise_gate=gate,
            enable_ai_severity_boost=boost,
            dynamic_baseline_enabled=False,
            enable_llm_actionability=False,
            suppress_max_severity=2,
            importance_value_map={"HIGH": "높음", "MID": "보통", "LOW": "낮음"},
            resolved_to_dashboard=False,
        ),
    )


async def _analyze(monkeypatch, llm, event, cfg) -> dict:
    monkeypatch.setattr(
        "noise_gate.application.nodes.alarm_analyzer.create_llm", lambda cfg, **kw: llm
    )
    state = {
        "alarm_event": event, "history_stats": None, "process_snapshot": None,
        "analysis_result": None, "error": None,
    }
    return await alarm_analyzer_node(state, {"configurable": {"app_config": cfg}})


class TestAnalyzerFallback:
    async def test_llm_exception_returns_result_not_error(self, monkeypatch):
        out = await _analyze(monkeypatch, _RaisingLLM(), make_event(), _cfg())
        assert "error" not in out  # 그래프 상태를 error 로 끝내지 않는다
        result = out["analysis_result"]
        assert result.error == "analysis_failed: ConnectionError"
        assert result.severity_label == "심각"
        assert "프로세스 다운" in result.summary
        # 예외 문구(접속 정보가 섞일 수 있음)는 사용자 문구에 싣지 않는다
        assert "unreachable" not in result.probable_cause
        assert result.is_routine is None and result.llm_actionability is None

    async def test_non_json_response_also_falls_back(self, monkeypatch):
        out = await _analyze(monkeypatch, _TextLLM(), make_event(severity=2), _cfg())
        result = out["analysis_result"]
        assert result.error == "analysis_failed: ValueError"
        assert result.severity_label == "경고"

    async def test_deterministic_signature_boost_still_applies(self, monkeypatch):
        event = make_event(severity=1, condition_log="kernel: Out of memory: Killed process 42")
        out = await _analyze(monkeypatch, _RaisingLLM(), event, _cfg(boost=True))
        result = out["analysis_result"]
        assert result.ai_message_severity == 3  # LLM 없이도 결정적 시그니처 상향은 유지


class TestGateAfterFallback:
    async def test_severity3_is_paged_and_recorded(self, monkeypatch, tmp_path):
        cfg = _cfg()
        event = make_event(severity=3)
        result = (await _analyze(monkeypatch, _RaisingLLM(), event, cfg))["analysis_result"]
        store = DecisionStore(str(tmp_path / "decisions.jsonl"))
        state = {
            "alarm_event": event, "history_stats": None, "analysis_result": result,
            "error": None,
            # 유지보수 중이어도 심각(3)은 억제되지 않는다
            "noise_context": {
                "importance_id": "LOW", "maintenance": True, "noti_policy": None,
                "parent_avail_status": None, "source": "polestar_db",
            },
        }
        out = await notification_gate_node(
            state, {"configurable": {"app_config": cfg, "decision_store": store}}
        )
        assert out["notification_decision"].tier == TIER_PAGE
        lines = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        line = json.loads(lines[-1])
        assert line["tier"] == TIER_PAGE
        assert line["stage_evidence"]["analysis"] == "AI 분석 실패 — 원문 알람으로 판단"
