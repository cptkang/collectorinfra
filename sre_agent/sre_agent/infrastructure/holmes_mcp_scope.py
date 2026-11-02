"""HolmesGPT MCP 호출의 조사 범위화 (plans/207 A1 · D-358 · 기본 off).

holmes 0.36.0 `toolset_mcp.RemoteMCPTool._invoke`는 같은 MCP URL 호출을 **프로세스 전역 잠금
하나**(`get_server_lock(url)`)로 직렬화한다(호출마다 SSE 새 연결 · 응답까지 잠금 보유). 동시 조사
K건의 HolmesGPT 도구 호출이 모두 이 잠금 앞에 줄을 서고, 이 경로는 전역 도구 상한
(`sre_tool_global_concurrency`) 밖이다(A0 측정 · plans/207 §8.6).

**공유 상태 확인(holmes 0.36.0 · mcp 1.x 소스)**: `_invoke_async`는 호출마다
`get_initialized_mcp_session`으로 새 `httpx.AsyncClient`(`create_mcp_http_client_factory`)·새 SSE
연결·새 `ClientSession`을 만들고 `asyncio.run`으로 그 스레드 안에서 끝낸다 — 세션·클라이언트
캐시가 없다. 헤더는 `_render_headers`가 호출마다 새 dict로 만든다(설정 읽기만). mcp 클라이언트
모듈(`client/sse.py`·`client/session.py`·`shared/session.py`)에 가변 전역 상태가 없다. 배치 경로
(`mcp_tool_client`)는 이미 같은 서버에 서로 다른 스레드에서 SSE 세션을 동시에 연다. 예외: stdio
설정은 호출마다 같은 로그 파일을 `"w"`로 다시 열어 동시 호출이 겹친다 — 그래서 범위화는
폴스타·APM MCP URL(SSE)에만 하고 그 밖의 URL은 종전 서버 잠금을 쓴다.

**`sre_holmes_mcp_lock_scope=investigation`**: `get_server_lock`을 감싸 조사 컨텍스트가 있는
스레드의 폴스타·APM URL 호출에는 (URL, 조사) 잠금을 준다 — 조사 안 직렬화는 종전과 같고 조사끼리는
병렬이다. 그 잠금은 들어갈 때 배치 경로와 **같은** 축 전역 세마포어(`global_semaphore`)를 (조사별
몫이 있으면 몫 먼저) 도구 1건 상한 안에서 배치와 같은 간격의 비차단 반복으로 얻는다(공정 —
차단 대기는 배치 쪽을 굶긴다) — 못 얻으면 예외로 holmes가 도구 오류
결과(`tool_concurrency_wait_timeout …`)를 만든다. 그래서 축별 동시 호출 합은 지금처럼 전역 상한으로
묶인다(상한 곱셈 없음). URL 판별은 holmes `MCPConfig.url`(pydantic `AnyUrl`)과 같은 정규화로
하고, 맞지 않는 URL은 URL별 1회 WARNING을 남긴다(침묵 폴백 금지).

**`sre_apm_owner_per_investigation_enabled`**: `RemoteMCPTool._invoke`를 감싸 APM URL 도구 중
입력 스키마에 `owner`가 있는 것(데이터 도구 · 작업 도구 `apm_job_*`)에 조사 owner를 싣는다(LLM이
준 값은 덮는다) — 작업 후속 호출도 같은 owner라 결과를 읽을 수 있다.

**계측과의 순서**: 계측(`investigation_metrics`)의 잠금 대기 래퍼가 이 잠금을 감싸야 조사 범위의
대기(조사 안 직렬화 + 전역 자리)가 `holmes_mcp` 대기로 잡힌다 — dispatcher는 이 모듈을 먼저
설치한다. 두 래퍼는 표지 이름이 달라 어느 순서로 설치해도 기능은 같다.

계층: infrastructure(외부 패키지 어댑터).
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from pydantic import AnyUrl

from sre_agent.infrastructure import investigation_context
from sre_agent.infrastructure.investigation_context import InvestigationContext, ShareGate
from sre_agent.infrastructure.mcp_tool_client import (
    _GLOBAL_POLL_S,
    TOOL_CONCURRENCY_WAIT_TIMEOUT,
    _apm_timeout_seconds,
    _redact_url,
    global_semaphore,
)
from sre_agent.settings import TOOL_CALL_TIMEOUT_S

logger = logging.getLogger(__name__)

#: 설치한 래퍼 표지(멱등 — 다른 래퍼가 `functools.wraps`로 감싸도 `__dict__`째 복사돼 남는다).
_HOOK_FLAG = "_sre_holmes_mcp_scope_hook"
_INSTALL_LOCK = threading.Lock()

SCOPE_SERVER = "server"
SCOPE_INVESTIGATION = "investigation"


class ToolSlotWaitTimeoutError(RuntimeError):
    """HolmesGPT 도구 호출이 전역 자리(또는 조사별 몫)를 도구 1건 상한 안에 못 얻었다."""


@dataclass(frozen=True)
class _Config:
    scope: str
    polestar_url: str
    apm_url: str
    global_limit: int | None
    share: int | None
    owner_enabled: bool
    polestar_timeout_s: float
    apm_timeout_s: float

    def axis_of(self, url: object) -> str | None:
        """MCP URL → 축(`polestar`·`apm`) · 둘 다 아니면 None."""
        key = _norm(url)
        if not key:
            return None
        if key == self.polestar_url:
            return "polestar"
        if key == self.apm_url:
            return "apm"
        return None

    def timeout_of(self, axis: str) -> float:
        return self.apm_timeout_s if axis == "apm" else self.polestar_timeout_s


_CONFIG: _Config | None = None
#: 폴스타·APM 어느 쪽과도 맞지 않아 경고한 holmes 잠금 문자열(URL별 1회 · 설치 때 비운다).
_UNMATCHED_WARNED: set[str] = set()


def _norm(url: object) -> str:
    """holmes `MCPConfig.url`(pydantic `AnyUrl`)과 같은 정규화 — 호스트 소문자 · 기본 포트 제거.

    holmes는 `get_lock_string()` = `str(AnyUrl)`을 넘기므로 설정 URL도 같은 규칙으로 맞춰야
    축 판별이 어긋나지 않는다(어긋나면 범위·owner가 꺼진다). URL이 아니면 공백만 걷는다.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        text = str(AnyUrl(text))
    except ValueError:  # pydantic ValidationError 포함
        pass
    return text.rstrip("/")


