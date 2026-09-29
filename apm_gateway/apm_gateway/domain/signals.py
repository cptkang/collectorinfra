"""WAS 시그니처 결정적 판정 — 단일 정의 (plans/87 §5.4(b) · D-274 ⑤ · D-035).

입력은 어댑터가 만든 **벤더 중립 레코드**(`heap_used_mb` 등)이고 출력은 `was_signals` 항목
(SPEC-apm-gateway §4)이다. `sre_agent`는 이 결과를 severity 신호로 승격만 하고, `noise_gate`는
`app_impact` 입력으로만 쓴다 — 규칙을 다른 곳에 다시 구현하지 않는다. LLM은 임계를 판단하지 않는다.

임계는 **잠정**(J0-L-b 실데이터·목업 시나리오로 보정)이며 `config/was_signatures.yaml`로 덮어쓴다.
순수 함수 · stdlib만.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import Any

CRITICAL = "CRITICAL"
WARNING = "WARNING"
STRONG = "strong"
MEDIUM = "medium"

LABELS: dict[str, str] = {
    "was_service_queuing": "WAS 서비스 큐잉(유입 대기·PLC 거절)",
    "was_thread_pool_exhaustion": "WAS 스레드 정체(동일 실행 모드 장기 정체)",
    "was_db_pool_exhaustion": "DB 커넥션 풀 고갈",
    "was_gc_stall": "GC 지연(GC 시간 비중 과다)",
    "was_heap_pressure": "힙 메모리 압박",
    "was_slow_sql": "SQL 지연(SQL·Fetch 비중 과다)",
    "was_external_call_delay": "외부 호출 지연",
    "was_error_burst": "오류 급증",
}
KINDS: tuple[str, ...] = tuple(LABELS)

_LEVEL_RANK = {CRITICAL: 2, WARNING: 1}
_CATEGORY_RANK = {STRONG: 2, MEDIUM: 1}


@dataclass(frozen=True)
class WasThresholds:
    """잠정 임계(§5.4(b) 표). yaml 키는 필드 이름과 같다."""

    heap_usage_ratio: float = 0.9
    heap_sustained_samples: int = 3
    heap_leak_step_ratio: float = 0.05
    gc_time_usage_pct: float = 10.0
    db_pool_usage_ratio: float = 0.95
    stuck_elapsed_ms: int = 600_000
    stuck_same_mode_ratio: float = 0.7
    stuck_min_count: int = 3
    slow_sql_share: float = 0.6
    external_share: float = 0.6
    share_min_transactions: int = 3
    error_rate: float = 0.2
    error_min_calls: int = 20

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> WasThresholds:
        """yaml dict에서 알려진 키만 읽는다(미지 키는 무시 — 로더가 경고)."""
        if not data:
            return cls()
        defaults = cls()
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {
            key: type(getattr(defaults, key))(value) for key, value in data.items() if key in known
        }
        return cls(**kwargs)


def make_signal(
    kind: str,
    level: str,
    category: str,
    evidence: str,
    *,
    instance_id: int | None,
    source_tool: str,
) -> dict[str, Any]:
    if kind not in LABELS:
        raise ValueError(f"미지 시그니처 kind: {kind}")
    return {
        "kind": kind,
        "level": level,
        "category": category,
        "label": LABELS[kind],
        "evidence": str(evidence)[:200],
        "instance_id": instance_id,
        "source_tool": source_tool,
    }


def dedupe(signals: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 `(kind, instance_id)`는 가장 강한 것 1건만 남긴다(입력 순서 보존)."""
    best: dict[tuple[str, Any], dict[str, Any]] = {}
    order: list[tuple[str, Any]] = []
    for sig in signals:
        key = (sig["kind"], sig.get("instance_id"))
        rank = (_LEVEL_RANK.get(sig["level"], 0), _CATEGORY_RANK.get(sig["category"], 0))
        if key not in best:
            order.append(key)
            best[key] = sig
            continue
        cur = best[key]
        if rank > (_LEVEL_RANK.get(cur["level"], 0), _CATEGORY_RANK.get(cur["category"], 0)):
            best[key] = sig
    return [best[k] for k in order]


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or not den:
        return None
    return num / den


# ── 런타임(힙·GC) ─────────────────────────────────────────────


def heap_ratio_series(trend: dict[str, Any] | None) -> list[tuple[int | None, float]]:
    """추세 `heap_used_mb`·`heap_committed_mb`를 시각으로 맞춰 사용률 시계열을 만든다."""
    if not trend:
        return []
    committed = {p["time_ms"]: p["value"] for p in trend.get("heap_committed_mb") or []}
    out: list[tuple[int | None, float]] = []
    for point in trend.get("heap_used_mb") or []:
        ratio = _ratio(point.get("value"), committed.get(point.get("time_ms")))
        if ratio is not None:
            out.append((point.get("time_ms"), ratio))
    return out


