"""replanner 노드 및 동적 재계획 루프 단위/통합 테스트 (Plan 49 §6).

검증 대상 (Plan 49 §6 표 10종):
- 후속 불필요 종료 (test_replanner_terminates)
- 후속 task 추가 + replan_count 증가 (test_replanner_adds_followup)
- 완료 task·결과 보존 (증분 추가) (test_replanner_preserves_completed)
- 재계획 상한 가드 (test_replan_max_guard)
- LLM/파싱 실패 보수적 종료 (test_replanner_parse_failure_terminates)
- 재진입 시 완료 task 미재실행 (test_orchestrator_skips_completed)
- 신규 task input_from 주입 (test_followup_input_from_injection)
- 정적 복합(①②) 회귀: 재계획 미발생 (test_pattern12_no_replan_regression)
- 그래프 루프 형태·라우팅 (test_graph_loop_shape)
- arch_check 위반 0건 (test_arch_check)
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import (
    AppConfig,
    DBHubConfig,
    LLMConfig,
    QueryConfig,
    SecurityConfig,
    ServerConfig,
)
from src.graph import (
    build_graph,
    route_after_orchestrator,
    route_after_replanner,
)
from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.replanner import replanner
from src.orchestration.subagents import SUBAGENT_REGISTRY, SubAgentSpec
from src.state import create_initial_state


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────

def _mock_llm(content: str) -> AsyncMock:
    """ainvoke가 주어진 content를 가진 응답을 반환하는 mock LLM을 만든다.

    AsyncMock은 KBGenAIChat이 아니므로 replanner의 AIMessage 삽입 분기를 타지 않는다.
    ainvoke가 호출되고 response.content가 extract_json_from_response로 파싱된다.
    """
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=content)
    return llm


def _make_state(*, user_query="복합 질의", task_plan=None, task_results=None, replan_count=0):
    """task_plan/task_results/replan_count를 주입한 초기 state를 만든다."""
    state = create_initial_state(user_query=user_query)
    state["task_plan"] = task_plan or []
    state["task_results"] = task_results or {}
    state["replan_count"] = replan_count
    return state


def _registry_with(handlers: dict[str, AsyncMock]) -> dict:
    """agent 이름 → mock handler를 가진 임시 registry 패치 dict를 만든다."""
    patched = {}
    for name, handler in handlers.items():
        base = SUBAGENT_REGISTRY[name]
        patched[name] = SubAgentSpec(
            base.name, base.description, handler, fallback=base.fallback
        )
    return patched


# ──────────────────────────────────────────────
# 1. test_replanner_terminates
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replanner_terminates(mock_config):
    """LLM이 needs_followup=false 반환 → needs_replan=False, task_plan 미변경(반환에 없음)."""
    content = json.dumps(
        {"needs_followup": False, "reason": "결과 충분", "new_tasks": []},
        ensure_ascii=False,
    )
    llm = _mock_llm(content)
    task_plan = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1",
         "depends_on": [], "input_from": [], "order": 1, "status": "completed"},
    ]
    state = _make_state(
        task_plan=task_plan,
        task_results={"t1": {"query_results": [{"hostname": "web-01"}]}},
    )

    result = await replanner(state, llm=llm, app_config=mock_config)

    assert result["needs_replan"] is False
    assert result["current_node"] == "replanner"
    # 종료 시 task_plan을 반환하지 않음 (미변경)
    assert "task_plan" not in result
    assert "replan_count" not in result
    llm.ainvoke.assert_awaited_once()


# ──────────────────────────────────────────────
# 2. test_replanner_adds_followup
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replanner_adds_followup(mock_config):
    """LLM이 needs_followup=true + new_tasks 1개 → task_plan=기존+신규, replan_count +1."""
    content = json.dumps(
        {
            "needs_followup": True,
            "reason": "장애 서버 3건 발견",
            "new_tasks": [
                {"agent": "alarm_query", "sub_query": "선행 결과의 장애 서버 알람 이력 조회",
                 "depends_on": ["t1"], "input_from": ["t1"]}
            ],
        },
        ensure_ascii=False,
    )
    llm = _mock_llm(content)
    task_plan = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "장애 서버 조회",
         "depends_on": [], "input_from": [], "order": 1, "status": "completed"},
    ]
    state = _make_state(
        task_plan=task_plan,
        task_results={"t1": {"query_results": [{"hostname": "web-01"}]}},
        replan_count=0,
    )

    result = await replanner(state, llm=llm, app_config=mock_config)

    assert result["needs_replan"] is True
    assert result["replan_count"] == 1
    assert result["current_node"] == "replanner"
    # 기존 + 신규 = 2개
    assert len(result["task_plan"]) == 2
    # 기존 t1 보존
    assert result["task_plan"][0]["task_id"] == "t1"
    # 신규 task: status=pending, task_id 충돌 없음(t2), agent 보존
    new_task = result["task_plan"][1]
    assert new_task["status"] == "pending"
    assert new_task["task_id"] != "t1"
    assert new_task["task_id"] == "t2"
    assert new_task["agent"] == "alarm_query"
    assert new_task["depends_on"] == ["t1"]
    assert new_task["input_from"] == ["t1"]


# ──────────────────────────────────────────────
# 3. test_replanner_preserves_completed
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replanner_preserves_completed(mock_config):
    """완료 task(t1)+그 결과가 있는 state 재계획 → t1 그대로 보존, 신규만 append."""
    content = json.dumps(
        {
            "needs_followup": True,
            "reason": "후속 필요",
            "new_tasks": [
                {"agent": "alarm_query", "sub_query": "후속 조회",
                 "depends_on": ["t1"], "input_from": ["t1"]}
            ],
        },
        ensure_ascii=False,
    )
    llm = _mock_llm(content)
    t1 = {"task_id": "t1", "agent": "data_query", "sub_query": "원조회",
          "depends_on": [], "input_from": [], "order": 1, "status": "completed"}
    t1_result = {"query_results": [{"hostname": "web-01"}, {"hostname": "web-02"}]}
    state = _make_state(
        task_plan=[t1],
        task_results={"t1": t1_result},
    )

    result = await replanner(state, llm=llm, app_config=mock_config)

    # t1이 그대로 보존 (status=completed, 동일 객체 내용)
    by_id = {t["task_id"]: t for t in result["task_plan"]}
    assert "t1" in by_id
    assert by_id["t1"]["status"] == "completed"
    assert by_id["t1"]["agent"] == "data_query"
    assert by_id["t1"]["sub_query"] == "원조회"
    # 신규 task만 추가됨 (t1 외 1개)
    assert len(result["task_plan"]) == 2
    new_ids = [t["task_id"] for t in result["task_plan"] if t["task_id"] != "t1"]
    assert len(new_ids) == 1
    # 기존 t1 결과는 replanner가 건드리지 않음 (state["task_results"] 불변)
    assert state["task_results"]["t1"] == t1_result


# ──────────────────────────────────────────────
# 4. test_replan_max_guard
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replan_max_guard(mock_config):
    """replan_count == max_replan(3) 진입 → LLM 호출 없이 needs_replan=False 종료."""
    assert mock_config.max_replan == 3
    llm = _mock_llm(json.dumps({"needs_followup": True, "new_tasks": [{"agent": "alarm_query"}]}))
    task_plan = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1",
         "depends_on": [], "input_from": [], "order": 1, "status": "completed"},
    ]
    state = _make_state(
        task_plan=task_plan,
        task_results={"t1": {"query_results": []}},
        replan_count=mock_config.max_replan,
    )

    result = await replanner(state, llm=llm, app_config=mock_config)

    # 상한 가드: 현재 결과로 종료
    assert result["needs_replan"] is False
    assert result["current_node"] == "replanner"
    assert "task_plan" not in result
    # LLM 호출 없음
    llm.ainvoke.assert_not_called()


# ──────────────────────────────────────────────
# 5. test_replanner_parse_failure_terminates
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replanner_parse_failure_terminates(mock_config):
    """LLM이 비JSON 텍스트 반환(파싱 불가) → needs_replan=False 보수적 종료."""
    llm = _mock_llm("이것은 JSON이 아닌 평범한 텍스트 응답입니다")
    task_plan = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1",
         "depends_on": [], "input_from": [], "order": 1, "status": "completed"},
    ]
    state = _make_state(
        task_plan=task_plan,
        task_results={"t1": {"query_results": [{"hostname": "web-01"}]}},
    )

    result = await replanner(state, llm=llm, app_config=mock_config)

    assert result["needs_replan"] is False
    assert result["current_node"] == "replanner"
    assert "task_plan" not in result


@pytest.mark.asyncio
async def test_replanner_exception_terminates(mock_config):
    """LLM ainvoke가 예외 발생 → needs_replan=False 보수적 종료 (파싱 실패 변형)."""
    llm = AsyncMock()
    llm.ainvoke.side_effect = RuntimeError("LLM 호출 실패")
    task_plan = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1",
         "depends_on": [], "input_from": [], "order": 1, "status": "completed"},
    ]
    state = _make_state(
        task_plan=task_plan,
        task_results={"t1": {"query_results": [{"hostname": "web-01"}]}},
    )

    result = await replanner(state, llm=llm, app_config=mock_config)

    assert result["needs_replan"] is False
    assert result["current_node"] == "replanner"
    assert "task_plan" not in result


# ──────────────────────────────────────────────
# 6. test_orchestrator_skips_completed
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orchestrator_skips_completed(mock_config):
    """[t1(completed)+t2(pending)] + t1 결과 시드 → t1 handler 미호출, t2만 실행, t1 결과 보존."""
    async def t1_handler(task, isolated, **kwargs):
        # t1은 이미 completed이므로 절대 호출되면 안 됨
        raise AssertionError("완료된 t1 handler가 재실행됨 (R-A2 위반)")

    async def t2_handler(task, isolated, **kwargs):
        return {"final_response": "t2 신규 결과"}

    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1",
         "depends_on": [], "input_from": [], "order": 1, "status": "completed"},
        {"task_id": "t2", "agent": "alarm_query", "sub_query": "q2",
         "depends_on": [], "input_from": [], "order": 2, "status": "pending"},
    ]
    # t1 결과를 시드로 주입 (재진입 상황 모사)
    state = _make_state(
        task_plan=tasks,
        task_results={"t1": {"final_response": "t1 기존 결과"}},
    )

    patched = _registry_with({
        "data_query": AsyncMock(side_effect=t1_handler),
        "alarm_query": AsyncMock(side_effect=t2_handler),
    })
    with patch.dict(SUBAGENT_REGISTRY, patched):
        result = await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)

    by_id = {t["task_id"]: t for t in result["task_plan"]}
    # t1은 재실행되지 않아 completed 유지
    assert by_id["t1"]["status"] == "completed"
    # t2는 신규 실행되어 completed
    assert by_id["t2"]["status"] == "completed"
    # t1 기존 결과 보존 + t2 신규 결과
    assert result["task_results"]["t1"]["final_response"] == "t1 기존 결과"
    assert result["task_results"]["t2"]["final_response"] == "t2 신규 결과"


# ──────────────────────────────────────────────
# 7. test_followup_input_from_injection
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_followup_input_from_injection(mock_config):
    """t1(completed, query_results 시드)+t2(pending, input_from=[t1]) → t2 isolated에 prior_rows[t1] 주입."""
    captured = {}

    async def t1_handler(task, isolated, **kwargs):
        raise AssertionError("완료된 t1이 재실행됨")

    async def t2_handler(task, isolated, **kwargs):
        captured["isolated"] = isolated
        return {"final_response": "t2 완료"}

    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "장애 서버 조회",
         "depends_on": [], "input_from": [], "order": 1, "status": "completed"},
        {"task_id": "t2", "agent": "alarm_query", "sub_query": "선행 서버 알람 조회",
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2, "status": "pending"},
    ]
    # t1 결과(query_results)를 results 시드로 미리 주입 (completed 상태이므로)
    t1_rows = [{"hostname": f"srv-{i}", "cpu": float(i)} for i in range(5)]
    state = _make_state(
        task_plan=tasks,
        task_results={"t1": {"query_results": t1_rows}},
    )

    patched = _registry_with({
        "data_query": AsyncMock(side_effect=t1_handler),
        "alarm_query": AsyncMock(side_effect=t2_handler),
    })
    with patch.dict(SUBAGENT_REGISTRY, patched):
        result = await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)

    isolated = captured["isolated"]
    assert "prior_rows" in isolated
    assert "t1" in isolated["prior_rows"]
    rows = isolated["prior_rows"]["t1"]
    assert len(rows) == 5
    # 식별 키(hostname)만 유지 (cpu 제거 — R-12)
    assert all(set(r.keys()) == {"hostname"} for r in rows)
    # t1 결과 보존, t2 신규 결과
    assert result["task_results"]["t1"]["query_results"] == t1_rows
    assert result["task_results"]["t2"]["final_response"] == "t2 완료"


# ──────────────────────────────────────────────
# 8. test_pattern12_no_replan_regression
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pattern12_no_replan_regression(mock_config):
    """정적 단일/복합(①②) → replanner가 needs_followup=false면 task_plan 미변경, needs_replan=False."""
    content = json.dumps(
        {"needs_followup": False, "reason": "정적 계획으로 충분", "new_tasks": []},
        ensure_ascii=False,
    )

    # 패턴① 단일 task
    single_plan = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "단일 조회",
         "depends_on": [], "input_from": [], "order": 1, "status": "completed"},
    ]
    state_single = _make_state(
        task_plan=single_plan,
        task_results={"t1": {"query_results": [{"hostname": "web-01"}]}},
    )
    result_single = await replanner(state_single, llm=_mock_llm(content), app_config=mock_config)
    assert result_single["needs_replan"] is False
    assert "task_plan" not in result_single  # 미변경
    # 원본 task_plan 그대로 유지
    assert len(state_single["task_plan"]) == 1

    # 패턴② 정적 복합 task (데이터 의존)
    composite_plan = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "장애 서버 조회",
         "depends_on": [], "input_from": [], "order": 1, "status": "completed"},
        {"task_id": "t2", "agent": "data_query", "sub_query": "해당 서버 CPU 조회",
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2, "status": "completed"},
    ]
    state_composite = _make_state(
        task_plan=composite_plan,
        task_results={
            "t1": {"query_results": [{"hostname": "web-01"}]},
            "t2": {"query_results": [{"hostname": "web-01", "cpu": 90.0}]},
        },
    )
    result_composite = await replanner(state_composite, llm=_mock_llm(content), app_config=mock_config)
    assert result_composite["needs_replan"] is False
    assert "task_plan" not in result_composite
    assert len(state_composite["task_plan"]) == 2


# ──────────────────────────────────────────────
# 9. test_graph_loop_shape
# ──────────────────────────────────────────────

def _build_orchestration_config() -> AppConfig:
    """deepagent 오케스트레이션 활성 config를 만든다 (test_graph_orchestration 패턴)."""
    os.environ.pop("ENABLE_DEEPAGENT_ORCHESTRATION", None)
    os.environ.pop("ENABLE_SEMANTIC_ROUTING", None)
    cfg = AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db", mcp_call_timeout=60),
        query=QueryConfig(max_retry_count=3, default_limit=1000),
        security=SecurityConfig(sensitive_columns=["password"], mask_pattern="***"),
        server=ServerConfig(host="0.0.0.0", port=8000),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )
    cfg.enable_deepagent_orchestration = True
    cfg.enable_semantic_routing = False
    # 트랙 B는 범위 밖 — .env의 ENABLE_DEEPAGENTS_PACKAGE 누수로 deep_agent 경로가
    # 선택되면 replanner 루프 노드가 미등록되어 오탐한다(D-037 Decision 2 패턴).
    cfg.enable_deepagents_package = False
    return cfg


def _node_names(compiled) -> set[str]:
    return set(compiled.get_graph().nodes.keys())


def _edge_set(compiled) -> set[tuple[str, str]]:
    """컴파일된 그래프의 (source, target) 엣지 집합을 반환한다."""
    return {(e.source, e.target) for e in compiled.get_graph().edges}


def test_graph_loop_shape():
    """orchestration on → replanner 노드 + 루프/종료 엣지 구성, 라우팅 함수 단위 검증."""
    cfg = _build_orchestration_config()
    compiled = build_graph(cfg)

    names = _node_names(compiled)
    edges = _edge_set(compiled)

    # replanner 노드 존재
    assert "replanner" in names

    # 루프/종료 엣지 존재
    assert ("agent_orchestrator", "replanner") in edges
    assert ("replanner", "agent_orchestrator") in edges
    assert ("replanner", "result_aggregator") in edges
    # result_aggregator → END (langgraph는 END를 __end__로 표기)
    assert ("result_aggregator", "__end__") in edges

    # route_after_orchestrator: 항상 replanner
    assert route_after_orchestrator({}) == "replanner"
    assert route_after_orchestrator({"needs_replan": True}) == "replanner"

    # route_after_replanner: needs_replan True → agent_orchestrator, False → result_aggregator
    assert route_after_replanner({"needs_replan": True}) == "agent_orchestrator"
    assert route_after_replanner({"needs_replan": False}) == "result_aggregator"
    # 기본값(키 없음) → 종료
    assert route_after_replanner({}) == "result_aggregator"


# ──────────────────────────────────────────────
# 10. test_arch_check
# ──────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARCH_SCRIPT = PROJECT_ROOT / "scripts" / "arch_check.py"


def _load_arch_check():
    """scripts/arch_check.py를 모듈로 동적 로드한다 (test_arch.py 방식)."""
    if "arch_check" in sys.modules:
        return sys.modules["arch_check"]
    spec = importlib.util.spec_from_file_location("arch_check", ARCH_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["arch_check"] = module
    spec.loader.exec_module(module)
    return module


def test_arch_check():
    """replanner 추가 후 arch_check error 위반 0건 (--ci exit 0 + check_project)."""
    # 1) CI 모드 exit 0
    result = subprocess.run(
        [sys.executable, str(ARCH_SCRIPT), "--ci"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"arch_check --ci 실패(returncode={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # 2) check_project 직접 호출 → error severity 위반 0건
    arch = _load_arch_check()
    proj = arch.check_project(PROJECT_ROOT)
    errors = [v for v in proj.violations if v.severity == "error"]
    assert not errors, "아키텍처 error 위반 발견:\n" + "\n".join(
        f"  {v.file}:{v.line} {v.from_layer}->{v.to_layer} ({v.reason})" for v in errors
    )
