"""plans/215 W1 — 보조 신호 S8~S12 묶음 · 전파형 후보 · 신호 순도 보정.

수용 28·29·31 · §14 S8~S12 행.
"""

from __future__ import annotations

from dataclasses import replace

from noise_gate.domain import biz_service as bs
from noise_gate.domain import biz_service_aux as aux
from noise_gate.domain import biz_service_model as m
from noise_gate.domain.biz_service_keys import sms_member_key

GENERIC = frozenset({"운영"})


def srv(name: str, *, ip: str = "", zone: str = "z1", db: str = "pg_a") -> m.SmsServer:
    return m.SmsServer(db, name.split("(")[0], name, ip=ip, zone=zone)


def key(s: m.SmsServer) -> str:
    return sms_member_key(s.db_id, s.resource_id) or ""


def on(signal: str, zone: str = "z1", purity: float | None = 1.0) -> m.SignalStatus:
    return m.SignalStatus(signal, zone, True, purity, 5, 5)


def bundle(signal: str, *refs: m.SmsServer, center: m.SmsServer | None = None) -> aux.AuxBundle:
    return aux.AuxBundle(signal, "z1", "b1", frozenset(s.ref for s in refs),
                         center.ref if center else None)


A1, A2, B1, X = srv("a1(카드)"), srv("a2(카드)"), srv("b1(대출)"), srv("x1")


# ── 전파 규칙(§3.2.3 1~3 · 수용 28) ────────────────────────────────────
def test_two_anchors_same_service_make_candidate_not_member() -> None:
    anchors = {A1.ref: "카드", A2.ref: "카드"}
    out = aux.propagate_candidates(
        [bundle(m.SIG_REGISTERED_BATCH, A1, A2, X)], anchors=anchors, eligible=[X.ref],
        status={(m.SIG_REGISTERED_BATCH, "z1"): on(m.SIG_REGISTERED_BATCH)}, min_anchors=2)
    assert [(c.ref, c.service_key, c.signal.basis, c.signal.anchors) for c in out.candidates] == [
        (X.ref, "카드", "aux:registered_batch", 2)]
    assert not out.shared


def test_split_anchors_shared_and_single_anchor_nothing() -> None:
    st = {(m.SIG_REGISTERED_BATCH, "z1"): on(m.SIG_REGISTERED_BATCH)}
    split = aux.propagate_candidates(
        [bundle(m.SIG_REGISTERED_BATCH, A1, B1, X)], anchors={A1.ref: "카드", B1.ref: "대출"},
        eligible=[X.ref], status=st, min_anchors=2)
    assert split.candidates == () and split.shared == {(m.SIG_REGISTERED_BATCH, "z1"): 1}
    single = aux.propagate_candidates(
        [bundle(m.SIG_REGISTERED_BATCH, A1, X)], anchors={A1.ref: "카드"}, eligible=[X.ref],
        status=st, min_anchors=2)
    assert single.candidates == () and not single.shared


def test_disabled_signal_bundles_are_not_used() -> None:
    off = m.SignalStatus(m.SIG_IP_SUBNET, "z1", False, reason=m.REASON_LOW_PURITY)
    out = aux.propagate_candidates(
        [bundle(m.SIG_IP_SUBNET, A1, A2, X)], anchors={A1.ref: "카드", A2.ref: "카드"},
        eligible=[X.ref], status={(m.SIG_IP_SUBNET, "z1"): off}, min_anchors=2)
    assert out.candidates == ()


def test_neighbor_bundle_only_center_gets_candidate() -> None:
    out = aux.propagate_candidates(
        [bundle(m.SIG_AVAIL_DEPENDENCY, X, A1, A2, center=X)],
        anchors={A1.ref: "카드", A2.ref: "카드"}, eligible=[X.ref, A1.ref],
        status={(m.SIG_AVAIL_DEPENDENCY, "z1"): on(m.SIG_AVAIL_DEPENDENCY)}, min_anchors=2)
    assert [c.ref for c in out.candidates] == [X.ref]


# ── 순도(§3.2.3 4 · 수용 29) ───────────────────────────────────────────
def _bundles(pure: int, mixed: int) -> tuple[list[frozenset[m.ServerRef]], dict[m.ServerRef, str]]:
    bundles: list[frozenset[m.ServerRef]] = []
    confirmed: dict[m.ServerRef, str] = {}
    for i in range(pure + mixed):
        a, b = ("pg", f"p{i}a"), ("pg", f"p{i}b")
        confirmed[a] = f"s{i}"
        confirmed[b] = f"s{i}" if i < pure else f"t{i}"
        bundles.append(frozenset({a, b}))
    return bundles, confirmed


