"""구조 분석 승인 게이트 노드.

schema_analyzer 이후, LLM 구조 분석 결과에 대한 사용자 승인을 처리한다.
LangGraph의 interrupt_before를 활용하여 그래프 실행을 중단하고,
사용자 응답 후 재개한다.
"""

from __future__ import annotations

import logging

from src.state import AgentState

logger = logging.getLogger(__name__)


async def structure_approval_gate(state: AgentState) -> dict:
    """구조 분석 승인 게이트.

    interrupt_before에 의해 이 노드 진입 시 그래프가 중단된다.
    재개 시 approval_action을 읽어 분기한다.

    - action=None: 첫 진입, 승인 요청 상태 유지 (schema_analyzer에서 이미 설정됨)
    - action="approve": 승인, schema_analyzer로 재진입하여 캐시 저장 후 계속
    - action="reject": 거부, 구조 메타 없이 query_generator로 진행

    Args:
        state: 현재 에이전트 상태

    Returns:
        업데이트할 State 필드
    """
    action = state.get("approval_action")
    approval_ctx = state.get("approval_context", {})

    if action is None:
        # 첫 진입: 승인 요청 상태 유지 (schema_analyzer에서 이미 설정됨)
        summary = approval_ctx.get(
            "summary", "DB 구조 분석 결과를 확인해주세요."
        )
        logger.info("structure_approval_gate: 구조 분석 승인 요청")
        return {
            "final_response": summary,
            "current_node": "structure_approval_gate",
        }

    elif action == "approve":
        logger.info("structure_approval_gate: 구조 분석 승인됨")
        # approval_context는 유지하여 schema_analyzer 재진입 시 사용
        return {
            "current_node": "structure_approval_gate",
        }

    elif action == "reject":
        logger.info("structure_approval_gate: 구조 분석 거부됨")
        return {
            "awaiting_approval": False,
            "approval_context": None,
            "approval_action": None,
            "current_node": "structure_approval_gate",
        }

    else:
        logger.warning(
            "structure_approval_gate: 알 수 없는 action=%s", action
        )
        return {
            "awaiting_approval": False,
            "approval_context": None,
            "approval_action": None,
            "current_node": "structure_approval_gate",
        }
