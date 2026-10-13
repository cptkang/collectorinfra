"""자동 조사 트리거 노드 (Plan 64 §0.2 CW-A · Plan 60 §14.2 · sre-agent/05 §3).

`notification_gate`가 최종 `NotificationDecision`을 산출한 **직후**(그래프상 gate 다음, notifier
이전)에 위치한다. tier가 `investigation_trigger_min_tier`(기본 PAGE) 이상이면 sre_agent 조사
서비스에 트리거 페이로드(`contract_version: "1"`)를 submit하고, **전체 타임아웃 가드** 안에서
poll하여 브리핑을 받아 state에 실어 notifier가 통보에 첨부하게 한다. 결과(investigation_id·
status·verdict)는 decision_store에 감사한다. (plans/153 W2) 조사 저장소가 주입되면
상태·사유·브리핑을 웹 조회용으로 함께 남긴다(미주입이면 no-op). (plans/153 W4) 사건 맥락 플래그가
켜져 있으면 페이로드에 제출 시점 사건 스냅샷을 싣고, 워커가 부르는 `submit_episode_investigation`이
사건당 1회 종합 조사를 제출한다.

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

from noise_gate.domain.investigation_payload import (
    EPISODE_FINGERPRINT_PREFIX,
    INVESTIGATION_KIND_EPISODE,
    build_episode_trigger_payload,
    build_escalation,
    build_trigger_payload,
    is_availability_alarm,
)
from noise_gate.domain.notification_policy import _TIER_RANK, TIER_PAGE
from noise_gate.domain.process_rank import is_apm_event
from src.domain.host_authz import Principal, authorize_host_investigation
from src.observability.investigation_metrics import record_investigation
from src.utils.prior_targets import resolve_targets

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
        후속 모드(investigation_followup_enabled)에서는 브리핑 대신
        {"investigation_pending": {...}}를 실어 notifier가 즉시 통보 후 후속 발송하게 한다.
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
    # (plans/144 §5.3 · §4.5) 크로스소스 사건 id — cross_source_mode off·사건 미소속이면 None
    # (페이로드·감사 비트 동일).
    episode_id = _episode_id(state)
    # (plans/153 W2) 조사 저장소 — 웹 조회용 상태·브리핑 기록. 미주입(트리거 off·워커 밖)이면 None →
    # 아래 `_store` 호출은 전부 no-op(비트 동일). 행의 `source`는 이 조사가 도는 모드다.
    inv_store = configurable.get("investigation_store")
    followup = bool(getattr(gate_cfg, "investigation_followup_enabled", False))
    source = "followup" if followup else "inline"

    # (plans/144 §4.5) APM 알람발 조사는 사건당 1회 — 같은 사건에서 APM 알람이 이미 제출한 조사가
    # 있으면(워커가 `episode_investigation`으로 넘긴다) 다시 제출하지 않고 기존 조사 id를 감사에
    # 남긴다. 인프라 알람의 조사 트리거는 이 분기에 들어오지 않는다(현행 그대로).
    existing = state.get("episode_investigation")
    if is_apm_event(event) and isinstance(existing, dict) and existing.get("investigation_id"):
        logger.info(
            "조사 생략 — 같은 사건의 기존 조사 재사용: alarm_id=%s episode=%s inv=%s",
            getattr(event, "alarm_id", ""), existing.get("episode_id"),
            existing.get("investigation_id"),
        )
        _audit(
            configurable.get("decision_store"), event, decision,
            str(existing["investigation_id"]), "episode_existing", None,
            episode_id=str(existing.get("episode_id") or episode_id or "") or None,
        )
        _store(
            inv_store, event, str(existing["investigation_id"]), "episode_existing",
            source=source,
            episode_id=str(existing.get("episode_id") or episode_id or "") or None,
        )
        return {}

    # plans/87 J4: 게이트웨이가 인스턴스↔hostname 정합에 실패한 APM 이벤트는 `hostname=""`로 온다
    # (SPEC-apm-gateway §5). 조사 계약 필수 필드(serverName·hostname·severity)를 채우지 못하므로
    # 보내지 않고 사유를 남긴다 — 보내 봐야 조사 서비스가 rejected로 되돌릴 뿐이다(왕복 제거 ·
    # 침묵 금지).
    # 폴스타 이벤트는 이 분기에 들어오지 않는다(판정은 resourceType 기준).
    if is_apm_event(event) and not str(getattr(event, "hostname", "") or "").strip():
        reason = "APM 인스턴스 hostname 미해소 — 조사 필수 필드(hostname) 결측으로 조사 생략"
        logger.warning(
            "조사 트리거 생략: alarm_id=%s 사유=%s", getattr(event, "alarm_id", ""), reason
        )
        _audit(
            configurable.get("decision_store"), event, decision,
            None, "target_unresolved", reason,
        )
        _store(inv_store, event, None, "target_unresolved", source=source, reason=reason)
        return {}

    # 조사 대상 확정 — **채팅 경로와 같은 공통 모듈**을 쓴다 (Plan 78 W1-4 · G5 대칭).
    # 이벤트 경로는 알람 페이로드가 1순위이므로 그것만 넘긴다. 종전에는 hostname이
    # 플레이스홀더("해당 서버" 등)여도 그대로 submit해 sre_agent가 rejected로 되돌렸다 —
    # 결정적 가드를 진입부에 두어 왕복을 없앤다.
    #
    # **플래그 off면 판정만 하고 막지 않는다**(회귀 0 — Plan 80 §5.4-③).
    target_resolution = resolve_targets(
        alarm_payload={
            "server_name": getattr(event, "server_name", None),
            "hostname": getattr(event, "hostname", None),
            "db_id": getattr(event, "db_id", None),
        }
    )
    if not target_resolution.resolved:
        reasons = [d.get("reason") for d in target_resolution.dropped] or ["no_identifier"]
        logger.info(
            "조사 트리거 대상 미확정: alarm_id=%s 사유=%s (차단=%s)",
            getattr(event, "alarm_id", ""), reasons,
            getattr(getattr(cfg, "composite", None), "prior_targets_enabled", False),
        )
        if getattr(getattr(cfg, "composite", None), "prior_targets_enabled", False):
            _audit(
                configurable.get("decision_store"), event, decision,
                None, "target_unresolved", None,
            )
            _store(inv_store, event, None, "target_unresolved", source=source)
            return {}

    # ★ 호스트 인가 게이트 — **채팅 경로와 같은 모듈**을 호출한다 (Plan 78 W3-5 · G5 대칭).
    # 한쪽만 적용되는 비대칭이 이 저장소의 반복 실수다. 이벤트 경로에는 사용자가 없으므로
    # `system` 주체를 쓰고, 허용되더라도 **판정 결과를 감사에 남긴다**(W6-5).
    first = target_resolution.targets[0] if target_resolution.resolved else None
    authz = authorize_host_investigation(
        mode=getattr(getattr(cfg, "host_authz", None), "mode", None),
        principal=Principal.system(),
        hostname=(first.hostname or first.server_name) if first else None,
        db_id=getattr(event, "db_id", None),
    )
    if not authz.allowed:
        logger.warning(
            "조사 트리거 인가 거부: alarm_id=%s %s",
            getattr(event, "alarm_id", ""), authz.as_audit(),
        )
        record_investigation(denied_reason=authz.reason)
        _audit(
            configurable.get("decision_store"), event, decision,
            None, "authz_denied", None,
        )
        _store(
            inv_store, event, None, "authz_denied", source=source,
            reason=f"호스트 인가 거부({authz.reason})" if authz.reason else None,
        )
        return {}

    # ★ 조사 서비스 사전 도달성 확인 (D-243). 서비스가 없으면 SSE 연결 타임아웃·traceback
    # 대신 한 줄 경고로 조사를 생략한다(통보는 그대로 진행). 확인 메서드가 없는 대역은 생략.
    probe = getattr(client, "unreachable_reason", None)
    if probe is not None:
        unreachable = await probe()
        if unreachable:
            logger.warning(
                "조사 서비스 미가용 — 조사 생략(통보는 진행): alarm_id=%s 사유=%s",
                getattr(event, "alarm_id", ""), unreachable,
            )
            _audit(configurable.get("decision_store"), event, decision, None, "down", None)
            _store(
                inv_store, event, None, "down", source=source,
                reason=f"조사 서비스 미가용: {unreachable}",
            )
            return {}

    # ★ 가용성 사전 판정 (Plan 81 · D-175). 판정 결과를 페이로드에 실어 보내면
    # `sre_agent`가 조사 예산을 쓰기 전에 거부한다(같은 요구, 다른 수단 — `docs/25` L-5).
    # **여기서 트리거를 취소하지 않는다** — 거부 사유가 담긴 브리핑이 통보에 첨부되는 편이
    # 침묵보다 낫다(침묵 폴백 금지).
    target_state = await _resolve_target_state(cfg, event, first)

    signals = getattr(decision, "signals", None) or {}
    episode_meta, cross_source_meta = _episode_context(state, gate_cfg, event, decision)
    payload = build_trigger_payload(
        event,
        decision,
        recurrence=state.get("recurrence"),
        correlation_meta=state.get("correlation_meta"),
        root_resource=signals.get("root_resource"),
        target_state=target_state,
        # plans/91 1-3: root 이름은 signals(§8.2 동결 스키마)가 아니라 노이즈 컨텍스트에 있다.
        root_resource_name=(state.get("noise_context") or {}).get("root_resource_name"),
        episode_id=episode_id,
        episode=episode_meta,
        cross_source=cross_source_meta,
    )

    # (Plan 66 3-E) 후속 모드 — submit까지만 하고 통보를 즉시 내보낸다(브리핑 미첨부).
    # poll·후속 발송은 notifier가 즉시 통보 **후** 백그라운드로 수행한다(순서 보장).
    if followup:
        return await _submit_only(
            client, payload, event, decision, configurable.get("decision_store"),
            episode_id=episode_id, inv_store=inv_store,
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

    _audit(
        configurable.get("decision_store"), event, decision, investigation_id, status, verdict,
        episode_id=episode_id,
    )
    # (plans/153 W2) 인라인 종결 — 상태·사유·verdict·브리핑을 웹 조회용으로 남긴다. rejected의
    # verdict 자리는 거부 사유 문자열이다(`_submit_and_poll` 반환 계약).
    if status == "rejected":
        inline_reason: Any = verdict
    elif status == "timeout":
        inline_reason = f"조사 전체 타임아웃({total_timeout:.0f}s 초과)"
    else:
        inline_reason = None
    _store(
        inv_store, event, investigation_id, status, source=source,
        reason=inline_reason,
        verdict=None if status == "rejected" else verdict,
        briefing=briefing, episode_id=episode_id,
    )

    result: dict[str, Any] = {}
    if briefing is not None:
        result["investigation_briefing"] = briefing
    # (plans/91 1-4) 조사 ID를 state에 남겨 카드 피드백이 "실제 원인은 X"를 조사와 잇게 한다(값 있을 때만 키).
    if investigation_id and status != "rejected":
        result["investigation_id"] = investigation_id
    # (Plan 64 CW-C) escalate-only 후속 통보 승격 — fault_escalation_enabled + verdict.escalate일
    # 때만 상향 안내 블록을 state에 싣는다(notifier가 통보에 첨부). 게이트 판정(tier/routing/
    # decision)은 소급 변경·하향하지 않는다(§5.1). off/미escalate면 미첨부 → 통보 비트동일.
    if getattr(gate_cfg, "fault_escalation_enabled", False):
        escalation = build_escalation(verdict)
        if escalation is not None:
            result["investigation_escalation"] = escalation
    return result


def _episode_id(state: dict[str, Any]) -> str | None:
    """state의 크로스소스 신호에서 사건 id를 읽는다(없으면 None · plans/144 §5.3)."""
    cross_source = state.get("cross_source")
    if isinstance(cross_source, dict):
        value = cross_source.get("episode_id")
        if isinstance(value, str) and value:
            return value
    return None


def _episode_context(
    state: dict[str, Any],
    gate_cfg: Any,
    event: Any,
    decision: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(plans/153 §4.4) 페이로드 `meta.episode`·`meta.cross_source` 재료.

    플래그 off·사건 미소속이면 (None, None).

    워커가 넘긴 스냅샷은 판정 전에 만든 사본이라 이 알람의 티어가 비어 있다 — 제출 시점의 판정
    티어를 채워 넣는다(원본 사건 상태는 건드리지 않는다). `meta.cross_source`는 이 알람 자신의
    결합 판정(`decision.evidence.cross_source`)이다.
    """
    if not getattr(gate_cfg, "investigation_episode_context_enabled", False):
        return None, None
    snapshot = state.get("episode_snapshot")
    if not isinstance(snapshot, dict):
        return None, None
    alarm_id = str(getattr(event, "alarm_id", "") or "")
    tier = getattr(decision, "tier", None)
    members = [
        {**m, "tier": tier} if m.get("alarm_id") == alarm_id and m.get("tier") is None else m
        for m in snapshot.get("members") or []
        if isinstance(m, dict)
    ]
    evidence = getattr(decision, "evidence", None)
    cross_source = evidence.get("cross_source") if isinstance(evidence, dict) else None
    return (
        {**snapshot, "members": members},
        cross_source if isinstance(cross_source, dict) else None,
    )


