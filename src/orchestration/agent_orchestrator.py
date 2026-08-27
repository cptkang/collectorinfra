"""task 오케스트레이터 노드 (Plan 48, deepagents `task` 위임 대응).

task_plan을 의존성 위상정렬하여 레벨 단위로 실행한다.
같은 레벨(서로 의존 없음)의 task는 asyncio.gather로 병렬, 레벨 간에는 순차 실행한다.
부분 실패를 허용하며(D-005 정책), 각 task의 status를 갱신한다(Planning P2 정합).

본 노드는 tool-calling을 사용하지 않으며, registry 기반 코드 디스패치로 위임한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from langchain_core.language_models import BaseChatModel

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.orchestration.subagents import (
    SUBAGENT_REGISTRY,
    SubAgentSpec,
    _make_isolated_input,
)
from src.orchestration.sufficiency import (
    SufficiencyReport,
    check_sufficiency,
    summarize_shortfalls,
)
from src.state import AgentState

logger = logging.getLogger(__name__)


async def agent_orchestrator(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """task_plan을 레벨 단위(위상정렬)로 병렬/순차 실행한다.

    Args:
        state: 현재 에이전트 상태 (task_plan 포함)
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    재진입(재계획 후)에는 이미 completed/failed인 task를 재실행하지 않고
    미완료(pending) task만 실행한다(R-A2). 누적 결과(task_results)를 시드로 시작하여
    완료 task 결과와 신규 결과를 함께 유지한다.

    Returns:
        업데이트할 State 필드:
        - task_plan: status가 갱신된 TaskSpec 목록 (전체 유지)
        - task_results: {task_id: 정규화된 결과} (기존 누적 + 신규)
        - current_node: "agent_orchestrator"
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    tasks = state["task_plan"]
    # 누적 결과를 시드로 시작 (재진입 시 완료 task 결과 보존, prior 주입원도 누적분 사용)
    results: dict[str, dict] = dict(state.get("task_results") or {})
    # task별 주입 대상 — 충족도 판정(W5)의 기준. 대상 주입이 없으면 비어 있고 검증도 건너뛴다.
    injected: dict[str, list[dict]] = {}

    # 미완료(pending) task만 실행 — 완료/실패 task 재실행 방지(R-A2).
    # topological_levels는 by_id에 없는 의존(완료 task)을 자동 무시하므로
    # pending만 넘겨도 완료 task를 가리키는 depends_on은 안전하게 처리된다.
    pending = [t for t in tasks if t.get("status") == "pending"]

    for level in topological_levels(pending):
        # 레벨 시작: 상태 전이 (P2)
        for t in level:
            t["status"] = "in_progress"

        coros = [
            _run_agent(task, state, llm, app_config, prior=results, injected=injected)
            for task in level
        ]
        level_results = await asyncio.gather(*coros, return_exceptions=True)

        for task, res in zip(level, level_results):
            norm = _normalize(res)
            task["status"] = "failed" if norm.get("error") else "completed"
            results[task["task_id"]] = norm
            if norm.get("error"):
                logger.warning(
                    "task %s (agent=%s) 실패: %s",
                    task["task_id"], task.get("agent"), norm["error"],
                )

    # 오케스트레이션 수준 충족도 검증·재계획 (Plan 78 W5 · P7 · LLM 미사용).
    # 대상 주입이 없었으면 `injected`가 비어 검증도 재시도도 일어나지 않는다 — 회귀 0.
    sufficiency_reasons: list[dict] = []
    if injected:
        report = check_sufficiency(tasks, results, injected)
        retried = False
        if not report.sufficient:
            logger.info("충족도 미달 — 1회 재실행: %s", report.as_reasons())
            results, retried = await _retry_once(
                report, tasks, state, llm, app_config, results, injected
            )
            # **재판정은 한 번뿐**이다. 개선 여부와 무관하게 여기서 끝낸다(무한 루프 금지).
            report = check_sufficiency(tasks, results, injected)
        if not report.sufficient:
            sufficiency_reasons = report.as_reasons()
            note = summarize_shortfalls(report, retried=retried)
            logger.warning("충족도 미달 잔존(사유 노출): %s", note)

    out: dict = {
        "task_plan": tasks,
        "task_results": results,
        "current_node": "agent_orchestrator",
    }
    if sufficiency_reasons:
        # 미충족 사유를 응답에 노출한다(78 W5-3 · 침묵 폴백 금지).
        out["sufficiency_shortfalls"] = sufficiency_reasons
    return out


