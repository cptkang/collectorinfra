"""plans/210 W1 — 연계 매핑 도메인 순수 함수.

키·조회 판정·쓰기 규칙·되적재·스캔 대조·VM 일치·힌트.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from noise_gate.domain import apm_host_map as m

POD = "bfm-kbland-admin-price-deploy-6dd49d5df6-x7k2p"
WORKLOAD = "bfm-kbland-admin-price"
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def _t(name: str, db: str = "pg_a", **kw: Any) -> dict[str, Any]:
    return m.make_target(db_id=db, server_name=name, hostname=name, **kw)


def _rec(origin: str = m.ORIGIN_AUTO, state: str = m.STATE_ACTIVE, *, verified: bool = False,
         targets: list[dict[str, Any]] | None = None, rid: int = 1,
         basis: str = m.BASIS_NODE_ADDRESS) -> dict[str, Any]:
    return {
        "id": rid, "origin": origin, "basis": basis, "state": state, "verified": verified,
        "targets": targets if targets is not None else [_t("node01")], "version": 1,
    }


# ── 키 ──────────────────────────────────────────────────────────────────
def test_keys_pod_vm_and_domain() -> None:
    assert m.instance_match_key("Web01.Example.com") == "web01"
    assert m.workload_match_key(POD) == WORKLOAD
    assert m.workload_match_key("web01") is None
    assert m.match_key_for(m.SCOPE_DOMAIN, POD) == ""
    assert m.match_key_for("bogus", POD) is None
    assert m.cache_field(m.SCOPE_WORKLOAD, "7", WORKLOAD) == f"workload|7|{WORKLOAD}"
    assert m.lookup_fields("7", POD) == {
        "instance": f"instance|7|{POD}",
        "workload": f"workload|7|{WORKLOAD}",
        "domain": "domain|7|",
    }
    assert list(m.lookup_fields("7", "web01")) == ["instance", "domain"]  # VM은 workload 없음
    assert list(m.lookup_fields("7", "")) == ["domain"]


def test_key_validation_rejects_separators_and_empty_parts() -> None:
    with pytest.raises(ValueError):
        m.cache_field(m.SCOPE_INSTANCE, "7|8", "web01")
    with pytest.raises(ValueError):
        m.cache_field(m.SCOPE_INSTANCE, "", "web01")
    assert m.valid_key("jen", "7", m.SCOPE_INSTANCE, "web01")
    assert not m.valid_key("jen:x", "7", m.SCOPE_INSTANCE, "web01")
    assert not m.valid_key("jen", "7", m.SCOPE_INSTANCE, "")
    assert m.valid_key("jen", "7", m.SCOPE_DOMAIN, "")
    assert not m.valid_key("jen", "7", m.SCOPE_DOMAIN, "x")
    assert not m.valid_key("jen", "7", "bogus", "x")


# ── 조회 판정 (§3.1 1·2·4순) ───────────────────────────────────────────
def test_manual_instance_is_stage1() -> None:
    d = m.decide_from_map({"instance": _rec(m.ORIGIN_MANUAL)})
    assert d.stage == m.STAGE_MANUAL and d.pick is not None and d.pick.scope == "instance"


def test_manual_workload_beats_auto_instance() -> None:
    d = m.decide_from_map({
        "instance": _rec(targets=[_t("auto01")]),
        "workload": _rec(m.ORIGIN_MANUAL, targets=[_t("man01")], rid=2),
    })
    assert d.stage == m.STAGE_MANUAL
    assert d.pick is not None and d.pick.scope == "workload"
    assert d.pick.targets[0]["server_name"] == "man01"


def test_verified_auto_domain_counts_as_admin() -> None:
    d = m.decide_from_map({
        "instance": _rec(targets=[_t("auto01")]),
        "domain": _rec(verified=True, targets=[_t("ver01")], rid=3),
    })
    assert d.stage == m.STAGE_MANUAL and d.pick is not None and d.pick.scope == "domain"


def test_disabled_is_absent_even_with_auto_instance() -> None:
    d = m.decide_from_map({
        "instance": _rec(),
        "workload": _rec(m.ORIGIN_MANUAL, m.STATE_DISABLED, targets=[], rid=2),
    })
    assert d.stage == m.STAGE_ABSENT and d.pick is not None and d.pick.scope == "workload"


def test_disabled_instance_before_manual_workload() -> None:
    d = m.decide_from_map({
        "instance": _rec(m.ORIGIN_MANUAL, m.STATE_DISABLED, targets=[]),
        "workload": _rec(m.ORIGIN_MANUAL, rid=2),
    })
    assert d.stage == m.STAGE_ABSENT and d.pick is not None and d.pick.scope == "instance"


def test_auto_instance_is_stage2() -> None:
    d = m.decide_from_map({"instance": _rec(), "workload": _rec(rid=2)})
    assert d.stage == m.STAGE_AUTO_INSTANCE and d.pick is not None and d.pick.scope == "instance"


def test_auto_workload_and_domain_are_deferred_stage4() -> None:
    d = m.decide_from_map({"workload": _rec(rid=2), "domain": _rec(rid=3)})
    assert d.stage == m.STAGE_MISS and d.pick is None
    assert d.deferred is not None and d.deferred.scope == "workload"
    d = m.decide_from_map({"domain": _rec(rid=3)})
    assert d.deferred is not None and d.deferred.scope == "domain"


def test_ambiguous_and_failed_are_not_used_but_reported() -> None:
    d = m.decide_from_map({
        "instance": _rec(state=m.STATE_AMBIGUOUS), "workload": _rec(rid=2),
    })
    assert d.stage == m.STAGE_MISS
    assert d.deferred is not None and d.deferred.scope == "workload"
    assert d.reason == m.REASON_AMBIGUOUS
    d = m.decide_from_map({"domain": _rec(state=m.STATE_FAILED)})
    assert d.stage == m.STAGE_MISS and d.deferred is None and d.reason == m.REASON_FAILED


def test_target_gone_and_non_sms_targets_excluded() -> None:
    d = m.decide_from_map({"instance": _rec(targets=[_t("gone01", target_gone=True)])})
    assert d.stage == m.STAGE_MISS and d.reason == m.REASON_FAILED
    mixed = [_t("dpm01", kind=m.KIND_DPM), _t("gone01", target_gone=True), _t("ok01")]
    d = m.decide_from_map({"instance": _rec(m.ORIGIN_MANUAL, targets=mixed)})
    assert d.pick is not None
    assert [t["server_name"] for t in d.pick.targets] == ["ok01"]


def test_targets_capped_at_max() -> None:
    many = [_t(f"n{i:02d}") for i in range(12)]
    d = m.decide_from_map({"instance": _rec(targets=many)})
    assert d.pick is not None and len(d.pick.targets) == m.DEFAULT_MAX_TARGETS
    d = m.decide_from_map({"instance": _rec(targets=many)}, max_targets=3)
    assert d.pick is not None and len(d.pick.targets) == 3


def test_empty_lookup_is_plain_miss() -> None:
    d = m.decide_from_map({})
    assert d == m.MapDecision(stage=m.STAGE_MISS)


# ── 쓰기 규칙 (§3.5 일괄 생성 표 · 되적재) ─────────────────────────────
def _prop(targets: list[dict[str, Any]], state: str = m.STATE_ACTIVE) -> m.AutoProposal:
    return m.AutoProposal(
        domain_id="7", scope=m.SCOPE_INSTANCE, match_key="web01", basis=m.BASIS_VM_EXACT,
        state=state, targets=tuple(targets), evidence={"stage": "vm_exact"},
    )


def test_write_new_row_is_insert() -> None:
    w = m.plan_auto_write(None, _prop([_t("a")]), now_iso="T")
    assert w.action == m.WRITE_INSERT
    assert w.basis == m.BASIS_VM_EXACT and w.evidence == {"stage": "vm_exact"}


def test_write_disabled_is_skip() -> None:
    w = m.plan_auto_write(_rec(m.ORIGIN_MANUAL, m.STATE_DISABLED, targets=[]),
                          _prop([_t("a")]), now_iso="T")
    assert w.action == m.WRITE_SKIP and w.state == m.STATE_DISABLED and w.targets == ()


@pytest.mark.parametrize("existing", [
    _rec(m.ORIGIN_MANUAL, targets=[_t("x")]),
    _rec(verified=True, targets=[_t("x")]),
])
def test_write_admin_rows_keep_targets_and_suggest(existing: dict[str, Any]) -> None:
    same = m.plan_auto_write(existing, _prop([_t("X")]), now_iso="T")  # 대소문자 무시 동일
    assert same.action == m.WRITE_SKIP
    w = m.plan_auto_write(existing, _prop([_t("y")]), now_iso="T")
    assert w.action == m.WRITE_SUGGEST
    assert [t["server_name"] for t in w.targets] == ["x"]  # 대상 불변
    assert w.evidence["scan_suggests"]["targets"][0]["server_name"] == "y"
    assert w.evidence["scan_suggests"]["at"] == "T"


def test_write_auto_same_and_changed() -> None:
    existing = _rec(targets=[_t("x")])
    assert m.plan_auto_write(existing, _prop([_t("x")]), now_iso="T").action == m.WRITE_SAME
    w = m.plan_auto_write(existing, _prop([_t("y")]), now_iso="T")
    assert w.action == m.WRITE_UPDATE
    assert [t["server_name"] for t in w.targets] == ["y"]
    assert w.evidence["previous_targets"][0]["server_name"] == "x"
    assert w.evidence["changed"] is True and w.evidence["changed_at"] == "T"
    # state 변화도 「변경」이다
    w = m.plan_auto_write(existing, _prop([_t("x")], m.STATE_AMBIGUOUS), now_iso="T")
    assert w.action == m.WRITE_UPDATE and w.state == m.STATE_AMBIGUOUS


def test_next_retry_only_for_failed_and_ambiguous() -> None:
    assert m.next_retry_at(m.STATE_FAILED, NOW, 24) == NOW + timedelta(hours=24)
    assert m.next_retry_at(m.STATE_AMBIGUOUS, NOW, 2) == NOW + timedelta(hours=2)
    assert m.next_retry_at(m.STATE_ACTIVE, NOW, 24) is None


# ── 되적재 병합 ─────────────────────────────────────────────────────────
def test_learn_pod_writes_instance_and_workload_union() -> None:
    existing_wl = _rec(targets=[_t("node01"), _t("node02")])
    props = m.learn_proposals(
        domain_id="7", instance_name=POD, targets=[_t("node03")],
        basis=m.BASIS_NODE_ADDRESS, existing_workload=existing_wl, domain_name="결제",
    )
    assert [(p.scope, p.match_key) for p in props] == [("instance", POD), ("workload", WORKLOAD)]
    assert [t["server_name"] for t in props[0].targets] == ["node03"]
    assert [t["server_name"] for t in props[1].targets] == ["node01", "node02", "node03"]
    assert props[1].instance_example == POD and props[1].domain_name == "결제"


def test_learn_workload_union_drops_oldest_over_cap() -> None:
    existing_wl = _rec(targets=[_t(f"n{i}") for i in range(3)])
    props = m.learn_proposals(
        domain_id="7", instance_name=POD, targets=[_t("n1"), _t("new")],
        basis=m.BASIS_NODE_ADDRESS, existing_workload=existing_wl, max_targets=3,
    )
    assert [t["server_name"] for t in props[1].targets] == ["n2", "n1", "new"]


def test_learn_vm_writes_instance_only_and_empty_is_noop() -> None:
    props = m.learn_proposals(domain_id="7", instance_name="web01", targets=[_t("web01")],
                              basis=m.BASIS_VM_EXACT)
    assert [p.scope for p in props] == ["instance"]
    assert m.learn_proposals(domain_id="7", instance_name="web01", targets=[],
                             basis=m.BASIS_VM_EXACT) == []


# ── 스캔 대조 — target_gone · 정리 ──────────────────────────────────────
def test_mark_targets_gone_only_in_scanned_dbs() -> None:
    targets = [_t("a", "pg_a"), _t("b", "pg_b"), _t("c", "pg_a", target_gone=True)]
    new = m.mark_targets_gone(targets, present={("pg_a", "c")}, scanned_db_ids={"pg_a"})
    assert new is not None
    assert [(t["server_name"], t["target_gone"]) for t in new] == [
        ("a", True), ("b", False), ("c", False),  # pg_b는 미확인 → 손대지 않음 · c는 재등장
    ]
    assert m.mark_targets_gone([_t("a")], present={("pg_a", "a")}, scanned_db_ids={"pg_a"}) is None


def test_purge_reason_rules() -> None:
    old = (NOW - timedelta(days=31)).isoformat()
    ancient = (NOW - timedelta(days=181)).isoformat()
    inst = {**_rec(), "scope": "instance", "created_at": old, "last_seen_at": old}
    assert m.purge_reason(inst, NOW) == m.PURGE_INSTANCE_IDLE
    recent = {**inst, "last_used_at": NOW.isoformat()}
    assert m.purge_reason(recent, NOW) is None
    wl = {**_rec(), "scope": "workload", "created_at": ancient, "last_seen_at": ancient}
    assert m.purge_reason(wl, NOW) == m.PURGE_UNSEEN
    assert m.purge_reason({**wl, "last_seen_at": old}, NOW) is None
    assert m.purge_reason({**wl, "origin": m.ORIGIN_MANUAL}, NOW) is None
    assert m.purge_reason({**wl, "verified": True}, NOW) is None


# ── VM 정확 일치 · SMS 색인 ─────────────────────────────────────────────
def test_vm_exact_match_one_server_only() -> None:
    a, b = _t("a"), _t("b")
    one = m.vm_exact_match([a], [dict(a, hostname="A")])  # IP·hostname 둘 다 같은 서버
    assert one.status == "linked" and one.state == m.STATE_ACTIVE and len(one.targets) == 1
    two = m.vm_exact_match([a], [b])
    assert two.status == "ambiguous" and two.state == m.STATE_AMBIGUOUS and len(two.targets) == 2
    assert m.vm_exact_match([a, b], None).status == "ambiguous"
    none = m.vm_exact_match(None, [])
    assert none.status == "none" and none.state is None


def test_build_sms_index_ip_nic_host() -> None:
    rows = [
        {"db_id": "pg_a", "server_name": "web01", "hostname": "web01.corp.local",
         "ip": "192.0.2.10", "nic_ips": ["192.0.2.11", "bad-ip"], "resource_id": "r1"},
        {"db_id": "pg_b", "server_name": "web02", "hostname": "web02",
         "ip": "192.0.2.10", "nic_ips": None, "resource_id": "r2"},
        {"db_id": "", "server_name": "skip", "ip": "192.0.2.99"},
    ]
    by_ip, by_host = m.build_sms_index(rows)
    assert sorted(by_ip) == ["192.0.2.10", "192.0.2.11"]
    assert [s["server_name"] for s in by_ip["192.0.2.10"]] == ["web01", "web02"]
    assert by_ip["192.0.2.11"][0]["resource_id"] == "r1"
    assert sorted(by_host) == ["web01", "web02"]
    assert m.vm_exact_match(by_ip["192.0.2.10"], None).status == "ambiguous"
    assert m.vm_exact_match(by_ip["192.0.2.11"], by_host["web01"]).status == "linked"


# ── 힌트 계약 (§3.7) ────────────────────────────────────────────────────
def test_linked_hint_single_target() -> None:
    rec = _rec(targets=[_t("node01", resource_id="r9")], rid=42)
    d = m.decide_from_map({"instance": rec})
    assert d.pick is not None
    hint = m.linked_hint(d.pick, at="2026-11-03T09:00:00+00:00")
    assert hint == {
        "status": "linked", "db_id": "pg_a", "server_name": "node01", "hostname": "node01",
        "basis": "apm_map", "at": "2026-11-03T09:00:00+00:00", "map_origin": "auto",
        "map_scope": "instance", "map_id": 42, "map_basis": "node_address",
        "resource_id": "r9", "evidence_text": "APM↔SMS 연계 — 자동 · 추정(노드 주소 정확 일치)",
    }


def test_linked_hint_multi_targets_first_is_top_level() -> None:
    rec = _rec(m.ORIGIN_MANUAL, targets=[_t("n1"), _t("n2", "pg_b"), _t("n3")],
               basis=m.BASIS_MANUAL)
    rec["evidence_text"] = "사본 근거"
    d = m.decide_from_map({"workload": rec})
    assert d.pick is not None
    hint = m.linked_hint(d.pick, at="T")
    assert (hint["db_id"], hint["server_name"]) == ("pg_a", "n1")
    assert [t["server_name"] for t in hint["targets"]] == ["n1", "n2", "n3"]
    assert set(hint["targets"][0]) == {"db_id", "server_name", "hostname", "resource_id"}
    assert hint["map_origin"] == "manual" and hint["evidence_text"] == "사본 근거"


def test_absent_and_unresolved_hints() -> None:
    assert m.absent_hint() == {"status": "absent", "basis": "apm_map"}
    assert m.unresolved_hint(m.REASON_PENDING) == {
        "status": "unresolved", "reason": "apm_map_pending", "no_config_fallback": True,
    }
    assert m.unresolved_hint(m.REASON_NO_INVENTORY, node_address_status="disabled") == {
        "status": "unresolved", "reason": "apm_map_no_inventory", "no_config_fallback": True,
        "node_address_status": "disabled",
    }


def test_evidence_text_and_cache_value() -> None:
    assert "자동 · 추정" in m.evidence_text(m.ORIGIN_AUTO, m.BASIS_ITAM)
    assert "관리자 확인" in m.evidence_text(m.ORIGIN_AUTO, m.BASIS_ITAM, verified=True)
    assert "관리자 등록" in m.evidence_text(m.ORIGIN_MANUAL, m.BASIS_MANUAL)
    row = {**_rec(rid=5), "source_id": "jen", "evidence": {"x": 1}, "note": "n"}
    value = m.cache_value(row)
    assert set(value) == {
        "id", "origin", "basis", "state", "verified", "targets", "evidence_text", "version",
    }
    assert value["id"] == 5 and value["targets"][0]["server_name"] == "node01"


def test_request_messages() -> None:
    msg = m.learn_message(source_id="jen", domain_id="7", instance_name=POD,
                          basis=m.BASIS_NODE_ADDRESS, targets=[_t("n1")])
    assert msg["type"] == m.MSG_LEARN and msg["state"] == m.STATE_ACTIVE
    assert msg["targets"][0]["server_name"] == "n1"
    assert m.touch_message(source_id="jen", map_ids=[3, 4]) == {
        "type": "touch", "source_id": "jen", "ids": [3, 4],
    }
    assert m.itam_request_message(source_id="jen", domain_id="7", domain_name="결제") == {
        "type": "itam_request", "source_id": "jen", "domain_id": "7", "domain_name": "결제",
    }
