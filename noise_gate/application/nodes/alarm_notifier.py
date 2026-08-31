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

import asyncio
import html
import logging
from datetime import datetime
from typing import Any

import httpx
from langchain_core.runnables import RunnableConfig

from typing import Optional

from noise_gate.domain.alarm import (
    AlarmAnalysisResult,
    MessageEnrichment,
    ProcessSnapshot,
)
from noise_gate.domain.enrichment_profile import build_summary
from noise_gate.domain.investigation_payload import build_escalation
from noise_gate.domain.notification_policy import (
    _TIER_RANK,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)

logger = logging.getLogger(__name__)

_SEVERITY_COLORS = {0: "#28a745", 1: "#ffc107", 2: "#fd7e14", 3: "#dc3545"}

# 발송하지 않는 티어(§7) — PAGE/미상 티어는 기존 발송 경로로 폴백(보수적, 재현율 우선)
_NON_PAGE_TIERS = frozenset({TIER_TICKET, TIER_DASHBOARD, TIER_SUPPRESS})

# (Plan 66 3-E) 후속 브리핑 발송 태스크 참조 보관소. asyncio는 태스크를 약참조로만 들고 있어
# 지역 변수로 두면 GC가 실행 중인 태스크를 수거할 수 있다 — 완료 시 discard로 자동 정리한다.
_FOLLOWUP_TASKS: set[asyncio.Task] = set()

# poll이 종결로 간주하는 상태(sre-agent/05 §3 · investigation_trigger와 동일 계약).
_TERMINAL_POLL_STATUSES: frozenset[str] = frozenset(
    {"done", "failed", "timeout", "stub", "rejected", "not_found"}
)


def _pattern_badge(result: AlarmAnalysisResult) -> str:
    """패턴 분석 배지 텍스트를 생성한다 (예: "[주기적 · 일상 알람]")."""
    if result.is_routine is True:
        return f"[{result.pattern_type} · 일상 알람]"
    if result.is_routine is False:
        return f"[{result.pattern_type} · 확인 필요]"
    return f"[{result.pattern_type}]"


def _process_rows_html(top: list) -> str:
    """프로세스 행(HTML)을 조립한다 — 프로세스 표·보강 블록 공용 (Plan 47-1 §5.6 · E6).

    수치는 결정적으로 선별된 값, args는 마스킹된 값만 사용한다.
    """
    return "".join(
        f"<tr><td>{i}. {p.name} (pid {p.pid})</td>"
        f"<td>CPU {p.p100cpu:.1f}% · 메모리 {p.pmem:.1f}%</td></tr>"
        # 실행 파라미터(args, 마스킹됨) — 행 아래 전체폭 보조 줄 (서비스 추적용)
        + (
            f'<tr><td colspan="2" style="color:#6c757d;font-size:0.85em;'
            f'word-break:break-all">{html.escape(p.args)}</td></tr>'
            if p.args
            else ""
        )
        for i, p in enumerate(top, start=1)
    )


def _process_table_html(snapshot: ProcessSnapshot) -> str:
    """영향 프로세스 텍스트 표(HTML) — workb 본문용 (Plan 47-1 §5.6).

    수치는 결정적으로 선별된 값, args는 마스킹된 값만 사용한다. cpu/memory 알람의
    기존 출력을 비트 동일하게 유지한다(Plan 60 E6는 이 표를 변경하지 않는다).
    """
    if not snapshot.top:
        return ""
    metric = "메모리" if snapshot.alarm_kind == "memory" else "CPU"
    captured = (
        f" ({snapshot.captured_at:%Y-%m-%d %H:%M:%S} 기준)"
        if snapshot.captured_at is not None
        else ""
    )
    rows = _process_rows_html(snapshot.top)
    return (
        f"<br><br><b>영향 프로세스 — {metric} 상위 "
        f"(전체 {snapshot.total_count}개{captured})</b>"
        f"<table>{rows}</table>"
    )


