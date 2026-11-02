"""plans/207 A1 독립 검증(verifier) — 기본값 비트 동일 · 상한 곱셈 없음 · 누수 · URL 판별 · 공정성.

`xfail(strict=True)`는 **결함 재현**이다 — 고치면 XPASS로 실패하므로 그때 표지를 지운다.
실 LLM·MCP 호출 0.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from holmes.core.llm import DefaultLLM
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.mcp import toolset_mcp
from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig, RemoteMCPTool
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.infrastructure import holmes_mcp_scope as hs
from sre_agent.infrastructure import investigation_context as ic
from sre_agent.infrastructure import mcp_tool_client as mc
from tests.test_plan207_a1_tool_scope import (
    PURL,
    FakeTool,
    Gauge,
    holmes_call,
    in_investigation,
    run_threads,
    scope_settings,
)
from tests.test_plan207_investigation_metrics import make_job, make_settings, ok_result


@pytest.fixture
def restore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(threading.Thread, "start", threading.Thread.start)
    monkeypatch.setattr(toolset_mcp, "get_server_lock", toolset_mcp.get_server_lock)
    monkeypatch.setattr(RemoteMCPTool, "_invoke", RemoteMCPTool.__dict__["_invoke"])
    monkeypatch.setattr(DefaultLLM, "completion", DefaultLLM.completion)
    monkeypatch.setattr(ToolCallingLLM, "_invoke_llm_tool_call",
                        ToolCallingLLM._invoke_llm_tool_call)
    monkeypatch.setattr(hs, "_CONFIG", None)
    monkeypatch.setattr(mc, "_GLOBAL_LIMITS", {})


def _targets() -> tuple[Any, ...]:
    return (threading.Thread.start, toolset_mcp.get_server_lock,
            RemoteMCPTool.__dict__["_invoke"], DefaultLLM.__dict__["completion"],
            ToolCallingLLM.__dict__["_invoke_llm_tool_call"])


# ── 수용 기준 1: 기본값 = 훅 5종 모두 미설치 ─────────────────────────────


def test_default_settings_touch_no_hook_target(restore: None, tmp_path: Path) -> None:
    before = _targets()
    d = InvestigationDispatcher(make_settings(polestar_mcp_url=PURL),
                                diagnose_fn=lambda j: ok_result(),
                                briefing_fn=build_briefing, audit_path=tmp_path / "a.jsonl")
    d(make_job("h1"))
    d.wait_workers(10)
    assert _targets() == before
    assert hs._CONFIG is None
    events = (tmp_path / "a.jsonl").read_text(encoding="utf-8")
    assert "investigation_metrics" not in events


# ── 수용 기준 2: 혼합 부하(성공·예외·자리 시간 초과)에서도 합 ≤ 상한 · 누수 0 ─────


def test_mixed_load_never_exceeds_global_and_restores_all_slots(restore: None) -> None:
    s = scope_settings(sre_holmes_mcp_lock_scope="investigation", sre_tool_global_concurrency=2,
                       sre_tool_per_investigation_share=1, evidence_prefetch_timeout_seconds=0.3)
    hs.install(s)
    gauge = Gauge()

    class Boom(FakeTool):
        async def _invoke_async(self, params: dict, request_context: Any) -> Any:
            self._gauge.enter(self.name, params)
            try:
                await asyncio.sleep(0.05)
                raise RuntimeError("tool exploded")
            finally:
                self._gauge.leave()

    results: list[Any] = []

    def work(n: int) -> None:
        tool = Boom(PURL, gauge) if n % 2 else FakeTool(PURL, gauge, delay=0.1)
        results.append(holmes_call(tool))

    run_threads([in_investigation(f"m{n}", lambda n=n: work(n)) for n in range(8)])
    assert gauge.peak <= 2
    assert len(results) == 8
    assert any(r.status == StructuredToolResultStatus.ERROR for r in results)
    sem = mc.global_semaphore("polestar", 2)
    assert sem is not None and sem.acquire(blocking=False) and sem.acquire(blocking=False)


def test_async_acquire_cancelled_during_global_wait_releases_share() -> None:
    sem = threading.BoundedSemaphore(1)
    assert sem.acquire(blocking=False)  # 전역 자리 소진
    gate = ic.ShareGate(1)

    async def main() -> None:
        task = asyncio.create_task(mc._acquire_global(sem, 5.0, gate))
        await asyncio.sleep(0.12)
        assert gate.held == 1  # 몫은 얻고 전역 대기 중
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert gate.held == 0
    sem.release()


# ── 결함 재현: URL 판별이 holmes 정규화(AnyUrl)와 어긋나면 기능이 조용히 꺼진다 ─


_URL_REASON = (
    "_norm은 대소문자·기본 포트를 정규화하지 않는다 — holmes get_lock_string(AnyUrl)과 "
    "어긋나면 axis_of=None → 서버 잠금·owner 미주입(침묵)"
)
_POOL_REASON = (
    "상속은 Thread.start 시점 1회 — 공유(장수) 풀 스레드는 처음 만든 조사의 컨텍스트를 "
    "계속 쥔다(현 런타임 MCP 경로에는 공유 풀 없음 · 잠재)"
)


@pytest.mark.parametrize("configured",
                         ["http://Polestar-MCP:9099/sse", "http://polestar-mcp:80/sse"])
def test_axis_matches_holmes_normalized_url(restore: None, configured: str) -> None:
    hs.install(scope_settings(polestar_mcp_url=configured,
                              sre_holmes_mcp_lock_scope="investigation"))
    lock_string = MCPConfig(mode="sse", url=configured).get_lock_string()  # holmes가 넘기는 값
    assert hs._CONFIG is not None
    assert hs._CONFIG.axis_of(lock_string) == "polestar"


# 배치·HolmesGPT 자리 공정성 재현은 `test_plan207_a1_tool_scope.py`
# `test_holmes_waiters_do_not_starve_batch_poller`가 대신한다 — 이 파일의 원 재현은
# 자리 1개를 쥔 채 재시도해 자기와 다투는 구조라 어떤 수정으로도 통과할 수 없었다.


# ── 결함 재현(잠재): 조사 밖에서 만든 공유 풀은 처음 제출한 조사에 영구 귀속된다 ──


def test_shared_pool_thread_not_attributed_to_first_investigation(restore: None) -> None:
    hs.install(scope_settings(sre_holmes_mcp_lock_scope="investigation"))
    pool = ThreadPoolExecutor(max_workers=1)  # 조사 밖에서 만든 공유 풀(스레드는 첫 제출 때 생김)
    seen: list[str | None] = []

    def probe() -> str | None:
        ctx = ic.current()
        return ctx.investigation_id if ctx else None

    try:
        run_threads([in_investigation("A", lambda: seen.append(pool.submit(probe).result(5)))])
        run_threads([in_investigation("B", lambda: seen.append(pool.submit(probe).result(5)))])
    finally:
        pool.shutdown(wait=True)
    assert seen == ["A", "B"]
