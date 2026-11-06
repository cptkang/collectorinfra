"""plans/215 W5 — 관리자 편집 보조 API.

스냅샷 검색 · 미소속 서버 · 일괄 지정 · 계층 일괄 · 변경 이력.

인가·감사는 W4 라우트 테스트와 같은 대역(실 `require_admin_user` · JWT · 감사 서비스 대역)을 쓴다.
저장소는 W4 가짜에 W5 메서드(소속 조회 · 일괄 지정 · 계층)를 더한 것이다. 210 저장소 가짜는 검색어·
상한을 받아 실 저장소처럼 부분 일치만 흉내 내고(이스케이프는 실 저장소 `_like`가 한다)
받은 값을 기록한다.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.tests.test_plan215_w3_service import FakeMaps, _sms
from tests.test_api.test_plan210_w6_api import ADMIN_BG, ADMIN_USER, PLAIN_USER
from tests.test_api.test_plan215_w4_admin_biz_service import _app as _w4_app
from tests.test_api.test_plan215_w4_admin_biz_service import _audits
from tests.test_api.test_plan215_w4_admin_biz_service import _Repo as _W4Repo

BASE = "/api/v1/admin/biz-services"


class _Repo(_W4Repo):
    """W4 가짜 + W5 메서드(실 저장소와 같은 판정 — 대상 version 확인 · 1서버 1서비스)."""

    async def member_owners(self, kind: str, member_keys: Collection[str] | None = None
                            ) -> dict[str, dict[str, Any]]:
        out = {}
        for m in self.members.values():
            if m["kind"] != kind:
                continue
            if member_keys is not None and m["member_key"] not in member_keys:
                continue
            s = self.services[m["service_id"]]
            out[m["member_key"]] = {"member_id": m["id"], "service_id": s["id"],
                                    "service_name": s["display_name"], "state": m["state"]}
        return out

    async def assign_members(self, service_id: int,
                             items: Sequence[tuple[str, str, Mapping[str, Any]]], *,
                             version: int | None = None, by: str = "") -> list[dict[str, Any]]:
        self._v(service_id, version)
        self.writes += 1
        out = []
        for kind, key, display in items:
            hit = next((m for m in self.members.values()
                        if (m["kind"], m["member_key"]) == (kind, key)), None)
            if hit is None:
                m = self.seed_member(service_id, kind, key, origin="manual", basis="manual",
                                     **dict(display))
                out.append({"member": dict(m), "outcome": "added", "moved_from": None})
                continue
            old = hit["service_id"]
            hit.update(service_id=service_id, origin="manual", confidence="confirmed",
                       state="active")
            out.append({"member": dict(hit), "outcome": "moved" if old != service_id else "kept",
                        "moved_from": old if old != service_id else None})
        self._bump(service_id)
        return out

    async def set_members_layer(self, service_id: int, member_ids: Collection[int], *,
                                layer: str | None, version: int | None = None,
                                by: str = "") -> int:
        self._v(service_id, version)
        self.writes += 1
        n = 0
        for mid in member_ids:
            m = self.members.get(mid)
            if m and m["service_id"] == service_id:
                m.update(layer=layer, layer_origin="manual")
                n += 1
        self._bump(service_id)
        return n


class _Maps(FakeMaps):
    """검색어·상한을 받는 210 저장소 가짜(받은 인자를 기록)."""

    def __init__(self, sms: list[dict[str, Any]], apm: list[dict[str, Any]] | None = None) -> None:
        super().__init__(sms)
        self.rows["apm_inventory"] = apm or []
        self.current["apm_inventory"] = "scan1"
        self.searches: list[tuple[str, str | None, int | None]] = []

    async def snapshot_rows(self, table: str, *, query: str | None = None,  # type: ignore[override]
                            limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        self.searches.append((table, query, limit))
        rows = [dict(r) for r in self.rows.get(table, [])]
        if query:
            q = query.lower()
            rows = [r for r in rows if any(q in str(v).lower() for v in r.values())]
        return rows[:limit] if limit is not None else rows


def _app(*, maps: _Maps | None = None, map_repo_present: bool = True
         ) -> tuple[TestClient, FastAPI, _Repo]:
    client, app, repo = _w4_app(repo=_Repo(), map_repo_present=map_repo_present)
    if map_repo_present:
        app.state.apm_host_map_repo = maps or _Maps(_sms())
    return client, app, repo  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import src.routing.registry as reg

    monkeypatch.setattr(reg, "get_registry",
                        lambda: SimpleNamespace(db_ids=lambda: ["pg_a", "pg_b"]))


# ── 인가 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("method", "path", "body"), [
    ("get", "/inventory/sms?q=ibk", None),
    ("get", "/inventory/apm?q=ibk", None),
    ("get", "/unassigned", None),
    ("post", "/bulk-assign", {"service_id": 1, "version": 1,
                              "servers": [{"db_id": "pg_a", "resource_id": "101"}]}),
    ("patch", "/1/members/layer", {"version": 1, "member_ids": [2], "layer": "web"}),
    ("get", "/1/history", None),
])
def test_plain_user_403_and_unauthenticated_401(method: str, path: str, body: Any) -> None:
    client, app, repo = _app()
    svc = repo.seed("svc")
    repo.seed_member(svc["id"], "sms_server", "pg_a|101")
    res = client.request(method, BASE + path, headers=PLAIN_USER, json=body)
    assert res.status_code == 403
    assert repo.writes == 0 and _audits(app) == []
    assert client.request(method, BASE + path, json=body).status_code == 401


# ── 스냅샷 검색 ────────────────────────────────────────────────────────
def test_inventory_query_length_rules() -> None:
    client, _, _ = _app()
    assert client.get(f"{BASE}/inventory/sms", headers=ADMIN_BG).status_code == 422
    assert client.get(f"{BASE}/inventory/sms?q=a", headers=ADMIN_BG).status_code == 422
    assert client.get(f"{BASE}/inventory/sms", params={"q": "x" * 65},
                      headers=ADMIN_BG).status_code == 422
    res = client.get(f"{BASE}/inventory/sms", params={"q": " a "}, headers=ADMIN_BG)
    assert res.status_code == 400 and res.json()["detail"]["code"] == "invalid_request"


def test_inventory_passes_raw_text_to_bound_like_search() -> None:
    """검색어는 다듬기만 해서 210 저장소로 넘긴다 — 와일드카드 이스케이프·바인드는 그쪽 몫."""
    from noise_gate.infrastructure.apm_host_map_repository import _like

    maps = _Maps(_sms())
    client, _, _ = _app(maps=maps)
    res = client.get(f"{BASE}/inventory/sms", params={"q": " 10%_x' "}, headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    assert maps.searches[-1] == ("sms_inventory", "10%_x'", 51)
    assert _like("10%_x'") == "%10\\%\\_x'%"


def test_inventory_sms_owner_and_cap_50() -> None:
    names = {str(1000 + i): f"ibkweb{i:02d}" for i in range(60)}
    client, _, repo = _app(maps=_Maps(_sms(names)))
    svc = repo.seed("ibk", name="인터넷뱅킹")
    repo.seed_member(svc["id"], "sms_server", "pg_a|1000")
    res = client.get(f"{BASE}/inventory/sms", params={"q": "ibkweb"}, headers=ADMIN_BG)
    body = res.json()
    assert set(body) == {"rows", "truncated", "limit", "snapshot_run_id", "reason"}
    assert len(body["rows"]) == 50 and body["truncated"] is True and body["limit"] == 50
    first = body["rows"][0]
    assert first["member_key"] == "pg_a|1000"
    assert first["owner"]["service_id"] == svc["id"]
    assert first["owner"]["service_name"] == "인터넷뱅킹"
    assert body["rows"][1]["owner"] is None and body["snapshot_run_id"] == "scan1"


def test_inventory_apm_rows_and_member_key() -> None:
    apm = [{"source_id": "jen1", "domain_id": "10", "domain_name": "IBK", "instance_name":
            "ibkwas01", "ip": "10.0.1.1", "host_name": "ibkwas01"}]
    client, _, _ = _app(maps=_Maps(_sms(), apm))
    body = client.get(f"{BASE}/inventory/apm", params={"q": "ibkwas"}, headers=ADMIN_BG).json()
    assert len(body["rows"]) == 1 and body["truncated"] is False
    row = body["rows"][0]
    assert row["instance_name"] == "ibkwas01" and row["member_key"] and row["owner"] is None


def test_inventory_scan_disabled_and_no_snapshot() -> None:
    client, _, _ = _app(map_repo_present=False)
    res = client.get(f"{BASE}/inventory/sms", params={"q": "ibk"}, headers=ADMIN_BG)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "scan_disabled"
    maps = _Maps(_sms())
    maps.current.clear()
    client, _, _ = _app(maps=maps)
    body = client.get(f"{BASE}/inventory/sms", params={"q": "ibk"}, headers=ADMIN_BG).json()
    assert body["rows"] == [] and body["snapshot_run_id"] is None and body["reason"]


# ── 미소속 서버 ────────────────────────────────────────────────────────
def test_unassigned_excludes_active_members_and_marks_excluded() -> None:
    client, _, repo = _app()
    svc = repo.seed("ibk", name="인터넷뱅킹")
    repo.seed_member(svc["id"], "sms_server", "pg_a|101")
    repo.seed_member(svc["id"], "sms_server", "pg_a|102", state="excluded")
    res = client.get(f"{BASE}/unassigned", headers=ADMIN_USER)
    assert res.status_code == 200, res.text
    body = res.json()
    assert {"groups", "total", "truncated", "limit", "snapshot_run_id", "reason"} <= set(body)
    servers = [s for g in body["groups"] for s in g["servers"]]
    keys = {s["member_key"] for s in servers}
    assert "pg_a|101" not in keys and "pg_a|102" in keys and body["total"] == 5
    excluded = next(s for s in servers if s["member_key"] == "pg_a|102")
    assert excluded["excluded_from"]["service_name"] == "인터넷뱅킹"


# ── 일괄 지정 ──────────────────────────────────────────────────────────
def test_bulk_assign_three_rows_one_batch_id() -> None:
    client, app, repo = _app()
    target = repo.seed("ibk", name="인터넷뱅킹")
    other = repo.seed("card", name="카드")
    repo.seed_member(other["id"], "sms_server", "pg_a|103", state="excluded")
    body = {"service_id": target["id"], "version": 1, "servers": [
        {"db_id": "pg_a", "resource_id": "101"}, {"db_id": "pg_a", "resource_id": "102"},
        {"db_id": "pg_a", "resource_id": "103"}]}
    res = client.post(f"{BASE}/bulk-assign", json=body, headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    got = res.json()
    assert set(got) == {"batch_id", "service_id", "results", "counts", "service", "cache_ok",
                        "cache_reason", "audit_logged"}
    assert got["counts"] == {"added": 2, "moved": 1, "kept": 0}
    assert [r["outcome"] for r in got["results"]] == ["added", "added", "moved"]
    assert got["results"][2]["moved_from"] == other["id"]
    rows = [a for a in _audits(app) if a["action"] == "biz_service_bulk_assign"]
    assert len(rows) == 3 and {a["batch_id"] for a in rows} == {got["batch_id"]}
    assert all(a["outcome"] == "ok" and a["service_id"] == target["id"] for a in rows)
    assert rows[2]["before"] == {"service_id": other["id"]} and rows[0]["before"] is None
    added = next(m for m in repo.members.values() if m["member_key"] == "pg_a|101")
    assert added["server_name"] == "ibkweb01(인터넷뱅킹)"  # 표시 칸은 스냅샷에서
    assert repo.services[target["id"]]["version"] == 2


def test_bulk_assign_version_conflict_and_input_limits() -> None:
    client, app, repo = _app()
    target = repo.seed("ibk", version=3)
    one = [{"db_id": "pg_a", "resource_id": "101"}]
    res = client.post(f"{BASE}/bulk-assign", json={"service_id": target["id"], "version": 1,
                                                   "servers": one}, headers=ADMIN_BG)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "version_conflict"
    assert _audits(app)[-1]["outcome"] == "denied" and repo.writes == 0
    unknown = client.post(f"{BASE}/bulk-assign", json={
        "service_id": target["id"], "version": 3,
        "servers": [{"db_id": "zz", "resource_id": "101"}]}, headers=ADMIN_BG)
    assert unknown.status_code == 400 and unknown.json()["detail"]["code"] == "unknown_db_id"
    many = [{"db_id": "pg_a", "resource_id": str(i)} for i in range(201)]
    for servers in ([], many, [{"db_id": "pg_a", "resource_id": "1;drop"}]):
        res = client.post(f"{BASE}/bulk-assign", json={"service_id": target["id"], "version": 3,
                                                       "servers": servers}, headers=ADMIN_BG)
        assert res.status_code == 422
    assert repo.writes == 0


# ── 계층 일괄 ──────────────────────────────────────────────────────────
def test_members_layer_bulk_and_clear() -> None:
    client, app, repo = _app()
    svc = repo.seed("ibk")
    a = repo.seed_member(svc["id"], "sms_server", "pg_a|101")
    b = repo.seed_member(svc["id"], "sms_server", "pg_a|102")
    res = client.patch(f"{BASE}/{svc['id']}/members/layer",
                       json={"version": 1, "member_ids": [a["id"], b["id"]], "layer": "was"},
                       headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    got = res.json()
    assert set(got) == {"changed", "service", "cache_ok", "cache_reason", "audit_logged"}
    assert got["changed"] == 2 and repo.members[a["id"]]["layer"] == "was"
    audit = _audits(app)[-1]
    assert audit["action"] == "biz_service_member_layer" and audit["layer"] == "was"
    res = client.patch(f"{BASE}/{svc['id']}/members/layer",
                       json={"version": 2, "member_ids": [a["id"]], "layer": None},
                       headers=ADMIN_BG)
    assert res.status_code == 200 and repo.members[a["id"]]["layer"] is None
    bad = client.patch(f"{BASE}/{svc['id']}/members/layer",
                       json={"version": 3, "member_ids": [a["id"]], "layer": "cache"},
                       headers=ADMIN_BG)
    assert bad.status_code == 422
    stale = client.patch(f"{BASE}/{svc['id']}/members/layer",
                         json={"version": 1, "member_ids": [a["id"]], "layer": "db"},
                         headers=ADMIN_BG)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "version_conflict"


# ── 변경 이력 ──────────────────────────────────────────────────────────
class _AuditRepo:
    """감사 저장소 대역 — 감사 서비스 대역에 쌓인 항목을 최근 순 DB 행 모양으로 돌려준다."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.calls: list[dict[str, Any]] = []

    async def query_logs_paginated(self, **kw: Any) -> tuple[list[dict[str, Any]], int]:
        self.calls.append(kw)
        rows = [{"created_at": f"2026-11-05T09:00:{i:02d}", "user_id": e.user_id,
                 "detail": {"request_id": None, "success": True, "extra": e.extra}}
                for i, e in enumerate(self.app.state.audit_service.entries)]
        return rows[::-1], len(rows)


