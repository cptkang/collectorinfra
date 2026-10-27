"""plans/187 G-26 — 잡 단위 도구 결과 캐시를 HolmesGPT 도구 경로와 공용(D-341 ⑲).

HolmesGPT 쪽은 실 holmes `ToolCallingLLM`(0.36.0)의 도구 실행 경로(`_invoke_llm_tool_call` →
`_directly_invoke_tool_call`)를 가짜 도구 실행기로 돌린다. 「실호출」은 가짜 도구의 `invoke` 횟수,
플레이북 쪽 실호출은 배치 콜러블 호출 횟수다. 실 LLM·MCP 호출 0.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.utils.stream import StreamEvents, StreamMessage
from litellm.types.utils import ChatCompletionMessageToolCall, Function
from sre_agent.diagnosis import DiagnosisResult, _StreamDrain, _with_tool_cache
from sre_agent.domain.activity_log import ActivityLimits, ActivityLog
from sre_agent.infrastructure.activity_masking import mask_activity_text
from sre_agent.infrastructure.tool_result_cache import ToolResultCache
from sre_agent.interface import mcp_service
from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import alarm_of, build, make_job, make_settings

from sre_agent import diagnosis

TOOL = "apm_resource_pool"
ARGS = {"agent": "was-01", "window_min": 10}


# ── 가짜 holmes 도구 실행기 ───────────────────────────────────────────────────


class FakeTool:
    """holmes 도구 대역 — `mcp_tool_name`이 있으면 MCP 도구다. 실호출을 센다."""

    def __init__(self, name: str, mcp_tool_name: str | None, *,
                 gate: threading.Event | None = None) -> None:
        self.name = name
        if mcp_tool_name is not None:
            self.mcp_tool_name = mcp_tool_name
        self.gate = gate
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def invoke(self, params: dict, context: Any = None) -> StructuredToolResult:
        with self._lock:
            self.calls.append(dict(params))
        if self.gate is not None:
            assert self.gate.wait(10)
        return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS,
                                    data=f'{{"holmes": "{self.name}"}}', params=params)

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{self.name} {json.dumps(params, sort_keys=True)}"


class FakeExecutor:
    def __init__(self, *tools: FakeTool) -> None:
        self.tools_by_name = {t.name: t for t in tools}

    def ensure_toolset_initialized(self, tool_name: str) -> None:
        return None

    def get_tool_by_name(self, name: str, user_id: str | None = None) -> FakeTool | None:
        return self.tools_by_name.get(name)

    def get_toolset_name(self, name: str, user_id: str | None = None) -> str:
        return "apm"


class FakeTCL(ToolCallingLLM):
    """도구 실행만 가짜로 — `tool.invoke` 뒤로는 holmes 원본 `_directly_invoke_tool_call` 대신
    같은 모양의 최소 구현을 쓴다(holmes 원본은 `ToolInvokeContext`가 실 LLM 타입을 요구한다)."""

    def _directly_invoke_tool_call(self, tool_name: str, tool_params: dict, user_approved: bool,
                                   tool_call_id: str, tool_number: int | None = None,
                                   session_approved_prefixes: list[str] | None = None,
                                   request_context: dict | None = None) -> StructuredToolResult:
        tool = self.tool_executor.get_tool_by_name(tool_name)
        assert tool is not None
        return tool.invoke(tool_params)


def make_llm(*tools: FakeTool) -> FakeTCL:
    llm_stub = SimpleNamespace(
        count_tokens=lambda messages: SimpleNamespace(total_tokens=1),
        get_max_token_count_for_single_tool=lambda: 10_000,
    )
    return FakeTCL(tool_executor=FakeExecutor(*tools), max_steps=3,  # type: ignore[arg-type]
                   llm=llm_stub, tool_results_dir=None)  # type: ignore[arg-type]


class CountingBatch:
    """플레이북 배치 콜러블 대역 — 실호출을 센다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, calls: Any) -> list[str]:
        with self._lock:
            self.calls.extend((t, dict(a)) for t, a in calls)
        return [json.dumps({"playbook": t}) for t, _ in calls]


