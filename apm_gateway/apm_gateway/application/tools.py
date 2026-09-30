"""`apm_*` 도구 코어 8종 + `gateway_health` (plans/87 §5.2(c) · SPEC-apm-gateway §3).

반환은 dict(정상 `{rows, row_count, …}` / 오류 `{error, reason}`)이고, MCP 등록·감사·JSON 직렬화는
인터페이스 계층이 맡는다. 원칙:

- 대상 인스턴스는 **결정적 정합**으로만 정한다(LLM이 인스턴스명을 추측하지 않는다) — 최대 5개.
- 상위 N 축약·마스킹은 서버측에서 한다(원문은 반환·감사 어디에도 남기지 않는다).
- 침묵 폴백 금지 — 일부 호출 실패·창 상한·과거 시점 등은 `limits`에 `[한계]`로 적고, 전부 실패면
  오류를 돌려준다.
- WAS 판정은 `domain.signals` 한 곳에서만 한다(`was_signals`).
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apm_gateway.adapters.jennifer.allowlist import ALLOWED
from apm_gateway.adapters.jennifer.api import SOURCE, XVIEW_WINDOW_MS, JenniferApi
from apm_gateway.application.masking import mask_ip, mask_sql, mask_text, mask_url
from apm_gateway.application.resolver import InstanceResolver, Resolution
from apm_gateway.config import GatewayConfig
from apm_gateway.domain import signals as sig
from apm_gateway.domain.errors import (
    CONTRACT_VIOLATION,
    INVALID_ARGUMENT,
    NOT_CONFIGURED,
    PROFILE_REF_MISMATCH,
    RATE_LIMITED,
    SOURCE_UNAVAILABLE,
    ApmError,
)
from apm_gateway.domain.events import level_rank

logger = logging.getLogger(__name__)

SOURCE_KIND = "apm_api"
XVIEW_MAX_MINUTES = 10
EVENTS_DEFAULT_MINUTES = 30
EVENTS_MAX_MINUTES = 24 * 60
SLOW_TX_DEFAULT_MINUTES = 10
HEALTH_DEFAULT_LOOKBACK = 30
TREND_INTERVAL_MINUTE = 5
TREND_MAX_INSTANCES = 2
EVENT_ROWS_MAX = 50
N_MAX = 20
PROFILE_LINES_MAX = 60
PROFILE_CHARS_MAX = 4000
_PROFILE_BUDGET_TTL = 3600.0
_HEALTH_CACHE_SECONDS = 30.0


def _now_iso(clock: Callable[[], float]) -> str:
    return datetime.fromtimestamp(clock()).astimezone().isoformat(timespec="seconds")


class Window:
    def __init__(self, start_ms: int, end_ms: int, end_is_now: bool, tz: str) -> None:
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.end_is_now = end_is_now
        self.tz = tz

    @property
    def minutes(self) -> int:
        return max(1, round((self.end_ms - self.start_ms) / 60_000))

    def as_dict(self) -> dict[str, Any]:
        zone = ZoneInfo(self.tz)
        return {
            "start": datetime.fromtimestamp(self.start_ms / 1000, zone).isoformat(
                timespec="seconds"
            ),
            "end": datetime.fromtimestamp(self.end_ms / 1000, zone).isoformat(timespec="seconds"),
            "minutes": self.minutes,
        }


def _check_n(n: Any, default: int = 10) -> int:
    value = default if n is None else int(n)
    if not 1 <= value <= N_MAX:
        raise ApmError(INVALID_ARGUMENT, f"n은 1~{N_MAX} 사이여야 한다: {n}")
    return value


def _group(resolution: Resolution) -> dict[int, list[int]]:
    groups: dict[int, list[int]] = {}
    for inst in resolution.instances:
        groups.setdefault(inst["domain_id"], []).append(inst["instance_id"])
    return groups


def _inst_meta(inst: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_id": inst["instance_id"],
        "instance_name": inst["instance_name"],
        "domain_id": inst["domain_id"],
    }


def _hour_floor(ms: int) -> int:
    return ms - ms % 3_600_000


def _hour_ceil(ms: int) -> int:
    floor = _hour_floor(ms)
    return floor if floor == ms else floor + 3_600_000


class ApmTools:
    """도구 코어. 인스턴스 하나를 프로세스에서 공유한다."""

    def __init__(
        self,
        api: JenniferApi,
        resolver: InstanceResolver,
        cfg: GatewayConfig,
        *,
        clock: Callable[[], float] = time.time,
        poller_status: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.api = api
        self.resolver = resolver
        self.cfg = cfg
        self.clock = clock
        self.poller_status = poller_status
        self._th = cfg.policies.thresholds
        self._tz = cfg.runtime.timezone
        self._profile_budget: dict[str, tuple[int, float]] = {}
        self._health_cache: tuple[float, dict[str, Any]] | None = None

    # ── 공통 ──────────────────────────────────────────────

    def ok(
        self,
        tool: str,
        rows: list[dict[str, Any]],
        *,
        resolution: Resolution | None = None,
        window: Window | None = None,
        was_signals: list[dict[str, Any]] | None = None,
        limits: list[str] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rows": rows,
            "row_count": len(rows),
            "queried_at": _now_iso(self.clock),
            "source_kind": SOURCE_KIND,
            "source": SOURCE,
            "tool": tool,
        }
        if resolution is not None:
            payload["instance_resolution"] = resolution.as_dict()
        if window is not None:
            payload["window"] = window.as_dict()
        if was_signals is not None:
            payload["was_signals"] = sig.dedupe(was_signals)
        payload["limits"] = list(
            dict.fromkeys((resolution.limits if resolution else []) + (limits or []))
        )
        payload.update(extra)
        return payload

    @staticmethod
    def err(tool: str, code: str, reason: str) -> dict[str, Any]:
        return {
            "error": code,
            "reason": mask_text(reason, limit=400),
            "source_kind": SOURCE_KIND,
            "source": SOURCE,
            "tool": tool,
        }

    def _require_configured(self) -> None:
        if not self.api.configured:
            raise ApmError(
                NOT_CONFIGURED, "APM API URL 미설정 — 게이트웨이 .env의 JENNIFER_API_URL을 설정"
            )

    def window(
        self,
        reference_time: str | None,
        lookback_minutes: int | None,
        *,
        default_minutes: int | None,
    ) -> Window | None:
        """구간 인자 → 창. 둘 다 없고 기본값도 없으면 None(현재 시점 조회)."""
        if reference_time is None and lookback_minutes is None and default_minutes is None:
            return None
        if reference_time is not None:
            try:
                ref = datetime.fromisoformat(str(reference_time).strip())
            except ValueError as e:
                raise ApmError(
                    INVALID_ARGUMENT, f"reference_time은 ISO 8601이어야 한다: {reference_time!r}"
                ) from e
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=ZoneInfo(self._tz))
            end_ms = int(ref.timestamp() * 1000)
            end_is_now = False
        else:
            end_ms = int(self.clock() * 1000)
            end_is_now = True
        minutes = (
            lookback_minutes
            if lookback_minutes is not None
            else (default_minutes or HEALTH_DEFAULT_LOOKBACK)
        )
        minutes = int(minutes)
        if minutes < 1:
            raise ApmError(
                INVALID_ARGUMENT, f"lookback_minutes는 1 이상이어야 한다: {lookback_minutes}"
            )
        return Window(end_ms - minutes * 60_000, end_ms, end_is_now, self._tz)

    async def _resolve(self, hostname: str | None, instance_id: int | None = None) -> Resolution:
        self._require_configured()
        if not hostname or not str(hostname).strip():
            raise ApmError(INVALID_ARGUMENT, "hostname이 비어 있음")
        return await self.resolver.resolve(str(hostname).strip(), instance_id)

    async def _realtime(
        self, resolution: Resolution, limits: list[str]
    ) -> dict[int, dict[str, Any]]:
        """해소 인스턴스의 실시간 스냅샷(도메인당 1호출)."""
        wanted = {i["instance_id"] for i in resolution.instances}
        out: dict[int, dict[str, Any]] = {}
        for domain_id in _group(resolution):
            try:
                for rec in await self.api.realtime(domain_id):
                    if rec["instance_id"] in wanted:
                        out[rec["instance_id"]] = rec
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.append(f"[한계] 실시간 조회 실패(도메인 {domain_id}): {e.code}")
        missing = wanted - set(out)
        if missing:
            limits.append(f"[한계] 실시간 데이터 없는 인스턴스: {sorted(missing)}")
        return out

    async def _xview(
        self, resolution: Resolution, window: Window, limits: list[str]
    ) -> tuple[list[dict[str, Any]], Window, bool]:
        """X-View 기본 데이터를 1분 창으로 나눠 모은다(상한 10분 — 사건 직전 구간). (거래, 실제 창,
        성공 여부)."""
        start = max(window.start_ms, window.end_ms - XVIEW_MAX_MINUTES * 60_000)
        used = Window(start, window.end_ms, window.end_is_now, self._tz)
        if start > window.start_ms:
            limits.append(
                f"[한계] 사건창 {window.minutes}분 > {XVIEW_MAX_MINUTES}분 — 트랜잭션 분석은 직전 "
                f"{XVIEW_MAX_MINUTES}분만(1분 창 분할 상한), 나머지는 시 단위 통계"
            )
        wanted = {i["instance_id"] for i in resolution.instances}
        seen: set[str] = set()
        txs: list[dict[str, Any]] = []
        any_ok = False
        for domain_id, ids in _group(resolution).items():
            t = start
            while t < window.end_ms:
                chunk_end = min(t + XVIEW_WINDOW_MS, window.end_ms)
                query_end = chunk_end if chunk_end == window.end_ms else chunk_end - 1
                try:
                    batch = await self.api.transactions(domain_id, ids, t, query_end)
                    any_ok = True
                except ApmError as e:
                    if e.code == CONTRACT_VIOLATION:
                        raise
                    limits.append(f"[한계] 트랜잭션 조회 실패(도메인 {domain_id}): {e.code}")
                    break
                for tx in batch:
                    key = (
                        tx.get("txid")
                        or f"{tx.get('instance_id')}:{tx.get('end_time_ms')}:{len(txs)}"
                    )
                    if tx.get("instance_id") in wanted and key not in seen:
                        seen.add(key)
                        txs.append(tx)
                t = chunk_end
        return txs, used, any_ok

    @staticmethod
    def _window_stats(txs: list[dict[str, Any]]) -> dict[str, Any]:
        times = [t["response_time_ms"] for t in txs if t.get("response_time_ms") is not None]
        errors = sum(1 for t in txs if t.get("error_type"))
        calls = len(txs)
        return {
            "calls": calls,
            "errors": errors,
            "error_rate": (errors / calls) if calls else None,
            "response_time_p50_ms": sig.percentile(times, 50),
            "response_time_p95_ms": sig.percentile(times, 95),
            "response_time_max_ms": max(times) if times else None,
        }

    async def _hourly(
        self, resolution: Resolution, window: Window, limits: list[str]
    ) -> dict[str, Any] | None:
        """시 단위 애플리케이션 통계(창 > 10분일 때 맥락) — 시 경계로 내림·올림."""
        start, end = _hour_floor(window.start_ms), _hour_ceil(window.end_ms)
        rows: list[dict[str, Any]] = []
        for domain_id, ids in _group(resolution).items():
            try:
                rows.extend(await self.api.application_status(domain_id, ids, start, end, 20))
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.append(f"[한계] 시 단위 통계 조회 실패(도메인 {domain_id}): {e.code}")
        if not rows:
            return None
        calls = sum(r["calls"] for r in rows)
        failures = sum(r["failures"] for r in rows)
        weighted = sum((r["response_time_avg_ms"] or 0) * r["calls"] for r in rows)
        top = sorted(rows, key=lambda r: r["response_time_avg_ms"] or 0, reverse=True)[:5]
        limits.append("[한계] 시 단위 통계 — 사건창보다 넓은 시간 경계로 집계된 값")
        return {
            "calls": calls,
            "failures": failures,
            "failure_rate": (failures / calls) if calls else None,
            "response_time_avg_ms": (weighted / calls) if calls else None,
            "max_response_time_ms": max((r["max_response_time_ms"] or 0) for r in rows),
            "top_applications": [{**r, "application": mask_url(r["application"])} for r in top],
            "hour_start": datetime.fromtimestamp(start / 1000, ZoneInfo(self._tz)).isoformat(
                timespec="seconds"
            ),
            "hour_end": datetime.fromtimestamp(end / 1000, ZoneInfo(self._tz)).isoformat(
                timespec="seconds"
            ),
        }

    # ── 도구 8종 ──────────────────────────────────────────

    async def apm_instance_map(self, hostname: str | None = None) -> dict[str, Any]:
        tool = "apm_instance_map"
        self._require_configured()
        if hostname and str(hostname).strip():
            res = await self.resolver.resolve(str(hostname).strip())
            rows = [
                {**inst, "match_confidence": res.confidence, "match_reason": res.reason}
                for inst in res.instances
            ]
            return self.ok(tool, rows, resolution=res)
        inv = await self.resolver.inventory()
        self.resolver.ensure_available(inv)
        limits = self.resolver.notes(inv)
        rows = []
        for inst in inv.instances[:200]:
            host, conf, reason, _ = self.resolver.reverse(
                inv, inst["domain_id"], inst["instance_id"]
            )
            rows.append(
                {**inst, "hostname": host, "match_confidence": conf, "match_reason": reason}
            )
        if len(inv.instances) > 200:
            limits.append(
                f"[한계] 인스턴스 {len(inv.instances)}개 중 200개만 반환"
                " — hostname으로 좁힐 수 있다"
            )
        return self.ok(tool, rows, limits=limits)

    async def apm_app_health(
        self,
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
    ) -> dict[str, Any]:
        tool = "apm_app_health"
        res = await self._resolve(hostname, instance_id)
        window = self.window(reference_time, lookback_minutes, default_minutes=None)
        limits: list[str] = []
        current: dict[int, dict[str, Any]] = {}
        if window is None or window.end_is_now:
            current = await self._realtime(res, limits)
        else:
            limits.append(
                "[한계] 과거 기준시각 — 실시간 스냅샷 생략(현재값은 사건 시점 증거가 아니다)"
            )
        per_inst_tx: dict[int, list[dict[str, Any]]] = {}
        xview_ok = False
        used_window = window
        hourly = None
        if window is not None:
            txs, used_window, xview_ok = await self._xview(res, window, limits)
            for tx in txs:
                per_inst_tx.setdefault(tx["instance_id"], []).append(tx)
            if window.minutes > XVIEW_MAX_MINUTES:
                hourly = await self._hourly(res, window, limits)
        if not current and not xview_ok and hourly is None:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits) or "APM 데이터 조회 실패")
        rows: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        for inst in res.instances:
            iid = inst["instance_id"]
            cur = current.get(iid)
            row = _inst_meta(inst)
            if cur:
                for key in (
                    "response_time_avg_ms",
                    "tps",
                    "active_services",
                    "bad_response_active_services",
                    "reject_rate",
                    "concurrent_users",
                    "arrival_rate",
                ):
                    row[key] = cur.get(key)
            stats = self._window_stats(per_inst_tx.get(iid, [])) if window is not None else None
            if stats is not None:
                row["window"] = stats
            rows.append(row)
            signals += sig.judge_app(cur, stats, self._th, instance_id=iid)
        extra = {"hourly": hourly} if hourly is not None else {}
        return self.ok(
            tool,
            rows,
            resolution=res,
            window=used_window,
            was_signals=signals,
            limits=limits,
            **extra,
        )

    async def apm_runtime_health(
        self,
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
    ) -> dict[str, Any]:
        tool = "apm_runtime_health"
        res = await self._resolve(hostname, instance_id)
        window = self.window(reference_time, lookback_minutes, default_minutes=None)
        limits: list[str] = []
        current: dict[int, dict[str, Any]] = {}
        if window is None or window.end_is_now:
            current = await self._realtime(res, limits)
        else:
            limits.append(
                "[한계] 과거 기준시각 — 실시간 스냅샷 생략(현재값은 사건 시점 증거가 아니다)"
            )
        trends: dict[int, dict[str, Any]] = {}
        if window is not None:
            targets = res.instances[:TREND_MAX_INSTANCES]
            if len(res.instances) > TREND_MAX_INSTANCES:
                limits.append(
                    f"[한계] 추세는 인스턴스 {TREND_MAX_INSTANCES}개만 조회"
                    "(지표 1개/호출 부하 상한)"
                )
            for inst in targets:
                trend: dict[str, list[dict[str, Any]]] = {}
                for metric in ("heap_used_mb", "heap_committed_mb", "gc_time_usage_pct"):
                    try:
                        series = await self.api.metric_series(
                            inst["domain_id"],
                            inst["instance_id"],
                            metric,
                            TREND_INTERVAL_MINUTE,
                            window.start_ms,
                            window.end_ms,
                        )
                    except ApmError as e:
                        if e.code == CONTRACT_VIOLATION:
                            raise
                        limits.append(
                            f"[한계] 추세 {metric} 조회 실패"
                            f"(인스턴스 {inst['instance_id']}): {e.code}"
                        )
                        continue
                    if series is not None:
                        trend[metric] = series
                if trend:
                    trends[inst["instance_id"]] = trend
        if not current and not trends:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits) or "APM 런타임 데이터 조회 실패")
        rows: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        for inst in res.instances:
            iid = inst["instance_id"]
            cur = current.get(iid)
            row = _inst_meta(inst)
            if cur:
                for key in (
                    "heap_used_mb",
                    "heap_committed_mb",
                    "non_heap_used_mb",
                    "gc_time_usage_pct",
                    "process_cpu_pct",
                    "process_memory_mb",
                    "thread_current",
                ):
                    row[key] = cur.get(key)
                used, committed = cur.get("heap_used_mb"), cur.get("heap_committed_mb")
                row["heap_usage_ratio"] = (
                    (used / committed) if used is not None and committed else None
                )
            if iid in trends:
                row["trend"] = trends[iid]
            rows.append(row)
            signals += sig.judge_runtime(cur, trends.get(iid), self._th, instance_id=iid)
        return self.ok(
            tool, rows, resolution=res, window=window, was_signals=signals, limits=limits
        )

    async def apm_resource_pool(
        self, hostname: str, instance_id: int | None = None
    ) -> dict[str, Any]:
        tool = "apm_resource_pool"
        res = await self._resolve(hostname, instance_id)
        limits = [
            "[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다",
            "[한계] WAS 스레드 풀 상한 필드 없음 — thread_current(JVM 전체)·active_services로 근사",
        ]
        current = await self._realtime(res, limits)
        services: dict[int, list[dict[str, Any]]] = {}
        active_ok = False
        for domain_id, ids in _group(res).items():
            try:
                for svc in await self.api.active_services(domain_id, ids):
                    services.setdefault(svc["instance_id"], []).append(svc)
                active_ok = True
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.append(f"[한계] 액티브 서비스 조회 실패(도메인 {domain_id}): {e.code}")
        if not current and not active_ok:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        rows: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        for inst in res.instances:
            iid = inst["instance_id"]
            cur = current.get(iid) or {}
            svcs = services.get(iid, [])
            active = cur.get("db_pool_active")
            configured = cur.get("db_pool_configured_avg")
            rows.append(
                {
                    **_inst_meta(inst),
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
            signals += sig.judge_pool(cur, self._th, instance_id=iid)
            signals += sig.judge_active_services(svcs, self._th, instance_id=iid, source_tool=tool)
        return self.ok(tool, rows, resolution=res, was_signals=signals, limits=limits)

    async def apm_slow_transactions(
        self,
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        n: int | None = None,
    ) -> dict[str, Any]:
        tool = "apm_slow_transactions"
        top_n = _check_n(n)
        res = await self._resolve(hostname, instance_id)
        window = self.window(
            reference_time, lookback_minutes, default_minutes=SLOW_TX_DEFAULT_MINUTES
        )
        assert window is not None
        limits: list[str] = []
        txs, used, ok = await self._xview(res, window, limits)
        hourly = (
            await self._hourly(res, window, limits) if window.minutes > XVIEW_MAX_MINUTES else None
        )
        if not ok and hourly is None:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits) or "트랜잭션 조회 실패")
        ranked = sorted(txs, key=lambda t: t.get("response_time_ms") or 0, reverse=True)
        rows = []
        for tx in ranked[:top_n]:
            rows.append(
                {
                    "instance_id": tx["instance_id"],
                    "application": mask_url(tx["application"]),
                    "response_time_ms": tx["response_time_ms"],
                    "cpu_ms": tx["cpu_ms"],
                    "sql_ms": tx["sql_ms"],
                    "fetch_ms": tx["fetch_ms"],
                    "external_ms": tx["external_ms"],
                    "network_ms": tx["network_ms"],
                    "error_type": tx["error_type"],
                    "end_time_ms": tx["end_time_ms"],
                    "profile_ref": {
                        "domain_id": tx.get("domain_id"),
                        "txid": tx["txid"],
                        "time_ms": tx["end_time_ms"],
                    }
                    if tx.get("txid")
                    else None,
                }
            )
        signals: list[dict[str, Any]] = []
        per_inst: dict[int, list[dict[str, Any]]] = {}
        for tx in ranked:
            per_inst.setdefault(tx["instance_id"], []).append(tx)
        for iid, items in per_inst.items():
            signals += sig.judge_transactions(items[:top_n], self._th, instance_id=iid)
        summary = {**self._window_stats(txs), **sig.transaction_shares(ranked[:top_n])}
        extra: dict[str, Any] = {"summary": summary}
        if hourly is not None:
            extra["hourly"] = hourly
        return self.ok(
            tool, rows, resolution=res, window=used, was_signals=signals, limits=limits, **extra
        )

    async def apm_active_services(
        self, hostname: str, instance_id: int | None = None, n: int | None = None
    ) -> dict[str, Any]:
        tool = "apm_active_services"
        top_n = _check_n(n)
        res = await self._resolve(hostname, instance_id)
        limits = [
            "[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다",
            "[한계] elapsed_ms는 경과 시간 필드 단위 미기재로 ms로 가정(U-12)",
        ]
        wanted = {i["instance_id"] for i in res.instances}
        services: list[dict[str, Any]] = []
        ok = False
        for domain_id, ids in _group(res).items():
            try:
                services += [
                    s
                    for s in await self.api.active_services(domain_id, ids)
                    if s["instance_id"] in wanted
                ]
                ok = True
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.append(f"[한계] 액티브 서비스 조회 실패(도메인 {domain_id}): {e.code}")
        if not ok:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        ranked = sorted(services, key=lambda s: s.get("elapsed_ms") or 0, reverse=True)
        rows = [
            {
                "instance_id": s["instance_id"],
                "application": mask_url(s["application"]),
                "status": s["status"],
                "status_name": s["status_name"],
                "elapsed_ms": s["elapsed_ms"],
                "status_elapsed_ms": s["status_elapsed_ms"],
                "running_mode": s["running_mode"],
                "running_text": mask_text(s["running_text"]),
                "datasource": s["datasource"],
                "client_ip": mask_ip(s["client_ip"]),
                "txid": s["txid"],
                "start_time_ms": s["start_time_ms"],
            }
            for s in ranked[:top_n]
        ]
        signals: list[dict[str, Any]] = []
        for iid in wanted:
            signals += sig.judge_active_services(
                [s for s in services if s["instance_id"] == iid],
                self._th,
                instance_id=iid,
                source_tool=tool,
            )
        summary = {
            "total": len(services),
            "by_running_mode": dict(Counter(s["running_mode"] or "(없음)" for s in services)),
            "by_status": dict(
                Counter(s["status_name"] or s["status"] or "(없음)" for s in services)
            ),
        }
        return self.ok(
            tool, rows, resolution=res, was_signals=signals, limits=limits, summary=summary
        )

    async def apm_events(
        self,
        hostname: str,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        level: str | None = None,
    ) -> dict[str, Any]:
        tool = "apm_events"
        if level is not None and str(level).strip().lower() not in ("fatal", "warning", "normal"):
            raise ApmError(
                INVALID_ARGUMENT, f"level은 fatal·warning·normal 중 하나(최소 레벨): {level}"
            )
        res = await self._resolve(hostname)
        limits: list[str] = []
        if lookback_minutes is not None and int(lookback_minutes) > EVENTS_MAX_MINUTES:
            limits.append(f"[한계] 이벤트 조회 창 상한 {EVENTS_MAX_MINUTES}분으로 줄였다")
            lookback_minutes = EVENTS_MAX_MINUTES
        window = self.window(
            reference_time, lookback_minutes, default_minutes=EVENTS_DEFAULT_MINUTES
        )
        assert window is not None
        wanted = {i["instance_id"] for i in res.instances}
        events: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        ok = False
        for domain_id, ids in _group(res).items():
            try:
                events += [
                    e
                    for e in await self.api.events(domain_id, ids, window.start_ms, window.end_ms)
                    if e["instance_id"] in wanted
                ]
                ok = True
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.append(f"[한계] 이벤트 조회 실패(도메인 {domain_id}): {e.code}")
                continue
            try:
                errors += [
                    e
                    for e in await self.api.errors(domain_id, ids, window.start_ms, window.end_ms)
                    if e["instance_id"] in wanted
                ]
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.append(f"[한계] 오류 기록 조회 실패(도메인 {domain_id}): {e.code}")
        if not ok:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        if level is not None:
            floor = level_rank(level)
            events = [e for e in events if level_rank(e["level"]) >= floor]
        events.sort(key=lambda e: e.get("time_ms") or 0, reverse=True)
        if len(events) > EVENT_ROWS_MAX:
            limits.append(f"[한계] 이벤트 {len(events)}건 중 최근 {EVENT_ROWS_MAX}건만 반환")
        rows = []
        signals: list[dict[str, Any]] = []
        for ev in events[:EVENT_ROWS_MAX]:
            rows.append(
                {
                    "time_ms": ev["time_ms"],
                    "level": ev["level"],
                    "event_type": ev["event_type"],
                    "event_kind": ev["event_kind"],
                    "value": ev["value"],
                    "message": mask_text(ev["message"]),
                    "instance_id": ev["instance_id"],
                    "instance_name": ev["instance_name"],
                    "application": mask_url(ev["application"]),
                    "profile_ref": {
                        "domain_id": ev["domain_id"],
                        "txid": ev["txid"],
                        "time_ms": ev["time_ms"],
                    }
                    if ev["txid"]
                    else None,
                }
            )
            s = sig.signal_from_event(
                self.api.event_signal(ev["event_type"]),
                ev["event_type"],
                instance_id=ev["instance_id"],
                source_tool=tool,
            )
            if s is not None:
                signals.append(s)
        error_summary = Counter(e["error_type"] or "(없음)" for e in errors).most_common(10)
        return self.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            was_signals=signals,
            limits=limits,
            errors_by_type=[{"error_type": k, "count": v} for k, v in error_summary],
        )

    def _consume_profile_budget(self, investigation_id: str | None) -> None:
        now = self.clock()
        for key in [
            k for k, (_, t0) in self._profile_budget.items() if now - t0 > _PROFILE_BUDGET_TTL
        ]:
            del self._profile_budget[key]
        key = investigation_id or "_anonymous"
        count, t0 = self._profile_budget.get(key, (0, now))
        limit = self.cfg.runtime.profile_calls_per_investigation
        if count >= limit:
            raise ApmError(
                RATE_LIMITED, f"조사당 프로파일 호출 상한 {limit}회 초과(investigation_id={key})"
            )
        self._profile_budget[key] = (count + 1, t0)

    async def apm_transaction_profile(
        self,
        hostname: str,
        domain_id: int | None = None,
        txid: str | int | None = None,
        time_ms: int | None = None,
        top_k: int | None = None,
        investigation_id: str | None = None,
    ) -> dict[str, Any]:
        tool = "apm_transaction_profile"
        k = _check_n(top_k)
        if domain_id is None or txid is None or time_ms is None:
            raise ApmError(
                INVALID_ARGUMENT,
                "profile_ref(domain_id·txid·time_ms)가 필요하다 — apm_slow_transactions·apm_events"
                " 결과의 profile_ref를 그대로 넘길 것",
            )
        if (
            not str(txid).lstrip("-").isdigit()
            or not str(time_ms).isdigit()
            or not str(domain_id).isdigit()
        ):
            raise ApmError(INVALID_ARGUMENT, "domain_id·txid·time_ms는 정수여야 한다")
        res = await self._resolve(hostname)
        if int(domain_id) not in res.domain_ids:
            raise ApmError(
                PROFILE_REF_MISMATCH,
                f"domain_id {domain_id}는 hostname {hostname!r}의 정합 도메인"
                f" {sorted(res.domain_ids)}이 아니다",
            )
        self._consume_profile_budget(investigation_id)
        d, tx_id, t_ms = int(domain_id), int(txid), int(time_ms)
        limits = ["[한계] 프로파일 텍스트 형식 미검증(J0-L-b 녹화 전) — 단계 요약 없이 마스킹 발췌"]
        detail = excerpt = None
        sqls: list[str] = []
        ok = False
        try:
            detail = await self.api.transaction_detail(d, tx_id, t_ms)
            ok = True
        except ApmError as e:
            if e.code == CONTRACT_VIOLATION:
                raise
            limits.append(f"[한계] 트랜잭션 상세 조회 실패: {e.code}")
        try:
            text = await self.api.profile_text(d, tx_id, t_ms)
            lines = [mask_text(line, limit=400) for line in text.splitlines()[:PROFILE_LINES_MAX]]
            excerpt = "\n".join(lines)[:PROFILE_CHARS_MAX]
            if len(text.splitlines()) > PROFILE_LINES_MAX:
                limits.append(f"[한계] 프로파일 {PROFILE_LINES_MAX}줄까지만 발췌")
            ok = True
        except ApmError as e:
            if e.code == CONTRACT_VIOLATION:
                raise
            limits.append(f"[한계] 프로파일 텍스트 조회 실패: {e.code}")
        try:
            sqls = [mask_sql(s)[:1000] for s in await self.api.transaction_sqls(d, tx_id, t_ms, k)]
            ok = True
        except ApmError as e:
            if e.code == CONTRACT_VIOLATION:
                raise
            limits.append(f"[한계] SQL 조회 실패: {e.code}")
        if not ok:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        transaction = None
        if detail:
            transaction = {**detail, "application": mask_url(detail["application"])}
        row = {
            "domain_id": d,
            "txid": str(tx_id),
            "time_ms": t_ms,
            "transaction": transaction,
            "profile_excerpt": excerpt,
            "sqls": sqls,
        }
        return self.ok(tool, [row], resolution=res, limits=limits)

    async def gateway_health(self) -> dict[str, Any]:
        tool = "gateway_health"
        now = self.clock()
        if self._health_cache and now - self._health_cache[0] < _HEALTH_CACHE_SECONDS:
            cached = dict(self._health_cache[1])
            cached["poller"] = self.poller_status() if self.poller_status else {"enabled": False}
            return cached
        body: dict[str, Any] = {
            "status": "ok",
            "jennifer_configured": self.api.configured,
            "jennifer_reachable": False,
            "domain_count": None,
            "allowlist_size": len(ALLOWED),
        }
        limits: list[str] = []
        if not self.api.configured:
            body["status"] = "not_configured"
        else:
            try:
                domains = await self.api.domains()
                body["jennifer_reachable"] = True
                body["domain_count"] = len(domains)
                if not domains:
                    body["status"] = "degraded"
                    limits.append("[한계] APM 도메인 0건 — 에이전트 미접속·라이선스 확인")
            except ApmError as e:
                body["status"] = "degraded"
                limits.append(f"[한계] APM API 조회 실패: {e.code}")
        body["api_calls_total"] = self.api.calls_total
        result = self.ok(tool, [body], limits=limits)
        self._health_cache = (now, result)
        result = dict(result)
        result["poller"] = self.poller_status() if self.poller_status else {"enabled": False}
        return result
