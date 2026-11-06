"""업무 서비스 계산 결과 → 저장 상태와의 차이 → 순수 「적용 연산 목록」 (plans/215 §3.6 쓰기 규칙).

W3 저장소가 `ApplyOps`를 그대로 실행한다(이 모듈은 I/O 0). 쓰기 규칙 표 전 행:

| 경우 | 연산 |
|---|---|
| 키에 서비스 없음 | 서비스 `insert`(auto · 감시 끔) + 구성원 `insert` |
| auto·미확인 · 결과 동일 / 변경 | `same`(updated_at만) / `update` + `evidence.previous` |
| manual·verified | 서비스·SMS 구성 불변 · 다르면 `suggest`(`evidence.scan_suggests`) |
| disabled | 불변(`skip`) |
| 구성원 `excluded` · `origin=manual` | 자동이 바꾸지 않음 |
| 스냅샷에서 사라짐(끝까지 읽은 db만) | `target_gone` · 180일 뒤 auto 구성원 `purge` |
| APM 구성원 — 출처 매핑이 사라지거나 비활성 | `target_gone`(관리자 수동 APM 구성원은 유지) |
| 거절·기존 후보 | 다시 내지 않음 |

해석(계획서 표에 없는 경우):
- auto 서비스의 auto SMS 구성원이 다른 auto 서비스로 제안되면 `move`, 서버는 있는데 어디에도
  제안되지 않으면 `delete`(자동 결과 갱신).
- APM 구성원은 210 연계에서 도출되므로 disabled가 아닌 서비스면 manual·verified 서비스에도
  동기화한다
  (관리자 수동 APM 구성원은 그대로).

계층: domain — stdlib + 같은 domain 모듈만 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER, parse_member_key
from noise_gate.domain.biz_service_model import (
    GONE_PURGE_DAYS,
    LAYER_ORIGIN_AUTO,
    LAYER_ORIGIN_MANUAL,
    MEMBER_ACTIVE,
    MEMBER_EXCLUDED,
    MEMBER_TARGET_GONE,
    ORIGIN_AUTO,
    ORIGIN_MANUAL,
    SVC_DISABLED,
    Candidate,
    Proposal,
    ProposedMember,
    ProposedService,
    StoredMember,
    StoredService,
    StoredState,
)

# 서비스 연산
OP_INSERT = "insert"
OP_UPDATE = "update"
OP_SAME = "same"
OP_SUGGEST = "suggest"
OP_SKIP = "skip"
# 구성원 연산(OP_INSERT · OP_UPDATE 공용)
OP_MOVE = "move"
OP_TARGET_GONE = "target_gone"
OP_RESTORE = "restore"
OP_DELETE = "delete"
OP_PURGE = "purge"


@dataclass(frozen=True)
class ServiceOp:
    """서비스 연산 1건 — `service_id`는 기존 행(신규는 None) · `evidence`는 쓸 값 전체."""

    action: str
    service_key: str
    service_id: int | None
    display_name: str
    state: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemberOp:
    """구성원 연산 1건.

    `service_id`가 None이면 같은 연산 목록에서 `insert`되는 서비스(`service_key`)에 붙인다.
    `move`는 `from_service_id`에서 `service_key` 서비스로 옮긴다. `layer`·`layer_origin`은 쓸 값이다
    (관리자 지정 계층은 그대로 실린다).
    """

    action: str
    service_key: str
    kind: str
    member_key: str
    service_id: int | None = None
    from_service_id: int | None = None
    origin: str = ORIGIN_AUTO
    basis: str = ""
    confidence: str = ""
    state: str = MEMBER_ACTIVE
    display: Mapping[str, Any] = field(default_factory=dict)
    prefix_code: str = ""
    apm_map_id: int | None = None
    layer: str | None = None
    layer_origin: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateOp:
    """후보 연산 1건(새 후보 `insert`)."""

    action: str
    candidate: Candidate


@dataclass(frozen=True)
class ApplyOps:
    """적용 연산 목록 — 서비스 → 구성원 → 후보 순으로 실행한다."""

    services: tuple[ServiceOp, ...] = ()
    members: tuple[MemberOp, ...] = ()
    candidates: tuple[CandidateOp, ...] = ()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _active_sms(svc: StoredService) -> list[StoredMember]:
    return [m for m in svc.members if m.kind == KIND_SMS_SERVER and m.state != MEMBER_EXCLUDED]


def _result_sig(
    display: str, state: str, members: list[tuple[str, str, str]]
) -> tuple[str, str, tuple[tuple[str, str, str], ...]]:
    return display, state, tuple(sorted(members))


def _member_op(
    action: str, pm: ProposedMember, *, service_id: int | None,
    from_service_id: int | None = None, stored: StoredMember | None = None,
) -> MemberOp:
    layer, layer_origin = pm.layer, (LAYER_ORIGIN_AUTO if pm.layer else None)
    if stored is not None and stored.layer_origin == LAYER_ORIGIN_MANUAL:
        layer, layer_origin = stored.layer, LAYER_ORIGIN_MANUAL  # 관리자 지정 계층은 덮지 않는다
    return MemberOp(
        action, pm.service_key, pm.kind, pm.member_key, service_id, from_service_id,
        ORIGIN_AUTO, pm.basis, pm.confidence, MEMBER_ACTIVE, pm.display, pm.prefix_code,
        pm.apm_map_id, layer, layer_origin, pm.evidence,
    )


def _differs(pm: ProposedMember, em: StoredMember) -> bool:
    if em.state != MEMBER_ACTIVE or em.basis != pm.basis or em.confidence != pm.confidence:
        return True
    if pm.apm_map_id is not None and em.apm_map_id != pm.apm_map_id:
        return True
    return em.layer_origin != LAYER_ORIGIN_MANUAL and em.layer != pm.layer


def _scanned(member: StoredMember, proposal: Proposal) -> bool:
    """SMS 구성원의 db를 이번 실행이 끝까지 읽었는가(못 읽은 db의 구성원은 판단하지 않는다)."""
    parts = parse_member_key(KIND_SMS_SERVER, member.member_key)
    return parts is not None and parts[0] in proposal.scanned_db_ids


def plan_apply(stored: StoredState, proposal: Proposal, *, now: datetime) -> ApplyOps:
    """저장 상태와 제안의 차이 → 적용 연산 목록(§3.6 쓰기 규칙 · 결정적 정렬)."""
    by_key = {s.service_key: s for s in stored.services}
    index: dict[tuple[str, str], tuple[StoredService, StoredMember]] = {}
    for svc in sorted(stored.services, key=lambda s: s.service_id):
        for sm in svc.members:
            index.setdefault((sm.kind, sm.member_key), (svc, sm))
    proposed = {(pm.kind, pm.member_key): pm for pm in proposal.members}
    sms_by_svc: dict[str, list[ProposedMember]] = {}
    for pm in proposal.members:
        if pm.kind == KIND_SMS_SERVER:
            sms_by_svc.setdefault(pm.service_key, []).append(pm)

    service_ops = [_service_op(p, by_key.get(p.service_key), sms_by_svc.get(p.service_key, []))
                   for p in sorted(proposal.services, key=lambda s: s.service_key)]

    member_ops: list[MemberOp] = []
    for key in sorted(proposed):
        pm = proposed[key]
        target = by_key.get(pm.service_key)
        if target is not None and target.state == SVC_DISABLED:
            continue
        if pm.kind == KIND_SMS_SERVER and target is not None and target.immutable:
            continue  # manual·verified 서비스의 SMS 구성은 불변(scan_suggests로만)
        sid = target.service_id if target is not None else None
        hit = index.get(key)
        if hit is None:
            member_ops.append(_member_op(OP_INSERT, pm, service_id=sid))
            continue
        es, em = hit
        if em.state == MEMBER_EXCLUDED or em.origin == ORIGIN_MANUAL:
            continue
        if es.state == SVC_DISABLED or (pm.kind == KIND_SMS_SERVER and es.immutable):
            continue
        if es.service_key != pm.service_key:
            member_ops.append(_member_op(OP_MOVE, pm, service_id=sid,
                                         from_service_id=es.service_id, stored=em))
        elif _differs(pm, em):
            member_ops.append(_member_op(OP_UPDATE, pm, service_id=sid, stored=em))

    purge_before = now - timedelta(days=GONE_PURGE_DAYS)
    for key in sorted(index):
        if key in proposed:
            continue
        es, em = index[key]
        if es.state == SVC_DISABLED or em.state == MEMBER_EXCLUDED:
            continue
        op = _stale_member_op(es, em, proposal, purge_before)
        if op is not None:
            member_ops.append(MemberOp(
                op, es.service_key, em.kind, em.member_key, es.service_id,
                origin=em.origin, basis=em.basis, confidence=em.confidence,
                state=MEMBER_TARGET_GONE if op == OP_TARGET_GONE else MEMBER_ACTIVE,
                apm_map_id=em.apm_map_id, layer=em.layer, layer_origin=em.layer_origin,
            ))

    existing = {(c.kind, c.service_key, c.target_key) for c in stored.candidates}
    cand_ops = [CandidateOp(OP_INSERT, c) for c in proposal.candidates
                if (c.kind, c.service_key, c.target_key) not in existing]
    return ApplyOps(tuple(service_ops), tuple(member_ops), tuple(cand_ops))


def _stale_member_op(
    es: StoredService, em: StoredMember, proposal: Proposal, purge_before: datetime
) -> str | None:
    """제안에 없는 저장 구성원의 연산 — 사라짐 · 복귀 · 삭제 · 180일 뒤 삭제."""
    if em.kind == KIND_SMS_SERVER:
        if not _scanned(em, proposal):
            return None  # 못 읽은 db — 사라짐·삭제를 판단하지 않고 그대로 둔다
        gone = em.member_key not in proposal.present_member_keys
    elif em.origin == ORIGIN_MANUAL:
        return None  # 관리자 수동 APM 구성원은 출처와 무관하게 유지
    else:
        gone = em.apm_map_id is not None and em.apm_map_id not in proposal.active_map_ids
    if em.state == MEMBER_TARGET_GONE:
        if not gone:
            if em.origin == ORIGIN_MANUAL or es.immutable:
                return OP_RESTORE
            return OP_DELETE
        since = _parse_time(em.gone_since)
        if em.origin == ORIGIN_AUTO and since is not None and since <= purge_before:
            return OP_PURGE
        return None
    if gone:
        return OP_TARGET_GONE
    if em.origin == ORIGIN_MANUAL or (em.kind == KIND_SMS_SERVER and es.immutable):
        return None
    return OP_DELETE


def _service_op(
    p: ProposedService, s: StoredService | None, sms: list[ProposedMember]
) -> ServiceOp:
    """서비스 1건의 연산(§3.6 표 1~4행)."""
    proposed_sig = _result_sig(p.display_name, p.state,
                               [(m.member_key, m.confidence, m.basis) for m in sms])
    if s is None:
        return ServiceOp(OP_INSERT, p.service_key, None, p.display_name, p.state, p.evidence)
    if s.state == SVC_DISABLED:
        return ServiceOp(OP_SKIP, p.service_key, s.service_id, s.display_name, s.state)
    stored_members = _active_sms(s)
    if s.immutable:
        have = {m.member_key for m in stored_members}
        want = {m.member_key for m in sms}
        suggest: dict[str, Any] = {}
        if p.display_name != s.display_name:
            suggest["display_name"] = p.display_name
        if p.state != s.state:
            suggest["state"] = p.state
        if want - have:
            suggest["add"] = sorted(want - have)
        if have - want:
            suggest["remove"] = sorted(have - want)
        if not suggest:
            return ServiceOp(OP_SKIP, p.service_key, s.service_id, s.display_name, s.state)
        evidence = dict(s.evidence)
        evidence["scan_suggests"] = suggest
        return ServiceOp(OP_SUGGEST, p.service_key, s.service_id, s.display_name, s.state,
                         evidence)
    auto_members = [(m.member_key, m.confidence, m.basis) for m in stored_members
                    if m.origin == ORIGIN_AUTO and m.state == MEMBER_ACTIVE]
    if _result_sig(s.display_name, s.state, auto_members) == proposed_sig:
        return ServiceOp(OP_SAME, p.service_key, s.service_id, s.display_name, s.state)
    evidence = dict(p.evidence)
    evidence["previous"] = {
        "display_name": s.display_name, "state": s.state,
        "members": sorted(m[0] for m in auto_members),
    }
    return ServiceOp(OP_UPDATE, p.service_key, s.service_id, p.display_name, p.state, evidence)
