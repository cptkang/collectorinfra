"""알람 알림 발송 노드.

AlarmAnalysisResult의 notification_channels에 따라 채널별로 순차 발송한다.
각 채널의 성공/실패를 notifications_sent에 개별 기록한다.

현재 지원 채널:
    workb — KB One 사내메신저 쪽지 발송

추후 추가 예정:
    email — 사내 SMTP 이메일
    webhook — Generic Webhook (내부 시스템 연동)
"""

from __future__ import annotations

import html
import logging
from typing import Any

import httpx
from langchain_core.runnables import RunnableConfig

from typing import Optional

from src.alarm.domain.alarm import AlarmAnalysisResult, ProcessSnapshot
from src.alarm.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_SUPPRESS,
    TIER_TICKET,
)

logger = logging.getLogger(__name__)

_SEVERITY_COLORS = {0: "#28a745", 1: "#ffc107", 2: "#fd7e14", 3: "#dc3545"}

# 발송하지 않는 티어(§7) — PAGE/미상 티어는 기존 발송 경로로 폴백(보수적, 재현율 우선)
_NON_PAGE_TIERS = frozenset({TIER_TICKET, TIER_DASHBOARD, TIER_SUPPRESS})


def _pattern_badge(result: AlarmAnalysisResult) -> str:
    """패턴 분석 배지 텍스트를 생성한다 (예: "[주기적 · 일상 알람]")."""
    if result.is_routine is True:
        return f"[{result.pattern_type} · 일상 알람]"
    if result.is_routine is False:
        return f"[{result.pattern_type} · 확인 필요]"
    return f"[{result.pattern_type}]"


def _process_table_html(snapshot: ProcessSnapshot) -> str:
    """영향 프로세스 텍스트 표(HTML) — workb 본문용 (Plan 47-1 §5.6).

    수치는 결정적으로 선별된 값, args는 마스킹된 값만 사용한다.
    """
    if not snapshot.top:
        return ""
    metric = "메모리" if snapshot.alarm_kind == "memory" else "CPU"
    captured = (
        f" ({snapshot.captured_at:%Y-%m-%d %H:%M:%S} 기준)"
        if snapshot.captured_at is not None
        else ""
    )
    rows = "".join(
        f"<tr><td>{i}. {p.name} (pid {p.pid})</td>"
        f"<td>CPU {p.p100cpu:.1f}% · 메모리 {p.pmem:.1f}%</td></tr>"
        # 실행 파라미터(args, 마스킹됨) — 행 아래 전체폭 보조 줄 (서비스 추적용)
        + (
            f'<tr><td colspan="2" style="color:#6c757d;font-size:0.85em;'
            f'word-break:break-all">{html.escape(p.args)}</td></tr>'
            if p.args
            else ""
        )
        for i, p in enumerate(snapshot.top, start=1)
    )
    return (
        f"<br><br><b>영향 프로세스 — {metric} 상위 "
        f"(전체 {snapshot.total_count}개{captured})</b>"
        f"<table>{rows}</table>"
    )


def build_workb_body(
    result: AlarmAnalysisResult,
    process_snapshot: Optional[ProcessSnapshot] = None,
) -> str:
    """WorkB 쪽지 본문을 HTML 형식으로 생성한다."""
    ev = result.alarm_event
    color = _SEVERITY_COLORS.get(ev.severity, "#6c757d")
    severity_html = f'<span style="color:{color};font-weight:bold">{result.severity_label}</span>'
    body = (
        f"<b>심각도:</b> {severity_html}<br>"
        f"<b>알람명:</b> {ev.alarm_name}<br>"
        f"<b>서버:</b> {ev.server_name} ({ev.hostname}, {ev.ip_address})<br>"
        f"<b>자원 경로:</b> {ev.resource_ancestry}<br>"
        f"<b>자원 종류:</b> {ev.resource_type}<br>"
        f"<b>자원 이름:</b> {ev.resource_name}<br>"
        f"<b>알람 상태:</b> {ev.alarm_status}<br>"
        f"<b>임계 조건:</b> {ev.conditions}<br>"
        f"<b>조건 로그:</b> {ev.condition_log}"
        f"<hr>"
        f"<b>요약</b><br>{result.summary}<br><br>"
        f"<b>추정 원인</b><br>{result.probable_cause}<br><br>"
        f"<b>권고 조치</b><br>{result.recommended_action}"
    )
    # Plan 47-1: 영향 프로세스 표 (스냅샷 없으면 생략)
    # — 패턴 분석보다 먼저 출력: "무엇이 문제인가 → 얼마나 잦은가" 순이 자연스러움
    if process_snapshot is not None:
        body += _process_table_html(process_snapshot)
    # Plan 47: 패턴 분석 섹션 (pattern_type=""이면 생략)
    if result.pattern_type:
        body += (
            f"<br><br><b>패턴 분석</b><br>"
            f"{_pattern_badge(result)} {result.pattern_analysis}"
        )
    return body


