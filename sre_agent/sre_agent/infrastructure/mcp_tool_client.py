"""mcp_server 도구 배치 호출 (plans/50 B′ · SPEC-evidence-correlation · D-197).

결정적 사전수집이 조사 LLM과 무관하게 도구를 부르기 위한 최소 SSE 클라이언트다. holmes의
`Tool.invoke`는 `ToolInvokeContext(llm=…)`를 요구해(실측 0.36.0) LLM 객체 없이는 쓸 수 없으므로
`mcp` 클라이언트를 직접 쓴다(`pyproject.toml`에 `mcp<2` 명시 — D-181 상한 동일).

한 배치는 **SSE 세션 1개**를 열어 `concurrency` 상한으로 동시 호출한다(plans/187 W21 P1 — 1이면
종전 순차와 동일). mcp `BaseSession.send_request`가 요청 id별 응답 스트림으로 응답을 매핑하므로 한
세션 안의 동시 요청이 안전하다(실측 1.25.0). 응답은 calls 순서를 유지한다. 호출 단위로 실패를 격리해
부분 반환을 보장하고(Known Mistakes: 개별 try), 세션 자체가 실패하면 전 호출이 `{error}`다. 동기
래퍼는 dispatcher 워커 스레드(실행 루프 없음)에서 `asyncio.run`으로 돈다.

전역 동시 상한(plans/194 W3 · D-347 · `sre_tool_global_concurrency`): 배치 안 상한 위에 축(폴스타
MCP · APM)별 **프로세스 전역** 세마포어를 건다 — 배치마다 스레드·이벤트 루프가 달라 asyncio
세마포어로는 배치 사이를 묶지 못하므로 `threading.BoundedSemaphore`다. 루프를 막지 않게 비차단
획득을 짧게 되풀이하고, 도구 1건 상한 안에 자리를 못 얻으면 그 호출만 결손
(`tool_concurrency_wait_timeout`)이다.

호출 점검 로그(2026-11-02 · D-347 부기): 배치마다 세션 연결·초기화 시간 1줄, 호출마다
「축 · 도구 · 전역 자리 대기 · 호출 시간 · 결과(ok/error/timeout/wait_timeout)」 1줄을 INFO로
남긴다 — 지연이 sre_agent 대기열(자리 대기)인지 조회 대상(호출 시간)인지 가르는 근거다.
인자·출력은 싣지 않는다.
세션 초기화(`initialize`)에도 도구 1건 상한을 건다 — 종전에는 상한이 없어 조회 대상의 이벤트 루프가
막히면 스텝 상한까지 「요청 N건 중 0건 완료」로만 보였다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sre_agent.settings import TOOL_CALL_TIMEOUT_S

logger = logging.getLogger(__name__)

#: (도구명, 인자) → 도구 반환 JSON 문자열. 응용 계층은 이 콜러블만 안다.
ToolCaller = Callable[[str, dict], str]


#: 전역 동시 상한 자리를 도구 1건 상한 안에 못 얻은 호출의 결손 사유(plans/194 W3).
TOOL_CONCURRENCY_WAIT_TIMEOUT = "tool_concurrency_wait_timeout"
#: 전역 세마포어 비차단 획득 재시도 간격(초).
_GLOBAL_POLL_S = 0.05

#: 축 → 프로세스 전역 세마포어(크기는 처음 만든 설정값 — 크기가 바뀌면 새로 만든다).
_GLOBAL_LIMITS: dict[str, tuple[int, threading.BoundedSemaphore]] = {}
_GLOBAL_LOCK = threading.Lock()


def _err(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def global_semaphore(axis: str, limit: int | None) -> threading.BoundedSemaphore | None:
    """축(`polestar`·`apm`)의 프로세스 전역 도구 동시 상한 세마포어. `limit`이 None이면 None.

    같은 축·같은 크기면 같은 객체를 돌려준다. 크기가 바뀌면 새로 만든다 — 이미 만든 호출자는
    자기가 받은 객체로 획득·반환하므로 반환이 엇갈리지 않는다.
    """
    if limit is None:
        return None
    size = max(1, int(limit))
    with _GLOBAL_LOCK:
        held = _GLOBAL_LIMITS.get(axis)
        if held is None or held[0] != size:
            held = (size, threading.BoundedSemaphore(size))
            _GLOBAL_LIMITS[axis] = held
        return held[1]


async def _acquire_global(sem: threading.BoundedSemaphore, timeout_seconds: float) -> bool:
    """전역 세마포어를 루프를 막지 않고 얻는다(비차단 획득 + 짧은 대기 반복).

    시간 안에 못 얻으면 False.

    블로킹 획득은 같은 루프의 다른 호출(자리를 쥔 쪽 포함)의 응답 처리를 막아 교착을 만들 수 있다.
    대기 중 취소되면 자리를 쥐지 않은 채 끝난다(누수 0).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout_seconds)
    while not sem.acquire(blocking=False):
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(_GLOBAL_POLL_S)
    return True


