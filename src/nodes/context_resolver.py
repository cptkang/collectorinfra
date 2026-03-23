"""대화 컨텍스트 해석 노드.

멀티턴 대화에서 이전 대화 맥락을 분석하고,
현재 질의에 필요한 컨텍스트를 추출한다.
첫 번째 노드로 실행된다.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage

from src.config import AppConfig
from src.state import AgentState

logger = logging.getLogger(__name__)

# 대화 히스토리 최대 턴 수 (Human + AI 쌍)
MAX_HISTORY_TURNS = 10


async def context_resolver(
    state: AgentState,
    *,
    app_config: AppConfig | None = None,
) -> dict:
    """이전 대화 맥락을 분석하고 현재 질의에 필요한 컨텍스트를 추출한다.

    첫 턴이면 맥락 없이 통과하고,
    후속 턴이면 이전 SQL, 결과, 테이블, pending 상태를 요약한다.

    Args:
        state: 현재 에이전트 상태
        app_config: 앱 설정

    Returns:
        업데이트할 State 필드:
        - conversation_context: 이전 대화 맥락 딕셔너리 (첫 턴이면 None)
        - messages: 트리밍된 메시지 (MAX_HISTORY_TURNS 초과 시)
        - current_node: "context_resolver"
    """
    messages = state.get("messages", [])
    turn_count = len([m for m in messages if isinstance(m, HumanMessage)])

    # 첫 턴이면 맥락 없음
    if turn_count <= 1:
        result: dict = {
            "conversation_context": None,
            "current_node": "context_resolver",
        }
        # 대화 히스토리 트리밍
        trimmed = _trim_messages(messages)
        if len(trimmed) < len(messages):
            result["messages"] = trimmed
        return result

    # 후속 턴: 이전 대화에서 맥락 추출
    previous_sql = state.get("generated_sql", "")
    previous_results = state.get("query_results", [])
    previous_tables = state.get("relevant_tables", [])
    previous_db_id = state.get("active_db_id")

    # pending 상태 감지
    pending_reuse = state.get("pending_synonym_reuse")
    pending_regs = state.get("pending_synonym_registrations")

    # 이전 결과 요약 (LLM 호출 없이 간단 요약)
    results_summary = ""
    if previous_results:
        results_summary = f"{len(previous_results)}건 조회됨"
        if len(previous_results) > 0:
            cols = list(previous_results[0].keys())
            results_summary += f", 컬럼: {', '.join(cols[:5])}"

    context = {
        "previous_sql": previous_sql,
        "previous_results_summary": results_summary,
        "previous_result_count": len(previous_results),
        "previous_tables": previous_tables,
        "previous_db_id": previous_db_id,
        "turn_count": turn_count,
        "has_pending_synonym_reuse": pending_reuse is not None,
        "has_pending_synonym_registrations": (
            pending_regs is not None and len(pending_regs or []) > 0
        ),
        "pending_synonym_reg_count": len(pending_regs) if pending_regs else 0,
    }

    logger.info(
        "context_resolver: turn=%d, prev_sql=%s, prev_results=%d, pending_reuse=%s, pending_regs=%d",
        turn_count,
        bool(previous_sql),
        len(previous_results),
        bool(pending_reuse),
        len(pending_regs) if pending_regs else 0,
    )

    result = {
        "conversation_context": context,
        "current_node": "context_resolver",
    }

    # 대화 히스토리 트리밍
    trimmed = _trim_messages(messages)
    if len(trimmed) < len(messages):
        result["messages"] = trimmed

    return result


def _trim_messages(messages: list) -> list:
    """대화 히스토리를 최대 턴 수로 제한한다.

    Args:
        messages: 전체 메시지 목록

    Returns:
        트리밍된 메시지 목록
    """
    max_messages = MAX_HISTORY_TURNS * 2
    if len(messages) <= max_messages:
        return messages
    return messages[-max_messages:]