async def alarm_notifier_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """분석 결과를 알림 채널에 발송한다.

    analysis_result가 없거나 error 상태이면 아무 작업 없이 반환한다.

    Args:
        state: LangGraph 상태 딕셔너리
        config: LangGraph configurable 설정 (app_config 필드 필수)

    Returns:
        analysis_result 업데이트 (notifications_sent 갱신)
    """
    result: AlarmAnalysisResult | None = state.get("analysis_result")
    if not result or state.get("error"):
        return {}

    cfg = config["configurable"]["app_config"]
    process_snapshot: Optional[ProcessSnapshot] = state.get("process_snapshot")

    # ── Plan 52: 4-티어 라우팅 (게이트 활성 시에만 decision 존재) ──
    # decision is None(게이트 off) → 아래 기존 발송 경로 그대로(무변경).
    # decision 존재 + TICKET/DASHBOARD/SUPPRESS → 발송하지 않고 로그만(감사는 gate가 기록).
    # decision 존재 + PAGE(또는 미상 티어) → 아래 기존 발송 경로로 폴백(보수적 PAGE).
    decision = state.get("notification_decision")
    if decision is not None and decision.tier in _NON_PAGE_TIERS:
        configurable = (config or {}).get("configurable", {})
        ticket_queue = configurable.get("ticket_queue")
        alarm_bus = configurable.get("alarm_bus")
        # (E3 후속) 워커 경로 SSE Redis pub/sub 발행기 — alarm_bus 미주입 시에만 사용.
        sse_publisher = configurable.get("sse_publisher")
        await _route_non_page_tier(
            result, decision, ticket_queue, alarm_bus, sse_publisher
        )
        return {"analysis_result": result}

    for channel in result.notification_channels:
        try:
            if channel == "workb":
                await _send_workb(cfg.workb, result, process_snapshot)
            elif channel == "webhook":
                await _send_webhook(cfg.alarm, result, process_snapshot)
            else:
                # 현재 worKB 외 채널 미지원
                # 추후 Slack/email 채널 추가 시 여기에 분기 추가
                logger.warning(
                    "지원하지 않는 알림 채널 무시: %s (현재 지원: workb, webhook)",
                    channel,
                )
                result.notifications_sent[channel] = False
                continue
            result.notifications_sent[channel] = True
            logger.info(
                "알람 알림 발송 완료: alarm_id=%s channel=%s",
                result.alarm_event.alarm_id,
                channel,
            )
        except Exception:
            result.notifications_sent[channel] = False
            logger.exception(
                "알람 알림 발송 실패: alarm_id=%s channel=%s",
                result.alarm_event.alarm_id,
                channel,
            )

    return {"analysis_result": result}


