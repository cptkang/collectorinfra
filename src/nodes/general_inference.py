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
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.config import AppConfig, load_config
from src.llm import USER_RESPONSE_TAG, astream_text, create_llm
from src.routing.domain_config import get_domain_by_id
from src.routing.registry import get_registry
from src.state import AgentState

logger = logging.getLogger(__name__)


def _location_vocab() -> str:
    """안내 프롬프트에 노출할 위치 표면어 나열을 레지스트리에서 렌더한다(Plan 67 R2).

    신규 DB 편입 시 위치 어휘가 자동 반영되도록 사본을 두지 않는다. 뒤따르는 예시
    문장은 위치↔환경 조합(예: "김포 운영"/"여의도 개발")까지 담아야 하는데 레지스트리가
    그 짝을 모델링하지 않으므로 예시는 그대로 둔다.
    """
    return "/".join(get_registry().location_terms())

_SYSTEM_PROMPT = (
    "당신은 인프라 관리 시스템의 AI 어시스턴트입니다. "
    "데이터베이스 조회가 필요하지 않은 질문에 한국어로 친절하게 답변하세요. "
    "에이전트 기능 범위 밖의 요청이라면 무엇을 할 수 있는지 안내해 주세요."
)

# 후속 턴 판단/분석 요청(rightsizing 권고 등) 처리 지침(④/D-055 정합 — 긍정 지시).
# 관측 증상: 직전 턴 조회 결과가 대화 이력·맥락으로 전달돼도 "구체적 데이터가 확보되지
# 않았다"며 판단을 통째로 거부(환각). 이전 대화의 조회 결과를 근거로 판단을 제시하도록 강제한다.
_JUDGMENT_GUIDANCE = (
    "\n\n[이전 결과 기반 분석·판단 요청 처리]\n"
    "- 사용자가 앞선 대화에서 조회된 결과(서버 사양·프로세스·CPU/메모리/디스크 사용량·알람 등)를 "
    "근거로 분석·판단·권고(예: rightsizing 적정성)를 요청하면, **위 대화 이력과 아래 이전 대화 맥락에 "
    "담긴 실제 값을 근거로** 판단을 제시하세요.\n"
    "- 대화에 이미 제공된 데이터가 있으면 \"데이터가 확보되지 않았다\"고 단정하여 판단을 거부하지 마세요. "
    "먼저 확보된 데이터로 판단 가능한 범위를 답하고, 정밀 판단에 **추가로 필요한 데이터만** 구체적으로 "
    "짚어 주세요.\n"
    "- **추가 조회를 제안할 때는 반드시 위 [지원 가능한 조회 유형] 목록 안의 항목만 제시하세요.** "
    "그 목록에 없는 항목 — DB 엔진 내부 설정·MySQL/InnoDB/버퍼풀 등 애플리케이션 내부 파라미터, "
    "OS·미들웨어 튜닝 가이드, 임의의 IT 개념 — 은 **예시로도 제안하지 말고**, 지원하지 않는 기능을 "
    "있는 것처럼 안내하지 마세요. 제안 예시는 목록에 있는 표현(CPU/메모리/디스크 사용률, 프로세스, "
    "알람 등)을 그대로 사용하세요.\n"
    "- 근거로 삼은 수치를 답변에 명시하고, 데이터가 직전 조회 시점 기준임을 필요 시 밝혀 주세요."
)

