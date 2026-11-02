"""알람 워커 부하 하네스 (plans/207 트랙 B W0 · C-0 — 측정 전용 · 실 LLM·실 Redis 0).

가짜 스트림에 알람 M건을 넣고 워커(`AlarmWorker._process`)로 처리해 **처리량(건/s)** ·
**stream lag 최대값**(스트림에 들어왔으나 워커가 아직 읽지 않은 건수) · **미처리 최대값**(들어왔으나
ACK 전인 건수) · **처리 지연 p50/p95**(스트림 도착 → 첫 ACK)를 낸다.

재료는 W1 동등성 하네스(`noise_gate/tests/equivalence_harness.py`)를 그대로 쓴다 — 게이트 상태를 켠
설정 · 가짜 스트림 · 가상 시계 · 실 게이트 노드를 부르는 가짜 그래프. 이 스크립트는 가짜 그래프
앞에 **고정 지연**(수집 `--collect-delay` 기본 0.5s → LLM `--llm-delay` 기본 2s)을 얹는다.
두 디렉터리(`tests/`·`scripts/`)는 모두 개발 전용(배포 제외 · `pyproject` `exclude`)이고 계층
검사 대상이 아니다.

시나리오:
    - `distinct` — 서로 다른 호스트 M건(호스트·알람명이 모두 다르다).
    - `same` — 같은 호스트 M건(알람명·자원이 서로 달라 지문 중복으로 빠지지 않는다).

실행기(`--executor`)는 하네스 계약 `async (worker, redis, stream_key, group) -> None`(스트림에 있는
것을 다 처리하고 돌아온다)을 따른다. 직렬(`serial` — 현행 N=1 루프)과 (W2) 동시 처리 실행기
`n2`·`n4`·`n8`(워커 `_consume_concurrent` — `ALARM_WORKER_MAX_CONCURRENCY=k`와 같은 경로)이 있다.
도착 간격 `--interval`이 0이면 M건을 한꺼번에 넣고(처리량 측정), 양수면 그 간격으로 흘려 넣는다
(stream lag 측정).

사용:
    python -m noise_gate.scripts.worker_load_bench                       # 두 시나리오 · 기본 지연
    python -m noise_gate.scripts.worker_load_bench --scenario same --count 10 --llm-delay 0.2
    python -m noise_gate.scripts.worker_load_bench --executor n4             # N=4 동시 처리(W2)
    python -m noise_gate.scripts.worker_load_bench --interval 1.0 --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests.equivalence_harness import (
    GROUP,
    STREAM_KEY,
    EventClock,
    Executor,
    FakeAlarmGraph,
    FakeStreamRedis,
    HarnessAlarm,
    concurrent_executor,
    harness_config,
    polestar_payload,
    serial_executor,
    write_enforce_rules,
)

#: 실행기 이름 → 함수. `nK` = (W2) 동시 처리 상한 K.
EXECUTORS: dict[str, Executor] = {
    "serial": serial_executor,
    **{f"n{k}": concurrent_executor(k) for k in (2, 4, 8)},
}

SCENARIOS = ("distinct", "same")
_BASE_TIME = datetime(2026, 11, 2, 9, 0, 0)
# 실행기를 다시 부르기 전 쉬는 시간(초) — 스트림이 잠시 비었을 때(간격 도착) 바쁜 대기를 피한다.
_IDLE_POLL_SECONDS = 0.005


def scenario_alarms(name: str, count: int) -> list[HarnessAlarm]:
    """시나리오 알람 `count`건 — 발생 시각 1초 간격(가상 시계 · 게이트 창 계산용)."""
    if name not in SCENARIOS:
        raise ValueError(f"알 수 없는 시나리오: {name} (가능: {', '.join(SCENARIOS)})")
    out: list[HarnessAlarm] = []
    for i in range(count):
        at = _BASE_TIME + timedelta(seconds=i)
        host = f"bench{i:03d}" if name == "distinct" else "bench000"
        out.append(HarnessAlarm(payload=polestar_payload(
            f"LOAD-{name}-{i}", at, host=host, name=f"부하 알람 {i}", resource=f"res{i}",
        )))
    return out


class _BenchGraph:
    """고정 지연(수집 → LLM) 뒤 하네스 가짜 그래프(실 게이트 노드)를 부른다."""

    def __init__(self, inner: FakeAlarmGraph, *, collect_delay: float, llm_delay: float) -> None:
        self._inner = inner
        self._collect_delay = collect_delay
        self._llm_delay = llm_delay

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        if self._collect_delay > 0:
            await asyncio.sleep(self._collect_delay)
        if self._llm_delay > 0:
            await asyncio.sleep(self._llm_delay)
        return await self._inner.ainvoke(state, config)


class _BenchRedis(FakeStreamRedis):
    """도착·읽기·첫 ACK 시각을 재는 가짜 스트림."""

    def __init__(self, clock: Callable[[], float]) -> None:
        super().__init__()
        self._clock = clock
        self.arrived_at: dict[bytes, float] = {}
        self.first_ack_at: dict[bytes, float] = {}
        self.read_total = 0
        self.max_lag = 0
        self.max_backlog = 0

    def add(self, stream_key: str, fields: dict[bytes, bytes]) -> bytes:
        msg_id = super().add(stream_key, fields)
        self.arrived_at[msg_id] = self._clock()
        arrived = len(self.arrived_at)
        # 값이 오르는 순간은 도착뿐이다 — 여기서만 최대값을 갱신하면 된다.
        self.max_lag = max(self.max_lag, arrived - self.read_total)
        self.max_backlog = max(self.max_backlog, arrived - len(self.first_ack_at))
        return msg_id

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> list[list[Any]]:
        out = await super().xreadgroup(*args, **kwargs)
        self.read_total += sum(len(batch) for _, batch in out)
        return out

    async def xack(self, stream_key: str, group: str, *ids: bytes) -> int:
        now = self._clock()
        for msg_id in ids:
            self.first_ack_at.setdefault(msg_id, now)
        return await super().xack(stream_key, group, *ids)


@dataclass
class BenchResult:
    """시나리오 1회 결과(초·건)."""

    scenario: str
    executor: str
    count: int
    interval: float
    collect_delay: float
    llm_delay: float
    elapsed_s: float
    throughput_per_s: float
    max_stream_lag: int
    max_backlog: int
    p50_latency_s: float
    p95_latency_s: float
    max_latency_s: float
    acked: int
    dead_letters: int


def percentile(values: Sequence[float], pct: float) -> float:
    """최근접 순위 백분위(값이 없으면 0)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


