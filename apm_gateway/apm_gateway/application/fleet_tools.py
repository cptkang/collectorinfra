"""전 대상 순위·이벤트 — `apm_fleet` 코어 (plans/134 W3 N-10 · 계약 A-6 · D-299 ② · D-296 ④).

서버를 정하지 않은 질문(「전체 중 가장 느린 WAS」·「전부의 이벤트」)을 범위 안 (소스, 도메인)
전부로 답한다. 식별은 (`source_id`, `domain_id`, `instance_id`)다(D-287). 범위는
`service`(서비스 이름 하나 또는 목록)·`domain_id`로 좁힌다(둘 다 = AND) — 서비스 해석·미해결
고지·후보(`suggestions`)는 `apm_service_status`와 같은 코드(`ScopeTools.service_scope`)다. 이름을
줬는데 하나도 못 찾으면 데이터 API 0회 · 행 0(전 도메인으로 넓히지 않는다 · D-290 ⑥).

- **ranking**: (소스, 도메인)마다 실시간 인스턴스 1호출(인스턴스 지정 없음 · 호출 계획 = 도메인 수)
  → 전 인스턴스를 **모은 뒤** 지표로 정렬해 상위 `n`(또는 `full` 전부). 지표 값이 없는 인스턴스는
  순위에서 빼고 센다(`instances_unranked`). 조회 실패 도메인·소스가 하나라도 있으면 `partial` +
  `provisional`(잠정 순위) + `[한계]` — 실패한 도메인의 인스턴스는 순위에 없다.
- **events**: (소스, 도메인)마다 창 `[시작, 끝]`의 이벤트 — 조회용 이벤트 버퍼(폴러가 받은 것)가
  확정한 부분은 버퍼, 덮지 못한 부분은 이벤트 API 1호출(덮지 못한 구간 전체를 감싸는 한 구간)로
  보충한다. 합칠 때는 **버퍼↔API 겹침만** 지운다(합치기 키 = 멱등 키 칸 + 레벨·값·메시지 — 같은
  출처 응답 안의 이벤트는 합치지 않는다). API 응답 모양을 알아보지 못하면 그 도메인은 실패다(0건이
  아니다). 레벨·`level_mode`·오류 유형은
  `apm_events`와 같은 검증·거르기 코드를 쓴다 — 버퍼 행이 레벨 필터 전 전부이므로 API에도 레벨을
  넘기지 않고 받은 뒤 거른다. 실패 도메인은 0건이 아니라 `domains_failed`·`partial`·`[한계]`로
  드러낸다. 봉투 `coverage`가 답한 출처(버퍼·API·섞임·실패) 도메인 수다.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from apm_gateway.adapters.jennifer.fields import METRIC_FIELDS, REALTIME_EXTRA_FIELDS
from apm_gateway.application.event_buffer import EventBuffer, buffer_row, merge_key
from apm_gateway.application.masking import mask_text
from apm_gateway.application.resolver import Inventory
from apm_gateway.application.scope_tools import ScopeTools, ServiceScope
from apm_gateway.application.sources import STATUS_NO_MATCH, STATUS_OK, JenniferSource
from apm_gateway.application.tools import (
    EVENTS_DEFAULT_MINUTES,
    FILE_ONLY_COLUMNS,
    VISIT_HIT_NOTE,
    ApmTools,
    _check_n,
)
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import API_ERROR, CONTRACT_VIOLATION, INVALID_ARGUMENT, ApmError

# 순위 지표 = 실시간 인스턴스 수치 칸의 중립 이름 전부(MCP 스키마 enum · 본체 레지스트리와 대조)
RANKING_METRICS: tuple[str, ...] = (*METRIC_FIELDS, *REALTIME_EXTRA_FIELDS)
DEFAULT_RANKING_METRIC = "response_time_avg_ms"
FLEET_MODES = ("ranking", "events")
ORDERS = ("desc", "asc")
RANKING_DEFAULT_N = 10
_CURRENT_ONLY = "[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다"
_FAILED_SHOWN = 20  # 실패 위치 고지에 싣는 수(나머지는 「외 n곳」 — 데이터가 아니라 고지 문구다)

Group = tuple[JenniferSource, Inventory, dict[str, Any]]


def _shown(items: list[str]) -> str:
    more = f" 외 {len(items) - _FAILED_SHOWN}곳" if len(items) > _FAILED_SHOWN else ""
    return ", ".join(items[:_FAILED_SHOWN]) + more


class FleetTools:
    """`apm_fleet` 코어 — 도구 코어(`ApmTools`)의 소스·창·봉투·이벤트 행 규칙을 그대로 쓴다."""

    def __init__(self, core: ApmTools, event_buffer: EventBuffer | None = None) -> None:
        self.core = core
        self.event_buffer = event_buffer
        self._scope = ScopeTools(core)

    async def _groups(
        self, service: Any, domain_id: Any, source_ids: list[str] | None
    ) -> tuple[ServiceScope, list[Group], list[str]]:
        """범위 안 (소스, 도메인) → (범위, 묶음, 조회 불가 소스 id). `service`(서비스 이름 하나 또는
        목록)·`domain_id`로 좁히면 그 도메인만(둘 다 = AND) — 해석·미해결 고지·후보는
        `apm_service_status`와 같은 서비스 범위 해석(`ScopeTools.service_scope`)이다. 좁혔는데
        0곳이면 `scope.blocked` — 호출자는 데이터 API를 부르지 않는다(전 도메인으로 넓히지
        않는다)."""
        scope = await self._scope.service_scope(service, domain_id, source_ids)
        inventories: dict[str, Inventory] = {}
        groups: list[Group] = []
        for unit in scope.units:
            sid = unit.source.source_id
            if sid not in inventories:  # 범위 해석이 읽은 캐시 명단(역정합 hostname용)
                inventories[sid] = await unit.source.resolver.inventory()
            groups.append(
                (
                    unit.source,
                    inventories[sid],
                    {"domain_id": unit.domain_id, "domain_name": unit.domain_name},
                )
            )
        # 서비스 이름이 맞지 않은 소스(no_match)는 조회 불가가 아니다
        down = [
            row["source_id"]
            for row in scope.statuses
            if row["status"] not in (STATUS_OK, STATUS_NO_MATCH)
        ]
        return scope, groups, down

    def _all_failed(self, failures: list[tuple[str, ApmError]]) -> ApmError:
        return self.core._all_failed(failures)

    # ── ranking ─────────────────────────────────────────────

    async def ranking(
        self,
        metric: str | None = None,
        order: str | None = None,
        n: int | None = None,
        full: bool = False,
        domain_id: int | None = None,
        source_ids: list[str] | None = None,
        *,
        ignored: tuple[str, ...] = (),
        service: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """전 인스턴스 실시간 순위. `service`·`domain_id`로 도메인을 좁힌다(`_groups`). `ignored`는
        이 모드가 쓰지 않는데 받은 인자 이름이다."""
        tool = "apm_fleet"
        name = str(metric or DEFAULT_RANKING_METRIC).strip()
        if name not in RANKING_METRICS:
            raise ApmError(
                INVALID_ARGUMENT,
                f"metric은 실시간 인스턴스 지표 중 하나여야 한다: {metric!r}"
                f"(허용값: {', '.join(RANKING_METRICS)})",
            )
        direction = str(order or "desc").strip().lower()
        if direction not in ORDERS:
            raise ApmError(INVALID_ARGUMENT, f"order는 desc·asc 중 하나여야 한다: {order!r}")
        top_n = _check_n(n, default=RANKING_DEFAULT_N)
        self.core.sources.require_configured()
        scope, groups, down = await self._groups(service, domain_id, source_ids)
        limits = scope.limits
        limits.insert(0, _CURRENT_ONLY)
        if ignored:
            limits.append(
                f"[한계] mode ranking은 인자 {'·'.join(ignored)}를 쓰지 않는다 — 빼고 조회했다"
            )
        expect_calls(len(groups))  # 좁혔는데 0곳(scope.blocked)이면 0 — 데이터 API를 부르지 않는다
        failures: list[tuple[str, ApmError]] = []
        records: list[tuple[Group, dict[str, Any]]] = []
        for group in groups:
            src, _inv, d = group
            sid, did = src.source_id, d["domain_id"]
            try:
                found = await src.api.realtime(did)
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                failures.append((self.core.sources.where(sid, did), e))
                continue
            for rec in found:
                if rec.get("domain_id") is None:
                    rec = {**rec, "domain_id": did}
                records.append((group, rec))
        if failures and len(failures) == len(groups):
            raise self._all_failed(failures)
        rank_of = {sid: i for i, sid in enumerate(self.core.sources.ids)}
        seen: set[tuple[str, Any, Any]] = set()
        ranked: list[tuple[Group, dict[str, Any]]] = []
        unranked = 0
        for group, rec in records:
            ident = (group[0].source_id, rec.get("domain_id"), rec.get("instance_id"))
            if ident in seen:  # 같은 인스턴스를 두 번 받았다 — 한 번만 센다
                continue
            seen.add(ident)
            value = rec.get(name)
            if value is None or (isinstance(value, float) and math.isnan(value)):
                unranked += 1
                continue
            ranked.append((group, rec))
        sign = -1 if direction == "desc" else 1
        ranked.sort(
            key=lambda gr: (
                sign * gr[1][name],
                rank_of.get(gr[0][0].source_id, len(rank_of)),
                gr[1].get("domain_id") or 0,
                gr[1].get("instance_id") or 0,
            )
        )
        selected = ranked if full or top_n is None else ranked[:top_n]
        rows = [
            self._ranking_row(position, group, rec, name)
            for position, (group, rec) in enumerate(selected, 1)
        ]
        provisional = bool(failures or down)
        if provisional:
            parts = []
            if failures:
                parts.append(f"도메인 {len(failures)}곳({_shown([w for w, _ in failures])})")
            if down:
                parts.append(f"소스 {len(down)}개({', '.join(down)})")
            limits.fail(f"[한계] 잠정 순위 — 조회 실패 {' · '.join(parts)}은 순위에 없습니다")
        if rows:  # 행마다 실시간 전 칸(방문·호출 수 포함)을 싣는다
            limits.append(VISIT_HIT_NOTE)
        summary = {
            "metric": name,
            "order": direction,
            "domains_total": len(groups),
            "domains_ok": len(groups) - len(failures),
            "domains_failed": len(failures),
            "instances_total": len(seen),
            "instances_ranked": len(ranked),
            "instances_unranked": unranked,
        }
        return self._scope.envelope(  # 못 찾은 서비스 이름이 있으면 suggestions(같은 모양)
            tool,
            rows,
            scope,
            file_only=FILE_ONLY_COLUMNS,
            mode="ranking",
            provisional=provisional,
            summary=summary,
        )

    @staticmethod
    def _ranking_row(
        position: int, group: Group, rec: dict[str, Any], metric: str
    ) -> dict[str, Any]:
        src, inv, d = group
        hostname, _conf, _reason, _inst = src.resolver.reverse(
            inv, rec.get("domain_id"), rec.get("instance_id"), rec.get("instance_name", "")
        )
        return {
            "rank": position,
            "source_id": src.source_id,
            "domain_id": rec.get("domain_id"),
            "domain_name": d.get("domain_name", ""),
            "instance_id": rec.get("instance_id"),
            "instance_name": rec.get("instance_name", ""),
            "hostname": hostname,
            "metric": metric,
            "value": rec.get(metric),
            **{field: rec.get(field) for field in RANKING_METRICS},
            "service_rate_by_range": rec.get("service_rate_by_range"),
            "instance_description": mask_text(rec.get("instance_description", ""), limit=None),
            "instance_oid": rec.get("instance_oid"),
        }

    # ── events ──────────────────────────────────────────────

    async def events(
        self,
        level: str | None = None,
        level_mode: str | None = None,
        error_type: str | None = None,
        n: int | None = None,
        full: bool = False,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        domain_id: int | None = None,
        source_ids: list[str] | None = None,
        *,
        ignored: tuple[str, ...] = (),
        service: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """전 도메인 이벤트(창 기본 최근 30분 · 시각 내림차순 · 상위 `n`/전부). `service`·
        `domain_id`로 도메인을 좁힌다(버퍼·API 경로 모두 그 도메인만)."""
        tool = "apm_fleet"
        core = self.core
        lvl, mode, etype, _ = core._event_options(level, level_mode, error_type, None)
        top_n = _check_n(n, default=None)
        window = core.window(
            reference_time, lookback_minutes, default_minutes=EVENTS_DEFAULT_MINUTES
        )
        assert window is not None
        core.sources.require_configured()
        scope, groups, down = await self._groups(service, domain_id, source_ids)
        limits = scope.limits
        if ignored:
            limits.append(
                f"[한계] mode events는 인자 {'·'.join(ignored)}를 쓰지 않는다 — 빼고 조회했다"
            )
        buffer = self.event_buffer
        if buffer is not None:
            buffer.sweep()
        start, end = window.start_ms, window.end_ms
        plans: list[tuple[Group, list[tuple[int, int]]]] = [
            (
                g,
                buffer.gaps(g[0].source_id, g[2]["domain_id"], start, end)
                if buffer
                else [(start, end)],
            )
            for g in groups
        ]
        expect_calls(sum(1 for _, gaps in plans if gaps))
        coverage = {"domains_total": len(groups), "from_buffer": 0, "from_api": 0, "mixed": 0,
                    "failed": 0}
        failures: list[tuple[str, ApmError]] = []
        rows: list[dict[str, Any]] = []
        for (src, inv, d), gaps in plans:
            sid, did = src.source_id, d["domain_id"]
            got: list[dict[str, Any]] = []
            if gaps:
                lo, hi = gaps[0][0], gaps[-1][1]
                try:
                    fetched, known = await src.api.events_checked(did, None, lo, hi)
                    if not known:  # 모르는 모양을 「확인한 0건」으로 세지 않는다(R34-3)
                        raise ApmError(
                            API_ERROR,
                            "이벤트 응답 모양이 예상과 다르다({result: [...]} 봉투가 아님)",
                        )
                except ApmError as e:
                    if e.code == CONTRACT_VIOLATION:
                        raise
                    failures.append((core.sources.where(sid, did), e))
                    coverage["failed"] += 1
                    continue
                got = [buffer_row(sid, did, ev) for ev in fetched]
                if buffer is not None and (lo, hi) != (start, end):
                    got += self._buffer_side(
                        got,
                        [
                            r for r in buffer.rows(sid, did, start, end)
                            if r["time_ms"] < lo or r["time_ms"] > hi
                        ],
                    )
                    coverage["mixed"] += 1
                else:
                    coverage["from_api"] += 1
            else:
                got = list(buffer.rows(sid, did, start, end)) if buffer else []
                coverage["from_buffer"] += 1
            rows += [self._event_row(src, inv, d, r) for r in got]
        if failures and len(failures) == len(groups):
            raise self._all_failed(failures)
        events = core._filter_events(rows, lvl, mode, etype)
        rank_of = {sid: i for i, sid in enumerate(core.sources.ids)}
        events.sort(
            key=lambda r: (
                -(r.get("time_ms") or 0),
                rank_of.get(r["source_id"], len(rank_of)),
                r.get("domain_id") or 0,
                r.get("instance_id") or 0,
                r.get("event_type") or "",
                merge_key(r),
            )
        )
        selected = events if full or top_n is None else events[:top_n]
        if failures:
            limits.fail(
                f"[한계] 이벤트 조회 실패 도메인 {len(failures)}곳"
                f"({_shown([w for w, _ in failures])}) — 0건이 아니라 확인하지 못했다"
            )
        if down:
            limits.fail(
                f"[한계] 조회 불가 소스 {len(down)}개({', '.join(down)})의 이벤트는 확인하지 못했다"
            )
        summary = {
            "domains_total": len(groups),
            "domains_ok": len(groups) - len(failures),
            "domains_failed": len(failures),
            "events_total": len(events),
        }
        return self._scope.envelope(
            tool,
            selected,
            scope,
            window=window,
            file_only=FILE_ONLY_COLUMNS,
            mode="events",
            summary=summary,
            coverage=coverage,
        )

    @staticmethod
    def _buffer_side(
        api_rows: list[dict[str, Any]], buffer_rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """버퍼 쪽 행 중 API 쪽과 겹치는 것만 뺀다(합치기 키별 건수만큼 — 같은 출처 안의 행은
        합치지 않는다)."""
        remaining = Counter(merge_key(r) for r in api_rows)
        kept = []
        for row in buffer_rows:
            key = merge_key(row)
            if remaining[key] > 0:
                remaining[key] -= 1
                continue
            kept.append(row)
        return kept

    @staticmethod
    def _event_row(
        src: JenniferSource, inv: Inventory, d: dict[str, Any], row: dict[str, Any]
    ) -> dict[str, Any]:
        """이벤트 행(버퍼·API 공통) + 도메인 이름(응답에 없으면 인벤토리) + 역정합 hostname."""
        hostname, _conf, _reason, _inst = src.resolver.reverse(
            inv, row.get("domain_id"), row.get("instance_id"), row.get("instance_name", "")
        )
        return {
            **row,
            "domain_name": row.get("domain_name") or d.get("domain_name", ""),
            "hostname": hostname,
        }
