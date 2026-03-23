"""SQL 승인 게이트 노드.

query_validator 이후, query_executor 이전에 사용자 승인을 요청한다.
LangGraph의 interrupt_before를 활용하여 그래프 실행을 중단하고,
사용자 응답 후 재개한다.
"""

from __future__ import annotations

import logging

from src.state import AgentState

logger = logging.getLogger(__name__)


async def approval_gate(state: AgentState) -> dict:
    """SQL 승인 게이트.

    interrupt_before에 의해 이 노드 진입 시 그래프가 중단된다.
    재개 시 approval_action을 읽어 분기한다.

    - action=None: 첫 진입, 승인 요청 상태 설정
    - action="approve": 승인, query_executor로 진행
    - action="reject": 거부, 종료
    - action="modify": SQL 수정, query_validator로 재검증

    Args:
        state: 현재 에이전트 상태

    Returns:
        업데이트할 State 필드
    """
    action = state.get("approval_action")

    if action is None:
        # 첫 진입: 승인 요청 상태 설정
        sql = state.get("generated_sql", "")
        logger.info("approval_gate: SQL 승인 요청 - %s...", sql[:80])
        return {
            "awaiting_approval": True,
            "approval_context": {
                "type": "sql_approval",
                "sql": sql,
                "validation_result": state.get("validation_result"),
            },
            "final_response": (
                f"다음 SQL을 실행하시겠습니까?\n\n"
                f"```sql\n{sql}\n```\n\n"
                f'- 승인: "실행" 또는 "approve"\n'
                f'- 거부: "취소" 또는 "reject"\n'
                f"- 수정: 수정된 SQL을 직접 입력"
            ),
            "current_node": "approval_gate",
        }

    elif action == "approve":
        logger.info("approval_gate: SQL 승인됨")
        return {
            "awaiting_approval": False,
            "approval_context": None,
            "approval_action": None,
            "current_node": "approval_gate",
        }

    elif action == "reject":
        logger.info("approval_gate: SQL 거부됨")
        return {
            "awaiting_approval": False,
            "approval_context": None,
            "approval_action": None,
            "final_response": "쿼리 실행이 취소되었습니다.",
            "current_node": "approval_gate",
        }

    elif action == "modify":
        modified_sql = state.get("approval_modified_sql", "")
        logger.info("approval_gate: SQL 수정됨 - %s...", modified_sql[:80])
        return {
            "awaiting_approval": False,
            "approval_context": None,
            "approval_action": None,
            "generated_sql": modified_sql,
            "current_node": "approval_gate",
        }

    else:
        logger.warning("approval_gate: 알 수 없는 action=%s", action)
        return {
            "awaiting_approval": False,
            "approval_context": None,
            "approval_action": None,
            "final_response": f"알 수 없는 승인 응답입니다: {action}",
            "current_node": "approval_gate",
        }
