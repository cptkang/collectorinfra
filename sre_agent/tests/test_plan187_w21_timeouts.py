"""plans/187 W21 — 수집 타임아웃 개선 P3~P6(단계별 타임아웃 · 부분 결과 보존·중단 · 소견기
타임아웃 설정화 · 폴스타 소스 설정 매핑).

가짜 배치 콜러블(지연 주입)·가짜 LLM만 쓴다 — 실 MCP·LLM 호출 0.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import threading
import time
from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher, PlaybookWiring
from sre_agent.application.playbook_narrator import NarratorLimits
from sre_agent.application.playbook_runner import (
    DEFAULT_STEP_TIMEOUT_S,
    POLESTAR_BRIDGE_MISSING,
    POLESTAR_MAPPED_BASIS,
    apm_source_id,
    compute_windows,
    mapped_polestar_source,
    resolve_polestar_source,
    run_playbook,
    step_wait_s,
)
from sre_agent.application.playbook_steps import (
    STEP_ABORTED,
    RunScope,
    StepContext,
    StepResult,
)
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.domain.playbook import WindowSpec, parse_playbook
from sre_agent.infrastructure.activity_masking import mask_activity_text
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load
from test_plan187_w4_dispatch import FakeDeep, FakeLLM, alarm_of, make_job, make_settings

RUNBOOK = {"meaning": "m", "impact": "i", "owner": "o", "last_reviewed": "2026-10-22"}


def _pb(steps: list[dict], pid: str = "was.w21") -> Any:
    return parse_playbook({
        "id": pid, "version": 1, "match": {"resource_type": "apm.Instance"},
        "steps": [*steps, {"id": "S9", "action": "synthesize"}], "runbook": RUNBOOK,
    })


def _playbook(pid: str) -> Any:
    return next(p for p in PLAYBOOKS if p.id == pid)


def _log() -> ActivityLog:
    return ActivityLog(masker=mask_activity_text)


class SlowCaller:
    """호출 1건마다 `delay`초 쉬는 가짜 배치 콜러블 — 실제로 받은 호출을 기록한다."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, calls):  # noqa: ANN001
        out = []
        for tool, args in calls:
            with self._lock:
                self.calls.append((tool, dict(args)))
            time.sleep(self.delay)
            out.append(json.dumps({"rows": []}))
        return out


# ── 설정 ────────────────────────────────────────────────────────────────────


def test_settings_defaults_and_bounds() -> None:
    s = AgentSettings(_env_file=None, model="test/model")
    assert s.sre_tool_batch_concurrency == 4  # 사용자 확정 예외(기본 4)
    assert s.sre_narrator_step_timeout_s == 60.0  # W22 45 → 60
    assert s.sre_playbook_polestar_source_map == {}
    for bad in ({"sre_tool_batch_concurrency": 0}, {"sre_tool_batch_concurrency": 9},
                {"sre_narrator_step_timeout_s": 0}):
        with pytest.raises(ValidationError):
            AgentSettings(_env_file=None, model="test/model", **bad)


def test_settings_source_map_reads_json_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRE_PLAYBOOK_POLESTAR_SOURCE_MAP", '{"cm_prd":"polestar_cm_gp"}')
    s = AgentSettings(_env_file=None, model="test/model")
    assert s.sre_playbook_polestar_source_map == {"cm_prd": "polestar_cm_gp"}


# ── P3 단계별 타임아웃 ───────────────────────────────────────────────────────


def test_step_spec_timeout_optional_and_positive() -> None:
    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 12.5},
              {"id": "S2", "action": "apm.runtime_and_pool"}])
    assert [s.timeout_s for s in pb.steps] == [12.5, None, None]
    with pytest.raises(ValidationError):
        _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0}])


def test_yaml_scope_steps_have_120s_and_versions_bumped() -> None:
    was = _playbook("was.latency")
    assert was.version == 3  # W21 2 → W22 S0 120s 3
    assert next(s for s in was.steps if s.id == "S0").timeout_s == 120
    for pid in ("host.cpu", "host.process"):
        pb = _playbook(pid)
        assert pb.version == 4  # W21 3 → W22 S0r 120s 4
        assert next(s for s in pb.steps if s.id == "S0r").timeout_s == 120
    # 나머지 스텝은 실행기 기본값
    assert all(s.timeout_s is None for s in was.steps if s.id != "S0")


