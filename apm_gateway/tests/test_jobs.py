"""장기 작업 — JobManager 상태 머신 · 스풀 · 소유 · 수명 (plans/134 W0-B ·
SPEC-apm-question-coverage §3.2~§3.5·§3.7 · 수용 ④).

가상 시계로 「10초 넘는 도구」·「120초 넘는 작업」·정체·만료를 재고, 도구 호출은 게이트
(`asyncio.Event`)로 붙잡는 가짜 코루틴이다. 외부 호출 0.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from apm_gateway.application.jobs import TEXT_PARTS_KEY, JobManager
from apm_gateway.application.spool import Spool, chunk_name
from apm_gateway.domain.call_context import current_scope, expect_calls
from apm_gateway.domain.errors import ApmError
from conftest import NOW_S, make_tools

from apm_gateway.config import JobConfig

TOOL = "apm_app_health"
RECORD_KEYS = {
    "job_id",
    "tool",
    "principal",
    "owner",
    "target",
    "state",
    "created_at",
    "started_at",
    "updated_at",
    "finished_at",
    "expires_at",
    "progress",
    "estimate",
    "api_calls",
    "result_meta",
    "artifact",
    "limits",
    "error",
}


class Clock:
    def __init__(self) -> None:
        self.now = NOW_S

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def env(tmp_path):
    tools, _ = make_tools("")
    clock = Clock()

    def build(**cfg) -> JobManager:
        job_cfg = JobConfig(spool_dir=tmp_path / "spool", **cfg)
        return JobManager(
            Spool(job_cfg.spool_dir),
            job_cfg,
            envelope=tools.ok,
            error_envelope=tools.err,
            rate_per_sec=5,
            clock=clock,
        )

    return tools, clock, build, tmp_path / "spool"


async def _settle(mgr: JobManager) -> None:
    for _ in range(5):
        await asyncio.sleep(0)
    if mgr._tasks:
        await asyncio.gather(*list(mgr._tasks), return_exceptions=True)


def _job_dirs(spool) -> list:
    return [p for p in spool.iterdir() if p.is_dir() and p.name != "tmp"] if spool.exists() else []


def _later(make_result, delay: float = 0.02):
    """`delay`초 걸리는 도구 호출 — `wait_seconds=0`이면 승격된다(즉시 끝나는 호출은 동기
    결과다)."""

    async def call():
        await asyncio.sleep(delay)
        return make_result()

    return call


async def _err(coro) -> ApmError:
    with pytest.raises(ApmError) as exc:
        await coro
    return exc.value


# ── 동기 실행(종전 의미) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_short_call_returns_envelope_and_writes_nothing(env):
    tools, _, build, spool = env
    mgr = build()

    async def call():
        return tools.ok(TOOL, [{"i": i} for i in range(3)])

    out = await mgr.execute(TOOL, call, principal="chat", owner="user:kim", wait_seconds=8)
    assert out["rows"] == [{"i": 0}, {"i": 1}, {"i": 2}] and out["total_row_count"] == 3
    assert "job" not in out and "artifact" not in out
    assert _job_dirs(spool) == []  # 짧은 동기 작업은 디스크 0


@pytest.mark.asyncio
async def test_without_wait_seconds_waits_until_done(env):
    """기존 소비자(조사·알람)는 wait_seconds를 넘기지 않는다 — 오래 걸려도 끝까지 기다려 결과를
    준다."""
    tools, clock, build, spool = env
    mgr = build()
    gate = asyncio.Event()

    async def call():
        await gate.wait()
        return tools.ok(TOOL, [{"i": 1}])

    async def release():
        await asyncio.sleep(0.05)
        clock.now += 600  # 10분 걸린 셈 — 10초·120초 같은 자체 상한 없음
        gate.set()

    releaser = asyncio.create_task(release())
    out = await mgr.execute(TOOL, call, principal="investigation")
    await releaser
    assert out["rows"] == [{"i": 1}] and "job" not in out and "error" not in out
    assert _job_dirs(spool) == []


# ── 승격 · 완료 · 읽기 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_long_tool_returns_handle_then_completes_after_120s(env):
    tools, clock, build, spool = env
    mgr = build()
    gate = asyncio.Event()

    async def call():
        expect_calls(4)
        for _ in range(2):
            await current_scope().before_call()  # 어댑터가 HTTP마다 부르는 훅
        await gate.wait()
        for _ in range(2):
            await current_scope().before_call()
        return tools.ok(TOOL, [{"i": i} for i in range(3)], limits=["[한계] x"])

    out = await mgr.execute(TOOL, call, principal="chat", owner="user:kim", wait_seconds=0.01)
    job = out["job"]
    assert out["rows"] == [] and out["row_count"] == 0 and "total_row_count" not in out
    assert job["state"] == "running" and len(job["job_id"]) == 32
    assert set(job) == {
        "job_id",
        "state",
        "progress",
        "estimate",
        "created_at",
        "updated_at",
        "expires_at",
    }
    assert job["progress"] == {"done": 2, "total": 4, "unit": "api_calls", "label": "API 호출"}
    assert job["estimate"] == {"api_calls": 4, "seconds": 0.8}  # 4호출 ÷ 5회/초

    st = await mgr.status(job["job_id"], principal="chat", owner="user:kim")
    assert st["job"]["state"] == "running" and st["rows"] == [] and "result_meta" not in st
    assert (await _err(mgr.read(job["job_id"], principal="chat", owner="user:kim"))).code == (
        "job_not_ready"
    )

    clock.now += 130  # 120초를 넘겨도 작업은 계속된다(일반 요청 마감과 분리 — D-299 ④)
    assert mgr.check_stalled() == []  # 정체 기준(300초) 안
    gate.set()
    await _settle(mgr)
    st = await mgr.status(job["job_id"], principal="chat", owner="user:kim")
    assert st["job"]["state"] == "completed" and st["job"]["progress"]["done"] == 4
    assert st["rows"] == [{"i": 0}, {"i": 1}, {"i": 2}] and st["total_row_count"] == 3
    assert st["result_meta"]["tool"] == TOOL and st["result_meta"]["limits"] == ["[한계] x"]
    assert "rows" not in st["result_meta"] and st["limits"] == ["[한계] x"]
    assert st["artifact"]["job_id"] == job["job_id"] and st["artifact"]["total_rows"] == 3
    read = await mgr.read(job["job_id"], principal="chat", owner="user:kim", chunk=0)
    assert read["rows"] == [{"i": 0}, {"i": 1}, {"i": 2}] and read["chunk"] == 0
    record = json.loads((spool / job["job_id"] / "job.json").read_text(encoding="utf-8"))
    assert set(record) == RECORD_KEYS  # 인자 원문·토큰 칸 없음
    assert record["principal"] == "chat" and record["owner"] == "user:kim"
    assert record["state"] == "completed" and record["api_calls"] == 4


@pytest.mark.asyncio
async def test_large_result_chunks_sum_order_and_sha256(env):
    """대량 합성 결과(100,000행) — 청크 합 = 원천 수 · 순서 보존 · sha256 일치 · 인라인 = 앞
    500행."""
    tools, _, build, spool = env
    mgr = build()
    source = [{"i": i, "name": f"row-{i}"} for i in range(100_000)]

    async def call():
        return tools.ok(TOOL, list(source))

    out = await mgr.execute(TOOL, call, principal="chat", owner="user:kim")
    art = out["artifact"]
    assert out["rows"] == source[:500] and out["row_count"] == 500
    assert out["total_row_count"] == 100_000 and out["job"]["state"] == "completed"
    assert art["total_rows"] == 100_000 and art["chunk_rows"] == 2000
    assert art["columns"] == ["i", "name"] and len(art["chunks"]) == 50
    assert sum(c["rows"] for c in art["chunks"]) == 100_000
    collected = []
    for chunk in art["chunks"]:
        data = (spool / out["job"]["job_id"] / chunk_name(chunk["index"])).read_bytes()
        assert hashlib.sha256(data).hexdigest() == chunk["sha256"] and len(data) == chunk["bytes"]
        read = await mgr.read(
            out["job"]["job_id"], principal="chat", owner="user:kim", chunk=chunk["index"]
        )
        collected += read["rows"]
    assert collected == source
    st = await mgr.status(out["job"]["job_id"], principal="chat", owner="user:kim")
    assert st["rows"] == source[:500]


@pytest.mark.asyncio
async def test_text_part_and_chunk_bounds(env):
    tools, _, build, _ = env
    mgr = build()

    async def call():
        return {
            **tools.ok("apm_transaction_profile", [{"i": i} for i in range(4500)]),
            TEXT_PARTS_KEY: {"profile": "START\n  SQL select ?\nEND"},
        }

    out = await mgr.execute("apm_transaction_profile", call, principal="chat")
    assert TEXT_PARTS_KEY not in out
    job_id = out["job"]["job_id"]
    assert [c["rows"] for c in out["artifact"]["chunks"]] == [2000, 2000, 500]
    assert out["artifact"]["text_parts"][0]["name"] == "profile"
    last = await mgr.read(job_id, principal="chat", chunk=2)
    assert [r["i"] for r in last["rows"]] == list(range(4000, 4500))
    text = await mgr.read(job_id, principal="chat", part="profile")
    assert text["text"] == "START\n  SQL select ?\nEND" and text["rows"] == []
    assert (await mgr.read(job_id, principal="chat"))["chunk"] == 0  # 둘 다 없으면 첫 청크
    bad_reads = ({"chunk": 3}, {"chunk": -1}, {"part": "../job"}, {"chunk": 0, "part": "profile"})
    for kwargs in bad_reads:
        assert (await _err(mgr.read(job_id, principal="chat", **kwargs))).code == (
            "invalid_argument"
        ), kwargs


@pytest.mark.asyncio
async def test_partial_and_failed_states(env):
    tools, _, build, _ = env
    mgr = build()

    partial_call = _later(
        lambda: tools.ok(
            TOOL, [{"i": 1}], limits=["[한계] 실시간 조회 실패(도메인 1): x"], partial=True
        )
    )
    out = await mgr.execute(TOOL, partial_call, principal="chat", wait_seconds=0)
    await _settle(mgr)
    st = await mgr.status(out["job"]["job_id"], principal="chat")
    assert st["job"]["state"] == "partial" and st["partial"] is True
    assert st["result_meta"]["partial"] is True and st["rows"] == [{"i": 1}]

    failing_call = _later(lambda: tools.err(TOOL, "source_unavailable", "APM 도메인 미접속"))
    out = await mgr.execute(TOOL, failing_call, principal="chat", wait_seconds=0)
    await _settle(mgr)
    st = await mgr.status(out["job"]["job_id"], principal="chat")
    assert st["job"]["state"] == "failed" and "error" not in st  # 상태 조회 자체는 성공
    assert st["job"]["error"] == {"code": "source_unavailable", "reason": "APM 도메인 미접속"}
    assert (await _err(mgr.read(out["job"]["job_id"], principal="chat"))).code == (
        "invalid_argument"
    )


# ── 취소 · 정체 · 재기동 · 만료 ──────────────────────────────


@pytest.mark.asyncio
async def test_cancel_running_job_and_finished_job_unchanged(env):
    tools, _, build, spool = env
    mgr = build()
    gate = asyncio.Event()
    cancelled = []

    async def call():
        try:
            await gate.wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return tools.ok(TOOL, [])

    out = await mgr.execute(TOOL, call, principal="chat", owner="o", wait_seconds=0)
    job_id = out["job"]["job_id"]
    await asyncio.sleep(0)
    res = await mgr.cancel(job_id, principal="chat", owner="o")
    assert res["job"]["state"] == "cancelled" and cancelled == [True]
    assert res["job"]["error"]["code"] == "cancelled"
    assert [p.name for p in (spool / job_id).iterdir()] == ["job.json"]  # 결과 조각 없음
    assert (await mgr.status(job_id, principal="chat", owner="o"))["job"]["state"] == "cancelled"

    quick = _later(lambda: tools.ok(TOOL, [{"i": 1}]))
    done = await mgr.execute(TOOL, quick, principal="chat", owner="o", wait_seconds=0)
    await _settle(mgr)
    again = await mgr.cancel(done["job"]["job_id"], principal="chat", owner="o")
    assert again["job"]["state"] == "completed"  # 끝난 작업의 취소는 상태를 바꾸지 않는다


@pytest.mark.asyncio
async def test_caller_cancel_before_promotion_cancels_job(env):
    tools, _, build, spool = env
    mgr = build()
    started = asyncio.Event()
    cancelled = []

    async def call():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return tools.ok(TOOL, [])

    caller = asyncio.create_task(mgr.execute(TOOL, call, principal="chat"))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await _settle(mgr)
    assert cancelled == [True] and mgr._live == {} and _job_dirs(spool) == []


@pytest.mark.asyncio
async def test_stalled_job_fails_but_active_job_does_not(env):
    tools, clock, build, _ = env
    mgr = build(stall_seconds=300)
    stuck = asyncio.Event()
    beat = asyncio.Event()

    async def stuck_call():
        await stuck.wait()
        return tools.ok(TOOL, [])

    async def busy_call():
        while not beat.is_set():
            await current_scope().before_call()  # 호출마다 임대(updated_at) 갱신
            await asyncio.sleep(0.01)
        return tools.ok(TOOL, [{"i": 1}])

    a = await mgr.execute(TOOL, stuck_call, principal="chat", wait_seconds=0)
    b = await mgr.execute(TOOL, busy_call, principal="chat", wait_seconds=0)
    await asyncio.sleep(0.02)
    clock.now += 301
    await asyncio.sleep(0.03)  # busy 작업은 그새 다시 호출했다
    assert mgr.check_stalled() == [a["job"]["job_id"]]
    beat.set()
    await _settle(mgr)
    st = await mgr.status(a["job"]["job_id"], principal="chat")
    assert st["job"]["state"] == "failed" and st["job"]["error"]["code"] == "stalled"
    assert (await mgr.status(b["job"]["job_id"], principal="chat"))["job"]["state"] == "completed"


@pytest.mark.asyncio
async def test_restart_marks_running_jobs_interrupted_and_keeps_finished(env):
    tools, _, build, spool = env
    first = build()
    gate = asyncio.Event()

    async def slow():
        await gate.wait()
        return tools.ok(TOOL, [])

    quick = _later(lambda: tools.ok(TOOL, [{"i": 7}]))
    running = await first.execute(TOOL, slow, principal="chat", owner="o", wait_seconds=0)
    done = await first.execute(TOOL, quick, principal="chat", owner="o", wait_seconds=0)
    await asyncio.sleep(0.05)
    (spool / "tmp").mkdir(parents=True, exist_ok=True)
    (spool / "tmp" / "apm-resp-left.body").write_text("{}", encoding="utf-8")

    second = build()  # 같은 스풀을 읽는 새 프로세스
    counts = second.recover()
    assert counts["interrupted"] == 1 and counts["tmp_files"] == 1
    st = await second.status(running["job"]["job_id"], principal="chat", owner="o")
    assert st["job"]["state"] == "interrupted"
    assert st["job"]["error"]["reason"] == "게이트웨이 재기동으로 중단 — 다시 요청해야 합니다"
    kept = await second.read(done["job"]["job_id"], principal="chat", owner="o")
    assert kept["rows"] == [{"i": 7}]
    gate.set()
    await first.aclose()


@pytest.mark.asyncio
async def test_expired_jobs_are_removed(env):
    tools, clock, build, spool = env
    mgr = build(retention_seconds=3600)
    quick = _later(lambda: tools.ok(TOOL, [{"i": 1}]))

    a = await mgr.execute(TOOL, quick, principal="chat", wait_seconds=0)
    b = await mgr.execute(TOOL, quick, principal="chat", wait_seconds=0)
    await _settle(mgr)
    clock.now += 3601
    err = await _err(mgr.status(a["job"]["job_id"], principal="chat"))
    assert err.code == "job_not_found" and "3600초" in err.reason
    assert not (spool / a["job"]["job_id"]).exists()
    assert mgr.sweep_expired() == [b["job"]["job_id"]] and _job_dirs(spool) == []


@pytest.mark.asyncio
async def test_other_principal_or_owner_is_job_not_found(env):
    tools, _, build, _ = env
    mgr = build()
    quick = _later(lambda: tools.ok(TOOL, [{"i": 1}]))
    out = await mgr.execute(TOOL, quick, principal="chat", owner="user:kim", wait_seconds=0)
    await _settle(mgr)
    job_id = out["job"]["job_id"]
    reasons = set()
    for kwargs in (
        {"principal": "investigation", "owner": "user:kim"},
        {"principal": "chat", "owner": "user:lee"},
        {"principal": "chat", "owner": None},
    ):
        for op in (mgr.status, mgr.read, mgr.cancel):
            err = await _err(op(job_id, **kwargs))
            assert err.code == "job_not_found"
            reasons.add(err.reason)
    for bogus in ("0" * 32, "../../etc/passwd", "", None):
        err = await _err(mgr.status(bogus, principal="chat", owner="user:kim"))
        assert err.code == "job_not_found"
        reasons.add(err.reason)
    assert len(reasons) == 1  # 없음·남의 작업·형식 밖이 같은 사유(존재 여부를 드러내지 않는다)


# ── 동시 실행 슬롯 · 비용 예측 · 메모 · 인자 ─────────────────


@pytest.mark.asyncio
async def test_concurrency_slots_are_fifo_and_queue_before_next_call(env):
    tools, _, build, _ = env
    mgr = build(max_concurrent=1)
    gate_a = asyncio.Event()
    order: list[str] = []

    def make(name, gate):
        async def call():
            await asyncio.sleep(0.01)
            await current_scope().before_call()
            if gate is not None:
                await gate.wait()
            await current_scope().before_call()
            order.append(name)
            return tools.ok(TOOL, [{"job": name}])

        return call

    a = await mgr.execute(TOOL, make("a", gate_a), principal="chat", wait_seconds=0)
    b = await mgr.execute(TOOL, make("b", None), principal="chat", wait_seconds=0)
    c = await mgr.execute(TOOL, make("c", None), principal="chat", wait_seconds=0)
    await asyncio.sleep(0.05)
    assert a["job"]["state"] == "running" and b["job"]["state"] == "queued"
    assert (await mgr.status(b["job"]["job_id"], principal="chat"))["job"]["state"] == "queued"
    assert mgr.summary() == {"running": 1, "queued": 2, "slots_in_use": 1, "max_concurrent": 1}
    gate_a.set()
    await _settle(mgr)
    assert order == ["a", "b", "c"]
    for out in (a, b, c):
        st = await mgr.status(out["job"]["job_id"], principal="chat")
        assert st["job"]["state"] == "completed"


@pytest.mark.asyncio
async def test_unknown_plan_has_null_total_and_notes_join_limits(env):
    tools, _, build, _ = env
    mgr = build()

    async def call():
        await asyncio.sleep(0.02)
        await current_scope().before_call()
        current_scope().note("[한계] 자격증명 검사: 예상 밖 응답 모양($.a) — 비밀 패턴 키만 검사")
        return tools.ok(TOOL, [{"i": 1}], limits=["[한계] 기존"])

    out = await mgr.execute(TOOL, call, principal="chat", wait_seconds=0)
    assert out["job"]["progress"]["total"] is None and out["job"]["estimate"] == {
        "api_calls": None,
        "seconds": None,
    }
    await _settle(mgr)
    st = await mgr.status(out["job"]["job_id"], principal="chat")
    assert st["limits"] == [
        "[한계] 기존",
        "[한계] 자격증명 검사: 예상 밖 응답 모양($.a) — 비밀 패턴 키만 검사",
    ]
    assert st["job"]["progress"]["done"] == 1 and st["job"]["progress"]["total"] is None


@pytest.mark.asyncio
async def test_bad_job_arguments(env):
    tools, _, build, _ = env
    mgr = build()

    async def call():
        return tools.ok(TOOL, [])

    assert (await _err(mgr.execute(TOOL, call, principal="c", wait_seconds=-1))).code == (
        "invalid_argument"
    )
    assert (await _err(mgr.execute(TOOL, call, principal="c", owner="x" * 201))).code == (
        "invalid_argument"
    )
