"""plans/104 — 관리자 「DB 구조」 API (`src/api/routes/db_structure.py`).

검증: RBAC(비로그인 401 · 비관리자 403 · 관리자/break-glass 200 · 개발 모드 우회) ·
고정 경로 순서 ·
잡 202·충돌 409 · 저장소 미가용 503 · 초안 승인 불가 409/없음 404 · 형식 422 ·
ADMIN_ACTION 감사 extra ·
diff-report YAML·감사 헤더.

서비스·잡 러너는 `dependency_overrides`로 가짜를 넣는다(실 MCP·Redis·LLM 0 — D-127).
앱은 `create_app(config)`로 만들되 lifespan은 띄우지 않는다(`TestClient`를 with 없이 사용).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from src.api.routes import db_structure
from src.api.server import create_app
from src.config import AdminConfig, AppConfig, AuthConfig
from src.schema_cache.admin_jobs import JobConflictError
from src.schema_cache.db_structure_service import DraftNotApprovable
from src.schema_cache.structure_store import StructureStoreUnavailable

AUTH_SECRET = "plan104-auth-secret-0123456789abcdef"
ADMIN_SECRET = "plan104-admin-secret-0123456789abcdef"


def _exp() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def _user_token(role: str) -> str:
    return jwt.encode(
        {"sub": f"{role}-user", "name": "U", "role": role, "type": "user", "exp": _exp()},
        AUTH_SECRET,
        algorithm="HS256",
    )


def _break_glass_token() -> str:
    return jwt.encode(
        {"sub": "ops", "type": "admin", "exp": _exp()}, ADMIN_SECRET, algorithm="HS256"
    )


def _config(auth_enabled: bool = True) -> AppConfig:
    return AppConfig(
        _env_file=None,
        auth=AuthConfig(_env_file=None, enabled=auth_enabled, jwt_secret=AUTH_SECRET),
        admin=AdminConfig(_env_file=None, jwt_secret=ADMIN_SECRET),
    )


class _CapturingAuditService:
    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def log(self, entry: Any) -> None:
        self.entries.append(entry)


class _FakeStructureService:
    """DBStructureService 대역 — 호출을 기록하고 설정된 예외를 던진다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.raise_on: dict[str, Exception] = {}

    def _hit(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))
        if name in self.raise_on:
            raise self.raise_on[name]

    async def list_sources(self) -> dict:
        self._hit("list_sources")
        return {
            "mcp_available": True,
            "mcp_error": None,
            "env": "http://mcp",
            "local_sandbox": False,
            "sources": [],
        }

    async def get_detail(self, source: str) -> dict:
        self._hit("get_detail", source)
        return {"source": source, "drafts": [], "versions": []}

    async def run_check(self, source: str, *, by, code_columns, ctx) -> dict:
        self._hit("run_check", source, by=by, code_columns=code_columns, ctx=ctx)
        return {"source": source}

    async def run_analyze(self, source: str, *, scope, tables, code_columns, by, ctx) -> dict:
        self._hit(
            "run_analyze",
            source,
            scope=scope,
            tables=tables,
            code_columns=code_columns,
            by=by,
            ctx=ctx,
        )
        return {"source": source}

    async def legacy_structure_meta(self, source: str) -> dict:
        self._hit("legacy_structure_meta", source)
        return {"patterns": [], "query_guide": "레거시 안내"}

    async def run_legacy_draft(self, source: str, *, by, ctx) -> dict:
        self._hit("run_legacy_draft", source, by=by, ctx=ctx)
        return {"source": source, "scope": "legacy"}

    async def approve_draft(self, source: str, draft_id: str, *, by, reason) -> dict:
        self._hit("approve_draft", source, draft_id, by=by, reason=reason)
        return {
            "source": source,
            "draft_id": draft_id,
            "version": {"ver": 3},
            "field_diff": [],
            "recomputed": False,
        }

    async def reject_draft(self, source: str, draft_id: str, *, by, reason) -> dict:
        self._hit("reject_draft", source, draft_id, by=by, reason=reason)
        return {"source": source, "draft_id": draft_id, "status": "rejected"}

    async def rollback(self, source: str, ver: int, *, by, reason) -> dict:
        self._hit("rollback", source, ver, by=by, reason=reason)
        return {"source": source, "rolled_back_to": ver, "version": {"ver": 5}}

    async def diff_report(self, source: str, draft_id: str) -> str:
        self._hit("diff_report", source, draft_id)
        return "# 보고서\nfield_diff: []\n"


