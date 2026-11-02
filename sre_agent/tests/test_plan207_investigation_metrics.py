"""plans/207 A0-a — 조사별 동시성 계측(`SRE_INVESTIGATION_METRICS_ENABLED`).

- off(기본): 훅을 설치하지 않고 감사에 계측 이벤트가 없다 · 조사 스레드에 계측 객체가 없다.
- on: 조사 1건당 감사 `investigation_metrics` 1건 — 워커가 만든 스레드(타임박스 → 풀)에서 부른
  LLM·HolmesGPT 도구·배치 도구·전역 자리 대기(HolmesGPT MCP 서버 잠금 대기 포함)가 그 조사에
  귀속된다. `status` 키가 없어 재기동 복구가
  활성 잡으로 오인하지 않는다.

실 LLM·MCP 호출 0 — 가짜 조사 함수 · 가짜 `DefaultLLM.completion`·도구 · 가짜 MCP 세션.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from holmes.core.llm import DefaultLLM
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.plugins.toolsets.mcp import toolset_mcp
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import InvestigationJob, JobStore
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.infrastructure import investigation_metrics as im
from sre_agent.infrastructure import mcp_tool_client as mc
from sre_agent.settings import AgentSettings

EVENT_KEYS = {
    "ts", "event", "investigation_id", "total_s", "queue_wait_s", "wall_start", "wall_end",
    "llm_calls", "llm_s_total", "llm_s_max", "slot_wait_s", "slot_wait_n", "slot_wait_s_total",
    "tool_calls", "tool_exec_s", "worker_thread_cpu_s", "threads_cpu_s", "threads_n",
    "process_cpu_start", "process_cpu_end",
}


def make_settings(**overrides: Any) -> AgentSettings:
    defaults: dict[str, Any] = {
        "model": "test/model", "api_key": None, "max_steps": 3, "gemini_api_key": "k",
        "service_bearer_token": None, "investigation_timeout_seconds": 30,
        "investigation_dedup_ttl_seconds": None, "investigation_max_concurrent": 2,
        "investigation_hourly_budget": None, "severity_judge_enabled": False,
        "sre_activity_log_enabled": False,
    }
    return AgentSettings(_env_file=None, **{**defaults, **overrides})


def make_job(server: str) -> InvestigationJob:
    now = time.time()
    return InvestigationJob(
        investigation_id=f"id-{server}", kind="alarm", status="running", created_at=now,
        updated_at=now, fingerprint=f"fp-{server}",
        payload={"event": {"serverName": server, "severity": 2}, "decision": {"tier": "PAGE"}})


def ok_result() -> DiagnosisResult:
    return DiagnosisResult(answer="원인 ← journalctl",
                           tool_outputs=[ToolCallRecord("journalctl", "로그", "success", "clean")])


def _events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _install_session(monkeypatch: pytest.MonkeyPatch, delay: float = 0.05) -> None:
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
            await asyncio.sleep(delay)
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"tool": name}))])

    @asynccontextmanager
    async def fake_sse(url: str, headers: Any = None, timeout: float = 5, **kw: Any):
        yield ("r", "w")

    import mcp
    import mcp.client.sse
    monkeypatch.setattr(mcp.client.sse, "sse_client", fake_sse)
    monkeypatch.setattr(mcp, "ClientSession", _Session)


@pytest.fixture
def hooks(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """훅 대상을 가짜로 바꾸고 테스트 뒤 원상 복구한다(설치는 전역 클래스 속성을 바꾼다)."""
    seen: dict[str, Any] = {"llm": 0, "tool": 0}

    def fake_completion(self: Any, messages: Any, *a: Any, **k: Any) -> str:
        time.sleep(0.02)
        seen["llm"] += 1
        return "ok"

    def fake_tool(self: Any, *a: Any, **k: Any) -> str:
        time.sleep(0.02)
        seen["tool"] += 1
        return "tool-ok"

    monkeypatch.setattr(threading.Thread, "start", threading.Thread.start)
    monkeypatch.setattr(DefaultLLM, "completion", fake_completion)
    monkeypatch.setattr(ToolCallingLLM, "_invoke_llm_tool_call", fake_tool)
    monkeypatch.setattr(toolset_mcp, "get_server_lock", toolset_mcp.get_server_lock)
    return seen


def test_flag_default_off() -> None:
    assert AgentSettings.model_fields["sre_investigation_metrics_enabled"].default is False
    assert make_settings().sre_investigation_metrics_enabled is False


def test_off_installs_nothing_and_writes_no_event(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(im, "install", lambda: calls.append(1))
    seen: list[Any] = []

    def diagnose(job: Any) -> DiagnosisResult:
        seen.append(im.current())
        return ok_result()

    audit = tmp_path / "a.jsonl"
    d = InvestigationDispatcher(make_settings(), diagnose_fn=diagnose, briefing_fn=build_briefing,
                                audit_path=audit)
    d(make_job("h1"))
    d.wait_workers(10)
    assert calls == [] and seen == [None]
    events = [e["event"] for e in _events(audit)]
    assert events and im.METRICS_EVENT not in events


def test_on_records_one_event_with_child_thread_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hooks: dict[str, Any],
) -> None:
    _install_session(monkeypatch)
    sem = mc.global_semaphore("polestar", 1)
    assert sem is not None

    def diagnose(job: Any) -> DiagnosisResult:
        # 타임박스 스레드(워커의 자식) — LLM 1회 · 풀(손자)에서 LLM 1회 + HolmesGPT 도구 1회 ·
        # 배치 도구 2건(전역 자리 1개를 나눠 쓴다 → 한 건은 기다린다).
        DefaultLLM.completion(None, [])  # type: ignore[arg-type]
        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(DefaultLLM.completion, None, []).result()  # type: ignore[arg-type]
            pool.submit(ToolCallingLLM._invoke_llm_tool_call, None, None, []).result()  # type: ignore[arg-type]
        mc.run_tool_batch("http://x/sse", None, [("a", {}), ("b", {})], concurrency=2,
                          global_limit=sem)
        return ok_result()

    audit = tmp_path / "a.jsonl"
    d = InvestigationDispatcher(make_settings(sre_investigation_metrics_enabled=True),
                                diagnose_fn=diagnose, briefing_fn=build_briefing,
                                audit_path=audit)
    d(make_job("h1"))
    d.wait_workers(10)
    (ev,) = [e for e in _events(audit) if e["event"] == im.METRICS_EVENT]
    assert set(ev) == EVENT_KEYS and "status" not in ev
    assert ev["investigation_id"] == "id-h1"
    assert ev["llm_calls"] == 2 and hooks["llm"] == 2
    assert ev["llm_s_total"] >= 0.04 and ev["llm_s_max"] >= 0.02
    assert ev["tool_calls"] == 3  # HolmesGPT 1 + 배치 2
    assert ev["slot_wait_n"] == {"polestar": 2} and ev["slot_wait_s"]["polestar"] >= 0.03
    assert ev["threads_n"] >= 2 and ev["queue_wait_s"] is not None
    assert ev["process_cpu_end"] >= ev["process_cpu_start"]
    assert ev["wall_end"] >= ev["wall_start"] and ev["total_s"] >= 0
    # 조사 밖 스레드는 계측 객체가 없다 — 훅은 그대로 통과한다.
    out: list[Any] = []
    t = threading.Thread(target=lambda: out.append((im.current(),
                                                    DefaultLLM.completion(None, []))))  # type: ignore[arg-type]
    t.start()
    t.join(5)
    assert out == [(None, "ok")]


def test_install_is_idempotent(hooks: dict[str, Any]) -> None:
    first = im.install()
    assert first == dict.fromkeys(("thread_inheritance", "llm", "holmes_tool",
                                   "holmes_mcp_lock"), True)
    assert not any(im.install().values())


def test_holmes_mcp_lock_wait_is_slot_wait_not_tool_time(
    monkeypatch: pytest.MonkeyPatch, hooks: dict[str, Any],
) -> None:
    """holmes는 같은 MCP URL 호출을 프로세스 전역 잠금으로 직렬화한다 — 그 대기는 자리 대기다."""

    def locked_tool(self: Any, *a: Any, **k: Any) -> str:
        with toolset_mcp.get_server_lock("http://mcp/sse"):
            time.sleep(0.15)
        return "ok"

    monkeypatch.setattr(ToolCallingLLM, "_invoke_llm_tool_call", locked_tool)
    im.install()
    out: dict[str, dict[str, Any]] = {}
    barrier = threading.Barrier(2)

    def investigation(name: str) -> None:
        metrics = im.start()
        barrier.wait()
        ToolCallingLLM._invoke_llm_tool_call(None)  # type: ignore[arg-type]
        out[name] = im.finish(metrics)

    threads = [threading.Thread(target=investigation, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    waits = sorted(out[n]["slot_wait_s"][im.HOLMES_MCP_AXIS] for n in ("a", "b"))
    assert waits[0] < 0.05 and waits[1] >= 0.12  # 한 건은 앞 건이 끝날 때까지 기다렸다
    assert all(out[n]["slot_wait_n"] == {im.HOLMES_MCP_AXIS: 1} for n in ("a", "b"))
    assert all(0.14 <= out[n]["tool_exec_s"] < 0.25 for n in ("a", "b"))  # 대기 제외
    with toolset_mcp.get_server_lock("http://mcp/sse"):  # 조사 밖 — 그대로 잠금으로 동작
        assert im.current() is None


def test_measure_helpers_are_noops_without_metrics() -> None:
    asked: list[int] = []
    with im.measure_slot_wait(lambda: asked.append(1) or "x"):  # type: ignore[func-returns-value]
        pass
    with im.measure_tool():
        pass
    assert asked == [] and im.current() is None


def test_recover_on_start_ignores_metrics_event(tmp_path: Path) -> None:
    """계측 이벤트가 잡의 마지막 감사 행이어도 재기동 복구가 활성 잡으로 보지 않는다."""
    audit = tmp_path / "a.jsonl"
    rows = [{"investigation_id": "x", "event": "running", "status": "running"},
            {"investigation_id": "x", "event": "terminal", "status": "done"},
            {"investigation_id": "x", "event": im.METRICS_EVENT, "total_s": 1.0}]
    audit.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    store = JobStore(make_settings(), audit_path=audit)
    assert store.recover_on_start() == 0
