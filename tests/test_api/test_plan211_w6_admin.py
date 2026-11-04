"""plans/211 W6 축 B — 관리자 「다시 찾기」를 노드 주소 우선 순서로(§9.3 B · §9.4 수용 5).

API는 plans/210 W6 테스트의 앱·인가·저장소 대역을 재사용하고, 저장소에 연계 요청 저장이 쓰는
메서드(`get_by_key`·`apply_auto_many`·`set_domain_superseded`)만 더한다. 게이트웨이는
`build_apm_gateway_client` 자리에 가짜 클라이언트(`_call`)를 끼워 실 `GatewayToolCaller` 경로를
탄다.
화면은 plans/210 W6 node 하네스로 실제 실행한다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from noise_gate.application import host_candidates as hc
from noise_gate.domain.apm_host_map import VmMatch, normalize_targets
from noise_gate.infrastructure import apm_gateway_client as gwc
from noise_gate.infrastructure.apm_gateway_client import ApmGatewayClientError
from src.api.routes import admin_apm_host_map as routes
from tests.test_api.test_plan210_w6_api import ADMIN_BG, BASE, _app, _FakeRepo
from tests.test_api.test_ui_plan210_w6 import _API, _ROW, _RUN, _admin

POD_A = "pay-api-7d9f8c6b5-x2k4q"
POD_B = "pay-api-7d9f8c6b5-b7n9r"
NODE_A, NODE_B = "10.9.0.5", "10.9.0.6"
SMS_ROWS = [
    {
        "db_id": "polestar_cm_gp",
        "resource_id": "2",
        "server_name": "node01",
        "hostname": "node01",
        "ip": "10.1.0.5",
        "nic_ips": [NODE_A],
    },
    {
        "db_id": "polestar_cm_gp",
        "resource_id": "3",
        "server_name": "node02",
        "hostname": "node02",
        "ip": "10.1.0.6",
        "nic_ips": [NODE_B],
    },
]


def _inst(instance_id: str, name: str, *, ip: str = "", host: str = "") -> dict[str, Any]:
    """`apm_instance_map` 행."""
    return {
        "source_id": "jen1",
        "domain_id": 1001,
        "domain_name": "pay-k8s",
        "domain_description": "",
        "instance_id": instance_id,
        "instance_name": name,
        "ip_address": ip,
        "host_name": host,
    }


def _node(instance_id: str, addr: str, status: str = "ok") -> dict[str, Any]:
    """`apm_node_addresses` 행."""
    return {
        "source_id": "jen1",
        "domain_id": 1001,
        "instance_id": instance_id,
        "host_address": addr,
        "status": status,
    }


def _snap(instance_id: str, name: str, *, addr: str = "", status: str = "") -> dict[str, Any]:
    """`apm_inventory` 스냅샷 행."""
    return {
        "source_id": "jen1",
        "domain_id": "1001",
        "domain_name": "pay-k8s",
        "domain_description": "",
        "instance_id": instance_id,
        "instance_name": name,
        "ip": "",
        "host_name": "",
        "workload_key": "pay-api",
        "host_address": addr,
        "host_address_status": status,
    }


class _Repo(_FakeRepo):
    """plans/210 W6 대역 + 연계 요청 저장(`apply_domain_link`)이 쓰는 메서드."""

    def __init__(self, events: list[str] | None = None) -> None:
        super().__init__()
        self.events = events if events is not None else []
        self.many: list[Any] = []
        self.superseded: list[tuple[str, str, Any]] = []

    async def current_snapshot_run(self, table):  # noqa: ANN001, ANN201
        if table.startswith("itam_"):
            self.events.append("I")
        return await super().current_snapshot_run(table)

    async def get_by_key(self, source_id, domain_id, scope, match_key):  # noqa: ANN001, ANN201
        for r in self.rows.values():
            if (r["source_id"], r["domain_id"], r["scope"], r["match_key"]) == (
                source_id,
                domain_id,
                scope,
                match_key,
            ):
                return copy.deepcopy(r)
        return None

    async def apply_auto(self, source_id, proposal, *, now=None, retry_hours=24):  # noqa: ANN001, ANN201
        return await super().apply_auto(source_id, proposal, retry_hours=retry_hours)

    async def apply_auto_many(self, source_id, proposals, *, now=None, retry_hours=24):  # noqa: ANN001, ANN201
        out = []
        for p in proposals:
            self.many.append(p)
            row = self.seed(
                source_id=source_id,
                domain_id=p.domain_id,
                scope=p.scope,
                match_key=p.match_key,
                instance_example=p.instance_example,
                basis=p.basis,
                state=p.state,
                targets=list(p.targets),
                evidence=dict(p.evidence),
            )
            out.append(("insert", row))
        return out

    async def set_domain_superseded(self, source_id, domain_id, marker):  # noqa: ANN001, ANN201
        self.superseded.append((source_id, domain_id, marker))
        for r in self.rows.values():
            if (r["source_id"], r["domain_id"], r["scope"]) == (source_id, domain_id, "domain"):
                r["evidence"] = {**r["evidence"], "superseded": marker}
                r["targets"] = normalize_targets(r["targets"])
                return copy.deepcopy(r)
        return None


class _Cache:
    """사본 대역 — put 기록(`redis` 없음 → 호출 상한은 프로세스 내)."""

    redis = None

    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    async def put(self, row: Any) -> None:
        self.puts.append(dict(row))

    async def remove(self, row: Any) -> None:
        return None


class _Gateway:
    """`ApmGatewayClient` 자리 — 실 `GatewayToolCaller`가 `_call`을 부른다(없는 도구 Unknown)."""

    def __init__(self, tools: dict[str, Any]) -> None:
        self.tools = tools
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, dict(args)))
        if tool not in self.tools:
            raise ApmGatewayClientError(f"게이트웨이 도구 오류({tool}): Unknown tool: {tool}")
        return self.tools[tool](dict(args))


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """게이트웨이 · 직접 조회(N3·V) 대역 — `gw`를 넣으면 그 가짜로 호출한다."""
    state: dict[str, Any] = {"gw": None, "node": [], "vm": [], "events": []}
    monkeypatch.setattr(gwc, "build_apm_gateway_client", lambda config: state["gw"])

    async def _node_link(config, *, host_address, db_ids=None, probe=None):  # noqa: ANN001, ANN202
        state["node"].append(host_address)
        return {"status": "none"}

    async def _vm_link(config, *, ip, host_name, db_ids=None, probe=None, max_targets=10):  # noqa: ANN001, ANN202
        state["vm"].append((ip, host_name))
        state["events"].append("V")
        return VmMatch("none"), []

    monkeypatch.setattr(hc, "node_address_link", _node_link)
    monkeypatch.setattr(hc, "vm_exact_link", _vm_link)
    return state


def _client(repo: _Repo) -> tuple[Any, Any, _Cache]:
    client, app = _app(repo=repo)
    cache = _Cache()
    app.state.apm_host_map_cache = cache
    return client, app, cache


def _pods_gateway(nodes: bool = True) -> _Gateway:
    tools: dict[str, Any] = {
        "apm_instance_map": lambda a: {"rows": [_inst("101", POD_A), _inst("102", POD_B)]},
    }
    if nodes:
        tools["apm_node_addresses"] = lambda a: {
            "rows": [_node("101", NODE_A), _node("102", NODE_B)],
            "node_address_sources": [{"source_id": "jen1", "status": "ok"}],
        }
    return _Gateway(tools)


# ─── 수용 5 — domain 행 미리보기 · 적용 ─────────────────────────────────


def test_domain_preview_proposes_node_address_and_saves_nothing(live: dict[str, Any]) -> None:
    repo = _Repo()
    repo.snapshots = {"sms_inventory": ("run-0", SMS_ROWS)}
    domain = repo.seed(scope="domain", match_key="", basis="itam", state="failed")
    live["gw"] = _pods_gateway()
    client, app, cache = _client(repo)
    res = client.post(f"{BASE}/{domain['id']}/preview", headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    body = res.json()
    # 기존 키 그대로 + 가산 키
    for key in ("map", "proposal", "reason", "action", "preview_only", "applied"):
        assert key in body
    assert body["applied"] is False and body["reason"] is None
    assert body["proposal"]["basis"] == "node_address" and body["proposal"]["state"] == "active"
    scopes = sorted((p["scope"], p["match_key"], p["basis"]) for p in body["proposals"])
    assert scopes == [
        ("instance", POD_B, "node_address"),
        ("instance", POD_A, "node_address"),
        ("workload", "pay-api", "node_address"),
    ]
    assert all(p["action"] == "insert" for p in body["proposals"])
    assert body["tried"]["instances"] == "gateway"
    assert body["tried"]["node_address"].startswith("ok 2/2")
    assert body["tried"]["itam"] == "skipped:linked"
    assert [c[0] for c in live["gw"].calls] == ["apm_instance_map", "apm_node_addresses"]
    # 저장 0 · 사본 0 · 감사 0 · ITAM 미조회
    assert repo.applied == [] and repo.many == [] and repo.superseded == []
    assert cache.puts == [] and app.state.audit_service.entries == []
    assert "I" not in repo.events


def test_domain_apply_writes_all_caches_supersedes_and_audits(live: dict[str, Any]) -> None:
    repo = _Repo()
    repo.snapshots = {"sms_inventory": ("run-0", SMS_ROWS)}
    domain = repo.seed(scope="domain", match_key="", basis="itam", state="failed")
    live["gw"] = _pods_gateway()
    client, app, cache = _client(repo)
    res = client.post(
        f"{BASE}/{domain['id']}/resolve", json={"apply": True, "version": 1}, headers=ADMIN_BG
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied"] is True and body["action"] == "insert"
    assert len(repo.many) == 3 and {p.basis for p in repo.many} == {"node_address"}
    # 실패 domain 행은 남고 「대체됨」 표지 → 검토 필요에서 빠진다
    assert repo.superseded and repo.superseded[0][:2] == ("jen1", "1001")
    assert body["map"]["id"] == domain["id"] and body["map"]["review_reasons"] == []
    assert {p["match_key"] for p in cache.puts} >= {POD_A, POD_B, "pay-api"}
    audit = app.state.audit_service.actions()[-1]
    assert audit["action"] == "apm_host_map_resolve" and audit["outcome"] == "ok"
    assert audit["before"]["state"] == "failed" and audit["after"]["id"] == domain["id"]
    assert sorted((r["scope"], r["action"]) for r in audit["rows"]) == [
        ("instance", "insert"),
        ("instance", "insert"),
        ("workload", "insert"),
    ]
    assert all(r["map_id"] > 0 for r in audit["rows"])
    assert body["audit_logged"] is True


def test_domain_node_tool_unregistered_goes_vm_then_itam(live: dict[str, Any]) -> None:
    repo = _Repo(events=live["events"])
    repo.snapshots = {
        "sms_inventory": ("run-0", SMS_ROWS),
        "itam_snapshot_app": ("run-0", [{"app_code": "ORD", "app_name": "order"}]),
        "itam_snapshot_server": (
            "run-0",
            [{"hostname": "ordweb01", "ips": ["10.2.0.1"], "active": None, "clue_text": "order"}],
        ),
    }
    domain = repo.seed(
        scope="domain", match_key="", basis="itam", state="failed", domain_name="order"
    )
    live["gw"] = _Gateway(
        {
            "apm_instance_map": lambda a: {
                "rows": [
                    _inst("201", "ordweb", ip="10.7.7.7", host="ordweb-x"),
                    _inst("202", POD_A),
                ]
            },
        }
    )
    client, _, _ = _client(repo)
    body = client.post(f"{BASE}/{domain['id']}/preview", headers=ADMIN_BG).json()
    assert body["tried"]["node_address"] == "node_address_disabled"
    assert "live 0/1" in body["tried"]["vm_exact"]
    assert not body["tried"]["itam"].startswith("skipped")
    assert live["vm"] == [("10.7.7.7", "ordweb-x")]
    assert live["events"].index("V") < live["events"].index("I")
    assert body["proposal"]["scope"] == "domain" and body["proposal"]["basis"] == "itam"
    assert [p["basis"] for p in body["proposals"]] == ["itam"]
    assert repo.applied == [] and repo.many == []


def test_domain_without_gateway_uses_snapshot_and_says_so(live: dict[str, Any]) -> None:
    repo = _Repo()
    repo.snapshots = {
        "sms_inventory": ("run-0", SMS_ROWS),
        "apm_inventory": ("run-0", [_snap("101", POD_A, addr=NODE_A, status="ok")]),
    }
    domain = repo.seed(scope="domain", match_key="", basis="itam", state="failed")
    client, _, _ = _client(repo)
    body = client.post(f"{BASE}/{domain['id']}/preview", headers=ADMIN_BG).json()
    assert body["tried"]["instances"] == "snapshot:gateway_not_configured"
    assert body["proposal"]["basis"] == "node_address"
    assert body["proposal"]["targets"][0]["server_name"] == "node01"


def test_domain_without_gateway_and_snapshot_is_no_inventory(live: dict[str, Any]) -> None:
    repo = _Repo()
    domain = repo.seed(scope="domain", match_key="", basis="itam", state="failed")
    client, _, _ = _client(repo)
    body = client.post(f"{BASE}/{domain['id']}/preview", headers=ADMIN_BG).json()
    assert body["proposal"] is None and body["reason"] == "apm_map_no_inventory"
    assert body["proposals"] == [] and body["tried"]["instances"].startswith("snapshot:")


def test_domain_gateway_error_has_kind_only_no_exception_text(live: dict[str, Any]) -> None:
    class _Boom(_Gateway):
        async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("secret-host:9999 refused")

    repo = _Repo()
    repo.snapshots = {"apm_inventory": ("run-0", [_snap("101", POD_A)])}
    domain = repo.seed(scope="domain", match_key="", basis="itam", state="failed")
    live["gw"] = _Boom({})
    client, _, _ = _client(repo)
    res = client.post(f"{BASE}/{domain['id']}/preview", headers=ADMIN_BG)
    assert res.status_code == 200 and "secret-host" not in res.text
    assert res.json()["tried"]["instances"] == "snapshot:gateway_unreachable"


# ─── instance 행 — 스냅샷 노드 주소가 ok가 아니면 그 인스턴스만 N1 ────────


def test_instance_row_asks_node_address_for_that_instance_only(live: dict[str, Any]) -> None:
    repo = _Repo()
    repo.snapshots = {
        "sms_inventory": ("run-0", SMS_ROWS),
        "apm_inventory": (
            "run-0",
            [
                _snap("101", POD_A, status="not_found"),
                _snap("102", POD_B, status=""),
            ],
        ),
    }
    row = repo.seed(scope="instance", match_key=POD_A, basis="vm_exact", state="failed")
    live["gw"] = _Gateway(
        {
            "apm_node_addresses": lambda a: {
                "rows": [_node("101", NODE_A)],
                "node_address_sources": [{"source_id": "jen1", "status": "ok"}],
            },
        }
    )
    client, app, cache = _client(repo)
    body = client.post(f"{BASE}/{row['id']}/preview", headers=ADMIN_BG).json()
    calls = live["gw"].calls
    assert [c[0] for c in calls] == ["apm_node_addresses"]
    assert calls[0][1]["instance_ids"] == [101] and calls[0][1]["domain_ids"] == [1001]
    assert body["proposal"]["basis"] == "node_address"
    assert body["proposal"]["targets"][0]["server_name"] == "node01"
    assert [(p["scope"], p["match_key"]) for p in body["proposals"]] == [("instance", POD_A)]
    assert body["action"] == "update" and "itam" not in body["tried"]
    assert repo.applied == []
    # 적용 — 그 행만 쓰고 사본 · 감사(쓴 행 목록)
    res = client.post(
        f"{BASE}/{row['id']}/resolve", json={"apply": True, "version": 1}, headers=ADMIN_BG
    )
    assert res.status_code == 200, res.text
    assert res.json()["map"]["basis"] == "node_address" and len(repo.applied) == 1
    assert cache.puts and cache.puts[0]["match_key"] == POD_A
    audit = app.state.audit_service.actions()[-1]
    assert audit["rows"] == [
        {
            "scope": "instance",
            "match_key": POD_A,
            "basis": "node_address",
            "state": "active",
            "action": "update",
            "map_id": row["id"],
        }
    ]


def test_instance_row_with_ok_snapshot_does_not_ask_gateway(live: dict[str, Any]) -> None:
    repo = _Repo()
    repo.snapshots = {
        "sms_inventory": ("run-0", SMS_ROWS),
        "apm_inventory": ("run-0", [_snap("101", POD_A, addr=NODE_A, status="ok")]),
    }
    row = repo.seed(scope="instance", match_key=POD_A, basis="vm_exact", state="failed")
    live["gw"] = _pods_gateway()
    client, _, _ = _client(repo)
    body = client.post(f"{BASE}/{row['id']}/preview", headers=ADMIN_BG).json()
    assert live["gw"].calls == [] and body["proposal"]["basis"] == "node_address"


# ─── 게이트웨이 읽기 상한 · 자동 생성 off ───────────────────────────────


def test_resolve_reads_use_narrow_limits(
    live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """다시 찾기의 게이트웨이 읽기 2회(인스턴스·노드 주소)는 좁힌 상한(`_RESOLVE_READ`)으로."""
    from noise_gate.application import apm_host_map_scan as scan_mod

    seen: list[tuple[str, dict[str, Any]]] = []
    real = scan_mod.read_all_rows

    async def _spy(caller, tool, arguments, **kwargs):  # noqa: ANN001, ANN202
        seen.append((tool, dict(kwargs)))
        return await real(caller, tool, arguments, **kwargs)

    monkeypatch.setattr(scan_mod, "read_all_rows", _spy)
    repo = _Repo()
    repo.snapshots = {"sms_inventory": ("run-0", SMS_ROWS)}
    domain = repo.seed(scope="domain", match_key="", basis="itam", state="failed")
    live["gw"] = _pods_gateway()
    client, _, _ = _client(repo)
    assert client.post(f"{BASE}/{domain['id']}/preview", headers=ADMIN_BG).status_code == 200
    limits = {"wait_seconds": 5.0, "call_timeout": 10.0, "deadline_seconds": 15.0}
    assert [t for t, _ in seen] == ["apm_instance_map", "apm_node_addresses"]
    assert all(kw == limits for _, kw in seen)
    assert routes._RESOLVE_READ == limits


def test_auto_off_preview_is_legacy_snapshot_shape(live: dict[str, Any]) -> None:
    """자동 생성 off = 종전(plans/210) 응답 — 가산 키 없음 · 게이트웨이 0 · 스냅샷 제안."""
    repo = _Repo()
    repo.snapshots = {
        "sms_inventory": ("run-0", SMS_ROWS),
        "apm_inventory": ("run-0", [_snap("101", POD_A, status="not_found")]),
    }
    row = repo.seed(scope="instance", match_key=POD_A, basis="vm_exact", state="failed")
    live["gw"] = _pods_gateway()
    client, _ = _app(repo=repo, auto=False)
    body = client.post(f"{BASE}/{row['id']}/preview", headers=ADMIN_BG).json()
    assert set(body) == {"map", "proposal", "reason", "action", "preview_only", "applied"}
    assert body["proposal"] is None and body["reason"] == "no_match"
    assert live["gw"].calls == [] and live["node"] == [] and live["vm"] == []


# ─── 화면 ──────────────────────────────────────────────────────────────


def _preview_routes(preview: dict[str, Any]) -> dict[str, Any]:
    return {
        f"GET {_API}": {
            "status": 200,
            "body": {"rows": [_ROW], "total": 1, "auto_enabled": True, "scan_available": False},
        },
        f"POST {_API}/7/preview": {"status": 200, "body": preview},
    }


def test_ui_preview_shows_tried_line_and_proposal_list() -> None:
    target = {"db_id": "polestar_cm_gp", "server_name": "node01"}
    proposals = [
        {
            "scope": "instance",
            "match_key": POD_A,
            "basis": "node_address",
            "state": "active",
            "targets": [target],
            "evidence": {},
            "action": "insert",
        },
        {
            "scope": "workload",
            "match_key": "pay-api",
            "basis": "node_address",
            "state": "active",
            "targets": [target],
            "evidence": {},
            "action": "same",
        },
    ]
    preview = {
        "map": _ROW,
        "proposal": proposals[0],
        "reason": None,
        "action": "insert",
        "preview_only": False,
        "applied": False,
        "proposals": proposals,
        "tried": {
            "node_address": "ok 1/1",
            "instances": "gateway",
            "vm_exact": "-",
            "itam": "skipped:linked",
        },
    }
    out = _admin(
        _preview_routes(preview),
        """
