#!/usr/bin/env python3
"""조사 실시간 허브 부하 프로브 — 본체 이벤트 루프 정지 측정 (plans/208 트랙 A0-b).

실제 `InvestigationLiveHub`와 `stream_events`(SSE 제너레이터)를 띄우고, 조사 서비스 호출
(`investigation_poll.fetch_poll`)만 가짜로 바꿔 미종결 조사 K건(= 폴러 K개)과 SSE 구독자를 돌린다.
그동안 `src.observability.loop_lag.LoopLagSampler`로 루프 정지(최대·p99·임계 초과 횟수)를 잰다.
실 sre_agent·Redis·LLM 접속은 없다(조사 저장소는 임시 폴더의 실제 `InvestigationStore` JSONL).

가짜 조사 서비스 응답은 sre_agent 계약과 같은 꼴이다 — 조사마다 링 버퍼 300건 · 이벤트 발췌 2KB ·
폴링마다 이벤트가 늘어난다. 응답 직렬화(조사 서비스 쪽 비용)는 스레드로 돌리고, 디코드(`json.loads`
— 본체 MCP 클라이언트 몫)는 루프에서 한다.

    python scripts/investigation_live_probe.py                       # K=1,2,5 · 30초 · 구독자 3+1
    python scripts/investigation_live_probe.py --k 1,2,5 --duration 60 --subscribers 5
    python scripts/investigation_live_probe.py --poll-seconds 1      # 폴링 간격 최소값(스트레스)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from noise_gate.infrastructure.investigation_store import (  # noqa: E402
    STATUS_RUNNING,
    InvestigationStore,
)
from src.api import investigation_live_hub as hub_mod  # noqa: E402
from src.api import investigation_poll  # noqa: E402
from src.observability.loop_lag import LoopLagSampler  # noqa: E402

#: sre_agent 실행 기록 링 버퍼 상한(허브 `ACTIVITY_MAX_EVENTS`와 같다).
RING_EVENTS = hub_mod.ACTIVITY_MAX_EVENTS
#: 실 설정 기본값(`NOISE_INVESTIGATION_LIVE_POLL_SECONDS`).
DEFAULT_POLL_SECONDS = 5.0


def _excerpt(chars: int) -> str:
    """도구 결과 발췌 꼴의 여러 줄 텍스트(마스킹은 줄 단위라 줄 수가 비용을 정한다)."""
    line = (
        "2026-11-02T10:15:03 host-web-01 cpu_util=93.4 mem_util=71.2 proc=java pid=40213 "
        "rss_mb=2048 threads=312 state=R cmd=/opt/app/bin/java -Xmx2g -jar app.jar"
    )
    out: list[str] = []
    size = 0
    while size < chars:
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)[:chars]


class FakeInvestigationService:
    """`fetch_poll` 대역 — 조사별 seq를 폴링마다 늘리고 커서 뒤 이벤트(링 버퍼 300건 안)를
    돌려준다."""

    def __init__(
        self,
        *,
        initial_events: int,
        events_per_poll: int,
        excerpt_chars: int,
        latency_ms: float,
    ) -> None:
        """첫 폴링 시점의 누적 이벤트 수·폴링당 증가분·발췌 길이·왕복 지연(ms)을 받는다."""
        self.initial_events = max(0, initial_events)
        self.events_per_poll = max(0, events_per_poll)
        self.excerpt = _excerpt(excerpt_chars)
        self.latency = max(0.0, latency_ms) / 1000.0
        self.calls = 0
        self.response_bytes = 0
        self._last_seq: dict[str, int] = {}
        self._started: dict[str, float] = {}

    def _event(self, investigation_id: str, seq: int) -> dict[str, Any]:
        engine = "holmes" if seq % 3 == 0 else "playbook"
        base: dict[str, Any] = {
            "seq": seq,
            "at": datetime.now(UTC).isoformat(),
            "engine": engine,
            "step": f"s{seq % 7}",
            "status": "ok",
            "elapsed_ms": 100 + seq % 900,
        }
        if seq % 5 == 0:
            return {**base, "kind": "ai_note", "note": self.excerpt}
        return {
            **base,
            "kind": "tool_result",
            "tool": "polestar_query",
            "label": f"{investigation_id} 서버 사용률 조회 #{seq}",
            "rows": seq % 50,
            "purpose": "CPU 사용률 급등 원인 프로세스 확인",
            "excerpt": self.excerpt,
        }

    async def fetch_poll(
        self, ng: Any, investigation_id: str, *, activity_since: int | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """`investigation_poll.fetch_poll`과 같은 시그니처·반환 꼴."""
        self.calls += 1
        await asyncio.sleep(self.latency)
        now = time.monotonic()
        started = self._started.setdefault(investigation_id, now)
        last = self._last_seq.get(investigation_id)
        last = self.initial_events if last is None else last + self.events_per_poll
        self._last_seq[investigation_id] = last
        first = max((activity_since or 0) + 1, last - RING_EVENTS + 1, 1)
        res = {
            "status": STATUS_RUNNING,
            "phase": "collecting",
            "elapsed_s": round(now - started, 1),
            "progress": {"done": last, "total": last + 50},
            "activity": {
                "next_seq": last,
                "truncated": last > RING_EVENTS,
                "events": [self._event(investigation_id, s) for s in range(first, last + 1)],
            },
        }
        # 직렬화는 조사 서비스 프로세스 몫 — 본체 루프 밖(스레드)에서 한다.
        payload = await asyncio.to_thread(json.dumps, res, ensure_ascii=False)
        self.response_bytes += len(payload.encode("utf-8"))
        # 디코드는 본체 MCP 클라이언트 몫 — 루프에서 한다.
        return json.loads(payload), None


@dataclass
class StreamStats:
    """구독자 SSE 소비 집계."""

    frames: int = 0
    bytes: int = 0


def _mask_fn() -> Any:
    """사용자 경로 마스킹(D-325 ③ — 라우트가 쓰는 `_investigation_mask_fn` 그대로)."""
    from src.api.routes.alarm import _investigation_mask_fn

    return _investigation_mask_fn(SimpleNamespace(security=SimpleNamespace(mask_pattern="***")))


async def _consume(
    hub: hub_mod.InvestigationLiveHub,
    ctx: hub_mod.StreamContext,
    stop: asyncio.Event,
    stats: StreamStats,
) -> None:
    async def _disconnected() -> bool:
        return stop.is_set()

    async for frame in hub_mod.stream_events(hub, ctx, _disconnected):
        stats.frames += 1
        stats.bytes += len(frame.encode("utf-8"))


async def run_one(
    k: int,
    *,
    duration: float,
    subscribers: int,
    operator_subscribers: int,
    poll_seconds: float,
    service: FakeInvestigationService,
    threshold_ms: float,
    workdir: Path,
) -> dict[str, Any]:
    """조사 K건 · 구독자로 `duration`초 돌리고 루프 정지 요약을 돌려준다."""
    store = InvestigationStore(str(workdir / f"investigations_k{k}.jsonl"))
    recs: dict[str, dict[str, Any]] = {}
    for i in range(k):
        investigation_id = f"probe-k{k}-{i}"
        store.record(
            investigation_id=investigation_id,
            alarm_ids=[f"alarm-{k}-{i}"],
            status=STATUS_RUNNING,
            source="probe",
        )
        rec = store.get_by_investigation(investigation_id)
        assert rec is not None
        recs[investigation_id] = rec
    ng = SimpleNamespace(investigation_live_poll_seconds=poll_seconds)
    hub = hub_mod.InvestigationLiveHub(
        ng, store_factory=lambda: store, activity_store=None, poll_seconds=poll_seconds
    )
    mask = _mask_fn()
    sampler = LoopLagSampler(threshold_ms=threshold_ms)
    sampler_task = asyncio.create_task(sampler.run())
    await asyncio.sleep(0.2)
    sampler.summary(reset=True)  # 기동 직후 흔들림은 뺀다
    calls_before, bytes_before = service.calls, service.response_bytes
    cpu_before, wall_before = time.process_time(), time.perf_counter()
    stop = asyncio.Event()
    stats = StreamStats()
    tasks = []
    for n in range(subscribers + operator_subscribers):
        operator = n >= subscribers
        ctx = hub_mod.StreamContext(
            operator=operator,
            mask_fn=None if operator else mask,
            in_scope=lambda rec: True,
            watch=dict(recs),
            cursors={investigation_id: -1 for investigation_id in recs},
        )
        tasks.append(asyncio.create_task(_consume(hub, ctx, stop, stats)))
    await asyncio.sleep(duration)
    summary = sampler.summary()
    cpu = time.process_time() - cpu_before
    wall = time.perf_counter() - wall_before
    stop.set()
    hub._notify()  # 스트림을 깨워 연결 해제를 바로 보게 한다
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=poll_seconds + 1.0)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    await hub.aclose()
    sampler_task.cancel()
    await asyncio.gather(sampler_task, return_exceptions=True)
    return {
        "k": k,
        "subscribers": subscribers + operator_subscribers,
        "polls": service.calls - calls_before,
        "response_mb": (service.response_bytes - bytes_before) / 1_000_000,
        "frames": stats.frames,
        "sse_mb": stats.bytes / 1_000_000,
        "samples": summary.samples,
        "max_ms": summary.max_ms,
        "p99_ms": summary.p99_ms,
        "over": summary.over_threshold,
        "cpu_pct": 100.0 * cpu / wall if wall > 0 else 0.0,
    }


async def run_probe(
    ks: list[int],
    *,
    duration: float = 30.0,
    subscribers: int = 3,
    operator_subscribers: int = 1,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    initial_events: int = RING_EVENTS,
    events_per_poll: int = 10,
    excerpt_chars: int = hub_mod.TEXT_MAX_CHARS,
    latency_ms: float = 20.0,
    threshold_ms: float = 100.0,
) -> list[dict[str, Any]]:
    """K 목록을 차례로 돌린다 — `fetch_poll`은 실행 동안만 가짜로 바꾸고 끝나면 되돌린다."""
    original = investigation_poll.fetch_poll
    rows: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="inv_live_probe_") as tmp:
            for k in ks:
                service = FakeInvestigationService(
                    initial_events=initial_events,
                    events_per_poll=events_per_poll,
                    excerpt_chars=excerpt_chars,
                    latency_ms=latency_ms,
                )
                investigation_poll.fetch_poll = service.fetch_poll  # type: ignore[assignment]
                rows.append(await run_one(
                    k,
                    duration=duration,
                    subscribers=subscribers,
                    operator_subscribers=operator_subscribers,
                    poll_seconds=poll_seconds,
                    service=service,
                    threshold_ms=threshold_ms,
                    workdir=Path(tmp),
                ))
    finally:
        investigation_poll.fetch_poll = original  # type: ignore[assignment]
    return rows


def format_table(rows: list[dict[str, Any]], threshold_ms: float) -> str:
    """결과 표(마크다운)."""
    head = (
        f"| K(폴러) | 구독자 | 폴링 | 응답 MB | SSE 프레임 | SSE MB | 샘플 | 최대 정지 ms | p99 ms "
        f"| ≥{threshold_ms:.0f}ms | CPU% |"
    )
    lines = [head, "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(
            f"| {r['k']} | {r['subscribers']} | {r['polls']} | {r['response_mb']:.2f} "
            f"| {r['frames']} | {r['sse_mb']:.2f} | {r['samples']} | {r['max_ms']:.1f} "
            f"| {r['p99_ms']:.1f} | {r['over']} | {r['cpu_pct']:.0f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add = parser.add_argument
    add("--k", default="1,2,5", help="동시 미종결 조사 수(폴러 수) 목록 — 쉼표 구분")
    add("--duration", type=float, default=30.0, help="K마다 측정 시간(초)")
    add("--subscribers", type=int, default=3, help="사용자 경로(마스킹) SSE 구독자 수")
    add("--operator-subscribers", type=int, default=1, help="운영자 경로 SSE 구독자 수")
    add("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help="허브 폴링 간격(초)")
    add("--initial-events", type=int, default=RING_EVENTS, help="첫 폴링 때 쌓인 이벤트 수")
    add("--events-per-poll", type=int, default=10, help="폴링마다 늘어나는 이벤트 수")
    add("--excerpt-chars", type=int, default=hub_mod.TEXT_MAX_CHARS, help="이벤트 발췌 길이(자)")
    add("--latency-ms", type=float, default=20.0, help="가짜 조사 서비스 왕복 지연(ms)")
    add("--threshold-ms", type=float, default=100.0, help="정지 임계(ms)")
    args = parser.parse_args(argv)
    ks = [int(x) for x in args.k.split(",") if x.strip()]
    rows = asyncio.run(run_probe(
        ks,
        duration=args.duration,
        subscribers=args.subscribers,
        operator_subscribers=args.operator_subscribers,
        poll_seconds=args.poll_seconds,
        initial_events=args.initial_events,
        events_per_poll=args.events_per_poll,
        excerpt_chars=args.excerpt_chars,
        latency_ms=args.latency_ms,
        threshold_ms=args.threshold_ms,
    ))
    print(
        f"조사 실시간 허브 프로브 — 측정 {args.duration:.0f}초/K · 폴링 {args.poll_seconds:g}초 · "
        f"구독자 사용자 {args.subscribers}+운영자 {args.operator_subscribers} · "
        f"첫 응답 이벤트 {args.initial_events}건 · 폴링당 +{args.events_per_poll} · "
        f"발췌 {args.excerpt_chars}자"
    )
    print(format_table(rows, args.threshold_ms))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
