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
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.dependencies import require_user
from src.alarm.domain.alarm import (
    AlarmAnalysisResult,
    AlarmEvent,
    AlarmHistoryEntry,
    AlarmHistoryStats,
    ProcessSnapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Request / Response 스키마 ───────────────────────────────────────────────

class AlarmTestRequest(BaseModel):
    """알람 분석 테스트 요청.

    폴스타 단일행 JSON 템플릿 변수 이름과 동일하게 맞춰 실제 메시지를 그대로 붙여넣을 수 있도록 한다.
    """

    # ── 폴스타 알람 필드 (AlarmEvent와 1:1 대응) ──
    db_id: str = Field(default="polestar_b0", description="dbId — 폴스타 인스턴스 식별자 (상수 직접 기입)")
    server_name: str = Field(default="", description="${platformName} — 폴스타 등록 서버명")
    hostname: str = Field(default="", description="${hostname} — 호스트네임")
    ip_address: str = Field(default="", description="${ipAddress} — IP 주소")
    resource_ancestry: str = Field(default="", description="${resourceAncestry} — 폴스타 트리 전체 경로")
    alarm_id: str = Field(default="TEST-001", description="${alarmId} — 중복 제거 키")
    severity: int = Field(
        default=2,
        ge=0,
        le=3,
        description="${severity} — 0=해소, 1=주의, 2=경고, 3=심각",
    )
    alarm_status: str = Field(
        default="NOT_ACK",
        description=(
            "${alarmStatus} — 폴스타 UI 인지(ACK) 상태 (NOT_ACK 등). "
            "해소 판정에 사용하지 않음 (해소는 severity=0 단독 기준)"
        ),
    )
    resource_type: str = Field(
        default="server.Server",
        description="${resourceType} — 예: server.Server, server.Cpus, network.NMSNode",
    )
    resource_name: str = Field(default="", description="${resourceName} — 자원 이름")
    alarm_name: str = Field(default="", description="${alarmName} — 알람 이름")
    alarm_time: str = Field(
        default="",
        description="${formatAlarmDate('yyyyMMddHHmmss')} — 알람 일시 (yyyyMMddHHmmss 형식, 비어있으면 현재 시각)",
    )
    conditions: str = Field(default="", description="${conditions} — 발생/해소 임계 조건 정의")
    condition_log: str = Field(default="", description="${conditionLog} — 이 알람이 울린 실제 값")
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
    push_to_ui: bool = Field(
        default=True,
        description="True이면 분석 결과를 웹 UI 채팅창에 알람 말풍선으로 실시간 표시한다.",
    )
    # ── Plan 47: 이력 패턴 분석 테스트 파라미터 ──
    query_history: bool = Field(
        default=True,
        description=(
            "폴스타 DB 이력 조회 수행 여부. 테스트 엔드포인트 기본값 True — "
            "패턴 근거표를 바로 확인할 수 있다. simulated_history 지정 시 그쪽 우선, "
            "false로 주면 이력 조회를 생략한다."
        ),
    )
    simulated_history: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description=(
            "[{\"alarm_time\":\"yyyyMMddHHmmss\",\"severity\":3,"
            "\"alarm_status\":\"NOT_ACK\",\"resource_name\":\"...\"}] 형식. "
            "지정 시 DB 조회 대신 이 목록으로 통계 계산 — 이력 시나리오"
            "(주기/급증/첫 발생)를 임의 구성하여 LLM 응답 검증 가능."
        ),
    )
    # ── Plan 47-1: 영향 프로세스 보강 테스트 파라미터 ──
    query_process: bool = Field(
        default=True,
        description=(
            "폴스타 프로세스 API 호출 여부 (hostname 기준). 테스트 엔드포인트 기본값 True. "
            "CPU/메모리 발생 알람 + base_url 매핑이 있어야 실제 조회되며(그 외엔 자동 생략), "
            "simulated_processes 지정 시 그쪽 우선."
        ),
    )
    simulated_processes: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description=(
            "지정 시 API 호출 없이 이 목록으로 select_top_processes 수행 — "
            "시나리오 검증·마스킹 확인용. "
            "예: [{\"name\":\"java\",\"pid\":12345,\"pmem\":38.0,\"p100cpu\":12.0,"
            "\"args\":\"--password=secret -jar app.jar\"}]"
        ),
    )

    model_config = {"json_schema_extra": {
        "example": {
            "db_id": "polestar_cm_gp",
            "server_name": "svr-infra-001",
            "hostname": "svr-infra-001.internal",
            "ip_address": "10.1.2.3",
            "resource_ancestry": "/Servers/Infrastructure/svr-infra-001/Cpus",
            "alarm_id": "ALARM-20260604-001",
            "severity": 3,
            "alarm_status": "NOT_ACK",
            "resource_type": "server.Cpus",
            "resource_name": "svr-infra-001-CPU",
            "alarm_name": "CPU 사용률 임계 초과",
            "alarm_time": "20260604143520",
            "conditions": "사용률 Threashold [TROUBLE (> 90.0 %), ATTENTION (>80.0 %), CLEAR (< 70.0 %)]",
            "condition_log": "사용률 Threashold [95.2 % (> 90.0 %)]",
            "is_clear": False,
            "dry_run": True,
            "send_notification": False,
            "channels": None,
        }
    }}


