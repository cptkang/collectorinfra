"""알람 분석 테스트 API.

폴스타와의 실제 TCP 연결 없이, 알람 페이로드를 직접 입력하여
알람 분석 에이전트를 실행하고 결과를 확인하기 위한 테스트 엔드포인트.

지원 기능:
    - dry_run=true : 발송 없이 분석 결과 + 발송될 메시지 미리보기 반환
    - send_notification=true : 실제 채널(workb/webhook)로 알림 발송
    - channels 오버라이드 : 설정 채널 대신 지정 채널로 발송
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.dependencies import (
    alarm_zones_for_user,
    is_admin as is_admin_user,
    require_admin_user,
    require_user,
    resolve_stream_user,
)
from src.api import investigation_live_hub, investigation_poll
from src.domain.investigation_reject_reasons import reject_display_text
from src.infrastructure import investigation_request_limit
from src.api.error_detail import admin_error_detail
# plans/166 A8 — 스키마는 alarm_schemas로 이동 · 여기서 재노출(D-337 ①)
from src.api.routes.alarm_schemas import (
    AlarmAnalysisOutput,
    AlarmCapabilitiesResponse,
    AlarmFeedbackRequest,
    AlarmFeedbackResponse,
    AlarmFeedbackRetractRequest,
    AlarmFeedbackRetractResponse,
    AlarmFeedbackSummaryResponse,
    AlarmMetricsResponse,
    AlarmPromptSuggestion,
    AlarmPromptSuggestRequest,
    AlarmPromptSuggestResponse,
    AlarmRawTestRequest,
    AlarmTestRequest,
    AlarmTestResponse,
    IncidentAckResponse,
    IncidentListResponse,
    NotificationPreview,
    _default_test_db_id as _default_test_db_id,
    _WebhookPreview,
    _WorkbPreview,
)
from src.routing.zones import all_zones, db_id_to_zone
from noise_gate.domain.severity import coerce_severity, parse_severity
from noise_gate.domain.alarm import (
    AlarmAnalysisResult,
    AlarmEvent,
    AlarmHistoryEntry,
    AlarmHistoryStats,
    ProcessSnapshot,
)
from noise_gate.application.alarm_test_payload import (
    build_ui_push_payload,
    identity_dict,
    process_to_dict,
    stats_to_dict,
)

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from noise_gate.infrastructure.investigation_store import InvestigationStore

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── 유틸 함수 ───────────────────────────────────────────────────────────────

_SEVERITY_LABELS = {0: "해소", 1: "주의", 2: "경고", 3: "심각"}

# plans/166 A8 — 변환 함수는 noise_gate 쪽으로 이동 · 옛 이름 별칭(D-337 ③)
_stats_to_dict = stats_to_dict
_process_to_dict = process_to_dict
_identity_dict = identity_dict


async def _resolve_process_snapshot(
    config,
    event: AlarmEvent,
    query_process: bool,
    simulated_processes: Optional[list[dict[str, Any]]],
) -> Optional[ProcessSnapshot]:
    """테스트 요청의 프로세스 파라미터에 따라 ProcessSnapshot을 산출한다 (Plan 47-1 §5.8).

    - simulated_processes 지정 시: API 호출 없이 해당 목록으로 select_top_processes
      (게이팅 없이 알람 종류만 판정해 정렬·마스킹 확인). 비CPU/메모리 알람이면 None.
    - query_process=True: 실제 폴스타 프로세스 API 호출 (enrich_processes 게이팅 적용).
    - 실패 시 None — 분석 파이프라인은 계속 진행 (graceful degradation).
    """
    try:
        if simulated_processes is not None:
            from noise_gate.domain.process_rank import (
                classify_alarm_kind,
                select_top_processes,
            )

            # Plan 60 E6: classify_alarm_kind가 disk/network 등도 판정하나, 이 테스트 경로
            # process_snapshot("영향 프로세스" 표)은 현행처럼 cpu/memory만 대상으로 유지한다.
            kind = classify_alarm_kind(event)
            if kind not in ("cpu", "memory"):
                return None
            top, total = select_top_processes(
                simulated_processes, kind, config.alarm.process_top_n
            )
            return ProcessSnapshot(
                alarm_kind=kind,
                captured_at=None,
                top=top,
                total_count=total,
                source_host=event.hostname,
            )
        if query_process:
            from noise_gate.application.nodes.alarm_context_enricher import (
                enrich_processes,
            )
            from noise_gate.infrastructure.polestar_process_api import (
                PolestarProcessApiClient,
            )

            client = PolestarProcessApiClient(config.alarm)
            return await asyncio.wait_for(
                enrich_processes(event, config.alarm, client),
                timeout=config.alarm.enrich_timeout_seconds,
            )
    except Exception as exc:
        logger.warning("테스트 프로세스 스냅샷 산출 실패 — 프로세스 없이 분석 진행: %s", exc)
    return None


async def _attach_server_identity(config, event: AlarmEvent) -> None:  # noqa: ANN001
    """hostname → 등록 서버명·IP 역조회를 붙인다 (D-188 · 워커 `_process`와 대칭).

    상시 동작(끄는 플래그 없음). 리졸버 생성·조회 실패는 graceful(존 라벨만 부착).
    """
    from noise_gate.application.server_identity import attach_server_identity

    resolver = None
    try:
        from noise_gate.infrastructure.polestar_hostname_resolver import (
            PolestarHostnameResolver,
        )
        from src.routing.db_registry import DBRegistry

        resolver = PolestarHostnameResolver(DBRegistry(config))
    except Exception:
        logger.warning("서버 식별 리졸버 생성 실패 — 역조회 없이 진행", exc_info=True)
    await attach_server_identity(
        event,
        resolver,
        timeout=float(getattr(config.alarm, "server_identity_timeout_seconds", 3.0)),
        cache_ttl=0,  # API 테스트 경로는 프로세스 로컬 — 캐시 미사용
    )


def _simulated_entries(items: list[dict[str, Any]]) -> list[AlarmHistoryEntry]:
    """simulated_history 항목을 AlarmHistoryEntry 목록으로 변환한다 (Plan 47 §5.9)."""
    from datetime import datetime as _dt

    entries: list[AlarmHistoryEntry] = []
    for i, item in enumerate(items):
        alarm_time = _dt.strptime(str(item["alarm_time"]), "%Y%m%d%H%M%S")
        entries.append(
            AlarmHistoryEntry(
                alarm_id=str(item.get("alarm_id", f"SIM-{i}")),
                severity=int(item.get("severity", 1)),
                alarm_status=str(item.get("alarm_status", "")),
                resource_name=str(item.get("resource_name", "")),
                alarm_time=alarm_time,
            )
        )
    return entries


async def _resolve_history_stats(
    config,
    event: AlarmEvent,
    query_history: bool,
    simulated_history: Optional[list[dict[str, Any]]],
) -> Optional[AlarmHistoryStats]:
    """테스트 요청의 이력 파라미터에 따라 AlarmHistoryStats를 산출한다.

    - simulated_history 지정 시: DB 조회 없이 해당 목록으로 통계 계산 (source="simulated")
    - query_history=True: 실제 폴스타 DB 이력 조회 (enrich_timeout_seconds 적용)
    - 실패 시 None 반환 — 분석 파이프라인은 계속 진행 (graceful degradation)
    """
    try:
        if simulated_history is not None:
            from noise_gate.domain.alarm_pattern import compute_history_stats

            entries = _simulated_entries(simulated_history)
            return compute_history_stats(
                event,
                entries,
                burst_threshold_24h=config.alarm.burst_threshold_24h,
                lookback_days=config.alarm.history_lookback_days,
                source="simulated",
            )
        if query_history:
            from noise_gate.application.nodes.alarm_context_enricher import enrich_history
            from noise_gate.infrastructure.polestar_history import (
                PolestarAlarmHistoryRepository,
            )
            from src.routing.db_registry import DBRegistry

            repo = PolestarAlarmHistoryRepository(DBRegistry(config), config.alarm)
            if not repo.is_db_registered(event.db_id):
                logger.warning(
                    "이력 조회 건너뜀 — 미등록 db_id: %s", event.db_id
                )
                return None
            return await asyncio.wait_for(
                enrich_history(event, config.alarm, repo, None),
                timeout=config.alarm.enrich_timeout_seconds,
            )
    except Exception as exc:
        logger.warning("테스트 이력 통계 산출 실패 — 이력 없이 분석 진행: %s", exc)
    return None


def _build_workb_preview(
    workb_cfg,
    result: AlarmAnalysisResult,
    process_snapshot: Optional[ProcessSnapshot] = None,
) -> _WorkbPreview:
    """WorkB 발송 미리보기를 생성한다."""
    from noise_gate.application.nodes.alarm_notifier import build_workb_body

    ev = result.alarm_event
    title = f"[{result.severity_label}] {ev.server_name} ({ev.hostname})"
    body = build_workb_body(result, process_snapshot)
    api_url = (
        f"{workb_cfg.base_url.rstrip('/')}/api/sendWorkbMsg"
        if workb_cfg.base_url else None
    )
    return _WorkbPreview(
        title=title,
        body=body,
        recipients=workb_cfg.get_user_ids(ev.severity),
        alias=workb_cfg.alias,
        api_url=api_url,
    )


def _build_webhook_preview(
    alarm_cfg,
    result: AlarmAnalysisResult,
    process_snapshot: Optional[ProcessSnapshot] = None,
) -> _WebhookPreview:
    """Webhook 발송 미리보기를 생성한다."""
    from noise_gate.application.nodes.alarm_notifier import _process_payload

    ev = result.alarm_event
    payload = {
        "alarm_id": ev.alarm_id,
        "severity": ev.severity,
        "severity_label": result.severity_label,
        "alarm_name": ev.alarm_name,
        "db_id": ev.db_id,
        "server_name": ev.server_name,
        "hostname": ev.hostname,
        "ip_address": ev.ip_address,
        "resource_ancestry": ev.resource_ancestry,
        "resource_type": ev.resource_type,
        "alarm_status": ev.alarm_status,
        "conditions": ev.conditions,
        "condition_log": ev.condition_log,
        "summary": result.summary,
        "probable_cause": result.probable_cause,
        "recommended_action": result.recommended_action,
        "is_clear": ev.is_clear,
        # Plan 47: 패턴 분석 결과 (_send_webhook payload와 동기 유지)
        "pattern_type": result.pattern_type,
        "is_routine": result.is_routine,
        "pattern_analysis": result.pattern_analysis,
        # Plan 47-1: 영향 프로세스 스냅샷 (_send_webhook payload와 동기 유지)
        "process_snapshot": _process_payload(process_snapshot),
    }
    return _WebhookPreview(
        url=alarm_cfg.webhook_url or None,
        payload=payload,
    )


def _build_notification_preview(
    config,
    result: AlarmAnalysisResult,
    channels: list[str],
    process_snapshot: Optional[ProcessSnapshot] = None,
) -> NotificationPreview:
    """채널 목록에 따라 발송 미리보기를 생성한다."""
    preview = NotificationPreview()
    for ch in channels:
        if ch == "workb":
            preview.workb = _build_workb_preview(config.workb, result, process_snapshot)
        elif ch == "webhook":
            preview.webhook = _build_webhook_preview(config.alarm, result, process_snapshot)
    return preview


def _alarm_extra_configurable(request: Request, config) -> dict[str, Any]:
    """게이트 활성 시 notifier/gate가 쓰는 부가 의존성을 묶어 반환한다 (Plan 52 E3).

    enable_noise_gate=False(기본)면 빈 dict — 기존 발송 경로 무변경(회귀 0).
    활성 시 TICKET 일배치 큐·감사 저장소·SSE 버스를 주입한다. 워커 경로와 달리 API 경로는
    app.state.alarm_bus를 주입할 수 있어 DASHBOARD/TICKET SSE가 실제로 동작한다.
    """
    if not config.noise_gate.enable_noise_gate:
        return {}
    from noise_gate.infrastructure.decision_store import DecisionStore
    from noise_gate.infrastructure.feedback_store import FeedbackStore
    from noise_gate.infrastructure.ticket_queue import TicketBatchQueue

    ng = config.noise_gate
    return {
        "decision_store": DecisionStore(ng.decision_store_path, ng.decision_store_enabled),
        "ticket_queue": TicketBatchQueue(
            ng.ticket_batch_queue_path, ng.ticket_batch_queue_enabled
        ),
        # (E4) 운영자 피드백 few-shot 저장소 — enable_llm_actionability off면 None → analyzer는
        # 피드백 섹션 없이 진행(회귀 0).
        "feedback_store": (
            FeedbackStore(
                ng.feedback_store_path,
                ng.feedback_store_enabled,
                getattr(ng, "feedback_store_max_lines", 20000),
            )
            if getattr(ng, "enable_llm_actionability", False)
            else None
        ),
        "alarm_bus": getattr(request.app.state, "alarm_bus", None),
        # (D-049) PAGE 결정 시 incident open 발행기 — 트래커 off면 app.state에 None.
        "incident_publisher": getattr(request.app.state, "incident_publisher", None),
    }


def _analysis_failure_response(
    event: AlarmEvent, channels: list[str], error: str, elapsed_ms: float
) -> AlarmTestResponse:
    """분석 노드 예외·결과 없음일 때의 테스트 응답 (두 테스트 엔드포인트 공용 · plans/166 A8)."""
    return AlarmTestResponse(
        alarm_id=event.alarm_id,
        severity=event.severity,
        severity_label=_SEVERITY_LABELS.get(event.severity, str(event.severity)),
        analysis=None,
        notification_channels=channels,
        notification_preview=NotificationPreview(),
        error=error,
        processing_time_ms=elapsed_ms,
    )


# ─── 엔드포인트 ───────────────────────────────────────────────────────────────

@router.post(
    "/alarm/analyze-test",
    response_model=AlarmTestResponse,
    summary="알람 분석 테스트",
    description=(
        "폴스타 알람 페이로드를 직접 입력하여 알람 분석 에이전트를 실행합니다.<br/>"
        "<b>관리자 전용</b>(관리자 역할 사용자 또는 운영자 토큰 · 그 밖은 403).<br/>"
        "<b>dry_run=true</b>(기본값): 분석 결과 + 발송될 메시지 미리보기만 반환, 실제 발송 안 함.<br/>"
        "<b>dry_run=false, send_notification=true</b>: 설정된 채널로 실제 알림 발송.<br/>"
        "<b>channels</b>: 특정 채널만 테스트하고 싶을 때 지정 (예: [\"workb\"])."
    ),
    tags=["alarm"],
)
async def analyze_alarm_test(
    request: Request,
    body: AlarmTestRequest,
    # 시험 도구 — 임의 db_id 조회·화면 게시·실제 발송이 되므로 관리자 전용(D-263 잔여 후속)
    current_user: dict = Depends(require_admin_user),
) -> AlarmTestResponse:
    """알람 페이로드를 입력받아 LLM 분석 및 알림 미리보기(또는 실제 발송)를 반환한다."""
    start_time = time.time()
    config = request.app.state.config

    # 1. AlarmEvent 구성
    from datetime import datetime as _dt
    alarm_time_str = body.alarm_time
    try:
        alarm_time = _dt.strptime(alarm_time_str, "%Y%m%d%H%M%S") if alarm_time_str else _dt.now()
    except ValueError:
        alarm_time = _dt.now()

    alarm_status = body.alarm_status
    severity = body.severity
    # is_clear는 severity == 0 단독 기준 — alarmStatus는 ACK 상태로 무관 (Plan 47 §9)
    is_clear = body.is_clear or severity == 0

    event = AlarmEvent(
        db_id=body.db_id,
        server_name=body.server_name,
        hostname=body.hostname,
        ip_address=body.ip_address,
        resource_ancestry=body.resource_ancestry,
        alarm_id=body.alarm_id or str(uuid.uuid4()),
        severity=severity,
        alarm_status=alarm_status,
        resource_type=body.resource_type,
        resource_name=body.resource_name,
        alarm_name=body.alarm_name,
        alarm_time=alarm_time,
        conditions=body.conditions,
        condition_log=body.condition_log,
        is_clear=is_clear,
        raw_payload=body.model_dump(
            exclude={
                "dry_run", "send_notification", "channels", "push_to_ui",
                "query_history", "simulated_history",
                "query_process", "simulated_processes",
            }
        ),
        received_at=_dt.now(),
    )
    # (D-188) hostname 역조회 — 워커 경로와 대칭(server_name이 hostname과 같을 때만 승격)
    await _attach_server_identity(config, event)

    # 2. 사용할 채널 결정 (요청 오버라이드 > 서버 설정)
    channels: list[str] = (
        body.channels
        if body.channels is not None
        else config.alarm.get_notification_channels()
    )

    # 2-1. 이력 통계 산출 (Plan 47 — query_history / simulated_history)
    history_stats = await _resolve_history_stats(
        config, event, body.query_history, body.simulated_history
    )

    # 2-2. 영향 프로세스 스냅샷 산출 (Plan 47-1 — query_process / simulated_processes)
    process_snapshot = await _resolve_process_snapshot(
        config, event, body.query_process, body.simulated_processes
    )

    # 3. LLM 알람 분석 실행 (analyzer 노드만 직접 호출)
    from noise_gate.application.nodes.alarm_analyzer import alarm_analyzer_node

    state: dict[str, Any] = {
        "alarm_event": event,
        "history_stats": history_stats,
        "process_snapshot": process_snapshot,
        "analysis_result": None,
        "error": None,
    }
    lc_config = {
        "configurable": {"app_config": config, **_alarm_extra_configurable(request, config)}
    }

    try:
        result_state = await alarm_analyzer_node(state, lc_config)
    except Exception as exc:
        elapsed_ms = (time.time() - start_time) * 1000
        return _analysis_failure_response(
            event, channels, admin_error_detail("알람 분석", exc, logger, request), elapsed_ms
        )

    analysis_result: Optional[AlarmAnalysisResult] = result_state.get("analysis_result")
    analyzer_error: Optional[str] = result_state.get("error")

    # 4. 분석 실패 응답
    if analyzer_error or not analysis_result:
        elapsed_ms = (time.time() - start_time) * 1000
        return _analysis_failure_response(
            event, channels, analyzer_error or "알람 분석 결과를 받지 못했습니다.", elapsed_ms
        )

    # 5. 분석 결과 채널 동기화
    analysis_result.notification_channels = channels

    # 5-1. 웹 UI 푸시 (push_to_ui=True일 때)
    if body.push_to_ui:
        await request.app.state.alarm_bus.publish(
            build_ui_push_payload(event, analysis_result, history_stats, process_snapshot)
        )

    # 6. 발송 미리보기 생성 (dry_run 여부와 무관하게 항상 생성)
    preview = _build_notification_preview(config, analysis_result, channels, process_snapshot)

    # 7. 실제 발송 (dry_run=False + send_notification=True일 때만)
    notifications_sent: Optional[dict[str, bool]] = None
    if not body.dry_run and body.send_notification:
        from noise_gate.application.nodes.alarm_notifier import alarm_notifier_node

        # result_state는 노드의 업데이트 dict({"analysis_result": ...})만 담으므로
        # 게이트/notifier가 쓰는 alarm_event·history_stats는 원본 state에서 병합한다.
        notifier_state = {
            **state, **result_state, "process_snapshot": process_snapshot, "error": None
        }
        # Plan 52 E3: 게이트 활성 시 4-티어 판단을 산출해 notifier에 전달한다
        # (TICKET 큐 적재·DASHBOARD/TICKET SSE 동작). 게이트 off면 decision 미생성 →
        # notifier는 기존 발송 경로로 폴백(무변경, 회귀 0).
        if config.noise_gate.enable_noise_gate:
            from noise_gate.application.nodes.notification_gate import (
                notification_gate_node,
            )

            gate_out = await notification_gate_node(notifier_state, lc_config)
            notifier_state = {**notifier_state, **gate_out}
        try:
            notifier_out = await alarm_notifier_node(notifier_state, lc_config)
            sent_result: Optional[AlarmAnalysisResult] = notifier_out.get("analysis_result")
            notifications_sent = sent_result.notifications_sent if sent_result else {}
        except Exception as exc:
            notifications_sent = {ch: False for ch in channels}
            analyzer_error = admin_error_detail("알림 발송", exc, logger, request)

    elapsed_ms = (time.time() - start_time) * 1000

    return AlarmTestResponse(
        alarm_id=event.alarm_id,
        severity=event.severity,
        severity_label=analysis_result.severity_label,
        analysis=AlarmAnalysisOutput(
            severity_label=analysis_result.severity_label,
            summary=analysis_result.summary,
            probable_cause=analysis_result.probable_cause,
            recommended_action=analysis_result.recommended_action,
            pattern_type=analysis_result.pattern_type,
            is_routine=analysis_result.is_routine,
            pattern_analysis=analysis_result.pattern_analysis,
        ),
        notification_channels=channels,
        notification_preview=preview,
        notifications_sent=notifications_sent,
        history_stats=_stats_to_dict(history_stats) if history_stats else None,
        process_snapshot=_process_to_dict(process_snapshot) if process_snapshot else None,
        error=analyzer_error,
        processing_time_ms=elapsed_ms,
    )


# ─── 알람 알림 SSE 스트림 ─────────────────────────────────────────────────────

@router.get(
    "/alarm/notifications/stream",
    summary="웹 UI 알람 알림 SSE 스트림",
    description=(
        "웹 UI가 구독하는 Server-Sent Events 엔드포인트. "
        "analyze-test API에서 push_to_ui=true로 호출하면 이 스트림으로 알람이 전송된다."
    ),
    tags=["alarm"],
)
async def alarm_notifications_stream(
    request: Request,
    token: Optional[str] = None,
) -> StreamingResponse:
    """분석된 알람 이벤트를 구독자 존으로 필터링해 SSE로 전송한다(Plan 59 §17).

    지역 스코프 RBAC: 관리자=전 존, 공동존/은행존 운영자=해당 존만, 일반=구독 거부(403).
    인증은 쿠키(user_token) 우선(EventSource 헤더 제약), 쿼리 토큰은 내부망 폴백.
    """
    config = request.app.state.config
    user = await resolve_stream_user(request, token)
    if config.auth.enabled and user is None:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")

    allowed_zones = alarm_zones_for_user(user, config.auth.enabled)
    if not allowed_zones:
        raise HTTPException(status_code=403, detail="알림 수신 권한이 없습니다.")

    # 전 존 허용(관리자/개발 모드)이면 db_id 필터를 건너뛴다.
    deliver_all = allowed_zones >= set(all_zones())
    # (Plan 83 T10) SUPPRESS 수신 권한 — 개발 모드는 종전 진입성을 보존한다.
    is_admin = (not config.auth.enabled) or is_admin_user(user)

    def _visible(event: dict) -> bool:
        return event_visible_to(
            event,
            allowed_zones=allowed_zones,
            deliver_all=deliver_all,
            is_admin=is_admin,
        )

    bus = request.app.state.alarm_bus
    q = bus.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    if not _visible(event):
                        continue  # 구독자 존이 아닌 이벤트는 전송하지 않음
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield 'data: {"type":"ping"}\n\n'
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── 운영 지표 / 메타모니터링 엔드포인트 ─────────────────────────────────────

@router.get(
    "/alarm/metrics",
    response_model=AlarmMetricsResponse,
    summary="알람 노이즈 게이트 운영 지표",
    description=(
        "발송 판단 감사(decision_store)에서 티어별 건수·억제율·액션가능 비율과 "
        "억제기 메타경보(억제율 임계 초과/이벤트 무수신)를 산출해 반환합니다.<br/>"
        "MTTA/MTTR/사건전환율은 ack·해소상관 계측이 없어 null이며 unavailable_metrics에 "
        "사유를 명시합니다(환각 금지)."
    ),
    tags=["alarm"],
)
async def alarm_metrics(
    request: Request,
    current_user: dict = Depends(require_user),
) -> AlarmMetricsResponse:
    """decision_store 집계 + 메타경보를 운영 지표 JSON으로 반환한다 (Plan 52 §9)."""
    from noise_gate.infrastructure.decision_store import DecisionStore

    config = request.app.state.config
    ng = config.noise_gate
    window = ng.meta_alert_window_seconds

    # 읽기 전용 — 파일이 있으면 집계, 없으면 빈 집계(전 키 포함)
    store = DecisionStore(ng.decision_store_path)
    agg = store.aggregate(window_seconds=window)
    meta = store.meta_alerts(
        window_seconds=window,
        suppress_ratio_threshold=ng.meta_alert_suppress_ratio,
        min_events=ng.meta_alert_min_events,
        # (plans/162 R4) 기본 0 = 끔. 구 config(필드 부재)도 끔으로 읽는다.
        collection_failed_ratio_threshold=float(
            getattr(ng, "meta_alert_collection_failed_ratio", 0.0)
        ),
    )

    # ── D-049: incident_store 주입 시 MTTA/incident_mttr/사건전환율 채움 ──
    # 트래커 off(store=None)면 기존 null + unavailable_metrics 동작 유지(회귀 0).
    incident_store = getattr(request.app.state, "incident_store", None)
    mtta_seconds: Optional[float] = None
    incident_mttr_seconds: Optional[float] = None
    incident_conversion_rate: Optional[float] = None
    open_incident_count = 0
    # 트래커 off — 기존 동작 유지(회귀 0): MTTA/MTTR/사건전환율 null + reason 사유 노출
    unavailable_metrics: dict[str, str] = {
        "reason": "ack/incident 계측 미활성(NOISE_INCIDENT_TRACKING_ENABLED=false)",
    }
    if incident_store is not None:
        m = await incident_store.metrics(
            window_seconds=window, page_count=agg["page_count"]
        )
        mtta_seconds = m["mtta_seconds"]
        incident_mttr_seconds = m["incident_mttr_seconds"]
        incident_conversion_rate = m["incident_conversion_rate"]
        open_incident_count = m["open_count"]
        # 계측 활성 — 해당 지표 사유를 제거(채워진 값으로 노출)
        unavailable_metrics = {}

    return AlarmMetricsResponse(
        window_seconds=window,
        total=agg["total"],
        by_tier=agg["by_tier"],
        page_count=agg["page_count"],
        ticket_count=agg["ticket_count"],
        dashboard_count=agg["dashboard_count"],
        suppress_count=agg["suppress_count"],
        actionable_ratio=agg["actionable_ratio"],
        suppress_ratio=agg["suppress_ratio"],
        last_event_age_seconds=agg["last_event_age_seconds"],
        meta_alerts=meta,
        mtta_seconds=mtta_seconds,
        # 하위호환 — mttr_seconds는 incident_mttr_seconds와 동일 값(운영자 MTTR)
        mttr_seconds=incident_mttr_seconds,
        incident_mttr_seconds=incident_mttr_seconds,
        # self-heal 편향 부분지표(sev3 제외) — incident_mttr와 명확히 구분
        auto_recovery_mttr_seconds=agg["auto_recovery_mttr_seconds"],
        incident_conversion_rate=incident_conversion_rate,
        open_incident_count=open_incident_count,
        unavailable_metrics=unavailable_metrics,
    )


# ─── SSE 이벤트 가시성 판정 (Plan 83 T10) ────────────────────────────────────

def event_visible_to(
    event: dict, *, allowed_zones: set[str], deliver_all: bool, is_admin: bool
) -> bool:
    """구독자에게 이 알람 이벤트를 전송해도 되는지 판정한다(순수 함수).

    두 축이 있고 **권한은 서버가, 표시 선호는 클라이언트가** 판단한다(Plan 83 §B):

    - **존(zone)**: 종전 규약 그대로 — 전 존이면 통과, 아니면 이벤트 db_id의 존이 구독자
      존에 속할 때만 통과.
    - **SUPPRESS 티어**: 관리자에게만 전송한다. 억제된 알람의 전문이 비관리자 브라우저에
      도달한 뒤 화면에서만 가려지는 상태를 만들지 않기 위해서다(클라이언트 필터는 이미 받은
      것을 안 그릴 뿐이라 개발자 도구로 그대로 보인다).

    page/ticket/dashboard 사이의 표시 여부는 **여기서 거르지 않는다** — 개인 표시 레벨은
    브라우저 localStorage에 있고(G-1 확정) 서버는 그 값을 모른다.
    """
    if event.get("tier") == "suppress" and not is_admin:
        return False
    if deliver_all:
        return True
    zone = db_id_to_zone(event.get("db_id"))
    return zone is not None and zone in allowed_zones


# ─── 존(zone) 접근 판정 — 피드백·ack·사건 목록·피드백 요약 공용 (Plan 83 T2·T3) ──

def _alarm_zone_scope(request: Request, current_user: dict) -> set[str] | None:
    """요청 사용자의 알람 존 범위를 산출한다 — 전 존이면 None(거를 것 없음).

    Raises:
        HTTPException: 존 집합이 비면 403(스트림의 구독 거부와 같은 의미).
    """
    zones = alarm_zones_for_user(current_user, request.app.state.config.auth.enabled)
    if not zones:
        raise HTTPException(status_code=403, detail="알람 수신 권한이 없습니다.")
    if zones >= set(all_zones()):
        return None
    return zones


def _zone_permits(scope: set[str] | None, db_id: str | None) -> bool:
    """`_alarm_zone_scope` 범위가 이 db_id의 알람을 허용하는지 판정한다(순수 함수).

    - 전 존(None)이면 db_id와 무관하게 허용.
    - db_id가 없으면 **막지 않는다** — 판정 불가를 거부로 바꾸지 않는다(하위호환).
    - 그 외에는 대상 존이 사용자 존에 속할 때만 허용(존 매핑 없는 db_id는 거부).
    """
    if scope is None or not db_id:
        return True
    zone = db_id_to_zone(db_id)
    return zone is not None and zone in scope


def _assert_zone_access(
    request: Request, current_user: dict, db_id: Optional[str]
) -> None:
    """알람 대상 db_id에 대한 구독자 존 권한을 강제한다.

    SSE 스트림(`alarm_notifications_stream`의 `_visible`)과 **동일한 규약**을 쓴다 —
    쓰기 경로만 무방비였던 비대칭을 없애는 것이 목적이다(docs/28 실측).
    조회 경로(사건 목록·피드백 요약)도 같은 `_alarm_zone_scope`·`_zone_permits`로 거른다.

    - 존 집합이 비면 403(스트림의 구독 거부와 같은 의미).
    - 전 존(관리자·개발 모드)이면 db_id와 무관하게 통과.
    - db_id가 없으면 **차단하지 않는다** — 판정 불가를 거부로 바꾸지 않는다(하위호환).
    - 그 외에는 대상 존이 구독자 존에 속할 때만 통과.

    Raises:
        HTTPException: 권한 없음(403).
    """
    if not _zone_permits(_alarm_zone_scope(request, current_user), db_id):
        raise HTTPException(
            status_code=403, detail="해당 존의 알람에 대한 권한이 없습니다."
        )


# ─── incident ack / 조회 엔드포인트 (D-049) ──────────────────────────────────

@router.post(
    "/alarm/incidents/{incident_id}/ack",
    response_model=IncidentAckResponse,
    summary="incident 확인(ack)",
    description=(
        "열린 incident를 확인(ack) 상태로 전이합니다(식별키=incident id, MTTA 계측).<br/>"
        "incident 계측이 비활성(NOISE_INCIDENT_TRACKING_ENABLED=false)이면 503을 반환합니다."
    ),
    tags=["alarm"],
)
async def ack_incident(
    incident_id: int,
    request: Request,
    current_user: dict = Depends(require_user),
) -> IncidentAckResponse:
    """incident를 ack 처리한다(트래커 비활성 시 503)."""
    from datetime import datetime as _dt, timezone as _tz

    store = getattr(request.app.state, "incident_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="incident tracking 비활성")

    # (Plan 83 T3) 존 RBAC — 대상 incident의 db_id로 판정한다.
    # get_db_id 미보유 구현체(테스트 대역 등)·조회 실패는 판정 생략(차단 아님·graceful).
    target_db_id: Optional[str] = None
    getter = getattr(store, "get_db_id", None)
    if getter is not None:
        try:
            target_db_id = await getter(incident_id)
        except Exception:  # noqa: BLE001 — 판정 불가는 통과시킨다
            target_db_id = None
    _assert_zone_access(request, current_user, target_db_id)

    acked_by = current_user.get("sub") or current_user.get("name") or "operator"
    ok = await store.ack(
        incident_id=incident_id,
        acked_at=_dt.now(tz=_tz.utc),
        acked_by=acked_by,
    )
    return IncidentAckResponse(acked=ok, incident_id=incident_id)


@router.get(
    "/alarm/incidents",
    response_model=IncidentListResponse,
    summary="열린 incident 목록",
    description=(
        "열린(open) incident 목록을 최신순으로 반환합니다.<br/>"
        "요청자의 알림 존에 속한 사건만 반환합니다(ack와 같은 존 판정 · 관리자는 전 존).<br/>"
        "incident 계측이 비활성이면 빈 배열을 반환합니다."
    ),
    tags=["alarm"],
)
async def list_incidents(
    request: Request,
    status: str = "open",
    limit: int = 100,
    current_user: dict = Depends(require_user),
) -> IncidentListResponse:
    """열린 incident 목록을 요청자 존으로 걸러 반환한다(트래커 비활성 시 빈 배열)."""
    # 결함 ⑤ — ack(쓰기)와 같은 존 판정. 존이 없으면 403, 전 존이면 거르지 않는다.
    scope = _alarm_zone_scope(request, current_user)
    store = getattr(request.app.state, "incident_store", None)
    if store is None:
        return IncidentListResponse(incidents=[])
    incidents = await store.list_open(limit=limit)
    # 저장소 포트(list_open)에 존 인자가 없어 조회 뒤에 거른다 — 스코프 사용자는 limit보다
    # 적게 받을 수 있다(상위 limit건 안에서만 거른 결과).
    incidents = [i for i in incidents if _zone_permits(scope, i.get("db_id"))]
    return IncidentListResponse(incidents=incidents)


# ─── 운영자 피드백 엔드포인트 (Plan 52 E4) ───────────────────────────────────

@router.post(
    "/alarm/suggest-prompt",
    response_model=AlarmPromptSuggestResponse,
    summary="알람 조회 질의 추천 (LLM · 기본 off)",
)
async def suggest_alarm_prompt(
    request: Request,
    body: AlarmPromptSuggestRequest,
    current_user: dict = Depends(require_user),
) -> AlarmPromptSuggestResponse:
    """권고 조치·패턴 분석을 근거로 조회 질의 1건을 제안한다 (Plan 86 · D-192).

    결정적 축 매핑이 비는 알람에서만 프론트가 호출한다. **기본 off**이며(과금 경로 —
    D-127) 꺼져 있으면 503이다. 생성·파싱 실패는 200 + `suggestion=null`로 돌려준다 —
    추천이 없다고 알람 카드가 깨지면 안 된다.
    """
    config = request.app.state.config
    ng = config.noise_gate
    if not getattr(ng, "alarm_prompt_llm_suggest_enabled", False):
        raise HTTPException(status_code=503, detail="프롬프트 추천 비활성")
    if not body.target.strip():
        raise HTTPException(status_code=400, detail="target(대상 서버)이 비어 있습니다")
    # 쓰기 경로와 같은 존 RBAC 규약 — 다른 존 알람의 내용을 LLM에 실어 보내지 못하게 한다.
    _assert_zone_access(request, current_user, body.db_id)

    from src.llm import create_llm
    from src.utils.json_extract import extract_json_from_response
    from noise_gate.prompts.alarm_prompt_suggest import (
        ALARM_PROMPT_SUGGEST_SYSTEM,
        ALARM_PROMPT_SUGGEST_USER_TEMPLATE,
    )

    user_msg = ALARM_PROMPT_SUGGEST_USER_TEMPLATE.format(
        target=body.target,
        alarm_name=body.alarm_name,
        resource_type=body.resource_type,
        resource_name=body.resource_name,
        severity_label=body.severity_label,
        summary=body.summary,
        probable_cause=body.probable_cause,
        recommended_action=body.recommended_action,
        pattern_type=body.pattern_type,
        pattern_analysis=body.pattern_analysis,
    )
    try:
        llm = create_llm(config)
        response = await llm.ainvoke(
            [
                {"role": "system", "content": ALARM_PROMPT_SUGGEST_SYSTEM},
                {"role": "user", "content": user_msg},
            ]
        )
        parsed = extract_json_from_response(getattr(response, "content", ""))
    except Exception as exc:  # noqa: BLE001 — 어떤 실패든 카드는 계속 동작해야 한다
        logger.warning("알람 질의 추천 실패: %s", exc)
        return AlarmPromptSuggestResponse(suggestion=None)

    label = (parsed or {}).get("label") or ""
    text = (parsed or {}).get("text") or ""
    if not isinstance(label, str) or not isinstance(text, str) or not text.strip():
        logger.warning("알람 질의 추천 산출 형식 불일치: %s", parsed)
        return AlarmPromptSuggestResponse(suggestion=None)
    # 주어 없는 질의는 쓸모가 없다 — 대상이 빠졌으면 추천하지 않는다(결정적 경로와 같은 규칙).
    if body.target not in text:
        logger.warning("알람 질의 추천에 대상 서버 누락: %s", text)
        return AlarmPromptSuggestResponse(suggestion=None)

    return AlarmPromptSuggestResponse(
        suggestion=AlarmPromptSuggestion(
            label=label.strip()[:40] or "알람 조회",
            text=text.strip(),
            axis=body.resource_type,
            source="llm",
        )
    )


@router.post(
    "/alarm/feedback",
    response_model=AlarmFeedbackResponse,
    summary="운영자 알람 피드백(유효/노이즈)",
    description=(
        "운영자가 알람 카드에서 매긴 라벨(유효/노이즈)을 few-shot 저장소에 적재합니다.<br/>"
        "이후 유사 알람의 LLM 액션가능성 자문(보조 입력)에 활용됩니다.<br/>"
        "게이트 또는 LLM 액션가능성이 비활성이면 503을 반환합니다."
    ),
    tags=["alarm"],
)
async def submit_alarm_feedback(
    request: Request,
    body: AlarmFeedbackRequest,
    current_user: dict = Depends(require_user),
) -> AlarmFeedbackResponse:
    """운영자 피드백을 검증·적재한다(게이트/액션가능성 비활성 시 503)."""
    config = request.app.state.config
    ng = config.noise_gate
    if not ng.enable_noise_gate or not getattr(ng, "enable_llm_actionability", False):
        raise HTTPException(status_code=503, detail="LLM actionability 비활성")
    if body.label not in ("noise", "valid"):
        raise HTTPException(status_code=400, detail="label은 'noise' 또는 'valid'만 허용")
    # (Plan 83 T2) 존 RBAC — 다른 존 알람에 라벨을 남기지 못하게 한다.
    _assert_zone_access(request, current_user, body.db_id)

    from noise_gate.infrastructure.feedback_store import FeedbackStore

    from datetime import datetime as _dt, timezone as _tz

    store = FeedbackStore(
        ng.feedback_store_path,
        ng.feedback_store_enabled,
        getattr(ng, "feedback_store_max_lines", 20000),
    )
    # 적재 시각을 여기서 만들어 응답에 실어야 UI가 철회 대상을 지목할 수 있다(Plan 83 A-5).
    recorded_ts = _dt.now(tz=_tz.utc)
    store.record_feedback(
        label=body.label,
        alarm_name=body.alarm_name,
        resource_name=body.resource_name,
        pattern=body.pattern_type,
        server_name=body.server_name,
        db_id=body.db_id,
        severity=body.severity,
        note=body.note,
        # (Plan 83 T6/A-4) 작성자 — 감사 전용(few-shot 프롬프트에는 실리지 않는다)
        labeled_by=current_user.get("sub") or current_user.get("name") or "",
        ts=recorded_ts,
        # (plans/91 1-4 · C′-3) 조사 참조 — 존 RBAC·작성자·철회는 위 경로를 그대로 재사용한다(별도 저장소 없음)
        investigation_id=body.investigation_id,
    )
    return AlarmFeedbackResponse(recorded=True, ts=recorded_ts.isoformat())


@router.post(
    "/alarm/feedback/retract",
    response_model=AlarmFeedbackRetractResponse,
    summary="운영자 알람 피드백 철회",
    description=(
        "오클릭한 라벨을 철회합니다(tombstone append — 원본은 감사용으로 남습니다).<br/>"
        "게이트 또는 LLM 액션가능성이 비활성이면 503을 반환합니다."
    ),
    tags=["alarm"],
)
async def retract_alarm_feedback(
    request: Request,
    body: AlarmFeedbackRetractRequest,
    current_user: dict = Depends(require_user),
) -> AlarmFeedbackRetractResponse:
    """피드백 철회 tombstone을 적재한다(적재 라우트와 동일한 게이트·존 판정)."""
    config = request.app.state.config
    ng = config.noise_gate
    if not ng.enable_noise_gate or not getattr(ng, "enable_llm_actionability", False):
        raise HTTPException(status_code=503, detail="LLM actionability 비활성")
    if not body.target_ts:
        raise HTTPException(status_code=400, detail="target_ts가 필요합니다")
    _assert_zone_access(request, current_user, body.db_id)

    from noise_gate.infrastructure.feedback_store import FeedbackStore

    store = FeedbackStore(
        ng.feedback_store_path,
        ng.feedback_store_enabled,
        getattr(ng, "feedback_store_max_lines", 20000),
    )
    store.record_retract(
        target_ts=body.target_ts,
        alarm_name=body.alarm_name,
        labeled_by=current_user.get("sub") or current_user.get("name") or "",
    )
    return AlarmFeedbackRetractResponse(retracted=True)


@router.get(
    "/alarm/feedback/summary",
    response_model=AlarmFeedbackSummaryResponse,
    summary="운영자 피드백 라벨 집계",
    description=(
        "(알람명, 자원명)별 유효/노이즈 라벨 수와 최근 라벨·작성자를 반환합니다.<br/>"
        "요청자의 알림 존에 속한 라벨만 집계합니다(적재와 같은 존 판정 · 관리자는 전 존).<br/>"
        "상반된 라벨이 쌓였는지 사람이 확인하기 위한 조회이며 판정에는 관여하지 않습니다."
    ),
    tags=["alarm"],
)
async def alarm_feedback_summary(
    request: Request,
    limit: int = 100,
    current_user: dict = Depends(require_user),
) -> AlarmFeedbackSummaryResponse:
    """피드백 라벨을 요청자 존으로 걸러 집계한다(저장소 비활성·파일 부재면 빈 목록)."""
    # 결함 ⑤ — 적재(쓰기)와 같은 존 판정. 집계 전에 레코드 단위로 걸러야
    # 다른 존 라벨이 카운트·최근 작성자에 섞이지 않는다.
    scope = _alarm_zone_scope(request, current_user)
    ng = request.app.state.config.noise_gate

    from noise_gate.infrastructure.feedback_store import FeedbackStore

    store = FeedbackStore(
        ng.feedback_store_path,
        getattr(ng, "feedback_store_enabled", True),
        getattr(ng, "feedback_store_max_lines", 20000),
    )
    db_id_filter = None if scope is None else (lambda db_id: _zone_permits(scope, db_id))
    items = store.summarize(limit=limit, db_id_filter=db_id_filter)
    # (plans/144 W5) 사건 피드백은 유효/노이즈 집계와 섞지 않고 건수만 따로 준다(같은 존 판정).
    episode_counts = store.summarize_episode_feedback(db_id_filter=db_id_filter)
    return AlarmFeedbackSummaryResponse(items=items, episode_counts=episode_counts)


@router.get(
    "/alarm/capabilities",
    response_model=AlarmCapabilitiesResponse,
    summary="알람 UI 기능 가용성",
    description=(
        "카드 UI가 버튼(피드백·확인)을 렌더할지 결정하기 위한 게이트 상태입니다.<br/>"
        "불리언·숫자만 반환하며 경로·시크릿은 노출하지 않습니다."
    ),
    tags=["alarm"],
)
async def alarm_capabilities(
    request: Request,
    current_user: dict = Depends(require_user),
) -> AlarmCapabilitiesResponse:
    """게이트 조합을 서버가 계산해 내린다(클라이언트가 .env를 추론하지 않게)."""
    ng = request.app.state.config.noise_gate
    gate_on = bool(getattr(ng, "enable_noise_gate", False))
    trigger_on = bool(getattr(ng, "investigation_trigger_enabled", False))
    manual_on = bool(getattr(ng, "investigation_manual_enabled", True))
    return AlarmCapabilitiesResponse(
        # 피드백 라우트의 503 조건과 **같은 식**이어야 한다(어긋나면 UI가 거짓말을 한다).
        feedback_enabled=gate_on and bool(getattr(ng, "enable_llm_actionability", False)),
        incident_tracking=bool(getattr(ng, "incident_tracking_enabled", False)),
        sse_bridge=bool(getattr(ng, "sse_bridge_enabled", False)),
        suppress_stream=bool(getattr(ng, "sse_suppressed_enabled", False)),
        # Plan 86: 꺼져 있으면 프론트가 아예 호출하지 않는다 — 알람마다 503을 때리지 않게 한다.
        prompt_suggest_enabled=bool(
            getattr(ng, "alarm_prompt_llm_suggest_enabled", False)
        ),
        suppress_max_severity=int(getattr(ng, "suppress_max_severity", 2)),
        # plans/153 W2: 조사가 실제로 돌 수 있는 조합일 때만 — 카드가 조사 섹션을 지연 로드할지.
        # plans/194 W1 · D-347: 수동 전용(자동 off · 이 알람 분석 on)에서도 조사 섹션이 보여야
        # 「분석 요청」 동선이 남는다 — 자동·수동 중 하나라도 켜져 있으면 참.
        investigation_enabled=gate_on and (trigger_on or manual_on)
        and bool(ng.investigation_service_url),
        investigation_auto_enabled=gate_on and trigger_on,
        investigation_manual_enabled=gate_on and manual_on,
        investigation_followup_timeout_seconds=float(
            getattr(ng, "investigation_followup_timeout_seconds", 1200.0)
        ),
        # plans/193 W2: 포털 「노이즈 관제」 뷰 탭 — 거짓이면 포털은 관제 자산을 싣지 않는다.
        noise_console_portal_tab=bool(getattr(ng, "console_portal_tab_enabled", False)),
    )


# ─── 자동 조사 브리핑 조회 (plans/153 W2 · D-325 G-3 (a)) ──────────────────────

# plans/155 G-10 · plans/187 W10 — 지연 poll(잠금·간격·종결 규칙)은 공용 모듈로 옮겼다(동작 불변).
# 옛 이름은 같은 객체로 재노출한다(테스트·호출부 호환 — dict는 공유되므로 변경이 그대로 보인다).
_INVESTIGATION_TERMINAL_POLL = investigation_poll.TERMINAL_POLL_STATUSES
_INVESTIGATION_POLL_MIN_INTERVAL = investigation_poll.POLL_MIN_INTERVAL
_INVESTIGATION_POLL_AT = investigation_poll.POLL_AT
_INVESTIGATION_POLL_LOCKS = investigation_poll.POLL_LOCKS
_sweep_investigation_polls = investigation_poll.sweep_polls
_refresh_investigation = investigation_poll.refresh_investigation
_poll_investigation = investigation_poll.poll_investigation


def _is_alarm_operator(config: Any, current_user: dict[str, Any]) -> bool:
    """운영자 뷰 여부 — SSE 스트림의 SUPPRESS 수신 판정과 같은 식(개발 모드는 운영자)."""
    return bool(not config.auth.enabled) or is_admin_user(current_user)


#: 값 패턴 앞뒤 경계 — 식별자·Base64 문자에 붙어 있으면 비밀값 조각으로 보지 않는다.
_INLINE_EDGE = r"A-Za-z0-9+/=_\-"


def _investigation_mask_fn(config: Any) -> Callable[[str], str]:
    """사용자 뷰 마스킹 함수 — 관제(`noise_dashboard._mask_fn`)와 같은 값 패턴 방식.

    plans/187 잔여 ⑥(2026-10-22 사용자 결정): 값 전체가 비밀값 꼴이면 통째로 가리고, 아니면 한 줄
    안에 섞인 비밀값만 가린다 — 민감 키의 값·Bearer·JWT·URL 비밀번호(`mask_secrets`)와 같은 값
    패턴의 부분 일치. 운영자 뷰에는 쓰지 않는다(원문).
    """
    from src.security.data_masker import DataMasker
    from src.utils.log_masking import mask_secrets

    mask = str(config.security.mask_pattern)
    inline = [
        re.compile(
            rf"(?<![{_INLINE_EDGE}])(?:{p.pattern.removeprefix('^').removesuffix('$')})"
            rf"(?![{_INLINE_EDGE}])"
        )
        for p in DataMasker.SENSITIVE_VALUE_PATTERNS
    ]

    def _mask(text: str) -> str:
        for pattern in DataMasker.SENSITIVE_VALUE_PATTERNS:
            if pattern.match(text):
                return mask
        masked = mask_secrets(text)
        for pattern in inline:
            masked = pattern.sub(mask, masked)
        return masked

    return _mask


def _investigation_store(config: Any) -> InvestigationStore | None:
    """조사 저장소(읽기) — 경로 설정이 없으면 None(조회할 것이 없다)."""
    from noise_gate.infrastructure.investigation_store import InvestigationStore

    ng = config.noise_gate
    path = ng.investigation_store_path
    if not path:
        return None
    return InvestigationStore(path, int(getattr(ng, "investigation_store_max_lines", 2000)))


def _decision_db_id(config: Any, alarm_id: str) -> str | None:
    """판정 레코드의 db_id — 조사 레코드에 db_id가 없을 때의 존 판정 폴백(없으면 None)."""
    from noise_gate.infrastructure.decision_store import DecisionStore

    ng = config.noise_gate
    decision = DecisionStore(
        ng.decision_store_path,
        bool(getattr(ng, "decision_store_enabled", True)),
        int(getattr(ng, "decision_store_max_lines", 20000)),
    ).get_decision(alarm_id)
    db_id = (decision or {}).get("db_id")
    return str(db_id) if db_id else None


def _assert_investigation_zone(
    request: Request,
    current_user: dict[str, Any],
    alarm_ids: list[str],
    known_db_ids: dict[str, str] | None = None,
    db_cache: dict[str, str | None] | None = None,
) -> None:
    """조사 응답의 존 검사 — 조사에 걸린 알람 **전부**가 사용자 존 안이어야 한다(fail-closed).

    - 존 집합이 비면 403. 전 존(관리자·개발 모드)이면 통과한다(판정 기록을 읽지 않는다).
    - 제한 스코프에서는 알람마다 db_id를 조사 레코드의 `alarm_db_ids` → 판정 기록 순으로 찾고,
      **하나라도 모르거나 범위 밖이면 403**이다. `_zone_permits`의 "db_id 없음 = 허용" 하위호환을
      여기서는 쓰지 않는다 — 판정 기록이 tail 밖으로 밀린 알람이나 타 존 멤버가 섞인 사건 조사의
      브리핑이 새지 않게 한다(plans/153 W2 교정 · 보안 H1).
    - 걸린 알람이 없으면(제한 스코프) 존을 정할 수 없으므로 403.

    Raises:
        HTTPException: 권한 없음(403).
    """
    scope = _alarm_zone_scope(request, current_user)
    if scope is None:
        return
    config = request.app.state.config
    known = known_db_ids or {}
    if not alarm_ids:
        raise HTTPException(status_code=403, detail="해당 존의 알람에 대한 권한이 없습니다.")
    for alarm_id in alarm_ids:
        db_id = known.get(alarm_id)
        if not db_id and db_cache is not None:
            # (plans/194 W5) 요청 단위 메모 — 판정 목록처럼 여러 행을 거를 때 알람마다 판정 기록
            # tail을 한 번만 훑는다(규칙 불변 — 찾는 값은 `_decision_db_id`와 같다).
            if alarm_id not in db_cache:
                db_cache[alarm_id] = _decision_db_id(config, alarm_id)
            db_id = db_cache[alarm_id]
        elif not db_id:
            db_id = _decision_db_id(config, alarm_id)
        if not db_id or not _zone_permits(scope, db_id):
            raise HTTPException(
                status_code=403, detail="해당 존의 알람에 대한 권한이 없습니다."
            )


def assert_alarm_investigation_access(
    request: Request,
    current_user: dict[str, Any],
    alarm_id: str,
    rec: dict[str, Any] | None,
    *,
    known_db_ids: dict[str, str] | None = None,
    db_cache: dict[str, str | None] | None = None,
) -> bool:
    """알람 id 기준 조사 열람 인가 — by-alarm 조회와 판정 목록 행 조사 상태(plans/194 W5 · C2)의
    단일 규칙.

    순서: 내 사용자 요청 조사(`_request_owner_view` — 존 검사 없이 연다) → 알람 수신 권한(없으면
    레코드 유무와 무관하게 403) → 알람 없는 사용자 요청 조사는 소유자·운영자만 → 이 알람과 조사에
    걸린 알람 **전부**의 존 검사(레코드가 없어도 이 알람의 존을 본다 · fail-closed).
    db_id는 `known_db_ids`(호출부가 아는 값) 위에 레코드 `alarm_db_ids`를 얹어 찾고, 없으면 판정
    기록(`db_cache`가 있으면 요청 단위 메모)이다.

    Returns:
        소유자 열람이면 True(존 검사를 건너뛰었다 — 호출부가 다른 조사 목록을 붙이지 않는다).

    Raises:
        HTTPException: 권한 없음(403).
    """
    config = request.app.state.config
    if _request_owner_view(config, current_user, rec):
        return True
    _alarm_zone_scope(request, current_user)
    _assert_unbound_request_owner(config, current_user, rec)
    members = [a for a in (rec or {}).get("alarm_ids") or [] if a != alarm_id]
    known = dict(known_db_ids or {})
    if isinstance((rec or {}).get("alarm_db_ids"), dict):
        known.update((rec or {})["alarm_db_ids"])
    _assert_investigation_zone(
        request, current_user, [alarm_id, *members], known, db_cache=db_cache
    )
    return False


def _attach_live_progress(
    view: dict[str, Any], rec: dict[str, Any], mask_fn: Callable[[str], str] | None
) -> None:
    """지연 poll이 얹은 진척 키를 응답 최상위에 싣는다(plans/187 W5a · 저장 안 함).

    `progress`는 그대로(수치·단계 id), `partial_briefing`은 운영자면 원문, 사용자면
    `playbook_user_view`로 마스킹한 표시 부분만 싣는다. 키가 없으면 응답은 종전과 같다.
    (plans/194 W5) 미종결이면 대기 순번 `queue_position`(정수)과 실행 단계 `phase`(문자열)도
    싣는다 — 지연 poll·허브 어느 경로든 조사 서비스 poll 응답 값이다(추가 호출 없음).
    """
    from noise_gate.infrastructure.investigation_store import ACTIVE_STATUSES, playbook_user_view

    progress = rec.get("progress")
    if isinstance(progress, dict):
        view["progress"] = progress
    if rec.get("status") in ACTIVE_STATUSES:
        queue = rec.get("queue_position")
        if isinstance(queue, int) and not isinstance(queue, bool):
            view["queue_position"] = queue
        phase = rec.get("phase")
        if isinstance(phase, str) and phase:
            view["phase"] = phase
    partial = rec.get("partial_briefing")
    if not isinstance(partial, dict):
        return
    if mask_fn is None:
        view["partial_briefing"] = partial
        return
    user_partial = playbook_user_view(partial, mask_fn)
    if user_partial is not None:
        view["partial_briefing"] = user_partial


async def _investigation_response(
    request: Request,
    current_user: dict[str, Any],
    rec: dict[str, Any] | None,
    activity_since: int | None = None,
    with_alarm: bool = False,
    with_requery: bool = False,
) -> dict[str, Any]:
    """조사 레코드 → 응답(지연 poll · 사용자 마스킹/운영자 원문). 존 검사는 호출부가 먼저 한다.

    (plans/187 W14) 실시간 화면이 켜져 있으면 ① `activity_since`가 오면 허브 버퍼에서 실행 기록
    (`activity`·`phase`·`queue_position`·`elapsed_s`)을 더하고 ② 허브 폴러가 도는 조사는 sre_agent를
    따로 묻지 않고 허브 진척을 싣는다(종전 `POLL_LIVE` 자리). 꺼져 있으면 `activity_since`를
    무시한다(종전 응답 · 종전 지연 poll).
    (plans/195 W2) `with_requery`(조사 id 조회만 넘긴다)면 지연 poll로 갱신한 레코드로
    `requery`를 가산한다 — 플래그가 꺼져 있으면 종전 응답과 같다(`_attach_requery`).
    """
    from noise_gate.infrastructure.investigation_store import (
        ACTIVE_STATUSES,
        investigation_view,
    )

    if rec is None:
        return {"status": "none"}
    config = request.app.state.config
    poll_reason: str | None = None
    hub = _live_hub(request) if _live_view_enabled(config) else None
    entry: investigation_live_hub.LiveEntry | None = None
    if hub is not None and activity_since is not None and rec.get("investigation_id"):
        entry = await hub.snapshot(rec)
        rec = hub.current_record(rec)
    if rec.get("status") in ACTIVE_STATUSES:
        if hub is not None and hub.is_live(str(rec.get("investigation_id") or "")):
            rec = hub.current_record(rec)
        else:
            store = _investigation_store(config)
            if store is not None:
                rec, poll_reason = await _refresh_investigation(store, rec, config.noise_gate)
    operator = _is_alarm_operator(config, current_user)
    view = investigation_view(
        rec,
        mask_fn=None if operator else _investigation_mask_fn(config),
        operator_view=operator,
    )
    if poll_reason:
        view["reason"] = poll_reason
    _attach_live_progress(view, rec, None if operator else _investigation_mask_fn(config))
    if hub is not None and entry is not None and activity_since is not None:
        view.update(hub.activity_payload(
            entry, max(activity_since, -1), operator=operator,
            mask_fn=None if operator else _investigation_mask_fn(config),
        ))
    if (hub is not None and activity_since is not None) or with_alarm:
        # (plans/187 W17) 장애 진단 화면 — 어떤 알람을 조사하는지(판정 기록의 알람 요약)
        # (W23) 조사 대화 카드는 첫 조회에만 `with_alarm`으로 같은 요약을 받는다
        alarm = _investigation_alarm_brief(
            config, rec, None if operator else _investigation_mask_fn(config), operator,
            related=with_alarm,
        )
        if alarm:
            view["alarm"] = alarm
    # (plans/187 W9 · G-15) KB가 켜져 있으면 운영자 뷰 브리핑 **사본**에 참고 문서·KE 재발 신호를
    # 붙인다. 꺼져 있거나 사용자 뷰(원 브리핑 없음)면 종전과 비트 동일하다. (plans/187 W5a) 사용자
    # 뷰의 플레이북 표시 부분(`briefing`)에는 붙이지 않는다.
    if (operator and _kb_enabled(config) and isinstance(view.get("briefing"), dict)
            and rec.get("status") == "done"):
        view["briefing"] = await _kb_briefing_extras(config, current_user, view["briefing"])
    if with_requery:
        _attach_requery(view, rec, config, current_user)
    return view


#: (plans/195 W2) 질의 비활성 사유 — 화면이 버튼을 막고 그대로 보인다(사용자 문장).
_REQUERY_DB_DENIED = (
    "이 질의가 조회하는 DB에 대한 질의 권한이 없습니다. 관리자에게 권한을 요청하세요."
)
_REQUERY_DB_UNKNOWN = "이 질의가 조회할 DB를 확인하지 못해 보낼 수 없습니다."
_REQUERY_APM_DENIED = "제니퍼 APM 조회 권한이 없습니다. 관리자에게 권한을 요청하세요."


def _requery_enabled(config: Any) -> bool:
    """조사 질의 가산 플래그(기동 시 설정값)."""
    return bool(getattr(config.noise_gate, "investigation_requery_enabled", False))


def _attach_requery(
    view: dict[str, Any], rec: dict[str, Any], config: Any, current_user: dict[str, Any]
) -> None:
    """「질의응답에서 확인할 질의」(`requery`)를 응답에 싣는다(plans/195 W2 · 플래그 off면 무동작).

    입력은 운영자·사용자 구분 없이 **가린 사용자 뷰**의 플레이북이다(D-325 ③ `mask_fn` ·
    원문 미사용). 질의 권한은 질의응답과 같은 기준으로 대조한다 — 폴스타 축은
    `polestar_source`가 활성 DB ∩ 사용자 `allowed_db_ids`(`authorized_db_ids` — None·관리자 =
    전체)에 없거나 비었으면, 제니퍼 축은 관측 소스 `apm` 권한(`is_source_allowed`)이 없으면 항목에
    `disabled_reason`을 싣는다. 질의응답 쪽 범위 검사가 최종 방어선이다. 생성이 실패해도 조사
    응답은 깨지 않는다(빈 목록 · 경고 로그).
    """
    if not _requery_enabled(config):
        return
    from noise_gate.infrastructure.investigation_store import playbook_user_view
    from src.domain.investigation_requery import build_requery
    from src.routing.db_authz import authorized_db_ids, is_source_allowed
    from src.utils.log_masking import MASK as PARTIAL_MASK

    items: list[dict[str, Any]] = []
    try:
        user_view = playbook_user_view(rec.get("briefing"), _investigation_mask_fn(config))
        # 사용자 뷰 마스킹은 값 전체를 `mask_pattern`으로, 섞인 비밀값은 `mask_secrets` 표지로
        # 가린다 — 둘 다 넘겨 하나라도 섞인 대상은 생략한다
        items = build_requery(
            (user_view or {}).get("playbook"),
            mask_marker=(str(config.security.mask_pattern), PARTIAL_MASK),
        )
        role = current_user.get("role")
        allowed_dbs: list[str] | None = None
        for item in items:
            if item.get("axis") == "polestar":
                if allowed_dbs is None:
                    allowed_dbs = authorized_db_ids(
                        list(config.multi_db.get_active_db_ids()),
                        current_user.get("allowed_db_ids"), role,
                    )
                db_id = item.get("polestar_source")
                if not isinstance(db_id, str) or not db_id.strip():
                    item["disabled_reason"] = _REQUERY_DB_UNKNOWN
                elif db_id.strip() not in allowed_dbs:
                    item["disabled_reason"] = _REQUERY_DB_DENIED
            elif not is_source_allowed("apm", current_user.get("allowed_sources"), role):
                item["disabled_reason"] = _REQUERY_APM_DENIED
    except Exception as exc:  # noqa: BLE001 — 질의 생성 실패가 조사 응답을 깨지 않는다
        logger.warning(
            "조사 질의 생성 실패(빈 목록): investigation_id=%s err=%s",
            rec.get("investigation_id"), type(exc).__name__, exc_info=True,
        )
        items = []
    view["requery"] = items


#: 채팅 증상 질의 조사(plans/187 W12)의 저장 레코드 `origin`.
_CHAT_REQUEST_ORIGIN = "chat"


def _request_owner_view(
    config: Any, current_user: dict[str, Any], rec: dict[str, Any] | None
) -> bool:
    """사용자 요청 조사의 소유자 열람인가(plans/187 W12 교정) — 참이면 존 검사를 건너뛴다.

    `USER_REQUEST_KINDS` 레코드의 `owner_sub`가 요청자 `sub`와 같고, 그 레코드를 연 플래그가 켜져
    있을 때만 참이다 — `origin=chat` → `sre_chat_investigation_enabled` · 그 밖의 `diagnosis` →
    `investigation_additional_enabled` · `parent_alarm_id`가 있는 `manual` →
    `investigation_manual_enabled`. 플래그가 꺼져 있거나 비소유자면 거짓(종전 판정 그대로).
    """
    from noise_gate.infrastructure.investigation_store import KIND_DIAGNOSIS, USER_REQUEST_KINDS

    if rec is None or rec.get("kind") not in USER_REQUEST_KINDS:
        return False
    sub = current_user.get("sub")
    if not sub or sub != rec.get("owner_sub"):
        return False
    if rec.get("origin") == _CHAT_REQUEST_ORIGIN:
        return bool(getattr(config.noise_gate, "sre_chat_investigation_enabled", False))
    if rec.get("kind") == KIND_DIAGNOSIS:
        return _followup_enabled(config)
    return bool(rec.get("parent_alarm_id")) and _manual_enabled(config)


def _assert_unbound_request_owner(
    config: Any, current_user: dict[str, Any], rec: dict[str, Any] | None
) -> None:
    """알람 없는 사용자 요청 조사 인가(plans/187 교정 H1) — 소유자 또는 운영자(관리자)만.

    `USER_REQUEST_KINDS` 레코드 중 `alarm_ids`가 빈 것(채팅 조사 `origin=chat` 등)은 존으로 범위를
    정할 수 없다 — 전 존을 부여받은 비관리자도 비소유자면 403이다(W10 `diagnosis`의
    `_assert_diagnosis_owner`와 대칭). 알람이 걸린 레코드·자동 조사는 건드리지 않는다
    (존 판정 그대로).

    Raises:
        HTTPException: 비소유·비운영자(403).
    """
    from noise_gate.infrastructure.investigation_store import USER_REQUEST_KINDS

    if rec is None or rec.get("kind") not in USER_REQUEST_KINDS or rec.get("alarm_ids"):
        return
    if _is_alarm_operator(config, current_user):
        return
    sub = current_user.get("sub")
    if not sub or sub != rec.get("owner_sub"):
        raise HTTPException(status_code=403, detail="이 사용자 요청 조사를 볼 권한이 없습니다.")


# ─── 조사 진행 실시간 허브·SSE (plans/187 W14 · §5.6.3 ⓒ · G-39·G-41·G-42) ─────────────
#
# 기본 off(`NOISE_INVESTIGATION_LIVE_VIEW_ENABLED`)면 두 라우트가 404(인증보다 먼저)이고 조회 경로의
# `activity_since`는 무시된다(비트 동일). **등록 순서 주의**: 아래 두 경로는
# `/{investigation_id}`보다 먼저 등록해야 한다(뒤에 두면 `stream`·`active`가 조사 id로 먹힌다).


def _live_view_enabled(config: Any) -> bool:
    """실시간 화면 플래그(기동 시 설정값)."""
    return bool(getattr(config.noise_gate, "investigation_live_view_enabled", False))


async def _require_live_view_enabled(request: Request) -> None:
    """플래그 off면 미등록 라우트와 같은 404 — 인증보다 먼저 판정한다."""
    if not _live_view_enabled(request.app.state.config):
        raise HTTPException(status_code=404, detail="Not Found")


def _live_hub(request: Request) -> investigation_live_hub.InvestigationLiveHub:
    """앱 단위 허브(첫 사용 때 만든다 · 종료는 lifespan `aclose`). 요약 보관은 조사 저장소 옆."""
    hub = getattr(request.app.state, "investigation_live_hub", None)
    if hub is None:
        from pathlib import Path

        from noise_gate.infrastructure.investigation_activity_store import (
            DEFAULT_FILENAME,
            InvestigationActivityStore,
        )

        config = request.app.state.config
        path = config.noise_gate.investigation_store_path
        hub = investigation_live_hub.InvestigationLiveHub(
            config.noise_gate,
            store_factory=lambda: _investigation_store(config),
            activity_store=(
                InvestigationActivityStore(Path(path).parent / DEFAULT_FILENAME) if path else None
            ),
        )
        request.app.state.investigation_live_hub = hub
    return hub


async def require_live_user(
    request: Request,
    token: str | None = Query(
        None, description="내부망 폴백 토큰(사용자 토큰만 — 운영자 토큰은 Authorization 헤더로)"
    ),
) -> dict[str, Any]:
    """실시간 경로 인증 — 운영자(break-glass) 토큰은 헤더로만, 사용자 토큰은 쿠키 → 헤더 → 쿼리.

    `/admin/noise/stream`과 같은 판정이다: 운영자 토큰은 운영자 시크릿 서명 + `type == "admin"` +
    `sub`, 사용자 토큰은 사용자 시크릿 서명 + `type == "user"` + 활성 사용자(`resolve_stream_user`).
    토큰이 없거나 무효면 401(종류가 다른 토큰 포함). 개발 모드(인증 꺼짐)는 익명 사용자.
    """
    from src.api.dependencies import _try_break_glass_admin

    config = request.app.state.config
    user: dict[str, Any] | None = None
    if config.auth.enabled:
        user = _try_break_glass_admin(request, request.headers.get("authorization"))
    if user is None:
        user = await resolve_stream_user(request, token)
    if config.auth.enabled and user is None:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    return user or {}


def _live_scope(
    request: Request, current_user: dict[str, Any], operator: bool
) -> Callable[[dict[str, Any]], bool]:
    """목록 범위(G-41) — 관리자 = 모든 미종결 조사 · 사용자 = 내가 요청한 조사만.

    사용자 몫은 `owner_sub`가 나이고 내가 요청한 조사(채팅 기원 `origin=chat` · 조사 대화
    추가 조사 `kind=diagnosis` · [조사 시작]/[분석 요청]으로 직접 접수한 분석 `kind=manual`)이며
    조회 인가를 통과하는 것이다. 알람 기원 자동 조사·남이 접수한 조사는 목록 밖이지만
    `watch`로는 열린다(존 검사).
    """
    from noise_gate.infrastructure.investigation_store import KIND_DIAGNOSIS, KIND_MANUAL

    if operator:
        return lambda rec: True
    sub = current_user.get("sub")

    def _in_scope(rec: dict[str, Any]) -> bool:
        if not sub or rec.get("owner_sub") != sub:
            return False
        if rec.get("origin") != _CHAT_REQUEST_ORIGIN and rec.get("kind") not in (
            KIND_DIAGNOSIS,
            KIND_MANUAL,
        ):
            return False
        try:
            _assert_investigation_access(request, current_user, rec)
        except HTTPException:
            return False
        return True

    return _in_scope


@router.get(
    "/alarm/investigations/active",
    summary="진행 중 조사 목록(장애 진단 화면)",
    description=(
        "목록 범위의 미종결 조사와 실시간 허브의 진척(phase·대기 순번·경과)을 반환합니다 — "
        "조사 서비스를 추가로 호출하지 않습니다.<br/>"
        "관리자(운영자 토큰 포함)는 모든 미종결 조사, 사용자는 자기가 요청한 조사"
        "(채팅·추가 조사·직접 접수한 분석)만 봅니다.<br/>"
        "`service_load`는 조사 서비스 부하 게이지(진행·대기·상한 · 10초 캐시 · "
        "조회 실패면 null)입니다.<br/>"
        "실시간 화면(NOISE_INVESTIGATION_LIVE_VIEW_ENABLED)이 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
)
async def list_active_investigations(
    request: Request,
    _enabled: None = Depends(_require_live_view_enabled),
    current_user: dict[str, Any] = Depends(require_live_user),
) -> dict[str, Any]:
    """미종결 조사 목록(첫 화면·SSE 끊김 대체용)."""
    config = request.app.state.config
    hub = _live_hub(request)
    operator = _is_alarm_operator(config, current_user)
    in_scope = _live_scope(request, current_user, operator)
    recs = [rec for rec in hub.active_records(fresh=True) if in_scope(rec)]
    return {
        "items": hub.list_items(
            recs, operator=operator, mask_fn=None if operator else _investigation_mask_fn(config)
        ),
        "poll_seconds": hub.poll_seconds,
        # (plans/187 W18) 「이벤트 찾아 조사」 — 다시 분석 접수가 켜져 있을 때만 화면이 칸을 보인다
        "event_search": _manual_enabled(config),
        # (plans/194 W5 · C1) 조사 서비스 부하 게이지(10초 캐시 · 실패·구버전은 null)
        "service_load": await _service_load(request),
    }


@router.get(
    "/alarm/investigations/stream",
    summary="조사 진행 실시간 스트림(SSE)",
    description=(
        "브라우저당 연결 1개로 여러 조사를 다중화합니다. `watch=<id>:<seq>,…`"
        "(열린 하위 탭과 마지막 seq)을 주면 그 조사의 실행 기록을 seq 뒤부터 보냅니다.<br/>"
        "이벤트: `summary`(목록 범위 `items` + 조사 서비스 부하 `service_load` · 바뀔 때만) · "
        "`activity`/`partial`/`done`/`poll_error`(watch 조사만) · "
        "`denied`(권한 없는 watch id) · `ping`(25초). `id:`는 watch와 같은 꼴이라 "
        "`Last-Event-ID`로 이어 받습니다.<br/>"
        "인증: 쿠키(user_token) → Authorization 헤더(운영자 토큰은 헤더만) → 쿼리 토큰. 실패 401 · "
        "꺼져 있으면 404."
    ),
    tags=["alarm"],
)
async def investigation_live_stream(
    request: Request,
    watch: str | None = Query(None, description="`<조사 id>:<마지막 seq>`를 쉼표로 잇는다"),
    _enabled: None = Depends(_require_live_view_enabled),
    current_user: dict[str, Any] = Depends(require_live_user),
) -> StreamingResponse:
    """조사 SSE — watch 조사는 연결(재연결 포함) 때 조사별로 권한을 검사한다(거부는 `denied`)."""
    config = request.app.state.config
    hub = _live_hub(request)
    operator = _is_alarm_operator(config, current_user)
    cursors = investigation_live_hub.parse_watch(watch)
    if len(cursors) > investigation_live_hub.WATCH_MAX_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"watch 항목은 {investigation_live_hub.WATCH_MAX_ITEMS}개까지입니다.",
        )
    investigation_live_hub.merge_last_event_id(cursors, request.headers.get("last-event-id"))
    store = _investigation_store(config)
    watch_recs: dict[str, dict[str, Any]] = {}
    denied: list[dict[str, Any]] = []
    for investigation_id in list(cursors):
        reason: str | None = None
        rec: dict[str, Any] | None = None
        if not investigation_live_hub.INVESTIGATION_ID_RE.match(investigation_id):
            reason = "invalid"
        else:
            rec = store.get_by_investigation(investigation_id) if store is not None else None
            if rec is None:
                reason = "not_found"
            else:
                try:
                    _assert_investigation_access(request, current_user, rec)
                except HTTPException:
                    reason = "forbidden"
        if reason is not None or rec is None:
            if not operator and reason in ("not_found", "forbidden"):
                reason = "unavailable"  # 사용자에게는 남의 id 존재를 구분해 주지 않는다
            cursors.pop(investigation_id)
            denied.append({"investigation_id": investigation_id, "reason": reason})
            continue
        watch_recs[investigation_id] = rec
    ctx = investigation_live_hub.StreamContext(
        operator=operator,
        mask_fn=None if operator else _investigation_mask_fn(config),
        in_scope=_live_scope(request, current_user, operator),
        watch=watch_recs,
        cursors=cursors,
        denied=denied,
        # (plans/194 W5 · C1) `summary` 프레임 = `{items, service_load}`
        service_load=lambda: _service_load(request),
    )
    return StreamingResponse(
        investigation_live_hub.stream_events(hub, ctx, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/alarm/investigations/by-alarm/{alarm_id}",
    summary="알람의 자동 조사 브리핑 조회",
    description=(
        "알람 1건에 걸린 자동 조사의 상태·브리핑을 반환합니다(없으면 status=none).<br/>"
        "여러 건이면 종결된 종합 조사 → 그 알람의 단독 조사 → (알람 다시 분석이 켜져 있으면) "
        "사용자 요청 분석 → 최신 순으로 고릅니다. 켜져 있으면 응답에 "
        "`previous`(나머지 조사 목록)를 더합니다.<br/>"
        "미종결이면 조사 서비스에 재조회합니다(조사마다 10초에 1회). 사용자 경로는 마스킹, "
        "관리자는 원문과 원 브리핑을 받습니다.<br/>"
        "존 권한은 이 알람과 조사에 걸린 알람 전부로 검사합니다 — 존 제한 사용자는 db_id를 "
        "확인할 수 없는 알람도 403입니다.<br/>"
        "`service_load`는 조사 서비스 부하 게이지(10초 캐시 · 조회 실패면 null)입니다."
    ),
    tags=["alarm"],
)
async def get_alarm_investigation(
    alarm_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
    activity_since: int | None = Query(
        None, description="(실시간 화면 켜짐일 때만) 실행 기록 커서 — 이 seq 뒤의 이벤트를 더한다"
    ),
) -> dict[str, Any]:
    """알람 id → 최신 조사(plans/153 §4.2).

    (plans/187 W11) 알람 다시 분석이 켜져 있으면 사용자 요청 분석(`kind="manual"`)을 자기 단독 다음
    우선순위로 보고, 고른 것 외의 조사를 `previous`(최신 먼저)로 더한다. 꺼져 있으면 종전과 같다.
    """
    config = request.app.state.config
    store = _investigation_store(config)
    manual = _manual_enabled(config)
    rec = store.get_by_alarm(alarm_id, include_manual=manual) if store is not None else None
    # (plans/187 W12) 고른 레코드가 내 사용자 요청 조사면 존 검사 없이 연다(`_request_owner_view`).
    # 그때는 이 알람의 다른 조사 목록(`previous`)을 붙이지 않는다 — 존 검사를 거치지 않았으므로.
    # 알람 수신 권한이 없는 사용자는 403. 레코드가 없어도 이 알람의 존을 먼저 본다 — 권한 없는
    # (또는 존을 모르는) 알람은 존재 여부와 무관하게 403이다(plans/194 W5: 판정 목록과 같은 함수).
    owner_view = assert_alarm_investigation_access(request, current_user, alarm_id, rec)
    view = await _investigation_response(request, current_user, rec, activity_since)
    if manual and not owner_view:
        view["previous"] = _previous_investigations(
            request, current_user, store, alarm_id, (rec or {}).get("investigation_id")
        )
    # (plans/194 W5 · C1) 조사 서비스 부하 게이지(10초 캐시 · 실패·구버전은 null)
    view["service_load"] = await _service_load(request)
    return view


@router.get(
    "/alarm/investigations/{investigation_id}",
    summary="자동 조사 브리핑 조회(조사 id)",
    description=(
        "조사 id의 최신 상태·브리핑을 반환합니다(없으면 status=none).<br/>"
        "존 권한은 조사에 걸린 알람 전부로 검사합니다 — 존 제한 사용자는 하나라도 타 존이거나 "
        "db_id를 확인할 수 없으면 403입니다. 알람이 걸리지 않은 사용자 요청 조사(채팅 조사 등)는 "
        "요청자 본인 또는 관리자만 엽니다.<br/>"
        "(NOISE_INVESTIGATION_REQUERY_ENABLED 켜짐) `requery`에 질의응답에서 확인할 질의를 "
        "싣습니다 — 가린 조사 내용에서 만들고, 조회 범위 밖이면 `disabled_reason`을 붙입니다."
    ),
    tags=["alarm"],
)
async def get_investigation(
    investigation_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
    activity_since: int | None = Query(
        None, description="(실시간 화면 켜짐일 때만) 실행 기록 커서 — 이 seq 뒤의 이벤트를 더한다"
    ),
    with_alarm: bool = Query(
        False, description="조사 대상 알람 요약(`alarm`)을 싣는다 — 조사 대화 카드의 첫 조회"
    ),
) -> dict[str, Any]:
    """조사 id → 최신 조사(plans/153 §4.2).

    (plans/187 W10 · plans/155 G-7) 추가 조사가 켜져 있고 레코드가 `kind="diagnosis"`면 알람 존 판정
    대신 **소유자 또는 관리자**만 허용한다. 꺼져 있으면 종전 판정 그대로다. 인가는
    `_assert_investigation_access`(실시간 스트림 watch와 공용 · plans/187 W14에서 추출 — 동작 불변).
    """
    config = request.app.state.config
    store = _investigation_store(config)
    rec = store.get_by_investigation(investigation_id) if store is not None else None
    _assert_investigation_access(request, current_user, rec)
    view = await _investigation_response(
        request, current_user, rec, activity_since, with_alarm, with_requery=True
    )
    if rec is not None and _followup_enabled(config) and _is_diagnosis(rec):
        return _with_notice(view)
    return view


def _assert_investigation_access(
    request: Request, current_user: dict[str, Any], rec: dict[str, Any] | None
) -> None:
    """조사 id 조회 인가 — `get_investigation`과 실시간 스트림 `watch`가 같이 쓴다.

    순서: 내 사용자 요청 조사(plans/187 W12 — 존 검사 없이 연다) → 알람 수신 권한(없으면
    레코드 유무와 무관하게 403) → 알람 없는 사용자 요청 조사는 소유자·운영자만(교정 H1) →
    추가 조사(켜짐)는 소유자·관리자만 → 그 밖에는 조사에 걸린 알람 전부의 존 검사(fail-closed).

    Raises:
        HTTPException: 권한 없음(403).
    """
    config = request.app.state.config
    if rec is not None and _request_owner_view(config, current_user, rec):
        return
    _alarm_zone_scope(request, current_user)
    _assert_unbound_request_owner(config, current_user, rec)
    if rec is not None and _followup_enabled(config) and _is_diagnosis(rec):
        _assert_diagnosis_owner(config, current_user, rec)
        return
    if rec is not None:
        _assert_investigation_zone(
            request, current_user, list(rec.get("alarm_ids") or []), rec.get("alarm_db_ids")
        )


# ─── 조사 대화의 「추가 조사 요청」 (plans/187 W10 · D-341 ⑭ · G-21 · plans/155 공용부) ───
#
# 부모 알람 조사에서 결정적 초점 + 보충 한 줄로 `sre_diagnose`를 비동기 접수한다.
# 기본 off면 두 라우트가 404(미등록과 같은 응답 — 인증보다 먼저 판정)이고 저장소에
# 아무것도 쓰지 않는다. 초점·대상·prior_findings는 `src/domain/investigation_followup.py`
# (순수), 질문 문장은 `src/prompts/investigation_followup.py`.

#: 감사·저장의 진입점 값(`log_investigation(entry_point=…)` — 문자열 제약 없음).
_FOLLOWUP_ENTRY_POINT = "investigation_chat"
#: 같은 (부모, 초점) 재접수를 새로 받는 종결 상태 — 실패한 것은 다시 요청할 수 있다.
_FOLLOWUP_RETRYABLE: frozenset[str] = frozenset({"failed", "timeout", "rejected", "not_found"})


class InvestigationFollowupRequest(BaseModel):
    """추가 조사 접수 요청 본문."""

    focus: str = Field(
        ..., min_length=1, max_length=64, description="조사 초점 id(목록 API의 focuses[].id)"
    )
    note: str | None = Field(
        None, max_length=2000,
        description="보충 한 줄(선택 · 개행 제거 · 설정 상한 이내 · 마스킹)",
    )


def _followup_enabled(config: Any) -> bool:
    """추가 조사 플래그(기동 시 설정값 — 요청마다 다시 해석하지 않는 설정 객체를 읽는다)."""
    return bool(getattr(config.noise_gate, "investigation_additional_enabled", False))


async def _require_followup_enabled(request: Request) -> None:
    """플래그 off면 미등록 라우트와 같은 404 — 인증·저장소 접근보다 먼저 판정한다."""
    if not _followup_enabled(request.app.state.config):
        raise HTTPException(status_code=404, detail="Not Found")


def _is_diagnosis(rec: dict[str, Any]) -> bool:
    from noise_gate.infrastructure.investigation_store import KIND_DIAGNOSIS

    return rec.get("kind") == KIND_DIAGNOSIS


def _assert_diagnosis_owner(config: Any, current_user: dict[str, Any], rec: dict[str, Any]) -> None:
    """추가 조사 레코드 인가 — 소유자(`sub` = `owner_sub`) 또는 관리자(개발 모드 포함)만."""
    if _is_alarm_operator(config, current_user):
        return
    sub = current_user.get("sub")
    if not sub or sub != rec.get("owner_sub"):
        raise HTTPException(status_code=403, detail="이 추가 조사를 볼 권한이 없습니다.")


def _with_notice(view: dict[str, Any]) -> dict[str, Any]:
    """추가 조사 응답의 고정 표기(「가설 · 참고용」)."""
    from src.domain.investigation_followup import HYPOTHESIS_NOTICE

    return {**view, "hypothesis_notice": HYPOTHESIS_NOTICE}


def _followup_error(
    status_code: int, code: str, reason: str, **extra: Any
) -> HTTPException:
    """추가 조사 라우트의 거부 — `detail = {code, reason[, …extra]}`(사유 노출 · 침묵 금지).

    `extra`(plans/194 W5)는 거부별 가산 키다(예: 시간당 상한의 `retry_after_seconds`).
    """
    return HTTPException(
        status_code=status_code, detail={"code": code, "reason": reason, **extra}
    )


#: 장애 진단 화면 머리의 알람 요약 키(판정 기록 뷰에서 고른다 — 조건 로그는 운영자 뷰에만 남는다).
_ALARM_BRIEF_KEYS: tuple[str, ...] = (
    "alarm_id", "alarm_name", "server_name", "hostname", "resource_name", "resource_type",
    "alarm_time", "ts", "tier", "stage_label", "reason", "condition_log",
)
#: (W23) 사건 묶음 조사의 「함께 조사하는 알람」 키와 상한 — 조사 대화 카드 첫 조회에만 붙인다.
_RELATED_BRIEF_KEYS: tuple[str, ...] = (
    "alarm_id", "alarm_name", "server_name", "hostname", "alarm_time", "ts",
)
_RELATED_BRIEF_MAX = 5


def _alarm_source_text(db_id: str) -> str:
    """(W24) 알람 dbId → 사람이 읽는 발생 소스(존·위치·수집 시스템 — 레지스트리에서 푼다).

    DB 소스는 「{존 약칭} {사이트} {제품명}」(예: 공동존 김포 폴스타), 존이 없으면 DB 표시 이름이다.
    APM 소스(`{family}_{소스 id}`)는 레지스트리 소스 이름에 존 약칭을 붙이고
    (이미 들어 있으면 그대로), 소스 표에 없는 단일 설정(`db_id == family`)은
    「{시스템}(존 미지정)」이다. 못 풀면 dbId 그대로.
    """
    from noise_gate.application.server_identity import source_labels_for, zone_labels_for
    from src.routing.registry import get_registry

    _, zone_label, site = zone_labels_for(db_id)
    zone = zone_label.split("(")[0].split("—")[0].strip()
    registry = get_registry()
    entry = registry.get(db_id)
    if entry is not None:
        product = source_labels_for(db_id, zone_label, site)[0]
        where = site if site and zone and zone in site else " ".join(p for p in (zone, site) if p)
        return f"{where} {product}" if where and product else (entry.display_name or db_id)
    found = registry.alarm_source(db_id)
    if found is not None:
        label = found[1].label
        return label if not zone or zone in label else f"{label}({zone})"
    solution = next((s for s in registry.solutions() if s.family and s.family == db_id), None)
    return f"{solution.label.split('(')[0].strip()}(존 미지정)" if solution else db_id


def _investigation_alarm_brief(
    config: Any,
    rec: dict[str, Any],
    mask_fn: Callable[[str], str] | None,
    operator: bool,
    related: bool = False,
) -> dict[str, Any] | None:
    """조사에 걸린 첫 알람(없으면 `parent_alarm_id`)의 판정 기록 요약(plans/187 W17).

    판정 기록 뷰(`get_decision`)가 사용자 경로의 운영자 전용 키 제거·마스킹을 이미 한다.
    존 검사는 호출부(`_assert_investigation_access`)가 먼저 했다. 기록이 없으면 None.
    (W23) `related`면 나머지 알람(상한 `_RELATED_BRIEF_MAX`)의 이름·서버·시각을 `related`로 싣는다 —
    알람마다 판정 기록을 다시 읽으므로 재조회마다 부르는 장애 진단 경로에서는 끈다.
    """
    from noise_gate.infrastructure.decision_store import DecisionStore

    alarm_ids = list(rec.get("alarm_ids") or [])
    alarm_id = next(iter(alarm_ids), None) or rec.get("parent_alarm_id")
    if not alarm_id:
        return None
    ng = config.noise_gate
    store = DecisionStore(
        ng.decision_store_path,
        bool(getattr(ng, "decision_store_enabled", True)),
        int(getattr(ng, "decision_store_max_lines", 20000)),
    )
    decision = store.get_decision(str(alarm_id), mask_fn=mask_fn, operator_view=operator)
    if not decision:
        return None
    brief = {k: decision[k] for k in _ALARM_BRIEF_KEYS if decision.get(k) not in (None, "")}
    signals = decision.get("signals")
    if isinstance(signals, dict) and isinstance(signals.get("severity"), (int, float)):
        brief["severity"] = signals["severity"]
    if decision.get("db_id"):
        # (W24) 알람이 어디서 났는지 — 은행존 제니퍼인지 공동존 김포 폴스타인지
        brief["db_id"] = decision["db_id"]
        brief["source"] = _alarm_source_text(str(decision["db_id"]))
    if len(alarm_ids) > 1:
        # (W23) 사건 묶음 조사 — 요약은 첫 알람이고 같은 조사에 걸린 알람 수를 함께 보인다
        brief["alarm_count"] = len(alarm_ids)
    if related and len(alarm_ids) > 1:
        rows = []
        for other in alarm_ids[1:1 + _RELATED_BRIEF_MAX]:
            d = store.get_decision(str(other), mask_fn=mask_fn, operator_view=operator)
            d = d or {"alarm_id": other}
            row = {k: d[k] for k in _RELATED_BRIEF_KEYS if d.get(k) not in (None, "")}
            if d.get("db_id"):
                row["source"] = _alarm_source_text(str(d["db_id"]))
            rows.append(row)
        brief["related"] = rows
    return brief


def _decision_alarm_info(config: Any, alarm_id: str) -> dict[str, Any]:
    """판정 기록의 서버명·db_id·시각 — 부모 브리핑에 대상이 없을 때의 폴백(없으면 빈 dict)."""
    from noise_gate.infrastructure.decision_store import DecisionStore

    ng = config.noise_gate
    decision = DecisionStore(
        ng.decision_store_path,
        bool(getattr(ng, "decision_store_enabled", True)),
        int(getattr(ng, "decision_store_max_lines", 20000)),
    ).get_decision(alarm_id)
    return dict(decision or {})


def _followup_parent(
    request: Request, current_user: dict[str, Any], investigation_id: str
) -> tuple[InvestigationStore, dict[str, Any]]:
    """부모 알람 조사 레코드 — 알람 존 검사까지 끝낸 것만 돌려준다.

    Raises:
        HTTPException: 저장소 미설정 503 · 없음 404 · 부모가 추가 조사 400 · 존 권한 없음 403.
    """
    _alarm_zone_scope(request, current_user)
    store = _investigation_store(request.app.state.config)
    if store is None:
        raise _followup_error(503, "store_unconfigured", "조사 저장소 경로가 설정되지 않았습니다.")
    parent = store.get_by_investigation(investigation_id)
    if parent is None:
        raise _followup_error(404, "parent_not_found", "부모 조사를 찾지 못했습니다.")
    if _is_diagnosis(parent):
        raise _followup_error(
            400, "parent_not_alarm", "추가 조사는 알람 조사에서만 요청할 수 있습니다."
        )
    _assert_investigation_zone(
        request, current_user, list(parent.get("alarm_ids") or []), parent.get("alarm_db_ids")
    )
    return store, parent


def _followup_target(config: Any, parent: dict[str, Any]) -> Any:
    """대상·사건창 — 부모 브리핑 `playbook.scope`, 없으면 첫 알람의 판정 기록(결정적)."""
    from src.domain.investigation_followup import resolve_target

    alarm_ids = list(parent.get("alarm_ids") or [])
    info = _decision_alarm_info(config, alarm_ids[0]) if alarm_ids else {}
    db_id = (parent.get("alarm_db_ids") or {}).get(alarm_ids[0]) if alarm_ids else None
    briefing = parent.get("briefing") if isinstance(parent.get("briefing"), dict) else None
    return resolve_target(
        briefing,
        fallback_server_name=info.get("server_name"),
        fallback_db_id=db_id or info.get("db_id"),
        fallback_reference_time=info.get("ts"),
    )


async def _audit_followup(
    current_user: dict[str, Any],
    target: Any,
    outcome: str,
    *,
    authz: dict[str, Any] | None = None,
    degraded: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> None:
    """추가 조사 감사 1건(성공·거부·상한·실패 모두 · 실패해도 응답을 막지 않는다)."""
    from src.security import audit_logger

    targets = (
        [{"server_name": target.server_name, "hostname": target.hostname, "db_id": target.db_id}]
        if target is not None else None
    )
    try:
        await audit_logger.log_investigation(
            entry_point=_FOLLOWUP_ENTRY_POINT,
            targets=targets,
            outcome=outcome,
            user_id=current_user.get("sub"),
            backend="sre_agent",
            authz=authz,
            degraded=degraded,
            **extra,
        )
    except Exception:  # noqa: BLE001 — 감사 기록 실패가 응답을 막지 않는다(사유는 로그)
        logger.warning("추가 조사 감사 기록 실패", exc_info=True)


def _submitted_epoch(rec: dict[str, Any]) -> float | None:
    return investigation_request_limit.submitted_epoch(rec)


def _user_request_slot(
    store: InvestigationStore, ng: Any, sub: str
) -> AbstractAsyncContextManager[tuple[str, str] | None]:
    """사용자별 임계구역(plans/187 교정 · M1) — 「상한 검사 → 접수 레코드 기록」을 한 잠금 안에서.

    `async with _user_request_slot(...) as limited:` 블록 안에서 거부하거나 접수·기록을 끝낸다.
    `sub`가 비면 `owner_unknown`(fail-closed)을 내준다.
    """
    return investigation_request_limit.user_request_slot(
        store, ng, sub, refresh=_refresh_investigation
    )


def _limit_status(code: str) -> int:
    """상한 거부 코드 → HTTP 상태(요청자 식별 불가는 403 · 상한 초과는 429)."""
    return 403 if code == investigation_request_limit.REASON_OWNER_UNKNOWN else 429


#: 사용자 시간당 상한 거부 코드(`investigation_request_limit.user_request_limit`).
_HOURLY_LIMIT_CODE = "hourly_limit"
#: 조사 서비스의 「같은 서버 진행 중」 거부 코드(sre_agent L-4 가드).
_HOST_IN_FLIGHT_REASON = "host_investigation_in_flight"


def _limit_extra(store: InvestigationStore, ng: Any, sub: str, code: str) -> dict[str, Any]:
    """상한 거부의 가산 키(plans/194 W5 · C3) — 시간당 상한이면 `retry_after_seconds`."""
    if code != _HOURLY_LIMIT_CODE:
        return {}
    retry = investigation_request_limit.hourly_retry_after(store, ng, sub)
    return {"retry_after_seconds": retry} if retry is not None else {}


def _target_names(*names: Any) -> set[str]:
    """서버 식별 이름 집합(소문자 · 빈 값 제외) — 같은 서버 진행 중 조사 찾기용."""
    return {str(n).strip().lower() for n in names if str(n or "").strip()}


def _record_host_names(config: Any, rec: dict[str, Any]) -> set[str]:
    """조사 레코드의 대상 서버 이름 — `subject`의 hostname·server_name, 없으면(알람 조사) 첫 알람의
    판정 기록 hostname·server_name."""
    raw_subject = rec.get("subject")
    subject: dict[str, Any] = raw_subject if isinstance(raw_subject, dict) else {}
    names = _target_names(subject.get("hostname"), subject.get("server_name"))
    alarm_ids = rec.get("alarm_ids") or []
    if names or not alarm_ids:
        return names
    decision = _manual_decision(config, str(alarm_ids[0])) or {}
    return _target_names(decision.get("hostname"), decision.get("server_name"))


def _existing_host_investigation(
    request: Request,
    current_user: dict[str, Any],
    store: InvestigationStore,
    names: set[str],
    exclude_id: str,
) -> str | None:
    """같은 서버의 진행 중 조사 id(plans/194 W5 · C3) — 요청자가 열 수 있는 것만(없으면 None).

    조사 서비스가 `host_investigation_in_flight`로 거부했을 때 본체 조사 저장소의 미종결 레코드에서
    대상 이름(hostname·server_name — 대소문자 무시)이 겹치는 최신 조사를 찾는다. 열람 인가는 조사 id
    조회와 같은 `_assert_investigation_access`다(화면이 그 id로 조사를 연다) — 통과 못 하면 id를
    알려 주지 않는다(존재를 드러내지 않는다).
    """
    from noise_gate.infrastructure.investigation_store import ACTIVE_STATUSES

    if not names:
        return None
    config = request.app.state.config
    for latest in reversed(store.latest_records()):
        investigation_id = str(latest.get("investigation_id") or "")
        if latest.get("status") not in ACTIVE_STATUSES or investigation_id == exclude_id:
            continue
        rec = store.get_by_investigation(investigation_id)
        if rec is None or not (_record_host_names(config, rec) & names):
            continue
        try:
            _assert_investigation_access(request, current_user, rec)
        except HTTPException:
            continue
        return investigation_id
    return None


async def _service_load(request: Request) -> dict[str, Any] | None:
    """조사 서비스 부하 게이지(plans/194 W5 · C1 — 앱 캐시 10초 · 실패·구버전은 None)."""
    from src.api.investigation_service_health import health_cache

    return await health_cache(request.app.state).service_load()


@router.get(
    "/alarm/investigations/{investigation_id}/followups",
    summary="부모 조사의 추가 조사 목록·초점",
    description=(
        "알람 조사 1건(부모)의 추가 조사 목록과 선택 가능한 조사 초점을 반환합니다.<br/>"
        "부모의 존 권한을 먼저 검사합니다. 일반 사용자는 자기 것만, 관리자는 전부 받습니다.<br/>"
        "`NOISE_INVESTIGATION_ADDITIONAL_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_followup_enabled)],
)
async def list_investigation_followups(
    investigation_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """부모 조사 → `{parent_investigation_id, focuses[], items[], hypothesis_notice}`."""
    from noise_gate.infrastructure.investigation_store import investigation_view
    from src.domain.investigation_followup import HYPOTHESIS_NOTICE, focus_options

    config = request.app.state.config
    store, parent = _followup_parent(request, current_user, investigation_id)
    operator = _is_alarm_operator(config, current_user)
    owner = None if operator else str(current_user.get("sub") or "")
    mask_fn = None if operator else _investigation_mask_fn(config)
    items = [
        investigation_view(rec, mask_fn=mask_fn, operator_view=operator)
        | {"submitted_at": rec.get("submitted_at")}
        for rec in store.list_diagnoses(owner_sub=owner, parent_investigation_id=investigation_id)
    ]
    briefing = parent.get("briefing") if isinstance(parent.get("briefing"), dict) else None
    return {
        "parent_investigation_id": investigation_id,
        "focuses": focus_options(briefing),
        "items": items,
        "hypothesis_notice": HYPOTHESIS_NOTICE,
    }


@router.post(
    "/alarm/investigations/{investigation_id}/followup",
    summary="추가 조사 요청(조사 대화 · 비동기)",
    description=(
        "부모 알람 조사의 브리핑을 바탕으로 sre_agent 진단(`sre_diagnose`)을 "
        "비동기로 접수합니다.<br/>"
        "질문은 코드 템플릿 + 조사 초점 + 보충 한 줄(선택)이며, "
        "이미 수집된 판정 요약(LLM 서술 제외)을 함께 넘깁니다. "
        "결과는 「가설 · 참고용」이며 원 브리핑을 바꾸지 않습니다.<br/>"
        "인가는 `HOST_AUTHZ_MODE`(기본 관리자만) · 사용자별 진행 중 1건 · 시간당 상한 · 같은 "
        "(부모, 초점) 재접수는 기존 조사 id를 돌려줍니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_followup_enabled)],
)
async def request_investigation_followup(
    investigation_id: str,
    body: InvestigationFollowupRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """추가 조사 접수 → `{investigation_id, status, reused, reason?, hypothesis_notice}`.

    조사 서비스가 거부했을 때만 `reason_text`(사유 코드의 한국어 문구)를 더 싣는다(plans/194 W1).
    (plans/194 W5 · C3) 같은 서버 진행 중 거부면 열 수 있는 진행 중 조사
    `existing_investigation_id`, 사용자 시간당 상한 429면 `detail.retry_after_seconds`를 더한다.
    """
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod
    from noise_gate.infrastructure.investigation_store import KIND_DIAGNOSIS
    from src.domain.host_authz import Principal, authorize_host_investigation
    from src.domain.investigation_followup import (
        HYPOTHESIS_NOTICE,
        ORIGIN_INVESTIGATION_CHAT,
        build_prior_findings,
        resolve_focus,
        sanitize_note,
    )
    from src.prompts.investigation_followup import render_followup_question
    from src.security.audit_logger import (
        INVESTIGATION_DENIED,
        INVESTIGATION_FAILED,
        INVESTIGATION_OK,
    )

    config = request.app.state.config
    ng = config.noise_gate
    store, parent = _followup_parent(request, current_user, investigation_id)
    briefing = parent.get("briefing") if isinstance(parent.get("briefing"), dict) else None
    sub = str(current_user.get("sub") or "")

    target = _followup_target(config, parent)
    if target is None:
        raise _followup_error(
            400, "target_unresolved", "부모 조사에서 조사 대상 호스트를 찾지 못했습니다."
        )

    # 호스트 인가 — 위임 직전 실행 경계에서 role 클레임으로 판정한다(Plan 78 W3-5 · 거부도 감사).
    decision = authorize_host_investigation(
        mode=getattr(getattr(config, "host_authz", None), "mode", None),
        principal=Principal(
            role=current_user.get("role"),
            user_id=sub or None,
            allowed_db_ids=current_user.get("allowed_db_ids"),
            entry_point=_FOLLOWUP_ENTRY_POINT,
        ),
        hostname=target.label,
        db_id=target.db_id,
    )
    if not decision.allowed:
        await _audit_followup(
            current_user, target, INVESTIGATION_DENIED, authz=decision.as_audit(),
            parent_investigation_id=investigation_id, focus=body.focus,
        )
        raise _followup_error(
            403, "authz_denied", f"추가 조사를 요청할 권한이 없습니다(사유: {decision.reason})."
        )

    # (교정 L2) 초점·보충 검증 거부도 사유와 함께 감사한다(보충 원문은 싣지 않는다).
    focus, focus_reason = resolve_focus(body.focus, briefing)
    if focus is None:
        await _audit_followup(
            current_user, target, INVESTIGATION_DENIED, authz=decision.as_audit(),
            degraded=[{"reason": "invalid_focus", "detail": str(focus_reason)}],
            parent_investigation_id=investigation_id, focus=body.focus,
        )
        raise _followup_error(400, "invalid_focus", str(focus_reason))
    note, note_reason = sanitize_note(
        body.note,
        int(getattr(ng, "investigation_additional_note_max_chars", 200)),
        _investigation_mask_fn(config),
    )
    if note_reason:
        await _audit_followup(
            current_user, target, INVESTIGATION_DENIED, authz=decision.as_audit(),
            degraded=[{"reason": "invalid_note", "detail": note_reason}],
            parent_investigation_id=investigation_id, focus=focus.id,
        )
        raise _followup_error(400, "invalid_note", note_reason)

    async def _deny(
        status_code: int, code: str, reason: str, outcome: str, **extra: Any
    ) -> HTTPException:
        await _audit_followup(
            current_user, target, outcome, authz=decision.as_audit(),
            degraded=[{"reason": code, "detail": reason}],
            parent_investigation_id=investigation_id, focus=focus.id,
        )
        return _followup_error(status_code, code, reason, **extra)

    # 가용성 — 클라이언트 구성 · 포트 도달성(D-243).
    client = sre_client_mod.build_sre_agent_client(ng)
    if client is None:
        raise await _deny(
            503, "service_unconfigured", "조사 서비스 URL이 설정되지 않았습니다.",
            INVESTIGATION_FAILED,
        )
    unreachable = await client.unreachable_reason()
    if unreachable:
        raise await _deny(
            503, "service_unreachable", f"조사 서비스에 연결할 수 없습니다: {unreachable}",
            INVESTIGATION_FAILED,
        )

    # 같은 (부모, 초점) 재접수 → 기존 id(실패한 것은 새로 받는다 · D-335 ⑥).
    mine = store.list_diagnoses(owner_sub=sub)
    for rec in mine:
        if (
            rec.get("parent_investigation_id") == investigation_id
            and rec.get("focus") == focus.id
            and rec.get("status") not in _FOLLOWUP_RETRYABLE
        ):
            return {
                "investigation_id": rec.get("investigation_id"),
                "status": rec.get("status"),
                "reused": True,
                "hypothesis_notice": HYPOTHESIS_NOTICE,
            }

    # 상한 — 진행 중 1건 · 시간당 N건(plans/187 W11: 알람 다시 분석과 같은 사용자별 카운터).
    # (교정 M1) 상한 검사부터 접수 레코드 기록까지 사용자별 잠금 한 구간이다.
    async with _user_request_slot(store, ng, sub) as limited:
        if limited is not None:
            raise await _deny(
                _limit_status(limited[0]), *limited, INVESTIGATION_DENIED,
                **_limit_extra(store, ng, sub, limited[0]),
            )

        question = render_followup_question(
            focus_id=focus.id,
            focus_label=focus.label,
            target_label=target.label,
            reference_time=target.reference_time,
            lookback_minutes=target.lookback_minutes,
            note=note,
        )
        prior_findings = build_prior_findings(parent, target)
        timeout = float(getattr(ng, "investigation_mcp_call_timeout_seconds", 10.0))

        async def _submit() -> dict[str, Any]:
            await client.connect()
            try:
                return await client.diagnose(
                    question,
                    server_name=target.server_name,
                    hostname=target.hostname,
                    db_id=target.db_id,
                    reference_time=target.reference_time,
                    lookback_minutes=target.lookback_minutes,
                    prior_findings=prior_findings,
                )
            finally:
                await client.disconnect()

        try:
            res = await asyncio.wait_for(_submit(), timeout=timeout)
        except TimeoutError:
            raise await _deny(
                502, "submit_failed", f"조사 서비스 접수 타임아웃({timeout:.0f}s 초과)",
                INVESTIGATION_FAILED,
            )
        except Exception as exc:  # noqa: BLE001 — 접수 실패는 사유와 함께 거부한다(침묵 금지)
            logger.warning("추가 조사 접수 실패: parent=%s", investigation_id, exc_info=True)
            raise await _deny(
                502, "submit_failed", f"조사 서비스 접수 실패: {type(exc).__name__}",
                INVESTIGATION_FAILED,
            )
        new_id = str(res.get("investigation_id") or "")
        if not new_id:
            raise await _deny(
                502, "submit_failed", "조사 서비스 응답에 조사 id가 없습니다.", INVESTIGATION_FAILED
            )
        service_status = str(res.get("status") or "")
        rejected = service_status == "rejected"
        reason = str(res.get("reason") or "") or None
        verdict = str(res.get("verdict") or "") or None
        subject = target.as_subject()
        if note:
            subject["note"] = note
        store.record(
            investigation_id=new_id,
            alarm_ids=[],
            kind=KIND_DIAGNOSIS,
            status="rejected" if rejected else "submitted",
            source=ORIGIN_INVESTIGATION_CHAT,
            reason=reason if rejected else None,
            owner_sub=sub or None,
            parent_investigation_id=investigation_id,
            focus=focus.id,
            origin=ORIGIN_INVESTIGATION_CHAT,
            subject=subject,
        )
    await _audit_followup(
        current_user, target, INVESTIGATION_DENIED if rejected else INVESTIGATION_OK,
        authz=decision.as_audit(),
        degraded=[{"reason": "service_rejected", "detail": reason}] if rejected else None,
        investigation_id=new_id, parent_investigation_id=investigation_id, focus=focus.id,
    )
    out: dict[str, Any] = {
        "investigation_id": new_id,
        "status": "rejected" if rejected else "submitted",
        "reused": False,
        "hypothesis_notice": HYPOTHESIS_NOTICE,
    }
    if rejected and reason:
        out["reason"] = reason
    if rejected:
        # plans/194 W1 · D-347: 거부 사유를 사람이 읽을 한 문장으로(모르는 코드도 코드를 담아 노출 ·
        # 가용성 거부는 조사 서비스 verdict의 확인 시각·판정을 그대로).
        out["reason_text"] = reject_display_text(reason, verdict)
    if rejected and reason == _HOST_IN_FLIGHT_REASON:
        # plans/194 W5 · C3: 같은 서버의 진행 중 조사(열 수 있는 것만)를 알려 준다.
        existing = _existing_host_investigation(
            request, current_user, store, _target_names(target.hostname, target.server_name),
            new_id,
        )
        if existing:
            out["existing_investigation_id"] = existing
    return out


# ─── 「이 알람 분석」 — 사용자 요청 알람 조사 (plans/187 W11 · D-341 ㉑~㉔ · G-28~G-31) ───
#
# 결정 저장소의 알람(등급 무관 · 30일)을 자동 조사와 같은 페이로드(`build_manual_payload` →
# `build_trigger_payload`)로 `sre_investigate_alarm`에 제출하고, 새 레코드 `kind="manual"`로 남긴다.
# 원 조사 레코드는 건드리지 않는다. 이 경로는 알람 그래프(notifier)를 거치지 않으므로 workb 후속
# 통보(D-137 · 3-E)와 승격(CW-C)이 생기지 않는다 — 결과는 조회 라우트(지연 poll · 저장만)로만 본다.
# 기본 off면 접수 라우트가 404이고 by-alarm 응답이 종전과 같다.

#: 감사의 진입점 값.
_MANUAL_ENTRY_POINT = "manual_alarm"
#: 저장 레코드의 `origin`·`source`.
_MANUAL_ORIGIN = "manual"
#: 조사 서비스 지문 dedup이 원 조사 잡을 돌려주지 않게 하는 지문 접두(`episode:` 전례).
_MANUAL_FINGERPRINT_PREFIX = "manual:"
#: 다시 분석할 수 있는 알람의 범위(G-29 — 결정 저장소 보존 30일).
_MANUAL_LOOKBACK_SECONDS = 30 * 86400
#: (plans/196) 「이 서버로 다시 조회」 레코드의 `origin` — 수동 분석 합류 대상에서 뺀다.
_HOST_RERUN_ORIGIN = "host_rerun"


def _manual_enabled(config: Any) -> bool:
    """알람 다시 분석 플래그(기동 시 설정값)."""
    return bool(getattr(config.noise_gate, "investigation_manual_enabled", True))


async def _require_manual_enabled(request: Request) -> None:
    """플래그 off면 미등록 라우트와 같은 404 — 인증·저장소 접근보다 먼저 판정한다."""
    if not _manual_enabled(request.app.state.config):
        raise HTTPException(status_code=404, detail="Not Found")


def _previous_investigations(
    request: Request,
    current_user: dict[str, Any],
    store: InvestigationStore | None,
    alarm_id: str,
    selected_id: str | None,
) -> list[dict[str, Any]]:
    """by-alarm의 `previous` — 고른 조사 외의 이 알람 조사(최신 먼저 · id·kind·상태·접수 시각).

    존 제한 사용자에게는 다른 멤버 알람이 하나라도 범위 밖(또는 존 미상)인 조사를 뺀다(fail-closed).
    """
    if store is None:
        return []
    out: list[dict[str, Any]] = []
    for rec in store.list_by_alarm(alarm_id):
        investigation_id = rec.get("investigation_id")
        if not investigation_id or investigation_id == selected_id:
            continue
        members = [a for a in rec.get("alarm_ids") or [] if a != alarm_id]
        if members:
            try:
                _assert_investigation_zone(
                    request, current_user, members, rec.get("alarm_db_ids")
                )
            except HTTPException:
                continue
        out.append({
            "investigation_id": investigation_id,
            "kind": rec.get("kind") or "alarm",
            "status": rec.get("status") or "",
            "submitted_at": rec.get("submitted_at"),
        })
    return out


def _manual_decision(config: Any, alarm_id: str) -> dict[str, Any] | None:
    """알람의 최신 판정 기록 원본(운영자 뷰 · 마스킹 없음 — 페이로드 재료 · 응답에 싣지 않는다)."""
    from noise_gate.infrastructure.decision_store import DecisionStore

    ng = config.noise_gate
    return DecisionStore(
        ng.decision_store_path,
        bool(getattr(ng, "decision_store_enabled", True)),
        int(getattr(ng, "decision_store_max_lines", 20000)),
    ).get_decision(alarm_id, operator_view=True)


#: 기준 시각 출처 표기 — 원 알람 발생 시각(판정 기록 `alarm_time`) / 판정 시각 대체(구 기록).
_REF_SOURCE_ALARM_TIME = "alarm_time"
_REF_SOURCE_DECISION_TS = "decision_ts"


def _local_naive(value: Any) -> Any:
    """ISO 8601 문자열 → 서버 로컬 naive datetime(폴스타 alarmTime과 같은 기준). 못 읽으면 None."""
    from datetime import datetime as _dt

    try:
        parsed = _dt.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _decision_alarm_time(decision: dict[str, Any]) -> Any:
    """판정 시각(`ts`) → 서버 로컬 naive(보존 범위 30일 판정 · 구 기록의 기준 시각 대체)."""
    return _local_naive(decision.get("ts"))


def _manual_reference_time(decision: dict[str, Any]) -> tuple[Any, str]:
    """조사 기준 시각과 그 출처.

    판정 기록에 원 알람 `alarm_time`(plans/187 W11 — 플래그 on 이후 기록)이 있으면 그것, 없으면
    판정 시각으로 대체하고 출처를 `decision_ts`로 표기한다(침묵 대체 금지).
    """
    alarm_time = _local_naive(decision.get("alarm_time")) if decision.get("alarm_time") else None
    if alarm_time is not None:
        return alarm_time, _REF_SOURCE_ALARM_TIME
    return _decision_alarm_time(decision), _REF_SOURCE_DECISION_TS


async def _manual_event(
    config: Any, alarm_id: str, decision: dict[str, Any], alarm_time: Any
) -> AlarmEvent | None:
    """판정 기록으로 조사용 `AlarmEvent`를 다시 만든다(서버명 없으면 None).

    hostname은 판정 기록의 원 알람 값(plans/187 W11)을 쓰고, 없으면(구 기록) 폴스타 등록 정보로
    해소하며(D-046 `lookup_host`), 그것도 못 하면 서버명을 그대로 쓴다(D-046 원시 값 폴백).
    `resource_type`도 기록에 있으면 싣는다(APM 알람 `meta.hints` 판정).
    """
    from noise_gate.infrastructure.polestar_hostname_resolver import lookup_host

    server_name = str(decision.get("server_name") or "").strip()
    if not server_name:
        return None
    db_id = str(decision.get("db_id") or "")
    hostname = str(decision.get("hostname") or "").strip() or None
    if hostname is None and db_id:
        hostname = (await lookup_host(config, db_id, server_name)).hostname
    raw_signals = decision.get("signals")
    signals: dict[str, Any] = raw_signals if isinstance(raw_signals, dict) else {}
    severity = signals.get("severity")
    return AlarmEvent(
        db_id=db_id,
        server_name=server_name,
        hostname=hostname or server_name,
        ip_address="",
        resource_ancestry="",
        alarm_id=alarm_id,
        severity=int(severity) if isinstance(severity, (int, float)) else 0,
        alarm_status="",
        resource_type=str(decision.get("resource_type") or ""),
        resource_name=str(decision.get("resource_name") or ""),
        alarm_name=str(decision.get("alarm_name") or ""),
        alarm_time=alarm_time,
        conditions="",
        condition_log=str(decision.get("condition_log") or ""),
    )


async def _audit_manual(
    current_user: dict[str, Any],
    alarm_id: str,
    outcome: str,
    *,
    event: AlarmEvent | None = None,
    degraded: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> None:
    """알람 다시 분석 감사 1건(접수·합류·거부·실패 모두 · 실패해도 응답을 막지 않는다)."""
    from src.security import audit_logger

    targets = (
        [{"server_name": event.server_name, "hostname": event.hostname, "db_id": event.db_id}]
        if event is not None else None
    )
    try:
        await audit_logger.log_investigation(
            entry_point=_MANUAL_ENTRY_POINT,
            targets=targets,
            outcome=outcome,
            user_id=current_user.get("sub"),
            backend="sre_agent",
            degraded=degraded,
            alarm_id=alarm_id,
            **extra,
        )
    except Exception:  # noqa: BLE001 — 감사 기록 실패가 응답을 막지 않는다(사유는 로그)
        logger.warning("알람 다시 분석 감사 기록 실패", exc_info=True)


async def _manual_join_target(
    store: InvestigationStore, ng: Any, alarm_id: str
) -> dict[str, Any] | None:
    """합류할 사용자 요청 조사 — 같은 알람에 진행 중이거나 합류 창 안에 접수된 것.

    실패·거부·타임아웃으로 끝난 것에는 합류하지 않는다(다시 요청할 수 있다).
    """
    from noise_gate.infrastructure.investigation_store import ACTIVE_STATUSES, KIND_MANUAL

    window = 60.0 * max(0, int(getattr(ng, "investigation_manual_join_minutes", 10)))
    now = time.time()
    for rec in store.list_by_alarm(alarm_id):
        if rec.get("kind") != KIND_MANUAL or rec.get("origin") == _HOST_RERUN_ORIGIN:
            # (plans/196) 「이 서버로 다시 조회」는 폴스타 축만 다시 돈 조사라 합류 대상이 아니다.
            continue
        current = rec
        if rec.get("status") in ACTIVE_STATUSES:
            current, _ = await _refresh_investigation(store, rec, ng)
            if current.get("status") in ACTIVE_STATUSES:
                return current
        at = _submitted_epoch(rec)
        if (
            at is not None
            and now - at < window
            and current.get("status") not in _FOLLOWUP_RETRYABLE
        ):
            return current
    return None


def _manual_subject(
    event: AlarmEvent, alarm_time: Any, reference_time_source: str
) -> dict[str, Any]:
    """다시 분석 레코드의 `subject` — 기준 시각과 출처 + (plans/194 W5 · C4) 표시 대상.

    목록 「대상」 칸(`subject.label`)에 조사 id 대신 「서버 · 알람명」을 보이도록 판정 기록의
    서버명·hostname·알람명을 함께 싣는다(값 있는 것만 — 빈 값은 `record`가 버리지 않으므로
    여기서 뺀다).
    """
    server = event.server_name or event.hostname
    label = " · ".join(v for v in (server, event.alarm_name) if v)
    raw = {
        "reference_time": alarm_time.isoformat() if alarm_time is not None else None,
        "reference_time_source": reference_time_source,
        "server_name": event.server_name,
        "hostname": event.hostname,
        "alarm_name": event.alarm_name,
        "label": label,
    }
    return {k: v for k, v in raw.items() if k == "reference_time" or v not in (None, "")}


@router.post(
    "/alarm/{alarm_id}/investigations",
    summary="이 알람 분석(사용자 요청 알람 조사 · 비동기)",
    description=(
        "결정 기록(30일) 안의 알람 1건을 자동 조사와 같은 방식으로 다시 조사하도록 접수합니다"
        "(등급 무관). 원 조사는 바뀌지 않고 새 조사(kind=manual)가 생깁니다.<br/>"
        "권한은 그 알람을 볼 수 있는 존 범위 사용자입니다(존 미상은 403). "
        "같은 알람에 진행 중이거나 최근 합류 창 안의 요청이 있으면 새로 띄우지 않고 "
        "그 조사 id를 돌려줍니다(joined=true). "
        "사용자별 진행 중 1건 · 시간당 상한은 추가 조사와 함께 셉니다. 통보는 보내지 않습니다.<br/>"
        "`NOISE_INVESTIGATION_MANUAL_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_manual_enabled)],
)
async def request_manual_investigation(
    alarm_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """알람 다시 분석 접수 → `{investigation_id, status, joined, reference_time_source?, reason?}`.

    `reference_time_source`는 새로 접수했을 때만 싣는다(`alarm_time` | `decision_ts`).
    조사 서비스가 거부했을 때만 `reason_text`(사유 코드의 한국어 문구)를 더 싣는다(plans/194 W1).
    (plans/194 W5 · C3) 같은 서버 진행 중 거부면 열 수 있는 진행 중 조사
    `existing_investigation_id`, 사용자 시간당 상한 429면 `detail.retry_after_seconds`를 더한다.
    """
    from noise_gate.application.nodes.investigation_trigger import build_manual_payload
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod
    from noise_gate.infrastructure.investigation_store import KIND_MANUAL
    from src.security.audit_logger import (
        INVESTIGATION_DENIED,
        INVESTIGATION_FAILED,
        INVESTIGATION_OK,
    )

    config = request.app.state.config
    ng = config.noise_gate
    sub = str(current_user.get("sub") or "")

    # 존 검사(fail-closed) — 판정 기록의 db_id로 본다. 존 제한 사용자는 기록이 없거나 db_id를 모르는
    # 알람도 403이다(존재 여부를 드러내지 않는다 · by-alarm과 같은 순서).
    try:
        scope = _alarm_zone_scope(request, current_user)
        decision = _manual_decision(config, alarm_id)
        db_id = str((decision or {}).get("db_id") or "")
        if scope is not None and (not db_id or not _zone_permits(scope, db_id)):
            raise HTTPException(status_code=403)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        await _audit_manual(
            current_user, alarm_id, INVESTIGATION_DENIED,
            degraded=[{"reason": "zone_denied", "detail": None}],
        )
        raise _followup_error(
            403, "zone_denied", "해당 존의 알람에 대한 권한이 없습니다."
        ) from None
    decided_at = _decision_alarm_time(decision or {})
    epoch = decided_at.timestamp() if decided_at is not None else None
    if decision is None or epoch is None or time.time() - epoch > _MANUAL_LOOKBACK_SECONDS:
        raise _followup_error(
            404, "alarm_not_found",
            "결정 기록 보존 범위(30일) 안에서 이 알람을 찾지 못했습니다.",
        )

    store = _investigation_store(config)
    if store is None:
        raise _followup_error(503, "store_unconfigured", "조사 저장소 경로가 설정되지 않았습니다.")

    # 합류 — 같은 알람의 진행 중·최근 요청(누가 요청했든)은 새로 띄우지 않는다(G-31).
    joined = await _manual_join_target(store, ng, alarm_id)
    if joined is not None:
        await _audit_manual(
            current_user, alarm_id, INVESTIGATION_OK,
            investigation_id=joined.get("investigation_id"), joined=True,
        )
        return {
            "investigation_id": joined.get("investigation_id"),
            "status": joined.get("status"),
            "joined": True,
        }

    alarm_time, reference_time_source = _manual_reference_time(decision)
    event = await _manual_event(config, alarm_id, decision, alarm_time)
    if event is None:
        await _audit_manual(
            current_user, alarm_id, INVESTIGATION_DENIED,
            degraded=[{"reason": "target_unresolved", "detail": None}],
        )
        raise _followup_error(
            400, "target_unresolved", "알람 기록에 서버명이 없어 조사 대상을 정하지 못했습니다."
        )

    async def _deny(
        status_code: int, code: str, reason: str, outcome: str, **extra: Any
    ) -> HTTPException:
        await _audit_manual(
            current_user, alarm_id, outcome, event=event,
            degraded=[{"reason": code, "detail": reason}],
        )
        return _followup_error(status_code, code, reason, **extra)

    # (교정 M1) 상한 검사부터 접수 레코드 기록까지 사용자별 잠금 한 구간이다.
    async with _user_request_slot(store, ng, sub) as limited:
        if limited is not None:
            raise await _deny(
                _limit_status(limited[0]), *limited, INVESTIGATION_DENIED,
                **_limit_extra(store, ng, sub, limited[0]),
            )

        client = sre_client_mod.build_sre_agent_client(ng)
        if client is None:
            raise await _deny(
                503, "service_unconfigured", "조사 서비스 URL이 설정되지 않았습니다.",
                INVESTIGATION_FAILED,
            )
        unreachable = await client.unreachable_reason()
        if unreachable:
            raise await _deny(
                503, "service_unreachable", f"조사 서비스에 연결할 수 없습니다: {unreachable}",
                INVESTIGATION_FAILED,
            )

        previous = store.list_by_alarm(alarm_id)
        parent_investigation_id = str(previous[0].get("investigation_id") or "") if previous else ""
        payload = await build_manual_payload(
            config,
            event,
            SimpleNamespace(
                tier=str(decision.get("tier") or ""),
                reason=str(decision.get("reason") or ""),
                fingerprint=str(decision.get("fingerprint") or ""),
                signals=(
                    decision.get("signals") if isinstance(decision.get("signals"), dict) else {}
                ),
            ),
            fingerprint=f"{_MANUAL_FINGERPRINT_PREFIX}{alarm_id}:{uuid.uuid4().hex[:12]}",
            investigation_kind=KIND_MANUAL,
        )
        timeout = float(getattr(ng, "investigation_mcp_call_timeout_seconds", 10.0))

        async def _submit() -> dict[str, Any]:
            await client.connect()
            try:
                return await client.submit(payload)
            finally:
                await client.disconnect()

        try:
            res = await asyncio.wait_for(_submit(), timeout=timeout)
        except TimeoutError:
            raise await _deny(
                502, "submit_failed", f"조사 서비스 접수 타임아웃({timeout:.0f}s 초과)",
                INVESTIGATION_FAILED,
            )
        except Exception as exc:  # noqa: BLE001 — 접수 실패는 사유와 함께 거부한다(침묵 금지)
            logger.warning("알람 다시 분석 접수 실패: alarm=%s", alarm_id, exc_info=True)
            raise await _deny(
                502, "submit_failed", f"조사 서비스 접수 실패: {type(exc).__name__}",
                INVESTIGATION_FAILED,
            )
        new_id = str(res.get("investigation_id") or "")
        if not new_id:
            raise await _deny(
                502, "submit_failed", "조사 서비스 응답에 조사 id가 없습니다.", INVESTIGATION_FAILED
            )
        existing = store.get_by_investigation(new_id)
        if existing is not None and existing.get("kind") != KIND_MANUAL:
            # 원 조사 레코드에 새 행을 섞지 않는다(원 조사 불변).
            # 지문이 달라 정상 경로에서는 생기지 않는다.
            raise await _deny(
                502, "submit_conflict", "조사 서비스가 기존 조사 id를 돌려주었습니다.",
                INVESTIGATION_FAILED,
            )
        rejected = str(res.get("status") or "") == "rejected"
        reason = str(res.get("reason") or "") or None
        verdict = str(res.get("verdict") or "") or None
        store.record(
            investigation_id=new_id,
            alarm_ids=[alarm_id],
            kind=KIND_MANUAL,
            status="rejected" if rejected else "submitted",
            source=_MANUAL_ORIGIN,
            reason=reason if rejected else None,
            alarm_db_ids={alarm_id: event.db_id} if event.db_id else None,
            owner_sub=sub or None,
            parent_investigation_id=parent_investigation_id or None,
            origin=_MANUAL_ORIGIN,
            parent_alarm_id=alarm_id,
            subject=_manual_subject(event, alarm_time, reference_time_source),
        )
    await _audit_manual(
        current_user, alarm_id, INVESTIGATION_DENIED if rejected else INVESTIGATION_OK,
        event=event,
        degraded=[{"reason": "service_rejected", "detail": reason}] if rejected else None,
        investigation_id=new_id, joined=False, reference_time_source=reference_time_source,
    )
    out: dict[str, Any] = {
        "investigation_id": new_id,
        "status": "rejected" if rejected else "submitted",
        "joined": False,
        "reference_time_source": reference_time_source,
    }
    if rejected and reason:
        out["reason"] = reason
    if rejected:
        # plans/194 W1 · D-347: 거부 사유를 사람이 읽을 한 문장으로(모르는 코드도 코드를 담아 노출 ·
        # 가용성 거부는 조사 서비스 verdict의 확인 시각·판정을 그대로).
        out["reason_text"] = reject_display_text(reason, verdict)
    if rejected and reason == _HOST_IN_FLIGHT_REASON:
        # plans/194 W5 · C3: 같은 서버의 진행 중 조사(열 수 있는 것만)를 알려 준다.
        existing_id = _existing_host_investigation(
            request, current_user, store, _target_names(event.hostname, event.server_name), new_id
        )
        if existing_id:
            out["existing_investigation_id"] = existing_id
    return out


# ─── 호스트 연결 학습 — 유사 호스트 후보 · 사용자 선택 · 재조회 (plans/196 §1.1 · §5.5) ───
#
# 제니퍼 알람 조사의 hostname으로 인가 존의 폴스타 소스에서 유사 서버 후보를 찾고
# (LLM 0 · 캐시 없음), 사용자가 고른 서버(또는 「해당 서버 없음」)를 연결 저장소
# (Redis `hostlink:*`)에 남긴다. 운영자 선택은 바로 확정(전역), 일반 사용자 선택은 확정 대기
# (그 조사의 재조회에만 적용 · Q5). 「이 서버로 다시 조회」는 폴스타 축만 다시 도는 새 조사
# (kind=manual · origin=host_rerun)를 접수한다 — 같은 알람이어도 합류하지 않는다.
# 플래그(`NOISE_HOST_LINK_ENABLED`) off면 두 라우트가 404다.

#: 감사 진입점 값.
_HOST_LINK_ENTRY_POINT = "host_link"
#: 연결 후보를 찾는 관측 시스템(레지스트리 시스템 키).
_HOST_LINK_SYSTEM = "polestar"


def _host_link_enabled(config: Any) -> bool:
    """호스트 연결 학습 플래그(기동 시 설정값)."""
    return bool(getattr(config.noise_gate, "host_link_enabled", False))


async def _require_host_link_enabled(request: Request) -> None:
    """플래그 off면 미등록 라우트와 같은 404 — 인증·저장소 접근보다 먼저 판정한다."""
    if not _host_link_enabled(request.app.state.config):
        raise HTTPException(status_code=404, detail="Not Found")


def _host_link_store(request: Request) -> Any:
    """연결 저장소(앱 수명 1개 — 첫 요청에 Redis 연결을 만든다 · 실패면 None)."""
    from noise_gate.infrastructure.host_link_store import build_host_link_store

    state = request.app.state
    store = getattr(state, "host_link_store", None)
    if store is None:
        store = build_host_link_store(state.config)
        state.host_link_store = store
    return store


def _host_link_db_ids(request: Request, current_user: dict[str, Any]) -> list[str]:
    """후보를 찾을 폴스타 소스 — 활성 DB ∩ 사용자 인가 존.

    순서는 `authorized_zones`(은행존 → 공동존)이고 존 미배정 DB는 뒤에 붙는다(전 존 사용자만).
    """
    from src.orchestration.entity_locator import probe_scope
    from src.routing.registry import get_registry

    config = request.app.state.config
    scope = _alarm_zone_scope(request, current_user)
    active = list(config.multi_db.get_active_db_ids())
    allowed = (
        None if scope is None
        else [d for d in active if db_id_to_zone(d) and _zone_permits(scope, d)]
    )
    return probe_scope(
        _HOST_LINK_SYSTEM, get_registry(),
        active_db_ids=active, targeted_db_ids=[], allowed_db_ids=allowed,
    )


def _host_link_subject(
    request: Request, current_user: dict[str, Any], investigation_id: str
) -> tuple[InvestigationStore, dict[str, Any], str, dict[str, Any]]:
    """부모 조사(존 검사 끝) · 알람 id · 판정 기록 — 제니퍼 알람만.

    Raises:
        HTTPException: `_followup_parent`의 거부 · 판정 기록 없음 404 · 제니퍼 알람 아님 400 ·
            hostname 없음 400.
    """
    from noise_gate.domain.alarm import is_apm_db_id

    store, parent = _followup_parent(request, current_user, investigation_id)
    alarm_id = next(iter(parent.get("alarm_ids") or []), None) or parent.get("parent_alarm_id")
    decision = _manual_decision(request.app.state.config, str(alarm_id)) if alarm_id else None
    if not alarm_id or decision is None:
        raise _followup_error(404, "alarm_not_found", "이 조사의 알람 판정 기록을 찾지 못했습니다.")
    if not is_apm_db_id(str(decision.get("db_id") or "")):
        raise _followup_error(
            400, "not_apm_alarm", "제니퍼 알람 조사에서만 폴스타 서버를 연결할 수 있습니다."
        )
    if not str(decision.get("hostname") or decision.get("server_name") or "").strip():
        raise _followup_error(400, "target_unresolved", "알람 기록에 hostname이 없습니다.")
    return store, parent, str(alarm_id), decision


def _decision_hostname(decision: dict[str, Any]) -> str:
    return str(decision.get("hostname") or decision.get("server_name") or "").strip()


async def _audit_host_link(
    current_user: dict[str, Any], alarm_id: str, outcome: str, **extra: Any
) -> None:
    """호스트 연결 감사 1건(선택·없음·승인·삭제 · 실패해도 응답을 막지 않는다)."""
    from src.security import audit_logger

    try:
        await audit_logger.log_investigation(
            entry_point=_HOST_LINK_ENTRY_POINT,
            outcome=outcome,
            user_id=current_user.get("sub"),
            backend="noise_gate",
            alarm_id=alarm_id or None,
            **extra,
        )
    except Exception:  # noqa: BLE001 — 감사 기록 실패가 응답을 막지 않는다(사유는 로그)
        logger.warning("호스트 연결 감사 기록 실패", exc_info=True)


#: 후보·연결 라우트의 사용자별 짧은 창 호출 상한 — 소스마다 SELECT를 도는 프로브 남용 방지.
_HOST_LINK_RATE_WINDOW_SECONDS = 60.0
_HOST_LINK_RATE_LIMITS: dict[str, int] = {"candidates": 10, "link": 5}


async def _host_link_rate_retry(request: Request, route: str, sub: str) -> int | None:
    """창 안 호출이 상한이면 다시 시도까지 남은 초, 아니면 None(이번 호출을 센다).

    연결 저장소의 Redis 공유 카운터(고정 창 · `HostLinkStore.rate_hit`)로 세어 uvicorn
    프로세스끼리 상한을 공유한다. 저장소가 없거나 Redis 오류면 in-process 카운터로 센다
    (상한을 열어 두지 않는다 · 사유는 warning).
    """
    store = _host_link_store(request)
    if store is not None:
        try:
            retry: int | None = await store.rate_hit(
                route, sub, limit=_HOST_LINK_RATE_LIMITS[route],
                window_seconds=int(_HOST_LINK_RATE_WINDOW_SECONDS),
            )
            return retry
        except Exception:  # noqa: BLE001 — Redis 오류는 프로세스 내 카운터로 폴백(열지 않는다)
            logger.warning(
                "호스트 연결 호출 상한 Redis 카운터 실패 — 프로세스 내로 셈", exc_info=True
            )
    return _host_link_rate_retry_local(request, route, sub)


def _host_link_rate_retry_local(request: Request, route: str, sub: str) -> int | None:
    """in-process 폴백(`app.state.host_link_rate` · 미끄럼 창) — 프로세스마다 따로 센다.

    창이 지난 기록은 매 호출 정리해 빈 키를 남기지 않는다.
    """
    from collections import deque

    now = time.monotonic()
    window = _HOST_LINK_RATE_WINDOW_SECONDS
    buckets: dict[tuple[str, str], Any] | None = getattr(request.app.state, "host_link_rate", None)
    if buckets is None:
        buckets = {}
        request.app.state.host_link_rate = buckets
    for key in list(buckets):
        stamps = buckets[key]
        while stamps and now - stamps[0] >= window:
            stamps.popleft()
        if not stamps:
            del buckets[key]
    stamps = buckets.setdefault((route, sub), deque())
    if len(stamps) >= _HOST_LINK_RATE_LIMITS[route]:
        return max(1, int(window - (now - stamps[0])) + 1)
    stamps.append(now)
    return None


def _host_link_rate_error(retry_after: int) -> HTTPException:
    return _followup_error(
        429, "host_link_rate_limited",
        f"요청이 너무 잦습니다. {retry_after}초 뒤에 다시 시도하세요.",
        retry_after_seconds=retry_after,
    )


def _host_link_view(link: dict[str, Any] | None, operator: bool) -> dict[str, Any] | None:
    """응답의 연결 레코드 — 누가·언제·어느 조사에서 골랐는지는 운영자에게만 싣는다."""
    from noise_gate.domain.host_link import public_link_view

    return link if operator else public_link_view(link)


class HostLinkRequest(BaseModel):
    """호스트 연결 요청 본문 — `select`(이 서버로 다시 조회) · `none`(해당 서버 없음)."""

    action: Literal["select", "none"]
    db_id: str | None = Field(
        None, max_length=64,
        description="select — 폴스타 소스 db_id(없으면 인가 소스 전부에서 확인)",
    )
    server_name: str | None = Field(None, max_length=255, description="select — 등록 서버명")
    hostname: str | None = Field(None, max_length=255, description="select — 등록 hostname")


@router.get(
    "/alarm/investigations/{investigation_id}/host-candidates",
    summary="제니퍼 알람 조사의 유사 폴스타 서버 후보",
    description=(
        "조사 대상 제니퍼 hostname과 비슷한 폴스타 등록 서버를 인가된 존의 소스에서 "
        "찾아 상위 5건과 등급(확정·일치·유력·유사)·사유를 돌려줍니다. "
        "확인하지 못한 소스는 `unchecked`에 따로 싣습니다. "
        "캐시하지 않습니다. 조사 열람과 같은 존 검사를 합니다. 사용자별 짧은 창 호출 상한이 "
        "있습니다(429 `host_link_rate_limited`).<br/>"
        "`NOISE_HOST_LINK_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_host_link_enabled)],
)
async def get_host_candidates(
    investigation_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """`{hostname, source_id, link, candidates[≤5], unchecked[], link_error?}`."""
    from noise_gate.application.host_candidates import find_host_candidates
    from noise_gate.domain.host_link import apm_source_id

    _, _, _, decision = _host_link_subject(request, current_user, investigation_id)
    retry_after = await _host_link_rate_retry(
        request, "candidates", str(current_user.get("sub") or "")
    )
    if retry_after is not None:
        raise _host_link_rate_error(retry_after)
    hostname = _decision_hostname(decision)
    source_id = apm_source_id(str(decision.get("db_id") or ""))
    store = _host_link_store(request)
    result = await find_host_candidates(
        request.app.state.config,
        hostname=hostname,
        source_id=source_id,
        db_ids=_host_link_db_ids(request, current_user),
        store=store,
        reveal_actor=_is_alarm_operator(request.app.state.config, current_user),
    )
    if store is None:
        result["link_error"] = "연결 저장소(Redis)를 쓸 수 없습니다."
    return result


@router.post(
    "/alarm/investigations/{investigation_id}/host-link",
    summary="폴스타 서버 연결 — 이 서버로 다시 조회 / 해당 서버 없음",
    description=(
        "`select`는 고른 서버가 그 소스에 있는지 다시 확인한 뒤 연결을 저장하고, "
        "폴스타 축 단계만 다시 도는 조사를 접수합니다. `db_id` 없이 서버명만 보내면(직접 입력) "
        "인가된 소스 전부에서 정확히 한 곳에 있을 때만 받습니다(여러 곳이면 409 "
        "`server_ambiguous`)"
        "(같은 알람이어도 합류하지 않음 · 사용자별 상한은 수동 조사와 함께 셈). "
        "`none`은 부정 연결(30일)을 저장합니다. 운영자 선택은 바로 확정, 그 밖은 확정 대기입니다. "
        "사용자별 짧은 창 호출 상한이 있습니다(429 `host_link_rate_limited`). "
        "감사 로그 대상입니다.<br/>`NOISE_HOST_LINK_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_host_link_enabled)],
)
async def post_host_link(
    investigation_id: str,
    body: HostLinkRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """`{link, stored, investigation_id, status[, reason, reason_text]}`.

    `stored=false`면 확정 대기 선택이 기존 확정 연결을 덮지 않았다는 뜻이다
    (재조회에는 선택이 쓰인다).
    `none`이면 `investigation_id`는 null, `status`는 `recorded`다.
    `link`는 운영자가 아니면 `public_link_view`로 줄인다(누가·언제·어느 조사는 운영자에게만).
    """
    from noise_gate.application.host_candidates import (
        locate_selected_server,
        verify_selected_server,
    )
    from noise_gate.application.nodes.investigation_trigger import build_manual_payload
    from noise_gate.domain.host_link import (
        KIND_ABSENT,
        KIND_LINK,
        apm_source_id,
        selection_grade,
        user_selected_hint,
        valid_link_key,
        valid_server_text,
    )
    from noise_gate.domain.process_rank import APM_RESOURCE_TYPE
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod
    from noise_gate.infrastructure.investigation_store import KIND_MANUAL
    from src.security.audit_logger import (
        INVESTIGATION_DENIED,
        INVESTIGATION_FAILED,
        INVESTIGATION_OK,
    )

    config = request.app.state.config
    ng = config.noise_gate
    sub = str(current_user.get("sub") or "")
    audit_base: dict[str, Any] = {"action": body.action, "investigation_id": investigation_id}
    alarm_id = ""

    async def _deny(status_code: int, code: str, reason: str, outcome: str) -> HTTPException:
        await _audit_host_link(
            current_user, alarm_id, outcome, degraded=[{"reason": code, "detail": reason}],
            **audit_base,
        )
        return _followup_error(status_code, code, reason)

    try:
        inv_store, parent, alarm_id, decision = _host_link_subject(
            request, current_user, investigation_id
        )
    except HTTPException as exc:
        # 초기 거부(존 403 · 제니퍼 알람 아님 · 대상 없음 · 저장소 503)도 감사한다.
        detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
        await _audit_host_link(
            current_user, alarm_id, INVESTIGATION_DENIED,
            degraded=[{"reason": detail.get("code") or str(exc.status_code),
                       "detail": detail.get("reason") or str(exc.detail)}],
            **audit_base,
        )
        raise
    hostname = _decision_hostname(decision)
    db_id_alarm = str(decision.get("db_id") or "")
    source_id = apm_source_id(db_id_alarm)
    audit_base["targets"] = [{"hostname": hostname, "db_id": db_id_alarm}]
    retry_after = await _host_link_rate_retry(request, "link", sub)
    if retry_after is not None:
        await _audit_host_link(
            current_user, alarm_id, INVESTIGATION_DENIED,
            degraded=[{"reason": "host_link_rate_limited", "detail": f"{retry_after}s"}],
            **audit_base,
        )
        raise _host_link_rate_error(retry_after)
    if not valid_link_key(source_id, hostname):
        raise await _deny(
            400, "invalid_link_key", "이 알람의 hostname·소스로는 연결을 저장할 수 없습니다.",
            INVESTIGATION_DENIED,
        )
    operator = _is_alarm_operator(config, current_user)
    by_role = "operator" if operator else "user"
    link_store = _host_link_store(request)
    if link_store is None:
        raise await _deny(
            503, "link_store_unavailable", "연결 저장소(Redis)를 쓸 수 없습니다.",
            INVESTIGATION_FAILED,
        )

    if body.action == "none":
        try:
            rec, stored = await link_store.save(
                source_id, hostname, kind=KIND_ABSENT, confirmed=operator,
                by=sub, by_role=by_role, investigation_id=investigation_id,
            )
        except Exception:  # noqa: BLE001 — 저장 실패는 사유와 함께 거부한다(침묵 금지)
            logger.warning("부정 연결 저장 실패: host=%s", hostname, exc_info=True)
            raise await _deny(
                503, "link_store_failed", "연결 저장에 실패했습니다.", INVESTIGATION_FAILED
            )
        await _audit_host_link(
            current_user, alarm_id, INVESTIGATION_OK, link_state=rec.get("state"),
            stored=stored, **audit_base,
        )
        return {
            "link": _host_link_view(rec, operator), "stored": stored,
            "investigation_id": None, "status": "recorded",
        }

    # ── select ── db_id 없이 서버명만 오면(후보 0건 직접 입력) 인가 소스 전부에서 확인한다.
    db_id = (body.db_id or "").strip()
    server_name = (body.server_name or "").strip()
    picked_host = (body.hostname or "").strip()
    if not (server_name or picked_host):
        raise await _deny(
            400, "invalid_selection", "서버명 또는 hostname이 필요합니다.", INVESTIGATION_DENIED
        )
    if not all(valid_server_text(v) for v in (server_name, picked_host) if v):
        raise await _deny(
            400, "invalid_selection",
            "서버명·hostname에는 영문·숫자와 . _ - 만 쓸 수 있습니다.", INVESTIGATION_DENIED,
        )
    allowed_db_ids = _host_link_db_ids(request, current_user)
    if db_id and db_id not in allowed_db_ids:
        raise await _deny(
            403, "zone_denied", "해당 소스에 대한 권한이 없습니다.", INVESTIGATION_DENIED
        )
    briefing_pb = (parent.get("briefing") or {}).get("playbook") or {}
    sections = briefing_pb.get("sections")
    if not isinstance(sections, list) or not sections:
        raise await _deny(
            409, "parent_no_sections", "부모 조사의 단계 결과가 아직 없어 다시 조회할 수 없습니다.",
            INVESTIGATION_DENIED,
        )
    unchecked: list[dict[str, str]] = []
    try:
        if db_id:
            row = await verify_selected_server(
                config, db_id=db_id, server_name=server_name or None,
                hostname=picked_host or None,
            )
        else:
            located = await locate_selected_server(
                config, db_ids=allowed_db_ids, server_name=server_name or None,
                hostname=picked_host or None,
            )
            unchecked = located["unchecked"]
            row = {"ambiguous": True} if located["ambiguous"] else located["row"]
            db_id = located["db_id"] or ""
    except Exception as exc:  # noqa: BLE001 — 확인 실패는 「확인하지 못함」으로 거부
        logger.warning("선택 서버 확인 실패: db_id=%s", db_id, exc_info=True)
        raise await _deny(
            502, "verify_failed", f"서버 존재를 확인하지 못했습니다({type(exc).__name__}).",
            INVESTIGATION_FAILED,
        )
    if row is None and unchecked:
        raise await _deny(
            502, "verify_failed",
            "일부 소스를 확인하지 못해 서버가 없다고 단정할 수 없습니다"
            f"({', '.join(u['db_id'] for u in unchecked)}).",
            INVESTIGATION_FAILED,
        )
    if row is None:
        raise await _deny(
            400, "server_not_found", "인가된 소스에서 해당 서버를 찾지 못했습니다.",
            INVESTIGATION_DENIED,
        )
    if row.get("ambiguous"):
        raise await _deny(
            409, "server_ambiguous",
            "같은 이름의 서버가 여러 대(또는 여러 소스)에 있어 하나로 정하지 못했습니다. "
            "후보 목록에서 소스를 골라 주세요.",
            INVESTIGATION_DENIED,
        )
    try:
        patterns = await link_store.patterns()
    except Exception:  # noqa: BLE001 — 패턴은 등급 사유일 뿐이다
        patterns = {}
    grade = selection_grade(hostname, db_id, {**row, "name": row["server_name"]}, patterns=patterns)
    audit_base.update({"link_db_id": db_id, "link_server_name": row["server_name"]})

    alarm_time, reference_time_source = _manual_reference_time(decision)
    event = await _manual_event(config, alarm_id, decision, alarm_time)
    if event is None:
        raise await _deny(
            400, "target_unresolved", "알람 기록에 서버명이 없어 조사 대상을 정하지 못했습니다.",
            INVESTIGATION_DENIED,
        )
    if not event.resource_type:
        # 구 판정 기록(resource_type 없음)도 제니퍼 알람이면 APM 힌트가 실리게 한다.
        event.resource_type = APM_RESOURCE_TYPE

    async with _user_request_slot(inv_store, ng, sub) as limited:
        if limited is not None:
            await _audit_host_link(
                current_user, alarm_id, INVESTIGATION_DENIED,
                degraded=[{"reason": limited[0], "detail": limited[1]}], **audit_base,
            )
            raise _followup_error(
                _limit_status(limited[0]), *limited, **_limit_extra(inv_store, ng, sub, limited[0])
            )
        client = sre_client_mod.build_sre_agent_client(ng)
        if client is None:
            raise await _deny(
                503, "service_unconfigured", "조사 서비스 URL이 설정되지 않았습니다.",
                INVESTIGATION_FAILED,
            )
        unreachable = await client.unreachable_reason()
        if unreachable:
            raise await _deny(
                503, "service_unreachable", f"조사 서비스에 연결할 수 없습니다: {unreachable}",
                INVESTIGATION_FAILED,
            )
        # 연결 저장은 상한·조사 서비스 검사를 지난 뒤에만 한다(거부된 요청이 연결을 남기지 않게).
        try:
            rec, stored = await link_store.save(
                source_id, hostname, kind=KIND_LINK, confirmed=operator, by=sub,
                by_role=by_role, investigation_id=investigation_id, db_id=db_id,
                server_name=row["server_name"], polestar_hostname=row["hostname"], grade=grade,
            )
        except Exception:  # noqa: BLE001
            logger.warning("서버 연결 저장 실패: host=%s", hostname, exc_info=True)
            raise await _deny(
                503, "link_store_failed", "연결 저장에 실패했습니다.", INVESTIGATION_FAILED
            )
        audit_base.update({"link_state": rec.get("state"), "stored": stored})
        rerun: dict[str, Any] = {
            "parent_investigation_id": investigation_id,
            "axes": ["polestar"],
            "prior_sections": sections,
        }
        prior_readings = briefing_pb.get("readings")
        if prior_readings is not None:
            rerun["prior_readings"] = prior_readings
        payload = await build_manual_payload(
            config,
            event,
            SimpleNamespace(
                tier=str(decision.get("tier") or ""),
                reason=str(decision.get("reason") or ""),
                fingerprint=str(decision.get("fingerprint") or ""),
                signals=(
                    decision.get("signals") if isinstance(decision.get("signals"), dict) else {}
                ),
            ),
            fingerprint=f"{_MANUAL_FINGERPRINT_PREFIX}{alarm_id}:{uuid.uuid4().hex[:12]}",
            investigation_kind=KIND_MANUAL,
            polestar_link=user_selected_hint(
                db_id=db_id, server_name=row["server_name"], polestar_hostname=row["hostname"],
                by=sub, at=str(rec.get("at") or ""),
            ),
            rerun=rerun,
        )
        timeout = float(getattr(ng, "investigation_mcp_call_timeout_seconds", 10.0))

        async def _submit() -> dict[str, Any]:
            await client.connect()
            try:
                return await client.submit(payload)
            finally:
                await client.disconnect()

        try:
            res = await asyncio.wait_for(_submit(), timeout=timeout)
        except TimeoutError:
            raise await _deny(
                502, "submit_failed", f"조사 서비스 접수 타임아웃({timeout:.0f}s 초과)",
                INVESTIGATION_FAILED,
            )
        except Exception as exc:  # noqa: BLE001 — 접수 실패는 사유와 함께 거부한다(침묵 금지)
            logger.warning("서버 연결 재조회 접수 실패: alarm=%s", alarm_id, exc_info=True)
            raise await _deny(
                502, "submit_failed", f"조사 서비스 접수 실패: {type(exc).__name__}",
                INVESTIGATION_FAILED,
            )
        new_id = str(res.get("investigation_id") or "")
        if not new_id:
            raise await _deny(
                502, "submit_failed", "조사 서비스 응답에 조사 id가 없습니다.", INVESTIGATION_FAILED
            )
        existing = inv_store.get_by_investigation(new_id)
        if existing is not None and existing.get("kind") != KIND_MANUAL:
            raise await _deny(
                502, "submit_conflict", "조사 서비스가 기존 조사 id를 돌려주었습니다.",
                INVESTIGATION_FAILED,
            )
        rejected = str(res.get("status") or "") == "rejected"
        reason = str(res.get("reason") or "") or None
        verdict = str(res.get("verdict") or "") or None
        inv_store.record(
            investigation_id=new_id,
            alarm_ids=[alarm_id],
            kind=KIND_MANUAL,
            status="rejected" if rejected else "submitted",
            source=_MANUAL_ORIGIN,
            reason=reason if rejected else None,
            alarm_db_ids={alarm_id: event.db_id} if event.db_id else None,
            owner_sub=sub or None,
            parent_investigation_id=investigation_id,
            origin=_HOST_RERUN_ORIGIN,
            parent_alarm_id=alarm_id,
            subject=_manual_subject(event, alarm_time, reference_time_source),
        )
    await _audit_host_link(
        current_user, alarm_id, INVESTIGATION_DENIED if rejected else INVESTIGATION_OK,
        degraded=[{"reason": "service_rejected", "detail": reason}] if rejected else None,
        rerun_investigation_id=new_id, **audit_base,
    )
    out: dict[str, Any] = {
        "link": _host_link_view(rec, operator),
        "stored": stored,
        "investigation_id": new_id,
        "status": "rejected" if rejected else "submitted",
    }
    if rejected:
        if reason:
            out["reason"] = reason
        out["reason_text"] = reject_display_text(reason, verdict)
    return out


# ─── 장애 대응 지식(KB) 쓰기·참조 측 (plans/187 W9 · D-341 ⑩⑮⑳ · G-14~G-16 · G-27) ───
#
# KB 초안(운영자만 · state=wip 고정 · LLM 0) · 포스트모템 내보내기(작성 기준 충족 건만) ·
# 알려진 오류 사용 피드백 · 엔진 선택 기록. 기록은 본체 별도 JSONL(`IncidentKbStore`)에만 하고
# sre_agent 로더 경로에는 쓰지 않는다. 선택·피드백은 판정·티어·통보를 바꾸지 않는다
# (조사·결정 저장소에 쓰지 않는다). 기본 off면 네 라우트가 404(미등록과 같은 응답 — 인증보다
# 먼저)이고 조회 응답에 아무것도 붙지 않는다.
# 관리자 화면 API는 `admin_incident_kb.py`(조건부 등록). 순수 로직은 `src/domain/kb_draft.py`.

#: 감사 진입점 값(`log_investigation(entry_point=…)`).
_KB_ENTRY_POINT = "incident_kb"
#: 매뉴얼 RAG 참고 인용 컬렉션(`config/rag_collections.yaml`).
_KB_REFERENCE_COLLECTION = "incident_docs"


def _kb_enabled(config: Any) -> bool:
    """KB 쓰기·참조 측 플래그(기동 시 설정값)."""
    return bool(getattr(config.noise_gate, "investigation_kb_enabled", False))


async def _require_kb_enabled(request: Request) -> None:
    """플래그 off면 미등록 라우트와 같은 404 — 인증·저장소 접근보다 먼저 판정한다."""
    if not _kb_enabled(request.app.state.config):
        raise HTTPException(status_code=404, detail="Not Found")


def _kb_store(config: Any) -> Any:
    """KB 기록 저장소 — 경로 미설정·sre_agent 아래 경로면 503(쓰기 차단 사유 노출)."""
    from noise_gate.infrastructure.incident_kb_store import ForbiddenKbPathError, IncidentKbStore

    ng = config.noise_gate
    path = str(ng.investigation_kb_store_path or "")
    if not path:
        raise _followup_error(503, "kb_store_unconfigured", "KB 기록 경로가 설정되지 않았습니다.")
    try:
        return IncidentKbStore(path, int(getattr(ng, "investigation_kb_store_max_lines", 20000)))
    except ForbiddenKbPathError as exc:
        logger.error("KB 기록 경로 거부: %s", exc)
        raise _followup_error(
            503, "kb_store_forbidden", "KB 기록 경로가 sre_agent 아래라 쓰지 않습니다."
        ) from exc


def _kb_scan_records(config: Any) -> list[dict[str, Any]]:
    """KB 집계 재료 — 조사 저장소 끝 `investigation_kb_scan_max_lines`줄의 조사별 최신 행."""
    from noise_gate.infrastructure.investigation_store import InvestigationStore

    ng = config.noise_gate
    path = ng.investigation_store_path
    if not path:
        return []
    return InvestigationStore(
        path, int(getattr(ng, "investigation_kb_scan_max_lines", 10000))
    ).latest_records()


async def _audit_kb(
    current_user: dict[str, Any], outcome: str, action: str, **extra: Any
) -> None:
    """KB 감사 1건(쓰기·내보내기·거부 · 실패해도 응답을 막지 않는다)."""
    from src.security import audit_logger

    try:
        await audit_logger.log_investigation(
            entry_point=_KB_ENTRY_POINT,
            outcome=outcome,
            user_id=current_user.get("sub"),
            backend="incident_kb",
            action=action,
            **extra,
        )
    except Exception:  # noqa: BLE001 — 감사 기록 실패가 응답을 막지 않는다(사유는 로그)
        logger.warning("KB 감사 기록 실패", exc_info=True)


def _kb_investigation(
    request: Request, current_user: dict[str, Any], investigation_id: str
) -> dict[str, Any]:
    """KB 대상 조사 — 존 검사(조사에 걸린 알람 전부 · fail-closed)까지 끝낸 레코드.

    Raises:
        HTTPException: 저장소 미설정 503 · 없음 404 · 존 권한 없음 403.
    """
    _alarm_zone_scope(request, current_user)
    store = _investigation_store(request.app.state.config)
    if store is None:
        raise _followup_error(503, "store_unconfigured", "조사 저장소 경로가 설정되지 않았습니다.")
    rec = store.get_by_investigation(investigation_id)
    if rec is None:
        raise _followup_error(404, "investigation_not_found", "조사를 찾지 못했습니다.")
    # (교정 H1) 알람 없는 사용자 요청 조사는 소유자 또는 운영자만.
    _assert_unbound_request_owner(request.app.state.config, current_user, rec)
    _assert_investigation_zone(
        request, current_user, list(rec.get("alarm_ids") or []), rec.get("alarm_db_ids")
    )
    return rec


def _kb_playbook_briefing(rec: dict[str, Any], *, require_done: bool) -> dict[str, Any]:
    """플레이북 브리핑 — 종결 전 409 · 플레이북 블록 없음 400."""
    from src.domain.kb_draft import playbook_of

    if require_done and rec.get("status") != "done":
        raise _followup_error(
            409, "not_done", "조사가 끝나지 않았습니다 — 완료된 조사만 가능합니다."
        )
    briefing = rec.get("briefing")
    if not isinstance(briefing, dict) or playbook_of(briefing) is None:
        raise _followup_error(400, "no_playbook", "플레이북 조사 브리핑이 아닙니다.")
    return briefing


def _kb_alarm(config: Any, rec: dict[str, Any]) -> dict[str, Any]:
    """조사 대표 알람(첫 알람)의 판정 기록 — 알람명·조건 원문·티어(없으면 알람 id만)."""
    alarm_ids = list(rec.get("alarm_ids") or [])
    if not alarm_ids:
        return {}
    return {**(_manual_decision(config, alarm_ids[0]) or {}), "alarm_id": alarm_ids[0]}


async def _kb_references(
    config: Any, current_user: dict[str, Any], briefing: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """매뉴얼 RAG 참고 인용(§5.7.4 · G-15) — `(references, {status, reason})`.

    질의는 결정적(플레이북 id + 런북 references + 대상 솔루션명)이고 LLM을 부르지 않는다. 문서 RAG의
    검색 함수(`retrieve_many`)·정렬(`sort_hits`)·인가(`allowed_collection_ids`)를 재사용한다 —
    `answer_from_documents`는 발췌를 돌려주지 않아 쓰지 않는다. 실패는 빈 검색 결과 + 사유이며
    런북 references(고정 문서)는 그래도 싣는다. 결과는 응답에만 붙고 sre_agent에는 가지 않는다.
    """
    from src.domain.kb_draft import merge_references, reference_query, runbook_references

    pinned = runbook_references(briefing)
    query = reference_query(briefing)
    rag = getattr(config, "rag", None)
    hits: list[dict[str, Any]] = []
    if rag is None or not getattr(rag, "enabled", False):
        return merge_references(pinned, hits), {
            "status": "disabled", "reason": "문서 검색이 꺼져 있습니다(RAG_ENABLED=false)."
        }
    status: dict[str, Any] = {"status": "ok", "reason": ""}
    started = time.monotonic()
    try:
        from src.clients.fabrix_retrieval import (
            STATUS_EMPTY,
            retrieve_many,
            timeouts_from_config,
        )
        from src.doc_qa.authz import allowed_collection_ids
        from src.doc_qa.evidence import sort_hits
        from src.infrastructure.doc_sources import resolve_collections

        cols = [c for c in resolve_collections(rag) if c.id == _KB_REFERENCE_COLLECTION]
        allowed = set(allowed_collection_ids(cols, user=current_user, rag_config=rag))
        usable = [c for c in cols if c.id in allowed and c.usable]
        if not usable:
            reason = (cols[0].disabled_reason or "인가되지 않은 문서군") if cols else (
                f"문서군 {_KB_REFERENCE_COLLECTION} 정본 없음"
            )
            return merge_references(pinned, hits), {"status": "disabled", "reason": reason}
        read_to, total_to = timeouts_from_config(rag)
        outcomes = await retrieve_many(
            usable, query, timeout_sec=read_to, total_timeout_sec=total_to
        )
        base = str(getattr(rag, "doc_url_base", "") or "")
        for hit in sort_hits([h for o in outcomes for h in o.hits]):
            href = f"{base.rstrip('/')}/{hit.url.lstrip('/')}" if base and hit.url else ""
            hits.append({"title": hit.display_title, "section": hit.subtitle,
                         "excerpt": hit.content, "href": href, "doc_id": hit.doc_id})
        failed = [o for o in outcomes if not o.ok and o.status != STATUS_EMPTY]
        if failed and not hits:
            status = {"status": failed[0].status, "reason": failed[0].reason}
        elif not hits:
            status = {"status": STATUS_EMPTY, "reason": "검색 결과 0건"}
    except Exception as exc:  # noqa: BLE001 — 참고 인용 실패가 조사 응답을 막지 않는다(사유 노출)
        logger.warning("KB 참고 문서 검색 실패: %s", exc, exc_info=True)
        status = {"status": "error", "reason": f"문서 검색 실패: {type(exc).__name__}"}
    try:
        from src.security.audit_logger import log_doc_retrieval
        from src.security.pii_filter import scrub_pii

        await log_doc_retrieval(
            collection_ids=[_KB_REFERENCE_COLLECTION], status=str(status["status"]),
            hit_count=len(hits), elapsed_ms=(time.monotonic() - started) * 1000,
            query=scrub_pii(query), doc_ids=[h["doc_id"] for h in hits if h["doc_id"]][:5],
            source=_KB_ENTRY_POINT, reason=status["reason"] or None,
            user_id=current_user.get("sub"),
        )
    except Exception:  # noqa: BLE001
        logger.warning("KB 참고 문서 감사 기록 실패", exc_info=True)
    return merge_references(pinned, hits), status


async def _kb_briefing_extras(
    config: Any, current_user: dict[str, Any], briefing: dict[str, Any]
) -> dict[str, Any]:
    """운영자 뷰 브리핑 사본 + `playbook.references`·`references_status` ·
    KE `recurrence`(§5.7.6).
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from src.domain.kb_draft import (
        ke_activity,
        known_error_ids,
        playbook_of,
        recurrence_of,
        with_kb_extras,
    )

    if playbook_of(briefing) is None:
        return briefing
    references, status = await _kb_references(config, current_user, briefing)
    recurrence: dict[str, dict[str, Any]] = {}
    if known_error_ids(briefing):
        recurrence = recurrence_of(ke_activity(_kb_scan_records(config), now=_dt.now(_UTC)))
    return with_kb_extras(
        briefing, references=references, references_status=status, recurrence=recurrence
    )