def _enrichment_block_html(enrichment: MessageEnrichment) -> str:
    """kind별 보강 컨텍스트 블록(HTML) — workb 본문용 (Plan 60 E6 §16.3).

    cpu/memory는 기존 프로세스 표로 처리하므로 이 블록은 disk/network/process/log만
    받는다. 프로파일 요지 제목(build_summary)을 첨부하고, 수집된 host-wide 스냅샷이
    있으면(disk/network) 참고 프로세스 표를 덧붙인다(스냅샷 args는 이미 마스킹됨).
    `signals` §8.2 동결 스키마 **밖** 별도 첨부다(E1 recurrence 방식).
    """
    summary = build_summary(enrichment.title, enrichment.signals)
    block = f"<br><br><b>보강 컨텍스트 — {enrichment.title}</b><br>{summary}"
    snap = enrichment.snapshot
    if snap is not None and snap.top:
        captured = (
            f" ({snap.captured_at:%Y-%m-%d %H:%M:%S} 기준)"
            if snap.captured_at is not None
            else ""
        )
        rows = _process_rows_html(snap.top)
        block += (
            f"<br>호스트 프로세스 상위 (전체 {snap.total_count}개{captured})"
            f"<table>{rows}</table>"
        )
    return block


def _investigation_briefing_html(briefing: dict) -> str:
    """sre_agent 조사 브리핑 블록(HTML) — workb 본문용 (Plan 64 CW-A · sre-agent/05 §3).

    브리핑 JSON은 sre_agent가 반환한 구조화 dict다. 스텁(조사 서비스 미가용·LLM 키 부재)이면
    `{"stub": True, "message": ...}`, 실 조사면 6요소 구조(sre-agent/02 §7 — timeline/bottleneck/
    cause/evidence/recommendation/limitation 등)다. CW-A는 수신한 브리핑을 **안전하게 첨부**만
    한다(6요소 렌더 심화·인용 검증은 조사 서비스/후속 Wave 소관). 모든 텍스트는 escape한다.
    """
    header = "<br><br><b>조사 브리핑 (자동 조사)</b>"
    if briefing.get("stub"):
        msg = html.escape(str(briefing.get("message", "조사 미실행(스텁)")))
        return f"{header}<br>{msg}"
    lines: list[str] = []
    # 알려진 6요소(있을 때만·순서 고정) + 그 외 스칼라 필드(정렬)로 안전하게 나열한다.
    ordered = ["timeline", "bottleneck", "cause", "evidence", "recommendation", "limitation"]
    labels = {
        "timeline": "타임라인", "bottleneck": "병목", "cause": "원인",
        "evidence": "근거", "recommendation": "권고", "limitation": "한계",
    }
    seen: set[str] = set()
    for key in ordered:
        val = briefing.get(key)
        if val:
            seen.add(key)
            lines.append(f"<b>{labels[key]}:</b> {html.escape(str(val))}")
    for key in sorted(briefing.keys()):
        if key in seen or key in ("stub", "elements") or briefing.get(key) in (None, "", [], {}):
            continue
        if isinstance(briefing[key], (str, int, float, bool)):
            lines.append(f"<b>{html.escape(str(key))}:</b> {html.escape(str(briefing[key]))}")
    body = "<br>".join(lines) if lines else html.escape(str(briefing))
    return f"{header}<br>{body}"


def _investigation_escalation_html(escalation: dict) -> str:
    """escalate-only 후속 통보 승격 안내 블록(HTML) — workb 본문용 (Plan 64 CW-C · §5.1).

    자동 조사의 구조화 verdict가 상향(escalate)을 지시할 때만 첨부된다(fault_escalation_enabled +
    verdict.escalate). **게이트 판정(tier/routing/decision)은 소급 변경·하향하지 않고**, 상향
    신호를 안내로만 노출한다(역방향 계약 — 상향만). 모든 텍스트는 escape한다.
    """
    header = "<br><br><b>[중요도 상향] 자동 조사 결과</b>"
    lines = ["자동 조사에서 중요도 상향 신호가 확인되었습니다(게이트 통보 판정은 유지)."]
    level = escalation.get("level")
    if level:
        lines.append(f"<b>상향 레벨:</b> {html.escape(str(level))}")
    confidence = escalation.get("confidence")
    if confidence:
        lines.append(f"<b>신뢰도:</b> {html.escape(str(confidence))}")
    signals = escalation.get("signals")
    if signals:
        sig_text = (
            ", ".join(str(s) for s in signals)
            if isinstance(signals, (list, tuple))
            else str(signals)
        )
        lines.append(f"<b>근거 신호:</b> {html.escape(sig_text)}")
    return f"{header}<br>" + "<br>".join(lines)