find(ids.apmMapBody, "button", "다시 찾기")[0].click();
await wait();
out = { text: ids.apmMapPreviewBody.textContent, hidden: ids.apmMapPreview.hidden };
""",
    )
    text = out["text"]
    assert out["hidden"] is False
    assert (
        "단계별 결과: 인스턴스 gateway · 노드 주소 ok 1/1 · VM 일치 - · ITAM skipped:linked" in text
    )
    assert "적용하면 쓰는 제안 2건" in text
    assert f"인스턴스 {POD_A} — 노드 주소 · 사용 — node01 — 새로 만듦" in text
    assert "워크로드 pay-api — 노드 주소 · 사용 — node01 — 결과 같음" in text


def test_ui_preview_single_proposal_has_no_list() -> None:
    preview = {
        "map": _ROW,
        "proposal": None,
        "reason": "no_match",
        "action": None,
        "preview_only": False,
        "applied": False,
        "proposals": [],
        "tried": {},
    }
    out = _admin(
        _preview_routes(preview),
        """
find(ids.apmMapBody, "button", "다시 찾기")[0].click();
await wait();
out = { text: ids.apmMapPreviewBody.textContent };
""",
    )
    assert "단계별 결과" not in out["text"] and "적용하면 쓰는 제안" not in out["text"]


def test_ui_scan_summary_shows_nonzero_node_counters_only() -> None:
    run = {
        **_RUN,
        "link_summary": {
            **_RUN["link_summary"],
            "node_ok": 4,
            "node_ip_unmatched": 2,
            "node_ip_ambiguous": 0,
            "node_not_ok": 1,
        },
    }
    routes = {
        f"GET {_API}": {
            "status": 200,
            "body": {"rows": [], "total": 0, "auto_enabled": True, "scan_available": True},
        },
        f"GET {_API}/scan/runs": {"status": 200, "body": {"runs": [run], "scan_available": True}},
    }
    out = _admin(
        routes,
        "await wait();\nout = { latest: ids.apmScanLatest.textContent };",
        hash_="#apmhostmap",
    )
    latest = out["latest"]
    assert "노드 주소 확보 4" in latest and "노드 IP 폴스타 미일치 2" in latest
    assert "노드 주소 없음 1" in latest and "노드 IP 여러 대 일치" not in latest
