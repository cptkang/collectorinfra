"""plans/187 W22 — 조사 시간 사슬 상향·정합(sre_agent 부분).

새 기본값 · 기동 시 사슬 정합 검사(경고 1줄 · 기동 계속) · 「스텝 타임아웃」 결손 스텝 1회 재시도
(실행기 · dispatcher 조사 흐름 카드·부분 브리핑). 가짜 배치 콜러블(지연 주입)·가짜 스텝만 쓴다 —
실 MCP·LLM 호출 0.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.application.playbook_runner import (
    DEFAULT_BUDGET_S,
    DEFAULT_STEP_TIMEOUT_S,
    RETRY_SCOPE_KEPT,
    run_playbook,
)
from sre_agent.application.playbook_steps import RunScope, StepContext, StepResult
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.infrastructure import mcp_tool_client
from sre_agent.infrastructure.activity_masking import mask_activity_text
from sre_agent.infrastructure.tool_result_cache import DEFAULT_WAIT_TIMEOUT_S, ToolResultCache
from sre_agent.interface import mcp_service
from sre_agent.settings import TOOL_CALL_TIMEOUT_S, AgentSettings, timechain_warnings
from test_plan187_w2_runner import PLAYBOOKS, _load
from test_plan187_w4_dispatch import alarm_of, make_settings
from test_plan187_w21_timeouts import _DelayedHealth, _dispatcher, _pb

S0_HEAD = "재시도 1회 — 성공(첫 시도: 스텝 타임아웃 "


def _settings(**kw: Any) -> AgentSettings:
    return AgentSettings(_env_file=None, model="test/model", **kw)


# ── 1. 새 기본값 ─────────────────────────────────────────────────────────────


def test_settings_defaults_raised() -> None:
    s = _settings()
    assert TOOL_CALL_TIMEOUT_S == 60.0
    assert s.evidence_prefetch_timeout_seconds == 60.0
    assert s.sre_playbook_budget_s == 240.0
    assert s.sre_narrator_step_timeout_s == 60.0
    assert s.sre_narrator_synthesis_timeout_s == 90.0
    assert s.investigation_timeout_seconds == 600
    assert s.sre_playbook_retry_timed_out_steps is True
    with pytest.raises(ValidationError):
        _settings(sre_narrator_synthesis_timeout_s=0)


def test_settings_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRE_NARRATOR_SYNTHESIS_TIMEOUT_S", "70")
    monkeypatch.setenv("SRE_PLAYBOOK_RETRY_TIMED_OUT_STEPS", "false")
    s = _settings()
    assert s.sre_narrator_synthesis_timeout_s == 70.0
    assert s.sre_playbook_retry_timed_out_steps is False


def test_runner_and_cache_constants() -> None:
    assert DEFAULT_STEP_TIMEOUT_S == 90.0
    assert DEFAULT_BUDGET_S == 240.0
    assert DEFAULT_WAIT_TIMEOUT_S == 600.0


def test_mcp_client_defaults_follow_single_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    for fn in (mcp_tool_client.run_tool_batch, mcp_tool_client.run_tool_batch_async):
        assert inspect.signature(fn).parameters["timeout_seconds"].default == TOOL_CALL_TIMEOUT_S
    seen: list[float] = []
    monkeypatch.setattr(mcp_tool_client, "run_tool_batch",
                        lambda url, headers, calls, *, timeout_seconds, concurrency:
                        seen.append(timeout_seconds) or [])
    # 설정에 필드가 없으면(대역) 같은 상수로 폴백
    ns = SimpleNamespace(polestar_mcp_url="http://x/sse", polestar_mcp_token=None,
                         apm_mcp_url="http://y/sse", apm_mcp_token=None)
    for make in (mcp_tool_client.make_batch_caller, mcp_tool_client.make_apm_batch_caller):
        caller = make(ns)
        assert caller is not None
        caller([("t", {})])
    assert seen == [TOOL_CALL_TIMEOUT_S, TOOL_CALL_TIMEOUT_S]


def test_yaml_scope_steps_120s() -> None:
    by_id = {pb.id: pb for pb in PLAYBOOKS}
    assert by_id["was.latency"].version == 3
    assert next(s for s in by_id["was.latency"].steps if s.id == "S0").timeout_s == 120
    for pid in ("host.cpu", "host.process"):
        assert by_id[pid].version == 4
        assert next(s for s in by_id[pid].steps if s.id == "S0r").timeout_s == 120
    # 적재된 플레이북의 스텝 상한 최대값이 수집 예산 안
    assert max(s.timeout_s for pb in PLAYBOOKS for s in pb.steps if s.timeout_s) <= 240


def test_dispatcher_job_cache_wait_bound_to_investigation_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[float] = []
    real_init = ToolResultCache.__init__

    def spy(self: ToolResultCache, *, wait_timeout_s: float = DEFAULT_WAIT_TIMEOUT_S) -> None:
        made.append(wait_timeout_s)
        real_init(self, wait_timeout_s=wait_timeout_s)

    monkeypatch.setattr(ToolResultCache, "__init__", spy)
    fx = _load("latency_sql")
    d = _dispatcher(make_settings(investigation_timeout_seconds=123), _DelayedHealth(fx, 0.0),
                    PLAYBOOKS)
    job = _job("inv-w22-cache")
    d(job)
    d.wait_workers(30)
    assert made and made[0] == 123.0


# ── 2. 사슬 정합 검사 ────────────────────────────────────────────────────────


def test_timechain_defaults_consistent() -> None:
    assert timechain_warnings(_settings(), step_default_s=DEFAULT_STEP_TIMEOUT_S,
                              max_step_timeout_s=120.0) == []


@pytest.mark.parametrize(
    ("overrides", "step_default", "max_step", "expected"),
    [
        ({"evidence_prefetch_timeout_seconds": 90.0}, 90.0, None, ["도구 1건 90s ≥ 스텝 기본 90s"]),
        ({"sre_playbook_budget_s": 80.0, "sre_narrator_synthesis_timeout_s": 1.0}, 90.0, None,
         ["스텝 기본 90s > 수집 예산 80s"]),
        ({}, 90.0, 300.0, ["플레이북 스텝 timeout_s 최대 300s > 수집 예산 240s"]),
        # 예산 ≥ 조사 전체면 「예산 + 소견 + 종합」 관계도 함께 어긋난다(소견·종합 > 0)
        ({"investigation_timeout_seconds": 240}, 90.0, None,
         ["수집 예산 240s ≥ 조사 전체 240s",
          "수집 예산 240s + 소견 60s + 종합 90s = 390s > 조사 전체 240s"]),
        ({"investigation_timeout_seconds": 300}, 90.0, None,
         ["수집 예산 240s + 소견 60s + 종합 90s = 390s > 조사 전체 300s"]),
    ],
)
def test_timechain_each_violation(overrides: dict, step_default: float, max_step: float | None,
                                  expected: list[str]) -> None:
    out = timechain_warnings(_settings(**overrides), step_default_s=step_default,
                             max_step_timeout_s=max_step)
    assert out == expected


def test_wiring_logs_one_warning_and_continues(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=mcp_service.__name__)
    # S0 120s > 예산 100s · 100 + 60 + 90 > 조사 전체 200 — 기동은 막지 않는다
    wiring = mcp_service._default_playbook_wiring(
        make_settings(sre_playbook_budget_s=100.0, investigation_timeout_seconds=200),
        lambda: None,
    )
    assert wiring is not None and wiring.playbooks
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "조사 시간 사슬" in r.getMessage()]
    assert len(warns) == 1
    msg = warns[0].getMessage()
    assert "플레이북 스텝 timeout_s 최대 120s > 수집 예산 100s" in msg
    assert "> 조사 전체 200s" in msg and "도구 1건 60s" in msg


def test_wiring_consistent_logs_info_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=mcp_service.__name__)
    wiring = mcp_service._default_playbook_wiring(
        make_settings(investigation_timeout_seconds=600), lambda: None
    )
    assert wiring is not None
    chain = [r for r in caplog.records if "조사 시간 사슬" in r.getMessage()]
    assert [r.levelno for r in chain] == [logging.INFO]
    assert wiring.narrator_limits.synthesis_timeout_s == 90.0
    assert wiring.narrator_limits.step_timeout_s == 60.0


def test_wiring_off_reads_no_chain(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=mcp_service.__name__)
    assert mcp_service._default_playbook_wiring(
        make_settings(sre_playbook_enabled=False), lambda: None) is None
    assert not [r for r in caplog.records if "조사 시간 사슬" in r.getMessage()]


# ── 3. 결손 스텝 재시도 — 실행기 ─────────────────────────────────────────────


class _Hang:
    """처음 `hang_times`번은 `release`까지 걸리고, 그 뒤는 바로 끝나는 가짜 스텝."""

    def __init__(self, hang_times: int, result: StepResult | None = None) -> None:
        self.hang_times = hang_times
        self.calls = 0
        self.release = threading.Event()
        self.result = result
        self.targets: list[list[str]] = []
        self._lock = threading.Lock()

    def __call__(self, ctx: StepContext) -> StepResult:
        with self._lock:
            self.calls += 1
            n = self.calls
        self.targets.append(list(ctx.targets))
        if n <= self.hang_times:
            self.release.wait(3)
        return self.result if self.result is not None else StepResult(status="ok")


def _run(pb: Any, steps: dict, **kw: Any) -> Any:
    fx = _load("latency_sql")
    return run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=None, steps=steps,
                        **kw)


def test_retry_success_reason() -> None:
    step = _Hang(1)
    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.2}])
    try:
        run = _run(pb, {"apm.golden_signals": step}, budget_s=3.0, retry_timed_out=True)
    finally:
        step.release.set()
    s1 = run.section("S1")
    assert s1.status == "ok"
    assert s1.reason == "재시도 1회 — 성공(첫 시도: 스텝 타임아웃 0.2s — 요청 0건 중 0건 완료)"
    assert step.calls == 2


def test_retry_times_out_again() -> None:
    step = _Hang(2)
    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.2}])
    try:
        run = _run(pb, {"apm.golden_signals": step}, budget_s=3.0, retry_timed_out=True)
    finally:
        step.release.set()
    s1 = run.section("S1")
    assert s1.status == "missing"
    assert s1.reason == (
        "스텝 타임아웃 0.2s — 요청 0건 중 0건 완료 · 재시도 1회 — 다시 타임아웃 0.2s — "
        "요청 0건 중 0건 완료"
    )
    assert step.calls == 2


def _sections(run: Any) -> list[tuple]:
    return [(s.step, s.status, s.reason, s.tools) for s in run.sections]


def test_retry_off_is_bit_identical_to_previous() -> None:
    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.2},
              {"id": "S2", "action": "apm.runtime_and_pool"}])
    runs = []
    for kw in ({}, {"retry_timed_out": False}):
        step = _Hang(5)
        try:
            runs.append(_run(pb, {"apm.golden_signals": step,
                                  "apm.runtime_and_pool": lambda ctx: StepResult(status="ok")},
                             budget_s=3.0, **kw))
        finally:
            step.release.set()
        assert step.calls == 1  # 재시도 0
    assert _sections(runs[0]) == _sections(runs[1])
    assert runs[0].section("S1").reason == "스텝 타임아웃 0.2s — 요청 0건 중 0건 완료"


def test_retry_skipped_when_budget_exhausted() -> None:
    step = _Hang(1)
    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 5}])
    try:
        run = _run(pb, {"apm.golden_signals": step}, budget_s=0.25, retry_timed_out=True)
    finally:
        step.release.set()
    s1 = run.section("S1")
    assert s1.status == "missing" and "재시도" not in (s1.reason or "")
    assert s1.reason.startswith("스텝 타임아웃 0.25s — ")
    assert step.calls == 1


def test_scope_step_retry_keeps_initial_scope_and_does_not_rerun_later_steps() -> None:
    wide = RunScope(alarm_instance="A1", targets=[
        {"instance": n, "verdict": "degraded", "verdict_source": "t", "confidence": None}
        for n in ("A1", "A2", "A3")
    ])
    s0 = _Hang(1, StepResult(status="ok", verdict="degraded", artifacts={"scope": wide}))
    s1 = _Hang(0)
    pb = _pb([{"id": "S0", "action": "scope.resolve_was_targets", "timeout_s": 0.2},
              {"id": "S1", "action": "apm.golden_signals", "for_each": "targets"}])
    try:
        run = _run(pb, {"scope.resolve_was_targets": s0, "apm.golden_signals": s1},
                   budget_s=3.0, retry_timed_out=True)
    finally:
        s0.release.set()
    sec = run.section("S0")
    assert sec.status == "ok" and sec.reason.startswith(S0_HEAD)
    assert sec.reason.endswith(f" · {RETRY_SCOPE_KEPT}")
    assert s0.calls == 2 and s1.calls == 1  # 뒤 단계는 다시 돌지 않는다
    assert s1.targets == [["A1"]]  # 초기 범위(알람 인스턴스 단독)
    assert [t["instance"] for t in run.scope["targets"]] == ["A1"]  # 범위 불변


class _CountingCaller:
    """도구별 실제 호출 수를 세는 가짜 배치 콜러블 — `slow` 도구만 처음 1회 `delay`초."""

    def __init__(self, slow: str, delay: float) -> None:
        self.slow, self.delay = slow, delay
        self.real: dict[str, int] = {}
        self._lock = threading.Lock()

    def __call__(self, calls):  # noqa: ANN001
        out = []
        for tool, _args in calls:
            with self._lock:
                self.real[tool] = self.real.get(tool, 0) + 1
                first = self.real[tool] == 1
            if tool == self.slow and first:
                time.sleep(self.delay)
            out.append(json.dumps({"rows": [{"tool": tool}]}))
        return out


def test_retry_reuses_job_cache_including_in_flight_call() -> None:
    """첫 시도에서 받은 호출은 캐시 적중 · 첫 시도 스레드가 기다리던 호출은 그 결과를 공유."""
    caller = _CountingCaller("t_b", 0.45)

    def step(ctx: StepContext) -> StepResult:
        a = ctx.call("apm", [("t_a", {})])
        b = ctx.call("apm", [("t_b", {})])
        ok = all(r.error is None for r in (*a, *b))
        return StepResult(status="ok" if ok else "missing")

    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.3}])
    fx = _load("latency_sql")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=caller,
                       steps={"apm.golden_signals": step}, budget_s=3.0, retry_timed_out=True)
    s1 = run.section("S1")
    assert s1.status == "ok", s1.reason
    assert s1.reason.startswith(
        "재시도 1회 — 성공(첫 시도: 스텝 타임아웃 0.3s — 요청 2건 중 1건 완료"
    )
    assert caller.real == {"t_a": 1, "t_b": 1}  # 재호출 0
    assert [c["tool"] for c in run.tool_calls] == ["t_a", "t_b"]  # 재시도 문맥의 호출 기록


# ── 3. 결손 스텝 재시도 — dispatcher 조사 흐름 ───────────────────────────────


@dataclass
class _RecJob(InvestigationJob):
    """부분 브리핑을 달 때마다 사본을 모으는 잡."""

    partial_log: list = field(default_factory=list)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "partial_briefing" and value is not None and hasattr(self, "partial_log"):
            self.partial_log.append(json.loads(json.dumps(value, default=str)))
        super().__setattr__(name, value)


def _job(iid: str) -> _RecJob:
    now = time.time()
    job = _RecJob(investigation_id=iid, kind="alarm", status="accepted", created_at=now,
                  updated_at=now, payload=alarm_of("latency_sql"))
    job.activity = ActivityLog(masker=mask_activity_text)
    return job


def _scope_retry_pb() -> Any:
    return _pb([{"id": "S0", "action": "scope.resolve_was_targets", "timeout_s": 0.3,
                 "narrate": {"focus": "판정 분포"}},
                {"id": "S1", "action": "apm.golden_signals", "for_each": "targets"}])


def test_dispatcher_retry_emits_step_start_end_and_partial_briefing() -> None:
    fx = _load("latency_sql")
    caller = _DelayedHealth(fx, 0.25)
    job = _job("inv-w22-retry")
    d = _dispatcher(make_settings(investigation_timeout_seconds=30), caller, [_scope_retry_pb()])
    d(job)
    d.wait_workers(30)
    time.sleep(0.6)  # 첫 시도 스레드의 늦은 완료가 끝날 시간 — 그 결과는 기록 0이어야 한다
    events = job.activity.since(0)["events"]
    starts = [e for e in events if e["kind"] == "step_start" and e.get("step") == "S0"]
    ends = [e for e in events if e["kind"] == "step_end" and e.get("step") == "S0"]
    assert len(starts) == 2 and len(ends) == 2
    assert "재시도" not in starts[0]["label"] and starts[1]["label"].endswith("(재시도)")
    assert starts[1]["because"].startswith("첫 시도가 시간 안에 끝나지 않아 1회 다시 확인(첫 시도: "
                                           "스텝 타임아웃 0.3s")
    assert [e["status"] for e in ends] == ["missing", "ok"]
    # 최종 섹션 사유
    s0 = next(s for s in job.briefing["playbook"]["sections"] if s["step"] == "S0")
    assert s0["status"] == "ok"
    assert s0["reason"].startswith(S0_HEAD + "0.3s") and RETRY_SCOPE_KEPT in s0["reason"]
    # 부분 브리핑 — 첫 시도 결손이 먼저, 재시도 성공(같은 사유)이 뒤에 보인다
    seq = [next((s for s in snap["playbook"]["sections"] if s["step"] == "S0"), None)
           for snap in job.partial_log if "playbook" in snap and "sections" in snap["playbook"]]
    seq = [s for s in seq if s is not None]
    statuses = [s["status"] for s in seq]
    assert "missing" in statuses and "ok" in statuses
    assert statuses.index("missing") < len(statuses) - 1 - statuses[::-1].index("ok")
    last_ok = [s for s in seq if s["status"] == "ok"][-1]
    assert last_ok["reason"] == s0["reason"]
    assert last_ok["opinion"]["status"] in ("none", "pending", "ok")
    # S1은 한 번만 시작
    assert sum(1 for e in events if e["kind"] == "step_start" and e.get("step") == "S1") == 1


def test_dispatcher_retry_off_keeps_w21_flow() -> None:
    fx = _load("latency_sql")
    caller = _DelayedHealth(fx, 0.25)
    job = _job("inv-w22-off")
    d = _dispatcher(make_settings(investigation_timeout_seconds=30,
                                  sre_playbook_retry_timed_out_steps=False),
                    caller, [_scope_retry_pb()])
    d(job)
    d.wait_workers(30)
    time.sleep(0.6)
    events = job.activity.since(0)["events"]
    assert sum(1 for e in events if e["kind"] == "step_start" and e.get("step") == "S0") == 1
    assert sum(1 for e in events if e["kind"] == "step_end" and e.get("step") == "S0") == 1
    s0 = next(s for s in job.briefing["playbook"]["sections"] if s["step"] == "S0")
    assert s0["status"] == "missing"
    assert re.match(r"스텝 타임아웃 0\.3s — ", s0["reason"]) and "재시도" not in s0["reason"]
