"""결과 기반 재계획 노드 (Plan 49, deepagents TodoListMiddleware 동적 재계획 대응).

1차 실행 결과(task_results)를 LLM으로 평가하여 (a) **종료** 또는 (b) **후속 task 추가**를
결정한다. 후속이 필요하면 신규 task만 task_plan에 증분 추가하고 agent_orchestrator로 되돌린다.

설계 원칙:
- **증분 추가**(R-A1): 신규 task만 append하고 기존 completed task·task_results는 절대 건드리지 않는다.
- **상한 가드**(R-A3): replan_count >= max_replan이면 재계획을 중단하고 현재 결과로 종료한다.
- **보수적 종료**(R-A1/R-A4): LLM 호출·파싱 실패 또는 후속 불필요·빈 task면 needs_replan=False로 종료한다.

본 노드는 tool-calling을 사용하지 않으며, 프롬프트 + JSON 파싱으로만 동작한다.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.llm import create_llm
from src.prompts.replanner import REPLANNER_SYSTEM_TEMPLATE
from src.state import AgentState
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)

# 결과 요약에 포함할 task별 샘플 행 상한 (토큰 폭증 방지)
_MAX_SUMMARY_ROWS = 5

# 텍스트 계열(general/cache/synonym) 결과 요약 상한.
# 너무 짧으면(예: 300자) 사용법+소스+데이터를 모두 담은 긴 안내 답변이 앞부분만 보여
# replanner가 "뒷부분 누락"으로 오판→불필요 재계획을 유발하므로 충분히 크게 둔다.
_MAX_SUMMARY_TEXT_CHARS = 1500


async def replanner(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """1차 실행 결과를 평가하여 후속 task 추가 여부를 결정한다.

    Args:
        state: 현재 에이전트 상태 (task_plan, task_results, replan_count 포함)
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - 종료 시: {"needs_replan": False, "current_node": "replanner"}
        - 추가 시: {"task_plan": 기존+신규, "needs_replan": True,
                    "replan_count": +1, "current_node": "replanner"}
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    replan_count = state.get("replan_count", 0)

    # 처리 현황 표시용 누적 이력 (루프 종료 시에도 보존되도록 항상 그대로 carry forward)
    replan_history = list(state.get("replan_history", []))

    # 상한 가드(R-A3): 재계획 반복 상한 초과 시 현재 결과로 종료
    if replan_count >= app_config.max_replan:
        logger.info("replanner: 재계획 상한(%d) 도달, 현재 결과로 종료", app_config.max_replan)
        return {"needs_replan": False, "replan_history": replan_history, "current_node": "replanner"}

    decision = await _llm_evaluate(
        llm,
        state.get("user_query", ""),
        state.get("task_plan", []),
        state.get("task_results", {}),
        app_config,
    )

    # 보수적 종료(R-A1/R-A4): 후속 불필요·빈 task·파싱 실패 시 루프 종료
    if not decision.get("needs_followup") or not decision.get("new_tasks"):
        logger.debug("replanner: 후속 불필요, 종료 (reason=%s)", decision.get("reason"))
        return {"needs_replan": False, "replan_history": replan_history, "current_node": "replanner"}

    new_tasks = _assign_ids(decision["new_tasks"], existing=state.get("task_plan", []))
    if not new_tasks:
        # 유효 신규 task가 없으면 보수적 종료
        return {"needs_replan": False, "replan_history": replan_history, "current_node": "replanner"}

    # 무의미 재시도 차단(D-049): 엔티티를 이미 찾은(>0행) 조회를 같은 방식으로 다시 묻는
    # 후속을 제거한다. 행은 찾았으나 일부 요청 필드가 null인 것은 "데이터에 그 속성이
    # 없음"이지 쿼리 오류가 아니므로, 재조회해도 채워지지 않고 max_replan까지 헛돈다.
    new_tasks = _filter_futile_retries(
        new_tasks, state.get("task_plan", []), state.get("task_results", {})
    )
    if not new_tasks:
        logger.info(
            "replanner: 엔티티를 이미 확보한 무의미 재시도 전부 제거 → 종료 (reason=%s)",
            decision.get("reason"),
        )
        return {"needs_replan": False, "replan_history": replan_history, "current_node": "replanner"}

    # 중복 재답변 방지(R-A4 강화): 후속이 모두 general_inference(일반 안내)면
    # 데이터 기반 후속이 아니라 같은 주제를 다시 답변하는 패턴이다. general_inference는
    # 자체 완결적 안내 답변(사용법·지원 소스·조회 가능 데이터 등)이므로, 여기에 또
    # general_inference를 붙이면 동일 내용이 중복 출력된다. 추가하지 않고 종료한다.
    if all(t.get("agent") == "general_inference" for t in new_tasks):
        logger.info(
            "replanner: 후속이 모두 general_inference → 중복 재답변 방지로 종료 (reason=%s)",
            decision.get("reason"),
        )
        return {"needs_replan": False, "replan_history": replan_history, "current_node": "replanner"}

    logger.info(
        "replanner: 후속 task %d개 추가 (replan_count=%d, reason=%s)",
        len(new_tasks), replan_count + 1, decision.get("reason"),
    )

    # 처리 현황용 이력 누적 (이번 재계획 회차의 사유·추가 개수)
    replan_history.append({
        "count": replan_count + 1,
        "reason": decision.get("reason") or "",
        "added": len(new_tasks),
    })

    # 증분 추가(R-A1): 기존 task_plan 보존 + 신규만 append (전체 교체 금지)
    return {
        "task_plan": state.get("task_plan", []) + new_tasks,
        "needs_replan": True,
        "replan_count": replan_count + 1,
        "replan_history": replan_history,
        "current_node": "replanner",
    }


