"""의도 분해 노드 (Plan 48, deepagents `write_todos` 대응).

사용자 질의를 sub-task 목록(`task_plan`)으로 분해한다. 단일 작업이면 task 1개만 생성한다.

처리 단계:
- [계층 A] deterministic pre-check (LLM 스킵): 기존 semantic_router 우선순위 ①~③를 이식.
  pending_synonym_reuse / synonym_registration / mapped_db_ids 가 있으면 단일 task로 즉시 반환.
- [계층 B] LLM 복합 분해: INTENT_PLANNER_SYSTEM_TEMPLATE로 분해 + 각 task agent 분류.
  실패/빈 결과면 단일 data_query task로 폴백한다.

본 노드는 tool-calling을 사용하지 않으며, 프롬프트 + JSON 파싱으로만 동작한다.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.llm import create_llm
from src.nodes.input_parser import LOCATION_HINT_TERMS
from src.domain.task_frame import render_task_query, task_spans, verify_task_frames
from src.prompts.intent_planner import (
    INTENT_PLANNER_SYSTEM_TEMPLATE,
    render_intent_planner_apm_template,
    render_intent_planner_areas_template,
    render_intent_planner_doc_template,
    render_intent_planner_environment_terms,
    render_intent_planner_ownership_template,
    render_intent_planner_task_frame_template,
)
from src.routing.db_scope import ZONE_GROUP_ONLY, ZONE_SELECTION_SCOPE_KEY
from src.routing.registry import get_registry
from src.routing.capability_ownership import (
    active_owner_system_count,
    known_capability_codes,
    render_ownership_rows,
    sanitize_capability_code,
    sanitize_capability_list,
)
from src.state import AgentState
from src.clients.instructor_adapter import StructuredOutputError, try_structured_call
from src.orchestration.schemas import (
    AGENT_FALLBACK_KEY,
    DecomposedPlan,
    OwnershipDecomposedPlan,
    SpanDecomposedPlan,
    views_plan_model,
    close_agent_vocabulary,
    validate_plan_dag,
)
from src.utils.json_extract import extract_json_from_response
from src.utils.prior_dependency import NOTE_DECOMPOSE, has_sequential_marker
from src.utils.synonym_set_parser import parse_synonym_set
from src.utils.usage_query import is_usage_query

logger = logging.getLogger(__name__)

# process_query 결정적 가드 키워드 (D-041/D-046 정합).
# 프로세스 신호 — 있으면 실시간 프로세스 조회 후보.
_PROCESS_KEYWORDS = ("프로세스", "process")
# 과거/이력 신호 — 있으면 DB 이력 조회(data_query)로 유지(실시간 교정 제외).
_PROCESS_HISTORY_KEYWORDS = (
    "이력", "추세", "추이", "트렌드", "지난", "과거", "기간",
    "일간", "주간", "월간", "동안", "history", "변화", "시점",
)

# alarm_query 결정적 가드 키워드 (D-076 후속3 — D-047 프로세스 교정과 동일 패턴).
# 모니터링 문맥의 "이벤트(event)"는 알람을 뜻하나 LLM이 data_query로 보수 분류하는 실측 사례가
# 있어(bare "event" 질의) 프롬프트 어휘만으로는 부족 — 결정적으로 교정한다.
_ALARM_KEYWORDS = ("알람", "alert", "이벤트", "event", "경보")

# 폼필 요청 감지 키워드 (Plan 73 D-150 — 파일 없는 "양식 채워줘" 안내용).
# 양식 명사 + 채움 동사가 함께 있어야 발동한다(오발동 최소).
_FORM_NOUN_KEYWORDS = ("양식", "서식", "템플릿")
_FORM_FILL_VERB_KEYWORDS = ("채우", "채워", "기입", "작성")

# 폼필 확인 이력 명령 판정(D-151) — 단일 출처는 utils.query_gen_common으로 이동
# (nodes.field_mapper가 계층 역방향 없이 공유하기 위함, FIX-24). 기존 임포터
# (api.routes.query 등)를 위해 이 모듈에서 재수출한다.
from src.utils.query_gen_common import (  # noqa: E402
    term_in_text,
    FORM_MEMORY_ALL_KEYWORDS as _FORM_MEMORY_ALL_KEYWORDS,
    FORM_MEMORY_DELETE_KEYWORDS as _FORM_MEMORY_DELETE_KEYWORDS,
    FORM_MEMORY_NOUN_KEYWORDS as _FORM_MEMORY_NOUN_KEYWORDS,
    FORM_MEMORY_VIEW_KEYWORDS as _FORM_MEMORY_VIEW_KEYWORDS,
    is_form_memory_command,
    FORM_MEMORY_SHORTCUT_HINT as _FORM_MEMORY_SHORTCUT_HINT,
    is_form_memory_shortcut as _is_form_memory_shortcut,
    memory_query_normalized as _memory_query_normalized,
    refers_to_demonstrative_server,
    UPTIME_RATE_GUIDANCE as _UPTIME_RATE_GUIDANCE,
    is_uptime_rate_query as _is_uptime_rate_query,
)

# 사용자에게 그대로 노출되는 고정 안내문 — LLM을 통과시키지 않는다(라이브 실측
# 2026-07-30: general_inference LLM 호출 실패 시 일반 오류 문구로 강등됨).
_FORM_FILL_NO_FILE_GUIDANCE = (
    "양식 파일이 첨부되지 않아 양식 채우기를 진행할 수 없습니다.\n\n"
    "Excel(.xlsx) 양식 파일을 첨부한 뒤 다시 요청해 주세요. 파일이 첨부되면 "
    "양식 헤더를 분석해 수집 중인 항목을 자동으로 채우고, 채울 수 없는 항목은 "
    "사유와 함께 안내해 드립니다."
)


def has_alarm_signal(text: str) -> bool:
    """질의에 알람/모니터링 이벤트 신호가 있는지 검사한다(결정적 교정 공용 헬퍼)."""
    low = (text or "").lower()
    return any(k in low for k in _ALARM_KEYWORDS)


def _coerce_alarm_intent(tasks: list[dict]) -> list[dict]:
    """알람/이벤트 조회인데 data_query로 분류된 task를 alarm_query로 교정한다.

    alarm_query여야 알람 전용 템플릿과 alarm_allowed_tables가 활성화된다 —
    data_query로 남으면 allowed_tables 필터가 알람 테이블을 제거해 환각/오답이 된다.

    Args:
        tasks: 분해된 task 목록(각 dict는 agent/sub_query 보유)

    Returns:
        교정이 적용된 동일 리스트(in-place 수정 후 반환)
    """
    for task in tasks:
        if task.get("agent") != "data_query":
            continue
        if task.get("input_from"):
            # 데이터 의존 task의 알람 어휘는 선행 task의 선별 조건 잔재("심각 알람이 있는
            # 서버들의 CPU 사용률")이지 알람 재조회 의도가 아니다 — alarm_query로 뒤집으면
            # 알람 템플릿(성능 지표 불가)로 가서 지표 조회가 사라진다(D-086).
            continue
        if not has_alarm_signal(str(task.get("sub_query", ""))):
            continue
        task["agent"] = "alarm_query"
        logger.info(
            "intent_planner: 알람 조회 결정적 교정 — data_query→alarm_query (sub_query=%r)",
            task.get("sub_query"),
        )
    return tasks


def _coerce_process_intent(tasks: list[dict]) -> list[dict]:
    """현재/실시간 프로세스 조회인데 data_query로 분류된 task를 process_query로 교정한다.

    배경(D-046): "프로세스 조회/리스트"에 '현재/실시간' 같은 시간성 신호가 없으면 LLM이
    보수적으로 `data_query`로 분류 → `cmm_resource`에서 `resource_type='process'` 행을 가져오는
    환각이 발생한다. 시간성(이력/추세 등) 신호가 없는 프로세스 조회는 실시간 API(`process_query`)가
    1급 의도이므로(D-041) LLM 비결정성에 의존하지 않고 결정적으로 교정한다.

    Args:
        tasks: 분해된 task 목록(각 dict는 agent/sub_query 보유)

    Returns:
        교정이 적용된 동일 리스트(in-place 수정 후 반환)
    """
    for task in tasks:
        if task.get("agent") != "data_query":
            continue
        sub = str(task.get("sub_query", "")).lower()
        if not any(k in sub for k in _PROCESS_KEYWORDS):
            continue
        if any(h in sub for h in _PROCESS_HISTORY_KEYWORDS):
            continue  # 과거/이력 프로세스는 DB 조회 유지
        task["agent"] = "process_query"
        logger.info(
            "intent_planner: 프로세스 조회 결정적 교정 — data_query→process_query (sub_query=%r)",
            task.get("sub_query"),
        )
    return tasks


def _coerce_host_inspect_intent(
    tasks: list[dict], state: AgentState, app_config: AppConfig
) -> list[dict]:
    """OS 구성·자원 현황·메트릭 추세 **단건 조회**를 `host_inspect`로 교정한다.

    Plan 78 W3-2(경로 선택 규칙) · WU-18. `_coerce_process_intent`(D-046/047)와 같은 계열의
    **결정적 교정**이다 — LLM 분류를 고치되 값을 만들지 않는다(D-035).

    중간 비용대의 공백을 메운다: 이 요구는 지금 `data_query`(DB SQL로 근사)나
    `fault_diagnosis`(sre_agent 위임 · 비쌈)로 가는데, `mcp_server` 고수준 도구가
    **더 싸고 정확한** 답을 준다(78 §2.2 G1).

    **발화 조건 셋을 모두 만족할 때만** 교정한다:

    1. 플래그 on (`COMPOSITE_INVESTIGATION_ENABLED`) — off면 **아무것도 하지 않는다**(비트 동일)
    2. 현재 분류가 `data_query` — 다른 경로는 건드리지 않는다
    3. 프로파일 키워드가 **명시적으로** 있고, **대상 호스트가 식별**된다

    조건 3이 좁은 이유: `data_query`는 본체의 주력 경로라 욕심을 내면 정상 조회를 잠식하는데,
    WU-06(분포 실측)이 막혀 있어 **정확도를 측정할 수단이 없다**(`SPEC-host-inspect-routing.md` §0.1).
    측정 없이 넓히지 않는다.

    Args:
        tasks: 분해된 task 목록
        state: 현재 상태(parsed_requirements·conversation_context에서 대상 신호를 본다)
        app_config: 앱 설정(플래그)

    Returns:
        교정이 적용된 동일 리스트(in-place 수정 후 반환)
    """
    # 설정 접근은 **방어적으로** 한다 — 이 교정은 intent_planner 말미에서 무조건 돌고,
    # 기존 테스트 중에는 `app_config`로 스텁 객체를 넘기는 것이 있다. 속성이 없으면
    # **꺼진 것으로 본다**(fail-closed · `semantic_router.py:216` 동일 패턴).
    composite = getattr(app_config, "composite", None)
    if not getattr(composite, "investigation_enabled", False):
        return tasks

    # 대상 신호 판정은 **78 자기 모듈**에 있다 — `intent_planner`가 `src.utils.prior_targets`를
    # 직접 임포트하면 W1 소유 경계를 넘는다(R-13 · 80 §6 소유권 계약).
    from src.orchestration.host_inspect import (
        HOST_INSPECT_AGENT,
        detect_profile,
        has_target_signal,
    )

    if not has_target_signal(state, app_config):
        return tasks

    for task in tasks:
        if task.get("agent") != "data_query":
            continue
        profile = detect_profile(str(task.get("sub_query", "")))
        if not profile:
            continue
        task["agent"] = HOST_INSPECT_AGENT
        logger.info(
            "intent_planner: 호스트 조사 결정적 교정 — data_query→%s "
            "(profile=%s · sub_query=%r)",
            HOST_INSPECT_AGENT, profile, task.get("sub_query"),
        )
    return tasks


async def intent_planner(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict[str, Any]:
    """사용자 질의를 sub-task 목록으로 분해한다(계획 본체 ``_plan_turn`` + 단일 출구 정규화).

    **모든 분기의 출구**에서 담당 교정(알람·프로세스)을 다시 적용한다(plans/111 D-1) — 사전 처리
    조기 반환(존 선택 재진입 ②.5 등)이 교정을 우회하던 구조(111 §2.4 · 27턴)를 닫는다.

    정규화는 **플래그와 무관하게 항상** 돈다(plans/114 T-1 · D-250 ⑤). 종전에는
    ``COMPOSITE_TASK_FRAME_ENABLED``에 묶여 있었는데, 그 플래그는 프롬프트 계약(111 C-1~C-3)을
    바꾸는 것이고 여기 교정은 LLM 출력을 고치지 않는 **순수 결정적 후처리**라 묶일 이유가 없었다.
    묶인 채로 두면 2단 기본 설정에서 알람 질의가 전건 ``data_query``로 남아(run
    ``20260922-112010``: 존 자동응답 105턴이 전부 이 경로) 사다리 단 비교가 공정하지 않다.
    """
    if app_config is None:
        app_config = load_config()
    result = await _plan_turn(state, llm=llm, app_config=app_config)
    _normalize_plan_exit(result, state)
    _apply_source_selection(result, state, app_config)
    await _apply_source_memory(result, state, app_config)
    await _record_source_choice(result, state, app_config)
    _record_clarification(result)
    return result


#: 스레드 범위 소스 선택(plans/132 N-10) — 소스 선택 칩에서 고른 시스템. 같은 스레드의 다음 모호
#: 판정이 다시 묻지 않고 쓴다(존 승계와 같은 자리 · 체크포인터 보존 · 스코프 칩 해제 때 비운다).
SOURCE_CHOICE_KEY = "source_choice"
#: 이번 턴 계획이 닿은 비DB 시스템(plans/132 — 다음 턴 `conversation_context.previous_sources`).
TURN_SOURCES_KEY = "turn_sources"
#: 소스 선택 칩 답변 턴의 선택(요청 스코프 — 라우트가 싣는다).
SELECTED_SOURCES_KEY = "selected_sources"
#: 칩·정정 답변 표지(요청 스코프 — 라우트가 직전 턴 칩을 보고 싣는다 · 기억 쓰기 게이트 · W5).
SOURCE_SELECTION_META_KEY = "source_selection_meta"
#: 기억을 써서 소스를 고른 턴의 「다른 소스로 보기」 칩(요청 스코프 · W5).
SOURCE_SWITCH_KEY = "source_switch"


def _apply_source_selection(
    result: dict[str, Any], state: AgentState, app_config: AppConfig,
) -> None:
    """계획 단일 출구의 소스 선별(plans/132 N-2 · W2 N-6·N-10).

    명시 지목(W1 · G-1)과 답변 영역의 정본 소유(W2 · G-7 (a))로 task 담당을 맞추고, 모호하면 소스
    선택 칩 task로 바꾼다(`conditional_agents.apply_source_selection`). 양식 턴은 제외한다(양식 단일
    task — `_normalize_plan_exit`와 같은 이유). 이번 턴이 닿은 비DB 시스템을 남긴다(G-15) — 비DB
    소스가 없는 턴은 직전 값이 있을 때만 비운다(그 밖의 턴은 반환 dict 바이트 불변).
    """
    selection = None
    tasks = result.get("task_plan")
    if (
        not (state.get("template_structure") or state.get("uploaded_file"))
        and isinstance(tasks, list) and tasks
    ):
        from src.orchestration.conditional_agents import apply_source_selection  # 지연 — 순환 방지

        selection = apply_source_selection(
            tasks, state, app_config, remembered=_remembered_sources(state),
        )
    turn_sources = selection.turn_sources if selection is not None else []
    if turn_sources or state.get(TURN_SOURCES_KEY):
        result[TURN_SOURCES_KEY] = turn_sources
    if selection is not None and selection.notes:
        result["dependency_notes"] = (
            list(result.get("dependency_notes") or state.get("dependency_notes") or [])
            + selection.notes
        )


def _remembered_sources(state: AgentState) -> list[str]:
    """기억된 소스 선택(판정 순서 「기억」 칸 · plans/132 §6.5) — 스레드 선택."""
    choice = state.get(SOURCE_CHOICE_KEY)
    system = choice.get("system") if isinstance(choice, dict) else None
    return [str(system)] if system else []


def _memory_text(state: AgentState) -> str:
    """기억 사례 질의 — 원문 정규화(민감값 마스킹 · 입력 파서가 뽑은 서버 식별자 자리 일반화)."""
    from src.schema_cache.source_memory import normalize_case_text
    from src.utils.query_gen_common import is_server_identity_col

    conditions = (state.get("parsed_requirements") or {}).get("filter_conditions") or []
    identifiers = [
        str(c.get("value")) for c in conditions
        if isinstance(c, dict) and is_server_identity_col(str(c.get("field") or ""))
        and isinstance(c.get("value"), str)
    ]
    return normalize_case_text(str(state.get("user_query") or ""), identifiers)


async def _audit_source_memory(state: AgentState, action: str, case: dict[str, Any]) -> None:
    """소스 선택 기억 감사(쓰기·사용) — 기록 실패는 질의를 막지 않는다."""
    from src.security.audit_logger import log_source_memory

    try:
        await log_source_memory(
            action=action, scope=str(case.get("scope")), case_id=str(case.get("case_id")),
            sources=list(case.get("sources") or []), origin=case.get("origin"),
            user_id=state.get("user_id"), thread_id=state.get("thread_id"),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("소스 선택 기억 감사 기록 실패: %s", e)


async def _apply_source_memory(
    result: dict[str, Any], state: AgentState, app_config: AppConfig,
) -> None:
    """소스 선택 칩 task를 확인된 사례로 해소한다(plans/132 W5 · §6.5 · G-12 (a)).

    판정 순서 `명시 > 단독 소유 > [기억] > 칩`의 기억 칸이다 — 칩이 걸린 task(소유 소스 2개+ · 명시
    없음)에만 쓴다. 개인 사례 → 조직 공용(관리자 승격) → 시드 순으로 찾고, 찾으면 칩 대신 그
    소스로 조회하면서 고지(경과 노트) + 「다른 소스로 보기」 칩을 붙인다. TTL 0이면 아무것도 하지
    않는다(비트 동일 · Redis 접근 0).
    """
    from src.schema_cache import source_memory as sm

    ttl = sm.ttl_seconds(app_config)
    tasks = [
        t for t in result.get("task_plan") or [] if isinstance(t, dict) and t.get("source_slot")
    ]
    if ttl <= 0 or not tasks:
        return
    from src.orchestration.conditional_agents import memory_notice, resolve_chip_by_memory

    reg = get_registry()
    store = await sm.open_store(app_config)
    scope = sm.user_scope(state.get("user_id"))
    stored: list[dict[str, Any]] = []
    if store is not None:
        stored = (await store.load(scope) if scope else []) + await store.load(sm.SCOPE_ORG)
    cases = stored + sm.seed_cases(reg)
    text = _memory_text(state)
    notes: list[dict[str, Any]] = []
    for task in tasks:
        candidates = list(task["source_slot"].get("candidates") or [])
        hit = sm.search_cases(cases, text, candidates=candidates, ttl_seconds=ttl,
                              registry_fp=sm.registry_fingerprint(reg))
        system = sm.chosen_source(hit[0], candidates) if hit else None
        switch = resolve_chip_by_memory(task, system, state, app_config) if system else None
        if hit is None or switch is None:
            continue
        case, score, step = hit
        logger.info("intent_planner: 소스 선택 기억 사용(plans/132 W5) — task=%s %s %s(%.2f)",
                    task.get("task_id"), system, step, score)
        switch["areas"] = list(task.get("areas") or [])
        result[SOURCE_SWITCH_KEY] = switch
        notes.append(memory_notice(str(task.get("task_id")), reg.system_label(str(system))))
        if store is not None and any(c is case for c in stored):
            await store.touch(case, ttl_seconds=ttl)
        await _audit_source_memory(state, "use", case)
    if notes:
        result["dependency_notes"] = (
            list(result.get("dependency_notes") or state.get("dependency_notes") or []) + notes
        )


async def _record_source_choice(
    result: dict[str, Any], state: AgentState, app_config: AppConfig,
) -> None:
    """소스 선택 칩·정정 답변 턴이면 고른 시스템을 기억한다(plans/132 N-10 · W5).

    스레드 범위 선택은 항상 남긴다(같은 스레드에서 다시 묻지 않는다). 확인된 사례(개인 범위)는
    기능이 켜졌고(TTL > 0) 라우트가 이 답변을 칩·「다른 소스로 보기」 응답으로 확인했을 때만 쓴다 —
    쓰기 진입점은 이 둘과 관리자 정리뿐이다(시스템 판정 저장 0).
    """
    chosen = [str(s) for s in state.get("selected_sources") or [] if s]
    if not chosen:
        return
    result[SOURCE_CHOICE_KEY] = {"system": chosen[0]}
    from src.schema_cache import source_memory as sm

    meta = state.get(SOURCE_SELECTION_META_KEY)
    ttl = sm.ttl_seconds(app_config)
    scope = sm.user_scope(state.get("user_id"))
    if ttl <= 0 or not isinstance(meta, dict) or not scope:
        return
    store = await sm.open_store(app_config)
    if store is None:
        return
    case = sm.build_case(
        _memory_text(state), chosen[:1], origin=str(meta.get("origin")), scope=scope,
        registry_fp=sm.registry_fingerprint(get_registry()), areas=meta.get("areas") or (),
    )
    await store.save(case, ttl_seconds=ttl)
    await _audit_source_memory(state, "write", case)
    logger.info("intent_planner: 소스 선택 기억 저장(plans/132 W5) — %s %s",
                case["origin"], chosen[0])


def _record_clarification(result: dict[str, Any]) -> None:
    """분해 되묻기 후보를 계획 출구에서 **항상** 쓴다(없으면 None · plans/121 TP-1.4 · N-3).

    `clarification_needed`는 선언된 요청 스코프 키라 쓰지 않은 턴에 앞 턴 값이 남는다(체크포인터
    델타 병합) — 매 턴 덮어쓴다. 소비는 plans/106 H1과 함께 넣고, 지금은 로그만 남긴다(내용은
    남기지 않는다 — 질문 길이·선택지 수만).
    """
    clarification = result.get("clarification_needed")
    if not isinstance(clarification, dict) or not clarification:
        result["clarification_needed"] = None
        return
    options = clarification.get("options")
    logger.info(
        "intent_planner: 분해 되묻기 후보 방출(소비 없음 · plans/106 H1) — 질문 %d자 · 선택지 %d개",
        len(str(clarification.get("question") or "")),
        len(options) if isinstance(options, list) else 0,
    )


def _normalize_plan_exit(result: dict[str, Any], state: AgentState) -> None:
    """계획 단일 출구 정규화 — 분기와 무관하게 같은 결정적 교정을 적용한다(in-place).

    교정 함수는 LLM 분해 경로(``_plan_turn``)와 같은 것이다(사본 금지 D-053). LLM 경로는
    이미 적용됐으므로 멱등이다. ``data_query`` 외 agent(캐시·일반 추론 등)는 건드리지 않는다.
    양식 채우기 턴은 제외한다 — 양식 단일 task(③·③.5)를 알람 템플릿으로 뒤집으면 양식 경로를 잃는다.
    """
    if state.get("template_structure") or state.get("uploaded_file"):
        return
    tasks = result.get("task_plan")
    if not isinstance(tasks, list) or not tasks:
        return
    result["task_plan"] = _coerce_alarm_intent(_coerce_process_intent(tasks))


#: 계획 경로 코드(plans/121 TP-0.1) — 어느 사전 처리 단락 또는 LLM 분해로 계획이 나왔는지.
#: 요청 스코프 상태 키 `plan_path`로 남아 계획 요약(`done.plan_summary`)이 읽는다(코드만).
PLAN_PATH_KEY = "plan_path"


def _on_path(plan: dict[str, Any], code: str) -> dict[str, Any]:
    plan[PLAN_PATH_KEY] = code
    return plan


#: 존 답변 턴에 직전 턴 복합 계획을 되살린 경로(plans/121 TP-1.2).
#: 종전 단일 task 복원은 `zone_reentry` 그대로다.
ZONE_REENTRY_RESTORED_PATH = "zone_reentry_restored"


#: 소스 선택 칩 답변 턴 경로(plans/132 N-10) — 단일 task · 직전 턴 복합 계획 복원.
SOURCE_REENTRY_PATH = "source_reentry"
SOURCE_REENTRY_RESTORED_PATH = "source_reentry_restored"


def _source_reentry_plan(
    state: AgentState, app_config: AppConfig, user_query: str,
) -> dict[str, Any] | None:
    """소스 선택 칩에서 비DB 소스를 고른 답변 턴의 계획 — 아니면 None(plans/132 N-10).

    고른 소스가 그사이 비활성이 되면 조회하지 않고 안내만 한다(G-1). 직전 턴이 복합 계획이었으면
    칩이 걸린 task만 그 처리기로 바꿔 계획을 되살린다(존 재진입 TP-1.2와 같은 스냅샷).
    """
    if (state.get("selected_db_ids") or state.get("template_structure")
            or state.get("uploaded_file")):
        return None
    reg = get_registry()
    chosen = [str(s) for s in state.get("selected_sources") or [] if reg.is_non_db_system(str(s))]
    if not chosen:
        return None
    from src.orchestration.conditional_agents import active_conditional_systems  # 지연 — 순환 방지
    from src.routing.source_hints import source_notice_text

    agent = active_conditional_systems(app_config).get(chosen[0])
    if agent is None:
        logger.info("intent_planner: 소스 선택 답변 턴 — %s 비활성 → 안내만(plans/132 G-1)",
                    chosen[0])
        plan = _single_task_plan("general_inference", user_query)
        plan["task_plan"][0]["direct_response"] = source_notice_text([reg.system_label(chosen[0])])
        plan["task_plan"][0]["source_notice"] = [chosen[0]]
        return _on_path(plan, SOURCE_REENTRY_PATH)
    restored = _restored_zone_reentry_plan(state, [], agent=agent)
    if restored is not None:
        logger.info("intent_planner: 소스 선택 답변 턴 — 직전 턴 계획 복원(칩 task → %s)", agent)
        return _on_path(restored, SOURCE_REENTRY_RESTORED_PATH)
    logger.info("intent_planner: 소스 선택 답변 턴 — %s 단일 task(plans/132 N-10)", agent)
    plan = _single_task_plan(agent, user_query)
    plan["task_plan"][0]["views"] = []
    return _on_path(plan, SOURCE_REENTRY_PATH)


def _doc_command_plan(
    state: AgentState, app_config: AppConfig, user_query: str,
) -> dict[str, Any] | None:
    """문서 검색 명령 턴 — `doc_query` 단일 task(활성) 또는 안내만(비활성) · 아니면 None
    (plans/138 W1·W2 · D-307).

    양식·존·소스 선택 답변 턴은 판정하지 않는다(그 턴의 결정적 단락이 우선한다).
    """
    if (state.get("template_structure") or state.get("uploaded_file")
            or state.get("selected_db_ids") or state.get("selected_sources")):
        return None
    from src.orchestration.conditional_agents import SOURCE_NOTICE_KEY  # 지연 — 순환 방지
    from src.orchestration.doc_command import DOC_COMMAND_PATH, parse_doc_command
    from src.orchestration.doc_query import DOC_QUERY_AGENT, DOC_SYSTEM
    from src.routing.source_hints import source_notice_text

    command = parse_doc_command(user_query, app_config)
    if command is None:
        return None
    if not command.active:
        logger.info("intent_planner: 문서 검색 명령 — 문서 검색 비활성 → 안내만(D-307 · D-293 G-1)")
        plan = _single_task_plan("general_inference", user_query)
        plan["task_plan"][0]["direct_response"] = source_notice_text([command.hint])
        plan["task_plan"][0][SOURCE_NOTICE_KEY] = [DOC_SYSTEM]
        return _on_path(plan, DOC_COMMAND_PATH)
    logger.info("intent_planner: 문서 검색 명령 — doc_query 단일 task(D-307) 보기=%s 질의=%r",
                command.views, command.query)
    plan = _single_task_plan(DOC_QUERY_AGENT, command.query)
    plan["task_plan"][0]["views"] = list(command.views)
    return _on_path(plan, DOC_COMMAND_PATH)


def _restored_zone_reentry_plan(
    state: AgentState, selected_db_ids: list[str], *, agent: str | None = None,
) -> dict[str, Any] | None:
    """존 답변 턴에 직전 턴 복합 계획을 복원한다 — 복원할 것이 없으면 None(plans/121 TP-1.2 · G-30).

    입력은 라우트가 Q-2 재사용 조건에서만 싣는 `reuse_task_plan`(직전 턴 존 역질문 단락의
    스냅샷)이다. 계획에서는 **게이트에 걸렸던 task만** `db_ids`를 선택 존으로 고정하고 나머지
    task에는 존 선택 적용 범위 표지(`ZONE_SELECTION_SCOPE_KEY` = 존 그룹 DB만)를 단다(§12.5) — 실행
    배관은 그 task를 분류한 뒤 존 그룹 대상만 선택 존으로 바꾼다. 상태는 pending으로 새로
    시작하고 `task_id`·`depends_on`·`input_from`·`order`는 그대로 둔다. 양식 턴은 복원하지
    않는다(D-150 단일 task). 스냅샷 모양이 어긋나면 None — 호출부가 종전 단일 task 경로로 간다.
    """
    snapshot = state.get("reuse_task_plan")
    if not isinstance(snapshot, dict):
        return None
    if state.get("template_structure") or state.get("uploaded_file"):
        return None
    raw_tasks = snapshot.get("tasks")
    gated = {str(t) for t in (snapshot.get("gated_task_ids") or [])}
    if not isinstance(raw_tasks, list) or len(raw_tasks) < 2 or not gated:
        return None
    tasks: list[dict[str, Any]] = []
    for raw in raw_tasks:
        if not isinstance(raw, dict) or not raw.get("task_id") or not raw.get("agent"):
            return None
        task = {k: (list(v) if isinstance(v, list) else v) for k, v in raw.items()}
        task["status"] = "pending"
        # 소스 선택 칩이 걸렸던 task(plans/132 N-10) — 칩 전 담당으로 되돌린 뒤 고른 소스를
        # 적용한다.
        slot = task.pop("source_slot", None)
        if isinstance(slot, dict) and slot.get("from_agent"):
            task["agent"] = slot["from_agent"]
        if str(task["task_id"]) in gated and agent:
            task["agent"] = agent
            task["views"] = []
        elif str(task["task_id"]) in gated:
            task["db_ids"] = list(selected_db_ids)
        elif not task.get("db_ids"):
            # 존 선택은 존 그룹 DB에만 적용한다 — 실행 배관이 분류 뒤 존 그룹 대상만 선택 존으로
            # 바꾼다(`subagents.run_data_query_pipeline` · 교차 시스템 task 보호)
            task[ZONE_SELECTION_SCOPE_KEY] = ZONE_GROUP_ONLY
        tasks.append(task)
    if not gated <= {str(t["task_id"]) for t in tasks}:
        return None
    return {"task_plan": tasks, "is_composite": True, "current_node": "intent_planner"}


async def _plan_turn(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """계획 본체 — 사전 처리(계층 A) 후 LLM 분해(계층 B).

    계층 A pre-check(멀티턴 pending 결합 보존)를 먼저 수행하고, 해당하지 않으면
    계층 B LLM 분해를 수행한다.

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - task_plan: TaskSpec 목록 (각 항목 status="pending")
        - is_composite: task 2개 이상 여부
        - current_node: "intent_planner"
        - (계층 B에서 모호성 방출 시) clarification_needed 보존
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    user_query = state["user_query"]

    # [계층 A] deterministic pre-check — semantic_router 우선순위 ①~③ 이식
    # ① pending_synonym_reuse → cache_management 강제
    if state.get("pending_synonym_reuse"):
        logger.info("intent_planner: pending_synonym_reuse 감지, cache_management 단일 task")
        return _on_path(_single_task_plan("cache_management", user_query), "pending_synonym_reuse")

    # ② 명시적 유사어 등록 요청 (멀티턴 두 번째 요청)
    parsed = state.get("parsed_requirements", {})
    if parsed.get("synonym_registration") and state.get("pending_synonym_registrations"):
        logger.info("intent_planner: 유사어 등록 요청 감지, synonym_registration 단일 task")
        return _on_path(
            _single_task_plan("synonym_registration", user_query), "synonym_registration"
        )

    # ②.3 앵커 없는 동의어 집합 선언(D-142) — 3단 pre-gate(semantic_router 우선순위 ③)와
    # 대칭. 트랙 A에는 이 분기가 없어 신규 셋 선언이 LLM 분해에서 synonym_registration
    # (pending 답변 턴 전용)으로 오분류되거나 field_mapper 전단에 가로채였다(2026-09-01
    # 라이브 실측 A-10). cache_management 노드가 같은 파서로 결정적 등록한다.
    if parse_synonym_set(user_query):
        logger.info("intent_planner: 동의어 집합 선언 감지(D-142), cache_management 단일 task")
        return _on_path(_single_task_plan("cache_management", user_query), "synonym_set")

    # ②.7 폼필 확인 이력 조회·삭제 (Plan 73 Phase 3, D-151 — FIX-21).
    # 반드시 ②.5(selected_db_ids)·③(mapped_db_ids)보다 먼저 판정해야 한다 —
    # 양식 업로드 턴은 field_mapper가 항상 mapped_db_ids를 세팅하므로 ③이 조기
    # 반환하면 이력 명령이 채우기(data_query)로 오탈취된다(라이브 실측
    # 2026-08-03: "기억된 답 보여줘"가 B0 채움 시도로 흘러감). 이력 명령은 DB
    # 라우팅 자체가 불필요 — 결정적 처리(LLM 미호출), direct_response 반환.
    # FIX-23: 파일 재첨부 없는 이력 명령도 직전 양식 시그니처(last_form_signature)로
    # 결정적 처리한다 — 커버리지 밖으로 새면 LLM이 "삭제했다"고 **환각 성공 안내**를
    # 하고 실제 삭제는 일어나지 않는다(라이브 실측 2026-08-03: Redis 항목 잔존).
    # 둘 다 없으면 안내 direct_response(환각 차단).
    _mq = _memory_query_normalized(user_query)  # '(주)기억장치' 오매칭 차단(FIX-20)
    if is_form_memory_command(user_query):
        if app_config is None:
            app_config = load_config()
        from src.utils.schema_utils import form_signature

        _template = state.get("template_structure")
        _sig = form_signature(_template) or state.get("last_form_signature")
        _panel = None
        if not _sig:
            text = (
                "기억된 답을 조회·삭제하려면 대상 양식이 필요합니다. 양식 파일을 "
                "첨부해 다시 요청해 주세요. (같은 세션에서 방금 다룬 양식이 있으면 "
                "파일 없이도 처리됩니다.)"
            )
            if _is_form_memory_shortcut(user_query):
                # '?'만 친 사용자에게는 문맥이 없다 — 단축키 의미를 먼저 밝힌다(D-186)
                text = "'?'는 양식에 저장된 값을 조회하는 단축키입니다. " + text
        elif any(k in _mq for k in _FORM_MEMORY_DELETE_KEYWORDS):
            text = await _form_memory_delete_response(state, user_query, app_config, _sig)
        else:
            # D-187: 조회 응답에 항목별 삭제 패널(구조화 컨텍스트)을 동봉 — 프론트가 체크박스·
            # 버튼으로 form_memory_delete를 보내면 라우트가 파이프라인·LLM 없이 결정적 삭제.
            text, _panel = await _form_memory_view_payload(state, app_config, _sig)
        logger.info("intent_planner: 폼필 확인 이력 조회/삭제 단락(D-151)")
        plan = _single_task_plan("general_inference", user_query)
        plan["task_plan"][0]["direct_response"] = text
        if _panel:
            plan["task_plan"][0]["form_memory_panel"] = _panel
        if _sig:
            plan["last_form_signature"] = _sig  # 직전 양식 컨텍스트 갱신(멀티턴 보존)
        return _on_path(plan, "form_memory")

    # ②.8 소스 선택 기억 보기·삭제 명령(plans/132 W5) — 양식 기억 명령(②.7)과 같은 결정적 단락.
    if _is_source_memory_command(user_query):
        return _on_path(
            await _source_memory_command_plan(state, app_config, user_query), "source_memory",
        )

    # ②.9 문서 검색 명령(plans/138 W1 · D-307) — 「RAG에서 … 검색해줘」처럼 등록 문서 소스 이름으로
    # 지목하고 검색을 명령하면 LLM 분해 없이 doc_query 단일 task로 고정한다(D-004 부기 — 등록 이름
    # 인식 · 내용어 판정 아님). 다른 소스 지목이 함께 있으면 판정하지 않는다(복합 질의는 종전 분해).
    doc_plan = _doc_command_plan(state, app_config, user_query)
    if doc_plan is not None:
        return doc_plan

    # ②.4 소스 선택 칩 답변 턴(plans/132 N-10) — 비DB 소스를 골랐으면 LLM 분해 없이 그 처리기로
    # 결정적 고정한다(존 선택 ②.5와 같은 모양). DB 소스를 골랐으면 칩이 DB 선택지를 함께 보냈으므로
    # 아래 ②.5가 그대로 처리한다.
    source_plan = _source_reentry_plan(state, app_config, user_query)
    if source_plan is not None:
        return source_plan

    # ②.5 존 역질문에서 사용자가 체크박스로 확정한 DB (Plan 75 §4) — LLM 분해를 건너뛰어
    # 자연어 재조합 없이 결정적 고정(mapped_db_ids 선례 동형). task.db_ids는 하류
    # run_data_query_pipeline이 classify_dbs를 우회하는 기존 배관을 그대로 탄다.
    selected_db_ids = state.get("selected_db_ids")
    if selected_db_ids:
        # 직전 턴 복합 계획 복원(plans/121 TP-1.2 · G-30) — 라우트가 Q-2 조건에서만 싣는다.
        # 없거나 복원할 수 없으면 아래 종전 단일 task 경로 그대로다.
        restored = _restored_zone_reentry_plan(state, list(selected_db_ids))
        if restored is not None:
            logger.info(
                "intent_planner: 존 답변 턴 — 직전 턴 계획 복원 %d건(게이트 task만 존 고정=%s)",
                len(restored["task_plan"]), selected_db_ids,
            )
            return _on_path(restored, ZONE_REENTRY_RESTORED_PATH)
        logger.info(
            "intent_planner: selected_db_ids 감지, data_query 단일 task (존 선택 고정=%s)",
            selected_db_ids,
        )
        return _on_path(_with_form_signature(
            _single_task_plan("data_query", user_query, db_ids=list(selected_db_ids)),
            state,
        ), "zone_reentry")

    # ③ field_mapper가 이미 대상 DB를 결정한 경우 (양식 업로드 시)
    mapped_db_ids = state.get("mapped_db_ids")
    if mapped_db_ids:
        logger.info("intent_planner: mapped_db_ids 감지, data_query 단일 task (DB 고정=%s)", mapped_db_ids)
        return _on_path(_with_form_signature(
            _single_task_plan("data_query", user_query, db_ids=mapped_db_ids), state,
        ), "mapped_db_ids")

    # ③.5 양식 업로드(template_structure) → 폼필 단일 task 고정 (Plan 73 D-150).
    # 양식 채우기는 의미상 단일 파이프라인 작업 — LLM 복합 분해가 서버정보/월지표를
    # 별도 task로 쪼개면 결과 병합이 2배 행이 된다(라이브 실측 2026-07-30 B0).
    # mapped_db_ids 미성립 턴(③ 미발동)도 결정적으로 단일화한다.
    if state.get("template_structure"):
        logger.info("intent_planner: template_structure 감지, data_query 단일 task (폼필 고정, D-150)")
        return _on_path(
            _with_form_signature(_single_task_plan("data_query", user_query), state), "form_fill"
        )

    # ③.6 파일 없는 폼필 요청 → 안내 응답으로 단락 (Plan 73 D-150).
    # template_structure 없이 "양식 채워줘"류가 LLM 분해로 가면 data_query가 존재하지
    # 않는 양식을 환각 처리한다(라이브 실측 2026-07-30 7차). 고정 안내문은 LLM을
    # 통과시키지 않고 direct_response로 결정적 반환한다(LLM 실패 시 일반 오류 강등 방지).
    if (any(k in user_query for k in _FORM_NOUN_KEYWORDS)
            and any(k in user_query for k in _FORM_FILL_VERB_KEYWORDS)):
        logger.info("intent_planner: 파일 없는 폼필 요청 감지 — 안내 응답 단락(D-150)")
        plan = _single_task_plan("general_inference", user_query)
        plan["task_plan"][0]["direct_response"] = _FORM_FILL_NO_FILE_GUIDANCE
        return _on_path(plan, "form_no_file")

    # ③.7 '가동률' 질의 → 미지원 지표 안내 단락 (D-200, D-150 동형).
    # 가동 시간 비율(uptime) 지표는 폴스타 3존 모두 미집계(2026-09-07 전수 실측) —
    # LLM 분해로 가면 CPU 사용률 임의 해석(공동존 오답) 또는 재계획 폭주(은행존 C-08)가
    # 된다. 고정 안내문을 direct_response로 결정적 반환한다(LLM 미통과).
    if _is_uptime_rate_query(user_query):
        logger.info("intent_planner: '가동률' 미지원 지표 감지 — 안내 응답 단락(D-200)")
        plan = _single_task_plan("general_inference", user_query)
        plan["task_plan"][0]["direct_response"] = _UPTIME_RATE_GUIDANCE
        return _on_path(plan, "uptime_guidance")

    # ③.8 사용법·지원 소스 문의 → general_inference 단일 task (plans/116 §10.3, D-038).
    # LLM 분해에 맡기면 분류가 흔들린다(분해 실패 시 data_query 폴백). 안내문은 노드가
    # 사용자 권한(allowed_db_ids)을 반영해 결정적으로 조립하므로 direct_response 는 두지 않는다.
    if is_usage_query(user_query):
        logger.info("intent_planner: 사용법 문의 감지 — general_inference 단일 task(plans/116)")
        return _on_path(_single_task_plan("general_inference", user_query), "usage_help")

    # [계층 B] LLM 복합 분해 — 후속 턴이면 압축 맥락(M3 보존 신호)을 주입한다(M1).
    conversation_context = state.get("conversation_context")
    decomposed = await _llm_decompose(
        llm, user_query, app_config, conversation_context=conversation_context
    )
    # 결정적 가드: 시간성 신호 없는 프로세스 조회는 실시간 API로 교정(D-041/D-046, 폴백 포함)
    tasks = _coerce_alarm_intent(_coerce_process_intent(decomposed["tasks"]))
    # Plan 78 W3-2(WU-18): 단건 호스트 조사를 중간 비용 경로로 교정.
    # 플래그 off면 no-op이다 — 프로세스/알람 교정 **뒤**에 두어 그 둘의 결과를 뒤집지 않는다.
    tasks = _coerce_host_inspect_intent(tasks, state, app_config)
    result: dict = {
        "task_plan": tasks,
        "is_composite": len(tasks) > 1,
        "current_node": "intent_planner",
    }
    # 모호성 방출 시 보존 (Phase 1은 인터럽트 없이 tasks로 진행 — §4.11)
    clarification = decomposed.get("clarification_needed")
    if clarification:
        result["clarification_needed"] = clarification
    # 분해 단계 강등·보정·미적용 사유를 사유 채널에 싣는다(D-203 §4.10 — 종전에는 `degraded`가
    # 이 노드에서 버려져 응답에 닿지 않았다).
    degraded = decomposed.get("degraded")
    if degraded:
        result["dependency_notes"] = list(state.get("dependency_notes") or []) + [
            {"kind": NOTE_DECOMPOSE, "task_id": None, "reason": d.get("reason"), "detail": d.get("detail", "")}
            for d in degraded if isinstance(d, dict) and d.get("detail")
        ]
    _record_decompose_fallback(result, decomposed, state, user_query)
    return _on_path(result, "llm_decompose")


