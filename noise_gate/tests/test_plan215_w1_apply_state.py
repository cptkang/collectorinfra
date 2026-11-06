"""plans/215 W1 — §3.6 쓰기 규칙(적용 연산 목록) · §4.4 서비스 상태 판정(수용 4·11·30)."""

from __future__ import annotations

from datetime import UTC, datetime

from noise_gate.domain import biz_service as bs
from noise_gate.domain import biz_service_apply as ap
from noise_gate.domain import biz_service_model as m
from noise_gate.domain import biz_service_state as st
from noise_gate.domain.biz_service_keys import apm_instance_member_key, sms_member_key

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
GENERIC = frozenset({"운영"})
CFG = m.BizServiceConfig(aux=m.AuxSignalsConfig(min_groups=1))


def srv(name: str, db: str = "pg_a") -> m.SmsServer:
    return m.SmsServer(db, name.split("(")[0], name)


def key(s: m.SmsServer) -> str:
    return sms_member_key(s.db_id, s.resource_id) or ""


IBK = [srv("ibkweb01(인터넷뱅킹)"), srv("ibkwas01(인터넷뱅킹)")]


def propose(servers: list[m.SmsServer], stored: m.StoredState = m.StoredState(),
            **kw: object) -> m.Proposal:
    return bs.compute_proposal(m.ProposalInputs(
        servers=tuple(servers), generic_terms=GENERIC, stored=stored, **kw), CFG)  # type: ignore[arg-type]


def stored_auto(*members: m.StoredMember, **kw: object) -> m.StoredState:
    svc = m.StoredService(1, "인터넷뱅킹", "인터넷뱅킹", members=members, **kw)  # type: ignore[arg-type]
    return m.StoredState(services=(svc,))


def sms(s: m.SmsServer, **kw: object) -> m.StoredMember:
    base: dict[str, object] = {"basis": m.SIG_BIZ_SUFFIX, "confidence": m.CONF_CONFIRMED}
    base.update(kw)
    return m.StoredMember("sms_server", key(s), **base)  # type: ignore[arg-type]


def ops(stored: m.StoredState, servers: list[m.SmsServer], **kw: object) -> ap.ApplyOps:
    return bs.plan_apply(stored, propose(servers, stored, **kw), now=NOW)


# ── 서비스 행(§3.6 표 1~4) ─────────────────────────────────────────────
def test_new_service_insert_with_members() -> None:
    out = ops(m.StoredState(), IBK)
    assert [(o.action, o.service_key, o.service_id) for o in out.services] == [
        (ap.OP_INSERT, "인터넷뱅킹", None)]
    assert {(o.action, o.member_key, o.service_id) for o in out.members} == {
        (ap.OP_INSERT, key(s), None) for s in IBK}


def test_auto_same_result_is_same_and_no_member_ops() -> None:
    out = ops(stored_auto(*(sms(s) for s in IBK)), IBK)
    assert [o.action for o in out.services] == [ap.OP_SAME]
    assert out.members == ()


def test_auto_changed_result_update_with_previous() -> None:
    extra = srv("ibkdb01(인터넷뱅킹)")
    out = ops(stored_auto(*(sms(s) for s in IBK)), [*IBK, extra])
    svc = out.services[0]
    assert svc.action == ap.OP_UPDATE
    assert svc.evidence["previous"]["members"] == sorted(key(s) for s in IBK)
    assert [(o.action, o.member_key, o.service_id) for o in out.members] == [
        (ap.OP_INSERT, key(extra), 1)]


def test_manual_or_verified_service_unchanged_suggests_only() -> None:
    """수용 4 — 관리자 확인 서비스는 재계산이 바꾸지 않는다(`scan_suggests`만)."""
    extra = srv("ibkdb01(인터넷뱅킹)")
    for kw in ({"verified": True}, {"origin": m.ORIGIN_MANUAL}):
        out = ops(stored_auto(*(sms(s) for s in IBK), **kw), [*IBK, extra])
        assert [o.action for o in out.services] == [ap.OP_SUGGEST]
        assert out.services[0].evidence["scan_suggests"] == {"add": [key(extra)]}
        assert not [o for o in out.members if o.kind == "sms_server"]


def test_disabled_service_skipped_entirely() -> None:
    """수용 4 — 「서비스 아님」(disabled)은 불변 · 그 구성원은 후보·배정 대상도 아니다."""
    out = ops(stored_auto(*(sms(s) for s in IBK), state=m.SVC_DISABLED), IBK)
    assert [o.action for o in out.services] == [ap.OP_SKIP]
    assert out.members == ()