def _enrichment_to_attach(
    enrichment: Optional[MessageEnrichment],
    decision,  # noqa: ANN001 — NotificationDecision | None
    enrichment_min_tier: str,
) -> Optional[MessageEnrichment]:
    """티어 게이트를 적용해 첨부할 보강 블록을 결정한다 (Plan 60 E6 §16.3).

    통보 결정 티어가 enrichment_min_tier(기본 PAGE) 이상일 때만 첨부한다(라우팅 불변 —
    첨부만). decision이 None(게이트 off)이면 본문 생성 자체가 발송 경로이므로 첨부한다.
    보강 블록이 없거나 티어 미달이면 None(첨부 생략).
    """
    if enrichment is None:
        return None
    min_rank = _TIER_RANK.get((enrichment_min_tier or TIER_PAGE).lower(), _TIER_RANK[TIER_PAGE])
    if decision is not None:
        if _TIER_RANK.get(decision.tier, _TIER_RANK[TIER_PAGE]) < min_rank:
            return None
    return enrichment


def build_workb_body(
    result: AlarmAnalysisResult,
    process_snapshot: Optional[ProcessSnapshot] = None,
    recurrence: Optional[dict] = None,
    repeat_interval_seconds: int = 14400,
    enrichment: Optional[MessageEnrichment] = None,
    investigation_briefing: Optional[dict] = None,
    investigation_escalation: Optional[dict] = None,
) -> str:
    """WorkB 쪽지 본문을 HTML 형식으로 생성한다.

    recurrence(Plan 60 E1): 재통보 시 직전 창 재발 메타(count 등)가 있고 count>1이면
    "직전 {N}h {count}회 재발 후 재통보" 1줄을 첨부한다(대표 알람 표기). 첨부만 —
    라우팅·발송 판단은 불변이다.

    enrichment(Plan 60 E6): message_enrichment_enabled·티어 게이트를 통과한 신규 kind
    (disk/network/process/log) 보강 블록이 있으면 별도 첨부한다. None(기본)이면 본문은
    비트 동일 — cpu/memory 통보는 기존 프로세스 표만 유지된다.

    investigation_briefing(Plan 64 CW-A): investigation_trigger_enabled 하에서 sre_agent 조사
    서비스가 반환한 브리핑 dict가 있으면 별도 첨부한다. None(기본·off·트리거 미발화·서비스
    미가용)이면 본문은 비트 동일(회귀 0).

    investigation_escalation(Plan 64 CW-C): fault_escalation_enabled + poll verdict.escalate 하에서
    escalate-only 상향 안내 데이터가 있으면 별도 첨부한다(게이트 판정 소급 변경 없음·상향만).
    None(기본·off·미escalate)이면 본문은 비트 동일(회귀 0).
    """
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
    # Plan 60 E6: kind별 보강 컨텍스트 블록(disk/network/process/log) — 프로세스 표와
    # 동일 위치. cpu/memory는 enrichment=None이라 이 블록이 없어 기존 통보와 비트 동일.
    if enrichment is not None:
        body += _enrichment_block_html(enrichment)
    # Plan 47: 패턴 분석 섹션 (pattern_type=""이면 생략)
    if result.pattern_type:
        body += (
            f"<br><br><b>패턴 분석</b><br>"
            f"{_pattern_badge(result)} {result.pattern_analysis}"
        )
    # Plan 60 E1: 재발생 이력 (직전 창에서 억제된 재발 횟수 — count>1일 때만)
    if recurrence and recurrence.get("count", 0) > 1:
        window_h = repeat_interval_seconds // 3600
        body += (
            f"<br><br><b>재발생 이력</b><br>"
            f"직전 {window_h}h {recurrence['count']}회 재발 후 재통보"
        )
    # Plan 64 CW-A: 자동 조사 브리핑 (investigation_briefing=None이면 미첨부 → 본문 비트 동일)
    if investigation_briefing is not None:
        body += _investigation_briefing_html(investigation_briefing)
    # Plan 64 CW-C: escalate-only 중요도 상향 안내 (investigation_escalation=None이면 미첨부 → 비트 동일)
    if investigation_escalation is not None:
        body += _investigation_escalation_html(investigation_escalation)
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
    # (Plan 60 E1) 재통보 시 직전 창 재발 메타 + 재통보 창(getattr 가드).
    recurrence: Optional[dict] = state.get("recurrence")
    gate_cfg = getattr(cfg, "noise_gate", None)
    repeat_interval_seconds = getattr(gate_cfg, "repeat_interval_seconds", 14400)

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
            result,
            decision,
            ticket_queue,
            alarm_bus,
            sse_publisher,
            # (Plan 83) SUPPRESS SSE 옵트인 — 게이트 설정 부재 시 False(현행 유지)
            suppress_sse=bool(getattr(gate_cfg, "sse_suppressed_enabled", False)),
        )
        return {"analysis_result": result}

    # ── D-049: PAGE 결정 시 incident open 이벤트 발행 ──
    # PAGE만 incident(TICKET/DASHBOARD/SUPPRESS는 위에서 분기·종료) → 전환율 분모=page_count 정합.
    # incident_publisher 미주입(트래커 off) 시 발행 스킵 → 회귀 0. 발행은 graceful(아래 workb 무차단).
    if decision is not None and decision.tier == TIER_PAGE:
        incident_publisher = (config or {}).get("configurable", {}).get(
            "incident_publisher"
        )
        await _publish_incident_open(result, decision, incident_publisher)

    # ── Plan 60 E6: 메시지 기반 L1 보강 블록 첨부(옵트인·티어 게이트) ──
    # message_enrichment_enabled + 통보 티어 ≥ enrichment_min_tier일 때만 첨부(라우팅 불변).
    # gate_cfg 없거나 message off면 None → 통보 본문 비트 동일(회귀 0). E1 recurrence 방식.
    enrichment: Optional[MessageEnrichment] = None
    if getattr(gate_cfg, "message_enrichment_enabled", False):
        enrichment = _enrichment_to_attach(
            state.get("enrichment"),
            decision,
            getattr(gate_cfg, "enrichment_min_tier", TIER_PAGE),
        )

    # ── Plan 64 CW-A: 자동 조사 브리핑 첨부(investigation_trigger 노드가 state에 실음) ──
    # 트리거 off/미발화/서비스 미가용이면 None → 통보 본문 비트 동일(회귀 0).
    investigation_briefing: Optional[dict] = state.get("investigation_briefing")

    # ── Plan 64 CW-C: escalate-only 중요도 상향 안내 첨부(investigation_trigger가 verdict.escalate 시 실음) ──
    # fault_escalation off/미escalate면 None → 통보 본문 비트 동일(회귀 0)·게이트 판정 소급 변경 없음.
    investigation_escalation: Optional[dict] = state.get("investigation_escalation")

    for channel in result.notification_channels:
        try:
            if channel == "workb":
                await _send_workb(
                    cfg.workb,
                    result,
                    process_snapshot,
                    recurrence=recurrence,
                    repeat_interval_seconds=repeat_interval_seconds,
                    enrichment=enrichment,
                    investigation_briefing=investigation_briefing,
                    investigation_escalation=investigation_escalation,
                )
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

    # ── Plan 66 3-E: 즉시통보 완료 후 후속 브리핑 발송 태스크 spawn ──
    # 후속 모드(investigation_followup_enabled)에서 트리거가 submit만 하고 넘긴 pending이 있을
    # 때만 진행한다. 여기서 spawn하므로 후속 메시지는 **즉시 통보 이후**에만 나간다(순서 보장).
    # off/pending 없음이면 아무 것도 하지 않는다 → 통보 경로 비트동일(회귀 0).
    _spawn_investigation_followup(
        state.get("investigation_pending"), result, cfg, gate_cfg, config
    )

    return {"analysis_result": result}


