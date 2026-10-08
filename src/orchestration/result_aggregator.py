"""결과 통합 노드 (Plan 48).

task_results를 통합하여 단일 final_response(또는 output_file)를 생성한다.

처리:
- 결과 이질성 흡수(§4.9.4): data_query/alarm_query는 organized_data를 output_generator로 최종화,
  텍스트 계열(cache/synonym/general)은 result["final_response"]를 그대로 수집.
- 단일 task: 그대로 최종화(기존 동작과 동일).
- 복합 task: order 순으로 묶어 통합 final_response(부분 실패 안내 포함, D-005 패턴).
  output_file이 있는 task가 있으면 우선 반환.

본 노드는 결과 묶음 텍스트 조립은 deterministic하게 수행하며, tool-calling을 사용하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Literal, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.domain import disclosure as disc
from src.domain.result_refs import RESULT_TABLES_KEY
from src.llm import USER_RESPONSE_TAG, astream_text, create_llm
from src.nodes.output_generator import (
    NO_TEMPLATE_NOTICE_HEAD,
    narration_limit_sec,
    output_generator,
)
from src.orchestration.apm_query import (
    APM_QUERY_AGENT,
    SOURCE_CLARIFICATION_KEY,
    reference_views_only,
    table_label,
)
from src.orchestration.apm_query import META_KEY as APM_META_KEY
from src.orchestration.host_inspect import HOST_INSPECT_AGENT
from src.prompts.result_synthesizer import RESULT_SYNTHESIZER_SYSTEM_PROMPT
from src.state import AgentState
from src.utils.deadline import MIN_CALL_TIMEOUT_SEC
from src.utils.empty_antecedent import (
    EMPTY_ANTECEDENT_KEY,
    ZERO_ROW_TURN_KEY,
    carried_zero_row_turn,
    carried_zone_turn_context,
    empty_antecedent_note,
    widened_beyond_antecedent,
)
from src.utils.prior_dependency import REASON_PRIOR_EMPTY, render_dependency_notes
from src.utils.progress_events import emit_answer_prefix

logger = logging.getLogger(__name__)

#: 병합이 성립하지 않는 복합 턴의 답변 조립 방식(`synthesize=True`일 때만 쓴다 · plans/121 TP-4.5).
#: "synthesize" = LLM 1회 합성(D-062 — 1단 `deep_agent` · 3단 계획 루프 `finalize`) ·
#: "steps" = 단계(task)별 결과를 순서대로 잇는 결정적 조립(G-7 ① — 2단 기준 경로).
CompositeAnswer = Literal["synthesize", "steps"]


async def result_aggregator(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
    synthesize: bool = False,
    composite_answer: CompositeAnswer = "synthesize",
) -> dict:
    """task_results를 통합하여 최종 응답을 생성한다.

    Args:
        state: 현재 에이전트 상태 (task_plan, task_results 포함)
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)
        synthesize: 복합 결과를 deterministic 이어붙이기(_merge_finalized) 대신 LLM
            1회로 단일 답변 합성(_synthesize_finalized)할지 여부. 딥 에이전트 경로
            (D-062)에서만 True — 오케스트레이터가 동일 질문을 재시도(부분 성공→재조회)할
            때 collector에 1·2차 결과가 모두 쌓여 "없음→있음" 모순 이중 답변이 한
            말풍선에 섞이는 문제를 합성으로 해소한다. replanner 경로(D-005/D-043)는
            기존 deterministic 병합을 유지한다(False).
        composite_answer: `synthesize=True`에서 공통 서버 키 병합(TP-4.1)이 성립하지 않는 복합
            턴을 어떻게 끝낼지(`CompositeAnswer`). 기본 "synthesize"는 종전 LLM 합성이다(1단·3단).
            "steps"(2단 그래프 배선만 — plans/121 TP-4.5 · G-7 ①)는 합성 LLM 없이 단계별 결과를
            잇고(`_finalize_steps`), `supersedes`가 빠진 재조회의 실패·0건 선행도 숨긴다
            (`_collect_implicit_superseded` — D-062 원 결함 재발 방지).
            `synthesize=False`면 무시한다.

    Returns:
        업데이트할 State 필드:
        - final_response: 통합 자연어 응답
        - output_file / output_file_name: 문서 생성 task가 있으면 포함
        - current_node: "result_aggregator"
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    tasks = state["task_plan"]
    task_results = state.get("task_results", {})

    # 대체(재조회)된 선행 task는 최종 답변 본문에서 제외한다(D-043).
    # replanner가 "0건/누락 → 재조회" 후속을 만들면 supersedes에 선행 task_id가 담긴다.
    # 그 후속이 성공(에러 없음)했을 때만 선행을 숨겨, 동일 질문에 대한 상반된 이중 답변
    # (없음→있음)을 방지한다. 재조회 자체가 실패하면 선행 결과를 그대로 유지한다(안전).
    superseded = _collect_superseded(tasks, task_results)
    # 단계별 조립(TP-4.5)은 합성 LLM이 모순을 풀어 주지 않는다 — LLM이 supersedes를 빠뜨린
    # 재조회도 같은 자리에서 결정적으로 숨긴다(D-062 원 결함 재발 방지).
    step_answers = synthesize and composite_answer == "steps"
    if step_answers:
        superseded |= _collect_implicit_superseded(
            tasks, task_results, state.get("replan_history") or [],
        )

    # order 순으로 task 정렬 (표시 순서 안정화), 대체된 task는 본문에서 제외.
    ordered_tasks = [
        t for t in sorted(tasks, key=lambda t: t.get("order", 0))
        if t["task_id"] not in superseded
    ]
    # 방어: 모든 task가 제외되는 비정상 상황이면 제외를 무시하고 전체 사용.
    if not ordered_tasks:
        ordered_tasks = sorted(tasks, key=lambda t: t.get("order", 0))

    # 멀티턴 DB 승계용 db_id 승격(D-053): orchestration 경로는 agent_orchestrator가
    # active_db_id/target_databases를 top-level state에 쓰지 않아, 다음 턴 context_resolver의
    # previous_db_ids가 비어 "해당 서버 …" 후속 질의의 db_id를 잃는다(process_query는 위치어가
    # 없어 위치 힌트로도 못 잡음). 실행된 task들의 target_db_ids를 top-level로 승격한다.
    db_promotion = _collect_db_promotion(tasks, task_results)

    # 존 역질문(D-143 후속2 · G-3 확대)은 **이번 턴의 최종 응답**이다 — 마감 루프보다 먼저 끊는다.
    #
    # G-3 로 복합 계획에도 게이트가 열리면서 한 턴에 여러 task 가 각자 같은 페이로드를 낼 수
    # 있게 됐다. 여기서 하나만 취해 **턴당 1회**로 묶는다(중복 질문 차단).
    # 아래 복합 경로(`_merge_finalized`·`_synthesize_finalized`)는 `zone_clarification` 키를
    # 옮기지 않으므로, 이 단락이 없으면 질문이 **본문 텍스트로만 합쳐져** API 응답의
    # `clarification` 이 비고 사용자는 구조화 응답을 할 수 없다(침묵 강등 — CLAUDE.md).
    # `db_promotion` 은 의도적으로 붙이지 않는다: 임의 분류 결과를 `previous_db_ids` 로
    # 남기면 다음 턴 승계가 오염된다(subagents 반환부가 `target_db_ids` 를 비우는 것과 같은 사유).
    zone_q = _zone_clarification_from_tasks(ordered_tasks, task_results)
    if zone_q:
        zone_out: dict[str, Any] = {
            "final_response": zone_q["question"],
            "zone_clarification": zone_q,
            "current_node": "result_aggregator",
            "query_results": [],
        }
        # 존 재진입 계획 보존(plans/121 TP-1.2 · G-30 · D-272 ⑪) — 다음 턴(존 답변)이 이번 턴 복합
        # 계획을 되살리도록 스냅샷을 남긴다. 2단 계획 턴(계획 경로 코드 있음)만 — 1단·3단도 이
        # 함수를 거치지만 `intent_planner`를 타지 않아 복원할 곳이 없다(키 미탑재 · 바이트 불변).
        snapshot = _zone_reentry_snapshot(tasks, task_results) if state.get("plan_path") else None
        if snapshot:
            zone_out["zone_reentry_plan"] = snapshot
        return _with_answer_history(_apply_incomplete_notice(zone_out, state))

    # 합성 모드 + 복합 task일 때만 per-task 마감의 토큰 스트리밍을 억제한다.
    # (최종 합성 1회에만 USER_RESPONSE_TAG를 부여하여 중간 답변 토큰 누출 방지 — D-062/D-009)
    suppress_stream = synthesize and len(ordered_tasks) > 1

    # 복합 task + 합성 모드(2단·1단): 먼저 공통 서버 키로 결정적 병합을 시도한다(D-100).
    # 여러 하위 조회(알람 선별·지표·설정)가 같은 서버 식별 컬럼을 공유하면, 질의에 언급된
    # 모든 항목(알람명·심각도·CPU평균·제조사·일련번호 등)을 한 표로 합쳐 노출한다.
    # 병합 판정은 task 마감 **앞**이다(plans/121 TP-4.1) — 병합이 성립하면 task별 요약 LLM은
    # 결과가 버려지므로 부르지 않는다. 병합 원천 밖 task(행 없음)만 LLM 없이 마감해 덧붙인다
    # (TP-11.6).
    if suppress_stream:
        merged_rows = _merge_task_results_by_identity(ordered_tasks, task_results)
        if merged_rows:
            # 표시용으로 셀을 줄인 원천이 있으면 CSV 원천은 전문 행으로 다시 병합한다(plans/134 W2)
            csv_rows = (
                _merge_task_results_by_identity(ordered_tasks, task_results, full=True)
                if any(r.get(_DISPLAY_CUT_KEY) for r in task_results.values()
                       if isinstance(r, dict))
                else None
            )
            merged_out = await _finalize_merged_path(
                merged_rows, ordered_tasks, task_results, state, llm, app_config,
                csv_rows=csv_rows,
            )
            return _with_answer_history(_apply_incomplete_notice(
                {**merged_out, **db_promotion}, state,
            ))
        # 병합 불성립 + 2단(TP-4.5): 합성 LLM 없이 단계별 결과를 순서대로 잇는다.
        if step_answers:
            steps_out = await _finalize_steps(ordered_tasks, task_results, state, llm, app_config)
            return _with_answer_history(_apply_incomplete_notice(_with_result_tables(
                {**steps_out, **db_promotion}, ordered_tasks, task_results, state,
            ), state))

    # 각 task 결과를 최종화 (텍스트 응답 + 선택적 output_file)
    finalized: list[dict] = []
    for task in ordered_tasks:
        tid = task["task_id"]
        res = task_results.get(tid, {})
        finalized.append(
            await _finalize_task(
                task, res, state, llm, app_config,
                stream_user_response=not suppress_stream,
            )
        )

    # 공통 키가 없으면(도메인 이질) LLM 1회 합성으로 폴백한다(D-062).
    if synthesize and len(finalized) > 1:
        synthesized = await _synthesize_finalized(finalized, state, llm, app_config)
        return _with_answer_history(_apply_incomplete_notice(_with_result_tables(
            {**synthesized, **db_promotion}, ordered_tasks, task_results, state,
        ), state))

    # 단일 task: 그대로 최종화
    if len(finalized) == 1:
        f = finalized[0]
        out: dict = {
            "final_response": f["text"],
            "current_node": "result_aggregator",
            # CSV 다운로드/row_count용 결과를 top-level state로 승격(D-047)
            "query_results": f.get("query_results") or [],
        }
        if f.get("output_file") is not None:
            out["output_file"] = f["output_file"]
            out["output_file_name"] = f.get("output_file_name")
        # HITL 폼필(D-151): 역질문 페이로드는 API 응답으로, pending은 체크포인터 state로.
        # 폼필은 D-150 단일 task 고정이라 이 단일 분기로 충분하다.
        if "form_fill_clarification" in f:
            out["form_fill_clarification"] = f["form_fill_clarification"]
        if "pending_form_fill" in f:
            out["pending_form_fill"] = f["pending_form_fill"]
        # 존 역질문(D-143 후속2): 단일 task 고정(게이트가 복합 계획 제외)이라 이 분기로 충분.
        if "zone_clarification" in f:
            out["zone_clarification"] = f["zone_clarification"]
        # 저장 값 삭제 패널(D-187) — 이력 조회는 단일 task 단락이라 이 분기로 충분
        if f.get("form_memory_panel"):
            out["form_memory_panel"] = f["form_memory_panel"]
        if f.get("disclosures"):
            out["disclosures"] = f["disclosures"]  # plans/123 W-8
        out.update(db_promotion)
        return _with_answer_history(_apply_incomplete_notice(out, state))

    # 복합 task: order 순으로 묶어 통합
    return _with_answer_history(_apply_incomplete_notice(_with_result_tables(
        {**_merge_finalized(finalized), **db_promotion}, ordered_tasks, task_results, state,
    ), state))


