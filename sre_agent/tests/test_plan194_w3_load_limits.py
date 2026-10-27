"""plans/194 W3 · D-347 — 조사 서비스 부하 상한.

대기 상한(`investigation_max_queued`) · 접수 응답 즉시 거부 · 대기 순번 상시 노출 ·
버려진 조사 스레드 계상(슬롯·L-4 호스트 키 유지 → 끝나면 반환 · grace 강제 반환·감사) ·
협조적 중단(가짜 스트림) · 프로세스 전역 도구 동시 상한(축별) · `sre_health` 부하 게이지.

실 LLM·MCP 호출 0 — 가짜 조사 함수·가짜 스트림·가짜 MCP 세션.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from holmes.core.tool_calling_llm import LLMInterruptedError
from holmes.utils.stream import StreamEvents
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import (
    GUARD_QUEUE_FULL,
    GUARD_SERVICE_OVERLOADED,
    InvestigationDispatcher,
)
from sre_agent.application.investigation_jobs import CONTRACT_VERSION, InvestigationJob, JobStore
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord, _StreamDrain
from sre_agent.infrastructure import mcp_tool_client as mc
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings


def make_settings(**overrides: Any) -> AgentSettings:
    defaults: dict[str, Any] = {
        "model": "test/model",
        "api_key": None,
        "max_steps": 3,
        "gemini_api_key": "k",
        "service_bearer_token": None,
        "investigation_timeout_seconds": 300,
        "investigation_dedup_ttl_seconds": None,
        "investigation_max_concurrent": 1,
        "investigation_max_queued": 1,
        "investigation_abandon_grace_seconds": 5.0,
        "investigation_hourly_budget": None,
        "severity_judge_enabled": False,
        "sre_activity_log_enabled": False,
    }
    return AgentSettings(_env_file=None, **{**defaults, **overrides})


def make_job(server: str, iid: str | None = None) -> InvestigationJob:
    now = time.time()
    return InvestigationJob(
        investigation_id=iid or f"id-{server}-{now}", kind="alarm", status="running",
        created_at=now, updated_at=now, fingerprint=f"fp-{server}",
        payload={"event": {"serverName": server, "severity": 2}, "decision": {"tier": "PAGE"}},
    )


def ok_result() -> DiagnosisResult:
    return DiagnosisResult(answer="원인 ← journalctl",
                           tool_outputs=[ToolCallRecord("journalctl", "로그", "success", "clean")])


class Gate:
    """풀릴 때까지 막히는 가짜 조사 함수 — 시작 순서를 기록한다(협조적 중단은 무시)."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, job: Any) -> DiagnosisResult:
        with self._lock:
            self.started.append(job.investigation_id)
        self.release.wait(30)
        return ok_result()


