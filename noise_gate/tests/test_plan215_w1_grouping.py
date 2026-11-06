"""plans/215 W1 — 축 ① S1~S7 합치기 · S4·S5 후보 · 축 ③ APM 도출 · 분석 지표 · 결정성.

§14 테스트 벡터(도메인 행)와 §13 수용 기준 1~3·5~7·27·32를 그대로 옮겼다.
"""

from __future__ import annotations

import random
from dataclasses import replace
from typing import Any

from noise_gate.domain import biz_service as bs
from noise_gate.domain import biz_service_model as m
from noise_gate.domain.biz_service_keys import apm_instance_member_key, sms_member_key
from noise_gate.domain.biz_service_signals import extract_prefix_code

GENERIC = frozenset({"운영", "개발", "서버", "테스트", "web", "was"})
#: 작은 픽스처라 평가 묶음 기준만 1로 낮춘다(순도 기준 0.8은 그대로).
CFG = m.BizServiceConfig(aux=m.AuxSignalsConfig(min_groups=1))
NO_S6 = replace(CFG, host_prefix=m.HostPrefixConfig(enabled=False))


def srv(name: str, rid: str | None = None, *, db: str = "pg_a", host: str = "", ip: str = "",
        zone: str = "z1") -> m.SmsServer:
    return m.SmsServer(db, rid or name.split("(")[0].strip(), name, host, ip, zone=zone)


def key(s: m.SmsServer) -> str:
    return sms_member_key(s.db_id, s.resource_id) or ""


def run(servers: list[m.SmsServer], config: m.BizServiceConfig = CFG, **kw: Any) -> m.Proposal:
    return bs.compute_proposal(
        m.ProposalInputs(servers=tuple(servers), generic_terms=GENERIC, **kw), config)


def svc_of(p: m.Proposal) -> dict[str, str]:
    return {mm.member_key: mm.service_key for mm in p.members if mm.kind == "sms_server"}


def member(p: m.Proposal, s: m.SmsServer) -> m.ProposedMember:
    return next(mm for mm in p.members if mm.member_key == key(s))


def cands(p: m.Proposal, kind: str = m.CAND_MEMBER) -> list[m.Candidate]:
    return [c for c in p.candidates if c.kind == kind]


IBK = [srv("ibkweb01(인터넷뱅킹)"), srv("ibkwas01(인터넷뱅킹)"), srv("ibkdb01 (인터넷 뱅킹)")]
#: S6 순도 0.8 유지용 — 코드가 업무 하나에만 대응하는 묶음 4개(불순 묶음 1개까지 견딘다).
PURE = [srv(f"{c}{r}0{i}({c.upper()}업무)") for c in ("aaa", "bbb", "ccc", "ddd")
        for r, i in (("web", 1), ("was", 2))]


# ── S1 ──────────────────────────────────────────────────────────────────
def test_s1_same_business_one_service_confirmed() -> None:
    p = run(IBK)
    assert {v for v in svc_of(p).values()} == {"인터넷뱅킹"}
    assert all(mm.confidence == m.CONF_CONFIRMED and mm.basis == m.SIG_BIZ_SUFFIX
               for mm in p.members)
    assert [s.service_key for s in p.services] == ["인터넷뱅킹"]


def test_s4_candidate_for_tailless_server_without_s6() -> None:
    """수용 2 — 꼬리·업무코드 없는 서버는 구성원이 아니라 후보(S4 stem)."""
    x = srv("ibkweb02")
    p = run([*IBK, x], NO_S6)
    assert key(x) not in svc_of(p)
    c = cands(p)
    assert [(c0.target_key, c0.service_key, c0.signals[0].basis) for c0 in c] == [
        (key(x), "인터넷뱅킹", m.SIG_HOST_STEM)]


def test_generic_business_name_is_not_a_signal() -> None:
    p = run([srv("srv01(운영)"), srv("srv02(운영)")])
    assert svc_of(p) == {} and p.services == ()


# ── S2 · S3 ─────────────────────────────────────────────────────────────
def _map(mid: int, targets: list[m.SmsServer], *, scope: str = "instance", mk: str = "inst1",
         origin: str = "auto", verified: bool = False, state: str = "active",
         domain: str = "10") -> m.ApmMapRow:
    return m.ApmMapRow(
        mid, "jen", domain, scope, mk, state, origin, verified,
        tuple({"kind": "sms", "db_id": t.db_id, "server_name": t.server_name,
               "resource_id": t.resource_id, "target_gone": False} for t in targets),
        domain_name="IBK도메인", instance_example=mk,
    )


