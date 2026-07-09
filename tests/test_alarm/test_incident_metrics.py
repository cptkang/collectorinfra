"""incident 계측 활성/비활성에 따른 /alarm/metrics + ack/incidents 엔드포인트 테스트 (D-049).

- store None(트래커 off): MTTA/incident_mttr/사건전환율 null + unavailable_metrics.reason (회귀 0)
- store 주입(트래커 on): 해당 지표 채움 + unavailable_metrics에서 제거 + open_incident_count
- auto_recovery_mttr_seconds: decision_store resolution 레코드 평균(편향 부분지표)
- POST /alarm/incidents/{id}/ack: store None → 503, 주입 시 ack 결과 반환
- GET /alarm/incidents: store None → 빈 배열, 주입 시 목록 반환
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.alarm.domain.notification_policy import NotificationDecision, TIER_PAGE
from src.alarm.infrastructure.decision_store import DecisionStore
from src.api.routes import alarm as alarm_routes


def _seed_pages(path, n: int) -> None:
    store = DecisionStore(str(path))
    for _ in range(n):
        store.record(NotificationDecision(
            tier=TIER_PAGE, reason="r", priority=300, signals={"severity": 2}, fingerprint="fp",
        ))


def _make_config(decisions_path) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=False),
        noise_gate=SimpleNamespace(
            decision_store_path=str(decisions_path),
            meta_alert_window_seconds=3600,
            meta_alert_suppress_ratio=0.9,
            meta_alert_min_events=1,
        ),
    )


def _make_client(config, incident_store=None) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    if incident_store is not None:
        app.state.incident_store = incident_store
    return TestClient(app)


class _FakeIncidentStore:
    """IncidentStore 대역 — metrics/ack/list_open 반환을 고정한다."""

    def __init__(self, metrics_value: dict) -> None:
        self._m = metrics_value
        self.last_metrics_args: tuple | None = None
        self.ack_result = True
        self.ack_calls: list[tuple] = []
        self.list_value: list[dict] = []

    async def metrics(self, *, window_seconds: int, page_count: int) -> dict:
        self.last_metrics_args = (window_seconds, page_count)
        return self._m

    async def ack(self, *, incident_id: int, acked_at, acked_by: str) -> bool:  # noqa: ANN001
        self.ack_calls.append((incident_id, acked_by))
        return self.ack_result

    async def list_open(self, *, limit: int = 100) -> list[dict]:
        return self.list_value


# ─── /alarm/metrics ──────────────────────────────────────────────────────────

def test_metrics_store_none_keeps_null_and_reason(tmp_path):
    decisions = tmp_path / "d.jsonl"
    _seed_pages(decisions, 2)
    client = _make_client(_make_config(decisions))   # incident_store 미주입

    data = client.get("/api/v1/alarm/metrics").json()
    assert data["mtta_seconds"] is None
    assert data["incident_mttr_seconds"] is None
    assert data["incident_conversion_rate"] is None
    assert data["open_incident_count"] == 0
    assert "reason" in data["unavailable_metrics"]   # 회귀 0 — 기존 사유 유지


def test_metrics_store_injected_fills_metrics_and_clears_unavailable(tmp_path):
    decisions = tmp_path / "d.jsonl"
    _seed_pages(decisions, 4)
    store = _FakeIncidentStore({
        "mtta_seconds": 15.0, "incident_mttr_seconds": 120.0,
        "incident_conversion_rate": 0.5, "incident_count": 2, "open_count": 1,
    })
    client = _make_client(_make_config(decisions), incident_store=store)

    data = client.get("/api/v1/alarm/metrics").json()
    assert data["mtta_seconds"] == 15.0
    assert data["incident_mttr_seconds"] == 120.0
    assert data["mttr_seconds"] == 120.0             # 하위호환 = incident_mttr
    assert data["incident_conversion_rate"] == 0.5
    assert data["open_incident_count"] == 1
    assert data["unavailable_metrics"] == {}         # 계측 활성 → 사유 제거
    # page_count(=4)가 metrics 분모로 전달됐는지
    assert store.last_metrics_args == (3600, 4)


def test_metrics_auto_recovery_mttr_from_resolution_records(tmp_path):
    decisions = tmp_path / "d.jsonl"
    _seed_pages(decisions, 1)
    ds = DecisionStore(str(decisions))
    ds.record_resolution(fingerprint="fp", duration_seconds=100.0)
    ds.record_resolution(fingerprint="fp", duration_seconds=200.0)
    client = _make_client(_make_config(decisions))

    data = client.get("/api/v1/alarm/metrics").json()
    assert data["auto_recovery_mttr_seconds"] == 150.0   # (100+200)/2
    # resolution 레코드는 page_count에 영향 없음(1건 유지)
    assert data["page_count"] == 1


# ─── POST /alarm/incidents/{id}/ack ──────────────────────────────────────────

def test_ack_503_when_store_none(tmp_path):
    client = _make_client(_make_config(tmp_path / "d.jsonl"))
    resp = client.post("/api/v1/alarm/incidents/5/ack")
    assert resp.status_code == 503
    assert "비활성" in resp.json()["detail"]


def test_ack_returns_result_when_store_present(tmp_path):
    store = _FakeIncidentStore({})
    client = _make_client(_make_config(tmp_path / "d.jsonl"), incident_store=store)
    resp = client.post("/api/v1/alarm/incidents/5/ack")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"acked": True, "incident_id": 5}
    # acked_by는 인증 off 시 anonymous(ANONYMOUS_USER.sub)
    assert store.ack_calls == [(5, "anonymous")]


def test_ack_false_when_already_handled(tmp_path):
    store = _FakeIncidentStore({})
    store.ack_result = False
    client = _make_client(_make_config(tmp_path / "d.jsonl"), incident_store=store)
    body = client.post("/api/v1/alarm/incidents/9/ack").json()
    assert body["acked"] is False


# ─── GET /alarm/incidents ────────────────────────────────────────────────────

def test_list_incidents_empty_when_store_none(tmp_path):
    client = _make_client(_make_config(tmp_path / "d.jsonl"))
    body = client.get("/api/v1/alarm/incidents").json()
    assert body["incidents"] == []


def test_list_incidents_returns_open_when_store_present(tmp_path):
    store = _FakeIncidentStore({})
    store.list_value = [{"id": 1, "status": "open", "server_name": "srv-1"}]
    client = _make_client(_make_config(tmp_path / "d.jsonl"), incident_store=store)
    body = client.get("/api/v1/alarm/incidents").json()
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["id"] == 1
