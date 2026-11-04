"""plans/211 W7 독립 검증(verifier) — 이름 후보(L3) 색인 정합성 · 쓰기 규칙 행렬 · 진단 비차단.

구현자 테스트(`test_plan211_w7_scan_canonical.py`)가 덮지 않는 경계만 둔다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noise_gate.domain import apm_host_map as hm
from noise_gate.domain import apm_host_map_scan as scan
from noise_gate.domain.apm_host_map import (
    BASIS_NAME_CANDIDATE,
    BASIS_VM_EXACT,
    REASON_NAME_CANDIDATE,
    STATE_ACTIVE,
    STATE_AMBIGUOUS,
    WRITE_SAME,
    WRITE_SKIP,
    WRITE_SUGGEST,
    WRITE_UPDATE,
    AutoProposal,
    build_sms_index,
    decide_from_map,
    instance_match_key,
    make_target,
    plan_auto_write,
)
from noise_gate.domain.apm_host_map_scan import plan_scan_proposals

ROOT = Path(__file__).resolve().parents[2]


def _sms(db_id: str, name: str, ip: str = "", *, hostname: str | None = None,
         rid: int = 1) -> dict[str, Any]:
    return {"db_id": db_id, "server_name": name,
            "hostname": name if hostname is None else hostname,
            "ip": ip, "nic_ips": [], "resource_id": str(rid)}


def _inst(name: str, *, host: str = "", ip: str = "") -> dict[str, Any]:
    return {"source_id": "s1", "domain_id": "10", "domain_name": "order",
            "domain_description": "", "instance_name": name, "ip": ip, "host_name": host,
            "host_address": "", "host_address_status": ""}


def _plan(inventory: list[dict[str, Any]], sms: list[dict[str, Any]], *, canonical: bool = True,
          max_targets: int = 10) -> dict[str, list[AutoProposal]]:
    by_ip, by_host = build_sms_index(sms, canonical=canonical)
    return plan_scan_proposals(inventory, by_ip, by_host, use_node_address=True, itam=None,
                               max_targets=max_targets, canonical=canonical)


def _instance(out: dict[str, list[AutoProposal]], name: str) -> AutoProposal | None:
    key = instance_match_key(name)
    return next((p for p in out.get("s1", []) if p.scope == "instance" and p.match_key == key),
                None)


# ── L3 색인 정합성(by_key 충돌 · KeyError) ──────────────────────────────────
def test_l3_same_identity_in_two_buckets_no_keyerror() -> None:
    # 같은 (db_id, 서버명 소문자) 서버가 hostname이 다른 두 행으로 와서 서로 다른 버킷에 들어간다
    sms = [
        _sms("za", "PayWeb01", hostname="payweb01.a.local", rid=1),
        _sms("za", "payweb01", hostname="payweb01-b", rid=2),
        _sms("za", "  payweb02  ", hostname=" PAYWEB02 ", rid=3),
        _sms("zb", "payweb01", rid=4),  # 다른 소스의 같은 이름 — 별개 서버
    ]
    p = _instance(_plan([_inst("i1", host="payweb")], sms), "i1")
    assert p is not None and (p.basis, p.state) == (BASIS_NAME_CANDIDATE, STATE_AMBIGUOUS)
    idents = [hm.target_identity(t) for t in p.targets]
    assert len(idents) == len(set(idents))
    assert ("sms", "za", "payweb01") in idents and ("sms", "zb", "payweb01") in idents
    assert ("sms", "za", "payweb02") in idents
    assert len(p.evidence["candidates"]) == len(p.targets)


def test_l3_targets_follow_rank_order_and_candidates_align() -> None:
    sms = [_sms("za", f"ordapi{i:02d}", rid=i) for i in range(1, 6)]
    p = _instance(_plan([_inst("i1", host="ordapi")], sms, max_targets=3), "i1")
    assert p is not None
    assert [t["server_name"] for t in p.targets] == [
        c["server_name"] for c in p.evidence["candidates"]
    ]


# ── L3는 L0(IP·이름)가 성립하면 돌지 않는다 ─────────────────────────────────
def test_l3_not_used_when_ip_links() -> None:
    sms = [_sms("za", "payweb01", "10.0.0.5", rid=1), _sms("za", "payweb02", rid=2)]
    p = _instance(_plan([_inst("i1", host="zzz", ip="10.0.0.5")], sms), "i1")
    assert p is not None and (p.basis, p.state) == (BASIS_VM_EXACT, STATE_ACTIVE)
    assert "match" not in p.evidence  # 이름 색인은 비었고 IP로만 이었다


def test_l3_not_used_when_ip_and_name_disagree() -> None:
    sms = [_sms("za", "payweb01", "10.0.0.5", rid=1), _sms("za", "pay-web-02", rid=2)]
    p = _instance(_plan([_inst("i1", host="payweb02", ip="10.0.0.5")], sms), "i1")
    assert p is not None and (p.basis, p.state) == (BASIS_VM_EXACT, STATE_AMBIGUOUS)
    assert p.evidence.get("match") == "canonical"


def test_l3_index_not_built_when_off_or_all_l0(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[int] = []
    real = scan._NameCandidateIndex

    def _spy(by_host: Any) -> Any:
        built.append(1)
        return real(by_host)

    monkeypatch.setattr(scan, "_NameCandidateIndex", _spy)
    sms = [_sms("za", "web-ap01", rid=1)]
    _plan([_inst("i1", host="payweb")], sms, canonical=False)
    _plan([_inst("i1", host="WEBAP01"), _inst("i2", host="web_ap01")], sms, canonical=True)
    assert built == []
    _plan([_inst("i1", host="payweb"), _inst("i2", host="ordweb")], sms, canonical=True)
    assert built == [1]  # 스캔 1회에 1번만


def test_l3_rank_input_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[int] = []
    real = scan.rank_candidates

    def _spy(plan: Any, rows: Any, **kw: Any) -> Any:
        sizes.append(len(rows))
        return real(plan, rows, **kw)

    monkeypatch.setattr(scan, "rank_candidates", _spy)
    sms = [_sms("za", f"core-svc{i:04d}", rid=i) for i in range(3000)]
    # 3자리 접두 질의 — `core`(3000대 공유)는 상한으로 버리고 `svcNNN`·`coresvcNNN` 접두 10대씩만
    inv = [_inst(f"i{i}", host=f"core-svc{i:03d}") for i in range(0, 300, 7)]
    out = _plan(inv, sms)
    assert sizes and max(sizes) <= scan.NAME_CANDIDATE_BUCKET_CAP * 3
    assert all(p.basis == BASIS_NAME_CANDIDATE for p in out["s1"])


def test_l3_never_active_and_never_counts_as_linked() -> None:
    sms = [_sms("za", f"payweb{i:02d}", rid=i) for i in range(1, 4)]
    out = _plan([_inst("i1", host="payweb"), _inst("i2", host="payweb9")], sms)
    rows = [p for p in out["s1"] if p.basis == BASIS_NAME_CANDIDATE]
    assert rows and all(p.state == STATE_AMBIGUOUS and p.scope == "instance" for p in rows)
    # 이름 후보 1대뿐이어도 active가 되지 않는다
    one = _instance(_plan([_inst("i1", host="ordweb")], [_sms("za", "ordweb01")]), "i1")
    assert one is not None and one.state == STATE_AMBIGUOUS and len(one.targets) == 1


# ── 쓰기 규칙 행렬(이탈 1) ──────────────────────────────────────────────
T1 = make_target(db_id="za", server_name="payweb01")
T2 = make_target(db_id="za", server_name="payweb02")


def _cand(*targets: dict[str, Any]) -> AutoProposal:
    return AutoProposal(domain_id="10", scope="instance", match_key="i1",
                        basis=BASIS_NAME_CANDIDATE, state=STATE_AMBIGUOUS, targets=targets)


def _row(**kw: Any) -> dict[str, Any]:
    base = {"origin": "auto", "verified": False, "state": STATE_ACTIVE, "basis": BASIS_VM_EXACT,
            "targets": [T1], "evidence": {"k": 1}}
    base.update(kw)
    return base


@pytest.mark.parametrize(("existing", "action", "basis"), [
    (_row(), WRITE_SKIP, BASIS_VM_EXACT),                                       # auto active
    (_row(targets=[T1, T2]), WRITE_SKIP, BASIS_VM_EXACT),                       # 같은 대상
    (_row(state="disabled", origin="manual"), WRITE_SKIP, BASIS_VM_EXACT),      # 연계 없음
    (_row(state=STATE_AMBIGUOUS, targets=[T1]), WRITE_UPDATE, BASIS_NAME_CANDIDATE),
    (_row(state=STATE_AMBIGUOUS, targets=[T1, T2]), WRITE_SAME, BASIS_VM_EXACT),
    (_row(state="failed", targets=[]), WRITE_UPDATE, BASIS_NAME_CANDIDATE),
    (_row(verified=True), WRITE_SKIP, BASIS_VM_EXACT),                          # 관리자 확인 active
    # 관리자 확인 · 실패 행
    (_row(verified=True, state="failed", targets=[]), WRITE_SUGGEST, BASIS_VM_EXACT),
])
def test_plan_auto_write_name_candidate_matrix(existing: dict[str, Any], action: str,
                                               basis: str) -> None:
    w = plan_auto_write(existing, _cand(T1, T2), now_iso="2026-11-04T00:00:00+00:00")
    assert (w.action, w.basis) == (action, basis)
    if action in (WRITE_SKIP, WRITE_SAME):
        assert w.state == existing["state"] and w.evidence == existing["evidence"]


def test_plan_auto_write_insert_name_candidate() -> None:
    w = plan_auto_write(None, _cand(T1), now_iso="x")
    assert (w.action, w.state, w.basis) == ("insert", STATE_AMBIGUOUS, BASIS_NAME_CANDIDATE)


def test_vm_exact_ambiguous_still_overwrites_active_auto_row() -> None:
    # 이탈 1의 skip은 name_candidate만 — 현행 vm_exact 제안의 덮어쓰기 규칙은 그대로
    p = AutoProposal(domain_id="10", scope="instance", match_key="i1", basis=BASIS_VM_EXACT,
                     state=STATE_AMBIGUOUS, targets=(T1, T2))
    assert plan_auto_write(_row(), p, now_iso="x").action == WRITE_UPDATE


# ── 진단: 이름 후보 instance 행은 4순(workload·domain)을 막지 않는다 ─────────────
def test_name_candidate_instance_does_not_block_deferred_workload() -> None:
    inst = _row(state=STATE_AMBIGUOUS, basis=BASIS_NAME_CANDIDATE, targets=[T1, T2])
    wl = _row(basis="node_address", targets=[T1])
    d = decide_from_map({"instance": inst, "workload": wl})
    assert d.stage == hm.STAGE_MISS and d.pick is None
    assert d.deferred is not None and d.deferred.scope == "workload"
    assert d.reason == REASON_NAME_CANDIDATE


def test_verified_name_candidate_row_is_still_not_used() -> None:
    # 관리자가 「확인」만 누르고 대상을 고르지 않은 이름 후보 행 — ambiguous라 연결하지 않는다
    inst = _row(state=STATE_AMBIGUOUS, basis=BASIS_NAME_CANDIDATE, verified=True)
    d = decide_from_map({"instance": inst})
    assert d.pick is None and d.stage == hm.STAGE_MISS and d.reason == REASON_NAME_CANDIDATE


# ── 사유 어휘 사본 대칭(noise_gate ↔ sre_agent ↔ 사용자 매뉴얼) ────────────────
def test_reason_vocabulary_in_sre_agent_and_user_manual() -> None:
    runner = (ROOT / "sre_agent/sre_agent/application/playbook_runner.py").read_text("utf-8")
    assert f'"{REASON_NAME_CANDIDATE}":' in runner
    phrase = "이름이 비슷한 후보만 있음 — 관리자 확인 전 자동 연결 안 함"
    assert phrase in runner
    assert phrase in (ROOT / "scripts/manual/content/user.md").read_text("utf-8")
    assert phrase in (ROOT / "src/static/manual/user.html").read_text("utf-8")


def test_admin_js_basis_label_and_no_html_injection() -> None:
    js = (ROOT / "src/static/js/admin-apm-host-map.js").read_text("utf-8")
    assert 'name_candidate: "이름 후보"' in js
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in js
    html = (ROOT / "src/static/admin/dashboard.html").read_text("utf-8")
    assert '<option value="name_candidate">이름 후보</option>' in html