async def submit_episode_investigation(
    client: Any,  # SreAgentClient (덕 타이핑)
    event: Any,  # 대표 알람 AlarmEvent
    decision: Any,  # 대표 알람 NotificationDecision
    episode: dict[str, Any],
    alarm_ids: list[str],
    *,
    cfg: Any,  # AppConfig (덕 타이핑)
    decision_store: Any = None,  # DecisionStore | None
    inv_store: Any = None,  # InvestigationStore | None
) -> dict[str, Any]:
    """사건 종합 조사를 submit만 한다 (plans/153 §4.5 · G-6 (a) — 워커가 사건당 1회 부른다).

    대표 알람 기준으로 단독 조사와 같은 결정적 가드(hostname 결측 · 호스트 인가 · 가용성 사전
    판정)를 거친 뒤 `kind="episode"` 페이로드(지문 `episode:<id>`)를 제출한다. poll·후속 발송은
    호출부가 notifier의 후속 경로로 띄운다. 조사 서비스의 예산·동시 상한·dedup TTL은 그대로 탄다.
    모든 실패는 graceful — 사유를 감사·조사 저장소에 남긴다(침묵 금지).

    Args:
        alarm_ids: 사건 멤버 alarm_id 전부(대표 알람이 첫 원소 — 저장소 존 판정 기준).

    Returns:
        `{"status", "investigation_id", "pending"}` — pending은 submit 성공 시 후속 태스크용
        식별자(`alarm_id`·`fingerprint`·`episode_id`·`kind`·`alarm_ids`), 아니면 None.
    """
    episode_id = str(episode.get("episode_id") or "")
    fingerprint = EPISODE_FINGERPRINT_PREFIX + episode_id
    audit_decision = _FingerprintOnly(fingerprint)
    # 멤버 db_id(조회 API 존 검사 근거) — 대표 알람 + 스냅샷 멤버. 스냅샷 상한 밖 멤버는 API가
    # 판정 기록으로 폴백한다.
    alarm_db_ids = _alarm_db_ids(event)
    for member in episode.get("members") or []:
        if isinstance(member, dict) and member.get("alarm_id") and member.get("db_id"):
            alarm_db_ids.setdefault(str(member["alarm_id"]), str(member["db_id"]))
    store_kw: dict[str, Any] = {
        "source": "followup", "episode_id": episode_id or None,
        "kind": INVESTIGATION_KIND_EPISODE, "alarm_ids": alarm_ids,
        "alarm_db_ids": alarm_db_ids,
    }

    def _done(investigation_id: str | None, status: str, reason: Any = None) -> dict[str, Any]:
        _audit(decision_store, event, audit_decision, investigation_id, status, reason,
               episode_id=episode_id or None)
        _store(inv_store, event, investigation_id, status, reason=reason, **store_kw)
        return {"status": status, "investigation_id": investigation_id, "pending": None}

    hostname = str(getattr(event, "hostname", "") or "").strip()
    if not hostname:
        return _done(None, "target_unresolved",
                     "대표 알람 hostname 미해소 — 조사 필수 필드(hostname) 결측으로 종합 조사 생략")
    target_resolution = resolve_targets(
        alarm_payload={
            "server_name": getattr(event, "server_name", None),
            "hostname": getattr(event, "hostname", None),
            "db_id": getattr(event, "db_id", None),
        }
    )
    first = target_resolution.targets[0] if target_resolution.resolved else None
    authz = authorize_host_investigation(
        mode=getattr(getattr(cfg, "host_authz", None), "mode", None),
        principal=Principal.system(),
        hostname=(first.hostname or first.server_name) if first else hostname,
        db_id=getattr(event, "db_id", None),
    )
    if not authz.allowed:
        record_investigation(denied_reason=authz.reason)
        return _done(None, "authz_denied",
                     f"호스트 인가 거부({authz.reason})" if authz.reason else None)

    target_state = await _resolve_target_state(cfg, event, first)
    payload = build_episode_trigger_payload(event, decision, episode, target_state=target_state)

    status = "error"
    investigation_id: str | None = None
    reason: Any = None
    try:
        await client.connect()
        try:
            sub = await client.submit(payload)
        finally:
            await client.disconnect()
        investigation_id = sub.get("investigation_id")
        if sub.get("status") == "rejected":
            status, reason = "rejected", sub.get("reason")
        elif investigation_id:
            status = "submitted"
    except Exception:  # noqa: BLE001 — 서비스 다운도 알람 처리를 막지 않는다
        status = "down"
        logger.warning(
            "사건 종합 조사 submit 실패(graceful): episode=%s", episode_id, exc_info=True
        )
    logger.info(
        "사건 종합 조사 제출: episode=%s 대표=%s 멤버=%d status=%s inv=%s",
        episode_id, getattr(event, "alarm_id", ""), len(alarm_ids), status, investigation_id,
    )
    result = _done(investigation_id, status, reason)
    if status == "submitted" and investigation_id:
        result["pending"] = {
            "investigation_id": investigation_id,
            "alarm_id": str(getattr(event, "alarm_id", "") or ""),
            "fingerprint": fingerprint,
            "episode_id": episode_id,
            "kind": INVESTIGATION_KIND_EPISODE,
            "alarm_ids": list(alarm_ids),
            "alarm_db_ids": dict(alarm_db_ids),
        }
    return result


