"""plans/134 W0-B 본체 검증(검증자) — 작업 API 적대적 시험
(SPEC-apm-question-coverage §3.8 · D-262).

구현자 테스트(`test_plan134_apm_jobs_api.py`)가 고정한 대표 경로 밖을 친다.

  1. 인가 행렬: 세 엔드포인트(상태 · 취소 · 다운로드) × (소유자 · 남 · 관리자 · 인증 꺼짐 ·
     장부에 없음 · 형식 밖 ID · 장부엔 있으나 게이트웨이 `job_not_found` · 주체 없는 토큰).
     거부는 **게이트웨이 세션
     0회**(장부 확인 → 게이트웨이 순서).
  2. 다운로드: 청크 다수(전량 · 순서 · 머리글/BOM 1회 · 한 번에 한 청크 — 읽기와 내보내기가
     맞물림) · `DataMasker` 대상 값(컬럼명 · 값 패턴 · IP/이메일 설정) · 미완료 상태 5종 409
     (읽기 0 · 감사 0) · `partial` 파일명·헤더(세 형식) · 감사(완료 1회 · 실제 바이트 · 요청자) ·
     0행 · 게이트웨이 미연결 502.
  3. 장부: Redis 없음 → 메모리(+ 카드가 읽는 `ledger` 칸) · Redis 기록 실패 → 메모리.
  4. 결함 재현(종전 `xfail(strict=True)` — 2026-10-02 구현자 수정으로 표지를 걷었다):
     - 중첩 값 안의 민감 키는 CSV에서 가려지지 않는다(`DataMasker`가 1단만 본다).
     - 메모리 장부에 남은 기록은 Redis 복구 뒤 갱신해도 옛 상태를 계속 돌려준다.
게이트웨이는 모의 MCP 세션이다(실 게이트웨이·LLM 0). 소스 코드는 바꾸지 않는다.
"""

from __future__ import annotations

import csv
import io
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
NO_SUB = {"sub": None, "role": "user"}
FINISHED = ("completed", "partial")


def _handle(state: str, job_id: str = JOB, error: dict | None = None) -> dict:
    handle: dict[str, Any] = {
        "job_id": job_id, "state": state,
        "progress": {"done": 3, "total": 10, "unit": "api_calls", "label": "API 호출"},
        "estimate": {"api_calls": 10, "seconds": 2.0},
        "created_at": "2026-10-02T10:00:00+09:00", "updated_at": "2026-10-02T10:00:03+09:00",
        "expires_at": "2026-10-03T10:00:05+09:00" if state in FINISHED else None,
    }
    if error:
        handle["error"] = error
    return handle


