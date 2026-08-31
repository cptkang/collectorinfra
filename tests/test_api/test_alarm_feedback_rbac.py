"""Plan 83 T2·T3 — 피드백·ack 엔드포인트 존(zone) RBAC 회귀 테스트.

배경: SSE 스트림(`alarm_notifications_stream`)은 `alarm_zones_for_user`로 구독자 존을
필터하는데, 피드백(`submit_alarm_feedback`)·ack(`ack_incident`)에는 같은 판정이 없어
**알람명만 알면 다른 존 알람에 라벨을 남기고 사건을 확인 처리**할 수 있었다(docs/28 실측).

판정 규약(SSE와 동일):
- 존 집합이 비면 403(구독 거부와 같은 의미).
- 전 존(관리자·개발 모드)이면 db_id 무관 통과.
- 그 외에는 대상 db_id의 존이 구독자 존에 속할 때만 통과.
- **db_id를 모르면 차단하지 않는다**(하위호환·graceful) — 판정 불가를 거부로 바꾸지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.api.routes import alarm as alarm_routes
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON

GONGJON_DB = "polestar_cm_gp"
BANKJON_DB = "polestar_b0"


def _make_config(tmp_path, *, auth_enabled=True, actionability=True) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=auth_enabled),
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            enable_llm_actionability=actionability,
            feedback_store_path=str(tmp_path / "feedback.jsonl"),
            feedback_store_enabled=True,
        ),
    )


def _make_client(config, user=None, incident_store=None) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    if incident_store is not None:
        app.state.incident_store = incident_store
    app.dependency_overrides[require_user] = lambda: (user or {})
    return TestClient(app)


def _feedback_body(**over):
    body = {"alarm_name": "CPU 임계 초과", "label": "noise"}
    body.update(over)
    return body


OPERATOR_GONGJON = {"sub": "op1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
OPERATOR_BANKJON = {"sub": "op2", "role": "user", "alarm_zones": [ZONE_BANKJON]}
GENERAL_USER = {"sub": "u1", "role": "user", "alarm_zones": []}
ADMIN = {"sub": "adm", "role": "admin", "alarm_zones": None}


# ─── T2: 피드백 존 RBAC ──────────────────────────────────────────────────────


def test_feedback_general_user_denied(tmp_path):
    """존이 부여되지 않은 일반 사용자는 라벨을 남길 수 없다."""
    client = _make_client(_make_config(tmp_path), user=GENERAL_USER)
    resp = client.post("/api/v1/alarm/feedback", json=_feedback_body(db_id=GONGJON_DB))
    assert resp.status_code == 403


def test_feedback_cross_zone_denied(tmp_path):
    """공동존 운영자는 은행존 알람에 라벨을 남길 수 없다."""
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON)
    resp = client.post("/api/v1/alarm/feedback", json=_feedback_body(db_id=BANKJON_DB))
    assert resp.status_code == 403


def test_feedback_same_zone_allowed(tmp_path):
    """자기 존 알람에는 정상 적재된다(회귀 0)."""
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON)
    resp = client.post("/api/v1/alarm/feedback", json=_feedback_body(db_id=GONGJON_DB))
    assert resp.status_code == 200
    assert resp.json()["recorded"] is True


def test_feedback_without_db_id_allowed(tmp_path):
    """db_id 미동반 요청은 존 무판정으로 통과한다(하위호환)."""
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON)
    resp = client.post("/api/v1/alarm/feedback", json=_feedback_body())
    assert resp.status_code == 200


def test_feedback_unmapped_db_id_denied_for_scoped_user(tmp_path):
    """존 매핑이 없는 db_id는 스코프 사용자에게 허용하지 않는다(SSE _visible과 동일)."""
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON)
    resp = client.post("/api/v1/alarm/feedback", json=_feedback_body(db_id="cloud_portal"))
    assert resp.status_code == 403


def test_feedback_admin_all_zones(tmp_path):
    """관리자는 전 존 — db_id 매핑 유무와 무관하게 통과한다."""
    client = _make_client(_make_config(tmp_path), user=ADMIN)
    for db in (GONGJON_DB, BANKJON_DB, "cloud_portal"):
        resp = client.post("/api/v1/alarm/feedback", json=_feedback_body(db_id=db))
        assert resp.status_code == 200, db


def test_feedback_dev_mode_allowed(tmp_path):
    """개발 모드(AUTH_ENABLED=false)는 전 존 — 진입성을 보존한다."""
    config = _make_config(tmp_path, auth_enabled=False)
    client = _make_client(config, user={})
    resp = client.post("/api/v1/alarm/feedback", json=_feedback_body(db_id=BANKJON_DB))
    assert resp.status_code == 200


def test_feedback_disabled_still_503(tmp_path):
    """기능 비활성은 종전대로 503 — 권한 검사가 이 동작을 바꾸지 않는다."""
    config = _make_config(tmp_path, actionability=False)
    client = _make_client(config, user=OPERATOR_GONGJON)
    resp = client.post("/api/v1/alarm/feedback", json=_feedback_body(db_id=GONGJON_DB))
    assert resp.status_code == 503


def test_feedback_invalid_label_still_400(tmp_path):
    """라벨 검증은 종전대로 400."""
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON)
    resp = client.post(
        "/api/v1/alarm/feedback", json=_feedback_body(db_id=GONGJON_DB, label="maybe")
    )
    assert resp.status_code == 400


# ─── T3: ack 존 RBAC ─────────────────────────────────────────────────────────


class _FakeIncidentStore:
    """IncidentStore 대역 — get_db_id/ack만 쓴다."""

    def __init__(self, db_id: str | None) -> None:
        self._db_id = db_id
        self.ack_calls: list[int] = []

    async def get_db_id(self, incident_id: int) -> str | None:
        return self._db_id

    async def ack(self, *, incident_id: int, acked_at, acked_by: str) -> bool:  # noqa: ANN001
        self.ack_calls.append(incident_id)
        return True


def test_ack_cross_zone_denied(tmp_path):
    """공동존 운영자는 은행존 사건을 확인 처리할 수 없다 — ack 호출 자체가 없어야 한다."""
    store = _FakeIncidentStore(BANKJON_DB)
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON, incident_store=store)
    resp = client.post("/api/v1/alarm/incidents/7/ack")
    assert resp.status_code == 403
    assert store.ack_calls == []


def test_ack_same_zone_allowed(tmp_path):
    store = _FakeIncidentStore(GONGJON_DB)
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON, incident_store=store)
    resp = client.post("/api/v1/alarm/incidents/7/ack")
    assert resp.status_code == 200
    assert resp.json()["acked"] is True
    assert store.ack_calls == [7]


def test_ack_unknown_db_id_not_blocked(tmp_path):
    """db_id를 알 수 없으면 차단하지 않는다(판정 불가 ≠ 거부)."""
    store = _FakeIncidentStore(None)
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON, incident_store=store)
    resp = client.post("/api/v1/alarm/incidents/7/ack")
    assert resp.status_code == 200
    assert store.ack_calls == [7]


def test_ack_general_user_denied(tmp_path):
    store = _FakeIncidentStore(GONGJON_DB)
    client = _make_client(_make_config(tmp_path), user=GENERAL_USER, incident_store=store)
    resp = client.post("/api/v1/alarm/incidents/7/ack")
    assert resp.status_code == 403
    assert store.ack_calls == []


def test_ack_tracker_off_still_503(tmp_path):
    """트래커 비활성은 종전대로 503(회귀 0)."""
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON)
    resp = client.post("/api/v1/alarm/incidents/7/ack")
    assert resp.status_code == 503


# ─── T7: 철회 라우트 ─────────────────────────────────────────────────────────


def test_feedback_response_carries_ts(tmp_path):
    """적재 응답의 ts로 철회 대상을 지목한다 — 없으면 되돌릴 방법이 없다."""
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON)
    body = client.post(
        "/api/v1/alarm/feedback", json=_feedback_body(db_id=GONGJON_DB)
    ).json()
    assert body["ts"]


def test_retract_removes_label_from_candidates(tmp_path):
    """철회 후에는 few-shot 후보에서 빠진다(파일에는 원본이 남는다)."""
    from noise_gate.infrastructure.feedback_store import FeedbackStore

    config = _make_config(tmp_path)
    client = _make_client(config, user=OPERATOR_GONGJON)
    ts = client.post(
        "/api/v1/alarm/feedback", json=_feedback_body(db_id=GONGJON_DB)
    ).json()["ts"]

    store = FeedbackStore(config.noise_gate.feedback_store_path)
    assert len(store.find_similar(alarm_name="CPU 임계 초과")) == 1

    resp = client.post(
        "/api/v1/alarm/feedback/retract",
        json={"target_ts": ts, "alarm_name": "CPU 임계 초과", "db_id": GONGJON_DB},
    )
    assert resp.status_code == 200
    assert store.find_similar(alarm_name="CPU 임계 초과") == []


def test_retract_cross_zone_denied(tmp_path):
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON)
    resp = client.post(
        "/api/v1/alarm/feedback/retract",
        json={"target_ts": "2026-01-01T00:00:00+00:00", "db_id": BANKJON_DB},
    )
    assert resp.status_code == 403


def test_retract_requires_target_ts(tmp_path):
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON)
    resp = client.post("/api/v1/alarm/feedback/retract", json={"target_ts": ""})
    assert resp.status_code == 400


def test_retract_disabled_returns_503(tmp_path):
    config = _make_config(tmp_path, actionability=False)
    client = _make_client(config, user=OPERATOR_GONGJON)
    resp = client.post("/api/v1/alarm/feedback/retract", json={"target_ts": "x"})
    assert resp.status_code == 503


def test_feedback_record_has_labeled_by(tmp_path):
    """A-4: 작성자가 레코드에 남는다(감사 추적)."""
    import json

    config = _make_config(tmp_path)
    client = _make_client(config, user=OPERATOR_GONGJON)
    client.post("/api/v1/alarm/feedback", json=_feedback_body(db_id=GONGJON_DB))
    row = json.loads(
        open(config.noise_gate.feedback_store_path, encoding="utf-8").readline()
    )
    assert row["labeled_by"] == "op1"
    assert row["db_id"] == GONGJON_DB


def test_feedback_summary_aggregates(tmp_path):
    """T13: 상반 라벨이 집계로 드러난다(판정에는 관여하지 않는다)."""
    config = _make_config(tmp_path)
    client = _make_client(config, user=OPERATOR_GONGJON)
    client.post("/api/v1/alarm/feedback", json=_feedback_body(db_id=GONGJON_DB, label="noise"))
    client.post("/api/v1/alarm/feedback", json=_feedback_body(db_id=GONGJON_DB, label="valid"))

    items = client.get("/api/v1/alarm/feedback/summary").json()["items"]
    assert len(items) == 1
    assert items[0]["noise"] == 1 and items[0]["valid"] == 1
    assert items[0]["last_label"] == "valid"


def test_feedback_summary_empty_without_file(tmp_path):
    client = _make_client(_make_config(tmp_path), user=OPERATOR_GONGJON)
    assert client.get("/api/v1/alarm/feedback/summary").json()["items"] == []
