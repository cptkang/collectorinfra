"""남은 예상 시간 보정 — 소스별 실 호출 소요 이동 평균 (plans/164 W7-B · 게이트 G-11 (a) · 수용 9).

폐쇄망에서 1073회 X-View 작업이 「예상 약 3.6분」(1073 ÷ 5회/초)으로 보였는데 실측은 호출당 약
12.5초였다. 가짜 어댑터가 호출마다 12초 소요를 기록하면(실제 sleep 없음) 몇 호출 뒤
`estimate.seconds`가 남은 호출 × 12초(≈3.6시간)로 바뀌고 남은 호출이 줄면 줄어든다. 표본이 없으면
종전 값 + `basis = rate_only`다. 외부 호출 0.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.application.jobs import BASIS_MEASURED, BASIS_RATE_ONLY, JobManager
from apm_gateway.application.spool import Spool
from apm_gateway.domain.call_context import (
    CallScope,
    SharedLoadScope,
    current_scope,
    expect_calls,
    use_scope,
)
from conftest import NOW_S, make_tools

from apm_gateway.config import JenniferApiConfig, JobConfig

TOOL = "apm_xview"
SOURCE = "jennifer"
CALLS = 1073
FAKE_CALL_SECONDS = 12.0


@pytest.fixture
def build(tmp_path):
    tools, _ = make_tools("")

    def make() -> tuple[JobManager, object]:
        cfg = JobConfig(spool_dir=tmp_path / "spool")
        mgr = JobManager(
            Spool(cfg.spool_dir),
            cfg,
            envelope=tools.ok,
            error_envelope=tools.err,
            rate_per_sec=5,
            clock=lambda: NOW_S,
        )
        return mgr, tools

    return make


async def _fake_call(seconds: float | None) -> None:
    """가짜 어댑터 호출 1회 — 훅 순서는 실제 클라이언트와 같다(소요는 기록만 · sleep 없음)."""
    scope = current_scope()
    await scope.before_call(SOURCE)
    if seconds is not None:
        scope.after_call(SOURCE, seconds)


async def _status(mgr: JobManager, job_id: str) -> dict:
    st = await mgr.status(job_id, principal="chat", owner="o")
    return st["job"]["estimate"]


@pytest.mark.asyncio
async def test_long_xview_estimate_switches_to_measured_and_shrinks(build):
    mgr, tools = build()
    gates = [asyncio.Event(), asyncio.Event(), asyncio.Event()]
    reached = [asyncio.Event(), asyncio.Event(), asyncio.Event()]
    stops = (0, 3, 803)

    async def call():
        expect_calls(CALLS)
        for i in range(CALLS):
            if i in stops:
                k = stops.index(i)
                reached[k].set()
                await gates[k].wait()
            await _fake_call(FAKE_CALL_SECONDS)
        return tools.ok(TOOL, [])

    out = await mgr.execute(TOOL, call, principal="chat", owner="o", wait_seconds=0)
    job_id = out["job"]["job_id"]
    await reached[0].wait()
    # 첫 호출 전(표본 0) — 종전 식 1073 ÷ 5 = 214.6초 · 지연 미반영
    assert await _status(mgr, job_id) == {
        "api_calls": CALLS,
        "seconds": round(CALLS / 5, 1),
        "basis": BASIS_RATE_ONLY,
    }

    gates[0].set()
    await reached[1].wait()
    first = await _status(mgr, job_id)  # 상태 조회마다 다시 계산
    assert first["basis"] == BASIS_MEASURED and first["api_calls"] == CALLS
    assert first["seconds"] == round((CALLS - 3) * FAKE_CALL_SECONDS, 1)
    assert 3.5 * 3600 <= first["seconds"] <= 3.7 * 3600  # 약 3.6시간(종전 3.6분이 아니라)

    gates[1].set()
    await reached[2].wait()
    later = await _status(mgr, job_id)
    assert later["basis"] == BASIS_MEASURED
    assert later["seconds"] == round((CALLS - 803) * FAKE_CALL_SECONDS, 1) < first["seconds"]

    gates[2].set()
    await asyncio.gather(*list(mgr._tasks), return_exceptions=True)
    assert mgr.call_seconds(SOURCE) == pytest.approx(FAKE_CALL_SECONDS)


@pytest.mark.asyncio
async def test_no_sample_keeps_previous_value_and_marks_rate_only(build):
    mgr, tools = build()
    gate, reached = asyncio.Event(), asyncio.Event()

    async def call():
        expect_calls(CALLS)
        for _ in range(5):
            await _fake_call(None)  # 응답을 못 받은 호출(소요 기록 없음)
        reached.set()
        await gate.wait()
        return tools.ok(TOOL, [])

    out = await mgr.execute(TOOL, call, principal="chat", owner="o", wait_seconds=0)
    await reached.wait()
    est = await _status(mgr, out["job"]["job_id"])
    assert est == {"api_calls": CALLS, "seconds": round(CALLS / 5, 1), "basis": BASIS_RATE_ONLY}
    assert mgr.call_seconds() is None
    gate.set()
    await asyncio.gather(*list(mgr._tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_fast_calls_are_bounded_by_rate_interval(build):
    """실측 소요가 속도 간격(1/5초)보다 짧으면 간격이 지배한다 — 남은 호출 × 0.2초."""
    mgr, tools = build()
    gate, reached = asyncio.Event(), asyncio.Event()

    async def call():
        expect_calls(100)
        for _ in range(10):
            await _fake_call(0.01)
        reached.set()
        await gate.wait()
        return tools.ok(TOOL, [])

    out = await mgr.execute(TOOL, call, principal="chat", owner="o", wait_seconds=0)
    await reached.wait()
    est = await _status(mgr, out["job"]["job_id"])
    assert est == {"api_calls": 100, "seconds": 18.0, "basis": BASIS_MEASURED}
    gate.set()
    await asyncio.gather(*list(mgr._tasks), return_exceptions=True)


def test_moving_average_and_pre_execution_lookup(build):
    """작업 없이(실행 전 견적) 소스별·최댓값 조회 · 지수 이동 평균 · 비정상 표본 무시."""
    mgr, _ = build()
    assert mgr.call_seconds() is None and mgr.call_seconds(SOURCE) is None
    mgr.observe_call(SOURCE, 10.0)
    assert mgr.call_seconds(SOURCE) == 10.0  # 첫 표본은 그대로
    mgr.observe_call(SOURCE, 20.0)
    assert mgr.call_seconds(SOURCE) == pytest.approx(13.0)  # 10 + 0.3 × (20 − 10)
    mgr.observe_call("other", 1.0)
    mgr.observe_call("other", float("nan"))
    mgr.observe_call("other", -1.0)
    assert mgr.call_seconds("other") == 1.0
    assert mgr.call_seconds() == pytest.approx(13.0)  # 잰 소스 중 가장 느린 값


class _Recorder(CallScope):
    def __init__(self) -> None:
        super().__init__()
        self.timed: list[tuple[str, float]] = []
        self.kinds: list[tuple[str, bool]] = []  # (경로 군, 공유 적재) — plans/164 W7-C 교정

    def after_call(
        self, source_id: str, seconds: float, *, group: str = "", shared: bool = False
    ) -> None:
        self.timed.append((source_id, seconds))
        self.kinds.append((group, shared))


@pytest.mark.asyncio
async def test_client_reports_http_time_excluding_throttle(monkeypatch):
    """실 클라이언트는 속도 제어 대기 뒤부터 본문 수신 끝까지를 `after_call`로 넘긴다(가짜 시계)."""
    import apm_gateway.adapters.jennifer.client as mod

    ticks = iter([100.0, 112.5, 200.0, 201.0])

    class _FakeTime:
        @staticmethod
        def monotonic() -> float:
            return next(ticks)

    monkeypatch.setattr(mod, "time", _FakeTime)
    responses = [httpx.Response(200, json={"result": []}), httpx.Response(500, text="x")]
    client = JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t", rate_limit_per_sec=0, source_id=SOURCE),
        transport=httpx.MockTransport(lambda r: responses.pop(0)),
    )
    rec = _Recorder()
    with use_scope(rec):
        await client.get_json("/api/domain")
        with pytest.raises(Exception):
            await client.get_json("/api/domain")  # 오류 응답도 응답을 받았으면 소요를 잰다
    await client.aclose()
    assert rec.timed == [(SOURCE, 12.5), (SOURCE, 1.0)]
    assert rec.kinds == [("/api/domain", False), ("/api/domain", False)]  # 경로 군 = 경로 템플릿


def test_shared_load_scope_forwards_timing_to_origin():
    rec = _Recorder()
    SharedLoadScope(rec).after_call(SOURCE, 3.0, group="/api/instance")
    assert rec.timed == [(SOURCE, 3.0)]
    assert rec.kinds == [("/api/instance", True)]  # 공유 적재 표지 — 작업 견적 표본에서 뺀다
