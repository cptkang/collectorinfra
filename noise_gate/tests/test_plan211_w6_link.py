"""plans/211 W6 — APM↔SMS 연계: 노드 주소 우선 · ITAM은 마지막(A) · 실패 표시 정리(E) ·
스캔 사유 카운터(C) · SMS hostname 색인 정규형(F). §9.4 수용 기준 1·2·3·4·6·8·9.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import pytest
from src.api.routes import admin_apm_host_map as admin_routes

from noise_gate.application import apm_host_map_scan as scan_mod
from noise_gate.application.apm_host_map_scan import (
    DomainLinkPlan,
    apply_domain_link,
    fetch_node_addresses,
    handle_itam_request,
    handle_link_request,
    plan_domain_link,
)
from noise_gate.domain import apm_host_map as hm
from noise_gate.domain.apm_host_map_scan import (
    NODE_SUMMARY_KEYS,
    ItamSnapshot,
    empty_link_summary,
    plan_scan_proposals,
)
from noise_gate.infrastructure import apm_host_map_cache as cm

from .plan210_w5_fakes import NOW, FakeCache, FakeGateway, FakeRepo, apm_row, node_row
from .test_plan210_w1_cache import _Redis
from .test_plan210_w1_repository import _repo as _pg_repo
from .test_plan210_w1_repository import _sql
from .test_plan210_w5_scan import _gateway, _scan, _service

POD_A = "pay-api-7d9f8c6b5-x2k4q"
POD_B = "pay-api-7d9f8c6b5-b7n9r"
POD_C = "pay-api-7d9f8c6b5-m3p8z"
NODE_A, NODE_B, NODE_C = "10.9.0.5", "10.9.0.6", "10.9.0.7"
SMS_ROWS = [
    {"db_id": "zone_a", "resource_id": "2", "server_name": "node01", "hostname": "node01",
     "ip": "10.1.0.5", "nic_ips": [NODE_A]},
    {"db_id": "zone_b", "resource_id": "3", "server_name": "node02", "hostname": "node02",
     "ip": "10.2.0.6", "nic_ips": [NODE_B]},
    {"db_id": "zone_a", "resource_id": "7", "server_name": "ordweb01", "hostname": "ordweb01",
     "ip": "10.1.0.1", "nic_ips": []},
    {"db_id": "zone_b", "resource_id": "4", "server_name": "stlbat01", "hostname": "stlbat01",
     "ip": "10.2.0.9", "nic_ips": []},
]
PODS = [
    apm_row("s1", "20", "101", POD_A, domain_name="pay-k8s"),
    apm_row("s1", "20", "102", POD_B, domain_name="pay-k8s"),
    apm_row("s1", "20", "103", POD_C, domain_name="pay-k8s"),
]


def _repo(*, sms: bool = True, itam: bool = True) -> FakeRepo:
    repo = FakeRepo(extra_columns={"itam_snapshot_server": ("clue_text",)})
    if itam:
        repo.set_snapshot("itam_snapshot_app", "r0", [{"app_code": "PAY", "app_name": "pay"}])
        repo.set_snapshot("itam_snapshot_server", "r0", [
            {"hostname": "stlbat01", "ips": ["10.2.0.9"], "active": None, "clue_text": "pay"}])
    if sms:
        repo.set_snapshot("sms_inventory", "r0", SMS_ROWS)
    return repo


def _gw(instances: list[dict[str, Any]], nodes: list[dict[str, Any]] | None) -> FakeGateway:
    tools: dict[str, Any] = {"apm_instance_map": lambda args: {"rows": list(instances)}}
    if nodes is not None:
        tools["apm_node_addresses"] = lambda args: {
            "rows": list(nodes),
            "node_address_sources": [{"source_id": "s1", "status": "ok"}],
        }
    return FakeGateway(tools)


class _ItamSpy:
    """`resolve_itam_chain` 호출 기록(ITAM 사슬 0회 단언)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict[str, Any]] = []
        real = scan_mod.resolve_itam_chain

        def _spy(**kw: Any) -> Any:
            self.calls.append(kw)
            return real(**kw)

        monkeypatch.setattr(scan_mod, "resolve_itam_chain", _spy)