#: 소스 선택 기억 명령(plans/132 W5) — 명사 + 동사(보기·삭제)가 함께 있을 때만(양식 기억 명령 선례).
_SOURCE_MEMORY_NOUNS = ("소스 기억", "소스 선택 기억")
_SOURCE_MEMORY_VIEW_WORDS = ("보여", "조회", "목록", "알려")
_SOURCE_MEMORY_DELETE_WORDS = ("삭제", "지워", "지우", "잊어")


def _is_source_memory_command(query: str) -> bool:
    text = query or ""
    return any(n in text for n in _SOURCE_MEMORY_NOUNS) and any(
        w in text for w in _SOURCE_MEMORY_VIEW_WORDS + _SOURCE_MEMORY_DELETE_WORDS)


async def _source_memory_command_plan(
    state: AgentState, app_config: AppConfig, user_query: str,
) -> dict[str, Any]:
    """「소스 기억 보여줘 / 삭제」 — 내 소스 선택 기억을 보여 주거나 지운다(LLM 0 · 개인 범위만)."""
    from src.schema_cache import source_memory as sm

    plan = _single_task_plan("general_inference", user_query)
    scope = sm.user_scope(state.get("user_id"))
    store = await sm.open_store(app_config) if sm.ttl_seconds(app_config) > 0 else None
    if sm.ttl_seconds(app_config) <= 0:
        text = "소스 선택 기억 기능이 꺼져 있습니다(관리자 설정). 기억된 소스 선택이 없습니다."
    elif store is None or not scope:
        text = "소스 선택 기억을 지금 조회할 수 없습니다(저장소 연결 또는 로그인 정보 없음)."
    elif any(w in user_query for w in _SOURCE_MEMORY_DELETE_WORDS):
        cases = await store.load(scope)
        removed = await store.delete(scope)
        for case in cases:
            await _audit_source_memory(state, "delete", case)
        text = (f"기억된 소스 선택 {removed}건을 모두 지웠습니다. 다음에 소스가 모호한 질문을 "
                "하시면 다시 어느 데이터 소스에서 조회할지 묻습니다.")
    else:
        reg = get_registry()
        cases = await store.load(scope)
        lines = [
            f"- 「{c.get('text')}」 → "
            f"{', '.join(reg.system_label(x) for x in c.get('sources') or [])}"
            f" (사용 {int(c.get('use_count') or 0)}회)"
            for c in cases
        ]
        text = (f"기억된 소스 선택이 {len(cases)}건 있습니다.\n\n" + "\n".join(lines)
                + "\n\n「소스 기억 삭제」라고 하시면 모두 지웁니다." if cases
                else "기억된 소스 선택이 없습니다. 소스 선택 칩에서 고르면 기억됩니다.")
    plan["task_plan"][0]["direct_response"] = text
    return plan


