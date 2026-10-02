"""APM 게이트웨이 MCP 클라이언트 — `app_impact` 승격용 `apm_events` 조회 (plans/87 J4 · D-274 ⑦).

`sre_agent_client.py` 전례(SSE transport · 정적 Bearer · 호출 타임아웃 · TCP 사전 도달성 확인)를
따르되,
게이트 경로에서 쓰이므로 더 짧게 끊는다 — 재연결 재시도 없이 호출마다 연결·해제하고, 서비스가 없으면
`UNREACHABLE_COOLDOWN` 동안 연결을 다시 시도하지 않는다(워커는 알람을 직렬 처리한다).

경계(엄수): **`apm_gateway` 패키지를 import하지 않는다**(D-274 ③) — 통신은 MCP 도구 계약
(SPEC-apm-gateway §3 `apm_events` · §3.1 정상 반환 · §3.2 오류 반환)으로만 한다. 제니퍼 자격증명은
게이트웨이에만 있고 여기에는 게이트웨이 Bearer만 있다.

이 모듈은 infrastructure 계층이므로 하위 계층 및 외부 패키지만 의존한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

APM_EVENTS_TOOL = "apm_events"


class ApmGatewayClientError(RuntimeError):
    """게이트웨이 통신 실패(연결·호출·도구 오류·파싱)."""


class ApmGatewayClient:
    """APM 게이트웨이 MCP(SSE) 클라이언트 — 호출마다 연결하고 끝나면 닫는다."""

    PROBE_TIMEOUT: float = 2.0  # 초 — TCP 연결만 시도
    UNREACHABLE_COOLDOWN: float = 30.0  # 초 — 미가용 판정 재사용 창
    CALL_TIMEOUT: float = 5.0  # 초 — 연결+초기화+도구 호출 전체

    def __init__(
        self,
        server_url: str,
        bearer_token: str | None = None,
        call_timeout: float = CALL_TIMEOUT,
    ) -> None:
        if not server_url:
            raise ValueError("ApmGatewayClient.server_url이 비어 있습니다.")
        self._server_url = server_url
        self._bearer_token = bearer_token or None
        self._call_timeout = call_timeout
        self._unreachable_until: float = 0.0
        self._unreachable_reason: str | None = None

    async def unreachable_reason(self) -> str | None:
        """게이트웨이 포트에 TCP 연결만 시도해 도달 가능 여부를 본다(D-243 전례).

        Returns:
            None(도달 가능) 또는 미가용 사유. 미가용 판정은 `UNREACHABLE_COOLDOWN` 동안 재사용한다.
        """
        now = time.monotonic()
        if now < self._unreachable_until:
            return self._unreachable_reason
        parts = urlsplit(self._server_url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, happy_eyeballs_delay=0.25),
                timeout=self.PROBE_TIMEOUT,
            )
        except TimeoutError:
            reason = f"{host}:{port} 연결 시간 초과({self.PROBE_TIMEOUT:g}초)"
        except OSError as e:
            reason = f"{host}:{port} 연결 불가({e.strerror or e})"
        else:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return None
        self._unreachable_until = now + self.UNREACHABLE_COOLDOWN
        self._unreachable_reason = reason
        return reason

    async def apm_events(
        self,
        *,
        hostname: str,
        reference_time: str,
        lookback_minutes: int,
        level: str,
        investigation_id: str,
        source_ids: list[str] | None = None,
    ) -> dict:
        """게이트웨이 `apm_events`를 호출해 반환 dict를 돌려준다.

        §3.1 정상 반환과 §3.2 `{"error": ...}` 반환을 가공 없이 그대로 돌려준다.
        `source_ids`(plans/87 J8 — 제니퍼 소스 id 목록)는 값이 있을 때만 인자에 싣는다(없으면 종전
        인자와 같다 = 전 소스).

        Raises:
            ApmGatewayClientError: 연결·호출 타임아웃·도구 오류(isError)·파싱 실패.
        """
        arguments: dict[str, Any] = {
            "hostname": hostname,
            "reference_time": reference_time,
            "lookback_minutes": int(lookback_minutes),
            "level": level,
            "investigation_id": investigation_id,
        }
        if source_ids:
            arguments["source_ids"] = list(source_ids)
        try:
            return await asyncio.wait_for(
                self._call(APM_EVENTS_TOOL, arguments), timeout=self._call_timeout
            )
        except ApmGatewayClientError:
            raise
        except TimeoutError as e:
            raise ApmGatewayClientError(
                f"게이트웨이 호출 타임아웃({APM_EVENTS_TOOL}, {self._call_timeout:g}초 초과)"
            ) from e
        except asyncio.CancelledError as e:
            # MCP SSE(anyio)는 서버가 끊기면 취소 스코프 예외를 올린다(D-213 실측). 이 태스크가
            # 실제로 취소 요청을 받은 경우(종료)만 전파하고, 그 밖은 통신 실패로 바꿔 워커 루프를
            # 지킨다.
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            raise ApmGatewayClientError(f"게이트웨이 세션 중단({APM_EVENTS_TOOL})") from e
        except ImportError as e:
            raise ApmGatewayClientError(f"MCP SDK 미설치로 게이트웨이 호출 불가: {e}") from e
        except Exception as e:  # noqa: BLE001 — 통신 실패를 명확히 노출(침묵 금지)
            raise ApmGatewayClientError(
                f"게이트웨이 호출 실패({APM_EVENTS_TOOL}): {_failure_detail(e)}"
            ) from e

    def _auth_headers(self) -> dict[str, str] | None:
        if not self._bearer_token:
            return None
        return {"Authorization": f"Bearer {self._bearer_token}"}

    async def _call(self, tool_name: str, arguments: dict) -> dict:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async with sse_client(url=self._server_url, headers=self._auth_headers()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
        if getattr(result, "isError", False):
            raise ApmGatewayClientError(
                f"게이트웨이 도구 오류({tool_name}): {_result_text(result)[:200]}"
            )
        return _parse_json_result(result)


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """예외 그룹을 끝 예외 목록으로 편다(그룹이 아니면 자기 자신)."""
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaf_exceptions(sub)]
    return [exc]


def _failure_detail(exc: Exception) -> str:
    """통신 실패 사유 — MCP SSE가 올리는 예외 그룹을 풀어 HTTP 상태를 싣는다(plans/87 F-4).

    Bearer가 틀리면 SDK가 `unhandled errors in a TaskGroup (1 sub-exception)`만 남겨 인증 실패인지
    알 수 없었다. 그룹이 아니고 HTTP 상태도 없는 예외는 종전 문구 그대로다. 토큰 값은 싣지 않는다.
    """
    leaves = _leaf_exceptions(exc)
    for leaf in leaves:
        status = getattr(getattr(leaf, "response", None), "status_code", None)
        if isinstance(status, int):
            if status in (401, 403):
                return (
                    f"HTTP {status} 인증 실패 — NOISE_APM_MCP_TOKEN이 게이트웨이의 "
                    "APM_GATEWAY_BEARER_TOKEN과 같은지 확인"
                )
            return f"HTTP {status}"
    if leaves == [exc]:
        return str(exc)
    return "; ".join(f"{type(leaf).__name__}: {leaf}" for leaf in leaves[:3])


def _result_text(raw_result: Any) -> str:
    content = getattr(raw_result, "content", raw_result)
    if isinstance(content, list):
        return "\n".join(item.text if hasattr(item, "text") else str(item) for item in content)
    return content if isinstance(content, str) else str(content)


def _parse_json_result(raw_result: Any) -> dict:
    """MCP 도구 결과(TextContent 목록)를 JSON dict로 파싱한다(sre_agent_client 동형)."""
    if raw_result is None:
        raise ApmGatewayClientError("게이트웨이 응답 없음")
    try:
        parsed = json.loads(_result_text(raw_result))
    except (json.JSONDecodeError, TypeError) as e:
        raise ApmGatewayClientError(f"게이트웨이 응답 파싱 실패: {e}") from e
    if not isinstance(parsed, dict):
        raise ApmGatewayClientError("게이트웨이 응답이 JSON 객체가 아님")
    return parsed


def build_apm_gateway_client(gate_cfg) -> ApmGatewayClient | None:  # noqa: ANN001
    """NoiseGateConfig(덕 타이핑)로 클라이언트를 만든다 — URL이 비거나 생성 실패면 None."""
    url = str(getattr(gate_cfg, "apm_mcp_url", "") or "").strip()
    if not url:
        return None
    token = getattr(gate_cfg, "apm_mcp_token", None)
    # SecretStr(.get_secret_value) 또는 평문 문자열(테스트 SimpleNamespace) 모두 수용.
    token_val = token.get_secret_value() if hasattr(token, "get_secret_value") else (token or "")
    try:
        return ApmGatewayClient(server_url=url, bearer_token=token_val or None)
    except Exception:  # noqa: BLE001 — 생성 실패는 graceful no-client(호출부가 사유를 남긴다)
        logger.warning("APM 게이트웨이 클라이언트 생성 실패", exc_info=True)
        return None


__all__ = [
    "APM_EVENTS_TOOL",
    "ApmGatewayClient",
    "ApmGatewayClientError",
    "build_apm_gateway_client",
]
