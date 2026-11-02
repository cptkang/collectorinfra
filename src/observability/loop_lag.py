"""이벤트 루프 정지(lag) 계측 (plans/208 트랙 A0-b).

주기 `asyncio.sleep(interval)`이 예정보다 늦게 깨어난 만큼을 「루프 정지」로 잰다. 루프를 막는
동기 작업(파일 전체 순회·큰 JSON 직렬화 등)이 있으면 그 시간만큼 잠이 늦게 끝난다.

- `LoopLagSampler.run()`이 샘플을 모으고, `summary()`가 창 단위 요약(샘플 수·최대·p99·임계 초과
  횟수)을 돌려준다. 창 샘플은 상한(`max_samples`)이 있는 링 버퍼다(in-memory 상한 필수).
- `run_loop_lag_monitor()`는 본체 lifespan이 옵트인일 때만 띄우는 태스크다 — 임계 초과 정지를
  WARNING 1줄, `report_seconds`마다 INFO 요약 1줄로 남긴다.

계층: infrastructure(`src/observability/`). 순수 asyncio · 외부 의존 없음.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: 샘플 간격(초) — 100ms 정지를 놓치지 않을 만큼 촘촘하고, 깨어남 비용(초당 20회)은 무시할 만하다.
DEFAULT_INTERVAL_SECONDS = 0.05
#: 창 샘플 상한 — 간격 0.05초면 약 50분 분량. 넘으면 오래된 샘플부터 밀려난다
#: (최대·초과 횟수는 유지).
DEFAULT_MAX_SAMPLES = 60_000


@dataclass(frozen=True)
class LagSummary:
    """창 1개의 정지 요약(ms)."""

    samples: int
    max_ms: float
    p99_ms: float
    over_threshold: int
    threshold_ms: float
    window_seconds: float


def percentile(values: list[float], q: float) -> float:
    """최근접 순위(nearest-rank) 백분위수 — 빈 목록이면 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


class LoopLagSampler:
    """주기 sleep의 초과 지연으로 이벤트 루프 정지를 재는 샘플러."""

    def __init__(
        self,
        *,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        threshold_ms: float = 100.0,
        max_samples: int = DEFAULT_MAX_SAMPLES,
        on_stall: Callable[[float], None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """간격(초)·임계(ms)·창 샘플 상한·임계 초과 콜백(정지 ms)·시계를 받는다."""
        self.interval = float(interval)
        self.threshold_ms = float(threshold_ms)
        self._on_stall = on_stall
        self._clock = clock
        self._samples: deque[float] = deque(maxlen=max(1, int(max_samples)))
        self._max_ms = 0.0
        self._over = 0
        self._window_started = clock()

    def observe(self, lag_ms: float) -> None:
        """샘플 1개(정지 ms)를 창에 더한다 — 임계 이상이면 초과로 세고 콜백을 부른다."""
        lag_ms = max(0.0, float(lag_ms))
        self._samples.append(lag_ms)
        if lag_ms > self._max_ms:
            self._max_ms = lag_ms
        if lag_ms >= self.threshold_ms:
            self._over += 1
            if self._on_stall is not None:
                self._on_stall(lag_ms)

    def summary(self, *, reset: bool = False) -> LagSummary:
        """현재 창 요약 — `reset`이면 창을 비우고 새로 시작한다."""
        now = self._clock()
        result = LagSummary(
            samples=len(self._samples),
            max_ms=self._max_ms,
            p99_ms=percentile(list(self._samples), 99.0),
            over_threshold=self._over,
            threshold_ms=self.threshold_ms,
            window_seconds=now - self._window_started,
        )
        if reset:
            self._samples.clear()
            self._max_ms = 0.0
            self._over = 0
            self._window_started = now
        return result

    async def run(self) -> None:
        """취소될 때까지 `interval`마다 깨어나 초과 지연을 샘플로 남긴다."""
        while True:
            started = self._clock()
            await asyncio.sleep(self.interval)
            self.observe((self._clock() - started - self.interval) * 1000.0)


def format_summary(summary: LagSummary) -> str:
    """요약 한 줄(로그·표 공용)."""
    return (
        f"samples={summary.samples} max={summary.max_ms:.1f}ms p99={summary.p99_ms:.1f}ms "
        f"over_{summary.threshold_ms:.0f}ms={summary.over_threshold} "
        f"window={summary.window_seconds:.0f}s"
    )


async def run_loop_lag_monitor(
    *,
    threshold_ms: float,
    report_seconds: float,
    interval: float = DEFAULT_INTERVAL_SECONDS,
) -> None:
    """본체 루프 정지 감시 태스크 — 임계 초과는 WARNING 1줄, `report_seconds`마다 INFO 요약 1줄.

    `report_seconds`는 1초 미만이면 1초로 올린다(0 이하 설정이 빈 루프가 되지 않게).
    """
    report_seconds = max(1.0, float(report_seconds))

    def _warn(lag_ms: float) -> None:
        logger.warning("이벤트 루프 정지 %.0fms (임계 %.0fms)", lag_ms, threshold_ms)

    sampler = LoopLagSampler(interval=interval, threshold_ms=threshold_ms, on_stall=_warn)
    sampler_task = asyncio.create_task(sampler.run(), name="loop-lag-sampler")
    try:
        while True:
            await asyncio.sleep(report_seconds)
            logger.info("이벤트 루프 정지 요약: %s", format_summary(sampler.summary(reset=True)))
    finally:
        sampler_task.cancel()
        try:
            await sampler_task
        except asyncio.CancelledError:
            pass


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_MAX_SAMPLES",
    "LagSummary",
    "LoopLagSampler",
    "format_summary",
    "percentile",
    "run_loop_lag_monitor",
]
