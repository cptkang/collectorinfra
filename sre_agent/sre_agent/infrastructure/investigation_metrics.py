"""조사별 동시성 계측 (plans/207 A0-a · 옵트인 `SRE_INVESTIGATION_METRICS_ENABLED`).

조사 여러 건이 동시에 돌 때 무엇이 늘어나는지(LLM 뒤단 지연 · 전역 도구 자리 대기 · 프로세스 CPU)를
조사 1건 단위로 잰다. 결과는 dispatcher가 감사 `investigation_metrics` 이벤트 1건으로 남긴다.

**귀속 — 스레드 상속**: 조사 1건은 dispatcher 워커 스레드에서 시작해 타임박스 스레드(HolmesGPT) ·
HolmesGPT 도구 풀 · 플레이북 스텝 풀 · 소견 풀 · 배치마다 `asyncio.run`을 도는 스레드로 갈라진다.
contextvars는 `threading.Thread`·`ThreadPoolExecutor` 경계를 넘지 못한다(Python 3.13 실측 — 상속
플래그는 3.14부터). 그래서 워커 스레드 객체에 계측 객체를 붙이고(`start`), 훅이 설치되면
`threading.Thread.start`가 **시작하는 스레드의 계측 객체를 새 스레드에 물려준다**. 풀 스레드는 처음
제출한 스레드가 만들므로 조사마다 새로 만드는 풀(HolmesGPT 턴 풀 · 플레이북·소견 풀 — 실측)은 그
조사에 귀속된다. 이벤트 루프 스레드·서버 풀처럼 조사 밖에서 만든 스레드는 계측 객체가 없다(기록 0).

**기록 지점**: `DefaultLLM.completion`(LLM 호출 수·지연 — HolmesGPT 루프·소견 LLM 공통 관문) ·
`ToolCallingLLM._invoke_llm_tool_call`(HolmesGPT 도구 실행) · `mcp_tool_client`의 전역 자리 대기와
도구 실행(`measure_slot_wait`·`measure_tool` — 사전수집·플레이북 배치 경로) · HolmesGPT MCP 서버별
잠금 획득 대기(`toolset_mcp.get_server_lock` — holmes가 같은 MCP URL 호출을 프로세스 전역 잠금
하나로 직렬화한다(holmesgpt 0.36.0 실측). 대기는 축 `holmes_mcp` 자리 대기로 싣고 HolmesGPT 도구
실행 시간에서는 뺀다).

**꺼져 있으면**: 훅을 설치하지 않고(`install`을 부르지 않음) 워커에 계측 객체를 붙이지 않는다 →
`current()`가 항상 None → 기록·누적 0(감사·응답·로그 비트 동일).

버려진(타임아웃 뒤) 스레드가 워커 종료 뒤에 부르는 LLM·도구는 이벤트에 들지 않는다.

계층: infrastructure(외부 패키지·표준 라이브러리 어댑터). 플랫폼 공통(`time.thread_time`·
`time.process_time`만 쓴다 — Windows 포함).
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

#: 스레드 객체에 붙는 계측 객체 속성 이름.
_ATTR = "_sre_investigation_metrics"
#: 설치한 래퍼 표지(멱등 — 다른 가드가 `functools.wraps`로 감싸도 `__dict__`째 복사돼 남는다).
_HOOK_FLAG = "_sre_investigation_metrics_hook"
_INSTALL_LOCK = threading.Lock()

#: 감사 이벤트 이름.
METRICS_EVENT = "investigation_metrics"
#: HolmesGPT MCP 서버별 잠금 대기 축 이름.
HOLMES_MCP_AXIS = "holmes_mcp"

#: 스레드별 누적 잠금 대기(초) — 도구 실행 시간에서 그 호출 안의 잠금 대기를 빼는 데 쓴다.
_TLS = threading.local()


class InvestigationMetrics:
    """조사 1건의 누적 계측(스레드 안전). 시작은 워커 스레드에서 만든다."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._wall_clock = wall_clock
        self._started = clock()
        self.wall_start = wall_clock()
        self.process_cpu_start = time.process_time()
        self._thread_cpu_start = time.thread_time()
        self.queue_wait_s: float | None = None
        self.llm_calls = 0
        self.llm_s_total = 0.0
        self.llm_s_max = 0.0
        self.slot_wait_s: dict[str, float] = {}
        self.slot_wait_n: dict[str, int] = {}
        self.tool_calls = 0
        self.tool_exec_s = 0.0
        self.threads_cpu_s = 0.0
        self.threads_n = 0

    def mark_slot_acquired(self) -> None:
        """동시 조사 상한 슬롯을 얻은 시점 — 시작부터 여기까지가 슬롯 대기다."""
        self.queue_wait_s = round(self._clock() - self._started, 3)

    def add_llm(self, seconds: float) -> None:
        """LLM 호출 1건."""
        with self._lock:
            self.llm_calls += 1
            self.llm_s_total += seconds
            self.llm_s_max = max(self.llm_s_max, seconds)

    def add_slot_wait(self, axis: str, seconds: float) -> None:
        """전역 도구 동시 상한 자리 대기 1건(축별)."""
        with self._lock:
            self.slot_wait_s[axis] = self.slot_wait_s.get(axis, 0.0) + seconds
            self.slot_wait_n[axis] = self.slot_wait_n.get(axis, 0) + 1

    def add_tool(self, seconds: float) -> None:
        """도구 실행 1건(자리 대기 제외)."""
        with self._lock:
            self.tool_calls += 1
            self.tool_exec_s += seconds

    def add_thread_cpu(self, seconds: float) -> None:
        """이 조사에서 갈라진 스레드 하나가 끝났다 — 그 스레드 CPU 초."""
        with self._lock:
            self.threads_cpu_s += seconds
            self.threads_n += 1

    def finish(self) -> dict[str, Any]:
        """워커 스레드에서 부른다 — 감사 이벤트 키(값은 초 · 소수 3자리)."""
        worker_cpu = time.thread_time() - self._thread_cpu_start
        with self._lock:
            return {
                "total_s": round(self._clock() - self._started, 3),
                "queue_wait_s": self.queue_wait_s,
                "wall_start": self.wall_start,
                "wall_end": self._wall_clock(),
                "llm_calls": self.llm_calls,
                "llm_s_total": round(self.llm_s_total, 3),
                "llm_s_max": round(self.llm_s_max, 3),
                "slot_wait_s": {k: round(v, 3) for k, v in sorted(self.slot_wait_s.items())},
                "slot_wait_n": dict(sorted(self.slot_wait_n.items())),
                "slot_wait_s_total": round(sum(self.slot_wait_s.values()), 3),
                "tool_calls": self.tool_calls,
                "tool_exec_s": round(self.tool_exec_s, 3),
                "worker_thread_cpu_s": round(worker_cpu, 3),
                "threads_cpu_s": round(self.threads_cpu_s, 3),
                "threads_n": self.threads_n,
                "process_cpu_start": round(self.process_cpu_start, 3),
                "process_cpu_end": round(time.process_time(), 3),
            }