def test_s2_bridging_two_strong_services_is_conflict() -> None:
    """수용 3 — S2가 결제·인터넷뱅킹 서버를 이으면 잇지 않고 충돌."""
    pay = srv("pay01(결제)")
    p = run([*IBK, pay], apm_maps=(_map(1, [IBK[1], pay]),))
    assert svc_of(p)[key(pay)] == "결제"
    assert svc_of(p)[key(IBK[1])] == "인터넷뱅킹"
    sms = [c for c in p.conflicts if c.kind == m.CONFLICT_SMS]
    assert len(sms) == 1 and sms[0].service_keys == ("결제", "인터넷뱅킹")


def test_s2_attaches_tailless_to_single_strong_service() -> None:
    x = srv("zzzhost77")
    p = run([*IBK, x], NO_S6, apm_maps=(_map(1, [IBK[1], x]),))
    assert member(p, x).service_key == "인터넷뱅킹"
    assert member(p, x).basis == m.SIG_APM_DOMAIN and member(p, x).confidence == m.CONF_INFERRED


def test_s3_itam_clue_one_match_member() -> None:
    x = srv("ibkweb03")
    p = run([*IBK, x], NO_S6,
            itam_apps=(m.ItamAppRow("IBK", "인터넷뱅킹"),),
            itam_servers=(m.ItamServerRow("IBK-WEB-03", clue_text="인터넷뱅킹 웹"),))
    mm = member(p, x)
    assert (mm.service_key, mm.basis, mm.confidence) == ("인터넷뱅킹", m.SIG_ITAM,
                                                         m.CONF_INFERRED)


def test_s3_itam_two_matches_not_linked() -> None:
    a, b = srv("ibkweb04", db="pg_a"), srv("ibkweb04", db="pg_b")
    p = run([*IBK, a, b], NO_S6,
            itam_apps=(m.ItamAppRow("IBK", "인터넷뱅킹"),),
            itam_servers=(m.ItamServerRow("IBK-WEB-04", clue_text="인터넷뱅킹"),))
    assert key(a) not in svc_of(p) and key(b) not in svc_of(p)


# ── S5 ──────────────────────────────────────────────────────────────────
def test_s5_merge_candidate_by_name_tokens() -> None:
    p = run([*IBK, srv("ibs01(인터넷뱅킹시스템)"), srv("ibs02(인터넷뱅킹시스템)")], NO_S6)
    merge = cands(p, m.CAND_MERGE)
    assert [(c.service_key, c.target_key) for c in merge] == [("인터넷뱅킹", "인터넷뱅킹시스템")]


# ── S6 ──────────────────────────────────────────────────────────────────
def test_s6_learned_code_attaches_tailless() -> None:
    """수용 6 — 꼬리 없는 `ibkdb02`가 코드 대응으로 `inferred` · `enabled: false`면 기여 0."""
    x = srv("ibkdb02")
    base = [srv("ibkweb01(인터넷뱅킹)"), srv("ibkwas01(인터넷뱅킹)"), x]
    mm = member(run(base), x)
    assert (mm.service_key, mm.basis, mm.confidence, mm.prefix_code) == (
        "인터넷뱅킹", m.SIG_HOST_PREFIX, m.CONF_INFERRED, "ibk")
    assert key(x) not in svc_of(run(base, NO_S6))


def test_s6_new_prefix_service_and_itam_name() -> None:
    crd = [srv("crdap01"), srv("crdap02"), srv("crddb01")]
    p = run([*IBK, *crd])
    svc = {s.service_key: s for s in p.services}
    assert svc["prefix:crd"].display_name == "CRD"
    assert {svc_of(p)[key(s)] for s in crd} == {"prefix:crd"}
    assert all(member(p, s).confidence == m.CONF_INFERRED for s in crd)
    p2 = run([*IBK, *crd], itam_apps=(m.ItamAppRow("CRD", "신용카드"),))
    svc2 = {s.service_key: s for s in p2.services}
    assert svc2["itam:crd"].display_name == "신용카드"


def test_s6_single_server_code_makes_no_service() -> None:
    x = srv("crdap09")
    p = run([*IBK, x])
    assert key(x) not in svc_of(p) and key(x) in p.unassigned


