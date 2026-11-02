"""plans/205 독립 검증(verifier) — 수용 기준 보강 · 결함 재현 (D-356 · D-358 ② · D-296 ③ · D-347 ③).

- W1 프로세스 풀: spawn 워커 import 표면 · 워커 오류 사슬(원격 traceback 포함)에 원문 없음 ·
  파싱 중 취소 → 스풀 파일 삭제 · 풀 정상 유지 · 종료 `shutdown(wait=True)`.
- W2 순서: 여러 소스가 섞인 입력에서 메모(`note`)가 입력 순서로 재신고되는가(`gather_per_source`).
- 되돌리기 값 `APM_FANOUT_CONCURRENCY=1`: 폭 계산 · 계약 위반 뒤 호출 중단(종전 직렬 동작).
- W3b: 자리·작업 슬롯의 호출자별 보유 수 누수(무작위 취소·대기 초과 스트레스 · 넘겨받은 직후 취소) ·
  호출자 키 비노출(작업 기록·혼잡 현황) · 감사 `waits=` 이스케이프 · 설정 키 검증 · D-347 ③ 기본값.

외부 호출 0 — httpx `MockTransport` · 실 spawn 프로세스는 `wait_for`로 묶는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pickle
import random
import traceback
from typing import Any

import httpx
import pytest
from apm_gateway.adapters.parse_pool import ParsePool
from apm_gateway.adapters.throttle import PrioritySlots
from apm_gateway.application.fleet_tools import FleetTools
from apm_gateway.application.jobs import Job, JobManager, _Slots
from apm_gateway.application.parallel import gather_bounded
from apm_gateway.application.sources import JenniferSource, build_source_set, gather_per_source
from apm_gateway.application.spool import Spool
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_POLLER,
    CallScope,
    current_scope,
    use_scope,
)
from apm_gateway.domain.errors import API_ERROR, CONTRACT_VIOLATION, ApmError
from apm_gateway.interface.audit import audit
from conftest import NOW_S, make_tools
from test_plan205_w1_parse_pool import SECRET, _client, _rows
from test_plan205_w2b_fanout import STATUS, FanFake, _tools

from apm_gateway.config import JobConfig, load_config

LIMIT_S = 20.0


# ── W1 프로세스 풀 ─────────────────────────────────────────


async def test_spawn_worker_imports_only_parse_modules():
    """워커 import 표면은 parse_pool · json_stream · credentials(+ 패키지 __init__)뿐 — 서버·설정·
    httpx를 끌어오지 않는다(spawn은 `-m pkg.__main__`을 다시 실행하지 않는다)."""
    pool = ParsePool(1, 1)
    try:
        await asyncio.wait_for(pool.json('{"a": 1}', None, "utf-8"), LIMIT_S)
        executor = pool._executor
        assert executor is not None
        probe = (
            "sorted(m for m in __import__('sys').modules"
            " if m.startswith(('apm_gateway', 'httpx', 'mcp', 'uvicorn', 'redis')))"
        )
        mods = await asyncio.wait_for(asyncio.wrap_future(executor.submit(eval, probe)), LIMIT_S)
    finally:
        pool.shutdown()
    assert set(mods) <= {
        "apm_gateway",
        "apm_gateway.adapters",
        "apm_gateway.adapters.parse_pool",
        "apm_gateway.adapters.json_stream",
        "apm_gateway.domain",
        "apm_gateway.domain.credentials",
    }, mods
    assert "apm_gateway.adapters.parse_pool" in mods


@pytest.mark.parametrize("cap", [4 * 1024 * 1024, 64 * 1024], ids=["memory", "spooled"])
async def test_pool_error_chain_carries_no_raw_body(tmp_path, cap):
    """풀 경로 파싱 오류의 예외 사슬 전체(원격 traceback 문자열 포함)에 원문 비밀값이 없다."""
    raw = ('{"password": "' + SECRET + '", "x": [1 2]}' + " " * 300_000).encode()
    pool = ParsePool(1, 1024)
    client, _ = _client(raw, tmp_path / "tmp", pool=pool, cap=cap)
    try:
        with pytest.raises(ApmError) as info:
            await asyncio.wait_for(client.get_json("/api/instance", {"domain_id": 1}), LIMIT_S)
    finally:
        await client.aclose()
        pool.shutdown()
    assert info.value.code == API_ERROR
    assert info.value.reason == "JSON 파싱 실패: /api/instance"
    text = "".join(traceback.format_exception(info.value))
    assert SECRET not in text
    exc: BaseException | None = info.value
    while exc is not None:  # 사슬의 각 예외 pickle에도 원문이 없다
        assert SECRET.encode() not in pickle.dumps(exc.args)
        exc = exc.__cause__ or exc.__context__
    assert list((tmp_path / "tmp").glob("apm-resp-*")) == []  # 실패해도 스풀 파일은 지운다


async def test_cancel_during_pool_parse_discards_spool_and_pool_survives(tmp_path, caplog):
    """파싱 중 취소 — 부모가 스풀 파일을 지우고(워커는 읽기만), 풀은 깨지지 않고 다음 호출을
    받는다."""
    raw = json.dumps({"result": _rows(24_000)}).encode()  # ~5MiB — 파싱에 수백 ms
    spool = tmp_path / "tmp"
    pool = ParsePool(1, 1024)
    client, _ = _client(raw, spool, pool=pool, cap=64 * 1024)
    try:
        await asyncio.wait_for(pool.text("warm-up"), LIMIT_S)
        task = asyncio.create_task(client.get_json("/api/instance", {"domain_id": 1}))
        for _ in range(500):  # 워커에 일이 넘어갈 때까지
            await asyncio.sleep(0.005)
            executor = pool._executor
            if executor is not None and executor._pending_work_items:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert list(spool.glob("apm-resp-*")) == []
        with caplog.at_level(logging.WARNING, logger="apm_gateway.adapters.parse_pool"):
            out = await asyncio.wait_for(pool.text("jdbc:mysql://u:" + SECRET + "@db/x"), LIMIT_S)
        assert SECRET not in out
        assert not [r for r in caplog.records if "풀 장애" in r.getMessage()]
    finally:
        await client.aclose()
        pool.shutdown()


async def test_pool_shutdown_waits_for_workers():
    pool = ParsePool(2, 1)
    await asyncio.wait_for(asyncio.gather(pool.text("a"), pool.text("b")), LIMIT_S)
    executor = pool._executor
    assert executor is not None
    procs = list(executor._processes.values())
    assert procs
    pool.shutdown()
    assert pool._executor is None
    assert all(not p.is_alive() for p in procs)  # wait=True — 워커가 끝난 뒤 돌아온다


# ── W2 순서: 여러 소스가 섞인 입력 ─────────────────────────────


class _Api:
    max_in_flight = 8


class _Notes(CallScope):
    def __init__(self) -> None:
        super().__init__()
        self.notes: list[str] = []
        self.masked_seen: list[list[str]] = []

    def note(self, text: str) -> None:
        self.notes.append(text)

    def masked(self, fields: Any) -> None:
        self.masked_seen.append(sorted(fields))


async def _notes_of(fanout: int) -> tuple[list[str], list[Any]]:
    a = JenniferSource("a", _Api(), None, fanout=fanout)  # type: ignore[arg-type]
    b = JenniferSource("b", _Api(), None, fanout=fanout)  # type: ignore[arg-type]
    items = [(a, 0), (b, 1), (a, 2), (b, 3), (a, 4), (b, 5)]  # 소스가 번갈아 나오는 입력

    async def fn(item: tuple[JenniferSource, int]) -> int:
        i = item[1]
        await asyncio.sleep(0.003 * (6 - i))  # 뒤 항목이 먼저 끝난다
        current_scope().note(f"n{i}")
        return i

    scope = _Notes()
    with use_scope(scope):
        out = await gather_per_source(items, lambda it: it[0], fn)
    return scope.notes, out


async def test_gather_per_source_results_keep_input_order_across_sources():
    serial, fan = await _notes_of(1), await _notes_of(2)
    assert serial[1] == fan[1] == [0, 1, 2, 3, 4, 5]


async def test_gather_per_source_notes_keep_input_order_across_sources():
    serial, fan = await _notes_of(1), await _notes_of(2)
    assert serial[0] == ["n0", "n1", "n2", "n3", "n4", "n5"]
    assert fan[0] == serial[0]


# ── 되돌리기 값 APM_FANOUT_CONCURRENCY=1 ─────────────────────────


def _two_sources(**extra: str) -> dict[str, str]:
    return {
        "JENNIFER_SOURCES": '["bank", "common"]',
        "JENNIFER_BANK_API_URL": "http://bank.test",
        "JENNIFER_BANK_API_TOKEN": "t1",
        "JENNIFER_COMMON_API_URL": "http://common.test",
        "JENNIFER_COMMON_API_TOKEN": "t2",
        **extra,
    }


def test_fanout_one_makes_every_width_one(tmp_path):
    cfg = load_config(_two_sources(APM_FANOUT_CONCURRENCY="1"), dotenv=None)
    sources = build_source_set(cfg)
    assert [s.width for s in sources] == [1, 1]
    assert [s.resolver._max_in_flight for s in sources] == [1, 1]
    # 기본값 2 — 자리 8은 그대로(D-347 ③)이고 폭만 min(2, 8)
    cfg = load_config(_two_sources(), dotenv=None)
    sources = build_source_set(cfg)
    assert [s.width for s in sources] == [2, 2]
    assert [s.api.max_in_flight for s in sources] == [8, 8]


async def test_fanout_one_runs_without_tasks_in_input_order():
    """폭 1은 태스크 없이 입력 순서대로 하나씩 — 맥락도 감싸지 않는다(바깥 맥락 그대로)."""
    scope = _Notes()
    seen: list[CallScope] = []

    async def fn(i: int) -> int:
        seen.append(current_scope())
        return i

    with use_scope(scope):
        out = await gather_bounded(range(4), fn, 1)
    assert out == [0, 1, 2, 3]
    assert all(s is scope for s in seen)


async def test_fanout_one_stops_at_first_contract_violation_like_serial(tmp_path):
    fake = FanFake(hosts=("bank.test",))
    asked: list[int] = []
    original = fake.handler

    async def counting(request: httpx.Request) -> httpx.Response:
        if request.url.path == STATUS:
            asked.append(int(request.url.params.get("domain_id", "0")))
        return await original(request)

    fake.handler = counting  # type: ignore[method-assign]
    tools = _tools(fake, tmp_path, 1)
    await tools.sources.get("default").resolver.inventory()
    fake.contract[STATUS] = {("bank.test", 1)}
    with pytest.raises(ApmError) as info:
        await FleetTools(tools).ranking(metric="calls", full=True, lookback_minutes=120)
    assert info.value.code == CONTRACT_VIOLATION
    # 종전: 첫 위반(도메인 1의 첫 인스턴스)에서 멈춘다 — 그 뒤 호출 0
    assert asked and asked[-1] == 1 and asked.count(1) == 1, asked


# ── W3b 누수 ───────────────────────────────────────────────


@pytest.mark.parametrize("seed", range(6))
async def test_priority_slots_stress_no_leak(seed):
    """무작위 순위·호출자(키 없음 포함)·대기 초과·바깥 취소가 섞여도 자리·호출자별 보유 수가 새지
    않고, 자리 상한을 넘지 않는다."""
    rng = random.Random(seed)
    slots = PrioritySlots(3, 0.02, per_caller=1)
    over: list[tuple[int, int]] = []

    def check() -> None:
        held_by = sum(slots._held_by.values())
        if slots.in_flight > slots.limit or held_by > slots.in_flight:
            over.append((slots.in_flight, held_by))

    async def worker() -> None:
        caller = rng.choice(["user:a", "user:b", "user:c", ""])
        prio = rng.choice([PRIORITY_POLLER, PRIORITY_INTERACTIVE, PRIORITY_BACKGROUND])
        try:
            if rng.random() < 0.3:
                await asyncio.wait_for(slots.acquire(prio, caller), rng.uniform(0.001, 0.02))
            else:
                await slots.acquire(prio, caller)
        except TimeoutError:
            return
        check()
        try:
            await asyncio.sleep(rng.uniform(0, 0.01))
        finally:
            slots.release(caller)
            check()

    tasks = [asyncio.create_task(worker()) for _ in range(120)]
    for _ in range(30):  # 대기 중·보유 중 아무 태스크나 바깥에서 취소
        await asyncio.sleep(rng.uniform(0, 0.004))
        rng.choice(tasks).cancel()
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), LIMIT_S)
    assert over == []
    assert (slots.in_flight, slots.waiting, slots._held_by) == (0, 0, {})


async def test_job_slots_cancel_after_handoff_no_leak():
    slots = _Slots(1, per_owner=1)
    assert slots.try_acquire("A")
    b = asyncio.create_task(slots.acquire("B"))
    c = asyncio.create_task(slots.acquire("C"))
    await asyncio.sleep(0)
    slots.release("A")  # B에게 넘긴다
    b.cancel()  # 깨기 전에 취소 — 넘겨받은 슬롯을 C에게
    with pytest.raises(asyncio.CancelledError):
        await b
    await asyncio.wait_for(c, 1.0)
    assert slots._held_by == {"C": 1} and slots.in_use == 1
    slots.release("C")
    assert slots._held_by == {} and slots.in_use == 0 and not slots._waiters


async def test_job_slots_waiter_cancelled_while_queued_no_leak():
    slots = _Slots(1, per_owner=2)
    assert slots.try_acquire("A")
    b = asyncio.create_task(slots.acquire("B"))
    await asyncio.sleep(0)
    b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await b
    assert not slots._waiters
    slots.release("A")
    assert slots._held_by == {} and slots.in_use == 0


# ── W3b 노출 · 감사 · 설정 ─────────────────────────────────


def _manager(tmp_path) -> JobManager:
    tools, _ = make_tools("")
    cfg = JobConfig(spool_dir=tmp_path / "spool")
    return JobManager(
        Spool(cfg.spool_dir), cfg, envelope=tools.ok, error_envelope=tools.err, clock=lambda: NOW_S
    )


def test_job_caller_key_not_in_record(tmp_path):
    job = Job(
        _manager(tmp_path),
        tool="apm_fleet_ranking",
        principal="chat",
        owner="user:alice",
        target="",
        now=NOW_S,
    )
    assert job.caller == "chat\x00user:alice"
    record = job.record()
    assert "caller" not in record
    text = json.dumps(record, ensure_ascii=False, default=str)
    assert "\x00" not in text and "\\u0000" not in text
    assert set(record["slot_wait_ms"]) == {"total", "max"}


async def test_congestion_snapshot_has_counts_only():
    slots = PrioritySlots(2, per_caller=1)
    await slots.acquire(PRIORITY_INTERACTIVE, "chat\x00user:alice")
    await slots.acquire(PRIORITY_INTERACTIVE, "chat\x00user:bob")
    waiter = asyncio.create_task(slots.acquire(PRIORITY_INTERACTIVE, "chat\x00user:carol"))
    await asyncio.sleep(0)
    snap = slots.snapshot()
    assert set(snap) == {"limit", "per_caller_limit", "in_flight", "waiting", "callers"}
    assert all(isinstance(v, int) for v in snap.values())
    assert snap["callers"] == 3 and snap["waiting"] == 1
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    slots.release("chat\x00user:alice")
    slots.release("chat\x00user:bob")
    assert slots._held_by == {}


def test_audit_waits_field_is_escaped(caplog):
    with caplog.at_level(logging.INFO, logger="apm_gateway.audit"):
        audit("t", "x", 1.0, waits="slot_ms:1/1\nforged=1")
        audit("t", "x", 1.0, error="boom", waits="slot_ms:1/1\rforged=1")
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 2
    assert all("\n" not in line and "\r" not in line for line in lines)
    assert "waits=slot_ms:1/1\\nforged=1" in lines[0]
    assert "waits=slot_ms:1/1\\rforged=1" in lines[1]


@pytest.mark.parametrize(
    "key,bad",
    [
        ("APM_FANOUT_CONCURRENCY", "0"),
        ("APM_FANOUT_CONCURRENCY", "-1"),
        ("APM_FANOUT_CONCURRENCY", "1.5"),
        ("APM_FANOUT_CONCURRENCY", "two"),
        ("APM_JOB_MAX_CONCURRENT_PER_OWNER", "0.5"),
        ("APM_JOB_MAX_CONCURRENT_PER_OWNER", "x"),
        ("JENNIFER_MAX_INFLIGHT_PER_CALLER", "1.5"),
        ("APM_PARSE_PROCESS_MIN_BYTES", "-1"),
    ],
)
def test_new_keys_reject_bad_values(key, bad):
    with pytest.raises(ValueError, match=key):
        load_config({key: bad}, dotenv=None)


def test_per_source_caller_cap_rejects_negative():
    with pytest.raises(ValueError, match="JENNIFER_BANK_MAX_INFLIGHT_PER_CALLER"):
        load_config(_two_sources(JENNIFER_BANK_MAX_INFLIGHT_PER_CALLER="-2"), dotenv=None)


def test_d347_defaults_unchanged_and_new_defaults():
    cfg = load_config({}, dotenv=None)
    assert cfg.jennifer.max_inflight_per_source == 8  # D-347 ③
    assert cfg.jennifer.rate_limit_per_sec == 5
    assert cfg.perf.fanout_concurrency == 2  # R-1 실측 뒤 기본값
    assert cfg.jobs.max_concurrent == 4 and cfg.jobs.max_concurrent_per_owner == 2
    assert cfg.jennifer.max_inflight_per_caller is None  # 자동 = 자리 // 2
    cfg = load_config(_two_sources(), dotenv=None)
    assert [s.api.client._slots.per_caller for s in build_source_set(cfg)] == [4, 4]