# ── 귀속 ──────────────────────────────────────────────────────────────


def current() -> InvestigationMetrics | None:
    """지금 스레드가 속한 조사의 계측 객체(없으면 None — 계측 off이거나 조사 밖 스레드)."""
    found = getattr(threading.current_thread(), _ATTR, None)
    return found if isinstance(found, InvestigationMetrics) else None


def start() -> InvestigationMetrics:
    """워커 스레드에서 조사 계측을 시작하고 이 스레드에 붙인다."""
    metrics = InvestigationMetrics()
    setattr(threading.current_thread(), _ATTR, metrics)
    return metrics


def finish(metrics: InvestigationMetrics) -> dict[str, Any]:
    """워커 스레드에서 계측을 닫는다 → 감사 이벤트 키. 스레드에서 계측 객체를 뗀다."""
    record = metrics.finish()
    thread = threading.current_thread()
    if getattr(thread, _ATTR, None) is metrics:
        delattr(thread, _ATTR)
    return record


@contextmanager
def measure_slot_wait(axis: Callable[[], str]) -> Iterator[None]:
    """전역 자리 대기 구간 — 계측 객체가 없으면 아무것도 하지 않는다(축 이름도 묻지 않는다)."""
    metrics = current()
    if metrics is None:
        yield
        return
    t0 = time.monotonic()
    try:
        yield
    finally:
        metrics.add_slot_wait(axis(), time.monotonic() - t0)


@contextmanager
def measure_tool() -> Iterator[None]:
    """도구 실행 구간 — 계측 객체가 없으면 아무것도 하지 않는다."""
    metrics = current()
    if metrics is None:
        yield
        return
    t0 = time.monotonic()
    try:
        yield
    finally:
        metrics.add_tool(time.monotonic() - t0)


# ── 훅 설치(계측 on일 때만 · 멱등 · 예외 없음) ─────────────────────────


def _count_thread_cpu(thread: threading.Thread, metrics: InvestigationMetrics) -> None:
    run = thread.run

    def counted() -> None:
        t0 = time.thread_time()
        try:
            run()
        finally:
            metrics.add_thread_cpu(time.thread_time() - t0)

    setattr(thread, "run", counted)  # noqa: B010 — 인스턴스 속성으로 run을 덮는다