def _hook_axis(cfg: _Config, url: object) -> str | None:
    """훅이 쓰는 축 판별 — 맞지 않는 URL은 URL별 1회 WARNING(침묵 폴백 금지)."""
    axis = cfg.axis_of(url)
    key = _norm(url)
    if axis is None and key not in _UNMATCHED_WARNED:
        _UNMATCHED_WARNED.add(key)
        logger.warning(
            "HolmesGPT MCP URL %s가 POLESTAR_MCP_URL(%s)·APM_MCP_URL(%s) 어느 쪽과도 맞지 않는다 — "
            "이 URL 호출은 조사 범위 잠금·APM owner 없이 종전대로 돈다",
            _redact_url(key), _redact_url(cfg.polestar_url) or "-",
            _redact_url(cfg.apm_url) or "-")
    return axis


def context_needed(settings: Any) -> bool:
    """조사 컨텍스트 전파가 필요한가 — 신규 설정 셋 중 하나라도 기본값이 아니면 True."""
    return (getattr(settings, "sre_holmes_mcp_lock_scope", SCOPE_SERVER) != SCOPE_SERVER
            or getattr(settings, "sre_tool_per_investigation_share", None) is not None
            or bool(getattr(settings, "sre_apm_owner_per_investigation_enabled", False)))


