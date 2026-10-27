"""plans/187 W21 독립 검증(verifier) — 수집 타임아웃 개선의 경합·경계.

가짜 배치 콜러블·대역 세션·mcp 메모리 세션만 쓴다(실 MCP 서버·LLM 호출 0).
`xfail(strict=True)`는 결함 재현 — 고쳐지면 XPASS로 실패해 표시를 걷어내게 한다.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import pytest
from sre_agent.application.playbook_runner import compute_windows, run_playbook
from sre_agent.application.playbook_steps import RunScope, StepContext, StepResult
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.domain.playbook import WindowSpec, parse_playbook
from sre_agent.infrastructure import mcp_tool_client as mc
from sre_agent.infrastructure.activity_masking import mask_activity_text
from test_plan187_w2_runner import _load
from test_plan187_w21_batch_concurrency import _calls, _install

RUNBOOK = {"meaning": "m", "impact": "i", "owner": "o", "last_reviewed": "2026-10-22"}
_ACTIONS = ["apm.golden_signals", "apm.runtime_and_pool", "apm.active_services",
            "apm.events_errors", "change.recent", "xsource.related_alarms"]


def _pb(steps: list[dict]) -> Any:
    return parse_playbook({
        "id": "was.w21v", "version": 1, "match": {"resource_type": "apm.Instance"},
        "steps": [*steps, {"id": "S9", "action": "synthesize"}], "runbook": RUNBOOK,
    })


# ── ① 배치 동시 상한 — 벽시계 ≈ ceil(n/c) × 지연 ──────────────────────────────


@pytest.mark.parametrize(("n", "c", "rounds"), [(10, 4, 3), (13, 4, 4), (5, 16, 1)])
def test_batch_wall_clock_is_ceil_n_over_c(monkeypatch, n, c, rounds):
    d = 0.08
    obs = _install(monkeypatch, delays={f"t{i}": d for i in range(n)})
    t0 = time.perf_counter()
    out = mc.run_tool_batch("http://x/sse", None, _calls(n), concurrency=c)
    elapsed = time.perf_counter() - t0
    assert [json.loads(o)["tool"] for o in out] == [f"t{i}" for i in range(n)]
    assert obs["peak"] == min(n, c)
    assert rounds * d * 0.9 <= elapsed < rounds * d + 0.15, elapsed


def test_real_mcp_client_session_concurrent_calls_map_by_request_id():
    """mcp 1.25 실물 ClientSession — 한 세션 동시 call_tool이 요청 id별로 제 응답을 받는다."""
    from mcp.server.fastmcp import FastMCP
    from mcp.shared.memory import create_connected_server_and_client_session

    srv = FastMCP("w21v")

    @srv.tool()
    async def echo(k: int, wait: float) -> str:
        await asyncio.sleep(wait)
        return f"k={k}"

    async def main() -> list[str]:
        async with create_connected_server_and_client_session(srv) as s:
            sem = asyncio.Semaphore(4)

            async def one(k: int) -> str:
                async with sem:
                    r = await s.call_tool("echo", {"k": k, "wait": 0.02 * (8 - k)},
                                          read_timeout_seconds=timedelta(seconds=5))
                    return r.content[0].text

            return list(await asyncio.gather(*(one(k) for k in range(8))))

    assert asyncio.run(main()) == [f"k={k}" for k in range(8)]


# ── ③ 중단 경합 ───────────────────────────────────────────────────────────────


class _Gate:
    """첫 배치를 `release`까지 붙잡는 배치 콜러블 — 호출 수신 시각을 남긴다."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.sent: list[tuple[str, float]] = []

    def __call__(self, calls):  # noqa: ANN001
        out = []
        for tool, _ in calls:
            self.sent.append((tool, time.monotonic()))
            self.release.wait(3)
            out.append(json.dumps({"rows": []}))
        return out


def _ctx(apm: Any, log: ActivityLog | None) -> StepContext:
    fx = _load("latency_sql")
    ctx = StepContext(payload=fx["alarm"], windows=compute_windows(fx["alarm_time"], WindowSpec()),
                      scope=RunScope(alarm_instance="A1"), args={}, targets=["A1"],
                      polestar=None, apm=apm, now=datetime.now, prior={})
    ctx.activity, ctx.step_id = log, "S0"
    return ctx


