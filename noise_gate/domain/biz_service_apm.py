"""업무 서비스 축 ③ — APM 구성요소를 업무 서비스에 잇기(축 ② 210 연계로 도출 · plans/215 §3.3).

축 ②(`apm_host_map`)는 읽기만 한다. 축 ①이 정한 SMS 서버의 서비스 위에서만 도출한다(같은 계산
1회 안의 2단계 — 순환 없음).

- instance·workload 행: `state=active` · 쓸 수 있는 대상(`kind=sms` · `target_gone` 아님)이
  **모두 같은 서비스 S**면 S의 `apm_instance` 구성원. 확신도는 매핑을 따른다
  (manual·verified → confirmed · auto → inferred). 대상이 서로 다른 서비스로 갈리면 「APM 충돌」.
- domain 행(같은 규칙) · 또는 인벤토리의 한 도메인 인스턴스가 전부 S로 이어짐 → `apm_domain` 구성원.
- 매핑이 없는 인스턴스는 같은 도메인의 다른 인스턴스가 S로 이어졌으면 후보
  (「같은 도메인 · 미연계」).
  연계 추가는 210 관리 탭 몫이다(이 계산은 210 매핑을 만들지 않는다).

계층: domain — stdlib + 같은 domain 모듈만 쓴다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from noise_gate.domain.apm_host_map import (
    ORIGIN_MANUAL,
    SCOPE_DOMAIN,
    SCOPE_INSTANCE,
    SCOPE_WORKLOAD,
    STATE_ACTIVE,
    instance_match_key,
    workload_match_key,
)
from noise_gate.domain.biz_service_keys import (
    KIND_APM_DOMAIN,
    KIND_APM_INSTANCE,
    apm_domain_member_key,
    apm_instance_member_key,
)
from noise_gate.domain.biz_service_model import (
    BASIS_APM_HOST_MAP,
    CAND_APM_UNLINKED,
    CONF_CONFIRMED,
    CONF_INFERRED,
    CONFLICT_APM,
    ApmInventoryRow,
    ApmMapRow,
    Candidate,
    CandidateSignal,
    Conflict,
    ProposedMember,
    ServerRef,
    SmsServer,
    ref_key,
    text,
)
from noise_gate.domain.biz_service_signals import resolve_targets, target_index

#: 미연계 후보 근거 이름.
BASIS_SAME_DOMAIN = "same_domain"


@dataclass(frozen=True)
class ApmDerivation:
    """축 ③ 결과 — 구성원 · 충돌 · 미연계 후보 · APM이 이어진 서버 · 활성 매핑 id."""

    members: tuple[ProposedMember, ...]
    conflicts: tuple[Conflict, ...]
    candidates: tuple[Candidate, ...]
    linked_refs: frozenset[ServerRef]
    active_map_ids: frozenset[int]


def _confidence(row: ApmMapRow) -> str:
    return CONF_CONFIRMED if row.verified or row.origin == ORIGIN_MANUAL else CONF_INFERRED


def _member_key(row: ApmMapRow) -> str | None:
    if row.scope == SCOPE_DOMAIN:
        return apm_domain_member_key(row.source_id, row.domain_id)
    return apm_instance_member_key(row.source_id, row.domain_id, row.scope, row.match_key)


def derive_apm_members(
    maps: Sequence[ApmMapRow],
    inventory: Sequence[ApmInventoryRow],
    servers: Sequence[SmsServer],
    effective: Mapping[ServerRef, str],
) -> ApmDerivation:
    """§3.3 도출 규칙 — `effective`(서버 → 서비스 키)는 저장된 수동 결정을 덮은 SMS 배정이다."""
    index = target_index(servers)
    members: dict[str, ProposedMember] = {}
    conflicts: list[Conflict] = []
    linked: set[ServerRef] = set()
    active_ids: set[int] = set()
    row_service: dict[int, str] = {}
    rows_by_key: dict[tuple[str, str, str, str], ApmMapRow] = {}
    for row in sorted(maps, key=lambda r: r.map_id):
        if row.state != STATE_ACTIVE:
            continue
        active_ids.add(row.map_id)
        rows_by_key.setdefault(
            (text(row.source_id), text(row.domain_id), row.scope, text(row.match_key)), row)
        refs = resolve_targets(row.targets, index)
        if not refs:
            continue
        linked |= refs
        services = {effective.get(r) for r in refs}
        known = sorted(s for s in services if s)
        key = _member_key(row)
        if len(known) >= 2:
            conflicts.append(Conflict(CONFLICT_APM, key or f"map:{row.map_id}", tuple(known),
                                      BASIS_APM_HOST_MAP, {
                                          "apm_map_id": row.map_id,
                                          "servers": sorted(ref_key(r) for r in refs),
                                      }))
            continue
        if None in services or not known or key is None:
            continue  # 대상 일부가 미배정이면 「모두 같은 서비스」가 아니다
        row_service[row.map_id] = known[0]
        kind = KIND_APM_DOMAIN if row.scope == SCOPE_DOMAIN else KIND_APM_INSTANCE
        members[key] = ProposedMember(
            known[0], kind, key, BASIS_APM_HOST_MAP, _confidence(row),
            display={
                "source_id": text(row.source_id), "domain_id": text(row.domain_id),
                "domain_name": text(row.domain_name), "scope": row.scope,
                "instance_name": text(row.instance_example) or text(row.match_key),
            },
            apm_map_id=row.map_id,
            evidence={"servers": sorted(ref_key(r) for r in refs)},
        )

    candidates: list[Candidate] = []
    by_domain: dict[tuple[str, str], list[ApmInventoryRow]] = defaultdict(list)
    for inv in inventory:
        if text(inv.instance_name):
            by_domain[(text(inv.source_id), text(inv.domain_id))].append(inv)
    for (source, domain), rows in sorted(by_domain.items()):
        resolved: list[tuple[ApmInventoryRow, ApmMapRow | None]] = []
        for inv in sorted(rows, key=lambda r: (text(r.instance_name).lower(), r.instance_id)):
            resolved.append((inv, _row_for(rows_by_key, source, domain, inv)))
        linked_services = {row_service[r.map_id] for _, r in resolved
                           if r is not None and r.map_id in row_service}
        domain_key = apm_domain_member_key(source, domain)
        if (domain_key and domain_key not in members and len(linked_services) == 1
                and all(r is not None and r.map_id in row_service for _, r in resolved)):
            service = next(iter(linked_services))
            confs = {_confidence(r) for _, r in resolved if r is not None}
            members[domain_key] = ProposedMember(
                service, KIND_APM_DOMAIN, domain_key, BASIS_APM_HOST_MAP,
                CONF_CONFIRMED if confs == {CONF_CONFIRMED} else CONF_INFERRED,
                display={"source_id": source, "domain_id": domain,
                         "domain_name": text(rows[0].domain_name)},
                evidence={"instances": len(resolved)},
            )
        if len(linked_services) != 1:
            continue
        service = next(iter(linked_services))
        for inv, hit in resolved:
            if hit is not None:
                continue
            key = apm_instance_member_key(source, domain, SCOPE_INSTANCE,
                                          instance_match_key(inv.instance_name))
            if key:
                candidates.append(Candidate(
                    CAND_APM_UNLINKED, service, KIND_APM_INSTANCE, key,
                    (CandidateSignal(BASIS_SAME_DOMAIN, f"{source}|{domain}",
                                     sum(1 for _, r in resolved if r is not None)),),
                ))
    return ApmDerivation(
        tuple(members[k] for k in sorted(members)),
        tuple(sorted(conflicts, key=lambda c: (c.target_key, c.service_keys))),
        tuple(candidates), frozenset(linked), frozenset(active_ids),
    )


def _row_for(
    rows: Mapping[tuple[str, str, str, str], ApmMapRow], source: str, domain: str,
    inv: ApmInventoryRow,
) -> ApmMapRow | None:
    """인벤토리 인스턴스 1개를 덮는 활성 매핑 행 — instance → workload → domain 순."""
    inst = instance_match_key(inv.instance_name)
    if inst and (row := rows.get((source, domain, SCOPE_INSTANCE, inst))) is not None:
        return row
    workload = text(inv.workload_key) or workload_match_key(inv.instance_name) or ""
    if workload and (row := rows.get((source, domain, SCOPE_WORKLOAD, workload))) is not None:
        return row
    return rows.get((source, domain, SCOPE_DOMAIN, ""))
