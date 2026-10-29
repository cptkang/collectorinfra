"""도구 코어 `ApmCore` — 도구군·형제 모듈이 함께 쓰는 상태·응답 봉투·조회 창·대상 해석·소스 호출
공용(plans/178 W2에서 `tools.py`의 `ApmTools`에서 옮김)."""

from __future__ import annotations

import difflib
import math
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apm_gateway.adapters.jennifer.api import SOURCE, XVIEW_WINDOW_MS, JenniferApi
from apm_gateway.application.business_map import (
    cache_fresh,
    parse_business_map,
)
from apm_gateway.application.jobs import (
    FILE_ONLY_KEY,
    MASKED_KEY,
    TEXT_PARTS_KEY,
    UNRESOLVED_KEY,
)
from apm_gateway.application.masking import mask_text, mask_url
from apm_gateway.application.name_search import (
    search_query,
)
from apm_gateway.application.node_address import NodeAddressService
from apm_gateway.application.resolver import Resolution
from apm_gateway.application.sources import (
    JenniferSource,
    SourceSet,
)
from apm_gateway.application.tool_common import (
    HEALTH_DEFAULT_LOOKBACK,
    Catalog,
    InstKey,
    Limits,
    Window,
    catalog_fingerprint,
    group_by_domain,
    hour_ceil,
    hour_floor,
    identifier_arg,
    inst_key,
    is_given,
    limit_notice,
    now_iso,
    profile_ref_of,
    tag_records,
    with_hostnames,
)
from apm_gateway.config import GatewayConfig
from apm_gateway.domain import signals as sig
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import API_ERROR, INVALID_ARGUMENT, SOURCE_UNAVAILABLE, ApmError
from apm_gateway.domain.events import level_rank

SOURCE_KIND = "apm_api"
HOURLY_TOP_APPLICATIONS = 5
LEVELS = ("fatal", "warning", "normal")
LEVEL_MODES = ("min", "exact")
RECORDS = ("event", "error")
_ERROR_TYPE = re.compile(r"[A-Za-z0-9_]{1,64}")
_METRIC_SUGGESTIONS = 3