def test_abort_concurrent_with_inflight_batch_keeps_counters_consistent():
    """진행 중 배치에 abort가 겹쳐도 inflight 제거 예외 없음 · 계수 정합."""
    gate = _Gate()
    ctx = _ctx(gate, None)
    t = threading.Thread(target=ctx.call, args=("apm", [("apm_app_health", {"i": 1}),
                                                        ("apm_app_health", {"i": 2})]))
    t.start()
    time.sleep(0.05)
    assert ctx.progress_text() == "요청 2건 중 0건 완료 · 대기 apm_app_health"
    ctx.abort("x")
    gate.release.set()
    t.join(3)
    assert (ctx.requested, ctx.completed, ctx.inflight) == (2, 2, [])


def test_late_batch_after_abort_is_not_recorded():
    gate = _Gate()
    log = ActivityLog(masker=mask_activity_text)
    ctx = _ctx(gate, log)
    t = threading.Thread(target=ctx.call, args=("apm", [("apm_app_health", {"i": 1})]))
    t.start()
    time.sleep(0.05)
    ctx.abort("스텝 타임아웃 0.05s — 요청 1건 중 0건 완료")
    n_at_abort = len(log.since(0)["events"])
    gate.release.set()
    t.join(3)
    late = log.since(0)["events"][n_at_abort:]
    assert [e["kind"] for e in late] == []  # 실측: ['tool_end']
    assert ctx.calls == [] and ctx.outputs == []


@pytest.mark.xfail(strict=True, reason="알려진 한계(W21 · 배치 단위 중단)")
def test_no_gateway_send_after_timeout_within_one_batch():
    sent: list[float] = []

    def seq_batch(calls):  # noqa: ANN001 — concurrency=1 배치와 같은 모양(차례로 송신)
        out = []
        for _ in calls:
            sent.append(time.monotonic())
            time.sleep(0.1)
            out.append(json.dumps({"rows": []}))
        return out

    def one_batch(ctx: StepContext) -> StepResult:
        ctx.call("apm", [("apm_app_health", {"i": i}) for i in range(6)])
        return StepResult(status="ok")

    pb = _pb([{"id": "S0", "action": "scope.resolve_was_targets", "timeout_s": 0.25}])
    fx = _load("latency_sql")
    t0 = time.monotonic()
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=seq_batch,
                       steps={"scope.resolve_was_targets": one_batch})
    timed_out_at = t0 + 0.25
    time.sleep(0.6)
    assert run.section("S0").status == "missing"
    assert [s for s in sent if s > timed_out_at + 0.02] == []  # 실측: 3건 안팎


# ── ② 대기 상한 — 제출 시각 기준이 큐 대기까지 잰다 ──────────────────────────


def test_queued_step_in_wide_stage_not_cut_by_queue_wait():
    def work(ctx: StepContext) -> StepResult:
        time.sleep(0.3)
        return StepResult(status="ok")

    pb = _pb([{"id": f"S{i + 1}", "action": a, "timeout_s": 0.45}
              for i, a in enumerate(_ACTIONS[:5])])
    fx = _load("latency_sql")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=None,
                       steps={a: work for a in _ACTIONS[:5]})  # max_workers 기본 4
    assert [run.section(f"S{i}").status for i in range(1, 6)] == ["ok"] * 5


def test_timed_out_thread_does_not_starve_next_stage():
    gate = _Gate()

    def hang_scope(ctx: StepContext) -> StepResult:
        ctx.call("apm", [("apm_app_health", {"i": 0})])
        return StepResult(status="ok")

    def quick(ctx: StepContext) -> StepResult:
        time.sleep(0.3)
        return StepResult(status="ok")

    pb = _pb([{"id": "S0", "action": "scope.resolve_was_targets", "timeout_s": 0.2},
              *({"id": f"S{i + 1}", "action": a, "timeout_s": 0.45}
                for i, a in enumerate(_ACTIONS[:4]))])
    fx = _load("latency_sql")
    steps = {"scope.resolve_was_targets": hang_scope, **{a: quick for a in _ACTIONS[:4]}}
    timer = threading.Timer(1.5, gate.release.set)
    timer.start()
    try:
        run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=gate, steps=steps)
    finally:
        gate.release.set()
        timer.cancel()
    assert run.section("S0").status == "missing"
    assert [run.section(f"S{i}").status for i in range(1, 5)] == ["ok"] * 4


# ── P2 미적용 흔적 ────────────────────────────────────────────────────────────


def test_no_p2_hostname_bundling_trace():
    import inspect

    from sre_agent.application import playbook_steps as ps

    src = inspect.getsource(ps)
    assert "W21 P2" not in src
    # app_health는 종전대로 인스턴스별(instance_name) 호출만 만든다
    fx = _load("latency_sql")
    ctx = _ctx(None, None)
    call = ps._incident_health_call(ctx, "A1")
    assert call[0] == ps.APM_APP_HEALTH and "hostname" not in call[1]
    assert fx  # 픽스처 로드 확인
