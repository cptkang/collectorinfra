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

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from src.api.schemas import ErrorResponse, QueryRequest, QueryResponse
from src.state import create_initial_state

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
            schema = output.get("schema_info", {})
            tables = output.get("relevant_tables", [])
            data = {}
            if tables:
                data["relevant_tables"] = tables
            if schema:
                table_summaries = {}
                for tbl_name, tbl_info in schema.items():
                    if isinstance(tbl_info, dict):
                        cols = tbl_info.get("columns", [])
                        if isinstance(cols, list):
                            table_summaries[tbl_name] = [
                                c.get("name", c) if isinstance(c, dict) else str(c)
                                for c in cols[:20]
                            ]
                        else:
                            table_summaries[tbl_name] = str(cols)[:200]
                    else:
                        table_summaries[tbl_name] = str(tbl_info)[:200]
                data["schema_summary"] = table_summaries
            return data if data else None

        elif node_name == "query_generator":
            sql = output.get("generated_sql", "")
            return {"generated_sql": sql} if sql else None

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
            if ctx:
                return {"turn_count": ctx.get("turn_count", 1)}
            return None

        elif node_name == "field_mapper":
            mapping = output.get("column_mapping") or {}
            sources = output.get("mapping_sources") or {}
            has_report = output.get("mapping_report_md") is not None
            data = {
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
            return data if data.get("total_count") else None

        elif node_name == "approval_gate":
            if output.get("awaiting_approval"):
                return {"awaiting_approval": True, "sql": output.get("approval_context", {}).get("sql", "")}
            return None

    except Exception as e:
        logger.debug(f"노드 진행 데이터 추출 실패 ({node_name}): {e}")
    return None


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

    if checkpoint_state is not None:
        # 후속 턴: delta input만 전달
        if checkpoint_state.get("awaiting_approval"):
            # SQL 승인 대기 중
            action, modified_sql = _parse_approval(body.query)
            input_state = {
                "user_query": body.query,
                "messages": [HumanMessage(content=body.query)],
                "approval_action": action,
                "approval_modified_sql": modified_sql if action == "modify" else None,
            }
        else:
            # 일반 후속 질의
            input_state = {
                "user_query": body.query,
                "messages": [HumanMessage(content=body.query)],
            }
    else:
        # 첫 턴: 전체 초기화
        input_state = create_initial_state(
            user_query=body.query,
            thread_id=thread_id,
        )

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

    if checkpoint_state is not None:
        if checkpoint_state.get("awaiting_approval"):
            action, modified_sql = _parse_approval(body.query)
            input_state = {
                "user_query": body.query,
                "messages": [HumanMessage(content=body.query)],
                "approval_action": action,
                "approval_modified_sql": modified_sql if action == "modify" else None,
            }
        else:
            input_state = {
                "user_query": body.query,
                "messages": [HumanMessage(content=body.query)],
            }
    else:
        input_state = create_initial_state(
            user_query=body.query,
            thread_id=thread_id,
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        """SSE 이벤트를 생성하는 비동기 제너레이터."""
        start_time = time.time()
        streamed_any_token = False
        _seen_nodes: set[str] = set()
        _tracked_row_count: int = 0
        _tracked_query_results: list[dict] = []

        try:
            if hasattr(graph, "astream_events"):
                try:
                    async for event in graph.astream_events(
                        input_state,
                        thread_config,
                        version="v2",
                    ):
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
                                "synonym_registrar", "error_response",
                            }
                            if name in _known_nodes:
                                _seen_nodes.add(name)
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

                        # LLM 토큰 스트리밍
                        if kind == "on_chat_model_stream":
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
) -> QueryResponse:
    """양식 파일과 함께 질의를 처리한다."""
    # 1. 파일 타입 검증
    file_ext = _get_file_extension(file.filename)
    if file_ext not in ("xlsx", "docx"):
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 파일 형식입니다: .{file_ext}. .xlsx 또는 .docx만 지원합니다.",
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
        user_query=query,
        uploaded_file=file_bytes,
        file_type=file_ext,
        thread_id=actual_thread_id,
        csv_sheet_data=csv_sheet_data,
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
    })

    return QueryResponse(**response_data)


def _get_file_extension(filename: str | None) -> str:
    """파일 확장자를 추출한다."""
    if not filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


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

    # 첫 번째 행에서 컬럼명 추출
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    csv_bytes = output.getvalue().encode("utf-8")

    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="query_result_{query_id[:8]}.csv"'
        },
    )
