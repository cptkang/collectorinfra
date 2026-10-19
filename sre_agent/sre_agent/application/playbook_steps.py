"""플레이북 스텝 라이브러리 — 액션 ID → 구현 (plans/187 §5.3 · §4.1 · D-341).

플레이북 YAML은 액션 ID만 쓰고, 관측 제품 어휘(도구명·응답 키)는 이 모듈까지만 둔다(§5.2 — 벤더
중립).
스텝은 결정적이다 — LLM을 부르지 않고, 판정은 게이트웨이 `was_signals`를 인용하거나(D-274 ⑤)
아래에 적은 결정적 규칙만 쓴다. 판독 규칙 R-A~R-H(enum)는 W3가 `data`(원자료)로 계산한다.

- 스텝 함수 시그니처: `StepFn = (StepContext) -> StepResult`. 실행기(`playbook_runner`)가
  `step`·`action`·`title`·`tools`·`elapsed_ms`를 채운다.
- 도구 호출은 `StepContext.call(axis, calls)`로만 한다 — 축(apm·polestar)별 배치 콜러블(잡 단위
  캐시를 거친 것)을 쓰고, 호출한 도구명·원시 출력을 기록한다(G-25 중요도 판정 입력).
- 결손은 `missing`(도구명 또는 지표 종류)과 `reason`으로 남긴다(침묵 금지). 폴스타 소스를 결정적으로
  정할 수 없으면 폴스타 축을 호출하지 않고 「폴스타 서버 미확정」으로 남긴다(§8 위험 1 ·
  plans/165 F3).
- S4·S5·S7(폴스타 변경)은 사전수집 `evidence_prefetch`의 같은 함수(`build_calls`·`_series`·
  `_alarms`·`_changes`)와 `correlation.correlate`를 재사용한다(G-24).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Literal

from sre_agent.application.evidence_prefetch import (
    METRIC_KINDS,
    TOOL_ALARMS,
    TOOL_CHANGES,
    TOOL_METRIC,
    EvidenceScope,
    _alarms,
    _changes,
    _series,
    build_calls,
)
from sre_agent.domain.correlation import EpisodePoint, MetricSeries, correlate
from sre_agent.domain.source_adapter import (
    DEFAULT_REGISTRY,
    DPM,
    ITSM,
    NOT_CONNECTED,
    AdapterRegistry,
    fetch_or_not_connected,
)

BatchCaller = Callable[[Sequence[tuple[str, dict]]], list[str]]
Axis = Literal["apm", "polestar"]
StepStatus = Literal["ok", "missing", "skipped", "pending_synthesis"]

# ── 도구명 (제품 어휘는 여기에만) ────────────────────────────────────────────
APM_INSTANCE_MAP = "apm_instance_map"
APM_APP_HEALTH = "apm_app_health"
APM_PERIOD_COMPARE = "apm_period_compare"
APM_RUNTIME_HEALTH = "apm_runtime_health"
APM_RESOURCE_POOL = "apm_resource_pool"
APM_SLOW_TX = "apm_slow_transactions"
APM_TX_PROFILE = "apm_transaction_profile"
APM_EVENTS = "apm_events"
APM_ACTIVE_SERVICES = "apm_active_services"
APM_SOURCE_CHANGES = "apm_source_changes"
APM_CHANGE_IMPACT = "apm_change_impact"
POLESTAR_PROCESS = "polestar_process_snapshot"
POLESTAR_RESOURCE_STATUS = "polestar_resource_status"
POLESTAR_TOPOLOGY = "polestar_topology"
POLESTAR_ALARM_HISTORY = "polestar_alarm_history"

#: 폴스타 소스 미확정 표기(§8 위험 1) — 폴스타 축 결손 사유의 머리말.
POLESTAR_UNRESOLVED = "폴스타 서버 미확정"
#: S5 고정 문구 — 시간 근접은 원인 근거가 아니다(SRE 12장).
CORRELATION_NOT_CAUSATION = (
    "상관 ≠ 인과 — 시간상 가까운 알람·이벤트는 원인 후보일 뿐 원인 근거가 아니다"
)
#: S7 고정 문구.
CHANGE_CANDIDATE_NOTE = "변경은 「상관 후보」로만 표기한다 — 원인 확정이 아니다"
# 미연동 외부 소스 사유 `NOT_CONNECTED`는 `domain.source_adapter`가 정본이다(G-6 · §6).

#: 판정 출처 — 게이트웨이 `was_signals` 인용 / 이 모듈의 결정적 규칙.
VERDICT_SOURCE_GATEWAY = "apm_gateway"
VERDICT_SOURCE_RULE = "playbook_rule"
#: 역방향 대상 확정(`scope.host_to_was`)에서 이 호스트에 WAS가 없다는 판정(W4 후속 ③).
NO_WAS_VERDICT = "no_was"
#: [제안] p95가 기준선(전일·전주 평균)의 이 배수 이상이면 degraded — 스텝 인자 `p95_ratio`로 바꾼다.
DEFAULT_P95_RATIO = 2.0
#: 정합 신뢰도 중 「대상 확정 실패」로 보는 값(게이트웨이 어휘 high·medium·none + 방어적 low).
LOW_CONFIDENCE = frozenset({"", "none", "low"})

ACTION_TITLES: dict[str, str] = {
    "scope.resolve_was_targets": "대상 확정 · 범위 비교",
    "scope.host_to_was": "호스트의 WAS 인스턴스 영향",
    "apm.golden_signals": "WAS 골든 시그널",
    "apm.runtime_and_pool": "JVM 런타임 · DB 커넥션 풀",
    "apm.slow_tx_breakdown": "느린 트랜잭션 Top 시간 분해",
    "host.usage_trend": "호스트 자원 시간대별 추이",
    "xsource.related_alarms": "전후 연관 알람",
    "host.process_top": "프로세스 CPU 상위",
    "change.recent": "최근 변경 대조",
    "dpm.db_health": "DB 상태(DPM)",
    "synthesize": "종합",
    "apm.events_errors": "오류 유형 상위",
    "apm.active_services": "액티브 서비스",
    "host.resource_status": "자원 가용 상태",
    "host.topology": "토폴로지",
    "host.alarm_history": "같은 알람 재발 이력",
}


# ── 실행 문맥 · 결과 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BaselineWindow:
    """기준선 창 1개(전일·전주 동시간)."""

    name: str  # prev_day | prev_week
    start: str  # ISO(naive)
    end: str
    reference_time: str  # 그 창의 도구 기준 시각(= end)


@dataclass(frozen=True)
class RunWindows:
    """시간창 — 기준 시각 = 알람 시각, 사건창 = 알람 −before ~ +after(§4.0)."""

    alarm_time: str
    reference_time: str  # 도구 기준 시각 = 사건창 끝
    lookback_minutes: int  # before + after
    incident_start: str
    incident_end: str
    baselines: tuple[BaselineWindow, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunScope:
    """조사 범위 — S0(또는 페이로드)가 정한 대상. 이후 스텝의 입력이다."""

    alarm_instance: str | None = None
    host: str | None = None
    polestar_source: str | None = None
    polestar_server: str | None = None
    polestar_reason: str | None = None  # 폴스타 소스 미확정 사유(확정이면 None)
    alarm_name: str | None = None
    targets: list[dict[str, Any]] = field(default_factory=list)

    def target_names(self) -> list[str]:
        names = [str(t["instance"]) for t in self.targets if t.get("instance")]
        if not names and self.alarm_instance:
            names = [self.alarm_instance]
        return names

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StepResult:
    """스텝 결과 1건(§5.5 `sections[]`).

    status:   ok(부분 성공 포함 — 결손은 `missing`) · missing · skipped · pending_synthesis(S9
    자리).
    verdict:  degraded·normal·None(판정 불가) — 출처는 `verdict_source`.
    table:    표시용 `{"columns": [...], "rows": [[...]]}`.
    data:     판독 규칙(W3) 입력 원자료 — JSON 직렬화 가능한 값만.
    missing:  결손된 도구명(S4는 지표 종류).
    """

    step: str = ""
    action: str = ""
    title: str = ""
    status: StepStatus = "ok"
    verdict: str | None = None
    verdict_source: str | None = None
    table: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)
    badges: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reason: str | None = None
    elapsed_ms: int = 0
    #: 직렬화하지 않는 객체(상관 계산 재료 — MetricSeries·AlarmPoint 등).
    artifacts: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, *, include_data: bool = True) -> dict[str, Any]:
        out = {
            "step": self.step,
            "action": self.action,
            "title": self.title,
            "status": self.status,
            "verdict": self.verdict,
            "verdict_source": self.verdict_source,
            "table": self.table,
            "badges": list(self.badges),
            "tools": list(self.tools),
            "missing": list(self.missing),
            "reason": self.reason,
            "elapsed_ms": self.elapsed_ms,
        }
        if include_data:
            out["data"] = self.data
        return out


@dataclass(frozen=True)
class ToolResponse:
    """도구 응답 1건 — 성공이면 `data`, 실패면 `error`(사유 문장)."""

    tool: str
    args: dict[str, Any]
    data: dict[str, Any] | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.data is not None


def parse_response(tool: str, args: dict, raw: str) -> ToolResponse:
    """원시 JSON → `ToolResponse`. 게이트웨이 오류는 `코드: 사유`, 폴스타 오류는 문장 그대로."""
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return ToolResponse(tool, args, None, "응답 JSON 파싱 실패")
    if not isinstance(obj, dict):
        return ToolResponse(tool, args, None, "응답 형식 오류")
    if obj.get("error"):
        reason = obj.get("reason")
        msg = f"{obj['error']}: {reason}" if reason else str(obj["error"])
        return ToolResponse(tool, args, None, msg)
    return ToolResponse(tool, args, obj, None)


class StepContext:
    """스텝 1회 실행 문맥. 스텝마다 새로 만든다(호출 기록이 스텝별)."""

    def __init__(
        self,
        *,
        payload: Mapping[str, Any],
        windows: RunWindows,
        scope: RunScope,
        args: Mapping[str, Any],
        targets: Sequence[str],
        polestar: BatchCaller | None,
        apm: BatchCaller | None,
        now: Callable[[], datetime],
        prior: Mapping[str, StepResult],
        adapters: AdapterRegistry | None = None,
    ) -> None:
        self.payload = payload
        self.windows = windows
        self.scope = scope
        self.args = dict(args)
        self.targets = list(targets)
        self.polestar = polestar
        self.apm = apm
        self.now = now
        self.prior = prior
        #: 외부 소스 어댑터(ITSM·DPM — §6). 미주입이면 전부 미연동 스텁.
        self.adapters = DEFAULT_REGISTRY if adapters is None else adapters
        self.tools: list[str] = []
        self.calls: list[tuple[str, dict]] = []
        self.outputs: list[str] = []

    @property
    def event(self) -> Mapping[str, Any]:
        ev = self.payload.get("event") if isinstance(self.payload, Mapping) else None
        return ev if isinstance(ev, Mapping) else {}

    def call(self, axis: Axis, calls: Sequence[tuple[str, dict]]) -> list[ToolResponse]:
        """축의 배치 콜러블로 calls를 부른다. 콜러블이 없으면 전부 오류 응답(호출하지 않음)."""
        if not calls:
            return []
        caller = self.apm if axis == "apm" else self.polestar
        if caller is None:
            msg = "APM 게이트웨이 미구성" if axis == "apm" else "폴스타 MCP 미구성"
            return [ToolResponse(t, dict(a), None, msg) for t, a in calls]
        raws = list(caller(list(calls)))
        if len(raws) < len(calls):
            raws += [json.dumps({"error": "응답 누락"})] * (len(calls) - len(raws))
        out: list[ToolResponse] = []
        for (tool, args), raw in zip(calls, raws):
            if tool not in self.tools:
                self.tools.append(tool)
            self.calls.append((tool, dict(args)))
            self.outputs.append(raw)
            out.append(parse_response(tool, dict(args), raw))
        return out

    def call_one(self, axis: Axis, tool: str, args: dict) -> ToolResponse:
        return self.call(axis, [(tool, args)])[0]


StepFn = Callable[[StepContext], StepResult]


# ── 공용 도우미 ─────────────────────────────────────────────────────────────


def naive_time(value: object) -> datetime | None:
    """ISO 문자열(`T`·공백 구분 · 시간대 표기 허용) → naive datetime. 해석 불가면 None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=None)
    except ValueError:
        return None


