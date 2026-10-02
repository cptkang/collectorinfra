"""질의응답 스레드 기록 — 소유 키 판정과 턴 기록 (D-248).

**완결된 턴만 기록한다** — 스트림의 `done` 이벤트 또는 `/query`·`/query/file`의
`QueryResponse` 반환. 실패·중단된 턴은 답이 없고, 이 기록은 "무엇을 묻고 무엇을
받았나"를 사용자 화면의 질의 이력에서 다시 보여 주는 용도다.

기록은 `done`을 클라이언트에 내보내기 **전에** 끝낸다. 그래야 클라이언트가 `done`을
받자마자 목록을 다시 불러도 방금 턴이 들어 있다. 대신 DB가 느려도 응답이 오래 막히지
않도록 상한(`_RECORD_TIMEOUT_SEC`)을 둔다. 기록 실패는 질의를 막지 않고 경고로 남긴다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from fastapi import Request

from src.api.dependencies import ANONYMOUS_USER

logger = logging.getLogger(__name__)

CLIENT_ID_HEADER = "X-Client-Id"
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_RECORD_TIMEOUT_SEC = 3.0


def history_owner(request: Request, current_user: dict[str, Any] | None) -> str | None:
    """이력의 소유 키. 로그인 사용자는 user_id, 익명은 브라우저 식별자다.

    인증이 꺼진 운영에서는 전원이 `anonymous` 하나라서(D-183 G-1) user_id로 묶으면
    남의 대화가 내 목록에 섞인다. 그래서 브라우저가 만든 무작위 식별자(`X-Client-Id`)로
    나눈다. 식별자가 없거나 형식이 틀리면 None을 돌려주고, 그때는 기록도 조회도 하지
    않는다 — 섞이는 것보다 비어 있는 편이 낫다.
    """
    sub = (current_user or {}).get("sub")
    if sub and sub != ANONYMOUS_USER["sub"]:
        return f"user:{sub}"
    client_id = request.headers.get(CLIENT_ID_HEADER, "")
    if _CLIENT_ID_RE.match(client_id):
        return f"anon:{client_id}"
    return None


def _turn_status(payload: dict[str, Any]) -> str:
    # QueryResponse는 status를 싣고, 스트림 done 이벤트는 싣지 않는다 — 같은 규칙으로 유도한다.
    if payload.get("status"):
        return str(payload["status"])
    if payload.get("clarification"):
        return "clarification"
    if payload.get("awaiting_approval"):
        return "awaiting_approval"
    return "completed"


def _done_payload(chunk: Any) -> dict[str, Any] | None:
    """SSE 청크가 `done` 이벤트면 본문을 돌려준다."""
    if not isinstance(chunk, str) or not chunk.startswith("data: ") or '"done"' not in chunk:
        return None
    try:
        payload = json.loads(chunk[len("data: "):])
    except ValueError:
        return None
    if isinstance(payload, dict) and payload.get("type") == "done":
        return payload
    return None


class TurnRecorder:
    """요청 1건의 턴 기록기. 저장소나 소유 키가 없으면 아무것도 하지 않는다."""

    def __init__(
        self,
        request: Request,
        current_user: dict[str, Any] | None,
        *,
        user_query: str,
        has_upload: bool,
    ) -> None:
        self._repo = getattr(request.app.state, "thread_repo", None)
        self._owner = history_owner(request, current_user) if self._repo else None
        self._user_query = user_query
        self._has_upload = has_upload

    async def record(self, payload: dict[str, Any]) -> None:
        thread_id = payload.get("thread_id")
        # 스레드가 없는 응답(파일 경로의 첫 턴 역질문 등)은 이어 붙일 곳이 없다.
        if not self._repo or not self._owner or not thread_id:
            return
        turn = {
            "thread_id": thread_id,
            "query_id": payload.get("query_id"),
            "user_query": self._user_query,
            "response": payload.get("response"),
            "status": _turn_status(payload),
            "executed_sql": payload.get("executed_sql"),
            "row_count": payload.get("row_count"),
            "processing_time_ms": payload.get("processing_time_ms"),
            "has_upload": self._has_upload,
            "db_scope": payload.get("db_scope"),
        }
        try:
            await asyncio.wait_for(
                self._repo.add_turn(self._owner, turn), timeout=_RECORD_TIMEOUT_SEC
            )
        except Exception as e:
            logger.warning("질의응답 스레드 기록 실패(thread=%s): %r", thread_id, e)

    async def response(self, resp: Any) -> Any:
        """`QueryResponse`를 기록하고 그대로 돌려준다."""
        await self.record(resp.model_dump())
        return resp

    async def stream(self, events: AsyncGenerator[str, None]) -> AsyncIterator[str]:
        """SSE 제너레이터를 감싸 `done` 이벤트를 내보내기 직전에 기록한다."""
        async with contextlib.aclosing(events) as it:
            async for chunk in it:
                payload = _done_payload(chunk)
                if payload is not None:
                    await self.record(payload)
                yield chunk
