"""plans/207 A1 — HolmesGPT MCP 잠금 범위 · 조사별 전역 자리 몫 · APM 조사별 owner (D-358).

- 기본값(셋 다): 훅을 설치하지 않고 워커에 컨텍스트가 없다 · holmes 잠금 객체가 종전과 같다.
- `SRE_HOLMES_MCP_LOCK_SCOPE=investigation`: 다른 조사끼리 병렬 · 같은 조사 직렬 · 조사 밖·다른
  URL은 서버 잠금 · HolmesGPT 경로도 배치와 **같은** 축 전역 세마포어를 잡는다
  (합이 상한을 넘지 않음).
- `SRE_TOOL_PER_INVESTIGATION_SHARE`: 한 조사가 축별로 동시에 쥐는 전역 자리 수 상한.
- `SRE_APM_OWNER_PER_INVESTIGATION_ENABLED`: 배치·HolmesGPT 경로 APM 호출에 `investigation:<id>`
  (LLM 값 덮어쓰기 · 작업 도구 후속 호출도 같은 owner).

실 LLM·MCP 호출 0 — 가짜 SSE 세션 · 실 holmes `RemoteMCPTool._invoke`에 가짜 도구 객체.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.toolsets.mcp import toolset_mcp
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPTool
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.infrastructure import holmes_mcp_scope as hs
from sre_agent.infrastructure import investigation_context as ic
from sre_agent.infrastructure import mcp_tool_client as mc
from sre_agent.settings import AgentSettings
from tests.test_plan207_investigation_metrics import make_job, make_settings, ok_result

PURL = "http://polestar-mcp:9099/sse"
AURL = "http://apm-gw:9096/sse"


def scope_settings(**overrides: Any) -> AgentSettings:
    base: dict[str, Any] = {"polestar_mcp_url": PURL, "apm_mcp_url": AURL,
                            "sre_tool_global_concurrency": 8}
    return make_settings(**{**base, **overrides})


class Gauge:
    """동시 실행 수 계측(배치 가짜 세션 · HolmesGPT 가짜 도구 공용)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.now = 0
        self.peak = 0
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def enter(self, name: str, args: dict[str, Any]) -> None:
        with self._lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
            self.seen.append((name, dict(args)))

    def leave(self) -> None:
        with self._lock:
            self.now -= 1


@pytest.fixture
def restore(monkeypatch: pytest.MonkeyPatch) -> None:
    """설치는 전역 클래스·모듈 속성을 바꾼다 — 테스트 뒤 원상 복구 · 전역 세마포어 격리."""
    monkeypatch.setattr(threading.Thread, "start", threading.Thread.start)
    monkeypatch.setattr(toolset_mcp, "get_server_lock", toolset_mcp.get_server_lock)
    monkeypatch.setattr(RemoteMCPTool, "_invoke", RemoteMCPTool.__dict__["_invoke"])
    monkeypatch.setattr(ThreadPoolExecutor, "submit", ThreadPoolExecutor.submit)
    monkeypatch.setattr(hs, "_CONFIG", None)
    monkeypatch.setattr(hs, "_UNMATCHED_WARNED", set())
    monkeypatch.setattr(mc, "_GLOBAL_LIMITS", {})


def install_session(monkeypatch: pytest.MonkeyPatch, gauge: Gauge, delay: float = 0.1) -> None:
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
            gauge.enter(name, arguments or {})
            try:
                await asyncio.sleep(delay)
            finally:
                gauge.leave()
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"tool": name}))])

    @asynccontextmanager
    async def fake_sse(url: str, headers: Any = None, timeout: float = 5, **kw: Any):
        yield ("r", "w")

    import mcp
    import mcp.client.sse
    monkeypatch.setattr(mcp.client.sse, "sse_client", fake_sse)
    monkeypatch.setattr(mcp, "ClientSession", _Session)


