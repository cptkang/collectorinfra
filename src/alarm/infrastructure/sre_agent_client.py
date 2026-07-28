"""sre_agent 조사 서비스 MCP 클라이언트 (Plan 64 §0.2 CW-A · sre-agent/05 §3).

`DBHubClient`(src/dbhub/client.py) 패턴을 미러링한다 — SSE transport·재연결·타임아웃·
JSON 결과 파싱. 차이점은 ①정적 Bearer 헤더(sre-agent/05 §5 인증)를 SSE 연결에 실을 수
있고 ②노출 도구가 `sre_investigate_alarm`(submit)·`sre_get_investigation`(poll)이라는 점뿐이다.

경계(엄수): **sre_agent 패키지를 import하지 않는다** — 통신은 MCP JSON 계약(submit/poll)으로만
한다. 조사 실행 본체(dispatcher·severity_judge·브리핑 조립)는 sre_agent 소관이고, 여기서는
그 서비스를 원격 MCP로 호출만 한다.

이 모듈은 infrastructure 계층이므로 하위 계층(domain/config/utils) 및 외부 패키지만 의존한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

logger = logging.getLogger(__name__)


class SreAgentClientError(RuntimeError):
    """sre_agent 조사 서비스 통신 실패(연결·호출·파싱)."""


class SreAgentClient:
    """sre_agent 조사 서비스 MCP(SSE) 클라이언트.

    원격 조사 서비스와 SSE transport로 통신한다. `submit`으로 조사 잡을 제출하고
    `poll`로 상태·브리핑을 조회한다(submit/poll 비동기 잡 패턴 — sre-agent/05 §3).
    정적 Bearer 토큰이 주어지면 SSE 연결 헤더에 실어 인증한다(없으면 무헤더 — 3-D에서 강제).
    """

    MAX_RECONNECT_ATTEMPTS: int = 3
    RECONNECT_DELAY: float = 2.0  # 초

    def __init__(
        self,
        server_url: str,
        bearer_token: Optional[str] = None,
        mcp_call_timeout: float = 10.0,
    ) -> None:
        """클라이언트를 초기화한다.

        Args:
            server_url: 조사 서비스 MCP SSE 엔드포인트(예: http://host:9098/sse).
            bearer_token: 정적 Bearer 토큰(None/빈 값이면 인증 헤더 미첨부 — 3-D에서 강제).
            mcp_call_timeout: submit/poll 개별 호출 타임아웃(초). 전체(submit+poll) 타임아웃은
                호출부(오케스트레이션)가 별도 가드한다(per-call만으로는 폴링 루프를 못 막음).
        """
        if not server_url:
            raise ValueError("SreAgentClient.server_url이 비어 있습니다.")
        self._server_url = server_url
        self._bearer_token = bearer_token or None
        self._mcp_call_timeout = mcp_call_timeout
        self._mcp_session: Optional[Any] = None
        self._connected: bool = False
        self._sse_context: Optional[Any] = None
        self._session_context: Optional[Any] = None

    def _auth_headers(self) -> Optional[dict[str, str]]:
        """Bearer 헤더를 구성한다(토큰 없으면 None — 무헤더)."""
        if not self._bearer_token:
            return None
        return {"Authorization": f"Bearer {self._bearer_token}"}

    async def connect(self) -> None:
        """조사 서비스에 SSE transport로 연결한다.

        Raises:
            SreAgentClientError: 연결 실패 시.
        """
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            # SSE 클라이언트로 원격 조사 서비스에 연결(Bearer 헤더는 있을 때만 첨부).
            self._sse_context = sse_client(
                url=self._server_url, headers=self._auth_headers()
            )
            sse_transport = await self._sse_context.__aenter__()
            read_stream, write_stream = sse_transport

            self._session_context = ClientSession(read_stream, write_stream)
            self._mcp_session = await self._session_context.__aenter__()
            await self._mcp_session.initialize()

            self._connected = True
            logger.debug("조사 서비스 연결 성공 (SSE): %s", self._server_url)
        except ImportError as e:
            # MCP SDK 미설치 — DBHubClient와 달리 제한 모드로 통과시키지 않는다(조사 트리거는
            # 옵트인 부가기능이므로, 통신 불가를 명확히 실패로 노출해 호출부가 graceful 폴백한다).
            raise SreAgentClientError(f"MCP SDK 미설치로 조사 서비스 연결 불가: {e}") from e
        except Exception as e:
            raise SreAgentClientError(
                f"조사 서비스 연결 실패 ({self._server_url}): {e}"
            ) from e

    async def disconnect(self) -> None:
        """조사 서비스 연결을 종료한다(실패는 warning 후 무시)."""
        try:
            if self._session_context:
                await self._session_context.__aexit__(None, None, None)
            if self._sse_context:
                await self._sse_context.__aexit__(None, None, None)
        except Exception as e:  # noqa: BLE001 — 종료 실패가 호출부를 막지 않는다
            logger.warning("조사 서비스 연결 종료 중 에러(무시): %s", e)
        finally:
            self._mcp_session = None
            self._sse_context = None
            self._session_context = None
            self._connected = False
            logger.debug("조사 서비스 연결 종료")

    async def _ensure_connected_with_retry(self) -> None:
        """연결 상태를 확인하고 필요 시 재연결한다(DBHubClient 패턴).

        Raises:
            SreAgentClientError: 최대 재연결 시도 초과 시.
        """
        if self._connected and self._mcp_session:
            return
        for attempt in range(self.MAX_RECONNECT_ATTEMPTS):
            try:
                await self.connect()
                return
            except Exception as e:  # noqa: BLE001 — 재시도 후 최종 실패만 전파
                if attempt < self.MAX_RECONNECT_ATTEMPTS - 1:
                    delay = self.RECONNECT_DELAY * (attempt + 1)
                    logger.warning(
                        "조사 서비스 재연결 시도 %d/%d, %.1f초 후 재시도: %s",
                        attempt + 1, self.MAX_RECONNECT_ATTEMPTS, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise SreAgentClientError(
                        f"조사 서비스 재연결 실패 ({self.MAX_RECONNECT_ATTEMPTS}회 시도): {e}"
                    ) from e

    async def submit(self, payload: dict) -> dict:
        """조사 잡을 제출한다(`sre_investigate_alarm`).

        Args:
            payload: 트리거 페이로드(`contract_version: "1"` — build_trigger_payload 산출물).

        Returns:
            `{investigation_id, status: accepted|duplicate|rejected, reason?}` (파싱된 dict).

        Raises:
            SreAgentClientError: 연결·호출·파싱 실패 시.
        """
        return await self._call_tool_json(
            "sre_investigate_alarm", {"payload": payload}
        )

    async def poll(self, investigation_id: str) -> dict:
        """조사 잡 상태·브리핑을 조회한다(`sre_get_investigation`).

        Args:
            investigation_id: submit이 반환한 조사 잡 식별자.

        Returns:
            `{status, briefing?, verdict?, ...}` (파싱된 dict).

        Raises:
            SreAgentClientError: 연결·호출·파싱 실패 시.
        """
        return await self._call_tool_json(
            "sre_get_investigation", {"investigation_id": investigation_id}
        )

    async def diagnose(
        self,
        question: str,
        server_name: Optional[str] = None,
        hostname: Optional[str] = None,
        db_id: Optional[str] = None,
    ) -> dict:
        """pull형 장애 진단 잡을 제출한다(`sre_diagnose` — sre-agent/05 §3·§7).

        챗 오케스트레이션의 `fault_diagnosis` 의도 위임용(Plan 64 §0.2 CW-B). submit
        (`sre_investigate_alarm`)과 **동일한 비동기 잡 패턴**이라, 반환된 investigation_id로
        기존 `poll`을 그대로 재사용한다(별도 폴링 경로를 만들지 않는다).

        Args:
            question: 사용자 자연어 진단 질의(예: "web-01 서버 원인 분석해줘").
            server_name: 대상 서버명(선택 — 식별자 이원화, sre-agent/05 §4).
            hostname: 대상 hostname(선택).
            db_id: 대상 폴스타 DB 식별자(선택 — 존별 스코프 힌트).

        Returns:
            `{investigation_id, status, ...}` (파싱된 dict). 이후 `poll`로 진단 결과를 조회한다.

        Raises:
            SreAgentClientError: 연결·호출·파싱 실패 시.
        """
        # 빈 값은 전송하지 않는다(선택 인자 — 서비스가 결측을 이유로 거부하지 않음, §4).
        arguments: dict[str, Any] = {"question": question}
        if server_name:
            arguments["server_name"] = server_name
        if hostname:
            arguments["hostname"] = hostname
        if db_id:
            arguments["db_id"] = db_id
        return await self._call_tool_json("sre_diagnose", arguments)

    # --- 내부 메서드 (DBHubClient 미러링) ---

    async def _call_tool_json(self, tool_name: str, arguments: dict) -> dict:
        """MCP 도구를 호출하고 JSON 결과를 dict로 파싱한다(타임아웃·재연결 포함)."""
        await self._ensure_connected_with_retry()
        if self._mcp_session is None:
            raise SreAgentClientError("조사 서비스 MCP 세션이 초기화되지 않았습니다.")
        start = time.time()
        try:
            result = await asyncio.wait_for(
                self._mcp_session.call_tool(tool_name, arguments),
                timeout=self._mcp_call_timeout,
            )
        except asyncio.TimeoutError as e:
            raise SreAgentClientError(
                f"조사 서비스 호출 타임아웃 ({tool_name}, {self._mcp_call_timeout}초 초과)"
            ) from e
        except Exception as e:  # noqa: BLE001 — 통신 실패를 명확히 노출(침묵 금지)
            raise SreAgentClientError(f"조사 서비스 호출 실패 ({tool_name}): {e}") from e
        logger.debug(
            "조사 서비스 호출 %s 완료 (%.0fms)", tool_name, (time.time() - start) * 1000
        )
        return self._parse_json_result(result)

    @staticmethod
    def _parse_json_result(raw_result: Any) -> dict:
        """MCP 도구 결과(TextContent 등)를 JSON dict로 파싱한다(DBHubClient 동형)."""
        if raw_result is None:
            return {}
        try:
            content = raw_result
            if hasattr(raw_result, "content"):
                content = raw_result.content
            if isinstance(content, list):
                parts = [
                    item.text if hasattr(item, "text") else str(item)
                    for item in content
                ]
                text = "\n".join(parts)
            elif isinstance(content, str):
                text = content
            else:
                text = str(content)
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            raise SreAgentClientError(f"조사 서비스 응답 파싱 실패: {e}") from e


@asynccontextmanager
async def get_sre_agent_client(
    server_url: str,
    bearer_token: Optional[str] = None,
    mcp_call_timeout: float = 10.0,
) -> AsyncGenerator[SreAgentClient, None]:
    """조사 서비스 클라이언트를 생성하고 연결을 관리한다(DBHubClient의 get_dbhub_client 미러).

    Yields:
        연결된 SreAgentClient 인스턴스.
    """
    client = SreAgentClient(server_url, bearer_token, mcp_call_timeout)
    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()


__all__ = ["SreAgentClient", "SreAgentClientError", "get_sre_agent_client"]
