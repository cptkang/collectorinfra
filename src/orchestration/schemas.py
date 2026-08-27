"""DAG 분해 출력 계약 (Plan 79 트랙 E-3a / D-169).

`intent_planner._llm_decompose`가 LLM에서 받는 구조의 타입 계약이다. 종전에는
`list[dict]`(`state.py:246` `task_plan`)이라 키 오타·타입 불일치가 런타임까지 살아남았다.

계층: orchestration.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.orchestration.subagents import SUBAGENT_REGISTRY


def allowed_agents() -> frozenset[str]:
    """TaskSpec.agent 허용값 — **`SUBAGENT_REGISTRY`가 정본**이다(D-053 사본 금지).

    `fault_diagnosis`는 여기 없다: 서브에이전트가 아니라 그래프 노드다.
    레지스트리에 agent가 추가되면 이 계약도 자동으로 따라간다.
    """
    return frozenset(SUBAGENT_REGISTRY.keys())


class TaskSpec(BaseModel):
    """분해된 sub-task 하나.

    `depends_on`(실행 순서)과 `input_from`(데이터 의존)은 다르다 — 섞으면 선행 결과가
    전달되지 않거나 불필요한 직렬화가 생긴다.
    """

    task_id: str
    agent: str
    sub_query: str
    depends_on: list[str] = Field(default_factory=list)
    input_from: list[str] = Field(default_factory=list)
    order: int = 1

    def model_post_init(self, _ctx) -> None:  # noqa: D105
        if self.agent not in allowed_agents():
            raise ValueError(
                f"agent가 알려진 서브에이전트가 아닙니다: {self.agent!r} "
                f"(허용: {sorted(allowed_agents())})"
            )


class DecomposedPlan(BaseModel):
    """`_llm_decompose`의 LLM 출력 전체."""

    tasks: list[TaskSpec] = Field(default_factory=list)
    clarification_needed: Optional[dict] = None