class _FakeRegistrationService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    async def get_registration(self, source: str) -> dict:
        self.calls.append(("get_registration", (source,), {}))
        return {"steps": {}, "readiness": {}, "estimate": {}, "server_variables": None}

    async def run_register(self, source: str, *, steps, tables, by, ctx) -> dict:
        self.calls.append(("run_register", (source,), {"steps": steps, "tables": tables, "by": by}))
        return {"source": source}

    async def apply_description_draft(self, source, draft_id, *, exclude_tables, by) -> dict:
        self.calls.append(
            (
                "apply_description_draft",
                (source, draft_id),
                {"exclude_tables": exclude_tables, "by": by},
            )
        )
        return {"source": source, "draft_id": draft_id, "status": "applied"}

    async def discard_description_draft(self, source, draft_id, *, by) -> dict:
        self.calls.append(("discard_description_draft", (source, draft_id), {"by": by}))
        return {"source": source, "draft_id": draft_id, "status": "discarded"}

    async def set_db_description(self, source, *, text, origin, by) -> dict:
        self.calls.append(
            ("set_db_description", (source,), {"text": text, "origin": origin, "by": by})
        )
        return {"source": source, "origin": origin}

    async def config_snippets(self, source: str) -> dict:
        self.calls.append(("config_snippets", (source,), {}))
        return {"registry_yaml": "db_id: x\n", "local_sandbox": False, "notes": []}


class _FakeRunner:
    """AdminJobRunner 대역 — work를 즉시 실행해 인자 전달을 확인한다."""

    def __init__(self) -> None:
        self.started: list[dict] = []
        self.records: dict[str, dict] = {
            "job1": {"job_id": "job1", "db_id": "src_a", "status": "running"}
        }
        self.raise_on_start: Exception | None = None

    async def start(self, *, kind, db_id, by, params, work) -> dict:
        if self.raise_on_start is not None:
            raise self.raise_on_start
        ctx = object()
        result = await work(ctx)
        self.started.append(
            {"kind": kind, "db_id": db_id, "by": by, "params": params, "result": result, "ctx": ctx}
        )
        return {"job_id": f"job-{kind}", "kind": kind, "db_id": db_id, "status": "running"}

    async def get(self, job_id: str) -> dict | None:
        return self.records.get(job_id)


@pytest.fixture(scope="module")
def auth_app():
    """인증 켠 앱(모듈당 1회 생성 — AppConfig 구성 비용 절감). lifespan은 띄우지 않는다."""
    return create_app(_config(auth_enabled=True))


@pytest.fixture
def harness(auth_app):
    """인증 켠 앱 + 테스트마다 새 가짜 서비스·러너·감사."""
    app = auth_app
    structure, registration, runner, audit = (
        _FakeStructureService(),
        _FakeRegistrationService(),
        _FakeRunner(),
        _CapturingAuditService(),
    )
    app.dependency_overrides[db_structure.get_structure_service] = lambda: structure
    app.dependency_overrides[db_structure.get_registration_service] = lambda: registration
    app.dependency_overrides[db_structure.get_admin_job_runner] = lambda: runner
    app.state.audit_service = audit
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {_user_token('admin')}"})
    yield client, structure, registration, runner, audit
    app.dependency_overrides.clear()
    app.state.audit_service = None


BASE = "/api/v1/admin/db-structure"

# (메서드, 경로, 본문) — 계약 §5 표 전부
ENDPOINTS: list[tuple[str, str, Any]] = [
    ("get", f"{BASE}/sources", None),
    ("get", f"{BASE}/jobs/job1", None),
    ("get", f"{BASE}/src_a", None),
    ("post", f"{BASE}/src_a/check", {"code_columns": ["t.c"]}),
    ("post", f"{BASE}/src_a/analyze", {"scope": "all"}),
    ("post", f"{BASE}/src_a/legacy/draft", None),
    ("post", f"{BASE}/src_a/drafts/d1/approve", {"reason": "ok"}),
    ("post", f"{BASE}/src_a/drafts/d1/reject", {"reason": "no"}),
    ("post", f"{BASE}/src_a/versions/2/rollback", {"reason": "back"}),
    ("get", f"{BASE}/src_a/diff-report?draft_id=d1", None),
    ("post", f"{BASE}/src_a/register", {"steps": ["probe", "schema"]}),
    ("get", f"{BASE}/src_a/registration", None),
    ("post", f"{BASE}/src_a/description-drafts/d1/apply", {"exclude_tables": ["t"]}),
    ("post", f"{BASE}/src_a/description-drafts/d1/discard", None),
    ("put", f"{BASE}/src_a/db-description", {"text": "설명", "origin": "manual"}),
    ("get", f"{BASE}/src_a/config-snippets", None),
]


