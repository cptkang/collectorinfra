"""알람 발송 판단 게이트 노드 (Plan 52 Phase E1).

alarm_analyzer 다음에 위치하여, 결정적 정책 함수 `decide_notification`으로 4-티어
(PAGE/TICKET/DASHBOARD/SUPPRESS) 발송 판단을 산출하고 감사 저장소(decision_store)에
기록한다. LLM은 호출하지 않는다(순수 규칙).

설계 안전장치:
    - enable_noise_gate=False면 그래프에 노드 자체가 포함되지 않으나, 방어적으로 노드
      진입부에서도 재확인하여 게이트 비활성 시 결정을 만들지 않는다(회귀 0).
    - 감사 기록(store.record) 실패는 발송을 막지 않는다(graceful — warning 후 진행).
    - analysis_result가 없거나 error 상태면 결정을 만들지 않는다. AI 분석 실패는 analyzer가
      원문 알람으로 만든 결과(result.error 표시)를 넘기므로 여기서도 판단·기록한다.
    - (plans/87 J4) app_impact_enabled일 때만, 매트릭스 DASHBOARD·TICKET 폴스타 알람에 한해 APM
      게이트웨이(`apm_client` 주입)에 fatal 이벤트를 묻고 있으면 승격만 한다. 조회 실패는 판정
      불변 + 사유(로그·`stage_evidence.app_impact_error`). (plans/87 J8) 조회는 알람 존의 제니퍼
      소스(`source_ids`)로 좁힌다 — 존에 소스가 없으면 전 소스.

계층: application → domain(notification_policy.decide_notification) 단방향 의존만 사용한다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from langchain_core.runnables import RunnableConfig

from noise_gate.domain.cross_source import APP_IMPACT_LEVELS
from noise_gate.domain.notification_policy import (
    STAGE_MATRIX,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_TICKET,
    NotificationDecision,
    decide_notification,
)
from noise_gate.domain.process_rank import classify_alarm_kind, is_apm_event

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

    def _decide(noise_ctx):  # noqa: ANN001, ANN202 — 같은 입력으로 한 번 더 판정하기 위한 묶음
        return decide_notification(
            event,
            state.get("history_stats"),
            result,
            noise_ctx,
            gate_cfg,
            self_heal=bool(state.get("self_heal", False)),
            inhibited=bool(state.get("inhibited", False)),
            flapping=bool(state.get("flapping", False)),
            storm=bool(state.get("storm", False)),
            correlated=bool(state.get("correlated", False)),
            # (Plan 60 E7-a) 워커가 산출한 계획-무해 코로보레이션 게이팅용 주석 신호
            # (off/없으면 None).
            annotation=state.get("annotation"),
            # (Plan 54 모듈 4) 워커가 읽어 넘긴 활성 침묵 규칙(off/없으면 빈 목록 → 단계 미평가).
            silence_rules=state.get("silence_rules"),
            # (plans/144 §4.2·4.3) 워커가 제니퍼 알람에 산출한 정규화 유형·정책 행·지속 조건
            # (apm_noise_policy_enabled off·폴스타 알람이면 None → 비트 동일).
            apm_policy=state.get("apm_policy"),
            # (plans/144 W3) 워커 사건 추적기의 크로스소스 신호(cross_source_mode off·사건
            # 미소속이면 None → 비트 동일).
            cross_source=state.get("cross_source"),
        )

    noise_ctx = state.get("noise_context")
    decision = _decide(noise_ctx)

    # (plans/87 J4 · R-7) app_impact 승격 — 매트릭스 결과가 DASHBOARD·TICKET인 폴스타 알람만
    # 게이트웨이에 묻는다(PAGE·억제 결과는 바뀔 수 없으니 부르지 않는다 — 부하·지연 절약). fatal
    # 이벤트가 있으면 예약키 app_impact를 채워 같은 입력으로 다시 판정한다(도메인 step 9.5가 PAGE로
    # 올린다). 실패는 판정 그대로 두고 사유를 로그·감사에 남긴다. off(기본)면 이 블록에 들어오지
    # 않아 비트 동일.
    # (plans/144 W4 §5.6) 워커가 사건 저장소에서 미리 산출한 앱 영향(`episode_app_impact` —
    # cross_source_mode annotate·enforce에서만 실린다)에 fatal 이벤트가 있으면 게이트웨이를 부르지
    # 않고 그 값으로 승격한다. 없으면 현행 게이트웨이 조회 그대로다(폴러가 꺼진 환경 호환).
    # (plans/144 W4 §5.7) 정상 강등 shadow 후보(CPU·메모리 경고)면 PAGE 판정이어도 한 번 조회해
    # 「창 안 이벤트 0건」이면 표지만 남긴다 — 판정·사유는 바꾸지 않는다.
    app_impact_audit: dict[str, Any] = {}
    updated_ctx: dict[str, Any] | None = None
    impact_applicable = _app_impact_applicable(gate_cfg, event, decision, noise_ctx)
    shadow_candidate = _healthy_shadow_applicable(gate_cfg, event, decision, noise_ctx)
    if impact_applicable or shadow_candidate:
        episode_impact = state.get("episode_app_impact")
        if not isinstance(episode_impact, dict):
            episode_impact = {}
        episode_app_impact = episode_impact.get("app_impact")
        episode_events = int(episode_impact.get("window_events") or 0)
        client = configurable.get("apm_client")
        app_impact: dict[str, Any] | None = None
        if impact_applicable and isinstance(episode_app_impact, dict):
            app_impact = episode_app_impact  # 사건 저장소 판정 — 게이트웨이 호출 없음
        elif episode_events and not impact_applicable:
            pass  # shadow 전용 조회인데 사건에 이미 APM 이벤트가 있다 — 정상 아님, 조회 불필요
        elif client is None:
            logger.debug(
                "app_impact 조회 생략 — 게이트웨이 클라이언트 없음: alarm_id=%s", event.alarm_id
            )
        else:
            # shadow 후보면 레벨을 낮춰(normal 이상 = 전 레벨) 한 번에 묻는다 — fatal 판정은 아래
            # 같은 레벨 필터를 거치므로 승격 결과는 fatal 조회와 같다.
            fetched, failure, total_rows = await _fetch_app_impact(
                client, event, gate_cfg, level="normal" if shadow_candidate else "fatal"
            )
            if impact_applicable:
                app_impact = fetched
            if failure:
                app_impact_audit["app_impact_error"] = failure
            elif shadow_candidate and total_rows == 0 and not episode_events:
                app_impact_audit["apm_healthy_shadow"] = {
                    "would_demote": True,
                    "window_minutes": int(getattr(gate_cfg, "app_impact_window_minutes", 10)),
                    "source": "mcp",
                }
                logger.info(
                    "제니퍼 정상 강등 shadow(판정 불변): alarm_id=%s tier=%s",
                    event.alarm_id, decision.tier,
                )
        if app_impact is not None:
            updated_ctx = {**(noise_ctx or {}), "app_impact": app_impact}
            promoted: NotificationDecision = _decide(updated_ctx)  # type: ignore[no-untyped-call]
            logger.info(
                "app_impact 승격: alarm_id=%s %s→%s fatal=%d types=%s source=%s",
                event.alarm_id, decision.tier, promoted.tier,
                app_impact["fatal_events"], app_impact["event_types"], app_impact["source"],
            )
            decision = promoted

    store = configurable.get("decision_store")
    if store is not None:
        try:
            # (plans/112 S6) 결정 단계의 근거만 모은다 — 도메인이 아는 것(decision.evidence) +
            # 워커 탐지가 아는 것(detection_evidence[결정 단계]). 다른 단계의 탐지값은 싣지 않는다
            # (예: 스톰이 탐지됐어도 인히비션이 먼저 결정했다면 스톰 창은 이 판단의 근거가 아니다).
            detected = state.get("detection_evidence") or {}
            stage_evidence = {
                **(getattr(decision, "evidence", None) or {}),
                **(detected.get(getattr(decision, "stage", "")) or {}),
                # (plans/87 J4) 게이트웨이 조회 실패 사유 — 판정은 바뀌지 않았다(없으면 키 없음).
                **app_impact_audit,
            }
            # AI 분석이 실패해 원문 알람으로 판단한 건 — 결정 추적에서 구별되게 남긴다
            if getattr(result, "error", None):
                stage_evidence["analysis"] = "AI 분석 실패 — 원문 알람으로 판단"
            # (Plan 60 E1) 재통보 시 직전 창 재발 메타를 최상위 recurrence 필드로 첨부.
            # (Plan 60 E2) 상관 억제 시 클러스터 메타를 최상위 correlation_meta 필드로 첨부.
            # (Plan 60 B-7 L-2 · §15.4 D-035) 의미적 근접중복 후보 주석을 최상위
            # semantic_annotation 필드로 첨부 — **감사·관측 전용**이며 위 decision(tier/reason/
            # priority)은 이 값과 무관하게 이미 산출됐다(주석은 판정에 영향 0). off/None이면 미첨부.
            store.record(
                decision,
                alarm_id=event.alarm_id,
                # (Plan 54) 관제 화면의 "무엇이 억제됐는가" — 지문 해시로는 역인용이 안 된다.
                alarm_name=str(getattr(event, "alarm_name", "") or ""),
                server_name=str(
                    getattr(event, "server_name", "") or getattr(event, "hostname", "") or ""
                ),
                recurrence=state.get("recurrence"),
                correlation_meta=state.get("correlation_meta"),
                semantic_annotation=state.get("semantic_annotation"),
                # (plans/112 S6 · G-2) 결정 단계 근거 + 식별 필드 — 빈 값이면 키를 넣지 않는다.
                stage_evidence=stage_evidence or None,
                db_id=str(getattr(event, "db_id", "") or ""),
                resource_name=str(getattr(event, "resource_name", "") or ""),
                condition_log=str(getattr(event, "condition_log", "") or ""),
            )
        except Exception:  # noqa: BLE001 — 기록 실패가 발송을 막지 않는다
            logger.warning("발송 판단 감사 기록 실패(무시): alarm_id=%s", event.alarm_id)

    logger.info(
        "발송 판단: alarm_id=%s tier=%s reason=%s",
        event.alarm_id,
        decision.tier,
        decision.reason,
    )
    out: dict[str, Any] = {"notification_decision": decision}
    if updated_ctx is not None:
        out["noise_context"] = updated_ctx  # 예약키 app_impact를 채운 컨텍스트(승격했을 때만)
    return out


# ── plans/87 J4: app_impact 승격(APM 게이트웨이 `apm_events`) ─────────────────
# fatal 이상으로 세는 레벨 — 게이트웨이 레벨 매핑(fatal·critical → 심각도 3 · SPEC-apm-gateway §5)과
# 같다.
# `level="fatal"`로 요청하지만, 다른 레벨 행이 섞여 와도 승격 근거로 쓰지 않는다(과승격 방지 · R-7).
# (plans/144 W4) 사건 저장소 판정과 같은 집합을 쓰도록 도메인 상수를 그대로 쓴다.
_APP_IMPACT_LEVELS: frozenset[str] = APP_IMPACT_LEVELS

# (plans/144 W4 §5.7) 정상 강등 shadow 대상 kind — 인프라 CPU·메모리 경고.
_HEALTHY_SHADOW_KINDS: frozenset[str] = frozenset({"cpu", "memory"})

# 제니퍼 소스 표를 가진 레지스트리 시스템 코드
# (`config/db_registry.yaml` `solutions[].code` · plans/87 J8).
_APM_SYSTEM = "apm"


def apm_source_ids_for(db_id: str) -> list[str] | None:
    """폴스타 알람 db_id의 존에 대응하는 제니퍼 소스 id 목록(선언 순서 · plans/87 J8 · D-287 ④).

    존은 레지스트리 DB 항목의 zone이고, 소스는 `solutions[apm].sources` 중 같은 존인 것이다 —
    `app_impact`가 다른 존의 같은 hostname 인스턴스 이벤트로 승격하지 않게 좁힌다. 존이 없거나 그
    존에 소스가 없으면 None(= 전 소스 · 종전 호출). 레지스트리 실패는 경고 후 None(판정을 막지
    않는다).
    """
    try:
        from src.routing.registry import get_registry

        registry = get_registry()
        entry = registry.get(db_id)
        zone = entry.zone if entry and entry.zone else ""
        if not zone:
            return None
        ids = [s.id for s in registry.sources_of(_APM_SYSTEM) if s.zone == zone]
        return ids or None
    except Exception:  # noqa: BLE001 — 소스 좁히기 실패가 발송 판단을 막지 않는다
        logger.warning("APM 소스 좁히기 실패 — 전 소스로 조회: db_id=%s", db_id, exc_info=True)
        return None


def _app_impact_applicable(gate_cfg, event, decision, noise_ctx) -> bool:  # noqa: ANN001
    """app_impact를 물을 대상인지 — 플래그 on · 폴스타(비 APM) 알람 · 매트릭스 DASHBOARD·TICKET."""
    if not getattr(gate_cfg, "app_impact_enabled", False):
        return False
    if is_apm_event(event) or not isinstance(noise_ctx, dict):
        return False
    return decision.stage == STAGE_MATRIX and decision.tier in (TIER_DASHBOARD, TIER_TICKET)


def _healthy_shadow_applicable(gate_cfg: Any, event: Any, decision: Any, noise_ctx: Any) -> bool:
    """제니퍼 정상 강등 shadow 후보인지 (plans/144 W4 §5.7 · G-5 (a)).

    플래그(apm_healthy_demotion_shadow AND app_impact_enabled) · 폴스타 알람 · kind cpu·memory ·
    매트릭스 판정 · 실효 심각도 2 · 내릴 여지가 있는 티어(PAGE·TICKET). 판정에는 쓰지 않는다.
    """
    if not (
        getattr(gate_cfg, "apm_healthy_demotion_shadow", False)
        and getattr(gate_cfg, "app_impact_enabled", False)
    ):
        return False
    if is_apm_event(event) or not isinstance(noise_ctx, dict):
        return False
    if decision.stage != STAGE_MATRIX or decision.tier not in (TIER_PAGE, TIER_TICKET):
        return False
    if (decision.signals or {}).get("effective_severity") != 2:
        return False
    return classify_alarm_kind(event) in _HEALTHY_SHADOW_KINDS


async def _fetch_app_impact(
    client: Any, event: Any, gate_cfg: Any, *, level: str = "fatal"
) -> tuple[dict[str, Any] | None, str, int]:
    """게이트웨이 `apm_events`로 같은 hostname·사건창의 fatal 이벤트를 조회한다.

    `level`(plans/144 W4)은 조회 하한 레벨이다 — 기본 fatal(현행 호출과 같다). 정상 강등 shadow
    후보는 normal(전 레벨)로 물어 창 안 전체 건수를 함께 본다. fatal 판정은 어느 쪽이든 아래 같은
    레벨 필터로 한다.

    Returns:
        (app_impact, "", n) — fatal 이벤트가 있을 때
                              `{source, fatal_events, event_types, was_signals}` · n = 응답 행 수.
        (None, "", n)       — 조회는 됐고 fatal 이벤트가 없다(승격 없음).
        (None, 사유, 0)     — 조회하지 못했다(판정 불변 · 사유는 호출부가 감사에 남긴다).
    """
    alarm_id = str(getattr(event, "alarm_id", "") or "")
    hostname = str(getattr(event, "hostname", "") or "").strip()
    if not hostname:
        reason = "invalid_argument — 알람 hostname이 비어 있다"
        logger.info("app_impact 조회 생략: alarm_id=%s 사유=%s", alarm_id, reason)
        return None, reason, 0
    alarm_time = getattr(event, "alarm_time", None)
    if not isinstance(alarm_time, datetime):
        reason = "invalid_argument — 알람 발생 시각이 없다"
        logger.info("app_impact 조회 생략: alarm_id=%s 사유=%s", alarm_id, reason)
        return None, reason, 0

    probe = getattr(client, "unreachable_reason", None)
    unreachable = await probe() if probe is not None else None
    if unreachable:
        reason = f"gateway_unreachable — {unreachable}"
        logger.warning("app_impact 조회 실패(판정 유지): alarm_id=%s 사유=%s", alarm_id, reason)
        return None, reason, 0
    kwargs: dict[str, Any] = {
        "hostname": hostname,
        # naive(폴스타 알람 시각 그대로) — 게이트웨이가 APM_TIMEZONE으로 해석한다(§3 공통 인자).
        "reference_time": alarm_time.isoformat(),
        "lookback_minutes": int(getattr(gate_cfg, "app_impact_window_minutes", 10)),
        "level": level,
        "investigation_id": alarm_id,
    }
    # (plans/87 J8) 알람 존의 제니퍼 소스로만 좁힌다 — 없으면 인자를 넣지 않아 종전 호출과 같다
    # (전 소스). 게이트웨이가 모르는 id라고 답하면(invalid_argument) 아래 오류 경로로 판정을
    # 유지한다 — 전 소스 재시도는 하지 않는다(다른 존 승격 방지 · 침묵 폴백 금지).
    source_ids = apm_source_ids_for(str(getattr(event, "db_id", "") or ""))
    if source_ids:
        kwargs["source_ids"] = source_ids
    logger.debug(
        "app_impact 조회: alarm_id=%s host=%s source_ids=%s",
        alarm_id, hostname, source_ids or "전 소스",
    )
    try:
        resp = await client.apm_events(**kwargs)
    except Exception as exc:  # noqa: BLE001 — 통신 실패도 판정을 막지 않는다(사유만 남긴다)
        reason = f"gateway_error — {exc}"
        logger.warning("app_impact 조회 실패(판정 유지): alarm_id=%s 사유=%s", alarm_id, reason)
        return None, reason, 0

    if resp.get("error"):
        reason = f"{resp.get('error')} — {resp.get('reason') or ''}".strip(" —")
        logger.warning(
            "app_impact 게이트웨이 오류 응답(판정 유지): alarm_id=%s 사유=%s", alarm_id, reason
        )
        return None, reason, 0
    rows = resp.get("rows")
    if not isinstance(rows, list):
        reason = "contract_violation — 응답에 rows 배열이 없다"
        logger.warning("app_impact 응답 계약 위반(판정 유지): alarm_id=%s", alarm_id)
        return None, reason, 0

    fatal_rows = [
        r for r in rows
        if isinstance(r, dict) and str(r.get("level") or "").strip().lower() in _APP_IMPACT_LEVELS
    ]
    if not fatal_rows:
        logger.debug("app_impact 없음(fatal 0건): alarm_id=%s host=%s", alarm_id, hostname)
        return None, "", len(rows)
    was_signals = resp.get("was_signals") if isinstance(resp.get("was_signals"), list) else []
    return {
        "source": str(resp.get("source") or ""),
        "fatal_events": len(fatal_rows),
        "event_types": sorted({str(r["event_type"]) for r in fatal_rows if r.get("event_type")}),
        "was_signals": sorted(
            {str(s["kind"]) for s in was_signals if isinstance(s, dict) and s.get("kind")}
        ),
    }, "", len(rows)