def _spawn_investigation_followup(
    pending: Optional[dict],
    result: AlarmAnalysisResult,
    cfg,  # noqa: ANN001 — AppConfig (덕 타이핑)
    gate_cfg,  # noqa: ANN001 — NoiseGateConfig | None (덕 타이핑)
    config: RunnableConfig,
) -> None:
    """후속 브리핑 발송을 백그라운드 태스크로 띄운다 (Plan 66 3-E · fire-and-forget).

    통보를 막지 않는 것이 이 경로의 존재 이유이므로 **await하지 않는다**. 스폰 자체가 불가능한
    조건(플래그 off·pending 없음·workb 미발송·상한 초과·이벤트 루프 부재)에서는 조용히 넘어가되,
    상한 초과만은 사유를 로그로 남긴다(침묵 폴백 금지 — 유실이 아니라 의도된 차단임을 남긴다).
    """
    if not pending or not getattr(gate_cfg, "investigation_followup_enabled", False):
        return
    # 즉시 통보가 workb로 실제 발송된 경우에만 후속을 보낸다 — 원 통보 없이 브리핑만 가는
    # 고아 메시지를 만들지 않는다.
    if not result.notifications_sent.get("workb"):
        return
    max_inflight = int(getattr(gate_cfg, "investigation_followup_max_inflight", 8))
    if len(_FOLLOWUP_TASKS) >= max_inflight:
        logger.warning(
            "후속 브리핑 태스크 상한(%d) 초과 — 이번 조사는 후속 발송 생략: alarm_id=%s inv=%s",
            max_inflight, result.alarm_event.alarm_id, pending.get("investigation_id"),
        )
        return
    store = (config or {}).get("configurable", {}).get("decision_store")
    try:
        task = asyncio.create_task(
            _deliver_investigation_followup(pending, result, cfg, gate_cfg, store)
        )
    except RuntimeError:  # 실행 중인 이벤트 루프 없음(동기 컨텍스트) — graceful
        logger.warning(
            "후속 브리핑 태스크 생성 불가(이벤트 루프 부재): alarm_id=%s",
            result.alarm_event.alarm_id,
        )
        return
    _FOLLOWUP_TASKS.add(task)
    task.add_done_callback(_FOLLOWUP_TASKS.discard)


