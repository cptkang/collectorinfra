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

import logging
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.llm import USER_RESPONSE_TAG, astream_text, create_llm
from src.nodes.output_generator import output_generator
from src.prompts.result_synthesizer import RESULT_SYNTHESIZER_SYSTEM_PROMPT
from src.state import AgentState

logger = logging.getLogger(__name__)


async def result_aggregator(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
    synthesize: bool = False,
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

    # 합성 모드 + 복합 task일 때만 per-task 마감의 토큰 스트리밍을 억제한다.
    # (최종 합성 1회에만 USER_RESPONSE_TAG를 부여하여 중간 답변 토큰 누출 방지 — D-062/D-009)
    suppress_stream = synthesize and len(ordered_tasks) > 1

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

    # 복합 task + 합성 모드(딥 에이전트): 먼저 공통 서버 키로 결정적 병합을 시도한다(D-100).
    # 여러 하위 조회(알람 선별·지표·설정)가 같은 서버 식별 컬럼을 공유하면, 질의에 언급된
    # 모든 항목(알람명·심각도·CPU평균·제조사·일련번호 등)을 한 표로 합쳐 노출한다.
    # 공통 키가 없으면(도메인 이질) LLM 1회 합성으로 폴백한다(D-062).
    if synthesize and len(finalized) > 1:
        merged_rows = _merge_task_results_by_identity(ordered_tasks, task_results)
        if merged_rows:
            out = await _finalize_merged_rows(merged_rows, state, llm, app_config)
            return _with_answer_history(_apply_incomplete_notice(
                {**out, **db_promotion}, state,
            ))
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
        # HITL 폼필(D-118): 역질문 페이로드는 API 응답으로, pending은 체크포인터 state로.
        # 폼필은 D-117 단일 task 고정이라 이 단일 분기로 충분하다.
        if "form_fill_clarification" in f:
            out["form_fill_clarification"] = f["form_fill_clarification"]
        if "pending_form_fill" in f:
            out["pending_form_fill"] = f["pending_form_fill"]
        out.update(db_promotion)
        return _with_answer_history(_apply_incomplete_notice(out, state))

    # 복합 task: order 순으로 묶어 통합
    return _with_answer_history(
        _apply_incomplete_notice({**_merge_finalized(finalized), **db_promotion}, state)
    )


# 서버 식별 컬럼 후보(병합 키 탐지 우선순위). server_name을 canonical로 선호한다.
_IDENTITY_COL_HINTS = ("server_name", "hostname", "host_name", "서버명", "name")


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

    id_cols = {idc for _, idc in sources}
    canonical = "server_name" if "server_name" in id_cols else sources[0][1]

    merged: dict[str, dict] = {}
    col_order: list[str] = [canonical]
    dropped = 0
    for rows, idc in sources:
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_key = row.get(idc)
            key = str(raw_key).strip().lower() if raw_key is not None else ""
            if not key:
                continue
            slot = merged.setdefault(key, {canonical: raw_key})
            for col, val in row.items():
                if col in id_cols:  # 모든 식별 컬럼은 canonical로 흡수(중복 제거)
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
    base_rows, base_idc = min(sources, key=lambda s: len(s[0]))
    base_keys = {
        str(r.get(base_idc)).strip().lower()
        for r in base_rows
        if isinstance(r, dict) and r.get(base_idc) is not None
    }
    scoped = {k: v for k, v in merged.items() if k in base_keys}
    if scoped:
        merged = scoped

    if dropped:
        logger.info(
            "result_aggregator 병합: 서버당 다건으로 %d개 값이 대표행에 흡수되지 않음(대표 유지)",
            dropped,
        )
    return [{c: slot.get(c) for c in col_order} for slot in merged.values()]


async def _finalize_merged_rows(
    merged_rows: list[dict],
    state: AgentState,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict:
    """병합된 통합 행을 단일 output_generator로 최종 표/자연어 응답으로 만든다(D-100).

    Args:
        merged_rows: _merge_task_results_by_identity 산출 통합 행
        state: 전체 에이전트 상태(원본 질의)
        llm: LLM 인스턴스
        app_config: 앱 설정

    Returns:
        final_response/query_results/current_node를 포함한 State 갱신 dict
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
        state, merge_task, {"organized_data": organized, "query_results": merged_rows}
    )
    # _build_output_state가 original_query를 sub_query(=전체 질의)로 세팅 — 그대로 사용.
    try:
        out = await output_generator(out_state, llm=llm, app_config=app_config)
        text = out.get("final_response", "")
    except Exception as e:  # noqa: BLE001 — 표 생성 실패는 로그 후 최소 안내
        logger.error("result_aggregator 병합 표 생성 실패: %s", e)
        text = f"통합 결과 {len(merged_rows)}건을 정리하는 중 오류가 발생했습니다: {e}"
    return {
        "final_response": text,
        "current_node": "result_aggregator",
        "query_results": merged_rows,
    }


def _apply_incomplete_notice(result: dict, state: AgentState) -> dict:
    """오케스트레이션 조기 종료 안내문을 최종 응답 말미에 덧붙인다 (D-092).

    deep_agent가 오케스트레이터 루프의 조기 종료(빈 AI 응답)를 감지하면 수행된 조회와
    미실행 작업 안내문을 state["orchestration_incomplete_notice"]로 전달한다. LLM 합성
    결과에 의존하지 않고 결정적으로 덧붙여, 일부 하위 작업만 수행된 부분 결과가 완전한
    답처럼 보이는 것을 차단한다(침묵적 강등 금지). 안내문이 없으면 원본 그대로 반환한다.

    Args:
        result: result_aggregator가 반환할 State 갱신 dict
        state: 현재 에이전트 상태 (안내문 보유 여부 확인용)

    Returns:
        안내문이 덧붙은 dict (안내문 없으면 원본 그대로)
    """
    notice = (state.get("orchestration_incomplete_notice") or "").strip()
    if not notice:
        return result
    body = (result.get("final_response") or "").strip()
    out = dict(result)
    out["final_response"] = f"{body}\n\n---\n{notice}" if body else notice
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
    for t in tasks:
        res = task_results.get(t.get("task_id"), {})
        for did in res.get("target_db_ids") or []:
            if did and did not in db_ids:
                db_ids.append(did)
    if not db_ids:
        return {}
    return {
        "active_db_id": db_ids[0],
        "target_databases": [{"db_id": d} for d in db_ids],
    }


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

    # data 계열: organized_data가 있으면 output_generator로 최종화
    organized = res.get("organized_data")
    if organized is not None:
        # 결정적 subagent(process_query)는 빈 결과에도 **원인 진단 summary**를 제공한다
        # (서버 식별 실패·API 미연결·API 미응답·0건 등). output_generator의 일반
        # "조건에 해당하는 …데이터가 없습니다" 문구로 덮어쓰면 실제 원인이 사라지므로,
        # 빈 결과(rows=[]) + summary가 있으면 그 summary를 그대로 노출한다(Plan 50 M4 / D-046).
        if (
            agent == "process_query"
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
            # HITL 폼필(D-118): 역질문 페이로드·대기 상태를 최종 응답까지 운반.
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
        # 폼필 산출물(D-113/D-118): 파이프라인 res 우선(top-level state는 orchestration
        # 경로에서 이 키들을 받지 못함 — run_data_query_pipeline이 res로 승격).
        "form_month_anchor": res.get("form_month_anchor") or state.get("form_month_anchor"),
        "form_fill_candidates": res.get("form_fill_candidates"),
        "form_fill_overrides": res.get("form_fill_overrides"),
        "form_fill_literals": res.get("form_fill_literals"),
        "form_fill_answers": state.get("form_fill_answers"),
        "pending_form_fill": state.get("pending_form_fill"),
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
        llm = create_llm(app_config)

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

    try:
        body = await astream_text(llm, messages, tags=[USER_RESPONSE_TAG])
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
