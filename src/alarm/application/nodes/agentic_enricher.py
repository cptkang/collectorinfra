"""deepagents Advisory Enricher 노드 (Plan 52 Phase E5, §8.7, D-048.7).

`alarm_analyzer → (agentic_enricher) → notification_gate` 사이에 위치하여, 메시지형·경계
알람에 한해 **읽기전용 신호를 동적으로 수집**하고 그 결과를 **승격 전용** 보조 입력으로 반영한다
(§3.12). 판단(4-티어)은 하지 않는다 — 결정적 `notification_policy.decide_notification`이 판단한다.

3경로 폴백 체인(§3.12 — 노드 내부에서 자동 선택):
    1. **트랙 B**(오케스트레이터가 읽기전용 도구를 ReAct로 동적 호출):
       - provider="vllm"(운영): vLLM health OK 시.
       - provider="gemini"(테스트/PoC — D-037/§4.7): api_key 존재 시. vLLM 인프라 없이
         Gemini 네이티브 tool-calling으로 agentic 경로를 실제 검증하기 위한 경로.
    2. 트랙 B 미가용 + fallback=="semantic_routing" → **트랙 A**: FabriX 1회 JSON 분류로
       needed_signals 산출 → 결정적 컬렉터가 고정 SQL/시그니처 스캔으로 수집.
    3. 그 외(deterministic_only / LLM 미가용) → **no-op**(결정적 게이트만, 회귀 0).

승격 비대칭(R-10/R-12):
    - 수집 결과의 상향 심각도 후보가 `event.severity`보다 크고, 기존 `ai_message_severity`보다
      클 때만 상향한다. **하향/강등은 절대 하지 않는다.**
    - 상향한 값이 실제 티어에 반영되려면 `enable_ai_severity_boost=True`여야 한다(정책이
      ai_severity를 소비하는 조건). enricher 자체 동작은 boost 플래그와 독립이다.
    - `noise_context`에 Plan 55 예약키(`app_impact`/`db_impact`)를 dict 확장으로 보강한다.

계층: application → infrastructure(noise_signal_tools)·config·llm·domain. orchestration 미참조.
      application→application(alarm_analyzer) import를 피하기 위해 `is_message_alarm` 판정은
      본 노드에서 자체 구현한다(warning 회피 — 팀리드 확정 사실 6).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from src.alarm.infrastructure.noise_signal_tools import (
    build_noise_enricher_backend,
    build_noise_signal_tools,
    collect_signal,
    extract_escalation_candidate,
    run_signal_react_loop,
    vllm_healthy,
)
from src.alarm.prompts.agentic_enricher import (
    AGENTIC_ENRICHER_CLASSIFY_PROMPT,
    AGENTIC_ENRICHER_SYSTEM_PROMPT,
)
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)

# 임계형(메트릭) 자원 토큰 — resource_type에 포함되면 메시지형 알람에서 제외한다
# (alarm_analyzer.is_message_alarm과 동일 규칙 — application→application import 회피 위해 재구현).
_METRIC_RESOURCE_TOKENS = ("cpu", "memory", "disk", "filesystem", "network")

# 트랙 A/B가 결정적으로 수집할 수 있는 신호 라벨 화이트리스트(오분류·환각 방어).
_ALLOWED_SIGNALS = ("message_signature", "importance_maintenance", "dependency")


def _is_message_alarm(event) -> bool:  # noqa: ANN001
    """메시지형 알람(condition_log 보유 + 비메트릭) 여부를 보수적으로 판정한다.

    LogMonitor/보안/앱 로그처럼 실제 로그 메시지가 담긴 알람에만 enricher를 적용한다.
    CPU/메모리 등 수치 임계형은 폴스타 숫자 심각도가 이미 유효하므로 제외한다(§3.11/§3.12).
    """
    condition_log = getattr(event, "condition_log", "") or ""
    if not condition_log.strip():
        return False
    resource_type = (getattr(event, "resource_type", "") or "").lower()
    if any(token in resource_type for token in _METRIC_RESOURCE_TOKENS):
        return False
    return True


def _build_user_prompt(event) -> str:  # noqa: ANN001
    """트랙 B용 알람 컨텍스트 요약(도구 호출 판단 재료)."""
    return (
        f"알람명: {getattr(event, 'alarm_name', '')}\n"
        f"서버: {getattr(event, 'server_name', '')}\n"
        f"자원유형: {getattr(event, 'resource_type', '')}\n"
        f"심각도: {getattr(event, 'severity', '')}\n"
        f"메시지(condition_log): {getattr(event, 'condition_log', '')}"
    )


async def _collect_track_a(cfg, event, noise_repo, collector: list) -> None:  # noqa: ANN001
    """트랙 A(FabriX 폴백): 1회 JSON 분류 → needed_signals → 결정적 수집.

    tool-calling 없이 FabriX가 needed_signals만 산출하고, 결정적 컬렉터가 고정 SQL/시그니처
    스캔으로 실제 수집한다(§3.12 — "무엇을 볼지"는 LLM 1회 분류, 수집은 결정적).
    """
    from src.llm import create_llm

    llm = create_llm(cfg)
    prompt = AGENTIC_ENRICHER_CLASSIFY_PROMPT.format(
        condition_log=getattr(event, "condition_log", "") or ""
    )
    response = await llm.ainvoke([{"role": "user", "content": prompt}])
    parsed = extract_json_from_response(getattr(response, "content", "") or "")
    needed = []
    if isinstance(parsed, dict):
        raw = parsed.get("needed_signals") or []
        if isinstance(raw, list):
            needed = [s for s in raw if s in _ALLOWED_SIGNALS]
    logger.info("트랙 A 분류 — needed_signals=%s", needed)
    for signal in needed:
        result = await collect_signal(signal, noise_repo=noise_repo, event=event)
        if result is not None:
            collector.append(result)


async def _collect_track_b(cfg, event, noise_repo, collector: list) -> None:  # noqa: ANN001
    """트랙 B(vLLM 가용): bind_tools ReAct 루프로 읽기전용 도구를 동적 호출한다.

    도구는 원본 결과를 collector에 적재하므로(ToolMessage 재파싱 금지), 루프 종료 후
    호출측이 collector 원본을 소비한다(Known Mistakes 2026-06-17).
    """
    max_tool_calls = int(getattr(cfg.noise_gate, "agentic_enricher_max_tool_calls", 5))
    tools = build_noise_signal_tools(
        noise_repo, event, collect_dependency=True, collector=collector
    )
    bound_llm = build_noise_enricher_backend(cfg, tools)
    calls = await run_signal_react_loop(
        bound_llm,
        tools,
        system_prompt=AGENTIC_ENRICHER_SYSTEM_PROMPT,
        user_prompt=_build_user_prompt(event),
        max_tool_calls=max_tool_calls,
    )
    logger.info("트랙 B ReAct 완료 — 도구 호출 %d회(상한 %d)", calls, max_tool_calls)


def _gemini_orchestrator_available(cfg) -> bool:  # noqa: ANN001
    """Gemini 오케스트레이터(테스트/PoC 트랙 B)가 실호출 가능한지 — api_key 존재 여부.

    `create_orchestrator_llm`의 gemini 키 해석과 동일하게 `orchestrator.api_key` →
    `llm.gemini_api_key` 순으로 확인한다. 키가 없으면 트랙 B를 켜지 않고 폴백한다
    (키 없이 track_b 선택 시 어차피 graceful no-op로 낭비되므로 사전 차단).
    """
    orchestrator_cfg = getattr(cfg, "orchestrator", None)
    orch_key = getattr(orchestrator_cfg, "api_key", "") if orchestrator_cfg else ""
    if orch_key:
        return True
    llm_cfg = getattr(cfg, "llm", None)
    return bool(getattr(llm_cfg, "gemini_api_key", "") if llm_cfg else "")


def _select_backend(gate_cfg, cfg) -> str:  # noqa: ANN001
    """3경로 자동 선택 — 오케스트레이터 provider·가용성·fallback으로 백엔드를 결정한다.

    - provider="vllm"(운영): vLLM health check OK → **트랙 B**(vLLM 네이티브 bind_tools).
    - provider="gemini"(테스트/PoC — D-037/§4.7): api_key 존재 → **트랙 B**(Gemini 네이티브
      tool-calling). vLLM 인프라 없이 agentic(도구 호출) 경로를 실제로 검증하기 위한 경로다.
      `build_noise_enricher_backend`→`create_orchestrator_llm`가 provider로 백엔드를 분기하므로
      실행 코드는 공용이고, 여기서 track_b 라우팅만 열어주면 된다.
    - 그 외(미가용): fallback으로 강등(semantic_routing→트랙 A / deterministic_only→no-op).

    Returns:
        "track_b" | "track_a" | "noop"
    """
    orchestrator_cfg = getattr(cfg, "orchestrator", None)
    provider = getattr(orchestrator_cfg, "provider", "vllm") if orchestrator_cfg else "vllm"
    if provider == "gemini":
        if _gemini_orchestrator_available(cfg):
            return "track_b"
    else:
        base_url = getattr(orchestrator_cfg, "base_url", "") if orchestrator_cfg else ""
        timeout = getattr(orchestrator_cfg, "health_timeout", 3) if orchestrator_cfg else 3
        if vllm_healthy(base_url, timeout):
            return "track_b"
    fallback = getattr(gate_cfg, "agentic_enricher_fallback", "semantic_routing")
    if fallback == "semantic_routing":
        return "track_a"
    return "noop"


async def agentic_enricher_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """메시지형·경계 알람에 한해 신호를 보강하고 승격 전용으로 반영한다(no-op 안전).

    Args:
        state: LangGraph 상태 (alarm_event/analysis_result/noise_context 사용).
        config: LangGraph configurable (app_config 필수, noise_repo 선택).

    Returns:
        변경분만: {"analysis_result": ..., "noise_context": ...}. no-op 조건이면 빈 dict.
    """
    result = state.get("analysis_result")
    if not result or state.get("error"):
        return {}  # 분석 없음/error → no-op

    configurable = (config or {}).get("configurable", {})
    cfg = configurable.get("app_config")
    if cfg is None:
        return {}

    gate_cfg = getattr(cfg, "noise_gate", None)
    if gate_cfg is None or not getattr(gate_cfg, "enable_agentic_enricher", False):
        return {}  # 옵트아웃 → E1~E4 완전 무변경(회귀 0)

    event = state["alarm_event"]

    # ── no-op 조건 ─────────────────────────────────────────────
    # 심각도3은 step0에서 이미 PAGE 단락 → enricher와 무관(비용 절감).
    if int(getattr(event, "severity", 0) or 0) == 3:
        return {}
    # 메시지형 한정(옵트) — 임계형(CPU/메모리 수치) 알람은 숫자 심각도가 이미 유효.
    if getattr(gate_cfg, "agentic_enricher_message_alarms_only", True) and not _is_message_alarm(event):
        return {}

    noise_repo = configurable.get("noise_repo")
    backend = _select_backend(gate_cfg, cfg)
    if backend == "noop":
        logger.info("Advisory Enricher: LLM 백엔드 없음 → 결정적 게이트만(no-op)")
        return {}

    collector: list[dict[str, Any]] = []
    timeout = float(getattr(gate_cfg, "agentic_enricher_timeout_seconds", 8.0))
    try:
        if backend == "track_b":
            await asyncio.wait_for(
                _collect_track_b(cfg, event, noise_repo, collector), timeout=timeout
            )
        else:  # track_a
            await asyncio.wait_for(
                _collect_track_a(cfg, event, noise_repo, collector), timeout=timeout
            )
    except asyncio.TimeoutError:
        logger.warning(
            "Advisory Enricher 타임아웃(%.1fs) → no-op: alarm_id=%s",
            timeout, getattr(event, "alarm_id", ""),
        )
        return {}
    except Exception:  # noqa: BLE001 — enricher 실패가 발송을 막지 않는다(graceful)
        logger.warning(
            "Advisory Enricher 실패 → no-op: alarm_id=%s",
            getattr(event, "alarm_id", ""), exc_info=True,
        )
        return {}

    return _apply_escalation(state, result, event, collector, backend)


def _apply_escalation(
    state: dict[str, Any], result, event, collector: list, backend: str  # noqa: ANN001
) -> dict[str, Any]:
    """수집 결과를 승격 전용으로 반영한다(하향 금지). 변경분만 반환한다.

    - ai_message_severity: 후보가 event.severity 초과 && 기존 ai보다 클 때만 상향.
    - noise_context: Plan 55 예약키(app_impact/db_impact) + agentic 감사 스냅샷을 dict 확장.
    """
    updated: dict[str, Any] = {}

    candidate = extract_escalation_candidate(collector)
    severity = int(getattr(event, "severity", 0) or 0)
    current_ai = getattr(result, "ai_message_severity", None)
    if candidate is not None:
        cand_sev, cand_label = candidate
        # 상향 전용: event.severity 초과 && 기존 ai_message_severity 초과일 때만.
        if cand_sev > severity and (current_ai is None or cand_sev > current_ai):
            result.ai_message_severity = cand_sev
            result.ai_severity_reason = (
                f"Advisory Enricher({backend}) — {cand_label}"
            )
            updated["analysis_result"] = result
            logger.info(
                "Advisory Enricher 상향: alarm_id=%s %s→ai_severity=%s (%s)",
                getattr(event, "alarm_id", ""), severity, cand_sev, cand_label,
            )

    # noise_context dict 확장(Plan 55 예약키 + 감사 스냅샷). 기존 폴스타 키는 보존한다.
    existing = state.get("noise_context")
    if collector:
        ctx = dict(existing) if isinstance(existing, dict) else {}
        ctx.setdefault("app_impact", None)  # (Plan 55 예약) APM 사용자 영향
        ctx.setdefault("db_impact", None)   # (Plan 55 예약) DPM DB 영향
        ctx["agentic"] = {
            "backend": backend,
            "signals": [c.get("signal") for c in collector if isinstance(c, dict)],
        }
        updated["noise_context"] = ctx

    return updated