def _log_call(axis: str, name: str, waited: float, took: float, outcome: str) -> None:
    """도구 호출 1건 점검 로그 — 전역 자리 대기와 호출 시간을 갈라 남긴다(인자·출력 미기재)."""
    logger.info("도구 호출 [%s] %s: 자리 대기 %.1fs · 호출 %.1fs · 결과=%s",
                axis, name, waited, took, outcome)


def _is_error(result: Any, text: str) -> bool:
    """MCP 오류 응답(`isError`)이거나 본문이 `{"error": …}` 봉투면 True."""
    if getattr(result, "isError", False):
        return True
    head = text.lstrip()[:200]
    if not head.startswith("{"):
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and "error" in data


def _is_timeout(exc: BaseException) -> bool:
    """호출 예외가 시간 초과인지(mcp 읽기 상한 `McpError` 문구 포함)."""
    if isinstance(exc, TimeoutError):
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text


def _text_of(result) -> str:  # noqa: ANN001 — mcp.types.CallToolResult
    """CallToolResult의 첫 텍스트 콘텐츠를 돌려준다(없으면 빈 JSON 오류)."""
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            return text
    return _err("도구 응답에 텍스트 콘텐츠 없음")


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """예외 그룹을 끝 예외 목록으로 편다(그룹이 아니면 자기 자신)."""
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaf_exceptions(sub)]
    return [exc]


def _redact_url(url: str) -> str:
    """URL의 userinfo(user:pass@)를 가린다 — 진단 문구·로그에 자격증명이 실리지 않게."""
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc="***@" + parts.netloc.rsplit("@", 1)[1]))


def _failure_detail(exc: BaseException, url: str) -> str:
    """세션 실패 사유 — MCP SSE가 올리는 예외 그룹을 끝 예외로 풀어 싣는다(plans/188 C4).

    `noise_gate/infrastructure/apm_gateway_client.py`의 `_failure_detail` 사본이다(D-139 — 패키지
    간 import 금지). 연결 실패는 SDK가 `unhandled errors in a TaskGroup (1 sub-exception)`만
    남겨 원인을 알 수 없었다. 그룹이 아니고 HTTP 상태·연결 오류도 아닌 예외는 종전 문구 그대로다.
    토큰 값은 싣지 않고, URL은 userinfo를 가려 싣는다.
    """
    import httpx

    leaves = _leaf_exceptions(exc)
    for leaf in leaves:
        status = getattr(getattr(leaf, "response", None), "status_code", None)
        if isinstance(status, int):
            if status in (401, 403):
                return (
                    f"HTTP {status} 인증 실패 — POLESTAR_MCP_TOKEN이 mcp_server의 "
                    "MCP_BEARER_TOKEN과 같은지 확인"
                )
            return f"HTTP {status}"
    for leaf in leaves:
        if isinstance(leaf, httpx.ConnectError):
            return f"연결 불가(ConnectError: {leaf}) — POLESTAR_MCP_URL={_redact_url(url)}"
    if leaves == [exc]:
        return str(exc)
    return "; ".join(f"{type(leaf).__name__}: {leaf}" for leaf in leaves[:3])


