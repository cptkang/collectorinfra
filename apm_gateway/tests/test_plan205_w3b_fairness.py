"""plans/205 W3b — 다중 사용자 공정 동시성(D-356 ⑤).

- ① 순위가 공정 분배보다 앞선다(poller·interactive 우선 · 에이징 불변).
- ② 호출자 A가 동시 12건을 먼저 넣은 뒤 B가 1건 → B가 A의 남은 대기분보다 먼저 자리를 받는다.
- ③ 호출자 하나뿐이면 상한을 넘겨 자리를 다 쓴다(일 보존) · 취소 시 자리·호출자 카운터 누수 없음.
- ④ 작업 슬롯: owner A 작업 4개 + B 작업 1개 → B가 3번째 이내로 실행.
- ⑤ 다중 사용자 부하(시간 축척 0.1): 사용자 10명 × 도구 혼합(W2 동시 경로) × 승격 전·후.
- ⑥ 되돌리기 값 0 = 16ea44fa 기준선과 같은 대기자 선택 순서(녹화본 대조).
- 혼잡 가시화: 호출자 키·대기 시간 훅 위임 · 작업 기록·감사 `waits` · `gateway_health` 자리 현황 ·
  자리 대기 초과 사유의 「동시 사용 혼잡」.

외부 네트워크 0 — `httpx.MockTransport`만 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import statistics
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import test_plan205_w2b_fanout as w2b
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.adapters.throttle import PrioritySlots, _effective_rank
from apm_gateway.application.batch import _ItemScope, _Plan
from apm_gateway.application.fanout import _FanoutScope
from apm_gateway.application.jobs import JobManager, _Slots, caller_key
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_POLLER,
    CallScope,
    SharedLoadScope,
    current_scope,
    use_scope,
)
from apm_gateway.domain.errors import SOURCE_UNAVAILABLE, ApmError
from apm_gateway.interface.audit import audit
from apm_gateway.interface.server import _waits_text
from conftest import NOW_S, TOKEN

from apm_gateway.config import JenniferApiConfig, JobConfig, load_config


async def _settle(rounds: int = 5) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _grab(
    slots: PrioritySlots, order: list[str], label: str, priority: str, caller: str
) -> asyncio.Task[None]:
    async def run() -> None:
        await slots.acquire(priority, caller)
        order.append(label)

    return asyncio.create_task(run())


# ── ① 순위가 공정 분배보다 앞선다 ──────────────────────────────


async def test_priority_precedes_fairness_and_cap():
    """자리를 적게 쥔·상한 아래 호출자라도 순위가 낮으면 뒤다 — poller > interactive(상한에 닿은
    호출자여도) > background."""
    slots = PrioritySlots(4, aging_seconds=0, per_caller=2)
    for _ in range(4):
        await slots.acquire(PRIORITY_INTERACTIVE, "a")  # a가 4개(상한 2 초과 — 빠른 경로)
    order: list[str] = []
    tasks = [
        _grab(slots, order, "b-bg", PRIORITY_BACKGROUND, "b"),
        _grab(slots, order, "a-int", PRIORITY_INTERACTIVE, "a"),
        _grab(slots, order, "poll", PRIORITY_POLLER, ""),
    ]
    await _settle()
    for _ in range(3):
        slots.release("a")
        await _settle()
    await asyncio.gather(*tasks)
    assert order == ["poll", "a-int", "b-bg"]


async def test_aging_lifts_background_into_the_fair_tier():
    """에이징은 그대로다 — 오래 기다린 background가 interactive 무리로 올라오면 그 안에서 자리를
    적게 쥔 호출자가 먼저다."""
    clock = _Clock()
    slots = PrioritySlots(2, aging_seconds=1.0, per_caller=1, clock=clock)
    await slots.acquire(PRIORITY_INTERACTIVE, "a")
    await slots.acquire(PRIORITY_INTERACTIVE, "a")
    order: list[str] = []
    bg = _grab(slots, order, "b-bg", PRIORITY_BACKGROUND, "b")
    await _settle()
    clock.now = 1.5  # b는 한 단계 올라 interactive
    a_int = _grab(slots, order, "a-int", PRIORITY_INTERACTIVE, "a")
    await _settle()
    slots.release("a")
    await _settle()
    assert order == ["b-bg"]  # 같은 무리 — a는 1개를 쥐어 상한(1), b는 0개
    slots.release("a")
    await asyncio.gather(bg, a_int)
    assert order == ["b-bg", "a-int"]


# ── ② 늦게 온 호출자가 무거운 호출자의 남은 대기분을 앞선다 ──────────


async def _heavy_then_light(per_caller: int) -> list[str]:
    slots = PrioritySlots(8, aging_seconds=0, per_caller=per_caller)
    order: list[str] = []
    tasks = [_grab(slots, order, f"a{i}", PRIORITY_INTERACTIVE, "a") for i in range(12)]
    await _settle()
    tasks.append(_grab(slots, order, "b0", PRIORITY_INTERACTIVE, "b"))
    await _settle()
    assert order == [f"a{i}" for i in range(8)]  # 빈 자리 8개는 바로(상한 무관)
    for _ in range(5):
        slots.release("a")  # a의 자리 하나가 끝날 때마다 다음 대기자에게
        await _settle()
    await asyncio.gather(*tasks)
    return order[8:]


async def test_late_caller_gets_next_slot_before_heavy_backlog():
    assert (await _heavy_then_light(4))[0] == "b0"
    # 되돌리기(0) — 먼저 온 순서: b는 a의 남은 4건 뒤
    assert (await _heavy_then_light(0)) == ["a8", "a9", "a10", "a11", "b0"]


# ── ③ 일 보존 · 취소 누수 없음 ─────────────────────────────────


async def test_single_caller_uses_every_slot_beyond_cap():
    slots = PrioritySlots(8, aging_seconds=0, per_caller=4)
    order: list[str] = []
    tasks = [_grab(slots, order, f"a{i}", PRIORITY_INTERACTIVE, "a") for i in range(12)]
    await _settle()
    assert len(order) == 8 and slots.in_flight == 8 and slots.callers == 1
    for _ in range(4):
        slots.release("a")  # 다른 호출자가 없으면 상한(4)을 넘긴 a에게 바로 넘긴다
        await _settle()
    assert len(order) == 12 and slots.in_flight == 8 and slots.waiting == 0
    await asyncio.gather(*tasks)


async def test_all_capped_waiters_are_still_served():
    """무리 전원이 상한이면 그중 가장 적게 쥔 호출자가 받는다 — 자리가 남는데 아무도 못 받는 상태
    없음."""
    slots = PrioritySlots(5, aging_seconds=0, per_caller=1)
    for caller in ("a", "a", "a", "b", "b"):
        await slots.acquire(PRIORITY_INTERACTIVE, caller)
    order: list[str] = []
    tasks = [
        _grab(slots, order, "a-next", PRIORITY_INTERACTIVE, "a"),
        _grab(slots, order, "b-next", PRIORITY_INTERACTIVE, "b"),
    ]
    await _settle()
    slots.release("b")  # a 3 · b 1 — 둘 다 상한(1) → 적게 쥔 b
    await _settle()
    assert order == ["b-next"] and slots.in_flight == 5
    slots.release("a")
    await asyncio.gather(*tasks)
    assert order == ["b-next", "a-next"]


async def test_cancel_returns_slot_and_caller_counters_do_not_leak():
    slots = PrioritySlots(1, aging_seconds=0, per_caller=1)
    await slots.acquire(PRIORITY_INTERACTIVE, "a")
    waiting = asyncio.create_task(slots.acquire(PRIORITY_INTERACTIVE, "b"))
    handed = asyncio.create_task(slots.acquire(PRIORITY_INTERACTIVE, "c"))
    await _settle()
    waiting.cancel()  # 대기 중 취소
    await asyncio.gather(waiting, return_exceptions=True)
    slots.release("a")  # c에게 넘긴다
    handed.cancel()  # 넘겨받은 직후 취소 → 돌려준다
    await asyncio.gather(handed, return_exceptions=True)
    assert slots.in_flight == 0 and slots.waiting == 0 and slots.callers == 0
    assert slots._by_caller == {}


# ── ④ 작업 슬롯 공정 분배 ──────────────────────────────────────


def _manager(tmp_path: Path, size: int, per_owner: int) -> JobManager:
    jcfg = JobConfig(
        spool_dir=tmp_path / f"spool-{uuid.uuid4().hex[:6]}",
        max_concurrent=size,
        max_concurrent_per_owner=per_owner,
    )
    return JobManager(
        Spool(jcfg.spool_dir),
        jcfg,
        envelope=lambda tool, rows, **kw: {"rows": rows, **kw},
        error_envelope=lambda tool, code, reason: {"error": code, "reason": reason},
    )


async def _job_start_order(
    tmp_path: Path, size: int, per_owner: int, a_jobs: int = 4
) -> list[str]:
    """owner A 작업 `a_jobs`개를 먼저, B 작업 1개를 뒤에 승격 → 슬롯을 받은(승격 뒤 첫 호출을
    시작한) 순서. 작업은 승격 전에 첫 단계를 돌므로 모두 승격된 뒤(`go`) 호출을 시작한다."""
    mgr = _manager(tmp_path, size, per_owner)
    started: list[str] = []
    gates: dict[str, asyncio.Event] = {}
    go = asyncio.Event()
    jobs: list[Any] = []

    def job(name: str) -> Callable[[], Awaitable[dict[str, Any]]]:
        gates[name] = asyncio.Event()

        async def run() -> dict[str, Any]:
            await go.wait()
            await current_scope().before_call("s")
            started.append(name)
            await gates[name].wait()
            return {"rows": []}

        return run

    order = [(f"a{i}", "user:a") for i in range(1, a_jobs + 1)] + [("b1", "user:b")]
    for name, owner in order:
        await mgr.execute(
            name, job(name), principal="chat", owner=owner, wait_seconds=0, on_job=jobs.append
        )
        await _settle()
    go.set()
    await _settle(10)
    while len(started) < len(order):
        await _settle()
        running = [n for n in started if not gates[n].is_set()]
        gates[running[0]].set()  # 먼저 시작한 작업부터 끝낸다
        await _settle(10)
    for gate in gates.values():
        gate.set()
    await asyncio.gather(*(j.task for j in jobs))
    assert mgr.summary()["slots_in_use"] == 0 and mgr._slots._running == {}
    await mgr.aclose()
    return started


async def test_owner_fair_job_slots_start_late_owner_within_third(tmp_path):
    """전역 슬롯 2 · owner 상한 2: A 둘이 실행 중이면 A는 상한 — 다음 슬롯은 B(3번째). 전역 4면
    A 4개가 대기 없이 바로 받으므로(일 보존 — 빈 슬롯은 대기가 없을 때만 생긴다) 3번째를 시험하려면
    전역 슬롯이 owner 상한보다 커서는 안 된다. 되돌리기(0)는 FIFO(B 5번째)."""
    fair = await _job_start_order(tmp_path, 2, 2)
    assert fair.index("b1") <= 2, fair
    fifo = await _job_start_order(tmp_path, 2, 0)
    assert fifo == ["a1", "a2", "a3", "a4", "b1"]


async def test_default_owner_cap_lets_late_owner_go_before_next_a_job(tmp_path):
    """기본값(전역 4 · owner 상한 4 = 전역 칸 수 — 사용자 결정 2026-11-02): A가 4칸을 다 쥔 상태에서
    A 다섯째와 B가 기다리면, 칸이 비었을 때 실행 중 작업이 적은 B가 A의 다음 작업보다 먼저 받는다.
    되돌리기(0)는 FIFO(A 다섯째가 먼저)."""
    defaults = JobConfig()
    assert (defaults.max_concurrent, defaults.max_concurrent_per_owner) == (4, 4)
    fair = await _job_start_order(tmp_path, 4, defaults.max_concurrent_per_owner, a_jobs=5)
    assert fair[:4] == ["a1", "a2", "a3", "a4"] and fair[4] == "b1", fair
    fifo = await _job_start_order(tmp_path, 4, 0, a_jobs=5)
    assert fifo == ["a1", "a2", "a3", "a4", "a5", "b1"]


async def test_single_owner_job_slots_work_conserving(tmp_path):
    """owner 하나뿐이면 상한(2)을 넘겨 전역 슬롯을 다 쓴다."""
    mgr = _manager(tmp_path, 4, 2)
    gate = asyncio.Event()
    jobs: list[Any] = []

    async def run() -> dict[str, Any]:
        await go.wait()
        await current_scope().before_call("s")
        await gate.wait()
        return {"rows": []}

    go = asyncio.Event()
    for i in range(4):
        await mgr.execute(
            f"a{i}", run, principal="chat", owner="user:a", wait_seconds=0, on_job=jobs.append
        )
    go.set()
    await _settle(10)
    assert mgr.summary()["slots_in_use"] == 4 and mgr.summary()["queued"] == 0
    gate.set()
    await asyncio.gather(*(j.task for j in jobs))
    await mgr.aclose()


async def test_owner_slot_cancel_does_not_leak(tmp_path):
    slots = _Slots(1, per_owner=1)
    assert slots.try_acquire("a")
    waiting = asyncio.create_task(slots.acquire("b"))
    handed = asyncio.create_task(slots.acquire("c"))
    await _settle()
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)
    slots.release("a")
    handed.cancel()
    await asyncio.gather(handed, return_exceptions=True)
    assert slots.in_use == 0 and slots._running == {} and not slots._waiters


# ── ⑤ 다중 사용자 부하(시간 축척) ──────────────────────────────

LOAD_SIDS = ("s1", "s2")
USERS = 10
SLOTS = 8
Call = Callable[[ApmTools], Awaitable[dict[str, Any]]]


def _active(t: ApmTools) -> Awaitable[dict[str, Any]]:
    return t.apm_active_services(hostname=w2b.HOST)


# W2 동시 경로(인스턴스 시계열 · 기간 비교 · 소스 변경 · 군 순위(기간·지금) · 액티브 서비스)
TOOLS: tuple[tuple[str, Call], ...] = (
    ("series", w2b.inst_series),
    ("period", w2b.period),
    ("changes", w2b.changes),
    ("fleet_period", w2b.fleet_period),
    ("active", _active),
    ("fleet_now", w2b.fleet_now),
)


class _LoadFake(w2b.Fake):
    """w2b 합성 응답 · 고정 지연. HTTP 시작마다 호출 맥락의 호출자 키로 첫 시작 시각과 (소스,
    호출자)별 동시 처리 수를 잰다."""

    def __init__(self, delay: float, clock: Callable[[], float]) -> None:
        super().__init__()
        self.delay = delay
        self.clock = clock
        self.first_start: dict[str, float] = {}
        self.live: Counter[tuple[str, str]] = Counter()
        self.caller_peak: Counter[tuple[str, str]] = Counter()

    async def handler(self, request: httpx.Request) -> httpx.Response:
        sid = request.url.host.split(".")[0]
        path = request.url.path
        q = dict(request.url.params)
        d: int | None = int(q["domain_id"]) if "domain_id" in q else None
        if path.startswith("/api-v2/deploy/"):
            d = int(path.rsplit("/", 1)[1])
            path = "/api-v2/deploy"
        key = (sid, current_scope().caller)
        self.first_start.setdefault(key[1], self.clock())
        self.live[key] += 1
        self.caller_peak[key] = max(self.caller_peak[key], self.live[key])
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return self._answer(sid, d, path, q)
        finally:
            self.live[key] -= 1


def _load_env(spool: Path, *, scale: float, fanout: int, fair: bool) -> dict[str, str]:
    env = {
        "JENNIFER_SOURCES": json.dumps(list(LOAD_SIDS)),
        "JENNIFER_RATE_LIMIT_PER_SEC": str(5 / scale),
        "JENNIFER_API_TIMEOUT_SECONDS": str(30 * scale),
        "JENNIFER_MAX_INFLIGHT_PER_SOURCE": str(SLOTS),
        "APM_JOB_MAX_CONCURRENT": "4",
        "APM_PRIORITY_AGING_SECONDS": str(10 * scale),
        "APM_PARSE_WORKERS": "0",
        "APM_FANOUT_CONCURRENCY": str(fanout),
        "APM_SPOOL_DIR": str(spool),
    }
    if not fair:  # 공정 분배 끔(되돌리기 값) — 켜짐은 기본값(호출자 상한 자리//2 = 4 · owner 2)
        env["JENNIFER_MAX_INFLIGHT_PER_CALLER"] = "0"
        env["APM_JOB_MAX_CONCURRENT_PER_OWNER"] = "0"
    for sid in LOAD_SIDS:
        env[f"JENNIFER_{sid.upper()}_API_URL"] = f"http://{sid}.test"
        env[f"JENNIFER_{sid.upper()}_API_TOKEN"] = f"{TOKEN}-{sid}"
    return env


def _plan(u: int) -> list[tuple[str, Call, bool]]:
    """사용자 u의 동시 작업 2개 — (도구 이름, 호출, 승격 여부). 첫 작업은 승격 전(interactive),
    둘째는 홀수 사용자만 바로 승격(background · 작업 슬롯 대기)."""
    first, second = TOOLS[u % len(TOOLS)], TOOLS[(u + 3) % len(TOOLS)]
    return [(first[0], first[1], False), (second[0], second[1], bool(u % 2))]


def _strip(out: dict[str, Any]) -> dict[str, Any]:
    """작업 고유 칸(작업 핸들·결과 파일 참조 — 작업 ID·시각)을 뺀 봉투."""
    return {k: v for k, v in out.items() if k not in ("job", "artifact")}


async def _run_in_job(
    mgr: JobManager, tools: ApmTools, name: str, call: Call, promoted: bool, owner: str
) -> tuple[dict[str, Any], Any]:
    holder: list[Any] = []

    async def guarded() -> dict[str, Any]:
        try:
            return await call(tools)
        except ApmError as e:
            return tools.err(name, e.code, e.reason)

    out = await mgr.execute(
        name,
        guarded,
        principal="chat",
        owner=owner,
        wait_seconds=0 if promoted else None,
        on_job=holder.append,
    )
    job = holder[0]
    if promoted:
        out = await job.task
    return out, job


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


async def standalone_envelopes(tmp_path: Path) -> dict[tuple[str, bool], dict[str, Any]]:
    """도구·승격 여부별 단독 실행 봉투(지연 0 · 경합 없음 · 캐시 데운 뒤)."""
    out: dict[tuple[str, bool], dict[str, Any]] = {}
    for name, call in TOOLS:
        for promoted in (False, True):
            env = _load_env(
                tmp_path / f"solo-{uuid.uuid4().hex[:6]}", scale=1.0, fanout=4, fair=True
            )
            env["JENNIFER_RATE_LIMIT_PER_SEC"] = "0"  # 단독 — 속도 제어 없이(봉투만 본다)
            cfg = load_config(env)
            fake = _LoadFake(0.0, lambda: 0.0)
            tools = ApmTools(
                build_source_set(cfg, transport=httpx.MockTransport(fake.handler)),
                cfg,
                clock=lambda: NOW_S,
            )
            await call(tools)  # 캐시(인스턴스 명단·지표 카탈로그)를 부하 실행과 같게 데운다
            mgr = JobManager.from_config(cfg, envelope=tools.ok, error_envelope=tools.err)
            got, _ = await _run_in_job(mgr, tools, name, call, promoted, "user:solo")
            out[(name, promoted)] = _strip(got)
            await mgr.aclose()
    return out


async def run_load(
    tmp_path: Path,
    *,
    delay: float,
    scale: float = 0.1,
    fanout: int = 4,
    fair: bool = True,
    users: int = USERS,
) -> dict[str, Any]:
    """사용자 `users`명이 동시에 작업 2개씩(승격 전·후 섞기)을 낸다 — 자리 8 · 초당 5/`scale` ·
    timeout 30×`scale`s · 응답 지연 `delay`s. 지표: 최대 자리 대기(s) · 자리 대기 초과 건수 · 상한
    위반 배정 수 · 전체 소요 · 사용자별 완료 시간 p50/p95/최대 · 사용자별 첫 HTTP 시작 최대 ·
    (소스, 호출자)별 최대 동시 · 봉투(사용자, 도구, 승격 여부, 봉투)."""
    cfg = load_config(
        _load_env(tmp_path / f"load-{uuid.uuid4().hex[:6]}", scale=scale, fanout=fanout, fair=fair)
    )
    loop = asyncio.get_running_loop()
    fake = _LoadFake(delay, loop.time)
    tools = ApmTools(
        build_source_set(cfg, transport=httpx.MockTransport(fake.handler)),
        cfg,
        clock=lambda: NOW_S,
    )
    # 캐시(인스턴스 명단·지표 카탈로그)를 데운다 — 공유 적재는 사용자 몫이 아니다
    await asyncio.gather(*(call(tools) for _, call in TOOLS))
    fake.first_start.clear()
    fake.caller_peak.clear()
    waits: list[float] = []
    stats = {"over": 0, "violations": 0}
    cap = cfg.sources[0].max_inflight_per_caller if fair else 0
    for src in tools.sources:
        slots: PrioritySlots = src.api.client._slots
        assert slots.per_caller == cap
        original = slots.acquire
        pick = slots._pick

        async def timed(
            priority: str = PRIORITY_INTERACTIVE,
            caller: str = "",
            _orig: Callable[..., Awaitable[None]] = original,
        ) -> None:
            started = loop.time()
            try:
                await _orig(priority, caller)
            except asyncio.CancelledError:  # wait_for 시간 초과(이 부하에는 다른 취소가 없다)
                stats["over"] += 1
                raise
            finally:
                waits.append(loop.time() - started)

        def checked(now: float, _s: PrioritySlots = slots, _pick: Any = pick) -> Any:
            """배정 직전 검사 — 상한에 닿은 호출자에게 주는데 같은 유효 순위에 받을 수 있는 다른
            호출자의 대기자가 있으면 위반."""
            chosen = _pick(now)
            if _s.per_caller and _s._capped(chosen):
                tier = _effective_rank(chosen, now, _s._aging)
                if any(
                    w.caller != chosen.caller
                    and not _s._capped(w)
                    and _effective_rank(w, now, _s._aging) == tier
                    for w in _s._waiters
                ):
                    stats["violations"] += 1
            return chosen

        slots.acquire = timed  # type: ignore[method-assign]
        slots._pick = checked  # type: ignore[method-assign]
    mgr = JobManager.from_config(cfg, envelope=tools.ok, error_envelope=tools.err)
    done: dict[str, float] = {}
    envelopes: list[tuple[int, str, bool, dict[str, Any]]] = []
    jobs: list[Any] = []
    t0 = loop.time()

    async def user(u: int) -> None:
        owner = f"user:u{u}"
        outs = await asyncio.gather(
            *(_run_in_job(mgr, tools, n, c, p, owner) for n, c, p in _plan(u))
        )
        done[caller_key("chat", owner)] = loop.time() - t0
        for (name, _, promoted), (out, job) in zip(_plan(u), outs, strict=True):
            envelopes.append((u, name, promoted, out))
            jobs.append(job)

    await asyncio.gather(*(user(u) for u in range(users)))
    elapsed = loop.time() - t0
    await mgr.aclose()
    finish = list(done.values())
    starts = {k: v - t0 for k, v in fake.first_start.items() if k}
    return {
        "max_slot_wait": max(waits, default=0.0),
        "max_job_slot_wait_ms": max(j.slot_wait_max_ms for j in jobs),
        "over": stats["over"],
        "violations": stats["violations"],
        "elapsed": elapsed,
        "done_p50": statistics.median(finish),
        "done_p95": _pct(finish, 0.95),
        "done_max": max(finish),
        "first_start": starts,
        "first_start_max": max(starts.values()),
        "caller_peak_max": max(
            (n for (_, caller), n in fake.caller_peak.items() if caller), default=0
        ),
        "cap": cap,
        "envelopes": envelopes,
        "calls": sum(src.api.calls_total for src in tools.sources),
    }


def fair_share_bound(delay: float, scale: float = 0.1, users: int = USERS) -> float:
    """굶주림 없음 기준(s) — 사용자가 모두 같은 순간 들어오면 소스 하나에서 첫 자리 8개(빠른 경로)
    다음 자리는 자리를 0개 쥔 호출자에게 먼저 가므로, 어느 사용자든 그 소스의 처음
    `자리 + 사용자 수`건 안에 첫 자리를 받는다. 처리율 R = min(자리 / 지연, 초당 상한)이면 그
    시점은 (자리 + 사용자 수) / R이고, 자리를 받은 뒤 속도 제어·응답 여유로 지연 2회를 더한다."""
    rate = min(SLOTS / delay, 5 / scale)
    return (SLOTS + users) / rate + 2 * delay


@pytest.mark.parametrize("delay", [0.1, 0.3])
async def test_multi_user_load_is_fair_and_never_times_out(tmp_path, delay):
    """사용자 10명 × 작업 2개(W2 동시 경로 도구 혼합 · 승격 전·후) — 지연 1s·3s를 축척 0.1로.

    굶주림 없음: 사용자마다 첫 HTTP 시작 ≤ `fair_share_bound`(지연 0.1 → 0.56s · 0.3 → 1.28s).
    다른 호출자가 기다리는 동안 상한에 닿은 호출자에게 자리를 주지 않는다(배정 검사 위반 0).
    자리 대기 초과 0건 · 봉투는 단독 실행과 같다."""
    solo = await standalone_envelopes(tmp_path)
    result = await run_load(tmp_path, delay=delay)
    report = {k: v for k, v in result.items() if k not in ("envelopes", "first_start")}
    print(f"\nW3b 부하(지연 {delay}s · 축척 0.1): {report}")
    assert result["cap"] == 4
    assert result["over"] == 0
    assert result["max_slot_wait"] < 3.0
    assert result["violations"] == 0
    assert len(result["first_start"]) == USERS
    assert result["first_start_max"] <= fair_share_bound(delay), result["first_start"]
    for u, name, promoted, out in result["envelopes"]:
        assert "error" not in out, (u, name, out)
        assert _strip(out) == solo[(name, promoted)], (u, name, promoted)


# ── ⑥ 되돌리기 값 0 = 16ea44fa 기준선 순서 ─────────────────────

# 16ea44fa의 `PrioritySlots`·`_Slots`로 아래 시나리오를 돌려 녹화한 배정 순서(라벨 = 단계 번호)
# fmt: off
SLOTS_7 = [3, 4, 5, 10, 13, 17, 18, 20, 21, 24, 25, 28, 30, 31, 37, 39, 33, 38, 42, 43, 45, 50, 51, 52, 54, 66, 74, 76, 79, 80, 81, 89, 90, 92, 95, 96, 99, 101, 103, 107, 108, 111, 112, 113, 116, 120, 124, 127, 126, 134, 135, 136, 139, 141, 142, 143, 145, 151, 159, 162, 166, 169, 170, 174, 176, 178, 179, 183, 186, 189, 190, 191, 193, 197, 198, 199, 200, 203, 205, 206, 207]  # noqa: E501
SLOTS_205 = [0, 2, 8, 9, 11, 13, 18, 19, 20, 23, 22, 27, 29, 35, 36, 39, 37, 48, 49, 50, 51, 52, 54, 55, 59, 61, 62, 63, 65, 67, 68, 75, 79, 81, 82, 84, 86, 89, 90, 91, 93, 94, 95, 96, 99, 100, 102, 106, 109, 116, 121, 123, 127, 131, 134, 136, 139, 141, 143, 146, 148, 150, 156, 160, 166, 172, 173, 179, 180, 183, 187, 188, 189, 194, 199, 204, 205, 207, 208, 209, 211, 215, 217, 218, 219]  # noqa: E501
JOBS_7 = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 17, 19, 20, 25, 27, 31, 32, 35, 42, 45, 47, 48, 49, 50, 51, 56, 57, 60, 61, 62, 64, 68, 70, 72, 75, 76, 79, 83, 85, 86, 89, 90, 91, 97, 102, 103, 105, 107, 109, 110, 111, 114, 116, 118, 119, 120, 121, 125, 129, 133, 147, 153, 157, 160, 165, 168, 173, 176, 177, 180, 181, 185, 189, 191, 195, 196, 199, 201, 202, 210]  # noqa: E501
JOBS_205 = [0, 1, 2, 3, 4, 6, 13, 15, 18, 20, 21, 22, 33, 34, 36, 37, 38, 39, 41, 42, 43, 44, 48, 50, 54, 56, 57, 60, 62, 64, 65, 67, 69, 74, 75, 78, 86, 88, 89, 90, 92, 93, 94, 95, 96, 98, 99, 101, 103, 104, 106, 113, 114, 116, 119, 121, 125, 127, 129, 130, 137, 138, 142, 143, 144, 145, 149, 151, 154, 159, 162, 168, 169, 170, 172, 176, 179]  # noqa: E501
# fmt: on
_PRIOS = (PRIORITY_POLLER, PRIORITY_INTERACTIVE, PRIORITY_BACKGROUND)
_CALLERS = ("", "a", "b", "c")


async def _slots_scenario(slots: PrioritySlots, clock: _Clock, seed: int) -> list[int]:
    """무작위(시드 고정) 획득·반납·취소 240단계 — 가짜 시계로 에이징을 섞는다. 녹화 스크립트와
    같은 난수 소비 순서다(호출자 키는 기준선에서 무시됐다)."""
    rnd = random.Random(seed)
    granted: list[int] = []
    held: list[tuple[int, str]] = []
    pending: dict[int, asyncio.Task[None]] = {}

    async def take(label: int, prio: str, caller: str) -> None:
        await slots.acquire(prio, caller)
        granted.append(label)
        held.append((label, caller))

    for step in range(240):
        clock.now += rnd.choice((0.0, 0.05, 0.3, 0.7))
        op = rnd.random()
        if op < 0.5:
            prio, caller = rnd.choice(_PRIOS), rnd.choice(_CALLERS)
            pending[step] = asyncio.create_task(take(step, prio, caller))
        elif op < 0.88:
            if held:
                _, caller = held.pop(rnd.randrange(len(held)))
                slots.release(caller)
        else:
            live = [k for k, t in pending.items() if not t.done()]
            if live:
                pending[rnd.choice(live)].cancel()
        for _ in range(3):
            await asyncio.sleep(0)
    for task in pending.values():
        task.cancel()
    await asyncio.gather(*pending.values(), return_exceptions=True)
    return granted


async def _jobs_scenario(slots: _Slots, seed: int) -> list[int]:
    rnd = random.Random(seed)
    granted: list[int] = []
    held: list[tuple[int, str]] = []
    pending: dict[int, asyncio.Task[None]] = {}

    async def take(label: int, key: str) -> None:
        await slots.acquire(key)
        granted.append(label)
        held.append((label, key))

    for step in range(240):
        op = rnd.random()
        if op < 0.5:
            pending[step] = asyncio.create_task(take(step, rnd.choice(_CALLERS[1:])))
        elif op < 0.88:
            if held:
                _, key = held.pop(rnd.randrange(len(held)))
                slots.release(key)
        else:
            live = [k for k, t in pending.items() if not t.done()]
            if live:
                pending[rnd.choice(live)].cancel()
        for _ in range(3):
            await asyncio.sleep(0)
    for task in pending.values():
        task.cancel()
    await asyncio.gather(*pending.values(), return_exceptions=True)
    return granted


@pytest.mark.parametrize(("seed", "expected"), [(7, SLOTS_7), (205, SLOTS_205)])
async def test_per_caller_zero_keeps_baseline_slot_order(seed, expected):
    clock = _Clock()
    slots = PrioritySlots(3, aging_seconds=1.0, per_caller=0, clock=clock)
    assert await _slots_scenario(slots, clock, seed) == expected


@pytest.mark.parametrize("seed", [7, 205])
async def test_per_caller_on_changes_the_same_scenario(seed):
    """대조 — 같은 시나리오에서 공정 분배를 켜면 순서가 실제로 달라진다.

    시나리오가 실제로 갈림길을 지난다는 확인이다."""
    clock = _Clock()
    slots = PrioritySlots(3, aging_seconds=1.0, per_caller=1, clock=clock)
    assert await _slots_scenario(slots, clock, seed) != {7: SLOTS_7, 205: SLOTS_205}[seed]


@pytest.mark.parametrize(("seed", "expected"), [(7, JOBS_7), (205, JOBS_205)])
async def test_per_owner_zero_keeps_baseline_fifo(seed, expected):
    assert await _jobs_scenario(_Slots(2, per_owner=0), seed) == expected


# ── 설정 ──────────────────────────────────────────────────────


def _multi(**extra: str) -> dict[str, str]:
    return {
        "JENNIFER_SOURCES": '["bank","common"]',
        "JENNIFER_BANK_API_URL": "http://bank.test",
        "JENNIFER_BANK_API_TOKEN": "tok-bank",
        "JENNIFER_COMMON_API_URL": "http://common.test",
        "JENNIFER_COMMON_API_TOKEN": "tok-common",
        **extra,
    }


def test_config_defaults_inheritance_and_zero():
    cfg = load_config({})
    assert cfg.jennifer.max_inflight_per_caller == 4  # 자리 8 // 2
    assert cfg.jobs.max_concurrent_per_owner == 4  # 전역 칸 수(사용자 결정 2026-11-02)
    assert (
        load_config({"JENNIFER_MAX_INFLIGHT_PER_SOURCE": "1"}).jennifer.max_inflight_per_caller == 1
    )
    off = load_config(
        {"JENNIFER_MAX_INFLIGHT_PER_CALLER": "0", "APM_JOB_MAX_CONCURRENT_PER_OWNER": "0"}
    )
    assert off.jennifer.max_inflight_per_caller == 0 and off.jobs.max_concurrent_per_owner == 0
    # 전역 미설정 — 소스마다 자기 자리 // 2
    auto = load_config(_multi(JENNIFER_BANK_MAX_INFLIGHT_PER_SOURCE="6"))
    assert [s.max_inflight_per_caller for s in auto.sources] == [3, 4]
    # 전역 설정 — 승계 · 소스 키가 앞선다(0 = 그 소스만 끔)
    inherit = load_config(
        _multi(JENNIFER_MAX_INFLIGHT_PER_CALLER="2", JENNIFER_COMMON_MAX_INFLIGHT_PER_CALLER="0")
    )
    assert [s.max_inflight_per_caller for s in inherit.sources] == [2, 0]
    assert JenniferApiConfig(max_inflight_per_source=5).max_inflight_per_caller == 2


@pytest.mark.parametrize(
    ("env", "key"),
    [
        ({"JENNIFER_MAX_INFLIGHT_PER_CALLER": "-1"}, "JENNIFER_MAX_INFLIGHT_PER_CALLER"),
        ({"JENNIFER_MAX_INFLIGHT_PER_CALLER": "2.5"}, "JENNIFER_MAX_INFLIGHT_PER_CALLER"),
        (
            _multi(JENNIFER_BANK_MAX_INFLIGHT_PER_CALLER="x"),
            "JENNIFER_BANK_MAX_INFLIGHT_PER_CALLER",
        ),
        ({"APM_JOB_MAX_CONCURRENT_PER_OWNER": "-2"}, "APM_JOB_MAX_CONCURRENT_PER_OWNER"),
    ],
)
def test_config_rejects_bad_values_with_key_name(env, key):
    with pytest.raises(ValueError, match=key):
        load_config(env)


def test_limits_are_wired_into_slots(tmp_path):
    client = JenniferClient(JenniferApiConfig(url="http://apm.test", max_inflight_per_caller=0))
    assert client._slots.per_caller == 0
    assert JenniferClient(JenniferApiConfig(url="http://apm.test"))._slots.per_caller == 4
    assert _manager(tmp_path, 4, 3)._slots.per_owner == 3


# ── 호출자 키 · 대기 시간 훅 · 가시화 ──────────────────────────


class _Outer(CallScope):
    def __init__(self) -> None:
        super().__init__()
        self.waits: list[tuple[str, float, float]] = []

    @property
    def caller(self) -> str:
        return "chat|user:kim"

    def after_wait(self, source_id: str, *, slot_ms: float, throttle_ms: float) -> None:
        self.waits.append((source_id, slot_ms, throttle_ms))


def test_scopes_forward_caller_and_waits():
    """항목·배치 항목 맥락은 바깥 호출자 키·대기 훅을 그대로, 공유 적재는 키 없음(`""`)·대기는
    시작한 요청에."""
    outer = _Outer()
    assert CallScope().caller == ""
    for scope in (_FanoutScope(outer), _ItemScope(_Plan(outer, 1), 0)):
        assert scope.caller == "chat|user:kim"
        scope.after_wait("s1", slot_ms=3.0, throttle_ms=1.0)
    shared = SharedLoadScope(outer)
    assert shared.caller == ""
    shared.after_wait("s1", slot_ms=5.0, throttle_ms=0.0)
    assert outer.waits == [("s1", 3.0, 1.0), ("s1", 3.0, 1.0), ("s1", 5.0, 0.0)]


async def test_job_caller_key_waits_record_and_audit(tmp_path, caplog):
    mgr = _manager(tmp_path, 4, 2)
    jobs: list[Any] = []

    async def run() -> dict[str, Any]:
        scope = current_scope()
        scope.after_wait("s1", slot_ms=10.0, throttle_ms=5.0)
        scope.after_wait("s1", slot_ms=30.0, throttle_ms=1.0)
        return {"rows": []}

    await mgr.execute("t", run, principal="chat", owner="user:kim", on_job=jobs.append)
    await mgr.execute("t", run, principal="chat", on_job=jobs.append)
    assert [j.caller for j in jobs] == ["chat|user:kim", "chat"]
    assert caller_key("default", None) == "default"
    assert jobs[0].record()["waits"] == {
        "calls": 2,
        "slot_ms_total": 40.0,
        "slot_ms_max": 30.0,
        "throttle_ms_total": 6.0,
        "throttle_ms_max": 5.0,
    }
    assert _waits_text(jobs[0]) == "slot:40/30,throttle:6/5"
    jobs[1].waited_calls = 0
    assert _waits_text(jobs[1]) is None  # 대기를 잰 호출이 없으면 칸 없음
    with caplog.at_level(logging.INFO, logger="apm_gateway.audit"):
        audit("t", "x", 1.0, waits=_waits_text(jobs[0]), search="exact(exact:1)")
        audit("t", "x", 1.0, error="e", waits=_waits_text(jobs[0]))
        audit("t", "x", 1.0)
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0].endswith(" waits=slot:40/30,throttle:6/5 search=exact(exact:1)")
    assert lines[1].endswith(" job_id=- waits=slot:40/30,throttle:6/5")
    assert "waits=" not in lines[2] and lines[2].endswith("job_id=-")
    await mgr.aclose()


async def test_client_passes_caller_reports_waits_and_congestion_reason():
    seen: list[dict[str, int]] = []
    release = asyncio.Event()
    client: JenniferClient

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(client._slots._by_caller))
        if request.url.path == "/api/domain":
            await release.wait()
        return httpx.Response(200, json={"result": []})

    client = JenniferClient(
        JenniferApiConfig(
            url="http://apm.test",
            token="tok-w3b",
            timeout_seconds=0.2,
            rate_limit_per_sec=0,
            max_inflight_per_source=1,
        ),
        transport=httpx.MockTransport(handler),
    )
    outer = _Outer()
    with use_scope(outer):
        holder = asyncio.create_task(client.get_json("/api/domain"))  # 자리 1개를 쥔다
        await _settle(20)
        waiter = asyncio.create_task(client._slots.acquire(PRIORITY_INTERACTIVE, "other"))
        await _settle()
        with pytest.raises(ApmError) as info:
            await client.get_json("/api/instance", {"domain_id": 7})
    assert info.value.code == SOURCE_UNAVAILABLE
    reason = info.value.reason
    assert "제니퍼 동시 호출 상한 대기 초과(1건 · 0.2s)" in reason
    assert "동시 사용 혼잡(대기 1건 · 호출자 1)" in reason and "/api/instance" in reason
    assert outer.waits[-1][0] == "default" and outer.waits[-1][2] == 0.0
    assert outer.waits[-1][1] >= 150  # 자리 대기 ≈ timeout(ms)
    release.set()
    await holder
    await waiter
    client._slots.release("other")
    assert seen[0] == {"chat|user:kim": 1}  # HTTP 중 자리는 호출 맥락의 호출자 키로 잡혔다
    assert len(outer.waits) == 2  # 받은 호출 1 + 넘친 호출 1
    assert client.slot_status() == {"in_flight": 0, "waiting": 0, "callers": 0}
    await client.aclose()


async def test_gateway_health_reports_live_slot_status_per_source():
    fake = w2b.Fake(step=0.0)
    tools = w2b._tools(fake, 4)
    first = next(iter(tools.sources))
    await first.api.client._slots.acquire(PRIORITY_INTERACTIVE, "chat|user:kim")
    health = await tools.gateway_health()
    rows = {r["source_id"]: r for r in health["rows"]}
    assert {k: rows[first.source_id][k] for k in ("in_flight", "waiting", "callers")} == {
        "in_flight": 1,
        "waiting": 0,
        "callers": 1,
    }
    assert all(r["in_flight"] == 0 for sid, r in rows.items() if sid != first.source_id)
    first.api.client._slots.release("chat|user:kim")
    cached = await tools.gateway_health()  # 캐시 안에서도 자리 현황은 새로 읽는다
    assert cached["rows"][0]["in_flight"] == 0 and cached["rows"][0]["callers"] == 0
