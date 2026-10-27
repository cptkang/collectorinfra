"""plans/187 W22 독립 검증 — 결손 스텝 재시도의 경합·예산·사유 일관성 · 사슬 정합 관계.

가짜 스텝·가짜 배치 콜러블·짧은 실지연만 쓴다(실 MCP·LLM 0). 결함 재현 테스트는
`xfail(strict=True)` — 제품 코드가 고쳐지면 XPASS로 실패해 표지를 걷게 한다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import pytest
from sre_agent.application.playbook_runner import (
    DEFAULT_STEP_TIMEOUT_S,
    run_playbook,
)
from sre_agent.application.playbook_steps import StepContext, StepResult
from sre_agent.infrastructure.tool_result_cache import ToolResultCache
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings, timechain_warnings
from test_plan187_w2_runner import _load
from test_plan187_w4_dispatch import make_settings
from test_plan187_w21_timeouts import _pb


def _settings(**kw: Any) -> AgentSettings:
    return AgentSettings(_env_file=None, model="test/model", **kw)


class _Hang:
    """처음 `hang_times`번은 `release`(최대 `hold`초)까지 걸리고, 그 뒤는 바로 끝나는 가짜 스텝."""

    def __init__(self, hang_times: int, hold: float = 3.0,
                 result: StepResult | None = None, raise_on: int | None = None) -> None:
        self.hang_times, self.hold = hang_times, hold
        self.calls = 0
        self.release = threading.Event()
        self.result = result
        self.raise_on = raise_on
        self._lock = threading.Lock()

    def __call__(self, ctx: StepContext) -> StepResult:
        with self._lock:
            self.calls += 1
            n = self.calls
        if self.raise_on == n:
            raise RuntimeError("재시도 실패 주입")
        if n <= self.hang_times:
            self.release.wait(self.hold)
        return self.result if self.result is not None else StepResult(status="ok")


def _run(pb: Any, steps: dict, **kw: Any) -> Any:
    fx = _load("latency_sql")
    return run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=None, steps=steps,
                        **kw)


# ── 1. 재시도 경합 ───────────────────────────────────────────────────────────


def test_second_retry_not_starved_by_hung_first_retry() -> None:
    a = _Hang(hang_times=2)  # 첫 시도·재시도 모두 걸림
    b = _Hang(hang_times=1)  # 첫 시도만 걸림 — 재시도는 즉시 성공해야 한다
    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.3},
              {"id": "S2", "action": "apm.runtime_and_pool", "timeout_s": 0.3}])
    try:
        run = _run(pb, {"apm.golden_signals": a, "apm.runtime_and_pool": b},
                   budget_s=3.0, retry_timed_out=True)
    finally:
        a.release.set()
        b.release.set()
    assert run.section("S1").status == "missing"
    s2 = run.section("S2")
    assert s2.status == "ok", s2.reason  # 재시도마다 새 실행기 — 앞 재시도 스레드에 굶지 않는다
    assert b.calls == 2


def test_retry_phase_never_exceeds_budget() -> None:
    """재시도 단계 전체가 수집 예산(실시간) 안에서 끝난다 — 스텝마다 남은 예산을 다시 잰다."""
    steps = {act: _Hang(hang_times=9, hold=5.0)
             for act in ("apm.golden_signals", "apm.runtime_and_pool", "apm.slow_tx_breakdown")}
    pb = _pb([{"id": f"S{i}", "action": act, "timeout_s": 0.4}
              for i, act in enumerate(steps, start=1)])
    t0 = time.monotonic()
    try:
        run = _run(pb, steps, budget_s=1.0, retry_timed_out=True)
    finally:
        for s in steps.values():
            s.release.set()
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0 + 0.35, elapsed
    for sid in ("S1", "S2", "S3"):
        assert run.section(sid).status == "missing"


def test_in_flight_entry_is_filled_after_abort_and_retry_reuses_it() -> None:
    """첫 시도가 중단된 뒤에도 그 스레드의 진행 중 배치는 캐시를 채운다 — 재시도는 실제 호출 0으로
    그 값을 받는다(무한 대기 없음 · 캐시 대기 상한은 잡 캐시 값)."""
    real: dict[str, int] = {}
    lock = threading.Lock()

    def caller(calls):  # noqa: ANN001
        out = []
        for tool, _ in calls:
            with lock:
                real[tool] = real.get(tool, 0) + 1
            if tool == "slow":
                time.sleep(0.5)
            out.append(json.dumps({"rows": [{"t": tool}]}))
        return out

    cache = ToolResultCache(wait_timeout_s=5.0)

    def step(ctx: StepContext) -> StepResult:
        r = ctx.call("apm", [("slow", {"k": 1})])
        return StepResult(status="ok" if r[0].error is None else "missing", reason=r[0].error)

    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.2}])
    fx = _load("latency_sql")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=caller,
                       steps={"apm.golden_signals": step}, budget_s=3.0, cache=cache,
                       retry_timed_out=True)
    s1 = run.section("S1")
    # 재시도 상한 0.2s 안에 첫 시도 배치(0.5s)가 끝나지 않으면 재시도도 다시 타임아웃 — 이때도
    # 실제 호출은 1회뿐이다(재시도는 진행 중 키를 기다리며 새 호출을 내지 않는다).
    assert real == {"slow": 1}
    assert s1.status == "missing" and "재시도 1회 — 다시 타임아웃" in (s1.reason or "")
    time.sleep(0.5)
    assert cache.contains("slow", {"k": 1})  # 중단 뒤에도 진행 중 배치가 값을 채웠다
    assert cache.real_calls == 1 and cache.hits >= 1


def test_retry_does_not_recall_tool_that_errored_in_first_attempt() -> None:
    """설계 확인(Minor 근거) — 첫 시도에서 오류(도구 1건 타임아웃 포함)로 끝난 호출은 잡 캐시에
    오류로 남아 재시도가 다시 부르지 않는다. 재시도가 「성공」이어도 그 도구는 오류 그대로다."""
    real: dict[str, int] = {}
    lock = threading.Lock()

    def caller(calls):  # noqa: ANN001
        out = []
        for tool, _ in calls:
            with lock:
                real[tool] = real.get(tool, 0) + 1
                first = real[tool] == 1
            if tool == "err":
                out.append(json.dumps({"error": "도구 호출 실패: timeout"}))
                continue
            if tool == "slow" and first:
                time.sleep(0.5)
            out.append(json.dumps({"rows": [{"t": tool}]}))
        return out

    errors: list[str | None] = []

    def step(ctx: StepContext) -> StepResult:
        e = ctx.call("apm", [("err", {})])
        ctx.call("apm", [("slow", {})])
        errors.append(e[0].error)
        return StepResult(status="ok")

    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.3}])
    fx = _load("latency_sql")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=None, apm=caller,
                       steps={"apm.golden_signals": step}, budget_s=3.0, retry_timed_out=True)
    time.sleep(0.4)
    assert real["err"] == 1  # 재시도에서 다시 부르지 않음
    assert run.section("S1").status == "ok"
    assert run.section("S1").reason.startswith("재시도 1회 — 성공")
    assert errors and all(e and "timeout" in e for e in errors)


def test_first_attempt_late_completion_does_not_override_retry_result() -> None:
    """첫 시도 스레드가 재시도 뒤에 늦게 끝나도 섹션은 재시도 결과다."""
    late = StepResult(status="ok", verdict="late-first")
    calls = {"n": 0}
    first_release = threading.Event()

    def step(ctx: StepContext) -> StepResult:
        calls["n"] += 1
        if calls["n"] == 1:
            first_release.wait(3)
            return late
        return StepResult(status="ok", verdict="retry")

    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.2}])
    run = _run(pb, {"apm.golden_signals": step}, budget_s=3.0, retry_timed_out=True)
    first_release.set()
    time.sleep(0.1)
    assert run.section("S1").verdict == "retry"


def test_not_started_reason_is_not_retried() -> None:
    """「스텝 미시작」(큐 대기)은 재시도 대상이 아니다 — 워커 1 · 같은 단계 두 스텝."""
    a = _Hang(hang_times=9, hold=5.0)
    b = _Hang(hang_times=0)
    pb = _pb([{"id": "S1", "action": "apm.golden_signals"},
              {"id": "S2", "action": "apm.runtime_and_pool"}])
    try:
        run = _run(pb, {"apm.golden_signals": a, "apm.runtime_and_pool": b},
                   budget_s=0.4, max_workers=1, step_timeout_s=10.0, retry_timed_out=True)
    finally:
        a.release.set()
    s2 = run.section("S2")
    assert s2.status == "missing" and (s2.reason or "").startswith("스텝 미시작")
    assert "재시도" not in s2.reason
    assert b.calls == 0


def test_retry_exception_reason_prefixed_in_final_section() -> None:
    """재시도에서 스텝이 예외를 내면 최종 섹션 사유는 재시도 머리 + 스텝 실패 문장."""
    step = _Hang(hang_times=1, raise_on=2)
    pb = _pb([{"id": "S1", "action": "apm.golden_signals", "timeout_s": 0.2}])
    try:
        run = _run(pb, {"apm.golden_signals": step}, budget_s=3.0, retry_timed_out=True)
    finally:
        step.release.set()
    s1 = run.section("S1")
    assert s1.status == "missing"
    assert s1.reason.startswith("재시도 1회 — 상태 missing(첫 시도: 스텝 타임아웃 0.2s")
    assert s1.reason.endswith("스텝 실패 — RuntimeError: 재시도 실패 주입")


# ── 2. 사슬 정합 관계 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("overrides", "max_step", "expect_ok"),
    [
        # 경계값 — 스텝 기본 = 예산은 허용(≤) · 도구 1건 = 스텝 기본은 위반(<)
        ({"sre_playbook_budget_s": 90.0, "investigation_timeout_seconds": 600}, None, True),
        ({"evidence_prefetch_timeout_seconds": 89.9}, None, True),
        ({"evidence_prefetch_timeout_seconds": 90.0}, None, False),
        # 스텝 최대 = 예산 허용
        ({}, 240.0, True),
        ({}, 240.1, False),
        # 예산 + 소견 + 종합 = 조사 전체 허용(≤)
        ({"investigation_timeout_seconds": 390}, None, True),
        ({"investigation_timeout_seconds": 389}, None, False),
    ],
)
def test_timechain_boundaries(overrides: dict, max_step: float | None, expect_ok: bool) -> None:
    out = timechain_warnings(_settings(**overrides), step_default_s=DEFAULT_STEP_TIMEOUT_S,
                             max_step_timeout_s=max_step)
    assert (out == []) is expect_ok, out


def test_timechain_log_has_no_secret_values(caplog: pytest.LogCaptureFixture) -> None:
    """기동 로그 1줄에 토큰·URL 자격증명이 실리지 않는다."""
    caplog.set_level(logging.INFO, logger=mcp_service.__name__)
    secret = "s3cr3t-token-w22"
    s = make_settings(polestar_mcp_token=secret, apm_mcp_token=secret,
                      polestar_mcp_url="http://u:pw@127.0.0.1:9/sse")
    mcp_service._default_playbook_wiring(s, lambda: None)
    chain = [r.getMessage() for r in caplog.records if "조사 시간 사슬" in r.getMessage()]
    assert len(chain) == 1
    assert secret not in chain[0] and "pw@" not in chain[0]