async def run_tool_batch_async(
    url: str,
    headers: dict[str, str] | None,
    calls: Sequence[tuple[str, dict]],
    *,
    timeout_seconds: float = TOOL_CALL_TIMEOUT_S,
    concurrency: int = 1,
    global_limit: threading.BoundedSemaphore | None = None,
    axis: str = "",
) -> list[str]:
    """SSE 세션 1개로 calls를 호출한다. 호출별 실패는 `{error}`로 격리한다.

    `concurrency`(최소 1)개까지 동시에 `call_tool`을 보내고, 응답은 calls 순서로 돌려준다.
    1이면 종전처럼 calls 순서대로 하나씩 호출한다. `global_limit`(plans/194 W3)이 있으면 호출마다
    그 전역 자리를 `timeout_seconds` 안에 얻어야 보낸다 — 못 얻으면 그 호출만
    `{error: tool_concurrency_wait_timeout …}`이다. None이면 종전 그대로.
    `axis`(`polestar`·`apm`)는 점검 로그의 축 이름이다(비면 `mcp`).
    """
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    limit = max(1, int(concurrency))
    tag = axis or "mcp"
    try:
        started = time.monotonic()
        async with sse_client(url, headers=headers, timeout=timeout_seconds) as (read, write):
            async with ClientSession(read, write) as session:
                try:
                    await asyncio.wait_for(session.initialize(), timeout_seconds)
                except TimeoutError:
                    raise TimeoutError(
                        f"MCP 세션 초기화 {timeout_seconds:g}s 초과(조회 대상 응답 없음)") from None
                logger.info("도구 배치 [%s] 세션 연결 %.1fs · 호출 %d건 · 동시 %d",
                            tag, time.monotonic() - started, len(calls), limit)

                async def _one(name: str, args: dict[str, Any]) -> str:
                    queued = time.monotonic()
                    if global_limit is None:
                        return await _call(name, args, queued)
                    if not await _acquire_global(global_limit, timeout_seconds):
                        logger.warning("도구 전역 동시 상한 대기 초과 %s (%gs)",
                                       name, timeout_seconds)
                        _log_call(tag, name, time.monotonic() - queued, 0.0, "wait_timeout")
                        return _err(f"{TOOL_CONCURRENCY_WAIT_TIMEOUT} — {name}: "
                                    f"전역 도구 동시 상한 대기 {timeout_seconds:g}s 초과")
                    try:
                        return await _call(name, args, queued)
                    finally:
                        global_limit.release()

                async def _call(name: str, args: dict[str, Any], queued: float) -> str:
                    sent = time.monotonic()
                    try:
                        res = await session.call_tool(
                            name, args, read_timeout_seconds=timedelta(seconds=timeout_seconds)
                        )
                        text = _text_of(res)
                        _log_call(tag, name, sent - queued, time.monotonic() - sent,
                                  "error" if _is_error(res, text) else "ok")
                        return text
                    except Exception as e:  # noqa: BLE001 — 호출 단위 격리
                        _log_call(tag, name, sent - queued, time.monotonic() - sent,
                                  "timeout" if _is_timeout(e) else "error")
                        logger.warning("사전수집 도구 실패 %s: %s", name, e)
                        return _err(f"{name} 호출 실패: {e}")

                if limit == 1:
                    out = [await _one(name, args) for name, args in calls]
                else:
                    sem = asyncio.Semaphore(limit)

                    async def _bounded(name: str, args: dict[str, Any]) -> str:
                        async with sem:
                            return await _one(name, args)

                    out = list(await asyncio.gather(*(_bounded(n, a) for n, a in calls)))
    except Exception as e:  # noqa: BLE001 — 세션 실패는 전 호출 오류
        detail = _failure_detail(e, url)
        logger.warning("사전수집 MCP 세션 실패 (%s): %s", _redact_url(url), detail)
        return [_err(f"MCP 세션 실패: {detail}") for _ in calls]
    return out


def run_tool_batch(
    url: str, headers: dict[str, str] | None, calls: Sequence[tuple[str, dict]],
    *, timeout_seconds: float = TOOL_CALL_TIMEOUT_S, concurrency: int = 1,
    global_limit: threading.BoundedSemaphore | None = None, axis: str = "",
) -> list[str]:
    """`run_tool_batch_async`의 동기 래퍼(실행 루프가 없는 워커 스레드용)."""
    extra: dict[str, Any] = {"global_limit": global_limit} if global_limit is not None else {}
    if axis:
        extra["axis"] = axis
    return asyncio.run(
        run_tool_batch_async(
            url, headers, calls, timeout_seconds=timeout_seconds, concurrency=concurrency,
            **extra,
        )
    )


