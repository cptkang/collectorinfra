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

`canonical`(plans/211 W7 · `HOST_CANONICAL_MATCH_ENABLED`)이 켜지면 SMS hostname 색인이
정규형(L0)이다(`build_sms_index(canonical=True)` — 같은 색인을 itam 사슬도 쓴다). 그래도
잇지 못한 인스턴스는 이름 후보(L3 · D-348 후보 등급 `rank_candidates`)를 메모리에서 매겨
instance 행을 **`ambiguous` · `name_candidate`로만** 남긴다(진단 연결에 쓰지 않음 · DB 조회
추가 0 · 파드 제외 · 도메인의 itam 판정에서는 잇지 못한 인스턴스로 센다). 꺼지면 현행과 같다.

계층: domain — stdlib + 같은 패키지 + `src.domain.host_names`(stdlib 순수)만.
스키마 리터럴 없음(D-088).
"""

from __future__ import annotations

import ipaddress
from bisect import bisect_left
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.domain.host_names import canonical_host_key, name_tokens

from noise_gate.domain.apm_host_map import (
    BASIS_NAME_CANDIDATE,
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
    instance_match_key,
    make_target,
    normalize_targets,
    sms_host_lookup,
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
from noise_gate.domain.host_link import build_probe_plan, rank_candidates

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

#: 이름 후보(L3) 좁힘 키 하나에 걸리는 SMS 색인 항목 상한 — 넘는 키(전 서버가 공유하는 흔한 토큰 ·
#: 짧은 접두)는 좁힘에 쓰지 않는다. 너무 흔해 후보 의미가 없고, 인스턴스 × SMS 전수 대조를 막는다.
NAME_CANDIDATE_BUCKET_CAP = 50


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


def _first_label(value: Any) -> str:
    key = canonical_host_key(value)
    return "" if not key or _ip(key) else key.split(".", 1)[0]


class _NameCandidateIndex:
    """이름 후보(L3) 좁힘 색인 — SMS 대상마다 정규화 토큰(`name_tokens`)·정규형 첫 레이블을 정렬해
    둔다(스캔 1회 · 메모리). 질의 키로 시작하는 항목(접두 범위)만 꺼내 `rank_candidates`에
    넘긴다."""

    def __init__(self, by_host: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        targets: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        for bucket in by_host.values():
            for target in bucket:
                targets.setdefault(target_identity(target), target)
        self._targets = list(targets.values())
        entries: list[tuple[str, int]] = []
        for i, target in enumerate(self._targets):
            keys: set[str] = set()
            for value in (target.get("hostname"), target.get("server_name")):
                keys.update(name_tokens(value))
                first = _first_label(value)
                if first:
                    keys.add(first)
            entries.extend((k, i) for k in keys)
        entries.sort()
        self._keys = [k for k, _ in entries]
        self._rows = [i for _, i in entries]

    def rank(
        self, name: str, ip: str, *, limit: int
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """질의 이름(+IP) → `[(후보 등급, 대상)…]`(등급·점수 순 · `limit`건)."""
        plan = build_probe_plan(name, ip=ip or None)
        probes = {*plan.tokens, _first_label(name)} - {""}
        picked: set[int] = set()
        for probe in probes:
            lo = bisect_left(self._keys, probe)
            hi = bisect_left(self._keys, probe + "\U0010ffff", lo)
            if hi - lo <= NAME_CANDIDATE_BUCKET_CAP:
                picked.update(self._rows[lo:hi])
        if not picked:
            return []
        by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        rows: list[tuple[str, Mapping[str, Any]]] = []
        for i in sorted(picked):
            t = self._targets[i]
            db_id, server, host = (_text(t.get(k)) for k in ("db_id", "server_name", "hostname"))
            by_key[(db_id, server.lower(), host.lower())] = t
            rows.append(
                (db_id, {"hostname": host, "name": server, "ipaddress": _text(t.get("ip"))})
            )
        out: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for cand in rank_candidates(plan, rows, canonical=True, limit=limit):
            t = by_key[(cand["db_id"], cand["server_name"].lower(), cand["hostname"].lower())]
            out.append((cand, make_target(
                db_id=_text(t.get("db_id")), server_name=_text(t.get("server_name")),
                hostname=_text(t.get("hostname")), ip=_text(t.get("ip")),
                resource_id=_text(t.get("resource_id")),
            )))
        return out


def _name_candidate_proposal(
    index: _NameCandidateIndex,
    row: Mapping[str, Any],
    *,
    domain: str,
    domain_name: str,
    key: str,
    name: str,
    cap: int,
    base: Mapping[str, Any],
) -> AutoProposal | None:
    """L0까지 못 이은 인스턴스 1건 → 이름 후보 행(`ambiguous` · `name_candidate`) 또는 None."""
    host_name = _text(row.get("host_name"))
    query = host_name or (name if canonical_host_key(name) else "")
    if not query:
        return None
    ip = _ip(row.get("ip")) or ""
    ranked = index.rank(query, ip, limit=cap)
    if not ranked:
        return None
    return AutoProposal(
        domain_id=domain, scope=SCOPE_INSTANCE, match_key=key,
        basis=BASIS_NAME_CANDIDATE, state=STATE_AMBIGUOUS,
        targets=tuple(normalize_targets([t for _c, t in ranked], cap)),
        evidence={**base, "source": "scan", "host_name": host_name, "query": query, "ip": ip,
                  "candidates": [
                      {"db_id": c["db_id"], "server_name": c["server_name"],
                       "grade_key": c["grade_key"], "score": c["score"],
                       "reasons": list(c["reasons"])}
                      for c, _t in ranked
                  ]},
        domain_name=domain_name, instance_example=name,
    )


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
) -> dict[str, list[AutoProposal]]:
    """APM 인벤토리 스냅샷 행 → `{source_id: [제안…]}`(모듈 머리 규칙).

    인벤토리 행: `{source_id, domain_id, domain_name, domain_description, instance_name, ip,
    host_name, host_address, host_address_status}`. `use_node_address`가 거짓(노드 주소 스캐너 실패·
    꺼짐)이면 node_address 규칙을, `itam`이 None(ITAM 스캐너 실패)이면 itam 규칙을 건너뛴다.
    `canonical`은 `by_host`가 `build_sms_index(canonical=True)`일 때 켠다 — L0 조회 + L3 이름 후보.
    """
    cap = max(1, int(max_targets))
    base = dict(evidence or {})
    names: _NameCandidateIndex | None = None
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
            if use_node_address and node_ip and row.get("host_address_status") == NODE_STATUS_OK:
                match = vm_exact_match(by_ip.get(node_ip), None, max_targets=cap)
                if match.status == "linked":
                    proposal = AutoProposal(
                        domain_id=domain, scope=SCOPE_INSTANCE, match_key=key,
                        basis=BASIS_NODE_ADDRESS, state=STATE_ACTIVE, targets=match.targets,
                        evidence={**base, "source": "scan", "node_ip": node_ip},
                        domain_name=domain_name, instance_example=name,
                    )
                    wl_key = workload_match_key(name)
                    if wl_key:
                        slot = workloads.setdefault(wl_key, (name, []))
                        slot[1].extend(match.targets)
            if proposal is None:
                ip = _ip(row.get("ip"))
                host_hits = sms_host_lookup(by_host, row.get("host_name"), canonical=canonical)
                match = vm_exact_match(
                    by_ip.get(ip) if ip else None,
                    host_hits,
                    max_targets=cap,
                )
                if match.state is not None:
                    vm_evidence = {**base, "source": "scan", "ip": ip or "",
                                   "host_name": _text(row.get("host_name")),
                                   "candidates": len(match.targets)}
                    if canonical and host_hits:
                        vm_evidence["match"] = "canonical"
                    proposal = AutoProposal(
                        domain_id=domain, scope=SCOPE_INSTANCE, match_key=key,
                        basis=BASIS_VM_EXACT, state=match.state,
                        targets=tuple(normalize_targets(list(match.targets), cap)),
                        evidence=vm_evidence,
                        domain_name=domain_name, instance_example=name,
                    )
            if proposal is None and canonical and workload_match_key(name) is None:
                # 파드는 이름 후보를 만들지 않는다(D-355 ④ — 서버 선택을 유도하지 않음)
                names = names or _NameCandidateIndex(by_host)
                proposal = _name_candidate_proposal(
                    names, row, domain=domain, domain_name=domain_name, key=key, name=name,
                    cap=cap, base=base,
                )
            if proposal is not None:
                proposals.append(proposal)
                linked_any = linked_any or proposal.state == STATE_ACTIVE
        for wl_key, (example, targets) in workloads.items():
            merged = normalize_targets(targets, cap)
            proposals.append(AutoProposal(
                domain_id=domain, scope=SCOPE_WORKLOAD, match_key=wl_key,
                basis=BASIS_NODE_ADDRESS, state=STATE_ACTIVE, targets=tuple(merged),
                evidence={**base, "source": "scan", "pods": sum(
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
    """연계 요약 초기값(§3.5 `link_summary`)."""
    return {"new": 0, "changed": 0, "ambiguous": 0, "failed": 0, "gone": 0, "kept_manual": 0,
            "errors": 0, "skipped": []}


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
    "NAME_CANDIDATE_BUCKET_CAP",
    "NODE_STATUS_OK",
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
    "ItamSnapshot",
    "ScannerResult",
    "add_write_results",
    "empty_link_summary",
    "failed_result",
    "plan_scan_proposals",
    "present_servers",
    "run_status",
]
