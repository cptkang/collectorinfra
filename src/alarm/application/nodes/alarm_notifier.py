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

import logging
from typing import Any

import httpx
from langchain_core.runnables import RunnableConfig

from src.alarm.domain.alarm import AlarmAnalysisResult

logger = logging.getLogger(__name__)

_SEVERITY_COLORS = {0: "#28a745", 1: "#ffc107", 2: "#fd7e14", 3: "#dc3545"}


def build_workb_body(result: AlarmAnalysisResult) -> str:
    """WorkB 쪽지 본문을 HTML 형식으로 생성한다."""
    ev = result.alarm_event
    color = _SEVERITY_COLORS.get(ev.severity, "#6c757d")
    severity_html = f'<span style="color:{color};font-weight:bold">{result.severity_label}</span>'
    return (
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

    for channel in result.notification_channels:
        try:
            if channel == "workb":
                await _send_workb(cfg.workb, result)
            elif channel == "webhook":
                await _send_webhook(cfg.alarm, result)
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


async def _send_workb(workb_cfg, result: AlarmAnalysisResult) -> None:
    """worKB 사내메신저 쪽지 발송.

    실제 쪽지 제목: "{alias} {msgTitle}" 형태로 사용자 쪽지창에 표시된다.
    예: "[인프라알람] [심각] svr-infra-001 (svr-infra-001.internal)"

    Args:
        workb_cfg: WorkbConfig 인스턴스
        result: 알람 분석 결과
    """
    if not workb_cfg.base_url:
        raise ValueError("WORKB_BASE_URL이 설정되지 않았습니다.")

    ev = result.alarm_event
    msg_title = f"[{result.severity_label}] {ev.server_name} ({ev.hostname})"
    msg_body = build_workb_body(result)
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


async def _send_webhook(alarm_cfg, result: AlarmAnalysisResult) -> None:
    """Generic Webhook 발송.

    내부 시스템 간 연동용 HTTP 콜백 채널.
    ALARM_WEBHOOK_URL이 비어있으면 ValueError를 발생시킨다.

    Args:
        alarm_cfg: AlarmConfig 인스턴스
        result: 알람 분석 결과
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
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Alarm-Source": "collectorinfra",
    }
    timeout = alarm_cfg.webhook_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
