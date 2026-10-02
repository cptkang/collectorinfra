"""비SQL 관측 소스 MCP 세션 — 두 번째 이후 MCP 엔드포인트(plans/125 A-2 · G-8 (a) · D-274 ⑦).

`mcp_server`(SQL·폴스타 도구)와 별개인 관측 게이트웨이(예: APM 게이트웨이 `apm_gateway/`)에 붙는다.
엔드포인트는 설정 `MCP_SOURCE_ENDPOINTS`(시스템 코드 → URL)에서 오고, 없으면 그 시스템은
비활성이다(처리기 미등록 — 신규 `enable_*` 플래그 없음 · D-162).

**세션 재사용**: 한 처리기 호출 안의 fan-out(호스트 N × 보기 M)을 연결 1회로 처리한다 — 호출마다
연결하면 지연이 곱으로 는다(plans/125 R-2). 호출마다 상한(`call_timeout`)을 둔다.

**경계**: 게이트웨이 패키지를 import 하지 않는다(D-274 ③) — MCP 도구 계약(JSON 텍스트 반환)으로만
통신한다. 반환 봉투(`rows`·`queried_at`·`window`·`instance_resolution`·`limits` 또는
`{"error", "reason"}`)는 **변형하지 않고** 돌려준다 — 해석은 처리기가 한다.

계층: infrastructure(clients).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class SourceMcpError(RuntimeError):
    """관측 소스 MCP 통신 실패(연결·호출 타임아웃·도구 오류·파싱).

    사유 문구에 토큰을 싣지 않는다.
    """


class _ToolSession(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


#: `(url, headers) -> 비동기 컨텍스트(열린 세션)` — 테스트는 모의 세션 공장을 준다(이 호스트의 루트
#: 파이썬에는 `mcp` 가 없다 — 실연결 검증은 게이트웨이 기동 환경에서).
SessionFactory = Callable[[str, dict[str, str] | None], AbstractAsyncContextManager[_ToolSession]]


@asynccontextmanager
async def _mcp_sse_session(url: str, headers: dict[str, str] | None) -> AsyncIterator[_ToolSession]:
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(url=url, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


class SourceMcpSession:
    """열린 세션 1개 — 도구 호출마다 상한을 두고 JSON 봉투를 돌려준다."""

    def __init__(self, session: _ToolSession, *, call_timeout: float, label: str) -> None:
        self._session = session
        self._call_timeout = call_timeout
        self._label = label

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """도구 1회 호출 → 봉투 dict. 게이트웨이 계약 오류(`{"error": …}`)도 dict 로 돌려준다.

        Raises:
            SourceMcpError: 호출 타임아웃 · 도구 오류(isError) · JSON 이 아닌 반환.
        """
        args = {k: v for k, v in arguments.items() if v is not None}
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool, args), timeout=self._call_timeout
            )
        except TimeoutError as e:
            raise SourceMcpError(
                f"{self._label} 도구 호출 시간 초과({tool}, {self._call_timeout:g}초)"
            ) from e
        if getattr(result, "isError", False):
            raise SourceMcpError(f"{self._label} 도구 오류({tool}): {_result_text(result)[:200]}")
        return _parse_json(result, tool, self._label)


@asynccontextmanager
async def open_source_session(
    url: str,
    token: str | None,
    *,
    call_timeout: float,
    label: str,
    session_factory: SessionFactory | None = None,
) -> AsyncIterator[SourceMcpSession]:
    """관측 소스 MCP 세션을 연다(처리기 호출 1회 동안 재사용).

    Raises:
        SourceMcpError: 연결 실패 · SDK 미설치(사유를 구조화해 처리기가 소스 상태 행에 싣는다).
    """
    headers = {"Authorization": f"Bearer {token}"} if token else None
    factory = session_factory or _mcp_sse_session
    try:
        async with factory(url, headers) as session:
            yield SourceMcpSession(session, call_timeout=call_timeout, label=label)
    except SourceMcpError:
        raise
    except ImportError as e:
        raise SourceMcpError(f"MCP SDK 미설치로 {label} 에 연결할 수 없습니다: {e}") from e
    except asyncio.CancelledError:
        # MCP SSE(anyio)는 서버가 끊기면 취소 스코프 예외를 올린다(D-213 실측) — 실제 취소만
        # 전파한다.
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise
        raise SourceMcpError(f"{label} 세션이 중단됐습니다") from None
    except Exception as e:  # noqa: BLE001 — 통신 실패를 사유로 드러낸다(침묵 금지)
        raise SourceMcpError(f"{label} 연결·호출 실패: {type(e).__name__}") from e


def _result_text(raw: Any) -> str:
    content = getattr(raw, "content", raw)
    if isinstance(content, list):
        return "\n".join(item.text if hasattr(item, "text") else str(item) for item in content)
    return content if isinstance(content, str) else str(content)


def _parse_json(raw: Any, tool: str, label: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(_result_text(raw))
    except (json.JSONDecodeError, TypeError) as e:
        raise SourceMcpError(f"{label} 응답 파싱 실패({tool}): {e}") from e
    if not isinstance(parsed, dict):
        raise SourceMcpError(f"{label} 응답이 JSON 객체가 아닙니다({tool})")
    return parsed


__all__ = ["SessionFactory", "SourceMcpError", "SourceMcpSession", "open_source_session"]
