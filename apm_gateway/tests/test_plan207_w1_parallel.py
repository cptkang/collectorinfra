"""병렬 기반 — 유계 병렬 헬퍼 · 소스별 동시 요청 상한 · `wait_slot` 경합 · 예상 시간 동시 폭
(plans/207 W1 · D-296 ⑤).

외부 호출 0 — 가짜 코루틴 · httpx `MockTransport`(지연) · 가상 시계.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.application.jobs import BASIS_MEASURED, Job, JobManager
from apm_gateway.application.parallel import gather_bounded
from apm_gateway.application.spool import Spool
from apm_gateway.domain import jobs as js
from apm_gateway.domain.call_context import CallScope, current_scope, use_scope
from apm_gateway.domain.errors import API_ERROR, ApmError
from conftest import NOW_S, make_tools

from apm_gateway.config import (
    GatewayConfig,
    JenniferApiConfig,
    JobConfig,
    PerfConfig,
    load_config,
)

TOOL = "apm_fleet_ranking"
SOURCE = "jennifer"
GROUP = "/api/realtime/instance"


# ── 유계 병렬 헬퍼 ─────────────────────────────────────────


class _Probe:
    """동시 실행 수·시작 순서를 잰다."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.started: list[int] = []

    async def run(self, item: int, delay: float) -> int:
        self.started.append(item)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(delay)
        finally:
            self.active -= 1
        return item * 10


async def test_results_keep_input_order_and_apm_error_is_a_value():
    probe = _Probe()

    async def fn(item: int) -> int:
        if item == 2:
            await asyncio.sleep(0.01)
            raise ApmError(API_ERROR, "도메인 2 실패")
        return await probe.run(item, 0.05 - item * 0.01)  # 뒤 항목이 먼저 끝난다

    out = await gather_bounded([0, 1, 2, 3, 4], fn, 3)
    assert [o if not isinstance(o, ApmError) else "E" for o in out] == [0, 10, "E", 30, 40]
    assert isinstance(out[2], ApmError) and out[2].reason == "도메인 2 실패"
    assert 1 < probe.peak <= 3


async def test_peak_concurrency_is_bounded_by_limit():
    probe = _Probe()
    out = await gather_bounded(range(10), lambda i: probe.run(i, 0.02), 4)
    assert out == [i * 10 for i in range(10)]
    assert probe.peak == 4


async def test_limit_one_is_serial_in_input_order():
    probe = _Probe()
    out = await gather_bounded([3, 1, 2], lambda i: probe.run(i, 0.01), 1)
    assert out == [30, 10, 20]
    assert probe.started == [3, 1, 2] and probe.peak == 1


async def test_empty_items_returns_empty_list():
    async def fn(item: int) -> int:  # pragma: no cover — 부르지 않는다
        raise AssertionError

    assert await gather_bounded([], fn, 4) == []


async def test_other_exception_propagates_and_cancels_the_rest():
    cancelled: list[int] = []
    finished: list[int] = []

    async def fn(item: int) -> int:
        if item == 0:
            await asyncio.sleep(0.01)
            raise RuntimeError("버그")
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            cancelled.append(item)
            raise
        finished.append(item)
        return item

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="버그"):
        await gather_bounded([0, 1, 2, 3], fn, 2)
    assert time.monotonic() - started < 0.5  # 남은 호출을 기다리지 않았다
    # 진행 중이던 1(과 0이 비운 자리에 막 들어온 2)은 취소됐고, 끝까지 간 호출·3의 시작은 없다
    assert finished == [] and 1 in cancelled and 3 not in cancelled


async def test_caller_cancel_cancels_children_and_propagates():
    cancelled: list[int] = []
    entered = asyncio.Event()

    async def fn(item: int) -> int:
        entered.set()
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            cancelled.append(item)
            raise
        return item

    task = asyncio.create_task(gather_bounded([0, 1, 2], fn, 2))
    await entered.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(cancelled) == [0, 1]


async def test_children_inherit_call_scope():
    scope = CallScope()
    seen: list[CallScope] = []

    async def fn(item: int) -> int:
        await asyncio.sleep(0)
        seen.append(current_scope())
        return item

    with use_scope(scope):
        await gather_bounded(range(5), fn, 3)
    assert seen and all(s is scope for s in seen)


# ── 소스별 동시 요청 상한(클라이언트) ─────────────────────────


def _delayed_client(delay: float, **cfg) -> tuple[JenniferClient, dict[str, int], list[float]]:
    stats = {"active": 0, "peak": 0}
    starts: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        starts.append(time.monotonic())
        stats["active"] += 1
        stats["peak"] = max(stats["peak"], stats["active"])
        try:
            await asyncio.sleep(delay)
        finally:
            stats["active"] -= 1
        return httpx.Response(200, json={"result": []})

    base = {"url": "http://apm.test", "token": "t", "rate_limit_per_sec": 0}
    base.update(cfg)
    client = JenniferClient(JenniferApiConfig(**base), transport=httpx.MockTransport(handler))
    return client, stats, starts


