"""plans/210 후속 — 관리자 「APM·SMS 연계」 확인 취소(`POST /{id}/unverify` · D-362 부기 4).

W6 API 테스트의 대역(`_FakeRepo` + 실 Redis 사본 · 실 `require_admin_user` JWT)과 관리자 탭
node 하네스(`_admin`)를 재사용한다. 확인 취소 뒤 행이 기존 자동 규칙(쓰기 판정·정리)으로
돌아가는지는 도메인 순수 함수로 고정한다.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI

from noise_gate.domain import apm_host_map as m
from noise_gate.infrastructure.apm_host_map_cache import map_key
from tests.test_api.test_plan210_w6_api import (
    ADMIN_BG,
    ADMIN_USER,
    BASE,
    PLAIN_USER,
    _app,
    _FakeRepo,
)
from tests.test_api.test_ui_plan210_w6 import _API, _ROW, _admin

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
#: 미확인 자동 행의 근거 문장(「자동 · 관리자 확인(…)」 → 「자동 · 추정(…)」)
_GUESS_TEXT = m.evidence_text(m.ORIGIN_AUTO, m.BASIS_VM_EXACT, verified=False)
_TARGET = {"db_id": "polestar_cm_gp", "server_name": "kbl-a", "ip": "10.0.0.7"}
_EVIDENCE = {
    "stage": "vm_exact",
    "previous_targets": [{"db_id": "polestar_cm_gp", "server_name": "kbl-old"}],
    "scan_suggests": {"basis": "itam", "state": "active", "targets": [], "at": "2026-11-02"},
}


class _Repo(_FakeRepo):
    """W6 대역 + 확인 취소 — 실 저장소와 같이 verified·updated_by·version만 바꾼다."""

    async def unverify(self, map_id, *, version, by=""):  # noqa: ANN001, ANN201
        row = self._check(map_id, version)
        if row is None:
            return None
        row.update(verified=False, updated_by=by, version=row["version"] + 1)
        return copy.deepcopy(row)


def _verified(repo: _Repo, **row: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "verified": True,
        "targets": [_TARGET],
        "evidence": copy.deepcopy(_EVIDENCE),
    }
    base.update(row)
    return repo.seed(**base)


def _cached(app: FastAPI, row: dict[str, Any]) -> dict[str, Any] | None:
    raw = asyncio.run(
        app.state.apm_host_map_cache.redis.hget(
            map_key(row["source_id"]),
            m.cache_field(row["scope"], row["domain_id"], row["match_key"]),
        )
    )
    return json.loads(raw) if raw else None


def _post(client: Any, map_id: int, version: int, headers: dict[str, str] = ADMIN_BG) -> Any:
    return client.post(f"{BASE}/{map_id}/unverify", json={"version": version}, headers=headers)


# ─── API ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("headers", [ADMIN_BG, ADMIN_USER])
def test_unverify_ok_clears_only_verified_and_updates_cache_and_audit(
    headers: dict[str, str],
) -> None:
    repo = _Repo()
    row = _verified(repo)
    client, app = _app(repo=repo)
    asyncio.run(app.state.apm_host_map_cache.put(row))
    first = _cached(app, row)
    assert first is not None and first["verified"] is True
    assert "자동 · 관리자 확인(" in first["evidence_text"]

    res = _post(client, row["id"], 1, headers)

    assert res.status_code == 200, res.text
    body = res.json()
    got = body["map"]
    assert got["verified"] is False and got["version"] == 2
    # 대상·상태·출처·근거·evidence(scan_suggests·previous_targets 포함)는 그대로
    for key in ("targets", "state", "origin", "basis", "evidence", "note", "match_key"):
        assert got[key] == row[key], key
    assert got["evidence_text"] == _GUESS_TEXT and "자동 · 추정(" in _GUESS_TEXT
    assert "scan_suggests" in got["review_reasons"]
    # 사본 갱신 — 진단이 읽는 값도 미확인 자동
    cached = _cached(app, row)
    assert cached is not None
    assert (cached["verified"], cached["version"]) == (False, 2)
    assert cached["evidence_text"] == _GUESS_TEXT
    # 감사 before/after
    assert body["audit_logged"] is True
    audit = app.state.audit_service.actions()[-1]
    assert (audit["action"], audit["outcome"]) == ("apm_host_map_unverify", "ok")
    assert audit["before"]["verified"] is True and audit["after"]["verified"] is False
    assert audit["after"]["version"] == 2
    assert audit["after"]["targets"] == audit["before"]["targets"]


@pytest.mark.parametrize(
    "row",
    [
        {"verified": False},  # 미확인 자동 행
        {"origin": "manual", "basis": "manual", "verified": False},  # 관리자 등록
        {"origin": "manual", "basis": "manual", "verified": True},  # 확인 뒤 수정된 관리자 행
    ],
    ids=["auto_unverified", "manual", "manual_verified"],
)
def test_unverify_rejects_rows_other_than_verified_auto(row: dict[str, Any]) -> None:
    repo = _Repo()
    seeded = _verified(repo, **row)
    client, app = _app(repo=repo)

    res = _post(client, seeded["id"], 1)

    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "unverify_verified_auto_only"
    assert repo.rows[seeded["id"]]["version"] == 1
    assert repo.rows[seeded["id"]]["verified"] is row["verified"]
    assert _cached(app, seeded) is None
    audit = app.state.audit_service.actions()[-1]
    assert audit["action"] == "apm_host_map_unverify"
    assert (audit["outcome"], audit["reason"]) == ("denied", "unverify_verified_auto_only")
    assert audit["before"]["id"] == seeded["id"]


def test_unverify_version_conflict_409() -> None:
    repo = _Repo()
    seeded = _verified(repo, version=3)
    client, app = _app(repo=repo)

    res = _post(client, seeded["id"], 2)

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert (detail["code"], detail["current_version"]) == ("version_conflict", 3)
    assert repo.rows[seeded["id"]]["verified"] is True
    assert repo.rows[seeded["id"]]["version"] == 3
    audit = app.state.audit_service.actions()[-1]
    assert (audit["outcome"], audit["reason"]) == ("denied", "version_conflict")


def test_unverify_missing_row_404() -> None:
    client, _ = _app(repo=_Repo())
    res = _post(client, 999, 1)
    assert res.status_code == 404 and res.json()["detail"]["code"] == "not_found"


def test_unverify_requires_admin_role() -> None:
    repo = _Repo()
    seeded = _verified(repo)
    client, app = _app(repo=repo)
    assert _post(client, seeded["id"], 1, PLAIN_USER).status_code == 403
    assert client.post(f"{BASE}/{seeded['id']}/unverify", json={"version": 1}).status_code == 401
    assert repo.rows[seeded["id"]]["verified"] is True
    assert app.state.audit_service.entries == []


def test_unverify_store_failure_is_503_and_audited() -> None:
    repo = _Repo()
    seeded = _verified(repo)

    async def _boom(map_id, **_: Any):  # noqa: ANN001, ANN202
        raise ConnectionError("pg down")

    repo.unverify = _boom  # type: ignore[method-assign]
    client, app = _app(repo=repo)

    res = _post(client, seeded["id"], 1)

    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "apm_host_map_store_failed"
    audit = app.state.audit_service.actions()[-1]
    assert (audit["outcome"], audit["reason"]) == ("failed", "ConnectionError")


# ─── 취소 뒤 자동 규칙 복귀 ─────────────────────────────────────────────


def _proposal(row: dict[str, Any], server_name: str) -> m.AutoProposal:
    return m.AutoProposal(
        domain_id=row["domain_id"],
        scope=row["scope"],
        match_key=row["match_key"],
        basis=m.BASIS_VM_EXACT,
        state=m.STATE_ACTIVE,
        targets=tuple(m.normalize_targets([{**_TARGET, "server_name": server_name}])),
        evidence={"stage": "vm_exact"},
    )


def test_after_unverify_scan_overwrites_and_cleanup_applies() -> None:
    old = (NOW - timedelta(days=m.AUTO_UNSEEN_DAYS + 1)).isoformat()
    repo = _Repo()
    seeded = _verified(repo, created_at=old, last_seen_at=old, last_used_at=old)
    client, _ = _app(repo=repo)
    before = copy.deepcopy(repo.rows[seeded["id"]])
    now_iso = NOW.isoformat()

    # 확인한 행 — 관리자 행이라 자동이 덮지 않고(제안만) 정리하지 않는다
    assert m.is_admin_record(before)
    assert m.plan_auto_write(before, _proposal(before, "kbl-b"), now_iso=now_iso).action == (
        m.WRITE_SUGGEST
    )
    assert m.plan_auto_write(before, _proposal(before, "kbl-a"), now_iso=now_iso).action == (
        m.WRITE_SKIP
    )
    assert m.purge_reason(before, NOW) is None

    res = _post(client, seeded["id"], 1)
    assert res.status_code == 200, res.text
    after = res.json()["map"]

    # 취소한 행 — 미확인 자동 행: 결과가 다르면 갱신(+previous_targets·변경됨), 같으면 same
    assert not m.is_admin_record(after)
    write = m.plan_auto_write(after, _proposal(after, "kbl-b"), now_iso=now_iso)
    assert write.action == m.WRITE_UPDATE
    assert [t["server_name"] for t in write.targets] == ["kbl-b"]
    assert [t["server_name"] for t in write.evidence["previous_targets"]] == ["kbl-a"]
    assert write.evidence["changed"] is True
    same = m.plan_auto_write(after, _proposal(after, "kbl-a"), now_iso=now_iso)
    assert same.action == m.WRITE_SAME
    # 정리 대상 복귀 — instance 30일 미사용이 먼저 걸리고, 그 밖 scope는 180일 미관측
    assert m.purge_reason(after, NOW) == m.PURGE_INSTANCE_IDLE
    assert m.purge_reason({**after, "scope": m.SCOPE_WORKLOAD}, NOW) == m.PURGE_UNSEEN
    recent = NOW.isoformat()
    fresh = {**after, "last_seen_at": recent, "last_used_at": recent}
    assert m.purge_reason(fresh, NOW) is None


# ─── 화면 ──────────────────────────────────────────────────────────────

_ROWS = [
    {**_ROW, "id": 7, "verified": True, "review_reasons": []},
    {**_ROW, "id": 8, "verified": False},
    {**_ROW, "id": 9, "origin": "manual", "basis": "manual", "verified": True},
    {**_ROW, "id": 10, "origin": "manual", "basis": "manual", "verified": False},
]
_LIST = {
    "status": 200,
    "body": {"rows": _ROWS, "total": 4, "auto_enabled": True, "scan_available": False},
}
_BUTTONS = """
var rows = ids.apmMapBody.children.map(function (tr) {
  return find(tr, "button").map(function (b) { return b.textContent; }); });