class AlarmRawTestRequest(BaseModel):
    """폴스타 TCP 소켓 원문 메시지로 알람 분석을 실행하는 요청.

    폴스타에서 실제로 전송되는 단일행 JSON을 그대로 붙여넣어
    소켓 수신 → 파싱 → 분석 전체 파이프라인을 시뮬레이션한다.
    """

    message: str = Field(
        description=(
            "폴스타 TCP 소켓으로 전달된 단일행 JSON 원문. "
            "예: {\"dbId\":\"polestar_cm_gp\",\"serverName\":\"svr-infra-001\",...}"
        ),
    )
    dry_run: bool = Field(default=True, description="True: 발송 없이 분석 결과 + 미리보기만 반환")
    send_notification: bool = Field(default=False, description="dry_run=False일 때만 유효. 실제 채널 발송 여부")
    channels: Optional[list[str]] = Field(default=None, description="채널 오버라이드. null이면 서버 설정 사용")
    push_to_ui: bool = Field(default=True, description="True이면 분석 결과를 웹 UI에 실시간 표시")
    # ── Plan 47: 이력 패턴 분석 테스트 파라미터 ──
    query_history: bool = Field(
        default=True,
        description="폴스타 DB 이력 조회 수행 여부 (기본 True — 패턴 근거표 표시). simulated_history 지정 시 그쪽 우선",
    )
    simulated_history: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description=(
            "[{\"alarm_time\":\"yyyyMMddHHmmss\",\"severity\":3,"
            "\"alarm_status\":\"NOT_ACK\",\"resource_name\":\"...\"}] 형식. "
            "지정 시 DB 조회 대신 이 목록으로 통계 계산."
        ),
    )
    # ── Plan 47-1: 영향 프로세스 보강 테스트 파라미터 ──
    query_process: bool = Field(
        default=True,
        description="폴스타 프로세스 API 호출 여부 (hostname 기준, 기본 True). CPU/메모리 발생 알람만 실제 조회",
    )
    simulated_processes: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="지정 시 API 호출 없이 이 목록으로 select_top_processes 수행 (마스킹 확인용)",
    )

    model_config = {"json_schema_extra": {
        "example": {
            "message": (
                '{"dbId":"polestar_cm_gp","serverName":"svr-infra-001",'
                '"hostname":"svr-infra-001.internal","ipAddress":"10.1.2.3",'
                '"resourceAncestry":"/Servers/Infrastructure/svr-infra-001/Cpus",'
                '"alarmId":"1234567","severity":3,"alarmStatus":"NOT_ACK",'
                '"resourceType":"server.Cpus","alarmName":"CPU 사용률 임계 초과",'
                '"alarmTime":"20260602143520",'
                '"conditions":"사용률 Threashold [TROUBLE (> 90.0 %), ATTENTION (>80.0 %), CLEAR (< 70.0 %)]",'
                '"conditionLog":"사용률 Threashold [86.1 % (> 80.0 %)]"}'
            ),
            "dry_run": True,
            "send_notification": False,
            "channels": None,
            "push_to_ui": True,
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
    # ── Plan 47: 패턴 분석 ──
    pattern_type: Optional[str] = Field(
        default="",
        description="첫 발생 | 주기적 | 급증 | 산발적 (이력 분석 불가 시 빈 값)",
    )
    is_routine: Optional[bool] = Field(
        default=None,
        description="True=일상적 반복 알람, None=판단 불가",
    )
    pattern_analysis: Optional[str] = Field(
        default="",
        description="LLM 패턴 해석 (1~3문장)",
    )


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
    history_stats: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "이력 통계 요약 (Plan 47) — query_history=true 또는 simulated_history "
            "지정 시 채워짐. 통계 + 사전 분류(pre_classification) + source 포함."
        ),
    )
    process_snapshot: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "영향 프로세스 스냅샷 요약 (Plan 47-1) — query_process=true 또는 "
            "simulated_processes 지정 시 채워짐. 상위 N + 전체 건수 + 스냅샷 시각 + "
            "alarm_kind 포함. args는 마스킹된 값만 노출됨."
        ),
    )
    error: Optional[str] = Field(default=None, description="분석 실패 시 오류 메시지")
    processing_time_ms: float


# ─── 유틸 함수 ───────────────────────────────────────────────────────────────

