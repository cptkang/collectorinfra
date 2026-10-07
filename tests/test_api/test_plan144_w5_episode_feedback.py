"""plans/144 W5 — 크로스소스 사건 피드백 API (`POST /admin/noise/episode-feedback`).

    - 운영자 경로에만 있다(D-245: 사용자 `/noise/*`는 읽기 5종 — 쓰기 미등록 404)
    - 인가는 `require_admin_user` 그대로 — 운영자 토큰 `type`·사용자 토큰 role을 명시 검증
    - 라벨 2종만(400) · episode_id는 `ep-`+16진 12자리 · 길이·제어문자 제한(422) · 저장소 off면 503
    - 기록은 few-shot 후보(find_similar)·유효/노이즈 집계에 섞이지 않고, `/alarm/feedback/summary`의
      `episode_counts`로만 센다
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noise_gate.infrastructure.feedback_store import FeedbackStore
from src.api.dependencies import require_admin_user, require_user
from src.api.routes import alarm as alarm_routes
from src.api.routes import noise_dashboard as noise_routes

ADMIN = {"sub": "adm", "role": "admin", "alarm_zones": None}
EP = "ep-0123456789ab"  # cross_source.episode_id_for 형식
PATH = "/api/v1/admin/noise/episode-feedback"


def _config(tmp_path, *, store_enabled: bool = True, auth_enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(
            enabled=auth_enabled, jwt_secret="user-secret-xxxxxxxxxxxxxxxxxxxxxxxx"
        ),
        admin=SimpleNamespace(jwt_secret="admin-secret-xxxxxxxxxxxxxxxxxxxxxxx"),
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            enable_llm_actionability=True,
            feedback_store_path=str(tmp_path / "feedback.jsonl"),
            feedback_store_enabled=store_enabled,
            feedback_store_max_lines=20000,
        ),
    )


def _client(config, *, override_admin: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(noise_routes.router, prefix="/api/v1")
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    if override_admin:
        app.dependency_overrides[require_admin_user] = lambda: ADMIN
    app.dependency_overrides[require_user] = lambda: ADMIN
    return TestClient(app)


def _body(**over) -> dict:
    body = {
        "label": "demotion_needed", "episode_id": EP, "alarm_id": "J-1", "alarm_name": "ERROR_X",
        "server_name": "xsapp01", "db_id": "jennifer_common", "tier": "dashboard",
        "stage": "cross_source", "applied": True,
    }
    body.update(over)
    return body


def _rows(config) -> list[dict]:
    with open(config.noise_gate.feedback_store_path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@pytest.mark.parametrize("label", ["episode_split", "demotion_needed"])
def test_records_episode_label_with_author(tmp_path, label):
    config = _config(tmp_path)
    resp = _client(config).post(PATH, json=_body(label=label))
    assert resp.status_code == 200 and resp.json()["recorded"] is True and resp.json()["ts"]
    row = _rows(config)[0]
    assert row["label"] == label and row["episode_id"] == EP
    assert row["alarm_id"] == "J-1" and row["applied"] is True and row["labeled_by"] == "adm"


@pytest.mark.parametrize("label", ["noise", "valid", "retract", ""])
def test_rejects_other_labels(tmp_path, label):
    config = _config(tmp_path)
    assert _client(config).post(PATH, json=_body(label=label)).status_code == 400


@pytest.mark.parametrize("episode_id", ["", "  ", "ep-gongjon-xsapp01-1", "ep-0123456789AB"])
def test_requires_episode_id_format(tmp_path, episode_id):
    client = _client(_config(tmp_path))
    assert client.post(PATH, json=_body(episode_id=episode_id)).status_code == 422


def test_note_length_capped(tmp_path):
    assert _client(_config(tmp_path)).post(PATH, json=_body(note="가" * 201)).status_code == 422


def test_store_disabled_is_503(tmp_path):
    client = _client(_config(tmp_path, store_enabled=False))
    assert client.post(PATH, json=_body()).status_code == 503


def test_user_path_has_no_write_route(tmp_path):
    # D-245 — 사용자 경로는 읽기 5종뿐이다(등록 자체가 없어 404/405).
    resp = _client(_config(tmp_path)).post("/api/v1/noise/episode-feedback", json=_body())
    assert resp.status_code in (404, 405)


def test_non_admin_denied(tmp_path):
    app = FastAPI()
    app.include_router(noise_routes.router, prefix="/api/v1")
    app.state.config = _config(tmp_path)

    def _deny():
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

    app.dependency_overrides[require_admin_user] = _deny
    assert TestClient(app).post(PATH, json=_body()).status_code == 403


# ─── 실제 인가 의존성 — 토큰 type·role 명시 검증 ─────────────────────────────


def _token(secret: str, **claims) -> str:
    return jwt.encode({"sub": "someone", **claims}, secret, algorithm="HS256")


def _real_auth_client(tmp_path) -> tuple[TestClient, SimpleNamespace]:
    config = _config(tmp_path)
    return _client(config, override_admin=False), config


def test_real_auth_break_glass_admin_token_allowed(tmp_path):
    client, config = _real_auth_client(tmp_path)
    token = _token(config.admin.jwt_secret, type="admin")
    resp = client.post(PATH, json=_body(), headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


@pytest.mark.parametrize("secret_attr,claims", [
    ("auth", {"role": "user"}),               # 사용자 토큰 · role user
    ("auth", {"type": "admin"}),              # type만 admin인 사용자 시크릿 토큰 — role 기본 user
    ("admin", {"type": "user"}),              # 운영자 시크릿이지만 type이 admin이 아님
])
def test_real_auth_rejects_non_admin_tokens(tmp_path, secret_attr, claims):
    client, config = _real_auth_client(tmp_path)
    secret = getattr(config, secret_attr).jwt_secret
    resp = client.post(PATH, json=_body(),
                       headers={"Authorization": f"Bearer {_token(secret, **claims)}"})
    assert resp.status_code in (401, 403)
    assert not (tmp_path / "feedback.jsonl").exists()


def test_real_auth_missing_token_401(tmp_path):
    client, _ = _real_auth_client(tmp_path)
    assert client.post(PATH, json=_body()).status_code == 401


# ─── few-shot·유효/노이즈 집계 비혼입 ─────────────────────────────────────


def test_not_mixed_into_few_shot_or_label_summary(tmp_path):
    config = _config(tmp_path)
    client = _client(config)
    client.post("/api/v1/alarm/feedback", json={"alarm_name": "ERROR_X", "label": "valid"})
    client.post(PATH, json=_body(label="episode_split"))
    client.post(PATH, json=_body(label="demotion_needed"))

    store = FeedbackStore(config.noise_gate.feedback_store_path)
    assert [r["label"] for r in store.find_similar(alarm_name="ERROR_X", limit=10)] == ["valid"]

    body = client.get("/api/v1/alarm/feedback/summary").json()
    assert len(body["items"]) == 1 and body["items"][0]["valid"] == 1
    assert body["items"][0]["noise"] == 0
    assert body["episode_counts"] == {"episode_split": 1, "demotion_needed": 1}
