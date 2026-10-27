"""plans/194 W3 독립 검증 — dispatcher 부하 계수 불변식 스트레스(verifier 작성).

스레드 수십 개가 무작위로 잡을 넣고, 조사 함수는 무작위로 짧게 끝나거나(정상) 타임아웃을 넘기거나
(버려진 조사) 예외를 올린다. 일부 대기 잡은 밖에서 종결시켜 `_skip_if_stale` 경로도 탄다.

불변식:
- 실행 중 `0 <= _zombie_holding <= _holding <= max_concurrent` · `_queued >= 0`
- 버려진 조사를 포함한 **실제 동시 조사 함수 수 <= max_concurrent**(grace가 충분히 길 때 — S1)
- 같은 호스트의 조사 함수가 동시에 둘 돌지 않는다(L-4 — 버려진 조사 포함)
- 끝나면 계수 0 · 세마포어 전량 반환(over-release ValueError 0) · 호스트 키·보조 신호·대기 목록 0
- 거부 사유는 정해진 집합 안

실 LLM 0 · 네트워크 0.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from typing import Any

import pytest
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import (
    GUARD_QUEUE_FULL,
    GUARD_SERVICE_OVERLOADED,
    InvestigationDispatcher,
)
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.settings import AgentSettings

MAX_CONCURRENT = 2
MAX_QUEUED = 3
ALLOWED_REJECTS = {GUARD_QUEUE_FULL, GUARD_SERVICE_OVERLOADED, "host_investigation_in_flight"}


def _settings(grace: float) -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        model="test/model",
        api_key=None,
        max_steps=3,
        gemini_api_key="k",
        service_bearer_token=None,
        investigation_timeout_seconds=300,
        investigation_dedup_ttl_seconds=None,
        investigation_max_concurrent=MAX_CONCURRENT,
        investigation_max_queued=MAX_QUEUED,
        investigation_abandon_grace_seconds=grace,
        investigation_hourly_budget=None,
        severity_judge_enabled=False,
        sre_activity_log_enabled=False,
    )


def _job(server: str, iid: str) -> InvestigationJob:
    now = time.time()
    return InvestigationJob(
        investigation_id=iid, kind="alarm", status="running", created_at=now, updated_at=now,
        fingerprint=f"fp-{iid}",
        payload={"event": {"serverName": server, "severity": 2}, "decision": {"tier": "PAGE"}},
    )


def _ok() -> DiagnosisResult:
    return DiagnosisResult(answer="원인 ← journalctl",
                           tool_outputs=[ToolCallRecord("journalctl", "로그", "success", "clean")])


class _Probe:
    """조사 함수 — 실제 동시 실행 수·호스트별 동시 실행을 잰다(협조적 중단은 무작위로 따름)."""

    def __init__(self, seed: int, timeout: float) -> None:
        self._rng = random.Random(seed)
        self._timeout = timeout
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.per_host: dict[str, int] = {}
        self.host_overlap: list[str] = []
        self.calls = 0

    def __call__(self, job: Any) -> DiagnosisResult:
        host = job.payload["event"]["serverName"]
        with self._lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.per_host[host] = self.per_host.get(host, 0) + 1
            if self.per_host[host] > 1:
                self.host_overlap.append(host)
            roll = self._rng.random()
            dur = self._rng.uniform(0.0, self._timeout * 3)
            cooperative = self._rng.random() < 0.5
        try:
            if roll < 0.15:
                time.sleep(dur / 4)
                raise RuntimeError("가짜 조사 실패")
            if roll < 0.2:
                raise asyncio.CancelledError()  # BaseException 파생(D-213)
            ev = getattr(job, "cancel_event", None)
            if cooperative and ev is not None:
                ev.wait(dur)
            else:
                time.sleep(dur)
            return _ok()
        finally:
            with self._lock:
                self.active -= 1
                self.per_host[host] -= 1


def _check_counts(d: InvestigationDispatcher, bad: list[str]) -> None:
    with d._lock:
        q, h, z = d._queued, d._holding, d._zombie_holding
    if not (q >= 0 and 0 <= z <= h <= MAX_CONCURRENT):
        bad.append(f"queued={q} holding={h} zombie={z}")


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_counters_and_slots_balance_under_random_load(seed: int) -> None:
    timeout = 0.15
    probe = _Probe(seed, timeout)
    d = InvestigationDispatcher(_settings(grace=30.0), diagnose_fn=probe,
                                briefing_fn=build_briefing, timeout_seconds=timeout)
    errors: list[BaseException] = []
    old_hook = threading.excepthook

    def hook(args: Any) -> None:  # 데몬 스레드 안 ValueError(over-release) 등을 잡는다
        errors.append(args.exc_value)

    threading.excepthook = hook
    jobs: list[InvestigationJob] = []
    jobs_lock = threading.Lock()
    bad: list[str] = []
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            _check_counts(d, bad)
            time.sleep(0.002)

    def submitter(tid: int) -> None:
        rng = random.Random(seed * 100 + tid)
        for k in range(8):
            job = _job(f"host-{rng.randrange(40)}", f"s{seed}-t{tid}-{k}")
            d(job)
            with jobs_lock:
                jobs.append(job)
            # 대기 중 잡을 밖에서 종결(워치독 흉내) — `_skip_if_stale` 경로
            if rng.random() < 0.15:
                time.sleep(0.01)
                if job.queue_position is not None:
                    job.status = "failed"
            time.sleep(rng.uniform(0, 0.05))

    samp = threading.Thread(target=sampler, daemon=True)
    samp.start()
    try:
        threads = [threading.Thread(target=submitter, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            d.wait_workers(1)
            snap = d.load_snapshot()
            if (not d._workers and snap["abandoned_slots"] == 0 and probe.active == 0
                    and snap["abandoned_threads"] == 0):
                break
            time.sleep(0.02)
    finally:
        stop.set()
        samp.join(2)
        threading.excepthook = old_hook

    assert not errors, errors
    assert not bad, bad[:5]
    assert probe.peak <= MAX_CONCURRENT, f"버려진 조사 포함 실동시 {probe.peak}"
    assert not probe.host_overlap, probe.host_overlap[:5]
    with d._lock:
        assert (d._queued, d._holding, d._zombie_holding) == (0, 0, 0)
        assert not d._inflight_hosts and not d._helpers and not d._waiting
    got = [d._semaphore.acquire(blocking=False) for _ in range(MAX_CONCURRENT)]
    assert all(got), "슬롯 누수"
    assert not d._semaphore.acquire(blocking=False)
    for _ in range(MAX_CONCURRENT):
        d._semaphore.release()
    with pytest.raises(ValueError):
        d._semaphore.release()  # 경계 확인 — 위 반환이 정확히 acquire 수였다
    statuses = {j.status for j in jobs}
    assert statuses <= {"done", "timeout", "failed", "rejected"}, statuses
    rejects = {j.reason for j in jobs if j.status == "rejected"}
    assert rejects <= ALLOWED_REJECTS, rejects
    assert all(j.queue_position is None for j in jobs)
    assert probe.calls > 0


def test_forced_release_after_short_grace_still_balances() -> None:
    """grace가 짧아 강제 반환이 섞여도 계수·세마포어가 맞는다(실동시 상한은 이때 보장 안 됨)."""
    timeout = 0.05
    probe = _Probe(7, timeout * 4)  # 대부분 타임아웃
    d = InvestigationDispatcher(_settings(grace=0.05), diagnose_fn=probe,
                                briefing_fn=build_briefing, timeout_seconds=timeout)
    errors: list[BaseException] = []
    old_hook = threading.excepthook
    threading.excepthook = lambda args: errors.append(args.exc_value)
    try:
        def submitter(tid: int) -> None:
            for k in range(10):
                d(_job(f"h{(tid + k) % 5}", f"f-{tid}-{k}"))
                time.sleep(0.01)

        threads = [threading.Thread(target=submitter, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            d.wait_workers(1)
            if not d._workers and d.load_snapshot()["abandoned_slots"] == 0 and probe.active == 0:
                break
            time.sleep(0.02)
    finally:
        threading.excepthook = old_hook
    assert not errors, errors
    with d._lock:
        assert (d._queued, d._holding, d._zombie_holding) == (0, 0, 0)
        assert not d._inflight_hosts and not d._helpers
    assert all(d._semaphore.acquire(blocking=False) for _ in range(MAX_CONCURRENT))


def test_worker_start_failure_returns_queue_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    import sre_agent.application.investigation_dispatcher as mod

    real_thread = mod.threading.Thread

    class _NoStart(real_thread):  # type: ignore[misc, valid-type]
        def start(self) -> None:
            if getattr(self, "_target", None) is not None and \
                    getattr(self._target, "__name__", "") == "_worker":
                raise RuntimeError("can't start new thread")
            super().start()

    monkeypatch.setattr(mod.threading, "Thread", _NoStart)
    d = InvestigationDispatcher(_settings(grace=1.0), diagnose_fn=lambda job: _ok(),
                                briefing_fn=build_briefing, timeout_seconds=1.0)
    job = _job("web-01", "boom-1")
    d(job)
    # 워커 start 실패는 예외 전파가 아니라 거부 응답(service_overloaded)이다.
    assert job.status == "rejected" and job.reason == GUARD_SERVICE_OVERLOADED
    monkeypatch.setattr(mod.threading, "Thread", real_thread)
    with d._lock:
        assert d._queued == 0, f"대기 자리 누수 {d._queued}"
        assert not d._inflight_hosts and not d._workers
