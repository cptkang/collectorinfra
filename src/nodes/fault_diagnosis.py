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
from typing import Optional

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
from src.observability.investigation_metrics import record_investigation
from src.security.audit_logger import INVESTIGATION_DENIED, log_investigation
from src.utils.prior_targets import resolve_targets

logger = logging.getLogger(__name__)

# poll이 종결로 간주하는 상태(investigation_trigger와 동일 계약 — sre-agent/05 §3).
_TERMINAL_POLL_STATUSES: frozenset[str] = frozenset(
    {"done", "failed", "timeout", "stub", "rejected", "not_found"}
)

# poll 결과에서 자연어 진단 텍스트를 담을 수 있는 필드(우선순위 순 — sre_diagnose 반환 방어).
_ANSWER_FIELDS = ("answer", "diagnosis", "response", "text", "message")

# 브리핑 dict를 자연어로 조립할 때의 6요소 라벨(alarm_notifier와 동일 순서·용어).
_BRIEFING_ORDER = ("timeline", "bottleneck", "cause", "evidence", "recommendation", "limitation")
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

_BRIEFING_LABELS = {
    "timeline": "타임라인", "bottleneck": "병목", "cause": "원인",
    "evidence": "근거", "recommendation": "권고", "limitation": "한계",
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

    client = _build_client(gate_cfg)
    if client is None:
        return _respond(
            "장애 진단 서비스에 연결할 수 없어 진단을 수행하지 못했습니다. "
            "잠시 후 다시 시도하시거나 관리자에게 문의해 주세요.",
        )

    total_timeout = float(
        getattr(gate_cfg, "investigation_total_timeout_seconds", 45.0)
    )

    try:
        text, status = await asyncio.wait_for(
            _diagnose_and_poll(
                client, gate_cfg, question, server_name, hostname, db_id
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


async def _diagnose_and_poll(
    client,  # noqa: ANN001 — SreAgentClient (덕 타이핑)
    gate_cfg,  # noqa: ANN001 — NoiseGateConfig (덕 타이핑)
    question: str,
    server_name: Optional[str],
    hostname: Optional[str],
    db_id: Optional[str],
) -> tuple[str, str]:
    """진단 잡을 submit(diagnose)하고 종결까지 poll한다(연결/해제 포함, 상위 wait_for가 전체 유계).

    Returns:
        (diagnosis_text, status). rejected/failed 등 종결이나 텍스트가 없으면 ("", status).
    """
    poll_interval = float(
        getattr(gate_cfg, "investigation_poll_interval_seconds", 1.0)
    )
    await client.connect()
    try:
        sub = await client.diagnose(
            question, server_name=server_name, hostname=hostname, db_id=db_id
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


def _build_client(gate_cfg):  # noqa: ANN001, ANN201 — NoiseGateConfig → SreAgentClient | None
    """noise_gate 설정으로 sre_agent 조사 서비스 클라이언트를 생성한다(CW-A 설정 재사용).

    생성 실패 시 None을 반환한다 — 노드는 사유를 담은 자연어 응답으로 graceful 처리한다.
    """
    try:
        from noise_gate.infrastructure.sre_agent_client import SreAgentClient

        url = getattr(gate_cfg, "investigation_service_url", "")
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
_BRIEFING_EXTRA_KEYS: tuple[str, ...] = ("recommendation", "limitation")


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
    lines: list[str] = []
    for key in _BRIEFING_EXTRA_KEYS:
        val = briefing.get(key)
        if not val:
            continue
        rendered = "\n".join(str(v) for v in val) if isinstance(val, list) else str(val)
        if not rendered.strip() or rendered.strip() in text:
            continue  # 이미 자연어에 포함됨 — 두 번 보이지 않게 한다
        lines.append(f"[{_BRIEFING_LABELS[key]}] {rendered}")
    if not lines:
        return text
    return text + "\n\n" + "\n".join(lines)


def _briefing_to_text(briefing: dict) -> str:
    """구조화 브리핑 dict를 자연어 텍스트로 조립한다(스텁·6요소·기타 스칼라)."""
    if briefing.get("stub"):
        return str(briefing.get("message", "조사 미실행(스텁)")).strip()
    lines: list[str] = []
    seen: set[str] = set()
    for key in _BRIEFING_ORDER:
        val = briefing.get(key)
        if val:
            seen.add(key)
            # 권고는 `list[str]`로 온다(`Remediation.to_line()` 렌더 결과) —
            # 문자열화하면 `['...', '...']`가 그대로 노출되므로 줄바꿈으로 편다.
            rendered = "\n".join(str(v) for v in val) if isinstance(val, list) else val
            lines.append(f"[{_BRIEFING_LABELS[key]}] {rendered}")
    for key in sorted(briefing.keys()):
        if key in seen or key in ("stub", "elements"):
            continue
        val = briefing.get(key)
        if isinstance(val, (str, int, float, bool)) and val not in (None, "", False):
            lines.append(f"[{key}] {val}")
    return "\n".join(lines).strip()


def _respond(text: str) -> dict:
    """final_response·routing_intent·current_node·messages를 담은 노드 반환 dict를 만든다.

    답변을 대화 이력에 누적한다(멀티턴 후속 턴이 직전 진단을 인지하도록 — general_inference 정합).
    """
    result: dict = {
        "final_response": text,
        "routing_intent": "fault_diagnosis",
        "current_node": "fault_diagnosis",
    }
    if text and text.strip():
        result["messages"] = [AIMessage(content=text)]
    return result
