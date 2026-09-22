"""Plan 54 모듈 5 — 노이즈 관제 API(`/admin/noise/*` · `/noise/*`) 계약·인가·안전 가드 테스트.

이 라우트는 **억제를 보여주는 곳**이자 **알람을 억제하는 규칙을 만드는 곳**이다.
따라서 세 가지를 겨눈다:
    1. 인가 — 운영자 경로는 운영자만(SSE 포함). 사용자 경로(D-245)는 읽기 5종만 열리고,
       침묵·정책·메타모니터링·스트림은 **등록 자체가 없다**.
    2. 안전 가드 — 전체 침묵·심각도 상한·만료 없는 침묵을 **서버가** 막는다.
    3. 강건성 — 게이트 off·저장소 부재에도 200과 빈 집계를 준다(화면이 깨지지 않는다).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.dependencies import require_admin_user, require_user
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
    # (plans/112 F-1) 종전 단언 `startswith("NOISE_GATE_")`는 **틀린 접두를 정답으로 굳혔다** —
    # 실제 접두는 `NOISE_`였고 딥링크가 존재하지 않는 키를 가리켰다. 접두를 하드코딩하지 않고
    # "설정 카탈로그에 실재하는 키"로 단언한다.
    from src.api.settings_catalog import field_index

    catalog = set(field_index())
    client = _make_client(_make_config(tmp_path))
    body = client.get("/api/v1/admin/noise/policy").json()
    missing = [s["env_key"] for s in body["settings"] if s["env_key"] not in catalog]
    assert body["settings"] and missing == []
    assert body["editor_path"]


def test_policy_has_no_write_endpoint(tmp_path):
    client = _make_client(_make_config(tmp_path))
    assert client.put("/api/v1/admin/noise/policy", json={}).status_code == 405


# ─── 사용자 경로 `/noise/*` (D-245) ──────────────────────────────────────


USER = {"sub": "u1", "role": "user"}

# 사용자에게 여는 것: 집계 3종 + 결정 목록 + 결정 추적. 그 이상은 열지 않는다.
USER_READ_PATHS = [
    "/api/v1/noise/summary",
    "/api/v1/noise/timeseries",
    "/api/v1/noise/top-suppressed",
    "/api/v1/noise/decisions",
]
# 사용자에게 열지 않는 것: 운영 통제(침묵)·설정값(정책)·메타모니터링·전 존 스트림.
USER_ABSENT_PATHS = [
    "/api/v1/noise/health",
    "/api/v1/noise/silences",
    "/api/v1/noise/policy",
    "/api/v1/noise/stream",
]


def _make_user_client(config, *, user=USER, authorized=True) -> TestClient:
    """사용자 인가(`require_user`)만 통과시키는 클라이언트."""
    app = FastAPI()
    app.include_router(noise_routes.router, prefix="/api/v1")
    app.state.config = config

    def _deny_admin():
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

    app.dependency_overrides[require_admin_user] = _deny_admin
    if authorized:
        app.dependency_overrides[require_user] = lambda: user
    else:
        def _deny_user():
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")

        app.dependency_overrides[require_user] = _deny_user
    return TestClient(app)


@pytest.mark.parametrize("path", USER_READ_PATHS)
def test_user_can_read_console_aggregates(tmp_path, path):
    # 운영자가 아니어도 억제 내역을 볼 수 있어야 한다 — 관리자 의존성은 거부로 고정돼 있다.
    client = _make_user_client(_make_config(tmp_path))
    assert client.get(path).status_code == 200


def test_user_can_trace_a_single_decision(tmp_path):
    _seed_decisions(tmp_path, [_decision_record()])
    client = _make_user_client(_make_config(tmp_path))
    body = client.get("/api/v1/noise/decisions/a1").json()
    assert body["decision"]["alarm_id"] == "a1"
    assert any(step["status"] == "decided" for step in body["timeline"])


@pytest.mark.parametrize("path", USER_ABSENT_PATHS)
def test_user_path_does_not_expose_control_or_policy(tmp_path, path):
    # 등록 자체가 없어야 한다(403이 아니라 404) — 인가 판정 하나에 기대지 않는다.
    client = _make_user_client(_make_config(tmp_path))
    assert client.get(path).status_code == 404


def test_user_path_has_no_silence_write(tmp_path):
    client = _make_user_client(_make_config(tmp_path))
    assert client.post("/api/v1/noise/silences", json=_silence_body()).status_code == 404
    assert client.delete("/api/v1/noise/silences/slc_x").status_code == 404


@pytest.mark.parametrize("path", USER_READ_PATHS)
def test_unauthenticated_cannot_read_user_console(tmp_path, path):
    client = _make_user_client(_make_config(tmp_path), authorized=False)
    assert client.get(path).status_code == 401


def test_admin_paths_still_require_admin_after_split(tmp_path):
    # 사용자 경로를 연 것이 운영자 경로의 인가를 느슨하게 만들지 않았는지 확인한다.
    client = _make_user_client(_make_config(tmp_path))
    for path in READ_PATHS:
        assert client.get(path).status_code == 403, path


# ─── plans/112 드릴다운 · 설명 · 가독성 — 운영자·사용자 경로 대칭 ─────────
# 읽기 핸들러는 두 경로가 공유한다(D-245). 새 파라미터·필드가 한쪽에서만 동작하는 비대칭 회귀를
# 막기 위해 **같은 케이스를 두 경로에서** 돌린다. 경로별 차이는 condition_log(운영자 전용) 하나다.

MODES = ["admin", "user"]


def _mode_client(mode: str, config) -> tuple[TestClient, str]:
    if mode == "admin":
        return _make_client(config), "/api/v1/admin/noise"
    return _make_user_client(config), "/api/v1/noise"


def _drill_records() -> list[dict]:
    now = datetime.now(UTC)

    def at(minutes: int) -> str:
        return (now - timedelta(minutes=60 - minutes)).isoformat()

    return [
        _decision_record(alarm_id="p1", tier="page", stage="severity3", ts=at(1),
                         fingerprint="fp-rep", server_name="AP-01", alarm_name="대표 알람"),
        _decision_record(alarm_id="t1", tier="ticket", stage="matrix", ts=at(2)),
        _decision_record(alarm_id="d1", tier="dashboard", stage="matrix", ts=at(3)),
        _decision_record(alarm_id="s1", tier="suppress", stage="flapping", ts=at(4)),
        _decision_record(
            alarm_id="c1", tier="suppress", stage="correlation", ts=at(5),
            correlation_meta={"representative_fp": "fp-rep", "member_seq": 3, "similarity": 0.9},
        ),
        _decision_record(alarm_id="f1", tier="ticket", stage="matrix", ts=at(6),
                         fingerprint="fp-heal", signals={"severity": 1}),
        _decision_record(alarm_id="h1", tier="suppress", stage="self_heal", ts=at(7),
                         fingerprint="fp-heal", signals={"severity": 0}),
    ]


@pytest.mark.parametrize("mode", MODES)
def test_decisions_tier_accepts_comma_separated_set(tmp_path, mode):
    _seed_decisions(tmp_path, _drill_records())
    client, base = _mode_client(mode, _make_config(tmp_path))
    body = client.get(f"{base}/decisions?tier=page,ticket").json()
    assert body["total"] == 3
    assert {i["tier"] for i in body["items"]} == {"page", "ticket"}
    # 토큰 앞뒤 공백은 무시한다.
    assert client.get(f"{base}/decisions?tier= page , ticket ").json()["total"] == 3


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("value", ["bogus", "page,bogus", "PAGE"])
def test_decisions_unknown_tier_is_400(tmp_path, mode, value):
    # 종전에는 오타가 조용히 0건이었다 — "해당 없음"으로 보이지 않게 거부한다.
    client, base = _mode_client(mode, _make_config(tmp_path))
    resp = client.get(f"{base}/decisions?tier={value}")
    assert resp.status_code == 400
    assert "티어" in resp.json()["detail"]


@pytest.mark.parametrize("mode", MODES)
def test_decisions_empty_tier_is_no_filter(tmp_path, mode):
    # 현행 화면이 `tier=` 빈 값을 보낸다 — 400이 아니라 필터 없음이다.
    _seed_decisions(tmp_path, _drill_records())
    client, base = _mode_client(mode, _make_config(tmp_path))
    resp = client.get(f"{base}/decisions?tier=")
    assert resp.status_code == 200 and resp.json()["total"] == 7


@pytest.mark.parametrize("mode", MODES)
def test_single_tier_response_is_unchanged_except_facets(tmp_path, mode):
    records = _drill_records()
    _seed_decisions(tmp_path, records)
    client, base = _mode_client(mode, _make_config(tmp_path))
    body = client.get(f"{base}/decisions?tier=page").json()
    assert set(body) == {"items", "total", "page", "size", "facets"}
    # 변경 전 뷰 = 레코드 전체 + stage_label (S6 필드가 없는 레코드는 키가 늘지 않는다).
    expected = [
        {**rec, "stage_label": "심각도3 단락"} for rec in records if rec["tier"] == "page"
    ]
    assert body["items"] == expected
    assert (body["total"], body["page"], body["size"]) == (1, 1, 50)


@pytest.mark.parametrize("mode", MODES)
def test_decisions_facets_identities(tmp_path, mode):
    _seed_decisions(tmp_path, _drill_records())
    client, base = _mode_client(mode, _make_config(tmp_path))

    body = client.get(f"{base}/decisions").json()
    assert body["facets"]["tiers"] == {"page": 1, "ticket": 2, "dashboard": 1, "suppress": 3}
    assert sum(body["facets"]["tiers"].values()) == body["total"]
    assert sum(body["facets"]["stages"].values()) == body["total"]

    body = client.get(f"{base}/decisions?tier=page,ticket").json()
    assert body["facets"]["tiers"]["page"] + body["facets"]["tiers"]["ticket"] == body["total"]

    body = client.get(f"{base}/decisions?stage=matrix").json()
    assert body["facets"]["stages"]["matrix"] == body["total"] == 3
    assert sum(body["facets"]["tiers"].values()) == body["total"]


@pytest.mark.parametrize("mode", MODES)
def test_decisions_related_representative_and_origin(tmp_path, mode):
    _seed_decisions(tmp_path, _drill_records())
    client, base = _mode_client(mode, _make_config(tmp_path))
    items = {i["alarm_id"]: i for i in client.get(f"{base}/decisions?related=true").json()["items"]}

    rep = items["c1"]["related"]
    assert rep["kind"] == "representative" and rep["found"] is True
    assert (rep["alarm_id"], rep["alarm_name"], rep["server_name"]) == ("p1", "대표 알람", "AP-01")
    origin = items["h1"]["related"]
    assert origin["kind"] == "origin" and origin["found"] is True and origin["alarm_id"] == "f1"
    # 상관·자가복구가 아닌 행에는 키가 없다 · related 미지정이면 어디에도 없다.
    assert "related" not in items["t1"]
    assert all("related" not in i for i in client.get(f"{base}/decisions").json()["items"])


@pytest.mark.parametrize("mode", MODES)
def test_decisions_related_outside_tail_is_marked_not_found(tmp_path, mode):
    _seed_decisions(tmp_path, _drill_records())
    config = _make_config(tmp_path)
    config.noise_gate.decision_store_max_lines = 3  # 대표(p1)가 tail 밖으로 밀려난다
    client, base = _mode_client(mode, config)
    items = {i["alarm_id"]: i for i in client.get(f"{base}/decisions?related=true").json()["items"]}
    assert items["c1"]["related"] == {"kind": "representative", "found": False}


@pytest.mark.parametrize("mode", MODES)
def test_decisions_alarm_name_exact_and_interval(tmp_path, mode):
    records = _drill_records()
    _seed_decisions(tmp_path, records)
    client, base = _mode_client(mode, _make_config(tmp_path))
    assert client.get(f"{base}/decisions", params={"alarm_name": "대표 알람"}).json()["total"] == 1
    assert client.get(f"{base}/decisions", params={"alarm_name": "대표"}).json()["total"] == 0

    since = records[1]["ts"]  # t1(포함)
    until = records[3]["ts"]  # s1(제외)
    body = client.get(f"{base}/decisions", params={"since": since, "until": until}).json()
    assert [i["alarm_id"] for i in body["items"]] == ["d1", "t1"]
    # 시간대 없는 값은 UTC로 본다.
    naive = datetime.fromisoformat(since).astimezone(UTC).replace(tzinfo=None)
    body = client.get(f"{base}/decisions", params={"since": naive.isoformat()}).json()
    assert body["total"] == 6


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("param", ["since", "until"])
def test_decisions_bad_instant_is_400(tmp_path, mode, param):
    client, base = _mode_client(mode, _make_config(tmp_path))
    resp = client.get(f"{base}/decisions", params={param: "어제쯤"})
    assert resp.status_code == 400


# ─── summary 단계 설명 · 활성 여부 ───────────────────────────────────────

_EXPECTED_ENABLE_KEYS = {
    "non_alarm": "NOISE_NON_ALARM_FILTER_ENABLED",
    "silence": "NOISE_SILENCE_ENABLED",
    "dependency": "NOISE_DEPENDENCY_SUPPRESSION",
    "inhibition": "NOISE_INHIBITION_ENABLED",
    "flapping": "NOISE_FLAPPING_ENABLED",
    "storm": "NOISE_STORM_GROUPING_ENABLED",
    "correlation": "NOISE_CROSS_HOST_CORRELATION_ENABLED",
    "annotation": "NOISE_ANNOTATION_PLANNED_SUPPRESS",
}
_ENABLE_FIELDS = {
    "non_alarm": "non_alarm_filter_enabled",
    "silence": "silence_enabled",
    "dependency": "dependency_suppression",
    "inhibition": "inhibition_enabled",
    "flapping": "flapping_enabled",
    "storm": "storm_grouping_enabled",
    "correlation": "cross_host_correlation_enabled",
    "annotation": "annotation_planned_suppress",
}


def _stages(client, base) -> dict[str, dict]:
    return {s["stage"]: s for s in client.get(f"{base}/summary").json()["stages"]}


@pytest.mark.parametrize("mode", MODES)
def test_summary_stages_carry_description_and_enable_key(tmp_path, mode):
    from noise_gate.domain.notification_policy import STAGE_DESCRIPTIONS, STAGE_ORDER

    # unknown 단계가 나오도록 판별 불가 사유의 구 레코드를 하나 넣는다.
    _seed_decisions(tmp_path, [_decision_record(stage=None, reason="정체불명 사유")])
    client, base = _mode_client(mode, _make_config(tmp_path))
    stages = _stages(client, base)
    assert set(stages) == set(STAGE_ORDER) | {"unknown"}
    for key, row in stages.items():
        assert row["description"] == STAGE_DESCRIPTIONS[key] and row["description"]
        assert row["enable_key"] == _EXPECTED_ENABLE_KEYS.get(key)


def test_stage_descriptions_cover_every_stage_and_unknown():
    from noise_gate.domain.notification_policy import STAGE_DESCRIPTIONS, STAGE_ORDER

    assert set(STAGE_DESCRIPTIONS) == set(STAGE_ORDER) | {"unknown"}
    assert all(text.strip() for text in STAGE_DESCRIPTIONS.values())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("stage", sorted(_ENABLE_FIELDS))
@pytest.mark.parametrize("flag", [True, False])
def test_summary_enabled_follows_stage_flag(tmp_path, mode, stage, flag):
    config = _make_config(tmp_path)
    for field_name in _ENABLE_FIELDS.values():
        setattr(config.noise_gate, field_name, not flag)  # 다른 단계는 반대값 — 섞이면 드러난다
    setattr(config.noise_gate, _ENABLE_FIELDS[stage], flag)
    client, base = _mode_client(mode, config)
    stages = _stages(client, base)
    assert stages[stage]["enabled"] is flag
    for always in ("severity3", "self_heal", "resolved", "collection_failed",
                   "maintenance", "matrix"):
        assert stages[always]["enabled"] is True  # 게이트 on이면 항상 평가된다


@pytest.mark.parametrize("mode", MODES)
def test_summary_gate_off_disables_every_stage(tmp_path, mode):
    config = _make_config(tmp_path, gate=False)
    for field_name in _ENABLE_FIELDS.values():
        setattr(config.noise_gate, field_name, True)
    client, base = _mode_client(mode, config)
    assert all(row["enabled"] is False for row in _stages(client, base).values())


def test_every_console_env_key_exists_in_settings_catalog():
    # 정책 딥링크 키와 단계 활성 키가 모두 설정 편집기에 실재해야 한다(F-1 재발 방지).
    from src.api.settings_catalog import field_index

    catalog = set(field_index())
    keys = [noise_routes._env_key(f) for f in noise_routes._POLICY_FIELDS]
    keys += [noise_routes._env_key(f) for f in noise_routes._STAGE_ENABLE_FIELDS.values()]
    assert [k for k in keys if k not in catalog] == []
    assert set(noise_routes._STAGE_ENABLE_FIELDS) == set(_EXPECTED_ENABLE_KEYS)


# ─── timeseries 로컬 격자 ────────────────────────────────────────────────

_FROZEN = datetime(2026, 9, 22, 4, 37, 12, 345678, tzinfo=UTC)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206
        return _FROZEN if tz is not None else _FROZEN.replace(tzinfo=None)


@pytest.fixture()
def frozen_store_clock(monkeypatch):
    import noise_gate.infrastructure.decision_store as decision_store_module

    monkeypatch.setattr(decision_store_module, "datetime", _FrozenDatetime)


@pytest.mark.parametrize("mode", MODES)
def test_timeseries_local_grid_starts_on_even_kst_hours(tmp_path, mode, frozen_store_clock):
    client, base = _mode_client(mode, _make_config(tmp_path))
    points = client.get(
        f"{base}/timeseries?range=24h&bucket=2h&tz_offset_minutes=540"
    ).json()["points"]
    kst = timezone(timedelta(hours=9))
    assert points
    for p in points:
        local = datetime.fromisoformat(p["bucket_ts"]).astimezone(kst)
        assert local.hour % 2 == 0 and local.minute == 0


@pytest.mark.parametrize("mode", MODES)
def test_timeseries_unspecified_offset_is_legacy_utc_grid(tmp_path, mode, frozen_store_clock):
    client, base = _mode_client(mode, _make_config(tmp_path))
    points = client.get(f"{base}/timeseries?range=24h&bucket=2h").json()["points"]
    now = _FROZEN.timestamp()
    start = int((now - 86400) // 7200 * 7200)
    end = int(now // 7200 * 7200)
    assert [p["bucket_ts"] for p in points] == [
        datetime.fromtimestamp(ts, UTC).isoformat()
        for ts in range(start, end + 7200, 7200)
    ]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("offset", [-721, 841])
def test_timeseries_offset_out_of_range_is_422(tmp_path, mode, offset):
    client, base = _mode_client(mode, _make_config(tmp_path))
    assert client.get(f"{base}/timeseries?tz_offset_minutes={offset}").status_code == 422


# ─── S6 식별·근거 필드 — condition_log는 운영자 경로만 ────────────────────


def _s6_record() -> dict:
    return _decision_record(
        alarm_id="e1", db_id="db1", resource_name="svr-01-CPU",
        condition_log="CPU 97% > 90%",
        stage_evidence={"flap_percent": 62.5, "high": 50.0, "low": 25.0, "samples": 12},
    )


@pytest.mark.parametrize("mode", MODES)
def test_s6_fields_and_condition_log_visibility(tmp_path, mode):
    _seed_decisions(tmp_path, [_s6_record()])
    client, base = _mode_client(mode, _make_config(tmp_path))
    listed = client.get(f"{base}/decisions").json()["items"][0]
    traced = client.get(f"{base}/decisions/e1").json()["decision"]
    for item in (listed, traced):
        assert item["db_id"] == "db1" and item["resource_name"] == "svr-01-CPU"
        assert item["stage_evidence"]["samples"] == 12
        if mode == "admin":
            assert item["condition_log"] == "CPU 97% > 90%"
        else:
            assert "condition_log" not in item  # 사용자 경로에는 키 자체가 없다(G-2 (c))


@pytest.mark.parametrize("mode", MODES)
def test_silence_evidence_creator_is_operator_only(tmp_path, mode):
    # 침묵 관리가 운영자 전용이듯(D-245) 규칙을 만든 운영자 계정도 사용자 경로에는 내보내지 않는다.
    _seed_decisions(tmp_path, [_decision_record(
        alarm_id="z1", stage="silence", reason="침묵 규칙(slc_1) — 월간 배포 점검",
        stage_evidence={"rule_id": "slc_1", "matcher_summary": "server=WEB-*",
                        "expires_at": "2026-09-22T12:00:00+00:00", "created_by": "adm"},
    )])
    client, base = _mode_client(mode, _make_config(tmp_path))
    listed = client.get(f"{base}/decisions").json()["items"][0]
    traced = client.get(f"{base}/decisions/z1").json()["decision"]
    for item in (listed, traced):
        evidence = item["stage_evidence"]
        assert evidence["rule_id"] == "slc_1" and evidence["matcher_summary"] == "server=WEB-*"
        if mode == "admin":
            assert evidence["created_by"] == "adm"
        else:
            assert "created_by" not in evidence


@pytest.mark.parametrize("mode", MODES)
def test_s6_string_fields_are_masked(tmp_path, mode):
    secret = "sk-abcdefghijklmnopqrstuvwxyz012345"
    _seed_decisions(tmp_path, [_decision_record(
        alarm_id="m1", resource_name=secret, condition_log=secret,
        stage_evidence={"inhibitor": secret, "inhibitor_severity": 2},
    )])
    client, base = _mode_client(mode, _make_config(tmp_path))
    item = client.get(f"{base}/decisions").json()["items"][0]
    assert item["resource_name"] == "***"
    assert item["stage_evidence"] == {"inhibitor": "***", "inhibitor_severity": 2}
    if mode == "admin":
        assert item["condition_log"] == "***"


# ─── plans/112 잔여 처리 — m-2 시각 불명 레코드 · m-5 정책 탭 키 ─────────


@pytest.mark.parametrize("mode", MODES)
def test_timeseries_reports_zero_excluded_when_all_ts_readable(tmp_path, mode):
    _seed_decisions(tmp_path, [_decision_record(alarm_id="t1")])
    client, base = _mode_client(mode, _make_config(tmp_path))
    body = client.get(f"{base}/timeseries?range=24h&bucket=2h").json()
    assert body["excluded_no_ts"] == 0


@pytest.mark.parametrize("mode", MODES)
def test_timeseries_excluded_records_close_the_summary_identity(tmp_path, mode):
    # 시각을 읽을 수 없는 레코드는 창 판정에서 포함되므로 KPI(summary.raw)에는 세지고 막대에는
    # 못 들어간다 — 그 차이가 excluded_no_ts로 드러나 `Σ구간 + excluded_no_ts == raw`가 된다.
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id="ok1", tier="page"),
        _decision_record(alarm_id="ok2", tier="suppress"),
        _decision_record(alarm_id="bad1", tier="ticket", ts="not-a-timestamp"),
        _decision_record(alarm_id="bad2", tier="suppress", ts=""),
    ])
    client, base = _mode_client(mode, _make_config(tmp_path))
    series = client.get(f"{base}/timeseries?range=24h&bucket=2h").json()
    summary = client.get(f"{base}/summary?range=24h").json()
    placed = sum(
        p["page"] + p["ticket"] + p["dashboard"] + p["suppress"] for p in series["points"]
    )
    assert series["excluded_no_ts"] == 2
    assert placed + series["excluded_no_ts"] == summary["raw"] == 4


def test_policy_tab_includes_every_stage_enable_key_and_keeps_existing_order(tmp_path):
    # 단계 설명이 "켜는 설정"으로 인용하는 키가 정책 탭에 빠지면 안 된다(m-5). 기존 14개 항목의
    # 순서·값은 그대로 두고 없던 키만 뒤에 붙는다(추가만).
    config = _make_config(tmp_path)
    config.noise_gate.non_alarm_filter_enabled = True
    config.noise_gate.annotation_planned_suppress = False
    client = _make_client(config)
    settings = client.get("/api/v1/admin/noise/policy").json()["settings"]
    env_keys = [s["env_key"] for s in settings]

    enable_keys = {noise_routes._env_key(f) for f in noise_routes._STAGE_ENABLE_FIELDS.values()}
    assert enable_keys <= set(env_keys)

    base = list(noise_routes._BASE_POLICY_FIELDS)
    assert [s["key"] for s in settings[: len(base)]] == base  # 기존 순서 그대로
    assert [s["key"] for s in settings[len(base):]] == [
        "non_alarm_filter_enabled", "annotation_planned_suppress",
    ]
    values = {s["key"]: s["value"] for s in settings}
    assert values["silence_enabled"] is True and values["enable_noise_gate"] is True
    assert values["non_alarm_filter_enabled"] is True
    assert values["annotation_planned_suppress"] is False
    assert len(env_keys) == len(set(env_keys))  # 중복 없음
