"""다중 사용자 공정 동시성 (plans/205 W3b · D-356 ⑤).

- ① 순위가 공정 분배보다 앞선다(poller·interactive 우선 · 에이징 불변) · 상한 닿은 호출자 건너뛰기
- ② A가 동시 12건을 먼저 넣은 뒤 B가 1건 → B가 A의 남은 대기분보다 먼저 자리를 받는다
- ③ 호출자 하나뿐이면 상한을 넘겨 자리를 다 쓴다(일 보존)
- ④ 작업 슬롯: A 작업 4개 + B 작업 1개 → B가 3번째 이내로 실행
- ⑤ 다중 사용자 부하(가짜 전송 · 지연 0.1s·0.3s = 실측 1s·3s의 1/10 · 속도·대기 상한도 같은 비율)
- ⑥ 되돌리기 값(0) = 종전 순서(종전 구현 사본과 같은 넘김 순서)
- ④ 혼잡 가시화: 작업 기록·감사의 대기 합계·최대 · `gateway_health` 혼잡 현황 · 대기 초과 사유

외부 호출 0 — httpx `MockTransport` · 가상 시계.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import random
import time
from typing import Any

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.adapters.throttle import PrioritySlots, _effective_rank, _Waiter
from apm_gateway.application.fleet_tools import FleetTools
from apm_gateway.application.jobs import Job, JobManager, _Slots
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain import jobs as js
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_POLLER,
    PRIORITY_RANK,
    CallScope,
    use_scope,
)
from apm_gateway.domain.errors import ApmError
from apm_gateway.interface.server import audit_job_finished
from conftest import NOW_S, make_tools
from fleet_fake import FakeFleet

from apm_gateway.config import JenniferApiConfig, JobConfig, load_config

TOOL = "apm_fleet_ranking"


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _Caller(CallScope):
    """호출자 키만 가진 맥락(작업 밖 시험용)."""

    def __init__(self, caller: str, priority: str = PRIORITY_INTERACTIVE) -> None:
        super().__init__(priority)
        self.caller = caller


async def _enqueue(slots: PrioritySlots, priority: str, caller: str, order: list[str], tag: str):
    async def run() -> None:
        await slots.acquire(priority, caller)
        order.append(tag)

    task = asyncio.create_task(run())
    await asyncio.sleep(0)
    return task


# ── ① 순위 > 공정 분배 · 에이징 불변 · 상한 건너뛰기 ─────────────────


async def test_rank_precedes_fair_share_and_poller_first():
    slots = PrioritySlots(4, per_caller=4)
    for _ in range(3):
        await slots.acquire(PRIORITY_INTERACTIVE, "A")
    await slots.acquire(PRIORITY_INTERACTIVE, "X")  # 자리 4 다 참 · A 3(상한 아래)
    order: list[str] = []
    tasks = [
        await _enqueue(slots, PRIORITY_BACKGROUND, "B", order, "B-bg"),  # 쥔 자리 0
        await _enqueue(slots, PRIORITY_INTERACTIVE, "A", order, "A-int"),  # 쥔 자리 3
        await _enqueue(slots, PRIORITY_POLLER, "", order, "poller"),
    ]
    for releaser in ("X", "A", "A"):
        slots.release(releaser)
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    # 적게 쥔 B보다 순위가 높은 A가 먼저 — poller는 언제나 맨 앞
    assert order == ["poller", "A-int", "B-bg"]


async def test_aging_still_lifts_long_waiting_background():
    clock = _Clock()
    slots = PrioritySlots(1, aging_seconds=10.0, per_caller=1, clock=clock)
    await slots.acquire(PRIORITY_INTERACTIVE, "X")
    order: list[str] = []
    old = await _enqueue(slots, PRIORITY_BACKGROUND, "B", order, "B-old-bg")
    clock.t += 25.0  # 두 단계 올라 유효 순위 0
    fresh = await _enqueue(slots, PRIORITY_INTERACTIVE, "C", order, "C-int")
    slots.release("X")
    await asyncio.sleep(0)
    assert order == ["B-old-bg"]
    slots.release("B")
    await asyncio.gather(old, fresh)
    assert order == ["B-old-bg", "C-int"]


async def test_least_held_caller_first_within_same_rank():
    slots = PrioritySlots(4, per_caller=4)
    for caller in ("A", "A", "A", "B"):
        await slots.acquire(PRIORITY_INTERACTIVE, caller)
    order: list[str] = []
    a = await _enqueue(slots, PRIORITY_INTERACTIVE, "A", order, "A")  # 먼저 왔지만 3개 쥠
    b = await _enqueue(slots, PRIORITY_INTERACTIVE, "B", order, "B")  # 1개 쥠
    slots.release("A")  # A 2 · B 1 → B
    await asyncio.sleep(0)
    assert order == ["B"]
    slots.release("A")
    await asyncio.gather(a, b)
    assert order == ["B", "A"]


async def test_capped_caller_waits_behind_same_rank_caller():
    """상한에 닿은 호출자의 대기자는 같은 순위의 상한 아래 호출자 뒤로 선다(먼저 왔어도)."""
    slots = PrioritySlots(4, per_caller=2)
    for caller in ("A", "A", "X", "X"):
        await slots.acquire(PRIORITY_INTERACTIVE, caller)
    order: list[str] = []
    a = await _enqueue(slots, PRIORITY_INTERACTIVE, "A", order, "A-int")  # A 2 = 상한
    b = await _enqueue(slots, PRIORITY_INTERACTIVE, "B", order, "B-int")  # B 0
    slots.release("X")
    await asyncio.sleep(0)
    assert order == ["B-int"]
    slots.release("X")
    await asyncio.gather(a, b)
    assert order == ["B-int", "A-int"]
    assert slots.held_by("A") == 3 and slots.held_by("B") == 1


async def test_capped_interactive_still_beats_uncapped_background():
    """순위가 먼저다 — 상한 도달 A의 interactive 대기자가 상한 아래 B의 background 대기자보다
    먼저 받는다(D-347 우선순위 불변)."""
    slots = PrioritySlots(4, per_caller=2)
    for caller in ("A", "A", "X", "X"):
        await slots.acquire(PRIORITY_INTERACTIVE, caller)
    order: list[str] = []
    b = await _enqueue(slots, PRIORITY_BACKGROUND, "B", order, "B-bg")  # B 0 · 먼저 옴
    a = await _enqueue(slots, PRIORITY_INTERACTIVE, "A", order, "A-int")  # A 2 = 상한
    slots.release("X")
    await asyncio.sleep(0)
    assert order == ["A-int"]
    slots.release("X")
    await asyncio.gather(a, b)
    assert order == ["A-int", "B-bg"]


async def test_release_and_cancel_do_not_leak_per_caller_counts():
    slots = PrioritySlots(1, per_caller=1)
    await slots.acquire(PRIORITY_INTERACTIVE, "A")
    b = asyncio.create_task(slots.acquire(PRIORITY_INTERACTIVE, "B"))
    c = asyncio.create_task(slots.acquire(PRIORITY_INTERACTIVE, "C"))
    await asyncio.sleep(0)
    slots.release("A")  # B에게 넘긴다
    b.cancel()  # B가 깨기 전에 취소 — 넘겨받은 자리를 C에게
    with pytest.raises(asyncio.CancelledError):
        await b
    await asyncio.wait_for(c, 1.0)
    assert (slots.held_by("A"), slots.held_by("B"), slots.held_by("C")) == (0, 0, 1)
    slots.release("C")
    assert slots.in_flight == 0 and slots.waiting == 0 and slots._held_by == {}
    with pytest.raises(TimeoutError):  # 대기 상한 초과로 취소된 대기자도 새지 않는다
        await slots.acquire(PRIORITY_INTERACTIVE, "D")
        await asyncio.wait_for(slots.acquire(PRIORITY_INTERACTIVE, "E"), 0.01)
    slots.release("D")
    assert slots.in_flight == 0 and slots.waiting == 0 and slots._held_by == {}


# ── ②·③ 클라이언트 경유 ───────────────────────────────────


def _client(delay: float = 0.03, **cfg: Any) -> tuple[JenniferClient, list[str], dict[str, int]]:
    starts: list[str] = []
    peak: dict[str, int] = {"all": 0}
    active: dict[str, int] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        tag = request.url.params.get("domain_id", "")
        caller = tag.split("-")[0]
        starts.append(tag)
        active[caller] = active.get(caller, 0) + 1
        peak["all"] = max(peak["all"], sum(active.values()))
        peak[caller] = max(peak.get(caller, 0), active[caller])
        try:
            await asyncio.sleep(delay)
        finally:
            active[caller] -= 1
        return httpx.Response(200, json={"result": []})

    base: dict[str, Any] = {"url": "http://apm.test", "token": "t", "rate_limit_per_sec": 0}
    base.update(cfg)
    client = JenniferClient(JenniferApiConfig(**base), transport=httpx.MockTransport(handler))
    return client, starts, peak


async def _calls(client: JenniferClient, caller: str, n: int) -> None:
    with use_scope(_Caller(caller)):
        await asyncio.gather(
            *(client.get_json("/api/instance", {"domain_id": f"{caller}-{i}"}) for i in range(n))
        )


async def test_late_user_gets_slot_before_first_users_backlog():
    client, starts, _ = _client()
    a = asyncio.create_task(_calls(client, "A", 12))
    await asyncio.sleep(0.005)  # A의 8건이 자리를 다 채우고 4건이 기다린다
    assert client._slots.in_flight == 8 and client._slots.waiting == 4
    b = asyncio.create_task(_calls(client, "B", 1))
    await asyncio.gather(a, b)
    await client.aclose()
    assert starts.index("B-0") == 8  # 첫 반납 자리 — A의 남은 4건보다 먼저


async def test_single_caller_uses_all_slots_beyond_cap():
    client, starts, peak = _client()
    assert client._slots.per_caller == 4  # 자동 = max(1, 8 // 2)
    await _calls(client, "A", 12)
    await client.aclose()
    assert peak["A"] == 8 and len(starts) == 12  # 다른 호출자가 없으면 상한을 넘겨 쓴다


# ── ④ 작업 슬롯 ─────────────────────────────────────────


def _manager(tmp_path, **job_cfg: Any) -> JobManager:
    tools, _ = make_tools("")
    cfg = JobConfig(spool_dir=tmp_path / "spool", **job_cfg)
    return JobManager(
        Spool(cfg.spool_dir), cfg, envelope=tools.ok, error_envelope=tools.err, clock=lambda: NOW_S
    )


def _job(mgr: JobManager, owner: str) -> Job:
    return Job(mgr, tool=TOOL, principal="chat", owner=owner, target="", now=NOW_S)


async def _job_grant_order(mgr: JobManager) -> list[str]:
    """슬롯 2개를 다른 사용자(c) 작업이 쥔 채 A 작업 4개 · B 작업 1개가 차례로 기다린다 — 쥔 작업이
    먼저 받은 순서대로 하나씩 끝날 때 대기 작업이 슬롯을 받는 순서."""
    running = [_job(mgr, "user:c") for _ in range(2)]
    for job in running:
        mgr._promote(job)
        assert job.holds_slot
    queued = [_job(mgr, "user:a") for _ in range(4)] + [_job(mgr, "user:b")]
    order: list[str] = []

    async def wait(job: Job, tag: str) -> None:
        await mgr.wait_slot(job)
        order.append(tag)
        running.append(job)

    tasks = []
    for i, job in enumerate(queued):
        mgr._promote(job)
        assert job.state == js.QUEUED
        tasks.append(asyncio.create_task(wait(job, "B" if job.owner == "user:b" else f"A{i}")))
        await asyncio.sleep(0)
    while running:
        mgr._release(running.pop(0))
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    assert mgr._slots.in_use == 0 and mgr._slots._held_by == {}
    return order


async def test_job_slot_other_owner_runs_within_third(tmp_path):
    order = await _job_grant_order(_manager(tmp_path, max_concurrent=2))
    assert order.index("B") < 3
    assert order == ["A0", "B", "A1", "A2", "A3"]


def test_job_slots_fair_pick_and_work_conservation():
    slots = _Slots(2, per_owner=1)
    assert slots.try_acquire("a") and slots.try_acquire("a")  # 대기자 없음 — 상한 넘겨 받는다
    assert slots.in_use == 2 and slots._held_by == {"a": 2}


# ── ⑥ 되돌리기 값 = 종전 순서 ───────────────────────────────


class _OldSlots:
    """plans/205 이전 `PrioritySlots` 사본(08a900f3) — 넘김 순서 대조 기준."""

    def __init__(self, limit: int, aging: float, clock) -> None:
        self.limit, self._aging, self._clock = limit, aging, clock
        self._held = 0
        self._waiters: list[_Waiter] = []
        self._seq = itertools.count()

    async def acquire(self, priority: str) -> None:
        if self._held < self.limit and not self._waiters:
            self._held += 1
            return
        w = _Waiter(
            PRIORITY_RANK[priority],
            next(self._seq),
            self._clock(),
            asyncio.get_running_loop().create_future(),
        )
        self._waiters.append(w)
        await w.future

    def release(self) -> None:
        now = self._clock()
        while self._waiters:
            best = min(self._waiters, key=lambda w: (_effective_rank(w, now, self._aging), w.seq))
            self._waiters.remove(best)
            if not best.future.done():
                best.future.set_result(None)
                return
        self._held -= 1


async def _replay(make, ops: list[tuple[str, str, str]], clock: _Clock) -> list[int]:
    """ops = (종류, 우선순위, 호출자) — 같은 조작을 재연해 넘겨받은 요청 번호 순서를 돌려준다."""
    slots = make()
    order: list[int] = []
    tasks = []
    holders: list[str] = []
    for i, (kind, prio, caller) in enumerate(ops):
        if kind == "acq":

            async def run(i: int = i, prio: str = prio, caller: str = caller) -> None:
                if isinstance(slots, _OldSlots):
                    await slots.acquire(prio)
                else:
                    await slots.acquire(prio, caller)
                order.append(i)
                holders.append(caller)

            tasks.append(asyncio.create_task(run()))
        elif kind == "tick":
            clock.t += 4.0
        elif holders:
            caller = holders.pop(0)
            if isinstance(slots, _OldSlots):
                slots.release()
            else:
                slots.release(caller)
        await asyncio.sleep(0)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return order


async def test_off_value_matches_previous_slot_order():
    rnd = random.Random(205)
    prios = [PRIORITY_POLLER, PRIORITY_INTERACTIVE, PRIORITY_BACKGROUND]
    ops = []
    for _ in range(400):
        r = rnd.random()
        if r < 0.55:
            ops.append(("acq", rnd.choice(prios), rnd.choice(["A", "B", "C", ""])))
        elif r < 0.65:
            ops.append(("tick", "", ""))
        else:
            ops.append(("rel", "", ""))
    c1, c2 = _Clock(), _Clock()
    old = await _replay(lambda: _OldSlots(3, 10.0, c1), ops, c1)
    new = await _replay(lambda: PrioritySlots(3, 10.0, per_caller=0, clock=c2), ops, c2)
    assert len(old) > 100 and new == old


async def test_off_value_job_slots_are_fifo(tmp_path):
    order = await _job_grant_order(_manager(tmp_path, max_concurrent=2, max_concurrent_per_owner=0))
    assert order == ["A0", "A1", "A2", "A3", "B"]  # 종전 FIFO — B가 마지막


def test_config_defaults_inheritance_and_off_values(tmp_path):
    cfg = load_config({}, dotenv=None)
    assert cfg.jennifer.max_inflight_per_caller is None and cfg.jobs.max_concurrent_per_owner == 2
    env = {
        "JENNIFER_SOURCES": '["bank", "common"]',
        "JENNIFER_BANK_API_URL": "http://bank.test",
        "JENNIFER_BANK_API_TOKEN": "t1",
        "JENNIFER_COMMON_API_URL": "http://common.test",
        "JENNIFER_COMMON_API_TOKEN": "t2",
        "JENNIFER_COMMON_MAX_INFLIGHT_PER_SOURCE": "6",
        "JENNIFER_MAX_INFLIGHT_PER_CALLER": "0",
        "JENNIFER_BANK_MAX_INFLIGHT_PER_CALLER": "3",
        "APM_JOB_MAX_CONCURRENT_PER_OWNER": "0",
    }
    cfg = load_config(env, dotenv=None)
    bank, common = cfg.sources
    assert bank.max_inflight_per_caller == 3 and common.max_inflight_per_caller == 0
    assert cfg.jobs.max_concurrent_per_owner == 0
    caps = {s.source_id: s.api.client._slots.per_caller for s in build_source_set(cfg)}
    assert caps == {"bank": 3, "common": 0}
    auto = load_config({**env, "JENNIFER_MAX_INFLIGHT_PER_CALLER": ""}, dotenv=None)
    caps = {s.source_id: s.api.client._slots.per_caller for s in build_source_set(auto)}
    assert caps == {"bank": 3, "common": 3}  # 자동 = 그 소스 자리 6 // 2
    for key in ("JENNIFER_MAX_INFLIGHT_PER_CALLER", "APM_JOB_MAX_CONCURRENT_PER_OWNER"):
        with pytest.raises(ValueError, match=key):
            load_config({key: "-1"}, dotenv=None)


# ── ④ 혼잡 가시화 ───────────────────────────────────────


async def test_slot_timeout_reason_names_congestion():
    client, _, _ = _client(delay=0.3, max_inflight_per_source=1, timeout_seconds=0.05)
    a = asyncio.create_task(_calls(client, "A", 1))  # A가 자리를 쥔다
    await asyncio.sleep(0.01)
    c = asyncio.create_task(client._slots.acquire(PRIORITY_INTERACTIVE, "C"))  # C는 계속 기다린다
    await asyncio.sleep(0)
    with use_scope(_Caller("B")), pytest.raises(ApmError) as info:
        await client.get_json("/api/instance", {"domain_id": "B-0"})
    assert info.value.code == "source_unavailable"
    assert "동시 사용 혼잡(대기 1건 · 호출자 2)" in info.value.reason
    for task in (a, c):
        task.cancel()
    await asyncio.gather(a, c, return_exceptions=True)
    await client.aclose()


async def test_waits_recorded_in_job_record_and_audit(tmp_path, caplog):
    client, _, _ = _client(delay=0.0, rate_limit_per_sec=10)
    mgr = _manager(tmp_path)
    tools, _ = make_tools("")
    owned: list[Job] = []

    async def call() -> dict[str, Any]:
        for d in range(3):  # 2·3번째 호출이 속도 간격(0.1s)을 기다린다
            await client.get_json("/api/instance", {"domain_id": f"A-{d}"})
        return tools.ok(TOOL, [])

    await mgr.execute(TOOL, call, principal="chat", owner="user:a", on_job=owned.append)
    await client.aclose()
    record = owned[0].record()
    assert record["throttle_wait_ms"]["max"] >= 60
    assert record["throttle_wait_ms"]["total"] >= record["throttle_wait_ms"]["max"]
    assert set(record["slot_wait_ms"]) == {"total", "max"}
    with caplog.at_level(logging.INFO, logger="apm_gateway.audit"):
        audit_job_finished(owned[0])
    line = caplog.records[-1].getMessage()
    assert " waits=slot_ms:" in line and ",throttle_ms:" in line


async def test_gateway_health_reports_live_congestion(tmp_path):
    fake = FakeFleet(1, hosts=("apm.test",))
    cfg = load_config(fake.env(APM_SPOOL_DIR=str(tmp_path / "spool")), dotenv=None)
    tools = ApmTools(
        build_source_set(cfg, transport=httpx.MockTransport(fake.handler)), cfg, clock=lambda: NOW_S
    )
    expected = {
        "source_id": "default",
        "limit": 8,
        "per_caller_limit": 4,
        "in_flight": 0,
        "waiting": 0,
        "callers": 0,
    }
    assert (await tools.gateway_health())["congestion"] == [expected]
    slots = tools.sources.get("default").api.client._slots
    await slots.acquire(PRIORITY_INTERACTIVE, "A")
    cached = await tools.gateway_health()  # 캐시된 상태 행이어도 혼잡은 현재 값
    assert cached["congestion"] == [{**expected, "in_flight": 1, "callers": 1}]
    slots.release("A")


# ── ⑤ 다중 사용자 부하 ──────────────────────────────────────

USERS = 10
DOMAINS = 12
FAST, SLOW = 0.1, 0.3  # 실측 1s·3s의 1/10 — 속도 상한(5 → 50/s)·대기 상한(30 → 3s)도 같은 비율


def _load_env(fake: FakeFleet, tmp_path, **extra: str) -> dict[str, str]:
    return fake.env(
        APM_SPOOL_DIR=str(tmp_path / "spool"),
        JENNIFER_RATE_LIMIT_PER_SEC="50",
        JENNIFER_API_TIMEOUT_SECONDS="3",
        **extra,
    )


async def _load_run(tmp_path, **extra: str) -> dict[str, Any]:
    fake = FakeFleet(DOMAINS, hosts=("apm.test",))

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/realtime/instance":
            d = int(request.url.params.get("domain_id", "0"))
            await asyncio.sleep(SLOW if d % 3 == 0 else FAST)
        return await fake.handler(request)

    cfg = load_config(_load_env(fake, tmp_path, **extra), dotenv=None)
    tools = ApmTools(
        build_source_set(cfg, transport=httpx.MockTransport(handler)), cfg, clock=lambda: NOW_S
    )
    for src in tools.sources:
        await src.resolver.inventory()
    slots = tools.sources.get("default").api.client._slots
    # (호출자, 받기 전 쥔 수, 상한 아래인 다른 호출자의 대기자가 있었나)
    grants: list[tuple[str, int, bool]] = []
    take = slots._take

    def recording_take(caller: str) -> None:
        open_other = any(
            not w.exempt and w.caller != caller and slots.held_by(w.caller) < slots.per_caller
            for w in slots._waiters
        )
        grants.append((caller, slots.held_by(caller), open_other))
        take(caller)

    slots._take = recording_take  # type: ignore[method-assign]
    mgr = JobManager(
        Spool(cfg.jobs.spool_dir),
        cfg.jobs,
        envelope=tools.ok,
        error_envelope=tools.err,
        rate_per_sec=50,
        clock=lambda: NOW_S,
    )
    fleet = FleetTools(tools)

    def tool_of(user: int):
        if user % 2 == 0:  # 동시 경로(W2 — 도메인 12곳 실시간 조회를 자리 폭으로 겹친다)
            return lambda: fleet.ranking()
        return lambda: fleet.ranking(domain_id=user % DOMAINS + 1)  # 1건

    async def solo(user: int) -> dict[str, Any]:
        return await mgr.execute(TOOL, tool_of(user), principal="chat", owner=f"user:{user}")

    started = time.monotonic()
    results = await asyncio.gather(*(solo(u) for u in range(USERS)))
    elapsed = time.monotonic() - started
    first = {}
    for index, (caller, _, _) in enumerate(grants):
        first.setdefault(caller, index)
    return {
        "results": results,
        "grants": grants,
        "first_index": first,
        "cap": slots.per_caller,
        "limit": slots.limit,
        "elapsed": elapsed,
        "tool_of": tool_of,
        "mgr": mgr,
    }


async def test_multi_user_load_is_fair_and_matches_solo_envelopes(tmp_path):
    run = await _load_run(tmp_path)
    cap, limit = run["cap"], run["limit"]
    assert cap == 4 and len(run["first_index"]) == USERS
    # 굶주림 없음 — 모든 사용자의 첫 자리가 「자리 수 + 사용자 수」 넘김 안
    assert max(run["first_index"].values()) < limit + USERS
    # 호출자별 동시 ≤ 상한 — 상한 아래인 다른 호출자가 기다리는 동안에는 상한을 넘겨 주지 않는다
    assert not [g for g in run["grants"] if g[1] >= cap and g[2]]
    # 자리 대기 초과 0건 · 결과 봉투는 단독 실행과 같다
    assert all("error" not in r for r in run["results"]), [r.get("reason") for r in run["results"]]
    load_grants = len(run["grants"])
    for user, result in enumerate(run["results"]):
        alone = await run["mgr"].execute(TOOL, run["tool_of"](user), principal="solo")
        assert result == alone
    print(  # 보고용 수치(-s)
        f"\n[W3b load] users={USERS} grants={load_grants} elapsed={run['elapsed']:.2f}s "
        f"first_index_max={max(run['first_index'].values())} cap={cap} limit={limit}"
    )


async def test_multi_user_load_off_value_keeps_fifo_backlog(tmp_path):
    """대조 — 끄면(0) 종전 순서라 뒤에 온 사용자가 앞 사용자의 대기분 뒤에 선다."""
    on = await _load_run(tmp_path / "on")
    off = await _load_run(tmp_path / "off", JENNIFER_MAX_INFLIGHT_PER_CALLER="0")
    assert off["cap"] == 0 and all("error" not in r for r in off["results"])
    worst_on, worst_off = max(on["first_index"].values()), max(off["first_index"].values())
    print(f"\n[W3b load] first_index_max on={worst_on} off={worst_off}")
    assert worst_off > worst_on