class FakeTool:
    """실 `RemoteMCPTool._invoke`가 쓰는 속성만 갖춘 도구(`_invoke_async`만 가짜)."""

    def __init__(self, url: str, gauge: Gauge, name: str = "polestar_x",
                 params: tuple[str, ...] = ("host",), delay: float = 0.15) -> None:
        self.name = self.mcp_tool_name = name
        self.parameters = dict.fromkeys(params, object())
        self.toolset = SimpleNamespace(_mcp_config=SimpleNamespace(get_lock_string=lambda: url))
        self._gauge = gauge
        self._delay = delay

    def _is_placeholder_connect_tool(self) -> bool:
        return False

    async def _invoke_async(self, params: dict, request_context: Any) -> StructuredToolResult:
        self._gauge.enter(self.name, params)
        try:
            await asyncio.sleep(self._delay)
        finally:
            self._gauge.leave()
        return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data="ok",
                                    params=params)


def holmes_call(tool: FakeTool, params: dict | None = None) -> StructuredToolResult:
    """holmes `Tool.invoke`와 같은 방식(키워드 인자)으로 실 `_invoke`를 부른다."""
    return RemoteMCPTool._invoke(tool, params=params or {},  # type: ignore[arg-type]
                                 context=SimpleNamespace(request_context=None))


def run_threads(targets: list[Any]) -> None:
    threads = [threading.Thread(target=t) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)


def in_investigation(iid: str, fn: Any) -> Any:
    def _target() -> None:
        ctx = ic.bind(iid)
        try:
            fn()
        finally:
            ic.unbind(ctx)
    return _target


# ── 기본값 = 비트 동일 ────────────────────────────────────────────────


def test_defaults_are_off() -> None:
    fields = AgentSettings.model_fields
    assert fields["sre_holmes_mcp_lock_scope"].default == "server"
    assert fields["sre_tool_per_investigation_share"].default is None
    assert fields["sre_apm_owner_per_investigation_enabled"].default is False
    assert hs.context_needed(make_settings()) is False
    # 코드 기본 불변(D-347)
    assert fields["sre_tool_global_concurrency"].default == 8


