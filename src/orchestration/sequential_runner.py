"""3단(semantic_router)·4단(legacy) 빌드 전용 순차 2-pass 노드 (D-203 · plans/88 §4.7 · E-1).

3·4단은 질의 하나를 SQL 하나로 만들어 *"…를 찾아 그 서버들의 …"* 같은 순차 질의에 순차 개념이
없다. 이 노드는 2단의 부품(`_llm_decompose` → `agent_orchestrator` → `result_aggregator`)을 **함수로
재사용**해 2-pass를 수행한다 — 1단이 2단 모듈을 도구로 쓰는 선례와 같다(`docs/21`: 배선은 배타적,
모듈 의존은 배타적이지 않다). 새 실행 엔진은 없다.

진입 조건(`sequential_entry` — 전부 AND · 결정적)은 그래프 라우팅이 판정한다:
  플래그 on · 순차 표지 · 데이터 조회 의도 · **SQL 승인 플래그 off**(`run_data_query_pipeline`은
  그래프 밖에서 노드 함수를 직접 부르므로 승인 게이트를 거치지 않는다 — 우회 금지) · 폼필 아님.
  (구조 승인 조건은 plans/104에서 게이트째 삭제됐다 — 질의 경로에 구조 승인이 더 이상 없다.)
불성립이면 노드는 등록만 되고 도달하지 않는다(현행 경로 바이트 동일).

분해 결과가 순차(2개 이상 + `input_from` 배선)가 아니면 **단일 task로 실행**하고 `sequential_not_applied`
사유를 남긴다 — 그래프는 이미 이 노드로 분기했으므로 되돌아가지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.intent_planner import _llm_decompose
from src.orchestration.result_aggregator import result_aggregator
from src.state import AgentState
from src.utils.prior_dependency import NOTE_DECOMPOSE, has_sequential_marker

logger = logging.getLogger(__name__)

# 이 노드가 실행할 수 있는 agent — 3·4단에는 다른 핸들러가 라우팅 대상이 아니다.
_RUNNABLE_AGENTS: tuple[str, ...] = ("data_query", "alarm_query")
_DATA_INTENTS: tuple[Any, ...] = (None, "data_query", "alarm_query")


def sequential_entry(state: dict, config: AppConfig) -> bool:
    """`sequential_runner` 진입 판정(plans/88 §4.7). 하나라도 불성립이면 False."""
    composite = getattr(config, "composite", None)
    if not bool(getattr(composite, "sequential_fallback_tiers_enabled", False)):
        return False
    # HITL 우회 금지 — SQL 승인 플래그가 켜져 있으면 진입하지 않는다.
    if bool(getattr(config, "enable_sql_approval", False)):
        return False
    if state.get("routing_intent") not in _DATA_INTENTS:
        return False
    if state.get("template_structure") or state.get("zone_clarification"):
        return False
    return has_sequential_marker(state.get("user_query", ""))


async def sequential_runner(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """분해 → (게이트·대조 포함) 순차 실행 → 집계. 2단 부품 재사용."""
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    user_query = state.get("user_query", "")
    decomposed = await _llm_decompose(
        llm, user_query, app_config, conversation_context=state.get("conversation_context"),
    )
    tasks = [dict(t) for t in (decomposed.get("tasks") or [])]
    notes: list[dict] = list(state.get("dependency_notes") or [])
    for d in decomposed.get("degraded") or []:
        if isinstance(d, dict) and d.get("detail"):
            notes.append({"kind": NOTE_DECOMPOSE, "task_id": None, "reason": d.get("reason"), "detail": d["detail"]})

    sequential = (
        len(tasks) >= 2
        and any(t.get("input_from") for t in tasks)
        and all(t.get("agent") in _RUNNABLE_AGENTS for t in tasks)
    )
    if not sequential:
        reason = "unsupported_agent" if tasks and any(t.get("agent") not in _RUNNABLE_AGENTS for t in tasks) else "sequential_not_applied"
        logger.info("sequential_runner: 순차 분해 미성립(%s) — 단일 조회로 실행", reason)
        tasks = [{
            "task_id": "t1", "agent": "data_query", "sub_query": user_query,
            "depends_on": [], "input_from": [], "order": 1, "status": "pending",
        }]
        if not any(n.get("reason") == "sequential_not_applied" for n in notes):
            notes.append({
                "kind": NOTE_DECOMPOSE, "task_id": None, "reason": reason,
                "detail": "순차 분해가 적용되지 않아 한 번의 조회로 처리했습니다.",
            })

    run_state: dict = {**dict(state), "task_plan": tasks, "task_results": {}, "dependency_notes": notes or None}
    orchestrated = await agent_orchestrator(run_state, llm=llm, app_config=app_config)
    run_state.update(orchestrated)
    if notes and not run_state.get("dependency_notes"):
        run_state["dependency_notes"] = notes

    out = await result_aggregator(run_state, llm=llm, app_config=app_config, synthesize=False)
    out.update({
        "task_plan": run_state["task_plan"],
        "task_results": run_state["task_results"],
        "is_composite": len(run_state["task_plan"]) > 1,
        "current_node": "sequential_runner",
    })
    if run_state.get("dependency_notes") and not out.get("dependency_notes"):
        out["dependency_notes"] = run_state["dependency_notes"]
    return out
