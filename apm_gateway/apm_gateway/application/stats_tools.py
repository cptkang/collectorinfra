"""상태 통계·지표 도구군 `StatsTools`(`apm_status_stats`·`apm_metrics` · plans/134 W2 N-5·N-6 ·
plans/178 W3에서 `tools.py`의 `ApmTools`에서 옮김)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apm_gateway.adapters.jennifer.api import STATUS_KINDS, JenniferApi
from apm_gateway.application.apm_core import ApmCore
from apm_gateway.application.masking import mask_sql, mask_text, mask_url
from apm_gateway.application.resolver import Resolution
from apm_gateway.application.sources import STATUS_OK, STATUS_UNAVAILABLE
from apm_gateway.application.tool_common import (
    SERIES_DEFAULT_MINUTES,
    SERIES_INTERVAL_NOTE,
    TREND_INTERVAL_MINUTE,
    Limits,
    Window,
    check_n,
    group_by_domain,
    hour_ceil,
    hour_floor,
    identifier_arg,
    inst_key,
    inst_meta,
    is_given,
    limit_notice,
    positive_int,
    select_rows,
)
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import API_ERROR, INVALID_ARGUMENT, ApmError

# plans/134 W2(N-5~N-7)
STATUS_DEFAULT_MINUTES = 60
METRIC_MODES = ("catalog", "series")
METRIC_SCOPES = ("domain", "instance", "business", "application", "sql", "external_call")
# 시계열이 있는 군(plans/134 W2 instance · W3 domain · W4 business)
SERIES_SCOPES = ("instance", "domain", "business")


class StatsTools:
    """상태 통계·지표 도구군 — 코어(`core`)를 받아 쓴다."""

    def __init__(self, core: ApmCore) -> None:
        self.core = core

    def _hour_window(self, window: Window, limits: Limits) -> tuple[int, int]:
        """`/api/status/*` 구간 — 시 경계로 내림·올림하고 넓혔으면 `[한계]`(제니퍼 시 단위 제약)."""
        start, end = hour_floor(window.start_ms), hour_ceil(window.end_ms)
        zone = ZoneInfo(self.core.tz)

        def iso(ms: int) -> str:
            return datetime.fromtimestamp(ms / 1000, zone).isoformat(timespec="minutes")

        limits.append(
            "[한계] 시 단위 통계 — 구간을 시 경계로 맞췄다"
            + (
                f"(요청 {iso(window.start_ms)}~{iso(window.end_ms)} → 조회 {iso(start)}~{iso(end)})"
                if (start, end) != (window.start_ms, window.end_ms)
                else f"({iso(start)}~{iso(end)})"
            )
        )
        return start, end

    @staticmethod
    def _status_summary(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        """합계 — 호출·실패·실패율·평균(= Σ총 응답시간 ÷ Σ호출 · 호출 수 가중). (요약, 총 응답시간
        칸이 빠져 평균×호출 수로 가중했는가)."""
        calls = sum(r["calls"] for r in rows)
        failures = sum(r["failures"] for r in rows)
        fallback = any(r.get("total_response_ms") is None for r in rows)
        if fallback:
            weighted = sum((r["response_time_avg_ms"] or 0) * r["calls"] for r in rows)
        else:
            weighted = float(sum(r["total_response_ms"] for r in rows))
        maxima = [
            r["max_response_time_ms"] for r in rows if r.get("max_response_time_ms") is not None
        ]
        return {
            "row_count": len(rows),
            "calls": calls,
            "failures": failures,
            "failure_rate": (failures / calls) if calls else None,
            "bad_responses": sum(r["bad_responses"] for r in rows),
            "response_time_avg_ms": (weighted / calls) if calls else None,
            "max_response_time_ms": max(maxima) if maxima else None,
        }, fallback

    async def apm_status_stats(
        self,
        kind: str,
        hostname: str | None = None,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        sort_by: str | None = None,
        n: int | None = None,
        full: bool = False,
        application_name: str | None = None,
        source_ids: list[str] | None = None,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        """시 단위 통계(`kind` = application·sql·external_call · plans/134 N-5)."""
        tool = "apm_status_stats"
        kind = str(kind or "").strip().lower()
        if kind not in STATUS_KINDS:
            raise ApmError(
                INVALID_ARGUMENT, f"kind는 {'·'.join(STATUS_KINDS)} 중 하나여야 한다: {kind!r}"
            )
        sort_name = identifier_arg(sort_by, "sort_by")
        top_n = check_n(n)
        app_name = str(application_name).strip() if application_name is not None else ""
        if app_name and kind != "application":
            raise ApmError(INVALID_ARGUMENT, "application_name은 kind application에서만 쓴다")
        res = await self.core.resolve(hostname, instance_id, source_ids, instance_name)
        window = self.core.window(
            reference_time, lookback_minutes, default_minutes=STATUS_DEFAULT_MINUTES
        )
        assert window is not None
        limits = Limits()
        start, end = self._hour_window(window, limits)
        if full:
            limits.append(
                "[한계] full — 행 수를 지정하지 않아 서버 기본 행 수(미공개 · W10)만큼 받았다"
                " · 서버가 잘랐을 수 있다"
            )
        if sort_name:
            limits.append(
                f"[한계] 정렬 기준 {sort_name} — 허용값 미공개(W10) · 원천이 거부하면 그 조건 없이"
                " 다시 받아 로컬에서 정렬한다(거부 사유는 그대로 싣는다)"
            )
        received, refused = await self._status_fetch(
            kind, res, start, end, sort_name, top_n, full, app_name, limits
        )
        field = JenniferApi.status_sort_field(kind, sort_name)
        rows = [r for group in received for r in group]
        shown = self._status_shown(received, rows, refused, field, sort_name, top_n, full, limits)
        summary, fallback = self._status_summary(shown)
        if fallback:
            limits.append(
                "[한계] 일부 행에 총 응답시간 칸이 없어 평균 응답시간 × 호출 수로 가중했다"
            )
        if not full and len(rows) > len(shown):
            limits.append(
                f"[한계] summary는 표시한 상위 {len(shown)}행의 합계다 — 전체 합계는 full"
            )
        out_rows = self._mask_status_rows(kind, shown)
        return self.core.ok(
            tool,
            out_rows,
            resolution=res,
            window=window,
            limits=limits,
            partial=limits.partial,
            kind=kind,
            hour_start=datetime.fromtimestamp(start / 1000, ZoneInfo(self.core.tz)).isoformat(
                timespec="seconds"
            ),
            hour_end=datetime.fromtimestamp(end / 1000, ZoneInfo(self.core.tz)).isoformat(
                timespec="seconds"
            ),
            summary=summary,
        )

    async def _status_fetch(
        self,
        kind: str,
        res: Resolution,
        start: int,
        end: int,
        sort_name: str | None,
        top_n: int | None,
        full: bool,
        app_name: str,
        limits: Limits,
    ) -> tuple[list[list[dict[str, Any]]], list[tuple[str, ApmError]]]:
        """(소스, 도메인) 묶음마다 시 단위 통계를 받는다. (묶음별 원천 순서 행, 정렬 기준을 거부해
        조건 없이 다시 받은 묶음). 묶음 실패는 `[한계]` · 전부 실패면 오류다."""
        received: list[list[dict[str, Any]]] = []  # (소스, 도메인) 묶음별 원천 순서
        failures: list[tuple[str, ApmError]] = []
        refused: list[tuple[str, ApmError]] = []  # 정렬 기준을 거부해 조건 없이 다시 받은 묶음
        groups = group_by_domain(res)
        expect_calls(len(groups))
        for (sid, domain_id), ids in groups.items():
            where = self.core.sources.where(sid, domain_id)
            api = self.core.api(sid)
            try:
                try:
                    batch = await api.status_stats(
                        kind,
                        domain_id,
                        ids,
                        start,
                        end,
                        sort_by=sort_name,
                        max_row=None if full else top_n,
                        application_name=app_name or None,
                    )
                except ApmError as e:
                    if not (sort_name and e.code == API_ERROR):
                        raise
                    # 원천이 정렬 기준을 받지 않았다 — 그 조건 없이·행 수 없이 다시 받아
                    # 로컬에서 정렬한다(W2V-B2 · 선택 조건 하나로 보기를 막지 않는다)
                    expect_calls(1)
                    batch = await api.status_stats(
                        kind, domain_id, ids, start, end, application_name=app_name or None
                    )
                    refused.append((where, e))
            except ApmError as e:
                notice = limit_notice(e, f"{kind} 통계", f"({where})", self.core.reason_tail(e))
                failures.append((where, e))
                limits.fail(notice)
                continue
            received.append([{"source_id": sid, "domain_id": domain_id, **r} for r in batch])
        if failures and len(failures) == len(groups):
            raise self.core.all_failed(failures)
        return received, refused

    @staticmethod
    def _status_shown(
        received: list[list[dict[str, Any]]],
        rows: list[dict[str, Any]],
        refused: list[tuple[str, ApmError]],
        field: str | None,
        sort_name: str | None,
        top_n: int | None,
        full: bool,
        limits: Limits,
    ) -> list[dict[str, Any]]:
        """화면 행을 고른다 — 정렬 칸이 있으면 합쳐 정렬한 상위 n, 없으면 묶음별 원천 순서의
        상위 n을 이어 붙인다. 원천이 정렬 기준을 거부했거나 전역 순위가 아니면 `[한계]`. `rows`는
        제자리 정렬한다(호출부가 전체 행 수에 쓴다)."""
        if refused:
            reasons = "; ".join(f"{w}: {mask_text(e.reason, limit=240)}" for w, e in refused)
            how = (
                "전체를 받아 정렬했다"
                if field is not None
                else "전체를 받았다 — 대응하는 행 칸이 없어 원천 기본 순서다"
            )
            limits.append(
                f"[한계] 원천이 정렬 기준 {sort_name}를 받지 않아 {how}(그 조건·행 수 없이 다시"
                f" 받음 — 행 수는 서버 기본값 · 미공개 W10 · 원천 사유 {reasons})"
            )
            limits.unresolve(
                f"원천이 정렬 기준 {sort_name}을(를) 받지 않아 그 조건 없이 받아 "
                + ("직접 정렬했습니다" if field is not None else "원천 기본 순서로 실었습니다")
            )
        if field is not None:
            if len(received) > 1 or refused:  # 묶음을 합치거나 원천이 정렬하지 않았다
                rows.sort(key=lambda r: r.get(field) or 0, reverse=True)
            return select_rows(rows, top_n, full)
        # 정렬 기준을 행 칸에 대응하지 못한다 — 묶음마다 원천 순서의 상위 n을 모두 남긴다
        # (뒤 묶음이 화면에서 사라지지 않게 · W2V-G3)
        shown = [r for group in received for r in select_rows(group, top_n, full)]
        if len(received) > 1:
            limits.append(
                f"[한계] 정렬 기준 {sort_name}를 행 칸에 대응하지 못해 (소스, 도메인) 묶음별"
                " 원천 순서를 이어 붙였다 — 전역 순위 아님 — 묶음별 상위 "
                + ("전부" if full or top_n is None else str(top_n))
            )
        return shown

    @staticmethod
    def _mask_status_rows(kind: str, shown: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """통계 행 사본의 이름 칸을 마스킹한다 — 애플리케이션은 URL, SQL은 SQL, 외부 호출은 URL."""
        out_rows = []
        for r in shown:
            row = dict(r)
            if kind == "application":
                row["application"] = mask_url(r["application"])
            elif kind == "sql":
                row["name"] = mask_sql(r["name"])
            else:
                row["name"] = mask_url(r["name"])
            out_rows.append(row)
        return out_rows

    async def apm_metrics(
        self,
        mode: str = "catalog",
        scope: str | None = None,
        hostname: str | None = None,
        instance_id: int | None = None,
        metrics: list[str] | None = None,
        interval_minute: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
        instance_name: str | None = None,
        service: str | list[str] | None = None,
        business: str | list[str] | None = None,
        business_id: int | None = None,
        domain_id: int | None = None,
    ) -> dict[str, Any]:
        """지표 카탈로그·시계열(plans/134 N-6). 도메인(서비스)·업무 시계열(W3·W4)은 scope_tools
        모듈이 같은 지표 검증(`_metric_plan`)으로 조회한다 — 대상은 `service`·`domain_id`(업무는
        `business` 또는 `business_id`+`domain_id`). 세 군 모두 `metrics`를 비우거나 전부 모르면
        군별 기본 지표로 조회하고 그 사실을 고지한다(그 모듈 `_series_plan` · 인스턴스는 plans/164
        W7-A)."""
        tool = "apm_metrics"
        mode = str(mode or "catalog").strip().lower()
        if mode not in METRIC_MODES:
            raise ApmError(INVALID_ARGUMENT, f"mode는 catalog·series 중 하나여야 한다: {mode!r}")
        scope_name = str(scope or "").strip().lower() or None
        if scope_name is not None and scope_name not in METRIC_SCOPES:
            raise ApmError(
                INVALID_ARGUMENT, f"scope는 {'·'.join(METRIC_SCOPES)} 중 하나여야 한다: {scope!r}"
            )
        if mode == "catalog":
            return await self._metric_catalog(tool, scope_name, source_ids)
        # 그 모듈이 이 모듈을 쓴다 — 순환 import를 피해 여기서 불러온다
        from apm_gateway.application.scope_tools import ScopeTools

        scope_name = scope_name or "instance"
        if scope_name not in SERIES_SCOPES:
            raise ApmError(
                INVALID_ARGUMENT,
                f"scope {scope_name} 지표는 시계열이 아니다 — apm_status_stats(kind {scope_name})",
            )
        requested = self.core.metric_names(metrics)
        interval = positive_int(interval_minute, "interval_minute", TREND_INTERVAL_MINUTE)
        if scope_name != "instance":
            if is_given(hostname) or is_given(instance_name) or instance_id is not None:
                raise ApmError(
                    INVALID_ARGUMENT,
                    f"scope {scope_name} 시계열은 hostname·instance_name·instance_id를 쓰지 않는다"
                    " — service·domain_id(업무는 business 또는 business_id+domain_id)로 정한다",
                )
            return await ScopeTools(self.core).metric_series(
                scope_name,
                requested,
                interval,
                reference_time,
                lookback_minutes,
                source_ids,
                service=service,
                business=business,
                business_id=business_id,
                domain_id=domain_id,
            )
        if any(v is not None for v in (service, business, business_id, domain_id)):
            raise ApmError(
                INVALID_ARGUMENT,
                "service·business·business_id·domain_id는 scope domain·business 시계열에서만 쓴다",
            )
        res = await self.core.resolve(hostname, instance_id, source_ids, instance_name)
        window = self.core.window(
            reference_time, lookback_minutes, default_minutes=SERIES_DEFAULT_MINUTES
        )
        assert window is not None
        limits = Limits([SERIES_INTERVAL_NOTE])
        source_list = sorted({i["source_id"] for i in res.instances})
        plan = None
        if requested:
            plan = await self.core.metric_plan(
                source_list, requested, "instance", limits, all_unknown_raises=False
            )
            if plan is not None and limits.unresolved:  # 일부를 빼고 조회 — 지표가 빠진 결과
                limits.partial = True
        if plan is None:  # 미지정·전부 모름 — 도메인·업무 시계열처럼 기본 지표 + 고지(plans/164)
            plan, used = await ScopeTools(self.core)._default_series_plan(
                source_list, "instance", limits
            )
            shown = ", ".join(used) or "없음"
            if requested:
                limits.unresolve(
                    f"요청한 지표 {', '.join(requested)}를 지표 목록에서 찾지 못해 기본 지표"
                    f"({shown})로 조회했습니다"
                )
                limits.partial = True
            else:
                limits.append(f"[한계] 지표 미지정 — 기본 지표({shown})로 조회")
            if not used:
                limits.fail(
                    "[한계] instance 기본 지표가 지표 카탈로그에 없어 조회하지 못했다 —"
                    " apm_metrics(mode catalog)로 지표를 고른다"
                )
        rows: list[dict[str, Any]] = []
        empty: list[str] = []
        failures: list[tuple[str, ApmError]] = []
        attempted = 0
        expect_calls(sum(len(plan.get(i["source_id"], [])) for i in res.instances))
        for inst in res.instances:
            label = self.core.inst_label(inst_key(inst))
            for metric_id, _label in plan.get(inst["source_id"], []):
                attempted += 1
                try:
                    points = await self.core.api(inst["source_id"]).instance_metric_series(
                        inst["domain_id"],
                        inst["instance_id"],
                        metric_id,
                        interval,
                        window.start_ms,
                        window.end_ms,
                    )
                except ApmError as e:
                    notice = limit_notice(
                        e, f"지표 {metric_id}", f"(인스턴스 {label})", self.core.reason_tail(e)
                    )
                    failures.append((f"인스턴스 {label} 지표 {metric_id}", e))
                    limits.fail(notice)
                    continue
                if not points:
                    empty.append(f"{label}:{metric_id}")
                rows += [
                    {
                        **inst_meta(inst),
                        "metric": metric_id,
                        "time_ms": p["time_ms"],
                        "value": p["value"],
                    }
                    for p in points
                ]
        if attempted and len(failures) == attempted:
            raise self.core.all_failed(failures)
        if empty:
            limits.append(f"[한계] 데이터 점이 없는 (인스턴스:지표): {', '.join(empty)}")
        return self.core.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            limits=limits,
            partial=limits.partial,
            mode=mode,
            scope=scope_name,
            interval_minute=interval,
        )

    async def _metric_catalog(
        self, tool: str, scope: str | None, source_ids: list[str] | None
    ) -> dict[str, Any]:
        """소스별 지표 카탈로그 → 행 `{source_id, scope, metric}`(TTL 캐시 · 변경 감지)."""
        limits = Limits()
        rows: list[dict[str, Any]] = []
        statuses: list[dict[str, Any]] = []
        failures: list[tuple[str, ApmError]] = []
        selected = self.core.sources.select(source_ids)
        for src in selected:
            try:
                entry = await self.core.catalog(src.source_id)
            except ApmError as e:
                notice = limit_notice(
                    e, "지표 카탈로그", self.core.where(src.source_id), self.core.reason_tail(e)
                )
                failures.append((self.core.sources.where(src.source_id), e))
                statuses.append(
                    {
                        "source_id": src.source_id,
                        "status": STATUS_UNAVAILABLE,
                        "reason": f"{e.code}: {e.reason}",
                    }
                )
                limits.fail(notice)
                continue
            statuses.append({"source_id": src.source_id, "status": STATUS_OK, "reason": ""})
            if entry.change_note:
                limits.append(entry.change_note)
            broken = [sc for sc in entry.invalid if scope is None or sc == scope]
            if broken:  # 군 단위 모양 위반 — 빈 군이 아니라 검증 불가(W2V-G4)
                limits.fail(
                    f"[한계] 지표 카탈로그 모양 위반{self.core.where(src.source_id)} — 군"
                    f" {', '.join(broken)}(목록이 아니거나 문자열이 아닌 항목)은 목록을 싣지 못했다"
                    " · 그 군의 지표 이름은 검증 불가"
                )
            for scope_name, names in entry.scopes.items():
                if scope is None or scope_name == scope:
                    rows += [
                        {"source_id": src.source_id, "scope": scope_name, "metric": m}
                        for m in names
                    ]
        if failures and len(failures) == len(selected):
            raise self.core.all_failed(failures)
        if scope in (None, *STATUS_KINDS):
            limits.append(
                "[한계] application·sql·external_call 지표 이름이 통계 정렬 기준(sort_by) 값인지는"
                " 미확인이다(W10)"
            )
        return self.core.ok(
            tool, rows, limits=limits, sources=statuses, partial=limits.partial, mode="catalog"
        )
