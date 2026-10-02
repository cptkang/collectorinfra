"""Plan 83 T5 — 알람 UI 기능 가용성 조회(`GET /alarm/capabilities`).

카드 UI가 "버튼을 렌더할지"를 서버에 묻기 위한 계약이다. 지금은 액션가능성이 off여도
피드백 버튼이 항상 그려지고 누르면 503이 떠서 운영자가 원인을 알 수 없다(docs/28 실측).

노출 원칙: **불리언·정수만** — 경로·시크릿·엔드포인트 주소는 싣지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.api.routes import alarm as alarm_routes


def _client(**noise_over) -> TestClient:
    ng = dict(
        enable_noise_gate=True,
        enable_llm_actionability=False,
        incident_tracking_enabled=False,
        sse_bridge_enabled=False,
        suppress_max_severity=2,
    )
    ng.update(noise_over)
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=False), noise_gate=SimpleNamespace(**ng)
    )
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def test_capabilities_all_off():
    body = _client().get("/api/v1/alarm/capabilities").json()
    assert body["feedback_enabled"] is False
    assert body["incident_tracking"] is False
    assert body["sse_bridge"] is False
    assert body["suppress_stream"] is False
    assert body["suppress_max_severity"] == 2


def test_feedback_enabled_requires_both_flags():
    """피드백 라우트의 503 조건과 **같은 식**이어야 한다(게이트 AND 액션가능성)."""
    assert _client(enable_llm_actionability=True).get(
        "/api/v1/alarm/capabilities"
    ).json()["feedback_enabled"] is True
    assert _client(
        enable_noise_gate=False, enable_llm_actionability=True
    ).get("/api/v1/alarm/capabilities").json()["feedback_enabled"] is False


def test_capabilities_reflect_flags():
    body = _client(
        incident_tracking_enabled=True, sse_bridge_enabled=True, suppress_max_severity=1
    ).get("/api/v1/alarm/capabilities").json()
    assert body["incident_tracking"] is True
    assert body["sse_bridge"] is True
    assert body["suppress_max_severity"] == 1


def test_capabilities_expose_no_paths_or_secrets():
    """경로·토큰·URL이 응답에 섞이면 안 된다(불리언·정수만)."""
    body = _client().json() if False else _client().get("/api/v1/alarm/capabilities").json()
    assert set(body) == {
        "feedback_enabled", "incident_tracking", "sse_bridge",
        "suppress_stream", "suppress_max_severity",
        "prompt_suggest_enabled",   # Plan 86 — 불리언(경로·시크릿 아님)
    }
    for value in body.values():
        assert isinstance(value, (bool, int)), value