def until(pred: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def dispatcher(fn: Any, settings: AgentSettings | None = None,
               **kw: Any) -> InvestigationDispatcher:
    return InvestigationDispatcher(settings or make_settings(), diagnose_fn=fn,
                                   briefing_fn=build_briefing, **kw)


# ── 1. 대기 상한 ─────────────────────────────────────────────────────────


def test_queue_full_rejects_without_creating_worker() -> None:
    gate = Gate()
    d = dispatcher(gate)
    j1, j2, j3 = make_job("a"), make_job("b"), make_job("c")
    try:
        d(j1)
        assert until(lambda: gate.started == [j1.investigation_id])
        d(j2)
        assert until(lambda: j2.queue_position == 1)   # 기록 off여도 대기 순번이 달린다
        d(j3)
        assert j3.status == "rejected" and j3.reason == GUARD_QUEUE_FULL
        assert j3.verdict == "조사 거부 — queue_full"
        assert len(d._workers) == 2, "거부된 잡은 대기 스레드를 만들지 않는다"
        load = d.load_snapshot()
        assert (load["running"], load["queued"], load["max_queued"]) == (1, 1, 1)
    finally:
        gate.release.set()
    d.wait_workers(10)
    assert (j1.status, j2.status) == ("done", "done")
    assert j2.queue_position is None


def test_queue_unlimited_when_none() -> None:
    gate = Gate()
    d = dispatcher(gate, make_settings(investigation_max_queued=None))
    jobs = [make_job(f"h{i}") for i in range(6)]
    try:
        for j in jobs:
            d(j)
        assert all(j.status == "running" for j in jobs)
    finally:
        gate.release.set()
    d.wait_workers(10)
    assert all(j.status == "done" for j in jobs)


def test_stub_path_is_not_counted_against_queue() -> None:
    """스텁(조사 LLM 게이트 차단)은 슬롯을 쓰지 않는다 — 대기 상한에 걸리지 않는다."""
    d = dispatcher(Gate(), make_settings(gemini_api_key=None, investigation_max_queued=0))
    jobs = [make_job(f"s{i}") for i in range(4)]
    for j in jobs:
        d(j)
    assert all(j.status == "stub" for j in jobs)


def test_submit_guard_prunes_finished_abandoned_threads() -> None:
    """버려진 스레드 목록은 게이지 조회 없이도 가드 진입(submit)에서 끝난 것을 정리한다."""
    d = dispatcher(Gate(), make_settings(gemini_api_key=None))
    done, alive = threading.Event(), threading.Event()
    done.set()
    with d._lock:
        d._abandoned.extend([done, alive])
    d(make_job("p1"))   # 스텁 경로 — 가드는 그대로 지난다
    with d._lock:
        assert d._abandoned == [alive]


# ── 2. 접수 응답 즉시 거부 · 대기 순번 상시 ───────────────────────────────


def alarm_payload(host: str) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "event": {"serverName": host, "hostname": host, "severity": 2},
        "decision": {"fingerprint": f"fp-{host}"},
    }


def test_submit_returns_rejected_immediately(tmp_path) -> None:
    gate = Gate()
    settings = make_settings(investigation_max_queued=0)
    d = dispatcher(gate, settings)
    store = JobStore(settings, executor=d, audit_path=tmp_path / "a.jsonl")
    try:
        first = store.submit(alarm_payload("web-01"))
        assert first["status"] == "accepted"
        assert until(lambda: len(gate.started) == 1)
        # 같은 호스트(L-4) — 종전에는 accepted 뒤 poll해야 알았다
        same = store.submit({**alarm_payload("web-01"), "decision": {"fingerprint": "other"}})
        assert same["status"] == "rejected" and same["reason"] == "host_investigation_in_flight"
        assert same["verdict"] == "조사 거부 — host_investigation_in_flight"
        full = store.submit(alarm_payload("web-02"))
        assert full == {"investigation_id": full["investigation_id"], "status": "rejected",
                        "reason": GUARD_QUEUE_FULL, "verdict": "조사 거부 — queue_full"}
        assert store.get(full["investigation_id"])["status"] == "rejected"
        diag = store.submit_diagnosis("원인?", hostname="db-01")
        assert diag["status"] == "rejected" and diag["reason"] == GUARD_QUEUE_FULL
    finally:
        gate.release.set()
    d.wait_workers(10)


def test_queue_position_in_get_without_activity_log(tmp_path) -> None:
    gate = Gate()
    settings = make_settings()
    d = dispatcher(gate, settings)
    store = JobStore(settings, executor=d, audit_path=tmp_path / "a.jsonl")
    try:
        a = store.submit(alarm_payload("web-01"))["investigation_id"]
        assert until(lambda: len(gate.started) == 1)
        b = store.submit(alarm_payload("web-02"))["investigation_id"]
        assert until(lambda: store.get(b).get("queue_position") == 1)
        view = store.get(b)
        assert view["status"] == "running" and view["phase"] == "queued"
        assert "phase" not in store.get(a) and "queue_position" not in store.get(a)
    finally:
        gate.release.set()
    d.wait_workers(10)
    final = store.get(b)
    assert final["status"] == "done" and "phase" not in final and "queue_position" not in final