def _config_of(settings: Any) -> _Config:
    return _Config(
        scope=str(getattr(settings, "sre_holmes_mcp_lock_scope", SCOPE_SERVER)),
        polestar_url=_norm(getattr(settings, "polestar_mcp_url", "")),
        apm_url=_norm(getattr(settings, "apm_mcp_url", "")),
        global_limit=getattr(settings, "sre_tool_global_concurrency", None),
        share=getattr(settings, "sre_tool_per_investigation_share", None),
        owner_enabled=bool(getattr(settings, "sre_apm_owner_per_investigation_enabled", False)),
        polestar_timeout_s=float(getattr(settings, "evidence_prefetch_timeout_seconds",
                                         TOOL_CALL_TIMEOUT_S)),
        apm_timeout_s=_apm_timeout_seconds(settings),
    )


# ── 잠금 범위 ─────────────────────────────────────────────────────────


def _acquire_slot(sem: threading.BoundedSemaphore, share: ShareGate | None,
                  timeout_seconds: float) -> bool:
    """몫(있으면) → 전역 자리를 같은 시한 안에서 얻는다. 못 얻으면 False(몫은 돌려준다).

    배치 경로(`mcp_tool_client._acquire_global`)와 **같은 간격의 비차단 획득 반복**이다. 차단
    대기(`acquire(timeout=…)`)는 반환 즉시 통지받아 간격마다 엿보는 배치 쪽을 늘 앞질러 굶긴다 —
    두 경로가 같은 방식으로 다퉈야 공정하다(plans/207 A1 verifier 지적).
    """
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    if share is not None:
        while not share.try_acquire():
            if time.monotonic() >= deadline:
                return False
            time.sleep(_GLOBAL_POLL_S)
    try:
        while not sem.acquire(blocking=False):
            if time.monotonic() >= deadline:
                if share is not None:
                    share.release()
                return False
            time.sleep(_GLOBAL_POLL_S)
    except BaseException:
        if share is not None:
            share.release()
        raise
    return True


class _ScopedLock:
    """(URL, 조사) 잠금 + 축 전역 자리(+ 조사별 몫). holmes `with lock:`에 그대로 쓰인다.

    `get_server_lock` 호출마다 새로 만든다 — 획득 상태는 이 객체 하나의 `with` 안에서만 쓴다.
    """

    def __init__(self, lock: threading.Lock, axis: str, sem: threading.BoundedSemaphore | None,
                 share: ShareGate | None, timeout_seconds: float) -> None:
        self._lock = lock
        self._axis = axis
        self._sem = sem
        self._share = share
        self._timeout = timeout_seconds
        self._slot = False

    def __enter__(self) -> bool:
        self._lock.acquire()
        if self._sem is None:
            return True
        try:
            got = _acquire_slot(self._sem, self._share, self._timeout)
        except BaseException:
            self._lock.release()
            raise
        if not got:
            self._lock.release()
            logger.warning("HolmesGPT 도구 전역 동시 상한 대기 초과 %s (%gs)",
                           self._axis, self._timeout)
            raise ToolSlotWaitTimeoutError(
                f"{TOOL_CONCURRENCY_WAIT_TIMEOUT} — {self._axis}: "
                f"전역 도구 동시 상한 대기 {self._timeout:g}s 초과")
        self._slot = True
        return True

    def __exit__(self, *exc: Any) -> None:
        if self._slot:
            self._slot = False
            assert self._sem is not None
            self._sem.release()
            if self._share is not None:
                self._share.release()
        self._lock.release()


def scoped_lock_for(ctx: InvestigationContext, url: str, cfg: _Config) -> _ScopedLock | None:
    """조사 범위 잠금 — 폴스타·APM URL이 아니면 None(종전 서버 잠금)."""
    axis = _hook_axis(cfg, url)
    if axis is None:
        return None
    sem = global_semaphore(axis, cfg.global_limit)
    share = ctx.share_gate(axis, cfg.share) if sem is not None else None
    return _ScopedLock(ctx.server_lock(_norm(url)), axis, sem, share, cfg.timeout_of(axis))


