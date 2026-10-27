"""plans/193 W4 — 인증 저장소 장애 중 break-glass 운영자 관제(D-338 · K-1 B 수용 기준 5).

실제 관제 라우터(`/admin/noise/*` · `/noise/*`)를 실제 인가 의존성(`require_admin_user` ·
`require_user`)으로 올리고 사용자 저장소(`user_repo`)를 비운다:
- break-glass 운영자 토큰(type=admin · 운영자 시크릿) → 운영자 관제 집계 200.
- 같은 상태에서 사용자 토큰 → 사용자 관제도 운영자 관제도 503(사용자 요청 fail-closed).

`tests/test_api/test_plan186_auth_fail_closed.py`는 의존성 함수와 가짜 `/admin` 라우트만 본다 —
관제 라우터를 그대로 붙인 종단 확인은 이 파일이 맡는다. 설정은 SimpleNamespace로 필요한 필드만 둔다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import AUTH_STORE_UNAVAILABLE_DETAIL
from src.api.routes import noise_dashboard as noise_routes
from tests.test_api.test_noise_dashboard_api import _make_config

AUTH_SECRET = "auth-secret-193w4-0123456789abcdef01"
ADMIN_SECRET = "admin-secret-193w4-0123456789abcdef0"


def _token(payload: dict, secret: str) -> str:
    payload = {**payload, "exp": datetime.now(UTC) + timedelta(hours=1)}
    return jwt.encode(payload, secret, algorithm="HS256")


def _client(tmp_path) -> TestClient:
    config = _make_config(tmp_path)
    config.auth.jwt_secret = AUTH_SECRET
    config.admin = SimpleNamespace(jwt_secret=ADMIN_SECRET)
    app = FastAPI()
    app.include_router(noise_routes.router, prefix="/api/v1")
    app.state.config = config
    app.state.user_repo = None  # 인증 저장소 장애(사용자 DB 부재)
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    ["/api/v1/admin/noise/summary", "/api/v1/admin/noise/decisions", "/api/v1/admin/noise/health"],
)
def test_break_glass_operator_console_reads_while_auth_store_down(tmp_path, path):
    token = _token({"sub": "ops", "type": "admin"}, ADMIN_SECRET)
    with _client(tmp_path) as client:
        res = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("path", ["/api/v1/noise/summary", "/api/v1/admin/noise/summary"])
def test_user_token_is_503_while_auth_store_down(tmp_path, path):
    token = _token({"sub": "u1", "name": "U", "role": "admin", "type": "user"}, AUTH_SECRET)
    with _client(tmp_path) as client:
        res = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 503
    assert res.json() == {"detail": AUTH_STORE_UNAVAILABLE_DETAIL}