def tool_call(call_id: str, name: str, args: dict) -> ChatCompletionMessageToolCall:
    return ChatCompletionMessageToolCall(
        id=call_id, type="function", function=Function(name=name, arguments=json.dumps(args)))


# ── 두 엔진 공용 · 실호출 1 ──────────────────────────────────────────────────


def test_playbook_first_then_holmes_same_args_is_one_real_call() -> None:
    # 노출 이름에 충돌 접두어가 붙어도 서버 쪽 이름(mcp_tool_name)으로 같은 키다.
    tool = FakeTool(f"apm__{TOOL}", TOOL)
    cache = ToolResultCache()
    batch = CountingBatch()
    pb_text = cache.wrap(batch).call(TOOL, {**ARGS, "unused": None})
    clone, invoke = _with_tool_cache(make_llm(tool), cache)
    res = clone._directly_invoke_tool_call(f"apm__{TOOL}", dict(reversed(list(ARGS.items()))),
                                           False, "c1")
    assert len(batch.calls) == 1 and tool.calls == []
    assert res.status == StructuredToolResultStatus.SUCCESS and res.data == pb_text
    assert cache.real_calls == 1 and cache.hits == 1
    assert invoke.is_hit("c1")


def test_holmes_first_then_playbook_gets_holmes_text() -> None:
    tool = FakeTool(TOOL, TOOL)
    cache = ToolResultCache()
    batch = CountingBatch()
    clone, invoke = _with_tool_cache(make_llm(tool), cache)
    res = clone._directly_invoke_tool_call(TOOL, dict(ARGS), False, "c1")
    assert cache.wrap(batch).cached(TOOL, ARGS)  # 플레이북 실행 기록이 `status=cached`로 표시
    pb_text = cache.wrap(batch).call(TOOL, dict(ARGS))
    assert len(tool.calls) == 1 and batch.calls == []
    assert pb_text == res.data and not invoke.is_hit("c1")


def test_error_result_shared_as_error_both_ways() -> None:
    cache = ToolResultCache()
    cache.wrap(lambda calls: [json.dumps({"error": "게이트웨이 401"})] * len(calls)).call(
        TOOL, ARGS)
    clone, _ = _with_tool_cache(make_llm(FakeTool(TOOL, TOOL)), cache)
    res = clone._directly_invoke_tool_call(TOOL, dict(ARGS), False, "c1")
    assert res.status == StructuredToolResultStatus.ERROR and res.error == "게이트웨이 401"


# ── holmes 스레드풀에서 호출돼도 적중 ─────────────────────────────────────────


def test_hits_inside_holmes_thread_pool_with_in_flight_wait() -> None:
    gate = threading.Event()
    tool = FakeTool(TOOL, TOOL, gate=gate)
    cache = ToolResultCache()
    clone, invoke = _with_tool_cache(make_llm(tool), cache)
    # holmes `call_stream`과 같은 방식 — 풀 스레드가 `self._invoke_llm_tool_call`을 부른다.
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [
            pool.submit(clone._invoke_llm_tool_call, tool_to_call=tool_call(f"c{i}", TOOL, ARGS),
                        previous_tool_calls=[], tool_number=i)
            for i in range(8)
        ]
        batch = CountingBatch()
        pb_future = pool.submit(cache.wrap(batch).call, TOOL, dict(ARGS))
        threading.Timer(0.2, gate.set).start()
        results = [f.result(timeout=10) for f in futures]
        pb_text = pb_future.result(timeout=10)
    assert len(tool.calls) == 1 and batch.calls == []  # 진행 중 호출을 기다려 공유
    assert {r.result.data for r in results} == {pb_text}
    assert sum(invoke.is_hit(f"c{i}") for i in range(8)) == 7
    assert cache.real_calls == 1 and cache.hits == 8


# ── 잡 간 누수 0 ─────────────────────────────────────────────────────────────