async def run_bench(
    alarms: Sequence[HarnessAlarm],
    *,
    scenario: str = "custom",
    executor: Executor = serial_executor,
    executor_name: str = "serial",
    collect_delay: float = 0.5,
    llm_delay: float = 2.0,
    interval: float = 0.0,
    work_dir: Path | None = None,
) -> BenchResult:
    """알람을 가짜 스트림에 넣고 `executor`로 처리해 처리량·lag·지연을 잰다.

    `interval`이 0이면 전부 넣은 뒤 실행기를 부르고, 양수면 도착 태스크가 그 간격으로 흘려 넣는
    동안 실행기를 반복해 부른다(실행기는 스트림이 비면 돌아온다). 모든 메시지가 ACK되면 끝난다.
    """
    alarms = list(alarms)
    base = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix="wlb-"))
    base.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=base))

    # 하네스처럼 내부 협력자를 바꿔 끼운다(Any — 생성자가 None으로 선언한 필드).
    worker: Any = AlarmWorker(harness_config(write_enforce_rules(run_dir)))
    inner = FakeAlarmGraph({a.alarm_id: a for a in alarms})
    r = _BenchRedis(time.perf_counter)
    worker._graph = _BenchGraph(inner, collect_delay=collect_delay, llm_delay=llm_delay)
    worker._redis = r
    worker._decision_store = DecisionStore(str(run_dir / "decisions.jsonl"))
    worker._episodes = worker._build_episode_tracker()
    worker._page_registry = worker._build_page_registry()

    fields = [
        {b"data": json.dumps(a.payload, ensure_ascii=False).encode("utf-8")} for a in alarms
    ]
    start_epoch = (
        datetime.strptime(str(alarms[0].payload["alarmTime"]), "%Y%m%d%H%M%S").timestamp()
        if alarms else 0.0
    )
    wmod: Any = worker_mod  # 워커 모듈 `time`만 가상 시계로(하네스 `run_sequence`와 같다)
    saved_time = wmod.time
    wmod.time = EventClock(start_epoch)
    t0 = time.perf_counter()
    try:
        feeder: asyncio.Task[None] | None = None
        if interval <= 0:
            for f in fields:
                r.add(STREAM_KEY, f)
        else:
            async def feed() -> None:
                for i, f in enumerate(fields):
                    if i:
                        await asyncio.sleep(interval)
                    r.add(STREAM_KEY, f)

            feeder = asyncio.create_task(feed())
        while True:
            await executor(worker, r, STREAM_KEY, GROUP)
            done_feeding = feeder is None or feeder.done()
            if done_feeding and len(r.first_ack_at) >= len(fields):
                break
            await asyncio.sleep(_IDLE_POLL_SECONDS)
        if feeder is not None:
            await feeder
    finally:
        wmod.time = saved_time

    end = max(r.first_ack_at.values(), default=t0)
    elapsed = end - t0
    latencies = [r.first_ack_at[m] - r.arrived_at[m] for m in r.first_ack_at]
    return BenchResult(
        scenario=scenario,
        executor=executor_name,
        count=len(alarms),
        interval=interval,
        collect_delay=collect_delay,
        llm_delay=llm_delay,
        elapsed_s=round(elapsed, 3),
        throughput_per_s=round(len(latencies) / elapsed, 3) if elapsed > 0 else 0.0,
        max_stream_lag=r.max_lag,
        max_backlog=r.max_backlog,
        p50_latency_s=round(percentile(latencies, 50), 3),
        p95_latency_s=round(percentile(latencies, 95), 3),
        max_latency_s=round(max(latencies, default=0.0), 3),
        acked=len(r.first_ack_at),
        dead_letters=len(r.dead_letters),
    )


