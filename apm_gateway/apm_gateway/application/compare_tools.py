"""변경 감지·GUID 추적·변경 전후·기간 비교 도구군 `CompareTools`(`apm_source_changes`·
`apm_transaction_trace`·`apm_change_impact`·`apm_period_compare` · plans/134 W2 N-7 · W5 N-13 ·
W6 A-1·A-2 · plans/178 W3에서 `tools.py`의 `ApmTools`에서 옮김)."""

from __future__ import annotations

import math
import unicodedata
from collections import Counter
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apm_gateway.adapters.jennifer.api import CHANGES_WINDOW_MS
from apm_gateway.application.apm_core import ApmCore
from apm_gateway.application.period_stats import (
    PERIOD_METRICS,
    period_stats,
    stats_delta,
    sum_stats,
)
from apm_gateway.application.resolver import Resolution
from apm_gateway.application.sources import STATUS_OK
from apm_gateway.application.tool_common import (
    ABSOLUTE_MAX_MS,
    FILE_ONLY_COLUMNS,
    InstKey,
    Limits,
    Window,
    check_n,
    group_by_domain,
    hour_ceil,
    hour_floor,
    identifier_fields,
    inst_key,
    inst_meta,
    is_given,
    limit_notice,
    positive_int,
    profile_ref_of,
    select_rows,
    tag_records,
    tx_fields,
)
from apm_gateway.domain import analysis as an
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import INVALID_ARGUMENT, SOURCE_UNAVAILABLE, ApmError

# plans/134 W2(N-7)
CHANGES_DEFAULT_MINUTES = 24 * 60
CHANGE_DETECTION_NOTE = (
    "[한계] 변경 감지(데이터 서버가 소스코드·리소스 변경을 인지한 시각) — 배포 확정 아님"
)
# plans/134 W5 N-13 — GUID 추적 창 기본값
TRACE_DEFAULT_MINUTES = 60
TRACE_AROUND_MINUTES = 5
GUID_MAX = 256
# plans/134 W6 A-2 — 변경 전후 비교 폭 기본값(분) · 구간 지표(X-View 칸) · 증감을 내는 지표
CHANGE_WIDTH_MINUTES = 60
_XVIEW_SIDE_METRICS = (
    "calls",
    "tx_errors",
    "error_rate",
    "avg_response_ms",
    "p95_response_ms",
    "max_response_ms",
)
_IMPACT_METRICS = (
    "calls",
    "tx_errors",
    "error_rate",
    "avg_response_ms",
    "p95_response_ms",
    "error_records",
)
# plans/134 W6 A-1 — 기간 비교 구간 이름(지표는 `period_stats.PERIOD_METRICS`)
_PERIOD_LABELS = {"current": "현재", "baseline": "기준"}