_FORM_MEMORY_ACTION_LABELS = {
    "blank": "공란 유지", "column": "DB 항목", "eav": "DB 항목", "literal": "직접 입력",
}


def build_form_memory_panel(
    signature: str | None, answers: dict | None, meta: dict | None
) -> dict | None:
    """저장 값 삭제 패널 컨텍스트(D-187) — 프론트 체크박스·버튼 렌더용 구조화 페이로드.

    {signature, display_name, entries: [{field, label, action, value}]}. 항목이 없으면 None.
    라우트의 삭제 후 재표시와 intent_planner의 조회 응답이 같은 shape를 쓴다(단일 출처).
    """
    if not signature or not answers:
        return None
    entries = []
    for field, ans in answers.items():
        entries.append({
            "field": field,
            "label": field.replace("|", " > "),
            "action": _FORM_MEMORY_ACTION_LABELS.get(ans.get("action"), str(ans.get("action"))),
            "value": ans.get("value"),
        })
    return {
        "signature": signature,
        "display_name": (meta or {}).get("display_name") or "이 양식",
        "entries": entries,
    }


async def _form_memory_view_payload(
    state: AgentState, app_config: AppConfig, signature: str | None = None
) -> tuple[str, dict | None]:
    """첨부 양식의 확인 이력을 조회 전용(TTL 미연장)으로 표시한다(D-151 Phase 3).

    Returns:
        (응답 텍스트, 삭제 패널 컨텍스트 또는 None) — 패널은 D-187.
    """
    from src.schema_cache.form_memory import load_form_memory_answers

    _sig, answers, meta = await load_form_memory_answers(
        state.get("template_structure"), app_config, touch=False, signature=signature,
    )
    if not answers or not meta:
        return (
            "이 양식에 기억된 답이 없습니다. 양식을 채운 뒤 미해결 항목 패널에서 "
            "'이 답을 기억'을 선택하면 저장됩니다(일정 기간 후 자동 만료). "
            + _FORM_MEMORY_SHORTCUT_HINT
        ), None
    lines = []
    for field, ans in answers.items():
        label = field.replace("|", " > ")
        act = _FORM_MEMORY_ACTION_LABELS.get(ans.get("action"), str(ans.get("action")))
        val = ans.get("value")
        suffix = f"({val})" if val not in (None, "") else ""
        lines.append(f"- {label}: {act}{suffix}")
    text = (
        f"'{meta.get('display_name', '이 양식')}' 양식에 기억된 답이 {len(answers)}건 "
        f"있습니다 (저장 {str(meta.get('created_at', ''))[:10]}, "
        f"{meta.get('use_count', 0)}회 사용).\n\n"
        + "\n".join(lines)
        + "\n\n아래 패널에서 항목을 선택해 삭제하거나, \"<필드명> 기억 삭제\"·"
        "\"기억 전부 삭제\"라고 요청할 수도 있습니다. 이 답들은 같은 양식을 채울 때 "
        "자동으로 반영됩니다. " + _FORM_MEMORY_SHORTCUT_HINT
    )
    return text, build_form_memory_panel(_sig or signature, answers, meta)


