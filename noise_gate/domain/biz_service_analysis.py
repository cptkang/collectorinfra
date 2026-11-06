"""업무 서비스 연계 분석 지표 — 서비스별 · 전체 · 직전 적용 대비 변화 (plans/215 §3.4).

계산(미리보기·적용)마다 결정적으로 구해 실행 기록(`summary`)에 그대로 싣는 JSON 모양 dict를 만든다.
키는 전부 정렬해 같은 입력이면 같은 바이트가 나온다.

계층: domain — stdlib + 같은 domain 모듈만 쓴다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, SCOPE_WORKLOAD, instance_match_key
from noise_gate.domain.biz_service_keys import (
    KIND_APM_DOMAIN,
    KIND_APM_INSTANCE,
    apm_domain_member_key,
    apm_instance_member_key,
)
from noise_gate.domain.biz_service_model import (
    CAND_APM_UNLINKED,
    CAND_MEMBER,
    CAND_MERGE,
    CONF_INFERRED,
    ApmInventoryRow,
    Candidate,
    Conflict,
    ProposedMember,
    ServerRef,
    SignalStatus,
    SmsServer,
    text,
)


@dataclass(frozen=True)
class AnalysisInputs:
    """분석 입력 — 저장 결정을 덮은 결과(`effective`) 기준이다."""

    servers: Sequence[SmsServer]
    effective: Mapping[ServerRef, str]
    confidence: Mapping[ServerRef, str]
    basis: Mapping[ServerRef, str]
    apm_members: Sequence[ProposedMember]
    apm_inventory: Sequence[ApmInventoryRow]
    linked_refs: Collection[ServerRef]
    candidates: Sequence[Candidate]
    conflicts: Sequence[Conflict]
    signal_status: Sequence[SignalStatus]
    shared: Mapping[tuple[str, str], int] = field(default_factory=dict)
    gone: Mapping[str, int] = field(default_factory=dict)
    notes: Mapping[str, int] = field(default_factory=dict)
    attr_failures: Sequence[Mapping[str, Any]] = ()


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _instance_keys(inv: ApmInventoryRow) -> list[str]:
    keys = [apm_instance_member_key(inv.source_id, inv.domain_id, SCOPE_INSTANCE,
                                    instance_match_key(inv.instance_name))]
    if text(inv.workload_key):
        keys.append(apm_instance_member_key(inv.source_id, inv.domain_id, SCOPE_WORKLOAD,
                                            inv.workload_key))
    return [k for k in keys if k]


def analyze(data: AnalysisInputs, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """§3.4 지표 → `{"services": {키: 지표}, "overall": 지표, "change": 변화}`."""
    by_ref = {s.ref: s for s in data.servers}
    svc_refs: dict[str, list[ServerRef]] = defaultdict(list)
    for ref in sorted(data.effective):
        if ref in by_ref:
            svc_refs[data.effective[ref]].append(ref)
    apm_by_svc: dict[str, list[ProposedMember]] = defaultdict(list)
    for m in data.apm_members:
        apm_by_svc[m.service_key].append(m)
    keys = sorted(set(svc_refs) | set(apm_by_svc))
    linked = set(data.linked_refs)

    cand_by_svc: dict[str, Counter[str]] = defaultdict(Counter)
    for c in data.candidates:
        cand_by_svc[c.service_key][c.kind] += 1
        if c.kind == CAND_MERGE:
            cand_by_svc[c.target_key][c.kind] += 1
    conflicts_by_svc: Counter[str] = Counter()
    for cf in data.conflicts:
        for k in cf.service_keys:
            conflicts_by_svc[k] += 1
    contrib: dict[str, Counter[str]] = defaultdict(Counter)
    for c in data.candidates:
        for sig in c.signals:
            contrib[c.service_key][sig.basis] += 1

    services: dict[str, Any] = {}
    total_inferred = total_members = 0
    for key in keys:
        refs = svc_refs.get(key, [])
        apm = apm_by_svc.get(key, [])
        inferred = sum(1 for r in refs if data.confidence.get(r) == CONF_INFERRED)
        inferred += sum(1 for m in apm if m.confidence == CONF_INFERRED)
        n_members = len(refs) + len(apm)
        total_inferred += inferred
        total_members += n_members
        n_linked = sum(1 for r in refs if r in linked)
        services[key] = {
            "sms_servers": len(refs),
            "sms_by_db": dict(sorted(Counter(r[0] for r in refs).items())),
            "sms_by_zone": dict(sorted(Counter(by_ref[r].zone for r in refs).items())),
            "confirmed": sum(1 for r in refs if data.confidence.get(r) != CONF_INFERRED),
            "inferred": sum(1 for r in refs if data.confidence.get(r) == CONF_INFERRED),
            "apm_linked_servers": n_linked,
            "apm_missing_servers": len(refs) - n_linked,
            "apm_instances": sum(1 for m in apm if m.kind == KIND_APM_INSTANCE),
            "apm_domains": sum(1 for m in apm if m.kind == KIND_APM_DOMAIN),
            "apm_unlinked_instances": cand_by_svc[key][CAND_APM_UNLINKED],
            "inferred_ratio": _ratio(inferred, n_members),
            "target_gone": data.gone.get(key, 0),
            "conflicts": conflicts_by_svc[key],
            "open_candidates": sum(cand_by_svc[key].values()),
            "member_basis": dict(sorted(Counter(data.basis[r] for r in refs
                                                if r in data.basis).items())),
            "candidate_signals": dict(sorted(contrib[key].items())),
        }

    member_keys = {m.member_key for m in data.apm_members}
    domains = {m.member_key for m in data.apm_members if m.kind == KIND_APM_DOMAIN}
    unassigned_instances = sum(
        1 for inv in data.apm_inventory if text(inv.instance_name)
        and not member_keys.intersection(_instance_keys(inv))
        and apm_domain_member_key(inv.source_id, inv.domain_id) not in domains
    )
    shared: dict[str, dict[str, int]] = defaultdict(dict)
    for (signal, zone), n in sorted(data.shared.items()):
        shared[signal][zone] = n
    overall = {
        "services": len(keys),
        "sms_servers": len(by_ref),
        "assigned_servers": sum(len(v) for v in svc_refs.values()),
        "unassigned_servers": len(by_ref) - sum(len(v) for v in svc_refs.values()),
        "apm_linked_servers": sum(1 for r in by_ref if r in linked),
        "unassigned_instances": unassigned_instances,
        "inferred_ratio": _ratio(total_inferred, total_members),
        "target_gone": sum(data.gone.values()),
        "conflicts": len(data.conflicts),
        "open_candidates": {
            kind: sum(1 for c in data.candidates if c.kind == kind)
            for kind in (CAND_MEMBER, CAND_MERGE, CAND_APM_UNLINKED)
        },
        "signals": [
            {"signal": s.signal, "zone": s.zone, "enabled": s.enabled, "purity": s.purity,
             "evaluated": s.evaluated, "pure": s.pure, "reason": s.reason,
             "reason_text": s.reason_text}
            for s in sorted(data.signal_status, key=lambda x: (x.signal, x.zone))
        ],
        "shared_bundles": dict(shared),
        "notes": dict(sorted(data.notes.items())),
        "attr_failures": list(data.attr_failures),
    }
    return {"services": services, "overall": overall, "change": _change(services, overall,
                                                                         previous)}


def _change(
    services: Mapping[str, Any], overall: Mapping[str, Any], previous: Mapping[str, Any] | None
) -> dict[str, Any]:
    """직전 적용 요약 대비 구성원·연계 증감. 직전이 없으면 `{"baseline": False}`."""
    if not isinstance(previous, Mapping) or not isinstance(previous.get("services"), Mapping):
        return {"baseline": False}
    prev_svcs: Mapping[str, Any] = previous["services"]
    prev_all: Mapping[str, Any] = previous.get("overall") or {}
    per: dict[str, Any] = {}
    fields = ("sms_servers", "apm_linked_servers", "apm_instances")
    for key in sorted(set(services) | set(prev_svcs)):
        now, before = services.get(key) or {}, prev_svcs.get(key) or {}
        delta: dict[str, Any] = {
            f: int(now.get(f, 0) or 0) - int(before.get(f, 0) or 0) for f in fields
        }
        if any(delta.values()) or (key in services) != (key in prev_svcs):
            delta["status"] = ("new" if key not in prev_svcs
                               else "removed" if key not in services else "changed")
            per[key] = delta
    return {
        "baseline": True,
        "services": per,
        "overall": {
            f: int(overall.get(f, 0) or 0) - int(prev_all.get(f, 0) or 0)
            for f in ("services", "assigned_servers", "apm_linked_servers")
        },
    }
