"""plans/200 W3 — 스텝 안 축 동시 조회(`StepContext.call_axes` · I-3).

플래그(`axis_concurrency`) off면 축마다 `call()`을 차례로 부른다(종전 비트 동일). on이면 축별 배치
콜러블 호출만 동시에 내고 기록·적재는 고정 축 순서로 한다. 대상 스텝은 S5 `related_alarms`·S7
`recent_changes`다. 녹화 픽스처·가짜 콜러블만 쓴다(실 MCP·LLM 호출 0).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Any

import pytest
from sre_agent.application.playbook_steps import (
    APM_SOURCE_UNRESOLVED_LIMIT,
    STEP_ABORTED,
    STEP_LIBRARY,
    TOOL_ALARMS,
    TOOL_CHANGES,
    RunScope,
    RunWindows,
    StepContext,
    recent_changes,
    related_alarms,
)
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.infrastructure.activity_masking import mask_activity_text
from test_plan187_w2_runner import SCENARIOS, FakeCaller, _load, _run

AXIS_ACTIONS = ("xsource.related_alarms", "change.recent")
#: S5·S7이 부르는 도구(지연 주입 대상).
AXIS_TOOLS = frozenset(
    {TOOL_ALARMS, TOOL_CHANGES, "apm_events", "apm_source_changes", "apm_change_impact"}
)


# ── 단위: call_axes ──────────────────────────────────────────────────────────


class Batch:
    """가짜 축 배치 콜러블 — 지연·예외·중단·응답 누락·캐시 적중을 주입한다."""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        exc: Exception | None = None,
        short: int = 0,
        barrier: threading.Barrier | None = None,
        on_call: Any = None,
        cached: bool | None = None,
    ) -> None:
        self.delay = delay
        self.exc = exc
        self.short = short
        self.barrier = barrier
        self.on_call = on_call
        self.batches: list[list[tuple[str, dict]]] = []
        self.finished = threading.Event()
        if cached is not None:
            self.cached = lambda tool, args: cached

    def __call__(self, calls):
        self.batches.append(list(calls))
        try:
            if self.barrier is not None:
                self.barrier.wait()
            if self.delay:
                time.sleep(self.delay)
            if self.on_call is not None:
                self.on_call()
            if self.exc is not None:
                raise self.exc
            out = [json.dumps({"rows": [{"tool": t}]}) for t, _ in calls]
            return out[: len(out) - self.short]
        finally:
            self.finished.set()


def _ctx(polestar, apm, *, on: bool, activity: ActivityLog | None = None) -> StepContext:
    windows = RunWindows(
        alarm_time="2026-10-19T10:00:00",
        reference_time="2026-10-19T10:10:00",
        lookback_minutes=40,
        incident_start="2026-10-19T09:30:00",
        incident_end="2026-10-19T10:10:00",
    )
    ctx = StepContext(
        payload={}, windows=windows, scope=RunScope(), args={}, targets=[],
        polestar=polestar, apm=apm, now=datetime.now, prior={}, axis_concurrency=on,
    )
    ctx.activity = activity
    ctx.step_id = "S5"
    return ctx


P_CALLS = [("polestar_a", {"x": 1})]
A_CALLS = [("apm_b", {"y": 2}), ("apm_c", {"y": 3})]


def _events(log: ActivityLog) -> list[tuple[str, str]]:
    return [(e["kind"], e["tool"]) for e in log.since(0)["events"]]


def _state(ctx: StepContext) -> tuple:
    return (ctx.tools, ctx.calls, ctx.outputs, ctx.requested, ctx.completed, ctx.inflight)


def test_default_is_off() -> None:
    ctx = StepContext(
        payload={}, windows=_ctx(None, None, on=False).windows, scope=RunScope(), args={},
        targets=[], polestar=None, apm=None, now=datetime.now, prior={},
    )
    assert ctx.axis_concurrency is False


def test_on_calls_axes_concurrently_off_sequentially() -> None:
    # on: 두 축이 서로를 기다리는 장벽을 통과한다 = 실제 동시 호출
    bar = threading.Barrier(2, timeout=5)
    ctx = _ctx(Batch(barrier=bar), Batch(barrier=bar), on=True)
    out = ctx.call_axes([("polestar", P_CALLS), ("apm", A_CALLS)])
    assert [[r.tool for r in g] for g in out] == [["polestar_a"], ["apm_b", "apm_c"]]
    assert all(r.ok for g in out for r in g)
    # off: 축 순차 — 첫 축이 장벽에서 홀로 기다리다 깨진다
    bar_off = threading.Barrier(2, timeout=0.3)
    ctx_off = _ctx(Batch(barrier=bar_off), Batch(barrier=bar_off), on=False)
    with pytest.raises(threading.BrokenBarrierError):
        ctx_off.call_axes([("polestar", P_CALLS), ("apm", A_CALLS)])


def test_on_matches_off_results_and_records_in_axis_order() -> None:
    results = {}
    logs = {}
    for on in (False, True):
        log = ActivityLog(masker=mask_activity_text)
        # 앞 축(폴스타)이 더 늦게 돌아와도 기록은 축 순서
        ctx = _ctx(Batch(delay=0.1), Batch(), on=on, activity=log)
        out = ctx.call_axes([("polestar", P_CALLS), ("apm", A_CALLS)])
        results[on] = (out, _state(ctx))
        logs[on] = log
    assert results[True] == results[False]
    assert results[True][1][1] == [("polestar_a", {"x": 1}), ("apm_b", {"y": 2}),
                                   ("apm_c", {"y": 3})]
    off, on_ev = _events(logs[False]), _events(logs[True])
    assert off == [("tool_start", "polestar_a"), ("tool_end", "polestar_a"),
                   ("tool_start", "apm_b"), ("tool_start", "apm_c"),
                   ("tool_end", "apm_b"), ("tool_end", "apm_c")]
    # on: 시작은 축 순서로 호출 전 · 끝은 전부 돌아온 뒤 축 순서(개수 동일)
    assert on_ev == [("tool_start", "polestar_a"), ("tool_start", "apm_b"),
                     ("tool_start", "apm_c"), ("tool_end", "polestar_a"),
                     ("tool_end", "apm_b"), ("tool_end", "apm_c")]
    assert sorted(off) == sorted(on_ev)
    ends = {e["tool"]: e for e in logs[True].since(0)["events"] if e["kind"] == "tool_end"}
    # 축별 소요는 그 축 호출 소요
    assert ends["polestar_a"]["elapsed_ms"] >= 80
    assert ends["apm_b"]["elapsed_ms"] < 80
    assert {e["status"] for e in ends.values()} == {"success"}


def test_cancel_before_start_calls_nothing() -> None:
    log = ActivityLog(masker=mask_activity_text)
    p, a = Batch(), Batch()
    ctx = _ctx(p, a, on=True, activity=log)
    ctx.cancel.set()
    out = ctx.call_axes([("polestar", P_CALLS), ("apm", A_CALLS)])
    assert [r.error for g in out for r in g] == [STEP_ABORTED] * 3
    assert p.batches == [] and a.batches == []
    assert _state(ctx) == ([], [], [], 0, 0, [])
    assert _events(log) == []


def test_cancel_after_return_records_nothing_but_counts() -> None:
    for on in (False, True):
        log = ActivityLog(masker=mask_activity_text)
        holder: dict[str, StepContext] = {}
        p = Batch(on_call=lambda: holder["ctx"].abort("타임아웃"))
        ctx = _ctx(p, Batch(), on=on, activity=log)
        holder["ctx"] = ctx
        out = ctx.call_axes([("polestar", P_CALLS), ("apm", A_CALLS)])
        assert [r.error for g in out for r in g] == [STEP_ABORTED] * 3
        assert (ctx.tools, ctx.calls, ctx.outputs, ctx.inflight) == ([], [], [], [])
        assert ctx.requested == ctx.completed
        assert not [e for e in _events(log) if e[0] == "tool_end"]
    # on은 두 축을 이미 함께 냈으므로 둘 다 계수된다(off는 둘째 축을 부르지 않는다)
    assert ctx.requested == 3


def test_unconfigured_axis_records_error_end_like_off() -> None:
    results = {}
    logs = {}
    for on in (False, True):
        log = ActivityLog(masker=mask_activity_text)
        ctx = _ctx(Batch(), None, on=on, activity=log)
        out = ctx.call_axes([("apm", A_CALLS), ("polestar", P_CALLS)])
        results[on] = (out, _state(ctx))
        logs[on] = log
    assert results[True] == results[False]
    out = results[True][0]
    assert [r.error for r in out[0]] == ["APM 게이트웨이 미구성"] * 2
    assert out[1][0].ok
    off, on_ev = _events(logs[False]), _events(logs[True])
    assert sorted(off) == sorted(on_ev)
    assert [t for k, t in on_ev if k == "tool_end"] == ["apm_b", "apm_c", "polestar_a"]


def test_axis_exception_waits_others_then_reraises_with_off_records() -> None:
    # 앞 축 정상 · 뒤 축 예외 — 앞 축은 off처럼 기록되고 같은 예외가 나간다
    states = {}
    for on in (False, True):
        ctx = _ctx(Batch(delay=0.1), Batch(exc=RuntimeError("boom")), on=on)
        with pytest.raises(RuntimeError, match="boom"):
            ctx.call_axes([("polestar", P_CALLS), ("apm", A_CALLS)])
        states[on] = (ctx.tools, ctx.calls, ctx.outputs, ctx.inflight)
    assert states[True] == states[False]
    assert states[True][1] == [("polestar_a", {"x": 1})]
    # 앞 축 예외 — 뒤 축 호출이 끝날 때까지 기다린 뒤 다시 낸다. 실제로 부른 뒤 축은 적재된다
    # (on 전용 차이 — off는 뒤 축을 부르지 않는다 · 팀 리드 결정)
    slow = Batch(delay=0.15)
    ctx = _ctx(Batch(exc=ValueError("first")), slow, on=True)
    with pytest.raises(ValueError, match="first"):
        ctx.call_axes([("polestar", P_CALLS), ("apm", A_CALLS)])
    assert slow.finished.is_set()
    assert ctx.calls == A_CALLS and ctx.tools == ["apm_b", "apm_c"] and ctx.inflight == []
    assert ctx.requested == 3 and ctx.completed == 2


def test_on_axis_exception_records_end_for_every_called_axis() -> None:
    # 부른 축은 모두 끝 기록(「진행 중」 잔류 없음) · 부르지 않은 축은 예외 뒤로 기록 없음
    log = ActivityLog(masker=mask_activity_text)
    ctx = _ctx(Batch(exc=ValueError("first")), None, on=True, activity=log)
    groups = [("polestar", P_CALLS), ("apm", A_CALLS)]
    # 폴스타 예외 + 제니퍼 미구성: 예외 뒤 미구성 축은 기록하지 않는다
    with pytest.raises(ValueError, match="first"):
        ctx.call_axes(groups)
    assert _events(log) == [("tool_start", "polestar_a"), ("tool_end", "polestar_a")]
    end = log.since(0)["events"][-1]
    assert end["status"] == "error" and "ValueError: first" in end["excerpt"]
    assert ctx.calls == []
    # 뒤 축 예외: 앞 축은 적재 + 끝 기록, 예외 축도 끝 기록(status=error)
    log2 = ActivityLog(masker=mask_activity_text)
    ctx2 = _ctx(Batch(), Batch(exc=RuntimeError("boom")), on=True, activity=log2)
    with pytest.raises(RuntimeError, match="boom"):
        ctx2.call_axes(groups)
    ends = [(e["tool"], e["status"]) for e in log2.since(0)["events"] if e["kind"] == "tool_end"]
    assert ends == [("polestar_a", "success"), ("apm_b", "error"), ("apm_c", "error")]
    assert ctx2.calls == P_CALLS


def test_missing_responses_padded_and_cached_status() -> None:
    results = {}
    for on in (False, True):
        log = ActivityLog(masker=mask_activity_text)
        ctx = _ctx(Batch(cached=True), Batch(short=1), on=on, activity=log)
        out = ctx.call_axes([("polestar", P_CALLS), ("apm", A_CALLS)])
        ends = [e for e in log.since(0)["events"] if e["kind"] == "tool_end"]
        results[on] = (out, _state(ctx), [(e["tool"], e["status"]) for e in ends])
    assert results[True] == results[False]
    out, _, ends = results[True]
    assert out[1][1].error == "응답 누락"
    assert ends == [("polestar_a", "cached"), ("apm_b", "success"), ("apm_c", "error")]


def test_empty_groups_and_calls() -> None:
    p, a = Batch(), Batch()
    ctx = _ctx(p, a, on=True)
    assert ctx.call_axes([]) == []
    assert ctx.call_axes([("polestar", []), ("apm", [])]) == [[], []]
    assert p.batches == [] and a.batches == []


def _step_ctx(polestar, apm, *, on: bool) -> StepContext:
    """S5·S7용 문맥 — 폴스타 확정 · 대상 1개(APM 소스 미확정 → `_apm_args`가 한계를 남긴다)."""
    ctx = _ctx(polestar, apm, on=on)
    ctx.scope = RunScope(polestar_source="polestar", polestar_server="srv01")
    ctx.targets, ctx.target_sources = ["inst01"], [None]
    return ctx


@pytest.mark.parametrize(
    ("step", "raising", "limits", "tools"),
    [
        # HEAD: S5는 폴스타 호출 뒤에 제니퍼 인자를 만든다 — 폴스타 예외면 한계·기록 0
        (related_alarms, "polestar", [], []),
        (related_alarms, "apm", [APM_SOURCE_UNRESOLVED_LIMIT], [TOOL_ALARMS]),
        # HEAD: S7은 제니퍼 인자(한계)를 먼저 만들고 제니퍼 → 폴스타 순으로 부른다
        (recent_changes, "apm", [APM_SOURCE_UNRESOLVED_LIMIT], []),
        (recent_changes, "polestar", [APM_SOURCE_UNRESOLVED_LIMIT],
         ["apm_source_changes", "apm_change_impact"]),
    ],
)
def test_off_axis_exception_keeps_head_order(step, raising, limits, tools) -> None:
    bad = Batch(exc=RuntimeError("boom"))
    pol, apm = (bad, Batch()) if raising == "polestar" else (Batch(), bad)
    ctx = _step_ctx(pol, apm, on=False)
    with pytest.raises(RuntimeError, match="boom"):
        step(ctx)
    assert ctx.limits == limits
    assert ctx.tools == tools
    assert [t for t, _ in ctx.calls] == tools
    if step is related_alarms and raising == "polestar":
        assert apm.batches == []   # 종전처럼 제니퍼 축은 부르지 않는다
    if step is recent_changes and raising == "apm":
        assert pol.batches == []


# ── 녹화 픽스처: S5·S7 off/on 동등 ───────────────────────────────────────────


class DelayCaller(FakeCaller):
    """S5·S7 도구가 든 배치만 `delay`초 늦게 돌려주는 녹화 콜러블."""

    def __init__(self, fixture: dict, delay: float) -> None:
        super().__init__(fixture, strict=False)
        self.delay = delay

    def __call__(self, calls):
        if self.delay and any(t in AXIS_TOOLS for t, _ in calls):
            time.sleep(self.delay)
        return super().__call__(calls)


def _run_axis(name: str, on: bool, delay: float = 0.0):
    """픽스처를 실행하되 S5·S7 문맥에 플래그·실행 기록을 달고 문맥을 잡아 둔다."""
    fx = _load(name)
    log = ActivityLog(masker=mask_activity_text)
    caps: dict[str, StepContext] = {}

    def wrap(action: str):
        fn = STEP_LIBRARY[action]

        def step(ctx: StepContext):
            ctx.axis_concurrency = on
            ctx.activity = log
            ctx.step_id = action
            caps[action] = ctx
            return fn(ctx)

        return step

    lib = dict(STEP_LIBRARY)
    for action in AXIS_ACTIONS:
        lib[action] = wrap(action)
    run = _run(fx, DelayCaller(fx, delay), steps=lib)
    return run, caps, log


def _strip_elapsed(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_elapsed(v)
            for k, v in value.items()
            if "elapsed" not in k and k != "timings"
        }
    if isinstance(value, list):
        return [_strip_elapsed(v) for v in value]
    return value


@pytest.mark.parametrize("name", SCENARIOS)
def test_fixture_off_on_equivalent(name: str) -> None:
    run_off, caps_off, log_off = _run_axis(name, False)
    run_on, caps_on, log_on = _run_axis(name, True)
    assert caps_off.keys() == caps_on.keys()
    assert caps_on, "S5·S7 중 하나는 실행돼야 한다"
    assert _strip_elapsed(run_on.to_dict()) == _strip_elapsed(run_off.to_dict())
    for action in caps_on:
        off, on = caps_off[action], caps_on[action]
        sec_off = next(s for s in run_off.sections if s.action == action)
        sec_on = next(s for s in run_on.sections if s.action == action)
        assert _strip_elapsed(sec_on.to_dict()) == _strip_elapsed(sec_off.to_dict())
        assert (on.tools, on.calls, on.outputs, on.limits) == (
            off.tools, off.calls, off.outputs, off.limits
        )

        def ev(log: ActivityLog, step: str = action) -> list[tuple]:
            return [
                (e["kind"], e["tool"], e.get("status"), e.get("rows"), e.get("excerpt"))
                for e in log.since(0)["events"] if e.get("step") == step
            ]

        e_off, e_on = ev(log_off), ev(log_on)
        assert sorted(e_off, key=repr) == sorted(e_on, key=repr)
        for kind in ("tool_start", "tool_end"):
            assert [e for e in e_on if e[0] == kind] == [e for e in e_off if e[0] == kind]
        # on: 시작이 전부 끝보다 앞선다(동시 호출)
        kinds = [e[0] for e in e_on]
        if kinds:
            assert kinds == sorted(kinds, key=lambda k: k != "tool_start")


def test_fixture_two_axes_exercised() -> None:
    _, caps, _ = _run_axis("case3_cpu_loop", True)
    for action in AXIS_ACTIONS:
        tools = caps[action].tools
        assert any(t.startswith("polestar_") for t in tools)
        assert any(t.startswith("apm_") for t in tools)


def test_delay_injection_on_shortens_s5_s7() -> None:
    delay = 0.2
    run_off, _, _ = _run_axis("case3_cpu_loop", False, delay)
    run_on, _, _ = _run_axis("case3_cpu_loop", True, delay)
    for action in AXIS_ACTIONS:
        off = next(s for s in run_off.sections if s.action == action).elapsed_ms
        on = next(s for s in run_on.sections if s.action == action).elapsed_ms
        assert off is not None and on is not None
        assert off >= 2 * delay * 1000 * 0.9, (action, off)
        assert on < off * 0.75, (action, off, on)
