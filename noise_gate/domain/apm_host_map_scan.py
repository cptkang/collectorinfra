"""사전 스캔 일괄 생성 — 스냅샷 → 자동 제안 · 스캐너 기록 · 실행 판정 · 연계 요약.

plans/210 §3.5 · D-362.

스캐너가 읽어 온 스냅샷(APM 인벤토리 · SMS 색인 · ITAM 업무·서버)에 §3.4 규칙을
**node_address → vm_exact → itam** 순으로 적용해 매핑 키별 자동 제안(`AutoProposal`)을 만든다.
쓰기 규칙(관리자 행 불변 · scan_suggests)은 저장소가 `plan_auto_write`로 판정한다.

- node_address: 인스턴스의 노드 주소(상태 ok)가 SMS IP 색인에서 서버 1대 → instance 행 · 같은
  워크로드의 파드들이 이번 스캔에서 찾은 대상의 합집합 → workload 행.
- vm_exact: 노드 주소로 잇지 못한 인스턴스의 `ip`·`host_name`이 SMS 서버 1대 → instance 행(active) ·
  2대 이상 → instance 행(ambiguous · 후보만 · 진단에 쓰지 않음).
- itam: 이번 스캔에서 인스턴스가 하나도 잇지 못한 도메인만 → domain 행(`apm_host_map_itam`).

계층: domain — stdlib + 같은 패키지 + `src.domain.host_names`(stdlib 순수)만.
스키마 리터럴 없음(D-088).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from noise_gate.domain.apm_host_map import (
    BASIS_NODE_ADDRESS,
    BASIS_VM_EXACT,
    DEFAULT_MAX_TARGETS,
    SCAN_FAILED,
    SCAN_OK,
    SCAN_PARTIAL,
    SCOPE_INSTANCE,
    SCOPE_WORKLOAD,
    STATE_ACTIVE,
    STATE_AMBIGUOUS,
    STATE_FAILED,
    WRITE_INSERT,
    WRITE_SAME,
    WRITE_SKIP,
    WRITE_SUGGEST,
    WRITE_UPDATE,
    AutoProposal,
    VmMatch,
    instance_match_key,
    normalize_targets,
    sms_host_servers,
    target_identity,
    vm_exact_match,
    workload_match_key,
)
from noise_gate.domain.apm_host_map_itam import (
    DEFAULT_MAX_SERVERS,
    ItamApp,
    ItamServer,
    itam_proposal,
    resolve_itam_chain,
)

#: 스캐너 이름(실행 기록 `scanners` 키 · 실행 순서).
SCANNER_APM_INVENTORY = "apm_inventory"
SCANNER_NODE_ADDRESS = "node_address"
SCANNER_SMS = "sms"
SCANNER_ITAM = "itam"
SCANNER_DPM = "dpm"
SCANNERS: tuple[str, ...] = (
    SCANNER_APM_INVENTORY, SCANNER_NODE_ADDRESS, SCANNER_SMS, SCANNER_ITAM, SCANNER_DPM,
)

#: 스캐너 상태 — 실행 상태 3종(ok·partial·failed) + 설정상 꺼짐 · 미연동(DPM 슬롯 §3.9).
SCANNER_OK = SCAN_OK
SCANNER_PARTIAL = SCAN_PARTIAL
SCANNER_FAILED = SCAN_FAILED
SCANNER_DISABLED = "disabled"
SCANNER_NOT_INTEGRATED = "not_integrated"
#: 스냅샷을 쓸 수 있는 스캐너 상태.
SCANNER_USABLE: frozenset[str] = frozenset({SCANNER_OK, SCANNER_PARTIAL})

#: 규칙 이름(`link_summary.skipped`).
RULE_NODE_ADDRESS = BASIS_NODE_ADDRESS
RULE_VM_EXACT = BASIS_VM_EXACT
RULE_ITAM = "itam"

#: 노드 주소 행 상태 중 「노드 주소를 안다」.
NODE_STATUS_OK = "ok"

#: 노드 주소 사유 카운터(`link_summary` 가산 키 · plans/211 §9.3 C).
STAT_NODE_OK = "node_ok"                    # 노드 주소 상태 ok(IP 형식 통과)
STAT_NODE_IP_UNMATCHED = "node_ip_unmatched"  # 노드 IP가 SMS 색인에 없음
STAT_NODE_IP_AMBIGUOUS = "node_ip_ambiguous"  # 노드 IP가 SMS 서버 2대 이상
STAT_NODE_NOT_OK = "node_not_ok"            # 노드 주소 상태가 ok가 아님(빈칸 포함)
NODE_SUMMARY_KEYS: tuple[str, ...] = (
    STAT_NODE_OK, STAT_NODE_IP_UNMATCHED, STAT_NODE_IP_AMBIGUOUS, STAT_NODE_NOT_OK,
)
#: VM 일치 카운터(`stats`에만 — 요청 처리의 단계별 결과 `tried`가 쓴다).
STAT_VM_TRIED = "vm_tried"
STAT_VM_LINKED = "vm_linked"
STAT_VM_AMBIGUOUS = "vm_ambiguous"


@dataclass
class ScannerResult:
    """스캐너 1개의 결과 — `record()`가 실행 기록 칸(§3.5 `{status, rows, seconds, error_kind,
    unchecked_zones}` + 스캐너별 부가 칸 `extra`)이다. `data`는 적재·대조용(기록에 싣지 않는다)."""

    status: str
    rows: int = 0
    seconds: float = 0.0
    error_kind: str | None = None
    unchecked_zones: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    data: Any = None

    def record(self) -> dict[str, Any]:
        """실행 기록 `scanners[이름]` 값."""
        out: dict[str, Any] = {
            "status": self.status,
            "rows": int(self.rows),
            "seconds": round(float(self.seconds), 3),
            "error_kind": self.error_kind,
            "unchecked_zones": list(self.unchecked_zones),
        }
        out.update(self.extra)
        return out


def failed_result(error_kind: str, *, seconds: float = 0.0) -> ScannerResult:
    """실패 스캐너 결과."""
    return ScannerResult(SCANNER_FAILED, seconds=seconds, error_kind=error_kind)


def run_status(results: Mapping[str, ScannerResult]) -> str:
    """실행 상태 — 전 스캐너가 ok(또는 꺼짐·미연동)면 ok · 인벤토리·SMS·ITAM이 모두 실패면 failed ·
    그 밖은 partial."""
    statuses = {name: r.status for name, r in results.items()}
    core = [statuses.get(n) for n in (SCANNER_APM_INVENTORY, SCANNER_SMS, SCANNER_ITAM)]
    if all(s == SCANNER_FAILED for s in core):
        return SCAN_FAILED
    fine = {SCANNER_OK, SCANNER_DISABLED, SCANNER_NOT_INTEGRATED}
    if all(s in fine for s in statuses.values()):
        return SCAN_OK
    return SCAN_PARTIAL


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ip(value: Any) -> str | None:
    try:
        return str(ipaddress.ip_address(_text(value)))
    except ValueError:
        return None


@dataclass(frozen=True)
class ItamSnapshot:
    """ITAM 사슬 입력 — 업무·서버 스냅샷과 일반어·서버 상한."""

    apps: Sequence[ItamApp]
    servers: Sequence[ItamServer]
    generic_terms: Collection[str] = ()
    max_servers: int = DEFAULT_MAX_SERVERS


def plan_scan_proposals(
    inventory: Iterable[Mapping[str, Any]],
    by_ip: Mapping[str, Sequence[Mapping[str, Any]]],
    by_host: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    use_node_address: bool,
    itam: ItamSnapshot | None,
    max_targets: int = DEFAULT_MAX_TARGETS,
    evidence: Mapping[str, Any] | None = None,
    canonical: bool = False,
    node_extra: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    vm_extra: Mapping[tuple[str, str, str], VmMatch] | None = None,
    stats: dict[str, int] | None = None,
    evidence_source: str = "scan",
) -> dict[str, list[AutoProposal]]:
    """APM 인벤토리 스냅샷 행 → `{source_id: [제안…]}`(모듈 머리 규칙).

    인벤토리 행: `{source_id, domain_id, domain_name, domain_description, instance_name, ip,
    host_name, host_address, host_address_status}`. `use_node_address`가 거짓(노드 주소 스캐너 실패·
    꺼짐)이면 node_address 규칙을, `itam`이 None(ITAM 스캐너 실패)이면 itam 규칙을 건너뛴다.

    선택 인자(plans/211 §9.3 — 기본값이면 현행과 같다):
    - `canonical`: SMS hostname 색인 키 공간(`build_sms_index`와 같은 값)
    - `node_extra`: 노드 IP → 직접 조회로 찾은 서버(색인 miss일 때만 본다 · evidence `live`)
    - `vm_extra`: `(source_id, domain_id, instance 키)` → 직접 조회한 VM 일치(색인 결과가 0대일
      때만 본다 · evidence `live`)
    - `stats`: 노드 주소 사유(`NODE_SUMMARY_KEYS`)·VM 일치 카운터를 더할 dict
    - `evidence_source`: evidence `source` 값
    """
    cap = max(1, int(max_targets))
    base = dict(evidence or {})
    counts: dict[str, int] = stats if stats is not None else {}

    def _count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    domains: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in inventory:
        source, domain = _text(row.get("source_id")), _text(row.get("domain_id"))
        if source and domain:
            domains.setdefault((source, domain), []).append(row)
    out: dict[str, list[AutoProposal]] = {}
    for (source, domain), rows in domains.items():
        domain_name = next((_text(r.get("domain_name")) for r in rows if r.get("domain_name")), "")
        proposals: list[AutoProposal] = []
        workloads: dict[str, tuple[str, list[dict[str, Any]]]] = {}
        linked_any = False
        for row in rows:
            name = _text(row.get("instance_name"))
            key = instance_match_key(name)
            if not key:
                continue
            proposal = None
            node_ip = _ip(row.get("host_address"))
            node_ok = node_ip is not None and row.get("host_address_status") == NODE_STATUS_OK
            if use_node_address and not node_ok:
                _count(STAT_NODE_NOT_OK)
            if use_node_address and node_ip and node_ok:
                _count(STAT_NODE_OK)
                servers = by_ip.get(node_ip)
                live = False
                if not servers and node_extra and node_extra.get(node_ip):
                    servers, live = node_extra[node_ip], True
                match = vm_exact_match(servers, None, max_targets=cap)
                if match.status == "none":
                    _count(STAT_NODE_IP_UNMATCHED)
                elif match.status == "ambiguous":
                    _count(STAT_NODE_IP_AMBIGUOUS)
                if match.status == "linked":
                    node_evidence: dict[str, Any] = {**base, "source": evidence_source,
                                                       "node_ip": node_ip}
                    if live:
                        node_evidence["live"] = True
                    proposal = AutoProposal(
                        domain_id=domain, scope=SCOPE_INSTANCE, match_key=key,
                        basis=BASIS_NODE_ADDRESS, state=STATE_ACTIVE, targets=match.targets,
                        evidence=node_evidence,
                        domain_name=domain_name, instance_example=name,
                    )
                    wl_key = workload_match_key(name)
                    if wl_key:
                        slot = workloads.setdefault(wl_key, (name, []))
                        slot[1].extend(match.targets)
            if proposal is None:
                ip = _ip(row.get("ip"))
                host_name = _text(row.get("host_name"))
                match = vm_exact_match(
                    by_ip.get(ip) if ip else None,
                    sms_host_servers(by_host, host_name, canonical=canonical),
                    max_targets=cap,
                )
                live = False
                extra = (vm_extra or {}).get((source, domain, key))
                if match.state is None and extra is not None and extra.state is not None:
                    match, live = extra, True
                if ip or host_name:
                    _count(STAT_VM_TRIED)
                    if match.status == "linked":
                        _count(STAT_VM_LINKED)
                    elif match.status == "ambiguous":
                        _count(STAT_VM_AMBIGUOUS)
                if match.state is not None:
                    vm_evidence: dict[str, Any] = {
                        **base, "source": evidence_source, "ip": ip or "", "host_name": host_name,
                        "candidates": len(match.targets),
                    }
                    if live:
                        vm_evidence["live"] = True
                    proposal = AutoProposal(
                        domain_id=domain, scope=SCOPE_INSTANCE, match_key=key,
                        basis=BASIS_VM_EXACT, state=match.state,
                        targets=tuple(normalize_targets(list(match.targets), cap)),
                        evidence=vm_evidence,
                        domain_name=domain_name, instance_example=name,
                    )
            if proposal is not None:
                proposals.append(proposal)
                linked_any = linked_any or proposal.state == STATE_ACTIVE
        for wl_key, (example, targets) in workloads.items():
            merged = normalize_targets(targets, cap)
            proposals.append(AutoProposal(
                domain_id=domain, scope=SCOPE_WORKLOAD, match_key=wl_key,
                basis=BASIS_NODE_ADDRESS, state=STATE_ACTIVE, targets=tuple(merged),
                evidence={**base, "source": evidence_source, "pods": sum(
                    1 for r in rows if workload_match_key(r.get("instance_name")) == wl_key)},
                domain_name=domain_name, instance_example=example,
            ))
        if itam is not None and not linked_any:
            description = next(
                (_text(r.get("domain_description")) for r in rows if r.get("domain_description")),
                "",
            )
            result = resolve_itam_chain(
                domain_name=domain_name, domain_description=description,
                apps=itam.apps, servers=itam.servers, by_ip=by_ip, by_host=by_host,
                generic_terms=itam.generic_terms, max_servers=itam.max_servers,
                max_targets=cap, canonical=canonical,
            )
            proposals.append(itam_proposal(
                domain_id=domain, domain_name=domain_name, result=result,
                instance_example=_text(rows[0].get("instance_name")), extra_evidence=base,
            ))
        if proposals:
            out.setdefault(source, []).extend(proposals)
    return out


def present_servers(rows: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """SMS 스냅샷 행 → 본 서버 `(db_id, 서버명 소문자)`(`mark_targets_gone` 입력)."""
    out: set[tuple[str, str]] = set()
    for row in rows:
        ident = target_identity({"db_id": row.get("db_id"), "server_name": row.get("server_name")})
        if ident[1] and ident[2]:
            out.add((ident[1], ident[2]))
    return out


def empty_link_summary() -> dict[str, Any]:
    """연계 요약 초기값(§3.5 `link_summary` + 노드 주소 사유 카운터 — plans/211 §9.3 C)."""
    return {"new": 0, "changed": 0, "ambiguous": 0, "failed": 0, "gone": 0, "kept_manual": 0,
            "errors": 0, "skipped": [], **dict.fromkeys(NODE_SUMMARY_KEYS, 0)}


def add_write_results(
    summary: dict[str, Any],
    proposals: Sequence[AutoProposal],
    results: Sequence[tuple[str, Any]],
) -> None:
    """`apply_auto_many` 결과를 요약에 더한다 — new(insert) · changed(update) · kept_manual
    (suggest·skip) · errors · 자동으로 쓴 행의 ambiguous·failed 수."""
    for proposal, (action, _row) in zip(proposals, results, strict=False):
        if action == WRITE_INSERT:
            summary["new"] += 1
        elif action == WRITE_UPDATE:
            summary["changed"] += 1
        elif action in (WRITE_SUGGEST, WRITE_SKIP):
            summary["kept_manual"] += 1
        elif action != WRITE_SAME:
            summary["errors"] += 1
            continue
        if action in (WRITE_INSERT, WRITE_UPDATE, WRITE_SAME):
            if proposal.state == STATE_AMBIGUOUS:
                summary["ambiguous"] += 1
            elif proposal.state == STATE_FAILED:
                summary["failed"] += 1


__all__ = [
    "NODE_STATUS_OK",
    "NODE_SUMMARY_KEYS",
    "RULE_ITAM",
    "RULE_NODE_ADDRESS",
    "RULE_VM_EXACT",
    "SCANNERS",
    "SCANNER_APM_INVENTORY",
    "SCANNER_DISABLED",
    "SCANNER_DPM",
    "SCANNER_FAILED",
    "SCANNER_ITAM",
    "SCANNER_NODE_ADDRESS",
    "SCANNER_NOT_INTEGRATED",
    "SCANNER_OK",
    "SCANNER_PARTIAL",
    "SCANNER_SMS",
    "SCANNER_USABLE",
    "STAT_NODE_IP_AMBIGUOUS",
    "STAT_NODE_IP_UNMATCHED",
    "STAT_NODE_NOT_OK",
    "STAT_NODE_OK",
    "STAT_VM_AMBIGUOUS",
    "STAT_VM_LINKED",
    "STAT_VM_TRIED",
    "ItamSnapshot",
    "ScannerResult",
    "add_write_results",
    "empty_link_summary",
    "failed_result",
    "plan_scan_proposals",
    "present_servers",
    "run_status",
]
