"""MCP 서버 — `apm_*` 도구 등록 · 정적 Bearer · 감사 (plans/87 §0.7 (3) · SPEC-apm-gateway §3).

전송은 SSE(`sre_agent` RemoteMCPToolset `mode: sse` 전례). Bearer 미들웨어는 `mcp_server` 패키지 서버
모듈의 `StaticBearerAuthMiddleware`를 **복제**했다(경계 불변식상 import 불가 — R-21 드리프트는
게이트웨이 테스트가 감시). 도구는 예외를 전파하지 않고 `{"error": code, "reason": …}` JSON을
돌려준다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.types import ASGIApp, Receive, Scope, Send

from apm_gateway.adapters.jennifer.allowlist import NotAllowedError
from apm_gateway.application.masking import mask_text
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.errors import API_ERROR, CONTRACT_VIOLATION, INVALID_ARGUMENT, ApmError
from apm_gateway.interface.audit import audit

logger = logging.getLogger(__name__)

SERVER_NAME = "apm-gateway"


class StaticBearerAuthMiddleware:
    """정적 Bearer 토큰 검증 ASGI 미들웨어(D-125) — `mcp_server` 동명 클래스 복제.

    token이 None이면 무인증 통과(로컬/개발). 설정되면 모든 HTTP 요청에 `Authorization: Bearer
    <token>`을
    요구하고 불일치 시 401을 돌려준다.
    """

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.token is None:
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        provided = headers.get(b"authorization", b"").decode("latin-1")
        if provided != f"Bearer {self.token}":
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps({"error": "unauthorized"}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def build_asgi_app(mcp: FastMCP, token: str | None) -> Starlette:
    """SSE 앱에 Bearer 미들웨어를 씌운다(`mcp.run(transport="sse")`에는 주입점이 없다)."""
    app = mcp.sse_app()
    app.add_middleware(StaticBearerAuthMiddleware, token=token)
    return app


async def run_tool(
    tools: ApmTools,
    tool: str,
    target: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    investigation_id: str | None = None,
    thread_id: str | None = None,
) -> str:
    """도구 1회 실행 — 오류를 계약 JSON으로 바꾸고 감사 1줄을 남긴다."""
    started = time.monotonic()
    calls_before = tools.sources.calls()
    try:
        result = await call()
    except ApmError as e:
        result = tools.err(tool, e.code, e.reason)
    except NotAllowedError as e:  # 도구 코드가 허용목록 밖 요청을 만들었다 — 게이트웨이 버그
        logger.warning("허용목록 거부(게이트웨이 버그): tool=%s %s", tool, e)
        result = tools.err(tool, CONTRACT_VIOLATION, f"허용목록 거부: {e}")
    except (TypeError, ValueError) as e:
        result = tools.err(tool, INVALID_ARGUMENT, str(e))
    except Exception as e:
        logger.exception("도구 실행 실패: %s", tool)
        result = tools.err(tool, API_ERROR, f"내부 오류: {type(e).__name__}")
    elapsed_ms = (time.monotonic() - started) * 1000
    called = {
        sid: n - calls_before.get(sid, 0)
        for sid, n in tools.sources.calls().items()
        if n > calls_before.get(sid, 0)
    }
    audit(
        tool,
        mask_text(target, limit=120),
        elapsed_ms,
        rows=result.get("row_count"),
        api_calls=sum(called.values()),
        sources=",".join(f"{sid}:{n}" for sid, n in called.items()) or "-",
        error=result.get("error"),
        investigation_id=investigation_id,
        thread_id=thread_id,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def register_tools(mcp: FastMCP, tools: ApmTools) -> list[str]:
    """`apm_*` 8종 + `gateway_health`를 등록하고 이름 목록을 돌려준다."""

    @mcp.tool()
    async def apm_instance_map(
        hostname: str | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """APM 인스턴스 목록과 hostname 정합 결과. hostname을 주면 그 서버의 WAS 인스턴스만(정합
        신뢰도·근거 포함). source_ids로 APM 소스를 좁힐 수 있다(비면 전 소스)."""
        return await run_tool(
            tools,
            "apm_instance_map",
            hostname or "*",
            lambda: tools.apm_instance_map(hostname, source_ids),
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_app_health(
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """WAS 골든 시그널 — 평균 응답시간·TPS·액티브 서비스·PLC 거절률(현재) + 구간
        p50/p95·에러율(분 단위 · 최근 10분 상한) + 판정(was_signals)."""
        return await run_tool(
            tools,
            "apm_app_health",
            hostname,
            lambda: tools.apm_app_health(
                hostname, instance_id, reference_time, lookback_minutes, source_ids
            ),
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_runtime_health(
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """JVM 런타임 — 힙 사용량(MB)·GC 시간 비중(%)·프로세스 CPU·스레드 + 구간 추세(5분 간격) +
        판정(힙 압박·GC 지연)."""
        return await run_tool(
            tools,
            "apm_runtime_health",
            hostname,
            lambda: tools.apm_runtime_health(
                hostname, instance_id, reference_time, lookback_minutes, source_ids
            ),
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_resource_pool(
        hostname: str,
        instance_id: int | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """자원 풀(현재값 전용) — DB 커넥션 풀 사용률·실행 모드별 액티브 서비스 수 + 판정(DB 풀
        고갈·스레드 정체)."""
        return await run_tool(
            tools,
            "apm_resource_pool",
            hostname,
            lambda: tools.apm_resource_pool(hostname, instance_id, source_ids),
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_slow_transactions(
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        n: int | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """느린 트랜잭션 상위 N(≤20) — 시간 분해(cpu·sql·fetch·external·network)·오류
        유형·profile_ref + 판정(SQL 지연·외부 호출 지연)."""
        return await run_tool(
            tools,
            "apm_slow_transactions",
            hostname,
            lambda: tools.apm_slow_transactions(
                hostname, instance_id, reference_time, lookback_minutes, n, source_ids
            ),
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_active_services(
        hostname: str,
        instance_id: int | None = None,
        n: int | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """지금 실행 중인 서비스 상위 N(≤20, 경과 시간 순 · 현재값 전용) — 상태·실행 모드·실행
        텍스트(마스킹) + 판정(스레드 정체)."""
        return await run_tool(
            tools,
            "apm_active_services",
            hostname,
            lambda: tools.apm_active_services(hostname, instance_id, n, source_ids),
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_events(
        hostname: str,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        level: str | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """APM 이벤트(기본 최근 30분 · 상한 24시간) — 유형·레벨·값·메시지(마스킹)·profile_ref ·
        level은 최소 레벨(fatal·warning·normal) + 판정."""
        return await run_tool(
            tools,
            "apm_events",
            hostname,
            lambda: tools.apm_events(hostname, reference_time, lookback_minutes, level, source_ids),
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_transaction_profile(
        hostname: str,
        domain_id: int | None = None,
        txid: str | None = None,
        time_ms: int | None = None,
        top_k: int | None = None,
        source_id: str | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """개별 트랜잭션 프로파일(마스킹 발췌)·SQL(리터럴 마스킹). source_id·domain_id·txid·
        time_ms는 앞 도구의 profile_ref를 그대로 넘긴다(APM 소스가 둘 이상이면 source_id 필수)."""
        return await run_tool(
            tools,
            "apm_transaction_profile",
            hostname,
            lambda: tools.apm_transaction_profile(
                hostname, domain_id, txid, time_ms, top_k, investigation_id, source_id
            ),
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def gateway_health() -> str:
        """게이트웨이 상태 — APM 소스별 설정·도달 여부·도메인 수 · 허용 경로 수 · 전체 상태 ·
        폴러 상태(헬스체크용)."""
        return await run_tool(tools, "gateway_health", "-", tools.gateway_health)

    return [
        "apm_instance_map",
        "apm_app_health",
        "apm_runtime_health",
        "apm_resource_pool",
        "apm_slow_transactions",
        "apm_active_services",
        "apm_events",
        "apm_transaction_profile",
        "gateway_health",
    ]


def create_server(tools: ApmTools, *, host: str = "127.0.0.1", port: int = 9096) -> FastMCP:
    mcp = FastMCP(SERVER_NAME, host=host, port=port)
    names = register_tools(mcp, tools)
    logger.info("APM 게이트웨이 MCP 서버 생성: 도구 %d종(%s)", len(names), ", ".join(names))
    return mcp
