"""plans/215 W5b — 관리자 CSV 가져오기 API(`/import/preview` · `/import/apply`).

인가·감사는 W4·W5 라우트 테스트와 같은 대역(실 `require_admin_user` · JWT · 감사 서비스
대역)을 쓴다. 저장소는 W5 가짜에 가져오기 메서드(키·id 조회 · 한 번에 적용 — 실 저장소와 같은
version·소속 판정)를 더한 것이다. 수용 21 · §14 벡터 3행 · 상한 · 409 · 감사 행 수 ·
`preview_id` 없음/만료를 본다.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.application.biz_service_import import MAX_BYTES, MAX_ROWS
from noise_gate.infrastructure.biz_service_repository import (
    BizServiceExistsError,
    BizServiceVersionConflict,
)
from tests.test_api.test_plan210_w6_api import ADMIN_BG, ADMIN_USER, PLAIN_USER
from tests.test_api.test_plan215_w4_admin_biz_service import _audits
from tests.test_api.test_plan215_w5_admin import _app as _w5_app
from tests.test_api.test_plan215_w5_admin import _Maps
from tests.test_api.test_plan215_w5_admin import _Repo as _W5Repo

BASE = "/api/v1/admin/biz-services"
HEADER = "service_key,display_name,tier,owner_org,db_id,server_name,hostname,layer"


class _Repo(_W5Repo):
    """W5 가짜 + 가져오기(실 저장소와 같은 판정 — 생성 경합 · version · 소속 확인 · 한 번에)."""

    async def services_brief(self, *, keys: Collection[str] = (),
                             ids: Collection[int] = ()) -> list[dict[str, Any]]:
        return [dict(s) for s in self.services.values()
                if s["service_key"] in set(keys) or s["id"] in set(ids)]

    async def import_members(self, *, creates: Sequence[Mapping[str, Any]],
                             versions: Mapping[int, int], items: Sequence[Mapping[str, Any]],
                             by: str = "") -> dict[str, Any]:
        for c in creates:
            hit = next((s for s in self.services.values()
                        if s["service_key"] == c["service_key"]), None)
            if hit is not None:
                raise BizServiceExistsError(hit["id"])
        for sid, ver in versions.items():
            self._v(sid, ver)
        for it in items:
            hit = next((m for m in self.members.values()
                        if (m["kind"], m["member_key"]) == (it["kind"], it["member_key"])), None)
            now = hit["service_id"] if hit else None
            if now != it["owner_id"]:
                sid = now or it["owner_id"]
                raise BizServiceVersionConflict(sid, self.services[sid]["version"])
        self.writes += 1
        created = []
        ids: dict[str, int] = {}
        for c in creates:
            row = self.seed(c["service_key"], name=c["display_name"], origin="manual",
                            tier=c.get("tier"), owner_org=c.get("owner_org"))
            created.append(dict(row))
            ids[c["service_key"]] = row["id"]
        for sid in versions:
            self._bump(sid)
        out = []
        for it in items:
            sid = it["service_id"] or ids[it["service_key"]]
            hit = next((m for m in self.members.values()
                        if (m["kind"], m["member_key"]) == (it["kind"], it["member_key"])), None)
            base = {"line": it["line"], "service_key": it["service_key"]}
            if hit is None:
                m = self.seed_member(sid, it["kind"], it["member_key"], origin="manual",
                                     basis="manual", layer=it["layer"], **dict(it["display"]))
                out.append({**base, "member": dict(m), "outcome": "added", "moved_from": None})
                continue
            old = hit["service_id"]
            hit.update(service_id=sid, origin="manual", confidence="confirmed", state="active")
            if it["layer"]:
                hit["layer"] = it["layer"]
            out.append({**base, "member": dict(hit), "outcome": "moved" if old != sid else "kept",
                        "moved_from": old if old != sid else None})
        return {"created": created, "members": out}


def _snap() -> list[dict[str, Any]]:
    rows = [("pg_a", "1", "ibkweb01", "ibkweb01"), ("pg_a", "2", "ibkwas01", "ibkwas01"),
            ("pg_a", "3", "dupsrv", "dupsrv"), ("pg_a", "4", "dupsrv", "dupsrv-b"),
            ("pg_a", "5", "cardweb01", "cardweb01")]
    return [{"db_id": d, "resource_id": r, "server_name": n, "hostname": h, "ip": f"10.0.0.{r}",
             "nic_ips": []} for d, r, n, h in rows]


def _app(maps: _Maps | None = None) -> tuple[TestClient, FastAPI, _Repo]:
    client, app, _ = _w5_app(maps=maps or _Maps(_snap()))
    repo = _Repo()
    app.state.biz_service_repo = repo
    return client, app, repo


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import src.routing.registry as reg

    monkeypatch.setattr(reg, "get_registry",
                        lambda: SimpleNamespace(db_ids=lambda: ["pg_a", "pg_b"]))


def _csv(*lines: str) -> bytes:
    return ("﻿" + "\r\n".join([HEADER, *lines]) + "\r\n").encode("utf-8")


def _preview(client: TestClient, data: bytes, headers: dict[str, str] = ADMIN_BG) -> Any:
    return client.post(f"{BASE}/import/preview", headers=headers,
                       files={"file": ("svc.csv", data, "text/csv")})


def _import_audits(app: FastAPI) -> list[dict[str, Any]]:
    return [a for a in _audits(app) if a.get("action") == "biz_service_import"]


# ── 인가 · 등록 ────────────────────────────────────────────────────────
def test_plain_user_403_and_unauthenticated_401() -> None:
    client, app, repo = _app()
    data = _csv("ibk,I,,,pg_a,ibkweb01,,")
    assert _preview(client, data, PLAIN_USER).status_code == 403
    res = client.post(f"{BASE}/import/apply", headers=PLAIN_USER, json={"preview_id": "abc"})
    assert res.status_code == 403
    assert repo.writes == 0 and _audits(app) == []
    assert client.post(f"{BASE}/import/preview",
                       files={"file": ("svc.csv", data, "text/csv")}).status_code == 401
    assert client.post(f"{BASE}/import/apply", json={"preview_id": "abc"}).status_code == 401


@pytest.mark.parametrize("enabled", [False, True])
def test_routes_registered_only_when_flag_on(enabled: bool,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # 앱 조립은 실 레지스트리를 쓴다(자동 대역 해제)
    from src.api.server import create_app
    from src.config import AppConfig, AuthConfig, NoiseGateConfig

    app = create_app(AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        auth=AuthConfig(enabled=False), noise_gate=NoiseGateConfig(biz_service_enabled=enabled),
    ))
    paths = {p for p in app.openapi()["paths"] if p.startswith(BASE)}
    wanted = {f"{BASE}/import/preview", f"{BASE}/import/apply"}
    assert (wanted <= paths) is enabled and (not enabled) is (not paths & wanted)


# ── 미리보기 → 적용(수용 21 · 벡터) ────────────────────────────────────
def test_preview_judgements_then_apply_with_per_row_audit_and_batch() -> None:
    client, app, repo = _app()
    old = repo.seed("old", name="옛 서비스")
    repo.seed_member(old["id"], "sms_server", "pg_a|2")
    res = _preview(client, _csv(
        "ibk,인터넷뱅킹,1,디지털부,pg_a,IBKWEB01,,web",   # 1대 → 추가(정규형) · 계층 web
        "ibk,인터넷뱅킹,1,디지털부,pg_a,ibkwas01,,was",   # 다른 서비스 소속 → 옮김
        "ibk,인터넷뱅킹,1,디지털부,pg_a,ghost01,,",       # 0대 → 스냅샷에 없음
        "ibk,인터넷뱅킹,1,디지털부,pg_a,dupsrv,,",        # 2대 → 고르지 않음
        "ibk,인터넷뱅킹,1,디지털부,nope,ibkweb01,,",      # 레지스트리 밖
        "ibk,인터넷뱅킹,1,디지털부,pg_a,cardweb01,,",     # 파일 안 충돌
        "card,카드,,,pg_a,CARDWEB01,,",                   # 파일 안 충돌
    ))
    assert res.status_code == 200, res.text
    body = res.json()
    assert {"preview_id", "expires_in", "snapshot_run_id", "rows", "counts", "services", "moves",
            "creates", "applicable", "labels", "audit_logged"} <= set(body)
    assert [r["label"] for r in body["rows"]] == [
        "추가", "옮김", "스냅샷에 없음", "고르지 않음", "등록되지 않은 DB", "파일 안 충돌",
        "파일 안 충돌"]
    assert body["applicable"] == 2 and body["creates"] == 1 and body["audit_logged"] is True
    assert body["moves"][0]["from_service_name"] == "옛 서비스"
    assert repo.writes == 0  # 미리보기는 쓰기 0
    before = repo.services[old["id"]]["version"]

    res = client.post(f"{BASE}/import/apply", headers=ADMIN_BG,
                      json={"preview_id": body["preview_id"]})
    assert res.status_code == 200, res.text
    out = res.json()
    assert set(out) == {"batch_id", "preview_id", "created", "results", "counts", "cache_ok",
                        "cache_reason", "audit_logged"}
    assert out["counts"] == {"added": 1, "moved": 1, "kept": 0}
    new_id = out["created"][0]["service_id"]
    members = {m["member_key"]: m for m in repo.members.values()}
    assert members["pg_a|1"]["service_id"] == new_id and members["pg_a|1"]["layer"] == "web"
    assert members["pg_a|1"]["origin"] == "manual"
    assert members["pg_a|1"]["confidence"] == "confirmed"
    assert members["pg_a|2"]["service_id"] == new_id
    assert "pg_a|5" not in members  # 파일 안 충돌은 적용 안 함
    assert repo.services[old["id"]]["version"] == before + 1
    audits = _import_audits(app)
    assert len(audits) == 3  # 새 서비스 1 + 구성원 2
    assert {a["batch_id"] for a in audits} == {out["batch_id"]}
    assert [a["assign"] for a in audits] == ["service_created", "added", "moved"]
    assert audits[2]["before"] == {"service_id": old["id"]}
    # 같은 미리보기를 두 번 적용하지 않는다
    again = client.post(f"{BASE}/import/apply", headers=ADMIN_BG,
                        json={"preview_id": body["preview_id"]})
    assert again.status_code == 404 and again.json()["detail"]["code"] == "preview_not_found"


def test_apply_409_when_target_service_version_changed() -> None:
    client, app, repo = _app()
    svc = repo.seed("ibk", name="인터넷뱅킹")
    pid = _preview(client, _csv("ibk,,,,pg_a,ibkweb01,,")).json()["preview_id"]
    repo._bump(svc["id"])  # 미리보기 뒤 다른 관리자가 고쳤다
    res = client.post(f"{BASE}/import/apply", headers=ADMIN_BG, json={"preview_id": pid})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "version_conflict"
    assert repo.writes == 0 and not repo.members
    denied = _import_audits(app)
    assert len(denied) == 1 and denied[0]["outcome"] == "denied"
    assert denied[0]["reason"] == "version_conflict"


def test_apply_409_when_member_joined_other_service_after_preview() -> None:
    client, _, repo = _app()
    repo.seed("ibk", name="인터넷뱅킹")
    other = repo.seed("other", name="다른")
    pid = _preview(client, _csv("ibk,,,,pg_a,ibkweb01,,")).json()["preview_id"]
    repo.seed_member(other["id"], "sms_server", "pg_a|1")
    res = client.post(f"{BASE}/import/apply", headers=ADMIN_BG, json={"preview_id": pid})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "version_conflict"


def test_apply_409_when_new_service_key_created_meanwhile() -> None:
    client, _, repo = _app()
    pid = _preview(client, _csv("ibk,인터넷뱅킹,,,pg_a,ibkweb01,,")).json()["preview_id"]
    repo.seed("ibk")
    res = client.post(f"{BASE}/import/apply", headers=ADMIN_BG, json={"preview_id": pid})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "exists"


def test_apply_409_when_snapshot_changed() -> None:
    maps = _Maps(_snap())
    client, _, repo = _app(maps)
    pid = _preview(client, _csv("ibk,인터넷뱅킹,,,pg_a,ibkweb01,,")).json()["preview_id"]
    maps.set_scan("scan2", ["pg_a"])
    res = client.post(f"{BASE}/import/apply", headers=ADMIN_BG, json={"preview_id": pid})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "snapshot_changed"
    assert repo.writes == 0


def test_preview_id_missing_expired_or_other_admin() -> None:
    client, app, _ = _app()
    res = client.post(f"{BASE}/import/apply", headers=ADMIN_BG, json={"preview_id": "nope"})
    assert res.status_code == 404 and res.json()["detail"]["code"] == "preview_not_found"
    assert client.post(f"{BASE}/import/apply", headers=ADMIN_BG,
                       json={"preview_id": "../x"}).status_code == 422
    pid = _preview(client, _csv("ibk,인터넷뱅킹,,,pg_a,ibkweb01,,")).json()["preview_id"]
    other = client.post(f"{BASE}/import/apply", headers=ADMIN_USER, json={"preview_id": pid})
    assert other.status_code == 404  # 만든 관리자만 적용
    store = app.state.biz_service_import_store
    store.ttl_seconds = -1  # 만료
    res = client.post(f"{BASE}/import/apply", headers=ADMIN_BG, json={"preview_id": pid})
    assert res.status_code == 404 and res.json()["detail"]["code"] == "preview_not_found"


# ── 상한 · 형식 ────────────────────────────────────────────────────────
def test_size_and_row_caps() -> None:
    client, app, _ = _app()
    big = _csv("a,A,,,pg_a,ibkweb01,,")
    big += b"\n" * (MAX_BYTES + 1 - len(big))
    res = _preview(client, big)
    assert res.status_code == 413 and res.json()["detail"]["code"] == "import_too_large"
    rows = _csv(*["a,A,,,pg_a,ghost,,"] * (MAX_ROWS + 1))
    res = _preview(client, rows)
    assert res.status_code == 400 and res.json()["detail"]["code"] == "import_too_many_rows"
    denied = [a for a in _audits(app) if a.get("action") == "biz_service_import_preview"]
    assert [a["outcome"] for a in denied] == ["denied", "denied"]
    ok = _preview(client, _csv(*["a,A,,,pg_a,ghost,,"] * MAX_ROWS))
    assert ok.status_code == 200 and len(ok.json()["rows"]) == MAX_ROWS


def test_bad_header_and_pii_owner_org() -> None:
    client, _, _ = _app()
    res = _preview(client, b"a,b\r\n1,2\r\n")
    assert res.status_code == 400 and res.json()["detail"]["code"] == "import_bad_header"
    res = _preview(client, _csv("ibk,인터넷뱅킹,,홍길동 010-1234-5678,pg_a,ibkweb01,,"))
    row = res.json()["rows"][0]
    assert row["status"] == "pii_rejected" and "010-1234" not in res.text


def test_scan_disabled_409() -> None:
    client, app, _ = _app()
    app.state.apm_host_map_repo = None
    res = _preview(client, _csv("ibk,인터넷뱅킹,,,pg_a,ibkweb01,,"))
    assert res.status_code == 409 and res.json()["detail"]["code"] == "scan_disabled"