def _install_server_lock() -> bool:
    from holmes.plugins.toolsets.mcp import toolset_mcp

    original = getattr(toolset_mcp, "get_server_lock", None)
    if original is None:
        logger.warning("HolmesGPT MCP 잠금 범위 훅 미설치 — get_server_lock 부재(상위 버전 변경?)")
        return False
    if getattr(original, _HOOK_FLAG, False):
        return False

    @functools.wraps(original)
    def scoped_server_lock(url: str) -> Any:
        ctx = investigation_context.current()
        cfg = _CONFIG
        if ctx is not None and cfg is not None and cfg.scope == SCOPE_INVESTIGATION:
            scoped = scoped_lock_for(ctx, url, cfg)
            if scoped is not None:
                return scoped
        return original(url)

    setattr(scoped_server_lock, _HOOK_FLAG, True)
    toolset_mcp.get_server_lock = scoped_server_lock
    return True


# ── APM owner ─────────────────────────────────────────────────────────


def _tool_url(tool: Any) -> str:
    config = getattr(getattr(tool, "toolset", None), "_mcp_config", None)
    try:
        return str(config.get_lock_string()) if config is not None else ""
    except Exception:  # noqa: BLE001 — 설정 모양이 다르면 owner를 싣지 않는다
        return ""


def _install_owner() -> bool:
    from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPTool

    original = RemoteMCPTool.__dict__.get("_invoke")
    if original is None:
        logger.warning("APM owner 훅 미설치 — RemoteMCPTool._invoke 부재(상위 버전 변경?)")
        return False
    if getattr(original, _HOOK_FLAG, False):
        return False

    @functools.wraps(original)
    def invoke_with_owner(self: Any, params: dict[str, Any], context: Any) -> Any:
        ctx = investigation_context.current()
        cfg = _CONFIG
        if (ctx is not None and cfg is not None and cfg.owner_enabled
                and "owner" in (getattr(self, "parameters", None) or {})
                and _hook_axis(cfg, _tool_url(self)) == "apm"):
            params = {**(params or {}), "owner": ctx.owner}
        return original(self, params=params, context=context)

    setattr(invoke_with_owner, _HOOK_FLAG, True)
    RemoteMCPTool._invoke = invoke_with_owner
    return True


# ── 설치 ──────────────────────────────────────────────────────────────


def install(settings: Any) -> dict[str, bool]:
    """설정에 맞는 훅을 설치한다(멱등 · 설정은 마지막 호출 값). 이번 호출에서 설치한 것은 True.

    `context_needed`가 False면 아무것도 하지 않는다. holmes 부재·구조 변경은 경고만 남기고
    건너뛴다 — 그 경우 HolmesGPT 경로는 종전 그대로다.
    """
    global _CONFIG
    out = {"thread_inheritance": False, "holmes_mcp_lock_scope": False, "apm_owner": False}
    if not context_needed(settings):
        return out
    cfg = _config_of(settings)
    with _INSTALL_LOCK:
        _CONFIG = cfg
        _UNMATCHED_WARNED.clear()
        out["thread_inheritance"] = investigation_context.install_thread_inheritance()
        try:
            if cfg.scope == SCOPE_INVESTIGATION:
                out["holmes_mcp_lock_scope"] = _install_server_lock()
            if cfg.owner_enabled:
                out["apm_owner"] = _install_owner()
        except Exception as exc:  # noqa: BLE001 — holmes 부재/구조 변경은 무해하게 건너뛴다
            logger.warning("HolmesGPT MCP 범위 훅 미설치 — holmes 임포트 실패: %s", exc)
    if any(out.values()):
        logger.info("조사 컨텍스트 훅 설치: %s (scope=%s · share=%s · apm_owner=%s)",
                    out, cfg.scope, cfg.share, cfg.owner_enabled)
    return out


__all__ = [
    "SCOPE_INVESTIGATION", "SCOPE_SERVER", "ToolSlotWaitTimeoutError", "context_needed", "install",
    "scoped_lock_for",
]
