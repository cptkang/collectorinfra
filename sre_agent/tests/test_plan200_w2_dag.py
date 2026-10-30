"""plans/200 W2(I-2) — 의존 기반 DAG 실행기(`dag_scheduling` · 기본 off).

- 동등성: 녹화 픽스처 전부(plan187 + plan200)에서 off와 on(축 동시 off·on)의 최종 결과
  (섹션·판독·가설·notes·`tool_calls` 순서)가 같다(경과 시간 칸 제외) · 재조회도 같다.
- `max_workers=1`이면 DAG도 YAML 순서 호출(off와 실제 호출 순서 동일).
- 지연 주입 시 on 벽시계 < off.
- 타임아웃 슬롯 반환 · 중단 신호 · 재시도 연계 · 예산 소진 · 잡 종료(`stop`) · 범위 규칙 ·
  `when` 거짓·판정 불가 · 조상 한정 `prior`·단계 끝 해석 입력(Q2).
- dispatcher 배선: 설정 → `run_playbook` 인자 · `_deep_job(parallel_tools)`.

실 LLM·MCP 0 — 녹화 조회·가짜 소견기·가짜 스텝 함수만 쓴다.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sre_agent.application import investigation_dispatcher as disp_mod
from sre_agent.application.playbook_narrator import StepOpinionStream
from sre_agent.application.playbook_runner import (
    STEP_TIMEOUT_PREFIX,
    alarm_facts_from_payload,
    run_playbook,
)
from sre_agent.application.playbook_schedule import ancestors, narration_basis
from sre_agent.application.playbook_steps import STEP_LIBRARY, RunScope, StepResult
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.domain.playbook import parse_playbook, select_playbook
from sre_agent.domain.step_findings import BUDGET_SKIP
from sre_agent.infrastructure.activity_masking import mask_activity_text
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller
from test_plan187_w4_dispatch import FakeLLM, build, make_job, make_settings, run_job
from test_plan196_sre_screen_defects import _LINKED, _latency_alarm

_TESTS = Path(__file__).resolve().parent
FIXTURES: dict[str, Path] = {
    p.stem: p
    for d in (_TESTS / "fixtures" / "plan187", _TESTS / "fixtures" / "plan200")
    for p in sorted(d.glob("*.json"))
}
NAMES = sorted(FIXTURES)
ON_VARIANTS = {
    "dag": {"dag_scheduling": True},
    "dag_axis": {"dag_scheduling": True, "axis_concurrency": True},
}


def _fx(name: str) -> dict:
    return json.loads(FIXTURES[name].read_text(encoding="utf-8"))


def _pb(pid: str):  # noqa: ANN202
    return next(p for p in PLAYBOOKS if p.id == pid)


def _run(fx: dict, caller: Any, **kw: Any):  # noqa: ANN202
    pb, matched_by = select_playbook(alarm_facts_from_payload(fx["alarm"]), PLAYBOOKS)
    kw.setdefault(
        "polestar_source", None if fx["expected"].get("polestar_unresolved") else "polestar"
    )
    return run_playbook(fx["alarm"], pb, matched_by=matched_by, polestar=caller, apm=caller, **kw)


def _comparable(run: Any) -> dict:
    """최종 결과 — 경과 시간 칸(`timings` · 섹션 `elapsed_ms`)만 뺀다."""
    d = run.to_dict()
    d.pop("timings")
    for s in d["sections"]:
        s.pop("elapsed_ms", None)
    return d


def _call_multiset(caller: FakeCaller) -> list[str]:
    return sorted(json.dumps([t, a], sort_keys=True, ensure_ascii=False) for t, a in caller.calls)


def _mini(steps: list[dict]):  # noqa: ANN202
    return parse_playbook({
        "id": "was.mini", "version": 1, "match": {"resource_type": "apm.Instance"},
        "steps": [*steps, {"id": "S9", "action": "synthesize"}],
        "runbook": {"meaning": "m", "impact": "i", "owner": "o", "last_reviewed": "2026-10-30"},
    })


# ── 동등성 · 비트 동일 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("variant", sorted(ON_VARIANTS))
@pytest.mark.parametrize("name", NAMES)
def test_dag_on_matches_off_on_all_fixtures(name: str, variant: str) -> None:
    fx = _fx(name)
    off_caller, on_caller = FakeCaller(fx, strict=False), FakeCaller(fx, strict=False)
    off = _run(fx, off_caller, synthesize=True)
    on = _run(fx, on_caller, synthesize=True, **ON_VARIANTS[variant])
    # 섹션 · 판독 · 가설 순위(synthesis) · notes · tool_calls(스텝별 순서 포함) · 범위
    assert _comparable(on) == _comparable(off)
    assert _call_multiset(on_caller) == _call_multiset(off_caller)
    assert len(on_caller.unrecorded) == len(off_caller.unrecorded)


@pytest.mark.parametrize("name", NAMES)
def test_single_slot_dag_keeps_yaml_call_order(name: str) -> None:
    fx = _fx(name)
    off_caller, on_caller = FakeCaller(fx, strict=False), FakeCaller(fx, strict=False)
    off = _run(fx, off_caller, max_workers=1)
    on = _run(fx, on_caller, max_workers=1, dag_scheduling=True)
    assert on_caller.calls == off_caller.calls  # 실제 호출 순서까지 같다
    assert _comparable(on) == _comparable(off)


@pytest.mark.parametrize("pid", ["was.latency", "was.sql", "was.fallback"])
def test_rerun_hint_same_on_and_off(pid: str) -> None:
    fx = _fx("latency_sql")
    pb = _pb(pid)
    link = _LINKED | {"server_name": "wasdemo01"}
    parent = run_playbook(_latency_alarm(link), pb, matched_by="t", polestar=FakeCaller(fx),
                          apm=FakeCaller(fx), max_workers=1, for_each_max=1)
    alarm = _latency_alarm(link)
    alarm["meta"]["hints"]["rerun"] = {
        "parent_investigation_id": "inv-parent", "axes": ["polestar"],
        "prior_sections": parent.to_briefing_dict()["sections"],
        "prior_readings": parent.to_briefing_dict()["readings"],
    }
    runs, callers = [], []
    for kw in ({}, {"dag_scheduling": True}):
        caller = FakeCaller(fx, strict=False)
        runs.append(run_playbook(alarm, pb, matched_by="t", polestar=caller, apm=caller,
                                 for_each_max=1, **kw))
        callers.append(caller)
    off, on = runs
    assert _comparable(on) == _comparable(off)
    assert _call_multiset(callers[1]) == _call_multiset(callers[0])
    assert any(s.reused_from == "inv-parent" for s in on.sections)


def test_stage_wall_is_per_topological_level() -> None:
    fx = _fx("host_cpu")
    on = _run(fx, FakeCaller(fx), dag_scheduling=True)
    # host.cpu 위상 레벨: {S4,S6,S0r} · {S1,S2,S5,S7} · {S3a} · {S3b}
    assert len(on.stage_wall_s) == 4
    assert "stage_wall_s" not in on.to_dict()


# ── 시간 ───────────────────────────────────────────────────────────────────


class _SlowCaller(FakeCaller):
    def __init__(self, fixture: dict, delay: float) -> None:
        super().__init__(fixture, strict=False)
        self.delay = delay

    def __call__(self, calls):  # noqa: ANN001, ANN204
        time.sleep(self.delay)
        return super().__call__(calls)


def test_dag_wall_clock_shorter_than_stages_with_delay() -> None:
    fx = _fx("host_cpu")
    walls = {}
    for dag in (False, True):
        t0 = time.monotonic()
        _run(fx, _SlowCaller(fx, 0.1), dag_scheduling=dag)
        walls[dag] = time.monotonic() - t0
    # 단계 장벽(S4,S6 | S0r | S1,S2 | S3a | S3b,S5,S7)이 사라져 S5·S7이 S1·S2와 겹친다
    assert walls[True] < walls[False] * 0.9, walls


# ── 타임아웃 · 재시도 · 예산 · 잡 종료 ─────────────────────────────────────


def _indep_playbook():  # noqa: ANN202
    return _mini([
        {"id": "S4", "action": "host.usage_trend"},
        {"id": "S6", "action": "host.process_top"},
        {"id": "S7", "action": "change.recent"},
    ])


def test_timed_out_step_returns_slot_immediately_and_aborts() -> None:
    release = threading.Event()
    seen: dict[str, Any] = {}

    def hang(ctx):  # noqa: ANN001, ANN202
        seen["S4"] = ctx
        release.wait(5)
        return StepResult(status="ok")

    def quick(sid: str):  # noqa: ANN202
        def fn(ctx):  # noqa: ANN001, ANN202
            seen[sid] = ctx
            return StepResult(status="ok")
        return fn

    lib = {"host.usage_trend": hang, "host.process_top": quick("S6"), "change.recent": quick("S7")}
    fx = _fx("normal")
    t0 = time.monotonic()
    try:
        run = run_playbook(fx["alarm"], _indep_playbook(), matched_by="t", polestar=None,
                           apm=None, steps=lib, max_workers=1, step_timeout_s=0.2,
                           dag_scheduling=True)
        elapsed = time.monotonic() - t0
    finally:
        release.set()
    assert elapsed < 2.0
    a = run.section("S4")
    assert a.status == "missing" and a.reason.startswith(STEP_TIMEOUT_PREFIX)
    assert seen["S4"].cancel.is_set()  # 중단 신호(이후 새 호출 0)
    # 슬롯이 즉시 반환돼 S6이 S4 마감 직후 시작(S4 스레드는 아직 붙잡혀 있다)
    b_wait = seen["S6"].started_at - seen["S4"].started_at
    assert 0.15 <= b_wait < 1.0, b_wait
    assert run.section("S6").status == "ok" and run.section("S7").status == "ok"
    assert seen["S4"].step_id == "S4" and seen["S6"].step_id == "S6"  # 스텝 ID 선지정


def test_timed_out_step_is_retried_after_dag() -> None:
    release = threading.Event()
    attempts: list[str] = []

    def flaky(ctx):  # noqa: ANN001, ANN202
        attempts.append(ctx.retry_of or "first")
        if ctx.retry_of is None:
            release.wait(5)
        return StepResult(status="ok")

    lib = {"host.usage_trend": flaky, "host.process_top": lambda ctx: StepResult(status="ok"),
           "change.recent": lambda ctx: StepResult(status="ok")}
    fx = _fx("normal")
    try:
        run = run_playbook(fx["alarm"], _indep_playbook(), matched_by="t", polestar=None,
                           apm=None, steps=lib, max_workers=2, step_timeout_s=0.2,
                           dag_scheduling=True, retry_timed_out=True)
    finally:
        release.set()
    a = run.section("S4")
    assert a.status == "ok" and a.reason.startswith("재시도 1회 — 성공")
    assert attempts[0] == "first" and attempts[1].startswith(STEP_TIMEOUT_PREFIX)


def test_budget_exhaustion_skips_waiting_and_descendant_steps() -> None:
    t = {"now": 0.0}

    def slow(ctx):  # noqa: ANN001, ANN202
        t["now"] += 200.0  # 주입 시계 — 예산을 넘긴다
        return StepResult(status="ok", verdict="degraded")

    lib = {"apm.golden_signals": slow, "change.recent": lambda ctx: StepResult(status="ok"),
           "apm.slow_tx_breakdown": lambda ctx: StepResult(status="ok")}
    pb = _mini([
        {"id": "S1", "action": "apm.golden_signals"},
        {"id": "S7", "action": "change.recent"},
        {"id": "S3", "action": "apm.slow_tx_breakdown", "when": "S1.verdict != 'normal'"},
    ])
    skips: list[tuple[str, str]] = []
    fx = _fx("normal")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=None, steps=lib,
                       clock=lambda: t["now"], budget_s=120.0, max_workers=1,
                       dag_scheduling=True, on_skip=lambda res, why: skips.append((res.step, why)))
    assert run.section("S1").status == "ok"
    for sid in ("S7", "S3"):  # S7 = 슬롯 대기 중 소진 · S3 = 의존 완료 시점에 소진
        sec = run.section(sid)
        assert sec.status == "skipped" and sec.reason.startswith("budget"), sid
    assert sorted(skips) == [("S3", BUDGET_SKIP), ("S7", BUDGET_SKIP)]
    assert run.timings["budget_exceeded"] is True


def test_stop_signal_ends_dag_without_new_tool_calls() -> None:
    fx = _fx("host_cpu")
    caller = FakeCaller(fx, strict=False)
    pb = _pb("host.cpu")
    d = build(make_settings(sre_playbook_dag_scheduling_enabled=True), caller)
    stop = threading.Event()
    lib = dict(d._progress_library(make_job(fx["alarm"]), pb, "t", {}, stop))
    inner = lib["scope.host_to_was"]
    n_calls: dict[str, int] = {}

    def scope_then_stop(ctx):  # noqa: ANN001, ANN202
        res = inner(ctx)
        stop.set()  # 잡 종료 — 이후 스텝은 시작하지 않는다
        n_calls["at_stop"] = len(caller.calls)
        return res

    lib["scope.host_to_was"] = scope_then_stop
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=caller, apm=caller, steps=lib,
                       polestar_source="polestar", dag_scheduling=True)
    for sid in ("S1", "S2", "S5", "S7"):  # S0r의 후손 — 시작 즉시 종료 사유
        assert run.section(sid).reason == disp_mod.STEP_STOPPED_REASON, sid
    late = [c for c in caller.calls[n_calls["at_stop"]:]
            if not c[0].startswith(("polestar_metric", "polestar_process"))]
    assert late == []  # S4·S6(동시 실행 중이던 앞 스텝) 외 새 호출 0


# ── 결정성(Q2) — when · 범위 · prior ──────────────────────────────────────


@pytest.mark.parametrize(
    ("verdict", "status", "note"),
    [("degraded", "ok", None), ("normal", "skipped", "when_false"), (None, "ok", "when 판정 불가")],
)
def test_when_three_valued_in_dag(verdict: str | None, status: str, note: str | None) -> None:
    lib = {"apm.golden_signals": lambda ctx: StepResult(status="ok", verdict=verdict),
           "apm.slow_tx_breakdown": lambda ctx: StepResult(status="ok")}
    pb = _mini([
        {"id": "S1", "action": "apm.golden_signals"},
        {"id": "S3", "action": "apm.slow_tx_breakdown", "when": "S1.verdict != 'normal'"},
    ])
    fx = _fx("normal")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=None, steps=lib,
                       dag_scheduling=True)
    s3 = run.section("S3")
    assert s3.status == status
    assert (s3.reason is None) if note is None else (note in s3.reason)


def test_step_before_scope_step_uses_initial_scope_even_if_submitted_later() -> None:
    """S0r보다 YAML 앞 스텝(S6)은 슬롯 사정으로 S0r 완료 뒤 제출돼도 초기 범위를 쓴다
    (off와 같다)."""
    new_scope = RunScope(host="NEW-HOST", alarm_instance="X1")
    events: list[str] = []
    seen: dict[str, Any] = {}

    def record(sid: str, delay: float = 0.0, **res_kw: Any):  # noqa: ANN202
        def fn(ctx):  # noqa: ANN001, ANN202
            seen[sid] = ctx.scope
            time.sleep(delay)
            events.append(sid)
            return StepResult(status="ok", **res_kw)
        return fn

    lib = {
        "host.usage_trend": record("S4", 0.2),
        "host.process_top": record("S6"),
        "scope.host_to_was": record("S0r", verdict="degraded", artifacts={"scope": new_scope}),
        "apm.golden_signals": record("S1"),
    }
    pb = _mini([
        {"id": "S4", "action": "host.usage_trend"},
        {"id": "S6", "action": "host.process_top", "when": "S4.status == 'ok'"},
        {"id": "S0r", "action": "scope.host_to_was"},
        {"id": "S1", "action": "apm.golden_signals"},
    ])
    fx = _fx("normal")
    scopes = {}
    for dag in (False, True):
        events.clear()
        run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=None, steps=lib,
                           max_workers=2, dag_scheduling=dag)
        scopes[dag] = {sid: seen[sid].host for sid in ("S6", "S1")}
        if dag:
            assert events.index("S0r") < events.index("S6")  # S6은 S0r 완료 뒤 시작
            assert run.scope["host"] == "NEW-HOST"  # 최종 범위 = 대상 확정 결과
    assert scopes[True] == scopes[False]
    assert scopes[True]["S6"] != "NEW-HOST" and scopes[True]["S1"] == "NEW-HOST"


def test_prior_and_when_context_limited_to_ancestors() -> None:
    """S3a(`apm.active_services`)는 S2(`runtime_and_pool`) 결과를 받고, 먼저 끝난 비조상 S5·S7은
    받지 않는다."""
    fx = _fx("host_cpu")
    pb = _pb("host.cpu")
    seen: dict[str, Any] = {}
    ended: dict[str, float] = {}

    def tap(action: str, sid: str, delay: float = 0.0):  # noqa: ANN202
        fn = STEP_LIBRARY[action]

        def run_(ctx):  # noqa: ANN001, ANN202
            seen[sid] = (time.monotonic(), dict(ctx.prior))
            time.sleep(delay)
            try:
                return fn(ctx)
            finally:
                ended[sid] = time.monotonic()
        return run_

    lib = dict(STEP_LIBRARY)
    lib["apm.runtime_and_pool"] = tap("apm.runtime_and_pool", "S2", 0.3)
    lib["apm.active_services"] = tap("apm.active_services", "S3a")
    lib["xsource.related_alarms"] = tap("xsource.related_alarms", "S5")
    lib["change.recent"] = tap("change.recent", "S7")
    caller = FakeCaller(fx, strict=False)
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=caller, apm=caller, steps=lib,
                       polestar_source="polestar", dag_scheduling=True)
    started_s3a, prior = seen["S3a"]
    assert ended["S5"] < started_s3a and ended["S7"] < started_s3a  # 먼저 끝났지만
    assert set(prior) == set(ancestors(pb)["S3a"])  # 조상만
    assert prior["S2"].action == "apm.runtime_and_pool" and prior["S2"].status == "ok"
    assert run.section("S3a").status == "ok"


# ── dispatcher ──────────────────────────────────────────────────────────────


def _host_cpu_job_with_activity(settings: Any, monkeypatch: pytest.MonkeyPatch,
                                requests: list) -> Any:
    fx = _fx("host_cpu")
    original = StepOpinionStream.submit

    def capture(self, request, on_done=None):  # noqa: ANN001, ANN202
        requests.append(request)
        return original(self, request, on_done)

    monkeypatch.setattr(StepOpinionStream, "submit", capture)
    slow = dict(disp_mod.STEP_LIBRARY)
    inner = slow["apm.runtime_and_pool"]

    def slow_pool(ctx):  # noqa: ANN001, ANN202
        time.sleep(0.3)  # S5·S7이 S3a보다 먼저 끝나게
        return inner(ctx)

    slow["apm.runtime_and_pool"] = slow_pool
    monkeypatch.setattr(disp_mod, "STEP_LIBRARY", slow)
    d = build(settings, FakeCaller(fx, strict=False), llm=FakeLLM())
    alarm = json.loads(json.dumps(fx["alarm"]))
    alarm.setdefault("meta", {})["episode"] = {"episode_id": "ep", "infra_db_ids": ["polestar"],
                                               "members": []}
    job = make_job(alarm)
    job.activity = ActivityLog(masker=mask_activity_text)
    d(job)
    d.wait_workers(30)
    return job


def test_dispatcher_step_narration_inputs_limited_to_ancestors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list = []
    settings = make_settings(sre_playbook_dag_scheduling_enabled=True,
                             sre_activity_log_enabled=True)
    job = _host_cpu_job_with_activity(settings, monkeypatch, requests)
    by_step = {r.step: r for r in requests}
    assert "S3a" in by_step, sorted(by_step)
    anc = ancestors(_pb("host.cpu"))
    for sid, req in by_step.items():
        quoted = {line.split(" ", 1)[0] for line in req.prior_findings}
        assert quoted <= anc[sid], (sid, quoted)
    ends = [e for e in job.activity.since(0)["events"] if e.get("kind") == "step_end"]
    order = [e["step"] for e in ends]
    assert order.index("S5") < order.index("S3a")  # S5 결과 한 줄은 이미 있었다
    assert any(e["step"] == "S5" and e.get("finding") for e in ends)


def test_dispatcher_briefing_same_on_and_off(monkeypatch: pytest.MonkeyPatch) -> None:
    blocks = []
    for dag in (False, True):
        settings = make_settings(sre_playbook_dag_scheduling_enabled=dag,
                                 sre_activity_log_enabled=True)
        job = _host_cpu_job_with_activity(settings, monkeypatch, [])
        block = dict(job.briefing["playbook"])
        block.pop("timings")
        for s in block["sections"]:
            s.pop("elapsed_ms", None)
        blocks.append(block)
    assert blocks[1] == blocks[0]


def test_dispatcher_passes_scheduling_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []
    real = disp_mod.run_playbook

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(disp_mod, "run_playbook", spy)
    fx = _fx("latency_sql")
    from test_plan187_w4_dispatch import alarm_of

    run_job(build(make_settings(), FakeCaller(fx, strict=False)), alarm_of("latency_sql"))
    run_job(build(make_settings(sre_playbook_max_workers=3,
                                sre_playbook_dag_scheduling_enabled=True,
                                sre_playbook_axis_concurrency_enabled=True),
                  FakeCaller(fx, strict=False)), alarm_of("latency_sql"), iid="inv-2")
    pick = [(k["max_workers"], k["dag_scheduling"], k["axis_concurrency"]) for k in seen]
    assert pick == [(4, False, False), (3, True, True)]


@pytest.mark.parametrize("flag", [False, True])
def test_deep_job_passes_parallel_tools(monkeypatch: pytest.MonkeyPatch, flag: bool) -> None:
    seen: list[bool] = []
    real = disp_mod.deep_dive_note

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen.append(kwargs.get("parallel_tools"))
        return real(*args, **kwargs)

    monkeypatch.setattr(disp_mod, "deep_dive_note", spy)
    from test_plan187_w4_dispatch import alarm_of

    fx = _fx("latency_sql")
    settings = make_settings(sre_deep_dive_mode="parallel",
                             sre_guidance_parallel_tools_enabled=flag)
    run_job(build(settings, FakeCaller(fx, strict=False)), alarm_of("latency_sql"))
    assert seen and all(v is flag for v in seen)


# ── 단계 끝 해석 미루기(W2 재교정 — 실행은 미루지 않는다) ─────────────────────


def _flow_library(d: Any, job: Any, pb: Any, stop: threading.Event | None = None):  # noqa: ANN202
    hooks = disp_mod._FlowHooks()
    lib = d._progress_library(job, pb, "t", {}, stop or threading.Event(), hooks=hooks)
    return dict(lib), hooks


def _activity_job(alarm: dict) -> Any:
    job = make_job(alarm)
    job.activity = ActivityLog(masker=mask_activity_text)
    return job


def _step_ends(job: Any) -> dict[str, dict]:
    return {e["step"]: e for e in job.activity.since(0)["events"] if e.get("kind") == "step_end"}


def test_deferred_narration_does_not_hold_the_worker() -> None:
    """S2의 해석 기준(S4 · cpu_owner 입력)이 아직 돌면 S2 결과는 곧바로 실행기로 가고, 단계 끝은
    S4가 정착할 때 낸다 — 그때 S2 결과 한 줄은 S4를 반영한다."""
    fx = _fx("host_cpu")
    pb = _pb("host.cpu")
    caller = FakeCaller(fx, strict=False)
    d = build(make_settings(sre_playbook_dag_scheduling_enabled=True,
                            sre_activity_log_enabled=True), caller)
    job = _activity_job(fx["alarm"])
    lib, hooks = _flow_library(d, job, pb)
    assert hooks.flush is not None
    took: dict[str, float] = {}
    usage, pool = lib["host.usage_trend"], lib["apm.runtime_and_pool"]

    def slow_usage(ctx):  # noqa: ANN001, ANN202
        time.sleep(0.5)
        return usage(ctx)

    def timed_pool(ctx):  # noqa: ANN001, ANN202
        t0 = time.monotonic()
        try:
            return pool(ctx)
        finally:
            took["S2"] = time.monotonic() - t0

    lib["host.usage_trend"], lib["apm.runtime_and_pool"] = slow_usage, timed_pool
    run_playbook(fx["alarm"], pb, matched_by="t", polestar=caller, apm=caller, steps=lib,
                 polestar_source="polestar", dag_scheduling=True, activity=job.activity,
                 on_skip=hooks.on_skip)
    assert took["S2"] < 0.3, took  # S4(0.5s)를 기다리지 않았다
    events = [e for e in job.activity.since(0)["events"] if e.get("kind") == "step_end"]
    order = [e["step"] for e in events]
    assert order.index("S4") < order.index("S2")  # 단계 끝은 기준 정착 뒤
    assert "CPU" in (_step_ends(job)["S2"].get("finding") or "")


def test_flush_runs_waiting_bundles_on_rerun_without_deadlock() -> None:
    """재조회 재사용 스텝은 정착 신호가 오지 않는다 — 그 스텝을 기준으로 둔 단계 끝은 수집 뒤
    flush가 낸다(대기 0 · 교착 없음)."""
    fx = _fx("latency_sql")
    pb = _pb("was.latency")
    link = _LINKED | {"server_name": "wasdemo01"}
    parent = run_playbook(_latency_alarm(link), pb, matched_by="t", polestar=FakeCaller(fx),
                          apm=FakeCaller(fx), max_workers=1, for_each_max=1)
    alarm = _latency_alarm(link)
    alarm["meta"]["hints"]["rerun"] = {
        "parent_investigation_id": "inv-parent", "axes": ["polestar"],
        "prior_sections": parent.to_briefing_dict()["sections"],
        "prior_readings": parent.to_briefing_dict()["readings"],
    }
    caller = FakeCaller(fx, strict=False)
    d = build(make_settings(sre_playbook_dag_scheduling_enabled=True,
                            sre_activity_log_enabled=True), caller)
    job = _activity_job(alarm)
    lib, hooks = _flow_library(d, job, pb)
    t0 = time.monotonic()
    run = run_playbook(alarm, pb, matched_by="t", polestar=caller, apm=caller, steps=lib,
                       for_each_max=1, dag_scheduling=True, activity=job.activity,
                       on_skip=hooks.on_skip)
    assert time.monotonic() - t0 < 5.0
    ran = [s.step for s in run.sections if s.reused_from is None and s.action != "synthesize"]
    assert ran, "폴스타 축 스텝이 돌아야 한다"
    basis = narration_basis(pb)
    waiting = [sid for sid in ran if sid not in _step_ends(job)]
    # 재사용 스텝을 기준으로 둔 단계 끝은 아직 대기 중이다
    assert waiting and all(basis[sid] - set(ran) for sid in waiting)
    assert hooks.flush is not None
    hooks.flush()
    assert [sid for sid in ran if sid not in _step_ends(job)] == []  # 대기 0
    hooks.flush()  # 두 번 불러도 무동작
    assert len(_step_ends(job)) == len(ran)


def _fixture_alarm(fx: dict) -> dict:
    alarm = json.loads(json.dumps(fx["alarm"]))
    if not fx["expected"].get("polestar_unresolved"):
        alarm.setdefault("meta", {})["episode"] = {"episode_id": "ep",
                                                   "infra_db_ids": ["polestar"], "members": []}
    return alarm


def _flow_trace(name: str, settings: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict]:
    """실행 기록 on 조사 1건 → (스텝 → 결과 한 줄, 스텝 → 소견 요청 입력(앞 단계 결과 줄 제외))."""
    requests: list = []
    original = StepOpinionStream.submit

    def capture(self, request, on_done=None):  # noqa: ANN001, ANN202
        requests.append(request)
        return original(self, request, on_done)

    with monkeypatch.context() as mp:
        mp.setattr(StepOpinionStream, "submit", capture)
        fx = _fx(name)
        d = build(settings, FakeCaller(fx, strict=False), llm=FakeLLM())
        job = _activity_job(_fixture_alarm(fx))
        d(job)
        d.wait_workers(30)
    findings = {sid: e.get("finding") for sid, e in _step_ends(job).items()}
    asked = {}
    for r in requests:
        section = dict(r.section)
        section.pop("elapsed_ms", None)
        asked[r.step] = (section, r.focus, r.max_sentences, r.readings, r.realtime_badges)
    return findings, asked


@pytest.mark.parametrize("name", NAMES)
def test_activity_on_step_findings_and_requests_same_as_off(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """결과 한 줄 · 소견 요청(섹션·판독·초점)이 단계 실행과 같다. off는 슬롯 1(YAML 순서 —
    같은 단계 안 완료 순서에 따른 off 자체의 흔들림을 없앤 기준). 앞 단계 결과 줄은 Q2로
    조상만이라 비교에서 뺀다."""
    off = _flow_trace(name, make_settings(sre_activity_log_enabled=True,
                                          sre_playbook_max_workers=1), monkeypatch)
    on = _flow_trace(name, make_settings(sre_activity_log_enabled=True,
                                         sre_playbook_dag_scheduling_enabled=True), monkeypatch)
    assert on[0] == off[0]
    assert on[1] == off[1]
