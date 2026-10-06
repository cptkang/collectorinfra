"""plans/138 W3 — 테이블 정의 API(D-305 ①).

- 흐름: 가져오기 → 예상 호출 수 → LLM 묶음 초안 202 → 폴링 → 편집 저장 →
  승인(검증 실패 행 409 → 고친 뒤 200)
  × 실제 서비스 × 실제 잡 러너 × 인메모리 Redis 페이크 × 목 LLM · 감사 기록
- 요청 검증 422 · 관리자 인가(토큰 없음 401 · 사용자 role 403 · `type` 없는 admin 시크릿 토큰 401)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import db_structure
from src.api.server import create_app
from src.config import AdminConfig, AppConfig, AuthConfig
from src.schema_cache.admin_jobs import AdminJobRunner
from src.schema_cache.asset_generation_service import AssetGenerationService
from src.schema_cache.db_registration_service import DBRegistrationService
from src.schema_cache.db_structure_service import DBStructureService
from tests.test_schema_cache.test_plan104_service_fixtures import make_env, make_registry
from tests.test_schema_cache.test_plan138_table_definitions_asset import (
    SRC,
    DefinitionLLM,
    _wide_tables,
)

BASE = "/api/v1/admin/db-structure"
AUTH_SECRET = "plan138-auth-secret-0123456789abcdef"
ADMIN_SECRET = "plan138-admin-secret-0123456789abcdef"

IMPORT_TEXT = """
tables:
  t_alpha01:
    kind: 현행
    manages: 알파 1번을 관리한다.
    key_columns: [id]
  t_alpha02:
    manages: 알파 2번을 관리한다.
    key_columns: [nope]