# ── 3. 버려진 조사 스레드 계상 ────────────────────────────────────────────


def test_abandoned_thread_holds_slot_and_host_key_until_it_ends() -> None:
    gate = Gate()
    d = dispatcher(gate, make_settings(investigation_max_queued=2), timeout_seconds=0.2)
    j1 = make_job("web-01")
    d(j1)
    d.wait_workers(10)
    try:
        assert j1.status == "timeout" and j1.reason == "investigation_timeout"  # 상태는 즉시 확정
        assert j1.cancel_event.is_set(), "버리기 전에 협조적 중단 신호를 세운다"
        load = d.load_snapshot()
        assert (load["running"], load["abandoned_slots"], load["abandoned_threads"]) == (0, 1, 1)
        again = make_job("web-01")
        d(again)
        assert again.status == "rejected" and again.reason == "host_investigation_in_flight"
        other = make_job("web-02")
        d(other)
        assert until(lambda: other.queue_position == 1), "슬롯은 버려진 스레드가 쥐고 있다"
        assert len(gate.started) == 1
    finally:
        gate.release.set()
    assert until(lambda: d.load_snapshot()["abandoned_slots"] == 0)
    d.wait_workers(10)
    assert other.status in ("done", "timeout")
    assert until(lambda: d.load_snapshot()["abandoned_threads"] == 0)
    assert until(lambda: not d._inflight_hosts)


def test_grace_forces_release_and_audits(tmp_path) -> None:
    audit = tmp_path / "a.jsonl"
    gate = Gate()
    d = dispatcher(gate, make_settings(investigation_abandon_grace_seconds=0.3),
                   timeout_seconds=0.2, audit_path=audit)
    j1 = make_job("web-01", iid="zombie-1")
    try:
        d(j1)
        d.wait_workers(10)
        assert j1.status == "timeout"
        assert until(lambda: d.load_snapshot()["abandoned_slots"] == 0)
        load = d.load_snapshot()
        assert load["abandoned_threads"] == 1, "스레드는 계속 돈다 — 게이지에 남는다"
        assert load["running"] == 0 and not d._inflight_hosts
        events = [json.loads(line) for line in audit.read_text().splitlines()]
        rel = [e for e in events if e["event"] == "zombie_released"]
        assert len(rel) == 1 and rel[0]["investigation_id"] == "zombie-1"
        assert rel[0]["threads"] == 1 and rel[0]["grace_s"] == 0.3
        assert d._semaphore.acquire(blocking=False), "슬롯이 반환됐다"
        d._semaphore.release()
    finally:
        gate.release.set()
    assert until(lambda: d.load_snapshot()["abandoned_threads"] == 0)


def test_service_overloaded_when_zombie_fills_capacity() -> None:
    gate = Gate()
    d = dispatcher(gate, make_settings(investigation_max_queued=0), timeout_seconds=0.2)
    try:
        d(make_job("web-01"))
        d.wait_workers(10)
        assert d.load_snapshot()["abandoned_slots"] == 1
        nxt = make_job("web-02")
        d(nxt)
        assert nxt.status == "rejected" and nxt.reason == GUARD_SERVICE_OVERLOADED
    finally:
        gate.release.set()
    assert until(lambda: d.load_snapshot()["abandoned_slots"] == 0)
    later = make_job("web-03")   # 버려진 스레드가 끝나 슬롯이 돌아오면 다시 받는다
    d(later)
    d.wait_workers(10)
    assert later.status == "done"


def test_cooperative_cancel_ends_zombie_before_grace() -> None:
    """조사 함수가 `job.cancel_event`를 보면 버려진 뒤 곧 끝나 슬롯이 grace 전에 돌아온다."""
    seen: list[bool] = []

    def cooperative(job: Any) -> DiagnosisResult:
        ev = job.cancel_event
        seen.append(ev.wait(10))
        return ok_result()

    d = dispatcher(cooperative, make_settings(investigation_abandon_grace_seconds=30),
                   timeout_seconds=0.2)
    j = make_job("web-01")
    t0 = time.monotonic()
    d(j)
    d.wait_workers(10)
    assert j.status == "timeout"
    assert until(lambda: d.load_snapshot()["abandoned_slots"] == 0, timeout=5)
    assert time.monotonic() - t0 < 5 and seen == [True]