def _tier_sse_payload(result: AlarmAnalysisResult, decision) -> dict:  # noqa: ANN001
    """티어 라우팅용 SSE 이벤트 payload를 생성한다(§7 · Phase E3).

    기존 `/alarm/notifications/stream`·analyze 경로의 publish 형식(alarm 필드 + 분석 결과)을
    그대로 따르고, 티어/근거(tier·tier_reason)를 추가해 일관성을 유지한다.
    """
    ev = result.alarm_event
    return {
        "type": "alarm_notification",
        "alarm_id": ev.alarm_id,
        "severity": ev.severity,
        "severity_label": result.severity_label,
        "alarm_name": ev.alarm_name,
        "db_id": ev.db_id,
        "server_name": ev.server_name,
        "hostname": ev.hostname,
        "ip_address": ev.ip_address,
        "resource_type": ev.resource_type,
        "resource_name": ev.resource_name,
        "alarm_status": ev.alarm_status,
        "summary": result.summary,
        "probable_cause": result.probable_cause,
        "recommended_action": result.recommended_action,
        "pattern_type": result.pattern_type,
        "is_routine": result.is_routine,
        "pattern_analysis": result.pattern_analysis,
        # ── Phase E3: 4-티어 라우팅 메타데이터 ──
        "tier": decision.tier,
        "tier_reason": decision.reason,
    }


async def _route_non_page_tier(
    result: AlarmAnalysisResult,
    decision,  # noqa: ANN001 — NotificationDecision
    ticket_queue,  # noqa: ANN001 — TicketBatchQueue | None (덕 타이핑)
    alarm_bus,  # noqa: ANN001 — AlarmNotificationBus | None (덕 타이핑)
    sse_publisher=None,  # noqa: ANN001 — RedisSseBridgePublisher | None (덕 타이핑)
) -> None:
    """PAGE 외 티어(TICKET/DASHBOARD/SUPPRESS)를 라우팅한다(발송 안 함, §7 · Phase E3).

    감사 기록(tier·reason·signals)은 notification_gate가 decision_store에 이미 적재했으므로
    여기서는 큐 적재/SSE만 수행한다(중복 적재 금지). 모든 부수효과는 graceful —
    큐/SSE 실패는 warning 후 무시하여 파이프라인을 막지 않는다.

    - TICKET: 일배치 요약 큐 적재(ticket_queue 있으면) + DASHBOARD와 동일하게 SSE 표시.
    - DASHBOARD: SSE(alarm_bus 또는 sse_publisher 있으면)로 UI에만 표시.
    - SUPPRESS: 발송·큐·SSE 모두 없음 — 로그만.

    alarm_bus는 API 경로(app.state.alarm_bus)에서만 주입된다. 워커 경로(cross-process)는
    alarm_bus를 공유할 수 없어 대신 sse_publisher(Redis pub/sub 브리지, E3 후속·D-048.9)를
    주입받는다. 둘 다 None이면 로그 폴백한다(워커 정상 동작).
    """
    alarm_id = result.alarm_event.alarm_id
    if decision.tier == TIER_TICKET:
        if ticket_queue is not None:
            try:
                ticket_queue.enqueue(decision, alarm_id=alarm_id)
            except Exception:  # noqa: BLE001 — 큐 적재 실패가 파이프라인을 막지 않는다
                logger.warning("TICKET 일배치 큐 적재 실패(무시): alarm_id=%s", alarm_id)
        await _publish_tier_sse(result, decision, alarm_bus, sse_publisher)
        logger.info(
            "TICKET(저우선 — 일배치 큐 적재 + SSE 표시, 감사 기록됨): alarm_id=%s reason=%s",
            alarm_id,
            decision.reason,
        )
    elif decision.tier == TIER_DASHBOARD:
        await _publish_tier_sse(result, decision, alarm_bus, sse_publisher)
        logger.info(
            "DASHBOARD(UI 표시만 — SSE, 발송 안 함): alarm_id=%s reason=%s",
            alarm_id,
            decision.reason,
        )
    else:  # TIER_SUPPRESS
        logger.info(
            "SUPPRESS(미통보 — 감사 기록만): alarm_id=%s reason=%s",
            alarm_id,
            decision.reason,
        )


