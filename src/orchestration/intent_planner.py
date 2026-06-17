"""의도 분해 노드 (Plan 48, deepagents `write_todos` 대응).

사용자 질의를 sub-task 목록(`task_plan`)으로 분해한다. 단일 작업이면 task 1개만 생성한다.

처리 단계:
- [계층 A] deterministic pre-check (LLM 스킵): 기존 semantic_router 우선순위 ①~③를 이식.
  pending_synonym_reuse / synonym_registration / mapped_db_ids 가 있으면 단일 task로 즉시 반환.
- [계층 B] LLM 복합 분해: INTENT_PLANNER_SYSTEM_TEMPLATE로 분해 + 각 task agent 분류.
  실패/빈 결과면 단일 data_query task로 폴백한다.

본 노드는 tool-calling을 사용하지 않으며, 프롬프트 + JSON 파싱으로만 동작한다.
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.llm import create_llm
from src.prompts.intent_planner import INTENT_PLANNER_SYSTEM_TEMPLATE
from src.state import AgentState
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)


async def intent_planner(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """사용자 질의를 sub-task 목록으로 분해한다.

    계층 A pre-check(멀티턴 pending 결합 보존)를 먼저 수행하고, 해당하지 않으면
    계층 B LLM 분해를 수행한다.

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - task_plan: TaskSpec 목록 (각 항목 status="pending")
        - is_composite: task 2개 이상 여부
        - current_node: "intent_planner"
        - (계층 B에서 모호성 방출 시) clarification_needed 보존
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    user_query = state["user_query"]

    # [계층 A] deterministic pre-check — semantic_router 우선순위 ①~③ 이식
    # ① pending_synonym_reuse → cache_management 강제
    if state.get("pending_synonym_reuse"):
        logger.info("intent_planner: pending_synonym_reuse 감지, cache_management 단일 task")
        return _single_task_plan("cache_management", user_query)

    # ② 명시적 유사어 등록 요청 (멀티턴 두 번째 요청)
    parsed = state.get("parsed_requirements", {})
    if parsed.get("synonym_registration") and state.get("pending_synonym_registrations"):
        logger.info("intent_planner: 유사어 등록 요청 감지, synonym_registration 단일 task")
        return _single_task_plan("synonym_registration", user_query)

    # ③ field_mapper가 이미 대상 DB를 결정한 경우 (양식 업로드 시)
    mapped_db_ids = state.get("mapped_db_ids")
    if mapped_db_ids:
        logger.info("intent_planner: mapped_db_ids 감지, data_query 단일 task (DB 고정=%s)", mapped_db_ids)
        return _single_task_plan("data_query", user_query, db_ids=mapped_db_ids)

    # [계층 B] LLM 복합 분해
    decomposed = await _llm_decompose(llm, user_query, app_config)
    tasks = decomposed["tasks"]
    result: dict = {
        "task_plan": tasks,
        "is_composite": len(tasks) > 1,
        "current_node": "intent_planner",
    }
    # 모호성 방출 시 보존 (Phase 1은 인터럽트 없이 tasks로 진행 — §4.11)
    clarification = decomposed.get("clarification_needed")
    if clarification:
        result["clarification_needed"] = clarification
    return result


def _single_task_plan(
    agent: str,
    query: str,
    *,
    db_ids: Optional[list[str]] = None,
) -> dict:
    """단일 task 계획을 생성한다 (계층 A pre-check 및 폴백용).

    Args:
        agent: 담당 agent 명
        query: sub_query로 사용할 질의
        db_ids: data_query 고정 DB 목록 (선택, 양식 업로드 시)

    Returns:
        task_plan/is_composite/current_node를 포함한 State 갱신 dict
    """
    task: dict = {
        "task_id": "t1",
        "agent": agent,
        "sub_query": query,
        "depends_on": [],
        "input_from": [],
        "order": 1,
        "status": "pending",
    }
    if db_ids:
        task["db_ids"] = db_ids
    return {
        "task_plan": [task],
        "is_composite": False,
        "current_node": "intent_planner",
    }


async def _llm_decompose(
    llm: BaseChatModel,
    user_query: str,
    app_config: AppConfig,
) -> dict:
    """LLM으로 질의를 sub-task 목록으로 분해한다.

    INTENT_PLANNER_SYSTEM_TEMPLATE로 LLM을 호출하고 JSON을 파싱한다.
    실패/빈 결과면 단일 data_query task로 폴백한다.

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 질의
        app_config: 앱 설정

    Returns:
        {"tasks": [...], "clarification_needed": {...} | None}
        (각 task에 누락 키가 보정되고 status="pending"이 부여됨)
    """
    fallback = {
        "tasks": [
            {
                "task_id": "t1",
                "agent": "data_query",
                "sub_query": user_query,
                "depends_on": [],
                "input_from": [],
                "order": 1,
                "status": "pending",
            }
        ],
        "clarification_needed": None,
    }

    try:
        messages: list[BaseMessage] = [
            SystemMessage(content=INTENT_PLANNER_SYSTEM_TEMPLATE)
        ]
        if isinstance(llm, KBGenAIChat):
            messages.append(AIMessage(content=""))
        messages.append(HumanMessage(content=user_query))

        response = await llm.ainvoke(messages)
        parsed = extract_json_from_response(response.content)
    except Exception as e:
        logger.error("intent_planner LLM 분해 실패, 단일 data_query 폴백: %s", e)
        return fallback

    if not parsed or not isinstance(parsed.get("tasks"), list) or not parsed["tasks"]:
        logger.warning("intent_planner 분해 결과 없음/무효, 단일 data_query 폴백")
        return fallback

    tasks: list[dict] = []
    for i, raw in enumerate(parsed["tasks"], 1):
        if not isinstance(raw, dict):
            continue
        agent = raw.get("agent", "data_query")
        task: dict = {
            "task_id": raw.get("task_id", f"t{i}"),
            "agent": agent,
            "sub_query": raw.get("sub_query", user_query),
            "depends_on": raw.get("depends_on") or [],
            "input_from": raw.get("input_from") or [],
            "order": raw.get("order", i),
            "status": "pending",
        }
        tasks.append(task)

    if not tasks:
        logger.warning("intent_planner 유효 task 없음, 단일 data_query 폴백")
        return fallback

    return {
        "tasks": tasks,
        "clarification_needed": parsed.get("clarification_needed"),
    }
