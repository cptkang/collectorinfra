"""POST /alarm/analyze-test(/raw) dry_run=false 게이트 경로 state 병합 회귀 테스트.

회귀(2026-07-03 실측, docs/16 §6 시나리오 실행 중 발견): analyze-test 엔드포인트가
notifier_state를 `{**result_state, ...}`로만 구성했는데, `alarm_analyzer_node`는
LangGraph 업데이트 dict(`{"analysis_result": ...}`)만 반환하므로 `alarm_event`가 빠져
`notification_gate_node`의 `state["alarm_event"]`에서 KeyError → 500이 발생했다.
수정: 원본 state 병합(`{**state, **result_state, ...}`). 본 테스트는 dry_run=false +
게이트 활성 경로가 200을 반환하고 결정 감사가 기록되는지 고정한다.

test_metrics_endpoint.py의 최소 앱 패턴(alarm 라우터만 마운트, auth.enabled=False)을 따르고,
LLM 호출인 alarm_analyzer_node만 업데이트 dict를 돌려주는 가짜로 대체한다(실제 반환 형태 모사).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.domain.alarm import AlarmAnalysisResult
from src.api.routes import alarm as alarm_routes

PAYLOAD = {
    "dbId": "polestar_pg",
    "serverName": "gate-state-test",
    "hostname": "gate-state-test",
    "ipAddress": "10.0.0.1",
    "resourceAncestry": "",
    "alarmId": "GATE-STATE-1",
    "severity": 3,
    "alarmStatus": "NOT_ACK",
    "resourceType": "server.Server",
    "resourceName": "CPU",
    "alarmName": "gate state 회귀",
    "alarmTime": "20260703090000",
    "conditions": "cpu>90",
    "conditionLog": "cpu=99",
}


async def fake_analyzer_node(state, config):  # noqa: ANN001
    """실제 노드처럼 업데이트 dict만 반환한다(alarm_event를 되돌려주지 않음)."""
    event = state["alarm_event"]
    result = AlarmAnalysisResult(
        alarm_event=event,
        severity_label="심각",
        summary="테스트 요약",
        probable_cause="테스트 원인",
        recommended_action="테스트 조치",
        notification_channels=[],
    )
    return {"analysis_result": result}


def _make_config(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=False),
        alarm=SimpleNamespace(
            get_notification_channels=lambda: [],
            history_enabled=False,
            process_enrich_enabled=False,
        ),
        workb=SimpleNamespace(),
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            decision_store_path=str(tmp_path / "decisions.jsonl"),
            decision_store_enabled=True,
            ticket_batch_queue_path=str(tmp_path / "ticket.jsonl"),
            ticket_batch_queue_enabled=False,
            enable_llm_actionability=False,
            suppress_max_severity=2,
            importance_value_map={},
            resolved_to_dashboard=False,
        ),
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "noise_gate.application.nodes.alarm_analyzer.alarm_analyzer_node",
        fake_analyzer_node,
    )
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = _make_config(tmp_path)
    return TestClient(app), tmp_path


class TestAnalyzeTestGateState:
    def test_raw_dry_run_false_gate_records_decision(self, client):
        """dry_run=false + 게이트 활성: KeyError 없이 200 + 결정 감사 기록(sev3→page)."""
        tc, tmp_path = client
        resp = tc.post(
            "/api/v1/alarm/analyze-test/raw",
            json={
                "message": json.dumps(PAYLOAD, ensure_ascii=False),
                "dry_run": False,
                "send_notification": True,
                "channels": [],
                "push_to_ui": False,
                "query_history": False,
                "query_process": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["error"] is None
        # 결정 감사가 기록되어야 한다 (게이트가 실제 실행됨 — state 병합 회귀 방지)
        lines = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        recs = [json.loads(ln) for ln in lines]
        mine = [r for r in recs if r["alarm_id"] == PAYLOAD["alarmId"]]
        assert mine, "결정 감사 레코드 없음 — 게이트가 실행되지 않음"
        assert mine[0]["tier"] == "page"  # 심각도3 절대 PAGE