async def _llm_evaluate(
    llm: BaseChatModel,
    user_query: str,
    task_plan: list[dict],
    task_results: dict[str, dict],
    app_config: AppConfig,
) -> dict:
    """LLM으로 결과를 평가하여 후속 task 필요 여부를 판단한다.

    REPLANNER_SYSTEM_TEMPLATE로 LLM을 호출하고 JSON을 파싱한다.
    호출·파싱 실패 시 빈 dict를 반환하여 호출부가 보수적으로 종료하도록 한다.

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 원래 질의
        task_plan: 현재까지의 TaskSpec 목록 (status 포함)
        task_results: {task_id: 정규화된 결과}
        app_config: 앱 설정

    Returns:
        {"needs_followup": bool, "reason": str, "new_tasks": [...]} 또는 빈 dict(실패 시)
    """
    try:
        context = _build_eval_context(user_query, task_plan, task_results)
        messages: list[BaseMessage] = [
            SystemMessage(content=REPLANNER_SYSTEM_TEMPLATE)
        ]
        if isinstance(llm, KBGenAIChat):
            messages.append(AIMessage(content=""))
        messages.append(HumanMessage(content=context))

        response = await llm.ainvoke(messages)
        parsed = extract_json_from_response(response.content)
    except Exception as e:
        logger.error("replanner LLM 평가 실패, 보수적 종료: %s", e)
        return {}

    if not isinstance(parsed, dict):
        logger.warning("replanner 평가 결과 무효, 보수적 종료")
        return {}
    return parsed


def _build_eval_context(
    user_query: str,
    task_plan: list[dict],
    task_results: dict[str, dict],
) -> str:
    """LLM 평가용 컨텍스트(원질의 + 완료 task 요약 + 결과 요약)를 구성한다.

    Args:
        user_query: 사용자 원래 질의
        task_plan: 현재까지의 TaskSpec 목록
        task_results: {task_id: 정규화된 결과}

    Returns:
        Human 메시지로 주입할 평가 컨텍스트 문자열
    """
    lines: list[str] = [f"## 사용자 원래 질의\n{user_query}", "", "## 완료된 작업과 결과 요약"]

    ordered = sorted(task_plan, key=lambda t: t.get("order", 0))
    for task in ordered:
        tid = task.get("task_id", "?")
        agent = task.get("agent", "?")
        sub_query = task.get("sub_query", "")
        status = task.get("status", "?")
        res = task_results.get(tid, {})
        lines.append(f"- [{tid}] agent={agent}, status={status}, 지시=\"{sub_query}\"")
        lines.append(f"    결과: {_summarize_result(res)}")

    return "\n".join(lines)