class _FingerprintOnly:
    """감사 기록용 판정 대용 — `_audit`가 읽는 `fingerprint`만 가진다(종합 조사 지문)."""

    def __init__(self, fingerprint: str) -> None:
        self.fingerprint = fingerprint


async def _resolve_target_state(cfg, event, first) -> Optional[dict]:  # noqa: ANN001
    """조사 대상의 가용성을 판정해 페이로드에 실을 dict를 만든다 (Plan 81 · G-3 예외 포함).

    판정하지 않는 경우(전부 None 반환 → 페이로드는 종전과 동일):
        - 플래그 off (`availability_precheck_enabled=false`) — 회귀 0
        - **가용성/다운 계열 알람** — 대상이 다운인 것이 당연하므로 판정하면 "왜 내려갔나"
          조사가 막힌다(G-3 확정)
        - 대상·db_id 미식별 — 조회할 곳이 없다

    Args:
        cfg: 앱 설정
        event: 알람 이벤트
        first: 해소된 첫 대상(TargetRef) 또는 None

    Returns:
        `HostAvailability.to_dict()` 또는 None
    """
    comp = getattr(cfg, "composite", None)
    if comp is None or not getattr(comp, "availability_precheck_enabled", False):
        return None
    if is_availability_alarm(event):
        logger.debug(
            "가용성 계열 알람 — 사전 판정 생략(다운 원인 조사 보존): alarm_id=%s",
            getattr(event, "alarm_id", ""),
        )
        return None
    target = (first.hostname or first.server_name) if first else None
    db_id = getattr(event, "db_id", None)
    if not target or not db_id:
        return None

    from noise_gate.infrastructure.polestar_hostname_resolver import lookup_host

    lookup = await lookup_host(cfg, db_id, target)
    availability = lookup.availability
    if availability.blocks_collection:
        logger.info(
            "조사 대상 가용성 비정상 — 판정을 페이로드에 실어 전달: alarm_id=%s host=%s reason=%s",
            getattr(event, "alarm_id", ""), target, availability.reason,
        )
    return availability.to_dict()