class CompareTools:
    """변경 감지·GUID 추적·변경 전후·기간 비교 도구군 — 코어(`core`)를 받아 쓴다."""

    def __init__(self, core: ApmCore) -> None:
        self.core = core

    async def apm_source_changes(
        self,
        hostname: str | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        """소스코드(리소스) 변경 감지 이력(plans/134 N-7) — 25시간 이하 조각 · 겹침 제거."""
        tool = "apm_source_changes"
        res = await self.core.resolve(hostname, None, source_ids, instance_name)
        window = self.core.window(
            reference_time, lookback_minutes, default_minutes=CHANGES_DEFAULT_MINUTES
        )
        assert window is not None
        limits = Limits([CHANGE_DETECTION_NOTE])
        rows = await self._change_rows(res, window, limits)
        return self.core.ok(
            tool, rows, resolution=res, window=window, limits=limits, partial=limits.partial
        )

    async def _change_rows(
        self, res: Resolution, window: Window, limits: Limits
    ) -> list[dict[str, Any]]:
        """변경 감지 행(최근 순) — 25시간 이하 조각 · 겹침 제거 · 받은 조각의 행은 남긴다
        (`apm_source_changes`·`apm_change_impact` 공용 · 묶음이 전부 실패하면 오류)."""
        names = {inst_key(i): i["instance_name"] for i in res.instances}
        seen: set[tuple[str, int, int | None, int | None]] = set()
        rows: list[dict[str, Any]] = []
        failures: list[tuple[str, ApmError]] = []
        groups = group_by_domain(res)
        span = window.end_ms - window.start_ms
        chunks = max(1, math.ceil(span / CHANGES_WINDOW_MS))
        expect_calls(chunks * len(groups))
        zone = ZoneInfo(self.core.tz)
        for sid, domain_id in groups:
            t = window.start_ms
            done = 0  # 이 묶음에서 받은 조각 수
            while True:
                chunk_end = min(t + CHANGES_WINDOW_MS, window.end_ms)
                try:
                    batch = await self.core.api(sid).source_changes(domain_id, t, chunk_end)
                except ApmError as e:
                    where = self.core.sources.where(sid, domain_id)
                    notice = limit_notice(
                        e,
                        "변경 이력",
                        f"({where} · 조각 {done + 1}/{chunks}부터 — 남은 조각 생략)",
                        self.core.reason_tail(e),
                    )
                    if not done:  # 받은 조각이 없으면 묶음 실패(전부 실패면 오류)
                        failures.append((where, e))
                    limits.fail(notice)
                    # 받은 조각의 행은 남긴다(partial · W2V-G1). 남은 조각은 같은 원인으로
                    # 실패할 가능성이 커 부르지 않는다(X-View 조각과 같은 처리).
                    break
                done += 1
                for change in batch:
                    key = (sid, domain_id, change["instance_id"], change["change_detected_ms"])
                    if (sid, change["instance_id"]) not in names or key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "source_id": sid,
                            "domain_id": domain_id,
                            "instance_id": change["instance_id"],
                            "instance_name": names[(sid, change["instance_id"])],
                            "change_detected_ms": change["change_detected_ms"],
                            # 사람이 읽는 시각(APM_TIMEZONE · W2V-B6) — 원시 epoch ms는 위 칸
                            "change_detected_at": datetime.fromtimestamp(
                                change["change_detected_ms"] / 1000, zone
                            ).isoformat(timespec="seconds")
                            if change["change_detected_ms"] is not None
                            else None,
                        }
                    )
                if chunk_end >= window.end_ms:
                    break
                t = chunk_end
        if failures and len(failures) == len(groups):
            raise self.core.all_failed(failures)
        rows.sort(key=lambda r: r["change_detected_ms"] or 0, reverse=True)
        return rows

    # ── W5·W6 도구(plans/134 N-13 · A-1~A-3) ───────────────────

    @staticmethod
    def guid(value: Any) -> str:
        """GUID 인자 — 앞뒤 공백 제거 뒤 1~256자 · 공백·제어·서식 문자 없음. GUID 형식은 미검증이라
        (W10) 그 밖 형식 제한은 두지 않는다. 길이를 먼저 본다(긴 입력을 끝까지 훑지 않는다)."""
        text = str(value).strip() if value is not None else ""
        if not text or len(text) > GUID_MAX:
            raise ApmError(
                INVALID_ARGUMENT, f"guid는 공백 제거 뒤 1~{GUID_MAX}자여야 한다({len(text)}자)"
            )
        if any(ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf") for ch in text):
            raise ApmError(INVALID_ARGUMENT, "guid에 공백·제어 문자를 둘 수 없다")
        return text

    def _trace_window(
        self,
        reference_time: str | None,
        lookback_minutes: int | None,
        around_ms: int | None,
        around_minutes: int | None,
        limits: Limits,
    ) -> Window:
        """GUID 추적 창 — 명시 기간 > 앞 결과 시각(`around_ms`) ± `around_minutes`(기본 5) > 최근
        60분. 명시 기간이 아니면 그 사실을 `[한계]`로 적는다(N-13). `around_ms` 없이 받은
        `around_minutes`는 쓰지 않고 그 사실을 적는다(VG-3)."""
        if around_minutes is not None and around_ms is None:
            limits.append("[한계] around_minutes는 around_ms 없이 쓰지 않았다")
        if reference_time is not None or lookback_minutes is not None:
            window = self.core.window(
                reference_time, lookback_minutes, default_minutes=TRACE_DEFAULT_MINUTES
            )
            assert window is not None
            return window
        if around_ms is not None:
            center = positive_int(around_ms, "around_ms", 0)
            minutes = positive_int(around_minutes, "around_minutes", TRACE_AROUND_MINUTES)
            limits.append(f"[한계] 기간 미지정 — ±{minutes}분(앞 결과 시각 기준)")
            return Window(
                center - minutes * 60_000, center + minutes * 60_000, False, self.core.tz
            )
        limits.append(f"[한계] 기간 미지정 — 최근 {TRACE_DEFAULT_MINUTES}분")
        window = self.core.window(
            None, TRACE_DEFAULT_MINUTES, default_minutes=TRACE_DEFAULT_MINUTES
        )
        assert window is not None
        return window

    async def apm_transaction_trace(
        self,
        guid: str,
        hostname: str | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        around_ms: int | None = None,
        around_minutes: int | None = None,
        source_ids: list[str] | None = None,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        """GUID가 같은 거래 묶음(plans/134 W5 N-13 · A-3). 범위 = `hostname`(·`instance_name`)의
        정합 (소스, 도메인) · 없으면 고른 소스의 전 도메인 — 도메인마다 1호출. (소스, 도메인,
        txid)로 중복을 지우고 원천 시작 시각 순으로 `trace_order`를 매긴다. 호출 관계는 만들지
        않는다."""
        tool = "apm_transaction_trace"
        gid = self.guid(guid)
        limits = Limits(
            ["[한계] GUID가 같은 거래 묶음이다 — 호출 관계(토폴로지)를 뜻하지 않는다"]
        )
        window = self._trace_window(
            reference_time, lookback_minutes, around_ms, around_minutes, limits
        )
        res: Resolution | None = None
        statuses: list[dict[str, Any]] | None = None
        source_partial = False
        if is_given(hostname) or is_given(instance_name):
            res = await self.core.resolve(hostname, None, source_ids, instance_name)
            groups = list(group_by_domain(res))
        else:
            usable, statuses, found = await self.core.sources.available(
                self.core.sources.select(source_ids)
            )
            limits.extend(found)
            source_partial = any(row["status"] != STATUS_OK for row in statuses)
            groups = [
                (src.source_id, d["domain_id"])
                for src, inv in usable
                for d in inv.domains
                if d.get("domain_id") is not None
            ]
        expect_calls(len(groups))
        hits: list[dict[str, Any]] = []
        failures: list[tuple[str, ApmError]] = []
        foreign = 0
        for sid, domain_id in groups:
            where = self.core.sources.where(sid, domain_id)
            try:
                batch = await self.core.api(sid).transactions_by_guid(
                    domain_id, gid, window.start_ms, window.end_ms
                )
            except ApmError as e:
                notice = limit_notice(e, "GUID 거래", f"({where})", self.core.reason_tail(e))
                failures.append((where, e))
                limits.fail(notice)
                continue
            for tx in tag_records(batch, sid, domain_id):
                if tx.get("guid") and tx["guid"] != gid:
                    foreign += 1  # 원천이 다른 GUID를 섞어 돌려줬다 — 묶음이 아니다
                    continue
                hits.append(tx)
        if failures and len(failures) == len(groups):
            raise self.core.all_failed(failures)
        seen: set[tuple[str, Any, str]] = set()
        unique: list[dict[str, Any]] = []
        for tx in hits:
            if tx.get("txid"):
                key = (tx["source_id"], tx.get("domain_id"), tx["txid"])
                if key in seen:
                    continue
                seen.add(key)
            unique.append(tx)
        # 히트가 걸친 (소스, 도메인)은 질의 묶음이 아니라 중복 제거 뒤 행으로 센다 — 원천이
        # domain_id를 무시하고 같은 거래를 두 도메인 질의에 돌려줘도 한 번이다(VG-6)
        with_hits = {(tx["source_id"], tx.get("domain_id")) for tx in unique}
        unique.sort(
            key=lambda t: (
                t.get("start_time_ms") is None,
                t.get("start_time_ms") or 0,
                t["source_id"],
                t.get("domain_id") or 0,
                t.get("txid") or "",
            )
        )
        rows = [
            {
                "source_id": tx["source_id"],
                **tx_fields(tx),
                "profile_ref": profile_ref_of(tx, "end_time_ms"),
                "trace_order": order,
            }
            for order, tx in enumerate(unique, 1)
        ]
        if len(with_hits) > 1:
            limits.append(
                "[한계] 소스·도메인 시계 차이를 보정하지 않았다 — 순서는 각 원천 시각 기준"
            )
        if foreign:
            limits.append(
                f"[한계] 원천이 다른 GUID의 거래 {foreign}건을 함께 돌려줘 뺐다 — guid 인자 의미"
                " 확인 필요(W10)"
            )
        if not rows:
            limits.append(
                "[한계] 구간 안에서 GUID 거래를 찾지 못했다"
                f"(구간 {self.core.at(window.start_ms)}~{self.core.at(window.end_ms)})"
            )
        starts = [t["start_time_ms"] for t in unique if t.get("start_time_ms") is not None]
        ends = [t["end_time_ms"] for t in unique if t.get("end_time_ms") is not None]
        first = min(starts) if starts else None
        last = max(ends) if ends else None
        summary = {
            "guid": gid,
            "transactions": len(rows),
            "domains_queried": len(groups),
            "domains_with_hits": len(with_hits),
            "domains_failed": len(failures),
            "sources": [
                sid for sid in self.core.sources.ids if any(g[0] == sid for g in with_hits)
            ],
            "first_start_ms": first,
            "last_end_ms": last,
            "span_ms": (last - first) if first is not None and last is not None else None,
            "instances": list(
                dict.fromkeys(t["instance_name"] for t in unique if t.get("instance_name"))
            ),
        }
        return self.core.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            limits=limits,
            sources=statuses,
            partial=limits.partial or source_partial,
            file_only=FILE_ONLY_COLUMNS,
            masked=identifier_fields(unique),
            summary=summary,
        )

    async def _impact_side(
        self,
        res: Resolution,
        start_ms: int,
        end_ms: int,
        *,
        inclusive_end: bool,
        what: str,
        limits: Limits,
    ) -> tuple[dict[str, Any], int, int]:
        """변경 전/후 구간 1개(인스턴스 1개)의 지표 → (지표, 원천 시도 수, 실패 수). X-View(호출·
        오류·응답시간 — 1분 조각)와 오류 기록을 따로 받고, 실패한 원천의 칸만 None으로 둔다
        (0으로 세지 않는다 · 조각 하나라도 실패하면 X-View 칸 전부 None)."""
        inst = res.instances[0]
        sid, domain_id = inst["source_id"], inst["domain_id"]
        where = self.core.sources.where(sid, domain_id)
        win = Window(start_ms, end_ms if inclusive_end else end_ms - 1, False, self.core.tz)
        side: dict[str, Any] = {"start_ms": start_ms, "end_ms": end_ms}
        fails = 0
        sub = Limits()
        txs, ok = await self.core.xview(res, win, sub)
        limits.extend(sub)
        if sub.partial or not ok:
            fails += 1
            limits.fail(f"[한계] {what} X-View 조회 실패 — 호출·오류·응답시간 N/A")
            side.update(dict.fromkeys(_XVIEW_SIDE_METRICS))
        else:
            times = [t["response_time_ms"] for t in txs if t.get("response_time_ms") is not None]
            errors = sum(1 for t in txs if t.get("error_type"))
            side.update(
                calls=len(txs),
                tx_errors=errors,
                error_rate=an.rate(errors, len(txs)),
                avg_response_ms=an.weighted_mean(float(sum(times)), len(times)),
                p95_response_ms=an.p95(times),
                max_response_ms=max(times) if times else None,
            )
        expect_calls(1)
        try:
            records = await self.core.fetch_errors(
                self.core.api(sid), domain_id, [inst["instance_id"]], win, None, where, limits
            )
        except ApmError as e:
            notice = limit_notice(e, f"{what} 오류 기록", f"({where})", self.core.reason_tail(e))
            fails += 1
            limits.fail(notice)
            side.update(error_records=None, errors_by_type=None)
        else:
            mine = [
                r for r in tag_records(records, sid, domain_id) if inst_key(r) == inst_key(inst)
            ]
            side.update(
                error_records=len(mine),
                errors_by_type=[
                    {"error_type": k, "count": v}
                    for k, v in Counter(r["error_type"] or "(없음)" for r in mine).most_common()
                ],
            )
        return side, 2, fails

    async def apm_change_impact(
        self,
        hostname: str | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        width_minutes: int | None = None,
        source_ids: list[str] | None = None,
        n: int | None = None,
        full: bool = False,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        """소스 변경 감지 전후 비교(plans/134 W6 A-2 · F-09). 변경 목록은 `apm_source_changes`와
        같은 로직이고, 변경(인스턴스 단위)마다 전 `[t−w, t)` · 후 `[t, min(t+w, 지금))`의 그
        인스턴스 지표를 받아 증감을 낸다. 동반 변화일 뿐 원인 확정이 아니다."""
        tool = "apm_change_impact"
        width = positive_int(width_minutes, "width_minutes", CHANGE_WIDTH_MINUTES)
        top_n = check_n(n, default=None)
        res = await self.core.resolve(hostname, None, source_ids, instance_name)
        window = self.core.window(
            reference_time, lookback_minutes, default_minutes=CHANGES_DEFAULT_MINUTES
        )
        assert window is not None
        limits = Limits(
            [
                "[한계] 변경 감지 시각 전후의 동반 변화다 — 원인 확정이 아니다",
                CHANGE_DETECTION_NOTE,
            ]
        )
        changes = await self._change_rows(res, window, limits)
        selected = select_rows(changes, top_n, full)
        if not changes:
            limits.append("[한계] 구간 안 변경 감지 0건")
        elif len(selected) < len(changes):
            limits.append(
                f"[한계] 변경 감지 {len(changes)}건 중 최근 {len(selected)}건을 비교했다(n)"
                " — 전부는 full"
            )
        by_key = {inst_key(i): i for i in res.instances}
        now_ms = int(self.core.clock() * 1000)
        w_ms = width * 60_000
        rows: list[dict[str, Any]] = []
        attempts = fails = 0
        for change in selected:
            t = change["change_detected_ms"]
            label = f"{change['instance_name']} {change['change_detected_at'] or ''}".strip()
            if t is None:
                limits.fail(f"[한계] 감지 시각이 없는 변경은 비교하지 못했다({label})")
                continue
            one = Resolution(
                res.hostname,
                [by_key[(change["source_id"], change["instance_id"])]],
                res.confidence,
                res.reason,
            )
            before, tried, failed = await self._impact_side(
                one, t - w_ms, t, inclusive_end=False, what=f"{label} 변경 전", limits=limits
            )
            attempts, fails = attempts + tried, fails + failed
            after_end = min(t + w_ms, now_ms)
            if after_end - t < w_ms:
                limits.append(
                    f"[한계] 변경 뒤 구간이 아직 {round(max(0, after_end - t) / 60_000, 1):g}분이다"
                    f"(비교 폭 {width}분) — {label}"
                )
            if after_end > t:
                after, tried, failed = await self._impact_side(
                    one,
                    t,
                    after_end,
                    inclusive_end=after_end == now_ms,
                    what=f"{label} 변경 후",
                    limits=limits,
                )
                attempts, fails = attempts + tried, fails + failed
            else:  # 감지 시각이 지금 이후(시계 차이) — 뒤 구간이 없다
                after = {
                    "start_ms": t,
                    "end_ms": t,
                    **dict.fromkeys(_XVIEW_SIDE_METRICS),
                    "error_records": None,
                    "errors_by_type": None,
                }
            rows.append(
                {
                    "source_id": change["source_id"],
                    "domain_id": change["domain_id"],
                    "instance_id": change["instance_id"],
                    "instance_name": change["instance_name"],
                    "change_detected_ms": t,
                    "change_detected_at": change["change_detected_at"],
                    "width_minutes": width,
                    "before": before,
                    "after": after,
                    "delta": {
                        m: (an.rate_delta if m == "error_rate" else an.delta)(after[m], before[m])
                        for m in _IMPACT_METRICS
                    },
                }
            )
        if attempts and fails == attempts:
            raise ApmError(
                SOURCE_UNAVAILABLE, "변경 전후 지표 조회가 모두 실패했다: " + "; ".join(limits)
            )
        return self.core.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            limits=limits,
            partial=limits.partial,
            summary={
                "changes": len(changes),
                "compared": len(rows),
                "window": window.as_dict(),
                "width_minutes": width,
            },
        )

    def _absolute_ms(self, value: Any, name: str) -> int:
        """명시 시각(ISO 8601 · 시간대 없으면 APM_TIMEZONE) → epoch ms. 비었거나 형식 밖이면
        `invalid_argument`."""
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ApmError(INVALID_ARGUMENT, f"{name}가 필요하다(ISO 8601)")
        try:
            at = datetime.fromisoformat(text)
        except ValueError as e:
            raise ApmError(
                INVALID_ARGUMENT, f"{name}는 ISO 8601이어야 한다: {text[:64]!r}"
            ) from e
        if at.tzinfo is None:
            at = at.replace(tzinfo=ZoneInfo(self.core.tz))
        try:
            ms = int(at.timestamp() * 1000)
        except (OverflowError, ValueError, OSError):
            ms = -1
        # 시 경계로 넓힌 뒤에도 시각으로 다시 나타낼 수 있는 범위만(9999-12-31 근처는 올림에서
        # 넘친다 — 내부 오류·스택 대신 인자 오류 · plans/134 W7 I-2)
        if not 0 <= ms < ABSOLUTE_MAX_MS:
            raise ApmError(
                INVALID_ARGUMENT, f"{name}는 1970~9998년 범위여야 한다: {text[:64]!r}"
            )
        return ms

    async def apm_period_compare(
        self,
        hostname: str | None = None,
        current_start: str | None = None,
        current_end: str | None = None,
        baseline_start: str | None = None,
        baseline_end: str | None = None,
        source_ids: list[str] | None = None,
        n: int | None = None,
        full: bool = False,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        """두 명시 구간 비교(plans/134 W6 A-1 — 조사 소비 · 채팅 배선은 본체 보기
        `apm.period_compare`). 시 단위 애플리케이션 통계를 인스턴스·구간마다 받아(시 경계로
        넓힌다) 호출·실패·실패율·가중 평균·최대와 증감을 낸다. p95는 시 단위 통계에 분포가
        없어 싣지 않는다. 한쪽에만 있는 인스턴스는 N/A다."""
        tool = "apm_period_compare"
        top_n = check_n(n, default=None)
        requested: dict[str, tuple[int, int]] = {}
        for period, start_raw, end_raw in (
            ("current", current_start, current_end),
            ("baseline", baseline_start, baseline_end),
        ):
            start = self._absolute_ms(start_raw, f"{period}_start")
            end = self._absolute_ms(end_raw, f"{period}_end")
            if start >= end:
                raise ApmError(
                    INVALID_ARGUMENT, f"{period}_start는 {period}_end보다 앞이어야 한다"
                )
            requested[period] = (start, end)
        res = await self.core.resolve(hostname, None, source_ids, instance_name)
        limits = Limits()
        hours: dict[str, tuple[int, int]] = {}
        for period, (start, end) in requested.items():
            hours[period] = (hour_floor(start), hour_ceil(end))
            span = f"{self.core.at(hours[period][0])}~{self.core.at(hours[period][1])}"
            limits.append(
                f"[한계] 시 단위 통계 — {_PERIOD_LABELS[period]} 구간을 시 경계로 맞췄다"
                + (
                    f"(요청 {self.core.at(start)}~{self.core.at(end)} → 조회 {span})"
                    if hours[period] != (start, end)
                    else f"({span})"
                )
            )
        lengths = {p: (e - s) / 3_600_000 for p, (s, e) in hours.items()}
        if lengths["current"] != lengths["baseline"]:
            limits.append(
                "[한계] 두 구간 길이가 다르다 — 합계 비교 주의(평균·비율은 비교 가능)"
                f"(현재 {lengths['current']:g}시간 · 기준 {lengths['baseline']:g}시간)"
            )
        limits.append(
            "[한계] p95는 싣지 않았다 — 시 단위 통계에는 응답시간 분포가 없다(구간 p95의 평균은"
            " 계산하지 않는다)"
        )
        limits.append(
            "[한계] 시 단위 통계 행 수는 서버 기본값이다(미공개 — W10) · 합계는 받은 전"
            " 애플리케이션 행으로 냈다"
        )
        stats: dict[tuple[InstKey, str], dict[str, Any] | None] = {}
        failed: dict[str, list[str]] = {"current": [], "baseline": []}
        failures: list[tuple[str, ApmError]] = []
        unweighted: list[str] = []
        expect_calls(len(res.instances) * len(hours))
        for inst in res.instances:
            label = self.core.inst_label(inst_key(inst))
            for period, (start, end) in hours.items():
                try:
                    app_rows = await self.core.api(inst["source_id"]).application_status(
                        inst["domain_id"], [inst["instance_id"]], start, end
                    )
                except ApmError as e:
                    notice = limit_notice(
                        e,
                        f"{_PERIOD_LABELS[period]} 구간 통계",
                        f"(인스턴스 {label})",
                        self.core.reason_tail(e),
                    )
                    failures.append((f"인스턴스 {label} {_PERIOD_LABELS[period]} 구간", e))
                    failed[period].append(label)
                    limits.fail(notice)
                    stats[(inst_key(inst), period)] = None
                    continue
                if not app_rows:
                    stats[(inst_key(inst), period)] = None
                    limits.append(
                        f"[한계] 인스턴스 {inst['instance_name'] or label}은"
                        f" {_PERIOD_LABELS[period]} 구간 통계 행이 없다 — 증감 N/A(0으로 채우지"
                        " 않았다)"
                    )
                    continue
                stat, missing = period_stats(app_rows)
                stats[(inst_key(inst), period)] = stat
                if missing:
                    unweighted.append(f"{label} {_PERIOD_LABELS[period]}")
        if failures and len(failures) == len(res.instances) * len(hours):
            raise self.core.all_failed(failures)
        if unweighted:
            limits.append(
                "[한계] 평균 응답시간 계산 불가 — 총 응답시간 칸이 없는 통계 행이 있다(가중 평균"
                f" 재료 없음 · {', '.join(unweighted)})"
            )
        summary: dict[str, Any] = {}
        overall: dict[str, dict[str, Any] | None] = {}
        for period, (start, end) in requested.items():
            present = [
                s for i in res.instances if (s := stats.get((inst_key(i), period))) is not None
            ]
            if failed[period]:
                overall[period] = None
                limits.append(
                    f"[한계] {_PERIOD_LABELS[period]} 구간 전체 합계 계산 불가 — 조회 실패"
                    f" 인스턴스 {', '.join(failed[period])}(0으로 세지 않았다)"
                )
            elif not present:
                overall[period] = None
            else:
                overall[period] = sum_stats(present)
            summary[period] = {
                "start": self.core.at(start),
                "end": self.core.at(end),
                "hour_start": self.core.at(hours[period][0]),
                "hour_end": self.core.at(hours[period][1]),
                "hours": lengths[period],
                **(overall[period] or dict.fromkeys((*PERIOD_METRICS, "total_response_ms"))),
            }
        summary["delta"] = stats_delta(overall["current"], overall["baseline"])
        summary["instances"] = len(res.instances)
        rows = [
            {
                **inst_meta(inst),
                "current": stats.get((inst_key(inst), "current")),
                "baseline": stats.get((inst_key(inst), "baseline")),
                "delta": stats_delta(
                    stats.get((inst_key(inst), "current")), stats.get((inst_key(inst), "baseline"))
                ),
            }
            for inst in res.instances
        ]
        rows.sort(
            key=lambda r: (
                r["current"] is None,
                -(r["current"]["calls"] if r["current"] else 0),
                r["source_id"],
                r["instance_id"],
            )
        )
        shown = select_rows(rows, top_n, full)
        if len(shown) < len(rows):
            limits.append(
                f"[한계] 인스턴스 {len(rows)}개 중 현재 구간 호출 수 상위 {len(shown)}개 행만"
                " 실었다 — summary는 전 인스턴스 합계 · 전부는 full"
            )
        return self.core.ok(
            tool,
            shown,
            resolution=res,
            limits=limits,
            partial=limits.partial,
            summary=summary,
        )