async def _form_memory_view_response(
    state: AgentState, app_config: AppConfig, signature: str | None = None
) -> str:
    """`_form_memory_view_payload`의 텍스트만(하위호환)."""
    text, _panel = await _form_memory_view_payload(state, app_config, signature)
    return text


async def _form_memory_delete_response(
    state: AgentState, user_query: str, app_config: AppConfig,
    signature: str | None = None,
) -> str:
    """첨부 양식의 확인 이력을 삭제한다 — 필드명 언급분만, '전부'류면 전체(D-151 Phase 3).

    필드 특정은 결정적 매칭(질의에 필드명 등장 여부)이며, 특정 실패 시 삭제하지 않고
    현황+지정 방법을 안내한다(침묵 오삭제 방지). 전체 삭제 응답에는 삭제된 내용
    전문을 표시한다(잘못 지웠을 때 재답변으로 저비용 복구).
    """
    from src.schema_cache.form_memory import (
        delete_form_memory_entries,
        load_form_memory_answers,
    )

    _sig, answers, _meta = await load_form_memory_answers(
        state.get("template_structure"), app_config, touch=False, signature=signature,
    )
    if not answers:
        return "이 양식에 기억된 답이 없어 삭제할 항목이 없습니다."
    q_norm = user_query.replace(" ", "")
    matched = [
        f for f in answers
        if f.replace(" ", "") in q_norm
        or f.split("|")[-1].replace(" ", "") in q_norm  # 복합명은 서브 라벨로도 매칭
    ]
    if any(k in user_query for k in _FORM_MEMORY_ALL_KEYWORDS) and not matched:
        removed, display = await delete_form_memory_entries(
            state.get("template_structure"), app_config, None, signature=signature,
        )
        detail = "\n".join(f"- {f.replace('|', ' > ')}" for f in answers)
        return (
            f"'{display or '이 양식'}'의 기억 {removed}건을 모두 삭제했습니다:\n{detail}\n\n"
            "다시 기억시키려면 양식 채우기 후 패널에서 답변하고 '이 답을 기억'을 선택하세요."
        )
    if not matched:
        listing = ", ".join(f.replace("|", " > ") for f in answers)
        return (
            f"삭제할 항목을 특정하지 못했습니다. 현재 기억된 답: {listing}\n"
            "특정 항목: \"<필드명> 기억 삭제\" / 전체: \"기억 전부 삭제\"라고 요청해주세요."
        )
    removed, display = await delete_form_memory_entries(
        state.get("template_structure"), app_config, matched, signature=signature,
    )
    shown = ", ".join(f.replace("|", " > ") for f in matched)
    return (
        f"'{display or '이 양식'}'에서 {shown}의 기억 {removed}건을 삭제했습니다. "
        "해당 항목은 다음 채우기에서 다시 질문됩니다."
    )


