"""결과 기반 재계획 노드 (Plan 49, deepagents TodoListMiddleware 동적 재계획 대응).

1차 실행 결과(task_results)를 LLM으로 평가하여 (a) **종료** 또는 (b) **후속 task 추가**를
결정한다. 후속이 필요하면 신규 task만 task_plan에 증분 추가하고 agent_orchestrator로 되돌린다.

설계 원칙:
- **증분 추가**(R-A1): 신규 task만 append하고 기존 completed task·task_results는 절대 건드리지 않는다.
- **상한 가드**(R-A3): replan_count >= max_replan이면 재계획을 중단하고 현재 결과로 종료한다.
- **보수적 종료**(R-A1/R-A4): LLM 호출·파싱 실패 또는 후속 불필요·빈 task면 needs_replan=False로 종료한다.

본 노드는 tool-calling을 사용하지 않으며, 프롬프트 + JSON 파싱으로만 동작한다.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.llm import create_llm
from src.orchestration.db_access import is_access_denied_result
from src.orchestration.intent_planner import (
    REASON_DECOMPOSE_FALLBACK,
    _coerce_alarm_intent,
    _coerce_process_intent,
)
from src.orchestration.schemas import close_agent_vocabulary, validate_plan_dag
from src.prompts.replanner import (
    REPLANNER_BUDGET_BLOCK_TEMPLATE,
    REPLANNER_BUDGET_TIME_LINE_TEMPLATE,
    REPLANNER_SYSTEM_TEMPLATE,
)
from src.routing.registry import get_registry
from src.state import AgentState
from src.utils.deadline import retrieval_remaining
from src.utils.json_extract import extract_json_from_response
from src.utils.query_gen_common import term_in_text
from src.utils.prior_dependency import (
    NOTE_DECOMPOSE,
    NOTE_DESCRIPTIONS_MISSING,
    NOTE_OWNERSHIP,
    NOTE_ROUTING_FALLBACK,
    NOTE_SOURCE_UNAVAILABLE,
    NOTE_TRACE,
    has_sequential_marker,
)

logger = logging.getLogger(__name__)

# 결과 요약에 포함할 task별 샘플 행 상한 (토큰 폭증 방지)
_MAX_SUMMARY_ROWS = 5

# 텍스트 계열(general/cache/synonym) 결과 요약 상한.
# 너무 짧으면(예: 300자) 사용법+소스+데이터를 모두 담은 긴 안내 답변이 앞부분만 보여
# replanner가 "뒷부분 누락"으로 오판→불필요 재계획을 유발하므로 충분히 크게 둔다.
_MAX_SUMMARY_TEXT_CHARS = 1500

#: 문서 조건부 처리기 이름(plans/127 — `doc_query.DOC_QUERY_AGENT`와 같은 값 · import 순환을 피해
#: 문자열로 둔다). 문서 전용 계획 결정적 종료·문서 재검색 중복 방지가 읽는다.
_DOC_AGENT = "doc_query"
#: 비SQL 조건부 처리기(plans/125·127 — 문자열로 둔다 · import 순환 방지). 이 처리기의 실패(연결 안
#: 됨 · 0건 · 도구 오류 · 창 밖)는 **소스 불가 종결**이다 — 다른 소스로 바꾸면 침묵 대체가 된다(125
#: §4.6).
_NONSQL_AGENTS = frozenset({"apm_query", _DOC_AGENT})
#: 명시 소스 안내로 끝난 결과의 사유(plans/132 — `subagents.REASON_SOURCE_*`와 같은 값).
_SOURCE_NOTICE_REASONS = frozenset({"source_inactive", "source_unsupported"})
#: 안내만 하는 task 표지(`conditional_agents.SOURCE_NOTICE_KEY`와 같은 값).
_SOURCE_NOTICE_KEY = "source_notice"


async def replanner(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """1차 실행 결과를 평가하여 후속 task 추가 여부를 결정한다.

    Args:
        state: 현재 에이전트 상태 (task_plan, task_results, replan_count 포함)
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - 종료 시: {"needs_replan": False, "current_node": "replanner"}
        - 추가 시: {"task_plan": 기존+신규, "needs_replan": True,
                    "replan_count": +1, "current_node": "replanner"}
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    replan_count = state.get("replan_count", 0)

    # 처리 현황 표시용 누적 이력 (루프 종료 시에도 보존되도록 항상 그대로 carry forward)
    replan_history = list(state.get("replan_history", []))

    # 결정적 direct_response(고정 안내·확인 이력 조회/삭제 등)는 정의상 **최종 응답** —
    # LLM 재평가에 넘기면 "내용이 제공되지 않았다"로 오판해 데이터 조회 후속(B0 등)을
    # 만들어 이력 조회가 채우기로 회귀한다(라이브 실측 2026-08-03, FIX-22).
    # 전 task가 direct_response면 재계획 없이 종료(LLM 호출 자체를 생략).
    _tasks_now = state.get("task_plan", [])
    if _tasks_now and all(t.get("direct_response") for t in _tasks_now):
        logger.info("replanner: 전 task가 결정적 direct_response — 재계획 스킵(D-150/D-151)")
        return {"needs_replan": False, "replan_history": replan_history, "current_node": "replanner"}

    # 존 역질문(D-143 후속2)은 정의상 이번 턴의 최종 응답(사용자 존 선택 대기) — LLM
    # 재평가에 넘기면 빈 결과로 오판해 데이터 조회 후속을 만들어 역질문을 덮어쓴다
    # (FIX-22 direct_response와 동형 위험). 결정적으로 종료한다.
    # 판정은 **이번 계획의 task 결과**로만 한다(plans/121 TP-1.1 · K-5) — 앞 턴 결과가 남아 있으면
    # 그 역질문·거부로 이번 턴 재계획을 건너뛰었다(존 역질문 대기 스킵 7구간 실측).
    _plan_ids = {str(t.get("task_id")) for t in _tasks_now if isinstance(t, dict)}
    _results_now = {
        k: v for k, v in (state.get("task_results", {}) or {}).items() if str(k) in _plan_ids
    }
    if any(
        isinstance(r, dict) and r.get("zone_clarification")
        for r in _results_now.values()
    ):
        logger.info("replanner: 존 역질문 대기 — 재계획 스킵(D-143 후속2)")
        zone_out: dict[str, Any] = {
            "needs_replan": False, "replan_history": replan_history, "current_node": "replanner",
        }
        # 역질문 본문에는 분해 폴백 노트를 붙이지 않는다(plans/121 §12.4 — 집계기가 경과 노트를
        # 역질문 말미에도 붙인다). 뺄 것이 없으면 키를 싣지 않는다(종전 반환과 같다).
        kept_notes = _without_zone_turn_notes(state.get("dependency_notes"))
        if kept_notes is not None:
            zone_out["dependency_notes"] = kept_notes
        return zone_out

    # 조회 권한 거부(D-232)는 결정적이다 — 다시 계획해도 같은 거부가 나온다. 전 task가 거부면
    # LLM 재평가 없이 종료해 3단 `access_denied` 종결과 같은 응답을 남긴다(plans/116 §10.3 결함 ②).
    # 일부만 거부된 복합 계획은 부분 실패처럼 종전대로 LLM이 평가한다(D-251 ⑤).
    if _results_now and all(is_access_denied_result(r) for r in _results_now.values()):
        logger.info("replanner: 전 task 조회 권한 없음(D-232) — 재계획 스킵")
        return {
            "needs_replan": False, "replan_history": replan_history, "current_node": "replanner",
        }

    # 소스 불가 종결(plans/132 N-3 · G-4 · D-293 — 플래그 없음): 비SQL 처리기 실패·명시 소스
    # 안내는 다시 계획해도 같거나, 다른 소스로 바꾸면 침묵 대체가 된다(125 §4.6 — 실측: 제니퍼 0건
    # 뒤 재계획이 폴스타 대체 task를 4/4 추가). 나머지 task가 없거나 전부 성공이면 평가 LLM 없이
    # 끝낸다. 권한 거부는 대상이 아니다(D-251 ⑤ — 일부 거부는 종전대로 평가).
    _terminal = _terminal_source_task_ids(_tasks_now, _results_now)
    if _terminal:
        _rest = [t for t in _tasks_now if str(t.get("task_id")) not in _terminal]
        if not _rest or _all_tasks_succeeded({**state, "task_plan": _rest}):
            logger.info("replanner: 소스 불가 종결 %s — 재계획 스킵(plans/132 G-4)",
                        sorted(_terminal))
            return {
                "needs_replan": False, "replan_history": replan_history,
                "current_node": "replanner",
            }

    # 결정적 성공 종료(D-251 ⑤ · CU-4 · plans/119 N-2 — 플래그 없음): 전 task 성공 · 조회 행 ≥1 ·
    # 미완 표지 없음이면 평가 LLM이 더할 판단이 없다. 0건·부분 실패·순차 의존(D-203) 경로는 아래
    # LLM 평가를 그대로 탄다(같은 컨텍스트 — 비트 동일). 판정 기준은 `_all_tasks_succeeded`.
    if _all_tasks_succeeded(state):
        logger.info("replanner: 전 task 성공·행 반환·미완 표지 없음 — LLM 평가 없이 종료(D-251 ⑤)")
        return {
            "needs_replan": False, "replan_history": replan_history, "current_node": "replanner",
        }

    # 문서 전용 계획(plans/127 G-12 (a) — 플래그 없음 · 문서 task 가 있는 계획에서만 발동):
    # 문서 답은 완결이다. 재계획이 붙일 수 있는 후속은 같은 문서 재검색(0건·색인 폐기는
    # 다시 물어도 같다 — 126 §4.7)이거나 일반 안내 보충뿐이라 평가 LLM 을 생략한다.
    if _tasks_now and all(
        isinstance(t, dict) and t.get("agent") == _DOC_AGENT for t in _tasks_now
    ) and all(isinstance(_results_now.get(str(t.get("task_id"))), dict) for t in _tasks_now):
        logger.info("replanner: 문서 전용 계획 완료 — LLM 평가 없이 종료(plans/127 G-12)")
        return {
            "needs_replan": False, "replan_history": replan_history, "current_node": "replanner",
        }

    # 상한 가드(R-A3): 재계획 반복 상한 초과 시 현재 결과로 종료. 위 결정적 종료(direct_response ·
    # 존 역질문 · 권한 거부 · 성공)는 상한과 같은 반환이라 뒤로 옮겨도 결과가 같다 — 여기 닿으면
    # 상한이 평가를 실제로 끊은 것이므로 사유를 경과 노트로 남긴다(plans/121 TP-1.6 · 3단
    # `tier3_plan.replan`과 같은 종류·사유·문구 · 첫 바퀴 전 상한(0회)은 노트 없음).
    if replan_count >= app_config.max_replan:
        logger.info("replanner: 재계획 상한(%d) 도달, 현재 결과로 종료", app_config.max_replan)
        cap_out: dict[str, Any] = {
            "needs_replan": False, "replan_history": replan_history, "current_node": "replanner",
        }
        if replan_count:
            cap_out["dependency_notes"] = list(state.get("dependency_notes") or []) + [
                _replan_cap_note(app_config.max_replan)
            ]
        return cap_out

    # 시간 예산(plans/118 P-1 · G-2 — 플래그 없음): 남은 시간이 직전 한 바퀴 소요보다 짧으면
    # 후속을 붙여도 그 턴은 상한에 걸려 **이미 얻은 결과까지** 버린다. LLM 평가 전에 끊는다.
    # 남은 시간은 **조회 마감**(처리 마감 − 서술 예약) 기준이다(plans/119 T-2 · D-267 ⑥).
    reserve_sec = _answer_reserve_sec(app_config)
    deadline_notice = _deadline_stop_notice(state, reserve_sec=reserve_sec)
    if deadline_notice:
        return {
            "needs_replan": False, "replan_history": replan_history,
            "current_node": "replanner",
            # 오케스트레이터가 조회 마감을 넘겨 후속을 시작하지 않았으면(T-2) 그 사유가 더
            # 구체적이다 — 이 노드 진입 시점의 사유는 그 한 곳에서만 온다(요청 스코프 · 라우트가
            # 매 턴 비운다).
            "replan_stop_notice": state.get("replan_stop_notice") or deadline_notice,
        }

    decision = await _llm_evaluate(
        llm,
        state.get("user_query", ""),
        state.get("task_plan", []),
        state.get("task_results", {}),
        app_config,
        # 예산 인지 블록(plans/119 T-6) — 플래그 off면 None이라 입력이 종전과 바이트 동일하다.
        budget_block=(
            _budget_block(state, app_config, replan_count, reserve_sec)
            if _budget_prompt_on(app_config) else None
        ),
    )

    # 평가 LLM 호출·응답 실패 — 보수적 종료는 종전과 같고, 사유를 경과 노트로 남긴다
    # (plans/121 TP-1.6 · N-7 — 종전에는 로그만 남았다). 원인은 예외 클래스명만.
    if _EVAL_FAILURE_KEY in decision:
        return {
            "needs_replan": False, "replan_history": replan_history, "current_node": "replanner",
            "dependency_notes": list(state.get("dependency_notes") or []) + [
                _eval_failure_note(str(decision.get(_EVAL_FAILURE_KEY) or ""))
            ],
        }

    # 보수적 종료(R-A1/R-A4): 후속 불필요·빈 task·파싱 실패 시 루프 종료
    if not decision.get("needs_followup") or not decision.get("new_tasks"):
        logger.debug("replanner: 후속 불필요, 종료 (reason=%s)", decision.get("reason"))
        return {"needs_replan": False, "replan_history": replan_history, "current_node": "replanner"}

    new_tasks = _assign_ids(decision["new_tasks"], existing=state.get("task_plan", []))
    # 소스 불가 종결 task를 대신하거나 그 결과에 기대는 후속은 뺀다(plans/132 G-4 — 침묵 대체 차단).
    if _terminal:
        new_tasks = _drop_terminal_substitutes(new_tasks, _terminal)
        if not new_tasks:
            logger.info("replanner: 소스 불가 종결 task의 대체 후속 전부 제거 → 종료"
                        "(plans/132 G-4)")
            return {
                "needs_replan": False, "replan_history": replan_history,
                "current_node": "replanner",
            }
    # 신규 task도 분해와 같은 계획 검증을 지난다(plans/121 TP-1.3 · N-5) — 담당 교정 + DAG만,
    # 무익 재시도 필터들보다 **앞**(교정된 담당으로 비교한다). 위반 task만 빼고 사유를 남긴다.
    new_tasks, plan_notes = _validate_replanned_tasks(
        new_tasks, state.get("task_plan", []), state, app_config,
    )
    replan_notes = list(state.get("dependency_notes") or []) + plan_notes
    if not new_tasks:
        # 유효 신규 task가 없으면 보수적 종료
        out: dict[str, Any] = {
            "needs_replan": False, "replan_history": replan_history, "current_node": "replanner",
        }
        if plan_notes:
            out["dependency_notes"] = replan_notes
        return out

    # 무의미 재시도 차단(D-063): 엔티티를 이미 찾은(>0행) 조회를 같은 방식으로 다시 묻는
    # 후속을 제거한다. 행은 찾았으나 일부 요청 필드가 null인 것은 "데이터에 그 속성이
    # 없음"이지 쿼리 오류가 아니므로, 재조회해도 채워지지 않고 max_replan까지 헛돈다.
    new_tasks = _filter_futile_retries(
        new_tasks, state.get("task_plan", []), state.get("task_results", {})
    )
    if not new_tasks:
        logger.info(
            "replanner: 엔티티를 이미 확보한 무의미 재시도 전부 제거 → 종료 (reason=%s)",
            decision.get("reason"),
        )
        return {"needs_replan": False, "replan_history": replan_history, "current_node": "replanner"}

    # 재생성 위임 제거(plans/119 Q-3 · D-063 확장): 내부 루프가 재생성을 멈춘(`regen_stop` — 검증
    # 예산 소진·산문·조회 마감) 조회를 같은 담당·같은 대상 DB·같은 식별 리터럴로 다시 시키는 후속은
    # 같은 프롬프트·같은 스키마로 같은 결과를 낸다. 제거로 끝나면 마지막 사유를 응답에 싣는다.
    new_tasks, regen_notice = _filter_regen_stopped(
        new_tasks, state.get("task_plan", []), state.get("task_results", {}),
    )
    if not new_tasks:
        logger.info("replanner: 재생성을 멈춘 조회의 재위임 후속 전부 제거 → 종료")
        return {
            "needs_replan": False, "replan_history": replan_history,
            "current_node": "replanner", "replan_stop_notice": regen_notice,
        }

    # 반복 0건 중단(plans/118 P-2 · D-063 개정 G-3): 직전 두 바퀴가 모두 대상 DB 전부 0건이고
    # 새 후속이 같은 엔티티를 또 찾으면 제거한다. 첫 0건 뒤 한 번의 재조회는 그대로 둔다.
    new_tasks, empty_notice = _filter_repeated_empty(
        new_tasks, state.get("task_plan", []), state.get("task_results", {}), replan_history,
    )
    if not new_tasks:
        logger.info("replanner: 전 DB 연속 0건 대상의 재조회 전부 제거 → 종료")
        return {
            "needs_replan": False, "replan_history": replan_history,
            "current_node": "replanner", "replan_stop_notice": empty_notice,
        }

    # 관리 작업 강등 차단: 순수 관리 요청(캐시/유사어)의 후속으로 DB 조회를 붙이면
    # 관리 실패가 무관한 조회 결과로 종결된다(침묵 강등 금지, D-059 계열).
    new_tasks = _filter_management_demotion(new_tasks, state.get("task_plan", []))
    if not new_tasks:
        logger.info(
            "replanner: 관리 작업 계획의 조회 강등 후속 전부 제거 → 종료 (reason=%s)",
            decision.get("reason"),
        )
        return {"needs_replan": False, "replan_history": replan_history, "current_node": "replanner"}

    # 중복 재답변 방지(R-A4 강화): 후속이 모두 general_inference(일반 안내)면
    # 데이터 기반 후속이 아니라 같은 주제를 다시 답변하는 패턴이다. general_inference는
    # 자체 완결적 안내 답변(사용법·지원 소스·조회 가능 데이터 등)이므로, 여기에 또
    # general_inference를 붙이면 동일 내용이 중복 출력된다. 추가하지 않고 종료한다.
    if all(t.get("agent") == "general_inference" for t in new_tasks):
        logger.info(
            "replanner: 후속이 모두 general_inference → 중복 재답변 방지로 종료 (reason=%s)",
            decision.get("reason"),
        )
        return {"needs_replan": False, "replan_history": replan_history, "current_node": "replanner"}
    # 같은 규칙을 문서 답에도(plans/127 §4.8): 이미 문서를 찾은 계획에 문서 재검색만 붙이면
    # 같은 답이 되풀이된다(플래그 없음 · 문서 task 가 있을 때만 발동).
    if all(t.get("agent") == _DOC_AGENT for t in new_tasks) and any(
        isinstance(t, dict) and t.get("agent") == _DOC_AGENT
        for t in state.get("task_plan", []) or []
    ):
        logger.info(
            "replanner: 후속이 모두 문서 재검색 → 중복 재답변 방지로 종료 (reason=%s)",
            decision.get("reason"),
        )
        return {
            "needs_replan": False, "replan_history": replan_history, "current_node": "replanner",
        }

    logger.info(
        "replanner: 후속 task %d개 추가 (replan_count=%d, reason=%s)",
        len(new_tasks), replan_count + 1, decision.get("reason"),
    )

    # 처리 현황용 이력 누적 (이번 재계획 회차의 사유·추가 개수)
    replan_history.append({
        "count": replan_count + 1,
        "reason": decision.get("reason") or "",
        "added": len(new_tasks),
    })

    # 증분 추가(R-A1): 기존 task_plan 보존 + 신규만 append (전체 교체 금지)
    added: dict[str, Any] = {
        "task_plan": state.get("task_plan", []) + new_tasks,
        "needs_replan": True,
        "replan_count": replan_count + 1,
        "replan_history": replan_history,
        "current_node": "replanner",
    }
    if plan_notes:
        added["dependency_notes"] = replan_notes
    return added