async def _publish_tier_sse(
    result: AlarmAnalysisResult,
    decision,  # noqa: ANN001 — NotificationDecision
    alarm_bus,  # noqa: ANN001 — AlarmNotificationBus | None
    sse_publisher=None,  # noqa: ANN001 — RedisSseBridgePublisher | None
) -> None:
    """티어 SSE 이벤트를 publish한다(대상 없으면 로그 폴백, 실패는 graceful).

    이중 발행 방지: API 경로의 alarm_bus를 우선 사용하고, 미주입(워커 경로)일 때만
    sse_publisher(Redis 브리지)를 사용한다 — 둘 중 하나만 호출된다(E3 후속·D-048.9).
    """
    target = alarm_bus if alarm_bus is not None else sse_publisher
    if target is None:
        logger.info(
            "SSE 미주입(로그 폴백): alarm_id=%s tier=%s",
            result.alarm_event.alarm_id,
            decision.tier,
        )
        return
    try:
        await target.publish(_tier_sse_payload(result, decision))
    except Exception:  # noqa: BLE001 — SSE 실패가 파이프라인을 막지 않는다
        logger.warning(
            "티어 SSE publish 실패(무시): alarm_id=%s tier=%s",
            result.alarm_event.alarm_id,
            decision.tier,
        )


async def _send_workb(
    workb_cfg,
    result: AlarmAnalysisResult,
    process_snapshot: Optional[ProcessSnapshot] = None,
) -> None:
    """worKB 사내메신저 쪽지 발송.

    실제 쪽지 제목: "{alias} {msgTitle}" 형태로 사용자 쪽지창에 표시된다.
    예: "[인프라알람] [심각] svr-infra-001 (svr-infra-001.internal)"

    Args:
        workb_cfg: WorkbConfig 인스턴스
        result: 알람 분석 결과
        process_snapshot: 영향 프로세스 스냅샷 (Plan 47-1, None이면 표 생략)
    """
    if not workb_cfg.base_url:
        raise ValueError("WORKB_BASE_URL이 설정되지 않았습니다.")

    ev = result.alarm_event
    msg_title = f"[{result.severity_label}] {ev.server_name} ({ev.hostname})"
    msg_body = build_workb_body(result, process_snapshot)
    payload = {
        "systemDiv": workb_cfg.system_div,
        "msgTitle": msg_title,
        "msgBody": msg_body,
        "sendId": workb_cfg.send_id,
        "userIds": workb_cfg.get_user_ids(ev.severity),
        "alias": workb_cfg.alias,
    }
    headers = {
        "Authorization": f"Bearer {workb_cfg.bearer_token}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    url = f"{workb_cfg.base_url.rstrip('/')}/api/sendWorkbMsg"
    async with httpx.AsyncClient(timeout=workb_cfg.timeout_seconds) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()


def _process_payload(snapshot: Optional[ProcessSnapshot]) -> Optional[dict]:
    """webhook payload용 프로세스 스냅샷 dict (Plan 47-1). args는 마스킹된 값만 포함."""
    if snapshot is None:
        return None
    return {
        "alarm_kind": snapshot.alarm_kind,
        "captured_at": snapshot.captured_at.isoformat() if snapshot.captured_at else None,
        "total_count": snapshot.total_count,
        "source_host": snapshot.source_host,
        "top": [
            {
                "name": p.name, "pid": p.pid, "user": p.user,
                "p100cpu": p.p100cpu, "pcpu": p.pcpu, "pmem": p.pmem,
                "rss": p.rss, "args": p.args,
            }
            for p in snapshot.top
        ],
    }


async def _send_webhook(
    alarm_cfg,
    result: AlarmAnalysisResult,
    process_snapshot: Optional[ProcessSnapshot] = None,
) -> None:
    """Generic Webhook 발송.

    내부 시스템 간 연동용 HTTP 콜백 채널.
    ALARM_WEBHOOK_URL이 비어있으면 ValueError를 발생시킨다.

    Args:
        alarm_cfg: AlarmConfig 인스턴스
        result: 알람 분석 결과
        process_snapshot: 영향 프로세스 스냅샷 (Plan 47-1, None이면 payload에 null)
    """
    url = alarm_cfg.webhook_url
    if not url:
        raise ValueError("ALARM_WEBHOOK_URL이 설정되지 않았습니다.")

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
        # Plan 47: 패턴 분석 결과
        "pattern_type": result.pattern_type,
        "is_routine": result.is_routine,
        "pattern_analysis": result.pattern_analysis,
        # Plan 47-1: 영향 프로세스 스냅샷 (args 마스킹됨)
        "process_snapshot": _process_payload(process_snapshot),
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Alarm-Source": "collectorinfra",
    }
    timeout = alarm_cfg.webhook_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
