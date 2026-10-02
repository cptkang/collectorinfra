"""대화 세션 관리 라우트.

멀티턴 대화 세션의 히스토리 조회, 삭제를 지원한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from src.api.dependencies import require_user
from src.api.thread_history import history_owner

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


# ─── 질의응답 스레드 이력 (D-248) ───
#
# 위 /conversation/{thread_id}는 체크포인트의 실행 상태(노드 중간 메시지 포함)를 읽는다.
# 아래는 사용자 화면이 남긴 턴 기록(`query_thread_turns`)을 읽는다 — 질의 이력 사이드바의 정본.
# 모든 조회·삭제는 소유 키(history_owner)를 조건으로 건다.

_THREAD_STORE_UNAVAILABLE = "대화 이력 저장소를 사용할 수 없습니다."


def _thread_scope(
    request: Request, current_user: dict[str, Any]
) -> tuple[Any, str | None]:
    repo = getattr(request.app.state, "thread_repo", None)
    owner = history_owner(request, current_user) if repo else None
    return repo, owner


@router.get("/threads")
async def list_threads(
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """내 대화 목록(최근 순). 저장소나 소유 키가 없으면 사유와 함께 빈 목록을 준다."""
    repo, owner = _thread_scope(request, current_user)
    if repo is None:
        return {"available": False, "reason": "no_store", "threads": []}
    if owner is None:
        return {"available": False, "reason": "no_owner", "threads": []}
    try:
        threads = await repo.list_threads(owner)
    except Exception as e:
        logger.error("대화 목록 조회 실패: %s", e)
        raise HTTPException(status_code=500, detail="대화 목록을 불러오지 못했습니다.")
    return {"available": True, "reason": None, "threads": threads}


@router.get("/threads/{thread_id}")
async def get_thread(
    request: Request,
    thread_id: str,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """대화 하나의 턴 목록. 남의 대화는 없는 대화와 같게 404다."""
    repo, owner = _thread_scope(request, current_user)
    if repo is None or owner is None:
        raise HTTPException(status_code=503, detail=_THREAD_STORE_UNAVAILABLE)
    try:
        turns = await repo.get_turns(owner, thread_id)
    except Exception as e:
        logger.error("대화 조회 실패: %s", e)
        raise HTTPException(status_code=500, detail="대화를 불러오지 못했습니다.")
    if not turns:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
    return {"thread_id": thread_id, "turns": turns}


@router.delete("/threads/{thread_id}")
async def delete_thread(
    request: Request,
    thread_id: str,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    repo, owner = _thread_scope(request, current_user)
    if repo is None or owner is None:
        raise HTTPException(status_code=503, detail=_THREAD_STORE_UNAVAILABLE)
    return {"deleted": await repo.delete_thread(owner, thread_id)}


@router.delete("/threads")
async def delete_all_threads(
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    repo, owner = _thread_scope(request, current_user)
    if repo is None or owner is None:
        raise HTTPException(status_code=503, detail=_THREAD_STORE_UNAVAILABLE)
    return {"deleted": await repo.delete_all(owner)}
