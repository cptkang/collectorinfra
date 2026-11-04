"""plans/211 W6(§9.3) — 인벤토리 APM 행의 현재 연계(`mapping`) 표시.

API는 plans/210 W6 대역(`_FakeRepo` · 실 `require_admin_user`)을, 화면은 같은 node 하네스
(`_admin`)를 재사용한다. 표시용이다 — 판정·저장 동작은 바뀌지 않는다(D-363 부기 4 ①).
"""

from __future__ import annotations

from typing import Any

from tests.test_api.test_plan210_w6_api import ADMIN_BG, BASE, PLAIN_USER, _app, _FakeRepo
from tests.test_api.test_ui_plan210_w6 import _API, _ROW, _admin

_POD = "ib-was-7d9f8c6b5-x2k4p"  # instance ib-was-7d9f8c6b5-x2k4p · workload ib-was
_WEB_POD = "ib-web-6c8d9f7b4-abcde"  # workload ib-web


def _inventory_repo() -> tuple[_FakeRepo, dict[str, dict[str, Any]]]:
    """instance+workload · workload만 · domain만 · 없음 · 다른 소스(키 같음) 다섯 인스턴스."""
    repo = _FakeRepo()
    apm = [
        {"source_id": "jen1", "domain_id": "1001", "instance_name": _POD},
        {"source_id": "jen1", "domain_id": "1001", "instance_name": _WEB_POD},
        {"source_id": "jen1", "domain_id": "2002", "instance_name": "solo1"},
        {"source_id": "jen1", "domain_id": "3003", "instance_name": "lone1"},
        {"source_id": "jen2", "domain_id": "1001", "instance_name": _POD},
    ]
    repo.snapshots = {
        "apm_inventory": ("run-0", apm),
        "sms_inventory": (
            "run-0",
            [{"db_id": "polestar_cm_gp", "server_name": "kbl-a", "hostname": "kbl-a"}],
        ),
        "itam_snapshot_server": ("run-0", [{"hostname": "kbl-a", "app_name": "인터넷뱅킹"}]),
    }
    seeded = {
        # 워크로드 행을 먼저 넣어도 instance가 나와야 한다(SCOPES 순서)
        "workload": repo.seed(
            scope="workload",
            match_key="ib-was",
            instance_example=_POD,
            targets=[{"db_id": "polestar_cm_gp", "server_name": "kbl-w"}],
        ),
        "instance": repo.seed(
            scope="instance",
            match_key=_POD,
            instance_example=_POD,
            state="ambiguous",
            targets=[
                {"db_id": "polestar_cm_gp", "server_name": "kbl-a"},
                {"db_id": "polestar_cm_gp", "server_name": "kbl-b"},
            ],
        ),
        "web": repo.seed(
            scope="workload",
            match_key="ib-web",
            instance_example=_WEB_POD,
            targets=[{"db_id": "polestar_cm_gp", "server_name": "kbl-c"}],
        ),
        "domain": repo.seed(
            scope="domain",
            domain_id="2002",
            match_key="",
            instance_example="",
            origin="manual",
            basis="manual",
            verified=True,
            targets=[{"db_id": "polestar_b0", "server_name": "kbl-d"}],
        ),
    }
    return repo, seeded


def _by_instance(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any] | None]:
    return {(r["source_id"], r["instance_name"]): r["mapping"] for r in rows}


