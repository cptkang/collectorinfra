"""업무 서비스 통합 연계 계산 — 진입 함수 (plans/215 §3 · §4.4 · D-368).

결정적 순수 함수만 둔다(LLM 0 · I/O 0 · 입력 순서 무관). W3 계산 경로가 부르는 진입은 셋이다.

- `compute_proposal(inputs, config, previous_summary=None) -> Proposal` — 축 ① S1~S7 합치기 →
  저장 결정 덮기 → 축 ③ APM 도출 → 보조 신호 S8~S12 후보 · S4·S5 후보 → §3.4 분석 지표.
- `plan_apply(stored, proposal, now=...) -> ApplyOps` — §3.6 쓰기 규칙으로 적용 연산 목록
  (`biz_service_apply`).
- `judge_service_state(...)` — §4.4 서비스 상태 판정(`biz_service_state`).

저장 결정(관리자 확인·이동·제외·「서비스 아님」)은 계산 결과를 덮는다. 그 서버는 자동 배정이
바뀌어도 `plan_apply`가 건드리지 않고, 보조 신호의 닻·순도 평가에는 관리자 결정이 쓰인다.

계층: domain — stdlib + `src.domain.host_names` + 같은 domain 모듈만 쓴다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.host_names import canonical_host_key, split_business_suffix

from noise_gate.domain.biz_service_analysis import AnalysisInputs, analyze
from noise_gate.domain.biz_service_apm import ApmDerivation, derive_apm_members
from noise_gate.domain.biz_service_apply import (
    ApplyOps,
    CandidateOp,
    MemberOp,
    ServiceOp,
    plan_apply,
)
from noise_gate.domain.biz_service_aux import (
    build_aux_bundles,
    propagate_candidates,
    signal_status,
)
from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER, parse_member_key
from noise_gate.domain.biz_service_model import (
    AUX_SIGNALS,
    CAND_MEMBER,
    CAND_MERGE,
    CAND_REJECTED,
    CONF_CONFIRMED,
    CONF_INFERRED,
    LAYERS,
    MEMBER_ACTIVE,
    MEMBER_EXCLUDED,
    MEMBER_TARGET_GONE,
    ORIGIN_MANUAL,
    SIG_BIZ_SUFFIX,
    SVC_ACTIVE,
    SVC_AMBIGUOUS,
    SVC_DISABLED,
    BizServiceConfig,
    Candidate,
    CandidateSignal,
    Proposal,
    ProposalInputs,
    ProposedMember,
    ProposedService,
    ServerRef,
    SmsServer,
    StoredState,
    fold_terms,
    ref_key,
    text,
)
from noise_gate.domain.biz_service_signals import (
    RawCandidate,
    merge_axis_one,
    merge_candidates,
    stem_candidates,
)
from noise_gate.domain.biz_service_state import judge_service_state

#: 병합 후보 대상 종류(서비스 ↔ 서비스).
TARGET_SERVICE = "service"


# ── 저장 결정 ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StoredView:
    """저장 상태에서 읽은 SMS 결정.

    - `decided`: 자동이 바꾸지 않는 배정(manual·verified·disabled 서비스의 구성원 · `origin=manual`)
    - `blocked`: 자동 배정·후보 대상이 아닌 서버(제외 구성원 · 「서비스 아님」 서비스의 구성원)
    - `excluded_pairs`: 다시 후보로 내지 않을 `(서버, 서비스)`
    - `confirmed`: 순도 평가용 확정 닻(수동·확인)
    - `rejected`: 거절 후보 `(종류, 서비스 키, 대상 키)`
    """

    decided: Mapping[ServerRef, str]
    decided_conf: Mapping[ServerRef, str]
    decided_basis: Mapping[ServerRef, str]
    blocked: frozenset[ServerRef]
    excluded_pairs: frozenset[tuple[ServerRef, str]]
    confirmed: Mapping[ServerRef, str]
    rejected: frozenset[tuple[str, str, str]]


def stored_view(stored: StoredState) -> StoredView:
    """저장 상태 → SMS 결정(서비스 id 순으로 읽어 결정적)."""
    decided: dict[ServerRef, str] = {}
    conf: dict[ServerRef, str] = {}
    basis: dict[ServerRef, str] = {}
    blocked: set[ServerRef] = set()
    excluded: set[tuple[ServerRef, str]] = set()
    confirmed: dict[ServerRef, str] = {}
    for svc in sorted(stored.services, key=lambda s: s.service_id):
        for m in svc.members:
            parts = parse_member_key(m.kind, m.member_key) if m.kind == KIND_SMS_SERVER else None
            if parts is None:
                continue
            ref: ServerRef = (parts[0], parts[1])
            if m.state == MEMBER_EXCLUDED:
                blocked.add(ref)
                excluded.add((ref, svc.service_key))
            elif svc.state == SVC_DISABLED:
                blocked.add(ref)
            elif svc.immutable or m.origin == ORIGIN_MANUAL:
                decided.setdefault(ref, svc.service_key)
                conf.setdefault(ref, m.confidence)
                basis.setdefault(ref, m.basis)
                if m.state == MEMBER_ACTIVE and (m.origin == ORIGIN_MANUAL
                                                 or m.confidence == CONF_CONFIRMED):
                    confirmed.setdefault(ref, svc.service_key)
    rejected = frozenset((c.kind, c.service_key, c.target_key) for c in stored.candidates
                         if c.status == CAND_REJECTED)
    for ref in blocked:
        decided.pop(ref, None)
        confirmed.pop(ref, None)
    return StoredView(decided, conf, basis, frozenset(blocked), frozenset(excluded), confirmed,
                      rejected)


# ── 계층 제안 ───────────────────────────────────────────────────────────
def suggest_layer(server: SmsServer, layer_tokens: Mapping[str, Sequence[str]]) -> str | None:
    """호스트 이름 토큰 규칙으로 계층 제안.

    토큰이 비면 늘 None(지어내지 않는다) · 2계층 이상 맞으면 None(고르지 않는다).
    """
    if not layer_tokens:
        return None
    name_host, _ = split_business_suffix(server.server_name)
    key = canonical_host_key(server.hostname or name_host)
    label = key.split(".", 1)[0] if key else ""
    if not label:
        return None
    hits = sorted(
        layer for layer, tokens in layer_tokens.items()
        if layer in LAYERS and any(text(t).lower() and text(t).lower() in label for t in tokens)
    )
    return hits[0] if len(hits) == 1 else None


# ── 후보 모으기 ─────────────────────────────────────────────────────────
def _member_candidates(raw: Sequence[RawCandidate]) -> list[Candidate]:
    """같은 서버 → 서비스 후보를 1건으로 합치고(근거 여럿) 정렬한다(근거 수 ↓ · 순도 ↓)."""
    grouped: dict[tuple[str, str], dict[str, CandidateSignal]] = defaultdict(dict)
    new_names: dict[tuple[str, str], str] = {}
    for c in raw:
        key = (ref_key(c.ref), c.service_key)
        grouped[key].setdefault(f"{c.signal.basis}|{c.signal.bundle_key}", c.signal)
        if c.new_service_name:
            new_names[key] = c.new_service_name
    alt = Counter(target for target, _ in grouped)
    out = [
        Candidate(CAND_MEMBER, service, KIND_SMS_SERVER, target,
                  tuple(sigs[k] for k in sorted(sigs)), alt[target],
                  new_names.get((target, service), ""))
        for (target, service), sigs in grouped.items()
    ]
    return sorted(out, key=lambda c: (
        -len(c.signals),
        -max((s.purity for s in c.signals if s.purity is not None), default=-1.0),
        c.target_key, c.service_key,
    ))


def _dedupe(servers: Sequence[SmsServer]) -> list[SmsServer]:
    by_ref: dict[ServerRef, SmsServer] = {}
    for s in sorted(servers, key=lambda x: (x.ref, x.server_name, x.hostname, x.ip)):
        if all(s.ref):
            by_ref.setdefault(s.ref, s)
    return [by_ref[r] for r in sorted(by_ref)]


def _gone_counts(
    stored: StoredState, present: frozenset[str], scanned: frozenset[str]
) -> dict[str, int]:
    out: dict[str, int] = {}
    for svc in stored.services:
        if svc.state == SVC_DISABLED:
            continue
        n = 0
        for m in svc.members:
            if m.kind != KIND_SMS_SERVER or m.state == MEMBER_EXCLUDED:
                continue
            parts = parse_member_key(m.kind, m.member_key)
            vanished = parts is not None and parts[0] in scanned and m.member_key not in present
            if m.state == MEMBER_TARGET_GONE or vanished:
                n += 1
        if n:
            out[svc.service_key] = n
    return out


# ── 진입 ────────────────────────────────────────────────────────────────
def compute_proposal(
    inputs: ProposalInputs,
    config: BizServiceConfig,
    previous_summary: Mapping[str, Any] | None = None,
) -> Proposal:
    """§3.2 → §3.3 → §3.4 계산 1회(미리보기·적용 공용 · 저장 0)."""
    generic = fold_terms(inputs.generic_terms)
    servers = _dedupe(inputs.servers)
    view = stored_view(inputs.stored)
    axis = merge_axis_one(
        servers, config=config, generic=generic, attrs=inputs.attrs, apm_maps=inputs.apm_maps,
        itam_apps=inputs.itam_apps, itam_servers=inputs.itam_servers,
        confirmed_stored=view.confirmed, attr_failures=inputs.attr_failures,
    )
    assign = axis.assignments
    counts = Counter(a.service_key for a in assign.values())
    ambiguous = {k for k, n in counts.items() if n > config.max_members}

    effective: dict[ServerRef, str] = {
        ref: a.service_key for ref, a in assign.items()
        if ref not in view.decided and ref not in view.blocked
    }
    effective.update(view.decided)
    confidence = {ref: (CONF_INFERRED if a.service_key in ambiguous else a.confidence)
                  for ref, a in assign.items()}
    confidence.update(view.decided_conf)
    basis = {ref: a.basis for ref, a in assign.items()}
    basis.update(view.decided_basis)

    apm: ApmDerivation = derive_apm_members(inputs.apm_maps, inputs.apm_inventory, servers,
                                            effective)

    zones = sorted({s.zone for s in servers})
    aux_build = build_aux_bundles(servers, inputs.attrs, inputs.alarm_minutes, config.aux)
    confirmed = {ref: key for ref, key in ((r, a.service_key) for r, a in assign.items()
                                           if a.basis == SIG_BIZ_SUFFIX)
                 if ref not in view.blocked}
    confirmed.update(view.confirmed)
    status = {
        (sig, zone): signal_status(
            sig, zone, config=config,
            bundles=[b.refs for b in aux_build.bundles if b.signal == sig and b.zone == zone],
            confirmed=confirmed, attr_failures=inputs.attr_failures,
        )
        for sig in AUX_SIGNALS for zone in zones
    }
    eligible = [s.ref for s in servers if s.ref not in effective and s.ref not in view.blocked]
    eligible_set = set(eligible)
    prop = propagate_candidates(
        aux_build.bundles, anchors=effective, eligible=eligible, status=status,
        min_anchors=config.aux.min_anchors, blocked=view.excluded_pairs,
    )
    raw: list[RawCandidate] = [c for c in axis.candidates if c.ref in eligible_set]
    raw += [RawCandidate(c.ref, c.service_key, c.signal) for c in prop.candidates]
    raw += stem_candidates(servers, effective=effective, eligible=eligible,
                           blocked=view.excluded_pairs)
    candidates = _member_candidates(raw)

    names = {k: axis.services[k].display_name for k in counts}
    for svc in inputs.stored.services:
        if svc.state != SVC_DISABLED:
            names.setdefault(svc.service_key, svc.display_name)
    candidates += [Candidate(CAND_MERGE, a, TARGET_SERVICE, b, (sig,))
                   for a, b, sig in merge_candidates(names, generic)]
    candidates += list(apm.candidates)
    candidates = [c for c in candidates
                  if (c.kind, c.service_key, c.target_key) not in view.rejected]

    by_ref = {s.ref: s for s in servers}
    members: list[ProposedMember] = []
    for ref in sorted(assign):
        a, s = assign[ref], by_ref[ref]
        ev = dict(a.evidence)
        ev["signals"] = list(a.signals)
        members.append(ProposedMember(
            a.service_key, KIND_SMS_SERVER, s.member_key, a.basis, confidence[ref],
            display={"server_name": text(s.server_name), "hostname": text(s.hostname),
                     "ip": text(s.ip), "db_id": s.ref[0], "zone": text(s.zone)},
            prefix_code=axis.codes.get(ref, ""), layer=suggest_layer(s, config.layer_tokens),
            evidence=ev,
        ))
    members += list(apm.members)

    services: list[ProposedService] = []
    for key in sorted(counts):
        draft = axis.services[key]
        ev_s: dict[str, Any] = dict(draft.evidence)
        ev_s["signals"] = dict(sorted(Counter(a.basis for a in assign.values()
                                              if a.service_key == key).items()))
        ev_s["sms_members"] = counts[key]
        if key in ambiguous:
            ev_s["ambiguous_reason"] = f"구성원 {counts[key]}대 > 상한 {config.max_members}대"
        services.append(ProposedService(key, draft.display_name,
                                        SVC_AMBIGUOUS if key in ambiguous else SVC_ACTIVE, ev_s))

    present = frozenset(s.member_key for s in servers)
    scanned = (frozenset(inputs.scanned_db_ids) if inputs.scanned_db_ids is not None
               else frozenset(s.ref[0] for s in servers))
    conflicts = tuple(axis.conflicts) + tuple(apm.conflicts)
    statuses = tuple(axis.signal_status) + tuple(status[k] for k in sorted(status))
    notes = dict(axis.notes)
    notes.update(aux_build.notes)
    summary = analyze(AnalysisInputs(
        servers=servers, effective=effective, confidence=confidence, basis=basis,
        apm_members=apm.members, apm_inventory=inputs.apm_inventory,
        linked_refs=apm.linked_refs, candidates=candidates, conflicts=conflicts,
        signal_status=statuses, shared=prop.shared,
        gone=_gone_counts(inputs.stored, present, scanned), notes=notes,
        attr_failures=[{"signal": f.signal, "zone": f.zone, "reason": f.reason}
                       for f in sorted(inputs.attr_failures,
                                       key=lambda f: (f.signal, f.zone or "", f.reason))],
    ), previous_summary)
    return Proposal(
        services=tuple(services), members=tuple(members), candidates=tuple(candidates),
        conflicts=conflicts, signal_status=statuses,
        unassigned=tuple(sorted(ref_key(r) for r in eligible)),
        active_map_ids=apm.active_map_ids, present_member_keys=present, scanned_db_ids=scanned,
        summary=summary,
    )


__all__ = [
    "ApplyOps",
    "CandidateOp",
    "MemberOp",
    "ServiceOp",
    "StoredView",
    "compute_proposal",
    "judge_service_state",
    "plan_apply",
    "stored_view",
    "suggest_layer",
]