async def _submit_only(
    client,  # noqa: ANN001 — SreAgentClient (덕 타이핑)
    payload: dict,
    event,  # noqa: ANN001 — AlarmEvent
    decision,  # noqa: ANN001 — NotificationDecision
    store,  # noqa: ANN001 — DecisionStore | None
    *,
    episode_id: str | None = None,
    inv_store=None,  # noqa: ANN001 — InvestigationStore | None (plans/153 W2)
) -> dict[str, Any]:
    """조사를 submit만 하고 통보를 즉시 내보낸다 (Plan 66 3-E 후속 모드).

    poll을 하지 않으므로 통보 지연이 submit 왕복(≤ investigation_mcp_call_timeout_seconds)으로
    묶인다. 후속 발송에 필요한 식별자를 `investigation_pending`으로 state에 실어 notifier에
    넘긴다(브리핑은 아직 없다). 여기서도 감사는 남긴다(status="submitted" — 종결 상태는 후속
    태스크가 재기록).

    Returns:
        {"investigation_pending": {...}} (submit 성공) 또는 {} (거부·실패 — graceful).
    """
    status = "error"
    investigation_id: Optional[str] = None
    reason: Any = None
    try:
        await client.connect()
        try:
            sub = await client.submit(payload)
        finally:
            await client.disconnect()
        investigation_id = sub.get("investigation_id")
        sub_status = sub.get("status")
        if sub_status == "rejected":
            status, reason = "rejected", sub.get("reason")
        elif investigation_id:
            status = "submitted"
        else:
            status = "error"
    except Exception:  # noqa: BLE001 — 서비스 다운도 게이트 통보를 막지 않는다
        status = "down"
        logger.warning(
            "조사 submit 실패(graceful·즉시통보 진행): alarm_id=%s",
            getattr(event, "alarm_id", ""), exc_info=True,
        )

    _audit(store, event, decision, investigation_id, status, reason, episode_id=episode_id)
    # (plans/153 W2) submit 직후 상태 — submitted면 종결 행은 notifier 후속 태스크(또는 API 지연
    # poll)가 남긴다. 거부·실패는 여기서 사유와 함께 끝난다.
    _store(
        inv_store, event, investigation_id, status, source="followup",
        reason=reason, episode_id=episode_id,
    )
    if status != "submitted" or not investigation_id:
        return {}
    return {
        "investigation_pending": {
            "investigation_id": investigation_id,
            "alarm_id": getattr(event, "alarm_id", ""),
            "fingerprint": getattr(decision, "fingerprint", ""),
            # (plans/153 W2) 후속 태스크의 저장 행에 사건 id를 잇는다 — 사건 미소속이면 키 없음.
            **({"episode_id": episode_id} if episode_id else {}),
            # (plans/153 W2 교정) 후속 저장 행의 존 검사 근거 — db_id를 모르면 키 없음.
            **({"alarm_db_ids": db_ids} if (db_ids := _alarm_db_ids(event)) else {}),
        }
    }


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
    *,
    episode_id: str | None = None,
) -> None:
    """조사 트리거 결과를 decision_store에 감사한다(미주입·실패는 graceful).

    `episode_id`(plans/144 §4.5)는 값이 있을 때만 인자로 넘긴다 — 없으면 호출이 종전과 같다.
    """
    if store is None:
        return
    extra: dict[str, Any] = {"episode_id": episode_id} if episode_id else {}
    try:
        store.record_investigation(
            alarm_id=getattr(event, "alarm_id", ""),
            fingerprint=getattr(decision, "fingerprint", ""),
            investigation_id=investigation_id,
            status=status,
            verdict=verdict,
            **extra,
        )
    except Exception:  # noqa: BLE001 — 감사 실패가 통보를 막지 않는다
        logger.warning(
            "조사 트리거 감사 기록 실패(무시): alarm_id=%s",
            getattr(event, "alarm_id", ""),
        )


