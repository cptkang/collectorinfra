"""비교 분석 계산 — 가중 평균 · 비율 · 증감 · 원시 분위수 (plans/134 W6 A-1·A-2 ·
SPEC-apm-question-coverage §8).

표준 라이브러리만 쓰는 순수 함수다. 원칙:

- 평균은 **가중 평균**(Σ합계 ÷ Σ건수)만 낸다 — 구간 평균의 평균을 내지 않는다. 건수가 0·없음이면
  None(계산 불가).
- 증감의 기준값이 0이면 증감률은 None(N/A) — 한쪽이 없으면 둘 다 None. 누락을 0으로 채우지 않는다.
- p95는 **원시 값에서만** 낸다(`signals.percentile` — nearest-rank). 구간 p95의 평균은 쓰지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence

from apm_gateway.domain.signals import percentile


def weighted_mean(total: float | None, count: float | None) -> float | None:
    """Σ합계 ÷ Σ건수 — 건수가 0·None이거나 합계가 None이면 None."""
    if total is None or not count:
        return None
    return total / count


def rate(part: float | None, whole: float | None) -> float | None:
    """비율 part ÷ whole(0~1) — whole이 0·None이거나 part가 None이면 None."""
    if part is None or not whole:
        return None
    return part / whole


def delta(current: float | None, baseline: float | None) -> dict[str, float | None]:
    """증감 `{"abs": c − b, "pct": (c − b) ÷ b × 100}` — 한쪽이 None이면 둘 다 None, b == 0이면
    pct만 None(N/A)."""
    if current is None or baseline is None:
        return {"abs": None, "pct": None}
    diff = current - baseline
    return {"abs": diff, "pct": (diff / baseline * 100) if baseline else None}


def rate_delta(current: float | None, baseline: float | None) -> dict[str, float | None]:
    """비율(0~1)의 증감 — `abs`는 %p(백분율 포인트), `pct`는 비율 자체의 상대 증감률."""
    return delta(
        current * 100 if current is not None else None,
        baseline * 100 if baseline is not None else None,
    )


def p95(values: Sequence[float]) -> float | None:
    """원시 값의 p95(nearest-rank · 빈 목록이면 None)."""
    return percentile(list(values), 95)
