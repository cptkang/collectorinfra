"""사다리 3단 계획 루프 노드 (plans/103 P1-1·P2-1~P2-3 · plans/111 C-4·C-5).

플래그 `TIER3_PLAN_LOOP_ENABLED`(기본 off) 뒤에 둔다.

```
semantic_router ─(계획 필요)→ plan → normalize → dispatch ─(Send)→ task_run → join(defer)
                                                   ↑                                 │
                                                   └────── replan(상한) ←────────────┤
                                                                                     └→ finalize
task_run 서브그래프: task_prompt → [schema_analyzer → query_generator ⇄ query_validator
                     → query_executor → result_organizer]
                     | [multi_db_executor → result_merger → result_organizer]
                     | task_handler(그 외 담당) → pack_outcome
```

**새 판단 로직은 없다** — 2단 부품을 노드로 옮긴 것이다(D-053 사본 금지 · 103 §3.3):
계획 본체 `_plan_turn` · 단일 출구 교정 `_normalize_plan_exit`·`_coerce_host_inspect_intent` ·
조각 계약 `_apply_task_frames` · 선행 결과 게이트 `_gate_level`·`_task_verdict` ·
사후 대조 `_postcheck_result` · 격리 입력 `_make_isolated_input` · 결과 접기
`_pack_pipeline_result` · 재계획 `replanner`(무익 재시도 차단 포함) · 합성 `result_aggregator`.
이 모듈이 더하는 것은 plans/111 델타의 배선뿐이다.

- **D-1 `normalize`** — 계획의 모든 분기(사전 처리 조기 반환 포함)가 지나는 단일 출구.
  교정 3종 + 조각 계약 재검증.
- **D-2 `task_prompt`** — task 질의를 서브그래프 안에서 조각으로 찍는다. 선행 결과 값은
  프롬프트가 아니라 `prior_rows` 결정적 스코프 주입(D-086)이 SQL에 넣는다 — 값을 나열하지 않는다.
- **D-3 `replan`** — 행을 돌려준 task만 재계획 입력으로 쓴다. task 실패(산문·검증 소진·0행)는
  task 서브그래프가 사유와 함께 끝낸 것이다 — 재계획으로 다시 시도하지 않는다.
  새 task 질의의 SQL 문장은 버린다.
- **D-4 선행 0행 게이트** — `dispatch`의 `_gate_level`
  (D-203 게이트 · `COMPOSITE_SEQUENTIAL_GATE_ENABLED` 기본 on).

`task_run` 서브그래프는 부모와 **팬인 키(`task_outcomes`) 하나만** 주고받는다(103 Q4).
서브그래프를 `Send` 대상 노드로 직접 등록하지 않고 함수 노드 안에서 `ainvoke`한다 — 직접
등록하면 `astream_events`(SSE 경로)에서 공유 키 되쓰기로 `InvalidUpdateError`가 난다
(`output_schema`를 줘도 — 2026-09-22 langgraph 1.2.11 실측).
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict, cast

from langchain_core.language_models import BaseChatModel
from langgraph.errors import GraphBubbleUp
from langgraph.types import Overwrite, Send

from src.config import AppConfig
from src.domain.task_frame import contains_sql_statement, render_task_query, task_spans
from src.nodes.key_bridge import BridgeContext, key_bridge_enabled
from src.orchestration.agent_orchestrator import (
    _fallback_spec,
    _gate_level,
    _normalize,
    _postcheck_result,
    _scope_postcheck_on,
    _sequential_gate_on,
    _task_verdict,
    topological_levels,
)
from src.orchestration.intent_planner import (
    _apply_task_frames,
    _build_context_block,
    _coerce_host_inspect_intent,
    _normalize_plan_exit,
    _plan_turn,
    _task_frame_on,
)
from src.orchestration.replanner import _assign_ids, _extract_rows, replanner
from src.orchestration.subagents import (
    SUBAGENT_REGISTRY,
    _make_isolated_input,
    _normalize_targets,
    _pack_pipeline_result,
)
from src.orchestration.task_progress import emit_task_progress
from src.state import AgentState
from src.utils.prior_dependency import NOTE_DECOMPOSE, has_sequential_marker

logger = logging.getLogger(__name__)

TASK_RUN_NODE = "task_run"
#: 노드 체인(서브그래프 안 SQL 파이프라인)으로 실행하는 담당 — 그 외는 2단 핸들러 함수를 부른다.
DATA_AGENTS: tuple[str, ...] = ("data_query", "alarm_query")
#: 라우터가 이번 턴에 정한 조회 대상 — 데이터 task가 이어 받는다(task별 재분류는 103 P1-2).
_ROUTING_KEYS: tuple[str, ...] = (
    "target_databases", "is_multi_db", "active_db_id", "user_specified_db", "db_scope_source",
)
#: 서브그래프 전용 키 — 2단 핸들러에 넘기는 격리 입력에서는 뺀다.
_TASK_KEYS: tuple[str, ...] = ("current_task", "task_total", "task_result")


class TaskRunState(AgentState, total=False):
    """task 서브그래프 상태 — 부모 상태 + 이 task의 사양."""

    current_task: dict[str, Any]
    task_total: int
    task_result: dict[str, Any] | None


class TaskOutcomeState(TypedDict):
    """task 서브그래프가 부모에 돌려주는 유일한 키(팬인)."""

    task_outcomes: list[dict[str, Any]]


# ──────────────────────────────────────────────
# 진입
# ──────────────────────────────────────────────

def plan_loop_on(app_config: object) -> bool:
    """`TIER3_PLAN_LOOP_ENABLED` — 호출부 설정에서 읽는다(기동 시 1회 · `is True` 판정)."""
    return getattr(app_config, "tier3_plan_loop_enabled", False) is True


def plan_loop_entry(state: AgentState, app_config: AppConfig) -> bool:
    """3단 라우터 뒤 계획 루프 진입 판정 — 하나라도 불성립이면 현행 분기 그대로다.

    진입 신호는 라우터 구조화 출력 `needs_plan`이 정본이고, 순차 표지는 전환기 OR 신호다
    (103 G-1 기본 가정). SQL 승인이 켜져 있으면 들어가지 않는다 — 서브그래프에 승인 게이트가
    아직 없다(103 P3). 양식 턴은 단일 파이프라인 작업이라 제외한다(D-150).
    """
    if not plan_loop_on(app_config):
        return False
    if bool(getattr(app_config, "enable_sql_approval", False)):
        return False
    if state.get("routing_intent") not in DATA_AGENTS:
        return False
    if state.get("template_structure") or state.get("uploaded_file"):
        return False
    if state.get("zone_clarification"):
        return False
    return state.get("needs_plan") is True or has_sequential_marker(state.get("user_query", ""))


# ──────────────────────────────────────────────
# plan · normalize (D-1)
# ──────────────────────────────────────────────

async def plan(
    state: AgentState, *, llm: BaseChatModel, app_config: AppConfig,
) -> dict[str, Any]:
    """계획 본체(2단 `_plan_turn` 그대로) + 이 턴의 루프 상태 초기화.

    루프 키는 요청 스코프다 — 체크포인터는 델타만 병합하므로 여기서 비우지 않으면 앞 턴의
    결과·재계획 횟수가 새 턴에 남는다(103 K-2 · K-5는 이 루프에 한해 해소).
    """
    result = await _plan_turn(state, llm=llm, app_config=app_config)
    result.update({
        "task_results": {},
        "task_outcomes": Overwrite([]),
        "replan_count": 0,
        "replan_history": [],
        "needs_replan": False,
        "current_node": "plan",
    })
    return result


async def normalize(state: AgentState, *, app_config: AppConfig) -> dict[str, Any]:
    """계획의 단일 출구(plans/111 D-1) — 어느 분기에서 왔든 같은 결정적 교정을 지난다.

    ①조각 계약(`COMPOSITE_TASK_FRAME_ENABLED`)이면 조각을 다시 검증한다(LLM 경로는 이미
    통과 — 멱등) ②알람·프로세스 교정(양식 턴 제외) ③호스트 점검 교정(플래그 off면 no-op).
    사전 처리 조기 반환(존 선택 재진입 등)이 교정을 우회하던 구조(111 §2.4)가 여기서 닫힌다.
    """
    user_query = state.get("user_query", "")
    tasks = [dict(t) for t in state.get("task_plan") or [] if isinstance(t, dict)]
    notes: list[dict[str, Any]] = []
    if _task_frame_on(app_config) and any(task_spans(t) for t in tasks):
        fallback = {
            "tasks": [{
                "task_id": "t1", "agent": "data_query", "sub_query": user_query,
                "depends_on": [], "input_from": [], "order": 1, "status": "pending",
            }],
            "clarification_needed": None,
        }
        checked = _apply_task_frames(
            {"tasks": tasks}, user_query,
            _build_context_block(state.get("conversation_context"), user_query), fallback,
        )
        tasks = [dict(t) for t in checked["tasks"]]
        notes = [
            {"kind": NOTE_DECOMPOSE, "task_id": None,
             "reason": d.get("reason"), "detail": d.get("detail", "")}
            for d in checked.get("degraded") or [] if isinstance(d, dict) and d.get("detail")
        ]
    result: dict[str, Any] = {"task_plan": tasks}
    _normalize_plan_exit(result, state)
    result["task_plan"] = _coerce_host_inspect_intent(result["task_plan"], state, app_config)
    result["is_composite"] = len(result["task_plan"]) > 1
    result["current_node"] = "normalize"
    if notes:
        result["dependency_notes"] = list(state.get("dependency_notes") or []) + notes
    return result


# ──────────────────────────────────────────────
# dispatch (D-4 게이트) · task_run
# ──────────────────────────────────────────────

async def dispatch(state: AgentState, *, app_config: AppConfig) -> dict[str, Any]:
    """다음 레벨을 고르고 선행 결과 게이트를 건다 — 통과한 task만 `in_progress`로 표시한다.

    게이트에 걸린 task(선행 실패·0건·식별 컬럼 없음)는 실행하지 않고 사유 결과를 바로 기록한다
    (111 G-4 — 0행 선행 위에 후속을 돌리면 없는 대상을 조회한다).
    실제 `Send`는 뒤 분기 함수(`route_dispatch`)가 만든다.
    """
    tasks = [dict(t) for t in state.get("task_plan") or [] if isinstance(t, dict)]
    results = dict(state.get("task_results") or {})
    levels = topological_levels([t for t in tasks if t.get("status") == "pending"])
    out: dict[str, Any] = {"task_plan": tasks, "task_results": results, "current_node": "dispatch"}
    if not levels:
        return out
    level = levels[0]
    notes: list[dict[str, Any]] = []
    runnable = _gate_level(
        level, results, gate_on=_sequential_gate_on(app_config), notes=notes,
        verdicts={}, bridges={} if key_bridge_enabled(app_config) else None,
    )
    for task in level:
        if task not in runnable:
            skipped = results.get(str(task.get("task_id", "")))
            await emit_task_progress(task, "end", result=skipped, total=len(tasks))
    for task in runnable:
        task["status"] = "in_progress"
        await emit_task_progress(task, "start", total=len(tasks))
    if notes:
        out["dependency_notes"] = list(state.get("dependency_notes") or []) + notes
    return out


def route_dispatch(state: AgentState) -> list[Send] | str:
    """`in_progress` task마다 `Send(task_run)` — 없으면(전부 게이트 탈락) 곧장 `join`."""
    tasks = [t for t in state.get("task_plan") or [] if isinstance(t, dict)]
    running = [t for t in tasks if t.get("status") == "in_progress"]
    if not running:
        return "join"
    prior = dict(state.get("task_results") or {})
    return [Send(TASK_RUN_NODE, task_payload(t, state, prior, total=len(tasks))) for t in running]


def task_payload(
    task: dict[str, Any], state: AgentState, prior: dict[str, Any], *, total: int,
) -> dict[str, Any]:
    """task 서브그래프 입력 — 2단과 같은 격리 입력 + 이 task 사양 + 데이터 task의 조회 대상."""
    payload = _make_isolated_input(task, dict(state), prior)
    payload["user_query"] = task.get("sub_query") or state.get("user_query", "")
    payload["current_task"] = dict(task)
    payload["task_total"] = total
    if task.get("agent") in DATA_AGENTS:
        if task.get("db_ids"):
            # 사전 처리가 DB를 고정한 task(존 선택 재진입 등) — 2단 핸들러와 같은 정규화
            targets = _normalize_targets(list(task["db_ids"]), payload["user_query"])
            payload.update({
                "target_databases": targets, "is_multi_db": len(targets) > 1,
                "active_db_id": targets[0]["db_id"] if targets else None,
                "db_scope_source": "selected" if state.get("selected_db_ids") else "planned",
            })
        else:
            payload.update({k: state.get(k) for k in _ROUTING_KEYS})
    return payload


async def run_task(payload: dict[str, Any], *, task_graph: Any) -> dict[str, Any]:
    """task 서브그래프 1회 실행 → 팬인 키만 돌려준다.

    한 task의 예외가 턴 전체를 깨지 않게 부분 실패로 기록한다(D-005 — 2단
    `gather(return_exceptions=True)`와 같은 계약). 인터럽트(`GraphBubbleUp`)는 실패가 아니므로
    그대로 올린다.
    """
    task = payload.get("current_task") or {}
    try:
        out = await task_graph.ainvoke(payload)
        outcomes = list((out or {}).get("task_outcomes") or [])
    except GraphBubbleUp:
        raise
    except Exception as exc:  # noqa: BLE001 — 부분 실패 허용(D-005)
        logger.warning("task %s 서브그래프 예외 — 실패로 기록: %s", task.get("task_id"), exc)
        outcomes = [{"task_id": task.get("task_id"), "result": {"error": str(exc)}}]
    return {"task_outcomes": outcomes}


# ──────────────────────────────────────────────
# task 서브그래프 노드 (D-2)
# ──────────────────────────────────────────────

async def task_prompt(state: TaskRunState) -> dict[str, Any]:
    """task 질의를 찍는다(plans/111 D-2 · LLM 0회).

    조각(`spans`)이 있으면 원문 순서로 이어 붙인다(`render_task_query` — 선행 결과를 받는
    task만 고정 접두). 없으면 계획의 `sub_query`다. SQL 생성 입력
    (`parsed_requirements.original_query`)과 질의 텍스트를 task 범위로 좁힌다(D-094).
    복합 계획의 멀티 DB task는 DB별 조회 설명도 task 질의로 바꾼다 — 라우터 설명은 턴 전체
    질의에 대한 것이라 그대로 두면 task 범위가 풀린다.
    """
    task = state.get("current_task") or {}
    original = str(state.get("original_user_query") or state.get("user_query", ""))
    if task_spans(task):
        text = render_task_query(task, original)
    else:
        text = str(task.get("sub_query") or original)
    parsed = dict(state.get("parsed_requirements") or {})
    parsed["original_query"] = text
    out: dict[str, Any] = {
        "user_query": text, "parsed_requirements": parsed, "current_node": "task_prompt",
    }
    agent = task.get("agent")
    if agent in DATA_AGENTS:
        out["routing_intent"] = agent
        targets = state.get("target_databases") or []
        if state.get("is_composite") and targets and text != original:
            out["target_databases"] = [{**t, "sub_query_context": text} for t in targets]
    return out


def route_task_entry(state: TaskRunState) -> str:
    """담당별 실행 경로 — 데이터·알람은 노드 체인(단일/멀티), 그 외는 2단 핸들러."""
    if (state.get("current_task") or {}).get("agent") not in DATA_AGENTS:
        return "task_handler"
    return "multi_db_executor" if state.get("is_multi_db") else "schema_analyzer"


async def task_handler(
    state: TaskRunState, *, llm: BaseChatModel, app_config: AppConfig,
) -> dict[str, Any]:
    """노드 체인이 없는 담당(프로세스·호스트 점검·일반 추론 등) — 2단 레지스트리 핸들러를 부른다."""
    task = dict(state.get("current_task") or {})
    spec = SUBAGENT_REGISTRY.get(task.get("agent", "")) or _fallback_spec()
    isolated = {k: v for k, v in dict(state).items() if k not in _TASK_KEYS}
    res: object
    try:
        res = await spec.handler(task, isolated, llm=spec.model or llm, app_config=app_config)
    except GraphBubbleUp:
        raise
    except Exception as exc:  # noqa: BLE001 — 2단 `gather(return_exceptions=True)`와 같은 부분 실패
        res = exc
    return {"task_result": _normalize(res), "current_node": "task_handler"}


def task_error(state: TaskRunState) -> dict[str, Any]:
    """검증·실행 재시도 소진 또는 산문 응답 — task 안에서 사유와 함께 끝낸다(111 D-3 · 108 CU-A2).

    최종 응답을 쓰지 않는다 — 사유는 task 결과의 `error`로 올라가 합성·경과 노트에 실린다.
    """
    validation = state.get("validation_result") or {}
    if validation.get("non_sql"):
        prose = (state.get("generated_sql") or "").strip()[:300]
        message = "요청을 SQL로 옮기지 못했습니다."
        if prose:
            message += f" 조회 엔진이 남긴 설명: {prose}"
    else:
        message = (
            state.get("error_message") or validation.get("reason")
            or "SQL 생성·검증 재시도 한도를 넘었습니다."
        )
    return {"error_message": message, "current_node": "task_error"}


def pack_outcome(state: TaskRunState) -> dict[str, Any]:
    """task 결과를 팬인 키 한 건으로 접는다 — 결과 모양은 2단 핸들러와 같은 함수로 만든다."""
    task = state.get("current_task") or {}
    handled = state.get("task_result")
    if isinstance(handled, dict):
        result = handled
    else:
        if task.get("db_ids"):
            origin = "selected" if state.get("selected_db_ids") else "planned"
        else:
            origin = state.get("db_scope_source") or "classified"
        result = _pack_pipeline_result(
            dict(state), list(state.get("target_databases") or []), state.get("error_message"),
            ownership_notes=[], db_origin=origin, db_succeeded=False, db_pinned=False,
        )
    return {"task_outcomes": [{"task_id": task.get("task_id"), "result": result}]}


# ──────────────────────────────────────────────
# join · replan (D-3)
# ──────────────────────────────────────────────

async def join(state: AgentState, *, app_config: AppConfig) -> dict[str, Any]:
    """이번 레벨의 팬인 결과를 `task_results`에 **한 번** 기록한다(`defer` — 병렬 task 전부 뒤).

    사후 대조는 2단과 같은 함수다. 판정은 결정적이라 게이트가 본 선행 결과로 다시 계산한다.
    """
    tasks = [dict(t) for t in state.get("task_plan") or [] if isinstance(t, dict)]
    before = dict(state.get("task_results") or {})
    results = dict(before)
    running = [t for t in tasks if t.get("status") == "in_progress"]
    outcomes = {
        o.get("task_id"): o.get("result")
        for o in state.get("task_outcomes") or [] if isinstance(o, dict)
    }
    bridges: dict[str, BridgeContext] | None = {} if key_bridge_enabled(app_config) else None
    postcheck_on = _scope_postcheck_on(app_config)
    for task in running:
        tid = task.get("task_id", "")
        raw = outcomes.get(tid)
        missing = RuntimeError("task 결과가 돌아오지 않았습니다.")
        norm = _normalize(raw if raw is not None else missing)
        verdict = _task_verdict(task, before, bridges)
        norm = _postcheck_result(
            task, norm, verdicts={tid: verdict} if verdict is not None else {},
            bridges=bridges, postcheck_on=postcheck_on,
        )
        task["status"] = "failed" if norm.get("error") else "completed"
        results[tid] = norm
        await emit_task_progress(task, "end", result=norm, total=len(tasks))
        if norm.get("error"):
            logger.warning("task %s (agent=%s) 실패: %s", tid, task.get("agent"), norm["error"])
    return {"task_plan": tasks, "task_results": results, "current_node": "join"}


def route_after_join(state: AgentState) -> str:
    """남은 대기 task가 있으면 다음 레벨, 없으면 재계획 판단."""
    tasks = [t for t in state.get("task_plan") or [] if isinstance(t, dict)]
    return "dispatch" if any(t.get("status") == "pending" for t in tasks) else "replan"


async def replan(
    state: AgentState, *, llm: BaseChatModel, app_config: AppConfig,
) -> dict[str, Any]:
    """재계획 — 입력을 **행을 돌려준 task**로 좁힌 2단 `replanner`(plans/111 D-3 · G-3).

    - task 실패(산문·검증 소진·실행 실패·0행)는 입력에서 뺀다. 행을 돌려준 task가 없으면
      LLM을 부르지 않는다.
    - 남는 후속은 ①행을 돌려준 선행에 의존하거나(선행 결과가 뒤 조건을 정함) ②계획에 없던
      담당이어야 한다 — 계획에 이미 있는 담당의 독립 task는 같은 질의의 재시도다(111 §2.3 B군).
      새 task 질의에 SQL 문장이 있으면 버린다.
    - 상한(`MAX_REPLAN`)에 닿아 멈추면 경과 노트로 알린다(103 §2.3 — 조용히 끊지 않는다).
    """
    tasks = [dict(t) for t in state.get("task_plan") or [] if isinstance(t, dict)]
    results = dict(state.get("task_results") or {})
    count = int(state.get("replan_count") or 0)
    history = list(state.get("replan_history") or [])
    stop: dict[str, Any] = {
        "needs_replan": False, "replan_history": history, "current_node": "replan",
    }

    if count >= app_config.max_replan:
        if count:
            stop["dependency_notes"] = list(state.get("dependency_notes") or []) + [{
                "kind": NOTE_DECOMPOSE, "task_id": None, "reason": "replan_cap",
                "detail": (
                    f"재계획 상한({app_config.max_replan}회)에 도달해 "
                    "지금까지의 결과로 답했습니다."
                ),
            }]
        return stop

    productive = [t for t in tasks if _extract_rows(results.get(str(t.get("task_id", "")), {}))]
    if not productive:
        logger.info(
            "replan: 행을 돌려준 task가 없다 — 재계획 생략"
            "(task 실패는 재계획으로 복구하지 않는다 · 111 D-3)"
        )
        return stop

    scoped = cast(AgentState, {
        **dict(state),
        "task_plan": productive,
        "task_results": {t["task_id"]: results[t["task_id"]] for t in productive},
    })
    decided = await replanner(scoped, llm=llm, app_config=app_config)
    if not decided.get("needs_replan"):
        return {**decided, "current_node": "replan"}

    agents_in_plan = {t.get("agent") for t in tasks}
    kept: list[dict[str, Any]] = []
    for new in decided.get("task_plan", [])[len(productive):]:
        if contains_sql_statement(str(new.get("sub_query") or "")):
            logger.info("replan: SQL 문장이 든 후속 제거(111 D-3): %.80s", new.get("sub_query"))
        elif not new.get("depends_on") and new.get("agent") in agents_in_plan:
            logger.info(
                "replan: 계획에 있는 담당의 독립 후속 제거 — 재시도(111 D-3): agent=%s",
                new.get("agent"),
            )
        else:
            kept.append(new)
    if not kept:
        return stop

    added = _assign_ids(kept, existing=tasks)
    new_history = list(decided.get("replan_history") or history)
    if new_history:
        new_history[-1] = {**new_history[-1], "added": len(added)}
    return {
        "task_plan": tasks + added,
        "needs_replan": True,
        "replan_count": count + 1,
        "replan_history": new_history,
        "current_node": "replan",
    }


def route_after_replan(state: AgentState) -> str:
    """재계획이 task를 더했으면 다시 `dispatch`, 아니면 합성."""
    return "dispatch" if state.get("needs_replan") else "finalize"