def test_no_leak_between_jobs_sharing_one_agent() -> None:
    tool = FakeTool(TOOL, TOOL)
    shared = make_llm(tool)  # DiagnosisAgent의 `ToolCallingLLM`은 잡 간 공유다
    cache_a, cache_b = ToolResultCache(), ToolResultCache()
    clone_a, _ = _with_tool_cache(shared, cache_a)
    clone_b, inv_b = _with_tool_cache(shared, cache_b)
    clone_a._directly_invoke_tool_call(TOOL, dict(ARGS), False, "a1")
    clone_b._directly_invoke_tool_call(TOOL, dict(ARGS), False, "b1")
    assert len(tool.calls) == 2 and not inv_b.is_hit("b1")
    assert len(cache_a) == 1 and len(cache_b) == 1
    # 공유 원본은 그대로(인스턴스 속성 오버라이드 없음) — 원본 경로는 캐시를 모른다.
    assert "_directly_invoke_tool_call" not in vars(shared)
    shared._directly_invoke_tool_call(TOOL, dict(ARGS), False, "s1")
    assert len(tool.calls) == 3 and cache_a.real_calls == 1 and cache_b.real_calls == 1


def test_closed_cache_returns_error_without_real_call() -> None:
    tool = FakeTool(TOOL, TOOL)
    cache = ToolResultCache()
    clone, _ = _with_tool_cache(make_llm(tool), cache)
    cache.close()  # 잡 종료 뒤 남은 심층 스레드
    res = clone._directly_invoke_tool_call(TOOL, dict(ARGS), False, "c1")
    assert res.status == StructuredToolResultStatus.ERROR and "닫혔다" in (res.error or "")
    assert tool.calls == []


def test_non_mcp_tool_and_user_approved_bypass_cache() -> None:
    bash = FakeTool("bash", None)
    mcp = FakeTool(TOOL, TOOL)
    cache = ToolResultCache()
    clone, _ = _with_tool_cache(make_llm(bash, mcp), cache)
    for i in range(2):
        clone._directly_invoke_tool_call("bash", {"command": "uptime"}, False, f"b{i}")
        clone._directly_invoke_tool_call(TOOL, dict(ARGS), True, f"m{i}")
    assert len(bash.calls) == 2 and len(mcp.calls) == 2 and len(cache) == 0


# ── 미주입 경로 비트 동일 ─────────────────────────────────────────────────────


def test_ask_without_cache_uses_shared_llm_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, Any]] = []

    class SpyDrain:
        def __init__(self, llm: Any, activity: Any = None, **kw: Any) -> None:
            seen.append((llm, kw))

        def run(self, messages: list[dict]) -> Any:
            return SimpleNamespace(result="ok", tool_calls=[], total_tokens=0, total_cost=0.0,
                                   num_llm_calls=1)

    monkeypatch.setattr(diagnosis, "_StreamDrain", SpyDrain)
    monkeypatch.setattr(diagnosis, "build_initial_ask_messages", lambda **kw: [])
    agent = diagnosis.DiagnosisAgent.__new__(diagnosis.DiagnosisAgent)
    agent.settings = SimpleNamespace(max_steps=3)  # type: ignore[assignment]
    shared = make_llm(FakeTool(TOOL, TOOL))
    agent._llm = shared
    agent.ask("q")
    cache = ToolResultCache()
    agent.ask("q", tool_cache=cache)
    (plain_llm, plain_kw), (cached_llm, cached_kw) = seen
    assert plain_llm is shared and plain_kw == {}  # 종전 호출 그대로
    assert cached_llm is not shared and isinstance(cached_llm, ToolCallingLLM)
    assert callable(cached_kw["cache_hit"])
    assert "_directly_invoke_tool_call" not in vars(shared)