async def _deliver_investigation_followup(
    pending: dict,
    result: AlarmAnalysisResult,
    cfg,  # noqa: ANN001 — AppConfig (덕 타이핑)
    gate_cfg,  # noqa: ANN001 — NoiseGateConfig (덕 타이핑)
    store,  # noqa: ANN001 — DecisionStore | None (덕 타이핑)
) -> None:
    """조사 종결까지 poll한 뒤 브리핑을 후속 메시지로 발송한다 (Plan 66 3-E).

    즉시 통보 뒤에 백그라운드로 도는 경로다. 전 구간이 graceful — 어떤 실패도 이미 나간 통보나
    다음 알람 처리에 영향을 주지 않으며, 최종 상태는 사유와 함께 감사에 남긴다(침묵 금지).
    브리핑도 상향 안내도 없으면 발송하지 않는다(빈 후속 메시지 금지).
    """
    investigation_id = pending.get("investigation_id") or ""
    alarm_id = pending.get("alarm_id") or ""
    fingerprint = pending.get("fingerprint") or ""
    timeout = float(getattr(gate_cfg, "investigation_followup_timeout_seconds", 300.0))

    briefing: Optional[dict] = None
    verdict: Any = None
    status = "error"
    try:
        briefing, status, verdict = await asyncio.wait_for(
            _poll_until_terminal(investigation_id, gate_cfg), timeout=timeout
        )
    except asyncio.TimeoutError:
        status = "followup_timeout"
        logger.warning(
            "후속 브리핑 poll 타임아웃(%.0fs 초과): alarm_id=%s inv=%s",
            timeout, alarm_id, investigation_id,
        )
    except Exception:  # noqa: BLE001 — 후속 실패가 이미 나간 통보를 되돌리지 않는다
        status = "followup_failed"
        logger.warning(
            "후속 브리핑 poll 실패: alarm_id=%s inv=%s",
            alarm_id, investigation_id, exc_info=True,
        )

    escalation = (
        build_escalation(verdict)
        if getattr(gate_cfg, "fault_escalation_enabled", False)
        else None
    )
    if briefing is None and escalation is None:
        _audit_followup(store, alarm_id, fingerprint, investigation_id, status, verdict)
        return

    try:
        await _send_workb_followup(cfg.workb, result, briefing, escalation)
        logger.info(
            "후속 브리핑 발송 완료: alarm_id=%s inv=%s status=%s",
            alarm_id, investigation_id, status,
        )
    except Exception:  # noqa: BLE001 — 발송 실패도 감사에 남기고 종료
        status = f"{status}/followup_send_failed"
        logger.warning(
            "후속 브리핑 발송 실패: alarm_id=%s inv=%s",
            alarm_id, investigation_id, exc_info=True,
        )
    _audit_followup(store, alarm_id, fingerprint, investigation_id, status, verdict)