def test_step_wait_is_min_of_step_timeout_and_remaining_budget() -> None:
    s0 = next(s for s in _playbook("was.latency").steps if s.id == "S0")
    s1 = next(s for s in _playbook("was.latency").steps if s.id == "S1")
    assert step_wait_s(s0, DEFAULT_STEP_TIMEOUT_S, 240.0) == 120  # 예산 240s 안에서 120s(W22)
    assert step_wait_s(s0, DEFAULT_STEP_TIMEOUT_S, 45.0) == 45.0  # 남은 예산을 넘지 않는다
    assert step_wait_s(s1, DEFAULT_STEP_TIMEOUT_S, 240.0) == DEFAULT_STEP_TIMEOUT_S
    assert step_wait_s(s1, DEFAULT_STEP_TIMEOUT_S, -1.0) == 0.0


def test_scope_step_timeout_capped_by_remaining_budget_in_run() -> None:
    """S0 timeout_s 60이어도 남은 예산(0.3s)이 상한 — 실행이 그 안에 끝난다."""
    release = threading.Event()

    def hang(ctx: StepContext) -> StepResult:
        release.wait(3)
        return StepResult(status="ok")

    pb = _pb([{"id": "S0", "action": "scope.resolve_was_targets", "timeout_s": 60}])
    fx = _load("latency_sql")
    t0 = time.monotonic()
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=None,
                       steps={"scope.resolve_was_targets": hang}, budget_s=0.3)
    release.set()
    assert time.monotonic() - t0 < 1.0
    s0 = run.section("S0")
    assert s0.status == "missing" and s0.reason.startswith("스텝 타임아웃 0.3s — ")


def test_step_timeouts_measured_from_stage_submission_not_accumulated() -> None:
    """같은 단계 두 스텝이 모두 걸려도 대기는 상한 1회분(종전은 차례로 기다려 2회분)."""
    release = threading.Event()

    def hang(ctx: StepContext) -> StepResult:
        release.wait(3)
        return StepResult(status="ok")

    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.3},
              {"id": "S2", "action": "apm.runtime_and_pool", "timeout_s": 0.3}])
    fx = _load("latency_sql")
    t0 = time.monotonic()
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=None,
                       steps={"apm.golden_signals": hang, "apm.runtime_and_pool": hang},
                       max_workers=2)
    elapsed = time.monotonic() - t0
    release.set()
    assert elapsed < 0.55, elapsed
    assert {run.section("S1").status, run.section("S2").status} == {"missing"}


def test_later_step_not_cut_by_earlier_wait() -> None:
    """앞 스텝이 상한까지 걸려도 뒤 스텝은 자기 상한(제출 시각 기준) 안에 끝나면 ok."""
    release = threading.Event()

    def hang(ctx: StepContext) -> StepResult:
        release.wait(3)
        return StepResult(status="ok")

    def slowish(ctx: StepContext) -> StepResult:
        time.sleep(0.35)
        return StepResult(status="ok")

    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.2},
              {"id": "S2", "action": "apm.runtime_and_pool", "timeout_s": 1.0}])
    fx = _load("latency_sql")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=None,
                       steps={"apm.golden_signals": hang, "apm.runtime_and_pool": slowish},
                       max_workers=2)
    release.set()
    assert run.section("S1").status == "missing"
    assert run.section("S2").status == "ok"


# ── P4 부분 결과 보존 + 중단 ─────────────────────────────────────────────────


def _ctx(apm: Any, activity: ActivityLog | None = None) -> StepContext:
    fx = _load("latency_sql")
    ctx = StepContext(payload=fx["alarm"], windows=compute_windows(fx["alarm_time"], WindowSpec()),
                      scope=RunScope(alarm_instance="A1"), args={}, targets=["A1"],
                      polestar=None, apm=apm, now=datetime.now, prior={})
    ctx.activity, ctx.step_id = activity, "S0"
    return ctx


