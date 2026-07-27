"""질의 관련 라우트.

자연어 질의를 처리하고 결과를 반환하는 엔드포인트를 제공한다.
SSE 스트리밍 응답도 지원한다.
멀티턴 대화와 Human-in-the-loop(SQL 승인)을 지원한다.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from src.api.dependencies import require_user
from src.api.schemas import ErrorResponse, QueryRequest, QueryResponse
from src.llm import USER_RESPONSE_TAG
from src.state import create_followup_input, create_initial_state
from src.utils.query_gen_common import is_full_scan_query, resolve_query_limit

# 폼필(파일 업로드) 기본 LIMIT — 전량 채움이 기본(_ALL_QUERY_LIMIT와 동일 값).
# 실행 상한은 db 클라이언트 max_rows(10,000)가 안전망(D-066 후속7 계열).
_FORM_FILL_DEFAULT_LIMIT = 100_000

logger = logging.getLogger(__name__)
router = APIRouter()

# 비동기 결과 저장소
_MAX_RESULTS_STORE_SIZE = 1000
_results_store: OrderedDict[str, dict] = OrderedDict()


def _store_result(query_id: str, data: dict) -> None:
    """결과를 저장하고, 최대 크기를 초과하면 오래된 항목을 제거한다."""
    _results_store[query_id] = data
    while len(_results_store) > _MAX_RESULTS_STORE_SIZE:
        _results_store.popitem(last=False)


def _sse_event(data: dict) -> str:
    """SSE 이벤트 문자열을 생성한다."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _get_checkpoint_state(graph, thread_config: dict) -> dict | None:
    """체크포인트에서 이전 State를 조회한다.

    Args:
        graph: 컴파일된 LangGraph 그래프
        thread_config: thread_id가 포함된 설정

    Returns:
        이전 State 딕셔너리 또는 None (체크포인트 없음)
    """
    try:
        state_snapshot = await asyncio.to_thread(
            graph.get_state, thread_config
        )
        if state_snapshot and state_snapshot.values:
            return state_snapshot.values
    except Exception as e:
        logger.debug("체크포인트 조회 실패 (첫 턴으로 진행): %s", e)
    return None


def _parse_approval(query: str) -> tuple[str, str]:
    """사용자 입력에서 승인 의도를 파싱한다.

    Args:
        query: 사용자 입력

    Returns:
        (action, modified_sql) 튜플
        - action: "approve" | "reject" | "modify"
        - modified_sql: modify 시 수정된 SQL
    """
    q = query.strip().lower()

    # 승인 패턴
    approve_patterns = ["실행", "approve", "승인", "확인", "네", "yes", "ㅇㅇ", "ok"]
    for p in approve_patterns:
        if q == p or q.startswith(p):
            return ("approve", "")

    # 거부 패턴
    reject_patterns = ["취소", "reject", "거부", "아니", "no", "cancel"]
    for p in reject_patterns:
        if q == p or q.startswith(p):
            return ("reject", "")

    # SQL이 포함된 경우 modify로 판단
    if re.search(r"\bSELECT\b", query, re.IGNORECASE):
        return ("modify", query.strip())

    # 기본: 승인
    return ("approve", "")


def _count_human_messages(messages: list) -> int:
    """메시지 목록에서 HumanMessage 수를 반환한다."""
    return len([m for m in messages if isinstance(m, HumanMessage)])


def _summarize_tasks(tasks: list[dict], results: dict | None = None) -> list[dict]:
    """task_plan을 처리 현황 표시용 경량 항목으로 요약한다 (order 정렬, 표시 필드만).

    results(task_results)가 주어지면 task별 생성 SQL·대상 DB·행수·DB 에러를 함께
    포함한다. orchestration 경로에서는 query_generator가 그래프 노드가 아니어서
    생성 SQL이 따로 노출되지 않으므로, 어떤 SQL이 어느 DB로 실행됐는지 보이게 한다.
    """
    ordered = sorted(tasks, key=lambda t: t.get("order", 0))
    summarized: list[dict] = []
    for i, t in enumerate(ordered):
        item: dict = {
            "order": t.get("order", i + 1),
            "agent": t.get("agent", ""),
            "sub_query": t.get("sub_query", ""),
            "status": t.get("status", "pending"),
        }
        res = results.get(t.get("task_id")) if isinstance(results, dict) else None
        if isinstance(res, dict):
            # 실행 경로 표식(Plan 66): realtime_api면 처리 현황 라벨을 "실시간 API 조회"로
            # 표시한다 — data_query 고정 라벨("DB 조회")이 실제 경로와 다르게 보이는 문제.
            if res.get("source"):
                item["source"] = res.get("source")
            sql = res.get("generated_sql")
            if sql:
                item["generated_sql"] = sql
            db_ids = res.get("target_db_ids")
            if db_ids:
                item["target_db_ids"] = db_ids
            rows = res.get("query_results")
            if isinstance(rows, list):
                item["row_count"] = len(rows)
            db_errors = res.get("db_errors")
            if db_errors:
                item["db_errors"] = db_errors
            elif res.get("error"):
                item["error"] = res.get("error")
        summarized.append(item)
    return summarized