def _validate_replanned_tasks(
    new_tasks: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    state: AgentState,
    app_config: AppConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """재계획 신규 task에 분해 출구와 같은 결정적 검증을 건다(plans/121 TP-1.3 · 3단 `replan` 공용).

    ① 닫힌 어휘(TP-1.7 — 목록 밖 담당은 현행 디스패치 의미의 폴백 담당) ② 담당 교정(알람·프로세스 —
    분해 출구 `_normalize_plan_exit`와 같은 함수 · 양식 턴 제외) ③ DAG(분해와 같은 계약·같은 플래그
    `composite.plan_dag_validation_enabled`). DAG 위반은 **그 task만** 뺀다 — 되먹임 재요청은
    하지 않는다(LLM +0). 빠진 task는 경과 노트(`decompose` · 미완 종류 — 후속을 못 붙였다는 뜻이라
    안내성이 아니다)로 남긴다.

    Returns:
        (검증을 통과한 신규 task, 경과 노트)
    """
    # 활성인 조건부 처리기(plans/125 A-3 · plans/127 — `apm_query`·`doc_query`)는 어휘 안이다 —
    # 비활성이면 빈 집합(종전 그대로).
    from src.orchestration.conditional_agents import active_conditional_agents  # 지연 — 순환 방지

    extra = frozenset(active_conditional_agents(app_config))
    tasks = [close_agent_vocabulary(t, extra) for t in new_tasks]
    if not (state.get("template_structure") or state.get("uploaded_file")):
        tasks = _coerce_alarm_intent(_coerce_process_intent(tasks))
    cfg = getattr(app_config, "composite", None)
    if not bool(getattr(cfg, "plan_dag_validation_enabled", False)):
        return tasks, []
    if validate_plan_dag(list(existing))[1]:
        return tasks, []  # 기존 계획이 이미 위반이면 신규 task에 책임을 돌릴 수 없다
    fixed, violations, _fixes = validate_plan_dag(list(existing) + tasks)
    if not violations:
        return fixed[len(existing):], []
    kept: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for task in tasks:
        checked, task_violations, _ = validate_plan_dag(list(existing) + kept + [task])
        if task_violations:
            logger.warning("replanner: 신규 task %s 계획 위반 — 제외: %s",
                           task.get("task_id"), task_violations)
            notes.append({
                "kind": NOTE_DECOMPOSE, "task_id": task.get("task_id"),
                "reason": "replan_task_invalid",
                "detail": (
                    "재계획 후속 작업이 계획 규칙을 어겨 실행하지 않았습니다"
                    f"({'; '.join(task_violations)})."
                ),
            })
            continue
        kept.append(checked[-1])
    return kept, notes


async def _llm_evaluate(
    llm: BaseChatModel,
    user_query: str,
    task_plan: list[dict],
    task_results: dict[str, dict],
    app_config: AppConfig,
    *,
    budget_block: str | None = None,
) -> dict:
    """LLM으로 결과를 평가하여 후속 task 필요 여부를 판단한다.

    REPLANNER_SYSTEM_TEMPLATE로 LLM을 호출하고 JSON을 파싱한다.
    호출·파싱 실패 시 `{_EVAL_FAILURE_KEY: 예외 클래스명 또는 ""}`를 반환하여 호출부가 보수적으로
    종료하고 사유를 남기도록 한다(plans/121 TP-1.6).

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 원래 질의
        task_plan: 현재까지의 TaskSpec 목록 (status 포함)
        task_results: {task_id: 정규화된 결과}
        app_config: 앱 설정
        budget_block: (plans/119 T-6) 평가 컨텍스트 말미에 붙일 예산 블록. None이면 붙이지 않는다
            — 종전 입력과 바이트 동일.

    Returns:
        {"needs_followup": bool, "reason": str, "new_tasks": [...]} 또는 실패 표지 dict
    """
    try:
        context = _build_eval_context(user_query, task_plan, task_results)
        if budget_block:
            context = f"{context}\n\n{budget_block}"
        messages: list[BaseMessage] = [
            SystemMessage(content=REPLANNER_SYSTEM_TEMPLATE)
        ]
        if isinstance(llm, KBGenAIChat):
            messages.append(AIMessage(content=""))
        messages.append(HumanMessage(content=context))

        response = await llm.ainvoke(messages)
        parsed = extract_json_from_response(response.content)
    except Exception as e:
        logger.error("replanner LLM 평가 실패, 보수적 종료: %s", e)
        return {_EVAL_FAILURE_KEY: type(e).__name__}

    if not isinstance(parsed, dict):
        logger.warning("replanner 평가 결과 무효, 보수적 종료")
        return {_EVAL_FAILURE_KEY: ""}
    return parsed


def _build_eval_context(
    user_query: str,
    task_plan: list[dict],
    task_results: dict[str, dict],
) -> str:
    """LLM 평가용 컨텍스트(원질의 + 완료 task 요약 + 결과 요약)를 구성한다.

    Args:
        user_query: 사용자 원래 질의
        task_plan: 현재까지의 TaskSpec 목록
        task_results: {task_id: 정규화된 결과}

    Returns:
        Human 메시지로 주입할 평가 컨텍스트 문자열
    """
    lines: list[str] = [f"## 사용자 원래 질의\n{user_query}", "", "## 완료된 작업과 결과 요약"]

    ordered = sorted(task_plan, key=lambda t: t.get("order", 0))
    for task in ordered:
        tid = task.get("task_id", "?")
        agent = task.get("agent", "?")
        sub_query = task.get("sub_query", "")
        status = task.get("status", "?")
        res = task_results.get(tid, {})
        lines.append(f"- [{tid}] agent={agent}, status={status}, 지시=\"{sub_query}\"")
        lines.append(f"    결과: {_summarize_result(res)}")

    return "\n".join(lines)


def _summarize_result(res: dict) -> str:
    """단일 task 결과를 평가용 요약 문자열로 압축한다.

    행수와 샘플 행 일부만 노출하여 토큰 폭증을 방지한다(R-A5).

    Args:
        res: 정규화된 task 결과

    Returns:
        요약 문자열 (에러/행수/샘플 포함)
    """
    if res.get("error"):
        return f"실패 (error={res['error']})"

    rows = _extract_rows(res)
    if rows is None:
        # 텍스트 계열(cache/synonym/general) 결과
        text = res.get("final_response")
        if text:
            return text[:_MAX_SUMMARY_TEXT_CHARS]
        return "결과 없음"

    n = len(rows)
    if n == 0:
        return "0건 (조회 결과 없음)"

    sample = rows[:_MAX_SUMMARY_ROWS]
    return f"{n}건 조회. 샘플(최대 {_MAX_SUMMARY_ROWS}건): {sample}"


def _extract_rows(res: dict) -> list[dict] | None:
    """task 결과에서 행 목록을 추출한다 (data_query/alarm_query 계열).

    Args:
        res: 정규화된 task 결과

    Returns:
        행 목록 또는 None(텍스트 계열로 판단되는 경우)
    """
    rows = res.get("query_results")
    if rows is not None:
        return rows
    organized = res.get("organized_data")
    if isinstance(organized, dict):
        return organized.get("rows", [])
    return None


def _assign_ids(new_tasks: list[dict], *, existing: list[dict]) -> list[dict]:
    """신규 task에 기존 task_id와 충돌하지 않는 새 id/order/status를 부여한다.

    기존 task_id 집합과 충돌하지 않도록 기존 최대 t번호+1부터 순차 부여하고,
    order는 기존 최대 order+1부터 부여한다. depends_on/input_from 누락은 []로 보정한다.
    신규 task끼리의 상대 참조(예: 신규 두 번째가 첫 번째를 참조)는 임시 task_id를
    실제 부여된 id로 재매핑하여 허용한다.

    Args:
        new_tasks: LLM이 생성한 신규 task 목록 (task_id/order 없음 가정)
        existing: 기존 task_plan (충돌 방지·order 기준점)

    Returns:
        task_id/order/status가 부여된 신규 task 목록
    """
    existing_ids = {t.get("task_id") for t in existing if t.get("task_id")}

    # 기존 t번호 최대값 → 신규 id 시작점
    max_num = 0
    for tid in existing_ids:
        if isinstance(tid, str) and tid.startswith("t") and tid[1:].isdigit():
            max_num = max(max_num, int(tid[1:]))

    # 기존 order 최대값 → 신규 order 시작점
    max_order = max((t.get("order", 0) for t in existing), default=0)

    # 신규 task의 임시 참조(예: LLM이 부여한 task_id)를 실제 id로 매핑
    id_map: dict[str, str] = {}
    assigned: list[dict] = []
    for i, raw in enumerate(new_tasks):
        if not isinstance(raw, dict):
            continue
        new_id = f"t{max_num + 1 + len(assigned)}"
        old_ref = raw.get("task_id")
        if old_ref:
            id_map[old_ref] = new_id

        task: dict[str, Any] = {
            "task_id": new_id,
            "agent": raw.get("agent", "data_query"),
            "sub_query": raw.get("sub_query", ""),
            "depends_on": list(raw.get("depends_on") or []),
            "input_from": list(raw.get("input_from") or []),
            # 대체(재조회) 관계: 이 후속이 대체하는 선행 task_id 목록 (없으면 빈 배열).
            # result_aggregator가 최종 답변 본문에서 대체된 선행 task 서술을 숨긴다(D-043).
            "supersedes": list(raw.get("supersedes") or []),
            "order": max_order + 1 + len(assigned),
            "status": "pending",
        }
        assigned.append(task)

    # 신규 task끼리의 상대 참조(임시 id)를 실제 id로 재매핑
    if id_map:
        for task in assigned:
            task["depends_on"] = [id_map.get(d, d) for d in task["depends_on"]]
            task["input_from"] = [id_map.get(d, d) for d in task["input_from"]]
            task["supersedes"] = [id_map.get(d, d) for d in task["supersedes"]]

    return assigned


# 관리 계열 agent — 실패 사유가 그대로 사용자에게 종결 노출되어야 하는 작업들
_MANAGEMENT_AGENTS = ("cache_management", "synonym_registration")
# DB/API 조회 계열 agent — 관리 작업의 대체 수단이 될 수 없는 작업들
_QUERY_AGENTS = ("data_query", "alarm_query", "process_query")


def _filter_management_demotion(
    new_tasks: list[dict],
    existing: list[dict],
) -> list[dict]:
    """순수 관리 요청 계획에 조회 후속을 붙이는 강등을 제거한다.

    캐시/유사어 관리 task가 실패하면 replanner LLM이 "다른 방법으로 정보를 구하자"며
    data_query 후속을 만들어, 관리 실패가 무관한 DB 조회 결과(테이블명 나열 등)로
    종결된다(2026-09-01 라이브 실측 — 캐시 갱신 실패가 재계획 3회 끝에 b0 테이블명
    출력으로 끝남). 관리 작업의 실패 사유는 조회로 덮지 않고 그대로 종결 노출한다
    (침묵 강등 금지 — D-059 계열).

    혼합 요청("캐시 갱신하고 서버 목록도 조회해줘")은 기존 계획에 조회 계열 task가
    이미 있으므로 대상이 아니다 — 순수 관리 계획일 때만 발동한다.

    Args:
        new_tasks: _assign_ids로 id가 부여된 신규 task 목록
        existing: 기존 task_plan

    Returns:
        조회 강등 후속을 제외한 신규 task 목록
    """
    if not existing or not all(
        t.get("agent") in _MANAGEMENT_AGENTS for t in existing
    ):
        return new_tasks

    kept: list[dict] = []
    for t in new_tasks:
        if t.get("agent") in _QUERY_AGENTS:
            logger.info(
                "replanner: 관리 작업 계획의 조회 강등 차단 (agent=%s, sub_query=%.60s)",
                t.get("agent"), t.get("sub_query", ""),
            )
            continue
        kept.append(t)
    return kept


def _filter_futile_retries(
    new_tasks: list[dict],
    existing: list[dict],
    task_results: dict[str, dict],
) -> list[dict]:
    """엔티티를 이미 찾은(>0행) 조회를 같은 방식으로 다시 묻는 무의미 재시도를 제거한다 (D-063).

    `replanner`의 LLM은 "행은 찾았으나 일부 요청 필드가 null"인 결과를 "0건/불충분 → 재조회"
    예시에 잘못 적용해, 데이터에 애초에 없는 속성을 채우려고 max_replan까지 헛 재시도한다.
    엔티티가 이미 확보되었으면(선행이 >0행 반환) 같은 의도 재조회로는 누락 필드가 채워질 수
    없으므로 결정적으로 차단한다. supersedes 필드 누락(LLM 비준수)에도 견고하도록 **행수 +
    독립성**으로 판정한다.

    제거 규칙 — 신규 task가 다음 중 하나면 제거:
    - (a) 그 `supersedes`가 **>0행을 반환한** 선행 task_id를 가리킨다(명시적 재조회).
    - (b) **독립**(`depends_on` 비어있음)이면서 **같은 agent**의 선행이 이미 >0행을 반환했다
      (LLM이 supersedes를 빠뜨린 암묵적 재조회).

    보존 — 정당한 후속은 그대로 둔다:
    - 선행이 0건이면(엔티티 못 찾음) "0건 → 재조회"는 (a)/(b) 모두 미해당 → 허용.
    - 데이터 의존 보강(`depends_on` 존재, 예: 장애 서버 → 알람 이력)은 (b) 미해당 → 허용.
    - 다른 agent의 보강(data_query 행 발견 → alarm_query)은 (b) 미해당 → 허용.

    Args:
        new_tasks: _assign_ids로 id/supersedes가 부여된 신규 task 목록
        existing: 기존 task_plan(완료 task 포함 — 행수 판정 기준)
        task_results: {task_id: 정규화된 결과}

    Returns:
        무의미 재시도를 제외한 신규 task 목록
    """
    # >0행을 반환한 완료 선행의 task_id 집합과 agent 집합
    rows_ids: set[str] = set()
    rows_agents: set[str] = set()
    for t in existing:
        if _extract_rows(task_results.get(t.get("task_id"), {})):
            rows_ids.add(t.get("task_id"))
            if t.get("agent"):
                rows_agents.add(t["agent"])

    if not rows_ids:
        # 아직 어떤 선행도 데이터를 못 찾음 → 재조회 가치가 있으므로 전부 보존
        return new_tasks

    kept: list[dict] = []
    for t in new_tasks:
        supersedes = t.get("supersedes") or []
        # (a) 데이터를 이미 찾은 선행을 명시적으로 재조회
        if any(sid in rows_ids for sid in supersedes):
            logger.info(
                "replanner: 무의미 재시도 제거(명시적 supersedes %s, 선행 이미 데이터 확보)",
                supersedes,
            )
            continue
        # (b) 독립 재조회인데 같은 agent가 이미 데이터를 찾음
        if not t.get("depends_on") and t.get("agent") in rows_agents:
            logger.info(
                "replanner: 무의미 재시도 제거(독립 %s 재조회, 같은 agent 이미 데이터 확보)",
                t.get("agent"),
            )
            continue
        kept.append(t)

    return kept


# ── plans/118 P-1 · P-2 ────────────────────────────────────────────────


def _deadline_stop_notice(
    state: AgentState, *, now: float | None = None, reserve_sec: float = 0.0,
) -> str | None:
    """조회 마감까지 남은 시간이 직전 한 바퀴 소요보다 짧으면 사유 문구, 아니면 None.

    plans/118 P-1 판정식(`남은 시간 < 직전 한 바퀴` · 배수 1.0 — 추정 상수 금지)을 유지하되,
    "남은 시간"은 **조회 마감**(= `request_deadline` − 서술 예약 `reserve_sec`)까지다
    (plans/119 T-2 · D-267 ⑥). 서술 몫을 남겨 두지 않으면 조회가 시간을 다 쓰고 서술 단계에서
    끊긴다(§2.9 12턴).
    조회 마감이 이미 지났으면(남은 시간 ≤ 0) 한 바퀴 소요와 무관하게 멈춘다.

    마감(`request_deadline`)이나 한 바퀴 소요(`orchestrator_round_sec`)가 없으면(CLI·옛
    체크포인트·3단 계획 루프) **판정하지 않는다** — 종전 동작이다. `reserve_sec=0`이면 plans/118
    P-1과 같은 판정·같은 문구다.
    """
    round_sec = state.get("orchestrator_round_sec")
    remaining = retrieval_remaining(state, reserve_sec, now=now)
    if (
        remaining is None
        or not isinstance(round_sec, (int, float))
        or isinstance(round_sec, bool)
    ):
        return None
    if remaining > 0 and remaining >= round_sec:
        return None
    reserve = max(0.0, float(reserve_sec or 0))
    logger.info(
        "replanner: 시간 예산 부족 — 조회 마감까지 %.1f초(서술 예약 %.0f초 제외) < 직전 한 바퀴 "
        "%.1f초, 재계획 없이 종료",
        remaining, reserve, round_sec,
    )
    left = f"답변 작성 몫 {reserve:.0f}초를 뺀 남은 시간" if reserve > 0 else "남은 시간"
    return (
        f"응답 시간 상한이 가까워 추가 조회가 필요한지 더 판단하지 않고 지금까지의 결과로 "
        f"답했습니다({left} {max(0.0, remaining):.0f}초 < 직전 조회 한 바퀴 "
        f"{round_sec:.0f}초)."
    )


def _answer_reserve_sec(app_config: object) -> float:
    """서술 예약 초(`ServerConfig.answer_reserve_sec`) — 테스트 대역 설정에서는 0(종전 동작)."""
    value = getattr(getattr(app_config, "server", None), "answer_reserve_sec", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, float(value))


def _budget_prompt_on(app_config: object) -> bool:
    """`REPLAN_BUDGET_PROMPT_ENABLED`(plans/119 T-6) — 명시 True일 때만 켠다(대역 설정은 off)."""
    return getattr(app_config, "replan_budget_prompt_enabled", False) is True


def _budget_block(
    state: AgentState, app_config: object, replan_count: int, reserve_sec: float,
) -> str:
    """재계획 평가 입력 말미에 붙일 예산 블록(plans/119 T-6 · 문헌 L-9).

    조회 마감까지 남은 시간(마감이 없는 요청은 줄 생략) · 남은 재계획 횟수(`max_replan −
    replan_count`) · "지금 결과로 종결 가능"을 싣는다. LLM 행동에 기대는 장치라 결정적 가드
    (P-1·T-2·Q-3) 뒤에 두고, 플래그 기본 off로 arm 측정한다(D-162 · D-267 ⑥).
    """
    left = retrieval_remaining(state, reserve_sec)
    time_line = "" if left is None else REPLANNER_BUDGET_TIME_LINE_TEMPLATE.format(
        remaining_sec=int(max(0.0, left)), reserve_sec=int(reserve_sec),
    )
    max_replan = getattr(app_config, "max_replan", 0)
    if isinstance(max_replan, bool) or not isinstance(max_replan, int):
        max_replan = 0
    return REPLANNER_BUDGET_BLOCK_TEMPLATE.format(
        time_line=time_line,
        remaining_replans=max(0, max_replan - int(replan_count or 0)),
        max_replan=max_replan,
    )


# 식별 리터럴 — 숫자를 포함한 호스트명형 토큰(영숫자·하이픈·밑줄 4자 이상)과 IPv4.
# 지표어("cpu"·"메모리")는 숫자가 없어 빠진다(`realtime_usage._host_tokens` 와 같은 규칙).
_ENTITY_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-_]{3,}")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# 전 DB 0건 판정 대상 — 행을 돌려주는 조회 담당만 본다.
_ROW_AGENTS = ("data_query", "alarm_query")


