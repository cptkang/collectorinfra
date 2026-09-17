"""plans/104 — API × 실제 서비스(구조·등록) × 인메모리 Redis 페이크.

목은 MCP 세션·레지스트리뿐이다(서비스 테스트 공용 픽스처 재사용 — 네트워크·실 Redis·LLM 0).
- 소스 목록 API가 MCP × 레지스트리 × ACTIVE_DB_IDS 대조를 그대로 내보낸다
  (MCP에만 있음 · 앱에만 있음 ·
  활성인데 구조 없음 · 준비도 요약).
- 변경 점검 잡을 실제 잡 러너로 돌려 202 → 폴링 → 상세의 마지막 점검까지 이어진다.
- 설정 저장 B-7이 실제 등록 서비스의 준비도 판정으로 필수 미충족 경고를 싣는다.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import db_structure
from src.api.routes.admin import EnvUpdateRequest, update_settings
from src.schema_cache.admin_jobs import AdminJobRunner
from src.schema_cache.db_registration_service import DBRegistrationService
from src.schema_cache.db_structure_service import DBStructureService
from tests.test_schema_cache.test_plan104_service_fixtures import (
    FakeTable,
    make_env,
    make_registry,
)

BASE = "/api/v1/admin/db-structure"


class _CapturingAuditService:
    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def log(self, entry: Any) -> None:
        self.entries.append(entry)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = make_env(tmp_path, monkeypatch, active=("app_active", "app_ghost"))
    e.session.add_source("app_new", "mariadb", {"t_item": FakeTable([("id", "int", False, True)])})
    e.session.add_source(
        "app_active", "postgresql", {"t_node": FakeTable([("id", "integer", False, True)])}
    )
    e.registry = make_registry(
        {"db_id": "app_active", "engine": "postgresql", "description": "가상 활성 DB"},
        {"db_id": "app_ghost", "engine": "postgresql", "description": "MCP에 없는 DB"},
    )
    return e


def _app(env: Any, audit: _CapturingAuditService) -> FastAPI:
    app = FastAPI()
    app.include_router(db_structure.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(auth=SimpleNamespace(enabled=False))
    app.state.audit_service = audit
    runner = AdminJobRunner(env.mgr._redis_cache)
    app.dependency_overrides[db_structure.get_structure_service] = lambda: DBStructureService(
        env.config, env.mgr, **env.service_kwargs()
    )
    app.dependency_overrides[db_structure.get_admin_job_runner] = lambda: runner
    return app


def test_list_sources_through_api_with_real_service(env):
    audit = _CapturingAuditService()
    response = TestClient(_app(env, audit)).get(f"{BASE}/sources")

    assert response.status_code == 200
    body = response.json()
    assert body["mcp_available"] is True and body["audit_logged"] is True
    rows = {row["source"]: row for row in body["sources"]}
    assert set(rows) == {"app_new", "app_active", "app_ghost"}

    assert rows["app_new"]["mismatch"] == "mcp_only"
    assert rows["app_new"]["active"] is False
    assert rows["app_new"]["health"]["healthy"] is True

    assert rows["app_active"]["mismatch"] is None
    assert rows["app_active"]["structure"]["status"] == "none"
    assert rows["app_active"]["structure"]["warning"] == "active_without_structure"

    assert rows["app_ghost"]["mismatch"] == "app_only"
    assert rows["app_ghost"]["health"] is None  # MCP에 없는 소스는 연결 확인하지 않는다

    for row in rows.values():
        readiness = row["readiness"]
        assert readiness["summary"].startswith("필수 ")
        assert readiness["ready"] is False
        assert readiness["unmet_required"]  # 목록에서 미충족 항목을 맨 위에 보일 재료
    assert [e.extra for e in audit.entries] == [{"action": "list_sources"}]


def test_check_job_end_to_end_through_api(env):
    """202 → 잡 폴링 → 상세의 마지막 점검(기준선)까지 실제 러너·서비스로 이어진다."""
    audit = _CapturingAuditService()
    with TestClient(_app(env, audit)) as client:
        started = client.post(f"{BASE}/app_new/check")
        assert started.status_code == 202
        job_id = started.json()["job_id"]
        record: dict = {}
        for _ in range(200):
            record = client.get(f"{BASE}/jobs/{job_id}").json()
            if record["status"] != "running":
                break
            client.portal.call(asyncio.sleep, 0.01)  # type: ignore[union-attr]
        assert record["status"] == "succeeded", record
        assert record["result"]["table_count"] == 1

        detail = client.get(f"{BASE}/app_new").json()
        assert detail["last_check"]["diff"]["baseline"] is True
        assert detail["snapshot"]["table_count"] == 1

    actions = [e.extra["action"] for e in audit.entries]
    assert actions[0] == "check" and actions[-1] == "get_detail"


async def test_settings_readiness_warning_with_real_registration_service(
    env, monkeypatch, tmp_path
):
    """B-7: 구조·캐시 없는 MCP 소스를 ACTIVE_DB_IDS에 넣으면 실제 판정의 필수 미충족이 실린다."""
    env_file = tmp_path / "settings.env"
    env_file.write_text("ACTIVE_DB_IDS=app_active\n", encoding="utf-8")
    monkeypatch.setattr("src.api.routes.admin._ENV_FILE", env_file)
    monkeypatch.setattr(
        db_structure,
        "build_registration_service",
        lambda config: DBRegistrationService(env.config, env.mgr, **env.service_kwargs()),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(config=env.config, audit_service=None, audit_repo=None)
        ),
        state=SimpleNamespace(client_ip=None, request_id=None),
    )

    response = await update_settings(
        request,
        EnvUpdateRequest(settings={"ACTIVE_DB_IDS": "app_active,app_new"}),
        {"sub": "admin"},
    )

    assert "ACTIVE_DB_IDS=app_active,app_new" in env_file.read_text(encoding="utf-8")
    assert list(response.readiness_warnings) == ["app_new"]
    codes = {item["code"] for item in response.readiness_warnings["app_new"]}
    assert "C2" in codes  # 레지스트리 미등록
    assert all(
        set(item) == {"code", "label", "detail"} for item in response.readiness_warnings["app_new"]
    )