async def test_client_in_flight_bounded_even_without_rate_limit():
    client, stats, _ = _delayed_client(0.03, max_inflight_per_source=2)
    await asyncio.gather(
        *(client.get_json("/api/instance", {"domain_id": d}) for d in range(8))
    )
    await client.aclose()
    assert stats["peak"] == 2 and client.calls_total == 8


async def test_client_in_flight_one_is_serial():
    client, stats, _ = _delayed_client(0.01, max_inflight_per_source=1)
    await asyncio.gather(
        *(client.get_json("/api/instance", {"domain_id": d}) for d in range(4))
    )
    await client.aclose()
    assert stats["peak"] == 1


async def test_client_in_flight_keeps_start_interval():
    # 호출(0.3초)이 속도 간격(10회/초 = 0.1초)보다 길어 겹쳐도 시작 간격은 지켜진다 — 동시 상한을
    # 속도 제어 앞에서 잡기 때문이다(뒤에서 잡으면 상한 대기를 마친 호출이 몰려 나간다)
    client, stats, starts = _delayed_client(0.3, max_inflight_per_source=3, rate_limit_per_sec=10)
    await client.get_json("/api/domain")  # 간격 기준점
    await asyncio.gather(
        *(client.get_json("/api/instance", {"domain_id": d}) for d in range(5))
    )
    await client.aclose()
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert min(gaps) >= 0.1 - 0.02
    assert stats["peak"] == 3  # 지연 0.3 > 간격 0.1 — 상한까지 겹친다


# ── `wait_slot` 동시 진입 경합 ──────────────────────────────


def _manager(tmp_path, *, max_concurrent: int = 4, max_in_flight: int = 1) -> JobManager:
    tools, _ = make_tools("")
    cfg = JobConfig(spool_dir=tmp_path / "spool", max_concurrent=max_concurrent)
    return JobManager(
        Spool(cfg.spool_dir),
        cfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        rate_per_sec=5,
        clock=lambda: NOW_S,
        max_in_flight=max_in_flight,
    )


def _job(mgr: JobManager) -> Job:
    return Job(mgr, tool=TOOL, principal="chat", owner=None, target="", now=NOW_S)


async def test_concurrent_wait_slot_of_one_job_takes_one_slot(tmp_path):
    mgr = _manager(tmp_path, max_concurrent=2)
    b, c, a = _job(mgr), _job(mgr), _job(mgr)
    mgr._promote(b)
    mgr._promote(c)
    mgr._promote(a)
    assert (b.holds_slot, c.holds_slot, a.state) == (True, True, js.QUEUED)
    # 같은 작업의 병렬 호출 둘이 함께 슬롯을 기다린다
    waits = [asyncio.create_task(mgr.wait_slot(a)) for _ in range(2)]
    await asyncio.sleep(0)
    mgr._release(b)
    await asyncio.sleep(0)
    mgr._release(c)
    await asyncio.wait_for(asyncio.gather(*waits), 1.0)
    assert a.holds_slot and a.state == js.RUNNING
    assert mgr._slots.in_use == 1  # 교정 전: 2(슬롯 하나를 더 가져가고 하나만 반납)
    mgr._release(a)
    assert mgr._slots.in_use == 0  # 누수 없음 — 다른 작업이 영구 대기하지 않는다


async def test_parallel_calls_of_queued_job_through_execute_release_all_slots(tmp_path):
    mgr = _manager(tmp_path, max_concurrent=1)
    tools, _ = make_tools("")
    gate = asyncio.Event()

    async def holder():
        await current_scope().before_call(SOURCE)
        await gate.wait()
        return tools.ok(TOOL, [])

    async def fan_out():
        async def one(i: int) -> int:
            await current_scope().before_call(SOURCE)
            return i

        out = await gather_bounded(range(4), one, 4)
        return tools.ok(TOOL, [{"i": i} for i in out])

    first = await mgr.execute(TOOL, holder, principal="chat", wait_seconds=0)
    second = await mgr.execute(TOOL, fan_out, principal="chat", wait_seconds=0)
    assert first["job"]["state"] == js.RUNNING and second["job"]["state"] == js.QUEUED
    gate.set()
    await asyncio.wait_for(asyncio.gather(*list(mgr._tasks)), 2.0)
    assert mgr._slots.in_use == 0
    third = await mgr.execute(TOOL, holder, principal="chat", wait_seconds=0.5)
    assert third["rows"] == [] and "error" not in third


