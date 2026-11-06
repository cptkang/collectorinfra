"""plans/215 W5 — 「업무 서비스 정의」(P1) 조회 API(목록 · CSV · 상세) · 페이지 라우트 · 등록 조건.

인가는 실 `require_user`(사용자 JWT `type=user` · `user_repo` 대역 — 존 부여)로 본다. 저장소는
라우트가 부르는 조회 메서드만 둔 메모리 가짜다(SQL은 저장소 테스트가 본다).
"""

from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import biz_service as routes
from tests.test_api.test_plan215_w6_biz_monitor import (
    ADMIN_SECRET,
    AUTH_SECRET,
    ZA,
    ZB,
    _hdr,
    _Users,
)

BASE = "/api/v1/biz-services"
SECRET_TEXT = "secret-dsn-password-xyz"


def _m(mid: int, sid: int, rid: str, host: str, zone: str, **extra: Any) -> dict[str, Any]:
    return {"id": mid, "service_id": sid, "kind": "sms_server", "member_key": f"pg|{rid}",
            "db_id": "pg", "resource_id": rid, "server_name": host, "hostname": host,
            "ip": "10.0.0." + rid, "zone": zone, "state": "active", "confidence": "confirmed",
            "origin": "auto", "basis": "biz_suffix", "layer": None, "evidence": {"why": "x"},
            **extra}


def _services() -> dict[int, dict[str, Any]]:
    return {
        1: {"id": 1, "service_key": "loan", "display_name": "여신", "tier": 1, "state": "active",
            "verified": True, "monitored": True, "origin": "auto", "version": 3,
            "description": "AKIAIOSFODNN7EXAMPLE", "owner_org": "여신팀", "note": "관리 메모",
            "members": [_m(11, 1, "101", "loan-01", ZA),
                        _m(12, 1, "102", "loan-02", ZB, confidence="inferred")]},
        2: {"id": 2, "service_key": "card", "display_name": "카드비밀", "tier": 2,
            "state": "ambiguous", "verified": False, "monitored": False, "origin": "auto",
            "version": 1, "description": None, "owner_org": None,
            "members": [_m(21, 2, "201", "card-01", ZB)]},
        3: {"id": 3, "service_key": "fx", "display_name": "=SUM(1)", "tier": None,
            "state": "active", "verified": False, "monitored": False, "origin": "manual",
            "version": 1, "description": None, "owner_org": "+cmd",
            "members": [_m(31, 3, "301", "@fx-01", ZA, layer="was")]},
    }


class _Repo:
    """조회 메서드 가짜 — 검색은 표시명 부분 일치만 흉내 낸다."""

    def __init__(self) -> None:
        self.services = _services()
        self.runs: list[dict[str, Any]] = []
        self.branches: dict[tuple[str, str], str] = {}
        self.fail = False

    def _boom(self) -> None:
        if self.fail:
            raise RuntimeError(SECRET_TEXT)

    async def search_services(self, *, tier: int | None = None, state: str | None = None,
                              monitored: bool | None = None, query: str | None = None,
                              member_zones: Any = None, **_: Any) -> list[dict[str, Any]]:
        self._boom()
        out = []
        for _, s in sorted(self.services.items()):
            if tier is not None and s["tier"] != tier:
                continue
            if state is not None and s["state"] != state:
                continue
            if monitored is not None and s["monitored"] != monitored:
                continue
            hosts = [m["hostname"] for m in s["members"]
                     if member_zones is None or m["zone"] in member_zones]
            if query and query not in s["display_name"] and not any(query in h for h in hosts):
                continue
            out.append({k: v for k, v in s.items() if k != "members"})
        return out

    async def member_stats(self) -> list[dict[str, Any]]:
        out: dict[tuple[Any, ...], int] = {}
        for s in self.services.values():
            for m in s["members"]:
                if m["state"] != "active":
                    continue
                key = (m["service_id"], m["kind"], m["zone"], m["db_id"], None, m["confidence"])
                out[key] = out.get(key, 0) + 1
        cols = ("service_id", "kind", "zone", "db_id", "source_id", "confidence")
        return [{**dict(zip(cols, k, strict=True)), "n": n} for k, n in out.items()]

    async def list_runs(self, *, mode: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [r for r in self.runs if mode is None or r["mode"] == mode][:limit]

    async def get_service(self, service_id: int) -> dict[str, Any] | None:
        self._boom()
        s = self.services.get(service_id)
        return json.loads(json.dumps(s)) if s else None

    async def owner_branches(self, refs: Any) -> dict[tuple[str, str], str]:
        return {r: self.branches[r] for r in refs if r in self.branches}

    async def all_services_with_members(self) -> list[dict[str, Any]]:
        return [json.loads(json.dumps(s)) for _, s in sorted(self.services.items())]


class _Maps:
    def __init__(self) -> None:
        self.calls = 0

    async def all_rows(self) -> list[dict[str, Any]]:
        self.calls += 1
        return []


def _config(*, auth: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=auth, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
        security=SimpleNamespace(mask_pattern="***"),
        noise_gate=SimpleNamespace(biz_service_active=True),
    )


def _app(*, auth: bool = True) -> tuple[TestClient, FastAPI, _Repo]:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.state.config = _config(auth=auth)
    app.state.user_repo = _Users()
    repo = _Repo()
    app.state.biz_service_repo = repo
    app.state.apm_host_map_repo = _Maps()
    return TestClient(app), app, repo


# ── 등록 조건 · 페이지 ─────────────────────────────────────────────────
@pytest.mark.parametrize("enabled", [False, True])
def test_flag_gates_api_and_page(enabled: bool) -> None:
    from src.api.server import create_app
    from src.config import AppConfig, AuthConfig, NoiseGateConfig

    app = create_app(AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        auth=AuthConfig(enabled=False), noise_gate=NoiseGateConfig(biz_service_enabled=enabled),
    ))
    paths = set(app.openapi()["paths"])
    client = TestClient(app)
    page = client.get("/biz-services")
    if not enabled:
        assert {BASE, f"{BASE}/export.csv", f"{BASE}/{{service_id}}"}.isdisjoint(paths)
        assert page.status_code == 404
        assert client.get(BASE).status_code == 404
        return
    assert {BASE, f"{BASE}/export.csv", f"{BASE}/{{service_id}}",
            "/api/v1/admin/biz-services/bulk-assign",
            "/api/v1/admin/biz-services/inventory/sms",
            "/api/v1/admin/biz-services/{service_id}/history"} <= paths
    assert page.status_code == 200
    assert "업무 서비스 정의" in page.text and "/static/js/biz-services.js" in page.text