def _with_result_tables(
    out: dict[str, Any], ordered_tasks: list[dict[str, Any]],
    task_results: dict[str, dict[str, Any]], state: AgentState,
) -> dict[str, Any]:
    """이어 붙인 `query_results`의 표 경계를 다음 턴 맥락에 남긴다(plans/134 R-1).

    복합 턴은 task별 표를 따로 보이고 `query_results`는 그 행을 task 순으로 잇는다 — 다음 턴의
    「N번째」가 표를 넘어 세지 않게 표마다 이름(APM 보기 라벨 · 없으면 task 질의)과 행 수를
    `conversation_context[RESULT_TABLES_KEY]`에 싣는다. 행이 있는 표가 둘 이상이고 행 수의 합이
    `query_results`와 같을 때만이다(그 밖은 종전 그대로). 맥락은 다음 턴 `context_resolver`가 새로
    만들므로 이 경계는 바로 다음 턴만 본다. 행·화면 표·CSV에는 칸을 더하지 않는다.
    """
    tables: list[dict[str, Any]] = []
    for task in ordered_tasks:
        res = task_results.get(task["task_id"]) or {}
        rows = res.get("query_results")
        if isinstance(rows, list) and rows:
            views = (res.get(APM_META_KEY) or {}).get("views") or task.get("views")
            tables.append({"label": table_label(views, task.get("sub_query")),
                           "rows": len(rows)})
    merged = out.get("query_results")
    if len(tables) < 2 or not isinstance(merged, list) or sum(
            t["rows"] for t in tables) != len(merged):
        return out
    ctx = state.get("conversation_context") or {}
    return {**out, "conversation_context": {**ctx, RESULT_TABLES_KEY: tables}}


# 서버 식별 컬럼 후보(병합 키 탐지 우선순위). server_name을 canonical로 선호한다.
_IDENTITY_COL_HINTS = ("server_name", "hostname", "host_name", "서버명", "name")

#: 병합 키에서 제거하는 표기 구분자 — 같은 서버의 서로 다른 표기형을 한 키로 모은다.
_IDENTITY_KEY_NOISE = re.compile(r"[\s_.-]")

#: 멀티 DB 결과의 행 출처 태그(`multi_db_executor` · 표의 "출처" 열 — 113 S-2). 병합에서는 일반 열이
#: 아니라 원천 목록으로 모은다(plans/121 TP-11.3).
_MERGE_SOURCE_KEY = "_source_db"


def _identity_key(value: Any) -> str:
    """식별 값을 병합 키로 정규화한다(소문자 + 구분자 제거).

    실측(plans/49 §12.3 B-1a · 2026-09-21): 알람 조회는 `server_name='SV-WEB-001'`을,
    이어지는 조회는 `hostname='svweb001'`을 돌려준다. 원시 값을 키로 쓰면 같은 서버가
    두 키로 갈라져, 뒤이은 base 스코프 절단에서 후속 조회 행이 통째로 탈락했다
    (컬럼만 남고 값이 비어 "데이터 없음"으로 보였다).
    """
    if value is None:
        return ""
    return _IDENTITY_KEY_NOISE.sub("", str(value).strip().lower())


#: 처리기가 표시용 행(`organized_data.rows`)의 긴 셀을 줄였다는 표지(plans/134 W2 —
#: `apm_query.DISPLAY_CUT_KEY`와 같은 값). 병합 표의 CSV 원천은 그 task의 `query_results`(전문)다.
_DISPLAY_CUT_KEY = "display_rows_cut"


def _extract_result_rows(res: dict, *, full: bool = False) -> list[dict]:
    """task 결과에서 행 리스트를 추출한다(organized_data.rows 우선, query_results 폴백).

    `full`이면 표시용으로 셀을 줄인 결과(`_DISPLAY_CUT_KEY`)만 `query_results`(전문)를 쓴다.
    """
    full_rows = res.get("query_results") if full and res.get(_DISPLAY_CUT_KEY) else None
    if isinstance(full_rows, list):
        return full_rows
    organized = res.get("organized_data") or {}
    rows = organized.get("rows")
    if rows is None:
        rows = res.get("query_results")
    return rows if isinstance(rows, list) else []


def _repeats_identity(rows: list[dict[str, Any]], idc: str) -> bool:
    """같은 식별 키를 가진 행이 둘 이상인가(키 없는 행은 세지 않는다)."""
    keys = [_identity_key(r.get(idc)) for r in rows if isinstance(r, dict)]
    present = [k for k in keys if k]
    return len(present) != len(set(present))


def _find_identity_col(rows: list[dict]) -> Optional[str]:
    """행에서 서버 식별 컬럼명을 찾는다(없으면 None). 힌트 우선순위대로 탐색한다."""
    if not rows or not isinstance(rows[0], dict):
        return None
    cols = list(rows[0].keys())
    for hint in _IDENTITY_COL_HINTS:
        for c in cols:
            if str(c).lower() == hint:
                return c
    for hint in _IDENTITY_COL_HINTS:
        for c in cols:
            if hint in str(c).lower():
                return c
    return None


def _merge_task_results_by_identity(
    ordered_tasks: list[dict], task_results: dict[str, dict], *, full: bool = False
) -> Optional[list[dict]]:
    """여러 하위 조회 결과를 공통 서버 식별 키로 병합해 단일 행 목록을 만든다(D-100).

    각 조회(알람 선별·지표·설정 등)가 서버 식별 컬럼(server_name/hostname/name)을 공유하면
    그 값을 canonical 키로 outer join하여, 질의에 언급된 모든 항목을 한 행에 모은다.
    병합 조건: 행이 있는 조회가 2개 이상이고, 그 각각에서 식별 컬럼을 찾을 수 있을 것.
    하나라도 식별 컬럼이 없으면(도메인 이질·집계 스칼라 등) None을 반환해 LLM 합성으로 폴백한다.

    기준(base) 서버 집합: 행수가 가장 적은 조회(가장 좁게 스코프된 결과 — 예: "가장 높은
    서버" 순위 1건)의 서버 키만 최종 표에 남긴다. 이렇게 해야 "심각 알람 서버 중 CPU 최고
    서버의 제조사"에서 알람 선별 결과 전체(2서버)가 아니라 최종 대상(1서버)만 나온다.
    행수가 같으면(각 서버 1행씩) 선행 조회를 기준으로 삼는다(선별 기준 우선).
    단, base가 다른 원천을 `input_from`으로 받은 단계일 때만 좁힌다 — 독립 형제 조회는 좁히지
    않는다(D-234 ③ 개정 · plans/123 G-13).

    카디널리티: 서버 키당 1행(대표)으로 접는다 — 한 조회가 서버당 여러 행(예: 서버당 알람
    다건)이면 먼저 채워진 값을 유지하고 로그로 알린다(침묵 절단 방지). 컬럼 순서는 조회·행
    등장 순서를 보존하며, 모든 식별 컬럼은 canonical 컬럼 하나로 흡수한다(name/server_name 중복 제거).

    Args:
        ordered_tasks: order 순으로 정렬된 task 목록
        task_results: {task_id: 정규화된 결과}
        full: 표시용으로 셀을 줄인 결과는 전문 행으로 병합한다(CSV 원천 — plans/134 W2)

    Returns:
        병합된 행 목록(canonical 식별 컬럼 우선). 병합 불가 시 None.
    """
    sources: list[tuple[list[dict], str]] = []
    source_tasks: list[dict] = []
    for t in ordered_tasks:
        rows = _extract_result_rows(task_results.get(t.get("task_id"), {}), full=full)
        if not rows:
            continue
        idc = _find_identity_col(rows)
        if idc is None:
            return None  # 식별 컬럼 없는 조회가 있으면 결정적 병합 불가 → 합성 폴백
        sources.append((rows, idc))
        source_tasks.append(t)
    if len(sources) < 2:
        return None
    # APM 처리기 행은 거래·요청 단위 관측이다 — 서버 키당 여러 행을 서버 단위로 접으면 다른 거래의
    # 상세가 첫 행에 붙고 나머지 행이 사라진다(plans/134 V-1). 앞 결과 행 참조 보기(프로파일·요청
    # 상세)는 서버당 1행이어도 base 좁히기가 목록의 다른 서버 행을 지운다. 둘 다 병합하지 않고
    # 단계별 답으로 넘긴다. 그 밖 서버당 1행인 APM 결과의 병합(폴스타 결과와의 서버 키 병합 포함)은
    # 종전대로다.
    per_row = [str(t.get("task_id")) for t, (rows, idc) in zip(source_tasks, sources)
               if t.get("agent") == APM_QUERY_AGENT
               and (_repeats_identity(rows, idc) or reference_views_only(t))]
    if per_row:
        logger.info(
            "result_aggregator 병합 취소: APM 처리기 결과(task %s)가 거래·요청 단위 행"
            "(서버 키당 여러 행 또는 앞 결과 행 참조)이라 서버 단위로 접지 않음 — 단계별 답"
            "(plans/134 V-1)",
            ", ".join(per_row),
        )
        return None

    # 공통 서버가 하나도 없으면 결정적 병합은 뜻이 없다 — 각 조회가 서로 다른 서버를
    # 가리키는 독립 조회(예: CPU 상위 3 + 메모리 상위 3)이므로 outer join 표는 절반이
    # 빈 칸이 되고, 이어지는 base 절단이 한쪽을 통째로 지운다. LLM 합성으로 넘긴다.
    key_sets = [{_identity_key(r.get(idc)) for r in rows if isinstance(r, dict)} - {""}
                for rows, idc in sources]
    shared = {k for k in set().union(*key_sets) if sum(k in s for s in key_sets) >= 2}
    if not shared:
        logger.info(
            "result_aggregator 병합 취소: 조회 %d건이 공통 서버를 하나도 공유하지 않음 "
            "— LLM 합성으로 폴백", len(sources),
        )
        return None

    id_cols = {idc for _, idc in sources}
    canonical = "server_name" if "server_name" in id_cols else sources[0][1]

    merged: dict[str, dict] = {}
    col_order: list[str] = [canonical]
    # 병합 행의 출처 원천 목록(등장 순서) — 첫 원천 값만 남기면 "출처"가 오표기된다(TP-11.3).
    row_sources: dict[str, list[str]] = {}
    dropped = 0
    keyless = 0
    for rows, idc in sources:
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_key = row.get(idc)
            key = _identity_key(raw_key)
            if not key:
                keyless += 1  # 식별키 없는 행(전역 COUNT류 등)은 표에 반영 불가
                continue
            slot = merged.setdefault(key, {canonical: raw_key})
            for col, val in row.items():
                if col in id_cols:  # 모든 식별 컬럼은 canonical로 흡수(중복 제거)
                    continue
                if col == _MERGE_SOURCE_KEY:
                    # 원천 태그는 대표값 경쟁 대상이 아니다 — 모으기만 하고 다건으로 세지 않는다
                    seen = row_sources.setdefault(key, [])
                    if val not in (None, "") and str(val) not in seen:
                        seen.append(str(val))
                    if col not in col_order:
                        col_order.append(col)
                    continue
                existing = slot.get(col)
                if col not in slot or existing in (None, ""):
                    slot[col] = val
                elif val not in (None, "", existing):
                    dropped += 1  # 서버당 다건 — 대표값 유지, 흡수 안 된 값 카운트
                if col not in col_order:
                    col_order.append(col)
    if not merged:
        return None

    # 기준 서버 집합: 행수가 가장 적은 조회(가장 좁게 스코프됨)의 키만 남긴다.
    # tie(각 1행)면 min이 첫 인덱스=선행 조회를 고른다(선별 기준 우선).
    base_idx = min(range(len(sources)), key=lambda i: len(sources[i][0]))
    base_keys = key_sets[base_idx]
    # 절단은 **좁히기일 때만** 한다 — base 집합이 다른 조회들의 집합 안에 온전히 들어갈 때.
    # 그렇지 않으면 base에만 있는 서버를 남기려다 다른 조회에만 있는 서버를 지우게 된다
    # (plans/49 §12.3 B-1 — 침묵 손실).
    # 그리고 base가 다른 원천의 결과를 **입력으로 받아 좁힌 단계**일 때만이다(D-234 ③ 개정 ·
    # plans/123 G-13 결함 A). 서로 독립인 형제 조회(「전체 서버 목록과 메모리 64GB 이상 서버」)는
    # 작은 쪽이 큰 쪽의 부분집합이어도 좁히기가 아니다 — 종전에는 전체 목록이 64GB 서버로 잘렸다
    # (run `20260923-103638` R1-03). 좁히기 표지는 데이터 의존 간선(`input_from` — D-203 ·
    # 121 TP-0.1 · 1단 D-095)이다.
    covered = set().union(*(s for i, s in enumerate(key_sets) if i != base_idx))
    narrows = _narrows_other_source(source_tasks, base_idx, ordered_tasks, task_results)
    scoped = (
        {k: v for k, v in merged.items() if k in base_keys}
        if base_keys <= covered and narrows else {}
    )
    if base_keys <= covered and not narrows and len(base_keys) < len(merged):
        logger.info(
            "result_aggregator 병합: 최소 행수 조회(%d행)가 다른 원천을 입력으로 받지 않은 "
            "형제 조회라 좁히지 않음(D-234 ③ 개정 · %d행 유지)",
            len(sources[base_idx][0]), len(merged),
        )
    if scoped:
        if len(scoped) < len(merged):
            logger.info(
                "result_aggregator 병합: 최소 행수 조회(%d행) 기준 스코프로 %d→%d행 축소",
                len(sources[base_idx][0]), len(merged), len(scoped),
            )
        merged = scoped

    if keyless:
        logger.info(
            "result_aggregator 병합: 식별키(%s) 없는 행 %d건 제외 — 전역 COUNT류 "
            "결과는 최종 표에 반영되지 않음",
            canonical, keyless,
        )
    if dropped:
        logger.info(
            "result_aggregator 병합: 서버당 다건으로 %d개 값이 대표행에 흡수되지 않음(대표 유지)",
            dropped,
        )
    out_rows: list[dict[str, Any]] = []
    for key, slot in merged.items():
        if row_sources.get(key):
            # 원천이 둘 이상이면 모두 적는다(표시 쪽이 db_id마다 표시명으로 바꾼다 — TP-11.3)
            slot = {**slot, _MERGE_SOURCE_KEY: ", ".join(row_sources[key])}
        out_rows.append({c: slot.get(c) for c in col_order})
    return out_rows