# ── 예상 시간 — 동시 폭 반영 ──────────────────────────────────


def _measured_job(mgr: JobManager, *, expected: int, done: int, seconds: float) -> Job:
    job = _job(mgr)
    job.expect_calls(expected)
    job.api_calls = done
    job.after_call(SOURCE, seconds, group=GROUP)
    return job


def test_estimate_width_one_matches_previous_formula(tmp_path):
    mgr = _manager(tmp_path, max_in_flight=4)
    job = _measured_job(mgr, expected=10, done=2, seconds=2.0)
    # 종전 식: 남은 8 × max(1/5, 2.0) = 16.0 — 신고가 없으면(직렬 루프) 상한 4여도 그대로
    assert job.estimate() == {"api_calls": 10, "seconds": 16.0, "basis": BASIS_MEASURED}


def test_estimate_divides_by_declared_width_within_source_cap(tmp_path):
    mgr = _manager(tmp_path, max_in_flight=4)
    job = _measured_job(mgr, expected=10, done=2, seconds=2.0)
    with job.parallel(4):
        assert job.estimate()["seconds"] == 4.0  # 8 × max(0.2, 2.0 / 4)
        with job.parallel(2):  # 겹친 신고는 가장 넓은 폭
            assert job.estimate()["seconds"] == 4.0
    assert job.estimate()["seconds"] == 16.0  # 블록을 나오면 직렬로 돌아간다
    capped = _manager(tmp_path, max_in_flight=2)
    job2 = _measured_job(capped, expected=10, done=2, seconds=2.0)
    with job2.parallel(4):
        assert job2.estimate()["seconds"] == 8.0  # 소스 상한 2로 자른다


def test_estimate_width_bounded_by_remaining_calls_and_rate(tmp_path):
    mgr = _manager(tmp_path, max_in_flight=4)
    job = _measured_job(mgr, expected=10, done=8, seconds=2.0)
    with job.parallel(4):
        assert job.estimate()["seconds"] == 2.0  # 남은 2 × 2.0 / min(4, 2)
    fast = _measured_job(_manager(tmp_path, max_in_flight=4), expected=10, done=2, seconds=0.4)
    with fast.parallel(4):
        assert fast.estimate()["seconds"] == 1.6  # 8 × max(1/5, 0.1) — 속도 상한이 지배


def test_estimate_calls_preflight_is_unchanged(tmp_path):
    mgr = _manager(tmp_path, max_in_flight=4)
    job = _measured_job(mgr, expected=10, done=0, seconds=2.0)
    with job.parallel(4):
        assert mgr.estimate_calls(10, [SOURCE], GROUP) == {
            "api_calls": 10,
            "seconds": 20.0,
            "basis": BASIS_MEASURED,
        }


async def test_gather_bounded_declares_width_to_job_only_while_running(tmp_path):
    mgr = _manager(tmp_path, max_in_flight=4)
    job = _measured_job(mgr, expected=10, done=2, seconds=2.0)
    inside: list[float] = []

    async def fn(item: int) -> int:
        inside.append(job.estimate()["seconds"])
        await asyncio.sleep(0)
        return item

    with use_scope(job):
        await gather_bounded(range(3), fn, 4)  # 폭 = min(4, 항목 3)
        await gather_bounded(range(3), fn, 1)  # 직렬 — 신고 없음
    assert inside[:3] == [round(8 * 2.0 / 3, 1)] * 3
    assert inside[3:] == [16.0] * 3
    assert job.estimate()["seconds"] == 16.0


def test_from_config_takes_slowest_source_in_flight():
    cfg = load_config(
        {
            "JENNIFER_SOURCES": '["bank", "legacy"]',
            "JENNIFER_BANK_API_URL": "http://bank.test",
            "JENNIFER_BANK_API_TOKEN": "a",
            "JENNIFER_LEGACY_API_URL": "http://legacy.test",
            "JENNIFER_LEGACY_API_TOKEN": "b",
            "JENNIFER_LEGACY_MAX_INFLIGHT_PER_SOURCE": "2",
        },
        dotenv=None,
    )
    tools, _ = make_tools("")
    mgr = JobManager.from_config(cfg, envelope=tools.ok, error_envelope=tools.err)
    assert mgr.max_in_flight == 2
    single = JobManager.from_config(GatewayConfig(), envelope=tools.ok, error_envelope=tools.err)
    # 폭 = min(소스 자리 수, 도구 안 폭 상한 APM_FANOUT_CONCURRENCY — plans/205 · D-356 ①)
    assert single.max_in_flight == min(
        JenniferApiConfig().max_inflight_per_source, PerfConfig().fanout_concurrency
    )