"""


def test_ui_unverify_button_only_on_verified_auto_rows() -> None:
    out = _admin({f"GET {_API}": _LIST}, _BUTTONS + "out = { rows: rows };")
    rows = out["rows"]
    assert [("확인 취소" in r) for r in rows] == [True, False, False, False]
    assert [("확인" in r) for r in rows] == [False, True, False, False]
    assert rows[0] == ["수정", "확인 취소", "다시 찾기", "삭제"]


def test_ui_unverify_confirms_posts_version_and_reloads() -> None:
    routes = {
        f"GET {_API}": _LIST,
        f"POST {_API}/7/unverify": {"status": 200, "body": {"map": _ROWS[0]}},
    }
    out = _admin(
        routes,
        """
var asked = [];
window.confirm = function (msg) { asked.push(msg); return true; };
find(ids.apmMapBody, "button", "확인 취소")[0].click();
await wait();
out = { asked: asked, post: reqs.filter(function (r) { return r.method === "POST"; }),
    gets: reqs.filter(function (r) { return r.method === "GET"; }).length,
    notice: ids.apmMapNotice.textContent };
""",
    )
    assert len(out["asked"]) == 1 and "확인을 취소" in out["asked"][0]
    assert [(p["url"], p["body"]) for p in out["post"]] == [(_API + "/7/unverify", {"version": 3})]
    assert out["post"][0]["auth"] == "Bearer utok"
    assert out["gets"] == 2 and "확인을 취소했습니다" in out["notice"]


def test_ui_unverify_cancelled_confirm_sends_nothing() -> None:
    out = _admin(
        {f"GET {_API}": _LIST},
        """
window.confirm = function () { return false; };
find(ids.apmMapBody, "button", "확인 취소")[0].click();
await wait();
out = { posts: reqs.filter(function (r) { return r.method === "POST"; }).length };
""",
    )
    assert out == {"posts": 0}


def test_ui_unverify_failure_shows_error_reason() -> None:
    reason = "확인 취소는 관리자 확인한 자동 행에만 씁니다"
    routes = {
        f"GET {_API}": _LIST,
        f"POST {_API}/7/unverify": {
            "status": 400,
            "body": {"detail": {"code": "unverify_verified_auto_only", "reason": reason}},
        },
    }
    out = _admin(
        routes,
        """
var btn = find(ids.apmMapBody, "button", "확인 취소")[0];
btn.click();
await wait();
out = { notice: ids.apmMapNotice.textContent, disabled: btn.disabled,
    gets: reqs.filter(function (r) { return r.method === "GET"; }).length };
""",
    )
    assert out == {"notice": reason, "disabled": False, "gets": 1}