def _leak_staircase(trend: dict[str, Any] | None, th: WasThresholds) -> str | None:
    """추세를 3구간으로 나눠 구간별 하한(힙 사용량 최소값)이 계단식으로 오르면 근거 문자열."""
    used = [
        p.get("value")
        for p in (trend or {}).get("heap_used_mb") or []
        if p.get("value") is not None
    ]
    committed = [
        p.get("value") for p in (trend or {}).get("heap_committed_mb") or [] if p.get("value")
    ]
    if len(used) < 6 or not committed:
        return None
    size = len(used) // 3
    lows = [min(used[i * size : (i + 1) * size]) for i in range(3)]
    step = th.heap_leak_step_ratio * max(committed)
    if lows[1] - lows[0] >= step and lows[2] - lows[1] >= step:
        return f"힙 하한 계단 상승 {lows[0]:.0f}→{lows[1]:.0f}→{lows[2]:.0f}MB(누수 의심)"
    return None


def judge_runtime(
    current: dict[str, Any] | None,
    trend: dict[str, Any] | None,
    th: WasThresholds,
    *,
    instance_id: int | None,
    source_tool: str = "apm_runtime_health",
) -> list[dict[str, Any]]:
    """힙 압박·GC 지연 판정."""
    out: list[dict[str, Any]] = []
    series = heap_ratio_series(trend)
    run = 0
    for _t, ratio in series:
        run = run + 1 if ratio >= th.heap_usage_ratio else 0
        if run >= th.heap_sustained_samples:
            out.append(
                make_signal(
                    "was_heap_pressure",
                    WARNING,
                    MEDIUM,
                    f"heap 사용률 ≥ {th.heap_usage_ratio:g} 연속 {run}샘플(최근 {ratio:.2f})",
                    instance_id=instance_id,
                    source_tool=source_tool,
                )
            )
            break
    leak = _leak_staircase(trend, th)
    if leak:
        out.append(
            make_signal(
                "was_heap_pressure",
                WARNING,
                MEDIUM,
                leak,
                instance_id=instance_id,
                source_tool=source_tool,
            )
        )
    if not series and current:
        now_ratio = _ratio(current.get("heap_used_mb"), current.get("heap_committed_mb"))
        if now_ratio is not None and now_ratio >= th.heap_usage_ratio:
            out.append(
                make_signal(
                    "was_heap_pressure",
                    WARNING,
                    MEDIUM,
                    f"heap_used/committed={now_ratio:.2f} "
                    f"({current.get('heap_used_mb'):.1f}MB/"
                    f"{current.get('heap_committed_mb'):.1f}MB · 단일 스냅샷)",
                    instance_id=instance_id,
                    source_tool=source_tool,
                )
            )
    gc_values = [
        p.get("value")
        for p in (trend or {}).get("gc_time_usage_pct") or []
        if p.get("value") is not None
    ]
    gc_now = (current or {}).get("gc_time_usage_pct")
    if gc_now is not None:
        gc_values.append(gc_now)
    gc_peak = max(gc_values) if gc_values else None
    if gc_peak is not None and gc_peak >= th.gc_time_usage_pct:
        out.append(
            make_signal(
                "was_gc_stall",
                WARNING,
                MEDIUM,
                f"gc_time_usage={gc_peak:.1f}% ≥ {th.gc_time_usage_pct:g}%",
                instance_id=instance_id,
                source_tool=source_tool,
            )
        )
    return dedupe(out)


# ── 골든 시그널(큐잉·오류 급증) ───────────────────────────────


