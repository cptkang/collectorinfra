"""plans/162 W2 R4 — 관제 `/admin/noise/health`가 수집 실패율 메타경보를 그대로 싣는지(D-330 ③)."""

from __future__ import annotations

from tests.test_api.test_noise_dashboard_api import (
    _decision_record,
    _make_client,
    _make_config,
    _seed_decisions,
)


def _seed(tmp_path) -> None:
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id="c1", tier="page", stage="collection_failed"),
        _decision_record(
            alarm_id="c2", tier="ticket", stage="matrix",
            stage_evidence={"collection_failed": True},
        ),
        _decision_record(alarm_id="p1", tier="page", stage="severity3"),
        _decision_record(alarm_id="t1", tier="ticket", stage="matrix"),
    ])


def test_health_reports_collection_failed_ratio(tmp_path):
    _seed(tmp_path)
    config = _make_config(tmp_path)
    config.noise_gate.meta_alert_collection_failed_ratio = 0.3
    body = _make_client(config).get("/api/v1/admin/noise/health").json()
    assert body["healthy"] is False
    alerts = [a for a in body["alerts"] if a["type"] == "collection_failed_ratio"]
    assert alerts == [{
        "type": "collection_failed_ratio",
        "value": 0.5,
        "threshold": 0.3,
        "window_seconds": 3600,
        "count": 2,
        "total": 4,
    }]


def test_health_without_setting_has_no_collection_failed_alert(tmp_path):
    # 설정 미주입(구 config) · 기본 0이면 평가하지 않는다 — 현행 응답 그대로.
    _seed(tmp_path)
    body = _make_client(_make_config(tmp_path)).get("/api/v1/admin/noise/health").json()
    assert body["healthy"] is True
    assert not any(a["type"] == "collection_failed_ratio" for a in body["alerts"])