def _extract_node_progress(node_name: str, output: dict) -> dict | None:
    """노드 완료 시 오른쪽 패널에 표시할 진행 데이터를 추출한다."""
    try:
        if node_name == "input_parser":
            parsed = output.get("parsed_requirements", {})
            template = output.get("template_structure")
            data: dict = {}
            if parsed:
                data["parsed_requirements"] = parsed
            if template:
                data["template_structure"] = template
            return data if data else None

        elif node_name == "schema_analyzer":
            tables = output.get("relevant_tables", [])
            data = {}
            cache_source = output.get("schema_cache_source")
            if cache_source:
                data["cache_source"] = cache_source
            if tables:
                data["relevant_tables"] = tables
            return data if data else None

        elif node_name == "query_generator":
            sql = output.get("generated_sql", "")
            data = {}
            if sql:
                data["generated_sql"] = sql
            usage = output.get("synonym_usage")
            if isinstance(usage, dict) and (
                usage.get("mappings") or usage.get("unregistered")
            ):
                data["synonym_usage"] = usage
            return data if data else None

        elif node_name == "query_validator":
            result = output.get("validation_result", {})
            return {
                "passed": result.get("passed", False),
                "reason": result.get("reason", ""),
            }

        elif node_name == "query_executor":
            results = output.get("query_results", [])
            error = output.get("error_message")
            data = {
                "row_count": len(results),
                "preview_rows": results[:10],
            }
            if error:
                data["error"] = error
            return data

        elif node_name == "result_organizer":
            organized = output.get("organized_data", {})
            data = {}
            if organized:
                data["summary"] = organized.get("summary", "")
                data["is_sufficient"] = organized.get("is_sufficient", False)
                rows = organized.get("rows", [])
                data["row_count"] = len(rows)
                mapping = organized.get("column_mapping")
                if mapping:
                    data["column_mapping"] = mapping
            return data if data else None

        elif node_name == "output_generator":
            return {"status": "응답 생성 완료"}

        elif node_name == "error_response":
            return {"error": output.get("final_response", "")}

        elif node_name == "context_resolver":
            ctx = output.get("conversation_context")
            turn = ctx.get("turn_count", 1) if ctx else 1
            return {"turn": turn}

        elif node_name == "field_mapper":
            if "column_mapping" not in output:
                return {"skipped": True}
            mapping = output.get("column_mapping") or {}
            sources = output.get("mapping_sources") or {}
            has_report = output.get("mapping_report_md") is not None
            data: dict = {
                "mapped_count": sum(1 for v in mapping.values() if v is not None),
                "total_count": len(mapping),
                "has_mapping_report": has_report,
            }
            if sources:
                data["sources"] = {
                    "hint": sum(1 for s in sources.values() if s == "hint"),
                    "synonym": sum(1 for s in sources.values() if s == "synonym"),
                    "eav_synonym": sum(1 for s in sources.values() if s == "eav_synonym"),
                    "llm_inferred": sum(1 for s in sources.values() if s == "llm_inferred"),
                }
            return data

        elif node_name == "semantic_router":
            intent = output.get("routing_intent", "")
            active_db = output.get("active_db_id")
            is_multi = output.get("is_multi_db", False)
            targets = output.get("target_databases", [])
            data = {"routing_intent": intent}
            if active_db:
                data["active_db_id"] = active_db
            if is_multi:
                data["is_multi_db"] = True
            if targets:
                data["targets"] = [
                    {"db_id": t.get("db_id"), "reason": t.get("reason", "")}
                    for t in targets[:3]
                ]
            return data

        elif node_name == "general_inference":
            return {"status": "응답 생성 완료"}

        elif node_name == "approval_gate":
            if output.get("awaiting_approval"):
                return {"awaiting_approval": True, "sql": output.get("approval_context", {}).get("sql", "")}
            return None

        elif node_name == "intent_planner":
            tasks = output.get("task_plan", [])
            if not tasks:
                return None
            return {
                "task_count": len(tasks),
                "is_composite": output.get("is_composite", False),
                "tasks": _summarize_tasks(tasks),
            }

        elif node_name == "agent_orchestrator":
            tasks = output.get("task_plan", [])
            if not tasks:
                return None
            return {
                "task_count": len(tasks),
                "tasks": _summarize_tasks(tasks, results=output.get("task_results")),
            }

        elif node_name == "replanner":
            needs = output.get("needs_replan", False)
            history = output.get("replan_history") or []
            data: dict = {"needs_replan": needs}
            if history:
                data["replan_history"] = history
            if needs:
                data["replan_count"] = output.get("replan_count", 0)
            return data

        elif node_name == "result_aggregator":
            return {"status": "응답 통합 완료"}

    except Exception as e:
        logger.debug(f"노드 진행 데이터 추출 실패 ({node_name}): {e}")
    return None


def _build_turn_input_state(
    body: QueryRequest,
    thread_id: str,
    checkpoint_state: dict | None,
    current_user: dict,
) -> dict:
    """턴 유형(첫/후속/승인)에 따른 그래프 입력 상태를 조립한다 — 텍스트 라우트 단일 출처.

    /query(비스트리밍)와 /query/stream(SSE)이 이 로직을 각자 인라인으로 들고 있다가,
    D-064 폼필 상태 초기화(create_followup_input)가 /query에만 적용되고 SSE 경로에는
    누락되는 비대칭이 발생했다(2026-07-16: 직전 폼업로드 턴의 uploaded_file이 체크포인터로
    복원돼 옛 양식이 재파싱됨). 두 라우트가 반드시 이 헬퍼를 공유하여 재발을 차단한다.
    """
    if checkpoint_state is not None:
        # 후속 턴: delta input만 전달
        if checkpoint_state.get("awaiting_approval"):
            # SQL 승인 대기 중
            action, modified_sql = _parse_approval(body.query)
            return {
                "user_query": body.query,
                "messages": [HumanMessage(content=body.query)],
                "approval_action": action,
                "approval_modified_sql": modified_sql if action == "modify" else None,
            }
        # 일반 후속 질의 — 직전 폼업로드 턴의 요청-스코프 폼필 상태를 초기화한다(D-064).
        # selected_db_ids(존 선택)는 요청 스코프 — 이번 턴 값 또는 None으로 매 턴 재공급.
        return create_followup_input(
            _substitute_zone_placeholder(body.query, body.selected_db_ids),
            selected_db_ids=body.selected_db_ids,
        )
    # 첫 턴: 전체 초기화
    return create_initial_state(
        user_query=_substitute_zone_placeholder(body.query, body.selected_db_ids),
        thread_id=thread_id,
        user_id=current_user.get("sub"),
        user_department=current_user.get("department"),
        allowed_db_ids=current_user.get("allowed_db_ids"),
        selected_db_ids=body.selected_db_ids,
    )


# ── 존 모호성 역질문 (Plan 65 §4 — clarification 배선) ─────────────────────────
# 결정적 게이트(D-035): LLM(clarification_needed) 방출에 의존하지 않는다.
# 발동 = (존 단위 대량 조회 의도 — is_full_scan_query, LIMIT 상향 게이트와 동일 판정 공유)
# AND (존 식별 불가), 또는 "ㅇㅇ존" 리터럴(버튼 프리필 무수정 전송 — 항상 모호).
# 후속 턴은 직전 존 승계가 우선이므로 비발동(§4.2), 단 "ㅇㅇ존" 리터럴은 턴과 무관하게
# 발동. selected_db_ids가 이미 오면 비발동(재개 턴).
_ZONE_PLACEHOLDER = "ㅇㅇ존"
# 선택지 입도는 DB 라우팅 입도와 일치(§4.4 확정 — 체크박스 3개 단독, 단축 버튼 없음).
_ZONE_OPTIONS: tuple[dict, ...] = (
    {"db_id": "polestar_b0", "label": "은행존"},
    {"db_id": "polestar_cm_gp", "label": "공동존 김포"},
    {"db_id": "polestar_cm_yd", "label": "공동존 여의도"},
)


_ZONE_LABEL_BY_ID: dict[str, str] = {o["db_id"]: o["label"] for o in _ZONE_OPTIONS}


