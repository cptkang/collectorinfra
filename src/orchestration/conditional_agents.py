"""조건부 처리기 결합 — 고정 목록(`SUBAGENT_REGISTRY`) 밖, 활성일 때만 합류하는 처리기들
(plans/127 §4.3 ①).

`plans/125`가 만든 결합(`apm_query.active_extra_subagents` — APM 전용)을 소스 무관으로 넓힌다.
처리기 하나(`apm_query` · `doc_query`)는 각자 활성 판정·보기 어휘·분해 절을 소유하고,
이 모듈은 결합과 정제 두 가지만 한 자리에 모은다:

- `active_conditional_agents` — 분해 목록 · `resolve_subagent`(2단 오케스트레이터 · 3단 계획 루프) ·
  재계획 어휘가 같은 함수를 부른다. **둘 다 비활성이면 빈 dict** — 종전과 같다(비활성 바이트 불변).
- `sanitize_task_views` — 처리기마다 자기 닫힌 어휘로 `views`를 정제하고, 다른 담당의 `views`는
  떼어 낸다. 문서 task 의 의존(`depends_on`·`input_from`)은 끊고 사유를 남긴다(간선 없음 · G-10).

분해 프롬프트 삽입은 처리기별 렌더 캐시가 있는 `intent_planner._planner_system_prompt`가 한다.

APM 쪽은 모듈 속성으로 부른다(`apm_query.active_extra_subagents`) — 125 테스트가 그 이름을 쓴다.

계층: orchestration.
"""

from __future__ import annotations

from typing import Any

from src.orchestration import apm_query, doc_query
from src.orchestration.subagents import SubAgentSpec


def active_conditional_agents(app_config: Any) -> dict[str, SubAgentSpec]:
    """활성인 조건부 처리기(선언 순서: APM → 문서) — 비활성이면 빈 dict."""
    agents = dict(apm_query.active_extra_subagents(app_config))
    agents.update(doc_query.active_doc_subagents(app_config))
    return agents


def sanitize_task_views(result: dict[str, Any], app_config: Any) -> list[str]:
    """분해 결과의 `views`·문서 task 의존을 정제한다(분해의 마지막 한 곳 · 모든 경로 공통).

    Returns:
        의존을 끊은 문서 task id(경과 노트용 — 없으면 빈 목록)
    """
    tasks = [t for t in result.get("tasks") or [] if isinstance(t, dict)]
    doc_ids = {str(t.get("task_id")) for t in tasks if t.get("agent") == doc_query.DOC_QUERY_AGENT}
    cut: list[str] = []
    for task in tasks:
        raw = task.pop("views", None)
        agent = task.get("agent")
        if agent == apm_query.APM_QUERY_AGENT:
            task["views"] = apm_query.sanitize_views(raw)
        elif agent == doc_query.DOC_QUERY_AGENT:
            task["views"] = doc_query.sanitize_views(raw, app_config)
        if not doc_ids:
            continue
        tid = str(task.get("task_id"))
        deps = [d for d in task.get("depends_on") or [] if tid in doc_ids or str(d) in doc_ids]
        feeds = [d for d in task.get("input_from") or [] if tid in doc_ids or str(d) in doc_ids]
        if deps or feeds:
            task["depends_on"] = [d for d in task.get("depends_on") or [] if d not in deps]
            task["input_from"] = [d for d in task.get("input_from") or [] if d not in feeds]
            cut.append(tid)
    return cut


__all__ = ["active_conditional_agents", "sanitize_task_views"]
