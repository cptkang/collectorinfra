"""plans/192 W3 — 끝나기 어려운 APM 작업의 조기 접수(`apm_jobs.poll_job`).

고정하는 계약(plans/192 §3.3 · D-346):
  1. 진행 중 상태를 볼 때마다 남은 예상 시간 R = max(게이트웨이 `estimate.seconds`, 본체 관측
     진행률 기반 남은 시간)을 내고, R > 조회 마감까지 남은 시간이면 그 상태 봉투를 즉시 돌려준다.
  2. R < 남은 시간이면 종전처럼 끝날 때까지(또는 마감까지) 다시 본다.
  3. `estimate.seconds`·`progress.total`이 둘 다 없으면 종전(마감까지 대기).
  4. 진행 0(슬롯 대기 `queued` 포함)은 관측 창(`EARLY_ACCEPT_MIN_OBSERVE_SEC`) 뒤 R = 무한대 → 접수.
  5. 끝난 상태·오류 봉투는 즉시 반환(종전).
  6. 대상 선정용 첫 홉(`_await_first_hop`)은 조기 접수하지 않는다 — 마감까지 기다린 뒤 취소
     (SPEC §3.8).
시계는 가짜다 — 상태 호출 1회마다 1초가 흐른다(모듈의 `time`만 바꾼다 · 이벤트 루프 시계는 그대로).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from src.orchestration import apm_jobs as jobs
from src.orchestration.apm import jobs_notices
from src.orchestration.apm.calls import _FIRST_HOP_TIMEOUT_ERROR, _Call, _JobScope

JOB = "0123456789abcdef0123456789abcdef"
OWNER = "user:alice"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _Session:
    """가짜 게이트웨이 세션 — 상태 응답 순서(마지막 값 반복) · 호출마다 시계 1초 전진 · 기록."""

    def __init__(self, clock: _Clock, statuses: list[dict[str, Any]]) -> None:
        self.clock = clock
        self.statuses = list(statuses)
        self.calls: list[tuple[str, float]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == jobs.CANCEL_TOOL:
            self.calls.append((name, self.clock.now))
            return {"job": {"job_id": arguments["job_id"], "state": "cancelled"}}
        self.clock.now += 1.0
        self.calls.append((name, self.clock.now))
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def status_calls(self) -> int:
        return sum(1 for name, _ in self.calls if name == jobs.STATUS_TOOL)


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(jobs, "time", SimpleNamespace(monotonic=fake.monotonic))
    return fake


def _status(state: str = "running", *, done: int | None = 10, total: int | None = 350,
            seconds: float | None = None, error: dict | None = None) -> dict[str, Any]:
    handle: dict[str, Any] = {
        "job_id": JOB, "state": state,
        "progress": {"done": done, "total": total, "unit": "api_calls", "label": "API 호출"},
        "estimate": {"api_calls": total, "seconds": seconds, "basis": "measured"},
    }
    if error:
        handle["error"] = error
    return {"job": handle, "rows": []}


async def _poll(session: _Session, until: float, **kwargs: Any) -> dict[str, Any] | None:
    return await jobs.poll_job(session, JOB, OWNER, until=until, interval=0.0, **kwargs)


# ── 1. 예상 > 남은 시간 → 첫 판단 시점에 접수 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_estimate_longer_than_deadline_returns_at_first_look(clock, caplog) -> None:
    session = _Session(clock, [_status(done=74, seconds=92.6)])
    with caplog.at_level(logging.INFO, logger=jobs.__name__):
        status = await _poll(session, until=30.0)

    assert jobs.job_state(status) == "running"
    assert session.status_calls() == 1 and clock.now == 1.0, "마감(30초)을 기다리지 않는다"
    (line,) = [r.getMessage() for r in caplog.records if "조기 접수" in r.getMessage()]
    assert JOB[:8] in line and JOB not in line
    assert "92.6초" in line and "29.0초" in line and "74/350" in line


# ── 2. 예상 < 남은 시간 → 끝날 때까지 대기(종전) ─────────────────────────────────

@pytest.mark.asyncio
async def test_estimate_shorter_than_deadline_waits_for_completion(clock) -> None:
    session = _Session(clock, [_status(done=10, seconds=3.0), _status(done=20, seconds=2.0),
                               _status("completed", done=30, seconds=0.0)])
    status = await _poll(session, until=30.0)
    assert jobs.job_state(status) == "completed"
    assert session.status_calls() == 3


@pytest.mark.asyncio
async def test_observed_progress_fast_enough_keeps_waiting(clock) -> None:
    """게이트웨이 예상이 없고 관측 속도로 마감 안에 끝나면 기다린다(관측 창 뒤에도)."""
    statuses = [_status(done=100 * i, total=1000) for i in range(1, 10)]
    session = _Session(clock, [*statuses, _status("completed", done=1000, total=1000)])
    status = await _poll(session, until=30.0)
    assert jobs.job_state(status) == "completed"
    assert session.status_calls() == 10


@pytest.mark.asyncio
async def test_observed_progress_too_slow_returns_after_observe_window(clock) -> None:
    """관측 속도 1회/초 · 남은 990여 회 → 관측 창(5초)이 차는 시점에 접수."""
    session = _Session(clock, [_status(done=10 + i, total=1000) for i in range(30)])
    status = await _poll(session, until=30.0)
    assert jobs.job_state(status) == "running"
    assert clock.now == 1.0 + jobs.EARLY_ACCEPT_MIN_OBSERVE_SEC


@pytest.mark.asyncio
async def test_remaining_is_the_larger_of_estimate_and_observed(clock) -> None:
    """게이트웨이 예상이 짧아도(1초) 관측 진행이 멈췄으면 큰 값(무한대)으로 판단한다."""
    session = _Session(clock, [_status(done=50, total=350, seconds=1.0)])
    status = await _poll(session, until=30.0)
    assert jobs.job_state(status) == "running"
    assert clock.now == 1.0 + jobs.EARLY_ACCEPT_MIN_OBSERVE_SEC


# ── 3. 근거 없음 → 종전(마감까지 대기) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_estimate_and_no_total_waits_until_deadline(clock) -> None:
    session = _Session(clock, [_status(done=1, total=None, seconds=None)])
    status = await _poll(session, until=10.0)
    assert jobs.job_state(status) == "running"
    assert session.status_calls() == 10 and clock.now == 10.0


@pytest.mark.asyncio
@pytest.mark.parametrize("basis", ["rate_only", None])
async def test_rate_only_estimate_does_not_accept_early(clock, basis) -> None:
    """`rate_only`(작업 전체 ÷ 속도 — 남은 시간 아님)·근거 없음의 큰 예상으로는 접수하지 않는다."""
    running = _status(done=50, total=60, seconds=600.0)
    running["job"]["estimate"]["basis"] = basis
    statuses = [running, _status("completed", done=60, total=60)]
    session = _Session(clock, statuses)
    status = await _poll(session, until=30.0)
    assert jobs.job_state(status) == "completed"
    assert session.status_calls() == 2


@pytest.mark.parametrize("estimate", [None, "모름", True])
def test_non_numeric_estimate_is_ignored(estimate) -> None:
    handle = _status(done=None, total=None)["job"]
    handle["estimate"]["seconds"] = estimate
    assert jobs._remaining_estimate(handle, (0.0, 0.0), 100.0) is None


# ── 4. 진행 0(queued) → 관측 창 뒤 접수 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_queued_without_progress_returns_after_observe_window(clock) -> None:
    session = _Session(clock, [_status("queued", done=0, total=350)])
    status = await _poll(session, until=60.0)
    assert jobs.job_state(status) == "queued"
    assert clock.now == 1.0 + jobs.EARLY_ACCEPT_MIN_OBSERVE_SEC, "관측 창 전에는 판단하지 않는다"


# ── 5. 끝난 상태 · 오류 → 즉시(종전) ────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    _status("completed", done=350, seconds=0.0),
    _status("failed", seconds=999.0, error={"code": "stalled", "reason": "정체"}),
    {"error": "job_not_found", "reason": "없음"},
], ids=["completed", "failed", "gateway_error"])
async def test_finished_or_error_returns_immediately(clock, reply) -> None:
    session = _Session(clock, [reply])
    assert await _poll(session, until=30.0) == reply
    assert session.status_calls() == 1


# ── 6. 끄기 · 호출부 ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_early_accept_off_waits_until_deadline(clock) -> None:
    session = _Session(clock, [_status(done=74, seconds=92.6)])
    status = await _poll(session, until=10.0, early_accept=False)
    assert jobs.job_state(status) == "running"
    assert session.status_calls() == 10 and clock.now == 10.0


def _scope(until: float) -> _JobScope:
    return _JobScope(sub="alice", owner=OWNER, thread_id=None, call_timeout=10.0,
                     poll_until=until, app_config=None)


@pytest.mark.asyncio
async def test_data_job_is_accepted_early(clock) -> None:
    """일반 데이터 조회(`_await_job`) — 조기 접수 봉투를 마감 도달과 같은 접수로 처리한다."""
    session = _Session(clock, [_status(done=74, seconds=92.6)])
    call = _Call(SimpleNamespace(), "web01", {}, job=_status()["job"])
    await jobs_notices._await_job(session, call, _scope(30.0), None)
    assert call.accepted and call.error is None and call.envelope is None
    assert call.job is not None and call.job["progress"]["done"] == 74
    assert clock.now == 1.0


@pytest.mark.asyncio
async def test_first_hop_keeps_waiting_until_deadline_then_cancels(clock) -> None:
    """대상 선정용 첫 홉 — 예상이 마감을 넘어도 마감까지 기다린 뒤 취소한다(SPEC §3.8 종전)."""
    session = _Session(clock, [_status(done=74, seconds=92.6)])
    call = _Call(SimpleNamespace(), None, {}, envelope=_status())
    await jobs_notices._await_first_hop(session, call, _scope(10.0))
    assert session.status_calls() == 10
    assert session.calls[-1] == (jobs.CANCEL_TOOL, 10.0)
    assert call.error == _FIRST_HOP_TIMEOUT_ERROR
