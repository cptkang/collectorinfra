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

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.nodes.output_generator import output_generator
from src.state import AgentState

logger = logging.getLogger(__name__)


async def result_aggregator(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """task_results를 통합하여 최종 응답을 생성한다.

    Args:
        state: 현재 에이전트 상태 (task_plan, task_results 포함)
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

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

    # 각 task 결과를 최종화 (텍스트 응답 + 선택적 output_file)
    finalized: list[dict] = []
    for task in ordered_tasks:
        tid = task["task_id"]
        res = task_results.get(tid, {})
        finalized.append(await _finalize_task(task, res, state, llm, app_config))

    # 단일 task: 그대로 최종화
    if len(finalized) == 1:
        f = finalized[0]
        out: dict = {
            "final_response": f["text"],
            "current_node": "result_aggregator",
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
    }

    # data 계열: organized_data가 있으면 output_generator로 최종화
    organized = res.get("organized_data")
    if organized is not None:
        s = _build_output_state(state, task, res)
        try:
            out = await output_generator(s, llm=llm, app_config=app_config)
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

    body = "\n\n".join(parts)
    if failed:
        body += "\n\n---\n일부 작업이 실패했습니다:\n" + "\n".join(failed)

    result: dict = {
        "final_response": body,
        "current_node": "result_aggregator",
    }
    if output_file is not None:
        result["output_file"] = output_file
        result["output_file_name"] = output_file_name
    return result
