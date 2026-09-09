"""Plan 54 모듈 5 — 노이즈 관제 API(`/admin/noise/*`) 계약·인가·안전 가드 테스트.

이 라우트는 **억제를 보여주는 곳**이자 **알람을 억제하는 규칙을 만드는 곳**이다.
따라서 세 가지를 겨눈다:
    1. 인가 — 운영자가 아니면 아무것도 볼 수 없다(SSE 포함).
    2. 안전 가드 — 전체 침묵·심각도 상한·만료 없는 침묵을 **서버가** 막는다.
    3. 강건성 — 게이트 off·저장소 부재에도 200과 빈 집계를 준다(화면이 깨지지 않는다).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.dependencies import require_admin_user
from src.api.routes import noise_dashboard as noise_routes

ADMIN = {"sub": "adm", "role": "admin"}


def _make_config(tmp_path, *, gate=True, suppress_cap=2, silence=True) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=True),
        security=SimpleNamespace(mask_pattern="***"),
        noise_gate=SimpleNamespace(
            enable_noise_gate=gate,
            suppress_max_severity=suppress_cap,
            decision_store_path=str(tmp_path / "decisions.jsonl"),
            decision_store_enabled=True,
            decision_store_max_lines=20000,
            silence_enabled=silence,
            silence_store_path=str(tmp_path / "silences.jsonl"),
            silence_max_duration_seconds=604800,
            meta_alert_window_seconds=3600,
            meta_alert_suppress_ratio=0.9,
            meta_alert_min_events=1,
            enable_ai_severity_boost=False,
            ai_severity_escalate_only=True,
            dependency_suppression=False,
            inhibition_enabled=False,
            flapping_enabled=False,
            storm_grouping_enabled=False,
            cross_host_correlation_enabled=False,
            enable_llm_actionability=False,
        ),
    )


def _make_client(config, *, user=ADMIN, authorized=True) -> TestClient:
    app = FastAPI()
    app.include_router(noise_routes.router, prefix="/api/v1")
    app.state.config = config

    if authorized:
        app.dependency_overrides[require_admin_user] = lambda: user
    else:
        def _deny():
            raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

        app.dependency_overrides[require_admin_user] = _deny
    return TestClient(app)


def _seed_decisions(tmp_path, records: list[dict]) -> None:
    """결정 감사 파일을 직접 채운다."""
    path = tmp_path / "decisions.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _decision_record(**over) -> dict:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "alarm_id": "a1",
        "alarm_name": "CPU 사용률 임계 초과",
        "server_name": "WEB-01",
        "tier": "suppress",
        "stage": "flapping",
        "reason": "플래핑 — 상태 진동",
        "priority": 10,
        "fingerprint": "fp1",
        "signals": {"severity": 2},
    }
    rec.update(over)
    return rec


def _silence_body(**over) -> dict:
    body = {
        "server_name": "WEB-*",
        "max_severity": 2,
        "reason": "월간 배포 점검",
        "duration_seconds": 7200,
    }
    body.update(over)
    return body


# ─── 인가 ────────────────────────────────────────────────────────────────


READ_PATHS = [
    "/api/v1/admin/noise/summary",
    "/api/v1/admin/noise/timeseries",
    "/api/v1/admin/noise/top-suppressed",
    "/api/v1/admin/noise/health",
    "/api/v1/admin/noise/decisions",
    "/api/v1/admin/noise/decisions/a1",
    "/api/v1/admin/noise/silences",
    "/api/v1/admin/noise/policy",
]


@pytest.mark.parametrize("path", READ_PATHS)
def test_non_admin_is_denied_on_every_read(tmp_path, path):
    client = _make_client(_make_config(tmp_path), authorized=False)
    assert client.get(path).status_code == 403


def test_non_admin_cannot_create_or_revoke_silence(tmp_path):
    client = _make_client(_make_config(tmp_path), authorized=False)
    assert client.post("/api/v1/admin/noise/silences", json=_silence_body()).status_code == 403
    assert client.delete("/api/v1/admin/noise/silences/slc_x").status_code == 403


def test_unauthenticated_cannot_subscribe_to_control_stream(tmp_path):
    # 관제 스트림은 전 존의 억제 내역이 흐르므로 인가가 특히 중요하다.
    # EventSource는 헤더를 못 실으므로 이 경로만 쿠키 기반 판정을 따로 한다.
    client = _make_client(_make_config(tmp_path))
    assert client.get("/api/v1/admin/noise/stream").status_code == 401


def test_regular_user_cannot_subscribe_to_control_stream(tmp_path, monkeypatch):
    # 사용자 토큰으로는 열 수 없다 — 존 RBAC를 우회해 전 존 억제 내역을 보게 되면 안 된다.
    async def _resolve(request, token=None):  # noqa: ANN001
        return {"sub": "u1", "role": "user"}

    monkeypatch.setattr("src.api.dependencies.resolve_stream_user", _resolve)
    client = _make_client(_make_config(tmp_path))
    assert client.get("/api/v1/admin/noise/stream").status_code == 403


# ─── 집계 ────────────────────────────────────────────────────────────────


def test_summary_identity_holds_in_response(tmp_path):
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id="a1", tier="page", stage="severity3"),
        _decision_record(alarm_id="a2", tier="suppress", stage="flapping"),
        _decision_record(alarm_id="a3", tier="ticket", stage="matrix"),
    ])
    client = _make_client(_make_config(tmp_path))
    body = client.get("/api/v1/admin/noise/summary?range=24h").json()

    assert body["raw"] == 3
    assert sum(body["tiers"].values()) == body["raw"]
    assert sum(s["terminated"] for s in body["stages"]) == body["raw"]


def test_summary_rejects_unknown_range(tmp_path):
    client = _make_client(_make_config(tmp_path))
    resp = client.get("/api/v1/admin/noise/summary?range=99y")
    assert resp.status_code == 400
    assert "조회 범위" in resp.json()["detail"]


def test_timeseries_rejects_unknown_bucket(tmp_path):
    client = _make_client(_make_config(tmp_path))
    assert client.get("/api/v1/admin/noise/timeseries?bucket=3s").status_code == 400


def test_timeseries_returns_filled_buckets(tmp_path):
    _seed_decisions(tmp_path, [_decision_record(tier="suppress")])
    client = _make_client(_make_config(tmp_path))
    body = client.get("/api/v1/admin/noise/timeseries?range=24h&bucket=2h").json()
    assert len(body["points"]) >= 12
    assert sum(p["suppress"] for p in body["points"]) == 1


def test_top_suppressed_ranks_by_count(tmp_path):
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id=f"n{i}", alarm_name="ntpd 감시") for i in range(3)
    ] + [_decision_record(alarm_id="d1", alarm_name="디스크")])
    client = _make_client(_make_config(tmp_path))
    items = client.get("/api/v1/admin/noise/top-suppressed").json()["items"]
    assert items[0]["alarm_name"] == "ntpd 감시" and items[0]["count"] == 3


def test_health_reports_meta_alerts(tmp_path):
    # 억제만 쌓이면 억제율 임계를 넘어 메타경보가 떠야 한다(억제기가 과도하게 억제 중).
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id=f"s{i}", tier="suppress") for i in range(10)
    ])
    client = _make_client(_make_config(tmp_path))
    body = client.get("/api/v1/admin/noise/health").json()
    assert body["healthy"] is False
    assert body["suppress_ratio"] == 1.0
    assert any(a.get("type") == "high_suppress_ratio" for a in body["alerts"])


def test_health_is_ok_on_balanced_traffic(tmp_path):
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id="p1", tier="page", stage="severity3"),
        _decision_record(alarm_id="s1", tier="suppress"),
    ])
    client = _make_client(_make_config(tmp_path))
    assert client.get("/api/v1/admin/noise/health").json()["healthy"] is True


# ─── 강건성 ──────────────────────────────────────────────────────────────


def test_empty_store_returns_200_with_zeros(tmp_path):
    client = _make_client(_make_config(tmp_path))
    body = client.get("/api/v1/admin/noise/summary").json()
    assert body["raw"] == 0 and body["suppress_ratio"] == 0.0


def test_gate_disabled_is_reported_but_not_an_error(tmp_path):
    client = _make_client(_make_config(tmp_path, gate=False))
    body = client.get("/api/v1/admin/noise/summary").json()
    assert body["gate_enabled"] is False


# ─── 결정 조회·추적 ──────────────────────────────────────────────────────


def test_decisions_filter_and_pagination(tmp_path):
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id="a1", tier="page", stage="severity3"),
        _decision_record(alarm_id="a2", tier="suppress", stage="flapping"),
        _decision_record(alarm_id="a3", tier="suppress", stage="maintenance"),
    ])
    client = _make_client(_make_config(tmp_path))
    assert client.get("/api/v1/admin/noise/decisions?tier=suppress").json()["total"] == 2
    assert client.get("/api/v1/admin/noise/decisions?stage=flapping").json()["total"] == 1
    page = client.get("/api/v1/admin/noise/decisions?size=2&page=1").json()
    assert len(page["items"]) == 2 and page["total"] == 3


def test_decision_trace_marks_passed_decided_and_short_circuited(tmp_path):
    _seed_decisions(tmp_path, [_decision_record(alarm_id="trace1", stage="flapping")])
    client = _make_client(_make_config(tmp_path))
    body = client.get("/api/v1/admin/noise/decisions/trace1").json()

    statuses = {row["stage"]: row["status"] for row in body["timeline"]}
    assert statuses["flapping"] == "decided"
    assert statuses["maintenance"] == "passed"        # 앞 단계는 통과했다
    assert statuses["matrix"] == "short_circuited"    # 뒤 단계는 평가되지 않았다
    assert body["decision"]["alarm_id"] == "trace1"


def test_decision_trace_404_when_missing(tmp_path):
    client = _make_client(_make_config(tmp_path))
    assert client.get("/api/v1/admin/noise/decisions/nope").status_code == 404


def test_secret_like_values_are_masked_before_display(tmp_path):
    _seed_decisions(tmp_path, [
        _decision_record(
            alarm_id="sec1",
            alarm_name="sk-abcdefghijklmnopqrstuvwxyz012345",
            signals={"severity": 2, "root_resource": "ghp_" + "a" * 36},
        )
    ])
    client = _make_client(_make_config(tmp_path))
    item = client.get("/api/v1/admin/noise/decisions/sec1").json()["decision"]
    assert item["alarm_name"] == "***"
    assert item["signals"]["root_resource"] == "***"
    assert item["signals"]["severity"] == 2  # 판정 근거인 숫자는 가리지 않는다


# ─── 침묵 안전 가드 ──────────────────────────────────────────────────────


def test_create_silence_succeeds_and_is_listed(tmp_path):
    client = _make_client(_make_config(tmp_path))
    resp = client.post("/api/v1/admin/noise/silences", json=_silence_body())
    assert resp.status_code == 200
    rule = resp.json()["rule"]
    assert rule["created_by"] == "adm" and rule["active"] is True

    items = client.get("/api/v1/admin/noise/silences").json()["items"]
    assert [i["id"] for i in items] == [rule["id"]]


def test_blank_matcher_is_rejected(tmp_path):
    # 전 필드가 비면 모든 알람이 사라진다 — 실수 한 번으로 그렇게 되지 않게 막는다.
    client = _make_client(_make_config(tmp_path))
    resp = client.post("/api/v1/admin/noise/silences", json=_silence_body(server_name=""))
    assert resp.status_code == 400
    assert "매처" in resp.json()["detail"]
    assert client.get("/api/v1/admin/noise/silences").json()["items"] == []


def test_severity_above_cap_is_rejected(tmp_path):
    client = _make_client(_make_config(tmp_path, suppress_cap=2))
    resp = client.post("/api/v1/admin/noise/silences", json=_silence_body(max_severity=3))
    assert resp.status_code == 400
    assert client.get("/api/v1/admin/noise/silences").json()["items"] == []


def test_duration_beyond_limit_is_rejected(tmp_path):
    client = _make_client(_make_config(tmp_path))
    resp = client.post(
        "/api/v1/admin/noise/silences", json=_silence_body(duration_seconds=999999999)
    )
    assert resp.status_code == 400


def test_missing_expiry_is_rejected_by_schema(tmp_path):
    client = _make_client(_make_config(tmp_path))
    body = _silence_body()
    body.pop("duration_seconds")
    assert client.post("/api/v1/admin/noise/silences", json=body).status_code == 422


def test_blank_reason_is_rejected(tmp_path):
    client = _make_client(_make_config(tmp_path))
    assert client.post(
        "/api/v1/admin/noise/silences", json=_silence_body(reason="   ")
    ).status_code == 400


def test_revoke_removes_from_active_but_keeps_history(tmp_path):
    client = _make_client(_make_config(tmp_path))
    rule_id = client.post("/api/v1/admin/noise/silences", json=_silence_body()).json()["rule"]["id"]

    assert client.delete(f"/api/v1/admin/noise/silences/{rule_id}").json()["revoked"] is True
    assert client.get("/api/v1/admin/noise/silences").json()["items"] == []
    history = client.get("/api/v1/admin/noise/silences?include_inactive=true").json()["items"]
    assert len(history) == 1 and history[0]["revoked_at"] is not None


def test_revoke_unknown_is_404(tmp_path):
    client = _make_client(_make_config(tmp_path))
    assert client.delete("/api/v1/admin/noise/silences/slc_nope").status_code == 404


def test_silence_changes_are_audited(tmp_path, monkeypatch):
    calls = []

    async def _capture(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(noise_routes, "log_silence_change", _capture)
    client = _make_client(_make_config(tmp_path))
    rule_id = client.post("/api/v1/admin/noise/silences", json=_silence_body()).json()["rule"]["id"]
    client.delete(f"/api/v1/admin/noise/silences/{rule_id}")

    assert [c["action"] for c in calls] == ["create", "revoke"]
    assert all(c["actor"] == "adm" for c in calls)
    assert calls[0]["detail"]["reason"] == "월간 배포 점검"  # 규칙 내용이 남는다


# ─── 정책 (읽기 전용) ────────────────────────────────────────────────────


def test_policy_exposes_matrix_with_severity3_locked(tmp_path):
    client = _make_client(_make_config(tmp_path))
    body = client.get("/api/v1/admin/noise/policy").json()
    rows = {row["severity"]: row for row in body["matrix"]}
    assert rows[3]["locked"] is True
    assert set(rows[3]["cells"].values()) == {"page"}  # 심각도3은 어느 중요도든 PAGE
    assert rows[2]["locked"] is False


def test_policy_marks_escalate_only_as_locked(tmp_path):
    client = _make_client(_make_config(tmp_path))
    settings = {s["key"]: s for s in client.get("/api/v1/admin/noise/policy").json()["settings"]}
    assert settings["ai_severity_escalate_only"]["locked"] is True
    assert settings["suppress_max_severity"]["value"] == 2


def test_policy_provides_env_keys_for_deeplink(tmp_path):
    # 변경은 설정 화면에서만 한다 — 그래서 화면이 이동할 키를 서버가 준다.
    client = _make_client(_make_config(tmp_path))
    body = client.get("/api/v1/admin/noise/policy").json()
    assert all(s["env_key"].startswith("NOISE_GATE_") for s in body["settings"])
    assert body["editor_path"]


def test_policy_has_no_write_endpoint(tmp_path):
    client = _make_client(_make_config(tmp_path))
    assert client.put("/api/v1/admin/noise/policy", json={}).status_code == 405
