"""plans/194 W1 — 알람 조사 「수동 전용」 모드를 코드 기본으로 (D-347).

- 코드 기본: `investigation_manual_enabled=True` · `investigation_trigger_enabled=False`
- capabilities: `investigation_enabled = 게이트 AND (자동 OR 수동) AND URL`
  · 자동·수동 가용 분리 노출
- 기본 설정(플래그 미지정)에서 「이 알람 분석」 접수 라우트가 404가 아니다
- 조사 서비스 거부(`status:"rejected"`)는 `reason_text`(한국어 한 문장)를 함께 싣는다

`.env` 누수를 막으려고 기본값 검증은 `model_fields`·`model_construct()`(환경 미독)로 한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.api.routes import alarm as alarm_routes
from src.config.alarm import NoiseGateConfig
from src.domain.investigation_reject_reasons import (
    REJECT_REASON_TEXTS,
    reject_display_text,
    reject_reason_text,
)
from tests.test_api.test_plan187_w10_followup import _config as _followup_config
from tests.test_api.test_plan187_w10_followup import _FakeClient as _FakeDiagnoseClient
from tests.test_api.test_plan187_w10_followup import _post as _followup_post
from tests.test_api.test_plan187_w10_followup import _seed_parent
from tests.test_api.test_plan187_w11_manual import (  # noqa: F401 — 픽스처 재사용
    ADMIN,
    _client,
    _config,
    _FakeClient,
    _hostname,
    _inject,
    _post,
    _reset_poll_state,
    _seed_alarm,
    audits,
)


# ── 코드 기본값 ───────────────────────────────────────────────────────────
def test_code_defaults_are_manual_only():
    fields = NoiseGateConfig.model_fields
    assert fields["investigation_manual_enabled"].default is True
    assert fields["investigation_trigger_enabled"].default is False


# ── capabilities 4조합 ────────────────────────────────────────────────────
def _caps(**ng_over) -> dict:
    ng = dict(
        enable_noise_gate=True,
        enable_llm_actionability=False,
        incident_tracking_enabled=False,
        sse_bridge_enabled=False,
        suppress_max_severity=2,
        investigation_service_url="http://127.0.0.1:9/sse",
    )
    ng.update(ng_over)
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=False), noise_gate=SimpleNamespace(**ng)
    )
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app).get("/api/v1/alarm/capabilities").json()


@pytest.mark.parametrize(
    ("trigger", "manual", "enabled"),
    [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
)
def test_capabilities_trigger_manual_combinations(trigger, manual, enabled):
    body = _caps(investigation_trigger_enabled=trigger, investigation_manual_enabled=manual)
    assert body["investigation_enabled"] is enabled
    assert body["investigation_auto_enabled"] is trigger
    assert body["investigation_manual_enabled"] is manual


def test_capabilities_require_gate_and_url():
    off_gate = _caps(
        enable_noise_gate=False,
        investigation_trigger_enabled=True,
        investigation_manual_enabled=True,
    )
    assert off_gate["investigation_enabled"] is False
    assert off_gate["investigation_auto_enabled"] is False
    assert off_gate["investigation_manual_enabled"] is False
    no_url = _caps(investigation_manual_enabled=True, investigation_service_url="")
    assert no_url["investigation_enabled"] is False
    # 수동 가용 자체는 플래그 식이다(URL 없음은 접수 시 service_unconfigured 503으로 드러난다).
    assert no_url["investigation_manual_enabled"] is True


# ── 기본 설정에서 접수 라우트 생존 ────────────────────────────────────────
def test_default_config_manual_route_is_not_404(tmp_path, monkeypatch, audits):  # noqa: F811
    _seed_alarm(tmp_path)
    config = _config(tmp_path)
    ng = dict(vars(config.noise_gate))
    # 플래그 미지정 — 코드 기본을 따른다(환경을 읽지 않는 model_construct).
    ng.pop("investigation_manual_enabled")
    ng["investigation_trigger_enabled"] = False
    config.noise_gate = NoiseGateConfig.model_construct(**ng)
    assert config.noise_gate.investigation_manual_enabled is True
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    resp = _post(_client(config, ADMIN))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "submitted" and len(fake.submit_calls) == 1
    assert "reason_text" not in resp.json()


# ── reason_text ───────────────────────────────────────────────────────────
def test_reason_text_table_and_fallback():
    assert reject_reason_text("host_investigation_in_flight") == (
        "같은 서버를 조사 중입니다 — 진행 중인 조사가 끝난 뒤 다시 요청하세요."
    )
    assert reject_reason_text("queue_full").startswith(
        "조사 대기열이 가득 찼습니다(동시 조사 상한)"
    )
    assert reject_reason_text("service_overloaded").startswith("조사 서비스가 바쁩니다")
    assert reject_reason_text("hourly_budget_exceeded") == (
        "조사 서비스의 시간당 조사 상한에 도달했습니다."
    )
    assert reject_reason_text("weird_code") == "조사 서비스가 요청을 거절했습니다(weird_code)."
    assert reject_reason_text(None) == "조사 서비스가 요청을 거절했습니다(사유 미상)."
    for code in ("dedup_ttl_active", "target_unavailable"):
        assert code in REJECT_REASON_TEXTS


def test_target_unavailable_code_matches_sre_constant():
    """표의 코드가 조사 서비스 가드 상수와 같은 값이어야 한다(소스 실측 — import 경계 0)."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "sre_agent/sre_agent/application/investigation_dispatcher.py"
    ).read_text(encoding="utf-8")
    assert 'GUARD_TARGET_UNAVAILABLE = "target_unavailable"' in src
    for code in ("dedup_ttl_active", "hourly_budget_exceeded", "host_investigation_in_flight"):
        assert f'return "{code}"' in src


