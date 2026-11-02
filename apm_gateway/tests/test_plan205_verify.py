"""plans/205 독립 검증(verifier) — 되돌리기 값 · 취소·누수 · 자격증명 경계 · spawn 표면.

결함 재현 테스트(계약 위반 뒤 새 항목 · 풀 폐기 시 대기 호출 취소)는 교정 뒤 회귀 가드로 남긴다.
외부 네트워크 0 — `httpx.MockTransport`만 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import pickle
import random
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.adapters.parse_pool import ParsePool, parse_scrub_json
from apm_gateway.adapters.throttle import PrioritySlots
from apm_gateway.application.fanout import gather_ordered
from apm_gateway.application.jobs import JobManager, _Slots
from apm_gateway.application.spool import Spool
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_POLLER,
    CallScope,
    use_scope,
)
from apm_gateway.domain.errors import CONTRACT_VIOLATION, ApmError
from apm_gateway.interface.server import audit_job_finished, run_tool
from conftest import TOKEN, make_tools, synthetic_handler

from apm_gateway.config import JenniferApiConfig, JobConfig, load_config

REVERT = {
    "APM_FANOUT_CONCURRENCY": "1",
    "APM_PARSE_WORKERS": "0",
    "JENNIFER_MAX_INFLIGHT_PER_CALLER": "0",
    "APM_JOB_MAX_CONCURRENT_PER_OWNER": "0",
}


async def _settle(rounds: int = 5) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


# ── 1. 설정 기본값·되돌리기 값 ───────────────────────────────────


def test_defaults_and_revert_values_parse():
    base = {"JENNIFER_API_URL": "http://apm.test", "JENNIFER_API_TOKEN": TOKEN}
    cfg = load_config(base)
    assert cfg.jennifer.max_inflight_per_source == 8  # D-347 ③ 불변
    assert cfg.jennifer.rate_limit_per_sec == 5
    assert cfg.jennifer.max_inflight_per_caller == 4  # 자리 // 2
    assert cfg.jobs.max_concurrent == 4
    assert cfg.jobs.max_concurrent_per_owner == 4  # 사용자 결정 2026-11-02(2 → 4)
    assert cfg.perf.fanout_concurrency == 2
    assert cfg.perf.parse_process_min_bytes == 256 * 1024
    rev = load_config({**base, **REVERT})
    assert rev.perf.fanout_concurrency == 1 and rev.perf.parse_workers == 0
    assert rev.jennifer.max_inflight_per_caller == 0
    assert rev.jobs.max_concurrent_per_owner == 0
    for src in rev.sources:  # 소스별 설정도 전역 0을 물려받는다
        assert src.max_inflight_per_caller == 0


def test_per_source_slots_drive_auto_per_caller():
    env = {
        "JENNIFER_SOURCES": '["a","b"]',
        "JENNIFER_A_API_URL": "http://a.test",
        "JENNIFER_A_API_TOKEN": TOKEN,
        "JENNIFER_A_MAX_INFLIGHT_PER_SOURCE": "3",
        "JENNIFER_B_API_URL": "http://b.test",
        "JENNIFER_B_API_TOKEN": TOKEN,
    }
    by_id = {s.source_id: s for s in load_config(env).sources}
    assert by_id["a"].max_inflight_per_caller == 1  # 3 // 2
    assert by_id["b"].max_inflight_per_caller == 4


# ── 1. 되돌리기 값에서 감사 줄 · 작업 기록 ─────────────────────────


async def _audit_line(tmp_path: Path, extra: dict[str, str]) -> tuple[str, dict[str, Any]]:
    handler = synthetic_handler()
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler), extra=extra)
    jcfg = JobConfig(
        spool_dir=tmp_path / f"spool-{uuid.uuid4().hex[:6]}",
        max_concurrent_per_owner=int(extra.get("APM_JOB_MAX_CONCURRENT_PER_OWNER", "4")),
    )
    jobs = JobManager(
        Spool(jcfg.spool_dir),
        jcfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        on_finish=audit_job_finished,
    )
    records: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.getMessage().startswith("apm audit"):
                records.append(record)

    handler_obj = _H()
    log = logging.getLogger("apm_gateway")
    old = log.level
    log.addHandler(handler_obj)
    log.setLevel(logging.INFO)
    try:
        text = await run_tool(
            tools,
            "apm_active_services",
            "was-host01",
            lambda: tools.apm_active_services(hostname="was-host01"),
            jobs=jobs,
            principal="chat",
            owner="user:kim",
        )
    finally:
        log.removeHandler(handler_obj)
        log.setLevel(old)
        await jobs.aclose()
    assert len(records) == 1
    return records[0].getMessage(), json.loads(text)


async def test_zero_wait_audit_line_documents_current_shape(tmp_path):
    """현 동작 기록 — 대기 0이어도(속도 제어 끔 · 자리 여유) 칸이 붙고 토큰·호출자 키는 없다.
    되돌리기 값과 무관하게 늘 켜진 관측 칸이다(허용 차이 — D-356 부기)."""
    line, _ = await _audit_line(tmp_path, {**REVERT})
    assert line.endswith(" waits=slot:0/0,throttle:0/0"), line
    assert TOKEN not in line and "chat|user:kim" not in line


# ── 2. 동시 경로의 계약 위반 뒤 새 호출 ───────────────────────────


async def test_concurrent_path_does_not_start_new_items_after_contract_violation():
    started: list[int] = []

    async def call(i: int) -> int:
        started.append(i)
        if i == 0:
            raise ApmError(CONTRACT_VIOLATION, "계약 위반")
        await asyncio.sleep(0.01)
        return i

    out = await gather_ordered(range(10), call, limit=2)
    assert [u for u, _ in out] == [0]  # 순차 경로처럼 첫 계약 위반 항목까지
    assert isinstance(dict(out)[0], ApmError)
    assert len(started) <= 2, started  # 0 + 이미 떠난 1건까지


# ── 3. 취소·누수 ─────────────────────────────────────────────


async def test_priority_slots_random_cancel_and_timeout_never_leak():
    rng = random.Random(205)
    for per_caller in (0, 2):
        slots = PrioritySlots(3, aging_seconds=0.01, per_caller=per_caller)
        held = 0
        max_seen = 0

        async def worker(i: int, s: PrioritySlots = slots) -> None:
            nonlocal held, max_seen
            caller = rng.choice(["", "a", "b", "c"])
            prio = rng.choice([PRIORITY_POLLER, PRIORITY_INTERACTIVE, PRIORITY_BACKGROUND])
            try:
                await asyncio.wait_for(s.acquire(prio, caller), rng.choice([0.001, 0.01, 1]))
            except TimeoutError:
                return
            held += 1
            max_seen = max(max_seen, held)
            try:
                await asyncio.sleep(rng.random() * 0.005)
            finally:
                held -= 1
                s.release(caller)

        tasks = [asyncio.create_task(worker(i)) for i in range(300)]
        for _ in range(60):
            await asyncio.sleep(rng.random() * 0.002)
            rng.choice(tasks).cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert max_seen <= 3
        assert slots.in_flight == 0 and slots.waiting == 0 and slots._by_caller == {}


async def test_job_slots_random_cancel_never_leak():
    rng = random.Random(7)
    for per_owner in (0, 2):
        slots = _Slots(2, per_owner)
        running = 0
        peak = 0

        async def worker(s: _Slots = slots) -> None:
            nonlocal running, peak
            key = rng.choice(["p|a", "p|b", "q"])
            await s.acquire(key)
            running += 1
            peak = max(peak, running)
            try:
                await asyncio.sleep(rng.random() * 0.004)
            finally:
                running -= 1
                s.release(key)

        tasks = [asyncio.create_task(worker()) for _ in range(200)]
        for _ in range(50):
            await asyncio.sleep(rng.random() * 0.002)
            rng.choice(tasks).cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert peak <= 2
        assert slots.in_use == 0 and not slots._waiters and slots._running == {}


class _Caller(CallScope):
    @property
    def caller(self) -> str:
        return "chat|user:kim"


async def test_outer_cancel_mid_fanout_returns_every_client_slot():
    """동시 항목이 자리 대기·HTTP 중일 때 바깥 작업을 취소하면 자리·호출자 카운터가 0이 된다."""
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json={"result": []})

    client = JenniferClient(
        JenniferApiConfig(
            url="http://apm.test",
            token=TOKEN,
            timeout_seconds=5,
            rate_limit_per_sec=0,
            max_inflight_per_source=2,
            max_inflight_per_caller=1,
        ),
        transport=httpx.MockTransport(handler),
    )

    async def tool() -> Any:
        with use_scope(_Caller()):
            return await gather_ordered(
                range(6), lambda i: client.get_json("/api/instance", {"domain_id": i}), limit=4
            )

    task = asyncio.create_task(tool())
    await _settle(30)
    assert client.slot_status()["in_flight"] == 2 and client.slot_status()["waiting"] == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.slot_status() == {"in_flight": 0, "waiting": 0, "callers": 0}
    assert client._slots._by_caller == {}
    await client.aclose()


# ── 4. 자격증명 경계 · 6. spawn 표면 ───────────────────────────────


SECRET = "pw-CANARY-205"


def test_worker_decode_error_carries_no_body():
    """워커가 돌려보내는 JSON 오류는 원문(`doc`)을 싣지 않는다 — 스레드 폴백도 같은 함수."""
    with pytest.raises(ValueError) as info:
        parse_scrub_json('{"password": "' + SECRET + '", ', None, "utf-8")
    e = info.value
    assert type(e) is ValueError and e.__cause__ is None and e.__suppress_context__
    assert SECRET not in repr(e.args) and not hasattr(e, "doc")
    # 프로세스 경계는 pickle — 맥락(__context__ · 원문 `doc`을 쥔 JSONDecodeError)은 건너가지 않는다
    back = pickle.loads(pickle.dumps(e))
    assert back.__context__ is None and SECRET not in repr(back.args)


async def test_pool_returns_only_scrubbed_values_and_errors_without_body():
    pool = ParsePool(1, 0)
    try:
        body = json.dumps({"result": [{"url": f"jdbc:oracle:thin:s/{SECRET}@db", "n": 1}]})
        cleaned, _notes, _masked = await pool.json(body, None, "utf-8", len(body))
        assert SECRET not in json.dumps(cleaned)
        with pytest.raises(ValueError) as info:
            await pool.json('{"a": "' + SECRET + '"', None, "utf-8", 10)
        chain = [info.value, info.value.__cause__, info.value.__context__]
        assert all(SECRET not in repr(getattr(x, "args", "")) for x in chain if x is not None)
        assert all(SECRET not in str(x) for x in chain if x is not None)
    finally:
        pool.shutdown()


def test_spawn_worker_imports_only_parse_surface():
    """워커 프로세스가 파싱 모듈을 불러온 뒤 apm_gateway 모듈 표면 — 설정·클라이언트·서버 없음."""
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as ex:
        assert ex.submit(parse_scrub_json, "[1]", None, "utf-8").result(timeout=60)[0] == [1]
        mods = ex.submit(
            eval, "sorted(m for m in __import__('sys').modules if m.startswith('apm_gateway'))"
        ).result(timeout=60)
    assert set(mods) <= {
        "apm_gateway",
        "apm_gateway.adapters",
        "apm_gateway.adapters.parse_pool",
        "apm_gateway.adapters.json_stream",
        "apm_gateway.domain",
        "apm_gateway.domain.credentials",
    }, mods


async def test_submit_failure_does_not_cancel_other_queued_calls():
    pool = ParsePool(1, 0)
    try:
        big = json.dumps([{"k": "v" * 50, "i": i} for i in range(150_000)])
        running = asyncio.create_task(pool.json(big, None, "utf-8", len(big)))
        await asyncio.sleep(0.5)  # 워커가 큰 본문을 잡고 있다
        # 호출 대기열(워커 수 + 1)을 넘는 만큼 줄 세운다 — 넘친 것은 아직 `pending` 상태다
        queued = [
            asyncio.create_task(pool.json(json.dumps({"x": i}), None, "utf-8", 8)) for i in range(4)
        ]
        await _settle(10)
        executor = pool._pool()
        real_submit = executor.submit

        def failing_submit(*a: Any, **kw: Any) -> Any:
            executor.submit = real_submit  # type: ignore[method-assign]
            raise OSError("spawn 실패 흉내")

        executor.submit = failing_submit  # type: ignore[method-assign]
        assert (await pool.json('{"y": 2}', None, "utf-8", 8))[0] == {"y": 2}  # 폴백
        got = await asyncio.gather(*queued, return_exceptions=True)
        assert [g if isinstance(g, BaseException) else g[0] for g in got] == [
            {"x": i} for i in range(4)
        ], got
        await running
    finally:
        pool.shutdown()