def _substitute_zone_placeholder(query: str, selected_db_ids: list[str] | None) -> str:
    """존 선택 재개 턴에서 'ㅇㅇ존' 플레이스홀더를 선택 존 라벨로 치환한다.

    결정적 문자열 치환(LLM 재해석 아님 — 라우팅은 selected_db_ids가 이미 고정).
    치환하지 않으면 sub_query·처리 현황·응답 서술에 'ㅇㅇ존'이 그대로 남는다
    (2026-07-24 폐쇄망 실측: 데이터는 정상인데 화면 표기가 전부 'ㅇㅇ존').
    """
    if not selected_db_ids or _ZONE_PLACEHOLDER not in (query or ""):
        return query
    labels = [_ZONE_LABEL_BY_ID.get(d, d) for d in selected_db_ids]
    return query.replace(_ZONE_PLACEHOLDER, ", ".join(labels))


def _file_zone_clarification_or_none(
    query: str, selected_db_ids: list[str] | None, config
) -> dict | None:
    """파일(폼필) 경로 존 역질문 (Plan 65 §4 확장, 2026-07-24 실측 요구).

    폼필은 본질적으로 존 단위 대량 조회이므로, 텍스트 경로와 달리 "모든/전체" 표면어
    조건 없이 **위치어 미해소면 발동**한다(미발동 시 임의 존(b0 등)으로 오라우팅되는
    실측 사례). has_file=True로 표기해 프론트가 보관한 파일과 함께 재전송하게 한다.
    """
    if selected_db_ids:
        return None  # 선택 재개 턴
    q = query or ""
    if _ZONE_PLACEHOLDER not in q:
        from src.nodes.input_parser import LOCATION_HINT_TERMS
        if any(t in q for t in LOCATION_HINT_TERMS):
            return None  # 위치어 해소 — D-065 결정적 보강이 처리
    active = set(config.multi_db.get_active_db_ids() or [])
    options = [o for o in _ZONE_OPTIONS if not active or o["db_id"] in active]
    if not options:
        return None
    return {
        "kind": "zone_select",
        "question": (
            "양식을 채울 대상 존이 지정되지 않았습니다. 아래에서 대상 존을 선택해 주세요. "
            "(복수 선택 가능 — 전체는 모두 선택)"
        ),
        "options": options,
        "original_query": q,
        "multi": True,
        "has_file": True,
    }


def _parse_selected_db_ids_form(raw: str | None) -> list[str] | None:
    """multipart Form의 selected_db_ids(CSV)를 목록으로 변환한다."""
    if not raw:
        return None
    ids = [s.strip() for s in raw.split(",") if s.strip()]
    return ids or None


def _zone_clarification_or_none(
    body: QueryRequest, checkpoint_state: dict | None, config
) -> dict | None:
    """존 선택 역질문이 필요하면 clarification 컨텍스트를, 아니면 None을 반환한다."""
    if body.selected_db_ids:
        return None  # 선택 재개 턴 — 게이트 통과
    query = body.query or ""
    placeholder = _ZONE_PLACEHOLDER in query
    if not placeholder:
        if checkpoint_state is not None:
            return None  # 후속 턴: previous_entities/DB 승계 우선(§4.2 비발동)
        if not is_full_scan_query(query) or "서버" not in query:
            return None  # 존 단위 대량 조회 의도 아님 — 과잉 역질문 방지
        # 위치 표면어가 하나라도 해소되면 비발동 (D-065 결정적 보강이 처리)
        from src.nodes.input_parser import LOCATION_HINT_TERMS
        if any(t in query for t in LOCATION_HINT_TERMS):
            return None
    # 비활성 DB는 선택지에서 제외
    active = set(config.multi_db.get_active_db_ids() or [])
    options = [o for o in _ZONE_OPTIONS if not active or o["db_id"] in active]
    if not options:
        return None  # 폴스타 전부 비활성 — 기존 폴백 유지
    return {
        "kind": "zone_select",
        "question": (
            "조회할 존이 지정되지 않았습니다. 아래에서 대상 존을 선택해 주세요. "
            "(복수 선택 가능 — 전체 조회는 모두 선택)"
        ),
        "options": options,
        "original_query": query,
        "multi": True,
    }


