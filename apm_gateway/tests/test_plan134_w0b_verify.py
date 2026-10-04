"""plans/134 W0-B 검증자 적대적 테스트 — 경계값 · 경합 · 스풀 손상 · 큰 응답 · 자격증명 변형 ·
우선순위 · 감사 · SSE 수명 (SPEC-apm-question-coverage §2·§3·§4 · 계획 §5 W0-B 완료 증거).

검증 단계에서 추가한 파일이다(소스는 고치지 않는다). 결함을 재현하는 테스트는
`xfail(strict=True)`로 두고 사유에 결함 ID(검증 보고)를 적는다 — 고쳐지면 XPASS가 실패로 드러나므로
그때 표시를 걷는다. 외부 네트워크 0(`httpx.MockTransport` · 127.0.0.1 루프백 uvicorn).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from apm_gateway.adapters.jennifer import client as client_mod
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.adapters.throttle import PriorityThrottle
from apm_gateway.application.jobs import JobManager, _iso, _Slots
from apm_gateway.application.spool import Spool, chunk_name
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_POLLER,
    current_scope,
)
from apm_gateway.domain.credentials import is_password_field, is_secret_key, scrub, scrub_text
from apm_gateway.domain.errors import ApmError
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import GATEWAY_ROOT, NOW_S, TOKEN, make_tools, synthetic_fixtures, synthetic_handler

from apm_gateway.config import JenniferApiConfig, JobConfig

TOOL = "apm_app_health"
MIB = 1024 * 1024
C = "CANARY-w0b-7d1e"  # 어디에도 나오면 안 되는 자격증명 값
XVIEW = "/api/transaction/time"


class Clock:
    def __init__(self, now: float = NOW_S) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _mgr(spool: Path, tools, clock=None, on_finish=None, **cfg) -> JobManager:
    job_cfg = JobConfig(spool_dir=spool, **cfg)
    return JobManager(
        Spool(spool),
        job_cfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        rate_per_sec=5,
        clock=clock or Clock(),
        on_finish=on_finish,
    )


@pytest.fixture
def env(tmp_path):
    tools, _ = make_tools("")
    spool = tmp_path / "spool"
    clock = Clock()

    def build(**cfg) -> JobManager:
        return _mgr(spool, tools, clock, **cfg)

    return tools, clock, build, spool


async def _settle(mgr: JobManager) -> None:
    for _ in range(5):
        await asyncio.sleep(0)
    if mgr._tasks:
        await asyncio.gather(*list(mgr._tasks), return_exceptions=True)


def _job_dirs(spool: Path) -> list[Path]:
    return [p for p in spool.iterdir() if p.is_dir() and p.name != "tmp"] if spool.exists() else []


async def _err(coro) -> ApmError:
    with pytest.raises(ApmError) as exc:
        await coro
    return exc.value


def _later(make, delay: float = 0.02):
    async def call():
        await asyncio.sleep(delay)
        return make()

    return call


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _server(tmp_path, handler, **job_cfg):
    """MCP 서버 — 소스 클라이언트 임시 파일도 tmp 스풀로(저장소 `apm_gateway/var` 오염 방지)."""
    spool = tmp_path / "spool"
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(handler),
        extra={"APM_SPOOL_DIR": str(spool)},
    )
    jobs = _mgr(spool, tools, clock=time.time, on_finish=audit_job_finished, **job_cfg)
    return create_server(tools, jobs=jobs), tools, jobs


async def _wait_state(mcp, job_id: str, owner: str | None = None, tries: int = 400) -> dict:
    args = {"job_id": job_id, **({"owner": owner} if owner is not None else {})}
    for _ in range(tries):
        status = _json(await mcp.call_tool("apm_job_status", args))
        if status.get("job", {}).get("state") not in ("queued", "running"):
            return status
        await asyncio.sleep(0.01)
    raise AssertionError("작업이 끝나지 않았다")


def _audit_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]


# ═══ 1. 인라인 · 청크 경계 (§2.3) ═══════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("n", "chunks"),
    [
        (0, None),
        (1, None),
        (500, None),  # 정확히 인라인 상한 — 스풀 없음
        (501, [501]),  # 한 행 넘음 — 스풀 · 인라인 500
        (2000, [2000]),  # 청크 1개 정확히
        (2001, [2000, 1]),  # 청크 경계 + 1
        (4000, [2000, 2000]),
        (4001, [2000, 2000, 1]),
    ],
)
async def test_inline_and_chunk_boundaries(env, n, chunks):
    tools, _, build, spool = env
    mgr = build()
    rows = [{"i": i, "s": f"r{i}"} for i in range(n)]

    async def call():
        return tools.ok(TOOL, list(rows))

    out = await mgr.execute(TOOL, call, principal="p", owner="o")
    assert out["total_row_count"] == n
    if chunks is None:
        assert out["rows"] == rows and out["row_count"] == n
        assert "artifact" not in out and "job" not in out
        assert _job_dirs(spool) == []  # 짧은 동기 작업 = 디스크 0
        return
    job_id = out["job"]["job_id"]
    assert out["rows"] == rows[:500] and out["row_count"] == 500
    art = out["artifact"]
    assert art["total_rows"] == n and [c["rows"] for c in art["chunks"]] == chunks
    got: list = []
    for c in art["chunks"]:
        got += (await mgr.read(job_id, principal="p", owner="o", chunk=c["index"]))["rows"]
    assert got == rows  # 청크 합 = 원천 · 순서 보존
    err = await _err(mgr.read(job_id, principal="p", owner="o", chunk=len(chunks)))
    assert err.code == "invalid_argument"


@pytest.mark.asyncio
async def test_tiny_inline_and_chunk_sizes(env):
    tools, _, build, _ = env
    mgr = build(inline_rows=1, chunk_rows=1)

    async def call():
        return tools.ok(TOOL, [{"i": 0}, {"i": 1}, {"i": 2}])

    out = await mgr.execute(TOOL, call, principal="p")
    assert out["rows"] == [{"i": 0}] and [c["rows"] for c in out["artifact"]["chunks"]] == [1, 1, 1]
    st = await mgr.status(out["job"]["job_id"], principal="p")
    assert st["rows"] == [{"i": 0}]  # 미리보기도 인라인 크기


@pytest.mark.asyncio
async def test_promoted_zero_rows_completes_with_empty_manifest(env):
    tools, _, build, _ = env
    mgr = build()
    out = await mgr.execute(
        TOOL, _later(lambda: tools.ok(TOOL, [])), principal="p", owner="o", wait_seconds=0
    )
    await _settle(mgr)
    job_id = out["job"]["job_id"]
    st = await mgr.status(job_id, principal="p", owner="o")
    assert st["job"]["state"] == "completed" and st["rows"] == [] and st["total_row_count"] == 0
    assert st["artifact"]["chunks"] == [] and st["artifact"]["total_rows"] == 0
    # 0행 결과의 기본 읽기는 범위 밖(invalid_argument)이다 — 사유가 「결과 행 없음」을 밝힌다
    err = await _err(mgr.read(job_id, principal="p", owner="o"))
    assert err.code == "invalid_argument" and "결과 행 없음" in err.reason


# ═══ 2. wait_seconds 경계 (§2.1) ════════════════════════════════


@pytest.mark.asyncio
async def test_wait_zero_and_negative_zero(env):
    """`wait_seconds=0`(`-0.0` 포함)은 루프 한 바퀴 — 그 안에 끝나는 호출은 동기 결과(디스크 0),
    한 번이라도 기다리는 호출은 접수 봉투다."""
    tools, _, build, spool = env
    mgr = build()

    async def instant():
        return tools.ok(TOOL, [{"i": 1}])

    for ws in (0, 0.0, -0.0):
        out = await mgr.execute(TOOL, instant, principal="p", wait_seconds=ws)
        assert out["rows"] == [{"i": 1}] and "job" not in out
    assert _job_dirs(spool) == []
    for ws in (0, -0.0):
        later = _later(lambda: tools.ok(TOOL, [{"i": 2}]))
        out = await mgr.execute(TOOL, later, principal="p", wait_seconds=ws)
        assert out["job"]["state"] == "running" and out["rows"] == []  # 접수 봉투
        await _settle(mgr)
        st = await mgr.status(out["job"]["job_id"], principal="p")
        assert st["job"]["state"] == "completed" and st["rows"] == [{"i": 2}]
    assert len(_job_dirs(spool)) == 2  # 승격 작업은 기록이 남는다


@pytest.mark.asyncio
async def test_wait_seconds_is_honoured_not_early_not_late(env):
    tools, _, build, _ = env
    mgr = build()
    gate = asyncio.Event()

    async def slow():
        await gate.wait()
        return tools.ok(TOOL, [])

    started = time.monotonic()
    out = await mgr.execute(TOOL, slow, principal="p", wait_seconds=0.2)
    elapsed = time.monotonic() - started
    assert "job" in out and 0.18 <= elapsed < 0.8, elapsed
    gate.set()
    await _settle(mgr)


@pytest.mark.asyncio
async def test_wait_seconds_nan_is_invalid_argument(env):
    tools, _, build, _ = env
    mgr = build()
    later = _later(lambda: tools.ok(TOOL, []))
    err = await _err(mgr.execute(TOOL, later, principal="p", wait_seconds=float("nan")))
    assert err.code == "invalid_argument"


@pytest.mark.asyncio
async def test_wait_seconds_infinite_is_invalid_argument(env):
    """수정 라운드(팀 리드 D7 「wait_seconds NaN·inf 거부」)로 단언을 바꿨다 — 종전 「inf = 생략과
    같음」. 끝까지 기다리려면 생략한다."""
    tools, _, build, spool = env
    mgr = build()
    later = _later(lambda: tools.ok(TOOL, [{"i": 1}]), 0.05)
    err = await _err(mgr.execute(TOOL, later, principal="p", wait_seconds=1e309))
    assert err.code == "invalid_argument" and _job_dirs(spool) == []


@pytest.mark.asyncio
async def test_wait_seconds_string_via_mcp_coerced_or_rejected_without_http(tmp_path):
    hits: list[str] = []
    inner = synthetic_handler()

    async def handler(request):
        hits.append(request.url.path)
        return await inner(request)

    mcp, _, jobs = _server(tmp_path, handler)
    ok = _json(await mcp.call_tool("apm_instance_map", {"wait_seconds": "5"}))
    assert ok["row_count"] == 3 and "error" not in ok  # 숫자 문자열은 수로 받는다
    before = len(hits)
    # 형식 밖 문자열은 도구 인자 검증에서 거부된다(계약 JSON이 아니라 FastMCP ToolError — 종전
    # 형 인자 전부와 같은 처리) · HTTP 0회 · 작업 기록 0
    with pytest.raises(Exception, match="validation error"):
        await mcp.call_tool("apm_instance_map", {"wait_seconds": "abc"})
    assert len(hits) == before and _job_dirs(tmp_path / "spool") == []
    await jobs.aclose()


@pytest.mark.asyncio
async def test_owner_length_boundary(env):
    tools, _, build, _ = env
    mgr = build()

    async def call():
        return tools.ok(TOOL, [])

    assert "error" not in await mgr.execute(TOOL, call, principal="p", owner="x" * 200)
    assert (await _err(mgr.execute(TOOL, call, principal="p", owner="x" * 201))).code == (
        "invalid_argument"
    )


# ═══ 3. 기존 소비자(wait_seconds 없음) — 종전 의미 ═════════════════


@pytest.mark.asyncio
async def test_legacy_call_never_waits_for_background_slots(env):
    """백그라운드 작업이 슬롯을 다 써도 승격 전(동기) 호출은 슬롯을 기다리지 않는다."""
    tools, _, build, _ = env
    mgr = build(max_concurrent=1)
    hold = asyncio.Event()

    async def hog():
        await asyncio.sleep(0.005)
        await current_scope().before_call()
        await hold.wait()
        return tools.ok(TOOL, [])

    bg = await mgr.execute(TOOL, hog, principal="p", wait_seconds=0)
    await asyncio.sleep(0.02)
    assert mgr.summary()["slots_in_use"] == 1

    async def legacy():
        for _ in range(3):
            await current_scope().before_call()
        return tools.ok(TOOL, [{"i": 1}])

    out = await asyncio.wait_for(mgr.execute(TOOL, legacy, principal="investigation"), 1.0)
    assert out["rows"] == [{"i": 1}] and "job" not in out
    hold.set()
    await _settle(mgr)
    assert (await mgr.status(bg["job"]["job_id"], principal="p"))["job"]["state"] == "completed"


@pytest.mark.asyncio
async def test_legacy_long_call_is_never_stall_killed(env):
    """정체 감시는 승격된 작업만 본다 — wait_seconds 없는 소비자의 긴 호출은 끊지 않는다."""
    tools, clock, build, _ = env
    mgr = build(stall_seconds=300)
    gate = asyncio.Event()

    async def slow():
        await gate.wait()
        return tools.ok(TOOL, [{"i": 1}])

    caller = asyncio.create_task(mgr.execute(TOOL, slow, principal="investigation"))
    await asyncio.sleep(0.02)
    clock.now += 10_000
    assert mgr.check_stalled() == []
    gate.set()
    out = await caller
    assert out["rows"] == [{"i": 1}] and "error" not in out


# ═══ 4. 경합 — 상태 · 취소 · 완료 직전 · 소유자 ═══════════════════


@pytest.mark.asyncio
async def test_concurrent_status_and_double_cancel(env):
    tools, _, build, spool = env
    mgr = build()

    async def forever():
        await asyncio.Event().wait()
        return tools.ok(TOOL, [])

    out = await mgr.execute(TOOL, forever, principal="p", owner="o", wait_seconds=0)
    job_id = out["job"]["job_id"]
    await asyncio.sleep(0)
    results = await asyncio.gather(
        mgr.status(job_id, principal="p", owner="o"),
        mgr.cancel(job_id, principal="p", owner="o"),
        mgr.cancel(job_id, principal="p", owner="o"),
        mgr.status(job_id, principal="p", owner="o"),
        return_exceptions=True,
    )
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert results[1]["job"]["state"] == "cancelled" and results[2]["job"]["state"] == "cancelled"
    assert {results[0]["job"]["state"], results[3]["job"]["state"]} <= {"running", "cancelled"}
    final = await mgr.status(job_id, principal="p", owner="o")
    assert final["job"]["state"] == "cancelled" and final["job"]["error"]["code"] == "cancelled"
    assert [p.name for p in (spool / job_id).iterdir()] == ["job.json"]
    assert mgr._live == {} and mgr.summary()["slots_in_use"] == 0


@pytest.mark.asyncio
async def test_cancel_while_writing_result_keeps_complete_result(env):
    """완료 직전(결과를 쓰는 중) 취소 — 상태를 바꾸지 않는다(게이트웨이 SPEC §3.3). 결과는
    온전하다."""
    tools, _, build, _ = env
    mgr = build()
    entered, release = threading.Event(), threading.Event()
    real = mgr.spool.write_result

    def blocked(*args, **kwargs):
        entered.set()
        release.wait(5)
        return real(*args, **kwargs)

    mgr.spool.write_result = blocked  # type: ignore[method-assign]
    rows = [{"i": i} for i in range(600)]
    out = await mgr.execute(
        TOOL, _later(lambda: tools.ok(TOOL, list(rows))), principal="p", owner="o", wait_seconds=0
    )
    job_id = out["job"]["job_id"]
    assert await asyncio.to_thread(entered.wait, 5)
    res = await asyncio.wait_for(mgr.cancel(job_id, principal="p", owner="o"), 1.0)
    assert res["job"]["state"] == "running"  # 쓰는 중 — 취소가 먹지 않았다(응답에 별도 표지 없음)
    release.set()
    await _settle(mgr)
    st = await mgr.status(job_id, principal="p", owner="o")
    assert st["job"]["state"] == "completed" and st["total_row_count"] == 600


@pytest.mark.asyncio
async def test_caller_cancel_during_sync_result_write_leaves_no_files(env):
    """승격 전(동기) 호출이 결과를 쓰는 중에 끊기면 — 끝난 뒤 결과 파일을 지운다(아무도 모르는
    작업을 남기지 않는다)."""
    tools, _, build, spool = env
    mgr = build()
    entered, release = threading.Event(), threading.Event()
    real = mgr.spool.write_result

    def blocked(*args, **kwargs):
        entered.set()
        release.wait(5)
        return real(*args, **kwargs)

    mgr.spool.write_result = blocked  # type: ignore[method-assign]

    async def call():
        return tools.ok(TOOL, [{"i": i} for i in range(600)])

    caller = asyncio.create_task(mgr.execute(TOOL, call, principal="p"))
    assert await asyncio.to_thread(entered.wait, 5)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    release.set()
    await _settle(mgr)
    await asyncio.sleep(0.01)
    assert _job_dirs(spool) == [] and mgr._live == {}


@pytest.mark.asyncio
async def test_foreign_cancel_neither_cancels_nor_reveals(env):
    tools, _, build, _ = env
    mgr = build()
    gate = asyncio.Event()

    async def call():
        await gate.wait()
        return tools.ok(TOOL, [{"i": 1}])

    out = await mgr.execute(TOOL, call, principal="chat", owner="user:a", wait_seconds=0)
    job_id = out["job"]["job_id"]
    reasons = set()
    for principal, owner in (("chat", "user:b"), ("investigation", "user:a"), ("chat", None)):
        for op in (mgr.cancel, mgr.status, mgr.read):
            err = await _err(op(job_id, principal=principal, owner=owner))
            assert err.code == "job_not_found"
            reasons.add(err.reason)
    missing = await _err(mgr.cancel("f" * 32, principal="chat", owner="user:a"))
    reasons.add(missing.reason)
    assert len(reasons) == 1  # 남의 작업 = 없는 작업과 같은 사유
    gate.set()
    await _settle(mgr)
    st = await mgr.status(job_id, principal="chat", owner="user:a")
    assert st["job"]["state"] == "completed"  # 남의 취소 시도는 작업에 영향 없음


@pytest.mark.asyncio
@pytest.mark.parametrize(("made", "asked"), [("", None), (None, ""), ("o", "O"), ("o", "o ")])
async def test_owner_matching_is_exact_empty_is_not_none(env, made, asked):
    """`owner`는 불투명 문자열 — 빈 문자열과 생략(None)도 다른 소유자다(정확 일치)."""
    tools, _, build, _ = env
    mgr = build()
    out = await mgr.execute(
        TOOL, _later(lambda: tools.ok(TOOL, [])), principal="p", owner=made, wait_seconds=0
    )
    await _settle(mgr)
    job_id = out["job"]["job_id"]
    assert (await mgr.status(job_id, principal="p", owner=made))["job"]["state"] == "completed"
    assert (await _err(mgr.status(job_id, principal="p", owner=asked))).code == "job_not_found"


@pytest.mark.asyncio
async def test_job_id_case_and_shape(env):
    tools, _, build, _ = env
    mgr = build()
    out = await mgr.execute(TOOL, _later(lambda: tools.ok(TOOL, [])), principal="p", wait_seconds=0)
    await _settle(mgr)
    job_id = out["job"]["job_id"]
    for bogus in (job_id.upper(), job_id + "0", job_id[:-1], f" {job_id}", 123, ["x"]):
        assert (await _err(mgr.status(bogus, principal="p"))).code == "job_not_found", bogus


# ═══ 5. 동시 실행 슬롯 (§3.4) ═══════════════════════════════════


@pytest.mark.asyncio
async def test_slot_handed_to_cancelled_waiter_passes_to_next():
    slots = _Slots(1)
    assert slots.try_acquire()
    b = asyncio.create_task(slots.acquire())
    c = asyncio.create_task(slots.acquire())
    await asyncio.sleep(0)
    slots.release()  # b에게 넘긴다
    b.cancel()  # b가 깨기 전에 취소 — 넘겨받은 슬롯을 c에게 돌려야 한다
    with pytest.raises(asyncio.CancelledError):
        await b
    await asyncio.wait_for(c, 1.0)
    assert slots.in_use == 1
    slots.release()
    assert slots.in_use == 0


@pytest.mark.asyncio
async def test_cancel_queued_job_keeps_fifo_and_slot_accounting(env):
    tools, _, build, _ = env
    mgr = build(max_concurrent=1)
    gate = asyncio.Event()
    order: list[str] = []

    def make(name: str, hold: asyncio.Event | None):
        async def call():
            await asyncio.sleep(0.005)  # 승격(wait_seconds=0) 뒤 첫 호출
            await current_scope().before_call()
            if hold is not None:
                await hold.wait()
            order.append(name)
            return tools.ok(TOOL, [{"n": name}])

        return call

    a = await mgr.execute(TOOL, make("a", gate), principal="p", wait_seconds=0)
    b = await mgr.execute(TOOL, make("b", None), principal="p", wait_seconds=0)
    c = await mgr.execute(TOOL, make("c", None), principal="p", wait_seconds=0)
    await asyncio.sleep(0.02)
    assert [x["job"]["state"] for x in (a, b, c)] == ["running", "queued", "queued"]
    cancelled = await mgr.cancel(b["job"]["job_id"], principal="p")
    assert cancelled["job"]["state"] == "cancelled"
    gate.set()
    await _settle(mgr)
    assert order == ["a", "c"]
    assert (await mgr.status(c["job"]["job_id"], principal="p"))["job"]["state"] == "completed"
    assert mgr.summary() == {"running": 0, "queued": 0, "slots_in_use": 0, "max_concurrent": 1}


@pytest.mark.asyncio
async def test_concurrency_never_exceeds_slots_and_is_fifo(env):
    tools, _, build, _ = env
    mgr = build(max_concurrent=2)
    active = 0
    peak = 0
    entered: list[int] = []

    def make(i: int):
        async def call():
            nonlocal active, peak
            await asyncio.sleep(0.005)  # 승격 뒤 첫 호출 — 승격 전 호출은 슬롯 밖(동기 호출)
            await current_scope().before_call()  # 슬롯 대기 지점
            active += 1
            peak = max(peak, active)
            entered.append(i)
            await asyncio.sleep(0.01)
            await current_scope().before_call()
            active -= 1
            return tools.ok(TOOL, [{"i": i}])

        return call

    outs = [await mgr.execute(TOOL, make(i), principal="p", wait_seconds=0) for i in range(8)]
    await _settle(mgr)
    assert peak <= 2 and entered == list(range(8))
    for out in outs:
        st = await mgr.status(out["job"]["job_id"], principal="p")
        assert st["job"]["state"] == "completed"
    assert mgr.summary()["slots_in_use"] == 0


# ═══ 6. 정체 감시 · 만료 정리 루프 (실시간 — 약 4초) ═════════════════


@pytest.mark.asyncio
async def test_maintainer_loop_stalls_then_sweeps_expired(tmp_path):
    tools, _ = make_tools("")
    spool = tmp_path / "spool"
    mgr = _mgr(spool, tools, clock=time.time, stall_seconds=1, retention_seconds=1)
    await mgr.start()

    async def stuck():
        await current_scope().before_call()
        await asyncio.Event().wait()
        return tools.ok(TOOL, [])

    out = await mgr.execute(TOOL, stuck, principal="p", wait_seconds=0)
    job_id = out["job"]["job_id"]
    record = spool / job_id / "job.json"
    state = None
    for _ in range(80):  # 감시 주기 1초 · 정체 1초 → 약 2초
        if record.exists():
            state = json.loads(record.read_text(encoding="utf-8")).get("state")
            if state != "running":
                break
        await asyncio.sleep(0.05)
    assert state == "failed"
    assert json.loads(record.read_text(encoding="utf-8"))["error"]["code"] == "stalled"
    for _ in range(100):  # 보관 1초 · 정리 주기 1초 — 조회 없이 감시 루프가 지운다
        if not (spool / job_id).exists():
            break
        await asyncio.sleep(0.05)
    assert not (spool / job_id).exists()
    await mgr.aclose()


@pytest.mark.asyncio
async def test_queued_job_is_not_stalled_while_waiting_for_slot(env):
    tools, clock, build, _ = env
    mgr = build(max_concurrent=1, stall_seconds=300)
    hold = asyncio.Event()

    async def hog():
        await asyncio.sleep(0.005)
        await current_scope().before_call()
        await hold.wait()
        await current_scope().before_call()  # 임대 갱신
        return tools.ok(TOOL, [])

    async def waiter():
        await asyncio.sleep(0.005)
        await current_scope().before_call()
        return tools.ok(TOOL, [])

    a = await mgr.execute(TOOL, hog, principal="p", wait_seconds=0)
    b = await mgr.execute(TOOL, waiter, principal="p", wait_seconds=0)
    await asyncio.sleep(0.02)
    clock.now += 1000
    stalled = mgr.check_stalled()
    assert stalled == [a["job"]["job_id"]]  # 슬롯을 쥔 정체 작업만 — 차례를 기다리는 작업은 아니다
    await _settle(mgr)
    assert (await mgr.status(b["job"]["job_id"], principal="p"))["job"]["state"] == "completed"
    hold.set()


@pytest.mark.asyncio
async def test_retention_boundary_is_exclusive(env):
    tools, clock, build, _ = env
    mgr = build(retention_seconds=100)
    out = await mgr.execute(TOOL, _later(lambda: tools.ok(TOOL, [])), principal="p", wait_seconds=0)
    await _settle(mgr)
    job_id = out["job"]["job_id"]
    finished = NOW_S
    clock.now = finished + 99
    assert (await mgr.status(job_id, principal="p"))["job"]["state"] == "completed"
    clock.now = finished + 100
    assert (await _err(mgr.status(job_id, principal="p"))).code == "job_not_found"


# ═══ 7. 재기동 스캔 (§3.5) ═════════════════════════════════════


def _record(job_id: str, state: str, *, expires: float | None, artifact=None) -> dict:
    return {
        "job_id": job_id,
        "tool": TOOL,
        "principal": "p",
        "owner": "o",
        "target": "h",
        "state": state,
        "created_at": _iso(NOW_S - 50),
        "started_at": _iso(NOW_S - 50),
        "updated_at": _iso(NOW_S - 40),
        "finished_at": None,
        "expires_at": _iso(expires) if expires else None,
        "progress": {"done": 1, "total": None, "unit": "api_calls", "label": "API 호출"},
        "estimate": {"api_calls": None, "seconds": None},
        "api_calls": 1,
        "result_meta": None,
        "artifact": artifact,
        "limits": [],
        "error": None,
    }


@pytest.mark.asyncio
async def test_recover_scan_handles_every_leftover_shape(env):
    tools, _, build, spool = env
    first = build()
    done = await first.execute(
        TOOL, _later(lambda: tools.ok(TOOL, [{"i": 1}])), principal="p", owner="o", wait_seconds=0
    )
    await _settle(first)
    ids = {k: f"{n:032x}" for n, k in enumerate(["corrupt", "orphan", "queued", "running", "old"])}
    sp = Spool(spool)
    (spool / ids["corrupt"]).mkdir(parents=True)
    (spool / ids["corrupt"] / "job.json").write_text("{not json", encoding="utf-8")
    (spool / ids["orphan"]).mkdir()
    (spool / ids["orphan"] / chunk_name(0)).write_text('{"i":1}\n', encoding="utf-8")
    sp.write_record(ids["queued"], _record(ids["queued"], "queued", expires=None))
    sp.write_record(ids["running"], _record(ids["running"], "running", expires=None))
    (spool / ids["running"] / chunk_name(0)).write_text('{"i":1}\n', encoding="utf-8")
    sp.write_record(ids["old"], _record(ids["old"], "completed", expires=NOW_S - 1))
    (spool / "not-a-job").mkdir()
    (spool / "tmp").mkdir(exist_ok=True)
    for i in range(3):
        (spool / "tmp" / f"apm-resp-{i}.body").write_bytes(b"x")

    second = build()
    counts = second.recover()
    assert counts == {"interrupted": 2, "expired": 1, "dropped": 2, "tmp_files": 3}
    for key in ("corrupt", "orphan", "old"):
        assert not (spool / ids[key]).exists(), key
    assert [p.name for p in (spool / ids["running"]).iterdir()] == ["job.json"]
    for key in ("queued", "running"):
        st = await second.status(ids[key], principal="p", owner="o")
        assert st["job"]["state"] == "interrupted" and st["job"]["error"]["code"] == "interrupted"
        assert (await _err(second.read(ids[key], principal="p", owner="o"))).code == (
            "invalid_argument"
        )
    assert (spool / "not-a-job").is_dir()  # 작업 ID 형식 밖 디렉터리는 건드리지 않는다
    kept = await second.read(done["job"]["job_id"], principal="p", owner="o")
    assert kept["rows"] == [{"i": 1}]
    assert second.recover()["interrupted"] == 0  # 두 번 돌려도 같다(멱등)


@pytest.mark.asyncio
async def test_shutdown_interrupts_then_restart_keeps_interrupted(env):
    tools, _, build, _ = env
    first = build()

    async def forever():
        await current_scope().before_call()
        await asyncio.Event().wait()
        return tools.ok(TOOL, [])

    out = await first.execute(TOOL, forever, principal="p", wait_seconds=0)
    await asyncio.sleep(0.01)
    await first.aclose()
    second = build()
    assert second.recover()["interrupted"] == 0  # 이미 끝난 상태(interrupted)로 기록됐다
    st = await second.status(out["job"]["job_id"], principal="p")
    assert st["job"]["state"] == "interrupted"


# ═══ 8. 스풀 손상 ══════════════════════════════════════════════


async def _spooled(mgr: JobManager, tools, n: int = 4100) -> str:
    async def call():
        return tools.ok(TOOL, [{"i": i} for i in range(n)])

    out = await mgr.execute(TOOL, call, principal="p", owner="o")
    return out["job"]["job_id"]


@pytest.mark.asyncio
async def test_damaged_chunks_are_errors_not_silent_rows(env):
    tools, _, build, spool = env
    mgr = build()
    job_id = await _spooled(mgr, tools)
    d = spool / job_id
    (d / chunk_name(1)).unlink()
    (d / chunk_name(2)).write_bytes(b'{"i":1}\n')  # sha256 불일치
    assert len((await mgr.read(job_id, principal="p", owner="o", chunk=0))["rows"]) == 2000
    for chunk in (1, 2):
        err = await _err(mgr.read(job_id, principal="p", owner="o", chunk=chunk))
        assert err.code == "apm_api_error", chunk
    (d / chunk_name(0)).write_bytes(b"garbage")
    err = await _err(mgr.status(job_id, principal="p", owner="o"))
    assert err.code == "apm_api_error" and str(spool) not in err.reason


@pytest.mark.asyncio
async def test_missing_chunk_error_does_not_reveal_spool_path(env):
    tools, _, build, spool = env
    mgr = build()
    job_id = await _spooled(mgr, tools)
    (spool / job_id / chunk_name(1)).unlink()
    err = await _err(mgr.read(job_id, principal="p", owner="o", chunk=1))
    assert str(spool) not in err.reason and job_id not in err.reason


@pytest.mark.asyncio
async def test_missing_text_part_and_record(env):
    tools, _, build, spool = env
    mgr = build()

    async def call():
        return {**tools.ok(TOOL, [{"i": 1}]), "_text_parts": {"profile": "p"}}

    out = await mgr.execute(TOOL, call, principal="p", owner="o")
    job_id = out["job"]["job_id"]
    (spool / job_id / "text-profile.txt").unlink()
    err = await _err(mgr.read(job_id, principal="p", owner="o", part="profile"))
    assert err.code == "apm_api_error" and str(spool) not in err.reason
    (spool / job_id / "job.json").unlink()  # 기록이 없으면 결과 조각만 남아도 없는 작업이다
    assert (await _err(mgr.status(job_id, principal="p", owner="o"))).code == "job_not_found"
    assert mgr.sweep_expired() == [job_id] and not (spool / job_id).exists()


# ═══ 9. 큰 응답 · 깨진 JSON · 임시 파일 (§3.5 N-18) ═════════════════


def _client(handler, spool: Path, cap: int = 4 * MIB) -> JenniferClient:
    return JenniferClient(
        JenniferApiConfig(
            url="http://apm.test", token=TOKEN, rate_limit_per_sec=0, max_response_bytes=cap
        ),
        transport=httpx.MockTransport(handler),
        spool_dir=spool,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tail",
    [b"", b"]", b"]}garbage", b',{"x":'],  # 잘림 · 닫는 괄호 하나 · 뒤 쓰레기 · 원소 중간 잘림
)
async def test_big_broken_json_is_api_error_and_temp_removed(tmp_path, tail):
    items = (b'{"i": %d, "s": "%s"}' % (i, b"y" * 200) for i in range(60_000))
    head = b'{"result": [' + b",".join(items)
    raw = head + tail
    assert len(raw) > 10 * MIB
    spool = tmp_path / "tmp"
    client = _client(lambda r: httpx.Response(200, content=raw), spool)
    with pytest.raises(ApmError) as exc:
        await client.get_json("/api/instance", {"domain_id": 1000})
    await client.aclose()
    assert exc.value.code == "apm_api_error" and "JSON 파싱 실패" in exc.value.reason
    assert list(spool.iterdir()) == []


@pytest.mark.asyncio
async def test_single_huge_element_over_10mib(tmp_path):
    big = "가" * (4 * MIB)  # UTF-8 12 MiB 문자열 하나
    raw = json.dumps({"result": [{"name": big, "password": C}, 1]}, ensure_ascii=False).encode()
    assert len(raw) > 10 * MIB
    spool = tmp_path / "tmp"
    client = _client(lambda r: httpx.Response(200, content=raw), spool)
    body = await client.get_json("/api/instance", {"domain_id": 1000})
    await client.aclose()
    assert body == {"result": [{"name": big}, 1]}  # password 필드는 키째 제거
    assert list(spool.iterdir()) == []


class _Breaks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'{"result": [' + b"1," * 4096
        raise httpx.ReadError("connection reset")


@pytest.mark.asyncio
async def test_stream_break_after_spill_is_source_unavailable_and_temp_removed(tmp_path):
    spool = tmp_path / "tmp"
    client = _client(lambda r: httpx.Response(200, stream=_Breaks()), spool, cap=1024)
    with pytest.raises(ApmError) as exc:
        await client.get_json("/api/instance", {"domain_id": 1000})
    await client.aclose()
    assert exc.value.code == "source_unavailable" and TOKEN not in exc.value.reason
    assert not spool.exists() or list(spool.iterdir()) == []


@pytest.mark.asyncio
async def test_cancel_during_big_parse_removes_temp(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real = client_mod.load_json_file

    def blocked(path, encoding="utf-8"):
        entered.set()
        release.wait(5)
        return real(path, encoding)

    monkeypatch.setattr(client_mod, "load_json_file", blocked)
    raw = json.dumps({"result": [{"i": i} for i in range(1000)]}).encode()
    spool = tmp_path / "tmp"
    client = _client(lambda r: httpx.Response(200, content=raw), spool, cap=1024)
    task = asyncio.create_task(client.get_json("/api/instance", {"domain_id": 1000}))
    assert await asyncio.to_thread(entered.wait, 5)
    assert len(list(spool.iterdir())) == 1  # 임계를 넘어 임시 파일로 받았다
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(spool.iterdir()) == []  # 취소돼도 지운다
    release.set()
    await client.aclose()


@pytest.mark.asyncio
async def test_big_inventory_through_tool_is_not_an_error(tmp_path):
    """메모리 임계(4 MiB)를 넘는 인스턴스 목록도 오류가 아니다(D-296 ④ — 오류로 끊지 않는다)."""
    fixtures = synthetic_fixtures()
    for fx in fixtures:
        if fx["request"]["template"] == "/api/instance":
            base = fx["response"]["body_json"]["result"][0]
            many = [
                {
                    **base,
                    "instanceId": 5000 + i,
                    "name": f"bulk_{i}",
                    "hostName": f"bulk-{i}",
                    "description": "z" * 120,
                }
                for i in range(30_000)
            ]
            fx["response"]["body_json"]["result"] += many
    mcp, _, jobs = _server(tmp_path, synthetic_handler(fixtures))
    out = _json(await mcp.call_tool("apm_instance_map", {}))
    assert "error" not in out and out["row_count"] > 0
    tmp = tmp_path / "spool" / "tmp"
    assert not tmp.exists() or list(tmp.iterdir()) == []
    assert not (GATEWAY_ROOT / "var").exists()  # 테스트가 저장소 스풀을 더럽히지 않는다
    await jobs.aclose()


# ═══ 9-b. 부분 실패 — 도구 봉투 `partial` 도출 (§2.2 · 기존 테스트 공백) ═════════


def _instances_body() -> dict:
    fx = next(f for f in synthetic_fixtures() if f["request"]["template"] == "/api/instance")
    return fx["response"]["body_json"]


@pytest.mark.asyncio
async def test_tool_partial_flag_from_failed_unit_and_job_state(tmp_path):
    """한 단위(오류 기록 조회) 실패 → 봉투 `partial: true` · 승격 작업은 `partial` 상태. 전부
    성공이면 `partial` 키가 없다."""
    fail = {"/api/dbsearch/error": httpx.Response(500, json={"exception": {"message": "boom"}})}
    mcp, _, jobs = _server(tmp_path, synthetic_handler(override=fail))
    sync = _json(await mcp.call_tool("apm_events", {"hostname": "was-host01"}))
    assert sync["partial"] is True and sync["row_count"] == 1
    assert any("오류 기록 조회 실패" in x for x in sync["limits"])
    handle = _json(await mcp.call_tool("apm_events", {"hostname": "was-host01", "wait_seconds": 0}))
    done = await _wait_state(mcp, handle["job"]["job_id"])
    assert done["job"]["state"] == "partial" and done["partial"] is True
    assert done["result_meta"]["partial"] is True
    await jobs.aclose()
    ok_mcp, _, ok_jobs = _server(tmp_path / "ok", synthetic_handler())
    ok = _json(await ok_mcp.call_tool("apm_events", {"hostname": "was-host01"}))
    assert "partial" not in ok
    await ok_jobs.aclose()


@pytest.mark.asyncio
async def test_unavailable_domain_marks_inventory_results_partial(tmp_path):
    domains = {"result": [{"domainId": 1000, "name": "d1"}, {"domainId": 2000, "name": "d2"}]}

    def instances(request):
        if request.url.params.get("domain_id") == "2000":
            return httpx.Response(
                500, json={"exception": {"message": "2000 Domain is not connected"}}
            )
        return httpx.Response(200, json=_instances_body())

    override = {"/api/domain": httpx.Response(200, json=domains), "/api/instance": instances}
    mcp, _, jobs = _server(tmp_path, synthetic_handler(override=override))
    listing = _json(await mcp.call_tool("apm_instance_map", {}))
    assert listing["partial"] is True and listing["row_count"] == 3
    health = _json(await mcp.call_tool("apm_app_health", {"hostname": "was-host01"}))
    assert "error" not in health and health["partial"] is True  # 대상이 빠진 도메인에 있을 수 있다
    await jobs.aclose()


# ═══ 10. 자격증명 변형 (§4.2 N-17) ═════════════════════════════════


@pytest.mark.parametrize(
    "text",
    [
        f"Authorization: Basic {C}",
        f"Proxy-Authorization: Basic {C}",
        f"GET /x HTTP/1.1\r\nAuthorization: Basic {C}\r\n",
    ],
)
def test_basic_auth_value_in_free_text_is_masked(text):
    assert C not in scrub_text(text)


@pytest.mark.parametrize(
    "value",
    [
        {"PGPASSWORD": C},
        [{"key": "PGPASSWORD", "value": C}],
        f"PGPASSWORD={C} psql -h db",
        {"SYSTEM": {"DBPASSWORD": C}},
    ],
)
def test_glued_secret_key_names_are_masked(value):
    cleaned, _ = scrub(value)
    assert C not in json.dumps(cleaned)


@pytest.mark.parametrize(
    "url",
    [
        f"jdbc:postgresql://app:pa/{C}@db:5432/x",
        f"jdbc:mysql://app:p#{C}@db:3306/x",
        f"jdbc:mysql://app:p@{C}@db:3306/x",
    ],
)
def test_userinfo_password_with_reserved_chars_is_masked(url):
    assert C not in scrub_text(url)


@pytest.mark.parametrize(
    "text",
    [
        f"jdbc:mysql://app:{C}@db:3306/x",
        f"-Dspring.datasource.password={C}",
        f"spring.datasource.password: {C}",
        f"export DB_PW2={C}",
        f'{{"secretKey": "{C}"}}',
        f"client_secret={C}&grant_type=x",
        f"DB_PASSWORD='{C} with space'",
        f"Authorization: Bearer {C}",
    ],
)
def test_common_credential_forms_are_masked(text):
    out = scrub_text(text)
    assert C not in out and "[가림]" in out


def test_jennifer_field_vocabulary_is_not_secret_classified():
    """일반 설정값은 가리지 않는다 — 게이트웨이가 아는 제니퍼 필드 이름이 비밀 패턴에 걸리지 않는다
    (계정 `password`만 예외)."""
    names: set[str] = set()
    for path in (
        GATEWAY_ROOT / "apm_gateway" / "adapters" / "jennifer" / "fields.py",
        GATEWAY_ROOT / "testdata" / "jennifer" / "scripts" / "jennifer_catalog.py",
    ):
        names |= set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', path.read_text(encoding="utf-8")))
    for fx in synthetic_fixtures():
        body = fx["response"].get("body_json")
        stack = [body]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                names |= set(node)
                stack += list(node.values())
            elif isinstance(node, list):
                stack += node
    flagged = {n for n in names if is_secret_key(n) or is_password_field(n)}
    assert flagged <= {"password"}, flagged


# ═══ 11. 우선순위 — 폴러 · 승격 뒤 background (§3.4 N-14) ═══════════


@pytest.mark.asyncio
async def test_poller_calls_run_at_poller_priority():
    from test_poller import FakeRedis, _poller

    seen: list[tuple[str, str]] = []
    inner = synthetic_handler()

    async def handler(request):
        seen.append((request.url.path, current_scope().priority))
        return await inner(request)

    poller = _poller("http://apm.test", FakeRedis(), transport=httpx.MockTransport(handler))
    await poller.poll_once()
    events = [p for path, p in seen if path == "/api/dbsearch/event"]
    assert events and set(events) == {PRIORITY_POLLER}
    # 인스턴스 명단 적재는 공유 적재 — 빈 맥락(interactive)에서 돈다(SPEC-apm-gateway §3.3)
    assert {p for path, p in seen if path in ("/api/domain", "/api/instance")} == {
        PRIORITY_INTERACTIVE
    }


@pytest.mark.asyncio
async def test_promoted_job_calls_switch_to_background_priority(tmp_path):
    seen: list[tuple[str, str]] = []
    inner = synthetic_handler()

    async def handler(request):
        seen.append((request.url.path, current_scope().priority))
        if request.url.path == XVIEW:
            await asyncio.sleep(0.04)
        return await inner(request)

    mcp, _, jobs = _server(tmp_path, handler)
    _json(await mcp.call_tool("apm_instance_map", {}))  # 명단 적재를 먼저 끝낸다
    seen.clear()
    out = _json(
        await mcp.call_tool(
            "apm_slow_transactions",
            {"hostname": "was-host01", "lookback_minutes": 4, "wait_seconds": 0.01},
        )
    )
    done = await _wait_state(mcp, out["job"]["job_id"])
    assert done["job"]["state"] == "completed"
    xview = [p for path, p in seen if path == XVIEW]
    assert xview[0] == PRIORITY_INTERACTIVE  # 승격 전 호출
    assert len(xview) == 4 and set(xview[1:]) == {PRIORITY_BACKGROUND}  # 승격 뒤 호출
    await jobs.aclose()


@pytest.mark.asyncio
async def test_aged_background_beats_later_poller():
    """기아 방지 — 에이징 2주기를 넘긴 background는 나중에 온 poller보다 먼저 나간다(같은 최고
    순위에서는 먼저 온 순서)."""
    now = [100.0]
    throttle = PriorityThrottle(rate_per_sec=10, aging_seconds=0.02, clock=lambda: now[0])
    await throttle.acquire(PRIORITY_INTERACTIVE)
    order: list[str] = []

    async def one(label: str) -> None:
        await throttle.acquire(label)
        order.append(label)

    bg = asyncio.create_task(one(PRIORITY_BACKGROUND))
    await asyncio.sleep(0)
    now[0] = 100.05
    pl = asyncio.create_task(one(PRIORITY_POLLER))
    await asyncio.sleep(0)
    now[0] = 100.1
    await asyncio.gather(bg, pl)
    assert order == [PRIORITY_BACKGROUND, PRIORITY_POLLER]


# ═══ 12. 감사 · 기록 위생 (§3.3·§3.6) ══════════════════════════════


@pytest.mark.asyncio
async def test_record_and_spool_hold_no_raw_args_or_tokens(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    mcp, _, jobs = _server(tmp_path, synthetic_handler(delay=0.01))
    out = _json(
        await mcp.call_tool(
            "apm_events",
            {
                "hostname": "was-host01",
                "wait_seconds": 0,
                "owner": "user:kim",
                "investigation_id": "inv-RAW-31337",
                "thread_id": "thr-RAW-31337",
            },
        )
    )
    done = await _wait_state(mcp, out["job"]["job_id"], "user:kim")
    assert done["job"]["state"] == "completed"
    files = [p for p in (tmp_path / "spool").rglob("*") if p.is_file()]
    assert files
    for p in files:
        text = p.read_text(encoding="utf-8")
        assert "RAW-31337" not in text and TOKEN not in text, p.name
    record = json.loads((tmp_path / "spool" / out["job"]["job_id"] / "job.json").read_text("utf-8"))
    assert record["principal"] == "anonymous" and record["owner"] == "user:kim"
    assert TOKEN not in caplog.text
    await jobs.aclose()


@pytest.mark.asyncio
async def test_job_tool_audit_has_principal_and_safe_job_id(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    mcp, _, jobs = _server(tmp_path, synthetic_handler())
    _json(await mcp.call_tool("apm_job_status", {"job_id": "../../etc/passwd"}))
    _json(await mcp.call_tool("apm_job_read", {"job_id": "f" * 32, "chunk": 0}))
    lines = _audit_lines(caplog)
    # 형식 밖 job_id 값은 감사에 싣지 않는다
    assert "tool=apm_job_status" in lines[-2] and "job_id=-" in lines[-2]
    assert "../" not in lines[-2] and "error=job_not_found" in lines[-2]
    assert f"job_id={'f' * 32}" in lines[-1] and "principal=anonymous" in lines[-1]
    assert all("api_calls=0" in x for x in lines[-2:])
    await jobs.aclose()


def _xview_slow_handler(delay: float):
    inner = synthetic_handler()

    async def handler(request):
        if request.url.path in (XVIEW, "/api/dbsearch/event", "/api/dbsearch/error"):
            await asyncio.sleep(delay)
        return await inner(request)

    return handler


@pytest.mark.asyncio
async def test_sync_audit_counts_only_its_own_calls(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    mcp, _, jobs = _server(tmp_path, _xview_slow_handler(0.03))
    _json(await mcp.call_tool("apm_instance_map", {}))
    handle = _json(
        await mcp.call_tool(
            "apm_slow_transactions",
            {"hostname": "was-host01", "lookback_minutes": 10, "wait_seconds": 0.01},
        )
    )
    out = _json(await mcp.call_tool("apm_events", {"hostname": "was-host01"}))
    assert out["row_count"] == 1
    events_line = [x for x in _audit_lines(caplog) if "tool=apm_events" in x][-1]
    try:
        assert "api_calls=2 " in events_line, events_line  # 이벤트 1 + 오류 기록 1
    finally:
        await _wait_state(mcp, handle["job"]["job_id"])
        await jobs.aclose()


@pytest.mark.asyncio
async def test_background_finish_audit_has_source_breakdown(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    mcp, _, jobs = _server(tmp_path, _xview_slow_handler(0.01))
    _json(await mcp.call_tool("apm_instance_map", {}))
    handle = _json(
        await mcp.call_tool(
            "apm_slow_transactions",
            {"hostname": "was-host01", "lookback_minutes": 3, "wait_seconds": 0},
        )
    )
    await _wait_state(mcp, handle["job"]["job_id"])
    job_id = handle["job"]["job_id"]
    finished = [
        x
        for x in _audit_lines(caplog)
        if f"job_id={job_id}" in x and "tool=apm_slow_transactions" in x
    ][-1]  # 접수 줄 다음의 종료 줄
    await jobs.aclose()
    assert "api_calls=3" in finished and "sources=default:3" in finished, finished


# ═══ 13. 실 SSE 전송 — 승격 전 끊김 · 승격 뒤 세션 종료 (§3.2) ═══════


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def _sse(app):
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    serving = asyncio.create_task(server.serve())
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/sse"
    finally:
        server.should_exit = True
        await serving


async def _sse_call(url: str, name: str, args: dict, token: str | None = None) -> dict:
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with sse_client(url, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return _json((await session.call_tool(name, args)).content)


@pytest.mark.asyncio
async def test_promoted_job_completes_after_session_closed(tmp_path):
    from apm_gateway.interface.server import build_asgi_app

    gate = asyncio.Event()
    mcp, _, jobs = _server(
        tmp_path, synthetic_handler(gate=gate, gate_paths=("/api/dbsearch/event",))
    )
    async with _sse(build_asgi_app(mcp, None)) as url:
        args = {"hostname": "was-host01", "wait_seconds": 0.05}
        handle = await _sse_call(url, "apm_events", args)
        job_id = handle["job"]["job_id"]  # 이 세션은 여기서 닫혔다
        await asyncio.sleep(0.1)
        gate.set()
        for _ in range(300):
            status = await _sse_call(url, "apm_job_status", {"job_id": job_id})
            if status["job"]["state"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.02)
        assert status["job"]["state"] == "completed" and status["total_row_count"] == 1
    await jobs.aclose()


@pytest.mark.asyncio
async def test_disconnect_before_promotion_cancels_job(tmp_path):
    from apm_gateway.interface.server import build_asgi_app

    reached = asyncio.Event()
    cancelled: list[bool] = []
    inner = synthetic_handler()

    async def handler(request):
        if request.url.path == "/api/dbsearch/event":
            reached.set()
            try:
                await asyncio.Event().wait()  # 끝나지 않는 조회
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        return await inner(request)

    mcp, _, jobs = _server(tmp_path, handler)
    async with _sse(build_asgi_app(mcp, None)) as url:
        client = asyncio.create_task(_sse_call(url, "apm_events", {"hostname": "was-host01"}))
        await asyncio.wait_for(reached.wait(), 5)
        client.cancel()  # 호출자 세션이 끊긴다(wait_seconds 없음 = 승격 없음)
        with contextlib.suppress(BaseException):
            await client
        for _ in range(300):
            if cancelled and not jobs._live:
                break
            await asyncio.sleep(0.01)
        assert cancelled == [True] and jobs._live == {}
        assert _job_dirs(tmp_path / "spool") == []
    await jobs.aclose()


@pytest.mark.asyncio
async def test_gateway_health_audit_carries_request_principal(tmp_path, caplog):
    from apm_gateway.interface.server import build_asgi_app

    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    mcp, _, jobs = _server(tmp_path, synthetic_handler())
    async with _sse(build_asgi_app(mcp, {"chat": "tok-chat-9"})) as url:
        out = await _sse_call(url, "gateway_health", {}, token="tok-chat-9")
    await jobs.aclose()
    assert out["status"] == "ok"
    line = [x for x in _audit_lines(caplog) if "tool=gateway_health" in x][-1]
    assert "principal=chat" in line, line


# ═══ 14. 실프로세스 — SIGTERM 정상 종료 (게이트웨이 SPEC §3.3 「정상 종료 때 interrupted」) ═══


@pytest.mark.asyncio
async def test_sigterm_marks_running_background_job_interrupted(
    tmp_path, mock_server_factory, synthetic_dir
):
    import os
    import signal
    import subprocess
    import sys

    base, _ = mock_server_factory(synthetic_dir, "connected")
    port = _free_port()
    spool = tmp_path / "spool"
    env = {
        **os.environ,
        "JENNIFER_SOURCES": "",
        "JENNIFER_API_URL": base,
        "JENNIFER_API_TOKEN": TOKEN,
        "JENNIFER_DOMAIN_IDS": "[]",
        "JENNIFER_RATE_LIMIT_PER_SEC": "1",
        "APM_GATEWAY_HOST": "127.0.0.1",
        "APM_GATEWAY_PORT": str(port),
        "APM_GATEWAY_BEARER_TOKEN": "",
        "APM_GATEWAY_BEARER_TOKENS": "",
        "APM_EVENT_POLLER_ENABLED": "false",
        "APM_SPOOL_DIR": str(spool),
        "APM_GATEWAY_LOG_LEVEL": "WARNING",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "apm_gateway"],
        cwd=GATEWAY_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    job_id = None
    try:
        for _ in range(150):
            with contextlib.suppress(OSError):
                socket.create_connection(("127.0.0.1", port), 0.1).close()
                break
            await asyncio.sleep(0.1)
        args = {"hostname": "was-host01", "lookback_minutes": 10, "wait_seconds": 1}
        handle = await _sse_call(f"http://127.0.0.1:{port}/sse", "apm_slow_transactions", args)
        job_id = handle["job"]["job_id"]
        assert handle["job"]["state"] == "running"
        proc.send_signal(signal.SIGTERM)
        await asyncio.to_thread(proc.wait, 20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    record = json.loads((spool / job_id / "job.json").read_text(encoding="utf-8"))
    assert record["state"] == "interrupted", record["state"]