def render_table(results: Sequence[BenchResult]) -> str:
    """마크다운 표."""
    head = (
        "| 시나리오 | 실행기 | 건수 | 도착 간격(s) | 소요(s) | 처리량(건/s) | stream lag 최대 | "
        "미처리 최대 | p50(s) | p95(s) | 최대(s) |"
    )
    sep = "|" + "---|" * 11
    rows = [
        f"| {x.scenario} | {x.executor} | {x.count} | {x.interval:g} | {x.elapsed_s:.2f} | "
        f"{x.throughput_per_s:.3f} | {x.max_stream_lag} | {x.max_backlog} | "
        f"{x.p50_latency_s:.2f} | {x.p95_latency_s:.2f} | {x.max_latency_s:.2f} |"
        for x in results
    ]
    return "\n".join([head, sep, *rows])


async def _main_async(args: argparse.Namespace) -> list[BenchResult]:
    names = SCENARIOS if args.scenario == "both" else (args.scenario,)
    out: list[BenchResult] = []
    with tempfile.TemporaryDirectory(prefix="wlb-") as tmp:
        for name in names:
            out.append(await run_bench(
                scenario_alarms(name, args.count),
                scenario=name,
                executor=EXECUTORS[args.executor],
                executor_name=args.executor,
                collect_delay=args.collect_delay,
                llm_delay=args.llm_delay,
                interval=args.interval,
                work_dir=Path(tmp),
            ))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(description="알람 워커 부하 하네스 (plans/207 W0·W2)")
    parser.add_argument("--scenario", choices=(*SCENARIOS, "both"), default="both")
    parser.add_argument("--count", type=int, default=40, help="시나리오당 알람 수 (기본 40)")
    parser.add_argument("--executor", choices=sorted(EXECUTORS), default="serial")
    parser.add_argument("--llm-delay", type=float, default=2.0, help="가짜 LLM 지연(초, 기본 2)")
    parser.add_argument(
        "--collect-delay", type=float, default=0.5, help="가짜 수집 지연(초, 기본 0.5)"
    )
    parser.add_argument(
        "--interval", type=float, default=0.0, help="도착 간격(초, 0=한꺼번에 · 기본 0)"
    )
    parser.add_argument("--json", action="store_true", help="표 대신 JSON 줄로 출력")
    args = parser.parse_args(argv)

    results = asyncio.run(_main_async(args))
    if args.json:
        for res in results:
            print(json.dumps(asdict(res), ensure_ascii=False))
    else:
        print(render_table(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