def test_purity_low_turns_signal_off_with_reason() -> None:
    bundles, confirmed = _bundles(pure=3, mixed=7)
    st = aux.signal_status(m.SIG_IP_SUBNET, "z1", config=m.BizServiceConfig(), bundles=bundles,
                           confirmed=confirmed)
    assert (st.enabled, st.purity, st.evaluated, st.reason) == (False, 0.3, 10,
                                                                m.REASON_LOW_PURITY)
    assert "순도 0.30" in st.reason_text


def test_purity_few_groups_reason_distinct() -> None:
    bundles, confirmed = _bundles(pure=4, mixed=0)
    st = aux.signal_status(m.SIG_IP_SUBNET, "z1", config=m.BizServiceConfig(), bundles=bundles,
                           confirmed=confirmed)
    assert (st.enabled, st.evaluated, st.reason) == (False, 4, m.REASON_FEW_GROUPS)
    assert "평가 부족" in st.reason_text
    bundles5, confirmed5 = _bundles(pure=5, mixed=0)
    assert aux.signal_status(m.SIG_IP_SUBNET, "z1", config=m.BizServiceConfig(),
                             bundles=bundles5, confirmed=confirmed5).enabled is True


def test_status_config_and_attr_failure_reasons() -> None:
    cfg = m.BizServiceConfig(disabled_reasons={m.SIG_TREE_GROUP: "min_share 오류"})
    assert aux.signal_status(m.SIG_TREE_GROUP, "z1", config=cfg, bundles=[], confirmed={}
                             ).reason == m.REASON_CONFIG_INVALID
    assert aux.signal_status(m.SIG_ITAM_CONTRACT, "z1", config=m.BizServiceConfig(), bundles=[],
                             confirmed={}).reason == m.REASON_CONFIG_OFF
    fail = aux.signal_status(
        m.SIG_TREE_GROUP, "z1", config=m.BizServiceConfig(), bundles=[], confirmed={},
        attr_failures=[m.AttrFailure(m.SIG_TREE_GROUP, "폴더 SELECT 실패")])
    assert fail.reason == m.REASON_ATTR_FAILED and "폴더 SELECT 실패" in fail.reason_text


# ── 묶음 키 추출(§14 S8~S12) ───────────────────────────────────────────
def _attr(s: m.SmsServer, **kw: object) -> m.ServerAttr:
    return m.ServerAttr(s.db_id, s.resource_id, **kw)  # type: ignore[arg-type]


def test_s8_gap_chain_and_mass_batch_excluded() -> None:
    pair = [srv("h1"), srv("h2")]
    attrs = [_attr(pair[0], registered_at_ms=1574822258074),
             _attr(pair[1], registered_at_ms=1574822259602)]
    mass = [srv(f"bulk{i:02d}") for i in range(30)]
    attrs += [_attr(s, registered_at_ms=1767200000000 + i) for i, s in enumerate(mass)]
    built = aux.build_aux_bundles([*pair, *mass], attrs, [], m.AuxSignalsConfig())
    s8 = [b for b in built.bundles if b.signal == m.SIG_REGISTERED_BATCH]
    assert [b.refs for b in s8] == [frozenset(s.ref for s in pair)]
    assert built.notes == {"mass_registered_batches": 1}


def test_s9_subnet_key_rules() -> None:
    assert aux.ipv4_subnet_key("10.0.1.7", 24) == "10.0.1.0/24"
    assert aux.ipv4_subnet_key("", 24) == ""
    assert aux.ipv4_subnet_key("fe80::1", 24) == ""
    assert aux.ipv4_subnet_key("10.9.1.7", 24, ("10.9.0.0/16",)) == ""


def test_s9_web_subnet_with_many_businesses_is_shared_and_off() -> None:
    webs = [srv(f"web{i:02d}(업무{i % 5})", ip=f"10.0.1.{i + 1}") for i in range(10)]
    x = srv("webxx", ip="10.0.1.200")
    cfg = m.BizServiceConfig(aux=m.AuxSignalsConfig(min_groups=1),
                             host_prefix=m.HostPrefixConfig(enabled=False))
    p = bs.compute_proposal(m.ProposalInputs(servers=(*webs, x), generic_terms=GENERIC), cfg)
    s9 = next(s for s in p.signal_status if s.signal == m.SIG_IP_SUBNET)
    assert s9.enabled is False and s9.reason == m.REASON_LOW_PURITY
    assert not [c for c in p.candidates if c.target_key == key(x)
                and any(sig.basis == "aux:ip_subnet" for sig in c.signals)]


