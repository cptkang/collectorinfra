"""plans/211 W7 — 다시 찾기(`/preview`)가 스캔과 같은 정규형·이름 후보 제안을 내는지(on/off 대칭).

저장소 대역·앱 조립은 plans/210 W6 API 테스트의 것을 그대로 쓴다.
"""

from __future__ import annotations

from typing import Any

import pytest

from noise_gate.domain.apm_host_map import build_sms_index, instance_match_key
from noise_gate.domain.apm_host_map_scan import plan_scan_proposals
from tests.test_api.test_plan210_w6_api import ADMIN_BG, BASE, _app, _FakeRepo

INVENTORY = [
    {"source_id": "jen1", "domain_id": "1001", "domain_name": "인터넷뱅킹", "instance_id": "1",
     "instance_name": "was1", "ip": "", "host_name": "webap01", "workload_key": "",
     "host_address": "", "host_address_status": ""},
    {"source_id": "jen1", "domain_id": "1001", "domain_name": "인터넷뱅킹", "instance_id": "2",
     "instance_name": "was2", "ip": "", "host_name": "payweb", "workload_key": "",
     "host_address": "", "host_address_status": ""},
]
SMS = [
    {"db_id": "polestar_cm_gp", "resource_id": "r1", "server_name": "WEB-AP01",
     "hostname": "WEB-AP01.corp.local", "ip": "10.0.0.7", "nic_ips": []},
    {"db_id": "polestar_cm_gp", "resource_id": "r2", "server_name": "payweb01",
     "hostname": "payweb01", "ip": "10.0.0.8", "nic_ips": []},
]


def _repo() -> _FakeRepo:
    repo = _FakeRepo()
    repo.snapshots = {"apm_inventory": ("run-0", INVENTORY), "sms_inventory": ("run-0", SMS)}
    return repo


def _scan(canonical: bool) -> dict[str, Any]:
    by_ip, by_host = build_sms_index(SMS, canonical=canonical)
    out = plan_scan_proposals(INVENTORY, by_ip, by_host, use_node_address=True, itam=None,
                              max_targets=10, canonical=canonical)
    return {p.match_key: p for p in out.get("jen1", [])}


@pytest.mark.parametrize("canonical", [True, False])
@pytest.mark.parametrize("instance", ["was1", "was2"])
def test_preview_matches_scan(canonical: bool, instance: str) -> None:
    repo = _repo()
    seeded = repo.seed(state="failed", match_key=instance_match_key(instance))
    client, app = _app(repo=repo)
    app.state.config.host_canonical_match_enabled = canonical
    body = client.post(f"{BASE}/{seeded['id']}/preview", headers=ADMIN_BG).json()
    expected = _scan(canonical).get(instance)
    if expected is None:
        assert body["proposal"] is None and body["reason"] == "no_match"
        return
    proposal = body["proposal"]
    assert proposal is not None
    assert (proposal["basis"], proposal["state"]) == (expected.basis, expected.state)
    assert [t["server_name"] for t in proposal["targets"]] == [
        t["server_name"] for t in expected.targets
    ]


def test_preview_on_gives_canonical_and_name_candidate() -> None:
    repo = _repo()
    first = repo.seed(state="failed", match_key="was1")
    second = repo.seed(state="failed", match_key="was2")
    client, app = _app(repo=repo)
    app.state.config.host_canonical_match_enabled = True
    p1 = client.post(f"{BASE}/{first['id']}/preview", headers=ADMIN_BG).json()["proposal"]
    p2 = client.post(f"{BASE}/{second['id']}/preview", headers=ADMIN_BG).json()["proposal"]
    assert (p1["basis"], p1["state"]) == ("vm_exact", "active")
    assert (p2["basis"], p2["state"]) == ("name_candidate", "ambiguous")


def test_preview_flag_must_be_true_bool() -> None:
    repo = _repo()
    seeded = repo.seed(state="failed", match_key="was2")
    client, app = _app(repo=repo)
    app.state.config.host_canonical_match_enabled = "true"  # `is True`만 켠다
    body = client.post(f"{BASE}/{seeded['id']}/preview", headers=ADMIN_BG).json()
    assert body["proposal"] is None and body["reason"] == "no_match"


def test_list_filter_accepts_name_candidate_basis() -> None:
    repo = _repo()
    repo.seed(basis="name_candidate", state="ambiguous", match_key="was2")
    client, _ = _app(repo=repo)
    res = client.get(f"{BASE}?basis=name_candidate", headers=ADMIN_BG)
    assert res.status_code == 200, res.text