def _with_form_signature(plan: dict, state: AgentState) -> dict:
    """양식 턴이면 last_form_signature를 계획에 실어 체크포인터에 보존한다(FIX-23).

    파일 재첨부 없는 "기억 보여줘/삭제"가 직전 양식을 가리키게 하는 멀티턴 컨텍스트.
    업로드 턴은 ②.5(존 재개)·③(mapped_db_ids)·③.5 어느 분기로든 반환될 수 있으므로
    세 분기 공통으로 적용한다.
    """
    template = state.get("template_structure")
    if template:
        from src.utils.schema_utils import form_signature

        sig = form_signature(template)
        if sig:
            plan["last_form_signature"] = sig
    return plan


def _single_task_plan(
    agent: str,
    query: str,
    *,
    db_ids: Optional[list[str]] = None,
) -> dict:
    """단일 task 계획을 생성한다 (계층 A pre-check 및 폴백용).

    Args:
        agent: 담당 agent 명
        query: sub_query로 사용할 질의
        db_ids: data_query 고정 DB 목록 (선택, 양식 업로드 시)

    Returns:
        task_plan/is_composite/current_node를 포함한 State 갱신 dict
    """
    task: dict = {
        "task_id": "t1",
        "agent": agent,
        "sub_query": query,
        "depends_on": [],
        "input_from": [],
        "order": 1,
        "status": "pending",
    }
    if db_ids:
        task["db_ids"] = db_ids
    return {
        "task_plan": [task],
        "is_composite": False,
        "current_node": "intent_planner",
    }


