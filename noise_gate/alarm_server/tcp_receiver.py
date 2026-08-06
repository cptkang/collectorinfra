"""TCP 소켓 알람 수신기.

폴스타 알람 시스템으로부터 TCP 소켓 연결을 유지하며 알람을 수신한다.
단일 행 JSON 형식으로 파싱하여 Redis Stream에 발행한다.

폴스타 권장 메시지 템플릿 (단일 행 JSON):
    {"alarmId":"${alarmId}","severity":${severity},...}

수신 포트: ALARM_SERVER_SOCKET_PORT (기본 9100)
"""

from __future__ import annotations

import asyncio
import json
import logging

from noise_gate.alarm_server.base_receiver import BaseReceiver

logger = logging.getLogger(__name__)


class TcpReceiver(BaseReceiver):
    """폴스타 알람 시스템으로부터 TCP 소켓 연결을 유지하며 알람을 수신한다."""

    async def start(self) -> None:
        """TCP 서버를 시작하고 연결 수락 루프를 실행한다."""
        await self._init_redis()
        server = await asyncio.start_server(
            self._handle_connection,
            self._config.socket_host,
            self._config.socket_port,
        )
        logger.info(
            "TCP 알람 수신 서버 시작: %s:%s",
            self._config.socket_host,
            self._config.socket_port,
        )
        async with server:
            await server.serve_forever()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """개별 TCP 연결을 처리한다.

        연결이 유지되는 동안 줄 단위로 JSON을 읽어 Redis에 발행한다.

        Args:
            reader: 스트림 리더
            writer: 스트림 라이터
        """
        peer = writer.get_extra_info("peername")
        logger.info("알람 연결 수립: %s", peer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                payload = self._parse(line)
                if payload:
                    await self._publish(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("알람 수신 오류 (%s)", peer)
        finally:
            writer.close()
            logger.info("알람 연결 종료: %s", peer)

    def _parse(self, raw: bytes) -> dict | None:
        """수신 바이트를 dict으로 변환한다.

        폴스타 권장 단일 행 JSON 템플릿 기준으로 파싱한다.

        Args:
            raw: 수신된 원시 바이트

        Returns:
            파싱된 딕셔너리 또는 None (파싱 실패 시)
        """
        try:
            return json.loads(raw.decode("utf-8").strip())
        except Exception:
            logger.warning("알람 페이로드 파싱 실패: %r", raw[:200])
            return None