@router.post(
    "/query",
    response_model=QueryResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def process_query(
    request: Request,
    body: QueryRequest,
    current_user: dict = Depends(require_user),
) -> QueryResponse:
    """자연어 질의를 처리하고 결과를 반환한다.

    멀티턴 대화를 지원한다:
    - thread_id가 없으면 새 UUID 발급 (단일 턴과 동일)
    - thread_id가 있으면 체크포인트에서 이전 State 복원
    - 후속 턴에서는 delta input만 전달 (체크포인트가 나머지 복원)
    """
    query_id = str(uuid.uuid4())
    start_time = time.time()

    graph = request.app.state.graph
    config = request.app.state.config
    thread_id = body.thread_id or query_id

    thread_config = {"configurable": {"thread_id": thread_id}}

    # 체크포인트에서 이전 State 확인
    checkpoint_state = await _get_checkpoint_state(graph, thread_config)

    # Plan 65 §4: 존 모호 시 파이프라인 실행 전에 역질문 반환(결정적 게이트, 서버측 보류 상태 없음)
    clarification = _zone_clarification_or_none(body, checkpoint_state, config)
    if clarification:
        return QueryResponse(
            query_id=query_id,
            status="clarification",
            response=clarification["question"],
            thread_id=thread_id,
            clarification=clarification,
        )

    input_state = _build_turn_input_state(body, thread_id, checkpoint_state, current_user)

    try:
        result = await asyncio.wait_for(
            graph.ainvoke(input_state, thread_config),
            timeout=config.server.query_timeout,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="처리 시간이 초과되었습니다. 질의를 단순화해주세요.",
        )
    except Exception as e:
        logger.error(f"그래프 실행 에러: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"처리 중 오류가 발생했습니다: {str(e)}",
        )

    elapsed_ms = (time.time() - start_time) * 1000

    # 응답 구성
    status = "awaiting_approval" if result.get("awaiting_approval") else "completed"
    turn_count = _count_human_messages(result.get("messages", []))

    response_data = {
        "query_id": query_id,
        "status": status,
        "response": result.get("final_response", ""),
        "thread_id": thread_id,
        "awaiting_approval": result.get("awaiting_approval", False),
        "approval_context": result.get("approval_context"),
        "has_file": result.get("output_file") is not None,
        "file_name": result.get("output_file_name"),
        "executed_sql": result.get("generated_sql"),
        "row_count": len(result.get("query_results", [])),
        "processing_time_ms": elapsed_ms,
        "turn_count": turn_count,
        "has_mapping_report": result.get("mapping_report_md") is not None,
    }
    _store_result(query_id, {
        **response_data,
        "output_file": result.get("output_file"),
        "mapping_report_md": result.get("mapping_report_md"),
        "query_results": result.get("query_results", []),
    })

    return QueryResponse(**response_data)


@router.post(
    "/query/stream",
)
async def process_query_stream(
    request: Request,
    body: QueryRequest,
    current_user: dict = Depends(require_user),
) -> StreamingResponse:
    """SSE 스트리밍 방식으로 질의를 처리한다.

    멀티턴 대화를 지원한다.
    """
    query_id = str(uuid.uuid4())

    graph = request.app.state.graph
    config = request.app.state.config
    thread_id = body.thread_id or query_id

    thread_config = {"configurable": {"thread_id": thread_id}}

    # 체크포인트에서 이전 State 확인
    checkpoint_state = await _get_checkpoint_state(graph, thread_config)

    # Plan 65 §4: 존 모호 시 파이프라인 실행 전에 역질문 반환 — /query와 대칭
    clarification = _zone_clarification_or_none(body, checkpoint_state, config)
    if clarification:
        async def clarification_generator() -> AsyncGenerator[str, None]:
            yield _sse_event({
                "type": "done",
                "response": clarification["question"],
                "query_id": query_id,
                "thread_id": thread_id,
                "clarification": clarification,
            })
        return StreamingResponse(
            clarification_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # /query와 동일 조립(단일 출처) — SSE 경로에만 D-064 초기화가 빠졌던 비대칭 재발 방지
    input_state = _build_turn_input_state(body, thread_id, checkpoint_state, current_user)

    async def event_generator() -> AsyncGenerator[str, None]:
        """SSE 이벤트를 생성하는 비동기 제너레이터."""
        start_time = time.time()
        streamed_any_token = False
        _seen_nodes: set[str] = set()
        _current_node: str | None = None
        _tracked_row_count: int = 0
        _tracked_query_results: list[dict] = []

        try:
            if hasattr(graph, "astream_events"):
                try:
                    # 이벤트 fetch마다 타임아웃을 건다(D-066 후속). 노드 내부 LLM 호출이
                    # 응답 없이 멈추면 astream_events가 다음 이벤트를 영영 못 내놓아 SSE가
                    # 무한 hang된다(healthcheck만 도는 증상). wait_for로 stuck fetch를 끊는다.
                    _event_iter = graph.astream_events(
                        input_state,
                        thread_config,
                        version="v2",
                    ).__aiter__()
                    while True:
                        try:
                            event = await asyncio.wait_for(
                                _event_iter.__anext__(),
                                timeout=config.server.query_timeout,
                            )
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            yield _sse_event({
                                "type": "error",
                                "message": "처리 시간이 초과되었습니다. 질의를 단순화해주세요.",
                            })
                            return
                        kind = event.get("event", "")
                        name = event.get("name", "")

                        # 노드 시작 이벤트 감지
                        if kind == "on_chain_start" and name and name not in _seen_nodes:
                            _known_nodes = {
                                "context_resolver", "input_parser",
                                "semantic_router", "schema_analyzer",
                                "field_mapper",
                                "query_generator", "query_validator",
                                "approval_gate", "query_executor",
                                "result_organizer", "output_generator",
                                "multi_db_executor", "result_merger",
                                "synonym_registrar", "general_inference", "error_response",
                                # Plan 48/49: 다중 의도 오케스트레이션 노드 (처리 현황 표시)
                                "intent_planner", "agent_orchestrator",
                                "replanner", "result_aggregator",
                            }
                            if name in _known_nodes:
                                _seen_nodes.add(name)
                                _current_node = name
                                yield _sse_event({
                                    "type": "node_start",
                                    "node": name,
                                    "timestamp_ms": (time.time() - start_time) * 1000,
                                })

                        # 노드 완료 이벤트
                        if kind == "on_chain_end" and name:
                            node_output = event.get("data", {}).get("output", {})
                            if isinstance(node_output, dict) and name in _seen_nodes:
                                # query_results를 반환하는 노드에서 추적
                                if name in ("query_executor", "multi_db_executor", "result_merger"):
                                    node_qr = node_output.get("query_results")
                                    if isinstance(node_qr, list):
                                        _tracked_row_count = len(node_qr)
                                        _tracked_query_results = node_qr
                                progress_data = _extract_node_progress(name, node_output)
                                if progress_data:
                                    yield _sse_event({
                                        "type": "node_complete",
                                        "node": name,
                                        "data": progress_data,
                                        "timestamp_ms": (time.time() - start_time) * 1000,
                                    })

                        # LLM 토큰 스트리밍 (output_generator, general_inference 노드)
                        if kind == "on_chat_model_stream":
                            # 최종 사용자 응답(USER_RESPONSE_TAG)으로 태깅된 LLM 호출의
                            # 토큰만 전달한다. orchestration 경로에서는 SQL 생성·DB 분류 등
                            # 중간 LLM 호출이 같은 노드(agent_orchestrator)에서 일어나므로
                            # 노드명이 아닌 태그로 구분해야 토큰이 새지 않는다.
                            _tags = event.get("tags", []) or []
                            _event_node = event.get("metadata", {}).get("langgraph_node", _current_node or "")
                            if USER_RESPONSE_TAG in _tags or _event_node in ("output_generator", "general_inference"):
                                chunk = event.get("data", {}).get("chunk")
                                if chunk and hasattr(chunk, "content") and chunk.content:
                                    streamed_any_token = True
                                    yield _sse_event({
                                        "type": "token",
                                        "content": chunk.content,
                                    })

                        elif kind == "on_chain_end":
                            output = event.get("data", {}).get("output", {})
                            if isinstance(output, dict) and "final_response" in output:
                                elapsed_ms = (time.time() - start_time) * 1000

                                if not streamed_any_token:
                                    yield _sse_event({
                                        "type": "token",
                                        "content": output.get("final_response", ""),
                                    })

                                # output_generator 노드 출력에는 query_results가 없으므로
                                # 이전 노드에서 추적한 _tracked_row_count 사용
                                _final_row_count = len(output.get("query_results", [])) or _tracked_row_count

                                yield _sse_event({
                                    "type": "meta",
                                    "executed_sql": output.get("generated_sql"),
                                    "row_count": _final_row_count,
                                })

                                status = "awaiting_approval" if output.get("awaiting_approval") else "completed"
                                turn_count = _count_human_messages(output.get("messages", []))

                                response_data = {
                                    "query_id": query_id,
                                    "status": status,
                                    "response": output.get("final_response", ""),
                                    "thread_id": thread_id,
                                    "has_file": output.get("output_file") is not None,
                                    "file_name": output.get("output_file_name"),
                                    "executed_sql": output.get("generated_sql"),
                                    "row_count": _final_row_count,
                                    "processing_time_ms": elapsed_ms,
                                    "turn_count": turn_count,
                                    "has_mapping_report": output.get("mapping_report_md") is not None,
                                }
                                _store_result(query_id, {
                                    **response_data,
                                    "output_file": output.get("output_file"),
                                    "mapping_report_md": output.get("mapping_report_md"),
                                    "query_results": output.get("query_results") or _tracked_query_results,
                                })

                                yield _sse_event({
                                    "type": "done",
                                    "response": response_data["response"],
                                    "query_id": query_id,
                                    "thread_id": thread_id,
                                    "processing_time_ms": elapsed_ms,
                                    "row_count": response_data["row_count"],
                                    "executed_sql": response_data["executed_sql"],
                                    "has_file": response_data["has_file"],
                                    "file_name": response_data.get("file_name"),
                                    "awaiting_approval": output.get("awaiting_approval", False),
                                    "turn_count": turn_count,
                                    "has_mapping_report": response_data.get("has_mapping_report", False),
                                })
                                return

                    if not streamed_any_token:
                        raise AttributeError("astream_events did not produce output")

                except (AttributeError, TypeError, NotImplementedError):
                    pass

            # Fallback: ainvoke
            result = await asyncio.wait_for(
                graph.ainvoke(input_state, thread_config),
                timeout=config.server.query_timeout,
            )

            elapsed_ms = (time.time() - start_time) * 1000

            final_response = result.get("final_response", "")
            yield _sse_event({"type": "token", "content": final_response})

            yield _sse_event({
                "type": "meta",
                "executed_sql": result.get("generated_sql"),
                "row_count": len(result.get("query_results", [])),
            })

            status = "awaiting_approval" if result.get("awaiting_approval") else "completed"
            turn_count = _count_human_messages(result.get("messages", []))

            response_data = {
                "query_id": query_id,
                "status": status,
                "response": final_response,
                "thread_id": thread_id,
                "has_file": result.get("output_file") is not None,
                "file_name": result.get("output_file_name"),
                "executed_sql": result.get("generated_sql"),
                "row_count": len(result.get("query_results", [])),
                "processing_time_ms": elapsed_ms,
                "turn_count": turn_count,
                "has_mapping_report": result.get("mapping_report_md") is not None,
            }
            _store_result(query_id, {
                **response_data,
                "output_file": result.get("output_file"),
                "mapping_report_md": result.get("mapping_report_md"),
                "query_results": result.get("query_results", []),
            })

            yield _sse_event({
                "type": "done",
                "response": response_data["response"],
                "query_id": query_id,
                "thread_id": thread_id,
                "processing_time_ms": elapsed_ms,
                "row_count": response_data["row_count"],
                "executed_sql": response_data["executed_sql"],
                "has_file": response_data["has_file"],
                "file_name": response_data.get("file_name"),
                "turn_count": turn_count,
                "has_mapping_report": response_data.get("has_mapping_report", False),
            })

        except asyncio.TimeoutError:
            yield _sse_event({
                "type": "error",
                "message": "처리 시간이 초과되었습니다. 질의를 단순화해주세요.",
            })
        except Exception as e:
            logger.error(f"SSE 스트리밍 에러: {e}")
            yield _sse_event({
                "type": "error",
                "message": f"처리 중 오류가 발생했습니다: {str(e)}",
            })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/query/file",
    response_model=QueryResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def process_file_query(
    request: Request,
    query: str = Form(..., min_length=1, max_length=2000),
    file: UploadFile = File(...),
    thread_id: Optional[str] = Form(None),
    selected_db_ids: Optional[str] = Form(None),
    current_user: dict = Depends(require_user),
) -> QueryResponse:
    """양식 파일과 함께 질의를 처리한다."""
    # 1. 파일 타입 검증
    file_ext = _get_file_extension(file.filename)
    if file_ext not in ("xlsx", "docx"):
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 파일 형식입니다: .{file_ext}. .xlsx 또는 .docx만 지원합니다.",
        )

    # 1.5 존 역질문 게이트 (Plan 65 §4 파일 경로 확장) — 무거운 처리 전에 조기 반환
    selected_list = _parse_selected_db_ids_form(selected_db_ids)
    clarification = _file_zone_clarification_or_none(
        query, selected_list, request.app.state.config
    )
    if clarification:
        return QueryResponse(
            query_id=str(uuid.uuid4()),
            status="clarification",
            response=clarification["question"],
            thread_id=thread_id,
            clarification=clarification,
        )

    # 2. 파일 크기 검증 (최대 10MB)
    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="파일 크기가 10MB를 초과합니다.")

    # 3. Excel → CSV 변환 (xlsx인 경우, Redis 캐시 활용)
    csv_sheet_data = None
    if file_ext == "xlsx":
        try:
            from dataclasses import asdict

            from src.document.excel_csv_converter import excel_to_csv_cached
            from src.schema_cache.cache_manager import get_cache_manager

            cache_mgr = get_cache_manager(request.app.state.config)
            csv_result = await excel_to_csv_cached(
                file_bytes, cache_manager=cache_mgr
            )
            csv_sheet_data = {k: asdict(v) for k, v in csv_result.items()}
        except Exception as e:
            logger.warning("Excel→CSV 변환 실패, 기존 방식으로 진행: %s", e)

    # 4. 초기 State 생성
    query_id = str(uuid.uuid4())
    start_time = time.time()

    graph = request.app.state.graph
    config = request.app.state.config
    actual_thread_id = thread_id or query_id

    initial_state = create_initial_state(
        user_query=_substitute_zone_placeholder(query, selected_list),
        uploaded_file=file_bytes,
        file_type=file_ext,
        thread_id=actual_thread_id,
        csv_sheet_data=csv_sheet_data,
        user_id=current_user.get("sub"),
        user_department=current_user.get("department"),
        allowed_db_ids=current_user.get("allowed_db_ids"),
        selected_db_ids=selected_list,
        # 폼필은 전량 채움이 기본 — 기본 LIMIT(1000) 절단 방지(실측: 지시문에 "모든"이
        # 없으면 1,000행 절단). 명시 건수("100건")는 resolve_query_limit이 우선 반영.
        resolved_limit=resolve_query_limit(query, _FORM_FILL_DEFAULT_LIMIT),
    )

    thread_config = {"configurable": {"thread_id": actual_thread_id}}

    # 5. 그래프 실행
    try:
        result = await asyncio.wait_for(
            graph.ainvoke(initial_state, thread_config),
            timeout=config.server.file_query_timeout,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="처리 시간이 초과되었습니다.")
    except Exception as e:
        logger.error(f"파일 질의 처리 에러: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"처리 중 오류가 발생했습니다: {str(e)}",
        )

    elapsed_ms = (time.time() - start_time) * 1000
    turn_count = _count_human_messages(result.get("messages", []))

    response_data = {
        "query_id": query_id,
        "status": "completed",
        "response": result.get("final_response", ""),
        "thread_id": actual_thread_id,
        "has_file": result.get("output_file") is not None,
        "file_name": result.get("output_file_name"),
        "executed_sql": result.get("generated_sql"),
        "row_count": len(result.get("query_results", [])),
        "processing_time_ms": elapsed_ms,
        "turn_count": turn_count,
        "has_mapping_report": result.get("mapping_report_md") is not None,
    }
    _store_result(query_id, {
        **response_data,
        "output_file": result.get("output_file"),
        "mapping_report_md": result.get("mapping_report_md"),
        "query_results": result.get("query_results", []),
        # §14: 첨부 파일 카드 클릭 시 원본 양식을 되돌려주기 위해 업로드 원본을 보관한다.
        # TODO(§14.5): _results_store는 인메모리 dict이므로 원본 바이트 누적 시 메모리가 커진다.
        #   다중 워커 환경에서는 워커 간 유실 가능 — TTL/공유 스토리지 도입을 검토할 것.
        "uploaded_file": file_bytes,
        "uploaded_file_name": file.filename,
    })

    return QueryResponse(**response_data)