def _build_context_block(
    conversation_context: Optional[dict], user_query: str = ""
) -> str:
    """후속 턴 분해용 압축 맥락 블록을 만든다 (Plan 50 M1/M3, B3).

    첫 턴(맥락 없음)이거나 turn_count<=1이면 빈 문자열을 반환한다.
    원시 메시지 히스토리는 절대 넣지 않고, context_resolver가 보존한 압축 신호
    (previous_location/previous_db_ids/previous_entities/요약)만 1블록으로 주입한다.

    이번 턴 원문에 위치 표면어(은행존/공동존/김포 등)가 명시돼 있으면 **직전 위치/DB
    줄을 주입하지 않는다** — "명시 위치 최우선, 승계 금지" 프롬프트 규칙을 LLM이 어기고
    직전 위치와 병합해 sub_query를 오염시킨 실측 사례(2026-07-16: "은행존 알람"이
    "김포 은행 공동존…"으로 재작성돼 gp 오라우팅)가 있어, 입력에서 오염원 자체를
    제거해 결정적으로 차단한다. 직전 서버 엔티티·요약 줄은 유지(D-055 지시어 해소 보존).

    Args:
        conversation_context: context_resolver가 채운 맥락 dict (없으면 None)
        user_query: 이번 턴 사용자 원문(위치 명시 게이트 판정용)

    Returns:
        HumanMessage 앞에 붙일 압축 맥락 블록 텍스트(없으면 "")
    """
    if not conversation_context:
        return ""
    if conversation_context.get("turn_count", 0) <= 1:
        return ""

    has_explicit_location = any(
        term_in_text(term, user_query or "") for term in LOCATION_HINT_TERMS
    )
    # 이번 턴에 지시어("해당/그/위 … 서버")가 있을 때만 직전 서버 엔티티를 주입한다
    # (D-153 후속1). previous_entities는 직전 턴이 대량 조회였으면 상한 샘플
    # (_MAX_ENTITY_ROWS)일 뿐 스코프가 아니다 — "대상 미명시 → 직전 값 보존" 규칙과
    # 결합되면 LLM이 새 전량 후속 질의를 샘플 서버 몇 대로 좁혀 재작성한다
    # (2026-08-04 라이브 실측: gp/yd 전량 조회 후 "OS 종류…확인" 후속이 4개 서버로 축소).
    # 위치 명시 게이트(2026-07-16)와 동형 — 오염원을 입력에서 결정적으로 제거한다.
    has_demonstrative = refers_to_demonstrative_server(user_query or "")

    location = conversation_context.get("previous_location") or ""
    db_ids = conversation_context.get("previous_db_ids") or []
    entities = conversation_context.get("previous_entities") or []
    summary = conversation_context.get("previous_results_summary") or ""

    lines = ["## 이전 대화 맥락 (후속 턴 분해 시 활용)"]
    if has_explicit_location:
        # 이번 턴에 위치가 명시됨 — 직전 위치/DB를 아예 제공하지 않아 병합 오염을 차단한다.
        lines.append(
            "- 이번 질의에 위치가 명시되어 직전 위치/DB는 제공하지 않는다. "
            "**이번 질의에 적힌 위치만** sub_query에 사용하라(다른 위치를 추가하지 말 것)."
        )
    else:
        lines.append(f"- 직전 대상 위치/환경: {location or '(미상)'}")
        lines.append(f"- 직전 대상 DB 후보: {', '.join(db_ids) if db_ids else '(미상)'}")
    if has_demonstrative:
        # 식별 엔티티는 상한 내 소량만 표면화(토큰 절약 — 2026-06-11 상한 원칙).
        entity_strs: list[str] = []
        seen: set[str] = set()
        for e in entities[:10]:
            if not isinstance(e, dict):
                continue
            field = e.get("field", "")
            value = e.get("value", "")
            token = f"{field}={value}"
            if value != "" and token not in seen:
                seen.add(token)
                entity_strs.append(token)
        entity_line = ", ".join(entity_strs) if entity_strs else "(없음)"
        lines.append(f"- 직전 대상 서버/장비: {entity_line}")
    lines += [
        f"- 직전 작업 요약: {summary or '(없음)'}",
        "",
        '지시어("해당 서버", "그 장비", "위 결과", "이 DB") 해소 규칙:',
        "- 사용자가 이번 질의에서 새 위치/DB/대상을 **명시하지 않으면** 위 직전 값을 sub_query에 그대로 보존하라.",
        "- 예: 후속 질의가 \"해당 서버의 프로세스\"이면 sub_query에 직전 위치·서버 식별자를 포함시켜라",
        "  (예: \"김포 운영 폴스타의 ### 서버 현재 프로세스 리스트\").",
        "- 사용자가 명시적으로 다른 위치/DB/대상을 지정하면 그 신호를 최우선으로 따르라(승계하지 말 것).",
        "",
    ]
    return "\n".join(lines)


def _plan_from_model(model: DecomposedPlan, user_query: str) -> dict:
    """검증된 DecomposedPlan을 기존 반환 계약(dict)으로 변환한다.

    상태 저장은 dict를 유지한다 — AgentState가 TypedDict이고 LangGraph 체크포인터가
    직렬화하므로 모델 객체를 그대로 싣지 않는다.
    """
    tasks: list[dict] = []
    for i, t in enumerate(model.tasks, 1):
        d = t.model_dump()
        d["order"] = d.get("order") or i
        d["status"] = "pending"
        tasks.append(d)
    if not tasks:
        logger.warning("intent_planner 구조화 분해 결과가 비었음 — 단일 data_query 폴백")
        return {
            "tasks": [{
                "task_id": "t1", "agent": "data_query", "sub_query": user_query,
                "depends_on": [], "input_from": [], "order": 1, "status": "pending",
            }],
            "clarification_needed": None,
            _FALLBACK_MARK: {"code": DECOMPOSE_FALLBACK_EMPTY_STRUCTURED, "error_class": ""},
        }
    return {"tasks": tasks, "clarification_needed": model.clarification_needed}


async def _llm_decompose(
    llm: BaseChatModel,
    user_query: str,
    app_config: AppConfig,
    *,
    conversation_context: Optional[dict] = None,
) -> dict:
    """LLM으로 질의를 sub-task 목록으로 분해한다.

    INTENT_PLANNER_SYSTEM_TEMPLATE로 LLM을 호출하고 JSON을 파싱한다.
    후속 턴(conversation_context 있음)이면 HumanMessage 앞에 압축 맥락 블록을 주입한다(M1).
    실패/빈 결과면 단일 data_query task로 폴백한다.

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 질의
        app_config: 앱 설정
        conversation_context: context_resolver가 보존한 직전 턴 압축 신호 (없으면 첫 턴)

    Returns:
        {"tasks": [...], "clarification_needed": {...} | None}
        (각 task에 누락 키가 보정되고 status="pending"이 부여됨)
    """
    fallback = {
        "tasks": [
            {
                "task_id": "t1",
                "agent": "data_query",
                "sub_query": user_query,
                "depends_on": [],
                "input_from": [],
                "order": 1,
                "status": "pending",
            }
        ],
        "clarification_needed": None,
    }

    context_block = _build_context_block(conversation_context, user_query)
    human_content = f"{context_block}{user_query}" if context_block else user_query

    messages: list[BaseMessage] = [
        SystemMessage(content=_planner_system_prompt(app_config))
    ]
    if isinstance(llm, KBGenAIChat):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=human_content))

    result = await _decompose_once(llm, messages, user_query, app_config, fallback)
    # 분해 계약(D-203 · plans/88 §4.2-b·§4.8): DAG 검증·순차 표지 — 플래그 off면 no-op(바이트 동일).
    result = await _enforce_plan_contract(llm, messages, user_query, app_config, fallback, result)
    if _capability_ownership_on(app_config):
        _sanitize_task_capabilities(result)
    _sanitize_task_areas(result)
    if _task_frame_on(app_config):
        result = _apply_task_frames(result, user_query, context_block, fallback)
    if _nonsql_agents(app_config):
        _sanitize_task_views(result, app_config)
        _restore_doc_query_text(result, user_query, conversation_context)
    return result


def _nonsql_agents(app_config: AppConfig) -> tuple[str, ...]:
    """활성인 비SQL 처리기 이름(plans/125 A-5 · plans/127 — `apm_query`·`doc_query`) — 비활성이면
    빈 튜플(분해 바이트 불변)."""
    from src.orchestration.conditional_agents import active_conditional_agents  # 지연 — 순환 방지

    return tuple(active_conditional_agents(app_config))


def _sanitize_task_views(result: dict[str, Any], app_config: AppConfig) -> None:
    """조건부 처리기 task 의 `views`를 자기 닫힌 어휘로 정제하고, 다른 담당의 `views`는 떼어 낸다.

    구조화·JSON·폴백 모든 경로의 결과에 같은 규칙을 적용하려고 분해의 마지막 한 곳에서 한다.
    문서 task(`doc_query`)의 의존은 끊고 사유를 경과 노트로 남긴다(간선 없음 · plans/127 G-10).
    """
    from src.orchestration.conditional_agents import sanitize_task_views

    cut = sanitize_task_views(result, app_config)
    if cut:
        logger.info("intent_planner: 문서 task 의존 제거(간선 없음 · plans/127) — %s", cut)
        result["degraded"] = list(result.get("degraded") or []) + [_degraded(
            "doc_task_dependency_cut",
            "문서 답은 다른 조회의 입력으로 쓰지 않았습니다"
            "(문서 검색과 다른 조회는 따로 답합니다).",
        )]


def _restore_doc_query_text(
    result: dict[str, Any], user_query: str, conversation_context: Optional[dict],
) -> None:
    """첫 턴의 단일 문서 task는 분해 LLM이 다시 쓴 질의 대신 **원문**으로 검색한다(plans/138 W4).

    문서 엔진은 받은 질의를 그대로 보낸다(plans/126 §4.5 규칙 1) — 재작성은 어휘 검색·HyDE 결과를
    바꿔 관리자 「문서 검색 시험」과 다른 근거를 가져왔다. 후속 턴은 재작성이 생략된 맥락(「그럼 백업
    담당자는?」)을 채우므로 종전대로 둔다. 복합 계획도 task 질의가 원문 일부라 그대로 둔다.
    """
    tasks = [t for t in result.get("tasks") or [] if isinstance(t, dict)]
    if conversation_context or len(tasks) != 1 or tasks[0].get("agent") != "doc_query":
        return
    if tasks[0].get("sub_query") != user_query:
        logger.info("intent_planner: 단일 문서 task 원문 검색(plans/138 W4) — 재작성 %r → 원문",
                    tasks[0].get("sub_query"))
        tasks[0]["sub_query"] = user_query


def _apply_task_frames(
    result: dict[str, Any], user_query: str, context_block: str, fallback: dict[str, Any],
) -> dict[str, Any]:
    """원문 조각으로 task 질의를 만들고 계획을 검증한다(plans/111 §5.3 · LLM 0회).

    - 폴백 계획(조각 없는 원문 단일 task)은 그대로 둔다 — 이미 가장 보수적인 형태다.
    - 검증 통과: 각 task의 ``sub_query``를 조각으로 다시 만든다(LLM이 쓴 문장은 버린다).
    - 검증 실패: 원문 단일 task로 되돌리고 ``degraded`` 사유를 싣는다(침묵 폴백 금지 —
      사유는 ``intent_planner``가 ``dependency_notes``로 응답까지 옮긴다).
    """
    tasks = [t for t in result.get("tasks") or [] if isinstance(t, dict)]
    if not any(task_spans(t) for t in tasks):
        if all(str(t.get("sub_query") or "") == user_query for t in tasks):
            return result
    verdict = verify_task_frames(tasks, user_query, context_block)
    if not verdict.ok:
        logger.warning(
            "intent_planner task 프레임 계약 위반 → 원문 단일 task: %s", list(verdict.violations),
        )
        degraded = {**fallback, "tasks": [dict(t) for t in fallback["tasks"]]}
        degraded["degraded"] = list(result.get("degraded") or []) + [_degraded(
            "task_frame_contract_violation", "; ".join(verdict.violations),
        )]
        return degraded
    for task in tasks:
        task["sub_query"] = render_task_query(task, user_query)
    return result