#: 알려진 오류 id 형식(plans/187 교정 M6) — sre_agent `known_error._KE_ID_PATTERN`
#: (`^KE-[A-Z0-9][A-Z0-9-]*$`)에 길이 상한 64를 더한 것. 경로 값이 이 밖이면 저장소를 읽기 전에 400.
_KE_ID_RE = re.compile(r"^KE-[A-Z0-9][A-Z0-9-]{0,60}$")


class KnownErrorFeedbackRequest(BaseModel):
    """알려진 오류 사용 피드백 본문."""

    verdict: str = Field(
        ..., max_length=32,
        description="correct(맞음) | incorrect(아님) | workaround_ineffective(우회 효과 없음)",
    )


class EngineChoiceRequest(BaseModel):
    """엔진 선택 본문."""

    choice: str = Field(..., max_length=32, description="playbook | holmesgpt | both | neither")


@router.post(
    "/alarm/investigations/{investigation_id}/kb-draft",
    summary="KB 초안 만들기(운영자 · 결정적)",
    description=(
        "완료된 플레이북 조사의 브리핑으로 알려진 오류 레코드 초안(state=wip 고정)을 만들어 "
        "본체 KB 기록에 남깁니다. 증상=알람 원문 · 환경=스코프·플레이북 · "
        "match 후보=성립한 판독 enum · "
        "원인=1순위 가설(미검증 · 엔진 선택이 있으면 그 결론) · cause_test·우회=빈 칸. "
        "LLM을 쓰지 않고 sre_agent 파일에 쓰지 않습니다.<br/>"
        "운영자(관리자)만 · 존 검사 · `NOISE_INVESTIGATION_KB_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_kb_enabled)],
)
async def create_kb_draft(
    investigation_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """조사 → `{draft_id, investigation_id, state, draft, yaml}`."""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from src.domain.kb_draft import DRAFT_STATE, build_draft, draft_to_yaml
    from src.security.audit_logger import INVESTIGATION_DENIED, INVESTIGATION_OK

    config = request.app.state.config
    if not _is_alarm_operator(config, current_user):
        await _audit_kb(current_user, INVESTIGATION_DENIED, "kb_draft",
                        investigation_id=investigation_id, reason="operator_only")
        raise _followup_error(403, "operator_only", "KB 초안은 운영자만 만들 수 있습니다.")
    rec = _kb_investigation(request, current_user, investigation_id)
    briefing = _kb_playbook_briefing(rec, require_done=True)
    store = _kb_store(config)
    sub = str(current_user.get("sub") or "")
    now = _dt.now(_UTC)
    draft = build_draft(
        draft_id=_uuid.uuid4().hex[:12],
        investigation_id=investigation_id,
        briefing=briefing,
        alarm=_kb_alarm(config, rec),
        engine_choice=store.engine_choice_of(investigation_id, sub),
        created_by=sub,
        created_at=now.isoformat(),
    )
    if not store.record_draft(draft, ts=now):
        raise _followup_error(503, "store_write_failed", "KB 초안 기록에 실패했습니다.")
    await _audit_kb(current_user, INVESTIGATION_OK, "kb_draft", investigation_id=investigation_id,
                    draft_id=draft["draft_id"], playbook_id=draft["playbook_id"])
    return {
        "draft_id": draft["draft_id"],
        "investigation_id": investigation_id,
        "state": DRAFT_STATE,
        "draft": draft,
        "yaml": draft_to_yaml(draft),
    }


@router.get(
    "/alarm/investigations/{investigation_id}/postmortem",
    summary="포스트모템 내보내기(작성 기준 충족 건만)",
    description=(
        "완료된 플레이북 조사를 PagerDuty 템플릿 절 Markdown으로 내보냅니다. "
        "작성 기준(당직 호출 PAGE · 사용자 영향 판정 등급 — 전부 충족)을 못 채우면 "
        "409와 미충족 기준을 돌려줍니다.<br/>"
        "존 검사 · 사용자 경로는 값 마스킹 · "
        "`NOISE_INVESTIGATION_KB_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_kb_enabled)],
)
async def export_postmortem(
    investigation_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """조사 → `{investigation_id, criteria[], markdown}` · 미충족 409 `{code, reason, criteria}`."""
    from noise_gate.infrastructure.investigation_store import investigation_view
    from src.domain.kb_draft import evaluate_postmortem, render_postmortem
    from src.security.audit_logger import INVESTIGATION_DENIED, INVESTIGATION_OK

    config = request.app.state.config
    rec = _kb_investigation(request, current_user, investigation_id)
    briefing = _kb_playbook_briefing(rec, require_done=True)
    alarm = _kb_alarm(config, rec)
    level = investigation_view(rec)["verdict_level"]
    evaluation = evaluate_postmortem(tier=alarm.get("tier"), verdict_level=level)
    if not evaluation["eligible"]:
        await _audit_kb(current_user, INVESTIGATION_DENIED, "postmortem",
                        investigation_id=investigation_id, reason="criteria_unmet")
        raise HTTPException(status_code=409, detail={
            "code": "criteria_unmet", "reason": evaluation["reason"],
            "criteria": evaluation["criteria"],
        })
    operator = _is_alarm_operator(config, current_user)
    markdown = render_postmortem(
        investigation_id=investigation_id, briefing=briefing, alarm=alarm,
        evaluation=evaluation, verdict_level=level,
        mask_fn=None if operator else _investigation_mask_fn(config),
    )
    await _audit_kb(current_user, INVESTIGATION_OK, "postmortem", investigation_id=investigation_id)
    return {
        "investigation_id": investigation_id,
        "criteria": evaluation["criteria"],
        "markdown": markdown,
    }


@router.post(
    "/alarm/investigations/{investigation_id}/known-errors/{ke_id}/feedback",
    summary="알려진 오류 사용 피드백(맞음/아님/우회 효과 없음)",
    description=(
        "브리핑의 알려진 오류 카드 피드백을 KE별로 기록합니다(KCS Reuse is Review). "
        "「아님」이 연속 N회면 관리자 검토 대상에 오릅니다"
        "(자동 강등 없음 · 판정·가설 순위 불변).<br/>"
        "그 조사 브리핑에 실린 KE만 받습니다 · 존 검사 · "
        "`NOISE_INVESTIGATION_KB_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_kb_enabled)],
)
async def record_known_error_feedback(
    investigation_id: str,
    ke_id: str,
    body: KnownErrorFeedbackRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """KE 피드백 1건 → `{recorded, investigation_id, ke_id, verdict, ts}`."""
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from src.domain.kb_draft import KE_FEEDBACK_LABELS, known_error_ids, playbook_of
    from src.security.audit_logger import INVESTIGATION_DENIED, INVESTIGATION_OK

    # (교정 M6) 형식·값 검증 거부도 감사 1건(경로 값은 형식이 맞을 때만 싣는다).
    if not _KE_ID_RE.match(ke_id):
        await _audit_kb(current_user, INVESTIGATION_DENIED, "ke_feedback",
                        investigation_id=investigation_id, reason="invalid_ke_id")
        raise _followup_error(400, "invalid_ke_id", "알려진 오류 id 형식이 아닙니다(KE-...).")
    if body.verdict not in KE_FEEDBACK_LABELS:
        await _audit_kb(current_user, INVESTIGATION_DENIED, "ke_feedback",
                        investigation_id=investigation_id, ke_id=ke_id, reason="invalid_verdict")
        raise _followup_error(
            400, "invalid_verdict", f"verdict는 {'·'.join(KE_FEEDBACK_LABELS)} 중 하나입니다."
        )
    config = request.app.state.config
    rec = _kb_investigation(request, current_user, investigation_id)
    briefing = _kb_playbook_briefing(rec, require_done=False)
    if ke_id not in known_error_ids(briefing):
        await _audit_kb(current_user, INVESTIGATION_DENIED, "ke_feedback",
                        investigation_id=investigation_id, ke_id=ke_id, reason="ke_not_found")
        raise _followup_error(404, "ke_not_found", "이 조사 브리핑에 그 알려진 오류가 없습니다.")
    store = _kb_store(config)
    now = _dt.now(_UTC)
    playbook_id = str((playbook_of(briefing) or {}).get("id") or "")
    if not store.record_ke_feedback(
        ke_id=ke_id, investigation_id=investigation_id, verdict=body.verdict,
        labeled_by=str(current_user.get("sub") or ""), playbook_id=playbook_id, ts=now,
    ):
        raise _followup_error(503, "store_write_failed", "피드백 기록에 실패했습니다.")
    await _audit_kb(current_user, INVESTIGATION_OK, "ke_feedback",
                    investigation_id=investigation_id, ke_id=ke_id, verdict=body.verdict)
    return {"recorded": True, "investigation_id": investigation_id, "ke_id": ke_id,
            "verdict": body.verdict, "ts": now.isoformat()}


@router.post(
    "/alarm/investigations/{investigation_id}/engine-choice",
    summary="엔진 선택 기록(플레이북/HolmesGPT/둘 다/둘 다 아님)",
    description=(
        "「이 결론이 맞다」 선택을 조사·사용자별로 기록합니다(최신 1건이 유효). 판정·티어·통보는 "
        "바뀌지 않습니다 — KB 초안 원인 기본값·유형별 적중 통계·모드 전환 권고에만 씁니다.<br/>"
        "플레이북 조사만 · 존 검사 · `NOISE_INVESTIGATION_KB_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_kb_enabled)],
)
async def record_engine_choice(
    investigation_id: str,
    body: EngineChoiceRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """엔진 선택 1건 → `{recorded, investigation_id, choice, playbook_id, ts}`."""
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from src.domain.kb_draft import ENGINE_CHOICES, playbook_of
    from src.security.audit_logger import INVESTIGATION_DENIED, INVESTIGATION_OK

    if body.choice not in ENGINE_CHOICES:
        await _audit_kb(current_user, INVESTIGATION_DENIED, "engine_choice",
                        investigation_id=investigation_id, reason="invalid_choice")
        raise _followup_error(
            400, "invalid_choice", f"choice는 {'·'.join(ENGINE_CHOICES)} 중 하나입니다."
        )
    config = request.app.state.config
    rec = _kb_investigation(request, current_user, investigation_id)
    briefing = _kb_playbook_briefing(rec, require_done=False)
    playbook_id = str((playbook_of(briefing) or {}).get("id") or "")
    store = _kb_store(config)
    now = _dt.now(_UTC)
    if not store.record_engine_choice(
        investigation_id=investigation_id, choice=body.choice, playbook_id=playbook_id,
        chosen_by=str(current_user.get("sub") or ""), ts=now,
    ):
        raise _followup_error(503, "store_write_failed", "엔진 선택 기록에 실패했습니다.")
    await _audit_kb(current_user, INVESTIGATION_OK, "engine_choice",
                    investigation_id=investigation_id, choice=body.choice, playbook_id=playbook_id)
    return {"recorded": True, "investigation_id": investigation_id, "choice": body.choice,
            "playbook_id": playbook_id, "ts": now.isoformat()}


# ─── 폴스타 원문 메시지 분석 엔드포인트 ──────────────────────────────────────

def _parse_raw_message(message: str) -> dict:
    """폴스타 단일행 JSON 원문을 파싱한다 (TcpReceiver._parse()와 동일한 로직)."""
    return json.loads(message.strip())


# (Plan 60 E7-c §17.5) 이질 포맷에서 severity 미식별 시 보수적 폴백값 — 비-해소(드롭 방지).
_E7C_CONSERVATIVE_SEVERITY = 2


def _build_alarm_event_from_payload(
    payload: dict, *, format_tolerant: bool = False
) -> AlarmEvent:
    """Redis Stream 페이로드(또는 파싱된 폴스타 JSON)를 AlarmEvent로 변환한다.

    (Plan 60 E7-c §17.5) format_tolerant=True면 이질 포맷(호스트 접두 없음·네트워크 장비·설비)
    에 대해 graceful 폴백을 적용한다 — 침묵 드롭·크래시 금지: severity 누락/비정수는 보수적
    비-해소 값으로 폴백(is_clear 오분류→드롭 방지)하고, 네트워크 장비 포맷("||(장애) <사이트명>")
    에서 사이트 토큰을 추출해 raw_payload 사본에 `_site_token`으로 노출한다(원본 payload 불변).
    format_tolerant=False(기본)면 **현행과 비트동일**(누락 severity→0, 비정수→ValueError 전파).
    알려진 포맷(정상 severity)의 파싱 결과는 두 경로에서 동일하다(신규 폴백만 추가).
    """
    from datetime import datetime as _dt

    alarm_time_str = payload.get("alarmTime", "")
    try:
        alarm_time = _dt.strptime(alarm_time_str, "%Y%m%d%H%M%S") if alarm_time_str else _dt.now()
    except ValueError:
        alarm_time = _dt.now()

    alarm_status = payload.get("alarmStatus", "")
    # (D-184) 폴스타 `${severity}`는 한글 라벨로 렌더링된다 — 워커 `_process`와 동일한
    # 결정적 정규화(noise_gate.domain.severity)를 쓴다(경로 대칭). 정수·정수 문자열·라벨 수용.
    raw_sev = payload.get("severity", None)
    if format_tolerant:
        # 누락·미지 값 → 보수적(드롭·크래시 방지). 라벨/정수는 정규화.
        severity, _ = coerce_severity(raw_sev, fallback=_E7C_CONSERVATIVE_SEVERITY)
    else:
        # 누락 → 0(현행 유지). 라벨/정수 문자열은 정규화, 미지 값은 SeverityParseError(ValueError
        # 하위) 전파 — 현행 '비정수 → ValueError' 계약 유지.
        severity = 0 if raw_sev is None else parse_severity(raw_sev)
    # is_clear는 severity == 0 단독 기준 — alarmStatus는 ACK 상태로 무관 (Plan 47 §9)
    is_clear = severity == 0

    raw_payload = payload
    if format_tolerant:
        # (E7-c) 사이트 토큰 추출(네트워크 장비 포맷) — 원본 payload를 변형하지 않도록 사본에 노출.
        from noise_gate.domain.correlation import extract_site_token

        site = extract_site_token(
            str(payload.get("serverName", "") or ""),
            str(payload.get("resourceName", "") or ""),
        )
        if site:
            raw_payload = {**payload, "_site_token": site}

    return AlarmEvent(
        db_id=payload.get("dbId", ""),
        server_name=payload.get("serverName", ""),
        hostname=payload.get("hostname", ""),
        ip_address=payload.get("ipAddress", ""),
        resource_ancestry=payload.get("resourceAncestry", ""),
        alarm_id=str(payload.get("alarmId", str(uuid.uuid4()))),
        severity=severity,
        alarm_status=alarm_status,
        resource_type=payload.get("resourceType", ""),
        resource_name=payload.get("resourceName", ""),
        alarm_name=payload.get("alarmName", ""),
        alarm_time=alarm_time,
        conditions=payload.get("conditions", ""),
        condition_log=payload.get("conditionLog", ""),
        is_clear=is_clear,
        raw_payload=raw_payload,
        received_at=_dt.now(),
    )


@router.post(
    "/alarm/analyze-test/raw",
    response_model=AlarmTestResponse,
    summary="폴스타 원문 메시지 알람 분석 테스트",
    description=(
        "폴스타 TCP 소켓으로 전달되는 <b>단일행 JSON 원문</b>을 그대로 붙여넣어 분석합니다.<br/>"
        "소켓 수신 → JSON 파싱 → AlarmEvent 변환 → LLM 분석 전체 파이프라인을 시뮬레이션합니다.<br/>"
        "실제 폴스타 메시지를 복사해서 테스트할 때 사용하세요.<br/>"
        "<b>관리자 전용</b>(관리자 역할 사용자 또는 운영자 토큰 · 그 밖은 403)."
    ),
    tags=["alarm"],
)
async def analyze_alarm_raw(
    request: Request,
    body: AlarmRawTestRequest,
    # 시험 도구 — 관리자 전용(`analyze_alarm_test`와 같은 가드)
    current_user: dict = Depends(require_admin_user),
) -> AlarmTestResponse:
    """폴스타 원문 JSON을 파싱해 AlarmEvent를 구성하고 LLM 분석을 실행한다."""
    start_time = time.time()
    config = request.app.state.config

    # 1. 원문 JSON 파싱 (TcpReceiver._parse()와 동일)
    try:
        payload = _parse_raw_message(body.message)
    except Exception as exc:
        return AlarmTestResponse(
            alarm_id="PARSE-ERROR",
            severity=0,
            severity_label="알 수 없음",
            analysis=None,
            notification_channels=[],
            notification_preview=NotificationPreview(),
            error=admin_error_detail("메시지 파싱", exc, logger, request),
            processing_time_ms=(time.time() - start_time) * 1000,
        )

    # 2. AlarmEvent 구성 (AlarmWorker._process()와 동일)
    # (Plan 60 E7-c) format_tolerant_parsing_enabled면 이질 포맷 graceful 폴백(off면 비트동일).
    event = _build_alarm_event_from_payload(
        payload,
        format_tolerant=bool(
            getattr(getattr(config, "noise_gate", None), "format_tolerant_parsing_enabled", False)
        ),
    )
    # (D-188) hostname 역조회 — 워커 경로와 대칭
    await _attach_server_identity(config, event)

    # 3. 사용할 채널 결정
    channels: list[str] = (
        body.channels
        if body.channels is not None
        else config.alarm.get_notification_channels()
    )

    # 3-1. 이력 통계 산출 (Plan 47 — query_history / simulated_history)
    history_stats = await _resolve_history_stats(
        config, event, body.query_history, body.simulated_history
    )

    # 3-2. 영향 프로세스 스냅샷 산출 (Plan 47-1 — query_process / simulated_processes)
    process_snapshot = await _resolve_process_snapshot(
        config, event, body.query_process, body.simulated_processes
    )

    # 4. LLM 알람 분석
    from noise_gate.application.nodes.alarm_analyzer import alarm_analyzer_node

    state: dict[str, Any] = {
        "alarm_event": event,
        "history_stats": history_stats,
        "process_snapshot": process_snapshot,
        "analysis_result": None,
        "error": None,
    }
    lc_config = {
        "configurable": {"app_config": config, **_alarm_extra_configurable(request, config)}
    }

    try:
        result_state = await alarm_analyzer_node(state, lc_config)
    except Exception as exc:
        elapsed_ms = (time.time() - start_time) * 1000
        return _analysis_failure_response(
            event, channels, admin_error_detail("알람 분석", exc, logger, request), elapsed_ms
        )

    analysis_result: Optional[AlarmAnalysisResult] = result_state.get("analysis_result")
    analyzer_error: Optional[str] = result_state.get("error")

    if analyzer_error or not analysis_result:
        elapsed_ms = (time.time() - start_time) * 1000
        return _analysis_failure_response(
            event, channels, analyzer_error or "알람 분석 결과를 받지 못했습니다.", elapsed_ms
        )

    analysis_result.notification_channels = channels

    # 5. 웹 UI 푸시
    if body.push_to_ui:
        await request.app.state.alarm_bus.publish(
            build_ui_push_payload(event, analysis_result, history_stats, process_snapshot)
        )

    # 6. 발송 미리보기
    preview = _build_notification_preview(config, analysis_result, channels, process_snapshot)

    # 7. 실제 발송 (dry_run=False + send_notification=True)
    notifications_sent: Optional[dict[str, bool]] = None
    if not body.dry_run and body.send_notification:
        from noise_gate.application.nodes.alarm_notifier import alarm_notifier_node

        # result_state는 노드의 업데이트 dict({"analysis_result": ...})만 담으므로
        # 게이트/notifier가 쓰는 alarm_event·history_stats는 원본 state에서 병합한다.
        notifier_state = {
            **state, **result_state, "process_snapshot": process_snapshot, "error": None
        }
        # Plan 52 E3: 게이트 활성 시 4-티어 판단을 산출해 notifier에 전달한다
        # (TICKET 큐 적재·DASHBOARD/TICKET SSE 동작). 게이트 off면 decision 미생성 →
        # notifier는 기존 발송 경로로 폴백(무변경, 회귀 0).
        if config.noise_gate.enable_noise_gate:
            from noise_gate.application.nodes.notification_gate import (
                notification_gate_node,
            )

            gate_out = await notification_gate_node(notifier_state, lc_config)
            notifier_state = {**notifier_state, **gate_out}
        try:
            notifier_out = await alarm_notifier_node(notifier_state, lc_config)
            sent_result: Optional[AlarmAnalysisResult] = notifier_out.get("analysis_result")
            notifications_sent = sent_result.notifications_sent if sent_result else {}
        except Exception as exc:
            notifications_sent = {ch: False for ch in channels}
            analyzer_error = admin_error_detail("알림 발송", exc, logger, request)

    elapsed_ms = (time.time() - start_time) * 1000

    return AlarmTestResponse(
        alarm_id=event.alarm_id,
        severity=event.severity,
        severity_label=analysis_result.severity_label,
        analysis=AlarmAnalysisOutput(
            severity_label=analysis_result.severity_label,
            summary=analysis_result.summary,
            probable_cause=analysis_result.probable_cause,
            recommended_action=analysis_result.recommended_action,
            pattern_type=analysis_result.pattern_type,
            is_routine=analysis_result.is_routine,
            pattern_analysis=analysis_result.pattern_analysis,
        ),
        notification_channels=channels,
        notification_preview=preview,
        notifications_sent=notifications_sent,
        history_stats=_stats_to_dict(history_stats) if history_stats else None,
        process_snapshot=_process_to_dict(process_snapshot) if process_snapshot else None,
        error=analyzer_error,
        processing_time_ms=elapsed_ms,
    )
