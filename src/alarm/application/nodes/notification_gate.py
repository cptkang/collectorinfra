"""알람 발송 판단 게이트 노드 (Plan 52 Phase E1).

alarm_analyzer 다음에 위치하여, 결정적 정책 함수 `decide_notification`으로 4-티어
(PAGE/TICKET/DASHBOARD/SUPPRESS) 발송 판단을 산출하고 감사 저장소(decision_store)에
기록한다. LLM은 호출하지 않는다(순수 규칙).

설계 안전장치:
    - enable_noise_gate=False면 그래프에 노드 자체가 포함되지 않으나, 방어적으로 노드
      진입부에서도 재확인하여 게이트 비활성 시 결정을 만들지 않는다(회귀 0).
    - 감사 기록(store.record) 실패는 발송을 막지 않는다(graceful — warning 후 진행).
    - analysis_result가 없거나 error 상태면 결정을 만들지 않는다.

계층: application → domain(notification_policy.decide_notification) 단방향 의존만 사용한다.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from src.alarm.domain.notification_policy import decide_notification

logger = logging.getLogger(__name__)


async def notification_gate_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """발송 판단(NotificationDecision)을 산출하여 state.notification_decision을 채운다.

    Args:
        state: LangGraph 상태 딕셔너리 (alarm_event/analysis_result/history_stats/
            noise_context/self_heal 사용).
        config: LangGraph configurable 설정 (app_config 필수, decision_store 선택).

    Returns:
        {"notification_decision": NotificationDecision} 또는 빈 dict
        (분석 없음/error/게이트 비활성/설정 미주입 시 빈 dict).
    """
    result = state.get("analysis_result")
    if not result or state.get("error"):
        return {}

    configurable = (config or {}).get("configurable", {})
    cfg = configurable.get("app_config")
    if cfg is None:
        return {}

    gate_cfg = getattr(cfg, "noise_gate", None)
    if gate_cfg is None or not gate_cfg.enable_noise_gate:
        return {}  # 안전장치 — 게이트 off면 결정하지 않음 (회귀 0)

    event = state["alarm_event"]
    decision = decide_notification(
        event,
        state.get("history_stats"),
        result,
        state.get("noise_context"),
        gate_cfg,
        self_heal=bool(state.get("self_heal", False)),
        inhibited=bool(state.get("inhibited", False)),
        flapping=bool(state.get("flapping", False)),
        storm=bool(state.get("storm", False)),
    )

    store = configurable.get("decision_store")
    if store is not None:
        try:
            store.record(decision, alarm_id=event.alarm_id)
        except Exception:  # noqa: BLE001 — 기록 실패가 발송을 막지 않는다
            logger.warning("발송 판단 감사 기록 실패(무시): alarm_id=%s", event.alarm_id)

    logger.info(
        "발송 판단: alarm_id=%s tier=%s reason=%s",
        event.alarm_id,
        decision.tier,
        decision.reason,
    )
    return {"notification_decision": decision}