def _capability_ownership_on(app_config: AppConfig) -> bool:
    """답변 영역 소유 플래그(plans/102 X-7) — 호출부가 넘긴 설정에서 읽는다(기동 시 1회 해석)."""
    return bool(getattr(getattr(app_config, "router", None), "capability_ownership_enabled", False))


def _planner_system_prompt(app_config: AppConfig) -> str:
    """분해 시스템 프롬프트.

    off면 기본 템플릿 그대로(바이트 동일), on이면 소유표·교차 예시 삽입본이다. 환경어 자리는
    마지막에 레지스트리 정본으로 채운다(plans/121 TP-11.2 — 종전 사본과 바이트 동일).
    """
    if not _capability_ownership_on(app_config):
        base = INTENT_PLANNER_SYSTEM_TEMPLATE
    else:
        base = _render_planner_ownership_prompt(tuple(app_config.multi_db.get_active_db_ids()))
    # 답변 영역·요청 소스 칸(plans/132 N-5) — 항상. 골격 줄을 바꾸므로 다른 삽입 렌더보다 먼저다.
    base = _render_areas_prompt(base, _area_rows(_active_nonsql_systems(app_config)))
    if _task_frame_on(app_config):
        base = _render_task_frame_prompt(base)
    agents = _nonsql_agents(app_config)
    if "apm_query" in agents:
        from src.orchestration.apm_query import render_agent_line, render_view_rows

        base = _render_apm_prompt(base, render_agent_line(), render_view_rows())
    if "doc_query" in agents:
        from src.orchestration import doc_query

        base = _render_doc_prompt(
            base, doc_query.render_agent_line(), doc_query.render_view_rows(app_config),
            doc_query.example_view(app_config),
        )
    return _render_planner_environment_terms(base, get_registry().environment_terms)


def _active_nonsql_systems(app_config: AppConfig) -> frozenset[str]:
    """활성인 비DB 시스템 코드(plans/134 W2 — `active_only` 영역 렌더 판정 입력)."""
    from src.orchestration.conditional_agents import active_conditional_systems  # 지연 — 순환 방지

    return frozenset(active_conditional_systems(app_config))


def _area_rows(active_systems: frozenset[str] = frozenset()) -> str:
    """영역 카탈로그 표 행 — 레지스트리 등록 전체(판정 재료 · G-7 (a) · 사본 금지 D-053).

    `active_only` 영역(plans/134 W2)은 소유 시스템이 활성일 때만 싣는다 — 비활성 배포의 분해
    프롬프트는 종전 바이트 그대로다.
    """
    reg = get_registry()
    return "\n".join(
        f"| {spec.code} | {spec.label or spec.code} |" for spec in reg.capability_specs()
        if not spec.active_only
        or (active_systems
            and any(owner in active_systems for owner in reg.capability_owners(spec.code)))
    )


@lru_cache(maxsize=8)
def _render_areas_prompt(base: str, area_rows: str) -> str:
    """답변 영역 칸 렌더 캐시(plans/132 N-5) — 기동 시 1회(프롬프트 접두 고정 · KV 캐시)."""
    return render_intent_planner_areas_template(base, area_rows)


@lru_cache(maxsize=8)
def _render_apm_prompt(base: str, agent_line: str, view_rows: str) -> str:
    """APM 활성 렌더 캐시(plans/125 A-5) — 기동 시 1회 렌더(프롬프트 접두 고정 · KV 캐시)."""
    return render_intent_planner_apm_template(base, agent_line, view_rows)


@lru_cache(maxsize=8)
def _render_doc_prompt(base: str, agent_line: str, view_rows: str, example_view: str) -> str:
    """문서 라우팅 활성 렌더 캐시(plans/127 W2) — APM 렌더와 같은 규율(삽입만 · 기동 시 1회)."""
    return render_intent_planner_doc_template(base, agent_line, view_rows, example_view)


@lru_cache(maxsize=8)
def _render_planner_environment_terms(prompt: str, environment_terms: tuple[str, ...]) -> str:
    """환경어 자리 채움 — (프롬프트, 레지스트리 환경어) 단위 캐시(plans/121 TP-11.2 · D-131).

    레지스트리는 프로세스 캐시(`get_registry`)라 값은 기동 시 1회 정해진다 — 요청마다 접두가 같다.
    """
    return render_intent_planner_environment_terms(prompt, environment_terms)


def _task_frame_on(app_config: AppConfig) -> bool:
    """task 프레임 계약 플래그(plans/111 C-3) — 호출부 설정에서 읽는다(기동 시 1회 해석).

    ``is True``로 판정한다 — MagicMock 설정(테스트)의 속성이 참으로 평가돼 플래그가 켜진 것처럼
    동작하지 않게 한다.
    """
    return getattr(getattr(app_config, "composite", None), "task_frame_enabled", False) is True


@lru_cache(maxsize=8)
def _render_task_frame_prompt(base: str) -> str:
    """기본(또는 소유) 프롬프트 단위 캐시 — 기동 시 1회 렌더(프롬프트 접두 고정 · KV 캐시)."""
    return render_intent_planner_task_frame_template(base)


@lru_cache(maxsize=8)
def _render_planner_ownership_prompt(active_db_ids: tuple[str, ...]) -> str:
    """활성 DB 목록 단위 캐시 — 기동 시 1회 렌더(프롬프트 접두 고정 · KV 캐시)."""
    rows = render_ownership_rows(active_db_ids, with_db_ids=False)
    if not rows:
        logger.warning(
            "답변 영역 소유 플래그 on이나 활성 DB에 소유 선언이 없다 — 기본 분해 프롬프트 사용"
        )
        return INTENT_PLANNER_SYSTEM_TEMPLATE
    # 교차 예시는 소유 시스템이 둘 이상 활성일 때만(라우터 프롬프트와 같은 규칙)
    return render_intent_planner_ownership_template(
        rows, with_examples=active_owner_system_count(active_db_ids) >= 2,
    )


def _sanitize_task_capabilities(result: dict[str, Any]) -> None:
    """분해 task의 `capability`를 카탈로그 코드로 정제한다(모르는 코드·형식 오류 → 빈 문자열).

    구조화·JSON 파싱·재요청·폴백 모든 경로의 결과에 같은 규칙을 적용하려고 분해의 마지막
    한 곳에서 한다.
    빈 문자열은 "소유 적용 없음"이다 — 실행부가 종전 분류 경로를 그대로 탄다.
    """
    known = known_capability_codes()
    for task in result.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        raw = task.get("capability")
        code = sanitize_capability_code(raw, known)
        if raw not in (None, "") and not code:
            logger.warning("분해 task 답변 영역 탈락(카탈로그 밖): task=%s capability=%r",
                           task.get("task_id"), raw)
        task["capability"] = code


#: 요청 소스 칸 길이 상한 — 사용자가 쓴 이름 한 개(문장이 오면 자른다).
_REQUESTED_SOURCE_MAX = 40


def _sanitize_task_areas(result: dict[str, Any]) -> None:
    """분해 task의 `areas`·`requested_source`를 정제한다(plans/132 N-5 — 모든 경로의 마지막 한 곳).

    `areas`는 레지스트리 영역 카탈로그 코드만 남긴다(등록 전체 · 순서 유지 · 중복 제거 — 모르는
    코드는 버리고 로그). 빈 목록은 「영역 없음」이다 — 소스 슬롯이 종전 경로를 탄다(K-3 칸 누락
    폴백).
    `requested_source`는 문자열 한 개(앞뒤 공백 제거 · 길이 상한)다. 소스로 푸는 일은 계획 출구가
    한다 — 이 턴의 결정적 지목과 맞을 때만 쓴다(`conditional_agents.apply_source_selection`).
    """
    known = known_capability_codes()
    for task in result.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        kept, dropped = sanitize_capability_list(task.get("areas"), known)
        if dropped:
            logger.warning("분해 task 답변 영역 탈락(카탈로그 밖): task=%s areas=%r",
                           task.get("task_id"), dropped)
        task["areas"] = kept
        raw = task.get("requested_source")
        task["requested_source"] = (
            raw.strip()[:_REQUESTED_SOURCE_MAX] if isinstance(raw, str) else ""
        )


def _degraded(reason: str, detail: str, *, attempts: int = 1) -> dict:
    """분해 단계 강등 사유 1건 — 구조화 경로(F4)와 같은 shape."""
    return {
        "stage": "intent_planner._llm_decompose", "reason": reason,
        "attempts": attempts, "detail": detail[:500],
    }


# ── 사유 없는 분해 폴백(plans/121 TP-1.6 · N-7) ─────────────────────────────
#: 요청 스코프 상태 키 — LLM 분해가 원문 단일 task로 폴백한 원인 코드(관측 전용). 계획 요약
#: (`done.plan_summary.decompose_fallback`)이 코드만 읽는다. 폴백이 없으면 쓰지 않는다.
DECOMPOSE_FALLBACK_KEY = "decompose_fallback"
#: 분해 결과 dict 안의 폴백 표지(내부) — `_decompose_once`·`_plan_from_model`이 싣는다.
_FALLBACK_MARK = "fallback_cause"
# 원인 코드 — D-241 판정 어휘(`invalid`·`timeout`·`clarify_blocked`)와 겹치지 않게 짓는다.
DECOMPOSE_FALLBACK_LLM_ERROR = "llm_error"            # JSON 경로 호출 예외
DECOMPOSE_FALLBACK_MALFORMED = "malformed_output"     # JSON 없음 · tasks 없음/빈 목록 · 유효 task 0
DECOMPOSE_FALLBACK_EMPTY_STRUCTURED = "empty_structured"  # 구조화 출력의 tasks가 빔
#: 순차 표지가 있는 질의의 분해 폴백 사용자 노트 사유(`decompose` 종류).
REASON_DECOMPOSE_FALLBACK = "decompose_fallback"
_DECOMPOSE_FALLBACK_HEADS = {
    DECOMPOSE_FALLBACK_LLM_ERROR: "질의 분해 호출이 실패해({error_class})",
    DECOMPOSE_FALLBACK_MALFORMED: "질의 분해 결과의 형식이 맞지 않아",
    DECOMPOSE_FALLBACK_EMPTY_STRUCTURED: "질의 분해 결과가 비어",
}


def _mark_fallback(
    fallback: dict[str, Any], code: str, error_class: str = "",
) -> dict[str, Any]:
    """원문 단일 task 폴백 사본에 원인 표지를 단다 — 공유 `fallback` 객체는 건드리지 않는다."""
    out = {**fallback, "tasks": [dict(t) for t in fallback.get("tasks") or []]}
    out[_FALLBACK_MARK] = {"code": code, "error_class": error_class}
    return out


