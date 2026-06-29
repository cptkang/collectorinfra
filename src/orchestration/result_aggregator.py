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
            (D-048)에서만 True — 오케스트레이터가 동일 질문을 재시도(부분 성공→재조회)할
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

    # 합성 모드 + 복합 task일 때만 per-task 마감의 토큰 스트리밍을 억제한다.
    # (최종 합성 1회에만 USER_RESPONSE_TAG를 부여하여 중간 답변 토큰 누출 방지 — D-048/D-009)
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

    # 복합 task + 합성 모드(딥 에이전트): LLM 1회로 단일 일관 답변 합성 (D-048)
    if synthesize and len(finalized) > 1:
        return await _synthesize_finalized(finalized, state, llm, app_config)

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
        return out

    # 복합 task: order 순으로 묶어 통합
    return _merge_finalized(finalized)


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
            합성 모드(D-048)에서 중간 per-task 토큰 누출을 막기 위해 False로 전달.

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
        except Exception as e:
            logger.error("result_aggregator output_generator 실패 (task=%s): %s", task.get("task_id"), e)
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
    return {
        "user_query": task.get("sub_query", state.get("user_query", "")),
        "parsed_requirements": state.get("parsed_requirements", {}),
        "organized_data": res.get("organized_data"),
        "query_results": res.get("query_results", []),
        "template_structure": state.get("template_structure"),
        "target_sheets": state.get("target_sheets"),
        "file_type": state.get("file_type"),
        "mapping_sources": state.get("mapping_sources"),
        "column_mapping": state.get("column_mapping"),
        "db_column_mapping": state.get("db_column_mapping"),
        "llm_inference_details": state.get("llm_inference_details"),
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
    """복합 task 결과를 LLM 1회 호출로 단일 일관 답변으로 합성한다 (D-048, 딥 에이전트 경로).

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
