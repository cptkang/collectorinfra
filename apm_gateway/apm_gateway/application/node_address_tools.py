"""노드 주소 일괄 조회 도구 `apm_node_addresses` (plans/210 W2 §3.6 · D-355).

`apm_instance_map`의 노드 주소 칸은 대상 지정 호출만 웹을 새로 조회한다(무인자 목록은 캐시 전용 ·
상한은 이 모듈의 `MAX_DOMAINS_PER_CALL` 공유 — plans/210 W8 B). 이 도구는 사전 스캔·채팅 보기
(`apm.node_address`)용으로 지정 도메인(생략 시 인벤토리 전 도메인)을 조회한다 — 호출당 (소스,
도메인) `MAX_DOMAINS_PER_CALL`개까지만 조회하고 초과분은 `[한계]`에 적는다(봉투 `partial`). 조회는
`NodeAddressService.domain_snapshots`다(소스 안 직렬 · 소스 간 병렬 · 도메인마다
`domain_snapshot(fetch=True)` 그대로 — TTL 캐시 · 로그인 차단기 · 실패 간격 · 소스 잠금 공유 ·
plans/210 W9 A). 대상 지정 호출(`domain_ids`·`instance_ids` 중 하나라도 줌)은 호출 시간 예산
(`call_budget_seconds`) 안에서만 조회한다 — 못 본 도메인은 TTL 안 캐시, 없으면 행 `budget_exceeded`
+ `[한계]` 1줄 + `partial`. 무인자(전 도메인 — 사전 스캔)는 예산 없이 소스 간 병렬만이다.

`instance_ids`(plans/210 W8 A · 가산 인자): 생략하면 현행 응답과 비트 동일이다. 주면 그 인스턴스가
있는 도메인만 조회하고(`domain_ids`와 AND — `domain_ids`를 생략해도 전 도메인 조회가 되지 않는다)
행·`instance_status`는 그 인스턴스만 센다. 조회한 소스·도메인의 인벤토리에 없는 id는 `[한계]`에
적는다(침묵 금지).

행(인스턴스마다): `source_id`·`domain_id`·`instance_id`·`instance_name`·`ip`(Open API 인스턴스 IP —
없으면 웹 목록 IP)·`host_address`(노드 주소 — 모르면 빈 값)·`pod`(파드 여부 — 모르면 None)·`status`
(노드 주소 상태 어휘 — `ok`·`not_found`(웹 목록에 없음)·`disabled`·`login_failed`·`login_blocked`·
`error`·`budget_exceeded`(호출 시간 예산 안에 조회하지 못함)). 봉투 `node_address_sources`(소스
마다): `source_id`·`status`(위 어휘 + 도메인 일부만 실패면 `partial` · 전 도메인 실패면 가장 급한
실패 상태)·`reason`·`domains`·
`failed_domains[{domain_id, status}]`·`instances`·`instance_status`(상태별 인스턴스 수).
`ok`가 아닌 소스는 `[한계]`에 적고 봉투 `partial`을 세운다(침묵 금지).

비밀번호·쿠키는 조회기 밖으로 나오지 않는다(행·사유·로그 모두 — 로그는 소스·도메인·예외 형 이름만).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from apm_gateway.adapters.jennifer.web_fields import KIND_POD, is_pod_row
from apm_gateway.application.apm_core import ApmCore
from apm_gateway.application.node_address import (
    STATUS_BUDGET_EXCEEDED,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_LOGIN_BLOCKED,
    STATUS_LOGIN_FAILED,
    STATUS_NOT_FOUND,
    STATUS_OK,
    DomainSnapshot,
    instance_key,
)
from apm_gateway.application.resolver import Inventory
from apm_gateway.application.sources import STATUS_OK as INVENTORY_OK
from apm_gateway.application.sources import partial_of
from apm_gateway.application.tool_common import Limits, nonneg_int
from apm_gateway.domain.errors import INVALID_ARGUMENT, NOT_CONFIGURED, ApmError

TOOL = "apm_node_addresses"
# 호출당 웹 조회 (소스, 도메인) 상한 — 인자 없는 호출이 전 도메인을 순차 웹 조회하지 않게(보안 M-2)
MAX_DOMAINS_PER_CALL = 50
#: `instance_ids` 길이 상한(plans/210 W8 교정) — 넘으면 `invalid_argument`
MAX_INSTANCE_IDS = 1000
#: 작업·감사 라벨과 `[한계]` 문구에 적는 id 수 — 나머지는 「외 n개」
SHOWN_IDS = 5


def shown_ids(ids: list[Any]) -> str:
    """id 목록의 표시 문구 — 앞 `SHOWN_IDS`개 + 「외 n개」(라벨·한계 문구가 길어지지 않게)."""
    head = ", ".join(map(str, ids[:SHOWN_IDS]))
    return head + (f" 외 {len(ids) - SHOWN_IDS}개" if len(ids) > SHOWN_IDS else "")
# 소스의 도메인 일부만 조회에 실패했다(나머지 도메인 행은 `ok`·`not_found`)
STATUS_PARTIAL = "partial"
# 조회기가 없을 때(플래그 off — 도구 자체가 등록되지 않는다 · 코어 직접 호출의 방어)
DISABLED_REASON = "노드 주소 조회 비활성(JENNIFER_NODE_ADDRESS_ENABLED)"

# 소스 상태 → 사유(값·자격증명 없음 — 설정 이름과 조치만)
_REASONS = {
    STATUS_DISABLED: (
        "웹 계정 미설정(JENNIFER_<ID>_WEB_USER·_WEB_PASSWORD) — 노드 주소를 조회하지 않았다"
    ),
    STATUS_LOGIN_FAILED: "웹 로그인 실패 — 웹 계정·비밀번호 확인",
    STATUS_LOGIN_BLOCKED: (
        "웹 로그인 연속 실패로 30분 정지(login_blocked) — 비밀번호 변경·초기화 확인"
    ),
    STATUS_ERROR: "웹 노드 주소 조회 실패(연결·응답 형태·시간 초과)",
    STATUS_BUDGET_EXCEEDED: (
        "호출 시간 예산 초과로 조회하지 못함(JENNIFER_NODE_ADDRESS_CALL_BUDGET_SECONDS)"
    ),
}


# 전 도메인이 실패했을 때 소스 상태로 고르는 순서(앞이 우선 — 조치가 더 급한 사유)
_SEVERITY = (
    STATUS_DISABLED,
    STATUS_LOGIN_BLOCKED,
    STATUS_LOGIN_FAILED,
    STATUS_ERROR,
    STATUS_BUDGET_EXCEEDED,
)


def _source_status(domain_statuses: list[str]) -> str:
    """도메인 상태들 → 소스 상태. 전부 `ok`(또는 도메인 0개)면 `ok` · 일부만 실패면 `partial` ·
    전부 실패면 실패 상태 중 `_SEVERITY` 앞의 것(예: 로그인 실패 3회 뒤 정지 → `login_blocked`)."""
    failed = {s for s in domain_statuses if s != STATUS_OK}
    if not failed:
        return STATUS_OK
    if STATUS_OK in domain_statuses:
        return STATUS_PARTIAL
    return next((s for s in _SEVERITY if s in failed), STATUS_ERROR)


class NodeAddressTools:
    """`apm_node_addresses` 도구 코어 — 코어(`core`)를 받아 쓴다."""

    def __init__(self, core: ApmCore) -> None:
        self.core = core

    async def apm_node_addresses(
        self,
        source_ids: list[str] | None = None,
        domain_ids: list[int] | None = None,
        instance_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """지정 도메인(비면 인벤토리 전 도메인)의 인스턴스별 노드 주소 — 소스 안 직렬·소스 간 병렬.
        `instance_ids`를 주면 그 인스턴스가 있는 도메인의 그 인스턴스만이다. `domain_ids`·
        `instance_ids` 중 하나라도 주면 호출 시간 예산(`call_budget_seconds`)을 건다."""
        service = self.core.node_address
        if service is None:
            raise ApmError(NOT_CONFIGURED, DISABLED_REASON)
        wanted = (
            None if domain_ids is None else {nonneg_int(d, "domain_ids") for d in domain_ids}
        )
        if instance_ids is not None and len(instance_ids) > MAX_INSTANCE_IDS:
            raise ApmError(
                INVALID_ARGUMENT,
                f"instance_ids는 {MAX_INSTANCE_IDS}개 이하여야 한다({len(instance_ids)}개)",
            )
        picked = (
            None
            if instance_ids is None
            else {str(nonneg_int(i, "instance_ids")) for i in instance_ids}
        )
        usable, statuses, found = await self.core.sources.available(
            self.core.sources.select(source_ids)
        )
        limits = Limits(found)
        partial = partial_of(usable, statuses)
        known = {d["domain_id"] for _, inv in usable for d in inv.domains}
        if wanted is not None and wanted - known:
            missing = ", ".join(map(str, sorted(wanted - known)))
            limits.append(f"[한계] 도메인 {missing}은 조회한 APM 소스의 도메인 목록에 없다")
        plan = [
            (src.source_id, inv, [
                d["domain_id"] for d in inv.domains if wanted is None or d["domain_id"] in wanted
            ])
            for src, inv in usable
        ]
        if picked is not None:  # 그 인스턴스가 있는 도메인만 · 인벤토리에 없는 id는 [한계]
            plan, seen = self._picked_plan(plan, picked)
            if picked - seen:
                missing = shown_ids(sorted(picked - seen, key=int))
                limits.append(
                    f"[한계] 인스턴스 {missing}은 조회한 APM 소스·도메인의 인스턴스 목록에 없다"
                )
        total = sum(len(domains) for _, _, domains in plan)
        if total > MAX_DOMAINS_PER_CALL:  # 앞에서부터 상한까지만 — 나머지는 조회하지 않는다
            budget = MAX_DOMAINS_PER_CALL
            trimmed: list[tuple[str, Inventory, list[int]]] = []
            for source_id, inv, domains in plan:
                trimmed.append((source_id, inv, domains[:budget]))
                budget -= len(trimmed[-1][2])
            plan = trimmed
            partial = True
            limits.append(
                f"[한계] 도메인 {total}개 중 {MAX_DOMAINS_PER_CALL}개만 조회"
                " — domain_ids로 좁혀 다시 호출하라"
            )
        targeted = domain_ids is not None or instance_ids is not None
        budget = service.call_budget_seconds if targeted else None
        pairs = [(source_id, d) for source_id, _, domains in plan for d in domains]
        snapshots, _ = await service.domain_snapshots(pairs, budget_seconds=budget)
        exceeded = sum(1 for s in snapshots.values() if s.status == STATUS_BUDGET_EXCEEDED)
        if exceeded and budget is not None:
            partial = True
            limits.append(
                f"[한계] 노드 주소: 호출 시간 예산 {budget:g}초 안에 도메인"
                f" {exceeded}/{len(pairs)}개를 조회하지 못했다(budget_exceeded)"
                " — domain_ids·instance_ids로 좁혀 다시 호출하라"
            )
        rows: list[dict[str, Any]] = []
        node_sources: list[dict[str, Any]] = []
        for source_id, inv, domains in plan:
            entry = self._source_rows(snapshots, source_id, inv, domains, rows, picked)
            node_sources.append(entry)
        for row in statuses:  # 인벤토리를 받지 못한 소스 — 노드 주소도 조회하지 못했다
            if row["status"] != INVENTORY_OK:
                node_sources.append(
                    {
                        "source_id": row["source_id"],
                        "status": STATUS_ERROR,
                        "reason": f"APM 인스턴스 목록 조회 불가({row['reason']})",
                        "domains": 0,
                        "failed_domains": [],
                        "instances": 0,
                        "instance_status": {},
                    }
                )
        for entry in node_sources:
            if entry["status"] != STATUS_OK:
                partial = True
                limits.append(
                    f"[한계] APM 소스 {entry['source_id']} 노드 주소 {entry['status']}"
                    f" — {entry['reason']}"
                )
        return self.core.ok(
            TOOL,
            rows,
            limits=limits,
            sources=statuses,
            partial=partial,
            node_address_sources=node_sources,
        )

    @staticmethod
    def _picked_plan(
        plan: list[tuple[str, Inventory, list[int]]], picked: set[str]
    ) -> tuple[list[tuple[str, Inventory, list[int]]], set[str]]:
        """조회 계획을 `picked` 인스턴스가 있는 도메인으로 좁힌다 — (좁힌 계획, 찾은 id 키)."""
        seen: set[str] = set()
        narrowed: list[tuple[str, Inventory, list[int]]] = []
        for source_id, inv, domains in plan:
            hit: set[Any] = set()
            for inst in inv.instances:
                key = instance_key(inst["instance_id"])
                if key in picked and inst["domain_id"] in domains:
                    hit.add(inst["domain_id"])
                    seen.add(key)
            narrowed.append((source_id, inv, [d for d in domains if d in hit]))
        return narrowed, seen

    def _source_rows(
        self,
        snapshots: dict[tuple[str, Any], DomainSnapshot],
        source_id: str,
        inv: Inventory,
        domains: list[int],
        rows: list[dict[str, Any]],
        picked: set[str] | None = None,
    ) -> dict[str, Any]:
        """소스 하나 — `domains`의 조회 결과(`snapshots`)를 입력 순서대로 `rows`에 잇고 소스 상태
        항목을 돌려준다(`picked`가 있으면 그 인스턴스 행만)."""
        domain_statuses: list[str] = []
        failed: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        for domain_id in domains:
            snapshot = snapshots[(source_id, domain_id)]
            domain_statuses.append(snapshot.status)
            if snapshot.status != STATUS_OK:
                failed.append({"domain_id": domain_id, "status": snapshot.status})
            for inst in inv.instances:
                if inst["domain_id"] != domain_id:
                    continue
                if picked is not None and instance_key(inst["instance_id"]) not in picked:
                    continue
                row = self._row(source_id, inst, snapshot)
                counts[row["status"]] += 1
                rows.append(row)
        status = _source_status(domain_statuses)
        if status == STATUS_PARTIAL:
            reason = f"도메인 {len(failed)}/{len(domains)}곳 노드 주소 조회 실패" + "".join(
                f" · {s} {n}곳" for s, n in Counter(f["status"] for f in failed).items()
            )
        else:
            reason = _REASONS.get(status, "")
        return {
            "source_id": source_id,
            "status": status,
            "reason": reason,
            "domains": len(domains),
            "failed_domains": failed,
            "instances": sum(counts.values()),
            "instance_status": dict(counts),
        }

    @staticmethod
    def _row(source_id: str, inst: dict[str, Any], snapshot: DomainSnapshot) -> dict[str, Any]:
        """인스턴스 1행 — 도메인 조회가 실패했으면 그 상태 · 웹 목록에 없으면 `not_found`."""
        web = (
            snapshot.rows.get(instance_key(inst["instance_id"]) or "")
            if snapshot.status == STATUS_OK
            else None
        )
        if web is None:
            status = snapshot.status if snapshot.status != STATUS_OK else STATUS_NOT_FOUND
            host_address = ""
            pod: bool | None = True if snapshot.kind == KIND_POD else None
        else:
            status = STATUS_OK
            host_address = web.get("host_address") or ""
            pod = is_pod_row(web)
        return {
            "source_id": source_id,
            "domain_id": inst["domain_id"],
            "instance_id": inst["instance_id"],
            "instance_name": inst["instance_name"],
            "ip": inst.get("ip_address") or (web or {}).get("ip") or "",
            "host_address": host_address,
            "pod": pod,
            "status": status,
        }