def realtime_badge(captured: object, alarm_time: str, now: Callable[[], datetime]) -> str:
    """실시간 전용 도구의 배지(§4.0) — 「현재값 · 알람 후 N분」. 수집 시각이 없으면 실행 시각."""
    at = naive_time(captured) or now().replace(tzinfo=None)
    alarm = naive_time(alarm_time)
    if alarm is None:
        return "현재값"
    minutes = int((at - alarm).total_seconds() // 60)
    return f"현재값 · 알람 후 {minutes}분" if minutes >= 0 else f"현재값 · 알람 {-minutes}분 전"


def _missing(tools: Sequence[str], reason: str) -> StepResult:
    return StepResult(status="missing", missing=list(dict.fromkeys(tools)), reason=reason)


def _add_missing(missing: list[str], name: str) -> None:
    if name not in missing:
        missing.append(name)


def _reasons(resps: Sequence[ToolResponse]) -> str:
    return " · ".join(f"{r.tool}: {r.error}" for r in resps if not r.ok)


def _polestar_reason(ctx: StepContext) -> str | None:
    """폴스타 축을 부를 수 없으면 사유 문장(미확정·미구성), 부를 수 있으면 None."""
    if not ctx.scope.polestar_source or not ctx.scope.polestar_server:
        detail = ctx.scope.polestar_reason or "폴스타 소스·서버명을 결정적으로 정할 수 없다"
        return f"{POLESTAR_UNRESOLVED} — {detail}"
    if ctx.polestar is None:
        return "폴스타 MCP 미구성"
    return None


def _evidence_scope(ctx: StepContext, granularity: str = "h") -> EvidenceScope:
    return EvidenceScope(
        source=str(ctx.scope.polestar_source),
        server_name=str(ctx.scope.polestar_server),
        reference_time=ctx.windows.reference_time,
        lookback_minutes=ctx.windows.lookback_minutes,
        granularity=granularity,
    )


def _signals(data: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    items = (data or {}).get("was_signals") or []
    return [s for s in items if isinstance(s, dict)]


def _first_row(data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    rows = (data or {}).get("rows") or []
    return rows[0] if rows and isinstance(rows[0], dict) else None


def _window_stats(data: Mapping[str, Any] | None, lookback_minutes: int) -> dict[str, Any] | None:
    """`apm_app_health`(과거 모드) 첫 행 window + TPS(= calls / 창 초)."""
    row = _first_row(data)
    win = (row or {}).get("window") if row else None
    if not isinstance(win, dict):
        return None
    calls = win.get("calls")
    tps = (
        round(float(calls) / (lookback_minutes * 60), 3)
        if isinstance(calls, (int, float)) and lookback_minutes > 0
        else None
    )
    return {
        "calls": calls,
        "errors": win.get("errors"),
        "error_rate": win.get("error_rate"),
        "p50_ms": win.get("response_time_p50_ms"),
        "p95_ms": win.get("response_time_p95_ms"),
        "max_ms": win.get("response_time_max_ms"),
        "tps": tps,
    }


def _baseline_p95(stats: Sequence[dict[str, Any] | None]) -> float | None:
    vals = [float(s["p95_ms"]) for s in stats if s and isinstance(s.get("p95_ms"), (int, float))]
    return round(mean(vals), 3) if vals else None


def target_verdict(
    signals: Sequence[Mapping[str, Any]],
    p95: object,
    baseline_p95: float | None,
    ratio: float,
) -> tuple[str | None, str | None]:
    """인스턴스 판정 — 게이트웨이 `was_signals`가 있으면 degraded(인용 · 재판정 없음).

    없으면 결정적 규칙: 사건창 p95 ≥ ratio × 기준선 p95 → degraded, 아니면 normal(출처
    playbook_rule).
    p95·기준선이 없으면 (None, None) — 판정 불가(결손을 normal로 단정하지 않는다).
    """
    if signals:
        return "degraded", VERDICT_SOURCE_GATEWAY
    if not isinstance(p95, (int, float)) or not baseline_p95:
        return None, None
    return ("degraded" if float(p95) >= ratio * baseline_p95 else "normal"), VERDICT_SOURCE_RULE


def _worst(verdicts: Sequence[tuple[str | None, str | None]]) -> tuple[str | None, str | None]:
    """대상 여럿의 대표 판정 — degraded 하나라도면 degraded, 전부 normal이면 normal, 아니면 판정
    불가."""
    for v, s in verdicts:
        if v == "degraded":
            return v, s
    if verdicts and all(v == "normal" for v, _ in verdicts):
        return "normal", verdicts[0][1]
    return None, None


def _pct(cur: object, base: object) -> float | None:
    if not isinstance(cur, (int, float)) or not isinstance(base, (int, float)) or not base:
        return None
    return round((float(cur) - float(base)) / float(base) * 100, 1)


def _shift(iso: str, days: int) -> str:
    return (datetime.fromisoformat(iso) - timedelta(days=days)).isoformat()


def _baseline_calls(ctx: StepContext, instance: str) -> list[tuple[str, tuple[str, dict]]]:
    return [
        (
            b.name,
            (
                APM_APP_HEALTH,
                {
                    "instance_name": instance,
                    "reference_time": b.reference_time,
                    "lookback_minutes": ctx.windows.lookback_minutes,
                },
            ),
        )
        for b in ctx.windows.baselines
    ]


def _incident_health_call(ctx: StepContext, instance: str) -> tuple[str, dict]:
    return (
        APM_APP_HEALTH,
        {
            "instance_name": instance,
            "reference_time": ctx.windows.reference_time,
            "lookback_minutes": ctx.windows.lookback_minutes,
        },
    )


def _series_summary(points: object) -> dict[str, Any] | None:
    vals = (
        [
            float(p["value"])
            for p in points or []
            if isinstance(p, dict) and isinstance(p.get("value"), (int, float))
        ]
        if isinstance(points, list)
        else []
    )
    if not vals:
        return None
    return {
        "first": vals[0],
        "last": vals[-1],
        "min": min(vals),
        "max": max(vals),
        "avg": round(mean(vals), 3),
        "points": len(vals),
    }


def _epoch_ms_iso(value: object) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000).replace(tzinfo=None).isoformat()  # type: ignore[arg-type]
    except (TypeError, ValueError, OSError, OverflowError):
        return None


# ── S0 · S0r 대상 확정 ──────────────────────────────────────────────────────


def _business_term(
    ctx: StepContext, row: Mapping[str, Any] | None
) -> tuple[str | None, str | None]:
    """R-A 같은 업무 조회어와 출처. 페이로드 힌트 → 인스턴스 행 업무명 → 인스턴스 설명 첫 낱말."""
    meta = ctx.payload.get("meta") if isinstance(ctx.payload, Mapping) else None
    hints = meta.get("hints") if isinstance(meta, Mapping) else None
    if isinstance(hints, Mapping):
        for key in ("business_name", "business"):
            val = str(hints.get(key) or "").strip()
            if val:
                return val, f"hints.{key}"
    if row:
        names = row.get("business_names")
        if isinstance(names, list) and names and str(names[0]).strip():
            return str(names[0]).strip(), "instance.business_names"
        desc = str(row.get("description") or "").split()
        if desc:
            return desc[0], "instance.description"
    return None, None


def _instance_rows(resp: ToolResponse) -> list[dict[str, Any]]:
    return [
        r
        for r in (resp.data or {}).get("rows") or []
        if isinstance(r, dict) and r.get("instance_name")
    ]


def resolve_was_targets(ctx: StepContext) -> StepResult:
    """S0 — 알람 인스턴스 → 호스트 → 그 호스트 인스턴스 + 같은 업무 인스턴스 → 판정 → 대상 선별.

    인자: `max_instances`(대상 상한 · 기본 3) · `max_compare`(범위 비교 app_health 상한 · 기본 8) ·
    `p95_ratio`(판정 배수 · 기본 2.0) · `pick`(degraded만 지원 — 그 밖은 비교 전원).
    정합 신뢰도 낮음/0건이면 「대상 확정 실패」 — 이후 APM 스텝은 알람 인스턴스 단독으로 강등한다.
    """
    alarm_instance = ctx.scope.alarm_instance
    if not alarm_instance:
        return _missing([], "알람 인스턴스명 없음(event.resourceName) — 대상 확정 불가")
    if ctx.apm is None:
        return _missing([], "APM 게이트웨이 미구성 — 대상 확정 불가")
    max_instances = int(ctx.args.get("max_instances", 3))
    max_compare = int(ctx.args.get("max_compare", 8))
    ratio = float(ctx.args.get("p95_ratio", DEFAULT_P95_RATIO))
    missing: list[str] = []
    reasons: list[str] = []

    q = ctx.call_one("apm", APM_INSTANCE_MAP, {"query": alarm_instance})
    q_rows = _instance_rows(q)
    alarm_row = next(
        (r for r in q_rows if r.get("instance_name") == alarm_instance),
        q_rows[0] if q_rows else None,
    )
    confidence = str(
        (alarm_row or {}).get("search_confidence")
        or (alarm_row or {}).get("match_confidence")
        or ""
    )
    resolved = alarm_row is not None and confidence.lower() not in LOW_CONFIDENCE
    if not q.ok:
        _add_missing(missing, APM_INSTANCE_MAP)
    host = ctx.scope.host
    business: str | None = None
    business_source: str | None = None
    compare: list[tuple[str, str | None]] = [(alarm_instance, None)]
    if resolved:
        host = str(alarm_row.get("hostname") or alarm_row.get("host_name") or host or "") or None  # type: ignore[union-attr]
        business, business_source = _business_term(ctx, alarm_row)
        calls: list[tuple[str, dict]] = []
        if host:
            calls.append((APM_INSTANCE_MAP, {"hostname": host}))
        if business:
            calls.append((APM_INSTANCE_MAP, {"business": business}))
        for resp in ctx.call("apm", calls):
            if not resp.ok:
                _add_missing(missing, APM_INSTANCE_MAP)
                reasons.append(f"{resp.tool}({next(iter(resp.args))}): {resp.error}")
            for r in _instance_rows(resp):
                compare.append(
                    (
                        str(r["instance_name"]),
                        str(r.get("hostname") or r.get("host_name") or "") or None,
                    )
                )
        compare[0] = (alarm_instance, host)
    else:
        why = q.error or ("0건" if alarm_row is None else f"정합 신뢰도 {confidence or '없음'}")
        reasons.append(f"대상 확정 실패({why}) — 이후 APM 스텝은 알람 인스턴스 단독")
    seen: dict[str, str | None] = {}
    for name, h in compare:
        seen.setdefault(name, h)
    names = list(seen)[: max(1, max_compare)]
    if len(seen) > len(names):
        reasons.append(f"범위 비교 상한 {max_compare} — {len(seen) - len(names)}개 인스턴스 생략")

    health_calls = [_incident_health_call(ctx, n) for n in names]
    base_calls = _baseline_calls(ctx, alarm_instance)
    resps = ctx.call("apm", health_calls + [c for _, c in base_calls])
    cur_resps, base_resps = resps[: len(names)], resps[len(names) :]
    lb = ctx.windows.lookback_minutes
    base_stats = {bn: _window_stats(r.data, lb) for (bn, _), r in zip(base_calls, base_resps)}
    base_p95 = _baseline_p95(list(base_stats.values()))
    if any(not r.ok for r in resps):
        _add_missing(missing, APM_APP_HEALTH)
        reasons.append(_reasons(resps))
    instances: list[dict[str, Any]] = []
    for name, resp in zip(names, cur_resps):
        stats = _window_stats(resp.data, lb)
        verdict, source = (
            target_verdict(_signals(resp.data), (stats or {}).get("p95_ms"), base_p95, ratio)
            if resp.ok
            else (None, None)
        )
        instances.append(
            {
                "instance": name,
                "host": seen.get(name),
                "stats": stats,
                "verdict": verdict,
                "verdict_source": source,
                "was_signals": _signals(resp.data),
                "error": resp.error,
            }
        )
    pick = str(ctx.args.get("pick", "degraded"))
    chosen = [instances[0]] + [
        i for i in instances[1:] if pick != "degraded" or i["verdict"] == "degraded"
    ]
    chosen = chosen[: max(1, max_instances)] if resolved else [instances[0]]
    targets = [
        {
            "instance": i["instance"],
            "verdict": i["verdict"],
            "verdict_source": i["verdict_source"],
            "confidence": confidence or None,
        }
        for i in chosen
    ]
    scope = RunScope(
        alarm_instance=alarm_instance,
        host=host,
        polestar_source=ctx.scope.polestar_source,
        polestar_server=host or ctx.scope.polestar_server,
        polestar_reason=ctx.scope.polestar_reason,
        alarm_name=ctx.scope.alarm_name,
        targets=targets,
    )
    if not scope.polestar_source:
        reasons.append(
            f"{POLESTAR_UNRESOLVED} — {scope.polestar_reason or '폴스타 소스 결정 불가'}"
        )
    table = {
        "columns": [
            "instance",
            "host",
            "tps",
            "p95_ms",
            "baseline_p95_ms",
            "error_rate",
            "verdict",
            "verdict_source",
        ],
        "rows": [
            [
                i["instance"],
                i["host"],
                (i["stats"] or {}).get("tps"),
                (i["stats"] or {}).get("p95_ms"),
                base_p95 if i["instance"] == alarm_instance else None,
                (i["stats"] or {}).get("error_rate"),
                i["verdict"],
                i["verdict_source"],
            ]
            for i in instances
        ],
    }
    head = instances[0]
    return StepResult(
        status="ok",
        verdict=head["verdict"],
        verdict_source=head["verdict_source"],
        table=table,
        data={
            "scope": scope.to_dict(),
            "resolution": "resolved" if resolved else "failed",
            "confidence": confidence or None,
            "business": business,
            "business_source": business_source,
            "baseline_p95_ms": base_p95,
            "baseline_stats": base_stats,
            "instances": instances,
        },
        missing=missing,
        reason=" · ".join(r for r in reasons if r) or None,
        artifacts={"scope": scope},
    )


def host_to_was(ctx: StepContext) -> StepResult:
    """S0′ — 호스트 알람의 역방향: 호스트의 WAS 인스턴스 → app_health(사건창) 판정.

    판정은 게이트웨이 `was_signals` 유무만 쓴다(기준선 조회 없음). 제니퍼 미구성이면 생략(결손
    아님).
    """
    if ctx.apm is None:
        return StepResult(status="skipped", reason="제니퍼 미구성 — 생략(결손 아님)")
    host = ctx.scope.polestar_server or ctx.scope.host
    if not host:
        return _missing([], "호스트명 없음 — 역방향 대상 확정 불가")
    max_compare = int(ctx.args.get("max_compare", 8))
    m = ctx.call_one("apm", APM_INSTANCE_MAP, {"hostname": host})
    if not m.ok:
        return _missing([APM_INSTANCE_MAP], f"{APM_INSTANCE_MAP}: {m.error}")
    names = list(dict.fromkeys(str(r["instance_name"]) for r in _instance_rows(m)))[:max_compare]
    if not names:
        # 명시 판정(W4 후속 ③) — None이면 역방향 `when`(S0r.verdict == 'degraded')이 판정 불가
        # (UNKNOWN)로 실행돼 버린다. 「WAS 없음」은 확인된 사실이라 결정적으로 거짓이 되게 한다.
        return StepResult(
            status="ok",
            verdict=NO_WAS_VERDICT,
            verdict_source=VERDICT_SOURCE_RULE,
            reason="이 호스트에 WAS 인스턴스 없음",
            data={"host": host, "instances": []},
        )
    resps = ctx.call("apm", [_incident_health_call(ctx, n) for n in names])
    instances = []
    for name, resp in zip(names, resps):
        sig = _signals(resp.data)
        verdict = None if not resp.ok else ("degraded" if sig else "normal")
        instances.append(
            {
                "instance": name,
                "stats": _window_stats(resp.data, ctx.windows.lookback_minutes),
                "verdict": verdict,
                "verdict_source": VERDICT_SOURCE_GATEWAY if resp.ok else None,
                "was_signals": sig,
                "error": resp.error,
            }
        )
    targets = [
        {
            "instance": i["instance"],
            "verdict": i["verdict"],
            "verdict_source": i["verdict_source"],
            "confidence": None,
        }
        for i in instances
        if i["verdict"] == "degraded"
    ]
    scope = RunScope(
        alarm_instance=ctx.scope.alarm_instance,
        host=host,
        polestar_source=ctx.scope.polestar_source,
        polestar_server=ctx.scope.polestar_server,
        polestar_reason=ctx.scope.polestar_reason,
        alarm_name=ctx.scope.alarm_name,
        targets=targets,
    )
    v, s = _worst([(i["verdict"], i["verdict_source"]) for i in instances])
    missing = [APM_APP_HEALTH] if any(not r.ok for r in resps) else []
    return StepResult(
        status="ok",
        verdict=v,
        verdict_source=s,
        table={
            "columns": ["instance", "tps", "p95_ms", "verdict"],
            "rows": [
                [
                    i["instance"],
                    (i["stats"] or {}).get("tps"),
                    (i["stats"] or {}).get("p95_ms"),
                    i["verdict"],
                ]
                for i in instances
            ],
        },
        data={"scope": scope.to_dict(), "host": host, "instances": instances},
        missing=missing,
        reason=_reasons(resps) or None,
        artifacts={"scope": scope},
    )


# ── S1 · S2 · S3 APM ────────────────────────────────────────────────────────


def _need_apm(ctx: StepContext, tools: Sequence[str]) -> StepResult | None:
    if ctx.apm is None:
        return _missing(tools, "APM 게이트웨이 미구성")
    if not ctx.targets:
        return _missing(tools, "대상 인스턴스 없음")
    return None


def golden_signals(ctx: StepContext) -> StepResult:
    """S1 — 대상별 p95·TPS·에러율 + 기준선(전일·전주 동시간) · 게이트웨이 판정 인용.

    호출: 사건창 `apm_app_health` + 기준 시각을 옮긴 `apm_app_health`(기준선 p95 — period_compare에
    p95가 없다) + `apm_period_compare`(사건창 vs 각 기준선 창). TPS = calls / 창 초.
    """
    blocked = _need_apm(ctx, [APM_APP_HEALTH])
    if blocked:
        return blocked
    ratio = float(ctx.args.get("p95_ratio", DEFAULT_P95_RATIO))
    lb = ctx.windows.lookback_minutes
    w = ctx.windows
    per_target: dict[str, dict[str, Any]] = {}
    rows: list[list[Any]] = []
    missing: list[str] = []
    reasons: list[str] = []
    verdicts: list[tuple[str | None, str | None]] = []
    all_ok_incident = True
    for name in ctx.targets:
        base_calls = _baseline_calls(ctx, name)
        cmp_calls = [
            (
                b.name,
                (
                    APM_PERIOD_COMPARE,
                    {
                        "instance_name": name,
                        "current_start": w.incident_start,
                        "current_end": w.incident_end,
                        "baseline_start": b.start,
                        "baseline_end": b.end,
                    },
                ),
            )
            for b in w.baselines
        ]
        resps = ctx.call(
            "apm",
            [_incident_health_call(ctx, name)]
            + [c for _, c in base_calls]
            + [c for _, c in cmp_calls],
        )
        inc = resps[0]
        base = {
            bn: _window_stats(r.data, lb)
            for (bn, _), r in zip(base_calls, resps[1 : 1 + len(base_calls)])
        }
        cmp = {
            bn: _first_row(r.data) for (bn, _), r in zip(cmp_calls, resps[1 + len(base_calls) :])
        }
        for r in resps:
            if not r.ok:
                _add_missing(missing, r.tool)
        if any(not r.ok for r in resps):
            reasons.append(f"{name}: {_reasons(resps)}")
        stats = _window_stats(inc.data, lb)
        all_ok_incident = all_ok_incident and inc.ok
        base_p95 = _baseline_p95(list(base.values()))
        verdict, source = (
            target_verdict(_signals(inc.data), (stats or {}).get("p95_ms"), base_p95, ratio)
            if inc.ok
            else (None, None)
        )
        verdicts.append((verdict, source))
        per_target[name] = {
            "incident": stats,
            "baselines": base,
            "baseline_p95_ms": base_p95,
            "period_compare": cmp,
            "was_signals": _signals(inc.data),
            "verdict": verdict,
            "verdict_source": source,
        }
        for metric in ("p95_ms", "tps", "error_rate", "calls"):
            cur = (stats or {}).get(metric)
            vals = [(base.get(b.name) or {}).get(metric) for b in w.baselines]
            rows.append([name, metric, cur, *vals, *[_pct(cur, v) for v in vals]])
        cur_avg = (
            next(((c or {}).get("current") or {}).get("avg_response_ms") for c in cmp.values() if c)
            if any(cmp.values())
            else None
        )
        avg_vals = [
            ((cmp.get(b.name) or {}).get("baseline") or {}).get("avg_response_ms")
            for b in w.baselines
        ]
        rows.append(
            [name, "avg_response_ms", cur_avg, *avg_vals, *[_pct(cur_avg, v) for v in avg_vals]]
        )
    names = [b.name for b in w.baselines]
    table = {
        "columns": ["instance", "metric", "incident", *names, *[f"change_pct_{n}" for n in names]],
        "rows": rows,
    }
    v, s = _worst(verdicts)
    if not all_ok_incident and all(v is None for v, _ in verdicts):
        return StepResult(
            status="missing",
            table=table,
            data={"targets": per_target},
            missing=missing,
            reason=" · ".join(reasons) or "사건창 조회 실패",
        )
    return StepResult(
        status="ok",
        verdict=v,
        verdict_source=s,
        table=table,
        data={"targets": per_target, "p95_ratio": ratio},
        missing=missing,
        reason=" · ".join(reasons) or None,
    )


def runtime_and_pool(ctx: StepContext) -> StepResult:
    """S2 — 힙·GC%(5분 추세) · 프로세스 CPU·액티브 서비스 추세 · DB 커넥션 풀(현재값 — 배지).

    인자: `lookback_minutes`(추세 창 재정의 — 예 힙 주기 판정) · `process_trend`(기본 true —
    false면 프로세스 CPU·액티브 추세 호출 생략).
    """
    blocked = _need_apm(ctx, [APM_RUNTIME_HEALTH, APM_RESOURCE_POOL])
    if blocked:
        return blocked
    lb = int(ctx.args.get("lookback_minutes", ctx.windows.lookback_minutes))
    with_proc = bool(ctx.args.get("process_trend", True))
    per_target: dict[str, dict[str, Any]] = {}
    rows: list[list[Any]] = []
    badges: list[str] = []
    missing: list[str] = []
    reasons: list[str] = []
    signals: list[dict[str, Any]] = []
    any_ok = False
    for name in ctx.targets:
        base = {
            "instance_name": name,
            "reference_time": ctx.windows.reference_time,
            "lookback_minutes": lb,
        }
        calls = [(APM_RUNTIME_HEALTH, dict(base))]
        if with_proc:
            calls.append((APM_RUNTIME_HEALTH, {**base, "metrics": ["proc_cpu", "active_service"]}))
        calls.append((APM_RESOURCE_POOL, {"instance_name": name}))
        resps = ctx.call("apm", calls)
        trend: dict[str, Any] = {}
        for r in resps[:-1]:
            row = _first_row(r.data)
            if isinstance((row or {}).get("trend"), dict):
                trend.update(row["trend"])  # type: ignore[index]
        pool_resp = resps[-1]
        pool = _first_row(pool_resp.data)
        badge = (
            realtime_badge(
                (pool_resp.data or {}).get("queried_at"), ctx.windows.alarm_time, ctx.now
            )
            if pool_resp.ok
            else None
        )
        if badge and badge not in badges:
            badges.append(badge)
        for r in resps:
            if not r.ok:
                _add_missing(missing, r.tool)
            else:
                any_ok = True
            signals.extend(_signals(r.data))
        if any(not r.ok for r in resps):
            reasons.append(f"{name}: {_reasons(resps)}")
        summaries = {k: _series_summary(v) for k, v in trend.items()}
        per_target[name] = {
            "trend": trend,
            "trend_summary": summaries,
            "pool": pool,
            "pool_badge": badge,
            "was_signals": [s for r in resps for s in _signals(r.data)],
        }
        for key, summ in summaries.items():
            if summ:
                rows.append([name, key, summ["min"], summ["max"], summ["last"], "추세"])
        for key in (
            "db_pool_active",
            "db_pool_configured_avg",
            "db_pool_idle_avg",
            "db_pool_usage_ratio",
            "active_services",
            "thread_current",
        ):
            if pool and key in pool:
                rows.append([name, key, None, None, pool[key], badge])
    if not any_ok:
        return StepResult(status="missing", missing=missing, reason=" · ".join(reasons))
    verdict = "degraded" if signals else "normal"
    return StepResult(
        status="ok",
        verdict=verdict,
        verdict_source=VERDICT_SOURCE_GATEWAY,
        table={"columns": ["instance", "metric", "min", "max", "last", "note"], "rows": rows},
        data={"targets": per_target, "lookback_minutes": lb, "was_signals": signals},
        badges=badges,
        missing=missing,
        reason=" · ".join(reasons) or None,
    )


_TX_COLUMNS = (
    "application",
    "response_time_ms",
    "sql_ms",
    "fetch_ms",
    "external_ms",
    "cpu_ms",
    "network_ms",
    "txid",
)


def slow_tx_breakdown(ctx: StepContext) -> StepResult:
    """S3 — 느린 트랜잭션 Top N(기본 5) 시간 분해 + 1위 건 프로파일(예산 1 — R-D 원자료).

    대상은 알람 인스턴스(첫 대상). 0건이면 결손. 프로파일 원본(`profile_excerpt`·`sqls`)은 해석하지
    않고 `data.profile`에 그대로 싣는다 — 커넥션 획득/SQL 실행 분리(R-D)는 W3(현 결론 n/a).
    """
    blocked = _need_apm(ctx, [APM_SLOW_TX])
    if blocked:
        return blocked
    name = ctx.targets[0]
    n = int(ctx.args.get("n", 5))
    profile_top = min(int(ctx.args.get("profile_top", 1)), 1)  # 프로파일 예산 1(§8)
    resp = ctx.call_one(
        "apm",
        APM_SLOW_TX,
        {
            "instance_name": name,
            "reference_time": ctx.windows.reference_time,
            "lookback_minutes": ctx.windows.lookback_minutes,
            "n": n,
        },
    )
    if not resp.ok:
        return _missing([APM_SLOW_TX], f"{APM_SLOW_TX}: {resp.error}")
    rows = [r for r in (resp.data or {}).get("rows") or [] if isinstance(r, dict)]
    if not rows:
        return StepResult(
            status="missing",
            missing=[APM_SLOW_TX],
            reason="사건창 느린 거래 0건",
            data={"instance": name, "summary": (resp.data or {}).get("summary")},
        )
    profile: dict[str, Any] | None = None
    missing: list[str] = []
    reason = None
    ref = rows[0].get("profile_ref")
    if profile_top and isinstance(ref, dict):
        p = ctx.call_one(
            "apm",
            APM_TX_PROFILE,
            {
                "instance_name": name,
                "source_id": ref.get("source_id"),
                "domain_id": ref.get("domain_id"),
                "txid": ref.get("txid"),
                "time_ms": ref.get("time_ms"),
            },
        )
        if p.ok:
            prow = _first_row(p.data) or {}
            profile = {
                k: prow.get(k)
                for k in ("transaction", "profile_excerpt", "profile_truncated", "sqls")
            }
            profile["limits"] = (p.data or {}).get("limits") or []
        else:
            missing.append(APM_TX_PROFILE)
            reason = f"{APM_TX_PROFILE}: {p.error}"
    signals = _signals(resp.data)
    return StepResult(
        status="ok",
        verdict="degraded" if signals else "normal",
        verdict_source=VERDICT_SOURCE_GATEWAY,
        table={
            "columns": list(_TX_COLUMNS),
            "rows": [[r.get(c) for c in _TX_COLUMNS] for r in rows],
        },
        data={
            "instance": name,
            "rows": [
                {c: r.get(c) for c in (*_TX_COLUMNS, "profile_ref", "business_names")} for r in rows
            ],
            "summary": (resp.data or {}).get("summary"),
            "profile": profile,
            "was_signals": signals,
        },
        missing=missing,
        reason=reason,
    )


# ── S4 · S5 · S6 · S7 호스트·교차 소스 ───────────────────────────────────────


def usage_trend(ctx: StepContext) -> StepResult:
    """S4 — 폴스타 시간대별 지표(`build_calls`의 지표 호출 · `_series` 해석 · `correlate` 이상
    판정 재사용).

    인자: `kinds`(기본 cpu·memory·filesystem·disk_io) · `granularity`(기본 h). 결손 단위는 지표
    종류다.
    """
    thresholds = dict(METRIC_KINDS)
    kinds = [k for k in (ctx.args.get("kinds") or list(thresholds)) if k in thresholds]
    blocked = _polestar_reason(ctx)
    if blocked:
        return _missing(kinds, blocked)
    scope = _evidence_scope(ctx, str(ctx.args.get("granularity", "h")))
    calls = [c for c in build_calls(scope) if c[0] == TOOL_METRIC and c[1].get("kind") in kinds]
    notes: list[str] = []
    series: list[MetricSeries] = []
    missing: list[str] = []
    for (_, args), resp in zip(calls, ctx.call("polestar", calls)):
        kind = str(args["kind"])
        if resp.data is None:
            missing.append(kind)
            notes.append(f"{kind} 축 결손: {resp.error}")
            continue
        series.append(_series(kind, thresholds[kind], resp.data, scope, notes))
    if not series:
        return StepResult(status="missing", missing=missing, reason=" · ".join(notes))
    result = correlate(ctx.windows.alarm_time, [], series, notes, alarm_axis=False)
    findings = result.metric_findings
    anomalous = [k for k, f in findings.items() if f.get("is_anomalous")]
    rows = [
        [
            s.name,
            findings[s.name].get("peak_value"),
            findings[s.name].get("baseline_mean"),
            findings[s.name].get("z_score"),
            findings[s.name].get("is_anomalous"),
            findings[s.name].get("kind"),
        ]
        for s in series
    ]
    return StepResult(
        status="ok",
        verdict="degraded" if anomalous else "normal",
        verdict_source=VERDICT_SOURCE_RULE,
        table={
            "columns": ["kind", "peak", "baseline_mean", "z_score", "is_anomalous", "anomaly_kind"],
            "rows": rows,
        },
        data={
            "findings": findings,
            "anomalous": anomalous,
            "notes": list(result.notes),
            "series": {
                s.name: {
                    "points": [list(p) for p in s.points],
                    "baseline": list(s.baseline),
                    "granularity_minutes": s.granularity_minutes,
                }
                for s in series
            },
            "source": scope.source,
            "server_name": scope.server_name,
        },
        missing=missing,
        reason=" · ".join(n for n in notes if "결손" in n) or None,
        artifacts={"series": series},
    )


def related_alarms(ctx: StepContext) -> StepResult:
    """S5 — 알람 전후 폴스타 알람(`build_calls` 알람 호출 · `_alarms`) + 제니퍼 이벤트 →
    `correlate()` 타임라인.

    「상관 ≠ 인과」 문구를 결과에 고정한다.
    """
    notes: list[str] = []
    missing: list[str] = []
    reasons: list[str] = []
    alarms = []
    polestar_ok = False
    blocked = _polestar_reason(ctx)
    if blocked:
        missing.append(TOOL_ALARMS)
        reasons.append(f"폴스타 축: {blocked}")
    else:
        call = build_calls(_evidence_scope(ctx))[0]
        resp = ctx.call_one("polestar", *call)
        if resp.ok:
            polestar_ok = True
            alarms = _alarms(resp.data or {}, notes)
        else:
            missing.append(TOOL_ALARMS)
            reasons.append(f"{TOOL_ALARMS}: {resp.error}")
    events: list[dict[str, Any]] = []
    points: list[EpisodePoint] = []
    apm_ok = False
    instance = ctx.targets[0] if ctx.targets else ctx.scope.alarm_instance
    if ctx.apm is None or not instance:
        missing.append(APM_EVENTS)
        reasons.append(
            "APM 축: " + ("APM 게이트웨이 미구성" if ctx.apm is None else "대상 인스턴스 없음")
        )
    else:
        ev = ctx.call_one(
            "apm",
            APM_EVENTS,
            {
                "instance_name": instance,
                "reference_time": ctx.windows.reference_time,
                "lookback_minutes": ctx.windows.lookback_minutes,
            },
        )
        if ev.ok:
            apm_ok = True
            for row in (ev.data or {}).get("rows") or []:
                if not isinstance(row, dict):
                    continue
                when = _epoch_ms_iso(row.get("time_ms"))
                item = {
                    k: row.get(k)
                    for k in (
                        "time_ms",
                        "level",
                        "event_type",
                        "instance_name",
                        "application",
                        "message",
                    )
                }
                item["time"] = when
                events.append(item)
                if when:
                    points.append(
                        EpisodePoint(
                            time=when,
                            kind="apm_event",
                            detail=f"{row.get('event_type') or '?'} "
                            f"{row.get('instance_name') or ''}".strip(),
                        )
                    )
        else:
            missing.append(APM_EVENTS)
            reasons.append(f"{APM_EVENTS}: {ev.error}")
    if not polestar_ok and not apm_ok:
        return StepResult(status="missing", missing=missing, reason=" · ".join(reasons))
    result = correlate(
        ctx.windows.alarm_time,
        alarms,
        [],
        notes,
        episode_points=points or None,
        alarm_axis=polestar_ok,
    )
    timeline = [
        {"t_offset_min": t.t_offset_min, "kind": t.kind, "detail": t.detail}
        for t in result.timeline
    ]
    return StepResult(
        status="ok",
        verdict=None,
        verdict_source=None,
        table={
            "columns": ["t_offset_min", "kind", "detail"],
            "rows": [[t["t_offset_min"], t["kind"], t["detail"]] for t in timeline],
        },
        data={
            "timeline": timeline,
            "alarm_summary": result.alarm_summary,
            "apm_events": events,
            "notes": list(result.notes),
            "caveat": CORRELATION_NOT_CAUSATION,
        },
        badges=[CORRELATION_NOT_CAUSATION],
        missing=missing,
        reason=" · ".join(reasons) or None,
        artifacts={"alarms": alarms, "episode_points": points},
    )


def process_top(ctx: StepContext) -> StepResult:
    """S6 — 폴스타 프로세스 상위(실시간 단면 — `captured_at` 배지 · 판정 없음)."""
    blocked = _polestar_reason(ctx)
    if blocked:
        return _missing([POLESTAR_PROCESS], blocked)
    resp = ctx.call_one(
        "polestar",
        POLESTAR_PROCESS,
        {
            "hostname": ctx.scope.polestar_server,
            "top_n": int(ctx.args.get("top_n", 10)),
            "sort": str(ctx.args.get("sort", "cpu")),
        },
    )
    if not resp.ok:
        # 실시간 조회를 시도한 스텝은 실패해도 배지를 단다(조회 시점 = 실행 시각 · 픽스처
        # partial_missing)
        res = _missing([POLESTAR_PROCESS], f"{POLESTAR_PROCESS}: {resp.error}")
        res.badges.append(realtime_badge(None, ctx.windows.alarm_time, ctx.now))
        return res
    data = resp.data or {}
    cols = ("name", "pid", "user", "pcpu", "pmem", "rss", "args")
    rows = [r for r in data.get("rows") or [] if isinstance(r, dict)]
    badge = realtime_badge(
        data.get("captured_at") or data.get("queried_at"), ctx.windows.alarm_time, ctx.now
    )
    return StepResult(
        status="ok",
        table={"columns": list(cols), "rows": [[r.get(c) for c in cols] for r in rows]},
        data={
            "rows": rows,
            "total_processes": data.get("total_processes"),
            "captured_at": data.get("captured_at"),
        },
        badges=[badge],
    )


def recent_changes(ctx: StepContext) -> StepResult:
    """S7 — 변경 대조: 제니퍼 소스 변경·변경 영향(reference_time만) · 폴스타 변경 이력(PG만) ·
    ITSM(미연동).

    인자: `lookback_hours`(기본 24). 후보는 알람 시각까지의 시간 근접순이며 「상관 후보」로만
    표기한다.
    """
    hours = int(ctx.args.get("lookback_hours", 24))
    alarm = datetime.fromisoformat(ctx.windows.alarm_time)
    axes: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    candidates: list[dict[str, Any]] = []
    impact: list[dict[str, Any]] = []
    instance = ctx.targets[0] if ctx.targets else ctx.scope.alarm_instance

    def _candidate(source: str, at: datetime | None, desc: str) -> None:
        if at is None:
            return
        before = int((alarm - at).total_seconds() // 60)
        if 0 <= before <= hours * 60:
            candidates.append(
                {
                    "source": source,
                    "at": at.isoformat(),
                    "minutes_before_alarm": before,
                    "description": desc,
                }
            )

    if ctx.apm is None or not instance:
        axes["apm"] = {
            "status": "missing",
            "reason": "APM 게이트웨이 미구성" if ctx.apm is None else "대상 인스턴스 없음",
        }
        missing += [APM_SOURCE_CHANGES, APM_CHANGE_IMPACT]
    else:
        args = {"instance_name": instance, "reference_time": ctx.windows.reference_time}
        sc, ci = ctx.call(
            "apm", [(APM_SOURCE_CHANGES, dict(args)), (APM_CHANGE_IMPACT, dict(args))]
        )
        for r in (sc, ci):
            if not r.ok:
                missing.append(r.tool)
        axes["apm"] = {
            "status": "ok" if sc.ok or ci.ok else "missing",
            "reason": _reasons([sc, ci]) or None,
        }
        for row in (sc.data or {}).get("rows") or []:
            if isinstance(row, dict):
                _candidate(
                    APM_SOURCE_CHANGES,
                    naive_time(row.get("change_detected_at")),
                    f"{row.get('instance_name') or instance} 소스 변경 감지",
                )
        impact = [r for r in (ci.data or {}).get("rows") or [] if isinstance(r, dict)]
    blocked = _polestar_reason(ctx)
    if blocked:
        axes["polestar"] = {"status": "missing", "reason": blocked}
        missing.append(TOOL_CHANGES)
    else:
        scope = _evidence_scope(ctx)
        pc = ctx.call_one(
            "polestar",
            TOOL_CHANGES,
            {
                "source": scope.source,
                "server_name": scope.server_name,
                "reference_time": ctx.windows.reference_time,
                "lookback_minutes": hours * 60,
            },
        )
        if not pc.ok:
            axes["polestar"] = {"status": "missing", "reason": pc.error}
            missing.append(TOOL_CHANGES)
        else:
            notes: list[str] = []
            changes = _changes(pc.data or {}, scope, notes)
            if changes is None:
                axes["polestar"] = {
                    "status": "skipped",
                    "reason": " · ".join(notes) or "unsupported",
                }
            else:
                axes["polestar"] = {"status": "ok", "reason": " · ".join(notes) or None}
                for c in changes:
                    _candidate(TOOL_CHANGES, naive_time(c.time), c.description)
    itsm = _adapter_fetch(ctx, ITSM, "S7")
    axes["itsm"] = {"status": itsm.get("status", "skipped"), "reason": itsm.get("reason")}
    candidates.sort(key=lambda c: (c["minutes_before_alarm"], c["source"]))
    queried = [a for k, a in axes.items() if k != "itsm"]
    if not any(a["status"] in ("ok", "skipped") for a in queried):
        return StepResult(
            status="missing",
            missing=missing,
            data={"axes": axes},
            reason=" · ".join(f"{k}: {a['reason']}" for k, a in axes.items() if a["reason"]),
        )
    return StepResult(
        status="ok",
        table={
            "columns": ["source", "at", "minutes_before_alarm", "description"],
            "rows": [
                [c["source"], c["at"], c["minutes_before_alarm"], c["description"]]
                for c in candidates
            ],
        },
        data={
            "candidates": candidates,
            "change_impact": impact,
            "axes": axes,
            "lookback_hours": hours,
            "caveat": CHANGE_CANDIDATE_NOTE,
        },
        badges=[CHANGE_CANDIDATE_NOTE],
        missing=missing,
        reason=" · ".join(
            f"{k}: {a['reason']}" for k, a in axes.items() if a["status"] != "ok" and a["reason"]
        )
        or None,
    )


def _adapter_fetch(ctx: StepContext, name: str, step_id: str) -> dict[str, Any]:
    """외부 소스 어댑터 호출 — 미연동이면 `skipped: not_connected`(§6 · 침묵 생략 금지)."""
    return fetch_or_not_connected(
        ctx.adapters.get(name), step_id, ctx.scope.to_dict(), ctx.windows.to_dict()
    )


def dpm_db_health(ctx: StepContext) -> StepResult:
    """S8 — DPM 어댑터(G-6 · §6). 기본 레지스트리는 미연동 스텁이라 skipped(not_connected)."""
    res = _adapter_fetch(ctx, DPM, "S8")
    return StepResult(
        status=res.get("status", "skipped"),
        reason=res.get("reason"),
        data=dict(res.get("data") or {}),
        missing=list(res.get("missing") or []),
    )


# ── §4.1 보조 액션 (호출 + 표 정리만) ────────────────────────────────────────


def _simple_table(rows: Sequence[Mapping[str, Any]], cols: Sequence[str]) -> dict[str, Any]:
    return {"columns": list(cols), "rows": [[r.get(c) for c in cols] for r in rows]}


def events_errors(ctx: StepContext) -> StepResult:
    """was.error — 사건창 오류 레코드의 유형 상위(`apm_events(record=error)`)."""
    blocked = _need_apm(ctx, [APM_EVENTS])
    if blocked:
        return blocked
    resp = ctx.call_one(
        "apm",
        APM_EVENTS,
        {
            "instance_name": ctx.targets[0],
            "reference_time": ctx.windows.reference_time,
            "lookback_minutes": ctx.windows.lookback_minutes,
            "record": "error",
            "n": int(ctx.args.get("n", 10)),
        },
    )
    if not resp.ok:
        return _missing([APM_EVENTS], f"{APM_EVENTS}: {resp.error}")
    by_type = [r for r in (resp.data or {}).get("errors_by_type") or [] if isinstance(r, dict)]
    cols = list(by_type[0]) if by_type else ["error_type", "count"]
    return StepResult(
        status="ok",
        table=_simple_table(by_type, cols),
        data={"errors_by_type": by_type, "rows": (resp.data or {}).get("rows") or []},
    )


def active_services(ctx: StepContext) -> StepResult:
    """was.active — 액티브 서비스(실시간 단면 · 배지)."""
    blocked = _need_apm(ctx, [APM_ACTIVE_SERVICES])
    if blocked:
        return blocked
    resp = ctx.call_one(
        "apm",
        APM_ACTIVE_SERVICES,
        {"instance_name": ctx.targets[0], "n": int(ctx.args.get("n", 10))},
    )
    if not resp.ok:
        return _missing([APM_ACTIVE_SERVICES], f"{APM_ACTIVE_SERVICES}: {resp.error}")
    data = resp.data or {}
    rows = [r for r in data.get("rows") or [] if isinstance(r, dict)]
    return StepResult(
        status="ok",
        table=_simple_table(
            rows, ("application", "elapsed_ms", "cpu_ms", "running_mode", "running_text")
        ),
        data={"rows": rows, "summary": data.get("summary")},
        badges=[realtime_badge(data.get("queried_at"), ctx.windows.alarm_time, ctx.now)],
    )


def _polestar_simple(
    ctx: StepContext, tool: str, extra: dict[str, Any], cols: Sequence[str]
) -> StepResult:
    blocked = _polestar_reason(ctx)
    if blocked:
        return _missing([tool], blocked)
    resp = ctx.call_one(
        "polestar",
        tool,
        {"source": ctx.scope.polestar_source, "server_name": ctx.scope.polestar_server, **extra},
    )
    if not resp.ok:
        return _missing([tool], f"{tool}: {resp.error}")
    rows = [r for r in (resp.data or {}).get("rows") or [] if isinstance(r, dict)]
    return StepResult(
        status="ok", table=_simple_table(rows, list(rows[0]) if rows else cols), data={"rows": rows}
    )


def resource_status(ctx: StepContext) -> StepResult:
    """host.process·host.network — 서버·서브리소스 가용 상태."""
    return _polestar_simple(ctx, POLESTAR_RESOURCE_STATUS, {}, ("name", "status"))


def topology(ctx: StepContext) -> StepResult:
    """host.network — 가용성 의존 토폴로지(인자 `max_hops` 기본 3)."""
    return _polestar_simple(
        ctx, POLESTAR_TOPOLOGY, {"max_hops": int(ctx.args.get("max_hops", 3))}, ("name", "status")
    )


def alarm_history(ctx: StepContext) -> StepResult:
    """재발 확인 — 같은 (서버, 알람명) 최근 이력(인자 `lookback_days` 기본 20 · §4.0)."""
    if not ctx.scope.alarm_name:
        return _missing([POLESTAR_ALARM_HISTORY], "알람명 없음 — 재발 조회 불가")
    return _polestar_simple(
        ctx,
        POLESTAR_ALARM_HISTORY,
        {"alarm_name": ctx.scope.alarm_name, "hours": int(ctx.args.get("lookback_days", 20)) * 24},
        ("alarm_time", "severity", "alarm_name"),
    )


#: 액션 ID → 스텝 함수. `synthesize`(S9)는 W3 담당이라 없다 — 실행기가 자리만 만든다.
STEP_LIBRARY: dict[str, StepFn] = {
    "scope.resolve_was_targets": resolve_was_targets,
    "scope.host_to_was": host_to_was,
    "apm.golden_signals": golden_signals,
    "apm.runtime_and_pool": runtime_and_pool,
    "apm.slow_tx_breakdown": slow_tx_breakdown,
    "host.usage_trend": usage_trend,
    "xsource.related_alarms": related_alarms,
    "host.process_top": process_top,
    "change.recent": recent_changes,
    "dpm.db_health": dpm_db_health,
    "apm.events_errors": events_errors,
    "apm.active_services": active_services,
    "host.resource_status": resource_status,
    "host.topology": topology,
    "host.alarm_history": alarm_history,
}

#: 대상 확정 액션 — 실행기가 결과의 `artifacts["scope"]`로 범위를 갱신한다.
SCOPE_ACTIONS = frozenset({"scope.resolve_was_targets", "scope.host_to_was"})


__all__ = [
    "ACTION_TITLES",
    "BaselineWindow",
    "CORRELATION_NOT_CAUSATION",
    "CHANGE_CANDIDATE_NOTE",
    "DEFAULT_P95_RATIO",
    "NOT_CONNECTED",
    "NO_WAS_VERDICT",
    "POLESTAR_UNRESOLVED",
    "RunScope",
    "RunWindows",
    "SCOPE_ACTIONS",
    "STEP_LIBRARY",
    "StepContext",
    "StepFn",
    "StepResult",
    "ToolResponse",
    "VERDICT_SOURCE_GATEWAY",
    "VERDICT_SOURCE_RULE",
    "naive_time",
    "parse_response",
    "realtime_badge",
    "target_verdict",
]
