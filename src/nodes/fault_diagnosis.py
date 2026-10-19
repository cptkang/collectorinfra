"""장애 진단 pull 위임 노드 (Plan 64 §0.2 CW-B · sre-agent/05 §3·§7).

시멘틱 라우터가 `fault_diagnosis` 의도로 분류한 요청(사용자가 "○○ 서버 원인 분석해줘"류로
장애 진단을 명시 요청)을 sre_agent 조사 서비스에 위임한다. `sre_diagnose`(question,
server_name?, hostname?, db_id?)로 진단 잡을 제출 → 전체 타임아웃 가드 안에서 poll →
자연어 진단 응답을 final_response로 반환한다(CW-A 트리거와 동일 submit/poll 잡 패턴).

경계(엄수): sre_agent 패키지를 import하지 않는다 — 통신은 MCP JSON 계약(diagnose/poll)으로만
한다. 조사 실행 본체(dispatcher·severity_judge·브리핑 조립)는 sre_agent 소관이다.

설계 안전장치(옵트인·graceful·D-003 읽기전용):
    - fault_diagnosis_enabled=False면 그래프에 노드 자체가 미배선(회귀 0)이나, 방어적으로
      진입부에서도 재확인해 위임하지 않는다.
    - 전체(diagnose+poll) 타임아웃 가드를 씌운다(per-call 아님 — 폴링 루프를 유계로).
    - 서비스 다운/타임아웃/거부/파싱 실패 시 **사유를 담은 자연어 응답**을 돌려준다
      (침묵 폴백 금지). DB 접근·조치 없음(읽기전용).

계층: application(node) → infrastructure(sre_agent_client) + config/domain(state)만 의존한다.
다른 노드(src.nodes.*)·graph를 import하지 않는다(D-004 정합).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional

from langchain_core.messages import AIMessage

from src.config import AppConfig, load_config
from src.state import AgentState
from src.domain.host_authz import (
    DENY_DB_NOT_ALLOWED,
    DENY_NO_PRINCIPAL,
    DENY_ROLE_NOT_ALLOWED,
    DENY_UNKNOWN_MODE,
    Principal,
    authorize_host_investigation,
)
from src.domain.host_availability import describe as describe_availability
from src.domain.incident_time import parse_incident_time
from src.domain.symptom_router import (
    INVESTIGATION_REF_KIND,
    ROUTE_PLAYBOOK,
    SymptomRoute,
    route_symptom,
    virtual_event_fields,
)
from src.infrastructure.investigation_request_limit import user_request_slot
from src.observability.investigation_metrics import record_investigation
from src.security.audit_logger import (
    INVESTIGATION_DENIED,
    INVESTIGATION_FAILED,
    INVESTIGATION_OK,
    log_investigation,
)
from src.utils.prior_targets import resolve_targets
from noise_gate.domain.investigation_briefing import render_briefing_lines
from noise_gate.domain.investigation_payload import build_trigger_payload

logger = logging.getLogger(__name__)

# poll이 종결로 간주하는 상태(investigation_trigger와 동일 계약 — sre-agent/05 §3).
_TERMINAL_POLL_STATUSES: frozenset[str] = frozenset(
    {"done", "failed", "timeout", "stub", "rejected", "not_found"}
)

# poll 결과에서 자연어 진단 텍스트를 담을 수 있는 필드(우선순위 순 — sre_diagnose 반환 방어).
_ANSWER_FIELDS = ("answer", "diagnosis", "response", "text", "message")

# 브리핑 렌더 순서·라벨은 공용 렌더러(noise_gate.domain.investigation_briefing)가 정본이다 —
# 생산자(sre_agent briefing_builder) 키를 따르며 alarm_notifier와 같은 함수를 쓴다(D-197).
# 인가 거부 시 사용자에게 보일 문구. 사유별로 **다른 안내**를 준다 —
# "권한이 없습니다" 하나로 뭉치면 설정 오류(미상 모드)와 정상 거부가 구분되지 않는다.
_DENY_MESSAGES: dict[str, str] = {
    DENY_ROLE_NOT_ALLOWED: (
        "장애 진단은 관리자 권한이 필요한 기능입니다. "
        "조회 가능한 데이터 질의로 다시 요청하시거나 관리자에게 문의해 주세요."
    ),
    DENY_NO_PRINCIPAL: (
        "요청자 권한 정보를 확인하지 못해 장애 진단을 수행하지 않았습니다. "
        "다시 로그인한 뒤 시도해 주세요."
    ),
    DENY_DB_NOT_ALLOWED: (
        "해당 대상은 조회 인가된 범위 밖이라 장애 진단을 수행하지 않았습니다."
    ),
    DENY_UNKNOWN_MODE: (
        "호스트 조사 인가 설정(HOST_AUTHZ_MODE)이 확인되지 않아 진단을 차단했습니다. "
        "관리자에게 문의해 주세요."
    ),
    "_default": "권한 확인에 실패하여 장애 진단을 수행하지 않았습니다.",
}

async def fault_diagnosis(
    state: AgentState,
    *,
    app_config: AppConfig | None = None,
) -> dict:
    """장애 진단 요청을 sre_agent에 위임하고 자연어 진단 응답을 반환한다.

    Args:
        state: 현재 에이전트 상태(user_query·active_db_id·parsed_requirements·
            conversation_context 사용).
        app_config: 앱 설정(외부 주입, 없으면 내부 로드). noise_gate의 조사 서비스
            연결 설정(url/token/타임아웃)을 재사용한다(CW-A와 단일 서비스).

    Returns:
        업데이트할 State 필드:
        - final_response: 자연어 진단 응답(서비스 미가용/실패 시 사유 안내)
        - routing_intent: "fault_diagnosis"
        - current_node: "fault_diagnosis"
        - messages: 답변 누적(멀티턴 후속 판단용)
    """
    if app_config is None:
        app_config = load_config()

    question = state.get("user_query", "") or ""
    gate_cfg = getattr(app_config, "noise_gate", None)

    # 방어적 재확인 — off면 그래프에 미배선이나(회귀 0) 진입 시에도 위임하지 않는다.
    if gate_cfg is None or not getattr(gate_cfg, "fault_diagnosis_enabled", False):
        return _respond(
            "현재 장애 진단 기능이 비활성화되어 있습니다. 조회 가능한 데이터 질의로 다시 요청해 주세요.",
        )

    server_name, hostname, db_id = _extract_targets(state)

    # ★ 호스트 인가 게이트 (Plan 78 W3-5 · G 계층 최우선).
    # **조회 권한 ≠ 조사 권한**이다 — allowed_db_ids만으로 실호스트 조사를 열지 않는다.
    # 판정은 **위임 직전**(실행 경계)에 둔다. planner·LLM 경로에서 막으면 우회가 생긴다
    # (UI 게이트 ≠ 인가). 이벤트 경로(investigation_trigger)가 **같은 모듈**을 호출한다(G5).
    decision = authorize_host_investigation(
        mode=getattr(getattr(app_config, "host_authz", None), "mode", None),
        principal=Principal(
            role=state.get("user_role"),
            user_id=state.get("user_id"),
            allowed_db_ids=state.get("allowed_db_ids"),
            entry_point="chat",
        ),
        hostname=hostname or server_name,
        db_id=db_id,
    )
    if not decision.allowed:
        # 조용히 건너뛰지 않는다 — 사유를 응답·감사·지표에 남긴다(78 W3-5 · W6-5).
        logger.info("장애 진단 인가 거부: %s", decision.as_audit())
        record_investigation(denied_reason=decision.reason)
        await log_investigation(
            request_id=state.get("request_id"),
            entry_point="chat",
            targets=[{"server_name": server_name, "hostname": hostname, "db_id": db_id}]
            if (server_name or hostname) else None,
            outcome=INVESTIGATION_DENIED,
            user_id=state.get("user_id"),
            thread_id=state.get("thread_id"),
            backend="sre_agent",
            authz=decision.as_audit(),
        )
        return _respond(_DENY_MESSAGES.get(decision.reason, _DENY_MESSAGES["_default"]))

    # ★ 가용성 사전 판정 게이트 (Plan 81 · D-175 · G-2 확정).
    # **인가 다음, 위임 직전**이다 — 인가보다 앞서면 권한 없는 요청이 DB를 읽게 되고,
    # 위임 뒤면 이미 조사 예산(전체 타임아웃·토큰)이 나간 뒤다.
    # 차단은 `unavailable` 하나뿐이며(fail-open), 판정이 꺼져 있으면 이 블록은 통째로 건너뛴다.
    availability = await _check_target_availability(state, app_config, server_name, hostname, db_id)
    if availability is not None and availability.blocks_collection:
        label = hostname or server_name or "대상 서버"
        notice = describe_availability(availability, label)
        logger.info(
            "장애 진단 생략(가용성 비정상): db_id=%s host=%s reason=%s",
            db_id, label, availability.reason,
        )
        record_investigation(denied_reason="target_unavailable")
        await log_investigation(
            request_id=state.get("request_id"),
            entry_point="chat",
            targets=[{"server_name": server_name, "hostname": hostname, "db_id": db_id}],
            # outcome 어휘를 늘리지 않는다(소비처 호환) — 사유는 degraded로 구분한다.
            outcome=INVESTIGATION_DENIED,
            user_id=state.get("user_id"),
            thread_id=state.get("thread_id"),
            backend="sre_agent",
            degraded=[{"reason": "target_unavailable", "detail": availability.reason}],
            availability=availability.to_dict(),
        )
        return _respond(
            f"{notice} 조사를 수행하지 않았습니다. "
            "서버 상태를 확인한 뒤 다시 요청해 주세요."
        )

    # ★ (plans/187 W12 · D-341 ㉕㉖) 채팅 증상 질의 조사 — on이면 동기 대기 대신 결정적 라우팅 후
    # 접수만 하고 조사 카드 ref로 답한다. 인가·가용성 게이트는 위에서 이미 통과했다(종전과 같다).
    # off면 이 블록을 건너뛰어 아래 동기 경로가 종전과 비트 동일하다.
    if getattr(gate_cfg, "sre_chat_investigation_enabled", False):
        return await _submit_chat_investigation(state, app_config, gate_cfg, question, decision)

    client = _build_client(gate_cfg)
    if client is None:
        return _respond(
            "장애 진단 서비스에 연결할 수 없어 진단을 수행하지 못했습니다. "
            "잠시 후 다시 시도하시거나 관리자에게 문의해 주세요.",
        )

    total_timeout = float(
        getattr(gate_cfg, "investigation_total_timeout_seconds", 45.0)
    )

    # 사건 좌표계(plans/50 A′-5): 질의의 시간 표현을 결정적으로 파싱해 위임 인자로 넘긴다.
    # 표현이 없으면 인자 자체를 보내지 않는다(종전 계약과 동일 — 앵커 없는 조사).
    scope = parse_incident_time(question, datetime.now())
    scope_kwargs: dict = (
        {"reference_time": scope.reference_time, "lookback_minutes": scope.lookback_minutes}
        if scope else {}
    )
    if scope:
        logger.info(
            "장애 진단 사건 구간: anchor=%s resolution=%s reference_time=%s lookback=%d",
            scope.anchor.isoformat(), scope.resolution, scope.reference_time, scope.lookback_minutes,
        )

    try:
        text, status = await asyncio.wait_for(
            _diagnose_and_poll(
                client, gate_cfg, question, server_name, hostname, db_id, scope_kwargs
            ),
            timeout=total_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "장애 진단 전체 타임아웃(%.1fs 초과): question=%r", total_timeout, question[:50]
        )
        return _respond(
            "장애 진단이 제한 시간 내에 완료되지 않았습니다. 대상 범위를 좁혀 다시 요청해 주세요.",
        )
    except Exception:  # noqa: BLE001 — 서비스 다운/통신 실패도 사유를 노출(침묵 금지)
        logger.warning(
            "장애 진단 위임 실패(graceful): question=%r", question[:50], exc_info=True
        )
        return _respond(
            "장애 진단 서비스 호출 중 오류가 발생하여 진단을 완료하지 못했습니다. "
            "잠시 후 다시 시도해 주세요.",
        )

    if not text:
        # 종결됐으나 진단 텍스트가 비어 있음(rejected/failed/증거 불충분 등) — 사유를 안내.
        return _respond(
            f"장애 진단을 완료했으나 제시할 결과를 받지 못했습니다(상태: {status}). "
            "대상 서버를 명확히 지정해 다시 요청해 주세요.",
        )

    logger.info(
        "장애 진단 응답 생성 완료 (status=%s, server=%s, host=%s)",
        status, server_name, hostname,
    )
    return _respond(text)


async def _check_target_availability(
    state: AgentState,
    app_config: AppConfig,
    server_name: Optional[str],
    hostname: Optional[str],
    db_id: Optional[str],
):
    """조사 대상의 가용성을 판정한다 (Plan 81). 판정하지 않을 조건이면 None.

    조회는 `noise_gate.infrastructure.polestar_hostname_resolver.lookup_host` —
    `process_query`·`investigation_trigger`와 **같은 함수**를 쓴다(사본 금지 · D-171 G5 선례).

    Args:
        state: 에이전트 상태(미사용 — 시그니처 대칭 유지)
        app_config: 앱 설정
        server_name / hostname: 대상 식별자(하나라도 있어야 판정한다)
        db_id: 대상 DB(없으면 조회할 곳을 모른다)

    Returns:
        `HostAvailability` 또는 None(판정 비활성·대상 미식별·차단 비활성)
    """
    cfg = getattr(app_config, "composite", None)
    if cfg is None or not cfg.availability_precheck_enabled:
        return None
    if not cfg.availability_block_on_unavailable:
        # 관찰 모드 — 조사 경로는 표시할 표가 없으므로 판정만 하고 진행한다(문구 미사용).
        return None
    target = hostname or server_name
    if not target or not db_id:
        return None

    from noise_gate.infrastructure.polestar_hostname_resolver import lookup_host

    lookup = await lookup_host(app_config, db_id, target)
    return lookup.availability


async def _diagnose_and_poll(
    client,  # noqa: ANN001 — SreAgentClient (덕 타이핑)
    gate_cfg,  # noqa: ANN001 — NoiseGateConfig (덕 타이핑)
    question: str,
    server_name: Optional[str],
    hostname: Optional[str],
    db_id: Optional[str],
    scope_kwargs: Optional[dict] = None,
) -> tuple[str, str]:
    """진단 잡을 submit(diagnose)하고 종결까지 poll한다(연결/해제 포함, 상위 wait_for가 전체 유계).

    `scope_kwargs`(reference_time·lookback_minutes)는 사건 좌표계다 — 있을 때만 전달한다.

    Returns:
        (diagnosis_text, status). rejected/failed 등 종결이나 텍스트가 없으면 ("", status).
    """
    poll_interval = float(
        getattr(gate_cfg, "investigation_poll_interval_seconds", 1.0)
    )
    await client.connect()
    try:
        sub = await client.diagnose(
            question, server_name=server_name, hostname=hostname, db_id=db_id,
            **(scope_kwargs or {}),
        )
        sub_status = sub.get("status")
        investigation_id = sub.get("investigation_id")
        if sub_status == "rejected" or not investigation_id:
            return "", sub_status or "rejected"
        while True:
            res = await client.poll(investigation_id)
            poll_status = res.get("status")
            if poll_status in _TERMINAL_POLL_STATUSES:
                return _extract_diagnosis_text(res), poll_status or "done"
            await asyncio.sleep(poll_interval)
    finally:
        await client.disconnect()


def _build_client(gate_cfg: Any) -> Any:  # NoiseGateConfig → SreAgentClient | None
    """noise_gate 설정으로 sre_agent 조사 서비스 클라이언트를 생성한다(CW-A 설정 재사용).

    생성 실패 시 None을 반환한다 — 노드는 사유를 담은 자연어 응답으로 graceful 처리한다.
    """
    try:
        from noise_gate.infrastructure.sre_agent_client import SreAgentClient

        url = gate_cfg.investigation_service_url
        if not url:
            return None
        token = getattr(gate_cfg, "investigation_service_token", None)
        # SecretStr(.get_secret_value) 또는 평문 문자열(테스트 SimpleNamespace) 모두 수용.
        token_val = (
            token.get_secret_value()
            if hasattr(token, "get_secret_value")
            else (token or "")
        )
        return SreAgentClient(
            server_url=url,
            bearer_token=token_val or None,
            mcp_call_timeout=float(
                getattr(gate_cfg, "investigation_mcp_call_timeout_seconds", 10.0)
            ),
        )
    except Exception:  # noqa: BLE001 — 생성 실패는 graceful no-client
        logger.warning("장애 진단 클라이언트 생성 실패 — 진단 없이 안내 응답", exc_info=True)
        return None


def _extract_targets(
    state: AgentState,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """진단 대상 식별자(server_name, hostname, db_id)를 state에서 추출한다.

    **해소 본체는 공통 모듈**(`src.utils.prior_targets`)이 담당한다 (Plan 78 W1-4 · G5) —
    `process_query`·`investigation_trigger`와 **같은 함수**를 쓴다. 각자 구현하면 개선이
    한쪽에만 들어간다(§2.2 G5 실측): 종전 이 함수는 `prior_targets`를 보지 않아
    *"CPU 80% 이상 서버를 조회하고 그 서버들 원인 분석"* 이 RCA 경로에서 불성립했다(G2).

    우선순위(전 경로 동일): ① 이번 턴 `filter_conditions` → ② `prior_targets`
    → ③ `previous_entities`. **①이 ②를 이긴다**(사용자 명시 지목 우선).

    db_id는 active_db_id → 직전 대상 DB 순으로 best-effort 추출한다(진단의 선택 힌트).

    반환형은 종전대로 **첫 대상 하나**다 — N-대상 진단은 본 계획 범위 밖이다(W2는
    `process_query` fan-out 소관).
    """
    ctx = state.get("conversation_context") or {}
    db_id = state.get("active_db_id") or None
    if db_id is None:
        prev_db_ids = ctx.get("previous_db_ids") or []
        if prev_db_ids:
            db_id = prev_db_ids[0]

    parsed = state.get("parsed_requirements") or {}
    resolution = resolve_targets(
        filter_conditions=parsed.get("filter_conditions"),
        prior_targets=state.get("prior_targets"),
        previous_entities=ctx.get("previous_entities"),
        db_id=db_id,
        max_targets=load_config().composite.max_targets,
    )
    if not resolution.resolved:
        if resolution.dropped:
            logger.info(
                "장애 진단 대상 미확정 — 사유: %s",
                [d.get("reason") for d in resolution.dropped],
            )
        return None, None, db_id

    first = resolution.targets[0]
    return first.server_name, first.hostname, first.db_id or db_id


# ── (plans/187 W12 · D-341 ㉕㉖ · G-32·G-33) 채팅 증상 질의 조사 — 비동기 접수 ──────
#
# 대상 1건 × 증상 유형 1건이 결정적으로 정해지면 플레이북 잡(가상 알람 맥락 →
# `sre_investigate_alarm`), 아니면 비동기 `sre_diagnose`로 접수한다(`src.domain.symptom_router`).
# 기록은 조사 저장소에만 한다 — 플레이북은 `kind=manual`, HolmesGPT는 `kind=diagnosis`,
# 둘 다 `origin=chat`·`owner_sub`이고 `parent_alarm_id`가 없다. 가상 알람은 알람·결정 저장소·
# 노이즈 게이트에 쓰지 않는다(알람 그래프를 거치지 않는다 → 통보·승격 없음).
# 상한은 W10/W11 사용자별 카운터를 공유한다.

#: 저장 레코드의 `origin`·`source`.
_CHAT_ORIGIN = "chat"
#: 조사 서비스 지문 dedup이 다른 잡과 섞이지 않게 하는 가상 알람 지문 접두.
_CHAT_FINGERPRINT_PREFIX = "chat:"
#: 엔진 표기(카드 ref `engine`).
_ENGINE_LABELS: dict[str, str] = {ROUTE_PLAYBOOK: "플레이북", "holmesgpt": "HolmesGPT"}


def _resolve_db_id(state: AgentState) -> str | None:
    """대상 DB — active_db_id → 직전 대상 DB(`_extract_targets`와 같은 순서)."""
    db_id = state.get("active_db_id") or None
    if db_id is None:
        prev = (state.get("conversation_context") or {}).get("previous_db_ids") or []
        if prev:
            db_id = prev[0]
    return db_id


def _explicit_targets(state: AgentState, app_config: AppConfig) -> list[dict[str, Any]]:
    """이번 턴에 명시 식별된 대상만(지시어·직전 턴·선행 결과 승계 제외 — G-32).

    「지시어는 식별자가 아니다」 — 승계 대상은 라우팅 입력에 넣지 않는다.

    `resolve_targets`의 ① 단계(`filter_conditions`)만 쓴다 — 지시어 값은 그 안에서 탈락한다.
    """
    parsed = state.get("parsed_requirements") or {}
    max_targets = int(getattr(getattr(app_config, "composite", None), "max_targets", 10) or 10)
    resolution = resolve_targets(
        filter_conditions=parsed.get("filter_conditions"),
        db_id=_resolve_db_id(state),
        max_targets=max_targets,
    )
    return [t.model_dump() for t in resolution.targets] if resolution.resolved else []


def _investigation_store(gate_cfg: Any) -> Any:
    """조사 저장소(`InvestigationStore`) — 경로 설정이 없으면 None."""
    from noise_gate.infrastructure.investigation_store import InvestigationStore

    path = getattr(gate_cfg, "investigation_store_path", None)
    if not path:
        return None
    return InvestigationStore(path, int(getattr(gate_cfg, "investigation_store_max_lines", 2000)))


async def _poll_status_only(
    store: Any, rec: dict[str, Any], gate_cfg: Any
) -> tuple[dict[str, Any], str | None]:
    """상한 판정용 재조회 — poll 1회로 최신 상태만 얹고 **저장하지 않는다**.

    종결 행 저장은 조회 라우트의 지연 poll(`src.api.investigation_poll`) 소관이다
    (카드가 조회할 때 쓴다).
    실패하면 저장소 상태 그대로 둔다(진행 중으로 본다 — 상한 쪽으로 안전).
    """
    investigation_id = str(rec.get("investigation_id") or "")
    client = _build_client(gate_cfg)
    if not investigation_id or client is None:
        return rec, "조사 서비스 미구성 — 진행 상태를 다시 조회하지 못했습니다"
    timeout = float(getattr(gate_cfg, "investigation_mcp_call_timeout_seconds", 10.0))

    async def _poll() -> dict[str, Any]:
        await client.connect()
        try:
            res: dict[str, Any] = await client.poll(investigation_id)
            return res
        finally:
            await client.disconnect()

    try:
        res = await asyncio.wait_for(_poll(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — 재조회 실패는 저장소 상태로 판정한다
        return rec, f"조사 서비스 재조회 실패: {type(exc).__name__}"
    status = str(res.get("status") or "")
    if status in _TERMINAL_POLL_STATUSES:
        return {**rec, "status": status}, None
    return rec, None


def _virtual_alarm_payload(route: SymptomRoute, reference: datetime) -> dict[str, Any]:
    """가상 알람 맥락 → 조사 트리거 페이로드(계약 "1" · 자동 조사와 같은 조립 함수).

    유형은 플레이북별 (resourceType, alarmName) 사본(`VIRTUAL_EVENTS`)으로 싣는다 — sre_agent의 기존
    플레이북 선택이 그 플레이북을 고른다. 기준 시각은 질의 속 시각(없으면 현재)이다.
    `meta.virtual`·`meta.playbook_hint`는 추적용 가산 키다(수신 측은 meta 추가 키를 허용한다).
    """
    from noise_gate.infrastructure.investigation_store import KIND_MANUAL

    target = route.target or {}
    resource_type, alarm_name = virtual_event_fields(str(route.playbook_hint))
    server = target.get("server_name") or target.get("hostname") or ""
    host = target.get("hostname") or target.get("server_name") or ""
    event = SimpleNamespace(
        db_id=target.get("db_id") or "",
        server_name=server,
        hostname=host,
        alarm_id="",
        severity=0,
        alarm_name=alarm_name,
        resource_type=resource_type,
        resource_name=server,
        alarm_time=reference,
        conditions="",
        condition_log="",
    )
    decision = SimpleNamespace(
        tier="",
        reason="chat_symptom",
        fingerprint=f"{_CHAT_FINGERPRINT_PREFIX}{uuid.uuid4().hex[:12]}",
        signals={},
    )
    payload = build_trigger_payload(event, decision, investigation_kind=KIND_MANUAL)
    payload["meta"]["virtual"] = True
    payload["meta"]["playbook_hint"] = route.playbook_hint
    return payload


def _chat_card_ref(route: SymptomRoute, investigation_id: str, status: str) -> dict[str, str]:
    """조사 카드 ref — 문자열 키·값만(`src.domain.disclosure` `ref` 규칙)."""
    ref = {
        "investigation_id": investigation_id,
        "status": status,
        "route": route.route,
        "engine": _ENGINE_LABELS.get(route.route, route.route),
        "route_label": route.label,
        "reason": route.reason,
        "target": route.target_label,
        "playbook": route.playbook_hint or "",
        "alarm_kind": route.alarm_kind or "",
    }
    return {k: v for k, v in ref.items() if v}


def _first_denied_target(
    state: AgentState, app_config: AppConfig, targets: list[dict[str, Any]]
) -> tuple[str, Any] | None:
    """대상이 2개 이상이면 각 대상에 호스트 인가 — 처음 거부된 `(대상 표시명, 판정)`, 없으면 None.

    대상이 1개 이하면 진입부 인가(같은 대상)로 충분하므로 판정하지 않는다.
    """
    if len(targets) < 2:
        return None
    mode = getattr(getattr(app_config, "host_authz", None), "mode", None)
    principal = Principal(
        role=state.get("user_role"),
        user_id=state.get("user_id"),
        allowed_db_ids=state.get("allowed_db_ids"),
        entry_point="chat",
    )
    for target in targets:
        host = str(target.get("hostname") or target.get("server_name") or "")
        decision = authorize_host_investigation(
            mode=mode,
            principal=principal,
            hostname=host or None,
            db_id=target.get("db_id") or _resolve_db_id(state),
        )
        if not decision.allowed:
            return host, decision
    return None


async def _submit_chat_investigation(
    state: AgentState,
    app_config: AppConfig,
    gate_cfg: Any,
    question: str,
    decision: Any,
) -> dict[str, Any]:
    """채팅 증상 질의를 라우팅해 비동기로 접수하고 조사 카드 ref를 담아 즉시 응답한다.

    상한 초과·저장소 미설정·서비스 미가용·접수 실패·거부는 모두 사유 응답이다(침묵 금지 · 감사 1건).
    """
    from noise_gate.infrastructure.investigation_store import KIND_DIAGNOSIS, KIND_MANUAL

    sub = str(state.get("user_id") or "")
    explicit = _explicit_targets(state, app_config)
    route = route_symptom(question, explicit)
    target = route.target or {}
    audit_targets = [dict(target)] if target else None
    logger.info(
        "채팅 증상 질의 라우팅: route=%s reason=%s playbook=%s target=%s",
        route.route, route.reason, route.playbook_hint, route.target_label,
    )

    async def _audit(
        outcome: str,
        *,
        degraded: list[dict[str, Any]] | None = None,
        authz: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        await log_investigation(
            request_id=state.get("request_id"),
            entry_point="chat",
            targets=audit_targets,
            outcome=outcome,
            user_id=state.get("user_id"),
            thread_id=state.get("thread_id"),
            backend="sre_agent",
            authz=authz or decision.as_audit(),
            degraded=degraded,
            route=route.route,
            route_reason=route.reason,
            playbook=route.playbook_hint,
            **extra,
        )

    async def _deny(code: str, text: str, outcome: str) -> dict[str, Any]:
        await _audit(outcome, degraded=[{"reason": code, "detail": text}])
        return _respond(text)

    # (교정 M4) 이번 턴 식별 대상이 2개 이상이면 **모든 대상**에 호스트 인가를 적용한다 — 진입부
    # 인가는 첫 대상만 봤다. 하나라도 거부면 접수하지 않는다(사유 응답 · 감사).
    denied = _first_denied_target(state, app_config, explicit)
    if denied is not None:
        host, target_decision = denied
        logger.info("채팅 증상 조사 다중 대상 인가 거부: %s", target_decision.as_audit())
        record_investigation(denied_reason=target_decision.reason)
        await _audit(
            INVESTIGATION_DENIED,
            degraded=[{"reason": "target_authz_denied", "detail": target_decision.reason,
                       "target": host}],
            authz=target_decision.as_audit(),
        )
        return _respond(
            _DENY_MESSAGES.get(target_decision.reason, _DENY_MESSAGES["_default"])
        )

    store = _investigation_store(gate_cfg)
    if store is None:
        return await _deny(
            "store_unconfigured",
            "조사 저장소 경로가 설정되지 않아 장애 조사를 접수하지 못했습니다. "
            "관리자에게 문의해 주세요.",
            INVESTIGATION_FAILED,
        )
    # (교정 M1) 상한 검사부터 접수 레코드 기록까지 사용자별 잠금 한 구간이다. `sub`가 비면
    # `owner_unknown`으로 거부한다(fail-closed — 상한을 셀 수 없다).
    async with user_request_slot(store, gate_cfg, sub, refresh=_poll_status_only) as limited:
        if limited is not None:
            code, why = limited
            return await _deny(
                code, f"장애 조사를 접수하지 않았습니다 — {why}", INVESTIGATION_DENIED
            )

        client = _build_client(gate_cfg)
        if client is None:
            return await _deny(
                "service_unconfigured",
                "장애 진단 서비스에 연결할 수 없어 조사를 접수하지 못했습니다. "
                "잠시 후 다시 시도하시거나 관리자에게 문의해 주세요.",
                INVESTIGATION_FAILED,
            )
        unreachable = await client.unreachable_reason()
        if unreachable:
            return await _deny(
                "service_unreachable",
                f"장애 진단 서비스에 연결할 수 없어 조사를 접수하지 못했습니다({unreachable}).",
                INVESTIGATION_FAILED,
            )

        now = datetime.now()
        scope = parse_incident_time(question, now)
        reference = scope.anchor if scope else now
        if route.route == ROUTE_PLAYBOOK:
            payload = _virtual_alarm_payload(route, reference)

            async def _call() -> dict[str, Any]:
                res: dict[str, Any] = await client.submit(payload)
                return res
        else:
            scope_kwargs: dict[str, Any] = (
                {"reference_time": scope.reference_time, "lookback_minutes": scope.lookback_minutes}
                if scope else {}
            )

            async def _call() -> dict[str, Any]:
                # 질문 원문 · 이번 턴에 식별된 대상만(대상 미확정·복수면 인자 없이 — G-32 ④).
                res: dict[str, Any] = await client.diagnose(
                    question,
                    server_name=target.get("server_name"),
                    hostname=target.get("hostname"),
                    db_id=target.get("db_id"),
                    **scope_kwargs,
                )
                return res

        async def _submit() -> dict[str, Any]:
            await client.connect()
            try:
                return await _call()
            finally:
                await client.disconnect()

        timeout = float(getattr(gate_cfg, "investigation_mcp_call_timeout_seconds", 10.0))
        try:
            res = await asyncio.wait_for(_submit(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — 접수 실패도 사유를 노출(침묵 금지)
            logger.warning("채팅 증상 조사 접수 실패: route=%s", route.route, exc_info=True)
            detail = (
                f"타임아웃({timeout:.0f}s 초과)" if isinstance(exc, TimeoutError)
                else type(exc).__name__
            )
            return await _deny(
                "submit_failed",
                f"장애 조사 접수 중 오류가 발생했습니다({detail}). 잠시 후 다시 시도해 주세요.",
                INVESTIGATION_FAILED,
            )
        investigation_id = str(res.get("investigation_id") or "")
        rejected = str(res.get("status") or "") == "rejected"
        reason: str | None = str(res.get("reason") or "") or None
        if not investigation_id:
            return await _deny(
                "service_rejected" if rejected else "submit_failed",
                f"조사 서비스가 접수하지 않았습니다(사유: {reason or '조사 id 없음'}).",
                INVESTIGATION_DENIED if rejected else INVESTIGATION_FAILED,
            )
        status = "rejected" if rejected else "submitted"
        subject = {
            "route": route.route,
            "route_label": route.label,
            "route_reason": route.reason,
            "playbook": route.playbook_hint,
            "alarm_kind": route.alarm_kind,
            "server_name": target.get("server_name"),
            "hostname": target.get("hostname"),
            "db_id": target.get("db_id"),
            "reference_time": reference.isoformat(timespec="seconds"),
        }
        store.record(
            investigation_id=investigation_id,
            alarm_ids=[],
            kind=KIND_MANUAL if route.route == ROUTE_PLAYBOOK else KIND_DIAGNOSIS,
            status=status,
            source=_CHAT_ORIGIN,
            reason=reason if rejected else None,
            owner_sub=sub or None,
            origin=_CHAT_ORIGIN,
            subject={k: v for k, v in subject.items() if v not in (None, "")},
        )
    await _audit(
        INVESTIGATION_DENIED if rejected else INVESTIGATION_OK,
        degraded=[{"reason": "service_rejected", "detail": reason}] if rejected else None,
        investigation_id=investigation_id,
    )
    card = {
        "kind": INVESTIGATION_REF_KIND,
        "text": f"조사 {investigation_id} — {route.label}",
        "source": "turn",
        "ref": _chat_card_ref(route, investigation_id, status),
    }
    where = f" (대상 {route.target_label})" if route.target_label else ""
    if rejected:
        text = (
            f"장애 조사 접수가 거부되었습니다 — {route.label}{where}. "
            f"사유: {reason or '미상'}"
        )
    else:
        text = (
            f"장애 조사를 접수했습니다 — {route.label}{where}.\n"
            "결과는 아래 조사 카드에서 이어서 확인하실 수 있습니다."
        )
    return _respond(text, [card])


def _extract_diagnosis_text(poll_result: dict) -> str:
    """poll 결과에서 자연어 진단 텍스트를 추출한다(sre_diagnose 반환 방어적 처리).

    명시적 자연어 필드(answer/diagnosis/...)를 우선하고, 없으면 briefing(6요소 dict/문자열)을
    자연어로 조립한다. 어느 것도 없으면 빈 문자열(호출부가 사유를 안내).
    """
    if not isinstance(poll_result, dict):
        return ""
    briefing = poll_result.get("briefing")
    for key in _ANSWER_FIELDS:
        val = poll_result.get(key)
        if isinstance(val, str) and val.strip():
            # ★ Plan 78 W4-1: 자연어 필드가 있어도 **브리핑의 권고·한계를 버리지 않는다.**
            # 종전에는 여기서 곧장 return해 `Remediation` 목록이 통째로 유실됐다 —
            # `sre_agent/domain/remediation.py`가 위험도 3등급까지 계산해 보낸 것을
            # 사용자가 못 보는 상태였다(실측 2026-08-27).
            return _append_briefing_extras(val.strip(), briefing)
    if isinstance(briefing, str) and briefing.strip():
        return briefing.strip()
    if isinstance(briefing, dict):
        return _briefing_to_text(briefing)
    return ""


#: 자연어 필드에 가려 유실되기 쉬운 브리핑 요소. 조치 권고와 한계는 **판단에 직접 쓰이므로**
#: 요약 텍스트가 있어도 반드시 함께 보인다.
_BRIEFING_EXTRA_KEYS: tuple[str, ...] = ("recommendation", "limitations")


def _append_briefing_extras(text: str, briefing: object) -> str:
    """자연어 진단에 브리핑의 권고·한계를 덧붙인다 (Plan 78 W4-1·2 — **소비만** 한다).

    **문자열을 가공하지 않는다.** `Remediation.to_line()`이 이미
    `"[검토 필요] … (위험도 high·신뢰도 medium) — 근거: …"` 형태로 위험도·신뢰도를 담아
    보낸다. 여기서 다시 쓰거나 접두를 떼면 **고위험×저신뢰로 강등된 항목이 정식 권고처럼
    보인다**(W4-2). 78은 권고를 **생성하지 않고 그대로 나른다**.

    Args:
        text: 자연어 진단 텍스트
        briefing: poll 결과의 브리핑(dict가 아니면 원문 그대로 반환)

    Returns:
        권고·한계가 덧붙은 텍스트(중복이면 덧붙이지 않는다)
    """
    if not isinstance(briefing, dict):
        return text
    subset = {key: briefing.get(key) for key in _BRIEFING_EXTRA_KEYS}
    lines: list[str] = []
    for label, rendered in render_briefing_lines(subset):
        # 항목 단위로 중복을 거른다 — 이미 자연어에 포함된 줄은 두 번 보이지 않게 한다.
        kept = [ln for ln in rendered.splitlines() if ln.strip() and ln.strip() not in text]
        if not kept:
            continue
        lines.append(f"[{label}] " + "\n".join(kept))
    if not lines:
        return text
    return text + "\n\n" + "\n".join(lines)


def _briefing_to_text(briefing: dict) -> str:
    """구조화 브리핑 dict를 자연어 텍스트로 조립한다(스텁 · 정본 키 순서 · 그 외 키 말미).

    렌더는 공용 렌더러가 한다 — 여기서 키·순서를 다시 정하지 않는다(alarm_notifier와 대칭).
    """
    if briefing.get("stub"):
        return str(briefing.get("message", "조사 미실행(스텁)")).strip()
    return "\n".join(f"[{label}] {val}" for label, val in render_briefing_lines(briefing)).strip()


def _respond(text: str, disclosures: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """final_response·routing_intent·current_node·messages를 담은 노드 반환 dict를 만든다.

    답변을 대화 이력에 누적한다(멀티턴 후속 턴이 직전 진단을 인지하도록 — general_inference 정합).
    `disclosures`(plans/187 W12 — 조사 카드 ref)는 주어졌을 때만 키를 싣는다(없으면 종전과 같다).
    """
    result: dict = {
        "final_response": text,
        "routing_intent": "fault_diagnosis",
        "current_node": "fault_diagnosis",
    }
    if text and text.strip():
        result["messages"] = [AIMessage(content=text)]
    if disclosures:
        result["disclosures"] = disclosures
    return result
