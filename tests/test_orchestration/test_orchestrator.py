"""agent_orchestrator 노드 단위 테스트 (Plan 48 §9).

검증 대상:
- 순차 실행 / 위상정렬 (test_orchestrator_sequential)
- 병렬 실행 / gather (test_orchestrator_parallel)
- 부분 실패 보존 (test_orchestrator_partial_failure, D-005)
- status 전이 (test_task_status_transition, P2)
- 데이터 의존 체이닝 (test_data_dependent_chaining, 패턴②)
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from src.orchestration.agent_orchestrator import agent_orchestrator, topological_levels
from src.orchestration.subagents import SUBAGENT_REGISTRY, SubAgentSpec
from src.state import create_initial_state


def _make_state(tasks: list[dict]):
    """task_plan을 주입한 초기 state를 만든다."""
    state = create_initial_state(user_query="복합 질의")
    state["task_plan"] = tasks
    return state


def _registry_with(handlers: dict[str, AsyncMock]) -> dict:
    """주어진 agent 이름 → mock handler를 가진 임시 registry 패치 dict를 만든다."""
    patched = {}
    for name, handler in handlers.items():
        base = SUBAGENT_REGISTRY[name]
        patched[name] = SubAgentSpec(
            base.name, base.description, handler, fallback=base.fallback
        )
    return patched


# ──────────────────────────────────────────────
# topological_levels
# ──────────────────────────────────────────────

def test_topological_levels_sequential():
    """depends_on 체인(t1→t2) → 2레벨 [[t1],[t2]]."""
    tasks = [
        {"task_id": "t1", "agent": "data_query", "depends_on": [], "order": 1},
        {"task_id": "t2", "agent": "data_query", "depends_on": ["t1"], "order": 2},
    ]
    levels = topological_levels(tasks)
    assert len(levels) == 2
    assert [t["task_id"] for t in levels[0]] == ["t1"]
    assert [t["task_id"] for t in levels[1]] == ["t2"]


def test_topological_levels_parallel():
    """독립 task 2개(depends_on=[]) → 1레벨 [[t1,t2]] (병렬)."""
    tasks = [
        {"task_id": "t1", "agent": "data_query", "depends_on": [], "order": 1},
        {"task_id": "t2", "agent": "data_query", "depends_on": [], "order": 2},
    ]
    levels = topological_levels(tasks)
    assert len(levels) == 1
    assert {t["task_id"] for t in levels[0]} == {"t1", "t2"}


# ──────────────────────────────────────────────
# test_orchestrator_sequential
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orchestrator_sequential(mock_config):
    """depends_on 체인 → t1 실행 후 t2 실행 (실행 순서·prior 보장)."""
    order_log: list[str] = []

    async def t1_handler(task, isolated, **kwargs):
        order_log.append("t1")
        return {"final_response": "t1 결과", "rows": [{"hostname": "web-01"}]}

    async def t2_handler(task, isolated, **kwargs):
        order_log.append("t2")
        return {"final_response": "t2 결과"}

    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1",
         "depends_on": [], "input_from": [], "order": 1, "status": "pending"},
        {"task_id": "t2", "agent": "alarm_query", "sub_query": "q2",
         "depends_on": ["t1"], "input_from": [], "order": 2, "status": "pending"},
    ]
    state = _make_state(tasks)

    patched = _registry_with({
        "data_query": AsyncMock(side_effect=t1_handler),
        "alarm_query": AsyncMock(side_effect=t2_handler),
    })
    with patch.dict(SUBAGENT_REGISTRY, patched):
        result = await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)

    # t1이 t2보다 먼저 실행됨
    assert order_log == ["t1", "t2"]
    assert result["task_results"]["t1"]["final_response"] == "t1 결과"
    assert result["task_results"]["t2"]["final_response"] == "t2 결과"
    # 두 task 모두 완료
    assert all(t["status"] == "completed" for t in result["task_plan"])


# ──────────────────────────────────────────────
# test_orchestrator_parallel
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orchestrator_parallel(mock_config):
    """독립 task 2개 → 동일 레벨에서 asyncio.gather로 동시 실행된다."""
    # 동시 실행 검증: 두 핸들러가 서로의 진입을 기다린 후 진행 (barrier)
    started = asyncio.Event()
    both_in = {"count": 0}

    async def make_handler(name):
        async def handler(task, isolated, **kwargs):
            both_in["count"] += 1
            if both_in["count"] == 2:
                started.set()
            # 두 핸들러가 모두 진입할 때까지 대기 (순차였다면 데드락/타임아웃)
            await asyncio.wait_for(started.wait(), timeout=2.0)
            return {"final_response": f"{name} 결과"}
        return handler

    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1",
         "depends_on": [], "input_from": [], "order": 1, "status": "pending"},
        {"task_id": "t2", "agent": "alarm_query", "sub_query": "q2",
         "depends_on": [], "input_from": [], "order": 2, "status": "pending"},
    ]
    state = _make_state(tasks)

    patched = _registry_with({
        "data_query": AsyncMock(side_effect=await make_handler("t1")),
        "alarm_query": AsyncMock(side_effect=await make_handler("t2")),
    })
    with patch.dict(SUBAGENT_REGISTRY, patched):
        result = await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)

    # 두 핸들러가 모두 진입했음 → 병렬 실행 확인
    assert both_in["count"] == 2
    assert result["task_results"]["t1"]["final_response"] == "t1 결과"
    assert result["task_results"]["t2"]["final_response"] == "t2 결과"


# ──────────────────────────────────────────────
# test_orchestrator_partial_failure (D-005)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orchestrator_partial_failure(mock_config):
    """1개 task 핸들러가 예외 → 해당 task failed, 나머지 completed, 결과 모두 기록."""
    async def ok_handler(task, isolated, **kwargs):
        return {"final_response": "성공"}

    async def fail_handler(task, isolated, **kwargs):
        raise RuntimeError("DB 연결 실패")

    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1",
         "depends_on": [], "input_from": [], "order": 1, "status": "pending"},
        {"task_id": "t2", "agent": "alarm_query", "sub_query": "q2",
         "depends_on": [], "input_from": [], "order": 2, "status": "pending"},
    ]
    state = _make_state(tasks)

    patched = _registry_with({
        "data_query": AsyncMock(side_effect=ok_handler),
        "alarm_query": AsyncMock(side_effect=fail_handler),
    })
    with patch.dict(SUBAGENT_REGISTRY, patched):
        result = await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)

    by_id = {t["task_id"]: t for t in result["task_plan"]}
    assert by_id["t1"]["status"] == "completed"
    assert by_id["t2"]["status"] == "failed"
    # 성공 결과 보존
    assert result["task_results"]["t1"]["final_response"] == "성공"
    # 실패는 error로 정규화 (D-005)
    assert "error" in result["task_results"]["t2"]
    assert "DB 연결 실패" in result["task_results"]["t2"]["error"]


# ──────────────────────────────────────────────
# test_task_status_transition (P2)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_status_transition(mock_config):
    """실행 전 pending → 실행 후 성공=completed/실패=failed. 레벨 진입 시 in_progress 설정."""
    seen_status_at_run: dict[str, str] = {}

    async def capture_handler(task, isolated, **kwargs):
        # 핸들러 실행 시점에는 in_progress여야 함 (레벨 진입 시 설정됨)
        seen_status_at_run[task["task_id"]] = task["status"]
        if task["task_id"] == "t2":
            raise RuntimeError("실패 유도")
        return {"final_response": "ok"}

    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1",
         "depends_on": [], "input_from": [], "order": 1, "status": "pending"},
        {"task_id": "t2", "agent": "alarm_query", "sub_query": "q2",
         "depends_on": [], "input_from": [], "order": 2, "status": "pending"},
    ]
    # 실행 전 모든 task가 pending인지 확인 (intent_planner 산출 계약)
    assert all(t["status"] == "pending" for t in tasks)

    state = _make_state(tasks)
    patched = _registry_with({
        "data_query": AsyncMock(side_effect=capture_handler),
        "alarm_query": AsyncMock(side_effect=capture_handler),
    })
    with patch.dict(SUBAGENT_REGISTRY, patched):
        result = await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)

    # 핸들러 실행 시점에는 in_progress였음
    assert seen_status_at_run["t1"] == "in_progress"
    assert seen_status_at_run["t2"] == "in_progress"
    # 실행 후 최종 상태
    by_id = {t["task_id"]: t for t in result["task_plan"]}
    assert by_id["t1"]["status"] == "completed"
    assert by_id["t2"]["status"] == "failed"


# ──────────────────────────────────────────────
# test_data_dependent_chaining (패턴②, §4.10.1)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_data_dependent_chaining(mock_config):
    """t1→t2(input_from=["t1"]) → t2 실행 시 isolated에 prior_rows[t1]이 주입된다."""
    captured = {}

    async def t1_handler(task, isolated, **kwargs):
        # data_query 결과를 query_results로 반환 → prior 주입원
        return {"query_results": [{"hostname": f"srv-{i}", "cpu": float(i)} for i in range(5)]}

    async def t2_handler(task, isolated, **kwargs):
        # t2가 받은 isolated 컨텍스트를 캡처
        captured["isolated"] = isolated
        return {"final_response": "t2 완료"}

    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "장애 서버 조회",
         "depends_on": [], "input_from": [], "order": 1, "status": "pending"},
        {"task_id": "t2", "agent": "data_query", "sub_query": "해당 서버 CPU 조회",
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2, "status": "pending"},
    ]
    state = _make_state(tasks)

    # 같은 agent(data_query)지만 task_id로 분기 불가하므로 task_id별 동작을 핸들러에서 처리
    async def router_handler(task, isolated, **kwargs):
        if task["task_id"] == "t1":
            return await t1_handler(task, isolated, **kwargs)
        return await t2_handler(task, isolated, **kwargs)

    patched = _registry_with({"data_query": AsyncMock(side_effect=router_handler)})
    with patch.dict(SUBAGENT_REGISTRY, patched):
        result = await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)

    # t2의 isolated에 prior_rows[t1]이 식별 키 위주로 주입됨
    isolated = captured["isolated"]
    assert "prior_rows" in isolated
    assert "t1" in isolated["prior_rows"]
    rows = isolated["prior_rows"]["t1"]
    assert len(rows) == 5
    # 식별 키(hostname)만 유지 (cpu 제거)
    assert all(set(r.keys()) == {"hostname"} for r in rows)
    assert all(t["status"] == "completed" for t in result["task_plan"])