def test_manual_rejected_carries_reason_text(tmp_path, monkeypatch, audits):  # noqa: F811
    _seed_alarm(tmp_path)
    _inject(monkeypatch, _FakeClient(status="rejected", reason="host_investigation_in_flight"))
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body["status"] == "rejected" and body["reason"] == "host_investigation_in_flight"
    assert body["reason_text"] == REJECT_REASON_TEXTS["host_investigation_in_flight"]


def test_manual_rejected_unknown_code_keeps_code(tmp_path, monkeypatch, audits):  # noqa: F811
    _seed_alarm(tmp_path)
    _inject(monkeypatch, _FakeClient(status="rejected", reason="queue_full_v2"))
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body["reason_text"] == "조사 서비스가 요청을 거절했습니다(queue_full_v2)."


def test_followup_rejected_carries_reason_text(tmp_path, monkeypatch, audits):  # noqa: F811
    _seed_parent(tmp_path)
    _inject(monkeypatch, _FakeDiagnoseClient(status="rejected", reason="queue_full"))
    body = _followup_post(_client(_followup_config(tmp_path), ADMIN), focus="db_wait").json()
    assert body["status"] == "rejected" and body["reason"] == "queue_full"
    assert body["reason_text"] == REJECT_REASON_TEXTS["queue_full"]


def test_display_text_prefers_table_over_code_verdict():
    """표시 문구 규칙 — 가용성 거부만 verdict 우선 · 표 문구 · 그 밖은 verdict · 기본 문구."""
    facts = "조사 거부 — 대상 호스트가 가용하지 않습니다 (확인 시각 t) [판정 down]."
    assert reject_display_text("target_unavailable", facts) == facts
    table = REJECT_REASON_TEXTS
    assert reject_display_text("target_unavailable", None) == table["target_unavailable"]
    assert reject_display_text("queue_full", "조사 거부 — queue_full") == table["queue_full"]
    assert reject_display_text("new_code", "새 거부 문구") == "새 거부 문구"
    assert reject_display_text("new_code", " ") == "조사 서비스가 요청을 거절했습니다(new_code)."
    assert reject_display_text(None, None) == "조사 서비스가 요청을 거절했습니다(사유 미상)."


class _VerdictClient(_FakeClient):
    """실 sre 응답 꼴 — 거부에 `verdict`(가용성 외에는 「조사 거부 — <코드>」)를 싣는다."""

    def __init__(self, *, verdict: str, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self.verdict = verdict

    async def submit(self, payload: dict) -> dict:
        return {**await super().submit(payload), "verdict": self.verdict}


class _VerdictDiagnoseClient(_FakeDiagnoseClient):
    def __init__(self, *, verdict: str, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self.verdict = verdict

    async def diagnose(self, question, **kwargs):  # noqa: ANN001, ANN003, ANN201
        return {**await super().diagnose(question, **kwargs), "verdict": self.verdict}


def test_manual_rejected_real_shape_uses_table_text(tmp_path, monkeypatch, audits):  # noqa: F811
    _seed_alarm(tmp_path)
    _inject(monkeypatch, _VerdictClient(status="rejected", reason="service_overloaded",
                                        verdict="조사 거부 — service_overloaded"))
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body["reason_text"] == REJECT_REASON_TEXTS["service_overloaded"]


def test_manual_rejected_unavailable_keeps_verdict_facts(tmp_path, monkeypatch, audits):  # noqa: F811
    facts = ("조사 거부 — 대상 호스트가 가용하지 않습니다 (확인 시각 2026-10-26T09:00:00) "
             "[판정 ping_fail]. 가용성 회복 후 다시 요청하십시오.")
    _seed_alarm(tmp_path)
    _inject(monkeypatch, _VerdictClient(status="rejected", reason="target_unavailable",
                                        verdict=facts))
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body["reason"] == "target_unavailable" and body["reason_text"] == facts


def test_followup_rejected_real_shape_uses_table_text(tmp_path, monkeypatch, audits):  # noqa: F811
    _seed_parent(tmp_path)
    _inject(monkeypatch, _VerdictDiagnoseClient(status="rejected", reason="queue_full",
                                                verdict="조사 거부 — queue_full"))
    body = _followup_post(_client(_followup_config(tmp_path), ADMIN), focus="db_wait").json()
    assert body["reason_text"] == REJECT_REASON_TEXTS["queue_full"]


def test_followup_accepted_has_no_reason_text(tmp_path, monkeypatch, audits):  # noqa: F811
    _seed_parent(tmp_path)
    _inject(monkeypatch, _FakeDiagnoseClient())
    body = _followup_post(_client(_followup_config(tmp_path), ADMIN), focus="db_wait").json()
    assert body["status"] == "submitted" and "reason_text" not in body


# ── 판정 기록 — 플래그 미지정이면 원 알람 재료가 남는다 ──────────────────────
async def test_gate_records_alarm_source_fields_by_default(tmp_path):
    from tests.test_api.test_plan187_w11_manual import _gate_record

    rec = await _gate_record(tmp_path)
    assert rec["alarm_time"] == "2026-10-01T09:30:15"
    assert rec["hostname"] == "saisvd01" and rec["resource_type"] == "server.Cpus"
