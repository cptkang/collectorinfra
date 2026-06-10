"""일반 추론 노드.

시멘틱 라우터에서 general_inference 의도로 분류된 요청을 처리한다.
DB 접근 없이 LLM이 직접 응답을 생성한다.

대상 요청:
- IT/인프라 개념 설명 (쿠버네티스란?, RAID 차이 등)
- 에이전트 기능 문의 (뭘 할 수 있어? 등)
- DB 조회 불필요한 범위 외 요청
- 인사말, 단순 확인 등 모호/불완전 질의
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "당신은 인프라 관리 시스템의 AI 어시스턴트입니다. "
    "데이터베이스 조회가 필요하지 않은 질문에 한국어로 친절하게 답변하세요. "
    "에이전트 기능 범위 밖의 요청이라면 무엇을 할 수 있는지 안내해 주세요."
)


async def general_inference(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """DB 접근 없이 LLM에 직접 질의하여 응답을 반환한다.

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - final_response: LLM 응답 텍스트
        - routing_intent: "general_inference"
        - current_node: "general_inference"
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    user_query = state["user_query"]

    # 멀티턴 대화 컨텍스트를 참조하여 메시지 구성
    messages = [SystemMessage(content=_SYSTEM_PROMPT)]

    prior_messages = state.get("messages", [])
    if prior_messages:
        # 이전 대화 이력 포함 (최근 10개로 제한하여 컨텍스트 윈도우 관리)
        for msg in prior_messages[-10:]:
            messages.append(msg)

    messages.append(HumanMessage(content=user_query))

    try:
        response = await llm.ainvoke(messages)
        response_text = response.content
    except Exception as e:
        logger.error("general_inference LLM 호출 실패: %s", e)
        response_text = (
            "죄송합니다. 요청을 처리하는 중 오류가 발생했습니다. "
            "다시 시도해 주시거나 다른 방식으로 질문해 주세요."
        )

    logger.info("general_inference 응답 생성 완료 (query=%r)", user_query[:50])

    return {
        "final_response": response_text,
        "routing_intent": "general_inference",
        "current_node": "general_inference",
    }