def test_s6_code_in_two_businesses_is_not_a_code() -> None:
    kbk = [srv("kbkweb01(인터넷뱅킹)"), srv("kbkwas01(인터넷뱅킹)"),
           srv("kbkweb11(기업뱅킹)"), srv("kbkwas11(기업뱅킹)")]
    x = srv("kbkapi99")
    p = run([*kbk, *PURE, x])
    assert key(x) not in svc_of(p)
    mine = [c for c in cands(p) if c.target_key == key(x)]
    assert {c.service_key for c in mine} == {"인터넷뱅킹", "기업뱅킹"}
    assert all(c.alt_count == 2 for c in mine)


def test_s6_s1_wins_with_prefix_mismatch_evidence() -> None:
    x = srv("ibkpay01(결제)")
    p = run([*IBK, x, srv("payap01(결제)"), srv("payap02(결제)"), *PURE])
    mm = member(p, x)
    assert mm.service_key == "결제" and mm.evidence.get("prefix_mismatch") == "인터넷뱅킹"


def test_s6_code_extraction_rules() -> None:
    hp = m.HostPrefixConfig()
    for name in ("web-01", "srv001", "10.1.2.3", "ib-web01", "a1bweb"):
        assert extract_prefix_code(srv(name), hp)[0] == "", name
    code, mismatch = extract_prefix_code(srv("crdweb07(카드)", host="ibkweb07"), hp)
    assert (code, mismatch) == ("ibk", True)


def test_s6_hostname_mismatch_keeps_s1_service() -> None:
    x = srv("crdweb07(카드)", host="ibkweb07")
    p = run([*IBK, x, srv("crdweb01(카드)"), srv("crdweb02(카드)")])
    mm = member(p, x)
    assert mm.service_key == "카드" and mm.prefix_code == "ibk"
    assert mm.evidence.get("host_mismatch") is True


def test_s6_zone_head_without_strip_is_not_a_code() -> None:
    """§14 — `cob0-` 머리를 떼지 않으면 `cob`가 여러 업무에 → 오묶음 0."""
    servers = [srv("cob0-kahapp01(AI상담)"), srv("cob0-bnndbp01(방카)"),
               srv("cob0-xyzapp01"), srv("cob0-xyzapp02")]
    p = run(servers)
    assert not any(k.startswith("prefix:") for k in {s.service_key for s in p.services})
    assert key(servers[2]) not in svc_of(p) and key(servers[3]) not in svc_of(p)


def test_s6_strip_prefix_pattern() -> None:
    """수용 32 — `^co(b0|cm)-`면 `cob0-kahapp01` → `kah`."""
    hp = m.HostPrefixConfig(strip_prefix_patterns=("^co(b0|cm)-",))
    assert extract_prefix_code(srv("cob0-kahapp01"), hp)[0] == "kah"
    assert extract_prefix_code(srv("cob0-bnndbp01"), hp)[0] == "bnn"
    assert extract_prefix_code(srv("cocm-hdkapp01"), hp)[0] == "hdk"


# ── S7 ──────────────────────────────────────────────────────────────────
def _attr(s: m.SmsServer, *folders: str, **kw: Any) -> m.ServerAttr:
    path = tuple(m.TreeFolder(f"f{i}-{name}", name) for i, name in enumerate(folders))
    return m.ServerAttr(s.db_id, s.resource_id, tree_path=path, **kw)


def test_s7_tree_folder_attaches_tailless_and_skips_generic() -> None:
    """수용 27 — S1 카드 3대 + 꼬리 없는 2대 → `inferred`(`tree_group`) · `운영` 건너뜀."""
    card = [srv("c1(카드)"), srv("c2(카드)"), srv("c3(카드)")]
    tail = [srv("zq1"), srv("zq2")]
    attrs = tuple(_attr(s, "센터A", "카드시스템", "운영") for s in card + tail)
    p = run(card + tail, NO_S6, attrs=attrs)
    for s in tail:
        mm = member(p, s)
        assert (mm.service_key, mm.basis, mm.confidence) == ("카드", m.SIG_TREE_GROUP,
                                                             m.CONF_INFERRED)


def test_s7_mixed_folder_is_not_a_signal() -> None:
    loose = replace(NO_S6, aux=m.AuxSignalsConfig(min_groups=1, min_purity=0.5))
    card = [srv("c1(카드)"), srv("c2(카드)"), srv("c3(카드)")]
    mix = [srv("k1(카드)"), srv("k2(카드)"), srv("l1(대출)"), srv("l2(대출)")]
    tail = srv("zq9")
    attrs = (*(_attr(s, "카드시스템") for s in card),
             *(_attr(s, "센터A") for s in [*mix, tail]))
    p = run([*card, *mix, tail], loose, attrs=attrs)
    assert key(tail) not in svc_of(p)


