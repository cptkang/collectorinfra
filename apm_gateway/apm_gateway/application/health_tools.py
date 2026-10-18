"""런타임·자원 풀·게이트웨이 상태 도구군 `HealthTools`(`apm_runtime_health`·`apm_resource_pool`·
`gateway_health` · plans/178 W2에서 `tools.py`의 `ApmTools`에서 옮김)."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from apm_gateway.adapters.jennifer.allowlist import ALLOWED
from apm_gateway.adapters.jennifer.api import JenniferApi
from apm_gateway.application.apm_core import ApmCore
from apm_gateway.application.resolver import SHORT_CACHE_SECONDS
from apm_gateway.application.sources import (
    JenniferSource,
)
from apm_gateway.application.tool_common import (
    HEALTH_DEFAULT_LOOKBACK,
    TREND_INTERVAL_MINUTE,
    InstKey,
    Limits,
    group_by_domain,
    inst_key,
    inst_meta,
    limit_notice,
    positive_int,
    tag_records,
)
from apm_gateway.domain import signals as sig
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import SOURCE_UNAVAILABLE, ApmError

_HEALTH_CACHE_SECONDS = SHORT_CACHE_SECONDS
# plans/134 W2(N-5~N-7)
DEFAULT_TREND_METRICS = ("heap_used_mb", "heap_committed_mb", "gc_time_usage_pct")


class HealthTools:
    """런타임·자원 풀·게이트웨이 상태 도구군 — 코어(`core`)를 받아 쓴다."""

    def __init__(self, core: ApmCore) -> None:
        self.core = core
        self._health_cache: tuple[float, dict[str, Any]] | None = None

    async def apm_runtime_health(
        self,
        hostname: str | None = None,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
        metrics: list[str] | None = None,
        interval_minute: int | None = None,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        tool = "apm_runtime_health"
        interval = positive_int(interval_minute, "interval_minute", TREND_INTERVAL_MINUTE)
        requested = self.core.metric_names(metrics)
        res = await self.core.resolve(hostname, instance_id, source_ids, instance_name)
        # 지표·간격을 주면 추세가 목적이다 — 구간이 없으면 기본 구간으로 본다(침묵 무시 금지).
        trend_wanted = bool(requested) or interval_minute is not None
        window = self.core.window(
            reference_time,
            lookback_minutes,
            default_minutes=HEALTH_DEFAULT_LOOKBACK if trend_wanted else None,
        )
        limits = Limits()
        current: dict[InstKey, dict[str, Any]] = {}
        if window is None or window.end_is_now:
            current = await self.core.realtime(res, limits)
        else:
            limits.append(
                "[한계] 과거 기준시각 — 실시간 스냅샷 생략(현재값은 사건 시점 증거가 아니다)"
            )
        trends: dict[InstKey, dict[str, Any]] = {}
        if window is not None:
            plan = None
            if requested:
                plan = await self.core.metric_plan(
                    sorted({i["source_id"] for i in res.instances}),
                    requested,
                    "instance",
                    limits,
                    all_unknown_raises=False,
                )
                if plan is None:  # 요청 지표를 모두 모른다 — 현재값은 버리지 않고 기본 추세로
                    limits.append(
                        "[한계] 요청한 지표를 모두 카탈로그에서 찾지 못해 기본 추세 지표"
                        f"({'·'.join(DEFAULT_TREND_METRICS)})로 조회했다"
                    )
                    limits.unresolve(
                        f"요청한 지표 {', '.join(requested)}를 지표 목록에서 찾지 못해 기본 지표"
                        f"({', '.join(DEFAULT_TREND_METRICS)})로 조회했습니다"
                    )
            if plan is None:  # 기본 3종(중립 이름 — 식별자 매핑이 정해져 있다)
                default = [
                    (mid, m) for m in DEFAULT_TREND_METRICS if (mid := JenniferApi.metric_id(m))
                ]
                plan = {i["source_id"]: default for i in res.instances}
            if interval_minute is not None:
                limits.append(
                    "[한계] interval_minute 허용값은 미공개다(W10) — 서버가 거부하면 그 사유를"
                    " 그대로 싣는다"
                )
            expect_calls(sum(len(plan.get(i["source_id"], [])) for i in res.instances))
            for inst in res.instances:  # 정합된 인스턴스 전부(종전 2개 상한 제거 · W1 N-4)
                trend: dict[str, list[dict[str, Any]]] = {}
                for metric_id, label in plan.get(inst["source_id"], []):
                    try:
                        trend[label] = await self.core.api(
                            inst["source_id"]
                        ).instance_metric_series(
                            inst["domain_id"],
                            inst["instance_id"],
                            metric_id,
                            interval,
                            window.start_ms,
                            window.end_ms,
                        )
                    except ApmError as e:
                        limits.fail(
                            limit_notice(
                                e,
                                f"추세 {label}",
                                f"(인스턴스 {self.core.inst_label(inst_key(inst))})",
                                self.core.reason_tail(e),
                            )
                        )
                if trend:
                    trends[inst_key(inst)] = trend
        if not current and not trends:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits) or "APM 런타임 데이터 조회 실패")
        rows: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        for inst in res.instances:
            key = inst_key(inst)
            cur = current.get(key)
            row = inst_meta(inst)
            if cur:
                for field_name in (
                    "heap_used_mb",
                    "heap_committed_mb",
                    "non_heap_used_mb",
                    "gc_time_usage_pct",
                    "process_cpu_pct",
                    "process_memory_mb",
                    "thread_current",
                    "thread_daemon",
                    "thread_started",
                    "socket_count",
                    "file_count",
                    "collection_count",
                ):
                    row[field_name] = cur.get(field_name)
                used, committed = cur.get("heap_used_mb"), cur.get("heap_committed_mb")
                row["heap_usage_ratio"] = (
                    (used / committed) if used is not None and committed else None
                )
            if key in trends:
                row["trend"] = trends[key]
            rows.append(row)
            signals += tag_records(
                sig.judge_runtime(cur, trends.get(key), self.core.th, instance_id=key[1]), key[0]
            )
        return self.core.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            was_signals=signals,
            limits=limits,
            partial=limits.partial,
        )

    async def apm_resource_pool(
        self,
        hostname: str | None = None,
        instance_id: int | None = None,
        source_ids: list[str] | None = None,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        tool = "apm_resource_pool"
        res = await self.core.resolve(hostname, instance_id, source_ids, instance_name)
        limits = Limits(
            [
                "[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다",
                "[한계] WAS 스레드 풀 상한 필드 없음 — thread_current(JVM 전체)·active_services로"
                " 근사",
            ]
        )
        current = await self.core.realtime(res, limits)
        services: dict[InstKey, list[dict[str, Any]]] = {}
        active_ok = False
        groups = group_by_domain(res)
        expect_calls(len(groups))
        for (sid, domain_id), ids in groups.items():
            try:
                for svc in tag_records(
                    await self.core.api(sid).active_services(domain_id, ids), sid
                ):
                    services.setdefault(inst_key(svc), []).append(svc)
                active_ok = True
            except ApmError as e:
                where = self.core.sources.where(sid, domain_id)
                limits.fail(limit_notice(e, "액티브 서비스", f"({where})"))
        if not current and not active_ok:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        rows: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        for inst in res.instances:
            key = inst_key(inst)
            cur = current.get(key) or {}
            svcs = services.get(key, [])
            active = cur.get("db_pool_active")
            configured = cur.get("db_pool_configured_avg")
            rows.append(
                {
                    **inst_meta(inst),
                    "db_pool_active": active,
                    "db_pool_idle_avg": cur.get("db_pool_idle_avg"),
                    "db_pool_configured_avg": configured,
                    "db_pool_usage_ratio": (active / configured)
                    if active is not None and configured
                    else None,
                    "thread_current": cur.get("thread_current"),
                    "active_services": cur.get("active_services") if cur else len(svcs),
                    "active_by_running_mode": dict(
                        Counter(s["running_mode"] or "(없음)" for s in svcs)
                    ),
                    "active_by_datasource": dict(
                        Counter(s["datasource"] for s in svcs if s["datasource"])
                    ),
                }
            )
            signals += tag_records(sig.judge_pool(cur, self.core.th, instance_id=key[1]), key[0])
            signals += tag_records(
                sig.judge_active_services(svcs, self.core.th, instance_id=key[1], source_tool=tool),
                key[0],
            )
        return self.core.ok(
            tool, rows, resolution=res, was_signals=signals, limits=limits, partial=limits.partial
        )

    async def _source_health(self, src: JenniferSource) -> tuple[dict[str, Any], list[str]]:
        body: dict[str, Any] = {
            "source_id": src.source_id,
            "status": "ok",
            "jennifer_configured": src.api.configured,
            "jennifer_reachable": False,
            "domain_count": None,
            "allowlist_size": len(ALLOWED),
        }
        limits: list[str] = []
        where = "" if len(self.core.sources) == 1 else f"소스 {src.source_id} "
        try:
            domains = await src.api.domains()
            body["jennifer_reachable"] = True
            body["domain_count"] = len(domains)
            if not domains:
                body["status"] = "degraded"
                limits.append(f"[한계] {where}APM 도메인 0건 — 에이전트 미접속·라이선스 확인")
        except ApmError as e:
            body["status"] = "degraded"
            limits.append(f"[한계] {where}APM API 조회 실패: {e.code}")
        body["api_calls_total"] = src.api.calls_total
        return body, limits

    async def gateway_health(self) -> dict[str, Any]:
        """소스별 행 · 전체 상태 `ok`(모두 정상) / `degraded`(하나라도) / `not_configured`(0개)."""
        tool = "gateway_health"
        now = self.core.clock()
        if self._health_cache and now - self._health_cache[0] < _HEALTH_CACHE_SECONDS:
            cached = dict(self._health_cache[1])
            cached["poller"] = (
                self.core.poller_status() if self.core.poller_status else {"enabled": False}
            )
            return cached
        results = await asyncio.gather(*(self._source_health(src) for src in self.core.sources))
        rows = [body for body, _ in results]
        limits = [line for _, lines in results for line in lines]
        if not rows:
            status = "not_configured"
            limits.append("[한계] APM 소스 미설정 — JENNIFER_API_URL 또는 JENNIFER_SOURCES")
        else:
            status = "ok" if all(r["status"] == "ok" for r in rows) else "degraded"
        result = self.core.ok(tool, rows, limits=limits, status=status)
        self._health_cache = (now, result)
        result = dict(result)
        result["poller"] = (
            self.core.poller_status() if self.core.poller_status else {"enabled": False}
        )
        return result
