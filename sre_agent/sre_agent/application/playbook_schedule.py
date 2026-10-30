"""플레이북 스텝 실행 의존 (plans/200 I-1 · D-341).

현행 단계 분할(`playbook_runner.plan_stages`)은 YAML 순서의 연속 구간을 장벽으로 끊는다. 이 모듈은
스텝마다 **실제로 기다려야 하는 앞 스텝**만 계산한다(DAG 실행기 W2의 입력 · 이 모듈만으로는 실행
동작 무변경). 순수 함수이며 LLM·I/O가 없다.

의존 = 다음의 합집합이고, **모두 YAML 순서상 이 스텝보다 앞선 스텝만** 본다. 뒤 스텝 참조는 현행처럼
판정 불가(UNKNOWN)로 남기 때문에 의존에 넣지 않는다 — 그래서 YAML 순서가 늘 유효한 위상 순서다.

- `when`이 참조하는 스텝 ID(`S2.status` · 보조 사실 `S2.pool`도 루트는 스텝 ID)
- `when`이 참조하는 판독의 입력 액션(`READING_INPUTS`)을 담당하는 앞 스텝
- 스텝 함수가 `ctx.prior`에서 읽는 액션(`PRIOR_INPUTS`)을 담당하는 앞 스텝
- 앞선 대상 확정 스텝(`SCOPE_ACTIONS` — 뒤 스텝 전부가 그 범위를 입력으로 쓴다). 대상 확정 스텝도
  앞선 대상 확정 스텝은 기다린다(단계 실행처럼 앞 대상 확정 결과 범위를 받는다 · W2 교정).

단계 끝 해석의 판독 기준(`narration_basis`)은 실행 의존과 따로 둔다 — 조상에 「이 스텝이
담당하는 판독(`READING_ACTIONS[r]`에 이 스텝 액션이 있는 r)의 입력 액션(`READING_INPUTS[r]`)을
맡은 앞 스텝」을 더한 집합이다. 실행은 기다리지 않고, 해석만 그 스텝들이 정착한 뒤로 미룬다
(plans/200 W2 교정 · Q2).

S9(`synthesize`)는 스케줄 대상 밖이다(`plan_stages`와 같음) — 의존 계산에서 빠진다.
"""

from __future__ import annotations

import re

from sre_agent.application.playbook_steps import PRIOR_INPUTS, SCOPE_ACTIONS
from sre_agent.domain.playbook import Playbook, StepSpec
from sre_agent.domain.playbook_expr import READING_NAMES
from sre_agent.domain.playbook_readings import READING_ACTIONS, READING_INPUTS

#: 종합 액션(S9) — 실행기가 수집 뒤 따로 채운다.
_SYNTHESIZE = "synthesize"

# 판독 참조 규칙의 정본(따옴표 문자열 · `S2.scope` 같은 하위 경로 제외) — 실행기
# (`playbook_runner`)가 이 함수를 import해 단계 분할에 쓴다(plans/200 W2 — 실행기 사본 제거 ·
# 순환을 피해 이 모듈은 실행기를 import하지 않는다).
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_ROOT_NAME = re.compile(r"(?<![.\w])([A-Za-z_]\w*)")


def reading_refs(when: str | None) -> frozenset[str]:
    """`when`이 경로 루트로 참조하는 판독 이름."""
    if not when:
        return frozenset()
    return frozenset(_ROOT_NAME.findall(_QUOTED.sub(" ", when))) & READING_NAMES


def deps_of(step: StepSpec, playbook: Playbook) -> frozenset[str]:
    """스텝이 시작 전에 끝나 있어야 하는 앞 스텝 ID 집합(직접 의존 · S9는 빈 집합)."""
    if step.action == _SYNTHESIZE:
        return frozenset()
    earlier: list[StepSpec] = []
    for s in playbook.steps:
        if s.id == step.id:
            break
        if s.action != _SYNTHESIZE:
            earlier.append(s)
    refs = step.when_expr.step_refs() if step.when_expr is not None else frozenset()
    actions = {a for r in reading_refs(step.when) for a in READING_INPUTS[r]}
    actions |= PRIOR_INPUTS.get(step.action, frozenset())
    return frozenset(
        s.id
        for s in earlier
        if s.id in refs or s.action in actions or s.action in SCOPE_ACTIONS
    )


def ancestors(playbook: Playbook) -> dict[str, frozenset[str]]:
    """스텝 ID → 전이 의존(조상) 집합 — S9 제외 · YAML 순서가 위상 순서라 한 번 훑어 닫는다."""
    out: dict[str, frozenset[str]] = {}
    for step in playbook.steps:
        if step.action == _SYNTHESIZE:
            continue
        direct = deps_of(step, playbook)
        out[step.id] = direct.union(*(out[d] for d in direct))
    return out


def narration_basis(playbook: Playbook) -> dict[str, frozenset[str]]:
    """스텝 ID → 단계 끝 해석의 판독 기준 스텝 집합(S9 제외).

    조상(`ancestors`) ∪ 이 스텝이 담당하는 판독의 입력 액션(`READING_INPUTS`)을 맡은 YAML상 앞 스텝.
    의존 기반 실행에서 자기 판독(예: host.cpu S2의 cpu_owner ← S4·S6)을 단계 실행과 같은 입력으로
    계산하게 한다 — 실행 의존(`deps_of`)에는 넣지 않는다(실행 단축 유지).
    """
    anc = ancestors(playbook)
    out: dict[str, frozenset[str]] = {}
    earlier: list[StepSpec] = []
    for step in playbook.steps:
        if step.action == _SYNTHESIZE:
            continue
        actions = {
            a
            for r, owners in READING_ACTIONS.items()
            if step.action in owners
            for a in READING_INPUTS[r]
        }
        out[step.id] = anc[step.id] | {s.id for s in earlier if s.action in actions}
        earlier.append(step)
    return out


__all__ = ["ancestors", "deps_of", "narration_basis", "reading_refs"]