def test_context_abort_short_circuits_calls_without_recording() -> None:
    caller = SlowCaller(0.0)
    log = _log()
    ctx = _ctx(caller, log)
    ctx.call("apm", [("apm_app_health", {"instance_name": "A1"}),
                     ("apm_instance_map", {"query": "A1"})])
    assert (ctx.requested, ctx.completed, ctx.inflight) == (2, 2, [])
    assert ctx.progress_text() == "요청 2건 중 2건 완료"
    before = len(log.since(0)["events"])
    seen: list[str] = []
    ctx.on_abort = seen.append
    ctx.abort("스텝 타임아웃 1s — x")
    out = ctx.call("apm", [("apm_app_health", {"instance_name": "A2"})])
    assert [r.error for r in out] == [STEP_ABORTED]
    assert len(caller.calls) == 2  # 중단 뒤 호출 0
    assert len(log.since(0)["events"]) == before  # tool_start/tool_end 기록 0
    assert ctx.tools == ["apm_app_health", "apm_instance_map"]
    assert seen == ["스텝 타임아웃 1s — x"]


def test_runner_timeout_reason_has_progress_and_stops_new_calls() -> None:
    caller = SlowCaller(0.1)

    def loop(ctx: StepContext) -> StepResult:
        for i in range(10):
            ctx.call("apm", [("apm_app_health", {"instance_name": f"I{i}"})])
        return StepResult(status="ok")

    pb = _pb([{"id": "S0", "action": "scope.resolve_was_targets", "timeout_s": 0.25}])
    fx = _load("latency_sql")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=caller,
                       steps={"scope.resolve_was_targets": loop})
    s0 = run.section("S0")
    m = re.fullmatch(r"스텝 타임아웃 0\.25s — 요청 (\d+)건 중 (\d+)건 완료 · 대기 apm_app_health",
                     s0.reason or "")
    assert m, s0.reason
    requested, completed = int(m.group(1)), int(m.group(2))
    assert requested == completed + 1 and 1 <= completed <= 3
    assert s0.status == "missing" and s0.tools == ["apm_app_health"]  # 끝난 호출의 도구
    time.sleep(0.4)  # 걸려 있던 호출 1건이 끝날 시간 — 그 뒤 새 호출은 없어야 한다
    assert len(caller.calls) == requested
    # 대상 확정 타임아웃 — 범위는 초기 범위(알람 인스턴스 단독) 그대로
    assert [t["instance"] for t in run.scope["targets"]] == ["A1"]


class _DelayedHealth(FakeCaller):
    """녹화 조회 + `apm_app_health`만 호출당 지연."""

    def __init__(self, fx: dict, delay: float) -> None:
        super().__init__(fx)
        self.delay = delay
        self.job: Any = None
        self.snaps: list[dict] = []

    def __call__(self, calls):  # noqa: ANN001
        if self.job is not None and self.job.partial_briefing:
            self.snaps.append(self.job.partial_briefing)
        out = []
        for tool, args in calls:
            if tool == "apm_app_health":
                time.sleep(self.delay)
            out.extend(super().__call__([(tool, args)]))
        return out


def _dispatcher(settings: AgentSettings, caller: Any, playbooks: Any, *, llm: Any = None,
                limits: NarratorLimits | None = None) -> InvestigationDispatcher:
    wiring = PlaybookWiring(playbooks=tuple(playbooks), polestar=caller, apm=caller, llm=llm,
                            **({"narrator_limits": limits} if limits is not None else {}))
    return InvestigationDispatcher(settings, diagnose_fn=FakeDeep(settings),
                                   briefing_fn=build_briefing, playbook=wiring)


