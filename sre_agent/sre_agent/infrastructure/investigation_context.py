"""조사 컨텍스트 전파 (plans/207 A1 · D-358).

조사 1건이 갈라진 스레드(타임박스 → HolmesGPT 도구 풀 · 플레이북·소견 풀 · 사전수집)에서도 「지금
어느 조사인가」를 알게 한다. 쓰는 곳은 HolmesGPT MCP 잠금 범위(`holmes_mcp_scope`) · 조사별 전역
자리 몫(`mcp_tool_client`) · APM owner 주입 셋이다.

**전파 — 스레드 상속**: `investigation_metrics`와 같은 기법이다(contextvars는 Python 3.13에서
`threading.Thread` 경계를 넘지 못한다 — 실측은 그 모듈 머리 참조). 워커 스레드 객체에 컨텍스트를
붙이고(`bind`), 훅이 설치되면 `threading.Thread.start`가 시작하는 스레드의 컨텍스트를 새 스레드에
물려준다. 계측 훅과 표지 이름이 달라 둘 다 설치돼도 서로 멱등을 깨지 않고, 계측 동작은 그대로다.

**풀 작업 단위 전파**: `Thread.start` 상속은 스레드가 생길 때 1회라, 조사 밖에서 만든 장수 풀의
스레드는 처음 제출한 조사의 컨텍스트를 계속 쥔다. 그래서 `ThreadPoolExecutor.submit`도 감싸
**제출한 스레드의 컨텍스트를 그 작업 동안만** 풀 스레드에 싣고 끝나면 되돌린다(제출 스레드가
조사 밖이면 작업 동안 컨텍스트 없음). 불변식: MCP를 부르는 작업은 `threading.Thread` 또는
`ThreadPoolExecutor.submit`(`map` 포함)으로만 갈라진다 — 그 밖의 실행기(다른 풀 구현 · 장수
스레드에 큐로 넘기기)는 컨텍스트를 잃어 조사 밖(종전 동작)으로 돈다.

**꺼져 있으면**: 신규 설정 셋이 모두 기본값이면 dispatcher가 `install_thread_inheritance`를 부르지
않고 `bind`도 하지 않는다 → `current()`가 항상 None → 잠금·자리·owner 모두 종전 그대로(비트 동일).

계층: infrastructure(표준 라이브러리 어댑터).
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

#: 스레드 객체에 붙는 조사 컨텍스트 속성 이름.
_ATTR = "_sre_investigation_context"
#: 설치한 래퍼 표지(멱등 — `investigation_metrics`의 표지와 이름이 다르다).
_HOOK_FLAG = "_sre_investigation_context_hook"
_INSTALL_LOCK = threading.Lock()

#: APM 게이트웨이 owner 머리말(plans/205 W3b 호출자 키 — 형식은 잠정 · Q-X1).
OWNER_PREFIX = "investigation:"


class ShareGate:
    """조사 1건 · 축 1개의 전역 자리 몫(동시에 쥘 수 있는 전역 자리 수 상한 · 스레드 안전)."""

    def __init__(self, size: int) -> None:
        self.size = max(1, int(size))
        self._held = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        """비차단 획득 — 몫이 남았으면 True. 두 경로(배치·HolmesGPT) 모두 이것을 같은 간격으로
        되풀이한다(차단 대기가 비차단 쪽을 앞지르지 않게)."""
        with self._lock:
            if self._held >= self.size:
                return False
            self._held += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._held = max(0, self._held - 1)

    @property
    def held(self) -> int:
        with self._lock:
            return self._held


class InvestigationContext:
    """조사 1건의 컨텍스트 — id · 조사별 MCP 잠금 · 축별 전역 자리 몫(스레드 안전)."""

    def __init__(self, investigation_id: str) -> None:
        self.investigation_id = str(investigation_id)
        self._lock = threading.Lock()
        self._server_locks: dict[str, threading.Lock] = {}
        self._shares: dict[str, ShareGate] = {}

    @property
    def owner(self) -> str:
        """APM 게이트웨이 owner 값(`investigation:<id>`)."""
        return OWNER_PREFIX + self.investigation_id

    def server_lock(self, key: str) -> threading.Lock:
        """이 조사 전용 MCP 서버 잠금(키 = holmes 잠금 문자열). 같은 키면 같은 객체."""
        with self._lock:
            found = self._server_locks.get(key)
            if found is None:
                found = self._server_locks[key] = threading.Lock()
            return found

    def share_gate(self, axis: str, size: int | None) -> ShareGate | None:
        """축의 조사별 자리 몫. `size`가 None이면 None(몫 없음 = 종전)."""
        if size is None:
            return None
        with self._lock:
            found = self._shares.get(axis)
            if found is None or found.size != max(1, int(size)):
                found = self._shares[axis] = ShareGate(size)
            return found


def current() -> InvestigationContext | None:
    """지금 스레드가 속한 조사 컨텍스트(없으면 None — 기능 off이거나 조사 밖 스레드)."""
    found = getattr(threading.current_thread(), _ATTR, None)
    return found if isinstance(found, InvestigationContext) else None


def bind(investigation_id: str) -> InvestigationContext:
    """워커 스레드에 조사 컨텍스트를 붙인다."""
    ctx = InvestigationContext(investigation_id)
    setattr(threading.current_thread(), _ATTR, ctx)
    return ctx


def unbind(ctx: InvestigationContext) -> None:
    """워커 스레드에서 컨텍스트를 뗀다(갈라진 스레드는 자기 참조를 그대로 쥔다)."""
    thread = threading.current_thread()
    if getattr(thread, _ATTR, None) is ctx:
        delattr(thread, _ATTR)


def _run_as(ctx: InvestigationContext | None, fn: Callable[..., Any]) -> Callable[..., Any]:
    """`fn`을 `ctx`(None = 조사 밖) 아래에서 돌리고 그 스레드의 원래 컨텍스트를 되돌린다."""

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        thread = threading.current_thread()
        prev = getattr(thread, _ATTR, None)
        if ctx is None:
            if prev is not None:
                delattr(thread, _ATTR)
        else:
            setattr(thread, _ATTR, ctx)
        try:
            return fn(*args, **kwargs)
        finally:
            if prev is None:
                if getattr(thread, _ATTR, None) is not None:
                    delattr(thread, _ATTR)
            else:
                setattr(thread, _ATTR, prev)

    return run


def _install_start() -> bool:
    original = threading.Thread.start
    if getattr(original, _HOOK_FLAG, False):
        return False

    @functools.wraps(original)
    def start_inheriting(self: threading.Thread) -> None:
        ctx = current()
        if ctx is not None and getattr(self, _ATTR, None) is None:
            setattr(self, _ATTR, ctx)
        original(self)

    setattr(start_inheriting, _HOOK_FLAG, True)
    threading.Thread.start = start_inheriting  # type: ignore[method-assign]
    return True


def _install_submit() -> bool:
    original: Any = ThreadPoolExecutor.submit
    if getattr(original, _HOOK_FLAG, False):
        return False

    @functools.wraps(original)
    def submit_with_context(self: ThreadPoolExecutor, fn: Callable[..., Any], /,
                            *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = original(self, _run_as(current(), fn), *args, **kwargs)
        return future

    setattr(submit_with_context, _HOOK_FLAG, True)
    setattr(ThreadPoolExecutor, "submit", submit_with_context)  # noqa: B010 — 클래스 메서드를 덮는다
    return True


def install_thread_inheritance() -> bool:
    """컨텍스트 전파 훅 — `Thread.start` 상속 + `ThreadPoolExecutor.submit` 작업 단위(멱등).

    이번 호출에서 하나라도 설치했으면 True.
    """
    with _INSTALL_LOCK:
        started = _install_start()
        submitted = _install_submit()
        return started or submitted


__all__ = [
    "InvestigationContext", "OWNER_PREFIX", "ShareGate", "bind", "current",
    "install_thread_inheritance", "unbind",
]