def test_inventory_apm_mapping_instance_workload_domain_none() -> None:
    repo, seeded = _inventory_repo()
    client, _ = _app(repo=repo)
    res = client.get(f"{BASE}/inventory/apm", headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    got = _by_instance(res.json()["rows"])
    assert got[("jen1", _POD)]["id"] == seeded["instance"]["id"]  # instance가 workload보다 먼저
    assert got[("jen1", _WEB_POD)]["id"] == seeded["web"]["id"]
    assert got[("jen1", "solo1")]["id"] == seeded["domain"]["id"]
    assert got[("jen1", "lone1")] is None
    assert got[("jen2", _POD)] is None  # 키가 같아도 소스가 다르면 남의 매핑이 아니다


def test_inventory_apm_mapping_carries_editor_fields_and_review_marks() -> None:
    repo, seeded = _inventory_repo()
    client, _ = _app(repo=repo)
    rows = client.get(f"{BASE}/inventory/apm", headers=ADMIN_BG).json()["rows"]
    mapping = _by_instance(rows)[("jen1", _POD)]
    assert mapping is not None
    for field in (
        "id",
        "version",
        "source_id",
        "domain_id",
        "domain_name",
        "scope",
        "match_key",
        "instance_example",
        "state",
        "origin",
        "basis",
        "verified",
        "targets",
        "note",
        "review_reasons",
        "evidence_text",
    ):
        assert field in mapping, field
    assert mapping["state"] == "ambiguous" and "ambiguous" in mapping["review_reasons"]
    assert [t["server_name"] for t in mapping["targets"]] == ["kbl-a", "kbl-b"]
    assert mapping["evidence_text"]
    domain = _by_instance(rows)[("jen1", "solo1")]
    assert domain["verified"] is True and domain["origin"] == "manual"


def test_inventory_unmapped_rows_all_have_null_mapping_and_match_full_listing() -> None:
    repo, _ = _inventory_repo()
    client, _ = _app(repo=repo)
    full = client.get(f"{BASE}/inventory/apm", headers=ADMIN_BG).json()["rows"]
    only = client.get(
        f"{BASE}/inventory/apm", params={"unmapped": "true"}, headers=ADMIN_BG
    ).json()["rows"]
    assert only and all(r["mapping"] is None for r in only)
    expected = [(r["source_id"], r["instance_name"]) for r in full if r["mapping"] is None]
    assert [(r["source_id"], r["instance_name"]) for r in only] == expected


def test_inventory_reads_mapping_rows_once_per_request() -> None:
    repo, _ = _inventory_repo()
    calls: list[int] = []
    original = repo.all_rows

    async def _counted() -> list[dict[str, Any]]:
        calls.append(1)
        return await original()

    repo.all_rows = _counted  # type: ignore[method-assign]
    client, _ = _app(repo=repo)
    page = client.get(f"{BASE}/inventory/apm", params={"limit": 2}, headers=ADMIN_BG).json()
    assert len(page["rows"]) == 2 and page["has_more"] is True
    assert all("mapping" in r for r in page["rows"]) and len(calls) == 1
    client.get(f"{BASE}/inventory/apm", params={"unmapped": "true"}, headers=ADMIN_BG)
    assert len(calls) == 2


def test_inventory_sms_and_itam_rows_have_no_mapping_key() -> None:
    repo, _ = _inventory_repo()
    client, _ = _app(repo=repo)
    for kind in ("sms", "itam"):
        for unmapped in ("false", "true"):
            body = client.get(
                f"{BASE}/inventory/{kind}", params={"unmapped": unmapped}, headers=ADMIN_BG
            ).json()
            assert all("mapping" not in r for r in body["rows"]), (kind, unmapped)
    assert client.get(f"{BASE}/inventory/sms", headers=ADMIN_BG).json()["rows"]


def test_inventory_without_snapshot_is_empty() -> None:
    repo = _FakeRepo()
    repo.seed(match_key="was1")
    client, _ = _app(repo=repo)
    body = client.get(f"{BASE}/inventory/apm", headers=ADMIN_BG).json()
    assert body["run_id"] is None and body["rows"] == []


def test_inventory_apm_stays_admin_only() -> None:
    repo, _ = _inventory_repo()
    client, _ = _app(repo=repo)
    assert client.get(f"{BASE}/inventory/apm", headers=PLAIN_USER).status_code == 403
    assert client.get(f"{BASE}/inventory/apm").status_code in (401, 403)


# ─── 화면 — node 실행 ──────────────────────────────────────────────────

_INV_INSTANCE = {
    **_ROW,
    "id": 9,
    "version": 5,
    "match_key": "ib-was02",
    "instance_example": "ib-was02",
    "state": "ambiguous",
    "review_reasons": ["ambiguous"],
    "targets": [
        {"db_id": "polestar_cm_gp", "server_name": "kbl-a"},
        {"db_id": "polestar_cm_gp", "server_name": "kbl-b"},
        {"db_id": "polestar_b0", "server_name": "kbl-c"},
    ],
}
_INV_WORKLOAD = {
    **_ROW,
    "id": 11,
    "scope": "workload",
    "match_key": "ib-web",
    "instance_example": "ib-web-1",
    "basis": "node_address",
}


def _inv_routes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        f"GET {_API}": {
            "status": 200,
            "body": {"rows": [], "total": 0, "auto_enabled": True, "scan_available": False},
        },
        f"GET {_API}/scan/runs": {"status": 200, "body": {"runs": [], "scan_available": False}},
        f"GET {_API}/inventory/apm": {
            "status": 200,
            "body": {"run_id": "run-2", "has_more": False, "rows": rows},
        },
        f"PUT {_API}/9": {"status": 200, "body": {"map": _INV_INSTANCE}},
    }


def _inv_row(instance: str, mapping: dict[str, Any] | None) -> dict[str, Any]:
    return {"source_id": "jen1", "domain_id": "1001", "instance_name": instance, "mapping": mapping}


