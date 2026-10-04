"""plans/134 W0-B — 작업 API(`/api/v1/apm/jobs`) 인가 · 취소 · 전체 결과 받기 (SPEC §3.8 · D-262).

고정하는 계약:
  1. 장부 소유자 200 · 남 403 · 없음 404 — 장부를 먼저 보고(거부면 게이트웨이 호출 0) 게이트웨이에도
     장부의 `owner`를 싣는다 · 관리자 허용(게이트웨이 owner는 원 소유자) · 인증 꺼짐은 통과(D-262).
  2. 상태 미리보기 행은 `DataMasker`로 가린다 · 게이트웨이가 모르는 작업(보관 경과) = 404.
  3. 취소 → `apm_job_cancel`(owner) · 장부 상태 갱신.
  4. 다운로드: 청크를 차례로 받아 흘려보낸다(한 번에 한 청크) · 전량 · CSV 마스킹 · BOM · 감사 1회 ·
     끝나지 않은 작업 409 · 부분 결과 = 파일 이름 `_partial` + 헤더 `X-Apm-Job-State: partial` ·
     JSONL · TXT.
게이트웨이는 모의 MCP 세션이다(실 게이트웨이·LLM 0).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import ANONYMOUS_USER, require_user
from src.api.routes import apm_jobs as routes
from src.config import DBHubConfig, SecurityConfig
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_jobs as jobs
from src.orchestration import apm_query as aq

JOB = "0123456789abcdef0123456789abcdef"
OTHER = "fedcba9876543210fedcba9876543210"
ALICE = {"sub": "alice", "role": "user"}
BOB = {"sub": "bob", "role": "user"}
ADMIN = {"sub": "root", "role": "admin"}
CHUNKS = [
    [{"instance_id": 1, "password": "s3cret-1", "profile_ref": {"txid": "a"}},
     {"instance_id": 2, "password": "s3cret-2", "profile_ref": {"txid": "b"}}],
    [{"instance_id": 3, "password": "s3cret-3"}, {"instance_id": 4, "password": "s3cret-4"}],
    [{"instance_id": 5, "password": "s3cret-5"}],
]


def _handle(state: str, job_id: str = JOB) -> dict:
    return {"job_id": job_id, "state": state,
            "progress": {"done": 3, "total": 10, "unit": "api_calls", "label": "API 호출"},
            "estimate": {"api_calls": 10, "seconds": 2.0},
            "created_at": "2026-10-02T10:00:00+09:00", "updated_at": "2026-10-02T10:00:03+09:00",
            "expires_at": "2026-10-03T10:00:05+09:00" if state in ("completed", "partial")
            else None}


class _JobGateway:
    """게이트웨이 작업 도구 3종 — 같은 owner일 때만 응답한다(아니면 job_not_found)."""

    def __init__(self, state: str = "completed", *, owner: str = "user:alice",
                 text_parts: dict[str, str] | None = None, fail_chunk: int | None = None) -> None:
        self.state = state
        self.owner = owner
        self.fail_chunk = fail_chunk
        self.text_parts = text_parts or {}
        self.calls: list[tuple[str, dict]] = []
        self.opened = 0

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    def _env(self, tool: str, rows: list[dict], **extra: Any) -> dict:
        return {"rows": rows, "row_count": len(rows), "source_kind": "apm_api",
                "source": "jennifer", "tool": tool, "limits": [], **extra}

    def _artifact(self) -> dict:
        return {"job_id": JOB, "total_rows": 5, "chunk_rows": 2,
                "chunks": [{"index": i, "rows": len(c), "bytes": 1, "sha256": "x"}
                           for i, c in enumerate(CHUNKS)],
                "columns": ["instance_id", "password", "profile_ref"],
                "text_parts": [{"name": n, "bytes": len(t)} for n, t in self.text_parts.items()]}

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        if arguments.get("job_id") != JOB or arguments.get("owner") != self.owner:
            reply = {"error": "job_not_found", "reason": "작업이 없다", "tool": name}
        elif name == "apm_job_status":
            extra: dict[str, Any] = {"job": _handle(self.state)}
            rows: list[dict] = []
            if self.state in ("completed", "partial"):
                rows = CHUNKS[0]
                extra.update(artifact=self._artifact(), total_row_count=5,
                             result_meta={"tool": "apm_slow_transactions"},
                             partial=self.state == "partial")
            reply = self._env(name, rows, **extra)
        elif name == "apm_job_cancel":
            self.state = "cancelled"
            reply = self._env(name, [], job=_handle("cancelled"))
        else:  # apm_job_read
            if "part" in arguments:
                reply = self._env(name, [], part=arguments["part"],
                                  text=self.text_parts[arguments["part"]])
            elif arguments["chunk"] == self.fail_chunk:
                reply = {"error": "apm_api_error", "reason": "결과 조각 1 내용이 기록과 다르다",
                         "tool": name}
            else:
                reply = self._env(name, CHUNKS[arguments["chunk"]], chunk=arguments["chunk"])
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


class _Audit:
    def __init__(self) -> None:
        self.downloads: list[dict] = []

    async def log_file_download(self, **kwargs: Any) -> None:
        self.downloads.append(kwargs)


@pytest.fixture
def store(monkeypatch) -> ApmJobStore:
    ledger = ApmJobStore(None)
    monkeypatch.setattr(store_mod, "_STORE", ledger)
    return ledger


async def _register(store: ApmJobStore, owner: str = "alice", job_id: str = JOB,
                    state: str = "running") -> None:
    await store.register({"job_id": job_id, "owner_sub": owner, "thread_id": "th-1",
                          "tool": "apm_slow_transactions", "view": "apm.slow_tx",
                          "view_label": "느린 트랜잭션", "hostname": "web01",
                          "scope": "느린 트랜잭션 · web01", "state": state,
                          "registered_at": "2026-10-02T10:00:00+09:00"})


def _client(monkeypatch, gw: _JobGateway | None, user: dict, *, auth: bool = True,
            audit: _Audit | None = None) -> TestClient:
    if gw is not None:
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    app = FastAPI()
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=auth),
        security=SecurityConfig(_env_file=None, mask_ip=False, mask_email=False),
        dbhub=DBHubConfig(source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
                          source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0),
    )
    app.state.audit_service = audit
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


# ── 1. 인가 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_gets_status_with_gateway_owner(monkeypatch, store) -> None:
    await _register(store)
    gw = _JobGateway("running")
    r = _client(monkeypatch, gw, ALICE).get(f"/api/v1/apm/jobs/{JOB}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "running" and body["progress"]["done"] == 3
    assert body["downloadable"] is False and body["view_label"] == "느린 트랜잭션"
    assert gw.named("apm_job_status") == [{"job_id": JOB, "owner": "user:alice"}]


@pytest.mark.asyncio
async def test_other_user_is_forbidden_before_gateway(monkeypatch, store) -> None:
    await _register(store)
    gw = _JobGateway("running")
    client = _client(monkeypatch, gw, BOB)
    for method, path in (("get", f"/api/v1/apm/jobs/{JOB}"),
                         ("post", f"/api/v1/apm/jobs/{JOB}/cancel"),
                         ("get", f"/api/v1/apm/jobs/{JOB}/download?format=csv")):
        r = getattr(client, method)(path)
        assert r.status_code == 403, (path, r.text)
    assert gw.calls == [] and gw.opened == 0, "장부 확인 → 게이트웨이 호출 순서"


@pytest.mark.asyncio
async def test_unknown_job_is_404_before_gateway(monkeypatch, store) -> None:
    gw = _JobGateway("running")
    client = _client(monkeypatch, gw, ALICE)
    assert client.get(f"/api/v1/apm/jobs/{OTHER}").status_code == 404
    assert client.get("/api/v1/apm/jobs/../../etc").status_code == 404
    assert client.get("/api/v1/apm/jobs/not-a-job-id").status_code == 404
    assert gw.calls == []


@pytest.mark.asyncio
async def test_admin_reads_others_job_with_original_owner(monkeypatch, store) -> None:
    await _register(store)
    gw = _JobGateway("running")
    r = _client(monkeypatch, gw, ADMIN).get(f"/api/v1/apm/jobs/{JOB}")
    assert r.status_code == 200
    assert gw.named("apm_job_status")[0]["owner"] == "user:alice", "게이트웨이에는 원 소유자"


@pytest.mark.asyncio
async def test_auth_disabled_passes_like_d262(monkeypatch, store) -> None:
    await _register(store)
    gw = _JobGateway("running")
    r = _client(monkeypatch, gw, ANONYMOUS_USER, auth=False).get(f"/api/v1/apm/jobs/{JOB}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_expired_job_on_gateway_is_404(monkeypatch, store) -> None:
    await _register(store)
    gw = _JobGateway("running", owner="user:someone-else")
    r = _client(monkeypatch, gw, ALICE).get(f"/api/v1/apm/jobs/{JOB}")
    assert r.status_code == 404 and "보관 기간" in r.json()["detail"]


@pytest.mark.asyncio
async def test_list_shows_only_my_jobs(monkeypatch, store) -> None:
    await _register(store)
    await _register(store, owner="bob", job_id=OTHER)
    r = _client(monkeypatch, None, ALICE).get("/api/v1/apm/jobs")
    assert r.status_code == 200
    assert [j["job_id"] for j in r.json()["jobs"]] == [JOB]


# ── 2·3. 상태 미리보기 마스킹 · 취소 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_finished_status_preview_is_masked(monkeypatch, store) -> None:
    await _register(store)
    r = _client(monkeypatch, _JobGateway("completed"), ALICE).get(f"/api/v1/apm/jobs/{JOB}")
    body = r.json()
    assert body["downloadable"] is True and body["total_row_count"] == 5
    assert body["preview_row_count"] == 2
    assert all(row["password"] == "***MASKED***" for row in body["preview_rows"])
    assert "s3cret" not in r.text
    assert (await store.get(JOB))["state"] == "completed", "장부 상태 캐시 갱신"


@pytest.mark.asyncio
async def test_cancel_calls_gateway_with_owner_and_updates_ledger(monkeypatch, store) -> None:
    await _register(store)
    gw = _JobGateway("running")
    r = _client(monkeypatch, gw, ALICE).post(f"/api/v1/apm/jobs/{JOB}/cancel")
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert gw.named("apm_job_cancel") == [{"job_id": JOB, "owner": "user:alice"}]
    assert (await store.get(JOB))["state"] == "cancelled"


# ── 4. 다운로드 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_csv_download_streams_every_chunk_masked_and_audited(monkeypatch, store) -> None:
    await _register(store)
    gw, audit = _JobGateway("completed"), _Audit()
    r = _client(monkeypatch, gw, ALICE, audit=audit).get(
        f"/api/v1/apm/jobs/{JOB}/download?format=csv")
    assert r.status_code == 200, r.text
    raw = r.content
    assert raw.startswith("﻿".encode()), "Excel 한글용 BOM"
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0] == "instance_id,password,profile_ref"
    assert [line.split(",")[0] for line in lines[1:]] == ["1", "2", "3", "4", "5"], "전량"
    assert "s3cret" not in raw.decode("utf-8") and "***MASKED***" in lines[1]
    assert '"{""txid"": ""a""}"' in lines[1], "중첩 값은 JSON 문자열"
    assert [a["chunk"] for a in gw.named("apm_job_read")] == [0, 1, 2], "청크를 차례로 읽었다"
    assert all(a["owner"] == "user:alice" for a in gw.named("apm_job_read"))
    assert r.headers["content-disposition"] == 'attachment; filename="apm_slow_tx_01234567.csv"'
    assert "x-apm-job-state" not in r.headers
    assert audit.downloads == [{"user_id": "alice", "file_name": "apm_slow_tx_01234567.csv",
                                "file_type": "csv", "file_size": len(raw),
                                "client_ip": "testclient", "request_id": None}]


@pytest.mark.asyncio
async def test_interrupted_download_is_still_audited(monkeypatch, store, caplog) -> None:
    """중단(청크 읽기 실패)이어도 실제로 보낸 바이트로 감사한다 — 일부 나간 데이터가 감사 밖에
    남지 않게(팀 리드 지시 2026-10-02)."""
    await _register(store)
    gw, audit = _JobGateway("completed", fail_chunk=1), _Audit()
    client = _client(monkeypatch, gw, ALICE, audit=audit)
    client = TestClient(client.app, raise_server_exceptions=False)
    with caplog.at_level("WARNING", logger="src.api.routes.apm_jobs"):
        try:
            client.get(f"/api/v1/apm/jobs/{JOB}/download?format=csv")
        except Exception:  # noqa: BLE001 — 끊긴 응답의 클라이언트 쪽 예외 모양은 관심 밖
            pass
    assert [a["chunk"] for a in gw.named("apm_job_read")] == [0, 1]
    (entry,) = audit.downloads
    assert 0 < entry["file_size"] < 200, "BOM + 머리글 + 첫 청크까지만 나갔다"
    assert any("다운로드 중단" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_download_body_reads_one_chunk_at_a_time(monkeypatch, store) -> None:
    """본문 반복자는 다음 조각을 요구받을 때 다음 청크를 읽는다(전량을 미리 모으지 않는다)."""
    await _register(store)
    gw = _JobGateway("completed")
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    config = SimpleNamespace(
        auth=SimpleNamespace(enabled=True), security=SecurityConfig(_env_file=None),
        dbhub=DBHubConfig(source_endpoints={"apm": "http://127.0.0.1:9096/sse"}))
    download = await jobs.ApmJobService(config).open_download(JOB, ALICE, "jsonl")
    assert gw.named("apm_job_read") == []
    first = await download.body.__anext__()
    assert len(gw.named("apm_job_read")) == 1 and first.count(b"\n") == 2
    rest = [part async for part in download.body]
    assert len(gw.named("apm_job_read")) == 3 and sum(p.count(b"\n") for p in rest) == 3


@pytest.mark.asyncio
async def test_unfinished_job_download_is_409(monkeypatch, store) -> None:
    await _register(store)
    gw = _JobGateway("running")
    r = _client(monkeypatch, gw, ALICE).get(f"/api/v1/apm/jobs/{JOB}/download")
    assert r.status_code == 409 and "running" in r.json()["detail"]
    assert gw.named("apm_job_read") == []


@pytest.mark.asyncio
async def test_partial_download_is_marked(monkeypatch, store) -> None:
    await _register(store)
    r = _client(monkeypatch, _JobGateway("partial"), ALICE).get(
        f"/api/v1/apm/jobs/{JOB}/download?format=csv")
    assert r.status_code == 200
    assert r.headers["x-apm-job-state"] == "partial"
    assert "apm_slow_tx_01234567_partial.csv" in r.headers["content-disposition"]


@pytest.mark.asyncio
async def test_jsonl_download_is_masked(monkeypatch, store) -> None:
    await _register(store)
    r = _client(monkeypatch, _JobGateway("completed"), ALICE).get(
        f"/api/v1/apm/jobs/{JOB}/download?format=jsonl")
    rows = [json.loads(line) for line in r.text.splitlines()]
    assert [row["instance_id"] for row in rows] == [1, 2, 3, 4, 5]
    assert {row["password"] for row in rows} == {"***MASKED***"}
    assert r.headers["content-type"].startswith("application/x-ndjson")


@pytest.mark.asyncio
async def test_txt_download_reads_text_parts(monkeypatch, store) -> None:
    await _register(store)
    gw = _JobGateway("completed", text_parts={"profile": "line1\nline2"})
    r = _client(monkeypatch, gw, ALICE).get(f"/api/v1/apm/jobs/{JOB}/download?format=txt")
    assert r.status_code == 200 and r.text == "line1\nline2\n"
    assert gw.named("apm_job_read") == [{"job_id": JOB, "owner": "user:alice", "part": "profile"}]
    no_text = _client(monkeypatch, _JobGateway("completed"), ALICE).get(
        f"/api/v1/apm/jobs/{JOB}/download?format=txt")
    assert no_text.status_code == 404


@pytest.mark.asyncio
async def test_unknown_format_is_rejected(monkeypatch, store) -> None:
    await _register(store)
    gw = _JobGateway("completed")
    r = _client(monkeypatch, gw, ALICE).get(f"/api/v1/apm/jobs/{JOB}/download?format=xlsx")
    assert r.status_code == 422 and gw.calls == []


def test_router_is_registered_on_the_app() -> None:
    from src.api.server import create_app

    paths = set(create_app().openapi()["paths"])
    for path in ("/api/v1/apm/jobs", "/api/v1/apm/jobs/{job_id}",
                 "/api/v1/apm/jobs/{job_id}/cancel", "/api/v1/apm/jobs/{job_id}/download"):
        assert path in paths


def test_unauthenticated_request_is_401() -> None:
    """로그인 안 한 첫 방문자 — 인증이 켜진 설치에서 토큰 없이 부르면 401(화면은 로그인으로)."""
    app = FastAPI()
    app.state.config = SimpleNamespace(auth=SimpleNamespace(enabled=True, jwt_secret="x"))
    app.include_router(routes.router, prefix="/api/v1")
    client = TestClient(app)
    assert client.get("/api/v1/apm/jobs").status_code == 401
    assert client.get(f"/api/v1/apm/jobs/{JOB}").status_code == 401
    assert client.get(f"/api/v1/apm/jobs/{JOB}/download").status_code == 401


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.ttl: dict[str, int | None] = {}

    async def ensure_connected(self) -> bool:
        return True

    async def get_json(self, key: str) -> Any:
        return self.data.get(key)

    async def set_json(self, key: str, value: Any, *, ex: int | None = None,
                       nx: bool = False) -> bool:
        self.data[key] = json.loads(json.dumps(value))
        self.ttl[key] = ex
        return True


@pytest.mark.asyncio
async def test_ledger_uses_redis_with_retention_and_owner_index() -> None:
    redis = _FakeRedis()
    ledger = ApmJobStore(redis, clock=lambda: 1_000_000.0)
    record = {"job_id": JOB, "owner_sub": "alice", "registered_at": "2026-10-02T10:00:00+09:00"}
    assert await ledger.register(record) == "redis"
    assert redis.ttl[f"apm:job:{JOB}"] == store_mod.RETENTION_SECONDS, "진행 중 = 기본 보관 기간"
    assert redis.data["apm:jobs:owner:alice"] == [JOB]
    await ledger.update(JOB, {"state": "completed",
                              "expires_at": "1970-01-12T14:20:00+00:00"})  # 시계 + 2000초
    assert redis.ttl[f"apm:job:{JOB}"] == 2000 + 60, "게이트웨이 expires_at까지(+60초 여유)"
    assert [r["job_id"] for r in await ledger.list_for("alice")] == [JOB]
    assert await ledger.list_for("bob") == []
    assert (await ledger.get(JOB))["ledger"] == "redis"