def judge_app(
    current: dict[str, Any] | None,
    window: dict[str, Any] | None,
    th: WasThresholds,
    *,
    instance_id: int | None,
    source_tool: str = "apm_app_health",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    reject = (current or {}).get("reject_rate")
    if reject is not None and reject > 0:
        out.append(
            make_signal(
                "was_service_queuing",
                CRITICAL,
                STRONG,
                f"reject_rate={reject:g}(PLC 거절 발생)",
                instance_id=instance_id,
                source_tool=source_tool,
            )
        )
    if window:
        calls = int(window.get("calls") or 0)
        rate = window.get("error_rate")
        if rate is not None and calls >= th.error_min_calls and rate >= th.error_rate:
            out.append(
                make_signal(
                    "was_error_burst",
                    CRITICAL,
                    STRONG,
                    f"error_rate={rate:.2f}({window.get('errors')}/{calls}) ≥ {th.error_rate:g}",
                    instance_id=instance_id,
                    source_tool=source_tool,
                )
            )
    return dedupe(out)


# ── 자원 풀(DB 풀) ────────────────────────────────────────────


def judge_pool(
    current: dict[str, Any] | None,
    th: WasThresholds,
    *,
    instance_id: int | None,
    source_tool: str = "apm_resource_pool",
) -> list[dict[str, Any]]:
    cur = current or {}
    ratio = _ratio(cur.get("db_pool_active"), cur.get("db_pool_configured_avg"))
    if ratio is not None and ratio >= th.db_pool_usage_ratio:
        return [
            make_signal(
                "was_db_pool_exhaustion",
                CRITICAL,
                STRONG,
                f"db_pool active/configured={ratio:.2f} "
                f"({cur.get('db_pool_active'):g}/{cur.get('db_pool_configured_avg'):g})",
                instance_id=instance_id,
                source_tool=source_tool,
            )
        ]
    return []


# ── 액티브 서비스(스레드 정체) ───────────────────────────────


def judge_active_services(
    services: list[dict[str, Any]],
    th: WasThresholds,
    *,
    instance_id: int | None,
    source_tool: str = "apm_active_services",
) -> list[dict[str, Any]]:
    """장기 실행(≥ stuck_elapsed_ms) 건 가운데 같은 실행 모드 비율이 높으면 스레드 정체."""
    stuck = [s for s in services if (s.get("elapsed_ms") or 0) >= th.stuck_elapsed_ms]
    if len(stuck) < th.stuck_min_count:
        return []
    counts: dict[str, int] = {}
    for s in stuck:
        mode = s.get("running_mode") or "(없음)"
        counts[mode] = counts.get(mode, 0) + 1
    mode, count = max(counts.items(), key=lambda kv: kv[1])
    share = count / len(stuck)
    if share < th.stuck_same_mode_ratio:
        return []
    return [
        make_signal(
            "was_thread_pool_exhaustion",
            CRITICAL,
            STRONG,
            f"≥{th.stuck_elapsed_ms // 1000}s 실행 {len(stuck)}건 중 "
            f"running_mode={mode} {count}건({share:.0%})",
            instance_id=instance_id,
            source_tool=source_tool,
        )
    ]


# ── 트랜잭션 시간 분해(SQL·외부 호출) ─────────────────────────


def transaction_shares(transactions: list[dict[str, Any]]) -> dict[str, float | None]:
    total = sum(t.get("response_time_ms") or 0 for t in transactions)
    if total <= 0:
        return {"sql_fetch_share": None, "external_share": None}
    sql_fetch = sum((t.get("sql_ms") or 0) + (t.get("fetch_ms") or 0) for t in transactions)
    external = sum(t.get("external_ms") or 0 for t in transactions)
    return {"sql_fetch_share": sql_fetch / total, "external_share": external / total}


def judge_transactions(
    transactions: list[dict[str, Any]],
    th: WasThresholds,
    *,
    instance_id: int | None,
    source_tool: str = "apm_slow_transactions",
) -> list[dict[str, Any]]:
    if len(transactions) < th.share_min_transactions:
        return []
    shares = transaction_shares(transactions)
    out: list[dict[str, Any]] = []
    sql_share = shares["sql_fetch_share"]
    if sql_share is not None and sql_share >= th.slow_sql_share:
        out.append(
            make_signal(
                "was_slow_sql",
                WARNING,
                MEDIUM,
                f"(sql+fetch)/response={sql_share:.2f} ≥ {th.slow_sql_share:g}"
                f" · 상위 {len(transactions)}건",
                instance_id=instance_id,
                source_tool=source_tool,
            )
        )
    ext_share = shares["external_share"]
    if ext_share is not None and ext_share >= th.external_share:
        out.append(
            make_signal(
                "was_external_call_delay",
                WARNING,
                MEDIUM,
                f"external/response={ext_share:.2f} ≥ {th.external_share:g}"
                f" · 상위 {len(transactions)}건",
                instance_id=instance_id,
                source_tool=source_tool,
            )
        )
    return out


# ── 이벤트 ────────────────────────────────────────────────────


def signal_from_event(
    mapped: tuple[str, str, str] | None,
    event_type: str,
    *,
    instance_id: int | None,
    source_tool: str,
) -> dict[str, Any] | None:
    """어댑터가 대응시킨 (kind, level, category)를 신호로 만든다. 대응 없으면 None."""
    if mapped is None:
        return None
    kind, level, category = mapped
    return make_signal(
        kind,
        level,
        category,
        f"이벤트 {event_type}",
        instance_id=instance_id,
        source_tool=source_tool,
    )


# ── 분위수 ───────────────────────────────────────────────────


def percentile(values: list[float], pct: float) -> float | None:
    """nearest-rank 분위수. 빈 목록이면 None."""
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    rank = max(1, int(-(-pct * len(data) // 100)))  # ceil(pct/100 · n)
    return float(data[min(rank, len(data)) - 1])
