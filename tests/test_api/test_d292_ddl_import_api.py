"""D-292 — DDL 스키마 등록 API × 실제 등록 서비스 × 실제 잡 러너 × 인메모리 Redis 페이크.

- 미리보기 200 → 등록 202 → 잡 폴링 → 상세 기준선 출처 `ddl`
- 미리보기와 다른 DDL(해시 불일치)은 잡을 만들지 않고 422 · 목록 밖 소스 404 · 미지원 엔진 422
- 감사 기록에는 DDL 본문을 싣지 않는다(길이·테이블 수·해시만)
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
from src.schema_cache.db_registration_service import DBRegistrationService
from src.schema_cache.db_structure_service import DBStructureService
from tests.test_schema_cache.test_plan104_service_fixtures import (
    FakeTable,
    make_env,
    make_registry,
)

BASE = "/api/v1/admin/db-structure"
DDL = """
CREATE TABLE t_node (id integer PRIMARY KEY, name varchar(20));
CREATE TABLE t_leaf (id integer PRIMARY KEY, node_id integer REFERENCES t_node(id));
"""


class _CapturingAuditService:
    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def log(self, entry: Any) -> None:
        self.entries.append(entry)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(
        "app_pg", "postgresql", {"t_old": FakeTable([("id", "integer", False, True)])}
    )
    e.registry = make_registry(
        {"db_id": "app_pg", "engine": "postgresql", "description": "가상 DB"}
    )
    return e


def _client(env: Any, audit: _CapturingAuditService) -> TestClient:
    app = FastAPI()
    app.include_router(db_structure.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(auth=SimpleNamespace(enabled=False))
    app.state.audit_service = audit
    runner = AdminJobRunner(env.mgr._redis_cache)
    kwargs = env.service_kwargs()
    app.dependency_overrides[db_structure.get_structure_service] = lambda: DBStructureService(
        env.config, env.mgr, **kwargs
    )
    app.dependency_overrides[db_structure.get_registration_service] = lambda: DBRegistrationService(
        env.config, env.mgr, **kwargs
    )
    app.dependency_overrides[db_structure.get_admin_job_runner] = lambda: runner
    return TestClient(app)


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for _ in range(200):
        record = client.get(f"{BASE}/jobs/{job_id}").json()
        if record["status"] != "running":
            break
        client.portal.call(asyncio.sleep, 0.01)  # type: ignore[union-attr]
    return record


def test_preview_then_import_end_to_end(env):
    audit = _CapturingAuditService()
    with _client(env, audit) as client:
        preview = client.post(
            f"{BASE}/app_pg/ddl/preview", json={"engine": "postgresql", "text": DDL}
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["table_count"] == 2 and body["relationship_count"] == 1
        assert body["audit_logged"] is True

        started = client.post(f"{BASE}/app_pg/ddl/import", json={
            "engine": "postgresql", "text": DDL, "expected_hash": body["snapshot_hash"],
        })
        assert started.status_code == 202, started.text
        job = started.json()
        assert job["kind"] == "ddl_import"
        assert "text" not in job["params"] and job["params"]["table_count"] == 2
        record = _wait(client, job["job_id"])
        assert record["status"] == "succeeded", record
        assert record["result"]["steps"]["schema"]["status"] == "ok"

        detail = client.get(f"{BASE}/app_pg").json()
        assert detail["snapshot"]["origin"] == "ddl"
        assert detail["snapshot"]["hash"] == body["snapshot_hash"]
        assert detail["registration"]["schema"]["detail"]["origin"] == "ddl"

    extras = {e.extra["action"]: e.extra for e in audit.entries}
    assert extras["ddl_preview"]["text_length"] == len(DDL)
    assert extras["ddl_import"]["snapshot_hash"] == body["snapshot_hash"]
    for extra in extras.values():
        assert DDL not in str(extra)  # 본문은 감사 기록에 남기지 않는다


def test_import_rejects_changed_ddl_without_starting_job(env):
    with _client(env, _CapturingAuditService()) as client:
        preview = client.post(
            f"{BASE}/app_pg/ddl/preview", json={"engine": "postgresql", "text": DDL}
        )
        changed = DDL + "CREATE TABLE t_more (id int);"

        response = client.post(f"{BASE}/app_pg/ddl/import", json={
            "engine": "postgresql", "text": changed,
            "expected_hash": preview.json()["snapshot_hash"],
        })

        assert response.status_code == 422
        assert "다시 분석" in response.json()["detail"]
    assert asyncio.run(env.store.load_snapshot("app_pg")) is None


@pytest.mark.parametrize(
    ("path", "payload", "status"),
    [
        ("app_nowhere/ddl/preview", {"engine": "postgresql", "text": DDL}, 404),
        ("app_pg/ddl/preview", {"engine": "oracle", "text": DDL}, 422),
        ("app_pg/ddl/preview", {"engine": "postgresql", "text": ""}, 422),
        (
            "app_pg/ddl/import",
            {"engine": "postgresql", "text": DDL, "expected_hash": "not-hex"},
            422,
        ),
    ],
)
def test_request_validation(env, path, payload, status):
    with _client(env, _CapturingAuditService()) as client:
        assert client.post(f"{BASE}/{path}", json=payload).status_code == status