def _get_file_extension(filename: str | None) -> str:
    """파일 확장자를 추출한다."""
    if not filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@router.post("/query/file/stream")
async def process_file_query_stream(
    request: Request,
    query: str = Form(..., min_length=1, max_length=2000),
    file: UploadFile = File(...),
    thread_id: Optional[str] = Form(None),
    selected_db_ids: Optional[str] = Form(None),
    current_user: dict = Depends(require_user),
) -> StreamingResponse:
    """파일 업로드와 함께 SSE 스트리밍 방식으로 질의를 처리한다."""
    file_ext = _get_file_extension(file.filename)
    if file_ext not in ("xlsx", "docx"):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"detail": f"지원하지 않는 파일 형식: .{file_ext}"},
        )

    # 존 역질문 게이트 (Plan 65 §4 파일 경로 확장) — /query/file과 대칭
    selected_list = _parse_selected_db_ids_form(selected_db_ids)
    clarification = _file_zone_clarification_or_none(
        query, selected_list, request.app.state.config
    )
    if clarification:
        _clar_qid = str(uuid.uuid4())
        async def file_clarification_generator() -> AsyncGenerator[str, None]:
            yield _sse_event({
                "type": "done",
                "response": clarification["question"],
                "query_id": _clar_qid,
                "thread_id": thread_id,
                "clarification": clarification,
            })
        return StreamingResponse(
            file_clarification_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"detail": "파일 크기가 10MB를 초과합니다."},
        )

    csv_sheet_data = None
    if file_ext == "xlsx":
        try:
            from dataclasses import asdict
            from src.document.excel_csv_converter import excel_to_csv_cached
            from src.schema_cache.cache_manager import get_cache_manager
            cache_mgr = get_cache_manager(request.app.state.config)
            csv_result = await excel_to_csv_cached(file_bytes, cache_manager=cache_mgr)
            csv_sheet_data = {k: asdict(v) for k, v in csv_result.items()}
        except Exception as e:
            logger.warning("Excel→CSV 변환 실패, 기존 방식으로 진행: %s", e)

    query_id = str(uuid.uuid4())
    graph = request.app.state.graph
    config = request.app.state.config
    actual_thread_id = thread_id or query_id

    initial_state = create_initial_state(
        user_query=_substitute_zone_placeholder(query, selected_list),
        uploaded_file=file_bytes,
        file_type=file_ext,
        thread_id=actual_thread_id,
        csv_sheet_data=csv_sheet_data,
        user_id=current_user.get("sub"),
        user_department=current_user.get("department"),
        allowed_db_ids=current_user.get("allowed_db_ids"),
        selected_db_ids=selected_list,
        # 폼필은 전량 채움이 기본 — 기본 LIMIT(1000) 절단 방지(실측: 지시문에 "모든"이
        # 없으면 1,000행 절단). 명시 건수("100건")는 resolve_query_limit이 우선 반영.
        resolved_limit=resolve_query_limit(query, _FORM_FILL_DEFAULT_LIMIT),
    )

    thread_config = {"configurable": {"thread_id": actual_thread_id}}

    async def event_generator() -> AsyncGenerator[str, None]:
        start_time = time.time()
        streamed_any_token = False
        _seen_nodes: set[str] = set()
        _current_node: str | None = None
        _tracked_row_count: int = 0
        _tracked_query_results: list[dict] = []

        try:
            if hasattr(graph, "astream_events"):
                try:
                    # 이벤트 fetch마다 타임아웃(D-066 후속). 노드 내부 LLM 호출이 응답 없이
                    # 멈추면 SSE가 무한 hang되므로 stuck fetch를 wait_for로 끊는다.
                    _event_iter = graph.astream_events(
                        initial_state,
                        thread_config,
                        version="v2",
                    ).__aiter__()
                    while True:
                        try:
                            event = await asyncio.wait_for(
                                _event_iter.__anext__(),
                                timeout=config.server.file_query_timeout,
                            )
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            yield _sse_event({
                                "type": "error",
                                "message": "처리 시간이 초과되었습니다. 질의를 단순화해주세요.",
                            })
                            return
                        kind = event.get("event", "")
                        name = event.get("name", "")

                        if kind == "on_chain_start" and name and name not in _seen_nodes:
                            _known_nodes = {
                                "context_resolver", "input_parser",
                                "semantic_router", "schema_analyzer",
                                "field_mapper",
                                "query_generator", "query_validator",
                                "approval_gate", "query_executor",
                                "result_organizer", "output_generator",
                                "multi_db_executor", "result_merger",
                                "synonym_registrar", "general_inference", "error_response",
                                # Plan 48/49: 다중 의도 오케스트레이션 노드 (처리 현황 표시)
                                "intent_planner", "agent_orchestrator",
                                "replanner", "result_aggregator",
                            }
                            if name in _known_nodes:
                                _seen_nodes.add(name)
                                _current_node = name
                                yield _sse_event({
                                    "type": "node_start",
                                    "node": name,
                                    "timestamp_ms": (time.time() - start_time) * 1000,
                                })

                        if kind == "on_chain_end" and name:
                            node_output = event.get("data", {}).get("output", {})
                            if isinstance(node_output, dict) and name in _seen_nodes:
                                if name in ("query_executor", "multi_db_executor", "result_merger"):
                                    node_qr = node_output.get("query_results")
                                    if isinstance(node_qr, list):
                                        _tracked_row_count = len(node_qr)
                                        _tracked_query_results = node_qr
                                progress_data = _extract_node_progress(name, node_output)
                                if progress_data:
                                    yield _sse_event({
                                        "type": "node_complete",
                                        "node": name,
                                        "data": progress_data,
                                        "timestamp_ms": (time.time() - start_time) * 1000,
                                    })

                        if kind == "on_chat_model_stream":
                            # 최종 사용자 응답(USER_RESPONSE_TAG)으로 태깅된 LLM 호출의
                            # 토큰만 전달한다. orchestration 경로에서는 SQL 생성·DB 분류 등
                            # 중간 LLM 호출이 같은 노드(agent_orchestrator)에서 일어나므로
                            # 노드명이 아닌 태그로 구분해야 토큰이 새지 않는다.
                            _tags = event.get("tags", []) or []
                            _event_node = event.get("metadata", {}).get("langgraph_node", _current_node or "")
                            if USER_RESPONSE_TAG in _tags or _event_node in ("output_generator", "general_inference"):
                                chunk = event.get("data", {}).get("chunk")
                                if chunk and hasattr(chunk, "content") and chunk.content:
                                    streamed_any_token = True
                                    yield _sse_event({
                                        "type": "token",
                                        "content": chunk.content,
                                    })

                        elif kind == "on_chain_end":
                            output = event.get("data", {}).get("output", {})
                            if isinstance(output, dict) and "final_response" in output:
                                elapsed_ms = (time.time() - start_time) * 1000

                                if not streamed_any_token:
                                    yield _sse_event({
                                        "type": "token",
                                        "content": output.get("final_response", ""),
                                    })

                                _final_row_count = len(output.get("query_results", [])) or _tracked_row_count

                                yield _sse_event({
                                    "type": "meta",
                                    "executed_sql": output.get("generated_sql"),
                                    "row_count": _final_row_count,
                                })

                                turn_count = _count_human_messages(output.get("messages", []))
                                response_data = {
                                    "query_id": query_id,
                                    "status": "completed",
                                    "response": output.get("final_response", ""),
                                    "thread_id": actual_thread_id,
                                    "has_file": output.get("output_file") is not None,
                                    "file_name": output.get("output_file_name"),
                                    "executed_sql": output.get("generated_sql"),
                                    "row_count": _final_row_count,
                                    "processing_time_ms": elapsed_ms,
                                    "turn_count": turn_count,
                                    "has_mapping_report": output.get("mapping_report_md") is not None,
                                }
                                _store_result(query_id, {
                                    **response_data,
                                    "output_file": output.get("output_file"),
                                    "mapping_report_md": output.get("mapping_report_md"),
                                    "query_results": output.get("query_results") or _tracked_query_results,
                                    # §14: 첨부 파일 카드 클릭 시 원본 양식을 되돌려주기 위해 업로드 원본을 보관.
                                    # TODO(§14.5): 인메모리 dict — 원본 누적 시 메모리 증가/다중 워커 유실 가능.
                                    #   TTL/공유 스토리지 도입 검토.
                                    "uploaded_file": file_bytes,
                                    "uploaded_file_name": file.filename,
                                })

                                yield _sse_event({
                                    "type": "done",
                                    "response": response_data["response"],
                                    "query_id": query_id,
                                    "thread_id": actual_thread_id,
                                    "processing_time_ms": elapsed_ms,
                                    "row_count": response_data["row_count"],
                                    "executed_sql": response_data["executed_sql"],
                                    "has_file": response_data["has_file"],
                                    "file_name": response_data.get("file_name"),
                                    "awaiting_approval": False,
                                    "turn_count": turn_count,
                                    "has_mapping_report": response_data.get("has_mapping_report", False),
                                })
                                return

                    if not streamed_any_token:
                        raise AttributeError("astream_events did not produce output")

                except (AttributeError, TypeError, NotImplementedError):
                    pass

            # Fallback: ainvoke
            result = await asyncio.wait_for(
                graph.ainvoke(initial_state, thread_config),
                timeout=config.server.file_query_timeout,
            )
            elapsed_ms = (time.time() - start_time) * 1000
            final_response = result.get("final_response", "")
            yield _sse_event({"type": "token", "content": final_response})
            _final_row_count = len(result.get("query_results", []))
            yield _sse_event({
                "type": "meta",
                "executed_sql": result.get("generated_sql"),
                "row_count": _final_row_count,
            })
            turn_count = _count_human_messages(result.get("messages", []))
            response_data = {
                "query_id": query_id,
                "status": "completed",
                "response": final_response,
                "thread_id": actual_thread_id,
                "has_file": result.get("output_file") is not None,
                "file_name": result.get("output_file_name"),
                "executed_sql": result.get("generated_sql"),
                "row_count": _final_row_count,
                "processing_time_ms": elapsed_ms,
                "turn_count": turn_count,
                "has_mapping_report": result.get("mapping_report_md") is not None,
            }
            _store_result(query_id, {
                **response_data,
                "output_file": result.get("output_file"),
                "mapping_report_md": result.get("mapping_report_md"),
                "query_results": result.get("query_results", []),
                # §14: 첨부 파일 카드 클릭 시 원본 양식을 되돌려주기 위해 업로드 원본을 보관.
                # TODO(§14.5): 인메모리 dict — 원본 누적 시 메모리 증가/다중 워커 유실 가능.
                "uploaded_file": file_bytes,
                "uploaded_file_name": file.filename,
            })
            yield _sse_event({
                "type": "done",
                "response": response_data["response"],
                "query_id": query_id,
                "thread_id": actual_thread_id,
                "processing_time_ms": elapsed_ms,
                "row_count": response_data["row_count"],
                "executed_sql": response_data["executed_sql"],
                "has_file": response_data["has_file"],
                "file_name": response_data.get("file_name"),
                "turn_count": turn_count,
                "has_mapping_report": response_data.get("has_mapping_report", False),
            })

        except asyncio.TimeoutError:
            yield _sse_event({
                "type": "error",
                "message": "처리 시간이 초과되었습니다.",
            })
        except Exception as e:
            logger.error(f"파일 SSE 스트리밍 에러: {e}")
            yield _sse_event({
                "type": "error",
                "message": f"처리 중 오류가 발생했습니다: {str(e)}",
            })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/query/{query_id}/result",
    response_model=QueryResponse,
)
async def get_query_result(query_id: str) -> QueryResponse:
    """비동기 질의의 결과를 조회한다."""
    if query_id not in _results_store:
        raise HTTPException(status_code=404, detail="결과를 찾을 수 없습니다.")

    stored = _results_store[query_id]
    return QueryResponse(
        query_id=stored["query_id"],
        status=stored["status"],
        response=stored["response"],
        thread_id=stored.get("thread_id"),
        has_file=stored["has_file"],
        file_name=stored.get("file_name"),
        executed_sql=stored.get("executed_sql"),
        row_count=stored.get("row_count"),
        processing_time_ms=stored.get("processing_time_ms"),
        turn_count=stored.get("turn_count"),
        has_mapping_report=stored.get("has_mapping_report", False),
    )


