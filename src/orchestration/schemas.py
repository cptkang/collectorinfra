"""DAG 분해 출력 계약 (Plan 79 트랙 E-3a / D-169).

`intent_planner._llm_decompose`가 LLM에서 받는 구조의 타입 계약이다. 종전에는
`list[dict]`(`state.py:246` `task_plan`)이라 키 오타·타입 불일치가 런타임까지 살아남았다.

계층: orchestration.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional, get_args

from pydantic import BaseModel, Field, create_model

from src.orchestration.subagents import SUBAGENT_REGISTRY


def allowed_agents() -> frozenset[str]:
    """TaskSpec.agent 허용값 — **`SUBAGENT_REGISTRY`가 정본**이다(D-053 사본 금지).

    `fault_diagnosis`는 여기 없다: 서브에이전트가 아니라 그래프 노드다.
    레지스트리에 agent가 추가되면 이 계약도 자동으로 따라간다.
    """
    return frozenset(SUBAGENT_REGISTRY.keys())


#: 닫힌 어휘 밖 담당을 맞췄다는 task 표지(plans/121 TP-1.7) — 계획 요약이 건수만 센다(U-18).
AGENT_FALLBACK_KEY = "agent_fallback"


def close_agent_vocabulary(
    task: dict[str, Any], extra_agents: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """task의 담당을 닫힌 어휘로 맞춘다 — **task 단위**, 현행 의미 그대로(plans/121 TP-1.7).

    JSON 분해 경로(`structured_output_backend=none`)와 재계획 신규 task는 담당 이름을 검증하지 않아
    목록 밖 이름이 계획에 남았다. 디스패치는 그런 task를 폴백 handler로 보냈으므로
    (`agent_orchestrator._fallback_spec`) 폴백 담당으로 적는 것이 그 의미를 바꾸지 않는다.
    `data_query`로 바꾸거나 계획 전체를 폴백하지 않는다(유효 task까지 잃는다). 목록 안이면
    입력 그대로(in-place)다. `extra_agents`는 활성인 조건부 처리기(plans/125 A-3 — 예
    `apm_query`)다.
    """
    agent = task.get("agent")
    if agent in allowed_agents() or agent in extra_agents:
        return task
    fallback = next(
        (name for name, spec in SUBAGENT_REGISTRY.items() if spec.fallback), "general_inference"
    )
    task["agent"] = fallback
    task[AGENT_FALLBACK_KEY] = True
    return task


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
    # 답변 영역·요청 소스 칸(plans/132 N-5 — 항상 켜짐 · 플래그 없음). 코드 값(카탈로그 대조)은
    # 스키마가 아니라 분해 후 정제(`intent_planner._sanitize_task_areas`)가 검증한다.
    areas: list[str] = Field(default_factory=list)
    requested_source: str = ""

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


# ── task 프레임 계약(plans/111 C-3 · `COMPOSITE_TASK_FRAME_ENABLED`) ──
# 켜졌을 때만 쓰는 서브클래스다(위 소유 서브클래스와 같은 이유 — off 스키마 불변).
# LLM은 `sub_query`를 비워 두고 원문 조각 `spans`만 낸다. `sub_query`는 분해 후 정제
# (`intent_planner._apply_task_frames`)가 조각으로 만든다. 소유 플래그와 함께 켜질 수 있어
# `capability`를 물려받는다(소유 off면 빈 문자열 그대로 — 소유 적용 없음).


class SpanTaskSpec(OwnershipTaskSpec):
    """sub-task + 원문 조각(자유문 `sub_query` 대신)."""

    sub_query: str = ""
    spans: list[str] = Field(default_factory=list)


class SpanDecomposedPlan(DecomposedPlan):
    """`_llm_decompose` 출력 — task마다 원문 조각을 싣는다."""

    tasks: list[SpanTaskSpec] = Field(default_factory=list)


# ── 비SQL 처리기 보기 슬롯(plans/125 A-5 · G-3 (a)) ──
# 비SQL 처리기(예 `apm_query`)가 활성일 때만 쓰는 파생 모델이다 — 기존 모델에 필드를 더하면 비활성
# 배포의 구조화 출력 스키마가 바뀌므로(위 소유·프레임 서브클래스와 같은 이유) 선택된 모델에서
# 파생한다. 보기 값(닫힌 어휘)은 스키마가 아니라 분해 후 정제(`intent_planner._sanitize_task_views`)가
# 검증한다.


@lru_cache(maxsize=8)
def views_plan_model(
    plan_model: type[DecomposedPlan], extra_agents: tuple[str, ...], view_args: bool = False,
) -> type[DecomposedPlan]:
    """`plan_model`의 task 에 `views: list[str]`을 더하고 `extra_agents`를 담당 어휘에 더한 모델.

    `view_args`가 참이면(보기 선택 조건을 쓰는 처리기가 활성 — plans/134 M-3) task 에
    `view_args: {보기 id: {조건 이름: 값}}`도 더한다. 값 검증은 처리기가 보기 선언으로 한다.
    같은 때 대상 텍스트 `targets: [{text, kind}]`(plans/130 M-1)도 더한다 — 항목 형태는 느슨하게
    받고(항목 하나가 형식 밖이라고 분해 전체를 버리지 않는다) 정제는 `apm_query.sanitize_targets`가
    한다.
    """
    task_model = get_args(plan_model.model_fields["tasks"].annotation)[0]
    allowed = allowed_agents() | frozenset(extra_agents)

    class ViewsTaskSpec(task_model):  # type: ignore[valid-type,misc]
        views: list[str] = Field(default_factory=list)

        def model_post_init(self, _ctx: Any) -> None:  # noqa: D105
            if self.agent not in allowed:
                raise ValueError(
                    f"agent가 알려진 서브에이전트가 아닙니다: {self.agent!r} "
                    f"(허용: {sorted(allowed)})"
                )

    task_cls: type[Any] = ViewsTaskSpec
    if view_args:
        class ViewArgsTaskSpec(ViewsTaskSpec):
            view_args: dict[str, dict[str, Any]] = Field(default_factory=dict)
            targets: list[Any] = Field(default_factory=list)

        task_cls = ViewArgsTaskSpec
    task_cls.__name__ = f"Views{task_model.__name__}"
    return create_model(
        f"Views{plan_model.__name__}",
        __base__=plan_model,
        tasks=(list[task_cls], Field(default_factory=list)),  # type: ignore[valid-type]
    )


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