def _narrows_other_source(
    source_tasks: list[dict],
    base_idx: int,
    ordered_tasks: list[dict],
    task_results: dict[str, dict] | None = None,
) -> bool:
    """base 원천이 **같은 시스템의** 다른 원천 결과를 입력으로 받아 좁힌 단계인가.

    D-234 ③ 개정(plans/123 G-13 · 125 G-6). `input_from`을 계획 전체에서 거슬러 올라가(행 없는
    중간 단계를 건너 — 예: 0건·텍스트 단계) 다른 행 원천에 닿으면 좁히기 후보다. 간선이 없으면 형제
    조회라 좁히지 않는다. **교차 시스템 보강**(폴스타 서버 → 자산관리 담당자처럼 다른 시스템에서
    속성을 찾아 붙이는 단계)은 짝을 못 찾은 행이 있어도 구동 집합을 줄이지 않는다 — 구동 집합 기준
    left join(125 §4.9). 시스템은 task가 조회한 DB의 레지스트리 소유 시스템으로 본다(모르면 같은
    시스템으로 보아 종전처럼 좁힌다).
    """
    by_id = {str(t.get("task_id")): t for t in ordered_tasks if isinstance(t, dict)}
    base_id = str(source_tasks[base_idx].get("task_id"))
    others = {str(t.get("task_id")) for i, t in enumerate(source_tasks) if i != base_idx}
    seen: set[str] = set()
    stack = [str(x) for x in (source_tasks[base_idx].get("input_from") or [])]
    while stack:
        tid = stack.pop()
        if tid in seen or tid == base_id:
            continue
        seen.add(tid)
        if tid in others:
            return not _crosses_system(base_id, tid, task_results or {})
        stack.extend(str(x) for x in ((by_id.get(tid) or {}).get("input_from") or []))
    return False


def _task_systems(res: dict[str, Any]) -> set[str]:
    """task가 조회한 DB들의 레지스트리 소유 시스템 집합(모르면 빈 집합)."""
    from src.routing.registry import get_registry

    try:
        registry = get_registry()
    except Exception:  # noqa: BLE001 — 레지스트리를 못 읽으면 판정 보류(종전 동작)
        return set()
    systems: set[str] = set()
    for db_id in res.get("target_db_ids") or []:
        system = registry.system_of(str(db_id)) or (
            (registry.get(str(db_id)).family or None) if registry.get(str(db_id)) else None
        )
        if system:
            systems.add(str(system))
    return systems


def _crosses_system(base_id: str, upstream_id: str, task_results: dict[str, dict]) -> bool:
    """두 task의 조회 시스템이 겹치지 않으면 교차 시스템 단계다(둘 중 하나라도 모르면 False)."""
    a = _task_systems(task_results.get(base_id) or {})
    b = _task_systems(task_results.get(upstream_id) or {})
    return bool(a) and bool(b) and not (a & b)


