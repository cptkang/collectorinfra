"""plans/104 S3·S4·S5 — 스키마 캐시 관리자 API 라우트 순서 · 감사 · 사람 자산 보존.

- 실 Redis·MCP·LLM 호출 0: 라우트가 호출 시점에 import하는 `load_config`·`get_cache_manager`·
  `create_llm`·`DescriptionGenerator`를 가짜로 치환한다(D-127).
- 인증은 개발 모드(auth 비활성)로 우회한다 —
  RBAC 자체는 `test_plan104_db_structure_api.py`·`test_admin_rbac.py` 소관.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import admin_audit
from src.api.routes import admin as admin_routes
from src.api.routes import schema_cache as schema_cache_routes


class _CapturingAuditService:
    """감사 기록을 가로챈다."""

    def __init__(self, fail: bool = False) -> None:
        self.entries: list[Any] = []
        self.fail = fail

    async def log(self, entry: Any) -> None:
        if self.fail:
            raise RuntimeError("audit down")
        self.entries.append(entry)


class _FakeCacheManager:
    """라우트가 부르는 캐시 매니저 메서드만 흉내 낸다(호출 기록)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.db_descriptions: dict[str, str] = {"db_a": "설명 A"}
        self.origins: dict[str, str] = {}
        self.schemas: dict[str, dict] = {
            "db_a": {"tables": {"t1": {}}},
            "db_b": {"tables": {"t2": {}}},
        }
        self.synonyms: dict[str, dict[str, list[str]]] = {"db_a": {"t1.c1": ["별칭"]}}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def called(self, name: str) -> list[tuple[tuple, dict]]:
        return [(a, k) for n, a, k in self.calls if n == name]

    async def get_db_descriptions(self) -> dict[str, str]:
        self._record("get_db_descriptions")
        return dict(self.db_descriptions)

    async def get_db_description(self, db_id: str) -> str | None:
        return self.db_descriptions.get(db_id)

    async def get_db_description_origin(self, db_id: str) -> str | None:
        self._record("get_db_description_origin", db_id)
        return self.origins.get(db_id)

    async def save_db_description(
        self, db_id: str, description: str, origin: str = "manual"
    ) -> bool:
        self._record("save_db_description", db_id, description, origin=origin)
        self.db_descriptions[db_id] = description
        self.origins[db_id] = origin
        return True

    async def delete_db_description(self, db_id: str) -> bool:
        self._record("delete_db_description", db_id)
        return self.db_descriptions.pop(db_id, None) is not None

    async def get_schema(self, db_id: str) -> dict | None:
        self._record("get_schema", db_id)
        return self.schemas.get(db_id)

    async def invalidate(self, db_id: str) -> bool:
        self._record("invalidate", db_id)
        return True

    async def invalidate_all(self) -> int:
        self._record("invalidate_all")
        return 3

    async def get_synonyms(self, db_id: str) -> dict[str, list[str]]:
        return dict(self.synonyms.get(db_id, {}))

    async def remove_synonyms(self, db_id: str, column: str, words: list[str]) -> bool:
        self._record("remove_synonyms", db_id, column, words)
        return True

    async def save_descriptions(self, db_id: str, descriptions: dict) -> bool:
        self._record("save_descriptions", db_id, descriptions)
        return True

    async def save_synonyms(self, db_id: str, synonyms: dict) -> bool:
        self._record("save_synonyms", db_id, synonyms)
        return True

    async def get_all_status(self) -> list:
        return []


class _FakeGenerator:
    """DescriptionGenerator 대역 — LLM 호출 수를 센다."""

    instances: list[_FakeGenerator] = []

    def __init__(self, llm: Any) -> None:
        self.db_calls: list[str] = []
        _FakeGenerator.instances.append(self)

    async def generate_db_description(self, db_id: str, schema_dict: dict) -> str:
        self.db_calls.append(db_id)
        return f"LLM 설명 {db_id}"

    async def generate_for_db(self, schema_dict: dict) -> tuple[dict, dict]:
        return {"t1.c1": "설명"}, {"t1.c1": ["단어"]}