def _alarm_db_ids(event: Any) -> dict[str, str]:
    """이 알람의 `{alarm_id: db_id}`(둘 중 하나라도 없으면 빈 dict)."""
    alarm_id = str(getattr(event, "alarm_id", "") or "")
    db_id = str(getattr(event, "db_id", "") or "")
    return {alarm_id: db_id} if alarm_id and db_id else {}


def _store(
    inv_store: Any,  # InvestigationStore | None (덕 타이핑)
    event: Any,  # AlarmEvent
    investigation_id: str | None,
    status: str,
    *,
    source: str,
    reason: Any = None,
    verdict: Any = None,
    briefing: dict[str, Any] | None = None,
    episode_id: str | None = None,
    kind: str = "alarm",
    alarm_ids: list[str] | None = None,
    alarm_db_ids: dict[str, Any] | None = None,
) -> None:
    """조사 상태를 조사 저장소에 남긴다(plans/153 W2 · 미주입·실패는 graceful).

    단일 알람 조사는 `kind="alarm"`·`alarm_ids=[이 알람]`(기본). 사건 종합 조사(W4)는
    `kind="episode"`·`alarm_ids=[대표, 멤버…]`를 넘긴다. `alarm_db_ids`(조회 API 존 검사 근거)를
    주지 않으면 이 알람의 db_id를 싣는다(`_alarm_db_ids`).
    """
    if inv_store is None:
        return
    try:
        inv_store.record(
            investigation_id=investigation_id,
            alarm_ids=alarm_ids or [str(getattr(event, "alarm_id", "") or "")],
            kind=kind,
            status=status,
            source=source,
            reason=reason,
            verdict=verdict,
            briefing=briefing,
            episode_id=episode_id,
            alarm_db_ids=alarm_db_ids if alarm_db_ids is not None else _alarm_db_ids(event),
        )
    except Exception:  # noqa: BLE001 — 저장 실패가 통보를 막지 않는다
        logger.warning(
            "조사 저장소 기록 실패(무시): alarm_id=%s", getattr(event, "alarm_id", "")
        )