def _summarize_result(res: dict) -> str:
    """단일 task 결과를 평가용 요약 문자열로 압축한다.

    행수와 샘플 행 일부만 노출하여 토큰 폭증을 방지한다(R-A5).

    Args:
        res: 정규화된 task 결과

    Returns:
        요약 문자열 (에러/행수/샘플 포함)
    """
    if res.get("error"):
        return f"실패 (error={res['error']})"

    rows = _extract_rows(res)
    if rows is None:
        # 텍스트 계열(cache/synonym/general) 결과
        text = res.get("final_response")
        if text:
            return text[:_MAX_SUMMARY_TEXT_CHARS]
        return "결과 없음"

    n = len(rows)
    if n == 0:
        return "0건 (조회 결과 없음)"

    sample = rows[:_MAX_SUMMARY_ROWS]
    return f"{n}건 조회. 샘플(최대 {_MAX_SUMMARY_ROWS}건): {sample}"


def _extract_rows(res: dict) -> list[dict] | None:
    """task 결과에서 행 목록을 추출한다 (data_query/alarm_query 계열).

    Args:
        res: 정규화된 task 결과

    Returns:
        행 목록 또는 None(텍스트 계열로 판단되는 경우)
    """
    rows = res.get("query_results")
    if rows is not None:
        return rows
    organized = res.get("organized_data")
    if isinstance(organized, dict):
        return organized.get("rows", [])
    return None


def _assign_ids(new_tasks: list[dict], *, existing: list[dict]) -> list[dict]:
    """신규 task에 기존 task_id와 충돌하지 않는 새 id/order/status를 부여한다.

    기존 task_id 집합과 충돌하지 않도록 기존 최대 t번호+1부터 순차 부여하고,
    order는 기존 최대 order+1부터 부여한다. depends_on/input_from 누락은 []로 보정한다.
    신규 task끼리의 상대 참조(예: 신규 두 번째가 첫 번째를 참조)는 임시 task_id를
    실제 부여된 id로 재매핑하여 허용한다.

    Args:
        new_tasks: LLM이 생성한 신규 task 목록 (task_id/order 없음 가정)
        existing: 기존 task_plan (충돌 방지·order 기준점)

    Returns:
        task_id/order/status가 부여된 신규 task 목록
    """
    existing_ids = {t.get("task_id") for t in existing if t.get("task_id")}

    # 기존 t번호 최대값 → 신규 id 시작점
    max_num = 0
    for tid in existing_ids:
        if isinstance(tid, str) and tid.startswith("t") and tid[1:].isdigit():
            max_num = max(max_num, int(tid[1:]))

    # 기존 order 최대값 → 신규 order 시작점
    max_order = max((t.get("order", 0) for t in existing), default=0)

    # 신규 task의 임시 참조(예: LLM이 부여한 task_id)를 실제 id로 매핑
    id_map: dict[str, str] = {}
    assigned: list[dict] = []
    for i, raw in enumerate(new_tasks):
        if not isinstance(raw, dict):
            continue
        new_id = f"t{max_num + 1 + len(assigned)}"
        old_ref = raw.get("task_id")
        if old_ref:
            id_map[old_ref] = new_id

        task: dict[str, Any] = {
            "task_id": new_id,
            "agent": raw.get("agent", "data_query"),
            "sub_query": raw.get("sub_query", ""),
            "depends_on": list(raw.get("depends_on") or []),
            "input_from": list(raw.get("input_from") or []),
            # 대체(재조회) 관계: 이 후속이 대체하는 선행 task_id 목록 (없으면 빈 배열).
            # result_aggregator가 최종 답변 본문에서 대체된 선행 task 서술을 숨긴다(D-043).
            "supersedes": list(raw.get("supersedes") or []),
            "order": max_order + 1 + len(assigned),
            "status": "pending",
        }
        assigned.append(task)

    # 신규 task끼리의 상대 참조(임시 id)를 실제 id로 재매핑
    if id_map:
        for task in assigned:
            task["depends_on"] = [id_map.get(d, d) for d in task["depends_on"]]
            task["input_from"] = [id_map.get(d, d) for d in task["input_from"]]
            task["supersedes"] = [id_map.get(d, d) for d in task["supersedes"]]

    return assigned