def test_timeout_reason_reaches_activity_flow_and_partial_briefing() -> None:
    """S0 타임아웃 사유가 실행 기록 step_end.finding · 부분 브리핑 · 최종 섹션에 그대로 실린다."""
    pb = _pb([{"id": "S0", "action": "scope.resolve_was_targets", "timeout_s": 0.3,
               "narrate": {"focus": "판정 분포"}},
              {"id": "S1", "action": "apm.golden_signals", "for_each": "targets"}])
    fx = _load("latency_sql")
    caller = _DelayedHealth(fx, 0.25)
    job = make_job(alarm_of("latency_sql"), "inv-w21-p4")
    job.activity = _log()
    caller.job = job
    # W21 계약(첫 시도 중단) 확인 — W22 결손 스텝 재시도는 끈다(재시도 경로는 test_plan187_w22_*)
    d = _dispatcher(make_settings(sre_playbook_retry_timed_out_steps=False), caller, [pb])
    d(job)
    d.wait_workers(30)
    s0 = next(s for s in job.briefing["playbook"]["sections"] if s["step"] == "S0")
    assert s0["status"] == "missing"
    assert re.match(r"스텝 타임아웃 0\.3s — 요청 \d+건 중 \d+건 완료 · 대기 apm_app_health",
                    s0["reason"]), s0["reason"]
    ends = [e for e in job.activity.since(0)["events"]
            if e["kind"] == "step_end" and e.get("step") == "S0"]
    assert len(ends) == 1  # 늦게 끝난 스텝 스레드가 단계 끝을 다시 내지 않는다
    assert ends[0]["status"] == "missing"
    assert ends[0]["finding"] == f"수집 결손 — {s0['reason']}"
    # 수집 중 부분 브리핑(S1 조회 시점)에 S0 사유·결과 한 줄이 이미 실려 있다
    partial = [{s["step"]: s for s in snap["playbook"]["sections"]} for snap in caller.snaps]
    with_s0 = [p["S0"] for p in partial if "S0" in p]
    assert with_s0 and with_s0[0]["status"] == "missing"
    assert with_s0[0]["reason"] == s0["reason"] and with_s0[0]["finding"] == ends[0]["finding"]
    # S1은 초기 범위(알람 인스턴스)로 계속 돈다
    assert any(e["kind"] == "step_start" and e.get("step") == "S1"
               for e in job.activity.since(0)["events"])


# ── P5 소견기 타임아웃 설정화 ────────────────────────────────────────────────


def test_wiring_uses_narrator_timeout_setting() -> None:
    settings = make_settings(sre_narrator_step_timeout_s=33.0)
    wiring = mcp_service._default_playbook_wiring(settings, lambda: None)
    assert wiring is not None and wiring.narrator_limits.step_timeout_s == 33.0
    default = mcp_service._default_playbook_wiring(make_settings(), lambda: None)
    assert default is not None and default.narrator_limits.step_timeout_s == 60.0  # W22
    assert default.narrator_limits.synthesis_timeout_s == 90.0  # W22 — 종전 30s 코드 고정


def test_narrator_shrinks_when_remaining_time_is_short() -> None:
    d = _dispatcher(make_settings(), FakeCaller(_load("latency_sql")), PLAYBOOKS,
                    llm=FakeLLM(), limits=NarratorLimits(step_timeout_s=45.0))
    full, _ = d._narrator(300.0)
    assert full is not None and full.limits.step_timeout_s == 45.0
    short, _ = d._narrator(50.0)  # 45 + 30 > 50 → 0.4 / 0.6
    assert short is not None
    assert short.limits.step_timeout_s == pytest.approx(20.0)
    assert short.limits.synthesis_timeout_s == pytest.approx(30.0)


def test_slow_step_end_narration_does_not_delay_collection() -> None:
    """단계 끝 해석 LLM이 막혀 있어도 수집은 끝까지 진행한다(별 실행기)."""
    release = threading.Event()
    started = threading.Event()

    class GatedLLM(FakeLLM):
        def __call__(self, messages):  # noqa: ANN001
            started.set()
            release.wait(5)
            return super().__call__(messages)

    llm = GatedLLM()
    job = make_job(alarm_of("latency_sql"), "inv-w21-p5")
    job.activity = _log()
    d = _dispatcher(make_settings(), FakeCaller(_load("latency_sql")), PLAYBOOKS, llm=llm,
                    limits=NarratorLimits(step_timeout_s=3.0))
    d(job)
    pb = _playbook("was.latency")
    collect_steps = {s.id for s in pb.steps if s.action != "synthesize"}
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline and job.activity.phase != "narrating":
            time.sleep(0.01)
        assert started.is_set() and llm.calls == 0  # 해석은 걸려 있다
        assert job.activity.phase == "narrating"  # 그래도 수집은 끝났다
        done = {e.get("step") for e in job.activity.since(0)["events"]
                if e["kind"] in ("step_end", "step_skip")}
        assert collect_steps <= done
    finally:
        release.set()
        d.wait_workers(30)


# ── P6 폴스타 소스 설정 매핑 ─────────────────────────────────────────────────


def _apm_alarm(source_id: str | None = "cm_prd", infra: list[str] | None = None) -> dict:
    alarm = copy.deepcopy(_load("latency_sql")["alarm"])
    alarm["meta"]["hints"]["source_id"] = source_id
    if infra is not None:
        alarm["meta"]["episode"] = {"episode_id": "ep", "infra_db_ids": infra, "members": []}
    return alarm