def test_unauthenticated_401_and_admin_token_type_rejected() -> None:
    client, _, _ = _app()
    assert client.get(BASE).status_code == 401
    assert client.get(f"{BASE}/1").status_code == 401
    assert client.get(f"{BASE}/export.csv").status_code == 401
    assert client.get(BASE, headers=_hdr("u", kind="admin")).status_code == 401


def test_repo_missing_503_and_store_failure_hides_text() -> None:
    client, app, repo = _app()
    repo.fail = True
    res = client.get(BASE, headers=_hdr("a"))
    assert res.status_code == 503 and res.json()["detail"]["code"] == "biz_service_store_failed"
    assert SECRET_TEXT not in res.text
    app.state.biz_service_repo = None
    res = client.get(f"{BASE}/1", headers=_hdr("a"))
    assert res.status_code == 503 and res.json()["detail"]["code"] == "biz_service_unavailable"


def test_int_path_does_not_swallow_monitor() -> None:
    """`/{service_id:int}` — `/monitor`는 이 라우터에 안 걸린다(업무 현황 라우터 몫 · 422 아님)."""
    client, _, _ = _app()
    assert client.get(f"{BASE}/monitor", headers=_hdr("a")).status_code == 404


# ── 목록 ────────────────────────────────────────────────────────────────
def test_plain_user_sees_own_zone_only_with_counts() -> None:
    client, _, _ = _app()
    res = client.get(BASE, headers=_hdr("u"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"rows", "total", "limit", "offset", "hidden_service_count",
                         "analysis_run_id", "analysis_at", "is_admin"}
    assert [r["id"] for r in body["rows"]] == [1, 3] and body["total"] == 2
    assert body["hidden_service_count"] == 1 and body["is_admin"] is False
    loan = body["rows"][0]
    assert (loan["sms_count"], loan["hidden_member_count"]) == (1, 1)
    assert loan["sms_by_zone"] == {ZA: 1}
    assert loan["inferred_ratio"] == 0.5  # 추정 비율은 존과 무관한 서비스 성질
    text = json.dumps(body, ensure_ascii=False)
    assert "카드비밀" not in text and "loan-02" not in text and "정상" not in text


def test_admin_sees_all_and_review_status_filter() -> None:
    client, _, _ = _app()
    body = client.get(BASE, headers=_hdr("a")).json()
    assert [r["id"] for r in body["rows"]] == [1, 2, 3] and body["is_admin"] is True
    assert body["hidden_service_count"] == 0
    review = client.get(BASE, params={"status": "review"}, headers=_hdr("a")).json()
    assert [r["id"] for r in review["rows"]] == [2]
    assert review["rows"][0]["review_reasons"] == ["ambiguous"]
    zone = client.get(BASE, params={"zone": ZB}, headers=_hdr("a")).json()
    assert [r["id"] for r in zone["rows"]] == [1, 2]


def test_sort_paging_and_coverage_from_latest_apply_run() -> None:
    client, _, repo = _app()
    repo.runs = [
        {"run_id": "r-fail", "mode": "apply", "status": "failed", "summary": {}},
        {"run_id": "r1", "mode": "apply", "status": "done", "finished_at": "2026-11-05T00:00:00",
         "summary": {"services": {"loan": {"sms_servers": 4, "apm_linked_servers": 1},
                                  "fx": {"sms_servers": 2, "apm_linked_servers": 2}}}},
    ]
    body = client.get(BASE, params={"sort": "name", "order": "desc", "limit": 1, "offset": 1},
                      headers=_hdr("a")).json()
    assert body["total"] == 3 and len(body["rows"]) == 1 and body["analysis_run_id"] == "r1"
    low = client.get(BASE, params={"coverage_below": 50}, headers=_hdr("a")).json()
    assert [(r["id"], r["apm_coverage"]) for r in low["rows"]] == [(1, 0.25)]
    assert client.get(BASE, params={"limit": 101}, headers=_hdr("a")).status_code == 422


def test_free_text_masked_for_plain_user_not_admin() -> None:
    client, _, _ = _app()
    user = client.get(f"{BASE}/1", headers=_hdr("u")).json()
    admin = client.get(f"{BASE}/1", headers=_hdr("a")).json()
    assert user["service"]["description"] == "***"
    assert admin["service"]["description"] == "AKIAIOSFODNN7EXAMPLE"


# ── 상세 ────────────────────────────────────────────────────────────────
def test_detail_scoped_members_and_out_of_zone_404() -> None:
    client, app, repo = _app()
    repo.branches = {("pg", "101"): "여의도지점"}
    res = client.get(f"{BASE}/1", headers=_hdr("u"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert {"service", "members", "hidden_member_count", "hidden_by_kind", "metrics",
            "analysis_run_id", "link_graph", "is_admin"} <= set(body)
    assert [m["id"] for m in body["members"]] == [11]
    assert body["hidden_member_count"] == 1
    member = body["members"][0]
    assert member["owner_branch"] == "여의도지점"
    assert "evidence" not in member  # 비관리자는 근거 원문·관리 메모를 안 본다
    assert "note" not in body["service"]
    assert "loan-02" not in res.text
    assert body["link_graph"]["available"] is True and app.state.apm_host_map_repo.calls == 0
    gone = client.get(f"{BASE}/2", headers=_hdr("u"))
    assert gone.status_code == 404 and "카드비밀" not in gone.text
    assert client.get(f"{BASE}/99", headers=_hdr("a")).status_code == 404
    assert client.get(f"{BASE}/2", headers=_hdr("a")).status_code == 200


def test_no_zone_user_sees_nothing_by_name() -> None:
    client, _, _ = _app()
    body = client.get(BASE, headers=_hdr("n")).json()
    assert body["rows"] == [] and body["hidden_service_count"] == 3
    assert client.get(f"{BASE}/1", headers=_hdr("n")).status_code == 404


# ── CSV ─────────────────────────────────────────────────────────────────
def _csv_rows(text: str) -> list[list[str]]:
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


def test_csv_formula_injection_bom_header_and_zone_scope() -> None:
    client, _, _ = _app()
    res = client.get(f"{BASE}/export.csv", headers=_hdr("u"))
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    assert "biz-services.csv" in res.headers["content-disposition"]
    rows = _csv_rows(res.content.decode("utf-8"))
    assert rows[0] == ["service_key", "display_name", "tier", "owner_org", "db_id",
                       "server_name", "hostname", "layer"]
    body = rows[1:]
    assert ["fx", "'=SUM(1)", "", "'+cmd", "pg", "'@fx-01", "'@fx-01", "was"] in body
    text = res.content.decode("utf-8")
    assert "loan-02" not in text and "card" not in text  # 존 밖 구성원·서비스 없음
    assert [r[0] for r in body] == ["loan", "fx"]


def test_csv_follows_list_filters_for_admin() -> None:
    client, _, _ = _app()
    res = client.get(f"{BASE}/export.csv", params={"status": "review"}, headers=_hdr("a"))
    rows = _csv_rows(res.content.decode("utf-8"))[1:]
    assert [r[0] for r in rows] == ["card"]


def test_member_name_search_limited_to_own_zones() -> None:
    """존 밖 호스트 이름 검색으로 소속 서비스를 알아낼 수 없다(목록·CSV) · 전 존이면 종전대로."""
    client, _, _ = _app()
    for q in ("card-01", "loan-02"):  # 둘 다 존 B 구성원(loan-02는 보이는 서비스 1의 존 B 구성원)
        body = client.get(BASE, params={"q": q}, headers=_hdr("u")).json()
        assert body["rows"] == [], q
        res = client.get(f"{BASE}/export.csv", params={"q": q}, headers=_hdr("u"))
        assert _csv_rows(res.content.decode("utf-8"))[1:] == [], q
    own = client.get(BASE, params={"q": "loan-01"}, headers=_hdr("u")).json()
    assert [r["id"] for r in own["rows"]] == [1]
    admin = client.get(BASE, params={"q": "loan-02"}, headers=_hdr("a")).json()
    assert [r["id"] for r in admin["rows"]] == [1]