class _Probe:
    """`node_address_link` 가짜 — IP → 판정."""

    def __init__(self, found: dict[str, dict[str, Any]], *, delay: float = 0.0,
                 error: Exception | None = None) -> None:
        self.found, self.delay, self.error = found, delay, error
        self.calls: list[str] = []

    async def __call__(self, ip: str) -> dict[str, Any]:
        self.calls.append(ip)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.found.get(ip, {"status": "unresolved", "reason": "node_not_registered"})


def _msg(domain: str = "20") -> dict[str, Any]:
    return hm.itam_request_message(source_id="s1", domain_id=domain, domain_name="pay-k8s")


# ── 수용 1 — 노드 주소 2/3 ok → instance 2 · workload 1 · ITAM 0 · domain 행 없음 ──────
async def test_node_address_links_instances_without_itam(monkeypatch: pytest.MonkeyPatch
                                                         ) -> None:
    spy = _ItamSpy(monkeypatch)
    repo, cache = _repo(), FakeCache()
    gateway = _gw(PODS, [node_row("s1", "20", "101", NODE_A), node_row("s1", "20", "102", NODE_B),
                         node_row("s1", "20", "103", "", status="not_found")])
    out = await handle_link_request(_msg(), repo=repo, cache=cache, gateway_client=gateway)
    assert out["status"] == "ok" and out["linked"] == 2
    name, args = gateway.calls[1]
    assert name == "apm_node_addresses"
    assert (args["source_ids"], args["domain_ids"]) == (["s1"], [20])
    for pod, server in ((POD_A, "node01"), (POD_B, "node02")):
        row = repo.row("s1", "20", "instance", hm.instance_match_key(pod))
        assert row and (row["basis"], row["state"]) == ("node_address", "active")
        assert [t["server_name"] for t in row["targets"]] == [server]
    assert repo.row("s1", "20", "instance", hm.instance_match_key(POD_C)) is None
    workload = repo.row("s1", "20", "workload", "pay-api")
    assert workload and (workload["basis"], workload["state"]) == ("node_address", "active")
    assert {t["server_name"] for t in workload["targets"]} == {"node01", "node02"}
    assert spy.calls == [] and repo.row("s1", "20", "domain", "") is None
    assert out["tried"] == {"node_address": "ok 2/3 · not_ok 1", "vm_exact": "-",
                            "itam": "skipped:linked"}
    assert len(cache.put_rows) == 3


async def test_handle_itam_request_is_alias() -> None:
    assert handle_itam_request is handle_link_request


# ── 수용 2 — 노드 IP가 SMS 색인에 없음 → node_address_link 직접 조회 ──────────────
@pytest.mark.parametrize("sms", [True, False], ids=["index_miss", "no_sms_snapshot"])
async def test_index_miss_uses_live_node_lookup(sms: bool) -> None:
    repo, cache = _repo(sms=sms), FakeCache()
    probe = _Probe({"10.9.9.1": {"status": "linked", "db_id": "zone_a", "server_name": "node09",
                                 "hostname": "node09", "resource_id": "9"}})
    gateway = _gw(PODS[:1], [node_row("s1", "20", "101", "10.9.9.1")])
    out = await handle_link_request(_msg(), repo=repo, cache=cache, gateway_client=gateway,
                                    node_link=probe)
    assert probe.calls == ["10.9.9.1"]
    row = repo.row("s1", "20", "instance", hm.instance_match_key(POD_A))
    assert row and row["basis"] == "node_address" and row["state"] == "active"
    assert row["targets"][0]["server_name"] == "node09"
    assert row["targets"][0]["ip"] == "10.9.9.1"
    assert row["evidence"]["live"] is True and row["evidence"]["trigger"] == "alarm_miss"
    assert out["tried"]["node_address"] == "ok 1/1 · live 1/1"