@router.get("/query/{query_id}/mapping-report")
async def download_mapping_report(query_id: str) -> StreamingResponse:
    """매핑 보고서 MD 파일을 다운로드한다."""
    if query_id not in _results_store:
        raise HTTPException(status_code=404, detail="결과를 찾을 수 없습니다.")

    stored = _results_store[query_id]
    report_md = stored.get("mapping_report_md")

    if not report_md:
        raise HTTPException(status_code=404, detail="매핑 보고서가 없습니다.")

    return StreamingResponse(
        io.BytesIO(report_md.encode("utf-8")),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="mapping_report_{query_id[:8]}.md"'
        },
    )


@router.post("/query/mapping-feedback")
async def process_mapping_feedback(
    request: Request,
    file: UploadFile = File(...),
    query_id: str = Form(...),
) -> dict:
    """수정된 매핑 보고서 MD 파일을 업로드하여 Redis에 반영한다.

    사용자가 매핑 보고서를 다운로드 -> 수정 -> 업로드하면
    원본과 비교하여 변경사항을 Redis synonyms에 반영한다.

    Args:
        request: FastAPI Request (app.state.config 접근용)
        file: 수정된 매핑 보고서 MD 파일
        query_id: 원본 결과의 query_id

    Returns:
        반영 결과 딕셔너리
    """
    # 1. 원본 보고서 조회
    if query_id not in _results_store:
        raise HTTPException(status_code=404, detail="원본 결과를 찾을 수 없습니다.")

    stored = _results_store[query_id]
    original_md = stored.get("mapping_report_md")
    if not original_md:
        raise HTTPException(status_code=404, detail="원본 매핑 보고서가 없습니다.")

    # 2. 업로드된 파일 읽기
    file_bytes = await file.read()
    if len(file_bytes) > 1 * 1024 * 1024:  # 1MB 제한
        raise HTTPException(status_code=400, detail="파일 크기가 1MB를 초과합니다.")

    try:
        modified_md = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="파일 인코딩이 올바르지 않습니다. UTF-8 형식이어야 합니다.",
        )

    # 3. 원본/수정본 파싱
    from src.document.mapping_report import parse_mapping_report

    original_mappings = parse_mapping_report(original_md)
    modified_mappings = parse_mapping_report(modified_md)

    if not original_mappings:
        raise HTTPException(
            status_code=400, detail="원본 보고서 파싱에 실패했습니다."
        )
    if not modified_mappings:
        raise HTTPException(
            status_code=400,
            detail="수정된 보고서 파싱에 실패했습니다. 테이블 형식을 확인하세요.",
        )

    # 4. 변경사항 추출
    from src.document.field_mapper import analyze_md_diff

    diff = analyze_md_diff(original_mappings, modified_mappings)

    if (
        not diff.get("added")
        and not diff.get("modified")
        and not diff.get("deleted")
    ):
        return {"status": "no_changes", "summary": "변경사항이 없습니다."}

    # 5. Redis 반영
    from src.document.field_mapper import apply_mapping_feedback_to_redis
    from src.schema_cache.cache_manager import get_cache_manager

    config = request.app.state.config
    cache_mgr = get_cache_manager(config)
    if not cache_mgr.redis_available:
        await cache_mgr.ensure_redis_connected()

    result = await apply_mapping_feedback_to_redis(cache_mgr, diff)

    return {
        "status": "applied",
        "diff": {
            "added": len(diff.get("added", [])),
            "modified": len(diff.get("modified", [])),
            "deleted": len(diff.get("deleted", [])),
        },
        "result": result,
    }