MAP = {"cm_prd": "polestar_cm_gp"}
BASIS = "설정 매핑으로 대체(cm_prd→polestar_cm_gp) — 존이 다른 알람이면 다른 서버를 조회할 수 있음"


def test_source_id_path_is_meta_hints_and_mapping_is_apm_only() -> None:
    assert apm_source_id(_apm_alarm()) == "cm_prd"
    assert mapped_polestar_source(_apm_alarm(), MAP) == ("polestar_cm_gp", BASIS)
    assert POLESTAR_MAPPED_BASIS.format(source_id="cm_prd", db_id="polestar_cm_gp") == BASIS
    assert mapped_polestar_source(_apm_alarm(), {}) == (None, None)
    assert mapped_polestar_source(_apm_alarm(None), MAP) == (None, None)
    assert mapped_polestar_source(_apm_alarm("other"), MAP) == (None, None)
    assert mapped_polestar_source(_apm_alarm(), {" cm_prd ": "polestar_cm_gp"})[0] == (
        "polestar_cm_gp"
    )  # 키 공백은 걷는다
    polestar = {"event": {"dbId": "polestar", "resourceType": "server"}, "meta": {}}
    assert mapped_polestar_source(polestar, MAP) == (None, None)
    # 시그니처 하위 호환 — 종전 호출 그대로
    assert resolve_polestar_source(_apm_alarm()) == (None, POLESTAR_BRIDGE_MISSING)


def _run_mapped(alarm: dict) -> Any:
    fx = _load("latency_sql")
    src, basis = mapped_polestar_source(alarm, MAP)
    return run_playbook(alarm, _playbook("was.latency"), matched_by="t",
                        polestar=FakeCaller(fx, strict=False), apm=FakeCaller(fx, strict=False),
                        polestar_source=src, polestar_source_basis=basis, max_workers=1)


def test_mapping_used_only_without_payload_source_and_basis_is_visible() -> None:
    run = _run_mapped(_apm_alarm())
    assert run.scope["polestar_source"] == "polestar_cm_gp"
    assert run.scope["polestar_basis"] == BASIS
    assert BASIS in run.notes
    assert BASIS in (run.section("S0").reason or "")
    assert BASIS in (run.section("S4").reason or "")
    assert run.to_briefing_dict()["scope"]["polestar_basis"] == BASIS

    payload_first = _run_mapped(_apm_alarm(infra=["polestar"]))
    assert payload_first.scope["polestar_source"] == "polestar"
    assert "polestar_basis" not in payload_first.scope  # 키 자체 없음(종전 모양)
    assert BASIS not in payload_first.notes
    assert BASIS not in (payload_first.section("S4").reason or "")


def test_dispatcher_passes_mapping_and_empty_map_is_unchanged() -> None:
    fx = _load("latency_sql")
    mapped_job = make_job(_apm_alarm(), "inv-w21-p6")
    d = _dispatcher(make_settings(sre_playbook_polestar_source_map=MAP),
                    FakeCaller(fx, strict=False), PLAYBOOKS)
    d(mapped_job)
    d.wait_workers(30)
    assert mapped_job.briefing["playbook"]["scope"]["polestar_source"] == "polestar_cm_gp"
    assert BASIS in mapped_job.briefing["playbook"]["notes"]

    plain_job = make_job(_apm_alarm(), "inv-w21-p6b")
    d = _dispatcher(make_settings(), FakeCaller(fx, strict=False), PLAYBOOKS)
    d(plain_job)
    d.wait_workers(30)
    scope = plain_job.briefing["playbook"]["scope"]
    assert scope["polestar_source"] is None and "polestar_basis" not in scope
    assert any("폴스타 서버 미확정" in n for n in plain_job.briefing["playbook"]["notes"])


def test_polestar_alarm_path_unchanged_by_mapping() -> None:
    alarm = {"event": {"dbId": "polestar", "serverName": "h1", "resourceType": "server"}}
    assert resolve_polestar_source(alarm, "polestar_cm_gp") == ("polestar", None)
    assert mapped_polestar_source(alarm, MAP) == (None, None)
    assert dataclasses.asdict(RunScope()).get("polestar_basis") is None
    assert "polestar_basis" not in RunScope().to_dict()