async def _poll_until_terminal(
    investigation_id: str,
    gate_cfg,  # noqa: ANN001 — NoiseGateConfig (덕 타이핑)
) -> tuple[Optional[dict], str, Any]:
    """자체 클라이언트로 조사 종결까지 poll한다(상위 wait_for가 전체 유계).

    워커가 주입한 공유 클라이언트를 쓰지 않는다 — 이 폴링은 통보 이후까지 살아있는데 워커는
    다음 알람을 처리하며 같은 인스턴스를 connect/disconnect하므로 세션이 서로 끊긴다.
    """
    from noise_gate.infrastructure.sre_agent_client import build_sre_agent_client

    client = build_sre_agent_client(gate_cfg)
    if client is None:
        return None, "followup_no_client", None
    poll_interval = float(
        getattr(gate_cfg, "investigation_poll_interval_seconds", 1.0)
    )
    await client.connect()
    try:
        while True:
            res = await client.poll(investigation_id)
            poll_status = res.get("status")
            if poll_status in _TERMINAL_POLL_STATUSES:
                return res.get("briefing"), poll_status, res.get("verdict")
            await asyncio.sleep(poll_interval)
    finally:
        await client.disconnect()


def _audit_followup(
    store,  # noqa: ANN001 — DecisionStore | None (덕 타이핑)
    alarm_id: str,
    fingerprint: str,
    investigation_id: str,
    status: str,
    verdict: Any,
) -> None:
    """후속 브리핑의 최종 상태를 감사에 남긴다(트리거의 submitted 레코드에 이어지는 종결 기록)."""
    if store is None:
        return
    try:
        store.record_investigation(
            alarm_id=alarm_id,
            fingerprint=fingerprint,
            investigation_id=investigation_id,
            status=status,
            verdict=verdict,
        )
    except Exception:  # noqa: BLE001 — 감사 실패는 무시(이미 발송 완료)
        logger.warning("후속 브리핑 감사 기록 실패(무시): alarm_id=%s", alarm_id)


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
        # (Plan 83 T6) 결정적 사전분류 — 카드가 피드백 저장 키로 되돌려 보낸다
        "pre_classification": result.pre_classification,
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
    suppress_sse: bool = False,
) -> None:
    """PAGE 외 티어(TICKET/DASHBOARD/SUPPRESS)를 라우팅한다(발송 안 함, §7 · Phase E3).

    감사 기록(tier·reason·signals)은 notification_gate가 decision_store에 이미 적재했으므로
    여기서는 큐 적재/SSE만 수행한다(중복 적재 금지). 모든 부수효과는 graceful —
    큐/SSE 실패는 warning 후 무시하여 파이프라인을 막지 않는다.

    - TICKET: 일배치 요약 큐 적재(ticket_queue 있으면) + DASHBOARD와 동일하게 SSE 표시.
    - DASHBOARD: SSE(alarm_bus 또는 sse_publisher 있으면)로 UI에만 표시.
    - SUPPRESS: 발송·큐 없음. `suppress_sse=True`(Plan 83 · NOISE_SSE_SUPPRESSED_ENABLED)면
      SSE만 발행해 **관리자 감사 레벨**에서 볼 수 있게 한다 — 기본 False면 종전처럼 로그만이라
      비트 동일하다. 수신 측 권한(관리자 전용)은 스트림 엔드포인트가 판정한다(이 함수 밖).

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
        # (Plan 83) 옵트인 시에만 SSE 발행 — 억제 내역을 UI에서 감사하기 위한 경로다.
        # 발송·큐는 여전히 없다(억제 판정 자체는 불변). 기본 off면 종전과 동일.
        if suppress_sse:
            await _publish_tier_sse(result, decision, alarm_bus, sse_publisher)
        logger.info(
            "SUPPRESS(미통보 — 감사 기록만%s): alarm_id=%s reason=%s",
            " · SSE 발행" if suppress_sse else "",
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


def _incident_open_payload(result: AlarmAnalysisResult, decision) -> dict:  # noqa: ANN001
    """incident open 이벤트 payload를 생성한다(§5 양측 합의 스키마 + 카드 표시필드, D-049).

    재발행 SSE 카드(app.js renderAlarmMessage)가 빈 칸 없이 렌더되도록 `_tier_sse_payload`의
    전체 표시필드(severity/severity_label/alarm_name/.../pattern_analysis)를 포함하고,
    incident 식별필드(type·fingerprint·priority·ts)를 합친다. process_snapshot/history_stats는
    SSE 직렬화 부담으로 `_tier_sse_payload`와 동일하게 제외한다(카드의 해당 섹션은 생략됨).
    """
    ev = result.alarm_event
    return {
        # ── incident 식별필드 ──
        "type": "open",
        "fingerprint": decision.fingerprint,
        "priority": str(decision.priority),
        "ts": datetime.now().isoformat(),
        # ── 카드 표시필드 (_tier_sse_payload 미러) ──
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
        # (Plan 83 T6) 결정적 사전분류 — 카드가 피드백 저장 키로 되돌려 보낸다
        "pre_classification": result.pre_classification,
        "tier": decision.tier,
    }


async def _publish_incident_open(
    result: AlarmAnalysisResult,
    decision,  # noqa: ANN001 — NotificationDecision
    incident_publisher=None,  # noqa: ANN001 — RedisIncidentPublisher | None (덕 타이핑)
) -> None:
    """PAGE 결정 시 incident open 이벤트를 발행한다(미주입·실패는 graceful).

    incident_publisher 미주입(트래커 off) 시 발행을 스킵한다 → 회귀 0.
    notifier(application)는 redis를 직접 import하지 않고 주입된 발행기를 덕타이핑 호출한다.
    발행 실패가 workb 발송을 막지 않는다(graceful degradation).
    """
    if incident_publisher is None:
        return
    try:
        await incident_publisher.publish(_incident_open_payload(result, decision))
    except Exception:  # noqa: BLE001 — incident 발행 실패가 발송을 막지 않는다
        logger.warning(
            "incident open 발행 실패(무시): alarm_id=%s",
            result.alarm_event.alarm_id,
        )


async def _send_workb(
    workb_cfg,
    result: AlarmAnalysisResult,
    process_snapshot: Optional[ProcessSnapshot] = None,
    *,
    recurrence: Optional[dict] = None,
    repeat_interval_seconds: int = 14400,
    enrichment: Optional[MessageEnrichment] = None,
    investigation_briefing: Optional[dict] = None,
    investigation_escalation: Optional[dict] = None,
) -> None:
    """worKB 사내메신저 쪽지 발송.

    실제 쪽지 제목: "{alias} {msgTitle}" 형태로 사용자 쪽지창에 표시된다.
    예: "[인프라알람] [심각] svr-infra-001 (svr-infra-001.internal)"

    Args:
        workb_cfg: WorkbConfig 인스턴스
        result: 알람 분석 결과
        process_snapshot: 영향 프로세스 스냅샷 (Plan 47-1, None이면 표 생략)
        recurrence: 재통보 시 직전 창 재발 메타 (Plan 60 E1, None이면 표기 생략)
        repeat_interval_seconds: 재통보 창(초) — 재발생 이력 표기 시간 산출용
        enrichment: kind별 L1 보강 블록 (Plan 60 E6, None이면 첨부 생략)
        investigation_briefing: sre_agent 조사 브리핑 (Plan 64 CW-A, None이면 첨부 생략)
        investigation_escalation: escalate-only 상향 안내 (Plan 64 CW-C, None이면 첨부 생략)
    """
    if not workb_cfg.base_url:
        raise ValueError("WORKB_BASE_URL이 설정되지 않았습니다.")

    ev = result.alarm_event
    msg_title = f"[{result.severity_label}] {ev.server_name} ({ev.hostname})"
    msg_body = build_workb_body(
        result,
        process_snapshot,
        recurrence=recurrence,
        repeat_interval_seconds=repeat_interval_seconds,
        enrichment=enrichment,
        investigation_briefing=investigation_briefing,
        investigation_escalation=investigation_escalation,
    )
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


def build_followup_body(
    result: AlarmAnalysisResult,
    briefing: Optional[dict] = None,
    escalation: Optional[dict] = None,
) -> str:
    """후속 브리핑 메시지 본문(HTML)을 생성한다 (Plan 66 3-E).

    즉시 통보에 이어지는 별도 메시지이므로 알람 원문 전체를 반복하지 않고, 어느 알람의 후속인지
    식별할 최소 정보(알람명·서버·알람ID)만 머리에 두고 브리핑·상향 안내 블록을 붙인다.
    블록 렌더는 인라인 첨부(CW-A/CW-C)와 **같은 함수**를 쓴다 — 두 모드의 표현이 갈리지 않는다.
    """
    ev = result.alarm_event
    body = (
        f"<b>자동 조사 결과 (후속)</b><br>"
        f"<b>알람명:</b> {html.escape(ev.alarm_name)}<br>"
        f"<b>서버:</b> {html.escape(ev.server_name)} ({html.escape(ev.hostname)})<br>"
        f"<b>알람 ID:</b> {html.escape(ev.alarm_id)}"
    )
    if briefing is not None:
        body += _investigation_briefing_html(briefing)
    if escalation is not None:
        body += _investigation_escalation_html(escalation)
    return body


async def _send_workb_followup(
    workb_cfg,  # noqa: ANN001 — WorkbConfig (덕 타이핑)
    result: AlarmAnalysisResult,
    briefing: Optional[dict],
    escalation: Optional[dict],
) -> None:
    """후속 브리핑을 worKB 쪽지로 발송한다 (Plan 66 3-E · `_send_workb` 전송 규약 동형).

    수신자·인증·엔드포인트는 원 통보와 동일하고 제목만 후속임을 드러낸다. webhook 채널은
    후속 대상이 아니다 — 기계 연동 채널은 원 알람을 이미 받았고, 브리핑은 사람이 읽는 정보다.
    """
    if not workb_cfg.base_url:
        raise ValueError("WORKB_BASE_URL이 설정되지 않았습니다.")

    ev = result.alarm_event
    payload = {
        "systemDiv": workb_cfg.system_div,
        "msgTitle": f"[조사 결과] {ev.server_name} ({ev.hostname})",
        "msgBody": build_followup_body(result, briefing, escalation),
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