def _send(client: TestClient, method: str, path: str, body: Any, headers: dict | None = None):
    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    if headers is not None:
        kwargs["headers"] = headers
    return getattr(client, method)(path, **kwargs)


# ─── RBAC ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_rbac_unauthenticated_401(harness, method, path, body):
    """로그인 안 한 첫 방문자(토큰 없음)는 모든 새 API에서 401."""
    client, structure, registration, runner, audit = harness
    client.headers.pop("Authorization")
    response = _send(client, method, path, body)
    assert response.status_code == 401
    assert structure.calls == [] and registration.calls == [] and runner.started == []
    assert audit.entries == []


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_rbac_non_admin_403(harness, method, path, body):
    """사용자 토큰 role=user는 403 — UI 게이트가 아니라 API가 막는다."""
    client, structure, registration, runner, audit = harness
    response = _send(
        client, method, path, body, headers={"Authorization": f"Bearer {_user_token('user')}"}
    )
    assert response.status_code == 403
    assert structure.calls == [] and registration.calls == [] and runner.started == []


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_rbac_admin_allowed_and_audited(harness, method, path, body):
    """관리자는 통과하고, 모든 엔드포인트가 ADMIN_ACTION 감사 1행 + audit_logged를 남긴다."""
    client, _structure, _registration, _runner, audit = harness
    response = _send(client, method, path, body)

    assert response.status_code in (200, 202), response.text
    if "/jobs/" in path:
        # 잡 상태 폴링은 감사하지 않는다(시작 엔드포인트가 감사) — 아래 전용 테스트
        assert audit.entries == []
        return
    assert len(audit.entries) == 1
    assert audit.entries[0].event == "admin_action"
    assert audit.entries[0].user_id == "admin-user"
    if "diff-report" in path:
        assert response.headers["X-Audit-Logged"] == "true"
    else:
        assert response.json()["audit_logged"] is True


def test_rbac_break_glass_admin_token_allowed(harness):
    client, structure, *_ = harness
    response = client.get(
        f"{BASE}/sources", headers={"Authorization": f"Bearer {_break_glass_token()}"}
    )
    assert response.status_code == 200
    assert structure.calls[0][0] == "list_sources"