def test_finished_investigation_releases_slot_immediately() -> None:
    d = dispatcher(lambda job: ok_result(), make_settings())
    j = make_job("web-01")
    d(j)
    d.wait_workers(10)
    assert j.status == "done" and not j.cancel_event.is_set()
    load = d.load_snapshot()
    assert (load["running"], load["queued"], load["abandoned_slots"]) == (0, 0, 0)
    assert not d._inflight_hosts and not d._helpers


# ── 4. 협조적 중단 — _StreamDrain(가짜 스트림) · 조사 함수 배선 ─────────────


def _ev(kind: Any, **data: Any) -> SimpleNamespace:
    return SimpleNamespace(event=kind, data=data)


class FakeStreamLLM:
    """`call_stream` 대역 — 받은 kwargs를 남기고, 첫 이벤트 뒤에 `on_first`를 부른다."""

    def __init__(self, on_first: Callable[[], None] | None = None) -> None:
        self.kwargs: list[dict] = []
        self.on_first = on_first
        self.yielded: list[Any] = []

    def call_stream(self, **kwargs: Any):
        self.kwargs.append(kwargs)
        first = _ev(StreamEvents.AI_MESSAGE, content="생각 중", reasoning=None)
        self.yielded.append(first.event)
        yield first
        if self.on_first is not None:
            self.on_first()
        second = _ev(StreamEvents.START_TOOL, tool_name="t", id="1")
        self.yielded.append(second.event)
        yield second
        end = _ev(StreamEvents.ANSWER_END, content="끝", messages=[], num_llm_calls=1, costs={})
        self.yielded.append(end.event)
        yield end


def test_stream_drain_stops_between_events_on_cancel() -> None:
    cancel = threading.Event()
    llm = FakeStreamLLM(on_first=cancel.set)
    drain = _StreamDrain(llm, cancel_event=cancel)  # type: ignore[arg-type]
    with pytest.raises(LLMInterruptedError):
        drain.run([{"role": "user", "content": "q"}])
    assert llm.kwargs[0]["cancel_event"] is cancel, "holmes 내부 확인 지점에도 넘긴다"
    assert StreamEvents.ANSWER_END not in llm.yielded
    assert drain.tool_calls == []


def test_stream_drain_cancel_before_start_makes_no_call() -> None:
    cancel = threading.Event()
    cancel.set()
    llm = FakeStreamLLM()
    with pytest.raises(LLMInterruptedError):
        _StreamDrain(llm, cancel_event=cancel).run([])  # type: ignore[arg-type]
    assert llm.kwargs == []


def test_stream_drain_without_cancel_passes_no_kwarg() -> None:
    llm = FakeStreamLLM()
    res = _StreamDrain(llm).run([])  # type: ignore[arg-type]
    assert res.result == "끝" and "cancel_event" not in llm.kwargs[0]


def test_diagnose_fn_forwards_cancel_event() -> None:
    asked: list[dict] = []

    class FakeAgent:
        def mcp_unavailable(self) -> dict:
            return {}

        def tool_names(self) -> frozenset:
            return frozenset()

        def ask(self, question: str, **kw: Any) -> DiagnosisResult:
            asked.append(kw)
            return ok_result()

    settings = make_settings(polestar_mcp_url="")
    fn = mcp_service._default_diagnose_fn(settings, None, lambda: FakeAgent())
    job = make_job("web-01")
    fn(job)
    assert "cancel_event" not in asked[-1]                   # 신호가 없으면 종전 호출 그대로
    job.cancel_event = threading.Event()  # type: ignore[attr-defined]
    fn(job)
    assert asked[-1]["cancel_event"] is job.cancel_event


