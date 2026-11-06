"""plans/215 W4 — 관리자 「업무 서비스 정의」 API(인가 · 감사 · version · 입력 검증 · 계산 · 후보 ·
APM 대조 · 게이트웨이 업무 매핑 사본 · 사본 재구성 · 라우트 등록 조건).

인가는 실 `require_admin_user`(210 W6 API 테스트의 JWT·`user_repo`·감사 대역 재사용)로 본다.
저장소는 메모리 가짜(관리자 동작 가짜를 넓힌 것) · 사본·계산·게이트웨이는 대역이다.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.application.biz_service import (
    BizServicePreviewNotFoundError,
    BizServiceSnapshotChangedError,
)
from noise_gate.infrastructure.biz_service_repository import (
    BizServiceExistsError,
    BizServiceMemberExistsError,
    BizServiceVersionConflict,
)
from noise_gate.tests.test_plan215_w3_service import FakeMaps, _sms
from noise_gate.tests.test_plan215_w4_admin import AdminFakeStore, FakeCaller
from src.api.dependencies import require_admin_user
from src.api.routes import admin_biz_service as routes
from tests.test_api.test_plan210_w6_api import (
    ADMIN_BG,
    ADMIN_SECRET,
    ADMIN_USER,
    AUTH_SECRET,
    PLAIN_USER,
    _Audit,
    _Users,
)

BASE = "/api/v1/admin/biz-services"
SECRET_TEXT = "secret-dsn-password-xyz"


class _Repo(AdminFakeStore):
    """라우트가 부르는 저장소 메서드 전부(version 낙관적 잠금 · 중복 판정은 실 저장소와 같다)."""

    def __init__(self) -> None:
        super().__init__()
        self.fail: str | None = None
        self.writes = 0

    def _boom(self) -> None:
        if self.fail:
            raise RuntimeError(f"{self.fail} {SECRET_TEXT}")

    def _v(self, sid: int, version: int | None) -> None:
        s = self.services.get(sid)
        if s is None:
            raise LookupError(sid)
        if version is not None and s["version"] != version:
            raise BizServiceVersionConflict(sid, s["version"])

    def seed(self, key: str, *, name: str | None = None, **extra: Any) -> dict[str, Any]:
        sid = next(self._ids)
        self.services[sid] = {"id": sid, "service_key": key, "display_name": name or key,
                              "tier": None, "description": None, "owner_org": None,
                              "origin": "auto", "state": "active", "verified": False,
                              "monitored": False, "evidence": {}, "version": 1, **extra}
        return self.services[sid]

    def seed_member(self, sid: int, kind: str, key: str, **extra: Any) -> dict[str, Any]:
        mid = next(self._ids)
        self.members[mid] = {"id": mid, "service_id": sid, "kind": kind, "member_key": key,
                             "origin": "auto", "basis": "biz_suffix", "confidence": "confirmed",
                             "state": "active", "layer": None, "evidence": {}, **extra}
        return self.members[mid]

    # 조회
    async def list_services(self, **kw: Any) -> tuple[list[dict[str, Any]], int]:
        self._boom()
        rows = [dict(s) for _, s in sorted(self.services.items())]
        return rows[kw["offset"]:kw["offset"] + kw["limit"]], len(rows)

    async def get_service(self, service_id: int) -> dict[str, Any] | None:
        s = self.services.get(service_id)
        return {**s, "members": [dict(m) for m in self._members_of(service_id)]} if s else None

    async def list_candidates(self, *, status: str | None = "open", kind: str | None = None,
                              service_id: int | None = None, limit: int = 50,
                              offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        rows = [dict(c) for _, c in sorted(self.candidates.items())
                if (status is None or c["status"] == status)
                and (service_id is None or c.get("service_id") == service_id)]
        return rows[offset:offset + limit], len(rows)

    # 서비스
    async def create_service(self, *, service_key: str, display_name: str, by: str = "",
                             **fields: Any) -> dict[str, Any]:
        self._boom()
        hit = next((s for s in self.services.values() if s["service_key"] == service_key), None)
        if hit is not None:
            raise BizServiceExistsError(hit["id"])
        self.writes += 1
        row = self.seed(service_key, name=display_name, origin="manual", **fields)
        return dict(row)

    async def update_service(self, service_id: int, *, version: int,
                             changes: Mapping[str, Any], by: str = "") -> dict[str, Any] | None:
        self._v(service_id, version)
        self.writes += 1
        s = self.services[service_id]
        s.update(changes)
        self._bump(service_id)
        return dict(s)

    async def set_candidates_status(self, candidate_ids: Collection[int], *, status: str,
                                    by: str = "") -> list[dict[str, Any]]:
        out = []
        for cid in candidate_ids:
            c = self.candidates.get(cid)
            if c is not None and c["status"] != status:
                c["status"] = status
                out.append(dict(c))
        return out

    # 구성원(관리자 동작 가짜 + version · 중복)
    async def add_member(self, service_id: int, *, kind: str, member_key: str,
                         version: int | None = None, **kw: Any) -> dict[str, Any]:
        self._boom()
        self._v(service_id, version)
        hit = next((m for m in self.members.values()
                    if (m["kind"], m["member_key"]) == (kind, member_key)), None)
        if hit is not None:
            raise BizServiceMemberExistsError(hit["id"], hit["service_id"])
        self.writes += 1
        return await super().add_member(service_id, kind=kind, member_key=member_key, **kw)

    async def move_members(self, member_ids: Collection[int], *, from_service_id: int,
                           to_service_id: int, from_version: int | None = None,
                           to_version: int | None = None, by: str = "", **_: Any) -> int:
        self._v(from_service_id, from_version)
        self._v(to_service_id, to_version)
        self._bump(from_service_id)
        self._bump(to_service_id)
        return await super().move_members(member_ids, from_service_id=from_service_id,
                                          to_service_id=to_service_id)

    async def merge_services(self, from_service_id: int, to_service_id: int, *,
                             from_version: int | None = None, to_version: int | None = None,
                             **kw: Any) -> dict[str, Any]:
        self._v(from_service_id, from_version)
        self._v(to_service_id, to_version)
        return await super().merge_services(from_service_id, to_service_id, **kw)


class _Cache:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.synced = 0

    async def sync_from_repo(self, repo: Any) -> dict[str, int]:
        if self.fail:
            raise ConnectionError(SECRET_TEXT)
        self.synced += 1
        return {"services": len(await repo.active_services_with_members())}


class _Compute:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[tuple[str, Any]] = []

    async def preview(self, by: str) -> dict[str, Any]:
        self.calls.append(("preview", by))
        if self.exc:
            raise self.exc
        return {"run_id": "p1", "mode": "preview", "scan_run_id": "scan1", "total_changes": 3}

    async def apply(self, preview_run_id: str, by: str) -> dict[str, Any]:
        self.calls.append(("apply", preview_run_id))
        if self.exc:
            raise self.exc
        return {"run_id": "a1", "mode": "apply", "preview_run_id": preview_run_id,
                "applied": {"service_insert": 1}, "cache": {"ok": True}}


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=True, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
        noise_gate=SimpleNamespace(biz_service_active=True),
    )


def _app(*, repo: _Repo | None = None, cache: _Cache | None = None,
         compute: _Compute | None = None, maps: FakeMaps | None = None,
         map_repo_present: bool = True) -> tuple[TestClient, FastAPI, _Repo]:
    repo = repo if repo is not None else _Repo()
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.state.config = _config()
    app.state.user_repo = _Users()
    app.state.audit_service = _Audit()
    app.state.biz_service_repo = repo
    app.state.biz_service_cache = cache if cache is not None else _Cache()
    app.state.biz_service_compute = compute
    app.state.apm_host_map_repo = (maps or FakeMaps(_sms())) if map_repo_present else None
    return TestClient(app), app, repo


def _audits(app: FastAPI) -> list[dict[str, Any]]:
    return app.state.audit_service.actions()


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.routing.registry as reg

    monkeypatch.setattr(reg, "get_registry",
                        lambda: SimpleNamespace(db_ids=lambda: ["pg_a", "pg_b"]))


# ── 인가 · 등록 ────────────────────────────────────────────────────────
def test_admin_both_login_paths_and_unauthenticated_401() -> None:
    client, _, repo = _app()
    repo.seed("svc")
    for headers in (ADMIN_BG, ADMIN_USER):
        res = client.get(BASE, headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == 1 and body["compute_available"] is False
        assert "연계" not in (body["compute_reason"] or "")  # 210 저장소는 있다 → 조립 실패 사유
    assert client.get(BASE).status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), [
    ("get", "", None),
    ("post", "", {"service_key": "x", "display_name": "x"}),
    ("post", "/preview", None),
    ("post", "/candidates/bulk", {"action": "reject", "candidate_ids": [1]}),
    ("post", "/1/merge", {"version": 1, "into_service_id": 2, "into_version": 1}),
    ("get", "/1/gateway-business-map", None),
])
def test_plain_user_403_without_writes_or_audit(method: str, path: str, body: Any) -> None:
    compute = _Compute()
    client, app, repo = _app(compute=compute)
    res = client.request(method, BASE + path, headers=PLAIN_USER, json=body)
    assert res.status_code == 403
    assert repo.writes == 0 and compute.calls == [] and _audits(app) == []


def test_role_claim_rechecked_after_dependency() -> None:
    """의존성이 사용자를 넘겨도 라우트가 role을 다시 본다(서명 검증만으로 끝내지 않는다)."""
    client, app, _ = _app()
    app.dependency_overrides[require_admin_user] = lambda: {"sub": "u", "role": "user"}
    assert client.get(BASE).status_code == 403
    assert client.post(BASE, json={"service_key": "x", "display_name": "x"}).status_code == 403


@pytest.mark.parametrize("enabled", [False, True])
def test_create_app_registers_routes_only_when_active(enabled: bool) -> None:
    from src.api.server import create_app
    from src.config import AppConfig, AuthConfig, NoiseGateConfig

    app = create_app(AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        auth=AuthConfig(enabled=False), noise_gate=NoiseGateConfig(biz_service_enabled=enabled),
    ))
    paths = {p for p in app.openapi()["paths"] if p.startswith(BASE)}
    if not enabled:
        assert paths == set()  # 꺼지면 라우트가 없다 → 404
        return
    assert {BASE, f"{BASE}/preview", f"{BASE}/apply", f"{BASE}/runs", f"{BASE}/analysis",
            f"{BASE}/candidates", f"{BASE}/candidates/bulk",
            f"{BASE}/candidates/{{candidate_id}}/accept", f"{BASE}/{{service_id}}",
            f"{BASE}/{{service_id}}/merge", f"{BASE}/{{service_id}}/split",
            f"{BASE}/{{service_id}}/apm-compare",
            f"{BASE}/{{service_id}}/gateway-business-map"} <= paths


def test_repo_missing_503_and_store_failure_hides_exception_text() -> None:
    client, app, repo = _app()
    app.state.biz_service_repo = None
    res = client.get(BASE, headers=ADMIN_BG)
    assert res.status_code == 503 and res.json()["detail"]["code"] == "biz_service_unavailable"
    app.state.biz_service_repo = repo
    repo.fail = "pg down"
    res = client.post(BASE, json={"service_key": "x", "display_name": "x"}, headers=ADMIN_BG)
    assert res.status_code == 503 and res.json()["detail"]["code"] == "biz_service_store_failed"
    audit = _audits(app)[-1]
    assert audit["outcome"] == "failed" and audit["reason"] == "biz_service_store_failed"
    assert SECRET_TEXT not in res.text and SECRET_TEXT not in json.dumps(_audits(app))


# ── 생성 · 수정 · version · PII ────────────────────────────────────────
def test_create_audit_exists_and_pii_rejected() -> None:
    client, app, repo = _app()
    body = {"service_key": "ibk", "display_name": "인터넷뱅킹", "tier": 1,
            "owner_org": "인프라운영팀"}
    res = client.post(BASE, json=body, headers=ADMIN_BG)
    assert res.status_code == 201, res.text
    got = res.json()
    assert got["service"]["origin"] == "manual" and got["cache_ok"] is True
    assert got["audit_logged"] is True
    audit = _audits(app)[-1]
    assert audit["action"] == "biz_service_create" and audit["outcome"] == "ok"
    assert audit["after"]["service_key"] == "ibk"
    dup = client.post(BASE, json=body, headers=ADMIN_BG)
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "exists"
    assert _audits(app)[-1]["outcome"] == "denied"

    writes = repo.writes
    pii = client.post(BASE, json={**body, "service_key": "x",
                                  "description": "담당 010-1234-5678"}, headers=ADMIN_BG)
    assert pii.status_code == 400
    detail = pii.json()["detail"]
    assert detail["code"] == "pii_rejected" and detail["fields"][0]["field"] == "description"
    assert "010-1234-5678" not in pii.text and repo.writes == writes
    assert _audits(app)[-1]["reason"] == "pii_rejected"
    assert "010-1234-5678" not in json.dumps(_audits(app))


@pytest.mark.parametrize("over", [
    {"tier": 4}, {"description": "x" * 501}, {"owner_org": "x" * 61}, {"service_key": ""},
])
def test_create_field_limits_422(over: dict[str, Any]) -> None:
    client, _, repo = _app()
    body = {"service_key": "s", "display_name": "s", **over}
    assert client.post(BASE, json=body, headers=ADMIN_BG).status_code == 422
    assert repo.writes == 0


def test_update_version_conflict_and_ok_with_cache_failure() -> None:
    client, app, repo = _app(cache=_Cache(fail=True))
    sid = repo.seed("ibk")["id"]
    stale = client.patch(f"{BASE}/{sid}", json={"version": 9, "tier": 2}, headers=ADMIN_BG)
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "version_conflict" and detail["current_version"] == 1
    assert _audits(app)[-1]["outcome"] == "denied" and repo.writes == 0
    res = client.patch(f"{BASE}/{sid}", json={"version": 1, "tier": 2, "verified": True,
                                               "display_name": None}, headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    got = res.json()
    assert got["service"]["tier"] == 2 and got["service"]["verified"] is True
    assert got["service"]["version"] == 2 and got["service"]["display_name"] == "ibk"
    assert got["cache_ok"] is False and "ConnectionError" in got["cache_reason"]
    assert SECRET_TEXT not in res.text
    audit = _audits(app)[-1]
    assert audit["before"]["tier"] is None and audit["after"]["tier"] == 2
    empty = client.patch(f"{BASE}/{sid}", json={"version": 2}, headers=ADMIN_BG)
    assert empty.status_code == 400 and empty.json()["detail"]["code"] == "invalid_request"
    mon = client.post(f"{BASE}/{sid}/monitored", json={"version": 2, "monitored": True},
                      headers=ADMIN_BG)
    assert mon.status_code == 200 and mon.json()["service"]["monitored"] is True
    assert client.get(f"{BASE}/999", headers=ADMIN_BG).status_code == 404


# ── 구성원 ─────────────────────────────────────────────────────────────
def _add(client: TestClient, sid: int, **over: Any) -> Any:
    body = {"version": 1, "kind": "sms_server", "db_id": "pg_a", "resource_id": "101", **over}
    return client.post(f"{BASE}/{sid}/members", json=body, headers=ADMIN_BG)


def test_add_sms_member_snapshot_display_and_refusals(registry: None) -> None:
    client, app, repo = _app()
    sid = repo.seed("ibk")["id"]
    res = _add(client, sid, layer="web")
    assert res.status_code == 201, res.text
    got = res.json()
    assert got["display_source"] == "snapshot" and got["member"]["hostname"] == "ibkweb01"
    assert got["member"]["origin"] == "manual" and got["member"]["confidence"] == "confirmed"
    assert got["service"]["version"] == 2 and got["cache_ok"] is True
    assert _audits(app)[-1]["after"]["member_key"] == "pg_a|101"
    req = _add(client, sid, version=2, resource_id="999", hostname="h999")
    assert req.json()["display_source"] == "request" and req.json()["member"]["hostname"] == "h999"

    other = repo.seed("card")["id"]
    dup = _add(client, other)
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "member_exists"
    assert dup.json()["detail"]["service_id"] == sid
    unknown = _add(client, sid, version=3, db_id="pg_zz")
    assert unknown.status_code == 400 and unknown.json()["detail"]["code"] == "unknown_db_id"
    assert _audits(app)[-1]["reason"] == "unknown_db_id"
    for bad in ({"resource_id": "12a"}, {"resource_id": "1" * 20}, {"layer": "mq"},
                {"kind": "apm_instance", "source_id": "Jen1", "domain_id": "1",
                 "instance_name": "a"},
                {"kind": "apm_domain", "source_id": "jen1", "domain_id": "x"}):
        assert _add(client, sid, version=3, **bad).status_code == 422, bad
    missing = _add(client, sid, version=3, kind="apm_domain", source_id="jen1", domain_id="")
    assert missing.status_code == 400 and missing.json()["detail"]["code"] == "invalid_member"


def test_add_apm_instance_without_snapshot_repo(registry: None) -> None:
    client, _, repo = _app(map_repo_present=False)
    sid = repo.seed("ibk")["id"]
    res = _add(client, sid, kind="apm_instance", source_id="jen1", domain_id="10",
               instance_name="WAS01_1", domain_name="IBK")
    assert res.status_code == 201, res.text
    assert res.json()["member"]["member_key"] == "jen1|10|instance|was01_1"
    assert res.json()["member"]["domain_name"] == "IBK"


def test_member_state_confirm_move() -> None:
    client, app, repo = _app()
    a, b = repo.seed("a")["id"], repo.seed("b")["id"]
    m1 = repo.seed_member(a, "sms_server", "pg_a|101")["id"]
    m2 = repo.seed_member(a, "sms_server", "pg_a|102")["id"]
    res = client.post(f"{BASE}/{a}/members/state",
                      json={"version": 1, "member_ids": [m1], "state": "excluded"},
                      headers=ADMIN_BG)
    assert res.status_code == 200 and res.json()["changed"] == 1
    assert repo.members[m1]["state"] == "excluded"
    res = client.post(f"{BASE}/{a}/members/confirm",
                      json={"version": 1, "member_ids": [m2], "confirmed": False},
                      headers=ADMIN_BG)
    assert res.status_code == 200
    assert repo.members[m2]["confidence"] == "inferred" and repo.members[m2]["origin"] == "manual"
    stale = client.post(f"{BASE}/{a}/members/move",
                        json={"version": 1, "member_ids": [m2], "to_service_id": b,
                              "to_version": 5}, headers=ADMIN_BG)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "version_conflict"
    res = client.post(f"{BASE}/{a}/members/move",
                      json={"version": 1, "member_ids": [m2], "to_service_id": b,
                            "to_version": 1}, headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    assert res.json()["changed"] == 1 and res.json()["to_service"]["id"] == b
    assert repo.members[m2]["service_id"] == b
    actions = [x["action"] for x in _audits(app)]
    assert actions == ["biz_service_member_state", "biz_service_member_confirm",
                       "biz_service_member_move", "biz_service_member_move"]
    too_many = client.post(f"{BASE}/{a}/members/state",
                           json={"version": 1, "member_ids": list(range(1, 202)),
                                 "state": "active"}, headers=ADMIN_BG)
    assert too_many.status_code == 422


def test_merge_and_split() -> None:
    client, app, repo = _app()
    a, b = repo.seed("a")["id"], repo.seed("b")["id"]
    m1 = repo.seed_member(a, "sms_server", "pg_a|101")["id"]
    repo.seed_member(a, "sms_server", "pg_a|102")
    res = client.post(f"{BASE}/{b}/split",
                      json={"version": 1, "member_ids": [m1], "service_key": "new",
                            "display_name": "새", "owner_org": "hong@kb.co.kr"},
                      headers=ADMIN_BG)
    assert res.status_code == 400 and res.json()["detail"]["code"] == "pii_rejected"
    res = client.post(f"{BASE}/{a}/merge",
                      json={"version": 1, "into_service_id": b, "into_version": 1},
                      headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    assert res.json()["moved"] == 2 and repo.services[a]["state"] == "disabled"
    assert _audits(app)[-1]["after"] == {"from_service_id": a, "to_service_id": b, "moved": 2,
                                         "from_version": res.json()["from_version"],
                                         "to_version": res.json()["to_version"]}
    b_version = repo.services[b]["version"]
    res = client.post(f"{BASE}/{b}/split",
                      json={"version": b_version, "member_ids": [m1], "service_key": "new",
                            "display_name": "새"}, headers=ADMIN_BG)
    assert res.status_code == 201, res.text
    new_id = res.json()["service"]["id"]
    assert res.json()["moved"] == 1 and repo.members[m1]["service_id"] == new_id


# ── 후보 ───────────────────────────────────────────────────────────────
def _cands(repo: _Repo, sid: int) -> dict[str, int]:
    out = {}
    for name, extra in (
        ("member", {"kind": "member", "member_kind": "sms_server", "member_key": "pg_a|103",
                    "signals": [{"basis": "aux:ip_subnet"}], "evidence": {}}),
        ("unlinked", {"kind": "apm_unlinked"}),
        ("member2", {"kind": "member", "member_kind": "sms_server", "member_key": "pg_a|201",
                     "signals": [], "evidence": {}}),
    ):
        cid = next(repo._ids)
        repo.candidates[cid] = {"id": cid, "status": "open", "service_id": sid,
                                "candidate_key": name, **extra}
        out[name] = cid
    return out


def test_candidate_accept_reject_single() -> None:
    client, app, repo = _app()
    sid = repo.seed("ibk")["id"]
    c = _cands(repo, sid)
    res = client.post(f"{BASE}/candidates/{c['member']}/accept", headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    got = res.json()
    assert got["result"]["kind"] == "member" and got["cache_ok"] is True
    member = repo.member("pg_a|103")
    assert member["origin"] == "manual" and member["evidence"]["aux"] == [
        {"basis": "aux:ip_subnet"}]
    assert member["hostname"] == "ibkdb01"  # 스냅샷 표시 칸
    audit = _audits(app)[-1]
    assert audit["action"] == "biz_service_candidate_accept"
    assert audit["after"]["member"]["member_key"] == "pg_a|103"
    again = client.post(f"{BASE}/candidates/{c['member']}/reject", headers=ADMIN_BG)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "candidate_closed"
    assert repo.candidates[c["member"]]["status"] == "accepted"  # 수락을 거절로 바꾸지 않는다
    no = client.post(f"{BASE}/candidates/{c['unlinked']}/accept", headers=ADMIN_BG)
    assert no.status_code == 400 and no.json()["detail"]["code"] == "candidate_not_acceptable"
    rej = client.post(f"{BASE}/candidates/{c['unlinked']}/reject", headers=ADMIN_BG)
    assert rej.status_code == 200 and rej.json()["candidate"]["status"] == "rejected"
    assert client.post(f"{BASE}/candidates/999/accept", headers=ADMIN_BG).status_code == 404
    listed = client.get(f"{BASE}/candidates", params={"status": "open"}, headers=ADMIN_BG)
    assert [r["id"] for r in listed.json()["rows"]] == [c["member2"]]
    detail = client.get(f"{BASE}/{sid}", headers=ADMIN_BG).json()
    assert [r["id"] for r in detail["candidates"]] == [c["member2"]]
    assert len(detail["service"]["members"]) == 1


def test_candidate_bulk_per_row_audit_with_batch_id() -> None:
    client, app, repo = _app()
    c = _cands(repo, repo.seed("ibk")["id"])
    ids = [c["member"], c["unlinked"], c["member2"], 999]
    res = client.post(f"{BASE}/candidates/bulk",
                      json={"action": "accept", "candidate_ids": ids}, headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [(r["candidate_id"], r["outcome"], r["code"]) for r in body["results"]] == [
        (c["member"], "ok", None), (c["unlinked"], "denied", "candidate_not_acceptable"),
        (c["member2"], "ok", None), (999, "denied", "not_found")]
    assert body["counts"] == {"ok": 2, "denied": 2, "failed": 0} and body["cache_ok"] is True
    audits = _audits(app)
    assert len(audits) == 4 and {a["batch_id"] for a in audits} == {body["batch_id"]}
    assert [a["outcome"] for a in audits] == ["ok", "denied", "ok", "denied"]
    over = client.post(f"{BASE}/candidates/bulk",
                       json={"action": "reject", "candidate_ids": list(range(1, 202))},
                       headers=ADMIN_BG)
    assert over.status_code == 422


# ── 계산 · 실행 기록 · 분석 ────────────────────────────────────────────
def test_preview_apply_mapping_and_scan_disabled() -> None:
    client, app, _ = _app(map_repo_present=False)
    res = client.post(f"{BASE}/preview", headers=ADMIN_BG)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "scan_disabled"
    assert "NOISE_APM_HOST_MAP_ENABLED" in res.json()["detail"]["reason"]
    assert _audits(app)[-1]["outcome"] == "denied"

    compute = _Compute()
    client, app, _ = _app(compute=compute)
    res = client.post(f"{BASE}/preview", headers=ADMIN_BG)
    assert res.status_code == 200 and res.json()["run_id"] == "p1"
    assert res.json()["audit_logged"] is True and compute.calls == [("preview", "ops")]
    res = client.post(f"{BASE}/apply", json={"preview_run_id": "p1"}, headers=ADMIN_BG)
    assert res.status_code == 200 and res.json()["applied"] == {"service_insert": 1}
    assert _audits(app)[-1]["after"]["run_id"] == "a1"
    assert client.post(f"{BASE}/apply", json={"preview_run_id": "../x"},
                       headers=ADMIN_BG).status_code == 422
    for exc, status, code in ((BizServiceSnapshotChangedError("바뀜"), 409, "snapshot_changed"),
                              (BizServicePreviewNotFoundError("없음"), 404,
                               "preview_not_found")):
        client, app, _ = _app(compute=_Compute(exc))
        res = client.post(f"{BASE}/apply", json={"preview_run_id": "p1"}, headers=ADMIN_BG)
        assert (res.status_code, res.json()["detail"]["code"]) == (status, code)
        assert _audits(app)[-1]["reason"] == code


def test_runs_and_analysis() -> None:
    client, _, repo = _app()
    assert client.get(f"{BASE}/analysis", headers=ADMIN_BG).json()["summary"] is None
    run = repo.runs["a9"] = {"run_id": "a9", "mode": "apply", "status": "done", "seq": 1,
                             "finished_at": "2026-11-05T00:00:00+00:00",
                             "summary": {"preview_run_id": "p9", "services": 3}}
    repo.runs["p9"] = {"run_id": "p9", "mode": "preview", "status": "done", "seq": 0,
                       "summary": {}}
    runs = client.get(f"{BASE}/runs", headers=ADMIN_BG).json()["runs"]
    assert [r["run_id"] for r in runs] == ["a9", "p9"]
    got = client.get(f"{BASE}/analysis", headers=ADMIN_BG).json()
    assert got["run_id"] == "a9" and got["preview_run_id"] == "p9"
    assert got["summary"] == run["summary"] and got["reason"] is None


# ── APM 대조 · 게이트웨이 업무 매핑 사본 ────────────────────────────────
def test_apm_compare_and_gateway_business_map(monkeypatch: pytest.MonkeyPatch) -> None:
    client, app, repo = _app()
    sid = repo.seed("ibk", name="인터넷뱅킹")["id"]
    repo.seed_member(sid, "apm_instance", "jen1|10|instance|web-a", instance_name="web-a")
    members_before = {k: dict(v) for k, v in repo.members.items()}
    monkeypatch.setattr(routes, "_gateway_caller", lambda config: None)
    res = client.post(f"{BASE}/{sid}/apm-compare", headers=ADMIN_BG)
    assert res.status_code == 503 and res.json()["detail"]["code"] == "gateway_unavailable"

    caller = FakeCaller({"rows": [
        {"source_id": "jen1", "domain_id": "10", "instance_name": "web-a"},
        {"source_id": "jen1", "domain_id": "10", "instance_name": "web-b"}]})
    monkeypatch.setattr(routes, "_gateway_caller", lambda config: caller)
    res = client.post(f"{BASE}/{sid}/apm-compare", headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    got = res.json()
    assert [r["instance_name"] for r in got["only_in_gateway"]] == ["web-b"]
    assert got["candidates"]["inserted"] == 1 and got["audit_logged"] is True
    assert repo.members == members_before  # 구성원은 그대로(수용 18)
    assert _audits(app)[-1]["after"]["only_in_gateway"] == 1

    yml = client.get(f"{BASE}/{sid}/gateway-business-map", headers=ADMIN_BG)
    assert yml.status_code == 200
    body = yml.json()
    assert body["instance_count"] == 1 and "business_map:" in body["yaml"]
    assert body["entries"][0]["instances"] == ["web-a"]


# ── 기동 배선(계산 서비스 조립 — 210 저장소가 있을 때만 · Q5) ────────────
@pytest.mark.parametrize("with_map_repo", [False, True])
async def test_startup_assembles_compute_only_with_scan_repo(
    monkeypatch: pytest.MonkeyPatch, with_map_repo: bool
) -> None:
    from noise_gate.application import biz_service as svc_mod
    from noise_gate.infrastructure import biz_service_cache as cache_mod
    from noise_gate.infrastructure.decision_store import DecisionStore
    from noise_gate.tests.test_plan215_w2_cache import _Redis
    from noise_gate.tests.test_plan215_w2_repository import _Conn, _Pool
    from src.api import server
    from tests.test_plan215_w2_startup import _RecState

    built: list[dict[str, Any]] = []

    def _build(config: Any, **kw: Any) -> str:
        built.append(kw)
        return "compute"

    monkeypatch.setattr(cache_mod, "connect_redis", lambda cfg: _Redis())
    monkeypatch.setattr(svc_mod, "build_biz_service", _build)
    initial: dict[str, Any] = {"auth_pool": _Pool(_Conn())}
    if with_map_repo:
        initial["apm_host_map_repo"] = "maps"
    state = _RecState(**initial)
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(biz_service_active=True,
                                   decision_store_path="logs/x.jsonl"),
        redis=SimpleNamespace(host="127.0.0.1", port=6399, password="", db=0),
    )
    await server._start_biz_service(SimpleNamespace(state=state), cfg)  # type: ignore[arg-type]
    if not with_map_repo:
        assert built == [] and "biz_service_compute" not in state.assigned
        return
    assert state.assigned[-1] == "biz_service_compute" and state.biz_service_compute == "compute"
    kw = built[0]
    assert kw["map_repo"] == "maps" and kw["cache"] is state.biz_service_cache
    assert kw["repo"] is state.biz_service_repo and isinstance(kw["decisions"], DecisionStore)
