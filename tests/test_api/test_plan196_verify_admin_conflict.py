"""plans/196 잔여 검증 — 관리자 연결 승인·삭제가 저장소 충돌·Redis 오류에 내는 HTTP 응답.

저장소는 스텁(메서드가 예외를 낸다) — Redis·DB·조사 서비스는 띄우지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import WatchError

import src.security.audit_logger as audit_mod
from noise_gate.infrastructure.host_link_store import HostLinkChangedError
from src.api.dependencies import require_admin_user
from src.api.routes import admin_host_links

ADMIN = {"sub": "admin1", "role": "admin"}
KEY = {"source_id": "cm_prd", "hostname": "web01"}
APPROVE = {**KEY, "db_id": "d", "server_name": "web01", "at": "t0"}


class _Store:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def approve(self, *a: Any, **kw: Any) -> Any:
        raise self.exc

    async def delete(self, *a: Any, **kw: Any) -> Any:
        raise self.exc


@pytest.fixture(autouse=True)
def audits(monkeypatch) -> list[dict]:  # noqa: ANN001
    calls: list[dict] = []

    async def _log(**kw: Any) -> None:
        calls.append(kw)

    monkeypatch.setattr(audit_mod, "log_investigation", _log)
    return calls


def _assert_store_failed(res: Any, audits: list[dict], action: str) -> None:
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert detail["code"] == "link_store_failed" and detail["reason"]
    assert audits and audits[-1]["action"] == action
    assert audits[-1]["outcome"] == audit_mod.INVESTIGATION_FAILED
    assert audits[-1]["degraded"][0]["reason"] == "link_store_failed"


def _client(exc: Exception) -> TestClient:
    app = FastAPI()
    app.include_router(admin_host_links.router, prefix="/api/v1")
    app.dependency_overrides[require_admin_user] = lambda: ADMIN
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=False),
        noise_gate=SimpleNamespace(host_link_enabled=True),
    )
    app.state.host_link_store = _Store(exc)
    return TestClient(app, raise_server_exceptions=False)


def test_approve_watch_conflict_is_409_link_changed():
    res = _client(HostLinkChangedError("k")).post("/api/v1/admin/host-links/approve", json=APPROVE)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "link_changed"


def test_delete_persistent_watch_conflict_status(audits):  # noqa: ANN001
    """저장소가 3회 충돌 뒤 `WatchError`를 올리면 503 `link_store_failed` + 실패 감사."""
    res = _client(WatchError("x")).request("DELETE", "/api/v1/admin/host-links", json=KEY)
    _assert_store_failed(res, audits, "delete")


@pytest.mark.parametrize("method,path,body,action", [
    ("POST", "/api/v1/admin/host-links/approve", APPROVE, "approve"),
    ("DELETE", "/api/v1/admin/host-links", KEY, "delete"),
])
def test_admin_write_on_redis_down_status(method, path, body, action, audits):  # noqa: ANN001
    """Redis 연결 오류 — 503 `link_store_failed` + 실패 감사."""
    res = _client(RedisConnectionError("down")).request(method, path, json=body)
    _assert_store_failed(res, audits, action)


def test_admin_list_on_redis_down_is_503():
    class _Down:
        async def list_links(self) -> Any:
            raise RedisConnectionError("down")

    client = _client(RedisConnectionError("down"))
    client.app.state.host_link_store = _Down()
    res = client.get("/api/v1/admin/host-links")
    assert res.status_code == 503 and res.json()["detail"]["code"] == "link_store_failed"