class _Gateway:
    """작업 도구 3종을 게이트웨이 계약대로 흉내 낸다(SPEC §3.7 · 게이트웨이 `jobs.py` 실코드 모양).

    - 같은 `owner`일 때만 응답(아니면 `job_not_found`) · 끝나지 않은 작업의 읽기 = `job_not_ready`.
    - 세션을 열 때마다 `opened`를 올린다(거부 경로의 게이트웨이 호출 0 단언용).
    - `trace`: 읽기·내보내기 순서(다운로드가 청크를 미리 모으지 않는지).
    """

    def __init__(self, state: str = "completed", *, chunks: list[list[dict]] | None = None,
                 columns: list[str] | None = None, owner: str = "user:alice",
                 text_parts: dict[str, str] | None = None, error: dict | None = None,
                 known: bool = True) -> None:
        self.state = state
        self.chunks = chunks if chunks is not None else [[{"instance_id": 1}]]
        self.columns = columns if columns is not None else list(dict.fromkeys(
            k for c in self.chunks for r in c for k in r))
        self.owner = owner
        self.text_parts = text_parts or {}
        self.error = error
        self.known = known
        self.calls: list[tuple[str, dict]] = []
        self.opened = 0
        self.trace: list[str] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    def _env(self, tool: str, rows: list[dict], **extra: Any) -> dict:
        return {"rows": rows, "row_count": len(rows), "source_kind": "apm_api",
                "source": "jennifer", "tool": tool, "limits": ["[한계] 예시"], **extra}

    def _artifact(self) -> dict:
        return {"job_id": JOB, "total_rows": sum(len(c) for c in self.chunks), "chunk_rows": 25,
                "chunks": [{"index": i, "rows": len(c), "bytes": 1, "sha256": "x"}
                           for i, c in enumerate(self.chunks)],
                "columns": self.columns,
                "text_parts": [{"name": n, "bytes": len(t)} for n, t in self.text_parts.items()]}

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        if not self.known or arguments.get("job_id") != JOB or arguments.get("owner") != self.owner:
            reply: dict[str, Any] = {"error": "job_not_found", "reason": "작업이 없다",
                                     "tool": name}
        elif name == "apm_job_status":
            extra: dict[str, Any] = {"job": _handle(self.state, error=self.error),
                                     "partial": self.state == "partial"}
            rows: list[dict] = []
            if self.state in FINISHED:
                rows = self.chunks[0] if self.chunks else []
                extra.update(artifact=self._artifact(),
                             total_row_count=sum(len(c) for c in self.chunks),
                             result_meta={"tool": "apm_events"})
            reply = self._env(name, rows, **extra)
        elif name == "apm_job_cancel":
            if self.state in ("queued", "running"):
                self.state = "cancelled"
                self.error = {"code": "cancelled", "reason": "취소 요청으로 끝냈다"}
            reply = self._env(name, [], job=_handle(self.state, error=self.error))
        elif self.state in ("queued", "running"):
            reply = {"error": "job_not_ready", "reason": "아직", "tool": name}
        elif "part" in arguments:
            reply = self._env(name, [], part=arguments["part"],
                              text=self.text_parts[arguments["part"]])
        else:
            index = arguments["chunk"]
            self.trace.append(f"read:{index}")
            reply = self._env(name, self.chunks[index], chunk=index)
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


async def _register(store: ApmJobStore, owner: str = "alice", job_id: str = JOB) -> None:
    await store.register({"job_id": job_id, "owner_sub": owner, "thread_id": "th-1",
                          "tool": "apm_events", "view": "apm.events",
                          "view_label": "WAS 이벤트", "hostname": "web01",
                          "scope": "WAS 이벤트 · web01", "state": "running",
                          "registered_at": "2026-10-02T10:00:00+09:00"})


def _config(*, auth: bool = True, endpoint: bool = True, mask_ip: bool = False,
            mask_email: bool = False) -> SimpleNamespace:
    endpoints = {"apm": "http://127.0.0.1:9096/sse"} if endpoint else {}
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=auth),
        security=SecurityConfig(_env_file=None, mask_ip=mask_ip, mask_email=mask_email),
        dbhub=DBHubConfig(_env_file=None, source_endpoints=endpoints,
                          source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0),
    )


def _client(monkeypatch, gw: _Gateway | None, user: dict, *, audit: _Audit | None = None,
            **cfg: Any) -> TestClient:
    if gw is not None:
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    app = FastAPI()
    app.state.config = _config(**cfg)
    app.state.audit_service = audit
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


ENDPOINTS = (
    ("get", "/api/v1/apm/jobs/{id}"),
    ("post", "/api/v1/apm/jobs/{id}/cancel"),
    ("get", "/api/v1/apm/jobs/{id}/download?format=csv"),
)


def _call(client: TestClient, method: str, path: str, job_id: str = JOB):
    return getattr(client, method)(path.format(id=job_id))


