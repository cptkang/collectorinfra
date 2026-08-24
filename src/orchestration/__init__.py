"""deepagents 기반 의도 분해 오케스트레이션 패키지 (Plan 48, Phase 1).

사용자 질의를 sub-task로 분해(intent_planner)하고, registry 기반으로 각 task를
순차/병렬/데이터-의존으로 실행(agent_orchestrator)한 뒤 결과를 통합(result_aggregator)한다.

ENABLE_INTENT_ORCHESTRATION 플래그가 활성일 때만 그래프에 연결된다
(사다리 2단 — 상위 단이 성립하면 배선되지 않는다. docs/21_orchestration_ladder.md).
"""

from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.deep_agent import (
    build_deep_agent,
    orchestrator_available,
    run_deep_agent,
    select_orchestration_backend,
    vllm_healthy,
)
from src.orchestration.deepagents_tools import build_tools
from src.orchestration.intent_planner import intent_planner
from src.orchestration.replanner import replanner
from src.orchestration.result_aggregator import result_aggregator
from src.orchestration.subagents import (
    SUBAGENT_REGISTRY,
    SubAgentSpec,
    classify_dbs,
)

__all__ = [
    "intent_planner",
    "agent_orchestrator",
    "replanner",
    "result_aggregator",
    "SUBAGENT_REGISTRY",
    "SubAgentSpec",
    "classify_dbs",
    "build_tools",
    "build_deep_agent",
    "run_deep_agent",
    "select_orchestration_backend",
    "orchestrator_available",
    "vllm_healthy",
]
