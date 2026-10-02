"""조건부 처리기 결합 — 고정 목록(`SUBAGENT_REGISTRY`) 밖, 활성일 때만 합류하는 처리기들
(plans/127 §4.3 ①).

`plans/125`가 만든 결합(`apm_query.active_extra_subagents` — APM 전용)을 소스 무관으로 넓힌다.
처리기 하나(`apm_query` · `doc_query`)는 각자 활성 판정·보기 어휘·분해 절을 소유하고,
이 모듈은 결합과 정제 두 가지만 한 자리에 모은다:

- `active_conditional_agents` — 분해 목록 · `resolve_subagent`(2단 오케스트레이터 · 3단 계획 루프) ·
  재계획 어휘가 같은 함수를 부른다. **둘 다 비활성이면 빈 dict** — 종전과 같다(비활성 바이트 불변).
- `sanitize_task_views` — 처리기마다 자기 닫힌 어휘로 `views`를 정제하고, 다른 담당의 `views`는
  떼어 낸다. 문서 task 의 의존(`depends_on`·`input_from`)은 끊고 사유를 남긴다(간선 없음 · G-10).
- `apply_explicit_sources` — 사용자가 이름(유사어 포함)으로 지목한 데이터 소스로 task 담당을 맞춘다
  (plans/132 N-2 · D-293). 지목한 비DB 시스템이 활성이면 그 처리기로 고정하고, 지목한 소스가 전부
  비활성이면 조회하지 않고 안내만 하는 task로 바꾼다(G-1 「안내만」 — 다른 소스로 대신 답하지
  않는다).
- `apply_source_selection` — 계획 출구의 소스 선별(plans/132 W2 · N-6·N-10). 명시 지목이 있는 task는
  위 규칙, 없는 조회 task는 분해 LLM이 낸 답변 영역(`areas`)의 정본 소유 시스템으로 판정한다
  (`domain.clarification_policy` — 단독 소유 · 기억 · 소스 선택 칩).

분해 프롬프트 삽입은 처리기별 렌더 캐시가 있는 `intent_planner._planner_system_prompt`가 한다.

APM 쪽은 모듈 속성으로 부른다(`apm_query.active_extra_subagents`) — 125 테스트가 그 이름을 쓴다.

계층: orchestration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.domain.clarification_policy import (
    ASK,
    KEEP,
    NOTICE,
    PIN,
    SourceOwner,
    SourceSlotDecision,
    decide_source_slot,
)
from src.orchestration import apm_query, doc_query
from src.orchestration.subagents import SubAgentSpec
from src.routing.db_authz import authorized_db_ids, is_source_allowed
from src.routing.registry import get_registry
from src.routing.source_hints import (
    KIND_NON_DB,
    SourceMention,
    area_notice_text,
    area_partial_notice_text,
    is_mention_active,
    mentions_for_task,
    resolve_source_mentions,
    source_choice_question,
    source_notice_text,
)
from src.utils.prior_dependency import NOTE_SOURCE_UNAVAILABLE, NOTE_TRACE
from src.utils.query_gen_common import ZONE_CLARIFY_OPTIONS

logger = logging.getLogger(__name__)

#: 비DB 시스템 → 그 시스템을 조회하는 조건부 처리기(선언 순서).
SYSTEM_AGENTS: dict[str, str] = {
    apm_query.APM_SYSTEM: apm_query.APM_QUERY_AGENT,
    doc_query.DOC_SYSTEM: doc_query.DOC_QUERY_AGENT,
}
#: 지목한 소스로 담당을 바꿔도 되는 조회 담당 — 개념 설명(`general_inference`)·캐시·유사어 등록은
#: 소스 조회가 아니라 건드리지 않는다(「제니퍼가 뭐야?」는 설명이 맞다).
_PINNABLE_AGENTS = frozenset({"data_query", "alarm_query", "process_query"})
#: 안내만 하는 task 표지(재계획이 종결로 본다 — plans/132 N-3).
SOURCE_NOTICE_KEY = "source_notice"
#: 소스 선택 칩 task 표지(plans/132 N-10) — 칩을 띄우기 전 담당·영역·후보. 존 재진입 스냅샷에 실려
#: 답변 턴이 원래 담당(DB 선택)이나 고른 처리기(비DB 선택)로 되살린다.
SOURCE_SLOT_KEY = "source_slot"
#: 칩 페이로드를 결과로 옮기는 task 키 — `subagents.run_general_inference`가 결과의
#: `zone_clarification`으로 싣는다.
SOURCE_CLARIFICATION_KEY = "source_clarification"
#: 소스 선택 칩 페이로드 종류 — 존 역질문(`zone_select`)과 같은 모양·같은 화면 위젯을 쓴다.
SOURCE_SELECT_KIND = "source_select"


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


def active_conditional_systems(app_config: Any) -> dict[str, str]:
    """활성인 비DB 시스템 → 처리기 이름(비활성이면 빈 dict)."""
    active = active_conditional_agents(app_config)
    return {system: agent for system, agent in SYSTEM_AGENTS.items() if agent in active}


def apply_explicit_sources(
    tasks: list[dict[str, Any]], hints: Any, app_config: Any,
) -> list[str]:
    """사용자가 지목한 데이터 소스로 task 담당을 맞춘다(in-place · plans/132 N-2 · G-1 · D-293).

    규칙(조회 담당 `data_query`·`alarm_query`·`process_query` task만 · 계획 고정 `db_ids`·고정 안내
    task 제외):

    1. task가 가리키는 지목 중 **활성 DB 시스템이 없고** 활성 비DB 시스템이 정확히 하나면 그
       처리기로 고정한다(「제니퍼의 인스턴스 리스트」 → `apm_query`). 보기는 비운다 — 처리기가 기본
       보기를 정한다.
    2. task가 가리키는 지목이 **전부 비활성**이면 조회하지 않고 안내만 하는 task로 바꾼다(G-1
       「안내만」 — 다른 소스로 대신 답하지 않는다). 안내는 사용자가 쓴 표현만 싣는다(D-264).
    3. 그 밖(활성 DB 시스템 지목이 있음 · 지목 없음)은 그대로 둔다 — 비활성 지목의 사유 노트는 SQL
       처리기가 단다(TP-1.11a).

    단일 task 계획은 원문의 지목 전체를, 복합 계획은 task 질의에 이름이 남은 지목만 본다.

    Returns:
        바뀐 task id 목록(로그·테스트용)
    """
    mentions = resolve_source_mentions(hints if isinstance(hints, list) else [])
    if not mentions:
        return []
    targets = [t for t in tasks if _pinnable(t)]
    if not targets:
        return []
    systems = active_conditional_systems(app_config)
    active_db_ids = list(app_config.multi_db.get_active_db_ids())
    single = len([t for t in tasks if isinstance(t, dict)]) == 1
    changed: list[str] = []
    for task in targets:
        scoped = _task_mentions(task, mentions, single=single)
        if not scoped:
            continue
        active = [m for m in scoped
                  if is_mention_active(m, active_db_ids=active_db_ids, active_non_db=systems)]
        active_non_db = {m.system for m in active if m.kind == KIND_NON_DB}
        active_systems = {m.system for m in active}
        if active and len(active_non_db) == 1 and active_systems == active_non_db:
            agent = systems[next(iter(active_non_db))]
            if task.get("agent") != agent:
                logger.info("명시 소스 고정(plans/132 N-2): task=%s %s → %s",
                            task.get("task_id"), task.get("agent"), agent)
                task["agent"] = agent
                task["views"] = []
                changed.append(str(task.get("task_id")))
        elif not active:
            names = [m.hint for m in scoped]
            logger.info("명시 소스 비활성 — 안내만(plans/132 G-1): task=%s 지목=%s",
                        task.get("task_id"), [m.system for m in scoped])
            task["agent"] = "general_inference"
            task["direct_response"] = source_notice_text(names)
            task[SOURCE_NOTICE_KEY] = sorted({m.system for m in scoped})
            task.pop("views", None)
            changed.append(str(task.get("task_id")))
    return changed


def _pinnable(task: Any) -> bool:
    """지목·영역으로 담당을 바꿔도 되는 조회 task인가(고정 안내·계획 고정 `db_ids` 제외)."""
    return (isinstance(task, dict) and task.get("agent") in _PINNABLE_AGENTS
            and not task.get("direct_response") and not task.get("db_ids"))


def _task_mentions(
    task: dict[str, Any], mentions: list[SourceMention], *, single: bool,
) -> list[SourceMention]:
    """task가 가리키는 지목 — 원문·task 질의 대조(W1) ∪ 분해 LLM의 `requested_source`(N-5).

    `requested_source`는 이 턴의 결정적 지목(입력 파서 힌트 해소)과 같은 시스템일 때만 쓴다 — LLM이
    추측한 이름으로 소스를 새로 만들지 않는다(D-004). 복합 계획에서 task 질의가 이름을 빠뜨린 경우의
    귀속을 돕는다.
    """
    scoped = mentions_for_task(mentions, str(task.get("sub_query") or ""), single_task=single)
    requested = str(task.get("requested_source") or "").strip()
    if requested:
        systems = {m.system for m in resolve_source_mentions([requested])}
        scoped += [m for m in mentions if m.system in systems and m not in scoped]
    return scoped


@dataclass
class SourceSelection:
    """계획 출구 소스 선별 결과(plans/132 W2)."""

    changed: list[str] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    #: 이번 턴 계획이 닿은 비DB 시스템(처리기 고정·안내 — 다음 턴 `previous_sources` 재료 · G-15)
    turn_sources: list[str] = field(default_factory=list)


def apply_source_selection(
    tasks: list[dict[str, Any]], state: Any, app_config: Any,
    *, remembered: list[str] | None = None,
) -> SourceSelection:
    """계획 출구 소스 선별(in-place · plans/132 W2 — §6.2 ③).

    1. 명시 지목이 있는 task — `apply_explicit_sources`(W1 · G-1)
    2. 지목이 없는 조회 task — 답변 영역(`areas`)의 정본 소유 시스템으로 소스 슬롯을 판정한다
       (`decide_source_slot`: 단독 소유 → 기억 → 칩). 판정 재료는 등록 시스템 전체다(G-7 (a)).

    Args:
        tasks: 계획 task 목록(바꾼다)
        state: 요청 상태(힌트·권한·채널·스레드 선택)
        app_config: 설정
        remembered: 기억된 선택(스레드 선택 → 확인된 사례 순 · W5)
    """
    hints = (state.get("parsed_requirements") or {}).get("target_db_hints")
    hints = hints if isinstance(hints, list) else []
    out = SourceSelection(changed=apply_explicit_sources(tasks, hints, app_config))
    mentions = resolve_source_mentions(hints)
    single = len([t for t in tasks if isinstance(t, dict)]) == 1
    for task in tasks:
        if not _pinnable(task) or not task.get("areas"):
            continue
        if mentions and _task_mentions(task, mentions, single=single):
            continue  # 명시 지목이 있는 task — W1 규칙이 처분했다
        _apply_area_slot(task, state, app_config, remembered or [], out)
    systems = {agent: system for system, agent in SYSTEM_AGENTS.items()}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        touched = [systems.get(str(task.get("agent")))] + list(task.get(SOURCE_NOTICE_KEY) or [])
        for system in touched:
            if system and system not in out.turn_sources:
                out.turn_sources.append(system)
    return out


def _area_owners(task: dict[str, Any], state: Any, app_config: Any) -> list[SourceOwner]:
    """task 답변 영역의 정본 소유 시스템(영역 순서 · 중복 없음)과 활성·권한 상태."""
    reg = get_registry()
    systems = active_conditional_systems(app_config)
    active_db_ids = list(app_config.multi_db.get_active_db_ids())
    role = state.get("user_role")
    owners: list[SourceOwner] = []
    for area in task.get("areas") or []:
        found = reg.capability_owners(str(area))
        if not found or any(o.system == found[0] for o in owners):
            continue
        system = found[0]
        if reg.is_non_db_system(system):
            owners.append(SourceOwner(
                system, non_db=True, active=system in systems,
                allowed=is_source_allowed(system, state.get("allowed_sources"), role),
            ))
            continue
        dbs = [d for d in reg.system_db_ids(system) if d in active_db_ids]
        owners.append(SourceOwner(
            system, non_db=False, active=bool(dbs),
            allowed=bool(dbs) and bool(authorized_db_ids(dbs, state.get("allowed_db_ids"), role)),
        ))
    return owners


def _area_labels(task: dict[str, Any], systems: set[str] | None = None) -> list[str]:
    """task 영역의 설명 라벨 — `systems`를 주면 그 시스템이 소유한 영역만."""
    reg = get_registry()
    labels = {spec.code: spec.label or spec.code for spec in reg.capability_specs()}
    out: list[str] = []
    for area in task.get("areas") or []:
        owners = reg.capability_owners(str(area))
        if systems is None or (owners and owners[0] in systems):
            out.append(labels.get(str(area), str(area)))
    return out


def _apply_area_slot(
    task: dict[str, Any], state: Any, app_config: Any, remembered: list[str],
    out: SourceSelection,
) -> None:
    decision = decide_source_slot(_area_owners(task, state, app_config), remembered=remembered)
    tid = str(task.get("task_id"))
    if decision.action == ASK and not state.get("zone_clarification_allowed"):
        # 비대화 채널(API 일괄·평가 하네스)은 묻지 않는다(존 역질문과 같은 채널 게이트 · 106 H1.4) —
        # 첫 후보로 진행하고 그 사실을 노트로 남긴다.
        decision = SourceSlotDecision(
            PIN if get_registry().is_non_db_system(decision.candidates[0]) else KEEP,
            system=decision.candidates[0], unavailable=decision.unavailable, reason="no_channel",
        )
        label = get_registry().system_label(decision.system or "")
        out.notes.append(_note(tid, "source_slot_default", (
            f"요청하신 정보는 여러 데이터 소스에 있어 「{label}」 기준으로 조회했습니다.")))
    if decision.action == NOTICE:
        logger.info("소스 슬롯 안내만(plans/132 G-7): task=%s 영역=%s 소유=%s",
                    tid, task.get("areas"), list(decision.unavailable))
        task["agent"] = "general_inference"
        task["direct_response"] = area_notice_text(_area_labels(task))
        task[SOURCE_NOTICE_KEY] = list(decision.unavailable)
        task.pop("views", None)
        out.changed.append(tid)
        return
    if decision.action == ASK:
        logger.info("소스 선택 칩(plans/132 N-10): task=%s 후보=%s", tid, list(decision.candidates))
        payload = _source_choice_payload(task, decision, state, app_config)
        task[SOURCE_SLOT_KEY] = {"from_agent": task.get("agent"), "areas": list(task["areas"]),
                                 "candidates": list(decision.candidates)}
        task["agent"] = "general_inference"
        task["direct_response"] = payload["question"]
        task[SOURCE_CLARIFICATION_KEY] = payload
        task.pop("views", None)
        out.changed.append(tid)
        return
    if decision.unavailable:
        labels = _area_labels(task, set(decision.unavailable))
        out.notes.append(_note(tid, "source_inactive", area_partial_notice_text(labels)))
    if decision.action == PIN and decision.system:
        agent = active_conditional_systems(app_config).get(decision.system)
        if agent and task.get("agent") != agent:
            logger.info("소스 슬롯 처리기 고정(plans/132 N-6): task=%s %s → %s (%s)",
                        tid, task.get("agent"), agent, decision.reason)
            task["agent"] = agent
            task["views"] = []
            out.changed.append(tid)


def resolve_chip_by_memory(
    task: dict[str, Any], system: str, state: Any, app_config: Any,
) -> dict[str, Any] | None:
    """소스 선택 칩 task를 기억된 소스로 되돌린다(plans/132 W5 · G-12 (a) 동점 해소).

    비DB 소스면 그 처리기로, DB 소스면 칩 전 담당(종전 SQL 분류)으로 돌린다. 「다른 소스로 보기」 칩
    페이로드(나머지 후보 — 정정 = 기억 갱신)를 돌려준다. 되돌릴 수 없으면(칩이 아님 · 처리기 비활성)
    None — 칩을 그대로 둔다.
    """
    slot = task.get(SOURCE_SLOT_KEY)
    if not isinstance(slot, dict) or system not in (slot.get("candidates") or []):
        return None
    if get_registry().is_non_db_system(system):
        agent = active_conditional_systems(app_config).get(system)
        if agent is None:
            return None
        task["agent"] = agent
        task["views"] = []
    else:
        task["agent"] = slot.get("from_agent") or "data_query"
    for key in (SOURCE_SLOT_KEY, SOURCE_CLARIFICATION_KEY, "direct_response"):
        task.pop(key, None)
    others = tuple(c for c in slot.get("candidates") or [] if c != system)
    switch = _source_choice_payload(task, SourceSlotDecision(ASK, candidates=others), state,
                                    app_config)
    switch["question"] = "다른 데이터 소스로 다시 보려면 선택하세요."
    switch["correction"] = True
    return switch


def memory_notice(task_id: str, label: str) -> dict[str, Any]:
    """기억을 써서 소스를 골랐다는 고지(plans/132 §6.5 — 바꾸기 칩과 함께)."""
    return {"kind": NOTE_TRACE, "task_id": task_id, "reason": "source_memory",
            "detail": (f"이전에 비슷한 질문에서 「{label}」을(를) 고르셔서 그 기준으로 "
                       "조회했습니다. 다른 데이터 소스로 보려면 아래에서 선택하세요.")}


def _note(task_id: str, reason: str, detail: str) -> dict[str, Any]:
    return {"kind": NOTE_SOURCE_UNAVAILABLE, "task_id": task_id, "reason": reason,
            "detail": detail}


def _source_choice_payload(
    task: dict[str, Any], decision: SourceSlotDecision, state: Any, app_config: Any,
) -> dict[str, Any]:
    """소스 선택 칩 페이로드 — 존 역질문과 같은 모양(`zone_select` 위젯 재사용 · LLM 0).

    선택지는 권한 안 활성 후보 시스템뿐이다(D-264 ② — 권한 밖 소스 비노출). 존이 있는 DB 시스템은
    존 단위 선택지로 펼쳐 **존 질문과 한 질문**으로 합친다(소스 → 그 소스의 존). 소스마다 `group`이
    달라 한 소스만 고를 수 있다(`group_exclusive`). 같은 소스의 존들은 한 그룹이다 — 존 그룹
    상호배타(`ZONE_GROUP_EXCLUSIVE` · D-143 후속3)가 켜졌을 때만 존 그룹으로 나눈다(D-206 동시
    선택 개방).
    """
    reg = get_registry()
    zone_rows = {o["db_id"]: o for o in ZONE_CLARIFY_OPTIONS}
    zone_exclusive = getattr(app_config.multi_db, "zone_group_exclusive", False) is True
    active = set(app_config.multi_db.get_active_db_ids())
    role = state.get("user_role")
    options: list[dict[str, Any]] = []
    for system in decision.candidates:
        label = reg.system_label(system)
        if reg.is_non_db_system(system):
            options.append({"source": system, "label": label, "group": system})
            continue
        dbs = authorized_db_ids(
            [d for d in reg.system_db_ids(system) if d in active],
            state.get("allowed_db_ids"), role,
        )
        rest: list[str] = []
        for db_id in dbs:
            zone = zone_rows.get(db_id)
            if zone:
                options.append({"db_id": db_id, "label": f"{label} — {zone['label']}",
                                "group": zone["group"] if zone_exclusive else system,
                                "source": system})
            else:
                rest.append(db_id)
        if rest:
            options.append({"db_ids": rest, "label": label, "group": system, "source": system})
    return {
        "kind": SOURCE_SELECT_KIND,
        "question": source_choice_question(_area_labels(task)),
        "options": options,
        "original_query": str(state.get("original_user_query") or state.get("user_query") or ""),
        "multi": True,
        "group_exclusive": True,
        "areas": list(task.get("areas") or []),
    }


__all__ = [
    "SOURCE_CLARIFICATION_KEY",
    "SOURCE_NOTICE_KEY",
    "SOURCE_SELECT_KIND",
    "SOURCE_SLOT_KEY",
    "SYSTEM_AGENTS",
    "SourceSelection",
    "active_conditional_agents",
    "active_conditional_systems",
    "apply_explicit_sources",
    "apply_source_selection",
    "memory_notice",
    "resolve_chip_by_memory",
    "sanitize_task_views",
]