# ── 1. 인가 행렬 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
@pytest.mark.parametrize(("user", "auth", "expected", "gw_owner"), [
    (ALICE, True, 200, "user:alice"),
    (BOB, True, 403, None),
    (NO_SUB, True, 403, None),
    (ADMIN, True, 200, "user:alice"),
    (ANONYMOUS_USER, False, 200, "user:alice"),
], ids=["owner", "other-user", "token-without-sub", "admin", "auth-off"])
async def test_authorization_matrix(monkeypatch, store, method, path, user, auth, expected,
                                    gw_owner) -> None:
    await _register(store)
    gw = _Gateway("completed")
    r = _call(_client(monkeypatch, gw, user, auth=auth), method, path)
    assert r.status_code == expected, r.text
    if expected == 403:
        assert gw.opened == 0 and gw.calls == [], "거부는 게이트웨이를 부르기 전에 끝난다"
        return
    assert gw.calls, "허용이면 게이트웨이를 부른다"
    assert {a["owner"] for _, a in gw.calls} == {gw_owner}, "게이트웨이에는 장부의 원 소유자"


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
@pytest.mark.parametrize("bad_id", [
    OTHER,                                     # 형식은 맞지만 장부에 없다
    JOB.upper(),                               # 대문자 16진수(게이트웨이는 소문자만 발급)
    JOB[:-1],                                  # 31자
    JOB + "0",                                 # 33자
    JOB[:-1] + "g",                            # 16진수 밖 문자
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",          # 인코딩된 경로 이동
    "..%5c..%5cwin",                           # 역슬래시 이동
    " " + JOB,                                 # 앞 공백
], ids=["unknown", "upper", "short", "long", "non-hex", "dotdot-enc", "backslash", "space"])
async def test_unknown_or_malformed_id_is_404_without_gateway(monkeypatch, store, method, path,
                                                              bad_id) -> None:
    await _register(store)
    gw = _Gateway("completed")
    r = _call(_client(monkeypatch, gw, ALICE), method, path, job_id=bad_id)
    assert r.status_code == 404, (bad_id, r.status_code, r.text)
    assert gw.opened == 0, "장부에 없으면 게이트웨이 세션을 열지 않는다"


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
async def test_ledger_hit_but_gateway_job_not_found_is_404(monkeypatch, store, method,
                                                           path) -> None:
    """장부엔 있으나 게이트웨이가 모른다(보관 만료 · 게이트웨이 재기동) — 세 엔드포인트 모두 404."""
    await _register(store)
    gw = _Gateway("completed", known=False)
    r = _call(_client(monkeypatch, gw, ALICE), method, path)
    assert r.status_code == 404, r.text
    assert "보관 기간" in r.json()["detail"]
    assert gw.named("apm_job_read") == [], "다운로드는 상태 확인 단계에서 끝난다"


@pytest.mark.asyncio
async def test_gateway_owner_mismatch_cannot_be_bypassed_by_admin(monkeypatch, store) -> None:
    """관리자도 게이트웨이에는 원 소유자 owner로만 묻는다(관리자 자신의 owner로 바꾸지 않는다)."""
    await _register(store, owner="carol")
    gw = _Gateway("completed", owner="user:carol")
    r = _client(monkeypatch, gw, ADMIN).get(f"/api/v1/apm/jobs/{JOB}")
    assert r.status_code == 200
    assert gw.named("apm_job_status") == [{"job_id": JOB, "owner": "user:carol"}]


@pytest.mark.asyncio
async def test_list_never_opens_gateway_and_is_owner_scoped(monkeypatch, store) -> None:
    await _register(store)
    await _register(store, owner="bob", job_id=OTHER)
    gw = _Gateway("completed")
    for user, expected in ((ALICE, [JOB]), (BOB, [OTHER]), (ADMIN, [])):
        r = _client(monkeypatch, gw, user).get("/api/v1/apm/jobs")
        assert r.status_code == 200
        assert [j["job_id"] for j in r.json()["jobs"]] == expected, user
    assert gw.opened == 0


# ── 2. 다운로드 ──────────────────────────────────────────────────────────────

def _many_chunks(n_chunks: int = 40, rows_per: int = 25) -> list[list[dict]]:
    seq = iter(range(n_chunks * rows_per))
    return [[{"seq": next(seq), "instance_name": f"was{c:02d}", "note": "가,나\"다"}
             for _ in range(rows_per)] for c in range(n_chunks)]