def test_s11_burst_minute_not_counted() -> None:
    hosts = [srv(f"n{i:02d}") for i in range(40)]
    minutes = [m.AlarmMinute(s.db_id, s.resource_id, 100) for s in hosts]
    minutes += [m.AlarmMinute(h.db_id, h.resource_id, t) for t in (1, 2, 3) for h in hosts[:2]]
    built = aux.build_aux_bundles(hosts, [], minutes, m.AuxSignalsConfig())
    s11 = [b for b in built.bundles if b.signal == m.SIG_ALARM_COOCCURRENCE]
    assert {b.refs for b in s11} == {frozenset({hosts[0].ref, hosts[1].ref})}
    assert built.notes["burst_minutes"] == 1


def test_s12_dependency_on_non_server_is_not_neighbor() -> None:
    a, b = srv("d1"), srv("d2")
    attrs = [_attr(a, depends=("icmp-node-9",)), _attr(b, depends=(a.resource_id,))]
    built = aux.build_aux_bundles([a, b], attrs, [], m.AuxSignalsConfig())
    s12 = [x for x in built.bundles if x.signal == m.SIG_AVAIL_DEPENDENCY]
    assert {x.refs for x in s12} == {frozenset({a.ref, b.ref})}


# ── 통합(compute_proposal) ─────────────────────────────────────────────
def test_two_aux_signals_same_target_one_candidate_two_signals_first() -> None:
    card = [srv("c1(카드)", ip="10.5.5.1"), srv("c2(카드)", ip="10.5.5.2")]
    x = srv("zz1", ip="10.5.5.3")
    y = srv("zz2", ip="10.6.6.3")
    anchor_y = [srv("l1(대출)", ip="10.6.6.1"), srv("l2(대출)", ip="10.6.6.2")]
    attrs = [_attr(s, registered_at_ms=5_000_000 + i) for i, s in enumerate([*card, x])]
    cfg = m.BizServiceConfig(aux=m.AuxSignalsConfig(min_groups=1),
                             host_prefix=m.HostPrefixConfig(enabled=False))
    p = bs.compute_proposal(m.ProposalInputs(
        servers=(*card, x, y, *anchor_y), attrs=tuple(attrs), generic_terms=GENERIC), cfg)
    member_c = [c for c in p.candidates if c.kind == m.CAND_MEMBER]
    assert member_c[0].target_key == key(x) and member_c[0].service_key == "카드"
    assert {s.basis for s in member_c[0].signals} == {"aux:registered_batch", "aux:ip_subnet"}
    assert any(c.target_key == key(y) and len(c.signals) == 1 for c in member_c)
    assert key(x) not in {mm.member_key for mm in p.members}  # 후보일 뿐 구성원이 아니다


def test_excluded_member_not_anchor_and_not_reproposed() -> None:
    card = [srv("c1(카드)", ip="10.5.5.1"), srv("c2(카드)", ip="10.5.5.2")]
    x = srv("zz1", ip="10.5.5.3")
    stored = m.StoredState(services=(m.StoredService(
        7, "카드", "카드", members=(m.StoredMember("sms_server", key(x),
                                                  state=m.MEMBER_EXCLUDED),)),))
    cfg = m.BizServiceConfig(aux=m.AuxSignalsConfig(min_groups=1),
                             host_prefix=m.HostPrefixConfig(enabled=False))
    p = bs.compute_proposal(m.ProposalInputs(
        servers=(*card, x), generic_terms=GENERIC, stored=stored), cfg)
    assert not [c for c in p.candidates if c.target_key == key(x)]


def test_attr_failure_only_turns_s7_off() -> None:
    """수용 31 — 트리 폴더 보강 읽기 실패 → S7만 꺼짐 · 나머지 계산 정상 · 사유 노출."""
    card = [srv("c1(카드)"), srv("c2(카드)")]
    cfg = replace(m.BizServiceConfig(), aux=m.AuxSignalsConfig(min_groups=1))
    p = bs.compute_proposal(m.ProposalInputs(
        servers=tuple(card), generic_terms=GENERIC,
        attr_failures=(m.AttrFailure(m.SIG_TREE_GROUP, "timeout"),)), cfg)
    s7 = [s for s in p.signal_status if s.signal == m.SIG_TREE_GROUP]
    assert s7 and all(not s.enabled and s.reason == m.REASON_ATTR_FAILED for s in s7)
    assert {mm.service_key for mm in p.members} == {"카드"}
    assert p.summary["overall"]["attr_failures"] == [
        {"signal": m.SIG_TREE_GROUP, "zone": None, "reason": "timeout"}]
