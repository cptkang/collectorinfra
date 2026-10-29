"""인스턴스 목록·이름 검색·업무명 해석 도구군 `InstanceTools`(`apm_instance_map` · plans/130
N-1·N-2 · plans/178 W2에서 `tools.py`의 `ApmTools`에서 옮김).

노드 주소 조회기(plans/199 · D-352)가 있으면 결과 행마다 `host_address`(웹 세션 노드 주소 — 알람
경로와 같은 캐시 · TTL 안 값 · 모르면 빈 값) 칸을 가산한다. 캐시 밖 도메인은 결과가
`MCP_FETCH_DOMAIN_LIMIT`개 도메인 이하일 때만 새로 조회한다(목록 전체 호출이 전 도메인 조회가 되지
않게). 조회기가 없으면(플래그 off) 칸이 없다(현행 동일)."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Iterable
from typing import Any

from apm_gateway.adapters.jennifer.api import XVIEW_WINDOW_MS
from apm_gateway.application.apm_core import ApmCore
from apm_gateway.application.business_map import (
    BusinessHit,
    BusinessMapping,
    business_key,
    cache_fresh,
)
from apm_gateway.application.masking import mask_text
from apm_gateway.application.name_search import (
    domain_order,
    same_name,
    search_instances,
    search_query,
)
from apm_gateway.application.node_address import (
    LOOKUP_TIMEOUT_SECONDS,
    MCP_FETCH_DOMAIN_LIMIT,
    instance_key,
)
from apm_gateway.application.resolver import Inventory
from apm_gateway.application.sources import (
    STATUS_NO_MATCH,
    STATUS_OK,
    JenniferSource,
    name_key,
    partial_of,
)
from apm_gateway.application.tool_common import (
    FILE_ONLY_COLUMNS,
    Limits,
    is_given,
    limit_notice,
    nonneg_int,
)
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import INVALID_ARGUMENT, ApmError

logger = logging.getLogger(__name__)

# plans/130 N-1 — 인스턴스 이름 검색 단계별 신뢰도
_SEARCH_CONFIDENCE = {
    "exact": "high",
    "normalized": "high",
    "prefix": "medium",
    "contains": "medium",
}
# plans/130 N-2 — 업무명 해석 근거(우선순위 순) · 업무 정의 역추적 창(분)
BUSINESS_KINDS = ("business_map", "domain", "business", "instance_text")
BUSINESS_TRACE_MINUTES = 5


class InstanceTools:
    """`apm_instance_map` 도구군 — 코어(`core`)를 받아 쓴다."""

    def __init__(self, core: ApmCore) -> None:
        self.core = core
        # 업무명 해석(plans/130 N-2) — (소스, 도메인) → (읽은 시각, 최근 처리 업무 id → 인스턴스 id)
        self._business_seen: dict[tuple[str, int], tuple[float, dict[str, set[int]]]] = {}

    async def _fill_host_address(self, rows: list[dict[str, Any]]) -> None:
        """노드 주소 칸 가산(조회기가 있을 때만). 조회 실패·상한 초과는 빈 값이다."""
        service = self.core.node_address
        if service is None:
            return
        pairs = list(dict.fromkeys((row["source_id"], row["domain_id"]) for row in rows))
        fetch = len(pairs) <= MCP_FETCH_DOMAIN_LIMIT
        found: dict[tuple[str, Any], dict[str, str]] = {}
        for source_id, domain_id in pairs:
            try:
                found[(source_id, domain_id)] = await asyncio.wait_for(
                    service.domain_addresses(source_id, domain_id, fetch=fetch),
                    LOOKUP_TIMEOUT_SECONDS,
                )
            except Exception as e:  # 부가 칸 — 실패가 목록 응답을 막지 않는다(빈 값)
                logger.warning(
                    "apm_instance_map: 노드 주소 칸 조회 실패(소스 %s 도메인 %s · %s) — 빈 값",
                    source_id,
                    domain_id,
                    type(e).__name__,
                )
                found[(source_id, domain_id)] = {}
        for row in rows:
            addresses = found.get((row["source_id"], row["domain_id"]), {})
            row["host_address"] = addresses.get(instance_key(row["instance_id"]) or "", "")

    async def _domain_descriptions(self, source_ids: set[str]) -> dict[tuple[str, int], str]:
        """도메인 설명(사용자 입력 자유 텍스트 — 마스킹본) — 캐시된 인벤토리에서 읽는다."""
        out: dict[tuple[str, int], str] = {}
        for sid in source_ids:
            inv = await self.core.sources.get(sid).resolver.inventory()
            for d in inv.domains:
                out[(sid, d["domain_id"])] = mask_text(
                    d.get("domain_description", ""), limit=None
                )
        return out

    @staticmethod
    def _instance_row(
        inst: dict[str, Any], domain_description: str, **extra: Any
    ) -> dict[str, Any]:
        return {
            "source_id": inst["source_id"],
            "instance_id": inst["instance_id"],
            "instance_name": inst["instance_name"],
            "domain_id": inst["domain_id"],
            "domain_name": inst["domain_name"],
            "domain_description": domain_description,
            "host_name": inst["host_name"],
            "ip_address": inst["ip_address"],
            "platform": inst["platform"],
            "status": inst["status"],
            "agent_version": inst["agent_version"],
            "config_file_path": inst.get("config_file_path", ""),
            "description": mask_text(inst.get("description", ""), limit=None),
            "instance_oid": inst.get("instance_oid"),
            **extra,
        }

    async def apm_instance_map(
        self,
        hostname: str | None = None,
        source_ids: list[str] | None = None,
        domain_id: int | None = None,
        query: str | None = None,
        business: str | None = None,
    ) -> dict[str, Any]:
        """`domain_id`를 주면 그 도메인의 인스턴스만 돌려준다(hostname과 함께 주면 둘 다 만족).
        `query`를 주면 인스턴스 이름·설명 검색이다(`search_instances` · plans/130 N-1 —
        hostname과 함께 줄 수 없고 `domain_id`와는 AND). `business`를 주면 업무명 → 인스턴스다
        (`_business_search` · N-2 — query·hostname과 함께 줄 수 없고 `domain_id`·`source_ids`와는
        AND)."""
        tool = "apm_instance_map"
        domain = None if domain_id is None else nonneg_int(domain_id, "domain_id")
        text = None if query is None else search_query(query)
        biz = None if business is None else search_query(business, "business")
        if text is not None and is_given(hostname):
            raise ApmError(INVALID_ARGUMENT, "hostname과 query는 함께 줄 수 없다")
        if biz is not None and (text is not None or is_given(hostname)):
            raise ApmError(INVALID_ARGUMENT, "business는 query·hostname과 함께 줄 수 없다")
        self.core.sources.require_configured()
        if hostname and str(hostname).strip():
            res = await self.core.sources.resolve(str(hostname).strip(), None, source_ids)
            descriptions = await self._domain_descriptions({i["source_id"] for i in res.instances})
            rows = []
            for inst in res.instances:
                if domain is not None and inst["domain_id"] != domain:
                    continue
                confidence, reason = res.match_of(inst)
                desc = descriptions.get((inst["source_id"], inst["domain_id"]), "")
                rows.append(
                    self._instance_row(
                        inst,
                        desc,
                        port=inst.get("port"),
                        kind=inst.get("kind"),
                        match_confidence=confidence,
                        match_reason=reason,
                    )
                )
            await self._fill_host_address(rows)
            return self.core.ok(tool, rows, resolution=res, file_only=FILE_ONLY_COLUMNS)
        usable, statuses, found = await self.core.sources.available(
            self.core.sources.select(source_ids)
        )
        limits = Limits(found)
        partial = partial_of(usable, statuses)
        known = sorted({d["domain_id"] for _, inv in usable for d in inv.domains})
        if domain is not None and domain not in known:
            listed = ", ".join(map(str, known)) or "없음"
            limits.append(
                f"[한계] 도메인 {domain}은 조회한 APM 소스의 도메인 목록에 없다({listed})"
            )
            limits.unresolve(
                f"도메인 ID {domain}는 제니퍼 도메인 목록에 없습니다(있는 도메인: {listed})"
            )
        if text is not None:
            return await self._instance_search(
                tool, usable, statuses, limits, partial, text, domain
            )
        if biz is not None:
            return await self._business_search(tool, usable, statuses, limits, partial, biz, domain)
        rows = []
        for src, inv in usable:
            descriptions = {
                d["domain_id"]: mask_text(d.get("domain_description", ""), limit=None)
                for d in inv.domains
            }
            for inst in inv.instances:
                if domain is not None and inst["domain_id"] != domain:
                    continue
                host, conf, reason, _ = src.resolver.reverse(
                    inv, inst["domain_id"], inst["instance_id"]
                )
                rows.append(
                    self._instance_row(
                        inst,
                        descriptions.get(inst["domain_id"], ""),
                        hostname=host,
                        match_confidence=conf,
                        match_reason=reason,
                    )
                )
        await self._fill_host_address(rows)
        return self.core.ok(
            tool,
            rows,
            limits=limits,
            sources=statuses,
            partial=partial,
            file_only=FILE_ONLY_COLUMNS,
        )

    async def _instance_search(
        self,
        tool: str,
        usable: list[tuple[JenniferSource, Inventory]],
        statuses: list[dict[str, Any]],
        limits: Limits,
        partial: bool,
        text: str,
        domain: int | None,
    ) -> dict[str, Any]:
        """`apm_instance_map(query=…)` 봉투 — 행은 목록 행 모양 + 검색 칸(`match_kind`·
        `match_tier`·`search_confidence`) · 행 상한 없음(D-296 ④). 0건이면 유사 후보는 봉투
        `suggestions`에만 싣고(행 아님) 사용자용 한 줄을 남긴다. `search`(채택 단계·단계별 후보
        수)는 감사가 읽는다."""
        found = search_instances(usable, text, domain)
        tier = found.tier or ""  # 행이 있으면 채택 단계가 있다
        rows = []
        descriptions: dict[str, dict[Any, str]] = {}
        for src, inv, inst in found.matches:
            if src.source_id not in descriptions:
                descriptions[src.source_id] = {
                    d["domain_id"]: mask_text(d.get("domain_description", ""), limit=None)
                    for d in inv.domains
                }
            host, conf, reason, _ = src.resolver.reverse(
                inv, inst["domain_id"], inst["instance_id"]
            )
            rows.append(
                self._instance_row(
                    inst,
                    descriptions[src.source_id].get(inst["domain_id"], ""),
                    hostname=host,
                    match_confidence=conf,
                    match_reason=reason,
                    match_kind="instance_name",
                    match_tier=tier,
                    search_confidence=_SEARCH_CONFIDENCE.get(tier, ""),
                )
            )
        hit_sources = {src.source_id for src, _, _ in found.matches}
        for row in statuses:
            if row["status"] == STATUS_OK and row["source_id"] not in hit_sources:
                row["status"] = STATUS_NO_MATCH
        await self._fill_host_address(rows)
        extra: dict[str, Any] = {"search": {"tier": found.tier, "counts": found.counts}}
        if not rows:
            extra["suggestions"] = found.suggestions
            limits.unresolve(
                f"인스턴스 이름 '{mask_text(text, limit=None)}'과(와) 일치하는 인스턴스를"
                " 찾지 못했습니다"
            )
        return self.core.ok(
            tool,
            rows,
            limits=limits,
            sources=statuses,
            partial=partial,
            file_only=FILE_ONLY_COLUMNS,
            **extra,
        )

    async def _business_search(
        self,
        tool: str,
        usable: list[tuple[JenniferSource, Inventory]],
        statuses: list[dict[str, Any]],
        limits: Limits,
        partial: bool,
        text: str,
        domain: int | None,
    ) -> dict[str, Any]:
        """`apm_instance_map(business=…)` 봉투(plans/130 N-2 · G-3 ①). 수동 매핑(B0)이 업무명에
        맞으면 **그것만**, 없으면 도메인 이름(B1)·제니퍼 업무 정의(B2)·인스턴스 이름/설명(B3)의
        합집합이다. 인스턴스마다 1행 — 목록 행 모양 + `match_kind`(첫 근거 · `BUSINESS_KINDS` 순)·
        `match_kinds`(근거 전부)·`business_names`(B2로 맞은 업무 이름) · 상한 없음(D-296 ④). B2
        조회 실패는 부분 결과(`partial`)로 두고 다른 근거 행은 그대로 돌려준다. `business.counts`
        (근거별 인스턴스 수)는 감사가 읽는다."""
        hits: dict[tuple[str, Any, Any], BusinessHit] = {}
        selected = {row["source_id"] for row in statuses}
        mapped = [
            m
            for m in self.core._business_map
            if (m.source_id is None or m.source_id in selected) and m.matches(text)
        ]
        suggestions: list[dict[str, Any]] = []
        if mapped:  # B0 — 정확 일치 이름(`resolve_instance_name` 규칙)만 · 다른 근거 호출 없음
            self._business_map_hits(hits, mapped, usable, domain, limits)
        else:
            suggestions = await self._business_inferred_hits(hits, usable, text, domain, limits)
        ordered = sorted(
            hits.values(),
            key=lambda h: (h.rank, domain_order(h.inst["domain_id"]), h.inst["instance_name"]),
        )
        rows = self._business_rows(ordered)
        await self._fill_host_address(rows)
        hit_sources = {h.src.source_id for h in ordered}
        for row in statuses:
            if row["status"] == STATUS_OK and row["source_id"] not in hit_sources:
                row["status"] = STATUS_NO_MATCH
        counts = {k: sum(1 for h in ordered if k in h.kinds) for k in BUSINESS_KINDS}
        extra: dict[str, Any] = {"business": {"counts": counts}}
        if not rows:
            extra["suggestions"] = suggestions
            limits.unresolve(
                f"업무명 '{mask_text(text, limit=None)}'에 해당하는 APM 인스턴스를 찾지 못했습니다"
            )
        return self.core.ok(
            tool,
            rows,
            limits=limits,
            sources=statuses,
            partial=partial or limits.partial,
            file_only=FILE_ONLY_COLUMNS,
            **extra,
        )

    @staticmethod
    def _add_business_hit(
        hits: dict[tuple[str, Any, Any], BusinessHit],
        rank: int,
        src: JenniferSource,
        inv: Inventory,
        inst: dict[str, Any],
        kind: str,
        names: Iterable[str] = (),
        tier: str = "",
    ) -> None:
        """업무명 해석 후보를 (소스, 도메인, 인스턴스) 1건으로 모은다 — 근거·업무 이름을 더하고
        이름 검색 단계(`tier`)가 있으면 그것으로 바꾼다. 첫 등록의 소스 순위를 유지한다."""
        key = (src.source_id, inst["domain_id"], inst["instance_id"])
        hit = hits.get(key)
        if hit is None:
            hit = hits[key] = BusinessHit(rank, src, inv, inst, set(), [])
        hit.kinds.add(kind)
        hit.business_names.extend(n for n in names if n not in hit.business_names)
        if tier:
            hit.tier = tier

    def _business_map_hits(
        self,
        hits: dict[tuple[str, Any, Any], BusinessHit],
        mapped: list[BusinessMapping],
        usable: list[tuple[JenniferSource, Inventory]],
        domain: int | None,
        limits: Limits,
    ) -> None:
        """B0 — 수동 매핑의 인스턴스 이름과 정확 일치하는 인스턴스를 모은다. 인벤토리에 없는
        이름은 `[한계]`로 남긴다(도메인 필터로 빠진 것은 찾은 것으로 친다)."""
        missing: list[str] = []
        for m in mapped:
            for name in m.instances:
                key, found = name_key(name), False
                for rank, (src, inv) in enumerate(usable):
                    if m.source_id is not None and src.source_id != m.source_id:
                        continue
                    for inst in inv.instances:
                        if name_key(inst["instance_name"]) != key:
                            continue
                        found = True
                        if domain is None or inst["domain_id"] == domain:
                            self._add_business_hit(hits, rank, src, inv, inst, "business_map")
                if not found and name not in missing:
                    missing.append(name)
        if missing:
            limits.append(
                "[한계] 업무 수동 매핑(business_map)의 인스턴스 이름을 조회한 APM 인벤토리에서"
                f" 찾지 못했다: {', '.join(missing)}"
            )

    async def _business_inferred_hits(
        self,
        hits: dict[tuple[str, Any, Any], BusinessHit],
        usable: list[tuple[JenniferSource, Inventory]],
        text: str,
        domain: int | None,
        limits: Limits,
    ) -> list[dict[str, Any]]:
        """수동 매핑이 없을 때 — 도메인 이름(B1)·제니퍼 업무 정의(B2)·인스턴스 이름/설명(B3)의
        합집합을 모은다. 반환은 B3 이름 검색의 유사 후보(`suggestions`)."""
        for rank, (src, inv) in enumerate(usable):  # B1 — 도메인 이름 일치 → 전 인스턴스
            named = {
                d["domain_id"]
                for d in inv.domains
                if same_name(str(d.get("domain_name") or ""), text)
                and (domain is None or d["domain_id"] == domain)
            }
            for inst in inv.instances:
                if inst["domain_id"] in named:
                    self._add_business_hit(hits, rank, src, inv, inst, "domain")
        traced, defined = await self._business_trace(usable, text, domain, limits)
        for rank, src, inv, inst, names in traced:
            self._add_business_hit(hits, rank, src, inv, inst, "business", names)
        if defined:
            limits.append(
                "[한계] 업무 정의 근거는 최근 처리한 인스턴스만 찾는다(현재 액티브 서비스 +"
                f" 최근 {BUSINESS_TRACE_MINUTES}분 트랜잭션)"
            )
        searched = search_instances(usable, text, domain)  # B3 — W1 이름·설명 검색
        rank_of = {src.source_id: i for i, (src, _) in enumerate(usable)}
        tier = searched.tier or ""
        for src, inv, inst in searched.matches:
            self._add_business_hit(
                hits, rank_of[src.source_id], src, inv, inst, "instance_text", tier=tier
            )
        return searched.suggestions

    def _business_rows(self, ordered: list[BusinessHit]) -> list[dict[str, Any]]:
        """정렬한 업무명 후보 → 목록 행(`_instance_row` + 근거 칸). 도메인 설명은 소스마다
        한 번 마스킹한다."""
        rows = []
        descriptions: dict[str, dict[Any, str]] = {}
        for hit in ordered:
            src, inv, inst = hit.src, hit.inv, hit.inst
            if src.source_id not in descriptions:
                descriptions[src.source_id] = {
                    d["domain_id"]: mask_text(d.get("domain_description", ""), limit=None)
                    for d in inv.domains
                }
            host, conf, reason, _ = src.resolver.reverse(
                inv, inst["domain_id"], inst["instance_id"]
            )
            kinds = [k for k in BUSINESS_KINDS if k in hit.kinds]
            rows.append(
                self._instance_row(
                    inst,
                    descriptions[src.source_id].get(inst["domain_id"], ""),
                    hostname=host,
                    match_confidence=conf,
                    match_reason=reason,
                    match_kind=kinds[0],
                    match_kinds=kinds,
                    match_tier=hit.tier if "instance_text" in hit.kinds else "",
                    search_confidence="high" if kinds[0] == "business_map" else "medium",
                    business_names=list(hit.business_names),
                )
            )
        return rows

    async def _business_trace(
        self,
        usable: list[tuple[JenniferSource, Inventory]],
        text: str,
        domain: int | None,
        limits: Limits,
    ) -> tuple[list[tuple[int, JenniferSource, Inventory, dict[str, Any], list[str]]], bool]:
        """B2 — 도메인마다 업무 정의를 읽어 이름·설명(마스킹본)에 업무명이 든 것(대소문자 무시)을
        찾고, 그 업무 id를 최근 처리한 인스턴스로 역추적한다(정의에 인스턴스 목록이 없다 —
        D-290). (후보 (소스 순위, 소스, 인벤토리, 인스턴스, 맞은 업무 이름), 정의가 하나라도
        맞았는가). 조회 실패는 단위마다 `[한계]`·부분 결과다."""
        q = text.casefold()
        now = self.core.clock()
        targets = [
            (rank, src, inv, d["domain_id"])
            for rank, (src, inv) in enumerate(usable)
            for d in inv.domains
            if isinstance(d.get("domain_id"), int)
            and d["domain_id"] not in inv.unavailable
            and (domain is None or d["domain_id"] == domain)
        ]
        expect_calls(
            sum(
                1
                for _, src, _, did in targets
                if not cache_fresh(self.core._business_defs.get((src.source_id, did)), now)
            )
        )
        matched: list[tuple[int, JenniferSource, Inventory, int, dict[str, str]]] = []
        for rank, src, inv, did in targets:
            names: dict[str, str] = {}
            for b in await self.core.business_defs_of(src, did, now, limits) or []:
                if b["business_id"] is not None and (
                    q in b["business_name"].casefold()
                    or q in mask_text(b["business_description"], limit=None).casefold()
                ):
                    names.setdefault(business_key(b["business_id"]), b["business_name"])
            if names:
                matched.append((rank, src, inv, did, names))
        per_domain = 1 + math.ceil(BUSINESS_TRACE_MINUTES * 60_000 / XVIEW_WINDOW_MS)
        expect_calls(
            per_domain
            * sum(
                1
                for _, src, _, did, _ in matched
                if not cache_fresh(self._business_seen.get((src.source_id, did)), now)
            )
        )
        out: list[tuple[int, JenniferSource, Inventory, dict[str, Any], list[str]]] = []
        for rank, src, inv, did, names in matched:
            seen = await self._business_seen_of(src, did, now, limits)
            by_inst: dict[int, list[str]] = {}
            for bid, bname in names.items():
                for iid in seen.get(bid, ()):
                    by_inst.setdefault(iid, []).append(bname)
            for inst in inv.instances:
                if inst["domain_id"] == did and inst["instance_id"] in by_inst:
                    out.append((rank, src, inv, inst, by_inst[inst["instance_id"]]))
        return out, bool(matched)

    async def _business_seen_of(
        self, src: JenniferSource, domain_id: int, now: float, limits: Limits
    ) -> dict[str, set[int]]:
        """도메인에서 최근 처리한 업무 id → 인스턴스 id — 현재 액티브 서비스 + 최근
        `BUSINESS_TRACE_MINUTES`분 트랜잭션(제니퍼 1분 창으로 나눠 묻는다 · `_xview`와 같은 경계).
        전부 성공했을 때만 TTL 캐시한다(일부 실패는 `[한계]`(부분) · 다음 호출이 다시 묻는다)."""
        key = (src.source_id, domain_id)
        cached = self._business_seen.get(key)
        if cached is not None and cache_fresh(cached, now):
            return cached[1]
        where = self.core.sources.where(src.source_id, domain_id)
        seen: dict[str, set[int]] = {}
        complete = True

        def note(records: list[dict[str, Any]]) -> None:
            for rec in records:
                if rec.get("instance_id") is None:
                    continue
                for bid in rec.get("business_ids") or []:
                    seen.setdefault(business_key(bid), set()).add(rec["instance_id"])

        try:
            note(await src.api.active_services(domain_id, None))
        except ApmError as e:
            notice = limit_notice(e, "업무 역추적 — 액티브 서비스", f"({where})")
            complete = False
            limits.fail(notice)
        end_ms = int(now * 1000)
        t = end_ms - BUSINESS_TRACE_MINUTES * 60_000
        while t < end_ms:
            chunk_end = min(t + XVIEW_WINDOW_MS, end_ms)
            query_end = chunk_end if chunk_end == end_ms else chunk_end - 1
            try:
                note(await src.api.transactions(domain_id, None, t, query_end))
            except ApmError as e:
                notice = limit_notice(e, "업무 역추적 — 트랜잭션", f"({where})")
                complete = False
                limits.fail(notice)
                break
            t = chunk_end
        if complete:
            self._business_seen[key] = (now, seen)
        return seen