def _filter_futile_retries(
    new_tasks: list[dict],
    existing: list[dict],
    task_results: dict[str, dict],
) -> list[dict]:
    """엔티티를 이미 찾은(>0행) 조회를 같은 방식으로 다시 묻는 무의미 재시도를 제거한다 (D-049).

    `replanner`의 LLM은 "행은 찾았으나 일부 요청 필드가 null"인 결과를 "0건/불충분 → 재조회"
    예시에 잘못 적용해, 데이터에 애초에 없는 속성을 채우려고 max_replan까지 헛 재시도한다.
    엔티티가 이미 확보되었으면(선행이 >0행 반환) 같은 의도 재조회로는 누락 필드가 채워질 수
    없으므로 결정적으로 차단한다. supersedes 필드 누락(LLM 비준수)에도 견고하도록 **행수 +
    독립성**으로 판정한다.

    제거 규칙 — 신규 task가 다음 중 하나면 제거:
    - (a) 그 `supersedes`가 **>0행을 반환한** 선행 task_id를 가리킨다(명시적 재조회).
    - (b) **독립**(`depends_on` 비어있음)이면서 **같은 agent**의 선행이 이미 >0행을 반환했다
      (LLM이 supersedes를 빠뜨린 암묵적 재조회).

    보존 — 정당한 후속은 그대로 둔다:
    - 선행이 0건이면(엔티티 못 찾음) "0건 → 재조회"는 (a)/(b) 모두 미해당 → 허용.
    - 데이터 의존 보강(`depends_on` 존재, 예: 장애 서버 → 알람 이력)은 (b) 미해당 → 허용.
    - 다른 agent의 보강(data_query 행 발견 → alarm_query)은 (b) 미해당 → 허용.

    Args:
        new_tasks: _assign_ids로 id/supersedes가 부여된 신규 task 목록
        existing: 기존 task_plan(완료 task 포함 — 행수 판정 기준)
        task_results: {task_id: 정규화된 결과}

    Returns:
        무의미 재시도를 제외한 신규 task 목록
    """
    # >0행을 반환한 완료 선행의 task_id 집합과 agent 집합
    rows_ids: set[str] = set()
    rows_agents: set[str] = set()
    for t in existing:
        if _extract_rows(task_results.get(t.get("task_id"), {})):
            rows_ids.add(t.get("task_id"))
            if t.get("agent"):
                rows_agents.add(t["agent"])

    if not rows_ids:
        # 아직 어떤 선행도 데이터를 못 찾음 → 재조회 가치가 있으므로 전부 보존
        return new_tasks

    kept: list[dict] = []
    for t in new_tasks:
        supersedes = t.get("supersedes") or []
        # (a) 데이터를 이미 찾은 선행을 명시적으로 재조회
        if any(sid in rows_ids for sid in supersedes):
            logger.info(
                "replanner: 무의미 재시도 제거(명시적 supersedes %s, 선행 이미 데이터 확보)",
                supersedes,
            )
            continue
        # (b) 독립 재조회인데 같은 agent가 이미 데이터를 찾음
        if not t.get("depends_on") and t.get("agent") in rows_agents:
            logger.info(
                "replanner: 무의미 재시도 제거(독립 %s 재조회, 같은 agent 이미 데이터 확보)",
                t.get("agent"),
            )
            continue
        kept.append(t)

    return kept