@pytest.mark.asyncio
async def test_many_chunks_download_is_complete_ordered_with_single_header(monkeypatch,
                                                                         store) -> None:
    await _register(store)
    gw, audit = _Gateway("completed", chunks=_many_chunks()), _Audit()
    r = _client(monkeypatch, gw, ALICE, audit=audit).get(
        f"/api/v1/apm/jobs/{JOB}/download?format=csv")
    assert r.status_code == 200, r.text
    raw = r.content
    assert raw.startswith("﻿".encode()) and raw.count("﻿".encode()) == 1, "BOM은 맨 앞 1회"
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0] == ["seq", "instance_name", "note"]
    assert sum(1 for row in rows if row == rows[0]) == 1, "머리글은 1회"
    assert [int(row[0]) for row in rows[1:]] == list(range(1000)), "전량 · 원래 순서"
    assert {row[2] for row in rows[1:]} == {'가,나"다'}, "쉼표·따옴표가 든 값도 RFC 4180으로 보존"
    assert [a["chunk"] for a in gw.named("apm_job_read")] == list(range(40))
    assert len(audit.downloads) == 1 and audit.downloads[0]["file_size"] == len(raw)


@pytest.mark.asyncio
async def test_download_reads_and_emits_in_lockstep(monkeypatch, store) -> None:
    """한 번에 한 청크 — k번째 조각을 내보낼 때까지 게이트웨이 읽기는 정확히 k회다.

    앞당겨 모으지 않는다."""
    await _register(store)
    gw = _Gateway("completed", chunks=_many_chunks(12, 5))
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    download = await jobs.ApmJobService(_config()).open_download(JOB, ALICE, "csv")
    assert gw.named("apm_job_read") == [], "준비 단계는 상태만 본다"
    bom = await download.body.__anext__()
    assert bom == "﻿".encode() and gw.trace == []
    for k in range(1, 13):
        part = await download.body.__anext__()
        assert part and len(gw.trace) == k, (k, gw.trace)
    with pytest.raises(StopAsyncIteration):
        await download.body.__anext__()
    assert gw.opened == 1 + 12, "상태 1회 + 청크마다 세션 1회(본문 전송 중 세션을 잡지 않음)"


@pytest.mark.asyncio
async def test_csv_masks_data_masker_targets(monkeypatch, store) -> None:
    """D-262 — 컬럼명(부분 일치) · 값 패턴 · IP/이메일(설정 켬)을 미리보기·CSV·JSONL 모두 가린다."""
    secrets = {
        "sk": "sk-" + "A" * 24,
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig",
        "aws": "AKIA" + "B" * 16,
        "ghp": "ghp_" + "c" * 36,
        "rrn": "900101-1234567",
        "visa": "4111111111111111",
        "bcrypt": "$2b$12$" + "d" * 53,
    }
    row = {"instance_id": 7, "db_password": "pw-plain-1", "user_token": "tok-plain-2",
           "api_key_value": "key-plain-3", **{f"v_{k}": v for k, v in secrets.items()},
           "client_ip": "192.168.10.77", "owner_mail": "kim@example.com"}
    await _register(store)
    gw = _Gateway("completed", chunks=[[row], [dict(row, instance_id=8)]])
    client = _client(monkeypatch, gw, ALICE, mask_ip=True, mask_email=True)
    plain = ["pw-plain-1", "tok-plain-2", "key-plain-3", "192.168.10.77", "kim@example.com",
             *secrets.values()]
    for path in (f"/api/v1/apm/jobs/{JOB}", f"/api/v1/apm/jobs/{JOB}/download?format=csv",
                 f"/api/v1/apm/jobs/{JOB}/download?format=jsonl"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
        leaked = [p for p in plain if p in r.text]
        assert leaked == [], (path, leaked)
    text = client.get(f"/api/v1/apm/jobs/{JOB}/download?format=csv").content.decode("utf-8-sig")
    assert "192.168.10.***" in text and "k***m@example.com" in text, "IP·이메일은 부분 가림"


@pytest.mark.asyncio
async def test_nested_sensitive_values_are_masked_in_csv(monkeypatch, store) -> None:
    await _register(store)
    row = {"instance_id": 1, "detail": {"password": "nested-secret-77",
                                        "token": "sk-" + "Z" * 24}}
    gw = _Gateway("completed", chunks=[[row]])
    r = _client(monkeypatch, gw, ALICE).get(f"/api/v1/apm/jobs/{JOB}/download?format=csv")
    assert r.status_code == 200
    assert "nested-secret-77" not in r.text and "Z" * 24 not in r.text


@pytest.mark.asyncio
async def test_text_download_masks_whole_line_patterns(monkeypatch, store) -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig"
    await _register(store)
    gw = _Gateway("completed", text_parts={"profile": f"START\n{jwt}\nEND",
                                           "sql": "select 1"})
    r = _client(monkeypatch, gw, ALICE).get(f"/api/v1/apm/jobs/{JOB}/download?format=txt")
    assert r.status_code == 200
    assert jwt not in r.text and "***MASKED***" in r.text
    assert "===== profile =====" in r.text and "===== sql =====" in r.text, "부분 둘 이상 = 구분선"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["queued", "running", "failed", "cancelled", "interrupted"])