def test_excluded_and_manual_members_untouched() -> None:
    """수용 4 — 제외 구성원 · 관리자가 옮긴 구성원은 자동이 바꾸지 않는다."""
    moved = srv("ibkdb01(인터넷뱅킹)")
    other = m.StoredService(2, "카드", "카드", members=(
        sms(moved, origin=m.ORIGIN_MANUAL, basis=m.BASIS_MANUAL),))
    excl = m.StoredService(1, "인터넷뱅킹", "인터넷뱅킹", members=(
        sms(IBK[0]), sms(IBK[1], state=m.MEMBER_EXCLUDED)))
    out = ops(m.StoredState(services=(excl, other)), [*IBK, moved])
    touched = {o.member_key for o in out.members}
    assert key(moved) not in touched and key(IBK[1]) not in touched


def test_accepted_aux_candidate_member_stays() -> None:
    """수용 30 — 수락한 후보(`origin=manual`·`confirmed`)는 재계산이 바꾸지 않는다."""
    x = srv("zz1")
    stored = stored_auto(*(sms(s) for s in IBK),
                         sms(x, origin=m.ORIGIN_MANUAL, basis=m.BASIS_MANUAL))
    out = ops(stored, [*IBK, x])
    assert not [o for o in out.members if o.member_key == key(x)]


def test_auto_member_move_and_delete() -> None:
    a = srv("ibkdb01(카드)")
    b = srv("zz9")
    stored = stored_auto(*(sms(s) for s in IBK), sms(a), sms(b, basis=m.SIG_HOST_PREFIX))
    out = ops(stored, [*IBK, a, b])
    by = {o.member_key: o for o in out.members}
    assert by[key(a)].action == ap.OP_MOVE and by[key(a)].from_service_id == 1
    assert by[key(b)].action == ap.OP_DELETE


# ── 사라짐 · 180일 · APM 출처 ──────────────────────────────────────────
def test_vanished_server_target_gone_then_purge() -> None:
    gone = srv("ibkdb09(인터넷뱅킹)")
    out = ops(stored_auto(*(sms(s) for s in IBK), sms(gone)), IBK)
    assert [(o.action, o.member_key) for o in out.members] == [
        (ap.OP_TARGET_GONE, key(gone))]
    old = sms(gone, state=m.MEMBER_TARGET_GONE, gone_since="2026-04-01T00:00:00+00:00")
    assert [o.action for o in ops(stored_auto(*(sms(s) for s in IBK), old), IBK).members] == [
        ap.OP_PURGE]
    recent = sms(gone, state=m.MEMBER_TARGET_GONE, gone_since="2026-10-01T00:00:00+00:00")
    assert ops(stored_auto(*(sms(s) for s in IBK), recent), IBK).members == ()


def test_unscanned_db_not_marked_gone() -> None:
    other_db = srv("ibkdb09(인터넷뱅킹)", db="pg_b")
    out = ops(stored_auto(*(sms(s) for s in IBK), sms(other_db)), IBK)
    assert out.members == ()


def _map(mid: int, target: m.SmsServer, state: str = "active") -> m.ApmMapRow:
    return m.ApmMapRow(mid, "jen", "10", "instance", "i1", state, "auto", False, (
        {"kind": "sms", "db_id": target.db_id, "server_name": target.server_name,
         "resource_id": target.resource_id},))


def test_apm_member_source_map_gone_and_manual_kept() -> None:
    k_auto = apm_instance_member_key("jen", "10", "instance", "i1") or ""
    k_man = apm_instance_member_key("jen", "10", "instance", "i2") or ""
    stored = stored_auto(
        *(sms(s) for s in IBK),
        m.StoredMember("apm_instance", k_auto, basis=m.BASIS_APM_HOST_MAP, apm_map_id=5),
        m.StoredMember("apm_instance", k_man, origin=m.ORIGIN_MANUAL, basis=m.BASIS_MANUAL),
    )
    out = ops(stored, IBK, apm_maps=(_map(5, IBK[1], state="disabled"),))
    assert [(o.action, o.member_key) for o in out.members] == [(ap.OP_TARGET_GONE, k_auto)]
    insert = ops(m.StoredState(), IBK, apm_maps=(_map(5, IBK[1]),))
    apm_ops = [o for o in insert.members if o.kind == "apm_instance"]
    assert [(o.action, o.apm_map_id) for o in apm_ops] == [(ap.OP_INSERT, 5)]


