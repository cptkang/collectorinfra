"""처리기 격리 입력의 순수 조각 (plans/166 O4 · 종전 정의처 `subagents`).

`subagents.make_isolated_input`(S3 부분 격리)이 쓰는 스코프 함수·선행 결과 행 꺼내기를 모은다.
설정(`load_config`)이나 처리기 레지스트리를 읽는 조각(`make_isolated_input` ·
`extract_identity_rows` · `_history_for_agent` · `_build_prior_targets_for_task`)은 테스트가
`subagents.load_config`를 바꿔 끼우므로 `subagents`에 남는다(D-337 ②). 여기 이름은 `subagents`가
모두 재노출한다.
"""

from __future__ import annotations

import logging
from typing import Any

from src.domain.query_time import QueryTime, resolve_task_time

# 로그 이름은 이동 전 그대로 둔다(로그 필터·수집 규칙 불변 — plans/166 Move-only).
logger = logging.getLogger("src.orchestration.subagents")


def _scope_parsed_requirements(state: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """parsed_requirements를 sub-task 스코프로 좁힌 사본을 만든다 (D-094).

    original_query만 task의 sub_query로 교체한다(사본 — 원본 state 비오염).
    time_range/query_targets 등 구조화 필드는 전체 질의 파싱값을 그대로 유지한다
    (SQL 생성 프롬프트의 보조 맥락 — 예: 기간 202606). 단일 task(sub_query==전체
    질의)면 실질 변화가 없다.

    Args:
        state: 전체 에이전트 상태
        task: 현재 TaskSpec

    Returns:
        스코프 적용된 parsed_requirements 사본
    """
    parsed = dict(state.get("parsed_requirements", {}) or {})
    sub_query = task.get("sub_query")
    if sub_query:
        parsed["original_query"] = sub_query
    return parsed


def request_query_text(state: dict[str, Any]) -> str:
    """요청 원문 — 시간 해석이 읽은 사용자 문장(task 기간 원문 대조의 기준 · plans/122 T-4).

    격리 입력의 `original_user_query`(교체 전 원문) → 그래프 `user_query`(그래프 노드는 원문을
    덮지 않는다) → `parsed_requirements.original_query`(1단 ambient — `user_query`가 없다 ·
    `input_parser`가 원문을 복사한다) 순. 없으면 빈 문자열.
    """
    parsed = state.get("parsed_requirements") or {}
    return str(
        state.get("original_user_query") or state.get("user_query")
        or (parsed.get("original_query") if isinstance(parsed, dict) else None) or ""
    )


def scope_time_resolution(
    state: dict[str, Any], task: dict[str, Any], task_text: str | None = None,
    *, original: str | None = None,
) -> dict[str, Any] | None:
    """요청 시간 해석을 task 범위로 좁힌 state 값을 만든다 (plans/122 §10.3 「2단 task」 · D-309).

    원문 해석(state `time_resolution`)이 전역 기본이다. task 문장에 **명시 기간**이 있고 그 기간
    표현이 **사용자 원문에도 있을 때만** 그 문장으로 다시 해석한다(기준 시각은 원문 해석과 같다 —
    `resolve_task_time(original=)`). 분해 LLM이 원문에 없는 기간을 task 문장에 지어 넣으면 원문
    해석을 유지한다 — 지어낸 기간이 명시 기간(엄격 검증)으로 승격되지 않게 한다(리뷰 상태 계약).
    원문을 알 수 없으면(빈 문자열) task별 해석을 하지 않는다. 하류 노드는 task 재작성문
    (`user_query`·`parsed_requirements.original_query`)을 다시 해석하지 않고 이 값을 읽는다 —
    §10.2 ⑦ 「2단은 재작성문에서 해석」의 단일 출처 교정이다.

    원문 해석이 없으면(플래그 off · 옛 체크포인트) None — 소비처는 종전 경로를 탄다.

    Args:
        state: 전체 에이전트 상태(원문 해석이 실린 상태)
        task: 현재 TaskSpec
        task_text: task 문장. 미지정이면 `task["sub_query"]`(3단은 조각으로 찍은 문장을 넘긴다)
        original: 사용자 원문. 미지정이면 `request_query_text(state)`(3단은 서브그래프 밖에서
            잡아 둔 원문을 넘긴다 — 서브그래프의 `user_query`는 task 문장이다)
    """
    raw = state.get("time_resolution")
    base = QueryTime.from_state(raw)
    if base is None:
        return None
    text = str(task.get("sub_query") or "") if task_text is None else str(task_text)
    source_text = request_query_text(state) if original is None else str(original)
    scoped = resolve_task_time(text, base, original=source_text) if text.strip() else base
    # task 해석이 원문 해석과 같으면(원문 그대로인 단일 task 등) 원문 값을 그대로 쓴다 — 슬롯
    # 처리 결과(`slot_status`)까지 원문 해석과 같은 값이 하류·하네스에 닿는다.
    if scoped is not base and (scoped.metric, scoped.event) == (base.metric, base.event):
        scoped = base
    res = scoped.metric
    logger.info(
        "task 시간 해석(plans/122 T-4): task=%s scope=%s source=%s label=%s",
        task.get("task_id"), "request" if scoped is base else "task",
        res.source if res is not None else f"clarify:{scoped.clarify}",
        res.label() if res is not None else "-",
    )
    return raw if scoped is base else scoped.to_state()


# 선행 결과를 **조사 대상 집합**으로 소비하는 agent (Plan 78 W1-2).
_TARGET_CONSUMER_AGENTS: tuple[str, ...] = ("process_query", "fault_diagnosis")
# 선행 결과 행의 **참조 칸**(profile_ref·active_ref·guid)을 순번으로 고르는 agent(plans/134 M-6).
_ROW_REF_CONSUMER_AGENTS: tuple[str, ...] = ("apm_query",)


def _row_ref_dependencies(task: dict[str, Any], state: dict[str, Any]) -> list[str]:
    """`depends_on`이 가리키는 선행 task 중 참조 칸 행을 내는 task(같은 처리기 — plans/134 V-5)."""
    agents = {str(t.get("task_id")): t.get("agent")
              for t in state.get("task_plan") or [] if isinstance(t, dict)}
    return [str(d) for d in task.get("depends_on") or []
            if agents.get(str(d)) in _ROW_REF_CONSUMER_AGENTS]


def _prior_result_rows(res: dict[str, Any]) -> list[dict[str, Any]]:
    """선행 task 결과에서 행 목록을 꺼낸다(결과 형태 3종 방어 — prior_rows 로직과 동일 규약)."""
    rows = res.get("rows")
    if rows is None:
        rows = res.get("query_results")
    if rows is None:
        organized = res.get("organized_data") or {}
        rows = organized.get("rows", [])
    return rows or []