async def test_unfinished_or_failed_state_is_409_without_read_or_audit(monkeypatch, store,
                                                                     state) -> None:
    await _register(store)
    error = None if state in ("queued", "running") else {"code": state, "reason": f"사유-{state}"}
    gw, audit = _Gateway(state, error=error), _Audit()
    client = _client(monkeypatch, gw, ALICE, audit=audit)
    for fmt in ("csv", "jsonl", "txt"):
        r = client.get(f"/api/v1/apm/jobs/{JOB}/download?format={fmt}")
        assert r.status_code == 409, (fmt, r.text)
        assert state in r.json()["detail"]
        if error:
            assert f"사유-{state}" in r.json()["detail"], "상태 사유를 함께 알린다"
    assert gw.named("apm_job_read") == [] and audit.downloads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["csv", "jsonl", "txt"])
@pytest.mark.parametrize("state", FINISHED)
async def test_partial_marker_on_every_format(monkeypatch, store, fmt, state) -> None:
    await _register(store)
    gw = _Gateway(state, text_parts={"profile": "x"})
    r = _client(monkeypatch, gw, ALICE).get(f"/api/v1/apm/jobs/{JOB}/download?format={fmt}")
    assert r.status_code == 200, r.text
    disposition = r.headers["content-disposition"]
    if state == "partial":
        assert r.headers["x-apm-job-state"] == "partial"
        assert f"apm_events_{JOB[:8]}_partial.{fmt}" in disposition
    else:
        assert "x-apm-job-state" not in r.headers
        assert f"apm_events_{JOB[:8]}.{fmt}" in disposition and "_partial" not in disposition


@pytest.mark.asyncio
async def test_admin_download_is_audited_as_admin_with_real_size(monkeypatch, store) -> None:
    await _register(store)
    gw, audit = _Gateway("completed", chunks=_many_chunks(3, 4)), _Audit()
    r = _client(monkeypatch, gw, ADMIN, audit=audit).get(
        f"/api/v1/apm/jobs/{JOB}/download?format=jsonl")
    assert r.status_code == 200
    (entry,) = audit.downloads
    assert entry["user_id"] == "root", "감사 주체는 내려받은 사람(관리자)"
    assert entry["file_type"] == "jsonl" and entry["file_size"] == len(r.content)
    assert entry["file_name"] == f"apm_events_{JOB[:8]}.jsonl"


@pytest.mark.asyncio
async def test_rejected_requests_leave_no_download_audit(monkeypatch, store) -> None:
    await _register(store)
    gw, audit = _Gateway("completed"), _Audit()
    _client(monkeypatch, gw, BOB, audit=audit).get(f"/api/v1/apm/jobs/{JOB}/download")
    _client(monkeypatch, gw, ALICE, audit=audit).get(f"/api/v1/apm/jobs/{OTHER}/download")
    assert audit.downloads == []


@pytest.mark.asyncio
async def test_zero_row_result_downloads_header_only(monkeypatch, store) -> None:
    await _register(store)
    gw = _Gateway("completed", chunks=[], columns=["seq", "level"])
    r = _client(monkeypatch, gw, ALICE).get(f"/api/v1/apm/jobs/{JOB}/download?format=csv")
    assert r.status_code == 200
    assert r.content.decode("utf-8-sig").splitlines() == ["seq,level"]
    assert gw.named("apm_job_read") == []