def test_rbac_user_token_signed_with_admin_secret_is_rejected(harness):
    """D-070: 사용자 토큰을 admin 시크릿으로 서명해도 type!=admin이면 break-glass가 아니다."""
    client, *_ = harness
    forged = jwt.encode(
        {"sub": "x", "role": "admin", "type": "user", "exp": _exp()},
        ADMIN_SECRET,
        algorithm="HS256",
    )
    response = client.get(f"{BASE}/sources", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_dev_mode_bypass_without_token():
    """AUTH_ENABLED=false(개발 모드)면 기존 관리자 API와 같이 우회한다."""
    app = create_app(_config(auth_enabled=False))
    structure = _FakeStructureService()
    app.dependency_overrides[db_structure.get_structure_service] = lambda: structure
    response = TestClient(app).get(f"{BASE}/sources")
    assert response.status_code == 200
    assert response.json()["audit_logged"] is False  # 감사 저장소 미구성은 응답에 드러난다


# ─── 경로 순서 · 입력 검증 ───────────────────────────────────────


def test_fixed_paths_are_declared_before_source_paths():
    paths = [route.path for route in db_structure.router.routes]
    first_source = min(
        i for i, p in enumerate(paths) if p.startswith("/admin/db-structure/{source}")
    )
    assert paths.index("/admin/db-structure/sources") < first_source
    assert paths.index("/admin/db-structure/jobs/{job_id}") < first_source


def test_sources_path_is_not_captured_as_source_detail(harness):
    client, structure, *_ = harness
    response = client.get(f"{BASE}/sources")
    assert response.status_code == 200
    assert [c[0] for c in structure.calls] == ["list_sources"]


def test_invalid_source_name_422(harness):
    client, structure, *_ = harness
    response = client.get(f"{BASE}/bad.name")
    assert response.status_code == 422
    assert structure.calls == []


def test_analyze_tables_scope_requires_tables_422(harness):
    client, _s, _r, runner, audit = harness
    response = client.post(f"{BASE}/src_a/analyze", json={"scope": "tables"})
    assert response.status_code == 422
    assert runner.started == [] and audit.entries == []


def test_register_rejects_unknown_step_422(harness):
    client, _s, _r, runner, _a = harness
    response = client.post(f"{BASE}/src_a/register", json={"steps": ["drop_everything"]})
    assert response.status_code == 422
    assert runner.started == []


def test_db_description_origin_must_be_manual_or_llm(harness):
    client, _s, registration, _r, _a = harness
    response = client.put(f"{BASE}/src_a/db-description", json={"text": "x", "origin": "auto"})
    assert response.status_code == 422
    assert registration.calls == []


# ─── 잡 ─────────────────────────────────────────────────────────


def test_check_job_returns_202_and_passes_arguments(harness):
    client, structure, _r, runner, audit = harness
    response = client.post(f"{BASE}/src_a/check", json={"code_columns": ["t.c"]})

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-check"
    started = runner.started[0]
    assert (started["kind"], started["db_id"], started["by"]) == ("check", "src_a", "admin-user")
    assert started["params"] == {"code_columns": ["t.c"]}
    name, args, kwargs = structure.calls[0]
    assert (name, args) == ("run_check", ("src_a",))
    assert kwargs["code_columns"] == ["t.c"] and kwargs["ctx"] is started["ctx"]
    assert audit.entries[0].extra == {
        "action": "check",
        "source": "src_a",
        "job_id": "job-check",
        "code_columns": ["t.c"],
    }


def test_check_job_without_body(harness):
    client, structure, _r, runner, _a = harness
    response = client.post(f"{BASE}/src_a/check")
    assert response.status_code == 202
    assert runner.started[0]["params"] == {"code_columns": None}


def test_analyze_and_register_jobs_pass_arguments(harness):
    client, structure, registration, runner, _a = harness
    assert (
        client.post(
            f"{BASE}/src_a/analyze",
            json={"scope": "tables", "tables": ["t1"], "code_columns": ["t1.k"]},
        ).status_code
        == 202
    )
    assert (
        client.post(
            f"{BASE}/src_a/register",
            json={"steps": ["schema", "schema", "probe"], "tables": ["t1"]},
        ).status_code
        == 202
    )

    analyze_call = structure.calls[0]
    assert analyze_call[2]["scope"] == "tables" and analyze_call[2]["tables"] == ["t1"]
    assert analyze_call[2]["code_columns"] == ["t1.k"]
    assert registration.calls[0] == (
        "run_register",
        ("src_a",),
        {"steps": ["schema", "probe"], "tables": ["t1"], "by": "admin-user"},
    )
    assert [s["kind"] for s in runner.started] == ["analyze", "register"]


def test_legacy_draft_job_202_arguments_and_audit(harness):
    """레거시 분석본 초안 잡 — 후보 확인 뒤 kind=legacy_draft 잡 · ADMIN_ACTION 감사."""
    client, structure, _r, runner, audit = harness
    response = client.post(f"{BASE}/src_a/legacy/draft")

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-legacy_draft"
    assert response.json()["audit_logged"] is True
    assert [c[0] for c in structure.calls] == ["legacy_structure_meta", "run_legacy_draft"]
    started = runner.started[0]
    assert (started["kind"], started["db_id"], started["by"]) == (
        "legacy_draft", "src_a", "admin-user"
    )
    assert structure.calls[1][2] == {"by": "admin-user", "ctx": started["ctx"]}
    assert audit.entries[0].extra == {
        "action": "legacy_draft", "source": "src_a", "job_id": "job-legacy_draft",
    }


def test_legacy_draft_not_candidate_404_without_job(harness):
    """후보가 아니면 잡을 만들지 않고 404 · 감사 행 없음."""
    client, structure, _r, runner, audit = harness
    structure.raise_on["legacy_structure_meta"] = DraftNotApprovable(
        "legacy_not_found", "승인 대기 레거시 분석본이 없습니다"
    )
    response = client.post(f"{BASE}/src_a/legacy/draft")
    assert response.status_code == 404
    assert runner.started == [] and audit.entries == []


def test_legacy_draft_store_unavailable_503(harness):
    client, structure, _r, runner, _a = harness
    structure.raise_on["legacy_structure_meta"] = StructureStoreUnavailable("Redis 미연결")
    assert client.post(f"{BASE}/src_a/legacy/draft").status_code == 503
    assert runner.started == []


def test_job_conflict_409(harness):
    client, _s, _r, runner, audit = harness
    runner.raise_on_start = JobConflictError("src_a에 진행 중인 관리자 작업이 있습니다(job_id=x)")
    response = client.post(f"{BASE}/src_a/check")
    assert response.status_code == 409
    assert "진행 중" in response.json()["detail"]
    assert audit.entries == []


def test_job_store_unavailable_503(harness):
    client, _s, _r, runner, _a = harness
    runner.raise_on_start = StructureStoreUnavailable("Redis에 연결할 수 없어")
    response = client.post(f"{BASE}/src_a/register", json={"steps": ["probe"]})
    assert response.status_code == 503


def test_job_get_unknown_404(harness):
    client, *_ = harness
    response = client.get(f"{BASE}/jobs/nope")
    assert response.status_code == 404


def test_job_get_polling_is_not_audited(harness):
    """잡 상태 폴링은 감사 로그를 남기지 않는다 — 잡 시작이 이미 감사된다."""
    client, _s, _r, _runner, audit = harness
    response = client.get(f"{BASE}/jobs/job1")
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert "audit_logged" not in response.json()
    assert audit.entries == []


# ─── 승인·반려·되돌리기 예외 매핑 ────────────────────────────────


@pytest.mark.parametrize(
    ("code", "status"),
    [("not_pending", 409), ("validation_failed", 409), ("env_mismatch", 409), ("not_found", 404)],
)
def test_approve_draft_not_approvable_mapping(harness, code, status):
    client, structure, _r, _runner, audit = harness
    structure.raise_on["approve_draft"] = DraftNotApprovable(code, f"승인 불가: {code}")
    response = client.post(f"{BASE}/src_a/drafts/d1/approve", json={"reason": "r"})
    assert response.status_code == status
    assert response.json()["detail"] == f"승인 불가: {code}"
    assert audit.entries == []  # 실패한 작업은 감사 행을 남기지 않는다


def test_rollback_missing_version_404(harness):
    client, structure, *_ = harness
    structure.raise_on["rollback"] = DraftNotApprovable("version_not_found", "버전이 없습니다: v9")
    assert client.post(f"{BASE}/src_a/versions/9/rollback", json={"reason": ""}).status_code == 404


def test_store_unavailable_on_approve_503(harness):
    client, structure, *_ = harness
    structure.raise_on["approve_draft"] = StructureStoreUnavailable("Redis 미연결")
    assert client.post(f"{BASE}/src_a/drafts/d1/approve").status_code == 503


def test_service_value_error_422(harness):
    client, structure, *_ = harness
    structure.raise_on["get_detail"] = ValueError("db_id 형식 오류")
    assert client.get(f"{BASE}/src_a").status_code == 422


def test_unmapped_error_is_not_swallowed(harness):
    """매핑 대상이 아닌 예외는 500으로 드러난다(침묵 금지)."""
    app_client, structure, *_ = harness
    structure.raise_on["get_detail"] = RuntimeError("boom")
    client = TestClient(app_client.app, raise_server_exceptions=False)
    response = client.get(
        f"{BASE}/src_a", headers={"Authorization": f"Bearer {_user_token('admin')}"}
    )
    assert response.status_code == 500


def test_approve_audit_extra_records_version_and_reason(harness):
    client, structure, _r, _runner, audit = harness
    response = client.post(f"{BASE}/src_a/drafts/d1/approve", json={"reason": "검토 완료"})
    assert response.status_code == 200
    assert structure.calls[0] == (
        "approve_draft",
        ("src_a", "d1"),
        {"by": "admin-user", "reason": "검토 완료"},
    )
    assert audit.entries[0].extra == {
        "action": "approve_draft",
        "source": "src_a",
        "draft_id": "d1",
        "reason": "검토 완료",
        "ver": 3,
    }


def test_rollback_audit_extra(harness):
    client, _structure, _r, _runner, audit = harness
    assert (
        client.post(f"{BASE}/src_a/versions/2/rollback", json={"reason": "되돌림"}).status_code
        == 200
    )
    assert audit.entries[0].extra == {
        "action": "rollback",
        "source": "src_a",
        "ver": 2,
        "reason": "되돌림",
        "new_ver": 5,
    }


def test_reason_body_is_optional(harness):
    client, structure, *_ = harness
    assert client.post(f"{BASE}/src_a/drafts/d1/reject").status_code == 200
    assert structure.calls[0][2]["reason"] == ""


def test_diff_report_is_yaml_download(harness):
    client, *_ = harness
    response = client.get(f"{BASE}/src_a/diff-report", params={"draft_id": "d1"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/yaml")
    assert 'filename="src_a-d1-diff.yaml"' in response.headers["content-disposition"]
    assert response.text.startswith("# 보고서")


def test_diff_report_requires_draft_id(harness):
    client, *_ = harness
    assert client.get(f"{BASE}/src_a/diff-report").status_code == 422


def test_set_db_description_audit_does_not_store_text(harness):
    client, _s, registration, _r, audit = harness
    response = client.put(f"{BASE}/src_a/db-description", json={"text": "긴 설명", "origin": "llm"})
    assert response.status_code == 200
    assert registration.calls[0] == (
        "set_db_description",
        ("src_a",),
        {"text": "긴 설명", "origin": "llm", "by": "admin-user"},
    )
    assert audit.entries[0].extra == {
        "action": "set_db_description",
        "source": "src_a",
        "origin": "llm",
        "text_length": 4,
    }


def test_real_job_runner_rejects_second_job_with_409():
    """실제 AdminJobRunner(인메모리 Redis 페이크)로 같은 소스 동시 잡이 409가 되는지 확인한다.

    백그라운드 태스크가 요청 사이에 살아 있도록 `with TestClient`를 쓰되,
    본체 앱(lifespan이 Redis·그래프를
    띄운다)이 아니라 라우터만 올린 최소 앱을 쓴다 — 외부 연결 0.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from fastapi import FastAPI

    from src.schema_cache.admin_jobs import AdminJobRunner
    from src.schema_cache.redis_cache import RedisSchemaCache
    from tests.mocks.async_redis import attach_fake_redis

    redis_cache = RedisSchemaCache(MagicMock())
    attach_fake_redis(redis_cache)
    runner = AdminJobRunner(redis_cache)
    gate: dict[str, asyncio.Event] = {}

    class _SlowService(_FakeStructureService):
        async def run_check(self, source, *, by, code_columns, ctx):
            await gate["release"].wait()
            return {"source": source, "by": by}

    app = FastAPI()
    app.include_router(db_structure.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(auth=SimpleNamespace(enabled=False))
    app.dependency_overrides[db_structure.get_structure_service] = lambda: _SlowService()
    app.dependency_overrides[db_structure.get_admin_job_runner] = lambda: runner

    with TestClient(app) as client:
        client.portal.call(_make_event, gate)  # type: ignore[union-attr]
        first = client.post(f"{BASE}/src_a/check")
        second = client.post(f"{BASE}/src_a/check")
        assert first.status_code == 202
        assert second.status_code == 409
        job_id = first.json()["job_id"]
        client.portal.call(_set_event, gate["release"])  # type: ignore[union-attr]
        record: dict = {}
        for _ in range(100):
            record = client.get(f"{BASE}/jobs/{job_id}").json()
            if record["status"] != "running":
                break
            client.portal.call(asyncio.sleep, 0.01)  # type: ignore[union-attr]
        assert record["status"] == "succeeded"
        assert record["result"] == {"source": "src_a", "by": "anonymous"}
        # 잡이 끝나면 락이 풀려 다음 잡을 시작할 수 있다
        assert client.post(f"{BASE}/src_a/check").status_code == 202
        client.portal.call(_set_event, gate["release"])  # type: ignore[union-attr]


async def _make_event(gate: dict) -> None:
    gate["release"] = asyncio.Event()


async def _set_event(event: asyncio.Event) -> None:
    event.set()