def _entity_literals(text: str) -> set[str]:
    """지시문에서 식별 리터럴(호스트명·IP)을 뽑는다 — LLM 판단에 의존하지 않는다."""
    found = {t.lower() for t in _ENTITY_TOKEN_RE.findall(text or "")
             if any(ch.isdigit() for ch in t)}
    found |= set(_IPV4_RE.findall(text or ""))
    return found


def _same_entity(a: set[str], b: set[str]) -> bool:
    """같거나 한쪽이 다른 쪽의 부분 문자열이면 같은 엔티티다(`sbhdbo53` ↔ `sbhdbo53-01`)."""
    return any(x in y or y in x for x in a for y in b)


def _rounds(task_plan: list[dict[str, Any]], replan_history: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """task_plan 을 바퀴(최초 계획 + 재계획 회차별 추가분)로 나눈다.

    재계획은 신규 task 만 뒤에 붙이고(R-A1) 회차마다 추가 개수를 `replan_history.added` 에
    남기므로, 뒤에서부터 그 개수만큼 떼면 회차가 복원된다. 개수가 맞지 않으면 한 바퀴로 본다.
    """
    ordered = sorted(task_plan, key=lambda t: t.get("order", 0))
    added = [int(h.get("added") or 0) for h in replan_history if isinstance(h, dict)]
    if sum(added) > len(ordered):
        return [ordered]
    head = len(ordered) - sum(added)
    rounds = [ordered[:head]]
    idx = head
    for n in added:
        rounds.append(ordered[idx:idx + n])
        idx += n
    return [r for r in rounds if r]


def _round_all_empty(tasks: list[dict[str, Any]], task_results: dict[str, dict[str, Any]]) -> bool:
    """이 바퀴의 조회 task 가 **전부** 오류 없이 대상 DB 전부 0건인가.

    단일 DB·멀티 DB 결과 모양이 같다 — 멀티 DB 는 DB별 행을 합친 `query_results` 가 비면
    전 DB 0건이다(`_pack_pipeline_result`). 미조회 DB(`skipped_dbs`)가 있으면 전부가 아니다.
    """
    row_tasks = [t for t in tasks if t.get("agent") in _ROW_AGENTS]
    if not row_tasks:
        return False
    for t in row_tasks:
        res = task_results.get(str(t.get("task_id")), {}) or {}
        if res.get("error") or res.get("skipped_dbs"):
            return False
        rows = _extract_rows(res)
        if rows is None or rows:
            return False
    return True


def _queried_dbs(tasks: list[dict[str, Any]], task_results: dict[str, dict[str, Any]]) -> list[str]:
    dbs: set[str] = set()
    for t in tasks:
        for target in (task_results.get(str(t.get("task_id")), {}) or {}).get("source") or []:
            if isinstance(target, dict) and target.get("db_id"):
                dbs.add(str(target["db_id"]))
    return sorted(dbs)


def _filter_repeated_empty(
    new_tasks: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    task_results: dict[str, dict[str, Any]],
    replan_history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """전 DB 연속 0건 대상을 또 찾는 후속을 제거한다(plans/118 P-2 · D-063 개정 G-3).

    발동 조건 — 셋 다:
    - 직전 **두 바퀴**가 모두 「조회 task 전부 오류 없이 대상 DB 전부 0건」이다
    - 새 후속의 식별 리터럴(호스트명·IP)이 그 두 바퀴 **각각의** 지시문 리터럴과 같거나 부분
      문자열이다(같은 엔티티를 두 번 못 찾았다)
    - 리터럴이 없는 후속은 판정하지 않는다(보존)

    첫 0건 뒤 한 번의 재조회(오타·대소문자 완화)는 두 바퀴 조건에 걸리지 않아 그대로 간다
    (D-063 의 "0건 → 재조회 허용"을 1회로 좁힌다 — 전면 금지가 아니다).

    Returns:
        (남길 후속, 전부 제거됐을 때 응답에 실을 결정적 사유 또는 None)
    """
    rounds = _rounds(existing, replan_history)
    if len(rounds) < 2:
        return new_tasks, None
    last_two = rounds[-2:]
    if not all(_round_all_empty(r, task_results) for r in last_two):
        return new_tasks, None
    round_literals = [
        set().union(*(_entity_literals(str(t.get("sub_query") or "")) for t in r)) for r in last_two
    ]
    kept: list[dict[str, Any]] = []
    removed: set[str] = set()
    for t in new_tasks:
        lits = _entity_literals(str(t.get("sub_query") or ""))
        if lits and all(_same_entity(lits, prev) for prev in round_literals):
            logger.info(
                "replanner: 전 DB 연속 0건 대상 재조회 제거(plans/118 P-2): %.80s",
                t.get("sub_query", ""),
            )
            removed |= {x for x in round_literals[-1] if _same_entity({x}, lits)} or lits
            continue
        kept.append(t)
    if kept or not removed:
        return kept, None
    dbs = _queried_dbs([t for r in last_two for t in r], task_results)
    names = ", ".join(f"`{x}`" for x in sorted(removed))
    return kept, (
        f"조건에 맞는 {names} 을(를) 찾지 못했습니다"
        f"(조회한 DB: {', '.join(dbs) if dbs else '기록 없음'}). 같은 대상을 두 번 연속 "
        "모든 대상 DB에서 조회했지만 0건이어서 더 재조회하지 않았습니다."
    )


# ── plans/119 N-2 (D-251 ⑤ · CU-4) · Q-3 (D-063 확장) ─────────────────────────


# 미완 표지로 보지 않는 경과 노트 종류 — 결과가 완결돼도 붙는 안내다(정상 주입 경과 · 답변 영역
# 소유 교정 · 컬럼 설명 미등록 · 요청 소스 불가(plans/121 TP-1.11a — 재계획으로 비활성 소스를 살릴
# 수 없다) · 분류 폴백(plans/121 TP-1.6 — 평가 LLM은 경과 노트를 보지 않아(`_build_eval_context`)
# 노트가 없는 성공 턴과 입력이 같다. 미완으로 두면 소유 플래그와 분리된 이 노트가 성공 턴마다 평가
# LLM을 부른다 — §12.6 ④)). 그 밖의 종류(게이트·절단·사후 대조·충족도·DB별 미조회·분해 강등·구조
# 미등록 등, 그리고 앞으로 생길 종류)는 전부 미완으로 본다 — 모르면 LLM 평가(종전). 새 종류는
# 여기서 안내성/미완을 선언한다(plans/121 §12.4).
# `decompose` 종류(미완 유지)에 TP-1.6이 더한 사유 셋은 평가 LLM 호출 수를 늘리지 않는다 —
# `decompose_fallback`은 순차 표지 질의에만 붙고(그 턴은 원래 결정적 성공 종료 대상이 아니다),
# `replan_cap`·`replan_eval_failed`는 이 노드가 루프를 끝내는 반환에만 실린다.
_INFO_NOTE_KINDS = frozenset(
    {
        NOTE_TRACE, NOTE_OWNERSHIP, NOTE_DESCRIPTIONS_MISSING, NOTE_SOURCE_UNAVAILABLE,
        NOTE_ROUTING_FALLBACK,
    }
)
# 재계획 종료 사유(plans/121 TP-1.6 · `decompose` 종류) — 3단 `tier3_plan.replan`의 상한 노트와
# 같은 사유 코드·문구다. D-241 판정 어휘(`invalid`·`timeout`·`clarify_blocked`)와 겹치지 않는다.
REASON_REPLAN_CAP = "replan_cap"
REASON_REPLAN_EVAL_FAILED = "replan_eval_failed"
# `_llm_evaluate` 실패 표지 키(내부) — 값은 예외 클래스명(응답 형식 무효면 "").
_EVAL_FAILURE_KEY = "_eval_failure"


def _replan_cap_note(max_replan: int) -> dict[str, Any]:
    """재계획 상한 도달 경과 노트 — 3단 `tier3_plan.replan`과 같은 문구."""
    return {
        "kind": NOTE_DECOMPOSE, "task_id": None, "reason": REASON_REPLAN_CAP,
        "detail": f"재계획 상한({max_replan}회)에 도달해 지금까지의 결과로 답했습니다.",
    }


def _eval_failure_note(error_class: str) -> dict[str, Any]:
    """재계획 평가 실패 경과 노트 — 원인은 예외 클래스명만(원문은 로그에만)."""
    head = (
        f"추가 조회가 필요한지 판단하는 단계가 실패해({error_class})" if error_class
        else "추가 조회가 필요한지 판단하는 단계의 응답 형식이 맞지 않아"
    )
    return {
        "kind": NOTE_DECOMPOSE, "task_id": None, "reason": REASON_REPLAN_EVAL_FAILED,
        "detail": f"{head} 지금까지의 결과로 답했습니다.",
    }


def _without_zone_turn_notes(notes: object) -> list[dict[str, Any]] | None:
    """존 역질문 턴에서 뺄 경과 노트(분해 폴백)를 걸러 낸 목록 — 뺄 것이 없으면 None.

    존 선택을 기다리는 턴은 역질문이 응답의 전부다. 분해 폴백 노트는 답변 턴의 조회와 무관한
    분해 단계 사유라 역질문 본문에 붙이지 않는다(plans/121 §12.4). 관측은 계획 요약의
    `decompose_fallback` 코드로 남는다.
    """
    if not isinstance(notes, list):
        return None
    kept = [
        n for n in notes
        if not (isinstance(n, dict) and n.get("reason") == REASON_DECOMPOSE_FALLBACK)
    ]
    if len(kept) == len(notes):
        return None
    return kept or None
# task 결과 dict 의 실패·부분 실패·미완 표지 — 값이 있으면 성공으로 보지 않는다.
#   error(실패·게이트 미실행) · skipped(D-203 게이트) · regen_stop(재생성 중단 — Q-3 계약) ·
#   db_errors(멀티 DB 일부 실패) · skipped_dbs(DB별 스코프 미조회) · zone_clarification(역질문 대기)
# plans/121이 더한 결과 키의 의미 선언(§12.4): `empty_diagnosis`(TP-11.5 — 0건일 때만 실린다.
# 0건은 행 판정이 이미 미완으로 보므로 따로 두지 않는다) · `organized_data`·`query_results`를 더한
# 호스트 조사 결과(TP-1.5 — 행 판정과 같은 규칙).
_INCOMPLETE_RESULT_KEYS = (
    "error", "skipped", "regen_stop", "db_errors", "skipped_dbs", "zone_clarification",
)


def _has_incomplete_notes(notes: object) -> bool:
    """경과 노트 중 미완 표지(안내성 종류 밖)가 하나라도 있는가."""
    if not isinstance(notes, list):
        return False
    return any(
        isinstance(n, dict) and n.get("kind") not in _INFO_NOTE_KINDS for n in notes
    )


def _terminal_source_task_ids(
    tasks: list[dict[str, Any]], results: dict[str, Any],
) -> set[str]:
    """이번 계획에서 **소스 불가로 종결**된 task id(plans/132 N-3 · G-4).

    - 비SQL 처리기(`apm_query`·`doc_query`)가 실패(`error`)로 끝남 — 연결 안 됨·0건·도구 오류·창 밖
    - 비SQL 처리기가 조회를 **작업으로 접수**함(`accepted_jobs` · plans/134 W0-B) — 다시 계획하면
      같은 조회를 또 접수한다. 결과는 작업 카드가 받는다
    - 명시 소스 안내로 끝남(계획 출구 안내 task · SQL 처리기 안내 단락 — G-1 「안내만」)

    조회 권한 거부는 넣지 않는다 — 일부만 거부된 복합 계획은 종전대로 평가 LLM이 본다(D-251 ⑤ ·
    plans/119 N-2). 전 task 거부는 위 단락이 이미 끝낸다.
    """
    out: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("task_id"))
        res = results.get(tid)
        if task.get(_SOURCE_NOTICE_KEY):
            out.add(tid)
        elif not isinstance(res, dict) or is_access_denied_result(res):
            continue
        elif task.get("agent") in _NONSQL_AGENTS and (res.get("error") or res.get("accepted_jobs")):
            out.add(tid)
        elif res.get("degraded_reason") in _SOURCE_NOTICE_REASONS:
            out.add(tid)
    return out


def _drop_terminal_substitutes(
    new_tasks: list[dict[str, Any]], terminal: set[str],
) -> list[dict[str, Any]]:
    """소스 불가 종결 task를 대체(`supersedes`)하거나 그 결과를 입력·선행으로 받는 후속을 뺀다."""
    kept: list[dict[str, Any]] = []
    for task in new_tasks:
        refs = {str(x) for key in ("supersedes", "depends_on", "input_from")
                for x in (task.get(key) or [])}
        if refs & terminal:
            logger.info("replanner: 소스 불가 종결 %s 대체·의존 후속 제거: %s",
                        sorted(refs & terminal), str(task.get("sub_query") or "")[:60])
            continue
        kept.append(task)
    return kept


def _all_tasks_succeeded(state: AgentState) -> bool:
    """재계획 평가 LLM 없이 끝내도 되는 성공 상태인가(D-251 ⑤ · CU-4 · plans/119 N-2).

    **전부** 참이어야 성공이다(하나라도 아니면 종전대로 LLM이 평가한다):

    1. 계획이 비어 있지 않다.
    2. 순차 의존(D-203) 경로가 아니다 — 어떤 task에도 `input_from`·`depends_on`이 없고, 원질의에
       D-203 순차 표지(`has_sequential_marker`)가 없다(단일 계획이 둘째 단계를 빠뜨렸을 수 있다).
    3. 상태 수준 미완 표지가 없다 — `orchestration_incomplete_notice` · `sufficiency_shortfalls`
       (78 W5 충족도 미달) · `dependency_notes`의 미완 종류(`_INFO_NOTE_KINDS` 밖).
    4. 모든 task가 `status == "completed"`이고 결과 dict가 있으며, 조회 권한 거부가 아니고,
       `_INCOMPLETE_RESULT_KEYS` 표지·미완 경과 노트가 없고, `organized_data.is_sufficient`가
       False가 아니다.
    5. 행 모양 결과(`_extract_rows`가 None이 아님)는 **모두 1행 이상**이다 — 0건은 LLM 평가 유지.
       조회 담당(`_QUERY_AGENTS`)인데 행 모양이 없으면 성공으로 보지 않는다.
    6. 행을 돌려준 task가 **하나 이상** 있다 — 텍스트 결과만 있는 계획(일반 안내 등)은 종전대로
       평가한다(폴백 담당이 받은 데이터 질의를 재계획이 되살리는 경로를 막지 않는다).
    """
    tasks = state.get("task_plan") or []
    results = state.get("task_results") or {}
    if not tasks or not isinstance(results, dict):
        return False
    if has_sequential_marker(str(state.get("user_query") or "")):
        return False
    if str(state.get("orchestration_incomplete_notice") or "").strip():
        return False
    if state.get("sufficiency_shortfalls") or _has_incomplete_notes(state.get("dependency_notes")):
        return False
    returned_rows = False
    for task in tasks:
        if task.get("input_from") or task.get("depends_on"):
            return False
        if task.get("status") != "completed":
            return False
        res = results.get(str(task.get("task_id")))
        if not isinstance(res, dict) or is_access_denied_result(res):
            return False
        if any(res.get(k) for k in _INCOMPLETE_RESULT_KEYS):
            return False
        if _has_incomplete_notes(res.get("dependency_notes")):
            return False
        organized = res.get("organized_data")
        if isinstance(organized, dict) and organized.get("is_sufficient") is False:
            return False
        rows = _extract_rows(res)
        if rows is None:
            if task.get("agent") in _QUERY_AGENTS:
                return False
            continue
        if not rows:
            return False
        returned_rows = True
    return returned_rows


# 재생성 중단 사유(`regen_stop.reason` — SQL 루프 담당과의 계약) → 사용자 문구.
_REGEN_STOP_REASON_TEXT = {
    "validation_budget": "SQL을 재시도 한도까지 다시 만들었지만 검증을 통과하지 못했습니다",
    "non_sql": "SQL이 아니라 설명문이 생성됐습니다",  # 「대신」은 하네스 교정 표지어(plans/123 W-6)
    "deadline": "응답 시간 상한이 가까워 SQL 재생성을 멈췄습니다",
}
_REGEN_STOP_FALLBACK_TEXT = "SQL 재생성이 멈췄습니다"
# 응답 말미 사유에 싣는 길이 상한 — 산문(non_sql)은 길 수 있다.
_MAX_NOTICE_QUERY_CHARS = 80
_MAX_NOTICE_DETAIL_CHARS = 200


def _db_signal_vocab() -> frozenset[str] | None:
    """DB를 지목하는 표면어(위치·제품·DB 신호어) — `config/db_registry.yaml` 파생.

    레지스트리를 읽지 못하면 None — 호출부는 "같은 대상 DB"를 판정하지 못한 것으로 보고 후속을
    **보존**한다(제거 쪽으로 틀리지 않는다).
    """
    try:
        reg = get_registry()
        terms = (*reg.location_terms(), *reg.product_terms(), *reg.db_signal_terms())
    except Exception:  # noqa: BLE001 — 판정 보류(보존)로 떨어진다
        logger.warning(
            "replanner: DB 신호어를 읽지 못해 재생성 위임 판정을 보류한다", exc_info=True,
        )
        return None
    return frozenset(str(t).lower() for t in terms if str(t).strip())


def _db_terms(text: str, vocab: frozenset[str]) -> frozenset[str]:
    low = (text or "").lower()
    return frozenset(t for t in vocab if term_in_text(t, low))


def _same_regen_family(
    new: dict[str, Any], prior: dict[str, Any], prior_res: dict[str, Any], vocab: frozenset[str],
) -> bool:
    """새 후속이 재생성을 멈춘 선행과 **같은 조회**인가 — 같은 담당 · 같은 대상 DB · 같은 계열.

    - 같은 담당(`agent`).
    - 다른 선행 결과를 입력으로 쓰는 보강(`input_from` 있음)은 같은 조회가 아니다 — 단, 이 선행을
      명시적으로 대체(`supersedes`)하면 같은 조회다.
    - 같은 대상 DB: 후속은 아직 실행 전이라 대상이 없다. 같은 턴의 DB 선택 입력 중 task마다 다른
      것은 지시문의 DB 지목 표면어뿐이므로 그 집합이 같아야 한다. 후속이 DB를 고정(`db_ids`)했으면
      선행이 실제로 조회한 DB(`target_db_ids`) 안이어야 한다.
    - 같은 계열: 식별 리터럴(호스트명·IP — `_entity_literals`) 집합이 같거나, 둘 다 있고 같은
      엔티티다(`_same_entity`).
    """
    if new.get("agent") != prior.get("agent"):
        return False
    prior_id = str(prior.get("task_id"))
    if new.get("input_from") and prior_id not in (new.get("supersedes") or []):
        return False
    pinned = new.get("db_ids")
    if pinned and not set(pinned) <= set(prior_res.get("target_db_ids") or []):
        return False
    new_q = str(new.get("sub_query") or "")
    prior_q = str(prior.get("sub_query") or "")
    if _db_terms(new_q, vocab) != _db_terms(prior_q, vocab):
        return False
    a, b = _entity_literals(new_q), _entity_literals(prior_q)
    return a == b or (bool(a) and bool(b) and _same_entity(a, b))


def _regen_stop_notice(stopped: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    """재위임을 막은 조회마다 마지막 사유를 사용자 문구 한 줄로 만든다(침묵 강등 금지)."""
    lines: list[str] = []
    for prior, stop in stopped:
        reason = _REGEN_STOP_REASON_TEXT.get(
            str(stop.get("reason") or ""), _REGEN_STOP_FALLBACK_TEXT,
        )
        query = " ".join(str(prior.get("sub_query") or "").split())[:_MAX_NOTICE_QUERY_CHARS]
        detail = " ".join(str(stop.get("detail") or "").split())[:_MAX_NOTICE_DETAIL_CHARS]
        line = (
            f"「{query}」 조회는 {reason}. "
            "같은 조회를 다시 맡기지 않고 지금까지의 결과로 답했습니다"
        )
        lines.append(f"{line}(마지막 사유: {detail})." if detail else f"{line}.")
    return "\n".join(lines)


def _filter_regen_stopped(
    new_tasks: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    task_results: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """재생성을 멈춘 조회를 같은 방식으로 다시 시키는 후속을 제거한다(plans/119 Q-3 · D-063 확장).

    발동 조건: 선행 task 결과에 `regen_stop`(`{"reason": "validation_budget"|"non_sql"|"deadline",
    "detail": str}` — SQL 루프 담당과의 계약)이 있고, 새 후속이 그 선행과 같은 조회다
    (`_same_regen_family`). 내부 루프가 이미 예산만큼 재생성한 질의를 새 task로 다시 시키면 같은
    프롬프트·같은 스키마로 같은 결과가 나온다(run `20260923-103638` — 한 턴 SQL 생성 최대 12회).

    보존: 다른 담당·다른 DB 지목·다른 식별 대상·다른 선행 결과를 쓰는 보강은 그대로 간다.
    `regen_stop` 이 없는 실패(실행 오류 등)는 대상이 아니다.

    Returns:
        (남길 후속, 전부 제거됐을 때 응답에 실을 사유 또는 None)
    """
    stopped = [
        (t, task_results.get(str(t.get("task_id"))) or {})
        for t in existing
        if isinstance((task_results.get(str(t.get("task_id"))) or {}).get("regen_stop"), dict)
    ]
    if not stopped or not new_tasks:
        return new_tasks, None
    vocab = _db_signal_vocab()
    if vocab is None:
        return new_tasks, None
    kept: list[dict[str, Any]] = []
    blocked: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for t in new_tasks:
        match = next(
            ((p, res) for p, res in stopped if _same_regen_family(t, p, res, vocab)), None,
        )
        if match is None:
            kept.append(t)
            continue
        prior, res = match
        logger.info(
            "replanner: 재생성 중단(%s) 조회의 재위임 후속 제거(plans/119 Q-3): %.80s",
            (res.get("regen_stop") or {}).get("reason"), t.get("sub_query", ""),
        )
        blocked.setdefault(str(prior.get("task_id")), (prior, res["regen_stop"]))
    if kept or not blocked:
        return kept, None
    return kept, _regen_stop_notice(list(blocked.values()))