async def test_live_lookup_absent_or_over_budget_moves_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """직접 조회가 없거나(None) 예산 초과·예외면 사유를 남기고 다음 단계로 간다."""
    gateway = _gw(PODS[:1], [node_row("s1", "20", "101", "10.9.9.1")])
    out = await handle_link_request(_msg(), repo=_repo(), cache=FakeCache(),
                                    gateway_client=gateway)
    assert out["tried"]["node_address"] == "ok 0/1 · unmatched 1 · live_off 1"

    slow = _Probe({}, delay=1.0)
    with caplog.at_level(logging.WARNING):
        out = await handle_link_request(
            _msg(), repo=_repo(), cache=FakeCache(), gateway_client=gateway, node_link=slow,
            live_budget_seconds=0.01,
        )
    assert slow.calls == ["10.9.9.1"] and "예산 초과" in caplog.text
    assert out["tried"]["node_address"] == "ok 0/1 · unmatched 1 · live 0/1 · live_failed 1"

    boom = _Probe({}, error=RuntimeError("소스 목록 해석 실패"))
    out = await handle_link_request(_msg(), repo=_repo(), cache=FakeCache(),
                                    gateway_client=gateway, node_link=boom)
    assert out["tried"]["node_address"].endswith("live_failed 1")
    assert out["tried"]["itam"] != "skipped:linked"  # 못 이었으므로 ITAM 단계로 간다


# ── 수용 3 — 노드 주소 도구 미등록 → node_address_disabled · V → I ─────────────────
async def test_node_tool_unregistered_goes_vm_then_itam(monkeypatch: pytest.MonkeyPatch
                                                        ) -> None:
    spy = _ItamSpy(monkeypatch)
    vm = [apm_row("s1", "10", "i1", "ordweb01", ip="10.1.0.1", domain_name="order")]
    repo, cache = _repo(), FakeCache()
    out = await handle_link_request(_msg("10"), repo=repo, cache=cache,
                                    gateway_client=_gw(vm, None))
    row = repo.row("s1", "10", "instance", "ordweb01")
    assert row and (row["basis"], row["state"]) == ("vm_exact", "active")
    assert out["tried"] == {"node_address": "node_address_disabled", "vm_exact": "linked 1/1",
                            "itam": "skipped:linked"}
    assert spy.calls == []

    # V도 못 이으면 ITAM — evidence에 단계별 결과(노드 주소 미등록 사유)
    lost = [apm_row("s1", "30", "b1", "settle-batch", domain_name="pay")]
    repo2 = _repo()
    out = await handle_link_request(_msg("30"), repo=repo2, cache=FakeCache(),
                                    gateway_client=_gw(lost, None))
    assert len(spy.calls) == 1
    domain = repo2.row("s1", "30", "domain", "")
    assert domain and domain["basis"] == "itam"
    assert domain["evidence"]["tried"]["node_address"] == "node_address_disabled"
    assert domain["evidence"]["tried"]["vm_exact"] == "-"
    assert out["tried"]["itam"] == domain["state"] or out["tried"]["itam"].startswith(
        domain["state"])


async def test_vm_live_lookup_for_non_pod_index_miss() -> None:
    """V: 파드 아닌 인스턴스가 색인 0대면 `vm_exact_link`(가짜) — 파드는 직접 조회하지 않는다."""
    calls: list[tuple[str, str]] = []

    async def _vm(ip: str, host: str) -> Any:
        calls.append((ip, host))
        target = hm.make_target(db_id="zone_a", server_name="newvm01", ip=ip)
        return hm.VmMatch("linked", (target,)), []

    rows = [apm_row("s1", "11", "i9", "newvm01", ip="10.5.0.9", domain_name="new"),
            apm_row("s1", "11", "p9", POD_A, ip="10.5.0.10", domain_name="new")]
    repo = _repo()
    out = await handle_link_request(_msg("11"), repo=repo, cache=FakeCache(),
                                    gateway_client=_gw(rows, []), vm_link=_vm)
    assert calls == [("10.5.0.9", "")]
    row = repo.row("s1", "11", "instance", "newvm01")
    assert row and row["basis"] == "vm_exact" and row["evidence"]["live"] is True
    assert out["tried"]["vm_exact"] == "linked 1/2 · live 1/1"