def test_s7_new_tree_service_with_code_and_itam() -> None:
    anchor = [srv("c1(카드)"), srv("c2(카드)")]
    kah = [srv("q1"), srv("q2"), srv("q3")]
    attrs = (*(_attr(s, "카드폴더") for s in anchor),
             *(_attr(s, "센터A", "AI 금융상담시스템(KAH)", "운영") for s in kah))
    p = run([*anchor, *kah], NO_S6, attrs=attrs)
    svc = {s.service_key: s for s in p.services}
    assert "tree:ai금융상담시스템(kah)" in svc
    assert svc["tree:ai금융상담시스템(kah)"].evidence["tree_code"] == "KAH"
    p2 = run([*anchor, *kah], NO_S6, attrs=attrs, itam_apps=(m.ItamAppRow("KAH", "AI상담"),))
    svc2 = {s.service_key: s for s in p2.services}
    assert svc2["itam:kah"].display_name == "AI상담"


def test_s7_tree_code_equals_prefix_code_badge_only() -> None:
    anchor = [srv("c1(카드)"), srv("c2(카드)")]
    kah = [srv("kahapp01"), srv("kahapp02")]
    attrs = (*(_attr(s, "카드폴더") for s in anchor),
             *(_attr(s, "AI 금융상담시스템(KAH)") for s in kah))
    p = run([*anchor, *kah], CFG, attrs=attrs)
    mm = member(p, kah[0])
    assert mm.confidence == m.CONF_INFERRED and mm.evidence["tree_code_matches_prefix"] is True


def test_s7_same_folder_name_across_zones_one_service() -> None:
    anchor = [srv("c1(카드)", zone="z1"), srv("c2(카드)", zone="z1"),
              srv("c3(카드)", zone="z2"), srv("c4(카드)", zone="z2")]
    ib = [srv("q1", zone="z1"), srv("q2", zone="z1"), srv("q3", zone="z2")]
    attrs = (*(_attr(s, "카드폴더") for s in anchor), *(_attr(s, "인터넷뱅킹") for s in ib))
    p = run([*anchor, *ib], NO_S6, attrs=attrs)
    assert {svc_of(p)[key(s)] for s in ib} == {"tree:인터넷뱅킹"}


# ── 상한 ────────────────────────────────────────────────────────────────
def test_member_cap_marks_ambiguous_no_auto_confirm() -> None:
    """수용 5."""
    p = run(IBK, replace(CFG, max_members=2))
    assert p.services[0].state == m.SVC_AMBIGUOUS
    assert all(mm.confidence == m.CONF_INFERRED for mm in p.members)


# ── 축 ③ ────────────────────────────────────────────────────────────────
POD = "ibk-was-7f9c-x2k1p"


def test_axis3_verified_confirmed_auto_inferred() -> None:
    """수용 7."""
    rows = (_map(1, [IBK[1]], mk=POD, verified=True), _map(2, [IBK[0]], mk="ibk-web-1"))
    p = run(IBK, apm_maps=rows)
    apm = {mm.member_key: mm for mm in p.members if mm.kind == "apm_instance"}
    k1 = apm_instance_member_key("jen", "10", "instance", POD) or ""
    k2 = apm_instance_member_key("jen", "10", "instance", "ibk-web-1") or ""
    assert (apm[k1].service_key, apm[k1].confidence, apm[k1].apm_map_id) == (
        "인터넷뱅킹", m.CONF_CONFIRMED, 1)
    assert apm[k2].confidence == m.CONF_INFERRED


def test_axis3_split_targets_is_apm_conflict() -> None:
    crd = srv("crdwas01(카드)")
    p = run([*IBK, crd], apm_maps=(_map(1, [IBK[1], crd], scope="workload", mk="ibk-was"),))
    assert not [mm for mm in p.members if mm.kind == "apm_instance"]
    apm = [c for c in p.conflicts if c.kind == m.CONFLICT_APM]
    assert len(apm) == 1 and apm[0].service_keys == ("인터넷뱅킹", "카드")


def test_axis3_same_domain_unlinked_candidate() -> None:
    rows = (_map(1, [IBK[0]], mk="i1"), _map(2, [IBK[1]], mk="i2"))
    inv = tuple(m.ApmInventoryRow("jen", "10", "IBK도메인", f"id{i}", f"i{i}") for i in (1, 2, 3))
    p = run(IBK, apm_maps=rows, apm_inventory=inv)
    assert len([mm for mm in p.members if mm.kind == "apm_instance"]) == 2
    assert not [mm for mm in p.members if mm.kind == "apm_domain"]
    un = cands(p, m.CAND_APM_UNLINKED)
    assert [(c.service_key, c.target_key) for c in un] == [
        ("인터넷뱅킹", apm_instance_member_key("jen", "10", "instance", "i3"))]