_SEVERITY_LABELS = {0: "해소", 1: "주의", 2: "경고", 3: "심각"}


def _stats_to_dict(stats: AlarmHistoryStats) -> dict[str, Any]:
    """AlarmHistoryStats를 응답용 dict로 변환한다 (Plan 47 §5.9)."""
    return {
        "total_count": stats.total_count,
        "count_24h": stats.count_24h,
        "count_7d": stats.count_7d,
        "count_30d": stats.count_30d,
        "same_resource_count": stats.same_resource_count,
        "first_seen": stats.first_seen.isoformat() if stats.first_seen else None,
        "last_seen": stats.last_seen.isoformat() if stats.last_seen else None,
        "hour_histogram": {str(h): c for h, c in sorted(stats.hour_histogram.items())},
        "median_interval_minutes": stats.median_interval_minutes,
        "interval_cv": stats.interval_cv,
        "period_label": stats.period_label,
        "truncated": stats.truncated,
        "pre_classification": stats.pre_classification,
        "source": stats.source,
    }


def _process_to_dict(snapshot: ProcessSnapshot) -> dict[str, Any]:
    """ProcessSnapshot을 응답/UI push용 dict로 변환한다 (Plan 47-1 §5.6).

    args는 이미 마스킹된 값이므로 그대로 노출한다 (평문 인자는 ProcessInfo에 없음).
    """
    return {
        "alarm_kind": snapshot.alarm_kind,
        "captured_at": snapshot.captured_at.isoformat() if snapshot.captured_at else None,
        "total_count": snapshot.total_count,
        "source_host": snapshot.source_host,
        "top": [
            {
                "name": p.name,
                "pid": p.pid,
                "ppid": p.ppid,
                "user": p.user,
                "p100cpu": p.p100cpu,
                "pcpu": p.pcpu,
                "pmem": p.pmem,
                "rss": p.rss,
                "args": p.args,
            }
            for p in snapshot.top
        ],
    }


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
            from src.alarm.domain.process_rank import (
                classify_alarm_kind,
                select_top_processes,
            )

            kind = classify_alarm_kind(event)
            if kind is None:
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
            from src.alarm.application.nodes.alarm_context_enricher import (
                enrich_processes,
            )
            from src.alarm.infrastructure.polestar_process_api import (
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
            from src.alarm.domain.alarm_pattern import compute_history_stats

            entries = _simulated_entries(simulated_history)
            return compute_history_stats(
                event,
                entries,
                burst_threshold_24h=config.alarm.burst_threshold_24h,
                lookback_days=config.alarm.history_lookback_days,
                source="simulated",
            )
        if query_history:
            from src.alarm.application.nodes.alarm_context_enricher import enrich_history
            from src.alarm.infrastructure.polestar_history import (
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
    from src.alarm.application.nodes.alarm_notifier import build_workb_body

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
    from src.alarm.application.nodes.alarm_notifier import _process_payload

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
    )

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
    from src.alarm.application.nodes.alarm_analyzer import alarm_analyzer_node

    state: dict[str, Any] = {
        "alarm_event": event,
        "history_stats": history_stats,
        "process_snapshot": process_snapshot,
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

    # 5-1. 웹 UI 푸시 (push_to_ui=True일 때)
    if body.push_to_ui:
        await request.app.state.alarm_bus.publish({
            "type": "alarm_notification",
            "alarm_id": event.alarm_id,
            "severity": event.severity,
            "severity_label": analysis_result.severity_label,
            "alarm_name": event.alarm_name,
            "db_id": event.db_id,
            "server_name": event.server_name,
            "hostname": event.hostname,
            "ip_address": event.ip_address,
            "resource_type": event.resource_type,
            "resource_name": event.resource_name,
            "alarm_status": event.alarm_status,
            "summary": analysis_result.summary,
            "probable_cause": analysis_result.probable_cause,
            "recommended_action": analysis_result.recommended_action,
            # Plan 47: 패턴 분석 결과
            "pattern_type": analysis_result.pattern_type,
            "is_routine": analysis_result.is_routine,
            "pattern_analysis": analysis_result.pattern_analysis,
            # Plan 47: 패턴 근거 표 렌더용 — 이력 통계 원본 + 현재 알람 시각
            "alarm_time": event.alarm_time.isoformat(),
            "history_stats": _stats_to_dict(history_stats) if history_stats else None,
            # Plan 47-1: 영향 프로세스 표 렌더용 (args는 마스킹된 값)
            "process_snapshot": _process_to_dict(process_snapshot) if process_snapshot else None,
        })

    # 6. 발송 미리보기 생성 (dry_run 여부와 무관하게 항상 생성)
    preview = _build_notification_preview(config, analysis_result, channels, process_snapshot)

    # 7. 실제 발송 (dry_run=False + send_notification=True일 때만)
    notifications_sent: Optional[dict[str, bool]] = None
    if not body.dry_run and body.send_notification:
        from src.alarm.application.nodes.alarm_notifier import alarm_notifier_node

        notifier_state = {**result_state, "process_snapshot": process_snapshot, "error": None}
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
async def alarm_notifications_stream(request: Request) -> StreamingResponse:
    """분석된 알람 이벤트를 SSE로 브로드캐스트한다."""
    bus = request.app.state.alarm_bus
    q = bus.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield 'data: {"type":"ping"}\n\n'
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── 폴스타 원문 메시지 분석 엔드포인트 ──────────────────────────────────────

def _parse_raw_message(message: str) -> dict:
    """폴스타 단일행 JSON 원문을 파싱한다 (TcpReceiver._parse()와 동일한 로직)."""
    return json.loads(message.strip())


def _build_alarm_event_from_payload(payload: dict) -> AlarmEvent:
    """Redis Stream 페이로드(또는 파싱된 폴스타 JSON)를 AlarmEvent로 변환한다."""
    from datetime import datetime as _dt

    alarm_time_str = payload.get("alarmTime", "")
    try:
        alarm_time = _dt.strptime(alarm_time_str, "%Y%m%d%H%M%S") if alarm_time_str else _dt.now()
    except ValueError:
        alarm_time = _dt.now()

    alarm_status = payload.get("alarmStatus", "")
    severity = int(payload.get("severity", 0))
    # is_clear는 severity == 0 단독 기준 — alarmStatus는 ACK 상태로 무관 (Plan 47 §9)
    is_clear = severity == 0

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
        raw_payload=payload,
    )


@router.post(
    "/alarm/analyze-test/raw",
    response_model=AlarmTestResponse,
    summary="폴스타 원문 메시지 알람 분석 테스트",
    description=(
        "폴스타 TCP 소켓으로 전달되는 <b>단일행 JSON 원문</b>을 그대로 붙여넣어 분석합니다.<br/>"
        "소켓 수신 → JSON 파싱 → AlarmEvent 변환 → LLM 분석 전체 파이프라인을 시뮬레이션합니다.<br/>"
        "실제 폴스타 메시지를 복사해서 테스트할 때 사용하세요."
    ),
    tags=["alarm"],
)
async def analyze_alarm_raw(
    request: Request,
    body: AlarmRawTestRequest,
    current_user: dict = Depends(require_user),
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
            error=f"메시지 파싱 실패: {exc}",
            processing_time_ms=(time.time() - start_time) * 1000,
        )

    # 2. AlarmEvent 구성 (AlarmWorker._process()와 동일)
    event = _build_alarm_event_from_payload(payload)

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
    from src.alarm.application.nodes.alarm_analyzer import alarm_analyzer_node

    state: dict[str, Any] = {
        "alarm_event": event,
        "history_stats": history_stats,
        "process_snapshot": process_snapshot,
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

    analysis_result.notification_channels = channels

    # 5. 웹 UI 푸시
    if body.push_to_ui:
        await request.app.state.alarm_bus.publish({
            "type": "alarm_notification",
            "alarm_id": event.alarm_id,
            "severity": event.severity,
            "severity_label": analysis_result.severity_label,
            "alarm_name": event.alarm_name,
            "db_id": event.db_id,
            "server_name": event.server_name,
            "hostname": event.hostname,
            "ip_address": event.ip_address,
            "resource_type": event.resource_type,
            "resource_name": event.resource_name,
            "alarm_status": event.alarm_status,
            "summary": analysis_result.summary,
            "probable_cause": analysis_result.probable_cause,
            "recommended_action": analysis_result.recommended_action,
            # Plan 47: 패턴 분석 결과
            "pattern_type": analysis_result.pattern_type,
            "is_routine": analysis_result.is_routine,
            "pattern_analysis": analysis_result.pattern_analysis,
            # Plan 47: 패턴 근거 표 렌더용 — 이력 통계 원본 + 현재 알람 시각
            "alarm_time": event.alarm_time.isoformat(),
            "history_stats": _stats_to_dict(history_stats) if history_stats else None,
            # Plan 47-1: 영향 프로세스 표 렌더용 (args는 마스킹된 값)
            "process_snapshot": _process_to_dict(process_snapshot) if process_snapshot else None,
        })

    # 6. 발송 미리보기
    preview = _build_notification_preview(config, analysis_result, channels, process_snapshot)

    # 7. 실제 발송 (dry_run=False + send_notification=True)
    notifications_sent: Optional[dict[str, bool]] = None
    if not body.dry_run and body.send_notification:
        from src.alarm.application.nodes.alarm_notifier import alarm_notifier_node

        notifier_state = {**result_state, "process_snapshot": process_snapshot, "error": None}
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