# ── 수용 4 — 기존 domain 행 failed·재시도 대기 → N·V 실행 · ITAM만 건너뜀 · 표지 ──────
async def test_retry_wait_blocks_only_itam_and_supersedes(monkeypatch: pytest.MonkeyPatch
                                                          ) -> None:
    spy = _ItamSpy(monkeypatch)
    repo, cache = _repo(), FakeCache()
    old = repo.seed("s1", "20", "domain", "", state="failed", basis="itam",
                    evidence={"reason": "no_sms_match"},
                    next_retry_at=(NOW + timedelta(hours=20)).isoformat())
    gateway = _gw(PODS[:2], [node_row("s1", "20", "101", NODE_A),
                             node_row("s1", "20", "102", NODE_B)])
    out = await handle_link_request(_msg(), repo=repo, cache=cache, gateway_client=gateway,
                                    now=NOW)
    assert out["status"] == "ok" and out["linked"] == 2 and spy.calls == []
    assert out["tried"]["itam"] == "skipped:linked"
    inst = repo.row("s1", "20", "instance", hm.instance_match_key(POD_A))
    assert inst and inst["state"] == "active"
    domain = repo.row("s1", "20", "domain", "")
    assert domain is not None and domain["id"] == old["id"] and domain["state"] == "failed"
    marker = domain["evidence"]["superseded"]
    assert marker["bases"] == ["node_address"] and marker["linked"] == 2
    assert marker["at"] == NOW.isoformat() and marker["tried"] == out["tried"]
    assert domain["evidence"]["reason"] == "no_sms_match"  # 원래 근거는 지우지 않는다
    assert any(r["id"] == domain["id"] and r["evidence"].get("superseded")
               for r in cache.put_rows)

    # 다음 알람 — 그 인스턴스는 2순 hit · 같은 도메인 다른 인스턴스는 7순 사유에 failed 없음
    hit = hm.decide_from_map({"instance": hm.cache_value(inst),
                              "domain": hm.cache_value(domain)})
    assert hit.stage == hm.STAGE_AUTO_INSTANCE and hit.reason is None
    other = hm.decide_from_map({"domain": hm.cache_value(domain)})
    assert other.stage == hm.STAGE_MISS and other.reason is None
    # 대조 — 표지 없는 같은 행은 종전대로 apm_map_failed
    plain = {**domain, "evidence": {"reason": "no_sms_match"}}
    assert hm.decide_from_map({"domain": hm.cache_value(plain)}).reason == hm.REASON_FAILED