def test_axis3_domain_member_when_all_instances_linked() -> None:
    rows = (_map(1, [IBK[0]], mk="i1", verified=True), _map(2, [IBK[1]], mk="i2", verified=True))
    inv = tuple(m.ApmInventoryRow("jen", "10", "IBK도메인", f"id{i}", f"i{i}") for i in (1, 2))
    p = run(IBK, apm_maps=rows, apm_inventory=inv)
    dom = [mm for mm in p.members if mm.kind == "apm_domain"]
    assert len(dom) == 1 and dom[0].confidence == m.CONF_CONFIRMED


def test_axis3_inactive_or_gone_targets_ignored() -> None:
    gone = _map(1, [IBK[1]], mk="g1")
    gone = replace(gone, targets=(dict(gone.targets[0], target_gone=True),))
    p = run(IBK, apm_maps=(gone, _map(2, [IBK[0]], mk="g2", state="disabled")))
    assert not [mm for mm in p.members if mm.kind != "sms_server"]
    assert p.active_map_ids == frozenset({1})


# ── 계층 제안 ───────────────────────────────────────────────────────────
def test_layer_tokens_empty_means_unassigned() -> None:
    assert all(mm.layer is None for mm in run(IBK).members)
    p = run(IBK, replace(CFG, layer_tokens={"web": ("web",), "db": ("db",)}))
    assert member(p, IBK[0]).layer == "web" and member(p, IBK[1]).layer is None


# ── 저장 상태 반영 ──────────────────────────────────────────────────────
def test_rejected_candidate_not_reoutput() -> None:
    x = srv("ibkweb02")
    stored = m.StoredState(candidates=(
        m.StoredCandidate(m.CAND_MEMBER, "인터넷뱅킹", key(x), m.CAND_REJECTED),))
    assert not cands(run([*IBK, x], NO_S6, stored=stored))


# ── 분석 · 결정성 ───────────────────────────────────────────────────────
def test_analysis_summary_and_change() -> None:
    rows = (_map(1, [IBK[1]], mk=POD, verified=True),)
    p = run(IBK, apm_maps=rows)
    s = p.summary["services"]["인터넷뱅킹"]
    assert (s["sms_servers"], s["apm_linked_servers"], s["apm_missing_servers"],
            s["apm_instances"]) == (3, 1, 2, 1)
    assert p.summary["overall"]["unassigned_servers"] == 0
    assert p.summary["change"] == {"baseline": False}
    p2 = bs.compute_proposal(
        m.ProposalInputs(servers=tuple(IBK[:2]), generic_terms=GENERIC, apm_maps=rows), CFG,
        p.summary)
    assert p2.summary["change"]["services"]["인터넷뱅킹"]["sms_servers"] == -1


def _big_inputs() -> dict[str, Any]:
    servers = [*IBK, srv("ibkdb02"), srv("crdap01"), srv("crdap02"), srv("pay01(결제)"),
               srv("ibkweb02"), srv("zq1"), srv("zq2"), srv("c1(카드)"), srv("c2(카드)")]
    attrs = tuple(_attr(s, "카드폴더", registered_at_ms=1_000 + i)
                  for i, s in enumerate(servers[-4:]))
    return {
        "servers": servers,
        "apm_maps": [_map(1, [IBK[1], servers[6]]), _map(2, [IBK[0]], mk="ibk-web-1")],
        "attrs": list(attrs),
        "itam_apps": [m.ItamAppRow("CRD", "신용카드")],
    }


def test_deterministic_under_shuffle() -> None:
    """수용 1 — 같은 스냅샷 2회 → 차이 0 · 행 순서를 섞어도 동일."""
    base = _big_inputs()

    def build(seed: int | None) -> m.Proposal:
        data = {k: list(v) for k, v in base.items()}
        if seed is not None:
            rng = random.Random(seed)
            for v in data.values():
                rng.shuffle(v)
        return bs.compute_proposal(m.ProposalInputs(
            servers=tuple(data["servers"]), apm_maps=tuple(data["apm_maps"]),
            attrs=tuple(data["attrs"]), itam_apps=tuple(data["itam_apps"]),
            generic_terms=GENERIC), CFG)

    first = build(None)
    assert build(None) == first
    for seed in range(5):
        assert build(seed) == first
