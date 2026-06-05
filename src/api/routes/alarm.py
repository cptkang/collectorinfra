"""알람 분석 테스트 API.

폴스타와의 실제 TCP 연결 없이, 알람 페이로드를 직접 입력하여
알람 분석 에이전트를 실행하고 결과를 확인하기 위한 테스트 엔드포인트.

지원 기능:
    - dry_run=true : 발송 없이 분석 결과 + 발송될 메시지 미리보기 반환
    - send_notification=true : 실제 채널(workb/webhook)로 알림 발송
    - channels 오버라이드 : 설정 채널 대신 지정 채널로 발송
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from src.api.dependencies import require_user
from src.alarm.domain.alarm import AlarmAnalysisResult, AlarmEvent

router = APIRouter()


# ─── Request / Response 스키마 ───────────────────────────────────────────────

class AlarmTestRequest(BaseModel):
    """알람 분석 테스트 요청.

    폴스타 템플릿 변수 이름과 동일하게 맞춰 실제 메시지를 그대로 붙여넣을 수 있도록 한다.
    """

    # ── 폴스타 알람 필드 (AlarmEvent와 1:1 대응) ──
    alarm_id: str = Field(default="TEST-001", description="${alarmId} — 중복 제거 키")
    severity: int = Field(
        default=2,
        ge=0,
        le=3,
        description="${severity} — 0=해소, 1=주의, 2=경고, 3=심각",
    )
    alarm_name: str = Field(default="", description="${alarmName} — 알람 이름")
    alarm_description: str = Field(default="", description="${alarmDescription} — 알람 설명")
    alarm_definition: str = Field(default="", description="${alarmDefinition} — 알람 정의")
    hostname: str = Field(default="", description="${hostname} — 대상 호스트명")
    resource_name: str = Field(default="", description="${resourceName} — 대상 자원 이름")
    resource_description: str = Field(default="", description="${resourceDescription} — 자원 설명")
    resource_type: str = Field(
        default="server.Server",
        description="${resourceType} — 예: server.Server, server.Cpus, network.NMSNode",
    )
    condition_log: str = Field(default="", description="${conditionLog} — 컨디션 로그 (임계치 정보 포함)")
    source_db_id: str = Field(default="polestar", description="발신 DB 식별자 (polestar, polestar_cm_gp 등)")
    is_clear: bool = Field(default=False, description="True이면 알람 해소 이벤트")

    # ── 테스트 제어 파라미터 ──
    dry_run: bool = Field(
        default=True,
        description=(
            "True: 알람 분석만 수행하고 실제 알림을 발송하지 않음 (발송 메시지 미리보기 포함). "
            "False: send_notification 설정에 따라 실제 발송 여부 결정."
        ),
    )
    send_notification: bool = Field(
        default=False,
        description=(
            "dry_run=False일 때만 유효. "
            "True: channels에 지정된 채널로 실제 알림 발송. "
            "False: 분석 결과만 반환하고 발송 안 함."
        ),
    )
    channels: Optional[list[str]] = Field(
        default=None,
        description=(
            "알림 채널 오버라이드. "
            "null이면 서버 설정(ALARM_NOTIFICATION_CHANNELS_CSV) 사용. "
            "예: [\"workb\"], [\"workb\", \"webhook\"]"
        ),
    )

    model_config = {"json_schema_extra": {
        "example": {
            "alarm_id": "ALARM-20260604-001",
            "severity": 3,
            "alarm_name": "CPU 사용률 임계 초과",
            "alarm_description": "서버 CPU 사용률이 설정된 임계값을 초과하였습니다.",
            "alarm_definition": "서버의 최근 5분 평균 CPU 사용률이 90%를 초과하면 심각 알람 발생",
            "hostname": "svr-infra-001",
            "resource_name": "svr-infra-001 CPU",
            "resource_description": "인프라 서버 CPU 리소스",
            "resource_type": "server.Cpus",
            "condition_log": "CPU Usage=95.2%, Threshold=90%, Duration=5min",
            "source_db_id": "polestar",
            "is_clear": False,
            "dry_run": True,
            "send_notification": False,
            "channels": None,
        }
    }}


class _WorkbPreview(BaseModel):
    """WorkB 발송 미리보기."""
    title: str
    body: str
    recipients: str = Field(description="수신자 사번 목록 (쉼표 구분)")
    alias: str
    api_url: Optional[str]


class _WebhookPreview(BaseModel):
    """Webhook 발송 미리보기."""
    url: Optional[str]
    payload: dict[str, Any]


class NotificationPreview(BaseModel):
    """채널별 발송 메시지 미리보기."""
    workb: Optional[_WorkbPreview] = None
    webhook: Optional[_WebhookPreview] = None


class AlarmAnalysisOutput(BaseModel):
    """LLM 알람 분석 결과."""
    severity_label: str = Field(description="심각 | 경고 | 주의")
    summary: str = Field(description="LLM 생성 요약 (1~2문장)")
    probable_cause: str = Field(description="추정 원인")
    recommended_action: str = Field(description="권고 조치")


class AlarmTestResponse(BaseModel):
    """알람 분석 테스트 응답."""
    alarm_id: str
    severity: int
    severity_label: str
    analysis: Optional[AlarmAnalysisOutput] = Field(
        default=None,
        description="LLM 분석 결과. 분석 실패 시 null.",
    )
    notification_channels: list[str] = Field(description="사용된 채널 목록")
    notification_preview: NotificationPreview = Field(
        description="dry_run=True 또는 send_notification=False일 때 발송될 메시지 미리보기",
    )
    notifications_sent: Optional[dict[str, bool]] = Field(
        default=None,
        description="실제 발송 시 채널별 성공 여부. dry_run=True이면 null.",
    )
    error: Optional[str] = Field(default=None, description="분석 실패 시 오류 메시지")
    processing_time_ms: float


# ─── 유틸 함수 ───────────────────────────────────────────────────────────────

_SEVERITY_LABELS = {0: "해소", 1: "주의", 2: "경고", 3: "심각"}


def _build_workb_preview(workb_cfg, result: AlarmAnalysisResult) -> _WorkbPreview:
    """WorkB 발송 미리보기를 생성한다."""
    ev = result.alarm_event
    title = f"[{result.severity_label}] {ev.resource_name} ({ev.hostname})"
    body = (
        f"알람명: {ev.alarm_name}\n"
        f"설명: {ev.alarm_description}\n"
        f"자원: {ev.resource_name} ({ev.resource_type})\n"
        f"컨디션: {ev.condition_log}\n\n"
        f"요약: {result.summary}\n"
        f"원인: {result.probable_cause}\n"
        f"권고 조치: {result.recommended_action}"
    )
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


def _build_webhook_preview(alarm_cfg, result: AlarmAnalysisResult) -> _WebhookPreview:
    """Webhook 발송 미리보기를 생성한다."""
    ev = result.alarm_event
    payload = {
        "alarm_id": ev.alarm_id,
        "severity": ev.severity,
        "severity_label": result.severity_label,
        "alarm_name": ev.alarm_name,
        "hostname": ev.hostname,
        "resource_name": ev.resource_name,
        "resource_type": ev.resource_type,
        "condition_log": ev.condition_log,
        "summary": result.summary,
        "probable_cause": result.probable_cause,
        "recommended_action": result.recommended_action,
        "source_db_id": ev.source_db_id,
        "is_clear": ev.is_clear,
    }
    return _WebhookPreview(
        url=alarm_cfg.webhook_url or None,
        payload=payload,
    )


def _build_notification_preview(
    config,
    result: AlarmAnalysisResult,
    channels: list[str],
) -> NotificationPreview:
    """채널 목록에 따라 발송 미리보기를 생성한다."""
    preview = NotificationPreview()
    for ch in channels:
        if ch == "workb":
            preview.workb = _build_workb_preview(config.workb, result)
        elif ch == "webhook":
            preview.webhook = _build_webhook_preview(config.alarm, result)
    return preview


# ─── 엔드포인트 ───────────────────────────────────────────────────────────────

@router.post(
    "/alarm/analyze-test",
    response_model=AlarmTestResponse,
    summary="알람 분석 테스트",
    description=(
        "폴스타 알람 페이로드를 직접 입력하여 알람 분석 에이전트를 실행합니다.<br/>"
        "<b>dry_run=true</b>(기본값): 분석 결과 + 발송될 메시지 미리보기만 반환, 실제 발송 안 함.<br/>"
        "<b>dry_run=false, send_notification=true</b>: 설정된 채널로 실제 알림 발송.<br/>"
        "<b>channels</b>: 특정 채널만 테스트하고 싶을 때 지정 (예: [\"workb\"])."
    ),
    tags=["alarm"],
)
async def analyze_alarm_test(
    request: Request,
    body: AlarmTestRequest,
    current_user: dict = Depends(require_user),
) -> AlarmTestResponse:
    """알람 페이로드를 입력받아 LLM 분석 및 알림 미리보기(또는 실제 발송)를 반환한다."""
    start_time = time.time()
    config = request.app.state.config

    # 1. AlarmEvent 구성
    event = AlarmEvent(
        alarm_id=body.alarm_id or str(uuid.uuid4()),
        severity=body.severity,
        alarm_name=body.alarm_name,
        alarm_description=body.alarm_description,
        alarm_definition=body.alarm_definition,
        hostname=body.hostname,
        resource_name=body.resource_name,
        resource_description=body.resource_description,
        resource_type=body.resource_type,
        condition_log=body.condition_log,
        source_db_id=body.source_db_id,
        is_clear=body.is_clear,
    )

    # 2. 사용할 채널 결정 (요청 오버라이드 > 서버 설정)
    channels: list[str] = (
        body.channels
        if body.channels is not None
        else config.alarm.get_notification_channels()
    )

    # 3. LLM 알람 분석 실행 (analyzer 노드만 직접 호출)
    from src.alarm.application.nodes.alarm_analyzer import alarm_analyzer_node

    state: dict[str, Any] = {
        "alarm_event": event,
        "analysis_result": None,
        "error": None,
    }
    lc_config = {"configurable": {"app_config": config}}

    try:
        result_state = await alarm_analyzer_node(state, lc_config)
    except Exception as exc:
        elapsed_ms = (time.time() - start_time) * 1000
        return AlarmTestResponse(
            alarm_id=event.alarm_id,
            severity=event.severity,
            severity_label=_SEVERITY_LABELS.get(event.severity, str(event.severity)),
            analysis=None,
            notification_channels=channels,
            notification_preview=NotificationPreview(),
            error=f"알람 분석 실패: {exc}",
            processing_time_ms=elapsed_ms,
        )

    analysis_result: Optional[AlarmAnalysisResult] = result_state.get("analysis_result")
    analyzer_error: Optional[str] = result_state.get("error")

    # 4. 분석 실패 응답
    if analyzer_error or not analysis_result:
        elapsed_ms = (time.time() - start_time) * 1000
        return AlarmTestResponse(
            alarm_id=event.alarm_id,
            severity=event.severity,
            severity_label=_SEVERITY_LABELS.get(event.severity, str(event.severity)),
            analysis=None,
            notification_channels=channels,
            notification_preview=NotificationPreview(),
            error=analyzer_error or "알람 분석 결과를 받지 못했습니다.",
            processing_time_ms=elapsed_ms,
        )

    # 5. 분석 결과 채널 동기화
    analysis_result.notification_channels = channels

    # 6. 발송 미리보기 생성 (dry_run 여부와 무관하게 항상 생성)
    preview = _build_notification_preview(config, analysis_result, channels)

    # 7. 실제 발송 (dry_run=False + send_notification=True일 때만)
    notifications_sent: Optional[dict[str, bool]] = None
    if not body.dry_run and body.send_notification:
        from src.alarm.application.nodes.alarm_notifier import alarm_notifier_node

        notifier_state = {**result_state, "error": None}
        try:
            notifier_out = await alarm_notifier_node(notifier_state, lc_config)
            sent_result: Optional[AlarmAnalysisResult] = notifier_out.get("analysis_result")
            notifications_sent = sent_result.notifications_sent if sent_result else {}
        except Exception as exc:
            notifications_sent = {ch: False for ch in channels}
            analyzer_error = f"알림 발송 실패: {exc}"

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
        ),
        notification_channels=channels,
        notification_preview=preview,
        notifications_sent=notifications_sent,
        error=analyzer_error,
        processing_time_ms=elapsed_ms,
    )