def test_admin_inventory_link_column_and_edit_opens_editor_with_candidates() -> None:
    routes = _inv_routes(
        [
            _inv_row("ib-was02", _INV_INSTANCE),
            _inv_row("ib-web-1", _INV_WORKLOAD),
            _inv_row("lone1", None),
        ]
    )
    out = _admin(
        routes,
        """
await wait();
var trs = ids.apmInvBody.children;
var cellText = function (tr, i) { return tr.children[i].textContent; };
var editBtns = find(trs[0], "button", "수정");
var addBtns = find(trs[0], "button", "이 인스턴스 연계 추가");
var link0 = cellText(trs[0], 4), link1 = cellText(trs[1], 4), link2 = cellText(trs[2], 4);
var addOnWorkload = find(trs[1], "button", "이 인스턴스 연계 추가").length;
var editOnWorkload = find(trs[1], "button", "수정").length;
var addOnNone = find(trs[2], "button", "이 인스턴스 연계 추가").length;
var editTitle = editBtns[0] && editBtns[0].title;
editBtns[0].click();
var title = ids.apmMapEditTitle.textContent, keyHidden = ids.apmMapEditKeyFields.hidden;
var targets = ids.apmMapEditTargets.textContent, editorHidden = ids.apmMapEditor.hidden;
ids.apmMapEditSaveBtn.click();
await wait();
out = { head: ids.apmInvHead.textContent, cols: ids.apmInvHead.children[0].children.length,
    edit: editBtns.length, add: addBtns.length, link0: link0, link1: link1, link2: link2,
    addOnWorkload: addOnWorkload, editOnWorkload: editOnWorkload, addOnNone: addOnNone,
    editTitle: editTitle, title: title, keyHidden: keyHidden, targets: targets,
    editorHidden: editorHidden,
    put: reqs.filter(function (r) { return r.method === "PUT"; })[0] };
""",
        hash_="#apmhostmap",
    )
    assert "연계" in out["head"] and out["cols"] == 6
    assert out["edit"] == 1 and out["add"] == 0
    assert "인스턴스" in out["link0"] and "모호" in out["link0"] and "VM 일치" in out["link0"]
    assert "polestar_cm_gp / kbl-a 외 2대" in out["link0"]
    assert "워크로드" in out["link1"] and "노드 주소" in out["link1"]
    assert "polestar_cm_gp / kbl-a" in out["link1"] and "외" not in out["link1"]
    assert out["link2"] == "없음"
    assert out["addOnWorkload"] == 1 and out["editOnWorkload"] == 0 and out["addOnNone"] == 1
    assert out["editTitle"].startswith("이 인스턴스의 연계 수정")
    assert out["title"].startswith("연계 수정 — ") and out["keyHidden"] is True
    assert out["editorHidden"] is False
    assert "kbl-a" in out["targets"] and "kbl-b" in out["targets"] and "kbl-c" in out["targets"]
    assert out["put"]["url"] == _API + "/9" and out["put"]["body"]["version"] == 5
    assert [t["server_name"] for t in out["put"]["body"]["targets"]] == ["kbl-a", "kbl-b", "kbl-c"]


def test_admin_inventory_add_on_workload_only_presets_instance() -> None:
    routes = _inv_routes([_inv_row("ib-web-1", _INV_WORKLOAD)])
    out = _admin(
        routes,
        """
await wait();
find(ids.apmInvBody, "button", "이 인스턴스 연계 추가")[0].click();
out = { title: ids.apmMapEditTitle.textContent, scope: ids.apmMapEditScope.value,
    instance: ids.apmMapEditInstance.value, keyHidden: ids.apmMapEditKeyFields.hidden };
""",
        hash_="#apmhostmap",
    )
    assert out == {
        "title": "수동 추가",
        "scope": "instance",
        "instance": "ib-web-1",
        "keyHidden": False,
    }


def test_admin_inventory_empty_row_spans_all_columns() -> None:
    out = _admin(
        _inv_routes([]),
        "await wait(); out = { span: ids.apmInvBody.children[0].children[0].colSpan };",
        hash_="#apmhostmap",
    )
    assert out == {"span": 6}


def test_admin_save_from_inventory_reloads_inventory() -> None:
    """저장 성공 뒤 인벤토리도 다시 읽는다 — 같은 인스턴스 재추가(409 exists) 방지(G1)."""
    routes = _inv_routes([_inv_row("lone1", None)])
    routes[f"POST {_API}"] = {"status": 200, "body": {"map": _INV_INSTANCE}}
    out = _admin(
        routes,
        """
await wait();
var invGets = function () { return reqs.filter(function (r) {
    return r.method === "GET" && r.url.indexOf("/inventory/apm") >= 0; }).length; };
var before = invGets();
find(ids.apmInvBody, "button", "이 인스턴스 연계 추가")[0].click();
ids.apmMapEditSaveBtn.click();
await wait();
out = { before: before, after: invGets(),
    post: reqs.filter(function (r) { return r.method === "POST"; }).length };
""",
        hash_="#apmhostmap",
    )
    assert out == {"before": 1, "after": 2, "post": 1}
