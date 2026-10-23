"""mcp_server 도구 배치 호출 (plans/50 B′ · SPEC-evidence-correlation · D-197).

결정적 사전수집이 조사 LLM과 무관하게 도구를 부르기 위한 최소 SSE 클라이언트다. holmes의
`Tool.invoke`는 `ToolInvokeContext(llm=…)`를 요구해(실측 0.36.0) LLM 객체 없이는 쓸 수 없으므로
`mcp` 클라이언트를 직접 쓴다(`pyproject.toml`에 `mcp<2` 명시 — D-181 상한 동일).

한 배치는 **SSE 세션 1개**를 열어 `concurrency` 상한으로 동시 호출한다(plans/187 W21 P1 — 1이면
종전 순차와 동일). mcp `BaseSession.send_request`가 요청 id별 응답 스트림으로 응답을 매핑하므로 한
세션 안의 동시 요청이 안전하다(실측 1.25.0). 응답은 calls 순서를 유지한다. 호출 단위로 실패를 격리해
부분 반환을 보장하고(Known Mistakes: 개별 try), 세션 자체가 실패하면 전 호출이 `{error}`다. 동기
래퍼는 dispatcher 워커 스레드(실행 루프 없음)에서 `asyncio.run`으로 돈다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sre_agent.settings import TOOL_CALL_TIMEOUT_S

logger = logging.getLogger(__name__)

#: (도구명, 인자) → 도구 반환 JSON 문자열. 응용 계층은 이 콜러블만 안다.
ToolCaller = Callable[[str, dict], str]


def _err(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


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
) -> list[str]:
    """SSE 세션 1개로 calls를 호출한다. 호출별 실패는 `{error}`로 격리한다.

    `concurrency`(최소 1)개까지 동시에 `call_tool`을 보내고, 응답은 calls 순서로 돌려준다.
    1이면 종전처럼 calls 순서대로 하나씩 호출한다.
    """
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    limit = max(1, int(concurrency))
    try:
        async with sse_client(url, headers=headers, timeout=timeout_seconds) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def _one(name: str, args: dict[str, Any]) -> str:
                    try:
                        res = await session.call_tool(
                            name, args, read_timeout_seconds=timedelta(seconds=timeout_seconds)
                        )
                        return _text_of(res)
                    except Exception as e:  # noqa: BLE001 — 호출 단위 격리
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
) -> list[str]:
    """`run_tool_batch_async`의 동기 래퍼(실행 루프가 없는 워커 스레드용)."""
    return asyncio.run(
        run_tool_batch_async(
            url, headers, calls, timeout_seconds=timeout_seconds, concurrency=concurrency
        )
    )


def _batch_concurrency(settings: object) -> int:
    """설정의 배치 동시 호출 상한(`sre_tool_batch_concurrency`) — 없거나 1 미만이면 1."""
    try:
        return max(1, int(getattr(settings, "sre_tool_batch_concurrency", 1)))
    except (TypeError, ValueError):
        return 1


def make_batch_caller(settings) -> Callable[[Sequence[tuple[str, dict]]], list[str]] | None:  # noqa: ANN001 — AgentSettings
    """설정에서 배치 호출자를 만든다. mcp URL 미설정이면 None(사전수집 불가)."""
    url = getattr(settings, "polestar_mcp_url", None)
    if not url:
        return None
    headers: dict[str, str] | None = None
    tok = getattr(settings, "polestar_mcp_token", None)
    if tok is not None and tok.get_secret_value():
        headers = {"Authorization": f"Bearer {tok.get_secret_value()}"}
    timeout = float(getattr(settings, "evidence_prefetch_timeout_seconds", TOOL_CALL_TIMEOUT_S))
    concurrency = _batch_concurrency(settings)

    def _call(calls: Sequence[tuple[str, dict]]) -> list[str]:
        return run_tool_batch(url, headers, calls, timeout_seconds=timeout, concurrency=concurrency)

    return _call


def make_apm_batch_caller(settings) -> Callable[[Sequence[tuple[str, dict]]], list[str]] | None:  # noqa: ANN001 — AgentSettings
    """APM 게이트웨이 배치 호출자(plans/187 W2). `apm_mcp_url` 미설정이면 None(APM 축 불가).

    `make_batch_caller`와 같은 방식(SSE 세션 1개 · `sre_tool_batch_concurrency` 상한 동시 호출 ·
    호출별 오류 격리)이며 토큰은 `apm_mcp_token`을 쓴다.
    """
    url = getattr(settings, "apm_mcp_url", None)
    if not url:
        return None
    headers: dict[str, str] | None = None
    tok = getattr(settings, "apm_mcp_token", None)
    if tok is not None and tok.get_secret_value():
        headers = {"Authorization": f"Bearer {tok.get_secret_value()}"}
    timeout = float(getattr(settings, "evidence_prefetch_timeout_seconds", TOOL_CALL_TIMEOUT_S))
    concurrency = _batch_concurrency(settings)

    def _call(calls: Sequence[tuple[str, dict]]) -> list[str]:
        return run_tool_batch(url, headers, calls, timeout_seconds=timeout, concurrency=concurrency)

    return _call


__all__ = [
    "ToolCaller", "run_tool_batch_async", "run_tool_batch", "make_batch_caller",
    "make_apm_batch_caller",
]
