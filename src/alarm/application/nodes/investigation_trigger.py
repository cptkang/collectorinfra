"""자동 조사 트리거 노드 (Plan 64 §0.2 CW-A · Plan 60 §14.2 · sre-agent/05 §3).

`notification_gate`가 최종 `NotificationDecision`을 산출한 **직후**(그래프상 gate 다음, notifier
이전)에 위치한다. tier가 `investigation_trigger_min_tier`(기본 PAGE) 이상이면 sre_agent 조사
서비스에 트리거 페이로드(`contract_version: "1"`)를 submit하고, **전체 타임아웃 가드** 안에서
poll하여 브리핑을 받아 state에 실어 notifier가 통보에 첨부하게 한다. 결과(investigation_id·
status·verdict)는 decision_store에 감사한다.

설계 안전장치(회귀 0·비차단·graceful):
    - 게이트 판정·라우팅 무변경 — 이 노드는 gate 노드를 건드리지 않고 gate 다음에 삽입되며,
      decision을 읽기만 한다(gate의 <10s 예산에 무영향, 별도 노드).
    - investigation_trigger_enabled=False면 그래프에 노드 자체가 배선되지 않으나, 방어적으로
      진입부에서도 재확인하여 트리거를 만들지 않는다(회귀 0).
    - 전체 타임아웃 가드는 submit+poll 시퀀스 전체에 씌운다(per-call 아님 — 폴링 루프가
      SSE 스트리밍으로 무력화되는 것을 방지, §3.2 Known Mistakes).
    - 서비스 다운/타임아웃/거부/파싱 실패 시 브리핑 미첨부로 graceful — 게이트 통보·판정은 정상
      완료하고, 트리거만 사유를 구조화해 감사에 남긴다(침묵 폴백 금지).

계층: application → domain(investigation_payload/notification_policy) + configurable로 주입된
infrastructure(sre_agent_client·decision_store) 소비. 다른 노드·graph를 import하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from src.alarm.domain.investigation_payload import build_trigger_payload
from src.alarm.domain.notification_policy import _TIER_RANK, TIER_PAGE

logger = logging.getLogger(__name__)

# poll이 종결로 간주하는 상태(sre-agent/05 §3 · JobStore TERMINAL_STATUSES + not_found).
# 그 외(accepted/running)는 재조회한다. 전체 루프는 상위 asyncio.wait_for가 유계로 만든다.
_TERMINAL_POLL_STATUSES: frozenset[str] = frozenset(
    {"done", "failed", "timeout", "stub", "rejected", "not_found"}
)


async def investigation_trigger_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """PAGE 결정 직후 조사를 트리거하고 브리핑을 state에 싣는다.

    Args:
        state: LangGraph 상태(alarm_event/notification_decision/recurrence/correlation_meta 사용).
        config: LangGraph configurable(app_config·sre_agent_client·decision_store).

    Returns:
        {"investigation_briefing": <dict>} (브리핑 수신 시) 또는 빈 dict
        (비활성/티어 미달/클라이언트 미주입/거부·타임아웃·실패 시 — graceful).
    """
    decision = state.get("notification_decision")
    if decision is None:
        return {}

    configurable = (config or {}).get("configurable", {})
    cfg = configurable.get("app_config")
    if cfg is None:
        return {}
    gate_cfg = getattr(cfg, "noise_gate", None)
    if gate_cfg is None or not getattr(gate_cfg, "investigation_trigger_enabled", False):
        return {}  # 안전장치 — off면 트리거하지 않음(회귀 0)

    # 티어 게이트 — enrichment_min_tier와 동일 규칙(대소문자 무시).
    min_tier = getattr(gate_cfg, "investigation_trigger_min_tier", TIER_PAGE)
    min_rank = _TIER_RANK.get((min_tier or TIER_PAGE).lower(), _TIER_RANK[TIER_PAGE])
    if _TIER_RANK.get(decision.tier, _TIER_RANK[TIER_PAGE]) < min_rank:
        return {}  # 티어 미달 — 트리거하지 않음

    client = configurable.get("sre_agent_client")
    if client is None:
        return {}  # 클라이언트 미주입(빌드 실패/off) → graceful no-op

    event = state["alarm_event"]
    signals = getattr(decision, "signals", None) or {}
    payload = build_trigger_payload(
        event,
        decision,
        recurrence=state.get("recurrence"),
        correlation_meta=state.get("correlation_meta"),
        root_resource=signals.get("root_resource"),
    )

    total_timeout = float(
        getattr(gate_cfg, "investigation_total_timeout_seconds", 45.0)
    )
    briefing: Optional[dict] = None
    status = "error"
    investigation_id: Optional[str] = None
    verdict: Any = None  # ImportanceVerdict(dict) 또는 문자열 — 반환 실측 방어(§5.1)
    try:
        briefing, status, investigation_id, verdict = await asyncio.wait_for(
            _submit_and_poll(client, payload, gate_cfg), timeout=total_timeout
        )
    except asyncio.TimeoutError:
        status = "timeout"
        logger.warning(
            "조사 트리거 전체 타임아웃(%.1fs 초과·게이트 무영향): alarm_id=%s",
            total_timeout, getattr(event, "alarm_id", ""),
        )
    except Exception:  # noqa: BLE001 — 서비스 다운/통신 실패도 게이트를 막지 않는다
        status = "down"
        logger.warning(
            "조사 트리거 실패(graceful·게이트 무영향): alarm_id=%s",
            getattr(event, "alarm_id", ""), exc_info=True,
        )

    _audit(configurable.get("decision_store"), event, decision, investigation_id, status, verdict)

    result: dict[str, Any] = {}
    if briefing is not None:
        result["investigation_briefing"] = briefing
    # (Plan 64 CW-C) escalate-only 후속 통보 승격 — fault_escalation_enabled + verdict.escalate일
    # 때만 상향 안내 블록을 state에 싣는다(notifier가 통보에 첨부). 게이트 판정(tier/routing/
    # decision)은 소급 변경·하향하지 않는다(§5.1). off/미escalate면 미첨부 → 통보 비트동일.
    if getattr(gate_cfg, "fault_escalation_enabled", False):
        escalation = _build_escalation(verdict)
        if escalation is not None:
            result["investigation_escalation"] = escalation
    return result


async def _submit_and_poll(
    client,  # noqa: ANN001 — SreAgentClient (덕 타이핑, configurable 주입)
    payload: dict,
    gate_cfg,  # noqa: ANN001 — NoiseGateConfig (덕 타이핑)
) -> tuple[Optional[dict], str, Optional[str], Any]:
    """조사 잡을 submit하고 종결까지 poll한다(연결/해제 포함, 상위 wait_for가 전체 유계).

    Returns:
        (briefing, status, investigation_id, verdict). verdict는 구조화 ImportanceVerdict
        (dict) 또는 문자열일 수 있다(§5.1 · sre-agent/05 §3 반환 방어).
        - rejected: (None, "rejected", id?, reason).
        - 종결(done/failed/stub/…): (briefing, status, id, verdict).
    """
    poll_interval = float(
        getattr(gate_cfg, "investigation_poll_interval_seconds", 1.0)
    )
    await client.connect()
    try:
        sub = await client.submit(payload)
        sub_status = sub.get("status")
        investigation_id = sub.get("investigation_id")
        if sub_status == "rejected":
            # 필수 필드 결측 등 계약 위반 — 조사 미수행, 사유를 verdict로 노출(침묵 금지).
            return None, "rejected", investigation_id, sub.get("reason")
        if not investigation_id:
            return None, "error", None, None
        # submit이 accepted/duplicate면 동일 잡을 poll한다(duplicate는 기존 조사 재사용 — dedup).
        while True:
            res = await client.poll(investigation_id)
            poll_status = res.get("status")
            if poll_status in _TERMINAL_POLL_STATUSES:
                return (
                    res.get("briefing"),
                    poll_status,
                    investigation_id,
                    res.get("verdict"),
                )
            await asyncio.sleep(poll_interval)
    finally:
        await client.disconnect()


def _audit(
    store,  # noqa: ANN001 — DecisionStore | None (덕 타이핑)
    event,  # noqa: ANN001 — AlarmEvent
    decision,  # noqa: ANN001 — NotificationDecision
    investigation_id: Optional[str],
    status: str,
    verdict: Optional[str],
) -> None:
    """조사 트리거 결과를 decision_store에 감사한다(미주입·실패는 graceful)."""
    if store is None:
        return
    try:
        store.record_investigation(
            alarm_id=getattr(event, "alarm_id", ""),
            fingerprint=getattr(decision, "fingerprint", ""),
            investigation_id=investigation_id,
            status=status,
            verdict=verdict,
        )
    except Exception:  # noqa: BLE001 — 감사 실패가 통보를 막지 않는다
        logger.warning(
            "조사 트리거 감사 기록 실패(무시): alarm_id=%s",
            getattr(event, "alarm_id", ""),
        )


def _verdict_escalates(verdict: Any) -> bool:
    """poll verdict가 상향(escalate)을 지시하는지 판정한다 (Plan 64 CW-C · §5.1).

    구조화 ImportanceVerdict(dict — sre-agent/02 §6)이면 `escalate` 불리언을, 문자열이면
    'escalate' 여부를 본다(sre_get_investigation 반환 실측 방어 — sre-agent/05 §3).
    그 외 타입/None이면 상향 아님.
    """
    if isinstance(verdict, dict):
        return verdict.get("escalate") is True
    if isinstance(verdict, str):
        return verdict.strip().lower() == "escalate"
    return False


def _build_escalation(verdict: Any) -> Optional[dict]:
    """escalate=True인 verdict에서 통보 승격 안내 블록 데이터를 만든다(아니면 None).

    **escalate-only** — 게이트 판정(tier/routing/decision)은 소급 변경·하향하지 않고,
    상향 신호만 통보에 첨부할 데이터를 반환한다(Plan 64 §5.1 역방향 계약). 구조화 verdict면
    level/confidence/signals를 함께 실어 근거를 노출한다(문자열이면 escalate 플래그만).
    """
    if not _verdict_escalates(verdict):
        return None
    escalation: dict[str, Any] = {"escalate": True}
    if isinstance(verdict, dict):
        for key in ("level", "confidence", "signals"):
            val = verdict.get(key)
            if val not in (None, "", [], {}):
                escalation[key] = val
    return escalation