def test_default_diagnose_fn_passes_tool_cache_only_when_job_carries_it() -> None:
    seen: list[dict] = []
    agent = SimpleNamespace(
        mcp_unavailable=lambda: {},
        tool_names=lambda: frozenset(),
        ask=lambda q, system_prompt_additions=None, **kw: (
            seen.append(kw) or DiagnosisResult(answer="ok")),
    )
    fn = mcp_service._default_diagnose_fn(make_settings(), agent_getter=lambda: agent)
    job = SimpleNamespace(investigation_id="i", kind="diagnosis", question="q", payload={},
                          reference_time=None, lookback_minutes=None, correlation=None)
    fn(job)
    cache = ToolResultCache()
    job.tool_cache = cache
    fn(job)
    assert seen == [{}, {"tool_cache": cache}]


# ── 실행 기록 `status=cached` ────────────────────────────────────────────────


class _ScriptedLLM:
    def __init__(self, events: list[StreamMessage]) -> None:
        self.events = events

    def call_stream(self, **kw: Any) -> Any:
        yield from self.events


def _script() -> list[StreamMessage]:
    def result(call_id: str) -> StreamMessage:
        return StreamMessage(event=StreamEvents.TOOL_RESULT, data={
            "tool_call_id": call_id, "tool_name": TOOL, "description": f"{TOOL} 호출",
            "result": {"status": "success", "data": "{}"}})

    return [
        StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": TOOL, "id": "c1"}),
        StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": TOOL, "id": "c2"}),
        result("c1"), result("c2"),
        StreamMessage(event=StreamEvents.ANSWER_END, data={
            "content": "결론", "messages": [], "metadata": {}, "num_llm_calls": 1,
            "costs": {}}),
    ]


def test_stream_drain_marks_cache_hits_as_cached() -> None:
    log = ActivityLog(masker=mask_activity_text, limits=ActivityLimits())
    _StreamDrain(_ScriptedLLM(_script()), activity=log,  # type: ignore[arg-type]
                 cache_hit=lambda cid: cid == "c2").run([])
    ends = [e for e in log.since(0)["events"] if e["kind"] == "tool_end"]
    assert [e["status"] for e in ends] == ["success", "cached"]
    plain = ActivityLog(masker=mask_activity_text, limits=ActivityLimits())
    _StreamDrain(_ScriptedLLM(_script()), activity=plain).run([])  # type: ignore[arg-type]
    assert [e["status"] for e in plain.since(0)["events"] if e["kind"] == "tool_end"] == [
        "success", "success"]


# ── dispatcher 배선 — parallel 잡에서 두 엔진이 한 캐시 ──────────────────────


def test_parallel_job_shares_one_cache_between_engines() -> None:
    probe = FakeCaller(_load("latency_sql"))
    run_probe = build(make_settings(), probe, llm=None)
    run_probe(make_job(alarm_of("latency_sql"), "probe"))
    run_probe.wait_workers(30)
    first_tool, first_args = probe.calls[0]  # 플레이북이 실제로 부르는 도구·인자

    tool = FakeTool(first_tool, first_tool)
    seen_jobs: list[Any] = []

    def deep(job: Any) -> DiagnosisResult:
        seen_jobs.append(job)
        clone, _ = _with_tool_cache(make_llm(tool), job.tool_cache)
        clone._directly_invoke_tool_call(first_tool, dict(first_args), False, "h1")
        return DiagnosisResult(answer="ok")

    audit: list = []
    caller = FakeCaller(_load("latency_sql"))
    d = build(make_settings(sre_deep_dive_mode="parallel"), caller, deep=deep, audit=audit)
    job = make_job(alarm_of("latency_sql"))
    d(job)
    d.wait_workers(30)
    assert job.status == "done"
    pb_real = sum(1 for t, a in caller.calls if t == first_tool and a == first_args)
    assert pb_real + len(tool.calls) == 1  # 두 엔진 합쳐 실호출 1
    done = next(r for r in audit if r["event"] == "done")
    assert done["cache_real_calls"] == len(caller.calls) + len(tool.calls)
    assert done["cache_hits"] >= 1
    assert seen_jobs[0].tool_cache.closed  # 잡 종료 시 폐기
    assert "tool_cache" not in vars(job)  # 원 잡에는 싣지 않는다(사본만)