def _merged_zone_state(
    merged_rows: list[dict[str, Any]], source_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """병합 원천 task들의 존 커버리지 재료를 병합 표 기준으로 모은다(plans/121 TP-11.6 ②).

    - `db_errors`: 원천 task의 DB별 실패를 합친다(같은 DB가 여러 task에서 실패하면 사유를 잇는다).
    - `db_result_summary`: 원천 task가 조회한 DB마다 **병합 표 행 중 그 DB에서 온 행 수**를 센다 —
      task별 건수를 더하면 병합 표와 맞지 않는다. 표시명은 원천 요약의 것을 쓴다.
    원천에 둘 다 없으면 빈 dict — 단일 DB 병합의 응답은 종전과 같다.
    """
    errors: dict[str, str] = {}
    names: dict[str, Any] = {}
    for res in source_results:
        for db_id, err in (res.get("db_errors") or {}).items():
            errors[db_id] = f"{errors[db_id]} / {err}" if db_id in errors else err
        for db_id, info in (res.get("db_result_summary") or {}).items():
            names.setdefault(db_id, (info or {}).get("display_name"))
    out: dict[str, Any] = {}
    if errors:
        out["db_errors"] = errors
    if names:
        sources = [
            {s.strip() for s in str(r.get(_MERGE_SOURCE_KEY) or "").split(",") if s.strip()}
            for r in merged_rows if isinstance(r, dict)
        ]
        out["db_result_summary"] = {
            db_id: {"row_count": sum(db_id in s for s in sources), "display_name": name or db_id}
            for db_id, name in names.items()
        }
    return out


async def _finalize_merged_path(
    merged_rows: list[dict[str, Any]],
    ordered_tasks: list[dict[str, Any]],
    task_results: dict[str, dict[str, Any]],
    state: AgentState,
    llm: BaseChatModel,
    app_config: AppConfig,
    *,
    csv_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """병합 성립 경로의 마감 — 병합 표 1회 서술 + 병합 원천 밖 task의 결정적 문구.

    plans/121 TP-4.1·11.6.

    행이 없는 task(실패·0건·텍스트 담당)는 병합 원천에서 빠져 오류·0건 사유가 사라졌다(①).
    그 task만
    `_finalize_task`로 마감해 표 뒤에 잇는다 — 행이 없으면 서술 LLM을 부르지 않는 경로다
    (빈 결과 문구·텍스트·오류 안내). 모든 task가 행을 가진 병합은 덧붙임이 없어 종전 병합
    응답과 같다.
    """
    source_results: list[dict[str, Any]] = []
    source_tasks: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    for task in ordered_tasks:
        res = task_results.get(task["task_id"], {})
        if _extract_result_rows(res):
            source_results.append(res)
            source_tasks.append(task)
        else:
            outside.append(task)
    out = await _finalize_merged_rows(
        merged_rows, state, llm, app_config,
        zone_state=_merged_zone_state(merged_rows, source_results),
        source_state=_merged_source_state(source_tasks, source_results),
        csv_rows=csv_rows,
    )
    notes: list[str] = []
    disclosures = list(out.get("disclosures") or [])
    # 병합 원천 task의 처리기 고지(plans/134 W0-B — 작업 참조 `ref` 포함)도 잃지 않는다
    for res in source_results:
        disclosures.extend(_handler_disclosures(res))
    # 병합 표에 이미 실린 `[조회 기간]` 줄은 병합 밖 task 문단에서 다시 싣지 않는다(plans/122 T-8)
    period_seen = _period_lines(out.get("final_response") or "")
    for task in outside:
        f = await _finalize_task(
            task, task_results.get(task["task_id"], {}), state, llm, app_config,
            stream_user_response=False,
        )
        disclosures.extend(f.get("disclosures") or [])
        text = _drop_repeated_period_lines(f.get("text") or "", period_seen)
        if text:
            notes.append(text)
        if f.get("output_file") is not None and "output_file" not in out:
            out["output_file"] = f["output_file"]
            out["output_file_name"] = f.get("output_file_name")
    lines = [line for res in source_results for line in _handler_answer_lines(res)]
    if lines:  # 병합 원천 task의 결정적 판정·집계 줄(plans/134 M-1)
        out["final_response"] = _with_answer_lines(out.get("final_response") or "", lines)
    handler = [d for res in source_results for d in _handler_disclosures(res)]
    if handler:  # 병합 원천 task의 비의무 처리기 고지(plans/134 W2 검증 B1)
        out["final_response"] = _with_handler_notices(out.get("final_response") or "", handler)
    if notes:
        body = (out.get("final_response") or "").strip()
        out["final_response"] = "\n\n".join([body, *notes]) if body else "\n\n".join(notes)
    if disclosures:
        out["disclosures"] = disc.dedupe(disclosures)  # plans/123 W-8
    return out


def _merged_source_state(
    source_tasks: list[dict[str, Any]], source_results: list[dict[str, Any]]
) -> dict[str, Any]:
    """병합 원천 task들의 결정적 고지 입력을 모은다(plans/123 W-1 ③ · W-3 · S-8).

    - `limit_sources`: 원천별 실행 SQL·원 조회 행·존별 건수 — 병합 표의 행 수는 상한 판정 입력이
      아니다(원천이 10,000행에 닿아도 병합 표는 3,000행일 수 있다).
    - `executed_sqls`: 원천 실행 SQL 전체(생성기 자기 고백 수집).
    - `spike_notes`: 원천 급증 한계 표기의 합집합(순서 보존).
    - `period_sources`: 원천별 시간 해석·실행 SQL·의도 — `[조회 기간]` 고지 입력(plans/122 T-8).
    - 의도: 원천이 **모두** 알람 조회일 때만 알람(헤드라인·당월 각주 제외) — 섞인 병합 표는 지표
      값이 있어 당월 각주가 필요하고, 알람 건수 헤드라인은 병합 표 행 수와 뜻이 다르다.
    """
    executed: list[dict[str, Any]] = []
    spikes: list[str] = []
    limit_sources: list[dict[str, Any]] = []
    period_sources: list[dict[str, Any]] = []
    for task, res in zip(source_tasks, source_results):
        sqls = [e for e in (res.get("executed_sqls") or []) if isinstance(e, dict) and e.get("sql")]
        executed.extend(sqls)
        for note in res.get("spike_notes") or []:
            if note not in spikes:
                spikes.append(note)
        limit_sources.append({
            "query_attempts": [{"sql": e["sql"]} for e in sqls],
            "query_results": res.get("query_results") or _extract_result_rows(res),
            "db_result_summary": res.get("db_result_summary"),
        })
        # 원천 task별 기간 고지 입력(plans/122 T-8) — 해석이 없으면 출력 생성이 턴 해석을 쓴다
        period_sources.append({
            "time_resolution": res.get("time_resolution"),
            "executed_sqls": sqls,
            "routing_intent": "alarm_query" if task.get("agent") == "alarm_query" else None,
        })
    all_alarm = bool(source_tasks) and all(t.get("agent") == "alarm_query" for t in source_tasks)
    return {
        "agent": "alarm_query" if all_alarm else "data_query",
        "executed_sqls": executed,
        "spike_notes": spikes or None,
        "limit_sources": limit_sources,
        "period_sources": period_sources,
    }


async def _finalize_merged_rows(
    merged_rows: list[dict],
    state: AgentState,
    llm: BaseChatModel,
    app_config: AppConfig,
    *,
    zone_state: dict[str, Any] | None = None,
    source_state: dict[str, Any] | None = None,
    csv_rows: list[dict[str, Any]] | None = None,
) -> dict:
    """병합된 통합 행을 단일 output_generator로 최종 표/자연어 응답으로 만든다(D-100).

    Args:
        merged_rows: _merge_task_results_by_identity 산출 통합 행
        state: 전체 에이전트 상태(원본 질의)
        llm: LLM 인스턴스
        app_config: 앱 설정
        zone_state: 병합 원천의 존 커버리지 재료(`_merged_zone_state` — plans/121 TP-11.6 ②)
        csv_rows: CSV 원천 통합 행(표시용으로 줄인 셀을 전문으로 병합한 것 — 없으면 `merged_rows`)

    Returns:
        final_response/query_results/current_node(+ output_file)를 포함한 State 갱신 dict
    """
    organized = {
        "summary": f"총 {len(merged_rows)}건의 통합 결과입니다.",
        "rows": merged_rows,
        "column_mapping": None,
        "resolved_mapping": None,
        "is_sufficient": True,
        "sheet_mappings": None,
    }
    # 통합 표는 전체 질의에 대한 답이므로 original_query를 전체 질의로 둔다(sub-task 스코프 아님).
    src_state = source_state or {}
    merge_task = {
        "sub_query": state.get("user_query", ""),
        "agent": src_state.get("agent") or "data_query",
    }
    full_rows = csv_rows if csv_rows is not None else merged_rows
    out_state = _build_output_state(
        state, merge_task,
        {
            "organized_data": organized,
            "query_results": full_rows,
            **(zone_state or {}),
            "executed_sqls": src_state.get("executed_sqls") or [],
            "spike_notes": src_state.get("spike_notes"),
        },
        limit_sources=src_state.get("limit_sources"),
        period_sources=src_state.get("period_sources"),
    )
    # _build_output_state가 original_query를 sub_query(=전체 질의)로 세팅 — 그대로 사용.
    out: dict[str, Any] = {}
    try:
        out = await output_generator(out_state, llm=llm, app_config=app_config)
        text = out.get("final_response", "")
    except Exception as e:  # noqa: BLE001 — 표 생성 실패는 로그 후 최소 안내
        logger.error("result_aggregator 병합 표 생성 실패: %s", e)
        text = f"통합 결과 {len(merged_rows)}건을 정리하는 중 오류가 발생했습니다: {e}"
    result: dict[str, Any] = {
        "final_response": text,
        "current_node": "result_aggregator",
        "query_results": full_rows,
    }
    # 병합 표로 만든 파일을 버리지 않는다(plans/121 TP-11.6 ③ — 종전에는 텍스트만 돌려줬다).
    if out.get("output_file") is not None:
        result["output_file"] = out["output_file"]
        result["output_file_name"] = out.get("output_file_name")
    if out.get("disclosures"):
        result["disclosures"] = list(out["disclosures"])  # plans/123 W-8
    return result


def _apply_incomplete_notice(result: dict, state: AgentState) -> dict:
    """오케스트레이션 조기 종료 안내문을 최종 응답 말미에 덧붙인다 (D-092).

    deep_agent가 오케스트레이터 루프의 조기 종료(빈 AI 응답)를 감지하면 수행된 조회와
    미실행 작업 안내문을 state["orchestration_incomplete_notice"]로 전달한다. LLM 합성
    결과에 의존하지 않고 결정적으로 덧붙여, 일부 하위 작업만 수행된 부분 결과가 완전한
    답처럼 보이는 것을 차단한다(침묵적 강등 금지). 안내문이 없으면 원본 그대로 반환한다.
    순차 처리 경과 블록(D-203)도 같은 자리에서 이어 붙인다 — 4개 반환 지점의 단일 통과점.

    Args:
        result: result_aggregator가 반환할 State 갱신 dict
        state: 현재 에이전트 상태 (안내문 보유 여부 확인용)

    Returns:
        안내문이 덧붙은 dict (안내문 없으면 원본 그대로)
    """
    # 앞 턴 0행 지시어 승계(plans/146 W3) — 안내·넓힘 차단·다음 턴 기록. 같은 단일 통과점이다.
    result = _apply_empty_antecedent(result, state)
    # 제니퍼 소스 되묻기·승계(plans/147) — APM task 결과를 상태로 올린다. 같은 단일 통과점이다.
    result = _apply_apm_source_state(result, state)
    # 결정적 고지(plans/123 W-8·W-9) — 턴 단위 고지를 턴당 한 번 붙이고, 합성에서 떨어진 task 단위
    # 의무 고지를 되살린다. 같은 단일 통과점이다.
    result = _apply_disclosures(result, state)
    # 재계획기가 결정적으로 멈춘 사유(plans/118 P-1 시간 상한 · P-2 전 DB 연속 0건)도 같은
    # 자리에서 싣는다 — 4개 반환 지점의 단일 통과점이다(침묵적 종료 금지).
    notice = "\n\n".join(
        n for n in (
            (state.get("orchestration_incomplete_notice") or "").strip(),
            (state.get("replan_stop_notice") or "").strip(),
        ) if n
    )
    if not notice:
        return _apply_dependency_notes(result, state)
    body = (result.get("final_response") or "").strip()
    out = dict(result)
    out["final_response"] = f"{body}\n\n---\n{notice}" if body else notice
    return _apply_dependency_notes(out, state)


def _apply_apm_source_state(result: dict[str, Any], state: AgentState) -> dict[str, Any]:
    """APM task의 소스 되묻기·승계를 상태 키로 올린다(plans/147 §4.3 · D-322).

    - 되묻기(`apm_source_clarification`): 첫 APM task 것 하나 — 응답 칸(요청 스코프)과 다음 턴 글
      답 판정용 대기 값(`apm_source_pending` = 원 질문 · 선택지)을 함께 쓴다. 같은 턴의 다른 task
      결과는 그대로 둔다(존 역질문 단락과 달리 부분 답 + 되묻기).
    - 승계(`apm_source_scope`): 사다리가 승계 값을 낸 APM task(마지막 것) — 근거·소스 + 이번 턴 행이
      온 소스(`last_target_sources` — 지시어 후속 재료).
    존 역질문이 이 턴의 답이면(다른 task 결과를 버리는 단락) 아무것도 쓰지 않는다. 없으면 원본
    그대로다.
    """
    if result.get("zone_clarification"):
        return result
    task_results = state.get("task_results") or {}
    ask: dict[str, Any] | None = None
    scope: dict[str, Any] | None = None
    for task in sorted(state.get("task_plan") or [], key=lambda t: t.get("order", 0)):
        res = task_results.get(str(task.get("task_id")))
        if task.get("agent") != APM_QUERY_AGENT or not isinstance(res, dict):
            continue
        if ask is None and isinstance(res.get(SOURCE_CLARIFICATION_KEY), dict):
            ask = res[SOURCE_CLARIFICATION_KEY]
        picked = (res.get(APM_META_KEY) or {}).get("source_selection") or {}
        if isinstance(picked.get("scope"), dict):
            scope = {**picked["scope"],
                     "last_target_sources": list(picked.get("last_target_sources") or [])}
    if ask is None and scope is None:
        return result
    out = dict(result)
    if ask is not None:
        out[SOURCE_CLARIFICATION_KEY] = ask
        out["apm_source_pending"] = {
            "query": ask.get("original_query") or "",
            "choices": [o.get("key") for o in ask.get("options") or [] if isinstance(o, dict)],
        }
    if scope is not None:
        out["apm_source_scope"] = scope
    return out


def _turn_sql_outcome(state: AgentState) -> tuple[list[str], int, list[str]] | None:
    """이번 턴 계획의 SQL 조회 결과 — (실행 SQL, 총 행 수, 행을 낸 task의 실행 SQL).

    모든 task가 SQL을 실행해 오류 없이 끝났을 때만 값을 낸다 — 조회가 아닌 task(APM·추론 등)나
    실패 task가 섞이면 None(0행 판정을 하지 않는다 · plans/146 W3). 선행 0건으로 순차 게이트가
    실행하지 않은 task(D-203 `prior_empty`)는 0행 결과로 센다.
    """
    tasks = state.get("task_plan") or []
    task_results = state.get("task_results") or {}
    sqls: list[str] = []
    row_sqls: list[str] = []
    rows = 0
    for task in tasks:
        res = task_results.get(str(task.get("task_id")))
        if isinstance(res, dict) and res.get("skip_reason") == REASON_PRIOR_EMPTY:
            continue
        if not isinstance(res, dict) or res.get("error"):
            return None
        executed = [
            str(e["sql"]) for e in (res.get("executed_sqls") or [])
            if isinstance(e, dict) and e.get("sql")
        ]
        if not executed:
            return None
        sqls.extend(executed)
        task_rows = len(_extract_result_rows(res))
        if task_rows:
            row_sqls.extend(executed)
        rows += task_rows
    return (sqls, rows, row_sqls) if sqls else None


def _apply_empty_antecedent(result: dict[str, Any], state: AgentState) -> dict[str, Any]:
    """앞 턴 0행 지시어 승계를 마감한다 (plans/146 W3 · G-3 (a)) — 1·2단 단일 통과점.

    - 이번 턴이 앞 턴 0행 조회를 지시어로 가리켰고(`EMPTY_ANTECEDENT_KEY`) 다시 0행이면
      「앞 질문에서 찾은 서버가 없다」를 본문 뒤에 붙인다. 행이 나왔는데 실행 SQL이 앞 턴 조건 값을
      하나도 잇지 않았으면 행은 그대로 두고 「전체 대상일 수 있다」 안내를 맨 앞에 싣는다(교정 1 —
      판정이 근사라 숨기지 않는다 · 조건 없이 넓힌 침묵 오답 차단).
    - 이번 턴이 0행이면 다음 턴 지시어가 잇도록 기록(`ZERO_ROW_TURN_KEY`)을 맥락에 남긴다.
    - 존 역질문 턴은 승계 중이면 기록만 넘긴다(존 답변 턴이 잇는다 · M-2).
    파일 산출 턴과 SQL 조회가 아닌 턴은 건드리지 않는다(종전 그대로).
    """
    if result.get("zone_clarification"):
        zone_ctx = carried_zone_turn_context(
            result.get("conversation_context") or state.get("conversation_context")
        )
        return {**result, "conversation_context": zone_ctx} if zone_ctx else result
    if result.get("output_file") is not None:
        return result
    outcome = _turn_sql_outcome(state)
    if outcome is None:
        return result
    sqls, rows, row_sqls = outcome
    ctx = state.get("conversation_context") or {}
    antecedent: dict[str, Any] | None = ctx.get(EMPTY_ANTECEDENT_KEY)
    out = dict(result)
    # 넓힘 판정은 행을 낸 task의 SQL로 한다 — 같은 턴에 앞 턴 조건을 다시 조회한 task(0행)가 있어도
    # 행을 낸 후속 task가 조건을 잇지 않았으면 넓힌 것이다.
    widened = (
        antecedent is not None and rows > 0
        and widened_beyond_antecedent(antecedent, row_sqls)
    )
    if antecedent and (rows == 0 or widened):
        note = empty_antecedent_note(antecedent, widened=widened)
        body = (out.get("final_response") or "").strip()
        if widened:
            logger.info(
                "앞 턴 0행 지시어 — 이번 조회 %d행이 앞 턴 조건을 잇지 않아 맨 앞에 안내"
                "(plans/146 W3)", rows,
            )
            out["final_response"] = f"{note}\n\n{body}" if body else note
        else:
            out["final_response"] = f"{body}\n\n{note}" if body else note
    if rows == 0:
        record = carried_zero_row_turn(antecedent, str(state.get("user_query") or ""), sqls)
        if record:
            base_ctx = out.get("conversation_context") or ctx
            out["conversation_context"] = {**base_ctx, ZERO_ROW_TURN_KEY: record}
    return out


def _norm_text(text: str) -> str:
    """본문 대조용 정규화 — 공백·강조 표지를 걷어낸다."""
    return " ".join(str(text).replace("**", "").split())


def _collect_task_disclosures(finalized: list[dict[str, Any]]) -> list[disc.Disclosure]:
    """task 마감 결과들의 고지를 등장 순서대로 모은다(plans/123 W-8)."""
    return disc.dedupe(d for f in finalized for d in (f.get("disclosures") or []))


def _turn_level_disclosures(
    result: dict[str, Any], state: AgentState
) -> tuple[str | None, list[disc.Disclosure]]:
    """턴 단위 고지 — (좁힌 범위 문단, 고지 목록) (plans/123 W-2·W-4·W-5·S-1).

    - W-2 좁힌 범위: 라우트가 남긴 기록(`scope_narrowed`)의 문구 — 3단 `output_generator`
      (`_append_scope_note`)와 같은 문구·같은 모양으로 싣는다(경로 대칭).
    - W-4 미등록 존 · S-1 단위 의심: 라우트가 원문으로 정한 `turn_disclosures`.
    - W-5 「전체」 강화: 좁힌 기록이 없는데 「전체」를 요청하고 조회 가능 존의 일부만 조회했으면.
    """
    from src.domain.scope_select import render_narrowed_note
    from src.nodes.output_generator import scope_partial_text
    from src.utils.query_gen_common import has_all_scope_keyword

    query = str(state.get("user_query") or "")
    items: list[disc.Disclosure] = []
    narrowed = render_narrowed_note(
        state.get("scope_narrowed"), full_scope_requested=has_all_scope_keyword(query)
    ) or None
    if narrowed:
        items.append(disc.make(disc.SCOPE_NARROWED, narrowed.lstrip("- ")))
    items.extend(disc.dedupe(state.get("turn_disclosures") or []))
    queried = [
        str(t.get("db_id")) for t in (result.get("target_databases") or [])
        if isinstance(t, dict) and t.get("db_id")
    ]
    partial = scope_partial_text(
        {**state, "original_user_query": query},
        queried_db_ids=queried,
        db_origin=result.get("db_scope_source"),
    )
    if partial:
        items.append(disc.make(disc.SCOPE_PARTIAL, partial))
    return narrowed, items


def _apply_disclosures(result: dict[str, Any], state: AgentState) -> dict[str, Any]:
    """결정적 고지를 턴 단위로 한 번 정리한다 — 1·2단 단일 통과점(plans/123 W-9).

    - **task 단위**(상한 도달 · 생성기 고백 · 조건 변경 · 조회 실패)는 각 task 마감
      (`output_generator` · `_finalize_task`)이 이미 본문에 붙였다. 1단 합성(D-062)은 LLM이
      그 줄을 떨어뜨릴 수 있어 **의무 고지가 본문에 없으면 합성 뒤에 다시 붙인다**.
    - **턴 단위**(좁힌 범위 · 미등록 존 · 단위 의심 · 「전체」 강화)는 여기서만 붙인다 — task 마감에
      붙이면 2단 단계별 답변에서 task 수만큼 반복된다. 의무 고지는 모두, 그 밖은 최대 3줄
      (`disclosure.body_lines_for_turn`) — 나머지는 구조 필드에만 남는다.
    - 존 역질문 턴에는 붙이지 않는다(되묻는 턴은 조회 결과가 아니다).
    - 모은 고지 전체를 `disclosures`로 돌려준다(라우트가 API에 싣는다). 고지가 없으면 원본 그대로.
    """
    if result.get("zone_clarification"):
        return result
    task_items = disc.dedupe(result.get("disclosures") or [])
    narrowed, turn_items = _turn_level_disclosures(result, state)
    if not task_items and not turn_items:
        return result
    body = (result.get("final_response") or "").strip()
    tail: list[str] = []
    for d in task_items:
        spec = disc.KIND_TABLE.get(d["kind"])
        if spec is not None and spec.mandatory and _norm_text(d["text"]) not in _norm_text(body):
            tail.append(disc.render_line(d))
    if narrowed:
        tail.append(narrowed)
    others = [d for d in disc.body_lines_for_turn(turn_items) if d["kind"] != disc.SCOPE_NARROWED]
    if others:
        tail.append(disc.render_lines(others))
    out = dict(result)
    if tail:
        block = "\n\n".join(tail)
        out["final_response"] = f"{body}\n\n{block}" if body else block
    out["disclosures"] = disc.dedupe([*task_items, *turn_items])
    return out


def _collect_dependency_notes(result: dict, state: AgentState) -> list[dict]:
    """상태·결과·task_plan(1단 도구 경로가 task에 실은 노트)의 순차 경과 노트를 한 목록으로 모은다."""
    notes: list[dict] = []
    seen: set[tuple] = set()
    sources = [state.get("dependency_notes") or [], result.get("dependency_notes") or []]
    # 1단(deepagents)은 ambient가 복사될 수 있어 노트를 task dict에 싣는다(D-203 · plans/88 §4.10).
    sources.append([t.get("dependency_note") for t in (state.get("task_plan") or []) if isinstance(t, dict)])
    # 사후 대조·DB별 분할 노트는 task 결과에 실린다(1단·2단 공통 — 집계기가 한 곳에서 모은다).
    for res in (state.get("task_results") or {}).values():
        if isinstance(res, dict) and res.get("dependency_notes"):
            sources.append(res["dependency_notes"])
    for src in sources:
        for n in src:
            if not isinstance(n, dict) or not n.get("detail"):
                continue
            key = (n.get("kind"), n.get("task_id"), n.get("detail"))
            if key in seen:
                continue
            seen.add(key)
            notes.append(n)
    return notes


def _apply_dependency_notes(result: dict, state: AgentState) -> dict:
    """순차 처리 경과(게이트·대조·절단·충족도 미달)를 최종 응답 말미에 **결정적으로** 덧붙인다.

    D-203 · plans/88 §4.4. LLM 합성에 맡기면 누락된다(`spike_notes`와 같은 이유). 노트가 없으면
    원본 그대로(바이트 동일). 모은 노트는 `dependency_notes`로도 돌려줘 라우트가 API에 노출한다.
    """
    notes = _collect_dependency_notes(result, state)
    block = render_dependency_notes(notes)
    if not block:
        return result
    body = (result.get("final_response") or "").strip()
    out = dict(result)
    out["final_response"] = f"{body}\n\n---\n{block}" if body else block
    out["dependency_notes"] = notes
    return out


def _with_answer_history(result: dict) -> dict:
    """최종 응답을 대화 이력(messages)에 AIMessage로 누적한다 (②, 멀티턴 후속 판단용).

    orchestration 경로의 유일한 top-level finalizer인 result_aggregator에서만 어시스턴트
    답변을 messages에 append한다(add_messages 리듀서로 누적). 이렇게 해야 다음 턴의
    추론 agent(general_inference)가 직전 턴 답변을 근거로 rightsizing 등 판단을 할 수 있다.
    subagent 반환은 task_results에만 담기고 top-level messages로 승격되지 않으므로(D-053
    비대칭) 이중 누적 위험이 없다. final_response가 비면 append하지 않는다.

    Args:
        result: result_aggregator가 반환할 State 갱신 dict

    Returns:
        messages(AIMessage 1건)가 추가된 dict (final_response가 비면 원본 그대로)
    """
    text = (result.get("final_response") or "").strip()
    if not text:
        return result
    return {**result, "messages": [AIMessage(content=text)]}


def _zone_clarification_from_tasks(
    ordered_tasks: list[dict], task_results: dict[str, dict]
) -> Optional[dict]:
    """이번 턴의 존 역질문 페이로드 1개. 없으면 None (G-3 · D-143 후속2).

    한 턴의 task 들은 같은 원문·같은 존 신호를 보므로 **같은 페이로드**를 만든다.
    order 순으로 첫 번째 것을 취해 턴당 1회로 묶는다 — 복합 계획에서 task 수만큼 같은
    질문이 쌓이는 것을 막는 지점이다.
    """
    for task in ordered_tasks:
        result = task_results.get(task.get("task_id"), {})
        if isinstance(result, dict) and result.get("zone_clarification"):
            return result["zone_clarification"]
    return None


#: 존 재진입 스냅샷에 싣는 task 계획 필드(plans/121 TP-1.2). 상태(`status`)·응답(`direct_response`)·
#: 실행 중 노트(`dependency_note`)는 싣지 않는다 — 다음 턴이 계획을 새로 실행한다. `source_slot`은
#: 소스 선택 칩 task의 칩 전 담당(plans/132 N-10) — 답변 턴이 원래 담당 또는 고른 처리기로 되살린다.
_ZONE_REENTRY_PLAN_FIELDS = (
    "task_id", "agent", "sub_query", "depends_on", "input_from", "order",
    "db_ids", "supersedes", "capability", "spans", "agent_fallback", "source_slot",
)


def _zone_reentry_snapshot(
    tasks: list[dict[str, Any]], task_results: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """존 역질문 턴의 계획 스냅샷 — 복합 계획(task 2개 이상)일 때만, 아니면 None.

    plans/121 TP-1.2 · G-30 · D-272 ⑪. 단일 task 계획은 종전 ②.5 단일 복원이 같은 일을 하므로
    남기지 않는다(단일 의도 답변 턴 바이트 불변). `gated_task_ids`는 결과에 존 역질문이 실린
    task다 — 복원 때 그 task만 선택 존으로 고정한다.
    """
    plan = [t for t in tasks if isinstance(t, dict) and t.get("task_id")]
    if len(plan) < 2:
        return None
    gated = [
        str(t["task_id"]) for t in plan
        if isinstance(task_results.get(t["task_id"]), dict)
        and task_results[t["task_id"]].get("zone_clarification")
    ]
    if not gated:
        return None
    return {
        "tasks": [
            {k: (list(t[k]) if isinstance(t[k], list) else t[k])
             for k in _ZONE_REENTRY_PLAN_FIELDS if k in t}
            for t in plan
        ],
        "gated_task_ids": gated,
    }


def _collect_db_promotion(
    tasks: list[dict], task_results: dict[str, dict]
) -> dict:
    """실행된 task들의 target_db_ids를 top-level state 필드로 승격한다 (D-053, 멀티턴 DB 승계).

    orchestration 경로는 subagent가 active_db_id/target_databases를 isolated에만 쓰고
    top-level로 올리지 않아, 다음 턴 context_resolver._extract_previous_db_ids가 읽을
    값이 없다. 본 함수가 task_results의 target_db_ids를 모아 `active_db_id`(첫 DB)와
    `target_databases`([{db_id}])로 승격하여 멀티턴 DB 승계를 복원한다.

    Args:
        tasks: 전체 task_plan
        task_results: {task_id: 정규화된 결과}

    Returns:
        {"active_db_id", "target_databases"} dict (승격할 db_id가 없으면 빈 dict)
    """
    db_ids: list[str] = []
    origin: Optional[str] = None
    for t in tasks:
        res = task_results.get(t.get("task_id"), {})
        for did in res.get("target_db_ids") or []:
            if did and did not in db_ids:
                db_ids.append(did)
        if origin is None and res.get("db_origin"):
            origin = str(res["db_origin"])
    if not db_ids:
        return {}
    promoted = {
        "active_db_id": db_ids[0],
        "target_databases": [{"db_id": d} for d in db_ids],
    }
    if origin:
        # D-205: 스코프 출처도 top-level로 — target_databases shape는 기존 테스트가 고정하므로 별도 키.
        promoted["db_scope_source"] = origin
    return promoted


def _collect_superseded(tasks: list[dict], task_results: dict[str, dict]) -> set[str]:
    """대체(재조회)되어 최종 답변 본문에서 숨길 선행 task_id 집합을 계산한다(D-043).

    각 task의 `supersedes`(선행 task_id 목록)를 읽되, **그 후속 task가 성공했을 때만**
    선행을 숨김 대상으로 인정한다. 후속이 실패(error)했거나 결과가 없으면 선행을 그대로
    유지하여, 재조회 실패 시 사용자에게 빈 답변만 보이는 상황을 방지한다.

    Args:
        tasks: 전체 task_plan
        task_results: {task_id: 정규화된 결과}

    Returns:
        본문에서 제외할 선행 task_id 집합
    """
    superseded: set[str] = set()
    for t in tasks:
        targets = t.get("supersedes") or []
        if not targets:
            continue
        res = task_results.get(t.get("task_id"), {})
        # 후속(대체) task 자체가 실패했으면 선행을 숨기지 않는다.
        if res.get("error"):
            continue
        superseded.update(tid for tid in targets if tid)
    return superseded


def _is_row_result(res: dict[str, Any]) -> bool:
    """행 모양 결과(조회 담당)인지 — 텍스트 담당 결과(`final_response`만)는 0건이 없다."""
    return isinstance(res.get("organized_data"), dict) or isinstance(res.get("query_results"), list)


def _task_rounds(
    tasks: list[dict[str, Any]], replan_history: list[dict[str, Any]]
) -> dict[str, int]:
    """task_id → 바퀴 번호(0 = 최초 계획 · k = k번째 재계획이 추가한 task).

    재계획은 신규 task만 order 뒤에 붙이고(R-A1) 회차마다 추가 개수를 `replan_history.added`에
    남긴다(`replanner._rounds`와 같은 복원). 개수가 맞지 않으면 빈 dict — 바퀴를 모르면
    숨기지 않는다.
    """
    ordered = sorted(tasks, key=lambda t: t.get("order", 0))
    added = [int(h.get("added") or 0) for h in replan_history if isinstance(h, dict)]
    if sum(added) > len(ordered):
        return {}
    rounds: dict[str, int] = {}
    start = 0
    for idx, size in enumerate([len(ordered) - sum(added), *added]):
        for t in ordered[start:start + size]:
            rounds[str(t.get("task_id"))] = idx
        start += size
    return rounds


def _collect_implicit_superseded(
    tasks: list[dict[str, Any]],
    task_results: dict[str, dict[str, Any]],
    replan_history: list[dict[str, Any]],
) -> set[str]:
    """`supersedes`가 빠진 재조회의 선행 task_id — 단계별 조립 전용(plans/121 TP-4.5 · D-062).

    LLM 합성이 없으면 "없음(선행) → 있음(재조회)" 두 서술이 한 답에 남는다. 재계획 LLM이
    `supersedes`를 빠뜨려도(D-043은 명시가 있어야 숨긴다) 다음 조건이 모두 맞으면 선행을 숨긴다.

    - 선행이 실패(`error` — 순차 게이트 미실행 포함)했거나 행 모양 결과가 0건이다.
    - **뒤 바퀴**(재계획이 나중에 추가한 회차)에 **같은 담당**의 후속이 있고, 그 후속은 데이터
      의존(`input_from`)이 없으며 성공했다 — 오류 없음 · 행 모양이면 1행 이상 · 텍스트면 본문 있음.

    같은 바퀴의 task(최초 분해가 나눈 서로 다른 질문)는 숨기지 않는다 — 한 질문이 0건이고 다른
    질문이 성공한 것은 재조회가 아니다(침묵 손실 방지). 행이 있는 선행(부분 성공)은 대상이
    아니다 — 그 재조회는 재계획 단계에서 이미 걸러진다(`replanner._filter_futile_retries` · D-063).
    """
    rounds = _task_rounds(tasks, replan_history)
    hidden: set[str] = set()
    for pred in tasks:
        pid = str(pred.get("task_id"))
        pres = task_results.get(pid) or {}
        if pid not in rounds or not (
            pres.get("error") or (_is_row_result(pres) and not _extract_result_rows(pres))
        ):
            continue
        for succ in tasks:
            sid = str(succ.get("task_id"))
            sres = task_results.get(sid) or {}
            if (
                rounds.get(sid, -1) <= rounds[pid]
                or succ.get("agent") != pred.get("agent")
                or succ.get("input_from")
                or sres.get("error")
            ):
                continue
            ok = (
                bool(_extract_result_rows(sres)) if _is_row_result(sres)
                else bool(str(sres.get("final_response") or "").strip())
            )
            if ok:
                logger.info(
                    "result_aggregator: 재조회 %s 성공 — 실패·0건 선행 %s 본문 숨김"
                    "(supersedes 없음 · 같은 담당 %s)", sid, pid, pred.get("agent"),
                )
                hidden.add(pid)
                break
    return hidden


async def _finalize_task(
    task: dict,
    res: dict,
    state: AgentState,
    llm: BaseChatModel,
    app_config: AppConfig,
    *,
    stream_user_response: bool = True,
) -> dict:
    """단일 task 결과를 최종 텍스트(+선택적 파일)로 정규화한다.

    - data_query/alarm_query: organized_data를 output_generator로 최종화.
    - 텍스트 계열(cache/synonym/general): res["final_response"]를 그대로 사용.
    - error가 있으면 부분 실패 안내 문구.

    Args:
        task: 현재 TaskSpec
        res: 해당 task의 정규화된 결과
        state: 전체 에이전트 상태 (output_generator 입력 보강용)
        llm: LLM 인스턴스
        app_config: 앱 설정
        stream_user_response: output_generator의 USER_RESPONSE_TAG 부여 여부.
            합성 모드(D-062)에서 중간 per-task 토큰 누출을 막기 위해 False로 전달.

    Returns:
        {"order", "agent", "text", "output_file", "output_file_name", "error"} dict
    """
    agent = task.get("agent", "")
    base: dict = {
        "order": task.get("order", 0),
        "agent": agent,
        "text": "",
        "output_file": None,
        "output_file_name": None,
        "error": res.get("error"),
        # CSV 다운로드/row_count용 원시 결과를 보존한다(orchestration 경로에서 query_results를
        # top-level state로 승격하기 위함 — process_query 전체 프로세스 CSV 등, D-047).
        "query_results": res.get("query_results") or [],
    }
    # 존 역질문(D-143 후속2): 페이로드를 최종 응답까지 운반(form_fill_clarification 동형).
    if "zone_clarification" in res:
        base["zone_clarification"] = res["zone_clarification"]
    # 저장 값 삭제 패널(D-187): 이력 조회 단락(direct_response)의 구조화 컨텍스트 운반
    if res.get("form_memory_panel"):
        base["form_memory_panel"] = res["form_memory_panel"]

    # 조회 실패를 「데이터 없음」으로 바꾸지 않는다(plans/123 W-6) — 실패 + 0행이면 결과 정리·출력
    # 생성의 빈 결과 문구(「조건에 해당하는 … 데이터가 없습니다」)가 아니라 사유가 있는 실패 안내로
    # 끝낸다.
    failure = _failure_disclosure(task, res)
    organized = res.get("organized_data")
    if (
        failure is not None
        and organized is not None
        and not _extract_result_rows(res)
        # 결정적 조사 처리기는 0행 원인 진단 요약을 스스로 낸다(Plan 50 M4 · D-046) — 그대로 둔다
        and agent not in ("process_query", HOST_INSPECT_AGENT)
    ):
        base["text"] = failure["text"]
        base["disclosures"] = [failure]
        logger.info(
            "result_aggregator: task %s 실패를 빈 결과가 아니라 실패 안내로 마감"
            "(kind=%s · plans/123 W-6)",
            task.get("task_id"), failure["kind"],
        )
        return base

    # data 계열: organized_data가 있으면 output_generator로 최종화
    if organized is not None:
        # 결정적 subagent(process_query)는 빈 결과에도 **원인 진단 summary**를 제공한다
        # (서버 식별 실패·API 미연결·API 미응답·0건 등). output_generator의 일반
        # "조건에 해당하는 …데이터가 없습니다" 문구로 덮어쓰면 실제 원인이 사라지므로,
        # 빈 결과(rows=[]) + summary가 있으면 그 summary를 그대로 노출한다(Plan 50 M4 / D-046).
        # 호스트 조사(plans/121 TP-1.5)도 같은 규약이다 — 결정적 요약이 0행 원인을 담는다.
        if (
            agent in ("process_query", HOST_INSPECT_AGENT)
            and not organized.get("rows")
            and organized.get("summary")
        ):
            base["text"] = organized["summary"]
            return base
        s = _build_output_state(
            state, task, res,
            # 조건 반영 대조(plans/123 S-11) 1차는 단일 task 계획만 — 복합은 전역 조건이 모든 task에
            # 들어가(D-094 ③) 다른 task 몫 조건 누락을 오탐한다(121 TP-3.2 뒤 확장).
            condition_check=len(state.get("task_plan") or []) == 1,
        )
        try:
            out = await output_generator(
                s, llm=llm, app_config=app_config,
                stream_user_response=stream_user_response,
            )
            handler = _handler_disclosures(res)
            base["text"] = _with_handler_notices(_with_answer_lines(
                out.get("final_response", ""), _handler_answer_lines(res)), handler)
            base["output_file"] = out.get("output_file")
            base["output_file_name"] = out.get("output_file_name")
            # task 단위 결정적 고지의 구조화본(plans/123 W-8) — 집계기가 턴 단위로 모은다
            if out.get("disclosures"):
                base["disclosures"] = list(out["disclosures"])
            if handler:
                base["disclosures"] = disc.dedupe([*(base.get("disclosures") or []), *handler])
            # HITL 폼필(D-151): 역질문 페이로드·대기 상태를 최종 응답까지 운반.
            # pending_form_fill은 None(해소·자기정리)도 유의미한 델타이므로 키 존재로 판별.
            if "form_fill_clarification" in out:
                base["form_fill_clarification"] = out["form_fill_clarification"]
            if "pending_form_fill" in out:
                base["pending_form_fill"] = out["pending_form_fill"]
        except Exception as e:
            # exc_info 필수 — 라이브에서 "'NoneType' object has no attribute 'get'"만
            # 남아 발생 지점을 특정할 수 없었다(2026-07-30 B0 CPU 양식 실측).
            logger.error(
                "result_aggregator output_generator 실패 (task=%s): %s",
                task.get("task_id"), e, exc_info=True,
            )
            base["text"] = f"결과 생성 중 오류가 발생했습니다: {e}"
            base["error"] = base["error"] or str(e)
        return base

    # 텍스트 계열: final_response 직접 사용
    text = res.get("final_response")
    handler = _handler_disclosures(res)
    if text:
        base["text"] = _with_handler_notices(
            _with_answer_lines(text, _handler_answer_lines(res)), handler)
    elif res.get("error"):
        base["text"] = f"작업 처리 중 오류가 발생했습니다: {res['error']}"
    else:
        base["text"] = "처리 결과가 없습니다."
    # SQL 루프가 사유 문구로 끝낸 조회(산문 조기 종결 · 조회 마감 — plans/119 N-5·T-3)도 kind를
    # 남긴다(plans/123 W-8). 본문은 그 사유 문구 그대로다.
    if failure is not None and res.get("regen_stop"):
        base["disclosures"] = [failure]
    if handler:
        base["disclosures"] = disc.dedupe([*(base.get("disclosures") or []), *handler])
    return base


#: 처리기가 결과에 싣는 결정적 판정·집계 줄의 키(plans/134 M-1 — `apm_query.ANSWER_LINES_KEY`와
#: 같은 값). LLM 서술에 맡기지 않고 task 본문 뒤에 그대로 붙인다.
_ANSWER_LINES_KEY = "answer_lines"
_ANSWER_LINES_HEAD = "**판정·집계**"


def _handler_answer_lines(res: dict[str, Any]) -> list[str]:
    lines = res.get(_ANSWER_LINES_KEY)
    return [str(line) for line in lines if str(line).strip()] if isinstance(lines, list) else []


def _with_answer_lines(text: str, lines: list[str]) -> str:
    """본문 뒤에 결정적 줄 블록을 붙인다 — 줄이 없으면 본문 그대로(종전 바이트)."""
    if not lines:
        return text
    block = _ANSWER_LINES_HEAD + "\n" + "\n".join(f"- {line}" for line in lines)
    body = (text or "").rstrip()
    return f"{body}\n\n{block}" if body else block


def _with_handler_notices(text: str, items: list[disc.Disclosure]) -> str:
    """처리기 task 고지 중 의무가 아닌 것을 task 본문 뒤에 싣는다(plans/123 W-9 · 134 W2 검증 B1).

    본문에 아직 없는 것만 우선순위 순으로 최대 `disc.TURN_BODY_MAX_OPTIONAL_LINES`줄 — 나머지는
    구조 필드(`disclosures`)에만 남는다. 의무 고지는 단일 통과점(`_apply_disclosures`)이 본문에
    없을 때 붙인다(종전 그대로). 실을 것이 없으면 본문 그대로(종전 바이트).
    """
    body = text or ""
    pending = [
        d for d in items
        if not getattr(disc.KIND_TABLE.get(d["kind"]), "mandatory", False)
        and _norm_text(d["text"]) not in _norm_text(body)
    ]
    shown = disc.body_lines_for_turn(pending)
    if not shown:
        return text
    block = disc.render_lines(shown)
    head = body.rstrip()
    return f"{head}\n\n{block}" if head else block


def _handler_disclosures(res: dict[str, Any]) -> list[disc.Disclosure]:
    """처리기가 결과에 직접 실은 task 단위 고지(plans/134 W0-B — APM 작업 접수·결과 파일·부분 결과).

    선택 칸 `ref`(작업 참조)를 보존한다. 의무 고지가 본문에 없으면 단일 통과점
    (`_apply_disclosures`)이 되살린다. 없으면 빈 목록(종전 결과와 같다).
    """
    return disc.dedupe(res.get("disclosures") or [])


#: `regen_stop` 사유 → 고지 kind(plans/123 W-6 — 119 계약 어휘 재사용).
_REGEN_STOP_KINDS = {
    disc.FAIL_VALIDATION_BUDGET, disc.FAIL_NON_SQL, disc.FAIL_DEADLINE,
}
#: 읽기 전용 가드가 막은 생성 SQL의 검증 사유 표지(`sql_validation.validate_sql` 2·3번 검사).
_SQL_BLOCKED_RE = re.compile(
    r"금지된 키워드가 포함되어 있습니다"
    r"|감지된 타입:\s*(?:INSERT|UPDATE|DELETE|MERGE|REPLACE|CREATE|ALTER|DROP|TRUNCATE"
    r"|RENAME|GRANT|REVOKE)"
)


def _failure_disclosure(task: dict[str, Any], res: dict[str, Any]) -> disc.Disclosure | None:
    """조회 task 실패의 고지 한 건 — 실패가 아니면 None (plans/123 W-6).

    kind는 `regen_stop.reason`(plans/119 Q-3 계약)을 재사용하고, 읽기 전용 가드 차단이면
    `sql_blocked`, 사유 표지가 없는 실패(실행 오류 · 전 DB 실패)는 `query_failed`다. 인가 거부
    (D-232)는 자체 문구가 있는 정상 종결이라 대상이 아니다.
    """
    error = res.get("error")
    if not error:
        return None
    raw_stop = res.get("regen_stop")
    stop: dict[str, Any] = raw_stop if isinstance(raw_stop, dict) else {}
    detail = str(stop.get("detail") or error)
    if _SQL_BLOCKED_RE.search(f"{error} {detail}"):
        kind = disc.SQL_BLOCKED
    elif str(stop.get("reason") or "") in _REGEN_STOP_KINDS:
        kind = str(stop["reason"])
    else:
        kind = disc.QUERY_FAILED
    text = disc.failure_text(kind, subject=str(task.get("sub_query") or ""), detail=detail)
    return disc.make(kind, text, source=f"task:{task.get('task_id')}")


def _build_output_state(
    state: AgentState,
    task: dict[str, Any],
    res: dict[str, Any],
    *,
    condition_check: bool = False,
    limit_sources: list[dict[str, Any]] | None = None,
    period_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """output_generator 호출용 입력 state를 구성한다.

    output_generator는 organized_data, parsed_requirements, mapping_sources 등을 읽는다.

    Args:
        state: 전체 에이전트 상태
        task: 현재 TaskSpec
        res: 해당 task 결과
        condition_check: 조건 반영 대조(plans/123 S-11 1차)를 켤지 — 단일 task 계획만
        limit_sources: 병합 경로의 원천 task별 상한 판정 입력(plans/123 W-1 ③)
        period_sources: 병합 경로의 원천 task별 조회 기간 고지 입력(plans/122 T-8)

    Returns:
        output_generator 입력 state dict
    """
    # per-task 최종화 스코프(D-092): output_generator의 응답 프롬프트는
    # parsed_requirements["original_query"]를 "사용자 질의"로 사용한다. 전체 질의를
    # 그대로 두면 하위 task 결과(예: 알람 서버 목록)만으로 전체 질문(예: CPU 최고
    # 서버의 제조사·일련번호)에 답한 듯 서술하는 환각이 생긴다 — sub_query로 좁힌다.
    sub_query = task.get("sub_query") or state.get("user_query", "")
    parsed = dict(state.get("parsed_requirements", {}) or {})
    if sub_query:
        parsed["original_query"] = sub_query
    return {
        "user_query": sub_query,
        "parsed_requirements": parsed,
        "organized_data": res.get("organized_data"),
        "query_results": res.get("query_results", []),
        # 존 커버리지 각주(D-159 계열 침묵 강등 금지) — 부분 실패·0행 존 명시용
        "db_errors": res.get("db_errors"),
        "db_result_summary": res.get("db_result_summary"),
        # 0건 원인 진단(plans/121 TP-11.5) — 없으면 None이라 빈 결과 문구가 종전과 바이트 동일하다.
        "empty_diagnosis": res.get("empty_diagnosis"),
        "template_structure": state.get("template_structure"),
        # uploaded_file(원본 파일 바이너리)이 없으면 output_generator가 양식을 채우지 못하고
        # CSV로만 강등된다(비대칭 전파 방지, D-053 계열).
        "uploaded_file": state.get("uploaded_file"),
        "target_sheets": state.get("target_sheets"),
        "file_type": state.get("file_type"),
        "mapping_sources": state.get("mapping_sources"),
        "column_mapping": state.get("column_mapping"),
        "db_column_mapping": state.get("db_column_mapping"),
        "llm_inference_details": state.get("llm_inference_details"),
        # 폼필 산출물(D-146/D-151): 파이프라인 res 우선(top-level state는 orchestration
        # 경로에서 이 키들을 받지 못함 — run_data_query_pipeline이 res로 승격).
        "form_month_anchor": res.get("form_month_anchor") or state.get("form_month_anchor"),
        "form_fill_candidates": res.get("form_fill_candidates"),
        "form_fill_overrides": res.get("form_fill_overrides"),
        "form_fill_literals": res.get("form_fill_literals"),
        "form_fill_answers": state.get("form_fill_answers"),
        "form_fill_remember": state.get("form_fill_remember"),
        "pending_form_fill": state.get("pending_form_fill"),
        # 존 보존(D-157/FIX-26 → D-186 실효화): `_build_form_fill_hitl`이 pending.db_ids를
        # selected_db_ids > target_databases > db_results 순으로 읽는데, 이 dict에는 셋 다
        # 없어 오케스트레이션(존 체크박스 런의 유일한 경로)에서 항상 None → 답변 턴이
        # 기본 DB(b0=은행존)로 침묵 전환됐다(라이브 실측 2026-08-25). 체크박스 선택과
        # 이 task가 실제 실행한 DB(subagents가 res로 승격)를 함께 싣는다.
        "selected_db_ids": state.get("selected_db_ids"),
        "target_databases": (
            [{"db_id": d} for d in (res.get("target_db_ids") or []) if d]
            or state.get("target_databases")
        ),
        # ── 2단 결정적 고지 입력(plans/123 W-1·W-3) ──────────────────────────────
        # 종전 허용목록에 없어 상한 절단·급증 한계·알람 헤드라인·당월 각주 알람 제외가 2단에서
        # no-op였다(10,000행 도달 17턴 고지 0 — run `20260923-103638`). 없으면 각 덧붙임이
        # no-op이다.
        # 실행 SQL은 task가 실제로 실행한 DB별 마지막 1건이다(`resolved_limit`은 넘기지 않는다 —
        # 격리 입력은 늘 값이 있어 「요청 상한」과 「실제 적용 상한」을 가르지 못한다 · W-1 ⑥).
        "executed_sqls": res.get("executed_sqls") or [],
        "query_attempts": [
            {"sql": e.get("sql")} for e in (res.get("executed_sqls") or [])
            if isinstance(e, dict) and e.get("sql")
        ],
        "spike_notes": res.get("spike_notes"),
        "routing_intent": "alarm_query" if task.get("agent") == "alarm_query" else None,
        # 전 행 null 강등(C-06)은 SQL 조회 결과용이다 — APM 처리기 행은 원천이 늘 비우는 칸(계정
        # 행의 email·phone 등)이 있어 끈다(plans/134 V-7). 없으면 종전대로 적용한다.
        "all_null_downgrade": task.get("agent") != APM_QUERY_AGENT,
        # 턴 원문 — 명시 건수 판정(W-1 ②)·「전체」 판정의 입력(task 질의는 재작성될 수 있다)
        "original_user_query": state.get("user_query", ""),
        # task 마감 표지 — 턴 단위 고지는 집계기가 턴당 한 번 붙인다(W-9 · 단계마다 반복 금지)
        "per_task_finalize": True,
        "task_id": task.get("task_id"),
        # 조건 반영 대조(S-11) 1차는 단일 task 계획만 — 집계기가 정한다
        "condition_check": condition_check,
        # 병합 경로의 원천 task별 상한 판정 입력(W-1 ③) — 병합 표 행 수는 표시용이라 쓰지 않는다
        "limit_sources": limit_sources,
        # ── 시간 해석(plans/122 T-4·T-8 · D-309) ─────────────────────────────────────
        # 허용목록이라 싣지 않으면 2단 출구에서 사라진다(D-186 사례) — 기준 정보(오늘·조회 기간)와
        # `[조회 기간]` 고지가 이 값과 위 실행 SQL로 정해진다. task 문장에 명시 기간이 있어 task별로
        # 해석했으면(`resolve_task_time`) task 결과의 값이 우선이다. 플래그 off면 둘 다 None.
        "time_resolution": res.get("time_resolution") or state.get("time_resolution"),
        # 병합 경로의 원천 task별 기간 고지 입력 — 원천마다 해석이 다를 수 있다
        "period_sources": period_sources,
        "final_response": "",
        "output_file": None,
        "output_file_name": None,
    }


def _merge_finalized(finalized: list[dict]) -> dict:
    """복합 task 결과를 order 순으로 묶어 통합 응답을 생성한다 (D-005 부분 실패 안내).

    답변 본문에는 내부 task 라벨("작업 N (agent)")을 노출하지 않고 각 결과 텍스트만
    자연스럽게 이어붙인다. task 구성·개수·재계획 이력은 처리 현황 패널(SSE)에서 보여준다.
    부분 실패가 있으면 본문 말미에 사용자용 안내(내부 agent명 미노출)를 덧붙인다.

    Args:
        finalized: _finalize_task 결과 목록

    Returns:
        통합 final_response(+선택적 output_file)를 포함한 State 갱신 dict
    """
    parts: list[str] = []
    failed: list[str] = []
    output_file: Optional[bytes] = None
    output_file_name: Optional[str] = None
    merged_rows: list[dict] = []
    period_seen: set[str] = set()

    for i, f in enumerate(finalized, 1):
        # 같은 `[조회 기간]` 줄은 처음 단계에만 남긴다(plans/122 T-8 — 기간이 다르면 모두 남는다)
        text = _drop_repeated_period_lines(f.get("text", ""), period_seen)
        if f.get("error"):
            failed.append(f"- 작업 {i}: {f['error']}")
        if text:
            parts.append(text)
        # output_file이 있는 첫 task의 파일을 우선 채택
        if output_file is None and f.get("output_file") is not None:
            output_file = f["output_file"]
            output_file_name = f.get("output_file_name")
        # CSV 다운로드용 결과 누적(복합 task — 각 task의 행을 순서대로 이어붙임, D-047)
        rows = f.get("query_results")
        if isinstance(rows, list):
            merged_rows.extend(rows)

    body = "\n\n".join(parts)
    if failed:
        body += "\n\n---\n일부 작업이 실패했습니다:\n" + "\n".join(failed)

    result: dict = {
        "final_response": body,
        "current_node": "result_aggregator",
        "query_results": merged_rows,
    }
    if output_file is not None:
        result["output_file"] = output_file
        result["output_file_name"] = output_file_name
    disclosures = _collect_task_disclosures(finalized)
    if disclosures:
        result["disclosures"] = disclosures  # plans/123 W-8
    return result


async def _finalize_steps(
    ordered_tasks: list[dict[str, Any]],
    task_results: dict[str, dict[str, Any]],
    state: AgentState,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict[str, Any]:
    """병합이 성립하지 않는 복합 턴 — 단계(task)별 결과를 순서대로 잇는다(plans/121 TP-4.5 · G-7 ①).

    합성 LLM을 부르지 않는다(D-062 의미 개정). 각 단계는 `_finalize_task` 산출(코드 표 + 짧은
    요약 · 0건 사유 · 오류 안내 · 텍스트 담당 응답)을 그대로 쓰고, 단계 머리말 없이 빈 줄로 잇는다
    — `_merge_finalized`(D-005)와 같은 모양이다(첫 파일 · 실패 안내 · CSV 행 누적 포함). 합성
    서술 상한 폴백(T-4 · `_merge_with_synthesis_skipped`)과 같은 본문에서 생략 안내만 없다.

    스트리밍(D-268 ① 표 먼저 · `done` 본문과 같은 순서):
    - **마지막 단계**는 `output_generator`가 표를 먼저 내고 요약을 토큰으로 흘린다(단일 task와
      같다).
    - **앞 단계들**은 스트림 없이 마감한 뒤 마지막 단계 직전에 **한 번에** 답변 선행 본문으로 낸다.
      앞 단계를 요약 토큰으로 흘리면 그 단계의 덧붙임(존별 결과 등 — 스트림에 실리지 않는다)이
      `done` 본문 **중간**에 끼어 교체 때 화면이 튄다. 마지막 단계의 덧붙임은 본문 끝이라 끝에
      붙을 뿐이다. 앞 단계를 하나씩 내지 않는 것은, 그 사이 스트림 없는 요약 LLM(상한 30초) 동안
      답변 토큰이 끊겨 첫 답변 뒤 토큰 간 상한(기본 30초)에 걸릴 수 있어서다.
    """
    *head, last = ordered_tasks
    finalized: list[dict[str, Any]] = []
    notice_seen = False
    period_seen: set[str] = set()
    for task in head:
        step = await _finalize_task(
            task, task_results.get(task["task_id"], {}), state, llm, app_config,
            stream_user_response=False,
        )
        notice_seen = _drop_repeated_turn_notice(step, notice_seen)
        step["text"] = _drop_repeated_period_lines(step.get("text") or "", period_seen)
        finalized.append(step)
    lead = "\n\n".join(f["text"] for f in finalized if f.get("text"))
    if lead:
        # `_merge_finalized`가 잇는 순서·구분자와 같다 — 마지막 단계 본문이 이 뒤에 붙는다.
        await emit_answer_prefix(lead + "\n\n")
    step = await _finalize_task(
        last, task_results.get(last["task_id"], {}), state, llm, app_config,
        stream_user_response=True,
    )
    _drop_repeated_turn_notice(step, notice_seen)
    step["text"] = _drop_repeated_period_lines(step.get("text") or "", period_seen)
    finalized.append(step)
    return _merge_finalized(finalized)


def _drop_repeated_turn_notice(step: dict[str, Any], seen: bool) -> bool:
    """턴 단위 안내가 단계마다 붙은 것을 두 번째 단계부터 뗀다(plans/121 TP-4.5 · 결함 B).

    양식 없는 파일 요청 안내(D-264 ④)는 `parsed_requirements.output_format`(턴 전체 값)으로 정해져
    `output_generator`가 **단계마다** 본문 머리에 붙인다. 합성 모드(D-062)는 LLM이 한 번으로
    합쳤지만 단계별 조립은 그대로 이어 붙여 같은 안내가 단계 수만큼 나왔다. 첫 안내만 남긴다 —
    CSV 안내 문장 유무는 첫 단계의 행 유무를 따른다. 마지막 단계의 안내를 떼면 `done` 본문이 다시
    스트림 본문의 뒤를 잇는다(단계 사이에 안내가 끼지 않는다). 안내가 있었으면 True를 돌려준다.
    """
    text = step.get("text") or ""
    if not text.startswith(NO_TEMPLATE_NOTICE_HEAD):
        return seen
    if seen:
        _, sep, rest = text.partition("\n\n")
        step["text"] = rest if sep else ""
    return True


def _period_lines(text: str) -> set[str]:
    """본문의 `[조회 기간]` 고지 줄들(plans/122 T-8)."""
    return {ln for ln in (text or "").split("\n") if ln.startswith(disc.QUERY_PERIOD_HEAD)}


def _drop_repeated_period_lines(text: str, seen: set[str]) -> str:
    """앞 단계에 이미 실린 `[조회 기간]` 줄을 이 단계 본문에서 뗀다(plans/122 T-8).

    task마다 `output_generator`가 자기 실행 SQL로 기간 고지 줄을 붙인다(task 단위 고지). 단계별
    조립·결정적 병합은 본문을 그대로 이어 붙여, 같은 기간이면 같은 줄이 단계 수만큼 나왔다 —
    처음 나온 줄만 남긴다. task별 해석이 달라 줄이 다르면 모두 남는다. 처음 본 줄은 `seen`에
    더한다. 뗄 것이 없으면 본문 그대로(종전 바이트).
    """
    if disc.QUERY_PERIOD_HEAD not in (text or ""):
        return text
    out = text
    for line in text.split("\n"):
        if not line.startswith(disc.QUERY_PERIOD_HEAD):
            continue
        if line not in seen:
            seen.add(line)
            continue
        for piece in ("\n\n" + line, line + "\n\n", "\n" + line, line + "\n", line):
            if piece in out:
                out = out.replace(piece, "", 1)
                break
    return out


_SYNTHESIS_SKIPPED_NOTE = (
    "**[안내]** 여러 조회 결과를 하나의 답변으로 합치는 서술은 처리 시간 상한으로 생략하고, "
    "조회별 결과를 이어 붙였습니다."
)


def _merge_with_synthesis_skipped(finalized: list[dict[str, Any]]) -> dict[str, Any]:
    """합성(D-062)을 시간 상한으로 건너뛴 결정적 병합 — 사유를 본문 끝에 싣는다(침묵 금지)."""
    merged = _merge_finalized(finalized)
    body = (merged.get("final_response") or "").strip()
    merged["final_response"] = (
        f"{body}\n\n{_SYNTHESIS_SKIPPED_NOTE}" if body else _SYNTHESIS_SKIPPED_NOTE
    )
    return merged


async def _synthesize_finalized(
    finalized: list[dict],
    state: AgentState,
    llm: BaseChatModel | None,
    app_config: AppConfig,
) -> dict:
    """복합 task 결과를 LLM 1회 호출로 단일 일관 답변으로 합성한다 (D-062, 딥 에이전트 경로).

    동일 질문 재시도로 인한 모순(없음→있음)·중복을 deterministic 이어붙이기(_merge_finalized)
    대신 LLM 합성으로 해소한다. 최종 합성만 USER_RESPONSE_TAG로 토큰 스트리밍(D-009)하며,
    per-task 마감의 스트리밍은 호출부(result_aggregator)가 억제한다. 합성이 실패하거나
    빈 결과면 기존 deterministic 병합으로 안전하게 폴백한다.

    Args:
        finalized: _finalize_task 결과 목록 (각 하위 응답 텍스트/파일/오류 포함)
        state: 전체 에이전트 상태 (원본 질의 등)
        llm: LLM 인스턴스 (없으면 내부 생성)
        app_config: 앱 설정

    Returns:
        통합 final_response(+선택적 output_file/query_results)를 포함한 State 갱신 dict
    """
    # 각 하위 응답을 라벨링하여 합성 입력으로 모은다(내부 라벨은 프롬프트가 비노출 지시).
    sections: list[str] = []
    for i, f in enumerate(finalized, 1):
        text = f.get("text", "") or ""
        if f.get("error") and not text:
            text = f"(처리 중 오류: {f['error']})"
        sections.append(f"[조회 {i}]\n{text}")

    # 파일은 첫 산출 task의 것을 채택, query_results는 전체 누적(CSV/row_count — D-047).
    output_file: Optional[bytes] = None
    output_file_name: Optional[str] = None
    merged_rows: list[dict] = []
    for f in finalized:
        if output_file is None and f.get("output_file") is not None:
            output_file = f["output_file"]
            output_file_name = f.get("output_file_name")
        rows = f.get("query_results")
        if isinstance(rows, list):
            merged_rows.extend(rows)

    if llm is None:
        # 최종 사용자 응답 합성 — answer 프로파일(D-194)
        llm = create_llm(app_config, purpose="answer")

    user_prompt = (
        f"## 사용자 원본 질의\n{state.get('user_query', '')}\n\n"
        f"## 수집된 하위 응답\n" + "\n\n".join(sections)
    )
    messages: list[BaseMessage] = [
        SystemMessage(content=RESULT_SYNTHESIZER_SYSTEM_PROMPT)
    ]
    # KBGenAIChat(FabriX)은 system 다음 AIMessage 빈 턴을 요구한다(output_generator와 동일 규약).
    if type(llm) is KBGenAIChat:
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user_prompt))

    # plans/119 T-4: 합성도 요약과 같은 서술 상한 — min(서술 상한, 처리 마감까지 남은 시간).
    # 마감이 없으면 종전(상한 없음). 넘으면 조회별 결과(각각 표 + 요약)를 이어 붙이고 사유를 싣는다.
    limit = narration_limit_sec()
    if limit is not None and limit < MIN_CALL_TIMEOUT_SEC:
        logger.warning("result_aggregator 합성 생략 — 처리 마감까지 %.1fs · 이어붙이기", limit)
        return _merge_with_synthesis_skipped(finalized)
    try:
        call = astream_text(llm, messages, tags=[USER_RESPONSE_TAG])
        body = await (call if limit is None else asyncio.wait_for(call, timeout=limit))
    except TimeoutError:
        logger.warning(
            "result_aggregator 합성이 서술 상한 %.1fs를 넘어 이어붙이기로 폴백", limit or 0.0
        )
        return _merge_with_synthesis_skipped(finalized)
    except Exception as e:  # noqa: BLE001 — 합성 실패는 deterministic 병합으로 폴백
        logger.error("result_aggregator 단일 합성 실패 → 이어붙이기 폴백: %s", e)
        return _merge_finalized(finalized)

    if not body.strip():
        # 빈 합성 결과 방어: deterministic 병합으로 폴백
        logger.warning("result_aggregator 합성 결과가 비어 있어 이어붙이기로 폴백")
        return _merge_finalized(finalized)

    result: dict = {
        "final_response": body,
        "current_node": "result_aggregator",
        "query_results": merged_rows,
    }
    if output_file is not None:
        result["output_file"] = output_file
        result["output_file_name"] = output_file_name
    # task 단위 고지는 합성 LLM이 떨어뜨릴 수 있다 — 구조화본을 싣고, 본문에 없는 의무 고지는 단일
    # 통과점(`_apply_disclosures`)이 되살린다(plans/123 W-9).
    disclosures = _collect_task_disclosures(finalized)
    if disclosures:
        result["disclosures"] = disclosures
    return result
