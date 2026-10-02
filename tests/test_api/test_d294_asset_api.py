"""D-294 W6 — 자산 자동 생성 API × 실제 서비스 × 실제 잡 러너 × 인메모리 Redis 페이크.

- 현황 조회 → 프로파일링 202 → 폴링 → 초안 → 승인(고른 자산만) → 시드 버전 되돌리기
- 모르는 자산·빈 포함 목록 422 · 없는 초안 404 · 감사 기록
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import db_structure
from src.schema_cache.admin_jobs import AdminJobRunner
from src.schema_cache.asset_generation_service import AssetGenerationService
from src.schema_cache.db_registration_service import DBRegistrationService
from src.schema_cache.db_structure_service import DBStructureService
from tests.test_schema_cache.test_d294_asset_generation_service import SRC, _sql_rows, _tables
from tests.test_schema_cache.test_plan104_service_fixtures import make_env, make_registry

BASE = "/api/v1/admin/db-structure"


class _Audit:
    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def log(self, entry: Any) -> None:
        self.entries.append(entry)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(SRC, "mariadb", _tables())
    e.session.sql_rows = _sql_rows
    e.registry = make_registry({"db_id": SRC, "engine": "mariadb", "description": "가상 자산 DB"})
    return e


def _client(env: Any, audit: _Audit) -> TestClient:
    app = FastAPI()
    app.include_router(db_structure.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(auth=SimpleNamespace(enabled=False))
    app.state.audit_service = audit
    runner = AdminJobRunner(env.mgr._redis_cache)
    kwargs = env.service_kwargs()
    app.dependency_overrides[db_structure.get_structure_service] = lambda: DBStructureService(
        env.config, env.mgr, **kwargs
    )
    app.dependency_overrides[db_structure.get_registration_service] = (
        lambda: DBRegistrationService(env.config, env.mgr, **kwargs)
    )
    app.dependency_overrides[db_structure.get_asset_service] = lambda: AssetGenerationService(
        env.config, env.mgr, **kwargs
    )
    app.dependency_overrides[db_structure.get_admin_job_runner] = lambda: runner
    return TestClient(app)


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for _ in range(300):
        record = client.get(f"{BASE}/jobs/{job_id}").json()
        if record["status"] != "running":
            break
        client.portal.call(asyncio.sleep, 0.01)  # type: ignore[union-attr]
    return record


def test_profile_approve_rollback_through_api(env):
    audit = _Audit()
    with _client(env, audit) as client:
        schema_job = client.post(f"{BASE}/{SRC}/register", json={"steps": ["schema"]}).json()
        assert _wait(client, schema_job["job_id"])["status"] == "succeeded"

        started = client.post(f"{BASE}/{SRC}/assets/profile", json={"tables": None})
        assert started.status_code == 202 and started.json()["kind"] == "asset_profile"
        record = _wait(client, started.json()["job_id"])
        assert record["status"] == "succeeded", record
        draft_id = record["result"]["draft_id"]

        overview = client.get(f"{BASE}/{SRC}/assets").json()
        assert overview["drafts"][0]["draft_id"] == draft_id
        assert overview["files"]["seeds"]["exists"] is False

        approved = client.post(f"{BASE}/{SRC}/asset-drafts/{draft_id}/approve", json={
            "include": ["relationships", "seeds"], "reason": "확인",
        })
        assert approved.status_code == 200, approved.text
        assert approved.json()["applied"]["seeds"]["ver"] == 1
        files = client.get(f"{BASE}/{SRC}/assets").json()["files"]
        assert files["seeds"]["exists"] is True

        rolled = client.post(f"{BASE}/{SRC}/assets/seeds/versions/1/rollback", json={"reason": "r"})
        assert rolled.status_code == 200 and rolled.json()["version"]["kind"] == "rollback"

    actions = [e.extra["action"] for e in audit.entries]
    assert {"asset_profile", "get_assets", "approve_asset_draft", "rollback_asset"} <= set(actions)
    approve = next(e.extra for e in audit.entries if e.extra["action"] == "approve_asset_draft")
    assert approve["include"] == ["relationships", "seeds"]


@pytest.mark.parametrize(
    ("method", "path", "payload", "status"),
    [
        ("post", f"{SRC}/asset-drafts/nope/approve", {"include": ["seeds"]}, 404),
        ("post", f"{SRC}/asset-drafts/nope/approve", {"include": ["profile"]}, 422),
        ("post", f"{SRC}/asset-drafts/nope/approve", {"include": []}, 422),
        ("post", f"{SRC}/asset-drafts/nope/reject", {"reason": ""}, 404),
        ("post", f"{SRC}/assets/profile_x/versions/1/rollback", {"reason": ""}, 422),
    ],
)
def test_request_errors(env, method, path, payload, status):
    with _client(env, _Audit()) as client:
        assert getattr(client, method)(f"{BASE}/{path}", json=payload).status_code == status
