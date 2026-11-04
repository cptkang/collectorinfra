"""plans/211 W7 — 스캔 정규형(L0) 색인 · 이름 후보(L3) 행 · 진단 사유 구분 (D-363 부기 4).

`HOST_CANONICAL_MATCH_ENABLED` on일 때만 SMS hostname 색인이 정규형이고, L0까지 못 이은 인스턴스는
`ambiguous` · `name_candidate`로만 남는다. off면 현행과 같다(plan210 테스트는 고치지 않는다).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from noise_gate.application.apm_host_map_scan import ApmHostMapScanService, ScanScanners
from noise_gate.domain import apm_host_map as hm
from noise_gate.domain.apm_host_map import (
    BASIS_NAME_CANDIDATE,
    BASIS_VM_EXACT,
    REASON_AMBIGUOUS,
    REASON_NAME_CANDIDATE,
    STATE_ACTIVE,
    STATE_AMBIGUOUS,
    WRITE_SKIP,
    WRITE_SUGGEST,
    WRITE_UPDATE,
    AutoProposal,
    build_sms_index,
    decide_from_map,
    instance_match_key,
    make_target,
    plan_auto_write,
    sms_host_key,
    sms_host_lookup,
)
from noise_gate.domain.apm_host_map_itam import ItamApp, ItamServer
from noise_gate.domain.apm_host_map_scan import (
    NAME_CANDIDATE_BUCKET_CAP,
    ItamSnapshot,
    ScannerResult,
    plan_scan_proposals,
)
from noise_gate.tests.plan210_w5_fakes import FakeCache, FakeLock, FakeRepo


def _sms(db_id: str, name: str, ip: str = "", *, hostname: str | None = None,
         rid: int = 1) -> dict[str, Any]:
    return {"db_id": db_id, "server_name": name,
            "hostname": name if hostname is None else hostname,
            "ip": ip, "nic_ips": [], "resource_id": str(rid)}


def _inst(name: str, *, host: str = "", ip: str = "", domain: str = "10",
          domain_name: str = "order") -> dict[str, Any]:
    return {"source_id": "s1", "domain_id": domain, "domain_name": domain_name,
            "domain_description": "", "instance_name": name, "ip": ip, "host_name": host,
            "host_address": "", "host_address_status": ""}


def _plan(inventory: list[dict[str, Any]], sms: list[dict[str, Any]], *, canonical: bool,
          itam: ItamSnapshot | None = None, max_targets: int = 10,
          ) -> dict[str, list[AutoProposal]]:
    by_ip, by_host = build_sms_index(sms, canonical=canonical)
    return plan_scan_proposals(inventory, by_ip, by_host, use_node_address=True, itam=itam,
                               max_targets=max_targets, canonical=canonical)


def _instance(out: dict[str, list[AutoProposal]], name: str) -> AutoProposal | None:
    key = instance_match_key(name)
    return next((p for p in out.get("s1", []) if p.scope == "instance" and p.match_key == key),
                None)


# ── L0: §5 벡터 ─────────────────────────────────────────────────────────
#: (인스턴스 host_name, SMS hostname, off 결과 state — None = 행 없음)
L0_VECTORS = [
    ("WEBAP01", "webap01", STATE_ACTIVE),
    ("web-ap01", "web_ap01", None),
    ("webap01.corp.local", "webap01", STATE_ACTIVE),
    ("webap01(인터넷뱅킹)", "webap01", None),
    ("webap01 (인터넷뱅킹)", "WEB-AP01.corp.local", None),
]


@pytest.mark.parametrize(("host", "sms_host", "_off"), L0_VECTORS)
def test_l0_vectors_link_active_with_canonical_evidence(host: str, sms_host: str,
                                                        _off: str | None) -> None:
    out = _plan([_inst("i1", host=host)], [_sms("zone_a", sms_host)], canonical=True)
    p = _instance(out, "i1")
    assert p is not None
    assert (p.basis, p.state) == (BASIS_VM_EXACT, STATE_ACTIVE)
    assert [t["server_name"] for t in p.targets] == [sms_host]
    assert p.evidence["match"] == "canonical"


@pytest.mark.parametrize(("host", "sms_host", "off"), L0_VECTORS)
def test_l0_vectors_off_is_current_behavior(host: str, sms_host: str, off: str | None) -> None:
    out = _plan([_inst("i1", host=host)], [_sms("zone_a", sms_host)], canonical=False)
    p = _instance(out, "i1")
    if off is None:
        assert p is None  # 현행 — 이름 후보 행도 만들지 않는다
    else:
        assert p is not None and (p.basis, p.state) == (BASIS_VM_EXACT, off)
        assert "match" not in p.evidence


def test_l0_fqdn_domains_differ_is_not_l0() -> None:
    out = _plan([_inst("i1", host="webap01.a.local")], [_sms("zone_a", "webap01.b.local")],
                canonical=True)
    p = _instance(out, "i1")
    assert p is None or p.basis != BASIS_VM_EXACT  # L0 불성립(이름 후보로만 남을 수 있다)
    assert p is None or p.state == STATE_AMBIGUOUS


def test_l0_prefix_is_not_l0_but_can_be_name_candidate() -> None:
    sms = [_sms("zone_a", "webap010")]
    by_ip, by_host = build_sms_index(sms, canonical=True)
    assert not sms_host_lookup(by_host, "webap01", canonical=True)  # L0 불성립
    p = _instance(_plan([_inst("i1", host="webap01")], sms, canonical=True), "i1")
    assert p is not None
    assert (p.basis, p.state) == (BASIS_NAME_CANDIDATE, STATE_AMBIGUOUS)
    assert [t["server_name"] for t in p.targets] == ["webap010"]
    assert p.evidence["candidates"][0]["grade_key"] == "similar"


def test_non_key_values() -> None:
    assert sms_host_key("(인터넷뱅킹)", canonical=True) == ""
    assert sms_host_key("web%01", canonical=True) == ""
    assert sms_host_key("10.1.0.1", canonical=True) == "10.1.0.1"
    _ip, by_host = build_sms_index([_sms("zone_a", "web%01")], canonical=True)
    assert by_host == {}


def test_l0_two_servers_is_ambiguous_vm_exact() -> None:
    sms = [_sms("zone_a", "web-ap01", rid=1), _sms("zone_b", "web_ap01", rid=2)]
    p = _instance(_plan([_inst("i1", host="webap01")], sms, canonical=True), "i1")
    assert p is not None
    assert (p.basis, p.state) == (BASIS_VM_EXACT, STATE_AMBIGUOUS)
    assert {t["server_name"] for t in p.targets} == {"web-ap01", "web_ap01"}


def test_off_lookup_matches_plain_get() -> None:
    rows = [_sms("zone_a", "WEB-AP01.corp.local"), _sms("zone_b", "db01", "10.0.0.9")]
    assert build_sms_index(rows) == build_sms_index(rows, canonical=False)
    _ip, by_host = build_sms_index(rows)
    for q in ("web-ap01", "WEB-AP01.corp.local", "db01", "", "x"):
        assert sms_host_lookup(by_host, q) == by_host.get(hm.normalize_hostname(q))


# ── L3: 이름 후보 ───────────────────────────────────────────────────────
def test_l3_candidates_capped_by_max_targets() -> None:
    sms = [_sms("zone_a", f"payweb{i:02d}", rid=i) for i in range(1, 16)]
    p = _instance(_plan([_inst("i1", host="payweb")], sms, canonical=True, max_targets=3), "i1")
    assert p is not None
    assert (p.basis, p.state) == (BASIS_NAME_CANDIDATE, STATE_AMBIGUOUS)
    assert len(p.targets) == 3 and len(p.evidence["candidates"]) == 3
    cand = p.evidence["candidates"][0]
    assert set(cand) == {"db_id", "server_name", "grade_key", "score", "reasons"}
    assert p.evidence["source"] == "scan" and p.evidence["host_name"] == "payweb"
    assert all(t["resource_id"] for t in p.targets)


def test_l3_no_candidates_no_row() -> None:
    out = _plan([_inst("i1", host="zzunique")], [_sms("zone_a", "ordweb01")], canonical=True)
    assert _instance(out, "i1") is None


def test_l3_uses_instance_name_when_no_host_name() -> None:
    out = _plan([_inst("payweb")], [_sms("zone_a", "payweb01")], canonical=True)
    p = _instance(out, "payweb")
    assert p is not None and p.basis == BASIS_NAME_CANDIDATE
    assert p.evidence["query"] == "payweb"


def test_l3_common_token_does_not_explode() -> None:
    # 전 서버가 `prod` 토큰을 공유 — 버킷 상한을 넘는 토큰은 좁힘에 쓰지 않는다
    sms = [_sms("zone_a", f"prod-svc{i:03d}", rid=i) for i in range(NAME_CANDIDATE_BUCKET_CAP + 5)]
    out = _plan([_inst("i1", host="prod-zz")], sms, canonical=True)
    assert _instance(out, "i1") is None


def test_l3_skips_pods_keeps_vm_instances() -> None:
    pod = "payweb-7d9f8c6b5-x2k4q"
    out = _plan([_inst(pod), _inst("i1", host="payweb")], [_sms("zone_a", "payweb01")],
                canonical=True)
    assert _instance(out, pod) is None
    vm = _instance(out, "i1")
    assert vm is not None and vm.basis == BASIS_NAME_CANDIDATE


def test_l3_off_makes_no_name_candidate() -> None:
    out = _plan([_inst("i1", host="payweb")], [_sms("zone_a", "payweb01")], canonical=False)
    assert _instance(out, "i1") is None


def test_l3_does_not_count_as_linked_for_itam() -> None:
    sms = [_sms("zone_a", "payweb01", rid=1), _sms("zone_a", "stlbat01", "10.2.0.9", rid=2)]
    itam = ItamSnapshot(apps=(ItamApp("STL", "결제정산"),),
                        servers=(ItamServer("stlbat01", ("10.2.0.9",), "결제정산 배치", True),))
    out = _plan([_inst("i1", host="payweb", domain_name="결제정산")], sms, canonical=True,
                itam=itam)
    kinds = {(p.scope, p.basis, p.state) for p in out["s1"]}
    assert ("instance", BASIS_NAME_CANDIDATE, STATE_AMBIGUOUS) in kinds
    assert ("domain", "itam", STATE_ACTIVE) in kinds


def test_itam_chain_uses_canonical_index() -> None:
    sms = [_sms("zone_a", "STL-BAT01.corp.local", rid=2)]
    itam = ItamSnapshot(apps=(ItamApp("STL", "결제정산"),),
                        servers=(ItamServer("stlbat01", (), "결제정산 배치", True),))
    inv = [_inst("i1", host="zzunique", domain_name="결제정산")]
    on = {p.scope: p for p in _plan(inv, sms, canonical=True, itam=itam)["s1"]}
    off = {p.scope: p for p in _plan(inv, sms, canonical=False, itam=itam)["s1"]}
    assert on["domain"].state == STATE_ACTIVE
    assert [t["server_name"] for t in on["domain"].targets] == ["STL-BAT01.corp.local"]
    assert off["domain"].state != STATE_ACTIVE


# ── 쓰기 규칙 ───────────────────────────────────────────────────────────
def _candidate(targets: tuple[dict[str, Any], ...]) -> AutoProposal:
    return AutoProposal(domain_id="10", scope="instance", match_key="i1",
                        basis=BASIS_NAME_CANDIDATE, state=STATE_AMBIGUOUS, targets=targets)


T1 = make_target(db_id="zone_a", server_name="payweb01")
T2 = make_target(db_id="zone_a", server_name="payweb02")


def test_manual_active_row_skips_name_candidate_without_scan_suggests() -> None:
    existing = {"origin": "manual", "verified": False, "state": STATE_ACTIVE, "basis": "manual",
                "targets": [T1], "evidence": {}}
    w = plan_auto_write(existing, _candidate((T2,)), now_iso="2026-11-04T00:00:00+00:00")
    assert w.action == WRITE_SKIP and w.targets == (T1,) and w.basis == "manual"
    assert "scan_suggests" not in w.evidence
    # 관리자 행 × vm_exact 다른 결과 → scan_suggests(현행 규칙 그대로)
    vm = AutoProposal(domain_id="10", scope="instance", match_key="i1", basis=BASIS_VM_EXACT,
                      state=STATE_ACTIVE, targets=(T2,))
    w = plan_auto_write(existing, vm, now_iso="2026-11-04T00:00:00+00:00")
    assert w.action == WRITE_SUGGEST and w.targets == (T1,)
    assert w.evidence["scan_suggests"]["basis"] == BASIS_VM_EXACT


def test_name_candidate_does_not_overwrite_active_auto_row() -> None:
    existing = {"origin": "auto", "verified": False, "state": STATE_ACTIVE,
                "basis": "node_address", "targets": [T1], "evidence": {}}
    w = plan_auto_write(existing, _candidate((T2,)), now_iso="x")
    assert w.action == WRITE_SKIP and w.state == STATE_ACTIVE and w.targets == (T1,)


def test_name_candidate_updates_failed_auto_row() -> None:
    existing = {"origin": "auto", "verified": False, "state": "failed", "basis": "vm_exact",
                "targets": [], "evidence": {}}
    w = plan_auto_write(existing, _candidate((T2,)), now_iso="x")
    assert w.action == WRITE_UPDATE and w.basis == BASIS_NAME_CANDIDATE
    assert w.state == STATE_AMBIGUOUS


# ── 진단: 이름 후보는 연결하지 않는다 ─────────────────────────────────────
def _rec(basis: str) -> dict[str, Any]:
    return {"origin": "auto", "verified": False, "state": STATE_AMBIGUOUS, "basis": basis,
            "targets": [T1]}


def test_decide_name_candidate_not_linked_distinct_reason() -> None:
    d = decide_from_map({"instance": _rec(BASIS_NAME_CANDIDATE)})
    assert d.stage == hm.STAGE_MISS and d.pick is None and d.deferred is None
    assert d.reason == REASON_NAME_CANDIDATE


def test_decide_vm_exact_ambiguous_keeps_reason() -> None:
    d = decide_from_map({"instance": _rec(BASIS_VM_EXACT)})
    assert d.stage == hm.STAGE_MISS and d.reason == REASON_AMBIGUOUS


def test_name_candidate_basis_registered() -> None:
    assert BASIS_NAME_CANDIDATE in hm.BASES and "BASIS_NAME_CANDIDATE" in hm.__all__
    assert "이름" in hm.evidence_text("auto", BASIS_NAME_CANDIDATE)


# ── 서비스 배선: 스캔 on · Redis 색인 불변 ─────────────────────────────────
SMS_ROWS = [_sms("zone_a", "WEB-AP01.corp.local", "10.1.0.1", rid=1),
            _sms("zone_a", "payweb01", "10.1.0.2", rid=2)]
INVENTORY = [_inst("i1", host="webap01"), _inst("i2", host="payweb")]


def _service(repo: FakeRepo, cache: FakeCache, *, canonical: bool) -> ApmHostMapScanService:
    async def inv() -> ScannerResult:
        return ScannerResult("ok", rows=len(INVENTORY), data=[dict(r) for r in INVENTORY])

    async def sms() -> ScannerResult:
        return ScannerResult("ok", rows=len(SMS_ROWS),
                             data={"rows": [dict(r) for r in SMS_ROWS], "scanned": ["zone_a"]})

    async def failed() -> ScannerResult:
        return ScannerResult("failed", error_kind="x")

    return ApmHostMapScanService(
        repo=repo, cache=cache, lock=FakeLock(), auto_enabled=True, canonical_match=canonical,
        scanners=ScanScanners(apm_inventory=inv, node_address=failed, sms=sms, itam=failed),
    )


@pytest.mark.parametrize("canonical", [True, False])
async def test_scan_service_canonical_and_redis_index_unchanged(canonical: bool) -> None:
    repo, cache = FakeRepo(), FakeCache()
    service = _service(repo, cache, canonical=canonical)
    await service.start_scan(requested_by="a")
    await service.wait_idle()
    vm = repo.row("s1", "10", "instance", "i1")
    cand = repo.row("s1", "10", "instance", "i2")
    if canonical:
        assert vm and (vm["basis"], vm["state"]) == (BASIS_VM_EXACT, STATE_ACTIVE)
        assert cand and (cand["basis"], cand["state"]) == (BASIS_NAME_CANDIDATE, STATE_AMBIGUOUS)
    else:
        assert vm is None and cand is None
    assert cache.sms_index is not None
    # 진단 고속 경로 Redis 색인은 늘 현행 키(normalize_hostname)
    assert cache.sms_index[1] == build_sms_index(SMS_ROWS)[1]
    assert "web-ap01" in cache.sms_index[1] and "webap01" not in cache.sms_index[1]


def test_build_scan_service_reads_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from noise_gate.application import apm_host_map_scan as app_scan
    from noise_gate.application import host_candidates as hc
    from noise_gate.infrastructure import apm_host_map_sql_scanners as sql_scanners

    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: ["zone_a"])
    monkeypatch.setattr(sql_scanners, "registry_executor", lambda app_config: None)
    captured: dict[str, Any] = {}
    real = app_scan.ApmHostMapScanService

    def _spy(**kw: Any) -> Any:
        captured.update(kw)
        return real(**kw)

    monkeypatch.setattr(app_scan, "ApmHostMapScanService", _spy)
    cache = SimpleNamespace(redis=object())
    for flag, want in ((True, True), (False, False), ("true", False)):
        cfg = SimpleNamespace(noise_gate=SimpleNamespace(), host_canonical_match_enabled=flag)
        app_scan.build_scan_service(cfg, repo=FakeRepo(), cache=cache)
        assert captured["canonical_match"] is want


# ── 비용: 5k × 5k ───────────────────────────────────────────────────────
def test_name_candidate_scale_5k_by_5k() -> None:
    sms = [_sms("zone_a" if i % 2 else "zone_b", f"prod-svc{i:04d}-g{i % 200:03d}", rid=i)
           for i in range(5000)]
    inventory = []
    for i in range(5000):
        if i % 3 == 0:
            host = f"prod-svc{i:04d}"          # 고유 토큰 · 접두 → 후보 1대 안팎
        elif i % 3 == 1:
            host = f"prod-x{i:04d}-g{i % 200:03d}"  # 그룹 토큰(25대) 버킷
        else:
            host = "prod-zz"                   # 흔한 토큰만 → 후보 없음
        inventory.append(_inst(f"inst{i:04d}", host=host))
    t0 = time.perf_counter()
    out = _plan(inventory, sms, canonical=True)
    elapsed = time.perf_counter() - t0
    assert elapsed < 10.0, elapsed
    rows = out["s1"]
    assert rows and all(p.basis == BASIS_NAME_CANDIDATE for p in rows)
    assert all(len(p.targets) <= 10 for p in rows)


# ── 진단 고속 경로(7순 사유) ─────────────────────────────────────────────
@pytest.mark.parametrize(("basis", "reason"), [
    (BASIS_NAME_CANDIDATE, REASON_NAME_CANDIDATE), (BASIS_VM_EXACT, REASON_AMBIGUOUS),
])
async def test_lookup_name_candidate_row_unresolved_with_reason(
    monkeypatch: pytest.MonkeyPatch, basis: str, reason: str,
) -> None:
    from noise_gate.application import host_candidates as hc
    from noise_gate.tests import test_plan210_w3_fast_path as fp

    probe = fp._FakeProbe()
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (probe, None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(fp.SOURCES))
    redis = fp._Redis()
    row = {**fp._row("instance", instance_match_key(fp.POD), rid=21, state=STATE_AMBIGUOUS),
           "basis": basis}
    await fp._cache_with(redis, row)
    event = fp._event(fp._apm(host_address="", host_address_status="not_found"))
    link = (await fp._attach(fp._cfg(), event, redis=redis))["polestar_link"]
    assert link["status"] == "unresolved" and link["reason"] == reason
    assert link["no_config_fallback"] is True