# ── 5. 전역 도구 동시 상한 ───────────────────────────────────────────────


def _install_session(monkeypatch: pytest.MonkeyPatch, delay: float = 0.1) -> dict:
    """스레드 안전 대역 세션 — 여러 배치(스레드·루프)에 걸친 최대 동시 호출을 잰다."""
    obs: dict[str, Any] = {"active": 0, "peak": 0, "calls": 0}
    lock = threading.Lock()

    class _Session:
        def __init__(self, read: Any, write: Any) -> None:
            pass

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        async def initialize(self) -> None:
            pass

        async def call_tool(self, name: str, arguments: Any = None,
                            read_timeout_seconds: Any = None) -> SimpleNamespace:
            with lock:
                obs["calls"] += 1
                obs["active"] += 1
                obs["peak"] = max(obs["peak"], obs["active"])
            try:
                await asyncio.sleep(delay)
                return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"tool": name}))])
            finally:
                with lock:
                    obs["active"] -= 1

    @asynccontextmanager
    async def fake_sse(url: str, headers: Any = None, timeout: float = 5, **kw: Any):
        yield ("r", "w")

    import mcp
    import mcp.client.sse
    monkeypatch.setattr(mcp.client.sse, "sse_client", fake_sse)
    monkeypatch.setattr(mcp, "ClientSession", _Session)
    return obs