@pytest.mark.asyncio
async def test_gateway_not_configured_is_502_after_ledger_check(monkeypatch, store) -> None:
    await _register(store)
    gw = _Gateway("completed")
    client = _client(monkeypatch, gw, ALICE, endpoint=False)
    assert client.get(f"/api/v1/apm/jobs/{JOB}").status_code == 502
    assert client.get(f"/api/v1/apm/jobs/{OTHER}").status_code == 404, "장부 판정이 먼저"
    assert gw.opened == 0


@pytest.mark.asyncio
async def test_cancel_of_finished_job_keeps_state(monkeypatch, store) -> None:
    await _register(store)
    gw = _Gateway("completed")
    r = _client(monkeypatch, gw, ALICE).post(f"/api/v1/apm/jobs/{JOB}/cancel")
    assert r.status_code == 200 and r.json()["state"] == "completed"
    assert r.json()["downloadable"] is True


# ── 3. 장부 — Redis 없음 · 실패 ────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self, *, connected: bool = True, fail_set: bool = False) -> None:
        self.connected = connected
        self.fail_set = fail_set
        self.data: dict[str, Any] = {}

    async def ensure_connected(self) -> bool:
        return self.connected

    async def get_json(self, key: str) -> Any:
        return self.data.get(key)

    async def set_json(self, key: str, value: Any, *, ex: int | None = None,
                       nx: bool = False) -> bool:
        if self.fail_set:
            raise ConnectionError("redis down")
        self.data[key] = json.loads(json.dumps(value))
        return True


@pytest.mark.asyncio
async def test_no_redis_falls_back_to_memory_and_status_exposes_ledger(monkeypatch) -> None:
    def broken(_cfg):
        raise RuntimeError("cache manager unavailable")

    monkeypatch.setattr(store_mod, "_STORE", None)
    monkeypatch.setattr("src.schema_cache.cache_manager.get_cache_manager", broken)
    ledger = store_mod.get_apm_job_store(SimpleNamespace())
    assert ledger._redis is None
    await _register(ledger)
    gw = _Gateway("running")
    r = _client(monkeypatch, gw, ALICE).get(f"/api/v1/apm/jobs/{JOB}")
    assert r.status_code == 200 and r.json()["ledger"] == "memory", "카드가 재기동 소실을 고지"
    monkeypatch.setattr(store_mod, "_STORE", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("redis", [_FakeRedis(connected=False), _FakeRedis(fail_set=True)],
                         ids=["unreachable", "set-fails"])
async def test_redis_failure_registers_in_memory(redis) -> None:
    ledger = ApmJobStore(redis)
    backend = await ledger.register({"job_id": JOB, "owner_sub": "alice"})
    assert backend == "memory"
    assert (await ledger.get(JOB))["ledger"] == "memory"
    assert [r["job_id"] for r in await ledger.list_for("alice")] == [JOB]


@pytest.mark.asyncio
async def test_owner_index_drops_expired_job_ids() -> None:
    redis = _FakeRedis()
    ledger = ApmJobStore(redis)
    await ledger.register({"job_id": JOB, "owner_sub": "alice"})
    redis.data.pop(f"apm:job:{JOB}")  # 보관 기간 경과(Redis TTL 만료)
    await ledger.register({"job_id": OTHER, "owner_sub": "alice"})
    assert [r["job_id"] for r in await ledger.list_for("alice")] == [OTHER]
    assert redis.data["apm:jobs:owner:alice"] == [OTHER]


@pytest.mark.asyncio
async def test_memory_record_is_not_stale_after_redis_recovers() -> None:
    redis = _FakeRedis(connected=False)
    ledger = ApmJobStore(redis)
    await ledger.register({"job_id": JOB, "owner_sub": "alice", "state": "running"})
    redis.connected = True
    await ledger.update(JOB, {"state": "completed"})
    record = await ledger.get(JOB)
    assert record["state"] == "completed" and record["ledger"] == "redis"
