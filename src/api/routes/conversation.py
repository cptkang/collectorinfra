"""대화 세션 관리 라우트.

멀티턴 대화 세션의 히스토리 조회, 삭제를 지원한다.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from src.api.dependencies import require_user

logger = logging.getLogger(__name__)
router = APIRouter()


class MessageItem(BaseModel):
    """대화 메시지 항목."""

    role: str = Field(..., description="메시지 역할: human | ai")
    content: str = Field(..., description="메시지 내용")


class ConversationResponse(BaseModel):
    """대화 히스토리 응답."""

    thread_id: str
    messages: list[MessageItem]
    turn_count: int
    has_pending_approval: bool = False
    has_pending_synonym_reuse: bool = False
    has_pending_registrations: bool = False


@router.get(
    "/conversation/{thread_id}",
    response_model=ConversationResponse,
)
async def get_conversation(
    request: Request,
    thread_id: str,
    current_user: dict = Depends(require_user),
) -> ConversationResponse:
    """대화 히스토리를 조회한다.

    Args:
        request: FastAPI Request
        thread_id: 세션 ID

    Returns:
        대화 히스토리

    Raises:
        HTTPException: 세션을 찾을 수 없을 때
    """
    graph = request.app.state.graph
    thread_config = {"configurable": {"thread_id": thread_id}}

    try:
        state_snapshot = await asyncio.to_thread(
            graph.get_state, thread_config
        )
    except Exception as e:
        logger.error("대화 세션 조회 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    if not state_snapshot or not state_snapshot.values:
        raise HTTPException(status_code=404, detail="대화 세션을 찾을 수 없습니다.")

    state = state_snapshot.values
    messages = state.get("messages", [])

    message_items = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            message_items.append(MessageItem(role="human", content=msg.content))
        elif isinstance(msg, AIMessage):
            message_items.append(MessageItem(role="ai", content=msg.content))

    turn_count = len([m for m in messages if isinstance(m, HumanMessage)])

    return ConversationResponse(
        thread_id=thread_id,
        messages=message_items,
        turn_count=turn_count,
        has_pending_approval=state.get("awaiting_approval", False),
        has_pending_synonym_reuse=state.get("pending_synonym_reuse") is not None,
        has_pending_registrations=(
            state.get("pending_synonym_registrations") is not None
            and len(state.get("pending_synonym_registrations", []) or []) > 0
        ),
    )