@router.get("/query/{query_id}/download")
async def download_file(query_id: str) -> StreamingResponse:
    """생성된 파일을 다운로드한다."""
    if query_id not in _results_store:
        raise HTTPException(status_code=404, detail="결과를 찾을 수 없습니다.")

    stored = _results_store[query_id]
    file_bytes = stored.get("output_file")
    file_name = stored.get("file_name", "download")

    if not file_bytes:
        raise HTTPException(status_code=404, detail="생성된 파일이 없습니다.")

    content_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if file_name.endswith(".xlsx")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"'
        },
    )


@router.get("/query/{query_id}/attachment")
async def download_attachment(
    query_id: str,
    current_user: dict = Depends(require_user),
) -> StreamingResponse:
    """사용자가 업로드한 원본 양식 파일을 그대로 다운로드한다(§14).

    첨부 파일 카드 클릭 시 호출된다. 생성 결과 파일(`/download`)이 아니라
    업로드 원본(`uploaded_file`)을 서빙한다. 로그인 사용자로 접근을 제한한다.
    (향후: 본인 소유 query_id로만 제한하는 소유자 확인 추가 검토 — §14.5)
    """
    if query_id not in _results_store:
        raise HTTPException(status_code=404, detail="결과를 찾을 수 없습니다.")

    stored = _results_store[query_id]
    file_bytes = stored.get("uploaded_file")
    file_name = stored.get("uploaded_file_name") or "attachment"

    if not file_bytes:
        raise HTTPException(status_code=404, detail="원본 첨부 파일이 없습니다.")

    ext = _get_file_extension(file_name)
    if ext == "docx":
        content_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    elif ext == "xlsx":
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        content_type = "application/octet-stream"

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"'
        },
    )


@router.get("/query/{query_id}/download-csv")
async def download_csv(query_id: str) -> StreamingResponse:
    """조회 결과를 CSV 파일로 다운로드한다."""
    if query_id not in _results_store:
        raise HTTPException(status_code=404, detail="결과를 찾을 수 없습니다.")

    stored = _results_store[query_id]
    rows = stored.get("query_results", [])

    if not rows:
        raise HTTPException(status_code=404, detail="다운로드할 조회 결과가 없습니다.")

    # CSV 생성 (BOM 포함하여 Excel에서 한글 깨짐 방지)
    output = io.StringIO()
    output.write("\ufeff")  # UTF-8 BOM

    # 컬럼명: 행마다 키가 다를 수 있으므로(복합 task 병합 등) 등장 순서를 유지하며
    # 키 합집합을 만든다. 누락 키는 빈 값(restval), 초과 키는 무시(extrasaction)한다.
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    writer = csv.DictWriter(
        output, fieldnames=fieldnames, restval="", extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(r for r in rows if isinstance(r, dict))

    csv_bytes = output.getvalue().encode("utf-8")

    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="query_result_{query_id[:8]}.csv"'
        },
    )