async def test_superseded_marker_cleared_when_itam_rewrites_same(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """표지 달린 행을 ITAM이 같은 결과로 다시 쓰면(evidence 유지) 표지를 뗀다."""
    repo, cache = _repo(), FakeCache()
    repo.seed("s1", "30", "domain", "", state="failed", basis="itam",
              evidence={"superseded": {"bases": ["node_address"], "linked": 1}})
    lost = [apm_row("s1", "30", "b1", "settle-batch", domain_name="nomatch-xyz")]
    out = await handle_link_request(_msg("30"), repo=repo, cache=cache,
                                    gateway_client=_gw(lost, []), now=NOW)
    assert out["state"] == "failed"
    domain = repo.row("s1", "30", "domain", "")
    assert domain and "superseded" not in domain["evidence"]
    assert ("s1", "30", None) in repo.superseded_calls  # type: ignore[attr-defined]


async def test_superseded_marker_is_not_reapplied() -> None:
    """같은 도메인 두 번째 요청 — 표지가 있으면 다시 달지 않는다(version 불변 · D-362 ③)."""
    repo = _repo()
    repo.seed("s1", "20", "domain", "", state="failed", basis="itam",
              next_retry_at=(NOW + timedelta(hours=20)).isoformat())
    gateway = _gw(PODS[:2], [node_row("s1", "20", "101", NODE_A),
                             node_row("s1", "20", "102", NODE_B)])
    await handle_link_request(_msg(), repo=repo, cache=FakeCache(), gateway_client=gateway,
                              now=NOW)
    first = repo.row("s1", "20", "domain", "")
    assert first is not None
    version, at = first["version"], first["evidence"]["superseded"]["at"]
    cache = FakeCache()
    out = await handle_link_request(_msg(), repo=repo, cache=cache, gateway_client=gateway,
                                    now=NOW + timedelta(minutes=10))
    assert out["linked"] == 2
    second = repo.row("s1", "20", "domain", "")
    assert second is not None and second["version"] == version
    assert second["evidence"]["superseded"]["at"] == at == NOW.isoformat()
    assert all(r["id"] != second["id"] for r in cache.put_rows)


def test_review_reasons_skip_superseded_rows() -> None:
    row = {"state": "failed", "evidence": {"superseded": {"linked": 1}, "changed": True},
           "targets": []}
    assert admin_routes._review_reasons(row) == []
    assert admin_routes._review_reasons({**row, "evidence": {}}) == ["failed"]


async def test_pg_set_domain_superseded_is_bound_and_scoped() -> None:
    repo, conn = _pg_repo()
    assert await repo.set_domain_superseded("jen", "7", {"linked": 2}) is None
    assert await repo.set_domain_superseded("jen", "7", None) is None
    put_sql, clear_sql = _sql(conn)
    for sql in (put_sql, clear_sql):
        assert "scope = $3 AND match_key = ''" in sql and "origin = $4" in sql
        assert "verified = FALSE AND state = ANY($5::text[])" in sql
    assert "evidence || $6::jsonb" in put_sql and "NOT (evidence ? 'superseded')" in put_sql
    assert "evidence - 'superseded'" in clear_sql and "evidence ? 'superseded'" in clear_sql
    args = conn.calls[0][2]
    assert args[:5] == ("jen", "7", "domain", "auto", ["failed", "ambiguous"])
    assert args[5] == '{"superseded": {"linked": 2}}'


async def test_review_sql_excludes_superseded() -> None:
    repo, conn = _pg_repo()
    conn.fetchval_q.append(0)
    await repo.list_maps(review_only=True)
    assert "NOT (evidence ? 'superseded')" in _sql(conn)[0]


def test_cache_value_adds_superseded_only_when_marked() -> None:
    row = {"id": 3, "origin": "auto", "basis": "itam", "state": "failed", "targets": [],
           "evidence": {"reason": "x"}, "version": 2}
    assert "superseded" not in hm.cache_value(row)
    marked = {**row, "evidence": {"superseded": {"linked": 1}}}
    assert hm.cache_value(marked) == {**hm.cache_value(row), "superseded": True}


# ── 공개 계획 단계(B 재사용) — 쓰기 0 ──────────────────────────────────────────
async def test_plan_domain_link_writes_nothing_and_apply_saves() -> None:
    repo, cache = _repo(), FakeCache()
    gateway = _gw(PODS[:1], [node_row("s1", "20", "101", NODE_A)])
    by_ip, by_host = hm.build_sms_index(SMS_ROWS)
    plan = await plan_domain_link(gateway, "s1", "20", PODS[:1], by_ip=by_ip, by_host=by_host)
    assert isinstance(plan, DomainLinkPlan) and plan.linked == 1 and plan.itam is None
    assert plan.itam_skip == "linked" and repo.rows == {}
    assert [p.scope for p in plan.proposals] == ["instance", "workload"]
    out = await apply_domain_link(plan, repo, cache, now=NOW)
    assert [r["scope"] for r in out["rows"]] == ["instance", "workload"]
    assert all(r["action"] == "insert" and r["map_id"] > 0 for r in out["rows"])


async def test_fetch_node_addresses_reasons() -> None:
    gateway = _gw([], [node_row("s1", "20", "101", NODE_A)])
    found, reason = await fetch_node_addresses(gateway, "s1", "20", instance_ids=["101"])
    assert reason == "ok" and found[("s1", "20", "101")] == (NODE_A, "ok")
    assert gateway.calls[0][1]["instance_ids"] == [101]
    _, reason = await fetch_node_addresses(_gw([], None), "s1", "20")
    assert reason == "node_address_disabled"
    broken = FakeGateway({"apm_node_addresses": {"error": "source_unavailable"}})
    _, reason = await fetch_node_addresses(broken, "s1", "20")
    assert reason == "node_address_error:source_unavailable"
    blocked = FakeGateway({"apm_node_addresses": {
        "rows": [], "node_address_sources": [{"source_id": "s1", "status": "login_failed"}]}})
    _, reason = await fetch_node_addresses(blocked, "s1", "20")
    assert reason == "node_address_error:login_failed"


async def test_stale_or_ambiguous_node_address_is_not_linked() -> None:
    """D-355 ⑦ — `stale`은 쓰지 않는다 · D-224 ④ — 노드 IP가 2대면 고르지 않는다."""
    sms = [*SMS_ROWS, {"db_id": "zone_b", "resource_id": "8", "server_name": "dup01",
                       "hostname": "dup01", "ip": "10.8.0.1", "nic_ips": [NODE_A]}]
    repo = _repo()
    repo.set_snapshot("sms_inventory", "r1", sms)
    probe = _Probe({})
    gateway = _gw(PODS[:2], [node_row("s1", "20", "101", NODE_A),
                             node_row("s1", "20", "102", NODE_B, status="stale")])
    out = await handle_link_request(_msg(), repo=repo, cache=FakeCache(),
                                    gateway_client=gateway, node_link=probe)
    assert out["linked"] == 0 and probe.calls == []
    assert out["tried"]["node_address"] == "ok 0/2 · ambiguous 1 · not_ok 1"
    assert repo.row("s1", "20", "instance", hm.instance_match_key(POD_A)) is None


# ── 수용 6 — 스캔 link_summary 노드 주소 사유 카운터 ───────────────────────────────
async def test_scan_link_summary_counts_node_reasons() -> None:
    from noise_gate.tests.test_plan210_w5_scan import INVENTORY, NODES

    inventory = [*INVENTORY, apm_row("s1", "20", "p3", POD_C, domain_name="pay-k8s"),
                 apm_row("s1", "20", "p4", "pay-api-7d9f8c6b5-q1w2e", domain_name="pay-k8s")]
    nodes = [*NODES, node_row("s1", "20", "p3", "10.99.0.1"),
             node_row("s1", "20", "p4", "", status="not_found")]
    repo, cache = FakeRepo(), FakeCache()
    run_id = await _scan(_service(repo, cache, gateway=_gateway(inventory=inventory,
                                                                 nodes=nodes)))
    summary = repo.runs[run_id]["link_summary"]
    # 노드 주소 ok 3(p1·p2·p3) · p3 색인 없음 · 나머지(i1·b1·p4) 상태 ok 아님
    assert (summary["node_ok"], summary["node_ip_unmatched"], summary["node_ip_ambiguous"],
            summary["node_not_ok"]) == (3, 1, 0, 3)
    assert set(empty_link_summary()) >= set(NODE_SUMMARY_KEYS)


async def test_scan_supersedes_failed_domain_row_of_linked_domain() -> None:
    repo, cache = FakeRepo(), FakeCache()
    repo.seed("s1", "20", "domain", "", state="failed", basis="itam")
    run_id = await _scan(_service(repo, cache))
    domain = repo.row("s1", "20", "domain", "")
    assert domain and domain["evidence"]["superseded"]["run_id"] == run_id
    assert domain["evidence"]["superseded"]["source"] == "scan"


# ── 수용 8 — 폴스타 `web01(인터넷뱅킹)` ↔ APM `WEB-01` (정규형 on 일치 · off 불일치) ───
WEB = [{"db_id": "zone_a", "resource_id": "5", "server_name": "web01(인터넷뱅킹)",
        "hostname": "web01(인터넷뱅킹)", "ip": "10.3.0.1", "nic_ips": []}]


@pytest.mark.parametrize("canonical", [True, False])
def test_sms_index_canonical_key(canonical: bool) -> None:
    by_ip, by_host = hm.build_sms_index(WEB, canonical=canonical)
    found = hm.sms_host_servers(by_host, "WEB-01", canonical=canonical)
    assert [s["db_id"] for s in found] == (["zone_a"] if canonical else [])
    inv = [{"source_id": "s1", "domain_id": "40", "domain_name": "ib",
            "instance_name": "ibweb", "ip": "", "host_name": "WEB-01"}]
    plan = plan_scan_proposals(inv, by_ip, by_host, use_node_address=False, itam=None,
                               canonical=canonical)
    if canonical:
        (proposal,) = plan["s1"]
        assert (proposal.basis, proposal.state) == ("vm_exact", "active")
    else:
        assert plan == {}


def test_canonical_fqdn_rule_and_off_identity() -> None:
    rows = [{"db_id": "zone_a", "server_name": "web01.a.example", "hostname": "web01.a.example",
             "ip": "10.3.0.2"}]
    _, by_host = hm.build_sms_index(rows, canonical=True)
    assert hm.sms_host_servers(by_host, "web01.b.example", canonical=True) == []
    assert len(hm.sms_host_servers(by_host, "WEB-01", canonical=True)) == 1
    assert len(hm.sms_host_servers(by_host, "web01.a.example.", canonical=True)) == 1
    # off — 키·결과가 현행(normalize_hostname)과 같다
    assert hm.build_sms_index(SMS_ROWS) == hm.build_sms_index(SMS_ROWS, canonical=False)
    assert hm.sms_host_key("Node01.corp.") == "node01"


@pytest.mark.parametrize("canonical", [True, False])
async def test_cache_sms_by_host_canonical(canonical: bool) -> None:
    class _SmsRepo:
        async def all_rows(self) -> list[dict[str, Any]]:
            return []

        async def snapshot_rows(self, table: str, **_: Any) -> list[dict[str, Any]]:
            return WEB

    cache = cm.RedisApmHostMapCache(_Redis(), canonical=canonical)
    await cache.sync_from_repo(_SmsRepo())  # type: ignore[arg-type]
    got = await cache.sms_by_host("WEB-01")
    assert got.error is None
    assert [s["server_name"] for s in got.servers] == (
        ["web01(인터넷뱅킹)"] if canonical else [])


def test_itam_chain_canonical_hostname() -> None:
    from noise_gate.domain.apm_host_map_itam import (
        apps_from_rows,
        resolve_itam_chain,
        servers_from_rows,
    )

    apps = apps_from_rows([{"app_code": "IB", "app_name": "인터넷뱅킹"}])
    servers = servers_from_rows([{"hostname": "WEB-01", "ips": [], "active": None,
                                  "clue_text": "인터넷뱅킹"}])
    for canonical, state in ((True, "active"), (False, "failed")):
        by_ip, by_host = hm.build_sms_index(WEB, canonical=canonical)
        result = resolve_itam_chain(domain_name="인터넷뱅킹", apps=apps, servers=servers,
                                    by_ip=by_ip, by_host=by_host, canonical=canonical)
        assert result.state == state


# ── 실 배선 — 직접 조회·예산·정규형 주입 ─────────────────────────────────────────
async def test_build_handler_injects_live_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from noise_gate.application import host_candidates as hc
    from noise_gate.infrastructure import apm_gateway_client as gc
    from noise_gate.infrastructure import apm_host_map_sql_scanners as sq

    probe = _Probe({"10.9.9.1": {"status": "linked", "db_id": "zone_a", "server_name": "node09"}})

    async def _node_address_link(cfg: Any, *, host_address: str) -> dict[str, Any]:
        return await probe(host_address)

    gateway = _gw(PODS[:1], [node_row("s1", "20", "101", "10.9.9.1")])
    monkeypatch.setattr(hc, "node_address_link", _node_address_link)
    monkeypatch.setattr(gc, "build_apm_gateway_client", lambda cfg: gateway)

    def _no_itam() -> Any:
        raise sq.ScanSqlError("config_invalid", "테스트")

    monkeypatch.setattr(sq, "load_itam_lookup", _no_itam)
    cfg = SimpleNamespace(host_canonical_match_enabled=True, noise_gate=SimpleNamespace(
        apm_host_map_max_targets=10, apm_host_map_retry_hours=24,
        apm_host_map_live_budget_seconds=3))
    repo = _repo(sms=False)
    handler = scan_mod.build_itam_request_handler(cfg, repo=repo, cache=FakeCache())  # type: ignore[arg-type]
    out = await handler(_msg())
    assert probe.calls == ["10.9.9.1"] and out["linked"] == 1
    assert scan_mod._live_budget(SimpleNamespace(apm_host_map_live_budget_seconds=-1)) == 5.0
    assert scan_mod._canonical(cfg) is True


def test_plan_scan_proposals_defaults_are_unchanged() -> None:
    """수용 9 — 새 선택 인자를 안 주면(기본값) 종전 결과·evidence와 같다."""
    inv = [{"source_id": "s1", "domain_id": "10", "instance_name": "ordweb01", "ip": "10.1.0.1",
            "host_name": "ordweb01"}]
    by_ip, by_host = hm.build_sms_index(SMS_ROWS)
    plan = plan_scan_proposals(inv, by_ip, by_host, use_node_address=True,
                               itam=ItamSnapshot(apps=(), servers=()))
    (proposal,) = plan["s1"]
    assert proposal.evidence == {"source": "scan", "ip": "10.1.0.1", "host_name": "ordweb01",
                                 "candidates": 1}
