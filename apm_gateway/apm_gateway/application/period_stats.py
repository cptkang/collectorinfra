"""기간 비교 합계 — 시 단위 애플리케이션 행의 구간 합계 · 인스턴스 합계의 전체 합계 · 지표별 증감
(plans/134 W6 A-1 · plans/175 T2에서 `ApmTools`의 staticmethod를 옮김).

순수 함수다. 비교 계산(가중 평균·비율·증감)은 `domain.analysis` 한 곳에서만 한다. `ApmTools`는
옛 이름(`_period_stats`·`_sum_stats`·`_stats_delta`)의 staticmethod로 다시 노출한다.
"""

from __future__ import annotations

from typing import Any

from apm_gateway.domain import analysis as an

# plans/134 W6 A-1 — 기간 비교 지표
PERIOD_METRICS = ("calls", "failures", "failure_rate", "avg_response_ms", "max_response_ms")


def period_stats(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    """시 단위 애플리케이션 행 → 구간 합계(호출·실패·실패율·가중 평균·최대 · 원자료
    `total_response_ms`). (합계, 가중 평균 재료가 빠졌는가) — 호출이 있는 행에 총 응답시간
    칸이 없으면 평균은 None(계산 불가)이다."""
    calls = sum(r["calls"] for r in rows)
    failures = sum(r["failures"] for r in rows)
    missing = any(r.get("total_response_ms") is None for r in rows if r["calls"])
    total = None if missing else float(sum(r.get("total_response_ms") or 0 for r in rows))
    maxima = [r["max_response_time_ms"] for r in rows if r.get("max_response_time_ms") is not None]
    return {
        "calls": calls,
        "failures": failures,
        "failure_rate": an.rate(failures, calls),
        "avg_response_ms": an.weighted_mean(total, calls),
        "max_response_ms": max(maxima) if maxima else None,
        "total_response_ms": total,
        "application_count": len(rows),
    }, missing


def sum_stats(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """인스턴스 합계 → 전체 합계(가중 평균 = Σ총 응답시간 ÷ Σ호출 — 재료가 하나라도 없으면
    None)."""
    calls = sum(p["calls"] for p in parts)
    failures = sum(p["failures"] for p in parts)
    totals = [p["total_response_ms"] for p in parts if p["calls"]]
    total = None if any(t is None for t in totals) else float(sum(totals))
    maxima = [p["max_response_ms"] for p in parts if p["max_response_ms"] is not None]
    return {
        "calls": calls,
        "failures": failures,
        "failure_rate": an.rate(failures, calls),
        "avg_response_ms": an.weighted_mean(total, calls),
        "max_response_ms": max(maxima) if maxima else None,
        "total_response_ms": total,
    }


def stats_delta(
    current: dict[str, Any] | None, baseline: dict[str, Any] | None
) -> dict[str, dict[str, float | None]]:
    """지표별 증감(실패율은 %p) — 한쪽이 없으면 N/A(0으로 채우지 않는다)."""

    def get(d: dict[str, Any] | None, k: str) -> Any:
        return d.get(k) if d else None

    return {
        m: (an.rate_delta if m == "failure_rate" else an.delta)(get(current, m), get(baseline, m))
        for m in PERIOD_METRICS
    }