"""


class _Audit:
    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def log(self, entry: Any) -> None:
        self.entries.append(entry)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(SRC, "mariadb", _wide_tables())
    e.registry = make_registry({"db_id": SRC, "engine": "mariadb", "description": "가상 업무 DB"})
    return e


def _client(env: Any, audit: _Audit, llm: DefinitionLLM) -> TestClient:
    app = FastAPI()
    app.include_router(db_structure.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(auth=SimpleNamespace(enabled=False))
    app.state.audit_service = audit
    runner = AdminJobRunner(env.mgr._redis_cache)
    kwargs = env.service_kwargs()
    app.dependency_overrides[db_structure.get_registration_service] = (
        lambda: DBRegistrationService(env.config, env.mgr, **kwargs)
    )
    asset_kwargs = {**kwargs, "llm_factory": lambda: llm}
    app.dependency_overrides[db_structure.get_asset_service] = lambda: AssetGenerationService(
        env.config, env.mgr, **asset_kwargs
    )
    app.dependency_overrides[db_structure.get_structure_service] = lambda: DBStructureService(
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


def test_import_llm_edit_approve_through_api(env):
    audit, llm = _Audit(), DefinitionLLM()
    with _client(env, audit, llm) as client:
        schema_job = client.post(f"{BASE}/{SRC}/register", json={"steps": ["schema"]}).json()
        assert _wait(client, schema_job["job_id"])["status"] == "succeeded"

        imported = client.post(f"{BASE}/{SRC}/table-definitions/import", json={"text": IMPORT_TEXT})
        assert imported.status_code == 200, imported.text
        assert imported.json()["summary"] == {"total": 2, "valid": 1, "invalid": 1}
        draft_id = imported.json()["draft_id"]
        drafts = f"{BASE}/{SRC}/asset-drafts/{draft_id}"

        estimate = client.get(f"{drafts}/table-definitions/estimate").json()
        assert (estimate["calls"], estimate["tables"]) == (2, 14)
        assert estimate["skipped"]["defined"] == 2 and llm.prompts == []

        started = client.post(f"{drafts}/table-definitions/llm", json={"only_failed": False})
        assert started.status_code == 202 and started.json()["kind"] == "asset_table_definitions"
        record = _wait(client, started.json()["job_id"])
        assert record["status"] == "succeeded", record
        assert record["result"]["llm_calls"] == 2 == len(llm.prompts)

        blocked = client.post(f"{drafts}/approve", json={
            "include": ["table_definitions"], "table_definition_tables": ["t_alpha01", "t_alpha02"],
        })
        assert blocked.status_code == 409

        bad_edit = client.put(f"{drafts}/table-definitions",
                              json={"edits": {"t_alpha02": {"manages": "{x}"}}})
        assert bad_edit.status_code == 422
        edited = client.put(f"{drafts}/table-definitions",
                            json={"edits": {"t_alpha02": {"key_columns": ["id"]}}})
        assert edited.status_code == 200, edited.text

        approved = client.post(f"{drafts}/approve", json={
            "include": ["table_definitions"],
            "table_definition_tables": ["t_alpha01", "t_alpha02", "t_alpha03"],
            "reason": "검토 완료",
        })
        assert approved.status_code == 200, approved.text
        overview = client.get(f"{BASE}/{SRC}/assets").json()
        assert overview["table_definitions"]["approved"] == 3

    profile = env.store.read_current_profile(SRC)["profile"]
    assert profile["table_definitions"]["t_alpha02"] == {
        "manages": "알파 2번을 관리한다.", "key_columns": ["id"], "origin": "manual",
    }
    assert profile["table_definitions"]["t_alpha03"]["origin"] == "llm"
    actions = [e.extra["action"] for e in audit.entries]
    assert {
        "import_table_definitions", "estimate_table_definition_llm", "asset_table_definitions",
        "update_table_definitions", "approve_asset_draft",
    } <= set(actions)
    approve = [e.extra for e in audit.entries if e.extra["action"] == "approve_asset_draft"][-1]
    assert approve["table_definition_tables"] == ["t_alpha01", "t_alpha02", "t_alpha03"]


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", f"{SRC}/table-definitions/import", {"text": ""}),
        ("post", f"{SRC}/table-definitions/import", {"text": "x" * 1_000_001}),
        ("put", f"{SRC}/asset-drafts/d1/table-definitions", {"edits": {}}),
        ("put", f"{SRC}/asset-drafts/d1/table-definitions",
         {"edits": {"t": {"key_columns": "id"}}}),
        ("post", f"{SRC}/asset-drafts/d1/approve",
         {"include": ["table_definitions"], "table_definition_tables": "t"}),
    ],
)
def test_request_validation_422(env, method, path, payload):
    with _client(env, _Audit(), DefinitionLLM()) as client:
        response = getattr(client, method)(f"{BASE}/{path}", json=payload)
        assert response.status_code == 422


def test_unknown_draft_404(env):
    with _client(env, _Audit(), DefinitionLLM()) as client:
        assert client.get(f"{BASE}/{SRC}/asset-drafts/nope/table-definitions/estimate"
                          ).status_code == 404
        assert client.put(f"{BASE}/{SRC}/asset-drafts/nope/table-definitions",
                          json={"edits": {"t": {"manages": "x"}}}).status_code == 404


# ─── 관리자 인가 ─────────────────────────────────────────────────


def _exp() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def _token(claims: dict[str, Any], secret: str) -> str:
    return jwt.encode({**claims, "exp": _exp()}, secret, algorithm="HS256")


class _FakeAssetService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def import_table_definitions(self, source: str, text: str, *, by: Any) -> dict:
        self.calls.append("import")
        return {"source": source, "draft_id": "d1", "summary": {}}

    async def estimate_table_definition_llm(self, source: str, draft_id: str, *,
                                            only_failed: bool = False) -> dict:
        self.calls.append("estimate")
        return {"calls": 0}

    async def run_table_definition_llm(self, source: str, draft_id: str, *, only_failed: bool,
                                       by: Any, ctx: Any) -> dict:
        self.calls.append("llm")
        return {"llm_calls": 0}

    async def update_table_definitions(self, source: str, draft_id: str, edits: dict, *,
                                       by: Any) -> dict:
        self.calls.append("update")
        return {"updated": sorted(edits)}

    async def approve_asset_draft(self, source: str, draft_id: str, **kwargs: Any) -> dict:
        self.calls.append("approve")
        return {"applied": {}}


class _FakeRunner:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def start(self, *, kind: str, db_id: str, by: Any, params: dict, work: Any) -> dict:
        await work(object())
        self.started.append(kind)
        return {"job_id": "j1", "kind": kind, "db_id": db_id, "status": "running"}


ENDPOINTS: list[tuple[str, str, Any]] = [
    ("post", f"{BASE}/src_a/table-definitions/import", {"text": "tables: {}"}),
    ("get", f"{BASE}/src_a/asset-drafts/d1/table-definitions/estimate", None),
    ("post", f"{BASE}/src_a/asset-drafts/d1/table-definitions/llm", {"only_failed": True}),
    ("put", f"{BASE}/src_a/asset-drafts/d1/table-definitions",
     {"edits": {"t": {"manages": "x"}}}),
    ("post", f"{BASE}/src_a/asset-drafts/d1/approve",
     {"include": ["table_definitions"], "table_definition_tables": ["t"]}),
]


@pytest.fixture(scope="module")
def auth_app():
    """인증 켠 앱(모듈당 1회) — lifespan은 띄우지 않는다."""
    return create_app(AppConfig(
        _env_file=None,
        auth=AuthConfig(_env_file=None, enabled=True, jwt_secret=AUTH_SECRET),
        admin=AdminConfig(_env_file=None, jwt_secret=ADMIN_SECRET),
    ))


@pytest.fixture
def harness(auth_app):
    service, runner, audit = _FakeAssetService(), _FakeRunner(), _Audit()
    auth_app.dependency_overrides[db_structure.get_asset_service] = lambda: service
    auth_app.dependency_overrides[db_structure.get_admin_job_runner] = lambda: runner
    auth_app.state.audit_service = audit
    yield TestClient(auth_app), service, runner, audit
    auth_app.dependency_overrides.clear()
    auth_app.state.audit_service = None


def _send(client: TestClient, method: str, path: str, body: Any, token: str | None):
    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    if token is not None:
        kwargs["headers"] = {"Authorization": f"Bearer {token}"}
    return getattr(client, method)(path, **kwargs)


REJECTED = [
    pytest.param(None, 401, id="no-token"),
    pytest.param(_token({"sub": "u", "role": "user", "type": "user"}, AUTH_SECRET), 403,
                 id="user-role"),
    pytest.param(_token({"sub": "ops", "role": "admin"}, ADMIN_SECRET), 401,
                 id="admin-secret-without-type"),
    pytest.param(_token({"sub": "ops", "role": "admin", "type": "user"}, ADMIN_SECRET), 401,
                 id="admin-secret-user-type"),
]


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
@pytest.mark.parametrize(("token", "status"), REJECTED)
def test_rejected_tokens_never_reach_service(harness, method, path, body, token, status):
    client, service, runner, audit = harness
    assert _send(client, method, path, body, token).status_code == status
    assert service.calls == [] and runner.started == [] and audit.entries == []


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
@pytest.mark.parametrize("token", [
    pytest.param(_token({"sub": "ops", "type": "admin"}, ADMIN_SECRET), id="break-glass"),
    pytest.param(_token({"sub": "a", "role": "admin", "type": "user"}, AUTH_SECRET),
                 id="admin-role"),
])
def test_admin_allowed_and_audited(harness, method, path, body, token):
    client, service, _runner, audit = harness
    response = _send(client, method, path, body, token)
    assert response.status_code in (200, 202), response.text
    assert len(service.calls) == 1 and len(audit.entries) == 1
    assert audit.entries[0].event == "admin_action"
    assert response.json()["audit_logged"] is True