def _batch_concurrency(settings: object) -> int:
    """설정의 배치 동시 호출 상한(`sre_tool_batch_concurrency`) — 없거나 1 미만이면 1."""
    try:
        return max(1, int(getattr(settings, "sre_tool_batch_concurrency", 1)))
    except (TypeError, ValueError):
        return 1


def make_batch_caller(settings) -> Callable[[Sequence[tuple[str, dict]]], list[str]] | None:  # noqa: ANN001 — AgentSettings
    """설정에서 배치 호출자를 만든다. mcp URL 미설정이면 None(사전수집 불가).

    폴스타 MCP 축 전역 상한(`sre_tool_global_concurrency` · plans/194 W3)을 건다(None이면 없음).
    """
    url = getattr(settings, "polestar_mcp_url", None)
    if not url:
        return None
    headers: dict[str, str] | None = None
    tok = getattr(settings, "polestar_mcp_token", None)
    if tok is not None and tok.get_secret_value():
        headers = {"Authorization": f"Bearer {tok.get_secret_value()}"}
    timeout = float(getattr(settings, "evidence_prefetch_timeout_seconds", TOOL_CALL_TIMEOUT_S))
    concurrency = _batch_concurrency(settings)
    limit = global_semaphore("polestar", getattr(settings, "sre_tool_global_concurrency", None))
    extra: dict[str, Any] = {"global_limit": limit} if limit is not None else {}
    extra["axis"] = "polestar"

    def _call(calls: Sequence[tuple[str, dict]]) -> list[str]:
        return run_tool_batch(url, headers, calls, timeout_seconds=timeout, concurrency=concurrency,
                              **extra)

    return _call


def _apm_timeout_seconds(settings) -> float:  # noqa: ANN001 — AgentSettings
    """APM 도구 호출 1건당 상한(초 · plans/190 W3).

    `apm_tool_timeout_seconds`가 양수면 그 값, None·0 이하면 종전대로
    `evidence_prefetch_timeout_seconds`(기본 `TOOL_CALL_TIMEOUT_S`)를 쓴다.
    """
    own = getattr(settings, "apm_tool_timeout_seconds", None)
    if own is not None and float(own) > 0:
        return float(own)
    return float(getattr(settings, "evidence_prefetch_timeout_seconds", TOOL_CALL_TIMEOUT_S))


def make_apm_batch_caller(settings) -> Callable[[Sequence[tuple[str, dict]]], list[str]] | None:  # noqa: ANN001 — AgentSettings
    """APM 게이트웨이 배치 호출자(plans/187 W2). `apm_mcp_url` 미설정이면 None(APM 축 불가).

    `make_batch_caller`와 같은 방식(SSE 세션 1개 · `sre_tool_batch_concurrency` 상한 동시 호출 ·
    APM 축 전역 상한 · 호출별 오류 격리)이며 토큰은 `apm_mcp_token`을 쓴다.
    호출 1건당 상한은 `_apm_timeout_seconds`가 정한다(plans/190 W3).
    """
    url = getattr(settings, "apm_mcp_url", None)
    if not url:
        return None
    headers: dict[str, str] | None = None
    tok = getattr(settings, "apm_mcp_token", None)
    if tok is not None and tok.get_secret_value():
        headers = {"Authorization": f"Bearer {tok.get_secret_value()}"}
    timeout = _apm_timeout_seconds(settings)
    concurrency = _batch_concurrency(settings)
    limit = global_semaphore("apm", getattr(settings, "sre_tool_global_concurrency", None))
    extra: dict[str, Any] = {"global_limit": limit} if limit is not None else {}
    extra["axis"] = "apm"

    def _call(calls: Sequence[tuple[str, dict]]) -> list[str]:
        return run_tool_batch(url, headers, calls, timeout_seconds=timeout, concurrency=concurrency,
                              **extra)

    return _call


__all__ = [
    "ToolCaller", "run_tool_batch_async", "run_tool_batch", "make_batch_caller",
    "make_apm_batch_caller", "global_semaphore", "TOOL_CONCURRENCY_WAIT_TIMEOUT",
]
