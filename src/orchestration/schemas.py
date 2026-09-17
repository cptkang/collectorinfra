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


# ── 답변 영역 소유(plans/102 X-7 · `ROUTER_CAPABILITY_OWNERSHIP_ENABLED`) ──
# 켜졌을 때만 쓰는 서브클래스다. instructor 백엔드는 응답 모델 스키마를 프롬프트에 싣는다.
# 기존 모델에 필드를 더하면 플래그 off에서도 스키마가 바뀌므로 필드는 서브클래스에만 둔다.
# 코드 값(카탈로그 대조)은 스키마가 아니라 분해 후 정제(`intent_planner._llm_decompose`)가
# 검증한다 — 모르는 코드는 빈 문자열(소유 적용 없음)로 떨어진다.


class OwnershipTaskSpec(TaskSpec):
    """sub-task + 그 task가 답할 답변 영역 코드 하나(없으면 빈 문자열)."""

    capability: str = ""


class OwnershipDecomposedPlan(DecomposedPlan):
    """`_llm_decompose` 출력 — task마다 답변 영역 코드를 싣는다."""

    tasks: list[OwnershipTaskSpec] = Field(default_factory=list)


def validate_plan_dag(tasks: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """task DAG를 결정적으로 검증한다 (D-203 · plans/88 §4.8). backend 무관 · LLM 0회.

    `model_post_init`은 agent 이름만 검증한다 — DAG 규칙(중복 id·미존재 참조·`input_from ⊄ depends_on`·
    순환)은 여기서 본다. `agent_orchestrator.topological_levels`가 순환·누락을 "한 레벨로 안전 처리"
    하므로 검증 없이는 순차가 **조용히 병렬**로 바뀐다.

    Returns:
        (보정된 tasks 사본, 보정 불가 위반 목록, 자동 보정 기록)
    """
    fixed: list[dict] = [dict(t) for t in tasks if isinstance(t, dict)]
    violations: list[str] = []
    fixes: list[str] = []
    ids = [str(t.get("task_id") or "") for t in fixed]
    known = set(ids)

    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        violations.append(f"task_id 중복: {', '.join(dup)}")
    if "" in known:
        violations.append("task_id가 비어 있는 task가 있습니다")

    for t in fixed:
        tid = str(t.get("task_id") or "")
        deps = [str(d) for d in (t.get("depends_on") or []) if d]
        inputs = [str(d) for d in (t.get("input_from") or []) if d]
        for ref in deps + inputs:
            if ref == tid:
                violations.append(f"{tid}: 자기 참조")
            elif ref not in known:
                violations.append(f"{tid}: 존재하지 않는 task 참조 {ref!r}")
        # 데이터 의존은 순서 의존을 함의한다(TaskSpec docstring) — 빠졌으면 보정한다.
        missing = [i for i in inputs if i not in deps]
        if missing:
            t["depends_on"] = deps + missing
            fixes.append(f"{tid}: input_from {missing} → depends_on에 추가")
        else:
            t["depends_on"] = deps
        t["input_from"] = inputs

    # 순환 — Kahn 잔여
    if not violations:
        remaining = {str(t["task_id"]): {d for d in t["depends_on"]} for t in fixed}
        placed: set[str] = set()
        while remaining:
            ready = [k for k, d in remaining.items() if d <= placed]
            if not ready:
                violations.append(f"순환 의존: {', '.join(sorted(remaining))}")
                break
            for k in ready:
                placed.add(k)
                remaining.pop(k)
    return fixed, violations, fixes