def test_defaults_install_no_hook_and_bind_nothing(restore: None, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    before = (threading.Thread.start, toolset_mcp.get_server_lock,
              RemoteMCPTool.__dict__["_invoke"])
    called: list[Any] = []
    monkeypatch.setattr(hs, "install", lambda s: called.append(s))
    seen: list[Any] = []

    def diagnose(job: Any) -> Any:
        child: list[Any] = []
        t = threading.Thread(target=lambda: child.append(ic.current()))
        t.start()
        t.join(5)
        seen.extend([ic.current(), *child])
        return ok_result()

    d = InvestigationDispatcher(scope_settings(), diagnose_fn=diagnose,
                                briefing_fn=build_briefing, audit_path=tmp_path / "a.jsonl")
    d(make_job("h1"))
    d.wait_workers(10)
    assert called == [] and seen == [None, None]
    after = (threading.Thread.start, toolset_mcp.get_server_lock,
             RemoteMCPTool.__dict__["_invoke"])
    assert after == before
    # holmes 잠금 객체는 종전 그대로(URL별 같은 threading.Lock)
    assert toolset_mcp.get_server_lock(PURL) is toolset_mcp._server_locks[PURL]


def test_install_with_defaults_is_noop(restore: None) -> None:
    before = threading.Thread.start
    assert not any(hs.install(make_settings()).values())
    assert threading.Thread.start is before and hs._CONFIG is None


def test_install_is_idempotent(restore: None) -> None:
    s = scope_settings(sre_holmes_mcp_lock_scope="investigation",
                       sre_apm_owner_per_investigation_enabled=True)
    assert hs.install(s) == {"thread_inheritance": True, "holmes_mcp_lock_scope": True,
                             "apm_owner": True}
    assert not any(hs.install(s).values())


# ── 잠금 범위 ─────────────────────────────────────────────────────────


def test_investigation_scope_parallel_across_serial_within(restore: None) -> None:
    hs.install(scope_settings(sre_holmes_mcp_lock_scope="investigation"))
    across = Gauge()
    run_threads([in_investigation(f"i{n}", lambda: holmes_call(FakeTool(PURL, across)))
                 for n in range(3)])
    assert across.peak == 3  # 조사끼리 병렬

    within = Gauge()

    def one_investigation() -> None:  # 같은 조사의 holmes 도구 풀(상속된 스레드 3개)
        run_threads([lambda: holmes_call(FakeTool(PURL, within)) for _ in range(3)])

    run_threads([in_investigation("same", one_investigation)])
    assert within.peak == 1 and len(within.seen) == 3  # 조사 안은 종전처럼 직렬


def test_outside_investigation_and_other_url_use_server_lock(restore: None) -> None:
    hs.install(scope_settings(sre_holmes_mcp_lock_scope="investigation"))
    gauge = Gauge()
    run_threads([lambda: holmes_call(FakeTool(PURL, gauge)) for _ in range(3)])  # 조사 밖
    assert gauge.peak == 1
    assert toolset_mcp.get_server_lock(PURL) is toolset_mcp._server_locks[PURL]
    other = Gauge()  # 조사 안이라도 폴스타·APM 외 URL은 서버 잠금
    run_threads([in_investigation(f"o{n}", lambda: holmes_call(FakeTool("http://x/sse", other)))
                 for n in range(3)])
    assert other.peak == 1


def test_server_scope_keeps_server_lock_even_with_context(restore: None) -> None:
    hs.install(scope_settings(sre_tool_per_investigation_share=2))  # 범위는 server
    gauge = Gauge()
    run_threads([in_investigation(f"s{n}", lambda: holmes_call(FakeTool(PURL, gauge)))
                 for n in range(3)])
    assert gauge.peak == 1


# ── 상한 곱셈 없음 · 조사별 몫 ────────────────────────────────────────


def test_holmes_and_batch_share_one_global_semaphore(restore: None,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    s = scope_settings(sre_holmes_mcp_lock_scope="investigation", sre_tool_global_concurrency=2)
    hs.install(s)
    gauge = Gauge()
    install_session(monkeypatch, gauge, delay=0.1)
    batch = mc.make_batch_caller(s)
    assert batch is not None

    def work(n: int) -> None:
        run_threads([lambda: batch([("polestar_a", {}), ("polestar_b", {}), ("polestar_c", {})]),
                     lambda: holmes_call(FakeTool(PURL, gauge, delay=0.1))])

    run_threads([in_investigation(f"g{n}", lambda n=n: work(n)) for n in range(3)])
    assert len(gauge.seen) == 12
    assert gauge.peak == 2  # 배치 + HolmesGPT 합이 전역 상한 2를 넘지 않는다


def test_holmes_slot_timeout_is_tool_error(restore: None) -> None:
    s = scope_settings(sre_holmes_mcp_lock_scope="investigation", sre_tool_global_concurrency=1,
                       evidence_prefetch_timeout_seconds=0.2)
    hs.install(s)
    sem = mc.global_semaphore("polestar", 1)  # 배치 경로와 같은 객체
    assert sem is not None and sem.acquire(blocking=False)
    out: list[StructuredToolResult] = []
    try:
        run_threads([in_investigation(
            "t1", lambda: out.append(holmes_call(FakeTool(PURL, Gauge()))))])
    finally:
        sem.release()
    (res,) = out
    assert res.status == StructuredToolResultStatus.ERROR
    assert res.error is not None and res.error.startswith(mc.TOOL_CONCURRENCY_WAIT_TIMEOUT)
    # 잠금·자리 누수 없음 — 다음 호출은 바로 된다
    ok: list[StructuredToolResult] = []
    run_threads([in_investigation(
        "t1", lambda: ok.append(holmes_call(FakeTool(PURL, Gauge()))))])
    assert ok[0].status == StructuredToolResultStatus.SUCCESS


def test_per_investigation_share_caps_batch(restore: None,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    s = scope_settings(sre_tool_per_investigation_share=1, sre_tool_batch_concurrency=4)
    hs.install(s)
    batch = mc.make_batch_caller(s)
    assert batch is not None
    calls = [(f"polestar_{i}", {}) for i in range(4)]

    capped = Gauge()
    install_session(monkeypatch, capped, delay=0.05)
    run_threads([in_investigation("c1", lambda: batch(calls))])
    assert capped.peak == 1 and len(capped.seen) == 4

    two = Gauge()  # 다른 조사는 자기 몫을 따로 쓴다
    install_session(monkeypatch, two, delay=0.1)
    run_threads([in_investigation(f"c{n}", lambda: batch(calls[:2])) for n in (2, 3)])
    assert two.peak == 2

    free = Gauge()  # 조사 밖 = 종전(배치 동시 4)
    install_session(monkeypatch, free, delay=0.05)
    batch(calls)
    assert free.peak == 4


def test_share_counts_holmes_and_batch_together(restore: None,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    s = scope_settings(sre_holmes_mcp_lock_scope="investigation",
                       sre_tool_per_investigation_share=1)
    hs.install(s)
    gauge = Gauge()
    install_session(monkeypatch, gauge, delay=0.1)
    batch = mc.make_batch_caller(s)
    assert batch is not None

    def work() -> None:
        run_threads([lambda: batch([("polestar_a", {}), ("polestar_b", {})]),
                     lambda: holmes_call(FakeTool(PURL, gauge, delay=0.1))])

    run_threads([in_investigation("one", work)])
    assert len(gauge.seen) == 3 and gauge.peak == 1


def test_share_wait_timeout_releases_nothing_extra(restore: None,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    s = scope_settings(sre_tool_per_investigation_share=1, sre_tool_global_concurrency=4,
                       evidence_prefetch_timeout_seconds=0.2)
    hs.install(s)
    install_session(monkeypatch, Gauge(), delay=0.01)
    batch = mc.make_batch_caller(s)
    assert batch is not None
    out: list[list[str]] = []

    def hold_share_then_call() -> None:
        ctx = ic.current()
        assert ctx is not None
        gate = ctx.share_gate("polestar", 1)
        assert gate is not None and gate.try_acquire()
        try:
            out.append(batch([("polestar_a", {})]))
        finally:
            gate.release()
        assert gate.held == 0

    run_threads([in_investigation("w1", hold_share_then_call)])
    assert json.loads(out[0][0])["error"].startswith(mc.TOOL_CONCURRENCY_WAIT_TIMEOUT)
    sem = mc.global_semaphore("polestar", 4)
    assert sem is not None and all(sem.acquire(blocking=False) for _ in range(4))  # 전역 누수 0


# ── APM owner ─────────────────────────────────────────────────────────


def test_apm_batch_owner_injected_and_overrides(restore: None,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    s = scope_settings(sre_apm_owner_per_investigation_enabled=True)
    hs.install(s)
    gauge = Gauge()
    install_session(monkeypatch, gauge, delay=0.0)
    apm = mc.make_apm_batch_caller(s)
    assert apm is not None
    calls = [("apm_app_health", {"owner": "llm"}), ("apm_events", {"x": 1}),
             ("gateway_health", {})]
    run_threads([in_investigation("inv-7", lambda: apm(calls))])
    got = dict(gauge.seen)
    assert got["apm_app_health"] == {"owner": "investigation:inv-7"}
    assert got["apm_events"] == {"x": 1, "owner": "investigation:inv-7"}
    assert got["gateway_health"] == {}
    assert calls[0][1] == {"owner": "llm"}  # 호출자 인자는 바꾸지 않는다(복사)

    gauge.seen.clear()  # 조사 밖 = 종전(owner 없음)
    apm([("apm_events", {"x": 1})])
    assert gauge.seen == [("apm_events", {"x": 1})]


def test_apm_batch_owner_off_by_default(restore: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = scope_settings(sre_tool_per_investigation_share=4)  # 컨텍스트는 있지만 owner off
    hs.install(s)
    gauge = Gauge()
    install_session(monkeypatch, gauge, delay=0.0)
    apm = mc.make_apm_batch_caller(s)
    assert apm is not None
    run_threads([in_investigation("inv-8", lambda: apm([("apm_events", {"x": 1})]))])
    assert gauge.seen == [("apm_events", {"x": 1})]


def test_holmes_apm_owner_injected_for_data_and_job_tools(restore: None) -> None:
    hs.install(scope_settings(sre_apm_owner_per_investigation_enabled=True))
    gauge = Gauge()
    data = FakeTool(AURL, gauge, name="apm_app_health", params=("app", "owner"), delay=0.0)
    job = FakeTool(AURL, gauge, name="apm_job_status", params=("job_id", "owner"), delay=0.0)
    health = FakeTool(AURL, gauge, name="gateway_health", params=(), delay=0.0)
    pole = FakeTool(PURL, gauge, name="polestar_x", params=("owner",), delay=0.0)

    def work() -> None:
        holmes_call(data, {"app": "a", "owner": "llm-made"})
        holmes_call(job, {"job_id": "j1"})
        holmes_call(health, {})
        holmes_call(pole, {"owner": "keep"})

    run_threads([in_investigation("inv-9", work)])
    assert gauge.seen == [
        ("apm_app_health", {"app": "a", "owner": "investigation:inv-9"}),
        ("apm_job_status", {"job_id": "j1", "owner": "investigation:inv-9"}),
        ("gateway_health", {}),
        ("polestar_x", {"owner": "keep"}),
    ]
    gauge.seen.clear()  # 조사 밖 = LLM 값 그대로
    holmes_call(data, {"app": "a", "owner": "llm-made"})
    assert gauge.seen == [("apm_app_health", {"app": "a", "owner": "llm-made"})]


# ── dispatcher 배선 ────────────────────────────────────────────────────


def test_dispatcher_binds_investigation_to_worker_descendants(restore: None,
                                                              tmp_path: Path) -> None:
    seen: list[Any] = []

    def diagnose(job: Any) -> Any:
        child: list[Any] = []
        t = threading.Thread(target=lambda: child.append(ic.current()))
        t.start()
        t.join(5)
        mine = ic.current()
        seen.extend([mine.investigation_id if mine else None,
                     child[0].investigation_id if child[0] else None])
        return ok_result()

    s = scope_settings(sre_holmes_mcp_lock_scope="investigation")
    d = InvestigationDispatcher(s, diagnose_fn=diagnose, briefing_fn=build_briefing,
                                audit_path=tmp_path / "a.jsonl")
    d(make_job("h1"))
    d.wait_workers(10)
    assert seen == ["id-h1", "id-h1"]
    assert ic.current() is None  # 조사 밖(테스트 스레드)


def test_scope_lock_measured_by_metrics_as_holmes_wait(restore: None, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """계측을 함께 켜면 조사 범위 잠금 대기도 `holmes_mcp` 대기로 잡힌다(설치 순서)."""
    from holmes.core.llm import DefaultLLM
    from holmes.core.tool_calling_llm import ToolCallingLLM
    from sre_agent.infrastructure import investigation_metrics as im
    monkeypatch.setattr(DefaultLLM, "completion", DefaultLLM.completion)
    monkeypatch.setattr(ToolCallingLLM, "_invoke_llm_tool_call",
                        ToolCallingLLM._invoke_llm_tool_call)

    def diagnose(job: Any) -> Any:
        gauge = Gauge()
        run_threads([lambda: holmes_call(FakeTool(PURL, gauge, delay=0.1)) for _ in range(2)])
        return ok_result()

    s = scope_settings(sre_holmes_mcp_lock_scope="investigation",
                       sre_investigation_metrics_enabled=True)
    audit = tmp_path / "a.jsonl"
    d = InvestigationDispatcher(s, diagnose_fn=diagnose, briefing_fn=build_briefing,
                                audit_path=audit)
    d(make_job("h1"))
    d.wait_workers(10)
    events = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    (ev,) = [e for e in events if e["event"] == im.METRICS_EVENT]
    assert ev["slot_wait_n"].get(im.HOLMES_MCP_AXIS) == 2
    assert ev["slot_wait_s"][im.HOLMES_MCP_AXIS] >= 0.08  # 같은 조사 2건 — 한 건은 기다렸다


# ── 교정(verifier 지적) ────────────────────────────────────────────────


def test_holmes_waiters_do_not_starve_batch_poller() -> None:
    """HolmesGPT 경로도 배치와 같은 간격의 비차단 반복 — 자리 대기자가 줄 서 있어도 배치가 얻는다.

    옛 차단 획득(`sem.acquire(timeout=…)`)이면 같은 조건에서 배치가 3회 모두 못 얻는다(실측).
    """
    sem = threading.BoundedSemaphore(1)
    stop = threading.Event()

    def holmes_like() -> None:  # 실 `_acquire_slot` → 도구 시간 보유 → 반환 → 결과 처리 틈
        while not stop.is_set():
            if hs._acquire_slot(sem, None, 0.5):
                time.sleep(0.03)
                sem.release()
                time.sleep(0.02)

    threads = [threading.Thread(target=holmes_like, daemon=True) for _ in range(3)]
    for t in threads:
        t.start()
    try:
        time.sleep(0.1)
        got: list[bool] = []
        for _ in range(3):  # 얻으면 바로 돌려준다(자리 1개 — 쥔 채로 다시 시도하면 자기와 다툰다)
            got.append(asyncio.run(mc._acquire_global(sem, 1.5)))
            if got[-1]:
                sem.release()
    finally:
        stop.set()
        for t in threads:
            t.join(3)
    assert all(got)


def test_unmatched_url_warns_once(restore: None, caplog: pytest.LogCaptureFixture) -> None:
    hs.install(scope_settings(sre_holmes_mcp_lock_scope="investigation"))
    caplog.set_level(logging.WARNING, logger=hs.__name__)
    run_threads([in_investigation("u1", lambda: holmes_call(FakeTool(PURL, Gauge(), delay=0.0))),
                 in_investigation("u2", lambda: [holmes_call(FakeTool("http://x/sse", Gauge(),
                                                                      delay=0.0))
                                                 for _ in range(2)])])
    warned = [r for r in caplog.records if "어느 쪽과도 맞지 않는다" in r.getMessage()]
    assert len(warned) == 1 and "http://x/sse" in warned[0].getMessage()


def test_configured_url_normalized_like_holmes(restore: None) -> None:
    from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig
    hs.install(scope_settings(apm_mcp_url="http://APM-GW:80/sse/",
                              sre_holmes_mcp_lock_scope="investigation"))
    assert hs._CONFIG is not None
    lock_string = MCPConfig(mode="sse", url="http://APM-GW:80/sse/").get_lock_string()
    assert hs._CONFIG.axis_of(lock_string) == "apm"


def test_pool_task_runs_under_submitter_context(restore: None) -> None:
    hs.install(scope_settings(sre_tool_per_investigation_share=2))
    seen: list[str | None] = []

    def probe() -> str | None:
        ctx = ic.current()
        return ctx.investigation_id if ctx else None

    with ThreadPoolExecutor(max_workers=1) as pool:
        run_threads([in_investigation("A", lambda: seen.append(pool.submit(probe).result(5)))])
        seen.append(pool.submit(probe).result(5))  # 조사 밖 제출 = 컨텍스트 없음
        run_threads([in_investigation("B", lambda: seen.append(
            next(iter(pool.map(lambda _: probe(), [0])))))])
    assert seen == ["A", None, "B"]


def test_metrics_records_wait_even_on_slot_timeout(restore: None,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from holmes.core.llm import DefaultLLM
    from holmes.core.tool_calling_llm import ToolCallingLLM
    from sre_agent.infrastructure import investigation_metrics as im
    monkeypatch.setattr(DefaultLLM, "completion", DefaultLLM.completion)
    monkeypatch.setattr(ToolCallingLLM, "_invoke_llm_tool_call",
                        ToolCallingLLM._invoke_llm_tool_call)
    hs.install(scope_settings(sre_holmes_mcp_lock_scope="investigation",
                              sre_tool_global_concurrency=1,
                              evidence_prefetch_timeout_seconds=0.2))
    im.install()
    sem = mc.global_semaphore("polestar", 1)
    assert sem is not None and sem.acquire(blocking=False)
    out: dict[str, Any] = {}

    def investigation() -> None:
        metrics = im.start()
        out["res"] = holmes_call(FakeTool(PURL, Gauge()))
        out["ev"] = im.finish(metrics)

    try:
        run_threads([in_investigation("m1", investigation)])
    finally:
        sem.release()
    assert out["res"].status == StructuredToolResultStatus.ERROR
    assert out["ev"]["slot_wait_n"] == {im.HOLMES_MCP_AXIS: 1}
    assert out["ev"]["slot_wait_s"][im.HOLMES_MCP_AXIS] >= 0.15
