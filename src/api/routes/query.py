"""질의 관련 라우트.

자연어 질의를 처리하고 결과를 반환하는 엔드포인트를 제공한다.
SSE 스트리밍 응답도 지원한다.
멀티턴 대화와 Human-in-the-loop(SQL 승인)을 지원한다.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional
from typing import Literal, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from src.api.dependencies import require_user
from src.api.error_detail import user_error_detail
from src.api.result_chart_view import (
    chart_carry,
    chart_field,
    result_tables_of,
    viz_store_fields,
)
from src.api.schemas import ErrorResponse, QueryRequest, QueryResponse
from src.api.sse_events import (
    SSE_DONE,
    SSE_META,
    SSE_NODE_COMPLETE,
    SSE_NODE_START,
    SSE_TOKEN,
)
from src.api.stream_failure import (
    CUT_HARD_CAP,
    CUT_PROCESSING,
    StreamTrace,
    StreamWatch,
    delivery_cut_notice,
)
from src.api.thread_history import TurnRecorder
from src.domain.partial_result import (
    PARTIAL_STATUS,
    PartialAnswer,
    extract_partial_answer,
    render_partial_text,
)
from src.llm import USER_RESPONSE_TAG
from src.security.data_masker import DataMasker
from src.utils.progress_events import ANSWER_PREFIX_EVENT
from src.routing.db_authz import SELECTION_DENIED_MESSAGE, filter_selected_db_ids
from src.state import create_followup_input, create_initial_state
from src.routing.db_scope import build_db_scope
from src.utils.query_gen_common import resolve_query_limit

# 폼필(파일 업로드) 기본 LIMIT — 전량 채움이 기본(전량 상향값과 동일).
# 실행 상한은 db 클라이언트 max_rows(10,000)가 안전망(D-066 후속7 계열).
# 값은 _ALL_QUERY_LIMIT 참조로 고정한다 — 독립 리터럴(구 100,000)로 두면 D-134 하향
# (10,000, spec 정합) 이후 enforce_all_query_limit의 발동 조건(effective==상향값)과
# 어긋나 캡 모방 교정이 침묵 무력화된다(병합 검증 실측).
from src.utils.query_gen_common import _ALL_QUERY_LIMIT

# plans/179 W2 재노출 shim(D-337 ①) — 다음 계획에서 grep 0 확인 후 제거.
# 옮긴 정의를 옛 이름 그대로 다시 노출한다(patch seam·`from src.api.routes.query import …`).
# plans/166 단계 8: 이 경로 사용처 0인 이름은 제거했다 — 남은 이름은 이 모듈 안에서 쓰이거나
# 테스트·코드가 이 경로로 import·patch한다.
from src.api.query.store import (  # noqa: F401
    _attachment_disposition as _attachment_disposition,
    _audit_file_download as _audit_file_download,
    _owned_result as _owned_result,
    _results_store as _results_store,
    _store_result as _store_result,
)
from src.api.query.sse import (  # noqa: F401
    _graph_event_stream as _graph_event_stream,
    _heartbeat_sse_payload as _heartbeat_sse_payload,
    _is_subgraph_event as _is_subgraph_event,
    _next_event_or_timeout as _next_event_or_timeout,
    _progress_sse_payload as _progress_sse_payload,
    _single_done_stream as _single_done_stream,
    _sse_event as _sse_event,
    _sse_response as _sse_response,
    _stream_error_payload as _stream_error_payload,
    _STREAM_KNOWN_NODES as _STREAM_KNOWN_NODES,
    _token_text as _token_text,
)
from src.api.query.turn_input import (  # noqa: F401
    _get_checkpoint_state as _get_checkpoint_state,
    _resolve_turn_approval as _resolve_turn_approval,
    _source_selection_meta as _source_selection_meta,
    _zone_answer_parse_reuse as _zone_answer_parse_reuse,
    _zone_answer_plan_reuse as _zone_answer_plan_reuse,
)
from src.pipeline.turn.approval import (  # noqa: F401
    _is_legacy_structure_hitl_thread as _is_legacy_structure_hitl_thread,
    _parse_approval as _parse_approval,
    _release_legacy_structure_hitl as _release_legacy_structure_hitl,
    resolve_approval_action as resolve_approval_action,
)
from src.pipeline.turn.selection import (  # noqa: F401
    _apm_source_text_answer as _apm_source_text_answer,
    apply_apm_source_selection_authorization as apply_apm_source_selection_authorization,
    apply_selection_authorization as apply_selection_authorization,
    apply_source_selection_authorization as apply_source_selection_authorization,
    _parse_selected_db_ids_form as _parse_selected_db_ids_form,
    _with_current_identity as _with_current_identity,
)
from src.api.query.fields import (  # noqa: F401
    _apm_source_fields as _apm_source_fields,
    _clarify_store_fields as _clarify_store_fields,
    _clarify_turn_fields as _clarify_turn_fields,
    _count_human_messages as _count_human_messages,
    _dependency_notes_field as _dependency_notes_field,
    _disclosures_field as _disclosures_field,
    _executed_sql as _executed_sql,
    _nonsql_task_summary as _nonsql_task_summary,
    _PARTIAL_DONE_DEFAULTS as _PARTIAL_DONE_DEFAULTS,
    _partial_response_data as _partial_response_data,
    _plan_summary_carry as _plan_summary_carry,
    _plan_summary_field as _plan_summary_field,
    _pre_gate_plan_summary as _pre_gate_plan_summary,
    _rewrite_trace_fields as _rewrite_trace_fields,
    _scope_reexpand_field as _scope_reexpand_field,
    _time_clarification as _time_clarification,
    _time_resolution_field as _time_resolution_field,
)
from src.api.query.progress import (  # noqa: F401
    _extract_node_progress as _extract_node_progress,
    _merge_node_delta as _merge_node_delta,
    _summarize_tasks as _summarize_tasks,
    _track_sql_state as _track_sql_state,
)
from src.pipeline.turn.zone import (  # noqa: F401
    build_scope_reexpand as build_scope_reexpand,
    _scope_narrowed_or_none as _scope_narrowed_or_none,
    _substitute_zone_placeholder as _substitute_zone_placeholder,
    _unregistered_zone_clarification_or_none as _unregistered_zone_clarification_or_none,
    _zone_display_label as _zone_display_label,
    _zone_group_exclusive_or_none as _zone_group_exclusive_or_none,
)
from src.pipeline.turn.intake import (  # noqa: F401
    _raw_query_seed as _raw_query_seed,
    _shadow_input_guard as _shadow_input_guard,
    _turn_disclosures as _turn_disclosures,
)
from src.api.query.gates import (  # noqa: F401
    _audit_clarification as _audit_clarification,
    _audit_scope_narrowed as _audit_scope_narrowed,
    _file_zone_clarification_or_none as _file_zone_clarification_or_none,
    _form_memory_delete_or_none as _form_memory_delete_or_none,
    _scope_select_or_none as _scope_select_or_none,
    _zone_clarification_or_none as _zone_clarification_or_none,
    _zone_gate_deferred_to_tasks as _zone_gate_deferred_to_tasks,
)
from src.api.query.deadline import (  # noqa: F401
    _answer_prefix_text as _answer_prefix_text,
    _bind_deadline as _bind_deadline,
    _finish_timeline as _finish_timeline,
    _processing_limit as _processing_limit,
    _request_deadline as _request_deadline,
    _stream_watch as _stream_watch,
    _unbind_deadline as _unbind_deadline,
)
from src.api.query.upload import (  # noqa: F401
    _get_file_extension as _get_file_extension,
    _resolve_uploaded_bytes as _resolve_uploaded_bytes,
)

_FORM_FILL_DEFAULT_LIMIT = _ALL_QUERY_LIMIT
# 존 선택 재개 턴 기본 LIMIT (D-153 후속1) — 존 역질문은 존 단위 전량 조회에서만
# 발동하므로 재개 턴은 전량 상향이 기본(명시 건수는 resolve_query_limit이 우선 반영).
# 미상향 시 "모든/전체" 표면어 없는 질의에서 few-shot 말미 캡(FETCH FIRST 100) 모방이
# 교정되지 않아 100건 절단된다(2026-08-04 라이브 실측: 은행존 VM 100건).
_ZONE_SCAN_LIMIT = _ALL_QUERY_LIMIT

logger = logging.getLogger(__name__)
router = APIRouter()

#: 그래프 실행 예외의 사용자 문구(plans/163 S3) — 원문은 `user_error_detail`이 서버 로그에 남기고
#: 응답에는 이 문구 + 「(요청 {번호})」만 싣는다.
_GRAPH_ERROR_MESSAGE = "처리 중 오류가 발생했습니다"


async def _audit_user_request(
    request: Request,
    current_user: dict,
    *,
    user_query: str,
    output_format: str,
    has_file: bool,
    thread_id: Optional[str],
) -> None:
    """사용자 질의를 감사에 기록한다 (D-183).

    **기록의 주체는 요청 수신 지점이지 파싱 노드가 아니다.** 종전에는
    `input_parser` 노드가 파일 감사만 직접 호출했는데(그래서 DB 감사에는 질의가
    한 건도 없었다), 노드는 `app.state`에 닿지 못해 `client_ip`·`session_id`를
    영원히 채울 수 없다. 이 헬퍼로 옮기면서 파일·DB가 한 지점에서 나오므로
    두 저장소의 건수가 일치한다.

    감사 실패는 질의를 막지 않는다 — 다만 삼키지 않고 경고로 남긴다.
    """
    audit_service = getattr(request.app.state, "audit_service", None)
    if not audit_service:
        return
    try:
        await audit_service.log_user_request(
            user_id=current_user.get("sub") if current_user else None,
            user_query=user_query,
            output_format=output_format,
            has_file=has_file,
            client_ip=request.client.host if request.client else None,
            session_id=thread_id,
        )
    except Exception as e:
        logger.warning("사용자 질의 감사 기록 실패: %s", e)


def _build_turn_input_state(
    body: QueryRequest,
    thread_id: str,
    checkpoint_state: dict | None,
    current_user: dict,
    *,
    approval: tuple[str, str] | None = None,
    config=None,
) -> dict:
    """턴 유형(첫/후속/승인)에 따른 그래프 입력 상태를 조립한다 — 텍스트 라우트 단일 출처.

    /query(비스트리밍)와 /query/stream(SSE)이 이 로직을 각자 인라인으로 들고 있다가,
    D-064 폼필 상태 초기화(create_followup_input)가 /query에만 적용되고 SSE 경로에는
    누락되는 비대칭이 발생했다(2026-07-16: 직전 폼업로드 턴의 uploaded_file이 체크포인터로
    복원돼 옛 양식이 재파싱됨). 두 라우트가 반드시 이 헬퍼를 공유하여 재발을 차단한다.

    Args:
        approval: 라우트가 미리 해소한 (action, modified_sql). 승인 의사 LLM 보조(R3-(ii))는
            async라 이 동기 헬퍼 밖에서 해소해 주입한다. 미지정이면 결정적 판정만 수행한다.
        config: 앱 설정. 범위 축소 기록(D-176 후속4)에만 쓰이며 **키워드 기본값**이라
            기존 호출부(테스트 12곳 포함)는 무변경으로 동작한다 — 미지정이면 기록을
            남기지 않는다(종전 동작과 동일).
    """
    if checkpoint_state is not None:
        # 후속 턴: delta input만 전달
        if (
            checkpoint_state.get("awaiting_approval")
            and not _is_legacy_structure_hitl_thread(checkpoint_state)
        ):
            # SQL 승인 대기 중
            action, modified_sql = approval or _parse_approval(body.query)
            return _with_current_identity({
                "user_query": body.query,
                "messages": [HumanMessage(content=body.query)],
                "approval_action": action,
                "approval_modified_sql": modified_sql if action == "modify" else None,
            }, current_user)
        # HITL 폼필 답변 턴(Plan 73 §11, D-151): 구조화 답변 + pending의 원본 파일 복원.
        # input_parser가 template을 재파싱(③.5 단일 task 고정)하고 결정적 조립이 답변을
        # 오버라이드(존재성 검증)로 적용한다. LLM 파싱 없음.
        pending_ff = checkpoint_state.get("pending_form_fill")
        if body.form_fill_answers:
            if pending_ff and pending_ff.get("uploaded_file"):
                # FIX-26(라이브 실측 2026-08-13): 존 선택 복원 — FIX-17의 원 질의 복원은
                # 존이 텍스트 위치어로 지정된 런에서만 라우팅을 재현한다. 존 체크박스 런
                # ("채워줘" 원 질의)은 위치어가 없어 답변 턴이 기본 DB(b0)로 침묵
                # 오라우팅되고 존재성 검증도 b0 스키마 기준이 됐다. 패널을 발행한 런의
                # 확정 존(pending.db_ids)을 selected_db_ids로 복원한다(이번 턴 명시
                # 선택이 있으면 그것이 우선 — 요청 스코프 계약 유지).
                restored_db_ids = body.selected_db_ids or pending_ff.get("db_ids") or None
                # 복원 값도 인가를 통과해야 한다(plans/104 C-4 · D-232) — 체크포인트는
                # 이전 턴의 산물이고 thread_id는 요청이 정하므로 권한 회수·타인 스레드
                # 지정으로 비인가 DB가 되살아날 수 있다. 전부 걸러지면 선택 없음으로
                # 되돌리고, 사유 통보는 라우터 경계(`authorized_router`)가 맡는다.
                restored_db_ids, _ = filter_selected_db_ids(
                    restored_db_ids,
                    current_user.get("allowed_db_ids"),
                    current_user.get("role"),
                )
                restored_db_ids = restored_db_ids or None
                delta = create_followup_input(
                    body.query or "[양식 미해결 항목 답변]",
                    selected_db_ids=restored_db_ids,
                )
                delta["uploaded_file"] = pending_ff.get("uploaded_file")
                delta["file_type"] = pending_ff.get("file_type")
                delta["form_fill_answers"] = body.form_fill_answers
                # FIX-17(라이브 실측 2026-07-31): 파이프라인 입력은 **원 질의**로 복원한다.
                # 답변 턴 고정 문구에는 위치 힌트(여의도 등)가 없어 priority_db_ids가
                # 비고 → 전 DB 유사어·설명이 LLM 프롬프트에 실려 413(FabriX 95K 한도)
                # + 타 DB(B0) 배회. 1차 런과 동일 입력이어야 동일 라우팅·매핑이 재현된다.
                original_q = pending_ff.get("original_query")
                if original_q:
                    delta["user_query"] = original_q
                # Phase 3(D-151): 기억 옵트인 — 검증 통과 답변만 output_generator가 저장
                delta["form_fill_remember"] = bool(body.form_fill_remember)
                # 폼필은 전량 채움이 기본 — 파일 경로와 동일 LIMIT(텍스트 경로 기본
                # 1,000 절단 방지, D-066 후속7 계열). 원 질의의 명시 건수("10건만")는
                # 첫 턴과 같이 우선한다 — 고정 전량이면 답변 뒤에 건수가 사라졌다.
                delta["resolved_limit"] = resolve_query_limit(
                    original_q or body.query, _FORM_FILL_DEFAULT_LIMIT
                )
                logger.info(
                    "폼필 답변 턴(D-151): %d개 필드 답변 수신, 원본 파일·원 질의 복원"
                    "(file_type=%s, query=%r, 존 복원=%s)",
                    len(body.form_fill_answers), pending_ff.get("file_type"),
                    (original_q or "")[:50], restored_db_ids,
                )
                return _with_current_identity(
                    _release_legacy_structure_hitl(delta, checkpoint_state), current_user
                )
            # pending 없이 답변만 도착 — 침묵 무시 대신 로그 후 일반 질의로 처리
            logger.warning(
                "form_fill_answers 수신했으나 pending_form_fill 부재 — 일반 질의로 처리"
            )
        # 일반 후속 질의 — 직전 폼업로드 턴의 요청-스코프 폼필 상태를 초기화한다(D-064).
        # selected_db_ids(존 선택)는 요청 스코프 — 이번 턴 값 또는 None으로 매 턴 재공급.
        # allow_zone_clarification=True: 대화형 텍스트 라우트는 존 역질문 후단 게이트
        # (D-143 후속2) 허용 채널 — 배치·평가·API 직접 호출 경로는 기본 False 유지.
        # 제니퍼 소스 되묻기 답(plans/147 · D-322) — 버튼(`selected_apm_source_ids`)이면
        # 같은 원 질문이 다시 오고, 글 답(소스 단어·「전체」뿐)이면 질의를 되묻기 원 질문으로
        # 바꾼다(대화 기록에는 사용자 원문을 남긴다). 직전 턴이 되묻었으면 근거는 `answered`,
        # 아니면 화면 선택(`selected`).
        pending = checkpoint_state.get("apm_source_pending")
        apm_ids = getattr(body, "selected_apm_source_ids", None)
        apm_answer: str | None = None
        turn_query = body.query
        if not apm_ids:
            text_answer = _apm_source_text_answer(body.query, pending, current_user)
            if text_answer is not None and isinstance(pending, dict):
                apm_ids, apm_answer = text_answer
                turn_query = str(pending["query"])
        apm_basis = ("answered" if isinstance(pending, dict) and pending else "selected") \
            if apm_ids else None
        delta = create_followup_input(
            _substitute_zone_placeholder(turn_query, body.selected_db_ids),
            selected_db_ids=body.selected_db_ids,
            selected_sources=getattr(body, "selected_sources", None),
            allow_zone_clarification=True,
            raw_user_query=_raw_query_seed(turn_query, config),
            # 스코프 칩 "해제"(D-205) — 승계 원천 초기화 + context_resolver sticky 차단
            reset_db_scope=bool(getattr(body, "reset_db_scope", False)),
            # 제니퍼 소스 칩 "×"(plans/147) — 제니퍼 승계만 끊는다
            reset_apm_source_scope=bool(getattr(body, "reset_apm_source_scope", False)),
            selected_apm_source_ids=apm_ids,
            apm_source_basis=apm_basis,
            apm_source_answer_text=apm_answer,
        )
        if turn_query != body.query:
            delta["messages"] = [HumanMessage(content=body.query)]
        # 존 선택 재개 턴은 전량 조회가 기본 — LIMIT 상향(D-153 후속1, 폼필 후속1과 동형)
        if body.selected_db_ids:
            delta["resolved_limit"] = resolve_query_limit(body.query, _ZONE_SCAN_LIMIT)
        # 존 역질문 답변 턴은 직전 턴 파싱본을 재사용한다(plans/119 Q-2 · D-267 ③ — 요청 스코프).
        delta["reuse_parsed_requirements"] = _zone_answer_parse_reuse(body, checkpoint_state)
        # 같은 조건에서 직전 턴 복합 계획 스냅샷을 복원 입력으로 옮긴다(plans/121 TP-1.2 · G-30).
        # 원 키(`zone_reentry_plan`)는 위 델타가 None으로 덮는다 — 체크포인트는 병합 전에 읽었다.
        delta["reuse_task_plan"] = _zone_answer_plan_reuse(body, checkpoint_state)
        # 소스 선택 칩·「다른 소스로 보기」 응답 표지(plans/132 W5 — 기억 쓰기 게이트)
        delta["source_selection_meta"] = _source_selection_meta(body, checkpoint_state)
        # 범위를 좁혔으면 그 사실을 state에 남긴다(D-176 후속4 — 침묵 절단 금지).
        delta["scope_narrowed"] = (
            _scope_narrowed_or_none(body, config, current_user) if config else None
        )
        # 원문만으로 정해지는 턴 단위 고지(plans/123 W-4 · S-1) — 요청 스코프
        delta["turn_disclosures"] = _turn_disclosures(turn_query, body.selected_db_ids)
        return _with_current_identity(
            _release_legacy_structure_hitl(delta, checkpoint_state), current_user
        )
    # 첫 턴: 전체 초기화
    return create_initial_state(
        user_query=_substitute_zone_placeholder(body.query, body.selected_db_ids),
        raw_user_query=_raw_query_seed(body.query, config),
        thread_id=thread_id,
        user_id=current_user.get("sub"),
        user_department=current_user.get("department"),
        user_role=current_user.get("role"),
        allowed_db_ids=current_user.get("allowed_db_ids"),
        allowed_sources=current_user.get("allowed_sources"),
        selected_db_ids=body.selected_db_ids,
        selected_sources=getattr(body, "selected_sources", None),
        # 제니퍼 소스 화면 선택(plans/147) — 첫 턴은 되묻기 대기가 없어 근거가 늘 `selected`다
        selected_apm_source_ids=getattr(body, "selected_apm_source_ids", None),
        allow_zone_clarification=True,
        # 존 선택 재개 턴(pre-gate는 파이프라인 미실행이라 첫 턴으로 도착)도 전량 상향
        resolved_limit=(
            resolve_query_limit(body.query, _ZONE_SCAN_LIMIT)
            if body.selected_db_ids else None
        ),
    ) | {
        "scope_narrowed": (
            _scope_narrowed_or_none(body, config, current_user) if config else None
        ),
        # 원문만으로 정해지는 턴 단위 고지(plans/123 W-4 · S-1) — 요청 스코프
        "turn_disclosures": _turn_disclosures(body.query, body.selected_db_ids),
    }


async def _partial_on_timeout(
    graph, thread_config: dict, tracked_rows: list[dict] | None = None
) -> PartialAnswer | None:
    """상한에 걸린 턴에서 **이미 조회한 행**을 건진다 (plans/114 P-2 · G-E).

    스트림 경로는 노드 출력에서 행을 이미 추적하고 있으므로(`_tracked_query_results`)
    그것을 먼저 쓴다 — 취소된 그래프의 체크포인트는 마지막 노드 경계까지만 쓰여 있어
    더 적을 수 있다. 추적분이 없으면 체크포인트를 읽는다(비스트림 경로).
    """
    tracked = [row for row in (tracked_rows or []) if isinstance(row, dict)]
    if tracked:
        return PartialAnswer(rows=tracked, source="query_results")
    # 2단 그래프(`intent_planner` 노드가 있다 — 사다리 배타는 빌드 타임이다)면 이번 계획의 하위
    # 작업 결과만 본다 — 최상위 행은 앞 턴 값이다(plans/121 TP-1.1b). 판정은 이 턴을 돌린 그래프로
    # 한다(프로세스 전역 확정 기록은 테스트·재빌드 사이에 남는다).
    nodes = getattr(graph, "nodes", None)
    return extract_partial_answer(
        await _get_checkpoint_state(graph, thread_config),
        task_scoped=isinstance(nodes, dict) and "intent_planner" in nodes,
    )


async def _timeout_sse_event(
    graph,
    thread_config: dict,
    *,
    tracked_rows: list[dict] | None,
    query_id: str,
    thread_id: str | None,
    start_time: float,
    limit_sec: float,
    trace: StreamTrace,
    message: str,
    owner: str | None,
    config: Any,
    user_query: str,
    timeline: dict[str, Any] | None = None,
) -> str:
    """상한에 걸린 스트림이 내보낼 마지막 이벤트 (plans/114 P-2 · G-E).

    행이 있으면 `done`(`status=partial`), 없으면 종전 오류 이벤트다. **`done` 으로 나가야
    기록기가 턴을 남긴다** — 오류 이벤트는 `TurnRecorder.stream` 이 기록하지 않는다.
    """
    partial = await _partial_on_timeout(graph, thread_config, tracked_rows)
    if partial is not None:
        data = _partial_response_data(
            partial, query_id=query_id, thread_id=thread_id,
            start_time=start_time, limit_sec=limit_sec,
        )
        # plans/160 A-2 — 부분 결과는 표 경계를 싣지 않는다(추적 행이 집계본과 다를 수 있다)
        chart = chart_field(config, partial.rows, None)
        data = {**data, **chart_carry(chart)}
        _store_result(query_id, {
            **data, "query_results": partial.rows,
            **viz_store_fields(chart, user_query=user_query, tables=None),
        }, owner=owner)
        event = {"type": SSE_DONE, **_PARTIAL_DONE_DEFAULTS, **data}
        if timeline is not None:
            event["timeline"] = timeline
        return _sse_event(event)
    return _sse_event(_stream_error_payload(
        message, trace, code="timeout", start_time=start_time, limit_sec=limit_sec,
        timeline=timeline,
    ))


async def _delivery_cut_sse_event(
    graph: Any,
    thread_config: dict[str, Any],
    *,
    watch: StreamWatch,
    kind: str,
    tracked_rows: list[dict[str, Any]] | None,
    query_id: str,
    thread_id: str | None,
    owner: str | None,
    config: Any,
    user_query: str,
    timeline: dict[str, Any],
) -> str:
    """첫 답변 **뒤** 상한(토큰 간 idle · 전체 상한)에 걸린 스트림의 마지막 이벤트 (plans/119 T-5).

    이미 보낸 답변을 잃지 않는다. 화면은 `done.response` 로 말풍선을 교체하므로(`app.js`) 보낸
    본문 그대로에 끊은 사유 한 줄을 붙여 돌려준다. 코드 렌더 표(`answer_prefix`)가 아직 나가지
    않았고 행이 있으면 D-265 결정적 표를 덧붙인다 — 행을 버리지 않는다(D-265 ①). 상태는
    `partial`(D-265 ② — 판정은 timeout 과 같은 제거 사유)이고 `done` 으로 낸다(D-265 ③ — 오류
    이벤트는 기록기가 턴을 남기지 않는다).
    """
    partial = await _partial_on_timeout(graph, thread_config, tracked_rows)
    caps = watch.caps
    text = watch.answer_text.rstrip() + "\n\n---\n" + delivery_cut_notice(
        kind, idle_sec=caps.idle_sec, limit_sec=caps.limit_sec, grace_sec=caps.grace_sec,
    )
    if partial is not None and not watch.prefix_sent:
        text += "\n\n" + render_partial_text(partial)
    rows = partial.rows if partial is not None else []
    data = {
        "query_id": query_id,
        "status": PARTIAL_STATUS,
        "response": text,
        "thread_id": thread_id,
        "row_count": len(rows),
        "processing_time_ms": watch.elapsed_ms(),
    }
    chart = chart_field(config, rows, None)   # plans/160 A-2 — 부분 결과는 표 경계 없음
    data.update(chart_carry(chart))
    _store_result(query_id, {
        **data, "query_results": rows,
        **viz_store_fields(chart, user_query=user_query, tables=None),
    }, owner=owner)
    return _sse_event({"type": SSE_DONE, **_PARTIAL_DONE_DEFAULTS, **data, "timeline": timeline})


async def _stream_cut_sse_event(
    graph: Any,
    thread_config: dict[str, Any],
    *,
    watch: StreamWatch,
    kind: str,
    tracked_rows: list[dict[str, Any]] | None,
    query_id: str,
    thread_id: str | None,
    trace: StreamTrace,
    message: str,
    owner: str | None,
    config: Any,
    user_query: str,
) -> str:
    """스트림 상한 출구 하나 — 사유별로 나눈다 (plans/119 T-5 · 두 스트림 라우트 공유).

    - 처리 상한(첫 답변 전): 종전 타임아웃 그대로 — D-265 부분 결과 또는 오류(`message` 불변).
    - 토큰 간 idle · 전체 상한(첫 답변 뒤): 보낸 답변 + 사유(`_delivery_cut_sse_event`).
    """
    timeline = _finish_timeline(watch, query_id, timeout_kind=kind)
    if kind == CUT_PROCESSING:
        return await _timeout_sse_event(
            graph, thread_config,
            tracked_rows=tracked_rows, query_id=query_id, thread_id=thread_id,
            start_time=watch.start_time, limit_sec=watch.caps.limit_sec,
            trace=trace, message=message, owner=owner, timeline=timeline,
            config=config, user_query=user_query,
        )
    return await _delivery_cut_sse_event(
        graph, thread_config,
        watch=watch, kind=kind, tracked_rows=tracked_rows,
        query_id=query_id, thread_id=thread_id, owner=owner, timeline=timeline,
        config=config, user_query=user_query,
    )


@dataclass
class PreGateResult:
    """사전 게이트 조기 반환(plans/168 A3 · plans/179 W1a) — 네 진입이 같은 모양으로 렌더한다.

    비스트림은 `QueryResponse(query_id, status, response, thread_id, **extra)`
    (`_pre_gate_response`), 스트림은 done 1건 `{type, response, query_id, thread_id, **extra}`
    (`_pre_gate_done`)이다.
    `extra` 키 순서가 done JSON 키 순서다.
    """

    kind: Literal["selection_denied", "clarification", "memory_delete"]
    status: str
    response: str
    extra: dict[str, Any]


async def _run_pre_gates(
    selected_db_ids: list[str] | None,
    current_user: dict[str, Any],
    config: Any,
    *,
    thread_id: str,
    body: QueryRequest | None = None,
    checkpoint_state: dict[str, Any] | None = None,
    file_query: str = "",
) -> tuple[list[str] | None, PreGateResult | None]:
    """네 진입 공유 사전 게이트 — (인가한 DB 선택, 조기 반환 또는 None) (plans/179 W1a).

    순서(현행 보존): DB 선택 인가(plans/104 C-4 · D-232 — 존 게이트보다 먼저) → 텍스트 진입만
    소스 선택 인가(plans/132 N-10 · plans/147) → 거부면 사유를 싣고 반환(침묵 강등 금지) →
    역질문 — 텍스트는 존 역질문(Plan 75 §4 — 결정적 게이트, 서버측 보류 상태 없음) → (없으면)
    범위 사전 선택(**모호성 해소 다음** — 2연속 질문 금지, D-176 후속4), 파일은 파일 존 역질문
    (Plan 75 §4 확장) → 역질문이면 발동 감사(plans/82 v7 R-6) 뒤 반환 → 텍스트만 (역질문이 없을 때)
    저장 값 패널 삭제(D-187 — 파이프라인·LLM 미경유).

    텍스트 진입(`body`)은 인가한 선택을 요청 본문에도 바로 적는다 — 뒤 게이트가 본문을 읽는다.
    인가·게이트·감사 함수는 호출 시점에 모듈 전역에서 찾는다(patch seam).
    """
    selected_db_ids, denied = apply_selection_authorization(selected_db_ids, current_user)
    if body is not None:
        body.selected_db_ids = selected_db_ids
        body.selected_sources = apply_source_selection_authorization(
            body.selected_sources, current_user)
        body.selected_apm_source_ids = apply_apm_source_selection_authorization(
            body.selected_apm_source_ids, current_user)
    if denied:
        return selected_db_ids, PreGateResult(
            "selection_denied", "success", SELECTION_DENIED_MESSAGE, {}
        )
    if body is not None:
        clarification = _zone_clarification_or_none(body, checkpoint_state, config, current_user)
        if not clarification:
            clarification = _scope_select_or_none(
                body, checkpoint_state, config, current_user
            )
    else:
        clarification = _file_zone_clarification_or_none(
            file_query, selected_db_ids, config, current_user
        )
    if clarification:
        await _audit_clarification(clarification, current_user, thread_id)
        return selected_db_ids, PreGateResult(
            "clarification", "clarification", clarification["question"],
            {"clarification": clarification, **_pre_gate_plan_summary()},  # plans/121 TP-0.1
        )
    if body is not None:
        mem_delete = await _form_memory_delete_or_none(body, checkpoint_state, config)
        if mem_delete:
            return selected_db_ids, PreGateResult(
                "memory_delete", "success", mem_delete["response"],
                {"form_memory_panel": mem_delete["form_memory_panel"]},
            )
    return selected_db_ids, None


def _pre_gate_response(gate: PreGateResult, *, query_id: str, thread_id: str) -> QueryResponse:
    """사전 게이트 조기 반환의 비스트림 응답(plans/179 W1a)."""
    return QueryResponse(
        query_id=query_id,
        status=gate.status,
        response=gate.response,
        thread_id=thread_id,
        **gate.extra,
    )


def _pre_gate_done(gate: PreGateResult, *, query_id: str, thread_id: str) -> dict[str, Any]:
    """사전 게이트 조기 반환의 스트림 done 페이로드(plans/179 W1a) — 키 순서 = 현행."""
    return {
        "type": SSE_DONE,
        "response": gate.response,
        "query_id": query_id,
        "thread_id": thread_id,
        **gate.extra,
    }


@dataclass(frozen=True)
class TurnContext:
    """그래프 턴의 응답·저장·done 조립 입력(plans/179 W1a) — 네 진입의 차이를 값으로 받는다.

    - `text_entry`: 텍스트 진입(`/query`·`/query/stream`) — 승인 대기 · 후단 존 역질문
      (D-143 후속2) · 소스 전환(plans/132 W5) · 제니퍼 소스 칸(plans/147)을 싣는다. 파일 진입은
      역질문이 기간 되묻기뿐이고 `source_switch`는 항상 null · done `awaiting_approval`은 항상
      False다(plans/163 D6).
    - `selected_db_ids`: 인가를 지난 DB 선택(`db_scope` 입력) · `user_query`: 질문 원문(저장 ·
      스트림 절단 부분 결과) · `owner`: 결과 소유자(`current_user["sub"]`).
    - `upload`: 파일 진입의 저장 추가 키(`uploaded_file`·`uploaded_file_name` — §14 첨부 원본 보관).
      TODO(§14.5): `_results_store`는 인메모리 dict라 원본 누적 시 메모리 증가 · 다중 워커 유실
      가능 — TTL/공유 스토리지 도입을 검토할 것.
    """

    query_id: str
    thread_id: str
    user_query: str
    selected_db_ids: list[str] | None
    owner: str | None
    text_entry: bool
    upload: dict[str, Any] | None = None


def _turn_response_data(
    ctx: TurnContext,
    output: dict[str, Any],
    state: dict[str, Any],
    *,
    mode: Literal["json", "fallback", "astream"],
    elapsed_ms: float,
    executed_sql: str | None,
    row_count: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """질의 응답 dict 한 벌(plans/179 W1a) + 기간 되묻기 페이로드(없으면 None).

    `mode`: `json`(비스트림) · `fallback`(스트림 ainvoke 폴백) · `astream`(스트림 최종 출력).
    `output`은 최종 출력(astream = 종료 노드 델타 · 그 밖 = ainvoke 결과), `state`는 누적 상태
    (astream = 체크포인트+입력+노드 델타 누적 — plans/120 S-1 · 그 밖 = `output`)다.
    키 순서는 진입·경로별 현행 그대로다(저장 dict 순서·SSE JSON 바이트에 들어간다):
    - `/query`만: `awaiting_approval`·`approval_context`(HITL) · `scope_reexpand`를
      `form_fill_clarification` 바로 뒤에 항상 싣는다(그 밖은 좁혔을 때만 · plans/123 W-2 ②).
    - `/query/file`만: `clarification`은 값이 있을 때만 싣는다.
    - astream: `dependency_notes`는 있을 때만(TP-11.8) · 기간 되묻기 결과 칸(D5) 덮어쓰기 없음.
    """
    query_json = mode == "json" and ctx.text_entry
    file_json = mode == "json" and not ctx.text_entry
    # 기간 되묻기(plans/122 T-3·T-4) — `input_parser → END`로 끝난 턴(누적 상태에서 판정)
    time_clar = _time_clarification(state)
    if ctx.text_entry:
        status = "awaiting_approval" if output.get("awaiting_approval") else "completed"
        # 존 역질문 후단 게이트(D-143 후속2): 파이프라인이 존 선택 요청으로 종결한 턴 —
        # pre-gate와 동일 shape(status="clarification" + clarification)로 프론트 UI 재사용.
        clarification = output.get("zone_clarification") or time_clar
    else:
        # 파일 경로의 역질문은 기간 되묻기뿐이다(후단 존 게이트는 텍스트 채널만 · D-143 후속2)
        status = "completed"
        clarification = time_clar
    if clarification:
        status = "clarification"
    return {
        "query_id": ctx.query_id,
        "status": status,
        "response": output.get("final_response", ""),
        "thread_id": ctx.thread_id,
        **({
            "awaiting_approval": output.get("awaiting_approval", False),
            "approval_context": output.get("approval_context"),
        } if query_json else {}),
        "has_file": output.get("output_file") is not None,
        "file_name": output.get("output_file_name"),
        "executed_sql": executed_sql,
        "row_count": row_count,
        "processing_time_ms": elapsed_ms,
        "turn_count": _count_human_messages(output.get("messages", [])),
        "has_mapping_report": output.get("mapping_report_md") is not None,
        # HITL 폼필(D-151): 미해결 필드 역질문 패널 컨텍스트(결과와 함께 첨부)
        "form_fill_clarification": output.get("form_fill_clarification"),
        # 범위 재확장(D-176 후속4): 좁혀 조회했을 때만 붙는 사후 패널 — 폼필 패널과 동형.
        # 기록만 남기고 되돌릴 길을 안 주면 좁히기의 비용이 이득을 넘는다(§5.3 불변식 6).
        **({"scope_reexpand": build_scope_reexpand(
            state.get("scope_narrowed"), state.get("user_query", "")
        )} if query_json else {}),
        "form_memory_panel": output.get("form_memory_panel"),  # D-187 저장 값 패널
        # 스레드 DB 스코프(plans/90 · D-205) — 다음 턴 승계 집합을 축 구조로 보고(4경로 대칭)
        "db_scope": build_db_scope(state, selected_db_ids=ctx.selected_db_ids),
        # 순차 처리 경과 노트(plans/88 · D-203) — astream은 있을 때만(TP-11.8)
        **(_dependency_notes_field(state) if mode == "astream"
           else {"dependency_notes": state.get("dependency_notes")}),
        **_disclosures_field(state),  # plans/123 W-8
        **_time_resolution_field(state),  # plans/122 T-4
        **({} if query_json else _scope_reexpand_field(state)),  # plans/123 W-2 ②
        **_plan_summary_field(state),  # TP-0.1
        # 존 역질문 후단 게이트(D-143 후속2) — pre-gate와 동일 키로 프론트 렌더
        **({"clarification": clarification} if clarification or not file_json else {}),
        # 소스 선택 기억을 쓴 턴의 「다른 소스로 보기」 칩(plans/132 W5) — 계획 노드가 쓰는 키라
        # 누적 상태에서 읽는다. 파일 경로는 키만 같게 두고 값은 항상 null(소스 전환 미지원 —
        # 칩이 양식 파일 없이 텍스트 경로로 재질의한다 · plans/163 D6)
        "source_switch": state.get("source_switch") if ctx.text_entry else None,
        **(_apm_source_fields(state) if ctx.text_entry else {}),  # plans/147 제니퍼 소스
        # 기간 되묻기 턴 — 직전 턴 SQL·행·파일 미탑재(D5)
        **({} if mode == "astream" else _clarify_turn_fields(time_clar)),
    }, time_clar


def _build_turn_response(
    ctx: TurnContext,
    config: Any,
    result: dict[str, Any],
    *,
    mode: Literal["json", "fallback"],
    elapsed_ms: float,
) -> dict[str, Any]:
    """ainvoke 결과 → 응답 dict(`chart` 포함) + 결과 저장.

    비스트림·스트림 폴백이 공유한다(plans/179 W1a).
    """
    response_data, time_clar = _turn_response_data(
        ctx, result, result, mode=mode, elapsed_ms=elapsed_ms,
        executed_sql=_executed_sql(result), row_count=len(result.get("query_results", [])),
    )
    store_data = {
        **response_data,
        "output_file": result.get("output_file"),
        "mapping_report_md": result.get("mapping_report_md"),
        "query_results": result.get("query_results", []),
        **_clarify_store_fields(time_clar),
        **(ctx.upload or {}),  # §14 첨부 원본(파일 진입)
    }
    # 그래프 보기 신호(plans/160 A-2) — 저장하는 행 그대로로 판정한다(되묻기 턴은 빈 행)
    tables = result_tables_of(result)
    response_data["chart"] = chart_field(
        config, store_data["query_results"], tables, status=response_data["status"])
    _store_result(ctx.query_id, {
        **store_data,
        **viz_store_fields(response_data["chart"], user_query=ctx.user_query, tables=tables),
    }, owner=ctx.owner)
    return response_data


def _build_stream_turn_response(
    ctx: TurnContext,
    config: Any,
    output: dict[str, Any],
    scope_state: dict[str, Any],
    *,
    elapsed_ms: float,
    executed_sql: str | None,
    row_count: int,
    tracked_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """astream 최종 출력 → 응답 dict(`chart` 포함) + 결과 저장 — 두 스트림 진입 공유(plans/179 W1a).

    응답 칸은 종료 노드 델타가 아니라 누적 상태(`scope_state`)에서 읽는다(plans/120 S-1).
    저장 행은 최종 출력 행, 없으면 앞 노드에서 추적한 행(`tracked_rows`)이다.
    """
    response_data, _ = _turn_response_data(
        ctx, output, scope_state, mode="astream", elapsed_ms=elapsed_ms,
        executed_sql=executed_sql, row_count=row_count,
    )
    rows = output.get("query_results") or tracked_rows
    # 그래프 보기 신호(plans/160 A-2) — 표 경계는 누적 상태에서
    tables = result_tables_of(scope_state)
    response_data["chart"] = chart_field(config, rows, tables, status=response_data["status"])
    _store_result(ctx.query_id, {
        **response_data,
        "output_file": output.get("output_file"),
        "mapping_report_md": output.get("mapping_report_md"),
        "query_results": rows,
        **viz_store_fields(response_data["chart"], user_query=ctx.user_query, tables=tables),
        **(ctx.upload or {}),  # §14 첨부 원본(파일 진입)
    }, owner=ctx.owner)
    return response_data


def _turn_done_payload(
    ctx: TurnContext,
    response_data: dict[str, Any],
    output: dict[str, Any],
    *,
    elapsed_ms: float,
    watch: StreamWatch,
) -> dict[str, Any]:
    """응답 dict → SSE done 페이로드 — 두 스트림 진입 × astream·폴백 공유(plans/179 W1a).

    `output`은 승인 대기 판정 입력(astream 최종 출력 · 폴백 ainvoke 결과)이다 — 파일 진입은 승인
    대기를 내지 않는다(astream·폴백 같은 키 · plans/163 D6).
    """
    return {
        "type": SSE_DONE,
        "response": response_data["response"],
        "query_id": ctx.query_id,
        "thread_id": ctx.thread_id,
        "processing_time_ms": elapsed_ms,
        "row_count": response_data["row_count"],
        "executed_sql": response_data["executed_sql"],
        "has_file": response_data["has_file"],
        "file_name": response_data.get("file_name"),
        "awaiting_approval": (
            output.get("awaiting_approval", False) if ctx.text_entry else False
        ),
        "turn_count": response_data["turn_count"],
        "has_mapping_report": response_data.get("has_mapping_report", False),
        "form_fill_clarification": response_data.get("form_fill_clarification"),
        "form_memory_panel": response_data.get("form_memory_panel"),  # D-187 저장 값 패널
        "db_scope": response_data.get("db_scope"),  # D-205
        **_dependency_notes_field(response_data),  # TP-11.8
        **_disclosures_field(response_data),  # plans/123 W-8
        **_time_resolution_field(response_data),  # plans/122 T-4
        **_scope_reexpand_field(response_data),  # plans/123 W-2 ②
        **_plan_summary_carry(response_data),  # TP-0.1
        # 존 역질문 후단 게이트(D-143 후속2) — pre-gate done 이벤트와 동일 키(값이 없어도 null)
        "clarification": response_data.get("clarification"),
        "source_switch": response_data.get("source_switch"),  # plans/132 W5
        **(_apm_source_fields(response_data) if ctx.text_entry else {}),  # plans/147
        **chart_carry(response_data["chart"]),  # plans/160 A-2
        **_rewrite_trace_fields(ctx.thread_id),  # plans/107 §4.9
        "timeline": _finish_timeline(watch, ctx.query_id, done=True),  # plans/119 T-0 단계 타임라인
    }


async def _partial_timeout_response(
    ctx: TurnContext,
    config: Any,
    graph: Any,
    thread_config: dict[str, Any],
    *,
    start_time: float,
    limit_sec: float,
) -> dict[str, Any] | None:
    """비스트림 처리 상한 초과 턴의 부분 결과 본문 + 저장 — 행이 없으면 None(호출자가 504).

    G-E(plans/114 P-2): 조회까지 끝내고 서술에서 상한을 넘긴 턴은 행을 버리지 않는다 — 양식은
    못 채웠어도 조회한 행은 준다. 두 비스트림 진입 공유(plans/179 W1a).
    """
    partial = await _partial_on_timeout(graph, thread_config)
    if partial is None:
        return None
    data = _partial_response_data(
        partial, query_id=ctx.query_id, thread_id=ctx.thread_id,
        start_time=start_time, limit_sec=limit_sec,
    )
    chart = chart_field(config, partial.rows, None)   # plans/160 A-2
    data.update(chart_carry(chart))
    _store_result(ctx.query_id, {
        **data, "query_results": partial.rows,
        **viz_store_fields(chart, user_query=ctx.user_query, tables=None),
    }, owner=ctx.owner)
    return data


async def _stream_graph_turn(
    request: Request,
    graph: Any,
    config: Any,
    graph_input: Any,
    thread_config: dict[str, Any],
    *,
    ctx: TurnContext,
    scope_seed: dict[str, Any],
    limit_sec: float,
    fallback_timeout_message: str,
) -> AsyncGenerator[str, None]:
    """두 스트림 진입(`/query/stream`·`/query/file/stream`)의 SSE 본문 공유(plans/168 A1).

    astream 루프(상한 절단·heartbeat·진행·노드 시작/완료·토큰) → 최종 출력의 토큰 보충·META →
    응답·저장(`_build_stream_turn_response`) → done. astream 이 출력 없이 끝나거나 미지원이면
    ainvoke 폴백 → 토큰·META → 응답·저장(`_build_turn_response`) → done. done 페이로드는
    `_turn_done_payload` 한 벌이다 — 진입별 차이는 `ctx`(`TurnContext`)가 싣는다(plans/179 W1a).

    빌더는 이 함수의 `try` 안에서 불린다 — 빌더 예외도 종전대로 폴백·오류 이벤트로 간다.
    `scope_seed`(db_scope 누적 상태의 시작값)는 노드 델타가 덮어쓰므로 호출자가 새 dict로 넘긴다.
    """
    start_time = time.time()
    streamed_any_token = False
    _seen_nodes: set[str] = set()
    _current_node: str | None = None
    _tracked_row_count: int = 0
    _tracked_query_results: list[dict[str, Any]] = []
    _sql_state: dict[str, Any] = {}   # 실행 SQL 출처 누적(plans/116 §10.3)
    # db_scope 입력 — 시작값 + 노드 델타 누적(plans/120 S-1 · ainvoke 결과와 같은 값)
    _scope_state: dict[str, Any] = scope_seed
    _trace = StreamTrace()   # 실패 시 경위(D-242)
    # 상한 세 시계(T-5) + 단계 타임라인(T-0) — plans/119 · D-267 ⑦
    _watch = _stream_watch(config, limit_sec, start_time)
    # 마감 바인딩(T-1) — 생산자 태스크·폴백 ainvoke 가 만들어지기 전에 묶는다
    _deadline_token = _bind_deadline(graph_input, config, stream=True)

    try:
        if hasattr(graph, "astream_events"):
            try:
                # 이벤트 fetch마다 타임아웃을 건다(D-066 후속). 노드 내부 LLM 호출이
                # 응답 없이 멈추면 astream_events가 다음 이벤트를 영영 못 내놓아 SSE가
                # 무한 hang된다(healthcheck만 도는 증상). wait_for로 stuck fetch를 끊는다.
                # plans/89 §3.2-④ · D-204: 생산자 태스크 + 큐. 그 사이 heartbeat를 낸다.
                # wait_for(__anext__) 재호출 금지. plans/119 T-5: 무이벤트 안전 상한은 전체
                # 상한(처리 상한 + 전달 연장)이고, 상한 판정은 `wake_at` tick으로 제때 한다.
                _progress_on = bool(getattr(config.server, "sse_progress_events", True))
                _hb = (
                    float(config.server.sse_heartbeat_interval_sec or 0)
                    if _progress_on else 0.0
                )
                _ev_payload: Any   # kind 별 페이로드 — "event"·"heartbeat"는 dict(D-204)
                async with contextlib.aclosing(_graph_event_stream(
                    graph, graph_input, thread_config,
                    idle_timeout=_watch.caps.hard_cap_sec, heartbeat_interval=_hb,
                    wake_at=_watch.next_check_at,
                )) as _events:
                    async for _ev_kind, _ev_payload in _events:
                        # 상한 세 시계(plans/119 T-5 · G-7) — 첫 답변 전 처리 상한 ·
                        # 첫 답변 뒤 토큰 간 idle · 전체 상한(CU-11 hang 방지).
                        # heartbeat는 시계를 되돌리지 않는다.
                        _cut = _watch.cut()
                        if _cut is None and _ev_kind == "timeout":
                            _cut = CUT_HARD_CAP if _watch.caps.answered else CUT_PROCESSING
                        if _cut is not None:
                            yield await _stream_cut_sse_event(
                                graph, thread_config,
                                watch=_watch, kind=_cut,
                                tracked_rows=_tracked_query_results,
                                query_id=ctx.query_id, thread_id=ctx.thread_id,
                                trace=_trace,
                                message="처리 시간이 초과되었습니다. 질의를 단순화해주세요.",
                                owner=ctx.owner,
                                config=config, user_query=ctx.user_query,   # plans/160 A-2
                            )
                            return
                        if _ev_kind == "tick":
                            continue
                        if _ev_kind == "heartbeat":
                            yield _sse_event(_heartbeat_sse_payload(start_time, _ev_payload))
                            continue
                        event = _ev_payload
                        kind = event.get("event", "")
                        name = event.get("name", "")
                        _watch.observe(event, root=not _is_subgraph_event(event))

                        # 응답 선행 본문(코드 렌더 표 · plans/119 N-1) — 답변 토큰이다
                        if kind == "on_custom_event" and name == ANSWER_PREFIX_EVENT:
                            _prefix = _answer_prefix_text(event)
                            if _prefix:
                                streamed_any_token = True
                                _watch.answer_sent(_prefix, prefix=True)
                                yield _sse_event({"type": SSE_TOKEN, "content": _prefix})
                            continue

                        # 도구·커스텀 이벤트 → progress (plans/89 T3)
                        _prog = _progress_sse_payload(event, _current_node, start_time)
                        if _prog is not None:
                            _trace.observe_progress(_prog)
                            if _progress_on:
                                yield _sse_event(_prog)
                                continue

                        # 노드 시작 이벤트 감지
                        if kind == "on_chain_start" and name in _STREAM_KNOWN_NODES:
                            _trace.node_started(name, (time.time() - start_time) * 1000)
                        if kind == "on_chain_start" and name and name not in _seen_nodes:
                            _known_nodes = _STREAM_KNOWN_NODES
                            if name in _known_nodes:
                                _seen_nodes.add(name)
                                _current_node = name
                                yield _sse_event({
                                    "type": SSE_NODE_START,
                                    "node": name,
                                    "timestamp_ms": (time.time() - start_time) * 1000,
                                })

                        # 노드 완료 이벤트
                        if kind == "on_chain_end" and name:
                            node_output = event.get("data", {}).get("output", {})
                            if isinstance(node_output, dict) and name in _seen_nodes:
                                _trace.node_ended(
                                    name, node_output, (time.time() - start_time) * 1000
                                )
                                # query_results를 반환하는 노드에서 추적
                                if name in ("query_executor", "multi_db_executor", "result_merger"):
                                    node_qr = node_output.get("query_results")
                                    if isinstance(node_qr, list):
                                        _tracked_row_count = len(node_qr)
                                        _tracked_query_results = node_qr
                                progress_data = _extract_node_progress(
                                    name, node_output, security=config.security
                                )
                                # 진행 데이터가 없어도 완료를 알린다 — 노드 시간 경계다
                                # (plans/120 V-6: 3단 multi_db_executor·result_merger 는
                                # 추출 분기가 없어 완료 이벤트가 0건 → 하네스가 구간을 잃었다).
                                yield _sse_event({
                                    "type": SSE_NODE_COMPLETE,
                                    "node": name,
                                    "data": progress_data or {},
                                    "timestamp_ms": (time.time() - start_time) * 1000,
                                })

                        # LLM 토큰 스트리밍 (output_generator, general_inference 노드)
                        # 종료 노드 출력엔 SQL 이 없어 앞 노드 출력에서 모은다(plans/116 §10.3)
                        if kind == "on_chain_end" and not _is_subgraph_event(event):
                            _root_out = event.get("data", {}).get("output")
                            _track_sql_state(_sql_state, _root_out)
                            _merge_node_delta(_scope_state, _root_out)   # plans/120 S-1

                        if kind == "on_chat_model_stream":
                            # 최종 사용자 응답(USER_RESPONSE_TAG)으로 태깅된 LLM 호출의
                            # 토큰만 전달한다. orchestration 경로에서는 SQL 생성·DB 분류 등
                            # 중간 LLM 호출이 같은 노드(agent_orchestrator)에서 일어나므로
                            # 노드명이 아닌 태그로 구분해야 토큰이 새지 않는다.
                            _tags = event.get("tags", []) or []
                            _event_node = event.get("metadata", {}).get(
                                "langgraph_node", _current_node or ""
                            )
                            if USER_RESPONSE_TAG in _tags or _event_node in (
                                "output_generator", "general_inference"
                            ):
                                chunk = event.get("data", {}).get("chunk")
                                token_text = _token_text(chunk) if chunk else ""
                                if token_text:
                                    streamed_any_token = True
                                    _watch.answer_sent(token_text)   # 첫 답변·idle 시계(T-5)
                                    yield _sse_event({
                                        "type": SSE_TOKEN,
                                        "content": token_text,
                                    })

                        elif kind == "on_chain_end" and not _is_subgraph_event(event):
                            output = event.get("data", {}).get("output", {})
                            if isinstance(output, dict) and "final_response" in output:
                                elapsed_ms = (time.time() - start_time) * 1000

                                if not streamed_any_token:
                                    _watch.answer_sent(
                                        output.get("final_response", ""), final=True
                                    )
                                    yield _sse_event({
                                        "type": SSE_TOKEN,
                                        "content": output.get("final_response", ""),
                                    })

                                # output_generator 노드 출력에는 query_results가 없으므로
                                # 이전 노드에서 추적한 _tracked_row_count 사용
                                _final_row_count = (
                                    len(output.get("query_results", [])) or _tracked_row_count
                                )

                                yield _sse_event({
                                    "type": SSE_META,
                                    "executed_sql": _executed_sql(_sql_state),
                                    "row_count": _final_row_count,
                                })

                                _response = _build_stream_turn_response(
                                    ctx, config, output, _scope_state,
                                    elapsed_ms=elapsed_ms,
                                    executed_sql=_executed_sql(_sql_state),
                                    row_count=_final_row_count,
                                    tracked_rows=_tracked_query_results,
                                )
                                _done = _turn_done_payload(
                                    ctx, _response, output,
                                    elapsed_ms=elapsed_ms, watch=_watch,
                                )
                                _drain = ctx.text_entry and bool(
                                    output.get("apm_source_pending")
                                    or output.get("apm_source_scope")
                                )
                                yield _sse_event(_done)
                                if _drain:
                                    # 제니퍼 소스 되묻기 대기·승계(plans/147)는 종료
                                    # 노드가 쓰는 스레드 칸이다 — 바로 끝내면 생산자가
                                    # 취소돼 그 노드의 체크포인트 쓰기가 사라진다
                                    # (LangGraph 실측). 이 턴만 그래프 끝까지 받는다
                                    # (텍스트 진입만 — 파일 진입은 제니퍼 소스 칸이 없다).
                                    async for _ in _events:
                                        pass
                                return

                if not streamed_any_token:
                    raise AttributeError("astream_events did not produce output")

            except (AttributeError, TypeError, NotImplementedError):
                pass

        # Fallback: ainvoke
        result = await asyncio.wait_for(
            graph.ainvoke(graph_input, thread_config),
            timeout=limit_sec,
        )

        elapsed_ms = (time.time() - start_time) * 1000

        final_response = result.get("final_response", "")
        _watch.answer_sent(final_response, final=True)
        yield _sse_event({"type": SSE_TOKEN, "content": final_response})
        # 기간 되묻기(plans/122 T-3·T-4) — 조회하지 않은 턴이라 직전 턴 SQL·행을 싣지 않는다(D5)
        _time_clar = _time_clarification(result)
        _turn_fields = _clarify_turn_fields(_time_clar)

        yield _sse_event({
            "type": SSE_META,
            "executed_sql": _executed_sql(result),
            "row_count": len(result.get("query_results", [])),
            **{k: v for k, v in _turn_fields.items() if k in ("executed_sql", "row_count")},
        })

        _response = _build_turn_response(
            ctx, config, result, mode="fallback", elapsed_ms=elapsed_ms,
        )
        yield _sse_event(_turn_done_payload(
            ctx, _response, result, elapsed_ms=elapsed_ms, watch=_watch,
        ))

    except TimeoutError:  # asyncio.TimeoutError 와 같은 클래스(3.11+)
        # 폴백 ainvoke 는 첫 답변 개념이 없다 — 처리 상한 그대로(plans/119 T-5)
        yield await _stream_cut_sse_event(
            graph, thread_config,
            watch=_watch, kind=CUT_PROCESSING,
            tracked_rows=_tracked_query_results,
            query_id=ctx.query_id, thread_id=ctx.thread_id,
            trace=_trace,
            message=fallback_timeout_message,
            owner=ctx.owner,
            config=config, user_query=ctx.user_query,   # plans/160 A-2
        )
    except Exception as e:
        yield _sse_event(_stream_error_payload(
            user_error_detail(_GRAPH_ERROR_MESSAGE, e, logger, request),   # plans/163 S3
            _trace, code="exception", start_time=start_time, limit_sec=limit_sec,
            timeline=_finish_timeline(_watch, ctx.query_id),
        ))
    finally:
        _unbind_deadline(_deadline_token)


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

    # 감사(D-183): 역질문으로 조기 반환하는 경로에서도 "물었다"는 사실은 남아야 하므로
    # 파이프라인 진입 전에 기록한다.
    await _audit_user_request(
        request,
        current_user,
        user_query=body.query,
        output_format=getattr(body.output_format, "value", str(body.output_format)),
        has_file=False,
        thread_id=thread_id,
    )
    # 질의응답 스레드 기록(D-248) — 아래 반환 지점마다 turn.response()로 감싼다
    turn = TurnRecorder(request, current_user, user_query=body.query, has_upload=False)
    _shadow_input_guard(body.query, thread_id)  # plans/123 S-2 섀도 — 응답 불변

    thread_config = {"configurable": {"thread_id": thread_id}}

    # 체크포인트에서 이전 State 확인
    checkpoint_state = await _get_checkpoint_state(graph, thread_config)

    # DB·소스 선택 인가(plans/104 C-4 — 존 게이트보다 먼저) → 거부 반환 → 사전 게이트(존 역질문·
    # 범위 사전 선택 → 저장 값 패널 삭제) — 파이프라인 실행 전 결정적 조기 반환(네 진입 공유 ·
    # plans/179 W1a)
    body.selected_db_ids, gate = await _run_pre_gates(
        body.selected_db_ids, current_user, config, thread_id=thread_id,
        body=body, checkpoint_state=checkpoint_state,
    )
    if gate is not None:
        return cast(QueryResponse, await turn.response(
            _pre_gate_response(gate, query_id=query_id, thread_id=thread_id)
        ))

    # 승인 의사 해소는 async(LLM 보조 옵트인)라 동기 조립 헬퍼 밖에서 수행한다 — 두 텍스트
    # 라우트가 동일하게 호출해야 한다(SSE만 빠지는 비대칭 재발 방지, D-066).
    approval = await _resolve_turn_approval(body, checkpoint_state, config)
    input_state = _build_turn_input_state(
        body, thread_id, checkpoint_state, current_user, approval=approval, config=config
    )
    await _audit_scope_narrowed(input_state, current_user, thread_id)

    # FIX-18: 폼필 답변 턴은 양식 재채움 전체 파이프라인(파일 런과 동일 부하)이므로
    # 텍스트 타임아웃이 아니라 파일 타임아웃을 적용한다(라이브 실측 2026-07-31:
    # 답변 턴 조기 타임아웃 — 폼필 런 소요가 query_timeout을 상회).
    # 처리 상한 = 첫 답변까지(plans/119 G-7) — 비스트림은 곧 전체 상한이다(네 진입점 공유 함수).
    effective_timeout = _processing_limit(
        config, file_turn=bool(input_state.get("form_fill_answers"))
    )
    input_state["request_deadline"] = _request_deadline(effective_timeout)   # plans/118 P-1
    ctx = TurnContext(
        query_id=query_id, thread_id=thread_id, user_query=body.query,
        selected_db_ids=body.selected_db_ids, owner=current_user.get("sub"), text_entry=True,
    )

    _deadline_token = _bind_deadline(input_state, config, stream=False)   # plans/119 T-1 — wait_for 전에 묶는다
    try:
        result = await asyncio.wait_for(
            graph.ainvoke(input_state, thread_config),
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        # G-E(plans/114 P-2) — 행이 있으면 부분 결과, 없으면 종전 504 그대로다.
        partial = await _partial_timeout_response(
            ctx, config, graph, thread_config,
            start_time=start_time, limit_sec=effective_timeout,
        )
        if partial is not None:
            return cast(QueryResponse, await turn.response(QueryResponse(**partial)))
        raise HTTPException(
            status_code=504,
            detail="처리 시간이 초과되었습니다. 질의를 단순화해주세요.",
        )
    except Exception as e:
        # 예외 원문은 서버 로그에만 — 응답은 고정 문구 + 요청 번호(plans/163 S3)
        raise HTTPException(
            status_code=500,
            detail=user_error_detail(_GRAPH_ERROR_MESSAGE, e, logger, request),
        )
    finally:
        _unbind_deadline(_deadline_token)

    elapsed_ms = (time.time() - start_time) * 1000
    # 응답 구성·결과 저장 — 네 진입 공유(plans/179 W1a)
    response_data = _build_turn_response(
        ctx, config, result, mode="json", elapsed_ms=elapsed_ms
    )
    return cast(QueryResponse, await turn.response(QueryResponse(**response_data)))


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

    # 감사(D-183): 역질문으로 조기 반환하는 경로에서도 "물었다"는 사실은 남아야 하므로
    # 파이프라인 진입 전에 기록한다.
    await _audit_user_request(
        request,
        current_user,
        user_query=body.query,
        output_format=getattr(body.output_format, "value", str(body.output_format)),
        has_file=False,
        thread_id=thread_id,
    )
    # 질의응답 스레드 기록(D-248) — 아래 스트림마다 turn.stream()으로 감싼다(/query와 대칭)
    turn = TurnRecorder(request, current_user, user_query=body.query, has_upload=False)
    _shadow_input_guard(body.query, thread_id)  # plans/123 S-2 섀도 — 응답 불변

    thread_config = {"configurable": {"thread_id": thread_id}}

    # 체크포인트에서 이전 State 확인
    checkpoint_state = await _get_checkpoint_state(graph, thread_config)

    # 선택 인가 → 거부 반환 → 사전 게이트 — /query와 대칭(네 진입 공유 · plans/179 W1a)
    body.selected_db_ids, gate = await _run_pre_gates(
        body.selected_db_ids, current_user, config, thread_id=thread_id,
        body=body, checkpoint_state=checkpoint_state,
    )
    if gate is not None:
        # 선택 거부 응답만 현행대로 `X-Accel-Buffering` 헤더가 없다(바이트 불변 보존)
        return _sse_response(
            turn.stream(_single_done_stream(
                _pre_gate_done(gate, query_id=query_id, thread_id=thread_id)
            )),
            accel_buffering=gate.kind != "selection_denied",
        )

    # /query와 동일 조립(단일 출처) — SSE 경로에만 D-064 초기화가 빠졌던 비대칭 재발 방지
    approval = await _resolve_turn_approval(body, checkpoint_state, config)
    input_state = _build_turn_input_state(
        body, thread_id, checkpoint_state, current_user, approval=approval, config=config
    )
    await _audit_scope_narrowed(input_state, current_user, thread_id)

    # FIX-18: 폼필 답변 턴은 파일 런과 동일 부하 — 파일 타임아웃 적용(/query와 대칭)
    # 처리 상한 = 첫 답변까지(plans/119 G-7) — 네 진입점 공유 함수(D-066)
    effective_timeout = _processing_limit(
        config, file_turn=bool(input_state.get("form_fill_answers"))
    )
    input_state["request_deadline"] = _request_deadline(effective_timeout)   # plans/118 P-1
    ctx = TurnContext(
        query_id=query_id, thread_id=thread_id, user_query=body.query,
        selected_db_ids=body.selected_db_ids, owner=current_user.get("sub"), text_entry=True,
    )

    # 공통 SSE 본문(plans/168 A1) — 응답·저장·done 조립도 공유 빌더 한 벌(plans/179 W1a)
    return _sse_response(turn.stream(_stream_graph_turn(
        request, graph, config, input_state, thread_config,
        ctx=ctx,
        # db_scope 입력 — 체크포인트 + 입력 + 노드 델타 누적(plans/120 S-1 · ainvoke 결과와 같은 값)
        scope_seed={**(checkpoint_state or {}), **input_state},
        limit_sec=effective_timeout,
        fallback_timeout_message="처리 시간이 초과되었습니다. 질의를 단순화해주세요.",
    )))


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
    # 감사(D-183): 파일 경로도 텍스트 경로와 대칭으로 기록한다
    # (한쪽만 기록하는 비대칭은 이 저장소의 반복 실수 유형이다).
    # query_id·thread_id는 요청 수신 시점에 정한다 — 텍스트 경로(`body.thread_id or query_id`)와
    # 같은 값이 감사(D-183)·사전 게이트 응답·그래프 실행에 함께 쓰인다(plans/163 D6).
    query_id = str(uuid.uuid4())
    actual_thread_id = thread_id or query_id
    await _audit_user_request(
        request,
        current_user,
        user_query=query,
        output_format="file",
        has_file=True,
        thread_id=actual_thread_id,
    )
    # 질의응답 스레드 기록(D-248) — 텍스트 경로와 대칭
    turn = TurnRecorder(request, current_user, user_query=query, has_upload=True)
    _shadow_input_guard(query, thread_id)  # plans/123 S-2 섀도 — 응답 불변

    # 1. 파일 타입 검증
    file_ext = _get_file_extension(file.filename)
    if file_ext not in ("xlsx", "docx"):
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 파일 형식입니다: .{file_ext}. .xlsx 또는 .docx만 지원합니다.",
        )

    # 1.5 존 역질문 게이트 (Plan 75 §4 파일 경로 확장) — 무거운 처리 전에 조기 반환
    selected_list = _parse_selected_db_ids_form(selected_db_ids)
    # 요청이 지정한 DB 선택의 인가(plans/104 C-4 · D-232) → 거부 반환 → 파일 존 역질문 —
    # 텍스트 경로와 대칭(네 진입 공유 · plans/179 W1a)
    selected_list, gate = await _run_pre_gates(
        selected_list, current_user, request.app.state.config,
        thread_id=actual_thread_id, file_query=query,
    )
    if gate is not None:
        return cast(QueryResponse, await turn.response(
            _pre_gate_response(gate, query_id=query_id, thread_id=actual_thread_id)
        ))

    # 2. 파일 크기 검증 (최대 10MB)
    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="파일 크기가 10MB를 초과합니다.")

    # 2.5 DRM 해제 (Plan 74) — 평문은 통과, 암호문은 복호화. /query/file/stream과 대칭.
    file_bytes = await _resolve_uploaded_bytes(
        file_bytes,
        file_ext,
        file.filename,
        request.app.state.config,
        user_id=current_user.get("sub"),
    )

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

    # 4. 초기 State 생성 (query_id·actual_thread_id는 요청 수신 시점에 정했다)
    start_time = time.time()

    graph = request.app.state.graph
    config = request.app.state.config

    initial_state = create_initial_state(
        user_query=_substitute_zone_placeholder(query, selected_list),
        raw_user_query=_raw_query_seed(query, config),
        uploaded_file=file_bytes,
        file_type=file_ext,
        thread_id=actual_thread_id,
        csv_sheet_data=csv_sheet_data,
        user_id=current_user.get("sub"),
        user_department=current_user.get("department"),
        user_role=current_user.get("role"),
        allowed_db_ids=current_user.get("allowed_db_ids"),
        allowed_sources=current_user.get("allowed_sources"),
        selected_db_ids=selected_list,
        # 폼필은 전량 채움이 기본 — 기본 LIMIT(1000) 절단 방지(실측: 지시문에 "모든"이
        # 없으면 1,000행 절단). 명시 건수("100건")는 resolve_query_limit이 우선 반영.
        resolved_limit=resolve_query_limit(query, _FORM_FILL_DEFAULT_LIMIT),
    )
    # 원문만으로 정해지는 턴 단위 고지(plans/123 W-4 · S-1) — 텍스트 라우트와 대칭
    initial_state["turn_disclosures"] = _turn_disclosures(query, selected_list)
    # 처리 상한 = 첫 답변까지(plans/119 G-7) — 비스트림은 곧 전체 상한이다(네 진입점 공유 함수).
    limit_sec = _processing_limit(config, file_turn=True)
    # 파일 경로도 같은 마감을 싣는다(plans/118 P-1 · 진입점 4곳 대칭 D-066).
    initial_state["request_deadline"] = _request_deadline(limit_sec)

    thread_config = {"configurable": {"thread_id": actual_thread_id}}
    ctx = TurnContext(
        query_id=query_id, thread_id=actual_thread_id, user_query=query,
        selected_db_ids=selected_list, owner=current_user.get("sub"), text_entry=False,
        upload={"uploaded_file": file_bytes, "uploaded_file_name": file.filename},
    )

    # 5. 그래프 실행
    _deadline_token = _bind_deadline(initial_state, config, stream=False)   # plans/119 T-1 — wait_for 전에 묶는다
    try:
        result = await asyncio.wait_for(
            graph.ainvoke(initial_state, thread_config),
            timeout=limit_sec,
        )
    except asyncio.TimeoutError:
        # G-E(plans/114 P-2) — 텍스트 경로와 대칭. 양식은 못 채웠어도 조회한 행은 준다.
        partial = await _partial_timeout_response(
            ctx, config, graph, thread_config, start_time=start_time, limit_sec=limit_sec,
        )
        if partial is not None:
            return cast(QueryResponse, await turn.response(QueryResponse(**partial)))
        raise HTTPException(status_code=504, detail="처리 시간이 초과되었습니다.")
    except Exception as e:
        # 예외 원문은 서버 로그에만 — 응답은 고정 문구 + 요청 번호(plans/163 S3)
        raise HTTPException(
            status_code=500,
            detail=user_error_detail(_GRAPH_ERROR_MESSAGE, e, logger, request),
        )
    finally:
        _unbind_deadline(_deadline_token)

    elapsed_ms = (time.time() - start_time) * 1000
    # 응답 구성·결과 저장(업로드 원본 보관 포함) — 네 진입 공유(plans/179 W1a)
    response_data = _build_turn_response(
        ctx, config, result, mode="json", elapsed_ms=elapsed_ms
    )
    return cast(QueryResponse, await turn.response(QueryResponse(**response_data)))


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
    # 감사(D-183): 파일 경로도 텍스트 경로와 대칭으로 기록한다
    # (한쪽만 기록하는 비대칭은 이 저장소의 반복 실수 유형이다).
    # query_id·thread_id는 요청 수신 시점에 정한다 — 텍스트 경로(`body.thread_id or query_id`)와
    # 같은 값이 감사(D-183)·사전 게이트 응답·그래프 실행에 함께 쓰인다(plans/163 D6).
    query_id = str(uuid.uuid4())
    actual_thread_id = thread_id or query_id
    await _audit_user_request(
        request,
        current_user,
        user_query=query,
        output_format="file",
        has_file=True,
        thread_id=actual_thread_id,
    )
    # 질의응답 스레드 기록(D-248) — 텍스트 경로와 대칭
    turn = TurnRecorder(request, current_user, user_query=query, has_upload=True)
    _shadow_input_guard(query, thread_id)  # plans/123 S-2 섀도 — 응답 불변

    file_ext = _get_file_extension(file.filename)
    if file_ext not in ("xlsx", "docx"):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"detail": f"지원하지 않는 파일 형식: .{file_ext}"},
        )

    # 존 역질문 게이트 (Plan 75 §4 파일 경로 확장) — /query/file과 대칭
    selected_list = _parse_selected_db_ids_form(selected_db_ids)
    # 요청이 지정한 DB 선택의 인가(plans/104 C-4 · D-232) → 거부 반환 → 파일 존 역질문 —
    # 네 진입 모두 같은 게이트를 지난다(plans/179 W1a)
    selected_list, gate = await _run_pre_gates(
        selected_list, current_user, request.app.state.config,
        thread_id=actual_thread_id, file_query=query,
    )
    if gate is not None:
        return _sse_response(turn.stream(_single_done_stream(
            _pre_gate_done(gate, query_id=query_id, thread_id=actual_thread_id)
        )))

    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"detail": "파일 크기가 10MB를 초과합니다."},
        )

    # DRM 해제 (Plan 74) — /query/file과 대칭. SSE 경로는 HTTPException 대신
    # JSONResponse로 반환해야 하므로 여기서 변환한다.
    try:
        file_bytes = await _resolve_uploaded_bytes(
            file_bytes,
            file_ext,
            file.filename,
            request.app.state.config,
            user_id=current_user.get("sub"),
        )
    except HTTPException as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})

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

    graph = request.app.state.graph
    config = request.app.state.config

    initial_state = create_initial_state(
        user_query=_substitute_zone_placeholder(query, selected_list),
        raw_user_query=_raw_query_seed(query, config),
        uploaded_file=file_bytes,
        file_type=file_ext,
        thread_id=actual_thread_id,
        csv_sheet_data=csv_sheet_data,
        user_id=current_user.get("sub"),
        user_department=current_user.get("department"),
        user_role=current_user.get("role"),
        allowed_db_ids=current_user.get("allowed_db_ids"),
        allowed_sources=current_user.get("allowed_sources"),
        selected_db_ids=selected_list,
        # 폼필은 전량 채움이 기본 — 기본 LIMIT(1000) 절단 방지(실측: 지시문에 "모든"이
        # 없으면 1,000행 절단). 명시 건수("100건")는 resolve_query_limit이 우선 반영.
        resolved_limit=resolve_query_limit(query, _FORM_FILL_DEFAULT_LIMIT),
    )
    # 원문만으로 정해지는 턴 단위 고지(plans/123 W-4 · S-1) — 텍스트 라우트와 대칭
    initial_state["turn_disclosures"] = _turn_disclosures(query, selected_list)
    # 처리 상한 = 첫 답변까지(plans/119 G-7) — 네 진입점 공유 함수(D-066)
    limit_sec = _processing_limit(config, file_turn=True)
    # 파일 경로도 같은 마감을 싣는다(plans/118 P-1 · 진입점 4곳 대칭 D-066).
    initial_state["request_deadline"] = _request_deadline(limit_sec)

    thread_config = {"configurable": {"thread_id": actual_thread_id}}
    ctx = TurnContext(
        query_id=query_id, thread_id=actual_thread_id, user_query=query,
        selected_db_ids=selected_list, owner=current_user.get("sub"), text_entry=False,
        upload={"uploaded_file": file_bytes, "uploaded_file_name": file.filename},
    )

    # 공통 SSE 본문(plans/168 A1) — 응답·저장·done 조립도 공유 빌더 한 벌(plans/179 W1a)
    return _sse_response(turn.stream(_stream_graph_turn(
        request, graph, config, initial_state, thread_config,
        ctx=ctx,
        # db_scope 입력 — 전체 초기 상태 + 노드 델타 누적(plans/120 S-1 · 텍스트 스트림과 대칭)
        scope_seed=dict(initial_state),
        limit_sec=limit_sec,
        fallback_timeout_message="처리 시간이 초과되었습니다.",
    )))


@router.get(
    "/query/{query_id}/result",
    response_model=QueryResponse,
)
async def get_query_result(
    request: Request,
    query_id: str,
    current_user: dict[str, Any] = Depends(require_user),
) -> QueryResponse:
    """비동기 질의의 결과를 조회한다(질의한 사용자·관리자만)."""
    stored = _owned_result(request, query_id, current_user)
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
async def download_mapping_report(
    request: Request,
    query_id: str,
    current_user: dict[str, Any] = Depends(require_user),
) -> StreamingResponse:
    """매핑 보고서 MD 파일을 다운로드한다(질의한 사용자·관리자만)."""
    stored = _owned_result(request, query_id, current_user)
    report_md = stored.get("mapping_report_md")

    if not report_md:
        raise HTTPException(status_code=404, detail="매핑 보고서가 없습니다.")

    report_bytes = report_md.encode("utf-8")
    file_name = f"mapping_report_{query_id[:8]}.md"
    response = StreamingResponse(
        io.BytesIO(report_bytes),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": _attachment_disposition(file_name)
        },
    )
    await _audit_file_download(
        request, current_user, file_name=file_name, file_type="md", file_size=len(report_bytes)
    )
    return response


@router.post("/query/mapping-feedback")
async def process_mapping_feedback(
    request: Request,
    file: UploadFile = File(...),
    query_id: str = Form(...),
    current_user: dict[str, Any] = Depends(require_user),
) -> dict:
    """수정된 매핑 보고서 MD 파일을 업로드하여 Redis에 반영한다.

    사용자가 매핑 보고서를 다운로드 -> 수정 -> 업로드하면
    원본과 비교하여 변경사항을 Redis synonyms에 반영한다.
    원본 결과를 질의한 사용자·관리자만 올릴 수 있다.

    Args:
        request: FastAPI Request (app.state.config 접근용)
        file: 수정된 매핑 보고서 MD 파일
        query_id: 원본 결과의 query_id
        current_user: 인증 사용자(소유자 확인)

    Returns:
        반영 결과 딕셔너리
    """
    # 1. 원본 보고서 조회
    stored = _owned_result(
        request, query_id, current_user, not_found="원본 결과를 찾을 수 없습니다."
    )
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
async def download_file(
    request: Request,
    query_id: str,
    current_user: dict[str, Any] = Depends(require_user),
) -> StreamingResponse:
    """생성된 파일을 다운로드한다(질의한 사용자·관리자만)."""
    stored = _owned_result(request, query_id, current_user)
    file_bytes = stored.get("output_file")
    file_name = stored.get("file_name", "download")

    if not file_bytes:
        raise HTTPException(status_code=404, detail="생성된 파일이 없습니다.")

    content_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if file_name.endswith(".xlsx")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    response = StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=content_type,
        headers={
            "Content-Disposition": _attachment_disposition(file_name)
        },
    )
    await _audit_file_download(
        request, current_user, file_name=file_name,
        file_type=_get_file_extension(file_name), file_size=len(file_bytes),
    )
    return response


@router.get("/query/{query_id}/attachment")
async def download_attachment(
    request: Request,
    query_id: str,
    current_user: dict = Depends(require_user),
) -> StreamingResponse:
    """사용자가 업로드한 원본 양식 파일을 그대로 다운로드한다(§14).

    첨부 파일 카드 클릭 시 호출된다. 생성 결과 파일(`/download`)이 아니라
    업로드 원본(`uploaded_file`)을 서빙한다. 그 파일을 올린 사용자만 받는다(§14.5 소유자
    확인). 저장본은 DRM 해제 뒤의 평문이라 관리자도 남의 원본은 받지 않는다 — 관리자 복호화
    다운로드는 승인 전 범위 밖이다(D-156 후속1 · D-262 후속).
    """
    stored = _owned_result(
        request, query_id, current_user,
        allow_admin=False, denied="원본 첨부 파일은 올린 사용자만 받을 수 있습니다.",
    )
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

    response = StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=content_type,
        headers={
            "Content-Disposition": _attachment_disposition(file_name)
        },
    )
    await _audit_file_download(
        request, current_user, file_name=file_name, file_type=ext, file_size=len(file_bytes)
    )
    return response


@router.get("/query/{query_id}/download-csv")
async def download_csv(
    request: Request,
    query_id: str,
    current_user: dict[str, Any] = Depends(require_user),
) -> StreamingResponse:
    """조회 결과를 CSV 파일로 다운로드한다(질의한 사용자·관리자만).

    행은 화면 응답과 같은 규칙(`DataMasker` — `result_organizer`가 응답 행에 적용하는 것)으로
    가린다. 저장된 `query_results`는 전량·원순서 그대로 두고(D-047 · plans/113 G-4) 내보낼
    때만 가린다.
    """
    stored = _owned_result(request, query_id, current_user)
    rows = stored.get("query_results", [])

    if not rows:
        raise HTTPException(status_code=404, detail="다운로드할 조회 결과가 없습니다.")

    rows = DataMasker(request.app.state.config.security).mask_rows(
        [r for r in rows if isinstance(r, dict)]
    )

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
    file_name = f"query_result_{query_id[:8]}.csv"

    response = StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": _attachment_disposition(file_name)
        },
    )
    await _audit_file_download(
        request, current_user, file_name=file_name, file_type="csv", file_size=len(csv_bytes)
    )
    return response
