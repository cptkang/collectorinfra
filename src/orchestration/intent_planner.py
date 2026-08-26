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
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.llm import create_llm
from src.nodes.input_parser import LOCATION_HINT_TERMS
from src.prompts.intent_planner import INTENT_PLANNER_SYSTEM_TEMPLATE
from src.state import AgentState
from src.utils.json_extract import extract_json_from_response

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
    FORM_MEMORY_ALL_KEYWORDS as _FORM_MEMORY_ALL_KEYWORDS,
    FORM_MEMORY_DELETE_KEYWORDS as _FORM_MEMORY_DELETE_KEYWORDS,
    FORM_MEMORY_NOUN_KEYWORDS as _FORM_MEMORY_NOUN_KEYWORDS,
    FORM_MEMORY_VIEW_KEYWORDS as _FORM_MEMORY_VIEW_KEYWORDS,
    is_form_memory_command,
    FORM_MEMORY_SHORTCUT_HINT as _FORM_MEMORY_SHORTCUT_HINT,
    is_form_memory_shortcut as _is_form_memory_shortcut,
    memory_query_normalized as _memory_query_normalized,
    refers_to_demonstrative_server,
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


async def intent_planner(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """사용자 질의를 sub-task 목록으로 분해한다.

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
        return _single_task_plan("cache_management", user_query)

    # ② 명시적 유사어 등록 요청 (멀티턴 두 번째 요청)
    parsed = state.get("parsed_requirements", {})
    if parsed.get("synonym_registration") and state.get("pending_synonym_registrations"):
        logger.info("intent_planner: 유사어 등록 요청 감지, synonym_registration 단일 task")
        return _single_task_plan("synonym_registration", user_query)

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
        if not _sig:
            text = (
                "기억된 답을 조회·삭제하려면 대상 양식이 필요합니다. 양식 파일을 "
                "첨부해 다시 요청해 주세요. (같은 세션에서 방금 다룬 양식이 있으면 "
                "파일 없이도 처리됩니다.)"
            )
            if _is_form_memory_shortcut(user_query):
                # '?'만 친 사용자에게는 문맥이 없다 — 단축키 의미를 먼저 밝힌다(D-165)
                text = "'?'는 양식에 저장된 값을 조회하는 단축키입니다. " + text
        elif any(k in _mq for k in _FORM_MEMORY_DELETE_KEYWORDS):
            text = await _form_memory_delete_response(state, user_query, app_config, _sig)
        else:
            text = await _form_memory_view_response(state, app_config, _sig)
        logger.info("intent_planner: 폼필 확인 이력 조회/삭제 단락(D-151)")
        plan = _single_task_plan("general_inference", user_query)
        plan["task_plan"][0]["direct_response"] = text
        if _sig:
            plan["last_form_signature"] = _sig  # 직전 양식 컨텍스트 갱신(멀티턴 보존)
        return plan

    # ②.5 존 역질문에서 사용자가 체크박스로 확정한 DB (Plan 75 §4) — LLM 분해를 건너뛰어
    # 자연어 재조합 없이 결정적 고정(mapped_db_ids 선례 동형). task.db_ids는 하류
    # run_data_query_pipeline이 classify_dbs를 우회하는 기존 배관을 그대로 탄다.
    selected_db_ids = state.get("selected_db_ids")
    if selected_db_ids:
        logger.info(
            "intent_planner: selected_db_ids 감지, data_query 단일 task (존 선택 고정=%s)",
            selected_db_ids,
        )
        return _with_form_signature(
            _single_task_plan("data_query", user_query, db_ids=list(selected_db_ids)),
            state,
        )

    # ③ field_mapper가 이미 대상 DB를 결정한 경우 (양식 업로드 시)
    mapped_db_ids = state.get("mapped_db_ids")
    if mapped_db_ids:
        logger.info("intent_planner: mapped_db_ids 감지, data_query 단일 task (DB 고정=%s)", mapped_db_ids)
        return _with_form_signature(
            _single_task_plan("data_query", user_query, db_ids=mapped_db_ids), state,
        )

    # ③.5 양식 업로드(template_structure) → 폼필 단일 task 고정 (Plan 73 D-150).
    # 양식 채우기는 의미상 단일 파이프라인 작업 — LLM 복합 분해가 서버정보/월지표를
    # 별도 task로 쪼개면 결과 병합이 2배 행이 된다(라이브 실측 2026-07-30 B0).
    # mapped_db_ids 미성립 턴(③ 미발동)도 결정적으로 단일화한다.
    if state.get("template_structure"):
        logger.info("intent_planner: template_structure 감지, data_query 단일 task (폼필 고정, D-150)")
        return _with_form_signature(_single_task_plan("data_query", user_query), state)

    # ③.6 파일 없는 폼필 요청 → 안내 응답으로 단락 (Plan 73 D-150).
    # template_structure 없이 "양식 채워줘"류가 LLM 분해로 가면 data_query가 존재하지
    # 않는 양식을 환각 처리한다(라이브 실측 2026-07-30 7차). 고정 안내문은 LLM을
    # 통과시키지 않고 direct_response로 결정적 반환한다(LLM 실패 시 일반 오류 강등 방지).
    if (any(k in user_query for k in _FORM_NOUN_KEYWORDS)
            and any(k in user_query for k in _FORM_FILL_VERB_KEYWORDS)):
        logger.info("intent_planner: 파일 없는 폼필 요청 감지 — 안내 응답 단락(D-150)")
        plan = _single_task_plan("general_inference", user_query)
        plan["task_plan"][0]["direct_response"] = _FORM_FILL_NO_FILE_GUIDANCE
        return plan

    # [계층 B] LLM 복합 분해 — 후속 턴이면 압축 맥락(M3 보존 신호)을 주입한다(M1).
    conversation_context = state.get("conversation_context")
    decomposed = await _llm_decompose(
        llm, user_query, app_config, conversation_context=conversation_context
    )
    # 결정적 가드: 시간성 신호 없는 프로세스 조회는 실시간 API로 교정(D-041/D-046, 폴백 포함)
    tasks = _coerce_alarm_intent(_coerce_process_intent(decomposed["tasks"]))
    result: dict = {
        "task_plan": tasks,
        "is_composite": len(tasks) > 1,
        "current_node": "intent_planner",
    }
    # 모호성 방출 시 보존 (Phase 1은 인터럽트 없이 tasks로 진행 — §4.11)
    clarification = decomposed.get("clarification_needed")
    if clarification:
        result["clarification_needed"] = clarification
    return result


async def _form_memory_view_response(
    state: AgentState, app_config: AppConfig, signature: str | None = None
) -> str:
    """첨부 양식의 확인 이력을 조회 전용(TTL 미연장)으로 표시한다(D-151 Phase 3)."""
    from src.schema_cache.form_memory import load_form_memory_answers

    _sig, answers, meta = await load_form_memory_answers(
        state.get("template_structure"), app_config, touch=False, signature=signature,
    )
    if not answers or not meta:
        return (
            "이 양식에 기억된 답이 없습니다. 양식을 채운 뒤 미해결 항목 패널에서 "
            "'이 답을 기억'을 선택하면 저장됩니다(일정 기간 후 자동 만료). "
            + _FORM_MEMORY_SHORTCUT_HINT
        )
    _act = {"blank": "공란 유지", "column": "DB 항목", "eav": "DB 항목", "literal": "직접 입력"}
    lines = []
    for field, ans in answers.items():
        label = field.replace("|", " > ")
        act = _act.get(ans.get("action"), str(ans.get("action")))
        val = ans.get("value")
        suffix = f"({val})" if val not in (None, "") else ""
        lines.append(f"- {label}: {act}{suffix}")
    return (
        f"'{meta.get('display_name', '이 양식')}' 양식에 기억된 답이 {len(answers)}건 "
        f"있습니다 (저장 {str(meta.get('created_at', ''))[:10]}, "
        f"{meta.get('use_count', 0)}회 사용).\n\n"
        + "\n".join(lines)
        + "\n\n특정 항목을 삭제하려면 \"<필드명> 기억 삭제\", 전체를 삭제하려면 "
        "\"기억 전부 삭제\"라고 요청해 주세요. 이 답들은 같은 양식을 채울 때 "
        "자동으로 반영됩니다. " + _FORM_MEMORY_SHORTCUT_HINT
    )


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
        term in (user_query or "") for term in LOCATION_HINT_TERMS
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

    try:
        messages: list[BaseMessage] = [
            SystemMessage(content=INTENT_PLANNER_SYSTEM_TEMPLATE)
        ]
        if isinstance(llm, KBGenAIChat):
            messages.append(AIMessage(content=""))
        messages.append(HumanMessage(content=human_content))

        response = await llm.ainvoke(messages)
        parsed = extract_json_from_response(response.content)
    except Exception as e:
        logger.error("intent_planner LLM 분해 실패, 단일 data_query 폴백: %s", e)
        return fallback

    if not parsed or not isinstance(parsed.get("tasks"), list) or not parsed["tasks"]:
        logger.warning("intent_planner 분해 결과 없음/무효, 단일 data_query 폴백")
        return fallback

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
        tasks.append(task)

    if not tasks:
        logger.warning("intent_planner 유효 task 없음, 단일 data_query 폴백")
        return fallback

    return {
        "tasks": tasks,
        "clarification_needed": parsed.get("clarification_needed"),
    }