def test_global_limit_bounds_concurrency_across_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    obs = _install_session(monkeypatch)
    sem = threading.BoundedSemaphore(3)
    outs: list[list[str]] = []

    def batch(prefix: str) -> None:
        calls = [(f"{prefix}{i}", {}) for i in range(4)]
        outs.append(mc.run_tool_batch("http://x/sse", None, calls, concurrency=4,
                                      global_limit=sem))

    threads = [threading.Thread(target=batch, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert obs["calls"] == 8 and obs["peak"] <= 3, obs
    assert all("error" not in json.loads(x) for out in outs for x in out)
    assert all(sem.acquire(blocking=False) for _ in range(3)), "자리를 전부 돌려줬다"


def test_global_limit_wait_timeout_marks_only_that_call(monkeypatch: pytest.MonkeyPatch) -> None:
    obs = _install_session(monkeypatch)
    sem = threading.BoundedSemaphore(1)
    sem.acquire()  # 다른 조사가 자리를 쥐고 있다
    try:
        out = mc.run_tool_batch("http://x/sse", None, [("a", {})], timeout_seconds=0.2,
                                concurrency=2, global_limit=sem)
    finally:
        sem.release()
    err = json.loads(out[0])["error"]
    assert err.startswith(mc.TOOL_CONCURRENCY_WAIT_TIMEOUT) and obs["calls"] == 0


def test_batch_callers_use_per_axis_process_global_semaphore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []

    def fake_run(url: str, headers: Any, calls: Any, **kw: Any) -> list[str]:
        seen.append(kw.get("global_limit"))
        return []

    monkeypatch.setattr(mc, "run_tool_batch", fake_run)
    s = make_settings(polestar_mcp_url="http://p/sse", apm_mcp_url="http://g/sse",
                      sre_tool_global_concurrency=5)
    for make in (mc.make_batch_caller, mc.make_batch_caller, mc.make_apm_batch_caller):
        make(s)([("t", {})])  # type: ignore[misc]
    pol1, pol2, apm = seen
    assert pol1 is pol2 and pol1 is not apm, "축별 · 프로세스 전역(배치 사이 공유)"
    off = make_settings(polestar_mcp_url="http://p/sse", sre_tool_global_concurrency=None)
    seen.clear()
    mc.make_batch_caller(off)([("t", {})])  # type: ignore[misc]
    assert seen == [None]


# ── 6. 설정 · sre_health 게이지 ──────────────────────────────────────────


def test_new_settings_defaults_and_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    s = AgentSettings(_env_file=None)
    assert s.investigation_max_queued == 4
    assert s.investigation_abandon_grace_seconds == 120.0
    assert s.sre_tool_global_concurrency == 8
    monkeypatch.setenv("INVESTIGATION_MAX_QUEUED", "7")
    monkeypatch.setenv("INVESTIGATION_ABANDON_GRACE_SECONDS", "30")
    monkeypatch.setenv("SRE_TOOL_GLOBAL_CONCURRENCY", "2")
    s2 = AgentSettings(_env_file=None)
    assert (s2.investigation_max_queued, s2.investigation_abandon_grace_seconds,
            s2.sre_tool_global_concurrency) == (7, 30.0, 2)


def test_sre_health_carries_load_gauges(tmp_path) -> None:
    settings = make_settings(investigation_max_queued=3, sre_tool_global_concurrency=6)
    d = dispatcher(lambda job: ok_result(), settings)
    store = JobStore(settings, executor=d, audit_path=tmp_path / "a.jsonl")
    svc = mcp_service.create_service(settings, job_store=store)
    res = asyncio.run(svc.call_tool("sre_health", {}))
    content = res[0] if isinstance(res, tuple) else res
    health = json.loads(content[0].text)
    assert health["status"] == "ok"
    assert {k: health[k] for k in ("running", "queued", "abandoned_slots", "abandoned_threads",
                                   "max_concurrent", "max_queued", "abandon_grace_seconds",
                                   "tool_global_concurrency")} == {
        "running": 0, "queued": 0, "abandoned_slots": 0, "abandoned_threads": 0,
        "max_concurrent": 1, "max_queued": 3, "abandon_grace_seconds": 5.0,
        "tool_global_concurrency": 6,
    }


def test_sre_health_without_dispatcher_has_no_gauges(tmp_path) -> None:
    settings = make_settings()
    store = JobStore(settings, audit_path=tmp_path / "a.jsonl")   # 스텁 executor
    svc = mcp_service.create_service(settings, job_store=store)
    res = asyncio.run(svc.call_tool("sre_health", {}))
    content = res[0] if isinstance(res, tuple) else res
    assert "running" not in json.loads(content[0].text)


# ── 7. 플레이북 경로 — 심층(HolmesGPT) 타임아웃도 같은 계상 ──────────────────


def test_playbook_deep_timeout_holds_slot_and_signals_cancel() -> None:
    from sre_agent.application.investigation_dispatcher import PlaybookWiring
    from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load
    from test_plan187_w4_dispatch import FakeDeep, alarm_of
    from test_plan187_w4_dispatch import make_settings as pb_settings

    settings = pb_settings(sre_deep_dive_mode="parallel", investigation_max_concurrent=1,
                           investigation_max_queued=1, investigation_abandon_grace_seconds=10)
    gate = threading.Event()
    deep = FakeDeep(settings, gate=gate)
    caller = FakeCaller(_load("latency_sql"))
    d = InvestigationDispatcher(
        settings, diagnose_fn=deep, briefing_fn=build_briefing, timeout_seconds=1.0,
        playbook=PlaybookWiring(playbooks=tuple(PLAYBOOKS), polestar=caller, apm=caller),
    )
    now = time.time()
    job = InvestigationJob(investigation_id="pb-1", kind="alarm", status="accepted",
                           created_at=now, updated_at=now, payload=alarm_of("latency_sql"))
    try:
        d(job)
        d.wait_workers(30)
        assert job.status == "done"
        assert job.briefing["deep_dive"]["status"] == "timeout"
        assert deep.jobs[0].cancel_event is job.cancel_event and job.cancel_event.is_set()
        assert d.load_snapshot()["abandoned_slots"] == 1
    finally:
        gate.set()
    assert until(lambda: d.load_snapshot()["abandoned_slots"] == 0)
    assert until(lambda: d.load_snapshot()["running"] == 0 and not d._inflight_hosts)