async def _retry_once(
    report: SufficiencyReport,
    tasks: list[dict],
    state: AgentState,
    llm: BaseChatModel,
    app_config: AppConfig,
    results: dict[str, dict],
    injected: dict[str, list[dict]],
) -> tuple[dict[str, dict], bool]:
    """미충족 task를 **1회만** 재실행한다 (78 W5-2).

    종료 조건 셋을 전부 지킨다: **최대 1회 재시도** · **개선 없으면 즉시 중단** ·
    전체 타임아웃 준수(handler 내부 가드에 위임).

    대상 집합을 **명시 주입**한다 — 재실행이 같은 경로를 다시 밟으면 같은 결과가 나온다.

    Returns:
        (갱신된 결과, 재시도 수행 여부)
    """
    by_id = {t.get("task_id"): t for t in tasks}
    retried = False
    for task_id in report.task_ids:
        task = by_id.get(task_id)
        if task is None:
            continue
        targets = injected.get(task_id) or []
        if not targets:
            continue
        retry_state = dict(state)
        retry_state["prior_targets"] = targets   # 명시 주입
        try:
            res = _normalize(
                await _run_agent(task, retry_state, llm, app_config, prior=results)
            )
        except Exception as exc:  # noqa: BLE001 — 재시도 실패가 원 결과를 지우지 않는다
            logger.warning("충족도 재실행 실패 task=%s: %s", task_id, exc)
            continue
        retried = True
        if res.get("error"):
            continue  # 개선 없음 — 원 결과를 유지한다
        results[task_id] = res
        task["status"] = "completed"
    return results, retried


def topological_levels(tasks: list[dict]) -> list[list[dict]]:
    """depends_on 기반 Kahn 위상정렬로 task를 레벨 단위로 분할한다.

    같은 레벨의 task는 서로 의존이 없어 병렬 실행 가능하다.
    순환/누락 의존이 있으면 남은 task를 한 레벨로 안전 처리한다(무한루프 방지).

    Args:
        tasks: TaskSpec 목록

    Returns:
        레벨별 task 목록의 리스트
    """
    if not tasks:
        return []

    by_id = {t["task_id"]: t for t in tasks}
    # 존재하는 task만 의존성으로 인정 (누락 의존 무시)
    remaining_deps = {
        t["task_id"]: {d for d in (t.get("depends_on") or []) if d in by_id}
        for t in tasks
    }

    levels: list[list[dict]] = []
    placed: set[str] = set()

    while len(placed) < len(tasks):
        # 모든 의존이 이미 배치된 task = 이번 레벨
        level_ids = [
            tid for tid in by_id
            if tid not in placed and remaining_deps[tid] <= placed
        ]

        if not level_ids:
            # 순환/해소 불가 — 남은 task를 한 레벨로 안전 처리
            level_ids = [tid for tid in by_id if tid not in placed]
            logger.warning("위상정렬 순환/누락 의존 감지, 남은 task를 단일 레벨로 처리: %s", level_ids)

        # order 기준 정렬로 표시 순서 안정화
        level = sorted((by_id[tid] for tid in level_ids), key=lambda t: t.get("order", 0))
        levels.append(level)
        placed.update(level_ids)

    return levels


async def _run_agent(
    task: dict,
    state: AgentState,
    llm: BaseChatModel,
    app_config: AppConfig,
    *,
    prior: dict,
    injected: Optional[dict] = None,
) -> dict:
    """단일 task를 해당 subagent handler로 실행한다.

    Args:
        task: 현재 TaskSpec
        state: 전체 에이전트 상태
        llm: 메인 LLM 인스턴스
        app_config: 앱 설정
        prior: 지금까지 완료된 task 결과 (input_from 주입용)
        injected: (선택) task별 주입 대상 기록 수집기 — 충족도 판정 기준(W5)

    Returns:
        subagent handler의 반환 dict
    """
    spec = SUBAGENT_REGISTRY.get(task["agent"]) or _fallback_spec()
    isolated = _make_isolated_input(task, state, prior)
    isolated["user_query"] = task["sub_query"]
    # 이 task에 실제로 주입된 대상 집합을 기록한다 — 충족도 판정(W5-1)과 대상 정합
    # 사후 대조(W5-5)의 **기준**이다. 기록하지 않으면 "몇 개를 조사했어야 하는가"를 알 수 없다.
    if injected is not None and isolated.get("prior_targets"):
        injected[task["task_id"]] = list(isolated["prior_targets"])
    return await spec.handler(
        task, isolated, llm=spec.model or llm, app_config=app_config
    )


def _fallback_spec() -> SubAgentSpec:
    """fallback(general-purpose) subagent 스펙을 반환한다 (SubAgent S5).

    Returns:
        registry에서 fallback=True인 SubAgentSpec
    """
    for spec in SUBAGENT_REGISTRY.values():
        if spec.fallback:
            return spec
    # 안전 폴백 (registry에 fallback이 없을 경우)
    return SUBAGENT_REGISTRY["general_inference"]


def _normalize(res: object) -> dict:
    """subagent 실행 결과를 정규화한다 (예외 → 부분 실패 기록, D-005 정책).

    Args:
        res: handler 반환값 또는 예외(gather return_exceptions)

    Returns:
        정규화된 결과 dict (실패 시 "error" 키 포함)
    """
    if isinstance(res, BaseException):
        return {"error": str(res)}
    if isinstance(res, dict):
        return res
    return {"error": "subagent가 예상치 못한 값을 반환했습니다."}