def test_existing_or_rejected_candidates_not_inserted() -> None:
    x = srv("ibkweb02")
    no_s6 = m.BizServiceConfig(aux=m.AuxSignalsConfig(min_groups=1),
                               host_prefix=m.HostPrefixConfig(enabled=False))
    p = bs.compute_proposal(m.ProposalInputs(servers=(*IBK, x), generic_terms=GENERIC), no_s6)
    assert [o.action for o in bs.plan_apply(m.StoredState(), p, now=NOW).candidates] == [
        ap.OP_INSERT]
    known = m.StoredState(candidates=(m.StoredCandidate(m.CAND_MEMBER, "인터넷뱅킹", key(x)),))
    assert bs.plan_apply(known, p, now=NOW).candidates == ()


def test_manual_layer_preserved() -> None:
    cfg = m.BizServiceConfig(aux=m.AuxSignalsConfig(min_groups=1),
                             layer_tokens={"web": ("web",)})
    stored = stored_auto(sms(IBK[0], layer="db", layer_origin=m.LAYER_ORIGIN_MANUAL),
                         sms(IBK[1]))
    p = bs.compute_proposal(m.ProposalInputs(servers=tuple(IBK), generic_terms=GENERIC,
                                             stored=stored), cfg)
    out = bs.plan_apply(stored, p, now=NOW)
    assert not [o for o in out.members if o.member_key == key(IBK[0])]


# ── §4.4 상태 판정(수용 11) ─────────────────────────────────────────────
TH = {"cpu": m.SmsThreshold(warn=80, crit=90)}


def test_risk_from_sms_crit_with_major_incident() -> None:
    j = st.judge_service_state(
        monitored=True, incidents=[st.IncidentFact("h1", "major")],
        sms_metrics=[st.SmsMetricFact("h1", "cpu", 95)], thresholds=TH)
    assert j.state == st.STATE_RISK and j.label == "위험"
    assert (j.evidence[0].rule, j.evidence[0].metric, j.evidence[0].value) == (
        st.RULE_SMS_CRIT, "cpu", 95.0)


def test_risk_from_critical_incident_and_apm_gateway_signal() -> None:
    assert st.judge_service_state(monitored=False, incidents=[st.IncidentFact("h", "심각")]
                                  ).state == st.STATE_RISK
    j = st.judge_service_state(monitored=True,
                               apm_signals=[st.ApmSignalFact("a", "was_error_burst", "CRITICAL")])
    assert j.state == st.STATE_RISK and j.evidence[0].rule == st.RULE_APM_SIGNAL


def test_caution_from_open_incident_or_warn() -> None:
    assert st.judge_service_state(monitored=True, incidents=[st.IncidentFact("h", 2)],
                                  sms_metrics=[st.SmsMetricFact("h", "cpu", 10)],
                                  thresholds=TH).state == st.STATE_CAUTION
    assert st.judge_service_state(monitored=True, sms_metrics=[st.SmsMetricFact("h", "cpu", 85)],
                                  thresholds=TH).state == st.STATE_CAUTION
    closed = st.judge_service_state(monitored=True, incidents=[st.IncidentFact("h", 3, False)],
                                    sms_metrics=[st.SmsMetricFact("h", "cpu", 10)],
                                    thresholds=TH)
    assert closed.state == st.STATE_NO_SIGNAL


def test_apm_response_time_display_only_no_signal() -> None:
    j = st.judge_service_state(
        monitored=True, apm_values=[st.ApmValueFact("a", "response_time_ms", 8000)],
        sms_metrics=[st.SmsMetricFact("h", "cpu", 40)], thresholds=TH)
    assert j.state == st.STATE_NO_SIGNAL and j.label == "이상 신호 없음"
    assert j.displays[0].value == 8000 and j.evidence == ()


def test_unknown_states_with_reasons() -> None:
    off = st.judge_service_state(monitored=False, sms_metrics=[st.SmsMetricFact("h", "cpu", 1)],
                                 thresholds=TH)
    assert off.state == st.STATE_UNKNOWN and "감시 꺼짐" in off.reasons
    empty = st.judge_service_state(monitored=True)
    assert empty.state == st.STATE_UNKNOWN and "지표 없음" in empty.reasons
    failed = st.judge_service_state(monitored=True, fetch_failures=["jennifer timeout"],
                                    sms_metrics=[st.SmsMetricFact("h", "cpu", 1)], thresholds=TH)
    assert failed.state == st.STATE_UNKNOWN and any("조회 실패" in r for r in failed.reasons)


def test_sms_threshold_unset_is_unknown_for_sms_part() -> None:
    j = st.judge_service_state(monitored=True, sms_metrics=[st.SmsMetricFact("h", "cpu", 99)],
                               thresholds={})
    assert j.sms_notes == {"cpu": "판정 불가 · 임계 미설정"}
    assert j.evidence == () and any("임계 미설정" in r for r in j.reasons)


def test_no_normal_word_in_labels() -> None:
    assert all("정상" not in label for label in st.STATE_LABELS.values())
