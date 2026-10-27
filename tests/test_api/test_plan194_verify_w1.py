"""plans/194 W1 독립 검증 — 실 sre 응답 꼴의 거부 문구 · 기본 on 라우트의 미인증 차단.

실 LLM 0 · 실 sre 0. 가짜 sre는 **실 dispatcher 응답 모양**(`verdict` = `조사 거부 — <코드>`)을
쓴다(mock 통과 ≠ 프로덕션 — Known Mistakes).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import alarm as alarm_routes
from src.config.alarm import NoiseGateConfig
from src.domain.investigation_reject_reasons import REJECT_REASON_TEXTS
from src.nodes.fault_diagnosis import fault_diagnosis
from tests.test_semantic_routing.test_fault_diagnosis import (
    FakeSreClient,
    _app_cfg,
    _gate_cfg,
    _patch_client,
    _state,
)


def _real_sre_rejection(code: str) -> dict:
    """sre_agent `JobStore._submit_result` + `InvestigationDispatcher._blocked_verdict` 꼴."""
    return {"investigation_id": "inv-r", "status": "rejected", "reason": code,
            "verdict": f"조사 거부 — {code}"}


@pytest.mark.parametrize("code", ["queue_full", "service_overloaded",
                                  "host_investigation_in_flight"])
async def test_sync_diagnosis_real_shape_rejection_shows_korean_reason(monkeypatch, code) -> None:
    client = FakeSreClient(sub_result=_real_sre_rejection(code))
    _patch_client(monkeypatch, client)
    out = await fault_diagnosis(_state(), app_config=_app_cfg(_gate_cfg()))
    assert REJECT_REASON_TEXTS[code] in out["final_response"]


def test_manual_flag_code_default_is_on_and_trigger_off() -> None:
    fields = NoiseGateConfig.model_fields
    assert fields["investigation_manual_enabled"].default is True
    assert fields["investigation_trigger_enabled"].default is False


def test_default_on_manual_route_still_requires_authentication(tmp_path) -> None:
    """기본 on이 된 「이 알람 분석」 접수는 인증 켜진 배포에서 토큰 없이 401이다."""
    ng = SimpleNamespace(investigation_manual_enabled=True)
    config = SimpleNamespace(noise_gate=ng, auth=SimpleNamespace(enabled=True, jwt_secret="s"))
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    resp = TestClient(app).post("/api/v1/alarm/a1/investigations")
    assert resp.status_code == 401