class ApmCore:
    """도구 코어 공유 부분 — 도구군(`core`로 받는다)과 Facade `ApmTools`가 함께 쓴다."""

    def __init__(
        self,
        sources: SourceSet,
        cfg: GatewayConfig,
        *,
        clock: Callable[[], float],
        poller_status: Callable[[], dict[str, Any]] | None,
        node_address: NodeAddressService | None = None,
    ) -> None:
        self.sources = sources
        self.cfg = cfg
        self.clock = clock
        self.poller_status = poller_status
        # 노드 주소 조회기(plans/199) — 플래그 off면 None(`apm_instance_map` 칸 없음 = 현행)
        self.node_address = node_address
        self.th = cfg.policies.thresholds
        self.tz = cfg.runtime.timezone
        self._catalogs: dict[str, Catalog] = {}
        # 업무명 해석(plans/130 N-2) — 수동 매핑 · (소스, 도메인) → (읽은 시각, 업무 정의 목록)
        self._business_map = parse_business_map(cfg.policies.instance_map)
        self._business_defs: dict[tuple[str, int], tuple[float, list[dict[str, Any]]]] = {}

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
        sources: list[dict[str, Any]] | None = None,
        partial: bool = False,
        file_only: tuple[str, ...] = (),
        text_parts: dict[str, str] | None = None,
        masked: set[str] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        if resolution is not None and resolution.by_name:
            rows = with_hostnames(rows, resolution)
        payload: dict[str, Any] = {
            "rows": rows,
            "row_count": len(rows),
            "queried_at": now_iso(self.clock),
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
        if sources is None and resolution is not None:
            sources = resolution.sources
        if sources is not None:
            payload["sources"] = sources
        if partial or (resolution is not None and resolution.partial):
            payload["partial"] = True
        if file_only:
            payload[FILE_ONLY_KEY] = list(file_only)
        if text_parts:
            payload[TEXT_PARTS_KEY] = dict(text_parts)
        if masked:
            payload[MASKED_KEY] = sorted(masked)
        if isinstance(limits, Limits) and limits.unresolved:
            payload[UNRESOLVED_KEY] = list(limits.unresolved)
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
                ref = ref.replace(tzinfo=ZoneInfo(self.tz))
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
        return Window(end_ms - minutes * 60_000, end_ms, end_is_now, self.tz)

    async def resolve(
        self,
        hostname: str | None,
        instance_id: int | None = None,
        source_ids: list[str] | None = None,
        instance_name: str | None = None,
    ) -> Resolution:
        """대상 인스턴스 — hostname 정합 또는 인스턴스 이름 **정확 일치**(plans/130 N-3). 둘 다
        주면 hostname 정합 결과 중 그 이름만 남긴다(AND). 이름은 `query`와 같은 형식 검사를 HTTP
        전에 한다(문자열 · `QUERY_MAX`자 이하)."""
        self.sources.require_configured()
        host = str(hostname).strip() if is_given(hostname) else ""
        name = search_query(instance_name, "instance_name") if is_given(instance_name) else ""
        if not host and not name:
            raise ApmError(
                INVALID_ARGUMENT, "hostname 또는 instance_name이 필요하다(둘 다 비어 있음)"
            )
        if not host:
            return await self.sources.resolve_instance_name(name, instance_id, source_ids)
        return await self.sources.resolve(host, instance_id, source_ids, name or None)

    def api(self, source_id: str) -> JenniferApi:
        return self.sources.get(source_id).api

    def inst_label(self, key: InstKey) -> str:
        """인스턴스 문구 — 소스가 하나면 v4 그대로(id만)."""
        return str(key[1]) if len(self.sources) == 1 else f"{key[0]}:{key[1]}"

    def ids_text(self, keys: set[InstKey]) -> str:
        if len(self.sources) == 1:
            return str(sorted(iid for _, iid in keys))
        return "[" + ", ".join(sorted(self.inst_label(k) for k in keys)) + "]"

    async def realtime(
        self, resolution: Resolution, limits: Limits
    ) -> dict[InstKey, dict[str, Any]]:
        """해소 인스턴스의 실시간 스냅샷((소스, 도메인)당 1호출 · 대상 인스턴스만 묻는다)."""
        wanted = {inst_key(i) for i in resolution.instances}
        out: dict[InstKey, dict[str, Any]] = {}
        groups = group_by_domain(resolution)
        expect_calls(len(groups))
        for (sid, domain_id), ids in groups.items():
            try:
                for rec in tag_records(await self.api(sid).realtime(domain_id, ids), sid):
                    if inst_key(rec) in wanted:
                        out[inst_key(rec)] = rec
            except ApmError as e:
                limits.fail(limit_notice(e, "실시간", f"({self.sources.where(sid, domain_id)})"))
        missing = wanted - set(out)
        if missing:
            limits.append(f"[한계] 실시간 데이터 없는 인스턴스: {self.ids_text(missing)}")
        return out

    async def xview(
        self, resolution: Resolution, window: Window, limits: Limits
    ) -> tuple[list[dict[str, Any]], bool]:
        """X-View 기본 데이터를 1분 창으로 나눠 **창 전체**를 모은다(제니퍼 1분 창 제약 —
        상한 없음). (거래, 성공 여부)."""
        wanted = {inst_key(i) for i in resolution.instances}
        seen: set[tuple[str, str]] = set()
        txs: list[dict[str, Any]] = []
        any_ok = False
        groups = group_by_domain(resolution)
        expect_calls(math.ceil((window.end_ms - window.start_ms) / XVIEW_WINDOW_MS) * len(groups))
        for (sid, domain_id), ids in groups.items():
            t = window.start_ms
            while t < window.end_ms:
                chunk_end = min(t + XVIEW_WINDOW_MS, window.end_ms)
                query_end = chunk_end if chunk_end == window.end_ms else chunk_end - 1
                try:
                    batch = await self.api(sid).transactions(domain_id, ids, t, query_end)
                    any_ok = True
                except ApmError as e:
                    limits.fail(
                        limit_notice(e, "트랜잭션", f"({self.sources.where(sid, domain_id)})")
                    )
                    break
                for tx in tag_records(batch, sid, domain_id):
                    key = (
                        sid,
                        tx.get("txid")
                        or f"{tx.get('instance_id')}:{tx.get('end_time_ms')}:{len(txs)}",
                    )
                    if inst_key(tx) in wanted and key not in seen:
                        seen.add(key)
                        txs.append(tx)
                t = chunk_end
        return txs, any_ok

    @staticmethod
    def window_stats(txs: list[dict[str, Any]]) -> dict[str, Any]:
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

    async def hourly(
        self, resolution: Resolution, window: Window, limits: Limits
    ) -> dict[str, Any] | None:
        """시 단위 애플리케이션 통계(창이 길 때 맥락) — 시 경계로 내림·올림. 합계는 받은 **전**
        애플리케이션 행으로 낸다(종전 `max_row=20` 합계 누락 교정 · plans/134 W1 N-2)."""
        start, end = hour_floor(window.start_ms), hour_ceil(window.end_ms)
        rows: list[dict[str, Any]] = []
        groups = group_by_domain(resolution)
        expect_calls(len(groups))
        for (sid, domain_id), ids in groups.items():
            try:
                rows.extend(
                    tag_records(
                        await self.api(sid).application_status(domain_id, ids, start, end), sid
                    )
                )
            except ApmError as e:
                limits.fail(
                    limit_notice(e, "시 단위 통계", f"({self.sources.where(sid, domain_id)})")
                )
        if not rows:
            return None
        calls = sum(r["calls"] for r in rows)
        failures = sum(r["failures"] for r in rows)
        if all(r.get("total_response_ms") is not None for r in rows):
            weighted = float(sum(r["total_response_ms"] for r in rows))
        else:
            weighted = sum((r["response_time_avg_ms"] or 0) * r["calls"] for r in rows)
        top = sorted(rows, key=lambda r: r["response_time_avg_ms"] or 0, reverse=True)[
            :HOURLY_TOP_APPLICATIONS
        ]
        limits.append("[한계] 시 단위 통계 — 사건창보다 넓은 시간 경계로 집계된 값")
        limits.append(
            "[한계] 시 단위 통계 행 수는 서버 기본값이다(미공개 — W10) · 합계는 받은 전"
            " 애플리케이션 행으로 냈다 · top_applications는 평균 응답시간 상위 "
            f"{HOURLY_TOP_APPLICATIONS}개 요약(전 애플리케이션 목록이 아니다)"
        )
        return {
            "calls": calls,
            "failures": failures,
            "failure_rate": (failures / calls) if calls else None,
            "response_time_avg_ms": (weighted / calls) if calls else None,
            "max_response_time_ms": max((r["max_response_time_ms"] or 0) for r in rows),
            "application_count": len(rows),
            "top_applications": [{**r, "application": mask_url(r["application"])} for r in top],
            "hour_start": datetime.fromtimestamp(start / 1000, ZoneInfo(self.tz)).isoformat(
                timespec="seconds"
            ),
            "hour_end": datetime.fromtimestamp(end / 1000, ZoneInfo(self.tz)).isoformat(
                timespec="seconds"
            ),
        }

    @staticmethod
    def reason_tail(e: ApmError) -> str:
        """서버가 요청을 거부한 사유(`apm_api_error`)를 그대로 붙인다 — 허용값이 미공개인 인자
        (정렬 기준·간격 — COV E-05·E-06)를 서버가 거부하면 그 사유가 사용자에게 보여야 한다."""
        return f" — {mask_text(e.reason, limit=240)}" if e.code == API_ERROR else ""

    @staticmethod
    def all_failed(failures: list[tuple[str, ApmError]]) -> ApmError:
        """모든 단위가 실패했을 때의 오류 — 원인 코드가 모두 같으면 그 코드(서버 거부 사유 그대로),
        섞이면 `source_unavailable`."""
        codes = {e.code for _, e in failures}
        return ApmError(
            codes.pop() if len(codes) == 1 else SOURCE_UNAVAILABLE,
            "; ".join(f"{where}: {e.reason}" if where else e.reason for where, e in failures),
        )

    def where(self, source_id: str) -> str:
        where = self.sources.where(source_id)
        return f"({where})" if where else ""

    async def catalog(self, source_id: str) -> Catalog:
        """소스의 지표 카탈로그(TTL 캐시 · plans/134 N-6). 다시 읽어 지문이 바뀌었으면 그 수명 동안
        `[한계]`로 알린다(지표 추가·삭제 수)."""
        now = self.clock()
        cur = self._catalogs.get(source_id)
        if cur is not None and now - cur.fetched_at < self.cfg.runtime.metric_catalog_ttl_seconds:
            return cur
        expect_calls(1)
        scopes, invalid = await self.api(source_id).metric_catalog()
        fingerprint = catalog_fingerprint(scopes)
        note = None
        if cur is not None and cur.fingerprint != fingerprint:
            before = {(sc, m) for sc, names in cur.scopes.items() for m in names}
            after = {(sc, m) for sc, names in scopes.items() for m in names}
            note = (
                f"[한계] 지표 카탈로그 변경 감지{self.where(source_id)} — 지문 {cur.fingerprint}"
                f" → {fingerprint} · 추가 {len(after - before)} · 삭제 {len(before - after)}"
            )
        entry = Catalog(now, scopes, fingerprint, note, tuple(invalid))
        self._catalogs[source_id] = entry
        return entry

    @staticmethod
    def metric_names(metrics: list[str] | None) -> list[str]:
        """요청 지표 이름(중복 제거 · 순서 유지) — 식별자 형식만 여기서 본다(값은 카탈로그)."""
        if metrics is None:
            return []
        if isinstance(metrics, str) or not isinstance(metrics, list):
            raise ApmError(INVALID_ARGUMENT, "metrics는 지표 이름 목록이어야 한다")
        names: list[str] = []
        for raw in metrics:
            name = identifier_arg(raw, "metrics 항목")
            if name is not None and name not in names:
                names.append(name)
        return names

    async def metric_plan(
        self,
        source_ids: list[str],
        requested: list[str],
        scope: str,
        limits: Limits,
        *,
        all_unknown_raises: bool,
        candidates: dict[str, list[str]] | None = None,
    ) -> dict[str, list[tuple[str, str]]] | None:
        """요청 지표 → 소스별 `[(지표 식별자, 표시 이름)]`. 카탈로그(`scope` 군)로 검증한다.

        - 중립 이름(`heap_used_mb`)은 식별자로 바꾸고 표시 이름은 중립 이름이다. 식별자로 주면
          표시 이름은 대응 중립 이름(없으면 식별자 그대로)이다.
        - 고른 소스 **전부**의 카탈로그에 없는 지표는 빼고 `[한계]`(difflib 후보 ≤3)에 적는다
          (W2V-B2 — 선택 조건 하나로 보기를 막지 않는다). 요청 지표가 **전부** 그렇다면
          `all_unknown_raises`면 `invalid_argument`(지표가 본질인 시계열), 아니면 None(호출자가
          기본 지표로 조회).
        - 일부 소스 카탈로그에만 없으면 그 소스만 생략하고 `[한계]`.
        - 카탈로그를 읽지 못했거나 그 군이 모양 위반인 소스는 검증 없이 조회하고 `[한계]`
          (partial)로 알린다.
        - `candidates`를 주면 모르는 지표마다 후보(≤3)를 담고, 일부만 모를 때의 미해결 고지에도
          후보를 싣는다(도메인·업무 시계열 — 인스턴스·런타임 경로는 주지 않아 종전 그대로).
        """
        wanted: list[tuple[str, str]] = []
        for name in requested:
            mapped = JenniferApi.metric_id(name)
            pair = (mapped, name) if mapped else (name, JenniferApi.neutral_metric(name) or name)
            if all(pair[0] != mid for mid, _ in wanted):
                wanted.append(pair)
        known: dict[str, set[str] | None] = {}
        for sid in source_ids:
            try:
                entry = await self.catalog(sid)
            except ApmError as e:
                limits.fail(
                    limit_notice(
                        e,
                        "지표 카탈로그",
                        self.where(sid),
                        " — 지표 이름을 검증하지 못하고 그대로 조회했다",
                    )
                )
                known[sid] = None
                continue
            if entry.change_note:
                limits.append(entry.change_note)
            if scope in entry.invalid:
                limits.fail(
                    f"[한계] 지표 카탈로그 {scope} 군 모양 위반{self.where(sid)} — 지표 이름을"
                    " 검증하지 못하고 그대로 조회했다"
                )
                known[sid] = None
                continue
            known[sid] = set(entry.scopes.get(scope, []))
        unknown = [
            (mid, label)
            for mid, label in wanted
            if known and all(k is not None and mid not in k for k in known.values())
        ]
        if unknown:
            ids = {m for k in known.values() if k for m in k}
            pool = sorted(ids) + sorted(
                n for n, mid in JenniferApi.neutral_metrics().items() if mid in ids
            )
            parts = []
            for _mid, label in unknown:
                close = difflib.get_close_matches(label, pool, n=_METRIC_SUGGESTIONS)
                parts.append(f"{label}(후보: {', '.join(close) if close else '없음'})")
                if candidates is not None:
                    candidates[label] = close
            if len(unknown) == len(wanted) and all_unknown_raises:
                raise ApmError(
                    INVALID_ARGUMENT,
                    f"{scope} 지표 카탈로그에 없는 지표: {'; '.join(parts)} — 전체 목록은"
                    " apm_metrics(mode catalog)",
                )
            note = f"[한계] {scope} 지표 카탈로그에 없는 지표는 빼고 조회했다: {'; '.join(parts)}"
            if len(unknown) < len(wanted):
                shown = "; ".join(parts) if candidates is not None else ", ".join(
                    label for _, label in unknown
                )
                limits.unresolve(f"요청한 지표 {shown}는 지표 목록에 없어 빼고 조회했습니다")
            if all_unknown_raises:
                limits.fail(note)  # 시계열은 지표가 본질이다 — 일부가 빠진 결과
            else:
                limits.append(note)
            if len(unknown) == len(wanted):
                return None
            wanted = [w for w in wanted if w not in unknown]
        plan: dict[str, list[tuple[str, str]]] = {}
        for sid, names in known.items():
            plan[sid] = []
            for mid, label in wanted:
                if names is None or mid in names:
                    plan[sid].append((mid, label))
                else:
                    limits.append(
                        f"[한계] 지표 {label}는 {self.sources.where(sid) or '이 소스'} 카탈로그에"
                        " 없어 그 소스 조회를 생략했다"
                    )
        return plan

    async def business_defs_of(
        self,
        src: JenniferSource,
        domain_id: int,
        now: float,
        limits: Limits,
        failures: list[tuple[str, ApmError]] | None = None,
    ) -> list[dict[str, Any]] | None:
        """도메인의 업무 정의 목록(TTL `BUSINESS_CACHE_SECONDS` 캐시). 조회 실패는 `[한계]`(부분) —
        None(`failures`를 주면 (위치, 오류)도 모은다 — 전부 실패 판정용 · plans/134 W4)."""
        key = (src.source_id, domain_id)
        cached = self._business_defs.get(key)
        if cached is not None and cache_fresh(cached, now):
            return cached[1]
        try:
            defs = await src.api.businesses(domain_id)
        except ApmError as e:
            notice = limit_notice(
                e, "업무 목록", f"({self.sources.where(src.source_id, domain_id)})"
            )
            if failures is not None:
                failures.append((self.sources.where(src.source_id, domain_id), e))
            limits.fail(notice)
            return None
        self._business_defs[key] = (now, defs)
        return defs

    @staticmethod
    def event_options(
        level: str | None,
        level_mode: str | None,
        error_type: str | None,
        record: str | None,
    ) -> tuple[str | None, str, str | None, str]:
        lvl = str(level).strip().lower() if level is not None else None
        if lvl is not None and lvl not in LEVELS:
            raise ApmError(
                INVALID_ARGUMENT, f"level은 fatal·warning·normal 중 하나여야 한다: {level}"
            )
        mode = str(level_mode).strip().lower() if level_mode else "min"
        if mode not in LEVEL_MODES:
            raise ApmError(
                INVALID_ARGUMENT, f"level_mode는 min·exact 중 하나여야 한다: {level_mode}"
            )
        if mode == "exact" and lvl is None:
            raise ApmError(INVALID_ARGUMENT, "level_mode=exact에는 level이 필요하다")
        etype = None
        if error_type is not None and str(error_type).strip():
            etype = str(error_type).strip()
            if not _ERROR_TYPE.fullmatch(etype):
                raise ApmError(
                    INVALID_ARGUMENT,
                    f"error_type은 영문·숫자·밑줄 64자 이하여야 한다: {error_type}",
                )
            etype = etype.upper()
            if not JenniferApi.error_type_variants(etype):
                raise ApmError(
                    INVALID_ARGUMENT, f"error_type에 유형 이름이 없다(접두뿐): {error_type}"
                )
        rec = str(record).strip().lower() if record else "event"
        if rec not in RECORDS:
            raise ApmError(INVALID_ARGUMENT, f"record는 event·error 중 하나여야 한다: {record}")
        return lvl, mode, etype, rec

    def filter_events(
        self, events: list[dict[str, Any]], lvl: str | None, mode: str, etype: str | None
    ) -> list[dict[str, Any]]:
        """이벤트 레벨(`min` 그 레벨 이상 · `exact` 그 레벨만)·오류 유형 거르기 — `apm_events`와
        전 대상 이벤트(`apm_fleet`)가 같은 규칙을 쓴다(이벤트 레코드·이벤트 행 모두 `level`·
        `event_type`·`source_id` 칸이 있다)."""
        if lvl is not None:
            floor = level_rank(lvl)
            events = [
                e
                for e in events
                if (e["level"] == lvl if mode == "exact" else level_rank(e["level"]) >= floor)
            ]
        if etype is not None:
            events = [e for e in events if self.api(e["source_id"]).same_error_type(
                e["event_type"], etype
            )]
        return events

    @staticmethod
    async def fetch_errors(
        api: JenniferApi,
        domain_id: int,
        ids: list[int],
        window: Window,
        etype: str | None,
        where: str,
        limits: Limits,
    ) -> list[dict[str, Any]]:
        """오류 기록 1묶음. `error_type`이 있으면 API 표기 후보(정규화 이름 → `ERROR_` → `WARNING_`
        접두 · 최대 3회)를 차례로 물어 처음 비지 않은 결과를 쓰고, 맞은 표기를 `[한계]`에 적는다
        (운영 명명 U-13 미확정 · W1 검증 M-1 — 이벤트 쪽 접두 무시와 결과를 맞춘다)."""
        if etype is None:
            return await api.errors(domain_id, ids, window.start_ms, window.end_ms)
        variants = api.error_type_variants(etype)
        at = f"({where})" if where else ""
        for i, spelled in enumerate(variants):
            if i:
                expect_calls(1)
            batch = await api.errors(
                domain_id, ids, window.start_ms, window.end_ms, error_type=spelled
            )
            if batch:
                tried = f" — 앞 표기 {'·'.join(variants[:i])} 0건" if i else ""
                limits.append(
                    f"[한계] 오류 유형 API 표기 {spelled}로 조회{at}{tried} · 운영 명명 미확정"
                    "(U-13 · W10)"
                )
                return batch
        limits.append(
            f"[한계] 오류 유형 API 표기 {'·'.join(variants)} 모두 오류 기록 0건{at} · 운영 명명"
            " 미확정(U-13 · W10)"
        )
        return []

    @staticmethod
    def event_row(ev: dict[str, Any]) -> dict[str, Any]:
        return {
            "time_ms": ev["time_ms"],
            "level": ev["level"],
            "event_type": ev["event_type"],
            "event_kind": ev["event_kind"],
            "value": ev["value"],
            "message": mask_text(ev["message"], limit=None),
            "source_id": ev["source_id"],
            "domain_id": ev.get("domain_id"),
            "domain_name": ev.get("domain_name", ""),
            "instance_id": ev["instance_id"],
            "instance_name": ev["instance_name"],
            "instance_oid": ev.get("instance_oid"),
            "application": mask_url(ev["application"]),
            "profile_ref": profile_ref_of(ev, "time_ms"),
        }

    def at(self, ms: int) -> str:
        """epoch ms → 사람이 읽는 시각(APM_TIMEZONE · 초 단위)."""
        return datetime.fromtimestamp(ms / 1000, ZoneInfo(self.tz)).isoformat(timespec="seconds")

    # 옛 이름 별칭 — D-337 ③ 1사이클(plans/178)
    _at = at
    _all_failed = all_failed
    _reason_tail = reason_tail
    _resolve = resolve
    _inst_label = inst_label
    _catalog = catalog
    _metric_plan = metric_plan
    _business_defs_of = business_defs_of
    _event_options = event_options
    _filter_events = filter_events
    _event_row = event_row