def test_history_lists_this_service_only_recent_first() -> None:
    client, app, repo = _app()
    svc = repo.seed("ibk")
    other = repo.seed("card")
    client.patch(f"{BASE}/{svc['id']}", json={"version": 1, "display_name": "인뱅"},
                 headers=ADMIN_BG)
    client.patch(f"{BASE}/{other['id']}", json={"version": 1, "display_name": "카드2"},
                 headers=ADMIN_BG)
    client.post(f"{BASE}/bulk-assign", json={"service_id": svc["id"], "version": 2, "servers": [
        {"db_id": "pg_a", "resource_id": "101"}]}, headers=ADMIN_BG)
    res = client.get(f"{BASE}/{svc['id']}/history", headers=ADMIN_BG)
    assert res.json()["reason"]  # 감사 저장소(DB)가 없으면 사유
    app.state.audit_repo = _AuditRepo(app)
    res = client.get(f"{BASE}/{svc['id']}/history", headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"service_id", "rows", "scanned", "reason"} and body["reason"] is None
    assert [r["action"] for r in body["rows"]] == ["biz_service_bulk_assign",
                                                   "biz_service_update"]
    assert body["rows"][0]["batch_id"] and body["rows"][1]["after"]["display_name"] == "인뱅"
    assert app.state.audit_repo.calls[-1]["keyword"] == "biz_service"
    limited = client.get(f"{BASE}/{svc['id']}/history", params={"limit": 1},
                         headers=ADMIN_BG).json()
    assert len(limited["rows"]) == 1
    assert client.get(f"{BASE}/{svc['id']}/history", params={"limit": 51},
                      headers=ADMIN_BG).status_code == 422
    assert client.get(f"{BASE}/999/history", headers=ADMIN_BG).status_code == 404
