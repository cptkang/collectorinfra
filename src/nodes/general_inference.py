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
from src.llm import USER_RESPONSE_TAG, astream_text, create_llm
from src.routing.domain_config import get_domain_by_id
from src.state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "당신은 인프라 관리 시스템의 AI 어시스턴트입니다. "
    "데이터베이스 조회가 필요하지 않은 질문에 한국어로 친절하게 답변하세요. "
    "에이전트 기능 범위 밖의 요청이라면 무엇을 할 수 있는지 안내해 주세요."
)

# 사용법/능력 문의 시 안내할 지원 메트릭 요약(소스 비의존 공통 도메인).
_SUPPORTED_CAPABILITIES = (
    "- 서버 사양: CPU/코어 수, 메모리 용량, 디스크 용량\n"
    "- 서버 사용량: CPU 사용률(평균/최고), 메모리 사용률, 디스크/파일시스템 사용량\n"
    "- 서버 정보: hostname, IP, gateway, OS/커널 파라미터\n"
    "- 프로세스: 서버에서 동작 중인 프로세스 조회\n"
    "- 모니터링 알람: 현재/이력 알람, 심각도, 발생 시각, 담당자, 알람 통계\n"
    "- 가상화/자산: VM 대수·영역별 분포, IT 자산·라이선스(소스에 따라 상이)"
)


def _build_source_catalog(state: AgentState, app_config: AppConfig) -> str:
    """현재 사용자가 조회 가능한 소스 카탈로그를 텍스트로 조립한다.

    활성 소스(active_db_ids)에 사용자별 접근 제어(allowed_db_ids)를 교집합으로
    적용하고, 각 소스의 표시명·설명을 DB_DOMAINS에서 가져와 안내문 컨텍스트로 만든다.
    DB에 접근하지 않으며(노드 계약 유지), 설정/도메인 정의만 참조한다.

    Args:
        state: 현재 에이전트 상태 (allowed_db_ids 참조)
        app_config: 앱 설정 (활성 소스 목록 참조)

    Returns:
        소스 카탈로그 텍스트. 활성 소스가 없으면 빈 문자열.
    """
    active_ids = app_config.multi_db.get_active_db_ids()
    if not active_ids:
        return ""

    allowed = state.get("allowed_db_ids")
    if allowed:  # None/빈 값이면 전체 허용
        active_ids = [db_id for db_id in active_ids if db_id in allowed]
    if not active_ids:
        return ""

    lines: list[str] = []
    for db_id in active_ids:
        domain = get_domain_by_id(db_id)
        name = domain.display_name if domain else db_id
        desc = (domain.description if domain else "").strip()
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    return "\n".join(lines)


def _build_system_prompt(state: AgentState, app_config: AppConfig) -> str:
    """기본 시스템 프롬프트에 소스 카탈로그·능력 안내 컨텍스트를 그라운딩한다.

    사용자가 "무엇을 할 수 있어?", "사용법", "어떤 소스가 있어?" 등을 물을 때
    실제 활성·허용 소스에 근거해 답하도록 컨텍스트를 주입한다. 일반 인사·개념
    질문에는 영향을 주지 않으며, 안내가 필요할 때만 활용하도록 지시한다.

    Args:
        state: 현재 에이전트 상태
        app_config: 앱 설정

    Returns:
        그라운딩된 시스템 프롬프트
    """
    catalog = _build_source_catalog(state, app_config)
    if not catalog:
        return _SYSTEM_PROMPT

    return (
        _SYSTEM_PROMPT
        + "\n\n[현재 지원 가능한 소스]\n"
        + catalog
        + "\n\n[지원 가능한 조회 유형]\n"
        + _SUPPORTED_CAPABILITIES
        + "\n\n[안내 규칙]\n"
        "- 사용자가 사용법·기능·조회 가능 범위·지원 소스를 물으면, 위 목록에 근거해 "
        "현재 지원 소스와 조회 유형을 자연스럽게 소개하세요. 목록에 없는 소스나 기능은 "
        "있다고 답하지 마세요.\n"
        "- 멀티턴 대화를 지원하므로, 안내 끝에는 \"어떤 것을 확인해 드릴까요?\"처럼 "
        "후속 질문을 유도하는 문장으로 마무리하세요.\n"
        "- 단순 인사·개념 설명 등 안내가 불필요한 질문에는 위 목록을 굳이 나열하지 마세요."
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
    # 사용법/능력 문의에 대비해 활성·허용 소스 카탈로그를 시스템 프롬프트에 그라운딩한다.
    messages = [SystemMessage(content=_build_system_prompt(state, app_config))]

    prior_messages = state.get("messages", [])
    if prior_messages:
        # 이전 대화 이력 포함 (최근 10개로 제한하여 컨텍스트 윈도우 관리)
        for msg in prior_messages[-10:]:
            messages.append(msg)

    messages.append(HumanMessage(content=user_query))

    try:
        # 토큰 단위 SSE 스트리밍(D-009)을 위해 .astream()으로 호출하고,
        # 최종 사용자 응답임을 USER_RESPONSE_TAG로 표시한다(orchestration 경로에서도
        # SQL 생성 등 중간 LLM 토큰과 구분되어 스트리밍됨).
        response_text = await astream_text(llm, messages, tags=[USER_RESPONSE_TAG])
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