@pytest.fixture
def cache_mgr(monkeypatch) -> _FakeCacheManager:
    """라우트의 지연 import 대상을 가짜로 바꾼다."""
    manager = _FakeCacheManager()
    config = SimpleNamespace(multi_db=SimpleNamespace(get_active_db_ids=lambda: []))
    llm_created: list[Any] = []
    monkeypatch.setattr("src.config.load_config", lambda: config)
    monkeypatch.setattr(
        "src.schema_cache.cache_manager.get_cache_manager", lambda cfg=None: manager
    )
    monkeypatch.setattr("src.llm.create_llm", lambda cfg: llm_created.append(cfg) or object())
    _FakeGenerator.instances = []
    monkeypatch.setattr(
        "src.schema_cache.description_generator.DescriptionGenerator", _FakeGenerator
    )
    manager.llm_created = llm_created  # type: ignore[attr-defined]
    return manager


def _client(audit_service: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(schema_cache_routes.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(auth=SimpleNamespace(enabled=False))
    if audit_service is not None:
        app.state.audit_service = audit_service
    return TestClient(app)


# ─── S3: 라우트 순서 ─────────────────────────────────────────────


def test_s3_db_descriptions_list_is_not_shadowed_by_db_id_route(cache_mgr):
    """`GET …/db-descriptions`가 `{db_id}` 상세가 아니라 DB 설명 목록을 돌려준다."""
    response = _client().get("/api/v1/admin/schema-cache/db-descriptions")

    assert response.status_code == 200
    assert response.json() == {"descriptions": {"db_a": "설명 A"}}
    assert cache_mgr.called("get_schema") == []  # 캐시 상세 핸들러로 가지 않았다


def test_s3_fixed_paths_declared_before_db_id_paths():
    """선언 순서 자체를 고정한다 — 고정 경로 `db-descriptions*`가 `/{db_id}`보다 앞선다."""
    paths = [route.path for route in schema_cache_routes.router.routes]
    first_db_id = min(
        i for i, path in enumerate(paths) if path.startswith("/admin/schema-cache/{db_id}")
    )
    fixed = [i for i, path in enumerate(paths) if "/db-descriptions" in path]
    assert fixed and max(fixed) < first_db_id


def test_s3_db_description_detail_route(cache_mgr):
    """`GET …/db-descriptions/{db_id}`도 `{db_id}/synonyms`에 가리지 않는다."""
    response = _client().get("/api/v1/admin/schema-cache/db-descriptions/db_a")
    assert response.status_code == 200
    assert response.json() == {"db_id": "db_a", "description": "설명 A"}


# ─── S4: 감사 ───────────────────────────────────────────────────


def test_s4_settings_audit_helper_is_shared_not_copied():
    """admin.py의 감사 헬퍼는 공용 함수의 별칭이다(사본 금지 — D-053)."""
    assert admin_routes._log_settings_event is admin_audit.log_admin_event


def test_s4_invalidate_writes_cache_operation_audit_row(cache_mgr):
    audit = _CapturingAuditService()
    response = _client(audit).delete("/api/v1/admin/schema-cache/db_a")

    assert response.status_code == 200
    assert response.json()["audit_logged"] is True
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.event == "cache_operation"
    assert entry.user_id == "anonymous"
    assert entry.extra["action"] == "invalidate"
    assert entry.extra["db_id"] == "db_a"


def test_s4_audit_failure_is_reported_in_response(cache_mgr):
    """감사 저장소가 실패·미구성이면 작업은 끝나되 응답에 audit_logged=false로 드러난다."""
    failing = _CapturingAuditService(fail=True)
    response = _client(failing).delete("/api/v1/admin/schema-cache")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted_count"] == 3
    assert body["audit_logged"] is False

    response_none = _client().put(
        "/api/v1/admin/schema-cache/db-descriptions/db_a", json={"description": "새 설명"}
    )
    assert response_none.status_code == 200
    assert response_none.json()["audit_logged"] is False


@pytest.mark.parametrize(
    ("method", "path", "body", "action"),
    [
        ("delete", "/api/v1/admin/schema-cache/db_a", None, "invalidate"),
        ("delete", "/api/v1/admin/schema-cache", None, "invalidate_all"),
        (
            "put",
            "/api/v1/admin/schema-cache/db-descriptions/db_a",
            {"description": "x"},
            "set_db_description",
        ),
        (
            "delete",
            "/api/v1/admin/schema-cache/db-descriptions/db_a",
            None,
            "delete_db_description",
        ),
        (
            "post",
            "/api/v1/admin/schema-cache/db-descriptions/generate",
            {"db_ids": ["db_b"]},
            "generate_db_descriptions",
        ),
        ("post", "/api/v1/admin/schema-cache/db_a/synonyms/generate", None, "generate_synonyms"),
        (
            "delete",
            "/api/v1/admin/schema-cache/db_a/synonyms/t1.c1",
            None,
            "delete_column_synonyms",
        ),
        (
            "post",
            "/api/v1/admin/schema-cache/generate-descriptions",
            {"db_ids": ["db_a"]},
            "generate_descriptions",
        ),
    ],
)
def test_s4_every_mutation_is_audited(cache_mgr, method, path, body, action):
    """변경 작업 전부가 CACHE_OPERATION 행을 남기고 audit_logged=true를 싣는다."""
    audit = _CapturingAuditService()
    client = _client(audit)
    kwargs = {"json": body} if body is not None else {}
    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 200, response.text
    assert response.json()["audit_logged"] is True
    assert [e.extra["action"] for e in audit.entries] == [action]
    assert all(e.event == "cache_operation" for e in audit.entries)


def test_s4_generate_cache_is_audited(cache_mgr, monkeypatch):
    """캐시 생성은 DB 클라이언트를 열므로 클라이언트·갱신을 가짜로 두고 감사만 확인한다."""

    class _Ctx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    async def fake_refresh(db_id, client, force=False):
        return SimpleNamespace(
            db_id=db_id,
            status="unchanged",
            table_count=1,
            fingerprint="f",
            description_status="complete",
            message="",
        )

    monkeypatch.setattr("src.db.get_db_client", lambda cfg, db_id=None: _Ctx())
    cache_mgr.refresh_cache = fake_refresh  # type: ignore[attr-defined]
    audit = _CapturingAuditService()

    response = _client(audit).post(
        "/api/v1/admin/schema-cache/generate",
        json={"db_ids": ["db_a"], "include_descriptions": False},
    )

    assert response.status_code == 200
    assert response.json()["audit_logged"] is True
    entry = audit.entries[0]
    assert entry.extra["action"] == "generate"
    assert entry.extra["results"] == [{"db_id": "db_a", "status": "unchanged"}]


# ─── S5: 사람 자산 보존 ──────────────────────────────────────────


def test_s5_manual_db_description_skips_llm_before_call(cache_mgr):
    """출처 manual이면 LLM을 만들지도 부르지도 않고 결과에 보존 표기를 남긴다."""
    cache_mgr.origins["db_a"] = "manual"

    response = _client().post(
        "/api/v1/admin/schema-cache/db-descriptions/generate", json={"db_ids": ["db_a"]}
    )

    assert response.status_code == 200
    assert response.json()["results"] == {"db_a": "(수동 설명 보존)"}
    assert cache_mgr.llm_created == []  # type: ignore[attr-defined]
    assert _FakeGenerator.instances == []
    assert cache_mgr.called("save_db_description") == []
    assert cache_mgr.db_descriptions["db_a"] == "설명 A"


def test_s5_llm_generation_saves_with_llm_origin(cache_mgr):
    """출처가 manual이 아니면(llm·레거시 None) 생성값을 origin=llm으로 저장한다."""
    cache_mgr.origins["db_a"] = "llm"

    response = _client().post(
        "/api/v1/admin/schema-cache/db-descriptions/generate", json={"db_ids": ["db_a", "db_b"]}
    )

    assert response.status_code == 200
    assert response.json()["results"] == {"db_a": "LLM 설명 db_a", "db_b": "LLM 설명 db_b"}
    saves = cache_mgr.called("save_db_description")
    assert [(args[0], kwargs["origin"]) for args, kwargs in saves] == [
        ("db_a", "llm"),
        ("db_b", "llm"),
    ]
    assert len(_FakeGenerator.instances) == 1  # 생성기는 필요할 때 1회만 만든다


def test_s5_llm_generation_reports_when_save_refused(cache_mgr):
    """저장이 거부되면(생성 도중 수동 설명 저장 등) 생성값을 성공으로 보이지 않는다."""

    async def refuse(db_id, description, origin="manual"):
        return False

    cache_mgr.save_db_description = refuse  # type: ignore[method-assign]
    response = _client().post(
        "/api/v1/admin/schema-cache/db-descriptions/generate", json={"db_ids": ["db_a"]}
    )
    result = response.json()
    assert result["results"]["db_a"].startswith("(저장 안 됨")
    assert result["message"] == "DB 설명 0개 생성 완료"


def test_s5_manual_put_saves_with_manual_origin(cache_mgr):
    response = _client().put(
        "/api/v1/admin/schema-cache/db-descriptions/db_b", json={"description": "사람이 쓴 설명"}
    )

    assert response.status_code == 200
    saves = cache_mgr.called("save_db_description")
    assert saves == [(("db_b", "사람이 쓴 설명"), {"origin": "manual"})]
