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

logger = logging.getLogger(__name__)

# poll이 종결로 간주하는 상태(investigation_trigger와 동일 계약 — sre-agent/05 §3).
_TERMINAL_POLL_STATUSES: frozenset[str] = frozenset(
    {"done", "failed", "timeout", "stub", "rejected", "not_found"}
)

# poll 결과에서 자연어 진단 텍스트를 담을 수 있는 필드(우선순위 순 — sre_diagnose 반환 방어).
_ANSWER_FIELDS = ("answer", "diagnosis", "response", "text", "message")

# 브리핑 dict를 자연어로 조립할 때의 6요소 라벨(alarm_notifier와 동일 순서·용어).
_BRIEFING_ORDER = ("timeline", "bottleneck", "cause", "evidence", "recommendation", "limitation")
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

    client = _build_client(gate_cfg)
    if client is None:
        return _respond(
            "장애 진단 서비스에 연결할 수 없어 진단을 수행하지 못했습니다. "
            "잠시 후 다시 시도하시거나 관리자에게 문의해 주세요.",
        )

    server_name, hostname, db_id = _extract_targets(state)
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
        from src.alarm.infrastructure.sre_agent_client import SreAgentClient

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

    parsed_requirements.filter_conditions의 식별 필터를 우선하고, 없으면 멀티턴
    직전 턴 식별 엔티티(conversation_context.previous_entities, "해당 서버")로 폴백한다.
    db_id는 active_db_id → 직전 대상 DB 순으로 best-effort 추출한다(진단의 선택 힌트).
    """
    db_id = state.get("active_db_id") or None
    server_name: Optional[str] = None
    hostname: Optional[str] = None

    parsed = state.get("parsed_requirements") or {}
    for cond in parsed.get("filter_conditions", []) or []:
        if not isinstance(cond, dict):
            continue
        field = str(cond.get("field", "")).lower()
        value = cond.get("value")
        if not value:
            continue
        if field in ("hostname", "host_name") and hostname is None:
            hostname = str(value)
        elif field in ("server_name", "name") and server_name is None:
            server_name = str(value)

    ctx = state.get("conversation_context") or {}
    if server_name is None and hostname is None:
        for e in ctx.get("previous_entities") or []:
            if not isinstance(e, dict):
                continue
            f = str(e.get("field", "")).lower()
            v = e.get("value")
            if not v:
                continue
            if f in ("hostname", "host_name") and hostname is None:
                hostname = str(v)
            elif f in ("server_name", "name") and server_name is None:
                server_name = str(v)

    if db_id is None:
        prev_db_ids = ctx.get("previous_db_ids") or []
        if prev_db_ids:
            db_id = prev_db_ids[0]

    return server_name, hostname, db_id


def _extract_diagnosis_text(poll_result: dict) -> str:
    """poll 결과에서 자연어 진단 텍스트를 추출한다(sre_diagnose 반환 방어적 처리).

    명시적 자연어 필드(answer/diagnosis/...)를 우선하고, 없으면 briefing(6요소 dict/문자열)을
    자연어로 조립한다. 어느 것도 없으면 빈 문자열(호출부가 사유를 안내).
    """
    if not isinstance(poll_result, dict):
        return ""
    for key in _ANSWER_FIELDS:
        val = poll_result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    briefing = poll_result.get("briefing")
    if isinstance(briefing, str) and briefing.strip():
        return briefing.strip()
    if isinstance(briefing, dict):
        return _briefing_to_text(briefing)
    return ""


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
            lines.append(f"[{_BRIEFING_LABELS[key]}] {val}")
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