# 사용법/능력 문의 시 안내할 지원 메트릭 요약(소스 비의존 공통 도메인).
_SUPPORTED_CAPABILITIES = (
    "- 서버 사양: CPU/코어 수, 메모리 용량, 디스크 용량\n"
    "- 서버 사용량: CPU 사용률(평균/최고), 메모리 사용률, 디스크/파일시스템 사용량\n"
    "- 서버 정보: hostname, IP, gateway, OS/커널 파라미터\n"
    "- 프로세스: 서버에서 동작 중인 프로세스 조회\n"
    "- 모니터링 알람: 현재/이력 알람, 심각도, 발생 시각, 담당자, 알람 통계\n"
    "- 양식 채우기: Excel(.xlsx) 양식 파일을 첨부하고 대상 소스를 지정하면 수집 항목을 "
    "자동으로 채움. 채우지 못한 항목은 선택 패널로 되물으며(공란 유지/DB 항목 선택/직접 "
    "입력), 패널에서 '이 답을 기억'을 선택하면 같은 양식에 자동 반영(일정 기간 후 자동 "
    "만료, 사용 시 연장)\n"
    "- 양식 기억 관리: 양식 파일을 첨부하고 \"이 양식에 기억된 답 보여줘\" / "
    "\"<필드명> 기억 삭제\" / \"기억 전부 삭제\"로 조회·삭제\n"
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


def _build_context_grounding(state: AgentState) -> str:
    """직전 턴 압축 맥락(conversation_context)을 판단 근거 블록으로 조립한다 (④).

    orchestration 경로에서 general_inference는 messages(대화 이력, ①)와 함께 압축 신호
    (위치/DB/서버 식별자/직전 결과 요약)를 받는다. 대화 이력만으로 식별자·대상 DB가
    분명치 않을 때 이 블록이 판단의 그라운딩을 보강한다. 첫 턴(맥락 없음/turn_count<=1)이면
    빈 문자열을 반환한다(회귀 방지).

    Args:
        state: 현재 에이전트 상태 (conversation_context 참조)

    Returns:
        판단 근거 그라운딩 텍스트 블록. 첫 턴이면 빈 문자열.
    """
    ctx = state.get("conversation_context")
    if not ctx or ctx.get("turn_count", 0) <= 1:
        return ""

    location = ctx.get("previous_location") or ""
    db_ids = ctx.get("previous_db_ids") or []
    entities = ctx.get("previous_entities") or []
    summary = ctx.get("previous_results_summary") or ""

    entity_strs: list[str] = []
    seen: set[str] = set()
    for e in entities[:10]:
        if not isinstance(e, dict):
            continue
        token = f"{e.get('field', '')}={e.get('value', '')}"
        if e.get("value") not in (None, "") and token not in seen:
            seen.add(token)
            entity_strs.append(token)

    lines = [
        "\n\n[이전 대화 맥락 (후속 턴 판단 근거)]",
        f"- 직전 대상 위치/환경: {location or '(미상)'}",
        f"- 직전 대상 DB: {', '.join(db_ids) if db_ids else '(미상)'}",
        f"- 직전 대상 서버/장비: {', '.join(entity_strs) if entity_strs else '(없음)'}",
        f"- 직전 조회 요약: {summary or '(없음)'}",
        "위 대상과 직전 조회 결과는 **바로 앞 대화 메시지**에 상세 내용이 있습니다. 이를 근거로 답하세요.",
    ]
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
    # 후속 턴이면 판단 근거 그라운딩 + 판단 지침을 덧붙인다(④). 첫 턴이면 빈 문자열.
    grounding = _build_context_grounding(state)
    addendum = (grounding + _JUDGMENT_GUIDANCE) if grounding else ""

    catalog = _build_source_catalog(state, app_config)
    if not catalog:
        # 소스 카탈로그가 없어도 판단/제안(addendum)이 붙는 후속 턴이면 지원 역량 목록을 함께
        # 실어, 제안 범위 제약(_JUDGMENT_GUIDANCE의 "[지원 가능한 조회 유형] 안에서만")이 참조할
        # 기준을 제공한다(미지원 기능 광고 차단).
        base = _SYSTEM_PROMPT
        if addendum:
            base += "\n\n[지원 가능한 조회 유형]\n" + _SUPPORTED_CAPABILITIES
        return base + addendum

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
        f"- **예시 질의를 제시할 때 위치({_location_vocab()} 등)는 데이터가 '등록된 소스'"
        "(폴스타 인스턴스)를 가리킵니다.** 위치를 서버 이름의 일부처럼 붙이지 말고, "
        "소스를 지목하는 형태로 표현하세요. "
        "(권장: \"여의도 폴스타에 등록된 서버의 …\", \"김포 운영 폴스타의 …\", \"여의도의 …\" / "
        "지양: \"여의도 개발 서버의 …\" — 위치가 서버명인지 소스인지 혼동됩니다.)\n"
        "- 예시 질의는 아래처럼 '소스(위치) + 대상/항목' 형태로 제시하세요.\n"
        "  · \"여의도 폴스타에 등록된 서버의 CPU 코어 수와 메모리 용량을 알려줘\"\n"
        "  · \"김포 운영 폴스타의 디스크 사용량을 조회해줘\"\n"
        "  · \"은행 폴스타의 현재 알람 현황을 보여줘\"\n"
        "- 멀티턴 대화를 지원하므로, 안내 끝에는 \"어떤 것을 확인해 드릴까요?\"처럼 "
        "후속 질문을 유도하는 문장으로 마무리하세요.\n"
        "- **후속 조회를 제안(예시 포함)할 때는 반드시 위 [지원 가능한 조회 유형] 목록 안의 "
        "항목만 사용하세요.** 목록에 없는 항목(DB 엔진 내부 설정·MySQL/InnoDB/버퍼풀 등 "
        "애플리케이션 내부 파라미터, OS·미들웨어 튜닝값, 임의 IT 개념)은 예시로도 제안하지 말고, "
        "지원하지 않는 기능을 있는 것처럼 말하지 마세요.\n"
        "- 단순 인사·개념 설명 등 안내가 불필요한 질문에는 위 목록을 굳이 나열하지 마세요."
        + addendum
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
        # 최종 사용자 응답(USER_RESPONSE_TAG) — answer 프로파일(D-194)
        llm = create_llm(app_config, purpose="answer")

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

    result: dict = {
        "final_response": response_text,
        "routing_intent": "general_inference",
        "current_node": "general_inference",
    }
    # 직접(semantic) 경로에서 general_inference가 그래프 종료 노드일 때 답변을 대화 이력에
    # 누적한다(멀티턴 후속 판단용, ②). orchestration 경로에서는 subagent로 실행되어 반환
    # messages가 top-level로 승격되지 않으므로(result_aggregator가 별도 누적) 이중 누적이 없다.
    if response_text and response_text.strip():
        result["messages"] = [AIMessage(content=response_text)]
    return result