def _install_thread_inheritance() -> bool:
    original = threading.Thread.start
    if getattr(original, _HOOK_FLAG, False):
        return False

    @functools.wraps(original)
    def start_inheriting(self: threading.Thread) -> None:
        metrics = current()
        if metrics is not None and getattr(self, _ATTR, None) is None:
            setattr(self, _ATTR, metrics)
            _count_thread_cpu(self, metrics)
        original(self)

    setattr(start_inheriting, _HOOK_FLAG, True)
    threading.Thread.start = start_inheriting  # type: ignore[method-assign]
    return True


class _TimedLock:
    """holmes MCP 서버 잠금 대리 — 계측 객체가 있으면 획득 대기를 `holmes_mcp` 자리 대기로 잰다."""

    def __init__(self, lock: Any) -> None:
        self._lock = lock

    def __enter__(self) -> Any:
        metrics = current()
        if metrics is None:
            return self._lock.__enter__()
        t0 = time.monotonic()
        try:
            return self._lock.__enter__()
        finally:
            # 획득 실패(조사 범위 잠금의 자리 대기 초과 예외 — plans/207 A1)도 기다린 시간은 대기다.
            waited = time.monotonic() - t0
            metrics.add_slot_wait(HOLMES_MCP_AXIS, waited)
            _TLS.lock_wait = getattr(_TLS, "lock_wait", 0.0) + waited

    def __exit__(self, *exc: Any) -> Any:
        return self._lock.__exit__(*exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._lock, name)


def _install_holmes_lock() -> bool:
    try:
        from holmes.plugins.toolsets.mcp import toolset_mcp
    except Exception as exc:  # noqa: BLE001 — holmes 부재/구조 변경은 무해하게 건너뛴다
        logger.warning("조사 계측 MCP 잠금 훅 미설치 — 임포트 실패: %s", exc)
        return False
    original = getattr(toolset_mcp, "get_server_lock", None)
    if original is None:
        logger.warning("조사 계측 MCP 잠금 훅 미설치 — get_server_lock 부재(상위 버전 변경?)")
        return False
    if getattr(original, _HOOK_FLAG, False):
        return False

    @functools.wraps(original)
    def timed_lock(url: str) -> Any:
        return _TimedLock(original(url))

    setattr(timed_lock, _HOOK_FLAG, True)
    toolset_mcp.get_server_lock = timed_lock
    return True


def _timed_method(owner: Any, name: str, record: Callable[[InvestigationMetrics, float], None],
                  what: str) -> bool:
    original = getattr(owner, name, None)
    if original is None:
        logger.warning("조사 계측 훅 미설치 — %s 부재(상위 버전 변경?)", what)
        return False
    if getattr(original, _HOOK_FLAG, False):
        return False

    @functools.wraps(original)
    def timed(self: Any, *args: Any, **kwargs: Any) -> Any:
        metrics = current()
        if metrics is None:
            return original(self, *args, **kwargs)
        t0 = time.monotonic()
        waited0 = getattr(_TLS, "lock_wait", 0.0)
        try:
            return original(self, *args, **kwargs)
        finally:
            waited = getattr(_TLS, "lock_wait", 0.0) - waited0
            record(metrics, time.monotonic() - t0 - waited)

    setattr(timed, _HOOK_FLAG, True)
    setattr(owner, name, timed)
    return True


def install() -> dict[str, bool]:
    """계측 훅 4종을 설치한다(멱등). 이번 호출에서 설치한 것은 True.

    holmes 부재·구조 변경은 경고만 남기고 건너뛴다 — 계측 실패가 조사를 막지 않는다.
    """
    out = {"thread_inheritance": False, "llm": False, "holmes_tool": False,
           "holmes_mcp_lock": False}
    with _INSTALL_LOCK:
        out["thread_inheritance"] = _install_thread_inheritance()
        try:
            from holmes.core.llm import DefaultLLM
            from holmes.core.tool_calling_llm import ToolCallingLLM
        except Exception as exc:  # noqa: BLE001 — holmes 부재/구조 변경은 무해하게 건너뛴다
            logger.warning("조사 계측 LLM·도구 훅 미설치 — holmes 임포트 실패: %s", exc)
            return out
        out["llm"] = _timed_method(DefaultLLM, "completion", InvestigationMetrics.add_llm,
                                   "DefaultLLM.completion")
        out["holmes_tool"] = _timed_method(
            ToolCallingLLM, "_invoke_llm_tool_call", InvestigationMetrics.add_tool,
            "ToolCallingLLM._invoke_llm_tool_call")
        out["holmes_mcp_lock"] = _install_holmes_lock()
    if any(out.values()):
        logger.info("조사 동시성 계측 훅 설치: %s", out)
    return out


__all__ = [
    "HOLMES_MCP_AXIS", "InvestigationMetrics", "METRICS_EVENT", "current", "start", "finish",
    "install", "measure_slot_wait", "measure_tool",
]
