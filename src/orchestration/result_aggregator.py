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
from src.llm import USER_RESPONSE_TAG, astream_text, create_llm
from src.nodes.output_generator import (
    NO_TEMPLATE_NOTICE_HEAD,
    narration_limit_sec,
    output_generator,
)
from src.orchestration.host_inspect import HOST_INSPECT_AGENT
from src.prompts.result_synthesizer import RESULT_SYNTHESIZER_SYSTEM_PROMPT
from src.state import AgentState
from src.utils.deadline import MIN_CALL_TIMEOUT_SEC
from src.utils.prior_dependency import render_dependency_notes
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
            merged_out = await _finalize_merged_path(
                merged_rows, ordered_tasks, task_results, state, llm, app_config,
            )
            return _with_answer_history(_apply_incomplete_notice(
                {**merged_out, **db_promotion}, state,
            ))
        # 병합 불성립 + 2단(TP-4.5): 합성 LLM 없이 단계별 결과를 순서대로 잇는다.
        if step_answers:
            steps_out = await _finalize_steps(ordered_tasks, task_results, state, llm, app_config)
            return _with_answer_history(_apply_incomplete_notice(
                {**steps_out, **db_promotion}, state,
            ))

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
        return _with_answer_history(_apply_incomplete_notice(
            {**await _synthesize_finalized(finalized, state, llm, app_config), **db_promotion},
            state,
        ))

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
        out.update(db_promotion)
        return _with_answer_history(_apply_incomplete_notice(out, state))

    # 복합 task: order 순으로 묶어 통합
    return _with_answer_history(
        _apply_incomplete_notice({**_merge_finalized(finalized), **db_promotion}, state)
    )


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


def _extract_result_rows(res: dict) -> list[dict]:
    """task 결과에서 행 리스트를 추출한다(organized_data.rows 우선, query_results 폴백)."""
    organized = res.get("organized_data") or {}
    rows = organized.get("rows")
    if rows is None:
        rows = res.get("query_results")
    return rows if isinstance(rows, list) else []


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
    ordered_tasks: list[dict], task_results: dict[str, dict]
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

    카디널리티: 서버 키당 1행(대표)으로 접는다 — 한 조회가 서버당 여러 행(예: 서버당 알람
    다건)이면 먼저 채워진 값을 유지하고 로그로 알린다(침묵 절단 방지). 컬럼 순서는 조회·행
    등장 순서를 보존하며, 모든 식별 컬럼은 canonical 컬럼 하나로 흡수한다(name/server_name 중복 제거).

    Args:
        ordered_tasks: order 순으로 정렬된 task 목록
        task_results: {task_id: 정규화된 결과}

    Returns:
        병합된 행 목록(canonical 식별 컬럼 우선). 병합 불가 시 None.
    """
    sources: list[tuple[list[dict], str]] = []
    for t in ordered_tasks:
        rows = _extract_result_rows(task_results.get(t.get("task_id"), {}))
        if not rows:
            continue
        idc = _find_identity_col(rows)
        if idc is None:
            return None  # 식별 컬럼 없는 조회가 있으면 결정적 병합 불가 → 합성 폴백
        sources.append((rows, idc))
    if len(sources) < 2:
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
    covered = set().union(*(s for i, s in enumerate(key_sets) if i != base_idx))
    scoped = {k: v for k, v in merged.items() if k in base_keys} if base_keys <= covered else {}
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
    outside: list[dict[str, Any]] = []
    for task in ordered_tasks:
        res = task_results.get(task["task_id"], {})
        if _extract_result_rows(res):
            source_results.append(res)
        else:
            outside.append(task)
    out = await _finalize_merged_rows(
        merged_rows, state, llm, app_config,
        zone_state=_merged_zone_state(merged_rows, source_results),
    )
    notes: list[str] = []
    for task in outside:
        f = await _finalize_task(
            task, task_results.get(task["task_id"], {}), state, llm, app_config,
            stream_user_response=False,
        )
        if f.get("text"):
            notes.append(f["text"])
        if f.get("output_file") is not None and "output_file" not in out:
            out["output_file"] = f["output_file"]
            out["output_file_name"] = f.get("output_file_name")
    if notes:
        body = (out.get("final_response") or "").strip()
        out["final_response"] = "\n\n".join([body, *notes]) if body else "\n\n".join(notes)
    return out


async def _finalize_merged_rows(
    merged_rows: list[dict],
    state: AgentState,
    llm: BaseChatModel,
    app_config: AppConfig,
    *,
    zone_state: dict[str, Any] | None = None,
) -> dict:
    """병합된 통합 행을 단일 output_generator로 최종 표/자연어 응답으로 만든다(D-100).

    Args:
        merged_rows: _merge_task_results_by_identity 산출 통합 행
        state: 전체 에이전트 상태(원본 질의)
        llm: LLM 인스턴스
        app_config: 앱 설정
        zone_state: 병합 원천의 존 커버리지 재료(`_merged_zone_state` — plans/121 TP-11.6 ②)

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
    merge_task = {"sub_query": state.get("user_query", ""), "agent": "data_query"}
    out_state = _build_output_state(
        state, merge_task,
        {"organized_data": organized, "query_results": merged_rows, **(zone_state or {})},
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
        "query_results": merged_rows,
    }
    # 병합 표로 만든 파일을 버리지 않는다(plans/121 TP-11.6 ③ — 종전에는 텍스트만 돌려줬다).
    if out.get("output_file") is not None:
        result["output_file"] = out["output_file"]
        result["output_file_name"] = out.get("output_file_name")
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
#: 실행 중 노트(`dependency_note`)는 싣지 않는다 — 다음 턴이 계획을 새로 실행한다.
_ZONE_REENTRY_PLAN_FIELDS = (
    "task_id", "agent", "sub_query", "depends_on", "input_from", "order",
    "db_ids", "supersedes", "capability", "spans", "agent_fallback",
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

    # data 계열: organized_data가 있으면 output_generator로 최종화
    organized = res.get("organized_data")
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
        s = _build_output_state(state, task, res)
        try:
            out = await output_generator(
                s, llm=llm, app_config=app_config,
                stream_user_response=stream_user_response,
            )
            base["text"] = out.get("final_response", "")
            base["output_file"] = out.get("output_file")
            base["output_file_name"] = out.get("output_file_name")
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
    if text:
        base["text"] = text
    elif res.get("error"):
        base["text"] = f"작업 처리 중 오류가 발생했습니다: {res['error']}"
    else:
        base["text"] = "처리 결과가 없습니다."
    return base


def _build_output_state(state: AgentState, task: dict, res: dict) -> dict:
    """output_generator 호출용 입력 state를 구성한다.

    output_generator는 organized_data, parsed_requirements, mapping_sources 등을 읽는다.

    Args:
        state: 전체 에이전트 상태
        task: 현재 TaskSpec
        res: 해당 task 결과

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

    for i, f in enumerate(finalized, 1):
        text = f.get("text", "")
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
    for task in head:
        step = await _finalize_task(
            task, task_results.get(task["task_id"], {}), state, llm, app_config,
            stream_user_response=False,
        )
        notice_seen = _drop_repeated_turn_notice(step, notice_seen)
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
    return result