def decompose_fallback_note(code: str, error_class: str = "") -> dict[str, Any]:
    """순차 표지 질의의 분해 폴백 경과 노트 — 원인은 예외 클래스명만(원문은 로그에만)."""
    head = _DECOMPOSE_FALLBACK_HEADS.get(code, "질의를 여러 단계로 나누지 못해").format(
        error_class=error_class or "호출 오류",
    )
    return {
        "kind": NOTE_DECOMPOSE, "task_id": None, "reason": REASON_DECOMPOSE_FALLBACK,
        "detail": (
            f"{head} 순차 분해가 적용되지 않았고 한 번의 조회로 처리했습니다"
            "(앞 결과 → 뒤 조회 대상 배선 없음)."
        ),
    }


def _record_decompose_fallback(
    result: dict[str, Any], decomposed: dict[str, Any], state: AgentState, user_query: str,
) -> None:
    """분해 폴백을 관측 필드로 남기고, 순차 표지가 있을 때만 사용자 노트를 단다(in-place).

    단일 의도 질의의 폴백 계획(원문 단일 task)은 정상 분해 결과와 같아 노트는 소음이다 — 관측
    필드만 남긴다. 순차 표지가 있으면 앞 결과 → 뒤 조회 배선이 빠진 것이라 알린다. 이미 분해 사유
    노트가 있으면(되먹임 재요청 뒤 `sequential_not_applied` 등) 더하지 않는다.
    재계획 의미(§12.4): `decompose` 종류는 미완이지만 순차 표지 턴은 원래 결정적 성공 종료
    대상이 아니므로(`replanner._all_tasks_succeeded`) 평가 LLM 호출 수가 늘지 않는다.
    """
    mark = decomposed.get(_FALLBACK_MARK)
    if not isinstance(mark, dict):
        return
    code = str(mark.get("code") or "")
    result[DECOMPOSE_FALLBACK_KEY] = code
    if decomposed.get("degraded") or not has_sequential_marker(user_query):
        return
    result["dependency_notes"] = list(state.get("dependency_notes") or []) + [
        decompose_fallback_note(code, str(mark.get("error_class") or ""))
    ]


async def _enforce_plan_contract(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    user_query: str,
    app_config: AppConfig,
    fallback: dict,
    result: dict,
) -> dict:
    """DAG 검증(§4.8)·순차 표지(§4.2-b)를 적용하고 필요하면 **되먹임 1회**만 재요청한다.

    두 판정은 재시도 예산을 공유한다(총 LLM 호출 ≤ 2). 되먹임은 user 메시지 말미에만 사유를
    덧붙인다(시스템 접두 불변 — KV 캐시). 재요청 뒤에도 위반이면 보정 가능한 것은 보정하고,
    불가하면 단일 폴백 + `degraded` 사유(침묵 폴백 금지). 표지가 있는데 여전히 단일이면 실행은
    하되 `sequential_not_applied`를 남긴다.
    """
    cfg = getattr(app_config, "composite", None)
    dag_on = bool(getattr(cfg, "plan_dag_validation_enabled", False))
    replan_on = bool(getattr(cfg, "sequential_replan_enabled", False))
    if not replan_on:
        # off 관측(plans/88 §11 · 게이트의 `관측(off)` 로그와 대칭) — 표지가 있는데 순차 배선이 없으면 로그만.
        plain = list(result.get("tasks") or [])
        if has_sequential_marker(user_query) and not any(t.get("input_from") for t in plain):
            logger.info("순차 재분해 관측(off) — 표지 있음·순차 배선 없음(task %d건)", len(plain))
    if not (dag_on or replan_on):
        return result

    def _apply(res: dict, *, final: bool) -> tuple[dict, list[str]]:
        hints: list[str] = []
        tasks = list(res.get("tasks") or [])
        notes = list(res.get("degraded") or [])
        if dag_on:
            tasks, violations, fixes = validate_plan_dag(tasks)
            if fixes:
                notes.append(_degraded("plan_dag_autofixed", "; ".join(fixes)))
            if violations:
                if final:
                    logger.warning("intent_planner DAG 위반 잔존 — 단일 data_query 폴백: %s", violations)
                    out = dict(fallback)
                    out["degraded"] = notes + [_degraded("plan_dag_invalid", "; ".join(violations), attempts=2)]
                    return out, []
                hints += [f"계획 오류: {v}" for v in violations]
        if replan_on and has_sequential_marker(user_query) and not any(t.get("input_from") for t in tasks):
            if final:
                notes.append(_degraded(
                    "sequential_not_applied",
                    "순차 분해가 적용되지 않아 한 번의 조회로 처리했습니다(앞 결과 → 뒤 조회 대상 배선 없음).",
                    attempts=2,
                ))
            else:
                hints.append(
                    "이 질의는 앞 조회 결과가 뒤 조회의 대상입니다 — 같은 agent라도 2개 task로 나누고, "
                    "뒤 task의 depends_on과 input_from에 앞 task의 task_id를 넣으세요."
                )
        out = dict(res)
        out["tasks"] = tasks
        if notes:
            out["degraded"] = notes
        return out, hints

    checked, hints = _apply(result, final=False)
    if not hints:
        return checked

    # 되먹임 1회 — 마지막 HumanMessage 말미에 사유를 덧붙인다.
    last = messages[-1]
    retry_messages = list(messages[:-1]) + [HumanMessage(
        content=f"{getattr(last, 'content', user_query)}\n\n## 재요청 사유\n" + "\n".join(f"- {h}" for h in hints)
    )]
    logger.info("intent_planner 되먹임 재요청 1회(D-203): %s", hints)
    retried = await _decompose_once(llm, retry_messages, user_query, app_config, fallback)
    final, _ = _apply(retried, final=True)
    prior_notes = list(checked.get("degraded") or [])
    if prior_notes:
        final["degraded"] = prior_notes + list(final.get("degraded") or [])
    return final


async def _decompose_once(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    user_query: str,
    app_config: AppConfig,
    fallback: dict,
) -> dict:
    """LLM 1회 호출로 분해한다(구조화 경로 → JSON 파싱 폴백)."""
    # 구조화 출력 경로 (E-3a · D-169). 플래그 off면 None을 돌려받아 기존 파싱으로 내려간다 —
    # 기존 경로를 지우지 않는다(off가 상시 존재한다).
    parsed: dict | None = None
    ownership_on = _capability_ownership_on(app_config)
    extra_agents = _nonsql_agents(app_config)
    plan_model: type[DecomposedPlan] = (
        # 소유 플래그 on이면 `capability` 필드가 있는 서브클래스 — off 스키마는 종전 그대로. task
        # 프레임 on이면 원문 조각(`spans`) 서브클래스(소유 필드 포함 — plans/111 C-3).
        SpanDecomposedPlan if _task_frame_on(app_config)
        else (OwnershipDecomposedPlan if ownership_on else DecomposedPlan)
    )
    if extra_agents:
        # 비SQL 처리기 활성일 때만 `views` 슬롯 파생 모델(plans/125 A-5) — 비활성은 종전 모델
        # 그대로.
        plan_model = views_plan_model(plan_model, extra_agents, "apm_query" in extra_agents)
    try:
        model = await try_structured_call(
            llm, messages,
            plan_model,
            backend=getattr(app_config, "structured_output_backend", "none"),
            max_retries=getattr(app_config, "structured_output_max_retries", 1),
        )
        if model is not None:
            return _plan_from_model(model, user_query)
    except StructuredOutputError as e:
        # F4 해소 — 침묵 폴백 금지. 사유를 구조화해 반환에 싣는다.
        logger.warning("intent_planner 구조화 분해 실패(%d회 시도): %s", e.attempts, e)
        degraded = dict(fallback)
        degraded["degraded"] = [{
            "stage": "intent_planner._llm_decompose",
            "reason": "structured_output_validation_failed",
            "attempts": e.attempts,
            "detail": str(e.last_error)[:500],
        }]
        return degraded

    try:
        response = await llm.ainvoke(messages)
        parsed = extract_json_from_response(response.content)
    except Exception as e:
        logger.error("intent_planner LLM 분해 실패, 단일 data_query 폴백: %s", e)
        return _mark_fallback(fallback, DECOMPOSE_FALLBACK_LLM_ERROR, type(e).__name__)

    if not parsed or not isinstance(parsed.get("tasks"), list) or not parsed["tasks"]:
        logger.warning("intent_planner 분해 결과 없음/무효, 단일 data_query 폴백")
        return _mark_fallback(fallback, DECOMPOSE_FALLBACK_MALFORMED)

    tasks: list[dict] = []
    for i, raw in enumerate(parsed["tasks"], 1):
        if not isinstance(raw, dict):
            continue
        agent = raw.get("agent", "data_query")
        task: dict = {
            "task_id": raw.get("task_id", f"t{i}"),
            "agent": agent,
            "sub_query": raw.get("sub_query", user_query),
            "depends_on": raw.get("depends_on") or [],
            "input_from": raw.get("input_from") or [],
            "order": raw.get("order", i),
            "status": "pending",
        }
        if ownership_on:
            # 고정 키로 새 dict를 만드는 경로라 명시적으로 보존한다
            # (정제는 `_sanitize_task_capabilities`).
            task["capability"] = raw.get("capability", "")
        if _task_frame_on(app_config):
            # 원문 조각 보존 — `sub_query`는 `_apply_task_frames`가 조각으로 다시 만든다
            # (plans/111 C-3).
            task["spans"] = raw.get("spans") or []
        if extra_agents:
            # 보기 슬롯 보존(plans/125 A-5) — 정제는 `_sanitize_task_views`.
            task["views"] = raw.get("views") or []
            if "apm_query" in extra_agents:
                # 보기 선택 조건(plans/134 M-3) — 형태 정제는 `_sanitize_task_views`,
                # 값 검증은 처리기.
                task["view_args"] = raw.get("view_args") or {}
                # 대상 텍스트(plans/130 M-1) — 형태 정제는 `_sanitize_task_views`, 해석은 처리기.
                task["targets"] = raw.get("targets") or []
        # 답변 영역·요청 소스 칸 보존(plans/132 N-5) — 정제는 `_sanitize_task_areas`.
        task["areas"] = raw.get("areas")
        task["requested_source"] = raw.get("requested_source")
        # 닫힌 어휘(plans/121 TP-1.7) — 목록 밖 담당은 디스패치 폴백과 같은 담당으로(task 단위)
        close_agent_vocabulary(task, frozenset(extra_agents))
        if task.get(AGENT_FALLBACK_KEY):
            logger.warning(
                "intent_planner: 목록 밖 담당 %r → %s(현행 디스패치 의미)", agent, task["agent"],
            )
        tasks.append(task)

    if not tasks:
        logger.warning("intent_planner 유효 task 없음, 단일 data_query 폴백")
        return _mark_fallback(fallback, DECOMPOSE_FALLBACK_MALFORMED)

    return {
        "tasks": tasks,
        "clarification_needed": parsed.get("clarification_needed"),
    }
